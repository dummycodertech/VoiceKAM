"""LLM Failover Router with per-provider 429 cooldown & startup smoke tests."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.config import AGENT_NAME, Settings
from app.engine.tools import TOOL_SCHEMAS
from app.llm.base import (
    AllProvidersUnavailableError,
    CanonicalMessage,
    LLMProvider,
    LLMStreamChunk,
    ProviderRateLimitOrTransientError,
)
from app.llm.gemini_provider import GeminiProvider
from app.llm.groq_provider import GroqProvider, fetch_live_groq_models
from app.llm.mock_provider import MockLLMProvider
from app.llm.openrouter_provider import OpenRouterProvider

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    provider: str
    model: str
    configured: bool
    live_in_catalog: bool | None = None
    smoke_ok: bool | None = None
    smoke_tool_called: bool | None = None
    smoke_latency_ms: int | None = None
    smoke_error: str | None = None
    cooldown_until: float = 0.0


class LLMRouter:
    """Routes streaming turns through ordered providers with 429 cooldown, 403 permanent disablement & failover."""

    def __init__(
        self,
        providers: list[LLMProvider],
        cooldown_seconds: int = 60,
    ) -> None:
        self.providers = providers
        self.cooldown_seconds = cooldown_seconds
        self.cooldowns: dict[str, float] = {}
        self.disabled_providers: dict[str, str] = {}
        self.startup_report: list[dict[str, Any]] = []
        self.live_groq_models: set[str] | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "LLMRouter":
        providers: list[LLMProvider] = []

        if settings.groq_api_key.strip():
            for model in settings.groq_model_list():
                providers.append(
                    GroqProvider(
                        api_key=settings.groq_api_key,
                        model_name=model,
                        reasoning_effort=settings.groq_reasoning_effort,
                        timeout_seconds=settings.llm_timeout_seconds,
                    )
                )

        if settings.gemini_api_key.strip():
            for model in settings.gemini_model_list():
                providers.append(
                    GeminiProvider(
                        api_key=settings.gemini_api_key,
                        model_name=model,
                        timeout_seconds=settings.llm_timeout_seconds,
                    )
                )

        if settings.openrouter_api_key.strip():
            providers.append(
                OpenRouterProvider(
                    api_key=settings.openrouter_api_key,
                    model_name=settings.openrouter_model,
                    timeout_seconds=max(settings.llm_timeout_seconds, 15.0),
                )
            )

        if settings.allow_mock_llm:
            providers.append(MockLLMProvider())

        return cls(providers=providers, cooldown_seconds=settings.provider_cooldown_seconds)

    def configured_providers(self) -> list[LLMProvider]:
        return [p for p in self.providers if p.is_configured()]

    def is_disabled(self, provider_key: str) -> bool:
        return provider_key in self.disabled_providers

    def mark_disabled(self, provider_key: str, reason: str = "forbidden") -> None:
        self.disabled_providers[provider_key] = reason
        self.cooldowns.pop(provider_key, None)
        logger.warning("Permanently disabled %s for process lifetime (%s)", provider_key, reason)

    def is_in_cooldown(self, provider_key: str, now: float | None = None) -> bool:
        t = now if now is not None else time.monotonic()
        return self.cooldowns.get(provider_key, 0.0) > t

    def mark_cooldown(self, provider_key: str, retry_after: float | None = None) -> None:
        duration = retry_after if (retry_after and retry_after > 0) else float(self.cooldown_seconds)
        self.cooldowns[provider_key] = time.monotonic() + duration
        logger.warning("Placed %s into cooldown for %.1fs", provider_key, duration)

    async def initialize_and_smoke_test(self, settings: Settings) -> list[dict[str, Any]]:
        """Query Groq /models to filter live models, then run a 1-request tool smoke test per provider."""
        if settings.groq_api_key.strip():
            live = await fetch_live_groq_models(settings.groq_api_key)
            self.live_groq_models = live
            if live is not None:
                filtered: list[LLMProvider] = []
                for p in self.providers:
                    if p.provider_name == "groq" and p.model_name not in live:
                        logger.warning("Groq model %s not found in live /models; skipping", p.model_name)
                        continue
                    filtered.append(p)
                if filtered:
                    self.providers = filtered
                logger.info("Groq live models verified: %s", [p.model_name for p in self.providers if p.provider_name == "groq"])

        smoke_tools = [t for t in TOOL_SCHEMAS if t["function"]["name"] == "get_seller_overview"]
        smoke_messages = [
            CanonicalMessage(role="system", content=f"You are {AGENT_NAME}. Call get_seller_overview once."),
            CanonicalMessage(role="user", content="Check the seller overview."),
        ]

        seen_provider_families: set[str] = set()
        report: list[dict[str, Any]] = []
        for p in self.configured_providers():
            if p.provider_name == "mock" or p.provider_name in seen_provider_families:
                continue
            seen_provider_families.add(p.provider_name)
            t0 = time.perf_counter()
            entry: dict[str, Any] = {
                "provider": p.provider_name,
                "model": p.model_name,
                "smoke_ok": False,
                "tool_called": False,
                "latency_ms": 0,
                "error": None,
            }
            try:
                async for chunk in p.stream_turn(smoke_messages, smoke_tools, temperature=0.0, max_tokens=200):
                    if chunk.type == "tool_calls" and chunk.tool_calls:
                        entry["tool_called"] = True
                entry["smoke_ok"] = True
            except Exception as exc:
                entry["error"] = str(exc)[:200]
                if isinstance(exc, ProviderRateLimitOrTransientError):
                    if exc.is_forbidden:
                        self.mark_disabled(p.key, reason=exc.reason_code)
                    elif exc.is_rate_limit:
                        self.mark_cooldown(p.key, exc.retry_after_seconds)
            entry["latency_ms"] = round((time.perf_counter() - t0) * 1000)
            report.append(entry)
            logger.info("Startup smoke test [%s]: %s", p.key, entry)

        self.startup_report = report
        return report

    def _ordered_candidates(self) -> list[LLMProvider]:
        active = [p for p in self.configured_providers() if not self.is_disabled(p.key)]
        if not active:
            return []
        now = time.monotonic()
        ready = [p for p in active if not self.is_in_cooldown(p.key, now)]
        cooling = sorted(
            [p for p in active if self.is_in_cooldown(p.key, now)],
            key=lambda p: self.cooldowns.get(p.key, 0.0),
        )
        # Prefer non-cooled-down providers; keep cooling providers only as last-ditch fallback if everything is cooling
        return ready if ready else cooling

    async def stream_turn_with_failover(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float = 0.25,
        max_tokens: int = 220,
    ) -> AsyncIterator[tuple[LLMStreamChunk, list[dict[str, str]]]]:
        """Yield (LLMStreamChunk, failover_events) across configured providers."""
        if not self.configured_providers():
            raise AllProvidersUnavailableError(
                "No LLM API keys are configured. Add GROQ_API_KEY or GEMINI_API_KEY in .env."
            )
        candidates = self._ordered_candidates()
        if not candidates:
            disabled_summary = ", ".join(f"{k} ({r})" for k, r in sorted(self.disabled_providers.items()))
            raise AllProvidersUnavailableError(
                f"All configured LLM providers are permanently disabled: {disabled_summary}"
            )

        sanitized_errors: list[str] = []
        detailed_errors: list[str] = []
        failovers: list[dict[str, str]] = []

        for idx, provider in enumerate(candidates):
            emitted_any_content = False
            try:
                async for chunk in provider.stream_turn(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    if chunk.type in ("token", "tool_calls"):
                        emitted_any_content = True
                    yield chunk, failovers
                return
            except ProviderRateLimitOrTransientError as exc:
                err_msg = str(exc)
                detailed_errors.append(f"{provider.key}: {err_msg}")
                sanitized_errors.append(f"{provider.key} ({exc.reason_code})")
                logger.warning("Provider %s failed [%s]: %s", provider.key, exc.reason_code, err_msg)
                if exc.is_forbidden:
                    self.mark_disabled(provider.key, reason=exc.reason_code)
                elif exc.is_rate_limit:
                    self.mark_cooldown(provider.key, exc.retry_after_seconds)
                if emitted_any_content:
                    # Do not duplicate already-streamed tokens; surface clean failure only if mid-stream broke
                    logger.error("Mid-stream failure on %s after tokens emitted: %s", provider.key, err_msg)
                    raise
                if idx + 1 < len(candidates):
                    nxt = candidates[idx + 1]
                    failovers.append(
                        {
                            "type": "provider_failover",
                            "from": provider.key,
                            "to": nxt.key,
                            "reason_code": exc.reason_code,
                        }
                    )
                    logger.warning("Failing over %s -> %s (reason_code=%s)", provider.key, nxt.key, exc.reason_code)
                continue

        logger.error("All configured LLM providers failed: %s", " | ".join(detailed_errors))
        raise AllProvidersUnavailableError(
            "All configured LLM providers failed: " + " | ".join(sanitized_errors)
        )


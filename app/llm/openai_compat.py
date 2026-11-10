"""Shared OpenAI-compatible async SSE streaming client for Groq & OpenRouter."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, AsyncIterator

import httpx

from app.llm.base import (
    CanonicalMessage,
    LLMProvider,
    LLMStreamChunk,
    ProviderRateLimitOrTransientError,
    ToolCallRequest,
)


def canonical_to_openai_messages(messages: list[CanonicalMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role in ("system", "user"):
            out.append({"role": msg.role, "content": msg.content})
        elif msg.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": msg.content or None}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            out.append(entry)
        elif msg.role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id or "",
                    "name": msg.tool_name or "",
                    "content": msg.content,
                }
            )
    return out


class ThinkTagFilter:
    """Incrementally strips <think>...</think> reasoning blocks from streamed tokens."""

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def feed(self, token: str) -> str:
        self._buf += token
        out: list[str] = []
        while self._buf:
            if self._in_think:
                end_idx = self._buf.find("</think>")
                if end_idx == -1:
                    # Keep only potential partial closing tag tail
                    if len(self._buf) > 8:
                        self._buf = self._buf[-8:]
                    break
                self._buf = self._buf[end_idx + len("</think>") :]
                self._in_think = False
            else:
                start_idx = self._buf.find("<think>")
                if start_idx == -1:
                    # Hold back possible partial "<think>" prefix
                    keep = 0
                    for k in range(min(6, len(self._buf)), 0, -1):
                        if "<think>".startswith(self._buf[-k:]):
                            keep = k
                            break
                    if keep:
                        out.append(self._buf[:-keep])
                        self._buf = self._buf[-keep:]
                    else:
                        out.append(self._buf)
                        self._buf = ""
                    break
                if start_idx > 0:
                    out.append(self._buf[:start_idx])
                self._buf = self._buf[start_idx + len("<think>") :]
                self._in_think = True
        return "".join(out)

    def flush(self) -> str:
        if self._in_think:
            self._buf = ""
            return ""
        rem = re.sub(r"</?think>", "", self._buf)
        self._buf = ""
        return rem


def _parse_retry_after(headers: httpx.Headers) -> float | None:
    val = headers.get("retry-after")
    if not val:
        return None
    try:
        return max(1.0, float(val))
    except ValueError:
        return None


class OpenAICompatProvider(LLMProvider):
    """Streaming tool-calling provider for OpenAI-compatible chat completion APIs."""

    def __init__(
        self,
        provider_name: str,
        model_name: str,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 12.0,
        reasoning_effort: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.reasoning_effort = reasoning_effort
        self.extra_headers = extra_headers or {}

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _build_payload(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        include_reasoning_effort: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": canonical_to_openai_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if include_reasoning_effort and self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = True
        return payload

    async def stream_turn(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float = 0.25,
        max_tokens: int = 220,
    ) -> AsyncIterator[LLMStreamChunk]:
        if not self.is_configured():
            raise ProviderRateLimitOrTransientError(f"{self.provider_name} API key is not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        timeout = httpx.Timeout(
            connect=min(5.0, self.timeout_seconds),
            read=self.timeout_seconds,
            write=5.0,
            pool=5.0,
        )

        include_reasoning = bool(self.reasoning_effort)
        for attempt in range(2):
            payload = self._build_payload(
                messages, tools, temperature, max_tokens, include_reasoning_effort=include_reasoning
            )
            partial_tools: dict[int, dict[str, str]] = {}
            think_filter = ThinkTagFilter()
            retry_without_reasoning = False

            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as resp:
                        if resp.status_code == 400 and include_reasoning and attempt == 0:
                            body = (await resp.aread()).decode("utf-8", errors="ignore")
                            if "reasoning" in body.lower() or "parallel_tool_calls" in body.lower():
                                include_reasoning = False
                                retry_without_reasoning = True
                                continue
                            raise ProviderRateLimitOrTransientError(
                                f"{self.provider_name} ({self.model_name}) HTTP 400: {body[:250]}",
                                status_code=400,
                            )

                        if resp.status_code == 429:
                            body = (await resp.aread()).decode("utf-8", errors="ignore")
                            raise ProviderRateLimitOrTransientError(
                                f"{self.provider_name} ({self.model_name}) HTTP 429 rate limited: {body[:200]}",
                                status_code=429,
                                retry_after_seconds=_parse_retry_after(resp.headers),
                                is_rate_limit=True,
                            )

                        if resp.status_code == 403:
                            body = (await resp.aread()).decode("utf-8", errors="ignore")
                            raise ProviderRateLimitOrTransientError(
                                f"{self.provider_name} ({self.model_name}) HTTP 403 blocked: {body[:200]}",
                                status_code=403,
                                retry_after_seconds=3600.0,
                                is_rate_limit=True,
                            )

                        if resp.status_code in (404, 408) or resp.status_code >= 500:
                            body = (await resp.aread()).decode("utf-8", errors="ignore")
                            raise ProviderRateLimitOrTransientError(
                                f"{self.provider_name} ({self.model_name}) HTTP {resp.status_code}: {body[:220]}",
                                status_code=resp.status_code,
                            )

                        if resp.status_code >= 400:
                            body = (await resp.aread()).decode("utf-8", errors="ignore")
                            raise ProviderRateLimitOrTransientError(
                                f"{self.provider_name} ({self.model_name}) HTTP {resp.status_code}: {body[:220]}",
                                status_code=resp.status_code,
                            )

                        async for raw_line in resp.aiter_lines():
                            line = raw_line.strip()
                            if not line or not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                event = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            if "error" in event:
                                err = event["error"]
                                code = err.get("code") if isinstance(err, dict) else None
                                is_rl = str(code) == "429" or "rate" in str(err).lower()
                                raise ProviderRateLimitOrTransientError(
                                    f"{self.provider_name} stream error: {err}",
                                    is_rate_limit=is_rl,
                                )

                            choices = event.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}

                            # Explicitly ignore delta.reasoning / delta.reasoning_content
                            content = delta.get("content")
                            if content:
                                clean = think_filter.feed(content)
                                if clean:
                                    yield LLMStreamChunk(
                                        type="token",
                                        text=clean,
                                        provider=self.provider_name,
                                        model=self.model_name,
                                    )

                            for tc_delta in delta.get("tool_calls") or []:
                                idx = int(tc_delta.get("index", 0))
                                bucket = partial_tools.setdefault(
                                    idx,
                                    {"id": "", "name": "", "arguments": ""},
                                )
                                if tc_delta.get("id"):
                                    bucket["id"] = tc_delta["id"]
                                fn = tc_delta.get("function") or {}
                                if fn.get("name"):
                                    bucket["name"] += fn["name"]
                                if fn.get("arguments"):
                                    bucket["arguments"] += fn["arguments"]

            except (httpx.TimeoutException, httpx.RequestError) as exc:
                raise ProviderRateLimitOrTransientError(
                    f"{self.provider_name} ({self.model_name}) network/timeout error: {exc}"
                ) from exc

            if retry_without_reasoning:
                continue

            tail = think_filter.flush()
            if tail:
                yield LLMStreamChunk(
                    type="token",
                    text=tail,
                    provider=self.provider_name,
                    model=self.model_name,
                )

            if partial_tools:
                parsed_calls: list[ToolCallRequest] = []
                for idx in sorted(partial_tools):
                    item = partial_tools[idx]
                    raw_args = item["arguments"].strip()
                    try:
                        parsed_args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        parsed_args = {}
                    parsed_calls.append(
                        ToolCallRequest(
                            id=item["id"] or f"call_{uuid.uuid4().hex[:10]}",
                            name=item["name"],
                            arguments=parsed_args if isinstance(parsed_args, dict) else {},
                        )
                    )
                yield LLMStreamChunk(
                    type="tool_calls",
                    tool_calls=parsed_calls,
                    provider=self.provider_name,
                    model=self.model_name,
                )

            yield LLMStreamChunk(
                type="done",
                provider=self.provider_name,
                model=self.model_name,
            )
            return

"""Canonical message types and LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal


class ProviderRateLimitOrTransientError(Exception):
    """Raised on 429, 5xx, timeout, decommissioned model, or network error to trigger failover."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        is_rate_limit: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.is_rate_limit = is_rate_limit


class ProviderConfigError(Exception):
    """Raised when provider API key is missing or invalid."""


class AllProvidersUnavailableError(Exception):
    """Raised when no configured LLM provider succeeded."""


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    extra_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass
class LLMStreamChunk:
    type: Literal["token", "tool_calls", "done"]
    text: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    provider: str = ""
    model: str = ""


class LLMProvider(ABC):
    """Abstract streaming LLM provider supporting tool/function calling."""

    provider_name: str = "base"
    model_name: str = "unknown"

    @property
    def key(self) -> str:
        return f"{self.provider_name}:{self.model_name}"

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if credentials exist for this provider."""

    @abstractmethod
    async def stream_turn(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float = 0.25,
        max_tokens: int = 220,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Yield LLMStreamChunk items for one model turn."""
        raise NotImplementedError

"""OpenRouter :free LLM provider (optional fallback 2)."""

from __future__ import annotations

from app.config import PROJECT_NAME
from app.llm.openai_compat import OpenAICompatProvider


class OpenRouterProvider(OpenAICompatProvider):
    def __init__(
        self,
        api_key: str,
        model_name: str = "openrouter/free",
        timeout_seconds: float = 15.0,
    ) -> None:
        super().__init__(
            provider_name="openrouter",
            model_name=model_name,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=timeout_seconds,
            reasoning_effort="low",
            extra_headers={
                "HTTP-Referer": "https://localhost",
                "X-Title": f"{PROJECT_NAME} Virtual KAM Prototype",
            },
        )

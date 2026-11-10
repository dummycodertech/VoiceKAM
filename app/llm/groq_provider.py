"""Groq Free Tier LLM provider + startup /models live discovery."""

from __future__ import annotations

import httpx

from app.llm.openai_compat import OpenAICompatProvider


class GroqProvider(OpenAICompatProvider):
    def __init__(
        self,
        api_key: str,
        model_name: str = "openai/gpt-oss-20b",
        reasoning_effort: str = "low",
        timeout_seconds: float = 12.0,
    ) -> None:
        super().__init__(
            provider_name="groq",
            model_name=model_name,
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
        )


async def fetch_live_groq_models(api_key: str, timeout_seconds: float = 6.0) -> set[str] | None:
    """Query Groq /models to discover currently active model IDs."""
    key = api_key.strip()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            items = data.get("data") or []
            return {str(m.get("id")) for m in items if m.get("id")}
    except Exception:
        return None

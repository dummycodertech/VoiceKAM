"""Central branding and application configuration loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Central Branding Constants (change here to rename across backend, API & UI)
# ---------------------------------------------------------------------------
PROJECT_NAME = "VoiceKAM"
AGENT_NAME = "metis"
AGENT_NAME_DEVANAGARI = "metis"
# Spoken TTS pronunciation overrides per language (display UI keeps AGENT_NAME / AGENT_NAME_DEVANAGARI)
AGENT_NAME_SPOKEN_EN = AGENT_NAME
AGENT_NAME_SPOKEN_HI = AGENT_NAME_DEVANAGARI
FOOTER_NOTICE = "VoiceKAM Seller Intelligence. Not affiliated with any marketplace."
PRIVACY_NOTICE = (
    "Do not upload real or confidential seller data; "
    "free LLM tiers may use prompts to improve their products."
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Central Branding (overridable via env, defaulting to constants above)
    project_name: str = PROJECT_NAME
    agent_name: str = AGENT_NAME
    agent_name_devanagari: str = AGENT_NAME_DEVANAGARI
    agent_name_spoken_en: str = AGENT_NAME_SPOKEN_EN
    agent_name_spoken_hi: str = AGENT_NAME_SPOKEN_HI
    footer_notice: str = FOOTER_NOTICE
    privacy_notice: str = PRIVACY_NOTICE

    # Primary LLM: Groq Free Tier (ordered chain, filtered live via /models at startup)
    groq_api_key: str = ""
    groq_models: str = "openai/gpt-oss-20b,openai/gpt-oss-120b,qwen/qwen3.6-27b,qwen/qwen3.8-27b"
    groq_reasoning_effort: str = "low"

    # Fallback 1 LLM: Google Gemini Free Tier (excludes gemini-3.8-flash ~20 RPD cap)
    gemini_api_key: str = ""
    gemini_models: str = "gemini-3.5-flash-lite,gemini-3.6-flash"

    # Fallback 2 LLM: OpenRouter Free Tier (optional)
    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"

    # Rate limit / 429 cooldown
    provider_cooldown_seconds: int = 60

    # STT Fallback: Groq Whisper
    groq_whisper_model: str = "whisper-large-v3-turbo"

    # TTS Settings (2-tier): auto | edge-tts | browser
    tts_provider: str = "auto"
    tts_voice_en: str = "en-IN-NeerjaNeural"
    tts_voice_hi: str = "hi-IN-SwaraNeural"
    tts_cache_enabled: bool = True

    # Server & Runtime
    port: int = 7860
    llm_timeout_seconds: float = 12.0
    enable_gemini_live: bool = False
    allow_mock_llm: bool = False

    # Upload & Session Constraints
    max_upload_skus: int = 2000
    max_upload_bytes: int = 1_048_576
    max_request_bytes: int = 2_097_152
    session_ttl_seconds: int = 7200
    max_sessions: int = 200

    # Security, Quotas & CORS
    cors_origins: str = "*"
    daily_turn_cap: int = 400
    rate_limit_chat_per_10m: int = 30
    rate_limit_tts_per_10m: int = 120
    rate_limit_stt_per_10m: int = 30
    rate_limit_starts_per_hour: int = 20

    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    def groq_model_list(self) -> list[str]:
        return [m.strip() for m in self.groq_models.split(",") if m.strip()]

    def gemini_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]

    def spoken_agent_name(self, language: str = "en-IN") -> str:
        """Return configurable spoken TTS pronunciation for the persona name without changing UI text."""
        if language == "hi-IN":
            if "agent_name_spoken_hi" in self.model_fields_set or self.agent_name_spoken_hi != AGENT_NAME_SPOKEN_HI:
                return self.agent_name_spoken_hi.strip() or self.agent_name_devanagari
            return self.agent_name_devanagari
        if "agent_name_spoken_en" in self.model_fields_set or self.agent_name_spoken_en != AGENT_NAME_SPOKEN_EN:
            return self.agent_name_spoken_en.strip() or self.agent_name
        return self.agent_name

    def branding_dict(self) -> dict[str, str]:
        return {
            "project_name": self.project_name,
            "agent_name": self.agent_name,
            "agent_name_devanagari": self.agent_name_devanagari,
            "agent_name_spoken_en": self.spoken_agent_name("en-IN"),
            "agent_name_spoken_hi": self.spoken_agent_name("hi-IN"),
            "footer_notice": self.footer_notice,
            "privacy_notice": self.privacy_notice,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

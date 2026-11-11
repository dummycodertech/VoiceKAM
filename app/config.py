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
FOOTER_NOTICE = "Prototype. Uses synthetic data. Not affiliated with any marketplace."
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

    # TTS Settings: auto | edge-tts | gemini-tts | browser
    tts_provider: str = "auto"
    tts_voice_en: str = "en-IN-NeerjaNeural"
    tts_voice_hi: str = "hi-IN-SwaraNeural"
    gemini_tts_models: str = "gemini-3.8-flash-tts,gemini-2.5-flash-preview-tts"
    gemini_tts_voice: str = "Aoede"
    tts_cache_enabled: bool = True

    # Server & Runtime
    port: int = 7860
    llm_timeout_seconds: float = 12.0
    enable_gemini_live: bool = False
    allow_mock_llm: bool = False

    # Upload & Session Constraints
    max_upload_skus: int = 2000
    max_upload_bytes: int = 1_048_576
    session_ttl_seconds: int = 7200
    max_sessions: int = 200

    def groq_model_list(self) -> list[str]:
        return [m.strip() for m in self.groq_models.split(",") if m.strip()]

    def gemini_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]

    def gemini_tts_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_tts_models.split(",") if m.strip()]

    def branding_dict(self) -> dict[str, str]:
        return {
            "project_name": self.project_name,
            "agent_name": self.agent_name,
            "agent_name_devanagari": self.agent_name_devanagari,
            "footer_notice": self.footer_notice,
            "privacy_notice": self.privacy_notice,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

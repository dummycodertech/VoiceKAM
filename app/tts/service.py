"""2-Tier TTS service with RAM LRU audio cache and clean browser fallback.

Chain:
  1. edge-tts (en-IN-NeerjaNeural / hi-IN-SwaraNeural) -> audio/mpeg
  2. Signal client to use browser window.speechSynthesis cleanly (no red HTTP 500s)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import edge_tts

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class CachedAudio:
    audio_bytes: bytes
    media_type: str
    provider: str
    voice: str


class TTSService:
    """Manages edge-tts synthesis, LRU audio caching, cooldowns, and browser fallback."""

    def __init__(self, settings: Settings, max_cache_entries: int = 200) -> None:
        self.settings = settings
        self.max_cache_entries = max_cache_entries
        self._cache: OrderedDict[tuple[str, str, str], CachedAudio] = OrderedDict()
        self.edge_tts_cooldown_until: float = 0.0

    def voice_for_language(self, language: str) -> str:
        if language == "hi-IN":
            return self.settings.tts_voice_hi
        return self.settings.tts_voice_en

    def _get_cached(self, key: tuple[str, str, str]) -> CachedAudio | None:
        if not self.settings.tts_cache_enabled:
            return None
        item = self._cache.get(key)
        if item is not None:
            self._cache.move_to_end(key)
        return item

    def _put_cached(self, key: tuple[str, str, str], item: CachedAudio) -> None:
        if not self.settings.tts_cache_enabled:
            return
        self._cache[key] = item
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_cache_entries:
            self._cache.popitem(last=False)

    async def _synthesize_edge_tts(self, text: str, voice: str, timeout_seconds: float = 8.0) -> bytes:
        async with asyncio.timeout(timeout_seconds):
            comm = edge_tts.Communicate(text=text, voice=voice)
            chunks: list[bytes] = []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            data = b"".join(chunks)
            if not data:
                raise RuntimeError("edge-tts returned 0 audio bytes")
            return data

    async def synthesize(
        self,
        text: str,
        language: str = "en-IN",
        provider_override: str | None = None,
    ) -> tuple[CachedAudio | None, dict[str, Any]]:
        """Synthesize speech with caching + failover. Returns (CachedAudio|None, metadata)."""
        clean_text = text.strip()
        if not clean_text:
            return None, {"fallback": "browser", "reason": "empty_text", "cached": False}

        pref = (provider_override or self.settings.tts_provider or "auto").lower()
        voice = self.voice_for_language(language)

        if pref == "browser":
            return None, {"fallback": "browser", "reason": "user_selected_browser", "cached": False}

        cache_key = (pref, voice, clean_text)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached, {"provider": cached.provider, "voice": cached.voice, "cached": True}

        now = time.monotonic()
        errors: list[str] = []

        # Tier 1: edge-tts neural voices
        if pref in ("auto", "edge-tts") and now >= self.edge_tts_cooldown_until:
            try:
                mp3_bytes = await self._synthesize_edge_tts(clean_text, voice=voice)
                item = CachedAudio(
                    audio_bytes=mp3_bytes,
                    media_type="audio/mpeg",
                    provider="edge-tts",
                    voice=voice,
                )
                self._put_cached(cache_key, item)
                return item, {"provider": "edge-tts", "voice": voice, "cached": False}
            except Exception as exc:
                errors.append(f"edge-tts: {exc}")
                # Brief cooldown if datacenter IP blocked / failed
                self.edge_tts_cooldown_until = now + 30.0
                logger.warning("edge-tts failed (%s); falling back to browser TTS", exc)

        # Tier 2: Browser speechSynthesis fallback signal
        reason = "; ".join(errors) if errors else (f"unsupported_provider:{pref}" if pref not in ("auto", "edge-tts") else "tts_in_cooldown")
        return None, {
            "fallback": "browser",
            "reason": reason,
            "cached": False,
        }

"""3-Tier TTS service with startup Gemini TTS smoke test, 429 cooldown, and audio cache.

Chain:
  1. edge-tts (en-IN-NeerjaNeural / hi-IN-SwaraNeural) -> audio/mpeg
  2. Emergency Gemini Free TTS (newest passing model from GEMINI_TTS_MODELS, fallback gemini-2.5-flash-preview-tts) -> audio/wav
  3. Signal client to use browser window.speechSynthesis cleanly (no red HTTP 500s)
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import wave
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import edge_tts
from google import genai
from google.genai import types

from app.config import Settings

logger = logging.getLogger(__name__)


def _wrap_pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1, sampwidth: int = 2) -> bytes:
    if pcm_bytes.startswith(b"RIFF"):
        return pcm_bytes
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _parse_sample_rate(mime_type: str | None) -> int:
    if not mime_type:
        return 24000
    for part in mime_type.split(";"):
        part = part.strip().lower()
        if part.startswith("rate="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                pass
    return 24000


@dataclass
class CachedAudio:
    audio_bytes: bytes
    media_type: str
    provider: str
    voice: str


class TTSService:
    """Manages edge-tts, emergency Gemini Free TTS, caching, and cooldowns."""

    def __init__(self, settings: Settings, max_cache_entries: int = 200) -> None:
        self.settings = settings
        self.max_cache_entries = max_cache_entries
        self._cache: OrderedDict[tuple[str, str, str], CachedAudio] = OrderedDict()
        self.active_gemini_tts_model: str | None = None
        self.gemini_tts_smoke_tested: bool = False
        self.gemini_tts_smoke_report: list[dict[str, Any]] = []
        self.edge_tts_cooldown_until: float = 0.0
        self.gemini_tts_cooldown_until: float = 0.0

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

    async def startup_smoke_test(self) -> list[dict[str, Any]]:
        """Smoke-test configured Gemini TTS models at startup; pick the newest free passing one."""
        report: list[dict[str, Any]] = []
        if not self.settings.gemini_api_key.strip():
            self.gemini_tts_smoke_report = report
            return report

        for model_id in self.settings.gemini_tts_model_list():
            t0 = time.perf_counter()
            entry: dict[str, Any] = {
                "model": model_id,
                "smoke_ok": False,
                "bytes": 0,
                "latency_ms": 0,
                "error": None,
            }
            try:
                audio, _ = await self._synthesize_gemini_tts(
                    text="Namaste Rajesh ji.",
                    model_id=model_id,
                    timeout_seconds=10.0,
                )
                entry["smoke_ok"] = True
                entry["bytes"] = len(audio)
                if self.active_gemini_tts_model is None:
                    self.active_gemini_tts_model = model_id
            except Exception as exc:
                entry["error"] = str(exc)[:200]
            entry["latency_ms"] = round((time.perf_counter() - t0) * 1000)
            report.append(entry)
            logger.info("Gemini TTS startup smoke [%s]: %s", model_id, entry)
            if self.active_gemini_tts_model is not None:
                # Found newest passing free model; do not burn extra free TTS quota probing older models
                break

        self.gemini_tts_smoke_tested = True
        self.gemini_tts_smoke_report = report
        return report

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

    async def _synthesize_gemini_tts(
        self,
        text: str,
        model_id: str,
        timeout_seconds: float = 10.0,
    ) -> tuple[bytes, str]:
        if not self.settings.gemini_api_key.strip():
            raise RuntimeError("GEMINI_API_KEY not configured")

        client = genai.Client(api_key=self.settings.gemini_api_key.strip())
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=f"Speak warmly and clearly in an Indian conversational tone: {text}")],
            )
        ]
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.settings.gemini_tts_voice
                    )
                )
            ),
        )
        async with asyncio.timeout(timeout_seconds):
            resp = await client.aio.models.generate_content(
                model=model_id,
                contents=contents,
                config=config,
            )

        for cand in resp.candidates or []:
            if not cand.content or not cand.content.parts:
                continue
            for part in cand.content.parts:
                if part.inline_data and part.inline_data.data:
                    mime = getattr(part.inline_data, "mime_type", None)
                    rate = _parse_sample_rate(mime)
                    wav_bytes = _wrap_pcm_to_wav(part.inline_data.data, sample_rate=rate)
                    return wav_bytes, "audio/wav"

        raise RuntimeError(f"Gemini TTS ({model_id}) returned no inline audio")

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

        # Tier 1: edge-tts
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
                logger.warning("edge-tts failed (%s); falling back", exc)

        # Tier 2: Emergency Gemini Free TTS (only if startup probe passed or not yet probed or explicit override)
        gemini_allowed = (
            pref == "gemini-tts"
            or (pref == "auto" and (not self.gemini_tts_smoke_tested or self.active_gemini_tts_model is not None))
        )
        if gemini_allowed and self.settings.gemini_api_key.strip() and now >= self.gemini_tts_cooldown_until:
            candidate_models = (
                [self.active_gemini_tts_model]
                if self.active_gemini_tts_model
                else self.settings.gemini_tts_model_list()
            )
            for model_id in candidate_models:
                try:
                    wav_bytes, media_type = await self._synthesize_gemini_tts(clean_text, model_id=model_id)
                    item = CachedAudio(
                        audio_bytes=wav_bytes,
                        media_type=media_type,
                        provider=f"gemini-tts:{model_id}",
                        voice=self.settings.gemini_tts_voice,
                    )
                    self._put_cached(cache_key, item)
                    return item, {"provider": item.provider, "voice": item.voice, "cached": False}
                except Exception as exc:
                    msg = str(exc)
                    errors.append(f"gemini-tts({model_id}): {msg[:120]}")
                    if "429" in msg or "resource_exhausted" in msg.lower() or "quota" in msg.lower():
                        self.gemini_tts_cooldown_until = now + float(self.settings.provider_cooldown_seconds)
                        break

        # Tier 3: Browser speechSynthesis fallback signal
        return None, {
            "fallback": "browser",
            "reason": "; ".join(errors) if errors else "tts_in_cooldown",
            "cached": False,
        }

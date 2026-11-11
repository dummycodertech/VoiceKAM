"""FastAPI backend for VoiceKAM (single-service API + static frontend)."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent.orchestrator import AgentOrchestrator
from app.agent.session import CallMode, LanguageCode, SessionStore
from app.config import Settings, get_settings
from app.data.seed_data import SELLER_PROFILE
from app.engine import diagnosis
from app.llm.base import AllProvidersUnavailableError
from app.llm.router import LLMRouter
from app.tts.service import TTSService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class StartSessionRequest(BaseModel):
    mode: CallMode = "inbound"
    language: LanguageCode = "en-IN"
    prior_session_id: str | None = None


class ChatRequest(BaseModel):
    session_id: str
    text: str
    language: LanguageCode | None = None


class UpdateSettingsRequest(BaseModel):
    language: LanguageCode | None = None


class TTSRequest(BaseModel):
    text: str
    language: LanguageCode = "en-IN"
    provider: str | None = None


def _format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(
    settings_override: Settings | None = None,
    router_override: LLMRouter | None = None,
    tts_override: TTSService | None = None,
    skip_startup_probes: bool = False,
) -> FastAPI:
    settings = settings_override or get_settings()
    store = SessionStore()
    router = router_override or LLMRouter.from_settings(settings)
    tts_service = tts_override or TTSService(settings)
    orchestrator = AgentOrchestrator(router=router)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not skip_startup_probes:
            await router.initialize_and_smoke_test(settings)
            await tts_service.startup_smoke_test()
            # Pre-warm TTS cache for common greeting/filler lines (best effort)
            if settings.tts_cache_enabled:
                warmup_lines = [
                    (
                        f"Namaste {SELLER_PROFILE['salutation']}, I am {settings.agent_name}, "
                        f"your marketplace account manager. How can I help {SELLER_PROFILE['shop_name']} today?",
                        "en-IN",
                    ),
                    (
                        f"Ji {SELLER_PROFILE['salutation']}, main {settings.agent_name} bol rahi hoon, ek second aapka data check karti hoon.",
                        "hinglish",
                    ),
                    (
                        f"नमस्ते {SELLER_PROFILE['salutation']}, मैं {settings.agent_name_devanagari} बोल रही हूँ, एक सेकंड आपका डेटा देख रही हूँ।",
                        "hi-IN",
                    ),
                ]
                for txt, lang in warmup_lines:
                    try:
                        await tts_service.synthesize(txt, language=lang)
                    except Exception:
                        pass
        yield

    app = FastAPI(title=f"{settings.project_name} (Prototype - Mock Data)", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.router = router
    app.state.tts_service = tts_service
    app.state.orchestrator = orchestrator

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def root():
        index_path = FRONTEND_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return JSONResponse({"status": "ok", "message": "Frontend not built yet; API ready."})

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "prototype_notice": settings.footer_notice,
            "branding": settings.branding_dict(),
            "providers_configured": {
                "groq": bool(settings.groq_api_key.strip()),
                "gemini": bool(settings.gemini_api_key.strip()),
                "openrouter": bool(settings.openrouter_api_key.strip()),
                "mock": settings.allow_mock_llm,
            },
            "active_llm_chain": [p.key for p in router.configured_providers() if not router.is_disabled(p.key)],
            "disabled_providers": dict(router.disabled_providers),
            "llm_startup_smoke": router.startup_report,
            "tts_config": {
                "provider": settings.tts_provider,
                "voice_en": settings.tts_voice_en,
                "voice_hi": settings.tts_voice_hi,
                "active_gemini_tts_model": tts_service.active_gemini_tts_model,
                "gemini_tts_smoke": tts_service.gemini_tts_smoke_report,
            },
            "features": {
                "enable_gemini_live": settings.enable_gemini_live,
                "whisper_stt_fallback": bool(settings.groq_api_key.strip()),
            },
        }

    @app.get("/api/demo-data")
    async def demo_data(snapshot: str = "current") -> dict[str, Any]:
        snap = "followup" if snapshot == "followup" else "current"
        return {
            "overview": diagnosis.get_seller_overview(snap),
            "metrics": diagnosis.compute_headline_metrics(snap),
            "diagnosis": diagnosis.run_diagnosis(snap),
            "oos_skus": diagnosis.get_out_of_stock_skus(limit=5, snapshot=snap),
            "cancellations": diagnosis.get_cancellation_breakdown(snap),
            "pricing": diagnosis.get_pricing_gaps(snap),
            "funnel": diagnosis.get_visibility_funnel(snap),
            "action_plan": diagnosis.build_action_plan(snap),
        }

    @app.post("/api/session/start")
    async def start_session(req: StartSessionRequest) -> dict[str, Any]:
        session = store.create(
            mode=req.mode,
            language=req.language,
            prior_session_id=req.prior_session_id,
        )
        return {
            "session_id": session.session_id,
            "session": session.to_public_dict(),
            "demo_data": await demo_data(snapshot=session.snapshot),
        }

    @app.post("/api/session/{session_id}/greeting")
    async def stream_greeting(session_id: str) -> StreamingResponse:
        session = store.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        async def event_gen():
            try:
                async for ev in orchestrator.start_call(session):
                    yield _format_sse(ev["event"], ev["data"])
            except AllProvidersUnavailableError as exc:
                yield _format_sse("error", {"message": str(exc)})
            except Exception:
                logger.exception("Greeting stream failed")
                yield _format_sse("error", {"message": "Temporary server error while starting call. Please try again."})

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chat")
    async def chat_sse(req: ChatRequest) -> StreamingResponse:
        session = store.get(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if req.language:
            session.language = req.language

        async def event_gen():
            try:
                async for ev in orchestrator.run_turn(session, req.text):
                    yield _format_sse(ev["event"], ev["data"])
            except AllProvidersUnavailableError as exc:
                yield _format_sse("error", {"message": str(exc)})
            except Exception:
                logger.exception("Chat SSE turn failed")
                yield _format_sse("error", {"message": "Temporary server error while generating response. Please try again."})

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/session/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        session = store.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session.to_public_dict()

    @app.post("/api/session/{session_id}/settings")
    async def update_session_settings(session_id: str, req: UpdateSettingsRequest) -> dict[str, Any]:
        session = store.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if req.language:
            session.language = req.language
        return session.to_public_dict()

    @app.post("/api/tts")
    async def synthesize_tts(req: TTSRequest):
        audio, meta = await tts_service.synthesize(
            text=req.text,
            language=req.language,
            provider_override=req.provider,
        )
        if audio is None:
            return JSONResponse({"use_browser_tts": True, **meta}, status_code=200)
        return Response(
            content=audio.audio_bytes,
            media_type=audio.media_type,
            headers={
                "X-TTS-Provider": audio.provider,
                "X-TTS-Voice": audio.voice,
                "X-TTS-Cached": "true" if meta.get("cached") else "false",
            },
        )

    @app.get("/api/tts-probe")
    async def tts_probe_endpoint(
        text: str | None = None,
        language: LanguageCode = "en-IN",
    ) -> dict[str, Any]:
        """Diagnostic probe to verify whether edge-tts works from current host IPs."""
        import time

        probe_text = text or f"Namaste, I am {settings.agent_name}, testing neural voice output."
        voice = tts_service.voice_for_language(language)
        t0 = time.perf_counter()
        try:
            mp3_bytes = await tts_service._synthesize_edge_tts(probe_text, voice=voice, timeout_seconds=8.0)
            return {
                "edge_tts_ok": True,
                "voice": voice,
                "bytes": len(mp3_bytes),
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "recommended_tts_mode": "edge-tts",
            }
        except Exception as exc:
            return {
                "edge_tts_ok": False,
                "voice": voice,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "error_type": type(exc).__name__,
                "error": str(exc)[:220],
                "recommended_tts_mode": "browser speechSynthesis fallback",
            }

    @app.post("/api/stt")
    async def whisper_stt_fallback(
        audio: UploadFile = File(...),
        language: str = Form("en"),
    ) -> dict[str, Any]:
        if not settings.groq_api_key.strip():
            raise HTTPException(status_code=503, detail="GROQ_API_KEY not configured for Whisper STT fallback")
        raw = await audio.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty audio upload")

        lang_hint = "hi" if language == "hi-IN" else "en"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {settings.groq_api_key.strip()}"},
                    data={"model": settings.groq_whisper_model, "language": lang_hint},
                    files={"file": (audio.filename or "speech.webm", raw, audio.content_type or "audio/webm")},
                )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Groq Whisper error {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
            return {"text": (payload.get("text") or "").strip(), "provider": "groq-whisper"}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Whisper STT failed: {exc}") from exc

    return app


app = create_app()


"""FastAPI backend for VoiceKAM (single-service API + static frontend)."""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent.orchestrator import AgentOrchestrator
from app.agent.session import CallMode, LanguageCode, SessionStore
from app.config import Settings, get_settings
from app.data.context import SellerContext
from app.data.seed_data import get_scenario_context, list_scenarios
from app.data.upload import (
    DatasetValidationError,
    UploadedDatasetStore,
    build_template_payload,
    parse_uploaded_csv,
    parse_uploaded_json,
)
from app.engine import diagnosis
from app.llm.base import AllProvidersUnavailableError
from app.llm.router import LLMRouter
from app.tts.service import TTSService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

_SECRET_REDACT_RE = re.compile(
    r"(?:gsk_[A-Za-z0-9_-]+|AIza[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]+)",
    re.IGNORECASE,
)


def _scrub_secrets(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_REDACT_RE.sub("[REDACTED]", value)
    if isinstance(value, list):
        return [_scrub_secrets(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_secrets(v) for k, v in value.items()}
    return value


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded.strip():
        first_hop = forwarded.split(",", 1)[0].strip()
        if first_hop:
            return first_hop[:64]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class SlidingWindowRateLimiter:
    """In-memory per-IP sliding window rate limiter (bounded by passive expiry)."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def check_and_record(
        self,
        bucket: str,
        ip: str,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> tuple[bool, int]:
        if limit <= 0 or window_seconds <= 0:
            return True, 0
        ts = time.monotonic() if now is None else now
        cutoff = ts - window_seconds
        key = (bucket, ip)
        q = self._hits.get(key)
        if q is None:
            q = deque()
            self._hits[key] = q
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= limit:
            retry_after = max(1, math.ceil(window_seconds - (ts - q[0])))
            return False, retry_after
        q.append(ts)
        # Periodically prune completely empty keys if map grows large
        if len(self._hits) > 2000:
            stale = [k for k, vals in self._hits.items() if not vals or vals[-1] <= cutoff]
            for k in stale:
                self._hits.pop(k, None)
        return True, 0


class DailyTurnTracker:
    """Global per-UTC-day LLM turn counter enforcing DAILY_TURN_CAP."""

    def __init__(self) -> None:
        self._date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        self._count = 0

    def _roll_if_needed(self) -> None:
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        if today != self._date:
            self._date = today
            self._count = 0

    def consume(self, cap: int) -> bool:
        self._roll_if_needed()
        if cap > 0 and self._count >= cap:
            return False
        self._count += 1
        return True

    @property
    def current_count(self) -> int:
        self._roll_if_needed()
        return self._count


class StartSessionRequest(BaseModel):
    mode: CallMode = "inbound"
    language: LanguageCode = "en-IN"
    prior_session_id: str | None = None
    scenario_id: str | None = None
    dataset_id: str | None = None


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


def _build_demo_payload(ctx: SellerContext, snapshot: str = "current") -> dict[str, Any]:
    snap = "followup" if snapshot == "followup" else "current"
    return {
        "overview": diagnosis.get_seller_overview(ctx, snap),
        "metrics": diagnosis.compute_headline_metrics(ctx, snap),
        "diagnosis": diagnosis.run_diagnosis(ctx, snap),
        "oos_skus": diagnosis.get_out_of_stock_skus(ctx, limit=5, snapshot=snap),
        "cancellations": diagnosis.get_cancellation_breakdown(ctx, snap),
        "pricing": diagnosis.get_pricing_gaps(ctx, snap),
        "funnel": diagnosis.get_visibility_funnel(ctx, snap),
        "action_plan": diagnosis.build_action_plan(ctx, snap),
        "dataset": ctx.summary_dict(),
    }


def _rate_limit_response(action_label: str, retry_after: int) -> JSONResponse:
    msg = f"Too many {action_label} requests from this address. Please wait {retry_after} seconds and try again."
    return JSONResponse(
        {"detail": msg, "message": msg, "retry_after_seconds": retry_after},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


def create_app(
    settings_override: Settings | None = None,
    router_override: LLMRouter | None = None,
    tts_override: TTSService | None = None,
    skip_startup_probes: bool = False,
) -> FastAPI:
    settings = settings_override or get_settings()
    store = SessionStore(
        ttl_seconds=settings.session_ttl_seconds,
        max_sessions=settings.max_sessions,
    )
    dataset_store = UploadedDatasetStore(
        ttl_seconds=settings.session_ttl_seconds,
        max_entries=settings.max_sessions,
    )
    rate_limiter = SlidingWindowRateLimiter()
    daily_turns = DailyTurnTracker()
    router = router_override or LLMRouter.from_settings(settings)
    tts_service = tts_override or TTSService(settings)
    orchestrator = AgentOrchestrator(router=router)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not skip_startup_probes:
            await router.initialize_and_smoke_test(settings)
            if settings.tts_cache_enabled:
                default_profile = get_scenario_context().profile
                warmup_lines = [
                    (
                        f"Namaste {default_profile.salutation}, I am {settings.agent_name}, "
                        f"your marketplace account manager. How can I help {default_profile.shop_name} today?",
                        "en-IN",
                    ),
                    (
                        f"Ji {default_profile.salutation}, main {settings.agent_name} bol rahi hoon, ek second aapka data check karti hoon.",
                        "hinglish",
                    ),
                    (
                        f"नमस्ते {default_profile.salutation}, मैं {settings.agent_name_devanagari} बोल रही हूँ, एक सेकंड आपका डेटा देख रही हूँ।",
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
    app.state.dataset_store = dataset_store
    app.state.rate_limiter = rate_limiter
    app.state.daily_turns = daily_turns
    app.state.router = router
    app.state.tts_service = tts_service
    app.state.orchestrator = orchestrator

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_and_size_guard(request: Request, call_next):
        cl_raw = request.headers.get("content-length")
        if cl_raw is not None:
            try:
                if int(cl_raw) > settings.max_request_bytes:
                    return JSONResponse(
                        {
                            "detail": (
                                f"Request body exceeds maximum allowed size "
                                f"({settings.max_request_bytes // 1024} KB)."
                            )
                        },
                        status_code=413,
                    )
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length header."}, status_code=400)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), payment=(), usb=(), microphone=(self)",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        return response

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
            "llm_startup_smoke": _scrub_secrets(router.startup_report),
            "tts_config": {
                "provider": settings.tts_provider,
                "chain": ["edge-tts", "browser"],
                "voice_en": settings.tts_voice_en,
                "voice_hi": settings.tts_voice_hi,
                "cache_enabled": settings.tts_cache_enabled,
            },
            "features": {
                "enable_gemini_live": settings.enable_gemini_live,
                "whisper_stt_fallback": bool(settings.groq_api_key.strip()),
                "max_upload_skus": settings.max_upload_skus,
                "max_upload_bytes": settings.max_upload_bytes,
                "max_request_bytes": settings.max_request_bytes,
                "daily_turn_cap": settings.daily_turn_cap,
            },
        }

    @app.get("/api/scenarios")
    async def scenarios() -> dict[str, Any]:
        return {"scenarios": list_scenarios()}

    @app.get("/api/data/template")
    async def download_data_template(format: str = "csv"):
        content, media_type, filename = build_template_payload(format)
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def _parse_uploaded_form_or_json(request: Request) -> tuple[SellerContext, dict[str, Any]]:
        content_type = (request.headers.get("content-type") or "").lower()
        if "multipart/form-data" in content_type:
            form = await request.form()
            meta = {
                k: str(form.get(k) or "")
                for k in (
                    "seller_name",
                    "salutation",
                    "shop_name",
                    "city",
                    "category",
                    "period_prev_label",
                    "period_curr_label",
                )
                if form.get(k)
            }
            main_file = form.get("file")
            if main_file is None or not hasattr(main_file, "read"):
                raise DatasetValidationError(["Please select a CSV or JSON file to upload."])
            raw_bytes = await main_file.read()
            if len(raw_bytes) > settings.max_upload_bytes:
                raise DatasetValidationError(
                    [f"Uploaded file exceeds the {settings.max_upload_bytes // 1024} KB size limit."]
                )
            filename = str(getattr(main_file, "filename", "") or "").lower()
            cancel_file = form.get("cancellations_file")
            cancel_bytes: bytes | None = None
            if cancel_file is not None and hasattr(cancel_file, "read"):
                cancel_bytes = await cancel_file.read()
                if len(cancel_bytes) > settings.max_upload_bytes:
                    raise DatasetValidationError(["Cancellations CSV exceeds the maximum allowed size."])

            if filename.endswith(".json") or raw_bytes.lstrip().startswith((b"{", b"[")):
                ctx = parse_uploaded_json(raw_bytes, settings=settings)
            else:
                ctx = parse_uploaded_csv(
                    raw_bytes,
                    cancellations_csv_bytes=cancel_bytes,
                    seller_meta=meta,
                    settings=settings,
                )
            return ctx, {str(k): v for k, v in form.items() if isinstance(v, str)}

        raw_body = await request.body()
        if len(raw_body) > settings.max_upload_bytes:
            raise DatasetValidationError(
                [f"Request JSON payload exceeds the {settings.max_upload_bytes // 1024} KB size limit."]
            )
        ctx = parse_uploaded_json(raw_body, settings=settings)
        return ctx, {}

    @app.post("/api/data/validate")
    async def validate_uploaded_data(request: Request):
        ok, retry = rate_limiter.check_and_record(
            "start", _client_ip(request), settings.rate_limit_starts_per_hour, 3600
        )
        if not ok:
            return _rate_limit_response("dataset validation", retry)

        try:
            ctx, _ = await _parse_uploaded_form_or_json(request)
        except DatasetValidationError as exc:
            return JSONResponse({"valid": False, "errors": exc.errors}, status_code=422)

        dataset_id = dataset_store.put(ctx)
        diag = diagnosis.run_diagnosis(ctx, "current")
        return {
            "valid": True,
            "dataset_id": dataset_id,
            "summary": ctx.summary_dict(),
            "completeness": diag["completeness"],
            "diagnosis_preview": {
                "total_lost_orders": diag["total_lost_orders"],
                "total_lost_revenue": diag["total_lost_revenue"],
                "missing_signals": diag["missing_signals"],
            },
            "errors": [],
        }

    def _resolve_context(
        scenario: str | None = None,
        dataset_id: str | None = None,
    ) -> SellerContext:
        if dataset_id:
            ctx = dataset_store.get(dataset_id)
            if ctx is None:
                raise HTTPException(
                    status_code=404,
                    detail="Uploaded dataset was not found or expired with the session TTL. Please re-upload.",
                )
            return ctx
        return get_scenario_context(scenario)

    @app.get("/api/demo-data")
    async def demo_data(
        snapshot: str = "current",
        scenario: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        ctx = _resolve_context(scenario=scenario, dataset_id=dataset_id)
        return _build_demo_payload(ctx, snapshot=snapshot)

    @app.post("/api/session/start")
    async def start_session(request: Request):
        ok, retry = rate_limiter.check_and_record(
            "start", _client_ip(request), settings.rate_limit_starts_per_hour, 3600
        )
        if not ok:
            return _rate_limit_response("session start", retry)

        content_type = (request.headers.get("content-type") or "").lower()
        mode: CallMode = "inbound"
        language: LanguageCode = "en-IN"
        prior_session_id: str | None = None
        scenario_id: str | None = None
        dataset_id: str | None = None
        resolved_ctx: SellerContext | None = None

        if "multipart/form-data" in content_type:
            form = await request.form()
            mode_val = str(form.get("mode") or "inbound")
            if mode_val in ("inbound", "proactive", "followup"):
                mode = mode_val  # type: ignore[assignment]
            lang_val = str(form.get("language") or "en-IN")
            if lang_val in ("en-IN", "hi-IN", "hinglish"):
                language = lang_val  # type: ignore[assignment]
            prior_session_id = str(form.get("prior_session_id")) if form.get("prior_session_id") else None
            scenario_id = str(form.get("scenario_id")) if form.get("scenario_id") else None
            dataset_id = str(form.get("dataset_id")) if form.get("dataset_id") else None

            if form.get("file") is not None:
                try:
                    resolved_ctx, _ = await _parse_uploaded_form_or_json(request)
                except DatasetValidationError as exc:
                    return JSONResponse(
                        {"valid": False, "detail": exc.errors[0], "errors": exc.errors},
                        status_code=422,
                    )
        else:
            raw_body = await request.body()
            if len(raw_body) > settings.max_request_bytes:
                return JSONResponse({"detail": "Request payload exceeds maximum allowed size."}, status_code=413)
            payload_obj: dict[str, Any] = {}
            if raw_body.strip():
                try:
                    payload_obj = json.loads(raw_body.decode("utf-8"))
                except Exception as exc:
                    raise HTTPException(status_code=400, detail=f"Invalid JSON request: {exc}") from exc
            req = StartSessionRequest(**payload_obj)
            mode = req.mode
            language = req.language
            prior_session_id = req.prior_session_id
            scenario_id = req.scenario_id
            dataset_id = req.dataset_id

        if resolved_ctx is None:
            if dataset_id:
                resolved_ctx = _resolve_context(dataset_id=dataset_id)
            elif scenario_id:
                resolved_ctx = _resolve_context(scenario=scenario_id)

        session = store.create(
            mode=mode,
            language=language,
            prior_session_id=prior_session_id,
            seller_context=resolved_ctx,
        )
        return {
            "session_id": session.session_id,
            "session": session.to_public_dict(),
            "demo_data": _build_demo_payload(session.seller_context, snapshot=session.snapshot),
        }

    @app.post("/api/session/{session_id}/greeting")
    async def stream_greeting(session_id: str, request: Request):
        ok, retry = rate_limiter.check_and_record(
            "chat", _client_ip(request), settings.rate_limit_chat_per_10m, 600
        )
        if not ok:
            return _rate_limit_response("call", retry)

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
    async def chat_sse(req: ChatRequest, request: Request):
        ok, retry = rate_limiter.check_and_record(
            "chat", _client_ip(request), settings.rate_limit_chat_per_10m, 600
        )
        if not ok:
            return _rate_limit_response("chat", retry)

        session = store.get(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if req.language:
            session.language = req.language

        if not daily_turns.consume(settings.daily_turn_cap):
            async def cap_gen():
                yield _format_sse(
                    "error",
                    {"message": "This public demo has reached its daily capacity. Please try again tomorrow."},
                )

            return StreamingResponse(
                cap_gen(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

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
    async def synthesize_tts(req: TTSRequest, request: Request):
        ok, retry = rate_limiter.check_and_record(
            "tts", _client_ip(request), settings.rate_limit_tts_per_10m, 600
        )
        if not ok:
            return _rate_limit_response("voice synthesis", retry)

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
        request: Request,
        text: str | None = None,
        language: LanguageCode = "en-IN",
    ):
        """Diagnostic probe to verify whether edge-tts works from current host IPs."""
        ok, retry = rate_limiter.check_and_record(
            "tts", _client_ip(request), settings.rate_limit_tts_per_10m, 600
        )
        if not ok:
            return _rate_limit_response("voice probe", retry)

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
        request: Request,
        audio: UploadFile = File(...),
        language: str = Form("en"),
    ) -> dict[str, Any]:
        ok, retry = rate_limiter.check_and_record(
            "stt", _client_ip(request), settings.rate_limit_stt_per_10m, 600
        )
        if not ok:
            return _rate_limit_response("speech-to-text", retry)  # type: ignore[return-value]

        if not settings.groq_api_key.strip():
            raise HTTPException(status_code=503, detail="GROQ_API_KEY not configured for Whisper STT fallback")
        raw = await audio.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty audio upload")
        if len(raw) > settings.max_request_bytes:
            raise HTTPException(status_code=413, detail="Audio upload exceeds maximum allowed size")

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

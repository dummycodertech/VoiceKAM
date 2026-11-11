"""Automated v1 API & SSE contract regression tests (matches docs/API_CONTRACT.md)."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.base import (
    CanonicalMessage,
    LLMProvider,
    LLMStreamChunk,
    ProviderRateLimitOrTransientError,
)
from app.llm.mock_provider import MockLLMProvider
from app.llm.router import LLMRouter
from app.main import create_app
from app.tts.service import TTSService


def _parse_sse(raw_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw_text.strip().split("\n\n"):
        if not block.strip():
            continue
        ev_name = "message"
        ev_data: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                ev_name = line[6:].strip()
            elif line.startswith("data:"):
                ev_data = json.loads(line[5:].strip())
        events.append({"event": ev_name, "data": ev_data})
    return events


def _make_app(providers: list[LLMProvider] | None = None, tts_override: TTSService | None = None):
    settings = Settings(
        groq_api_key="",
        gemini_api_key="",
        openrouter_api_key="",
        allow_mock_llm=True,
        tts_cache_enabled=True,
    )
    router = LLMRouter(providers=providers if providers is not None else [MockLLMProvider()])
    return create_app(
        settings_override=settings,
        router_override=router,
        tts_override=tts_override,
        skip_startup_probes=True,
    ), router


class _FailingProvider(LLMProvider):
    def __init__(
        self,
        provider_name: str,
        model_name: str,
        exc: ProviderRateLimitOrTransientError,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.exc = exc
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    async def stream_turn(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float = 0.25,
        max_tokens: int = 220,
    ) -> AsyncIterator[LLMStreamChunk]:
        self.calls += 1
        raise self.exc
        yield  # pragma: no cover


class _OkProvider(LLMProvider):
    def __init__(self, provider_name: str = "gemini", model_name: str = "gemini-3.5-flash-lite") -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    async def stream_turn(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float = 0.25,
        max_tokens: int = 220,
    ) -> AsyncIterator[LLMStreamChunk]:
        self.calls += 1
        yield LLMStreamChunk(
            type="token",
            text="Got it, Vikram ji. Let me check your account.",
            provider=self.provider_name,
            model=self.model_name,
        )
        yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)


def test_rest_endpoints_v1_contract_shapes(monkeypatch):
    app, _ = _make_app()
    client = TestClient(app)

    # /api/health
    h = client.get("/api/health")
    assert h.status_code == 200
    h_json = h.json()
    for req_key in (
        "status",
        "prototype_notice",
        "providers_configured",
        "active_llm_chain",
        "disabled_providers",
        "llm_startup_smoke",
        "tts_config",
        "features",
    ):
        assert req_key in h_json, f"Missing /api/health key: {req_key}"

    # /api/demo-data (current & followup)
    for snap in ("current", "followup"):
        d = client.get(f"/api/demo-data?snapshot={snap}")
        assert d.status_code == 200
        d_json = d.json()
        for req_key in (
            "overview",
            "metrics",
            "diagnosis",
            "oos_skus",
            "cancellations",
            "pricing",
            "funnel",
            "action_plan",
        ):
            assert req_key in d_json, f"Missing /api/demo-data ({snap}) key: {req_key}"

    # /api/session/start, /api/session/{id}, /api/session/{id}/settings
    s = client.post("/api/session/start", json={"mode": "inbound", "language": "en-IN"})
    assert s.status_code == 200
    s_json = s.json()
    assert {"session_id", "session", "demo_data"} <= set(s_json.keys())
    sid = s_json["session_id"]

    upd = client.post(f"/api/session/{sid}/settings", json={"language": "hinglish"})
    assert upd.status_code == 200
    assert upd.json()["language"] == "hinglish"

    get_s = client.get(f"/api/session/{sid}")
    assert get_s.status_code == 200
    assert get_s.json()["session_id"] == sid

    assert client.get("/api/session/nonexistent999").status_code == 404

    # /api/tts binary audio vs browser JSON fallback contract
    svc: TTSService = app.state.tts_service

    async def fake_edge(text: str, voice: str, timeout_seconds: float = 8.0) -> bytes:
        return b"ID3-CONTRACT-MP3"

    monkeypatch.setattr(svc, "_synthesize_edge_tts", fake_edge)
    tts_ok = client.post("/api/tts", json={"text": "Hello Vikram ji", "language": "en-IN"})
    assert tts_ok.status_code == 200
    assert tts_ok.headers["content-type"].startswith("audio/mpeg")
    assert "x-tts-provider" in tts_ok.headers
    assert "x-tts-voice" in tts_ok.headers
    assert tts_ok.headers.get("x-tts-cached") in ("true", "false")

    tts_fb = client.post(
        "/api/tts",
        json={"text": "Hello Vikram ji", "language": "en-IN", "provider": "browser"},
    )
    assert tts_fb.status_code == 200
    fb_json = tts_fb.json()
    assert fb_json["use_browser_tts"] is True
    assert fb_json["fallback"] == "browser"
    assert "reason" in fb_json and "cached" in fb_json

    # /api/stt error shape without key / empty audio
    stt_err = client.post(
        "/api/stt",
        files={"audio": ("speech.webm", b"123", "audio/webm")},
        data={"language": "en"},
    )
    assert stt_err.status_code == 503
    assert "detail" in stt_err.json()


def test_sse_preexec_tools_and_phase_change_contract():
    app, _ = _make_app()
    client = TestClient(app)

    start = client.post("/api/session/start", json={"mode": "proactive", "language": "en-IN"})
    sid = start.json()["session_id"]

    # Proactive GREETING pre-executes overview + metric_comparison before spoken text
    g_evs = _parse_sse(client.post(f"/api/session/{sid}/greeting").text)
    g_names = [e["event"] for e in g_evs]
    assert "phase" in g_names
    assert "tool_start" in g_names
    assert "tool_call" in g_names
    assert "tool_result" in g_names
    assert "token" in g_names
    assert "sentence" in g_names
    assert "latency" in g_names
    assert "done" in g_names

    first_spoken_idx = min(g_names.index("token"), g_names.index("sentence"))
    last_tool_start_idx = max(i for i, e in enumerate(g_evs) if e["event"] == "tool_start")
    last_tool_res_idx = max(i for i, e in enumerate(g_evs) if e["event"] == "tool_result")
    assert last_tool_start_idx < first_spoken_idx
    assert last_tool_res_idx < first_spoken_idx
    for tr in [e["data"] for e in g_evs if e["event"] == "tool_result"]:
        assert {"name", "result", "latency_ms", "source"} <= set(tr.keys())
        assert isinstance(tr["result"], dict) and tr["result"]

    # Turn 1: GREETING -> CLARIFY must emit phase_change
    t1 = _parse_sse(
        client.post(
            "/api/chat",
            json={"session_id": sid, "text": "Yes, I have 2 minutes. Please go ahead."},
        ).text
    )
    pc1 = [e["data"] for e in t1 if e["event"] == "phase_change"]
    assert len(pc1) == 1
    assert pc1[0] == {"phase": "CLARIFY", "previous_phase": "GREETING", "mode": "proactive"}

    # Turn 2: staying inside CLARIFY -> phase did NOT change, so phase_change must NOT be emitted
    t2 = _parse_sse(
        client.post(
            "/api/chat",
            json={"session_id": sid, "text": "Pressure cooker went out of stock last week."},
        ).text
    )
    assert t2[0]["event"] == "phase" and t2[0]["data"]["phase"] == "CLARIFY"
    assert not any(e["event"] == "phase_change" for e in t2)

    # Turn 3: transition CLARIFY -> DIAGNOSE emits phase_change AND all 5 pre-exec tool_start/tool_result BEFORE token/sentence
    t3 = _parse_sse(
        client.post("/api/chat", json={"session_id": sid, "text": "Check my data and tell me why."}).text
    )
    t3_names = [e["event"] for e in t3]
    pc3 = [e["data"] for e in t3 if e["event"] == "phase_change"]
    assert len(pc3) == 1
    assert pc3[0] == {"phase": "DIAGNOSE", "previous_phase": "CLARIFY", "mode": "proactive"}

    first_spoken_t3 = min(t3_names.index("token"), t3_names.index("sentence"))
    diag_starts = [e["data"]["name"] for i, e in enumerate(t3) if e["event"] == "tool_start" and i < first_spoken_t3]
    diag_results = [e["data"] for i, e in enumerate(t3) if e["event"] == "tool_result" and i < first_spoken_t3]
    expected_diag_tools = {
        "run_diagnosis",
        "get_out_of_stock_skus",
        "get_cancellation_breakdown",
        "get_pricing_gaps",
        "get_visibility_funnel",
    }
    assert set(diag_starts) == expected_diag_tools
    assert {r["name"] for r in diag_results} == expected_diag_tools
    assert all(isinstance(r["result"], dict) and len(r["result"]) > 0 for r in diag_results)

    # Turn 4 (RECOMMEND), Turn 5 (COMMIT), Turn 6 (SUMMARY)
    _parse_sse(client.post("/api/chat", json={"session_id": sid, "text": "Makes sense. What should I do?"}).text)
    t5 = _parse_sse(
        client.post(
            "/api/chat",
            json={"session_id": sid, "text": "I commit to restocking top 5 SKUs within 48 hours and syncing daily."},
        ).text
    )
    assert any(e["event"] == "tool_start" and e["data"]["source"] == "llm" for e in t5)
    assert any(e["event"] == "tool_result" and e["data"]["source"] == "llm" for e in t5)

    t6 = _parse_sse(client.post("/api/chat", json={"session_id": sid, "text": "Send summary please."}).text)
    assert any(e["event"] == "summary_card" for e in t6)
    done6 = next(e["data"] for e in t6 if e["event"] == "done")
    assert {"text", "speakable", "phase", "provider", "commitments", "followup_schedule", "summary_card"} <= set(
        done6.keys()
    )
    assert done6["summary_card"] is not None


def test_failover_event_is_sanitized_and_403_is_permanently_disabled():
    leaky_403 = _FailingProvider(
        "groq",
        "qwen/qwen3.8-27b",
        ProviderRateLimitOrTransientError(
            'groq (qwen/qwen3.8-27b) HTTP 403 blocked: {"error":{"message":"blocked in org_SECRET123 proj_ABC"}}',
            status_code=403,
            is_forbidden=True,
            reason_code="forbidden",
        ),
    )
    leaky_429 = _FailingProvider(
        "groq",
        "openai/gpt-oss-20b",
        ProviderRateLimitOrTransientError(
            'groq (openai/gpt-oss-20b) HTTP 429: {"error":{"message":"Rate limit in organization org_SECRET999"}}',
            status_code=429,
            retry_after_seconds=60.0,
            is_rate_limit=True,
            reason_code="rate_limited",
        ),
    )
    ok = _OkProvider("gemini", "gemini-3.5-flash-lite")

    app, router = _make_app(providers=[leaky_403, leaky_429, ok])
    client = TestClient(app)

    sid = client.post("/api/session/start", json={"mode": "inbound", "language": "en-IN"}).json()["session_id"]

    # Turn 1 triggers 403 on qwen -> 429 on gpt-oss-20b -> succeeds on gemini
    raw1 = client.post("/api/chat", json={"session_id": sid, "text": "Check my sales please."}).text
    evs1 = _parse_sse(raw1)
    failovers1 = [e["data"] for e in evs1 if e["event"] == "failover"]
    assert len(failovers1) == 2

    assert failovers1[0] == {
        "type": "provider_failover",
        "from": "groq:qwen/qwen3.8-27b",
        "to": "groq:openai/gpt-oss-20b",
        "reason_code": "forbidden",
    }
    assert failovers1[1] == {
        "type": "provider_failover",
        "from": "groq:openai/gpt-oss-20b",
        "to": "gemini:gemini-3.5-flash-lite",
        "reason_code": "rate_limited",
    }
    for fo in failovers1:
        assert "reason" not in fo
        serialized = json.dumps(fo)
        assert "org_SECRET" not in serialized
        assert "proj_ABC" not in serialized
        assert '{"error"' not in serialized

    assert leaky_403.calls == 1
    assert leaky_429.calls == 1
    assert router.is_disabled("groq:qwen/qwen3.8-27b")
    assert not router.is_in_cooldown("groq:qwen/qwen3.8-27b")
    assert router.is_in_cooldown("groq:openai/gpt-oss-20b")

    # Verify /api/health hides permanently disabled 403 model from active_llm_chain & reports disabled_providers
    h_json = client.get("/api/health").json()
    assert "groq:qwen/qwen3.8-27b" not in h_json["active_llm_chain"]
    assert h_json["disabled_providers"].get("groq:qwen/qwen3.8-27b") == "forbidden"

    # Even if we force ALL cooldowns to expire (simulate >3600s elapsed), 403 model MUST stay disabled forever!
    router.cooldowns.clear()
    _ = client.post("/api/chat", json={"session_id": sid, "text": "Yes, some delays happened."}).text
    assert leaky_403.calls == 1, "HTTP 403 model must remain permanently disabled for server lifetime"
    assert leaky_429.calls == 2, "HTTP 429 model resumes after cooldown clears"


def test_all_providers_failed_error_event_does_not_leak_raw_upstream_json():
    p_to = _FailingProvider(
        "groq",
        "openai/gpt-oss-120b",
        ProviderRateLimitOrTransientError(
            'groq timeout org_LEAKED_ID {"error":{"secret":"raw"}}',
            reason_code="timeout",
        ),
    )
    p_500 = _FailingProvider(
        "gemini",
        "gemini-3.5-flash-lite",
        ProviderRateLimitOrTransientError(
            "503 Service Unavailable proj_LEAKED_999 Bearer sk-fake",
            status_code=503,
            reason_code="server_error",
        ),
    )
    app, _ = _make_app(providers=[p_to, p_500])
    client = TestClient(app)

    sid = client.post("/api/session/start", json={"mode": "inbound", "language": "en-IN"}).json()["session_id"]
    raw = client.post("/api/chat", json={"session_id": sid, "text": "Help me with my sales."}).text
    evs = _parse_sse(raw)
    err_ev = next(e["data"] for e in evs if e["event"] == "error")
    msg = err_ev["message"]
    assert "timeout" in msg and "server_error" in msg
    assert "org_LEAKED_ID" not in msg
    assert "proj_LEAKED_999" not in msg
    assert "sk-fake" not in msg
    assert '{"error"' not in msg


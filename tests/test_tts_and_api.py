"""Tests for TTS failover/cache and FastAPI REST + SSE endpoints."""

from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.mock_provider import MockLLMProvider
from app.llm.router import LLMRouter
from app.main import create_app
from app.tts.service import TTSService


def _make_test_app(tts_override: TTSService | None = None):
    settings = Settings(
        groq_api_key="",
        gemini_api_key="",
        openrouter_api_key="",
        allow_mock_llm=True,
        tts_cache_enabled=True,
    )
    router = LLMRouter(providers=[MockLLMProvider()])
    return create_app(
        settings_override=settings,
        router_override=router,
        tts_override=tts_override,
        skip_startup_probes=True,
    )


def _parse_sse(raw_text: str) -> list[dict]:
    events = []
    for block in raw_text.strip().split("\n\n"):
        if not block.strip():
            continue
        ev_name = "message"
        ev_data = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                ev_name = line[6:].strip()
            elif line.startswith("data:"):
                ev_data = json.loads(line[5:].strip())
        events.append({"event": ev_name, "data": ev_data})
    return events


def test_health_and_demo_data_endpoints():
    app = _make_test_app()
    client = TestClient(app)

    h = client.get("/api/health")
    assert h.status_code == 200
    body = h.json()
    assert body["status"] == "ok"
    assert "mock:deterministic-test-kam" in body["active_llm_chain"]

    d = client.get("/api/demo-data")
    assert d.status_code == 200
    d_json = d.json()
    m = d_json["metrics"]["metrics"]
    diag = d_json["diagnosis"]
    assert m["sales_gmv"]["current"] > 0
    assert m["sales_gmv"]["current"] < m["sales_gmv"]["baseline"]
    assert sum(dr["lost_orders"] for dr in diag["drivers"]) == diag["total_lost_orders"]


def test_full_sse_session_lifecycle():
    app = _make_test_app()
    client = TestClient(app)

    start = client.post("/api/session/start", json={"mode": "proactive", "language": "en-IN"})
    assert start.status_code == 200
    sid = start.json()["session_id"]

    # Greeting stream
    g_resp = client.post(f"/api/session/{sid}/greeting")
    assert g_resp.status_code == 200
    g_events = _parse_sse(g_resp.text)
    assert any(e["event"] == "sentence" for e in g_events)
    assert any(e["event"] == "done" for e in g_events)

    # Multi-turn conversation through CLOSE
    turns = [
        "Yes, I have 2 minutes. Please tell me what happened.",
        "Yes, pressure cooker went out of stock and we had some dispatch delays.",
        "Okay, check my data and tell me why.",
        "Makes sense. What should I do?",
        "Yes, I commit to restock top 5 within 48 hours and sync stock daily.",
        "Please send the summary.",
    ]
    all_events = []
    for msg in turns:
        r = client.post("/api/chat", json={"session_id": sid, "text": msg})
        assert r.status_code == 200
        evs = _parse_sse(r.text)
        all_events.extend(evs)
        assert any(e["event"] == "done" for e in evs)

    sess = client.get(f"/api/session/{sid}").json()
    assert len(sess["commitments"]) >= 1
    assert sess["followup_schedule"] is not None
    assert sess["summary_card"] is not None
    assert "whatsapp_text" in sess["summary_card"]
    assert sess["ungrounded_warnings"] == []


@pytest.mark.asyncio
async def test_tts_cache_and_browser_fallback_when_providers_fail(monkeypatch):
    settings = Settings(groq_api_key="", gemini_api_key="", tts_provider="auto", tts_cache_enabled=True)
    svc = TTSService(settings)

    calls = 0

    async def fake_edge(text: str, voice: str, timeout_seconds: float = 8.0) -> bytes:
        nonlocal calls
        calls += 1
        return b"ID3-FAKE-MP3-BYTES"

    monkeypatch.setattr(svc, "_synthesize_edge_tts", fake_edge)

    audio1, meta1 = await svc.synthesize("Namaste Vikram ji.", language="en-IN")
    assert audio1 is not None and meta1["cached"] is False and calls == 1

    audio2, meta2 = await svc.synthesize("Namaste Vikram ji.", language="en-IN")
    assert audio2 is not None and meta2["cached"] is True and calls == 1  # Hit cache!

    # Now simulate edge-tts failure -> falls back cleanly to browser speechSynthesis
    async def broken_edge(text: str, voice: str, timeout_seconds: float = 8.0) -> bytes:
        raise RuntimeError("Datacenter IP blocked")

    monkeypatch.setattr(svc, "_synthesize_edge_tts", broken_edge)
    audio3, meta3 = await svc.synthesize("Different uncached sentence.", language="en-IN")
    assert audio3 is None
    assert meta3["fallback"] == "browser"
    assert "Datacenter IP blocked" in meta3["reason"]


def test_tts_endpoint_returns_browser_fallback_json_cleanly():
    settings = Settings(groq_api_key="", gemini_api_key="", tts_provider="browser", allow_mock_llm=True)
    svc = TTSService(settings)
    app = _make_test_app(tts_override=svc)
    client = TestClient(app)

    r = client.post("/api/tts", json={"text": "Hello Vikram ji", "language": "en-IN"})
    assert r.status_code == 200
    assert r.json()["use_browser_tts"] is True


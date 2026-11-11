"""Security, quota, CORS, size-limit, secret-redaction, and session eviction tests."""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from app.agent.session import SessionStore
from app.config import Settings
from app.llm.mock_provider import MockLLMProvider
from app.llm.router import LLMRouter
from app.main import _scrub_secrets, create_app
from app.tts.service import TTSService


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        ev = "message"
        data = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        out.append((ev, data))
    return out


def _build_app(**overrides) -> tuple:
    base_kwargs = dict(
        allow_mock_llm=True,
        tts_provider="browser",
        tts_cache_enabled=False,
        groq_api_key="gsk_testsecretvalue12345",
        gemini_api_key="AIzaTestSecretKeyValue12345",
        cors_origins="https://demo.example.com,http://localhost:7860",
        rate_limit_starts_per_hour=20,
        rate_limit_chat_per_10m=30,
        rate_limit_tts_per_10m=120,
        daily_turn_cap=400,
        max_request_bytes=262_144,
    )
    base_kwargs.update(overrides)
    settings = Settings(**base_kwargs)
    router = LLMRouter(providers=[MockLLMProvider()])
    app = create_app(
        settings_override=settings,
        router_override=router,
        tts_override=TTSService(settings),
        skip_startup_probes=True,
    )
    return app, router


def test_security_headers_cors_and_health_expose_no_secrets():
    app, router = _build_app()
    # Inject a simulated upstream error containing a token pattern to verify scrubbing
    router.startup_report = [
        {"provider": "groq", "model": "m", "error": "401 Bearer secret_abc gsk_999999 AIzaSyFakeKey sk-abcdef123456"}
    ]
    client = TestClient(app)
    r = client.get("/api/health", headers={"Origin": "https://demo.example.com"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://demo.example.com"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "no-referrer"
    assert "camera=()" in (r.headers.get("permissions-policy") or "")

    raw_text = r.text
    for forbidden in ("gsk_", "AIza", "sk-abcdef", "secret_abc"):
        assert forbidden not in raw_text
    assert "[REDACTED]" in raw_text

    # Disallowed CORS origin does not receive allow-origin
    r_bad = client.get("/api/health", headers={"Origin": "https://evil.example.org"})
    assert "access-control-allow-origin" not in r_bad.headers

    # Scrub helper direct check
    assert "[REDACTED]" in _scrub_secrets("Authorization: Bearer tok_12345")


def test_per_ip_rate_limiting_uses_x_forwarded_for_first_hop():
    app, _ = _build_app(rate_limit_starts_per_hour=2, rate_limit_chat_per_10m=2, rate_limit_tts_per_10m=2)
    client = TestClient(app)

    ip1 = {"X-Forwarded-For": "203.0.113.10, 10.0.0.1"}
    ip2 = {"X-Forwarded-For": "198.51.100.77"}

    assert client.post("/api/session/start", json={"mode": "inbound"}, headers=ip1).status_code == 200
    s2 = client.post("/api/session/start", json={"mode": "inbound"}, headers=ip1)
    assert s2.status_code == 200
    sid = s2.json()["session_id"]

    blocked = client.post("/api/session/start", json={"mode": "inbound"}, headers=ip1)
    assert blocked.status_code == 429
    assert "too many" in blocked.json()["detail"].lower()

    # Different client IP still allowed
    assert client.post("/api/session/start", json={"mode": "inbound"}, headers=ip2).status_code == 200

    # Chat limit check
    assert client.post("/api/chat", json={"session_id": sid, "text": "Hi"}, headers=ip1).status_code == 200
    assert client.post("/api/chat", json={"session_id": sid, "text": "Sales dropped"}, headers=ip1).status_code == 200
    chat_429 = client.post("/api/chat", json={"session_id": sid, "text": "Third"}, headers=ip1)
    assert chat_429.status_code == 429

    # TTS limit check
    assert client.post("/api/tts", json={"text": "One", "language": "en-IN"}, headers=ip1).status_code == 200
    assert client.post("/api/tts", json={"text": "Two", "language": "en-IN"}, headers=ip1).status_code == 200
    assert client.post("/api/tts", json={"text": "Three", "language": "en-IN"}, headers=ip1).status_code == 429


def test_daily_turn_cap_emits_friendly_sse_error():
    app, _ = _build_app(daily_turn_cap=2, rate_limit_chat_per_10m=100)
    client = TestClient(app)
    sid = client.post("/api/session/start", json={"mode": "inbound"}).json()["session_id"]

    assert client.post("/api/chat", json={"session_id": sid, "text": "Hi"}).status_code == 200
    assert client.post("/api/chat", json={"session_id": sid, "text": "My sales dropped"}).status_code == 200

    r3 = client.post("/api/chat", json={"session_id": sid, "text": "Check my data"})
    assert r3.status_code == 200
    events = _parse_sse(r3.text)
    err = next((d for e, d in events if e == "error"), None)
    assert err is not None
    assert "daily capacity" in err["message"].lower()


def test_oversized_request_rejected_with_413_and_session_ttl_lru():
    app, _ = _build_app(max_request_bytes=1024)
    client = TestClient(app)
    big_text = "x" * 2048
    resp = client.post("/api/session/start", content=json.dumps({"mode": "inbound", "scenario_id": big_text}))
    assert resp.status_code == 413

    # Session TTL & LRU eviction
    store = SessionStore(ttl_seconds=60, max_sessions=10)
    ids = [store.create().session_id for _ in range(12)]
    assert len(store) == 10
    assert store.get(ids[0]) is None
    assert store.get(ids[-1]) is not None
    # Force expiry check via monotonic backdate
    latest = store.get(ids[-1])
    assert latest is not None
    latest.last_accessed = time.monotonic() - 120
    assert store.get(ids[-1]) is None

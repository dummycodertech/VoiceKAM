"""Tests for LLM failover, 429 cooldown, reasoning stripping, speakable normalization, and grounding."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from app.agent.orchestrator import AgentOrchestrator
from app.agent.session import Phase, SessionStore
from app.agent.speakable import (
    enforce_voice_length,
    find_ungrounded_numbers,
    to_speakable_text,
)
from app.agent.state_machine import tools_for_phase
from app.llm.base import (
    CanonicalMessage,
    LLMProvider,
    LLMStreamChunk,
    ProviderRateLimitOrTransientError,
)
from app.llm.openai_compat import ThinkTagFilter
from app.llm.router import LLMRouter


class Failing429Provider(LLMProvider):
    def __init__(self, provider_name: str = "groq", model_name: str = "openai/gpt-oss-20b") -> None:
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
        raise ProviderRateLimitOrTransientError(
            "429 Too Many Requests", status_code=429, retry_after_seconds=60.0, is_rate_limit=True
        )
        yield  # pragma: no cover


class SimpleOkProvider(LLMProvider):
    def __init__(self, provider_name: str = "gemini", model_name: str = "gemini-3.5-flash-lite", reply: str = "Got it, Rajesh ji.") -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.reply = reply
        self.seen_messages: list[list[CanonicalMessage]] = []

    def is_configured(self) -> bool:
        return True

    async def stream_turn(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float = 0.25,
        max_tokens: int = 220,
    ) -> AsyncIterator[LLMStreamChunk]:
        self.seen_messages.append(list(messages))
        yield LLMStreamChunk(type="token", text=self.reply, provider=self.provider_name, model=self.model_name)
        yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)


@pytest.mark.asyncio
async def test_429_triggers_failover_and_cooldown_skips_exhausted_provider():
    p1 = Failing429Provider()
    p2 = SimpleOkProvider()
    router = LLMRouter(providers=[p1, p2], cooldown_seconds=60)

    # Turn 1: p1 fails with 429 -> router fails over to p2 and puts p1 in cooldown
    events = []
    async for chunk, fo in router.stream_turn_with_failover([CanonicalMessage(role="user", content="Hi")], []):
        events.append((chunk, fo))
    assert p1.calls == 1
    assert any(c.text == "Got it, Rajesh ji." for c, _ in events)
    assert events[0][1][0]["from"] == "groq:openai/gpt-oss-20b"
    assert events[0][1][0]["to"] == "gemini:gemini-3.5-flash-lite"
    assert router.is_in_cooldown(p1.key)

    # Turn 2: p1 is still in cooldown so router skips p1 completely (calls stays 1!)
    async for _chunk, _fo in router.stream_turn_with_failover([CanonicalMessage(role="user", content="Next")], []):
        pass
    assert p1.calls == 1
    assert len(p2.seen_messages) == 2


@pytest.mark.asyncio
async def test_session_survives_mid_call_provider_switch():
    store = SessionStore()
    session = store.create(mode="inbound", language="en-IN")
    p1 = SimpleOkProvider("groq", "openai/gpt-oss-20b", "I understand, Rajesh ji. Did any top sellers go out of stock?")
    p2 = SimpleOkProvider("gemini", "gemini-3.5-flash-lite", "Got it. Did dispatch delays also happen?")
    router = LLMRouter(providers=[p1, p2])
    orch = AgentOrchestrator(router)

    async for _ in orch.run_turn(session, "My sales dropped significantly this month."):
        pass
    assert session.active_provider == "groq:openai/gpt-oss-20b"

    # Simulate p1 failing mid-call on turn 2 by swapping p1 for Failing429Provider
    router.providers = [Failing429Provider("groq", "openai/gpt-oss-20b"), p2]
    async for _ in orch.run_turn(session, "Yes, pressure cooker went out of stock."):
        pass

    assert session.active_provider == "gemini:gemini-3.5-flash-lite"
    assert len(session.failover_events) == 1
    # Verify p2 received canonical history from turn 1
    flattened = " ".join(m.content for m in p2.seen_messages[0])
    assert "pressure cooker" in flattened.lower()
    assert "sales dropped" in flattened.lower()


@pytest.mark.asyncio
async def test_diagnose_phase_pre_executes_tools_and_exposes_zero_tools_to_llm():
    assert tools_for_phase(Phase.DIAGNOSE) == []
    assert tools_for_phase(Phase.REVIEW_COMMITMENTS) == []
    assert len(tools_for_phase(Phase.COMMIT)) == 2
    assert len(tools_for_phase(Phase.SUMMARY)) == 2

    store = SessionStore()
    session = store.create(mode="proactive", language="en-IN")
    session.phase = Phase.CLARIFY
    session.clarify_questions_asked = 3  # Cap reached -> server must move to DIAGNOSE

    p = SimpleOkProvider("groq", "openai/gpt-oss-20b", "Your sales fell 32.5% to Rs 1,35,000, mostly from 18 out-of-stock products.")
    orch = AgentOrchestrator(LLMRouter(providers=[p]))
    sse_events = [ev async for ev in orch.run_turn(session, "Please check my data and tell me why.")]

    assert session.phase == Phase.DIAGNOSE
    tool_calls = [ev["data"]["name"] for ev in sse_events if ev["event"] == "tool_call"]
    assert set(tool_calls) == {
        "run_diagnosis",
        "get_out_of_stock_skus",
        "get_cancellation_breakdown",
        "get_pricing_gaps",
        "get_visibility_funnel",
    }
    # Only 1 LLM request needed in DIAGNOSE!
    lat = next(ev["data"] for ev in sse_events if ev["event"] == "latency")
    assert lat["llm_requests"] == 1


def test_think_tag_filter_strips_reasoning_blocks():
    f = ThinkTagFilter()
    chunks = ["<thi", "nk>internal math 1+1=2</th", "ink>Hello ", "Rajesh ji."]
    out = "".join(f.feed(c) for c in chunks) + f.flush()
    assert "internal math" not in out
    assert out.strip() == "Hello Rajesh ji."


def test_speakable_text_normalizes_rupees_percentages_and_abbreviations():
    raw = "**Sales** dropped 32.5% (Rs 2,00,000 -> ₹1,35,000) across 18 OOS SKUs and high SLA breaches."
    spoken_en = to_speakable_text(raw, "en-IN")
    assert "thirty-two point five percent" in spoken_en
    assert "two lakh rupees" in spoken_en
    assert "one lakh thirty-five thousand rupees" in spoken_en
    assert "out of stock" in spoken_en
    assert "products" in spoken_en
    assert "dispatch deadline" in spoken_en
    assert "**" not in spoken_en
    assert "₹" not in spoken_en

    spoken_hi = to_speakable_text("Revenue is ₹65,000 with 14% cancels.", "hinglish")
    assert "sixty-five thousand rupaye" in spoken_hi
    assert "fourteen percent" in spoken_hi


def test_canonical_to_gemini_adds_trigger_content_for_system_only_greetings():
    from app.llm.base import ToolCallRequest
    from app.llm.gemini_provider import _canonical_to_gemini

    sys_inst, contents = _canonical_to_gemini(
        [CanonicalMessage(role="system", content="You are Riya calling proactively.")]
    )
    assert sys_inst == "You are Riya calling proactively."
    assert len(contents) == 1
    assert contents[0].role == "user"

    # Cross-provider history with unsigned Groq tool_call folds to text (never missing thought_signature 400)
    _, cross_contents = _canonical_to_gemini(
        [
            CanonicalMessage(role="user", content="Okay I commit."),
            CanonicalMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCallRequest(id="g1", name="commit_action", arguments={"action_id": "ACT-1"})],
            ),
            CanonicalMessage(role="tool", content='{"recorded": true}', tool_call_id="g1", tool_name="commit_action"),
            CanonicalMessage(role="user", content="Send summary please."),
        ]
    )
    assert all(
        all(p.function_call is None for p in c.parts or [])
        for c in cross_contents
    )


def test_enforce_voice_length_and_number_grounding_checker():
    long_text = " ".join(["Word"] * 60) + "."
    trimmed = enforce_voice_length(long_text, phase="CLARIFY", max_words=45)
    assert len(trimmed.split()) <= 45

    grounded = {200000.0, 135000.0, 32.5, 500.0, 18.0}
    assert find_ungrounded_numbers("Sales fell 32.5% to Rs 1,35,000 across 18 products in 2 minutes.", grounded) == []
    bad = find_ungrounded_numbers("Sales fell 47.9% to Rs 99,000 across 25 products.", grounded)
    assert set(bad) == {"47.9", "99,000", "25"}


class ChunkedTokenProvider(LLMProvider):
    def __init__(self, chunks: list[str], provider_name: str = "groq", model_name: str = "openai/gpt-oss-20b") -> None:
        self.chunks = chunks
        self.provider_name = provider_name
        self.model_name = model_name

    def is_configured(self) -> bool:
        return True

    async def stream_turn(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float = 0.25,
        max_tokens: int = 220,
    ) -> AsyncIterator[LLMStreamChunk]:
        for c in self.chunks:
            yield LLMStreamChunk(type="token", text=c, provider=self.provider_name, model=self.model_name)
        yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)


@pytest.mark.asyncio
async def test_sentence_boundary_does_not_split_decimal_numbers():
    from app.agent.orchestrator import _SENTENCE_BOUNDARY_RE

    # Direct regex verification: decimal periods stay together while digit-ending sentences followed by space split
    assert _SENTENCE_BOUNDARY_RE.match("I see your sales fell 32.") is None
    assert _SENTENCE_BOUNDARY_RE.match("Sales fell 32.5% today.")[1] == "Sales fell 32.5% today."
    assert _SENTENCE_BOUNDARY_RE.match("Sales fell 32.5% to Rs 1,35,000.") is None
    assert _SENTENCE_BOUNDARY_RE.match("Sales fell 32.5% to Rs 1,35,000. Have...")[1] == "Sales fell 32.5% to Rs 1,35,000."
    assert _SENTENCE_BOUNDARY_RE.match("ASP stayed Rs 500. Let us fix it.")[1] == "ASP stayed Rs 500."

    # Streaming token boundary verification: split right around '.' in '32.5%'
    store = SessionStore()
    session = store.create(mode="inbound", language="en-IN")
    p = ChunkedTokenProvider(
        ["Sales dropped ", "32", ".", "5%", " to Rs 1,35,000. ", "ASP stayed Rs 500. ", "How should we fix it?"]
    )
    orch = AgentOrchestrator(LLMRouter(providers=[p]))
    events = [ev async for ev in orch.run_turn(session, "Why did my sales drop?")]
    sentences = [ev["data"]["text"] for ev in events if ev["event"] == "sentence"]
    assert sentences == [
        "Sales dropped 32.5% to Rs 1,35,000.",
        "ASP stayed Rs 500.",
        "How should we fix it?",
    ]



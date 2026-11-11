"""Phase 4 behavioral tests: word-boundary signals, negation, message slicing, hi-IN speakable text, localized fallbacks."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from app.agent import state_machine
from app.agent.orchestrator import AgentOrchestrator, slice_recent_messages
from app.agent.session import Phase, SessionStore
from app.agent.speakable import find_ungrounded_numbers, to_speakable_text
from app.data.context import SellerContext, SellerProfile, SkuRecord
from app.llm.base import CanonicalMessage, LLMProvider, LLMStreamChunk, ToolCallRequest
from app.llm.router import LLMRouter


def test_word_boundary_signals_and_negation_and_dead_commit_removed():
    # Dead _COMMIT_SIGNALS must stay removed
    assert not hasattr(state_machine, "_COMMIT_SIGNALS")

    # "look", "broken", "booking", "cookie" must NOT trigger the "ok" buy-in signal
    for tricky in ("Just look at my store", "Something is broken here", "Wait for the booking", "Cookie batch"):
        assert not state_machine.is_buy_in(tricky), f"{tricky!r} falsely matched buy-in"

    # Authentic English + Hindi/Hinglish buy-ins MUST trigger
    for positive in ("Yes let's do this", "haan", "theek hai kar sakte hain", "makes sense, what should I do?", "OK"):
        assert state_machine.is_buy_in(positive), f"{positive!r} failed to match buy-in"

    # Negation within the same utterance MUST cancel buy-in
    for negated in (
        "No, I cannot commit right now",
        "Not okay with that option",
        "I don't think yes is right here",
        "Nahi, theek nahi lag raha",
        "Na kar paunga",
    ):
        assert not state_machine.is_buy_in(negated), f"{negated!r} failed negation cancellation"


def test_state_machine_recommend_does_not_advance_on_look_or_negation():
    store = SessionStore()
    s = store.create(mode="proactive")
    s.phase = Phase.RECOMMEND
    # User says "look" -> word boundary prevents false match on "ok"
    state_machine.advance_phase_before_turn(s, "Look at the margins again please")
    assert s.phase == Phase.RECOMMEND

    # Negated approval stays in RECOMMEND
    state_machine.advance_phase_before_turn(s, "No, wait, not sure about this plan")
    assert s.phase == Phase.RECOMMEND

    # Clear Hindi/Hinglish agreement moves RECOMMEND -> COMMIT
    state_machine.advance_phase_before_turn(s, "Haan theek hai, let's lock this")
    assert s.phase == Phase.COMMIT


def test_message_history_slicing_never_starts_with_orphaned_tool_or_broken_pair():
    from app.llm.gemini_provider import _canonical_to_gemini

    # Build 13 messages where naive [-10:] lands right on index 3 (role="tool")!
    msgs = [
        CanonicalMessage(role="user", content="u1"),  # 0
        CanonicalMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="t1",
                    name="run_diagnosis",
                    arguments={},
                    extra_metadata={"gemini_thought_signature": "c2lnMQ=="},
                )
            ],
        ),  # 1
        CanonicalMessage(role="tool", content="{}", tool_call_id="t1", tool_name="run_diagnosis"),  # 2
        CanonicalMessage(role="tool", content="{}", tool_call_id="t2", tool_name="get_pricing_gaps"),  # 3 <-- naive [-10] start!
        CanonicalMessage(role="assistant", content="diagnosis summary"),  # 4
        CanonicalMessage(role="user", content="u2"),  # 5
        CanonicalMessage(role="assistant", content="recommend"),  # 6
        CanonicalMessage(role="user", content="u3"),  # 7
        CanonicalMessage(role="assistant", content="commit?"),  # 8
        CanonicalMessage(role="user", content="yes"),  # 9
        CanonicalMessage(role="assistant", content="ok"),  # 10
        CanonicalMessage(role="user", content="send"),  # 11
        CanonicalMessage(role="assistant", content="done"),  # 12
    ]
    sliced = slice_recent_messages(msgs, max_count=10)
    assert sliced[0].role == "user" and sliced[0].content == "u1"
    assert len(sliced) == 13

    # When max_count lands directly on assistant(tool_calls) at index 1, must also back up to u1 (index 0)
    sliced_on_fc = slice_recent_messages(msgs, max_count=12)
    assert sliced_on_fc[0].role == "user" and sliced_on_fc[0].content == "u1"

    # Verify Gemini contents ordering rule: every model(function_call) immediately follows user/function_response
    _, gem_contents = _canonical_to_gemini([CanonicalMessage(role="system", content="sys"), *sliced])
    assert gem_contents[0].role == "user"
    for idx, turn in enumerate(gem_contents):
        if any(p.function_call is not None for p in (turn.parts or [])):
            assert idx > 0 and gem_contents[idx - 1].role == "user"

    # Corrupted leading orphans without prior user turn should be skipped cleanly when later user turn exists
    corrupt = [CanonicalMessage(role="tool", content="{}", tool_call_id="orphan", tool_name="x")] + msgs[4:]
    sliced_c = slice_recent_messages(corrupt, max_count=len(corrupt))
    assert sliced_c[0].role != "tool"


def test_gemini_canonical_guards_headless_function_calls_and_orphan_tools():
    from app.llm.gemini_provider import _canonical_to_gemini

    sig = {"gemini_thought_signature": "c2ln"}
    # Even if a caller directly passes [system, assistant(tool_calls), tool, user] without slice_recent_messages
    _, contents_headless = _canonical_to_gemini(
        [
            CanonicalMessage(role="system", content="sys"),
            CanonicalMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCallRequest(id="c1", name="commit_action", arguments={"action_id": "ACT-1"}, extra_metadata=sig)],
            ),
            CanonicalMessage(role="tool", content='{"recorded": true}', tool_call_id="c1", tool_name="commit_action"),
            CanonicalMessage(role="user", content="Send summary"),
        ]
    )
    assert contents_headless[0].role == "user"
    for idx, turn in enumerate(contents_headless):
        if any(p.function_call is not None for p in (turn.parts or [])):
            assert idx > 0 and contents_headless[idx - 1].role == "user"

    # Orphan tool response without open function_call folds to text safely
    _, contents_orphan = _canonical_to_gemini(
        [
            CanonicalMessage(role="user", content="hello"),
            CanonicalMessage(role="tool", content='{"x": 1}', tool_call_id="missing", tool_name="foo"),
        ]
    )
    assert all(all(p.function_response is None for p in (c.parts or [])) for c in contents_orphan)


@pytest.mark.asyncio
async def test_multiturn_gemini_orchestrator_never_violates_function_call_ordering():
    from app.llm.gemini_provider import _canonical_to_gemini

    sig = {"gemini_thought_signature": "Z2VtX3NpZw=="}

    class _StrictGeminiSimProvider(LLMProvider):
        provider_name = "gemini"
        model_name = "gemini-3.5-flash-lite"

        def is_configured(self) -> bool:
            return True

        async def stream_turn(
            self,
            messages: list[CanonicalMessage],
            tools: list[dict[str, Any]],
            temperature: float = 0.25,
            max_tokens: int = 220,
        ) -> AsyncIterator[LLMStreamChunk]:
            # Validate Gemini strict ordering rule on EVERY turn (including after len(messages) > 10)
            _, contents = _canonical_to_gemini(messages)
            for idx, turn in enumerate(contents):
                if any(p.function_call is not None for p in (turn.parts or [])):
                    assert idx > 0 and contents[idx - 1].role == "user", (
                        f"Gemini function_call ordering violated at idx={idx}: {contents}"
                    )

            names = {t["function"]["name"] for t in tools}
            has_recent_tool = any(m.role == "tool" for m in reversed(messages[1:]))
            if "get_seller_overview" in names and not any(
                m.role == "tool" and m.tool_name == "get_seller_overview" for m in messages[-3:]
            ):
                yield LLMStreamChunk(
                    type="tool_calls",
                    tool_calls=[ToolCallRequest(id="g_ov", name="get_seller_overview", arguments={}, extra_metadata=sig)],
                    provider=self.provider_name,
                    model=self.model_name,
                )
            elif "commit_action" in names and not any(
                m.role == "tool" and m.tool_name == "commit_action" for m in messages[-3:]
            ):
                yield LLMStreamChunk(
                    type="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="g_cm",
                            name="commit_action",
                            arguments={"action_id": "ACT-1", "action_summary": "Restock", "owner": "Vikram", "due_date": "48h"},
                            extra_metadata=sig,
                        )
                    ],
                    provider=self.provider_name,
                    model=self.model_name,
                )
            elif "send_call_summary" in names and not any(
                m.role == "tool" and m.tool_name == "send_call_summary" for m in messages[-3:]
            ):
                yield LLMStreamChunk(
                    type="tool_calls",
                    tool_calls=[ToolCallRequest(id="g_sm", name="send_call_summary", arguments={}, extra_metadata=sig)],
                    provider=self.provider_name,
                    model=self.model_name,
                )
            else:
                _ = has_recent_tool
                yield LLMStreamChunk(type="token", text="Okay, noted.", provider=self.provider_name, model=self.model_name)
            yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)

    store = SessionStore()
    s = store.create(mode="inbound", language="en-IN")
    orch = AgentOrchestrator(LLMRouter(providers=[_StrictGeminiSimProvider()]))
    async for _ in orch.start_call(s):
        pass
    for utt in [
        "Hi, sales dropped.",
        "Yes stockouts happened.",
        "Any price issue too?",
        "Check my data and tell me why.",
        "Got it, what should I do?",
        "Yes I commit to ACT-1 within 48 hours.",
        "Please send the summary now.",
        "Thanks, goodbye!",
    ]:
        async for _ in orch.run_turn(s, utt):
            pass
    assert len(s.messages) > 10
    assert s.summary_card is not None


def test_hi_in_speakable_rupee_and_percent_and_custom_sku_grounding():
    custom_ctx = SellerContext(
        profile=SellerProfile(shop_name="Test Shop"),
        skus=[SkuRecord(sku_id="ZAP-9021", name="Widget", price=500.0, orders_prev=20, orders_curr=10)],
    )
    spoken_hi = to_speakable_text("बिक्री 32.5% गिरकर ₹1,75,500 हुई, ZAP-9021 चेक करें।", language="hi-IN", seller_context=custom_ctx)
    assert "रुपये" in spoken_hi
    assert "लाख" in spoken_hi and "हज़ार" in spoken_hi
    assert "प्रतिशत" in spoken_hi
    assert "पचहत्तर" in spoken_hi
    assert "9021" in spoken_hi

    # Custom SKU ID ZAP-9021 must NOT trigger ungrounded number warnings while true fake numbers DO
    assert find_ungrounded_numbers("Check ZAP-9021 which lost 10 orders.", {10.0, 500.0}, seller_context=custom_ctx) == []
    assert find_ungrounded_numbers("Check ZAP-9021 which lost 9999 fake orders.", {10.0, 500.0}, seller_context=custom_ctx) == ["9999"]


class _EmptyProvider(LLMProvider):
    provider_name = "empty"
    model_name = "empty-test"

    def is_configured(self) -> bool:
        return True

    async def stream_turn(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float = 0.25,
        max_tokens: int = 220,
    ) -> AsyncIterator[LLMStreamChunk]:
        yield LLMStreamChunk(type="token", text="   ", provider=self.provider_name, model=self.model_name)
        yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)


@pytest.mark.asyncio
@pytest.mark.parametrize("lang,expected_substr", [("hi-IN", "समय"), ("hinglish", "timeline"), ("en-IN", "timeline")])
async def test_localized_empty_reply_fallbacks(lang: str, expected_substr: str):
    store = SessionStore()
    s = store.create(mode="inbound", language=lang)
    orch = AgentOrchestrator(LLMRouter(providers=[_EmptyProvider()]))
    evs = [ev async for ev in orch.run_turn(s, "Hello")]
    done = next(e["data"] for e in evs if e["event"] == "done")
    assert expected_substr in done["text"].lower() or expected_substr in done["text"]
    assert s.ungrounded_warnings == []


def test_spoken_agent_name_config_per_language_without_changing_ui_text(monkeypatch: pytest.MonkeyPatch):
    from app.config import AGENT_NAME, Settings, get_settings

    monkeypatch.setenv("AGENT_NAME_SPOKEN_EN", "meh-tiss")
    monkeypatch.setenv("AGENT_NAME_SPOKEN_HI", "मेटिस")
    get_settings.cache_clear()
    try:
        cfg = get_settings()
        assert cfg.agent_name == AGENT_NAME == "Metis"
        assert cfg.spoken_agent_name("en-IN") == "meh-tiss"
        assert cfg.spoken_agent_name("hinglish") == "meh-tiss"
        assert cfg.spoken_agent_name("hi-IN") == "मेटिस"

        spoken_en = to_speakable_text("Namaste, I am Metis, checking symmetrical SKUs.", language="en-IN", settings=cfg)
        assert "meh-tiss" in spoken_en
        assert "metis" not in spoken_en.lower()
        assert "symmetrical" in spoken_en

        spoken_hinglish = to_speakable_text("Ji, main Metis bol rahi hoon.", language="hinglish", settings=cfg)
        assert "meh-tiss" in spoken_hinglish
        assert "metis" not in spoken_hinglish.lower()

        spoken_hi = to_speakable_text("नमस्ते, मैं Metis हूँ।", language="hi-IN", settings=cfg)
        assert "मेटिस" in spoken_hi
        assert "metis" not in spoken_hi.lower()

        # When AGENT_NAME is overridden without spoken env overrides, spoken_agent_name tracks it cleanly
        monkeypatch.delenv("AGENT_NAME_SPOKEN_EN", raising=False)
        monkeypatch.delenv("AGENT_NAME_SPOKEN_HI", raising=False)
        custom_brand = Settings(agent_name="meera", agent_name_devanagari="मीरा")
        assert custom_brand.spoken_agent_name("en-IN") == "meera"
        assert custom_brand.spoken_agent_name("hi-IN") == "मीरा"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_orchestrator_greeting_preserves_ui_name_and_uses_spoken_tts_name():
    from app.config import Settings

    custom_settings = Settings(
        agent_name_spoken_en="meh-tiss",
        agent_name_spoken_hi="मेटिस",
    )
    orch = AgentOrchestrator(router=LLMRouter(providers=[_EmptyProvider()]), settings=custom_settings)
    store = SessionStore()

    s_en = store.create(mode="inbound", language="en-IN")
    evs_en = [ev async for ev in orch.start_call(s_en)]
    sent_en = next(e["data"] for e in evs_en if e["event"] == "sentence")
    done_en = next(e["data"] for e in evs_en if e["event"] == "done")
    assert "Metis" in sent_en["text"] and "Metis" in done_en["text"]
    assert "meh-tiss" not in sent_en["text"]
    assert "meh-tiss" in sent_en["speakable"] and "meh-tiss" in done_en["speakable"]

    s_hi = store.create(mode="inbound", language="hi-IN")
    evs_hi = [ev async for ev in orch.start_call(s_hi)]
    sent_hi = next(e["data"] for e in evs_hi if e["event"] == "sentence")
    done_hi = next(e["data"] for e in evs_hi if e["event"] == "done")
    assert "Metis" in sent_hi["text"] and "Metis" in done_hi["text"]
    assert "मेटिस" in sent_hi["speakable"] and "मेटिस" in done_hi["speakable"]

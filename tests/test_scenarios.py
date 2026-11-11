"""M8 Scenario & Latency Verification Tests across 4 Seller Personas, Proactive, Follow-Up, and Live LLM."""

from __future__ import annotations

import pytest

from app.agent.orchestrator import AgentOrchestrator
from app.agent.session import Phase, SessionStore
from app.config import get_settings
from app.llm.mock_provider import MockLLMProvider
from app.llm.router import LLMRouter


def _collect_turn_stats(events: list[dict]) -> dict:
    done = next(e["data"] for e in events if e["event"] == "done")
    latency = next(e["data"] for e in events if e["event"] == "latency")
    sentences = [e["data"] for e in events if e["event"] == "sentence"]
    return {
        "done": done,
        "latency": latency,
        "sentences": sentences,
    }


async def _drive_conversation(
    mode: str,
    language: str,
    turns: list[str],
    use_real_router: bool = False,
):
    store = SessionStore()
    session = store.create(mode=mode, language=language)
    if use_real_router:
        settings = get_settings()
        router = LLMRouter.from_settings(settings)
        await router.initialize_and_smoke_test(settings)
    else:
        router = LLMRouter(providers=[MockLLMProvider()])
    orch = AgentOrchestrator(router=router)

    seen_phases: list[str] = []
    start_events = [ev async for ev in orch.start_call(session)]
    for ev in start_events:
        if ev["event"] == "phase":
            seen_phases.append(ev["data"]["phase"])

    turn_outputs = []
    for utt in turns:
        events = [ev async for ev in orch.run_turn(session, utt)]
        for ev in events:
            if ev["event"] == "phase":
                seen_phases.append(ev["data"]["phase"])
        stats = _collect_turn_stats(events)
        turn_outputs.append(stats)

        reply_words = len(stats["done"]["text"].split())
        cap = 75 if stats["done"]["phase"] == "SUMMARY" else 45
        assert reply_words <= cap, f"Voice cap exceeded ({reply_words}>{cap}): {stats['done']['text']}"
        assert stats["latency"]["llm_first_token_ms"] >= 0
        assert stats["latency"]["total_turn_ms"] >= 0

    return session, seen_phases, turn_outputs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "persona,utterances",
    [
        (
            "cooperative_detailed",
            [
                "My sales dropped significantly this month from 476 orders to 354. Can you help me?",
                "Yes, our Trail Runner Shoes and Canvas Sneakers ran out of stock last week.",
                "We also struggled to update stock counts before the afternoon pickup.",
                "Yes, please check my store data and tell me all the reasons.",
                "Makes sense, what are the top recommended actions?",
                "Sounds practical, let's agree on those steps.",
                "Yes, I commit to restocking top 5 SKUs within 48 hours and syncing stock daily starting today.",
                "Please send me the call summary and schedule the follow-up.",
            ],
        ),
        (
            "vague_low_detail",
            [
                "Hi.",
                "Sales are just bad this month, I don't know why.",
                "No idea really.",
                "Maybe everything is down.",
                "You tell me what is wrong in my data.",
                "Okay, what should I do next?",
                "Alright, seems reasonable.",
                "Yes okay, I agree to restock within 48 hours.",
                "Send the summary please.",
            ],
        ),
        (
            "frustrated_angry",
            [
                "I am fed up! The marketplace is ruining my sales and platform charges feel unfair.",
                "Stop giving excuses, orders dropped way too much and I'm losing money!",
                "Fine, check my data yourself and show me concrete numbers!",
                "Okay at least those numbers make sense. Give me a practical plan.",
                "Fine, I want to fix this.",
                "Yes I agree, I'll restock within 48 hours and handle dispatch by 2 PM tomorrow.",
                "Send the summary on WhatsApp.",
            ],
        ),
        (
            "off_topic_interrupting",
            [
                "Wait before we talk, how do I change my GST bank branch and Delhi traffic is awful today!",
                "Hold on, let me finish, my brother manages packaging when I travel to Jaipur.",
                "Also check if courier boy can arrive earlier.",
                "Anyway my sales fell a lot, check my store data and tell me why.",
                "Okay got it, give me the 3 actions.",
                "Sure, makes sense, let's proceed.",
                "Yes I agree to start today and restock top 5 items within 48 hours.",
                "Okay send the final summary.",
            ],
        ),
    ],
)
async def test_four_seller_personas_end_to_end(persona: str, utterances: list[str]):
    session, seen_phases, outputs = await _drive_conversation("inbound", "en-IN", utterances)

    # Zero hallucinated / ungrounded numbers
    assert session.ungrounded_warnings == [], f"{persona} ungrounded warnings: {session.ungrounded_warnings}"

    # Must respect clarify question cap (<= 3) and reach DIAGNOSE -> RECOMMEND -> COMMIT -> SUMMARY
    assert session.clarify_questions_asked <= 3, f"{persona} asked {session.clarify_questions_asked} clarify Qs"
    assert "DIAGNOSE" in seen_phases, f"{persona} missed DIAGNOSE: {seen_phases}"
    assert "RECOMMEND" in seen_phases, f"{persona} missed RECOMMEND: {seen_phases}"
    assert "COMMIT" in seen_phases, f"{persona} missed COMMIT: {seen_phases}"
    assert "SUMMARY" in seen_phases, f"{persona} missed SUMMARY: {seen_phases}"

    # Tools executed properly including action plan & commitment tracking
    tool_names = [t["name"] for t in session.tool_history]
    assert "run_diagnosis" in tool_names
    assert "build_action_plan" in tool_names
    assert session.commitments, f"{persona} failed to record seller commitment"
    assert session.summary_card is not None, f"{persona} missing summary_card"
    assert "whatsapp_text" in session.summary_card

    # Pre-executed phases must require <= 1 LLM request
    for st in outputs:
        if st["done"]["phase"] in ("DIAGNOSE", "RECOMMEND"):
            assert st["latency"]["llm_requests"] == 1


@pytest.mark.asyncio
async def test_proactive_and_followup_languages_and_carryover():
    # Hinglish proactive call
    s_pro, phases_pro, _ = await _drive_conversation(
        "proactive",
        "hinglish",
        [
            "Haan Metis, batao kya hua mere store mein?",
            "Achha, ab mujhe kya karna chahiye?",
            "Theek hai, let's lock dates.",
            "Haan, I commit to restocking within 48 hours starting tomorrow.",
            "Summary bhej do please.",
        ],
    )
    assert "DIAGNOSE" in phases_pro
    assert "SUMMARY" in phases_pro
    assert s_pro.ungrounded_warnings == []
    assert s_pro.summary_card is not None

    # 3-Day Follow-up session linked to prior session commitments
    store = SessionStore()
    prev = store.create(mode="inbound", language="en-IN")
    prev.commitments.append(
        {
            "action_id": "ACT-1",
            "action_summary": "Restock top 5 OOS SKUs",
            "owner": "Vikram Kulkarni",
            "due_date": "Within 48 hours",
            "target_metric": "OOS <= 8",
            "status": "committed",
        }
    )
    fu = store.create(mode="followup", language="hi-IN", prior_session_id=prev.session_id)
    assert fu.snapshot == "followup"
    assert len(fu.prior_commitments) == 1
    assert fu.prior_commitments[0]["status"] == "partially_done"

    orch = AgentOrchestrator(LLMRouter(providers=[MockLLMProvider()]))
    start_evs = [ev async for ev in orch.start_call(fu)]
    assert any(ev["event"] == "done" for ev in start_evs)
    turn1 = [ev async for ev in orch.run_turn(fu, "Haan Metis, progress batao.")]
    assert fu.phase == Phase.REVIEW_COMMITMENTS
    assert any(ev["event"] == "tool_result" and ev["data"]["name"] == "run_diagnosis" for ev in turn1)
    assert fu.ungrounded_warnings == []


@pytest.mark.asyncio
async def test_live_llm_cooperative_scenario_grounding_and_latency():
    settings = get_settings()
    if not (settings.groq_api_key.strip() or settings.gemini_api_key.strip()):
        pytest.skip("No live GROQ_API_KEY or GEMINI_API_KEY configured in environment")

    session, seen_phases, outputs = await _drive_conversation(
        mode="proactive",
        language="en-IN",
        turns=[
            "Wait, why did my sales drop? Check my data and explain.",
            "Understood. What are your top prioritised recommendations?",
            "Sounds good, I agree to fix this.",
            "Yes, I commit to restocking top 5 SKUs within 48 hours and syncing stock daily starting today. Schedule the follow-up in 3 days.",
            "Please send the final call summary.",
        ],
        use_real_router=True,
    )

    assert "DIAGNOSE" in seen_phases
    assert "RECOMMEND" in seen_phases
    assert "COMMIT" in seen_phases
    assert "SUMMARY" in seen_phases
    assert session.summary_card is not None
    assert session.ungrounded_warnings == [], f"Real LLM invented ungrounded numbers: {session.ungrounded_warnings}"
    for st in outputs:
        assert st["latency"]["total_turn_ms"] < 15000


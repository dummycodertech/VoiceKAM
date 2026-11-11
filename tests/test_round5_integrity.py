"""Round 5 integrity unit and end-to-end conversation regression tests."""

from __future__ import annotations

from pathlib import Path

from app.agent.orchestrator import AgentOrchestrator
from app.agent.session import SessionStore
from app.data.upload import parse_uploaded_csv
from app.engine.tools import execute_tool
from app.llm.mock_provider import MockLLMProvider
from app.llm.router import LLMRouter
from app.utils.formatting import format_indian_number, format_inr, normalize_indian_grouping

ROOT = Path(__file__).resolve().parent.parent


def _load_brief_context():
    skus = (ROOT / "brief_scenario_skus.csv").read_bytes()
    cancels = (ROOT / "brief_scenario_cancellations.csv").read_bytes()
    return parse_uploaded_csv(
        skus,
        cancellations_csv_bytes=cancels,
        seller_meta={
            "shop_name": "HomeCraft Essentials",
            "seller_name": "Ananya",
        },
    )


async def _collect_turn(orchestrator: AgentOrchestrator, session, user_text: str | None) -> dict:
    events = []
    if user_text is None:
        async for ev in orchestrator.start_call(session):
            events.append(ev)
    else:
        async for ev in orchestrator.run_turn(session, user_text):
            events.append(ev)
    done = next(e["data"] for e in events if e["event"] == "done")
    return {"events": events, "done": done}


def test_indian_digit_grouping_formatter():
    assert format_indian_number(500) == "500"
    assert format_indian_number(28500) == "28,500"
    assert format_indian_number(135000) == "1,35,000"
    assert format_indian_number(200000) == "2,00,000"
    assert format_indian_number(1000000) == "10,00,000"
    assert format_indian_number(12345678) == "1,23,45,678"
    assert format_inr(135000) == "Rs 1,35,000"
    assert format_inr(-65000) == "-Rs 65,000"

    assert (
        normalize_indian_grouping("Sales fell from Rs 200,000 to Rs 135,000 and Rs 1,000,000.")
        == "Sales fell from Rs 2,00,000 to Rs 1,35,000 and Rs 10,00,000."
    )
    assert normalize_indian_grouping("Drop was Rs135000") == "Drop was Rs1,35,000"


async def test_indian_digit_grouping_in_turns_summary_and_whatsapp():
    ctx = _load_brief_context()
    store = SessionStore()
    session = store.create(mode="proactive", language="en-IN", seller_context=ctx)
    router = LLMRouter([MockLLMProvider()])
    orch = AgentOrchestrator(router)

    turn0 = await _collect_turn(orch, session, None)
    assert "1,35,000" in turn0["done"]["text"]
    assert "135,000" not in turn0["done"]["text"]

    # Send call summary tool directly and verify written card + WhatsApp text
    res = execute_tool("send_call_summary", {}, session=session)
    card = res["summary_card"]
    combined = " ".join(card["key_issues"]) + "\n" + card["whatsapp_text"]
    assert "1,35,000" in combined
    assert "2,00,000" in combined
    assert "135,000" not in combined
    assert "200,000" not in combined

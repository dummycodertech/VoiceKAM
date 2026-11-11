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


def test_dynamic_followup_targets_and_tool_projections():
    from app.data.scenarios import build_scenario
    from app.engine.diagnosis import build_action_plan, compute_followup_targets, get_out_of_stock_skus

    # Brief dataset: 18 OOS (5 chronic + 13 newly OOS), top 5 restocked -> projected 13 OOS
    brief_ctx = _load_brief_context()
    brief_targets = compute_followup_targets(brief_ctx, "current")
    assert brief_targets["out_of_stock_skus_max"] == 13
    # Baseline cancel ~7.4%, current ~12.3% -> 7.4 + 0.3*(12.3-7.4) = ~8.9%
    assert brief_targets["cancellation_rate_pct_max"] != 10.0

    oos = get_out_of_stock_skus(brief_ctx, limit=5, snapshot="current")
    assert oos["total_oos_count"] == 18
    assert oos["planned_restock_count"] == 5
    assert oos["projected_oos_after_restock"] == 13

    plan = build_action_plan(brief_ctx, snapshot="current", anchor_dt="2026-11-11T10:00:00+05:30")
    assert plan["projections"]["projected_oos_after_restock"] == 13
    assert plan["actions"][0]["due_date_ist"] == "Friday 13 Nov"

    # Distinct datasets yield distinct targets (never hardcoded 8 / 10.0 across scenarios)
    sc_targets = {sid: compute_followup_targets(build_scenario(sid), "current") for sid in ("A", "B", "C", "D")}
    assert sc_targets["B"]["out_of_stock_skus_max"] != sc_targets["A"]["out_of_stock_skus_max"]
    assert sc_targets["C"]["cancellation_rate_pct_max"] != sc_targets["A"]["cancellation_rate_pct_max"]


def test_ist_dates_and_date_audit():
    from app.utils.dates import audit_dates, compute_due_date_ist, compute_followup_date_ist

    anchor = "2026-11-11T10:30:00+05:30"  # Wednesday 11 Nov 2026
    assert compute_due_date_ist(2, anchor=anchor)["due_date_ist"] == "Friday 13 Nov"
    assert compute_followup_date_ist(3, anchor=anchor) == "Saturday 14 Nov at 11:00 AM IST"

    # Wrong weekday ("Monday 13 Nov" when 13 Nov 2026 is Friday) must be flagged
    warns = audit_dates("Please restock by Monday 13 Nov.", anchor=anchor)
    assert any(w["type"] == "weekday_mismatch" and "Friday" in w["message"] for w in warns)

    # Matching weekday has zero warnings
    assert audit_dates("Please restock by Friday 13 Nov.", anchor=anchor) == []

    # Spoken vs written mismatch must be flagged
    fake_card = {
        "commitments": [{"due_date": "Friday 13 Nov"}],
        "followup": {"followup_date": "Saturday 14 Nov at 11:00 AM IST"},
    }
    mismatch = audit_dates("Your due date is Sunday 15 Nov.", anchor=anchor, summary_card=fake_card)
    assert any(w["type"] == "spoken_written_date_mismatch" for w in mismatch)


async def test_projection_claim_audit_and_no_causal_claim_before_diagnose():
    from app.agent.session import Phase
    from app.agent.speakable import audit_projection_claims
    from app.agent.state_machine import build_system_prompt
    from app.llm.base import CanonicalMessage, LLMProvider, LLMStreamChunk

    class _CausalAndFalseClaimProvider(LLMProvider):
        provider_name = "test"
        model_name = "test-model"

        def is_configured(self) -> bool:
            return True

        async def stream_turn(self, messages, tools, temperature=0.25, max_tokens=220):
            sys_txt = messages[0].content
            if "CURRENT PHASE: UNDERSTAND" in sys_txt:
                yield LLMStreamChunk(
                    type="token",
                    text="Your drop is mainly due to out of stock items.",
                    provider=self.provider_name,
                    model=self.model_name,
                )
            else:
                yield LLMStreamChunk(
                    type="token",
                    text="Restocking top 5 items will bring out-of-stock down to 8.",
                    provider=self.provider_name,
                    model=self.model_name,
                )
            yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)

    # False projection unit check directly against brief dataset (18 - 5 = 13, NOT 8)
    proj_warns = audit_projection_claims(
        "Restocking these 5 items will bring out-of-stock down to 8.",
        {"projected_oos_after_restock": 13},
    )
    assert len(proj_warns) == 1
    assert proj_warns[0]["claimed"] == 8 and proj_warns[0]["projected"] == 13

    ctx = _load_brief_context()
    store = SessionStore()
    session = store.create(mode="inbound", language="en-IN", seller_context=ctx)
    # System prompt before DIAGNOSE must NOT leak drivers_summary
    pre_prompt = build_system_prompt(session)
    assert '"drivers_summary": []' in pre_prompt

    orch = AgentOrchestrator(LLMRouter([_CausalAndFalseClaimProvider()]))
    # Turn in UNDERSTAND: premature causal claim ("mainly due to") must be flagged & stripped
    t1 = await _collect_turn(orch, session, "My sales fell a lot.")
    assert len(session.causal_warnings) == 1
    assert "mainly due to" not in t1["done"]["text"].lower()

    # Transitioning DIAGNOSE -> RECOMMEND pre-executes build_action_plan (projected_oos_after_restock=13);
    # claiming "down to 8" must be flagged in projection_warnings & ungrounded_warnings.
    session.phase = Phase.DIAGNOSE
    await _collect_turn(orch, session, "Show me the options please.")
    assert len(session.projection_warnings) >= 1
    assert any(w["claimed"] == 8 and w["projected"] == 13 for w in session.projection_warnings)

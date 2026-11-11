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


async def test_group3_consent_gate_one_action_per_turn_and_summary_sections():
    ctx = _load_brief_context()
    store = SessionStore()
    session = store.create(mode="proactive", language="en-IN", seller_context=ctx)
    router = LLMRouter([MockLLMProvider()])
    orch = AgentOrchestrator(router)

    await _collect_turn(orch, session, None)  # GREETING
    await _collect_turn(orch, session, "Tell me why sales dropped.")  # DIAGNOSE
    rec = await _collect_turn(orch, session, "Okay what actions do you recommend?")  # RECOMMEND
    rec_text = rec["done"]["text"]

    # Item 2: plain prose (no bullets/numbering), single pending action with yes/no + real due date, 2-3 actions in plan
    assert "1." not in rec_text and "Priority 1" not in rec_text and "- " not in rec_text
    assert "?" in rec_text
    assert len(session.action_plan_items) >= 2
    assert session.pending_confirmation is not None

    # Direct commit_action call with ambiguous last reply MUST be rejected (Condition b)
    await _collect_turn(orch, session, "maybe i will try")
    rejected = execute_tool(
        "commit_action",
        {"action_id": "ACT-1", "action_summary": "Restock", "owner": "Ananya", "due_date": "Friday 13 Nov"},
        session=session,
    )
    assert rejected["recorded"] is False
    assert session.commitments == []

    # Second ambiguous reply ("I'll try") marks ACT-1 as tentative (never agreed) and moves to next pending action
    await _collect_turn(orch, session, "I'll try")
    assert session.commitments == []
    assert session.action_statuses["ACT-1"]["status"] == "tentative"

    # Explicit decline on next pending action ("No, not that") marks it declined rather than committed
    next_id = session.pending_confirmation["action_id"]
    await _collect_turn(orch, session, "No, not the price change")
    assert session.action_statuses[next_id]["status"] == "declined" or any(
        st["status"] == "declined" for st in session.action_statuses.values()
    )

    # Clear yes commits remaining action and finishes to SUMMARY
    if session.phase.value != "SUMMARY":
        last_ev = await _collect_turn(orch, session, "Yes, I agree")
    else:
        last_ev = None
    if session.phase.value != "SUMMARY":
        last_ev = await _collect_turn(orch, session, "Please send the call summary.")

    assert last_ev is not None
    spoken_recap = last_ev["done"]["text"]
    assert 10 <= len(spoken_recap.split()) <= 80
    assert "1,35,000" in spoken_recap and "follow-up" in spoken_recap.lower()
    assert session.commitments[0]["due_date"] in spoken_recap
    assert session.ungrounded_warnings == []
    assert session.date_warnings == []

    assert session.summary_card is not None
    card = session.summary_card
    wa = card["whatsapp_text"]
    assert "*Agreed actions:*" in wa
    assert "*Tentative:*" in wa
    assert "*Declined or not agreed:*" in wa
    assert "*Not discussed:*" in wa
    # Only explicitly consented actions under Agreed actions
    assert [a["action_id"] for a in card["agreed_actions"]] == [c["action_id"] for c in session.commitments]
    assert card["commitments"] == card["agreed_actions"]
    assert len(card["tentative_actions"]) >= 1
    assert len(card["declined_actions"]) >= 1
    # Flat average price + multiple causes present in key_issues
    joined_issues = " ".join(card["key_issues"]).lower()
    assert "flat" in joined_issues or "average selling price" in joined_issues
    assert "stock" in joined_issues and ("pric" in joined_issues or "visibility" in joined_issues or "cancel" in joined_issues)


def test_group4_spoken_register_ranges_codes_eligibility_and_cadence():
    from app.agent.session import SessionStore
    from app.agent.speakable import enforce_spoken_cadence_and_register, to_speakable_text

    ctx = _load_brief_context()

    # 1. Ranges use "to" in en-IN, hinglish, and hi-IN; .0% omitted; no ASP/SKU/OOS/funnel/codes
    rng_text = "Rs 22,200 - Rs 28,500/month recovery, 50.0% drop across HK-008 OOS SKU funnel ASP."
    out_en = to_speakable_text(rng_text, "en-IN", seller_context=ctx)
    assert "twenty-two thousand two hundred to twenty-eight thousand five hundred rupees a month" in out_en
    assert "point zero" not in out_en and "fifty percent" in out_en
    for forbidden in ("asp", "sku", "oos", "funnel", "hk-008"):
        assert forbidden not in out_en.lower()
    assert "Insulated Steel Casserole Set" in out_en

    out_hg = to_speakable_text(rng_text, "hinglish", seller_context=ctx)
    assert "twenty-two thousand two hundred to twenty-eight thousand five hundred rupaye a month" in out_hg
    assert "fifty percent" in out_hg

    out_hi = to_speakable_text(rng_text, "hi-IN", seller_context=ctx)
    assert "-" not in out_hi
    assert "रुपये" in out_hi and (" टू " in out_hi or " to " in out_hi)
    assert "दशमलव शून्य" not in out_hi and "पचास प्रतिशत" in out_hi

    # 2. Never say "Seller Partner" or "Custom Uploaded Store" in speech; cap product names at 2
    default_ctx = parse_uploaded_csv(
        (ROOT / "brief_scenario_skus.csv").read_bytes(),
        cancellations_csv_bytes=(ROOT / "brief_scenario_cancellations.csv").read_bytes(),
    )
    store = SessionStore()
    s = store.create(mode="inbound", language="en-IN", seller_context=default_ctx)
    bad = (
        "Got it, Seller Partner at Custom Uploaded Store! "
        "HK-008, HK-007, and HK-009 qualify subject to eligibility and also subject to eligibility."
    )
    cleaned = enforce_spoken_cadence_and_register(bad, session=s, turn_count=0)
    assert "seller partner" not in cleaned.lower()
    assert "custom uploaded store" not in cleaned.lower()
    assert cleaned.lower().count("subject to eligibility") == 1
    assert "Insulated Steel Casserole Set" in cleaned
    assert "Airtight Kitchen Storage Jars" not in cleaned  # 3rd product capped cleanly

    # Second turn in same call: "subject to eligibility" stripped; repeated "Got it" & salutation throttled
    s2 = store.create(mode="inbound", language="en-IN", seller_context=ctx)
    t0 = enforce_spoken_cadence_and_register("Got it, Ananya ji! Promos are subject to eligibility.", session=s2, turn_count=0)
    t1 = enforce_spoken_cadence_and_register("Got it, Ananya ji! Promos are subject to eligibility.", session=s2, turn_count=1)
    assert "got it" in t0.lower() and "ananya ji" in t0.lower()
    assert "got it" not in t1.lower() and "ananya" not in t1.lower()
    assert "subject to eligibility" not in t1.lower()


async def test_group4_clarify_topics_non_repeating_non_redundant_and_max_3():
    ctx = _load_brief_context()
    store = SessionStore()
    session = store.create(mode="proactive", language="en-IN", seller_context=ctx)
    router = LLMRouter([MockLLMProvider()])
    orch = AgentOrchestrator(router)

    await _collect_turn(orch, session, None)  # GREETING (notes 18 products OOS)
    q1 = await _collect_turn(orch, session, "Yes, I have 2 minutes, please go ahead.")  # GREETING -> CLARIFY (Q1)
    q2 = await _collect_turn(orch, session, "Our supplier shipments slipped by a week.")  # CLARIFY (Q2)
    q3 = await _collect_turn(orch, session, "Yes pickup cutoffs were missed too.")  # CLARIFY (Q3)
    d4 = await _collect_turn(orch, session, "Yes competitors cut prices slightly.")  # -> DIAGNOSE after 3 Qs

    assert session.clarify_questions_asked == 3
    assert len(session.clarify_topics_asked) == len(set(session.clarify_topics_asked)) >= 3
    for q_ev in (q1, q2, q3):
        low = q_ev["done"]["text"].lower()
        assert "were any bestsellers out of stock" not in low
        assert "run out of stock" not in low
        assert "gone out of stock" not in low
    assert session.phase.value in ("DIAGNOSE", "RECOMMEND")
    assert d4["done"]["text"]

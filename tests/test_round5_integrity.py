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

_FALLBACK_BRIEF_SKUS_CSV = """sku_id,name,category,price,orders_prev,orders_curr,competitor_lowest_price,days_out_of_stock_prev,days_out_of_stock_curr,stock_qty_curr,impressions_prev,impressions_curr,clicks_prev,clicks_curr
HK-001,Steel Lunch Box 3-Tier,Cookware,760,0,0,772,30,30,0,20,20,0,0
HK-002,Glass Water Bottle 1L,Storage,500,0,0,510,30,30,0,20,20,0,0
HK-003,Wooden Rolling Pin,Tableware,510,0,0,526,30,30,0,20,20,0,0
HK-004,Plastic Kitchen Organizer,Appliances,400,0,0,414,30,30,0,20,20,0,0
HK-005,Cast Iron Skillet 10in,Kitchen Tools,240,0,0,248,30,30,0,20,20,0,0
HK-006,Stainless Steel Pressure Cooker 5L,Cookware,230,24,6,232,0,22,0,4800,3600,192,144
HK-007,Non-Stick Granite Tawa 28cm,Storage,470,20,4,488,0,20,0,4000,3000,160,120
HK-008,Insulated Steel Casserole Set,Tableware,840,18,5,849,0,19,0,3600,2700,144,108
HK-009,Airtight Kitchen Storage Jars,Appliances,570,16,5,573,0,18,0,3200,2400,128,96
HK-010,Tadka Pan 14cm,Kitchen Tools,470,14,4,468,0,16,0,2800,2100,112,84
HK-011,Steel Idli Maker 4-Plate,Cookware,620,5,3,630,0,10,0,1000,750,40,30
HK-012,Copper Water Jug,Storage,660,4,2,659,0,9,0,800,600,32,24
HK-013,Bamboo Chopping Board,Tableware,380,4,3,381,0,9,0,800,600,32,24
HK-014,Silicone Baking Mat,Appliances,460,3,2,469,0,8,0,600,450,24,18
HK-015,Masala Dabba Steel 7-Cup,Kitchen Tools,450,3,2,463,0,8,0,600,450,24,18
HK-016,Ceramic Coffee Mug Set 4Pc,Cookware,770,2,1,791,0,7,0,400,300,16,12
HK-017,Melamine Serving Tray Pair,Storage,240,2,1,239,0,6,0,400,300,16,12
HK-018,Glass Bowl Set 6Pc,Tableware,250,1,0,248,0,6,0,200,150,8,6
HK-019,Electric Kettle 1.5L,Appliances,700,30,22,655,0,0,77,6000,6000,240,240
HK-020,3-Tier Modular Dish Rack,Kitchen Tools,500,28,20,465,0,0,27,5600,5600,224,224
HK-021,Silicone Non-Stick Spatula Set,Cookware,300,24,14,277,0,0,111,4800,4800,192,192
HK-022,Vegetable Chopper 12-in-1,Storage,710,8,5,736,0,0,91,1600,1000,64,40
HK-023,Steel Ladle Set,Tableware,590,8,5,593,0,0,92,1600,1000,64,40
HK-024,Hand Blender 300W,Appliances,160,7,4,160,0,0,127,1400,800,56,32
HK-025,Tea Strainer Set,Kitchen Tools,760,7,4,773,0,0,45,1400,800,56,32
HK-026,Oil Dispenser 500ml,Cookware,470,6,4,479,0,0,112,1200,800,48,32
HK-027,Pasta Maker Manual,Storage,440,6,4,448,0,0,72,1200,800,48,32
HK-028,Grater 4-Side,Tableware,580,6,4,581,0,0,40,1200,800,48,32
HK-029,Egg Boiler Electric,Appliances,440,6,4,448,0,0,31,1200,800,48,32
HK-030,Rice Cooker 1.8L,Kitchen Tools,820,6,4,845,0,0,83,1200,800,48,32
HK-031,Mixing Bowl Set 5Pc,Cookware,330,5,3,334,0,0,84,1000,600,40,24
HK-032,Peeler Swivel Pack of 3,Storage,270,5,4,280,0,0,62,1000,800,40,32
HK-033,Measuring Cups Set,Tableware,650,5,4,669,0,0,115,1000,800,40,32
HK-034,Kitchen Scale Digital,Appliances,530,3,3,545,0,0,99,600,600,24,24
HK-035,Chapati Box Insulated,Kitchen Tools,190,5,5,192,0,0,35,1000,1000,40,40
HK-036,Steel Thali Set 6Pc,Cookware,820,5,5,833,0,0,133,1000,1000,40,40
HK-037,Cutlery Set 24Pc,Storage,250,5,5,253,0,0,130,1000,1000,40,40
HK-038,Water Dispenser Pump,Tableware,360,5,5,363,0,0,125,1000,1000,40,40
HK-039,Fridge Bottle Set 6Pc,Appliances,730,5,5,733,0,0,71,1000,1000,40,40
HK-040,Microfiber Cloth Pack of 6,Kitchen Tools,340,5,5,339,0,0,91,1000,1000,40,40
HK-041,Dustbin Pedal 10L,Cookware,480,5,5,477,0,0,88,1000,1000,40,40
HK-042,Spice Rack Rotating,Storage,840,5,5,853,0,0,146,1000,1000,40,40
HK-043,Steel Glass Set 6Pc,Tableware,210,5,5,211,0,0,70,1000,1000,40,40
HK-044,Milk Pan 1.5L,Appliances,370,5,5,374,0,0,91,1000,1000,40,40
HK-045,Kadhai Iron 12in,Kitchen Tools,160,5,5,164,0,0,102,1000,1000,40,40
HK-046,Frying Pan Non-Stick 24cm,Cookware,720,5,5,748,0,0,128,1000,1000,40,40
HK-047,Stainless Steel Strainer,Storage,340,5,5,338,0,0,24,1000,1000,40,40
HK-048,Plastic Container Set 10Pc,Tableware,590,5,5,594,0,0,94,1000,1000,40,40
HK-049,Lunch Bag Insulated,Appliances,680,5,5,679,0,0,59,1000,1000,40,40
HK-050,Ice Cube Tray 2-Pack,Kitchen Tools,230,5,5,236,0,0,99,1000,1000,40,40
HK-051,Wooden Cooking Spoon Set 5Pc,Cookware,252,5,5,256,0,0,83,1000,1000,40,40
HK-052,Cake Tin Round,Storage,710,5,5,703,0,0,78,1000,1000,40,40
HK-053,Pizza Cutter Wheel,Tableware,740,5,5,752,0,0,26,1000,1000,40,40
HK-054,Can Opener Heavy Duty,Appliances,600,5,5,599,0,0,42,1000,1000,40,40
HK-055,Garlic Press Steel,Kitchen Tools,160,5,5,162,0,0,33,1000,1000,40,40
HK-056,Kitchen Towel Holder,Cookware,610,5,5,630,0,0,129,1000,1000,40,40
HK-057,Sink Organizer Caddy,Storage,350,4,4,360,0,0,76,800,800,32,32
HK-058,Silicone Oven Mitt Pair,Tableware,480,4,4,491,0,0,51,800,800,32,32
HK-059,Salad Spinner 3L,Appliances,370,3,3,375,0,0,58,600,600,24,24
HK-060,Pickle Jar Glass 1L,Kitchen Tools,760,3,3,790,0,0,106,600,600,24,24
"""

_FALLBACK_BRIEF_CANCELS_CSV = """reason,count_prev,count_curr
seller_stock_unavailable,6,14
dispatch_sla_breach,5,9
buyer_initiated,15,11
courier_address_issue,6,4
"""


def _brief_csv_paths() -> tuple[Path, Path]:
    sku_p = ROOT / "brief_scenario_skus.csv"
    can_p = ROOT / "brief_scenario_cancellations.csv"
    if sku_p.exists() and can_p.exists():
        return sku_p, can_p
    scratch = ROOT / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    fb_sku = scratch / "brief_fallback_skus.csv"
    fb_can = scratch / "brief_fallback_cancellations.csv"
    fb_sku.write_text(_FALLBACK_BRIEF_SKUS_CSV, encoding="utf-8")
    fb_can.write_text(_FALLBACK_BRIEF_CANCELS_CSV, encoding="utf-8")
    return fb_sku, fb_can


def _load_brief_context():
    sku_p, can_p = _brief_csv_paths()
    return parse_uploaded_csv(
        sku_p.read_bytes(),
        cancellations_csv_bytes=can_p.read_bytes(),
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
    rng_text = "Rs 22,200 - Rs 28,500/month recovery, 50.0% drop across HK-008 OOS SKU search funnel ASP."
    out_en = to_speakable_text(rng_text, "en-IN", seller_context=ctx)
    assert "twenty-two thousand two hundred to twenty-eight thousand five hundred rupees a month" in out_en
    assert "rupeesa" not in to_speakable_text("Rs 22,200 - Rs 28,500 a month recovery.", "en-IN", seller_context=ctx)
    assert "rupees a month" in to_speakable_text("Rs 22,200 - Rs 28,500 a month recovery.", "en-IN", seller_context=ctx)
    assert "point zero" not in out_en and "fifty percent" in out_en
    assert "search visibility" in out_en.lower() and "search search" not in out_en.lower()
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
    sku_p, can_p = _brief_csv_paths()
    default_ctx = parse_uploaded_csv(
        sku_p.read_bytes(),
        cancellations_csv_bytes=can_p.read_bytes(),
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


def _assert_clean_spoken_turn(text: str, speakable: str) -> None:
    import re

    for sample in (text, speakable):
        low = sample.lower()
        assert not re.search(r"\b(?:asp|sku|skus|oos|funnel|funnels)\b", low), f"Forbidden term in spoken output: {sample!r}"
        assert not re.search(r"\b[A-Z]{2,8}-\d+\b", sample), f"Product/action code in spoken output: {sample!r}"
        assert not re.search(r"\b\d+\.0+\s*%", sample), f"Trailing .0% in spoken output: {sample!r}"
        assert "seller partner" not in low, f"Placeholder seller in spoken output: {sample!r}"
        assert "custom uploaded store" not in low, f"Placeholder store in spoken output: {sample!r}"
        assert "135,000" not in sample and "200,000" not in sample, f"Western grouping found: {sample!r}"


async def test_regression_transcript_1_soft_yes_no_commitments():
    ctx = _load_brief_context()
    store = SessionStore()
    session = store.create(mode="proactive", language="en-IN", seller_context=ctx)
    router = LLMRouter([MockLLMProvider()])
    orch = AgentOrchestrator(router)

    turns = []
    turns.append(await _collect_turn(orch, session, None))
    turns.append(await _collect_turn(orch, session, "Wait, what happened to my sales? Tell me why."))
    turns.append(await _collect_turn(orch, session, "What actions do you recommend?"))
    # Transcript 1 exact soft-yes replies must NEVER record a commitment
    turns.append(await _collect_turn(orch, session, "okay sounds good"))
    assert len(session.commitments) == 0
    turns.append(await _collect_turn(orch, session, "alright"))
    assert len(session.commitments) == 0
    turns.append(await _collect_turn(orch, session, "yup"))
    assert len(session.commitments) == 0
    summary_turn = await _collect_turn(orch, session, "Please send me the call summary now.")
    turns.append(summary_turn)

    assert len(session.commitments) == 0
    assert session.summary_card is not None
    assert session.summary_card["agreed_actions"] == []
    assert "*Agreed actions:*\nNone" in session.summary_card["whatsapp_text"]
    recap = summary_turn["done"]["text"]
    assert "?" in recap and len(recap.split()) <= 80
    for t in turns:
        _assert_clean_spoken_turn(t["done"]["text"], t["done"]["speakable"])


async def test_regression_transcript_2_selective_first_action_only():
    ctx = _load_brief_context()
    store = SessionStore()
    session = store.create(mode="proactive", language="en-IN", seller_context=ctx)
    router = LLMRouter([MockLLMProvider()])
    orch = AgentOrchestrator(router)

    turns = []
    turns.append(await _collect_turn(orch, session, None))
    turns.append(await _collect_turn(orch, session, "Why did sales drop? Check my data and tell me why."))
    turns.append(await _collect_turn(orch, session, "Okay, tell me the recommended plan."))
    # Clear yes on 1st action, then selective keep of 1st only and decline others
    turns.append(await _collect_turn(orch, session, "yes"))
    assert len(session.commitments) == 1
    summary_turn = await _collect_turn(orch, session, "lets go ahead with only 1st action point not others")
    turns.append(summary_turn)

    assert session.phase.value == "SUMMARY"
    assert len(session.commitments) == 1
    assert session.summary_card is not None
    agreed = session.summary_card["agreed_actions"]
    declined = session.summary_card["declined_actions"]
    assert len(agreed) == 1
    assert agreed[0]["action_id"] == session.commitments[0]["action_id"]
    assert len(declined) >= 1
    wa = session.summary_card["whatsapp_text"]
    assert "*Agreed actions:*" in wa and "*Declined or not agreed:*" in wa
    recap = summary_turn["done"]["text"]
    assert len(recap.split()) <= 80
    assert "agreed:" in recap.lower()
    assert "not agreed:" in recap.lower()
    for t in turns:
        _assert_clean_spoken_turn(t["done"]["text"], t["done"]["speakable"])


async def test_regression_transcript_3_full_consent_clean_register():
    ctx = _load_brief_context()
    store = SessionStore()
    session = store.create(mode="proactive", language="en-IN", seller_context=ctx)
    router = LLMRouter([MockLLMProvider()])
    orch = AgentOrchestrator(router)

    turns = []
    turns.append(await _collect_turn(orch, session, None))
    turns.append(await _collect_turn(orch, session, "Yes I have 2 minutes."))
    turns.append(await _collect_turn(orch, session, "Our supplier restocks were delayed by 10 days."))
    turns.append(await _collect_turn(orch, session, "Show me the full diagnosis now."))
    turns.append(await _collect_turn(orch, session, "That is helpful. What should I do?"))
    turns.append(await _collect_turn(orch, session, "Yes, confirmed."))
    turns.append(await _collect_turn(orch, session, "Yes, I agree to that too."))
    turns.append(await _collect_turn(orch, session, "Yes, I commit to this price update as well."))

    assert 2 <= len(session.commitments) <= 3
    assert session.phase.value == "SUMMARY"
    assert session.summary_card is not None
    assert len(session.summary_card["agreed_actions"]) == len(session.commitments)
    summary_done = turns[-1]["done"]
    assert len(summary_done["text"].split()) <= 80
    assert "agreed:" in summary_done["text"].lower()
    assert "follow-up" in summary_done["text"].lower()

    full_text = " ".join(t["done"]["text"] for t in turns).lower()
    assert full_text.count("subject to eligibility") <= 1
    assert session.ungrounded_warnings == []
    assert session.date_warnings == []
    assert session.projection_warnings == []
    assert session.causal_warnings == []
    for t in turns:
        d = t["done"]
        _assert_clean_spoken_turn(d["text"], d["speakable"])


def test_group5_frontend_reprompt_timing_and_upload_names_in_greeting():
    import os
    import socket
    import subprocess
    import sys
    import time
    import httpx
    from playwright.sync_api import sync_playwright

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    base_url = f"http://127.0.0.1:{port}"
    scratch = ROOT / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    log_file = open(scratch / "uvicorn_group5_ui.log", "w", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "ALLOW_MOCK_LLM": "true",
            "GROQ_API_KEY": "",
            "GEMINI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "TTS_PROVIDER": "browser",
            "TTS_CACHE_ENABLED": "false",
            "PORT": str(port),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )

    speech_stub = """
    class MockSR {
      start() { if (this.onstart) this.onstart(); }
      stop() { if (this.onend) this.onend(); }
    }
    window.SpeechRecognition = MockSR;
    window.webkitSpeechRecognition = MockSR;
    class MockUtterance {
      constructor(text) { this.text = text; this.lang = 'en-IN'; this.rate = 1; }
    }
    Object.defineProperty(window, 'SpeechSynthesisUtterance', { configurable: true, writable: true, value: MockUtterance });
    const mockSynth = {
      getVoices() { return [{ lang: 'en-IN', name: 'MockVoice' }]; },
      cancel() {},
      speak(u) { setTimeout(() => { if (u && typeof u.onend === 'function') u.onend(); }, 30); }
    };
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, get: () => mockSynth });
    HTMLMediaElement.prototype.play = function() {
      setTimeout(() => { if (this.onended) this.onended(); }, 40);
      return Promise.resolve();
    };
    """

    try:
        ready = False
        for _ in range(60):
            try:
                if httpx.get(f"{base_url}/api/health", timeout=1.5).status_code == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.3)
        assert ready, "Server did not start for Group 5 UI test"

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)

            context = browser.new_context(viewport={"width": 1280, "height": 860})
            page = context.new_page()
            page.add_init_script(speech_stub)
            page.goto(base_url, wait_until="networkidle")

            # 1. Fill custom upload fields & CSV -> custom shop + seller names must appear in the call greeting
            sku_path, can_path = _brief_csv_paths()
            page.click("#btnDataModeUpload")
            page.set_input_files("#fileSkuDataset", str(sku_path))
            page.set_input_files("#fileCancelDataset", str(can_path))
            page.fill("#txtUploadShop", "Loom and Craft Studio")
            page.fill("#txtUploadSeller", "Meera Joshi")
            page.click("#btnValidateUpload")
            page.wait_for_function(
                "() => document.querySelector('#datasetSummary').innerText.includes('Loom and Craft Studio')",
                timeout=8000,
            )

            page.select_option("#selMode", "inbound")
            page.click("#btnStartCall")
            page.wait_for_selector('#micOrb[data-state="listening"]', timeout=10000)

            greet_caps = page.evaluate("() => [...document.querySelectorAll('.caption-line.kam')].map(n => n.innerText)")
            assert len(greet_caps) == 1
            assert "Meera Joshi" in greet_caps[0]
            assert "Loom and Craft Studio" in greet_caps[0]

            # 2. First-turn silence must wait 8s (NOT fire at 6.3s, fire by ~8.5s)
            page.wait_for_timeout(6300)
            caps_mid = page.evaluate("() => [...document.querySelectorAll('.caption-line.kam')].map(n => n.innerText)")
            assert len(caps_mid) == 1, f"Reprompted too early before 8s: {caps_mid}"

            page.wait_for_function(
                "() => document.querySelectorAll('.caption-line.kam').length >= 2",
                timeout=4000,
            )
            caps_after_first = page.evaluate("() => [...document.querySelectorAll('.caption-line.kam')].map(n => n.innerText)")
            assert len(caps_after_first) == 2
            assert "still there" in caps_after_first[1].lower()
            assert "Meera Joshi" in caps_after_first[1]
            assert "seller partner" not in caps_after_first[1].lower()

            # 3. Subsequent user turn silence must NOT reprompt a second time (once per call)
            page.click("#btnToggleDebug")
            before_done = int(page.evaluate("() => document.body.dataset.completedStreams"))
            page.fill("#txtDebugInput", "Yes my sales dropped last month.")
            page.click("#btnSendDebug")
            page.wait_for_function(
                f"() => Number(document.body.dataset.completedStreams) > {before_done}",
                timeout=15000,
            )
            page.wait_for_selector('#micOrb[data-state="listening"]', timeout=8000)
            page.wait_for_timeout(6600)
            caps_once = page.evaluate("() => [...document.querySelectorAll('.caption-line.kam')].length")
            assert caps_once == 3, f"Expected no second reprompt in same call, got {caps_once} KAM messages"
            page.click("#btnEndCall")

            # 4. Call 2: Seller speaks on turn 0 -> subsequent silence triggers 6s reprompt
            page.click("#btnStartCall")
            page.wait_for_selector('#micOrb[data-state="listening"]', timeout=10000)
            before_c2 = int(page.evaluate("() => document.body.dataset.completedStreams"))
            page.fill("#txtDebugInput", "Hello, please check my store.")
            page.click("#btnSendDebug")
            page.wait_for_function(
                f"() => Number(document.body.dataset.completedStreams) > {before_c2}",
                timeout=15000,
            )
            page.wait_for_selector('#micOrb[data-state="listening"]', timeout=8000)
            page.wait_for_timeout(4200)
            assert page.evaluate("() => document.querySelectorAll('.caption-line.kam').length") == 2
            page.wait_for_function(
                "() => document.querySelectorAll('.caption-line.kam').length >= 3",
                timeout=3500,
            )
            page.click("#btnEndCall")

            # Also verify JSON upload honors seller_meta form overrides via API
            tpl_json_bytes = httpx.get(f"{base_url}/api/data/template?format=json", timeout=5.0).content
            r_json = httpx.post(
                f"{base_url}/api/data/validate",
                files={"file": ("brief.json", tpl_json_bytes, "application/json")},
                data={"shop_name": "Kaveri Home Store", "seller_name": "Vikram Nair"},
                timeout=10.0,
            )
            assert r_json.status_code == 200 and r_json.json()["valid"] is True
            ds_id = r_json.json()["dataset_id"]
            r_demo = httpx.get(f"{base_url}/api/demo-data", params={"dataset_id": ds_id}, timeout=5.0)
            assert r_demo.status_code == 200
            sel_meta = r_demo.json()["overview"]["seller"]
            assert sel_meta["shop_name"] == "Kaveri Home Store"
            assert sel_meta["seller_name"] == "Vikram Nair"

            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log_file.close()

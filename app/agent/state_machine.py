"""Server-controlled conversation phase state machine & SellerContext-parameterized prompt builder."""

from __future__ import annotations

import json
import re
from typing import Any

from app.agent.session import Phase, SessionState
from app.config import get_settings
from app.engine import diagnosis
from app.engine.tools import TOOL_SCHEMAS

# Precompiled word-boundary regexes (avoid substring bugs like "look"/"broken" triggering "ok")
_DIAGNOSIS_REQUEST_RE = re.compile(
    r"\b(?:"
    r"what's wrong|whats wrong|what happened|check my data|tell me why|"
    r"tell me|show me|you check|aap check|kya hua|"
    r"why|reason|cause|dropped|drop|declined|happened|check|data|numbers|kyu|kya"
    r")\b",
    re.IGNORECASE,
)

_PROACTIVE_WHY_RE = re.compile(
    r"\b(?:what happened|tell me|kya hua|why|kyu|kya)\b",
    re.IGNORECASE,
)

_CLARIFY_DATA_NOW_RE = re.compile(
    r"\b(?:check my data|show me|tell me why|you check|aap check)\b",
    re.IGNORECASE,
)

_BUY_IN_RE = re.compile(
    r"\b(?:"
    r"what should i do|makes sense|theek hai|let's do|lets do|kar sakte|"
    r"yes|yeah|yep|sure|okay|ok|haan|theek|kar|next|action|fix|recommend|plan|agree"
    r")\b",
    re.IGNORECASE,
)

_NEGATION_RE = re.compile(
    r"\b(?:no|not|don't|dont|do not|cannot|can't|cant|won't|wont|nahi|nahin|na)\b",
    re.IGNORECASE,
)


def is_diagnosis_request(text: str) -> bool:
    """Return True when the utterance asks for root causes or data inspection."""
    return bool(_DIAGNOSIS_REQUEST_RE.search(text or ""))


def is_buy_in(text: str) -> bool:
    """Return True for genuine buy-in; simple negation in the same utterance cancels buy-in."""
    if not text or _NEGATION_RE.search(text):
        return False
    return bool(_BUY_IN_RE.search(text))


def tools_for_phase(phase: Phase) -> list[dict[str, Any]]:
    """Phase-gate tools: 0 tools in pre-executed phases; max 2 tools in CLARIFY/COMMIT/SUMMARY."""
    allowed_map: dict[Phase, set[str]] = {
        Phase.GREETING: set(),
        Phase.UNDERSTAND: set(),
        Phase.CLARIFY: {"get_seller_overview", "get_metric_comparison"},
        Phase.DIAGNOSE: set(),  # Server pre-executes all 5 diagnosis tools
        Phase.REVIEW_COMMITMENTS: set(),  # Server pre-executes follow-up diagnosis + metrics
        Phase.RECOMMEND: set(),  # Server pre-executes build_action_plan
        Phase.COMMIT: {"commit_action", "schedule_followup"},
        Phase.SUMMARY: {"send_call_summary", "schedule_followup"},
        Phase.CLOSE: set(),
    }
    allowed = allowed_map.get(phase, set())
    if not allowed:
        return []
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in allowed]


def advance_phase_before_turn(session: SessionState, user_text: str) -> Phase:
    """Deterministically advance phase BEFORE generating the agent's reply."""
    text = user_text.strip()
    p = session.phase

    if p == Phase.GREETING:
        if session.mode == "followup":
            session.phase = Phase.REVIEW_COMMITMENTS
        elif session.mode == "proactive":
            if _PROACTIVE_WHY_RE.search(text):
                session.phase = Phase.DIAGNOSE
            else:
                session.phase = Phase.CLARIFY
        else:
            if len(text.split()) >= 6 and is_diagnosis_request(text):
                session.phase = Phase.CLARIFY
            else:
                session.phase = Phase.UNDERSTAND
        return session.phase

    if p == Phase.UNDERSTAND:
        session.phase = Phase.CLARIFY
        return session.phase

    if p == Phase.CLARIFY:
        if (
            session.clarify_questions_asked >= 3
            or (session.clarify_questions_asked >= 2 and is_diagnosis_request(text))
            or bool(_CLARIFY_DATA_NOW_RE.search(text))
        ):
            session.phase = Phase.DIAGNOSE
        return session.phase

    if p == Phase.REVIEW_COMMITMENTS:
        session.phase = Phase.RECOMMEND
        return session.phase

    if p == Phase.DIAGNOSE:
        session.phase = Phase.RECOMMEND
        return session.phase

    if p == Phase.RECOMMEND:
        if session.recommend_turns >= 1 or is_buy_in(text):
            session.phase = Phase.COMMIT
        return session.phase

    if p == Phase.COMMIT:
        if session.commitments or session.commit_turns >= 2:
            session.phase = Phase.SUMMARY
        return session.phase

    if p == Phase.SUMMARY:
        session.phase = Phase.CLOSE
        return session.phase

    return session.phase


def advance_phase_after_turn(session: SessionState) -> Phase:
    """Track per-phase turn counters."""
    p = session.phase
    if p == Phase.CLARIFY:
        session.clarify_questions_asked += 1
    elif p == Phase.DIAGNOSE:
        session.diagnose_turns += 1
    elif p == Phase.RECOMMEND:
        session.recommend_turns += 1
    elif p == Phase.COMMIT:
        session.commit_turns += 1
    return session.phase


def _language_rule(language: str, salutation: str) -> str:
    if language == "hi-IN":
        return (
            "LANGUAGE: hi-IN. Speak natural conversational Hindi in Devanagari, "
            "keeping business words (stock, order, cancel, price, dispatch) in simple English or Hinglish."
        )
    if language == "hinglish":
        return (
            f"LANGUAGE: hinglish. Speak warm, natural Indian Hinglish in Latin script (e.g., 'Haan {salutation}, maine check kiya...'), "
            "keeping business terms (stock, order, cancel, price) in English."
        )
    return f"LANGUAGE: en-IN. Speak warm, natural Indian English addressing the seller as '{salutation}'."


def _phase_instruction(
    session: SessionState,
    headline: dict[str, Any],
    diag: dict[str, Any],
) -> str:
    profile = session.seller_context.profile
    salutation = profile.salutation
    shop_name = profile.shop_name
    m = headline["metrics"]
    sales_curr = m["sales_gmv"]["current"]
    sales_pct = abs(m["sales_gmv"]["delta_pct"])
    orders_drop = abs(m["orders"]["delta"])
    asp_curr = m["asp"]["current"]
    oos_curr = m["out_of_stock_skus"]["current"]
    cancel_curr = m["cancellation_rate_pct"]["current"]
    fu = headline.get("followup") or {}
    assessed_drivers = [
        d for d in diag.get("drivers", []) if d.get("assessable") and d.get("driver") != "unexplained_residual"
    ]
    top_driver = assessed_drivers[0] if assessed_drivers else None
    missing_fields = diag.get("missing_signals") or []

    p = session.phase
    if p == Phase.GREETING:
        if session.mode == "followup" and fu:
            return (
                f"GOAL: Greet {salutation} warmly for your follow-up call, cite the updated pace "
                f"(Rs {fu['sales_gmv_pace']:,} monthly pace, OOS {fu['out_of_stock_skus']}, "
                f"cancellations {fu['cancellation_rate_pct']}%), and ask if they have 2 minutes to review remaining blockers."
            )
        if session.mode == "proactive":
            oos_note = f" and {oos_curr} products are out of stock" if oos_curr is not None else ""
            return (
                f"GOAL: Open proactively—state sales moved {sales_pct}% to Rs {sales_curr:,}{oos_note}, "
                f"and ask if {salutation} has 2 minutes to review this together."
            )
        return f"GOAL: Greet {salutation} warmly and ask how you can help {shop_name} today."

    if p == Phase.UNDERSTAND:
        return (
            "GOAL: Empathize sincerely with the revenue decline (never defensive), acknowledge their concern, "
            "and ask 1 focused question about what changed in their operations recently."
        )

    if p == Phase.CLARIFY:
        asp_note = (
            f" (ASP stayed steady around Rs {asp_curr})" if diag.get("asp_flat") else f" (ASP shifted to Rs {asp_curr})"
        )
        return (
            f"GOAL: Clarify root causes (question {session.clarify_questions_asked + 1} of 3 max). Ask ONE short non-redundant question "
            "(e.g. restocking delays on bestsellers, dispatch/stock-sync delays, or recent competitor price changes). "
            f"NEVER ask if ticket size/ASP collapsed if the data already shows it{asp_note}."
        )

    if p == Phase.REVIEW_COMMITMENTS:
        return (
            "GOAL: Review earlier commitments using the pre-loaded follow-up data: highlight progress, cite remaining open blockers "
            "directly from VERIFIED DATA CONTEXT, and ask to close them."
        )

    if p == Phase.DIAGNOSE:
        top_text = (
            f"{top_driver['title']} explained roughly {top_driver['share_pct']}% ({top_driver['lost_orders']} of {orders_drop} lost orders)"
            if top_driver
            else f"{orders_drop} orders were lost"
        )
        resid_driver = next((d for d in diag.get("drivers", []) if d["driver"] == "unexplained_residual"), None)
        resid_note = (
            f" Explicitly mention the unexplained residual (~{resid_driver['share_pct']}%, {resid_driver['lost_orders']} orders) rather than overclaiming certainty."
            if resid_driver and resid_driver.get("share_pct", 0) >= 10.0
            else ""
        )
        missing_note = (
            " Also name any missing columns as 'not assessable with the data provided'."
            if missing_fields
            else ""
        )
        cancel_note = f" Cancellation rate is {cancel_curr}%." if cancel_curr is not None else ""
        return (
            "GOAL: Give 2 ultra-short sentences under 40 words total using uncertain phrasing ('roughly'): "
            f"(1) Cite exact ASP Rs {asp_curr} and that {top_text}.{cancel_note} "
            f"(2) Name the secondary cause.{resid_note}{missing_note} Ask if they are ready for the action plan."
        )

    if p == Phase.RECOMMEND:
        return (
            "GOAL: Share the prioritized actions from the pre-loaded action plan concisely (referencing exact target SKUs and "
            "indicative recovery ranges, adding 'subject to eligibility' where promotions apply; never guarantee results or name real marketplace programs) "
            "and ask for buy-in."
        )

    if p == Phase.COMMIT:
        return (
            f"GOAL: Drive ownership! Ask {salutation} to commit to specific due dates for the top actions. "
            "If the seller agrees or states a timeline, IMMEDIATELY call commit_action and schedule_followup."
        )

    if p == Phase.SUMMARY:
        return (
            "GOAL: Call send_call_summary with 2-3 key issues citing verified numbers, then deliver a crisp spoken recap covering root causes, "
            "committed actions with due dates, and the follow-up check-in."
        )

    return f"GOAL: Warmly close the call and thank {salutation}."


def build_system_prompt(
    session: SessionState,
    precomputed_context: dict[str, Any] | None = None,
) -> str:
    """Build a compact system prompt parameterized cleanly by session.seller_context."""
    settings = get_settings()
    ctx = session.seller_context
    profile = ctx.profile
    headline = diagnosis.compute_headline_metrics(ctx, session.snapshot)
    diag = diagnosis.run_diagnosis(ctx, session.snapshot)

    agent_name = settings.agent_name_devanagari if session.language == "hi-IN" else settings.agent_name
    seller_name = profile.seller_name
    salutation = profile.salutation
    shop_name = profile.shop_name
    city_part = f", {profile.city}" if profile.city else ""
    baseline_snapshot = {
        "seller_name": seller_name,
        "salutation": salutation,
        "shop_name": shop_name,
        "period_prev": profile.period_prev_label,
        "period_curr": profile.period_curr_label,
        "sales_prev": headline["metrics"]["sales_gmv"]["baseline"],
        "sales_curr": headline["metrics"]["sales_gmv"]["current"],
        "sales_delta_pct": headline["metrics"]["sales_gmv"]["delta_pct"],
        "orders_prev": headline["metrics"]["orders"]["baseline"],
        "orders_curr": headline["metrics"]["orders"]["current"],
        "orders_drop": abs(headline["metrics"]["orders"]["delta"]),
        "asp": headline["metrics"]["asp"]["current"],
        "oos_prev": headline["metrics"]["out_of_stock_skus"]["baseline"],
        "oos_curr": headline["metrics"]["out_of_stock_skus"]["current"],
        "cancel_prev_pct": headline["metrics"]["cancellation_rate_pct"]["baseline"],
        "cancel_curr_pct": headline["metrics"]["cancellation_rate_pct"]["current"],
        "missing_signals": diag.get("missing_signals") or [],
        "drivers_summary": [
            {
                "driver": d["driver"],
                "title": d["title"],
                "assessable": d["assessable"],
                "share_pct": d["share_pct"],
                "lost_orders": d["lost_orders"],
            }
            for d in diag.get("drivers", [])
        ],
    }

    lines = [
        f"You are {agent_name}, a warm, proactive Virtual Key Account Manager for an online marketplace speaking with {seller_name} ({salutation}) of {shop_name}{city_part}.",
        _language_rule(session.language, salutation),
        f"CURRENT PHASE: {session.phase.value} | SNAPSHOT: {session.snapshot} | SCENARIO: {profile.scenario_id}",
        "STRICT VOICE RULES:",
        "- Keep replies to 1-2 short sentences (under 35 words; max 45 words; SUMMARY up to 65 words).",
        "- Ask at most ONE question per turn. Use natural acknowledgments ('Got it', 'I understand').",
        "- NO markdown, NO bullet lists, NO emojis, NO headings, NO internal reasoning.",
        "- NEVER invent or calculate numbers; speak ONLY numbers present in BASELINE SNAPSHOT or VERIFIED DATA CONTEXT.",
        "- Keep recommendations marketplace-agnostic: never claim guaranteed outcomes or real marketplace program names; qualify promos/ads with 'subject to eligibility'.",
        "- If a driver or metric is missing from the uploaded file, explicitly say it is 'not assessable with the data provided' and never guess.",
        "- If seller is angry or off-topic, empathize briefly and steer back to recovering their sales.",
        _phase_instruction(session, headline, diag),
        "BASELINE SNAPSHOT: " + json.dumps(baseline_snapshot, ensure_ascii=False),
    ]
    if session.commitments:
        lines.append("COMMITMENTS LOGGED: " + json.dumps(session.commitments, ensure_ascii=False))
    if session.mode == "followup" and session.prior_commitments:
        lines.append("PRIOR CALL COMMITMENTS: " + json.dumps(session.prior_commitments, ensure_ascii=False))
    if precomputed_context:
        lines.append("VERIFIED DATA CONTEXT (cite directly, do not invent figures): " + json.dumps(precomputed_context, ensure_ascii=False))
    return "\n".join(lines)

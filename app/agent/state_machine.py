"""Server-controlled conversation phase state machine & compact prompt builder."""

from __future__ import annotations

import json
from typing import Any

from app.agent.session import Phase, SessionState
from app.engine.tools import TOOL_SCHEMAS

# Signals for deterministic server phase transitions
_DIAGNOSIS_REQUEST_SIGNALS = (
    "why", "reason", "cause", "dropped", "drop", "declined", "happened",
    "check", "data", "numbers", "tell me", "what's wrong", "kya hua", "kyu",
)
_BUY_IN_SIGNALS = (
    "yes", "yeah", "yep", "sure", "okay", "ok", "haan", "theek", "kar", "let's do",
    "makes sense", "what should i do", "next", "action", "fix", "recommend", "plan",
)
_COMMIT_SIGNALS = (
    "commit", "promise", "i will", "will do", "friday", "monday", "tuesday",
    "tomorrow", "kal", "48 hours", "today", "aaj", "days", "agree", "done", "yes", "haan", "ok",
)


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
    """Deterministically advance phase BEFORE generating Riya's reply."""
    low = user_text.lower()
    p = session.phase

    if p == Phase.GREETING:
        if session.mode == "followup":
            session.phase = Phase.REVIEW_COMMITMENTS
        elif session.mode == "proactive":
            # If seller says yes/asks what happened, jump straight into 1 quick clarify or diagnose
            if any(k in low for k in ("why", "what happened", "tell me", "kyu", "kya")):
                session.phase = Phase.DIAGNOSE
            else:
                session.phase = Phase.CLARIFY
        else:
            # Inbound mode: seller states reason on turn 1
            if len(low.split()) >= 6 and any(k in low for k in _DIAGNOSIS_REQUEST_SIGNALS):
                session.phase = Phase.CLARIFY
            else:
                session.phase = Phase.UNDERSTAND
        return session.phase

    if p == Phase.UNDERSTAND:
        session.phase = Phase.CLARIFY
        return session.phase

    if p == Phase.CLARIFY:
        # Advance to DIAGNOSE once cap (3 questions) reached or seller asks for data / gives clear context
        if (
            session.clarify_questions_asked >= 3
            or (session.clarify_questions_asked >= 2 and any(k in low for k in _DIAGNOSIS_REQUEST_SIGNALS))
            or any(k in low for k in ("check my data", "show me", "tell me why", "you check", "aap check"))
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
        if session.recommend_turns >= 1 or any(k in low for k in _BUY_IN_SIGNALS):
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
    """Track per-phase turn counters and advance if a milestone artifact was captured."""
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


def _language_rule(language: str) -> str:
    if language == "hi-IN":
        return (
            "LANGUAGE: hi-IN. Speak natural conversational Hindi in Devanagari or simple Romanized Hindi? "
            "Use clear Devanagari Hindi, keeping business words (stock, order, cancel, price, dispatch) in simple English or Hinglish."
        )
    if language == "hinglish":
        return (
            "LANGUAGE: hinglish. Speak warm, natural Indian Hinglish in Latin script (e.g., 'Haan Rajesh ji, maine check kiya...'), "
            "keeping business terms (stock, order, cancel, price) in English."
        )
    return "LANGUAGE: en-IN. Speak warm, natural Indian English addressing the seller as 'Rajesh ji'."


def _phase_instruction(session: SessionState) -> str:
    p = session.phase
    if p == Phase.GREETING:
        if session.mode == "followup":
            return (
                "GOAL: Greet Rajesh ji warmly for your 3-day follow-up call, cite the headline jump (Rs 1,35,000 -> Rs 1,75,500 pace, OOS 18 -> 9), "
                "and ask if he has 2 minutes to review the last 2 blockers."
            )
        if session.mode == "proactive":
            return (
                "GOAL: Open proactively—state you noticed sales dropped 32.5% to Rs 1,35,000 and 18 products are out of stock, "
                "and ask if Rajesh ji has 2 minutes to review this together."
            )
        return "GOAL: Greet Rajesh ji warmly and ask how you can help Kumar Home & Kitchen today."

    if p == Phase.UNDERSTAND:
        return (
            "GOAL: Empathize sincerely with the sales drop (never defensive), acknowledge his concern, "
            "and ask 1 focused question about what changed in his operations recently."
        )

    if p == Phase.CLARIFY:
        return (
            f"GOAL: Clarify root causes (question {session.clarify_questions_asked + 1} of 3 max). Ask ONE short non-redundant question "
            "(e.g. restocking delays on bestsellers, dispatch/stock-sync delays, or recent competitor price changes). "
            "NEVER ask if order value/ASP dropped (data already proves ASP stayed flat at Rs 500) or if buyers cancelled more."
        )

    if p == Phase.REVIEW_COMMITMENTS:
        return (
            "GOAL: Review earlier commitments using the pre-loaded follow-up data: celebrate what moved (4/5 top SKUs restocked, "
            "OOS 18 -> 9, cancellations 14% -> 10.5%, daily pace +30% to Rs 1,75,500), name the 2 remaining blockers (HK-009, HK-021/SLA), and ask to close them."
        )

    if p == Phase.DIAGNOSE:
        return (
            "GOAL: Give 2 ultra-short sentences under 35 words total: "
            "(1) ASP stayed flat at Rs 500, and 60% of the 130-order drop came from 18 out-of-stock products like 5L Pressure Cooker. "
            "(2) 14% cancellations and 3 overpriced products explain the rest; ask if he is ready for the action plan."
        )

    if p == Phase.RECOMMEND:
        return (
            "GOAL: Share the prioritized actions from the pre-loaded action plan concisely (1st: restock top 5 OOS + alerts; "
            "2nd: sync stock 2x/day & 2 PM dispatch SLA; 3rd: reprice 3 SKUs or explore promo/ads subject to eligibility) and ask for his buy-in."
        )

    if p == Phase.COMMIT:
        return (
            "GOAL: Drive ownership! Ask Rajesh ji to commit to specific due dates (e.g. restock within 48 hours, daily sync starting today). "
            "If he agrees or states a timeline, IMMEDIATELY call commit_action and schedule_followup (targets: OOS <= 8, cancellations <= 10%)."
        )

    if p == Phase.SUMMARY:
        return (
            "GOAL: Call send_call_summary with the 3 key issues, then deliver a crisp spoken recap covering root causes, "
            "committed actions with due dates, and the 3-day follow-up call."
        )

    return "GOAL: Warmly close the call and thank Rajesh ji."


def build_system_prompt(
    session: SessionState,
    precomputed_context: dict[str, Any] | None = None,
) -> str:
    """Build a compact (<400 token) system prompt for the active server-controlled phase."""
    lines = [
        "You are Riya, Snapdeal's warm, proactive Virtual Key Account Manager speaking with Rajesh Kumar (Rajesh ji) of Kumar Home & Kitchen, Delhi.",
        _language_rule(session.language),
        f"CURRENT PHASE: {session.phase.value} | SNAPSHOT: {session.snapshot}",
        "STRICT VOICE RULES:",
        "- Keep replies to 1-2 short sentences (under 35 words; max 45 words; SUMMARY up to 65 words).",
        "- Ask at most ONE question per turn. Use natural acknowledgments ('Got it', 'I understand').",
        "- NO markdown, NO bullet lists, NO emojis, NO headings, NO internal reasoning.",
        "- NEVER invent or calculate numbers; speak ONLY numbers present in the provided data/tool outputs.",
        "- If seller is angry or off-topic, empathize briefly and steer back to recovering their sales.",
        _phase_instruction(session),
    ]
    if session.commitments:
        lines.append("COMMITMENTS LOGGED: " + json.dumps(session.commitments, ensure_ascii=False))
    if session.mode == "followup" and session.prior_commitments:
        lines.append("PRIOR CALL COMMITMENTS: " + json.dumps(session.prior_commitments, ensure_ascii=False))
    if precomputed_context:
        lines.append("VERIFIED DATA CONTEXT (cite directly, do not invent figures): " + json.dumps(precomputed_context, ensure_ascii=False))
    return "\n".join(lines)


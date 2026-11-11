"""Server-controlled conversation phase state machine & SellerContext-parameterized prompt builder."""

from __future__ import annotations

import json
import re
from typing import Any

from app.agent.session import Phase, SessionState
from app.config import Settings, get_settings
from app.engine import diagnosis
from app.engine.tools import TOOL_SCHEMAS
from app.utils.dates import (
    anchor_now_ist,
    compute_due_date_ist,
    compute_followup_date_ist,
    extract_mentioned_dates,
    format_ist_date,
)
from app.utils.formatting import format_inr

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


_EARLY_END_RE = re.compile(
    r"\b(?:"
    r"send\s+(?:me\s+)?(?:the\s+)?(?:final\s+|call\s+)?summary|summary\s+bhej|"
    r"call\s+summary|wrap\s+up|end\s+(?:the\s+)?call|that's\s+all|thats\s+all|"
    r"close\s+(?:the\s+)?call|finish\s+call|bas\s+itna\s+hi"
    r")\b",
    re.IGNORECASE,
)

_AMBIGUOUS_SOFT_RE = re.compile(
    r"\b(?:"
    r"okay\s+sounds\s+good|sounds\s+good|alright|all\s+right|yup|looks\s+good|"
    r"maybe|i'?ll\s+try|i\s+will\s+try|let'?s\s+see|lets\s+see|might|not\s+sure|"
    r"dekhunga|try\s+karunga|koshish|seems\s+reasonable|fine"
    r")\b",
    re.IGNORECASE,
)

_CLEAR_YES_RE = re.compile(
    r"\b(?:"
    r"yes|haan|hanji|ji\s+haan|i\s+agree|i\s+commit|committed|confirm|confirmed|"
    r"definitely|bilkul|pakka|let'?s\s+do\s+it|will\s+do|i'?ll\s+do|"
    r"i'?ll\s+restock|i'?ll\s+update|i'?ll\s+sync|go\s+ahead"
    r")\b",
    re.IGNORECASE,
)

_SELECTIVE_ONLY_RE = re.compile(
    r"\b(?:only|just)\b.{0,40}\b(?:1st|first|2nd|second|3rd|third|one|restock|pricing|price|visibility|cancel)",
    re.IGNORECASE,
)
_SELECTIVE_NOT_OTHERS_RE = re.compile(
    r"\b(?:not\s+(?:the\s+)?others|not\s+(?:the\s+)?rest|skip\s+(?:the\s+)?(?:others|rest)|none\s+of\s+the\s+others)\b",
    re.IGNORECASE,
)


def _match_action_indices(text: str, actions: list[dict[str, Any]]) -> list[int]:
    low = text.lower()
    matched: list[int] = []
    for idx, act in enumerate(actions):
        atype = str(act.get("action_type") or "").lower()
        title = str(act.get("title") or "").lower()
        if idx == 0 and re.search(r"\b(?:1st|first|one)\b", low):
            matched.append(idx)
        elif idx == 1 and re.search(r"\b(?:2nd|second)\b", low):
            matched.append(idx)
        elif idx == 2 and re.search(r"\b(?:3rd|third)\b", low):
            matched.append(idx)
        elif atype == "restock" and re.search(r"\b(?:restock|stock|inventory)\b", low):
            matched.append(idx)
        elif atype == "pricing" and re.search(r"\b(?:price|pricing|reprice)\b", low):
            matched.append(idx)
        elif atype == "visibility" and re.search(r"\b(?:visibility|keyword|ads|sponsored|listing)\b", low):
            matched.append(idx)
        elif atype == "cancellations" and re.search(r"\b(?:cancel|cancellation|dispatch|sync)\b", low):
            matched.append(idx)
        elif title and any(w in low for w in re.findall(r"[a-z]{5,}", title)[:2]):
            matched.append(idx)
    return list(dict.fromkeys(matched))


def sync_action_plan_to_session(session: SessionState, plan_result: dict[str, Any]) -> None:
    """Populate session.action_plan_items and initialize pending statuses for 2-3 actions."""
    actions = list(plan_result.get("actions") or [])[:3]
    if not actions:
        return
    session.action_plan_items = actions
    if not hasattr(session, "action_statuses") or session.action_statuses is None:
        session.action_statuses = {}
    for act in actions:
        aid = str(act.get("action_id") or "")
        if not aid or aid in session.action_statuses:
            continue
        session.action_statuses[aid] = {
            "action_id": aid,
            "action_type": act.get("action_type", ""),
            "title": act.get("title", ""),
            "action_summary": act.get("title", ""),
            "owner": act.get("owner", session.seller_context.profile.seller_name),
            "due_date": act.get("due_date_ist", ""),
            "target_metric": "",
            "status": "pending",
        }


def next_pending_action(session: SessionState) -> dict[str, Any] | None:
    """Return the first action in action_plan_items whose status is still 'pending'."""
    for act in session.action_plan_items:
        aid = str(act.get("action_id") or "")
        st = (session.action_statuses.get(aid) or {}).get("status", "pending")
        if st == "pending":
            return act
    return None


def all_actions_resolved(session: SessionState) -> bool:
    """True when every planned action has been accepted, declined, or marked tentative/not_discussed."""
    if not session.action_plan_items:
        return bool(session.commitments) or session.commit_turns >= 2
    for act in session.action_plan_items:
        aid = str(act.get("action_id") or "")
        st = (session.action_statuses.get(aid) or {}).get("status", "pending")
        if st == "pending":
            return False
    return True


def classify_consent_reply(text: str, session: SessionState) -> dict[str, Any]:
    """Strictly classify seller consent vs soft-yes, ambiguity, selective choice, decline, or early end."""
    clean = (text or "").strip()
    low = clean.lower()
    actions = session.action_plan_items or []
    pending_conf = session.pending_confirmation
    current_act = next_pending_action(session)
    current_aid = (
        str(pending_conf["action_id"])
        if pending_conf and pending_conf.get("action_id")
        else (str(current_act["action_id"]) if current_act else None)
    )

    # 1. Early end / explicit summary request
    if _EARLY_END_RE.search(low) and "commit to" not in low:
        return {"kind": "EARLY_END", "target_action_id": current_aid}

    # 2. Selective keep + reject others ("lets go ahead with only 1st action point not others")
    if (_SELECTIVE_ONLY_RE.search(low) or _SELECTIVE_NOT_OTHERS_RE.search(low)) and actions:
        keep_indices = _match_action_indices(low, actions)
        if not keep_indices and pending_conf:
            keep_indices = [
                i for i, a in enumerate(actions) if str(a.get("action_id")) == str(pending_conf.get("action_id"))
            ]
        if not keep_indices and actions:
            keep_indices = [0]
        keep_ids = [str(actions[i].get("action_id")) for i in keep_indices]
        decline_ids = [str(a.get("action_id")) for i, a in enumerate(actions) if i not in keep_indices]
        return {
            "kind": "SELECTIVE_KEEP",
            "keep_action_ids": keep_ids,
            "decline_action_ids": decline_ids,
        }

    # 3. Ambiguity / soft-yes phrasing MUST NOT commit (unless explicit clear yes/commit overrides standalone ok)
    has_clear_yes = bool(_CLEAR_YES_RE.search(low)) and not bool(_NEGATION_RE.search(low))
    has_soft_or_ambig = bool(_AMBIGUOUS_SOFT_RE.search(low)) or low in ("ok", "okay", "k", "kk")
    # Pure soft replies ("okay sounds good", "alright", "yup", "maybe", "I'll try") -> AMBIGUOUS_OR_SOFT
    if has_soft_or_ambig and not has_clear_yes:
        return {"kind": "AMBIGUOUS_OR_SOFT", "target_action_id": current_aid}
    if re.search(r"\b(?:maybe|i'?ll\s+try|i\s+will\s+try|not\s+sure)\b", low):
        return {"kind": "AMBIGUOUS_OR_SOFT", "target_action_id": current_aid}

    # 4. Negation / decline
    if _NEGATION_RE.search(low) or re.search(r"\b(?:skip|decline|leave\s+that|won't\s+work)\b", low):
        declined_indices = _match_action_indices(low, actions) if actions else []
        if declined_indices:
            target_id = str(actions[declined_indices[0]].get("action_id"))
        else:
            target_id = current_aid
        return {"kind": "DECLINE", "target_action_id": target_id}

    # 5. Clear yes / explicit commit
    if has_clear_yes:
        return {"kind": "CLEAR_YES", "target_action_id": current_aid}

    return {"kind": "NEUTRAL", "target_action_id": current_aid}


def detect_confirmation_ask(reply_text: str, session: SessionState) -> dict[str, Any] | None:
    """Detect Condition (a): assistant asked yes/no confirmation about ONE specific action with a real IST due date."""
    if not reply_text or session.phase not in (Phase.RECOMMEND, Phase.COMMIT):
        return None
    clean = reply_text.strip()
    if "?" not in clean:
        return None
    low = clean.lower()
    # Ignore summary/readiness questions that are NOT action-due-date confirmations
    if re.search(r"\b(?:shall i send|send the summary|review next steps|anything else|2 minutes)\b", low):
        return None
    if not re.search(
        r"\b(?:confirm|yes or no|is that right|shall we|will you|can you|do you agree|work for you|lock|kar lenge|theek rahega)\b",
        low,
    ):
        return None

    dates = extract_mentioned_dates(clean, anchor=getattr(session, "anchor_date_ist", None))
    valid_dates = [d for d in dates if d.get("valid_calendar_date") and not d.get("weekday_mismatch")]
    if not valid_dates:
        return None

    target_act = next_pending_action(session)
    if target_act is None and session.action_plan_items:
        target_act = session.action_plan_items[0]
    if target_act is None:
        return {
            "action_id": "ACT-1",
            "due_date": valid_dates[-1]["expected"],
            "action_summary": "Restock priority out-of-stock products",
            "ambiguity_retried": bool(
                session.pending_confirmation and session.pending_confirmation.get("ambiguity_retried")
            ),
        }

    aid = str(target_act.get("action_id") or "ACT-1")
    prev = session.pending_confirmation
    same_action = bool(prev and prev.get("action_id") == aid)
    return {
        "action_id": aid,
        "due_date": str(target_act.get("due_date_ist") or valid_dates[-1]["expected"]),
        "action_summary": str(target_act.get("title") or aid),
        "ambiguity_retried": bool(prev.get("ambiguity_retried")) if same_action else False,
    }


def apply_consent_transition(session: SessionState, user_text: str) -> dict[str, Any]:
    """Evaluate user consent reply against pending confirmation and update action_statuses."""
    res = classify_consent_reply(user_text, session)
    session.last_consent_eval = res
    kind = res["kind"]

    if kind == "EARLY_END":
        session.early_end_requested = True
        session.pending_confirmation = None
        for st in session.action_statuses.values():
            if st.get("status") == "pending":
                st["status"] = "not_discussed"
        return res

    if kind == "SELECTIVE_KEEP":
        keep_ids = set(res.get("keep_action_ids") or [])
        decline_ids = set(res.get("decline_action_ids") or [])
        for aid in decline_ids:
            if aid in session.action_statuses:
                session.action_statuses[aid]["status"] = "declined"
        for aid in keep_ids:
            if aid in session.action_statuses:
                st = session.action_statuses[aid]
                if st.get("status") != "agreed":
                    st["status"] = "agreed"
                    if not any(c.get("action_id") == aid for c in session.commitments):
                        session.commitments.append(
                            {
                                "action_id": aid,
                                "action_summary": st.get("action_summary") or st.get("title") or aid,
                                "owner": st.get("owner") or session.seller_context.profile.seller_name,
                                "due_date": st.get("due_date") or "",
                                "target_metric": st.get("target_metric") or "",
                                "status": "committed",
                            }
                        )
        session.pending_confirmation = None
        return res

    if kind == "DECLINE":
        tid = res.get("target_action_id")
        if tid and tid in session.action_statuses:
            session.action_statuses[tid]["status"] = "declined"
        if session.pending_confirmation and session.pending_confirmation.get("action_id") == tid:
            session.pending_confirmation = None
        return res

    if kind == "AMBIGUOUS_OR_SOFT":
        pending = session.pending_confirmation
        if pending and pending.get("action_id"):
            tid = str(pending["action_id"])
            if pending.get("ambiguity_retried"):
                # Already asked once more -> mark tentative (never agreed) and advance
                if tid in session.action_statuses:
                    session.action_statuses[tid]["status"] = "tentative"
                session.pending_confirmation = None
                res["marked_tentative"] = tid
            else:
                pending["ambiguity_retried"] = True
                res["ask_once_more"] = tid
        return res

    return res


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
        consent_preview = classify_consent_reply(text, session)
        session.last_consent_eval = consent_preview
        if consent_preview["kind"] == "EARLY_END":
            apply_consent_transition(session, text)
            session.phase = Phase.SUMMARY
            return session.phase
        if consent_preview["kind"] in ("SELECTIVE_KEEP", "DECLINE"):
            apply_consent_transition(session, text)
            if all_actions_resolved(session):
                session.phase = Phase.SUMMARY
                return session.phase
            session.phase = Phase.COMMIT
            return session.phase
        if session.recommend_turns >= 1 or is_buy_in(text):
            if session.pending_confirmation and consent_preview["kind"] == "AMBIGUOUS_OR_SOFT":
                session.pending_confirmation["ambiguity_retried"] = True
            session.phase = Phase.COMMIT
        return session.phase

    if p == Phase.COMMIT:
        apply_consent_transition(session, text)
        if session.early_end_requested or all_actions_resolved(session):
            for st in session.action_statuses.values():
                if st.get("status") == "pending":
                    st["status"] = "not_discussed"
            session.phase = Phase.SUMMARY
        return session.phase

    if p == Phase.SUMMARY:
        session.phase = Phase.CLOSE
        return session.phase

    return session.phase


_CLARIFY_TOPIC_POOL: list[tuple[str, str]] = [
    ("restock_delay", "Ask why restocking your out-of-stock products has been delayed (supplier or budget bottleneck)."),
    ("dispatch_cutoff", "Ask whether afternoon dispatch cutoffs or warehouse stock sync caused order cancellations."),
    ("competitor_pricing", "Ask whether competitor price drops impacted orders and if margins allow price matching."),
    ("visibility_catalog", "Ask whether search ads were paused or catalog attributes changed recently."),
]


def already_answered_by_data(
    headline: dict[str, Any],
    diag: dict[str, Any],
) -> list[str]:
    """Return concrete facts already answered in the dataset so CLARIFY never asks redundant questions."""
    m = headline["metrics"]
    answered: list[str] = []
    oos_curr = m["out_of_stock_skus"]["current"]
    if oos_curr is not None and oos_curr > 0:
        answered.append(f"{oos_curr} products are already out of stock (never ask if items went out of stock).")
    asp_curr = m["asp"]["current"]
    if diag.get("asp_flat"):
        answered.append(f"Average price stayed steady around {format_inr(asp_curr)} (never ask if ticket size fell).")
    cancel_curr = m["cancellation_rate_pct"]["current"]
    if cancel_curr is not None:
        answered.append(f"Cancellation rate is {cancel_curr}% (never ask if cancellations happened).")
    return answered


def select_next_clarify_topic(
    session: SessionState,
    headline: dict[str, Any] | None = None,
    diag: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Pick the next unasked non-redundant clarifying topic for CLARIFY (max 3 total)."""
    asked = set(getattr(session, "clarify_topics_asked", None) or [])
    if headline is None:
        headline = diagnosis.compute_headline_metrics(session.seller_context, session.snapshot)
    if diag is None:
        diag = diagnosis.run_diagnosis(session.seller_context, session.snapshot)
    comp = session.seller_context.completeness
    for topic_key, guidance in _CLARIFY_TOPIC_POOL:
        if topic_key in asked:
            continue
        if topic_key == "competitor_pricing" and comp is not None and not comp.pricing_assessable:
            continue
        if topic_key == "visibility_catalog" and comp is not None and not comp.visibility_assessable:
            continue
        if topic_key == "dispatch_cutoff" and comp is not None and not comp.cancellations_assessable:
            continue
        return topic_key, guidance
    return ("operations_general", "Ask one brief question about what operational constraint is delaying recovery.")


def detect_clarify_topic(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Inspect the actual question sentence first so the 1-clause answer acknowledgement doesn't shadow the topic
    sentences = [s.strip() for s in re.split(r"(?<=[.?!\u0964])\s+", raw) if s.strip()]
    q_sents = [s for s in sentences if "?" in s]
    low = (q_sents[-1] if q_sents else raw).lower()
    if re.search(r"\b(?:restock|supplier|lead\s+time|replenish|out[- ]of[- ]stock|went\s+out|out\s+of\s+stock)\b", low):
        return "restock_delay"
    if re.search(r"\b(?:dispatch|cutoff|pickup|sync|sla|cancel|warehouse)\b", low):
        return "dispatch_cutoff"
    if re.search(r"\b(?:price|pricing|competitor|margin|discount)\b", low):
        return "competitor_pricing"
    if re.search(r"\b(?:ads?|sponsored|keyword|impression|visibility|catalog|listing)\b", low):
        return "visibility_catalog"
    if "?" in raw:
        return "operations_general"
    return None


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
                f"({format_inr(fu['sales_gmv_pace'])} monthly pace, OOS {fu['out_of_stock_skus']}, "
                f"cancellations {fu['cancellation_rate_pct']}%), and ask if they have 2 minutes to review remaining blockers."
            )
        if session.mode == "proactive":
            oos_note = f" and {oos_curr} products are out of stock" if oos_curr is not None else ""
            return (
                f"GOAL: Open proactively—state sales moved {sales_pct}% to {format_inr(sales_curr)}{oos_note}, "
                f"and ask if {salutation} has 2 minutes to review this together."
            )
        return f"GOAL: Greet {salutation} warmly and ask how you can help {shop_name} today."

    if p == Phase.UNDERSTAND:
        answered = already_answered_by_data(headline, diag)
        no_repeat = f" ALREADY IN DATA (do NOT ask about these): {'; '.join(answered)}" if answered else ""
        return (
            "GOAL: Empathize sincerely with the revenue decline in one short clause (never defensive), "
            "and ask 1 focused question about supplier restock delays, dispatch timing, or pricing bottlenecks." + no_repeat
        )

    if p == Phase.CLARIFY:
        topic_key, topic_guide = select_next_clarify_topic(session, headline, diag)
        asked_list = list(getattr(session, "clarify_topics_asked", None) or [])
        answered = already_answered_by_data(headline, diag)
        answered_note = f" ALREADY KNOWN (NEVER ask): {'; '.join(answered)}." if answered else ""
        asked_note = f" ALREADY ASKED TOPICS (NEVER repeat): {', '.join(asked_list)}." if asked_list else ""
        return (
            f"GOAL: Clarify root causes (question {session.clarify_questions_asked + 1} of 3 max, topic={topic_key}). "
            "Start with ONE short clause acknowledging the seller's previous answer, then ask ONE focused non-redundant question: "
            f"{topic_guide}{asked_note}{answered_note}"
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
            f"(1) Cite exact ASP {format_inr(asp_curr)} and that {top_text}.{cancel_note} "
            f"(2) Name the secondary cause.{resid_note}{missing_note} Ask if they are ready for the action plan."
        )

    if p == Phase.RECOMMEND:
        pending_act = next_pending_action(session)
        due_hint = pending_act.get("due_date_ist", "Friday 13 Nov") if pending_act else "Friday 13 Nov"
        return (
            "GOAL: Present ONLY the single highest-priority pending action from the pre-loaded action plan in plain conversational prose "
            "(NO bullet lists or numbering; cover 2-3 total actions one per turn across the close; never trim pricing or visibility when present; "
            "say 'subject to eligibility' at most once for promos). "
            f"End with a clear yes/no confirmation question mentioning the code-computed due date ({due_hint})."
        )

    if p == Phase.COMMIT:
        pending_act = next_pending_action(session)
        next_desc = (
            f"Next pending action is {pending_act.get('action_id')}: {pending_act.get('title')} (due {pending_act.get('due_date_ist')})."
            if pending_act
            else "All actions have been reviewed."
        )
        return (
            f"GOAL: Drive ownership one action per turn in plain sentences (NO bullets/numbering). {next_desc} "
            "Record commit_action ONLY if the previous assistant turn asked yes/no for ONE action with an IST due date AND the seller just gave a clear yes. "
            "Treat 'okay sounds good', 'alright', 'yup', 'maybe', 'I'll try' as soft/ambiguous (do NOT call commit_action; ask once more clearly, then move on as tentative). "
            "After handling the current action, present the next pending action with its IST date and ask yes/no."
        )

    if p == Phase.SUMMARY:
        return (
            "GOAL: Call send_call_summary (and schedule_followup if not yet set), then speak a recap <= 80 words total: "
            "up to 3 key issues with numbers, agreed actions with exact IST calendar dates (or ask for explicit confirmation if none agreed), "
            "what was declined or not agreed, and the follow-up date. Never say only 'I have sent the summary'."
        )

    return f"GOAL: Warmly close the call and thank {salutation}."


def build_system_prompt(
    session: SessionState,
    precomputed_context: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> str:
    """Build a compact system prompt parameterized cleanly by session.seller_context."""
    settings = settings or get_settings()
    ctx = session.seller_context
    profile = ctx.profile
    headline = diagnosis.compute_headline_metrics(ctx, session.snapshot)
    diag = diagnosis.run_diagnosis(ctx, session.snapshot)

    diag_ran = (
        session.diagnose_preloaded
        or any(t.get("name") == "run_diagnosis" for t in session.tool_history)
        or session.phase
        in (
            Phase.DIAGNOSE,
            Phase.REVIEW_COMMITMENTS,
            Phase.RECOMMEND,
            Phase.COMMIT,
            Phase.SUMMARY,
            Phase.CLOSE,
        )
    )

    anchor_dt = anchor_now_ist(getattr(session, "anchor_date_ist", None))
    date_anchors = {
        "today_ist": format_ist_date(anchor_dt),
        "due_today": compute_due_date_ist(0, anchor_dt)["due_date_ist"],
        "due_1d": compute_due_date_ist(1, anchor_dt)["due_date_ist"],
        "due_2d": compute_due_date_ist(2, anchor_dt)["due_date_ist"],
        "due_3d": compute_due_date_ist(3, anchor_dt)["due_date_ist"],
        "due_5d": compute_due_date_ist(5, anchor_dt)["due_date_ist"],
        "followup_3d": compute_followup_date_ist(3, anchor_dt),
    }

    dyn_targets = diagnosis.compute_followup_targets(ctx, session.snapshot)
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
        "targets": dyn_targets,
        "missing_signals": (diag.get("missing_signals") or []) if diag_ran else [],
        "drivers_summary": (
            [
                {
                    "driver": d["driver"],
                    "title": d["title"],
                    "assessable": d["assessable"],
                    "share_pct": d["share_pct"],
                    "lost_orders": d["lost_orders"],
                }
                for d in diag.get("drivers", [])
            ]
            if diag_ran
            else []
        ),
    }

    lines = [
        f"You are {agent_name}, a warm, proactive Virtual Key Account Manager speaking with {seller_name} ({salutation}) of {shop_name}{city_part}.",
        _language_rule(session.language, salutation),
        f"CURRENT PHASE: {session.phase.value} | SNAPSHOT: {session.snapshot} | SCENARIO: {profile.scenario_id}",
        f"CURRENT_DATE_IST: {date_anchors['today_ist']} (Asia/Kolkata)",
        "STRICT VOICE RULES:",
        "- Keep replies to 1-2 short sentences (under 35 words; max 45 words; SUMMARY up to 74 words).",
        "- Ask at most ONE question per turn. Vary acknowledgements ('Understood', 'Makes sense', 'I see'; use 'Got it' at most once per 3 turns).",
        "- Address the seller by their real name or 'you' (use a salutation at most once per 3 turns; NEVER say 'Seller Partner' or 'Custom Uploaded Store').",
        "- Spoken register: NEVER say 'ASP', 'SKU', 'OOS', 'funnel', product codes (like 'HK-008'), or '.0%' (say '50%'). Name at most 2 products per turn.",
        "- NO markdown, NO bullet lists, NO emojis, NO headings, NO internal reasoning.",
        "- NEVER invent or calculate numbers; speak ONLY numbers present in BASELINE SNAPSHOT or VERIFIED DATA CONTEXT.",
        "- Quote outcome projections (like projected_oos_after_restock) ONLY from VERIFIED DATA CONTEXT['projections']; never invent post-action counts.",
        "- NEVER invent dates or weekdays; use ONLY code-computed IST dates from DATE_ANCHORS or VERIFIED DATA CONTEXT.",
        "- Format ALL rupee and large numbers using Indian digit grouping (e.g. Rs 1,35,000 and Rs 2,00,000, never Western 135,000).",
        "- Keep recommendations marketplace-agnostic: never claim guaranteed outcomes or real marketplace program names; say 'subject to eligibility' at most once per call.",
        "- If a driver or metric is missing from the uploaded file, explicitly say it is 'not assessable with the data provided' and never guess.",
        "- If seller is angry or off-topic, empathize briefly and steer back to recovering their sales.",
    ]
    if not diag_ran:
        lines.append(
            "- CRITICAL PRE-DIAGNOSIS RULE: Diagnosis has NOT run yet. In UNDERSTAND and CLARIFY you may ONLY restate headline numbers "
            "(sales change, orders). NEVER state or guess root causes (never say 'mainly due to...', 'driven by...', 'caused by...')."
        )
    lines.extend(
        [
            _phase_instruction(session, headline, diag),
            "DATE_ANCHORS: " + json.dumps(date_anchors, ensure_ascii=False),
            "BASELINE SNAPSHOT: " + json.dumps(baseline_snapshot, ensure_ascii=False),
        ]
    )
    next_act = next_pending_action(session)
    action_plan_state = {
        "pending_confirmation": session.pending_confirmation,
        "last_consent_eval": getattr(session, "last_consent_eval", None) or {},
        "next_pending_action": (
            {
                "action_id": next_act.get("action_id"),
                "action_type": next_act.get("action_type"),
                "title": next_act.get("title"),
                "target_sku_names": (next_act.get("target_sku_names") or [])[:2],
                "due_date_ist": next_act.get("due_date_ist"),
                "estimated_recovery_range_rs": next_act.get("estimated_recovery_range_rs"),
            }
            if next_act
            else None
        ),
        "actions": [
            {
                "action_id": a.get("action_id"),
                "action_type": a.get("action_type"),
                "title": a.get("title"),
                "target_sku_names": (a.get("target_sku_names") or [])[:2],
                "due_date_ist": a.get("due_date_ist"),
                "estimated_recovery_range_rs": a.get("estimated_recovery_range_rs"),
                "status": ((getattr(session, "action_statuses", None) or {}).get(str(a.get("action_id") or "")) or {}).get("status", "pending"),
            }
            for a in (getattr(session, "action_plan_items", None) or [])
        ],
        "action_statuses": {
            aid: {
                "action_id": aid,
                "action_summary": st.get("action_summary"),
                "due_date": st.get("due_date"),
                "status": st.get("status"),
            }
            for aid, st in (getattr(session, "action_statuses", None) or {}).items()
        },
        "all_resolved": all_actions_resolved(session),
    }
    next_topic_key, next_topic_guide = select_next_clarify_topic(session, headline, diag)
    clarify_state = {
        "questions_asked": session.clarify_questions_asked,
        "max_questions": 3,
        "topics_asked": list(getattr(session, "clarify_topics_asked", None) or []),
        "next_topic": next_topic_key,
        "next_topic_guidance": next_topic_guide,
        "already_answered": already_answered_by_data(headline, diag),
    }
    lines.append("CLARIFY_STATE: " + json.dumps(clarify_state, ensure_ascii=False))
    lines.append("ACTION_PLAN_STATE: " + json.dumps(action_plan_state, ensure_ascii=False))
    if session.commitments:
        lines.append("COMMITMENTS LOGGED: " + json.dumps(session.commitments, ensure_ascii=False))
    if session.mode == "followup" and session.prior_commitments:
        lines.append("PRIOR CALL COMMITMENTS: " + json.dumps(session.prior_commitments, ensure_ascii=False))
    if precomputed_context:
        lines.append("VERIFIED DATA CONTEXT (cite directly, do not invent figures): " + json.dumps(precomputed_context, ensure_ascii=False))
    return "\n".join(lines)

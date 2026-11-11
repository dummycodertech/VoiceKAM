"""Agent turn orchestrator: pre-executes DIAGNOSE/RECOMMEND tools, streams SSE tokens/sentences, and audits numbers."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator

from app.agent.session import Phase, SessionState
from app.agent.speakable import (
    audit_projection_claims,
    detect_premature_causal_claims,
    enforce_spoken_cadence_and_register,
    enforce_voice_length,
    extract_numbers_from_obj,
    find_ungrounded_numbers,
    sanitize_premature_causal_claims,
    strip_reasoning_and_markdown,
    to_speakable_text,
)
from app.agent.state_machine import (
    advance_phase_after_turn,
    advance_phase_before_turn,
    all_actions_resolved,
    build_system_prompt,
    detect_clarify_topic,
    detect_confirmation_ask,
    next_pending_action,
    sync_action_plan_to_session,
    tools_for_phase,
)
from app.config import Settings, get_settings
from app.engine import diagnosis as diagnosis_engine
from app.engine.tools import execute_tool
from app.llm.base import CanonicalMessage, ToolCallRequest
from app.llm.router import LLMRouter
from app.utils.dates import anchor_now_ist, audit_dates, compute_followup_date_ist
from app.utils.formatting import format_inr, normalize_indian_grouping

MAX_TOOL_ROUNDS = 2
_SENTENCE_BOUNDARY_RE = re.compile(
    r"(.+?(?:[?!\u0964]|(?<!\d)\.(?!\d)|\.(?=\s)))(?:\s+|$)",
    re.DOTALL,
)


def _sse(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"event": event, "data": data}


def slice_recent_messages(messages: list[CanonicalMessage], max_count: int = 10) -> list[CanonicalMessage]:
    """Slice trailing conversation messages without orphaning tool-call/tool-result pairs or Gemini user-turn ordering."""
    n = len(messages)
    if n == 0:
        return []
    start = max(0, n - max_count)

    # Step backward over tool responses AND assistant(tool_calls) so Gemini always sees
    # a preceding user turn before any model(function_call) turn.
    while start > 0 and (
        messages[start].role == "tool"
        or (messages[start].role == "assistant" and bool(messages[start].tool_calls))
    ):
        start -= 1

    # Skip corrupted/orphan leading tool results or headless tool-call exchanges without a prior user turn
    while start < n:
        msg = messages[start]
        if msg.role == "tool":
            start += 1
            continue
        if msg.role == "assistant" and msg.tool_calls:
            nxt = start + 1
            if nxt >= n or messages[nxt].role != "tool":
                start += 1
                continue
            if any(m.role == "user" for m in messages[start + 1 :]):
                start += 1
                while start < n and messages[start].role == "tool":
                    start += 1
                continue
        break

    return messages[start:]


def _short_action_label(item: dict[str, Any], language: str = "en-IN") -> str:
    atype = str(item.get("action_type") or "").lower()
    if not atype:
        aid = str(item.get("action_id") or "").upper()
        summary = str(item.get("action_summary") or item.get("title") or "").lower()
        if "restock" in summary or "oos" in summary or aid in ("ACT-1", "ACT-FU-1"):
            atype = "restock"
        elif "pric" in summary or aid in ("ACT-2", "ACT-FU-3"):
            atype = "pricing"
        elif "visib" in summary or "keyword" in summary or "search" in summary or aid == "ACT-4":
            atype = "visibility"
        elif "cancel" in summary or "sync" in summary or "dispatch" in summary or aid in ("ACT-3", "ACT-FU-2"):
            atype = "cancellations"
    if language == "hi-IN":
        mapping = {
            "restock": "आउट-ऑफ-स्टॉक प्रोडक्ट रीस्टॉक करना",
            "pricing": "कीमतें अपडेट करना",
            "visibility": "सर्च विज़िबिलिटी सुधारना",
            "cancellations": "डिस्पैच और स्टॉक सिंक करना",
            "audit": "टॉप प्रोडक्ट ऑडिट करना",
        }
    elif language == "hinglish":
        mapping = {
            "restock": "top products restock karna",
            "pricing": "prices align karna",
            "visibility": "search visibility sudharna",
            "cancellations": "stock sync aur dispatch",
            "audit": "top listings audit",
        }
    else:
        mapping = {
            "restock": "restocking top products",
            "pricing": "updating pricing",
            "visibility": "improving search visibility",
            "cancellations": "syncing stock and dispatch",
            "audit": "auditing top listings",
        }
    return mapping.get(atype, str(item.get("action_summary") or item.get("title") or "action")[:40])


def _clean_salutation(session: SessionState) -> str:
    raw = (session.seller_context.profile.salutation or "").strip()
    if not raw or raw.lower() in ("seller partner", "custom uploaded store"):
        return ""
    return raw


def _build_spoken_summary_recap(session: SessionState) -> str:
    ctx = session.seller_context
    headline = diagnosis_engine.compute_headline_metrics(ctx, session.snapshot)
    diag = diagnosis_engine.run_diagnosis(ctx, session.snapshot)
    m = headline["metrics"]
    sales_curr = int(m["sales_gmv"]["current"])
    sales_pct_raw = abs(float(m["sales_gmv"]["delta_pct"]))
    sales_pct = int(sales_pct_raw) if sales_pct_raw == int(sales_pct_raw) else sales_pct_raw
    asp_curr = int(m["asp"]["current"])
    oos_curr = m["out_of_stock_skus"]["current"]
    cancel_curr = m["cancellation_rate_pct"]["current"]
    pricing = diagnosis_engine.get_pricing_gaps(ctx, session.snapshot)
    overpriced_cnt = int(pricing.get("overpriced_sku_count") or 0)

    sal = _clean_salutation(session)
    sal_prefix = f", {sal}" if sal else ""

    issue_bits = [f"sales dropped {sales_pct}% to {format_inr(sales_curr)} with steady {format_inr(asp_curr)} average price"]
    if oos_curr is not None and oos_curr > 0:
        issue_bits.append(f"{oos_curr} out-of-stock products")
    if overpriced_cnt > 0:
        issue_bits.append(f"{overpriced_cnt} overpriced products")
    elif cancel_curr is not None and len(issue_bits) < 3:
        c_val = int(cancel_curr) if float(cancel_curr) == int(cancel_curr) else cancel_curr
        issue_bits.append(f"{c_val}% cancellations")

    card = session.summary_card or {}
    agreed = card.get("agreed_actions") if isinstance(card.get("agreed_actions"), list) else session.commitments
    tentative = card.get("tentative_actions") or []
    declined = card.get("declined_actions") or []
    not_disc = card.get("not_discussed_actions") or []

    anchor_dt = anchor_now_ist(getattr(session, "anchor_date_ist", None))
    fu_date = (
        (session.followup_schedule or {}).get("followup_date")
        or (card.get("followup") or {}).get("followup_date")
        or compute_followup_date_ist(offset_days=3, anchor=anchor_dt)
    )

    if agreed:
        agreed_parts = [f"{_short_action_label(a, 'en-IN')} by {a.get('due_date', '')}".strip() for a in agreed[:3]]
        agreed_txt = f"Agreed: {', '.join(agreed_parts)}."
    else:
        agreed_txt = "Agreed actions: none yet."

    not_agreed_items = [*declined, *tentative, *not_disc]
    if not_agreed_items and len(agreed_parts if agreed else []) < 3:
        na_labels = [_short_action_label(a, "en-IN") for a in not_agreed_items[:2]]
        not_agreed_txt = f" Not agreed: {', '.join(na_labels)}."
    elif declined:
        na_labels = [_short_action_label(a, "en-IN") for a in declined[:2]]
        not_agreed_txt = f" Declined: {', '.join(na_labels)}."
    else:
        not_agreed_txt = ""

    confirm_tail = (
        " Before we close, please confirm with a clear yes which action you want to lock?"
        if not agreed
        else ""
    )

    recap = (
        f"Recap{sal_prefix}: {', '.join(issue_bits[:3])}. "
        f"{agreed_txt}{not_agreed_txt} Follow-up is {fu_date}.{confirm_tail}"
    )
    return normalize_indian_grouping(re.sub(r"\s+", " ", recap).strip())


def _is_summary_recap_compliant(session: SessionState, text: str) -> bool:
    clean = (text or "").strip()
    if not clean:
        return False
    low = clean.lower()
    words = clean.split()
    if len(words) > 75:
        return False
    if re.search(r"\b(?:i have sent|sent your call summary|summary bhej)\b", low) and not re.search(r"\d", clean):
        return False
    # Must contain at least two distinct grounded numbers (issue numbers + dates)
    if len(re.findall(r"\d+", clean)) < 2:
        return False
    # Must include follow-up mention/date
    if "follow" not in low and "फ़ॉलो" not in low:
        return False
    if not session.commitments:
        if "?" not in clean and "confirm" not in low:
            return False
    else:
        # Agreed date must appear in spoken recap
        agreed_dates = [str(c.get("due_date") or "") for c in session.commitments if c.get("due_date")]
        if agreed_dates:
            first_day = re.search(r"\b\d{1,2}\s+[A-Za-z]{3}\b", agreed_dates[0])
            if first_day and first_day.group(0).lower() not in low:
                return False
    return True


def _localized_empty_fallback(session: SessionState) -> str:
    sal = _clean_salutation(session)
    sal_comma = f", {sal}" if sal else ""
    lang = session.language
    if session.commitments and session.phase == Phase.COMMIT:
        if lang == "hi-IN":
            return f"जी{sal_comma}, मैंने आपका एक्शन और समय दर्ज कर लिया है। क्या मैं समरी भेज दूँ?"
        if lang == "hinglish":
            return f"Ji{sal_comma}, maine aapka action aur date note kar li hai. Kya main call summary bhej doon?"
        return f"Noted{sal_comma}. I have logged that action with its date. Shall I send the summary?"
    if session.summary_card and session.phase == Phase.SUMMARY:
        return _build_spoken_summary_recap(session)
    if lang == "hi-IN":
        return f"जी{sal_comma}, कृपया इस एक्शन की समय-सीमा पर हाँ या ना बताइए?"
    if lang == "hinglish":
        return f"Ji{sal_comma}, kya aap is action aur timeline ke liye haan bolenge?"
    return f"Understood{sal_comma}. Could you confirm yes or no on this action timeline?"


def _compact_tool_payload(name: str, res: dict[str, Any]) -> dict[str, Any]:
    """Trim verbose fields before passing pre-executed context to the LLM to preserve TPM."""
    if name == "get_out_of_stock_skus":
        return {
            "total_oos_count": res.get("total_oos_count"),
            "newly_oos_count": res.get("newly_oos_count"),
            "total_oos_lost_orders": res.get("total_oos_lost_orders"),
            "total_oos_lost_revenue": res.get("total_oos_lost_revenue"),
            "top_3_oos": [
                {
                    "sku_id": item["sku_id"],
                    "name": item["name"],
                    "price": item["price"],
                    "lost_orders": item["lost_orders"],
                    "lost_rev_per_day": item["estimated_lost_rev_per_day"],
                }
                for item in (res.get("top_oos_skus") or [])[:3]
            ],
            "remaining_priority_blocker": res.get("remaining_priority_blocker"),
        }
    if name == "get_pricing_gaps":
        return {
            "overpriced_sku_count": res.get("overpriced_sku_count"),
            "total_pricing_lost_orders": res.get("total_pricing_lost_orders"),
            "total_pricing_lost_revenue": res.get("total_pricing_lost_revenue"),
            "skus": [
                {
                    "sku_id": g["sku_id"],
                    "name": g["name"],
                    "seller_price": g["seller_price"],
                    "comp_lowest": g["competitor_lowest_price"],
                    "gap_pct": g["price_gap_pct"],
                }
                for g in (res.get("pricing_gaps") or [])
            ],
            "remaining_overpriced_sku": res.get("remaining_overpriced_sku"),
        }
    if name == "get_cancellation_breakdown":
        return {
            "baseline_rate_pct": res.get("baseline_cancellation_rate_pct"),
            "current_rate_pct": res.get("current_cancellation_rate_pct"),
            "baseline_count": res.get("baseline_cancelled_orders"),
            "current_count": res.get("current_cancelled_orders"),
            "seller_stock_unavail": next(
                (r for r in (res.get("reasons") or []) if r["reason_key"] == "seller_stock_unavailable"),
                None,
            ),
            "dispatch_sla": next(
                (r for r in (res.get("reasons") or []) if r["reason_key"] == "dispatch_sla_breach"),
                None,
            ),
            "followup_rate_pct": res.get("followup_cancellation_rate_pct"),
        }
    if name == "get_visibility_funnel":
        return {
            "last_month": res.get("last_month"),
            "this_month": res.get("this_month"),
            "impressions_pct": (res.get("changes") or {}).get("impressions_pct"),
        }
    return res


class AgentOrchestrator:
    """Runs turns with deterministic tool pre-execution, streaming SSE, and failover."""

    def __init__(self, router: LLMRouter, settings: Settings | None = None) -> None:
        self.router = router
        self.settings = settings

    async def _run_pre_tools(
        self,
        session: SessionState,
        tool_specs: list[tuple[str, dict[str, Any]]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Execute deterministic tools concurrently and emit live KAM Brain tool events."""
        events: list[dict[str, Any]] = []
        combined: dict[str, Any] = {}

        async def _exec_one(tname: str, targs: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any], int]:
            t0 = time.perf_counter()
            res = await asyncio.to_thread(execute_tool, tname, targs, session)
            ms = round((time.perf_counter() - t0) * 1000, 2)
            return tname, targs, res, ms

        for tname, targs in tool_specs:
            start_payload = {"name": tname, "arguments": targs, "source": "server_preexec"}
            events.append(_sse("tool_start", start_payload))
            events.append(_sse("tool_call", start_payload))

        results = await asyncio.gather(*[_exec_one(n, a) for n, a in tool_specs])
        for tname, targs, res, ms in results:
            session.grounded_numbers.update(extract_numbers_from_obj(res))
            if tname == "build_action_plan":
                sync_action_plan_to_session(session, res)
                if isinstance(res.get("projections"), dict):
                    session.latest_projections.update(res["projections"])
            elif tname == "get_out_of_stock_skus" and "projected_oos_after_restock" in res:
                session.latest_projections.setdefault(
                    "projected_oos_after_restock", res.get("projected_oos_after_restock")
                )
                session.latest_projections.setdefault("current_oos_count", res.get("total_oos_count"))
                session.latest_projections.setdefault("planned_restock_count", res.get("planned_restock_count"))
            record = {
                "name": tname,
                "arguments": targs,
                "result": res,
                "latency_ms": ms,
                "source": "server_preexec",
            }
            session.tool_history.append(record)
            combined[tname] = _compact_tool_payload(tname, res)
            events.append(
                _sse(
                    "tool_result",
                    {"name": tname, "result": res, "latency_ms": ms, "source": "server_preexec"},
                )
            )

        return combined, events

    async def _pre_execute_for_phase(self, session: SessionState) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        p = session.phase
        if p == Phase.GREETING and session.mode in ("proactive", "followup"):
            return await self._run_pre_tools(
                session,
                [("get_seller_overview", {}), ("get_metric_comparison", {})],
            )
        if p == Phase.DIAGNOSE and not session.diagnose_preloaded:
            session.diagnose_preloaded = True
            return await self._run_pre_tools(
                session,
                [
                    ("run_diagnosis", {}),
                    ("get_out_of_stock_skus", {"limit": 5}),
                    ("get_cancellation_breakdown", {}),
                    ("get_pricing_gaps", {}),
                    ("get_visibility_funnel", {}),
                ],
            )
        if p == Phase.REVIEW_COMMITMENTS and not session.diagnose_preloaded:
            session.diagnose_preloaded = True
            return await self._run_pre_tools(
                session,
                [
                    ("run_diagnosis", {}),
                    ("get_metric_comparison", {}),
                    ("get_out_of_stock_skus", {"limit": 3}),
                    ("get_pricing_gaps", {}),
                ],
            )
        if p == Phase.RECOMMEND and not session.recommend_preloaded:
            session.recommend_preloaded = True
            return await self._run_pre_tools(session, [("build_action_plan", {})])
        if p in (Phase.COMMIT, Phase.SUMMARY) and not session.action_plan_items:
            plan = await asyncio.to_thread(
                diagnosis_engine.build_action_plan,
                session.seller_context,
                session.snapshot,
                anchor_now_ist(getattr(session, "anchor_date_ist", None)),
            )
            sync_action_plan_to_session(session, plan)
            session.grounded_numbers.update(extract_numbers_from_obj(plan))
            if isinstance(plan.get("projections"), dict):
                session.latest_projections.update(plan["projections"])
        return None, []

    async def start_call(self, session: SessionState) -> AsyncIterator[dict[str, Any]]:
        """Emit the opening greeting SSE stream when a call begins."""
        yield _sse("phase", {"phase": session.phase.value, "mode": session.mode})

        if session.mode == "inbound":
            # Instant zero-LLM greeting for Inbound mode (low latency, warm persona)
            settings = self.settings or get_settings()
            salutation = session.seller_context.profile.salutation
            shop_name = session.seller_context.profile.shop_name
            if session.language == "hi-IN":
                greeting = (
                    f"नमस्ते {salutation}, मैं {settings.agent_name_devanagari} हूँ, आपकी मार्केटप्लेस अकाउंट मैनेजर। "
                    "आज मैं आपकी क्या मदद कर सकती हूँ?"
                )
            elif session.language == "hinglish":
                greeting = (
                    f"Namaste {salutation}, main {settings.agent_name} bol rahi hoon, aapki marketplace account manager. "
                    "Aaj main aapki kya help kar sakti hoon?"
                )
            else:
                greeting = (
                    f"Namaste {salutation}, I am {settings.agent_name}, your marketplace account manager. "
                    f"How can I help {shop_name} today?"
                )

            greeting = enforce_spoken_cadence_and_register(
                greeting, session=session, turn_count=session.turn_count
            )
            session.messages.append(CanonicalMessage(role="assistant", content=greeting))
            yield _sse("token", {"text": greeting})
            yield _sse(
                "sentence",
                {
                    "text": greeting,
                    "speakable": to_speakable_text(
                        greeting,
                        session.language,
                        seller_context=session.seller_context,
                        settings=settings,
                    ),
                },
            )
            yield _sse(
                "done",
                {
                    "text": greeting,
                    "speakable": to_speakable_text(
                        greeting,
                        session.language,
                        seller_context=session.seller_context,
                        settings=settings,
                    ),
                    "phase": session.phase.value,
                    "provider": "template",
                },
            )
            return

        # Proactive or Follow-up mode: pre-execute overview/metrics & stream LLM opening
        async for ev in self._stream_agent_response(session):
            yield ev

    async def run_turn(self, session: SessionState, user_text: str) -> AsyncIterator[dict[str, Any]]:
        """Handle one user utterance/message through phase update, tools, LLM stream, and grounding audit."""
        clean_user = user_text.strip()
        if not clean_user:
            return

        session.turn_count += 1
        session.messages.append(CanonicalMessage(role="user", content=clean_user))
        prev_phase = session.phase
        advance_phase_before_turn(session, clean_user)
        yield _sse("phase", {"phase": session.phase.value, "mode": session.mode})
        if session.phase != prev_phase:
            yield _sse(
                "phase_change",
                {
                    "phase": session.phase.value,
                    "previous_phase": prev_phase.value,
                    "mode": session.mode,
                },
            )

        async for ev in self._stream_agent_response(session):
            yield ev

    async def _stream_agent_response(self, session: SessionState) -> AsyncIterator[dict[str, Any]]:
        t_start = time.perf_counter()
        first_token_ms: int | None = None
        llm_requests = 0

        pre_ctx, pre_events = await self._pre_execute_for_phase(session)
        for ev in pre_events:
            yield ev

        # Keep recent history compact (last ~10 non-system messages) without breaking tool sequences
        recent_history = slice_recent_messages(session.messages, max_count=10)
        sys_prompt = build_system_prompt(session, precomputed_context=pre_ctx, settings=self.settings)
        working_messages = [CanonicalMessage(role="system", content=sys_prompt), *recent_history]
        phase_tools = tools_for_phase(session.phase)

        final_text = ""
        provider_used = session.active_provider

        for round_idx in range(MAX_TOOL_ROUNDS):
            llm_requests += 1
            round_text_parts: list[str] = []
            sentence_buf = ""
            pending_tool_calls: list[ToolCallRequest] = []

            async for chunk, failovers in self.router.stream_turn_with_failover(
                messages=working_messages,
                tools=phase_tools,
                temperature=0.25,
                max_tokens=550,
            ):
                if failovers:
                    for fo in failovers:
                        if fo not in session.failover_events:
                            session.failover_events.append(fo)
                            yield _sse("failover", fo)

                if chunk.provider:
                    provider_used = f"{chunk.provider}:{chunk.model}"
                    session.active_provider = provider_used

                if chunk.type == "token" and chunk.text:
                    if first_token_ms is None:
                        first_token_ms = round((time.perf_counter() - t_start) * 1000)
                    round_text_parts.append(chunk.text)
                    sentence_buf += chunk.text
                    yield _sse("token", {"text": chunk.text})

                    # Stream complete sentences early so TTS fetches N+1 while N plays
                    while True:
                        match = _SENTENCE_BOUNDARY_RE.match(sentence_buf)
                        if not match:
                            break
                        sent = normalize_indian_grouping(strip_reasoning_and_markdown(match.group(1)))
                        sentence_buf = sentence_buf[match.end() :]
                        if sent and re.search(r"[A-Za-z0-9\u0900-\u097F]", sent):
                            yield _sse(
                                "sentence",
                                {
                                    "text": sent,
                                    "speakable": to_speakable_text(
                                        sent,
                                        session.language,
                                        seller_context=session.seller_context,
                                        settings=self.settings,
                                    ),
                                },
                            )

                elif chunk.type == "tool_calls" and chunk.tool_calls:
                    pending_tool_calls.extend(chunk.tool_calls)

            # Flush trailing fragment if no tool calls remain
            rem = normalize_indian_grouping(strip_reasoning_and_markdown(sentence_buf))
            if rem and not pending_tool_calls and re.search(r"[A-Za-z0-9\u0900-\u097F]", rem):
                yield _sse(
                    "sentence",
                    {
                        "text": rem,
                        "speakable": to_speakable_text(
                            rem,
                            session.language,
                            seller_context=session.seller_context,
                            settings=self.settings,
                        ),
                    },
                )

            round_text = "".join(round_text_parts).strip()

            if pending_tool_calls and round_idx + 1 < MAX_TOOL_ROUNDS:
                assistant_tc_msg = CanonicalMessage(
                    role="assistant",
                    content=round_text,
                    tool_calls=pending_tool_calls,
                )
                working_messages.append(assistant_tc_msg)
                session.messages.append(assistant_tc_msg)

                for tc in pending_tool_calls:
                    tc_payload = {"name": tc.name, "arguments": tc.arguments, "source": "llm"}
                    yield _sse("tool_start", tc_payload)
                    yield _sse("tool_call", tc_payload)

                async def _exec_tc(tc: ToolCallRequest):
                    t0 = time.perf_counter()
                    res = await asyncio.to_thread(execute_tool, tc.name, tc.arguments, session)
                    ms = round((time.perf_counter() - t0) * 1000, 2)
                    return tc, res, ms

                tc_results = await asyncio.gather(*[_exec_tc(tc) for tc in pending_tool_calls])
                for tc, res, ms in tc_results:
                    session.grounded_numbers.update(extract_numbers_from_obj(res))
                    if tc.name == "build_action_plan":
                        sync_action_plan_to_session(session, res)
                        if isinstance(res.get("projections"), dict):
                            session.latest_projections.update(res["projections"])
                    elif tc.name == "get_out_of_stock_skus" and "projected_oos_after_restock" in res:
                        session.latest_projections.setdefault(
                            "projected_oos_after_restock", res.get("projected_oos_after_restock")
                        )
                        session.latest_projections.setdefault("current_oos_count", res.get("total_oos_count"))
                        session.latest_projections.setdefault("planned_restock_count", res.get("planned_restock_count"))
                    session.tool_history.append(
                        {
                            "name": tc.name,
                            "arguments": tc.arguments,
                            "result": res,
                            "latency_ms": ms,
                            "source": "llm",
                        }
                    )
                    yield _sse(
                        "tool_result",
                        {"name": tc.name, "result": res, "latency_ms": ms, "source": "llm"},
                    )
                    if tc.name == "send_call_summary" and "summary_card" in res:
                        yield _sse("summary_card", res["summary_card"])

                    tool_msg = CanonicalMessage(
                        role="tool",
                        content=json.dumps(res, ensure_ascii=False),
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                    )
                    working_messages.append(tool_msg)
                    session.messages.append(tool_msg)

                if (
                    session.phase == Phase.COMMIT
                    and all_actions_resolved(session)
                    and session.action_plan_items
                ):
                    session.phase = Phase.SUMMARY
                    yield _sse("phase", {"phase": session.phase.value, "mode": session.mode})
                    yield _sse(
                        "phase_change",
                        {
                            "phase": session.phase.value,
                            "previous_phase": Phase.COMMIT.value,
                            "mode": session.mode,
                        },
                    )
                    if session.summary_card is None:
                        res_sum = await asyncio.to_thread(execute_tool, "send_call_summary", {}, session)
                        session.grounded_numbers.update(extract_numbers_from_obj(res_sum))
                        session.tool_history.append(
                            {
                                "name": "send_call_summary",
                                "arguments": {},
                                "result": res_sum,
                                "latency_ms": 0.1,
                                "source": "server_fallback",
                            }
                        )
                        fb_p = {"name": "send_call_summary", "arguments": {}, "source": "server_fallback"}
                        yield _sse("tool_start", fb_p)
                        yield _sse("tool_call", fb_p)
                        yield _sse(
                            "tool_result",
                            {"name": "send_call_summary", "result": res_sum, "latency_ms": 0.1, "source": "server_fallback"},
                        )
                        if "summary_card" in res_sum:
                            yield _sse("summary_card", res_sum["summary_card"])
                    working_messages[0] = CanonicalMessage(
                        role="system",
                        content=build_system_prompt(session, precomputed_context=pre_ctx, settings=self.settings),
                    )
                    phase_tools = []
                continue

            final_text = round_text
            break

        # Ensure SUMMARY phase always generates a summary_card even if the LLM skipped calling send_call_summary
        if session.phase == Phase.SUMMARY and session.summary_card is None:
            res = await asyncio.to_thread(execute_tool, "send_call_summary", {}, session)
            session.grounded_numbers.update(extract_numbers_from_obj(res))
            session.tool_history.append(
                {
                    "name": "send_call_summary",
                    "arguments": {},
                    "result": res,
                    "latency_ms": 0.1,
                    "source": "server_fallback",
                }
            )
            fb_payload = {"name": "send_call_summary", "arguments": {}, "source": "server_fallback"}
            yield _sse("tool_start", fb_payload)
            yield _sse("tool_call", fb_payload)
            yield _sse(
                "tool_result",
                {"name": "send_call_summary", "result": res, "latency_ms": 0.1, "source": "server_fallback"},
            )
            if "summary_card" in res:
                yield _sse("summary_card", res["summary_card"])

        if session.summary_card:
            session.grounded_numbers.update(extract_numbers_from_obj(session.summary_card))

        # Guard Item 8 + enforce voice word-count cap & clean text with Indian digit grouping
        diag_has_run = (
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
        cleaned_reply = normalize_indian_grouping(
            enforce_voice_length(final_text, session.phase.value, max_words=45)
        )
        causal_hits: list[str] = []
        if not diag_has_run and cleaned_reply:
            causal_hits = detect_premature_causal_claims(cleaned_reply)
            if causal_hits:
                session.causal_warnings.append(
                    {
                        "phase": session.phase.value,
                        "claims": causal_hits,
                        "reply": cleaned_reply,
                    }
                )
                hm = diagnosis_engine.compute_headline_metrics(session.seller_context, session.snapshot)
                cleaned_reply = normalize_indian_grouping(
                    sanitize_premature_causal_claims(
                        cleaned_reply,
                        sales_delta_pct=hm["metrics"]["sales_gmv"]["delta_pct"],
                        sales_curr=hm["metrics"]["sales_gmv"]["current"],
                        salutation=session.seller_context.profile.salutation,
                        language=session.language,
                    )
                )
        if session.phase == Phase.SUMMARY and not _is_summary_recap_compliant(session, cleaned_reply):
            was_empty = not cleaned_reply
            cleaned_reply = normalize_indian_grouping(
                enforce_voice_length(_build_spoken_summary_recap(session), "SUMMARY", max_words=74)
            )
            if was_empty:
                yield _sse("token", {"text": cleaned_reply})
            yield _sse(
                "sentence",
                {
                    "text": cleaned_reply,
                    "speakable": to_speakable_text(
                        cleaned_reply,
                        session.language,
                        seller_context=session.seller_context,
                        settings=self.settings,
                    ),
                },
            )
        elif not cleaned_reply:
            cleaned_reply = normalize_indian_grouping(_localized_empty_fallback(session))
            yield _sse("token", {"text": cleaned_reply})
            yield _sse(
                "sentence",
                {
                    "text": cleaned_reply,
                    "speakable": to_speakable_text(
                        cleaned_reply,
                        session.language,
                        seller_context=session.seller_context,
                        settings=self.settings,
                    ),
                },
            )
        cleaned_reply = enforce_spoken_cadence_and_register(
            cleaned_reply,
            session=session,
            turn_count=session.turn_count,
        )
        if session.phase in (Phase.UNDERSTAND, Phase.CLARIFY):
            c_topic = detect_clarify_topic(cleaned_reply)
            if c_topic and c_topic not in session.clarify_topics_asked:
                session.clarify_topics_asked.append(c_topic)
        ungrounded = find_ungrounded_numbers(
            cleaned_reply,
            session.grounded_numbers,
            seller_context=session.seller_context,
        )
        if ungrounded:
            session.ungrounded_warnings.append(
                {
                    "phase": session.phase.value,
                    "ungrounded_numbers": ungrounded,
                    "reply": cleaned_reply,
                }
            )

        proj_warns = audit_projection_claims(cleaned_reply, session.latest_projections)
        if proj_warns:
            for pw in proj_warns:
                entry = {"phase": session.phase.value, **pw}
                session.projection_warnings.append(entry)
                session.ungrounded_warnings.append(entry)

        date_warns = audit_dates(
            cleaned_reply,
            anchor=session.anchor_date_ist,
            summary_card=session.summary_card if session.phase in (Phase.SUMMARY, Phase.CLOSE) else None,
        )
        if date_warns:
            for dw in date_warns:
                session.date_warnings.append({"phase": session.phase.value, **dw})

        session.messages.append(CanonicalMessage(role="assistant", content=cleaned_reply))
        session.pending_confirmation = detect_confirmation_ask(cleaned_reply, session)
        prev_after = session.phase
        advance_phase_after_turn(session)
        if session.phase != prev_after:
            yield _sse(
                "phase_change",
                {
                    "phase": session.phase.value,
                    "previous_phase": prev_after.value,
                    "mode": session.mode,
                },
            )

        total_ms = round((time.perf_counter() - t_start) * 1000)
        latency_info = {
            "llm_first_token_ms": first_token_ms if first_token_ms is not None else total_ms,
            "total_turn_ms": total_ms,
            "llm_requests": llm_requests,
            "provider": provider_used,
            "ungrounded_numbers": ungrounded,
            "date_warnings": date_warns,
            "projection_warnings": proj_warns,
            "causal_warnings": causal_hits,
        }
        session.latency_history.append(latency_info)
        yield _sse("latency", latency_info)
        yield _sse(
            "done",
            {
                "text": cleaned_reply,
                "speakable": to_speakable_text(
                    cleaned_reply,
                    session.language,
                    seller_context=session.seller_context,
                    settings=self.settings,
                ),
                "phase": session.phase.value,
                "provider": provider_used,
                "commitments": session.commitments,
                "followup_schedule": session.followup_schedule,
                "summary_card": session.summary_card,
            },
        )


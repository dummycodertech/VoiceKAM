"""Agent turn orchestrator: pre-executes DIAGNOSE/RECOMMEND tools, streams SSE tokens/sentences, and audits numbers."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator

from app.agent.session import Phase, SessionState
from app.agent.speakable import (
    enforce_voice_length,
    extract_numbers_from_obj,
    find_ungrounded_numbers,
    strip_reasoning_and_markdown,
    to_speakable_text,
)
from app.agent.state_machine import (
    advance_phase_after_turn,
    advance_phase_before_turn,
    build_system_prompt,
    tools_for_phase,
)
from app.config import Settings, get_settings
from app.engine.tools import execute_tool
from app.llm.base import CanonicalMessage, ToolCallRequest
from app.llm.router import LLMRouter
from app.utils.formatting import normalize_indian_grouping

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


def _localized_empty_fallback(session: SessionState) -> str:
    salutation = session.seller_context.profile.salutation
    lang = session.language
    if session.commitments and session.phase == Phase.COMMIT:
        if lang == "hi-IN":
            return f"जी {salutation}, मैंने आपका एक्शन प्लान और समय दर्ज कर लिया है। क्या मैं समरी भेज दूँ?"
        if lang == "hinglish":
            return f"Ji {salutation}, maine aapki commitment aur timeline note kar li hai. Kya main call summary bhej doon?"
        return f"Got it, {salutation}. I have noted your commitment with due dates and follow-up targets. Shall I send the summary?"
    if session.summary_card and session.phase == Phase.SUMMARY:
        if lang == "hi-IN":
            return f"मैंने आपकी कॉल समरी, जरूरी एक्शन और फॉलो-अप कॉल का समय भेज दिया है।"
        if lang == "hinglish":
            return "Maine aapki call summary key findings aur follow-up details ke saath bhej di hai."
        return "I have sent your call summary with key issues, action deadlines, and our follow-up check-in."
    if lang == "hi-IN":
        return f"जी {salutation}, कृपया अपनी पसंदीदा समय-सीमा बताइए ताकि मैं एक्शन प्लान तय कर सकूँ।"
    if lang == "hinglish":
        return f"Ji {salutation}, please apni preferred timeline bataiye taaki main action plan lock kar sakoon."
    return f"Got it, {salutation}. Please tell me your preferred timeline so I can lock your action plan."


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

        # Enforce voice word-count cap & clean text with Indian digit grouping
        cleaned_reply = normalize_indian_grouping(
            enforce_voice_length(final_text, session.phase.value, max_words=45)
        )
        if not cleaned_reply:
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

        session.messages.append(CanonicalMessage(role="assistant", content=cleaned_reply))
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


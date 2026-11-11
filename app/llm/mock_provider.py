"""Deterministic mock LLM provider used strictly in automated tests & offline UI checks."""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

from app.llm.base import (
    CanonicalMessage,
    LLMProvider,
    LLMStreamChunk,
    ToolCallRequest,
)
from app.utils.formatting import format_inr


def _extract_json_marker(sys_text: str, prefix: str) -> dict[str, Any]:
    for line in sys_text.splitlines():
        if line.startswith(prefix):
            raw = line[len(prefix) :].strip()
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
    return {}


def _pick_next_action(
    action_state: dict[str, Any],
    recent_tools: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick next unresolved action, accounting for any action committed in Round 1 of this turn."""
    just_committed = None
    ca = recent_tools.get("commit_action") or {}
    if ca.get("recorded") and isinstance(ca.get("commitment"), dict):
        just_committed = str(ca["commitment"].get("action_id") or "")

    actions = action_state.get("actions") or []
    statuses = action_state.get("action_statuses") or {}
    for act in actions:
        aid = str(act.get("action_id") or "")
        if just_committed and aid == just_committed:
            continue
        st = (statuses.get(aid) or {}).get("status") or act.get("status") or "pending"
        if st == "pending":
            return act
    next_act = action_state.get("next_pending_action")
    if next_act and str(next_act.get("action_id") or "") != just_committed:
        return next_act
    return None


def _short_product_list(names: list[str] | None) -> str:
    clean = [str(n).strip() for n in (names or []) if str(n).strip() and not re.match(r"^[A-Z]{2,8}-\d+$", str(n).strip())]
    clean = clean[:2]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    if len(clean) == 1:
        return clean[0]
    return ""


def _format_action_question(
    act: dict[str, Any] | None,
    due_fallback: str,
    prefix: str = "",
    retry: bool = False,
) -> str:
    if not act:
        if retry:
            return f"Just to confirm yes or no: will you restock top out-of-stock products by {due_fallback}?"
        return f"{prefix}Will you restock top out-of-stock products by {due_fallback}, yes or no?".strip()

    atype = str(act.get("action_type") or "").lower()
    due = str(act.get("due_date_ist") or due_fallback)
    prod_txt = _short_product_list(act.get("target_sku_names"))
    if atype == "restock":
        item = prod_txt or "your priority out-of-stock items"
        core = f"will you restock {item} by {due}"
    elif atype == "pricing":
        item = prod_txt or "your overpriced items"
        core = f"will you update prices on {item} by {due}"
    elif atype == "visibility":
        core = f"can you refresh search keywords on in-stock listings by {due}"
    elif atype == "cancellations":
        core = f"can you sync inventory twice daily before dispatch cutoff by {due}"
    else:
        item = prod_txt or "top declining items"
        core = f"can you review {item} by {due}"

    if retry:
        return f"Just to be clear, yes or no: {core}?"
    if prefix:
        return f"{prefix} {core[0].upper() + core[1:]} — yes or no?"
    return f"Just to confirm: {core}, yes or no?"


def _clean_pct_str(val: Any) -> str:
    try:
        f = abs(float(val))
    except (TypeError, ValueError):
        return "0"
    if f == int(f):
        return str(int(f))
    return f"{round(f, 1)}".rstrip("0").rstrip(".")


class MockLLMProvider(LLMProvider):
    """Rule-based test LLM parameterized dynamically by SellerContext in the system prompt."""

    provider_name = "mock"
    model_name = "deterministic-test-kam"

    def is_configured(self) -> bool:
        return True

    async def stream_turn(
        self,
        messages: list[CanonicalMessage],
        tools: list[dict[str, Any]],
        temperature: float = 0.25,
        max_tokens: int = 220,
    ) -> AsyncIterator[LLMStreamChunk]:
        sys_text = "\n".join(m.content for m in messages if m.role == "system")
        phase_match = re.search(r"CURRENT PHASE:\s*([A-Z_]+)", sys_text)
        phase = phase_match.group(1) if phase_match else "UNDERSTAND"
        is_followup = "SNAPSHOT: followup" in sys_text
        lang = "en-IN"
        if "LANGUAGE: hi-IN" in sys_text:
            lang = "hi-IN"
        elif "LANGUAGE: hinglish" in sys_text:
            lang = "hinglish"

        snap = _extract_json_marker(sys_text, "BASELINE SNAPSHOT:")
        date_anchors = _extract_json_marker(sys_text, "DATE_ANCHORS:")
        action_state = _extract_json_marker(sys_text, "ACTION_PLAN_STATE:")
        pre_ctx = _extract_json_marker(sys_text, "VERIFIED DATA CONTEXT (cite directly, do not invent figures):")

        available_tools = {t["function"]["name"] for t in tools}
        last_user = ""
        for m in reversed(messages):
            if m.role == "user":
                last_user = m.content.lower()
                break

        recent_tools: dict[str, dict[str, Any]] = {}
        for m in reversed(messages):
            if m.role == "user":
                break
            if m.role == "tool" and m.tool_name:
                try:
                    recent_tools[m.tool_name] = json.loads(m.content)
                except Exception:
                    recent_tools[m.tool_name] = {}

        seller_name = str(snap.get("seller_name") or "Seller Partner")
        dyn_targets = snap.get("targets") or {}
        due_2d = str(date_anchors.get("due_2d") or "Friday 13 Nov")
        followup_3d = str(date_anchors.get("followup_3d") or "Saturday 14 Nov at 11:00 AM IST")

        if not recent_tools:
            calls: list[ToolCallRequest] = []
            if phase in ("GREETING", "UNDERSTAND") and "get_seller_overview" in available_tools:
                calls.append(ToolCallRequest(id="m1", name="get_seller_overview", arguments={}))
            elif phase == "REVIEW_COMMITMENTS" and "run_diagnosis" in available_tools:
                calls.append(ToolCallRequest(id="m1", name="run_diagnosis", arguments={}))
                if "get_metric_comparison" in available_tools:
                    calls.append(ToolCallRequest(id="m2", name="get_metric_comparison", arguments={}))
            elif phase == "DIAGNOSE":
                for idx, tname in enumerate(
                    ["run_diagnosis", "get_out_of_stock_skus", "get_cancellation_breakdown", "get_pricing_gaps"],
                    1,
                ):
                    if tname in available_tools:
                        args = {"limit": 5} if tname == "get_out_of_stock_skus" else {}
                        calls.append(ToolCallRequest(id=f"m{idx}", name=tname, arguments=args))
            elif phase == "RECOMMEND" and "build_action_plan" in available_tools:
                calls.append(ToolCallRequest(id="m1", name="build_action_plan", arguments={}))
            elif phase == "COMMIT":
                last_consent = action_state.get("last_consent_eval") or {}
                pending_conf = action_state.get("pending_confirmation")
                consent_kind = last_consent.get("kind")
                if (
                    "commit_action" in available_tools
                    and consent_kind in ("CLEAR_YES", "SELECTIVE_KEEP")
                    and pending_conf
                ):
                    oos_target = dyn_targets.get("out_of_stock_skus_max")
                    if oos_target is None:
                        oos_curr = snap.get("oos_curr")
                        oos_target = max(0, int(oos_curr) - min(5, int(oos_curr))) if isinstance(oos_curr, int) else 0
                    cancel_target = dyn_targets.get("cancellation_rate_pct_max")
                    target_list = dyn_targets.get("targets_list") or [
                        f"Out-of-stock products <= {oos_target}",
                        *(
                            [f"Cancellation rate <= {cancel_target}%"]
                            if cancel_target is not None
                            else []
                        ),
                    ]
                    aid = str(pending_conf.get("action_id") or "ACT-1")
                    act_sum = str(pending_conf.get("action_summary") or "Restock priority out-of-stock products")
                    due_act = str(pending_conf.get("due_date") or due_2d)
                    calls.append(
                        ToolCallRequest(
                            id="m1",
                            name="commit_action",
                            arguments={
                                "action_id": aid,
                                "action_summary": act_sum,
                                "owner": seller_name,
                                "due_date": due_act,
                                "target_metric": target_list[0] if target_list else f"Out-of-stock products <= {oos_target}",
                            },
                        )
                    )
                    if "schedule_followup" in available_tools:
                        calls.append(
                            ToolCallRequest(
                                id="m2",
                                name="schedule_followup",
                                arguments={
                                    "followup_date": followup_3d,
                                    "success_targets": target_list,
                                },
                            )
                        )
            elif phase == "SUMMARY" and "send_call_summary" in available_tools:
                issues = self._build_summary_issues(snap)
                calls.append(
                    ToolCallRequest(
                        id="m1",
                        name="send_call_summary",
                        arguments={"key_issues": issues},
                    )
                )

            if calls:
                yield LLMStreamChunk(type="tool_calls", tool_calls=calls, provider=self.provider_name, model=self.model_name)
                yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)
                return

        reply = self._render_reply(
            phase, is_followup, lang, last_user, snap, pre_ctx, recent_tools, date_anchors, action_state
        )
        for word in reply.split(" "):
            if word:
                yield LLMStreamChunk(type="token", text=word + " ", provider=self.provider_name, model=self.model_name)
        yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)

    def _build_summary_issues(self, snap: dict[str, Any]) -> list[str]:
        sales_prev = int(snap.get("sales_prev") or 0)
        sales_curr = int(snap.get("sales_curr") or 0)
        sales_pct = _clean_pct_str(snap.get("sales_delta_pct") or 0.0)
        asp = int(snap.get("asp") or 0)
        drivers = [
            d
            for d in (snap.get("drivers_summary") or [])
            if d.get("assessable") and d.get("driver") != "unexplained_residual" and d.get("lost_orders", 0) > 0
        ]
        issues = [
            f"Sales moved {sales_pct}% ({format_inr(sales_prev)} -> {format_inr(sales_curr)}) while average price stayed flat around {format_inr(asp)}."
        ]
        for d in drivers:
            issues.append(f"{d['title']} accounted for {_clean_pct_str(d['share_pct'])}% ({d['lost_orders']} lost orders).")
        if snap.get("missing_signals"):
            issues.append("Some metrics are not assessable with the data provided.")
        return issues

    def _render_reply(
        self,
        phase: str,
        is_followup: bool,
        lang: str,
        last_user: str,
        snap: dict[str, Any],
        pre_ctx: dict[str, Any],
        recent_tools: dict[str, dict[str, Any]],
        date_anchors: dict[str, Any] | None = None,
        action_state: dict[str, Any] | None = None,
    ) -> str:
        date_anchors = date_anchors or {}
        action_state = action_state or {}
        due_2d = str(date_anchors.get("due_2d") or "Friday 13 Nov")
        followup_3d = str(date_anchors.get("followup_3d") or "Saturday 14 Nov at 11:00 AM IST")
        raw_sal = str(snap.get("salutation") or "").strip()
        salutation = "" if raw_sal.lower() in ("seller partner", "custom uploaded store") else raw_sal
        sal_comma = f", {salutation}" if salutation else ""
        sal_lead = f"{salutation}, " if salutation else ""
        shop_name = str(snap.get("shop_name") or "your store")
        if shop_name.lower() == "custom uploaded store":
            shop_name = "your store"
        sales_curr = int(snap.get("sales_curr") or 0)
        sales_pct = _clean_pct_str(snap.get("sales_delta_pct") or 0.0)
        orders_drop = int(snap.get("orders_drop") or 0)
        asp = int(snap.get("asp") or 0)
        oos_curr = snap.get("oos_curr")
        cancel_curr = snap.get("cancel_curr_pct")
        missing_signals = snap.get("missing_signals") or []
        drivers = [d for d in (snap.get("drivers_summary") or []) if d.get("assessable") and d.get("driver") != "unexplained_residual"]
        resid_driver = next((d for d in (snap.get("drivers_summary") or []) if d.get("driver") == "unexplained_residual"), None)
        top_driver = drivers[0] if drivers else None

        if is_followup and phase == "GREETING":
            fu_m = (pre_ctx.get("get_metric_comparison") or {}).get("followup") or {}
            pace = int(fu_m.get("sales_gmv_pace") or sales_curr)
            pace_pct = _clean_pct_str(fu_m.get("sales_vs_crisis_pct", 0))
            fu_oos = fu_m.get("out_of_stock_skus")
            oos_txt = f" and out-of-stock items are {fu_oos}" if fu_oos is not None else ""
            return (
                f"Namaste{sal_comma}, calling for our check-in. "
                f"Your sales pace moved {pace_pct}% to {format_inr(pace)}{oos_txt}. Should we review remaining blockers?"
            )

        if phase == "GREETING":
            oos_txt = f" and {oos_curr} products went out of stock" if oos_curr is not None else ""
            return (
                f"Namaste{sal_comma}, I noticed your sales moved {sales_pct}% to {format_inr(sales_curr)}{oos_txt}. "
                "Do you have 2 minutes to review this together?"
            )

        if phase == "UNDERSTAND":
            if any(w in last_user for w in ("angry", "frustrated", "fed up", "cancel", "waste", "useless", "problem")):
                return f"I understand how frustrating this drop is{sal_comma}. What changed in your operations recently?"
            return f"I understand how worrying this drop is{sal_comma}. What changed in your operations recently?"

        if phase == "CLARIFY":
            if "stock" in last_user or "out" in last_user or "yes" in last_user or "supplier" in last_user:
                return f"Understood{sal_comma}. Were there any dispatch delays or competitor price changes recently?"
            return "I see. Did you notice any supplier restock delays or competitor price changes recently?"

        if phase in ("DIAGNOSE", "REVIEW_COMMITMENTS"):
            if is_followup:
                diag_ctx = pre_ctx.get("run_diagnosis") or {}
                oos_ctx = pre_ctx.get("get_out_of_stock_skus") or {}
                price_ctx = pre_ctx.get("get_pricing_gaps") or {}
                fu_c = diag_ctx.get("followup_comparison", {}).get("metrics") or {}
                rem_oos = oos_ctx.get("remaining_priority_blocker")
                rem_pr = price_ctx.get("remaining_overpriced_sku")
                parts = []
                if fu_c.get("cancellation_rate_pct") is not None:
                    parts.append(f"cancellations are at {_clean_pct_str(fu_c['cancellation_rate_pct'])}%")
                if rem_oos:
                    parts.append(f"{rem_oos['name']} still needs restock")
                if rem_pr:
                    parts.append(f"{rem_pr['name']} is {_clean_pct_str(rem_pr['gap_pct'])}% above competitors")
                detail = ", ".join(parts) or "several actions improved performance"
                return f"Great progress{sal_comma}: {detail}. Shall we close the remaining items?"

            if missing_signals:
                summary_bit = (
                    f"roughly {_clean_pct_str(top_driver['share_pct'])}% ({top_driver['lost_orders']} of {orders_drop} lost orders) stems from {top_driver['title']}"
                    if top_driver
                    else f"{orders_drop} orders were lost"
                )
                return (
                    f"{sal_lead}your average price is {format_inr(asp)} and {summary_bit}. "
                    "Optional missing columns are not assessable with the data provided. Ready for the action plan?"
                )

            top_text = (
                f"roughly {_clean_pct_str(top_driver['share_pct'])}% of the {orders_drop}-order drop comes from {top_driver['title']}"
                if top_driver
                else f"{orders_drop} fewer orders overall"
            )
            resid_txt = (
                f" About {_clean_pct_str(resid_driver['share_pct'])}% ({resid_driver['lost_orders']} orders) remains unexplained residual."
                if resid_driver and float(resid_driver.get("share_pct") or 0) >= 10.0
                else ""
            )
            cancel_txt = f", with {_clean_pct_str(cancel_curr)}% cancellations" if cancel_curr is not None else ""
            return (
                f"{sal_lead}average price stayed around {format_inr(asp)}, so {top_text}{cancel_txt}.{resid_txt} "
                "Ready for the prioritised action plan?"
            )

        if phase == "RECOMMEND":
            plan_ctx = pre_ctx.get("build_action_plan") or {}
            actions = plan_ctx.get("actions") or action_state.get("actions") or []
            if actions:
                first = actions[0]
                return _format_action_question(first, due_2d)
            return _format_action_question(None, due_2d)

        if phase == "COMMIT":
            last_consent = action_state.get("last_consent_eval") or {}
            consent_kind = last_consent.get("kind")
            pending_conf = action_state.get("pending_confirmation")
            next_act = _pick_next_action(action_state, recent_tools)
            all_acts = action_state.get("actions") or []

            if consent_kind == "AMBIGUOUS_OR_SOFT":
                if last_consent.get("marked_tentative"):
                    if next_act:
                        return _format_action_question(next_act, due_2d, prefix="Noted as tentative.")
                    return f"Noted as tentative{sal_comma}. Shall I send the summary?"
                tid = (pending_conf or {}).get("action_id")
                match_full = next((a for a in all_acts if a.get("action_id") == tid), None) if tid else None
                return _format_action_question(match_full or pending_conf or next_act, due_2d, retry=True)

            if consent_kind == "DECLINE":
                if next_act:
                    return _format_action_question(next_act, due_2d, prefix="Understood, skipped that.")
                return f"Understood, skipped that. Shall I send the summary?"

            if "commit_action" in recent_tools:
                if next_act:
                    return _format_action_question(next_act, due_2d, prefix="Confirmed.")
                return f"Confirmed{sal_comma}. Follow-up is set for {followup_3d}. Shall I send the summary?"

            return _format_action_question(next_act or pending_conf, due_2d)

        if phase == "SUMMARY":
            card = (recent_tools.get("send_call_summary") or {}).get("summary_card") or {}
            agreed = card.get("agreed_actions") or card.get("commitments") or []
            declined = card.get("declined_actions") or []
            tentative = card.get("tentative_actions") or []
            not_disc = card.get("not_discussed_actions") or []
            fu_date = ((card.get("followup") or {}).get("followup_date")) or followup_3d

            issue_bits = [f"sales dropped {sales_pct}% to {format_inr(sales_curr)} with {format_inr(asp)} average price"]
            if oos_curr is not None and int(oos_curr) > 0:
                issue_bits.append(f"{oos_curr} out-of-stock products")
            if cancel_curr is not None:
                issue_bits.append(f"{_clean_pct_str(cancel_curr)}% cancellations")

            if agreed:
                ag_txts = [
                    f"{self._short_label(a)} by {a.get('due_date', due_2d)}".strip()
                    for a in agreed[:3]
                ]
                ag_part = f"Agreed: {', '.join(ag_txts)}."
            else:
                ag_part = "Agreed actions: none yet."

            not_ag = [*declined, *tentative, *not_disc]
            na_part = (
                f" Not agreed: {', '.join(self._short_label(a) for a in not_ag[:2])}."
                if not_ag
                else ""
            )
            tail = (
                " Please confirm with a clear yes which action to lock before we close?"
                if not agreed
                else ""
            )
            return f"Recap{sal_comma}: {', '.join(issue_bits[:3])}. {ag_part}{na_part} Follow-up is {fu_date}.{tail}"

        return f"Thank you for your time{sal_comma}. Wishing {shop_name} strong sales this week!"

    def _short_label(self, act: dict[str, Any]) -> str:
        s = str(act.get("action_summary") or act.get("title") or act.get("action_id") or "").lower()
        aid = str(act.get("action_id") or "").upper()
        if "restock" in s or aid in ("ACT-1", "ACT-FU-1"):
            return "restocking top products"
        if "pric" in s or aid in ("ACT-2", "ACT-FU-3"):
            return "updating pricing"
        if "visib" in s or "keyword" in s or aid == "ACT-4":
            return "improving search visibility"
        if "sync" in s or "dispatch" in s or "cancel" in s or aid in ("ACT-3", "ACT-FU-2"):
            return "syncing stock and dispatch"
        return "action item"

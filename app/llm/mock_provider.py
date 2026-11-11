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
                if "commit_action" in available_tools and any(
                    w in last_user for w in ("yes", "ok", "haan", "theek", "agree", "friday", "tomorrow", "kal", "sure", "done", "commit")
                ):
                    oos_curr = snap.get("oos_curr")
                    oos_target = max(1, (int(oos_curr) // 2)) if isinstance(oos_curr, int) else 8
                    calls.append(
                        ToolCallRequest(
                            id="m1",
                            name="commit_action",
                            arguments={
                                "action_id": "ACT-1",
                                "action_summary": "Execute priority restocking and operational recovery actions",
                                "owner": seller_name,
                                "due_date": "Within 48 hours",
                                "target_metric": f"Out-of-stock products <= {oos_target}",
                            },
                        )
                    )
                    if "schedule_followup" in available_tools:
                        calls.append(
                            ToolCallRequest(
                                id="m2",
                                name="schedule_followup",
                                arguments={
                                    "followup_date": "In 3 days at 11:00 AM",
                                    "success_targets": [
                                        f"Out-of-stock products <= {oos_target}",
                                        "Cancellation rate <= 10%",
                                    ],
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

        reply = self._render_reply(phase, is_followup, lang, last_user, snap, pre_ctx, recent_tools)
        for word in reply.split(" "):
            if word:
                yield LLMStreamChunk(type="token", text=word + " ", provider=self.provider_name, model=self.model_name)
        yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)

    def _build_summary_issues(self, snap: dict[str, Any]) -> list[str]:
        sales_prev = int(snap.get("sales_prev") or 0)
        sales_curr = int(snap.get("sales_curr") or 0)
        sales_pct = abs(float(snap.get("sales_delta_pct") or 0.0))
        asp = int(snap.get("asp") or 0)
        drivers = [d for d in (snap.get("drivers_summary") or []) if d.get("assessable") and d.get("lost_orders", 0) > 0]
        issues = [f"Sales moved {sales_pct}% (Rs {sales_prev:,} -> Rs {sales_curr:,}); ASP is Rs {asp:,}."]
        for d in drivers[:2]:
            issues.append(f"{d['title']} accounted for {d['share_pct']}% ({d['lost_orders']} lost orders).")
        if snap.get("missing_signals"):
            issues.append("Some metrics are not assessable with the data provided.")
        return issues[:3]

    def _render_reply(
        self,
        phase: str,
        is_followup: bool,
        lang: str,
        last_user: str,
        snap: dict[str, Any],
        pre_ctx: dict[str, Any],
        recent_tools: dict[str, dict[str, Any]],
    ) -> str:
        salutation = str(snap.get("salutation") or "Seller Partner")
        shop_name = str(snap.get("shop_name") or "your store")
        sales_curr = int(snap.get("sales_curr") or 0)
        sales_pct = abs(float(snap.get("sales_delta_pct") or 0.0))
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
            pace_pct = fu_m.get("sales_vs_crisis_pct", 0)
            fu_oos = fu_m.get("out_of_stock_skus")
            oos_txt = f" and out-of-stock items are {fu_oos}" if fu_oos is not None else ""
            return (
                f"Namaste {salutation}, calling for our check-in. "
                f"Your sales pace moved {pace_pct}% to Rs {pace:,}{oos_txt}. Should we review remaining blockers?"
            )

        if phase == "GREETING":
            oos_txt = f" and {oos_curr} products went out of stock" if oos_curr is not None else ""
            return (
                f"Namaste {salutation}, I noticed your sales moved {sales_pct}% to Rs {sales_curr:,}{oos_txt}. "
                "Do you have 2 minutes to review this together?"
            )

        if phase == "UNDERSTAND":
            if any(w in last_user for w in ("angry", "frustrated", "fed up", "cancel", "waste", "useless", "problem")):
                return f"I understand how frustrating this drop is, {salutation}. Have any top-selling items recently gone out of stock?"
            return f"I understand how worrying this drop is, {salutation}. Did any top-selling products run out of stock recently?"

        if phase == "CLARIFY":
            if "stock" in last_user or "out" in last_user or "yes" in last_user or "supplier" in last_user:
                return f"Got it, {salutation}. Were there also any dispatch delays or price changes from competitors?"
            return "Okay, I see. Did you notice delays in dispatch or any competitor price changes recently?"

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
                    parts.append(f"cancellations are at {fu_c['cancellation_rate_pct']}%")
                if rem_oos:
                    parts.append(f"{rem_oos['sku_id']} ({rem_oos['name']}) still needs restock")
                if rem_pr:
                    parts.append(f"{rem_pr['sku_id']} is {rem_pr['gap_pct']}% above competitors")
                detail = ", ".join(parts) or "several actions improved performance"
                return f"Great progress, {salutation}: {detail}. Shall we close the remaining items?"

            if missing_signals:
                summary_bit = (
                    f"roughly {top_driver['share_pct']}% ({top_driver['lost_orders']} of {orders_drop} lost orders) stems from {top_driver['title']}"
                    if top_driver
                    else f"{orders_drop} orders were lost"
                )
                return (
                    f"{salutation}, your ASP is Rs {asp:,} and {summary_bit}. "
                    "Optional missing columns are not assessable with the data provided. Ready for the action plan?"
                )

            top_text = (
                f"roughly {top_driver['share_pct']}% of the {orders_drop}-order drop comes from {top_driver['title']}"
                if top_driver
                else f"{orders_drop} fewer orders overall"
            )
            resid_txt = (
                f" About {resid_driver['share_pct']}% ({resid_driver['lost_orders']} orders) remains unexplained residual."
                if resid_driver and float(resid_driver.get("share_pct") or 0) >= 10.0
                else ""
            )
            cancel_txt = f", with {cancel_curr}% cancellations" if cancel_curr is not None else ""
            return (
                f"{salutation}, ASP stayed around Rs {asp:,}, so {top_text}{cancel_txt}.{resid_txt} "
                "Ready for the prioritised action plan?"
            )

        if phase == "RECOMMEND":
            plan_ctx = pre_ctx.get("build_action_plan") or {}
            actions = plan_ctx.get("actions") or []
            if actions:
                first = actions[0]
                rec = first.get("estimated_recovery_range_rs", "recovery")
                return (
                    f"Priority 1: {first['title']} ({first['recommended_due']}) for {rec}. "
                    "Any promos are subject to eligibility. Shall we lock your timeline?"
                )
            return f"I recommend fixing top declining SKUs first, {salutation}. Shall we agree on a 48-hour timeline?"

        if phase == "COMMIT":
            if "commit_action" in recent_tools:
                return f"Thank you, {salutation}. I have logged your 48-hour commitment and scheduled a follow-up check-in. Shall I send the summary?"
            return f"Perfect, {salutation}. Can you confirm starting the top action within 48 hours so I can schedule follow-up?"

        if phase == "SUMMARY":
            share_bit = (
                f"roughly {top_driver['share_pct']}% came from {top_driver['title']}"
                if top_driver
                else f"sales moved {sales_pct}%"
            )
            return (
                f"Recap for {salutation}: {share_bit} in the {sales_pct}% drop. "
                "You committed to next steps and I sent your summary with a 3-day follow-up. Anything else?"
            )

        return f"Thank you for your time, {salutation}. Wishing {shop_name} strong sales this week!"

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


class MockLLMProvider(LLMProvider):
    """Rule-based test LLM that emits realistic tool calls & concise voice replies."""

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

        available_tools = {t["function"]["name"] for t in tools}
        last_user = ""
        for m in reversed(messages):
            if m.role == "user":
                last_user = m.content.lower()
                break

        # Check if we just received tool outputs in this turn (since last user message)
        recent_tools: dict[str, dict[str, Any]] = {}
        for m in reversed(messages):
            if m.role == "user":
                break
            if m.role == "tool" and m.tool_name:
                try:
                    recent_tools[m.tool_name] = json.loads(m.content)
                except Exception:
                    recent_tools[m.tool_name] = {}

        if not recent_tools:
            # Decide which parallel tools to invoke based on phase
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
                    w in last_user for w in ("yes", "ok", "haan", "agree", "friday", "tomorrow", "kal", "sure", "done", "commit")
                ):
                    calls.append(
                        ToolCallRequest(
                            id="m1",
                            name="commit_action",
                            arguments={
                                "action_id": "ACT-1",
                                "action_summary": "Restock top 5 OOS products and fix stock sync",
                                "owner": "Rajesh Kumar",
                                "due_date": "Within 48 hours",
                                "target_metric": "Out-of-stock products <= 8",
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
                                        "Out-of-stock products <= 8",
                                        "Cancellation rate <= 10%",
                                    ],
                                },
                            )
                        )
            elif phase == "SUMMARY" and "send_call_summary" in available_tools:
                calls.append(
                    ToolCallRequest(
                        id="m1",
                        name="send_call_summary",
                        arguments={
                            "key_issues": [
                                "Sales fell 32.5% (Rs 2,00,000 -> Rs 1,35,000) on flat ASP Rs 500.",
                                "60% of the drop came from 13 newly out-of-stock products (total OOS 18).",
                                "Cancellation rate rose 8% -> 14% and 3 SKUs are 6.9%-8.3% above competitor prices.",
                            ]
                        },
                    )
                )

            if calls:
                yield LLMStreamChunk(type="tool_calls", tool_calls=calls, provider=self.provider_name, model=self.model_name)
                yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)
                return

        # Craft concise grounded response (<35 words) based on phase and language
        reply = self._render_reply(phase, is_followup, lang, last_user, recent_tools)
        for word in reply.split(" "):
            yield LLMStreamChunk(type="token", text=word + " ", provider=self.provider_name, model=self.model_name)
        yield LLMStreamChunk(type="done", provider=self.provider_name, model=self.model_name)

    def _render_reply(
        self,
        phase: str,
        is_followup: bool,
        lang: str,
        last_user: str,
        recent_tools: dict[str, dict[str, Any]],
    ) -> str:
        if is_followup and phase == "GREETING":
            return (
                "Namaste Rajesh ji, calling for our 3-day check-in. "
                "Your daily sales pace jumped 30% to Rs 1,75,500 and out-of-stock products dropped from 18 to 9. Should we review the last 2 blockers?"
            )
        if phase == "GREETING":
            return (
                "Namaste Rajesh ji, I noticed your sales dropped 32.5% to Rs 1,35,000 and 18 products went out of stock. "
                "Do you have 2 minutes to fix this together?"
            )
        if phase == "UNDERSTAND":
            if any(w in last_user for w in ("angry", "frustrated", "fed up", "cancel", "waste", "useless", "problem")):
                return "I understand how frustrating this 32.5% drop is, Rajesh ji. Have any bestsellers recently run out of stock in your warehouse?"
            return "I understand how worrying this drop is, Rajesh ji. Did any top-selling kitchen products run out of stock recently?"
        if phase == "CLARIFY":
            if "stock" in last_user or "out" in last_user or "yes" in last_user or "supplier" in last_user:
                return "Got it, Rajesh ji. Were there also any dispatch delays or stock-sync mismatches during pickup?"
            return "Okay, I see. Did you notice delays in dispatch or any competitor price changes recently?"
        if phase in ("DIAGNOSE", "REVIEW_COMMITMENTS"):
            if is_followup:
                return (
                    "Great progress, Rajesh ji: 4 of 5 products are restocked and cancellations fell from 14% to 10.5%. "
                    "Only Storage Jars HK-009 is out of stock and Spatula Set HK-021 is 8.3% above competition. Shall we close those?"
                )
            return (
                "Rajesh ji, your average price stayed flat at Rs 500, so this is a volume issue: 60% of the 130-order drop is from 18 out-of-stock products like the 5L Pressure Cooker, "
                "plus 14% cancellations and 3 overpriced items. Ready for the action plan?"
            )
        if phase == "RECOMMEND":
            return (
                "Priority 1: restock your top 5 out-of-stock products within 48 hours to recover Rs 28,000 to Rs 36,000 monthly, and sync stock twice daily to cut cancellations below 10%. "
                "Should we also match competitor pricing on the 3 overpriced products?"
            )
        if phase == "COMMIT":
            if "commit_action" in recent_tools:
                return "Thank you, Rajesh ji. I have logged your 48-hour commitment and scheduled our follow-up in 3 days targeting 8 or fewer out-of-stock products and under 10% cancellations. Shall I send the summary?"
            return "Perfect, Rajesh ji. Can you confirm you will restock the top 5 products within 48 hours and sync inventory daily starting today?"
        if phase == "SUMMARY":
            return (
                "Here is our recap, Rajesh ji: 60% of the 32.5% drop came from out-of-stock products, alongside 14% cancellations and 3 overpriced items. "
                "You committed to restocking within 48 hours, and I sent your summary with a 3-day follow-up. Anything else?"
            )
        return "Thank you for your time, Rajesh ji. Wishing Kumar Home & Kitchen strong sales this week!"


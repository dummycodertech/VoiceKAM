"""LLM Function-Calling Tool Definitions & Deterministic Dispatcher."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.engine import diagnosis

if TYPE_CHECKING:
    from app.agent.session import SessionState


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_seller_overview",
            "description": "Get Rajesh Kumar's store profile and headline monthly performance summary (sales, orders, ASP, OOS, cancellations).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metric_comparison",
            "description": "Compare Last Month vs This Month (and 3-day follow-up if in follow-up mode) across sales, orders, ASP, cancellations, and out-of-stock SKUs.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_diagnosis",
            "description": "Run the deterministic revenue-decline driver attribution engine to rank root causes (% contribution, lost orders, lost rupees, assumptions).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_out_of_stock_skus",
            "description": "List out-of-stock products ranked by estimated lost revenue per day.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of out-of-stock products to return (default 5).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cancellation_breakdown",
            "description": "Get order cancellations broken down by reason (seller stock unavailable, dispatch SLA delay, buyer initiated, courier issues).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pricing_gaps",
            "description": "Identify top active products priced higher than the lowest competitor price and quantify lost orders.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_visibility_funnel",
            "description": "Compare search impressions, clicks, CTR, orders, and conversion rate between Last Month and This Month.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_action_plan",
            "description": "Build the prioritised 3-action recovery plan ranked by business impact and effort with indicative recovery estimates.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit_action",
            "description": "Record the seller's explicit verbal commitment to a specific action, owner, and due date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action_id": {"type": "string", "description": "Action identifier, e.g. ACT-1, ACT-2, ACT-3."},
                    "action_summary": {"type": "string", "description": "Short clear summary of the committed action."},
                    "owner": {"type": "string", "description": "Who owns executing this action (usually Rajesh Kumar or KAM Support)."},
                    "due_date": {"type": "string", "description": "Target completion date or timeframe agreed by the seller."},
                    "target_metric": {"type": "string", "description": "Optional measurable target, e.g. OOS <= 8 products."},
                },
                "required": ["action_id", "action_summary", "owner", "due_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_followup",
            "description": "Schedule a mock follow-up review call with measurable success metric targets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "followup_date": {"type": "string", "description": "Agreed date/time for follow-up call, e.g. 'In 3 days at 11 AM'."},
                    "success_targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Measurable metric targets for the next check-in, e.g. ['Out-of-stock products <= 8', 'Cancellation rate <= 10%'].",
                    },
                },
                "required": ["followup_date", "success_targets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_call_summary",
            "description": "Generate and dispatch the final mock WhatsApp/SMS call summary card containing key issues, committed actions, due dates, and follow-up call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key_issues": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-3 concise diagnosed issues with exact numbers.",
                    }
                },
                "required": [],
            },
        },
    },
]


def _format_whatsapp_summary(session: SessionState, key_issues: list[str]) -> str:
    header = (
        "*Snapdeal Virtual KAM — 3-Day Follow-Up Summary (Prototype)*"
        if session.mode == "followup"
        else "*Snapdeal Virtual KAM — Call Summary (Prototype)*"
    )
    lines = [
        header,
        "Seller: Rajesh Kumar (Kumar Home & Kitchen)",
        f"Date: {datetime.now(timezone.utc).strftime('%d %b %Y')}",
        "",
        "*Key Findings:*",
    ]
    for issue in key_issues:
        lines.append(f"- {issue}")

    lines.append("")
    lines.append("*Agreed Action Plan:*")
    if session.commitments:
        for idx, c in enumerate(session.commitments, 1):
            lines.append(f"{idx}. {c['action_summary']} | Owner: {c['owner']} | Due: {c['due_date']}")
    else:
        plan = diagnosis.build_action_plan(session.snapshot)
        for idx, a in enumerate(plan["actions"], 1):
            lines.append(f"{idx}. {a['title']} | Owner: {a['owner']} | Target: {a['recommended_due']}")

    lines.append("")
    if session.followup_schedule:
        fu = session.followup_schedule
        lines.append(f"*Next Follow-up Call:* {fu['followup_date']}")
        lines.append("*Success Targets:* " + "; ".join(fu["success_targets"]))
    else:
        lines.append("*Next Follow-up Call:* In 3 days")
        lines.append("*Success Targets:* Out-of-stock products <= 8; Cancellation rate <= 10%")

    lines.append("")
    lines.append("_Prototype mock data summary — Riya, Virtual KAM_")
    return "\n".join(lines)


def execute_tool(
    name: str,
    arguments: dict[str, Any] | None,
    session: SessionState | None = None,
) -> dict[str, Any]:
    """Execute a deterministic tool and update session state if applicable."""
    args = arguments or {}
    snapshot = session.snapshot if session else "current"

    if name == "get_seller_overview":
        return diagnosis.get_seller_overview(snapshot=snapshot)

    if name == "get_metric_comparison":
        return diagnosis.compute_headline_metrics(snapshot=snapshot)

    if name == "run_diagnosis":
        return diagnosis.run_diagnosis(snapshot=snapshot)

    if name == "get_out_of_stock_skus":
        limit = int(args.get("limit", 5))
        return diagnosis.get_out_of_stock_skus(limit=limit, snapshot=snapshot)

    if name == "get_cancellation_breakdown":
        return diagnosis.get_cancellation_breakdown(snapshot=snapshot)

    if name == "get_pricing_gaps":
        return diagnosis.get_pricing_gaps(snapshot=snapshot)

    if name == "get_visibility_funnel":
        return diagnosis.get_visibility_funnel(snapshot=snapshot)

    if name == "build_action_plan":
        return diagnosis.build_action_plan(snapshot=snapshot)

    if name == "commit_action":
        commitment = {
            "action_id": str(args.get("action_id", f"ACT-{len(session.commitments) + 1 if session else 1}")),
            "action_summary": str(args.get("action_summary", "")).strip(),
            "owner": str(args.get("owner", "Rajesh Kumar")).strip(),
            "due_date": str(args.get("due_date", "Within 48 hours")).strip(),
            "target_metric": str(args.get("target_metric", "")).strip(),
            "status": "committed",
        }
        if session is not None:
            # Replace if same action_id already committed, else append
            existing_idx = next(
                (i for i, c in enumerate(session.commitments) if c["action_id"] == commitment["action_id"]),
                None,
            )
            if existing_idx is not None:
                session.commitments[existing_idx] = commitment
            else:
                session.commitments.append(commitment)
        return {
            "recorded": True,
            "commitment": commitment,
            "total_commitments": len(session.commitments) if session else 1,
        }

    if name == "schedule_followup":
        targets = args.get("success_targets") or [
            "Out-of-stock products <= 8",
            "Cancellation rate <= 10%",
        ]
        schedule = {
            "followup_date": str(args.get("followup_date", "In 3 days at 11:00 AM")),
            "success_targets": [str(t) for t in targets],
            "scheduled": True,
        }
        if session is not None:
            session.followup_schedule = schedule
        return schedule

    if name == "send_call_summary":
        default_issues = (
            [
                "3-day monthly sales pace recovered +30.0% (Rs 1,35,000 -> Rs 1,75,500 pace) with 4 of top 5 OOS SKUs restocked (OOS 18 -> 9).",
                "Cancellation rate dropped from 14.0% to 10.5% after stock sync; tightening 2 PM dispatch SLA closes the gap under 10.0%.",
                "Remaining blockers: HK-009 (Storage Jars) restock and HK-021 (Spatula Set) reprice (Rs 300 -> ~Rs 280).",
            ]
            if snapshot == "followup"
            else [
                "Sales dropped 32.5% (Rs 2,00,000 -> Rs 1,35,000) while ASP stayed flat at Rs 500 (volume problem).",
                "60% of drop driven by 13 newly out-of-stock products (total OOS 5 -> 18).",
                "Seller stock cancellations rose (6 -> 14, overall rate 8% -> 14%) and 3 top products are priced 6.9%-8.3% above competitors.",
            ]
        )
        issues = args.get("key_issues") or default_issues
        issues = [str(i) for i in issues]
        if session is not None:
            whatsapp_text = _format_whatsapp_summary(session, issues)
            summary_card = {
                "seller_name": "Rajesh Kumar",
                "shop_name": "Kumar Home & Kitchen",
                "key_issues": issues,
                "commitments": list(session.commitments),
                "followup": session.followup_schedule
                or {
                    "followup_date": "In 3 days",
                    "success_targets": ["Out-of-stock products <= 8", "Cancellation rate <= 10%"],
                },
                "whatsapp_text": whatsapp_text,
            }
            session.summary_card = summary_card
            return {"sent": True, "summary_card": summary_card}

        # Fallback if called without session (e.g. direct unit test)
        return {
            "sent": True,
            "summary_card": {
                "seller_name": "Rajesh Kumar",
                "shop_name": "Kumar Home & Kitchen",
                "key_issues": issues,
                "commitments": [],
                "followup": {"followup_date": "In 3 days", "success_targets": []},
                "whatsapp_text": "\n".join(issues),
            },
        }

    return {"error": f"Unknown tool: {name}"}


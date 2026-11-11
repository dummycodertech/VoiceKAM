"""LLM Function-Calling Tool Definitions & Deterministic Dispatcher."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.data.context import SellerContext
from app.data.seed_data import get_scenario_context
from app.engine import diagnosis
from app.utils.dates import (
    anchor_now_ist,
    compute_followup_date_ist,
    format_ist_short_date,
    resolve_due_date_ist,
)
from app.utils.formatting import format_inr, normalize_indian_grouping

if TYPE_CHECKING:
    from app.agent.session import SessionState


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_seller_overview",
            "description": "Get the seller's store profile and headline monthly performance summary (sales, orders, ASP, OOS, cancellations).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metric_comparison",
            "description": "Compare Last Month vs This Month (and follow-up pace if in follow-up mode) across sales, orders, ASP, cancellations, and out-of-stock SKUs.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_diagnosis",
            "description": "Run the deterministic revenue-decline driver attribution engine to rank root causes (% contribution, lost orders, lost rupees, assumptions, missing columns).",
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
            "description": "Compare search impressions, clicks, CTR, orders, and conversion rate between baseline and current periods.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_action_plan",
            "description": "Build the prioritised recovery plan ranked by business impact and effort with indicative recovery estimates.",
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
                    "owner": {"type": "string", "description": "Who owns executing this action (usually the seller or ops lead)."},
                    "due_date": {"type": "string", "description": "Code-computed IST completion date agreed by the seller (e.g. 'Friday 13 Nov')."},
                    "target_metric": {"type": "string", "description": "Optional measurable target derived from the seller's metrics."},
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
                    "followup_date": {"type": "string", "description": "Code-computed IST date/time for follow-up call, e.g. 'Saturday 14 Nov at 11:00 AM IST'."},
                    "success_targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Measurable metric targets derived from the dataset targets.",
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


def _default_targets(ctx: SellerContext, snapshot: str = "current") -> list[str]:
    snap = "followup" if snapshot == "followup" else "current"
    return diagnosis.compute_followup_targets(ctx, snap)["targets_list"]  # type: ignore[arg-type]


def _derive_default_issues(ctx: SellerContext, snapshot: str) -> list[str]:
    snap = "followup" if snapshot == "followup" else "current"
    headline = diagnosis.compute_headline_metrics(ctx, snap)  # type: ignore[arg-type]
    diag = diagnosis.run_diagnosis(ctx, snap)  # type: ignore[arg-type]
    m = headline["metrics"]

    sales_prev = m["sales_gmv"]["baseline"]
    sales_curr = m["sales_gmv"]["current"]
    sales_pct = m["sales_gmv"]["delta_pct"]
    asp_curr = m["asp"]["current"]

    issues: list[str] = []
    if snap == "followup" and headline.get("followup"):
        fu = headline["followup"]
        issues.append(
            f"Follow-up pace moved {fu['sales_vs_crisis_pct']:+.1f}% ({format_inr(sales_curr)} -> {format_inr(fu['sales_gmv_pace'])} monthly pace)."
        )
    else:
        issues.append(
            f"Sales moved {sales_pct}% ({format_inr(sales_prev)} -> {format_inr(sales_curr)}); ASP is {format_inr(asp_curr)}."
        )

    top_assessed = [d for d in diag.get("drivers", []) if d.get("assessable") and d.get("lost_orders", 0) > 0]
    for d in top_assessed[:2]:
        issues.append(f"{d['title']} accounted for {d['share_pct']}% ({d['lost_orders']} orders): {d['evidence']}")

    missing = diag.get("missing_signals") or []
    if missing:
        issues.append("Some columns were missing (" + "; ".join(missing[:2]) + ").")
    return issues[:3]


def _format_whatsapp_summary(session: SessionState, ctx: SellerContext, key_issues: list[str]) -> str:
    settings = get_settings()
    seller_name = ctx.profile.seller_name
    shop_name = ctx.profile.shop_name
    header = (
        f"*{settings.project_name} — Follow-Up Summary*"
        if session.mode == "followup"
        else f"*{settings.project_name} — Call Summary*"
    )
    anchor_dt = anchor_now_ist(getattr(session, "anchor_date_ist", None))
    default_fu_date = compute_followup_date_ist(offset_days=3, anchor=anchor_dt)
    lines = [
        header,
        f"Seller: {seller_name} ({shop_name})",
        f"Date: {format_ist_short_date(anchor_dt)} (IST)",
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
        plan = diagnosis.build_action_plan(ctx, session.snapshot, anchor_dt=anchor_dt)
        for idx, a in enumerate(plan["actions"], 1):
            lines.append(f"{idx}. {a['title']} | Owner: {a['owner']} | Target: {a['recommended_due']}")

    lines.append("")
    if session.followup_schedule:
        fu = session.followup_schedule
        lines.append(f"*Next Follow-up Call:* {fu['followup_date']}")
        lines.append("*Success Targets:* " + "; ".join(fu["success_targets"]))
    else:
        lines.append(f"*Next Follow-up Call:* {default_fu_date}")
        lines.append("*Success Targets:* " + "; ".join(_default_targets(ctx, session.snapshot)))

    lines.append("")
    lines.append(f"_Generated by {settings.agent_name}, {settings.project_name} Virtual KAM_")
    return normalize_indian_grouping("\n".join(lines))


def execute_tool(
    name: str,
    arguments: dict[str, Any] | None,
    session: SessionState | None = None,
    context: SellerContext | None = None,
) -> dict[str, Any]:
    """Execute a deterministic tool parameterized purely by SellerContext."""
    args = arguments or {}
    snapshot = session.snapshot if session else "current"
    ctx = session.seller_context if session is not None else (context or get_scenario_context())

    anchor_dt = anchor_now_ist(getattr(session, "anchor_date_ist", None) if session else None)

    if name == "get_seller_overview":
        return diagnosis.get_seller_overview(ctx, snapshot=snapshot)

    if name == "get_metric_comparison":
        return diagnosis.compute_headline_metrics(ctx, snapshot=snapshot)

    if name == "run_diagnosis":
        return diagnosis.run_diagnosis(ctx, snapshot=snapshot)

    if name == "get_out_of_stock_skus":
        limit = int(args.get("limit", 5))
        return diagnosis.get_out_of_stock_skus(ctx, limit=limit, snapshot=snapshot)

    if name == "get_cancellation_breakdown":
        return diagnosis.get_cancellation_breakdown(ctx, snapshot=snapshot)

    if name == "get_pricing_gaps":
        return diagnosis.get_pricing_gaps(ctx, snapshot=snapshot)

    if name == "get_visibility_funnel":
        return diagnosis.get_visibility_funnel(ctx, snapshot=snapshot)

    if name == "build_action_plan":
        return diagnosis.build_action_plan(ctx, snapshot=snapshot, anchor_dt=anchor_dt)

    if name == "commit_action":
        action_id = str(args.get("action_id", f"ACT-{len(session.commitments) + 1 if session else 1}"))
        raw_due = str(args.get("due_date") or "").strip()
        due_date_ist = resolve_due_date_ist(raw_due, anchor=anchor_dt, default_days=2)
        commitment = {
            "action_id": action_id,
            "action_summary": normalize_indian_grouping(str(args.get("action_summary", "")).strip()),
            "owner": str(args.get("owner", ctx.profile.seller_name)).strip(),
            "due_date": due_date_ist,
            "target_metric": normalize_indian_grouping(str(args.get("target_metric", "")).strip()),
            "status": "committed",
        }
        if session is not None:
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
        targets = args.get("success_targets") or _default_targets(ctx, snapshot)
        raw_fu = str(args.get("followup_date") or "").strip()
        if not raw_fu or "in 3 days" in raw_fu.lower():
            fu_date = compute_followup_date_ist(offset_days=3, anchor=anchor_dt)
        else:
            resolved_d = resolve_due_date_ist(raw_fu, anchor=anchor_dt, default_days=3)
            fu_date = f"{resolved_d} at 11:00 AM IST"
        schedule = {
            "followup_date": fu_date,
            "success_targets": [normalize_indian_grouping(str(t)) for t in targets],
            "scheduled": True,
        }
        if session is not None:
            session.followup_schedule = schedule
        return schedule

    if name == "send_call_summary":
        issues = args.get("key_issues") or _derive_default_issues(ctx, snapshot)
        issues = [normalize_indian_grouping(str(i)) for i in issues]
        default_fu = compute_followup_date_ist(offset_days=3, anchor=anchor_dt)
        if session is not None:
            whatsapp_text = normalize_indian_grouping(_format_whatsapp_summary(session, ctx, issues))
            norm_commits = [
                {
                    **c,
                    "action_summary": normalize_indian_grouping(str(c.get("action_summary", ""))),
                    "target_metric": normalize_indian_grouping(str(c.get("target_metric", ""))),
                }
                for c in session.commitments
            ]
            summary_card = {
                "seller_name": ctx.profile.seller_name,
                "shop_name": ctx.profile.shop_name,
                "key_issues": issues,
                "commitments": norm_commits,
                "followup": session.followup_schedule
                or {
                    "followup_date": default_fu,
                    "success_targets": _default_targets(ctx, snapshot),
                },
                "whatsapp_text": whatsapp_text,
            }
            session.summary_card = summary_card
            return {"sent": True, "summary_card": summary_card}

        return {
            "sent": True,
            "summary_card": {
                "seller_name": ctx.profile.seller_name,
                "shop_name": ctx.profile.shop_name,
                "key_issues": issues,
                "commitments": [],
                "followup": {"followup_date": default_fu, "success_targets": _default_targets(ctx, snapshot)},
                "whatsapp_text": "\n".join(issues),
            },
        }

    return {"error": f"Unknown tool: {name}"}

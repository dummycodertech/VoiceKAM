"""Deterministic Diagnosis & Counterfactual Attribution Engine.

Zero global store constants and zero ground-truth label leakage.
Every metric, driver attribution, confidence tier, SKU ranking, and follow-up
recovery projection is derived cleanly from the session's SellerContext.
"""

from __future__ import annotations

import statistics
from typing import Any

from app.data.context import (
    DataCompleteness,
    SellerContext,
    SkuRecord,
    SnapshotType,
    compute_data_completeness,
)

UNAVAILABLE_MSG = "not assessable with the data provided"


def _pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return round(((new - old) / old) * 100.0, 1)


def _completeness(context: SellerContext) -> DataCompleteness:
    return context.completeness or compute_data_completeness(
        context.skus, context.cancellations, context.funnel
    )


def _suggested_reprice(comp_lowest: float) -> float:
    if comp_lowest >= 100:
        return float(round(min(comp_lowest * 1.015, comp_lowest + 4)))
    return round(comp_lowest * 1.015, 2)


def _compute_attribution_core(
    context: SellerContext, snapshot: SnapshotType = "current"
) -> dict[str, Any]:
    """Single source of truth for disjoint SKU-level attribution & reconciliation."""
    comp = _completeness(context)
    skus = context.resolve_skus(snapshot)
    cancels = context.resolve_cancellations(snapshot)
    funnel = context.resolve_funnel(snapshot)

    baseline_orders = sum(s.orders_prev for s in context.skus)
    current_orders = sum(s.orders_curr for s in skus)
    baseline_sales = round(sum(s.orders_prev * s.price for s in context.skus))
    current_sales = round(sum(s.orders_curr * s.price for s in skus))
    base_asp = (baseline_sales / baseline_orders) if baseline_orders else 0.0

    total_lost_orders = max(0, baseline_orders - current_orders)
    total_lost_rev = max(0, baseline_sales - current_sales)

    # Track per-SKU remaining order decline budget d_i so drivers never double-count the same SKU drop
    rem_sku_drop: dict[str, int] = {
        s.sku_id: max(0, s.orders_prev - s.orders_curr) for s in skus
    }
    rem_budget_orders = total_lost_orders
    rem_budget_rev = total_lost_rev

    # ------------------------------------------------------------------
    # (a) Stock-Outs: daily baseline velocity * days_out_of_stock_curr
    # ------------------------------------------------------------------
    oos_items: list[dict[str, Any]] = []
    newly_oos_list: list[SkuRecord] = []
    chronic_oos_list: list[SkuRecord] = []
    raw_oos_orders = 0
    raw_oos_rev = 0

    if comp.stock_oos_assessable:
        for s in skus:
            if not s.is_oos_curr:
                continue
            is_new_oos = not s.is_oos_prev
            if is_new_oos:
                newly_oos_list.append(s)
            else:
                chronic_oos_list.append(s)

            sku_drop = rem_sku_drop[s.sku_id]
            est_lost = 0
            if is_new_oos and s.orders_prev > 0 and sku_drop > 0:
                if comp.has_days_oos and s.days_out_of_stock_curr is not None and s.days_out_of_stock_curr > 0:
                    active_days = max(1, 30 - int(s.days_out_of_stock_prev or 0))
                    vel = s.orders_prev / float(active_days)
                    est_lost = min(sku_drop, round(vel * s.days_out_of_stock_curr))
                else:
                    # Binary stock_qty_curr == 0 fallback when exact days OOS is missing
                    est_lost = sku_drop

            rem_sku_drop[s.sku_id] = max(0, sku_drop - est_lost)
            raw_oos_orders += est_lost
            est_rev = round(est_lost * s.price)
            raw_oos_rev += est_rev

            daily_vel = s.orders_prev / max(1.0, 30.0 - float(s.days_out_of_stock_prev or 0))
            oos_items.append(
                {
                    "sku_id": s.sku_id,
                    "name": s.name,
                    "category": s.category,
                    "price": s.price,
                    "last_month_orders": s.orders_prev,
                    "this_month_orders": s.orders_curr,
                    "lost_orders": est_lost if is_new_oos else 0,
                    "lost_revenue_month": est_rev if is_new_oos else 0,
                    "estimated_lost_rev_per_day": round(daily_vel * s.price),
                    "days_oos_this_month": s.days_oos_this_month,
                    "newly_oos": is_new_oos,
                }
            )

    oos_orders = min(rem_budget_orders, raw_oos_orders) if comp.stock_oos_assessable else 0
    oos_rev = min(rem_budget_rev, raw_oos_rev) if comp.stock_oos_assessable else 0
    rem_budget_orders -= oos_orders
    rem_budget_rev -= oos_rev

    # ------------------------------------------------------------------
    # (b) Price Gaps vs In-Stock Fairly-Priced Control Group
    # ------------------------------------------------------------------
    pricing_items: list[dict[str, Any]] = []
    control_ratios: list[float] = []
    raw_price_orders = 0
    raw_price_rev = 0

    if comp.pricing_assessable:
        # Comparable control group: in-stock, orders_prev > 0, fairly priced (<= 3% above comp)
        control_skus = [
            s
            for s in skus
            if not s.is_oos_curr
            and s.orders_prev > 0
            and s.competitor_lowest_price is not None
            and s.competitor_lowest_price > 0
            and ((s.price - s.competitor_lowest_price) / s.competitor_lowest_price) <= 0.03
        ]
        # Prefer stable-visibility controls so ambient impression shock is isolated from pricing
        clean_controls = [
            s
            for s in control_skus
            if not (s.impressions_prev and s.impressions_curr is not None and s.impressions_curr < s.impressions_prev * 0.85)
        ]
        control_cohort = clean_controls if len(clean_controls) >= 3 else control_skus
        control_ratios = [(s.orders_curr - s.orders_prev) / float(s.orders_prev) for s in control_cohort]
        median_ctrl_change = statistics.median(control_ratios) if control_ratios else 0.0
        ambient_trend = min(0.0, median_ctrl_change)

        for s in skus:
            if s.is_oos_curr or s.competitor_lowest_price is None or s.competitor_lowest_price <= 0:
                continue
            gap_rs = round(s.price - s.competitor_lowest_price, 2)
            gap_pct = round((gap_rs / s.competitor_lowest_price) * 100.0, 1)
            if gap_pct <= 3.0 or s.orders_prev <= s.orders_curr:
                continue

            expected_orders = round(s.orders_prev * (1.0 + ambient_trend))
            excess_drop = max(0, expected_orders - s.orders_curr)
            sku_drop = rem_sku_drop[s.sku_id]
            est_lost = min(sku_drop, excess_drop)
            if est_lost <= 0:
                continue

            rem_sku_drop[s.sku_id] = max(0, sku_drop - est_lost)
            raw_price_orders += est_lost
            est_rev = round(est_lost * s.price)
            raw_price_rev += est_rev

            pricing_items.append(
                {
                    "sku_id": s.sku_id,
                    "name": s.name,
                    "seller_price": s.price,
                    "competitor_lowest_price": s.competitor_lowest_price,
                    "price_gap_rs": gap_rs,
                    "price_gap_pct": gap_pct,
                    "last_month_orders": s.orders_prev,
                    "this_month_orders": s.orders_curr,
                    "lost_orders": est_lost,
                    "lost_revenue": est_rev,
                    "suggested_price": _suggested_reprice(s.competitor_lowest_price),
                }
            )
    else:
        median_ctrl_change = 0.0

    price_orders = min(rem_budget_orders, raw_price_orders) if comp.pricing_assessable else 0
    price_rev = min(rem_budget_rev, raw_price_rev) if comp.pricing_assessable else 0
    rem_budget_orders -= price_orders
    rem_budget_rev -= price_rev

    # ------------------------------------------------------------------
    # (c) Visibility Drop: impressions delta * baseline CTR * CVR net of OOS/price
    # ------------------------------------------------------------------
    raw_vis_orders = 0
    raw_vis_rev = 0
    if comp.visibility_assessable:
        per_sku_imp = any(
            s.impressions_prev is not None and s.impressions_curr is not None and s.impressions_prev > 0
            for s in skus
        )
        if per_sku_imp:
            for s in skus:
                if s.impressions_prev is None or s.impressions_curr is None or s.impressions_prev <= 0:
                    continue
                imp_drop = max(0, s.impressions_prev - s.impressions_curr)
                if imp_drop <= 0:
                    continue
                sku_rem = rem_sku_drop[s.sku_id]
                if sku_rem <= 0:
                    continue
                # Baseline order rate per impression == baseline_ctr * baseline_cvr
                base_rate = s.orders_prev / float(s.impressions_prev)
                est_vis = min(sku_rem, round(imp_drop * base_rate))
                if est_vis > 0:
                    rem_sku_drop[s.sku_id] = max(0, sku_rem - est_vis)
                    raw_vis_orders += est_vis
                    raw_vis_rev += round(est_vis * s.price)
        elif funnel is not None and funnel.has_impressions:
            imp_prev = int(funnel.impressions_prev or 0)
            imp_curr = int(funnel.impressions_curr or 0)
            imp_drop = max(0, imp_prev - imp_curr)
            if imp_prev > 0 and imp_drop > 0 and baseline_orders > 0:
                base_rate = baseline_orders / float(imp_prev)
                gross_vis = round(imp_drop * base_rate)
                # Net of share already attributable to newly OOS items
                oos_order_share = (
                    sum(s.orders_prev for s in newly_oos_list) / float(baseline_orders)
                    if baseline_orders
                    else 0.0
                )
                raw_vis_orders = max(0, round(gross_vis * max(0.0, 1.0 - oos_order_share)))
                raw_vis_rev = round(raw_vis_orders * base_asp)

    vis_orders = min(rem_budget_orders, raw_vis_orders) if comp.visibility_assessable else 0
    vis_rev = min(rem_budget_rev, raw_vis_rev) if comp.visibility_assessable else 0
    rem_budget_orders -= vis_orders
    rem_budget_rev -= vis_rev

    # ------------------------------------------------------------------
    # (d) Seller-Caused Cancellations
    # ------------------------------------------------------------------
    raw_cancel_orders = 0
    if comp.cancellations_assessable:
        excess_seller = sum(max(0, r.count_curr - r.count_prev) for r in cancels if r.is_seller_fault)
        raw_cancel_orders = excess_seller

    cancel_orders = min(rem_budget_orders, raw_cancel_orders) if comp.cancellations_assessable else 0
    # Estimate cancellation revenue using remaining unattributed SKU losses or store ASP, strictly capped
    cancel_asp = (rem_budget_rev / rem_budget_orders) if rem_budget_orders > 0 else base_asp
    cancel_rev = (
        min(rem_budget_rev, round(cancel_orders * cancel_asp))
        if comp.cancellations_assessable and cancel_orders > 0
        else 0
    )
    rem_budget_orders -= cancel_orders
    rem_budget_rev -= cancel_rev

    # ------------------------------------------------------------------
    # (e) Unexplained Residual (exact remainder)
    # ------------------------------------------------------------------
    residual_orders = rem_budget_orders
    residual_rev = rem_budget_rev

    # Build confidence scores cleanly
    oos_conf = "high" if comp.has_days_oos else ("medium" if comp.stock_oos_assessable else "unavailable")
    if not comp.pricing_assessable:
        price_conf = "unavailable"
    elif len(control_ratios) >= 4:
        price_conf = "high"
    elif len(control_ratios) >= 2:
        price_conf = "medium"
    else:
        price_conf = "low"
    vis_conf = (
        "high"
        if (comp.visibility_assessable and comp.has_clicks)
        else ("medium" if comp.visibility_assessable else "unavailable")
    )
    cancel_conf = "high" if comp.cancellations_assessable else "unavailable"

    return {
        "comp": comp,
        "skus": skus,
        "cancels": cancels,
        "funnel": funnel,
        "baseline_orders": baseline_orders,
        "current_orders": current_orders,
        "baseline_sales": baseline_sales,
        "current_sales": current_sales,
        "total_lost_orders": total_lost_orders,
        "total_lost_rev": total_lost_rev,
        "oos": {
            "orders": oos_orders,
            "revenue": oos_rev,
            "confidence": oos_conf,
            "newly_oos_count": len(newly_oos_list),
            "chronic_oos_count": len(chronic_oos_list),
            "total_oos_count": len(newly_oos_list) + len(chronic_oos_list),
            "items": sorted(
                oos_items,
                key=lambda x: (-x["estimated_lost_rev_per_day"], -x["lost_revenue_month"]),
            ),
        },
        "pricing": {
            "orders": price_orders,
            "revenue": price_rev,
            "confidence": price_conf,
            "control_size": len(control_ratios),
            "control_median_change_pct": round(median_ctrl_change * 100.0, 1) if control_ratios else 0.0,
            "items": sorted(pricing_items, key=lambda g: (-g["lost_revenue"], -g["price_gap_pct"])),
        },
        "visibility": {
            "orders": vis_orders,
            "revenue": vis_rev,
            "confidence": vis_conf,
        },
        "cancellations": {
            "orders": cancel_orders,
            "revenue": cancel_rev,
            "confidence": cancel_conf,
        },
        "residual": {
            "orders": residual_orders,
            "revenue": residual_rev,
        },
    }


def compute_headline_metrics(context: SellerContext, snapshot: SnapshotType = "current") -> dict[str, Any]:
    """Aggregate SKU-level & operational metrics cleanly from SellerContext."""
    comp = _completeness(context)
    profile = context.profile
    base_skus = context.skus
    curr_skus = context.resolve_skus("current")
    base_cancels = context.resolve_cancellations("current")

    last_orders = sum(s.orders_prev for s in base_skus)
    curr_orders = sum(s.orders_curr for s in curr_skus)
    last_sales = round(sum(s.orders_prev * s.price for s in base_skus))
    curr_sales = round(sum(s.orders_curr * s.price for s in curr_skus))
    last_asp = round(last_sales / last_orders, 2) if last_orders else 0.0
    curr_asp = round(curr_sales / curr_orders, 2) if curr_orders else 0.0

    last_oos = sum(1 for s in base_skus if s.is_oos_prev) if comp.stock_oos_assessable else None
    curr_oos = sum(1 for s in curr_skus if s.is_oos_curr) if comp.stock_oos_assessable else None
    newly_oos = (
        sum(1 for s in curr_skus if (not s.is_oos_prev and s.is_oos_curr))
        if comp.stock_oos_assessable
        else None
    )

    if comp.cancellations_assessable:
        last_cancelled = sum(r.count_prev for r in base_cancels)
        curr_cancelled = sum(r.count_curr for r in base_cancels)
        last_cancel_pct = round((last_cancelled / last_orders) * 100.0, 1) if last_orders else 0.0
        curr_cancel_pct = round((curr_cancelled / curr_orders) * 100.0, 1) if curr_orders else 0.0
        cancel_delta_pp: float | None = round(curr_cancel_pct - last_cancel_pct, 1)
    else:
        last_cancelled = curr_cancelled = None
        last_cancel_pct = curr_cancel_pct = cancel_delta_pp = None

    sales_delta = curr_sales - last_sales
    sales_delta_pct = _pct_change(last_sales, curr_sales)
    orders_delta = curr_orders - last_orders
    asp_delta_pct = _pct_change(last_asp, curr_asp)

    if orders_delta < 0 and abs(asp_delta_pct) <= 3.0:
        finding = (
            f"Average Selling Price stayed nearly flat (Rs {int(round(last_asp))} -> Rs {int(round(curr_asp))}), "
            f"proving the {abs(sales_delta_pct)}% sales drop is primarily a VOLUME issue ({abs(orders_delta)} fewer orders)."
        )
    elif orders_delta < 0:
        finding = (
            f"Orders moved {last_orders} -> {curr_orders} ({_pct_change(last_orders, curr_orders)}%) while ASP shifted "
            f"Rs {int(round(last_asp))} -> Rs {int(round(curr_asp))} ({asp_delta_pct}%)."
        )
    else:
        finding = (
            f"Orders moved {last_orders} -> {curr_orders} ({_pct_change(last_orders, curr_orders)}%) and sales moved "
            f"Rs {last_sales:,} -> Rs {curr_sales:,}."
        )

    result: dict[str, Any] = {
        "snapshot": snapshot,
        "baseline_label": profile.period_prev_label,
        "current_label": profile.period_curr_label,
        "completeness": comp.to_dict(),
        "metrics": {
            "sales_gmv": {
                "label": "Total Sales (Placed GMV)",
                "unit": "INR",
                "baseline": last_sales,
                "current": curr_sales,
                "delta": sales_delta,
                "delta_pct": sales_delta_pct,
            },
            "orders": {
                "label": "Total Orders Placed",
                "unit": "orders",
                "baseline": last_orders,
                "current": curr_orders,
                "delta": orders_delta,
                "delta_pct": _pct_change(last_orders, curr_orders),
            },
            "asp": {
                "label": "Average Selling Price (ASP)",
                "unit": "INR",
                "baseline": int(round(last_asp)),
                "current": int(round(curr_asp)),
                "delta": int(round(curr_asp - last_asp)),
                "delta_pct": asp_delta_pct,
            },
            "cancellation_rate_pct": {
                "label": "Cancellation Rate",
                "unit": "%",
                "assessable": comp.cancellations_assessable,
                "baseline": last_cancel_pct,
                "current": curr_cancel_pct,
                "baseline_count": last_cancelled,
                "current_count": curr_cancelled,
                "delta_pp": cancel_delta_pp,
                "status": "assessed" if comp.cancellations_assessable else UNAVAILABLE_MSG,
            },
            "out_of_stock_skus": {
                "label": "Out-of-Stock Products",
                "unit": "products",
                "assessable": comp.stock_oos_assessable,
                "baseline": last_oos,
                "current": curr_oos,
                "delta": (curr_oos - last_oos) if (curr_oos is not None and last_oos is not None) else None,
                "newly_oos": newly_oos,
                "status": "assessed" if comp.stock_oos_assessable else UNAVAILABLE_MSG,
            },
        },
        "volume_vs_price_finding": finding,
    }

    if snapshot == "followup" and context.followup is not None:
        fu = context.followup
        fu_skus = context.resolve_skus("followup")
        fu_orders_pace = sum(s.orders_curr for s in fu_skus)
        fu_sales_pace = round(sum(s.orders_curr * s.price for s in fu_skus))
        fu_asp = int(round(fu_sales_pace / fu_orders_pace)) if fu_orders_pace else int(round(curr_asp))
        fu_cancels = context.resolve_cancellations("followup")
        fu_cancel_pct = (
            round((sum(r.count_curr for r in fu_cancels) / fu_orders_pace) * 100.0, 1)
            if (comp.cancellations_assessable and fu_orders_pace)
            else None
        )
        fu_oos = sum(1 for s in fu_skus if s.is_oos_curr) if comp.stock_oos_assessable else None
        fu_funnel = context.resolve_funnel("followup")
        imp_pace = (
            round((fu_funnel.impressions_curr or 0) / 30.0)
            if (fu_funnel and fu_funnel.impressions_curr is not None)
            else None
        )

        result["followup_label"] = fu.label
        result["followup"] = {
            "sales_gmv_pace": fu_sales_pace,
            "projected_monthly_sales": fu_sales_pace,
            "sales_vs_crisis_pct": _pct_change(curr_sales, fu_sales_pace),
            "orders_pace": fu_orders_pace,
            "projected_monthly_orders": fu_orders_pace,
            "orders_vs_crisis_pct": _pct_change(curr_orders, fu_orders_pace),
            "asp": fu_asp,
            "out_of_stock_skus": fu_oos,
            "oos_target_max": fu.oos_target_max,
            "cancellation_rate_pct": fu_cancel_pct,
            "cancellation_target_max": fu.cancellation_target_pct_max,
            "impressions_daily_pace": imp_pace,
        }

    return result


def get_seller_overview(context: SellerContext, snapshot: SnapshotType = "current") -> dict[str, Any]:
    headline = compute_headline_metrics(context, snapshot)
    m = headline["metrics"]
    return {
        "seller": context.profile.to_dict(sku_count=len(context.skus)),
        "snapshot": snapshot,
        "completeness": headline["completeness"],
        "headline_summary": {
            "sales_last_month": m["sales_gmv"]["baseline"],
            "sales_this_month": m["sales_gmv"]["current"],
            "sales_drop_pct": abs(m["sales_gmv"]["delta_pct"]),
            "orders_last_month": m["orders"]["baseline"],
            "orders_this_month": m["orders"]["current"],
            "orders_drop": abs(m["orders"]["delta"]),
            "asp": m["asp"]["current"],
            "oos_last_month": m["out_of_stock_skus"]["baseline"],
            "oos_this_month": m["out_of_stock_skus"]["current"],
            "cancellation_rate_last_month_pct": m["cancellation_rate_pct"]["baseline"],
            "cancellation_rate_this_month_pct": m["cancellation_rate_pct"]["current"],
        },
        "followup_metrics": headline.get("followup"),
    }


def get_out_of_stock_skus(
    context: SellerContext, limit: int = 6, snapshot: SnapshotType = "current"
) -> dict[str, Any]:
    """Return OOS SKUs derived strictly from resolve_skus(snapshot)."""
    core = _compute_attribution_core(context, snapshot)
    comp: DataCompleteness = core["comp"]
    if not comp.stock_oos_assessable:
        return {
            "snapshot": snapshot,
            "assessable": False,
            "status": UNAVAILABLE_MSG,
            "reason_unavailable": "No days_out_of_stock_curr or stock_qty_curr column was provided.",
            "total_oos_count": None,
            "newly_oos_count": None,
            "chronic_oos_count": None,
            "total_oos_lost_orders": 0,
            "total_oos_lost_revenue": 0,
            "top_oos_skus": [],
        }

    oos = core["oos"]
    ranked = oos["items"]
    payload: dict[str, Any] = {
        "snapshot": snapshot,
        "assessable": True,
        "status": "assessed",
        "confidence": oos["confidence"],
        "total_oos_count": oos["total_oos_count"],
        "newly_oos_count": oos["newly_oos_count"],
        "chronic_oos_count": oos["chronic_oos_count"],
        "total_oos_lost_orders": oos["orders"],
        "total_oos_lost_revenue": oos["revenue"],
        "top_oos_skus": ranked[:limit],
    }
    if snapshot == "followup" and context.followup is not None:
        restocked_list = [
            f"{sid} ({next((s.name for s in context.skus if s.sku_id == sid), sid)})"
            for sid in context.followup.restocked_skus
        ]
        payload["recently_restocked"] = restocked_list
        newly_remaining = [item for item in ranked if item["newly_oos"]] or ranked
        if newly_remaining:
            top_rem = newly_remaining[0]
            payload["remaining_priority_blocker"] = {
                "sku_id": top_rem["sku_id"],
                "name": top_rem["name"],
                "price": top_rem["price"],
                "estimated_daily_loss": top_rem["estimated_lost_rev_per_day"],
                "blocker": context.followup.blocker_notes.get(top_rem["sku_id"], "Restock pending"),
            }
    return payload


def get_cancellation_breakdown(
    context: SellerContext, snapshot: SnapshotType = "current"
) -> dict[str, Any]:
    comp = _completeness(context)
    if not comp.cancellations_assessable:
        return {
            "snapshot": snapshot,
            "assessable": False,
            "status": UNAVAILABLE_MSG,
            "reason_unavailable": "No cancellations breakdown table was provided.",
            "baseline_cancellation_rate_pct": None,
            "current_cancellation_rate_pct": None,
            "baseline_cancelled_orders": None,
            "current_cancelled_orders": None,
            "reasons": [],
            "key_insight": f"Cancellations are {UNAVAILABLE_MSG}.",
        }

    cancels = context.resolve_cancellations(snapshot)
    last_orders = sum(s.orders_prev for s in context.skus)
    curr_orders = sum(s.orders_curr for s in context.resolve_skus(snapshot))
    last_total = sum(r.count_prev for r in cancels)
    curr_total = sum(r.count_curr for r in cancels)
    last_pct = round((last_total / last_orders) * 100.0, 1) if last_orders else 0.0
    curr_pct = round((curr_total / curr_orders) * 100.0, 1) if curr_orders else 0.0

    reasons = []
    for row in cancels:
        last_rate = round((row.count_prev / last_orders) * 100.0, 2) if last_orders else 0.0
        curr_rate = round((row.count_curr / curr_orders) * 100.0, 2) if curr_orders else 0.0
        reasons.append(
            {
                "reason_key": row.reason_key,
                "label": row.label,
                "is_seller_fault": row.is_seller_fault,
                "last_month_count": row.count_prev,
                "this_month_count": row.count_curr,
                "delta_count": row.count_curr - row.count_prev,
                "last_month_rate_pct": last_rate,
                "this_month_rate_pct": curr_rate,
            }
        )

    seller_spikes = [r for r in reasons if r["is_seller_fault"] and r["delta_count"] > 0]
    insight = (
        "; ".join(
            f"{r['label']} moved ({r['last_month_count']} -> {r['this_month_count']})"
            for r in seller_spikes
        )
        or f"Cancellation rate across catalog orders: {last_pct}% -> {curr_pct}%."
    )

    payload: dict[str, Any] = {
        "snapshot": snapshot,
        "assessable": True,
        "status": "assessed",
        "baseline_cancellation_rate_pct": last_pct,
        "current_cancellation_rate_pct": curr_pct,
        "baseline_cancelled_orders": last_total,
        "current_cancelled_orders": curr_total,
        "reasons": reasons,
        "key_insight": insight,
    }
    if snapshot == "followup" and context.followup is not None:
        payload["followup_cancellation_rate_pct"] = curr_pct
        payload["followup_note"] = (
            f"Cancellation rate is {curr_pct}% (target <= {context.followup.cancellation_target_pct_max}%)."
        )
    return payload


def get_pricing_gaps(context: SellerContext, snapshot: SnapshotType = "current") -> dict[str, Any]:
    core = _compute_attribution_core(context, snapshot)
    comp: DataCompleteness = core["comp"]
    if not comp.pricing_assessable:
        return {
            "snapshot": snapshot,
            "assessable": False,
            "status": UNAVAILABLE_MSG,
            "reason_unavailable": "No competitor_lowest_price column was provided.",
            "overpriced_sku_count": 0,
            "total_pricing_lost_orders": 0,
            "total_pricing_lost_revenue": 0,
            "pricing_gaps": [],
            "key_insight": f"Competitor pricing gaps are {UNAVAILABLE_MSG}.",
        }

    pricing = core["pricing"]
    gaps = pricing["items"]
    payload: dict[str, Any] = {
        "snapshot": snapshot,
        "assessable": True,
        "status": "assessed",
        "confidence": pricing["confidence"],
        "control_group_size": pricing["control_size"],
        "control_median_change_pct": pricing["control_median_change_pct"],
        "overpriced_sku_count": len(gaps),
        "total_pricing_lost_orders": pricing["orders"],
        "total_pricing_lost_revenue": pricing["revenue"],
        "pricing_gaps": gaps,
        "key_insight": (
            f"{len(gaps)} in-stock SKU(s) are priced >3% above competitor offers (vs {pricing['control_size']} control SKUs), "
            f"accounting for roughly {pricing['orders']} lost orders (Rs {pricing['revenue']:,})."
            if gaps
            else "No in-stock SKUs showed >3% competitor price gaps beyond control trends."
        ),
    }
    if snapshot == "followup" and context.followup is not None:
        payload["repriced_skus"] = list(context.followup.repriced_skus)
        if gaps:
            top_g = gaps[0]
            payload["remaining_overpriced_sku"] = {
                "sku_id": top_g["sku_id"],
                "name": top_g["name"],
                "current_price": top_g["seller_price"],
                "competitor_lowest": top_g["competitor_lowest_price"],
                "gap_pct": top_g["price_gap_pct"],
                "suggested_price": top_g["suggested_price"],
            }
    return payload


def get_visibility_funnel(context: SellerContext, snapshot: SnapshotType = "current") -> dict[str, Any]:
    comp = _completeness(context)
    funnel = context.resolve_funnel(snapshot)
    if not comp.visibility_assessable or funnel is None or not funnel.has_impressions:
        return {
            "snapshot": snapshot,
            "assessable": False,
            "status": UNAVAILABLE_MSG,
            "reason_unavailable": "No impressions_prev / impressions_curr data was provided.",
            "last_month": None,
            "this_month": None,
            "changes": {},
            "key_insight": f"Search visibility funnel is {UNAVAILABLE_MSG}.",
        }

    orders_prev = sum(s.orders_prev for s in context.skus)
    orders_curr = sum(s.orders_curr for s in context.resolve_skus(snapshot))
    imp_prev = int(funnel.impressions_prev or 0)
    imp_curr = int(funnel.impressions_curr or 0)

    if funnel.has_clicks:
        clk_prev = int(funnel.clicks_prev or 0)
        clk_curr = int(funnel.clicks_curr or 0)
        last_ctr = round((clk_prev / imp_prev) * 100.0, 2) if imp_prev else 0.0
        curr_ctr = round((clk_curr / imp_curr) * 100.0, 2) if imp_curr else 0.0
        last_cvr = round((orders_prev / clk_prev) * 100.0, 2) if clk_prev else 0.0
        curr_cvr = round((orders_curr / clk_curr) * 100.0, 2) if clk_curr else 0.0
    else:
        clk_prev = clk_curr = None
        last_ctr = curr_ctr = last_cvr = curr_cvr = None

    changes: dict[str, Any] = {
        "impressions_pct": _pct_change(imp_prev, imp_curr),
        "orders_pct": _pct_change(orders_prev, orders_curr),
    }
    if clk_prev is not None and clk_curr is not None:
        changes["clicks_pct"] = _pct_change(clk_prev, clk_curr)
        changes["ctr_delta_pp"] = round((curr_ctr or 0.0) - (last_ctr or 0.0), 2)
        changes["cvr_delta_pp"] = round((curr_cvr or 0.0) - (last_cvr or 0.0), 2)

    payload: dict[str, Any] = {
        "snapshot": snapshot,
        "assessable": True,
        "status": "assessed",
        "last_month": {
            "impressions": imp_prev,
            "clicks": clk_prev,
            "orders": orders_prev,
            "ctr_pct": last_ctr,
            "cvr_pct": last_cvr,
        },
        "this_month": {
            "impressions": imp_curr,
            "clicks": clk_curr,
            "orders": orders_curr,
            "ctr_pct": curr_ctr,
            "cvr_pct": curr_cvr,
        },
        "changes": changes,
        "key_insight": (
            f"Search impressions moved {changes['impressions_pct']}% ({imp_prev:,} -> {imp_curr:,})"
            + (f" while clicks moved {changes['clicks_pct']}%." if "clicks_pct" in changes else ".")
        ),
    }
    if snapshot == "followup":
        payload["followup_impressions_daily_pace"] = round(imp_curr / 30.0)
    return payload


def run_diagnosis(context: SellerContext, snapshot: SnapshotType = "current") -> dict[str, Any]:
    """Compute counterfactual & disjoint driver attribution strictly from SellerContext."""
    core = _compute_attribution_core(context, snapshot)
    comp: DataCompleteness = core["comp"]
    headline = compute_headline_metrics(context, snapshot)
    cancels = get_cancellation_breakdown(context, snapshot=snapshot)
    funnel = get_visibility_funnel(context, snapshot=snapshot)

    total_lost_orders: int = core["total_lost_orders"]
    total_lost_rev: int = core["total_lost_rev"]

    def _share(val: int) -> float:
        return round((val / total_lost_orders) * 100.0, 1) if total_lost_orders else 0.0

    oos = core["oos"]
    pricing = core["pricing"]
    vis = core["visibility"]
    canc = core["cancellations"]
    resid = core["residual"]

    drivers: list[dict[str, Any]] = []

    if comp.stock_oos_assessable:
        top_names = ", ".join(item["name"] for item in oos["items"][:2])
        drivers.append(
            {
                "driver": "stock_outs",
                "title": "Out-of-Stock Products",
                "assessable": True,
                "confidence": oos["confidence"],
                "lost_orders": oos["orders"],
                "lost_revenue": oos["revenue"],
                "share_pct": _share(oos["orders"]),
                "evidence": (
                    f"{oos['newly_oos_count']} newly OOS SKUs (total OOS {oos['total_oos_count']}); "
                    + (f"top impacted: {top_names}." if top_names else "derived from daily velocity x days out of stock.")
                ),
            }
        )
    else:
        drivers.append(
            {
                "driver": "stock_outs",
                "title": "Out-of-Stock Products",
                "assessable": False,
                "confidence": "unavailable",
                "lost_orders": 0,
                "lost_revenue": 0,
                "share_pct": 0.0,
                "evidence": f"{UNAVAILABLE_MSG} (missing days_out_of_stock_curr / stock_qty_curr).",
            }
        )

    if comp.pricing_assessable:
        gap_count = len(pricing["items"])
        drivers.append(
            {
                "driver": "pricing_gaps",
                "title": "Competitor Price Gaps",
                "assessable": True,
                "confidence": pricing["confidence"],
                "lost_orders": pricing["orders"],
                "lost_revenue": pricing["revenue"],
                "share_pct": _share(pricing["orders"]),
                "evidence": (
                    f"{gap_count} overpriced active SKU(s) compared against {pricing['control_size']} fairly-priced control SKUs "
                    f"(control median {pricing['control_median_change_pct']:+.1f}%)."
                    if gap_count
                    else f"No active overpriced SKUs exceeded the {pricing['control_size']}-SKU control benchmark."
                ),
            }
        )
    else:
        drivers.append(
            {
                "driver": "pricing_gaps",
                "title": "Competitor Price Gaps",
                "assessable": False,
                "confidence": "unavailable",
                "lost_orders": 0,
                "lost_revenue": 0,
                "share_pct": 0.0,
                "evidence": f"{UNAVAILABLE_MSG} (missing competitor_lowest_price).",
            }
        )

    if comp.visibility_assessable:
        drivers.append(
            {
                "driver": "visibility_drop",
                "title": "Search Visibility & Funnel Drop",
                "assessable": True,
                "confidence": vis["confidence"],
                "lost_orders": vis["orders"],
                "lost_revenue": vis["revenue"],
                "share_pct": _share(vis["orders"]),
                "evidence": (
                    f"Impressions shifted {funnel['changes'].get('impressions_pct', 0)}% net of OOS & pricing overlaps."
                ),
            }
        )
    else:
        drivers.append(
            {
                "driver": "visibility_drop",
                "title": "Search Visibility & Funnel Drop",
                "assessable": False,
                "confidence": "unavailable",
                "lost_orders": 0,
                "lost_revenue": 0,
                "share_pct": 0.0,
                "evidence": f"{UNAVAILABLE_MSG} (missing impressions_prev / impressions_curr).",
            }
        )

    if comp.cancellations_assessable:
        drivers.append(
            {
                "driver": "cancellations",
                "title": "Seller-Caused Cancellations & SLA",
                "assessable": True,
                "confidence": canc["confidence"],
                "lost_orders": canc["orders"],
                "lost_revenue": canc["revenue"],
                "share_pct": _share(canc["orders"]),
                "evidence": cancels["key_insight"],
            }
        )
    else:
        drivers.append(
            {
                "driver": "cancellations",
                "title": "Seller-Caused Cancellations & SLA",
                "assessable": False,
                "confidence": "unavailable",
                "lost_orders": 0,
                "lost_revenue": 0,
                "share_pct": 0.0,
                "evidence": f"{UNAVAILABLE_MSG} (missing cancellations breakdown table).",
            }
        )

    # Always include explicit unexplained residual so sum(drivers) == total_lost_orders / total_lost_revenue
    residual_reason = (
        f"Unmeasured or residual variance not explained by stock, price, visibility, or seller cancellations"
        + (f" (missing: {', '.join(comp.missing_fields)})." if comp.missing_fields else ".")
    )
    drivers.append(
        {
            "driver": "unexplained_residual",
            "title": "Unexplained Residual",
            "assessable": True,
            "confidence": "low",
            "lost_orders": resid["orders"],
            "lost_revenue": resid["revenue"],
            "share_pct": _share(resid["orders"]),
            "evidence": residual_reason,
        }
    )

    # Rank assessed causal drivers by impact descending, followed by unexplained_residual, then unassessed
    causal_assessed = sorted(
        [d for d in drivers if d["assessable"] and d["driver"] != "unexplained_residual"],
        key=lambda d: (-d["lost_orders"], -d["lost_revenue"]),
    )
    residual_list = [d for d in drivers if d["driver"] == "unexplained_residual"]
    unassessed = [d for d in drivers if not d["assessable"]]
    ranked_drivers = [
        {**d, "rank": idx + 1}
        for idx, d in enumerate(causal_assessed + residual_list + unassessed)
    ]

    result: dict[str, Any] = {
        "snapshot": snapshot,
        "problem_type": "volume_driven_decline" if total_lost_orders > 0 else "stable_or_growing",
        "total_lost_orders": total_lost_orders,
        "total_lost_revenue": total_lost_rev,
        "unexplained_residual": {
            "lost_orders": resid["orders"],
            "lost_revenue": resid["revenue"],
            "share_pct": _share(resid["orders"]),
        },
        "asp_flat": abs(headline["metrics"]["asp"]["delta_pct"]) <= 3.0,
        "completeness": comp.to_dict(),
        "missing_signals": list(comp.missing_fields),
        "drivers": ranked_drivers,
        "already_answered_questions": [
            headline["volume_vs_price_finding"],
            cancels["key_insight"],
        ],
        "assumptions": [
            "Stock-out impact = baseline daily order rate x days OOS, net of baseline chronic OOS.",
            "Pricing impact = excess order drop vs comparable fairly-priced in-stock control SKUs.",
            "Visibility impact = impression decline x baseline order/impression rate, net of OOS & pricing overlaps.",
            "Drivers + unexplained_residual always reconcile strictly to total order and revenue declines.",
        ],
    }

    if snapshot == "followup" and context.followup is not None:
        result["followup_comparison"] = {
            "days_elapsed": context.followup.days_elapsed,
            "prior_commitments": list(context.followup.default_prior_commitments),
            "metrics": headline.get("followup"),
            "targets": {
                "out_of_stock_skus_max": context.followup.oos_target_max,
                "cancellation_rate_pct_max": context.followup.cancellation_target_pct_max,
                "monthly_sales_pace_target": context.followup.monthly_sales_pace_target,
            },
        }
    return result


def build_action_plan(context: SellerContext, snapshot: SnapshotType = "current") -> dict[str, Any]:
    """Generate prioritized recovery plan ranked dynamically by diagnosed impact."""
    owner_name = context.profile.seller_name
    diag = run_diagnosis(context, snapshot)
    oos = get_out_of_stock_skus(context, limit=5, snapshot=snapshot)
    pricing = get_pricing_gaps(context, snapshot=snapshot)
    cancels = get_cancellation_breakdown(context, snapshot=snapshot)
    funnel = get_visibility_funnel(context, snapshot=snapshot)
    comp = _completeness(context)

    candidates: list[tuple[int, dict[str, Any]]] = []

    if comp.stock_oos_assessable and (oos.get("top_oos_skus") or []):
        top_skus = [s for s in oos["top_oos_skus"] if s.get("newly_oos")] or oos["top_oos_skus"]
        top_skus = top_skus[:5]
        sku_ids = [s["sku_id"] for s in top_skus]
        sku_names = ", ".join(f"{s['name']} ({s['sku_id']})" for s in top_skus[:3])
        impact_rev = max(1000, int(oos.get("total_oos_lost_revenue") or 0))
        low_est = round(impact_rev * 0.70, -2)
        high_est = round(impact_rev * 0.90, -2)
        candidates.append(
            (
                impact_rev,
                {
                    "action_id": "ACT-FU-1" if snapshot == "followup" else "ACT-1",
                    "title": f"Restock priority OOS products ({', '.join(sku_ids[:3])}) and enable low-stock alerts",
                    "owner": owner_name,
                    "effort": "Medium",
                    "impact": "High",
                    "target_skus": sku_ids,
                    "estimated_recovery_range_rs": f"Rs {int(low_est):,} - Rs {int(high_est):,}/month",
                    "recommended_due": "Within 48 hours",
                    "details": f"Top impacted OOS items: {sku_names}. Restocking restores baseline daily order pace.",
                },
            )
        )

    if comp.pricing_assessable and (pricing.get("pricing_gaps") or []):
        gaps = pricing["pricing_gaps"][:3]
        gap_ids = [g["sku_id"] for g in gaps]
        gap_desc = ", ".join(
            f"{g['name']} (Rs {int(g['seller_price'])} -> ~Rs {int(g['suggested_price'])})"
            for g in gaps
        )
        impact_rev = max(1000, int(pricing.get("total_pricing_lost_revenue") or 0))
        candidates.append(
            (
                impact_rev,
                {
                    "action_id": "ACT-FU-3" if snapshot == "followup" else "ACT-2",
                    "title": f"Align prices within ~2% of lowest competitor offer on {len(gaps)} active SKU(s)",
                    "owner": owner_name,
                    "effort": "Low",
                    "impact": "Medium-High",
                    "target_skus": gap_ids,
                    "estimated_recovery_range_rs": f"Rs {int(round(impact_rev * 0.60, -2)):,} - Rs {int(round(impact_rev * 0.90, -2)):,}/month",
                    "recommended_due": "Within 3 days",
                    "details": f"Adjust pricing on {gap_desc}, or test a limited promotional offer subject to eligibility.",
                },
            )
        )

    if comp.cancellations_assessable:
        curr_pct = cancels.get("current_cancellation_rate_pct") or 0.0
        target_pct = context.followup.cancellation_target_pct_max if context.followup else 10.0
        canc_driver = next((d for d in diag["drivers"] if d["driver"] == "cancellations"), None)
        impact_rev = max(800, int((canc_driver or {}).get("lost_revenue") or 0))
        candidates.append(
            (
                impact_rev,
                {
                    "action_id": "ACT-FU-2" if snapshot == "followup" else "ACT-3",
                    "title": f"Sync warehouse inventory twice daily and enforce dispatch cutoff (target <= {target_pct}%)",
                    "owner": owner_name,
                    "effort": "Low",
                    "impact": "High" if impact_rev > 10000 else "Medium",
                    "target_skus": ["Storewide Inventory Sync", "Dispatch Queue"],
                    "estimated_recovery_range_rs": f"Rs {int(round(impact_rev * 0.55, -2)):,} - Rs {int(round(impact_rev * 0.85, -2)):,}/month",
                    "recommended_due": "Starting today",
                    "details": f"Current cancellation rate is {curr_pct}%. Reconcile safety stock before pickup cutoff to avoid seller-fault penalties.",
                },
            )
        )

    if comp.visibility_assessable and (funnel.get("changes", {}).get("impressions_pct", 0) or 0) <= -12.0:
        vis_driver = next((d for d in diag["drivers"] if d["driver"] == "visibility_drop"), None)
        impact_rev = max(500, int((vis_driver or {}).get("lost_revenue") or 0))
        if impact_rev > 1500:
            candidates.append(
                (
                    impact_rev,
                    {
                        "action_id": "ACT-4",
                        "title": "Refresh search keywords, catalog titles, and sponsored visibility on in-stock listings",
                        "owner": owner_name,
                        "effort": "Medium",
                        "impact": "Medium-High",
                        "target_skus": ["Low-Impression Active SKUs"],
                        "estimated_recovery_range_rs": f"Rs {int(round(impact_rev * 0.45, -2)):,} - Rs {int(round(impact_rev * 0.80, -2)):,}/month",
                        "recommended_due": "Within 5 days",
                        "details": "Search impressions dropped significantly; optimize product attributes and consider sponsored placement subject to eligibility.",
                    },
                )
            )

    candidates.sort(key=lambda pair: -pair[0])
    actions = [{**item, "priority": idx + 1} for idx, (_, item) in enumerate(candidates[:3])]

    if not actions:
        actions.append(
            {
                "action_id": "ACT-1",
                "priority": 1,
                "title": "Audit top declining SKUs and supply missing operational columns",
                "owner": owner_name,
                "effort": "Low",
                "impact": "Medium",
                "target_skus": [s.sku_id for s in sorted(context.skus, key=lambda x: x.orders_curr - x.orders_prev)[:3]],
                "estimated_recovery_range_rs": "Estimate unavailable until operational columns are provided",
                "recommended_due": "Within 48 hours",
                "details": (
                    "Optional stock, competitor price, visibility, and cancellation columns were not provided; "
                    "audit availability and pricing directly on your highest-volume declining listings."
                ),
            }
        )

    total_lost_rev = max(0, diag["total_lost_revenue"])
    return {
        "snapshot": snapshot,
        "actions": actions,
        "estimated_combined_recovery_rs": (
            f"Rs {int(round(total_lost_rev * 0.55, -2)):,} - Rs {int(round(total_lost_rev * 0.88, -2)):,}/month (indicative range)"
            if total_lost_rev > 0
            else "Maintains baseline stability"
        ),
        "assumptions": (
            "Indicative recovery ranges assume normal marketplace demand, restored listing visibility after restocking, "
            "and competitive fulfillment SLAs. Not a guaranteed outcome; any promotions or ads are subject to eligibility."
        ),
    }

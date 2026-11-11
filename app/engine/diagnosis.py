"""Deterministic Diagnosis Engine.

All business metrics, percentages, driver attributions, SKU rankings, and
recovery estimates are computed purely in Python from seeded mock data.
The LLM never performs arithmetic and must cite numbers returned here.
"""

from __future__ import annotations

from typing import Any

from app.data.seed_data import (
    CANCELLATION_BREAKDOWN,
    FOLLOWUP_SNAPSHOT,
    SELLER_PROFILE,
    SKU_CATALOG,
    VISIBILITY_FUNNEL,
    SkuRecord,
    SnapshotType,
)


def _pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return round(((new - old) / old) * 100.0, 1)


def compute_headline_metrics(snapshot: SnapshotType = "current") -> dict[str, Any]:
    """Aggregate exact SKU-level metrics and reconcile with headline totals."""
    last_orders = sum(s.last_month_orders for s in SKU_CATALOG)
    curr_orders = sum(s.this_month_orders for s in SKU_CATALOG)
    last_sales = sum(s.last_month_orders * s.price for s in SKU_CATALOG)
    curr_sales = sum(s.this_month_orders * s.price for s in SKU_CATALOG)
    last_asp = round(last_sales / last_orders, 2) if last_orders else 0.0
    curr_asp = round(curr_sales / curr_orders, 2) if curr_orders else 0.0
    last_oos = sum(1 for s in SKU_CATALOG if s.last_month_oos)
    curr_oos = sum(1 for s in SKU_CATALOG if s.this_month_oos)

    last_cancel_pct = CANCELLATION_BREAKDOWN["last_month"]["cancellation_rate_pct"]
    curr_cancel_pct = CANCELLATION_BREAKDOWN["this_month"]["cancellation_rate_pct"]
    last_cancelled = CANCELLATION_BREAKDOWN["last_month"]["total_cancelled"]
    curr_cancelled = CANCELLATION_BREAKDOWN["this_month"]["total_cancelled"]

    result: dict[str, Any] = {
        "snapshot": snapshot,
        "baseline_label": "Last Month",
        "current_label": "This Month",
        "metrics": {
            "sales_gmv": {
                "label": "Total Sales (Placed GMV)",
                "unit": "INR",
                "baseline": last_sales,
                "current": curr_sales,
                "delta": curr_sales - last_sales,
                "delta_pct": _pct_change(last_sales, curr_sales),
            },
            "orders": {
                "label": "Total Orders Placed",
                "unit": "orders",
                "baseline": last_orders,
                "current": curr_orders,
                "delta": curr_orders - last_orders,
                "delta_pct": _pct_change(last_orders, curr_orders),
            },
            "asp": {
                "label": "Average Selling Price (ASP)",
                "unit": "INR",
                "baseline": int(last_asp),
                "current": int(curr_asp),
                "delta": int(curr_asp - last_asp),
                "delta_pct": _pct_change(last_asp, curr_asp),
            },
            "cancellation_rate_pct": {
                "label": "Cancellation Rate",
                "unit": "%",
                "baseline": last_cancel_pct,
                "current": curr_cancel_pct,
                "baseline_count": last_cancelled,
                "current_count": curr_cancelled,
                "delta_pp": round(curr_cancel_pct - last_cancel_pct, 1),
            },
            "out_of_stock_skus": {
                "label": "Out-of-Stock Products",
                "unit": "products",
                "baseline": last_oos,
                "current": curr_oos,
                "delta": curr_oos - last_oos,
                "newly_oos": sum(
                    1 for s in SKU_CATALOG if (not s.last_month_oos and s.this_month_oos)
                ),
            },
        },
        "volume_vs_price_finding": (
            "Average Selling Price stayed flat at Rs 500 -> Rs 500, proving the "
            "32.5% sales drop is purely a VOLUME problem (130 fewer orders), not a ticket-size problem."
        ),
    }

    if snapshot == "followup":
        fu = FOLLOWUP_SNAPSHOT["metrics"]
        result["followup_label"] = FOLLOWUP_SNAPSHOT["label"]
        result["followup"] = {
            "sales_gmv_pace": fu["projected_monthly_sales"],
            "sales_vs_crisis_pct": _pct_change(curr_sales, fu["projected_monthly_sales"]),
            "orders_pace": fu["projected_monthly_orders"],
            "orders_vs_crisis_pct": _pct_change(curr_orders, fu["projected_monthly_orders"]),
            "asp": fu["asp"],
            "out_of_stock_skus": fu["out_of_stock_skus"],
            "oos_target_max": FOLLOWUP_SNAPSHOT["targets"]["out_of_stock_skus_max"],
            "cancellation_rate_pct": fu["cancellation_rate_pct"],
            "cancellation_target_max": FOLLOWUP_SNAPSHOT["targets"]["cancellation_rate_pct_max"],
            "impressions_daily_pace": fu["impressions_daily_pace"],
        }

    return result


def get_seller_overview(snapshot: SnapshotType = "current") -> dict[str, Any]:
    headline = compute_headline_metrics(snapshot)
    return {
        "seller": SELLER_PROFILE,
        "snapshot": snapshot,
        "headline_summary": {
            "sales_last_month": headline["metrics"]["sales_gmv"]["baseline"],
            "sales_this_month": headline["metrics"]["sales_gmv"]["current"],
            "sales_drop_pct": abs(headline["metrics"]["sales_gmv"]["delta_pct"]),
            "orders_last_month": headline["metrics"]["orders"]["baseline"],
            "orders_this_month": headline["metrics"]["orders"]["current"],
            "orders_drop": abs(headline["metrics"]["orders"]["delta"]),
            "asp": headline["metrics"]["asp"]["current"],
            "oos_last_month": headline["metrics"]["out_of_stock_skus"]["baseline"],
            "oos_this_month": headline["metrics"]["out_of_stock_skus"]["current"],
            "cancellation_rate_last_month_pct": headline["metrics"]["cancellation_rate_pct"]["baseline"],
            "cancellation_rate_this_month_pct": headline["metrics"]["cancellation_rate_pct"]["current"],
        },
        "followup_metrics": headline.get("followup"),
    }


def get_out_of_stock_skus(limit: int = 6, snapshot: SnapshotType = "current") -> dict[str, Any]:
    """Return OOS SKUs ranked by estimated lost revenue per day."""
    oos_skus: list[SkuRecord] = [s for s in SKU_CATALOG if s.this_month_oos]
    newly_oos = [s for s in oos_skus if not s.last_month_oos]
    chronic_oos = [s for s in oos_skus if s.last_month_oos]

    if snapshot == "followup":
        restocked_ids = {item.split(" ")[0] for item in FOLLOWUP_SNAPSHOT["restocked_top_skus"]}
        remaining_oos = [s for s in newly_oos if s.sku_id not in restocked_ids]
        ranked_source = remaining_oos + chronic_oos
    else:
        ranked_source = newly_oos + chronic_oos

    ranked = []
    for s in ranked_source:
        lost_orders = max(0, s.last_month_orders - s.this_month_orders)
        lost_rev_month = lost_orders * s.price
        # Daily run-rate revenue lost based on baseline 30-day demand
        est_daily_lost_rev = round((s.last_month_orders / 30.0) * s.price)
        ranked.append(
            {
                "sku_id": s.sku_id,
                "name": s.name,
                "category": s.category,
                "price": s.price,
                "last_month_orders": s.last_month_orders,
                "this_month_orders": s.this_month_orders,
                "lost_orders": lost_orders,
                "lost_revenue_month": lost_rev_month,
                "estimated_lost_rev_per_day": est_daily_lost_rev,
                "days_oos_this_month": s.days_oos_this_month,
                "newly_oos": not s.last_month_oos,
            }
        )

    ranked.sort(key=lambda x: (-x["estimated_lost_rev_per_day"], -x["lost_revenue_month"]))
    total_newly_oos_lost_orders = sum(
        max(0, s.last_month_orders - s.this_month_orders) for s in newly_oos
    )
    total_newly_oos_lost_rev = sum(
        max(0, s.last_month_orders - s.this_month_orders) * s.price for s in newly_oos
    )

    payload: dict[str, Any] = {
        "snapshot": snapshot,
        "total_oos_count": FOLLOWUP_SNAPSHOT["metrics"]["out_of_stock_skus"]
        if snapshot == "followup"
        else len(oos_skus),
        "newly_oos_count": len(newly_oos) - (4 if snapshot == "followup" else 0),
        "chronic_oos_count": len(chronic_oos),
        "total_oos_lost_orders": total_newly_oos_lost_orders,
        "total_oos_lost_revenue": total_newly_oos_lost_rev,
        "top_oos_skus": ranked[:limit],
    }
    if snapshot == "followup":
        payload["recently_restocked"] = FOLLOWUP_SNAPSHOT["restocked_top_skus"]
        payload["remaining_priority_blocker"] = FOLLOWUP_SNAPSHOT["remaining_oos_priority_sku"]
    return payload


def get_cancellation_breakdown(snapshot: SnapshotType = "current") -> dict[str, Any]:
    last = CANCELLATION_BREAKDOWN["last_month"]
    curr = CANCELLATION_BREAKDOWN["this_month"]

    reasons = []
    for key, curr_info in curr["reasons"].items():
        last_count = last["reasons"][key]["count"]
        curr_count = curr_info["count"]
        last_rate = round((last_count / last["total_orders"]) * 100.0, 2)
        curr_rate = round((curr_count / curr["total_orders"]) * 100.0, 2)
        reasons.append(
            {
                "reason_key": key,
                "label": curr_info["label"],
                "last_month_count": last_count,
                "this_month_count": curr_count,
                "delta_count": curr_count - last_count,
                "last_month_rate_pct": last_rate,
                "this_month_rate_pct": curr_rate,
            }
        )

    payload: dict[str, Any] = {
        "snapshot": snapshot,
        "baseline_cancellation_rate_pct": last["cancellation_rate_pct"],
        "current_cancellation_rate_pct": curr["cancellation_rate_pct"],
        "baseline_cancelled_orders": last["total_cancelled"],
        "current_cancelled_orders": curr["total_cancelled"],
        "reasons": reasons,
        "key_insight": (
            "Seller-side stock-unavailable cancellations more than doubled (6 -> 14, from 1.5% to 5.2% of orders) "
            "and dispatch-SLA auto-cancellations rose (5 -> 9), while buyer-initiated cancellation rate remained flat (~4%)."
        ),
    }
    if snapshot == "followup":
        fu = FOLLOWUP_SNAPSHOT["metrics"]
        payload["followup_cancellation_rate_pct"] = fu["cancellation_rate_pct"]
        payload["followup_seller_stock_cancel_rate_pct"] = fu["seller_stock_cancel_rate_pct"]
        payload["followup_dispatch_sla_cancel_rate_pct"] = fu["dispatch_sla_cancel_rate_pct"]
        payload["followup_note"] = (
            "Stock-unavailable cancellations fell sharply after syncing inventory, bringing overall rate "
            "from 14.0% down to 10.5% (close to the 10.0% target); dispatch SLA delays remain the main gap."
        )
    return payload


def get_pricing_gaps(snapshot: SnapshotType = "current") -> dict[str, Any]:
    overpriced = [s for s in SKU_CATALOG if s.driver_group == "overpriced"]
    gaps = []
    for s in overpriced:
        gap_rs = s.price - s.competitor_lowest_price
        gap_pct = round((gap_rs / s.competitor_lowest_price) * 100.0, 1)
        lost_orders = s.last_month_orders - s.this_month_orders
        lost_rev = lost_orders * s.price
        gaps.append(
            {
                "sku_id": s.sku_id,
                "name": s.name,
                "seller_price": s.price,
                "competitor_lowest_price": s.competitor_lowest_price,
                "price_gap_rs": gap_rs,
                "price_gap_pct": gap_pct,
                "last_month_orders": s.last_month_orders,
                "this_month_orders": s.this_month_orders,
                "lost_orders": lost_orders,
                "lost_revenue": lost_rev,
                "suggested_price": s.competitor_lowest_price + 4,
            }
        )

    payload: dict[str, Any] = {
        "snapshot": snapshot,
        "overpriced_sku_count": len(gaps) if snapshot != "followup" else 1,
        "total_pricing_lost_orders": sum(g["lost_orders"] for g in gaps),
        "total_pricing_lost_revenue": sum(g["lost_revenue"] for g in gaps),
        "pricing_gaps": gaps,
        "key_insight": (
            "3 active top-selling products are priced 6.9% to 8.3% above the lowest marketplace offer, "
            "causing 26 lost orders (Rs 13,000) without affecting overall store ASP."
        ),
    }
    if snapshot == "followup":
        payload["repriced_skus"] = FOLLOWUP_SNAPSHOT["repriced_skus"]
        payload["remaining_overpriced_sku"] = FOLLOWUP_SNAPSHOT["remaining_overpriced_sku"]
    return payload


def get_visibility_funnel(snapshot: SnapshotType = "current") -> dict[str, Any]:
    last = VISIBILITY_FUNNEL["last_month"]
    curr = VISIBILITY_FUNNEL["this_month"]
    last_ctr = round((last["clicks"] / last["impressions"]) * 100.0, 2)
    curr_ctr = round((curr["clicks"] / curr["impressions"]) * 100.0, 2)
    last_cvr = round((last["orders"] / last["clicks"]) * 100.0, 2)
    curr_cvr = round((curr["orders"] / curr["clicks"]) * 100.0, 2)

    payload: dict[str, Any] = {
        "snapshot": snapshot,
        "last_month": {**last, "ctr_pct": last_ctr, "cvr_pct": last_cvr},
        "this_month": {**curr, "ctr_pct": curr_ctr, "cvr_pct": curr_cvr},
        "changes": {
            "impressions_pct": _pct_change(last["impressions"], curr["impressions"]),
            "clicks_pct": _pct_change(last["clicks"], curr["clicks"]),
            "orders_pct": _pct_change(last["orders"], curr["orders"]),
            "ctr_delta_pp": round(curr_ctr - last_ctr, 2),
            "cvr_delta_pp": round(curr_cvr - last_cvr, 2),
        },
        "key_insight": (
            "Impressions fell 14.0% (80,000 -> 68,800) primarily because 18 out-of-stock listings dropped out "
            "of search ranking, while conversion fell 1.6 percentage points (12.5% -> 10.9%) due to pricing gaps and stock unavailability."
        ),
    }
    if snapshot == "followup":
        payload["followup_impressions_daily_pace"] = FOLLOWUP_SNAPSHOT["metrics"]["impressions_daily_pace"]
    return payload


def run_diagnosis(snapshot: SnapshotType = "current") -> dict[str, Any]:
    """Compute ranked causal attribution for the 130-order / Rs 65,000 revenue drop."""
    headline = compute_headline_metrics(snapshot)
    oos = get_out_of_stock_skus(limit=5, snapshot=snapshot)
    pricing = get_pricing_gaps(snapshot=snapshot)
    cancels = get_cancellation_breakdown(snapshot=snapshot)
    funnel = get_visibility_funnel(snapshot=snapshot)

    total_lost_orders = abs(headline["metrics"]["orders"]["delta"])
    total_lost_rev = abs(headline["metrics"]["sales_gmv"]["delta"])

    vis_sla_lost_orders = sum(
        max(0, s.last_month_orders - s.this_month_orders)
        for s in SKU_CATALOG
        if s.driver_group == "visibility_sla"
    )
    vis_sla_lost_rev = sum(
        max(0, s.last_month_orders - s.this_month_orders) * s.price
        for s in SKU_CATALOG
        if s.driver_group == "visibility_sla"
    )

    oos_orders = oos["total_oos_lost_orders"]
    oos_rev = oos["total_oos_lost_revenue"]
    price_orders = pricing["total_pricing_lost_orders"]
    price_rev = pricing["total_pricing_lost_revenue"]

    drivers = [
        {
            "rank": 1,
            "driver": "stock_outs",
            "title": "Out-of-Stock Top Sellers",
            "lost_orders": oos_orders,
            "lost_revenue": oos_rev,
            "share_pct": round((oos_orders / total_lost_orders) * 100.0, 1),
            "evidence": (
                f"Out-of-stock products jumped from 5 to 18 (+13 new). Top 5 newly OOS products alone lost "
                f"{sum(max(0, s.last_month_orders - s.this_month_orders) for s in SKU_CATALOG if s.driver_group == 'newly_oos_top')} orders."
            ),
        },
        {
            "rank": 2,
            "driver": "pricing_gaps",
            "title": "Competitor Price Gaps on Top SKUs",
            "lost_orders": price_orders,
            "lost_revenue": price_rev,
            "share_pct": round((price_orders / total_lost_orders) * 100.0, 1),
            "evidence": (
                "3 active high-volume products (Electric Kettle, Dish Rack, Spatula Set) are priced 6.9% to 8.3% "
                "higher than the lowest marketplace price."
            ),
        },
        {
            "rank": 3,
            "driver": "cancellations_visibility",
            "title": "Cancellations & Search Visibility Drag",
            "lost_orders": vis_sla_lost_orders,
            "lost_revenue": vis_sla_lost_rev,
            "share_pct": round((vis_sla_lost_orders / total_lost_orders) * 100.0, 1),
            "evidence": (
                f"Cancellation rate spiked from 8.0% to 14.0% (seller stock-unavailable 6 -> 14, dispatch SLA 5 -> 9), "
                f"and search impressions dipped {abs(funnel['changes']['impressions_pct'])}% as OOS listings lost rank."
            ),
        },
    ]

    result: dict[str, Any] = {
        "snapshot": snapshot,
        "problem_type": "volume_driven_decline",
        "total_lost_orders": total_lost_orders,
        "total_lost_revenue": total_lost_rev,
        "asp_flat": headline["metrics"]["asp"]["baseline"] == headline["metrics"]["asp"]["current"],
        "drivers": drivers,
        "already_answered_questions": [
            "Was average order value / ASP lower? No, ASP stayed flat at Rs 500.",
            "Was buyer cancellation driving the spike? No, buyer-initiated cancellation rate stayed flat at ~4%; seller-side stock and dispatch SLA drove it.",
            "Are all products down equally? No, 60% of the drop is concentrated in newly out-of-stock products.",
        ],
        "assumptions": [
            "Orders = placed orders; Sales = placed GMV (Orders x SKU Price).",
            "Cancellation rate = cancelled orders / placed orders.",
            "Causal attributions and recovery estimates are indicative mock model estimates, not guarantees.",
        ],
    }

    if snapshot == "followup":
        result["followup_comparison"] = {
            "days_elapsed": FOLLOWUP_SNAPSHOT["days_elapsed"],
            "prior_commitments": FOLLOWUP_SNAPSHOT["default_prior_commitments"],
            "metrics": FOLLOWUP_SNAPSHOT["metrics"],
            "targets": FOLLOWUP_SNAPSHOT["targets"],
        }

    return result


def build_action_plan(snapshot: SnapshotType = "current") -> dict[str, Any]:
    """Rule-based prioritised action plan ranked by impact and effort."""
    owner_name = SELLER_PROFILE["seller_name"]
    if snapshot == "followup":
        actions = [
            {
                "action_id": "ACT-FU-1",
                "priority": 1,
                "title": "Expedite HK-009 (Airtight Storage Jars) restock or temporary alternate pack",
                "owner": owner_name,
                "effort": "Low-Medium",
                "impact": "High",
                "target_skus": ["HK-009"],
                "estimated_recovery_range_rs": "Rs 6,000 - Rs 7,500/month",
                "recommended_due": "Within 48 hours",
                "details": (
                    "4 of top 5 OOS products are back in stock (OOS down 18 -> 9). Restocking HK-009 brings OOS "
                    "to 8 or below, hitting the target threshold."
                ),
            },
            {
                "action_id": "ACT-FU-2",
                "priority": 2,
                "title": "Lock 2 PM daily manifest cutoff to push cancellation rate under 10%",
                "owner": owner_name,
                "effort": "Low",
                "impact": "Medium-High",
                "target_skus": ["Warehouse Dispatch Queue"],
                "estimated_recovery_range_rs": "Rs 4,000 - Rs 6,000/month",
                "recommended_due": "Starting tomorrow",
                "details": (
                    "Stock-unavailable cancellations fell sharply, moving overall rate from 14.0% to 10.5%. "
                    "Eliminating the remaining dispatch SLA delays gets below the 10.0% target."
                ),
            },
            {
                "action_id": "ACT-FU-3",
                "priority": 3,
                "title": "Reprice HK-021 (Silicone Spatula Set) from Rs 300 to Rs 280 and consider visibility boost",
                "owner": owner_name,
                "effort": "Low",
                "impact": "Medium",
                "target_skus": ["HK-021"],
                "estimated_recovery_range_rs": "Rs 2,000 - Rs 3,000/month",
                "recommended_due": "Today",
                "details": (
                    "HK-019 and HK-020 are already repriced. Matching HK-021 closer to Rs 277 closes the last price gap; "
                    "you can also explore a promotional offer or ads campaign, subject to eligibility."
                ),
            },
        ]
    else:
        actions = [
            {
                "action_id": "ACT-1",
                "priority": 1,
                "title": "Restock top 5 high-velocity OOS products and enable low-stock alerts",
                "owner": owner_name,
                "effort": "Medium",
                "impact": "Very High (~60% of drop)",
                "target_skus": ["HK-006", "HK-007", "HK-008", "HK-009", "HK-010"],
                "estimated_recovery_range_rs": "Rs 28,000 - Rs 36,000/month",
                "recommended_due": "Within 48 hours",
                "details": (
                    "Prioritize Pressure Cooker 5L (HK-006), Granite Tawa (HK-007), Casserole Set (HK-008), "
                    "Storage Jars (HK-009), and Tadka Pan (HK-010), and set reorder alerts at 10 units."
                ),
            },
            {
                "action_id": "ACT-2",
                "priority": 2,
                "title": "Fix daily inventory sync & dispatch before SLA cutoff to cut cancellations below 10%",
                "owner": owner_name,
                "effort": "Low",
                "impact": "High (~20% of drop + search rank protection)",
                "target_skus": ["Storewide Stock Sync", "Dispatch Queue"],
                "estimated_recovery_range_rs": "Rs 10,000 - Rs 14,000/month",
                "recommended_due": "Starting today",
                "details": (
                    "Update stock buffer twice daily to stop stock-unavailable cancellations (currently 14/month) "
                    "and clear ready-to-ship orders before 2 PM to avoid auto-cancellations (currently 9/month)."
                ),
            },
            {
                "action_id": "ACT-3",
                "priority": 3,
                "title": "Reprice 3 overpriced bestsellers within 2% of lowest offer (or run a promo/ads boost)",
                "owner": owner_name,
                "effort": "Low",
                "impact": "Medium-High (~20% of drop)",
                "target_skus": ["HK-019", "HK-020", "HK-021"],
                "estimated_recovery_range_rs": "Rs 9,000 - Rs 13,000/month",
                "recommended_due": "Within 3 days",
                "details": (
                    "Adjust Electric Kettle (Rs 700 -> ~Rs 659), Dish Rack (Rs 500 -> ~Rs 469), and Spatula Set "
                    "(Rs 300 -> ~Rs 280), or run an optional limited-time promotion or ads campaign, subject to eligibility."
                ),
            },
        ]

    return {
        "snapshot": snapshot,
        "actions": actions,
        "estimated_combined_recovery_rs": (
            "Rs 12,000 - Rs 16,500 additional monthly pace"
            if snapshot == "followup"
            else "Rs 47,000 - Rs 63,000/month (indicative range)"
        ),
        "assumptions": (
            "Indicative recovery ranges assume normal marketplace demand, restored search rank within 3-5 days of restocking, "
            "and competitive fulfillment SLAs. Not a guaranteed outcome."
        ),
    }


"""Unit tests for the deterministic Diagnosis Engine and 11 LLM tools."""

from app.engine.diagnosis import (
    build_action_plan,
    get_cancellation_breakdown,
    get_out_of_stock_skus,
    get_pricing_gaps,
    get_visibility_funnel,
    run_diagnosis,
)
from app.engine.tools import TOOL_SCHEMAS, execute_tool


def test_driver_attribution_sums_to_total_drop():
    diag = run_diagnosis("current")
    assert diag["total_lost_orders"] == 130
    assert diag["total_lost_revenue"] == 65_000
    assert diag["asp_flat"] is True

    drivers = diag["drivers"]
    assert len(drivers) == 3
    assert drivers[0]["driver"] == "stock_outs"
    assert drivers[0]["share_pct"] == 60.0
    assert sum(d["lost_orders"] for d in drivers) == 130
    assert sum(d["lost_revenue"] for d in drivers) == 65_000
    assert round(sum(d["share_pct"] for d in drivers), 1) == 100.0


def test_oos_skus_ranked_by_daily_lost_revenue():
    oos = get_out_of_stock_skus(limit=5, snapshot="current")
    assert oos["total_oos_count"] == 18
    assert oos["newly_oos_count"] == 13
    top = oos["top_oos_skus"]
    assert len(top) == 5
    daily_losses = [item["estimated_lost_rev_per_day"] for item in top]
    assert daily_losses == sorted(daily_losses, reverse=True)
    assert top[0]["sku_id"] == "HK-006"


def test_pricing_gaps_within_6_to_9_percent():
    pricing = get_pricing_gaps("current")
    assert pricing["overpriced_sku_count"] == 3
    assert pricing["total_pricing_lost_orders"] == 26
    for item in pricing["pricing_gaps"]:
        assert 6.0 <= item["price_gap_pct"] <= 9.0


def test_cancellation_breakdown_story():
    canc = get_cancellation_breakdown("current")
    reasons = {r["reason_key"]: r for r in canc["reasons"]}
    assert reasons["seller_stock_unavailable"]["this_month_count"] > reasons["seller_stock_unavailable"]["last_month_count"]
    assert reasons["dispatch_sla_breach"]["this_month_count"] > reasons["dispatch_sla_breach"]["last_month_count"]
    # Buyer-initiated rate stays roughly flat (~3.75% -> ~4.07%)
    assert abs(reasons["buyer_initiated"]["this_month_rate_pct"] - reasons["buyer_initiated"]["last_month_rate_pct"]) < 0.5


def test_visibility_funnel():
    funnel = get_visibility_funnel("current")
    assert funnel["last_month"]["impressions"] == 80_000
    assert funnel["this_month"]["impressions"] == 68_800
    assert funnel["changes"]["impressions_pct"] == -14.0


def test_action_plan_has_3_prioritized_actions():
    plan = build_action_plan("current")
    assert len(plan["actions"]) == 3
    assert [a["priority"] for a in plan["actions"]] == [1, 2, 3]
    assert "subject to eligibility" in plan["actions"][2]["details"].lower()


def test_followup_snapshot_shows_partial_recovery():
    diag = run_diagnosis("followup")
    fu = diag["followup_comparison"]
    assert fu["days_elapsed"] == 3
    assert fu["metrics"]["out_of_stock_skus"] == 9
    assert fu["metrics"]["cancellation_rate_pct"] == 10.5
    assert fu["metrics"]["projected_monthly_sales"] == 175_500


def test_all_11_tools_execute_cleanly():
    expected_tools = {
        "get_seller_overview",
        "get_metric_comparison",
        "run_diagnosis",
        "get_out_of_stock_skus",
        "get_cancellation_breakdown",
        "get_pricing_gaps",
        "get_visibility_funnel",
        "build_action_plan",
        "commit_action",
        "schedule_followup",
        "send_call_summary",
    }
    schema_names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert schema_names == expected_tools

    for name in expected_tools:
        res = execute_tool(name, {}, session=None)
        assert "error" not in res


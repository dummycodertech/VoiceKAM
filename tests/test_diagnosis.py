"""Unit tests for the counterfactual Diagnosis Engine, follow-up consistency, and 11 LLM tools."""

from pathlib import Path

from app.data.seed_data import get_scenario_context
from app.engine.diagnosis import (
    build_action_plan,
    compute_headline_metrics,
    get_cancellation_breakdown,
    get_out_of_stock_skus,
    get_pricing_gaps,
    get_visibility_funnel,
    run_diagnosis,
)
from app.engine.tools import TOOL_SCHEMAS, execute_tool


def test_diagnosis_engine_does_not_reference_ground_truth_labels():
    diag_src = Path("app/engine/diagnosis.py").read_text(encoding="utf-8")
    assert "driver_group" not in diag_src
    assert "_ground_truth" not in diag_src


def test_scenarios_a_through_d_reconcile_and_rank_correctly():
    expected_top = {
        "A": "stock_outs",
        "B": "pricing_gaps",
        "C": "cancellations",
        "D": "stock_outs",
    }
    for sid, top_driver in expected_top.items():
        ctx = get_scenario_context(sid)
        diag = run_diagnosis(ctx, "current")
        drivers = diag["drivers"]
        assert sum(d["lost_orders"] for d in drivers) == diag["total_lost_orders"]
        assert sum(d["lost_revenue"] for d in drivers) == diag["total_lost_revenue"]
        causal = [d for d in drivers if d["driver"] != "unexplained_residual" and d["assessable"]]
        assert causal[0]["driver"] == top_driver
        if sid == "D":
            assert diag["unexplained_residual"]["share_pct"] >= 20.0


def test_oos_skus_ranked_by_daily_lost_revenue():
    ctx = get_scenario_context("A")
    oos = get_out_of_stock_skus(ctx, limit=5, snapshot="current")
    assert oos["total_oos_count"] == oos["newly_oos_count"] + oos["chronic_oos_count"]
    top = oos["top_oos_skus"]
    assert len(top) == 5
    daily_losses = [item["estimated_lost_rev_per_day"] for item in top]
    assert daily_losses == sorted(daily_losses, reverse=True)


def test_pricing_gaps_and_control_group_assessment():
    ctx = get_scenario_context("B")
    pricing = get_pricing_gaps(ctx, "current")
    assert pricing["assessable"] is True
    assert pricing["control_group_size"] >= 4
    assert pricing["overpriced_sku_count"] >= 4
    for item in pricing["pricing_gaps"]:
        assert item["price_gap_pct"] > 3.0


def test_cancellation_breakdown_story():
    ctx = get_scenario_context("C")
    canc = get_cancellation_breakdown(ctx, "current")
    reasons = {r["reason_key"]: r for r in canc["reasons"]}
    assert reasons["seller_stock_unavailable"]["this_month_count"] > reasons["seller_stock_unavailable"]["last_month_count"]
    assert reasons["dispatch_sla_breach"]["this_month_count"] > reasons["dispatch_sla_breach"]["last_month_count"]
    assert abs(reasons["buyer_initiated"]["this_month_rate_pct"] - reasons["buyer_initiated"]["last_month_rate_pct"]) < 1.5


def test_visibility_funnel():
    ctx = get_scenario_context("B")
    funnel = get_visibility_funnel(ctx, "current")
    assert funnel["assessable"] is True
    assert funnel["changes"]["impressions_pct"] < 0


def test_action_plan_prioritized_and_marketplace_agnostic():
    ctx = get_scenario_context("A")
    plan = build_action_plan(ctx, "current")
    assert len(plan["actions"]) == 3
    assert [a["priority"] for a in plan["actions"]] == [1, 2, 3]
    assert "subject to eligibility" in plan["assumptions"].lower()


def test_followup_snapshot_derives_from_single_cloned_records():
    for sid in ("A", "B", "C", "D"):
        ctx = get_scenario_context(sid)
        headline = compute_headline_metrics(ctx, "followup")
        diag = run_diagnosis(ctx, "followup")
        oos = get_out_of_stock_skus(ctx, limit=25, snapshot="followup")
        pricing = get_pricing_gaps(ctx, snapshot="followup")
        cancels = get_cancellation_breakdown(ctx, snapshot="followup")
        plan = build_action_plan(ctx, snapshot="followup")

        fu_m = headline["followup"]
        assert fu_m["sales_gmv_pace"] > headline["metrics"]["sales_gmv"]["current"]
        assert oos["total_oos_count"] == oos["newly_oos_count"] + oos["chronic_oos_count"]
        assert oos["total_oos_count"] == fu_m["out_of_stock_skus"]
        assert cancels["followup_cancellation_rate_pct"] == fu_m["cancellation_rate_pct"]

        # Repriced SKUs must no longer appear in followup pricing_gaps
        repriced_ids = {r["sku_id"] for r in pricing.get("repriced_skus", [])}
        open_price_ids = {g["sku_id"] for g in pricing["pricing_gaps"]}
        assert not (repriced_ids & open_price_ids)

        # Suggested price for remaining overpriced SKU must match ACT-FU-3 in action_plan
        if pricing.get("remaining_overpriced_sku"):
            rem = pricing["remaining_overpriced_sku"]
            expected_price_txt = f"~Rs {int(rem['suggested_price'])}"
            act3 = next((a for a in plan["actions"] if a["action_id"] == "ACT-FU-3"), None)
            if act3:
                assert expected_price_txt in act3["details"]

        # Driver + residual still sums cleanly in followup too
        assert sum(d["lost_orders"] for d in diag["drivers"]) == diag["total_lost_orders"]
        assert sum(d["lost_revenue"] for d in diag["drivers"]) == diag["total_lost_revenue"]


def test_all_11_tools_execute_cleanly():
    ctx = get_scenario_context("A")
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
        res = execute_tool(name, {}, session=None, context=ctx)
        assert "error" not in res

"""Exact SKU-level & operational reconciliation tests across Scenarios A-D."""

from app.data.seed_data import get_scenario_context
from app.engine.diagnosis import compute_headline_metrics, get_cancellation_breakdown


def test_default_scenario_a_is_distinct_and_reconciled():
    ctx = get_scenario_context("A")
    # Scenario A uses the new standalone store (not old assessment 60-SKU / 400-order / Rs 500 fixture)
    assert ctx.profile.shop_name == "UrbanStride Footwear & Apparel"
    assert len(ctx.skus) == 48
    assert len({s.sku_id for s in ctx.skus}) == 48

    last_orders = sum(s.orders_prev for s in ctx.skus)
    curr_orders = sum(s.orders_curr for s in ctx.skus)
    last_rev = round(sum(s.orders_prev * s.price for s in ctx.skus))
    curr_rev = round(sum(s.orders_curr * s.price for s in ctx.skus))
    assert last_orders > curr_orders
    assert last_rev > curr_rev

    headline = compute_headline_metrics(ctx, "current")
    m = headline["metrics"]
    assert m["orders"]["baseline"] == last_orders
    assert m["orders"]["current"] == curr_orders
    assert m["sales_gmv"]["baseline"] == last_rev
    assert m["sales_gmv"]["current"] == curr_rev
    assert m["out_of_stock_skus"]["baseline"] == sum(1 for s in ctx.skus if s.is_oos_prev)
    assert m["out_of_stock_skus"]["current"] == sum(1 for s in ctx.skus if s.is_oos_curr)


def test_all_four_scenarios_have_unique_skus_and_exact_headline_totals():
    for sid in ("A", "B", "C", "D"):
        ctx = get_scenario_context(sid)
        assert len({s.sku_id for s in ctx.skus}) == len(ctx.skus)

        headline = compute_headline_metrics(ctx, "current")
        canc = get_cancellation_breakdown(ctx, "current")
        m = headline["metrics"]

        assert m["orders"]["delta"] < 0
        assert m["sales_gmv"]["delta"] < 0
        assert m["cancellation_rate_pct"]["baseline"] == canc["baseline_cancellation_rate_pct"]
        assert m["cancellation_rate_pct"]["current"] == canc["current_cancellation_rate_pct"]

"""Exact reconciliation tests for the SellerContext seed dataset."""

from app.data.seed_data import build_seed_seller_context
from app.engine.diagnosis import compute_headline_metrics


def test_catalog_count_and_unique_ids():
    ctx = build_seed_seller_context()
    assert len(ctx.skus) == 60
    assert ctx.profile.to_dict(sku_count=len(ctx.skus))["active_catalog_size"] == 60
    sku_ids = [s.sku_id for s in ctx.skus]
    assert len(set(sku_ids)) == 60


def test_sku_orders_reconciliation():
    ctx = build_seed_seller_context()
    last_orders = sum(s.orders_prev for s in ctx.skus)
    curr_orders = sum(s.orders_curr for s in ctx.skus)
    assert last_orders == 400
    assert curr_orders == 270


def test_sku_revenue_and_weighted_asp_reconciliation():
    ctx = build_seed_seller_context()
    last_revenue = sum(s.orders_prev * s.price for s in ctx.skus)
    curr_revenue = sum(s.orders_curr * s.price for s in ctx.skus)
    assert last_revenue == 200_000
    assert curr_revenue == 135_000

    last_orders = sum(s.orders_prev for s in ctx.skus)
    curr_orders = sum(s.orders_curr for s in ctx.skus)
    assert last_revenue / last_orders == 500
    assert curr_revenue / curr_orders == 500


def test_oos_counts_reconciliation():
    ctx = build_seed_seller_context()
    last_oos = sum(1 for s in ctx.skus if s.is_oos_prev)
    curr_oos = sum(1 for s in ctx.skus if s.is_oos_curr)
    assert last_oos == 5
    assert curr_oos == 18


def test_cancellation_reconciliation():
    ctx = build_seed_seller_context()
    last_total = sum(r.count_prev for r in ctx.cancellations)
    curr_total = sum(r.count_curr for r in ctx.cancellations)
    assert last_total == round(0.08 * 400)
    assert curr_total == round(0.14 * 270)


def test_funnel_reconciliation():
    ctx = build_seed_seller_context()
    assert ctx.funnel is not None
    assert ctx.funnel.impressions_prev == 80_000
    assert ctx.funnel.impressions_curr == 68_800
    imp_drop = (ctx.funnel.impressions_curr - ctx.funnel.impressions_prev) / ctx.funnel.impressions_prev
    assert -0.15 <= imp_drop <= -0.12


def test_headline_engine_matches_exact_targets():
    ctx = build_seed_seller_context()
    headline = compute_headline_metrics(ctx, "current")
    m = headline["metrics"]
    assert m["sales_gmv"]["baseline"] == 200_000
    assert m["sales_gmv"]["current"] == 135_000
    assert m["sales_gmv"]["delta_pct"] == -32.5
    assert m["orders"]["baseline"] == 400
    assert m["orders"]["current"] == 270
    assert m["orders"]["delta_pct"] == -32.5
    assert m["asp"]["baseline"] == 500
    assert m["asp"]["current"] == 500
    assert m["out_of_stock_skus"]["baseline"] == 5
    assert m["out_of_stock_skus"]["current"] == 18
    assert m["cancellation_rate_pct"]["baseline"] == 8.0
    assert m["cancellation_rate_pct"]["current"] == 14.1

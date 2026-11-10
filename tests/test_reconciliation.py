"""Exact reconciliation tests for the 60-SKU Kumar Home & Kitchen dataset."""

from app.data.seed_data import (
    CANCELLATION_BREAKDOWN,
    SELLER_PROFILE,
    SKU_CATALOG,
    VISIBILITY_FUNNEL,
)
from app.engine.diagnosis import compute_headline_metrics


def test_catalog_count_and_unique_ids():
    assert len(SKU_CATALOG) == 60
    assert SELLER_PROFILE["active_catalog_size"] == 60
    sku_ids = [s.sku_id for s in SKU_CATALOG]
    assert len(set(sku_ids)) == 60


def test_sku_orders_reconciliation():
    last_orders = sum(s.last_month_orders for s in SKU_CATALOG)
    curr_orders = sum(s.this_month_orders for s in SKU_CATALOG)
    assert last_orders == 400
    assert curr_orders == 270


def test_sku_revenue_and_weighted_asp_reconciliation():
    last_revenue = sum(s.last_month_orders * s.price for s in SKU_CATALOG)
    curr_revenue = sum(s.this_month_orders * s.price for s in SKU_CATALOG)
    assert last_revenue == 200_000
    assert curr_revenue == 135_000

    last_orders = sum(s.last_month_orders for s in SKU_CATALOG)
    curr_orders = sum(s.this_month_orders for s in SKU_CATALOG)
    assert last_revenue / last_orders == 500
    assert curr_revenue / curr_orders == 500


def test_oos_counts_reconciliation():
    last_oos = sum(1 for s in SKU_CATALOG if s.last_month_oos)
    curr_oos = sum(1 for s in SKU_CATALOG if s.this_month_oos)
    assert last_oos == 5
    assert curr_oos == 18


def test_cancellation_reconciliation():
    last = CANCELLATION_BREAKDOWN["last_month"]
    curr = CANCELLATION_BREAKDOWN["this_month"]

    assert last["total_cancelled"] == round(0.08 * 400)
    assert curr["total_cancelled"] == round(0.14 * 270)
    assert sum(r["count"] for r in last["reasons"].values()) == last["total_cancelled"]
    assert sum(r["count"] for r in curr["reasons"].values()) == curr["total_cancelled"]


def test_funnel_reconciliation():
    assert VISIBILITY_FUNNEL["last_month"]["orders"] == 400
    assert VISIBILITY_FUNNEL["this_month"]["orders"] == 270
    imp_drop = (
        VISIBILITY_FUNNEL["this_month"]["impressions"] - VISIBILITY_FUNNEL["last_month"]["impressions"]
    ) / VISIBILITY_FUNNEL["last_month"]["impressions"]
    assert -0.15 <= imp_drop <= -0.12


def test_headline_engine_matches_exact_targets():
    headline = compute_headline_metrics("current")
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
    assert m["cancellation_rate_pct"]["current"] == 14.0


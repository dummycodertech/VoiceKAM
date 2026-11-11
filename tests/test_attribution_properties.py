"""Property-based invariants & 200-trial ground-truth recovery tests for Diagnosis Engine."""

from __future__ import annotations

import random

from app.data.context import CancellationRow, SellerContext, SellerProfile, SkuRecord, VisibilityFunnel
from app.data.scenarios import DominantCause, generate_random_trial_context
from app.engine.diagnosis import UNAVAILABLE_MSG, run_diagnosis


def test_property_random_datasets_always_reconcile_without_negative_drivers():
    rng = random.Random(2026)
    for trial in range(100):
        n_skus = rng.randint(5, 65)
        include_oos = rng.random() > 0.25
        include_days_oos = include_oos and (rng.random() > 0.30)
        include_price = rng.random() > 0.25
        include_imp = rng.random() > 0.25
        include_clicks = include_imp and (rng.random() > 0.35)
        include_cancels = rng.random() > 0.25
        flat_or_growing = trial % 12 == 0

        skus: list[SkuRecord] = []
        for i in range(n_skus):
            price = float(rng.randint(150, 2500))
            o_prev = rng.randint(0, 60)
            if flat_or_growing:
                o_curr = o_prev + rng.randint(0, 10)
            else:
                o_curr = rng.randint(0, max(1, o_prev + 5))  # allow mix of growing and declining SKUs!

            days_curr = rng.randint(0, 28) if include_days_oos else None
            stock_curr = (0 if (days_curr and days_curr > 0) else rng.randint(0, 50)) if include_oos else None
            comp_p = round(price * rng.uniform(0.78, 1.05), 1) if include_price else None
            imp_p = rng.randint(100, 8000) if include_imp else None
            imp_c = rng.randint(20, imp_p + 1000) if (include_imp and imp_p is not None) else None
            clk_p = rng.randint(0, imp_p) if (include_clicks and imp_p is not None) else None
            clk_c = rng.randint(0, imp_c) if (include_clicks and imp_c is not None) else None

            skus.append(
                SkuRecord(
                    sku_id=f"RND-{i:03d}",
                    name=f"Random SKU {i}",
                    price=price,
                    orders_prev=o_prev,
                    orders_curr=o_curr,
                    competitor_lowest_price=comp_p,
                    days_out_of_stock_curr=days_curr,
                    stock_qty_curr=stock_curr,
                    impressions_prev=imp_p,
                    impressions_curr=imp_c,
                    clicks_prev=clk_p,
                    clicks_curr=clk_c,
                )
            )

        cancels = (
            [
                CancellationRow("seller_stock_unavailable", "Stock unavailable", rng.randint(0, 10), rng.randint(0, 35), True),
                CancellationRow("dispatch_sla_breach", "SLA breach", rng.randint(0, 8), rng.randint(0, 25), True),
                CancellationRow("buyer_initiated", "Buyer cancelled", rng.randint(2, 15), rng.randint(2, 15), False),
            ]
            if include_cancels
            else []
        )

        # Also occasionally pass store-level funnel only
        funnel = (
            VisibilityFunnel(impressions_prev=50_000, impressions_curr=38_000)
            if (not include_imp and rng.random() > 0.5)
            else None
        )
        ctx = SellerContext(profile=SellerProfile(scenario_id="FUZZ"), skus=skus, cancellations=cancels, funnel=funnel)
        diag = run_diagnosis(ctx, "current")

        drivers = diag["drivers"]
        assert sum(d["lost_orders"] for d in drivers) == diag["total_lost_orders"]
        assert sum(d["lost_revenue"] for d in drivers) == diag["total_lost_revenue"]
        for d in drivers:
            assert d["lost_orders"] >= 0
            assert d["lost_revenue"] >= 0
            assert d["share_pct"] >= 0.0
            if not d["assessable"]:
                assert UNAVAILABLE_MSG in d["evidence"]


def test_200_seeded_trials_ground_truth_recovery():
    """Verify >= 90% top-driver recovery accuracy across 200 randomized causal trials."""
    causes: tuple[DominantCause, ...] = ("stock_outs", "pricing_gaps", "visibility_drop", "cancellations")
    top_hits = 0
    max_errors: list[float] = []

    for trial_idx in range(200):
        dom = causes[trial_idx % len(causes)]
        ctx, gt = generate_random_trial_context(trial_seed=5000 + trial_idx, dominant=dom)
        diag = run_diagnosis(ctx, "current")

        assert sum(d["lost_orders"] for d in diag["drivers"]) == diag["total_lost_orders"]
        assert sum(d["lost_revenue"] for d in diag["drivers"]) == diag["total_lost_revenue"]

        assessed_causal = [d for d in diag["drivers"] if d["driver"] != "unexplained_residual" and d["assessable"]]
        if assessed_causal and assessed_causal[0]["driver"] == dom:
            top_hits += 1

        est_shares = {d["driver"]: d["share_pct"] for d in diag["drivers"]}
        for cause in causes:
            max_errors.append(abs(est_shares.get(cause, 0.0) - gt["shares"].get(cause, 0.0)))

    accuracy = top_hits / 200.0
    mean_abs_err = sum(max_errors) / len(max_errors)
    assert accuracy >= 0.90, f"Top-driver recovery dropped to {accuracy:.1%}"
    assert max(max_errors) <= 15.0, f"Worst driver share error too high: {max(max_errors):.1f}pp"
    assert mean_abs_err <= 3.0, f"Mean driver share error too high: {mean_abs_err:.2f}pp"

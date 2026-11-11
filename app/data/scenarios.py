"""Seeded deterministic scenario generator with Scenarios A-D and synthetic ground-truth trials."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Literal

from app.config import get_settings
from app.data.context import (
    CancellationRow,
    FollowupAdjustment,
    SellerContext,
    SellerProfile,
    SkuRecord,
    VisibilityFunnel,
)

DominantCause = Literal["stock_outs", "pricing_gaps", "visibility_drop", "cancellations", "mixed_noise"]


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    seller_id: str
    seller_name: str
    salutation: str
    shop_name: str
    city: str
    category: str
    title: str
    description: str
    sku_count: int
    base_price: int
    dominant_cause: DominantCause
    seed: int


SCENARIO_SPECS: dict[str, ScenarioSpec] = {
    "A": ScenarioSpec(
        scenario_id="A",
        seller_id="VKAM-BLR-1042",
        seller_name="Vikram Kulkarni",
        salutation="Vikram ji",
        shop_name="UrbanStride Footwear & Apparel",
        city="Bengaluru",
        category="Footwear & Activewear",
        title="UrbanStride Footwear (Stock-out Dominant)",
        description="Stock-outs on 14 fast-moving footwear & activewear SKUs drive ~57% of a 124-order decline.",
        sku_count=48,
        base_price=920,
        dominant_cause="stock_outs",
        seed=101,
    ),
    "B": ScenarioSpec(
        scenario_id="B",
        seller_id="VKAM-MUM-2087",
        seller_name="Neha Sharma",
        salutation="Neha ji",
        shop_name="TechNest Accessories",
        city="Mumbai",
        category="Electronics Accessories",
        title="TechNest Accessories (Visibility & Price Dominant)",
        description="Uncompetitive pricing on top active listings (~41%) and search impression loss (~35%) drive the drop.",
        sku_count=42,
        base_price=680,
        dominant_cause="pricing_gaps",
        seed=202,
    ),
    "C": ScenarioSpec(
        scenario_id="C",
        seller_id="VKAM-PUN-3195",
        seller_name="Rohan Deshmukh",
        salutation="Rohan ji",
        shop_name="FreshBrew Pantry",
        city="Pune",
        category="Beverages & Pantry",
        title="FreshBrew Pantry (Cancellation & SLA Dominant)",
        description="Seller-initiated stock cancellations & dispatch SLA delays account for ~60% of lost orders.",
        sku_count=36,
        base_price=540,
        dominant_cause="cancellations",
        seed=303,
    ),
    "D": ScenarioSpec(
        scenario_id="D",
        seller_id="VKAM-JAI-4410",
        seller_name="Meenakshi Iyer",
        salutation="Meenakshi ji",
        shop_name="ArtisanCraft Decor",
        city="Jaipur",
        category="Handcrafted Home Decor",
        title="ArtisanCraft Decor (Mixed + High Residual)",
        description="Moderate stock, price, visibility, and cancellation drags leave ~28% unexplained seasonal residual.",
        sku_count=40,
        base_price=1150,
        dominant_cause="mixed_noise",
        seed=404,
    ),
}

_SKU_TEMPLATES: dict[str, list[tuple[str, str, int]]] = {
    "Footwear & Activewear": [
        ("Trail Runner Shoes", "Running Shoes", 1650),
        ("Canvas Everyday Sneakers", "Casual Shoes", 1100),
        ("Cushion Grip Yoga Socks 3-Pack", "Activewear", 480),
        ("Lightweight Mesh Trainers", "Running Shoes", 1420),
        ("Quick-Dry Gym Tee", "Apparel", 620),
        ("Arch Support Insoles Pair", "Accessories", 450),
        ("Slip-On Recovery Sandals", "Footwear", 890),
        ("Compression Ankle Sleeve", "Activewear", 540),
        ("Rainproof Shoe Covers Pair", "Accessories", 390),
        ("Track Jogger Pants", "Apparel", 1180),
        ("Breathable Knit Loafers", "Casual Shoes", 1250),
        ("Thermal Wool Running Socks", "Activewear", 520),
    ],
    "Electronics Accessories": [
        ("Braided USB-C Fast Cable 2m", "Cables", 420),
        ("Silent Wireless Optical Mouse", "Peripherals", 780),
        ("Aluminum Foldable Laptop Stand", "Workspace", 1190),
        ("PD 30W Dual Wall Adapter", "Chargers", 890),
        ("Magnetic Car Vent Mount", "Mobile Gear", 540),
        ("7-in-1 USB-C Hub Dongle", "Hubs", 1450),
        ("Neoprene Laptop Sleeve 14-inch", "Bags", 660),
        ("Webcam Privacy Slide 3-Pack", "Workspace", 290),
        ("Desk Cable Organizer Tray", "Workspace", 490),
        ("Bluetooth Keypad Slim", "Peripherals", 1050),
    ],
    "Beverages & Pantry": [
        ("Single-Origin Filter Roast 250g", "Coffee", 460),
        ("Assam Orthodox Whole Leaf 200g", "Tea", 390),
        ("Cold Brew Concentrate Pack", "Coffee", 620),
        ("Cardamom Masala Chai Premix", "Tea", 440),
        ("Stainless Pour-Over Dripper", "Brew Gear", 840),
        ("Raw Wildflower Acacia Honey", "Sweeteners", 580),
        ("Roasted Almond Butter Jar", "Spreads", 720),
        ("Zero-Sugar Iced Peach Tea Mix", "Beverages", 350),
    ],
    "Handcrafted Home Decor": [
        ("Hand-Block Cotton Cushion Cover 2pc", "Textiles", 760),
        ("Hammered Brass Tealight Holder Set", "Lighting", 1280),
        ("Terracotta Matte Table Vase", "Vases", 940),
        ("Macrame Woven Wall Hanging", "Wall Art", 1420),
        ("Sheesham Wood Coaster Set 6pc", "Tabletop", 590),
        ("Indigo Quilted Table Runner", "Textiles", 1120),
        ("Ceramic Scented Diffuser Pot", "Aroma", 860),
        ("Jute Braided Round Placemat 4pc", "Tabletop", 990),
    ],
}

_PREFIX_MAP = {
    "Footwear & Activewear": "US",
    "Electronics Accessories": "TN",
    "Beverages & Pantry": "FB",
    "Handcrafted Home Decor": "AC",
}


def _generate_skus_and_cancels(
    *,
    rng: random.Random,
    sku_count: int,
    sku_prefix: str,
    category: str,
    base_price: int,
    dominant_cause: DominantCause,
    target_oos_orders: int,
    target_price_orders: int,
    target_vis_orders: int,
    target_cancel_orders: int,
    target_noise_orders: int,
) -> tuple[list[SkuRecord], list[CancellationRow], dict[str, int]]:
    """Construct SKU-level records where causal shocks reflect true drivers cleanly."""
    templates = _SKU_TEMPLATES.get(category, _SKU_TEMPLATES["Footwear & Activewear"])
    skus: list[SkuRecord] = []

    # Partition SKUs cleanly into disjoint causal roles + stable control pool + chronic OOS
    indices = list(range(sku_count))
    # First reserve 2-3 chronic OOS SKUs (OOS in both periods -> orders_prev = 0, orders_curr = 0)
    chronic_n = 3 if sku_count >= 25 else 1
    chronic_indices = set(indices[-chronic_n:])

    pool = indices[:-chronic_n]
    oos_n = max(1, min(14, round(target_oos_orders / 7.5))) if target_oos_orders > 0 else 0
    price_n = max(1, min(8, round(target_price_orders / 7.0))) if target_price_orders > 0 else 0
    vis_n = max(1, min(10, round(target_vis_orders / 5.5))) if target_vis_orders > 0 else 0
    noise_n = max(1, min(8, round(target_noise_orders / 5.0))) if target_noise_orders > 0 else 0
    cancel_n = max(1, min(10, round(target_cancel_orders / 6.5))) if target_cancel_orders > 0 else 0

    # Ensure enough control SKUs remain (>= 6 when possible)
    max_shock_n = max(6, len(pool) - 8)
    total_shock_n = oos_n + price_n + vis_n + cancel_n + noise_n
    if total_shock_n > max_shock_n:
        scale = max_shock_n / total_shock_n
        oos_n = max(1 if oos_n else 0, int(oos_n * scale))
        price_n = max(1 if price_n else 0, int(price_n * scale))
        vis_n = max(1 if vis_n else 0, int(vis_n * scale))
        cancel_n = max(1 if cancel_n else 0, int(cancel_n * scale))
        noise_n = max(1 if noise_n else 0, int(noise_n * scale))

    cursor = 0
    oos_idx = pool[cursor : cursor + oos_n]; cursor += oos_n
    price_idx = pool[cursor : cursor + price_n]; cursor += price_n
    vis_idx = pool[cursor : cursor + vis_n]; cursor += vis_n
    cancel_idx = pool[cursor : cursor + cancel_n]; cursor += cancel_n
    noise_idx = pool[cursor : cursor + noise_n]; cursor += noise_n

    def _spread(total: int, k: int) -> list[int]:
        if k <= 0 or total <= 0:
            return []
        base, rem = divmod(total, k)
        # Weight slightly downward across items so top items stand out clearly for action plan
        weights = [max(1, k - i + 1) for i in range(k)]
        w_sum = sum(weights)
        alloc = [max(1, round(total * w / w_sum)) for w in weights]
        diff = total - sum(alloc)
        idx = 0
        while diff != 0 and alloc:
            if diff > 0:
                alloc[idx % k] += 1
                diff -= 1
            elif alloc[idx % k] > 1:
                alloc[idx % k] -= 1
                diff += 1
            idx += 1
            if idx > 500:
                break
        if sum(alloc) != total and alloc:
            alloc[0] = max(1, alloc[0] + (total - sum(alloc)))
        return alloc

    oos_drops = dict(zip(oos_idx, _spread(target_oos_orders, len(oos_idx))))
    price_drops = dict(zip(price_idx, _spread(target_price_orders, len(price_idx))))
    vis_drops = dict(zip(vis_idx, _spread(target_vis_orders, len(vis_idx))))
    cancel_drops = dict(zip(cancel_idx, _spread(target_cancel_orders, len(cancel_idx))))
    noise_drops = dict(zip(noise_idx, _spread(target_noise_orders, len(noise_idx))))

    true_orders = {"stock_outs": 0, "pricing_gaps": 0, "visibility_drop": 0, "cancellations": 0, "residual": 0}

    for i in range(sku_count):
        t_name, t_cat, t_price = templates[i % len(templates)]
        variant = (i // len(templates)) + 1
        name = t_name if variant == 1 else f"{t_name} V{variant}"
        sku_id = f"{sku_prefix}-{i + 1:03d}"
        price = float(round(t_price * (base_price / 850.0) / 10.0) * 10)
        price = max(190.0, price)
        comp_price = round(price * rng.uniform(0.985, 1.01), 0)

        if i in chronic_indices:
            skus.append(
                SkuRecord(
                    sku_id=sku_id,
                    name=name,
                    category=t_cat,
                    price=price,
                    orders_prev=0,
                    orders_curr=0,
                    competitor_lowest_price=comp_price,
                    days_out_of_stock_curr=30,
                    days_out_of_stock_prev=30,
                    stock_qty_curr=0,
                    impressions_prev=120,
                    impressions_curr=80,
                    clicks_prev=4,
                    clicks_curr=2,
                    driver_group="chronic_oos",
                )
            )
            continue

        if i in oos_drops:
            lost = oos_drops[i]
            days_oos = rng.randint(12, 24)
            # Match daily_vel * days_oos ~= lost -> orders_prev = round(lost * 30 / days_oos)
            prev_ord = max(lost + 2, round((lost * 30.0) / days_oos))
            est_oos = round((prev_ord / 30.0) * days_oos)
            curr_ord = max(0, prev_ord - est_oos)
            true_lost = prev_ord - curr_ord
            true_orders["stock_outs"] += true_lost
            imp_prev = prev_ord * rng.randint(170, 220)
            imp_curr = max(100, round(imp_prev * ((30 - days_oos) / 30.0)))
            clk_prev = max(10, round(imp_prev * 0.045))
            clk_curr = max(3, round(imp_curr * 0.042))
            skus.append(
                SkuRecord(
                    sku_id=sku_id,
                    name=name,
                    category=t_cat,
                    price=price,
                    orders_prev=prev_ord,
                    orders_curr=curr_ord,
                    competitor_lowest_price=comp_price,
                    days_out_of_stock_curr=days_oos,
                    days_out_of_stock_prev=0,
                    stock_qty_curr=0,
                    impressions_prev=imp_prev,
                    impressions_curr=imp_curr,
                    clicks_prev=clk_prev,
                    clicks_curr=clk_curr,
                    driver_group="stock_outs",
                )
            )
        elif i in price_drops:
            lost = price_drops[i]
            prev_ord = max(lost + 4, rng.randint(max(12, lost + 4), max(18, lost + 14)))
            curr_ord = prev_ord - lost
            true_orders["pricing_gaps"] += lost
            gap_factor = rng.uniform(1.08, 1.17)
            comp_low = round(price / gap_factor, 0)
            imp_prev = prev_ord * rng.randint(180, 210)
            imp_curr = round(imp_prev * rng.uniform(0.97, 1.01))
            clk_prev = max(10, round(imp_prev * 0.045))
            clk_curr = max(4, round(imp_curr * 0.032))
            skus.append(
                SkuRecord(
                    sku_id=sku_id,
                    name=name,
                    category=t_cat,
                    price=price,
                    orders_prev=prev_ord,
                    orders_curr=curr_ord,
                    competitor_lowest_price=comp_low,
                    days_out_of_stock_curr=0,
                    days_out_of_stock_prev=0,
                    stock_qty_curr=rng.randint(25, 80),
                    impressions_prev=imp_prev,
                    impressions_curr=imp_curr,
                    clicks_prev=clk_prev,
                    clicks_curr=clk_curr,
                    driver_group="pricing_gaps",
                )
            )
        elif i in vis_drops:
            lost = vis_drops[i]
            prev_ord = max(lost + 4, rng.randint(max(10, lost + 5), max(16, lost + 15)))
            curr_ord = prev_ord - lost
            true_orders["visibility_drop"] += lost
            imp_prev = prev_ord * 200
            # Drop impressions by exact fraction lost / prev_ord
            imp_curr = max(100, round(imp_prev * (curr_ord / prev_ord)))
            clk_prev = max(8, round(imp_prev * 0.045))
            clk_curr = max(4, round(imp_curr * 0.045))
            skus.append(
                SkuRecord(
                    sku_id=sku_id,
                    name=name,
                    category=t_cat,
                    price=price,
                    orders_prev=prev_ord,
                    orders_curr=curr_ord,
                    competitor_lowest_price=comp_price,
                    days_out_of_stock_curr=0,
                    days_out_of_stock_prev=0,
                    stock_qty_curr=rng.randint(20, 65),
                    impressions_prev=imp_prev,
                    impressions_curr=imp_curr,
                    clicks_prev=clk_prev,
                    clicks_curr=clk_curr,
                    driver_group="visibility_drop",
                )
            )
        elif i in cancel_drops:
            lost = cancel_drops[i]
            prev_ord = max(lost + 3, rng.randint(max(10, lost + 4), max(16, lost + 12)))
            curr_ord = prev_ord - lost
            true_orders["cancellations"] += lost
            imp_prev = prev_ord * 190
            imp_curr = round(imp_prev * 0.98)
            clk_prev = max(8, round(imp_prev * 0.045))
            clk_curr = max(6, round(imp_curr * 0.044))
            skus.append(
                SkuRecord(
                    sku_id=sku_id,
                    name=name,
                    category=t_cat,
                    price=price,
                    orders_prev=prev_ord,
                    orders_curr=curr_ord,
                    competitor_lowest_price=comp_price,
                    days_out_of_stock_curr=0,
                    days_out_of_stock_prev=0,
                    stock_qty_curr=rng.randint(15, 55),
                    impressions_prev=imp_prev,
                    impressions_curr=imp_curr,
                    clicks_prev=clk_prev,
                    clicks_curr=clk_curr,
                    driver_group="cancellations",
                )
            )
        elif i in noise_drops:
            lost = noise_drops[i]
            prev_ord = max(lost + 4, rng.randint(max(10, lost + 4), max(16, lost + 12)))
            curr_ord = prev_ord - lost
            true_orders["residual"] += lost
            imp_prev = prev_ord * 190
            imp_curr = imp_prev  # unchanged visibility, unchanged price, in stock -> unexplained residual!
            clk_prev = max(8, round(imp_prev * 0.045))
            clk_curr = max(6, round(imp_curr * 0.044))
            skus.append(
                SkuRecord(
                    sku_id=sku_id,
                    name=name,
                    category=t_cat,
                    price=price,
                    orders_prev=prev_ord,
                    orders_curr=curr_ord,
                    competitor_lowest_price=comp_price,
                    days_out_of_stock_curr=0,
                    days_out_of_stock_prev=0,
                    stock_qty_curr=rng.randint(25, 70),
                    impressions_prev=imp_prev,
                    impressions_curr=imp_curr,
                    clicks_prev=clk_prev,
                    clicks_curr=clk_curr,
                    driver_group="noise_residual",
                )
            )
        else:
            # Healthy in-stock fairly-priced control group SKU (flat orders)
            prev_ord = rng.randint(5, 10)
            curr_ord = prev_ord
            imp_prev = prev_ord * 190
            imp_curr = imp_prev
            clk_prev = max(5, round(imp_prev * 0.045))
            clk_curr = clk_prev
            skus.append(
                SkuRecord(
                    sku_id=sku_id,
                    name=name,
                    category=t_cat,
                    price=price,
                    orders_prev=prev_ord,
                    orders_curr=curr_ord,
                    competitor_lowest_price=comp_price,
                    days_out_of_stock_curr=0,
                    days_out_of_stock_prev=0,
                    stock_qty_curr=rng.randint(30, 90),
                    impressions_prev=imp_prev,
                    impressions_curr=imp_curr,
                    clicks_prev=clk_prev,
                    clicks_curr=clk_curr,
                    driver_group="healthy_control",
                )
            )

    # Cancellations table matches excess seller-fault orders = target_cancel_orders
    prev_stock_cancel = 4
    prev_sla_cancel = 3
    actual_cancel_excess = true_orders["cancellations"]
    stock_share = round(actual_cancel_excess * 0.62)
    sla_share = max(0, actual_cancel_excess - stock_share)
    cancellations = [
        CancellationRow(
            reason_key="seller_stock_unavailable",
            label="Seller cancelled - stock unavailable",
            count_prev=prev_stock_cancel,
            count_curr=prev_stock_cancel + stock_share,
            is_seller_fault=True,
        ),
        CancellationRow(
            reason_key="dispatch_sla_breach",
            label="Auto-cancelled - dispatch SLA delay",
            count_prev=prev_sla_cancel,
            count_curr=prev_sla_cancel + sla_share,
            is_seller_fault=True,
        ),
        CancellationRow(
            reason_key="buyer_initiated",
            label="Buyer initiated cancellation",
            count_prev=11,
            count_curr=9,
            is_seller_fault=False,
        ),
        CancellationRow(
            reason_key="courier_address_issue",
            label="Courier / address / COD issue",
            count_prev=5,
            count_curr=4,
            is_seller_fault=False,
        ),
    ]
    return skus, cancellations, true_orders


def _build_followup_adjustment(
    skus: list[SkuRecord],
    cancellations: list[CancellationRow],
    profile: SellerProfile,
) -> FollowupAdjustment:
    """Build deterministic follow-up record deltas deriving strictly from top declining SKUs."""
    # Find top OOS SKUs (restock all but the 1 remaining priority blocker)
    newly_oos = [s for s in skus if s.is_oos_curr and not s.is_oos_prev]
    newly_oos.sort(key=lambda s: -round((s.orders_prev / 30.0) * s.price))

    restocked_skus: dict[str, dict[str, int]] = {}
    restock_recoveries = newly_oos[:3] if len(newly_oos) >= 4 else newly_oos[:-1]
    for s in restock_recoveries:
        recovered_orders = max(s.orders_curr + 2, round(s.orders_prev * 0.88))
        restocked_skus[s.sku_id] = {
            "days_out_of_stock_curr": 0,
            "stock_qty_curr": 32,
            "orders_curr": recovered_orders,
        }

    remaining_oos_id = newly_oos[len(restock_recoveries)].sku_id if len(newly_oos) > len(restock_recoveries) else ""
    blocker_notes = (
        {remaining_oos_id: "Supplier shipment delayed by 2 days"}
        if remaining_oos_id
        else {}
    )

    # Reprice all overpriced SKUs except 1 remaining overpriced blocker
    overpriced = [
        s
        for s in skus
        if not s.is_oos_curr
        and s.competitor_lowest_price
        and ((s.price - s.competitor_lowest_price) / s.competitor_lowest_price) > 0.03
        and s.orders_prev > s.orders_curr
    ]
    overpriced.sort(key=lambda s: -round((s.orders_prev - s.orders_curr) * s.price))
    repriced_skus: list[dict[str, Any]] = []
    if len(overpriced) >= 2:
        for s in overpriced[:-1]:
            comp = float(s.competitor_lowest_price or s.price)
            new_p = float(round(min(comp * 1.015, comp + 4)))
            repriced_skus.append(
                {
                    "sku_id": s.sku_id,
                    "name": s.name,
                    "old_price": s.price,
                    "new_price": new_p,
                    "orders_curr": max(s.orders_curr + 1, round(s.orders_prev * 0.9)),
                }
            )

    updated_cancels = []
    for row in cancellations:
        if row.is_seller_fault:
            new_c = max(row.count_prev, row.count_curr - max(2, round((row.count_curr - row.count_prev) * 0.55)))
            updated_cancels.append(
                CancellationRow(
                    reason_key=row.reason_key,
                    label=row.label,
                    count_prev=row.count_prev,
                    count_curr=new_c,
                    is_seller_fault=True,
                )
            )
        else:
            updated_cancels.append(row)

    total_imp_prev = sum(int(s.impressions_prev or 0) for s in skus)
    total_imp_curr = sum(int(s.impressions_curr or 0) for s in skus)
    fu_imp = round(total_imp_curr + (total_imp_prev - total_imp_curr) * 0.55)
    total_clk_curr = sum(int(s.clicks_curr or 0) for s in skus)
    fu_clk = round(total_clk_curr * 1.15)
    updated_funnel = VisibilityFunnel(
        impressions_prev=total_imp_prev,
        impressions_curr=fu_imp,
        clicks_prev=sum(int(s.clicks_prev or 0) for s in skus),
        clicks_curr=fu_clk,
    )

    chronic_oos_count = sum(1 for s in skus if s.is_oos_prev)
    rem_oos_count = sum(1 for s in skus if s.is_oos_curr) - len(restocked_skus)
    oos_target = max(chronic_oos_count + 1, rem_oos_count - 1)

    commitments = (
        {
            "action_id": "ACT-1",
            "action": f"Restock priority OOS products ({', '.join(list(restocked_skus.keys())[:3]) or 'top OOS'})",
            "owner": profile.seller_name,
            "due_date": "Within 48 hours",
            "status": "partially_done",
            "progress_note": (
                f"{len(restocked_skus)} top OOS products restocked"
                + (f"; {remaining_oos_id} pending supplier delivery." if remaining_oos_id else ".")
            ),
        },
        {
            "action_id": "ACT-2",
            "action": "Sync warehouse stock twice daily and meet dispatch cutoff",
            "owner": profile.seller_name,
            "due_date": "Daily",
            "status": "partially_done",
            "progress_note": "Seller-stock cancellations dropped after stock sync improvements.",
        },
    )

    return FollowupAdjustment(
        days_elapsed=3,
        label="3 Days Later (Follow-up Review)",
        restocked_skus=restocked_skus,
        repriced_skus=repriced_skus,
        updated_cancellations=tuple(updated_cancels),
        updated_funnel=updated_funnel,
        oos_target_max=oos_target,
        cancellation_target_pct_max=10.0,
        default_prior_commitments=commitments,
        blocker_notes=blocker_notes,
    )


def build_scenario(scenario_id: str | None = None) -> SellerContext:
    """Build a deterministic SellerContext for scenario A, B, C, or D (default A)."""
    settings = get_settings()
    sid = (scenario_id or "A").strip().upper()
    if sid not in SCENARIO_SPECS:
        sid = "A"
    spec = SCENARIO_SPECS[sid]
    rng = random.Random(spec.seed)

    # Target causal breakdowns totaling ~100-124 orders per scenario
    if sid == "A":
        targets = dict(target_oos_orders=70, target_price_orders=20, target_vis_orders=14, target_cancel_orders=12, target_noise_orders=6)
    elif sid == "B":
        targets = dict(target_oos_orders=12, target_price_orders=46, target_vis_orders=38, target_cancel_orders=8, target_noise_orders=6)
    elif sid == "C":
        targets = dict(target_oos_orders=15, target_price_orders=12, target_vis_orders=10, target_cancel_orders=66, target_noise_orders=6)
    else:  # Scenario D: mixed + high residual
        targets = dict(target_oos_orders=26, target_price_orders=22, target_vis_orders=16, target_cancel_orders=10, target_noise_orders=30)

    sku_prefix = _PREFIX_MAP.get(spec.category, "VK")
    skus, cancels, true_counts = _generate_skus_and_cancels(
        rng=rng,
        sku_count=spec.sku_count,
        sku_prefix=sku_prefix,
        category=spec.category,
        base_price=spec.base_price,
        dominant_cause=spec.dominant_cause,
        **targets,
    )

    profile = SellerProfile(
        seller_id=spec.seller_id,
        seller_name=spec.seller_name,
        salutation=spec.salutation,
        shop_name=spec.shop_name,
        city=spec.city,
        category=spec.category,
        tenure_months=18,
        scenario_id=spec.scenario_id,
        scenario_title=spec.title,
        scenario_description=spec.description,
        is_custom_upload=False,
        prototype_notice=settings.footer_notice,
    )
    followup = _build_followup_adjustment(skus, cancels, profile)
    ctx = SellerContext(
        profile=profile,
        skus=skus,
        cancellations=cancels,
        followup=followup,
    )
    # Attach hidden ground truth via non-domain attribute for property/recovery tests ONLY (never read by diagnosis.py)
    object.__setattr__(ctx, "_ground_truth", true_counts)
    return ctx


def generate_random_trial_context(trial_seed: int, dominant: DominantCause) -> tuple[SellerContext, dict[str, Any]]:
    """Generate a random synthetic trial dataset with known hidden ground truth for evaluation."""
    rng = random.Random(trial_seed)
    sku_count = rng.randint(28, 56)
    base_price = rng.choice([450, 650, 850, 1100, 1400])
    total_target = rng.randint(90, 150)

    dom_share = rng.uniform(0.52, 0.68)
    rem = 1.0 - dom_share
    other_w = [rng.uniform(0.15, 0.4) for _ in range(3)]
    noise_share = rem * rng.uniform(0.08, 0.20)
    rem_causal = rem - noise_share
    w_sum = sum(other_w)
    other_shares = [rem_causal * (w / w_sum) for w in other_w]

    share_map = {
        "stock_outs": 0.0,
        "pricing_gaps": 0.0,
        "visibility_drop": 0.0,
        "cancellations": 0.0,
    }
    others_iter = iter(other_shares)
    for k in share_map:
        share_map[k] = dom_share if k == dominant else next(others_iter)

    skus, cancels, true_counts = _generate_skus_and_cancels(
        rng=rng,
        sku_count=sku_count,
        sku_prefix="TR",
        category="Footwear & Activewear",
        base_price=base_price,
        dominant_cause=dominant,
        target_oos_orders=max(2, round(total_target * share_map["stock_outs"])),
        target_price_orders=max(2, round(total_target * share_map["pricing_gaps"])),
        target_vis_orders=max(2, round(total_target * share_map["visibility_drop"])),
        target_cancel_orders=max(2, round(total_target * share_map["cancellations"])),
        target_noise_orders=max(1, round(total_target * noise_share)),
    )
    profile = SellerProfile(
        seller_id=f"TRIAL-{trial_seed}",
        seller_name="Trial Seller",
        salutation="Trial Partner",
        shop_name="Synthetic Trial Store",
        scenario_id="TRIAL",
    )
    ctx = SellerContext(profile=profile, skus=skus, cancellations=cancels)
    total_true = sum(true_counts.values())
    shares = {k: round((v / total_true) * 100.0, 1) if total_true else 0.0 for k, v in true_counts.items()}
    return ctx, {"dominant": dominant, "counts": true_counts, "shares": shares}


def list_all_scenarios() -> list[dict[str, str]]:
    out = []
    for sid in ("A", "B", "C", "D"):
        spec = SCENARIO_SPECS[sid]
        out.append(
            {
                "scenario_id": spec.scenario_id,
                "title": spec.title,
                "description": spec.description,
                "shop_name": spec.shop_name,
                "category": spec.category,
                "dominant_cause": spec.dominant_cause,
            }
        )
    return out

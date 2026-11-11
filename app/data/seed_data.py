"""Deterministic mock data generator for 'Metro Home & Kitchen' (60 SKUs).

All numbers are mock prototype data designed to reconcile exactly across SKU,
cancellation, and funnel views.
"""

from __future__ import annotations

from typing import Literal

from app.data.context import (
    CancellationRow,
    FollowupAdjustment,
    SellerContext,
    SellerProfile,
    SkuRecord,
    SnapshotType,
    VisibilityFunnel,
)

DATA_SEED = 42

SELLER_PROFILE = {
    "seller_id": "DEMO-DL-4821",
    "seller_name": "Arjun Mehta",
    "salutation": "Arjun ji",
    "shop_name": "Metro Home & Kitchen",
    "city": "Delhi",
    "category": "Home & Kitchen",
    "active_catalog_size": 60,
    "tenure_months": 30,
    "prototype_notice": "Prototype. Uses synthetic data. Not affiliated with any marketplace.",
}


# Legacy SkuRecord alias now lives cleanly in app.data.context.SkuRecord

def _legacy_unused_placeholder() -> None:
    pass


def build_sku_catalog() -> list[SkuRecord]:
    """Generate the exact 60-SKU catalog for Metro Home & Kitchen."""
    skus: list[SkuRecord] = []

    # Group A: 5 Chronic OOS SKUs (OOS last month and this month, 0 orders both months)
    chronic_specs = [
        ("HK-001", "Copper Bottom Handi 2L", "Cookware", 650, 640),
        ("HK-002", "Bamboo Chopping Board Large", "Kitchen Tools", 350, 345),
        ("HK-003", "Glass Spice Rack Rotating 16-Jar", "Storage", 800, 790),
        ("HK-004", "Stainless Steel Water Jug 2L", "Dining", 500, 495),
        ("HK-005", "Heavy Duty Mop & Bucket Set", "Cleaning", 750, 740),
    ]
    for sku_id, name, cat, price, comp in chronic_specs:
        skus.append(
            SkuRecord(
                sku_id=sku_id,
                name=name,
                category=cat,
                price=price,
                competitor_lowest_price=comp,
                orders_prev=0,
                orders_curr=0,
                days_out_of_stock_prev=30,
                days_out_of_stock_curr=30,
                stock_qty_curr=0,
                driver_group="chronic_oos",
            )
        )

    # Group B1: 5 Top-Selling Newly OOS SKUs (lost 68 orders, Rs 36,250)
    top_oos_specs = [
        ("HK-006", "Stainless Steel Pressure Cooker 5L", "Cookware", 800, 795, 22, 6, 20),
        ("HK-007", "Non-Stick Granite Tawa 28cm", "Cookware", 500, 495, 20, 5, 21),
        ("HK-008", "Insulated Steel Casserole Set 3Pc", "Dining", 600, 590, 19, 5, 19),
        ("HK-009", "Airtight Kitchen Storage Jars 12Pc", "Storage", 400, 395, 17, 5, 18),
        ("HK-010", "Hard Anodized Tadka Pan & Ladle", "Cookware", 250, 245, 15, 4, 19),
    ]
    for sku_id, name, cat, price, comp, last_o, curr_o, days_oos in top_oos_specs:
        skus.append(
            SkuRecord(
                sku_id=sku_id,
                name=name,
                category=cat,
                price=price,
                competitor_lowest_price=comp,
                orders_prev=last_o,
                orders_curr=curr_o,
                days_out_of_stock_prev=0,
                days_out_of_stock_curr=days_oos,
                stock_qty_curr=0,
                driver_group="newly_oos_top",
            )
        )

    # Group B2: 8 Tail Newly OOS SKUs (lost 10 orders, Rs 2,750 -> Group B total = 78 orders, Rs 39,000 = 60.0%)
    tail_oos_specs = [
        ("HK-011", "Steel Tea Strainer Twin Pack", "Kitchen Tools", 250, 245, 2, 1, 14),
        ("HK-012", "Silicone Oil Brush & Spatula", "Kitchen Tools", 250, 249, 2, 1, 15),
        ("HK-013", "Plastic Fridge Organizer Tray 2Pc", "Storage", 250, 245, 2, 1, 16),
        ("HK-014", "Microfiber Kitchen Cleaning Cloths 6Pc", "Cleaning", 250, 245, 2, 1, 14),
        ("HK-015", "Stainless Steel Vegetable Peeler Duo", "Kitchen Tools", 250, 248, 2, 1, 15),
        ("HK-016", "Ceramic Coffee Mug Set 4Pc", "Dining", 300, 295, 3, 1, 18),
        ("HK-017", "Melamine Serving Tray Pair", "Dining", 300, 295, 3, 1, 17),
        ("HK-018", "Wall Mounted Knife & Spoon Holder", "Storage", 300, 295, 2, 1, 14),
    ]
    for sku_id, name, cat, price, comp, last_o, curr_o, days_oos in tail_oos_specs:
        skus.append(
            SkuRecord(
                sku_id=sku_id,
                name=name,
                category=cat,
                price=price,
                competitor_lowest_price=comp,
                orders_prev=last_o,
                orders_curr=curr_o,
                days_out_of_stock_prev=0,
                days_out_of_stock_curr=days_oos,
                stock_qty_curr=0,
                driver_group="newly_oos_tail",
            )
        )

    # Group C: 3 Top Active SKUs priced 6.9% - 8.3% above lowest competitor (lost 26 orders, Rs 13,000 = 20.0%)
    overpriced_specs = [
        ("HK-019", "Electric Kettle 1.5L Double Wall", "Appliances", 700, 655, 22, 14, 32),
        ("HK-020", "3-Tier Modular Dish Drying Rack", "Storage", 500, 465, 24, 14, 40),
        ("HK-021", "Silicone Non-Stick Spatula Set 5Pc", "Kitchen Tools", 300, 277, 18, 10, 45),
    ]
    for sku_id, name, cat, price, comp, last_o, curr_o, stock in overpriced_specs:
        skus.append(
            SkuRecord(
                sku_id=sku_id,
                name=name,
                category=cat,
                price=price,
                competitor_lowest_price=comp,
                orders_prev=last_o,
                orders_curr=curr_o,
                days_out_of_stock_prev=0,
                days_out_of_stock_curr=0,
                stock_qty_curr=stock,
                driver_group="overpriced",
            )
        )

    # Group D: 13 Active SKUs affected by Visibility & Dispatch-SLA / Stock-Sync Drag (lost 26 orders, Rs 13,000 = 20.0%)
    vis_sla_specs = [
        ("HK-022", "Induction Base Fry Pan 24cm", "Cookware", 600, 595, 8, 6, 18),
        ("HK-023", "Steel Lunch Box 4-Container", "Dining", 600, 590, 8, 6, 22),
        ("HK-024", "Double Layer Masala Dabba", "Storage", 600, 595, 8, 6, 15),
        ("HK-025", "Glass Oil Dispenser Bottle Pair", "Dining", 600, 590, 8, 6, 25),
        ("HK-026", "Manual Vegetable Chopper 900ml", "Appliances", 600, 595, 8, 6, 20),
        ("HK-027", "Stainless Steel Kadai 26cm", "Cookware", 600, 598, 8, 6, 16),
        ("HK-028", "Foldable Dish Drainer Mat", "Cleaning", 400, 395, 8, 6, 28),
        ("HK-029", "Steel Puri Press Heavy Base", "Kitchen Tools", 400, 395, 8, 6, 19),
        ("HK-030", "Insulated Water Bottle 1L", "Dining", 400, 398, 8, 6, 30),
        ("HK-031", "Multi-Purpose Scrubber & Sponge Pack", "Cleaning", 400, 395, 8, 6, 35),
        ("HK-032", "Stainless Steel Rice Strainer Bowl", "Kitchen Tools", 500, 495, 7, 5, 24),
        ("HK-033", "Microwave Safe Mixing Bowls 3Pc", "Dining", 500, 495, 7, 5, 21),
        ("HK-034", "Magnetic Fridge Spice Containers 6Pc", "Storage", 300, 295, 6, 4, 27),
    ]
    for sku_id, name, cat, price, comp, last_o, curr_o, stock in vis_sla_specs:
        skus.append(
            SkuRecord(
                sku_id=sku_id,
                name=name,
                category=cat,
                price=price,
                competitor_lowest_price=comp,
                orders_prev=last_o,
                orders_curr=curr_o,
                days_out_of_stock_prev=0,
                days_out_of_stock_curr=0,
                stock_qty_curr=stock,
                driver_group="visibility_sla",
            )
        )

    # Group E: 26 Stable Active SKUs (Last == Curr: 125 orders, Rs 62,350)
    stable_specs = [
        ("HK-035", "Wooden Rolling Pin & Chakla", "Kitchen Tools", 350, 349, 1, 20),
        ("HK-036", "Stainless Steel Dinner Plates 6Pc", "Dining", 500, 495, 5, 34),
        ("HK-037", "Heavy Duty Gas Lighter Steel", "Kitchen Tools", 500, 499, 5, 42),
        ("HK-038", "Steel Milk Boiler 2L Whistle", "Cookware", 500, 495, 5, 25),
        ("HK-039", "Airtight Atta & Rice Container 10kg", "Storage", 500, 498, 5, 28),
        ("HK-040", "Non-Stick Appam Patra 12-Cavity", "Cookware", 500, 495, 5, 22),
        ("HK-041", "Stainless Steel Tea Kettle 1.2L", "Cookware", 500, 495, 5, 19),
        ("HK-042", "Silicone Trivet & Pot Holder Set", "Kitchen Tools", 500, 499, 5, 30),
        ("HK-043", "Steel Idli Stand 4-Plate", "Cookware", 500, 495, 5, 26),
        ("HK-044", "Modular Drawer Cutlery Organizer", "Storage", 500, 495, 5, 33),
        ("HK-045", "Glass Baking Dish Rectangular 1.8L", "Dining", 500, 498, 5, 21),
        ("HK-046", "Stainless Steel Vacuum Flask 750ml", "Dining", 500, 495, 5, 29),
        ("HK-047", "Sink Corner Waste Strainer Basket", "Cleaning", 500, 495, 5, 40),
        ("HK-048", "Cast Iron Grill Pan 24cm", "Cookware", 600, 595, 4, 18),
        ("HK-049", "Ceramic Dinner Bowl Set 6Pc", "Dining", 600, 595, 4, 24),
        ("HK-050", "Electric Hand Blender 250W", "Appliances", 600, 598, 4, 16),
        ("HK-051", "Stainless Steel Saucepan with Lid", "Cookware", 600, 595, 4, 20),
        ("HK-052", "Under-Shelf Kitchen Basket Rack", "Storage", 600, 595, 4, 27),
        ("HK-053", "3-Jar Glass Dry Fruit Gift Box", "Dining", 600, 595, 4, 22),
        ("HK-054", "Non-Stick Grill Sandwich Toaster", "Appliances", 600, 598, 4, 19),
        ("HK-055", "Heavy Steel Colander Mesh Basket", "Kitchen Tools", 600, 595, 4, 25),
        ("HK-056", "Stainless Steel Spoon & Fork Set 24Pc", "Dining", 400, 395, 6, 45),
        ("HK-057", "Silicone Kitchen Sink Splash Guard", "Cleaning", 400, 399, 6, 38),
        ("HK-058", "Plastic Roti Maker & Rolling Sheet", "Kitchen Tools", 400, 395, 6, 31),
        ("HK-059", "Steel Salt & Pepper Shaker Pair", "Dining", 400, 398, 6, 50),
        ("HK-060", "Reusable Cotton Mesh Produce Bags 8Pc", "Storage", 400, 395, 8, 44),
    ]
    for sku_id, name, cat, price, comp, orders, stock in stable_specs:
        skus.append(
            SkuRecord(
                sku_id=sku_id,
                name=name,
                category=cat,
                price=price,
                competitor_lowest_price=comp,
                orders_prev=orders,
                orders_curr=orders,
                days_out_of_stock_prev=0,
                days_out_of_stock_curr=0,
                stock_qty_curr=stock,
                driver_group="stable",
            )
        )

    return skus


SKU_CATALOG: list[SkuRecord] = build_sku_catalog()

# Cancellation breakdown: exact match to round(8% * 400) = 32 and round(14% * 270) = 38
CANCELLATION_BREAKDOWN = {
    "last_month": {
        "total_orders": 400,
        "total_cancelled": 32,
        "cancellation_rate_pct": 8.0,
        "reasons": {
            "seller_stock_unavailable": {"count": 6, "label": "Seller cancelled - stock unavailable"},
            "dispatch_sla_breach": {"count": 5, "label": "Auto-cancelled - dispatch SLA delay"},
            "buyer_initiated": {"count": 15, "label": "Buyer initiated cancellation"},
            "courier_address_issue": {"count": 6, "label": "Courier / address / COD issue"},
        },
    },
    "this_month": {
        "total_orders": 270,
        "total_cancelled": 38,
        "cancellation_rate_pct": 14.0,
        "reasons": {
            "seller_stock_unavailable": {"count": 14, "label": "Seller cancelled - stock unavailable"},
            "dispatch_sla_breach": {"count": 9, "label": "Auto-cancelled - dispatch SLA delay"},
            "buyer_initiated": {"count": 11, "label": "Buyer initiated cancellation"},
            "courier_address_issue": {"count": 4, "label": "Courier / address / COD issue"},
        },
    },
}

# Visibility funnel: impressions -> clicks -> placed orders
VISIBILITY_FUNNEL = {
    "last_month": {
        "impressions": 80000,
        "clicks": 3200,
        "orders": 400,
    },
    "this_month": {
        "impressions": 68800,  # -14.0% due to 18 OOS listings losing search rank
        "clicks": 2477,        # CTR 3.60% vs 4.00%
        "orders": 270,         # CVR 10.90% vs 12.50%
    },
}

# Follow-up snapshot ("3 days later" simulation):
# Shows partial recovery after the seller acts on earlier commitments, leaving 1-2 blockers to close.
FOLLOWUP_SNAPSHOT = {
    "days_elapsed": 3,
    "label": "3 Days Later (Follow-up Review)",
    "metrics": {
        "projected_monthly_sales": 175500,   # Rs 5,850/day pace vs Rs 4,500/day (Rs 1,35,000) before
        "projected_monthly_orders": 351,     # +30% run-rate rebound vs 270
        "asp": 500,
        "out_of_stock_skus": 9,              # Down from 18 -> 9 (target <= 8; 1 top SKU still awaiting supplier)
        "cancellation_rate_pct": 10.5,       # Down from 14.0% -> 10.5% (target <= 10.0%)
        "seller_stock_cancel_rate_pct": 1.4, # Down sharply from 5.2% after stock-sync fix
        "dispatch_sla_cancel_rate_pct": 3.6, # Still slightly elevated; courier pickup cutoff missed twice
        "impressions_daily_pace": 2610,      # Equivalent to ~78,300/month (+13.8% vs crisis month)
    },
    "restocked_top_skus": [
        "HK-006 (Stainless Steel Pressure Cooker 5L)",
        "HK-007 (Non-Stick Granite Tawa 28cm)",
        "HK-008 (Insulated Steel Casserole Set 3Pc)",
        "HK-010 (Hard Anodized Tadka Pan & Ladle)",
    ],
    "remaining_oos_priority_sku": {
        "sku_id": "HK-009",
        "name": "Airtight Kitchen Storage Jars 12Pc",
        "price": 400,
        "estimated_daily_loss": 227,
        "blocker": "Supplier shipment delayed by 2 days",
    },
    "repriced_skus": [
        {"sku_id": "HK-019", "name": "Electric Kettle 1.5L Double Wall", "old_price": 700, "new_price": 659, "competitor_lowest": 655},
        {"sku_id": "HK-020", "name": "3-Tier Modular Dish Drying Rack", "old_price": 500, "new_price": 469, "competitor_lowest": 465},
    ],
    "remaining_overpriced_sku": {
        "sku_id": "HK-021",
        "name": "Silicone Non-Stick Spatula Set 5Pc",
        "current_price": 300,
        "competitor_lowest": 277,
        "gap_pct": 8.3,
    },
    "default_prior_commitments": [
        {
            "action_id": "ACT-1",
            "action": "Restock top 5 out-of-stock products and enable low-stock alerts",
            "owner": "Arjun Mehta",
            "due_date": "Within 48 hours",
            "status": "partially_done",
            "progress_note": "4 of top 5 restocked (HK-006, HK-007, HK-008, HK-010). HK-009 still out of stock awaiting supplier; total OOS down from 18 to 9 (target <= 8).",
        },
        {
            "action_id": "ACT-2",
            "action": "Sync warehouse stock twice daily and clear morning dispatch before 2 PM cutoff",
            "owner": "Arjun Mehta",
            "due_date": "Daily",
            "status": "partially_done",
            "progress_note": "Stock-unavailable cancellations fell sharply, bringing total cancellation rate from 14% to 10.5% (just shy of <= 10% target due to 2 dispatch delays).",
        },
        {
            "action_id": "ACT-3",
            "action": "Match competitor pricing within 2% on 3 overpriced products",
            "owner": "Arjun Mehta",
            "due_date": "Within 3 days",
            "status": "partially_done",
            "progress_note": "Repriced Kettle (HK-019) and Dish Rack (HK-020); Spatula Set (HK-021) is still 8.3% above competitor.",
        },
    ],
    "targets": {
        "out_of_stock_skus_max": 8,
        "cancellation_rate_pct_max": 10.0,
        "monthly_sales_pace_target": 180000,
    },
}


def build_seed_seller_context() -> SellerContext:
    """Wrap the Phase 2 baseline fixture into a per-session SellerContext instance."""
    profile = SellerProfile(
        seller_id=SELLER_PROFILE["seller_id"],
        seller_name=SELLER_PROFILE["seller_name"],
        salutation=SELLER_PROFILE["salutation"],
        shop_name=SELLER_PROFILE["shop_name"],
        city=SELLER_PROFILE["city"],
        category=SELLER_PROFILE["category"],
        tenure_months=SELLER_PROFILE["tenure_months"],
        scenario_id="A",
        scenario_title="Metro Home & Kitchen (Stock-out Dominant)",
        scenario_description="Synthetic 60-SKU seller fixture undergoing Phase 2 SellerContext migration.",
        is_custom_upload=False,
        prototype_notice=SELLER_PROFILE["prototype_notice"],
    )
    cancellations = [
        CancellationRow(
            reason_key="seller_stock_unavailable",
            label="Seller cancelled - stock unavailable",
            count_prev=6,
            count_curr=14,
            is_seller_fault=True,
        ),
        CancellationRow(
            reason_key="dispatch_sla_breach",
            label="Auto-cancelled - dispatch SLA delay",
            count_prev=5,
            count_curr=9,
            is_seller_fault=True,
        ),
        CancellationRow(
            reason_key="buyer_initiated",
            label="Buyer initiated cancellation",
            count_prev=15,
            count_curr=11,
            is_seller_fault=False,
        ),
        CancellationRow(
            reason_key="courier_address_issue",
            label="Courier / address / COD issue",
            count_prev=6,
            count_curr=4,
            is_seller_fault=False,
        ),
    ]
    funnel = VisibilityFunnel(
        impressions_prev=VISIBILITY_FUNNEL["last_month"]["impressions"],
        impressions_curr=VISIBILITY_FUNNEL["this_month"]["impressions"],
        clicks_prev=VISIBILITY_FUNNEL["last_month"]["clicks"],
        clicks_curr=VISIBILITY_FUNNEL["this_month"]["clicks"],
    )
    followup = FollowupAdjustment(
        days_elapsed=3,
        label=FOLLOWUP_SNAPSHOT["label"],
        restocked_skus={
            "HK-006": {"days_out_of_stock_curr": 0, "stock_qty_curr": 30},
            "HK-007": {"days_out_of_stock_curr": 0, "stock_qty_curr": 28},
            "HK-008": {"days_out_of_stock_curr": 0, "stock_qty_curr": 24},
            "HK-010": {"days_out_of_stock_curr": 0, "stock_qty_curr": 25},
        },
        repriced_skus=list(FOLLOWUP_SNAPSHOT["repriced_skus"]),
        oos_target_max=FOLLOWUP_SNAPSHOT["targets"]["out_of_stock_skus_max"],
        cancellation_target_pct_max=FOLLOWUP_SNAPSHOT["targets"]["cancellation_rate_pct_max"],
        monthly_sales_pace_target=FOLLOWUP_SNAPSHOT["targets"]["monthly_sales_pace_target"],
        default_prior_commitments=tuple(dict(item) for item in FOLLOWUP_SNAPSHOT["default_prior_commitments"]),
        blocker_notes={"HK-009": "Supplier shipment delayed by 2 days"},
    )
    return SellerContext(
        profile=profile,
        skus=build_sku_catalog(),
        cancellations=cancellations,
        funnel=funnel,
        followup=followup,
    )


def get_scenario_context(scenario_id: str | None = None) -> SellerContext:
    """Return a fresh SellerContext for the requested built-in scenario."""
    return build_seed_seller_context()


def list_scenarios() -> list[dict[str, str]]:
    """Return metadata descriptors for built-in demo scenarios."""
    ctx = build_seed_seller_context()
    return [
        {
            "scenario_id": ctx.profile.scenario_id,
            "title": ctx.profile.scenario_title,
            "description": ctx.profile.scenario_description,
            "shop_name": ctx.profile.shop_name,
            "category": ctx.profile.category,
        }
    ]



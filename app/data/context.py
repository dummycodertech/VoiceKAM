"""Per-session SellerContext domain models (zero global store state)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

SnapshotType = Literal["current", "followup"]


@dataclass(frozen=True)
class SellerProfile:
    seller_id: str = "CUSTOM-SELLER"
    seller_name: str = "Seller Partner"
    salutation: str = "Seller Partner"
    shop_name: str = "Uploaded Store"
    city: str = ""
    category: str = "General"
    tenure_months: int = 12
    scenario_id: str = "custom"
    scenario_title: str = "Custom Store Data"
    scenario_description: str = "User-uploaded SKU and marketplace performance dataset."
    is_custom_upload: bool = False
    period_prev_label: str = "Last Month"
    period_curr_label: str = "This Month"
    prototype_notice: str = "VoiceKAM open-source prototype. Not affiliated with any marketplace."

    def to_dict(self, sku_count: int = 0) -> dict[str, Any]:
        return {
            "seller_id": self.seller_id,
            "seller_name": self.seller_name,
            "salutation": self.salutation,
            "shop_name": self.shop_name,
            "city": self.city,
            "category": self.category,
            "active_catalog_size": sku_count,
            "tenure_months": self.tenure_months,
            "scenario_id": self.scenario_id,
            "scenario_title": self.scenario_title,
            "scenario_description": self.scenario_description,
            "is_custom_upload": self.is_custom_upload,
            "period_prev_label": self.period_prev_label,
            "period_curr_label": self.period_curr_label,
            "prototype_notice": self.prototype_notice,
        }


@dataclass(frozen=True)
class SkuRecord:
    """Single SKU across baseline (prev) and current periods.

    Required schema fields: sku_id, name, price, orders_prev, orders_curr.
    Optional schema fields default to None / "General" so missing columns
    degrade gracefully without fabricated assumptions.
    """

    sku_id: str
    name: str
    price: float
    orders_prev: int
    orders_curr: int
    category: str = "General"
    competitor_lowest_price: float | None = None
    days_out_of_stock_curr: int | None = None
    days_out_of_stock_prev: int | None = None
    stock_qty_curr: int | None = None
    impressions_prev: int | None = None
    impressions_curr: int | None = None
    clicks_prev: int | None = None
    clicks_curr: int | None = None
    driver_group: str = ""  # Optional hidden test-only ground truth; NEVER read by diagnosis engine

    @property
    def is_oos_curr(self) -> bool:
        if self.days_out_of_stock_curr is not None and self.days_out_of_stock_curr > 0:
            return True
        if self.stock_qty_curr is not None and self.stock_qty_curr <= 0:
            return True
        return False

    @property
    def is_oos_prev(self) -> bool:
        if self.days_out_of_stock_prev is not None and self.days_out_of_stock_prev > 0:
            return True
        if self.orders_prev == 0 and self.days_out_of_stock_curr is not None and self.days_out_of_stock_curr >= 28:
            return True
        return False

    # Backward-compatible attribute aliases for existing callers/tests
    @property
    def last_month_orders(self) -> int:
        return self.orders_prev

    @property
    def this_month_orders(self) -> int:
        return self.orders_curr

    @property
    def last_month_oos(self) -> bool:
        return self.is_oos_prev

    @property
    def this_month_oos(self) -> bool:
        return self.is_oos_curr

    @property
    def days_oos_this_month(self) -> int:
        return int(self.days_out_of_stock_curr or 0)

    @property
    def stock_qty(self) -> int:
        return int(self.stock_qty_curr or 0)


@dataclass(frozen=True)
class CancellationRow:
    reason_key: str
    label: str
    count_prev: int
    count_curr: int
    is_seller_fault: bool


@dataclass(frozen=True)
class VisibilityFunnel:
    impressions_prev: int | None = None
    impressions_curr: int | None = None
    clicks_prev: int | None = None
    clicks_curr: int | None = None

    @property
    def has_impressions(self) -> bool:
        return self.impressions_prev is not None and self.impressions_curr is not None

    @property
    def has_clicks(self) -> bool:
        return self.clicks_prev is not None and self.clicks_curr is not None


@dataclass(frozen=True)
class DataCompleteness:
    stock_oos_assessable: bool
    has_days_oos: bool
    has_stock_qty: bool
    pricing_assessable: bool
    visibility_assessable: bool
    has_impressions: bool
    has_clicks: bool
    cancellations_assessable: bool
    missing_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_oos_assessable": self.stock_oos_assessable,
            "has_days_oos": self.has_days_oos,
            "has_stock_qty": self.has_stock_qty,
            "pricing_assessable": self.pricing_assessable,
            "visibility_assessable": self.visibility_assessable,
            "has_impressions": self.has_impressions,
            "has_clicks": self.has_clicks,
            "cancellations_assessable": self.cancellations_assessable,
            "missing_fields": list(self.missing_fields),
        }


@dataclass(frozen=True)
class FollowupAdjustment:
    """Follow-up (+N days) delta specification applied to cloned records."""

    days_elapsed: int = 3
    label: str = "3 Days Later (Follow-up Review)"
    restocked_skus: dict[str, dict[str, int]] = field(default_factory=dict)
    repriced_skus: list[dict[str, Any]] = field(default_factory=list)
    updated_cancellations: tuple[CancellationRow, ...] = ()
    updated_funnel: VisibilityFunnel | None = None
    oos_target_max: int | None = None
    cancellation_target_pct_max: float | None = None
    monthly_sales_pace_target: int = 0
    default_prior_commitments: tuple[dict[str, Any], ...] = ()
    blocker_notes: dict[str, str] = field(default_factory=dict)


@dataclass
class SellerContext:
    """Self-contained store dataset passed explicitly through session -> engine -> prompt."""

    profile: SellerProfile
    skus: list[SkuRecord]
    cancellations: list[CancellationRow] = field(default_factory=list)
    funnel: VisibilityFunnel | None = None
    completeness: DataCompleteness | None = None
    followup: FollowupAdjustment | None = None

    def __post_init__(self) -> None:
        if self.funnel is None:
            self.funnel = derive_funnel_from_skus(self.skus)
        if self.completeness is None:
            self.completeness = compute_data_completeness(self.skus, self.cancellations, self.funnel)

    def resolve_skus(self, snapshot: SnapshotType = "current") -> list[SkuRecord]:
        if snapshot != "followup" or self.followup is None:
            return list(self.skus)
        reprice_map = {str(r["sku_id"]): r for r in self.followup.repriced_skus}
        cloned: list[SkuRecord] = []
        for s in self.skus:
            updates: dict[str, Any] = {}
            if s.sku_id in self.followup.restocked_skus:
                adj = self.followup.restocked_skus[s.sku_id]
                if "orders_curr" in adj:
                    updates["orders_curr"] = int(adj["orders_curr"])
                updates["days_out_of_stock_curr"] = int(adj.get("days_out_of_stock_curr", 0))
                updates["stock_qty_curr"] = int(adj.get("stock_qty_curr", max(10, s.stock_qty_curr or 20)))
            if s.sku_id in reprice_map:
                rp = reprice_map[s.sku_id]
                updates["price"] = float(rp["new_price"])
                if "orders_curr" in rp:
                    updates["orders_curr"] = int(rp["orders_curr"])
            cloned.append(replace(s, **updates) if updates else s)
        return cloned

    def resolve_cancellations(self, snapshot: SnapshotType = "current") -> list[CancellationRow]:
        if snapshot == "followup" and self.followup is not None and self.followup.updated_cancellations:
            return list(self.followup.updated_cancellations)
        return list(self.cancellations)

    def resolve_funnel(self, snapshot: SnapshotType = "current") -> VisibilityFunnel | None:
        if snapshot == "followup" and self.followup is not None and self.followup.updated_funnel is not None:
            return self.followup.updated_funnel
        return self.funnel

    def summary_dict(self) -> dict[str, Any]:
        orders_prev = sum(s.orders_prev for s in self.skus)
        orders_curr = sum(s.orders_curr for s in self.skus)
        sales_prev = round(sum(s.orders_prev * s.price for s in self.skus))
        sales_curr = round(sum(s.orders_curr * s.price for s in self.skus))
        comp = self.completeness or compute_data_completeness(self.skus, self.cancellations, self.funnel)
        return {
            "seller": self.profile.to_dict(sku_count=len(self.skus)),
            "sku_count": len(self.skus),
            "orders_prev": orders_prev,
            "orders_curr": orders_curr,
            "sales_prev": sales_prev,
            "sales_curr": sales_curr,
            "has_followup": self.followup is not None,
            "completeness": comp.to_dict(),
        }


def derive_funnel_from_skus(skus: list[SkuRecord]) -> VisibilityFunnel:
    has_imp = any(s.impressions_prev is not None and s.impressions_curr is not None for s in skus)
    has_clk = any(s.clicks_prev is not None and s.clicks_curr is not None for s in skus)
    return VisibilityFunnel(
        impressions_prev=sum(int(s.impressions_prev or 0) for s in skus) if has_imp else None,
        impressions_curr=sum(int(s.impressions_curr or 0) for s in skus) if has_imp else None,
        clicks_prev=sum(int(s.clicks_prev or 0) for s in skus) if has_clk else None,
        clicks_curr=sum(int(s.clicks_curr or 0) for s in skus) if has_clk else None,
    )


def compute_data_completeness(
    skus: list[SkuRecord],
    cancellations: list[CancellationRow],
    funnel: VisibilityFunnel | None,
) -> DataCompleteness:
    has_days_oos = any(s.days_out_of_stock_curr is not None for s in skus)
    has_stock_qty = any(s.stock_qty_curr is not None for s in skus)
    stock_oos_assessable = has_days_oos or has_stock_qty
    pricing_assessable = any(
        s.competitor_lowest_price is not None and s.competitor_lowest_price > 0 for s in skus
    )
    has_imp = bool(funnel and funnel.has_impressions)
    has_clk = bool(funnel and funnel.has_clicks)
    visibility_assessable = has_imp
    cancellations_assessable = len(cancellations) > 0

    missing: list[str] = []
    if not stock_oos_assessable:
        missing.append("days_out_of_stock_curr / stock_qty_curr (stock-out attribution unavailable)")
    elif not has_days_oos:
        missing.append("days_out_of_stock_curr (using binary stock_qty_curr == 0 only)")
    if not pricing_assessable:
        missing.append("competitor_lowest_price (price-gap attribution unavailable)")
    if not has_imp:
        missing.append("impressions_prev / impressions_curr (visibility-funnel attribution unavailable)")
    elif not has_clk:
        missing.append("clicks_prev / clicks_curr (visibility conversion check partial)")
    if not cancellations_assessable:
        missing.append("cancellations table (cancellation-breakdown attribution unavailable)")

    return DataCompleteness(
        stock_oos_assessable=stock_oos_assessable,
        has_days_oos=has_days_oos,
        has_stock_qty=has_stock_qty,
        pricing_assessable=pricing_assessable,
        visibility_assessable=visibility_assessable,
        has_impressions=has_imp,
        has_clicks=has_clk,
        cancellations_assessable=cancellations_assessable,
        missing_fields=tuple(missing),
    )

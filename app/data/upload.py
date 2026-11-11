"""Strict, RAM-only CSV and JSON dataset parser, validator, and TTL store.

Uploaded seller datasets live exclusively in memory, expire alongside session TTL,
and are never written to disk or logs.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings
from app.data.context import (
    CancellationRow,
    SellerContext,
    SellerProfile,
    SkuRecord,
)

REQUIRED_SKU_COLUMNS: tuple[str, ...] = (
    "sku_id",
    "name",
    "price",
    "orders_prev",
    "orders_curr",
)

OPTIONAL_SKU_COLUMNS: tuple[str, ...] = (
    "category",
    "competitor_lowest_price",
    "days_out_of_stock_curr",
    "days_out_of_stock_prev",
    "stock_qty_curr",
    "impressions_prev",
    "impressions_curr",
    "clicks_prev",
    "clicks_curr",
)

REQUIRED_CANCELLATION_COLUMNS: tuple[str, ...] = (
    "reason",
    "count_prev",
    "count_curr",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_SCRIPT_OR_STYLE_RE = re.compile(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SELLER_FAULT_KEYWORDS = ("stock", "seller", "dispatch", "sla", "fulfill", "warehouse", "inventory", "pack")


class DatasetValidationError(ValueError):
    """Raised when uploaded CSV/JSON fails structural, type, or safety validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _check_formula_safe(raw: Any, location: str, errors: list[str]) -> bool:
    if isinstance(raw, str) and raw.startswith(_FORMULA_PREFIXES):
        errors.append(
            f"{location}: value starting with '{raw[0]}' is blocked for spreadsheet formula safety."
        )
        return False
    return True


def _sanitize_text(raw: Any, location: str, errors: list[str], *, required: bool = False, max_len: int = 160) -> str:
    if raw is None:
        if required:
            errors.append(f"{location}: required text value is missing.")
        return ""
    text = str(raw)
    if not _check_formula_safe(text, location, errors):
        return ""
    stripped = _SCRIPT_OR_STYLE_RE.sub("", text)
    stripped = _HTML_TAG_RE.sub("", stripped).strip()
    if required and not stripped:
        errors.append(f"{location}: value cannot be empty.")
        return ""
    if len(stripped) > max_len:
        stripped = stripped[:max_len]
    return stripped


def _parse_optional_number(
    raw: Any,
    location: str,
    errors: list[str],
    *,
    is_int: bool = False,
    min_value: float = 0.0,
    max_value: float | None = None,
    strictly_positive: bool = False,
) -> int | float | None:
    if raw is None or isinstance(raw, bool):
        if isinstance(raw, bool):
            errors.append(f"{location}: expected a number, got boolean.")
        return None
    if isinstance(raw, str):
        if not raw.strip():
            return None
        if not _check_formula_safe(raw, location, errors):
            return None
        cleaned = raw.strip().replace(",", "")
    else:
        cleaned = raw

    try:
        val = float(cleaned)
    except (TypeError, ValueError):
        errors.append(f"{location}: '{raw}' is not a valid number.")
        return None

    if not math.isfinite(val):
        errors.append(f"{location}: value must be a finite number.")
        return None
    if strictly_positive and val <= 0:
        errors.append(f"{location}: must be greater than 0 (got {val}).")
        return None
    if val < min_value:
        errors.append(f"{location}: cannot be less than {min_value} (got {val}).")
        return None
    if max_value is not None and val > max_value:
        errors.append(f"{location}: cannot exceed {max_value} (got {val}).")
        return None
    if is_int:
        if not float(val).is_integer():
            errors.append(f"{location}: expected a whole integer, got {val}.")
            return None
        return int(val)
    return round(val, 2)


def _infer_seller_fault(reason_label: str, raw_flag: Any, location: str, errors: list[str]) -> bool:
    if raw_flag is not None and str(raw_flag).strip() != "":
        if isinstance(raw_flag, bool):
            return raw_flag
        low = str(raw_flag).strip().lower()
        if low in {"true", "1", "yes", "y"}:
            return True
        if low in {"false", "0", "no", "n"}:
            return False
        errors.append(f"{location}: invalid boolean for is_seller_fault ('{raw_flag}').")
        return False
    low_reason = reason_label.lower()
    return any(k in low_reason for k in _SELLER_FAULT_KEYWORDS)


def parse_csv_rows(raw_bytes: bytes, *, max_bytes: int, label: str = "CSV file") -> list[dict[str, str]]:
    if len(raw_bytes) == 0:
        raise DatasetValidationError([f"{label} is empty."])
    if len(raw_bytes) > max_bytes:
        raise DatasetValidationError(
            [f"{label} exceeds the {max_bytes // 1024} KB ({max_bytes:,} bytes) size limit."]
        )
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw_bytes.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise DatasetValidationError([f"{label} must be UTF-8 encoded text."]) from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DatasetValidationError([f"{label} is missing a header row."])

    # Normalize header keys cleanly (strip spaces & BOM leftovers) without formula chars
    normalized_rows: list[dict[str, str]] = []
    for row in reader:
        if None in row and row[None]:
            raise DatasetValidationError(
                [f"{label} row {len(normalized_rows) + 2}: has more columns than the header."]
            )
        clean_row = {(k.strip() if k else ""): (v if v is not None else "") for k, v in row.items()}
        if not any(str(v).strip() for v in clean_row.values()):
            continue
        normalized_rows.append(clean_row)
    return normalized_rows


def _validate_sku_rows(
    rows: list[dict[str, Any]],
    *,
    max_skus: int,
    errors: list[str],
) -> list[SkuRecord]:
    if not rows:
        errors.append("Dataset must contain at least 1 SKU row.")
        return []
    if len(rows) > max_skus:
        errors.append(f"Upload contains {len(rows):,} SKUs; maximum allowed is {max_skus:,}.")
        return []

    first_keys = {str(k).strip() for k in rows[0].keys()}
    missing_cols = [c for c in REQUIRED_SKU_COLUMNS if c not in first_keys]
    if missing_cols:
        errors.append(
            "Missing required SKU columns: " + ", ".join(missing_cols) + "."
        )
        return []

    records: list[SkuRecord] = []
    seen_ids: dict[str, int] = {}

    for idx, row in enumerate(rows, start=2):
        row_label = f"SKU row {idx}"
        if not isinstance(row, dict):
            errors.append(f"{row_label}: must be an object/dictionary.")
            continue

        # Check all string cells in the row for formula prefixes first
        for col_name, cell_val in row.items():
            if col_name and isinstance(cell_val, str) and cell_val.startswith(_FORMULA_PREFIXES):
                _check_formula_safe(cell_val, f"{row_label} column '{col_name}'", errors)

        sku_id = _sanitize_text(row.get("sku_id"), f"{row_label} column 'sku_id'", errors, required=True, max_len=64)
        name = _sanitize_text(row.get("name"), f"{row_label} column 'name'", errors, required=True, max_len=160)
        category = _sanitize_text(row.get("category"), f"{row_label} column 'category'", errors, required=False, max_len=80) or "General"

        if sku_id:
            if sku_id in seen_ids:
                errors.append(f"{row_label}: duplicate sku_id '{sku_id}' (first seen on row {seen_ids[sku_id]}).")
            else:
                seen_ids[sku_id] = idx

        price = _parse_optional_number(
            row.get("price"), f"{row_label} column 'price'", errors, is_int=False, strictly_positive=True
        )
        if row.get("orders_prev") is None or str(row.get("orders_prev")).strip() == "":
            errors.append(f"{row_label} column 'orders_prev': required value is missing.")
            orders_prev = None
        else:
            orders_prev = _parse_optional_number(
                row.get("orders_prev"), f"{row_label} column 'orders_prev'", errors, is_int=True, min_value=0
            )
        if row.get("orders_curr") is None or str(row.get("orders_curr")).strip() == "":
            errors.append(f"{row_label} column 'orders_curr': required value is missing.")
            orders_curr = None
        else:
            orders_curr = _parse_optional_number(
                row.get("orders_curr"), f"{row_label} column 'orders_curr'", errors, is_int=True, min_value=0
            )

        comp_price = _parse_optional_number(
            row.get("competitor_lowest_price"),
            f"{row_label} column 'competitor_lowest_price'",
            errors,
            is_int=False,
            strictly_positive=True,
        )
        days_oos_curr = _parse_optional_number(
            row.get("days_out_of_stock_curr"),
            f"{row_label} column 'days_out_of_stock_curr'",
            errors,
            is_int=True,
            min_value=0,
            max_value=31,
        )
        days_oos_prev = _parse_optional_number(
            row.get("days_out_of_stock_prev"),
            f"{row_label} column 'days_out_of_stock_prev'",
            errors,
            is_int=True,
            min_value=0,
            max_value=31,
        )
        stock_qty_curr = _parse_optional_number(
            row.get("stock_qty_curr"),
            f"{row_label} column 'stock_qty_curr'",
            errors,
            is_int=True,
            min_value=0,
        )
        imp_prev = _parse_optional_number(
            row.get("impressions_prev"),
            f"{row_label} column 'impressions_prev'",
            errors,
            is_int=True,
            min_value=0,
        )
        imp_curr = _parse_optional_number(
            row.get("impressions_curr"),
            f"{row_label} column 'impressions_curr'",
            errors,
            is_int=True,
            min_value=0,
        )
        clk_prev = _parse_optional_number(
            row.get("clicks_prev"),
            f"{row_label} column 'clicks_prev'",
            errors,
            is_int=True,
            min_value=0,
        )
        clk_curr = _parse_optional_number(
            row.get("clicks_curr"),
            f"{row_label} column 'clicks_curr'",
            errors,
            is_int=True,
            min_value=0,
        )

        if imp_prev is not None and clk_prev is not None and clk_prev > imp_prev:
            errors.append(f"{row_label}: clicks_prev ({clk_prev}) cannot exceed impressions_prev ({imp_prev}).")
        if imp_curr is not None and clk_curr is not None and clk_curr > imp_curr:
            errors.append(f"{row_label}: clicks_curr ({clk_curr}) cannot exceed impressions_curr ({imp_curr}).")

        if price is None or orders_prev is None or orders_curr is None or not sku_id or not name:
            continue

        records.append(
            SkuRecord(
                sku_id=sku_id,
                name=name,
                category=category,
                price=float(price),
                orders_prev=int(orders_prev),
                orders_curr=int(orders_curr),
                competitor_lowest_price=float(comp_price) if comp_price is not None else None,
                days_out_of_stock_curr=int(days_oos_curr) if days_oos_curr is not None else None,
                days_out_of_stock_prev=int(days_oos_prev) if days_oos_prev is not None else None,
                stock_qty_curr=int(stock_qty_curr) if stock_qty_curr is not None else None,
                impressions_prev=int(imp_prev) if imp_prev is not None else None,
                impressions_curr=int(imp_curr) if imp_curr is not None else None,
                clicks_prev=int(clk_prev) if clk_prev is not None else None,
                clicks_curr=int(clk_curr) if clk_curr is not None else None,
            )
        )

    return records


def _validate_cancellations_rows(
    rows: list[dict[str, Any]],
    errors: list[str],
) -> list[CancellationRow]:
    if not rows:
        return []
    if len(rows) > 100:
        errors.append(f"Cancellations table has {len(rows)} rows; maximum allowed is 100.")
        return []

    first_keys = {str(k).strip() for k in rows[0].keys()}
    missing_cols = [c for c in REQUIRED_CANCELLATION_COLUMNS if c not in first_keys]
    if missing_cols:
        errors.append(
            "Missing required cancellations columns: " + ", ".join(missing_cols) + "."
        )
        return []

    parsed: list[CancellationRow] = []
    for idx, row in enumerate(rows, start=2):
        loc = f"Cancellations row {idx}"
        if not isinstance(row, dict):
            errors.append(f"{loc}: must be an object.")
            continue
        reason = _sanitize_text(row.get("reason"), f"{loc} column 'reason'", errors, required=True, max_len=120)
        count_prev = _parse_optional_number(row.get("count_prev"), f"{loc} column 'count_prev'", errors, is_int=True, min_value=0)
        count_curr = _parse_optional_number(row.get("count_curr"), f"{loc} column 'count_curr'", errors, is_int=True, min_value=0)
        is_seller = _infer_seller_fault(reason, row.get("is_seller_fault"), f"{loc} column 'is_seller_fault'", errors)
        if not reason or count_prev is None or count_curr is None:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", reason.lower()).strip("_") or f"reason_{idx}"
        parsed.append(
            CancellationRow(
                reason_key=key,
                label=reason,
                count_prev=int(count_prev),
                count_curr=int(count_curr),
                is_seller_fault=is_seller,
            )
        )
    return parsed


def _build_profile_from_meta(meta: dict[str, Any], errors: list[str], settings: Settings) -> SellerProfile:
    seller_name = _sanitize_text(meta.get("seller_name"), "seller.seller_name", errors, max_len=80) or "Seller Partner"
    salutation = _sanitize_text(meta.get("salutation"), "seller.salutation", errors, max_len=80) or seller_name
    shop_name = _sanitize_text(meta.get("shop_name"), "seller.shop_name", errors, max_len=100) or "Custom Uploaded Store"
    city = _sanitize_text(meta.get("city"), "seller.city", errors, max_len=60) or ""
    category = _sanitize_text(meta.get("category"), "seller.category", errors, max_len=60) or "Mixed Catalog"
    prev_lbl = _sanitize_text(meta.get("period_prev_label"), "seller.period_prev_label", errors, max_len=40) or "Previous Period"
    curr_lbl = _sanitize_text(meta.get("period_curr_label"), "seller.period_curr_label", errors, max_len=40) or "Current Period"
    return SellerProfile(
        seller_id="CUSTOM-UPLOAD",
        seller_name=seller_name,
        salutation=salutation,
        shop_name=shop_name,
        city=city,
        category=category,
        tenure_months=12,
        scenario_id="custom",
        scenario_title=shop_name,
        scenario_description="Custom seller dataset uploaded in this browser session (RAM only).",
        is_custom_upload=True,
        period_prev_label=prev_lbl,
        period_curr_label=curr_lbl,
        prototype_notice=settings.footer_notice,
    )


def parse_uploaded_csv(
    sku_csv_bytes: bytes,
    *,
    cancellations_csv_bytes: bytes | None = None,
    seller_meta: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> SellerContext:
    cfg = settings or get_settings()
    errors: list[str] = []

    sku_rows = parse_csv_rows(sku_csv_bytes, max_bytes=cfg.max_upload_bytes, label="SKU CSV")
    skus = _validate_sku_rows(sku_rows, max_skus=cfg.max_upload_skus, errors=errors)

    cancels: list[CancellationRow] = []
    if cancellations_csv_bytes is not None and len(cancellations_csv_bytes.strip()) > 0:
        c_rows = parse_csv_rows(
            cancellations_csv_bytes,
            max_bytes=min(cfg.max_upload_bytes, 262_144),
            label="Cancellations CSV",
        )
        cancels = _validate_cancellations_rows(c_rows, errors=errors)

    profile = _build_profile_from_meta(seller_meta or {}, errors, cfg)
    if errors:
        raise DatasetValidationError(errors[:15])
    return SellerContext(profile=profile, skus=skus, cancellations=cancels)


def parse_uploaded_json(
    raw_bytes: bytes,
    *,
    settings: Settings | None = None,
) -> SellerContext:
    cfg = settings or get_settings()
    if len(raw_bytes) == 0:
        raise DatasetValidationError(["JSON payload is empty."])
    if len(raw_bytes) > cfg.max_upload_bytes:
        raise DatasetValidationError(
            [f"JSON payload exceeds the {cfg.max_upload_bytes // 1024} KB ({cfg.max_upload_bytes:,} bytes) size limit."]
        )
    try:
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetValidationError([f"Invalid JSON syntax: {exc}"]) from exc

    errors: list[str] = []
    if isinstance(payload, list):
        sku_rows = payload
        cancel_rows: list[dict[str, Any]] = []
        seller_meta: dict[str, Any] = {}
    elif isinstance(payload, dict):
        if "skus" not in payload or not isinstance(payload["skus"], list):
            raise DatasetValidationError(["JSON object must contain a 'skus' array."])
        sku_rows = payload["skus"]
        cancel_rows = payload.get("cancellations") or []
        if not isinstance(cancel_rows, list):
            errors.append("'cancellations' must be an array when provided.")
            cancel_rows = []
        seller_meta = payload.get("seller") or {}
        if not isinstance(seller_meta, dict):
            errors.append("'seller' must be an object when provided.")
            seller_meta = {}
    else:
        raise DatasetValidationError(["Uploaded JSON must be an object with 'skus' or a list of SKU rows."])

    skus = _validate_sku_rows(sku_rows, max_skus=cfg.max_upload_skus, errors=errors)
    cancels = _validate_cancellations_rows(cancel_rows, errors=errors) if cancel_rows else []
    profile = _build_profile_from_meta(seller_meta, errors, cfg)
    if errors:
        raise DatasetValidationError(errors[:15])
    return SellerContext(profile=profile, skus=skus, cancellations=cancels)


def build_template_payload(fmt: str = "csv") -> tuple[str, str, str]:
    """Return (content_text, media_type, filename) for template download."""
    sample_skus = [
        {
            "sku_id": "SAMPLE-001",
            "name": "Insulated Water Bottle 1L",
            "category": "Hydration",
            "price": 650,
            "competitor_lowest_price": 640,
            "orders_prev": 38,
            "orders_curr": 12,
            "days_out_of_stock_curr": 18,
            "stock_qty_curr": 0,
            "impressions_prev": 4200,
            "impressions_curr": 2800,
            "clicks_prev": 190,
            "clicks_curr": 115,
        },
        {
            "sku_id": "SAMPLE-002",
            "name": "Wireless Mouse Silent 2.4GHz",
            "category": "Peripherals",
            "price": 850,
            "competitor_lowest_price": 780,
            "orders_prev": 28,
            "orders_curr": 16,
            "days_out_of_stock_curr": 0,
            "stock_qty_curr": 45,
            "impressions_prev": 3600,
            "impressions_curr": 3400,
            "clicks_prev": 150,
            "clicks_curr": 120,
        },
        {
            "sku_id": "SAMPLE-003",
            "name": "Ceramic Desk Organizer",
            "category": "Home Office",
            "price": 450,
            "competitor_lowest_price": 445,
            "orders_prev": 24,
            "orders_curr": 23,
            "days_out_of_stock_curr": 0,
            "stock_qty_curr": 60,
            "impressions_prev": 2900,
            "impressions_curr": 2850,
            "clicks_prev": 116,
            "clicks_curr": 112,
        },
    ]
    sample_cancels = [
        {"reason": "Seller cancelled - stock unavailable", "count_prev": 3, "count_curr": 9, "is_seller_fault": True},
        {"reason": "Auto-cancelled - dispatch SLA delay", "count_prev": 2, "count_curr": 5, "is_seller_fault": True},
        {"reason": "Buyer initiated cancellation", "count_prev": 4, "count_curr": 3, "is_seller_fault": False},
    ]

    norm = fmt.strip().lower()
    if norm == "json":
        obj = {
            "seller": {
                "seller_name": "Sample Seller",
                "salutation": "Seller Partner",
                "shop_name": "Sample Direct Store",
                "city": "Bengaluru",
                "category": "Multi-Category",
                "period_prev_label": "Last Month",
                "period_curr_label": "This Month",
            },
            "skus": sample_skus,
            "cancellations": sample_cancels,
        }
        return json.dumps(obj, indent=2), "application/json", "voicekam_template.json"

    if norm in {"cancellations_csv", "cancels_csv"}:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["reason", "count_prev", "count_curr", "is_seller_fault"])
        writer.writeheader()
        writer.writerows(sample_cancels)
        return buf.getvalue(), "text/csv; charset=utf-8", "voicekam_cancellations_template.csv"

    buf = io.StringIO()
    cols = list(REQUIRED_SKU_COLUMNS) + list(OPTIONAL_SKU_COLUMNS)
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    writer.writerows(sample_skus)
    return buf.getvalue(), "text/csv; charset=utf-8", "voicekam_skus_template.csv"


@dataclass
class _StoredDataset:
    context: SellerContext
    created_at: float
    last_accessed: float


class UploadedDatasetStore:
    """In-memory bounded TTL cache for validated custom datasets."""

    def __init__(self, ttl_seconds: int = 7200, max_entries: int = 200) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_entries = max(10, int(max_entries))
        self._items: OrderedDict[str, _StoredDataset] = OrderedDict()

    def _prune(self, now: float | None = None) -> None:
        ts = time.monotonic() if now is None else now
        expired = [k for k, v in self._items.items() if (ts - v.last_accessed) > self.ttl_seconds]
        for k in expired:
            self._items.pop(k, None)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def put(self, context: SellerContext) -> str:
        now = time.monotonic()
        self._prune(now)
        dataset_id = "ds_" + uuid.uuid4().hex[:12]
        self._items[dataset_id] = _StoredDataset(context=context, created_at=now, last_accessed=now)
        self._prune(now)
        return dataset_id

    def get(self, dataset_id: str) -> SellerContext | None:
        now = time.monotonic()
        self._prune(now)
        item = self._items.get(dataset_id)
        if item is None:
            return None
        item.last_accessed = now
        self._items.move_to_end(dataset_id)
        return item.context

    def __len__(self) -> int:
        self._prune()
        return len(self._items)

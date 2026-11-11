# VoiceKAM Custom Dataset Schema

Upload your own synthetic or anonymized SKU performance dataset to run the VoiceKAM diagnostic engine and voice walkthrough without modifying code.

> **Privacy Notice:** Do not upload real or confidential seller data; free LLM tiers may use prompts to improve their products.

## Endpoints

- **Download Templates:**
  - `GET /api/data/template?format=csv` — SKU performance CSV with all supported headers
  - `GET /api/data/template?format=cancellations_csv` — Optional order cancellations table CSV
  - `GET /api/data/template?format=json` — Combined JSON document (`seller`, `skus`, `cancellations`)
- **Validate & Preview:** `POST /api/data/validate` (accepts `multipart/form-data` or `application/json`; returns `dataset_id`, `summary`, `completeness`, and any validation errors)
- **Start Session with Upload:** `POST /api/session/start` (accepts either JSON `{mode, language, dataset_id}` or direct `multipart/form-data` with `file` and optional `cancellations_file`)

---

## SKU Table Columns (CSV or `skus[]` in JSON)

### Required Columns

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `sku_id` | string | Non-empty, unique, max 64 chars | Unique product identifier |
| `name` | string | Non-empty, max 160 chars | Product title |
| `price` | number | Strictly > 0 | Selling price per unit (INR) |
| `orders_prev` | integer | >= 0 | Orders placed in the baseline period |
| `orders_curr` | integer | >= 0 | Orders placed in the current period |

### Optional Columns & Graceful Degradation

| Column | Type | Constraints | Enables When Provided |
| :--- | :--- | :--- | :--- |
| `category` | string | Max 80 chars | Category breakdown (`"General"` default) |
| `competitor_lowest_price` | number | > 0 | Competitor price-gap driver & control-group comparison |
| `days_out_of_stock_curr` | integer | 0 to 31 | Exact stock-out daily velocity attribution |
| `days_out_of_stock_prev` | integer | 0 to 31 | Separates newly OOS from chronic OOS items |
| `stock_qty_curr` | integer | >= 0 | Binary fallback OOS flag (`0` = out of stock) when days unavailable |
| `impressions_prev` | integer | >= 0 | Search visibility funnel baseline |
| `impressions_curr` | integer | >= 0 | Search visibility funnel current |
| `clicks_prev` | integer | 0 to `impressions_prev` | Baseline CTR & CVR computation |
| `clicks_curr` | integer | 0 to `impressions_curr` | Current CTR & CVR computation |

When any optional group is omitted:
- The engine marks that driver `"not assessable with the data provided"` (`assessable: false`, `confidence: "unavailable"`).
- Any unattributed order decline remains cleanly accounted for under `unexplained_residual` so totals always reconcile without fabricated estimates.
- The LLM system prompt instructs the voice agent to state honestly that missing metrics are `"not assessable with the data provided"`.

---

## Optional Cancellations Table (`cancellations_file` CSV or `cancellations[]` JSON)

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `reason` | string | Non-empty, max 120 chars | Cancellation reason label |
| `count_prev` | integer | >= 0 | Cancelled orders in baseline period |
| `count_curr` | integer | >= 0 | Cancelled orders in current period |
| `is_seller_fault` | boolean (optional) | `true`/`false`/`1`/`0`/`yes`/`no` | Overrides keyword inference (`stock`, `dispatch`, `sla`, `seller`, `fulfill`, `warehouse`) |

---

## JSON Upload Structure

```json
{
  "seller": {
    "seller_name": "Sample Seller",
    "salutation": "Seller Partner",
    "shop_name": "Sample Direct Store",
    "city": "Bengaluru",
    "category": "Multi-Category",
    "period_prev_label": "Last Month",
    "period_curr_label": "This Month"
  },
  "skus": [
    {
      "sku_id": "SAMPLE-001",
      "name": "Insulated Water Bottle 1L",
      "price": 650,
      "orders_prev": 38,
      "orders_curr": 12
    }
  ],
  "cancellations": [
    {
      "reason": "Seller cancelled - stock unavailable",
      "count_prev": 3,
      "count_curr": 9,
      "is_seller_fault": true
    }
  ]
}
```

---

## Limits, Safety & Data Handling

- **Row cap:** Maximum **2,000 SKUs** per upload (`MAX_UPLOAD_SKUS`), and **100 rows** for the cancellations table.
- **File size cap:** Maximum **1 MB (1,048,576 bytes)** per uploaded payload (`MAX_UPLOAD_BYTES`).
- **Formula-injection defense:** Any cell value starting with `=`, `+`, `-`, `@`, tab, or carriage return is rejected with a clear validation error.
- **HTML sanitization:** `<script>`, `<style>`, and HTML tags are stripped from text fields on ingest.
- **RAM-only retention:** Validated datasets live strictly inside the session's in-memory `SellerContext` and LRU cache, expire with `SESSION_TTL_SECONDS` (default 2 hours), and are never written to disk or server logs.

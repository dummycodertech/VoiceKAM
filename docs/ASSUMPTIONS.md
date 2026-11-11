# Data, Business Logic & Technical Assumptions (Prototype — Synthetic Data Only)

> All figures, store records, SKUs, and conversation scenarios in this project are **100% synthetic data** created solely for demonstrating the **VoiceKAM ("metis")** architecture. No real marketplace seller data, internal APIs, pricing feeds, or trademark assets are used.

---

## 1. Metric Definitions & Attribution Scope

- **Orders:** Placed marketplace orders in the comparison periods (`orders_prev` vs `orders_curr`).
- **Sales (Placed GMV):** $\sum (\text{Orders} \times \text{SKU Price})$.
- **Average Selling Price (ASP):** `Sales / Placed Orders`. When `abs(asp_delta_pct) <= 3%`, the decline is flagged as volume-driven rather than ticket-size compression.
- **Cancellation Rate:** `sum(Cancelled Orders) / Placed Orders`. Reasons are separated into seller-controllable causes (`seller_stock_unavailable`, `dispatch_sla_breach`) and external/buyer causes (`buyer_initiated`, `courier_address_issue`).
- **Out-of-Stock (OOS) Products:** Derived dynamically from `days_out_of_stock_curr > 0` or binary `stock_qty_curr <= 0` fallback when duration days are omitted.
- See [ATTRIBUTION.md](ATTRIBUTION.md) for the full disjoint attribution formulas (daily-velocity stock-out estimation, control-group median pricing counterfactual, net-of-OOS visibility conversion, seller-fault cancellations, and exact `unexplained_residual` reconciliation).

---

## 2. Built-in Seeded Demo Scenarios (A–D)

| Scenario | Fictional Seller & Category | SKUs | Dominant Operational Driver |
| :---: | :--- | :---: | :--- |
| **A (Default)** | **UrbanStride Footwear & Apparel** (Bengaluru) — Footwear & Activewear | 48 | **Stock-outs (~57%)** on fast-moving SKUs + secondary pricing, visibility & cancellations |
| **B** | **TechNest Accessories** (Mumbai) — Electronics Accessories | 42 | **Competitor Price Gaps (~42%) & Search Visibility (~35%)** |
| **C** | **FreshBrew Pantry** (Pune) — Beverages & Pantry | 36 | **Seller-Caused Cancellations & Dispatch SLA (~61%)** |
| **D** | **ArtisanCraft Decor** (Jaipur) — Handcrafted Home Decor | 40 | **Mixed causes with ~29% Unexplained Residual** (uncaptured demand noise) |

---

## 3. Graceful Degradation & Missing Optional Columns

Custom uploads only require `sku_id, name, price, orders_prev, orders_curr` ([DATA_SCHEMA.md](DATA_SCHEMA.md)). Whenever optional signals (`days_out_of_stock_curr`, `stock_qty_curr`, `competitor_lowest_price`, `impressions_prev/curr`, or `cancellations`) are absent:
- The missing driver reports `"not assessable with the data provided"` (`assessable: false`, `confidence: "unavailable"`).
- Any unattributed drop stays inside `unexplained_residual` so totals still reconcile to the exact order and rupee.
- The agent explicitly states what cannot be assessed instead of guessing.

---

## 4. Follow-Up Review Mode

In `followup` mode, `SellerContext.resolve_skus("followup")`, `resolve_cancellations("followup")`, and `resolve_funnel("followup")` clone and update baseline records with partial restocking, repricing, and cancellation reductions. All follow-up metrics, OOS counts (`total == newly + chronic`), remaining pricing gaps, and action-plan targets derive strictly from those cloned records without hand-patched constants.

---

## 5. Free-Tier Architecture Constraints

- **RAM-only State (`--workers 1`):** `SessionStore` and `UploadedDatasetStore` live strictly in process memory with TTL expiry and LRU eviction; zero uploaded seller records touch disk or logs.
- **Voice Execution:** Primary live STT uses Chrome's Web Speech API; non-Chrome browsers fall back to Whisper STT (`/api/stt` when Groq key set) or the debug text bar. TTS uses Edge TTS -> Gemini TTS -> browser `speechSynthesis`.

# VoiceKAM Diagnosis & Attribution Methodology

Every number spoken by the VoiceKAM agent or rendered in the KAM Brain panel is computed deterministically in `app/engine/diagnosis.py` directly from SKU-level, visibility-funnel, and cancellation records inside the session's `SellerContext`. The diagnosis engine **never reads** ground-truth labels (`driver_group` is strictly restricted to test generation).

---

## 1. Headline Totals

Given SKU records $i \in \{1, \dots, N\}$ for two comparison windows (`prev` = baseline, `curr` = current):

- **Baseline Orders:** $O_0 = \sum_i O_{i,0}$
- **Current Orders:** $O_1 = \sum_i O_{i,1}$
- **Net Order Decline to Explain:** $\Delta O = \max(0, O_0 - O_1)$
- **Baseline Sales (GMV):** $R_0 = \text{round}(\sum_i O_{i,0} \cdot P_i)$
- **Current Sales (GMV):** $R_1 = \text{round}(\sum_i O_{i,1} \cdot P_i)$
- **Net Revenue Decline to Explain:** $\Delta R = \max(0, R_0 - R_1)$
- **Average Selling Price (ASP):** $\text{ASP}_t = R_t / O_t$

---

## 2. Sequential Disjoint Driver Attribution (No Double Counting)

To ensure drivers never double-count overlapping phenomena on the same SKUs and always reconcile to exact totals ($\sum_k \hat{O}_k + \hat{O}_{\text{residual}} = \Delta O$ and $\sum_k \hat{R}_k + \hat{R}_{\text{residual}} = \Delta R$), the engine attributes order and revenue declines along mutually disjoint capacity budgets:

Let $D_i = \max(0, O_{i,0} - O_{i,1})$ be SKU $i$'s observed decline, and maintain remaining global budgets:
$$B_O = \Delta O, \qquad B_R = \Delta R$$
and remaining SKU decline budget $d_i = D_i$.

### (a) Stock-Outs (`stock_outs`)
- **Required columns:** `days_out_of_stock_curr` (high confidence) or binary `stock_qty_curr == 0` fallback (medium confidence).
- **Condition:** SKUs that were available in the baseline period (`not is_oos_prev`, `orders_prev > 0`) and experienced out-of-stock duration in the current period (`is_oos_curr`).
- **Formula:**
  - When `days_out_of_stock_curr` is available:
    $$\text{active\_days}_0 = \max\big(1, 30 - \text{days\_oos\_prev}_i\big), \quad v_i = \frac{O_{i,0}}{\text{active\_days}_0}$$
    $$\hat{o}^{\text{oos}}_i = \min\big(d_i,\; \text{round}(v_i \cdot \text{days\_oos\_curr}_i)\big)$$
  - When only `stock_qty_curr == 0` is available:
    $$\hat{o}^{\text{oos}}_i = d_i$$
- Deduct $\hat{o}^{\text{oos}}_i$ from $d_i$; clamp total $(\hat{O}_{\text{oos}}, \hat{R}_{\text{oos}})$ against $(B_O, B_R)$ and update budgets.

### (b) Competitor Price Gaps (`pricing_gaps`)
- **Required column:** `competitor_lowest_price`.
- **Control Group:** Active in-stock SKUs (`not is_oos_curr`, `orders_prev > 0`) priced within $\le 3\%$ of `competitor_lowest_price` (excluding extreme visibility outliers where impressions fell $>25\%$ if impressions exist, or taking the full fairly-priced in-stock cohort). Compute median relative order change ratio:
  $$m_{\text{ctrl}} = \text{median}_{c \in \text{Control}}\left(\frac{O_{c,1} - O_{c,0}}{O_{c,0}}\right)$$
  Let expected ambient factor be $f_{\text{ctrl}} = \min(0, m_{\text{ctrl}})$ so ambient store-wide shifts are separated from pricing elasticity.
- **Treated Group:** Active in-stock SKUs (`not is_oos_curr`) priced $> 3\%$ above `competitor_lowest_price` whose decline exceeds the control expectation:
  $$\text{expected\_orders}_j = \text{round}\big(O_{j,0} \cdot (1 + f_{\text{ctrl}})\big)$$
  $$\hat{o}^{\text{price}}_j = \min\big(d_j,\; \max(0, \text{expected\_orders}_j - O_{j,1})\big)$$
- Because $j$ is strictly in-stock (`not is_oos_curr`), stock-out and price-gap sets are **strictly disjoint**.
- Suggested reprice target uses one shared helper across `get_pricing_gaps` and `build_action_plan`: $\min(1.015 \cdot C_j, C_j + 4)$ for $C_j \ge 100$.
- **Confidence:** `high` ($\ge 4$ control SKUs), `medium` ($2\text{--}3$ control SKUs), `low` ($< 2$ control SKUs).

### (c) Search Visibility (`visibility_drop`)
- **Required columns:** `impressions_prev` and `impressions_curr` (plus `clicks_prev` for full CTR/CVR split).
- **Formula (Per-SKU impressions available):**
  For each SKU with impression drop $\Delta I_i = \max(0, I_{i,0} - I_{i,1})$ and remaining unattributed drop $d_i > 0$:
  $$\text{base\_order\_per\_imp}_i = \text{CTR}_{i,0} \cdot \text{CVR}_{i,0} = \frac{O_{i,0}}{I_{i,0}}$$
  $$\hat{o}^{\text{vis}}_i = \min\Big(d_i,\; \text{round}\big(\Delta I_i \cdot \text{base\_order\_per\_imp}_i\big)\Big)$$
  Notice that taking $\min(d_i, \dots)$ automatically removes impressions already accounted for by stock-outs or pricing on that SKU!
- **Formula (Store-level funnel only):**
  Pro-rate baseline order-per-impression across overall $\Delta I = \max(0, I_0 - I_1)$, scaled by non-OOS order share, and clamped by remaining $(B_O, B_R)$.
- **Confidence:** `high` if both impressions and clicks are present, `medium` if only impressions are present.

### (d) Seller-Caused Cancellations (`cancellations`)
- **Required table:** `cancellations` (`reason, count_prev, count_curr, [is_seller_fault]`).
- **Formula:** Sum seller-fault rows (`stock`, `dispatch`, `sla`, `seller`, `fulfill`, `warehouse`, or explicit `is_seller_fault=true`):
  $$\Delta C_{\text{seller}} = \max\Big(0,\; \sum_{r \in \text{seller}} (C_{r,1} - C_{r,0})\Big)$$
  $$\hat{O}_{\text{cancel}} = \min(B_O,\; \Delta C_{\text{seller}}), \qquad \hat{R}_{\text{cancel}} = \min\big(B_R,\; \text{round}(\hat{O}_{\text{cancel}} \cdot \text{ASP}_0)\big)$$

### (e) Unexplained Residual (`unexplained_residual`)
$$\hat{O}_{\text{residual}} = \Delta O - (\hat{O}_{\text{oos}} + \hat{O}_{\text{price}} + \hat{O}_{\text{vis}} + \hat{O}_{\text{cancel}}) \ge 0$$
$$\hat{R}_{\text{residual}} = \Delta R - (\hat{R}_{\text{oos}} + \hat{R}_{\text{price}} + \hat{R}_{\text{vis}} + \hat{R}_{\text{cancel}}) \ge 0$$

Every missing optional signal reports `"not assessable with the data provided"` (`assessable: false`, `confidence: "unavailable"`) and leaves any unattributed loss inside `unexplained_residual`.

---

## 3. Exact Reconciliation Guarantee

Across all snapshots (`current`, `followup`, custom CSV/JSON, and random datasets):
$$\sum_{d \in \text{drivers}} d.\text{lost\_orders} = \text{total\_lost\_orders}, \qquad \sum_{d \in \text{drivers}} d.\text{lost\_revenue} = \text{total\_lost\_revenue}$$
and in `followup` mode all OOS counts, pricing gaps, cancellation rates, and action-plan recommendations derive strictly from cloned `resolve_skus("followup")` and `resolve_cancellations("followup")` records.

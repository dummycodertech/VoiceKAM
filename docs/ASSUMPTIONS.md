# Data, Business Logic & Technical Assumptions (Prototype — Mock Data Only)

> All figures, store records, SKUs, and conversation scenarios in this project are **100% synthetic mock data** created solely for demonstrating the **Snapdeal Virtual KAM ("Riya")** architecture. No real Snapdeal seller data, internal APIs, pricing feeds, or trademark assets are used.

---

## 1. Metric Definitions & Reconciliation

- **Store Persona:** Rajesh Kumar — *Kumar Home & Kitchen* (Delhi), 60 active catalog SKUs (`HK-001` through `HK-060`).
- **Orders:** Placed marketplace orders in the billing period (`400` last month vs `270` this month, `-130` orders / `-32.5%`).
- **Sales (Placed GMV):** $\sum (\text{Orders} \times \text{SKU Price})$.
  - Last month: `₹2,00,000` (`400 × ₹500` blended ASP).
  - This month: `₹1,35,000` (`270 × ₹500` blended ASP, `-₹65,000` / `-32.5%`).
- **Average Selling Price (ASP):** `Sales / Placed Orders = ₹500` (last month) -> `₹500` (this month). Because ASP stayed flat (`0.0%` delta), the entire `32.5%` revenue contraction is proven to be a **volume problem**, not a ticket-size drop.
- **Cancellation Rate:** `Cancelled Orders / Placed Orders`.
  - Last month: `32 / 400 = 8.0%`.
  - This month: `38 / 270 = 14.07%` (reported rounded at `14.0%`, `+6.0 pp`).
- **Out-of-Stock (OOS) Products:** Total catalog SKUs currently unavailable for purchase jumped from `5` chronic OOS last month to `18` this month (`+13` newly OOS SKUs).

---

## 2. Deterministic Causal Attribution Model

Every figure spoken by the agent or shown in the KAM Brain panel is computed in plain Python inside `app/engine/diagnosis.py`:

| Driver | Share | Lost Orders | Lost Sales | Evidence in Mock Catalog |
| :--- | ---: | ---: | ---: | :--- |
| **1. Stock-outs on Top Sellers** | **60.0%** | **78** | **₹39,000** | 13 newly OOS SKUs (`HK-006`..`HK-018`); top 5 (`Pressure Cooker 5L`, `Granite Tawa`, `Casserole Set`, `Storage Jars`, `Tadka Pan`) lost 68 orders alone. |
| **2. Competitor Price Gaps** | **20.0%** | **26** | **₹13,000** | 3 active high-velocity SKUs (`HK-019` Electric Kettle `+6.9%`, `HK-020` Dish Rack `+7.5%`, `HK-021` Spatula Set `+8.3%`) priced above lowest competing marketplace offer. |
| **3. Cancellations & Visibility Drag** | **20.0%** | **26** | **₹13,000** | Seller stock-unavailable cancellations jumped `6 -> 14`, dispatch-SLA auto-cancellations rose `5 -> 9` (while buyer-initiated rate stayed ~4%), and search impressions dropped `14.0%` (`80,000 -> 68,800`) as OOS items lost ranking. |
| **Total Reconciled Drop** | **100.0%** | **130** | **₹65,000** | Exact reconciliation across all 60 catalog SKUs verified by automated unit tests (`tests/test_reconciliation.py`). |

---

## 3. Action Plan & Recovery Estimate Assumptions

- **Estimated Combined Recovery (`₹47,000 - ₹63,000/month`):** Indicative model range, **not a guarantee**, assuming:
  1. Top 5 newly OOS SKUs restock within 48 hours and regain search visibility within 3–5 days.
  2. Twice-daily inventory sync and clearing ready-to-ship manifests before the 2 PM dispatch SLA cut cancellation rate below `10%`.
  3. Selective repricing within ~2% of lowest competitor offer (or an optional temporary promotion / ads boost, **explicitly stated as subject to marketplace eligibility**).

---

## 4. Three-Day Follow-Up Simulation Snapshot

To demonstrate post-call accountability ("taking ownership"), the simulated **3-days-later follow-up call** transitions to a verified `followup` snapshot (`app/data/seed_data.py`):
- **OOS Products:** `18 -> 9` (`4 of top 5` restocked; `HK-009 Airtight Storage Jars` still pending -> target `<= 8`).
- **Cancellation Rate:** `14.0% -> 10.5%` (stock-unavailable dropped sharply; 2 PM dispatch SLA remains slightly above the `<= 10.0%` target).
- **Pricing Gaps:** `HK-019` and `HK-020` repriced; `HK-021 Silicone Spatula Set` (`₹300 vs ₹277`) remains.
- **Run-Rate Pace:** Projected monthly sales recovered `+30.0%` from `₹1,35,000` crisis floor toward `₹1,75,500/month`.

---

## 5. Architecture & Free-Tier Operational Assumptions

- **Single-Process In-Memory State:** `SessionStore` holds active session state in RAM (`--workers 1`) to avoid requiring external Redis/DB paid services on the free tier. Session history resets on container restart.
- **Voice-First Client Execution:** Primary live STT uses Chrome's Web Speech API (`SpeechRecognition`, `en-IN` / `hi-IN`); secondary mic fallback accepts recorded audio via `POST /api/stt` (Groq Whisper). Desktop non-Chrome browsers without `SpeechRecognition` automatically surface the Chrome banner and allow full testing via the collapsible Debug Text Box.
- **TTS Hierarchy & Fallbacks:** Primary neural voice uses Microsoft `edge-tts` (`en-IN-NeerjaNeural` / `hi-IN-SwaraNeural`), failing over automatically to configurable Gemini Free TTS (if smoke-tested healthy at startup) and then zero-dependency browser `speechSynthesis`.


# VoiceKAM (`Metis`) — 90-Second Live Walkthrough Script

> **Setup before recording / presenting:** Open Google Chrome at your VoiceKAM URL (`https://<tunnel>.trycloudflare.com` via `.\run.ps1`, local `http://127.0.0.1:7860`, or Render service). Ensure **Demo Scenario** is set to **Scenario A: UrbanStride Footwear (Stock-out Dominant)** and language is **English (`en-IN`)**.

---

### Beat 1 (`0:00 – 0:15`) — Framing & Custom/Scenario Selector
- **Point out the UI:** *"This is VoiceKAM, a brand-neutral open-source prototype voice Key Account Manager (`Metis`) running on synthetic store data — zero real merchant or marketplace systems."*
- **Briefly show Store Dataset card:** Highlight Scenarios A–D (`UrbanStride`, `TechNest`, `FreshBrew`, `ArtisanCraft`) and the **"Use my own data"** option with downloadable CSV/JSON templates. Keep Scenario **A** selected (`UrbanStride Footwear & Apparel`, `Vikram ji`, Bengaluru, 48 SKUs).

### Beat 2 (`0:15 – 0:35`) — Inbound Voice Call & Phase Transition
- **Action:** Click **Start Call** (Mode: `Inbound Call`). Wait for the two-ring tone and `Metis`'s opening greeting (`"Namaste Vikram ji, I am Metis..."`).
- **Say / Send:** *"Sales are really slow this month and orders dropped sharply. Please check my store data and tell me why."*
- **Highlight:** Point to the **KAM Brain** panel (`Clarifying` -> `Diagnosis`), showing how pre-executed Python tools (`run_diagnosis`, `get_out_of_stock_skus`, `get_pricing_gaps`, `get_visibility_funnel`, `get_cancellation_breakdown`) fire concurrently in milliseconds before `Metis` speaks.

### Beat 3 (`0:35 – 0:55`) — Counterfactual Root-Cause Reveal & Numeric Grounding
- **Highlight Diagnosis Card:**
  - Orders fell **`476 -> 354` (`-122` orders)**; GMV dropped **`₹4,75,190 -> ₹3,43,370` (`-27.7%`)** while ASP stayed flat (`-2.8%`).
  - Counterfactual attribution isolates **Out-of-Stock Products at `57.4%`** (`70` lost orders / `₹73,290` — led by **`US-001` Trail Runner Shoes** and **`US-004` Lightweight Mesh Trainers**) ahead of **`US-010` Track Jogger Pants** pricing (`16.4%`), search visibility (`11.5%`), cancellations (`9.8%`), and a transparent `4.9%` `unexplained_residual`.
  - Optional: Press **Interrupt (Space)** mid-playback or mention the **Headphones: speak to interrupt** toggle and language switcher (**English / Hinglish / हिंदी**).

### Beat 4 (`0:55 – 1:15`) — Remediation Plan, Commitment Capture & Call Summary
- **Say / Send:** *"Yes, those numbers make sense. Let's fix the top stock-outs and pricing — what should I prioritize first?"*
- **Wait for Recommend response**, then **Say / Send:** *"I commit to restocking US-001 and US-004 by Friday and matching pricing on US-010 tomorrow. Check back with me in 3 days."*
- **Highlight Summary Card:** Point to locked commitments, **groundedness indicator (`0` ungrounded numbers)**, and the formatted **Mock WhatsApp Recap** ready via **Copy message** or **Download .txt**.

### Beat 5 (`1:15 – 1:30`) — Custom CSV Honesty & 3-Day Follow-Up Mode
- **Switch toggle / mention Custom CSV:** Briefly click **Use my own data** to show the privacy notice (`Do not upload real or confidential seller data...`) and note how optional columns cleanly degrade to **`"Not assessable with the data provided"`** without crashing or fabricating numbers.
- **Click `Simulate follow-up in 3 days`:** Switch back to Demo Scenario A and click **Start Call** — `Metis` reviews recovery directly from cloned follow-up records (`₹3,94,336` projected pace, **`+14.8%`** vs crisis) and calls out remaining open blockers.

# 90–120 Second Live Demo Script — Snapdeal Virtual KAM ("Riya")

> **Recommended Live Demo Path (Zero Cold Start, Natural Neural Voice):**  
> Run locally on your laptop with a free Cloudflare Quick Tunnel (`.\run.ps1` on Windows or `./run.sh` on macOS/Linux) and open the printed `https://*.trycloudflare.com` URL in **Google Chrome**.  
> **Alternative Public URL:** Deploy free on Render (`render.yaml`, zero credit card). Note: Render free instances sleep after 15 minutes of inactivity (~45–60s cold start), so open the Render `/api/health` URL 90 seconds before your live call.

---

## Pre-Flight Checklist (30 Seconds Before Demo)

1. Run `.\run.ps1` (Windows) or `./run.sh` (macOS/Linux) and open the `https://*.trycloudflare.com` URL (or `http://127.0.0.1:7860` locally) in **Google Chrome**.
2. Verify header badges:
   - **`PROTOTYPE · MOCK DATA ONLY`** is clearly visible.
   - `TTS: Auto` (uses local `edge-tts` neural voice; use `/api/tts-probe` if testing from a cloud host).
3. Settings setup for crisp demo:
   - **Call Mode:** `Inbound (Seller calls)` (or toggle `Proactive (Riya calls)` to show outbound ownership).
   - **Language:** `English (India)` (switch to `Hinglish` mid-demo if desired).
   - **Headphones:** Check **Headphones Mode** ONLY if wearing earphones/headset; keep unchecked on laptop speakers for clean half-duplex echo prevention.

---

## Live Choreography (90–120 Seconds)

| Time | Who | Action / Spoken Line | What Reviewers Notice on Screen |
| :--- | :--- | :--- | :--- |
| **0:00 – 0:15** | **You (Seller)** | Click **Start Call**. Riya greets immediately: *"Namaste Rajesh ji, I am Riya, your Snapdeal account manager..."* Speak: **"Hi Riya, my sales have dropped significantly this month. I don't understand why. Can you help me?"** | Instant ringtone -> zero-latency neural greeting -> Mic Orb pulses emerald (`Listening`) -> 800ms pause sends cleanly. |
| **0:15 – 0:30** | **Riya -> You** | Riya empathizes and asks 1 focused clarifying question. Reply naturally: **"Yes, Pressure Cooker 5L and Granite Tawa ran out of stock last week, and stock sync happens manually next morning."** *(Optional interrupt demo: click **Tap to Interrupt (Space)** mid-sentence while Riya responds, say **"Also check my prices compared to competitors."**)* | Live phase stepper moves `Greeting -> Understand -> Clarify`; smooth interruption cuts off audio queue cleanly. |
| **0:30 – 0:55** | **Riya (Diagnose)** | Riya triggers deterministic diagnosis: points out ASP stayed flat at **₹500 -> ₹500** (`-32.5%` is pure **volume**, 130 fewer orders), **60%** from **18 OOS SKUs**, **20%** from **3 overpriced SKUs** (`6.9%–8.3%`), **20%** from **14% cancellations** & impression drop. | Highlight **KAM Brain** on the right: all 5 deterministic Python tools executed concurrently in ~1–3 ms (`0 Ungrounded Numbers`), ranked driver progress bars, and exact SKU table. |
| **0:55 – 1:15** | **You + Riya (Recommend & Commit)** | Say: **"Got it, what should I fix first?"** Riya delivers prioritised top-3 actions (`ACT-1`, `ACT-2`, `ACT-3`) with indicative recovery ranges. Commit loudly: **"Okay, I'll restock the top 5 products within 48 hours and start daily stock sync today."** | Notice how Riya **drives ownership**: `commit_action` & `schedule_followup` fire live, turning `P1 · ACT-1` emerald **`Committed · Due: 48 hours`** and locking the 3-day check-in targets (`OOS <= 8`, `Cancels <= 10%`). |
| **1:15 – 1:30** | **Riya (Summary)** | Say: **"Please send me the summary."** Riya speaks a concise 30-second recap of root causes, dates, and follow-up. | **Call Summary & Mock WhatsApp Recap** appears automatically with **Copy WhatsApp Text** and **Download .txt**. |
| **1:30 – 1:55** | **Closed-Loop Follow-Up** | Click **`Simulate follow-up call (3 days later)`** and say: **"Hi Riya, I restocked most items—how do things look now?"** | **3-Day Accountability Mode** activates: `Store Performance Comparison` shows **+30% sales pace (`₹1,35,000 -> ₹1,75,500`)**, `OOS 18 -> 9`, `Cancels 14% -> 10.5%`, and Riya immediately focuses on closing the last 2 blockers (`HK-009`, `HK-021`). |

---

## Deployment Limits to Mention Honestly if Asked

1. **Render Free Web Service:** Sleeps after 15 minutes of inactivity (~45–60s cold start), 512 MB RAM (`--workers 1`, in-memory session storage).
2. **Free-Tier LLM Quotas:** Groq / Gemini free tiers enforce RPM/TPM caps; our `LLMRouter` mitigates this with compact prompts, zero-tool `DIAGNOSE` pre-execution (1 request/turn), and automatic 429 cooldown failover (`openai/gpt-oss-20b` -> `openai/gpt-oss-120b` -> `gemini-3.5-flash-lite` -> `gemini-3.6-flash`).
3. **Unofficial `edge-tts`:** Microsoft Edge's TTS endpoint occasionally throttles shared datacenter IPs; `GET /api/tts-probe` tests this live, and the frontend seamlessly falls back to browser `speechSynthesis` (`Local + Cloudflare Tunnel` uses your residential IP and virtually guarantees neural voice).


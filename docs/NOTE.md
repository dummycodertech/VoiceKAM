# Snapdeal Virtual KAM ("Riya") — Engineering & Product Note (1-Page Overview)

> **Prototype — Mock Data Only.** All seller profiles, 60-SKU catalog numbers, and marketplace workflows are synthetic fixtures built for the Snapdeal **AI First Intern** assignment. Zero paid APIs or credit cards are required.

---

## 1. Problem & Ownership Philosophy
Most marketplace support bots sit passively waiting for FAQs. For a small seller experiencing a sudden **32.5% monthly sales decline**, what helps most is a **proactive Key Account Manager (KAM)** who separates symptom from cause, diagnoses root metrics accurately, drives concrete seller commitments with dates, and follows up on results. **Riya** implements that closed-loop KAM workflow over natural two-way voice in **English (India), Hinglish, or Hindi**.

---

## 2. Models & Zero-Cost Stack (Verified Nov 2026)

| Layer | Choice | Why & Failover |
| :--- | :--- | :--- |
| **LLM Primary** | **Groq Free Tier** (`openai/gpt-oss-20b` -> `openai/gpt-oss-120b`) | Sub-second TTFT streaming with native parallel tool calling and `reasoning_effort="low"` + streaming `<think>` filter. *(Note: `llama-4-scout` was shut down by Groq on 17 Jul 2026; startup queries `GET /models` to keep only live endpoints.)* |
| **LLM Fallback** | **Google Gemini Free Tier** (`gemini-3.5-flash-lite` -> `gemini-3.6-flash`) + optional **OpenRouter** (`openrouter/free`) | Preserves multi-turn `thought_signature` (`google-genai` SDK) and applies per-provider **429/403 cooldown** in `LLMRouter` so rate limits never break an active call. |
| **Voice I/O (STT/TTS)** | **Web Speech API** (`SpeechRecognition`, `en-IN`/`hi-IN`) + **`edge-tts`** (`en-IN-NeerjaNeural`, `hi-IN-SwaraNeural`) | Zero-cost neural Indian voices with LRU cache (`TTSService`), sentence-pipelined audio fetching (`audio.js`), emergency configurable Gemini TTS probe, and automatic browser `speechSynthesis` + optional Groq Whisper `/api/stt` fallback. |
| **Backend & UI** | **Python FastAPI + Vanilla ES Modules** | Single deployable process (`--workers 1`), Server-Sent Events (`/api/chat`), no frontend bundler overhead, 512 MB RAM friendly. |
| **Hosting** | **Local + Cloudflare Quick Tunnel** (`run.ps1` / `run.sh`) & **Render Free Web Service** (`render.yaml`) | Zero-card public HTTPS URLs required for browser microphone permissions. *(HF Docker Spaces were avoided as Docker/Gradio Spaces now require PRO.)* |

---

## 3. End-to-End Architecture & Hallucination Guardrails

1. **Deterministic Diagnosis Engine (`app/engine/diagnosis.py`):** Plain Python reconciles all **60 SKUs (`HK-001`..`HK-060`)**:
   - Proves ASP stayed flat (`₹500 -> ₹500`) -> pure **volume problem** (`400 -> 270` orders, `₹2,00,000 -> ₹1,35,000`).
   - Computes exact attribution: **60% Out-of-Stock** (13 newly OOS SKUs / 78 orders / `₹39,000`), **20% Price Gaps** (3 SKUs `+6.9%` to `+8.3%` above lowest offer / 26 orders / `₹13,000`), **20% Cancellations & Visibility Drag** (`8% -> 14%` cancels, `-14%` impressions / 26 orders / `₹13,000`).
2. **Server-Controlled State Machine (`app/agent/state_machine.py`):** The server—not the LLM—governs phase progression (`GREETING -> UNDERSTAND -> CLARIFY [<=3 Qs] -> DIAGNOSE -> RECOMMEND -> COMMIT -> SUMMARY -> CLOSE`), preventing redundant questioning.
3. **Zero-Tool Pre-Execution in `DIAGNOSE` & `RECOMMEND`:** Upon phase entry, the orchestrator executes all relevant deterministic tools concurrently via `asyncio.gather`, streams live `tool_call`/`tool_result` SSE events to the **KAM Brain** audit UI, and exposes **0 tools** to the LLM so analytical turns take **1 LLM call**.
4. **Speakable Normalization & Number Grounding (`app/agent/speakable.py`):** Converts `₹2,00,000` -> *"two lakh rupees"* (or *"rupaye"* in Hinglish/Hindi) and expands `SKU/OOS/SLA`, while `find_ungrounded_numbers()` flags any spoken number not traceable to tool outputs (`0 ungrounded numbers` audited).
5. **Ownership & Accountability Loop:** Captures explicit commitments (`commit_action`), measurable follow-up targets (`schedule_followup`: `OOS <= 8`, `Cancels <= 10%`), formatted mock WhatsApp summary (`send_call_summary`), **Proactive Outbound Mode**, and a **3-Day Follow-Up Simulation** showing `+30%` pace recovery (`₹1,75,500`) while chasing remaining blockers (`HK-009`, `HK-021`).

---

## 4. Known Limitations & Production Next Steps
- **Limitations:** Render Free sleeps after 15 min idle (~1 min cold start); free LLM tiers impose RPM/TPM limits (handled via compact prompts & auto-failover); `edge-tts` relies on an unofficial endpoint (`GET /api/tts-probe` verifies datacenter access live); headless Playwright cannot exercise a real physical acoustic microphone loop (`SpeechRecognition` stubbed in automated tests).
- **Production Next Steps:** Connect authenticated Snapdeal Seller Analytics & OMS webhooks behind feature flags, move `SessionStore` to Redis for multi-worker scaling, integrate paid Telephony/WebRTC (LiveKit/Daily + official Azure/Google Neural TTS), and log anonymized KAM outcome cohorts (commitment completion vs 7-day GMV lift).


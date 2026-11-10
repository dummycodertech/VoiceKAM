# Interview Defense & Architecture Q&A Guide

Prepared so every technical decision in **Snapdeal Virtual KAM** can be explained concisely and defended under deep follow-up questioning.

---

### 1. Why separate a deterministic Python Diagnosis Engine from the LLM?
- **Problem with pure-LLM analytics:** LLMs hallucinate arithmetic (`2,00,000 -> 1,35,000` subtraction, cancellation rates, weighted attribution), drift across turns, and cannot guarantee exact SKU reconciliation.
- **Our design:** Python (`app/engine/diagnosis.py`) computes every single number from the 60-SKU dataset (`400 -> 270` orders, `60% / 20% / 20%` split, per-SKU ₹/day impact, indicative action plan) with 15 deterministic unit tests (`pytest`). The LLM strictly acts as the **empathetic voice conversationalist** phrasing pre-verified outputs.
- **Safety net:** `find_ungrounded_numbers()` (`app/agent/speakable.py`) scans every generated spoken turn against the set of all numbers returned by tool outputs in that session (`ungrounded_warnings`), surfacing any ungrounded metric live in the KAM Brain audit bar.

---

### 2. Why is the conversation state machine server-controlled (not LLM-routed)?
- **Failure mode of agentic open-ended loops:** In voice calls on free-tier LLMs, letting the model decide phase transitions ("am I done clarifying?", "should I call 5 diagnosis tools one by one?") causes repetitive questions, 4–6 sequential LLM round-trips (high latency + TPM exhaustion), or skipped commitments.
- **Our design (`app/agent/state_machine.py` + `app/agent/orchestrator.py`):**
  1. **Deterministic phase transitions** (`GREETING -> UNDERSTAND -> CLARIFY -> DIAGNOSE -> RECOMMEND -> COMMIT -> SUMMARY -> CLOSE`), enforcing a hard **max 3 clarify questions** and skipping irrelevant phases (e.g., jumping to `REVIEW_COMMITMENTS` in 3-day follow-up mode).
  2. **Pre-execution on `DIAGNOSE` / `REVIEW_COMMITMENTS` / `RECOMMEND`:** When the seller enters `DIAGNOSE`, the server executes all 5 diagnosis tools **in parallel** (`asyncio.gather`) before invoking the LLM, streams `tool_call` / `tool_result` SSE events immediately to KAM Brain, and exposes **0 tools** to the LLM during that turn.
  3. **Payoff:** `DIAGNOSE` and `RECOMMEND` complete in **1 LLM request**, cutting latency by ~60–75% and protecting free-tier RPM/TPM quotas.

---

### 3. How do you achieve sub-2-second perceived voice latency on free APIs?
1. **800ms EOU timer** after Web Speech `onresult` pauses so turns dispatch quickly without cutting off mid-clause.
2. **600ms latency filler:** If LLM+TTS hasn't started speaking by 600ms, browser plays a short context-aware acknowledgment (`"One moment Rajesh ji, checking your account data."`).
3. **Minimal/low reasoning overhead & `<think>` stripping:** Groq uses `reasoning_effort="low"` and streaming `ThinkTagFilter`; Gemini uses `ThinkingLevel.MINIMAL` + `HttpRetryOptions(attempts=1)` with generous `max_output_tokens` headroom, preventing verbose hidden CoT or SDK retry delays from truncating mid-number or hurting voice latency.
4. **Sentence-pipelined TTS:** Backend emits SSE `sentence` events as punctuation boundaries stream in; frontend (`frontend/audio.js`) starts fetching `/api/tts` for sentence `N+1` while sentence `N` is still playing, plus an in-memory LRU cache (`TTSService`) for common phrases/greetings.

---

### 4. What happens if Groq hits a 429 rate limit mid-call?
- `LLMRouter` (`app/llm/router.py`) maintains a clean chain verified at startup against Groq's live `/models` catalog (`openai/gpt-oss-20b -> openai/gpt-oss-120b` -> Gemini `gemini-3.5-flash-lite -> gemini-3.6-flash` -> optional `openrouter/free`). *(Side benefit caught in testing: `llama-4-scout` was deprecated July 2026 and filtered/replaced automatically via `/models`.)*
- On HTTP `429` or `403`, `LLMRouter` places the offending provider into **cooldown** (`Retry-After` seconds or 60s default, 3600s on project 403 block), skips it immediately on subsequent turns, converts `CanonicalMessage` history (preserving Gemini `thought_signature` and injecting user turn content for system-only proactive openings), emits a transparent `failover` SSE badge in KAM Brain, and completes the turn seamlessly without dropping the call.

---

### 5. Why half-duplex default + Headphones true barge-in + Tap-to-Interrupt?
- Browser `SpeechRecognition` lacks low-level hardware AEC control on Windows/laptop speakers. Keeping continuous mic open over laptop speakers causes Riya to transcribe her own voice and loop.
- Defaulting to **half-duplex** (mic pauses cleanly during `<audio>` playback) + **Tap to Interrupt / Spacebar** works reliably everywhere, while toggling **Headphones Mode** activates true voice barge-in (`onspeechstart` aborts audio queue & fetches).

---

### 6. Why Render Free + Local Cloudflare Quick Tunnel instead of Hugging Face Docker Spaces?
- Hugging Face Docker & Gradio Spaces now require a paid PRO plan; we pivoted strictly to stay **100% free with zero credit card**:
  - **Local + `cloudflared` quick tunnel (`run.ps1` / `run.sh`)**: Zero-cost public `https://*.trycloudflare.com` URL for mic permissions, local residential IP (virtually never blocked by `edge-tts`), zero cold start, best live-demo reliability.
  - **Render Free Web Service (`render.yaml`, Singapore)**: Always-available public HTTPS URL (`https://*.onrender.com`) with zero card at signup. Documented trade-offs: 15-minute idle sleep (~1 min cold start), 512MB RAM (`--workers 1`), and `/api/tts-probe` verifies live whether Render datacenter IPs allow `edge-tts` or auto-fallback to browser `speechSynthesis`.

---

### 7. How does this agent "TAKE OWNERSHIP" rather than just answer FAQs?
1. **Proactive Calling Mode:** Riya can initiate the call citing the exact 32.5% drop and 18 OOS SKUs before the seller asks.
2. **Guided Accountability (`commit_action`):** Doesn't stop at generic advice — asks Rajesh ji to commit to specific actions, owners, and due dates (e.g., `ACT-1` within 48 hours), updating the live KAM Brain tracker.
3. **Measurable Targets + Summary (`schedule_followup` + `send_call_summary`):** Books a 3-day check-in with concrete SLA/OOS numeric targets (`OOS <= 8`, `Cancellation rate <= 10%`) and sends a ready-to-paste WhatsApp action recap.
4. **3-Day Follow-Up Simulation Mode:** Demonstrates the closed loop — reviewing earlier commitments (`Done` vs `Partial`), celebrating the `+30%` pace recovery (`₹1,35,000 -> ₹1,75,500`), and driving remaining blockers (`HK-009`, `HK-021`) to closure.

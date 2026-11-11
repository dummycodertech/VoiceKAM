# VoiceKAM (`metis`) — Project & Portfolio Summary

> **Note:** All quantitative claims below come directly from verified offline evaluation runs and automated test suites (`docs/EVALUATION.md`, `tests/test_attribution_properties.py`). Live voice playback and hosted judge benchmarks remain explicitly marked with placeholders where not yet executed against live audio hardware or hosted LLMs.

---

## Executive Summary

**VoiceKAM** is a full-stack, brand-neutral open-source voice AI Key Account Manager (`metis`) engineered in Python (FastAPI + SSE) and vanilla Web Speech / Canvas JS. Designed around strict separation between causal numerical analytics and conversational LLM rendering, VoiceKAM diagnoses marketplace seller revenue drops across stock-outs, competitor pricing gaps, search funnel drops, and seller-fault cancellations, while conducting sub-50-word bilingual voice calls (`en-IN`, `hinglish`, `hi-IN`) with custom CSV/JSON dataset support and free-tier multi-provider failover.

---

## Verified Quantitative Highlights

- **Causal Attribution Recovery (`200/200` Trials):** Counterfactual disjoint attribution engine achieves **`100.0%` (`200/200`) top-driver recovery** (`0.02 pp` mean absolute error, `8.3 pp` worst-case share error across `200` randomized multi-driver stores and `100` missing-column fuzz tests in [`tests/test_attribution_properties.py`](../tests/test_attribution_properties.py)) with exact rupee/order reconciliation across every cohort.
- **Strict Grounding & Tool Accuracy (`24` Offline Eval Calls / `156` Turns):** Across all `6` scripted seller personas x `4` seeded scenarios using `MockLLMProvider` (`docs/EVALUATION.md`):
  - **`100.0%` (`156/156` turns, `348/348` number tokens)** grounded directly in server tool outputs (`0` hallucinated numbers).
  - **`100.0%` (`192/192`)** expected diagnostic/summary tools executed and **`100.0%` (`24/24`)** multi-turn call completion to commitment & summary card.
  - **`23.5` avg / `34` max words per turn** enforced for natural voice pacing.
- **Automated Regression Coverage:** **`53` passing offline tests** (`-k "not live_llm"`) spanning API `v1` SSE contracts, causal reconciliation properties, word-boundary intent/negation state machine, CSV/JSON upload security (formula-injection & HTML sanitization), IP rate limits, `DAILY_TURN_CAP`, HTTP `413` guards, de-branding assertions, and hermetic Playwright UI tests at desktop (`1280px`) and mobile (`390px`) viewports.
- **Measured Live Metrics (Pending Hosted Run):**
  - Live free-tier LLM p50 / p95 first-token latency: `<live LLM latency ms pending hosted run>` (local offline mock measured `1.0 ms` / `2.0 ms`).
  - Live voice-to-voice first-audio latency (`p50` / `p95`): `<first_audio_ms not measured in headless CI>`.
  - Live LLM-as-judge rubric score (`1–5`): `<judge score pending live run via make eval-live>`.

---

## Resume / Portfolio Bullets

- **Built VoiceKAM**, an open-source, brand-neutral voice AI Key Account Manager (`FastAPI`, `SSE`, `Web Speech API`, `edge-tts`) with server-controlled phase state machine, `en-IN`/`hinglish`/`hi-IN` speakable normalization, and 3-tier voice synthesis fallback (`edge-tts` -> Gemini TTS -> browser `speechSynthesis`).
- **Engineered a counterfactual disjoint attribution engine** combining daily-velocity OOS imputation, in-stock control-group median DiD pricing comparison, net-of-overlap impression funnels, and explicit residual accounting — recovering the true dominant revenue driver in **`200/200` synthetic causal trials (`0.02 pp` MAE)** while safely accepting RAM-only custom seller CSV/JSON uploads.
- **Eliminated LLM number hallucination & fragile phase drift** via server-side diagnostic tool pre-execution, post-generation numeric token auditing (`100.0%` turn & token grounding across `24` evaluated mock calls), word-boundary intent/negation matching, and `LLMRouter` failover across Groq, Gemini, and OpenRouter with `429` cooldowns, IP rate limits, and daily turn caps.

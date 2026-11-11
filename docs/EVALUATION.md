# VoiceKAM Evaluation Report

> **Strict Separation Policy:** Tables below are explicitly labeled **`Live`** or **`Offline Mock`** and are never combined. Any metric from a failed call or unmeasured channel is reported literally as **`not measured`**.

<!-- BEGIN_LIVE_EVAL -->
## Live Evaluation (Live)

### Run Metadata (Live)
- **Run Date (UTC):** 2026-11-11 08:18 UTC
- **Evaluation Kind:** Live
- **Responder Model ID(s):** gemini:gemini-3.5-flash-lite, gemini:gemini-3.6-flash, groq:openai/gpt-oss-120b, groq:openai/gpt-oss-20b
- **Judge Model ID(s):** gemini:gemini-3.5-flash-lite, gemini:gemini-3.6-flash, groq:openai/gpt-oss-120b, groq:openai/gpt-oss-20b
- **N (trials per persona x scenario cell):** 2
- **Calls Succeeded / Attempted:** 45 / 48 (3 failed)
- **Total Assistant Turns:** 292
- **Measurement Environment:** Local Windows development machine over consumer broadband (hosted Render free tier latency: `not measured`)
- **Execution / Cache Note:** Live API router + exponential backoff + disk cache (1/48 call cache hits)

### Aggregate Measured Metrics (Live)

| Metric (Live) | Measured Value | Detail |
| --- | --- | --- |
| Grounding rate (turns) | 100.0% | 292/292 turns with 0 ungrounded numbers |
| Grounding rate (number tokens) | 100.0% | 604/604 spoken numeric tokens traced to tools |
| Expected tool-call accuracy | 100.0% | 360/360 expected phase tools executed |
| Phase completion rate | 93.8% | 45/48 attempted calls reached SUMMARY/CLOSE with card (45/48 completed without API failure) |
| Words per turn (avg / max) | 32.3 / 56 | Voice word budget (`45` default, `55` DIAGNOSE/RECOMMEND, `82` SUMMARY) |
| Commitments captured | 93.8% (45 total) | 45/48 calls captured >=1 commitment |
| LLM first-token latency (p50 / p95) | 2004.0 ms / 5840.0 ms | SSE `latency.llm_first_token_ms` (292 turns measured on local machine; hosted latency `not measured`) |
| First-audio latency (p50 / p95) | 3969.0 ms / 7966.0 ms | Turn start -> first `sentence` + `edge-tts` MP3 ready (284 turns measured on local machine; hosted latency `not measured`) |
| Cross-model score (LLM-judged, 1-5) | 4.6 / 5.0 (rel 4.72, emp 4.24, clr 4.69, act 4.74, unc 4.6; n=43/48) | LLM-judged by distinct model (`relevance, empathy, clarity, actionability, honesty_uncertainty`) |

### Breakdown by Scenario (Live)

| Scenario (Live) | Calls | Completion Rate | Tool Accuracy | Grounded Turns | Avg Words | Commitments |
| --- | --- | --- | --- | --- | --- | --- |
| Scenario A (Live) | 11/12 | 91.7% | 100.0% | 100.0% | 30.4 | 11 |
| Scenario B (Live) | 12/12 | 100.0% | 100.0% | 100.0% | 32.2 | 12 |
| Scenario C (Live) | 12/12 | 100.0% | 100.0% | 100.0% | 33.7 | 12 |
| Scenario D (Live) | 10/12 | 83.3% | 100.0% | 100.0% | 32.7 | 10 |

### Breakdown by Persona (Live)

| Persona (Live) | Calls | Completion Rate | Grounded Turns | Avg / Max Words | Commitments | Judge (1-5) |
| --- | --- | --- | --- | --- | --- | --- |
| cooperative (en-IN, Live) | 7/8 | 87.5% | 100.0% | 31.0 / 50 | 7 | 4.6 (n=7) |
| vague (en-IN, Live) | 8/8 | 100.0% | 100.0% | 30.4 / 56 | 8 | 4.39 (n=7) |
| frustrated (en-IN, Live) | 8/8 | 100.0% | 100.0% | 31.8 / 54 | 8 | 4.47 (n=8) |
| off_topic (en-IN, Live) | 8/8 | 100.0% | 100.0% | 34.8 / 54 | 8 | 4.56 (n=8) |
| interrupting (en-IN, Live) | 6/8 | 75.0% | 100.0% | 32.0 / 54 | 6 | 4.8 (n=6) |
| hinglish (hinglish, Live) | 8/8 | 100.0% | 100.0% | 33.5 / 53 | 8 | 4.82 (n=7) |
<!-- END_LIVE_EVAL -->

---

<!-- BEGIN_OFFLINE_EVAL -->
## Offline Mock Evaluation (Offline Mock)

### Run Metadata (Offline Mock)
- **Run Date (UTC):** 2026-11-11 07:39 UTC
- **Evaluation Kind:** Offline Mock
- **Responder Model ID(s):** mock:deterministic-test-kam
- **Judge Model ID(s):** not measured
- **N (trials per persona x scenario cell):** 1
- **Calls Succeeded / Attempted:** 24 / 24 (0 failed)
- **Total Assistant Turns:** 156
- **Measurement Environment:** In-process offline mock on local Windows development machine (`first_audio_ms` and LLM-judged score: `not measured`; hosted cloud latency: `not measured`)
- **Execution / Cache Note:** Offline mode (`MockLLMProvider`); disk cache & audio TTS disabled (`not measured`)

### Aggregate Measured Metrics (Offline Mock)

| Metric (Offline Mock) | Measured Value | Detail |
| --- | --- | --- |
| Grounding rate (turns) | 100.0% | 156/156 turns with 0 ungrounded numbers |
| Grounding rate (number tokens) | 100.0% | 348/348 spoken numeric tokens traced to tools |
| Expected tool-call accuracy | 100.0% | 192/192 expected phase tools executed |
| Phase completion rate | 100.0% | 24/24 attempted calls reached SUMMARY/CLOSE with card (24/24 completed without API failure) |
| Words per turn (avg / max) | 23.5 / 34 | Voice word budget (`45` default, `55` DIAGNOSE/RECOMMEND, `82` SUMMARY) |
| Commitments captured | 100.0% (24 total) | 24/24 calls captured >=1 commitment |
| LLM first-token latency (p50 / p95) | 1.0 ms / 3.0 ms | SSE `latency.llm_first_token_ms` (156 turns measured on local machine; hosted latency `not measured`) |
| First-audio latency (p50 / p95) | not measured / not measured | Turn start -> first `sentence` + `edge-tts` MP3 ready (0 turns measured) |
| Cross-model score (LLM-judged, 1-5) | not measured | LLM-judged by distinct model (`relevance, empathy, clarity, actionability, honesty_uncertainty`) |

### Breakdown by Scenario (Offline Mock)

| Scenario (Offline Mock) | Calls | Completion Rate | Tool Accuracy | Grounded Turns | Avg Words | Commitments |
| --- | --- | --- | --- | --- | --- | --- |
| Scenario A (Offline Mock) | 6/6 | 100.0% | 100.0% | 100.0% | 23.1 | 6 |
| Scenario B (Offline Mock) | 6/6 | 100.0% | 100.0% | 100.0% | 23.4 | 6 |
| Scenario C (Offline Mock) | 6/6 | 100.0% | 100.0% | 100.0% | 23.5 | 6 |
| Scenario D (Offline Mock) | 6/6 | 100.0% | 100.0% | 100.0% | 24.0 | 6 |

### Breakdown by Persona (Offline Mock)

| Persona (Offline Mock) | Calls | Completion Rate | Grounded Turns | Avg / Max Words | Commitments | Judge (1-5) |
| --- | --- | --- | --- | --- | --- | --- |
| cooperative (en-IN, Offline Mock) | 4/4 | 100.0% | 100.0% | 23.9 / 34 | 4 | not measured |
| vague (en-IN, Offline Mock) | 4/4 | 100.0% | 100.0% | 23.1 / 34 | 4 | not measured |
| frustrated (en-IN, Offline Mock) | 4/4 | 100.0% | 100.0% | 24.4 / 34 | 4 | not measured |
| off_topic (en-IN, Offline Mock) | 4/4 | 100.0% | 100.0% | 23.0 / 34 | 4 | not measured |
| interrupting (en-IN, Offline Mock) | 4/4 | 100.0% | 100.0% | 22.5 / 34 | 4 | not measured |
| hinglish (hinglish, Offline Mock) | 4/4 | 100.0% | 100.0% | 24.4 / 34 | 4 | not measured |
<!-- END_OFFLINE_EVAL -->

---

## Causal-Recovery & Known Limitations
- **Synthetic Causal-Recovery Scope:** All causal-recovery results (`200/200` top-driver recovery, `0.02 pp` mean absolute error in `tests/test_attribution_properties.py`) are measured strictly on **synthetic ground truth generated by the project's own scenario generator (`app/data/scenarios.py`)** and are **not claims about real-world marketplace causal accuracy**.
- **Offline vs Live Separation:** Offline Mock rows run deterministically in-process via `MockLLMProvider` (`first_audio_ms` and judge score reported as `not measured`). Live rows exercise real external LLM providers, cross-model judging, and server-side `edge-tts` MP3 synthesis timing (`first_audio_ms`).
- **Headless Microphone Gap:** Scripted seller personas simulate multi-turn interactions deterministically and do not capture live acoustic room noise, speaker echo, or browser `SpeechRecognition` STT errors; real browser microphone verification remains manual per [VOICE_TEST_CHECKLIST.md](VOICE_TEST_CHECKLIST.md).

# VoiceKAM Evaluation Report

## Run Metadata
- **Run Date (UTC):** 2026-11-11 03:23 UTC
- **Mode:** offline (hermetic MockLLMProvider)
- **Responder Model ID(s):** mock:deterministic-test-kam
- **Judge Model ID(s):** not measured
- **N (trials per persona x scenario cell):** 1
- **Total Evaluated Calls:** 24 (6 personas x 4 scenarios x 1)
- **Total Assistant Turns:** 156
- **Cache / Execution Note:** Offline mode; disk cache unused

## Aggregate Measured Metrics

| Metric | Measured Value | Detail |
| --- | --- | --- |
| Grounding rate (turns) | 100.0% | 156/156 turns with 0 ungrounded numbers |
| Grounding rate (number tokens) | 100.0% | 348/348 spoken numeric tokens traced to tools |
| Expected tool-call accuracy | 100.0% | 192/192 expected phase tools executed |
| Phase completion rate | 100.0% | 24/24 calls reached SUMMARY/CLOSE with card |
| Words per turn (avg / max) | 23.5 / 34 | Hard cap 45 words (60 in RECOMMEND) |
| Commitments captured | 100.0% (24 total) | 24/24 calls captured >=1 commitment |
| LLM first-token latency (p50 / p95) | 1.0 ms / 2.0 ms | Computed from SSE `latency` events |
| First-audio latency (p50 / p95) | not measured / not measured | Audio TTS synthesis/playback timing |
| LLM-as-judge rubric score (1-5) | not measured | Relevance, empathy, clarity, actionability, uncertainty honesty |

## Breakdown by Scenario

| Scenario | Calls | Completion Rate | Tool Accuracy | Grounded Turns | Avg Words | Commitments |
| --- | --- | --- | --- | --- | --- | --- |
| Scenario A | 6 | 100.0% | 100.0% | 100.0% | 23.1 | 6 |
| Scenario B | 6 | 100.0% | 100.0% | 100.0% | 23.4 | 6 |
| Scenario C | 6 | 100.0% | 100.0% | 100.0% | 23.5 | 6 |
| Scenario D | 6 | 100.0% | 100.0% | 100.0% | 24.0 | 6 |

## Breakdown by Persona

| Persona | Calls | Completion Rate | Grounded Turns | Avg / Max Words | Commitments |
| --- | --- | --- | --- | --- | --- |
| cooperative (en-IN) | 4 | 100.0% | 100.0% | 23.9 / 34 | 4 |
| vague (en-IN) | 4 | 100.0% | 100.0% | 23.1 / 34 | 4 |
| frustrated (en-IN) | 4 | 100.0% | 100.0% | 24.4 / 34 | 4 |
| off_topic (en-IN) | 4 | 100.0% | 100.0% | 23.0 / 34 | 4 |
| interrupting (en-IN) | 4 | 100.0% | 100.0% | 22.5 / 34 | 4 |
| hinglish (hinglish) | 4 | 100.0% | 100.0% | 24.4 / 34 | 4 |

## Known Limitations
- Scripted seller personas simulate multi-turn interactions deterministically and do not capture spontaneous acoustic interruptions, background noise, or real Web Speech API STT errors (`first_audio_ms` and real microphone STT remain `not measured` in headless CI evaluation).
- Offline evaluation uses the deterministic in-process `MockLLMProvider`; first-token latencies reflect local Python execution rather than networked free-tier LLM API latency, and LLM-as-judge quality scores are reported as `not measured`.

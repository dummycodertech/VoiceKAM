# VoiceKAM Technical Architecture

VoiceKAM separates **analytical truth**, **dialog control**, **language generation**, and **speech synthesis** into cleanly bounded server layers so free-tier language models never perform arithmetic, drop out of the consultative flow unexpectedly, or stall without fallback on failing providers.

---

## 1. Server-Owned Conversation State Machine

Rather than trusting a lightweight LLM to remember where a call is in a consultative diagnostic workflow, [`app/agent/state_machine.py`](../app/agent/state_machine.py) owns the call graph (`Phase` enum in [`app/agent/session.py`](../app/agent/session.py)):

- **Standard / Inbound & Proactive Flow:**
  `GREETING -> UNDERSTAND -> CLARIFY -> DIAGNOSE -> RECOMMEND -> COMMIT -> SUMMARY -> CLOSE`
- **3-Day Follow-Up Flow (`mode == "followup"`):**
  `GREETING -> REVIEW_COMMITMENTS -> RECOMMEND -> COMMIT -> SUMMARY -> CLOSE`

### Key Design Mechanics
1. **Before-Turn & After-Turn Transitions:** `advance_phase_before_turn()` evaluates normalized word-boundary regexes (`_DIAGNOSIS_REQUEST_RE`, `_BUY_IN_RE`, negated via `_NEGATION_RE`) and `clarify_questions_asked` depth before calling the LLM; `advance_phase_after_turn()` increments clarification counts and moves from `DIAGNOSE` or `COMMIT` once structured commitments exist.
2. **Phase-Gated Tool Exposure (`tools_for_phase()`):**
   - `GREETING`, `UNDERSTAND`, `DIAGNOSE`, `REVIEW_COMMITMENTS`, `RECOMMEND`, `CLOSE`: `0` LLM tools exposed.
   - `CLARIFY`: `get_seller_overview`, `get_metric_comparison`.
   - `COMMIT`: `commit_action`, `schedule_followup`.
   - `SUMMARY`: `send_call_summary`, `schedule_followup`.
   - Keeping active tool schemas `<= 2` dramatically reduces free-tier function-calling syntax errors and prompt token usage.
3. **Server Pre-Execution (`_pre_execute_for_phase()`):**
   - When entering `DIAGNOSE` (all 5 diagnostic tools), `REVIEW_COMMITMENTS` (4 tools), `RECOMMEND` (`build_action_plan`), or `proactive`/`followup` `GREETING`, [`AgentOrchestrator`](../app/agent/orchestrator.py) executes the deterministic Python tools via `asyncio.gather` **before** contacting the LLM.
   - Tool results emit `tool_start`, `tool_call`, and `tool_result` SSE events immediately so the KAM Brain side panel reveals charts cleanly even while the spoken reply streams.
   - Compact tool payloads (`_compact_tool_payload()`) trim verbose per-SKU metadata when injecting precomputed context into the system prompt, preserving free-tier TPM budget.
4. **History Slicing (`slice_recent_messages()`):**
   - Retains the trailing `~10` messages per turn while scanning backward/forward so a conversation window never opens on an orphaned `role="tool"` message or an incomplete `assistant(tool_calls)` record.

---

## 2. Deterministic Attribution & Numeric Grounding Pipeline

1. **Counterfactual Attribution (`app/engine/diagnosis.py`):** Computes lost orders and revenue monotonically from the session's immutable `SellerContext` (`app/data/context.py`) — never reading synthetic `driver_group` test tags. Missing optional CSV columns degrade cleanly to `"not assessable with the data provided"` with unattributed variance accounted for inside `unexplained_residual` ([ATTRIBUTION.md](ATTRIBUTION.md)).
2. **Ground-Truth Collector:** Every tool execution (server pre-executed, LLM-invoked, or server fallback) extracts numeric tokens via `extract_numbers_from_obj()` into `session.grounded_numbers`.
3. **Voice Length & Hygiene (`enforce_voice_length`, `strip_reasoning_and_markdown`):** Strips `<think>...</think>` blocks, markdown syntax, bullets, and emojis, clipping cleanly at sentence boundaries around `45` words (`75` in `SUMMARY`) so turns remain crisp for telephony/voice playback.
4. **Post-Generation Numeric Audit (`find_ungrounded_numbers`):** Inspects every spoken digit sequence against `session.grounded_numbers` (masking legitimate `SellerContext` SKU IDs such as `US-001` or uploaded identifiers, years, and natural ordinals). Any ungrounded number is flagged in the `latency` SSE event (`ungrounded_numbers`) and recorded on `session.ungrounded_warnings`.
5. **Summary Guarantee:** In `SUMMARY`, if the LLM does not invoke `send_call_summary`, the orchestrator runs a server fallback (`source: "server_fallback"`) so the `summary_card` and WhatsApp recap always render.

---

## 3. Multi-Provider LLM Failover (`LLMRouter`)

[`LLMRouter`](../app/llm/router.py) routes streaming completions across ordered free-tier backends:
1. **Groq Free Tier:** Filtered live against Groq's `/models` catalog at startup (`fetch_live_groq_models`), skipping decommissioned models before requests hit inference.
2. **Google Gemini Free Tier:** Configured for high-RPD Flash models (`gemini-3.5-flash-lite`, `gemini-3.6-flash`).
3. **OpenRouter Free Tier:** Optional 3rd-tier backup (`openrouter/free`).
4. **MockLLMProvider:** Optional deterministic offline provider active only when `ALLOW_MOCK_LLM=true` for CI, offline evaluation, and browser testing.

**Failure Handling:**
- **HTTP `429` (`rate_limited`):** Places provider into temporary cooldown (`Retry-After` or `PROVIDER_COOLDOWN_SECONDS`).
- **HTTP `403` (`forbidden`):** Permanently disables provider for the lifetime of the server process so subsequent turns skip straight to healthy fallbacks.
- **Timeouts / `5xx` (`timeout` / `server_error`):** Fails over cleanly while emitting a sanitized `failover` SSE event (never leaking raw API keys or upstream error payloads).

---

## 4. Speech Pipeline (STT, Speakable Normalization & 2-Tier TTS)

- **Speakable Normalization (`app/agent/speakable.py`):** Converts telemetry tokens (`₹1,31,820`, `57.4%`, `US-001`, `GMV`, `SLA`, `->`) into natural `en-IN`, `hinglish`, or Devanagari `hi-IN` phrasing (`एक लाख इकतीस हज़ार आठ सौ बीस रुपये`, `प्रोडक्ट 1`, `प्रतिशत`) per sentence before audio synthesis.
- **Streaming Sentence Synthesis:** `orchestrator.py` yields SSE `sentence` events as soon as punctuation closes a clause so the frontend pre-fetches audio for sentence `N+1` while playing sentence `N`.
- **2-Tier TTS (`app/tts/service.py`):**
  1. **Microsoft Edge Neural TTS (`edge-tts`):** Indian neural voices (`en-IN-NeerjaNeural`, `hi-IN-SwaraNeural`) cached in a 200-entry RAM LRU (`X-TTS-Cached`).
  2. **Browser Fallback:** Returns HTTP `200` `{ "use_browser_tts": true }` on upstream throttling/absence so the browser invokes `window.speechSynthesis` cleanly without red console errors.
- **Dual STT Input (`frontend/audio.js`):** Primary Chrome `SpeechRecognition` with 800 ms end-of-utterance debouncing, half-duplex speaker echo guard, optional headphone barge-in, 6-second silence check-in, and optional Groq Whisper `/api/stt` fallback for non-Chrome browsers.

---

## 5. Ephemeral State, Security & Public-Demo Quotas

- **RAM-Only Multi-Tenant Isolation:** Both [`SessionStore`](../app/agent/session.py) (`max_sessions=200`, `session_ttl_seconds=7200`) and [`UploadedDatasetStore`](../app/data/upload.py) hold per-session state exclusively in RAM (`--workers 1`).
- **Traffic & Cost Protection (`app/main.py`):**
  - `SlidingWindowRateLimiter` keyed by `X-Forwarded-For` first hop (chat/greeting, TTS/probe, STT, session start/validation) returning HTTP `429` JSON with `Retry-After`.
  - `DailyTurnTracker` enforcing `DAILY_TURN_CAP` per UTC day with a friendly SSE `error` message.
  - Global request-body `MAX_REQUEST_BYTES` guard (HTTP `413`), strict upload schema validator (formula-injection blocking + HTML stripping), response security headers (`nosniff`, `DENY`, `no-referrer`, `Permissions-Policy`, `COOP`), and `/api/health` secret scrubbing (`_scrub_secrets`).

# Snapdeal Virtual KAM ("Riya") — Backend API & SSE Contract (`v1`)

- **Contract Version:** `v1` (post-handoff stabilized baseline)
- **Runtime Requirement:** Single-worker FastAPI + Uvicorn (`--workers 1`) due to in-memory `SessionStore`, `LLMRouter` cooldown/disablement tables, and `TTSService` LRU cache.
- **CORS / Headers:** `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`; SSE streams set `Cache-Control: no-cache` and `X-Accel-Buffering: no`. No CSP headers are emitted.
- **Breaking Change Policy (`v1`):** Required endpoint paths, SSE event names, and required payload fields documented below must not be removed or renamed within `v1`. Enforced by `tests/test_api_contract.py`.

---

## 1. Static & Diagnostic REST Endpoints

| Method | Path | Request / Query | Response (`200 OK` unless noted) |
| :--- | :--- | :--- | :--- |
| `GET` | `/` | — | `frontend/index.html` (`FileResponse`) or `{"status":"ok","message":...}` |
| `GET` | `/static/{path}` | Path under `frontend/` | Static asset (`200`) or `404` |
| `GET` | `/favicon.ico` | — | `204 No Content` |
| `GET` | `/api/health` | — | `{status, prototype_notice, providers_configured, active_llm_chain, disabled_providers, llm_startup_smoke, tts_config, features}` |
| `GET` | `/api/demo-data` | `?snapshot=current\|followup` (default `"current"`) | `{overview, metrics, diagnosis, oos_skus, cancellations, pricing, funnel, action_plan}` |
| `GET` | `/api/tts-probe` | `?text=...&language=en-IN\|hi-IN\|hinglish` | `{edge_tts_ok, voice, latency_ms, recommended_tts_mode, ...}` |

---

## 2. Session & Voice REST Endpoints

### `POST /api/session/start`
- **Request (`application/json`):** `{"mode"?: "inbound"|"proactive"|"followup" = "inbound", "language"?: "en-IN"|"hi-IN"|"hinglish" = "en-IN", "prior_session_id"?: string|null = null}`
- **Response (`200 OK`):** `{"session_id": string, "session": SessionPublicDict, "demo_data": DemoDataDict}`

### `GET /api/session/{session_id}` & `POST /api/session/{session_id}/settings`
- **`POST .../settings` Request (`application/json`):** `{"language"?: "en-IN"|"hi-IN"|"hinglish"|null}`
- **Response (`200 OK`):** `SessionPublicDict` (`session_id`, `mode`, `snapshot`, `language`, `phase`, `turn_count`, `clarify_questions_asked`, `commitments`, `prior_commitments`, `followup_schedule`, `summary_card`, `tool_history`, `active_provider`, `failover_events`, `latency_history`, `ungrounded_warnings`)
- **Error (`404`):** `{"detail": "Session not found"}`

### `POST /api/tts`
- **Request (`application/json`):** `{"text": string, "language"?: "en-IN"|"hi-IN"|"hinglish" = "en-IN", "provider"?: "auto"|"edge-tts"|"gemini-tts"|"browser"|null}`
- **Success Audio Response (`200 OK`):**
  - Body: Binary audio (`audio/mpeg` or `audio/wav`)
  - Headers: `X-TTS-Provider`, `X-TTS-Voice`, `X-TTS-Cached` (`"true"|"false"`)
- **Browser Fallback Response (`200 OK`):**
  - `Content-Type: application/json` (intentionally HTTP `200` so clients switch to Web Speech `speechSynthesis` cleanly without network errors)
  - Body: `{"use_browser_tts": true, "fallback": "browser", "reason": string, "cached": false}`

### `POST /api/stt`
- **Request (`multipart/form-data`):** `audio` (`UploadFile`, required), `language` (`Form`, optional `"en"`|`hi-IN`)
- **Response (`200 OK`):** `{"text": string, "provider": "groq-whisper"}`
- **Errors:** `503` (missing `GROQ_API_KEY`), `400` (empty audio), `502` (upstream failure)

---

## 3. SSE Streaming Endpoints (`POST /api/session/{session_id}/greeting` & `POST /api/chat`)

- **Transport:** `POST` with `fetch()` + `ReadableStream` (not `EventSource`). Frames follow `event: <name>\ndata: <json>\n\n`.
- **`POST /api/session/{session_id}/greeting`:** Empty body accepted (`404` if invalid `session_id`).
- **`POST /api/chat`:** `{"session_id": string, "text": string, "language"?: LanguageCode|null}`. (`404` if session absent; whitespace-only `text` yields empty stream.)

### Canonical `v1` SSE Event Catalog

| Event | Required Fields | Optional / Mode Notes & Ordering Guarantees |
| :--- | :--- | :--- |
| `phase` | `phase`, `mode` | Emitted at start of every non-empty stream (`start_call`, `run_turn`). |
| `phase_change` | `phase`, `previous_phase`, `mode` | Emitted immediately after `phase` **whenever `session.phase` transitions** (`session.phase != prev_phase`). |
| `tool_start` | `name`, `arguments`, `source` | Emitted **before** executing each tool (`source` in `"server_preexec" \| "llm" \| "server_fallback"`). For pre-executed phases, emitted before any `token` or `sentence`. |
| `tool_call` | `name`, `arguments`, `source` | Legacy/compat alias emitted alongside `tool_start` with identical payload. |
| `tool_result` | `name`, `result`, `latency_ms`, `source` | Emitted immediately upon completion with full structured `result` dict. For `server_preexec`, guaranteed to precede any spoken `token`/`sentence`. |
| `summary_card` | `seller_name`, `shop_name`, `key_issues`, `commitments`, `followup`, `whatsapp_text` | Emitted immediately after `send_call_summary` `tool_result` (Round 0 LLM or `SUMMARY` server fallback). |
| `failover` | `type`, `from`, `to`, `reason_code` | Sanitized failover notification: `type == "provider_failover"`, `reason_code in {"rate_limited", "forbidden", "timeout", "server_error"}`. **Never** contains raw upstream error JSON, stack traces, org IDs, or API keys. |
| `token` | `text` | Incremental assistant display text chunk (not sent to TTS directly). |
| `sentence` | `text`, `speakable` | Complete spoken clause for TTS (`speakable` has INR/percent/abbreviation normalization). |
| `latency` | `llm_first_token_ms`, `total_turn_ms`, `llm_requests`, `provider`, `ungrounded_numbers` | Emitted at end of LLM-backed turns before `done` (omitted on zero-LLM inbound template greeting). |
| `done` | `text`, `phase`, `provider` | Also includes `speakable`, `commitments`, `followup_schedule`, `summary_card` on LLM-backed turns. Terminal success event. |
| `error` | `message` | Sanitized failure notice when all providers fail or unexpected server exception occurs (no raw upstream JSON/org IDs/keys). |

### Provider Failure Semantics (`LLMRouter`)
- **HTTP `429` (`reason_code: "rate_limited"`):** Places provider into temporary cooldown (`Retry-After` or `PROVIDER_COOLDOWN_SECONDS`); retried only after cooldown or if all other candidates are cooling.
- **HTTP `403` (`reason_code: "forbidden"`):** Marks provider **permanently disabled for the lifetime of the server process** (`router.disabled_providers`); never selected again in `_ordered_candidates()`.
- **Timeout / 408 / 504 (`reason_code: "timeout"`):** Triggers failover immediately without disabling permanently.
- **5xx / 4xx / stream / network errors (`reason_code: "server_error"`):** Triggers failover immediately without disabling permanently.


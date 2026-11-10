# Snapdeal Virtual KAM ("Riya") — Frontend Redesign Handoff Packet (Part 2 of 2)

> **Read-only reference specification (Part 2 of 2: Contracts, Voice Loop, DOM/State, KAM Schemas, Known Limits, Captures & Backend Gaps)** generated directly from the live codebase and real captured runs (`<REDACTED>` applied to all secrets, tokens, org/project IDs, and account names).
>
> **Companion file context (`FRONTEND_HANDOFF_part1.md`):** The backend is a single-worker (`--workers 1`) Python 3.12 + FastAPI service (`port 7860` default, `--cors allow_origins=["*"]`, no CSP headers) that serves `GET /` (`frontend/index.html`) and mounts `/static/*` -> `frontend/` (currently 5 Vanilla ES6 files, zero bundler: `index.html`, `styles.css`, `app.js`, `audio.js`, `kam_brain.js`). Real session logs live under `docs/captures/*.json`.

### Cross-Part Contents
- **Part 1 (`FRONTEND_HANDOFF_part1.md`):** `## 1. Overview` | `## 2. Verbatim source of EVERY frontend file`
- **Part 2 (`FRONTEND_HANDOFF_part2.md` — this file):** `## 3. API contract` | `## 4. Voice-loop behaviour spec` | `## 5. DOM and state contract` | `## 6. Data shapes rendered by the KAM Brain panel` | `## 7. Known issues and limits` | `## 8. Captured real session logs` | `## 9. Backend gaps that affect the UI`


---

## 3. API contract (the interface I must not break)

> Note: All REST/SSE JSON payloads below use UTF-8 (`Content-Type: application/json` or `text/event-stream; charset=utf-8`). Standard FastAPI errors return `404` / `400` / `502` / `503` with `{"detail": "<string>"}` and Pydantic validation errors return `422 Unprocessable Entity` with `{"detail": [...]}`.

### 3.1 Endpoint Summary Table
| Method & Path | Purpose | Request Shape | Response Shape / Type | Status Codes |
| :--- | :--- | :--- | :--- | :--- |
| `GET /` | Serve UI shell | None | HTML (`frontend/index.html`) or JSON notice | `200` |
| `GET /static/{path}` | Serve frontend files | Path under `frontend/` | JS / CSS / static asset | `200`, `404` |
| `GET /favicon.ico` | Suppress browser 404 | None | Empty body | `204` |
| `GET /api/health` | Probe LLM/TTS/feature readiness | None | JSON health object | `200` |
| `GET /api/demo-data` | Pre-hydrate & inspect store KPIs | Query: `snapshot` (`"current"` optional, `"followup"` if exact match, else `"current"`) | JSON bundle of 8 diagnosis datasets | `200` |
| `POST /api/session/start` | Create session & return snapshot | JSON `StartSessionRequest` | JSON `{session_id, session, demo_data}` | `200`, `422` |
| `POST /api/session/{id}/greeting` | Stream opening KAM greeting | Path `session_id`; empty `{}` body ignored | SSE stream (`text/event-stream`) | `200`, `404` |
| `POST /api/chat` | Run one seller turn (streaming) | JSON `ChatRequest` | SSE stream (`text/event-stream`) | `200`, `404`, `422` |
| `GET /api/session/{id}` | Fetch full session audit state | Path `session_id` | JSON `SessionState.to_public_dict()` | `200`, `404` |
| `POST /api/session/{id}/settings` | Update active call language | Path `session_id`; JSON `UpdateSettingsRequest` | JSON `SessionState.to_public_dict()` | `200`, `404`, `422` |
| `POST /api/tts` | Synthesize sentence speech | JSON `TTSRequest` | Binary `audio/mpeg` / `audio/wav` OR HTTP `200` JSON fallback | `200`, `422` |
| `GET /api/tts-probe` | Live diagnostic for `edge-tts` | Query: `text` (opt), `language` (opt, `"en-IN"`) | JSON diagnostic object | `200`, `422` |
| `POST /api/stt` | Whisper STT fallback (unused in current UI) | `multipart/form-data`: `audio` (File, req), `language` (Form, opt `"en"`) | JSON `{"text": str, "provider": "groq-whisper"}` | `200`, `400`, `502`, `503` |

### 3.2 Detailed REST Endpoint Contracts & Real Captures

#### `GET /api/health`
- **Request:** No headers required.
- **Response (`200 OK`):**
  - `status`: `"ok"`
  - `prototype_notice`: `"Prototype - Mock Data Only"`
  - `providers_configured`: `{groq: bool, gemini: bool, openrouter: bool, mock: bool}`
  - `active_llm_chain`: `string[]` (format `"<provider>:<model>"`, excluding permanently disabled HTTP 403 models)
  - `disabled_providers`: `Record<string, string>` *(added in `v1`)* — map of permanently disabled `provider:model` -> `reason_code` (`"forbidden"`)
  - `llm_startup_smoke`: `Array<{provider: string, model: string, smoke_ok: bool, tool_called: bool, latency_ms: int, error: string|null}>`
  - `tts_config`: `{provider: string, voice_en: string, voice_hi: string, active_gemini_tts_model: string|null, gemini_tts_smoke: Array<...>}`
  - `features`: `{enable_gemini_live: bool, whisper_stt_fallback: bool}`
- **Real Captured Payload (`docs/captures/health_and_demo_data.json`, pre-v1 baseline; `v1` adds `disabled_providers: {}`):**
```json
{
  "status": "ok",
  "prototype_notice": "Prototype - Mock Data Only",
  "providers_configured": {
    "groq": true,
    "gemini": true,
    "openrouter": false,
    "mock": false
  },
  "active_llm_chain": [
    "groq:openai/gpt-oss-20b",
    "groq:openai/gpt-oss-120b",
    "groq:qwen/qwen3.8-27b",
    "gemini:gemini-3.5-flash-lite",
    "gemini:gemini-3.6-flash"
  ],
  "llm_startup_smoke": [
    {
      "provider": "groq",
      "model": "openai/gpt-oss-20b",
      "smoke_ok": true,
      "tool_called": true,
      "latency_ms": 496,
      "error": null
    },
    {
      "provider": "gemini",
      "model": "gemini-3.5-flash-lite",
      "smoke_ok": true,
      "tool_called": true,
      "latency_ms": 2421,
      "error": null
    }
  ],
  "tts_config": {
    "provider": "auto",
    "voice_en": "en-IN-NeerjaNeural",
    "voice_hi": "hi-IN-SwaraNeural",
    "active_gemini_tts_model": null,
    "gemini_tts_smoke": [
      {
        "model": "gemini-3.8-flash-tts",
        "smoke_ok": false,
        "bytes": 0,
        "latency_ms": 472,
        "error": "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'Invalid JSON payload received. Unknown name \"speechConfig\" at \\'generation_config\\': Cannot find field.', 'status': 'INVALID_ARGUMENT', 'detai"
      },
      {
        "model": "gemini-2.5-flash-preview-tts",
        "smoke_ok": false,
        "bytes": 0,
        "latency_ms": 445,
        "error": "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'Invalid JSON payload received. Unknown name \"speechConfig\" at \\'generation_config\\': Cannot find field.', 'status': 'INVALID_ARGUMENT', 'detai"
      }
    ]
  },
  "features": {
    "enable_gemini_live": false,
    "whisper_stt_fallback": true
  }
}
```

#### `GET /api/demo-data?snapshot=current|followup`
- **Query Params:** `snapshot?: string = "current"` (exact `"followup"` selects follow-up snapshot; any other value defaults to `"current"`).
- **Response (`200 OK`):** JSON object with 8 top-level keys: `overview`, `metrics`, `diagnosis`, `oos_skus`, `cancellations`, `pricing`, `funnel`, `action_plan`. Exact real schemas and examples for every key are documented in Section 6.

#### `POST /api/session/start`
- **Request Headers:** `Content-Type: application/json`
- **Request Body (`StartSessionRequest`):**
  - `mode?: "inbound" | "proactive" | "followup"` (default `"inbound"`)
  - `language?: "en-IN" | "hi-IN" | "hinglish"` (default `"en-IN"`)
  - `prior_session_id?: string | null` (default `null`; when `mode == "followup"`, copies `prev.commitments` with `status: "partially_done"`, or falls back to `FOLLOWUP_SNAPSHOT["default_prior_commitments"]` if absent/empty)
- **Response (`200 OK`):** `{"session_id": str, "session": SessionPublicDict, "demo_data": DemoDataDict}`
- **Real Captured Payload (`docs/captures/inbound_call.json`):**
```json
{
  "session_id": "e04608bf960e",
  "session": {
    "session_id": "e04608bf960e",
    "mode": "inbound",
    "snapshot": "current",
    "language": "en-IN",
    "phase": "GREETING",
    "turn_count": 0,
    "clarify_questions_asked": 0,
    "tool_history": [],
    "commitments": [],
    "prior_commitments": [],
    "followup_schedule": null,
    "summary_card": null,
    "active_provider": "",
    "failover_events": [],
    "latency_history": [],
    "ungrounded_warnings": []
  },
  "demo_data": "<identical schema to GET /api/demo-data?snapshot=current — see Section 6>"
}
```

#### `GET /api/session/{session_id}` & `POST /api/session/{session_id}/settings`
- **Settings Request Headers & Body:** `Content-Type: application/json`, `{"language"?: "en-IN" | "hi-IN" | "hinglish" | null}` (optional; only mutates `session.language` when truthy).
- **Response (`200 OK`):** Full `SessionPublicDict`. **Error (`404`):** `{"detail": "Session not found"}`.
- **Real Captured Settings Response (`docs/captures/inbound_call.json`):**
  - Called `POST /api/session/e04608bf960e/settings` with `{"language": "en-IN"}` -> `200 OK` returning full session state (identical shape to `GET /api/session/{id}` below; full `tool_history` & `summary_card` arrays documented in Sections 6 & 8):
```json
{
  "session_id": "e04608bf960e",
  "mode": "inbound",
  "snapshot": "current",
  "language": "en-IN",
  "phase": "CLOSE",
  "turn_count": 7,
  "clarify_questions_asked": 2,
  "commitments": [
    {
      "action_id": "ACT-1",
      "action_summary": "Restock top 5 high velocity OOS SKUs (HK 006 to HK 010)",
      "owner": "Rajesh Kumar",
      "due_date": "Within 48 hours",
      "target_metric": "OOS <= 8 products",
      "status": "committed"
    }
  ],
  "prior_commitments": [],
  "followup_schedule": null,
  "active_provider": "groq:openai/gpt-oss-120b",
  "failover_events": [
    {
      "from": "groq:openai/gpt-oss-20b",
      "to": "groq:openai/gpt-oss-120b",
      "reason": "groq (openai/gpt-oss-20b) HTTP 429 rate limited: {\"error\":{\"message\":\"Rate limit reached for model `openai/gpt-oss-20b` in organization `org"
    }
  ],
  "latency_history": [
    {
      "llm_first_token_ms": 640,
      "total_turn_ms": 698,
      "llm_requests": 1,
      "provider": "groq:openai/gpt-oss-120b",
      "ungrounded_numbers": []
    },
    {
      "llm_first_token_ms": 581,
      "total_turn_ms": 626,
      "llm_requests": 1,
      "provider": "groq:openai/gpt-oss-120b",
      "ungrounded_numbers": []
    },
    {
      "llm_first_token_ms": 490,
      "total_turn_ms": 618,
      "llm_requests": 1,
      "provider": "groq:openai/gpt-oss-120b",
      "ungrounded_numbers": []
    },
    {
      "llm_first_token_ms": 471,
      "total_turn_ms": 752,
      "llm_requests": 1,
      "provider": "groq:openai/gpt-oss-120b",
      "ungrounded_numbers": []
    },
    {
      "llm_first_token_ms": 1330,
      "total_turn_ms": 1330,
      "llm_requests": 2,
      "provider": "groq:openai/gpt-oss-120b",
      "ungrounded_numbers": []
    },
    {
      "llm_first_token_ms": 1255,
      "total_turn_ms": 1346,
      "llm_requests": 2,
      "provider": "groq:openai/gpt-oss-120b",
      "ungrounded_numbers": []
    },
    {
      "llm_first_token_ms": 554,
      "total_turn_ms": 640,
      "llm_requests": 1,
      "provider": "groq:openai/gpt-oss-120b",
      "ungrounded_numbers": []
    }
  ],
  "ungrounded_warnings": []
}
```

#### `GET /api/tts-probe`
- **Query Params:** `text?: string = "Namaste Rajesh ji, testing neural voice output."`, `language?: "en-IN" | "hi-IN" | "hinglish" = "en-IN"`
- **Real Captured Payload (`docs/captures/health_and_demo_data.json`):**
```json
{
  "edge_tts_ok": true,
  "voice": "en-IN-NeerjaNeural",
  "bytes": 27504,
  "latency_ms": 1111,
  "recommended_tts_mode": "edge-tts"
}
```

#### `POST /api/tts`
- **Request Headers & Body:** `Content-Type: application/json`; `{"text": string (required), "language"?: "en-IN"|"hi-IN"|"hinglish" (default "en-IN"), "provider"?: "auto"|"edge-tts"|"gemini-tts"|"browser"|null (default null)}`.
- **Response Mode 1 — Binary Audio (`200 OK`):**
  - `Content-Type`: `"audio/mpeg"` (`edge-tts`) or `"audio/wav"` (`gemini-tts`).
  - **Custom Response Headers:** `X-TTS-Provider` (`"edge-tts"` or `"gemini-tts:<model>"`), `X-TTS-Voice` (`"en-IN-NeerjaNeural"`, `"hi-IN-SwaraNeural"`, or `"Aoede"`), `X-TTS-Cached` (`"true"` | `"false"`).
- **Response Mode 2 — Browser Fallback Signal (still `200 OK`!):**
  - **How failure/fallback is signalled:** Backend deliberately returns **HTTP `200`** with `Content-Type: application/json` instead of 4xx/5xx so browser fetch never logs red console errors. Frontend `audio.js` checks `if (!resp.ok || contentType.includes("application/json"))` and seamlessly speaks locally via `window.speechSynthesis`.
  - JSON body: `{"use_browser_tts": true, "fallback": "browser", "reason": string, "cached": false}`.
- **Caching:** Server-side `OrderedDict` LRU (max `200` entries, keyed by `(provider_pref, voice, clean_text)`); HTTP response itself does not set `Cache-Control`.
- **Real Captured `/api/tts` Success + Cache Hit + Fallbacks (`docs/captures/tts_success_and_fallback.json`):**
```json
{
  "tts_success_cold": {
    "request": {
      "method": "POST",
      "url": "/api/tts",
      "body": {
        "text": "Namaste Rajesh ji, I am Riya, your Snapdeal account manager.",
        "language": "en-IN",
        "provider": "auto"
      }
    },
    "status_code": 200,
    "headers": {
      "date": "Tue, 10 Nov 2026 11:11:36 GMT",
      "server": "uvicorn",
      "x-tts-provider": "edge-tts",
      "x-tts-voice": "en-IN-NeerjaNeural",
      "x-tts-cached": "false",
      "content-length": "33552",
      "content-type": "audio/mpeg",
      "vary": "Origin"
    },
    "total_bytes": 33552,
    "first_64_bytes_hex": "fff364c40000000348000000004c414d455555554c414d45332e3130305555555555555555555555555555555555555555555555555555555555555555555555"
  },
  "tts_success_cached": {
    "request": {
      "method": "POST",
      "url": "/api/tts",
      "body": {
        "text": "Namaste Rajesh ji, I am Riya, your Snapdeal account manager.",
        "language": "en-IN",
        "provider": "auto"
      }
    },
    "status_code": 200,
    "headers": {
      "date": "Tue, 10 Nov 2026 11:11:40 GMT",
      "server": "uvicorn",
      "x-tts-provider": "edge-tts",
      "x-tts-voice": "en-IN-NeerjaNeural",
      "x-tts-cached": "true",
      "content-length": "33552",
      "content-type": "audio/mpeg",
      "vary": "Origin"
    },
    "total_bytes": 33552,
    "first_64_bytes_hex": "fff364c40000000348000000004c414d455555554c414d45332e3130305555555555555555555555555555555555555555555555555555555555555555555555"
  },
  "tts_fallback_browser_selected": {
    "request": {
      "method": "POST",
      "url": "/api/tts",
      "body": {
        "text": "Namaste Rajesh ji.",
        "language": "en-IN",
        "provider": "browser"
      }
    },
    "status_code": 200,
    "headers": {
      "date": "Tue, 10 Nov 2026 11:11:40 GMT",
      "server": "uvicorn",
      "content-length": "93",
      "content-type": "application/json",
      "vary": "Origin"
    },
    "response": {
      "use_browser_tts": true,
      "fallback": "browser",
      "reason": "user_selected_browser",
      "cached": false
    }
  },
  "tts_fallback_empty_text": {
    "request": {
      "method": "POST",
      "url": "/api/tts",
      "body": {
        "text": "   ",
        "language": "en-IN",
        "provider": "auto"
      }
    },
    "status_code": 200,
    "headers": {
      "date": "Tue, 10 Nov 2026 11:11:40 GMT",
      "server": "uvicorn",
      "content-length": "82",
      "content-type": "application/json",
      "vary": "Origin"
    },
    "response": {
      "use_browser_tts": true,
      "fallback": "browser",
      "reason": "empty_text",
      "cached": false
    }
  },
  "tts_fallback_live_edge_failure": {
    "request": {
      "method": "POST",
      "url": "/api/tts",
      "body": {
        "text": "Namaste Rajesh ji, your sales dropped 32.5 percent.",
        "language": "en-IN",
        "provider": "auto"
      }
    },
    "status_code": 200,
    "headers": {
      "date": "Tue, 10 Nov 2026 11:09:17 GMT",
      "server": "uvicorn",
      "content-length": "152",
      "content-type": "application/json",
      "vary": "Origin"
    },
    "total_bytes": 152,
    "first_64_bytes_hex": "7b227573655f62726f777365725f747473223a747275652c2266616c6c6261636b223a2262726f77736572222c22726561736f6e223a22656467652d7474733a",
    "is_json_fallback": true,
    "json_if_fallback": {
      "use_browser_tts": true,
      "fallback": "browser",
      "reason": "edge-tts: No audio was received. Please verify that your parameters are correct.",
      "cached": false
    }
  }
}
```

#### `POST /api/stt` (Optional Whisper Fallback — Unused by Current Frontend)
- **Request:** `multipart/form-data` with `audio` (`UploadFile`, required; e.g. `audio/webm`) and `language` (Form string, default `"en"`, mapped `"hi-IN"` -> `"hi"` else `"en"`).
- **Response (`200 OK`):** `{"text": string, "provider": "groq-whisper"}`
- **Errors:** `503` if `GROQ_API_KEY` empty (`"GROQ_API_KEY not configured..."`); `400` if empty audio bytes (`"Empty audio upload"`); `502` on upstream Groq HTTP/network failure. *(Note: Not captured live with real WebM audio in Section 8.)*

---

### 3.3 SSE Streaming Contract (`/api/session/{id}/greeting` & `/api/chat`)

- **Endpoints:**
  - `POST /api/session/{session_id}/greeting` — No JSON body required (`stream_greeting()` ignores request body; frontend sends `POST` with empty body). Streams opening turn.
  - `POST /api/chat` — Request body `{"session_id": string (req), "text": string (req), "language"?: "en-IN"|"hi-IN"|"hinglish"|null (opt)}`. Note: if `text.strip()` is empty, generator returns immediately with **0 SSE events** (beware `completedStreams` won't increment!).
- **Transport Details:**
  - Uses `fetch(url, { method: "POST", headers: {"Content-Type": "application/json"}, body, signal: controller.signal })` with `response.body.getReader()` + `TextDecoder("utf-8")` — **NOT** `EventSource` (because `EventSource` only supports `GET` requests).
  - Server `_format_sse()` emits strict double-newline delimited frames:
    `event: <name>\ndata: <single-line JSON>\n\n` with `media_type="text/event-stream"` and headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
  - Client `readSseStream()` (`frontend/app.js:324-354`) splits chunks on `\n\n`, parses single `event:` and last `data:` line per block via `JSON.parse()`, and invokes `onEvent(eventName, dataJson)`.
- **Complete SSE Event Schema Catalog (`v1` — matches `docs/API_CONTRACT.md` & `tests/test_api_contract.py`):**

| Event Name | Speak vs UI | Payload Schema | When Emitted |
| :--- | :--- | :--- | :--- |
| `phase` | UI-only | `{"phase": PhaseEnum, "mode": CallMode}` | Start of `start_call()` and start of every non-empty `run_turn()` (emitted even if phase did not change). |
| `phase_change` | UI-only | `{"phase": PhaseEnum, "previous_phase": PhaseEnum, "mode": CallMode}` | *(Added in `v1`)* Immediately after `phase` **whenever `session.phase` transitions** (`session.phase != previous_phase`). Not emitted if phase stays identical (e.g., consecutive `CLARIFY` Qs). |
| `tool_start` | UI-only | `{"name": string, "arguments": object, "source": "server_preexec"\|"llm"\|"server_fallback"}` | *(Added in `v1`)* Emitted **before** executing each tool. For `server_preexec` phases (`DIAGNOSE`, `REVIEW_COMMITMENTS`, `RECOMMEND`, proactive/followup `GREETING`), strictly precedes spoken `token`/`sentence` events. |
| `tool_call` | UI-only | `{"name": string, "arguments": object, "source": "server_preexec"\|"llm"\|"server_fallback"}` | Legacy/compat event emitted alongside `tool_start` with identical payload. |
| `tool_result` | UI-only | `{"name": string, "result": object, "latency_ms": float, "source": "server_preexec"\|"llm"\|"server_fallback"}` | After each tool executes (`server_preexec` emitted in batch strictly before LLM streaming begins so no spoken text references un-emitted tools). |
| `failover` | UI-only | `{"type": "provider_failover", "from": "<provider>:<model>", "to": "<provider>:<model>", "reason_code": "rate_limited"\|"forbidden"\|"timeout"\|"server_error"}` | *(Sanitized in `v1`)* Emitted when `LLMRouter` fails over before any tokens stream. Zero raw upstream JSON, stack traces, org/project IDs, or keys. Deduplicated per session in `session.failover_events`. Note HTTP `403` permanently disables the model for the server lifetime; `429` places it into cooldown. |
| `token` | UI caption only | `{"text": string}` | Incremental raw text token from LLM (or full template/fallback string). Appended into `#captionsBox` (never sent to TTS). |
| `sentence` | **Audio (TTS)** (+ fallback text) | `{"text": string, "speakable": string}` | Emitted as soon as `_SENTENCE_BOUNDARY_RE` (`(.+?[.?!\u0964])(?:\s+\|$)`) matches or trailing buffer flushes (`!pending_tool_calls`). Frontend sends `data.speakable \|\| data.text` to `AudioPlaybackQueue.enqueueSentence()`. |
| `summary_card` | UI-only | `{"seller_name": str, "shop_name": str, "key_issues": str[], "commitments": Commitment[], "followup": {followup_date: str, success_targets: str[], scheduled?: bool}, "whatsapp_text": str}` | Immediately after `send_call_summary` `tool_result` (either LLM Round 0 or `SUMMARY` server fallback). |
| `latency` | UI telemetry | `{"llm_first_token_ms": int, "total_turn_ms": int, "llm_requests": int, "provider": string, "ungrounded_numbers": string[]}` | End of `_stream_agent_response` right before `done`. **(Not emitted on inbound template greeting!)** |
| `done` | Final UI sync | `{"text": str, "speakable"?: str, "phase": PhaseEnum, "provider": str, "commitments"?: Commitment[], "followup_schedule"?: object\|null, "summary_card"?: object\|null}` | Terminal event of successful stream. Note inbound template greeting `done` only sends `{"text", "phase", "provider": "template"}`. Frontend replaces live caption with `data.text` and increments `body.dataset.completedStreams`. |
| `error` | Error UI | `{"message": string}` | *(Sanitized in `v1`)* Emitted on `AllProvidersUnavailableError` (`"All configured LLM providers failed: <provider>:<model> (<reason_code>)"`) or unexpected server exception (no raw upstream JSON/org IDs/secrets; full detail kept in server logs). Stream closes without `latency` or `done`. |

- **Sentence Chunking, Delimiting, Ending & Abort Behaviour:**
  - Sentence regex (`app/agent/orchestrator.py:30`): `re.compile(r"(.+?[.?!\u0964])(?:\s+|$)", re.DOTALL)`. Each matched chunk passes `strip_reasoning_and_markdown()`, must match `[A-Za-z0-9\u0900-\u097F]`, and runs `to_speakable_text(sent, session.language)`. Remaining buffer flushes on stream EOF iff `!pending_tool_calls`.
  - If Round 1 finishes with empty `cleaned_reply` (`""`), backend synthesizes a phase-appropriate fallback string and emits one `token` + one `sentence`.
  - **Client Abort:** Calling `handleInterrupt()`, `endCall()`, or starting a new turn calls `state.chatAbortController.abort()`, cancelling the active `fetch()` SSE stream cleanly (`err.name === "AbortError"` ignored in UI) while `AudioPlaybackQueue.stopAll()` aborts in-flight `/api/tts` fetches.

- **Typical Event Orders (`v1` By Turn Type):**
  1. **Inbound Template Greeting:** `phase` -> `token` (full text) -> `sentence` -> `done` (`provider: "template"`, no `latency`).
  2. **Proactive / Follow-up Greeting:** `phase` -> `tool_start`+`tool_call` (x2 `server_preexec`) -> `tool_result` (x2) -> [`failover`?] -> (`token`... interleaved with `sentence`...) -> `latency` -> `done`.
  3. **Pre-Executed Analysis (`DIAGNOSE` / `REVIEW_COMMITMENTS` / `RECOMMEND`):** `phase` -> `phase_change` (when transitioning) -> all `tool_start`+`tool_call` (`server_preexec`) -> all `tool_result` -> [`failover`?] -> interleaved `token` & `sentence` -> `latency` -> `done`. *(Strict 1 LLM request/turn; all tool results precede any spoken text.)*
  4. **Tool-Calling Action (`CLARIFY` / `COMMIT` / `SUMMARY`):** `phase` -> [`phase_change`?] -> Round 0 [`failover`?] -> `tool_start`+`tool_call` (`llm`) -> `tool_result` -> [`summary_card`?] -> Round 1 interleaved `token` & `sentence` -> [`server_fallback` `send_call_summary` if missing in `SUMMARY`] -> `latency` -> `done`.
  - **Post-v1 Sanitized `failover` & `error` Sample Shapes (`tests/test_api_contract.py`):**
    ```json
    {"failover": {"type": "provider_failover", "from": "groq:qwen/qwen3.8-27b", "to": "groq:openai/gpt-oss-20b", "reason_code": "forbidden"}, "error": {"message": "All configured LLM providers failed: groq:openai/gpt-oss-120b (timeout) | gemini:gemini-3.5-flash-lite (server_error)"}}
    ```

- **Real Captured SSE Sequences:**
  1. *Inbound Template Greeting (`docs/captures/inbound_call.json`):*
```json
[
  {
    "event": "phase",
    "data": {
      "phase": "GREETING",
      "mode": "inbound"
    }
  },
  {
    "event": "token",
    "data": {
      "text": "Namaste Rajesh ji, I am Riya, your Snapdeal account manager. How can I help Kumar Home and Kitchen today?"
    }
  },
  {
    "event": "sentence",
    "data": {
      "text": "Namaste Rajesh ji, I am Riya, your Snapdeal account manager. How can I help Kumar Home and Kitchen today?",
      "speakable": "Namaste Rajesh ji, I am Riya, your Snapdeal account manager. How can I help Kumar Home and Kitchen today?"
    }
  },
  {
    "event": "done",
    "data": {
      "text": "Namaste Rajesh ji, I am Riya, your Snapdeal account manager. How can I help Kumar Home and Kitchen today?",
      "phase": "GREETING",
      "provider": "template"
    }
  }
]
```
  2. *DIAGNOSE Pre-Executed Turn (`docs/captures/inbound_call.json` Turn 2, `token` frames omitted for brevity — see `docs/captures/inbound_call.json` for all token frames):*
```json
[
  {
    "event": "phase",
    "data": {
      "phase": "DIAGNOSE",
      "mode": "inbound"
    }
  },
  {
    "event": "tool_call",
    "data": {
      "name": "run_diagnosis",
      "arguments": {},
      "source": "server_preexec"
    }
  },
  {
    "event": "tool_call",
    "data": {
      "name": "get_out_of_stock_skus",
      "arguments": {
        "limit": 5
      },
      "source": "server_preexec"
    }
  },
  {
    "event": "tool_call",
    "data": {
      "name": "get_cancellation_breakdown",
      "arguments": {},
      "source": "server_preexec"
    }
  },
  {
    "event": "tool_call",
    "data": {
      "name": "get_pricing_gaps",
      "arguments": {},
      "source": "server_preexec"
    }
  },
  {
    "event": "tool_call",
    "data": {
      "name": "get_visibility_funnel",
      "arguments": {},
      "source": "server_preexec"
    }
  },
  {
    "event": "tool_result",
    "data": {
      "name": "run_diagnosis",
      "result": {
        "snapshot": "current",
        "problem_type": "volume_driven_decline",
        "total_lost_orders": 130,
        "total_lost_revenue": 65000,
        "asp_flat": true,
        "drivers": [
          {
            "rank": 1,
            "driver": "stock_outs",
            "title": "Out-of-Stock Top Sellers",
            "lost_orders": 78,
            "lost_revenue": 39000,
            "share_pct": 60.0,
            "evidence": "Out-of-stock products jumped from 5 to 18 (+13 new). Top 5 newly OOS products alone lost 68 orders."
          },
          {
            "rank": 2,
            "driver": "pricing_gaps",
            "title": "Competitor Price Gaps on Top SKUs",
            "lost_orders": 26,
            "lost_revenue": 13000,
            "share_pct": 20.0,
            "evidence": "3 active high-volume products (Electric Kettle, Dish Rack, Spatula Set) are priced 6.9% to 8.3% higher than the lowest marketplace price."
          },
          {
            "rank": 3,
            "driver": "cancellations_visibility",
            "title": "Cancellations & Search Visibility Drag",
            "lost_orders": 26,
            "lost_revenue": 13000,
            "share_pct": 20.0,
            "evidence": "Cancellation rate spiked from 8.0% to 14.0% (seller stock-unavailable 6 -> 14, dispatch SLA 5 -> 9), and search impressions dipped 14.0% as OOS listings lost rank."
          }
        ],
        "already_answered_questions": [
          "Was average order value / ASP lower? No, ASP stayed flat at Rs 500.",
          "Was buyer cancellation driving the spike? No, buyer-initiated cancellation rate stayed flat at ~4%; seller-side stock and dispatch SLA drove it.",
          "Are all products down equally? No, 60% of the drop is concentrated in newly out-of-stock products."
        ],
        "assumptions": [
          "Orders = placed orders; Sales = placed GMV (Orders x SKU Price).",
          "Cancellation rate = cancelled orders / placed orders.",
          "Causal attributions and recovery estimates are indicative mock model estimates, not guarantees."
        ]
      },
      "latency_ms": 0.98
    }
  },
  {
    "event": "tool_result",
    "data": {
      "name": "get_out_of_stock_skus",
      "result": "<see Section 6 for full schema & real captured payload>",
      "latency_ms": 0.94
    }
  },
  {
    "event": "tool_result",
    "data": {
      "name": "get_cancellation_breakdown",
      "result": "<see Section 6 for full schema & real captured payload>",
      "latency_ms": 0.93
    }
  },
  {
    "event": "tool_result",
    "data": {
      "name": "get_pricing_gaps",
      "result": "<see Section 6 for full schema & real captured payload>",
      "latency_ms": 0.41
    }
  },
  {
    "event": "tool_result",
    "data": {
      "name": "get_visibility_funnel",
      "result": "<see Section 6 for full schema & real captured payload>",
      "latency_ms": 0.38
    }
  },
  {
    "event": "sentence",
    "data": {
      "text": "ASP stayed flat at Rs 500, and 60% of the 130 order drop came from 18 out of stock products like the 5L Pressure Cooker.",
      "speakable": "average order price stayed flat at five hundred rupees and sixty percent of the 130 order drop came from 18 out of stock products like the 5L Pressure Cooker."
    }
  },
  {
    "event": "sentence",
    "data": {
      "text": "The remaining loss is due to 14% cancellations and three overpriced SKUs; are you ready for the action plan?",
      "speakable": "The remaining loss is due to fourteen percent cancellations and three overpriced products; are you ready for the action plan?"
    }
  },
  {
    "event": "latency",
    "data": {
      "llm_first_token_ms": 490,
      "total_turn_ms": 618,
      "llm_requests": 1,
      "provider": "groq:openai/gpt-oss-120b",
      "ungrounded_numbers": []
    }
  },
  {
    "event": "done",
    "data": {
      "text": "ASP stayed flat at Rs 500, and 60% of the 130 order drop came from 18 out of stock products like the 5L Pressure Cooker. The remaining loss is due to 14% cancellations and three overpriced SKUs; are you ready for the action plan?",
      "speakable": "average order price stayed flat at five hundred rupees and sixty percent of the 130 order drop came from 18 out of stock products like the 5L Pressure Cooker. The remaining loss is due to fourteen percent cancellations and three overpriced products; are you ready for the action plan?",
      "phase": "DIAGNOSE",
      "provider": "groq:openai/gpt-oss-120b",
      "commitments": [],
      "followup_schedule": null,
      "summary_card": null
    }
  }
]
```
  3. *Live Failover + Token/Sentence Stream (`docs/captures/failover_turn.json`):* — See full verbatim capture in Section 8.3!
  4. *Follow-Up Turn 3 with `schedule_followup` + `send_call_summary` (`docs/captures/followup_call.json` Turn 3, `token` omitted):*
```json
[
  {
    "event": "phase",
    "data": {
      "phase": "SUMMARY",
      "mode": "followup"
    }
  },
  {
    "event": "tool_call",
    "data": {
      "name": "schedule_followup",
      "arguments": {
        "success_targets": [
          "Out-of-stock products <= 8",
          "Cancellation rate <= 10%"
        ],
        "followup_date": "In 3 days at 11 AM"
      },
      "source": "llm"
    }
  },
  {
    "event": "tool_call",
    "data": {
      "name": "send_call_summary",
      "arguments": {
        "key_issues": [
          "18 out-of-stock products dropping to 9",
          "High cancellation rate of 12.5% improving to 10.5%",
          "Uncompetitive pricing on HK-021"
        ]
      },
      "source": "llm"
    }
  },
  {
    "event": "tool_result",
    "data": {
      "name": "schedule_followup",
      "result": {
        "followup_date": "In 3 days at 11 AM",
        "success_targets": [
          "Out-of-stock products <= 8",
          "Cancellation rate <= 10%"
        ],
        "scheduled": true
      },
      "latency_ms": 0.96
    }
  },
  {
    "event": "tool_result",
    "data": {
      "name": "send_call_summary",
      "result": {
        "sent": true,
        "summary_card": {
          "seller_name": "Rajesh Kumar",
          "shop_name": "Kumar Home & Kitchen",
          "key_issues": [
            "18 out-of-stock products dropping to 9",
            "High cancellation rate of 12.5% improving to 10.5%",
            "Uncompetitive pricing on HK-021"
          ],
          "commitments": [
            {
              "action_id": "ACT-2",
              "action_summary": "Restock HK-009 within 48 hours and reprice HK-021 today",
              "owner": "Rajesh Kumar",
              "due_date": "Within 48 hours",
              "target_metric": "Out-of-stock products <= 8",
              "status": "committed"
            }
          ],
          "followup": {
            "followup_date": "In 3 days at 11 AM",
            "success_targets": [
              "Out-of-stock products <= 8",
              "Cancellation rate <= 10%"
            ],
            "scheduled": true
          },
          "whatsapp_text": "*Snapdeal Virtual KAM — 3-Day Follow-Up Summary (Prototype)*\nSeller: Rajesh Kumar (Kumar Home & Kitchen)\nDate: 10 Nov 2026\n\n*Key Findings:*\n- 18 out-of-stock products dropping to 9\n- High cancellation rate of 12.5% improving to 10.5%\n- Uncompetitive pricing on HK-021\n\n*Agreed Action Plan:*\n1. Restock HK-009 within 48 hours and reprice HK-021 today | Owner: Rajesh Kumar | Due: Within 48 hours\n\n*Next Follow-up Call:* In 3 days at 11 AM\n*Success Targets:* Out-of-stock products <= 8; Cancellation rate <= 10%\n\n_Prototype mock data summary — Riya, Virtual KAM_"
        }
      },
      "latency_ms": 0.94
    }
  },
  {
    "event": "summary_card",
    "data": {
      "seller_name": "Rajesh Kumar",
      "shop_name": "Kumar Home & Kitchen",
      "key_issues": [
        "18 out-of-stock products dropping to 9",
        "High cancellation rate of 12.5% improving to 10.5%",
        "Uncompetitive pricing on HK-021"
      ],
      "commitments": [
        {
          "action_id": "ACT-2",
          "action_summary": "Restock HK-009 within 48 hours and reprice HK-021 today",
          "owner": "Rajesh Kumar",
          "due_date": "Within 48 hours",
          "target_metric": "Out-of-stock products <= 8",
          "status": "committed"
        }
      ],
      "followup": {
        "followup_date": "In 3 days at 11 AM",
        "success_targets": [
          "Out-of-stock products <= 8",
          "Cancellation rate <= 10%"
        ],
        "scheduled": true
      },
      "whatsapp_text": "*Snapdeal Virtual KAM — 3-Day Follow-Up Summary (Prototype)*\nSeller: Rajesh Kumar (Kumar Home & Kitchen)\nDate: 10 Nov 2026\n\n*Key Findings:*\n- 18 out-of-stock products dropping to 9\n- High cancellation rate of 12.5% improving to 10.5%\n- Uncompetitive pricing on HK-021\n\n*Agreed Action Plan:*\n1. Restock HK-009 within 48 hours and reprice HK-021 today | Owner: Rajesh Kumar | Due: Within 48 hours\n\n*Next Follow-up Call:* In 3 days at 11 AM\n*Success Targets:* Out-of-stock products <= 8; Cancellation rate <= 10%\n\n_Prototype mock data summary — Riya, Virtual KAM_"
    }
  },
  {
    "event": "sentence",
    "data": {
      "text": "Rajesh ji, I have sent the WhatsApp summary card with your key issues and action items.",
      "speakable": "Rajesh ji, I have sent the WhatsApp summary card with your key issues and action items."
    }
  },
  {
    "event": "sentence",
    "data": {
      "text": "We will review your progress on out-of-stock items and cancellations in three days at eleven in the morning.",
      "speakable": "We will review your progress on out-of-stock items and cancellations in three days at eleven in the morning."
    }
  },
  {
    "event": "sentence",
    "data": {
      "text": "Thank you and have a wonderful day!",
      "speakable": "Thank you and have a wonderful day!"
    }
  },
  {
    "event": "latency",
    "data": {
      "llm_first_token_ms": 2382,
      "total_turn_ms": 2575,
      "llm_requests": 2,
      "provider": "gemini:gemini-3.5-flash-lite",
      "ungrounded_numbers": []
    }
  },
  {
    "event": "done",
    "data": {
      "text": "Rajesh ji, I have sent the WhatsApp summary card with your key issues and action items. We will review your progress on out-of-stock items and cancellations in three days at eleven in the morning. Thank you and have a wonderful day!",
      "speakable": "Rajesh ji, I have sent the WhatsApp summary card with your key issues and action items. We will review your progress on out-of-stock items and cancellations in three days at eleven in the morning. Thank you and have a wonderful day!",
      "phase": "SUMMARY",
      "provider": "gemini:gemini-3.5-flash-lite",
      "commitments": [
        {
          "action_id": "ACT-2",
          "action_summary": "Restock HK-009 within 48 hours and reprice HK-021 today",
          "owner": "Rajesh Kumar",
          "due_date": "Within 48 hours",
          "target_metric": "Out-of-stock products <= 8",
          "status": "committed"
        }
      ],
      "followup_schedule": {
        "followup_date": "In 3 days at 11 AM",
        "success_targets": [
          "Out-of-stock products <= 8",
          "Cancellation rate <= 10%"
        ],
        "scheduled": true
      },
      "summary_card": {
        "seller_name": "Rajesh Kumar",
        "shop_name": "Kumar Home & Kitchen",
        "key_issues": [
          "18 out-of-stock products dropping to 9",
          "High cancellation rate of 12.5% improving to 10.5%",
          "Uncompetitive pricing on HK-021"
        ],
        "commitments": [
          {
            "action_id": "ACT-2",
            "action_summary": "Restock HK-009 within 48 hours and reprice HK-021 today",
            "owner": "Rajesh Kumar",
            "due_date": "Within 48 hours",
            "target_metric": "Out-of-stock products <= 8",
            "status": "committed"
          }
        ],
        "followup": {
          "followup_date": "In 3 days at 11 AM",
          "success_targets": [
            "Out-of-stock products <= 8",
            "Cancellation rate <= 10%"
          ],
          "scheduled": true
        },
        "whatsapp_text": "*Snapdeal Virtual KAM — 3-Day Follow-Up Summary (Prototype)*\nSeller: Rajesh Kumar (Kumar Home & Kitchen)\nDate: 10 Nov 2026\n\n*Key Findings:*\n- 18 out-of-stock products dropping to 9\n- High cancellation rate of 12.5% improving to 10.5%\n- Uncompetitive pricing on HK-021\n\n*Agreed Action Plan:*\n1. Restock HK-009 within 48 hours and reprice HK-021 today | Owner: Rajesh Kumar | Due: Within 48 hours\n\n*Next Follow-up Call:* In 3 days at 11 AM\n*Success Targets:* Out-of-stock products <= 8; Cancellation rate <= 10%\n\n_Prototype mock data summary — Riya, Virtual KAM_"
      }
    }
  }
]
```
  5. *All Providers Failed `error` Event (`docs/captures/error_all_providers_failed.json`):*
```json
[
  {
    "event": "phase",
    "data": {
      "phase": "SUMMARY",
      "mode": "inbound"
    }
  },
  {
    "event": "error",
    "data": {
      "message": "All configured LLM providers failed: groq:openai/gpt-oss-120b: groq (openai/gpt-oss-120b) HTTP 429 rate limited: {\"error\":{\"message\":\"Rate limit reached for model `openai/gpt-oss-120b` in organization `<REDACTED>` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 6843, Req | groq:qwen/qwen3.8-27b: groq (qwen/qwen3.8-27b) HTTP 403 blocked: {\"error\":{\"message\":\"The model `qwen/qwen3.8-27b` is blocked at the project level. Please have a project admin enable this model in the project settings at https://console.groq.com/settings/project/li | gemini:gemini-3.5-flash-lite: gemini (gemini-3.5-flash-lite) error: 503 Service Unavailable. {'message': '{\\n  \"error\": {\\n    \"code\": 503,\\n    \"message\": \"This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.\",\\n    \"status\": \"UNAVAILABLE\"\\n  }\\n}\\n', 'sta | gemini:gemini-3.6-flash: gemini (gemini-3.6-flash) timed out after 12.0s"
    }
  }
]
```


---

## 4. Voice-loop behaviour spec

### 4.1 UI States (`state.orbState`) & Transitions
The UI maintains `state.inCall` (`boolean`) and `state.orbState` (`"idle" | "listening" | "thinking" | "speaking"`). There are **no** separate `"ringing"` or `"error"` orb states: the synthesized ringtone plays overlapped with the initial `"thinking"` state during greeting fetch, and errors show `#errorBanner` while falling back either to `"idle"` (if `/api/session/start` fails) or `"listening"` (if an in-call turn fails).

| Current State | Trigger | Next State | Side Effects (Exact Code Behaviour) |
| :--- | :--- | :--- | :--- |
| `idle` | Click `#btnStartCall`, `#micOrb`, `#btnFollowup`, or `sendUserUtterance()` while `!inCall` | `thinking` | Sets `inCall=true`, `didSilenceReprompt=false`, resets `#captionsBox`, hides `#summaryCardSection`, disables `#btnStartCall`, enables `#btnEndCall`, plays 2-pulse ringtone (`playRingtone()`), starts 500ms interval `#callTimer`, `POST /api/session/start` -> `POST /api/session/{id}/greeting`. |
| `thinking` | First queued sentence ready to play -> `AudioPlaybackQueue._drainQueue()` fires `onSpeakingStart` | `speaking` | Cancels 600ms filler timer (`clearLatencyFiller()`), sets `#micOrb[data-state="speaking"]`, enables `#btnInterrupt`. If `!state.headphonesMode`, calls `stopRecognition()`. |
| `thinking` | Greeting or chat `done` arrives AND `!audioQueue.isPlaying && audioQueue.queue.length === 0 && state.inCall` | `listening` | `audioQueue.clearLatencyFiller()`, calls `startListening()`. *(Safety net for empty replies or 0 sentences.)* |
| `thinking` | SSE `error` event OR fetch `catch` (non-`AbortError`) while `state.inCall` | `listening` | `clearLatencyFiller()`, `showError(msg)`, calls `startListening()`. *(If `/api/session/start` failed, calls `endCall()` -> `idle`.)* |
| `speaking` | Last sentence finishes playing -> `_drainQueue()` fires `onSpeakingEnd` (with `state.inCall && state.orbState !== "thinking"`) | `listening` | Disables `#btnInterrupt`, calls `startListening()`. |
| `speaking` | Click `#btnInterrupt`, `#micOrb`, press `Spacebar` (outside `INPUT`/`SELECT`), or `rec.onspeechstart` in `headphonesMode` | `listening` | Calls `handleInterrupt()`: `audioQueue.stopAll()`, `chatAbortController.abort()`, disables/resets playback, calls `startListening()`. |
| `listening` | 800ms EOU timer fires with non-empty `(finalTranscript + " " + interimTranscript).trim()` OR user sends text via `#btnSendDebug`/`Enter` | `thinking` | Stops `SpeechRecognition` (`stopRecognition()`), calls `audioQueue.stopAll()`, resets `didSilenceReprompt=false`, records `sttEndMs = performance.now()`, schedules `audioQueue.scheduleLatencyFiller(600)`, `POST /api/chat`. |
| `listening` | `silencePromptTimer` (6000ms) fires once (`!didSilenceReprompt`) | `speaking` (-> `listening`) | Sets `didSilenceReprompt=true`, appends KAM caption, calls `audioQueue.enqueueSentence(prompt)` (which triggers `speaking`, pauses half-duplex mic, then returns to `listening` on end). |
| Any (`inCall`) | Click `#btnEndCall` or `endCall()` on fatal start failure | `idle` | Sets `inCall=false`, clears call timer, `stopRecognition()`, `audioQueue.stopAll()`, aborts `chatAbortController`, disables `#btnEndCall` & `#btnInterrupt`, enables `#btnStartCall`. |

### 4.2 `SpeechRecognition` Configuration, EOU, Re-Prompt & Chrome Quirks (`frontend/app.js:174-312`)
- **Constructor Detection:** `window.SpeechRecognition || window.webkitSpeechRecognition || null`. Missing -> unhides `#browserBanner` on `init()` and inside `startListening()` if somehow null.
- **Exact Settings Per Utterance:**
  - `rec.lang = state.language === "hi-IN" ? "hi-IN" : "en-IN"` *(Notice **Hinglish uses `"en-IN"`** STT recognition while sending `"hinglish"` to the backend LLM!)*
  - `rec.interimResults = true`
  - `rec.continuous = true`
  - `rec.maxAlternatives = 1`
- **End-of-Utterance (EOU) Timing (`800 ms`):**
  - Inside `rec.onresult`: immediately clears `silencePromptTimer`, merges all results from `event.resultIndex` to `event.results.length`, appending `isFinal` text into `state.finalTranscript` (`+ " "`) and overwriting `state.interimTranscript` with latest non-final chunk.
  - Updates `#seller-live-caption` in `#captionsBox` (marked `.interim`).
  - Resets `state.eouTimer = setTimeout(..., 800)`. When 800ms elapse without another `onresult`, combines `(state.finalTranscript + " " + state.interimTranscript).trim()` (so even unfinalized interim text still submits promptly!), strips `id="seller-live-caption"` & `.interim`, calls `stopRecognition()`, and calls `sendUserUtterance(toSend)`.
- **6-Second Silence Re-Prompt (`6000 ms`):**
  - `scheduleSilenceReprompt()` sets `state.silencePromptTimer` for **`6000 ms`** iff `state.inCall && !state.didSilenceReprompt`.
  - Guarded inside callback: `if (!state.inCall || state.orbState !== "listening" || state.didSilenceReprompt) return;` -> sets `didSilenceReprompt = true`.
  - Exact localized re-prompts:
    - `hi-IN`: `"राजेश जी, क्या आप सुन रहे हैं? आप बोल सकते हैं।"`
    - `hinglish`: `"Rajesh ji, aap line par hain? Aap bata sakte hain."`
    - `en-IN` (default): `"Rajesh ji, are you still there? I am listening whenever you are ready."`
  - Reset: `didSilenceReprompt` resets to `false` on `startCall()` and on `sendUserUtterance()`, so each conversational turn allows up to 1 gentle silence prompt.
- **Low-Confidence Handling (`< 0.45`):**
  - Checked on `res.isFinal`: `typeof alt.confidence === "number" && alt.confidence > 0 && alt.confidence < 0.45`. Note `alt.confidence > 0` explicitly avoids false positives when browsers report `0` for unavailable confidence!
  - Behaviour: **Visual-only** — appends `" (confirming...)"` to the interim `#captionsBox` bubble. Wait: when the 800ms timer commits `toSend`, it reads `(state.finalTranscript + " " + state.interimTranscript).trim()`, so it **does NOT ask for confirmation or append `(confirming...)` to the API payload**.
- **Restart-on-End Behaviour (`180 ms`) & Chrome Quirks Handled:**
  - Chrome's `continuous = true` Web Speech recognizer silently fires `onend` after extended silence (~60s) or transient network hiccups, or throws if `rec.start()` is called while shutting down.
  - `rec.onend` sets `state.recognizing = false`, then checks `if (state.inCall && (state.orbState === "listening" || state.headphonesMode))`, waits **`180 ms`** via `setTimeout`, re-checks `!state.recognizing`, and calls `rec.start()` inside `try/catch`.
  - `rec.onerror`: resets `state.recognizing = false`; on `"not-allowed"` or `"service-not-allowed"` shows `#errorBanner`: `"Microphone permission denied. Enable mic access or use the Debug Text box in Settings."`
  - Changing `#selLanguage` mid-listening calls `stopRecognition()` -> `startListening()` to re-instantiate with the updated `rec.lang`, plus fires fire-and-forget `POST /api/session/{id}/settings`.

### 4.3 Half-Duplex vs Headphones Barge-In & Interruption Cancellation
- **Half-Duplex Mode (`headphonesMode === false`, default):**
  - On `onSpeakingStart`: calls `stopRecognition()` so Riya's speaker output cannot trigger self-echo into the mic.
  - User interrupts manually via:
    1. Clicking `#btnInterrupt`
    2. Clicking `#micOrb` while `state.orbState === "speaking"`
    3. Pressing `Spacebar` (`ev.code === "Space"` when `document.activeElement?.tagName` is not `"INPUT"` or `"SELECT"` and `state.inCall && state.orbState === "speaking"`).
- **Headphones Mode (`headphonesMode === true` via `#chkHeadphones`):**
  - On `onSpeakingStart`, recognition is **NOT** stopped (and `rec.onend` keeps restarting it even when `orbState === "speaking"`).
  - Inside `rec.onspeechstart`: `if (state.headphonesMode && audioQueue.isPlaying) handleInterrupt();`.
- **Exact Cancellation Sequence (`handleInterrupt()` & `AudioPlaybackQueue.stopAll()`):**
  1. `audioQueue.stopAll()`:
     - `clearLatencyFiller()` (`clearTimeout(this.fillerTimer)`)
     - `this._generation++` (invalidates any active `_drainQueue()` loop)
     - `this.aborted = true`
     - `this.queue = []`
     - Calls `.abort()` on every `AbortController` in `this.abortControllers` (cancelling pending `/api/tts` fetches) and clears the set
     - If `this.currentAudio`: `pause()`, `currentTime = 0`, `this.currentAudio = null`
     - Invokes `this._currentResolve()` (unblocking awaiting `_playBlob` / `_speakBrowser` promise)
     - Calls `window.speechSynthesis?.cancel()`
     - Sets `this.isPlaying = false` *(Note: `stopAll()` does NOT invoke `onSpeakingEnd`; the caller calls `startListening()` or starts the next turn!)*
  2. If `state.chatAbortController`: calls `.abort()`, sets `null` (aborts SSE `fetch()` stream cleanly).
  3. Calls `startListening()` (switches orb to `"listening"`, enables mic, schedules 6s silence re-prompt).

### 4.4 Audio Queue, Sentence Pipelining, Ringtone & 600ms Filler (`frontend/audio.js`)
- **Unbounded FIFO Parallel Prefetch (`enqueueSentence`):**
  - Immediately when SSE `sentence` arrives, clears latency filler, sets `aborted = false`, creates an `AbortController`, and **immediately starts `fetch("/api/tts")` (`_fetchTtsAudio`)** without waiting for previous sentences to finish playing (`N+1` downloads in parallel while `N` plays).
  - `_drainQueue()` pulls items strictly in FIFO order (`this.queue.shift()`), `await`s `item.fetchPromise`, guards `if (this.aborted || myGen !== this._generation) break;`, invokes `onFirstAudioCallback()` (records client `#latencySttToAudio` on first callback), updates `#ttsBadge` via `onTtsProviderUsed(audioRes.provider)`, then awaits `_playBlob(audioRes.blob)` (`URL.createObjectURL` + `new Audio(url)` + `URL.revokeObjectURL`) or `_speakBrowser(item.text)`.
- **Autoplay Unlock & Ringtone (`playRingtone()`):**
  - Called synchronously inside `startCall()` user gesture handler (`click`) before `await fetch("/api/session/start")` so Web Audio runs unlocked.
  - Creates `AudioContext`, plays 2 `440 Hz` sine tones (`now + 0.0s` and `now + 0.35s`, gain ramp `0.0001 -> 0.06` in 30ms -> `0.0001` at `0.25s`, stops at `0.26s`), closes context at `900 ms`. Swallows errors if blocked/unsupported.
- **Latency Filler (`scheduleLatencyFiller(600)`):**
  - Triggered at the start of `sendUserUtterance()` right before `POST /api/chat`.
  - After **`600 ms`**, checks `if (this.isPlaying || this.queue.length > 0) return;` then speaks localized filler directly via **browser `window.speechSynthesis`** (`_speakBrowser(fillerText, { isFiller: true })` — zero server TTS fetch!):
    - `hi-IN`: `"जी, एक सेकंड, मैं आपका डेटा देख रही हूँ।"`
    - `hinglish`: `"Ji, ek second, main aapka data dekh rahi hoon."`
    - `en-IN`: `"One moment Rajesh ji, checking your account data."`
  - **Cancellation:** `clearLatencyFiller()` clears the timeout immediately when ANY `sentence` event arrives, when `done` arrives with empty queue, on `error`, or in `stopAll()`. Note: `speechSynthesis.cancel()` is called before speaking each `_speakBrowser` utterance and inside `stopAll()` *(see Section 7 regarding late MP3 vs already-started filler overlap!)*.

### 4.5 Language & Voice-Engine Mapping Table
| UI `<option>` Value | Web Speech `rec.lang` | `/api/stt` Hint | Edge-TTS Neural Voice | Browser `utter.lang` | Speakable Currency Word | Prompt Style |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `"en-IN"` | `"en-IN"` | `"en"` | `"en-IN-NeerjaNeural"` | `"en-IN"` | `"rupees"` | Indian English (`"Rajesh ji"`) |
| `"hinglish"` | `"en-IN"` | `"en"` | `"en-IN-NeerjaNeural"` | `"en-IN"` | `"rupaye"` | Latin Hinglish (`"Haan Rajesh ji..."`) |
| `"hi-IN"` | `"hi-IN"` | `"hi"` | `"hi-IN-SwaraNeural"` | `"hi-IN"` | `"rupaye"` | Devanagari Hindi + simple English biz words |

Browser `speechSynthesis` selects the first voice matching `utter.lang.toLowerCase()`, falling back to `en-in`, or browser default (`utter.rate = 1.03`).

### 4.6 Critical Invariants (MUST Preserve in Any Frontend Rewrite)
1. **Use `fetch()` + `ReadableStream` for SSE**, never `EventSource` (both `/greeting` and `/chat` are `POST`).
2. **Treat HTTP `200` + `Content-Type: application/json` from `/api/tts` as browser `speechSynthesis` fallback**, not as corrupt audio or an HTTP error.
3. **Always `audioQueue.stopAll()` BEFORE cancelling `chatAbortController` and starting `SpeechRecognition`** during interruption or new turn submission to guarantee zero echo self-triggering and zero orphaned TTS fetches (`_generation` token + `AbortController`).
4. **In Half-Duplex mode (`!headphonesMode`), stop `SpeechRecognition` on `onSpeakingStart` and only restart on `onSpeakingEnd` / `done` (empty queue) / `handleInterrupt()`**.
5. **Emit audio from `sentence` events (`data.speakable || data.text`), NOT raw `token` events**, while updating live visible captions from `token` and finalizing displayed text with `done.text`.
6. **Reset `body.dataset.completedStreams` incrementally on every `done` SSE event** and initialize `#latencySttToAudio` to literal `"-"` (ASCII hyphen) if compatibility with existing Playwright E2E checks is desired (see Section 5).
7. **Always run uvicorn with `--workers 1`** unless `SessionStore`/`TTSService`/`LLMRouter` are externalized; preserve `session_id` in client memory across turns and pass `prior_session_id` when starting follow-up calls.
8. **Send `language` on `/api/session/start`, `/api/chat`, and `/api/session/{id}/settings`** cleanly so mid-call language switches persist across SSE failover and summary generation.


---

## 5. DOM and state contract

> Any redesign reusing `tests/test_walking_skeleton_ui.py` and `tests/test_full_ui.py` (or maintaining parity with the existing controllers) must respect these exact selectors, `data-*` attributes, and text contracts.

### 5.1 Element IDs, Classes & `data-*` Attributes Contract

| Selector / Attribute | Mutated / Read By | Exact Value / Behaviour Required |
| :--- | :--- | :--- |
| `body[data-completed-streams]` | `app.js:511` writes; Playwright reads | Initial `"0"`. Incremented as string (`"1"`, `"2"`, ...) on every SSE `done` event. **Playwright E2E polls `Number(document.body.dataset.completedStreams)`!** |
| `header` | Static HTML; Playwright checks | Must contain case-insensitive `"prototype"` text (e.g. `PROTOTYPE · MOCK DATA ONLY`). |
| `#browserBanner` | `app.js:217,548` toggles `.hidden` | Shown when `!window.SpeechRecognition && !window.webkitSpeechRecognition`. |
| `#errorBanner`, `#errorBannerText` | `app.js:101-109`; Playwright checks on failure | Displays `data.message` / network error; dismissed via `.hidden`. |
| `#btnStartCall`, `#btnEndCall`, `#btnInterrupt`, `#btnFollowup` | `app.js` listeners + `disabled` states; Playwright clicks `#btnStartCall`, `#btnFollowup`, `#btnEndCall` | Start/Followup trigger `startCall()`; Interrupt enabled only while `orbState === "speaking"`; End Call enabled only while `inCall`. |
| `#callTimer` | `app.js:165` writes `MM:SS` | Updated every `500ms` during active call. |
| `#micOrb[data-state]` | `app.js:78-87,582` writes/reads; Playwright asserts `data-state="idle"` | `data-state` in `{"idle","listening","thinking","speaking"}`. **Invariant in current JS:** `el.micOrb.textContent = labels[next]` overwrites children, so keep `#micOrb` leaf text-only! Clicking starts call or interrupts speaking. |
| `#orbLabel` | `app.js:88-98` writes | Status subtitle synced to `orbState` + `headphonesMode`. |
| `#captionsBox` | `app.js:111-130,375`; Playwright waits `#captionsBox .caption-line.kam` & asserts `inner_text` | Appends `<div class="caption-line {kam\|seller}[ interim]" [id="seller-live-caption"\|"kam-live-..."]>` with `.captions-empty` initial placeholder, `.caption-who`, and `.caption-body`. Cleared on `startCall()`. |
| `#selMode` (`inbound`\|`proactive`\|`followup`), `#selLanguage` (`en-IN`\|`hinglish`\|`hi-IN`), `#selTtsProvider` (`auto`\|`edge-tts`\|`browser`), `#chkHeadphones` | `app.js` reads/listens; Playwright `select_option("#selMode", "proactive")` | Setting overrides call mode/language/TTS/barge-in. Note Playwright expects `#selMode` value `"proactive"` to be selectable. |
| `#btnToggleDebug`, `#debugBox`, `#txtDebugInput`, `#btnSendDebug` | `app.js:614-629`; Playwright clicks `#btnToggleDebug`, fills `#txtDebugInput`, clicks `#btnSendDebug` | `#debugBox` starts `.hidden`; toggle shows it; `Enter` triggers `#btnSendDebug.click()`. |
| `#phaseStrip` -> `.phase-pill[data-phase]` | `app.js:133-156` toggles `.active`, `.done`, `.hidden` | Must have pills for `GREETING`, `UNDERSTAND`, `CLARIFY`, `REVIEW_COMMITMENTS`, `DIAGNOSE`, `RECOMMEND`, `COMMIT`, `SUMMARY`, `CLOSE`. |
| `#ttsBadge` | `app.js:64` writes `TTS: {provider}` | Initial `"TTS: Auto"`. Updated on every sentence playback (`TTS: edge-tts` or `TTS: browser`). |
| `#latencySttToToken`, `#latencySttToAudio`, `#latencyProvider` | `app.js:487,495,502`; **Playwright asserts** `!== "-"` | **MUST initialize text to ASCII `"-"`** (`test_walking_skeleton_ui.py:117,125-126` waits for `#latencySttToAudio.innerText !== '-'`). Format: `"{ms} ms"` and `"<provider>:<model>"`. |
| `#kamBrainRoot` | `kam_brain.js` writes `innerHTML`; **Playwright asserts visible `inner_text`** | Must contain `.kb-table` (`Sales (GMV)`), `"Root-Cause Revenue Attribution"`, `"Prioritised Recovery Plan"`, and during calls: `"Live Diagnosis Verified"`, `"3-Day Follow-Up"`, `"Prior Commitments Reviewed (3 Days Ago)"`, `"3-day pace"`, `"1,75,500"`. |
| `#summaryCardSection` -> `.summary-card`, `#whatsappPreview`, `#btnCopyWhatsapp`, `#btnDownloadSummary` | `app.js:376`, `kam_brain.js:523-604`; **Playwright waits `#summaryCardSection:not(.hidden) .summary-card`** | Starts `.hidden`; removed when `summary_card` arrives; must render `"Call Summary & Mock WhatsApp Recap"`, `"mock whatsapp / sms dispatch"`, `"Kumar Home & Kitchen"`, and committed `"ACT-..."` IDs. |
| `.hidden` CSS class | Strict utility in `styles.css:1237-1239` | Must enforce `display: none !important;` so Playwright `is_hidden()` / `:not(.hidden)` assertions stay accurate. |

### 5.2 Module State Variables & Mutators
1. **`app.js` `state` object:**
   - `inCall` (`false`): `startCall` (`true`), `endCall` (`false`).
   - `sessionId` (`null`): `startCall` (response `session_id`; note NOT cleared by `endCall()`, so `#btnFollowup` can link `priorSessionId: state.sessionId` after ending call!).
   - `mode` (`"inbound"`): `init` / `#selMode change` / `startCall` (`modeOverride`) / SSE `phase` (`data.mode`).
   - `language` (`"en-IN"`): `startCall` / `#selLanguage change`.
   - `headphonesMode` (`false`): `startCall` / `#chkHeadphones change`.
   - `ttsProvider` (`"auto"`): `startCall` / `#selTtsProvider change`.
   - `orbState` (`"idle"`): `setOrbState()` only.
   - `callStartMs` (`0`), `timerHandle` (`null`): `startCallTimer()` / `stopCallTimer()`.
   - `recognition` (`null`), `recognizing` (`false`): `startListening()`, `rec.onstart/onerror/onend`, `stopRecognition()`.
   - `eouTimer` (`null`), `silencePromptTimer` (`null`), `didSilenceReprompt` (`false`): `startListening()`, `rec.onresult`, `scheduleSilenceReprompt()`, `stopRecognition()`, `sendUserUtterance()`.
   - `interimTranscript` (`""`), `finalTranscript` (`""`): `startListening()`, `rec.onresult`.
   - `sttEndMs` (`0`), `firstTokenClientMs` (`null`), `firstAudioClientMs` (`null`): `sendUserUtterance()`, SSE `token`, first `sentence` audio callback.
   - `chatAbortController` (`null`): `consumeAgentStream()`, `handleInterrupt()`, `endCall()`.
   - `kamBrainHook` (`null`): `setKamBrainHook()` in `init()`.
   - `completedStreams` (`0`): incremented in SSE `done`.
2. **`audio.js` `AudioPlaybackQueue` instance:**
   - `queue`, `isPlaying`, `currentAudio`, `aborted`, `language`, `ttsProvider`, `fillerTimer`, `abortControllers` (`Set`), `_generation` (`number`), `_currentResolve`.
3. **`kam_brain.js` `brain` closure state:**
   - `mode`, `snapshot`, `activeProvider`, `demoData`, `diagnosedLive`, `actionPlanLive`, `toolLogs`, `failovers`, `commitments`, `priorCommitments`, `followupSchedule`, `summaryCard`, `lastLatency`, `ungroundedTotal`.

### 5.3 Event Listeners & Browser Storage
- **Static DOM listeners registered in `init()` (`app.js:567-642`):**
  - `window`: `DOMContentLoaded` -> `init()`; `keydown` -> `Spacebar` interrupt.
  - `#btnStartCall`, `#btnEndCall`, `#btnInterrupt`, `#btnFollowup`, `#micOrb`, `#btnToggleDebug`, `#btnSendDebug`: `click`.
  - `#selMode`, `#selLanguage`, `#chkHeadphones`, `#selTtsProvider`: `change`.
  - `#txtDebugInput`: `keydown` (`Enter` -> `#btnSendDebug.click()`).
  - `#errorBanner button`: inline `onclick` attribute in `index.html:54`.
- **Dynamic listeners in `kam_brain.js:575-603`:** `#btnCopyWhatsapp` (`navigator.clipboard.writeText`), `#btnDownloadSummary` (`Blob` download `.txt`).
- **Storage (`localStorage`, `sessionStorage`, `document.cookie`, `IndexedDB`):** **None used anywhere in `frontend/`**. All state resets on page reload.


---

## 6. Data shapes rendered by the KAM Brain panel

All metrics below are computed deterministically in `app/engine/diagnosis.py` from the seeded 60-SKU catalog (`HK-001..HK-060`) in `app/data/seed_data.py` (`Kumar Home & Kitchen`, Delhi, `MOCK-DL-4821`). Below are exact JSON shapes & real captured responses from `docs/captures/`.

### 6.1 Metrics Comparison (`GET /api/demo-data -> metrics` or `get_metric_comparison`)
- Fields: `snapshot` (`"current"|"followup"`), `baseline_label`, `current_label`, `metrics` (`sales_gmv`, `orders`, `asp`, `cancellation_rate_pct`, `out_of_stock_skus`), `volume_vs_price_finding`.
- When `snapshot == "followup"`, adds `followup_label` + `followup` (`sales_gmv_pace`, `sales_vs_crisis_pct`, `orders_pace`, `orders_vs_crisis_pct`, `asp`, `out_of_stock_skus`, `oos_target_max`, `cancellation_rate_pct`, `cancellation_target_max`, `impressions_daily_pace`). Note how `kam_brain.js` uses `metrics.followup` to add the 5th `"3-Day Pace"` table column!
- **Real Captured Example (`followup` snapshot, `docs/captures/health_and_demo_data.json`):**
```json
{
  "snapshot": "followup",
  "baseline_label": "Last Month",
  "current_label": "This Month",
  "metrics": {
    "sales_gmv": {
      "label": "Total Sales (Placed GMV)",
      "unit": "INR",
      "baseline": 200000,
      "current": 135000,
      "delta": -65000,
      "delta_pct": -32.5
    },
    "orders": {
      "label": "Total Orders Placed",
      "unit": "orders",
      "baseline": 400,
      "current": 270,
      "delta": -130,
      "delta_pct": -32.5
    },
    "asp": {
      "label": "Average Selling Price (ASP)",
      "unit": "INR",
      "baseline": 500,
      "current": 500,
      "delta": 0,
      "delta_pct": 0.0
    },
    "cancellation_rate_pct": {
      "label": "Cancellation Rate",
      "unit": "%",
      "baseline": 8.0,
      "current": 14.0,
      "baseline_count": 32,
      "current_count": 38,
      "delta_pp": 6.0
    },
    "out_of_stock_skus": {
      "label": "Out-of-Stock Products",
      "unit": "products",
      "baseline": 5,
      "current": 18,
      "delta": 13,
      "newly_oos": 13
    }
  },
  "volume_vs_price_finding": "Average Selling Price stayed flat at Rs 500 -> Rs 500, proving the 32.5% sales drop is purely a VOLUME problem (130 fewer orders), not a ticket-size problem.",
  "followup_label": "3 Days Later (Follow-up Review)",
  "followup": {
    "sales_gmv_pace": 175500,
    "sales_vs_crisis_pct": 30.0,
    "orders_pace": 351,
    "orders_vs_crisis_pct": 30.0,
    "asp": 500,
    "out_of_stock_skus": 9,
    "oos_target_max": 8,
    "cancellation_rate_pct": 10.5,
    "cancellation_target_max": 10.0,
    "impressions_daily_pace": 2610
  }
}
```

### 6.2 Root-Cause Attribution (`diagnosis` or `run_diagnosis`)
- Fields: `snapshot`, `problem_type`, `total_lost_orders`, `total_lost_revenue`, `asp_flat`, `drivers[]` (`rank`, `driver`, `title`, `lost_orders`, `lost_revenue`, `share_pct`, `evidence`), `already_answered_questions[]`, `assumptions[]`, optional `followup_comparison`.
- **Real Captured Example (`current` `diagnosis` + `followup` `followup_comparison`):**
```json
{
  "snapshot": "current",
  "problem_type": "volume_driven_decline",
  "total_lost_orders": 130,
  "total_lost_revenue": 65000,
  "asp_flat": true,
  "drivers": [
    {
      "rank": 1,
      "driver": "stock_outs",
      "title": "Out-of-Stock Top Sellers",
      "lost_orders": 78,
      "lost_revenue": 39000,
      "share_pct": 60.0,
      "evidence": "Out-of-stock products jumped from 5 to 18 (+13 new). Top 5 newly OOS products alone lost 68 orders."
    },
    {
      "rank": 2,
      "driver": "pricing_gaps",
      "title": "Competitor Price Gaps on Top SKUs",
      "lost_orders": 26,
      "lost_revenue": 13000,
      "share_pct": 20.0,
      "evidence": "3 active high-volume products (Electric Kettle, Dish Rack, Spatula Set) are priced 6.9% to 8.3% higher than the lowest marketplace price."
    },
    {
      "rank": 3,
      "driver": "cancellations_visibility",
      "title": "Cancellations & Search Visibility Drag",
      "lost_orders": 26,
      "lost_revenue": 13000,
      "share_pct": 20.0,
      "evidence": "Cancellation rate spiked from 8.0% to 14.0% (seller stock-unavailable 6 -> 14, dispatch SLA 5 -> 9), and search impressions dipped 14.0% as OOS listings lost rank."
    }
  ],
  "already_answered_questions": [
    "Was average order value / ASP lower? No, ASP stayed flat at Rs 500.",
    "Was buyer cancellation driving the spike? No, buyer-initiated cancellation rate stayed flat at ~4%; seller-side stock and dispatch SLA drove it.",
    "Are all products down equally? No, 60% of the drop is concentrated in newly out-of-stock products."
  ],
  "assumptions": [
    "Orders = placed orders; Sales = placed GMV (Orders x SKU Price).",
    "Cancellation rate = cancelled orders / placed orders.",
    "Causal attributions and recovery estimates are indicative mock model estimates, not guarantees."
  ]
}
```
*(In `followup` snapshot, `diagnosis.followup_comparison` is appended:)*
```json
{
  "days_elapsed": 3,
  "prior_commitments": [
    {
      "action_id": "ACT-1",
      "action": "Restock top 5 out-of-stock products and enable low-stock alerts",
      "owner": "Rajesh Kumar",
      "due_date": "Within 48 hours",
      "status": "partially_done",
      "progress_note": "4 of top 5 restocked (HK-006, HK-007, HK-008, HK-010). HK-009 still out of stock awaiting supplier; total OOS down from 18 to 9 (target <= 8)."
    },
    {
      "action_id": "ACT-2",
      "action": "Sync warehouse stock twice daily and clear morning dispatch before 2 PM cutoff",
      "owner": "Rajesh Kumar",
      "due_date": "Daily",
      "status": "partially_done",
      "progress_note": "Stock-unavailable cancellations fell sharply, bringing total cancellation rate from 14% to 10.5% (just shy of <= 10% target due to 2 dispatch delays)."
    },
    {
      "action_id": "ACT-3",
      "action": "Match competitor pricing within 2% on 3 overpriced products",
      "owner": "Rajesh Kumar",
      "due_date": "Within 3 days",
      "status": "partially_done",
      "progress_note": "Repriced Kettle (HK-019) and Dish Rack (HK-020); Spatula Set (HK-021) is still 8.3% above competitor."
    }
  ],
  "metrics": {
    "projected_monthly_sales": 175500,
    "projected_monthly_orders": 351,
    "asp": 500,
    "out_of_stock_skus": 9,
    "cancellation_rate_pct": 10.5,
    "seller_stock_cancel_rate_pct": 1.4,
    "dispatch_sla_cancel_rate_pct": 3.6,
    "impressions_daily_pace": 2610
  },
  "targets": {
    "out_of_stock_skus_max": 8,
    "cancellation_rate_pct_max": 10.0,
    "monthly_sales_pace_target": 180000
  }
}
```

### 6.3 Out-of-Stock SKUs (`oos_skus` or `get_out_of_stock_skus(limit=5)`)
- **Real Captured Examples (`current` & `followup` diff fields):**
```json
{
  "snapshot": "current",
  "total_oos_count": 18,
  "newly_oos_count": 13,
  "chronic_oos_count": 5,
  "total_oos_lost_orders": 78,
  "total_oos_lost_revenue": 39000,
  "top_oos_skus": [
    {
      "sku_id": "HK-006",
      "name": "Stainless Steel Pressure Cooker 5L",
      "category": "Cookware",
      "price": 800,
      "last_month_orders": 22,
      "this_month_orders": 6,
      "lost_orders": 16,
      "lost_revenue_month": 12800,
      "estimated_lost_rev_per_day": 587,
      "days_oos_this_month": 20,
      "newly_oos": true
    },
    {
      "sku_id": "HK-008",
      "name": "Insulated Steel Casserole Set 3Pc",
      "category": "Dining",
      "price": 600,
      "last_month_orders": 19,
      "this_month_orders": 5,
      "lost_orders": 14,
      "lost_revenue_month": 8400,
      "estimated_lost_rev_per_day": 380,
      "days_oos_this_month": 19,
      "newly_oos": true
    },
    {
      "sku_id": "HK-007",
      "name": "Non-Stick Granite Tawa 28cm",
      "category": "Cookware",
      "price": 500,
      "last_month_orders": 20,
      "this_month_orders": 5,
      "lost_orders": 15,
      "lost_revenue_month": 7500,
      "estimated_lost_rev_per_day": 333,
      "days_oos_this_month": 21,
      "newly_oos": true
    },
    {
      "sku_id": "HK-009",
      "name": "Airtight Kitchen Storage Jars 12Pc",
      "category": "Storage",
      "price": 400,
      "last_month_orders": 17,
      "this_month_orders": 5,
      "lost_orders": 12,
      "lost_revenue_month": 4800,
      "estimated_lost_rev_per_day": 227,
      "days_oos_this_month": 18,
      "newly_oos": true
    },
    {
      "sku_id": "HK-010",
      "name": "Hard Anodized Tadka Pan & Ladle",
      "category": "Cookware",
      "price": 250,
      "last_month_orders": 15,
      "this_month_orders": 4,
      "lost_orders": 11,
      "lost_revenue_month": 2750,
      "estimated_lost_rev_per_day": 125,
      "days_oos_this_month": 19,
      "newly_oos": true
    }
  ]
}
```
*(In `followup` snapshot, `top_oos_skus` puts `HK-009` first and adds `recently_restocked[]` & `remaining_priority_blocker`:)*
```json
{
  "total_oos_count": 9,
  "newly_oos_count": 9,
  "recently_restocked": [
    "HK-006 (Stainless Steel Pressure Cooker 5L)",
    "HK-007 (Non-Stick Granite Tawa 28cm)",
    "HK-008 (Insulated Steel Casserole Set 3Pc)",
    "HK-010 (Hard Anodized Tadka Pan & Ladle)"
  ],
  "remaining_priority_blocker": {
    "sku_id": "HK-009",
    "name": "Airtight Kitchen Storage Jars 12Pc",
    "price": 400,
    "estimated_daily_loss": 227,
    "blocker": "Supplier shipment delayed by 2 days"
  },
  "top_oos_skus_first": {
    "sku_id": "HK-009",
    "name": "Airtight Kitchen Storage Jars 12Pc",
    "category": "Storage",
    "price": 400,
    "last_month_orders": 17,
    "this_month_orders": 5,
    "lost_orders": 12,
    "lost_revenue_month": 4800,
    "estimated_lost_rev_per_day": 227,
    "days_oos_this_month": 18,
    "newly_oos": true
  }
}
```

### 6.4 Price-Gap SKUs (`pricing` or `get_pricing_gaps`)
- **Important Contract Quirk:** Even in `followup` snapshot, `pricing_gaps[]` still returns all 3 items while `overpriced_sku_count` becomes `1` and `repriced_skus` / `remaining_overpriced_sku` are added — `kam_brain.js:312-315` filters `pricing_gaps` down to `remaining_overpriced_sku.sku_id` when in follow-up mode!
- **Real Captured Example (`followup` snapshot showing all fields):**
```json
{
  "snapshot": "followup",
  "overpriced_sku_count": 1,
  "total_pricing_lost_orders": 26,
  "total_pricing_lost_revenue": 13000,
  "pricing_gaps": [
    {
      "sku_id": "HK-019",
      "name": "Electric Kettle 1.5L Double Wall",
      "seller_price": 700,
      "competitor_lowest_price": 655,
      "price_gap_rs": 45,
      "price_gap_pct": 6.9,
      "last_month_orders": 22,
      "this_month_orders": 14,
      "lost_orders": 8,
      "lost_revenue": 5600,
      "suggested_price": 659
    },
    {
      "sku_id": "HK-020",
      "name": "3-Tier Modular Dish Drying Rack",
      "seller_price": 500,
      "competitor_lowest_price": 465,
      "price_gap_rs": 35,
      "price_gap_pct": 7.5,
      "last_month_orders": 24,
      "this_month_orders": 14,
      "lost_orders": 10,
      "lost_revenue": 5000,
      "suggested_price": 469
    },
    {
      "sku_id": "HK-021",
      "name": "Silicone Non-Stick Spatula Set 5Pc",
      "seller_price": 300,
      "competitor_lowest_price": 277,
      "price_gap_rs": 23,
      "price_gap_pct": 8.3,
      "last_month_orders": 18,
      "this_month_orders": 10,
      "lost_orders": 8,
      "lost_revenue": 2400,
      "suggested_price": 281
    }
  ],
  "key_insight": "3 active top-selling products are priced 6.9% to 8.3% above the lowest marketplace offer, causing 26 lost orders (Rs 13,000) without affecting overall store ASP.",
  "repriced_skus": [
    {
      "sku_id": "HK-019",
      "name": "Electric Kettle 1.5L Double Wall",
      "old_price": 700,
      "new_price": 659,
      "competitor_lowest": 655
    },
    {
      "sku_id": "HK-020",
      "name": "3-Tier Modular Dish Drying Rack",
      "old_price": 500,
      "new_price": 469,
      "competitor_lowest": 465
    }
  ],
  "remaining_overpriced_sku": {
    "sku_id": "HK-021",
    "name": "Silicone Non-Stick Spatula Set 5Pc",
    "current_price": 300,
    "competitor_lowest": 277,
    "gap_pct": 8.3
  }
}
```

### 6.5 Cancellation Breakdown (`cancellations` or `get_cancellation_breakdown`)
*(Returned by `/api/demo-data` and `DIAGNOSE` pre-execution, logged in Tool Audit, currently unrendered as a standalone visual card in `kam_brain.js`.)*
- **Real Captured Example (`followup` snapshot including baseline & follow-up keys):**
```json
{
  "snapshot": "followup",
  "baseline_cancellation_rate_pct": 8.0,
  "current_cancellation_rate_pct": 14.0,
  "baseline_cancelled_orders": 32,
  "current_cancelled_orders": 38,
  "reasons": [
    {
      "reason_key": "seller_stock_unavailable",
      "label": "Seller cancelled - stock unavailable",
      "last_month_count": 6,
      "this_month_count": 14,
      "delta_count": 8,
      "last_month_rate_pct": 1.5,
      "this_month_rate_pct": 5.19
    },
    {
      "reason_key": "dispatch_sla_breach",
      "label": "Auto-cancelled - dispatch SLA delay",
      "last_month_count": 5,
      "this_month_count": 9,
      "delta_count": 4,
      "last_month_rate_pct": 1.25,
      "this_month_rate_pct": 3.33
    },
    {
      "reason_key": "buyer_initiated",
      "label": "Buyer initiated cancellation",
      "last_month_count": 15,
      "this_month_count": 11,
      "delta_count": -4,
      "last_month_rate_pct": 3.75,
      "this_month_rate_pct": 4.07
    },
    {
      "reason_key": "courier_address_issue",
      "label": "Courier / address / COD issue",
      "last_month_count": 6,
      "this_month_count": 4,
      "delta_count": -2,
      "last_month_rate_pct": 1.5,
      "this_month_rate_pct": 1.48
    }
  ],
  "key_insight": "Seller-side stock-unavailable cancellations more than doubled (6 -> 14, from 1.5% to 5.2% of orders) and dispatch-SLA auto-cancellations rose (5 -> 9), while buyer-initiated cancellation rate remained flat (~4%).",
  "followup_cancellation_rate_pct": 10.5,
  "followup_seller_stock_cancel_rate_pct": 1.4,
  "followup_dispatch_sla_cancel_rate_pct": 3.6,
  "followup_note": "Stock-unavailable cancellations fell sharply after syncing inventory, bringing overall rate from 14.0% down to 10.5% (close to the 10.0% target); dispatch SLA delays remain the main gap."
}
```

### 6.6 Visibility Funnel (`funnel` or `get_visibility_funnel`)
*(Returned by `/api/demo-data` and `DIAGNOSE` pre-execution, logged in Tool Audit, currently unrendered as a chart in `kam_brain.js`.)*
- **Real Captured Example (`followup` snapshot):**
```json
{
  "snapshot": "followup",
  "last_month": {
    "impressions": 80000,
    "clicks": 3200,
    "orders": 400,
    "ctr_pct": 4.0,
    "cvr_pct": 12.5
  },
  "this_month": {
    "impressions": 68800,
    "clicks": 2477,
    "orders": 270,
    "ctr_pct": 3.6,
    "cvr_pct": 10.9
  },
  "changes": {
    "impressions_pct": -14.0,
    "clicks_pct": -22.6,
    "orders_pct": -32.5,
    "ctr_delta_pp": -0.4,
    "cvr_delta_pp": -1.6
  },
  "key_insight": "Impressions fell 14.0% (80,000 -> 68,800) primarily because 18 out-of-stock listings dropped out of search ranking, while conversion fell 1.6 percentage points (12.5% -> 10.9%) due to pricing gaps and stock unavailability.",
  "followup_impressions_daily_pace": 2610
}
```

### 6.7 Prioritised Action Plan (`action_plan` or `build_action_plan`)
- **Real Captured Example (`current` Actions `ACT-1..ACT-3`; `followup` Actions `ACT-FU-1..ACT-FU-3`):**
```json
{
  "snapshot": "current",
  "actions": [
    {
      "action_id": "ACT-1",
      "priority": 1,
      "title": "Restock top 5 high-velocity OOS products and enable low-stock alerts",
      "owner": "Rajesh Kumar",
      "effort": "Medium",
      "impact": "Very High (~60% of drop)",
      "target_skus": [
        "HK-006",
        "HK-007",
        "HK-008",
        "HK-009",
        "HK-010"
      ],
      "estimated_recovery_range_rs": "Rs 28,000 - Rs 36,000/month",
      "recommended_due": "Within 48 hours",
      "details": "Prioritize Pressure Cooker 5L (HK-006), Granite Tawa (HK-007), Casserole Set (HK-008), Storage Jars (HK-009), and Tadka Pan (HK-010), and set reorder alerts at 10 units."
    },
    {
      "action_id": "ACT-2",
      "priority": 2,
      "title": "Fix daily inventory sync & dispatch before SLA cutoff to cut cancellations below 10%",
      "owner": "Rajesh Kumar",
      "effort": "Low",
      "impact": "High (~20% of drop + search rank protection)",
      "target_skus": [
        "Storewide Stock Sync",
        "Dispatch Queue"
      ],
      "estimated_recovery_range_rs": "Rs 10,000 - Rs 14,000/month",
      "recommended_due": "Starting today",
      "details": "Update stock buffer twice daily to stop stock-unavailable cancellations (currently 14/month) and clear ready-to-ship orders before 2 PM to avoid auto-cancellations (currently 9/month)."
    },
    {
      "action_id": "ACT-3",
      "priority": 3,
      "title": "Reprice 3 overpriced bestsellers within 2% of lowest offer (or run a promo/ads boost)",
      "owner": "Rajesh Kumar",
      "effort": "Low",
      "impact": "Medium-High (~20% of drop)",
      "target_skus": [
        "HK-019",
        "HK-020",
        "HK-021"
      ],
      "estimated_recovery_range_rs": "Rs 9,000 - Rs 13,000/month",
      "recommended_due": "Within 3 days",
      "details": "Adjust Electric Kettle (Rs 700 -> ~Rs 659), Dish Rack (Rs 500 -> ~Rs 469), and Spatula Set (Rs 300 -> ~Rs 280), or run an optional limited-time promotion or ads campaign, subject to eligibility."
    }
  ],
  "estimated_combined_recovery_rs": "Rs 47,000 - Rs 63,000/month (indicative range)",
  "assumptions": "Indicative recovery ranges assume normal marketplace demand, restored search rank within 3-5 days of restocking, and competitive fulfillment SLAs. Not a guaranteed outcome."
}
```
```json
{
  "snapshot": "followup",
  "actions": [
    {
      "action_id": "ACT-FU-1",
      "priority": 1,
      "title": "Expedite HK-009 (Airtight Storage Jars) restock or temporary alternate pack",
      "owner": "Rajesh Kumar",
      "effort": "Low-Medium",
      "impact": "High",
      "target_skus": [
        "HK-009"
      ],
      "estimated_recovery_range_rs": "Rs 6,000 - Rs 7,500/month",
      "recommended_due": "Within 48 hours",
      "details": "4 of top 5 OOS products are back in stock (OOS down 18 -> 9). Restocking HK-009 brings OOS to 8 or below, hitting the target threshold."
    },
    {
      "action_id": "ACT-FU-2",
      "priority": 2,
      "title": "Lock 2 PM daily manifest cutoff to push cancellation rate under 10%",
      "owner": "Rajesh Kumar",
      "effort": "Low",
      "impact": "Medium-High",
      "target_skus": [
        "Warehouse Dispatch Queue"
      ],
      "estimated_recovery_range_rs": "Rs 4,000 - Rs 6,000/month",
      "recommended_due": "Starting tomorrow",
      "details": "Stock-unavailable cancellations fell sharply, moving overall rate from 14.0% to 10.5%. Eliminating the remaining dispatch SLA delays gets below the 10.0% target."
    },
    {
      "action_id": "ACT-FU-3",
      "priority": 3,
      "title": "Reprice HK-021 (Silicone Spatula Set) from Rs 300 to Rs 280 and consider visibility boost",
      "owner": "Rajesh Kumar",
      "effort": "Low",
      "impact": "Medium",
      "target_skus": [
        "HK-021"
      ],
      "estimated_recovery_range_rs": "Rs 2,000 - Rs 3,000/month",
      "recommended_due": "Today",
      "details": "HK-019 and HK-020 are already repriced. Matching HK-021 closer to Rs 277 closes the last price gap; you can also explore a promotional offer or ads campaign, subject to eligibility."
    }
  ],
  "estimated_combined_recovery_rs": "Rs 12,000 - Rs 16,500 additional monthly pace",
  "assumptions": "Indicative recovery ranges assume normal marketplace demand, restored search rank within 3-5 days of restocking, and competitive fulfillment SLAs. Not a guaranteed outcome."
}
```

### 6.8 Commitments & Prior Commitments (`commitments[]`, `prior_commitments[]`)
- **Live Commitment Shape** (created by `commit_action`): `{"action_id", "action_summary", "owner", "due_date", "target_metric", "status": "committed"}`.
- **Two Distinct Shapes for `prior_commitments[]` (IMPORTANT Backend/UI Discrepancy!):**
  1. When `/api/session/start` receives valid `prior_session_id` with commitments (`app/agent/session.py:94-99`): clones `{"action_id", "action_summary", "owner", "due_date", "target_metric", "status": "partially_done"}`.
  2. When `prior_session_id` is omitted/empty (`app/agent/session.py:100-103`): copies `FOLLOWUP_SNAPSHOT["default_prior_commitments"]`, whose keys are `{"action_id", "action", "owner", "due_date", "status": "partially_done", "progress_note"}` *(see bug in Section 7.1!)*.
- **Real Captured Examples (`followup_call.json` Session + `default_prior_commitments`):**
```json
{
  "linked_prior_commitment": {
    "action_id": "ACT-1",
    "action_summary": "Restock top 5 out-of-stock items",
    "owner": "Rajesh Kumar",
    "due_date": "Within 48 hours",
    "target_metric": "Out-of-stock products <= 8",
    "status": "partially_done"
  },
  "live_followup_commitment": {
    "action_id": "ACT-2",
    "action_summary": "Restock HK-009 within 48 hours and reprice HK-021 today",
    "owner": "Rajesh Kumar",
    "due_date": "Within 48 hours",
    "target_metric": "Out-of-stock products <= 8",
    "status": "committed"
  },
  "default_prior_commitment_sample": {
    "action_id": "ACT-1",
    "action": "Restock top 5 out-of-stock products and enable low-stock alerts",
    "owner": "Rajesh Kumar",
    "due_date": "Within 48 hours",
    "status": "partially_done",
    "progress_note": "4 of top 5 restocked (HK-006, HK-007, HK-008, HK-010). HK-009 still out of stock awaiting supplier; total OOS down from 18 to 9 (target <= 8)."
  }
}
```

### 6.9 Follow-Up Schedule & Targets (`followup_schedule` / `schedule_followup`)
- Nullable on `session` (`null` until `schedule_followup` is called; if omitted before `SUMMARY`, `send_call_summary` injects default `followup_date: "In 3 days"` + default 2 targets into `summary_card.followup`).
- **Real Captured Example (`followup_call.json` `schedule_followup` result):**
```json
{
  "followup_date": "In 3 days at 11 AM",
  "success_targets": [
    "Out-of-stock products <= 8",
    "Cancellation rate <= 10%"
  ],
  "scheduled": true
}
```

### 6.10 Call Summary & WhatsApp Card (`summary_card` / `send_call_summary`)
- **Real Captured Example (`followup_call.json` final `summary_card`):**
```json
{
  "seller_name": "Rajesh Kumar",
  "shop_name": "Kumar Home & Kitchen",
  "key_issues": [
    "18 out-of-stock products dropping to 9",
    "High cancellation rate of 12.5% improving to 10.5%",
    "Uncompetitive pricing on HK-021"
  ],
  "commitments": [
    {
      "action_id": "ACT-2",
      "action_summary": "Restock HK-009 within 48 hours and reprice HK-021 today",
      "owner": "Rajesh Kumar",
      "due_date": "Within 48 hours",
      "target_metric": "Out-of-stock products <= 8",
      "status": "committed"
    }
  ],
  "followup": {
    "followup_date": "In 3 days at 11 AM",
    "success_targets": [
      "Out-of-stock products <= 8",
      "Cancellation rate <= 10%"
    ],
    "scheduled": true
  },
  "whatsapp_text": "*Snapdeal Virtual KAM — 3-Day Follow-Up Summary (Prototype)*\nSeller: Rajesh Kumar (Kumar Home & Kitchen)\nDate: 10 Nov 2026\n\n*Key Findings:*\n- 18 out-of-stock products dropping to 9\n- High cancellation rate of 12.5% improving to 10.5%\n- Uncompetitive pricing on HK-021\n\n*Agreed Action Plan:*\n1. Restock HK-009 within 48 hours and reprice HK-021 today | Owner: Rajesh Kumar | Due: Within 48 hours\n\n*Next Follow-up Call:* In 3 days at 11 AM\n*Success Targets:* Out-of-stock products <= 8; Cancellation rate <= 10%\n\n_Prototype mock data summary — Riya, Virtual KAM_"
}
```

### 6.11 Latency Stats (`latency` SSE / `latency_history[]`)
- Server fields: `{"llm_first_token_ms": int, "total_turn_ms": int, "llm_requests": int, "provider": string, "ungrounded_numbers": string[]}`.
- Client-side KPIs: `#latencySttToToken` (`performance.now() - sttEndMs` on first SSE `token`), `#latencySttToAudio` (`performance.now() - sttEndMs` inside first queued sentence's `onFirstAudioCallback` — note this includes network TTS fetch + queue start!), `#latencyProvider` (`data.provider`).
- **Real Captured Example (`inbound_call.json` Turn 2 vs Turn 4):**
```json
[
  {
    "llm_first_token_ms": 490,
    "total_turn_ms": 618,
    "llm_requests": 1,
    "provider": "groq:openai/gpt-oss-120b",
    "ungrounded_numbers": []
  },
  {
    "llm_first_token_ms": 1330,
    "total_turn_ms": 1330,
    "llm_requests": 2,
    "provider": "groq:openai/gpt-oss-120b",
    "ungrounded_numbers": []
  }
]
```

### 6.12 Grounding-Check Result (`ungrounded_numbers`, `session.ungrounded_warnings`)
- Inside `latency`: `ungrounded_numbers: string[]` (`[]` when 0 hallucinated metric numbers found by `find_ungrounded_numbers()`). `kam_brain.js` accumulates `brain.ungroundedTotal += data.ungrounded_numbers.length` and toggles the badge `"Grounding Check: 0 Ungrounded Numbers"` vs `"Warning: N Ungrounded"`.
- Session state also appends `{"phase": str, "ungrounded_numbers": string[], "reply": str}` to `session.ungrounded_warnings` whenever non-empty (`[]` across all captured runs).

### 6.13 Phase List & Transitions Per Call Mode
- **Standard (`inbound` & `proactive`):** `GREETING` -> `UNDERSTAND` -> `CLARIFY` (up to 3 Qs) -> `DIAGNOSE` -> `RECOMMEND` -> `COMMIT` -> `SUMMARY` -> `CLOSE`. In `#phaseStrip`, **`REVIEW_COMMITMENTS` is hidden** via `.hidden`.
  - Note shortcut in `proactive`: Turn 1 with `why|what happened|tell me|kyu|kya` jumps straight `GREETING -> DIAGNOSE`, otherwise `GREETING -> CLARIFY` (skips `UNDERSTAND`).
  - Note shortcut in `inbound`: Turn 1 with `>= 6` words matching `_DIAGNOSIS_REQUEST_SIGNALS` skips `UNDERSTAND` -> `CLARIFY`.
- **Follow-Up (`followup`):** `GREETING` -> `REVIEW_COMMITMENTS` -> `RECOMMEND` -> `COMMIT` -> `SUMMARY` -> `CLOSE`. In `#phaseStrip`, **`UNDERSTAND`, `CLARIFY`, `DIAGNOSE` are hidden** via `.hidden`.

### 6.14 Tool Log Entries (`tool_call`, `tool_result`, `session.tool_history`, `kam_brain.toolLogs`)
- **SSE Execution Lifecycle:** `tool_call` (`{"name", "arguments", "source"}`) creates a `"running"` row in `brain.toolLogs`; matching `tool_result` (`{"name", "result", "latency_ms"}`) marks the latest matching running row `"done"` with server `latency_ms` and UI `summary`.
- **`source` values:** `"server_preexec"` (deterministic phase preload before LLM call), `"llm"` (model function call in `CLARIFY`/`COMMIT`/`SUMMARY`), `"server_fallback"` (auto-run `send_call_summary` in `SUMMARY` if LLM omitted it).
- **Complete Tool Summary Formatting Table (`kam_brain.js:96-121`):**
  - `run_diagnosis`: `"Attributed -{total_lost_orders} orders (₹{total_lost_revenue})"`
  - `get_out_of_stock_skus`: `"{total_oos_count} OOS SKUs ({newly_oos_count} newly OOS)"`
  - `get_pricing_gaps`: `"{overpriced_sku_count} overpriced SKUs (-{total_pricing_lost_orders} orders)"`
  - `get_cancellation_breakdown`: `"Cancel rate {baseline}% -> {current}%"`
  - `get_visibility_funnel`: `"Impressions {impressions_pct}%"`
  - `build_action_plan`: `"{actions.length} prioritised actions ({estimated_combined_recovery_rs})"`
  - `commit_action`: `"Committed {commitment.action_id} due {commitment.due_date}"`
  - `schedule_followup`: `"Scheduled: {followup_date}"`
  - `send_call_summary`: `"Generated Call Summary & WhatsApp text"`
  - `get_metric_comparison`: `"Sales {sales_gmv.delta_pct}% | ASP flat ₹{asp.current}"`
  - `get_seller_overview`: `"Loaded {seller.shop_name} ({snapshot})"`
- **Real Captured Entry (`session.tool_history[0]` from `inbound_call.json`, `result` abbreviated to keys):**
```json
{
  "name": "run_diagnosis",
  "arguments": {},
  "latency_ms": 0.98,
  "source": "server_preexec",
  "result_keys": [
    "snapshot",
    "problem_type",
    "total_lost_orders",
    "total_lost_revenue",
    "asp_flat",
    "drivers",
    "already_answered_questions",
    "assumptions"
  ]
}
```


---

## 7. Known issues and limits

Being completely candid about bugs, hacks, and untested paths in the current frontend/integration:

1. **Prior Commitments Field-Name Bug (`kam_brain.js:277-284` vs `seed_data.py:319-344`):**
   - Without a prior session ID, `default_prior_commitments` returns keys `action` and `progress_note`. With a prior session ID, `SessionStore` returns `action_summary` and `target_metric`.
   - `kam_brain.js` reads `c.summary || c.action_summary || c.action_id` and `c.detail`. Consequently, default follow-up calls render `"ACT-1: ACT-1"` with zero progress note, and even linked calls omit `target_metric` (`detail` is always `undefined`).
2. **Decimal Numbers Split Across `sentence` Events During Token Streaming (`orchestrator.py:30`):**
   - **Verified live in `docs/captures/failover_turn.json`:** `_SENTENCE_BOUNDARY_RE` (`(.+?[.?!\u0964])(?:\s+|$)`) matched the period inside `"32."` at the end of a token chunk before `"5%"` arrived, emitting two malformed sentences: `"I see your sales fell 32."` and `"5% and out of stock SKUs rose to 18 this month."` — breaking spoken TTS into `"fell 32"` followed by `"five percent"`. Future frontend buffering or backend regex fix needed!
3. **600ms Browser Filler Can Talk Over Early MP3 Playback (`audio.js:50-171`):**
   - `_speakBrowser(fillerText, { isFiller: true })` starts at `600ms`. When a server MP3 arrives shortly afterward (`700–900ms`), `enqueueSentence` only calls `clearLatencyFiller()` (`clearTimeout`) and `_playBlob()` **never** calls `window.speechSynthesis.cancel()`. Thus the filler utterance can briefly speak simultaneously with the first MP3 sentence! Only `stopAll()` or another browser-TTS fallback cancels it.
4. **Raw Provider Error Text & Truncated Org String Shown in UI Banner (`kam_brain.js:462` & `router.py:216`):**
   - Current failover banner renders `f.reason` directly (`"groq (openai/gpt-oss-20b) HTTP 429 rate limited: {\"error\":{... in organization `org"`). Addressed in Section 9 Item 1.
5. **Spacebar Interrupt Keydown Quirks (`app.js:632-639`):**
   - Only ignores `tagName === "INPUT"` and `"SELECT"` (does not ignore `"TEXTAREA"`, `contentEditable`, or focused `<button>` elements where Space also triggers button activation). Note also there is no mobile push-to-talk hold gesture besides clicking `#btnInterrupt` or `#micOrb`.
6. **Low-Confidence `(confirming...)` Is Cosmetic Only (`app.js:251-278`):**
   - `alt.confidence < 0.45` appends `" (confirming...)"` to the interim caption only; the 800ms EOU timer still auto-sends the unconfirmed raw transcript automatically.
7. **`/api/stt` (Groq Whisper Fallback) Is Unimplemented on Frontend:**
   - Firefox/Safari show `#browserBanner` and have no MediaRecorder-to-`/api/stt` pipeline wired up; only `#debugBox` text input works outside Chrome/Edge.
8. **Gemini TTS Emergency Tier Fails Startup Smoke on Configured Models (`tts/service.py`):**
   - Captured live in `health_and_demo_data.json`: both `gemini-3.8-flash-tts` and `gemini-2.5-flash-preview-tts` fail with `400 INVALID_ARGUMENT: Unknown name "speechConfig" at 'generation_config'`. Also `edge-tts` sporadically returned `"No audio was received"` on an isolated sentence in testing (triggering a 30s cooldown to browser `speechSynthesis`) before succeeding on subsequent runs.
9. **Backend Computes Cancellations & Visibility Funnel Data Not Shown Visually:**
   - `/api/demo-data` and `DIAGNOSE` tools supply full `cancellations` (`reasons[]`) and `funnel` (`impressions`, `clicks`, `ctr_pct`, `cvr_pct`) datasets, plus `already_answered_questions[]` & `assumptions[]`, none of which are rendered cleanly in dedicated UI views yet.
10. **Pricing Follow-Up Copy Mismatch & Follow-Up SKU Filtering Quirk (`diagnosis.py:273,450` vs `kam_brain.js:312-315`):**
   - `get_pricing_gaps("followup")` returns all 3 items in `pricing_gaps[]` with `HK-021.suggested_price = 281`, whereas `build_action_plan("followup")` `ACT-FU-3` says `"from Rs 300 to Rs 280"`.
11. **Zero Session Persistence Across Browser Refresh / Multi-Worker Deployments:**
   - Reloading `/` loses `state.sessionId` and wipes `#kamBrainRoot` back to initial snapshot; server `SessionStore` is in-RAM (`--workers 1`).
12. **Playwright Stubbing & Untested Real-Microphone Behaviours (`tests/test_walking_skeleton_ui.py`, `tests/test_full_ui.py`):**
   - Automated Playwright E2E tests **stub** `window.SpeechRecognition` (with empty `start()`/`stop()` methods) and **stub** `HTMLMediaElement.prototype.play` (`setTimeout(..., 100/150)` -> immediate `onended`) and drive all conversation turns exclusively via `#txtDebugInput` + `#btnSendDebug`.
   - **Untested in automation (manual Chrome checklist only — `docs/VOICE_TEST_CHECKLIST.md`):**
     - Real acoustic microphone capture, Chrome AEC, and `SpeechRecognition` 800ms EOU cutoff on partial/final streams.
     - Chrome's 60s continuous-listening auto-stop and the `180ms` restart-on-end loop.
     - `Headphones Mode` (`#chkHeadphones`) `rec.onspeechstart` live barge-in vs half-duplex speaker echo leakage.
     - `6000ms` silence re-prompt timing and recovery back into listening.
     - `Spacebar` interrupt shortcut and `#micOrb` click-to-interrupt while audio plays.
     - 600ms filler overlap with real MP3 playback and real Web Audio ringtone output (`AudioContext`).
     - `hi-IN` (Devanagari Hindi) and `hinglish` live STT/TTS end-to-end flows and `#btnFollowup` clicked flow with `#selLanguage` change (`POST /api/session/{id}/settings`).
     - `/api/stt` live Groq Whisper transcription upload.


---

## 8. Captured real session logs

All raw SSE event streams (`raw_sse` + `parsed_events`) and REST payloads below were recorded against the running FastAPI backend at the pre-Section-9 baseline (with live Groq + Gemini + Edge-TTS) and saved verbatim (secrets `<REDACTED>`) under `docs/captures/` for building a frontend replay/mock mode without live keys. *(Note: in `v1` post-Section-9 builds, `failover` and `error` events emit the sanitized shapes in Section 3.3, and transitions additionally emit `phase_change` + `tool_start`.)*

### 8.1 Capture File Inventory (`docs/captures/`)
| File | Size | Covers | Key Observations From Real Run |
| :--- | :--- | :--- | :--- |
| `docs/captures/inbound_call.json` | 185,588 B | **(a) Full INBOUND call** (`GREETING` -> `CLOSE`, 7 turns + settings + final session) | Zero-LLM template greeting -> `CLARIFY` (2 turns) -> `DIAGNOSE` (5 `server_preexec` tools) -> `RECOMMEND` -> `COMMIT` (`ACT-1`) -> `SUMMARY` (`summary_card`) -> `CLOSE`. Includes live 429 failover on Turn 0 (`gpt-oss-20b` -> `gpt-oss-120b`). |
| `docs/captures/proactive_call.json` | 147,180 B | **(b) Full PROACTIVE call** (`GREETING` -> `CLOSE`, 5 turns + final session) | LLM greeting pre-executes `get_seller_overview` + `get_metric_comparison`. Turn 0 jumps directly to `DIAGNOSE`, fails over live from Groq `gpt-oss-120b` (429) -> Gemini `gemini-3.5-flash-lite`, finishes in `CLOSE` with `summary_card`. |
| `docs/captures/followup_call.json` | 160,603 B | **(c) Full 3-day FOLLOW-UP call** (`prior_session_id` linked to `proactive_call` session, 5 turns) | Greeting pre-execs followup pace (`1,35,000 -> 1,75,500`, `18 -> 9` OOS); Turn 0 runs `REVIEW_COMMITMENTS` (`failover` Groq -> Gemini); Turn 3 invokes BOTH `schedule_followup` and `send_call_summary`; reaches `CLOSE`. |
| `docs/captures/failover_turn.json` | 11,618 B | **(d) Real provider failover SSE turn** (extracted from inbound Turn 0) | Real HTTP 429 on `groq:openai/gpt-oss-20b` -> fails over to `groq:openai/gpt-oss-120b`; includes raw token stream & proves decimal-split sentence quirk (`"32."` / `"5%"`). |
| `docs/captures/error_all_providers_failed.json` | 3,083 B | **Bonus:** Real `AllProvidersUnavailableError` SSE `error` event | Shows simultaneous 429 TPM limit (`gpt-oss-120b`), 403 project block (`qwen/qwen3.8-27b`), 503 high demand (`gemini-3.5-flash-lite`), and 12s timeout (`gemini-3.6-flash`). |
| `docs/captures/tts_success_and_fallback.json` | 3,744 B | **(e) `/api/tts` success (cold + cached) & fallback responses** | Cold MP3 (`33,552` bytes, `x-tts-cached: false`), cached repeat (`x-tts-cached: true`), browser override JSON, empty-text JSON, AND real live `edge-tts` `"No audio was received"` JSON fallback (`152` bytes). |
| `docs/captures/health_and_demo_data.json` | 35,759 B | `/api/health`, `/api/demo-data?snapshot=current`, `?snapshot=followup`, `/api/tts-probe` | Real startup smoke results (`groq` 496ms, `gemini` 2421ms, Gemini TTS 400 error, Edge probe 27,504B in 1,111ms). |

### 8.2 Offline Replay Mode Tip for the New Frontend
To build a standalone UI replay mode using `docs/captures/*.json`:
- Load `health_and_demo_data.json` to stub `GET /api/health` and `GET /api/demo-data`.
- Load `inbound_call.json`, `proactive_call.json`, or `followup_call.json`, read `session_start.response` for `/api/session/start`, and drip-feed each `parsed_events` array (or split `raw_sse` on `"\n\n"`) on a `20–40ms` token timer.
- Return `tts_fallback_browser_selected.response` (HTTP `200` `application/json`) for `/api/tts` in offline replay so `AudioPlaybackQueue` speaks every sentence cleanly using the browser's `speechSynthesis`.

### 8.3 Verbatim Failover Turn Capture (`docs/captures/failover_turn.json` — Raw SSE & Request/Headers)
```json
{
  "source_call": "inbound",
  "turn_index": 0,
  "user_text": "Hi Riya, my sales dropped significantly this month. Can you help?",
  "request": {
    "method": "POST",
    "url": "/api/chat",
    "body": {
      "session_id": "8b4d1d1f4a7b",
      "text": "Hi Riya, my sales dropped significantly this month. Can you help?",
      "language": "en-IN"
    }
  },
  "status_code": 200,
  "headers": {
    "date": "Tue, 10 Nov 2026 11:08:37 GMT",
    "server": "uvicorn",
    "cache-control": "no-cache",
    "x-accel-buffering": "no",
    "content-type": "text/event-stream; charset=utf-8",
    "vary": "Origin",
    "transfer-encoding": "chunked"
  },
  "raw_sse": "event: phase\ndata: {\"phase\": \"CLARIFY\", \"mode\": \"inbound\"}\n\nevent: tool_call\ndata: {\"name\": \"get_seller_overview\", \"arguments\": {}, \"source\": \"llm\"}\n\nevent: tool_result\ndata: {\"name\": \"get_seller_overview\", \"result\": {\"seller\": {\"seller_id\": \"MOCK-DL-4821\", \"seller_name\": \"Rajesh Kumar\", \"salutation\": \"Rajesh ji\", \"shop_name\": \"Kumar Home & Kitchen\", \"city\": \"Delhi\", \"category\": \"Home & Kitchen\", \"active_catalog_size\": 60, \"tenure_months\": 30, \"prototype_notice\": \"Prototype - Mock Data Only\"}, \"snapshot\": \"current\", \"headline_summary\": {\"sales_last_month\": 200000, \"sales_this_month\": 135000, \"sales_drop_pct\": 32.5, \"orders_last_month\": 400, \"orders_this_month\": 270, \"orders_drop\": 130, \"asp\": 500, \"oos_last_month\": 5, \"oos_this_month\": 18, \"cancellation_rate_last_month_pct\": 8.0, \"cancellation_rate_this_month_pct\": 14.0}, \"followup_metrics\": null}, \"latency_ms\": 0.34}\n\nevent: failover\ndata: {\"from\": \"groq:openai/gpt-oss-20b\", \"to\": \"groq:openai/gpt-oss-120b\", \"reason\": \"groq (openai/gpt-oss-20b) HTTP 429 rate limited: {\\\"error\\\":{\\\"message\\\":\\\"Rate limit reached for model `openai/gpt-oss-20b` in organization `org\"}\n\nevent: token\ndata: {\"text\": \"I\"}\n\nevent: token\ndata: {\"text\": \" see\"}\n\nevent: token\ndata: {\"text\": \" your\"}\n\nevent: token\ndata: {\"text\": \" sales\"}\n\nevent: token\ndata: {\"text\": \" fell\"}\n\nevent: token\ndata: {\"text\": \" \"}\n\nevent: token\ndata: {\"text\": \"32\"}\n\nevent: token\ndata: {\"text\": \".\"}\n\nevent: sentence\ndata: {\"text\": \"I see your sales fell 32.\", \"speakable\": \"I see your sales fell 32.\"}\n\nevent: token\ndata: {\"text\": \"5\"}\n\nevent: token\ndata: {\"text\": \"%\"}\n\nevent: token\ndata: {\"text\": \" and\"}\n\nevent: token\ndata: {\"text\": \" out\"}\n\nevent: token\ndata: {\"text\": \"‑\"}\n\nevent: token\ndata: {\"text\": \"of\"}\n\nevent: token\ndata: {\"text\": \"‑\"}\n\nevent: token\ndata: {\"text\": \"stock\"}\n\nevent: token\ndata: {\"text\": \" SK\"}\n\nevent: token\ndata: {\"text\": \"Us\"}\n\nevent: token\ndata: {\"text\": \" rose\"}\n\nevent: token\ndata: {\"text\": \" to\"}\n\nevent: token\ndata: {\"text\": \" \"}\n\nevent: token\ndata: {\"text\": \"18\"}\n\nevent: token\ndata: {\"text\": \" this\"}\n\nevent: token\ndata: {\"text\": \" month\"}\n\nevent: token\ndata: {\"text\": \".\"}\n\nevent: sentence\ndata: {\"text\": \"5% and out of stock SKUs rose to 18 this month.\", \"speakable\": \"five percent and out of stock products rose to 18 this month.\"}\n\nevent: token\ndata: {\"text\": \" Have\"}\n\nevent: token\ndata: {\"text\": \" there\"}\n\nevent: token\ndata: {\"text\": \" been\"}\n\nevent: token\ndata: {\"text\": \" any\"}\n\nevent: token\ndata: {\"text\": \" delays\"}\n\nevent: token\ndata: {\"text\": \" rest\"}\n\nevent: token\ndata: {\"text\": \"ocking\"}\n\nevent: token\ndata: {\"text\": \" your\"}\n\nevent: token\ndata: {\"text\": \" best\"}\n\nevent: token\ndata: {\"text\": \"‑\"}\n\nevent: token\ndata: {\"text\": \"selling\"}\n\nevent: token\ndata: {\"text\": \" items\"}\n\nevent: token\ndata: {\"text\": \" recently\"}\n\nevent: token\ndata: {\"text\": \"?\"}\n\nevent: sentence\ndata: {\"text\": \"Have there been any delays restocking your best selling items recently?\", \"speakable\": \"Have there been any delays restocking your best selling items recently?\"}\n\nevent: latency\ndata: {\"llm_first_token_ms\": 995, \"total_turn_ms\": 1081, \"llm_requests\": 2, \"provider\": \"groq:openai/gpt-oss-120b\", \"ungrounded_numbers\": []}\n\nevent: done\ndata: {\"text\": \"I see your sales fell 32.5% and out of stock SKUs rose to 18 this month. Have there been any delays restocking your best selling items recently?\", \"speakable\": \"I see your sales fell thirty-two point five percent and out of stock products rose to 18 this month. Have there been any delays restocking your best selling items recently?\", \"phase\": \"CLARIFY\", \"provider\": \"groq:openai/gpt-oss-120b\", \"commitments\": [], \"followup_schedule\": null, \"summary_card\": null}\n\n"
}
```

### 8.4 Verbatim Summary of Multi-Turn Calls (`inbound_call.json`, `proactive_call.json`, `followup_call.json`)
*(Full raw SSE strings & `parsed_events` for every turn live verbatim inside `docs/captures/`. Key turn milestones extracted below:)*
```json
{
  "inbound_milestones": {
    "greeting_done": {
      "text": "Namaste Rajesh ji, I am Riya, your Snapdeal account manager. How can I help Kumar Home and Kitchen today?",
      "phase": "GREETING",
      "provider": "template"
    },
    "turn_phases": [
      {
        "turn": 0,
        "user": "Hi Riya, my sales dropped significantly this month. Can you help?",
        "phase": "CLARIFY",
        "tools": [],
        "done_text": "I'm sorry to hear that, Rajesh ji. Have you experienced any restocking delays on your best selling items this month?"
      },
      {
        "turn": 1,
        "user": "Yes, some pressure cookers and tawas ran out of stock for two weeks.",
        "phase": "CLARIFY",
        "tools": [],
        "done_text": "Got it. Have you noticed any recent price changes from competitors on those pressure cookers or tawas?"
      },
      {
        "turn": 2,
        "user": "We also missed a couple of 2 PM dispatch cutoffs. Please check my data and tell me why.",
        "phase": "DIAGNOSE",
        "tools": [
          "run_diagnosis",
          "get_out_of_stock_skus",
          "get_cancellation_breakdown",
          "get_pricing_gaps",
          "get_visibility_funnel"
        ],
        "done_text": "ASP stayed flat at Rs 500, and 60% of the 130 order drop came from 18 out of stock products like the 5L Pressure Cooker. The remaining loss is due to 14% cancellations and three overpriced SKUs; are you ready for the action plan?"
      },
      {
        "turn": 3,
        "user": "That makes sense. What exact actions should I take first?",
        "phase": "RECOMMEND",
        "tools": [
          "build_action_plan"
        ],
        "done_text": "Sure, Rajesh ji. First, restock the top 5 high velocity OOS SKUs - HK 006, HK 007, HK 008, HK 009, HK 010 - and set low stock alerts at 10 units (due within 48 hours). Next, sync inventory twice daily and dispatch ready orders before 2 PM each day (start today)."
      },
      {
        "turn": 4,
        "user": "Okay I agree, let's lock the dates.",
        "phase": "COMMIT",
        "tools": [
          "commit_action"
        ],
        "done_text": "Got it, Rajesh ji. I have noted your commitment with due dates and follow-up targets. Shall I send the summary?"
      },
      {
        "turn": 5,
        "user": "I commit to restocking the top 5 SKUs within 48 hours and syncing stock daily starting today.",
        "phase": "SUMMARY",
        "tools": [
          "send_call_summary"
        ],
        "done_text": "Got it, Rajesh ji. I've sent the summary with the three issues, your restocking commitment, and the follow up call in 3 days. Let's touch base then to review the results."
      },
      {
        "turn": 6,
        "user": "Yes, please send the call summary and schedule the follow-up.",
        "phase": "CLOSE",
        "tools": [],
        "done_text": "Sure, Rajesh ji. The call summary has been sent and your follow up is scheduled for three days from today. Thank you for your time, and I look forward to seeing the improvements."
      }
    ]
  },
  "proactive_milestones": {
    "greeting_done": {
      "text": "Rajesh ji, I noticed your sales dropped 32.5% to Rs 1,35,000 and 18 products are now out of stock. Do you have 2 minutes to review this together?",
      "speakable": "Rajesh ji, I noticed your sales dropped thirty-two point five percent to one lakh thirty-five thousand rupees and 18 products are now out of stock. Do you have 2 minutes to review this together?",
      "phase": "GREETING",
      "provider": "groq:openai/gpt-oss-120b",
      "commitments": [],
      "followup_schedule": null,
      "summary_card": null
    },
    "turn_phases": [
      {
        "turn": 0,
        "user": "Wait, why did my sales drop? Check everything and tell me.",
        "phase": "DIAGNOSE",
        "failover": [
          {
            "from": "groq:openai/gpt-oss-120b",
            "to": "gemini:gemini-3.5-flash-lite",
            "reason": "groq (openai/gpt-oss-120b) HTTP 429 rate limited: {\"error\":{\"message\":\"Rate limit reached for model `openai/gpt-oss-120b` in organization `o"
          }
        ],
        "tools": [
          "run_diagnosis",
          "get_out_of_stock_skus",
          "get_cancellation_breakdown",
          "get_pricing_gaps",
          "get_visibility_funnel"
        ],
        "done_text": "Got it, Rajesh ji. Your ASP stayed flat at Rs 500, and 60% of the 130-order drop came from 18 out-of-stock products like your 5L Pressure Cooker. Meanwhile, 14% cancellations and 3 overpriced products explain the rest, so are you ready for the action plan?"
      },
      {
        "turn": 1,
        "user": "Understood. What are your prioritised recommendations?",
        "phase": "RECOMMEND",
        "failover": [],
        "tools": [
          "build_action_plan"
        ],
        "done_text": "I understand, Rajesh ji. First, restock your top 5 out-of-stock items within 48 hours to recover up to Rs 36,000 monthly, and then we can fix your dispatch SLA. Shall we start with restocking those top items today?"
      },
      {
        "turn": 2,
        "user": "Okay I agree, let's lock dates.",
        "phase": "COMMIT",
        "failover": [],
        "tools": [
          "commit_action"
        ],
        "done_text": "Got it, Rajesh ji. I have noted your commitment with due dates and follow-up targets. Shall I send the summary?"
      },
      {
        "turn": 3,
        "user": "I commit to restocking top 5 SKUs within 48 hours and syncing stock daily starting today.",
        "phase": "SUMMARY",
        "failover": [],
        "tools": [
          "send_call_summary"
        ],
        "done_text": "Rajesh ji, I have sent the WhatsApp summary card detailing our action plan for restocking your top SKUs, fixing cancellations, and our follow-up in 3 days. Thank you for your time, and have a wonderful day ahead."
      },
      {
        "turn": 4,
        "user": "Yes, please send the call summary.",
        "phase": "CLOSE",
        "failover": [],
        "tools": [],
        "done_text": "Got it, Rajesh ji, I have sent the summary to your WhatsApp. Thank you so much for your time today, and wishing you great sales ahead!"
      }
    ]
  },
  "followup_milestones": {
    "prior_commitments": [
      {
        "action_id": "ACT-1",
        "action_summary": "Restock top 5 out-of-stock items",
        "owner": "Rajesh Kumar",
        "due_date": "Within 48 hours",
        "target_metric": "Out-of-stock products <= 8",
        "status": "partially_done"
      }
    ],
    "greeting_done": {
      "text": "Namaste Rajesh ji, it is Riya from Snapdeal following up. I am glad to see our sales pace jump from Rs 1,35,000 to Rs 1,75,500 and out-of-stock items drop from 18 to 9. Do you have two minutes to review the last two blockers?",
      "speakable": "Namaste Rajesh ji, it is Riya from Snapdeal following up. I am glad to see our sales pace jump from one lakh thirty-five thousand rupees to one lakh seventy-five thousand five hundred rupees and out-of-stock items drop from 18 to 9. Do you have two minutes to review the last two blockers?",
      "phase": "GREETING",
      "provider": "gemini:gemini-3.5-flash-lite",
      "commitments": [],
      "followup_schedule": null,
      "summary_card": null
    },
    "turn_phases": [
      {
        "turn": 0,
        "user": "Hi Riya, yes I restocked most items. How do my numbers look now?",
        "phase": "REVIEW_COMMITMENTS",
        "tools": [
          "run_diagnosis",
          "get_metric_comparison",
          "get_out_of_stock_skus",
          "get_pricing_gaps"
        ],
        "done_text": "Rajesh ji, your daily sales pace has jumped by 30 percent to Rs 1,75,500, and cancellations improved to 10.5 percent. We just need to close out item HK-009 and price item HK-021. Shall we tackle those today?"
      },
      {
        "turn": 1,
        "user": "Got it, what should I do for the remaining two blockers?",
        "phase": "RECOMMEND",
        "tools": [
          "build_action_plan"
        ],
        "done_text": "First, let us restock HK-009 within 48 hours to bring your out-of-stock items to eight or below. Second, let us reprice HK-021 from Rs 300 to Rs 280 and keep our 2 PM cutoff to cross the finish line. Does that sound good to you?"
      },
      {
        "turn": 2,
        "user": "I commit to expediting HK-009 within 48 hours and repricing HK-021 today.",
        "phase": "COMMIT",
        "tools": [
          "commit_action"
        ],
        "done_text": "Got it, Rajesh ji. I have noted your commitment with due dates and follow-up targets. Shall I send the summary?"
      },
      {
        "turn": 3,
        "user": "Please send the follow-up call summary.",
        "phase": "SUMMARY",
        "tools": [
          "schedule_followup",
          "send_call_summary"
        ],
        "done_text": "Rajesh ji, I have sent the WhatsApp summary card with your key issues and action items. We will review your progress on out-of-stock items and cancellations in three days at eleven in the morning. Thank you and have a wonderful day!"
      },
      {
        "turn": 4,
        "user": "Thank you, bye.",
        "phase": "CLOSE",
        "tools": [],
        "done_text": "Thank you so much, Rajesh ji, and have a wonderful day ahead!"
      }
    ]
  }
}
```


---

## 9. Backend gaps that affect the UI (fix these AFTER the packet is written, then update the contract)

All four backend gaps identified during the handoff audit have now been **implemented & verified (`v1`)**, and Section 3 + `docs/API_CONTRACT.md` reflect the updated contract:

1. **Raw Provider Error JSON Leaked in Failover UI — IMPLEMENTED (`v1`):**
   - *Files updated:* `app/llm/base.py`, `app/llm/openai_compat.py`, `app/llm/gemini_provider.py`, `app/llm/router.py`, `app/main.py`.
   - *Behaviour:* SSE `event: failover` now emits sanitized `{"type": "provider_failover", "from": "<provider>:<model>", "to": "<provider>:<model>", "reason_code": "rate_limited"|"forbidden"|"timeout"|"server_error"}` with **zero** raw upstream JSON and **zero** org/project identifiers (full upstream details are logged strictly on the server). SSE `event: error` is likewise sanitized (`provider:model (reason_code)` or generic failure text).
   - *Legacy UI note:* Unmodified `frontend/kam_brain.js:462` still reads `f.reason`, rendering `()` harmlessly after `from -> to` until the new frontend switches to `f.reason_code`.
2. **HTTP 403 Models Enter Permanent Process-Lifetime Disablement — IMPLEMENTED (`v1`):**
   - *Files updated:* `app/llm/base.py`, `app/llm/openai_compat.py`, `app/llm/gemini_provider.py`, `app/llm/router.py`, `app/main.py`.
   - *Behaviour:* HTTP `403` errors (`is_forbidden=True`, `reason_code="forbidden"`) call `router.mark_disabled(provider.key)` instead of placing the model in 3600s cooldown. `_ordered_candidates()` and `/api/health -> active_llm_chain` permanently exclude disabled models for the server process lifetime (and `/api/health -> disabled_providers` lists them), while HTTP `429` retains temporary cooldown semantics.
3. **Progressive UI Event Semantics (`tool_start`, `tool_result`, `phase_change`) — IMPLEMENTED (`v1`):**
   - *Files updated:* `app/agent/orchestrator.py`.
   - *Behaviour:* Every tool execution emits `tool_start` + backward-compatible `tool_call` before execution and `tool_result` (including `result` + `source`) on completion. Server-preexecuted phases emit all `tool_start` and `tool_result` events strictly before any spoken `token`/`sentence` events. `phase_change` (`{"phase", "previous_phase", "mode"}`) is emitted whenever `session.phase` transitions.
4. **Formal Versioned Contract Doc + Automated Contract Test — IMPLEMENTED (`v1`):**
   - *Files added:* `docs/API_CONTRACT.md` (`v1` specification) and `tests/test_api_contract.py` (automated pytest regression suite verifying REST endpoints, SSE event ordering, sanitized failovers/errors, and permanent 403 disablement).

# VoiceKAM Architecture Decision Records (ADRs)

---

### ADR-001: Central Single-Source Branding Configuration
- **Context:** Turning an assessment demo into a clean, reusable open-source template requires changing project and persona names without hunting across prompts, greetings, warmups, and HTML templates.
- **Decision:** Keep `PROJECT_NAME`, `AGENT_NAME`, `AGENT_NAME_DEVANAGARI`, `FOOTER_NOTICE`, and `PRIVACY_NOTICE` in [`app/config.py`](../app/config.py), expose them on `GET /api/health`, and hydrate all frontend copy dynamically (`frontend/app.js`, `frontend/audio.js`, `frontend/kam_brain.js`). Enforced by `tests/test_debranding.py`.
- **Trade-off:** Requires one lightweight `/api/health` bootstrap fetch on page load; fallback DOM defaults remain functional if offline.

### ADR-002: Server-Owned Phase State Machine & Compact Phase-Gated Tools
- **Context:** Small free-tier LLMs frequently skip diagnostic steps, loop in chit-chat, or hallucinate invalid JSON tool calls when handed 8+ tool schemas across an entire conversation.
- **Decision:** Keep conversation phases (`Phase`), phase transitions, and tool availability (`tools_for_phase`) strictly in [`app/agent/state_machine.py`](../app/agent/state_machine.py), pre-executing diagnostic and recommendation tools on the server inside [`AgentOrchestrator`](../app/agent/orchestrator.py).
- **Trade-off:** Slightly less free-form improvisation across turn order in exchange for near-100% phase completion and predictable sub-50-word voice pacing.

### ADR-003: Counterfactual Disjoint Attribution with Explicit `unexplained_residual`
- **Context:** Naive per-driver sums routinely attribute the same lost order to both stock-outs and visibility/pricing, making percentages sum above 100% or forcing numbers via hidden residual hacks.
- **Decision:** Allocate SKU decline budgets sequentially along disjoint cohorts (stock-outs using daily baseline velocity -> pricing gaps via in-stock fairly-priced control-group median DiD -> visibility impressions net of OOS/price -> seller-fault cancellations), attributing remaining unexplained drop explicitly to `unexplained_residual` ([ATTRIBUTION.md](ATTRIBUTION.md)). Missing optional columns report `"not assessable with the data provided"`.
- **Trade-off:** Order of attribution prioritizes physical availability (`stock_outs`) ahead of conversion (`pricing_gaps` / `visibility_drop`), which matches retail fulfillment causality.

### ADR-004: Ephemeral RAM-Only `SellerContext` & Bounded LRU/TTL Stores (`--workers 1`)
- **Context:** Free hosting tiers (`512 MB` RAM, ephemeral filesystem) penalize database dependencies, while accepting arbitrary user CSVs creates privacy liability if stored on disk.
- **Decision:** Remove all module-level seller globals; bind an immutable `SellerContext` to each `SessionState`, keep uploaded CSV/JSON datasets solely inside RAM (`UploadedDatasetStore` and `SessionStore`, default 2-hour TTL / 200-entry LRU), and run Uvicorn single-worker (`--workers 1`).
- **Trade-off:** State does not persist across process restarts or horizontal replicas unless an external session store is introduced later.

### ADR-005: Post-Generation Numeric Grounding Audit & Word-Boundary Signals
- **Context:** Conversational models occasionally extrapolate percentages, rupee savings, or future targets not computed by tools, and substring intent checks (e.g., `"ok"` inside `"look"` / `"broken"`) caused premature phase jumps.
- **Decision:** Collect all numbers returned by session tools in `session.grounded_numbers`, audit generated replies with `find_ungrounded_numbers()`, emit `ungrounded_warnings` in telemetry, use precompiled word-boundary regexes with same-utterance negation (`no/not/don't/nahi/na`), and slice chat history via `slice_recent_messages()` so tool sequences stay intact.
- **Trade-off:** Audit flags rather than silently re-writing every ungrounded number in live mode, maintaining sub-second streaming latency while giving evaluators exact traceability.

### ADR-006: Free-Tier Multi-Provider Failover & Public-Demo Quota Protection
- **Context:** Publicly reachable free demos risk exhausting free-tier RPD/TPM quotas or exposing upstream errors when shared by multiple visitors.
- **Decision:** Chain Groq (filtered live via `/models`), Gemini Flash, and OpenRouter in `LLMRouter` with per-provider `429` cooldowns and `403` lifetime disablement; enforce IP-based sliding-window limits (`X-Forwarded-For` first hop), `DAILY_TURN_CAP=400`, HTTP `413` body caps (`MAX_REQUEST_BYTES`), strict upload validation, and `/api/health` secret scrubbing in [`app/main.py`](../app/main.py).
- **Trade-off:** Public demo traffic is capped per day and per IP without requiring user accounts or authentication barriers.

### ADR-007: Non-Failing 2-Tier TTS Fallback Chain
- **Context:** Microsoft Edge neural TTS voices deliver superior Indian English and Devanagari Hindi quality locally, but cloud datacenter egress IPs can intermittently receive HTTP `403`/`429` throttles, and dedicated Gemini TTS models are not exposed cleanly via `generate_content` on this API endpoint (`400 INVALID_ARGUMENT` / `404`).
- **Decision:** Try `edge-tts` (with RAM LRU caching) -> return HTTP `200` JSON (`use_browser_tts: true`) so clients cleanly invoke `window.speechSynthesis` instead of failing calls on HTTP 5xx errors ([`app/tts/service.py`](../app/tts/service.py)).
- **Trade-off:** Voice timbre shifts gracefully between neural cloud audio and OS browser synthesis when running inside throttled hosting environments.

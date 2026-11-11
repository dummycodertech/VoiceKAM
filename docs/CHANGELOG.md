# Changelog

All notable changes on the `standalone` branch are logged here by phase.
The original v1.0 snapshot remains frozen on `main`.

---

## Phase 0 — Freeze and Branch
- Tagged `main` (`be6b582`) as an annotated release baseline without modifying `main`.
- Merged verified fix branch `round2-fixes` (`a6f9a1f`: follow-up `repriced_skus` object filter fix in `frontend/kam_brain.js`) into new branch `standalone` (`cbcfb2a`).
- Established baseline offline test suite (`31/31` passing across unit, reconciliation, failover, TTS/API, and `v1` contract suites).
- Added `docs/CHANGELOG.md` to track Phase 0 through Phase 9.

## Phase 1 — Full De-Branding
- Centralized project/agent identity and notices in `app/config.py` (`PROJECT_NAME = "VoiceKAM"`, `AGENT_NAME = "metis"`, `AGENT_NAME_DEVANAGARI = "metis"`, `FOOTER_NOTICE`, `PRIVACY_NOTICE`) and exposed `branding` via `GET /api/health`.
- Replaced legacy marketplace/persona names across backend prompts, greeting templates (`en-IN`, `hinglish`, `hi-IN`), TTS warmups/probes, tools, mock provider, seed fixture (`DEMO-DL-4821` / `Arjun Mehta` / `Metro Home & Kitchen` interim fixture), frontend UI copy/hydration, `pyproject.toml` (`voicekam`), `render.yaml` (`voicekam-virtual-kam`), `.env.example`, `run.ps1`, `run.sh`, `scripts/smoke_test.py`, and docs.
- Added footer notice (`"Prototype. Uses synthetic data. Not affiliated with any marketplace."`) in `frontend/index.html` and dynamic `/api/health` branding hydration in `frontend/app.js`, `frontend/audio.js`, and `frontend/kam_brain.js`.
- Removed legacy single-store docs and old captures (`docs/NOTE.md`, `docs/DEMO_SCRIPT.md`, `docs/FRONTEND_HANDOFF_part1.md`, `docs/FRONTEND_HANDOFF_part2.md`, `docs/captures/*`) from `standalone` (preserved on `main`).
- Added `tests/test_debranding.py` asserting zero legacy brand/persona strings repo-wide and single-source branding config.
- Added clean historyless export scripts (`scripts/export_clean_repo.sh` and `scripts/export_clean_repo.ps1`).

## Phase 2 — SellerContext and Custom Data
- Added `app/data/context.py` defining per-session immutable domain models (`SellerContext`, `SellerProfile`, `SkuRecord`, `CancellationRow`, `VisibilityFunnel`, `DataCompleteness`, `FollowupAdjustment`) and `compute_data_completeness()`.
- Refactored `app/agent/session.py` (`SessionState.seller_context`, TTL expiry, LRU bounded eviction), `app/engine/diagnosis.py`, `app/engine/tools.py`, `app/agent/state_machine.py`, `app/agent/orchestrator.py`, `app/llm/mock_provider.py`, and `app/main.py` so zero global seller constants are imported outside `app/data/seed_data.py`.
- Implemented strict RAM-only CSV and JSON upload validation in `app/data/upload.py` (max 1 MB / 2,000 SKUs, formula-injection blocking for leading `= + - @ \t \r`, HTML stripping, duplicate `sku_id` detection, bounded TTL `UploadedDatasetStore`).
- Added `GET /api/data/template` (`csv`, `cancellations_csv`, `json`), `POST /api/data/validate`, `GET /api/scenarios`, `GET /api/demo-data?scenario=...&dataset_id=...`, and multipart/JSON upload support on `POST /api/session/start`.
- Documented schema, graceful degradation rules (`"not assessable with the data provided"`), and RAM-only privacy controls in `docs/DATA_SCHEMA.md`.
- Added `tests/test_custom_data.py` and parameterized `tests/test_diagnosis.py` / `tests/test_reconciliation.py` (`42` offline unit/API/contract/debranding tests passing).

## Phase 3 — Computed Attribution and 4 Seeded Demo Scenarios
- Implemented `app/data/scenarios.py` with a deterministic scenario generator supporting Scenarios A–D completely distinct from the frozen baseline dataset:
  - **Scenario A (Default):** *UrbanStride Footwear & Apparel* (Bengaluru, 48 SKUs, Footwear & Activewear — stock-out dominant, ~57%).
  - **Scenario B:** *TechNest Accessories* (Mumbai, 42 SKUs — competitor price gaps & search visibility dominant).
  - **Scenario C:** *FreshBrew Pantry* (Pune, 36 SKUs — seller-caused cancellations & SLA delays dominant).
  - **Scenario D:** *ArtisanCraft Decor* (Jaipur, 40 SKUs — mixed operational causes with ~29% unexplained residual).
- Rewrote `app/engine/diagnosis.py` to compute attribution purely from SKU, funnel, and cancellation records (zero `driver_group` references): velocity × OOS duration for stock-outs, in-stock fairly-priced control-group median DiD for pricing gaps, impression decline × baseline CTR × CVR net of OOS/price overlaps for visibility, excess seller-fault cancellations, explicit `unexplained_residual`, confidence tiers (`high | medium | low | unavailable`), and follow-up single-record derivation from cloned `SellerContext` records.
- Documented formulas and reconciliation guarantees in `docs/ATTRIBUTION.md` and updated `docs/ASSUMPTIONS.md`.
- Added uncertain spoken phrasing (`"roughly"`, unexplained residual naming, and `"not assessable with the data provided"` prompts) in `app/agent/state_machine.py` and `app/llm/mock_provider.py`.
- Added property reconciliation tests (100 random fuzz trials) and 200-trial ground-truth recovery verification in `tests/test_attribution_properties.py` (`200/200` top-driver recovery in test fixture; `40` offline tests passing).

## Phase 4 — Agent Behaviour Cleanup
- Replaced substring checks in `app/agent/state_machine.py` with precompiled word-boundary regexes (`_DIAGNOSIS_REQUEST_RE`, `_BUY_IN_RE`, `_NEGATION_RE`) so substrings like `"look"` or `"broken"` never trigger `"ok"`, same-utterance negation (`"no"`, `"not"`, `"don't"`, `"nahi"`, `"na"`) cancels buy-in, and dead `_COMMIT_SIGNALS` was removed.
- Added `slice_recent_messages()` in `app/agent/orchestrator.py` to prevent history slicing (`session.messages[-10:]`) from starting on orphaned `role="tool"` messages or incomplete `assistant(tool_calls)` pairs.
- Updated `app/agent/speakable.py` with `int_to_indian_hindi()` / `decimal_to_spoken_hi()` (`हज़ार`, `लाख`, `करोड़`, `रुपये`, `प्रतिशत`) for `hi-IN`, generic & `SellerContext`-parameterized SKU masking in `find_ungrounded_numbers()`, and marketplace-agnostic recommendations (`"subject to eligibility"`).
- Added localized empty-reply fallbacks (`hi-IN`, `hinglish`, `en-IN`) in `app/agent/orchestrator.py`.
- Added `tests/test_agent_behavior.py` verifying word-boundary matching, negation, message slicing, `hi-IN` speakable conversions, custom SKU grounding, and localized fallbacks (`47` offline tests passing).

## Phase 5 — Frontend Upgrades
- Hydrated project name, agent name, privacy notice, footer notice, and seller labels dynamically from `/api/health`, `/api/scenarios`, and `/api/demo-data` in `frontend/index.html`, `frontend/app.js`, `frontend/audio.js`, and `frontend/kam_brain.js` (no hardcoded brand/agent literals in HTML/JS).
- Added the **Store Dataset** panel in `frontend/index.html` and `frontend/app.js` with Demo Scenario switcher (A–D), "Use my own data" mode, template download links (`/api/data/template?format=csv|json|cancellations_csv`), visible `PRIVACY_NOTICE`, inline validation error display (`#uploadError`), and pre-call `#datasetSummary` card.
- Updated `frontend/kam_brain.js` to handle missing optional columns (`Not assessable` without `null%`), 5-driver bar colors including `unexplained_residual`, confidence badges (`high | medium | low | unavailable`), and friendly API error parsing (including HTTP 429 messages).
- Fixed WebAudio lifecycle & animation efficiency: `frontend/audio.js` reuses `this.ctx` in `playRingtone()`, and `frontend/viz.js` pauses `requestAnimationFrame` when idle or hidden.
- Pruned unused CSS selectors, upgraded contrast tokens (`--dim`, `--mute`, `.btn.primary`) for WCAG AA readability, and verified 390px responsive layout with zero horizontal overflow in `frontend/styles.css`.
- Updated `tests/test_walking_skeleton_ui.py` and `tests/test_full_ui.py` to run hermetically (`ALLOW_MOCK_LLM=true`, `TTS_PROVIDER=browser`) covering Scenario A–D switching, broken/minimal CSV uploads, and 390px layout (`49` passing including UI & debranding suites).

## Phase 6 — Evaluation Harness
- Added `scripts/run_eval.py` and `Makefile` `eval` / `eval-live` targets evaluating 6 scripted personas (`cooperative`, `vague`, `frustrated`, `off_topic`, `interrupting`, `hinglish`) across Scenarios A–D (`N` configurable, default `1`, with `.eval_cache/` disk caching and exponential backoff for `--live`).
- Generated `docs/EVALUATION.md` from a real hermetic `--offline --n 1` run (`24` calls, `156` assistant turns: `100.0%` grounding, `100.0%` expected tool accuracy, `100.0%` phase completion, `23.5` avg / `34` max words per turn, `100.0%` commitments captured, `1.0 / 2.0 ms` local mock first-token latency; `first_audio_ms` and `LLM-as-judge` honestly reported as `not measured`).
- Added `.eval_cache/` to `.gitignore`.

## Phase 7 — Security and Repo Hygiene
- Added configurable per-IP sliding-window rate limiting (`RATE_LIMIT_CHAT_PER_10M`, `RATE_LIMIT_TTS_PER_10M`, `RATE_LIMIT_STT_PER_10M`, `RATE_LIMIT_STARTS_PER_HOUR`, `X-Forwarded-For` first-hop aware -> HTTP `429`), global `DAILY_TURN_CAP` (default `400` -> friendly SSE `error` event), configurable `CORS_ORIGINS`, `MAX_REQUEST_BYTES` HTTP `413` payload guard, security response headers middleware, and `/api/health` secret-pattern redaction in `app/config.py` and `app/main.py`.
- Added `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`, `docs/PRIVACY.md`, and `.github/workflows/tests.yml` (deterministic, hermetic Playwright UI, and offline eval checks).
- Added `tests/test_security_and_limits.py` covering rate limits, daily cap, CORS, headers, payload cap, TTL/LRU eviction, and `/api/health` scrubbing (`53` offline tests passing).
- Removed tracked legacy archive `files.zip` and untracked `newfrontend.zip`, ignored `*.zip` in `.gitignore`, and confirmed zero real credentials leaked across both working tree and full `git log -p --all` history (only local untracked `.env` holds runtime keys).

## Phase 8 — Documentation
- Rewrote `README.md` with VoiceKAM (`metis`) overview, Mermaid architecture diagram, local/Docker/Render quick-start commands, complete environment configuration table, Scenarios A–D comparison table, grounded baseline metrics copied from `docs/EVALUATION.md`, privacy note link, and synthetic prototype notice.
- Added `docs/ARCHITECTURE.md` (server-owned state machine, pre-execution, numeric grounding audit, multi-provider LLM failover, speakable TTS/STT flow, and ephemeral store limits).
- Added `docs/DECISIONS.md` (numbered ADRs covering branding config, phase/tool gating, counterfactual disjoint attribution, RAM-only single-worker persistence, numeric grounding & word-boundary intent rules, public quota limits, and 3-tier TTS fallback).
- Added `docs/RELATED_WORK.md` situating VoiceKAM alongside `ReAct` (`arXiv:2210.03629`), `Toolformer` (`arXiv:2302.04761`), `FActScore` (`arXiv:2305.14251`), and the W3C `Web Speech API` specification.
- Added `docs/DEMO_SCRIPT.md` (90-second walkthrough on Scenario A *UrbanStride Footwear & Apparel*, custom CSV template upload, and 3-day follow-up review) and `docs/PROJECT_SUMMARY.md` using verified metrics and explicit placeholders for unmeasured live voice metrics.
- Verified `tests/test_debranding.py` passes (`0` legacy strings across docs and code).

## Phase 9 — Deploy Prep
- Updated `render.yaml` (`voicekam-virtual-kam`, `plan: free`, `--workers 1`, zero impact on legacy Render services) with Phase 7 security, TTL, upload, and quota environment variables.
- Updated `.env.example`, `.dockerignore` (excluding `*.zip`, `.eval_cache/`, `frontend_old_backup/`), and `Dockerfile` (OCI metadata labels, `ALLOW_MOCK_LLM=false`, Python native `HEALTHCHECK`, single-worker CMD).
- Enhanced `scripts/smoke_test.py` to also check `GET /api/scenarios` (`>= 4` scenarios), `GET /api/data/template?format=csv`, and exact revenue reconciliation in `GET /api/demo-data`, and verified end-to-end against a booted Uvicorn instance (`ALL SMOKE CHECKS PASSED`).

## Round 3 — Hardening, Live Evaluation & Polish
1. **Disable non-functional Gemini TTS tier cleanly:** Verified live Gemini API catalog omits `*-tts` models and rejects `speechConfig` under `generation_config` (`400 INVALID_ARGUMENT` / `404`). Removed Gemini TTS from `app/tts/service.py`, `app/config.py`, `app/main.py` (`/api/health` now reports `"chain": ["edge-tts", "browser"]`), `.env.example`, and all documentation (`2-tier TTS: edge-tts -> browser speechSynthesis`), with tests asserting the 2-tier health schema.
2. **Line ending normalization (`.gitattributes`):** Added `.gitattributes` enforcing `eol=lf` across source, script, config, and Markdown files (preserving binaries), ran `git add --renormalize .`, refreshed working-copy checkouts to `lf`, and confirmed zero remaining `CRLF` entries in the index and a clean diff.
3. **Live multi-persona evaluation & strict separation (`make eval-live`):** Upgraded `scripts/run_eval.py` with disk call caching (`.eval_cache/`), exponential backoff retries, real server-side `edge-tts` `first_audio_ms` timing, strict cross-model LLM-as-judge enforcement (never self-judging), and explicit `<!-- BEGIN_LIVE_EVAL -->` / `<!-- BEGIN_OFFLINE_EVAL -->` separation in `docs/EVALUATION.md`. Executed `make eval-live` (`2 runs × 6 personas × 4 scenarios = 48 calls`): `45/48` calls succeeded (`3` failed on Gemini multi-turn tool ordering), `100.0%` (`292/292` turns, `604/604` numeric tokens) grounded, `100.0%` (`360/360`) expected phase tools, `93.8%` completion, `p50/p95` `2004.0 ms / 5840.0 ms` first-token and `3969.0 ms / 7966.0 ms` first-audio (`284` turns), and **`4.6 / 5.0`** cross-model judge score (`n=43/48`), plus an explicit note that synthetic causal-recovery figures are scoped to generated synthetic data only.
4. **Documentation audit, MIT holder & hygiene cleanup:** Updated the copyright holder in `LICENSE` to `dummycodertech`, updated `README.md` and `docs/PROJECT_SUMMARY.md` with strictly separated Live and Offline Mock evaluation metrics and realistic latency/audit claims, removed stale Gemini TTS references, softened overclaims in `docs/DECISIONS.md` / `docs/ARCHITECTURE.md` / `docs/VOICE_TEST_CHECKLIST.md`, and purged all remaining local paths and legacy context wording across docs and tests.
5. **Configurable per-language TTS pronunciation (`AGENT_NAME_SPOKEN_EN`, `AGENT_NAME_SPOKEN_HI`):** Added `AGENT_NAME_SPOKEN_EN` and `AGENT_NAME_SPOKEN_HI` defaults (`Settings.spoken_agent_name()`, `branding_dict()`, `.env.example`, `README.md`) and updated `to_speakable_text()`, `AgentOrchestrator`, startup TTS warmup, and `/api/tts-probe` so speech synthesis receives the tuned pronunciation without changing display UI captions or transcript text, verified by unit and orchestrator tests.

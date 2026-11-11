# Changelog

All notable changes on the `standalone` branch are logged here by phase.
The original assessment snapshot remains frozen on `main` at tag `v1.0-assessment`.

---

## Phase 0 — Freeze and Branch
- Tagged `main` (`be6b582`) as annotated tag `v1.0-assessment` without modifying `main`.
- Merged verified fix branch `round2-fixes` (`a6f9a1f`: follow-up `repriced_skus` object filter fix in `frontend/kam_brain.js`) into new branch `standalone` (`cbcfb2a`).
- Established baseline offline test suite (`31/31` passing across unit, reconciliation, failover, TTS/API, and `v1` contract suites).
- Added `docs/CHANGELOG.md` to track Phase 0 through Phase 9.

## Phase 1 — Full De-Branding
- Centralized project/agent identity and notices in `app/config.py` (`PROJECT_NAME = "VoiceKAM"`, `AGENT_NAME = "metis"`, `AGENT_NAME_DEVANAGARI = "metis"`, `FOOTER_NOTICE`, `PRIVACY_NOTICE`) and exposed `branding` via `GET /api/health`.
- Replaced legacy marketplace/persona names across backend prompts, greeting templates (`en-IN`, `hinglish`, `hi-IN`), TTS warmups/probes, tools, mock provider, seed fixture (`DEMO-DL-4821` / `Arjun Mehta` / `Metro Home & Kitchen` interim fixture), frontend UI copy/hydration, `pyproject.toml` (`voicekam`), `render.yaml` (`voicekam-virtual-kam`), `.env.example`, `run.ps1`, `run.sh`, `scripts/smoke_test.py`, and docs.
- Added footer notice (`"Prototype. Uses synthetic data. Not affiliated with any marketplace."`) in `frontend/index.html` and dynamic `/api/health` branding hydration in `frontend/app.js`, `frontend/audio.js`, and `frontend/kam_brain.js`.
- Removed assessment-specific docs and old captures (`docs/NOTE.md`, `docs/DEMO_SCRIPT.md`, `docs/INTERVIEW_PREP.md`, `docs/FRONTEND_HANDOFF_part1.md`, `docs/FRONTEND_HANDOFF_part2.md`, `docs/captures/*`) from `standalone` (preserved on `main` at `v1.0-assessment`).
- Added `tests/test_debranding.py` asserting zero legacy brand/persona strings repo-wide and single-source branding config.
- Added clean historyless export scripts (`scripts/export_clean_repo.sh` and `scripts/export_clean_repo.ps1`).

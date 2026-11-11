# Contributing to VoiceKAM

Thank you for helping improve **VoiceKAM**! Keep pull requests focused, tested, and brand-neutral.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install pytest pytest-asyncio playwright
playwright install chromium
```

## Running Tests & Evaluation

```bash
# Full hermetic suite (unit, property, contract, de-branding, and Playwright UI)
pytest -k "not live_llm" -v

# Re-generate docs/EVALUATION.md offline across 6 personas x 4 scenarios
make eval
```

## Ground Rules

1. **Deterministic attribution:** All numerical calculations belong in `app/engine/diagnosis.py`, never in LLM prompts or text templates. Any new driver must reconcile `sum(drivers + residual) == total_change`.
2. **Central branding:** Read project and agent identifiers from `app/config.py` / `/api/health` rather than hardcoding literals.
3. **Synthetic data only:** Never commit real seller catalogs, credentials, or `.env` files. Run `tests/test_debranding.py` before opening a PR.
4. **Tests:** Add regression coverage in `tests/` for any bug fix, schema rule, or attribution change, and keep `docs/API_CONTRACT.md` backwards-compatible.

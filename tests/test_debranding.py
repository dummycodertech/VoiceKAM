"""Verify full de-branding across the repository and single-source branding config."""

from __future__ import annotations

import pathlib
import re

from app.config import (
    AGENT_NAME,
    AGENT_NAME_DEVANAGARI,
    AGENT_NAME_SPOKEN_EN,
    AGENT_NAME_SPOKEN_HI,
    FOOTER_NOTICE,
    PROJECT_NAME,
    Settings,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

_SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "scratch",
    "frontend_old_backup",
    "node_modules",
    ".eval_cache",
}

_SKIP_EXTS = {
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".mp3",
    ".wav",
    ".exe",
    ".pyc",
    ".pyo",
}

_BANNED_REGEX = re.compile(
    "|".join(
        [
            r"snap" + r"deal",
            r"snap\s+" + r"deal",
            r"\b" + r"ri" + r"ya\b",
            "\u0930\u093f\u092f\u093e",
            r"ku" + r"mar\s+home",
            r"\b" + r"ku" + r"mar\b",
            r"\b" + r"raj" + r"esh\b",
            r"mock-dl-" + r"4821",
            "\u0930\u093e\u091c\u0947\u0936",
            "\u0915\u0941\u092e\u093e\u0930",
            "\u0938\u094d\u0928\u0948\u092a\u0921\u0940\u0932",
        ]
    ),
    re.IGNORECASE,
)

_EXACT_CASE_PATTERNS = [
    re.compile(r"\bSD "),
]

_ALLOWED_README_URLS = (
    "https://" + "snap" + "deal-virtual-kam.onrender.com",
)


def test_repo_contains_no_legacy_brand_or_persona_strings():
    findings: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*")):
        rel = path.relative_to(REPO_ROOT)
        if any(part in _SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        if not path.is_file() or path.suffix.lower() in _SKIP_EXTS:
            continue
        rel_posix = rel.as_posix()
        if rel_posix == "docs/CHANGELOG.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        if rel_posix == "README.md":
            for allowed_url in _ALLOWED_README_URLS:
                text = text.replace(allowed_url, "")

        matches = _BANNED_REGEX.findall(text)
        for pat in _EXACT_CASE_PATTERNS:
            matches.extend(pat.findall(text))
        if matches:
            findings.append(f"{rel_posix}: {sorted(set(matches))}")

    assert findings == [], "Legacy brand or persona strings found:\n" + "\n".join(findings)


def test_central_branding_config_single_source_of_truth():
    s = Settings()
    assert s.project_name == PROJECT_NAME == "VoiceKAM"
    assert s.agent_name == AGENT_NAME == "Metis"
    assert s.agent_name_devanagari == AGENT_NAME_DEVANAGARI == "Metis"
    assert s.agent_name_spoken_en == AGENT_NAME_SPOKEN_EN == "Metis"
    assert s.agent_name_spoken_hi == AGENT_NAME_SPOKEN_HI == "Metis"
    assert s.footer_notice == FOOTER_NOTICE
    branding = s.branding_dict()
    assert branding["project_name"] == s.project_name
    assert branding["agent_name"] == s.agent_name
    assert branding["agent_name_devanagari"] == s.agent_name_devanagari
    assert branding["agent_name_spoken_en"] == s.spoken_agent_name("en-IN")
    assert branding["agent_name_spoken_hi"] == s.spoken_agent_name("hi-IN")
    assert "not affiliated with any marketplace" in branding["footer_notice"].lower()

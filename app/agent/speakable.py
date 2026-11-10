"""Speakable-text normalizer (rupee amounts, percentages, abbreviations) and number-grounding checker."""

from __future__ import annotations

import re
from typing import Any

# Common Indian marketplace / voice phrasing expansions
_ABBREV_MAP = [
    (re.compile(r"\bSKUs\b"), "products"),
    (re.compile(r"\bSKU\b"), "product"),
    (re.compile(r"\bOOS\b"), "out of stock"),
    (re.compile(r"\bSLAs\b"), "dispatch deadlines"),
    (re.compile(r"\bSLA\b"), "dispatch deadline"),
    (re.compile(r"\bGMV\b"), "total sales"),
    (re.compile(r"\bASP\b"), "average order price"),
    (re.compile(r"\bCTR\b"), "click rate"),
    (re.compile(r"\bCVR\b"), "conversion rate"),
    (re.compile(r"\bCOD\b"), "cash on delivery"),
    (re.compile(r"\bKAM\b"), "account manager"),
]

_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _two_digits_en(n: int) -> str:
    if n < 20:
        return _ONES[n]
    t, u = divmod(n, 10)
    return _TENS[t] + (f"-{_ONES[u]}" if u else "")


def _under_thousand_en(n: int) -> str:
    h, rem = divmod(n, 100)
    parts = []
    if h:
        parts.append(f"{_ONES[h]} hundred")
    if rem:
        parts.append(_two_digits_en(rem))
    return " ".join(parts)


def int_to_indian_english(n: int) -> str:
    """Convert integer into natural Indian numbering words (lakh / crore / thousand)."""
    if n == 0:
        return "zero"
    if n < 0:
        return "minus " + int_to_indian_english(-n)
    parts = []
    crore, rem = divmod(n, 10_000_000)
    if crore:
        parts.append(f"{int_to_indian_english(crore)} crore")
    lakh, rem = divmod(rem, 100_000)
    if lakh:
        parts.append(f"{_two_digits_en(lakh)} lakh")
    thousand, rem = divmod(rem, 1_000)
    if thousand:
        parts.append(f"{_two_digits_en(thousand)} thousand")
    if rem:
        parts.append(_under_thousand_en(rem))
    return " ".join(parts)


def decimal_to_spoken_en(num_str: str) -> str:
    clean = num_str.replace(",", "").strip()
    if "." in clean:
        whole_s, frac_s = clean.split(".", 1)
        whole_words = int_to_indian_english(int(whole_s)) if whole_s else "zero"
        frac_words = " ".join(_ONES[int(d)] if int(d) > 0 else "zero" for d in frac_s if d.isdigit())
        return f"{whole_words} point {frac_words}".strip()
    return int_to_indian_english(int(clean))


def strip_reasoning_and_markdown(text: str) -> str:
    """Strip <think> blocks, markdown formatting, emojis, and excess whitespace."""
    # Normalize smart typography before character filtering so contractions don't split ("You'll" != "You ll")
    text = (
        text.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2014", " - ")
        .replace("\u2013", " - ")
        .replace("→", "->")
    )
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</?think>", " ", text, flags=re.IGNORECASE)
    # Strip markdown headings, bold/italic, inline code, bullet markers
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Strip emojis / pictographs while preserving ASCII/Devanagari letters, punctuation, <= >= + & ₹
    text = re.sub(r"[^\w\s.,?!:;'\-\"/%₹()<>+=&\u0900-\u097F\u0964]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def to_speakable_text(text: str, language: str = "en-IN") -> str:
    """Normalize caption text into natural TTS-ready speech."""
    s = strip_reasoning_and_markdown(text)
    if not s:
        return ""

    # Replace arrows and ranges
    s = s.replace("->", " to ")
    s = s.replace("–", " to ")
    s = re.sub(r"\s+to\s+to\s+", " to ", s)

    # Expand abbreviations and SKU codes like HK-006 -> product H K 006 or product
    for pattern, repl in _ABBREV_MAP:
        s = pattern.sub(repl, s)

    # Convert SKU IDs like HK-006 into clear speech ("H K 6")
    def _sku_repl(m: re.Match[str]) -> str:
        num = int(m.group(1))
        return f"product {num}"

    s = re.sub(r"\bHK-0*(\d+)\b", _sku_repl, s)

    # Convert rupee amounts: Rs 2,00,000 | Rs. 500 | ₹1,35,000 | INR 65,000
    currency_word = "rupaye" if language in ("hi-IN", "hinglish") else "rupees"

    def _rupee_repl(m: re.Match[str]) -> str:
        raw_num = m.group(1).replace(",", "")
        try:
            if "." in raw_num:
                spoken = decimal_to_spoken_en(raw_num)
            else:
                spoken = int_to_indian_english(int(raw_num))
            return f"{spoken} {currency_word}"
        except ValueError:
            return m.group(0)

    s = re.sub(r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", _rupee_repl, s, flags=re.IGNORECASE)

    # Convert percentages: 32.5% -> thirty-two point five percent (or pratishat/percent)
    pct_word = "percent"

    def _pct_repl(m: re.Match[str]) -> str:
        raw_num = m.group(1).replace(",", "")
        try:
            spoken = decimal_to_spoken_en(raw_num)
            return f"{spoken} {pct_word}"
        except ValueError:
            return m.group(0)

    s = re.sub(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*%", _pct_repl, s)

    # Clean <= and >= symbols
    s = s.replace("<=", "at most ").replace(">=", "at least ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def enforce_voice_length(text: str, phase: str, max_words: int = 45) -> str:
    """Keep spoken turns crisp and within the voice word-count budget without ugly mid-phrase cuts."""
    clean = strip_reasoning_and_markdown(text)
    limit = 75 if phase == "SUMMARY" else max_words
    soft_limit = 82 if phase == "SUMMARY" else (55 if phase in ("DIAGNOSE", "RECOMMEND") else limit)
    words = clean.split()
    if len(words) <= limit:
        return clean

    # Trim cleanly at sentence boundary inside soft_limit if possible
    sentences = re.split(r"(?<=[.?!\u0964])\s+", clean)
    kept: list[str] = []
    running = 0
    for sent in sentences:
        sw = len(sent.split())
        if running + sw <= soft_limit or not kept:
            kept.append(sent)
            running += sw
        else:
            break
    candidate = " ".join(kept).strip()
    c_words = candidate.split()
    if len(c_words) <= soft_limit:
        return candidate
    trimmed = " ".join(c_words[:limit]).rstrip(",;:-")
    if trimmed and trimmed[-1] not in ".?!\u0964":
        trimmed += "."
    return trimmed


def extract_numbers_from_obj(obj: Any) -> set[float]:
    """Recursively collect all numeric values (and string-embedded numbers) from tool output."""
    nums: set[float] = set()
    if isinstance(obj, bool):
        return nums
    if isinstance(obj, (int, float)):
        val = float(obj)
        nums.add(val)
        nums.add(abs(val))
        return nums
    if isinstance(obj, str):
        for m in re.finditer(r"-?[0-9][0-9,]*(?:\.[0-9]+)?", obj):
            raw = m.group(0).replace(",", "")
            try:
                val = float(raw)
                nums.add(val)
                nums.add(abs(val))
            except ValueError:
                pass
        return nums
    if isinstance(obj, dict):
        for v in obj.values():
            nums.update(extract_numbers_from_obj(v))
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            nums.update(extract_numbers_from_obj(item))
    return nums


# Non-metric conversational numbers allowed in natural dialogue (e.g. "2 minutes", "3 days", "48 hours", "2 PM", "top 5")
_CONVERSATIONAL_ALLOW_PATTERNS = [
    re.compile(r"\b\d+\s*(?:minutes?|mins?|days?|hours?|hrs?|weeks?|months?|seconds?|pm|am)\b", re.IGNORECASE),
    re.compile(r"\b(?:priority|top|step|action|option|number)\s*\d+\b", re.IGNORECASE),
    re.compile(r"\bHK-\d+\b", re.IGNORECASE),
    re.compile(r"\bACT-(?:FU-)?\d+\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*(?:to|-)\s*\d+\b"),
    re.compile(r"\b5L\b|\b28cm\b|\b3Pc\b|\b12Pc\b|\b1\.5L\b|\b5Pc\b", re.IGNORECASE),
]


def find_ungrounded_numbers(reply: str, grounded_numbers: set[float]) -> list[str]:
    """Return any metric numbers in reply that cannot be traced to tool outputs."""
    if not reply or not grounded_numbers:
        return []

    masked = reply
    for pat in _CONVERSATIONAL_ALLOW_PATTERNS:
        masked = pat.sub(" ", masked)

    ungrounded: list[str] = []
    # Also include rounded integer & 1-decimal forms of grounded numbers
    expanded: set[float] = set(grounded_numbers)
    for g in list(grounded_numbers):
        expanded.add(round(g))
        expanded.add(round(g, 1))

    for m in re.finditer(r"(?<![A-Za-z0-9_])([0-9][0-9,]*(?:\.[0-9]+)?)", masked):
        token = m.group(1)
        try:
            val = float(token.replace(",", ""))
        except ValueError:
            continue
        # Small ordinals 1..3 used naturally in phrasing ("1 question", "2 actions")
        if val in (1.0, 2.0, 3.0):
            continue
        if any(abs(val - g) <= 0.05 for g in expanded):
            continue
        ungrounded.append(token)
    return ungrounded


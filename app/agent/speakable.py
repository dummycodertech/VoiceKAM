"""Speakable-text normalizer (en-IN/hi-IN rupee & percentage phrasing) and SellerContext grounding checker."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable

from app.config import Settings, get_settings

if TYPE_CHECKING:
    from app.data.context import SellerContext

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

# Standard Hindi number words (0..99) in Devanagari for natural hi-IN speech
_HI_NUMBERS_0_99 = [
    "शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ",
    "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस",
    "बीस", "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस",
    "तीस", "इकतीस", "बत्तीस", "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस",
    "चालीस", "इकतालीस", "बयालीस", "तैंतालीस", "चवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास",
    "पचास", "इक्यावन", "बावन", "तिरेपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ",
    "साठ", "इकसठ", "बासठ", "तिरसठ", "चौंसठ", "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ", "उनहत्तर",
    "सत्तर", "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उनासी",
    "अस्सी", "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सत्तासी", "अट्ठासी", "नवासी",
    "नब्बे", "इक्यानवे", "बानवे", "तिरानवे", "चौरानवे", "पचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे", "निन्यानवे",
]


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


def _under_thousand_hi(n: int) -> str:
    h, rem = divmod(n, 100)
    parts = []
    if h:
        parts.append(f"{_HI_NUMBERS_0_99[h]} सौ")
    if rem:
        parts.append(_HI_NUMBERS_0_99[rem])
    return " ".join(parts)


def int_to_indian_hindi(n: int) -> str:
    """Convert integer into natural Indian Hindi words (हज़ार / लाख / करोड़)."""
    if n == 0:
        return _HI_NUMBERS_0_99[0]
    if n < 0:
        return "माइनस " + int_to_indian_hindi(-n)
    parts = []
    crore, rem = divmod(n, 10_000_000)
    if crore:
        parts.append(f"{int_to_indian_hindi(crore)} करोड़")
    lakh, rem = divmod(rem, 100_000)
    if lakh:
        parts.append(f"{_HI_NUMBERS_0_99[lakh]} लाख")
    thousand, rem = divmod(rem, 1_000)
    if thousand:
        parts.append(f"{_HI_NUMBERS_0_99[thousand]} हज़ार")
    if rem:
        parts.append(_under_thousand_hi(rem))
    return " ".join(parts)


def decimal_to_spoken_en(num_str: str) -> str:
    clean = num_str.replace(",", "").strip()
    if "." in clean:
        whole_s, frac_s = clean.split(".", 1)
        whole_words = int_to_indian_english(int(whole_s)) if whole_s else "zero"
        frac_words = " ".join(_ONES[int(d)] if int(d) > 0 else "zero" for d in frac_s if d.isdigit())
        return f"{whole_words} point {frac_words}".strip()
    return int_to_indian_english(int(clean))


def decimal_to_spoken_hi(num_str: str) -> str:
    clean = num_str.replace(",", "").strip()
    if "." in clean:
        whole_s, frac_s = clean.split(".", 1)
        whole_words = int_to_indian_hindi(int(whole_s)) if whole_s else _HI_NUMBERS_0_99[0]
        frac_words = " ".join(_HI_NUMBERS_0_99[int(d)] for d in frac_s if d.isdigit())
        return f"{whole_words} दशमलव {frac_words}".strip()
    return int_to_indian_hindi(int(clean))


def strip_reasoning_and_markdown(text: str) -> str:
    """Strip <think> blocks, markdown formatting, emojis, and excess whitespace."""
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
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"[^\w\s.,?!:;'\-\"/%₹()<>+=&\u0900-\u097F\u0964]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def to_speakable_text(
    text: str,
    language: str = "en-IN",
    seller_context: SellerContext | None = None,
    settings: Settings | None = None,
) -> str:
    """Normalize caption text into natural TTS-ready speech in en-IN, hinglish, or hi-IN."""
    s = strip_reasoning_and_markdown(text)
    if not s:
        return ""

    s = s.replace("->", " टू " if language == "hi-IN" else " to ")
    s = s.replace("–", " टू " if language == "hi-IN" else " to ")
    s = re.sub(r"\s+to\s+to\s+", " to ", s)

    for pattern, repl in _ABBREV_MAP:
        s = pattern.sub(repl, s)

    # Expand explicit SellerContext SKU IDs first, then generic ALPHA-001 style SKU identifiers
    if seller_context is not None and seller_context.skus:
        for sku in seller_context.skus:
            if sku.sku_id and sku.sku_id in s:
                m_digits = re.search(r"(\d+)$", sku.sku_id)
                if m_digits:
                    lbl = f"प्रोडक्ट {int(m_digits.group(1))}" if language == "hi-IN" else f"product {int(m_digits.group(1))}"
                    s = re.sub(rf"\b{re.escape(sku.sku_id)}\b", lbl, s)

    def _generic_sku_repl(m: re.Match[str]) -> str:
        num = int(m.group(1))
        return f"प्रोडक्ट {num}" if language == "hi-IN" else f"product {num}"

    s = re.sub(r"\b[A-Z]{2,8}-0*(\d+)\b", _generic_sku_repl, s)

    # Convert rupee amounts: Rs 2,00,000 | Rs. 500 | ₹1,35,000 | INR 65,000
    if language == "hi-IN":
        currency_word = "रुपये"
    elif language == "hinglish":
        currency_word = "rupaye"
    else:
        currency_word = "rupees"

    def _rupee_repl(m: re.Match[str]) -> str:
        raw_num = m.group(1).replace(",", "")
        try:
            if language == "hi-IN":
                spoken = decimal_to_spoken_hi(raw_num)
            else:
                spoken = decimal_to_spoken_en(raw_num)
            return f"{spoken} {currency_word}"
        except ValueError:
            return m.group(0)

    s = re.sub(r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", _rupee_repl, s, flags=re.IGNORECASE)

    # Convert percentages cleanly
    pct_word = "प्रतिशत" if language == "hi-IN" else "percent"

    def _pct_repl(m: re.Match[str]) -> str:
        raw_num = m.group(1).replace(",", "")
        try:
            spoken = decimal_to_spoken_hi(raw_num) if language == "hi-IN" else decimal_to_spoken_en(raw_num)
            return f"{spoken} {pct_word}"
        except ValueError:
            return m.group(0)

    s = re.sub(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*%", _pct_repl, s)

    s = s.replace("<=", "कम से कम " if False else ("अधिकतम " if language == "hi-IN" else "at most "))
    s = s.replace(">=", "न्यूनतम " if language == "hi-IN" else "at least ")

    cfg = settings or get_settings()
    spoken_name = cfg.spoken_agent_name(language)
    if spoken_name:
        display_candidates = sorted(
            {c.strip() for c in (cfg.agent_name, cfg.agent_name_devanagari) if c and c.strip()},
            key=len,
            reverse=True,
        )
        for display_name in display_candidates:
            if display_name != spoken_name:
                pattern = re.compile(
                    rf"(?<![A-Za-z0-9_\u0900-\u097F]){re.escape(display_name)}(?![A-Za-z0-9_\u0900-\u097F])",
                    re.IGNORECASE,
                )
                s = pattern.sub(lambda _m, rep=spoken_name: rep, s)

    s = re.sub(r"\s+", " ", s).strip()
    return s


def enforce_voice_length(text: str, phase: str, max_words: int = 45) -> str:
    """Keep spoken turns crisp and within the voice word-count budget without mid-phrase cuts."""
    clean = strip_reasoning_and_markdown(text)
    limit = 75 if phase == "SUMMARY" else max_words
    soft_limit = 82 if phase == "SUMMARY" else (55 if phase in ("DIAGNOSE", "RECOMMEND") else limit)
    words = clean.split()
    if len(words) <= limit:
        return clean

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


# Non-metric conversational numbers allowed in natural dialogue (dataset-agnostic)
_CONVERSATIONAL_ALLOW_PATTERNS = [
    re.compile(r"\b\d+\s*(?:minutes?|mins?|days?|hours?|hrs?|weeks?|months?|seconds?|pm|am)\b", re.IGNORECASE),
    re.compile(r"\b(?:priority|top|step|action|option|number|scenario)\s*\d+\b", re.IGNORECASE),
    re.compile(r"\bACT-(?:FU-)?\d+\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]{2,8}-\d+\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*(?:to|-)\s*\d+\b"),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:L|ml|kg|g|cm|mm|m|Pc|pcs|W|GHz|GB|MB)\b", re.IGNORECASE),
    re.compile(r"\b\d+-in-\d+\b|\b\d+-Pack\b|\b\d+pc\b", re.IGNORECASE),
]


def find_ungrounded_numbers(
    reply: str,
    grounded_numbers: set[float],
    seller_context: SellerContext | None = None,
    allowed_sku_ids: Iterable[str] | None = None,
) -> list[str]:
    """Return any metric numbers in reply that cannot be traced to tool outputs."""
    if not reply or not grounded_numbers:
        return []

    masked = reply
    # Mask any explicit SellerContext SKU IDs first so digits inside custom SKU IDs are never flagged
    sku_id_pool: list[str] = []
    if seller_context is not None:
        sku_id_pool.extend(s.sku_id for s in seller_context.skus if s.sku_id)
    if allowed_sku_ids is not None:
        sku_id_pool.extend(str(sid) for sid in allowed_sku_ids if sid)
    for sid in sku_id_pool:
        if sid in masked:
            masked = re.sub(re.escape(sid), " SKU_ID ", masked)

    for pat in _CONVERSATIONAL_ALLOW_PATTERNS:
        masked = pat.sub(" ", masked)

    ungrounded: list[str] = []
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
        if val in (1.0, 2.0, 3.0):
            continue
        if any(abs(val - g) <= 0.05 for g in expanded):
            continue
        ungrounded.append(token)
    return ungrounded

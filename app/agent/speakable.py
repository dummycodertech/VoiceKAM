"""Speakable-text normalizer (en-IN/hi-IN rupee & percentage phrasing) and SellerContext grounding checker."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable

from app.config import Settings, get_settings
from app.utils.formatting import format_indian_number, format_inr, normalize_indian_grouping

if TYPE_CHECKING:
    from app.data.context import SellerContext

# Common Indian marketplace / voice phrasing expansions (case-insensitive where safe)
_ABBREV_MAP = [
    (re.compile(r"\bvisibility\s*(?:&|and)\s*funnel\b", re.IGNORECASE), "search visibility"),
    (re.compile(r"\bfunnels?\b", re.IGNORECASE), "search visibility"),
    (re.compile(r"\bSKUs?\(s\)", re.IGNORECASE), "products"),
    (re.compile(r"\bSKUs\b", re.IGNORECASE), "products"),
    (re.compile(r"\bSKU\b", re.IGNORECASE), "product"),
    (re.compile(r"\bOOS\b", re.IGNORECASE), "out of stock"),
    (re.compile(r"\bSLAs\b", re.IGNORECASE), "dispatch deadlines"),
    (re.compile(r"\bSLA\b", re.IGNORECASE), "dispatch deadline"),
    (re.compile(r"\bGMV\b", re.IGNORECASE), "total sales"),
    (re.compile(r"\bASP\b", re.IGNORECASE), "average order price"),
    (re.compile(r"\bCTR\b", re.IGNORECASE), "click rate"),
    (re.compile(r"\bCVR\b", re.IGNORECASE), "conversion rate"),
    (re.compile(r"\bCOD\b", re.IGNORECASE), "cash on delivery"),
    (re.compile(r"\bKAM\b", re.IGNORECASE), "account manager"),
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


def _cap_product_names(text: str, sku_names: list[str], max_names: int = 2) -> str:
    """Ensure at most max_names distinct catalog product names appear in one spoken turn."""
    unique_names = sorted({n.strip() for n in sku_names if n and len(n.strip()) >= 3}, key=len, reverse=True)
    if not unique_names:
        return text
    found: list[tuple[int, str]] = []
    for name in unique_names:
        m = re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text)
        if m:
            found.append((m.start(), name))
    found.sort(key=lambda pair: pair[0])
    if len(found) <= max_names:
        return text
    drop_names = [name for _, name in found[max_names:]]
    out = text
    for name in drop_names:
        out = re.sub(
            rf"(?:,\s*(?:and\s+)?|\s+and\s+)?(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            "",
            out,
        )
    out = re.sub(r"\(\s*,?\s*\)", "", out)
    out = re.sub(r",\s*,+", ",", out)
    out = re.sub(r"\s+([.,?!])", r"\1", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _strip_placeholder_names(text: str, seller_context: SellerContext | None = None) -> str:
    """Remove synthetic placeholders ('Seller Partner', 'Custom Uploaded Store') from spoken copy."""
    real_seller = ""
    real_shop = ""
    if seller_context is not None:
        sn = (seller_context.profile.seller_name or "").strip()
        sh = (seller_context.profile.shop_name or "").strip()
        if sn and sn.lower() not in ("seller partner", "custom uploaded store"):
            real_seller = sn
        if sh and sh.lower() not in ("custom uploaded store", "uploaded store"):
            real_shop = sh

    out = text
    if real_seller:
        out = re.sub(r"\bSeller\s+Partner\b", real_seller, out, flags=re.IGNORECASE)
    else:
        out = re.sub(r"\bfor\s+Seller\s+Partner\b", "for you", out, flags=re.IGNORECASE)
        out = re.sub(r",\s*Seller\s+Partner\b", "", out, flags=re.IGNORECASE)
        out = re.sub(r"\bSeller\s+Partner\s*,?\s*", "", out, flags=re.IGNORECASE)

    if real_shop:
        out = re.sub(r"\b(?:Custom\s+)?Uploaded\s+Store\b", real_shop, out, flags=re.IGNORECASE)
    else:
        out = re.sub(r"\b(?:Custom\s+)?Uploaded\s+Store\b", "your store", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+([.,?!])", r"\1", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def enforce_spoken_cadence_and_register(
    text: str,
    *,
    session: Any,
    turn_count: int,
) -> str:
    """Enforce spoken register (clean codes, no .0%, <=2 products, <=1 eligibility, salutation/'Got it' cadence)."""
    if not text:
        return ""
    ctx = getattr(session, "seller_context", None)
    lang = getattr(session, "language", "en-IN")
    out = _strip_placeholder_names(text, seller_context=ctx)

    # Remove .0% decimals (50.0% -> 50%) while preserving 32.5%
    out = re.sub(r"\b(\d+)\.0+\s*%", r"\1%", out)

    # Replace product codes in text with human names (and strip parenthetical code lists)
    out = re.sub(r"\(\s*[A-Z]{2,8}-\d+(?:\s*,\s*[A-Z]{2,8}-\d+)*\s*\)", "", out)
    if ctx is not None and ctx.skus:
        for sku in ctx.skus:
            if sku.sku_id and re.search(rf"\b{re.escape(sku.sku_id)}\b", out):
                rep = sku.name or "that product"
                out = re.sub(rf"\b{re.escape(sku.sku_id)}\b", rep, out)
        out = _cap_product_names(out, [s.name for s in ctx.skus if s.name], max_names=2)
    out = re.sub(r"\b(?!ACT-)[A-Z]{2,8}-\d+\b", "that product", out)

    # Replace jargon terms even in displayed spoken turns
    out = re.sub(r"\bvisibility\s*(?:&|and)\s*funnel\b", "search visibility", out, flags=re.IGNORECASE)
    out = re.sub(r"\bfunnels?\b", "search visibility", out, flags=re.IGNORECASE)
    out = re.sub(r"\bSKUs?\(s\)", "products", out, flags=re.IGNORECASE)
    out = re.sub(r"\bSKUs\b", "products", out, flags=re.IGNORECASE)
    out = re.sub(r"\bSKU\b", "product", out, flags=re.IGNORECASE)
    out = re.sub(r"\bOOS\b", "out of stock", out, flags=re.IGNORECASE)
    out = re.sub(r"\bASP\b", "average price", out, flags=re.IGNORECASE)

    # Subject to eligibility at most once per call
    elig_re = re.compile(r"\bsubject\s+to\s+eligibility\b", re.IGNORECASE)
    if elig_re.search(out):
        if getattr(session, "subject_to_eligibility_count", 0) >= 1:
            out = re.sub(r",?\s*\bsubject\s+to\s+eligibility\b", "", out, flags=re.IGNORECASE)
        else:
            matches = list(elig_re.finditer(out))
            if len(matches) > 1:
                first_end = matches[0].end()
                head = out[:first_end]
                tail = re.sub(r",?\s*\bsubject\s+to\s+eligibility\b", "", out[first_end:], flags=re.IGNORECASE)
                out = head + tail
            session.subject_to_eligibility_count = getattr(session, "subject_to_eligibility_count", 0) + 1

    # "Got it" at most once per 3 turns
    got_it_re = re.compile(r"\bGot\s+it\b", re.IGNORECASE)
    if got_it_re.search(out):
        last_gi = getattr(session, "last_got_it_turn", -10)
        if turn_count - last_gi < 3:
            alts = (
                ["समझ गया" if lang == "hi-IN" else "Understood", "Makes sense", "I see", "Noted"]
                if lang != "hi-IN"
                else ["समझ गई", "ठीक है", "जी बिल्कुल"]
            )
            replacement = alts[turn_count % len(alts)]
            out = got_it_re.sub(replacement, out)
        else:
            session.last_got_it_turn = turn_count

    # Salutation at most once per 3 turns (turn 0 greeting always allowed)
    if ctx is not None:
        sal_candidates = [
            c.strip()
            for c in (ctx.profile.salutation, ctx.profile.seller_name)
            if c and c.strip() and c.strip().lower() not in ("seller partner", "custom uploaded store")
        ]
        if sal_candidates:
            sal_pattern = re.compile(
                "|".join(rf"(?<![A-Za-z0-9_]){re.escape(c)}(?![A-Za-z0-9_])" for c in sorted(sal_candidates, key=len, reverse=True))
            )
            if sal_pattern.search(out):
                last_sal = getattr(session, "last_salutation_turn", -10)
                if turn_count - last_sal < 3:
                    for c in sorted(sal_candidates, key=len, reverse=True):
                        esc_c = re.escape(c)
                        out = re.sub(rf",\s*{esc_c}\b", "", out)
                        out = re.sub(rf"^{esc_c}\s*[,:]\s*", "", out)
                        out = re.sub(rf"\b(?:for|to)\s+{esc_c}\b", "for you", out, flags=re.IGNORECASE)
                        out = re.sub(rf"(?<![A-Za-z0-9_]){esc_c}(?![A-Za-z0-9_])", "", out)
                else:
                    session.last_salutation_turn = turn_count

    out = re.sub(r"\s+([.,?!])", r"\1", out)
    out = re.sub(r"([.,?!])\1+", r"\1", out)
    out = re.sub(r"\s+", " ", out).strip()
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out


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

    s = _strip_placeholder_names(s, seller_context=seller_context)

    to_word = " टू " if language == "hi-IN" else " to "
    if language == "hi-IN":
        currency_word = "रुपये"
        month_suffix = " प्रति माह"
        day_suffix = " प्रति दिन"
        pct_word = "प्रतिशत"
    elif language == "hinglish":
        currency_word = "rupaye"
        month_suffix = " a month"
        day_suffix = " a day"
        pct_word = "percent"
    else:
        currency_word = "rupees"
        month_suffix = " a month"
        day_suffix = " a day"
        pct_word = "percent"

    def _clean_num_words(raw_num: str) -> str:
        clean = raw_num.replace(",", "").strip()
        if "." in clean:
            clean = clean.rstrip("0").rstrip(".") or "0"
        return decimal_to_spoken_hi(clean) if language == "hi-IN" else decimal_to_spoken_en(clean)

    # Expand product codes to human product names BEFORE range/number conversion (never emit codes)
    s = re.sub(r"\(\s*[A-Z]{2,8}-\d+(?:\s*,\s*[A-Z]{2,8}-\d+)*\s*\)", "", s)
    if seller_context is not None and seller_context.skus:
        for sku in seller_context.skus:
            if sku.sku_id and re.search(rf"\b{re.escape(sku.sku_id)}\b", s):
                rep = sku.name or "that product"
                s = re.sub(rf"\b{re.escape(sku.sku_id)}\b", rep, s)
        s = _cap_product_names(s, [sk.name for sk in seller_context.skus if sk.name], max_names=2)

    s = re.sub(
        r"\b(?!ACT-)[A-Z]{2,8}-0*\d+\b",
        "यह प्रोडक्ट" if language == "hi-IN" else "that product",
        s,
    )

    # Expand currency ranges BEFORE single currency items (e.g. "Rs 22,200 - Rs 28,500/month")
    def _currency_range_repl(m: re.Match[str]) -> str:
        try:
            w1 = _clean_num_words(m.group(1))
            w2 = _clean_num_words(m.group(2))
            has_mo = bool(m.group(3))
            tail = month_suffix if has_mo else ""
            sep = to_word.strip()
            return f"{w1} {sep} {w2} {currency_word}{tail}"
        except ValueError:
            return m.group(0)

    s = re.sub(
        r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:-|–)\s*(?:₹|Rs\.?|INR)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(/\s*(?:month|mo)\b|per\s+month\b)?",
        _currency_range_repl,
        s,
        flags=re.IGNORECASE,
    )

    # Expand percentage ranges BEFORE single percentage items (e.g. "10% - 15%")
    def _pct_range_repl(m: re.Match[str]) -> str:
        try:
            w1 = _clean_num_words(m.group(1))
            w2 = _clean_num_words(m.group(2))
            sep = to_word.strip()
            return f"{w1} {sep} {w2} {pct_word}"
        except ValueError:
            return m.group(0)

    s = re.sub(
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*%?\s*(?:-|–)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*%",
        _pct_range_repl,
        s,
    )

    s = s.replace("->", to_word)
    s = s.replace("–", to_word)
    s = re.sub(r"\s+to\s+to\s+", " to ", s)

    for pattern, repl in _ABBREV_MAP:
        s = pattern.sub("सर्च विज़िबिलिटी" if (language == "hi-IN" and repl == "search visibility") else repl, s)

    # Convert remaining single rupee amounts: Rs 2,00,000 | Rs. 500 | ₹1,35,000 | INR 65,000
    def _rupee_repl(m: re.Match[str]) -> str:
        try:
            spoken = _clean_num_words(m.group(1))
            return f"{spoken} {currency_word}"
        except ValueError:
            return m.group(0)

    s = re.sub(r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", _rupee_repl, s, flags=re.IGNORECASE)

    # Convert remaining percentages cleanly (.0 stripped so 50.0% -> "fifty percent")
    def _pct_repl(m: re.Match[str]) -> str:
        try:
            spoken = _clean_num_words(m.group(1))
            return f"{spoken} {pct_word}"
        except ValueError:
            return m.group(0)

    s = re.sub(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*%", _pct_repl, s)

    # Convert remaining bare numeric ranges (avoid YYYY-MM-DD dates and product codes)
    def _num_range_repl(m: re.Match[str]) -> str:
        try:
            w1 = _clean_num_words(m.group(1))
            w2 = _clean_num_words(m.group(2))
            sep = to_word.strip()
            return f"{w1} {sep} {w2}"
        except ValueError:
            return m.group(0)

    s = re.sub(
        r"(?<![A-Za-z0-9-])([0-9][0-9,]*(?:\.[0-9]+)?)\s*-\s*([0-9][0-9,]*(?:\.[0-9]+)?)(?!(?:[A-Za-z0-9_-]|-\d))",
        _num_range_repl,
        s,
    )

    s = re.sub(r"/\s*(?:month|mo)\b", month_suffix, s, flags=re.IGNORECASE)
    s = re.sub(r"/\s*day\b", day_suffix, s, flags=re.IGNORECASE)

    s = s.replace("<=", "अधिकतम " if language == "hi-IN" else "at most ")
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

    s = re.sub(r"\s+([.,?!])", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def enforce_voice_length(text: str, phase: str, max_words: int = 45) -> str:
    """Keep spoken turns crisp and within the voice word-count budget without mid-phrase cuts."""
    clean = strip_reasoning_and_markdown(text)
    limit = 74 if phase == "SUMMARY" else max_words
    soft_limit = 74 if phase == "SUMMARY" else limit
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
    re.compile(r"\b\d+\s*(?:minutes?|mins?|days?|hours?|hrs?|weeks?|months?|seconds?|pm|am)(?:\s+ist)?\b", re.IGNORECASE),
    re.compile(
        r"(?:\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?),?\s+)?"
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+"
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
        r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"(?:,?\s+\d{4})?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{1,2}:\d{2}\s*(?:am|pm)?(?:\s+ist)?\b", re.IGNORECASE),
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


_OOS_OUTCOME_CLAIM_RES = [
    re.compile(
        r"(?:bring|reduce|cut|drop|lower|get)\s+(?:out[- ]of[- ]stock|oos|out of stock products?)\s*(?:count|products?|items?)?\s*"
        r"(?:down\s+)?to\s+(\d+)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:out[- ]of[- ]stock|oos)\s*(?:count|products?|items?)?\s*(?:down\s+to|will\s+(?:be|drop\s+to|fall\s+to))\s+(\d+)\b",
        re.IGNORECASE,
    ),
]


def audit_projection_claims(reply: str, projections: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flag numeric post-action outcome claims that contradict tool-computed projections."""
    if not reply or not projections:
        return []
    warnings: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    proj_oos = projections.get("projected_oos_after_restock")
    if proj_oos is not None:
        for pat in _OOS_OUTCOME_CLAIM_RES:
            for m in pat.finditer(reply):
                claimed = int(m.group(1))
                key = ("projected_oos_after_restock", claimed, m.end(1))
                if claimed != int(proj_oos) and key not in seen:
                    seen.add(key)
                    warnings.append(
                        {
                            "type": "projection_mismatch",
                            "metric": "projected_oos_after_restock",
                            "claimed": claimed,
                            "projected": int(proj_oos),
                            "phrase": m.group(0),
                            "message": (
                                f"Reply claimed OOS outcome {claimed}, but tool projection is {proj_oos}."
                            ),
                        }
                    )
    return warnings


_CAUSAL_CLAIM_RES = [
    re.compile(
        r"\b(?:mainly|primarily|mostly|largely)\s+(?:due to|because of|from|driven by|caused by)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:is|was|are|were)\s+(?:driven by|caused by)\s+(?:stock|out[- ]?of[- ]?stock|pricing|price|visibility|cancel)",
        re.IGNORECASE,
    ),
    re.compile(r"\baccounted for\s+\d+(?:\.\d+)?%", re.IGNORECASE),
    re.compile(
        r"\b(?:biggest|main|top|primary)\s+(?:reason|driver|cause|factor)\s+(?:is|was)\s+(?:stock|out[- ]?of[- ]?stock|pricing|visibility|cancel)",
        re.IGNORECASE,
    ),
]


def detect_premature_causal_claims(text: str) -> list[str]:
    """Detect causal attribution phrases in text (disallowed before DIAGNOSE runs)."""
    if not text:
        return []
    hits: list[str] = []
    for pat in _CAUSAL_CLAIM_RES:
        m = pat.search(text)
        if m:
            hits.append(m.group(0))
    return hits


def sanitize_premature_causal_claims(
    reply: str,
    *,
    sales_delta_pct: float | int,
    sales_curr: int | float,
    salutation: str,
    language: str = "en-IN",
) -> str:
    """Replace premature causal claims before DIAGNOSE with neutral headline summary + clarifying question."""
    if not detect_premature_causal_claims(reply):
        return reply
    sentences = re.split(r"(?<=[.?!\u0964])\s+", reply.strip())
    kept = [s for s in sentences if not detect_premature_causal_claims(s)]
    if kept:
        return " ".join(kept).strip()
    pct_str = str(abs(sales_delta_pct)).rstrip("0").rstrip(".") if "." in str(sales_delta_pct) else str(abs(sales_delta_pct))
    if language == "hi-IN":
        return f"मैं समझती हूँ {salutation}, आपकी बिक्री {pct_str}% घटकर {format_inr(int(round(sales_curr)))} रह गई है। हाल ही में आपके स्टोर संचालन में क्या बदलाव आया?"
    if language == "hinglish":
        return f"Main samajhti hoon {salutation}, sales {pct_str}% ghat kar {format_inr(int(round(sales_curr)))} hui hain. Haal mein store operations mein kya change aaya?"
    return f"I understand your concern, {salutation} — sales moved {pct_str}% to {format_inr(int(round(sales_curr)))}. What changed in your operations recently?"

"""Indian digit grouping (lakh/crore comma placement) and currency text formatters."""

from __future__ import annotations

import re

_COMMA_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.\-/,])(\d+(?:,\d+)+)(?:\.(\d+))?(?!(?:[A-Za-z0-9_,]|-\d|\.\d))"
)
_CURRENCY_BARE_RE = re.compile(
    r"(?i)(₹|Rs\.?|INR)(\s*)(\d{4,})(?:\.(\d+))?(?!(?:[A-Za-z0-9_,]|\.\d))"
)
_FIVE_PLUS_DIGITS_RE = re.compile(
    r"(?<![A-Za-z0-9_.\-/,])(\d{5,})(?:\.(\d+))?(?!(?:[A-Za-z0-9_,]|-\d|\.\d))"
)


def format_indian_number(value: int | float, decimals: int | None = None) -> str:
    """Format an integer or float using Indian digit grouping (e.g. 135000 -> '1,35,000')."""
    if isinstance(value, bool):
        return str(value)
    neg = value < 0
    abs_val = abs(float(value))

    if decimals is None:
        if isinstance(value, int) or abs_val.is_integer():
            decimals = 0
        else:
            decimals = 2

    if decimals <= 0:
        digits = str(int(round(abs_val)))
        frac_part = ""
    else:
        formatted = f"{abs_val:.{decimals}f}"
        digits, frac_part = formatted.split(".", 1)
        frac_part = f".{frac_part}"

    if len(digits) <= 3:
        grouped = digits
    else:
        last3 = digits[-3:]
        rest = digits[:-3]
        chunks: list[str] = []
        while len(rest) > 2:
            chunks.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            chunks.append(rest)
        grouped = ",".join(reversed(chunks)) + "," + last3

    return f"{'-' if neg else ''}{grouped}{frac_part}"


def format_inr(value: int | float, *, decimals: int | None = None, symbol: str = "Rs ") -> str:
    """Format an INR amount with Indian digit grouping (e.g. 135000 -> 'Rs 1,35,000')."""
    if value < 0:
        return f"-{symbol}{format_indian_number(abs(value), decimals=decimals)}"
    return f"{symbol}{format_indian_number(value, decimals=decimals)}"


def normalize_indian_grouping(text: str) -> str:
    """Rewrite any Western-grouped or ungrouped large numbers (>=10,000 or >=1,000 after Rs) into Indian grouping."""
    if not text:
        return text

    def _repl_comma(m: re.Match[str]) -> str:
        raw_int = m.group(1).replace(",", "")
        frac = m.group(2)
        grouped = format_indian_number(int(raw_int), decimals=0)
        return f"{grouped}.{frac}" if frac is not None else grouped

    def _repl_curr(m: re.Match[str]) -> str:
        prefix, space, raw_int, frac = m.group(1), m.group(2), m.group(3), m.group(4)
        grouped = format_indian_number(int(raw_int), decimals=0)
        tail = f".{frac}" if frac is not None else ""
        return f"{prefix}{space}{grouped}{tail}"

    def _repl_large(m: re.Match[str]) -> str:
        raw_int, frac = m.group(1), m.group(2)
        # Do not alter long numeric identifiers if they look like 10+ digit phone/account strings
        if len(raw_int) > 9:
            return m.group(0)
        grouped = format_indian_number(int(raw_int), decimals=0)
        return f"{grouped}.{frac}" if frac is not None else grouped

    out = _COMMA_NUMBER_RE.sub(_repl_comma, text)
    out = _CURRENCY_BARE_RE.sub(_repl_curr, out)
    out = _FIVE_PLUS_DIGITS_RE.sub(_repl_large, out)
    return out


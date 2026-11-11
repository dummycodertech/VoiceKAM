"""Code-computed Asia/Kolkata (IST) dates, weekday validation, and spoken/written date auditor."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment]

_IST_FIXED = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")

_WEEKDAY_MAP = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_MONTH_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_WEEKDAY_DAY_MONTH_RE = re.compile(
    r"\b(?:(Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?),?\s+)?"
    r"(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"(?:,?\s+(\d{4}))?\b",
    re.IGNORECASE,
)


def get_ist_tz() -> tzinfo:
    """Return Asia/Kolkata tzinfo with a fixed UTC+05:30 fallback when OS lacks tzdata."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Kolkata")
        except Exception:
            return _IST_FIXED
    return _IST_FIXED


def anchor_now_ist(now: datetime | str | None = None) -> datetime:
    """Return a timezone-aware IST datetime from an optional datetime, ISO string, or current clock."""
    tz = get_ist_tz()
    if now is None:
        return datetime.now(tz)
    if isinstance(now, str):
        parsed = datetime.fromisoformat(now)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def _to_date(anchor: datetime | date | str | None) -> date:
    if isinstance(anchor, date) and not isinstance(anchor, datetime):
        return anchor
    return anchor_now_ist(anchor).date()


def format_ist_date(dt: datetime | date) -> str:
    """Format an IST date as 'Friday 13 Nov' (explicit weekday + day + 3-letter month)."""
    d = dt.date() if isinstance(dt, datetime) else dt
    return f"{d.strftime('%A')} {d.day} {d.strftime('%b')}"


def format_ist_short_date(dt: datetime | date) -> str:
    """Format an IST date as '13 Nov 2026'."""
    d = dt.date() if isinstance(dt, datetime) else dt
    return f"{d.day} {d.strftime('%b')} {d.year}"


def compute_due_date_ist(offset_days: int, anchor: datetime | date | str | None = None) -> dict[str, Any]:
    """Compute a code-verified IST target date at anchor + offset_days."""
    base = _to_date(anchor)
    target = base + timedelta(days=max(0, int(offset_days)))
    label = format_ist_date(target)
    return {
        "offset_days": max(0, int(offset_days)),
        "iso_date": target.isoformat(),
        "weekday": target.strftime("%A"),
        "due_date_ist": label,
        "day_month": f"{target.day} {target.strftime('%b')}",
    }


def compute_followup_date_ist(
    offset_days: int = 3,
    anchor: datetime | date | str | None = None,
    time_label: str = "11:00 AM IST",
) -> str:
    """Compute code-verified follow-up call date/time string in IST."""
    info = compute_due_date_ist(offset_days=offset_days, anchor=anchor)
    return f"{info['due_date_ist']} at {time_label}"


def offset_days_for_timeframe(raw: str | None, default_days: int = 2) -> int:
    """Infer integer day offset from standard timeframe descriptions."""
    if not raw:
        return default_days
    low = raw.lower()
    if any(k in low for k in ("today", "daily", "immediately", "aaj")):
        return 0
    if "48" in low or "2 day" in low or "two day" in low or "day after tomorrow" in low or "parso" in low:
        return 2
    if "24" in low or "tomorrow" in low or "kal" in low or "1 day" in low:
        return 1
    m_days = re.search(r"(\d+)\s*(?:day|days|din)", low)
    if m_days:
        return max(0, min(30, int(m_days.group(1))))
    m_hrs = re.search(r"(\d+)\s*(?:hour|hours|hr|hrs)", low)
    if m_hrs:
        hrs = int(m_hrs.group(1))
        return max(1, (hrs + 23) // 24)
    return default_days


def resolve_due_date_ist(
    raw: str | None,
    *,
    anchor: datetime | date | str | None = None,
    default_days: int = 2,
) -> str:
    """Convert a relative timeframe or LLM-provided date into an exact code-computed IST calendar date."""
    base = _to_date(anchor)
    if raw:
        m = _WEEKDAY_DAY_MONTH_RE.search(raw)
        if m:
            day = int(m.group(2))
            month = _MONTH_MAP.get(m.group(3).lower(), 0)
            year = int(m.group(4)) if m.group(4) else _infer_year(base, month, day)
            try:
                parsed = date(year, month, day)
                return format_ist_date(parsed)
            except ValueError:
                pass
    offset = offset_days_for_timeframe(raw, default_days=default_days)
    return compute_due_date_ist(offset, anchor=base)["due_date_ist"]


def _infer_year(anchor_d: date, month: int, day: int) -> int:
    candidates = []
    for yr in (anchor_d.year - 1, anchor_d.year, anchor_d.year + 1):
        try:
            cand = date(yr, month, day)
            candidates.append((abs((cand - anchor_d).days), yr))
        except ValueError:
            continue
    if not candidates:
        return anchor_d.year
    candidates.sort()
    return candidates[0][1]


def extract_mentioned_dates(text: str, anchor: datetime | date | str | None = None) -> list[dict[str, Any]]:
    """Extract all 'DD Mon' or 'Weekday DD Mon' mentions with their true calendar IST date."""
    base = _to_date(anchor)
    found: list[dict[str, Any]] = []
    for m in _WEEKDAY_DAY_MONTH_RE.finditer(text or ""):
        claimed_wd_raw = m.group(1)
        day = int(m.group(2))
        month_raw = m.group(3)
        month = _MONTH_MAP.get(month_raw.lower(), 0)
        year = int(m.group(4)) if m.group(4) else _infer_year(base, month, day)
        try:
            actual_date = date(year, month, day)
        except ValueError:
            found.append(
                {
                    "raw": m.group(0),
                    "valid_calendar_date": False,
                    "claimed_weekday": claimed_wd_raw,
                    "expected": None,
                    "day_month": f"{day} {month_raw.title()[:3]}",
                }
            )
            continue
        actual_wd = actual_date.strftime("%A")
        claimed_wd_idx = _WEEKDAY_MAP.get(claimed_wd_raw.lower()) if claimed_wd_raw else None
        weekday_mismatch = claimed_wd_idx is not None and claimed_wd_idx != actual_date.weekday()
        found.append(
            {
                "raw": m.group(0),
                "valid_calendar_date": True,
                "iso_date": actual_date.isoformat(),
                "day_month": f"{actual_date.day} {actual_date.strftime('%b')}",
                "claimed_weekday": claimed_wd_raw,
                "actual_weekday": actual_wd,
                "expected": format_ist_date(actual_date),
                "weekday_mismatch": weekday_mismatch,
            }
        )
    return found


def audit_dates(
    reply_text: str,
    *,
    anchor: datetime | date | str | None = None,
    summary_card: dict[str, Any] | None = None,
    expected_dates: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Flag invalid dates, wrong weekdays (e.g. 'Monday 13 Nov'), and spoken-vs-written date mismatches."""
    warnings: list[dict[str, Any]] = []
    mentions = extract_mentioned_dates(reply_text, anchor=anchor)

    for m in mentions:
        if not m["valid_calendar_date"]:
            warnings.append(
                {
                    "type": "invalid_calendar_date",
                    "claimed": m["raw"],
                    "message": f"'{m['raw']}' is not a valid calendar date.",
                }
            )
        elif m["weekday_mismatch"]:
            warnings.append(
                {
                    "type": "weekday_mismatch",
                    "claimed": m["raw"],
                    "expected": m["expected"],
                    "message": f"Claimed '{m['raw']}' has wrong weekday; {m['day_month']} is {m['actual_weekday']}.",
                }
            )

    written_day_months: set[str] = set()
    written_iso_dates: set[str] = set()
    if summary_card:
        for item in summary_card.get("commitments") or []:
            for wm in extract_mentioned_dates(str(item.get("due_date") or ""), anchor=anchor):
                if wm.get("iso_date"):
                    written_iso_dates.add(wm["iso_date"])
                    written_day_months.add(wm["day_month"])
        fu = summary_card.get("followup") or {}
        for wm in extract_mentioned_dates(str(fu.get("followup_date") or ""), anchor=anchor):
            if wm.get("iso_date"):
                written_iso_dates.add(wm["iso_date"])
                written_day_months.add(wm["day_month"])

    for ed in expected_dates or []:
        for wm in extract_mentioned_dates(str(ed), anchor=anchor):
            if wm.get("iso_date"):
                written_iso_dates.add(wm["iso_date"])
                written_day_months.add(wm["day_month"])

    # Also allow anchor date itself (call date) if mentioned
    anchor_iso = _to_date(anchor).isoformat()
    if (summary_card or expected_dates) and written_iso_dates:
        allowed_isos = written_iso_dates | {anchor_iso}
        for m in mentions:
            if m.get("iso_date") and m["iso_date"] not in allowed_isos:
                warnings.append(
                    {
                        "type": "spoken_written_date_mismatch",
                        "claimed": m["expected"] or m["raw"],
                        "expected": sorted(written_day_months),
                        "message": f"Spoken date '{m['raw']}' does not match written summary dates {sorted(written_day_months)}.",
                    }
                )
    return warnings


"""Conservative funding-deadline and last-verified date parsing. No invented dates."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from dateutil import parser as date_parser

TODAY = date(2026, 11, 17)

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December|"
    "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
# "December 11, 2026" or "December11,2026" or "Dec 11 2026"
DATE_RE = re.compile(
    rf"\b((?:{MONTHS})\.?\s*\d{{1,2}}(?:st|nd|rd|th)?(?:,)?\s*\d{{4}})\b",
    re.I,
)
SLASH_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")
ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

CLOSING_CUE = re.compile(
    r"\b(due|deadline|cutoff|cut-off|closes?|closing|through|until|by|before|no later than|"
    r"must reach|applications? (?:are )?(?:due|close)|enrollment (?:runs|ends|closes))\b",
    re.I,
)
PASSED_CUE = re.compile(r"\bhas passed\b|\bpassed\b|\bclosed\b|\bno longer (?:accepting|open)\b", re.I)
OPEN_CUE = re.compile(
    r"\b(year-?round|rolling|ongoing|no (?:fixed |published |single )?(?:deadline|cutoff|closing date)|"
    r"no annual deadline|throughout the year|continuous(?:ly)?|accepted year-round|"
    r"no cutoff|no published cutoff|no fixed closing)\b",
    re.I,
)


def to_iso(d: date | datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    return d.isoformat()


def parse_one_date(raw: str, default_year: int | None = None) -> date | None:
    raw = re.sub(r"([A-Za-z])(\d)", r"\1 \2", raw)
    raw = re.sub(r"(\d),(\d{4})", r"\1, \2", raw)
    try:
        dt = date_parser.parse(raw, fuzzy=False, default=datetime(default_year or 2026, 1, 1))
        return dt.date()
    except (ValueError, OverflowError, TypeError):
        try:
            dt = date_parser.parse(raw, fuzzy=True, default=datetime(default_year or 2026, 1, 1))
            return dt.date()
        except (ValueError, OverflowError, TypeError):
            return None


def parse_last_verified(text: str | None) -> str | None:
    if not text:
        return None
    # Prefer the first explicit calendar date in the "Last verified" string.
    m = DATE_RE.search(text) or ISO_RE.search(text)
    if m:
        d = parse_one_date(m.group(1))
        return to_iso(d)
    d = parse_one_date(text)
    return to_iso(d)


def _dates_in(text: str) -> list[tuple[int, int, date, str]]:
    found: list[tuple[int, int, date, str]] = []
    for cre in (ISO_RE, DATE_RE, SLASH_RE):
        for m in cre.finditer(text):
            d = parse_one_date(m.group(1))
            if d:
                found.append((m.start(), m.end(), d, m.group(1)))
    # de-dupe by position
    found.sort()
    uniq = []
    seen_span = set()
    for start, end, d, raw in found:
        key = (start, end)
        if key not in seen_span:
            uniq.append((start, end, d, raw))
            seen_span.add(key)
    return uniq


def parse_deadline(deadline_text: str | None, today: date = TODAY) -> dict[str, Any]:
    """Return deadline_iso (closing date if clearly stated), expired, and a short note.

    Leaves deadline_iso null when the page does not give a single parseable close date.
    """
    result: dict[str, Any] = {
        "deadline_iso": None,
        "expired": None,
        "deadline_parse_note": None,
    }
    if not deadline_text or not deadline_text.strip():
        result["deadline_parse_note"] = "no deadline text"
        return result

    text = deadline_text.strip()
    dates = _dates_in(text)
    is_open = bool(OPEN_CUE.search(text))
    passed = bool(PASSED_CUE.search(text))

    chosen: date | None = None
    note = None

    # Prefer a date immediately after a closing cue (due / by / through / cutoff).
    cued: list[date] = []
    for start, end, d, raw in dates:
        window = text[max(0, start - 48) : start]
        if CLOSING_CUE.search(window) or CLOSING_CUE.search(text[start:end + 24]):
            cued.append(d)
    if cued:
        # For a range ("September 16 through December 11, 2026") take the latest cued date.
        chosen = max(cued)
        note = "parsed from an explicit due/through/cutoff/by date in the deadline text"

    if chosen is None and dates and not is_open:
        # A single unambiguous calendar date in the deadline field, not just a historical year.
        unique_dates = sorted({d for _, _, d, _ in dates})
        if len(unique_dates) == 1:
            chosen = unique_dates[0]
            note = "single calendar date in the deadline text"
        elif passed and unique_dates:
            # "The October 17, 2026 cutoff ... has passed"
            chosen = min(unique_dates)
            note = "earliest dated cutoff, text indicates it has passed"

    result["deadline_iso"] = to_iso(chosen)
    if chosen is not None:
        result["expired"] = chosen < today
        result["deadline_parse_note"] = note
    elif is_open:
        result["expired"] = False
        result["deadline_parse_note"] = "year-round, rolling, or no fixed deadline stated; no single close date parsed"
    elif passed and not dates:
        result["expired"] = True
        result["deadline_parse_note"] = "text says the window has passed, but no parseable close date was found"
    else:
        result["expired"] = None
        result["deadline_parse_note"] = "no single parseable application close date"

    return result

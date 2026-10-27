"""Date of birth parsing. Ambiguous numeric dates are read day-first (UK)."""

from __future__ import annotations

import datetime as dt
import re

from app.schemas import Issue

MONTHS = {
    m: i + 1
    for i, names in enumerate([
        ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
        ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
        ("oct", "october"), ("nov", "november"), ("dec", "december"),
    ])
    for m in names
}

# System default / sentinel dates that say nothing about the person.
PLACEHOLDER_DATES = {
    dt.date(1900, 1, 1), dt.date(1901, 1, 1), dt.date(1899, 12, 31), dt.date(1800, 1, 1),
    dt.date(1753, 1, 1), dt.date(1, 1, 1), dt.date(1970, 1, 1), dt.date(1111, 11, 11),
    dt.date(1999, 9, 9), dt.date(2000, 1, 1),
}
MAX_AGE_YEARS = 120


def _pivot(yy: int, today: dt.date) -> int:
    century = today.year // 100 * 100
    year = century + yy
    return year - 100 if year > today.year else year


def _try(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def parse_dob(raw: str | dt.date | None, today: dt.date | None = None) -> tuple[dt.date | None, bool, list[Issue]]:
    """Return (date, usable_for_matching, issues)."""
    today = today or dt.date.today()
    issues: list[Issue] = []
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, False, issues

    d: dt.date | None = None
    if isinstance(raw, dt.datetime):
        d = raw.date()
    elif isinstance(raw, dt.date):
        d = raw
    else:
        s = raw.strip().lower()
        s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
        s = re.sub(r"[t ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?(z|[+-]\d{2}:?\d{2})?$", "", s).strip()
        if m := re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s):          # ISO
            d = _try(int(m[1]), int(m[2]), int(m[3]))
        elif m := re.fullmatch(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4}|\d{2})", s):  # UK day-first
            y = int(m[3]) if len(m[3]) == 4 else _pivot(int(m[3]), today)
            day, month = int(m[1]), int(m[2])
            d = _try(y, month, day)
            if d is None and month > 12 and day <= 12:
                d = _try(y, day, month)  # clearly US month-first
                if d:
                    issues.append(Issue(field="date_of_birth", code="month_first_detected", severity="info"))
        elif m := re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s):                       # YYYYMMDD
            d = _try(int(m[1]), int(m[2]), int(m[3]))
        elif m := re.fullmatch(r"(\d{1,2})[ \-/]*([a-z]+)[ \-/,]*(\d{4}|\d{2})", s):  # 12 Mar 1985
            mon = MONTHS.get(m[2])
            if mon:
                y = int(m[3]) if len(m[3]) == 4 else _pivot(int(m[3]), today)
                d = _try(y, mon, int(m[1]))
        elif m := re.fullmatch(r"([a-z]+)[ \-/]*(\d{1,2})[ ,\-/]*(\d{4})", s):      # March 12, 1985
            mon = MONTHS.get(m[1])
            if mon:
                d = _try(int(m[3]), mon, int(m[2]))

    if d is None:
        issues.append(Issue(field="date_of_birth", code="unparseable_date", detail=str(raw)))
        return None, False, issues
    if d > today:
        issues.append(Issue(field="date_of_birth", code="future_date", severity="error", detail=d.isoformat()))
        return None, False, issues
    if d in PLACEHOLDER_DATES or d == today:
        issues.append(Issue(field="date_of_birth", code="placeholder_date", detail=d.isoformat()))
        return d, False, issues
    if (today.year - d.year) > MAX_AGE_YEARS:
        issues.append(Issue(field="date_of_birth", code="implausible_age", detail=d.isoformat()))
        return d, False, issues
    age = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    if age < 16:
        issues.append(Issue(field="date_of_birth", code="minor", severity="info"))
    return d, True, issues


def unordered_dob(d: dt.date) -> str:
    """Day/month order-insensitive form, so 03/04/1985 and 04/03/1985 agree."""
    lo, hi = sorted((d.day, d.month))
    return f"{d.year:04d}{lo:02d}{hi:02d}"

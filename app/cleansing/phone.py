"""UK telephone number standardisation to E.164, following the Ofcom
National Telephone Numbering Plan."""

from __future__ import annotations

import re

from app.schemas import CleansedPhone, Issue

_EXTENSION = re.compile(r"(?:\s*(?:ext\.?|extension|x|#)\s*\d{1,6})\s*$", re.IGNORECASE)

# Ofcom ranges reserved for TV/radio drama: never real customers.
_DRAMA_RANGES = [
    ("7700900000", "7700900999"),
    ("1134960000", "1134960999"), ("1144960000", "1144960999"), ("1154960000", "1154960999"),
    ("1164960000", "1164960999"), ("1174960000", "1174960999"), ("1184960000", "1184960999"),
    ("1214960000", "1214960999"), ("1314960000", "1314960999"), ("1414960000", "1414960999"),
    ("1514960000", "1514960999"), ("1614960000", "1614960999"), ("2079460000", "2079460999"),
    ("1914980000", "1914980999"), ("2896496000", "2896496999"), ("2920180000", "2920180999"),
    ("1632960000", "1632960999"), ("8081570000", "8081570999"), ("9098790000", "9098790999"),
]


def _classify(nsn: str) -> str | None:
    """Return the number type for a UK national significant number (no leading 0)."""
    n = len(nsn)
    if nsn.startswith("7"):
        if n != 10:
            return None
        if nsn.startswith("7624"):
            return "mobile"  # Isle of Man mobiles
        if nsn.startswith("70"):
            return "personal"
        if nsn.startswith("76"):
            return "pager"
        if nsn[1] in "1234579" or nsn.startswith("78"):
            return "mobile"
        return None
    if nsn.startswith("1"):
        return "landline" if n in (9, 10) else None
    if nsn.startswith("2"):
        return "landline" if n == 10 else None
    if nsn.startswith("3"):
        return "non_geographic" if n == 10 else None
    if nsn.startswith("80"):
        return "freephone" if n in (9, 10) else None
    if nsn.startswith(("84", "87")):
        return "non_geographic" if n == 10 else None
    if nsn.startswith("9"):
        return "premium" if n == 10 else None
    if nsn.startswith(("55", "56")):
        return "voip" if n == 10 else None
    return None


def _national_format(nsn: str, kind: str) -> str:
    d = "0" + nsn
    if kind in ("mobile", "personal", "pager"):
        return f"{d[:5]} {d[5:]}"
    if nsn.startswith("2"):
        return f"{d[:3]} {d[3:7]} {d[7:]}"
    if nsn.startswith("1") and (nsn[1] == "1" or nsn[2] == "1"):  # 0113, 0121 style
        return f"{d[:4]} {d[4:7]} {d[7:]}"
    if nsn.startswith(("3", "8", "9")):
        return f"{d[:4]} {d[4:7]} {d[7:]}"
    return f"{d[:5]} {d[5:]}"


def _junk(nsn: str) -> bool:
    if len(set(nsn)) <= 2:
        return True
    return nsn in {"1234567890", "123456789", "7123456789", "7000000000", "1234567891"}


def cleanse_phone(raw: str | None, field: str = "phone") -> tuple[CleansedPhone | None, list[Issue]]:
    issues: list[Issue] = []
    if not raw or not str(raw).strip():
        return None, issues
    s = _EXTENSION.sub("", str(raw).strip())
    s = s.replace("(0)", "")
    plus = s.lstrip().startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        issues.append(Issue(field=field, code="invalid_phone", detail=str(raw)))
        return None, issues

    if plus or digits.startswith("00"):
        digits = digits[2:] if digits.startswith("00") else digits
        if not digits.startswith("44"):
            if 8 <= len(digits) <= 15:
                return CleansedPhone(e164="+" + digits, type="international"), issues
            issues.append(Issue(field=field, code="invalid_phone", detail=str(raw)))
            return None, issues
        nsn = digits[2:].lstrip("0")
    elif digits.startswith("44") and len(digits) in (11, 12):
        nsn = digits[2:].lstrip("0")
    elif digits.startswith("0"):
        nsn = digits[1:]
    elif len(digits) == 10 and digits[0] == "7":
        nsn = digits  # leading zero lost (classic spreadsheet damage)
        issues.append(Issue(field=field, code="leading_zero_restored", severity="info"))
    elif len(digits) in (9, 10) and digits[0] in "123":
        nsn = digits
        issues.append(Issue(field=field, code="leading_zero_restored", severity="info"))
    else:
        issues.append(Issue(field=field, code="invalid_phone", detail=str(raw)))
        return None, issues

    kind = _classify(nsn)
    if kind is None:
        issues.append(Issue(field=field, code="invalid_uk_number", detail=str(raw)))
        return None, issues

    usable = True
    if _junk(nsn):
        issues.append(Issue(field=field, code="placeholder_number", severity="warning", detail=str(raw)))
        usable = False
    elif any(lo <= nsn.ljust(10, "0") <= hi for lo, hi in _DRAMA_RANGES):
        issues.append(Issue(field=field, code="ofcom_drama_number", severity="warning", detail=str(raw)))
        usable = False
    if kind in ("premium", "freephone", "non_geographic"):
        # Usually a company switchboard, not a personal number.
        usable = False
        issues.append(Issue(field=field, code="non_personal_number", severity="info"))

    return CleansedPhone(
        e164="+44" + nsn, national=_national_format(nsn, kind), type=kind,  # type: ignore[arg-type]
        usable_for_matching=usable,
    ), issues

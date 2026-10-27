"""UK postcode validation and formatting (BS 7666 formats)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Outward code formats: A9, A99, AA9, AA99, A9A, AA9A. Letter restrictions per Royal Mail.
_OUTWARD = (
    r"(?:[A-PR-UWYZ][0-9][0-9]?"
    r"|[A-PR-UWYZ][A-HK-Y][0-9][0-9]?"
    r"|[A-PR-UWYZ][0-9][A-HJKPSTUW]"
    r"|[A-PR-UWYZ][A-HK-Y][0-9][ABEHMNPRVWXY])"
)
_INWARD = r"[0-9][ABD-HJLNP-UW-Z]{2}"
_POSTCODE = re.compile(rf"^(?P<out>{_OUTWARD})(?P<in>{_INWARD})$")
_SPECIAL = {"GIR0AA"}  # Girobank, still valid

# Characters commonly mistyped in the digit position of the inward code.
_DIGIT_FIXES = {"O": "0", "I": "1", "L": "1", "S": "5", "Z": "2"}


@dataclass(frozen=True)
class Postcode:
    full: str      # "SW1A 1AA"
    outward: str   # "SW1A"
    inward: str    # "1AA"
    sector: str    # "SW1A 1"
    district: str  # "SW1A"
    area: str      # "SW"
    corrected: bool = False

    @property
    def compact(self) -> str:
        return self.outward + self.inward


def _build(out: str, inw: str, corrected: bool) -> Postcode:
    area = re.match(r"[A-Z]+", out).group(0)  # type: ignore[union-attr]
    return Postcode(
        full=f"{out} {inw}", outward=out, inward=inw, sector=f"{out} {inw[0]}",
        district=out, area=area, corrected=corrected,
    )


def compact_postcode(raw: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def parse_postcode(raw: str | None) -> Postcode | None:
    """Return a validated, formatted postcode, or None if it isn't a valid UK postcode."""
    s = compact_postcode(raw)
    if not 5 <= len(s) <= 7:
        return None
    if s in _SPECIAL:
        return _build("GIR", "0AA", False)
    m = _POSTCODE.match(s)
    if m:
        return _build(m.group("out"), m.group("in"), False)
    # Conservative repair: only the inward digit (third from the end), which must be numeric.
    ch = s[-3]
    if ch in _DIGIT_FIXES:
        fixed = s[:-3] + _DIGIT_FIXES[ch] + s[-2:]
        m = _POSTCODE.match(fixed)
        if m:
            return _build(m.group("out"), m.group("in"), True)
    return None


_EMBEDDED = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b"
)


def find_postcode_in_text(text: str) -> Postcode | None:
    """Find a postcode embedded in an address line, e.g. "LONDON SW1A 1AA"."""
    for m in _EMBEDDED.finditer(text.upper()):
        pc = parse_postcode(m.group(1) + m.group(2))
        if pc:
            return pc
    return None

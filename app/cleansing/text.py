"""Shared text normalisation helpers."""

from __future__ import annotations

import re
import unicodedata

_SPECIAL = str.maketrans(
    {
        "ß": "ss", "Æ": "AE", "æ": "ae", "Ø": "O", "ø": "o", "Ł": "L", "ł": "l",
        "Đ": "D", "đ": "d", "Þ": "TH", "þ": "th", "Œ": "OE", "œ": "oe", "Ð": "D", "ð": "d",
        "’": "'", "‘": "'", "`": "'", "´": "'", "ʼ": "'",
        "–": "-", "—": "-", "‐": "-", "‑": "-",
        " ": " ", " ": " ", " ": " ",
    }
)

# Values source systems use to mean "no data".
NULL_TOKENS = {
    "", "N/A", "NA", "N.A", "NULL", "NONE", "NIL", "UNKNOWN", "UNK", "-", "--", "---",
    "TBC", "TBA", "?", "??", "NOT KNOWN", "NOT PROVIDED", "NOT GIVEN", "REFUSED",
    "NO DATA", "UNDEFINED", "BLANK", "X", "XX", "XXX", "0",
}
# In name fields these are real names (Na is a common Korean surname, Nil a
# Turkish forename), so only the unambiguous tokens count as "no data".
NAME_NULL_TOKENS = NULL_TOKENS - {"NA", "NIL"}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def fold(value: str) -> str:
    """Strip accents and map special letters to ASCII equivalents."""
    value = value.translate(_SPECIAL)
    value = unicodedata.normalize("NFKD", value)
    return "".join(c for c in value if not unicodedata.combining(c))


def clean_text(value: object, null_tokens: set[str] | frozenset[str] = NULL_TOKENS) -> str | None:
    """Fold, strip control characters, collapse whitespace and drop null tokens."""
    if value is None:
        return None
    s = fold(str(value))
    s = _CONTROL.sub(" ", s)
    s = " ".join(s.split())
    if s.upper().strip(" .") in null_tokens:
        return None
    return s


def alpha_key(value: str | None) -> str:
    """Lower-case letters only. Used for name key material."""
    if not value:
        return ""
    return re.sub(r"[^a-z]", "", fold(value).lower())


def alnum_key(value: str | None) -> str:
    """Lower-case letters and digits only."""
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", fold(value).lower())

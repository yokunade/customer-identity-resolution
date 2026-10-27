"""Phonetic encodings used for typo-tolerant deterministic keys."""

from __future__ import annotations

import re

_SOUNDEX_CODES = {
    **dict.fromkeys("BFPV", "1"), **dict.fromkeys("CGJKQSXZ", "2"), **dict.fromkeys("DT", "3"),
    "L": "4", **dict.fromkeys("MN", "5"), "R": "6",
}


def soundex(name: str) -> str:
    """American Soundex (with the H/W rule). "Robert" and "Rupert" -> R163."""
    s = re.sub(r"[^A-Z]", "", name.upper())
    if not s:
        return ""
    first = s[0]
    out = [first]
    prev = _SOUNDEX_CODES.get(first, "")
    for ch in s[1:]:
        code = _SOUNDEX_CODES.get(ch, "")
        if code and code != prev:
            out.append(code)
        if ch not in "HW":  # H and W do not separate letters with the same code
            prev = code
        if len(out) == 4:
            break
    return "".join(out).ljust(4, "0")


_VOWELS = set("AEIOU")


def nysiis(name: str) -> str:
    """New York State Identification and Intelligence System code (untruncated)."""
    s = re.sub(r"[^A-Z]", "", name.upper())
    if not s:
        return ""
    for a, b in (("MAC", "MCC"), ("KN", "NN"), ("K", "C"), ("PH", "FF"), ("PF", "FF"), ("SCH", "SSS")):
        if s.startswith(a):
            s = b + s[len(a):]
            break
    for a, b in (("EE", "Y"), ("IE", "Y"), ("DT", "D"), ("RT", "D"), ("RD", "D"), ("NT", "D"), ("ND", "D")):
        if s.endswith(a):
            s = s[: -len(a)] + b
            break

    chars = list(s)
    key = chars[0]
    i = 1
    while i < len(chars):
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if c == "E" and nxt == "V":
            chars[i], chars[i + 1] = "A", "F"
        elif c in _VOWELS:
            chars[i] = "A"
        elif c == "Q":
            chars[i] = "G"
        elif c == "Z":
            chars[i] = "S"
        elif c == "M":
            chars[i] = "N"
        elif c == "K":
            chars[i] = "N" if nxt == "N" else "C"
        elif c == "S" and chars[i + 1: i + 3] == ["C", "H"]:
            chars[i: i + 3] = ["S", "S", "S"]
        elif c == "P" and nxt == "H":
            chars[i], chars[i + 1] = "F", "F"
        elif c == "H" and (chars[i - 1] not in _VOWELS or (nxt and nxt not in _VOWELS) or not nxt):
            chars[i] = chars[i - 1]
        elif c == "W" and chars[i - 1] in _VOWELS:
            chars[i] = chars[i - 1]
        if chars[i] != key[-1]:
            key += chars[i]
        i += 1

    if len(key) > 1 and key.endswith("S"):
        key = key[:-1]
    if key.endswith("AY"):
        key = key[:-2] + "Y"
    if len(key) > 1 and key.endswith("A"):
        key = key[:-1]
    return key

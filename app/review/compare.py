"""Side-by-side comparison of two customers for a reviewer.

Each row aggregates one attribute across all of a customer's source records
and gives a verdict:

    match     at least one value is shared
    close     no shared value, but one is a near miss (nickname, typo,
              transposed DOB, same postcode)
    differs   both have values, none shared; normal for contact details
    conflict  both have values that cannot both be true (DOB, gender, Jr/Sr)
    missing   one or both customers have no value
"""

from __future__ import annotations

from typing import Any

from app.cleansing.nicknames import first_name_forms
from app.cleansing.text import alpha_key, alnum_key
from app.matching.rules import _dobs_conflict, damerau


def _name(rec: dict) -> str | None:
    n = rec.get("name") or {}
    parts = [n.get("first_name"), n.get("middle_names"), n.get("last_name"), n.get("suffix")]
    s = " ".join(p for p in parts if p)
    return s or None


def _addr(a: dict | None) -> str | None:
    if not a:
        return None
    return ", ".join(a.get("lines") or []) or None


def _values(records: list[dict], getter) -> list[dict]:
    """Distinct values with the sources that hold them, most-supported first."""
    seen: dict[str, dict] = {}
    for r in records:
        for v in getter(r):
            if not v:
                continue
            item = seen.setdefault(v, {"value": v, "sources": []})
            if r["_source"] not in item["sources"]:
                item["sources"].append(r["_source"])
    return sorted(seen.values(), key=lambda x: (-len(x["sources"]), x["value"]))


def _mark_shared(a: list[dict], b: list[dict], key) -> bool:
    ka = {key(x["value"]): x for x in a}
    kb = {key(x["value"]): x for x in b}
    shared = set(ka) & set(kb)
    for k in shared:
        ka[k]["shared"] = kb[k]["shared"] = True
    return bool(shared)


def _row(field: str, label: str, a: list[dict], b: list[dict], verdict: str, note: str | None = None) -> dict:
    return {"field": field, "label": label, "a": a, "b": b, "verdict": verdict, "note": note}


def compare(records_a: list[dict], records_b: list[dict]) -> list[dict[str, Any]]:
    rows = []

    # ---- name
    na, nb = _values(records_a, lambda r: [_name(r)]), _values(records_b, lambda r: [_name(r)])

    def parts(records: list[dict]) -> list[dict]:
        out = []
        for r in records:
            n = r.get("name") or {}
            out.append({"first": alpha_key(n.get("first_name")), "middle": alpha_key(n.get("middle_names")),
                        "last": alpha_key(n.get("last_name")), "suffix": n.get("suffix")})
        return out

    pa, pb = parts(records_a), parts(records_b)
    if not na or not nb:
        verdict, note = "missing", None
    elif _mark_shared(na, nb, alpha_key):
        verdict, note = "match", None
    else:
        same_first_last = any(x["first"] and x["first"] == y["first"] and x["last"] and x["last"] == y["last"]
                              for x in pa for y in pb)
        if same_first_last:
            verdict = "close"
            suffix_one_side = bool({x["suffix"] for x in pa} - {None}) != bool({y["suffix"] for y in pb} - {None})
            mids_a, mids_b = {x["middle"] for x in pa} - {""}, {y["middle"] for y in pb} - {""}
            if suffix_one_side:
                note = "Same name, but only one customer has a Jr/Sr suffix"
            elif mids_a and mids_b and not mids_a & mids_b:
                verdict, note = "differs", "Same forename and surname, different middle names"
            else:
                note = "Same forename and surname; middle name recorded on one side only"
        else:
            fa = {x["first"] for x in pa if x["first"]}
            fb = {y["first"] for y in pb if y["first"]}
            la = {x["last"] for x in pa if x["last"]}
            lb = {y["last"] for y in pb if y["last"]}
            forms_a = set().union(*[first_name_forms(x) for x in fa]) if fa else set()
            forms_b = set().union(*[first_name_forms(y) for y in fb]) if fb else set()
            first_ok = bool(forms_a & forms_b) or any(damerau(x, y) <= 1 for x in fa for y in fb)
            initial_ok = any((len(x) == 1 or len(y) == 1) and x[0] == y[0] for x in fa for y in fb)
            last_ok = bool(la & lb) or any(damerau(x, y) <= 1 for x in la for y in lb)
            if first_ok and last_ok:
                verdict, note = "close", "Nickname or spelling difference"
            elif initial_ok and last_ok:
                verdict, note = "close", "Initial only on one side"
            elif first_ok:
                verdict, note = "close", "Same forename, different surname"
            else:
                verdict, note = "differs", None
    rows.append(_row("name", "Name", na, nb, verdict, note))

    # ---- suffix (Jr / Sr)
    sa = {r["name"].get("suffix") for r in records_a if (r.get("name") or {}).get("suffix")}
    sb = {r["name"].get("suffix") for r in records_b if (r.get("name") or {}).get("suffix")}
    if sa and sb and not sa & sb:
        rows.append(_row("suffix", "Generation", _values(records_a, lambda r: [(r.get("name") or {}).get("suffix")]),
                         _values(records_b, lambda r: [(r.get("name") or {}).get("suffix")]), "conflict",
                         "Different generations, e.g. Jr and Sr"))

    # ---- date of birth
    def dobs(r: dict) -> list[str]:
        return [r["date_of_birth"]] if r.get("date_of_birth") and r.get("date_of_birth_usable") else []

    da, db = _values(records_a, dobs), _values(records_b, dobs)
    if not da or not db:
        verdict, note = "missing", None
    elif _mark_shared(da, db, lambda v: v):
        verdict, note = "match", None
        if len(da) > 1 or len(db) > 1:
            note = "More than one date recorded"
    elif any(not _dobs_conflict(x["value"], y["value"], "lenient") for x in da for y in db):
        verdict, note = "close", "Day and month swapped, or one field differs"
    else:
        verdict, note = "conflict", None
    rows.append(_row("dob", "Date of birth", da, db, verdict, note))

    # ---- gender
    ga = _values(records_a, lambda r: [r.get("gender")])
    gb = _values(records_b, lambda r: [r.get("gender")])
    if not ga or not gb:
        verdict = "missing"
    elif _mark_shared(ga, gb, lambda v: v):
        verdict = "match"
    else:
        verdict = "conflict"
    rows.append(_row("gender", "Gender", ga, gb, verdict))

    # ---- email
    def emails(r: dict) -> list[str]:
        return [e["address"] for e in r.get("emails") or []]

    canon = {e["address"]: e["canonical"] for r in records_a + records_b for e in r.get("emails") or []}
    ea, eb = _values(records_a, emails), _values(records_b, emails)
    if not ea or not eb:
        verdict = "missing"
    else:
        verdict = "match" if _mark_shared(ea, eb, lambda v: canon.get(v, v)) else "differs"
    rows.append(_row("email", "Email", ea, eb, verdict))

    # ---- phone
    def phones(r: dict) -> list[str]:
        return [p.get("national") or p["e164"] for p in r.get("phones") or []]

    e164 = {(p.get("national") or p["e164"]): p["e164"] for r in records_a + records_b for p in r.get("phones") or []}
    pa, pb = _values(records_a, phones), _values(records_b, phones)
    if not pa or not pb:
        verdict = "missing"
    else:
        verdict = "match" if _mark_shared(pa, pb, lambda v: e164.get(v, v)) else "differs"
    rows.append(_row("phone", "Phone", pa, pb, verdict))

    # ---- address (current and previous)
    def addresses(r: dict) -> list[str]:
        return [_addr(a) for a in [r.get("address"), *(r.get("previous_addresses") or [])] if a]

    postcode = {}
    for r in records_a + records_b:
        for a in [r.get("address"), *(r.get("previous_addresses") or [])]:
            if a and _addr(a):
                postcode[_addr(a)] = a.get("postcode")
    aa, ab = _values(records_a, addresses), _values(records_b, addresses)
    if not aa or not ab:
        verdict, note = "missing", None
    elif _mark_shared(aa, ab, lambda v: alnum_key(v)):
        verdict, note = "match", None
    elif {postcode.get(x["value"]) for x in aa} & {postcode.get(x["value"]) for x in ab} - {None}:
        verdict, note = "close", "Same postcode, address written differently"
    else:
        verdict, note = "differs", None
    rows.append(_row("address", "Address", aa, ab, verdict, note))
    return rows


def summarise(rows: list[dict]) -> dict[str, int]:
    out = {"match": 0, "close": 0, "differs": 0, "conflict": 0, "missing": 0}
    for r in rows:
        out[r["verdict"]] += 1
    return out

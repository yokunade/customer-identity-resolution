"""Engine checks for managers: evidence that matching links the right records.

Each check looks for something that would be a sign of a wrong decision, then
explains what it finds where the engine's rules account for it:

  Inside one customer (signs of over-merging): more than one date of birth,
  forename or surname, male and female records, Jr and Sr, a very large
  customer.
  Across customers (signs of missed matches): an email or mobile held by
  more than one customer, the same name and date of birth on more than one.

A finding the rules explain (a nickname, a day/month swap, a family email,
a pair a reviewer kept apart) is expected. The rest are listed first: those
are the ones to look at. Loading the page is logged to lookup_access.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any
from uuid import UUID

import psycopg

from app.config import Settings
from app.matching.rules import RULES_BY_KEY, damerau
from app.review.auth import User
from app.review.lookup import log_access, summaries

EXAMPLES = 25
BIG_CUSTOMER = 10  # records


class Finding:
    """One customer or one shared value that a check found, with its explanation."""

    def __init__(self, values: list[str], label: str, expected: bool, customers: list[UUID]) -> None:
        self.values, self.label, self.expected, self.customers = values, label, expected, customers


def _check(key: str, group: str, title: str, detects: str, findings: list[Finding]) -> dict[str, Any]:
    classes = Counter((f.label, f.expected) for f in findings)
    ordered = sorted(findings, key=lambda f: f.expected)  # what to look at first
    return {
        "key": key, "group": group, "title": title, "detects": detects,
        "found": len(findings), "to_look_at": sum(not f.expected for f in findings),
        "classes": [{"label": label, "expected": exp, "count": n}
                    for (label, exp), n in sorted(classes.items(), key=lambda x: (x[0][1], -x[1]))],
        "examples": ordered[:EXAMPLES],
    }


# ------------------------------------------------------------------ pair explanations


def _dob_pair(a: str, b: str) -> tuple[str, bool]:
    ya, ma, da = a.split("-")
    yb, mb, db = b.split("-")
    if ya == yb and ma == db and da == mb:
        return "Day and month swapped", True
    if (ya != yb) + (ma != mb) + (da != db) == 1:
        return "One part different (typing slip)", True
    return "Different dates", False


def _forename_pair(a: dict, b: dict) -> tuple[str, bool]:
    ka, kb = a["key"], b["key"]
    if set(a["forms"] or ()) & set(b["forms"] or ()):
        return "Short form or nickname", True
    if min(len(ka), len(kb)) == 1 and ka[0] == kb[0]:
        return "Initial only", True
    if damerau(ka, kb) <= (2 if min(len(ka), len(kb)) >= 8 else 1):
        return "Spelling slip", True
    if ka[0] == kb[0] and sorted(ka) == sorted(kb):
        return "Letters swapped", True
    return "Different forenames", False


def _surname_pair(a: str, b: str) -> tuple[str, bool]:
    if a in b or b in a:
        return "Double-barrelled or added surname", True
    if damerau(a, b) <= 2:
        return "Spelling slip", True
    # Linked on forename + DOB + email, mobile or home: usually marriage.
    return "Changed surname", True


def _explain_set(items: list, pair) -> tuple[str, bool]:
    """Explained if every value is tied to the others through explained pairs:
    Robert, Robeert and Robbie are one person even though Robeert and Robbie
    are not alike. Otherwise the pair label that fails to explain them."""
    group = list(range(len(items)))

    def root(i: int) -> int:
        while group[i] != i:
            i = group[i]
        return i

    explained: Counter = Counter()
    failed = None
    for (i, a), (j, b) in combinations(enumerate(items), 2):
        label, ok = pair(a, b)
        if ok:
            explained[label] += 1
            group[root(i)] = root(j)
        else:
            failed = label
    if len({root(i) for i in range(len(items))}) > 1:
        return failed, False  # type: ignore[return-value]
    return explained.most_common(1)[0][0], True


def _same_forename(a: list[dict], b: list[dict]) -> bool:
    return any(_forename_pair(x, y)[1] for x in a for y in b)


# ------------------------------------------------------------------ inside one customer


def _dobs(conn: psycopg.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT customer_id, array_agg(DISTINCT match_profile->>'dob') AS v FROM source_record"
        " WHERE match_profile->>'dob' IS NOT NULL GROUP BY customer_id"
        " HAVING count(DISTINCT match_profile->>'dob') > 1"
    ).fetchall()
    return [Finding(sorted(r["v"]), *_explain_set(sorted(r["v"]), _dob_pair), [r["customer_id"]]) for r in rows]


def _forenames(conn: psycopg.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT customer_id, jsonb_agg(DISTINCT jsonb_build_object('key', match_profile->>'first_key',"
        " 'forms', match_profile->'first_forms', 'shown', cleansed->'name'->>'first_name')) AS v"
        " FROM source_record WHERE match_profile->>'first_key' IS NOT NULL GROUP BY customer_id"
        " HAVING count(DISTINCT match_profile->>'first_key') > 1"
    ).fetchall()
    out = []
    for r in rows:
        names = list({n["key"]: n for n in r["v"]}.values())
        label, ok = _explain_set(names, _forename_pair)
        out.append(Finding(sorted({n["shown"] or n["key"] for n in names}), label, ok, [r["customer_id"]]))
    return out


def _surnames(conn: psycopg.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT customer_id, array_agg(DISTINCT match_profile->>'last_key') AS k,"
        " array_agg(DISTINCT cleansed->'name'->>'last_name') AS shown FROM source_record"
        " WHERE match_profile->>'last_key' IS NOT NULL GROUP BY customer_id"
        " HAVING count(DISTINCT match_profile->>'last_key') > 1"
    ).fetchall()
    return [Finding(sorted(x for x in r["shown"] if x), *_explain_set(r["k"], _surname_pair), [r["customer_id"]])
            for r in rows]


def _distinct(conn: psycopg.Connection, field: str, label: str, shown: dict[str, str]) -> list[Finding]:
    rows = conn.execute(
        f"SELECT customer_id, array_agg(DISTINCT match_profile->>'{field}') AS v FROM source_record"
        f" WHERE match_profile->>'{field}' IS NOT NULL GROUP BY customer_id"
        f" HAVING count(DISTINCT match_profile->>'{field}') > 1"
    ).fetchall()
    return [Finding([shown.get(v, v) for v in sorted(r["v"])], label, False, [r["customer_id"]]) for r in rows]


def _big(conn: psycopg.Connection) -> list[Finding]:
    rows = conn.execute(
        "SELECT customer_id, count(*) AS n FROM source_record GROUP BY customer_id HAVING count(*) > %s",
        (BIG_CUSTOMER,),
    ).fetchall()
    return [Finding([f"{r['n']} records"], f"More than {BIG_CUSTOMER} records", False, [r["customer_id"]])
            for r in rows]


# ------------------------------------------------------------------ across customers


class Pairs:
    """Open review cases and pairs kept apart, to explain customers that share something."""

    def __init__(self, conn: psycopg.Connection) -> None:
        rows = conn.execute(
            "SELECT customer_a, customer_b, status FROM merge_candidate WHERE status IN ('open', 'rejected')"
        ).fetchall()
        self.open = {frozenset((r["customer_a"], r["customer_b"])) for r in rows if r["status"] == "open"}
        self.apart = {frozenset((r["customer_a"], r["customer_b"])) for r in rows if r["status"] == "rejected"}


def _forenames_of(conn: psycopg.Connection, ids: list[UUID]) -> dict[UUID, list[dict]]:
    rows = conn.execute(
        "SELECT customer_id, jsonb_agg(DISTINCT jsonb_build_object('key', match_profile->>'first_key',"
        " 'forms', match_profile->'first_forms')) AS v FROM source_record"
        " WHERE customer_id = ANY(%s) AND match_profile->>'first_key' IS NOT NULL GROUP BY customer_id",
        (ids,),
    ).fetchall()
    return {r["customer_id"]: r["v"] for r in rows}


def _explain_shared(customers: list[UUID], names: dict[UUID, list[dict]], pairs: Pairs,
                    same_name_given: bool = False) -> tuple[str, bool]:
    same = [frozenset(p) for p in combinations(customers, 2)
            if same_name_given or _same_forename(names.get(p[0], []), names.get(p[1], []))]
    if not same:
        return "Different people (family or household)", True
    if all(p in pairs.apart for p in same):
        return "Kept apart by a reviewer or a move", True
    if any(p in pairs.open for p in same):
        return "Waiting for review", True
    return "Same person in separate customers?", False


def _shared(conn: psycopg.Connection, settings: Settings, pairs: Pairs, kind: str) -> list[Finding]:
    if kind == "email":
        sql = ("SELECT x->>'canonical' AS v, array_agg(DISTINCT sr.customer_id) AS c FROM source_record sr,"
               " jsonb_array_elements(sr.cleansed->'emails') x WHERE (x->>'usable_for_matching')::bool"
               " GROUP BY 1 HAVING count(DISTINCT sr.customer_id) > 1")
    else:
        sql = ("SELECT coalesce(x->>'national', x->>'e164') AS v, array_agg(DISTINCT sr.customer_id) AS c"
               " FROM source_record sr, jsonb_array_elements(sr.cleansed->'phones') x"
               " WHERE (x->>'usable_for_matching')::bool AND x->>'type' = 'mobile'"
               " GROUP BY 1 HAVING count(DISTINCT sr.customer_id) > 1")
    rows = conn.execute(sql).fetchall()
    limit = settings.shared_contact_customers
    small = [r for r in rows if len(r["c"]) <= limit]
    names = _forenames_of(conn, list({c for r in small for c in r["c"]}))
    out = []
    for r in rows:
        if len(r["c"]) > limit:
            out.append(Finding([r["v"], f"{len(r['c'])} customers"], "Too widely shared to match on", True, r["c"]))
        else:
            out.append(Finding([r["v"]], *_explain_shared(r["c"], names, pairs), r["c"]))
    return out


def _name_dob(conn: psycopg.Connection, pairs: Pairs) -> list[Finding]:
    rows = conn.execute(
        "SELECT match_profile->>'dob' AS dob, min(cleansed->'name'->>'first_name') AS first,"
        " min(cleansed->'name'->>'last_name') AS last, array_agg(DISTINCT customer_id) AS c FROM source_record"
        " WHERE match_profile->>'dob' IS NOT NULL AND match_profile->>'first_key' IS NOT NULL"
        " AND match_profile->>'last_key' IS NOT NULL"
        " GROUP BY match_profile->>'first_key', match_profile->>'last_key', match_profile->>'dob'"
        " HAVING count(DISTINCT customer_id) > 1"
    ).fetchall()
    out = []
    for r in rows:
        label, ok = _explain_shared(r["c"], {}, pairs, same_name_given=True)
        if not ok:
            label = "Nothing else in common to link them"
        out.append(Finding([f"{r['first']} {r['last']}", r["dob"]], label, ok, r["c"]))
    return out


# ------------------------------------------------------------------ summary


def _summary(conn: psycopg.Connection) -> dict[str, Any]:
    s = conn.execute(
        "SELECT (SELECT count(*) FROM source_record) AS records,"
        " (SELECT count(*) FROM customer WHERE status = 'active') AS customers,"
        " (SELECT count(*) FROM merge_candidate WHERE status = 'open') AS open_cases,"
        " (SELECT count(*) FROM merge_candidate WHERE status = 'rejected' AND rule <> 'SPLIT') AS kept_apart"
    ).fetchone()
    events = conn.execute(
        "SELECT outcome, rule, coalesce((details->>'auto')::bool, false) AS auto, count(*) AS n"
        " FROM resolution_event WHERE outcome IN ('new_customer', 'matched', 'relinked', 'merge', 'review_merge',"
        " 'split') AND (outcome <> 'split' OR record_id IS NULL) GROUP BY 1, 2, 3"
    ).fetchall()
    tiers = Counter()
    merges = Counter()
    for e in events:
        if e["outcome"] in ("matched", "relinked") and e["rule"] in RULES_BY_KEY:
            tiers[RULES_BY_KEY[e["rule"]].tier] += e["n"]
        elif e["outcome"] == "merge":
            merges["automatic" if e["auto"] else "api"] += e["n"]
        elif e["outcome"] == "review_merge":
            merges["reviewer"] += e["n"]
        elif e["outcome"] == "split":
            merges["moved_out"] += e["n"]
        elif e["outcome"] == "new_customer":
            tiers["new"] += e["n"]
    # A reviewer's merge is logged as a merge too, with the case in its reason.
    merges["api"] = max(0, merges["api"] - merges["reviewer"])
    return {**s, "links": {t: tiers.get(t, 0) for t in ("new", "exact", "strong", "probable", "possible")},
            "merges": {k: merges.get(k, 0) for k in ("automatic", "reviewer", "api", "moved_out")}}


def run(conn: psycopg.Connection, settings: Settings, user: User) -> dict[str, Any]:
    pairs = Pairs(conn)
    checks = [
        _check("dob", "inside", "More than one date of birth",
               "Customers whose records give different dates of birth.", _dobs(conn)),
        _check("forename", "inside", "More than one forename",
               "Customers whose records give different forenames.", _forenames(conn)),
        _check("surname", "inside", "More than one surname",
               "Customers whose records give different surnames.", _surnames(conn)),
        _check("gender", "inside", "Male and female records",
               "Customers with a record for a man and one for a woman (stated, or inferred from the forename).",
               _distinct(conn, "gender", "Male and female", {"M": "Male", "F": "Female"})),
        _check("generation", "inside", "Jr and Sr together",
               "Customers holding a Jr record and a Sr record.", _distinct(conn, "generation", "Jr and Sr", {})),
        _check("big", "inside", "Very large customers",
               f"Customers with more than {BIG_CUSTOMER} source records.", _big(conn)),
        _check("email", "across", "Email on more than one customer",
               "Email addresses held by more than one customer.", _shared(conn, settings, pairs, "email")),
        _check("mobile", "across", "Mobile on more than one customer",
               "Mobile numbers held by more than one customer.", _shared(conn, settings, pairs, "mobile")),
        _check("name_dob", "across", "Same name and date of birth on more than one customer",
               "Customers with the same forename, surname and date of birth.", _name_dob(conn, pairs)),
    ]
    summary = _summary(conn)
    shown = list(dict.fromkeys(c for ch in checks for f in ch["examples"] for c in f.customers))
    people = {s["customer_id"]: s for s in summaries(conn, shown)}
    conn.rollback()
    log_access(conn, user, "checks", shown)
    for ch in checks:
        ch["examples"] = [{
            "values": f.values, "label": f.label, "expected": f.expected,
            "customers": [{"customer_id": str(c), "name": people.get(str(c), {}).get("name"),
                           "record_count": people.get(str(c), {}).get("record_count")} for c in f.customers[:6]],
            "more_customers": max(0, len(f.customers) - 6),
        } for f in ch["examples"]]
    return {"summary": summary, "checks": checks}

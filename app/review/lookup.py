"""Customer lookup for managers: find customers by email, phone, postcode, name and
date of birth, or ID, and show everything held about one customer.

What is typed is cleansed exactly as an inbound record would be, then found
through the same blocking keys the matcher uses, so "Bill.Smith+promo@googlemail.com"
finds billsmith@gmail.com and any format of a phone number finds it.

Every search and every customer view is written to lookup_access with the
user, the cleansed value, the customers shown and an optional reason.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import psycopg

from app.cleansing.dob import parse_dob
from app.cleansing.email import cleanse_email
from app.cleansing.nicknames import first_name_forms
from app.cleansing.phone import cleanse_phone
from app.cleansing.postcode import parse_postcode
from app.cleansing.text import alpha_key
from app.config import Settings
from app.matching.keys import BLOCKING_KEYS, blocking_key_value, build_keys
from app.matching.phonetics import soundex
from app.matching.rules import RULES_BY_KEY
from app.repository import Repository
from app.review.auth import User

MAX_RESULTS = 50
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_PHONEISH = re.compile(r"^[\d\s+()\-.]{7,}$")


class LookupFailed(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ------------------------------------------------------------------ access log


def check_rate(conn: psycopg.Connection, user: User, settings: Settings) -> None:
    n = conn.execute(
        "SELECT count(*) AS n FROM lookup_access WHERE user_id = %s AND accessed_at > now() - interval '1 hour'",
        (user.user_id,),
    ).fetchone()["n"]
    conn.rollback()
    if n >= settings.lookup_max_per_hour:
        raise LookupFailed(429, "Lookup limit reached for this hour. Try again later.")


def log_access(conn: psycopg.Connection, user: User, action: str, customer_ids: list[UUID], *,
               search_type: str | None = None, search_value: str | None = None, reason: str | None = None) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO lookup_access (user_id, action, search_type, search_value, customer_ids, reason)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (user.user_id, action, search_type, search_value, customer_ids, reason),
        )


# ------------------------------------------------------------------ search


def detect(q: str) -> str:
    q = q.strip()
    if "@" in q:
        return "email"
    if _UUID.match(q):
        return "customer_id"
    if "/" in q:
        return "source_record"
    if parse_postcode(q):
        return "postcode"
    if _PHONEISH.match(q):
        return "phone"
    return "name_dob"


def _by_key(conn: psycopg.Connection, key_type: str, value: bytes, extra: str = "", params: tuple = ()) -> list[UUID]:
    rows = conn.execute(
        "SELECT sr.customer_id FROM blocking_key b JOIN source_record sr ON sr.record_id = b.record_id"
        " WHERE b.key_type = %s AND b.key_value = %s" + extra +
        " GROUP BY sr.customer_id ORDER BY max(sr.updated_at) DESC LIMIT %s",
        (key_type, value, *params, MAX_RESULTS + 1),
    ).fetchall()
    return [r["customer_id"] for r in rows]


def search(conn: psycopg.Connection, settings: Settings, user: User, q: str, *, kind: str = "auto",
           dob: str | None = None, reason: str | None = None) -> dict[str, Any]:
    q = q.strip()
    if not q:
        raise LookupFailed(422, "Type something to search for.")
    kind = detect(q) if kind == "auto" else kind
    secret = settings.key_secret
    repo = Repository(conn)
    shared_key: tuple[str, bytes, int] | None = None

    if kind == "email":
        email, _ = cleanse_email(q)
        if email is None:
            raise LookupFailed(422, "Not a valid email address.")
        if not email.usable_for_matching:
            raise LookupFailed(422, "Placeholder and role addresses (info@, noreply@ ...) are not indexed.")
        value = email.canonical
        key = blocking_key_value("B_EMAIL", value, secret)
        ids = _by_key(conn, "B_EMAIL", key)
        shared_key = ("B_EMAIL", key, settings.shared_contact_customers)
    elif kind == "phone":
        phone, _ = cleanse_phone(q)
        if phone is None:
            raise LookupFailed(422, "Not a valid phone number.")
        if not phone.usable_for_matching:
            raise LookupFailed(422, "Placeholder, drama-range and non-personal numbers are not indexed.")
        value = phone.e164
        key = blocking_key_value("B_PHONE", value, secret)
        ids = _by_key(conn, "B_PHONE", key)
        shared_key = ("B_PHONE", key, settings.shared_contact_customers)
    elif kind == "postcode":
        pc = parse_postcode(q)
        if pc is None:
            raise LookupFailed(422, "Not a valid UK postcode.")
        value = pc.full
        key = blocking_key_value("B_POSTCODE", pc.compact, secret)
        ids = _by_key(conn, "B_POSTCODE", key)
        shared_key = ("B_POSTCODE", key, settings.shared_postcode_customers)
    elif kind == "customer_id":
        if not _UUID.match(q):
            raise LookupFailed(422, "Not a customer ID.")
        value = q.lower()
        ids = [repo.survivor(UUID(value))] if repo.get_customer(UUID(value)) else []
    elif kind == "source_record":
        system, _, rec_id = q.partition("/")
        value = f"{system.strip().upper()}/{rec_id.strip()}"
        row = repo.find_source_record(system.strip().upper(), rec_id.strip())
        ids = [repo.survivor(row["customer_id"])] if row else []
    elif kind == "name_dob":
        if not dob:
            raise LookupFailed(422, "A name alone is too broad: add a date of birth, or search by email or phone.")
        d, usable, _ = parse_dob(dob)
        parts = [alpha_key(p) for p in q.split()]
        parts = [p for p in parts if p]
        if d is None or not usable:
            raise LookupFailed(422, "Not a usable date of birth.")
        if len(parts) < 2:
            raise LookupFailed(422, "Give a forename and a surname.")
        first, last = parts[0], parts[-1]
        comps = {"dob": frozenset({d.isoformat()}), "last_soundex": frozenset({soundex(last)})}
        key = next(iter(build_keys(comps, {"B_DOB_LAST_SOUNDEX": BLOCKING_KEYS["B_DOB_LAST_SOUNDEX"]},
                                   secret)["B_DOB_LAST_SOUNDEX"]))
        value = f"{first} {last} {d.isoformat()}"
        ids = _by_key(conn, "B_DOB_LAST_SOUNDEX", key,
                      " AND sr.match_profile->>'last_key' = %s AND sr.match_profile->'first_forms' ?| %s",
                      (last, sorted(first_name_forms(first))))
    else:
        raise LookupFailed(422, f"Cannot search by {kind}.")

    truncated = len(ids) > MAX_RESULTS
    ids = ids[:MAX_RESULTS]
    shared = False
    if shared_key is not None and len(ids) > shared_key[2]:
        held = repo.block_customers([(shared_key[0], shared_key[1])], shared_key[2])
        shared = held.get((shared_key[0], shared_key[1]), 0) > shared_key[2]
    customers = summaries(conn, ids)
    conn.rollback()
    log_access(conn, user, "search", ids, search_type=kind, search_value=value, reason=reason)
    return {"searched_by": kind, "normalised_value": value, "shared": shared, "truncated": truncated,
            "customers": customers}


def _name(cleansed: dict) -> str | None:
    n = cleansed.get("name") or {}
    return " ".join(x for x in (n.get("first_name"), n.get("last_name")) if x) or None


def summaries(conn: psycopg.Connection, ids: list[UUID]) -> list[dict]:
    """One line per customer, in the order given: the newest record's name, DOB and postcode."""
    if not ids:
        return []
    rows = conn.execute(
        "SELECT sr.customer_id, count(*) AS n, array_agg(DISTINCT sr.source_system) AS sources,"
        " max(sr.updated_at) AS updated_at,"
        " (array_agg(sr.cleansed ORDER BY sr.updated_at DESC, sr.record_id DESC))[1] AS latest,"
        " EXISTS (SELECT 1 FROM merge_candidate m WHERE m.status = 'open'"
        "         AND sr.customer_id IN (m.customer_a, m.customer_b)) AS open_case"
        " FROM source_record sr WHERE sr.customer_id = ANY(%s) GROUP BY sr.customer_id",
        (ids,),
    ).fetchall()
    by_id = {r["customer_id"]: r for r in rows}
    out = []
    for cid in ids:
        r = by_id.get(cid)
        if r is None:
            continue
        latest = r["latest"]
        addr = latest.get("address") or {}
        out.append({
            "customer_id": str(cid), "name": _name(latest), "date_of_birth": latest.get("date_of_birth"),
            "postcode": addr.get("postcode"), "record_count": r["n"], "sources": sorted(r["sources"]),
            "updated_at": r["updated_at"], "open_case": r["open_case"],
            "emails": [e["canonical"] for e in latest.get("emails", [])],
            "phones": [p.get("national") or p["e164"] for p in latest.get("phones", [])],
        })
    return out


# ------------------------------------------------------------------ one customer


def _add(acc: dict[str, dict], value: str | None, source: str, **extra: Any) -> None:
    if not value:
        return
    item = acc.setdefault(value, {"value": value, "sources": [], **extra})
    if source not in item["sources"]:
        item["sources"].append(source)
        item["sources"].sort()


def customer_view(conn: psycopg.Connection, settings: Settings, user: User, customer_id: UUID, *,
                  reason: str | None = None) -> dict[str, Any]:
    repo = Repository(conn)
    if repo.get_customer(customer_id) is None:
        conn.rollback()
        raise LookupFailed(404, "No such customer.")
    cid = repo.survivor(customer_id)
    cust = repo.get_customer(cid)
    conn.rollback()
    log_access(conn, user, "view_customer", [cid], reason=reason)

    records = conn.execute(
        "SELECT sr.record_id, sr.source_system, sr.source_record_id, sr.raw, sr.cleansed, sr.data_quality,"
        " sr.received_at, sr.updated_at, u.display_name AS added_by, m.added_at, m.note"
        " FROM source_record sr LEFT JOIN manual_record m USING (record_id)"
        " LEFT JOIN review_user u ON u.user_id = m.added_by WHERE sr.customer_id = %s ORDER BY sr.record_id", (cid,),
    ).fetchall()
    rids = [r["record_id"] for r in records]
    first_events = {e["record_id"]: e for e in conn.execute(
        "SELECT DISTINCT ON (record_id) record_id, outcome, rule, created_at FROM resolution_event"
        " WHERE record_id = ANY(%s) ORDER BY record_id, event_id", (rids,),
    ).fetchall()}
    merged_from = [r["customer_id"] for r in conn.execute(
        "SELECT customer_id FROM customer WHERE merged_into = %s ORDER BY updated_at", (cid,)).fetchall()]
    history = conn.execute(
        "SELECT event_id, record_id, outcome, rule, created_at, details->>'relinked_from' AS relinked_from,"
        " details->>'merged_customer_id' AS merged_customer_id, details->>'reason' AS reason"
        " FROM resolution_event WHERE record_id = ANY(%s) OR (record_id IS NULL AND customer_id = ANY(%s))"
        " ORDER BY event_id DESC LIMIT 100", (rids, [cid, *merged_from]),
    ).fetchall()
    cases = conn.execute(
        "SELECT case_id, rule, created_at, CASE WHEN customer_a = %s THEN customer_b ELSE customer_a END AS other"
        " FROM merge_candidate WHERE status = 'open' AND %s IN (customer_a, customer_b) ORDER BY created_at",
        (cid, cid),
    ).fetchall()
    access = conn.execute(
        "SELECT u.display_name, a.accessed_at, a.action, a.reason FROM lookup_access a"
        " JOIN review_user u USING (user_id) WHERE a.customer_ids @> ARRAY[%s]::uuid[]"
        " ORDER BY a.access_id DESC LIMIT 10", (cid,),
    ).fetchall()

    names: dict[str, dict] = {}
    dobs: dict[str, dict] = {}
    emails: dict[str, dict] = {}
    phones: dict[str, dict] = {}
    addresses: dict[str, dict] = {}
    current = None
    for r in sorted(records, key=lambda r: (r["updated_at"], r["record_id"])):
        c, src = r["cleansed"], r["source_system"]
        _add(names, _name(c), src)
        if c.get("date_of_birth") and c.get("date_of_birth_usable"):
            _add(dobs, c["date_of_birth"], src)
        for e in c.get("emails", []):
            _add(emails, e["canonical"], src, usable=e.get("usable_for_matching", True), shared=False)
        for p in c.get("phones", []):
            _add(phones, p["e164"], src, national=p.get("national"), type=p.get("type"),
                 usable=p.get("usable_for_matching", True), shared=False)
        for i, a in enumerate([c.get("address"), *c.get("previous_addresses", [])]):
            if not a:
                continue
            text = ", ".join(a.get("lines") or []) or a.get("postcode")
            _add(addresses, text, src, postcode=a.get("postcode"), uprn=a.get("uprn"), current=False)
            if i == 0:
                current = text
    if current in addresses:
        addresses[current]["current"] = True

    # Mark contact values held by so many customers that matching ignores them.
    limit = settings.shared_contact_customers
    keys = {("B_EMAIL", blocking_key_value("B_EMAIL", v, settings.key_secret)): emails[v]
            for v in emails if emails[v]["usable"]}
    keys |= {("B_PHONE", blocking_key_value("B_PHONE", v, settings.key_secret)): phones[v]
             for v in phones if phones[v]["usable"]}
    for k, n in repo.block_customers(list(keys), limit).items():
        keys[k]["shared"] = n > limit
    conn.rollback()

    def linked_by(rid: int) -> dict | None:
        e = first_events.get(rid)
        if e is None:
            return None
        rule = RULES_BY_KEY.get(e["rule"]) if e["rule"] else None
        return {"outcome": e["outcome"], "rule": e["rule"], "tier": rule.tier if rule else None,
                "at": e["created_at"]}

    return {
        "customer_id": str(cid), "requested_customer_id": str(customer_id), "status": cust["status"],
        "created_at": cust["created_at"], "merged_from": [str(m) for m in merged_from],
        "profile": {
            "names": list(names.values()), "dates_of_birth": list(dobs.values()),
            "emails": list(emails.values()), "phones": list(phones.values()),
            "addresses": sorted(addresses.values(), key=lambda a: not a["current"]),
        },
        "records": [{
            "record_id": r["record_id"], "source_system": r["source_system"],
            "source_record_id": r["source_record_id"], "received_at": r["received_at"],
            "updated_at": r["updated_at"], "raw": r["raw"], "cleansed": r["cleansed"],
            "data_quality": r["data_quality"], "linked_by": linked_by(r["record_id"]),
            "added_by": {"name": r["added_by"], "at": r["added_at"], "note": r["note"]} if r["added_at"] else None,
        } for r in records],
        "history": [dict(h) for h in history],
        "open_cases": [{"case_id": c["case_id"], "rule": c["rule"], "created_at": c["created_at"],
                        "other_customer_id": str(c["other"])} for c in cases],
        "recent_access": [dict(a) for a in access],
    }


def recent(conn: psycopg.Connection, user: User, limit: int = 8) -> list[dict]:
    """This user's latest distinct searches, newest first, each with the first customer found."""
    rows = conn.execute(
        "SELECT * FROM (SELECT DISTINCT ON (search_type, search_value) access_id, accessed_at, search_type,"
        " search_value, reason, customer_ids FROM lookup_access WHERE user_id = %s AND action = 'search'"
        " AND accessed_at > now() - interval '30 days' ORDER BY search_type, search_value, access_id DESC) s"
        " ORDER BY access_id DESC LIMIT %s",
        (user.user_id, limit),
    ).fetchall()
    firsts = [r["customer_ids"][0] for r in rows if r["customer_ids"]]
    names = {s["customer_id"]: s["name"] for s in summaries(conn, list(dict.fromkeys(firsts)))}
    conn.rollback()
    return [{
        "accessed_at": r["accessed_at"], "search_type": r["search_type"], "search_value": r["search_value"],
        "reason": r["reason"], "customers": len(r["customer_ids"]),
        "customer_id": str(r["customer_ids"][0]) if r["customer_ids"] else None,
        "name": names.get(str(r["customer_ids"][0])) if r["customer_ids"] else None,
    } for r in rows]

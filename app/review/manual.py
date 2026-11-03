"""Records added by hand: a manager types a customer's details, checks what custIDR
would do with them, then saves.

A saved record is an ordinary source record in the reserved source system MANUAL,
numbered from a sequence (MANUAL/M-000001), so it is cleansed, matched and linked
exactly like a record from a feed. manual_record keeps who added it, when and why.
Feeds cannot send MANUAL records, so nothing else can overwrite one.

Checking and saving are both written to lookup_access: a check shows the customers
the details would join, which is a lookup in all but name.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.matching.keys import MATCH_KEYS
from app.review.auth import User
from app.review.lookup import LookupFailed, check_rate, log_access, summaries
from app.schemas import InboundAddress, InboundCustomer
from app.service import ResolutionService

SOURCE = "MANUAL"
MAX_CONTACTS = 5

Text = Annotated[str, Field(max_length=200)]
Short = Annotated[str, Field(max_length=50)]


class ManualAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line1: Text | None = None
    line2: Text | None = None
    town: Text | None = None
    county: Text | None = None
    postcode: Short | None = None


class ManualRecord(BaseModel):
    """The form. Blank strings count as not given."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: Short | None = None
    first_name: Text | None = None
    middle_names: Text | None = None
    last_name: Text | None = None
    gender: Short | None = None
    date_of_birth: Short | None = None
    emails: list[Annotated[str, Field(max_length=254)]] = Field(default_factory=list, max_length=MAX_CONTACTS)
    phones: list[Short] = Field(default_factory=list, max_length=MAX_CONTACTS)
    address: ManualAddress | None = None
    note: Annotated[str, Field(max_length=500)] | None = None


class ManualSave(ManualRecord):
    submit_token: UUID = Field(description="New for each form; a repeated save with the same token adds nothing.")


def _given(v: str | None) -> str | None:
    return v.strip() or None if v else None


def to_inbound(body: ManualRecord, source_record_id: str) -> InboundCustomer:
    addr = body.address.model_dump() if body.address else {}
    addr = {k: _given(v) for k, v in addr.items() if _given(v)}
    return InboundCustomer(
        source_system=SOURCE, source_record_id=source_record_id,
        title=_given(body.title), first_name=_given(body.first_name), middle_names=_given(body.middle_names),
        last_name=_given(body.last_name), gender=_given(body.gender), date_of_birth=_given(body.date_of_birth),
        emails=[e for e in map(_given, body.emails) if e], phones=[p for p in map(_given, body.phones) if p],
        address=InboundAddress(**addr) if addr else None,
    )


def _require(body: ManualRecord) -> None:
    if not _given(body.last_name):
        raise LookupFailed(422, "Enter the customer's last name.")
    postcode = _given(body.address.postcode) if body.address else None
    if not (_given(body.date_of_birth) or postcode or any(map(_given, body.emails)) or any(map(_given, body.phones))):
        raise LookupFailed(422, "Add at least one of: date of birth, email, phone or postcode.")


def check(conn: psycopg.Connection, settings: Settings, svc: ResolutionService, user: User,
          body: ManualRecord) -> dict[str, Any]:
    """What saving would do now: the cleansed values, and which customer the record
    would join or which review cases it would raise. Nothing is stored."""
    _require(body)
    check_rate(conn, user, settings)
    p, found, review = svc.preview(conn, to_inbound(body, "M-CHECK"))
    d = found.decision
    joins = d.customer_id if found.auto_link else None
    shown = [x for x in [joins, *review] if x is not None]
    log_access(conn, user, "add_record_check", shown)
    people = {s["customer_id"]: s for s in summaries(conn, shown)}
    conn.rollback()
    return {
        "cleansed": p.cleansed.model_dump(mode="json"),  # type: ignore[attr-defined]
        "data_quality": [i.model_dump() for i in p.issues],
        "outcome": "matched" if joins else "new_customer",
        "rule": d.rule.key if d.rule else None,
        "rule_description": MATCH_KEYS[d.rule.key].description if d.rule and d.rule.key in MATCH_KEYS else None,
        "tier": d.rule.tier if d.rule else None,
        "customer": people.get(str(joins)) if joins else None,
        "review_with": [people[str(x)] for x in review if str(x) in people],
        "shared_identifiers": found.shared_identifiers,
    }


def _saved(conn: psycopg.Connection, token: UUID) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT sr.record_id, sr.source_record_id, c.customer_id FROM manual_record m"
        " JOIN source_record sr USING (record_id) JOIN customer c ON c.customer_id = sr.customer_id"
        " WHERE m.submit_token = %s", (token,),
    ).fetchone()
    if row is None:
        return None
    cases = [r["case_id"] for r in conn.execute(
        "SELECT case_id FROM merge_candidate WHERE record_id = %s AND status = 'open' ORDER BY case_id",
        (row["record_id"],)).fetchall()]
    return {"record_id": row["record_id"], "source_system": SOURCE, "source_record_id": row["source_record_id"],
            "customer_id": str(row["customer_id"]), "review_cases": cases}


def save(conn: psycopg.Connection, settings: Settings, svc: ResolutionService, user: User,
         body: ManualSave) -> dict[str, Any]:
    """Add the record and resolve it like any inbound record. Saving the same form
    twice (same submit_token) returns the first result instead of adding again."""
    _require(body)
    done = _saved(conn, body.submit_token)
    conn.rollback()
    if done:
        return {**done, "outcome": "already_saved"}
    check_rate(conn, user, settings)
    try:
        with conn.transaction():
            n = conn.execute("SELECT nextval('manual_record_seq') AS n").fetchone()["n"]
            res = svc.resolve(conn, to_inbound(body, f"M-{n:06d}"))
            conn.execute(
                "INSERT INTO manual_record (record_id, added_by, note, submit_token) VALUES (%s, %s, %s, %s)",
                (res.record_id, user.user_id, _given(body.note), body.submit_token),
            )
    except psycopg.errors.UniqueViolation:
        # The same form saved twice at once: the other save won and this one rolled back.
        done = _saved(conn, body.submit_token)
        conn.rollback()
        if done is None:
            raise
        return {**done, "outcome": "already_saved"}
    result = _saved(conn, body.submit_token)
    conn.rollback()
    assert result is not None
    log_access(conn, user, "add_record", [res.customer_id], search_type="source_record",
               search_value=f"{SOURCE}/{result['source_record_id']}")
    return {**result, "outcome": res.outcome}

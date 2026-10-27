"""Review cases: claiming, viewing, deciding.

A case is a merge_candidate row: two customers that both matched an inbound
record at the same rule, or a suggested link held back by AUTO_LINK_TIERS.

Workflow
  open --take--> claimed (by one reviewer, for CLAIM_MINUTES)
  claimed --decide merge--> merged      (customers merged, tables updated)
  claimed --decide keep apart--> rejected (pair is never suggested again)
  claimed --release / expiry--> open
  open --either customer merged elsewhere--> repointed, or obsolete
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from app.config import Settings
from app.matching.keys import MATCH_KEYS
from app.matching.rules import RULES_BY_KEY
from app.repository import Repository
from app.review.auth import User
from app.review.compare import compare, summarise
from app.schemas import MergeRequest
from app.service import MergeError, ResolutionService

CLAIM_MINUTES = 30
MIN_NOTES = 3


class CaseError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ------------------------------------------------------------------ queue


def stats(conn: psycopg.Connection, user: User) -> dict[str, int]:
    row = conn.execute(
        "SELECT"
        " count(*) FILTER (WHERE status = 'open') AS open,"
        " count(*) FILTER (WHERE status = 'open' AND claimed_by = %(u)s"
        "                  AND claimed_at > now() - make_interval(mins => %(m)s)) AS mine,"
        " count(*) FILTER (WHERE decided_by = %(u)s AND decided_at >= date_trunc('day', now())) AS decided_today,"
        " count(*) FILTER (WHERE decided_at >= date_trunc('day', now())) AS team_today"
        " FROM merge_candidate",
        {"u": user.user_id, "m": CLAIM_MINUTES},
    ).fetchone()
    conn.rollback()
    return dict(row)


def _reconcile(conn: psycopg.Connection, case: dict) -> str:
    """Bring a case up to date with merges made since it was raised.
    Returns 'ok', or 'obsolete' if there is nothing left to decide."""
    repo = Repository(conn)
    a, b = repo.survivor(case["customer_a"]), repo.survivor(case["customer_b"])
    if a == b:
        conn.execute("UPDATE merge_candidate SET status = 'obsolete', claimed_by = NULL WHERE case_id = %s",
                     (case["case_id"],))
        return "obsolete"
    lo, hi = sorted([a, b], key=str)
    if (lo, hi) != (case["customer_a"], case["customer_b"]):
        dup = conn.execute(
            "SELECT 1 FROM merge_candidate WHERE customer_a = %s AND customer_b = %s AND case_id <> %s",
            (lo, hi, case["case_id"]),
        ).fetchone()
        if dup:
            conn.execute("UPDATE merge_candidate SET status = 'obsolete', claimed_by = NULL WHERE case_id = %s",
                         (case["case_id"],))
            return "obsolete"
        conn.execute("UPDATE merge_candidate SET customer_a = %s, customer_b = %s WHERE case_id = %s",
                     (lo, hi, case["case_id"]))
    return "ok"


def take_next(conn: psycopg.Connection, user: User) -> int | None:
    """Return the case this reviewer holds, or claim the oldest unclaimed one."""
    with conn.transaction():
        held = conn.execute(
            "SELECT case_id FROM merge_candidate WHERE status = 'open' AND claimed_by = %s"
            " AND claimed_at > now() - make_interval(mins => %s) ORDER BY claimed_at LIMIT 1",
            (user.user_id, CLAIM_MINUTES),
        ).fetchone()
        if held:
            return held["case_id"]
        for _ in range(50):  # skip cases made obsolete by earlier merges
            case = conn.execute(
                "SELECT case_id, customer_a, customer_b FROM merge_candidate"
                " WHERE status = 'open' AND (claimed_by IS NULL OR claimed_at <= now() - make_interval(mins => %s))"
                " ORDER BY created_at, case_id LIMIT 1 FOR UPDATE SKIP LOCKED",
                (CLAIM_MINUTES,),
            ).fetchone()
            if case is None:
                return None
            if _reconcile(conn, case) == "obsolete":
                continue
            conn.execute("UPDATE merge_candidate SET claimed_by = %s, claimed_at = now() WHERE case_id = %s",
                         (user.user_id, case["case_id"]))
            return case["case_id"]
    return None


def release(conn: psycopg.Connection, user: User, case_id: int) -> None:
    with conn.transaction():
        n = conn.execute(
            "UPDATE merge_candidate SET claimed_by = NULL, claimed_at = NULL"
            " WHERE case_id = %s AND status = 'open' AND claimed_by = %s",
            (case_id, user.user_id),
        ).rowcount
    if not n:
        raise CaseError(409, "This case isn't held by you.")


# ------------------------------------------------------------------ detail


def _customer(conn: psycopg.Connection, cid: UUID) -> dict:
    cust = conn.execute("SELECT customer_id, created_at FROM customer WHERE customer_id = %s", (cid,)).fetchone()
    recs = conn.execute(
        "SELECT record_id, source_system, source_record_id, received_at, cleansed FROM source_record"
        " WHERE customer_id = %s ORDER BY record_id", (cid,),
    ).fetchall()
    records = []
    for r in recs:
        c = dict(r["cleansed"])
        c["_source"] = f'{r["source_system"]} {r["source_record_id"]}'
        records.append(c)
    return {
        "customer_id": str(cid),
        "created_at": cust["created_at"].isoformat() if cust else None,
        "records": [{"record_id": r["record_id"], "source_system": r["source_system"],
                     "source_record_id": r["source_record_id"], "received_at": r["received_at"].isoformat(),
                     "cleansed": r["cleansed"]} for r in recs],
        "_cleansed": records,
    }


def get_case(conn: psycopg.Connection, user: User, case_id: int) -> dict[str, Any]:
    case = conn.execute(
        "SELECT m.*, cu.display_name AS claimed_by_name, du.display_name AS decided_by_name,"
        " (m.claimed_at + make_interval(mins => %s)) AS claim_expires_at"
        " FROM merge_candidate m"
        " LEFT JOIN review_user cu ON cu.user_id = m.claimed_by"
        " LEFT JOIN review_user du ON du.user_id = m.decided_by"
        " WHERE m.case_id = %s", (CLAIM_MINUTES, case_id),
    ).fetchone()
    if case is None:
        conn.rollback()
        raise CaseError(404, "Case not found.")
    a, b = _customer(conn, case["customer_a"]), _customer(conn, case["customer_b"])
    # Older customer first: it is the default survivor.
    if (a["created_at"] or "") > (b["created_at"] or ""):
        a, b = b, a
    trigger = None
    if case["record_id"]:
        t = conn.execute(
            "SELECT record_id, source_system, source_record_id, customer_id, received_at FROM source_record"
            " WHERE record_id = %s", (case["record_id"],),
        ).fetchone()
        if t:
            trigger = {"record_id": t["record_id"], "source": f'{t["source_system"]} {t["source_record_id"]}',
                       "customer_id": str(t["customer_id"]), "received_at": t["received_at"].isoformat()}
    conn.rollback()

    rows = compare(a.pop("_cleansed"), b.pop("_cleansed"))
    rule = RULES_BY_KEY.get(case["rule"])
    held_by_me = case["status"] == "open" and case["claimed_by"] == user.user_id
    return {
        "case_id": case["case_id"],
        "status": case["status"],
        "created_at": case["created_at"].isoformat(),
        "rule": case["rule"],
        "rule_description": MATCH_KEYS[case["rule"]].description if case["rule"] in MATCH_KEYS else case["rule"],
        "tier": rule.tier if rule else None,
        "trigger": trigger,
        "held_by_me": held_by_me,
        "claimed_by": case["claimed_by_name"],
        "claim_expires_at": case["claim_expires_at"].isoformat() if case["claim_expires_at"] else None,
        "decision": case["decision"],
        "decided_by": case["decided_by_name"],
        "decided_at": case["decided_at"].isoformat() if case["decided_at"] else None,
        "notes": case["notes"],
        "survivor_customer_id": str(case["survivor_customer_id"]) if case["survivor_customer_id"] else None,
        "customer_a": a,
        "customer_b": b,
        "comparison": rows,
        "summary": summarise(rows),
    }


# ------------------------------------------------------------------ decision


def decide(conn: psycopg.Connection, settings: Settings, user: User, case_id: int, *, decision: str,
           notes: str, survivor_customer_id: UUID | None) -> dict[str, Any]:
    notes = (notes or "").strip()
    if decision not in ("merge", "keep_apart"):
        raise CaseError(422, "Choose merge or keep apart.")
    if len(notes) < MIN_NOTES:
        raise CaseError(422, "Add a note explaining the decision.")

    with conn.transaction():
        case = conn.execute(
            "SELECT * FROM merge_candidate WHERE case_id = %s FOR UPDATE", (case_id,)
        ).fetchone()
        if case is None:
            raise CaseError(404, "Case not found.")
        if case["status"] != "open":
            raise CaseError(409, "This case has already been decided.")
        if case["claimed_by"] != user.user_id:
            raise CaseError(409, "Take this case before deciding it.")
        if _reconcile(conn, case) == "obsolete":
            raise CaseError(409, "These customers have already been merged by another decision.")
        case = conn.execute("SELECT * FROM merge_candidate WHERE case_id = %s", (case_id,)).fetchone()
        pair = {case["customer_a"], case["customer_b"]}

        survivor = merged = None
        moved = 0
        if decision == "merge":
            if survivor_customer_id not in pair:
                raise CaseError(422, "Choose which customer to keep.")
            survivor = survivor_customer_id
            merged = next(c for c in pair if c != survivor)
            try:
                res = ResolutionService(settings).merge(
                    conn, MergeRequest(survivor_customer_id=survivor, merged_customer_id=merged,
                                       reason=f"Review case {case_id}: {notes}"))
            except MergeError as exc:
                raise CaseError(409, str(exc)) from exc
            moved = res.source_records_moved
            status = "merged"
        else:
            status = "rejected"

        conn.execute(
            "UPDATE merge_candidate SET status = %s, decision = %s, notes = %s, decided_by = %s,"
            " decided_at = now(), survivor_customer_id = %s, claimed_by = NULL WHERE case_id = %s",
            (status, decision, notes, user.user_id, survivor, case_id),
        )
        Repository(conn).log_event(
            record_id=None, customer_id=survivor or case["customer_a"],
            outcome="review_merge" if decision == "merge" else "review_keep_apart", rule=case["rule"],
            details={"case_id": case_id, "reviewer": user.email, "notes": notes,
                     "customer_a": str(case["customer_a"]), "customer_b": str(case["customer_b"]),
                     "merged_customer_id": str(merged) if merged else None, "records_moved": moved},
        )
        # Other open cases that named the merged customer now refer to the survivor.
        if merged is not None:
            others = conn.execute(
                "SELECT case_id, customer_a, customer_b FROM merge_candidate"
                " WHERE status = 'open' AND (customer_a = %s OR customer_b = %s)", (merged, merged),
            ).fetchall()
            for o in others:
                _reconcile(conn, o)

    return {"case_id": case_id, "status": status, "decision": decision,
            "survivor_customer_id": str(survivor) if survivor else None,
            "merged_customer_id": str(merged) if merged else None, "source_records_moved": moved}


def history(conn: psycopg.Connection, user: User, everyone: bool = False, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT m.case_id, m.rule, m.decision, m.notes, m.decided_at, m.survivor_customer_id,"
        " m.customer_a, m.customer_b, u.display_name AS decided_by"
        " FROM merge_candidate m JOIN review_user u ON u.user_id = m.decided_by"
        " WHERE m.decided_by IS NOT NULL AND (%s OR m.decided_by = %s)"
        " ORDER BY m.decided_at DESC LIMIT %s",
        (everyone, user.user_id, limit),
    ).fetchall()
    conn.rollback()
    return [{**r, "decided_at": r["decided_at"].isoformat(),
             "survivor_customer_id": str(r["survivor_customer_id"]) if r["survivor_customer_id"] else None,
             "customer_a": str(r["customer_a"]), "customer_b": str(r["customer_b"])} for r in rows]


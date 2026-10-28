"""PostgreSQL data access. All SQL lives here."""

from __future__ import annotations

import uuid
from typing import Any, Iterable
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.matching.keys import KeySet


def _pairs(keys: KeySet) -> tuple[list[str], list[bytes]]:
    types: list[str] = []
    values: list[bytes] = []
    for t, vals in keys.items():
        for v in vals:
            types.append(t)
            values.append(v)
    return types, values


def lock_id(material: bytes) -> int:
    return int.from_bytes(material[:8], "big", signed=True)


class Repository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------ locking
    def advisory_locks(self, ids: Iterable[int]) -> None:
        """Transaction-scoped locks, always taken in sorted order to avoid deadlocks."""
        for i in sorted(set(ids)):
            self.conn.execute("SELECT pg_advisory_xact_lock(%s)", (i,))

    # ------------------------------------------------------------------ source records
    def find_source_record(self, source_system: str, source_record_id: str) -> dict | None:
        return self.conn.execute(
            "SELECT sr.record_id, sr.customer_id FROM source_record sr"
            " WHERE sr.source_system = %s AND sr.source_record_id = %s",
            (source_system, source_record_id),
        ).fetchone()

    def insert_source_record(self, *, source_system: str, source_record_id: str, customer_id: UUID,
                             raw: dict, cleansed: dict, profile: dict, components: dict, issues: list[dict]) -> int:
        row = self.conn.execute(
            "INSERT INTO source_record (source_system, source_record_id, customer_id, raw, cleansed,"
            " match_profile, match_components, data_quality) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING record_id",
            (source_system, source_record_id, customer_id, Jsonb(raw), Jsonb(cleansed), Jsonb(profile),
             Jsonb(components), Jsonb(issues)),
        ).fetchone()
        return row["record_id"]

    def update_source_record(self, record_id: int, *, raw: dict, cleansed: dict, profile: dict,
                             components: dict, issues: list[dict]) -> None:
        self.conn.execute(
            "UPDATE source_record SET raw = %s, cleansed = %s, match_profile = %s, match_components = %s,"
            " data_quality = %s, updated_at = now() WHERE record_id = %s",
            (Jsonb(raw), Jsonb(cleansed), Jsonb(profile), Jsonb(components), Jsonb(issues), record_id),
        )
        self.conn.execute("DELETE FROM blocking_key WHERE record_id = %s", (record_id,))

    def get_source_lookup(self, source_system: str, source_record_id: str) -> dict | None:
        row = self.find_source_record(source_system, source_record_id)
        if not row:
            return None
        row["customer_id"] = self.survivor(row["customer_id"])
        return row

    # ------------------------------------------------------------------ keys
    def insert_blocking_keys(self, record_id: int, blocking_keys: KeySet) -> None:
        types, values = _pairs(blocking_keys)
        if not types:
            return
        self.conn.execute(
            "INSERT INTO blocking_key (record_id, key_type, key_value)"
            " SELECT %s, t, v FROM unnest(%s::text[], %s::bytea[]) AS u(t, v)"
            " ON CONFLICT DO NOTHING",
            (record_id, types, values),
        )

    def block_sizes(self, blocking_keys: KeySet) -> dict[tuple[str, bytes], int]:
        types, values = _pairs(blocking_keys)
        if not types:
            return {}
        rows = self.conn.execute(
            "SELECT b.key_type, b.key_value, count(*) AS n FROM blocking_key b"
            " JOIN unnest(%s::text[], %s::bytea[]) AS q(t, v) ON b.key_type = q.t AND b.key_value = q.v"
            " GROUP BY b.key_type, b.key_value",
            (types, values),
        ).fetchall()
        return {(r["key_type"], bytes(r["key_value"])): r["n"] for r in rows}

    def candidate_pool(self, blocking_keys: KeySet, limit: int, exclude_record_id: int | None = None) -> list[int]:
        types, values = _pairs(blocking_keys)
        if not types:
            return []
        rows = self.conn.execute(
            "SELECT DISTINCT b.record_id FROM blocking_key b"
            " JOIN unnest(%s::text[], %s::bytea[]) AS q(t, v) ON b.key_type = q.t AND b.key_value = q.v"
            " WHERE b.record_id IS DISTINCT FROM %s"
            " ORDER BY b.record_id LIMIT %s",
            (types, values, exclude_record_id, limit),
        ).fetchall()
        return [r["record_id"] for r in rows]

    def load_candidates(self, record_ids: list[int]) -> list[dict]:
        """Pooled records with their surviving customer and match-key components.
        cleansed is returned only for rows written before components were stored."""
        if not record_ids:
            return []
        return self.conn.execute(
            "WITH RECURSIVE chain AS ("
            "  SELECT sr.record_id, c.customer_id, c.merged_into, c.created_at, 0 AS depth"
            "  FROM source_record sr JOIN customer c ON c.customer_id = sr.customer_id"
            "  WHERE sr.record_id = ANY(%s)"
            "  UNION ALL"
            "  SELECT ch.record_id, c.customer_id, c.merged_into, c.created_at, ch.depth + 1"
            "  FROM chain ch JOIN customer c ON c.customer_id = ch.merged_into"
            "  WHERE ch.depth < 20"
            "), live AS ("
            "  SELECT DISTINCT ON (record_id) record_id, customer_id, created_at"
            "  FROM chain WHERE merged_into IS NULL ORDER BY record_id, depth DESC"
            ") SELECT l.record_id, l.customer_id, l.created_at, sr.match_profile, sr.match_components,"
            "  CASE WHEN sr.match_components IS NULL THEN sr.cleansed END AS cleansed"
            "  FROM live l JOIN source_record sr ON sr.record_id = l.record_id ORDER BY l.record_id",
            (record_ids,),
        ).fetchall()

    # ------------------------------------------------------------------ customers
    def create_customer(self) -> UUID:
        cid = uuid.uuid4()
        self.conn.execute("INSERT INTO customer (customer_id) VALUES (%s)", (cid,))
        return cid

    def touch_customer(self, customer_id: UUID) -> None:
        self.conn.execute("UPDATE customer SET updated_at = now() WHERE customer_id = %s", (customer_id,))

    def survivor(self, customer_id: UUID) -> UUID:
        """Follow merged_into links to the surviving customer."""
        current = customer_id
        for _ in range(20):
            row = self.conn.execute(
                "SELECT merged_into FROM customer WHERE customer_id = %s", (current,)
            ).fetchone()
            if not row or row["merged_into"] is None:
                return current
            current = row["merged_into"]
        return current

    def get_customer(self, customer_id: UUID) -> dict | None:
        return self.conn.execute(
            "SELECT customer_id, status, merged_into, created_at FROM customer WHERE customer_id = %s",
            (customer_id,),
        ).fetchone()

    def customers_by_blocking_key(self, key_type: str, key_value: bytes, limit: int) -> list[UUID]:
        """Customers holding a record with this blocking key, oldest record first."""
        rows = self.conn.execute(
            "SELECT sr.customer_id FROM blocking_key b JOIN source_record sr ON sr.record_id = b.record_id"
            " WHERE b.key_type = %s AND b.key_value = %s"
            " GROUP BY sr.customer_id ORDER BY min(sr.record_id) LIMIT %s",
            (key_type, key_value, limit),
        ).fetchall()
        return [r["customer_id"] for r in rows]

    def record_count(self, customer_id: UUID) -> int:
        return self.conn.execute(
            "SELECT count(*) AS n FROM source_record WHERE customer_id = %s", (customer_id,)
        ).fetchone()["n"]

    def pair_rejected(self, a: UUID, b: UUID | None) -> bool:
        """A reviewer decided these two customers are different people."""
        if b is None:
            return False
        a, b = sorted([a, b], key=str)
        return self.conn.execute(
            "SELECT 1 FROM merge_candidate WHERE customer_a = %s AND customer_b = %s AND status = 'rejected'",
            (a, b),
        ).fetchone() is not None

    def customer_records(self, customer_id: UUID) -> list[dict]:
        return self.conn.execute(
            "SELECT record_id, source_system, source_record_id, received_at, updated_at, cleansed"
            " FROM source_record WHERE customer_id = %s ORDER BY record_id",
            (customer_id,),
        ).fetchall()

    def merge_customers(self, survivor: UUID, merged: UUID) -> int:
        moved = self.conn.execute(
            "UPDATE source_record SET customer_id = %s, updated_at = now() WHERE customer_id = %s",
            (survivor, merged),
        ).rowcount
        self.conn.execute(
            "UPDATE customer SET status = 'merged', merged_into = %s, updated_at = now() WHERE customer_id = %s",
            (survivor, merged),
        )
        # Flatten any earlier merges into the now-merged customer.
        self.conn.execute(
            "UPDATE customer SET merged_into = %s, updated_at = now() WHERE merged_into = %s AND customer_id <> %s",
            (survivor, merged, survivor),
        )
        a, b = sorted([survivor, merged], key=str)
        self.conn.execute(
            "UPDATE merge_candidate SET status = 'merged' WHERE customer_a = %s AND customer_b = %s",
            (a, b),
        )
        self.touch_customer(survivor)
        return moved

    # ------------------------------------------------------------------ audit
    def add_merge_candidates(self, winner: UUID, others: list[UUID], rule: str, record_id: int) -> None:
        for other in others:
            a, b = sorted([winner, other], key=str)
            self.conn.execute(
                "INSERT INTO merge_candidate (customer_a, customer_b, rule, record_id)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (customer_a, customer_b) DO NOTHING",
                (a, b, rule, record_id),
            )

    def log_event(self, *, record_id: int | None, customer_id: UUID | None, outcome: str,
                  rule: str | None, details: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO resolution_event (record_id, customer_id, outcome, rule, details)"
            " VALUES (%s, %s, %s, %s, %s)",
            (record_id, customer_id, outcome, rule, Jsonb(details)),
        )

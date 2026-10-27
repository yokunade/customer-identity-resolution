"""Load demo data: synthetic UK customers resolved through the real pipeline,
plus two reviewer accounts. Safe to run repeatedly: it skips loading if the
database already holds records.

    python -m benchmark.seed_demo            # uses DATABASE_URL, KEY_SECRET, AUTO_LINK_TIERS

Environment
    DEMO_RECORDS    number of records to load (default 20000)
    DEMO_PASSWORD   password for the demo reviewers (default review-demo-2026)
"""

from __future__ import annotations

import os
import time

import psycopg
from psycopg.rows import dict_row

from app.config import Settings
from app.db import migrate
from app.review.auth import create_user
from app.schemas import InboundCustomer
from app.service import ResolutionService
from benchmark.generate import generate

REVIEWERS = [
    ("ana@demo.local", "Ana Silva", "admin"),
    ("tom@demo.local", "Tom Reed", "reviewer"),
]


def main() -> None:
    settings = Settings.from_env()
    n_records = int(os.getenv("DEMO_RECORDS", "20000"))
    password = os.getenv("DEMO_PASSWORD", "review-demo-2026")

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        migrate(conn)
        for email, name, role in REVIEWERS:
            exists = conn.execute("SELECT 1 FROM review_user WHERE email = %s", (email,)).fetchone()
            conn.rollback()
            if not exists:
                create_user(conn, email, name, password, role)
        existing = conn.execute("SELECT count(*) AS n FROM source_record").fetchone()["n"]
        conn.rollback()
        if existing:
            print(f"Demo data already loaded ({existing:,} records). Skipping.", flush=True)
            summary(conn, password)
            return

        print(f"Generating {n_records:,} synthetic customer records...", flush=True)
        rows = generate(n_records, seed=7)
        svc = ResolutionService(settings)
        order = ["exact", "strong", "probable", "possible"]
        tiers = ", ".join(t for t in order if t in settings.auto_link_tiers)
        print(f"Resolving them through the matching pipeline (auto-linking: {tiers}; the rest go to review)...",
              flush=True)
        t0 = time.time()
        for i, row in enumerate(rows, 1):
            svc.resolve(conn, InboundCustomer(**row["record"]))
            if i % 2000 == 0 or i == len(rows):
                rate = i / (time.time() - t0)
                print(f"  {i:>7,} / {len(rows):,}  ({rate:,.0f} records/s)", flush=True)
        summary(conn, password)


def summary(conn: psycopg.Connection, password: str) -> None:
    s = conn.execute(
        "SELECT (SELECT count(*) FROM source_record) AS records,"
        " (SELECT count(*) FROM customer WHERE status = 'active') AS customers,"
        " (SELECT count(*) FROM merge_candidate WHERE status = 'open') AS cases"
    ).fetchone()
    conn.rollback()
    port = os.getenv("API_PORT", "8000")
    print(f"""
Demo ready.
  {s['records']:,} source records resolved to {s['customers']:,} customers; {s['cases']:,} cases waiting for review.

  Review app   http://localhost:{port}/review
               {REVIEWERS[0][0]} (admin) or {REVIEWERS[1][0]}, password: {password}
  API docs     http://localhost:{port}/docs   (X-API-Key: demo-key)
""", flush=True)


if __name__ == "__main__":
    main()

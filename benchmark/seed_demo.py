"""Load demo data: curated review cases, then bulk synthetic UK customers, all
resolved through the real pipeline, plus two reviewer accounts. Every open
case is labelled with its true answer. Safe to run repeatedly: it skips
loading if the database already holds records.

Curated cases (benchmark/demo_scenarios.py) are loaded first, so they are at
the front of the review queue.

    python -m benchmark.seed_demo            # uses DATABASE_URL, KEY_SECRET, AUTO_LINK_TIERS

Environment
    DEMO_RECORDS    number of records to load (default 20000)
    DEMO_PASSWORD   password for the demo reviewers (default review-demo-2026)
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

import psycopg
from psycopg.rows import dict_row

from app import engine_config
from app.config import Settings
from app.db import migrate
from app.review import tuning
from app.review.auth import create_user
from app.review.cases import build_case, record_example, snapshot_of
from app.schemas import InboundCustomer
from app.service import ResolutionService
from benchmark.demo_scenarios import SCENARIOS, queue_order
from benchmark.demo_scenarios import person_map as scenario_people
from benchmark.generate import generate

REVIEWERS = [
    ("ana@demo.local", "Ana Silva", "admin"),
    ("mo@demo.local", "Mo Patel", "manager"),
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
            label_cases(conn, generate(n_records, seed=7))
            summary(conn, password)
            return

        order = ["exact", "strong", "probable", "possible"]
        tiers = ", ".join(t for t in order if t in settings.auto_link_tiers)
        print(f"Auto-linking {tiers}; other matches go to review.", flush=True)
        started = time.time()
        load(conn, ResolutionService(settings), n_records, _print_progress)
        tuning.record_run(conn, engine_config.current(settings), "Initial seed", started)
        summary(conn, password)


def _print_progress(done: int, total: int) -> None:
    if done == 0:
        print(f"Resolving {total:,} records through the matching pipeline...", flush=True)
    elif done % 2000 == 0 or done == total:
        print(f"  {done:>7,} / {total:,}", flush=True)


def load(conn: psycopg.Connection, svc: ResolutionService, n_records: int,
         progress: Callable[[int, int], None] | None = None) -> None:
    """Resolve the curated scenarios, then n_records generated ones (always the
    same records: seed 7), store who each record really is and label the open
    cases with the true answer. progress(done, total) is called as it goes."""
    rows = generate(n_records, seed=7)
    curated = [record for sc in queue_order() for _, record in sc.records]
    total = len(curated) + len(rows)
    if progress:
        progress(0, total)
    for i, record in enumerate([*curated, *(r["record"] for r in rows)], 1):
        svc.resolve(conn, InboundCustomer(**record))
        if progress and (i % 250 == 0 or i == total):
            progress(i, total)
    person = {(r["record"]["source_system"].upper(), r["record"]["source_record_id"]): str(r["truth"]["person_id"])
              for r in rows}
    person.update(scenario_people())
    with conn.transaction(), conn.cursor().copy(
            "COPY demo_truth (source_system, source_record_id, person) FROM STDIN") as copy:
        for (system, rid), p in person.items():
            copy.write_row((system, rid, p))
    label_cases(conn, rows)


def label_cases(conn: psycopg.Connection, rows: list[dict]) -> None:
    """Record the true answer for every open case as a 'synthetic_truth' example.

    The generator knows which real person each record belongs to, so these
    labels are exact. They form a ready-made evaluation set for an AI reviewer
    and are kept separate from human decisions (label_source)."""
    done = conn.execute("SELECT count(*) AS n FROM review_example WHERE label_source = 'synthetic_truth'").fetchone()["n"]
    conn.rollback()
    if done:
        return
    person: dict = {(r["record"]["source_system"].upper(), r["record"]["source_record_id"]): r["truth"]["person_id"]
                    for r in rows}
    person.update(scenario_people())
    scenario_of = {(r["source_system"].upper(), r["source_record_id"]): sc for sc in SCENARIOS for _, r in sc.records}
    cases = conn.execute("SELECT case_id FROM merge_candidate WHERE status = 'open' ORDER BY case_id").fetchall()
    conn.rollback()
    labelled = {"merge": 0, "keep_apart": 0, "skipped": 0, "curated": 0}
    with conn.transaction():
        for row in cases:
            case = build_case(conn, None, row["case_id"])
            people = []
            for side in ("customer_a", "customer_b"):
                people.append({person.get((r["source_system"], r["source_record_id"])) for r in case[side]["records"]})
            a, b = people
            if None in a | b or len(a) != 1 or len(b) != 1:
                labelled["skipped"] += 1  # a customer already mixes people; no single right answer
                continue
            decision = "merge" if a == b else "keep_apart"
            keys = [(r["source_system"], r["source_record_id"]) for side in ("customer_a", "customer_b")
                    for r in case[side]["records"]]
            sc = next((scenario_of[k] for k in keys if k in scenario_of), None)
            meta = ({"scenario": sc.key, "title": sc.title, "explanation": sc.explanation, "curated": True}
                    if sc else {"generated": True})
            record_example(conn, case_id=row["case_id"], snapshot=snapshot_of(case), label_source="synthetic_truth",
                           decision=decision, survivor="A" if decision == "merge" else None, metadata=meta)
            labelled[decision] += 1
            labelled["curated"] += bool(sc)
    print(f"Labelled {labelled['merge'] + labelled['keep_apart']:,} cases with synthetic ground truth "
          f"({labelled['merge']:,} same person, {labelled['keep_apart']:,} different people; "
          f"{labelled['skipped']:,} skipped), including {labelled['curated']} curated cases.", flush=True)


def summary(conn: psycopg.Connection, password: str) -> None:
    s = conn.execute(
        "SELECT (SELECT count(*) FROM source_record) AS records,"
        " (SELECT count(*) FROM customer WHERE status = 'active') AS customers,"
        " (SELECT count(*) FROM merge_candidate WHERE status = 'open') AS cases,"
        " (SELECT count(*) FROM review_example WHERE label_source = 'synthetic_truth') AS gold,"
        " (SELECT count(*) FROM review_example WHERE metadata ? 'scenario') AS curated"
    ).fetchone()
    conn.rollback()
    port = os.getenv("API_PORT", "8000")
    print(f"""
Demo ready.
  {s['records']:,} source records resolved to {s['customers']:,} customers; {s['cases']:,} cases waiting for review.
  The first {s['curated']} cases in the queue are curated hard cases; every case has a known answer.

  custIDR      http://localhost:{port}/app
               {REVIEWERS[0][0]} (admin), {REVIEWERS[1][0]} (manager) or {REVIEWERS[2][0]}, password: {password}
  API docs     http://localhost:{port}/docs   (X-API-Key: demo-key)
""", flush=True)


if __name__ == "__main__":
    main()

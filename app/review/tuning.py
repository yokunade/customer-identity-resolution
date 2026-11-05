"""Demo tuning (admins, DEMO_MODE only): change the engine settings, re-run the
same demo records through them and score the result against the known answer.

A run wipes the demo's customers, records, cases and labels, resolves the
curated scenarios and the generated records again (benchmark.seed_demo.load),
then scores the final state:

pairwise   precision and recall over record pairs (same customer vs same person)
people     correct: all their records in one customer, alone; split: across
           customers; mixed: sharing a customer with someone else
cases      open cases whose two customers are the same person (review the
           engine could have avoided) or different people (review needed)
rules      links made by each rule and how many joined the wrong person,
           cases raised by each rule and how many were the same person

Each run stores every person's state, so a run is compared person by person
with the one before it.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter, defaultdict
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import engine_config
from app.review.auth import User
from app.service import ResolutionService

log = logging.getLogger(__name__)

EXAMPLES = 50
# The demo data; reviewers, sessions, the lookup log and the runs stay.
WIPE = ("customer, source_record, blocking_key, resolution_event, merge_candidate, review_example, manual_record,"
        " demo_truth")


class TuningError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _c2(n: int) -> int:
    return n * (n - 1) // 2


def score(conn: psycopg.Connection) -> tuple[dict, dict]:
    """Scores for the data as it stands, and each person's state."""
    rows = conn.execute(
        "SELECT r.record_id, r.source_system, r.source_record_id, r.customer_id::text AS cid, t.person"
        " FROM source_record r JOIN demo_truth t USING (source_system, source_record_id) ORDER BY r.record_id"
    ).fetchall()
    person_of = {r["record_id"]: r["person"] for r in rows}
    by_cust: dict[str, set[str]] = defaultdict(set)
    by_person: dict[str, set[str]] = defaultdict(set)
    cell: Counter = Counter()
    first: dict[str, list[str]] = {}
    for r in rows:
        by_cust[r["cid"]].add(r["person"])
        by_person[r["person"]].add(r["cid"])
        cell[(r["cid"], r["person"])] += 1
        first.setdefault(r["person"], [r["source_system"], r["source_record_id"]])
    cust_size = Counter(r["cid"] for r in rows)
    person_size = Counter(r["person"] for r in rows)
    tp = sum(_c2(n) for n in cell.values())
    pred, true = sum(_c2(n) for n in cust_size.values()), sum(_c2(n) for n in person_size.values())
    precision, recall = (tp / pred if pred else 1.0), (tp / true if true else 1.0)

    cases = conn.execute(
        "SELECT customer_a::text AS a, customer_b::text AS b, rule FROM merge_candidate WHERE status = 'open'"
    ).fetchall()
    in_review = {c for row in cases for c in (row["a"], row["b"])}
    case_kind: Counter = Counter()
    rules: dict[str, Counter] = defaultdict(Counter)
    for row in cases:
        pa, pb = by_cust.get(row["a"], set()), by_cust.get(row["b"], set())
        kind = ("same" if len(pa) == 1 and pa == pb else "different" if pa and pb and not pa & pb else "mixed")
        case_kind[kind] += 1
        rules[row["rule"]]["cases"] += 1
        rules[row["rule"]]["cases_same"] += kind == "same"

    for e in conn.execute(
        "SELECT record_id, customer_id::text AS cid, rule, details->'evaluations' AS ev FROM resolution_event"
        " WHERE outcome = 'matched' AND rule IS NOT NULL"
    ).fetchall():
        me = person_of.get(e["record_id"])
        if me is None:
            continue
        matched = {person_of.get(x["record_id"]) for x in e["ev"] or []
                   if x["customer_id"] == e["cid"] and x["rule"] == e["rule"]}
        rules[e["rule"]]["links"] += 1
        rules[e["rule"]]["wrong"] += bool(matched) and matched != {me}
    auto_merges = conn.execute(
        "SELECT count(*) AS n FROM resolution_event WHERE outcome = 'merge' AND details->>'auto' = 'true'"
    ).fetchone()["n"]

    people: dict[str, dict] = {}
    for p, cs in by_person.items():
        state = ("mixed" if any(len(by_cust[c]) > 1 for c in cs) else "split" if len(cs) > 1 else "correct")
        people[p] = {"state": state, "review": bool(cs & in_review), "record": first[p]}
    states = Counter(v["state"] for v in people.values())
    scores = {
        "records": len(rows), "people": len(by_person), "customers": len(by_cust),
        "precision": round(precision, 5), "recall": round(recall, 5),
        "f1": round(2 * precision * recall / (precision + recall), 5) if precision + recall else 0.0,
        "people_correct": states["correct"], "people_split": states["split"], "people_mixed": states["mixed"],
        "customers_overmerged": sum(1 for ps in by_cust.values() if len(ps) > 1),
        "open_cases": len(cases), "cases_same": case_kind["same"], "cases_different": case_kind["different"],
        "cases_mixed": case_kind["mixed"], "auto_merges": auto_merges,
        "links": sum(c["links"] for c in rules.values()), "wrong_links": sum(c["wrong"] for c in rules.values()),
        "rules": {k: dict(v) for k, v in sorted(rules.items())},
    }
    return scores, people


def record_run(conn: psycopg.Connection, config: dict, note: str, started: float) -> int:
    """Store a finished run of the data as it stands (the initial seed)."""
    scores, people = score(conn)
    conn.rollback()  # end the reads, so the insert below commits on its own
    scores["seconds"] = round(time.time() - started)
    with conn.transaction():
        return conn.execute(
            "INSERT INTO tuning_run (finished_at, status, note, config, scores, people)"
            " VALUES (now(), 'done', %s, %s, %s, %s) RETURNING run_id",
            (note, Jsonb(config), Jsonb(scores), Jsonb(people)),
        ).fetchone()["run_id"]


def on_start(conn: psycopg.Connection, settings: Any) -> Any:
    """At app start: a run still marked running was cut off by the restart, and
    the settings in force are those of the last finished run, which built the
    data."""
    with conn.transaction():
        conn.execute("UPDATE tuning_run SET status = 'failed', finished_at = now(),"
                     " error = 'Interrupted: the app restarted during the run.' WHERE status = 'running'")
        last = conn.execute("SELECT config FROM tuning_run WHERE status = 'done' ORDER BY run_id DESC LIMIT 1"
                            ).fetchone()
    if last is None:
        return settings
    try:
        return engine_config.apply(settings, last["config"])
    except ValueError:
        log.warning("the last tuning run's settings no longer apply; using the defaults")
        return settings


def start(app: Any, user: User, config: dict, note: str | None) -> int:
    """Validate config, record the run and start it in the background."""
    try:
        tuned = engine_config.apply(app.state.service.settings, config)
    except ValueError as exc:
        raise TuningError(422, str(exc)) from exc
    config = engine_config.current(tuned)
    try:
        with app.state.db.connection() as conn, conn.transaction():
            run_id = conn.execute(
                "INSERT INTO tuning_run (started_by, note, config) VALUES (%s, %s, %s) RETURNING run_id",
                (user.user_id, note, Jsonb(config)),
            ).fetchone()["run_id"]
    except psycopg.errors.UniqueViolation as exc:
        raise TuningError(409, "A run is already in progress.") from exc
    threading.Thread(target=run_job, args=(app, run_id, tuned), name=f"tuning-{run_id}", daemon=True).start()
    return run_id


def run_job(app: Any, run_id: int, tuned: Any, n_records: int | None = None) -> None:
    """Wipe the demo data, load it again with tuned settings, score it. When it
    is done the app resolves new records with tuned settings too."""
    from benchmark.seed_demo import load  # the demo loader; not part of the service

    n = n_records or int(os.getenv("DEMO_RECORDS", "20000"))
    started = time.time()
    url = tuned.database_url
    with psycopg.connect(url, row_factory=dict_row) as conn, \
            psycopg.connect(url, row_factory=dict_row, autocommit=True) as side:
        def progress(done: int, total: int) -> None:
            side.execute("UPDATE tuning_run SET progress = %s WHERE run_id = %s",
                         (Jsonb({"done": done, "total": total}), run_id))
        try:
            with conn.transaction():
                conn.execute(f"TRUNCATE {WIPE} RESTART IDENTITY CASCADE")
                conn.execute("ALTER SEQUENCE manual_record_seq RESTART")
            svc = ResolutionService(tuned)
            load(conn, svc, n, progress)
            scores, people = score(conn)
            conn.rollback()
            scores["seconds"] = round(time.time() - started)
            side.execute("UPDATE tuning_run SET status = 'done', finished_at = now(), scores = %s, people = %s"
                         " WHERE run_id = %s", (Jsonb(scores), Jsonb(people), run_id))
            app.state.service = svc
        except Exception as exc:  # noqa: BLE001 - recorded on the run for the page to show
            log.exception("tuning run %s failed", run_id)
            side.execute("UPDATE tuning_run SET status = 'failed', finished_at = now(), error = %s"
                         " WHERE run_id = %s", (f"{type(exc).__name__}: {exc}", run_id))


_RUN_COLUMNS = ("r.run_id, r.started_at, r.finished_at, r.status, r.note, r.config, r.progress, r.scores, r.error,"
                " u.display_name AS started_by")


def _summary(r: dict, before: dict | None) -> dict:
    out = {k: r[k] for k in ("run_id", "started_at", "finished_at", "status", "note", "progress", "error",
                             "started_by")}
    out["changes"] = engine_config.changes(before["config"] if before else None, r["config"])
    if r["scores"]:
        out["scores"] = {k: v for k, v in r["scores"].items() if k != "rules"}
    return out


def page(conn: psycopg.Connection, settings: Any) -> dict:
    runs = conn.execute(f"SELECT {_RUN_COLUMNS} FROM tuning_run r LEFT JOIN review_user u ON u.user_id = r.started_by"
                        " ORDER BY r.run_id").fetchall()
    conn.rollback()
    out, before = [], None
    for r in runs:
        out.append(_summary(r, before))
        if r["status"] == "done":
            before = r
    return {"config": engine_config.current(settings), "catalogue": engine_config.describe(),
            "runs": out[::-1], "running": any(r["status"] == "running" for r in runs)}


def _transition(before: dict, after: dict) -> str | None:
    if before["state"] != after["state"]:
        if after["state"] == "correct":
            return "fixed"
        if before["state"] == "correct":
            return "broken"
        return "changed"
    if before["review"] != after["review"]:
        return "left_review" if before["review"] else "entered_review"
    return None


def run_detail(conn: psycopg.Connection, run_id: int) -> dict:
    """A run with per-rule scores, and each person whose result changed since
    the previous finished run."""
    r = conn.execute(f"SELECT {_RUN_COLUMNS}, r.people FROM tuning_run r"
                     " LEFT JOIN review_user u ON u.user_id = r.started_by WHERE r.run_id = %s", (run_id,)).fetchone()
    if r is None:
        conn.rollback()
        raise TuningError(404, "No such run.")
    prev = conn.execute("SELECT run_id, config, scores, people FROM tuning_run WHERE status = 'done' AND run_id < %s"
                        " ORDER BY run_id DESC LIMIT 1", (run_id,)).fetchone()
    latest = conn.execute("SELECT max(run_id) AS id FROM tuning_run WHERE status = 'done'").fetchone()["id"]
    out = _summary(r, prev)
    out["rules"] = (r["scores"] or {}).get("rules", {})
    out["previous"] = None
    if prev and r["people"]:
        out["previous"] = {"run_id": prev["run_id"], "rules": prev["scores"].get("rules", {}),
                           "scores": {k: v for k, v in prev["scores"].items() if k != "rules"}}
        groups: dict[str, list[dict]] = defaultdict(list)
        for p, now in r["people"].items():
            was = prev["people"].get(p)
            kind = was and _transition(was, now)
            if kind:
                groups[kind].append({"person": p, "record": now["record"], "before": was["state"],
                                     "after": now["state"], "review_before": was["review"],
                                     "review_after": now["review"]})
        if run_id == latest:  # the data is this run's: link to the customers
            keys = [e["record"] for g in groups.values() for e in g[:EXAMPLES]]
            where = {tuple(k): None for k in keys}
            if keys:
                for row in conn.execute(
                    "SELECT source_system, source_record_id, customer_id FROM source_record"
                    " WHERE (source_system, source_record_id) IN (SELECT * FROM unnest(%s::text[], %s::text[]))",
                    ([k[0] for k in keys], [k[1] for k in keys]),
                ).fetchall():
                    where[(row["source_system"], row["source_record_id"])] = row["customer_id"]
            for g in groups.values():
                for e in g[:EXAMPLES]:
                    e["customer_id"] = where.get(tuple(e["record"]))
        out["changed"] = {k: {"count": len(v), "examples": v[:EXAMPLES]} for k, v in groups.items()}
    conn.rollback()
    return out


def find(conn: psycopg.Connection, record: str) -> dict:
    """A demo record by source record ID (with or without its system), its
    customer now, and how its person came out in every run."""
    system, _, rid = record.strip().rpartition("/")
    rows = conn.execute(
        "SELECT t.source_system, t.source_record_id, t.person, r.customer_id FROM demo_truth t"
        " LEFT JOIN source_record r USING (source_system, source_record_id)"
        " WHERE t.source_record_id = %s AND (%s = '' OR t.source_system = upper(%s))",
        (rid.strip(), system.strip(), system.strip()),
    ).fetchall()
    if not rows:
        conn.rollback()
        raise TuningError(404, "No demo record with that ID.")
    hit = rows[0]
    history = conn.execute(
        "SELECT run_id, note, finished_at, people -> %s AS person FROM tuning_run"
        " WHERE status = 'done' ORDER BY run_id", (hit["person"],),
    ).fetchall()
    conn.rollback()
    return {"source_system": hit["source_system"], "source_record_id": hit["source_record_id"],
            "customer_id": hit["customer_id"],
            "runs": [{"run_id": h["run_id"], "note": h["note"], "finished_at": h["finished_at"],
                      "state": (h["person"] or {}).get("state"), "review": (h["person"] or {}).get("review")}
                     for h in history]}

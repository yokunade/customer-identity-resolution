"""Load a generated dataset through the resolution service in parallel.

    python -m benchmark.run --data data.jsonl --db postgresql://.../identity_bench --workers 3 --out results/

Records are dealt round-robin to worker processes so arrival order is
preserved approximately, and concurrent workers exercise the locking.
The target database is wiped first.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import time

import psycopg
from psycopg.rows import dict_row

from app.config import Settings
from app.db import migrate
from app.schemas import InboundCustomer
from app.service import ResolutionService


def reset(db: str) -> None:
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    with psycopg.connect(db, row_factory=dict_row) as conn:
        migrate(conn)


def worker(idx: int, n_workers: int, data_path: str, db: str, out_dir: str, secret: str, tiers: str,
           progress, start: int = 0, end: int | None = None, durable: bool = True) -> None:
    """Resolve lines [start, end) of the dataset whose index modulo n_workers is idx."""
    svc = ResolutionService(Settings(database_url=db, key_secret=secret,
                                     auto_link_tiers=frozenset(tiers.split(","))))
    out = open(os.path.join(out_dir, f"results_{idx}.jsonl"), "w")
    with psycopg.connect(db, row_factory=dict_row) as conn:
        if not durable:
            conn.execute("SET synchronous_commit = off")
            conn.commit()
        with open(data_path) as f:
            for i, line in enumerate(f):
                if end is not None and i >= end:
                    break
                if i < start or i % n_workers != idx:
                    continue
                row = json.loads(line)
                t0 = time.perf_counter()
                try:
                    r = svc.resolve(conn, InboundCustomer(**row["record"]))
                    res = {
                        "i": i, "record_id": r.record_id, "customer_id": str(r.customer_id), "outcome": r.outcome,
                        "rule": r.match.rule if r.match else None,
                        "auto_linked": r.match.auto_linked if r.match else None, "tier": r.match.tier if r.match else None,
                        "matched_record_ids": r.match.matched_record_ids if r.match else [],
                        "competing": [str(x) for x in r.match.competing_customer_ids] if r.match else [],
                        "pool": r.candidates_considered, "skipped": r.skipped_blocks,
                        "ms": (time.perf_counter() - t0) * 1000, "match_ms": r.matching_ms, "t": time.time(),
                    }
                except Exception as exc:  # keep going; count errors
                    conn.rollback()
                    res = {"i": i, "error": f"{type(exc).__name__}: {exc}"[:300]}
                out.write(json.dumps(res) + "\n")
                with progress.get_lock():
                    progress.value += 1
    out.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--secret", default="bench-secret")
    ap.add_argument("--auto-link-tiers", default="exact,strong,probable,possible")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    total = sum(1 for _ in open(args.data))
    reset(args.db)

    progress = mp.Value("i", 0)
    t0 = time.time()
    procs = [mp.Process(target=worker, args=(i, args.workers, args.data, args.db, args.out, args.secret,
                                                     args.auto_link_tiers, progress))
             for i in range(args.workers)]
    for p in procs:
        p.start()
    while any(p.is_alive() for p in procs):
        time.sleep(15)
        done = progress.value
        el = time.time() - t0
        print(f"{done:>7}/{total}  {el:6.0f}s  {done / max(el, 1):6.1f} rec/s", flush=True)
    for p in procs:
        p.join()
    el = time.time() - t0
    meta = {"records": total, "workers": args.workers, "auto_link_tiers": args.auto_link_tiers, "elapsed_s": el, "throughput_rps": total / el}
    json.dump(meta, open(os.path.join(args.out, "meta.json"), "w"), indent=1)
    print("done", meta, flush=True)


if __name__ == "__main__":
    main()

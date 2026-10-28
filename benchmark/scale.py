"""Seed-then-measure benchmark: how does resolution perform against a large, existing database?

    python -m benchmark.scale --records 5000000 --seed-records 2500000 \\
        --db postgresql://.../identity_bench --workers 12 --dir /data --report-dir /reports

1. generate  A dense synthetic UK population (benchmark.generate with
             --people-per-postcode and --large-buildings), streamed to disk.
             Reused if the file already exists.
2. seed      Wipe the database and resolve the first --seed-records records
             through the real pipeline, so the database holds realistically
             linked customers. Commits are asynchronous: this is setup.
3. settle    VACUUM ANALYZE and CHECKPOINT, then record table sizes and the
             largest blocking-key blocks.
4. measure   Resolve the remaining records against the seeded database with
             durable commits, timing each one.
5. report    report.json and report.md: throughput and latency by window of
             arrivals, time spent finding candidates, pool sizes, skipped
             blocks, accuracy at arrival, cache hit ratio and sizes.

--phase runs a single step (generate, seed or measure) so a long seed can be
done once and inspected before measuring. measure needs a freshly seeded
database: it adds records, so running it twice measures a different load.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import time
from array import array
from collections import Counter, defaultdict

import psycopg
from psycopg.rows import dict_row

from benchmark import run
from benchmark.generate import generate_to_file

OUTCOMES = {"new_customer": 0, "matched": 1, "existing_source_record": 2, "relinked": 3}


# --------------------------------------------------------------------------- loading


def load(phase: str, args: argparse.Namespace, start: int, end: int, durable: bool) -> dict:
    out = os.path.join(args.dir, phase)
    os.makedirs(out, exist_ok=True)
    progress = mp.Value("l", 0)
    procs = [mp.Process(target=run.worker,
                        args=(i, args.workers, args.data, args.db, out, args.secret, args.auto_link_tiers, progress,
                              start, end, durable))
             for i in range(args.workers)]
    total = end - start
    t0 = time.time()
    for p in procs:
        p.start()
    last, last_t = 0, t0
    while any(p.is_alive() for p in procs):
        time.sleep(1)
        if time.time() - last_t < 30:
            continue
        done, now = progress.value, time.time()
        eta = (total - done) / max((done - last) / (now - last_t), 1e-9)
        print(f"[{phase}] {done:>9,}/{total:,}  {now - t0:7.0f}s  now {(done - last) / (now - last_t):6.0f} rec/s"
              f"  eta {eta / 60:5.0f} min", flush=True)
        last, last_t = done, now
    for p in procs:
        p.join()
    elapsed = time.time() - t0
    meta = {"phase": phase, "records": total, "workers": args.workers, "durable_commits": durable,
            "elapsed_s": round(elapsed, 1), "throughput_rps": round(total / elapsed, 1)}
    json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=1)
    print(f"[{phase}] done: {meta}", flush=True)
    return meta


# --------------------------------------------------------------------------- database facts


def db_facts(db: str, blocks: bool = True) -> dict:
    with psycopg.connect(db, row_factory=dict_row, autocommit=True) as conn:
        facts: dict = {
            "database_size": conn.execute("SELECT pg_size_pretty(pg_database_size(current_database())) AS s").fetchone()["s"],
            "settings": {r["name"]: r["setting"] + (r["unit"] or "") for r in conn.execute(
                "SELECT name, setting, unit FROM pg_settings WHERE name IN ('shared_buffers', 'effective_cache_size',"
                " 'work_mem', 'max_wal_size', 'synchronous_commit', 'random_page_cost')")},
            "rows": {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                     for t in ("customer", "source_record", "blocking_key", "merge_candidate")},
            "tables": [dict(r) for r in conn.execute(
                "SELECT relname AS table, pg_size_pretty(pg_table_size(relid)) AS data,"
                " pg_size_pretty(pg_indexes_size(relid)) AS indexes,"
                " pg_size_pretty(pg_total_relation_size(relid)) AS total"
                " FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 8")],
            "indexes": [dict(r) for r in conn.execute(
                "SELECT indexrelname AS index, pg_size_pretty(pg_relation_size(indexrelid)) AS size"
                " FROM pg_stat_user_indexes ORDER BY pg_relation_size(indexrelid) DESC LIMIT 8")],
        }
        if blocks:
            # How big the groups that one inbound record can pull in have become.
            rows = conn.execute(
                "SELECT key_type, n FROM (SELECT key_type, n, row_number() OVER (PARTITION BY key_type ORDER BY n DESC) rk"
                " FROM (SELECT key_type, key_value, count(*) AS n FROM blocking_key GROUP BY 1, 2) s) t"
                " WHERE rk <= 5 ORDER BY key_type, n DESC").fetchall()
            largest: dict[str, list[int]] = defaultdict(list)
            for r in rows:
                largest[r["key_type"]].append(r["n"])
            facts["largest_blocks"] = dict(largest)
    return facts


def io_counters(db: str) -> dict:
    with psycopg.connect(db, row_factory=dict_row, autocommit=True) as conn:
        d = conn.execute("SELECT blks_hit, blks_read FROM pg_stat_database WHERE datname = current_database()").fetchone()
        idx = {r["indexrelname"]: (r["idx_blks_hit"], r["idx_blks_read"]) for r in conn.execute(
            "SELECT indexrelname, idx_blks_hit, idx_blks_read FROM pg_statio_user_indexes"
            " WHERE indexrelname IN ('ix_blocking_key_lookup', 'blocking_key_pkey', 'source_record_pkey')")}
    return {"blks_hit": d["blks_hit"], "blks_read": d["blks_read"], "indexes": idx}


def hit_ratio(before: dict, after: dict) -> dict:
    def ratio(h0, r0, h1, r1):
        h, r = h1 - h0, r1 - r0
        return round(100.0 * h / (h + r), 2) if h + r else None
    out = {"database": ratio(before["blks_hit"], before["blks_read"], after["blks_hit"], after["blks_read"])}
    for name, (h1, r1) in after["indexes"].items():
        h0, r0 = before["indexes"].get(name, (0, 0))
        out[name] = ratio(h0, r0, h1, r1)
    return out


# --------------------------------------------------------------------------- analysis


def pctl(xs: list[float], *ps: float) -> list[float]:
    if not xs:
        return [0.0 for _ in ps]
    xs = sorted(xs)
    return [round(xs[min(len(xs) - 1, int(p / 100 * len(xs)))], 2) for p in ps]


def analyse(args: argparse.Namespace) -> dict:
    """Stream the results once. Arrival accuracy is judged in record_id order,
    i.e. against whatever had been stored when each record arrived."""
    person = array("l")
    with open(args.data) as f:
        for line in f:
            person.append(json.loads(line)["truth"]["person_id"])

    by_rid: dict[int, tuple[int, int, list[int]]] = {}  # record_id -> (i, outcome, matched record ids)
    windows: dict[int, dict] = defaultdict(lambda: {"ms": [], "match_ms": [], "t": [], "pool": []})
    phase_stats: dict[str, dict] = {}
    for phase in ("seed", "measure"):
        d = os.path.join(args.dir, phase)
        if not os.path.isdir(d):
            continue
        ms, match_ms, pool = [], [], []
        skipped: Counter = Counter()
        outcomes: Counter = Counter()
        tiers: Counter = Counter()
        errors = hit_skip = 0
        for name in os.listdir(d):
            if not name.startswith("results_"):
                continue
            for line in open(os.path.join(d, name)):
                r = json.loads(line)
                if "error" in r:
                    errors += 1
                    continue
                ms.append(r["ms"])
                match_ms.append(r.get("match_ms", 0.0))
                pool.append(r["pool"])
                skipped.update(r["skipped"])
                hit_skip += bool(r["skipped"])
                outcomes[r["outcome"]] += 1
                if r["tier"]:
                    tiers[r["tier"]] += 1
                w = windows[r["i"] // args.window]
                w["ms"].append(r["ms"])
                w["match_ms"].append(r.get("match_ms", 0.0))
                w["t"].append(r["t"])
                w["pool"].append(r["pool"])
                by_rid[r["record_id"]] = (r["i"], OUTCOMES.get(r["outcome"], 9),
                                          r["matched_record_ids"] if r["outcome"] == "matched" else [])
        meta = json.load(open(os.path.join(d, "meta.json"))) if os.path.exists(os.path.join(d, "meta.json")) else {}
        p50, p95, p99, mx = pctl(ms, 50, 95, 99, 100)
        m50, m95, m99 = pctl(match_ms, 50, 95, 99)
        q50, q95, q99, qmx = pctl(pool, 50, 95, 99, 100)
        phase_stats[phase] = {
            **meta, "errors": errors,
            "latency_ms": {"p50": p50, "p95": p95, "p99": p99, "max": mx},
            "finding_candidates_ms": {"p50": m50, "p95": m95, "p99": m99},
            "candidate_pool": {"p50": q50, "p95": q95, "p99": q99, "max": qmx},
            "records_with_skipped_blocks": hit_skip,
            "skipped_blocks_by_key": dict(skipped),
            "outcomes": dict(outcomes),
            "links_by_tier": dict(tiers),
        }

    # Accuracy at arrival, per phase.
    seen = bytearray(max(person) + 1 if person else 0)
    acc = {ph: Counter() for ph in ("seed", "measure")}
    for rid in sorted(by_rid):
        i, outcome, matched = by_rid[rid]
        ph = "seed" if i < args.seed_records else "measure"
        p = person[i]
        if outcome == OUTCOMES["matched"]:
            acc[ph]["links"] += 1
            if all(person[by_rid[m][0]] != p for m in matched if m in by_rid):
                acc[ph]["wrong_links"] += 1
        elif outcome == OUTCOMES["new_customer"]:
            if seen[p]:
                acc[ph]["missed_links"] += 1
            else:
                acc[ph]["first_records"] += 1
        seen[p] = 1
    for ph, c in acc.items():
        if ph in phase_stats:
            links = c["links"]
            phase_stats[ph]["accuracy_at_arrival"] = {
                **dict(c),
                "wrong_link_rate_pct": round(100.0 * c["wrong_links"] / links, 3) if links else None,
                "missed_link_rate_pct": round(100.0 * c["missed_links"] / (links + c["missed_links"]), 3)
                if links + c["missed_links"] else None,
            }

    curve = []
    for k in sorted(windows):
        w = windows[k]
        span = max(w["t"]) - min(w["t"]) if len(w["t"]) > 1 else 0
        p50, p95, p99 = pctl(w["ms"], 50, 95, 99)
        m50, m95 = pctl(w["match_ms"], 50, 95)
        curve.append({"arrivals": f"{k * args.window:,}-{(k + 1) * args.window:,}",
                      "phase": "seed" if k * args.window < args.seed_records else "measure",
                      "rec_per_s": round(len(w["ms"]) / span, 1) if span else None,
                      "p50_ms": p50, "p95_ms": p95, "p99_ms": p99,
                      "find_p50_ms": m50, "find_p95_ms": m95, "pool_p95": pctl(w["pool"], 95)[0]})
    return {"phases": phase_stats, "curve": curve, "people": len(set(person))}


# --------------------------------------------------------------------------- report


def markdown(rep: dict) -> str:
    lines = [f"# Scale benchmark: {rep['records']:,} records, {rep['seed_records']:,} seeded first", ""]
    lines.append(f"{rep['analysis']['people']:,} generated people; {rep['people_per_postcode']} people per postcode;"
                 f" large buildings {'on' if rep['large_buildings'] else 'off'}; {rep['workers']} workers.")
    lines.append("")
    lines.append("| | Seed | Measure |")
    lines.append("|---|---:|---:|")
    ph = rep["analysis"]["phases"]
    s, m = ph.get("seed", {}), ph.get("measure", {})

    def row(label, f):
        lines.append(f"| {label} | {f(s) if s else ''} | {f(m) if m else ''} |")
    row("Records", lambda x: f"{x.get('records', 0):,}")
    row("Commits", lambda x: "durable" if x.get("durable_commits") else "asynchronous")
    row("Throughput (rec/s)", lambda x: x.get("throughput_rps", ""))
    row("Latency p50 / p95 / p99 (ms)", lambda x: "{p50} / {p95} / {p99}".format(**x["latency_ms"]))
    row("Finding candidates p50 / p95 / p99 (ms)", lambda x: "{p50} / {p95} / {p99}".format(**x["finding_candidates_ms"]))
    row("Candidate pool p50 / p95 / max", lambda x: "{p50} / {p95} / {max}".format(**x["candidate_pool"]))
    row("Wrong links at arrival", lambda x: f"{x['accuracy_at_arrival'].get('wrong_link_rate_pct')}%")
    row("Missed links at arrival", lambda x: f"{x['accuracy_at_arrival'].get('missed_link_rate_pct')}%")
    row("Records with a block skipped", lambda x: f"{x['records_with_skipped_blocks']:,}")
    row("Errors", lambda x: x["errors"])
    lines += ["", "## Latency as the database grows", "",
              "| Arrivals | Phase | rec/s | p50 ms | p95 ms | p99 ms | finding p50 / p95 ms | pool p95 |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for c in rep["analysis"]["curve"]:
        lines.append(f"| {c['arrivals']} | {c['phase']} | {c['rec_per_s']} | {c['p50_ms']} | {c['p95_ms']} | {c['p99_ms']}"
                     f" | {c['find_p50_ms']} / {c['find_p95_ms']} | {c['pool_p95']} |")
    if rep.get("cache_hit_pct"):
        lines += ["", "## Cache hit ratio during measure", ""]
        lines += [f"- {k}: {v}%" for k, v in rep["cache_hit_pct"].items()]
    for label, key in (("after seed", "db_after_seed"), ("after measure", "db_after_measure")):
        f = rep.get(key)
        if not f:
            continue
        lines += ["", f"## Database {label}: {f['database_size']}", "",
                  "| Table | Data | Indexes | Total |", "|---|---:|---:|---:|"]
        lines += [f"| {t['table']} | {t['data']} | {t['indexes']} | {t['total']} |" for t in f["tables"]]
        lines += ["", "Rows: " + ", ".join(f"{k} {v:,}" for k, v in f["rows"].items())]
        if f.get("largest_blocks"):
            lines += ["", "Largest blocks (records sharing one blocking key):", ""]
            lines += [f"- {k}: {', '.join(map(str, v))}" for k, v in f["largest_blocks"].items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", type=int, default=5_000_000)
    ap.add_argument("--seed-records", type=int, default=2_500_000)
    ap.add_argument("--db", required=True)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--dir", required=True, help="Working directory for the dataset and per-record results.")
    ap.add_argument("--report-dir", default=None, help="Where report.json and report.md go (default: --dir).")
    ap.add_argument("--data", default=None, help="Dataset path (default: <dir>/scale_<records>.jsonl).")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--people-per-postcode", type=float, default=3.5)
    ap.add_argument("--no-large-buildings", action="store_true")
    ap.add_argument("--secret", default="bench-secret")
    ap.add_argument("--auto-link-tiers", default="exact,strong,probable,possible")
    ap.add_argument("--async-measure", action="store_true", help="Asynchronous commits while measuring too.")
    ap.add_argument("--window", type=int, default=250_000, help="Arrivals per row of the latency curve.")
    ap.add_argument("--phase", choices=["all", "generate", "seed", "measure", "report"], default="all")
    args = ap.parse_args()
    os.makedirs(args.dir, exist_ok=True)
    report_dir = args.report_dir or args.dir
    os.makedirs(report_dir, exist_ok=True)
    args.data = args.data or os.path.join(args.dir, f"scale_{args.records}.jsonl")
    state_path = os.path.join(args.dir, "state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}

    def save() -> None:
        json.dump(state, open(state_path, "w"), indent=1, default=str)

    if args.phase in ("all", "generate") and not os.path.exists(args.data):
        t0 = time.time()
        people = generate_to_file(args.records, args.seed, args.data, args.people_per_postcode,
                                  not args.no_large_buildings)
        print(f"generated {args.records:,} records for {people:,} people in {time.time() - t0:.0f}s", flush=True)

    if args.phase in ("all", "seed"):
        run.reset(args.db)
        state["seed"] = load("seed", args, 0, args.seed_records, durable=False)
        print("settling: VACUUM ANALYZE, CHECKPOINT", flush=True)
        with psycopg.connect(args.db, autocommit=True) as conn:
            conn.execute("VACUUM ANALYZE")
            conn.execute("CHECKPOINT")
        state["db_after_seed"] = db_facts(args.db)
        state.pop("measure", None)
        save()

    if args.phase in ("all", "measure"):
        before = io_counters(args.db)
        state["measure"] = load("measure", args, args.seed_records, args.records, durable=not args.async_measure)
        state["cache_hit_pct"] = hit_ratio(before, io_counters(args.db))
        state["db_after_measure"] = db_facts(args.db)
        save()

    if args.phase in ("all", "report", "measure"):
        rep = {"records": args.records, "seed_records": args.seed_records, "workers": args.workers,
               "people_per_postcode": args.people_per_postcode, "large_buildings": not args.no_large_buildings,
               **state, "analysis": analyse(args)}
        json.dump(rep, open(os.path.join(report_dir, "report.json"), "w"), indent=1, default=str)
        open(os.path.join(report_dir, "report.md"), "w").write(markdown(rep))
        print(markdown(rep), flush=True)


if __name__ == "__main__":
    main()

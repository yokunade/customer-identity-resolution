"""Score a benchmark run against ground truth.

    python -m benchmark.evaluate --data data.jsonl --results results/ --db postgresql://... --out report.json

Metrics
-------
pairwise      precision / recall / F1 over all record pairs (same customer vs same person)
clusters      people fully resolved, people split across customers, customers
              containing more than one person (over-merges)
per rule      share of links made by each rule that joined the right person
per scenario  for each tag: wrong-link rate and missed-link rate, decided at
              arrival time (record_id order):
                wrong link  = linked to a customer whose matched records belong to someone else
                missed link = new customer created although this person already had a record
reachable     a missed link is "unreachable" when the person's earlier records
              share no identifier at all with this one (no common email, phone,
              postcode or DOB+surname), so no deterministic system could link it
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
from collections import Counter, defaultdict
from statistics import median

import psycopg

from app.cleansing import cleanse
from app.matching.keys import build_blocking_keys, extract_components
from app.schemas import InboundCustomer

NEGATIVE_TAGS = {
    "household", "shared_landline", "shared_family_email", "shared_couple_mobile", "twin", "junior_senior",
    "doppelganger", "house_share", "wrong_email_other_person", "wrong_mobile_other_person",
    "partner_email_by_mistake", "placeholder_email", "placeholder_phone", "suffix_omitted",
}


def c2(n: int) -> int:
    return n * (n - 1) // 2


def pct(a: float, b: float) -> float:
    return round(100.0 * a / b, 3) if b else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--examples", type=int, default=40)
    args = ap.parse_args()

    data = [json.loads(line) for line in open(args.data)]
    results: dict[int, dict] = {}
    for path in glob.glob(os.path.join(args.results, "results_*.jsonl")):
        for line in open(path):
            r = json.loads(line)
            results[r["i"]] = r
    meta = json.load(open(os.path.join(args.results, "meta.json")))
    errors = [r for r in results.values() if "error" in r]

    with psycopg.connect(args.db) as conn:
        final = dict(conn.execute("SELECT record_id, customer_id::text FROM source_record").fetchall())

    # ---- index
    rid_person: dict[int, int] = {}
    rid_index: dict[int, int] = {}
    for i, r in results.items():
        if "record_id" in r:
            rid_person[r["record_id"]] = data[i]["truth"]["person_id"]
            rid_index[r["record_id"]] = i

    # ---- pairwise + cluster metrics on the final state
    cell: Counter = Counter()
    by_cust: dict[str, set[int]] = defaultdict(set)
    by_person: dict[int, set[str]] = defaultdict(set)
    cust_size: Counter = Counter()
    person_size: Counter = Counter()
    for rid, cid in final.items():
        p = rid_person[rid]
        cell[(cid, p)] += 1
        cust_size[cid] += 1
        person_size[p] += 1
        by_cust[cid].add(p)
        by_person[p].add(cid)
    tp = sum(c2(n) for n in cell.values())
    pred = sum(c2(n) for n in cust_size.values())
    true = sum(c2(n) for n in person_size.values())
    precision, recall = tp / pred if pred else 1.0, tp / true if true else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    overmerged = [c for c, ps in by_cust.items() if len(ps) > 1]
    split = [p for p, cs in by_person.items() if len(cs) > 1]
    perfect = sum(1 for p, cs in by_person.items() if len(cs) == 1 and len(by_cust[next(iter(cs))]) == 1)

    # ---- arrival-time decisions
    first_rid: dict[int, int] = {}
    for rid in sorted(rid_person):
        first_rid.setdefault(rid_person[rid], rid)

    tag_total: Counter = Counter()
    tag_wrong: Counter = Counter()
    tag_missed: Counter = Counter()
    tag_missed_unreach: Counter = Counter()
    tag_should_link: Counter = Counter()
    rule_total: Counter = Counter()
    rule_wrong: Counter = Counter()
    tier_total: Counter = Counter()
    tier_wrong: Counter = Counter()
    review_total: Counter = Counter()
    review_right: Counter = Counter()
    wrong_examples: list[dict] = []
    missed: list[tuple[int, int]] = []

    for i, r in results.items():
        if "record_id" not in r:
            continue
        truth = data[i]["truth"]
        p, rid = truth["person_id"], r["record_id"]
        tags = truth["tags"] or ["(clean)"]
        has_earlier = first_rid[p] < rid
        for t in tags:
            tag_total[t] += 1
            if has_earlier:
                tag_should_link[t] += 1
        if r["outcome"] == "matched":
            rule_total[r["rule"]] += 1
            tier_total[r["tier"]] += 1
            matched_people = {rid_person.get(m) for m in r["matched_record_ids"]}
            if matched_people != {p}:
                rule_wrong[r["rule"]] += 1
                tier_wrong[r["tier"]] += 1
                for t in tags:
                    tag_wrong[t] += 1
                wrong_examples.append({"i": i, "rule": r["rule"], "matched": r["matched_record_ids"]})
        elif r.get("rule") and r.get("auto_linked") is False:
            review_total[r["tier"]] += 1
            if {rid_person.get(m) for m in r["matched_record_ids"]} == {p}:
                review_right[r["tier"]] += 1
            if has_earlier:
                missed.append((i, p))
                for t in tags:
                    tag_missed[t] += 1
        elif r["outcome"] == "new_customer" and has_earlier:
            missed.append((i, p))
            for t in tags:
                tag_missed[t] += 1

    # ---- reachability of misses: does this record share any blocking key with an earlier record of the person?
    def bkeys(i: int) -> set:
        c, _ = cleanse(InboundCustomer(**data[i]["record"]))
        ks = build_blocking_keys(extract_components(c), "x")
        return {(t, v) for t, vals in ks.items() for v in vals}

    person_rids: dict[int, list[int]] = defaultdict(list)
    for rid in sorted(rid_person):
        person_rids[rid_person[rid]].append(rid)
    reachable_missed: list[int] = []
    for i, p in missed:
        rid = results[i]["record_id"]
        mine = bkeys(i)
        earlier = [rid_index[x] for x in person_rids[p] if x < rid]
        if any(mine & bkeys(j) for j in earlier):
            reachable_missed.append(i)
        else:
            for t in data[i]["truth"]["tags"] or ["(clean)"]:
                tag_missed_unreach[t] += 1

    # ---- latency
    ms = sorted(r["ms"] for r in results.values() if "ms" in r)
    pools = sorted(r["pool"] for r in results.values() if "pool" in r)

    def q(xs: list[float], f: float) -> float:
        return round(xs[min(len(xs) - 1, int(f * len(xs)))], 2) if xs else 0.0

    scenarios = []
    for t in sorted(tag_total, key=lambda x: -tag_total[x]):
        sl = tag_should_link[t]
        scenarios.append({
            "tag": t, "kind": "negative" if t in NEGATIVE_TAGS else "variation",
            "records": tag_total[t],
            "wrong_links": tag_wrong[t], "wrong_link_pct": pct(tag_wrong[t], tag_total[t]),
            "should_link": sl, "missed": tag_missed[t], "missed_pct": pct(tag_missed[t], sl),
            "missed_unreachable": tag_missed_unreach[t],
            "missed_reachable_pct": pct(tag_missed[t] - tag_missed_unreach[t], sl),
        })

    rng = random.Random(1)

    def show(i: int) -> dict:
        return {"record": data[i]["record"], "truth": data[i]["truth"], "result": {
            k: results[i].get(k) for k in ("customer_id", "outcome", "rule", "matched_record_ids")}}

    wrong_detail = []
    for ex in rng.sample(wrong_examples, min(args.examples, len(wrong_examples))):
        wrong_detail.append({"incoming": show(ex["i"]),
                             "matched_to": [show(rid_index[m]) for m in ex["matched"] if m in rid_index][:2]})
    missed_detail = []
    for i in rng.sample(reachable_missed, min(args.examples, len(reachable_missed))):
        p = data[i]["truth"]["person_id"]
        rid = results[i]["record_id"]
        missed_detail.append({"incoming": show(i), "earlier_records_of_same_person":
                              [show(rid_index[x]) for x in person_rids[p] if x < rid][:3]})

    linkable = sum(1 for r in results.values()
                   if "record_id" in r and first_rid[rid_person[r["record_id"]]] < r["record_id"])
    report = {
        "run": meta,
        "dataset": {"records": len(data), "people": len({d["truth"]["person_id"] for d in data}),
                    "customers_created": len(cust_size), "errors": len(errors)},
        "pairwise": {"precision": round(precision, 5), "recall": round(recall, 5), "f1": round(f1, 5),
                     "true_pairs": true, "predicted_pairs": pred, "correct_pairs": tp},
        "clusters": {
            "people": len(by_person), "people_perfectly_resolved": perfect,
            "people_perfect_pct": pct(perfect, len(by_person)),
            "people_split": len(split), "customers": len(by_cust),
            "customers_overmerged": len(overmerged), "customers_overmerged_pct": pct(len(overmerged), len(by_cust)),
            "records_in_overmerged_customers": sum(cust_size[c] for c in overmerged),
        },
        "arrival_decisions": {
            "records_that_should_link": linkable,
            "links_made": sum(rule_total.values()),
            "wrong_links": sum(rule_wrong.values()),
            "wrong_link_pct_of_links": pct(sum(rule_wrong.values()), sum(rule_total.values())),
            "missed_links": len(missed),
            "missed_reachable": len(reachable_missed),
            "missed_unreachable": len(missed) - len(reachable_missed),
            "recall_at_arrival_pct": pct(linkable - len(missed), linkable),
            "recall_of_reachable_pct": pct(linkable - len(missed), linkable - (len(missed) - len(reachable_missed))),
        },
        "review_queue": [{"tier": t, "suggestions": review_total[t], "correct": review_right[t]}
                         for t in review_total],
        "if_probable_sent_to_review_estimate": {
            "note": "Estimated from this run by treating probable-tier links as new customers; ignores knock-on effects.",
            "links_made": sum(rule_total.values()) - tier_total["probable"],
            "wrong_links": sum(rule_wrong.values()) - tier_wrong["probable"],
            "wrong_link_pct_of_links": pct(sum(rule_wrong.values()) - tier_wrong["probable"],
                                           sum(rule_total.values()) - tier_total["probable"]),
            "recall_at_arrival_pct": pct(linkable - len(missed) - (tier_total["probable"] - tier_wrong["probable"]),
                                         linkable),
            "review_queue_items": tier_total["probable"],
        },
        "by_tier": [{"tier": t, "links": tier_total[t], "wrong": tier_wrong[t],
                     "precision_pct": round(100 - pct(tier_wrong[t], tier_total[t]), 3)}
                    for t in ("exact", "strong", "probable", "possible")],
        "by_rule": sorted([{"rule": k, "links": v, "wrong": rule_wrong[k],
                            "precision_pct": round(100 - pct(rule_wrong[k], v), 3)}
                           for k, v in rule_total.items()], key=lambda x: -x["links"]),
        "by_scenario": scenarios,
        "performance": {"throughput_rps": round(meta["throughput_rps"], 1), "latency_ms_p50": q(ms, .5),
                        "latency_ms_p95": q(ms, .95), "latency_ms_p99": q(ms, .99), "latency_ms_max": q(ms, 1),
                        "pool_median": median(pools) if pools else 0, "pool_p99": q(pools, .99),
                        "pool_max": pools[-1] if pools else 0,
                        "records_with_skipped_blocks": sum(1 for r in results.values() if r.get("skipped"))},
        "examples": {"wrong_links": wrong_detail, "missed_reachable": missed_detail},
        "errors_sample": errors[:10],
    }
    json.dump(report, open(args.out, "w"), indent=1, default=str)
    s = {k: report[k] for k in ("dataset", "pairwise", "clusters", "arrival_decisions", "by_tier", "performance")}
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()

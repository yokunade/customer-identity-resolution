"""Score an AI reviewer's predictions against an eval export.

    python -m app.review.export --format eval --source all --split test --out eval.jsonl
    # run your model over each line's "input" and write predictions.jsonl:
    #   {"id": "<same id>", "output": "<the model's raw text answer>"}   or
    #   {"id": "<same id>", "decision": "merge" | "keep_apart", "survivor": "A" | "B" | null}
    python -m benchmark.score_ai_reviewer --eval eval.jsonl --predictions predictions.jsonl

    # reference points, no model needed:
    python -m benchmark.score_ai_reviewer --eval eval.jsonl --baseline always_merge
    python -m benchmark.score_ai_reviewer --eval eval.jsonl --baseline no_conflict

Most review cases are the same person, so accuracy flatters a reviewer that
always merges. The headline numbers are therefore:
  wrong merges      predicted merge, truth keep apart (mixes two people's data)
  keep-apart recall share of true keep-apart cases the reviewer caught
  merge recall      share of true merges the reviewer caught
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict


def parse_output(text: str) -> dict | None:
    """Pull the JSON answer out of a model's raw text."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if obj.get("decision") in ("merge", "keep_apart") else None


def baseline(name: str, item: dict) -> dict:
    if name == "always_merge":
        return {"decision": "merge", "survivor": "A"}
    if name == "no_conflict":  # merge unless the comparison shows a conflict
        conflict = (item["metadata"].get("summary") or {}).get("conflict", 0)
        return {"decision": "keep_apart", "survivor": None} if conflict else {"decision": "merge", "survivor": "A"}
    raise ValueError(name)


def score(items: list[dict], preds: dict[str, dict | None]) -> dict:
    cm = Counter()
    by_tier: dict[str, Counter] = defaultdict(Counter)
    invalid = survivor_wrong = 0
    for it in items:
        truth = it["expected"]["decision"]
        p = preds.get(it["id"])
        tier = it["metadata"].get("tier") or "?"
        if p is None:
            invalid += 1
            cm[(truth, "invalid")] += 1
            by_tier[tier][(truth, "invalid")] += 1
            continue
        cm[(truth, p["decision"])] += 1
        by_tier[tier][(truth, p["decision"])] += 1
        if truth == p["decision"] == "merge" and it["expected"].get("survivor") and \
                p.get("survivor") != it["expected"]["survivor"]:
            survivor_wrong += 1

    def rates(c: Counter) -> dict:
        tm = sum(v for (t, _), v in c.items() if t == "merge")
        tk = sum(v for (t, _), v in c.items() if t == "keep_apart")
        n = tm + tk
        correct = c[("merge", "merge")] + c[("keep_apart", "keep_apart")]
        return {
            "cases": n,
            "accuracy": round(correct / n, 4) if n else None,
            "wrong_merges": c[("keep_apart", "merge")],
            "missed_merges": c[("merge", "keep_apart")],
            "keep_apart_recall": round(c[("keep_apart", "keep_apart")] / tk, 4) if tk else None,
            "merge_recall": round(c[("merge", "merge")] / tm, 4) if tm else None,
        }

    return {**rates(cm), "invalid_outputs": invalid, "survivor_disagreements": survivor_wrong,
            "by_tier": {t: rates(c) for t, c in sorted(by_tier.items())}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--predictions")
    g.add_argument("--baseline", choices=("always_merge", "no_conflict"))
    args = ap.parse_args()

    items = [json.loads(line) for line in open(args.eval, encoding="utf-8") if line.strip()]
    if args.baseline:
        preds = {it["id"]: baseline(args.baseline, it) for it in items}
    else:
        preds = {}
        for line in open(args.predictions, encoding="utf-8"):
            if not line.strip():
                continue
            row = json.loads(line)
            preds[row["id"]] = parse_output(row["output"]) if "output" in row else (
                row if row.get("decision") in ("merge", "keep_apart") else None)
    print(json.dumps(score(items, preds), indent=2))


if __name__ == "__main__":
    main()

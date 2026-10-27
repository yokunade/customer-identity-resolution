"""Export labelled review cases for prompt engineering, fine-tuning and evaluation.

    python -m app.review.export --format chat   --out train.jsonl --split train
    python -m app.review.export --format eval   --out test.jsonl  --split test
    python -m app.review.export --format prompt --out prompt.md   --shots 6
    python -m app.review.export --format raw    --out all.jsonl   --pii full

Formats
  chat    one JSON object per line: {"messages": [system, user, assistant], "metadata": {...}}
  eval    {"id", "input", "expected": {decision, survivor}, "metadata"} for scoring a model
  raw     the stored example (snapshot, labels, reviewer metadata) for your own tooling
  prompt  a markdown few-shot prompt: instructions, k worked examples, then a {case} slot

PII is masked by default. Use --pii full only for models you host yourself.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from app.review.prompting import (
    PROMPT_VERSION,
    Masker,
    chat_messages,
    few_shot_prompt,
    render_case,
    select_few_shot,
)

FORMATS = ("chat", "eval", "raw", "prompt")


def load_examples(conn: psycopg.Connection, *, split: str = "all", source: str = "reviewer") -> list[dict]:
    rows = conn.execute(
        "SELECT e.*, u.email AS reviewer_email FROM review_example e"
        " LEFT JOIN review_user u ON u.user_id = e.reviewer_id"
        " WHERE (%(split)s = 'all' OR e.split = %(split)s) AND (%(source)s = 'all' OR e.label_source = %(source)s)"
        " ORDER BY e.example_id",
        {"split": split, "source": source},
    ).fetchall()
    conn.rollback()
    return rows


def _metadata(ex: dict) -> dict[str, Any]:
    snap = ex["snapshot"]
    return {"example_id": ex["example_id"], "case_id": ex["case_id"], "split": ex["split"],
            "label_source": ex["label_source"], "rule": snap.get("rule"), "tier": snap.get("tier"),
            "summary": snap.get("summary"), "confidence": ex.get("confidence"),
            "prompt_version": PROMPT_VERSION, "schema_version": ex["schema_version"]}


def iter_lines(examples: list[dict], fmt: str, masked: bool) -> Iterator[str]:
    for ex in examples:
        if fmt == "chat":
            obj = {"messages": chat_messages(ex, masked), "metadata": _metadata(ex)}
        elif fmt == "eval":
            obj = {"id": f"case-{ex['case_id']}-{ex['label_source']}",
                   "input": render_case(ex["snapshot"], Masker() if masked else None),
                   "expected": {"decision": ex["decision"], "survivor": ex["survivor"]},
                   "metadata": _metadata(ex)}
        elif fmt == "raw":
            if masked:
                raise ValueError("raw export contains the full snapshot; use --pii full for it")
            obj = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in ex.items()}
        else:
            raise ValueError(f"unknown format {fmt}")
        yield json.dumps(obj, ensure_ascii=False, default=str)


def export(conn: psycopg.Connection, fmt: str, *, split: str = "all", source: str = "reviewer",
           masked: bool = True, shots: int = 6) -> str:
    if fmt == "prompt":
        pool = load_examples(conn, split="train", source=source)
        return few_shot_prompt(select_few_shot(pool, shots), masked)
    return "\n".join(iter_lines(load_examples(conn, split=split, source=source), fmt, masked)) + "\n"


def main() -> None:
    from app.config import get_settings

    ap = argparse.ArgumentParser(prog="python -m app.review.export", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--format", choices=FORMATS, default="chat")
    ap.add_argument("--split", choices=("train", "dev", "test", "all"), default="all")
    ap.add_argument("--source", choices=("reviewer", "synthetic_truth", "all"), default="reviewer",
                    help="reviewer decisions (default), synthetic ground truth from the demo, or both")
    ap.add_argument("--pii", choices=("masked", "full"), default="masked")
    ap.add_argument("--shots", type=int, default=6, help="examples in a prompt export")
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    with psycopg.connect(get_settings().database_url, row_factory=dict_row) as conn:
        try:
            text = export(conn, args.format, split=args.split, source=args.source,
                          masked=args.pii == "masked", shots=args.shots)
        except ValueError as exc:
            sys.exit(str(exc))
    if args.out == "-":
        sys.stdout.write(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        n = text.count("\n") if args.format != "prompt" else args.shots
        print(f"Wrote {args.out} ({args.format}, {n} item(s), PII {args.pii})", file=sys.stderr)


if __name__ == "__main__":
    main()

"""Turn review cases into prompts and targets for an AI reviewer.

Everything here is deterministic text generation over a case snapshot (see
cases.snapshot_of), so the same example always renders the same way and a
prompt change is a code change you can diff and evaluate.

    render_case(snapshot, masker)  -> the user message a model sees
    render_target(example, masker) -> the JSON answer we expect back
    SYSTEM_PROMPT                  -> the reviewer's instructions

PII: pass a Masker to replace names, emails, phones, addresses and dates of
birth with placeholders that are consistent within one example ("Name 1"
everywhere it appears, including in the reviewer's notes). Relationships the
model needs (shared values, verdicts, nickname/typo notes) are kept, because
they come from the comparison, not from the raw values.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from app.review.reasons import CONFIDENCE, KEEP_APART_REASONS, MERGE_REASONS

PROMPT_VERSION = "2026-10-27.1"

_REASON_LINES = "\n".join(
    [f"  - {k}: {v}" for k, v in MERGE_REASONS.items()]
    + [f"  - {k}: {v}" for k, v in KEEP_APART_REASONS.items()]
)

SYSTEM_PROMPT = f"""You review possible duplicate customer records for a UK organisation.

Each case shows two customers, A and B. Each customer is built from one or more
source records held by different systems (CRM, e-commerce, loyalty, billing,
call centre, marketing). An incoming record matched both customers, or matched
one of them on evidence too weak to link automatically. Decide whether A and B
are the same real person.

How to judge
- Weigh identifiers by how personal they are. A date of birth plus a full name
  is strong. A mobile number or personal email is strong. A shared address,
  landline or family email (e.g. thesmiths@...) is weak on its own: UK
  households share them.
- Treat as conflicts unless clearly a data error: different dates of birth
  (but a day/month swap or one mistyped digit is usually an error), different
  generations (Jr vs Sr), different gender.
- Expect noise: nicknames (Bill/William, Liz/Elizabeth), typos, missing fields,
  surname changes after marriage, house moves, postcodes written differently.
- Parents and children, and fathers and sons with the same name, often live at
  the same address. Same name + same address with no date of birth on one side
  is NOT enough to merge if anything suggests two generations.
- When the evidence is thin, keep them apart: a wrong merge mixes two people's
  data, which is worse than a missed duplicate.
- If you merge, keep customer A unless B is clearly the better record (A is
  the older customer ID and other systems are more likely to hold it).

Reason codes (use only codes valid for your decision)
{_REASON_LINES}

Answer with JSON only, no other text:
{{"decision": "merge" | "keep_apart",
  "survivor": "A" | "B" | null,
  "reason_codes": ["..."],
  "confidence": {" | ".join(f'"{c}"' for c in CONFIDENCE)},
  "rationale": "one or two sentences citing the evidence"}}
"""


# ------------------------------------------------------------------ masking


class Masker:
    """Consistent placeholders for PII within one example."""

    KINDS = {"name": "Name", "dob": "DOB", "email": "Email", "phone": "Phone", "address": "Address",
             "postcode": "Postcode", "token": "Name part"}

    def __init__(self) -> None:
        self._maps: dict[str, dict[str, str]] = defaultdict(dict)
        self._raw: dict[str, str] = {}  # original text -> placeholder, for scrubbing free text

    def __call__(self, kind: str, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        key = re.sub(r"\s+", " ", str(value).strip().lower())
        m = self._maps[kind]
        if key not in m:
            m[key] = f"[{self.KINDS[kind]} {len(m) + 1}]"
        self._raw[str(value)] = m[key]
        return m[key]

    def learn_name_parts(self, full_name: str) -> None:
        for part in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", full_name or ""):
            if part.lower() not in {"jr", "sr", "mr", "mrs", "miss", "ms", "dr"}:
                self("token", part)

    def scrub(self, text: str | None) -> str | None:
        """Replace every known original value in free text (longest first)."""
        if not text:
            return text
        out = text
        for raw in sorted(self._raw, key=len, reverse=True):
            if len(raw) < 3:
                continue
            out = re.sub(re.escape(raw), self._raw[raw], out, flags=re.IGNORECASE)
            digits = re.sub(r"\D", "", raw)
            if len(digits) >= 9:  # phone numbers written another way
                pattern = r"\D*".join(re.escape(d) for d in digits[-9:])
                out = re.sub(r"(?:\+?44\s*|0)?" + pattern, self._raw[raw], out)
        return out


def _identity(kind: str, value: str | None) -> str | None:
    return value


# ------------------------------------------------------------------ rendering

_FIELD_KIND = {"name": "name", "dob": "dob", "email": "email", "phone": "phone", "address": "address"}


def _value_text(field: str, value: str, mask) -> str:
    kind = _FIELD_KIND.get(field)
    if kind is None or mask is _identity:
        return value
    return mask(kind, value)


def _prime_masker(snapshot: dict, masker: Masker) -> None:
    """Register every PII value up front so placeholders are numbered in reading order."""
    for row in snapshot["comparison"]:
        for side in ("a", "b"):
            for v in row[side]:
                kind = _FIELD_KIND.get(row["field"])
                if kind:
                    masker(kind, v["value"])
                    if kind == "name":
                        masker.learn_name_parts(v["value"])
    for side in ("customer_a", "customer_b"):
        for r in snapshot[side]["records"]:
            c = r.get("cleansed") or {}
            for a in [c.get("address"), *(c.get("previous_addresses") or [])]:
                if a and a.get("postcode"):
                    masker("postcode", a["postcode"])


def render_case(snapshot: dict[str, Any], masker: Masker | None = None) -> str:
    mask = masker if masker is not None else _identity
    if masker is not None:
        _prime_masker(snapshot, masker)
    a, b = snapshot["customer_a"], snapshot["customer_b"]
    lines = [
        f"Case raised by rule {snapshot['rule']} ({snapshot['rule_description']}), {snapshot['tier']} tier.",
        f"Customer A: {len(a['records'])} source record(s), the older customer.",
        f"Customer B: {len(b['records'])} source record(s).",
        "",
        "Comparison (values across each customer's records; [both] = held by A and B):",
    ]
    for row in snapshot["comparison"]:
        lines.append(f"\n{row['label']}: {row['verdict'].upper()}" + (f" ({row['note']})" if row.get("note") else ""))
        for side, letter in (("a", "A"), ("b", "B")):
            vals = row[side]
            if not vals:
                lines.append(f"  {letter}: not recorded")
                continue
            parts = []
            for v in vals:
                n = len(v["sources"])
                parts.append(f"{_value_text(row['field'], v['value'], mask)}"
                             f" ({n} source{'s' if n != 1 else ''}){' [both]' if v.get('shared') else ''}")
            lines.append(f"  {letter}: " + "; ".join(parts))

    lines.append("\nSource records:")
    for cust, letter in ((a, "A"), (b, "B")):
        for r in cust["records"]:
            c = r.get("cleansed") or {}
            n = c.get("name") or {}
            name = " ".join(x for x in [n.get("first_name"), n.get("middle_names"), n.get("last_name"),
                                         n.get("suffix")] if x)
            bits = [f"{letter} | {r['source_system']}"]
            if name:
                bits.append("name " + str(_value_text("name", name, mask)))
            if c.get("date_of_birth") and c.get("date_of_birth_usable"):
                bits.append("born " + str(_value_text("dob", c["date_of_birth"], mask)))
            if c.get("gender"):
                bits.append("gender " + c["gender"])
            for e in c.get("emails") or []:
                bits.append("email " + str(_value_text("email", e["address"], mask)))
            for p in c.get("phones") or []:
                bits.append(f"{p['type']} " + str(_value_text("phone", p.get("national") or p["e164"], mask)))
            if c.get("address"):
                addr = ", ".join(c["address"].get("lines") or [])
                bits.append("address " + str(_value_text("address", addr, mask)))
            lines.append("  " + " | ".join(bits))
    return "\n".join(lines)


def render_target(example: dict[str, Any], masker: Masker | None = None) -> str:
    rationale = example.get("notes")
    if masker is not None:
        rationale = masker.scrub(rationale)
    return json.dumps({
        "decision": example["decision"],
        "survivor": example.get("survivor"),
        "reason_codes": list(example.get("reason_codes") or []),
        "confidence": example.get("confidence"),
        "rationale": rationale,
    }, ensure_ascii=False)


# ------------------------------------------------------------------ few-shot


def select_few_shot(examples: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Pick up to k examples that cover decisions, tiers and conflict patterns.
    Deterministic: round-robins over (decision, tier, has_conflict) groups."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ex in sorted(examples, key=lambda e: e["example_id"]):
        snap = ex["snapshot"]
        groups[(ex["decision"], snap.get("tier"), snap["summary"].get("conflict", 0) > 0)].append(ex)
    order = sorted(groups)
    picked: list[dict] = []
    while len(picked) < k and any(groups.values()):
        for key in order:
            if groups[key] and len(picked) < k:
                picked.append(groups[key].pop(0))
    return picked


def chat_messages(example: dict[str, Any], masked: bool) -> list[dict[str, str]]:
    masker = Masker() if masked else None
    user = render_case(example["snapshot"], masker)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
        {"role": "assistant", "content": render_target(example, masker)},
    ]


def few_shot_prompt(shots: list[dict[str, Any]], masked: bool) -> str:
    parts = [SYSTEM_PROMPT.rstrip(), "", "Worked examples follow.", ""]
    for i, ex in enumerate(shots, 1):
        masker = Masker() if masked else None
        parts += [f"### Example {i}", "", render_case(ex["snapshot"], masker), "", "Answer:",
                  render_target(ex, masker), ""]
    parts += ["### Now review this case", "", "{case}"]
    return "\n".join(parts)

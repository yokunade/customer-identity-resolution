"""Turn review cases into prompts and targets for an AI reviewer.

Everything here is deterministic text generation over a case snapshot (see
cases.snapshot_of), so the same example always renders the same way and a
prompt change is a code change you can diff and evaluate.

    render_case(snapshot, masker)  -> the user message a model sees
    render_target(example, masker) -> the JSON answer we expect back
    SYSTEM_PROMPT                  -> the reviewer's instructions

PII: pass a Masker to replace names, emails, phones, addresses and dates of
birth with placeholders that are consistent within one example ("Name 1"
everywhere it appears). Structured values are masked exactly; scrubbing the
reviewer's free-text notes is best effort, so masked exports leave notes out
unless asked. Relationships the
model needs (shared values, verdicts, nickname/typo notes) are kept, because
they come from the comparison, not from the raw values.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from app.cleansing.address import ABBREVIATIONS, DESCRIPTORS
from app.review.reasons import CONFIDENCE, KEEP_APART_REASONS, MERGE_REASONS

PROMPT_VERSION = "2026-11-02.1"

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
             "postcode": "Postcode", "token": "Name part", "street": "Address part"}

    def __init__(self) -> None:
        self._maps: dict[str, dict[str, str]] = defaultdict(dict)
        # For scrubbing free text: regex -> (length of the value it came from, placeholder).
        self._patterns: dict[str, tuple[int, str]] = {}

    def __call__(self, kind: str, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        key = re.sub(r"\s+", " ", str(value).strip().lower())
        m = self._maps[kind]
        if key not in m:
            m[key] = f"[{self.KINDS[kind]} {len(m) + 1}]"
        self._learn(kind, str(value), m[key])
        return m[key]

    def _add(self, pattern: str, length: int, placeholder: str) -> None:
        self._patterns.setdefault(pattern, (length, placeholder))

    def _learn(self, kind: str, raw: str, placeholder: str) -> None:
        # Whole words only, so a short name part like "Li" never eats "Linden".
        self._add(rf"(?<![A-Za-z0-9]){_spaced(raw)}(?![A-Za-z0-9])", len(raw), placeholder)
        if kind == "dob":
            for p in _dob_patterns(raw):
                self._add(p, len(raw), placeholder)
        elif kind == "address":
            for part in raw.split(","):  # "12 ACACIA AVENUE, LEEDS, LS6 2AB": each line on its own
                if len(part.strip()) >= 4:
                    self._add(rf"(?<![A-Za-z0-9]){_spaced(part.strip())}(?![A-Za-z0-9])", len(part), placeholder)
        elif kind == "postcode":
            out, _, inw = raw.upper().partition(" ")
            if inw:  # LS62AB, ls6-2ab
                self._add(rf"(?<![A-Za-z0-9]){re.escape(out)}[\s\-]*{re.escape(inw)}(?![A-Za-z0-9])", len(raw),
                          placeholder)
        elif kind == "phone":
            digits = re.sub(r"\D", "", raw)
            if len(digits) >= 9:  # phone numbers written another way
                pattern = r"\D*".join(re.escape(d) for d in digits[-9:])
                self._add(r"(?:\+?44\s*|0)?" + pattern, len(raw), placeholder)

    def learn_name_parts(self, full_name: str) -> None:
        for part in re.findall(r"[A-Za-z][A-Za-z'\-]+", full_name or ""):
            if part.lower() not in {"jr", "sr", "mr", "mrs", "miss", "ms", "dr"}:
                self("token", part)

    def learn_address_parts(self, address: dict) -> None:
        """Street and building names, so "12 Acacia Ave" is caught when the record says ACACIA AVENUE."""
        for value in (address.get("street"), address.get("building_name")):
            words = [w for w in (value or "").split() if w not in _ADDRESS_WORDS]
            if words and len("".join(words)) >= 3:
                self("street", " ".join(words))

    def scrub(self, text: str | None) -> str | None:
        """Replace every known original value in free text (longest first). Best effort:
        free text can always describe a person in a way no pattern anticipates."""
        if not text:
            return text
        out = text
        for pattern, (_, placeholder) in sorted(self._patterns.items(), key=lambda p: -p[1][0]):
            out = re.sub(pattern, placeholder, out, flags=re.IGNORECASE)
        return out


_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
                "october", "november", "december"]
_ADDRESS_WORDS = {*DESCRIPTORS, *ABBREVIATIONS.values(), "THE", "SAINT"}


def _spaced(raw: str) -> str:
    """The literal text, with any run of spaces matching any run of spaces."""
    return r"\s+".join(re.escape(w) for w in raw.split())


def _dob_patterns(iso: str) -> list[str]:
    """14/02/1979, 14.2.79, 14-02-1979, 14 Feb 1979, 14th February 1979, February 14, 1979."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso)
    if not m:
        return []
    y, mo, d = m[1], int(m[2]), int(m[3])
    year = rf"(?:{y}|{y[2:]})"
    day = rf"0?{d}(?:st|nd|rd|th)?"
    month_name = _MONTH_NAMES[mo - 1]
    month_word = rf"(?:{month_name[:3]}\.?|{month_name})"
    edge_l, edge_r = r"(?<![0-9])", r"(?![0-9])"
    return [
        rf"{edge_l}{day}[/.\-]0?{mo}[/.\-]{year}{edge_r}",
        rf"{edge_l}{day}\s*{month_word}\s*,?\s*{year}{edge_r}",
        rf"{month_word}\s*{day}\s*,?\s*{year}{edge_r}",
    ]


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
                if a:
                    masker.learn_address_parts(a)


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


def render_target(example: dict[str, Any], masker: Masker | None = None, include_notes: bool = True) -> str:
    """The reviewer's notes become the rationale. Masked exports leave them out unless
    include_notes is set: scrubbing free text is best effort, so notes can leak PII."""
    rationale = example.get("notes") if include_notes else None
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


def chat_messages(example: dict[str, Any], masked: bool, include_notes: bool = False) -> list[dict[str, str]]:
    masker = Masker() if masked else None
    user = render_case(example["snapshot"], masker)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
        {"role": "assistant", "content": render_target(example, masker, include_notes or not masked)},
    ]


def few_shot_prompt(shots: list[dict[str, Any]], masked: bool, include_notes: bool = False) -> str:
    parts = [SYSTEM_PROMPT.rstrip(), "", "Worked examples follow.", ""]
    for i, ex in enumerate(shots, 1):
        masker = Masker() if masked else None
        parts += [f"### Example {i}", "", render_case(ex["snapshot"], masker), "", "Answer:",
                  render_target(ex, masker, include_notes or not masked), ""]
    parts += ["### Now review this case", "", "{case}"]
    return "\n".join(parts)

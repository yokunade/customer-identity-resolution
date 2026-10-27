"""Structured reasons a reviewer gives for a decision.

These become labels for training and evaluating an AI reviewer, so codes
are stable identifiers: add new ones freely, but never rename or reuse one.
"""

from __future__ import annotations

MERGE_REASONS: dict[str, str] = {
    "same_dob": "Same date of birth",
    "same_email": "Same email",
    "same_phone": "Same phone",
    "same_address": "Same address",
    "nickname_or_typo": "Nickname or spelling difference",
    "surname_change": "Surname changed (e.g. marriage)",
    "moved_address": "Moved address",
    "dob_entry_error": "DOB typo or day/month swapped",
    "data_entry_error": "Other data entry error",
}

KEEP_APART_REASONS: dict[str, str] = {
    "different_dob": "Different dates of birth",
    "different_generation": "Different generations (Jr/Sr)",
    "different_gender": "Different gender",
    "household_member": "Different people in one household",
    "shared_contact": "Shared family email or phone",
    "name_coincidence": "Same name, different people",
    "insufficient_evidence": "Not enough evidence to merge",
}

REASONS = {"merge": MERGE_REASONS, "keep_apart": KEEP_APART_REASONS}
CONFIDENCE = ("certain", "likely", "unsure")


def validate(decision: str, codes: list[str]) -> list[str]:
    allowed = REASONS.get(decision, {})
    clean = list(dict.fromkeys(c for c in codes if c))
    bad = [c for c in clean if c not in allowed]
    if bad:
        raise ValueError(f"Reason not valid for this decision: {', '.join(bad)}")
    if not clean:
        raise ValueError("Pick at least one reason.")
    return clean


def options() -> dict[str, list[dict[str, str]]]:
    return {d: [{"code": k, "label": v} for k, v in r.items()] for d, r in REASONS.items()}

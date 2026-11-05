"""Engine settings that the demo tuning page can change, as plain JSON.

    {"rules": {"EMAIL_NAME_DOB": {"mode": "auto", "vetoes": ["dob", "generation"], "dob_mode": "exact"}, ...},
     "shared_contact_customers": 10, "shared_postcode_customers": 250, "max_auto_merge_records": 50}

A rule's mode is "auto" (its matches link), "review" (they go to review) or
"off" (it never fires). Rules themselves, their order and their match keys
stay in code (app/matching/rules.py): changing those needs tests.
"""

from __future__ import annotations

from dataclasses import replace

from app.config import Settings
from app.matching.keys import BLOCKING_KEYS, MATCH_KEYS
from app.matching.rules import RULES, Rule

MODES = ("auto", "review", "off")
VETOES = {
    "dob": "Dates of birth differ",
    "gender": "One is male, the other female",
    "generation": "Jr and Sr",
    "middle_initial": "Middle initials differ",
    "first_name": "Forenames differ (not a nickname or a one-letter slip)",
    "first_name_letters": "Forenames differ, unless the same letters in another order",
    "postcode": "Postcodes differ (for street-only rules)",
}
NUMBERS = {
    "shared_contact_customers": ("An email or phone held by more customers than this is not matched on", 1, 1000),
    "shared_postcode_customers": ("A postcode held by more customers than this is not matched on", 1, 100_000),
    "max_auto_merge_records": ("Customers holding more records than this together are not merged automatically",
                               2, 1000),
}
TIERS = ("exact", "strong", "probable", "possible")


def current(settings: Settings) -> dict:
    """The settings in force, as config JSON."""
    in_force = {r.key: r for r in (settings.rules if settings.rules is not None else RULES)}
    rules = {}
    for base in RULES:
        r = in_force.get(base.key)
        if r is None:
            mode = "off"
            r = base
        elif r.tier not in settings.auto_link_tiers or r.key in settings.review_rules:
            mode = "review"
        else:
            mode = "auto"
        rules[r.key] = {"mode": mode, "vetoes": sorted(r.vetoes), "dob_mode": r.dob_mode}
    return {"rules": rules, **{k: getattr(settings, k) for k in NUMBERS}}


def apply(settings: Settings, cfg: dict) -> Settings:
    """settings with cfg in force. Raises ValueError naming what is wrong."""
    rules_cfg = cfg.get("rules")
    if not isinstance(rules_cfg, dict):
        raise ValueError("rules must be an object keyed by rule")
    unknown = set(rules_cfg) - {r.key for r in RULES}
    if unknown:
        raise ValueError(f"unknown rules: {', '.join(sorted(unknown))}")
    rules: list[Rule] = []
    review: set[str] = set()
    for base in RULES:
        rc = rules_cfg.get(base.key, {})
        mode = rc.get("mode", "auto")
        if mode not in MODES:
            raise ValueError(f"{base.key}: mode must be one of {', '.join(MODES)}")
        vetoes = rc.get("vetoes", sorted(base.vetoes))
        if not isinstance(vetoes, list) or set(vetoes) - set(VETOES):
            raise ValueError(f"{base.key}: vetoes must be from {', '.join(VETOES)}")
        dob_mode = rc.get("dob_mode", base.dob_mode)
        if dob_mode not in ("exact", "lenient"):
            raise ValueError(f"{base.key}: dob_mode must be exact or lenient")
        if mode == "off":
            continue
        if mode == "review":
            review.add(base.key)
        rules.append(Rule(base.priority, base.key, base.tier, frozenset(vetoes), dob_mode))
    numbers = {}
    for key, (_, lo, hi) in NUMBERS.items():
        v = cfg.get(key, getattr(settings, key))
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise ValueError(f"{key} must be a whole number from {lo} to {hi}")
        numbers[key] = v
    return replace(settings, rules=tuple(rules), review_rules=frozenset(review),
                   auto_link_tiers=frozenset(TIERS), **numbers)


def changes(before: dict | None, after: dict) -> list[str]:
    """What differs from before to after, in words."""
    if before is None:
        return []
    out = []
    for key, rc in after["rules"].items():
        old = before["rules"].get(key)
        if old is None:
            continue
        if rc["mode"] != old["mode"]:
            out.append(f"{key}: {old['mode']} → {rc['mode']}")
        added, gone = set(rc["vetoes"]) - set(old["vetoes"]), set(old["vetoes"]) - set(rc["vetoes"])
        out += [f"{key}: + {v} veto" for v in sorted(added)] + [f"{key}: − {v} veto" for v in sorted(gone)]
        if rc["dob_mode"] != old["dob_mode"]:
            out.append(f"{key}: DOB {old['dob_mode']} → {rc['dob_mode']}")
    for key in NUMBERS:
        if after.get(key) != before.get(key):
            out.append(f"{key}: {before.get(key)} → {after.get(key)}")
    return out


def describe() -> dict:
    """The rules, vetoes and keys, for the tuning page."""
    return {
        "rules": [{"key": r.key, "priority": r.priority, "tier": r.tier, "description": MATCH_KEYS[r.key].description,
                   "components": list(MATCH_KEYS[r.key].components), "default_vetoes": sorted(r.vetoes),
                   "default_dob_mode": r.dob_mode} for r in RULES],
        "vetoes": [{"key": k, "label": v} for k, v in VETOES.items()],
        "numbers": [{"key": k, "label": label, "min": lo, "max": hi} for k, (label, lo, hi) in NUMBERS.items()],
        "blocking_keys": [{"key": k.name, "description": k.description, "components": list(k.components)}
                          for k in BLOCKING_KEYS.values()],
    }

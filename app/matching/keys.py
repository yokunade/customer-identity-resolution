"""Deterministic match keys and blocking keys.

A *component* is a named set of normalised strings taken from a cleansed
record (e.g. ``first_forms`` = {"bill", "william"}). A *key* is an ordered
list of components; its values are the cartesian product of the component
values, joined and HMAC'd. Two records share a key when every component has
at least one value in common, which is exactly the condition a match rule
needs. If any component is empty the key is not generated.

Match keys are specific (a rule fires when one is shared). Blocking keys are
broad and only gather the candidate pool.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
from dataclasses import dataclass

from app.cleansing.address import premise_keys
from app.cleansing.dob import unordered_dob
from app.cleansing.forename_gender import gender_from_forename
from app.cleansing.names import PARTICLES
from app.cleansing.nicknames import first_name_forms
from app.cleansing.text import alnum_key, alpha_key
from app.matching.phonetics import nysiis, soundex
from app.schemas import CleansedRecord

Components = dict[str, frozenset[str]]
KeySet = dict[str, set[bytes]]

MAX_VALUES_PER_KEY = 64
KEY_BYTES = 16


# --------------------------------------------------------------------------- components


def _last_parts(last_name: str | None) -> list[str]:
    import re

    if not last_name:
        return []
    parts = [alpha_key(p) for p in re.split(r"[\s\-]+", last_name)]
    parts = [p for p in parts if len(p) > 1 and p.upper() not in PARTICLES]
    return parts if len(parts) > 1 else []


def extract_components(rec: CleansedRecord) -> Components:
    first_key = alpha_key(rec.name.first_name)
    last_key = alpha_key(rec.name.last_name)
    c: dict[str, set[str]] = {
        "first_forms": set(first_name_forms(first_key)),
        "first_initial": {first_key[0]} if first_key else set(),
        "first_soundex": {soundex(first_key)} if len(first_key) > 1 else set(),
        "first_nysiis": {nysiis(first_key)} if len(first_key) > 1 else set(),
        "last_key": {last_key} if len(last_key) > 1 else set(),
        "last_parts": set(_last_parts(rec.name.last_name)),
        "last_soundex": {soundex(last_key)} if len(last_key) > 1 else set(),
        "last_nysiis": {nysiis(last_key)} if len(last_key) > 1 else set(),
        "dob": set(),
        "dob_unordered": set(),
        "email": {e.canonical for e in rec.emails if e.usable_for_matching},
        "phone": {p.e164 for p in rec.phones if p.usable_for_matching},
        "mobile": {p.e164 for p in rec.phones if p.usable_for_matching and p.type == "mobile"},
        "uprn": set(),
        "postcode": set(),
        "district": set(),
        "premise_postcode": set(),
        "premise_district": set(),
        "premise_street": set(),
    }
    if rec.date_of_birth and rec.date_of_birth_usable:
        c["dob"].add(rec.date_of_birth.isoformat())
        c["dob_unordered"].add(unordered_dob(rec.date_of_birth))

    for addr in [a for a in [rec.address, *rec.previous_addresses] if a]:
        if addr.uprn:
            c["uprn"].add(addr.uprn)
        street, town = alnum_key(addr.street), alnum_key(addr.post_town)
        if street and town:  # lets a record without a postcode meet one with it
            for prem in premise_keys(addr):
                c["premise_street"].add(f"{prem}@{street}@{town}")
        if not addr.postcode:
            continue
        pc = addr.postcode.replace(" ", "") if addr.is_uk else f"{addr.country}:{addr.postcode}"
        c["postcode"].add(pc)
        district = addr.postcode_district or None
        if district:
            c["district"].add(district)
        for prem in premise_keys(addr):
            c["premise_postcode"].add(f"{prem}@{pc}")
            if district:
                c["premise_district"].add(f"{prem}@{district}")
    return {k: frozenset(v) for k, v in c.items()}


# --------------------------------------------------------------------------- key definitions


@dataclass(frozen=True)
class KeyDef:
    name: str
    components: tuple[str, ...]
    description: str


MATCH_KEYS: dict[str, KeyDef] = {k.name: k for k in [
    KeyDef("EMAIL_NAME_DOB", ("email", "first_forms", "last_key", "dob"), "Email + forename + surname + DOB"),
    KeyDef("PHONE_NAME_DOB", ("phone", "first_forms", "last_key", "dob"), "Any personal phone + forename + surname + DOB"),
    KeyDef("UPRN_NAME_DOB", ("uprn", "first_forms", "last_key", "dob"), "UPRN + forename + surname + DOB"),
    KeyDef("NAME_DOB_PREMISE_POSTCODE", ("first_forms", "last_key", "dob", "premise_postcode"), "Forename + surname + DOB + dwelling"),
    KeyDef("NAME_DOB_PREMISE_STREET", ("first_forms", "last_key", "dob", "premise_street"), "Forename + surname + DOB + dwelling by street and town"),
    KeyDef("NAME_DOB_POSTCODE", ("first_forms", "last_key", "dob", "postcode"), "Forename + surname + DOB + postcode"),
    KeyDef("EMAIL_MOBILE_FIRST", ("email", "mobile", "first_forms"), "Email + mobile + forename"),
    KeyDef("EMAIL_NAME", ("email", "first_forms", "last_key"), "Email + forename + surname"),
    KeyDef("MOBILE_NAME", ("mobile", "first_forms", "last_key"), "Mobile + forename + surname"),
    KeyDef("UPRN_NAME", ("uprn", "first_forms", "last_key"), "UPRN + forename + surname"),
    KeyDef("NAME_PREMISE_POSTCODE", ("first_forms", "last_key", "premise_postcode"), "Forename + surname + dwelling"),
    KeyDef("NAME_PREMISE_STREET", ("first_forms", "last_key", "premise_street"), "Forename + surname + dwelling by street and town"),
    KeyDef("INITIAL_DOB_PREMISE_POSTCODE", ("first_initial", "last_key", "dob", "premise_postcode"), "Initial + surname + DOB + dwelling"),
    KeyDef("SOUNDEX_NAME_DOB_POSTCODE", ("first_soundex", "last_soundex", "dob", "postcode"), "Soundex names + DOB + postcode (spelling errors)"),
    KeyDef("NAME_LASTSOUNDEX_PREMISE_POSTCODE", ("first_forms", "last_soundex", "premise_postcode"), "Forename + surname Soundex + dwelling (surname misspelt, no DOB)"),
    KeyDef("INITIAL_LAST_PREMISE_POSTCODE", ("first_initial", "last_key", "premise_postcode"), "Initial + surname + dwelling (forename misspelt, no DOB)"),
    KeyDef("NYSIIS_NAME_DOB_PREMISE_DISTRICT", ("first_nysiis", "last_nysiis", "dob", "premise_district"), "NYSIIS names + DOB + dwelling in district (spelling + postcode errors)"),
    KeyDef("EMAIL_FIRST", ("email", "first_forms"), "Email + forename (surname typo or change)"),
    KeyDef("MOBILE_FIRST", ("mobile", "first_forms"), "Mobile + forename (surname typo or change)"),
    KeyDef("EMAIL_LAST_INITIAL", ("email", "last_key", "first_initial"), "Email + surname + initial (forename typo)"),
    KeyDef("MOBILE_LAST_INITIAL", ("mobile", "last_key", "first_initial"), "Mobile + surname + initial (forename typo)"),
    KeyDef("NAME_DOBSWAP_POSTCODE", ("first_forms", "last_key", "dob_unordered", "postcode"), "Names + DOB with day/month transposed + postcode"),
    KeyDef("ALTSURNAME_DOB_PREMISE_POSTCODE", ("first_forms", "last_parts", "dob", "premise_postcode"), "One half of a double-barrelled surname + DOB + dwelling"),
    KeyDef("NAME_POSTCODE", ("first_forms", "last_key", "postcode"), "Forename + surname + postcode"),
    KeyDef("FIRST_DOB_PREMISE_POSTCODE", ("first_forms", "dob", "premise_postcode"), "Forename + DOB + dwelling (surname change)"),
    KeyDef("FIRST_DOB_EMAIL", ("first_forms", "dob", "email"), "Forename + DOB + email (surname change)"),
    KeyDef("FIRST_DOB_MOBILE", ("first_forms", "dob", "mobile"), "Forename + DOB + mobile (surname change)"),
    KeyDef("EMAIL_DOB", ("email", "dob"), "Email + DOB (forename must not conflict)"),
    KeyDef("MOBILE_DOB", ("mobile", "dob"), "Mobile + DOB (forename must not conflict)"),
]}

BLOCKING_KEYS: dict[str, KeyDef] = {k.name: k for k in [
    KeyDef("B_EMAIL", ("email",), "Shared email"),
    KeyDef("B_PHONE", ("phone",), "Shared phone"),
    KeyDef("B_UPRN", ("uprn",), "Shared UPRN"),
    KeyDef("B_POSTCODE", ("postcode",), "Shared full postcode"),
    KeyDef("B_PREMISE_STREET", ("premise_street",), "Same dwelling by street and town"),
    KeyDef("B_DOB_LAST_SOUNDEX", ("dob", "last_soundex"), "DOB + surname Soundex"),
    KeyDef("B_DOB_LAST_NYSIIS", ("dob", "last_nysiis"), "DOB + surname NYSIIS"),
]}

# When a key component is equal on two records, which blocking components are
# also guaranteed equal? Used to prove every match key is reachable by a block.
IMPLIES: dict[str, set[str]] = {
    "last_key": {"last_soundex", "last_nysiis"},
    "mobile": {"phone"},
    "premise_postcode": {"postcode", "district"},
    "premise_district": {"district"},
    "dob": {"dob_unordered"},
}


def implied(components: tuple[str, ...]) -> set[str]:
    out = set(components)
    for c in components:
        out |= IMPLIES.get(c, set())
    return out


# --------------------------------------------------------------------------- generation


def _digest(secret: bytes, key_type: str, values: tuple[str, ...]) -> bytes:
    material = "\x1f".join((key_type, *values)).encode()
    return hmac.new(secret, material, hashlib.sha256).digest()[:KEY_BYTES]


def build_keys(comps: Components, defs: dict[str, KeyDef], secret: str) -> KeySet:
    sec = secret.encode()
    out: KeySet = {}
    for d in defs.values():
        value_sets = [sorted(comps.get(c, ())) for c in d.components]
        if not all(value_sets):
            continue
        vals = set()
        for combo in itertools.islice(itertools.product(*value_sets), MAX_VALUES_PER_KEY):
            vals.add(_digest(sec, d.name, combo))
        out[d.name] = vals
    return out


def build_match_keys(comps: Components, secret: str) -> KeySet:
    return build_keys(comps, MATCH_KEYS, secret)


def build_blocking_keys(comps: Components, secret: str) -> KeySet:
    return build_keys(comps, BLOCKING_KEYS, secret)


# --------------------------------------------------------------------------- veto profile


def match_profile(rec: CleansedRecord) -> dict:
    """The attributes vetoes compare. Stored with each record so candidates
    can be evaluated without re-cleansing."""
    first_key = alpha_key(rec.name.first_name)
    return {
        "dob": rec.date_of_birth.isoformat() if rec.date_of_birth and rec.date_of_birth_usable else None,
        "dob_unordered": unordered_dob(rec.date_of_birth) if rec.date_of_birth and rec.date_of_birth_usable else None,
        # Explicit gender or title first; otherwise inferred from common UK forenames.
        "gender": rec.gender if rec.gender in ("M", "F") else gender_from_forename(first_key),
        "generation": rec.name.suffix,
        "first_forms": sorted(first_name_forms(first_key)),
        "first_key": first_key or None,
        "first_initial": first_key[:1] or None,
        "middle_initial": alpha_key(rec.name.middle_names)[:1] or None,
        "last_key": alpha_key(rec.name.last_name) or None,
        "postcodes": sorted({a.postcode.replace(" ", "") for a in [rec.address, *rec.previous_addresses]
                             if a and a.postcode and a.is_uk}),
    }

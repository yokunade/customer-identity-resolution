"""The ordered match rule hierarchy.

Rules are evaluated strongest first. A rule fires for a candidate when the
candidate shares the rule's match key with the inbound record and none of the
rule's vetoes apply. The inbound record links to the customer whose records
fired the highest-priority rule.

Vetoes stop shared identifiers from linking different people, which is
common in UK households: parents and children share landlines, addresses and
sometimes email; fathers and sons share names (Jr/Sr).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.matching.keys import MATCH_KEYS

Tier = Literal["exact", "strong", "probable", "possible"]
Veto = Literal["dob", "gender", "generation", "middle_initial", "first_name", "postcode"]
DobMode = Literal["exact", "lenient"]

DEFAULT_VETOES: frozenset[str] = frozenset({"dob", "gender", "generation"})


@dataclass(frozen=True)
class Rule:
    priority: int
    key: str
    tier: Tier
    vetoes: frozenset[str] = field(default=DEFAULT_VETOES)
    # "exact": DOBs conflict unless equal (day/month transposition tolerated).
    # "lenient": also tolerate a single wrong field (e.g. year keyed one off).
    # Lenient is only used where a strong identifier and a name already agree.
    dob_mode: DobMode = "exact"

    @property
    def name(self) -> str:
        return self.key


def _r(priority: int, key: str, tier: Tier, extra: set[str] | None = None, **kw) -> Rule:
    vetoes = DEFAULT_VETOES | frozenset(extra or ())
    if tier == "exact":
        # Name, DOB and a strong identifier all agree; a gender mismatch here is
        # far more likely to be a data error than a different person.
        vetoes -= {"gender"}
    return Rule(priority, key, tier, vetoes, **kw)


MI = {"middle_initial"}
FN = {"first_name"}
PC = {"postcode"}

RULES: tuple[Rule, ...] = (
    # Exact: a strong identifier (or the dwelling) plus full name and DOB.
    _r(10, "EMAIL_NAME_DOB", "exact"),
    _r(20, "PHONE_NAME_DOB", "exact"),
    _r(30, "UPRN_NAME_DOB", "exact"),
    _r(40, "NAME_DOB_PREMISE_POSTCODE", "exact"),
    _r(45, "NAME_DOB_PREMISE_STREET", "exact", PC),
    _r(50, "NAME_DOB_POSTCODE", "exact"),
    # Strong: a personal identifier (email / mobile) or DOB, plus name or name variants.
    _r(55, "EMAIL_MOBILE_FIRST", "strong", MI, dob_mode="lenient"),
    _r(60, "EMAIL_NAME", "strong", MI, dob_mode="lenient"),
    _r(70, "MOBILE_NAME", "strong", MI, dob_mode="lenient"),
    _r(100, "INITIAL_DOB_PREMISE_POSTCODE", "strong", FN),
    # Phonetic forenames must also be compatible (shared nickname form or one
    # keystroke apart): otherwise twins like Ella/Ellie or Jack/Jake merge.
    _r(110, "SOUNDEX_NAME_DOB_POSTCODE", "strong", FN),
    _r(120, "NYSIIS_NAME_DOB_PREMISE_DISTRICT", "strong", FN),
    _r(125, "EMAIL_FIRST", "strong", MI, dob_mode="lenient"),
    _r(126, "MOBILE_FIRST", "strong", MI, dob_mode="lenient"),
    _r(127, "EMAIL_LAST_INITIAL", "strong", MI | FN, dob_mode="lenient"),
    _r(128, "MOBILE_LAST_INITIAL", "strong", MI | FN, dob_mode="lenient"),
    _r(130, "NAME_DOBSWAP_POSTCODE", "strong"),
    _r(140, "ALTSURNAME_DOB_PREMISE_POSTCODE", "strong"),
    # Possible: name + address with no DOB or personal identifier. Same-named
    # relatives at one address (mother/daughter, father/son without Jr/Snr)
    # cannot be told apart, so these are the rules to send to review when
    # over-merging is costly (AUTO_LINK_TIERS=exact,strong,probable).
    _r(141, "UPRN_NAME", "possible", MI),
    _r(142, "NAME_PREMISE_POSTCODE", "possible", MI),
    _r(143, "NAME_PREMISE_STREET", "possible", MI | PC),
    _r(144, "NAME_LASTSOUNDEX_PREMISE_POSTCODE", "possible", MI),
    _r(146, "INITIAL_LAST_PREMISE_POSTCODE", "possible", MI | FN),
    _r(145, "NAME_POSTCODE", "possible", MI),
    # Probable: surname changes and shared identifiers without a surname, all
    # anchored on DOB.
    _r(150, "FIRST_DOB_PREMISE_POSTCODE", "probable"),
    _r(160, "FIRST_DOB_EMAIL", "probable"),
    _r(170, "FIRST_DOB_MOBILE", "probable"),
    _r(180, "EMAIL_DOB", "probable", FN),
    _r(190, "MOBILE_DOB", "probable", FN),
)

RULES_BY_KEY = {r.key: r for r in RULES}

assert all(r.key in MATCH_KEYS for r in RULES), "every rule needs a match key definition"
assert len({r.priority for r in RULES}) == len(RULES), "rule priorities must be unique"
assert len(RULES_BY_KEY) == len(RULES), "one rule per match key"


def veto_reasons(rule: Rule, a: dict, b: dict) -> list[str]:
    """Return the vetoes that block ``rule`` between profiles ``a`` and ``b``."""
    out: list[str] = []
    if "dob" in rule.vetoes and _dobs_conflict(a.get("dob"), b.get("dob"), rule.dob_mode):
        out.append("dob")
    if "gender" in rule.vetoes and a.get("gender") and b.get("gender") and a["gender"] != b["gender"]:
        out.append("gender")
    if "generation" in rule.vetoes and a.get("generation") and b.get("generation") \
            and a["generation"] != b["generation"]:
        out.append("generation")
    if "middle_initial" in rule.vetoes and a.get("middle_initial") and b.get("middle_initial") \
            and a["middle_initial"] != b["middle_initial"]:
        out.append("middle_initial")
    if "first_name" in rule.vetoes and first_names_conflict(a, b):
        out.append("first_name")
    if "postcode" in rule.vetoes and a.get("postcodes") and b.get("postcodes") \
            and not set(a["postcodes"]) & set(b["postcodes"]):
        out.append("postcode")  # same street name in a different part of town
    return out


def _dobs_conflict(a: str | None, b: str | None, mode: DobMode) -> bool:
    if not a or not b or a == b:
        return False
    ya, ma, da = a.split("-")
    yb, mb, db = b.split("-")
    if ya == yb and ma == db and da == mb:
        return False  # day and month transposed
    if mode == "lenient":
        differing = (ya != yb) + (ma != mb) + (da != db)
        return differing > 1
    return True


def damerau(a: str, b: str) -> int:
    """Optimal string alignment distance (insert, delete, substitute, transpose)."""
    if abs(len(a) - len(b)) > 2:
        return 3
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def first_names_conflict(a: dict, b: dict) -> bool:
    """Forenames conflict unless they share a form (nickname table) or are one
    keystroke apart (two for names of 8+ letters). Sounding alike is not enough:
    John, Jenny, Jean and Jim share a Soundex code."""
    fa, fb = set(a.get("first_forms") or ()), set(b.get("first_forms") or ())
    if fa and fb:
        if fa & fb:
            return False
        ka, kb = a.get("first_key") or "", b.get("first_key") or ""
        if ka and kb:
            limit = 2 if min(len(ka), len(kb)) >= 8 else 1
            if damerau(ka, kb) <= limit:
                return False
        return True
    ia, ib = a.get("first_initial"), b.get("first_initial")
    return bool(ia and ib and ia != ib)

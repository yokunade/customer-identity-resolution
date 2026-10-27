"""Personal name parsing and standardisation (UK conventions)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.cleansing.text import alpha_key, clean_text
from app.schemas import Issue

TITLES: dict[str, str] = {
    "MR": "Mr", "MISTER": "Mr", "MRS": "Mrs", "MISSUS": "Mrs", "MISS": "Miss", "MS": "Ms",
    "MX": "Mx", "DR": "Dr", "DOCTOR": "Dr", "PROF": "Prof", "PROFESSOR": "Prof",
    "REV": "Rev", "REVD": "Rev", "REVEREND": "Rev", "FR": "Fr", "FATHER": "Fr",
    "SIR": "Sir", "DAME": "Dame", "LORD": "Lord", "LADY": "Lady", "MASTER": "Master",
    "CAPT": "Capt", "CAPTAIN": "Capt", "MAJ": "Major", "MAJOR": "Major", "COL": "Col",
    "COLONEL": "Col", "LT": "Lt", "LIEUT": "Lt", "SGT": "Sgt", "CLLR": "Cllr", "HON": "Hon",
    "RT": "Rt", "RABBI": "Rabbi", "IMAM": "Imam", "SISTER": "Sister", "BROTHER": "Brother",
}
TITLE_GENDER: dict[str, str] = {
    "Mr": "M", "Master": "M", "Sir": "M", "Lord": "M", "Fr": "M", "Brother": "M",
    "Mrs": "F", "Miss": "F", "Ms": "F", "Dame": "F", "Lady": "F", "Sister": "F",
}
GENERATIONS: dict[str, str] = {
    "JR": "Jr", "JNR": "Jr", "JUNIOR": "Jr", "SR": "Sr", "SNR": "Sr", "SENIOR": "Sr",
    "II": "II", "III": "III", "IV": "IV",
}
POSTNOMINALS = {
    "OBE", "MBE", "CBE", "KBE", "DBE", "BEM", "CH", "KG", "KT", "QC", "KC", "MP", "MSP", "MEP",
    "JP", "DL", "PHD", "DPHIL", "MD", "MA", "MSC", "BSC", "BA", "BENG", "MENG", "LLB", "LLM",
    "FRCS", "FRCP", "MRCP", "MRCS", "FCA", "ACA", "ACCA", "CIMA", "FRS", "RN", "RGN", "ESQ",
    "DDS", "BDS", "MBA", "CENG", "MICE", "FICE",
}
PARTICLES = {
    "VAN", "VON", "DE", "DA", "DI", "DEL", "DELLA", "DER", "DEN", "DU", "LA", "LE", "DOS",
    "DAS", "AL", "EL", "BIN", "BINT", "AP", "ST", "TER", "TEN",
}
JUNK_NAMES = {
    "TEST", "TESTER", "TESTING", "DUMMY", "CUSTOMER", "CLIENT", "UNKNOWN", "ANON",
    "ANONYMOUS", "NONAME", "NO NAME", "SAMPLE", "FAKE", "ASDF", "QWERTY", "XXX", "ABC",
}
ORG_MARKERS = re.compile(r"\b(LTD|LIMITED|PLC|LLP|INC|CORP|COMPANY|CO|TRUST|COUNCIL|SERVICES)\b")

_TOKEN = re.compile(r"[^\s,]+")


@dataclass
class ParsedName:
    title: str | None = None
    first_name: str | None = None
    middle_names: str | None = None
    last_name: str | None = None
    suffix: str | None = None
    title_gender: str | None = None
    issues: list[Issue] = field(default_factory=list)

    # ---- key material
    @property
    def first_key(self) -> str:
        return alpha_key(self.first_name)

    @property
    def first_initial(self) -> str:
        k = self.first_key
        return k[:1]

    @property
    def middle_initial(self) -> str:
        return alpha_key(self.middle_names)[:1]

    @property
    def last_key(self) -> str:
        return alpha_key(self.last_name)


def _tokens(value: str | None) -> list[str]:
    if not value:
        return []
    return [t.strip(".") for t in _TOKEN.findall(value) if t.strip(".")]


def proper_case(value: str | None) -> str | None:
    """Capitalise a name the way it is usually written in the UK: O'Brien,
    McDonald, Smith-Jones. Mixed-case input is assumed deliberate and kept."""
    if not value:
        return value
    if value != value.upper() and value != value.lower():
        return value

    def word(w: str) -> str:
        out = []
        for part in w.split("-"):
            p = part.lower()
            if re.match(r"^[od]'[a-z]", p):
                out.append(p[0].upper() + "'" + p[2:].capitalize())
            elif p.startswith("mc") and len(p) > 2:
                out.append("Mc" + p[2:].capitalize())
            else:
                out.append(p.capitalize())
        return "-".join(out)

    return " ".join(word(w) for w in value.split())


def _take_titles(tokens: list[str]) -> tuple[list[str], list[str]]:
    titles: list[str] = []
    while tokens and tokens[0].upper() in TITLES and len(tokens) > 1:
        titles.append(TITLES[tokens.pop(0).upper()])
    return titles, tokens


def _take_suffixes(tokens: list[str]) -> tuple[str | None, list[str]]:
    generation = None
    while tokens and len(tokens) > 1:
        up = tokens[-1].upper()
        if up in GENERATIONS:
            generation = generation or GENERATIONS[up]
            tokens.pop()
        elif up in POSTNOMINALS:
            tokens.pop()
        else:
            break
    return generation, tokens


def _split_surname(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Split given names from surname, keeping particles with the surname."""
    if len(tokens) == 1:
        return [], tokens
    i = len(tokens) - 1
    while i > 1 and tokens[i - 1].upper() in PARTICLES:
        i -= 1
    return tokens[:i], tokens[i:]


def parse_name(
    *,
    title: str | None,
    first_name: str | None,
    middle_names: str | None,
    last_name: str | None,
    full_name: str | None,
    suffix: str | None,
) -> ParsedName:
    title, first_name, middle_names = clean_text(title), clean_text(first_name), clean_text(middle_names)
    last_name, full_name, suffix = clean_text(last_name), clean_text(full_name), clean_text(suffix)
    out = ParsedName()
    titles: list[str] = []
    generation: str | None = None

    if title:
        for t in _tokens(title):
            if t.upper() in TITLES:
                titles.append(TITLES[t.upper()])
        if not titles:
            out.issues.append(Issue(field="title", code="unrecognised_title", severity="info", detail=title))

    if suffix:
        up = suffix.upper().strip(".")
        if up in GENERATIONS:
            generation = GENERATIONS[up]

    given: list[str] = []
    surname: list[str] = []

    if not (first_name or last_name) and full_name:
        if "," in full_name:  # "Smith, John Paul"
            last_part, _, rest = full_name.partition(",")
            t, rest_tokens = _take_titles(_tokens(rest))
            titles += t
            g, rest_tokens = _take_suffixes(rest_tokens)
            generation = generation or g
            given, surname = rest_tokens, _tokens(last_part)
        else:
            t, toks = _take_titles(_tokens(full_name))
            titles += t
            g, toks = _take_suffixes(toks)
            generation = generation or g
            given, surname = _split_surname(toks)
    else:
        given = _tokens(first_name)
        if given and not titles:
            t, given = _take_titles(given) if len(given) > 1 else ([], given)
            titles += t
        surname = _tokens(last_name)
        if surname:
            g, surname = _take_suffixes(surname)
            generation = generation or g

    # Middle names: explicit field wins; extra given-name tokens are prepended.
    middle = _tokens(middle_names)
    if len(given) > 1:
        middle = given[1:] + middle
        given = given[:1]

    out.title = titles[0] if titles else None
    out.title_gender = next((TITLE_GENDER[t] for t in titles if t in TITLE_GENDER), None)
    out.first_name = proper_case(given[0]) if given else None
    out.middle_names = proper_case(" ".join(middle)) if middle else None
    out.last_name = proper_case(" ".join(surname)) if surname else None
    out.suffix = generation

    # ---- quality checks
    if out.first_name and len(out.first_key) == 1:
        out.issues.append(Issue(field="first_name", code="initial_only", severity="info"))
    if not out.last_name:
        out.issues.append(Issue(field="last_name", code="missing", severity="warning"))
    if not out.first_name:
        out.issues.append(Issue(field="first_name", code="missing", severity="warning"))
    for fld in ("first_name", "last_name"):
        v = getattr(out, fld)
        if not v:
            continue
        if re.search(r"\d", v):
            out.issues.append(Issue(field=fld, code="contains_digits", severity="warning"))
        if v.upper() in JUNK_NAMES:
            out.issues.append(Issue(field=fld, code="placeholder_name", severity="error"))
            setattr(out, fld, None)
    joined = " ".join(filter(None, [out.first_name, out.last_name])).upper()
    if ORG_MARKERS.search(joined):
        out.issues.append(Issue(field="name", code="looks_like_organisation", severity="warning"))
    return out

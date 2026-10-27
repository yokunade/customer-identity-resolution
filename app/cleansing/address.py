"""UK postal address standardisation.

Output follows Royal Mail addressing conventions: upper case, no punctuation,
full thoroughfare descriptors (ROAD, not RD), post town on its own line,
county optional, postcode validated and formatted.

The parser also extracts the premise (sub-building + building number/name),
which match keys combine with the postcode to identify a dwelling.
"""

from __future__ import annotations

import re

from app.cleansing.postcode import compact_postcode, find_postcode_in_text, parse_postcode
from app.cleansing.text import alnum_key, clean_text
from app.schemas import CleansedAddress, InboundAddress, Issue

UK_COUNTRIES = {
    "GB", "UK", "U K", "GBR", "UNITED KINGDOM", "GREAT BRITAIN", "BRITAIN", "ENGLAND",
    "SCOTLAND", "WALES", "CYMRU", "NORTHERN IRELAND", "NI", "N IRELAND", "UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND",
}
CROWN_DEPENDENCIES = {"JE": "JE", "JERSEY": "JE", "GY": "GY", "GUERNSEY": "GY", "IM": "IM", "ISLE OF MAN": "IM"}

ABBREVIATIONS = {
    "RD": "ROAD", "ST": "STREET", "STR": "STREET", "AVE": "AVENUE", "AV": "AVENUE", "AVN": "AVENUE",
    "LN": "LANE", "CL": "CLOSE", "CRES": "CRESCENT", "CRS": "CRESCENT", "CRESC": "CRESCENT",
    "DR": "DRIVE", "DRV": "DRIVE", "GDNS": "GARDENS", "GDN": "GARDEN", "GRN": "GREEN",
    "GR": "GROVE", "GRV": "GROVE", "GRO": "GROVE", "PL": "PLACE", "SQ": "SQUARE", "TER": "TERRACE",
    "TERR": "TERRACE", "TCE": "TERRACE", "CT": "COURT", "CRT": "COURT", "PK": "PARK",
    "HSE": "HOUSE", "HO": "HOUSE", "BLDG": "BUILDING", "BLDGS": "BUILDINGS", "MWS": "MEWS",
    "WY": "WAY", "PDE": "PARADE", "HTS": "HEIGHTS", "HL": "HILL", "BVD": "BOULEVARD",
    "BLVD": "BOULEVARD", "CTGS": "COTTAGES", "COTT": "COTTAGE", "COTTS": "COTTAGES",
    "ESP": "ESPLANADE", "EST": "ESTATE", "FM": "FARM", "GT": "GREAT", "LT": "LITTLE",
    "UPR": "UPPER", "LWR": "LOWER", "NTH": "NORTH", "STH": "SOUTH", "WK": "WALK", "VW": "VIEW",
    "APTS": "APARTMENTS", "MT": "MOUNT", "RDBT": "ROUNDABOUT", "CNR": "CORNER", "WHF": "WHARF",
    "IND": "INDUSTRIAL", "CTR": "CENTRE", "CENTER": "CENTRE",
}
DESCRIPTORS = {
    "ROAD", "STREET", "AVENUE", "LANE", "CLOSE", "CRESCENT", "DRIVE", "GARDENS", "GREEN",
    "GROVE", "PLACE", "SQUARE", "TERRACE", "COURT", "PARK", "WAY", "WALK", "HILL", "RISE",
    "ROW", "MEWS", "PARADE", "VIEW", "HEIGHTS", "BOULEVARD", "ESPLANADE", "GATE", "WYND",
    "BRAE", "LOAN", "VALE", "DALE", "END", "FIELDS", "MEADOWS", "CHASE", "BANK", "QUAY",
    "WHARF", "PROMENADE", "BROADWAY", "CIRCUS", "YARD", "PASSAGE", "ALLEY", "PATH",
}
SUB_BUILDING_TYPES = {
    "FLAT": "FLAT", "FLT": "FLAT", "APARTMENT": "FLAT", "APT": "FLAT", "STUDIO": "FLAT",
    "MAISONETTE": "FLAT", "UNIT": "UNIT", "SUITE": "SUITE", "ROOM": "ROOM", "RM": "ROOM",
}
_SUB_RE = re.compile(
    r"\b(" + "|".join(SUB_BUILDING_TYPES) + r")\s*(?:NO\s*)?([0-9]+[A-Z]?|[A-Z]|[0-9]+/[0-9]+)\b"
)
_FLOOR_RE = re.compile(
    r"\b(BASEMENT|LOWER GROUND|GROUND|FIRST|1ST|SECOND|2ND|THIRD|3RD|FOURTH|4TH|TOP|UPPER)\s+FLOOR(?:\s+FLAT)?\b"
)
_FLOOR_WORDS = {"1ST": "FIRST", "2ND": "SECOND", "3RD": "THIRD", "4TH": "FOURTH"}
_NUMBER_RE = re.compile(r"^([0-9]+[A-Z]?(?:-[0-9]+[A-Z]?)?)\s+(.+)$")


def _normalise_line(line: str) -> str:
    s = line.upper().replace("'", "")                     # ST JOHN'S -> ST JOHNS
    s = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "-", s)        # 12 - 14 -> 12-14
    s = re.sub(r"(?<=\d)\s+([A-Z])\b(?=\s)", r"\1", s)  # 12 A HIGH ST -> 12A HIGH ST
    s = re.sub(r"[^A-Z0-9/\- ]", " ", s)                # drop punctuation
    s = re.sub(r"(?<![0-9])[-/]|[-/](?![0-9])", " ", s)  # keep - and / only between digits
    tokens = s.split()
    out: list[str] = []
    for i, t in enumerate(tokens):
        if t == "ST":
            # "ST JOHNS ROAD" -> SAINT; "HIGH ST" -> STREET
            out.append("SAINT" if i + 1 < len(tokens) and tokens[i + 1] not in ABBREVIATIONS else "STREET")
        elif t in ABBREVIATIONS:
            out.append(ABBREVIATIONS[t])
        else:
            out.append(t)
    return " ".join(out)


def _ends_with_descriptor(line: str) -> bool:
    words = line.split()
    return bool(words) and words[-1] in DESCRIPTORS


def normalise_country(raw: str | None) -> str:
    s = (clean_text(raw) or "").upper().replace(".", "").strip()
    if not s or s in UK_COUNTRIES:
        return "GB"
    if s in CROWN_DEPENDENCIES:
        return CROWN_DEPENDENCIES[s]
    return s[:2] if len(s) == 2 else s


def cleanse_address(addr: InboundAddress | None, field: str = "address") -> tuple[CleansedAddress | None, list[Issue]]:
    issues: list[Issue] = []
    if addr is None:
        return None, issues

    raw_lines = [addr.line1, addr.line2, addr.line3, addr.line4, *(addr.lines or [])]
    lines = [_normalise_line(t) for t in (clean_text(x) for x in raw_lines) if t]
    lines = [ln for ln in lines if ln]
    town = _normalise_line(clean_text(addr.town) or "") or None
    county = _normalise_line(clean_text(addr.county) or "") or None
    country = normalise_country(addr.country)
    is_uk = country in {"GB", "JE", "GY", "IM"}

    # ---- postcode
    pc_raw = clean_text(addr.postcode)
    pc = parse_postcode(pc_raw) if is_uk else None
    if is_uk and pc is None:
        for ln in lines:  # postcode typed into an address line
            pc = find_postcode_in_text(ln)
            if pc:
                issues.append(Issue(field=f"{field}.postcode", code="postcode_found_in_lines", severity="info"))
                break
    if is_uk:
        if pc is None:
            issues.append(Issue(
                field=f"{field}.postcode", code="missing" if not pc_raw else "invalid_uk_postcode",
                severity="warning", detail=pc_raw,
            ))
        elif pc.corrected:
            issues.append(Issue(field=f"{field}.postcode", code="postcode_corrected", severity="info",
                                detail=f"{pc_raw} -> {pc.full}"))

    # Strip postcode, town and county text out of the lines.
    def strip_line(ln: str) -> str:
        if pc:
            ln = re.sub(rf"\b{pc.outward}\s*{pc.inward}\b", " ", ln)
        for value in (town, county):
            if value and ln.endswith(" " + value):
                ln = ln[: -len(value)]
        return " ".join(ln.split())

    lines = [strip_line(ln) for ln in lines]
    lines = [ln for ln in lines if ln and ln not in {town, county}]
    if not town and len(lines) > 1 and not re.search(r"\d", lines[-1]) and not _ends_with_descriptor(lines[-1]):
        town = lines.pop()  # last line is very often the town
    if county and county == town:
        county = None

    # ---- premise parsing
    sub_building: str | None = None
    building_number: str | None = None
    building_name: str | None = None
    street: str | None = None
    localities: list[str] = []

    work = list(lines)
    for i, ln in enumerate(work):
        m = _SUB_RE.search(ln) or None
        if m:
            sub_building = f"{SUB_BUILDING_TYPES[m.group(1)]} {m.group(2)}"
            work[i] = " ".join((ln[: m.start()] + " " + ln[m.end():]).split())
            break
        f = _FLOOR_RE.search(ln)
        if f:
            word = _FLOOR_WORDS.get(f.group(1), f.group(1))
            sub_building = f"{word} FLOOR FLAT"
            work[i] = " ".join((ln[: f.start()] + " " + ln[f.end():]).split())
            break
    work = [ln for ln in work if ln]

    street_idx = None
    numbered = [
        (i, m) for i, ln in enumerate(work)
        if (m := _NUMBER_RE.match(ln)) and not re.fullmatch(r"[0-9]+", m.group(2))
    ]
    if numbered:
        # With "3 ROSE COURT, 12 HIGH STREET" the last numbered line is the
        # thoroughfare; earlier ones are handled as named buildings below.
        street_idx, m = numbered[-1] if len(numbered) > 1 and _ends_with_descriptor(numbered[-1][1].group(2)) \
            else numbered[0]
        building_number, street = m.group(1), m.group(2)
    if street_idx is None:
        for i, ln in enumerate(work):
            if _ends_with_descriptor(ln) and not re.search(r"\d", ln):
                street, street_idx = ln, i
                break
    if street_idx is not None:
        before = [ln for ln in work[:street_idx] if not re.fullmatch(r"[0-9]+[A-Z]?", ln)]
        if before:
            building_name = before[-1]
        # A bare number on its own line before the street is the building number.
        for ln in work[:street_idx]:
            if re.fullmatch(r"[0-9]+[A-Z]?", ln) and not building_number:
                building_number = ln
        localities = work[street_idx + 1:]
    elif work:
        building_name, localities = work[0], work[1:]

    if sub_building is None and building_name and building_number:
        # "3 ROSE COURT, 12 HIGH STREET": leading number on the name line is the flat.
        m = re.match(r"^([0-9]+[A-Z]?)\s+(.+)$", building_name)
        if m:
            sub_building, building_name = f"FLAT {m.group(1)}", m.group(2)

    uprn = None
    if addr.uprn is not None:
        digits = re.sub(r"\D", "", str(addr.uprn)).lstrip("0")
        if 1 <= len(digits) <= 12:
            uprn = digits
        else:
            issues.append(Issue(field=f"{field}.uprn", code="invalid_uprn", detail=str(addr.uprn)))

    premise_line = " ".join(filter(None, [building_number, street])) or None
    formatted = [x for x in [sub_building, building_name, premise_line, *localities, town] if x]
    if pc:
        formatted.append(pc.full)
    elif pc_raw and not is_uk:
        formatted.append(pc_raw.upper())

    if not (building_number or building_name):
        issues.append(Issue(field=field, code="no_premise_identified", severity="info"))
    if not town and is_uk:
        issues.append(Issue(field=f"{field}.town", code="missing", severity="info"))

    out = CleansedAddress(
        lines=formatted,
        sub_building=sub_building,
        building_name=building_name,
        building_number=building_number,
        street=street,
        localities=localities,
        post_town=town,
        county=county,
        postcode=pc.full if pc else (compact_postcode(pc_raw) or None if not is_uk else None),
        postcode_outward=pc.outward if pc else None,
        postcode_inward=pc.inward if pc else None,
        postcode_sector=pc.sector if pc else None,
        postcode_district=pc.district if pc else None,
        postcode_area=pc.area if pc else None,
        country=country,
        is_uk=is_uk,
        uprn=uprn,
    )
    if not (out.lines or out.postcode or out.uprn):
        return None, issues
    return out, issues


def premise_keys(addr: CleansedAddress) -> set[str]:
    """Identifiers for the dwelling within its postcode. A building can be
    recorded by number, by name, or both, so each available form is emitted."""
    sub = alnum_key(addr.sub_building)
    out: set[str] = set()
    for ident in (addr.building_number, addr.building_name):
        k = alnum_key(ident)
        if k:
            out.add(f"{sub}|{k}")
    return out

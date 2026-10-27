"""Cleansing pipeline: turns any inbound record into a UK-standardised record."""

from __future__ import annotations

import datetime as dt

from app.cleansing.address import cleanse_address
from app.cleansing.dob import parse_dob
from app.cleansing.email import cleanse_email
from app.cleansing.names import parse_name
from app.cleansing.phone import cleanse_phone
from app.cleansing.text import clean_text
from app.schemas import CleansedName, CleansedRecord, InboundCustomer, Issue

_GENDERS = {
    "M": "M", "MALE": "M", "MAN": "M", "BOY": "M",
    "F": "F", "FEMALE": "F", "WOMAN": "F", "GIRL": "F",
    "X": "X", "NB": "X", "NON-BINARY": "X", "NONBINARY": "X", "OTHER": "X",
}


def cleanse(rec: InboundCustomer, today: dt.date | None = None) -> tuple[CleansedRecord, list[Issue]]:
    issues: list[Issue] = []

    name = parse_name(
        title=rec.title, first_name=rec.first_name, middle_names=rec.middle_names,
        last_name=rec.last_name, full_name=rec.full_name, suffix=rec.suffix,
    )
    issues += name.issues

    g_raw = (clean_text(rec.gender) or "").upper()
    gender = _GENDERS.get(g_raw)
    if g_raw and gender is None:
        issues.append(Issue(field="gender", code="unrecognised_gender", severity="info", detail=rec.gender))
    if gender is None:
        gender = name.title_gender
    elif name.title_gender and gender in ("M", "F") and gender != name.title_gender:
        issues.append(Issue(field="gender", code="conflicts_with_title", severity="info"))

    dob, dob_usable, dob_issues = parse_dob(rec.date_of_birth, today)
    issues += dob_issues

    emails = []
    seen_e: set[str] = set()
    for i, raw in enumerate([rec.email, *rec.emails]):
        e, iss = cleanse_email(raw, field="email" if i == 0 else f"emails[{i - 1}]")
        issues += iss
        if e and e.canonical not in seen_e:
            seen_e.add(e.canonical)
            emails.append(e)

    phones = []
    seen_p: set[str] = set()
    labelled = [("phone", rec.phone), ("mobile_phone", rec.mobile_phone), ("home_phone", rec.home_phone),
                ("work_phone", rec.work_phone)] + [(f"phones[{i}]", p) for i, p in enumerate(rec.phones)]
    for fld, raw in labelled:
        p, iss = cleanse_phone(raw, field=fld)
        issues += iss
        if p and p.e164 not in seen_p:
            if fld == "work_phone" and p.type != "mobile":
                p.usable_for_matching = False  # office switchboards are shared
            seen_p.add(p.e164)
            phones.append(p)

    address, iss = cleanse_address(rec.address, "address")
    issues += iss
    previous = []
    for i, a in enumerate(rec.previous_addresses):
        pa, iss = cleanse_address(a, f"previous_addresses[{i}]")
        issues += iss
        if pa:
            previous.append(pa)

    cleansed = CleansedRecord(
        source_system=rec.source_system.strip().upper(),
        source_record_id=rec.source_record_id.strip(),
        name=CleansedName(
            title=name.title, first_name=name.first_name, middle_names=name.middle_names,
            last_name=name.last_name, suffix=name.suffix,
        ),
        gender=gender,  # type: ignore[arg-type]
        date_of_birth=dob,
        date_of_birth_usable=dob_usable,
        emails=emails,
        phones=phones,
        address=address,
        previous_addresses=previous,
    )
    return cleansed, issues

"""Curated review cases for the demo.

Each scenario is a short sequence of source records. Loading them through the
real resolution service raises genuine review cases: the rule, tier and
comparison a reviewer sees are exactly what the engine produced. Every record
is tagged with the real person it belongs to, so each case has a known
answer, and each scenario carries a one-line explanation for the demo's
answer reveal and for evaluating an AI reviewer on hard cases.

The demo runs with AUTO_LINK_TIERS=exact,strong,probable, so cases come from
two places:
  - a record whose best match is in the possible tier (name + address only),
  - a record that ties between two customers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scenario:
    key: str
    title: str
    truth: str  # what the reviewer should conclude, in words
    explanation: str
    records: list[tuple[str, dict]]  # (person label, inbound record)
    expected_cases: int = 1


_n = 0


def rec(system: str, **fields) -> dict:
    global _n
    _n += 1
    return {"source_system": system, "source_record_id": f"SC-{_n:04d}", **fields}


def addr(line1: str, town: str | None = None, postcode: str | None = None, **more) -> dict:
    return {k: v for k, v in {"line1": line1, "town": town, "postcode": postcode, **more}.items() if v}


SCENARIOS: list[Scenario] = [
    # ------------------------------------------------------------ same person
    Scenario(
        "nickname_no_dob", "Nickname on a billing record", "Same person",
        "Liz is short for Elizabeth, it is the same house, and nothing conflicts. Billing systems often hold no date of birth.",
        [
            ("P1", rec("CRM", title="Mrs", first_name="Elizabeth", middle_names="Anne", last_name="Hartley",
                       date_of_birth="14/03/1958", email="liz.hartley58@btinternet.com", mobile_phone="07723 418902",
                       address=addr("14 Rowan Close", "Harrogate", "HG2 7JB"))),
            ("P1", rec("LOYALTY", first_name="Elizabeth", last_name="Hartley", date_of_birth="1958-03-14",
                       email="liz.hartley58@btinternet.com", address=addr("14 Rowan Close", "Harrogate", "HG2 7JB"))),
            ("P1", rec("BILLING", full_name="HARTLEY, LIZ", home_phone="01423 567210",
                       address=addr("14 ROWAN CL", "HARROGATE", "HG27JB"))),
        ],
    ),
    Scenario(
        "surname_typo", "Surname misspelt by the call centre", "Same person",
        "Pritchad is a one-letter slip for Pritchard; same forename, same house, no conflicting details.",
        [
            ("P1", rec("CRM", title="Mr", first_name="Gareth", last_name="Pritchard", date_of_birth="02/11/1979",
                       mobile_phone="07811 234567", address=addr("3 Heol Llanishen Fach", "Cardiff", "CF14 6RQ"))),
            ("P1", rec("CALLCENTRE", first_name="Gareth", last_name="Pritchad", home_phone="029 2075 1123",
                       address=addr("3 Heol Llanishen Fach", "Cardiff", "CF14 6RQ"))),
        ],
    ),
    Scenario(
        "forename_typo", "Forename misspelt at checkout", "Same person",
        "Shiobhan is a misspelling of Siobhan at the same flat. A second email address is normal.",
        [
            ("P1", rec("CRM", first_name="Siobhan", last_name="Doherty", date_of_birth="21/06/1990",
                       email="s.doherty90@gmail.com", address=addr("Flat 2, 41 Ormeau Road", "Belfast", "BT7 1EB"))),
            ("P1", rec("ECOMMERCE", full_name="Shiobhan Doherty", email="shiobhan.d@outlook.com",
                       address={"lines": ["Flat 2, 41 Ormeau Rd", "Belfast BT7 1EB"]})),
        ],
    ),
    Scenario(
        "no_postcode", "New record has no postcode", "Same person",
        "Same name, house number, street and town; only the postcode is missing.",
        [
            ("P1", rec("CRM", title="Mr", first_name="Rajesh", last_name="Patel", date_of_birth="30/08/1972",
                       mobile_phone="07932 556104", address=addr("27 Melton Road", "Leicester", "LE4 6PN"))),
            ("P1", rec("CALLCENTRE", first_name="RAJESH", last_name="PATEL", address=addr("27 Melton Rd", "Leicester"))),
        ],
    ),
    Scenario(
        "flat_left_off", "Flat number left off", "Same person",
        "Billing recorded the building, not the flat. Same name and postcode, and an unusual name.",
        [
            ("P1", rec("CRM", title="Mr", first_name="Tomasz", last_name="Nowak", date_of_birth="17/01/1986",
                       email="tomasz.nowak86@gmail.com",
                       address={"line1": "Flat 4 Kingsley Court", "line2": "9 Albion Street", "town": "Leeds",
                                "postcode": "LS1 6HX"})),
            ("P1", rec("BILLING", full_name="NOWAK, TOMASZ", address=addr("9 ALBION ST", "LEEDS", "LS1 6HX"))),
        ],
    ),
    Scenario(
        "uprn_named_house", "Same property reference, house name abbreviated", "Same person",
        "Both records carry the same UPRN; ROSE COTT is Rose Cottage.",
        [
            ("P1", rec("CRM", title="Ms", first_name="Fiona", last_name="MacLeod", date_of_birth="09/12/1968",
                       email="fiona.macleod@outlook.com",
                       address={"line1": "Rose Cottage", "line2": "Main Street", "line3": "Kirkmichael",
                                "town": "Pitlochry", "postcode": "PH10 7NA", "uprn": "906700123456"})),
            ("P1", rec("BILLING", full_name="MACLEOD, FIONA",
                       address={"line1": "ROSE COTT", "line2": "MAIN ST, KIRKMICHAEL", "postcode": "PH10 7NA",
                                "uprn": "906700123456"})),
        ],
    ),
    Scenario(
        "moved_with_previous", "Moved house, old address given", "Same person",
        "The new record lists the old Stanley Road address as previous; an uncommon full name at the same dwelling.",
        [
            ("P1", rec("CRM", title="Mr", first_name="Chukwuemeka", last_name="Okafor", date_of_birth="05/04/1983",
                       mobile_phone="07863 120456", address=addr("58 Stanley Road", "Liverpool", "L5 2QE"))),
            ("P1", rec("ECOMMERCE", full_name="Chukwuemeka Okafor", email="emeka.okafor@icloud.com",
                       address=addr("Flat 12, Quayside, 3 Wapping Quay", "Liverpool", "L1 8DQ"),
                       previous_addresses=[addr("58 Stanley Rd", "Liverpool", "L5 2QE")])),
        ],
    ),
    Scenario(
        "double_barrel_spacing", "Double-barrelled surname without the hyphen", "Same person",
        "Lloyd Davies and Lloyd-Davies are the same surname written two ways, at the same address.",
        [
            ("P1", rec("CRM", title="Ms", first_name="Anna", last_name="Lloyd-Davies", date_of_birth="28/02/1994",
                       email="anna.lloyddavies@gmail.com", address=addr("22 Cathedral Road", "Cardiff", "CF11 9LJ"))),
            ("P1", rec("LOYALTY", title="Ms", first_name="Anna", last_name="Lloyd Davies",
                       address=addr("22 Cathedral Rd", "Cardiff", "CF11 9LJ"))),
        ],
    ),
    # ------------------------------------------------------------ ties and different people
    Scenario(
        "father_son_no_suffix", "Father and son, Jr/Sr missing", "Same person as the son; not the father",
        "Two Robert Clarkes live here, born 1961 and 1993. The new record has no DOB or suffix, but its email "
        "ends in 93, so it is the son: same person as Robert Clarke Jr, a different person from his father.",
        [
            ("SR", rec("CRM", title="Mr", first_name="Robert", last_name="Clarke", suffix="Snr",
                       date_of_birth="19/07/1961", email="rob.clarke61@sky.com",
                       address=addr("7 Beech Grove", "Stockport", "SK4 3HT"))),
            ("JR", rec("ECOMMERCE", full_name="Robert Clarke Jnr", date_of_birth="1993-10-02",
                       mobile_phone="07956 781234", address=addr("7 Beech Grove", "Stockport", "SK4 3HT"))),
            ("JR", rec("CALLCENTRE", title="Mr", first_name="Robert", last_name="Clarke",
                       email="robert.clarke93@outlook.com", address=addr("7 Beech Grove", "Stockport", "SK4 3HT"))),
        ],
        expected_cases=2,
    ),
    Scenario(
        "mother_daughter_family_email", "Mother and daughter share a name and a family email", "Different people",
        "Both are Margaret Doyle at one address with one family email, but born 1955 and 1987.",
        [
            ("MUM", rec("CRM", title="Mrs", first_name="Margaret", last_name="Doyle", date_of_birth="23/01/1955",
                        email="thedoyles@btinternet.com", address=addr("31 Willow Way", "Bristol", "BS16 2QR"))),
            ("DAUGHTER", rec("LOYALTY", title="Miss", first_name="Margaret", last_name="Doyle",
                             date_of_birth="14/09/1987", email="thedoyles@btinternet.com",
                             mobile_phone="07834 219876", address=addr("31 Willow Way", "Bristol", "BS16 2QR"))),
            ("MUM", rec("MARKETING", first_name="Margaret", last_name="Doyle", email="thedoyles@btinternet.com",
                        address={"postcode": "BS16 2QR"})),
        ],
    ),
    Scenario(
        "twins", "Twins at the same address", "Different people",
        "Same surname, date of birth and address, but Oliver and Owen are different forenames: twins.",
        [
            ("OLIVER", rec("ECOMMERCE", full_name="Oliver Grant", date_of_birth="2004-05-11",
                           mobile_phone="07412 665098", address=addr("16 Linden Avenue", "Nottingham", "NG11 6AB"))),
            ("OWEN", rec("CRM", title="Mr", first_name="Owen", last_name="Grant", date_of_birth="11/05/2004",
                         email="owen.grant04@gmail.com", address=addr("16 Linden Avenue", "Nottingham", "NG11 6AB"))),
            ("OLIVER", rec("BILLING", full_name="GRANT, O", date_of_birth="11/05/2004",
                           address=addr("16 LINDEN AVE", "NOTTINGHAM", "NG116AB"))),
        ],
    ),
    Scenario(
        "twins_similar_names", "Twins with similar-sounding names", "Different people",
        "Ella and Ellie share a surname, birthday and home, and their names sound alike, but they are twin sisters.",
        [
            ("ELLA", rec("CRM", title="Miss", first_name="Ella", last_name="Walsh", date_of_birth="22/09/2006",
                         email="ella.walsh06@gmail.com", address=addr("8 Meadow Rise", "Norwich", "NR4 7LP"))),
            ("ELLIE", rec("ECOMMERCE", full_name="Ellie Walsh", date_of_birth="2006-09-22",
                          mobile_phone="07591 334872", address=addr("8 Meadow Rise", "Norwich", "NR4 7LP"))),
            ("ELLA", rec("LOYALTY", first_name="E", last_name="Walsh", date_of_birth="22/9/06",
                         address=addr("8 Meadow Rise", "Norwich", "NR4 7LP"))),
        ],
    ),
    Scenario(
        "neighbours_same_name", "Neighbours with the same name", "Different people",
        "Two David Joneses in one Welsh postcode: number 12 and number 30 are different houses.",
        [
            ("NO12", rec("CRM", title="Mr", first_name="David", last_name="Jones", date_of_birth="03/06/1949",
                         address=addr("12 Heol y Parc", "Bala", "LL23 7AB"))),
            ("NO30", rec("LOYALTY", first_name="David", last_name="Jones", mobile_phone="07887 301245",
                         address=addr("30 Heol y Parc", "Bala", "LL23 7AB"))),
        ],
    ),
    Scenario(
        "same_building_different_flats", "Same name, different flats", "Different people",
        "Flat 3 and Flat 7 in Riverside House are different homes; Sarah Ahmed is a common name.",
        [
            ("FLAT3", rec("CRM", title="Ms", first_name="Sarah", last_name="Ahmed", date_of_birth="12/02/1991",
                          email="sarah.ahmed91@gmail.com",
                          address={"line1": "Flat 3 Riverside House", "line2": "2 Narrow Quay", "town": "Bristol",
                                   "postcode": "BS1 4QA"})),
            ("FLAT7", rec("ECOMMERCE", full_name="Sarah Ahmed", email="s.ahmed@yahoo.co.uk",
                          address={"lines": ["Flat 7 Riverside House", "2 Narrow Quay", "Bristol"],
                                   "postcode": "BS1 4QA"})),
        ],
    ),
    Scenario(
        "son_jr_father_unmarked", "Son marked Jr; father's record has no suffix", "Different people",
        "One record says Jr. The other belongs to a man born in 1950: a father and son, not one person.",
        [
            ("FATHER", rec("CRM", title="Mr", first_name="Peter", last_name="Lloyd", date_of_birth="03/03/1950",
                           address=addr("5 Church Lane", "Ludlow", "SY8 1AP"))),
            ("SON", rec("ECOMMERCE", full_name="Peter Lloyd Jr", email="peterlloyd.jr@gmail.com",
                        address=addr("5 Church Lane", "Ludlow", "SY8 1AP"))),
        ],
    ),
    Scenario(
        "couple_shared_mobile", "Couple share a mobile; caller gave only an initial", "Different people",
        "Sana (born 1988) and Salman (born 1985) share one mobile. 'S Khan' fits both, but they are a couple.",
        [
            ("SANA", rec("CRM", title="Mrs", first_name="Sana", last_name="Khan", date_of_birth="07/08/1988",
                         mobile_phone="07445 882610", address=addr("44 Grange Road", "Birmingham", "B10 9QX"))),
            ("SALMAN", rec("LOYALTY", title="Mr", first_name="Salman", last_name="Khan", date_of_birth="1985-12-01",
                           mobile_phone="+44 7445 882610", address=addr("44 Grange Road", "Birmingham", "B10 9QX"))),
            ("SANA", rec("CALLCENTRE", first_name="S", last_name="Khan", mobile_phone="07445882610")),
        ],
    ),
    Scenario(
        "mother_daughter_clue_in_email", "Mother and daughter; the clue is in the email", "Different people",
        "Same name and address, no DOB on the new record, but Miss and an email ending in 90 point to a daughter "
        "born around 1990, not the Mrs born in 1962.",
        [
            ("MUM", rec("CRM", title="Mrs", first_name="Patricia", last_name="Evans", date_of_birth="15/04/1962",
                        mobile_phone="07790 456123", address=addr("175 Park Close", "Wrexham", "LL11 2RG"))),
            ("DAUGHTER", rec("LOYALTY", title="Miss", first_name="Patricia", last_name="Evans",
                             email="patsy.evans90@hotmail.co.uk", address=addr("175 Park Cl", "Wrexham", "LL11 2RG"))),
        ],
    ),
]


def person_map() -> dict[tuple[str, str], str]:
    """(SOURCE_SYSTEM, source_record_id) -> a person label unique across scenarios."""
    return {(r["source_system"].upper(), r["source_record_id"]): f"{s.key}:{p}"
            for s in SCENARIOS for p, r in s.records}


def queue_order() -> list[Scenario]:
    """Alternate same-person and different-people scenarios so the queue isn't predictable."""
    same = [s for s in SCENARIOS if s.truth == "Same person"]
    other = [s for s in SCENARIOS if s.truth != "Same person"]
    out: list[Scenario] = []
    while same or other:
        if other:
            out.append(other.pop(0))
        if same:
            out.append(same.pop(0))
    return out

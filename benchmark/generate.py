"""Synthetic UK customer population with ground truth.

Builds households of people, then emits several source-system records per
person with realistic formatting differences and corruption. Every record
carries the true person_id and a list of scenario tags so results can be
broken down by cause.

    python -m benchmark.generate --records 200000 --out data.jsonl --seed 7

Scenarios covered
-----------------
Variation:   source formats (parts / "SURNAME, First" / full name, DOB formats,
             phone formats, upper case, abbreviations, postcode spacing)
Corruption:  forename/surname typos, nicknames, DOB day/month swap, DOB typo,
             DOB placeholder, email variants (dots, +tags, case), email domain
             typo, older email, mobile lost leading zero, mobile digit typo,
             missing flat number, postcode typo, missing postcode, postcode
             typed into address lines, missing fields
Life events: house moves (with/without previous address), surname change
Siblings never share a forename, and a child takes a parent's forename only
as a Jr (whose records carry the suffix 40% of the time).

Hard negatives (must NOT merge):
             households sharing address and landline, family-shared email,
             couples sharing a mobile, twins (same DOB/surname/address),
             fathers and sons with the same name (Jr/Snr), doppelgangers
             (same forename + surname + DOB, different person), unrelated
             house-sharers, flats in the same building
Accidental shared PII:
             another person's email or mobile typed onto a record, a partner's
             email used by mistake, call-centre placeholder email/phone
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import string
from collections import defaultdict
from dataclasses import dataclass, field

from app.cleansing.nicknames import NICKNAMES
from app.cleansing.postcode import parse_postcode

# --------------------------------------------------------------------------- name pools

MALE = """James John Robert Michael William David Richard Joseph Thomas Charles Christopher Daniel Matthew
Anthony Mark Steven Paul Andrew Joshua Kenneth Kevin Brian George Timothy Ronald Edward Jason Jeffrey Ryan
Jacob Gary Nicholas Eric Jonathan Stephen Larry Justin Scott Brandon Benjamin Samuel Gregory Alexander
Patrick Frank Raymond Jack Dennis Jerry Tyler Aaron Jose Adam Nathan Henry Douglas Zachary Peter Kyle
Oliver Harry Charlie Leo Alfie Freddie Archie Arthur Theo Oscar Noah Lewis Liam Connor Callum Jamie Ross
Gareth Rhys Dylan Owen Iwan Ewan Fraser Angus Hamish Mohammed Ali Ahmed Hassan Imran Bilal Usman Arjun
Ravi Sanjay Vikram Rajesh Amit Krzysztof Piotr Tomasz Marek Chukwuemeka Oluwaseun Kwame Wei Jun Hao
Declan Seamus Padraig Cormac Niall Ciaran Simon Neil Graham Ian Keith Colin Derek Trevor Malcolm Stuart""".split()

FEMALE = """Mary Patricia Jennifer Linda Elizabeth Barbara Susan Jessica Sarah Karen Lisa Nancy Margaret Sandra
Ashley Kimberley Emily Donna Michelle Carol Amanda Dorothy Melissa Deborah Stephanie Rebecca Sharon Laura
Cynthia Kathryn Amy Angela Helen Anna Brenda Pamela Emma Nicola Samantha Katherine Christine Debbie Rachel
Catherine Carolyn Janet Ruth Maria Heather Diane Virginia Julie Joyce Victoria Olivia Kelly Christina
Lauren Joan Evelyn Judith Megan Andrea Cheryl Hannah Jacqueline Martha Gloria Teresa Sara Madison Frances
Kathleen Abigail Alice Julia Judy Sophie Amelia Isla Ava Mia Poppy Isabella Grace Lily Freya Ella Chloe
Charlotte Jade Leanne Gemma Kirsty Siobhan Aoife Niamh Ciara Sinead Eilidh Morag Catriona Fatima Aisha
Zainab Ayesha Maryam Priya Anjali Sunita Kavita Agnieszka Katarzyna Magdalena Ngozi Chiamaka Adaeze
Mei Ling Yan Gwen Ffion Bethan Carys Lowri Megan Shirley Jean Gillian Pauline Lorraine Tracey Wendy""".split()

SURNAMES = """Smith Jones Williams Taylor Brown Davies Evans Wilson Thomas Johnson Roberts Robinson Thompson Wright
Walker White Edwards Hughes Green Hall Lewis Harris Clarke Patel Jackson Wood Turner Martin Cooper Hill Ward
Morris Moore Clark Lee King Baker Harrison Morgan Allen James Scott Phillips Watson Davis Parker Price
Bennett Young Griffiths Mitchell Kelly Cook Carter Richardson Bailey Collins Bell Shaw Murphy Miller Cox
Richards Khan Marshall Anderson Simpson Ellis Adams Singh Begum Wilkinson Foster Chapman Powell Webb
Rogers Gray Mason Ali Hunt Hussain Campbell Matthews Owen Palmer Holmes Mills Barnes Knight Lloyd Butler
Russell Barker Fisher Stevens Jenkins Murray Dixon Harvey Graham Pearson Ahmed Fletcher Walsh Kaur Gibson
Howard Andrews Stewart Elliott Reynolds Saunders Payne Fox Ford Pearce Day Brooks West Lawrence Cole
Atkinson Bradley Spencer Gill Dawson Ball Burton O'Brien Watts Rose Booth Perry Ryan Grant Wells Armstrong
Francis Rees Hayes Hart Hudson Newman Barrett Webster Hunter Gregory Carr Lowe Page Marsh Riley Dunn
Woods Parsons Berry Stone Reid Holland Hawkins Harding Porter Robertson Newton Oliver Reed Kennedy
Williamson Bird Gardner Shah Dean Lane Cooke Bates Henderson Parry Burgess Bishop Walton Burns Nicholson
Shepherd Ross Cross Long Freeman Warren Nicholls Hamilton Byrne Sutton McDonald Yates Hodgson Robson Curtis
Hopkins O'Connor Harper Coleman Watkins Moss McCarthy Chambers O'Neill Griffin Sharp Hardy Wheeler Potter
Osborne Johnston Gordon Doyle Wallace George Jordan Hutchinson Rowe Burke May Pritchard Gilbert Willis
Higgins Read Miles Stevenson Stephenson Hammond Arnold Buckley Walters Hewitt Barber Nelson Slater Austin
Sullivan Whitehead Mann Frost Lambert Stephens Blake Akhtar Lynch Goodwin Barton Woodward Thomson Cunningham
Quinn Barnett Baxter Bibi Clayton Nash Greenwood Jennings Holt Kemp Poole Gallagher Bond Stokes Tucker
Davidson Fowler Heath Norman Middleton Lawson Banks French Stanley Jarvis Gibbs Ferguson Hayward Carroll
Douglas Nowak Kowalski Wisniewski Okafor Adeyemi Mensah Chen Wang Zhang Nguyen Kumar Sharma Rahman Iqbal
MacLeod MacKenzie McLaughlin Fitzgerald Maguire""".split()

STREETS = """Church Station Park Victoria Mill High Queens Kings Manor School Main Green Grange Chapel North
South West East New Albert Alexandra Windsor York Richmond Oak Elm Ash Beech Cedar Willow Birch Chestnut
Maple Holly Rowan Hawthorn Meadow Orchard Springfield Fairfield Highfield Westfield Brookfield Mount
Hill Valley River Bridge Castle Abbey Priory Cross Market Garden Farm Wood Forest Heath Moor Lake
Stanley Clarence Wellington Nelson Gladstone Cromwell Milton Shakespeare Byron Tennyson Wordsworth
Kingsway Broadway Grosvenor Belgrave Cavendish Portland Lansdowne Marlborough Devonshire Russell
Cambridge Oxford Clifton Hamilton Lincoln Warwick Stafford Chester Derby Bedford Salisbury Exeter""".split()
DESCRIPTORS = ["Road"] * 6 + ["Street"] * 5 + ["Avenue"] * 3 + ["Lane"] * 3 + ["Close"] * 3 + \
    ["Crescent", "Drive", "Gardens", "Grove", "Way", "Court", "Place", "Terrace", "Walk", "Rise", "Mews"]
DESC_ABBR = {"Road": "Rd", "Street": "St", "Avenue": "Ave", "Lane": "Ln", "Close": "Cl", "Crescent": "Cres",
             "Drive": "Dr", "Gardens": "Gdns", "Grove": "Gr", "Court": "Ct", "Place": "Pl", "Terrace": "Terr"}
HOUSE_NAMES = ["Rose Cottage", "The Old Rectory", "Ivy Cottage", "The Laurels", "Orchard House", "The Old Mill",
               "Holly Lodge", "Meadow View", "The Willows", "Fern Cottage", "Hillside", "Woodlands", "The Barn"]
BUILDINGS = ["Rose Court", "Victoria House", "Albion Court", "Mill House", "Kingsley Court", "Oak Lodge",
             "Regent House", "Park View", "Quayside", "The Maltings", "Cedar Court", "Clarence House"]
LOCALITIES = ["Headingley", "Chapel Allerton", "Didsbury", "Moseley", "Clifton", "Roath", "Jesmond",
              "Hampstead", "Morningside", "Hyde Park", "Beeston", "Kirkstall", "Fallowfield", "Edgbaston"]

AREAS = {  # postcode area -> (post town, number of districts)
    "LS": ("Leeds", 28), "M": ("Manchester", 40), "B": ("Birmingham", 45), "SW": ("London", 20),
    "E": ("London", 18), "N": ("London", 22), "SE": ("London", 28), "NW": ("London", 11), "G": ("Glasgow", 40),
    "CF": ("Cardiff", 24), "BS": ("Bristol", 40), "NG": ("Nottingham", 25), "LE": ("Leicester", 19),
    "S": ("Sheffield", 36), "L": ("Liverpool", 38), "NE": ("Newcastle Upon Tyne", 30), "EH": ("Edinburgh", 30),
    "BT": ("Belfast", 49), "OX": ("Oxford", 29), "CB": ("Cambridge", 11), "YO": ("York", 26),
    "PL": ("Plymouth", 35), "EX": ("Exeter", 39), "SO": ("Southampton", 53), "NR": ("Norwich", 35),
}
COUNTIES = {"Leeds": "West Yorkshire", "Manchester": "Greater Manchester", "Birmingham": "West Midlands",
            "Bristol": "Avon", "Sheffield": "South Yorkshire", "Oxford": "Oxfordshire", "Norwich": "Norfolk"}
INWARD_LETTERS = "ABDEFGHJLNPQRSTUWXYZ"

EMAIL_PROVIDERS = [("gmail.com", 35), ("hotmail.co.uk", 14), ("outlook.com", 10), ("yahoo.co.uk", 9),
                   ("btinternet.com", 8), ("icloud.com", 7), ("sky.com", 5), ("live.co.uk", 5),
                   ("aol.com", 2), ("talktalk.net", 2), ("virginmedia.com", 3)]
EMAIL_TYPOS = {"gmail.com": "gmial.com", "hotmail.co.uk": "hotmial.co.uk", "btinternet.com": "btinterent.com",
               "outlook.com": "outlok.com", "yahoo.co.uk": "yaho.co.uk"}

SOURCES = ["CRM", "ECOMMERCE", "LOYALTY", "BILLING", "CALLCENTRE", "MARKETING"]
SOURCE_WEIGHTS = [25, 25, 18, 12, 12, 8]

KEYBOARD = {c: n for c, n in zip(
    "qwertyuiopasdfghjklzxcvbnm",
    ["wa", "qes", "wrd", "etf", "ryg", "tuh", "yij", "uok", "ipl", "ol", "qsz", "awdx", "sefc", "drgv",
     "fthb", "gyjn", "hukm", "jil", "kop", "asx", "zsdc", "xdfv", "cfgb", "vghn", "bhjm", "njk"])}

FORMAL_TO_NICK: dict[str, list[str]] = defaultdict(list)
for _nick, _formals in NICKNAMES.items():
    for _f in _formals:
        if _nick != _f and len(_nick) > 1:
            FORMAL_TO_NICK[_f].append(_nick)


def zipf_weights(n: int, s: float = 0.9) -> list[float]:
    return [1 / (i + 1) ** s for i in range(n)]


# --------------------------------------------------------------------------- model


@dataclass
class Dwelling:
    dwelling_id: int
    sub: str | None
    building_name: str | None
    number: str | None
    street: str
    locality: str | None
    town: str
    postcode: str
    uprn: str


@dataclass
class Person:
    person_id: int
    household_id: int
    first: str
    middle: str | None
    last: str
    gender: str
    dob: dt.date
    dwelling: Dwelling
    maiden: str | None = None
    suffix: str | None = None
    email: str | None = None
    old_email: str | None = None
    mobile: str | None = None
    landline: str | None = None
    family_email: str | None = None
    partner: "Person | None" = None
    moved_to: Dwelling | None = None
    tags: set[str] = field(default_factory=set)  # person-level scenario tags


class World:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        # Separate stream for re-drawing a colliding forename, so the fix for
        # same-named siblings leaves every other generated attribute unchanged.
        self.alt = random.Random(seed * 7919 + 1)
        self.male_w = zipf_weights(len(MALE))
        self.female_w = zipf_weights(len(FEMALE))
        self.surname_w = zipf_weights(len(SURNAMES), 0.75)
        self.dwellings: list[Dwelling] = []
        self.used_emails: set[str] = set()
        self.used_mobiles: set[str] = set()
        self.used_uprns: set[str] = set()
        self.people: list[Person] = []
        self._postcode_streets: list = []
        self._next_household = 0

    # ---------------------------------------------------------------- primitives
    def male(self) -> str:
        return self.rng.choices(MALE, self.male_w)[0]

    def female(self) -> str:
        return self.rng.choices(FEMALE, self.female_w)[0]

    def surname(self) -> str:
        if self.rng.random() < 0.02:
            a, b = self.rng.sample(SURNAMES[:120], 2)
            return f"{a}-{b}"
        return self.rng.choices(SURNAMES, self.surname_w)[0]

    def dob(self, min_age: int, max_age: int) -> dt.date:
        today = dt.date(2026, 10, 27)
        days = self.rng.randint(min_age * 365, max_age * 365)
        d = today - dt.timedelta(days=days)
        return d if d not in (dt.date(1970, 1, 1), dt.date(2000, 1, 1)) else d + dt.timedelta(days=1)

    def postcode(self, area: str | None = None) -> tuple[str, str]:
        while True:
            area = area or self.rng.choice(list(AREAS))
            town, n = AREAS[area]
            district = f"{area}{self.rng.randint(1, n)}"
            pc = f"{district} {self.rng.randint(0, 9)}{self.rng.choice(INWARD_LETTERS)}{self.rng.choice(INWARD_LETTERS)}"
            if parse_postcode(pc):
                return pc, town

    def uprn(self) -> str:
        while True:
            u = str(self.rng.randint(10**9, 10**12 - 1))
            if u not in self.used_uprns:
                self.used_uprns.add(u)
                return u

    def new_dwelling(self, area: str | None = None) -> Dwelling:
        pc, town = self.postcode(area)
        street = f"{self.rng.choice(STREETS)} {self.rng.choice(DESCRIPTORS)}"
        locality = self.rng.choice(LOCALITIES) if self.rng.random() < 0.15 else None
        r = self.rng.random()
        sub = name = None
        number: str | None = str(self.rng.randint(1, 220))
        if r < 0.15:  # flat in a block: several dwellings share number + postcode
            block = [d for d in self.dwellings[-50:] if d.sub and d.postcode == pc]
            if block and self.rng.random() < 0.6:
                base = block[0]
                used = {d.sub for d in self.dwellings[-50:] if d.postcode == pc}
                n = 1
                while f"Flat {n}" in used:
                    n += 1
                d = Dwelling(len(self.dwellings), f"Flat {n}", base.building_name, base.number, base.street,
                             base.locality, base.town, base.postcode, self.uprn())
                self.dwellings.append(d)
                return d
            sub = f"Flat {self.rng.randint(1, 30)}"
            name = self.rng.choice(BUILDINGS) if self.rng.random() < 0.5 else None
        elif r < 0.20:
            name, number = self.rng.choice(HOUSE_NAMES), None
        elif r < 0.25:
            number += self.rng.choice("AB")
        d = Dwelling(len(self.dwellings), sub, name, number, street, locality, town, pc, self.uprn())
        self.dwellings.append(d)
        return d

    def email_for(self, first: str, last: str) -> str:
        f, l = first.lower(), last.lower().replace("'", "").replace(" ", "")
        for _ in range(50):
            pattern = self.rng.random()
            if pattern < 0.35:
                local = f"{f}.{l}"
            elif pattern < 0.55:
                local = f"{f}{l}{self.rng.randint(1, 99)}"
            elif pattern < 0.7:
                local = f"{f[0]}{l}"
            elif pattern < 0.8:
                local = f"{f}_{l}{self.rng.randint(60, 99)}"
            else:
                local = f"{f}{self.rng.choice(['x', 'y', 'z', ''])}{self.rng.randint(100, 9999)}"
            domain = self.rng.choices([p for p, _ in EMAIL_PROVIDERS], [w for _, w in EMAIL_PROVIDERS])[0]
            e = f"{local}@{domain}"
            if e not in self.used_emails:
                self.used_emails.add(e)
                return e
        raise RuntimeError("email space exhausted")

    def mobile_number(self) -> str:
        while True:
            m = "07" + self.rng.choice("12345789") + "".join(self.rng.choices(string.digits, k=8))
            if m.startswith("07700900") or len(set(m[1:])) <= 2 or m in self.used_mobiles:
                continue
            self.used_mobiles.add(m)
            return m

    def landline(self) -> str:
        if self.rng.random() < 0.3:
            return "020" + self.rng.choice("378") + "".join(self.rng.choices(string.digits, k=7))
        return "01" + "".join(self.rng.choices(string.digits[1:], k=3)) + "".join(self.rng.choices(string.digits, k=6))

    # ---------------------------------------------------------------- people
    def person(self, hh: int, dwelling: Dwelling, *, gender: str | None = None, first: str | None = None,
               last: str | None = None, dob: dt.date | None = None, min_age=18, max_age=90,
               avoid: set[str] | None = None) -> Person:
        gender = gender or self.rng.choice("MF")
        first = first or (self.male() if gender == "M" else self.female())
        while avoid and first in avoid:  # siblings / parents don't share a forename
            pool, wts = (MALE, self.male_w) if gender == "M" else (FEMALE, self.female_w)
            first = self.alt.choices(pool, wts)[0]
        middle = None
        if self.rng.random() < 0.45:
            middle = self.male() if gender == "M" else self.female()
        p = Person(len(self.people), hh, first, middle, last or self.surname(), gender,
                   dob or self.dob(min_age, max_age), dwelling)
        if self.rng.random() < 0.88:
            p.email = self.email_for(p.first, p.last)
        if self.rng.random() < 0.15:
            p.old_email = self.email_for(p.first, p.last)
        if self.rng.random() < 0.90:
            p.mobile = self.mobile_number()
        if self.rng.random() < 0.10:  # moves house during the data's lifetime
            same_area = self.rng.random() < 0.4
            area = next((a for a in AREAS if dwelling.postcode.startswith(a) and
                         dwelling.postcode[len(a)].isdigit()), None) if same_area else None
            p.moved_to = self.new_dwelling(area)
            p.tags.add("moved")
        self.people.append(p)
        return p

    def household(self) -> list[Person]:
        hh = self._next_household
        self._next_household += 1
        home = self.new_dwelling()
        landline = self.landline() if self.rng.random() < 0.35 else None
        r = self.rng.random()
        members: list[Person] = []
        if r < 0.30:
            members.append(self.person(hh, home))
        elif r < 0.37:  # unrelated house-sharers
            for _ in range(self.rng.randint(2, 4)):
                members.append(self.person(hh, home, min_age=19, max_age=35))
            for m in members:
                m.tags.add("house_share")
        else:
            surname = self.surname()
            a = self.person(hh, home, gender="M", last=surname, min_age=25, max_age=85)
            b = self.person(hh, home, gender="F", last=surname, min_age=23, max_age=83)
            if self.rng.random() < 0.6:
                b.maiden = self.surname()
            if self.rng.random() < 0.25:  # couple keeps separate surnames
                b.last, b.maiden = self.surname(), None
            a.partner, b.partner = b, a
            members += [a, b]
            if self.rng.random() < 0.04:  # couple share one mobile
                b.mobile = a.mobile
                a.tags.add("shared_couple_mobile")
                b.tags.add("shared_couple_mobile")
            if r > 0.62:  # children
                n_kids = self.rng.choices([1, 2, 3, 4], [35, 40, 18, 7])[0]
                parent_age = (dt.date(2026, 10, 27) - b.dob).days // 365
                max_kid_age = max(0, min(parent_age - 18, 40))
                kids: list[Person] = []
                twins = n_kids >= 2 and self.rng.random() < 0.05
                junior = self.rng.random() < 0.04
                for k in range(n_kids):
                    g = self.rng.choice("MF")
                    kid_dob = kids[0].dob if (twins and k == 1) else self.dob(0, max(max_kid_age, 1))
                    first = None
                    if junior and g == "M" and not any(x.suffix for x in kids):
                        first = a.first
                    if twins and k == 1:
                        first = None  # distinct name
                        while first is None or first == kids[0].first:
                            first = self.male() if g == "M" else self.female()
                    taken = None if first else {a.first, b.first, *(k.first for k in kids)}
                    kid = self.person(hh, home, gender=g, first=first, last=surname, dob=kid_dob, avoid=taken)
                    if first == a.first and g == "M" and junior and not (twins and k == 1):
                        kid.suffix, a.suffix = "Jr", "Sr"
                        kid.tags.add("junior_senior")
                        a.tags.add("junior_senior")
                    if twins and k <= 1:
                        kid.tags.add("twin")
                    kids.append(kid)
                members += kids
            if len(members) > 2 and self.rng.random() < 0.08:
                fam = f"the{surname.lower().replace(chr(39), '').replace('-', '')}s"
                fe = f"{fam}{self.rng.randint(1, 999)}@" + self.rng.choice(["btinternet.com", "sky.com", "gmail.com"])
                self.used_emails.add(fe)
                for m in members:
                    m.family_email = fe
                    m.tags.add("shared_family_email")
        for m in members:
            m.landline = landline
            if landline and len(members) > 1:
                m.tags.add("shared_landline")
            if len(members) > 1:
                m.tags.add("household")
        return members

    def doppelganger(self, p: Person) -> Person:
        same_district = self.rng.random() < 0.25
        area = next((a for a in AREAS if p.dwelling.postcode.startswith(a)
                     and p.dwelling.postcode[len(a)].isdigit()), None)
        d = self.new_dwelling(area if same_district else None)
        if same_district:  # force same district, different postcode unit
            pc, _ = self.postcode(area)
            district = p.dwelling.postcode.split()[0]
            pc = f"{district} {pc.split()[1]}"
            if parse_postcode(pc) and pc != p.dwelling.postcode:
                d.postcode = pc
                d.town = p.dwelling.town
        hh = self._next_household
        self._next_household += 1
        q = self.person(hh, d, gender=p.gender, first=p.first, last=p.last, dob=p.dob)
        q.middle = None if self.rng.random() < 0.5 else q.middle
        q.tags.add("doppelganger")
        p.tags.add("doppelganger")
        return q


# --------------------------------------------------------------------------- record rendering


class Renderer:
    def __init__(self, world: World) -> None:
        self.w = world
        self.rng = world.rng
        self.seq: dict[str, int] = defaultdict(int)

    def typo(self, s: str) -> str:
        if len(s) < 3:
            return s
        for _ in range(10):
            i = self.rng.randint(1, len(s) - 1)
            op = self.rng.random()
            c = s[i].lower()
            if op < 0.3 and c in KEYBOARD:
                n = self.rng.choice(KEYBOARD[c])
                out = s[:i] + (n.upper() if s[i].isupper() else n) + s[i + 1:]
            elif op < 0.55 and len(s) > 3:
                out = s[:i] + s[i + 1:]
            elif op < 0.8 and i < len(s) - 1:
                out = s[:i] + s[i + 1] + s[i] + s[i + 2:]
            else:
                out = s[:i] + s[i] + s[i:]
            if out != s and out.lower() != s.lower():
                return out
        return s

    def phone_fmt(self, num: str, tags: list[str], allow_lost_zero=True) -> str:
        if allow_lost_zero and self.rng.random() < 0.02 and num.startswith("07"):
            tags.append("mobile_lost_zero")
            return num[1:]
        nsn = num[1:]
        style = self.rng.random()
        if style < 0.35:
            return f"{num[:5]} {num[5:]}"
        if style < 0.55:
            return num
        if style < 0.7:
            return f"+44 {nsn[:4]} {nsn[4:]}"
        if style < 0.8:
            return f"+44 (0){nsn[:4]} {nsn[4:]}"
        if style < 0.88:
            return f"0044{nsn}"
        if style < 0.94:
            return f"({num[:5]}) {num[5:]}"
        return f"{num[:4]}-{num[4:7]}-{num[7:]}"

    def dob_fmt(self, d: dt.date, source: str) -> str:
        r = self.rng.random()
        if source == "ECOMMERCE" and r < 0.5 or r < 0.2:
            return d.isoformat()
        if source == "LOYALTY" and r < 0.7:
            return f"{d.day}/{d.month}/{d.year % 100:02d}"
        if source == "CALLCENTRE" and r < 0.35:
            suf = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
            return f"{d.day}{suf} {d.strftime('%B')} {d.year}"
        if r < 0.85:
            return d.strftime("%d/%m/%Y")
        if r < 0.92:
            return d.strftime("%d-%m-%Y")
        return d.strftime("%d %b %Y")

    def address(self, d: Dwelling, source: str, tags: list[str], allow_corrupt=True) -> dict:
        upper = source == "BILLING" or self.rng.random() < 0.2
        abbr = source in ("BILLING", "CALLCENTRE") or self.rng.random() < 0.3
        street = d.street
        if abbr:
            nm, desc = street.rsplit(" ", 1)
            street = f"{nm} {DESC_ABBR.get(desc, desc)}"
        sub = d.sub
        if sub and allow_corrupt and self.rng.random() < 0.04:
            sub = None
            tags.append("missing_flat_number")
        premise = " ".join(x for x in [d.number, street] if x)
        lines: list[str] = []
        if sub and d.building_name:
            lines += [f"{sub} {d.building_name}" if self.rng.random() < 0.5 else f"{sub}, {d.building_name}", premise]
        elif sub:
            lines += [f"{sub}, {premise}"] if self.rng.random() < 0.5 else [sub, premise]
        elif d.building_name:
            lines += [d.building_name, premise] if premise != street or d.number else [d.building_name, street]
        else:
            lines.append(premise)
        if d.locality:
            lines.append(d.locality)
        pc = d.postcode
        if allow_corrupt and self.rng.random() < 0.012:
            tags.append("postcode_typo")
            if self.rng.random() < 0.5:   # O for 0 / I for 1 in the inward digit
                digit = pc[-3]
                swap = {"0": "O", "1": "I"}.get(digit)
                pc = pc[:-3] + swap + pc[-2:] if swap else pc[:-1] + self.rng.choice(INWARD_LETTERS)
            else:                          # wrong last letter: a different valid postcode
                pc = pc[:-1] + self.rng.choice([c for c in INWARD_LETTERS if c != pc[-1]])
        r = self.rng.random()
        if r < 0.3:
            pc = pc.replace(" ", "")
        elif r < 0.4:
            pc = pc.lower()
        out: dict = {"town": d.town}
        if allow_corrupt and self.rng.random() < 0.02:
            tags.append("postcode_missing")
            pc = None
        elif self.rng.random() < 0.03:
            tags.append("postcode_in_lines")
            lines.append(f"{d.town} {pc}")
            out.pop("town")
            pc = None
        if self.rng.random() < 0.15 and d.town in COUNTIES:
            out["county"] = COUNTIES[d.town]
        if upper:
            lines = [ln.upper() for ln in lines]
            out = {k: v.upper() for k, v in out.items()}
        if source in ("ECOMMERCE",) and self.rng.random() < 0.5:
            out["lines"] = lines
        else:
            for i, ln in enumerate(lines[:4]):
                out[f"line{i + 1}"] = ln
        if pc:
            out["postcode"] = pc
        uprn_rate = {"CRM": 0.35, "BILLING": 0.6}.get(source, 0.0)
        if self.rng.random() < uprn_rate:
            out["uprn"] = d.uprn
        return out

    def record(self, p: Person, k: int, n: int, others: list[Person]) -> dict:
        rng = self.rng
        source = rng.choices(SOURCES, SOURCE_WEIGHTS)[0]
        self.seq[source] += 1
        tags: list[str] = sorted(p.tags)
        typo_rate = 0.08 if source == "CALLCENTRE" else 0.035
        rec: dict = {"source_system": source, "source_record_id": f"{source[:3]}-{self.seq[source]:07d}"}

        # ---- name
        first = p.first
        if FORMAL_TO_NICK.get(first.lower()) and rng.random() < 0.12:
            first = rng.choice(FORMAL_TO_NICK[first.lower()]).capitalize()
            tags.append("nickname")
        if rng.random() < typo_rate:
            first = self.typo(first)
            tags.append("typo_first")
        last = p.last
        use_maiden = p.maiden and rng.random() < 0.3
        if use_maiden:
            last = p.maiden  # type: ignore[assignment]
            tags.append("surname_change")
        if rng.random() < typo_rate:
            last = self.typo(last)
            tags.append("typo_last")
        title = {"M": "Mr", "F": rng.choice(["Mrs", "Ms", "Miss"])}[p.gender] if rng.random() < 0.6 else None
        if title and p.gender == "F" and use_maiden:
            title = rng.choice(["Miss", "Ms"])
        suffix = p.suffix if p.suffix and rng.random() < 0.4 else None
        if p.suffix and not suffix:
            tags.append("suffix_omitted")
        middle = p.middle if p.middle and rng.random() < 0.4 else None

        style = rng.random()
        if source == "BILLING":
            rec["full_name"] = f"{last.upper()}, {first.upper()}" + (f" {middle.upper()}" if middle else "")
            if suffix:
                rec["suffix"] = suffix
        elif source == "ECOMMERCE" and style < 0.6:
            full = " ".join(x for x in [title, first, middle, last, suffix] if x)
            rec["full_name"] = full.upper() if rng.random() < 0.3 else full
        else:
            rec.update({k: v for k, v in {"title": title, "first_name": first, "middle_names": middle,
                                           "last_name": last, "suffix": suffix}.items() if v})
            if rng.random() < 0.15:
                rec["first_name"], rec["last_name"] = first.upper(), last.upper()
        if source != "MARKETING" and rng.random() < 0.3:
            rec["gender"] = rng.choice({"M": ["M", "Male", "male"], "F": ["F", "Female", "female"]}[p.gender])

        # ---- date of birth
        dob_rate = {"CRM": 0.9, "ECOMMERCE": 0.6, "LOYALTY": 0.85, "BILLING": 0.3, "CALLCENTRE": 0.8,
                    "MARKETING": 0.15}[source]
        if rng.random() < dob_rate:
            d = p.dob
            r = rng.random()
            if r < 0.02 and d.day <= 12 and d.day != d.month:
                d = dt.date(d.year, d.day, d.month)
                tags.append("dob_swapped")
            elif r < 0.03:
                try:
                    d = d.replace(year=d.year + rng.choice([-1, 1]))
                except ValueError:
                    pass
                tags.append("dob_typo")
            if r > 0.995:
                rec["date_of_birth"] = "01/01/1900"
                tags.append("dob_placeholder")
            else:
                rec["date_of_birth"] = self.dob_fmt(d, source)
        else:
            tags.append("dob_missing")

        # ---- email
        email_rate = {"CRM": 0.8, "ECOMMERCE": 1.0, "LOYALTY": 0.85, "BILLING": 0.5, "CALLCENTRE": 0.5,
                      "MARKETING": 1.0}[source]
        email = p.email
        if p.family_email and rng.random() < 0.6:
            email = p.family_email
        if p.old_email and rng.random() < 0.3:
            email = p.old_email
            tags.append("old_email")
        r = rng.random()
        if r < 0.004 and others:
            email = rng.choice(others).email or email
            tags.append("wrong_email_other_person")
        elif r < 0.009 and p.partner and p.partner.email:
            email = p.partner.email
            tags.append("partner_email_by_mistake")
        elif r < 0.019 and source == "CALLCENTRE":
            email = "noemail@noemail.com"
            tags.append("placeholder_email")
        if email and rng.random() < email_rate:
            local, domain = email.split("@")
            v = rng.random()
            if v < 0.03 and domain == "gmail.com":
                local = local.replace(".", "") if "." in local else local[:3] + "." + local[3:]
                tags.append("email_variant")
            elif v < 0.05 and domain in ("gmail.com", "outlook.com"):
                local = f"{local}+{rng.choice(['shop', 'promo', 'news'])}"
                tags.append("email_variant")
            elif v < 0.06 and domain in EMAIL_TYPOS:
                domain = EMAIL_TYPOS[domain]
                tags.append("email_domain_typo")
            e = f"{local}@{domain}"
            rec["email"] = e.upper() if rng.random() < 0.05 else (f" {e} " if rng.random() < 0.03 else e)
        else:
            tags.append("email_missing")

        # ---- phones
        mobile_rate = {"CRM": 0.8, "ECOMMERCE": 0.7, "LOYALTY": 0.8, "BILLING": 0.4, "CALLCENTRE": 0.9,
                       "MARKETING": 0.2}[source]
        mobile = p.mobile
        r = rng.random()
        if r < 0.004 and others:
            mobile = rng.choice(others).mobile or mobile
            tags.append("wrong_mobile_other_person")
        elif r < 0.012 and mobile:
            i = rng.randint(3, 10)
            mobile = mobile[:i] + str((int(mobile[i]) + rng.randint(1, 9)) % 10) + mobile[i + 1:]
            tags.append("mobile_digit_typo")
        elif r < 0.017 and source == "CALLCENTRE":
            mobile = "07000000000"
            tags.append("placeholder_phone")
        if mobile and rng.random() < mobile_rate:
            rec["mobile_phone" if rng.random() < 0.7 else "phone"] = self.phone_fmt(mobile, tags)
        else:
            tags.append("mobile_missing")
        if p.landline and source in ("CRM", "BILLING", "CALLCENTRE") and rng.random() < 0.6:
            rec["home_phone"] = self.phone_fmt(p.landline, tags, allow_lost_zero=False)

        # ---- address
        moved_record = p.moved_to is not None and k >= n // 2
        dwelling = p.moved_to if moved_record else p.dwelling
        if use_maiden and rng.random() < 0.5:
            dwelling = self.w.new_dwelling()  # maiden-name record held at an old (parental) address
            tags.append("old_address_unlinked")
        addr_rate = {"MARKETING": 0.4}.get(source, 0.95)
        if rng.random() < addr_rate:
            if source == "MARKETING":
                a = {"postcode": dwelling.postcode}  # type: ignore[union-attr]
                if rng.random() < 0.5:
                    a["line1"] = " ".join(x for x in [dwelling.number, dwelling.street] if x)  # type: ignore[union-attr]
                rec["address"] = a
            else:
                rec["address"] = self.address(dwelling, source, tags)  # type: ignore[arg-type]
            if moved_record:
                tags.append("moved_new_address")
                if rng.random() < 0.4:
                    rec["previous_addresses"] = [self.address(p.dwelling, source, [], allow_corrupt=False)]
                    tags.append("previous_address_supplied")
        else:
            tags.append("address_missing")
        return {"record": rec, "truth": {"person_id": p.person_id, "household_id": p.household_id,
                                         "tags": sorted(set(tags))}}


def generate(n_records: int, seed: int) -> list[dict]:
    w = World(seed)
    r = Renderer(w)
    rng = w.rng
    plan: list[tuple[Person, int]] = []
    total = 0
    while total < n_records:
        for p in w.household():
            age = (dt.date(2026, 10, 27) - p.dob).days // 365
            if age < 16:
                continue
            n = rng.choices([1, 2, 3, 4, 5, 6], [34, 30, 19, 10, 5, 2])[0]
            plan.append((p, n))
            total += n
            if rng.random() < 0.003:  # doppelganger: same name + DOB, different person
                q = w.doppelganger(p)
                m = rng.choices([1, 2, 3], [50, 35, 15])[0]
                plan.append((q, m))
                total += m
    out: list[dict] = []
    for p, n in plan:
        for k in range(n):
            out.append(r.record(p, k, n, w.people[-5000:] if len(w.people) > 10 else []))
    rng.shuffle(out)
    return out[:n_records]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", type=int, default=200_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    data = generate(args.records, args.seed)
    with open(args.out, "w") as f:
        for row in data:
            f.write(json.dumps(row) + "\n")
    persons = {d["truth"]["person_id"] for d in data}
    print(f"wrote {len(data)} records for {len(persons)} people to {args.out}")


if __name__ == "__main__":
    main()

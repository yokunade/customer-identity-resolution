import datetime as dt

import pytest

from app.cleansing import cleanse
from app.cleansing.address import cleanse_address, premise_keys
from app.cleansing.dob import parse_dob, unordered_dob
from app.cleansing.email import cleanse_email
from app.cleansing.names import parse_name
from app.cleansing.phone import cleanse_phone
from app.cleansing.postcode import parse_postcode
from app.schemas import InboundAddress, InboundCustomer

TODAY = dt.date(2026, 10, 27)


def name(**kw):
    base = dict(title=None, first_name=None, middle_names=None, last_name=None, full_name=None, suffix=None)
    base.update(kw)
    return parse_name(**base)


# ------------------------------------------------------------------ names
class TestNames:
    def test_full_name_with_title_and_suffix(self):
        n = name(full_name="MR JOHN PAUL O'BRIEN JNR")
        assert (n.title, n.first_name, n.middle_names, n.last_name, n.suffix) == ("Mr", "John", "Paul", "O'Brien", "Jr")
        assert n.title_gender == "M"

    def test_surname_first_with_comma(self):
        n = name(full_name="Smith, Jane Ann")
        assert (n.first_name, n.middle_names, n.last_name) == ("Jane", "Ann", "Smith")

    def test_particles_stay_with_surname(self):
        n = name(full_name="Ludwig van Beethoven")
        assert n.last_name == "van Beethoven" and n.first_name == "Ludwig"  # mixed case kept

    def test_postnominals_dropped(self):
        n = name(full_name="Dr Sarah Jones OBE PhD")
        assert (n.title, n.first_name, n.last_name) == ("Dr", "Sarah", "Jones")

    def test_proper_case(self):
        assert name(last_name="MCDONALD").last_name == "McDonald"
        assert name(last_name="smith-jones").last_name == "Smith-Jones"
        assert name(last_name="DeVito").last_name == "DeVito"  # mixed case kept

    def test_first_name_with_extra_tokens_moves_to_middle(self):
        n = name(first_name="John Paul", last_name="Smith")
        assert (n.first_name, n.middle_names) == ("John", "Paul")

    def test_placeholder_name_removed(self):
        n = name(first_name="Test", last_name="Customer")
        assert n.first_name is None and n.last_name is None
        assert {i.code for i in n.issues} >= {"placeholder_name"}

    def test_accents_folded(self):
        assert name(first_name="Zoë", last_name="Brontë").first_key == "zoe"


# ------------------------------------------------------------------ postcodes
class TestPostcodes:
    @pytest.mark.parametrize("raw,expected", [
        ("sw1a1aa", "SW1A 1AA"), ("EC1A 1BB", "EC1A 1BB"), ("w1a 0ax", "W1A 0AX"), ("M1 1AE", "M1 1AE"),
        ("B33 8TH", "B33 8TH"), ("CR2 6XH", "CR2 6XH"), ("DN55 1PT", "DN55 1PT"), (" ls6-2ab ", "LS6 2AB"),
        ("GIR 0AA", "GIR 0AA"), ("JE2 3AB", "JE2 3AB"),
    ])
    def test_valid(self, raw, expected):
        assert parse_postcode(raw).full == expected

    @pytest.mark.parametrize("raw", ["", "12345", "SW1A", "QA1 1AA", "SW1A 1AC", "HP7 9QO", "ABCDEFG"])
    def test_invalid(self, raw):
        assert parse_postcode(raw) is None

    def test_inward_digit_repaired(self):
        pc = parse_postcode("LS6 OAB")
        assert pc.full == "LS6 0AB" and pc.corrected

    def test_parts(self):
        pc = parse_postcode("SW1A 1AA")
        assert (pc.outward, pc.inward, pc.sector, pc.district, pc.area) == ("SW1A", "1AA", "SW1A 1", "SW1A", "SW")


# ------------------------------------------------------------------ addresses
class TestAddresses:
    def test_flat_number_street(self):
        a, _ = cleanse_address(InboundAddress(line1="Flat 3, 12a High St", line2="Little Chalfont",
                                              town="Amersham", postcode="hp7 9qa"))
        assert a.sub_building == "FLAT 3" and a.building_number == "12A" and a.street == "HIGH STREET"
        assert a.localities == ["LITTLE CHALFONT"] and a.post_town == "AMERSHAM" and a.postcode == "HP7 9QA"
        assert a.lines == ["FLAT 3", "12A HIGH STREET", "LITTLE CHALFONT", "AMERSHAM", "HP7 9QA"]

    def test_saint_vs_street(self):
        a, _ = cleanse_address(InboundAddress(line1="4 St. John's Rd", town="Bath", postcode="BA1 1AA"))
        assert a.street == "SAINT JOHNS ROAD"

    def test_named_house(self):
        a, _ = cleanse_address(InboundAddress(lines=["Rose Cottage", "Church Lane", "Little Snoring", "Fakenham", "NR21 0AA"]))
        assert a.building_name == "ROSE COTTAGE" and a.street == "CHURCH LANE"
        assert a.post_town == "FAKENHAM" and a.postcode == "NR21 0AA"

    def test_postcode_found_in_lines(self):
        a, issues = cleanse_address(InboundAddress(lines=["12 Acacia Avenue", "Leeds LS6 2AB"]))
        assert a.postcode == "LS6 2AB" and a.post_town == "LEEDS"
        assert "postcode_found_in_lines" in {i.code for i in issues}

    def test_floor_flat(self):
        a, _ = cleanse_address(InboundAddress(line1="Ground Floor Flat", line2="22 Park Road", postcode="BS8 1AA"))
        assert a.sub_building == "GROUND FLOOR FLAT" and a.building_number == "22"

    def test_number_name_line_becomes_flat(self):
        a, _ = cleanse_address(InboundAddress(line1="3 Rose Court", line2="12 High Street", postcode="LS1 1AA"))
        assert a.sub_building == "FLAT 3" and a.building_name == "ROSE COURT" and a.building_number == "12"

    def test_premise_keys_number_and_name(self):
        a, _ = cleanse_address(InboundAddress(lines=["The Old Mill", "7 River Way"], postcode="YO1 7AA"))
        assert premise_keys(a) == {"|7", "|theoldmill"}

    def test_non_uk(self):
        a, issues = cleanse_address(InboundAddress(line1="1 Rue de Rivoli", postcode="75001", country="France"))
        assert not a.is_uk and a.postcode == "75001"
        assert not any(i.code == "invalid_uk_postcode" for i in issues)

    def test_uprn(self):
        a, _ = cleanse_address(InboundAddress(line1="1 A Road", postcode="LS1 1AA", uprn="000100023336956"))
        assert a.uprn == "100023336956"


# ------------------------------------------------------------------ phones
class TestPhones:
    @pytest.mark.parametrize("raw,e164,kind", [
        ("07712 345678", "+447712345678", "mobile"),
        ("+44 (0)7712 345678", "+447712345678", "mobile"),
        ("0044 7712 345678", "+447712345678", "mobile"),
        ("447712345678", "+447712345678", "mobile"),
        ("7712345678", "+447712345678", "mobile"),
        ("020 8123 4567", "+442081234567", "landline"),
        ("0113 245 6789 ext 22", "+441132456789", "landline"),
        ("01539 123456", "+441539123456", "landline"),
        ("0345 123 4567", "+443451234567", "non_geographic"),
        ("+353 1 234 5678", "+35312345678", "international"),
    ])
    def test_valid(self, raw, e164, kind):
        p, _ = cleanse_phone(raw)
        assert (p.e164, p.type) == (e164, kind)

    def test_national_format(self):
        assert cleanse_phone("07712345678")[0].national == "07712 345678"
        assert cleanse_phone("02081234567")[0].national == "020 8123 4567"
        assert cleanse_phone("01132456789")[0].national == "0113 245 6789"

    @pytest.mark.parametrize("raw", ["123", "07712", "0771234567890", "abc", "06123456789"])
    def test_invalid(self, raw):
        assert cleanse_phone(raw)[0] is None

    def test_drama_and_placeholder_numbers_not_usable(self):
        assert cleanse_phone("07700 900123")[0].usable_for_matching is False
        assert cleanse_phone("07777 777777")[0].usable_for_matching is False


# ------------------------------------------------------------------ emails
class TestEmails:
    def test_gmail_canonical(self):
        e, _ = cleanse_email("  J.O.Brien+shop@GoogleMail.com ")
        assert e.address == "j.o.brien+shop@googlemail.com" and e.canonical == "jobrien@gmail.com"

    def test_plus_only_for_supporting_providers(self):
        assert cleanse_email("a.b+x@btinternet.com")[0].canonical == "a.b+x@btinternet.com"
        assert cleanse_email("a.b+x@outlook.com")[0].canonical == "a.b@outlook.com"

    def test_typo_domain(self):
        e, issues = cleanse_email("someone@hotmial.co.uk")
        assert e.address == "someone@hotmail.co.uk" and "domain_typo_corrected" in {i.code for i in issues}

    @pytest.mark.parametrize("raw", ["noemail@noemail.com", "test@example.com", "none@gmail.com"])
    def test_placeholders_not_usable(self, raw):
        assert cleanse_email(raw)[0].usable_for_matching is False

    def test_role_account_not_usable(self):
        assert cleanse_email("info@acme.co.uk")[0].usable_for_matching is False

    @pytest.mark.parametrize("raw", ["not an email", "a@b", "@x.com", "a@@b.com"])
    def test_invalid(self, raw):
        assert cleanse_email(raw)[0] is None


# ------------------------------------------------------------------ dates of birth
class TestDob:
    @pytest.mark.parametrize("raw,expected", [
        ("03/04/1985", dt.date(1985, 4, 3)), ("3-4-85", dt.date(1985, 4, 3)), ("1985-04-03", dt.date(1985, 4, 3)),
        ("19850403", dt.date(1985, 4, 3)), ("3rd April 1985", dt.date(1985, 4, 3)), ("03 Apr 85", dt.date(1985, 4, 3)),
        ("April 3, 1985", dt.date(1985, 4, 3)), ("1985-04-03T00:00:00Z", dt.date(1985, 4, 3)),
        ("12/25/1990", dt.date(1990, 12, 25)), ("01/02/20", dt.date(2020, 2, 1)), ("01/02/30", dt.date(1930, 2, 1)),
    ])
    def test_formats(self, raw, expected):
        d, usable, _ = parse_dob(raw, TODAY)
        assert d == expected and usable

    @pytest.mark.parametrize("raw,code", [
        ("01/01/1900", "placeholder_date"), ("1970-01-01", "placeholder_date"), ("2030-01-01", "future_date"),
        ("31/02/1990", "unparseable_date"), ("1850-05-05", "implausible_age"), ("banana", "unparseable_date"),
    ])
    def test_unusable(self, raw, code):
        _, usable, issues = parse_dob(raw, TODAY)
        assert not usable and code in {i.code for i in issues}

    def test_unordered(self):
        assert unordered_dob(dt.date(1985, 4, 3)) == unordered_dob(dt.date(1985, 3, 4))


# ------------------------------------------------------------------ whole record
def test_gender_from_title_and_dedupe():
    rec = InboundCustomer(source_system="crm", source_record_id="1", title="Mrs", first_name="Ann", last_name="Lee",
                          email="ann@gmail.com", emails=["a.n.n@gmail.com"], phone="07712345678",
                          mobile_phone="+447712345678")
    c, _ = cleanse(rec, TODAY)
    assert c.gender == "F" and len(c.emails) == 1 and len(c.phones) == 1 and c.source_system == "CRM"


def test_work_landline_not_used_for_matching():
    rec = InboundCustomer(source_system="crm", source_record_id="1", work_phone="020 8123 4567")
    c, _ = cleanse(rec, TODAY)
    assert c.phones[0].usable_for_matching is False

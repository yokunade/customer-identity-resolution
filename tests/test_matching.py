import datetime as dt
import uuid

import pytest

from app.cleansing import cleanse
from app.matching.engine import Candidate, decide
from app.matching.keys import (
    BLOCKING_KEYS,
    MATCH_KEYS,
    build_blocking_keys,
    build_match_keys,
    extract_components,
    implied,
    match_profile,
)
from app.matching.phonetics import nysiis, soundex
from app.matching.rules import RULES, RULES_BY_KEY, veto_reasons
from app.schemas import InboundCustomer

SECRET = "test-secret"
TODAY = dt.date(2026, 10, 27)


def prep(**kw):
    rec = InboundCustomer(source_system="t", source_record_id=str(uuid.uuid4()), **kw)
    c, _ = cleanse(rec, TODAY)
    comps = extract_components(c)
    return c, build_match_keys(comps, SECRET), build_blocking_keys(comps, SECRET), match_profile(c)


def shared(a, b):
    return {k for k in a if k in b and a[k] & b[k]}


# ------------------------------------------------------------------ phonetics
@pytest.mark.parametrize("name,code", [
    ("Robert", "R163"), ("Rupert", "R163"), ("Ashcraft", "A261"), ("Tymczak", "T522"), ("Pfister", "P236"),
    ("Smith", "S530"), ("Smyth", "S530"), ("Lee", "L000"),
])
def test_soundex(name, code):
    assert soundex(name) == code


def test_nysiis_groups_variants():
    assert nysiis("Catherine") == nysiis("Katherine")
    assert nysiis("Philips") == nysiis("Phillips")
    assert nysiis("Macdonald") == nysiis("Mcdonald")
    assert nysiis("Smith") != nysiis("Jones")


# ------------------------------------------------------------------ key catalogue invariants
def test_every_rule_key_is_reachable_by_a_blocking_key():
    """If two records share a rule's match key they must share at least one
    blocking key, otherwise the match could never be found (and concurrent
    inserts would not be serialised by the advisory locks)."""
    for rule in RULES:
        comps = implied(MATCH_KEYS[rule.key].components)
        covering = [b for b in BLOCKING_KEYS.values() if set(b.components) <= comps]
        assert covering, f"{rule.key} is not covered by any blocking key"


def test_keys_are_deterministic_and_secret_dependent():
    _, k1, _, _ = prep(first_name="Ann", last_name="Lee", date_of_birth="1980-01-02", email="ann@x.com")
    _, k2, _, _ = prep(first_name="ANN", last_name="lee", date_of_birth="02/01/1980", email="ANN@X.COM")
    assert k1 == k2
    rec = InboundCustomer(source_system="t", source_record_id="1", first_name="Ann", last_name="Lee",
                          email="ann@x.com", date_of_birth="1980-01-02")
    c, _ = cleanse(rec, TODAY)
    assert build_match_keys(extract_components(c), "other") != k1


def test_no_keys_from_unusable_values():
    _, mk, bk, _ = prep(first_name="Ann", last_name="Lee", email="test@example.com", phone="07700 900123",
                        date_of_birth="01/01/1900")
    assert "B_EMAIL" not in bk and "B_PHONE" not in bk
    assert not any("DOB" in k for k in mk)


# ------------------------------------------------------------------ key behaviour
BASE = dict(first_name="William", last_name="Smith", date_of_birth="14/02/1979", email="bill.smith@gmail.com",
            mobile_phone="07712 345678", address={"line1": "12 Acacia Avenue", "town": "Leeds", "postcode": "LS6 2AB"})


def test_nickname_and_email_variants_share_exact_key():
    _, a, _, _ = prep(**BASE)
    _, b, _, _ = prep(full_name="Bill Smith", date_of_birth="1979-02-14", email="billsmith+x@googlemail.com")
    assert "EMAIL_NAME_DOB" in shared(a, b)


def test_typo_in_surname_caught_by_phonetic_key():
    _, a, _, _ = prep(**BASE)
    _, b, _, _ = prep(first_name="Wiliam", last_name="Smyth", date_of_birth="14/02/1979",
                      address={"line1": "12 Acacia Ave", "postcode": "LS62AB"})
    assert "SOUNDEX_NAME_DOB_POSTCODE" in shared(a, b)
    assert "NAME_DOB_POSTCODE" not in shared(a, b)


def test_transposed_dob():
    _, a, _, _ = prep(first_name="Ann", last_name="Lee", date_of_birth="03/04/1985", address={"line1": "1 A Road", "postcode": "LS1 1AA"})
    _, b, _, _ = prep(first_name="Ann", last_name="Lee", date_of_birth="04/03/1985", address={"line1": "1 A Road", "postcode": "LS1 1AA"})
    assert "NAME_DOBSWAP_POSTCODE" in shared(a, b) and "NAME_DOB_POSTCODE" not in shared(a, b)


def test_surname_change_same_dwelling():
    _, a, _, _ = prep(first_name="Emma", last_name="Jones", date_of_birth="1990-06-01",
                      address={"line1": "Flat 2, 5 Mill Lane", "postcode": "M1 1AE"})
    _, b, _, _ = prep(first_name="Emma", last_name="Patel", date_of_birth="1990-06-01",
                      address={"line1": "Flat 2", "line2": "5 Mill Lane", "postcode": "M1 1AE"})
    assert shared(a, b) & set(MATCH_KEYS) == {"FIRST_DOB_PREMISE_POSTCODE"}


def test_double_barrelled_surname():
    _, a, _, _ = prep(first_name="Kate", last_name="Smith-Jones", date_of_birth="1988-08-08",
                      address={"line1": "9 Elm Close", "postcode": "BS1 4DJ"})
    _, b, _, _ = prep(first_name="Katherine", last_name="Jones", date_of_birth="1988-08-08",
                      address={"line1": "9 Elm Close", "postcode": "BS1 4DJ"})
    assert "ALTSURNAME_DOB_PREMISE_POSTCODE" not in shared(a, b)  # needs both sides multi-part
    assert "FIRST_DOB_PREMISE_POSTCODE" in shared(a, b)


def test_previous_address_lets_movers_match():
    _, a, _, _ = prep(first_name="Raj", last_name="Patel", date_of_birth="1975-05-05",
                      address={"line1": "1 Old Road", "postcode": "LE1 1AA"})
    _, b, _, _ = prep(first_name="Raj", last_name="Patel", date_of_birth="1975-05-05",
                      address={"line1": "77 New Street", "postcode": "NG1 1AA"},
                      previous_addresses=[{"line1": "1 Old Road", "postcode": "LE1 1AA"}])
    assert "NAME_DOB_PREMISE_POSTCODE" in shared(a, b)


# ------------------------------------------------------------------ vetoes
def test_father_and_son_vetoed_by_generation():
    _, a, _, pa = prep(first_name="John", last_name="Smith", suffix="Snr", address={"line1": "1 A Road", "postcode": "LS1 1AA"})
    _, b, _, pb = prep(first_name="John", last_name="Smith", suffix="Jnr", address={"line1": "1 A Road", "postcode": "LS1 1AA"})
    assert "NAME_PREMISE_POSTCODE" in shared(a, b)
    assert veto_reasons(RULES_BY_KEY["NAME_PREMISE_POSTCODE"], pa, pb) == ["generation"]


def test_shared_family_email_vetoed_by_first_name():
    _, a, _, pa = prep(first_name="Sarah", last_name="Brown", date_of_birth="1960-01-01", email="brownfamily@btinternet.com")
    _, b, _, pb = prep(first_name="Peter", last_name="Brown", date_of_birth="1960-01-01", email="brownfamily@btinternet.com")
    assert "EMAIL_DOB" in shared(a, b)
    assert "first_name" in veto_reasons(RULES_BY_KEY["EMAIL_DOB"], pa, pb)


def test_dob_conflict_vetoes_name_address_match():
    _, a, _, pa = prep(first_name="Tom", last_name="Hughes", date_of_birth="1950-03-03", address={"line1": "2 B Road", "postcode": "CF10 1AA"})
    _, b, _, pb = prep(first_name="Tom", last_name="Hughes", date_of_birth="1982-07-07", address={"line1": "2 B Road", "postcode": "CF10 1AA"})
    assert "dob" in veto_reasons(RULES_BY_KEY["NAME_PREMISE_POSTCODE"], pa, pb)


# ------------------------------------------------------------------ engine
def cand(rid, cid, keys, profile, created=None):
    return Candidate(rid, cid, set(keys), profile, created)


def test_engine_picks_highest_priority_customer():
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    p = {"dob": "1980-01-01"}
    d = decide(p, [cand(1, c1, {"NAME_DOB_DISTRICT"}, p), cand(2, c2, {"EMAIL_NAME_DOB"}, p)])
    assert d.customer_id == c2 and d.rule.key == "EMAIL_NAME_DOB" and d.competing_customer_ids == []


def test_engine_falls_through_vetoed_rule():
    c1 = uuid.uuid4()
    incoming = {"first_forms": ["sarah"], "first_initial": "s"}
    other = {"first_forms": ["peter"], "first_initial": "p"}
    d = decide(incoming, [cand(1, c1, {"EMAIL_DOB"}, other)])
    assert d.customer_id is None and d.evaluations[0].vetoed == {"EMAIL_DOB": ["first_name"]}


def test_engine_tie_breaks_on_corroboration_then_age():
    old, new = uuid.uuid4(), uuid.uuid4()
    t0 = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    t1 = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    d = decide({}, [cand(1, new, {"EMAIL_NAME"}, {}, t1), cand(2, old, {"EMAIL_NAME"}, {}, t0)])
    assert d.customer_id == old and d.competing_customer_ids == [new]
    d = decide({}, [cand(1, new, {"EMAIL_NAME"}, {}, t1), cand(3, new, {"EMAIL_NAME"}, {}, t1),
                    cand(2, old, {"EMAIL_NAME"}, {}, t0)])
    assert d.customer_id == new and d.matched_record_ids == [1, 3]


# ------------------------------------------------------------------ rules added after the 200k benchmark
from app.matching.rules import damerau, first_names_conflict  # noqa: E402


def test_dob_transposition_never_conflicts():
    a, b = {"dob": "1973-03-05"}, {"dob": "1973-05-03"}
    assert veto_reasons(RULES_BY_KEY["NAME_PREMISE_POSTCODE"], a, b) == []


def test_lenient_dob_tolerates_one_wrong_field_only_on_identifier_rules():
    a, b = {"dob": "1980-06-15"}, {"dob": "1981-06-15"}
    assert veto_reasons(RULES_BY_KEY["MOBILE_NAME"], a, b) == []
    assert veto_reasons(RULES_BY_KEY["NAME_PREMISE_POSTCODE"], a, b) == ["dob"]
    assert veto_reasons(RULES_BY_KEY["MOBILE_NAME"], a, {"dob": "1990-02-15"}) == ["dob"]


def test_first_name_compatibility():
    def prof(k, forms=None):
        return {"first_key": k, "first_forms": forms or [k], "first_initial": k[0]}
    assert not first_names_conflict(prof("justin"), prof("jusyin"))        # one keystroke
    assert not first_names_conflict(prof("bill", ["bill", "william"]), prof("william"))
    assert not first_names_conflict(prof("jon"), prof("john"))             # soundex
    assert first_names_conflict(prof("sarah"), prof("simon"))
    assert first_names_conflict(prof("peter"), prof("paul"))
    assert damerau("christopher", "christohper") == 1


def test_surname_typo_with_shared_email_and_no_dob():
    _, a, _, _ = prep(first_name="Steven", last_name="Gibson", date_of_birth="05/01/1993",
                      email="steven.gibson@hotmail.co.uk")
    _, b, _, _ = prep(first_name="Steven", last_name="Gibso", email="STEVEN.GIBSON@HOTMAIL.CO.UK")
    assert "EMAIL_FIRST" in shared(a, b)


def test_forename_typo_with_shared_mobile():
    _, a, _, pa = prep(first_name="Justin", last_name="Hughes", mobile_phone="07723 861742")
    _, b, _, pb = prep(first_name="Jusyin", last_name="Hughes", mobile_phone="+44 7723 861742")
    assert "MOBILE_LAST_INITIAL" in shared(a, b)
    assert veto_reasons(RULES_BY_KEY["MOBILE_LAST_INITIAL"], pa, pb) == []


def test_couple_sharing_mobile_still_kept_apart():
    _, a, _, pa = prep(first_name="Sarah", last_name="Cole", mobile_phone="07723 861742")
    _, b, _, pb = prep(first_name="Simon", last_name="Cole", mobile_phone="07723 861742")
    assert "MOBILE_LAST_INITIAL" in shared(a, b)
    assert "first_name" in veto_reasons(RULES_BY_KEY["MOBILE_LAST_INITIAL"], pa, pb)


def test_dwelling_by_street_when_postcode_missing():
    _, a, _, _ = prep(first_name="Mary", last_name="Hill", date_of_birth="29/07/1971",
                      address={"line1": "141 Tennyson Close", "town": "London", "postcode": "SW13 0AH"})
    _, b, _, _ = prep(full_name="HILL, MARY", date_of_birth="1971-07-29",
                      address={"line1": "141 TENNYSON CL", "town": "LONDON"})
    assert "NAME_DOB_PREMISE_STREET" in shared(a, b)


def test_name_and_postcode_when_premise_detail_differs():
    _, a, _, _ = prep(first_name="Leo", last_name="Evans-Richards",
                      address={"line1": "Flat 3, Rose Court", "line2": "106 Lincoln Rd", "postcode": "LS1 0HS"})
    _, b, _, _ = prep(first_name="LEO", last_name="EVANS-RICHARDS", address={"line1": "106 Lincoln Road", "postcode": "LS1 0HS"})
    assert "NAME_POSTCODE" in shared(a, b)


@pytest.mark.parametrize("a,b", [("Ella", "Ellie"), ("Jack", "Jake"), ("Mia", "Maya")])
def test_twins_with_similar_names_are_not_auto_linked(a, b):
    base = dict(last_name="Grant", date_of_birth="2010-03-04", address={"line1": "5 Twin Street", "postcode": "LS6 1AA"})
    _, ka, _, pa = prep(first_name=a, **base)
    _, kb, _, pb = prep(first_name=b, **base)
    blocked = {"SOUNDEX_NAME_DOB_POSTCODE", "NYSIIS_NAME_DOB_PREMISE_DISTRICT"} & shared(ka, kb)
    assert blocked, "phonetic keys should still be shared"
    for rule in blocked:
        assert "first_name" in veto_reasons(RULES_BY_KEY[rule], pa, pb)


def test_one_keystroke_forename_typo_still_links_phonetically():
    base = dict(last_name="Smyth", date_of_birth="1979-02-14", address={"line1": "12 Acacia Ave", "postcode": "LS6 2AB"})
    _, ka, _, pa = prep(first_name="William", **base)
    _, kb, _, pb = prep(first_name="Wiliam", last_name="Smith", date_of_birth="1979-02-14",
                        address={"line1": "12 Acacia Ave", "postcode": "LS6 2AB"})
    assert "SOUNDEX_NAME_DOB_POSTCODE" in shared(ka, kb)
    assert veto_reasons(RULES_BY_KEY["SOUNDEX_NAME_DOB_POSTCODE"], pa, pb) == []

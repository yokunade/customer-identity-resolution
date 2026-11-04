"""Engine checks: what they find inside and across customers, and how they explain it."""

import psycopg
from psycopg.rows import dict_row

from app.review.checks import _dob_pair, _explain_set, _forename_pair
from tests.test_lookup import resolve, settings, signed_in  # noqa: F401  (fixture)


def by_key(body):
    return {c["key"]: c for c in body["checks"]}


def forms(key, *more):
    return {"key": key, "forms": [key, *more]}


def test_explanations():
    assert _dob_pair("1980-03-04", "1980-04-03") == ("Day and month swapped", True)
    assert _dob_pair("1980-03-04", "1981-03-04") == ("One part different (typing slip)", True)
    assert _dob_pair("1980-03-04", "1995-07-21") == ("Different dates", False)
    assert _forename_pair(forms("robert", "rob"), forms("rob", "robert"))[1]
    assert _forename_pair(forms("sarah"), forms("sara"))[0] == "Spelling slip"
    assert _forename_pair(forms("sarah"), forms("helen")) == ("Different forenames", False)
    # Robeert and Robbie are not alike, but both are Robert: one person.
    names = [forms("robert", "robbie"), forms("robeert"), forms("robbie", "robert")]
    assert _explain_set(names, _forename_pair)[1] is True
    assert _explain_set([forms("sarah"), forms("helen")], _forename_pair)[1] is False


def put(settings, sql, *args):
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        conn.execute(sql, args)
        conn.commit()


def test_checks_find_and_explain(settings):
    home = {"line1": "4 Elm Road", "town": "Leeds", "postcode": "LS6 2AB"}
    # One customer, DOB keyed with day and month swapped on a second record.
    a = resolve(settings, source_system="crm", source_record_id="1", first_name="Sarah", last_name="Jones",
                date_of_birth="1986-02-11", email="sarah.jones86@gmail.com")
    resolve(settings, source_system="web", source_record_id="2", first_name="Sarah", last_name="Jones",
            date_of_birth="1986-11-02", email="sarah.jones86@gmail.com")
    # A family email: mother and son, different forenames, two customers.
    resolve(settings, source_system="crm", source_record_id="3", first_name="Helen", last_name="Shaw",
            date_of_birth="1960-01-05", email="theshaws@gmail.com", address=home)
    resolve(settings, source_system="crm", source_record_id="4", first_name="Tom", last_name="Shaw",
            date_of_birth="1992-06-30", email="theshaws@gmail.com", address=home)
    # The same name and DOB on two customers with nothing else in common.
    resolve(settings, source_system="crm", source_record_id="5", first_name="Owen", last_name="Pritchard",
            date_of_birth="1979-11-02", email="owen.p@outlook.com")
    resolve(settings, source_system="web", source_record_id="6", first_name="Owen", last_name="Pritchard",
            date_of_birth="1979-11-02", mobile_phone="07700 900456")
    # A wrong link made by hand: a customer now holding two unrelated DOBs.
    b = resolve(settings, source_system="crm", source_record_id="7", first_name="Ivy", last_name="Cole",
                date_of_birth="1950-01-01", email="ivy.cole@gmail.com")
    put(settings, "UPDATE source_record SET customer_id = %s WHERE source_record_id = '7'", a.customer_id)

    mo = signed_in(settings)
    r = mo.get("/review/api/checks")
    assert r.status_code == 200, r.text
    checks = by_key(r.json())

    dob = checks["dob"]
    assert dob["found"] == 1 and dob["to_look_at"] == 1  # Sarah's swap is explained, but Ivy's DOB is not
    assert dob["examples"][0]["customers"][0]["customer_id"] == str(a.customer_id)
    assert checks["forename"]["to_look_at"] == 1
    email = checks["email"]
    assert email["found"] == 1 and email["examples"][0]["label"] == "Different people (family or household)"
    name_dob = checks["name_dob"]
    assert name_dob["found"] == 1 and name_dob["examples"][0]["label"] == "Nothing else in common to link them"
    summary = r.json()["summary"]
    assert summary["records"] == 7 and summary["customers"] == 6
    assert summary["links"] == {"new": 6, "exact": 0, "strong": 1, "probable": 0, "possible": 0}
    assert b.customer_id  # its record now sits on Sarah's customer

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        assert conn.execute("SELECT action FROM lookup_access").fetchall() == [{"action": "checks"}]
    tom = signed_in(settings, "tom@example.co.uk")
    assert tom.get("/review/api/checks").status_code == 403

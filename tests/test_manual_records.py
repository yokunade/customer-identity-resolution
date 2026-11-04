"""Records added by hand: managers check what custIDR would do with a customer's
details, then save them as a MANUAL source record that is matched like any other."""

import uuid

import psycopg
from psycopg.rows import dict_row

from tests.test_lookup import HDR, SARAH, access_log, resolve, settings, signed_in  # noqa: F401  (fixture)

FORM = {"first_name": "Sarah", "last_name": "Jones", "date_of_birth": "14/02/1986",
        "emails": ["Sarah.Jones86@googlemail.com"], "phones": ["07911 123456"],
        "address": {"line1": "12 Acacia Avenue", "town": "Leeds", "postcode": "ls6 2ab"}}


def count(settings, sql):
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        return conn.execute(sql).fetchone()["n"]


def save(client, form, token=None):
    return client.post("/review/api/records", json={**form, "submit_token": str(token or uuid.uuid4())},
                       headers=HDR)


def test_only_managers_can_add_records(settings):
    tom = signed_in(settings, "tom@example.co.uk")
    assert tom.post("/review/api/records/check", json=FORM, headers=HDR).status_code == 403
    assert save(tom, FORM).status_code == 403
    mo = signed_in(settings)
    assert mo.post("/review/api/records/check", json=FORM).status_code == 403  # no X-Requested-With


def test_check_shows_the_customer_it_would_join_and_stores_nothing(settings):
    first = resolve(settings, **SARAH)
    before = count(settings, "SELECT count(*) AS n FROM source_record")
    r = signed_in(settings).post("/review/api/records/check", json=FORM, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "matched"
    assert body["customer"]["customer_id"] == str(first.customer_id)
    assert body["rule"] and body["tier"]
    assert body["cleansed"]["emails"][0]["canonical"] == "sarahjones86@gmail.com"
    assert body["cleansed"]["address"]["postcode"] == "LS6 2AB"
    assert count(settings, "SELECT count(*) AS n FROM source_record") == before
    assert [(a["action"], a["customer_ids"]) for a in access_log(settings)] == [
        ("add_record_check", [first.customer_id])]


def test_check_for_someone_new(settings):
    resolve(settings, **SARAH)
    r = signed_in(settings).post("/review/api/records/check", headers=HDR, json={
        "first_name": "Owen", "last_name": "Pritchard", "emails": ["owen.pritchard@outlook.com"]})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "new_customer"
    assert r.json()["customer"] is None


def test_a_name_and_one_identifier_are_required(settings):
    mo = signed_in(settings)
    r = mo.post("/review/api/records/check", json={"first_name": "Sarah", "emails": ["s@gmail.com"]}, headers=HDR)
    assert r.status_code == 422 and "last name" in r.json()["detail"]
    r = mo.post("/review/api/records/check", json={"first_name": "Sarah", "last_name": "Jones"}, headers=HDR)
    assert r.status_code == 422 and "at least one" in r.json()["detail"]
    assert mo.post("/review/api/records/check", json={**FORM, "source_system": "CRM"}, headers=HDR).status_code == 422


def test_save_adds_a_manual_record_linked_like_any_other(settings):
    first = resolve(settings, **SARAH)
    mo = signed_in(settings)
    r = save(mo, {**FORM, "note": "Customer called to update details"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_system"] == "MANUAL" and body["source_record_id"] == "M-000001"
    assert body["outcome"] == "matched"
    assert body["customer_id"] == str(first.customer_id)

    view = mo.get(f"/review/api/customers/{first.customer_id}").json()
    added = [x for x in view["records"] if x["source_system"] == "MANUAL"]
    assert len(added) == 1
    assert added[0]["added_by"]["name"] == "Mo Patel"
    assert added[0]["added_by"]["note"] == "Customer called to update details"
    assert [x["added_by"] for x in view["records"] if x["source_system"] == "CRM"] == [None]

    found = mo.get("/review/api/lookup", params={"q": "MANUAL/M-000001"}).json()
    assert [c["customer_id"] for c in found["customers"]] == [str(first.customer_id)]
    assert ("add_record", [first.customer_id]) in [(a["action"], a["customer_ids"]) for a in access_log(settings)]


def test_save_numbers_records_and_creates_new_customers(settings):
    mo = signed_in(settings)
    a = save(mo, {"first_name": "Owen", "last_name": "Pritchard", "emails": ["owen.pritchard@outlook.com"]}).json()
    b = save(mo, {"first_name": "Ffion", "last_name": "Hughes", "phones": ["07700 900456"]}).json()
    assert (a["source_record_id"], b["source_record_id"]) == ("M-000001", "M-000002")
    assert a["outcome"] == b["outcome"] == "new_customer"
    assert a["customer_id"] != b["customer_id"]


def test_saving_the_same_form_twice_adds_one_record(settings):
    mo = signed_in(settings)
    token = uuid.uuid4()
    first = save(mo, FORM, token).json()
    again = save(mo, FORM, token).json()
    assert again["outcome"] == "already_saved"
    assert again["source_record_id"] == first["source_record_id"]
    assert count(settings, "SELECT count(*) AS n FROM source_record WHERE source_system = 'MANUAL'") == 1


def test_feeds_cannot_send_manual_records(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    rec = {**SARAH, "source_system": "manual", "source_record_id": "M-000001"}
    with TestClient(create_app(settings)) as c:
        r = c.post("/v1/resolve", json=rec)
        assert r.status_code == 422 and "reserved" in r.json()["detail"]
        r = c.post("/v1/resolve/batch", json=[rec, SARAH])
        results = r.json()["results"]
        assert "reserved" in results[0]["error"]
        assert results[1]["outcome"] == "new_customer"
    assert count(settings, "SELECT count(*) AS n FROM source_record WHERE source_system = 'MANUAL'") == 0

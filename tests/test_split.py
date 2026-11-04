"""Undoing a wrong link: moving source records out of a customer, either to a
new customer or back to the customer that was merged in by mistake."""

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.main import create_app
from app.schemas import MergeRequest
from app.service import ResolutionService
from tests.test_lookup import HDR, SARAH, resolve, settings, signed_in  # noqa: F401  (fixture)

ADDRESS = {"line1": "40 Byron Avenue", "town": "London", "postcode": "NW8 0RJ", "uprn": "100021234567"}
SON = {"source_system": "crm", "source_record_id": "C-7", "first_name": "Brandon", "last_name": "Wright",
       "date_of_birth": "07/10/1996", "gender": "M", "address": ADDRESS}
# The father: same name, same address, and billing holds no date of birth.
FATHER = {"source_system": "billing", "source_record_id": "BIL-1", "first_name": "Brandon", "last_name": "Wright",
          "gender": "M", "address": ADDRESS}


def split(client, customer_id, record_ids, reason="Billing record is his father"):
    return client.post(f"/review/api/customers/{customer_id}/split", headers=HDR,
                       json={"record_ids": record_ids, "reason": reason})


def customers(settings):
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        return {r["customer_id"]: r for r in conn.execute("SELECT * FROM customer").fetchall()}


def test_a_wrong_auto_link_is_moved_out_and_stays_out(settings):
    son = resolve(settings, **SON)
    father = resolve(settings, **FATHER)
    assert father.outcome == "matched" and father.match.tier == "possible"
    assert father.customer_id == son.customer_id

    r = split(signed_in(settings), son.customer_id, [father.record_id])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["customer_id"] == str(son.customer_id)
    assert body["restored"] is False and body["source_records_moved"] == 1
    moved_to = body["moved_to_customer_id"]

    # Re-sent unchanged, or with the father's DOB, it stays with its own customer.
    again = resolve(settings, **FATHER)
    assert str(again.customer_id) == moved_to and again.outcome == "existing_source_record"
    again = resolve(settings, **{**FATHER, "date_of_birth": "12/03/1968"})
    assert str(again.customer_id) == moved_to
    # And it is not suggested for review either: the pair was kept apart.
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        assert conn.execute("SELECT status FROM merge_candidate").fetchall() == [{"status": "rejected"}]


def test_the_customer_pages_show_the_split(settings):
    son = resolve(settings, **SON)
    father = resolve(settings, **FATHER)
    mo = signed_in(settings)
    moved_to = split(mo, son.customer_id, [father.record_id]).json()["moved_to_customer_id"]

    left = mo.get(f"/review/api/customers/{son.customer_id}").json()
    assert [x["source_record_id"] for x in left["records"]] == ["C-7"]
    event = left["history"][0]
    assert (event["outcome"], event["moved_to"], event["records_moved"], event["done_by"], event["reason"]) == (
        "split", moved_to, 1, "Mo Patel", "Billing record is his father")

    new = mo.get(f"/review/api/customers/{moved_to}").json()
    rec = new["records"][0]
    assert rec["source_record_id"] == "BIL-1"
    assert rec["linked_by"]["outcome"] == "split"
    assert rec["linked_by"]["by"] == "Mo Patel" and rec["linked_by"]["split_from"] == str(son.customer_id)


def test_undoing_a_merge_brings_the_merged_customer_back(settings):
    keep = resolve(settings, **SARAH)
    other = resolve(settings, **{**SARAH, "source_record_id": "C-2", "first_name": "Sophie",
                                 "date_of_birth": "01/01/1990", "email": "sophie@example.com",
                                 "mobile_phone": "07700 900111", "home_phone": None})
    second = resolve(settings, **{**SARAH, "source_system": "web", "source_record_id": "W-2",
                                  "first_name": "Sophie", "date_of_birth": "01/01/1990",
                                  "email": "sophie@example.com", "mobile_phone": "07700 900111",
                                  "home_phone": None})
    assert other.customer_id != keep.customer_id and second.customer_id == other.customer_id
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        ResolutionService(settings).merge(conn, MergeRequest(survivor_customer_id=keep.customer_id,
                                                             merged_customer_id=other.customer_id))

    r = split(signed_in(settings), keep.customer_id, [other.record_id, second.record_id], "Merged by mistake")
    assert r.status_code == 200, r.text
    assert r.json()["restored"] is True
    assert r.json()["moved_to_customer_id"] == str(other.customer_id)
    assert r.json()["source_records_moved"] == 2
    restored = customers(settings)[other.customer_id]
    assert restored["status"] == "active" and restored["merged_into"] is None

    with TestClient(create_app(settings)) as c:
        got = c.get(f"/v1/customers/{other.customer_id}").json()
        assert got["customer_id"] == str(other.customer_id)
        assert {x["source_record_id"] for x in got["source_records"]} == {"C-2", "W-2"}


def test_moving_only_part_of_a_merged_customer_makes_a_new_one(settings):
    keep = resolve(settings, **SON)
    other = resolve(settings, **{**SARAH, "source_record_id": "C-2"})
    second = resolve(settings, **{**SARAH, "source_system": "web", "source_record_id": "W-2"})
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        ResolutionService(settings).merge(conn, MergeRequest(survivor_customer_id=keep.customer_id,
                                                             merged_customer_id=other.customer_id))
    mixed = split(signed_in(settings), keep.customer_id, [keep.record_id, second.record_id]).json()
    assert mixed["restored"] is False
    assert mixed["moved_to_customer_id"] not in (str(keep.customer_id), str(other.customer_id))


def test_what_cannot_be_split(settings):
    son = resolve(settings, **SON)
    father = resolve(settings, **FATHER)
    sarah = resolve(settings, **SARAH)
    mo = signed_in(settings)
    r = split(mo, son.customer_id, [son.record_id, father.record_id])
    assert r.status_code == 409 and "must stay" in r.json()["detail"]
    r = split(mo, son.customer_id, [sarah.record_id])
    assert r.status_code == 409 and "does not belong" in r.json()["detail"]
    assert split(mo, son.customer_id, [999999]).status_code == 409
    assert split(mo, son.customer_id, [father.record_id], reason="  ").status_code == 422
    assert split(mo, son.customer_id, []).status_code == 422

    tom = signed_in(settings, "tom@example.co.uk")
    assert split(tom, son.customer_id, [father.record_id]).status_code == 403
    assert mo.post(f"/review/api/customers/{son.customer_id}/split",
                   json={"record_ids": [father.record_id], "reason": "x" * 10}).status_code == 403  # no header
    assert len(customers(settings)) == 2  # nothing moved


def test_split_through_the_api_follows_a_merged_id(settings):
    son = resolve(settings, **SON)
    father = resolve(settings, **FATHER)
    with TestClient(create_app(settings)) as c:
        r = c.post("/v1/customers/split", json={"customer_id": str(son.customer_id),
                                                "record_ids": [father.record_id], "reason": "Father, not son"})
        assert r.status_code == 200, r.text
        assert r.json()["restored"] is False
        assert c.get("/v1/source-records/billing/BIL-1").json()["customer_id"] == r.json()["moved_to_customer_id"]

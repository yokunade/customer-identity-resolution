"""Customer lookup: managers search by email, phone, postcode, name and DOB, or ID,
see the customer's full record, and every lookup is logged."""

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.config import Settings
from app.main import create_app
from app.review.auth import create_user
from app.schemas import InboundCustomer, MergeRequest
from app.service import ResolutionService

HDR = {"X-Requested-With": "review-app"}
PW = "correct-horse-battery"

SARAH = {"source_system": "crm", "source_record_id": "C-1", "first_name": "Sarah", "last_name": "Jones",
         "date_of_birth": "14/02/1986", "email": "sarah.jones86@gmail.com", "mobile_phone": "07911 123456",
         "home_phone": "0113 245 6789", "address": {"line1": "12 Acacia Avenue", "town": "Leeds",
                                                    "postcode": "LS6 2AB"}}


@pytest.fixture
def settings(clean_db):
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        conn.execute("TRUNCATE review_session, review_user, lookup_access RESTART IDENTITY CASCADE")
        conn.commit()
        create_user(conn, "mo@example.co.uk", "Mo Patel", PW, "manager")
        create_user(conn, "tom@example.co.uk", "Tom Reed", PW)
    return Settings(database_url=clean_db, key_secret="test-secret")


def signed_in(settings, email="mo@example.co.uk"):
    c = TestClient(create_app(settings), base_url="https://testserver")
    c.__enter__()
    r = c.post("/review/api/login", json={"email": email, "password": PW}, headers=HDR)
    assert r.status_code == 200, r.text
    return c


def resolve(settings, **rec):
    svc = ResolutionService(settings)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        return svc.resolve(conn, InboundCustomer(**rec))


def access_log(settings):
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        return conn.execute("SELECT * FROM lookup_access ORDER BY access_id").fetchall()


def test_only_managers_can_look_up(settings):
    first = resolve(settings, **SARAH)
    tom = signed_in(settings, "tom@example.co.uk")
    assert tom.get("/review/api/lookup", params={"q": "sarah.jones86@gmail.com"}).status_code == 403
    assert tom.get(f"/review/api/customers/{first.customer_id}").status_code == 403
    assert tom.get("/review/api/me").json()["can_lookup"] is False
    mo = signed_in(settings)
    assert mo.get("/review/api/me").json()["can_lookup"] is True
    assert mo.get("/review/api/lookup", params={"q": "sarah.jones86@gmail.com"}).status_code == 200
    with TestClient(create_app(settings), base_url="https://testserver") as anon:
        assert anon.get("/review/api/lookup", params={"q": "x@y.com"}).status_code == 401


@pytest.mark.parametrize("q,searched_by", [
    ("Sarah.Jones86+shop@googlemail.com", "email"),   # cleansed like an inbound record
    ("+44 7911 123456", "phone"),
    ("0113 2456789", "phone"),                         # landlines too, not only mobiles
    ("ls62ab", "postcode"),
    ("CRM/C-1", "source_record"),
])
def test_search_finds_the_customer_however_the_value_is_typed(settings, q, searched_by):
    first = resolve(settings, **SARAH)
    r = signed_in(settings).get("/review/api/lookup", params={"q": q})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["searched_by"] == searched_by
    assert [c["customer_id"] for c in body["customers"]] == [str(first.customer_id)]
    hit = body["customers"][0]
    assert hit["name"] == "Sarah Jones" and hit["date_of_birth"] == "1986-02-14"
    assert hit["postcode"] == "LS6 2AB" and hit["record_count"] == 1 and hit["sources"] == ["CRM"]


def test_search_by_customer_id_follows_merges(settings):
    a = resolve(settings, **SARAH)
    b = resolve(settings, source_system="web", source_record_id="9", first_name="Sally", last_name="Smith",
                email="sally.s@outlook.com")
    svc = ResolutionService(settings)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        svc.merge(conn, MergeRequest(survivor_customer_id=a.customer_id, merged_customer_id=b.customer_id,
                                     reason="test"))
    body = signed_in(settings).get("/review/api/lookup", params={"q": str(b.customer_id)}).json()
    assert body["searched_by"] == "customer_id"
    assert [c["customer_id"] for c in body["customers"]] == [str(a.customer_id)]


def test_search_by_name_and_date_of_birth(settings):
    first = resolve(settings, **SARAH)
    resolve(settings, source_system="crm", source_record_id="C-2", first_name="Sarah", last_name="Jones",
            date_of_birth="1990-05-05", email="other.sarah@gmail.com")
    mo = signed_in(settings)
    body = mo.get("/review/api/lookup", params={"q": "sarah jones", "dob": "14/02/1986"}).json()
    assert body["searched_by"] == "name_dob"
    assert [c["customer_id"] for c in body["customers"]] == [str(first.customer_id)]
    # A name alone is too broad to search on.
    r = mo.get("/review/api/lookup", params={"q": "sarah jones"})
    assert r.status_code == 422 and "date of birth" in r.json()["detail"]


def test_nothing_found_is_an_empty_list(settings):
    body = signed_in(settings).get("/review/api/lookup", params={"q": "nobody.here@gmail.com"}).json()
    assert body["customers"] == [] and body["shared"] is False


def test_shared_email_is_flagged(settings):
    shop = "orders@harrowlane.co.uk"
    names = [("Amir", "Adebayo"), ("Beth", "Brennan"), ("Carl", "Chowdhury")]
    for i, (f, sur) in enumerate(names):
        resolve(settings, source_system="s", source_record_id=str(i), first_name=f, last_name=sur,
                date_of_birth=f"1990-01-0{i + 1}", email=shop)
    tight = Settings(database_url=settings.database_url, key_secret="test-secret", shared_contact_customers=2)
    body = signed_in(tight).get("/review/api/lookup", params={"q": shop}).json()
    assert len(body["customers"]) == 3 and body["shared"] is True


def test_every_lookup_is_logged_and_a_reason_is_optional(settings):
    first = resolve(settings, **SARAH)
    mo = signed_in(settings)
    mo.get("/review/api/lookup", params={"q": "sarah.jones86@gmail.com"})
    mo.get("/review/api/lookup", params={"q": "07911123456", "reason": "Customer contact"})
    mo.get(f"/review/api/customers/{first.customer_id}", params={"reason": "Customer contact"})
    log = access_log(settings)
    assert [(r["action"], r["search_type"], r["reason"]) for r in log] == [
        ("search", "email", None), ("search", "phone", "Customer contact"), ("view_customer", None, "Customer contact")]
    assert log[0]["search_value"] == "sarahjones86@gmail.com"  # cleansed: Gmail ignores dots
    assert all(r["customer_ids"] == [first.customer_id] for r in log)
    assert {r["user_id"] for r in log} == {1}
    assert mo.get("/review/api/lookup", params={"q": "a@b.com", "reason": "x" * 201}).status_code == 422


def test_lookups_are_rate_limited_per_user(settings):
    limited = Settings(database_url=settings.database_url, key_secret="test-secret", lookup_max_per_hour=3)
    mo = signed_in(limited)
    codes = [mo.get("/review/api/lookup", params={"q": f"n{i}.lookup@gmail.com"}).status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]


def test_customer_view_shows_every_record_and_how_it_was_linked(settings):
    first = resolve(settings, **SARAH)
    second = resolve(settings, source_system="web", source_record_id="991", full_name="SARAH JONES",
                     date_of_birth="1986-02-14", email="Sarah.Jones86+shop@googlemail.com",
                     emails=["orders@harrowlane.co.uk"])
    assert second.customer_id == first.customer_id
    mo = signed_in(settings)
    r = mo.get(f"/review/api/customers/{first.customer_id}")
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["customer_id"] == str(first.customer_id) and v["status"] == "active"
    recs = v["records"]
    assert [(x["source_system"], x["source_record_id"]) for x in recs] == [("CRM", "C-1"), ("WEB", "991")]
    assert recs[0]["linked_by"] == {"outcome": "new_customer", "rule": None, "tier": None,
                                    "at": recs[0]["linked_by"]["at"]}
    assert recs[1]["linked_by"]["rule"] == "EMAIL_NAME_DOB" and recs[1]["linked_by"]["tier"] == "exact"
    assert recs[1]["raw"]["email"] == "Sarah.Jones86+shop@googlemail.com"
    assert recs[1]["cleansed"]["emails"][0]["canonical"] == "sarahjones86@gmail.com"
    assert isinstance(recs[1]["data_quality"], list)

    p = v["profile"]
    emails = {e["value"]: e for e in p["emails"]}
    assert emails["sarahjones86@gmail.com"]["sources"] == ["CRM", "WEB"]
    assert emails["orders@harrowlane.co.uk"]["sources"] == ["WEB"]
    phones = {x["value"]: x for x in p["phones"]}
    assert phones["+447911123456"]["type"] == "mobile" and phones["+441132456789"]["type"] == "landline"
    assert p["dates_of_birth"] == [{"value": "1986-02-14", "sources": ["CRM", "WEB"]}]
    assert p["addresses"][0]["postcode"] == "LS6 2AB" and p["addresses"][0]["current"] is True
    assert [e["outcome"] for e in v["history"]] == ["matched", "new_customer"]
    assert v["recent_access"][0]["display_name"] == "Mo Patel"


def test_customer_view_follows_a_merge_and_lists_open_cases(settings):
    a = resolve(settings, source_system="s", source_record_id="1", first_name="Chris", last_name="Hall",
                email="chall@gmail.com")
    b = resolve(settings, source_system="s", source_record_id="2", first_name="Chris", last_name="Hall",
                email="chris.hall@yahoo.co.uk")
    c = resolve(settings, source_system="s", source_record_id="3", first_name="Chris", last_name="Hall",
                emails=["chall@gmail.com", "chris.hall@yahoo.co.uk"])
    assert c.match.competing_customer_ids
    mo = signed_in(settings)
    v = mo.get(f"/review/api/customers/{a.customer_id}").json()
    assert len(v["open_cases"]) == 1
    assert {v["open_cases"][0]["other_customer_id"]} <= {str(a.customer_id), str(b.customer_id)}

    svc = ResolutionService(settings)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        svc.merge(conn, MergeRequest(survivor_customer_id=a.customer_id, merged_customer_id=b.customer_id,
                                     reason="test"))
    v = mo.get(f"/review/api/customers/{b.customer_id}").json()
    assert v["customer_id"] == str(a.customer_id) and v["requested_customer_id"] == str(b.customer_id)
    assert v["merged_from"] == [str(b.customer_id)]
    assert mo.get("/review/api/customers/00000000-0000-0000-0000-000000000000").status_code == 404

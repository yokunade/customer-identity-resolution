"""End-to-end tests against a real PostgreSQL database."""

import threading

import psycopg
from psycopg.rows import dict_row

from app.config import Settings
from app.schemas import InboundCustomer
from app.service import ResolutionService

WILLIAM = {
    "source_system": "crm", "source_record_id": "C-1", "title": "Mr", "first_name": "William",
    "last_name": "Smith", "date_of_birth": "14/02/1979", "email": "bill.smith@gmail.com",
    "mobile_phone": "07712 345678",
    "address": {"line1": "12 Acacia Ave", "town": "Leeds", "postcode": "LS6 2AB"},
}


def post(client, body):
    r = client.post("/v1/resolve", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_auth_required(client):
    r = client.post("/v1/resolve", json=WILLIAM, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_new_then_match_then_idempotent(client):
    first = post(client, WILLIAM)
    assert first["outcome"] == "new_customer"

    second = post(client, {
        "source_system": "web-shop", "source_record_id": "991", "full_name": "BILL SMITH",
        "date_of_birth": "1979-02-14", "email": "billsmith+promo@googlemail.com",
        "address": {"lines": ["12 ACACIA AVENUE", "LEEDS LS62AB"]},
    })
    assert second["outcome"] == "matched"
    assert second["customer_id"] == first["customer_id"]
    assert second["match"]["rule"] == "EMAIL_NAME_DOB" and second["match"]["tier"] == "exact"

    again = post(client, {**WILLIAM, "email": "w.smith@outlook.com"})
    assert again["outcome"] == "existing_source_record" and again["customer_id"] == first["customer_id"]

    cust = client.get(f"/v1/customers/{first['customer_id']}").json()
    assert len(cust["source_records"]) == 2

    look = client.get("/v1/source-records/web-shop/991").json()
    assert look["customer_id"] == first["customer_id"]


def test_household_members_kept_apart(client):
    dad = post(client, {"source_system": "crm", "source_record_id": "D", "first_name": "John", "last_name": "Smith",
                        "suffix": "Snr", "home_phone": "0113 245 6789", "email": "smiths@btinternet.com",
                        "address": {"line1": "1 Church Lane", "postcode": "LS1 1AA"}})
    son = post(client, {"source_system": "crm", "source_record_id": "S", "first_name": "John", "last_name": "Smith",
                        "suffix": "Jnr", "home_phone": "0113 245 6789", "email": "smiths@btinternet.com",
                        "address": {"line1": "1 Church Lane", "postcode": "LS1 1AA"}})
    mum = post(client, {"source_system": "crm", "source_record_id": "M", "first_name": "Mary", "last_name": "Smith",
                        "date_of_birth": "1961-05-05", "email": "smiths@btinternet.com",
                        "address": {"line1": "1 Church Lane", "postcode": "LS1 1AA"}})
    ids = {dad["customer_id"], son["customer_id"], mum["customer_id"]}
    assert len(ids) == 3
    assert son["candidates_considered"] >= 1  # they were in the pool, just not matched


def test_surname_change_matches_probable(client):
    a = post(client, {"source_system": "crm", "source_record_id": "1", "first_name": "Emma", "last_name": "Jones",
                      "date_of_birth": "1990-06-01", "address": {"line1": "Flat 2, 5 Mill Lane", "postcode": "M1 1AE"}})
    b = post(client, {"source_system": "loyalty", "source_record_id": "x", "title": "Mrs", "first_name": "Emma",
                      "last_name": "Patel", "date_of_birth": "01/06/1990",
                      "address": {"lines": ["Flat 2", "5 Mill Lane", "Manchester"], "postcode": "m11ae"}})
    assert b["customer_id"] == a["customer_id"]
    assert b["match"]["rule"] == "FIRST_DOB_PREMISE_POSTCODE" and b["match"]["tier"] == "probable"


def test_ambiguous_match_records_merge_candidate_and_merge(client, clean_db):
    # Two customers that could not be linked to each other...
    a = post(client, {"source_system": "s1", "source_record_id": "1", "first_name": "Alex", "last_name": "Green",
                      "email": "alex.green@gmail.com"})
    b = post(client, {"source_system": "s2", "source_record_id": "2", "first_name": "Alex", "last_name": "Green",
                      "mobile_phone": "07801 234567"})
    assert a["customer_id"] != b["customer_id"]
    # ...then a record that bridges both at the same tier.
    c = post(client, {"source_system": "s3", "source_record_id": "3", "first_name": "Alex", "last_name": "Green",
                      "email": "alex.green@gmail.com", "mobile_phone": "07801234567"})
    assert c["outcome"] == "matched" and c["match"]["tier"] == "strong"
    # EMAIL_NAME (60) outranks MOBILE_NAME (70), so customer A wins outright.
    assert c["customer_id"] == a["customer_id"]

    r = client.post("/v1/customers/merge", json={"survivor_customer_id": a["customer_id"],
                                                 "merged_customer_id": b["customer_id"], "reason": "test"})
    assert r.status_code == 200 and r.json()["source_records_moved"] == 1
    merged_view = client.get(f"/v1/customers/{b['customer_id']}").json()
    assert merged_view["customer_id"] == a["customer_id"] and len(merged_view["source_records"]) == 3
    # New records matching only the merged customer's data resolve to the survivor.
    d = post(client, {"source_system": "s4", "source_record_id": "4", "first_name": "Alex", "last_name": "Green",
                      "mobile_phone": "+44 7801 234567"})
    assert d["customer_id"] == a["customer_id"]


def test_tie_at_same_rule_logs_merge_candidate(client, clean_db):
    a = post(client, {"source_system": "s1", "source_record_id": "1", "first_name": "Chris", "last_name": "Hall",
                      "email": "chall@gmail.com"})
    b = post(client, {"source_system": "s2", "source_record_id": "2", "first_name": "Chris", "last_name": "Hall",
                      "email": "chris.hall@yahoo.co.uk"})
    c = post(client, {"source_system": "s3", "source_record_id": "3", "first_name": "Chris", "last_name": "Hall",
                      "emails": ["chall@gmail.com", "chris.hall@yahoo.co.uk"]})
    assert c["match"]["competing_customer_ids"] == [b["customer_id"]]
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        rows = conn.execute("SELECT * FROM merge_candidate").fetchall()
    assert len(rows) == 1 and rows[0]["rule"] == "EMAIL_NAME"


def test_cleanse_dry_run(client):
    r = client.post("/v1/cleanse", json=WILLIAM)
    body = r.json()
    assert r.status_code == 200 and body["cleansed"]["address"]["postcode"] == "LS6 2AB"
    assert body["match_keys"]["EMAIL_NAME_DOB"] >= 1 and "B_POSTCODE" in body["blocking_keys"]


def test_batch(client):
    recs = [dict(WILLIAM, source_record_id=f"B{i}") for i in range(5)]
    r = client.post("/v1/resolve/batch", json=recs)
    ids = {x["customer_id"] for x in r.json()["results"]}
    assert r.status_code == 200 and len(ids) == 1


def test_oversized_block_is_skipped(clean_db):
    svc = ResolutionService(Settings(database_url=clean_db, key_secret="test-secret", max_block_size=2))
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        for i in range(3):
            svc.resolve(conn, InboundCustomer(source_system="s", source_record_id=str(i), first_name=f"P{i}x",
                                              last_name=f"Person{i}", address={"line1": f"{i} Hall Road",
                                                                               "postcode": "LS2 9JT"}))
        res = svc.resolve(conn, InboundCustomer(source_system="s", source_record_id="z", first_name="Zed",
                                                last_name="Last", address={"line1": "9 Hall Road",
                                                                           "postcode": "LS2 9JT"}))
    assert "B_POSTCODE" in res.skipped_blocks


def test_concurrent_arrivals_get_one_customer(clean_db):
    """Ten requests for the same person arriving at once must not create duplicates."""
    svc = ResolutionService(Settings(database_url=clean_db, key_secret="test-secret"))
    results: list = []
    barrier = threading.Barrier(10)

    def worker(i: int) -> None:
        with psycopg.connect(clean_db, row_factory=dict_row) as conn:
            barrier.wait()
            r = svc.resolve(conn, InboundCustomer(**dict(WILLIAM, source_system=f"sys{i}", source_record_id="1")))
            results.append(r.customer_id)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 10 and len(set(results)) == 1


def test_tier_outside_auto_link_goes_to_review(clean_db):
    svc = ResolutionService(Settings(database_url=clean_db, key_secret="test-secret",
                                     auto_link_tiers=frozenset({"exact", "strong", "probable"})))
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        a = svc.resolve(conn, InboundCustomer(source_system="s", source_record_id="1", first_name="Mary",
                                              last_name="Edwards", date_of_birth="1973-07-16",
                                              address={"line1": "159 Stafford Court", "postcode": "EX13 0LD"}))
        b = svc.resolve(conn, InboundCustomer(source_system="s", source_record_id="2", full_name="EDWARDS, MARY",
                                              address={"line1": "159 STAFFORD CT", "postcode": "EX130LD"}))
        rows = conn.execute("SELECT rule, status FROM merge_candidate").fetchall()
    assert b.outcome == "new_customer" and b.customer_id != a.customer_id
    assert b.match.tier == "possible" and b.match.auto_linked is False
    assert b.match.suggested_customer_id == a.customer_id
    assert rows == [{"rule": "NAME_PREMISE_POSTCODE", "status": "open"}]


def test_search_customers_by_email_mobile_postcode(client):
    william = post(client, WILLIAM)
    post(client, {"source_system": "crm", "source_record_id": "N", "first_name": "Nina", "last_name": "Okafor",
                  "email": "nina@outlook.com", "address": {"line1": "14 Acacia Ave", "postcode": "LS6 2AB"}})

    # Inputs are cleansed like inbound records, so formatting differences still find the customer.
    r = client.get("/v1/customers", params={"email": "Bill.Smith+news@GoogleMail.com"}).json()
    assert r["normalised_value"] == "billsmith@gmail.com"
    assert [c["customer_id"] for c in r["customers"]] == [william["customer_id"]]
    assert r["customers"][0]["source_records"][0]["source_record_id"] == "C-1"

    r = client.get("/v1/customers", params={"mobile": "+44 (0)7712 345678"}).json()
    assert r["normalised_value"] == "+447712345678"
    assert [c["customer_id"] for c in r["customers"]] == [william["customer_id"]]

    r = client.get("/v1/customers", params={"postcode": "ls62ab"}).json()
    assert r["normalised_value"] == "LS6 2AB" and len(r["customers"]) == 2 and not r["truncated"]

    r = client.get("/v1/customers", params={"postcode": "LS6 2AB", "limit": 1}).json()
    assert len(r["customers"]) == 1 and r["truncated"]

    r = client.get("/v1/customers", params={"email": "someone.else@gmail.com"})
    assert r.status_code == 200 and r.json()["customers"] == []


def test_search_customers_rejects_bad_queries(client):
    assert client.get("/v1/customers").status_code == 422
    assert client.get("/v1/customers", params={"email": "a@b.com", "postcode": "LS6 2AB"}).status_code == 422
    assert client.get("/v1/customers", params={"postcode": "NOT A PC"}).status_code == 422
    assert client.get("/v1/customers", params={"email": "info@acme.co.uk"}).status_code == 422
    assert client.get("/v1/customers", params={"mobile": "12"}).status_code == 422
    assert client.get("/v1/customers", params={"email": "a@b.com"}, headers={"X-API-Key": "x"}).status_code == 401


YEMI = {"source_system": "crm", "source_record_id": "Y-1", "first_name": "Yemi", "last_name": "Okunade",
        "date_of_birth": "29/05/1981", "email": "yokunade@gmail.com"}


def test_corrected_resend_of_a_lone_record_relinks(client):
    first = post(client, YEMI)
    typo = post(client, {**YEMI, "source_record_id": "Y-2", "first_name": "Yime"})
    assert typo["outcome"] == "new_customer"  # Yime/Yemi conflict without a mobile to vouch for it

    fixed = post(client, {**YEMI, "source_record_id": "Y-2"})
    assert fixed["outcome"] == "relinked" and fixed["customer_id"] == first["customer_id"]
    # The stranded customer ID now follows the merge.
    old = client.get(f"/v1/customers/{typo['customer_id']}").json()
    assert old["customer_id"] == first["customer_id"] and len(old["source_records"]) == 2

    # Records with company keep their link on resend.
    again = post(client, {**YEMI, "source_record_id": "Y-2", "first_name": "Yime"})
    assert again["outcome"] == "existing_source_record" and again["customer_id"] == first["customer_id"]


def test_relink_respects_a_keep_apart_decision(client, clean_db):
    first = post(client, YEMI)
    typo = post(client, {**YEMI, "source_record_id": "Y-2", "first_name": "Yime"})
    a, b = sorted([first["customer_id"], typo["customer_id"]])
    with psycopg.connect(clean_db) as conn:
        conn.execute("INSERT INTO merge_candidate (customer_a, customer_b, rule, status)"
                     " VALUES (%s, %s, 'EMAIL_NAME_DOB', 'rejected')", (a, b))
    fixed = post(client, {**YEMI, "source_record_id": "Y-2"})
    assert fixed["outcome"] == "existing_source_record" and fixed["customer_id"] == typo["customer_id"]
    assert fixed["match"]["auto_linked"] is False

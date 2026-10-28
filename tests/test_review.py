"""Review app: sign-in, the claim/decide workflow, and its effect on the identity tables."""

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.config import Settings
from app.main import create_app
from app.review.auth import create_user, hash_password, verify_password
from app.schemas import InboundCustomer
from app.service import ResolutionService

HDR = {"X-Requested-With": "review-app"}
PW = "correct-horse-battery"


@pytest.fixture
def app_settings(clean_db):
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        conn.execute("TRUNCATE review_session, review_user RESTART IDENTITY CASCADE")
        conn.commit()
        create_user(conn, "ana@example.co.uk", "Ana Silva", PW, "admin")
        create_user(conn, "tom@example.co.uk", "Tom Reed", PW)
    return Settings(database_url=clean_db, key_secret="test-secret")


def client_for(settings, email):
    c = TestClient(create_app(settings), base_url="https://testserver")
    c.__enter__()
    r = c.post("/review/api/login", json={"email": email, "password": PW}, headers=HDR)
    assert r.status_code == 200, r.text
    return c


def make_case(db_url, settings, tag):
    """Two customers that can't be linked to each other, then a record that ties them."""
    svc = ResolutionService(settings)
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        a = svc.resolve(conn, InboundCustomer(source_system="s", source_record_id=f"{tag}1", first_name="Chris",
                                              last_name=f"Hall{tag}", email=f"chall{tag}@gmail.com"))
        b = svc.resolve(conn, InboundCustomer(source_system="s", source_record_id=f"{tag}2", first_name="Chris",
                                              last_name=f"Hall{tag}", email=f"chris.hall{tag}@yahoo.co.uk"))
        c = svc.resolve(conn, InboundCustomer(source_system="s", source_record_id=f"{tag}3", first_name="Chris",
                                              last_name=f"Hall{tag}",
                                              emails=[f"chall{tag}@gmail.com", f"chris.hall{tag}@yahoo.co.uk"]))
    assert c.match.competing_customer_ids
    return a.customer_id, b.customer_id


def test_password_hashing():
    h = hash_password("s3cret-password")
    assert h.startswith("scrypt$") and verify_password("s3cret-password", h)
    assert not verify_password("wrong", h) and not verify_password("x", "garbage")


def test_sign_in_and_protection(app_settings):
    with TestClient(create_app(app_settings), base_url="https://testserver") as c:
        assert c.get("/review").status_code == 200
        assert c.get("/review/api/me").status_code == 401
        r = c.post("/review/api/login", json={"email": "ana@example.co.uk", "password": "nope"}, headers=HDR)
        assert r.status_code == 401 and r.json()["detail"] == "Email or password is incorrect."
        # Without the custom header (e.g. a cross-site form post) state-changing calls are refused.
        assert c.post("/review/api/login", json={"email": "ana@example.co.uk", "password": PW}).status_code == 403
        r = c.post("/review/api/login", json={"email": "ANA@example.co.uk", "password": PW}, headers=HDR)
        assert r.status_code == 200
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie
        assert c.get("/review/api/me").json()["display_name"] == "Ana Silva"
        c.post("/review/api/logout", headers=HDR)
        assert c.get("/review/api/me").status_code == 401


def test_login_throttle(app_settings):
    with TestClient(create_app(app_settings), base_url="https://testserver") as c:
        for _ in range(5):
            c.post("/review/api/login", json={"email": "throttle@example.co.uk", "password": "x"}, headers=HDR)
        r = c.post("/review/api/login", json={"email": "throttle@example.co.uk", "password": "x"}, headers=HDR)
        assert r.status_code == 429


def test_claim_is_exclusive_and_held(app_settings, clean_db):
    make_case(clean_db, app_settings, "a")
    make_case(clean_db, app_settings, "b")
    ana, tom = client_for(app_settings, "ana@example.co.uk"), client_for(app_settings, "tom@example.co.uk")
    c1 = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    c2 = tom.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    assert c1 and c2 and c1 != c2
    assert ana.post("/review/api/cases/next", headers=HDR).json()["case_id"] == c1  # keeps her case
    assert tom.post("/review/api/cases/next", headers=HDR).json()["case_id"] == c2
    assert ana.get("/review/api/stats").json() == {"open": 2, "mine": 1, "decided_today": 0, "team_today": 0}
    # Tom cannot decide Ana's case.
    r = tom.post(f"/review/api/cases/{c1}/decision", json={"decision": "keep_apart", "notes": "nope", "reason_codes": ["different_dob"], "confidence": "certain"}, headers=HDR)
    assert r.status_code == 409


def test_case_detail(app_settings, clean_db):
    a, b = make_case(clean_db, app_settings, "d")
    ana = client_for(app_settings, "ana@example.co.uk")
    cid = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    c = ana.get(f"/review/api/cases/{cid}").json()
    assert c["held_by_me"] and c["rule"] == "EMAIL_NAME" and c["tier"] == "strong"
    assert {c["customer_a"]["customer_id"], c["customer_b"]["customer_id"]} == {str(a), str(b)}
    assert c["customer_a"]["created_at"] <= c["customer_b"]["created_at"]
    rows = {r["field"]: r for r in c["comparison"]}
    # The tying record joined customer A, so A now holds both emails.
    assert rows["name"]["verdict"] == "match" and rows["email"]["verdict"] == "match"
    assert c["trigger"]["source"] == "S d3"


def test_merge_decision_updates_identity_tables(app_settings, clean_db):
    a, b = make_case(clean_db, app_settings, "m")
    ana = client_for(app_settings, "ana@example.co.uk")
    cid = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]

    base = {"decision": "merge", "reason_codes": ["same_email"], "confidence": "certain"}
    r = ana.post(f"/review/api/cases/{cid}/decision", json={**base, "notes": ""}, headers=HDR)
    assert r.status_code == 422 and "note" in r.json()["detail"]
    r = ana.post(f"/review/api/cases/{cid}/decision", json={**base, "notes": "Same person"}, headers=HDR)
    assert r.status_code == 422  # survivor required
    r = ana.post(f"/review/api/cases/{cid}/decision", headers=HDR,
                 json={**base, "notes": "Same person", "reason_codes": [], "survivor_customer_id": str(a)})
    assert r.status_code == 422 and "reason" in r.json()["detail"]
    r = ana.post(f"/review/api/cases/{cid}/decision", headers=HDR,
                 json={**base, "notes": "Same person", "reason_codes": ["different_dob"], "survivor_customer_id": str(a)})
    assert r.status_code == 422  # a keep-apart reason on a merge
    r = ana.post(f"/review/api/cases/{cid}/decision", headers=HDR,
                 json={**base, "notes": "Same person", "confidence": None, "survivor_customer_id": str(a)})
    assert r.status_code == 422 and "confident" in r.json()["detail"]

    r = ana.post(f"/review/api/cases/{cid}/decision",
                 json={"decision": "merge", "notes": "Same name, both emails on one record", "survivor_customer_id": str(a),
                       "reason_codes": ["same_email", "nickname_or_typo"], "confidence": "likely"},
                 headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["survivor_customer_id"] == str(a) and r.json()["source_records_moved"] >= 1

    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        cust = conn.execute("SELECT status, merged_into FROM customer WHERE customer_id = %s", (b,)).fetchone()
        case = conn.execute("SELECT * FROM merge_candidate WHERE case_id = %s", (cid,)).fetchone()
        left = conn.execute("SELECT count(*) AS n FROM source_record WHERE customer_id = %s", (b,)).fetchone()
        ev = conn.execute("SELECT outcome, details FROM resolution_event WHERE outcome = 'review_merge'").fetchone()
    assert cust == {"status": "merged", "merged_into": a}
    assert case["status"] == "merged" and case["decision"] == "merge" and case["decided_by"] is not None
    assert case["notes"] == "Same name, both emails on one record" and case["survivor_customer_id"] == a
    assert left["n"] == 0
    assert ev["details"]["reviewer"] == "ana@example.co.uk" and ev["details"]["case_id"] == cid

    # The decided case can't be decided again, and the customer endpoint follows the merge.
    r = ana.post(f"/review/api/cases/{cid}/decision", headers=HDR,
                 json={"decision": "keep_apart", "notes": "again", "reason_codes": ["different_dob"], "confidence": "certain"})
    assert r.status_code == 409
    assert ana.get("/review/api/history").json()[0]["case_id"] == cid


def test_keep_apart_and_pair_not_resuggested(app_settings, clean_db):
    a, b = make_case(clean_db, app_settings, "k")
    tom = client_for(app_settings, "tom@example.co.uk")
    cid = tom.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    r = tom.post(f"/review/api/cases/{cid}/decision", headers=HDR,
                 json={"decision": "keep_apart", "notes": "Different DOBs on file", "reason_codes": ["different_dob"],
                       "confidence": "certain"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    # Another tying record arrives: no new case for the same pair.
    svc = ResolutionService(app_settings)
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        svc.resolve(conn, InboundCustomer(source_system="s", source_record_id="k4", first_name="Chris", last_name="Hallk",
                                          emails=["challk@gmail.com", "chris.hallk@yahoo.co.uk"]))
        n = conn.execute("SELECT count(*) AS n FROM merge_candidate").fetchone()["n"]
        cust = conn.execute("SELECT status FROM customer WHERE customer_id = %s", (b,)).fetchone()
    assert n == 1 and cust["status"] == "active"
    assert tom.post("/review/api/cases/next", headers=HDR).json()["case_id"] is None


def test_release_returns_case_to_queue(app_settings, clean_db):
    make_case(clean_db, app_settings, "r")
    ana, tom = client_for(app_settings, "ana@example.co.uk"), client_for(app_settings, "tom@example.co.uk")
    cid = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    assert tom.post("/review/api/cases/next", headers=HDR).json()["case_id"] is None
    assert ana.post(f"/review/api/cases/{cid}/release", headers=HDR).status_code == 200
    assert tom.post("/review/api/cases/next", headers=HDR).json()["case_id"] == cid


def test_expired_claim_can_be_taken(app_settings, clean_db):
    make_case(clean_db, app_settings, "e")
    ana, tom = client_for(app_settings, "ana@example.co.uk"), client_for(app_settings, "tom@example.co.uk")
    cid = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    with psycopg.connect(clean_db) as conn:
        conn.execute("UPDATE merge_candidate SET claimed_at = now() - interval '31 minutes' WHERE case_id = %s", (cid,))
    assert tom.post("/review/api/cases/next", headers=HDR).json()["case_id"] == cid


def test_merge_makes_overlapping_case_obsolete(app_settings, clean_db):
    """A, B and C are one person split three ways; cases A~B and B~C. Merging A~B
    repoints B~C to A~C; merging that leaves nothing else to decide."""
    svc = ResolutionService(app_settings)
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        def rec(i, **kw):
            return svc.resolve(conn, InboundCustomer(source_system="s", source_record_id=str(i), first_name="Dana",
                                                     last_name="Ross", **kw))
        a = rec(1, email="dana@gmail.com")
        b = rec(2, mobile_phone="07801 234567")
        c = rec(3, email="dross@yahoo.co.uk")
        rec(4, email="dana@gmail.com", emails=["dross@yahoo.co.uk"])            # ties A and C
        rec(5, mobile_phone="07801 234567", emails=["dross@yahoo.co.uk"])       # B (mobile) vs C (email)
        cases = conn.execute("SELECT case_id, customer_a, customer_b, status FROM merge_candidate ORDER BY case_id").fetchall()
    assert len(cases) >= 1
    ana = client_for(app_settings, "ana@example.co.uk")
    decided = 0
    while (cid := ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]) is not None:
        case = ana.get(f"/review/api/cases/{cid}").json()
        r = ana.post(f"/review/api/cases/{cid}/decision", headers=HDR, json={
            "decision": "merge", "notes": "Same person", "survivor_customer_id": case["customer_a"]["customer_id"],
            "reason_codes": ["same_email"], "confidence": "likely"})
        assert r.status_code == 200, r.text
        decided += 1
        assert decided < 10
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        active = conn.execute(
            "SELECT count(DISTINCT customer_id) AS n FROM source_record WHERE customer_id = ANY(%s)",
            ([a.customer_id, b.customer_id, c.customer_id],)).fetchone()["n"]
        statuses = {r["status"] for r in conn.execute("SELECT status FROM merge_candidate").fetchall()}
    assert "open" not in statuses
    assert active <= 2


def test_overview_counts_and_queue_order(app_settings, clean_db):
    make_case(clean_db, app_settings, "o")
    make_case(clean_db, app_settings, "p")
    make_case(clean_db, app_settings, "q")
    ana, tom = client_for(app_settings, "ana@example.co.uk"), client_for(app_settings, "tom@example.co.uk")
    assert tom.get("/review/api/overview").status_code == 200  # every reviewer, not just admins

    done = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    ana.post(f"/review/api/cases/{done}/decision", headers=HDR,
             json={"decision": "keep_apart", "notes": "Different people", "reason_codes": ["different_dob"],
                   "confidence": "certain"})
    held = tom.post("/review/api/cases/next", headers=HDR).json()["case_id"]

    ov = ana.get("/review/api/overview").json()
    assert (ov["waiting"], ov["in_progress"], ov["merged"], ov["kept_apart"]) == (1, 1, 0, 1)
    assert ov["open_by_tier"] == {"exact": 0, "strong": 2, "probable": 0, "possible": 0}
    assert ov["oldest_open_at"]
    # Same order as take_next; the held case shows who has it.
    assert [r["case_id"] for r in ov["queue"]] == sorted(r["case_id"] for r in ov["queue"])
    first = ov["queue"][0]
    assert first["case_id"] == held and first["held_by"] == "Tom Reed" and first["held_until"]
    assert ov["queue"][1]["held_by"] is None
    assert first["names"] == ["Chris Hallp", "Chris Hallp"] and first["tier"] == "strong"
    assert first["records"] == 3


def test_overview_requires_sign_in(app_settings):
    with TestClient(create_app(app_settings), base_url="https://testserver") as c:
        assert c.get("/review/api/overview").status_code == 401

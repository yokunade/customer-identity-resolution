"""Curated demo cases: each raises its intended review case through the real
engine, labels are balanced, and the demo answer is never shown early."""

from dataclasses import replace

import psycopg
from psycopg.rows import dict_row

from app.config import Settings
from app.review.compare import compare
from app.schemas import InboundCustomer
from app.service import ResolutionService
from benchmark.demo_scenarios import SCENARIOS, queue_order
from benchmark.seed_demo import label_cases
from tests.test_review import HDR, app_settings, client_for  # noqa: F401  (fixture re-export)

DEMO_TIERS = frozenset({"exact", "strong", "probable"})


def load(db_url, settings):
    svc = ResolutionService(settings)
    raised = {}
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        for sc in queue_order():
            before = conn.execute("SELECT coalesce(max(case_id), 0) AS m FROM merge_candidate").fetchone()["m"]
            conn.rollback()
            for _, record in sc.records:
                svc.resolve(conn, InboundCustomer(**record))
            raised[sc.key] = conn.execute("SELECT count(*) AS n FROM merge_candidate WHERE case_id > %s",
                                          (before,)).fetchone()["n"]
            conn.rollback()
        label_cases(conn, [])
        labels = conn.execute("SELECT decision, metadata FROM review_example ORDER BY case_id").fetchall()
    return raised, labels


def test_every_scenario_raises_its_cases_with_balanced_labels(clean_db):
    settings = Settings(database_url=clean_db, key_secret="test-secret", auto_link_tiers=DEMO_TIERS)
    raised, labels = load(clean_db, settings)
    assert raised == {sc.key: sc.expected_cases for sc in SCENARIOS}
    assert len(labels) == sum(sc.expected_cases for sc in SCENARIOS)
    assert all(lab["metadata"].get("scenario") and lab["metadata"].get("explanation") for lab in labels)
    merges = sum(lab["decision"] == "merge" for lab in labels)
    assert abs(merges - (len(labels) - merges)) <= 2  # roughly half and half
    # Alternating order: no long run of one answer at the front of the queue.
    seq = "".join("M" if lab["decision"] == "merge" else "K" for lab in labels)
    assert "MMM" not in seq and "KKK" not in seq


def test_demo_answer_only_after_decision(app_settings, clean_db):
    settings = replace(app_settings, auto_link_tiers=DEMO_TIERS, demo_mode=True)
    load(clean_db, settings)
    ana = client_for(settings, "ana@example.co.uk")
    cid = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    case = ana.get(f"/review/api/cases/{cid}").json()
    assert "demo_answer" not in case
    reasons = case["reason_options"]["keep_apart"]
    r = ana.post(f"/review/api/cases/{cid}/decision", headers=HDR, json={
        "decision": "keep_apart", "notes": "Two generations at one address", "confidence": "likely",
        "reason_codes": [reasons[0]["code"]]})
    answer = r.json()["demo_answer"]
    assert answer["decision"] in ("merge", "keep_apart") and answer["explanation"] and answer["title"]
    assert answer["matches"] == (answer["decision"] == "keep_apart")
    assert ana.get(f"/review/api/cases/{cid}").json()["demo_answer"] == answer


def test_demo_answer_off_outside_demo_mode(app_settings, clean_db):
    settings = replace(app_settings, auto_link_tiers=DEMO_TIERS, demo_mode=False)
    load(clean_db, settings)
    ana = client_for(settings, "ana@example.co.uk")
    cid = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    r = ana.post(f"/review/api/cases/{cid}/decision", headers=HDR, json={
        "decision": "keep_apart", "notes": "Not enough evidence", "confidence": "unsure",
        "reason_codes": ["insufficient_evidence"]})
    assert r.json()["demo_answer"] is None


def test_name_row_names_the_real_difference():
    def rec(first, last, middle=None, suffix=None):
        return {"_source": "X 1", "name": {"first_name": first, "last_name": last, "middle_names": middle,
                                            "suffix": suffix}}

    def row(a, b):
        r = compare([rec(*a)], [rec(*b)])[0]
        return r["verdict"], r["note"]

    assert row(("Robert", "Clarke", None, "Jr"), ("Robert", "Clarke")) == \
        ("close", "Same name, but only one customer has a Jr/Sr suffix")
    assert row(("James", "Robinson", "Neil"), ("James", "Robinson"))[1].endswith("middle name recorded on one side only")
    assert row(("Karen", "Fox", "Louise"), ("Karen", "Fox", "Emma"))[0] == "differs"
    assert row(("Liz", "Hartley"), ("Elizabeth", "Hartley")) == ("close", "Nickname or spelling difference")
    assert row(("O", "Grant"), ("Owen", "Grant")) == ("close", "Initial only on one side")

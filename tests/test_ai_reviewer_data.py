"""Training examples captured from review decisions, prompt rendering, masking and export."""

import json

import psycopg
import pytest
from psycopg.rows import dict_row

from app.review.cases import split_for
from app.review.export import export
from app.review.prompting import SYSTEM_PROMPT, Masker, render_case, render_target, select_few_shot
from benchmark.score_ai_reviewer import parse_output, score
from tests.test_review import HDR, app_settings, client_for, make_case  # noqa: F401  (fixture re-export)


def decide(client, cid, **body):
    payload = {"notes": "Same person: both emails appear together on the newest record",
               "reason_codes": ["same_email"], "confidence": "likely", **body}
    r = client.post(f"/review/api/cases/{cid}/decision", json=payload, headers=HDR)
    assert r.status_code == 200, r.text
    return r.json()


def examples(db_url):
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        return conn.execute("SELECT * FROM review_example ORDER BY example_id").fetchall()


def test_decision_captures_pre_merge_snapshot(app_settings, clean_db):
    a, b = make_case(clean_db, app_settings, "s")
    ana = client_for(app_settings, "ana@example.co.uk")
    cid = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    decide(ana, cid, decision="merge", survivor_customer_id=str(a))

    [ex] = examples(clean_db)
    snap = ex["snapshot"]
    assert ex["label_source"] == "reviewer" and ex["decision"] == "merge" and ex["survivor"] == "A"
    assert ex["reason_codes"] == ["same_email"] and ex["confidence"] == "likely"
    assert ex["seconds_to_decide"] is not None and ex["split"] == split_for(cid)
    # B's records were moved by the merge, but the snapshot still shows them on B.
    assert {snap["customer_a"]["customer_id"], snap["customer_b"]["customer_id"]} == {str(a), str(b)}
    assert len(snap["customer_b"]["records"]) >= 1
    assert set(snap) == {"case_id", "created_at", "rule", "rule_description", "tier", "trigger",
                         "customer_a", "customer_b", "comparison", "summary"}
    # The case row keeps the structured labels too.
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        row = conn.execute("SELECT reason_codes, confidence FROM merge_candidate WHERE case_id = %s", (cid,)).fetchone()
    assert row == {"reason_codes": ["same_email"], "confidence": "likely"}


def test_split_is_stable_and_roughly_80_10_10():
    splits = [split_for(i) for i in range(1, 5001)]
    assert splits == [split_for(i) for i in range(1, 5001)]
    share = {k: splits.count(k) / len(splits) for k in ("train", "dev", "test")}
    assert 0.77 < share["train"] < 0.83 and 0.08 < share["dev"] < 0.12 and 0.08 < share["test"] < 0.12


def test_render_and_mask(app_settings, clean_db):
    a, _ = make_case(clean_db, app_settings, "x")
    ana = client_for(app_settings, "ana@example.co.uk")
    cid = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    decide(ana, cid, decision="merge", survivor_customer_id=str(a),
           notes="Chris Hallx used challx@gmail.com and chris.hallx@yahoo.co.uk")
    [ex] = examples(clean_db)

    full = render_case(ex["snapshot"])
    assert "challx@gmail.com" in full and "Chris Hallx" in full and "EMAIL_NAME" in full

    masker = Masker()
    masked = render_case(ex["snapshot"], masker)
    assert "challx" not in masked.lower() and "hallx" not in masked.lower()
    assert "[Name 1]" in masked and "[Email 1]" in masked and "[both]" in masked
    target = json.loads(render_target(ex, masker))
    assert target["decision"] == "merge" and target["survivor"] == "A"
    assert "hallx" not in target["rationale"].lower() and "[Email" in target["rationale"]
    # Same value, same placeholder, every time.
    assert render_case(ex["snapshot"], Masker()) == masked


def test_exports_and_admin_endpoint(app_settings, clean_db):
    a, _ = make_case(clean_db, app_settings, "e")
    ana = client_for(app_settings, "ana@example.co.uk")
    cid = ana.post("/review/api/cases/next", headers=HDR).json()["case_id"]
    decide(ana, cid, decision="merge", survivor_customer_id=str(a))

    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        chat = [json.loads(x) for x in export(conn, "chat").splitlines() if x]
        evals = [json.loads(x) for x in export(conn, "eval").splitlines() if x]
        prompt = export(conn, "prompt", shots=3)
        with pytest.raises(ValueError):
            export(conn, "raw", masked=True)
        raw = [json.loads(x) for x in export(conn, "raw", masked=False).splitlines() if x]
    assert [m["role"] for m in chat[0]["messages"]] == ["system", "user", "assistant"]
    assert chat[0]["messages"][0]["content"] == SYSTEM_PROMPT
    assert chat[0]["metadata"]["rule"] == "EMAIL_NAME" and chat[0]["metadata"]["prompt_version"]
    assert evals[0]["expected"] == {"decision": "merge", "survivor": "A"} and "rationale" not in evals[0]
    assert "{case}" in prompt and ("### Example 1" in prompt) == (raw[0]["split"] == "train")
    assert raw[0]["snapshot"]["case_id"] == cid

    r = ana.get("/review/api/examples/export?format=chat")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")
    assert ana.get("/review/api/examples/summary").json()["reviewer"]
    tom = client_for(app_settings, "tom@example.co.uk")
    assert tom.get("/review/api/examples/export").status_code == 403


def test_few_shot_covers_both_decisions():
    def ex(i, decision, tier, conflict):
        return {"example_id": i, "decision": decision, "snapshot": {"tier": tier, "summary": {"conflict": conflict}}}
    pool = [ex(i, "merge", "possible", 0) for i in range(10)] + [ex(20, "keep_apart", "possible", 1)]
    picked = select_few_shot(pool, 3)
    assert len(picked) == 3 and {p["decision"] for p in picked} == {"merge", "keep_apart"}


def test_scorer():
    items = [
        {"id": "1", "expected": {"decision": "merge", "survivor": "A"}, "metadata": {"tier": "possible"}},
        {"id": "2", "expected": {"decision": "keep_apart", "survivor": None}, "metadata": {"tier": "possible"}},
        {"id": "3", "expected": {"decision": "keep_apart", "survivor": None}, "metadata": {"tier": "strong"}},
    ]
    preds = {"1": parse_output('Sure. {"decision": "merge", "survivor": "A"}'),
             "2": {"decision": "merge", "survivor": "A"}, "3": parse_output("not json")}
    s = score(items, preds)
    assert s["wrong_merges"] == 1 and s["invalid_outputs"] == 1 and s["merge_recall"] == 1.0
    assert s["keep_apart_recall"] == 0.0


def test_demo_seed_labels_cases_with_ground_truth(clean_db, monkeypatch):
    import benchmark.seed_demo as seed

    monkeypatch.setenv("DATABASE_URL", clean_db)
    monkeypatch.setenv("KEY_SECRET", "test-secret")
    monkeypatch.setenv("AUTO_LINK_TIERS", "exact,strong,probable")
    monkeypatch.setenv("DEMO_RECORDS", "600")
    with psycopg.connect(clean_db) as conn:
        conn.execute("TRUNCATE review_session, review_user RESTART IDENTITY CASCADE")
    seed.main()
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        gold = conn.execute("SELECT decision, reviewer_id FROM review_example WHERE label_source = 'synthetic_truth'").fetchall()
        open_cases = conn.execute("SELECT count(*) AS n FROM merge_candidate WHERE status = 'open'").fetchone()["n"]
        users = conn.execute("SELECT count(*) AS n FROM review_user").fetchone()["n"]
    assert users == 2 and open_cases >= len(gold) > 0
    assert all(g["reviewer_id"] is None for g in gold)
    seed.main()  # second run changes nothing
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM review_example").fetchone()["n"] == len(gold)

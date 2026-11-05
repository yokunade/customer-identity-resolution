"""Demo tuning: engine settings as JSON, re-running the demo data and scoring it."""

from dataclasses import replace
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from app import engine_config
from app.config import Settings
from app.db import Database
from app.review import tuning
from app.schemas import InboundCustomer
from app.service import ResolutionService
from tests.test_review import HDR, app_settings, client_for  # noqa: F401  (fixture re-export)


def test_config_round_trips_and_is_validated():
    base = Settings(key_secret="x")
    cfg = engine_config.current(base)
    assert all(r["mode"] == "auto" for r in cfg["rules"].values())
    assert engine_config.current(engine_config.apply(base, cfg)) == cfg

    cfg["rules"]["NAME_POSTCODE"]["mode"] = "off"
    cfg["rules"]["NAME_PREMISE_POSTCODE"]["mode"] = "review"
    cfg["rules"]["EMAIL_NAME"]["vetoes"] = ["dob"]
    cfg["max_auto_merge_records"] = 20
    tuned = engine_config.apply(base, cfg)
    assert "NAME_POSTCODE" not in {r.key for r in tuned.rules}
    assert tuned.review_rules == {"NAME_PREMISE_POSTCODE"} and tuned.max_auto_merge_records == 20
    assert engine_config.current(tuned) == cfg
    assert engine_config.changes(engine_config.current(base), cfg) == [
        "EMAIL_NAME: − gender veto", "EMAIL_NAME: − generation veto", "EMAIL_NAME: − middle_initial veto",
        "NAME_PREMISE_POSTCODE: auto → review", "NAME_POSTCODE: auto → off", "max_auto_merge_records: 50 → 20"]

    for bad in ({"rules": {"NOPE": {}}}, {"rules": {"EMAIL_NAME": {"mode": "maybe"}}},
                {"rules": {"EMAIL_NAME": {"vetoes": ["shoe_size"]}}}, {"rules": {}, "max_auto_merge_records": 0}):
        with pytest.raises(ValueError):
            engine_config.apply(base, bad)


def test_review_and_off_rules_are_obeyed(clean_db):
    home = {"line1": "4 Elm Grove", "town": "Leeds", "postcode": "LS6 2AB"}
    first = dict(source_system="a", first_name="Dana", last_name="Ross", address=home)
    base = Settings(database_url=clean_db, key_secret="test-secret")
    cfg = engine_config.current(base)
    cfg["rules"]["NAME_PREMISE_POSTCODE"]["mode"] = "review"
    svc = ResolutionService(engine_config.apply(base, cfg))
    with psycopg.connect(clean_db, row_factory=dict_row) as conn:
        svc.resolve(conn, InboundCustomer(source_record_id="1", **first))
        r = svc.resolve(conn, InboundCustomer(source_record_id="2", **first))
        assert r.outcome == "new_customer" and r.match.rule == "NAME_PREMISE_POSTCODE" and not r.match.auto_linked
        for key in ("NAME_PREMISE_POSTCODE", "NAME_PREMISE_STREET", "NAME_LASTSOUNDEX_PREMISE_POSTCODE",
                    "NAME_POSTCODE", "INITIAL_LAST_PREMISE_POSTCODE", "UPRN_NAME"):
            cfg["rules"][key]["mode"] = "off"
        off = ResolutionService(engine_config.apply(base, cfg))
        r = off.resolve(conn, InboundCustomer(source_record_id="3", **first))
        assert r.outcome == "new_customer" and r.match is None


@pytest.fixture
def demo(app_settings):  # noqa: F811
    with psycopg.connect(app_settings.database_url) as conn:
        conn.execute("TRUNCATE demo_truth, tuning_run RESTART IDENTITY")
        conn.commit()
    settings = replace(app_settings, demo_mode=True)
    db = Database(settings.database_url, 1, 2)
    app = SimpleNamespace(state=SimpleNamespace(db=db, service=ResolutionService(settings)))
    yield settings, app
    db.close()


def _run(app, config, note, user_id=1):
    tuned = engine_config.apply(app.state.service.settings, config)
    with app.state.db.connection() as conn, conn.transaction():
        run_id = conn.execute("INSERT INTO tuning_run (started_by, note, config) VALUES (%s, %s, %s) RETURNING run_id",
                              (user_id, note, psycopg.types.json.Jsonb(engine_config.current(tuned)))
                              ).fetchone()["run_id"]
    tuning.run_job(app, run_id, tuned, n_records=400)
    return run_id


def test_runs_score_the_demo_and_compare_person_by_person(demo):
    settings, app = demo
    cfg = engine_config.current(settings)
    first = _run(app, cfg, "Baseline")
    with app.state.db.connection() as conn:
        d = tuning.run_detail(conn, first)
    assert d["status"] == "done", d["error"]
    s = d["scores"]
    assert s["records"] > 400 and 0.9 < s["precision"] <= 1 and 0.5 < s["recall"] <= 1
    assert s["people_correct"] + s["people_split"] + s["people_mixed"] == s["people"]
    assert s["open_cases"] == s["cases_same"] + s["cases_different"] + s["cases_mixed"] > 0
    assert d["previous"] is None and d["rules"]["EMAIL_NAME_DOB"]["links"] > 0

    # Send every possible-tier match to review: fewer links, more people split.
    for r in engine_config.describe()["rules"]:
        if r["tier"] == "possible":
            cfg["rules"][r["key"]]["mode"] = "review"
    second = _run(app, cfg, "Possible to review")
    with app.state.db.connection() as conn:
        d = tuning.run_detail(conn, second)
        page = tuning.page(conn, app.state.service.settings)
    assert d["previous"]["run_id"] == first
    assert d["scores"]["people_split"] > d["previous"]["scores"]["people_split"]
    assert "UPRN_NAME: auto → review" in d["changes"]
    broken = d["changed"]["broken"]["examples"][0]
    assert broken["before"] == "correct" and broken["after"] == "split" and broken["customer_id"]
    # The app now resolves with the run's settings.
    assert "NAME_POSTCODE" in app.state.service.settings.review_rules
    assert page["config"]["rules"]["NAME_POSTCODE"]["mode"] == "review"
    assert [r["run_id"] for r in page["runs"]] == [second, first]

    with app.state.db.connection() as conn:
        system, rid = broken["record"]
        found = tuning.find(conn, rid)
        assert found["customer_id"] and [x["state"] for x in found["runs"]] == ["correct", "split"]
        assert tuning.find(conn, f"{system}/{rid}")["source_system"] == system
        with pytest.raises(tuning.TuningError):
            tuning.find(conn, "NOPE-1")
        # A restart picks up the last run's settings.
        assert "NAME_POSTCODE" in tuning.on_start(conn, settings).review_rules


def test_tuning_api_is_for_admins_in_demo_mode(demo):
    settings, _ = demo
    ana = client_for(settings, "ana@example.co.uk")
    assert ana.get("/review/api/me").json()["can_tune"] is True
    page = ana.get("/review/api/tuning").json()
    assert page["runs"] == [] and len(page["catalogue"]["rules"]) == len(page["config"]["rules"])
    bad = ana.post("/review/api/tuning/runs", headers=HDR, json={"config": {"rules": {"X": {}}}})
    assert bad.status_code == 422 and "unknown rules" in bad.json()["detail"]
    assert ana.post("/review/api/tuning/runs", json={"config": page["config"]}).status_code == 403  # no CSRF header
    tom = client_for(settings, "tom@example.co.uk")
    assert tom.get("/review/api/tuning").status_code == 403
    off = client_for(replace(settings, demo_mode=False), "ana@example.co.uk")
    assert off.get("/review/api/tuning").status_code == 404 and off.get("/review/api/me").json()["can_tune"] is False
    for c in (ana, tom, off):
        c.__exit__(None, None, None)

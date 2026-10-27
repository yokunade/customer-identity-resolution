import os

import psycopg
import pytest
from psycopg.rows import dict_row

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/identity_test")


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DATABASE_URL, connect_timeout=2):
            return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def db_url():
    if not _db_available():
        pytest.skip(f"PostgreSQL not reachable at {TEST_DATABASE_URL}")
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    return TEST_DATABASE_URL


@pytest.fixture
def clean_db(db_url):
    from app.db import migrate

    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        migrate(conn)
        conn.execute("TRUNCATE customer, source_record, match_key, blocking_key, resolution_event,"
                     " merge_candidate RESTART IDENTITY CASCADE")
        conn.commit()
    return db_url


@pytest.fixture
def client(clean_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings(database_url=clean_db, key_secret="test-secret", api_keys=frozenset({"k1"}))
    with TestClient(create_app(settings), headers={"X-API-Key": "k1"}) as c:
        yield c

"""Database connectivity and the migration runner."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

try:  # psycopg_pool is optional; fall back to a connection per request.
    from psycopg_pool import ConnectionPool
except ImportError:  # pragma: no cover - depends on environment
    ConnectionPool = None  # type: ignore[assignment,misc]


class Database:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 10) -> None:
        self.url = url
        self._pool = None
        if ConnectionPool is not None:
            self._pool = ConnectionPool(
                url,
                min_size=min_size,
                max_size=max_size,
                kwargs={"row_factory": dict_row, "autocommit": False},
                open=True,
            )
        else:
            log.warning("psycopg_pool not installed; opening a connection per request")

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        if self._pool is not None:
            with self._pool.connection() as conn:
                yield conn
        else:
            with psycopg.connect(self.url, row_factory=dict_row, autocommit=False) as conn:
                yield conn

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply any SQL files in app/migrations that have not been applied yet."""
    applied: list[str] = []
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        # Serialise concurrent start-ups.
        conn.execute("SELECT pg_advisory_xact_lock(727274)")
        done = {r["version"] if isinstance(r, dict) else r[0]
                for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in done:
                continue
            log.info("applying migration %s", path.name)
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
            applied.append(path.stem)
    return applied


if __name__ == "__main__":  # python -m app.db
    from app.config import get_settings

    logging.basicConfig(level="INFO")
    with psycopg.connect(get_settings().database_url, row_factory=dict_row) as c:
        print("applied:", migrate(c) or "nothing to do")

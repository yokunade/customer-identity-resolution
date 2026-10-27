"""Reviewer accounts and sessions.

Passwords are hashed with scrypt (standard library). Sessions are random
tokens held in an HttpOnly cookie; only their SHA-256 is stored, so a
database leak does not leak usable sessions, and deleting the row revokes it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import psycopg

SESSION_HOURS = 12
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


@dataclass(frozen=True)
class User:
    user_id: int
    email: str
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    b64 = base64.b64encode
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt).decode()}${b64(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


# A dummy hash so unknown emails take as long as wrong passwords.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def authenticate(conn: psycopg.Connection, email: str, password: str) -> User | None:
    row = conn.execute(
        "SELECT user_id, email, display_name, role, password_hash FROM review_user"
        " WHERE email = %s AND active", (email.strip().lower(),),
    ).fetchone()
    conn.rollback()  # end the read so later writes start a real transaction
    if row is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return User(row["user_id"], row["email"], row["display_name"], row["role"])


def create_session(conn: psycopg.Connection, user: User) -> str:
    token = secrets.token_urlsafe(32)
    with conn.transaction():
        conn.execute("DELETE FROM review_session WHERE expires_at < now()")
        conn.execute(
            "INSERT INTO review_session (token_hash, user_id, expires_at)"
            " VALUES (%s, %s, now() + make_interval(hours => %s))",
            (_token_hash(token), user.user_id, SESSION_HOURS),
        )
    return token


def user_for_token(conn: psycopg.Connection, token: str | None) -> User | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT u.user_id, u.email, u.display_name, u.role FROM review_session s"
        " JOIN review_user u USING (user_id)"
        " WHERE s.token_hash = %s AND s.expires_at > now() AND u.active",
        (_token_hash(token),),
    ).fetchone()
    conn.rollback()
    return User(row["user_id"], row["email"], row["display_name"], row["role"]) if row else None


def end_session(conn: psycopg.Connection, token: str | None) -> None:
    if token:
        with conn.transaction():
            conn.execute("DELETE FROM review_session WHERE token_hash = %s", (_token_hash(token),))


def create_user(conn: psycopg.Connection, email: str, display_name: str, password: str,
                role: str = "reviewer") -> int:
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO review_user (email, display_name, password_hash, role) VALUES (%s, %s, %s, %s)"
            " RETURNING user_id",
            (email.strip().lower(), display_name.strip(), hash_password(password), role),
        ).fetchone()
    return row["user_id"]


class LoginThrottle:
    """At most `limit` failed logins per email per `window` seconds (per process)."""

    def __init__(self, limit: int = 5, window: float = 300.0) -> None:
        self.limit, self.window = limit, window
        self._fails: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        with self._lock:
            q = self._fails[key]
            while q and q[0] < time.monotonic() - self.window:
                q.popleft()
            return len(q) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)

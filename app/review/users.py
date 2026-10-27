"""Manage reviewer accounts.

    python -m app.review.users add --email ana@example.co.uk --name "Ana Silva" [--admin]
    python -m app.review.users list
    python -m app.review.users disable --email ana@example.co.uk
    python -m app.review.users password --email ana@example.co.uk
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from app.db import migrate
from app.review.auth import create_user, hash_password


def _password(args: argparse.Namespace) -> str:
    pw = os.getenv("REVIEW_PASSWORD") or getpass.getpass("Password (10+ characters): ")
    if not os.getenv("REVIEW_PASSWORD") and getpass.getpass("Repeat password: ") != pw:
        sys.exit("Passwords don't match.")
    return pw


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.review.users")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--email", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--admin", action="store_true")
    sub.add_parser("list")
    d = sub.add_parser("disable")
    d.add_argument("--email", required=True)
    p = sub.add_parser("password")
    p.add_argument("--email", required=True)
    args = ap.parse_args()

    with psycopg.connect(get_settings().database_url, row_factory=dict_row) as conn:
        migrate(conn)
        if args.cmd == "add":
            try:
                uid = create_user(conn, args.email, args.name, _password(args), "admin" if args.admin else "reviewer")
            except (ValueError, psycopg.errors.UniqueViolation) as exc:
                sys.exit(f"Not created: {exc}")
            print(f"Created user {uid} ({args.email})")
        elif args.cmd == "list":
            for r in conn.execute("SELECT user_id, email, display_name, role, active FROM review_user ORDER BY user_id"):
                print(f'{r["user_id"]:>4}  {r["email"]:<32} {r["display_name"]:<24} {r["role"]:<9} '
                      f'{"active" if r["active"] else "disabled"}')
        elif args.cmd == "disable":
            with conn.transaction():
                n = conn.execute("UPDATE review_user SET active = false WHERE email = %s",
                                 (args.email.lower(),)).rowcount
                conn.execute("DELETE FROM review_session WHERE user_id IN"
                             " (SELECT user_id FROM review_user WHERE email = %s)", (args.email.lower(),))
            print("Disabled" if n else "No such user")
        elif args.cmd == "password":
            pw = _password(args)
            if len(pw) < 10:
                sys.exit("Password must be at least 10 characters.")
            with conn.transaction():
                n = conn.execute("UPDATE review_user SET password_hash = %s WHERE email = %s",
                                 (hash_password(pw), args.email.lower())).rowcount
                conn.execute("DELETE FROM review_session WHERE user_id IN"
                             " (SELECT user_id FROM review_user WHERE email = %s)", (args.email.lower(),))
            print("Password updated; existing sessions signed out" if n else "No such user")


if __name__ == "__main__":
    main()

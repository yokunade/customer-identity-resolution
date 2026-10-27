"""Review app: JSON API under /review/api and the single-page UI under /review."""

# No "from __future__ import annotations": FastAPI resolves the Annotated aliases at runtime.

from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.review import auth, cases
from app.review import export as review_export
from app.review.auth import LoginThrottle, User

STATIC = Path(__file__).parent / "static"
COOKIE = "cir_review_session"
_throttle = LoginThrottle()


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class DecisionBody(BaseModel):
    decision: Literal["merge", "keep_apart"]
    notes: str = Field(max_length=4000)
    survivor_customer_id: UUID | None = None
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    confidence: Literal["certain", "likely", "unsure"] | None = None


def build_router(cookie_secure: bool) -> APIRouter:
    router = APIRouter()

    def get_conn(request: Request):
        with request.app.state.db.connection() as conn:
            yield conn

    Conn = Annotated[psycopg.Connection, Depends(get_conn)]

    def current_user(request: Request, conn: Conn) -> User:
        user = auth.user_for_token(conn, request.cookies.get(COOKIE))
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
        return user

    def same_origin(x_requested_with: Annotated[str | None, Header()] = None) -> None:
        # Cross-site forms cannot set custom headers; with SameSite=Strict cookies this blocks CSRF.
        if x_requested_with != "review-app":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing X-Requested-With header.")

    CurrentUser = Annotated[User, Depends(current_user)]
    write = [Depends(same_origin)]

    def case_error(exc: cases.CaseError) -> HTTPException:
        return HTTPException(exc.status, exc.message)

    # ------------------------------------------------------------------ UI
    @router.get("/review", include_in_schema=False)
    @router.get("/review/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @router.get("/review/static/{name}", include_in_schema=False)
    def static_file(name: str) -> FileResponse:
        path = (STATIC / name).resolve()
        if path.parent != STATIC.resolve() or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    # ------------------------------------------------------------------ auth
    @router.post("/review/api/login", dependencies=write, tags=["review"])
    def login(body: LoginBody, conn: Conn) -> JSONResponse:
        key = body.email.strip().lower()
        if _throttle.blocked(key):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "Too many failed sign-ins. Wait five minutes and try again.")
        user = auth.authenticate(conn, body.email, body.password)
        if user is None:
            _throttle.fail(key)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect.")
        _throttle.reset(key)
        token = auth.create_session(conn, user)
        resp = JSONResponse({"email": user.email, "display_name": user.display_name, "role": user.role})
        resp.set_cookie(COOKIE, token, httponly=True, secure=cookie_secure, samesite="strict",
                        max_age=auth.SESSION_HOURS * 3600, path="/review")
        return resp

    @router.post("/review/api/logout", dependencies=write, tags=["review"])
    def logout(request: Request, response: Response, conn: Conn) -> dict:
        auth.end_session(conn, request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/review")
        return {"ok": True}

    @router.get("/review/api/me", tags=["review"])
    def me(user: CurrentUser) -> dict:
        return {"email": user.email, "display_name": user.display_name, "role": user.role}

    # ------------------------------------------------------------------ cases
    @router.get("/review/api/stats", tags=["review"])
    def get_stats(user: CurrentUser, conn: Conn) -> dict:
        return cases.stats(conn, user)

    @router.post("/review/api/cases/next", dependencies=write, tags=["review"])
    def take_next(user: CurrentUser, conn: Conn) -> dict:
        return {"case_id": cases.take_next(conn, user)}

    @router.get("/review/api/cases/{case_id}", tags=["review"])
    def get_case(case_id: int, request: Request, user: CurrentUser, conn: Conn) -> dict:
        try:
            case = cases.get_case(conn, user, case_id)
        except cases.CaseError as exc:
            raise case_error(exc) from exc
        # Never reveal a demo answer before the case is decided.
        if request.app.state.service.settings.demo_mode and case["status"] != "open":
            case["demo_answer"] = cases.demo_answer(conn, case_id, case["decision"])
            conn.rollback()
        return case

    @router.post("/review/api/cases/{case_id}/decision", dependencies=write, tags=["review"])
    def decide(case_id: int, body: DecisionBody, request: Request, user: CurrentUser, conn: Conn) -> dict:
        try:
            return cases.decide(conn, request.app.state.service.settings, user, case_id,
                                decision=body.decision, notes=body.notes,
                                survivor_customer_id=body.survivor_customer_id,
                                reason_codes=body.reason_codes, confidence=body.confidence)
        except cases.CaseError as exc:
            raise case_error(exc) from exc

    @router.post("/review/api/cases/{case_id}/release", dependencies=write, tags=["review"])
    def release(case_id: int, user: CurrentUser, conn: Conn) -> dict:
        try:
            cases.release(conn, user, case_id)
        except cases.CaseError as exc:
            raise case_error(exc) from exc
        return {"ok": True}

    @router.get("/review/api/history", tags=["review"])
    def get_history(user: CurrentUser, conn: Conn, everyone: bool = False) -> list[dict]:
        return cases.history(conn, user, everyone=everyone and user.is_admin)

    # ------------------------------------------------------------------ training data (admins)
    def require_admin(user: CurrentUser) -> User:
        if not user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can export training data.")
        return user

    Admin = Annotated[User, Depends(require_admin)]

    @router.get("/review/api/examples/summary", tags=["review"])
    def examples_summary(user: Admin, conn: Conn) -> dict:
        rows = conn.execute(
            "SELECT label_source, split, count(*) AS n FROM review_example GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        conn.rollback()
        out: dict = {}
        for r in rows:
            out.setdefault(r["label_source"], {})[r["split"]] = r["n"]
        return out

    @router.get("/review/api/examples/export", tags=["review"])
    def examples_export(user: Admin, conn: Conn,
                        format: Literal["chat", "eval", "raw", "prompt"] = "chat",
                        split: Literal["train", "dev", "test", "all"] = "all",
                        source: Literal["reviewer", "synthetic_truth", "all"] = "reviewer",
                        pii: Literal["masked", "full"] = "masked", shots: int = 6) -> Response:
        try:
            text = review_export.export(conn, format, split=split, source=source, masked=pii == "masked",
                                        shots=max(1, min(shots, 20)))
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        ext = "md" if format == "prompt" else "jsonl"
        name = f"review-{format}-{source}-{split}-{pii}.{ext}"
        return Response(text, media_type="text/markdown" if ext == "md" else "application/x-ndjson",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    return router

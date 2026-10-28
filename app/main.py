"""HTTP API."""

# No "from __future__ import annotations": FastAPI must resolve the locally
# defined Annotated dependency aliases at runtime.

import logging
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Iterator
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status

from app.config import Settings, get_settings
from app.db import Database, migrate
from app.repository import Repository
from app.review.routes import build_router as build_review_router
from app.schemas import (
    BatchResponse,
    CleanseResponse,
    CustomerResponse,
    CustomerSearchResponse,
    InboundCustomer,
    MergeRequest,
    MergeResponse,
    ResolveResponse,
    SourceLookupResponse,
)
from app.service import MergeError, ResolutionService, SearchError

log = logging.getLogger("identity")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if settings.key_secret == "change-me":
        log.warning("KEY_SECRET is the default value; set a real secret before storing data")
    if not settings.api_keys:
        log.warning("API_KEYS is empty; authentication is disabled")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url, settings.pool_min_size, settings.pool_max_size)
        if settings.auto_migrate:
            with db.connection() as conn:
                applied = migrate(conn)
                if applied:
                    log.info("migrations applied: %s", applied)
        app.state.db = db
        app.state.service = ResolutionService(settings)
        yield
        db.close()

    app = FastAPI(
        title="Customer Identity Resolution",
        version="0.1.0",
        description="Cleanses inbound customer records to UK standards and resolves them to a persistent customer ID.",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------ dependencies
    def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if not settings.api_keys:
            return
        if not x_api_key or not any(secrets.compare_digest(x_api_key, k) for k in settings.api_keys):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-API-Key")

    def get_conn(request: Request) -> Iterator[psycopg.Connection]:
        with request.app.state.db.connection() as conn:
            yield conn

    def get_service(request: Request) -> ResolutionService:
        return request.app.state.service

    Conn = Annotated[psycopg.Connection, Depends(get_conn)]
    Service = Annotated[ResolutionService, Depends(get_service)]
    auth = [Depends(require_api_key)]

    # ------------------------------------------------------------------ routes
    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready(conn: Conn) -> dict:
        conn.execute("SELECT 1")
        conn.rollback()
        return {"status": "ready"}

    @app.post("/v1/resolve", response_model=ResolveResponse, dependencies=auth, tags=["resolution"],
              summary="Cleanse a customer record and return its customer ID")
    def resolve(record: InboundCustomer, conn: Conn, svc: Service) -> ResolveResponse:
        return svc.resolve(conn, record)

    @app.post("/v1/resolve/batch", response_model=BatchResponse, dependencies=auth, tags=["resolution"],
              summary="Resolve many records in order; each is its own transaction")
    def resolve_batch(records: list[InboundCustomer], conn: Conn, svc: Service) -> BatchResponse:
        if len(records) > settings.max_batch_size:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                f"batch limit is {settings.max_batch_size} records")
        results: list = []
        for rec in records:
            try:
                results.append(svc.resolve(conn, rec))
            except Exception as exc:  # one bad record must not sink the batch
                log.exception("batch item failed: %s/%s", rec.source_system, rec.source_record_id)
                results.append({"source_system": rec.source_system, "source_record_id": rec.source_record_id,
                                "error": type(exc).__name__})
        return BatchResponse(results=results)

    @app.post("/v1/cleanse", response_model=CleanseResponse, dependencies=auth, tags=["resolution"],
              summary="Dry run: cleanse and generate keys without storing or matching")
    def cleanse_only(record: InboundCustomer, svc: Service) -> CleanseResponse:
        return svc.dry_run(record)

    @app.get("/v1/customers", response_model=CustomerSearchResponse, dependencies=auth, tags=["customers"],
             summary="Find customers by email, mobile or postcode (exactly one)")
    def search_customers(
        conn: Conn, svc: Service,
        email: Annotated[str | None, Query()] = None,
        mobile: Annotated[str | None, Query(description="Matches the number on any phone field")] = None,
        postcode: Annotated[str | None, Query(description="UK postcode; includes previous addresses")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> CustomerSearchResponse:
        given = {k: v for k, v in (("email", email), ("mobile", mobile), ("postcode", postcode)) if v and v.strip()}
        if len(given) != 1:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "give exactly one of email, mobile, postcode")
        [(field, value)] = given.items()
        try:
            return svc.search_customers(conn, field, value, limit)
        except SearchError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field}: {exc}") from exc
        finally:
            conn.rollback()

    @app.get("/v1/customers/{customer_id}", response_model=CustomerResponse, dependencies=auth, tags=["customers"])
    def get_customer(customer_id: UUID, conn: Conn, svc: Service) -> CustomerResponse:
        cust = svc.get_customer(conn, customer_id)
        conn.rollback()
        if cust is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
        return cust

    @app.get("/v1/source-records/{source_system}/{source_record_id}", response_model=SourceLookupResponse,
             dependencies=auth, tags=["customers"], summary="Look up the customer ID for a source record")
    def lookup_source(source_system: str, source_record_id: str, conn: Conn) -> SourceLookupResponse:
        row = Repository(conn).get_source_lookup(source_system.strip().upper(), source_record_id.strip())
        conn.rollback()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "source record not found")
        return SourceLookupResponse(source_system=source_system.upper(), source_record_id=source_record_id,
                                    record_id=row["record_id"], customer_id=row["customer_id"])

    @app.post("/v1/customers/merge", response_model=MergeResponse, dependencies=auth, tags=["customers"],
              summary="Merge two customers (e.g. after reviewing a merge candidate)")
    def merge(req: MergeRequest, conn: Conn, svc: Service) -> MergeResponse:
        if req.survivor_customer_id == req.merged_customer_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "cannot merge a customer into itself")
        try:
            return svc.merge(conn, req)
        except MergeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    app.include_router(build_review_router(settings.review_cookie_secure))
    return app


app = create_app()

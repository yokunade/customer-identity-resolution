"""Resolution orchestration: cleanse -> keys -> block -> match -> assign ID."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from uuid import UUID

import psycopg

from app.cleansing import cleanse
from app.cleansing.email import cleanse_email
from app.cleansing.phone import cleanse_phone
from app.cleansing.postcode import parse_postcode
from app.config import Settings
from app.matching.engine import Candidate, Decision, decide
from app.matching.keys import (
    MATCH_KEYS,
    Components,
    KeySet,
    blocking_key_value,
    build_blocking_keys,
    extract_components,
    key_counts,
    match_profile,
    shared_match_keys,
    stored_components,
)
from app.repository import Repository, lock_id
from app.schemas import (
    CleanseResponse,
    CleansedRecord,
    CustomerResponse,
    CustomerSearchResponse,
    InboundCustomer,
    MatchDetail,
    MergeRequest,
    MergeResponse,
    ResolveResponse,
    SourceRecordSummary,
)

log = logging.getLogger(__name__)


class MergeError(ValueError):
    pass


class SearchError(ValueError):
    pass


@dataclass
class Prepared:
    inbound: InboundCustomer
    cleansed: object
    issues: list
    profile: dict
    components: Components
    match_key_counts: dict[str, int]
    blocking_keys: KeySet


@dataclass
class Found:
    pool: list[int]
    skipped: list[str]
    candidates: int
    decision: Decision
    auto_link: bool
    ms: float  # time spent finding and scoring candidates


class ResolutionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------ pure steps
    def prepare(self, inbound: InboundCustomer) -> Prepared:
        cleansed, issues = cleanse(inbound)
        comps = extract_components(cleansed)
        return Prepared(
            inbound=inbound,
            cleansed=cleansed,
            issues=issues,
            profile=match_profile(cleansed),
            components=comps,
            match_key_counts=key_counts(comps, MATCH_KEYS),
            blocking_keys=build_blocking_keys(comps, self.settings.key_secret),
        )

    def dry_run(self, inbound: InboundCustomer) -> CleanseResponse:
        p = self.prepare(inbound)
        return CleanseResponse(
            cleansed=p.cleansed,  # type: ignore[arg-type]
            data_quality=p.issues,
            match_keys=p.match_key_counts,
            blocking_keys={k: len(v) for k, v in p.blocking_keys.items()},
        )

    # ------------------------------------------------------------------ resolve
    def resolve(self, conn: psycopg.Connection, inbound: InboundCustomer) -> ResolveResponse:
        started = time.perf_counter()
        p = self.prepare(inbound)
        c = p.cleansed
        repo = Repository(conn)
        raw = inbound.model_dump(mode="json", exclude_none=True)
        cleansed_json = c.model_dump(mode="json")  # type: ignore[attr-defined]
        issues_json = [i.model_dump() for i in p.issues]
        components_json = stored_components(p.components)
        n_keys = sum(p.match_key_counts.values())

        with conn.transaction():
            # Any two records that could match share at least one blocking key
            # (proved in tests), so locking every blocking key serialises
            # concurrent arrivals of the same person without a global lock.
            source_lock = lock_id(hashlib.sha256(f"{c.source_system}\x1f{c.source_record_id}".encode()).digest())
            repo.advisory_locks([source_lock, *(lock_id(v) for vals in p.blocking_keys.values() for v in vals)])

            existing = repo.find_source_record(c.source_system, c.source_record_id)  # type: ignore[attr-defined]
            relinked_from = None
            if existing:
                record_id = existing["record_id"]
                customer_id = repo.survivor(existing["customer_id"])
                repo.update_source_record(record_id, raw=raw, cleansed=cleansed_json, profile=p.profile,
                                          components=components_json, issues=issues_json)
                repo.insert_blocking_keys(record_id, p.blocking_keys)
                repo.touch_customer(customer_id)
                outcome = "existing_source_record"
                if repo.record_count(customer_id) > 1:
                    # Other records rest on this link; never re-link silently.
                    repo.log_event(record_id=record_id, customer_id=customer_id, outcome=outcome,
                                   rule=None, details={})
                    return ResolveResponse(
                        customer_id=customer_id, outcome=outcome, record_id=record_id,
                        match_keys_generated=n_keys, data_quality=p.issues, cleansed=c,  # type: ignore[arg-type]
                        processing_ms=round((time.perf_counter() - started) * 1000, 2),
                    )
                # The record is its customer's only one, so a corrected field
                # (e.g. a forename typo fixed) gets to match again. The move is a
                # merge, so callers holding the old customer ID are followed on.
                found = self._match(repo, p, exclude_record_id=record_id)
                decision = found.decision
                auto_link = found.auto_link and not repo.pair_rejected(customer_id, decision.customer_id)
                if auto_link:
                    winner: UUID = decision.customer_id  # type: ignore[assignment]
                    repo.advisory_locks([lock_id(customer_id.bytes), lock_id(winner.bytes)])
                    repo.merge_customers(winner, customer_id)
                    relinked_from, customer_id, outcome = customer_id, winner, "relinked"
            else:
                found = self._match(repo, p)
                decision = found.decision
                auto_link = found.auto_link
                if auto_link:
                    customer_id = decision.customer_id  # type: ignore[assignment]
                    outcome = "matched"
                    repo.touch_customer(customer_id)
                else:
                    customer_id = repo.create_customer()
                    outcome = "new_customer"

                record_id = repo.insert_source_record(
                    source_system=c.source_system, source_record_id=c.source_record_id,  # type: ignore[attr-defined]
                    customer_id=customer_id, raw=raw, cleansed=cleansed_json, profile=p.profile,
                    components=components_json, issues=issues_json,
                )
                repo.insert_blocking_keys(record_id, p.blocking_keys)

            match = None
            if decision.rule is not None:
                match = MatchDetail(
                    rule=decision.rule.key, tier=decision.rule.tier, priority=decision.rule.priority,
                    matched_record_ids=decision.matched_record_ids, auto_linked=auto_link,
                    suggested_customer_id=decision.customer_id,
                    competing_customer_ids=decision.competing_customer_ids,
                )
                review = list(decision.competing_customer_ids)
                if not auto_link:
                    review.append(decision.customer_id)  # type: ignore[arg-type]
                if review:
                    repo.add_merge_candidates(customer_id, review, decision.rule.key, record_id)

            details = {
                "auto_linked": auto_link,
                "pool_size": len(found.pool),
                "candidates_sharing_keys": found.candidates,
                "skipped_blocks": found.skipped,
                "competing_customer_ids": [str(x) for x in decision.competing_customer_ids],
                "evaluations": [
                    {"record_id": e.record_id, "customer_id": str(e.customer_id), "rule": e.rule,
                     "vetoed": e.vetoed}
                    for e in decision.evaluations
                ],
            }
            if relinked_from is not None:
                details["relinked_from"] = str(relinked_from)
            repo.log_event(record_id=record_id, customer_id=customer_id, outcome=outcome,
                           rule=decision.rule.key if decision.rule else None, details=details)

        return ResolveResponse(
            customer_id=customer_id, outcome=outcome, record_id=record_id, match=match,  # type: ignore[arg-type]
            candidates_considered=len(found.pool), skipped_blocks=found.skipped, matching_ms=round(found.ms, 2),
            match_keys_generated=n_keys, data_quality=p.issues, cleansed=c,  # type: ignore[arg-type]
            processing_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def _match(self, repo: Repository, p: Prepared, exclude_record_id: int | None = None) -> Found:
        started = time.perf_counter()
        # ---- blocking
        sizes = repo.block_sizes(p.blocking_keys)
        usable: KeySet = {}
        skipped: list[str] = []
        for ktype, vals in p.blocking_keys.items():
            for v in vals:
                if sizes.get((ktype, v), 0) > self.settings.max_block_size:
                    skipped.append(ktype)
                else:
                    usable.setdefault(ktype, set()).add(v)
        pool = repo.candidate_pool(usable, self.settings.max_candidates, exclude_record_id)

        # ---- matching: which rules' keys each pooled record shares with the inbound one
        candidates: list[Candidate] = []
        for r in repo.load_candidates(pool):
            comps = r["match_components"]
            if comps is None:  # stored before components were kept
                comps = extract_components(CleansedRecord.model_validate(r["cleansed"]))
            shared = shared_match_keys(p.components, comps)
            if shared:
                candidates.append(Candidate(r["record_id"], r["customer_id"], shared, r["match_profile"],
                                            r["created_at"]))
        decision = decide(p.profile, candidates)
        auto_link = (decision.customer_id is not None and decision.rule is not None
                     and decision.rule.tier in self.settings.auto_link_tiers)
        return Found(pool, sorted(set(skipped)), len(candidates), decision, auto_link,
                     (time.perf_counter() - started) * 1000)

    # ------------------------------------------------------------------ merge
    def merge(self, conn: psycopg.Connection, req: MergeRequest) -> MergeResponse:
        repo = Repository(conn)
        with conn.transaction():
            survivor = repo.survivor(req.survivor_customer_id)
            merged = req.merged_customer_id
            if repo.get_customer(survivor) is None or repo.get_customer(merged) is None:
                raise MergeError("customer not found")
            if repo.survivor(merged) == survivor:
                raise MergeError("customers are already merged")
            repo.advisory_locks([lock_id(survivor.bytes), lock_id(merged.bytes)])
            moved = repo.merge_customers(survivor, merged)
            repo.log_event(record_id=None, customer_id=survivor, outcome="merge", rule=None,
                           details={"merged_customer_id": str(merged), "reason": req.reason,
                                    "records_moved": moved})
        return MergeResponse(survivor_customer_id=survivor, merged_customer_id=merged, source_records_moved=moved)

    # ------------------------------------------------------------------ lookups
    def get_customer(self, conn: psycopg.Connection, customer_id: UUID) -> CustomerResponse | None:
        repo = Repository(conn)
        if repo.get_customer(customer_id) is None:
            return None
        survivor_id = repo.survivor(customer_id)
        cust = repo.get_customer(survivor_id)
        records = repo.customer_records(survivor_id)
        return CustomerResponse(
            customer_id=survivor_id, requested_customer_id=customer_id, status=cust["status"],  # type: ignore[index]
            created_at=cust["created_at"], source_records=[SourceRecordSummary(**r) for r in records],  # type: ignore[index]
        )

    def search_customers(self, conn: psycopg.Connection, field: str, raw: str, limit: int) -> CustomerSearchResponse:
        """Find customers by email, mobile or postcode. The value is cleansed the
        same way inbound records are and looked up through its blocking key, so
        "Bill.Smith+promo@googlemail.com" finds "billsmith@gmail.com"."""
        if field == "email":
            email, _ = cleanse_email(raw)
            if email is None:
                raise SearchError("not a valid email address")
            if not email.usable_for_matching:
                raise SearchError("placeholder and role addresses (info@, noreply@ ...) are not indexed")
            key_type, value = "B_EMAIL", email.canonical
        elif field == "mobile":
            phone, _ = cleanse_phone(raw)
            if phone is None:
                raise SearchError("not a valid phone number")
            if not phone.usable_for_matching:
                raise SearchError("placeholder, drama-range and non-personal numbers are not indexed")
            key_type, value = "B_PHONE", phone.e164
        elif field == "postcode":
            pc = parse_postcode(raw)
            if pc is None:
                raise SearchError("not a valid UK postcode")
            key_type, value = "B_POSTCODE", pc.compact
        else:
            raise SearchError(f"cannot search by {field}")

        repo = Repository(conn)
        ids = repo.customers_by_blocking_key(key_type, blocking_key_value(key_type, value, self.settings.key_secret),
                                             limit + 1)
        survivors = list(dict.fromkeys(repo.survivor(i) for i in ids))
        customers = [c for c in (self.get_customer(conn, i) for i in survivors[:limit]) if c is not None]
        normalised = pc.full if field == "postcode" else value
        return CustomerSearchResponse(searched_by=field, normalised_value=normalised,  # type: ignore[arg-type]
                                      customers=customers, truncated=len(ids) > limit)

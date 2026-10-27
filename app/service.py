"""Resolution orchestration: cleanse -> keys -> block -> match -> assign ID."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

import psycopg

from app.cleansing import cleanse
from app.config import Settings
from app.matching.engine import decide
from app.matching.keys import (
    KeySet,
    build_blocking_keys,
    build_match_keys,
    extract_components,
    match_profile,
)
from app.repository import Repository, lock_id
from app.schemas import (
    CleanseResponse,
    InboundCustomer,
    MatchDetail,
    MergeRequest,
    MergeResponse,
    ResolveResponse,
)

log = logging.getLogger(__name__)


class MergeError(ValueError):
    pass


@dataclass
class Prepared:
    inbound: InboundCustomer
    cleansed: object
    issues: list
    profile: dict
    match_keys: KeySet
    blocking_keys: KeySet


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
            match_keys=build_match_keys(comps, self.settings.key_secret),
            blocking_keys=build_blocking_keys(comps, self.settings.key_secret),
        )

    def dry_run(self, inbound: InboundCustomer) -> CleanseResponse:
        p = self.prepare(inbound)
        return CleanseResponse(
            cleansed=p.cleansed,  # type: ignore[arg-type]
            data_quality=p.issues,
            match_keys={k: len(v) for k, v in p.match_keys.items()},
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
        n_keys = sum(len(v) for v in p.match_keys.values())

        with conn.transaction():
            # Any two records that could match share at least one blocking key
            # (proved in tests), so locking every blocking key serialises
            # concurrent arrivals of the same person without a global lock.
            source_lock = lock_id(hashlib.sha256(f"{c.source_system}\x1f{c.source_record_id}".encode()).digest())
            repo.advisory_locks([source_lock, *(lock_id(v) for vals in p.blocking_keys.values() for v in vals)])

            existing = repo.find_source_record(c.source_system, c.source_record_id)  # type: ignore[attr-defined]
            if existing:
                record_id = existing["record_id"]
                customer_id = repo.survivor(existing["customer_id"])
                repo.update_source_record(record_id, raw=raw, cleansed=cleansed_json, profile=p.profile,
                                          issues=issues_json)
                repo.insert_keys(record_id, p.match_keys, p.blocking_keys)
                repo.touch_customer(customer_id)
                repo.log_event(record_id=record_id, customer_id=customer_id, outcome="existing_source_record",
                               rule=None, details={})
                return ResolveResponse(
                    customer_id=customer_id, outcome="existing_source_record", record_id=record_id,
                    match_keys_generated=n_keys, data_quality=p.issues, cleansed=c,  # type: ignore[arg-type]
                    processing_ms=round((time.perf_counter() - started) * 1000, 2),
                )

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
            pool = repo.candidate_pool(usable, self.settings.max_candidates)

            # ---- matching
            shared = repo.shared_match_keys(pool, p.match_keys)
            candidates = repo.load_candidates(shared)
            decision = decide(p.profile, candidates)

            auto_link = decision.rule is not None and decision.rule.tier in self.settings.auto_link_tiers
            if decision.customer_id is not None and auto_link:
                customer_id = decision.customer_id
                outcome = "matched"
                repo.touch_customer(customer_id)
            else:
                customer_id = repo.create_customer()
                outcome = "new_customer"

            record_id = repo.insert_source_record(
                source_system=c.source_system, source_record_id=c.source_record_id,  # type: ignore[attr-defined]
                customer_id=customer_id, raw=raw, cleansed=cleansed_json, profile=p.profile, issues=issues_json,
            )
            repo.insert_keys(record_id, p.match_keys, p.blocking_keys)

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

            repo.log_event(
                record_id=record_id, customer_id=customer_id, outcome=outcome,
                rule=decision.rule.key if decision.rule else None,
                details={
                    "auto_linked": auto_link,
                    "pool_size": len(pool),
                    "candidates_sharing_keys": len(candidates),
                    "skipped_blocks": sorted(set(skipped)),
                    "competing_customer_ids": [str(x) for x in decision.competing_customer_ids],
                    "evaluations": [
                        {"record_id": e.record_id, "customer_id": str(e.customer_id), "rule": e.rule,
                         "vetoed": e.vetoed}
                        for e in decision.evaluations
                    ],
                },
            )

        return ResolveResponse(
            customer_id=customer_id, outcome=outcome, record_id=record_id, match=match,  # type: ignore[arg-type]
            candidates_considered=len(pool), skipped_blocks=sorted(set(skipped)),
            match_keys_generated=n_keys, data_quality=p.issues, cleansed=c,  # type: ignore[arg-type]
            processing_ms=round((time.perf_counter() - started) * 1000, 2),
        )

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


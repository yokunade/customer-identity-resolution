"""Resolution orchestration: cleanse -> keys -> block -> match -> assign ID."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
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
from app.matching.rules import Rule, customers_conflict, matched_records_conflict
from app.repository import Repository, lock_id
from app.schemas import (
    CleansedRecord,
    CleanseResponse,
    CustomerResponse,
    CustomerSearchResponse,
    InboundCustomer,
    MatchDetail,
    MergeRequest,
    MergeResponse,
    ResolveResponse,
    SourceRecordSummary,
    SplitRequest,
    SplitResponse,
)

log = logging.getLogger(__name__)

# Customers bigger than this together are not merged automatically: checking
# every pair of records costs too much, and a customer that big wants a person.
MAX_AUTO_MERGE_RECORDS = 50


class MergeError(ValueError):
    pass


class SearchError(ValueError):
    pass


class SplitError(ValueError):
    pass


def source_lock(source_system: str, source_record_id: str) -> int:
    """The advisory lock that serialises work on one source record."""
    return lock_id(hashlib.sha256(f"{source_system}\x1f{source_record_id}".encode()).digest())


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
    shared_identifiers: list[str]  # identifier kinds too widely held to match on
    # Customers passed over because another of their records conflicts: customer -> vetoes.
    blocked: dict[UUID, list[str]] = field(default_factory=dict)
    # The match profile of each candidate record, by record ID.
    profiles: dict[int, dict] = field(default_factory=dict)


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

    def preview(self, conn: psycopg.Connection,
                inbound: InboundCustomer) -> tuple[Prepared, Found, list[UUID], list[UUID]]:
        """What resolve would do with this record now, storing nothing: the
        prepared record, the match found, the customers a review case would be
        raised against and the customers that would be merged in (the same
        choices resolve makes for a new record)."""
        p = self.prepare(inbound)
        repo = Repository(conn)
        found = self._match(repo, p)
        decision = found.decision
        review: list[UUID] = []
        if decision.customer_id is not None and not found.auto_link:
            review.append(decision.customer_id)
        merges: list[UUID] = []
        for cid, rule in self._others(decision):
            if not found.auto_link:
                review.append(cid)
            elif not repo.pair_rejected(decision.customer_id, cid):  # type: ignore[arg-type]
                into = [decision.customer_id, *merges]
                ok = self._can_merge(repo, found, into, cid, cid, rule, extra=[p.profile])  # type: ignore[list-item]
                (merges if ok else review).append(cid)
        conn.rollback()
        return p, found, list(dict.fromkeys(review)), merges

    def _others(self, decision: Decision) -> list[tuple[UUID, Rule]]:
        """The other customers the record matched that could be the same person
        as the winner: those tied at the winning rule, and those that lost only
        on rank at a rule that would have linked automatically."""
        if decision.rule is None:
            return []
        ties = [(cid, decision.rule) for cid in decision.competing_customer_ids]
        return [*ties, *((cid, r) for cid, r in decision.runners_up if r.tier in self.settings.auto_link_tiers)]

    def _can_merge(self, repo: Repository, found: Found, into: list[UUID], matched: UUID, other: UUID,
                   rule: Rule, extra: list[dict] | None = None) -> bool:
        """Whether other can be merged into the customers in into: the records the
        inbound one matched in the winner and in other (as the decision knew it,
        matched) are the same person by the rule, and no record of one
        conflicts with any record of the other."""
        d = found.decision
        bridged = [found.profiles[e.record_id] for e in d.evaluations
                   if e.customer_id == matched and e.rule == rule.key]
        if matched_records_conflict(rule, [found.profiles[i] for i in d.matched_record_ids], bridged):
            return False
        cap = MAX_AUTO_MERGE_RECORDS + 1
        mine = [*(extra or []), *(pr for cid in into for pr in repo.customer_profiles(cid, cap))]
        theirs = repo.customer_profiles(other, cap)
        if len(mine) + len(theirs) > MAX_AUTO_MERGE_RECORDS:
            return False
        return not customers_conflict(rule, mine, theirs)

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
            lock_started = time.perf_counter()
            repo.advisory_locks([source_lock(c.source_system, c.source_record_id),
                                 *(lock_id(v) for vals in p.blocking_keys.values() for v in vals)])
            lock_ms = round((time.perf_counter() - lock_started) * 1000, 2)
            if lock_ms > self.settings.lock_wait_warn_ms:
                # Usually a hot blocking key: many arrivals at one busy postcode or shared number.
                log.warning("resolve of %s/%s waited %.0f ms for advisory locks", c.source_system,
                            c.source_record_id, lock_ms)

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
                                   rule=None, details={"lock_wait_ms": lock_ms})
                    return ResolveResponse(
                        customer_id=customer_id, outcome=outcome, record_id=record_id,
                        match_keys_generated=n_keys, data_quality=p.issues, cleansed=c,  # type: ignore[arg-type]
                        processing_ms=round((time.perf_counter() - started) * 1000, 2), lock_wait_ms=lock_ms,
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

            # Another customer this record would have been auto-linked to on its
            # own (a tie, or one that lost only on rank) is the same person as the
            # winner, so it is merged in, unless a record of one conflicts with a
            # record of the other (the father's DOB on a record this one did not
            # match): that goes to review.
            merged: list[UUID] = []
            held: list[tuple[UUID, Rule]] = []
            for cid, r in self._others(decision):
                other = repo.survivor(cid)
                if other == customer_id:
                    continue
                if not auto_link or repo.pair_rejected(customer_id, other):
                    held.append((other, r))
                    continue
                repo.advisory_locks([lock_id(customer_id.bytes), lock_id(other.bytes)])
                if not self._can_merge(repo, found, [customer_id], cid, other, r):
                    held.append((other, r))
                    continue
                moved = repo.merge_customers(customer_id, other)
                merged.append(other)
                repo.log_event(record_id=None, customer_id=customer_id, outcome="merge", rule=r.key, details={
                    "merged_customer_id": str(other), "records_moved": moved, "auto": True,
                    "via_record_id": record_id,
                    "reason": f"{c.source_system}/{c.source_record_id} matched both customers",  # type: ignore[attr-defined]
                })
            competing = [cid for cid, _ in held]
            match = None
            if decision.rule is not None:
                match = MatchDetail(
                    rule=decision.rule.key, tier=decision.rule.tier, priority=decision.rule.priority,
                    matched_record_ids=decision.matched_record_ids, auto_linked=auto_link,
                    suggested_customer_id=decision.customer_id, competing_customer_ids=competing,
                    merged_customer_ids=merged,
                )
                if not auto_link:
                    repo.add_merge_candidates(customer_id, [decision.customer_id],  # type: ignore[list-item]
                                              decision.rule.key, record_id)
                for cid, r in held:
                    repo.add_merge_candidates(customer_id, [cid], r.key, record_id)

            details = {
                "auto_linked": auto_link,
                "lock_wait_ms": lock_ms,
                "pool_size": len(found.pool),
                "candidates_sharing_keys": found.candidates,
                "skipped_blocks": found.skipped,
                "shared_identifiers": found.shared_identifiers,
                "competing_customer_ids": [str(x) for x in competing],
                "merged_customer_ids": [str(x) for x in merged],
                "blocked_customers": {str(k): v for k, v in found.blocked.items()},
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
            candidates_considered=len(found.pool), skipped_blocks=found.skipped,
            shared_identifiers=found.shared_identifiers, matching_ms=round(found.ms, 2),
            match_keys_generated=n_keys, data_quality=p.issues, cleansed=c,  # type: ignore[arg-type]
            processing_ms=round((time.perf_counter() - started) * 1000, 2), lock_wait_ms=lock_ms,
        )

    def _match(self, repo: Repository, p: Prepared, exclude_record_id: int | None = None) -> Found:
        started = time.perf_counter()
        # ---- blocking
        sizes = repo.block_sizes(p.blocking_keys, self.settings.max_block_size)
        usable: KeySet = {}
        skipped: list[str] = []
        for ktype, vals in p.blocking_keys.items():
            for v in vals:
                if sizes.get((ktype, v), 0) > self.settings.max_block_size:
                    skipped.append(ktype)
                else:
                    usable.setdefault(ktype, set()).add(v)
        pool = repo.candidate_pool(usable, self.settings.max_candidates, exclude_record_id)
        mine, shared_ids = self._personal_components(repo, p, sizes)

        # ---- matching: which rules' keys each pooled record shares with the inbound one
        candidates: list[Candidate] = []
        for r in repo.load_candidates(pool):
            comps = r["match_components"]
            if comps is None:  # stored before components were kept
                comps = extract_components(CleansedRecord.model_validate(r["cleansed"]))
            shared = shared_match_keys(mine, comps)
            if shared:
                candidates.append(Candidate(r["record_id"], r["customer_id"], shared, r["match_profile"],
                                            r["created_at"]))
        decision = decide(p.profile, candidates)
        # The records that matched agree, but the customer may hold another that
        # does not: a son's DOB, linked through a record with none. Pass over
        # such a customer and decide again among the rest.
        blocked: dict[UUID, list[str]] = {}
        while decision.customer_id is not None and decision.rule is not None:
            profiles = repo.customer_profiles(decision.customer_id, MAX_AUTO_MERGE_RECORDS)
            clash = customers_conflict(decision.rule, [p.profile], profiles)
            if not clash:
                break
            blocked[decision.customer_id] = clash
            candidates = [x for x in candidates if x.customer_id != decision.customer_id]
            decision = decide(p.profile, candidates)
        auto_link = (decision.customer_id is not None and decision.rule is not None
                     and decision.rule.tier in self.settings.auto_link_tiers)
        return Found(pool, sorted(set(skipped)), len(candidates), decision, auto_link,
                     (time.perf_counter() - started) * 1000, shared_ids, blocked,
                     {x.record_id: x.profile for x in candidates})

    def _personal_components(self, repo: Repository, p: Prepared,
                             sizes: dict[tuple[str, bytes], int]) -> tuple[Components, list[str]]:
        """The inbound components without identifiers held by too many customers
        to be personal (a company order email, a switchboard, a big-user
        postcode). Those still gather candidates through blocking, but match on
        nothing. Returns the components and the kinds of identifier dropped."""
        limits = {"email": self.settings.shared_contact_customers, "phone": self.settings.shared_contact_customers,
                  "postcode": self.settings.shared_postcode_customers}
        block_of = {"email": "B_EMAIL", "phone": "B_PHONE", "postcode": "B_POSTCODE"}
        to_count: dict[tuple[str, bytes], tuple[str, str]] = {}
        dropped: dict[str, set[str]] = {}
        for comp, ktype in block_of.items():
            for value in p.components[comp]:
                key = (ktype, blocking_key_value(ktype, value, self.settings.key_secret))
                records = sizes.get(key, 0)
                if records > self.settings.max_block_size:
                    dropped.setdefault(comp, set()).add(value)  # too big even to count
                elif records > limits[comp]:  # every customer has a record, so fewer records rules it out
                    to_count[key] = (comp, value)
        held = repo.block_customers(list(to_count), max(limits.values()))
        for key, (comp, value) in to_count.items():
            if held.get(key, 0) > limits[comp]:
                dropped.setdefault(comp, set()).add(value)
        if not dropped:
            return p.components, []
        mine = dict(p.components)
        for comp, values in dropped.items():
            mine[comp] = mine[comp] - values
        if "phone" in dropped:
            mine["mobile"] = mine["mobile"] - dropped["phone"]
        return mine, sorted(dropped)

    # ------------------------------------------------------------------ merge
    def merge(self, conn: psycopg.Connection, req: MergeRequest) -> MergeResponse:
        repo = Repository(conn)
        with conn.transaction():
            if repo.get_customer(req.survivor_customer_id) is None or repo.get_customer(req.merged_customer_id) is None:
                raise MergeError("customer not found")
            # Either ID may already have been merged away, so merge the customers
            # they now resolve to. Lock, then resolve again: a merge that landed
            # before the lock could have moved either one on.
            ids = {req.survivor_customer_id, req.merged_customer_id}
            while True:
                repo.advisory_locks(lock_id(i.bytes) for i in ids)
                survivor, merged = repo.survivor(req.survivor_customer_id), repo.survivor(req.merged_customer_id)
                if {survivor, merged} <= ids:
                    break
                ids |= {survivor, merged}
            if survivor == merged:
                raise MergeError("customers are already merged")
            moved = repo.merge_customers(survivor, merged)
            details = {"merged_customer_id": str(merged), "reason": req.reason, "records_moved": moved}
            if merged != req.merged_customer_id:
                details["requested_merged_customer_id"] = str(req.merged_customer_id)
            repo.log_event(record_id=None, customer_id=survivor, outcome="merge", rule=None, details=details)
        return MergeResponse(survivor_customer_id=survivor, merged_customer_id=merged, source_records_moved=moved)

    def split(self, conn: psycopg.Connection, req: SplitRequest, by: str | None = None) -> SplitResponse:
        """Undo a wrong link: move some of a customer's source records to a
        customer of their own. If they all arrived as one customer that was
        later merged into this one, that customer is brought back, so its old
        ID finds them again; otherwise a new customer is created. The two are
        then kept apart: re-sending a moved record does not link it back."""
        repo = Repository(conn)
        rids = sorted(set(req.record_ids))
        with conn.transaction():
            if repo.get_customer(req.customer_id) is None:
                raise SplitError("customer not found")
            rows = repo.records_by_id(rids)
            if len(rows) != len(rids):
                raise SplitError("source record not found")
            # Source records first, as resolve takes them, then customers as merge does.
            repo.advisory_locks(source_lock(r["source_system"], r["source_record_id"]) for r in rows)
            ids = {req.customer_id}
            while True:
                repo.advisory_locks(lock_id(i.bytes) for i in ids)
                cid = repo.survivor(req.customer_id)
                if cid in ids:
                    break
                ids.add(cid)
            rows = repo.records_by_id(rids)
            if any(r["customer_id"] != cid for r in rows):
                raise SplitError("a source record does not belong to this customer")
            if repo.record_count(cid) == len(rids):
                raise SplitError("at least one source record must stay with the customer")

            origins = repo.record_origins(rids)
            first = set(origins.values())
            origin = first.pop() if len(origins) == len(rids) and len(first) == 1 else None
            restored = (origin is not None and origin != cid
                        and repo.get_customer(origin)["status"] == "merged"  # type: ignore[index]
                        and repo.survivor(origin) == cid)
            if restored:
                target: UUID = origin  # type: ignore[assignment]
                repo.advisory_locks([lock_id(target.bytes)])
                repo.restore_customer(target)
            else:
                target = repo.create_customer()
            moved = repo.move_records(rids, target)
            repo.keep_apart(cid, target, rule="SPLIT", record_id=rids[0], notes=req.reason)
            repo.touch_customer(cid)

            refs = [f"{r['source_system']}/{r['source_record_id']}" for r in rows]
            common = {"reason": req.reason, "by": by, "restored": restored}
            repo.log_event(record_id=None, customer_id=cid, outcome="split", rule=None,
                           details={**common, "moved_to": str(target), "records_moved": moved, "records": refs})
            for r in rows:
                repo.log_event(record_id=r["record_id"], customer_id=target, outcome="split", rule=None,
                               details={**common, "split_from": str(cid)})
        return SplitResponse(customer_id=cid, moved_to_customer_id=target, restored=restored,
                             source_records_moved=moved)

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

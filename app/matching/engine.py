"""Pure decision logic: given the inbound profile and the candidate pool,
choose the customer to link to (or none). No I/O, so it is fully unit-testable."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from uuid import UUID

from app.matching.rules import RULES, Rule, veto_reasons


@dataclass
class Candidate:
    record_id: int
    customer_id: UUID
    shared_keys: set[str]
    profile: dict
    customer_created_at: dt.datetime | None = None


@dataclass
class Evaluation:
    record_id: int
    customer_id: UUID
    rule: str | None
    vetoed: dict[str, list[str]] = field(default_factory=dict)  # rule -> veto reasons


@dataclass
class Decision:
    customer_id: UUID | None
    rule: Rule | None = None
    matched_record_ids: list[int] = field(default_factory=list)
    competing_customer_ids: list[UUID] = field(default_factory=list)
    evaluations: list[Evaluation] = field(default_factory=list)


def decide(profile: dict, candidates: list[Candidate], rules: tuple[Rule, ...] = RULES) -> Decision:
    ordered = sorted(rules, key=lambda r: r.priority)
    # customer -> (best priority, rule, record ids that fired it, created_at)
    best: dict[UUID, tuple[int, Rule, list[int], dt.datetime | None]] = {}
    evaluations: list[Evaluation] = []

    for cand in candidates:
        ev = Evaluation(cand.record_id, cand.customer_id, None)
        for rule in ordered:
            if rule.key not in cand.shared_keys:
                continue
            reasons = veto_reasons(rule, profile, cand.profile)
            if reasons:
                ev.vetoed[rule.key] = reasons
                continue
            ev.rule = rule.key
            cur = best.get(cand.customer_id)
            if cur is None or rule.priority < cur[0]:
                best[cand.customer_id] = (rule.priority, rule, [cand.record_id], cand.customer_created_at)
            elif rule.priority == cur[0]:
                cur[2].append(cand.record_id)
            break
        evaluations.append(ev)

    if not best:
        return Decision(customer_id=None, evaluations=evaluations)

    top = min(v[0] for v in best.values())
    tied = [(cid, v) for cid, v in best.items() if v[0] == top]
    far_future = dt.datetime.max.replace(tzinfo=dt.timezone.utc)

    def tie_key(item: tuple[UUID, tuple]) -> tuple:
        cid, (_, _, recs, created) = item
        # More corroborating records first, then the oldest customer, then id.
        return (-len(recs), created or far_future, str(cid))

    tied.sort(key=tie_key)
    winner_id, (_, rule, recs, _) = tied[0]
    return Decision(
        customer_id=winner_id,
        rule=rule,
        matched_record_ids=sorted(recs),
        competing_customer_ids=[cid for cid, _ in tied[1:]],
        evaluations=evaluations,
    )

"""Frontier policy: which discovered targets actually get queued.

Adapters propose expansions; this decides. Keeping the two apart means an
adapter cannot accidentally start an unbounded crawl, and the caps live in one
place rather than being re-implemented per platform.
"""

from __future__ import annotations

from ..core.config import settings
from ..db import engine
from ..db.repo import jobs as Q
from .adapters.base import Target


class Budget:
    """Caps a run. Without one, snowball expansion from a single seed is
    unbounded by construction: every video yields an account, every account a
    timeline, every timeline more videos."""

    def __init__(self, max_items: int = 0, max_depth: int | None = None) -> None:
        self.max_items = max_items or settings.max_items
        self.max_depth = settings.max_depth if max_depth is None else max_depth
        self.enqueued = 0
        self.rejected_depth = 0
        self.rejected_budget = 0
        self.rejected_type = 0

    @property
    def item_count(self) -> int:
        return int(engine.scalar("SELECT count(*) FROM items") or 0)

    @property
    def exhausted(self) -> bool:
        return bool(self.max_items) and self.item_count >= self.max_items

    def allows(self, t: Target) -> bool:
        if t.depth > self.max_depth:
            self.rejected_depth += 1
            return False
        if t.job_type == "account_timeline" and not settings.expand_accounts:
            self.rejected_type += 1
            return False
        if t.job_type == "tag_page" and not settings.expand_tags:
            self.rejected_type += 1
            return False
        if t.job_type == "sound_page" and not settings.expand_sounds:
            self.rejected_type += 1
            return False
        if self.exhausted:
            self.rejected_budget += 1
            return False
        return True


def enqueue_targets(targets: list[Target], platform_id: int, budget: Budget,
                    source_id: int | None = None,
                    parent_job_id: int | None = None) -> int:
    """Queue what the budget allows. Deduplication is the queue's UNIQUE
    constraint, so re-proposing a known target is free and harmless — which is
    what lets a snowball crawl converge instead of looping."""
    allowed = [t for t in targets if budget.allows(t)]
    if not allowed:
        return 0
    n = Q.enqueue([{
        "platform_id": platform_id, "job_type": t.job_type, "target": t.target,
        "priority": t.priority, "depth": t.depth,
        "parent_job_id": parent_job_id, "source_id": source_id,
    } for t in allowed])
    budget.enqueued += n
    return n


def seed(platform_id: int, targets: list[Target], source_id: int | None = None) -> int:
    return Q.enqueue([{
        "platform_id": platform_id, "job_type": t.job_type, "target": t.target,
        "priority": t.priority, "depth": t.depth, "source_id": source_id,
    } for t in targets])

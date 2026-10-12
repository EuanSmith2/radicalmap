"""The collection engine.

N async workers pull from the shared queue, dispatch to an adapter, persist, and
feed discovered targets back through the frontier. v1 was one synchronous
process walking a hand-written URL list on a single Playwright page — roughly
20 s of wall clock per video, which is about 23 days of continuous browser time
for 100k videos before any failure.

The concurrency rule from adapters/base.py is load-bearing here: fetches are
awaited, database writes are not. A transaction held across an await would
serialise every other worker behind it, because SQLite has one writer.
"""

from __future__ import annotations

import asyncio
import signal
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..core.config import settings
from ..db import engine
from ..db.repo import jobs as Q
from ..db.repo import writers as W
from .adapters.base import Adapter, AdapterError, PermanentError
from .frontier import Budget, enqueue_targets
from .persist import persist
from .ratelimit import CircuitOpen, Pacer
from .robots import RobotsCache, apply as apply_robots


@dataclass
class Stats:
    claimed: int = 0
    done: int = 0
    failed: int = 0
    dead: int = 0
    deferred: int = 0
    stored: dict[str, int] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)

    def add(self, counts: dict[str, int]) -> None:
        for k, v in counts.items():
            self.stored[k] = self.stored.get(k, 0) + v

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def line(self) -> str:
        rate = self.done / self.elapsed if self.elapsed else 0
        stored = " ".join(f"{k}={v}" for k, v in sorted(self.stored.items()) if v)
        return (f"done={self.done} failed={self.failed} dead={self.dead} "
                f"deferred={self.deferred} {rate:.1f} jobs/s  {stored}")


class Engine:
    def __init__(self, adapters: Sequence[Adapter], platform: str, *,
                 source_id: int, method: str = "collect",
                 workers: int | None = None, budget: Budget | None = None,
                 pacer: Pacer | None = None, verbose: bool = True) -> None:
        self.adapters = list(adapters)
        self.platform = platform
        self.platform_id = W.platform_id(platform)
        self.source_id = source_id
        self.method = method
        self.workers = workers or settings.workers
        self.budget = budget or Budget()
        self.pacer = pacer or Pacer(settings.requests_per_second, settings.burst,
                                    settings.breaker_threshold, settings.breaker_cooldown)
        self.stats = Stats()
        self.robots = RobotsCache()
        self.verbose = verbose
        self._stop = asyncio.Event()
        self._busy = 0

    def _adapter_for(self, job_type: str) -> Adapter | None:
        return next((a for a in self.adapters if a.handles(job_type)), None)

    async def run(self, max_jobs: int = 0, idle_timeout: float = 5.0) -> Stats:
        # Anything stranded by a previous crash comes back before new work starts.
        recovered = Q.reap()
        if recovered and self.verbose:
            print(f"  recovered {recovered} job(s) from an interrupted run")

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_stop)
            except (NotImplementedError, ValueError):
                pass

        tasks = [asyncio.create_task(self._worker(i, max_jobs, idle_timeout))
                 for i in range(self.workers)]
        reporter = asyncio.create_task(self._report())
        try:
            await asyncio.gather(*tasks)
        finally:
            reporter.cancel()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, ValueError):
                    pass
        return self.stats

    def _request_stop(self) -> None:
        if not self._stop.is_set():
            print("\n  stop requested — finishing in-flight jobs, "
                  "queue state is preserved")
            self._stop.set()

    async def _report(self) -> None:
        while self.verbose and not self._stop.is_set():
            await asyncio.sleep(10)
            print(f"  [{self.stats.elapsed:6.0f}s] {self.stats.line()}")

    async def _worker(self, n: int, max_jobs: int, idle_timeout: float) -> None:
        idle_since: float | None = None
        while not self._stop.is_set():
            if max_jobs and self.stats.claimed >= max_jobs:
                return
            if self.budget.exhausted:
                if self.verbose and n == 0:
                    print(f"  budget reached ({self.budget.max_items} items) — stopping")
                self._stop.set()
                return

            batch = Q.claim(limit=1, lease_s=settings.lease_seconds)
            if not batch:
                # Nothing due. Workers may still be producing new targets, so
                # idle is not the same as finished.
                if self._busy == 0:
                    idle_since = idle_since or time.monotonic()
                    if time.monotonic() - idle_since > idle_timeout:
                        return
                await asyncio.sleep(0.25)
                continue

            idle_since = None
            job = batch[0]
            self.stats.claimed += 1
            self._busy += 1
            try:
                await self._handle(job)
            finally:
                self._busy -= 1

    async def _handle(self, job: dict[str, Any]) -> None:
        target = job["target"]
        adapter = self._adapter_for(job["job_type"])
        if adapter is None:
            Q.skip(job["id"], f"no adapter for job_type {job['job_type']!r}")
            return

        # robots.txt before anything else. A Disallow means we do not fetch,
        # full stop — and a declared Crawl-delay tightens this host's pacing for
        # the rest of the run. The politeness guarantee lives in the engine, so
        # no adapter can forget it; same reasoning as edmo-attribution-check
        # putting the pause inside fetch() rather than leaving it to callers.
        if target.startswith(("http://", "https://")):
            try:
                allowed, reason = await apply_robots(self.robots, self.pacer, target)
            except Exception:                            # noqa: BLE001
                allowed, reason = True, "robots check errored"
            if not allowed:
                Q.skip(job["id"], reason)
                Q.log(job["id"], "robots_disallow", reason, "info")
                self.stats.dead += 1
                return

        try:
            await self.pacer.acquire(target)
        except CircuitOpen as exc:
            # Not a job failure: the host is being left alone. Push it out past
            # the cooldown without consuming an attempt.
            engine.execute(
                "UPDATE fetch_jobs SET status='pending', attempts=max(0, attempts-1), "
                "next_attempt_at=unixepoch()+?, last_error=?, lease_expires_at=NULL "
                "WHERE id=?",
                (int(exc.retry_after) + 1, str(exc), job["id"]))
            self.stats.deferred += 1
            return

        try:
            result = await adapter.run(job)
        except PermanentError as exc:
            self.pacer.record(target, ok=True)   # our fault, not the host's
            Q.skip(job["id"], f"permanent: {exc}")
            Q.log(job["id"], "permanent_failure", str(exc), "warn")
            self.stats.dead += 1
            return
        except AdapterError as exc:
            limited = "rate limited" in str(exc).lower()
            self.pacer.record(target, ok=False, rate_limited=limited)
            outcome = Q.fail(job["id"], str(exc), settings.max_attempts)
            Q.log(job["id"], "fetch_failed", str(exc), "warn")
            self.stats.failed += 1
            self.stats.dead += outcome == "dead"
            return
        except Exception as exc:                  # noqa: BLE001 — never lose a worker
            self.pacer.record(target, ok=False)
            Q.fail(job["id"], f"{type(exc).__name__}: {exc}", settings.max_attempts)
            Q.log(job["id"], "unexpected_error", f"{type(exc).__name__}: {exc}", "error")
            self.stats.failed += 1
            return

        self.pacer.record(target, ok=True)
        if not settings.dry_run:
            self.stats.add(persist(result, self.platform, self.source_id, self.method))
            if result.discovered:
                enqueue_targets(result.discovered, self.platform_id, self.budget,
                                self.source_id, job["id"])
        Q.complete(job["id"])
        self.stats.done += 1

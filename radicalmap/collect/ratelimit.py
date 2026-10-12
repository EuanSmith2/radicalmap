"""Per-host pacing and failure containment.

v1 had neither. It ran one hardcoded sleep per loop, one User-Agent, one cookie
jar lifted from the researcher's own logged-in browser, and no notion of a host
being angry with it — so a single ban killed the pipeline and tied the whole
dataset to one identity.

Two mechanisms here:

  TokenBucket with AIMD. Pacing is not fixed; it *responds*. A 429 or 403
  multiplicatively halves the rate, sustained success additively recovers it.
  This is TCP congestion control, and it is the right shape for the same reason
  it is there: the polite rate is not knowable in advance, only discoverable.

  CircuitBreaker. After enough consecutive failures a host is left alone for a
  cooldown rather than hammered. The jobs do not fail — they are returned to the
  queue for later, so a temporary block costs time, not data.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def host_of(target: str) -> str:
    """Bucket key. Anything that is not a URL (a bare video id, a handle)
    shares one logical host, which is what we want — pacing is about the
    remote service, not the string shape of the job."""
    parts = urlsplit(target)
    if parts.scheme in ("http", "https") and parts.netloc:
        netloc = parts.netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    # Anything else — a bare id, a handle, a custom scheme — shares one bucket.
    # Pacing is about the remote service, not the string shape of the job.
    return "_default"


class TokenBucket:
    """Classic token bucket, plus additive-increase/multiplicative-decrease."""

    def __init__(self, rate: float, burst: int = 3, *, floor: float | None = None,
                 ceiling: float | None = None, recover_after: float = 30.0) -> None:
        self.base_rate = rate
        self.rate = rate
        # The floor is a FRACTION of the configured rate, not an absolute.
        # It was 0.02/s absolute, and the 10k scale test found the consequence:
        # with multiplicative decrease and a flat additive increase, ~40 rate-
        # limit responses drove a 100k/s test bucket to 0.02/s — one request
        # every 50 seconds — with no path back. A crawler that cannot recover
        # from a burst of 429s is worse than one that never backs off, because
        # the failure is silent and looks like a hang.
        self.floor = floor if floor is not None else max(rate / 32.0, 1e-3)
        self.ceiling = ceiling if ceiling is not None else rate
        self.recover_after = recover_after
        self._last_penalty = 0.0
        self.burst = max(1, burst)
        self._tokens = float(self.burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()
        self.min_interval = 0.0   # robots.txt Crawl-delay, when one is declared
        self._last_release = 0.0

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.burst, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                self._refill()
                wait = 0.0
                if self._tokens >= 1.0:
                    # A declared Crawl-delay is a hard floor on spacing and is
                    # honoured even when tokens are available. This is the part
                    # of the pacing that is a promise, not an optimisation.
                    gap = time.monotonic() - self._last_release
                    if self.min_interval and gap < self.min_interval:
                        wait = self.min_interval - gap
                    else:
                        self._tokens -= 1.0
                        self._last_release = time.monotonic()
                        return
                else:
                    wait = (1.0 - self._tokens) / max(self.rate, 1e-6)
            # Jitter so parallel workers do not resynchronise into a thundering
            # herd against the same host the instant a token becomes available.
            await asyncio.sleep(wait + random.uniform(0, 0.25))

    def penalise(self, factor: float = 0.5) -> None:
        self.rate = max(self.floor, self.rate * factor)
        self._last_penalty = time.monotonic()

    def reward(self, step: float | None = None) -> None:
        """Additive increase, proportional to the configured rate.

        Proportional matters: a fixed +0.02 recovers a 0.5/s bucket in a few
        successes and a 1000/s bucket never. At base/20, one halving costs
        about twenty successes to undo regardless of scale, so recovery time
        is bounded rather than accidental.
        """
        self.rate = min(self.ceiling, self.rate + (step if step is not None
                                                   else self.base_rate / 20.0))

    def relax(self) -> None:
        """Time-based recovery. AIMD alone only climbs back while requests are
        succeeding; a host that goes quiet after rate-limiting us would
        otherwise leave the bucket pinned at the floor indefinitely."""
        if self._last_penalty and time.monotonic() - self._last_penalty > self.recover_after:
            self.rate = min(self.ceiling, max(self.rate * 2.0, self.base_rate / 8.0))
            self._last_penalty = time.monotonic()


@dataclass
class CircuitBreaker:
    threshold: int = 8
    cooldown: float = 300.0
    failures: int = 0
    opened_at: float = 0.0

    @property
    def is_open(self) -> bool:
        if self.opened_at == 0.0:
            return False
        if time.monotonic() - self.opened_at >= self.cooldown:
            # Half-open: let the next request through as a probe. If it fails,
            # record_failure() trips the breaker again immediately.
            self.opened_at = 0.0
            self.failures = self.threshold - 1
            return False
        return True

    def retry_after(self) -> float:
        return max(0.0, self.cooldown - (time.monotonic() - self.opened_at))

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()


@dataclass
class HostState:
    bucket: TokenBucket
    breaker: CircuitBreaker
    requests: int = 0
    failures: int = 0
    rate_limited: int = 0


class Pacer:
    """Owns one bucket and one breaker per host."""

    def __init__(self, rate: float = 0.5, burst: int = 3,
                 breaker_threshold: int = 8, breaker_cooldown: float = 300.0) -> None:
        self.rate, self.burst = rate, burst
        self.breaker_threshold, self.breaker_cooldown = breaker_threshold, breaker_cooldown
        self._hosts: dict[str, HostState] = {}

    def state(self, host: str) -> HostState:
        st = self._hosts.get(host)
        if st is None:
            st = HostState(
                bucket=TokenBucket(self.rate, self.burst),
                breaker=CircuitBreaker(self.breaker_threshold, self.breaker_cooldown))
            self._hosts[host] = st
        return st

    def set_crawl_delay(self, host: str, delay: float) -> None:
        """A declared Crawl-delay tightens pacing but is never allowed to loosen
        it below our own default — a site saying `Crawl-delay: 0` is not
        permission to flood it."""
        st = self.state(host)
        st.bucket.min_interval = max(st.bucket.min_interval, delay)

    async def acquire(self, target: str) -> None:
        st = self.state(host_of(target))
        st.bucket.relax()
        if st.breaker.is_open:
            raise CircuitOpen(host_of(target), st.breaker.retry_after())
        await st.bucket.acquire()
        st.requests += 1

    def record(self, target: str, *, ok: bool, rate_limited: bool = False) -> None:
        st = self.state(host_of(target))
        if rate_limited:
            st.rate_limited += 1
            st.bucket.penalise()
            st.breaker.record_failure()
        elif ok:
            st.bucket.reward()
            st.breaker.record_success()
        else:
            st.failures += 1
            st.breaker.record_failure()

    def snapshot(self) -> dict[str, dict[str, float | int | bool]]:
        return {
            host: {
                "rate": round(st.bucket.rate, 3),
                "min_interval": st.bucket.min_interval,
                "requests": st.requests,
                "failures": st.failures,
                "rate_limited": st.rate_limited,
                "breaker_open": st.breaker.is_open,
            }
            for host, st in self._hosts.items()
        }


class CircuitOpen(RuntimeError):
    def __init__(self, host: str, retry_after: float) -> None:
        super().__init__(f"circuit open for {host}, retry in {retry_after:.0f}s")
        self.host = host
        self.retry_after = retry_after

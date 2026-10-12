#!/usr/bin/env python
"""Rate limiter behaviour, including the collapse the 10k scale test found."""
from __future__ import annotations
import asyncio, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from radicalmap.collect.ratelimit import CircuitBreaker, CircuitOpen, Pacer, TokenBucket, host_of

fails = []
def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label:<54} {got!r}" + ("" if ok else f" != {want!r}"))

check("host bucketing strips www", host_of("https://www.tiktok.com/x"), "tiktok.com")
check("non-url targets share a bucket", host_of("syn://item/1"), "_default")

# ── the regression ──────────────────────────────────────────────────────────
# 40 rate-limit responses used to pin any bucket at an absolute 0.02/s floor
# with no route back. Assert both that the floor scales and that it recovers.
b = TokenBucket(rate=100.0, burst=5)
for _ in range(40):
    b.penalise()
check("floor scales with configured rate, not absolute", b.rate >= 100.0 / 32)
check("collapsed rate is still usable (>1/s)", b.rate > 1.0)

before = b.rate
for _ in range(20):
    b.reward()
check("~20 successes undo one halving", b.rate > before * 1.9)

b2 = TokenBucket(rate=100.0, burst=5, recover_after=0.05)
for _ in range(40):
    b2.penalise()
floorish = b2.rate
time.sleep(0.06)
b2.relax()
check("recovers on time alone, with no successful traffic", b2.rate > floorish)

b3 = TokenBucket(rate=2.0, burst=2)
for _ in range(50):
    b3.reward()
check("never exceeds the configured ceiling", b3.rate, 2.0)

# ── pacing actually paces ───────────────────────────────────────────────────
async def timed():
    bucket = TokenBucket(rate=20.0, burst=1)
    t = time.monotonic()
    for _ in range(4):
        await bucket.acquire()
    return time.monotonic() - t
elapsed = asyncio.run(timed())
check("4 requests at 20/s take >= ~0.15s", elapsed > 0.12)

async def crawl_delay():
    p = Pacer(rate=1000.0, burst=10)
    p.set_crawl_delay("x.test", 0.2)
    t = time.monotonic()
    for _ in range(3):
        await p.acquire("https://x.test/a")
    return time.monotonic() - t
check("declared Crawl-delay overrides a fast bucket", asyncio.run(crawl_delay()) > 0.4)

p = Pacer(rate=1.0)
p.set_crawl_delay("y.test", 20.0)
p.set_crawl_delay("y.test", 0.0)
check("Crawl-delay: 0 cannot loosen pacing",
      p.state("y.test").bucket.min_interval, 20.0)

# ── circuit breaker ─────────────────────────────────────────────────────────
cb = CircuitBreaker(threshold=3, cooldown=0.05)
for _ in range(3):
    cb.record_failure()
check("opens at the threshold", cb.is_open)
time.sleep(0.06)
check("half-opens after cooldown to probe", cb.is_open, False)
cb.record_failure()
check("a failed probe re-opens immediately", cb.is_open)
cb.opened_at = 0.0; cb.record_success()
check("success clears the failure count", cb.failures, 0)

async def breaker_raises():
    pc = Pacer(rate=100.0, breaker_threshold=2, breaker_cooldown=5)
    for _ in range(2):
        pc.record("https://z.test/a", ok=False)
    try:
        await pc.acquire("https://z.test/a")
        return False
    except CircuitOpen:
        return True
check("open circuit refuses new requests", asyncio.run(breaker_raises()))

pc = Pacer(rate=10.0)
pc.record("https://q.test/a", ok=False, rate_limited=True)
check("429 both slows the bucket and counts against the breaker",
      (pc.state("q.test").bucket.rate < 10.0, pc.state("q.test").breaker.failures), (True, 1))

print()
if fails:
    sys.exit(f"FAILED: {len(fails)} check(s): {fails}")
print("  all checks passed")

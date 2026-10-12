#!/usr/bin/env python
"""robots.txt handling.

Politeness is part of the legal argument this project makes, not a nicety, so
it gets tested like anything else load-bearing. No network: records are built
the way a fetch would build them.
"""
from __future__ import annotations
import asyncio, sys, time
from pathlib import Path
from urllib.robotparser import RobotFileParser
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from radicalmap.collect.ratelimit import Pacer
from radicalmap.collect.robots import RobotsCache, RobotsRecord, apply
from radicalmap.core.config import settings

fails = []
def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label:<54} {got!r}" + ("" if ok else f" != {want!r}"))

def rec(body: str, delay=None):
    p = RobotFileParser(); p.parse(body.splitlines())
    d = delay if delay is not None else p.crawl_delay("*")
    return RobotsRecord(p, float(d) if d is not None else None, time.monotonic(), "ok", body)

cache = RobotsCache()

STANDARD = """User-agent: *
Crawl-delay: 20
Disallow: /private/
Disallow: /admin
Allow: /
"""
r = rec(STANDARD)
check("allowed path permitted", cache.can_fetch(r, "https://x.test/tag/remigration"))
check("disallowed path refused", cache.can_fetch(r, "https://x.test/private/x"), False)
check("prefix disallow refused", cache.can_fetch(r, "https://x.test/admin/panel"), False)
check("crawl-delay parsed", r.crawl_delay, 20.0)

# We present an honest named UA, so the `*` group binds. We must not read a
# search engine's more generous group and act on it.
GREEDY = """User-agent: Googlebot
Disallow:

User-agent: *
Disallow: /
"""
check("does not borrow Googlebot's permissions",
      cache.can_fetch(rec(GREEDY), "https://x.test/anything"), False)

# A group naming THIS crawler outranks a permissive "*" group. can_fetch used to
# OR the two, so a site that singled us out and said no was crawled anyway.
SPECIFIC = """User-agent: RadicalMapResearchBot
Disallow: /

User-agent: *
Disallow:
"""
check("a Disallow aimed at us beats a permissive *",
      cache.can_fetch(rec(SPECIFIC), "https://x.test/anything"), False)

# An unreachable robots.txt is not permission.
unreachable = RobotsRecord(None, None, time.monotonic(), "unreachable: timeout")
check("unreachable robots.txt yields no crawl-delay", unreachable.crawl_delay, None)
check("...and falls back to our own pacing, not to unrestricted",
      settings.requests_per_second <= 1.0)

async def tightens():
    p = Pacer(rate=10.0)
    cache.records["x.test"] = rec(STANDARD)
    allowed, _ = await apply(cache, p, "https://x.test/tag/a")
    return allowed, p.state("x.test").bucket.min_interval
allowed, interval = asyncio.run(tightens())
check("apply() permits an allowed path", allowed)
check("apply() installs the declared Crawl-delay", interval, 20.0)

async def blocks():
    p = Pacer(rate=10.0)
    cache.records["y.test"] = rec(STANDARD)
    allowed, reason = await apply(cache, p, "https://y.test/private/z")
    return allowed, "disallowed" in reason
check("apply() refuses a disallowed path", asyncio.run(blocks()), (False, True))

async def zero_delay():
    p = Pacer(rate=0.5)
    p.set_crawl_delay("z.test", 5.0)
    cache.records["z.test"] = rec("User-agent: *\nCrawl-delay: 0\nAllow: /\n", delay=0)
    await apply(cache, p, "https://z.test/a")
    return p.state("z.test").bucket.min_interval
check("Crawl-delay: 0 never loosens an existing delay", asyncio.run(zero_delay()), 5.0)

check("cache record goes stale after its TTL",
      RobotsRecord(None, None, time.monotonic() - 7200, "ok").stale)

print()
if fails: sys.exit(f"FAILED: {len(fails)} check(s): {fails}")
print("  all checks passed")

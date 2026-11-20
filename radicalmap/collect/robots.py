"""robots.txt: fetch, cache, and honour.

This is not a nicety. LEGAL.md's argument that the project operates as
legitimate research — and its reading of CDSM Art. 3 and DSA Art. 40 — is much
weaker if the collector behaves like something trying not to be noticed. The
same judgement was already made on edmo-attribution-check, where robots.txt
declared `Crawl-delay: 20` and the scraper honoured it unconditionally across a
multi-thousand-article crawl rather than going faster because it could.

Two rules follow from that, both enforced here:

  A declared Crawl-delay can only ever SLOW us down. A site declaring
  `Crawl-delay: 0` is not permission to flood it, so our own default still
  applies as the floor.

  A Disallow is obeyed for the identity we actually present. We use an honest
  named User-Agent, so the `*` group is the one that binds — we do not read
  Googlebot's more generous group and claim it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from ..core.config import settings
from .ratelimit import host_of

CACHE_TTL = 3600.0


@dataclass
class RobotsRecord:
    parser: RobotFileParser | None
    crawl_delay: float | None
    fetched_at: float
    status: str
    raw: str = ""

    @property
    def stale(self) -> bool:
        return time.monotonic() - self.fetched_at > CACHE_TTL


@dataclass
class RobotsCache:
    records: dict[str, RobotsRecord] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def _lock(self, host: str) -> asyncio.Lock:
        return self._locks.setdefault(host, asyncio.Lock())

    async def get(self, url: str) -> RobotsRecord:
        host = host_of(url)
        async with self._lock(host):
            rec = self.records.get(host)
            if rec is not None and not rec.stale:
                return rec
            rec = await self._fetch(url, host)
            self.records[host] = rec
            return rec

    async def _fetch(self, url: str, host: str) -> RobotsRecord:
        scheme = urlsplit(url).scheme or "https"
        robots_url = f"{scheme}://{host}/robots.txt"
        # Fetching robots.txt is itself a request to the platform, so it sits
        # behind the same gate as collection. With the gate closed nothing is
        # fetched; the adapter refuses the job right after this anyway.
        if not settings.allow_network:
            return RobotsRecord(None, None, time.monotonic(), "network disabled")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await client.get(robots_url,
                                     headers={"User-Agent": settings.user_agent})
        except Exception as exc:                       # noqa: BLE001
            # Unreachable robots.txt is NOT taken as permission. We fall back to
            # our own conservative default rather than to "allowed, no delay".
            return RobotsRecord(None, None, time.monotonic(), f"unreachable: {exc}")

        if r.status_code == 404:
            # A genuine 404 does mean no restrictions were published.
            return RobotsRecord(None, None, time.monotonic(), "absent (404)")
        if r.status_code != 200:
            return RobotsRecord(None, None, time.monotonic(), f"http {r.status_code}")

        parser = RobotFileParser()
        parser.parse(r.text.splitlines())
        delay = None
        for agent in (settings.user_agent.split("/")[0], "*"):
            d = parser.crawl_delay(agent)
            if d is not None:
                delay = float(d)
                break
        return RobotsRecord(parser, delay, time.monotonic(), "ok", r.text[:4000])

    def can_fetch(self, rec: RobotsRecord, url: str) -> bool:
        if rec.parser is None:
            return True
        # RobotFileParser already falls back to the "*" group when no group
        # names this agent. OR-ing in a separate "*" check meant a Disallow
        # aimed specifically at this crawler was overridden by a permissive
        # "*" group — the most specific rule must win.
        return rec.parser.can_fetch(settings.user_agent, url)


async def apply(cache: RobotsCache, pacer, url: str) -> tuple[bool, str]:
    """Consult robots.txt and tighten pacing. Returns (allowed, reason)."""
    if not settings.respect_robots:
        return True, "robots checking disabled"
    rec = await cache.get(url)
    if rec.crawl_delay:
        # Only ever tightens — set_crawl_delay() takes the max.
        pacer.set_crawl_delay(host_of(url), rec.crawl_delay)
    if not cache.can_fetch(rec, url):
        return False, f"disallowed by robots.txt ({rec.status})"
    return True, rec.status

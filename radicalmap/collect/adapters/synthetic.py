"""A fake platform, for proving the engine without touching a real one.

Collection is an outward-facing act against a live service. Verifying that the
worker pool, frontier, pacing, retry, backoff and crash-resume all behave at
scale should not require sending a hundred thousand requests to TikTok, so this
adapter simulates one: configurable latency, failure rate, rate limiting and
permanent-gone responses, with a deterministic corpus generated from a seed.

It is a test double, but it is the same Adapter protocol the real one
implements, driven by the same engine through the same persistence path — so
what passes here is the actual production code path, not a mock of it.
"""

from __future__ import annotations

import asyncio
import random
import re
from typing import Any

from .base import (AdapterError, CollectResult, ParsedAccount, ParsedComment,
                   ParsedItem, ParsedSound, PermanentError, Target)

WORDS = ("replacement great reset noticing based heritage europe tradition folk "
         "remigration clown world symbolism cartoons kalergi globalist pipeline "
         "recipe football travel review vlog tutorial").split()

TAGS = ("remigration", "clownworld", "1488", "noticing", "basedandtrue", "heritage",
        "europa", "cooking", "fyp", "sunset", "greatreplacement", "kalergi")


class SyntheticAdapter:
    """job targets look like `syn://item/<n>`, `syn://timeline/<n>`."""

    name = "synthetic"
    platform = "tiktok"          # reuses the real schema path

    JOB_TYPES = {"item", "account_timeline", "tag_page", "sound_page"}
    RE = re.compile(r"^syn://(\w+)/(\d+)$")

    def __init__(self, *, corpus_size: int = 10_000, accounts: int = 400,
                 sounds: int = 600, comments_per_item: int = 8,
                 latency: float = 0.0, failure_rate: float = 0.0,
                 rate_limit_rate: float = 0.0, gone_rate: float = 0.0,
                 fanout: int = 12, seed: int = 1848) -> None:
        self.corpus_size = corpus_size
        self.accounts = accounts
        self.sounds = sounds
        self.comments_per_item = comments_per_item
        self.latency = latency
        self.failure_rate = failure_rate
        self.rate_limit_rate = rate_limit_rate
        self.gone_rate = gone_rate
        self.fanout = fanout
        self.seed = seed
        self.requests = 0

    def handles(self, job_type: str) -> bool:
        return job_type in self.JOB_TYPES

    def target_for(self, node_type: str, node_id: int, row: dict) -> tuple[str, str] | None:
        pid = str(row.get("platform_account_id") or "")
        if node_type == "account" and pid.startswith("synacct"):
            return "account_timeline", f"syn://timeline/{pid[7:]}"
        if node_type == "item":
            ipid = str(row.get("platform_item_id") or "")
            if ipid.startswith("syn"):
                return "item", f"syn://item/{ipid[3:]}"
        if node_type == "sound":
            spid = str(row.get("platform_sound_id") or "")
            if spid.startswith("synsnd"):
                return "sound_page", f"syn://sound/{spid[6:]}"
        return None

    def _rng(self, n: int) -> random.Random:
        """Deterministic per-id, so a retry of job 57 returns exactly what the
        first attempt would have — which is what makes resume testable."""
        return random.Random(self.seed * 1_000_003 + n)

    async def run(self, job: dict[str, Any]) -> CollectResult:
        m = self.RE.match(job["target"])
        if not m:
            raise PermanentError(f"malformed synthetic target {job['target']!r}")
        kind, n = m.group(1), int(m.group(2))
        self.requests += 1

        if self.latency:
            await asyncio.sleep(self.latency)

        # Failure injection uses a non-deterministic RNG on purpose: a retry
        # must be able to succeed where the first attempt failed.
        roll = random.random()
        if roll < self.rate_limit_rate:
            raise AdapterError("rate limited: HTTP 429 (synthetic)")
        if roll < self.rate_limit_rate + self.failure_rate:
            raise AdapterError("synthetic transient error")
        if self._rng(n).random() < self.gone_rate:
            raise PermanentError("video not available (synthetic)")

        if kind == "item":
            return CollectResult(items=[self._item(n)],
                                 discovered=self._expand(n, job.get("depth", 0)))
        return CollectResult(discovered=[
            Target("item", f"syn://item/{(n * 7919 + k) % self.corpus_size}",
                   depth=job.get("depth", 0) + 1)
            for k in range(self.fanout)])

    def _item(self, n: int) -> ParsedItem:
        r = self._rng(n)
        acct = n % self.accounts
        snd = n % self.sounds
        tags = r.sample(TAGS, k=r.randint(2, 5))
        caption = " ".join(r.choices(WORDS, k=r.randint(6, 18)))
        # A slice of items carry a recruitment link in a COMMENT rather than the
        # caption — the exact shape v1 could not see, so the end-to-end test can
        # assert we now do.
        comments = []
        for k in range(r.randint(0, self.comments_per_item)):
            text = " ".join(r.choices(WORDS, k=r.randint(3, 12)))
            if k == 0 and n % 17 == 0:
                text = f"join the group t dot me slash chan{n % 500}"
            comments.append(ParsedComment(
                platform_comment_id=f"c{n}_{k}", text=text,
                author_handle=f"commenter{r.randrange(50_000)}",
                like_count=r.randint(0, 900), posted_at=1_750_000_000 + n))
        return ParsedItem(
            platform_item_id=f"syn{n}",
            url=f"https://example.invalid/v/{n}",
            caption=caption + " " + " ".join(f"#{t}" for t in tags),
            posted_at=1_750_000_000 + n * 37,
            duration_s=r.uniform(5, 90),
            view_count=r.randint(100, 4_000_000), like_count=r.randint(0, 150_000),
            comment_count=len(comments), share_count=r.randint(0, 40_000),
            save_count=r.randint(0, 9_000),
            account=ParsedAccount(platform_account_id=f"synacct{acct}",
                                  handle=f"user{acct}",
                                  bio=f"contact t.me/bio{acct}" if acct % 23 == 0 else "bio",
                                  follower_count=r.randint(50, 900_000)),
            sound=ParsedSound(platform_sound_id=f"synsnd{snd}",
                              title=f"sound {snd}", author_name=f"user{snd % self.accounts}",
                              duration_s=r.uniform(5, 60),
                              is_original=int(snd % 5 == 0)),
            tags=tags, comments=comments,
            transcript=caption if n % 3 == 0 else None,
        )

    def _expand(self, n: int, depth: int) -> list[Target]:
        return [
            Target("account_timeline", f"syn://timeline/{n % self.accounts}", depth=depth + 1),
            Target("sound_page", f"syn://sound/{n % self.sounds}", depth=depth + 1),
        ]

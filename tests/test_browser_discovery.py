"""Offline tests for browser discovery.

Everything here runs without a browser and without a network. The part worth
testing is `harvest`: TikTok returns at least three different listing shapes
from endpoints that get renamed, and the whole point of walking the structure
instead of branching on the endpoint is that a rename must not silently return
zero results.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radicalmap.collect.adapters.tiktok_browser import (  # noqa: E402
    TikTokBrowserAdapter, harvest, item_url, search_url, tag_url)

checks = 0
failures: list[str] = []


def check(cond: bool, label: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label)


def video(vid: str, handle: str) -> dict:
    return {"id": vid, "desc": "x", "author": {"uniqueId": handle, "nickname": handle},
            "music": {"id": "999", "title": "s"}, "stats": {"playCount": 1}}


# ── shape 1: tag / sound pages ───────────────────────────────────────────────
tag_payload = {"statusCode": 0, "itemList": [video("7111111111111111111", "alpha"),
                                             video("7222222222222222222", "beta")],
               "hasMore": True}
got = harvest(tag_payload)
check(len(got) == 2, "tag payload yields 2")
check(got[0] == ("7111111111111111111", "alpha"), "tag payload id+handle")

# ── shape 2: general search wraps each hit in {item: ...} ────────────────────
search_payload = {"data": [{"type": 1, "item": video("7333333333333333333", "gamma")},
                           {"type": 4, "item": video("7444444444444444444", "delta")}]}
got = harvest(search_payload)
check(len(got) == 2, "search payload yields 2")
check({h for _, h in got} == {"gamma", "delta"}, "search payload handles")

# ── shape 3: a renamed envelope must still work ──────────────────────────────
# This is the regression the walk exists for. If harvest branched on endpoint
# name or on a top-level key, this returns 0 and a crawl reports success while
# discovering nothing -- the exact silent-failure shape Phase 4 kept finding.
renamed = {"someNewWrapper": {"results": {"videos": [video("7555555555555555555", "eps")]}}}
check(len(harvest(renamed)) == 1, "unknown envelope still harvested")

# ── non-video numeric ids must not be mistaken for videos ────────────────────
noise = {"challengeInfo": {"challenge": {"id": "1234567", "title": "cycling"}},
         "music": {"id": "7009999999999999999", "title": "sound"},
         "stickers": [{"id": "8888888888"}]}
check(harvest(noise) == [], "numeric non-video ids rejected (no author object)")

mixed = {"itemList": [video("7666666666666666666", "zeta")], **noise}
got = harvest(mixed)
check(len(got) == 1 and got[0][0] == "7666666666666666666", "video found among noise")

# ── duplicates across paginated responses collapse ───────────────────────────
dupes = {"a": {"itemList": [video("7777777777777777777", "eta")]},
         "b": {"itemList": [video("7777777777777777777", "eta")]}}
check(len(harvest(dupes)) == 1, "duplicate ids collapse")

# ── a missing handle is tolerated, not fatal ─────────────────────────────────
no_handle = {"itemList": [{"id": "7888888888888888888", "author": {}}]}
got = harvest(no_handle)
check(got == [("7888888888888888888", None)], "missing handle -> None")
check("/@_/video/" in item_url("7888888888888888888", None), "url falls back to @_")

# ── empty and malformed input ────────────────────────────────────────────────
check(harvest({}) == [], "empty dict")
check(harvest([]) == [], "empty list")
check(harvest(None) == [], "None")
check(harvest({"itemList": None}) == [], "null itemList")

# ── url builders ─────────────────────────────────────────────────────────────
check(tag_url("#Accelerationism") == "https://www.tiktok.com/tag/Accelerationism",
      "tag url strips leading hash")
check("q=mike%20ma" in search_url("mike ma") or "q=mike+ma" in search_url("mike ma"),
      "search url encodes spaces")
check(tag_url("a b") == "https://www.tiktok.com/tag/a%20b", "tag url encodes spaces")

# ── adapter contract ─────────────────────────────────────────────────────────
a = TikTokBrowserAdapter()
check(a.handles("search") and a.handles("tag_page"), "claims discovery job types")
check(not a.handles("item"), "does NOT claim item collection")
check(not a.handles("account_timeline"), "does NOT claim timelines")
check(a.target_for("tag", 1, {"tag": "siege"}) == ("tag_page", tag_url("siege")),
      "propagates tag pages")
check(a.target_for("sound", 1, {"title": "x"}) is None,
      "does not propagate sound pages")
check(a.target_for("account", 1, {"handle": "x"}) is None,
      "leaves accounts to the yt-dlp adapter")

print(f"  {checks - len(failures)}/{checks} passed")
for f in failures:
    print(f"    FAIL: {f}")
sys.exit(1 if failures else 0)

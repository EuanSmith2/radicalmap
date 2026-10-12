#!/usr/bin/env python
"""Offline tests for the in-house TikTok collection path and Apify interchange.

Every fetch is a fake. The gate test proves the real transport refuses to run
without RM_ALLOW_NETWORK=1; everything else checks what gets parsed and stored.
All handles and text in the fixtures are invented.
"""
from __future__ import annotations

import asyncio, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_DB"] = str(tmp / "a.db")
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")
os.environ.pop("RM_ALLOW_NETWORK", None)

from radicalmap.core.config import settings
from radicalmap.collect.adapters import tiktok_web as web
from radicalmap.collect.adapters.base import (AdapterError, CollectResult, ParsedAccount,
                                              ParsedItem, PermanentError)
from radicalmap.collect.adapters.tiktok import TikTokAdapter
from radicalmap.collect.export_apify import export
from radicalmap.collect.importers.corpus import import_file
from radicalmap.collect.persist import persist
from radicalmap.db import engine, migrate
from radicalmap.db.repo import writers as W
from radicalmap.propagate import edges

fails = []
def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label:<56} {got!r}" + ("" if ok else f" != {want!r}"))

def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception as e:  # wrong type is a failure, not a pass
        return type(e).__name__
    return False

section = lambda s: print(f"\n  ── {s} " + "─" * (56 - len(s)))
html = (ROOT / "tests/fixtures/tiktok_item_page.html").read_text()

# ── parsing ─────────────────────────────────────────────────────────────────
section("itemStruct parsing")
item = web.parse_item_struct(web.item_struct(web.rehydration(html)))
check("item id", item.platform_item_id, "7400000000000000001")
check("posted_at from createTime string", item.posted_at, 1720000000)
check("sound keeps the platform music id", item.sound.platform_sound_id, "7300000000000000009")
check("sound play url kept", item.sound.play_url, "https://example.invalid/audio/9.mp3")
check("original sound flagged", item.sound.is_original, 1)
check("tags from textExtra + challenges, deduped", item.tags, ["examplecoded", "fyp"])
check("mentions from textExtra", item.mentions, ["quietharbor"])
check("author bio", item.account.bio, "synthetic bio, see link")
check("link-in-bio", item.account.bio_link, "t.me/examplechannel")
check("follower count from authorStats", item.account.follower_count, 1200)
check("pinned flag", item.is_pinned, True)
check("location", item.location, "IE")
check("sticker text", item.stickers[0].text_content, "synthetic sticker")
check("english caption track preferred", web.subtitle_url(
    web.item_struct(web.rehydration(html))), ("https://example.invalid/sub/en.vtt", "en"))

section("status handling")
def page(code):
    return {"webapp.video-detail": {"statusCode": code, "itemInfo": {}}}
check("private post is permanent", raises(lambda: web.item_struct(page(10216)), PermanentError))
check("deleted video is permanent", raises(lambda: web.item_struct(page(10204)), PermanentError))
check("unknown status is retryable", raises(lambda: web.item_struct(page(99999)), AdapterError))
check("challenge page (no blob) parses to nothing",
      web.item_struct(web.rehydration("<html>Please wait...</html>")), None)

section("profiles and comments")
acct = web.parse_user_detail({"user": {"id": "1", "uniqueId": "x_example", "signature": "s",
                                       "bioLink": {"link": "example.invalid/l"},
                                       "isUnderAge18": True},
                              "stats": {"followerCount": 5, "videoCount": 2}})
check("profile bio link", acct.bio_link, "example.invalid/l")
check("platform under-18 flag honoured", acct.is_minor, True)
check("absence of the flag is not a minor", web.is_minor({"uniqueId": "y"}), False)
cs, cur, more = web.parse_comment_page({"status_code": 0, "cursor": 20, "has_more": 1,
    "comments": [{"cid": "9", "text": " hi ", "digg_count": 3, "reply_comment_total": 1,
                  "create_time": 1720000001, "user": {"unique_id": "commenter_x"}}]})
check("comment parsed", (cs[0].platform_comment_id, cs[0].text, cs[0].reply_count), ("9", "hi", 1))
check("cursor + has_more", (cur, more), (20, True))
check("refused comment page raises, not empty",
      raises(lambda: web.parse_comment_page({"status_code": 10201, "comments": []}), AdapterError))

section("date window")
check("inside", web.in_window(1720000000, 1719999999, 1720000001), True)
check("before from", web.in_window(1, 1720000000, 0), False)
check("after to", web.in_window(1720000002, 0, 1720000001), False)
check("undated passes (no dating bias)", web.in_window(None, 1, 2), True)

# ── the adapter, with fake transports ───────────────────────────────────────
section("adapter: web path")
os.environ.pop("RM_ALLOW_NETWORK", None)
check("real transport refuses without RM_ALLOW_NETWORK",
      raises(lambda: asyncio.run(TikTokAdapter()._http_text("https://example.invalid")),
             PermanentError))

calls = []
async def fake_text(url):
    calls.append(url)
    if url.endswith(".vtt"):
        return "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nsynthetic spoken words\n"
    return html

async def fake_json(url):
    calls.append(url)
    if "/reply/" in url:
        parent = url.split("comment_id=", 1)[1].split("&", 1)[0]
        return {"status_code": 0, "has_more": 0, "cursor": 0,
                "comments": [{"cid": f"r-{parent}", "text": "synthetic reply",
                              "user": {"unique_id": "replier"}}]}
    cursor = int(url.rsplit("cursor=", 1)[1])
    body = {"status_code": 0, "cursor": cursor + 1, "has_more": int(cursor == 0),
            "comments": [{"cid": f"p{cursor}", "text": f"synthetic page {cursor}",
                          "reply_comment_total": 1, "user": {"unique_id": "commenter_y"}}]}
    return body

object.__setattr__(settings, "requests_per_second", 0)     # no sleeping in tests
ad = TikTokAdapter(fetch_text=fake_text, fetch_json=fake_json, max_comments=10, max_replies=5)
res = asyncio.run(ad.run({"job_type": "item", "target": "https://www.tiktok.com/@a/video/1", "depth": 0}))
it = res.items[0]
check("both comment pages followed", [c.platform_comment_id for c in it.comments if not c.parent_platform_comment_id], ["p0", "p1"])
check("replies fetched and threaded",
      [(c.platform_comment_id, c.parent_platform_comment_id) for c in it.comments if c.parent_platform_comment_id],
      [("r-p0", "p0"), ("r-p1", "p1")])
check("transcript from caption track", it.transcript, "synthetic spoken words")
check("mention queued as an account target",
      any(t.job_type == "account_timeline" and t.target.endswith("/@quietharbor") for t in res.discovered))
check("sound page queued from the real music id",
      any(t.target.endswith("/music/-7300000000000000009") for t in res.discovered))

object.__setattr__(settings, "tt_exclude_pinned", True)
res = asyncio.run(TikTokAdapter(fetch_text=fake_text, fetch_json=fake_json).run(
    {"job_type": "item", "target": "u", "depth": 0}))
check("pinned post excluded when configured", (res.items, "pinned" in res.notes), ([], True))
object.__setattr__(settings, "tt_exclude_pinned", False)

object.__setattr__(settings, "tt_date_from", 1800000000)
res = asyncio.run(TikTokAdapter(fetch_text=fake_text, fetch_json=fake_json).run(
    {"job_type": "item", "target": "u", "depth": 0}))
check("out-of-window item dropped", res.items, [])
check("listing filter drops by id timestamp before fetching",
      TikTokAdapter().parse_listing('{"id": "7400000000000000001"}\n', 0), [])
object.__setattr__(settings, "tt_date_from", 0)

async def blocked(url):
    return "<html>Please wait...</html>"
object.__setattr__(settings, "tt_ytdlp_fallback", False)
check("no blob + fallback off -> retryable error, not empty success",
      raises(lambda: asyncio.run(TikTokAdapter(fetch_text=blocked).run(
          {"job_type": "item", "target": "u", "depth": 0})), AdapterError))
object.__setattr__(settings, "tt_ytdlp_fallback", True)
fb = TikTokAdapter(fetch_text=blocked)
async def fake_ytdlp(target):
    return ParsedItem(platform_item_id="42")
fb._item_ytdlp = fake_ytdlp
res = asyncio.run(fb.run({"job_type": "item", "target": "u", "depth": 0}))
check("no blob + fallback on -> yt-dlp path used", res.items[0].platform_item_id, "42")

# ── persistence ─────────────────────────────────────────────────────────────
section("persistence")
migrate.migrate(verbose=False)
src = W.ensure_source("test", "manual", "tests/test_tiktok_web.py")
minor_item = ParsedItem(platform_item_id="900", caption="x",
                        account=ParsedAccount("m1", handle="smallfry_example", is_minor=True))
c = persist(CollectResult(items=[it, minor_item],
                          accounts=[ParsedAccount("m2", handle="other_minor", is_minor=True)]),
            "tiktok", src, "test")
check("minors skipped and counted", c["skipped_minors"], 2)
check("minor's item never written",
      engine.scalar("SELECT count(*) FROM items WHERE platform_item_id='900'"), 0)
check("minor account never written",
      engine.scalar("SELECT count(*) FROM accounts WHERE handle IN ('smallfry_example','other_minor')"), 0)
check("bio link stored as a bio-surface link",
      engine.scalar("SELECT count(*) FROM links WHERE source_type='account' AND url LIKE '%examplechannel%'") >= 1)
check("reply threaded to its parent",
      engine.scalar("""SELECT p.platform_comment_id FROM comments c JOIN comments p
                        ON p.id = c.parent_comment_id WHERE c.platform_comment_id='r-p0'"""), "p0")
persist(CollectResult(accounts=[ParsedAccount("6900000000000000002", handle="QuietHarbor")]),
        "tiktok", src, "test")
check("mention becomes an edge, case-insensitively", edges.build_mentions(), 1)

# ── Apify interchange ───────────────────────────────────────────────────────
section("apify round trip")
engine.close()
os.environ["RADICALMAP_DB"] = str(tmp / "b.db")
migrate.migrate(verbose=False)
fixture = ROOT / "tests/fixtures/apify_tiktok_items.json"
t1 = import_file(fixture, mapping=dict(web.APIFY_IMPORT_MAPPING), verbose=False)
check("minor in an imported corpus is dropped", t1.get("skipped_minors"), 1)
check("two items imported", engine.scalar("SELECT count(*) FROM items"), 2)
check("shared music id -> one sound for two accounts",
      engine.scalar("SELECT count(*) FROM sounds"), 1)
check("shared sound becomes a shares_sound tie", edges.build_shares_sound() >= 1)
check("imported mention resolves to an edge", edges.build_mentions(), 1)
out1 = tmp / "export1.json"
check("export writes both items", export(out1), 2)

engine.close()
os.environ["RADICALMAP_DB"] = str(tmp / "c.db")
migrate.migrate(verbose=False)
import_file(out1, mapping=dict(web.APIFY_IMPORT_MAPPING), verbose=False)
out2 = tmp / "export2.json"
export(out2)

def comparable(path):
    rows = json.loads(path.read_text())
    for r in rows:   # commenter ids are pseudonyms of pseudonyms after a re-import
        for cm in r["comments"]:
            cm.pop("uniqueId", None)
    return rows
check("export -> import -> export is lossless", comparable(out1), comparable(out2))
first = json.loads(out1.read_text())[0]
check("exported commenter is a pseudonym, never a handle",
      all(c["uniqueId"].startswith("anon:") for c in first["comments"]))
check("reply thread survives export", [c["repliesToId"] for c in first["comments"]], [None, "c1"])

if fails: sys.exit(f"FAILED: {len(fails)} check(s): {fails}")
print("\n  all checks passed")

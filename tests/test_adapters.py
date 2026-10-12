#!/usr/bin/env python
"""Offline tests for the parsing layers.

Everything here runs without network. Collection is an outward-facing act, so
the parsing that decides what gets stored must be verifiable without performing
one — v1 had no tests at all, and its transcripts/external_links/stickers tables
sat at 0 rows for two months without anyone noticing.
"""
from __future__ import annotations

import json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_DB"] = str(tmp / "t.db")
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")

from radicalmap.collect.adapters.tiktok import (TikTokAdapter, posted_at_from_id,
                                                slug, vtt_to_text)
from radicalmap.collect.extract import extract_links, extract_tags, normalise_url, platform_of
from radicalmap.collect.importers.corpus import read_records, to_item
from radicalmap.collect.persist import persist
from radicalmap.collect.adapters.base import CollectResult
from radicalmap.db import engine, migrate
from radicalmap.db.repo import writers as W

fails = []
def check(label, got, want):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label:<52} {got!r}" + ("" if ok else f" != {want!r}"))

section = lambda s: print(f"\n  ── {s} " + "─" * (56 - len(s)))

# ── TikTok parsing ──────────────────────────────────────────────────────────
section("tiktok adapter")
info = json.loads((ROOT / "tests/fixtures/tiktok_video.json").read_text())
item = TikTokAdapter().parse_video(info)

check("item id", item.platform_item_id, "7653385075140218144")
check("caption kept in full", item.caption.startswith("they always kept it"), True)
check("posted_at prefers yt-dlp timestamp", item.posted_at, 1782000173)
check("engagement mapped (share<-repost)", (item.view_count, item.share_count, item.save_count),
      (1240000, 4200, 9100))
check("account uses stable channel_id, not the handle",
      item.account.platform_account_id, "6812345678901234567")
check("handle retained", item.account.handle, "testuser")
check("sound captured", (item.sound.title, item.sound.author_name),
      ("original sound", "testuser"))
check("original sound detected", item.sound.is_original, 1)
check("sound id derived when platform gives none",
      item.sound.platform_sound_id, "tt:original-sound|testuser")
check("derivation is recorded, not hidden", item.sound.raw["id_source"], "derived-from-track")
check("tags include non-ascii", item.tags, ["symbolism", "noticing", "återvandring"])
check("empty comments dropped", len(item.comments), 2)
check("comment authors carried for hashing",
      [c.author_handle for c in item.comments], ["commenter_a", "commenter_b"])

# posted_at fallback when the page is gone and yt-dlp gives no timestamp
no_ts = {**info}; no_ts.pop("timestamp")
check("posted_at falls back to the id", TikTokAdapter().parse_video(no_ts).posted_at,
      posted_at_from_id("7653385075140218144"))
check("id decode rejects nonsense", posted_at_from_id("123"), None)
check("id decode rejects non-numeric", posted_at_from_id("abc"), None)

check("vtt flattened, dupes and tags dropped",
      vtt_to_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nthey <c>always</c> knew\n\n"
                  "00:00:03.000 --> 00:00:05.000\nthey always knew\nin plain sight"),
      "they always knew in plain sight")

section("listing / frontier expansion")
lines = "\n".join(json.dumps({"id": i, "uploader": "u"}) for i in
                  ["7653385075140218144", "7615406924032478477", "not-a-number"])
check("listing parses ids, skips junk", len(TikTokAdapter().parse_listing(lines, 0)), 2)
check("discovered targets are one level deeper",
      TikTokAdapter().parse_listing(lines, 2)[0].depth, 3)

# ── link extraction: the v1 defect ──────────────────────────────────────────
section("link extraction")
check("plain url", [l.platform for l in extract_links("see https://t.me/chan")], ["telegram"])
check("scheme-less domain", [l.normalised_url for l in extract_links("join t.me/chan")],
      ["https://t.me/chan"])
check("spelled-out obfuscation",
      [l.normalised_url for l in extract_links("t dot me slash europa88")],
      ["https://t.me/europa88"])
check("bracket obfuscation",
      [l.normalised_url for l in extract_links("t[.]me/whitepilled")],
      ["https://t.me/whitepilled"])
# Handles shorter than 3 chars are rejected: "t(.)me/x" is far more likely to
# be prose than a channel pointer, and a false link is worse than a missed one.
check("obfuscation is flagged", extract_links("t(.)me/chan99")[0].obfuscated, True)
check("implausibly short handle rejected", extract_links("t(.)me/x"), [])
check("clean link not flagged as obfuscated",
      extract_links("https://t.me/x")[0].obfuscated, False)
check("handle-only telegram pointer",
      [l.normalised_url for l in extract_links("tg: @euroident")], ["https://t.me/euroident"])
check("tracking params stripped",
      normalise_url("https://rumble.com/a/?utm_source=x&id=7&fbclid=z"),
      "https://rumble.com/a?id=7")
check("platform from subdomain", platform_of("https://web.telegram.org/k/"), "other")
check("platform from host", platform_of("https://discord.gg/abc"), "discord")
check("no false positive on prose", extract_links("nothing to see here"), [])
# Numeric dog whistles must survive extraction — see extract_tags().
check("numeric dog whistles kept", extract_tags("#2026 #1488 #88"), ["2026", "1488", "88"])

# ── importer ────────────────────────────────────────────────────────────────
section("corpus importer")
jsonl = tmp / "c.jsonl"
jsonl.write_text("\n".join([
    json.dumps({"_meta": {"note": "self-describing archive header"}}),
    json.dumps({"id": "1", "caption": "hi #fyp", "author": {"id": "a1", "handle": "bob"},
                "music": {"id": "m1", "title": "snd"}, "hashtags": ["fyp"],
                "comments": [{"id": "c1", "text": "t.me/x", "author": "eve"}],
                "view_count": "1,200"}),
    json.dumps({"caption": "no id, should be skipped"}),
]))
recs = list(read_records(jsonl))
check("_meta header line skipped", len(recs), 2)
mapped = [to_item(r, {}) for r in recs]
check("record without an id is rejected", mapped[1], None)
check("comma-formatted counts parsed", mapped[0].view_count, 1200)
check("nested account mapped", mapped[0].account.handle, "bob")

csvf = tmp / "c.csv"
csvf.write_text("id,caption,view_count\n9,hello #fyp,3400\n")
check("csv detected by content", to_item(next(read_records(csvf)), {}).view_count, 3400)

# ── persistence, end to end ─────────────────────────────────────────────────
section("persistence")
migrate.migrate(verbose=False)
src = W.ensure_source("test", "import", "")
counts = persist(CollectResult(items=[item]), "tiktok", src, "test")
check("one item stored", counts["items"], 1)
check("comments stored", counts["comments"], 2)
check("sound stored", counts["sounds"], 1)

link_rows = engine.query("SELECT extracted_from, normalised_url, context FROM links "
                         "ORDER BY extracted_from")
surfaces = sorted({r["extracted_from"] for r in link_rows})
check("link found in COMMENT text — the v1 bug", "comment" in surfaces, True)
check("that link is the obfuscated one",
      any(r["normalised_url"] == "https://t.me/europa88" and "[obfuscated]" in r["context"]
          for r in link_rows), True)
check("author stored only as a hash",
      engine.scalar("SELECT count(*) FROM comments WHERE author_hash IS NULL"), 0)
check("plaintext handle isolated in the side table",
      engine.scalar("SELECT handle FROM author_identities ORDER BY handle LIMIT 1"),
      "commenter_a")
check("hashing is deterministic",
      engine.scalar("SELECT count(DISTINCT author_hash) FROM comments"), 2)
check("re-persisting the same item does not duplicate",
      (persist(CollectResult(items=[item]), "tiktok", src, "test"),
       engine.scalar("SELECT count(*) FROM items"))[1], 1)
check("nor duplicate its comments", engine.scalar("SELECT count(*) FROM comments"), 2)
check("engagement history keeps both observations",
      engine.scalar("SELECT count(*) FROM item_metrics"), 1)

print()
if fails:
    sys.exit(f"FAILED: {len(fails)} check(s): {fails}")
print(f"  all checks passed")

#!/usr/bin/env python
"""Backfill the v1 corpus into the v2 schema.

Reads db/radicalmap.db READ-ONLY and writes db/radicalmap.v2.db. The v1 file is
the artefact behind the Zenodo DOI, so it is never opened for writing.

Two things are deliberately NOT copied verbatim:

  * v1's flag columns (videos.caption_flagged, comments.flagged, hashtags.category
    and severity) are interpretations, not observations. They land in
    classifications under a classifier_runs row tagged 'v1-legacy', so the old
    verdicts stay queryable and comparable against future runs instead of being
    baked into the entity rows where nothing can tell them apart from fact.

  * hashtag_cooccurrence and hashtags.usage_count are derived. v1 maintained
    them incrementally during ingest — one connection and commit per pair. They
    are recomputed by the analyse phase from item_tags.

One thing is ADDED that v1 never had: posted_at, decoded from the top 32 bits of
the numeric TikTok item id. v1 recorded only scraped_at, so no time series of
content was possible — only of collection.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radicalmap.core import identity
from radicalmap.db import engine, migrate
from radicalmap.db.repo import writers as W

V1_PATH = Path(__file__).resolve().parent.parent / "db" / "radicalmap.db"
PLATFORM = "tiktok"


def posted_at_from_id(tiktok_id: str) -> int | None:
    """TikTok item ids are snowflake-style: the high 32 bits are unix seconds."""
    if not tiktok_id or not str(tiktok_id).isdigit():
        return None
    ts = int(tiktok_id) >> 32
    # Sanity-bound it. TikTok predates neither 2016 nor outlives the present.
    return ts if 1451606400 < ts < 2000000000 else None


def open_v1() -> sqlite3.Connection:
    if not V1_PATH.exists():
        sys.exit(f"v1 database not found at {V1_PATH}")
    conn = sqlite3.connect(f"file:{V1_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> None:
    migrate.migrate(verbose=False)
    v1 = open_v1()
    pid = W.platform_id(PLATFORM)
    salt = identity.load_salt()

    counts_before = {t: v1.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("videos", "accounts", "hashtags", "video_hashtags",
                               "comments", "transcripts", "stickers", "external_links")}

    with engine.transaction():
        src = W.ensure_source("v1-corpus", "v1_migration",
                              "18-video methodological sample collected 2026-07-09")

        # ── accounts ────────────────────────────────────────────────────────
        # v1 never captured TikTok's numeric user id, only the handle, so the
        # surrogate is marked as handle-derived rather than silently pretending
        # to be a platform id. resolve_account_ids() upgrades these once a real
        # id is observed.
        accounts = v1.execute("SELECT * FROM accounts").fetchall()
        W.upsert_accounts([{
            "platform_id": pid,
            "platform_account_id": f"handle:{a['username']}",
            "handle": a["username"],
            "bio": a["bio"],
            "follower_count": a["follower_count"],
            "following_count": a["following_count"],
            "item_count": a["video_count"],
            "source_id": src,
            "collection_method": "v1_manual",
        } for a in accounts])
        acct_ids = W.account_ids(pid, [f"handle:{a['username']}" for a in accounts])

        # ── items ───────────────────────────────────────────────────────────
        videos = v1.execute("SELECT * FROM videos").fetchall()
        recovered = 0
        item_records = []
        for v in videos:
            pa = posted_at_from_id(v["tiktok_id"])
            recovered += pa is not None
            item_records.append({
                "platform_id": pid,
                "platform_item_id": v["tiktok_id"],
                "account_id": acct_ids.get(f"handle:{v['username']}"),
                "kind": "video",
                "url": v["url"],
                "caption": v["caption"],
                "posted_at": pa,
                "view_count": v["view_count"],
                "like_count": v["like_count"],
                "comment_count": v["comment_count"],
                "share_count": v["share_count"],
                "save_count": v["save_count"],
                "source_id": src,
                "collection_method": "v1_manual",
            })
        W.upsert_items(item_records)
        item_ids = W.item_ids(pid, [v["tiktok_id"] for v in videos])
        v1_video_to_item = {v["id"]: item_ids[v["tiktok_id"]] for v in videos}

        # ── tags ────────────────────────────────────────────────────────────
        hashtags = v1.execute("SELECT * FROM hashtags").fetchall()
        tag_ids = W.upsert_tags([h["tag"] for h in hashtags])
        v1_tag_to_tag = {h["id"]: tag_ids[h["tag"].strip().lstrip("#").lower()]
                         for h in hashtags if h["tag"].strip().lstrip("#").lower() in tag_ids}
        W.link_item_tags([
            (v1_video_to_item[r["video_id"]], v1_tag_to_tag[r["hashtag_id"]])
            for r in v1.execute("SELECT * FROM video_hashtags").fetchall()
            if r["video_id"] in v1_video_to_item and r["hashtag_id"] in v1_tag_to_tag
        ])

        # ── comments (pseudonymised) ────────────────────────────────────────
        comments = v1.execute("SELECT * FROM comments").fetchall()
        for c in comments:
            if c["author"]:
                identity.record_identity(c["author"], pid, salt)
        W.upsert_comments([{
            "item_id": v1_video_to_item[c["video_id"]],
            # v1 had no platform comment id and keyed uniqueness on
            # (video, author, text) — which silently dropped genuine duplicate
            # comments. A deterministic v1: surrogate keeps the backfill
            # re-runnable without inheriting that collision.
            "platform_comment_id": f"v1:{c['id']}",
            "author_hash": identity.author_hash(c["author"], salt) if c["author"] else None,
            "text": c["text"],
            "like_count": c["like_count"],
            "posted_at": c["timestamp"],
            "source_id": src,
            "collection_method": "v1_manual",
        } for c in comments if c["video_id"] in v1_video_to_item])
        v1_comment_to_id = {
            int(r["platform_comment_id"].split(":")[1]): int(r["id"])
            for r in engine.query(
                "SELECT id, platform_comment_id FROM comments WHERE platform_comment_id LIKE 'v1:%'")
        }

        # ── transcripts / stickers / links (all empty in v1, copied anyway) ──
        W.upsert_transcripts([
            {"item_id": v1_video_to_item[t["video_id"]], "content": t["content"],
             "language": t["language"]}
            for t in v1.execute("SELECT * FROM transcripts").fetchall()
            if t["video_id"] in v1_video_to_item])
        W.insert_stickers([
            {"item_id": v1_video_to_item[s["video_id"]], "sticker_id": s["sticker_id"],
             "sticker_type": s["sticker_type"], "description": s["description"]}
            for s in v1.execute("SELECT * FROM stickers").fetchall()
            if s["video_id"] in v1_video_to_item])

        # ── v1 verdicts → classifications ───────────────────────────────────
        run_id = engine.execute(
            """INSERT INTO classifier_runs (stage, version, notes, finished_at)
               VALUES ('lexicon', 'v1-legacy', ?, unixepoch())
               RETURNING id""",
            ("Flags carried over from RadicalMap v1 (regex lexicon, unmeasured). "
             "Preserved for comparison against v2 runs; not a validated baseline.",),
        ).fetchone()[0]

        verdicts = []
        for v in videos:
            iid = v1_video_to_item[v["id"]]
            if v["caption_flagged"]:
                for cat in filter(None, (v["caption_flag_categories"] or "").split(",")):
                    verdicts.append(("item", iid, run_id, "lexicon", cat.strip(), None,
                                     None, v["caption_flag_reason"], "caption"))
            if v["username_flagged"]:
                verdicts.append(("item", iid, run_id, "lexicon", "username_signal", None,
                                 None, v["username_flag_reason"], "username"))
        for a in accounts:
            aid = acct_ids.get(f"handle:{a['username']}")
            if aid and a["bio_flagged"]:
                for cat in filter(None, (a["bio_flag_categories"] or "").split(",")):
                    verdicts.append(("account", aid, run_id, "lexicon", cat.strip(), None,
                                     None, a["bio_flag_reason"], "bio"))
        for c in comments:
            cid = v1_comment_to_id.get(c["id"])
            if cid and c["flagged"]:
                for cat in filter(None, (c["flag_categories"] or "").split(",")):
                    verdicts.append(("comment", cid, run_id, "lexicon", cat.strip(),
                                     c["severity"], None, c["flag_reason"], "text"))
        for h in hashtags:
            tid = v1_tag_to_tag.get(h["id"])
            if tid and h["category"]:
                verdicts.append(("tag", tid, run_id, "lexicon", h["category"],
                                 h["severity"], None, h["definition"], "tag"))

        engine.connect().executemany(
            """INSERT INTO classifications (target_type, target_id, run_id, stage, label,
                                            severity, confidence, rationale, evidence_span)
               VALUES (?,?,?,?,?,?,?,?,?)""", verdicts)
        engine.execute("UPDATE classifier_runs SET target_count=? WHERE id=?",
                       (len(verdicts), run_id))

        # ── dictionary.json → versioned lexicon ─────────────────────────────
        dict_path = Path(__file__).resolve().parent.parent / "data" / "dictionary.json"
        lex_entries = 0
        if dict_path.exists():
            payload = json.loads(dict_path.read_text())
            # A list of objects, each carrying its own "tag" key — not a
            # tag-keyed map. (40 entries, despite the README's claim of 48;
            # that figure is the hashtags TABLE count, which includes 21
            # uncategorised tags harvested at ingest.)
            entries = payload.get("hashtags", [])
            import hashlib
            lex_hash = hashlib.sha256(dict_path.read_bytes()).hexdigest()
            lex_id = engine.execute(
                """INSERT INTO lexicon_versions (version, hash, notes) VALUES (?,?,?)
                   ON CONFLICT (version) DO UPDATE SET hash=excluded.hash RETURNING id""",
                ("v1-dictionary", lex_hash,
                 f"Imported from data/dictionary.json ({len(entries)} entries)")).fetchone()[0]
            engine.connect().executemany(
                """INSERT INTO lexicon_entries (lexicon_version_id, pattern, pattern_type,
                                                category, severity, definition, origin)
                   VALUES (?,?,'literal',?,?,?,?)""",
                [(lex_id, e["tag"], e.get("category", "uncategorised"), e.get("severity"),
                  e.get("definition"), e.get("origin")) for e in entries])
            lex_entries = len(entries)

    # ── report ──────────────────────────────────────────────────────────────
    after = {
        "items": engine.scalar("SELECT count(*) FROM items"),
        "accounts": engine.scalar("SELECT count(*) FROM accounts"),
        "tags": engine.scalar("SELECT count(*) FROM tags"),
        "item_tags": engine.scalar("SELECT count(*) FROM item_tags"),
        "comments": engine.scalar("SELECT count(*) FROM comments"),
        "transcripts": engine.scalar("SELECT count(*) FROM transcripts"),
        "stickers": engine.scalar("SELECT count(*) FROM stickers"),
    }
    print("  v1 -> v2 backfill")
    pairs = [("videos", "items"), ("accounts", "accounts"), ("hashtags", "tags"),
             ("video_hashtags", "item_tags"), ("comments", "comments"),
             ("transcripts", "transcripts"), ("stickers", "stickers")]
    ok = True
    for v1name, v2name in pairs:
        a, b = counts_before[v1name], after[v2name]
        match = "ok" if a == b else "MISMATCH"
        ok &= a == b
        print(f"    {v1name:16} {a:>6}  ->  {v2name:12} {b:>6}   {match}")
    print(f"    posted_at recovered from item ids: {recovered}/{len(videos)}")
    print(f"    v1 verdicts preserved as classifications: {len(verdicts)}")
    print(f"    lexicon entries imported: {lex_entries}")
    print(f"    author identities pseudonymised: "
          f"{engine.scalar('SELECT count(*) FROM author_identities')}")
    if not ok:
        sys.exit("row counts do not match — backfill is not faithful")


if __name__ == "__main__":
    main()

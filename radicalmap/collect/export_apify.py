"""Write the store back out in the Apify TikTok scraper's dataset shape.

The point is interchange: a collaborator whose tooling reads Apify exports can
read ours, and `radicalmap import --mapping apify` reads theirs. Round-tripping
through this module and the importer must reproduce the same entities, which
tests/test_apify_compat.py checks.

Commenters stay pseudonymous. The store never held their handles (persist.py
hashes them at write time), so `uniqueId` on a comment is the keyed hash with
an `anon:` prefix, never a reconstructed name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from ..db import engine
from .adapters.base import (ParsedAccount, ParsedComment, ParsedItem, ParsedSound,
                            ParsedSticker)
from .adapters.tiktok_web import to_apify


def _raw(text: str | None) -> dict:
    try:
        v = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}
    return v if isinstance(v, dict) else {}


def iter_items(platform: str = "tiktok", limit: int = 0) -> Iterator[ParsedItem]:
    pid = engine.scalar("SELECT id FROM platforms WHERE name = ?", (platform,))
    if pid is None:
        return
    sql = """SELECT i.*, a.platform_account_id, a.handle, a.display_name, a.bio,
                    a.follower_count, a.following_count, a.item_count, a.verified,
                    a.raw_json AS account_raw
               FROM items i LEFT JOIN accounts a ON a.id = i.account_id
              WHERE i.platform_id = ? AND i.is_deleted = 0 ORDER BY i.id"""
    params: tuple = (pid,)
    if limit:
        sql += " LIMIT ?"
        params = (pid, limit)
    for r in engine.query(sql, params):
        iid = int(r["id"])
        raw = _raw(r["raw_json"])
        account = None
        if r["platform_account_id"]:
            account = ParsedAccount(
                platform_account_id=r["platform_account_id"], handle=r["handle"],
                display_name=r["display_name"], bio=r["bio"],
                follower_count=r["follower_count"], following_count=r["following_count"],
                item_count=r["item_count"], verified=int(r["verified"] or 0),
                bio_link=_raw(r["account_raw"]).get("bio_link"))
        s = engine.query(
            """SELECT s.* FROM item_sounds x JOIN sounds s ON s.id = x.sound_id
                WHERE x.item_id = ? LIMIT 1""", (iid,))
        sound = None
        if s:
            s = s[0]
            sound = ParsedSound(platform_sound_id=s["platform_sound_id"], title=s["title"],
                                author_name=s["author_name"], duration_s=s["duration_s"],
                                is_original=int(s["is_original"] or 0),
                                play_url=_raw(s["raw_json"]).get("play_url"))
        tags = [t["tag"] for t in engine.query(
            """SELECT t.tag FROM item_tags x JOIN tags t ON t.id = x.tag_id
                WHERE x.item_id = ? ORDER BY t.tag""", (iid,))]
        comments = [ParsedComment(
            platform_comment_id=c["platform_comment_id"], text=c["text"],
            author_handle=f"anon:{c['author_hash']}" if c["author_hash"] else None,
            like_count=c["like_count"], reply_count=c["reply_count"],
            posted_at=c["posted_at"], parent_platform_comment_id=c["parent_pid"])
            for c in engine.query(
                """SELECT c.*, p.platform_comment_id AS parent_pid
                     FROM comments c LEFT JOIN comments p ON p.id = c.parent_comment_id
                    WHERE c.item_id = ? ORDER BY c.id""", (iid,))]
        tr = engine.query("SELECT content, language FROM transcripts WHERE item_id = ? LIMIT 1",
                          (iid,))
        stickers = [ParsedSticker(sticker_type=st["sticker_type"], text_content=st["text_content"])
                    for st in engine.query(
                        "SELECT sticker_type, text_content FROM stickers WHERE item_id = ?", (iid,))]
        yield ParsedItem(
            platform_item_id=r["platform_item_id"], url=r["url"], kind=r["kind"],
            caption=r["caption"], posted_at=r["posted_at"], duration_s=r["duration_s"],
            language=r["language"], view_count=r["view_count"], like_count=r["like_count"],
            comment_count=r["comment_count"], share_count=r["share_count"],
            save_count=r["save_count"], account=account, sound=sound, tags=tags,
            comments=comments, stickers=stickers,
            transcript=tr[0]["content"] if tr else None,
            mentions=list(raw.get("mentions") or []),
            is_pinned=raw.get("is_pinned"), is_ad=raw.get("is_ad"),
            location=raw.get("location"))


def export(path: Path, platform: str = "tiktok", limit: int = 0) -> int:
    """Write a JSON array (Apify's default dataset download format)."""
    n = 0
    with path.open("w", encoding="utf-8") as f:
        f.write("[")
        for item in iter_items(platform, limit):
            f.write(("," if n else "") + "\n" + json.dumps(to_apify(item), ensure_ascii=False))
            n += 1
        f.write("\n]\n")
    return n

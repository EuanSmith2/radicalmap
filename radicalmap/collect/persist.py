"""Write a CollectResult to the store.

Synchronous and short by design (see adapters/base.py). One transaction per
result, so a video and its account, sound, tags, comments, transcript and links
either all land or none do — v1 committed each of those separately, so an
interrupted ingest left half-written videos behind with no way to tell.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from ..core import identity
from ..db import engine
from ..db.repo import writers as W
from .adapters.base import CollectResult, ParsedItem
from .extract import extract_links, extract_tags


def _link_rows(source_type: str, source_id: int, surface: str, text: str | None):
    return [{
        "source_type": source_type, "source_id": source_id, "extracted_from": surface,
        "platform": l.platform, "url": l.url, "normalised_url": l.normalised_url,
        "domain": l.domain,
        "context": (l.context + (" [obfuscated]" if l.obfuscated else ""))[:500],
    } for l in extract_links(text)]


def _without_minors(result: CollectResult) -> tuple[CollectResult, int]:
    """Drop every account the platform flags as under 18, and everything they
    posted, before a single row is written. Done here rather than per adapter
    so no collection path (live, import, future platform) can bypass it."""
    minors = {a.platform_account_id for a in result.accounts if a.is_minor}
    minors |= {i.account.platform_account_id for i in result.items
               if i.account and i.account.is_minor}
    if not minors:
        return result, 0
    keep_items = [i for i in result.items
                  if not (i.account and i.account.platform_account_id in minors)]
    keep_accounts = [a for a in result.accounts if a.platform_account_id not in minors]
    return replace(result, items=keep_items, accounts=keep_accounts), len(minors)


def _item_raw(i: ParsedItem) -> dict | None:
    extra = {k: v for k, v in (("mentions", i.mentions or None), ("is_pinned", i.is_pinned),
                               ("is_ad", i.is_ad), ("location", i.location)) if v is not None}
    if not extra:
        return i.raw
    return {**(i.raw or {}), **extra}


def persist(result: CollectResult, platform: str, source_id: int,
            method: str) -> dict[str, int]:
    counts = {k: 0 for k in
              ("items", "accounts", "tags", "comments", "sounds", "links",
               "transcripts", "stickers", "skipped_minors")}
    if not result:
        return counts
    result, counts["skipped_minors"] = _without_minors(result)
    if not result:
        return counts

    pid = W.platform_id(platform)
    salt = identity.load_salt()

    with engine.transaction():
        # ── accounts first: items reference them ────────────────────────────
        accounts = {a.platform_account_id: a for a in result.accounts}
        for item in result.items:
            if item.account:
                accounts.setdefault(item.account.platform_account_id, item.account)
        if accounts:
            W.upsert_accounts([{
                "platform_id": pid, "platform_account_id": a.platform_account_id,
                "handle": a.handle, "display_name": a.display_name, "bio": a.bio,
                "follower_count": a.follower_count, "following_count": a.following_count,
                "item_count": a.item_count, "verified": a.verified,
                "source_id": source_id, "collection_method": method,
                "raw_json": ({**(a.raw or {}), "bio_link": a.bio_link} if a.bio_link else a.raw),
            } for a in accounts.values()])
            counts["accounts"] = len(accounts)
        acct_ids = W.account_ids(pid, list(accounts))

        # Links in bios — one of the two surfaces v1 did scan.
        for a in accounts.values():
            aid = acct_ids.get(a.platform_account_id)
            if aid and a.bio:
                counts["links"] += W.insert_links(_link_rows("account", aid, "bio", a.bio))
            # Link-in-bio is a structured field, stored as the bio surface so it
            # joins the same links_to edges as a URL written into the bio text.
            if aid and a.bio_link:
                counts["links"] += W.insert_links(_link_rows("account", aid, "bio", a.bio_link))

        if not result.items:
            return counts

        # ── items ───────────────────────────────────────────────────────────
        W.upsert_items([{
            "platform_id": pid, "platform_item_id": i.platform_item_id,
            "account_id": acct_ids.get(i.account.platform_account_id) if i.account else None,
            "kind": i.kind, "url": i.url, "title": i.title, "caption": i.caption,
            "description": i.description, "posted_at": i.posted_at,
            "duration_s": i.duration_s, "language": i.language,
            "view_count": i.view_count, "like_count": i.like_count,
            "comment_count": i.comment_count, "share_count": i.share_count,
            "save_count": i.save_count, "source_id": source_id,
            "collection_method": method, "raw_json": _item_raw(i),
        } for i in result.items])
        counts["items"] = len(result.items)
        ids = W.item_ids(pid, [i.platform_item_id for i in result.items])

        # ── tags: declared plus any only present inline in the caption ──────
        all_tags: set[str] = set()
        per_item: dict[str, list[str]] = {}
        for i in result.items:
            tags = list(dict.fromkeys(
                [t.lstrip("#") for t in i.tags]
                + extract_tags(i.caption) + extract_tags(i.description)))
            per_item[i.platform_item_id] = tags
            all_tags.update(t.lower() for t in tags)
        if all_tags:
            tag_ids = W.upsert_tags(all_tags)
            W.link_item_tags([
                (ids[pi], tag_ids[t.lower()])
                for pi, tags in per_item.items() if pi in ids
                for t in tags if t.lower() in tag_ids])
            counts["tags"] = len(all_tags)

        # ── sounds ──────────────────────────────────────────────────────────
        sounds = {i.sound.platform_sound_id: i.sound for i in result.items if i.sound}
        if sounds:
            sound_ids = W.upsert_sounds([{
                "platform_id": pid, "platform_sound_id": s.platform_sound_id,
                "title": s.title, "author_name": s.author_name,
                "duration_s": s.duration_s, "is_original": s.is_original,
                "use_count": s.use_count, "source_id": source_id,
                "raw_json": ({**(s.raw or {}), "play_url": s.play_url} if s.play_url else s.raw),
            } for s in sounds.values()])
            W.link_item_sounds([
                (ids[i.platform_item_id], sound_ids[i.sound.platform_sound_id])
                for i in result.items
                if i.sound and i.platform_item_id in ids
                and i.sound.platform_sound_id in sound_ids])
            counts["sounds"] = len(sounds)

        # ── comments, pseudonymised at write time ───────────────────────────
        rows, link_rows = [], []
        for i in result.items:
            iid = ids.get(i.platform_item_id)
            if iid is None:
                continue
            for n, c in enumerate(i.comments):
                h = identity.record_identity(c.author_handle, pid, salt) if c.author_handle else None
                rows.append({
                    "item_id": iid,
                    # A stable surrogate when the platform gave us no id, so a
                    # re-fetch updates rather than duplicating.
                    "platform_comment_id": c.platform_comment_id or f"idx:{n}",
                    "author_hash": h, "text": c.text, "like_count": c.like_count,
                    "reply_count": c.reply_count, "posted_at": c.posted_at,
                    "source_id": source_id, "collection_method": method,
                    "raw_json": c.raw,
                })
            # THE v1 BUG, FIXED. Its Telegram regex read bios and captions and
            # never comment text — which is where recruitment links actually
            # get posted, and why external_links was empty.
            link_rows += _link_rows("item", iid, "caption", i.caption)
            link_rows += _link_rows("item", iid, "description", i.description)
            link_rows += _link_rows("item", iid, "transcript", i.transcript)
        if rows:
            counts["comments"] = W.upsert_comments(rows)

        comment_ids = {}
        if rows:
            item_id_list = [ids[i.platform_item_id] for i in result.items if i.platform_item_id in ids]
            marks = ",".join("?" * len(item_id_list))
            comment_ids = {
                (r["item_id"], r["platform_comment_id"]): int(r["id"])
                for r in engine.query(
                    f"SELECT id, item_id, platform_comment_id FROM comments "
                    f"WHERE item_id IN ({marks})", item_id_list)}
        parents = []
        for i in result.items:
            iid = ids.get(i.platform_item_id)
            for n, c in enumerate(i.comments):
                cid = comment_ids.get((iid, c.platform_comment_id or f"idx:{n}"))
                if cid:
                    link_rows += _link_rows("comment", cid, "comment", c.text)
                    pid_ = comment_ids.get((iid, c.parent_platform_comment_id))
                    if c.parent_platform_comment_id and pid_:
                        parents.append((pid_, cid))
        # Replies keep their thread: a reply is often the recruitment step
        # ("dm me", a link) answering an innocuous top-level comment.
        if parents:
            engine.connect().executemany(
                "UPDATE comments SET parent_comment_id = ? WHERE id = ?", parents)

        if link_rows:
            counts["links"] += W.insert_links(link_rows)

        # ── attachments ─────────────────────────────────────────────────────
        transcripts = [{"item_id": ids[i.platform_item_id], "content": i.transcript,
                        "language": i.transcript_language}
                       for i in result.items if i.transcript and i.platform_item_id in ids]
        if transcripts:
            counts["transcripts"] = W.upsert_transcripts(transcripts)

        stickers = [{"item_id": ids[i.platform_item_id], "sticker_id": s.sticker_id,
                     "sticker_type": s.sticker_type, "description": s.description,
                     "text_content": s.text_content}
                    for i in result.items if i.platform_item_id in ids for s in i.stickers]
        if stickers:
            counts["stickers"] = W.insert_stickers(stickers)

    return counts

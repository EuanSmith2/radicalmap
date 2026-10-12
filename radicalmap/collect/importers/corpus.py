"""Bulk import of an already-collected corpus.

The new supply includes a dataset someone else assembled. It must land in the
same tables, through the same writers, with the same provenance as anything
scraped — never a side-loaded table, or two months later nobody can say which
rows came from where or under what basis they were collected.

Mapping is configuration, not code: a dict from our field names to the source's
(dotted paths supported), so a new corpus shape needs a JSON mapping rather than
a new importer module.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..adapters.base import (CollectResult, ParsedAccount, ParsedComment,
                             ParsedItem, ParsedSound)
from ..persist import persist
from ...db.repo import writers as W

DEFAULT_MAPPING: dict[str, str] = {
    "platform_item_id": "id",
    "url": "url",
    "caption": "caption",
    "posted_at": "posted_at",
    "view_count": "view_count",
    "like_count": "like_count",
    "comment_count": "comment_count",
    "share_count": "share_count",
    "account_id": "author.id",
    "account_handle": "author.handle",
    "account_bio": "author.bio",
    "account_followers": "author.followers",
    "sound_id": "music.id",
    "sound_title": "music.title",
    "sound_author": "music.author",
    "tags": "hashtags",
    "comments": "comments",
    "comment_text": "text",
    "comment_author": "author",
    "comment_id": "id",
    "transcript": "transcript",
}


def dig(record: Any, path: str | None) -> Any:
    if not path:
        return None
    cur = record
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _flag(v: Any) -> bool | None:
    return None if v is None or v == "" else bool(v)


def read_records(path: Path) -> Iterator[dict[str, Any]]:
    """JSONL, JSON array, or CSV — decided by content, not by extension, since
    corpus files are routinely misnamed."""
    text_head = path.open("r", encoding="utf-8", errors="replace").read(4096).lstrip()
    if path.suffix.lower() == ".csv" or ("," in text_head.split("\n")[0]
                                         and not text_head.startswith(("{", "["))):
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            yield from csv.DictReader(f)
        return
    with path.open("r", encoding="utf-8", errors="replace") as f:
        if text_head.startswith("["):
            for rec in json.load(f):
                yield rec
            return
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Self-describing archives (the shape edmo-attribution-check writes)
            # put a _meta line first; it is not a record.
            if isinstance(rec, dict) and rec.get("_meta"):
                continue
            yield rec


def to_item(record: dict[str, Any], mapping: dict[str, str]) -> ParsedItem | None:
    m = {**DEFAULT_MAPPING, **mapping}
    pid = dig(record, m["platform_item_id"])
    if pid in (None, ""):
        return None

    account = None
    acct_id = dig(record, m.get("account_id"))
    handle = dig(record, m.get("account_handle"))
    if acct_id or handle:
        account = ParsedAccount(
            platform_account_id=str(acct_id or f"handle:{handle}"),
            handle=handle, bio=dig(record, m.get("account_bio")),
            display_name=dig(record, m.get("account_display_name")),
            follower_count=_int(dig(record, m.get("account_followers"))),
            verified=int(bool(dig(record, m.get("account_verified")))),
            bio_link=dig(record, m.get("account_bio_link")) or None,
            # Honoured on import too: a corpus someone else collected must not
            # be a way round the minors rule (persist.py drops these).
            is_minor=bool(dig(record, m.get("account_is_minor"))))

    sound = None
    sid = dig(record, m.get("sound_id"))
    stitle = dig(record, m.get("sound_title"))
    if sid or stitle:
        sound = ParsedSound(platform_sound_id=str(sid or f"import:{stitle}"),
                            title=stitle, author_name=dig(record, m.get("sound_author")),
                            is_original=int(bool(dig(record, m.get("sound_original")))),
                            play_url=dig(record, m.get("sound_play_url")) or None)

    tags = dig(record, m.get("tags")) or []
    if isinstance(tags, str):
        tags = [t for t in tags.replace(",", " ").split() if t]
    # Some exports (Apify's among them) give tags as objects, not strings.
    tags = [dig(t, m.get("tag_name", "name")) if isinstance(t, dict) else t for t in tags]
    tags = [t for t in tags if t]

    mentions = dig(record, m.get("mentions")) or []
    if isinstance(mentions, str):
        mentions = mentions.replace(",", " ").split()
    mentions = [str(x).lstrip("@") for x in mentions if x]

    comments = []
    for c in (dig(record, m.get("comments")) or []):
        if isinstance(c, str):
            comments.append(ParsedComment(platform_comment_id=None, text=c))
        elif isinstance(c, dict):
            text = dig(c, m.get("comment_text", "text"))
            if text:
                comments.append(ParsedComment(
                    platform_comment_id=(str(dig(c, m.get("comment_id", "id")))
                                         if dig(c, m.get("comment_id", "id")) else None),
                    text=str(text),
                    author_handle=dig(c, m.get("comment_author", "author")),
                    like_count=_int(dig(c, m.get("comment_likes", "like_count"))),
                    posted_at=_int(dig(c, m.get("comment_posted_at", "timestamp"))
                                   or dig(c, "posted_at")),
                    parent_platform_comment_id=(str(dig(c, m.get("comment_parent")))
                                                if dig(c, m.get("comment_parent")) else None)))

    return ParsedItem(
        platform_item_id=str(pid),
        url=dig(record, m.get("url")),
        caption=dig(record, m.get("caption")),
        posted_at=_int(dig(record, m.get("posted_at"))),
        view_count=_int(dig(record, m.get("view_count"))),
        like_count=_int(dig(record, m.get("like_count"))),
        comment_count=_int(dig(record, m.get("comment_count"))),
        share_count=_int(dig(record, m.get("share_count"))),
        save_count=_int(dig(record, m.get("save_count"))),
        language=dig(record, m.get("language")) or None,
        account=account, sound=sound,
        tags=[str(t).lstrip("#") for t in tags],
        comments=comments,
        transcript=dig(record, m.get("transcript")),
        mentions=mentions,
        is_pinned=_flag(dig(record, m.get("is_pinned"))),
        is_ad=_flag(dig(record, m.get("is_ad"))),
        location=dig(record, m.get("location")) or None,
        raw=record,
    )


def import_file(path: Path, platform: str = "tiktok", *, mapping: dict[str, str] | None = None,
                source_name: str | None = None, batch_size: int = 1000,
                limit: int = 0, verbose: bool = True) -> dict[str, int]:
    mapping = mapping or {}
    source_id = W.ensure_source(source_name or f"import:{path.name}", "import", str(path))
    totals: dict[str, int] = {}
    batch: list[ParsedItem] = []
    seen = skipped = 0

    def flush() -> None:
        nonlocal batch
        if not batch:
            return
        for k, v in persist(CollectResult(items=batch), platform, source_id, "import").items():
            totals[k] = totals.get(k, 0) + v
        batch = []

    for record in read_records(path):
        if limit and seen >= limit:
            break
        item = to_item(record, mapping)
        if item is None:
            skipped += 1
            continue
        batch.append(item)
        seen += 1
        if len(batch) >= batch_size:
            flush()
            if verbose and seen % (batch_size * 10) == 0:
                print(f"    {seen:,} records...")
    flush()
    totals["records_read"] = seen
    totals["records_skipped"] = skipped
    return totals

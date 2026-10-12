"""Batch writers for the core entities.

Every function here takes a SEQUENCE and issues one statement, because the
defining defect of v1's data layer was per-row work: insert_video() opened a
connection, inserted, committed, then re-queried by URL just to learn the rowid
it had already been handed. Nothing in this module commits; callers own the
transaction, so a whole video — item, account, tags, sound, comments — lands
atomically or not at all.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence

from .. import engine


def _json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def platform_id(name: str) -> int:
    row = engine.query_one("SELECT id FROM platforms WHERE name = ?", (name,))
    if row is None:
        raise KeyError(f"unknown platform {name!r}; add it in a migration")
    return int(row["id"])


def ensure_source(name: str, kind: str, description: str = "") -> int:
    engine.execute(
        "INSERT OR IGNORE INTO sources (name, kind, description) VALUES (?,?,?)",
        (name, kind, description),
    )
    return int(engine.scalar("SELECT id FROM sources WHERE name = ?", (name,)))


# ─── accounts ────────────────────────────────────────────────────────────────

ACCOUNT_SQL = """
INSERT INTO accounts (platform_id, platform_account_id, handle, display_name, bio,
                      follower_count, following_count, item_count, verified,
                      source_id, collection_method, raw_json)
VALUES (:platform_id, :platform_account_id, :handle, :display_name, :bio,
        :follower_count, :following_count, :item_count, :verified,
        :source_id, :collection_method, :raw_json)
ON CONFLICT (platform_id, platform_account_id) DO UPDATE SET
    handle          = coalesce(excluded.handle, accounts.handle),
    display_name    = coalesce(excluded.display_name, accounts.display_name),
    bio             = coalesce(excluded.bio, accounts.bio),
    -- coalesce, never overwrite-with-null: a cheap metadata refresh must not
    -- erase a follower count that a more thorough earlier pass established.
    follower_count  = coalesce(excluded.follower_count, accounts.follower_count),
    following_count = coalesce(excluded.following_count, accounts.following_count),
    item_count      = coalesce(excluded.item_count, accounts.item_count),
    raw_json        = coalesce(excluded.raw_json, accounts.raw_json),
    last_seen_at    = unixepoch()
"""

ACCOUNT_FIELDS = ("platform_id", "platform_account_id", "handle", "display_name", "bio",
                  "follower_count", "following_count", "item_count", "verified",
                  "source_id", "collection_method", "raw_json")


def _rows(records: Iterable[Mapping[str, Any]], fields: Sequence[str],
          defaults: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    base: dict[str, Any] = {f: None for f in fields}
    if defaults:
        base.update(defaults)
    out = []
    for r in records:
        row = dict(base)
        row.update({k: v for k, v in r.items() if k in base})
        if "raw_json" in row:
            row["raw_json"] = _json(row["raw_json"])
        out.append(row)
    return out


def upsert_accounts(records: Iterable[Mapping[str, Any]]) -> int:
    rows = _rows(records, ACCOUNT_FIELDS, {"verified": 0})
    if not rows:
        return 0
    engine.connect().executemany(ACCOUNT_SQL, rows)
    # Handle history: a rename after a takedown is a research signal, so record
    # every handle an account has ever been seen under rather than just the last.
    engine.connect().executemany(
        """INSERT INTO account_handles (account_id, handle)
           SELECT id, ? FROM accounts WHERE platform_id = ? AND platform_account_id = ?
           ON CONFLICT (account_id, handle) DO UPDATE SET last_seen = unixepoch()""",
        [(r["handle"], r["platform_id"], r["platform_account_id"]) for r in rows if r["handle"]],
    )
    return len(rows)


def account_ids(platform: int, platform_account_ids: Sequence[str]) -> dict[str, int]:
    if not platform_account_ids:
        return {}
    marks = ",".join("?" * len(platform_account_ids))
    rows = engine.query(
        f"SELECT id, platform_account_id FROM accounts WHERE platform_id = ? "
        f"AND platform_account_id IN ({marks})",
        (platform, *platform_account_ids),
    )
    return {r["platform_account_id"]: int(r["id"]) for r in rows}


# ─── items ───────────────────────────────────────────────────────────────────

ITEM_SQL = """
INSERT INTO items (platform_id, platform_item_id, account_id, kind, url, title,
                   caption, description, posted_at, duration_s, language,
                   view_count, like_count, comment_count, share_count, save_count,
                   source_id, collection_method, raw_json)
VALUES (:platform_id, :platform_item_id, :account_id, :kind, :url, :title,
        :caption, :description, :posted_at, :duration_s, :language,
        :view_count, :like_count, :comment_count, :share_count, :save_count,
        :source_id, :collection_method, :raw_json)
ON CONFLICT (platform_id, platform_item_id) DO UPDATE SET
    account_id    = coalesce(excluded.account_id, items.account_id),
    caption       = coalesce(excluded.caption, items.caption),
    title         = coalesce(excluded.title, items.title),
    description   = coalesce(excluded.description, items.description),
    posted_at     = coalesce(excluded.posted_at, items.posted_at),
    duration_s    = coalesce(excluded.duration_s, items.duration_s),
    view_count    = coalesce(excluded.view_count, items.view_count),
    like_count    = coalesce(excluded.like_count, items.like_count),
    comment_count = coalesce(excluded.comment_count, items.comment_count),
    share_count   = coalesce(excluded.share_count, items.share_count),
    save_count    = coalesce(excluded.save_count, items.save_count),
    raw_json      = coalesce(excluded.raw_json, items.raw_json),
    last_seen_at  = unixepoch()
"""

ITEM_FIELDS = ("platform_id", "platform_item_id", "account_id", "kind", "url", "title",
               "caption", "description", "posted_at", "duration_s", "language",
               "view_count", "like_count", "comment_count", "share_count", "save_count",
               "source_id", "collection_method", "raw_json")


def upsert_items(records: Iterable[Mapping[str, Any]]) -> int:
    rows = _rows(records, ITEM_FIELDS, {"kind": "video"})
    if not rows:
        return 0
    engine.connect().executemany(ITEM_SQL, rows)
    # Engagement is a trajectory. Snapshot each observation so a later refresh
    # adds a point rather than destroying the previous one, which is what v1's
    # refresh_stats.py did.
    snaps = [
        (r["platform_id"], r["platform_item_id"], r["view_count"], r["like_count"],
         r["comment_count"], r["share_count"], r["save_count"])
        for r in rows
        if any(r[k] is not None for k in
               ("view_count", "like_count", "comment_count", "share_count", "save_count"))
    ]
    if snaps:
        engine.connect().executemany(
            """INSERT OR REPLACE INTO item_metrics
                   (item_id, observed_at, view_count, like_count, comment_count, share_count, save_count)
               SELECT id, unixepoch(), ?, ?, ?, ?, ? FROM items
               WHERE platform_id = ? AND platform_item_id = ?""",
            [(s[2], s[3], s[4], s[5], s[6], s[0], s[1]) for s in snaps],
        )
    return len(rows)


def item_ids(platform: int, platform_item_ids: Sequence[str]) -> dict[str, int]:
    if not platform_item_ids:
        return {}
    marks = ",".join("?" * len(platform_item_ids))
    rows = engine.query(
        f"SELECT id, platform_item_id FROM items WHERE platform_id = ? "
        f"AND platform_item_id IN ({marks})",
        (platform, *platform_item_ids),
    )
    return {r["platform_item_id"]: int(r["id"]) for r in rows}


# ─── tags ────────────────────────────────────────────────────────────────────

def upsert_tags(tags: Iterable[str]) -> dict[str, int]:
    """Record observed tags. Note there is no category or severity here — that
    is a judgement, and it belongs to a versioned classifier run, not to the
    observation. v1 stored them on the tag row, so reclassifying meant a
    destructive UPDATE with no record of the previous verdict."""
    norm = {t.strip().lstrip("#").lower(): t.strip().lstrip("#") for t in tags if t and t.strip()}
    if not norm:
        return {}
    engine.connect().executemany(
        "INSERT OR IGNORE INTO tags (tag, normalised, display_name) VALUES (?,?,?)",
        [(orig, n, f"#{orig}") for n, orig in norm.items()],
    )
    marks = ",".join("?" * len(norm))
    rows = engine.query(
        f"SELECT id, normalised FROM tags WHERE normalised IN ({marks})", tuple(norm))
    return {r["normalised"]: int(r["id"]) for r in rows}


def link_item_tags(pairs: Iterable[tuple[int, int]]) -> int:
    pairs = list(pairs)
    if not pairs:
        return 0
    engine.connect().executemany(
        "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?,?)", pairs)
    return len(pairs)


# ─── sounds ──────────────────────────────────────────────────────────────────

SOUND_SQL = """
INSERT INTO sounds (platform_id, platform_sound_id, title, author_name, duration_s,
                    is_original, use_count, local_path, audio_sha256, fingerprint,
                    source_id, raw_json)
VALUES (:platform_id, :platform_sound_id, :title, :author_name, :duration_s,
        :is_original, :use_count, :local_path, :audio_sha256, :fingerprint,
        :source_id, :raw_json)
ON CONFLICT (platform_id, platform_sound_id) DO UPDATE SET
    title        = coalesce(excluded.title, sounds.title),
    author_name  = coalesce(excluded.author_name, sounds.author_name),
    duration_s   = coalesce(excluded.duration_s, sounds.duration_s),
    use_count    = coalesce(excluded.use_count, sounds.use_count),
    local_path   = coalesce(excluded.local_path, sounds.local_path),
    audio_sha256 = coalesce(excluded.audio_sha256, sounds.audio_sha256),
    fingerprint  = coalesce(excluded.fingerprint, sounds.fingerprint),
    raw_json     = coalesce(excluded.raw_json, sounds.raw_json)
"""

SOUND_FIELDS = ("platform_id", "platform_sound_id", "title", "author_name", "duration_s",
                "is_original", "use_count", "local_path", "audio_sha256", "fingerprint",
                "source_id", "raw_json")


def upsert_sounds(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    rows = _rows(records, SOUND_FIELDS, {"is_original": 0})
    if not rows:
        return {}
    engine.connect().executemany(SOUND_SQL, rows)
    ids = [r["platform_sound_id"] for r in rows]
    marks = ",".join("?" * len(ids))
    found = engine.query(
        f"SELECT id, platform_sound_id FROM sounds WHERE platform_id = ? "
        f"AND platform_sound_id IN ({marks})", (rows[0]["platform_id"], *ids))
    return {r["platform_sound_id"]: int(r["id"]) for r in found}


def link_item_sounds(pairs: Iterable[tuple[int, int]]) -> int:
    pairs = list(pairs)
    if not pairs:
        return 0
    engine.connect().executemany(
        "INSERT OR IGNORE INTO item_sounds (item_id, sound_id) VALUES (?,?)", pairs)
    return len(pairs)


# ─── comments ────────────────────────────────────────────────────────────────

COMMENT_FIELDS = ("item_id", "platform_comment_id", "parent_comment_id", "author_hash",
                  "author_account_id", "text", "like_count", "reply_count", "posted_at",
                  "source_id", "collection_method", "raw_json")

COMMENT_SQL = """
INSERT INTO comments (item_id, platform_comment_id, parent_comment_id, author_hash,
                      author_account_id, text, like_count, reply_count, posted_at,
                      source_id, collection_method, raw_json)
VALUES (:item_id, :platform_comment_id, :parent_comment_id, :author_hash,
        :author_account_id, :text, :like_count, :reply_count, :posted_at,
        :source_id, :collection_method, :raw_json)
ON CONFLICT (item_id, platform_comment_id) DO UPDATE SET
    like_count  = coalesce(excluded.like_count, comments.like_count),
    reply_count = coalesce(excluded.reply_count, comments.reply_count)
"""


def upsert_comments(records: Iterable[Mapping[str, Any]]) -> int:
    rows = _rows(records, COMMENT_FIELDS)
    if not rows:
        return 0
    engine.connect().executemany(COMMENT_SQL, rows)
    return len(rows)


# ─── attachments ─────────────────────────────────────────────────────────────

def upsert_transcripts(records: Iterable[Mapping[str, Any]]) -> int:
    rows = _rows(records, ("item_id", "content", "language", "source", "confidence"),
                 {"language": "en", "source": "auto"})
    if not rows:
        return 0
    engine.connect().executemany(
        """INSERT INTO transcripts (item_id, content, language, source, confidence)
           VALUES (:item_id, :content, :language, :source, :confidence)
           ON CONFLICT (item_id) DO UPDATE SET
               content = excluded.content, language = excluded.language,
               source = excluded.source, confidence = excluded.confidence""",
        rows)
    return len(rows)


def insert_links(records: Iterable[Mapping[str, Any]]) -> int:
    rows = _rows(records, ("source_type", "source_id", "extracted_from", "platform",
                           "url", "normalised_url", "domain", "context"))
    if not rows:
        return 0
    engine.connect().executemany(
        """INSERT INTO links (source_type, source_id, extracted_from, platform,
                              url, normalised_url, domain, context)
           VALUES (:source_type, :source_id, :extracted_from, :platform,
                   :url, :normalised_url, :domain, :context)""",
        rows)
    return len(rows)


def insert_stickers(records: Iterable[Mapping[str, Any]]) -> int:
    rows = _rows(records, ("item_id", "sticker_id", "sticker_type", "description", "text_content"))
    if not rows:
        return 0
    engine.connect().executemany(
        """INSERT INTO stickers (item_id, sticker_id, sticker_type, description, text_content)
           VALUES (:item_id, :sticker_id, :sticker_type, :description, :text_content)""",
        rows)
    return len(rows)

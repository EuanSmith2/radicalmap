"""TikTok's own JSON, parsed in full.

Why this exists
---------------
Every TikTok video and profile page embeds the record the app renders from, in
a `__UNIVERSAL_DATA_FOR_REHYDRATION__` script tag: `webapp.video-detail` holds
the video's `itemStruct`, `webapp.user-detail` the profile. yt-dlp reads that
same blob and then keeps a fraction of it. Dropped on the floor, and all needed
here:

  * the sound's platform id and audio URL (yt-dlp keeps title/artist only, so
    sound identity had to be guessed from a slug of the track name);
  * structured @-mentions (`textExtra`), which are account-to-account ties;
  * the author's bio and link-in-bio;
  * pinned / ad flags, the creation-region code, sticker text.

Commercial scrapers sell exactly this field set. Parsing it ourselves gives the
same coverage with no per-result cost and every field under our control.

What this module is not
-----------------------
It does no I/O. Fetching is the adapter's job and is subject to the network
gate, the per-host pacer and robots.txt. Everything here is a pure function of
a payload, so the field mapping is verified offline against fixtures.

Apify compatibility
-------------------
`to_apify` / `APIFY_IMPORT_MAPPING` speak the dataset shape of the widely used
Apify TikTok scraper, so a corpus a collaborator collected that way imports
through the normal importer, and ours can be handed back in a shape their
tooling already reads.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import (AdapterError, ParsedAccount, ParsedComment, ParsedItem,
                   ParsedSound, ParsedSticker, PermanentError)

_REHYDRATION_RE = re.compile(
    r'<script[^>]+\bid="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)

# Status codes the web app reports inside the blob. Mirrors the handling in
# yt-dlp's extractor so the two transports classify failures identically.
STATUS_PRIVATE = {10216: "private post", 10222: "private account"}
STATUS_GONE = {10204: "video not found", 10221: "account banned", 10202: "user not found"}


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def rehydration(html: str) -> dict[str, Any]:
    """The `__DEFAULT_SCOPE__` dict from a page, or {} when the page carries
    none (a challenge interstitial, a login wall, a markup change)."""
    m = _REHYDRATION_RE.search(html or "")
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    scope = data.get("__DEFAULT_SCOPE__") if isinstance(data, dict) else None
    return scope if isinstance(scope, dict) else {}


def _check_status(detail: dict[str, Any], what: str) -> None:
    code = _int(detail.get("statusCode")) or 0
    if code == 0:
        return
    if code in STATUS_PRIVATE or code in STATUS_GONE:
        raise PermanentError(f"{what}: {STATUS_PRIVATE.get(code) or STATUS_GONE[code]} ({code})")
    raise AdapterError(f"{what}: unexpected statusCode {code}")


def item_struct(scope: dict[str, Any]) -> dict[str, Any] | None:
    detail = scope.get("webapp.video-detail")
    if not isinstance(detail, dict):
        return None
    _check_status(detail, "video")
    struct = (detail.get("itemInfo") or {}).get("itemStruct")
    return struct if isinstance(struct, dict) and struct.get("id") else None


def user_detail(scope: dict[str, Any]) -> dict[str, Any] | None:
    detail = scope.get("webapp.user-detail")
    if not isinstance(detail, dict):
        return None
    _check_status(detail, "account")
    info = detail.get("userInfo")
    return info if isinstance(info, dict) and info.get("user") else None


# ── accounts ────────────────────────────────────────────────────────────────

def is_minor(user: dict[str, Any] | None) -> bool:
    """True only when the platform itself says so. Absence is not evidence."""
    if not isinstance(user, dict):
        return False
    return bool(user.get("isUnderAge18") or user.get("is_under_age_18"))


def _bio_link(user: dict[str, Any]) -> str | None:
    link = user.get("bioLink")
    if isinstance(link, dict):
        link = link.get("link")
    return link if isinstance(link, str) and link.strip() else None


def parse_account(user: dict[str, Any], stats: dict[str, Any] | None = None) -> ParsedAccount:
    stats = stats or {}
    handle = user.get("uniqueId")
    return ParsedAccount(
        platform_account_id=str(user.get("id") or f"handle:{handle}"),
        handle=handle,
        display_name=user.get("nickname"),
        bio=user.get("signature") or None,
        follower_count=_int(stats.get("followerCount")),
        following_count=_int(stats.get("followingCount")),
        item_count=_int(stats.get("videoCount")),
        verified=int(bool(user.get("verified"))),
        bio_link=_bio_link(user),
        is_minor=is_minor(user),
        raw={"secUid": user.get("secUid"), "privateAccount": user.get("privateAccount"),
             "region": user.get("region"), "createTime": user.get("createTime")},
    )


def parse_user_detail(info: dict[str, Any]) -> ParsedAccount:
    return parse_account(info["user"], info.get("stats") or info.get("statsV2"))


# ── items ───────────────────────────────────────────────────────────────────

def subtitle_url(struct: dict[str, Any]) -> tuple[str | None, str]:
    """Best English WebVTT track, else any WebVTT track."""
    subs = ((struct.get("video") or {}).get("subtitleInfos")) or []
    best, lang = None, "en"
    for s in subs:
        if not isinstance(s, dict) or not s.get("Url"):
            continue
        if (s.get("Format") or "webvtt").lower() != "webvtt":
            continue
        code = (s.get("LanguageCodeName") or "").lower()
        if code.startswith("en"):
            return s["Url"], "en"
        if best is None:
            best, lang = s["Url"], code.split("-")[0] or "und"
    return best, lang


def parse_item_struct(struct: dict[str, Any]) -> ParsedItem:
    vid = str(struct.get("id") or "")
    if not vid:
        raise PermanentError("itemStruct has no id")

    author = struct.get("author") if isinstance(struct.get("author"), dict) else {}
    account = parse_account(author, struct.get("authorStats")) if author else None

    music = struct.get("music") if isinstance(struct.get("music"), dict) else {}
    sound = None
    if music.get("id") or music.get("title"):
        sound = ParsedSound(
            platform_sound_id=str(music.get("id") or f"tt:{music.get('title')}"),
            title=music.get("title"),
            author_name=music.get("authorName"),
            duration_s=_int(music.get("duration")),
            is_original=int(bool(music.get("original"))),
            play_url=music.get("playUrl") or None,
            raw={"album": music.get("album"), "id_source": "platform" if music.get("id") else "title"},
        )

    extras = struct.get("textExtra") or []
    for c in struct.get("contents") or []:          # slideshows carry text per slide
        if isinstance(c, dict):
            extras = [*extras, *(c.get("textExtra") or [])]
    tags = [e["hashtagName"] for e in extras
            if isinstance(e, dict) and e.get("hashtagName")]
    tags += [c["title"] for c in struct.get("challenges") or []
             if isinstance(c, dict) and c.get("title")]
    mentions = [e["userUniqueId"] for e in extras
                if isinstance(e, dict) and e.get("userUniqueId")]

    stickers = []
    for st in struct.get("stickersOnItem") or []:
        if not isinstance(st, dict):
            continue
        text = " ".join(t for t in st.get("stickerText") or [] if isinstance(t, str))
        stickers.append(ParsedSticker(sticker_type=str(st.get("stickerType") or ""),
                                      text_content=text or None))

    stats = struct.get("stats") or {}
    stats2 = struct.get("statsV2") or {}
    stat = lambda k: _int(stats.get(k)) if stats.get(k) is not None else _int(stats2.get(k))

    handle = author.get("uniqueId")
    caption = struct.get("desc") or None
    return ParsedItem(
        platform_item_id=vid,
        url=f"https://www.tiktok.com/@{handle or '_'}/video/{vid}",
        kind="image" if struct.get("imagePost") else "video",
        caption=caption,
        posted_at=_int(struct.get("createTime")),
        duration_s=_int((struct.get("video") or {}).get("duration")) or None,
        language=struct.get("textLanguage") or None,
        view_count=stat("playCount"),
        like_count=stat("diggCount"),
        comment_count=stat("commentCount"),
        share_count=stat("shareCount"),
        save_count=stat("collectCount"),
        account=account,
        sound=sound,
        tags=list(dict.fromkeys(tags)),
        stickers=stickers,
        mentions=list(dict.fromkeys(mentions)),
        is_pinned=bool(struct.get("isPinnedItem")) if "isPinnedItem" in struct else None,
        is_ad=bool(struct.get("isAd")) if "isAd" in struct else None,
        location=struct.get("locationCreated") or None,
        raw={"id": vid, "createTime": struct.get("createTime"),
             "secUid": author.get("secUid"), "music_id": music.get("id"),
             "diversificationLabels": struct.get("diversificationLabels")},
    )


# ── comments ────────────────────────────────────────────────────────────────

def parse_comment_page(payload: dict[str, Any], parent: str | None = None
                       ) -> tuple[list[ParsedComment], int, bool]:
    """One page of /api/comment/list/ or /api/comment/list/reply/.

    Returns (comments, next_cursor, has_more). An error status inside a 200 is
    raised, not returned empty — an empty page and a refused page must not look
    the same, or a blocked crawl reports zero comments as if that were true.
    """
    if not isinstance(payload, dict):
        raise AdapterError("comment page is not a JSON object")
    code = _int(payload.get("status_code")) or 0
    if code != 0:
        raise AdapterError(f"comment API status {code}: {payload.get('status_msg')!r}")
    out = []
    for c in payload.get("comments") or []:
        if not isinstance(c, dict):
            continue
        text = (c.get("text") or "").strip()
        if not text:
            continue
        user = c.get("user") if isinstance(c.get("user"), dict) else {}
        out.append(ParsedComment(
            platform_comment_id=str(c.get("cid")) if c.get("cid") else None,
            text=text,
            author_handle=user.get("unique_id") or user.get("uid"),
            like_count=_int(c.get("digg_count")),
            reply_count=_int(c.get("reply_comment_total")),
            posted_at=_int(c.get("create_time")),
            parent_platform_comment_id=parent,
        ))
    return out, _int(payload.get("cursor")) or 0, bool(payload.get("has_more"))


def comment_list_url(item_id: str, cursor: int, count: int = 50) -> str:
    return (f"https://www.tiktok.com/api/comment/list/?aid=1988&aweme_id={item_id}"
            f"&count={count}&cursor={cursor}")


def reply_list_url(item_id: str, comment_id: str, cursor: int, count: int = 50) -> str:
    return (f"https://www.tiktok.com/api/comment/list/reply/?aid=1988&item_id={item_id}"
            f"&comment_id={comment_id}&count={count}&cursor={cursor}")


# ── window ──────────────────────────────────────────────────────────────────

def in_window(posted_at: int | None, date_from: int = 0, date_to: int = 0) -> bool:
    """Unknown dates pass: dropping what cannot be dated would bias the sample
    toward items whose timestamp happened to be decodable."""
    if posted_at is None:
        return True
    if date_from and posted_at < date_from:
        return False
    if date_to and posted_at > date_to:
        return False
    return True


# ── Apify dataset shape ─────────────────────────────────────────────────────

def to_apify(item: ParsedItem) -> dict[str, Any]:
    """One item in the Apify TikTok scraper's dataset shape (the fields that
    carry data; presentation-only fields are omitted)."""
    a, s = item.account, item.sound
    from datetime import datetime, timezone
    iso = (datetime.fromtimestamp(item.posted_at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
           if item.posted_at else None)
    return {
        "id": item.platform_item_id,
        "text": item.caption,
        "textLanguage": item.language,
        "createTime": item.posted_at,
        "createTimeISO": iso,
        "webVideoUrl": item.url,
        "locationCreated": item.location,
        "isAd": item.is_ad,
        "isPinned": item.is_pinned,
        "isSlideshow": item.kind == "image",
        "authorMeta": None if a is None else {
            "id": None if a.platform_account_id.startswith("handle:") else a.platform_account_id,
            "name": a.handle, "nickName": a.display_name, "signature": a.bio,
            "bioLink": a.bio_link, "verified": bool(a.verified),
            "fans": a.follower_count, "following": a.following_count, "video": a.item_count,
        },
        "musicMeta": None if s is None else {
            "musicId": None if s.platform_sound_id.startswith("tt:") else s.platform_sound_id,
            "musicName": s.title, "musicAuthor": s.author_name,
            "musicOriginal": bool(s.is_original), "playUrl": s.play_url,
        },
        "videoMeta": {"duration": item.duration_s},
        "diggCount": item.like_count, "shareCount": item.share_count,
        "playCount": item.view_count, "commentCount": item.comment_count,
        "collectCount": item.save_count,
        "hashtags": [{"name": t} for t in item.tags],
        "mentions": [f"@{m}" for m in item.mentions],
        "effectStickers": [{"name": st.text_content} for st in item.stickers if st.text_content],
        "comments": [{"cid": c.platform_comment_id, "text": c.text,
                      "uniqueId": c.author_handle, "diggCount": c.like_count,
                      "createTime": c.posted_at, "replyCommentTotal": c.reply_count,
                      "repliesToId": c.parent_platform_comment_id}
                     for c in item.comments],
        "transcript": item.transcript,
    }


# Field mapping for collect/importers/corpus.py. Dotted paths into an Apify item.
APIFY_IMPORT_MAPPING: dict[str, str] = {
    "platform_item_id": "id",
    "url": "webVideoUrl",
    "caption": "text",
    "posted_at": "createTime",
    "language": "textLanguage",
    "view_count": "playCount",
    "like_count": "diggCount",
    "comment_count": "commentCount",
    "share_count": "shareCount",
    "save_count": "collectCount",
    "account_id": "authorMeta.id",
    "account_handle": "authorMeta.name",
    "account_display_name": "authorMeta.nickName",
    "account_bio": "authorMeta.signature",
    "account_bio_link": "authorMeta.bioLink",
    "account_followers": "authorMeta.fans",
    "account_verified": "authorMeta.verified",
    "account_is_minor": "authorMeta.isUnderAge18",
    "sound_id": "musicMeta.musicId",
    "sound_title": "musicMeta.musicName",
    "sound_author": "musicMeta.musicAuthor",
    "sound_original": "musicMeta.musicOriginal",
    "sound_play_url": "musicMeta.playUrl",
    "tags": "hashtags",
    "tag_name": "name",
    "mentions": "mentions",
    "is_pinned": "isPinned",
    "is_ad": "isAd",
    "location": "locationCreated",
    "comments": "comments",
    "comment_text": "text",
    "comment_author": "uniqueId",
    "comment_id": "cid",
    "comment_likes": "diggCount",
    "comment_posted_at": "createTime",
    "comment_parent": "repliesToId",
    "transcript": "transcript",
}

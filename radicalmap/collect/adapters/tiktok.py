"""TikTok adapter.

Ported from v1's manual_ingest.py / fetch_comments.py / fetch_bios.py, with the
interactive parts removed. v1 required a human: URLs hand-pasted into a text
file, comment extraction done by pasting JavaScript into a logged-in browser
console, 18 video ids hardcoded in the source of batch_comment_extractor.js.
Nothing in it could find a video on its own.

Transport is yt-dlp as a subprocess, as in v1, but driven asynchronously so many
fetches are in flight at once, and it carries no hard dependency on cookies
lifted from the researcher's personal logged-in browser — that made one account
a single point of failure and tied the corpus to one identity.

Two things v1 collected and discarded, both recovered here:

  posted_at, decoded from the numeric item id. v1 stored only scraped_at, so its
  whole 18-video corpus timestamps to a single 79-second window and no time
  series of content was possible.

  The sound. yt-dlp surfaces track/artist/album but no music id, so identity
  falls back to a slug of track+artist — imperfect, and marked as such, with
  audio_sha256 and the acoustic fingerprint as the real identity once audio is
  fetched. On TikTok the sound is the propagation mechanism, so discarding it
  (as v1 did) throws away the strongest tie in the network.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from typing import Any

from ...core.config import settings
from .base import (Adapter, AdapterError, CollectResult, ParsedAccount, ParsedComment,
                   ParsedItem, ParsedSound, PermanentError, Target)

PLATFORM = "tiktok"

# yt-dlp messages that mean "this will never work", not "try again later".
PERMANENT_MARKERS = (
    "video not available", "video is private", "content isn't available",
    "removed for violating", "account is private", "unable to find webpage",
    "this post is unavailable", "video currently unavailable",
    "no video formats found", "account has been banned",
)
RATE_LIMIT_MARKERS = ("429", "too many requests", "rate limit", "slow down")


def slug(text: str, limit: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "unknown"


def posted_at_from_id(item_id: str) -> int | None:
    """TikTok ids are snowflake-shaped: the high 32 bits are unix seconds."""
    if not item_id or not str(item_id).isdigit():
        return None
    ts = int(item_id) >> 32
    return ts if 1451606400 < ts < 2000000000 else None


def item_url(item_id: str, handle: str | None = None) -> str:
    return f"https://www.tiktok.com/@{handle or 'i'}/video/{item_id}"


class TikTokAdapter:
    name = "tiktok"
    platform = PLATFORM

    JOB_TYPES = {"item", "account_timeline", "tag_page", "sound_page", "account"}

    def __init__(self, *, binary: str = "yt-dlp", timeout: float | None = None,
                 want_comments: bool = True, want_transcript: bool = True,
                 max_comments: int = 500, page_limit: int = 200) -> None:
        self.binary = binary
        self.timeout = timeout or settings.request_timeout
        self.want_comments = want_comments
        self.want_transcript = want_transcript
        self.max_comments = max_comments
        self.page_limit = page_limit

    def handles(self, job_type: str) -> bool:
        return job_type in self.JOB_TYPES

    # ── subprocess ───────────────────────────────────────────────────────────

    async def _ytdlp(self, args: list[str]) -> str:
        if not settings.allow_network:
            raise PermanentError(
                "network disabled — set RM_ALLOW_NETWORK=1 to permit live collection")
        cmd = [self.binary, "--ignore-config", "--no-warnings", "--no-progress"]
        if settings.cookies_from_browser:
            cmd += ["--cookies-from-browser", settings.cookies_from_browser]
        cmd += ["--user-agent", settings.user_agent, *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise AdapterError(f"yt-dlp timed out after {self.timeout}s")
        if proc.returncode != 0:
            msg = (err or b"").decode(errors="replace").strip()
            low = msg.lower()
            # The retryable/non-retryable split proven in edmo-attribution-check:
            # a deleted video must not burn five attempts and a backoff schedule.
            if any(m in low for m in RATE_LIMIT_MARKERS):
                raise AdapterError(f"rate limited: {msg[:300]}")
            if any(m in low for m in PERMANENT_MARKERS):
                raise PermanentError(msg[:300])
            raise AdapterError(msg[:300] or f"yt-dlp exited {proc.returncode}")
        return (out or b"").decode(errors="replace")

    # ── parsing (pure, unit-testable without touching the network) ───────────

    def parse_video(self, info: dict[str, Any]) -> ParsedItem:
        vid = str(info.get("id") or "")
        if not vid:
            raise PermanentError("yt-dlp returned no id")

        handle = info.get("uploader") or info.get("channel") or info.get("creator")
        account = None
        if handle or info.get("uploader_id"):
            account = ParsedAccount(
                # Prefer TikTok's stable numeric id; fall back to the handle,
                # marked, so a later rename does not silently fork the actor.
                platform_account_id=str(info.get("channel_id") or info.get("uploader_id")
                                        or f"handle:{handle}"),
                handle=handle,
                display_name=info.get("channel") or info.get("creator"),
                follower_count=info.get("channel_follower_count"),
            )

        sound = None
        track, artist = info.get("track"), (info.get("artist")
                                            or (info.get("artists") or [None])[0])
        music_id = info.get("music_id") or (info.get("music") or {}).get("id")
        if music_id or track:
            sound = ParsedSound(
                platform_sound_id=str(music_id) if music_id else f"tt:{slug(track)}|{slug(artist or '')}",
                title=track, author_name=artist,
                # "original sound" means the uploader made it; those are the
                # seeds of propagation rather than borrowed audio.
                is_original=int(bool(track and "original sound" in track.lower())),
                raw={"track": track, "artist": artist, "album": info.get("album"),
                     "id_source": "platform" if music_id else "derived-from-track"},
            )

        comments = []
        for c in (info.get("comments") or [])[: self.max_comments]:
            text = (c.get("text") or "").strip()
            if not text:
                continue
            parent = c.get("parent")
            comments.append(ParsedComment(
                platform_comment_id=str(c.get("id")) if c.get("id") else None,
                text=text,
                author_handle=c.get("author") or c.get("author_id"),
                like_count=c.get("like_count"),
                posted_at=c.get("timestamp"),
                parent_platform_comment_id=str(parent) if parent and parent != "root" else None,
            ))

        caption = info.get("description") or info.get("title")
        return ParsedItem(
            platform_item_id=vid,
            url=info.get("webpage_url") or item_url(vid, handle),
            title=info.get("title"),
            caption=caption,
            # yt-dlp's timestamp when present, else decoded from the id — which
            # works even for a video whose page no longer loads.
            posted_at=info.get("timestamp") or posted_at_from_id(vid),
            duration_s=info.get("duration"),
            language=info.get("language"),
            view_count=info.get("view_count"),
            like_count=info.get("like_count"),
            comment_count=info.get("comment_count"),
            share_count=info.get("repost_count"),
            save_count=info.get("save_count"),
            account=account,
            sound=sound,
            tags=re.findall(r"#([\wÀ-ɏ]+)", caption or ""),
            comments=comments,
            raw={k: info.get(k) for k in
                 ("id", "timestamp", "duration", "view_count", "like_count",
                  "comment_count", "repost_count", "save_count", "track", "artist",
                  "uploader", "uploader_id", "channel_id", "webpage_url")},
        )

    def parse_listing(self, lines: str, depth: int) -> list[Target]:
        """Flat-playlist output: one JSON object per line."""
        out: list[Target] = []
        for line in lines.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            vid = str(entry.get("id") or "")
            if vid.isdigit():
                out.append(Target("item", item_url(vid, entry.get("uploader")),
                                  depth=depth + 1))
        return out

    def target_for(self, node_type: str, node_id: int, row: dict) -> tuple[str, str] | None:
        if node_type == "account" and row.get("handle"):
            return "account_timeline", f"https://www.tiktok.com/@{row['handle']}"
        if node_type == "item" and row.get("url"):
            return "item", row["url"]
        if node_type == "tag" and row.get("tag"):
            return "tag_page", f"https://www.tiktok.com/tag/{row['tag']}"
        if node_type == "sound":
            sid = str(row.get("platform_sound_id") or "")
            # "tt:" ids are our own surrogate for a sound the platform never
            # named; there is no page behind one.
            if sid and not sid.startswith("tt:"):
                return "sound_page", f"https://www.tiktok.com/music/-{sid}"
        return None

    # ── jobs ─────────────────────────────────────────────────────────────────

    async def run(self, job: dict[str, Any]) -> CollectResult:
        kind, target, depth = job["job_type"], job["target"], job.get("depth", 0)

        if kind == "item":
            args = ["--dump-single-json", "--skip-download"]
            if self.want_comments:
                args += ["--write-comments",
                         "--extractor-args", f"tiktok:max_comments={self.max_comments}"]
            info = json.loads(await self._ytdlp([*args, target]))
            item = self.parse_video(info)
            if self.want_transcript:
                item.transcript = await self._transcript(info)
            return CollectResult(items=[item],
                                 discovered=self._expand(item, depth))

        if kind in ("account_timeline", "tag_page", "sound_page"):
            out = await self._ytdlp([
                "--flat-playlist", "--dump-json", "--skip-download",
                "--playlist-end", str(self.page_limit), target])
            found = self.parse_listing(out, depth)
            return CollectResult(discovered=found,
                                 notes=f"{len(found)} items from {kind}")

        if kind == "account":
            info = json.loads(await self._ytdlp(
                ["--dump-single-json", "--flat-playlist", "--playlist-end", "1", target]))
            handle = info.get("uploader") or info.get("channel") or target.rsplit("@", 1)[-1]
            return CollectResult(accounts=[ParsedAccount(
                platform_account_id=str(info.get("channel_id") or f"handle:{handle}"),
                handle=handle, display_name=info.get("channel"),
                bio=info.get("description"),
                follower_count=info.get("channel_follower_count"),
                item_count=info.get("playlist_count"))])

        raise PermanentError(f"tiktok adapter cannot handle job_type {kind!r}")

    async def _transcript(self, info: dict[str, Any]) -> str | None:
        """Auto-captions come back as a URL, not text. v1's transcripts table has
        0 rows across all 18 videos, so this path was never actually working."""
        subs = (info.get("automatic_captions") or {}) | (info.get("subtitles") or {})
        for lang in ("en", "en-US", "eng"):
            for fmt in subs.get(lang, []):
                url = fmt.get("url")
                if not url:
                    continue
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=20) as client:
                        r = await client.get(url, headers={"User-Agent": settings.user_agent})
                        if r.status_code == 200:
                            return vtt_to_text(r.text)
                except Exception:
                    return None
        return None

    def _expand(self, item: ParsedItem, depth: int) -> list[Target]:
        """Frontier proposals from one fetched video."""
        if depth >= settings.max_depth:
            return []
        out: list[Target] = []
        if settings.expand_accounts and item.account and item.account.handle:
            out.append(Target("account_timeline",
                              f"https://www.tiktok.com/@{item.account.handle}",
                              depth=depth + 1, priority=90))
        if settings.expand_tags:
            for tag in item.tags[:8]:
                out.append(Target("tag_page", f"https://www.tiktok.com/tag/{tag}",
                                  depth=depth + 1, priority=120))
        # A sound page is often the better expansion: unlike a hashtag, a sound
        # cannot be swapped out to evade detection without re-editing the video.
        if settings.expand_sounds and item.sound and not item.sound.platform_sound_id.startswith("tt:"):
            out.append(Target("sound_page",
                              f"https://www.tiktok.com/music/-{item.sound.platform_sound_id}",
                              depth=depth + 1, priority=95))
        return out


VTT_TS = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s+-->")


def vtt_to_text(vtt: str) -> str:
    """Flatten WebVTT to prose, dropping cue timings and the duplicate lines
    auto-captions emit as a rolling window."""
    lines, seen = [], None
    for raw in vtt.splitlines():
        line = raw.strip()
        if (not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))
                or VTT_TS.match(line) or line.isdigit()):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line and line != seen:
            lines.append(line)
            seen = line
    return " ".join(lines)

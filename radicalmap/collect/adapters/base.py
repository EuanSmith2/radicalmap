"""The adapter contract.

One protocol, one result type, so TikTok / Telegram / Rumble differ only in how
they fetch and parse — never in how their output is stored or how the frontier
expands. v1 had no such boundary: each of its 13 scraper files talked to SQLite
directly, in its own way, with its own flagging and its own idea of a schema.

Concurrency rule enforced by this shape: adapters are async and do I/O;
persistence is synchronous and short. A database transaction is never held
across an await, because SQLite writers serialise and an awaited transaction
would deadlock every other worker on the same connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Target:
    """A unit of work the frontier can propose."""
    job_type: str
    target: str
    depth: int = 0
    priority: int = 100


@dataclass
class ParsedAccount:
    platform_account_id: str
    handle: str | None = None
    display_name: str | None = None
    bio: str | None = None
    follower_count: int | None = None
    following_count: int | None = None
    item_count: int | None = None
    verified: int = 0
    # The profile's link-in-bio. Separate from `bio` because it is a structured
    # field on the platform, not text, and it is where off-platform recruitment
    # destinations usually sit.
    bio_link: str | None = None
    # Set only when the platform itself flags the account as under 18. Such
    # accounts are never persisted (see persist.py); the flag exists so the
    # skip can be counted rather than silently happening.
    is_minor: bool = False
    raw: dict[str, Any] | None = None


@dataclass
class ParsedSound:
    platform_sound_id: str
    title: str | None = None
    author_name: str | None = None
    duration_s: float | None = None
    is_original: int = 0
    use_count: int | None = None
    # Where the audio can be fetched. Only collect/audio.py ever downloads it.
    play_url: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ParsedComment:
    platform_comment_id: str | None
    text: str
    author_handle: str | None = None
    like_count: int | None = None
    reply_count: int | None = None
    posted_at: int | None = None
    parent_platform_comment_id: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ParsedSticker:
    sticker_id: str | None = None
    sticker_type: str | None = None
    description: str | None = None
    text_content: str | None = None


@dataclass
class ParsedItem:
    platform_item_id: str
    url: str | None = None
    kind: str = "video"
    title: str | None = None
    caption: str | None = None
    description: str | None = None
    posted_at: int | None = None
    duration_s: float | None = None
    language: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None
    save_count: int | None = None
    account: ParsedAccount | None = None
    sound: ParsedSound | None = None
    tags: list[str] = field(default_factory=list)
    comments: list[ParsedComment] = field(default_factory=list)
    stickers: list[ParsedSticker] = field(default_factory=list)
    transcript: str | None = None
    transcript_language: str = "en"
    # Handles @-mentioned in the caption, as the platform tagged them.
    mentions: list[str] = field(default_factory=list)
    is_pinned: bool | None = None
    is_ad: bool | None = None
    location: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class CollectResult:
    """Everything one job produced: data to store, and targets to queue."""
    items: list[ParsedItem] = field(default_factory=list)
    accounts: list[ParsedAccount] = field(default_factory=list)
    discovered: list[Target] = field(default_factory=list)
    notes: str = ""

    def __bool__(self) -> bool:
        return bool(self.items or self.accounts or self.discovered)


@runtime_checkable
class Adapter(Protocol):
    """Implemented once per platform."""

    name: str
    platform: str

    def handles(self, job_type: str) -> bool: ...

    async def run(self, job: dict[str, Any]) -> CollectResult: ...

    def target_for(self, node_type: str, node_id: int,
                   row: dict[str, Any]) -> tuple[str, str] | None:
        """Turn a graph node into something THIS adapter can fetch.

        Propagation works over an abstract graph, but the thing it hands back to
        the queue has to be a real address for whichever platform is being
        crawled. Building that address in the propagation engine coupled it to
        TikTok's URL shapes and silently produced unfetchable jobs whenever any
        other adapter was running, so it belongs here.
        """
        ...


class AdapterError(RuntimeError):
    """Raised for a failure worth retrying (network, 429, 5xx)."""


class PermanentError(RuntimeError):
    """Raised for a failure that will never succeed — a deleted video, a private
    account. Dead-lettered immediately rather than burning five attempts, the
    same retryable/non-retryable split proven in edmo-attribution-check."""

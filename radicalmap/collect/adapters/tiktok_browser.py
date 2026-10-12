"""TikTok discovery adapter — a real browser, used only to find things.

Why this exists
---------------
The yt-dlp adapter cannot discover by tag, sound or keyword, and the reason is
not a bug anyone can patch around:

  * yt-dlp's TikTok extractor reaches tag and sound listings through TikTok's
    *mobile app* API, which needs app credentials. yt-dlp deliberately ships
    none -- `_KNOWN_APP_INFO` returns [] unless the caller supplies a genuine
    `device_id`, which is why every tag fetch dies with "No working app info is
    available". Supplying a random 19-digit device id clears yt-dlp's own guard
    and then gets an empty body from TikTok, because the id is not a real
    install. Verified 2026-09-21 on 2026.07.04 and 2026.08.19.
  * The web page carries no listing to scrape. `https://www.tiktok.com/tag/x`
    returns 200 with a `__UNIVERSAL_DATA_FOR_REHYDRATION__` blob holding only
    app-context scopes -- no `webapp.challenge-detail`, no `itemList`. The
    videos arrive later over XHR. Verified 2026-09-21.
  * Keyword search has no yt-dlp extractor at all, in any form.

So discovery needs something that executes the page's own JavaScript. That is
the only claim this module makes.

The split that keeps it honest
------------------------------
**This adapter never collects. It only discovers.** It returns `Target`s and no
`ParsedItem`s, ever. A browser page-load costs seconds and a yt-dlp metadata
fetch costs a fraction of one, so the browser is used for the few hundred
listing pages that reveal a network, and `TikTokAdapter` still does the tens of
thousands of item fetches behind it. Letting this adapter parse items too would
be easy and would quietly move the whole corpus onto the slow path.

Consequence worth stating: an item id seen here is a *claim that a video exists*
and nothing more. Its metadata, comments, sound and transcript all come from the
normal collection path, so nothing in the corpus is sourced from DOM scraping.

Interception, not scraping
--------------------------
Listings are read by intercepting the JSON responses the page fetches for
itself, not by reading the DOM. Markup changes weekly; the response shape is a
contract the app depends on. It also means a listing that renders lazily is
captured in full, rather than however much happened to be painted when a
selector was queried.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote

from ...core.config import settings
from .base import AdapterError, CollectResult, PermanentError, Target

PLATFORM = "tiktok"

# The endpoints a listing page fetches for itself. Matched as substrings against
# the response URL, so query strings and host shards do not matter.
LISTING_ENDPOINTS = (
    "/api/challenge/item_list",   # tag pages
    "/api/search/general/full",   # search - mixed results
    "/api/search/item/full",      # search - video tab
    "/api/music/item_list",       # sound pages
    "/api/post/item_list",        # account timelines (fallback)
)

# Emptiness is decided by the listing response, never by page text.
#
# The first version of this module looked for "no results found" in the HTML.
# Every TikTok page embeds a full i18n translation bundle, so that string is
# present on a page showing thousands of videos — the check matched instantly,
# every tag reported "no results" in under a second, and a crawl that discovered
# nothing would have looked like a crawl of empty tags. A listing is empty only
# when its own endpoint replies and the reply carries no items.
_ID_RE = re.compile(r"^\d{6,25}$")

# How long to wait for the page to fetch its own listing before deciding nothing
# is coming. Measured at ~3-6s on a warm connection; 20s leaves headroom without
# stalling a queue behind a page that will never load.
LISTING_WAIT_S = 20.0

# TikTok's anti-bot slider. Verified 2026-09-21: an anonymous keyword search
# renders this and /api/search/item/full/ then returns 200 with an empty body,
# so the block is indistinguishable from "no results" unless it is named.
# Matched against rendered body text, not raw HTML -- the i18n bundle in the
# page source contains every UI string whether or not it is shown, which is what
# made the first emptiness check fire on every page.
CAPTCHA_MARKERS = ("drag the slider", "verify to continue", "security check")


def tag_url(tag: str) -> str:
    return f"https://www.tiktok.com/tag/{quote(tag.lstrip('#'), safe='')}"


def search_url(query: str) -> str:
    return f"https://www.tiktok.com/search/video?q={quote(query, safe='')}"


def item_url(item_id: str, handle: str | None) -> str:
    return f"https://www.tiktok.com/@{handle or '_'}/video/{item_id}"


def harvest(payload: Any) -> list[tuple[str, str | None]]:
    """Pull (item_id, handle) pairs out of any TikTok listing response.

    Three shapes are in play and they are not versioned: `itemList` on tag and
    sound pages, `data[].item` on general search, and occasionally a bare list.
    Rather than branch on which endpoint replied -- which would break silently
    the next time one is renamed -- this walks the structure and takes any dict
    that looks like a video. Pure and unit-testable without a browser.
    """
    found: list[tuple[str, str | None]] = []
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if not isinstance(node, dict):
            return
        vid = node.get("id")
        author = node.get("author")
        # A video node has a numeric id AND an author object. Requiring both
        # keeps out music ids, sticker ids and challenge ids, which are also
        # numeric strings sitting in the same payload.
        if isinstance(vid, str) and _ID_RE.match(vid) and isinstance(author, dict):
            if vid not in seen:
                seen.add(vid)
                handle = author.get("uniqueId") or author.get("nickname")
                found.append((vid, handle if isinstance(handle, str) else None))
        for value in node.values():
            visit(value)

    visit(payload)
    return found


class TikTokBrowserAdapter:
    """Discovery by tag, sound and keyword. Returns targets, never items."""

    name = "tiktok-browser"
    platform = PLATFORM
    JOB_TYPES = {"tag_page", "sound_page", "search"}

    def __init__(self, *, headless: bool = True, max_scrolls: int = 12,
                 scroll_pause: float = 1.2, page_timeout: float = 45.0,
                 max_targets: int = 600) -> None:
        self.headless = headless
        self.max_scrolls = max_scrolls
        self.scroll_pause = scroll_pause
        self.page_timeout = page_timeout
        self.max_targets = max_targets
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()

    def handles(self, job_type: str) -> bool:
        return job_type in self.JOB_TYPES

    def target_for(self, node_type: str, node_id: int,
                   row: dict[str, Any]) -> tuple[str, str] | None:
        """Propagation addresses. Sounds are deliberately absent.

        A sound page is the strongest expansion TikTok offers -- a shared sound
        is the propagation mechanism itself -- but `/api/music/item_list` is not
        reachable without a signed-in session, so proposing sound pages here
        would queue work that always fails. Sound *ties* are unaffected: they
        are built from the track metadata on each collected item.
        """
        if node_type == "tag":
            name = row.get("tag") or row.get("name")
            return ("tag_page", tag_url(name)) if name else None
        return None

    # ── browser lifecycle ────────────────────────────────────────────────────

    async def _ensure_browser(self):
        """Started on first use and shared across jobs.

        A browser launch is ~1s and a context is ~50ms. Discovery jobs are few
        (tens), so one browser with a fresh context per job gives isolation
        without paying the launch cost repeatedly.
        """
        if self._browser is not None:
            return self._browser
        async with self._lock:
            if self._browser is not None:
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover - import-time env issue
                raise PermanentError(
                    "playwright is not installed; discovery needs it "
                    "(pip install -r requirements.txt)") from exc
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"])
            return self._browser

    async def aclose(self) -> None:
        """Explicit teardown. Engine has no adapter lifecycle hook, so the CLI
        calls this in a finally block -- a leaked chromium outlives the process
        and is invisible until something notices the RAM."""
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    # ── the job ──────────────────────────────────────────────────────────────

    async def run(self, job: dict[str, Any]) -> CollectResult:
        if not settings.allow_network:
            raise PermanentError(
                "network disabled — set RM_ALLOW_NETWORK=1 to permit live discovery")

        kind, target, depth = job["job_type"], job["target"], job.get("depth", 0)
        if kind not in self.JOB_TYPES:
            raise PermanentError(f"browser adapter cannot handle job_type {kind!r}")

        url = target if target.startswith("http") else (
            search_url(target) if kind == "search" else tag_url(target))

        browser = await self._ensure_browser()
        context = await browser.new_context(
            user_agent=settings.user_agent,
            locale="en-US",
            viewport={"width": 1280, "height": 900})
        captured: list[tuple[str, str | None]] = []
        endpoints_seen: set[str] = set()
        first_listing = asyncio.Event()
        challenged = False

        async def on_response(response) -> None:
            if not any(e in response.url for e in LISTING_ENDPOINTS):
                return
            endpoints_seen.add(next(e for e in LISTING_ENDPOINTS if e in response.url))
            try:
                payload = await response.json()
            except Exception:
                return  # a listing response that isn't JSON tells us nothing
            captured.extend(harvest(payload))
            first_listing.set()

        context.on("response", on_response)
        page = await context.new_page()
        try:
            try:
                await page.goto(url, timeout=self.page_timeout * 1000,
                                wait_until="domcontentloaded")
            except Exception as exc:
                raise AdapterError(f"navigation failed: {exc}"[:300]) from exc

            # The listing arrives over XHR well after DOM-ready. Waiting on the
            # response rather than a fixed sleep means a fast page is not padded
            # and a slow one is not cut off.
            try:
                await asyncio.wait_for(first_listing.wait(), timeout=LISTING_WAIT_S)
            except asyncio.TimeoutError:
                pass  # resolved below, where endpoints_seen separates the cases

            # Scroll until the listing stops growing. Stopping on "no new items"
            # rather than a fixed count means a small tag costs one scroll and a
            # large one is still followed to the end.
            stagnant = 0
            for _ in range(self.max_scrolls):
                before = len(captured)
                await page.mouse.wheel(0, 4000)
                await asyncio.sleep(self.scroll_pause)
                if len(captured) == before:
                    stagnant += 1
                    if stagnant >= 2:
                        break
                else:
                    stagnant = 0
                if len(captured) >= self.max_targets:
                    break

            if not captured:
                try:
                    shown = (await page.inner_text("body")).lower()
                except Exception:
                    shown = ""
                challenged = any(m in shown for m in CAPTCHA_MARKERS)
        finally:
            await context.close()

        if not captured:
            # Three outcomes look identical from here and must not be collapsed:
            # a CAPTCHA (never succeeds anonymously), a fetch failure (retry),
            # and a genuinely empty listing (done). Returning an empty result
            # for all three is how a blocked crawl reports success.
            if challenged:
                raise PermanentError(
                    f"{kind}: TikTok served an anti-bot slider CAPTCHA for {target!r}. "
                    "Anonymous keyword search is gated behind it; tag pages are not. "
                    "Use --type tag_page, or the official Research API for keyword search.")
            if not endpoints_seen:
                raise AdapterError(
                    f"{kind}: no listing response seen for {target!r} "
                    "(geo-block, network failure, or markup change)")
            return CollectResult(notes=f"{kind}: listing returned 0 items for {target!r}")

        from .tiktok import posted_at_from_id
        from .tiktok_web import in_window
        seen: set[str] = set()
        targets: list[Target] = []
        for vid, handle in captured:
            if vid in seen:
                continue
            seen.add(vid)
            # Same window as the yt-dlp listings: the id dates the video, so an
            # out-of-window one is dropped before it costs a fetch.
            if not in_window(posted_at_from_id(vid), settings.tt_date_from, settings.tt_date_to):
                continue
            targets.append(Target("item", item_url(vid, handle), depth=depth + 1))
            if len(targets) >= self.max_targets:
                break

        return CollectResult(
            discovered=targets,
            notes=(f"{kind}: {len(targets)} items from {target!r} "
                   f"via {','.join(sorted(endpoints_seen))}"))

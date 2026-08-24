"""
Fetch TikTok comments using Playwright + your Chrome cookies.
Intercepts the TikTok comment API response directly from the browser session.

Run: python -m scraper.fetch_comments
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright, Page
from db.database import get_connection, init_db, insert_comment
from scraper.flag_patterns import flag_comment

SAMPLE_FILE = Path(__file__).parent.parent / "data" / "sample_urls.txt"
MAX_COMMENTS = 100   # per video
SCROLL_ROUNDS = 12   # mouse-wheel scroll passes to page the comment API


# ── Cookie loading ───────────────────────────────────────────

def load_tiktok_cookies() -> list[dict]:
    """Read TikTok cookies from Firefox (preferred) or Chrome."""
    import browser_cookie3
    for browser_name, loader in [("firefox", browser_cookie3.firefox), ("chrome", browser_cookie3.chrome)]:
        try:
            jar = loader()
            found = [c for c in jar if "tiktok" in c.domain.lower()]
            if found:
                print(f"  [+] Using {browser_name} ({len(found)} TikTok cookies)")
                return [{
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain if c.domain.startswith(".") else f".{c.domain}",
                    "path": c.path or "/",
                    "secure": bool(c.secure),
                    "sameSite": "None",
                } for c in found]
        except Exception as e:
            print(f"  [~] {browser_name}: {e}")
    return []


# ── DB helpers ───────────────────────────────────────────────

def get_all_videos():
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT id, url, tiktok_id, username FROM videos").fetchall()]
    finally:
        conn.close()


def already_fetched(video_id: int) -> bool:
    conn = get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM comments WHERE video_id=?", (video_id,)).fetchone()[0]
        return count > 0
    finally:
        conn.close()


# ── Playwright scraper ───────────────────────────────────────

def scrape_comments_for_video(page: Page, video: dict) -> int:
    url = video["url"]
    video_id = video["id"]
    username = video["username"]
    tiktok_id = video["tiktok_id"]
    collected: list[dict] = []
    api_done = False

    def handle_response(response):
        if "api/comment/list/" in response.url:
            try:
                body = response.json()
                raw = body.get("comments", []) or []
                for c in raw:
                    collected.append({
                        "author": (c.get("user") or {}).get("unique_id", "") or c.get("author", ""),
                        "text": c.get("text", ""),
                        "like_count": c.get("digg_count", 0) or 0,
                        "timestamp": c.get("create_time"),
                    })
            except Exception:
                pass

    page.on("response", handle_response)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(4)

        # Current desktop layout keeps the comment panel closed until the
        # comment icon is clicked — nothing fires api/comment/list before that.
        try:
            icon = page.locator("[data-e2e='comment-icon']").first
            if icon.count():
                icon.click(timeout=5000)
        except Exception:
            pass

        # Real mouse-wheel scrolling is what makes TikTok page its comment API.
        # (Scripted element.scrollTop returns empty 200s.) Keep scrolling until
        # the collected count stops growing.
        last = -1
        for _ in range(SCROLL_ROUNDS):
            page.mouse.wheel(0, 1600)
            time.sleep(1.4)
            if len(collected) == last and len(collected) > 0:
                break
            last = len(collected)

    except Exception as e:
        print(f"  [!] Page error for @{username}: {e}")
    finally:
        page.remove_listener("response", handle_response)

    # Deduplicate by (author, text)
    seen = set()
    unique = []
    for c in collected:
        key = (c.get("author", ""), c.get("text", ""))
        if key not in seen and c.get("text", "").strip():
            seen.add(key)
            unique.append(c)

    flagged = 0
    for c in unique[:MAX_COMMENTS]:
        flagged_flag, reason, categories, severity = flag_comment(c["text"])
        insert_comment(
            video_id=video_id,
            author=c["author"],
            text=c["text"],
            like_count=c["like_count"],
            flagged=int(flagged_flag),
            flag_reason=reason,
            flag_categories=categories,
            severity=severity,
            timestamp=c.get("timestamp"),
        )
        if flagged_flag:
            flagged += 1

    print(f"  [+] @{username}: {len(unique)} comments, {flagged} flagged")
    return len(unique)


def run():
    init_db()
    videos = get_all_videos()

    if not videos:
        print("No videos in database. Run manual_ingest first.")
        return

    print(f"Loading TikTok cookies from browser...")
    tiktok_cookies = load_tiktok_cookies()
    if not tiktok_cookies:
        print("Aborting — no TikTok cookies found. Log into TikTok in Firefox or Chrome first.")
        return
    print(f"  [+] {len(tiktok_cookies)} TikTok cookies loaded")

    total_comments = 0

    with sync_playwright() as p:
        # Headful only: TikTok's comment API returns empty 200s to headless
        # automation. A real window with AutomationControlled disabled gets served.
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        ctx.add_cookies(tiktok_cookies)
        page = ctx.new_page()

        for video in videos:
            if already_fetched(video["id"]):
                print(f"  [~] @{video['username']} — already has comments, skipping")
                continue
            print(f"  Scraping @{video['username']}...")
            n = scrape_comments_for_video(page, video)
            total_comments += n
            time.sleep(1.5)

        browser.close()

    print(f"\nDone. {total_comments} comments stored across {len(videos)} videos.")


if __name__ == "__main__":
    run()

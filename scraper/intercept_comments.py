"""
Comment extractor via network interception.

Connects to the MCP Chrome for Testing browser (port 9222), injects Firefox
TikTok cookies, navigates to each video page, and intercepts the comment API
response that TikTok's own JS fires automatically (with correct X-Bogus
signature). Falls back to DOM scraping if interception misses.

Run: python -m scraper.intercept_comments
Output: data/comments/comments_{tiktok_id}.json (ready for import_comments.py)
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import browser_cookie3
from playwright.async_api import async_playwright
from db.database import get_connection

COMMENTS_DIR = Path(__file__).parent.parent / "data" / "comments"
COMMENTS_DIR.mkdir(parents=True, exist_ok=True)

WAIT_FOR_COMMENTS_MS = 8000   # time to wait for TikTok to load comments
SCROLL_STEPS = 3               # scroll the comment list this many times
MAX_COMMENTS = 200


def load_ff_cookies() -> list[dict]:
    jar = browser_cookie3.firefox()
    cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain if c.domain.startswith(".") else f".{c.domain}",
            "path": c.path or "/",
            "secure": bool(c.secure),
            "sameSite": "None",
        }
        for c in jar if "tiktok" in c.domain.lower()
    ]
    print(f"  [+] Loaded {len(cookies)} TikTok cookies from Firefox")
    return cookies


def get_all_videos() -> list[dict]:
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, url, tiktok_id, username FROM videos"
        ).fetchall()]
    finally:
        conn.close()


def already_extracted(tiktok_id: str) -> bool:
    p = COMMENTS_DIR / f"comments_{tiktok_id}.json"
    return p.exists() and p.stat().st_size > 10


async def extract_for_video(page, video: dict) -> list[dict]:
    url      = video["url"]
    username = video["username"]
    tiktok_id= video["tiktok_id"]
    collected: list[dict] = []

    # ── Intercept comment API responses ────────────────────────
    async def on_response(response):
        if "api/comment/list" in response.url:
            try:
                body = await response.json()
                for c in body.get("comments", []) or []:
                    collected.append({
                        "video_id": tiktok_id,
                        "author":   c.get("user", {}).get("unique_id", "")
                                    or c.get("user", {}).get("nickname", ""),
                        "text":     c.get("text", ""),
                        "like_count": c.get("digg_count", 0) or 0,
                        "timestamp":  c.get("create_time"),
                    })
                    print(f"    intercepted {len(body.get('comments', []))} comments", end="\r")
            except Exception:
                pass

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)

        # Wait for comment list to appear (TikTok auto-fires the API)
        try:
            await page.wait_for_selector(
                "[data-e2e='comment-list']",
                timeout=WAIT_FOR_COMMENTS_MS,
            )
        except Exception:
            pass

        # Give the API a moment to complete
        await page.wait_for_timeout(2000)

        # Scroll comment panel to trigger more loads
        for _ in range(SCROLL_STEPS):
            await page.evaluate("""
                const el = document.querySelector('[data-e2e="comment-list"]');
                if (el) el.scrollTop += 900;
            """)
            await page.wait_for_timeout(1200)

    except Exception as e:
        print(f"  [!] Page error @{username}: {e}")
    finally:
        page.remove_listener("response", on_response)

    # ── DOM fallback if API interception caught nothing ─────────
    if not collected:
        try:
            dom = await page.evaluate("""
                () => {
                    const items = document.querySelectorAll(
                        '[data-e2e="comment-list"] [data-e2e="comment-level-1"]'
                    );
                    return Array.from(items).slice(0, 200).map(el => ({
                        author: el.querySelector('[data-e2e="comment-username-1"]')
                                   ?.textContent?.trim() || '',
                        text:   el.querySelector('[data-e2e="comment-level-1-text"]')
                                   ?.textContent?.trim() || '',
                        like_count: parseInt(
                            (el.querySelector('[data-e2e="comment-like-count"]')
                               ?.textContent || '0').replace(/[^0-9]/g, '')
                        ) || 0,
                        timestamp: null,
                    }));
                }
            """)
            for c in dom:
                c["video_id"] = tiktok_id
                if c.get("text", "").strip():
                    collected.append(c)
        except Exception as e:
            print(f"  [!] DOM fallback error: {e}")

    # Deduplicate
    seen, unique = set(), []
    for c in collected:
        key = (c.get("author", ""), c.get("text", ""))
        if key not in seen and c.get("text", "").strip():
            seen.add(key)
            unique.append(c)

    return unique[:MAX_COMMENTS]


async def main():
    videos = get_all_videos()
    if not videos:
        print("No videos in DB. Run manual_ingest first.")
        return

    ff_cookies = load_ff_cookies()
    if not ff_cookies:
        print("No TikTok Firefox cookies found — log in first.")
        return

    async with async_playwright() as p:
        # Connect to MCP Chrome for Testing (already running)
        try:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            print("  [+] Connected to Chrome for Testing (port 9222)")
        except Exception as e:
            print(f"  [!] CDP connection failed: {e}")
            print("  [~] Falling back to new headless browser")
            browser = await p.chromium.launch(headless=False)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
            )

        await ctx.add_cookies(ff_cookies)
        page = await ctx.new_page()

        total_saved = 0
        for v in videos:
            tid = v.get("tiktok_id", "")
            if not tid:
                print(f"  [~] @{v['username']}: no tiktok_id, skipping")
                continue
            if already_extracted(tid):
                print(f"  [~] @{v['username']}: already extracted")
                continue

            print(f"  Fetching @{v['username']} ({tid})...")
            comments = await extract_for_video(page, v)

            if comments:
                out = COMMENTS_DIR / f"comments_{tid}.json"
                out.write_text(json.dumps(comments, indent=2))
                print(f"  [+] @{v['username']}: {len(comments)} comments → {out.name}")
                total_saved += len(comments)
            else:
                print(f"  [-] @{v['username']}: 0 comments")

            await page.wait_for_timeout(1500)

        await page.close()
        print(f"\nDone. {total_saved} comments saved across {len(videos)} videos.")
        print(f"Now run: python -m scraper.import_comments")


if __name__ == "__main__":
    asyncio.run(main())

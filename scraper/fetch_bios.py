"""
Scrape TikTok profile bios using the MCP Chrome for Testing browser (CDP port 9222).
Injects Firefox cookies for auth, navigates to each profile page, extracts bio text.

Run: python -m scraper.fetch_bios
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import browser_cookie3
from playwright.async_api import async_playwright
from db.database import get_connection


def load_ff_cookies():
    jar = browser_cookie3.firefox()
    return [
        {
            "name": c.name, "value": c.value,
            "domain": c.domain if c.domain.startswith(".") else f".{c.domain}",
            "path": c.path or "/", "secure": bool(c.secure), "sameSite": "None",
        }
        for c in jar if "tiktok" in c.domain.lower()
    ]


def get_all_usernames():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT DISTINCT username FROM accounts").fetchall()
        return [r["username"] for r in rows]
    finally:
        conn.close()


def save_bio(username: str, bio: str):
    conn = get_connection()
    conn.execute("UPDATE accounts SET bio=? WHERE username=?", (bio, username))
    conn.commit()
    conn.close()


async def fetch_bio(page, username: str) -> str:
    url = f"https://www.tiktok.com/@{username}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2500)
        bio = await page.evaluate("""
            () => {
                // TikTok bio selectors (try multiple)
                const selectors = [
                    '[data-e2e="user-bio"]',
                    'h2[data-e2e="user-subtitle"]',
                    '.tiktok-1qb12g8-H2ShareDesc',
                    '[class*="ShareDesc"]',
                    'meta[name="description"]',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const text = sel.startsWith('meta')
                            ? el.getAttribute('content')
                            : el.textContent;
                        if (text && text.trim().length > 0) return text.trim();
                    }
                }
                // Fallback: look for the bio container via aria
                const desc = document.querySelector('[aria-label*="bio"], [aria-label*="description"]');
                if (desc) return desc.textContent.trim();
                return '';
            }
        """)
        return bio or ""
    except Exception as e:
        print(f"  [!] @{username}: {e}")
        return ""


async def main():
    usernames = get_all_usernames()
    if not usernames:
        print("No accounts in DB.")
        return

    ff_cookies = load_ff_cookies()
    print(f"Loaded {len(ff_cookies)} Firefox TikTok cookies")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            print("Connected to Chrome for Testing (port 9222)")
        except Exception:
            browser = await p.chromium.launch(headless=False)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
            )

        await ctx.add_cookies(ff_cookies)
        page = await ctx.new_page()

        for username in usernames:
            print(f"  @{username}...", end=" ")
            bio = await fetch_bio(page, username)
            save_bio(username, bio)
            print(repr(bio[:80]) if bio else "(empty)")
            await page.wait_for_timeout(1000)

        await page.close()
    print("\nDone. Run python -m scraper.analyse_text to re-analyse bios.")


if __name__ == "__main__":
    asyncio.run(main())

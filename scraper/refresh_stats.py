"""
Re-fetch metadata for all videos and update stats in DB.
Pulls: view_count, like_count, comment_count, share_count (repost_count), save_count.
Does NOT re-process hashtags or comments — stats update only.

Run: python -m scraper.refresh_stats
"""

import subprocess
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import get_connection

# Use the venv's yt-dlp to ensure latest version with save_count support
YTDLP = str(Path(__file__).parent.parent / "venv" / "bin" / "yt-dlp")


def fetch_stats(url: str) -> dict | None:
    for cookie_args in ([], ["--cookies-from-browser", "firefox"]):
        args = [YTDLP, "--dump-json", "--no-download", "--no-playlist"] + cookie_args + [url]
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return None
    return None


def run():
    conn = get_connection()
    videos = conn.execute("SELECT id, url, username FROM videos").fetchall()
    updated = 0

    for v in videos:
        print(f"  @{v['username']}...", end=" ", flush=True)
        meta = fetch_stats(v["url"])
        if not meta:
            print("FAILED")
            continue

        view_count    = meta.get("view_count", 0) or 0
        like_count    = meta.get("like_count", 0) or 0
        comment_count = meta.get("comment_count", 0) or 0
        share_count   = meta.get("repost_count", 0) or meta.get("share_count", 0) or 0
        save_count    = meta.get("save_count", 0) or 0

        conn.execute(
            """UPDATE videos
               SET view_count=?, like_count=?, comment_count=?, share_count=?, save_count=?
               WHERE id=?""",
            (view_count, like_count, comment_count, share_count, save_count, v["id"]),
        )
        conn.commit()
        print(f"views={view_count:,} likes={like_count:,} comments={comment_count} saves={save_count:,} shares={share_count:,}")
        updated += 1

    conn.close()
    print(f"\nDone. {updated}/{len(videos)} videos updated.")


if __name__ == "__main__":
    run()

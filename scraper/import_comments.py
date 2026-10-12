"""
Import comment JSON files extracted via the browser console script.

1. Run browser_comment_extractor.js in Firefox for each TikTok video
2. Move the downloaded JSON files to data/comments/
3. Run: python -m scraper.import_comments

Each file is named comments_{video_id}.json and contains a list of comment objects.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import init_db, insert_comment, get_connection
from scraper.flag_patterns import flag_comment

COMMENTS_DIR = Path(__file__).parent.parent / "data" / "comments"


def get_video_id_by_tiktok_id(tiktok_id: str) -> int | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM videos WHERE tiktok_id=?", (tiktok_id,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def run():
    init_db()
    files = sorted(COMMENTS_DIR.glob("comments_*.json"))
    if not files:
        print(f"No comment files found in {COMMENTS_DIR}")
        print("Run browser_comment_extractor.js in Firefox and move JSON files here.")
        return

    total_imported = 0
    total_flagged = 0

    for f in files:
        tiktok_id = f.stem.replace("comments_", "")
        video_db_id = get_video_id_by_tiktok_id(tiktok_id)
        if not video_db_id:
            print(f"  [!] {f.name}: no matching video in DB (tiktok_id={tiktok_id}) — run manual_ingest first")
            continue

        try:
            comments = json.loads(f.read_text())
        except json.JSONDecodeError:
            print(f"  [!] {f.name}: invalid JSON, skipping")
            continue
        if not isinstance(comments, list):
            print(f"  [!] {f.name}: expected a list of comment objects, skipping")
            continue
        flagged = 0
        for c in comments:
            text = c.get("text", "") or ""
            author = c.get("author", "") or ""
            like_count = c.get("like_count", 0) or 0
            timestamp = c.get("timestamp")
            is_flagged, reason, categories, severity = flag_comment(text)
            insert_comment(
                video_id=video_db_id,
                author=author,
                text=text,
                like_count=like_count,
                flagged=int(is_flagged),
                flag_reason=reason,
                flag_categories=categories,
                severity=severity,
                timestamp=timestamp,
            )
            if is_flagged:
                flagged += 1

        print(f"  [+] {f.name}: {len(comments)} comments, {flagged} flagged")
        total_imported += len(comments)
        total_flagged += flagged

    print(f"\nDone. {total_imported} comments imported, {total_flagged} flagged.")


if __name__ == "__main__":
    run()

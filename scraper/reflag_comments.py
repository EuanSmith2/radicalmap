"""
Re-run the flagging logic over comments already stored in the DB, without
re-scraping. Use after editing COMMENT_FLAG_PATTERNS in fetch_comments.py.

Run: python -m scraper.reflag_comments
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import get_connection
from scraper.flag_patterns import flag_comment


def run():
    conn = get_connection()
    rows = conn.execute("SELECT id, text FROM comments").fetchall()
    changed = flagged = 0
    for r in rows:
        is_flagged, reason, categories, severity = flag_comment(r["text"] or "")
        conn.execute(
            "UPDATE comments SET flagged=?, flag_reason=?, flag_categories=?, severity=? WHERE id=?",
            (int(is_flagged), reason, categories, severity, r["id"]),
        )
        changed += 1
        flagged += int(is_flagged)
    conn.commit()
    conn.close()
    print(f"Re-flagged {changed} comments — {flagged} now flagged.")


if __name__ == "__main__":
    run()

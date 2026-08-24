import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "radicalmap.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    conn.close()
    print(f"Database initialised at {DB_PATH}")


def insert_video(url, tiktok_id, username, caption, view_count, like_count, comment_count, share_count):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO videos (url, tiktok_id, username, caption, view_count, like_count, comment_count, share_count) VALUES (?,?,?,?,?,?,?,?)",
            (url, tiktok_id, username, caption, view_count, like_count, comment_count, share_count)
        )
        conn.commit()
        return conn.execute("SELECT id FROM videos WHERE url=?", (url,)).fetchone()["id"]
    finally:
        conn.close()


def insert_account(username, bio, follower_count, following_count, video_count):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (username, bio, follower_count, following_count, video_count) VALUES (?,?,?,?,?)",
            (username, bio, follower_count, following_count, video_count)
        )
        conn.commit()
    finally:
        conn.close()


def insert_hashtag(tag, category, display_name, definition, origin, severity):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO hashtags (tag, category, display_name, definition, origin, severity) VALUES (?,?,?,?,?,?)",
            (tag, category, display_name, definition, origin, severity)
        )
        conn.commit()
        return conn.execute("SELECT id FROM hashtags WHERE tag=?", (tag,)).fetchone()["id"]
    finally:
        conn.close()


def link_video_hashtag(video_id, hashtag_id):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO video_hashtags (video_id, hashtag_id) VALUES (?,?)",
            (video_id, hashtag_id)
        )
        conn.execute(
            "UPDATE hashtags SET usage_count = usage_count + 1 WHERE id=?",
            (hashtag_id,)
        )
        conn.commit()
    finally:
        conn.close()


def record_cooccurrence(tag_a_id, tag_b_id):
    a, b = min(tag_a_id, tag_b_id), max(tag_a_id, tag_b_id)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO hashtag_cooccurrence (hashtag_a_id, hashtag_b_id, count) VALUES (?,?,1) ON CONFLICT(hashtag_a_id, hashtag_b_id) DO UPDATE SET count=count+1",
            (a, b)
        )
        conn.commit()
    finally:
        conn.close()


def insert_external_link(source_type, source_id, platform, url, context=""):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO external_links (source_type, source_id, platform, url, context) VALUES (?,?,?,?,?)",
            (source_type, source_id, platform, url, context)
        )
        conn.commit()
    finally:
        conn.close()


def insert_comment(video_id, author, text, like_count=0, flagged=0, flag_reason="", flag_categories="", severity="low", timestamp=None):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO comments (video_id, author, text, like_count, flagged, flag_reason, flag_categories, severity, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
            (video_id, author, text, like_count, flagged, flag_reason, flag_categories, severity, timestamp)
        )
        conn.commit()
    finally:
        conn.close()


def insert_transcript(video_id, content, language="en"):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO transcripts (video_id, content, language) VALUES (?,?,?)",
            (video_id, content, language)
        )
        conn.commit()
    finally:
        conn.close()


def insert_sticker(video_id, sticker_id, sticker_type, description, flagged=0):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO stickers (video_id, sticker_id, sticker_type, description, flagged) VALUES (?,?,?,?,?)",
            (video_id, sticker_id, sticker_type, description, flagged)
        )
        conn.commit()
    finally:
        conn.close()


def get_video_comments(video_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM comments WHERE video_id=? ORDER BY like_count DESC",
            (video_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_video_transcript(video_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT content, language FROM transcripts WHERE video_id=?", (video_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_graph_data():
    conn = get_connection()
    try:
        hashtags = conn.execute("SELECT * FROM hashtags WHERE usage_count > 0").fetchall()
        videos = conn.execute("SELECT * FROM videos").fetchall()
        video_hashtags = conn.execute(
            "SELECT vh.video_id, vh.hashtag_id FROM video_hashtags vh"
        ).fetchall()
        cooccurrences = conn.execute(
            "SELECT * FROM hashtag_cooccurrence WHERE count > 0"
        ).fetchall()
        links = conn.execute("SELECT * FROM external_links").fetchall()
        flagged_counts = conn.execute(
            "SELECT video_id, COUNT(*) as cnt FROM comments WHERE flagged=1 GROUP BY video_id"
        ).fetchall()
        comment_total = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        transcript_ids = conn.execute("SELECT video_id FROM transcripts").fetchall()
        flagged_map = {r["video_id"]: r["cnt"] for r in flagged_counts}
        transcript_set = {r["video_id"] for r in transcript_ids}
        video_dicts = []
        for v in videos:
            d = dict(v)
            d["flagged_comment_count"] = flagged_map.get(d["id"], 0)
            d["has_transcript"] = d["id"] in transcript_set
            video_dicts.append(d)
        return {
            "hashtags": [dict(h) for h in hashtags],
            "videos": video_dicts,
            "video_hashtags": [dict(vh) for vh in video_hashtags],
            "cooccurrences": [dict(c) for c in cooccurrences],
            "external_links": [dict(l) for l in links],
            "comment_total": comment_total,
        }
    finally:
        conn.close()

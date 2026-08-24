"""
Manual ingest — paste TikTok URLs into data/sample_urls.txt (one per line).
Run: python -m scraper.manual_ingest
Pulls metadata via yt-dlp, parses hashtags, comments, transcripts, loads into DB.
"""

import subprocess
import json
import re
import sys
import glob
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.database import (
    init_db, insert_video, insert_account, insert_hashtag,
    link_video_hashtag, record_cooccurrence, insert_external_link,
    insert_comment, insert_transcript, insert_sticker,
)
from analysis.dictionary import load_dictionary
from scraper.flag_patterns import flag_comment

SAMPLE_FILE = Path(__file__).parent.parent / "data" / "sample_urls.txt"
TELEGRAM_RE = re.compile(r"t\.me/\S+", re.IGNORECASE)
HASHTAG_RE = re.compile(r"#(\w+)")

FETCH_COMMENTS = True
FETCH_TRANSCRIPTS = True


def fetch_metadata(url: str) -> dict | None:
    base = ["yt-dlp", "--dump-json", "--no-download", "--no-playlist"]
    if FETCH_COMMENTS:
        base.append("--write-comments")

    for cookie_args in (["--cookies-from-browser", "chrome"], []):
        args = base + cookie_args + [url]
        result = subprocess.run(args, capture_output=True, text=True, timeout=90)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                print(f"  [!] JSON parse error for {url}")
                return None
        if cookie_args:
            print(f"  [~] Cookie fetch failed, retrying without auth...")

    print(f"  [!] yt-dlp failed: {result.stderr[:120]}")
    return None


def fetch_transcript(url: str, tiktok_id: str) -> str | None:
    if not FETCH_TRANSCRIPTS:
        return None
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run([
            "yt-dlp",
            "--write-auto-subs", "--sub-langs", "en",
            "--sub-format", "vtt",
            "--skip-download", "--no-playlist",
            "-o", f"{tmpdir}/sub",
            url
        ], capture_output=True, text=True, timeout=30)
        vtt_files = glob.glob(f"{tmpdir}/*.vtt")
        if not vtt_files:
            return None
        vtt_text = Path(vtt_files[0]).read_text(errors="replace")
        lines = []
        for line in vtt_text.splitlines():
            if line.startswith("WEBVTT") or line.startswith("NOTE") or "-->" in line or not line.strip():
                continue
            clean = re.sub(r"<[^>]+>", "", line).strip()
            if clean:
                lines.append(clean)
        deduped = []
        for line in lines:
            if not deduped or deduped[-1] != line:
                deduped.append(line)
        return " ".join(deduped) if deduped else None


def extract_hashtags(text: str) -> list[str]:
    return [h.lower() for h in HASHTAG_RE.findall(text or "")]


def process_url(url: str, known_tags: dict):
    print(f"  Fetching: {url}")
    meta = fetch_metadata(url)
    if not meta:
        return

    caption = meta.get("description", "") or meta.get("title", "")
    url_match = re.search(r"tiktok\.com/@([^/?]+)", url)
    username = url_match.group(1) if url_match else (meta.get("uploader", "") or meta.get("uploader_id", ""))
    bio = meta.get("uploader_description", "")
    tiktok_id = meta.get("id", "")

    video_id = insert_video(
        url=url,
        tiktok_id=tiktok_id,
        username=username,
        caption=caption,
        view_count=meta.get("view_count", 0),
        like_count=meta.get("like_count", 0),
        comment_count=meta.get("comment_count", 0),
        share_count=meta.get("repost_count", 0),
    )
    insert_account(username, bio, meta.get("uploader_follower_count", 0), 0, 0)

    # Hashtags
    tags_in_video = extract_hashtags(caption)
    tag_ids = []
    for tag in tags_in_video:
        if tag in known_tags:
            info = known_tags[tag]
            htag_id = insert_hashtag(
                tag=tag,
                category=info["category"],
                display_name=info["display_name"],
                definition=info["definition"],
                origin=info["origin"],
                severity=info["severity"],
            )
        else:
            htag_id = insert_hashtag(
                tag=tag, category="uncategorised",
                display_name=f"#{tag}", definition="", origin="", severity="low"
            )
        link_video_hashtag(video_id, htag_id)
        tag_ids.append(htag_id)

    for i, a in enumerate(tag_ids):
        for b in tag_ids[i + 1:]:
            record_cooccurrence(a, b)

    # External links
    for field, text in [("bio", bio), ("caption", caption)]:
        for match in TELEGRAM_RE.findall(text or ""):
            insert_external_link("video" if field == "caption" else "account", video_id, "telegram", match, field)
            print(f"  [+] Telegram link in {field}: {match}")

    # Comments
    raw_comments = meta.get("comments", []) or []
    flagged_count = 0
    for c in raw_comments[:200]:
        text = c.get("text", "") or ""
        author = c.get("author", "") or c.get("author_id", "")
        like_count = c.get("like_count", 0) or 0
        timestamp = c.get("timestamp")
        flagged, reason, categories, severity = flag_comment(text)
        insert_comment(video_id, author, text, like_count, int(flagged), reason, categories, severity, timestamp)
        if flagged:
            flagged_count += 1
    if raw_comments:
        print(f"  [+] {len(raw_comments)} comments ingested, {flagged_count} flagged")

    # Stickers
    sticker_list = meta.get("stickers", []) or []
    STICKER_FLAG_KEYWORDS = {"nazi", "ss", "reich", "swastika", "1488", "heil", "iron cross", "black sun", "sonnenrad"}
    for s in sticker_list:
        sid = str(s.get("id", ""))
        stype = s.get("type", "")
        desc = s.get("description", "") or s.get("name", "") or ""
        flagged = int(any(kw in desc.lower() for kw in STICKER_FLAG_KEYWORDS))
        insert_sticker(video_id, sid, stype, desc, flagged)
        if flagged:
            print(f"  [!] Flagged sticker: {desc}")

    # Transcript
    transcript = fetch_transcript(url, tiktok_id)
    if transcript:
        insert_transcript(video_id, transcript)
        print(f"  [+] Transcript: {len(transcript.split())} words")

    print(f"  [+] Done: @{username} | {len(tags_in_video)} hashtags | {len(raw_comments)} comments")


def run():
    if not SAMPLE_FILE.exists():
        SAMPLE_FILE.write_text("# Paste one TikTok URL per line\n")
        print(f"Created {SAMPLE_FILE} — add your URLs and run again.")
        return

    urls = [
        line.strip() for line in SAMPLE_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not urls:
        print("No URLs found in sample_urls.txt")
        return

    print(f"Initialising database...")
    init_db()

    print(f"Loading dictionary...")
    known_tags = load_dictionary()

    print(f"\nProcessing {len(urls)} URLs...\n")
    for url in urls:
        process_url(url, known_tags)

    print("\nIngest complete.")


if __name__ == "__main__":
    run()

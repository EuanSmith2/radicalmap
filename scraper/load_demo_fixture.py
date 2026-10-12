"""Load a small synthetic fixture into the local SQLite DB for offline demos."""

import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.dictionary import load_dictionary
from db.database import (
    DB_PATH,
    init_db,
    insert_account,
    insert_comment,
    insert_hashtag,
    insert_transcript,
    insert_video,
    link_video_hashtag,
    record_cooccurrence,
)
from scraper.flag_patterns import flag_comment

FIXTURE_PATH = Path(__file__).parent.parent / "data" / "demo_fixture.json"


def _load_fixture() -> dict:
    data = json.loads(FIXTURE_PATH.read_text())
    if not isinstance(data.get("videos"), list):
        raise ValueError("Fixture must include a 'videos' list")
    return data


def _reset_db() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def run() -> None:
    fixture = _load_fixture()
    known_tags = load_dictionary()
    _reset_db()

    video_count = comment_count = flagged_count = 0

    for video in fixture["videos"]:
        url = video.get("url", "")
        tiktok_id = video.get("tiktok_id", "")
        username = video.get("username", "")
        caption = video.get("caption", "")
        if not url or not username:
            raise ValueError("Each fixture video needs at least 'url' and 'username'")

        video_id = insert_video(
            url=url,
            tiktok_id=tiktok_id,
            username=username,
            caption=caption,
            view_count=int(video.get("view_count", 0) or 0),
            like_count=int(video.get("like_count", 0) or 0),
            comment_count=int(video.get("comment_count", 0) or 0),
            share_count=int(video.get("share_count", 0) or 0),
        )
        insert_account(
            username=username,
            bio=video.get("bio", "") or "",
            follower_count=int(video.get("follower_count", 0) or 0),
            following_count=int(video.get("following_count", 0) or 0),
            video_count=int(video.get("video_count", 0) or 0),
        )

        hashtag_ids = []
        for tag in video.get("hashtags", []):
            clean_tag = str(tag).strip().lstrip("#").lower()
            if not clean_tag:
                continue
            if clean_tag in known_tags:
                info = known_tags[clean_tag]
                hashtag_id = insert_hashtag(
                    tag=clean_tag,
                    category=info["category"],
                    display_name=info["display_name"],
                    definition=info["definition"],
                    origin=info["origin"],
                    severity=info["severity"],
                )
            else:
                hashtag_id = insert_hashtag(
                    tag=clean_tag,
                    category="uncategorised",
                    display_name=f"#{clean_tag}",
                    definition="Synthetic demo hashtag",
                    origin="synthetic_fixture",
                    severity="low",
                )
            link_video_hashtag(video_id, hashtag_id)
            hashtag_ids.append(hashtag_id)

        for a, b in combinations(sorted(set(hashtag_ids)), 2):
            record_cooccurrence(a, b)

        for c in video.get("comments", []):
            text = str(c.get("text", "") or "")
            author = str(c.get("author", "") or "")
            like_count = int(c.get("like_count", 0) or 0)
            flagged, reason, categories, severity = flag_comment(text)
            insert_comment(
                video_id=video_id,
                author=author,
                text=text,
                like_count=like_count,
                flagged=int(flagged),
                flag_reason=reason,
                flag_categories=categories,
                severity=severity,
                timestamp=c.get("timestamp"),
            )
            comment_count += 1
            if flagged:
                flagged_count += 1

        transcript = video.get("transcript")
        if transcript:
            insert_transcript(video_id, str(transcript), language="en")

        video_count += 1

    print(
        f"Loaded synthetic fixture: {video_count} videos, {comment_count} comments, {flagged_count} flagged comments"
    )


if __name__ == "__main__":
    run()

"""
Run Fable (claude-fable-5) analysis across all videos in the DB.
Each video is sent as a separate request to maximise analysis depth.
Results saved to data/fable_results.json.

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python -m analysis.run_fable

Requirements:
  pip install anthropic
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.database import get_connection

SYSTEM_PROMPT_PATH = Path(__file__).parent / "fable-analyst-prompt.txt"
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "fable_results.json"
MODEL = "claude-fable-5"


def build_video_payload(v: dict, accounts: dict, all_hashtags: dict, comments: list) -> dict:
    bio = accounts.get(v["username"], {}).get("bio", "") or ""
    hashtag_names = [h.strip() for h in (v.get("hashtags") or "").split(",") if h.strip()]

    known_dict = {
        tag: {
            "category": all_hashtags.get(tag, {}).get("category", ""),
            "severity":  all_hashtags.get(tag, {}).get("severity", ""),
            "definition": all_hashtags.get(tag, {}).get("definition", ""),
        }
        for tag in hashtag_names if tag in all_hashtags
    }

    return {
        "video_url": v.get("url", ""),
        "tiktok_id": v.get("tiktok_id", ""),
        "username": v.get("username", ""),
        "bio": bio,
        "caption": v.get("caption", "") or "",
        "hashtags": hashtag_names,
        "view_count": v.get("view_count", 0) or 0,
        "like_count": v.get("like_count", 0) or 0,
        "comment_count": v.get("comment_count", 0) or 0,
        "share_count": v.get("share_count", 0) or 0,
        "save_count": v.get("save_count", 0) or 0,
        "comments": [
            {
                "author": c["author"],
                "text": c["text"],
                "like_count": c.get("like_count", 0) or 0,
            }
            for c in comments
        ],
        "transcript": v.get("transcript") or None,
        "stickers": [],
        "known_dictionary": known_dict,
        "cluster_context": None,
    }


def run():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("  export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic package not installed.")
        print("  pip install anthropic")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = SYSTEM_PROMPT_PATH.read_text()

    conn = get_connection()

    # Load data
    videos = conn.execute("""
        SELECT v.*,
               GROUP_CONCAT(h.tag, ',') as hashtags
        FROM videos v
        LEFT JOIN video_hashtags vh ON vh.video_id = v.id
        LEFT JOIN hashtags h ON h.id = vh.hashtag_id
        GROUP BY v.id
        ORDER BY v.id
    """).fetchall()

    accounts = {
        r["username"]: dict(r)
        for r in conn.execute("SELECT * FROM accounts")
    }

    all_hashtags = {
        r["tag"]: dict(r)
        for r in conn.execute("SELECT * FROM hashtags")
    }

    all_comments = {}
    for c in conn.execute("SELECT * FROM comments ORDER BY video_id, id"):
        all_comments.setdefault(c["video_id"], []).append(dict(c))

    conn.close()

    results = {}
    existing = {}
    if OUTPUT_PATH.exists():
        existing = json.loads(OUTPUT_PATH.read_text())

    total = len(videos)
    for i, v in enumerate(videos):
        vid_key = f"v_{v['id']}_{v['username']}"
        if vid_key in existing:
            print(f"[{i+1}/{total}] @{v['username']} — cached, skipping")
            results[vid_key] = existing[vid_key]
            continue

        payload = build_video_payload(
            dict(v),
            accounts,
            all_hashtags,
            all_comments.get(v["id"], [])
        )

        print(f"[{i+1}/{total}] @{v['username']} — sending to Fable...", end=" ", flush=True)

        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
                ],
            )
            raw = msg.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = "\n".join(raw.split("\n")[:-1])

            analysis = json.loads(raw)
            results[vid_key] = {"payload": payload, "analysis": analysis}
            print(f"OK (confidence={analysis.get('self_critique', {}).get('confidence_score', '?')})")
        except json.JSONDecodeError as e:
            print(f"JSON ERROR: {e}")
            results[vid_key] = {"payload": payload, "raw": raw, "error": str(e)}
        except Exception as e:
            print(f"ERROR: {e}")
            results[vid_key] = {"payload": payload, "error": str(e)}

        # Save after each video (crash recovery)
        OUTPUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))

        if i < total - 1:
            time.sleep(2)  # Rate limit buffer

    print(f"\nDone. {len(results)} videos analysed → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()

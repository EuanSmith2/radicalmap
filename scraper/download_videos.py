"""
Downloads videos from sample_urls.txt into data/videos/.
Filenames match TikTok video ID for easy lookup.
Run: python -m scraper.download_videos
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SAMPLE_FILE = Path(__file__).parent.parent / "data" / "sample_urls.txt"
VIDEO_DIR = Path(__file__).parent.parent / "data" / "videos"


def download(url: str):
    result = subprocess.run([
        "yt-dlp",
        "--cookies-from-browser", "chrome",
        "-o", str(VIDEO_DIR / "%(id)s.%(ext)s"),
        "--no-playlist",
        "--merge-output-format", "mp4",
        url
    ], capture_output=True, text=True)

    if result.returncode == 0:
        print(f"  [+] Downloaded: {url.split('/video/')[1].split('?')[0]}")
    else:
        # Try without cookies
        result2 = subprocess.run([
            "yt-dlp",
            "-o", str(VIDEO_DIR / "%(id)s.%(ext)s"),
            "--no-playlist",
            "--merge-output-format", "mp4",
            url
        ], capture_output=True, text=True)
        if result2.returncode == 0:
            print(f"  [+] Downloaded (no auth): {url.split('/video/')[1].split('?')[0]}")
        else:
            print(f"  [!] Failed: {result2.stderr[-200:]}")


def run():
    urls = [
        line.strip() for line in SAMPLE_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    print(f"Downloading {len(urls)} videos to {VIDEO_DIR}...\n")
    for url in urls:
        download(url)
    print("\nDone.")


if __name__ == "__main__":
    run()

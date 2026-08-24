"""FastAPI app — serves the frontend and graph data API."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from analysis.graph_builder import build_graph
from db.database import get_video_comments, get_video_transcript

app = FastAPI(title="Radical Network Map")
STATIC = Path(__file__).parent / "static"
VIDEO_DIR = Path(__file__).parent.parent / "data" / "videos"

app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/videos", StaticFiles(directory=VIDEO_DIR), name="videos")


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/graph")
def graph():
    data = build_graph()
    for node in data["nodes"]:
        if node["type"] == "video":
            tiktok_id = node.get("tiktok_id", "")
            mp4 = VIDEO_DIR / f"{tiktok_id}.mp4"
            webm = VIDEO_DIR / f"{tiktok_id}.webm"
            if mp4.exists():
                node["local_video"] = f"/videos/{tiktok_id}.mp4"
            elif webm.exists():
                node["local_video"] = f"/videos/{tiktok_id}.webm"
    return data


@app.get("/api/video/{db_id}/comments")
def video_comments(db_id: int):
    return {"comments": get_video_comments(db_id)}


@app.get("/api/video/{db_id}/transcript")
def video_transcript(db_id: int):
    result = get_video_transcript(db_id)
    return {"transcript": result}

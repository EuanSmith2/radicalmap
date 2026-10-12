from fastapi.testclient import TestClient

from frontend import app as app_module


def test_api_endpoints(monkeypatch):
    monkeypatch.setattr(app_module, "build_graph", lambda: {"nodes": [], "edges": [], "categories": {}, "stats": {}})
    monkeypatch.setattr(app_module, "get_video_comments", lambda db_id: [{"video_id": db_id, "text": "ok"}])
    monkeypatch.setattr(app_module, "get_video_transcript", lambda db_id: {"content": "hello", "language": "en"})

    client = TestClient(app_module.app)

    graph = client.get("/api/graph")
    comments = client.get("/api/video/1/comments")
    transcript = client.get("/api/video/1/transcript")

    assert graph.status_code == 200
    assert comments.status_code == 200
    assert transcript.status_code == 200
    assert comments.json()["comments"][0]["video_id"] == 1

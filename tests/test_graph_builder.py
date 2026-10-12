from analysis import graph_builder


def test_build_graph_handles_missing_optional_fields(monkeypatch):
    raw = {
        "hashtags": [
            {
                "id": 1,
                "tag": "demo",
                "display_name": "#demo",
                "category": "uncategorised",
                "definition": "d",
                "origin": "o",
                "severity": "low",
                "usage_count": 1,
            }
        ],
        "videos": [
            {
                "id": 1,
                "username": "demo_user",
                "caption": None,
                "url": "https://example.invalid",
            }
        ],
        "video_hashtags": [{"video_id": 1, "hashtag_id": 1}],
        "cooccurrences": [],
        "external_links": [],
        "comment_total": 0,
    }
    monkeypatch.setattr(graph_builder, "get_graph_data", lambda: raw)
    monkeypatch.setattr(graph_builder, "load_categories", lambda: {"uncategorised": "U"})

    out = graph_builder.build_graph()

    assert out["stats"]["total_videos"] == 1
    assert any(n["type"] == "video" for n in out["nodes"])
    assert any(e["type"] == "uses_hashtag" for e in out["edges"])

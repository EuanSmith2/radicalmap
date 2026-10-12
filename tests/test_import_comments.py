import json

from scraper import import_comments


def test_import_comments_skips_malformed_files(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "comments_demo_bad.json"
    bad.write_text("{not-json")

    monkeypatch.setattr(import_comments, "COMMENTS_DIR", tmp_path)
    monkeypatch.setattr(import_comments, "init_db", lambda: None)
    monkeypatch.setattr(import_comments, "get_video_id_by_tiktok_id", lambda tid: 1)
    monkeypatch.setattr(import_comments, "insert_comment", lambda **kwargs: None)

    import_comments.run()

    out = capsys.readouterr().out
    assert "invalid JSON" in out


def test_import_comments_skips_non_list_payload(tmp_path, monkeypatch, capsys):
    obj = tmp_path / "comments_demo_obj.json"
    obj.write_text(json.dumps({"text": "wrong-shape"}))

    monkeypatch.setattr(import_comments, "COMMENTS_DIR", tmp_path)
    monkeypatch.setattr(import_comments, "init_db", lambda: None)
    monkeypatch.setattr(import_comments, "get_video_id_by_tiktok_id", lambda tid: 1)
    monkeypatch.setattr(import_comments, "insert_comment", lambda **kwargs: None)

    import_comments.run()

    out = capsys.readouterr().out
    assert "expected a list" in out

import sqlite3

from db import database
from scraper import load_demo_fixture


def test_demo_loader_populates_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(load_demo_fixture, "DB_PATH", db_path)

    load_demo_fixture.run()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM videos")
    assert cur.fetchone()[0] == 2
    cur.execute("SELECT COUNT(*) FROM comments")
    assert cur.fetchone()[0] == 3
    cur.execute("SELECT COUNT(*) FROM hashtags")
    assert cur.fetchone()[0] >= 2
    conn.close()

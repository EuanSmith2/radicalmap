"""Rebuild the derived metrics tables.

Everything here is a set-based INSERT..SELECT: one statement per table, no
Python loop over rows. The whole point of moving these out of ingest is that
they can be recomputed cheaply and repeatedly, so they must not be expensive.
"""

from __future__ import annotations

import time

from ..db import engine


def _run(kind: str, sql_statements: list[str], table: str) -> int:
    started = time.perf_counter()
    with engine.transaction() as conn:
        rid = conn.execute(
            "INSERT INTO metrics_runs (kind) VALUES (?) RETURNING id", (kind,)).fetchone()[0]
        conn.execute(f"DELETE FROM {table}")
        for sql in sql_statements:
            conn.execute(sql)
        n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        conn.execute(
            "UPDATE metrics_runs SET finished_at=unixepoch(), row_count=?, notes=? WHERE id=?",
            (n, f"{time.perf_counter()-started:.2f}s", rid))
    return int(n)


def rebuild_tag_cooccurrence(min_count: int = 1) -> int:
    return _run("tag_cooccurrence", [f"""
        INSERT INTO tag_cooccurrence (tag_a_id, tag_b_id, count, jaccard)
        WITH pair AS (
            SELECT a.tag_id AS a, b.tag_id AS b, count(*) AS n
              FROM item_tags a
              JOIN item_tags b ON a.item_id = b.item_id AND a.tag_id < b.tag_id
             GROUP BY a.tag_id, b.tag_id
            HAVING count(*) >= {int(min_count)}
        ), freq AS (
            SELECT tag_id, count(*) AS n FROM item_tags GROUP BY tag_id
        )
        SELECT p.a, p.b, p.n,
               CAST(p.n AS REAL) / (fa.n + fb.n - p.n)
          FROM pair p JOIN freq fa ON fa.tag_id = p.a JOIN freq fb ON fb.tag_id = p.b
    """], "tag_cooccurrence")


def rebuild_tag_stats() -> int:
    return _run("tag_stats", ["""
        INSERT INTO tag_stats (tag_id, item_count, account_count, first_used, last_used, total_views)
        SELECT it.tag_id, count(DISTINCT it.item_id), count(DISTINCT i.account_id),
               min(i.posted_at), max(i.posted_at), sum(i.view_count)
          FROM item_tags it JOIN items i ON i.id = it.item_id
         GROUP BY it.tag_id
    """], "tag_stats")


def rebuild_sound_stats() -> int:
    return _run("sound_stats", ["""
        INSERT INTO sound_stats (sound_id, item_count, account_count, first_used, last_used, total_views)
        SELECT isx.sound_id, count(DISTINCT isx.item_id), count(DISTINCT i.account_id),
               min(i.posted_at), max(i.posted_at), sum(i.view_count)
          FROM item_sounds isx JOIN items i ON i.id = isx.item_id
         GROUP BY isx.sound_id
    """], "sound_stats")


def rebuild_account_stats() -> int:
    return _run("account_stats", ["""
        INSERT INTO account_stats (account_id, item_count, total_views, first_posted,
                                   last_posted, distinct_tags, distinct_sounds, comment_count)
        SELECT i.account_id, count(DISTINCT i.id), sum(i.view_count),
               min(i.posted_at), max(i.posted_at),
               (SELECT count(DISTINCT it.tag_id) FROM item_tags it
                 JOIN items i2 ON i2.id = it.item_id WHERE i2.account_id = i.account_id),
               (SELECT count(DISTINCT s.sound_id) FROM item_sounds s
                 JOIN items i3 ON i3.id = s.item_id WHERE i3.account_id = i.account_id),
               (SELECT count(*) FROM comments c
                 JOIN items i4 ON i4.id = c.item_id WHERE i4.account_id = i.account_id)
          FROM items i WHERE i.account_id IS NOT NULL
         GROUP BY i.account_id
    """], "account_stats")


def rebuild_daily_volume() -> int:
    return _run("daily_volume", ["""
        INSERT INTO daily_volume (day, platform_id, item_count, account_count, total_views)
        SELECT posted_at / 86400, platform_id, count(*), count(DISTINCT account_id), sum(view_count)
          FROM items WHERE posted_at IS NOT NULL
         GROUP BY posted_at / 86400, platform_id
    """], "daily_volume")


def rebuild_all(verbose: bool = True) -> dict[str, int]:
    out = {}
    for name, fn in (("tag_cooccurrence", rebuild_tag_cooccurrence),
                     ("tag_stats", rebuild_tag_stats),
                     ("sound_stats", rebuild_sound_stats),
                     ("account_stats", rebuild_account_stats),
                     ("daily_volume", rebuild_daily_volume)):
        t = time.perf_counter()
        out[name] = fn()
        if verbose:
            print(f"  {name:<20} {out[name]:>9,} rows  {time.perf_counter()-t:6.2f}s")
    engine.execute("ANALYZE")
    return out

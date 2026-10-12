"""Materialising the network.

The graph has been implicit until now — spread across item_tags, item_sounds,
comments and links. That is fine for storage and useless for traversal, which
needs to ask "what is one hop from here, along which kind of tie, and how
strong is it".

Every builder normalises weight to roughly [0,1], because a propagation score
that mixes "shares a sound" with "shares a hashtag" has to put them on one
scale. Two weighting decisions carry most of the signal:

  Hashtag ties are IDF-weighted. Two accounts both using #fyp share nothing;
  two accounts both using a rare coded tag share a great deal. Raw co-occurrence
  counts get this exactly backwards, because the common tags dominate.

  Sound ties are measured across ACCOUNTS, not uses. A sound used 500 times by
  one account is that account's habit; the same sound used 50 times across 50
  accounts is a movement moving through the network.
"""

from __future__ import annotations

import math
import time

from ..db import engine

EDGE_TYPES = ("authored", "uses_tag", "uses_sound", "commented_on", "co_tag",
              "shares_sound", "shares_tag", "same_audio", "links_to", "alt_of")


def _replace(edge_type: str, sql: str, params: tuple = ()) -> int:
    with engine.transaction():
        engine.execute("DELETE FROM edges WHERE edge_type = ?", (edge_type,))
        engine.execute(sql, params)
        return int(engine.scalar("SELECT count(*) FROM edges WHERE edge_type = ?",
                                 (edge_type,)) or 0)


def build_authored() -> int:
    return _replace("authored", """
        INSERT INTO edges (src_type, src_id, dst_type, dst_id, edge_type, weight, observed_count)
        SELECT 'account', account_id, 'item', id, 'authored', 1.0, 1
          FROM items WHERE account_id IS NOT NULL""")


def build_uses_tag() -> int:
    """IDF-weighted: a tag on everything carries no information."""
    return _replace("uses_tag", """
        INSERT INTO edges (src_type, src_id, dst_type, dst_id, edge_type, weight, observed_count)
        WITH n AS (SELECT CAST(count(*) AS REAL) AS total FROM items),
             df AS (SELECT tag_id, count(DISTINCT item_id) AS c FROM item_tags GROUP BY tag_id)
        SELECT 'item', it.item_id, 'tag', it.tag_id, 'uses_tag',
               min(1.0, ln(n.total / df.c) / ln(max(n.total, 2.718))), 1
          FROM item_tags it JOIN df ON df.tag_id = it.tag_id CROSS JOIN n
         WHERE df.c > 0""")


def build_uses_sound() -> int:
    return _replace("uses_sound", """
        INSERT INTO edges (src_type, src_id, dst_type, dst_id, edge_type, weight, observed_count)
        SELECT 'item', item_id, 'sound', sound_id, 'uses_sound', 1.0, 1 FROM item_sounds""")


def build_commented_on() -> int:
    """Commenter -> item. Only where the commenter resolves to a known account;
    a bare pseudonymous hash is an engagement, not a traversable node."""
    return _replace("commented_on", """
        INSERT INTO edges (src_type, src_id, dst_type, dst_id, edge_type, weight, observed_count)
        SELECT 'account', c.author_account_id, 'item', c.item_id, 'commented_on',
               min(1.0, count(*) / 5.0), count(*)
          FROM comments c WHERE c.author_account_id IS NOT NULL
         GROUP BY c.author_account_id, c.item_id""")


def build_co_tag() -> int:
    """Hashtag co-occurrence, taken from the precomputed Jaccard rather than raw
    counts — see analyse/metrics.py for why."""
    return _replace("co_tag", """
        INSERT INTO edges (src_type, src_id, dst_type, dst_id, edge_type, weight, observed_count)
        SELECT 'tag', tag_a_id, 'tag', tag_b_id, 'co_tag',
               coalesce(jaccard, 0.0), count
          FROM tag_cooccurrence WHERE coalesce(jaccard, 0) > 0""")


def build_shares_sound() -> int:
    """Account <-> account through a shared sound.

    Weighted by the sound's own reach: a sound used by three accounts makes a
    much more meaningful tie between two of them than one used by three hundred.
    """
    return _replace("shares_sound", """
        INSERT INTO edges (src_type, src_id, dst_type, dst_id, edge_type, weight, observed_count)
        WITH acc_sound AS (
            SELECT DISTINCT i.account_id AS acct, s.sound_id AS snd
              FROM item_sounds s JOIN items i ON i.id = s.item_id
             WHERE i.account_id IS NOT NULL),
             reach AS (SELECT snd, count(*) AS n FROM acc_sound GROUP BY snd)
        SELECT 'account', a.acct, 'account', b.acct, 'shares_sound',
               min(1.0, sum(1.0 / (1.0 + ln(r.n)))), count(*)
          FROM acc_sound a
          JOIN acc_sound b ON b.snd = a.snd AND a.acct < b.acct
          JOIN reach r ON r.snd = a.snd
         GROUP BY a.acct, b.acct""")


def build_shares_tag() -> int:
    """Account <-> account through shared tags, IDF-weighted the same way."""
    return _replace("shares_tag", """
        INSERT INTO edges (src_type, src_id, dst_type, dst_id, edge_type, weight, observed_count)
        WITH n AS (SELECT CAST(count(*) AS REAL) AS total FROM items),
             acc_tag AS (
                SELECT DISTINCT i.account_id AS acct, it.tag_id AS tag
                  FROM item_tags it JOIN items i ON i.id = it.item_id
                 WHERE i.account_id IS NOT NULL),
             df AS (SELECT tag_id, count(DISTINCT item_id) AS c FROM item_tags GROUP BY tag_id)
        SELECT 'account', a.acct, 'account', b.acct, 'shares_tag',
               min(1.0, sum(ln(n.total / df.c) / ln(max(n.total, 2.718)))), count(*)
          FROM acc_tag a
          JOIN acc_tag b ON b.tag = a.tag AND a.acct < b.acct
          JOIN df ON df.tag_id = a.tag CROSS JOIN n
         WHERE df.c > 0
         GROUP BY a.acct, b.acct
        HAVING sum(ln(n.total / df.c)) > 0""")


def build_same_audio(threshold: int = 32) -> int:
    """Sound <-> sound where the AUDIO matches but the platform id does not.

    This is the laundered re-upload: identical audio issued a new sound id after
    a takedown. Sound-id analysis sees two unrelated sounds and hashtag analysis
    sees nothing at all, so this edge is the only thing that reconnects them.
    """
    rows = engine.query(
        "SELECT id, fingerprint FROM sounds WHERE fingerprint IS NOT NULL")
    pairs = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            dist = bin(int(a["fingerprint"], 16) ^ int(b["fingerprint"], 16)).count("1")
            if dist <= threshold:
                pairs.append((int(a["id"]), int(b["id"]), 1.0 - dist / 256.0))
    with engine.transaction():
        engine.execute("DELETE FROM edges WHERE edge_type = 'same_audio'")
        engine.connect().executemany(
            """INSERT INTO edges (src_type, src_id, dst_type, dst_id, edge_type, weight)
               VALUES ('sound', ?, 'sound', ?, 'same_audio', ?)""", pairs)
    return len(pairs)


def build_links_to() -> int:
    """Account/item -> external destination, keyed by normalised URL.

    Two accounts pointing at the same Telegram channel is one of the strongest
    off-platform ties available, and it is exactly what v1's empty external_links
    table could never show.
    """
    return _replace("links_to", """
        INSERT INTO edges (src_type, src_id, dst_type, dst_id, edge_type, weight, observed_count)
        SELECT l.source_type, l.source_id, 'link', min(l.id), 'links_to',
               CASE WHEN l.platform IN ('telegram','discord','session') THEN 1.0 ELSE 0.5 END,
               count(*)
          FROM links l
         WHERE l.source_type IN ('item','account') AND l.normalised_url IS NOT NULL
         GROUP BY l.source_type, l.source_id, l.normalised_url""")


def rebuild_all(verbose: bool = True, *, with_audio: bool = True) -> dict[str, int]:
    builders = [("authored", build_authored), ("uses_tag", build_uses_tag),
                ("uses_sound", build_uses_sound), ("commented_on", build_commented_on),
                ("co_tag", build_co_tag), ("shares_sound", build_shares_sound),
                ("shares_tag", build_shares_tag), ("links_to", build_links_to)]
    if with_audio:
        builders.append(("same_audio", build_same_audio))
    out = {}
    for name, fn in builders:
        t = time.perf_counter()
        out[name] = fn()
        if verbose:
            print(f"    {name:<14} {out[name]:>9,} edges  {time.perf_counter()-t:6.2f}s")
    engine.execute("ANALYZE")
    return out


def neighbours(node_type: str, node_id: int, *, min_weight: float = 0.0,
               limit: int = 200) -> list[dict]:
    """One hop out, in both directions — edges are stored once, undirected ties
    with the lower id first, so traversal has to look both ways."""
    rows = engine.query(
        """SELECT dst_type AS t, dst_id AS i, edge_type, weight FROM edges
            WHERE src_type=? AND src_id=? AND weight >= ?
            UNION ALL
           SELECT src_type AS t, src_id AS i, edge_type, weight FROM edges
            WHERE dst_type=? AND dst_id=? AND weight >= ?
            ORDER BY weight DESC LIMIT ?""",
        (node_type, node_id, min_weight, node_type, node_id, min_weight, limit))
    return [{"node_type": r["t"], "node_id": int(r["i"]),
             "edge_type": r["edge_type"], "weight": float(r["weight"])} for r in rows]

"""Read helpers behind the UI.

Every function here returns plain dicts and takes only bound parameters. The
one place a caller-supplied string reaches SQL directly is the ORDER BY column,
and that is resolved through an allow-list map rather than interpolated — a
sort control is the classic way an otherwise-parameterised read layer grows an
injection hole.

Paging is keyset-free (OFFSET) on purpose: the Explorer's worst case is a few
hundred thousand rows and migration 003 added the index that makes the default
unfiltered ORDER BY cheap. A cursor scheme would buy nothing here and would
break the "jump to page N" control the dense table wants.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Sequence

from ..db import engine

MAX_LIMIT = 500
DEFAULT_LIMIT = 100


class BadQuery(ValueError):
    """A caller-supplied search expression SQLite could not parse.

    FTS5 MATCH is the one place user text becomes a query *expression* rather
    than a bound value. SQLite reports the failures inconsistently — an
    unbalanced quote surfaces as OperationalError("unterminated string"), a bad
    operator as 'fts5: syntax error near ...' — so matching on the message text
    is not a reliable way to tell a client mistake from a server fault. Any
    OperationalError raised while a search term is in play is treated as the
    client's, and reported as 400.
    """



def _rows(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    return [dict(r) for r in engine.query(sql, params)]


def _one(sql: str, params: Sequence[Any] = ()) -> dict | None:
    r = engine.query_one(sql, params)
    return dict(r) if r else None


def _clamp(limit: int | None, offset: int | None) -> tuple[int, int]:
    lim = DEFAULT_LIMIT if limit is None else max(1, min(int(limit), MAX_LIMIT))
    off = max(0, int(offset or 0))
    return lim, off


def _order(spec: str | None, allowed: dict[str, str], default: str,
           default_dir: str = "ASC") -> str:
    """Resolve a sort key through an allow-list, always returning an explicit
    direction. Unknown keys fall back to the default rather than erroring — a
    stale bookmark should not 500. A leading "-" means descending."""
    if not spec:
        return f"{allowed[default]} {default_dir}"
    col = allowed.get(spec.lstrip("-"))
    if col is None:                      # unknown key: fall back, don't 500
        return f"{allowed[default]} {default_dir}"
    return f"{col} {'DESC' if spec.startswith('-') else 'ASC'}"


# ── dashboard ────────────────────────────────────────────────────────────────

_CORPUS_TABLES = [
    ("items", "items"), ("accounts", "accounts"), ("tags", "tags"),
    ("sounds", "sounds"), ("comments", "comments"), ("transcripts", "transcripts"),
    ("links", "links"), ("edges", "edges"), ("classifications", "classifications"),
    ("gold_annotations", "gold_annotations"),
]


def overview() -> dict:
    counts = {label: engine.scalar(f"SELECT COUNT(*) FROM {t}") for label, t in _CORPUS_TABLES}

    findings = engine.scalar(
        "SELECT COUNT(*) FROM classifications WHERE is_finding = 1") or 0
    # "candidate" is a verdict, not a stage: stage records WHICH classifier
    # spoke (lexicon / llm), verdict records what it concluded. Counting
    # stage='candidate' silently returns 0 forever.
    verdicts = {r["verdict"]: r["n"] for r in _rows(
        "SELECT verdict, COUNT(*) AS n FROM classifications "
        "WHERE verdict IS NOT NULL GROUP BY verdict")}
    candidates = verdicts.get("candidate", 0)

    jobs = _rows("SELECT status, COUNT(*) AS n FROM fetch_jobs GROUP BY status ORDER BY n DESC")
    runs = _rows(
        """SELECT id, stage, version, model, target_count, started_at, finished_at
           FROM classifier_runs ORDER BY id DESC LIMIT 8""")
    lex = _one(
        """SELECT v.id, v.version, v.created_at, COUNT(e.id) AS entries
           FROM lexicon_versions v LEFT JOIN lexicon_entries e ON e.lexicon_version_id = v.id
           GROUP BY v.id ORDER BY v.id DESC LIMIT 1""")
    span = _one(
        "SELECT MIN(posted_at) AS first_post, MAX(posted_at) AS last_post FROM items WHERE posted_at IS NOT NULL")
    platforms = _rows(
        """SELECT p.name, COUNT(i.id) AS items FROM platforms p
           LEFT JOIN items i ON i.platform_id = p.id GROUP BY p.id
           HAVING items > 0 ORDER BY items DESC""")
    saturation = _rows(
        """SELECT fetches, known_accounts, known_items, new_accounts, at
           FROM saturation_samples ORDER BY id""")
    return {
        "counts": counts, "findings": findings, "candidates": candidates,
        "verdicts": verdicts,
        "jobs": jobs, "runs": runs, "lexicon": lex, "span": span or {},
        "platforms": platforms, "saturation": saturation,
        "db": str(engine.db_path()),
    }


# ── explorer ─────────────────────────────────────────────────────────────────

_ITEM_SORT = {
    "posted": "i.posted_at", "views": "i.view_count", "likes": "i.like_count",
    "comments": "i.comment_count", "collected": "i.collected_at", "id": "i.id",
}


def items(q: str | None = None, account: int | None = None, tag: int | None = None,
          sound: int | None = None, has_findings: bool = False,
          sort: str | None = None, limit: int | None = None,
          offset: int | None = None) -> dict:
    lim, off = _clamp(limit, offset)
    where: list[str] = []
    params: list[Any] = []

    if q:
        # FTS over caption/title/description. MATCH is fed as a bound parameter,
        # so a query containing quotes is a bad FTS expression, never SQL.
        where.append("i.id IN (SELECT rowid FROM items_fts WHERE items_fts MATCH ?)")
        params.append(q)
    if account:
        where.append("i.account_id = ?")
        params.append(account)
    if tag:
        where.append("i.id IN (SELECT item_id FROM item_tags WHERE tag_id = ?)")
        params.append(tag)
    if sound:
        where.append("i.id IN (SELECT item_id FROM item_sounds WHERE sound_id = ?)")
        params.append(sound)
    if has_findings:
        where.append("i.id IN (SELECT target_id FROM classifications "
                     "WHERE target_type = 'item' AND is_finding = 1)")

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        return _items_page(clause, params, sort, lim, off)
    except sqlite3.OperationalError as exc:
        if q:
            raise BadQuery(str(exc)) from exc
        raise


def _items_page(clause: str, params: list, sort: str | None,
                lim: int, off: int) -> dict:
    total = engine.scalar(f"SELECT COUNT(*) FROM items i{clause}", params) or 0
    rows = _rows(
        f"""SELECT i.id, i.platform_item_id, i.kind, i.title, i.caption, i.posted_at,
                   i.view_count, i.like_count, i.comment_count, i.url,
                   a.handle AS account_handle, a.id AS account_id,
                   (SELECT COUNT(*) FROM classifications c
                     WHERE c.target_type = 'item' AND c.target_id = i.id AND c.is_finding = 1)
                     AS findings
            FROM items i LEFT JOIN accounts a ON a.id = i.account_id
            {clause} ORDER BY {_order(sort, _ITEM_SORT, 'posted', 'DESC')} LIMIT ? OFFSET ?""",
        [*params, lim, off])
    return {"total": total, "limit": lim, "offset": off, "rows": rows}


def item_detail(item_id: int) -> dict | None:
    it = _one(
        """SELECT i.*, a.handle AS account_handle, p.name AS platform
           FROM items i LEFT JOIN accounts a ON a.id = i.account_id
           LEFT JOIN platforms p ON p.id = i.platform_id WHERE i.id = ?""", [item_id])
    if not it:
        return None
    it.pop("raw_json", None)          # large, and never rendered
    return {
        "item": it,
        "tags": _rows("""SELECT t.id, t.tag, t.display_name FROM item_tags it
                         JOIN tags t ON t.id = it.tag_id WHERE it.item_id = ?
                         ORDER BY t.tag""", [item_id]),
        "sounds": _rows("""SELECT s.id, s.title, s.author_name, s.use_count
                           FROM item_sounds s2 JOIN sounds s ON s.id = s2.sound_id
                           WHERE s2.item_id = ?""", [item_id]),
        "comments": _rows("""SELECT id, author_hash, text, like_count, posted_at
                             FROM comments WHERE item_id = ?
                             ORDER BY like_count DESC, id LIMIT 200""", [item_id]),
        "classifications": _rows(
            """SELECT c.id, c.stage, c.label, c.verdict, c.domain, c.harm_type, c.stance,
                      c.severity, c.confidence, c.evidence_span, c.rationale, c.is_finding,
                      c.run_id, r.version AS run_version
               FROM classifications c LEFT JOIN classifier_runs r ON r.id = c.run_id
               WHERE c.target_type = 'item' AND c.target_id = ?
               ORDER BY c.is_finding DESC, c.id""", [item_id]),
        "metrics": _rows("""SELECT observed_at, view_count, like_count, comment_count
                            FROM item_metrics WHERE item_id = ? ORDER BY observed_at""", [item_id]),
    }


# ── findings ─────────────────────────────────────────────────────────────────

_CLS_SORT = {"id": "c.id", "confidence": "c.confidence", "severity": "c.severity"}


def classifications(stage: str | None = None, verdict: str | None = None,
                    domain: str | None = None, harm: str | None = None,
                    stance: str | None = None, run: int | None = None,
                    findings_only: bool = False, target_type: str | None = None,
                    sort: str | None = None, limit: int | None = None,
                    offset: int | None = None) -> dict:
    lim, off = _clamp(limit, offset)
    where, params = [], []
    for col, val in (("c.stage", stage), ("c.verdict", verdict), ("c.domain", domain),
                     ("c.harm_type", harm), ("c.stance", stance),
                     ("c.target_type", target_type)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    if run:
        where.append("c.run_id = ?")
        params.append(run)
    if findings_only:
        where.append("c.is_finding = 1")

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = engine.scalar(f"SELECT COUNT(*) FROM classifications c{clause}", params) or 0
    rows = _rows(
        f"""SELECT c.id, c.target_type, c.target_id, c.stage, c.label, c.verdict,
                   c.domain, c.harm_type, c.stance, c.severity, c.confidence,
                   c.evidence_span, c.is_finding, c.run_id, c.rationale,
                   r.version AS run_version, l.pattern AS lexicon_pattern, l.tier
            FROM classifications c
            LEFT JOIN classifier_runs r ON r.id = c.run_id
            LEFT JOIN lexicon_entries l ON l.id = c.lexicon_entry_id
            {clause} ORDER BY {_order(sort, _CLS_SORT, 'id', 'DESC')} LIMIT ? OFFSET ?""",
        [*params, lim, off])
    return {"total": total, "limit": lim, "offset": off, "rows": rows}


def classification_facets() -> dict:
    """Distinct values actually present, so the filter bar only ever offers
    something that returns rows."""
    def vals(col: str) -> list[str]:
        return [r[col] for r in _rows(
            f"SELECT DISTINCT {col} AS {col} FROM classifications "
            f"WHERE {col} IS NOT NULL AND {col} <> '' ORDER BY {col}")]
    return {
        "stage": vals("stage"), "verdict": vals("verdict"), "domain": vals("domain"),
        "harm_type": vals("harm_type"), "stance": vals("stance"),
        "target_type": vals("target_type"),
        "runs": _rows("SELECT id, stage, version FROM classifier_runs ORDER BY id DESC"),
    }


def classification_target(target_type: str, target_id: int) -> dict | None:
    """The text a verdict was actually made against — without it a findings row
    is an assertion the reviewer cannot check."""
    if target_type == "comment":
        return _one("""SELECT c.id, c.text AS text, c.author_hash, c.posted_at,
                              c.item_id FROM comments c WHERE c.id = ?""", [target_id])
    if target_type == "item":
        return _one("""SELECT id, caption AS text, title, posted_at, url
                       FROM items WHERE id = ?""", [target_id])
    if target_type == "tag":
        return _one("SELECT id, tag AS text, display_name FROM tags WHERE id = ?", [target_id])
    if target_type == "account":
        return _one("SELECT id, bio AS text, handle, display_name FROM accounts WHERE id = ?",
                    [target_id])
    return None


# ── network ──────────────────────────────────────────────────────────────────

_NODE_SORT = {
    "pagerank": "n.pagerank", "degree": "n.degree", "weighted": "n.weighted_degree",
    "core": "n.core_number", "bridge": "n.bridge_score", "community": "n.community",
}


def nodes(node_type: str | None = None, community: int | None = None,
          sort: str | None = None, limit: int | None = None,
          offset: int | None = None) -> dict:
    lim, off = _clamp(limit, offset)
    where, params = [], []
    if node_type:
        where.append("n.node_type = ?")
        params.append(node_type)
    if community is not None:
        where.append("n.community = ?")
        params.append(community)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = engine.scalar(f"SELECT COUNT(*) FROM node_metrics n{clause}", params) or 0
    rows = _rows(
        f"""SELECT n.*,
                   CASE n.node_type
                     WHEN 'account' THEN (SELECT handle FROM accounts WHERE id = n.node_id)
                     WHEN 'tag'     THEN (SELECT tag    FROM tags     WHERE id = n.node_id)
                     WHEN 'sound'   THEN (SELECT title  FROM sounds   WHERE id = n.node_id)
                     WHEN 'item'    THEN (SELECT COALESCE(title, substr(caption,1,60))
                                          FROM items WHERE id = n.node_id)
                   END AS label
            FROM node_metrics n {clause}
            ORDER BY {_order(sort, _NODE_SORT, 'pagerank', 'DESC')} LIMIT ? OFFSET ?""",
        [*params, lim, off])
    return {"total": total, "limit": lim, "offset": off, "rows": rows}


def node_edges(node_type: str, node_id: int, limit: int | None = None) -> list[dict]:
    lim, _ = _clamp(limit, 0)
    return _rows(
        """SELECT e.*, 'out' AS direction FROM edges e
             WHERE e.src_type = ? AND e.src_id = ?
           UNION ALL
           SELECT e.*, 'in' AS direction FROM edges e
             WHERE e.dst_type = ? AND e.dst_id = ?
           ORDER BY weight DESC LIMIT ?""",
        [node_type, node_id, node_type, node_id, lim])


def communities() -> list[dict]:
    return _rows(
        """SELECT community, COUNT(*) AS members,
                  SUM(node_type = 'account') AS accounts,
                  SUM(node_type = 'tag') AS tags,
                  ROUND(AVG(pagerank), 6) AS avg_pagerank,
                  ROUND(MAX(bridge_score), 4) AS max_bridge
           FROM node_metrics WHERE community IS NOT NULL
           GROUP BY community ORDER BY members DESC""")


def edge_types() -> list[dict]:
    # Written by network.rebuild() at the end of every graph build. Before the
    # first build the table is empty, and the live aggregate is cheap anyway.
    stored = _rows("SELECT edge_type, n, avg_weight, max_weight FROM edge_type_stats "
                   "ORDER BY n DESC")
    if stored:
        return stored
    return _rows(
        """SELECT edge_type, COUNT(*) AS n, ROUND(AVG(weight), 4) AS avg_weight,
                  ROUND(MAX(weight), 4) AS max_weight
           FROM edges GROUP BY edge_type ORDER BY n DESC""")


# ── lexicon ──────────────────────────────────────────────────────────────────

def lexicon(version: int | None = None, tier: str | None = None,
            domain: str | None = None, q: str | None = None,
            limit: int | None = None, offset: int | None = None) -> dict:
    lim, off = _clamp(limit, offset)
    where, params = [], []
    if version:
        where.append("e.lexicon_version_id = ?")
        params.append(version)
    else:
        where.append("e.lexicon_version_id = (SELECT MAX(id) FROM lexicon_versions)")
    if tier:
        where.append("e.tier = ?")
        params.append(tier)
    if domain:
        where.append("e.domain = ?")
        params.append(domain)
    if q:
        where.append("(e.pattern LIKE ? OR e.definition LIKE ? OR e.slug LIKE ?)")
        params += [f"%{q}%"] * 3
    clause = " WHERE " + " AND ".join(where)
    total = engine.scalar(f"SELECT COUNT(*) FROM lexicon_entries e{clause}", params) or 0
    rows = _rows(
        f"""SELECT e.*, (SELECT COUNT(*) FROM classifications c
                          WHERE c.lexicon_entry_id = e.id) AS hits,
                   (SELECT COUNT(*) FROM classifications c
                     WHERE c.lexicon_entry_id = e.id AND c.is_finding = 1) AS findings
            FROM lexicon_entries e {clause}
            ORDER BY e.tier, e.domain, e.pattern LIMIT ? OFFSET ?""",
        [*params, lim, off])
    return {"total": total, "limit": lim, "offset": off, "rows": rows}


def taxonomy() -> dict:
    return {
        "domains": _rows("SELECT * FROM domains ORDER BY family, slug"),
        "harm_types": _rows("SELECT * FROM harm_types ORDER BY slug"),
        "stances": _rows("SELECT * FROM stances ORDER BY slug"),
        "versions": _rows("""SELECT v.id, v.version, v.created_at, v.notes,
                                    COUNT(e.id) AS entries
                             FROM lexicon_versions v
                             LEFT JOIN lexicon_entries e ON e.lexicon_version_id = v.id
                             GROUP BY v.id ORDER BY v.id DESC"""),
    }


# ── queue / provenance ───────────────────────────────────────────────────────

def jobs(status: str | None = None, limit: int | None = None,
         offset: int | None = None) -> dict:
    lim, off = _clamp(limit, offset)
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = engine.scalar(f"SELECT COUNT(*) FROM fetch_jobs{clause}", params) or 0
    rows = _rows(
        f"""SELECT id, job_type, target, status, priority, attempts, depth, score,
                   drift, edge_type, last_error, created_at, finished_at
            FROM fetch_jobs{clause} ORDER BY id DESC LIMIT ? OFFSET ?""",
        [*params, lim, off])
    return {"total": total, "limit": lim, "offset": off, "rows": rows}


def propagation(limit: int | None = None, offset: int | None = None) -> dict:
    lim, off = _clamp(limit, offset)
    total = engine.scalar("SELECT COUNT(*) FROM propagation_events") or 0
    rows = _rows(
        """SELECT id, run_id, src_type, src_id, edge_type, target, depth,
                  ROUND(score, 4) AS score, ROUND(drift, 4) AS drift, decision, at
           FROM propagation_events ORDER BY id DESC LIMIT ? OFFSET ?""", [lim, off])
    decisions = _rows(
        "SELECT decision, COUNT(*) AS n FROM propagation_events GROUP BY decision ORDER BY n DESC")
    return {"total": total, "limit": lim, "offset": off, "rows": rows,
            "decisions": decisions, "edge_yield": _rows("SELECT * FROM edge_yield")}

"""Run bookkeeping: every verdict belongs to a versioned, reproducible run.

v1 wrote flags directly onto entity rows. There was no record of which rules
produced them, no way to re-run without destroying the previous answer, and no
way to compare two approaches on the same data. Everything here exists so that
"this comment is antisemitic" is always shorthand for "run 7, lexicon v2.0,
fingerprint d3f35593, said so, and here is the span it relied on".
"""

from __future__ import annotations

import time
from typing import Iterable, Iterator

from ..db import engine
from . import lexicon as L


# ─── lexicon <-> database ────────────────────────────────────────────────────

def sync_lexicon(version: str | None = None) -> tuple[int, int]:
    """Persist the in-code lexicon as a versioned row set.

    The stored copy is what classifications point at, so a result stays
    interpretable even after the Python has moved on.
    """
    version = version or L.VERSION
    fp = L.fingerprint()
    with engine.transaction():
        row = engine.query_one("SELECT id, hash FROM lexicon_versions WHERE version = ?",
                               (version,))
        if row and row["hash"] == fp:
            lex_id = int(row["id"])
        elif row:
            # Same name, different content. Refuse rather than silently
            # rewriting history that stored classifications depend on.
            raise RuntimeError(
                f"lexicon version {version!r} already stored with a different "
                f"fingerprint ({row['hash'][:12]} vs {fp[:12]}). Bump VERSION in "
                f"lexicon.py instead of redefining a published version.")
        else:
            lex_id = int(engine.execute(
                "INSERT INTO lexicon_versions (version, hash, notes) VALUES (?,?,?) "
                "RETURNING id",
                (version, fp,
                 f"{len(L.ALL_ENTRIES)} entries across tiers A/B/C; "
                 f"{len(L.RETIRED)} v1 patterns retired")).fetchone()[0])

        existing = {r["slug"] for r in engine.query(
            "SELECT slug FROM lexicon_entries WHERE lexicon_version_id = ?", (lex_id,))}
        new = [e for e in L.ALL_ENTRIES if e.slug not in existing]
        if new:
            engine.connect().executemany(
                """INSERT INTO lexicon_entries
                       (lexicon_version_id, slug, pattern, pattern_type, tier,
                        category, severity, definition, origin, lang, notes)
                   VALUES (?,?,?,'regex',?,?,NULL,?,?,?,'')""",
                [(lex_id, e.slug, e.pattern, e.tier, e.category, e.note, e.origin, e.lang)
                 for e in new])
    return lex_id, len(new)


def entry_ids(lexicon_version_id: int) -> dict[str, int]:
    return {r["slug"]: int(r["id"]) for r in engine.query(
        "SELECT id, slug FROM lexicon_entries WHERE lexicon_version_id = ?",
        (lexicon_version_id,))}


# ─── runs ────────────────────────────────────────────────────────────────────

def start(stage: str, version: str, *, lexicon_version_id: int | None = None,
          model: str | None = None, prompt_hash: str | None = None,
          params: str | None = None, notes: str = "") -> int:
    return int(engine.execute(
        """INSERT INTO classifier_runs
               (stage, version, lexicon_version_id, model, prompt_hash, params_json, notes)
           VALUES (?,?,?,?,?,?,?) RETURNING id""",
        (stage, version, lexicon_version_id, model, prompt_hash, params, notes)).fetchone()[0])


def finish(run_id: int, target_count: int) -> None:
    engine.execute("UPDATE classifier_runs SET finished_at=unixepoch(), target_count=? "
                   "WHERE id=?", (target_count, run_id))


def latest(stage: str) -> int | None:
    row = engine.query_one(
        "SELECT id FROM classifier_runs WHERE stage=? AND finished_at IS NOT NULL "
        "ORDER BY finished_at DESC LIMIT 1", (stage,))
    return int(row["id"]) if row else None


# ─── applying the lexicon ────────────────────────────────────────────────────

TARGETS: dict[str, tuple[str, str, str]] = {
    # target_type -> (table, id column, text expression)
    "comment": ("comments", "id", "text"),
    "item":    ("items", "id", "coalesce(caption,'') || ' ' || coalesce(title,'') "
                              "|| ' ' || coalesce(description,'')"),
    "account": ("accounts", "id", "coalesce(bio,'')"),
    # Tags are scanned too. v1 classified hashtags from its dictionary, so
    # omitting them made any v1-vs-v2 comparison unfair by construction —
    # every tag v1 categorised counted as a v2 miss.
    "tag":     ("tags", "id", "'#' || tag"),
}


def iter_targets(target_type: str, batch: int = 5000) -> Iterator[tuple[int, str]]:
    table, idcol, expr = TARGETS[target_type]
    last = 0
    while True:
        rows = engine.query(
            f"SELECT {idcol} AS id, {expr} AS text FROM {table} "
            f"WHERE {idcol} > ? ORDER BY {idcol} LIMIT ?", (last, batch))
        if not rows:
            return
        for r in rows:
            yield int(r["id"]), r["text"] or ""
        last = int(rows[-1]["id"])


def scan_corpus(target_types: Iterable[str] = ("comment", "item", "account", "tag"),
                *, notes: str = "", verbose: bool = True) -> dict[str, object]:
    """Generate candidates across the corpus. Writes nothing but candidates —
    no severity, no verdict, because that is not this stage's job."""
    lex_id, added = sync_lexicon()
    ids = entry_ids(lex_id)
    run_id = start("lexicon", L.VERSION, lexicon_version_id=lex_id,
                   params=f'{{"gate":"A|B|2C","fingerprint":"{L.fingerprint()[:16]}"}}',
                   notes=notes or "candidate generation, tuned for recall")

    started = time.perf_counter()
    stats = {"scanned": 0, "candidates": 0, "matches": 0,
             "by_tier": {}, "by_category": {}, "by_type": {}}
    pending: list[tuple] = []

    for target_type in target_types:
        seen = 0
        for target_id, text in iter_targets(target_type):
            seen += 1
            stats["scanned"] += 1
            cand = L.scan(text)
            if not cand.is_candidate:
                continue
            stats["candidates"] += 1
            top = cand.top_tier
            stats["by_tier"][top] = stats["by_tier"].get(top, 0) + 1
            for m in cand.matches:
                stats["matches"] += 1
                stats["by_category"][m.category] = stats["by_category"].get(m.category, 0) + 1
                pending.append((target_type, target_id, run_id, "lexicon", m.category,
                                None, None, m.span, m.start, m.end, m.note[:500],
                                ids.get(m.slug), "candidate"))
            if len(pending) >= 5000:
                _flush(pending)
        stats["by_type"][target_type] = seen

    _flush(pending)
    finish(run_id, stats["candidates"])
    stats["run_id"] = run_id
    stats["lexicon_version_id"] = lex_id
    stats["entries_added"] = added
    stats["elapsed"] = round(time.perf_counter() - started, 2)
    if verbose:
        print(f"  run {run_id}: scanned {stats['scanned']:,}, "
              f"{stats['candidates']:,} candidates, {stats['matches']:,} matches "
              f"in {stats['elapsed']}s")
    return stats


def _flush(pending: list[tuple]) -> None:
    if not pending:
        return
    with engine.transaction():
        engine.connect().executemany(
            """INSERT INTO classifications
                   (target_type, target_id, run_id, stage, label, severity, confidence,
                    evidence_span, evidence_start, evidence_end, rationale,
                    lexicon_entry_id, verdict)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", pending)
    pending.clear()

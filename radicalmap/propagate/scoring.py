"""Deciding where to go next.

Propagation is a ranking problem: from everything one hop away, which handful
is worth spending a fetch on. Five components, and the third is the one that
makes this a learning system rather than a fixed heuristic.

  source       how interesting was the node we came from — a candidate reached
               from a confirmed finding is worth more than one reached from an
               unremarkable video
  tie          the edge weight, already normalised across modalities by
               propagate/edges.py
  yield        the MEASURED rate at which this edge type has produced anything.
               Read back from edge_yield, so the crawl learns that (say) shared
               sounds lead somewhere and shared hashtags mostly do not, instead
               of trusting weights someone guessed once
  novelty      penalise regions already densely sampled
  drift        distance from the seed set's territory, from propagate/control

The weights below are a starting prior, not a finding. Once edge_yield has real
counts the `yield` term dominates, which is the intended behaviour: the network's
own structure should tell you how to traverse it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..db import engine

WEIGHTS = {"source": 0.25, "tie": 0.20, "yield": 0.30, "novelty": 0.15, "drift": 0.35}

# Prior yield per edge type, used until enough outcomes accumulate to measure it.
# Deliberately encodes the argument made in the plan: a shared sound is a
# stronger tie than a shared hashtag, because a sound cannot be swapped out to
# evade detection without re-editing the video.
PRIOR_YIELD = {
    "authored": 0.70, "alt_of": 0.65, "shares_sound": 0.55, "same_audio": 0.60,
    "uses_sound": 0.45, "commented_on": 0.35, "shares_tag": 0.30,
    "uses_tag": 0.25, "co_tag": 0.20, "links_to": 0.40,
}
SHRINKAGE = 20      # observations before the measured rate fully replaces the prior


@dataclass
class Candidate:
    node_type: str
    node_id: int
    edge_type: str
    src_type: str
    src_id: int
    depth: int
    tie: float = 0.0
    source_signal: float = 0.0
    novelty: float = 1.0
    drift: float = 0.0
    score: float = 0.0
    components: dict = field(default_factory=dict)


def measured_yield() -> dict[str, float]:
    """Empirical yield per edge type, shrunk toward the prior.

    Shrinkage matters: three fetches down a new edge type that happened to find
    two findings is not evidence that it yields 0.67, and letting it dominate
    would send the whole crawl down a path chosen by noise.
    """
    out = dict(PRIOR_YIELD)
    for r in engine.query("SELECT edge_type, fetched, candidates FROM edge_yield"):
        et, n = r["edge_type"], int(r["fetched"] or 0)
        if n <= 0:
            continue
        # A rate, so capped: several candidates from one fetch is not a yield
        # of 5.0, and letting it through made a single observation swamp the
        # prior that shrinkage exists to protect.
        observed = min(1.0, (int(r["candidates"] or 0)) / n)
        prior = PRIOR_YIELD.get(et, 0.3)
        w = n / (n + SHRINKAGE)
        out[et] = w * observed + (1 - w) * prior
    return out


def source_signal(node_type: str, node_id: int) -> float:
    """How much the node we are expanding from suggests the region is worth it."""
    row = engine.query_one(
        """SELECT count(*) AS n,
                  sum(CASE WHEN verdict = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
                  max(CASE WHEN severity = 'high' THEN 1 ELSE 0 END) AS high
             FROM classifications WHERE target_type = ? AND target_id = ?""",
        (node_type, node_id))
    if not row or not row["n"]:
        return 0.15
    n = int(row["n"])
    confirmed = int(row["confirmed"] or 0)
    base = 0.35 + min(n, 6) / 12.0
    if confirmed:
        base += 0.25
    if row["high"]:
        base += 0.15
    return float(min(1.0, base))


def novelty(node_type: str, node_id: int) -> float:
    """1.0 for never-seen, falling as a region gets densely sampled."""
    row = engine.query_one(
        "SELECT degree FROM node_metrics WHERE node_type=? AND node_id=?",
        (node_type, node_id))
    if row is None:
        return 1.0
    return float(1.0 / (1.0 + math.log1p(max(int(row["degree"] or 0), 0))))


def expand(node_type: str, node_id: int, depth: int, *,
           min_weight: float = 0.05, limit: int = 120) -> list[Candidate]:
    """Everything one hop out, as scorable candidates."""
    from .edges import neighbours
    src = source_signal(node_type, node_id)
    out = []
    for n in neighbours(node_type, node_id, min_weight=min_weight, limit=limit):
        out.append(Candidate(n["node_type"], n["node_id"], n["edge_type"],
                             node_type, node_id, depth + 1,
                             tie=n["weight"], source_signal=src))
    return out


def score(candidates: list[Candidate], *, drift_monitor=None,
          yields: dict[str, float] | None = None,
          text_of=None) -> list[Candidate]:
    yields = yields or measured_yield()
    for c in candidates:
        c.novelty = novelty(c.node_type, c.node_id)
        y = yields.get(c.edge_type, 0.3)
        if drift_monitor is not None and text_of is not None:
            c.drift = drift_monitor.drift(text_of(c.node_type, c.node_id) or "")
        c.components = {"source": c.source_signal, "tie": c.tie, "yield": y,
                        "novelty": c.novelty, "drift": c.drift}
        c.score = float(
            WEIGHTS["source"] * c.source_signal
            + WEIGHTS["tie"] * c.tie
            + WEIGHTS["yield"] * y
            + WEIGHTS["novelty"] * c.novelty
            - WEIGHTS["drift"] * c.drift)
    candidates.sort(key=lambda c: -c.score)
    return candidates


# ─── outcome accounting ──────────────────────────────────────────────────────

def note_proposed(edge_type: str, n: int = 1) -> None:
    _bump(edge_type, "proposed", n)


def note_fetched(edge_type: str, *, items: int = 0, candidates: int = 0,
                 findings: int = 0) -> None:
    with engine.transaction():
        engine.execute(
            """INSERT INTO edge_yield (edge_type, fetched, items_found, candidates, findings)
               VALUES (?,1,?,?,?)
               ON CONFLICT (edge_type) DO UPDATE SET
                   fetched = fetched + 1,
                   items_found = items_found + excluded.items_found,
                   candidates = candidates + excluded.candidates,
                   findings = findings + excluded.findings,
                   updated_at = unixepoch()""",
            (edge_type, items, candidates, findings))


def _bump(edge_type: str, column: str, n: int) -> None:
    engine.execute(
        f"""INSERT INTO edge_yield (edge_type, {column}) VALUES (?,?)
            ON CONFLICT (edge_type) DO UPDATE SET
                {column} = {column} + excluded.{column}, updated_at = unixepoch()""",
        (edge_type, n))


def yield_report() -> list[dict]:
    measured = measured_yield()
    rows = [dict(r) for r in engine.query(
        "SELECT edge_type, proposed, fetched, items_found, candidates, findings "
        "FROM edge_yield ORDER BY fetched DESC")]
    for r in rows:
        r["effective_yield"] = round(measured.get(r["edge_type"], 0.0), 3)
        r["prior"] = PRIOR_YIELD.get(r["edge_type"])
    for et, prior in PRIOR_YIELD.items():
        if not any(r["edge_type"] == et for r in rows):
            rows.append({"edge_type": et, "proposed": 0, "fetched": 0,
                         "items_found": 0, "candidates": 0, "findings": 0,
                         "effective_yield": prior, "prior": prior})
    return rows

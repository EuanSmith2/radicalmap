"""The propagation loop.

Crawls in WAVES rather than expanding each item as it arrives, and the reason
is not convenience: most of the tie strengths this thing steers by are
population statistics. Hashtag IDF, shared-sound reach, co-occurrence Jaccard,
username rarity — none of them can be computed from a single fetched video. So
each wave is:

    collect a batch  ->  rebuild the derived structures  ->  expand from the
    best nodes, scored against the whole population  ->  enqueue the next wave

which also gives a natural place to sample the discovery rate and check whether
the crawl is still on topic.

Nothing here reaches the network. It queues work for the Phase 1 collection
engine, which is the thing that refuses to run without RM_ALLOW_NETWORK.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ..core.config import settings
from ..db import engine
from ..db.repo import jobs as Q
from ..db.repo import writers as W
from . import edges as E
from . import identity, scoring
from .control import DriftMonitor, SaturationMonitor, seed_texts


NODE_ROW_SQL = {
    "account": "SELECT handle, platform_account_id FROM accounts WHERE id=?",
    "item":    "SELECT url, platform_item_id FROM items WHERE id=?",
    "tag":     "SELECT tag FROM tags WHERE id=?",
    "sound":   "SELECT platform_sound_id FROM sounds WHERE id=?",
}


def node_row(node_type: str, node_id: int) -> dict | None:
    sql = NODE_ROW_SQL.get(node_type)
    if sql is None:
        return None
    row = engine.query_one(sql, (node_id,))
    return dict(row) if row else None


def target_for(node_type: str, node_id: int, adapter) -> tuple[str, str] | None:
    """Delegate to the adapter that will actually have to fetch it."""
    row = node_row(node_type, node_id)
    if row is None or adapter is None:
        return None
    return adapter.target_for(node_type, node_id, row)


def already_fetched(targets: list[str]) -> set[str]:
    """Targets a completed job already covers.

    Propagation kept re-proposing nodes it had already collected, because the
    graph edge to them exists precisely BECAUSE they were collected. Checking
    the queue is the direct fix; node degree was a poor proxy for it.
    """
    if not targets:
        return set()
    out: set[str] = set()
    for i in range(0, len(targets), 400):
        chunk = targets[i:i + 400]
        marks = ",".join("?" * len(chunk))
        out |= {r["target"] for r in engine.query(
            f"SELECT target FROM fetch_jobs WHERE status IN ('done','running','skipped') "
            f"AND target IN ({marks})", tuple(chunk))}
    return out


def text_of(node_type: str, node_id: int, sample: int = 8) -> str:
    """Representative text for judging a node's topicality.

    Deliberately NOT just the node's own field. Judging an account by its bio
    refused almost every account in a real run — bios are short, often empty,
    and frequently say nothing about what the account posts. A node is best
    represented by the content it actually produces, so an account is its recent
    captions, a tag or sound is the captions of the items carrying it.
    """
    if node_type == "item":
        row = engine.query_one("SELECT caption FROM items WHERE id=?", (node_id,))
        return (row["caption"] or "") if row else ""

    if node_type == "account":
        bio = engine.query_one("SELECT bio FROM accounts WHERE id=?", (node_id,))
        caps = engine.query(
            "SELECT caption FROM items WHERE account_id=? AND coalesce(caption,'') != '' "
            "ORDER BY coalesce(posted_at, first_seen_at) DESC LIMIT ?", (node_id, sample))
        return " ".join([(bio["bio"] or "") if bio else ""]
                        + [r["caption"] for r in caps]).strip()

    if node_type == "tag":
        tag = engine.query_one("SELECT tag FROM tags WHERE id=?", (node_id,))
        caps = engine.query(
            """SELECT i.caption FROM item_tags it JOIN items i ON i.id = it.item_id
                WHERE it.tag_id = ? AND coalesce(i.caption,'') != '' LIMIT ?""",
            (node_id, sample))
        return " ".join([f"#{tag['tag']}" if tag else ""]
                        + [r["caption"] for r in caps]).strip()

    if node_type == "sound":
        snd = engine.query_one("SELECT title FROM sounds WHERE id=?", (node_id,))
        caps = engine.query(
            """SELECT i.caption FROM item_sounds s JOIN items i ON i.id = s.item_id
                WHERE s.sound_id = ? AND coalesce(i.caption,'') != '' LIMIT ?""",
            (node_id, sample))
        return " ".join([(snd["title"] or "") if snd else ""]
                        + [r["caption"] for r in caps]).strip()
    return ""


@dataclass
class WaveResult:
    wave: int
    expanded_from: int
    candidates: int
    enqueued: int
    skipped: dict[str, int] = field(default_factory=dict)
    by_edge: dict[str, int] = field(default_factory=dict)
    saturation: dict | None = None
    drift: dict | None = None
    elapsed: float = 0.0


class Propagator:
    def __init__(self, *, adapter=None, platform: str = "tiktok", run_id: int | None = None,
                 max_depth: int | None = None, drift_threshold: float = 0.80,
                 per_wave: int = 200, frontier_size: int = 60,
                 min_score: float = 0.05, verbose: bool = True) -> None:
        self.adapter = adapter
        self.platform = platform
        self.platform_id = W.platform_id(platform)
        self.run_id = run_id
        self.max_depth = settings.max_depth if max_depth is None else max_depth
        self.per_wave = per_wave
        self.frontier_size = frontier_size
        self.min_score = min_score
        self.verbose = verbose
        self.wave_no = 0
        self.drift = DriftMonitor(threshold=drift_threshold).fit(seed_texts())
        if not self.drift.fitted and verbose:
            print("  note: no seed text yet — drift control is inactive until the "
                  "first wave refits it")
        self.saturation = SaturationMonitor(run_id=run_id)

    # ── derived structures ───────────────────────────────────────────────────

    def refresh(self, *, with_audio: bool = False) -> None:
        """Recompute everything the scorer steers by."""
        from ..analyse import metrics, network
        # Refit the drift centroid every wave. A Propagator is usually built
        # before anything has been collected, so its first fit sees nothing;
        # and seeds keep arriving during the early waves.
        self.drift.fit(seed_texts())
        metrics.rebuild_all(verbose=False)
        E.rebuild_all(verbose=False, with_audio=with_audio)
        identity.detect(verbose=False)
        network.rebuild(verbose=False)

    # ── frontier ─────────────────────────────────────────────────────────────

    def frontier(self) -> list[tuple[str, int, int]]:
        """Nodes worth expanding from: central, and not yet exhausted.

        Ordered by PageRank within the classified region rather than by
        recency — a crawl that always expands from whatever arrived last walks
        a random path through the network instead of working outward from its
        densest part.
        """
        rows = engine.query(
            """SELECT m.node_type, m.node_id, m.pagerank,
                      coalesce((SELECT count(*) FROM classifications c
                                 WHERE c.target_type = m.node_type
                                   AND c.target_id = m.node_id), 0) AS signal
                 FROM node_metrics m
                WHERE m.node_type IN ('account','item','sound','tag')
                ORDER BY (m.pagerank * (1 + signal)) DESC LIMIT ?""",
            (self.frontier_size,))
        return [(r["node_type"], int(r["node_id"]), 0) for r in rows]

    # ── one wave ─────────────────────────────────────────────────────────────

    def wave(self, *, refresh: bool = True) -> WaveResult:
        t0 = time.perf_counter()
        self.wave_no += 1
        if refresh:
            self.refresh()

        seeds = self.frontier()
        skipped = {"drift": 0, "budget": 0, "seen": 0, "score": 0, "unreachable": 0}
        by_edge: dict[str, int] = {}
        all_cands: list[scoring.Candidate] = []
        yields = scoring.measured_yield()

        for node_type, node_id, depth in seeds:
            cands = scoring.expand(node_type, node_id, depth)
            scoring.score(cands, drift_monitor=self.drift, yields=yields, text_of=text_of)
            all_cands += cands
        all_cands.sort(key=lambda c: -c.score)

        # One batched lookup of what has already been fetched, rather than a
        # query per candidate.
        prospective = []
        for c in all_cands:
            spec = target_for(c.node_type, c.node_id, self.adapter)
            if spec:
                prospective.append(spec[1])
        done_targets = already_fetched(prospective)

        events, jobs, enqueued = [], [], 0
        seen: set[tuple[str, int]] = set()
        for c in all_cands:
            if enqueued >= self.per_wave:
                skipped["budget"] += 1
                events.append((c, "skipped_budget"))
                continue
            key = (c.node_type, c.node_id)
            if key in seen:
                continue
            seen.add(key)
            self.drift.record(c.drift)

            if c.depth > self.max_depth:
                skipped["depth"] = skipped.get("depth", 0) + 1
                events.append((c, "skipped_depth"))
                continue
            if c.drift > self.drift.threshold:
                skipped["drift"] += 1
                events.append((c, "skipped_drift"))
                continue
            if c.score < self.min_score:
                skipped["score"] += 1
                events.append((c, "skipped_score"))
                continue
            spec = target_for(c.node_type, c.node_id, self.adapter)
            if spec is None:
                skipped["unreachable"] += 1
                events.append((c, "skipped_unreachable"))
                continue
            job_type, target = spec
            if target in done_targets:
                skipped["seen"] += 1
                events.append((c, "skipped_seen"))
                continue
            jobs.append({"platform_id": self.platform_id, "job_type": job_type,
                         "target": target, "priority": max(1, int(200 - c.score * 200)),
                         "depth": c.depth})
            events.append((c, "enqueued"))
            by_edge[c.edge_type] = by_edge.get(c.edge_type, 0) + 1
            enqueued += 1

        added = Q.enqueue(jobs) if jobs else 0
        skipped["seen"] += max(0, len(jobs) - added)

        with engine.transaction():
            engine.connect().executemany(
                """INSERT INTO propagation_events
                       (run_id, src_type, src_id, edge_type, target, depth, score,
                        drift, decision)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [(self.run_id, c.src_type, c.src_id, c.edge_type,
                  f"{c.node_type}/{c.node_id}", c.depth, c.score, c.drift, d)
                 for c, d in events])
            for et, n in by_edge.items():
                scoring.note_proposed(et, n)

        sat = self.saturation.tick(len(seeds))
        res = WaveResult(self.wave_no, len(seeds), len(all_cands), added,
                         skipped, by_edge, sat, self.drift.summary(),
                         round(time.perf_counter() - t0, 2))
        if self.verbose:
            print(f"  wave {self.wave_no}: expanded {len(seeds)} node(s) -> "
                  f"{len(all_cands)} candidates -> {added} queued  ({res.elapsed}s)")
            print(f"    by edge type : {by_edge or '—'}")
            print(f"    skipped      : {', '.join(f'{k}={v}' for k, v in skipped.items() if v) or 'none'}")
            if res.drift and res.drift.get("observations"):
                print(f"    drift        : mean {res.drift['mean_drift']}, "
                      f"p90 {res.drift['p90_drift']}, "
                      f"{res.drift['over_threshold']} over threshold")
        return res

    def report(self) -> dict[str, object]:
        return {
            "waves": self.wave_no,
            "drift": self.drift.summary(),
            "saturation": self.saturation.summary(),
            "edge_yield": scoring.yield_report(),
            "queue": Q.stats(),
        }


def decisions(limit: int = 20) -> list[dict]:
    return [dict(r) for r in engine.query(
        """SELECT edge_type, decision, count(*) AS n, round(avg(score),3) AS avg_score,
                  round(avg(drift),3) AS avg_drift
             FROM propagation_events GROUP BY edge_type, decision
            ORDER BY n DESC LIMIT ?""", (limit,))]

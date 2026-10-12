#!/usr/bin/env python
"""A multi-wave crawl through a simulated network.

Alternates collection and propagation, and tracks the two things that decide
whether the resulting sample means anything: is it still on topic, and has the
reachable community been enumerated.

Runs against the synthetic adapter in a throwaway database. Nothing here
touches a live platform.
"""
from __future__ import annotations
import asyncio, json, os, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ.update({"RADICALMAP_DB": str(tmp / "prop.db"),
                   "RADICALMAP_SALT_FILE": str(tmp / ".salt"),
                   "RM_WORKERS": "8", "RM_RPS": "100000", "RM_BURST": "400",
                   "RM_MAX_DEPTH": "4", "RM_EXPAND_ACCOUNTS": "0",
                   "RM_EXPAND_TAGS": "0", "RM_EXPAND_SOUNDS": "0"})

from radicalmap.collect.adapters.synthetic import SyntheticAdapter
from radicalmap.collect.frontier import Budget
from radicalmap.collect.worker import Engine
from radicalmap.db import engine, migrate
from radicalmap.db.repo import jobs as Q, writers as W
from radicalmap.propagate import identity, scoring
from radicalmap.propagate.engine import Propagator, decisions

def rule(t): print(f"\n{'═'*72}\n  {t}\n{'═'*72}")

CORPUS, WAVES, SEEDS = 4000, 6, 8
migrate.migrate(verbose=False)
pid = W.platform_id("tiktok")
src = W.ensure_source("seed", "seed_list", "propagation demo")

rule(f"seeding {SEEDS} items, then {WAVES} waves of collect -> propagate")
Q.enqueue([{"platform_id": pid, "job_type": "item", "target": f"syn://item/{i*137}",
            "source_id": src} for i in range(SEEDS)])

adapter = SyntheticAdapter(corpus_size=CORPUS, accounts=180, sounds=260,
                           latency=0.0005, failure_rate=0.01, gone_rate=0.01)
prop = Propagator(adapter=adapter, per_wave=120, frontier_size=40, verbose=True)

def collect(max_jobs: int) -> int:
    eng = Engine([adapter], "tiktok", source_id=src, method="synthetic",
                 budget=Budget(max_items=0), verbose=False)
    stats = asyncio.run(eng.run(max_jobs=max_jobs, idle_timeout=1.5))
    return stats.done

history = []
for w in range(1, WAVES + 1):
    done = collect(260)
    items = int(engine.scalar("SELECT count(*) FROM items") or 0)
    accts = int(engine.scalar("SELECT count(*) FROM accounts") or 0)
    print(f"\n  ── wave {w} ──  collected {done} job(s); corpus now "
          f"{items:,} items / {accts:,} accounts")
    res = prop.wave()
    history.append({"wave": w, "items": items, "accounts": accts,
                    "queued": res.enqueued, "by_edge": res.by_edge})

rule("saturation — new accounts discovered per wave")
print(f"  {'wave':>5} {'items':>8} {'accounts':>9} {'new':>6}   discovery")
prev = 0
for h in history:
    new = h["accounts"] - prev
    prev = h["accounts"]
    bar = "█" * min(int(new / 2), 46)
    print(f"  {h['wave']:>5} {h['items']:>8,} {h['accounts']:>9,} {new:>6}   {bar}")
print("\n  A flattening bar is the stopping criterion: when new accounts per")
print("  wave falls to near zero the reachable community is enumerated, and")
print("  further crawling buys duplicates. That is what lets a paper say the")
print("  network was mapped rather than that the crawl was stopped.")

rule("what the crawl learned about its own edges")
print(f"  {'edge type':<16} {'proposed':>9} {'prior':>7} {'effective':>10}")
for r in sorted(scoring.yield_report(), key=lambda r: -r["proposed"])[:8]:
    print(f"  {r['edge_type']:<16} {r['proposed']:>9} {str(r['prior']):>7} "
          f"{r['effective_yield']:>10}")

rule("drift control")
d = prop.drift.summary()
print(f"  seed families        : {prop.drift.families}")
print(f"  candidates scored    : {d['observations']}")
print(f"  mean drift           : {d['mean_drift']}   (p90 {d['p90_drift']})")
print(f"  refused for drift    : {d['over_threshold']} of {d['observations']}")
print("\n  decisions by edge type:")
for x in decisions(8):
    print(f"    {x['edge_type']:<14} {x['decision']:<16} n={x['n']:<5} "
          f"score={x['avg_score']}  drift={x['avg_drift']}")

rule("alt accounts found by username pattern")
identity.detect(verbose=True)
for c in identity.clusters(3):
    print(f"    cluster {c['cluster_id']}: "
          f"{', '.join('@' + m['handle'] for m in c['members'][:6])}")

rule("the network")
from radicalmap.analyse import network
network.rebuild()
print("\n  most central accounts:")
for r in network.top("account", "pagerank", 5):
    print(f"    @{str(r['label'])[:20]:<22} pr={r['pagerank']:.5f} deg={r['degree']:<5} "
          f"core={r['core_number']:<4} comm={r['community']:<4} bridge={r['bridge_score']:.2f}")
print("\n  bridges between communities (where a network reaches outside itself):")
for r in network.top("account", "bridge", 4):
    print(f"    @{str(r['label'])[:20]:<22} bridge={r['bridge_score']:.2f} "
          f"comm={r['community']} deg={r['degree']}")

print(f"\n  scratch db: {engine.db_path()}")

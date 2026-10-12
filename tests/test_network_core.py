#!/usr/bin/env python
"""k-core numbers: the O(n+m) bucket algorithm must match plain peeling.

The reference below is the original O(n^2) implementation, kept here as the
oracle. Random multigraphs (parallel edges between a pair count twice, which is
how two edge types between the same nodes appear in the store).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from radicalmap.analyse.network import Graph

def reference(g: Graph) -> np.ndarray:
    deg = g.degree().astype(np.int64)
    core = np.zeros(g.n, dtype=np.int64)
    alive = np.ones(g.n, dtype=bool)
    order = np.argsort(g.src, kind="stable")
    s, d = g.src[order], g.dst[order]
    starts = np.searchsorted(s, np.arange(g.n)); ends = np.searchsorted(s, np.arange(g.n), side="right")
    k = 0
    for _ in range(g.n):
        live = np.where(alive, deg, np.iinfo(np.int64).max)
        v = int(np.argmin(live))
        k = max(k, int(deg[v])); core[v] = k; alive[v] = False
        for u in d[starts[v]:ends[v]]:
            if alive[u] and deg[u] > 0:
                deg[u] -= 1
    return core

def graph(n: int, m: int, seed: int) -> Graph:
    rng = np.random.default_rng(seed)
    a = rng.zipf(1.8, m) % n; b = rng.integers(0, n, m)
    keep = a != b
    g = Graph.__new__(Graph)
    s, d = a[keep], b[keep]
    g.n = n
    g.src = np.concatenate([s, d]).astype(np.int64)
    g.dst = np.concatenate([d, s]).astype(np.int64)
    g.w = np.ones(g.src.size)
    return g

fails = []
for seed in range(25):
    rng = np.random.default_rng(seed)
    g = graph(int(rng.integers(20, 1500)), int(rng.integers(50, 6000)), seed)
    if not np.array_equal(g.core_numbers(), reference(g)):
        fails.append(seed)
empty = Graph.__new__(Graph); empty.n = 0
empty.src = empty.dst = np.zeros(0, dtype=np.int64); empty.w = np.zeros(0)
print(f"  {'ok  ' if not fails else 'FAIL'} bucket k-core matches peeling on 25 random multigraphs  {fails or ''}")
ok_empty = empty.core_numbers().size == 0
print(f"  {'ok  ' if ok_empty else 'FAIL'} empty graph")
if fails or not ok_empty: sys.exit("FAILED")
print("\n  all checks passed")

"""Network structure.

networkx is not installed and is not worth a dependency for four algorithms
that are each twenty to forty lines over the edge list already in the database.
Implementing them here also means the weighting is explicit rather than hidden
behind a default nobody chose.

Four measures, each answering a different question:

  degree        how connected, the crude version
  pagerank      how central, accounting for the centrality of your neighbours —
                the right "who matters here" measure for an undirected social
                graph
  k-core        how deep inside the dense part of the network a node sits. The
                k-core decomposition is the standard way to find a community's
                committed core as distinct from its periphery, which is exactly
                the distinction radicalisation research cares about.
  community     label propagation (Raghavan et al. 2007): near-linear, no
                parameters to tune, no dependency. Louvain would give slightly
                better modularity for considerably more code.

Plus bridge_score, which is not a standard measure but is the useful one here:
how many DISTINCT communities a node touches. A high-degree node buried inside
one cluster is a hub; a node connecting several is where a network reaches
outside itself, and that is where recruitment happens.
"""

from __future__ import annotations

import time

import numpy as np

from ..db import engine


class Graph:
    """The edge list, loaded once, as index arrays."""

    def __init__(self, edge_types: tuple[str, ...] | None = None,
                 node_types: tuple[str, ...] | None = None,
                 min_weight: float = 0.0) -> None:
        where = ["weight >= ?"]
        params: list = [min_weight]
        if edge_types:
            where.append(f"edge_type IN ({','.join('?' * len(edge_types))})")
            params += list(edge_types)
        if node_types:
            marks = ",".join("?" * len(node_types))
            where.append(f"src_type IN ({marks}) AND dst_type IN ({marks})")
            params += list(node_types) * 2
        rows = engine.query(
            f"SELECT src_type, src_id, dst_type, dst_id, weight FROM edges "
            f"WHERE {' AND '.join(where)}", tuple(params))

        index: dict[tuple[str, int], int] = {}
        src, dst, w = [], [], []
        for r in rows:
            a = index.setdefault((r["src_type"], int(r["src_id"])), len(index))
            b = index.setdefault((r["dst_type"], int(r["dst_id"])), len(index))
            if a == b:
                continue                       # self-loops carry no structure
            src.append(a); dst.append(b); w.append(float(r["weight"]))

        self.index = index
        self.nodes = [k for k, _ in sorted(index.items(), key=lambda kv: kv[1])]
        self.n = len(index)
        # Symmetrise: every tie here is mutual, and storing each once means both
        # directions have to be materialised for traversal and for PageRank.
        self.src = np.array(src + dst, dtype=np.int64)
        self.dst = np.array(dst + src, dtype=np.int64)
        self.w = np.array(w + w, dtype=np.float64)

    @property
    def m(self) -> int:
        return self.src.size // 2

    def degree(self) -> np.ndarray:
        return np.bincount(self.src, minlength=self.n)

    def weighted_degree(self) -> np.ndarray:
        return np.bincount(self.src, weights=self.w, minlength=self.n)

    def pagerank(self, damping: float = 0.85, iters: int = 60,
                 tol: float = 1e-8) -> np.ndarray:
        if self.n == 0:
            return np.zeros(0)
        wd = self.weighted_degree()
        wd[wd == 0] = 1.0
        r = np.full(self.n, 1.0 / self.n)
        teleport = (1.0 - damping) / self.n
        for _ in range(iters):
            contrib = self.w * (r[self.src] / wd[self.src])
            nxt = teleport + damping * np.bincount(self.dst, weights=contrib,
                                                   minlength=self.n)
            # Dangling mass: nodes with no edges would otherwise leak rank.
            nxt += damping * (1.0 - nxt.sum()) / self.n
            if np.abs(nxt - r).sum() < tol:
                r = nxt
                break
            r = nxt
        return r / max(r.sum(), 1e-12)

    def core_numbers(self) -> np.ndarray:
        """k-core numbers by bucket peeling (Batagelj & Zaversnik, 2003).

        The first version peeled by scanning every node for the minimum live
        degree on each step: O(n^2), about 7s at 117k nodes and an estimated
        11 minutes at a million. Keeping nodes sorted in degree buckets makes
        each removal O(degree), so the whole pass is O(n + m). Output is
        identical, including on multigraphs (two edge types between the same
        pair count twice, as before).
        """
        n = self.n
        if n == 0:
            return np.zeros(0, dtype=np.int64)
        deg_arr = self.degree().astype(np.int64)
        order = np.argsort(self.src, kind="stable")
        nbrs = self.dst[order].tolist()
        starts = np.searchsorted(self.src[order], np.arange(n + 1)).tolist()
        counts = np.bincount(deg_arr, minlength=int(deg_arr.max()) + 1)
        bin_start = np.concatenate(([0], np.cumsum(counts)[:-1])).tolist()
        vert = np.argsort(deg_arr, kind="stable").tolist()   # nodes by degree
        pos = [0] * n
        for i, v in enumerate(vert):
            pos[v] = i
        deg = deg_arr.tolist()
        for i in range(n):
            v = vert[i]
            dv = deg[v]                    # final: v's core number
            for u in nbrs[starts[v]:starts[v + 1]]:
                du = deg[u]
                if du > dv:
                    # move u to the front of its bucket, then shrink the bucket
                    pu, pw = pos[u], bin_start[du]
                    w = vert[pw]
                    if u != w:
                        vert[pu], vert[pw] = w, u
                        pos[u], pos[w] = pw, pu
                    bin_start[du] += 1
                    deg[u] = du - 1
        return np.array(deg, dtype=np.int64)

    def communities(self, iters: int = 30, seed: int = 1848) -> np.ndarray:
        """Asynchronous label propagation.

        Each node repeatedly adopts the weight-weighted most common label among
        its neighbours. Randomised visiting order, and ties broken randomly, so
        the algorithm does not oscillate between two equally good labellings.
        """
        rng = np.random.default_rng(seed)
        labels = np.arange(self.n)
        order = np.argsort(self.src, kind="stable")
        s_sorted, d_sorted, w_sorted = self.src[order], self.dst[order], self.w[order]
        starts = np.searchsorted(s_sorted, np.arange(self.n))
        ends = np.searchsorted(s_sorted, np.arange(self.n), side="right")

        for _ in range(iters):
            changed = 0
            for v in rng.permutation(self.n):
                a, b = starts[v], ends[v]
                if a == b:
                    continue
                nbr, wt = d_sorted[a:b], w_sorted[a:b]
                lab = labels[nbr]
                uniq, inv = np.unique(lab, return_inverse=True)
                totals = np.bincount(inv, weights=wt)
                best = uniq[rng.permutation(len(uniq))[
                    np.argmax(totals[rng.permutation(len(uniq))])]] \
                    if len(uniq) > 1 else uniq[0]
                # The line above shuffles before argmax so equal-weight labels
                # are chosen between fairly rather than by array order.
                best = uniq[int(np.argmax(totals))] if len(uniq) == 1 else best
                if best != labels[v]:
                    labels[v] = best
                    changed += 1
            if changed == 0:
                break
        # Compact the label space so community ids are 0..k-1.
        _, compact = np.unique(labels, return_inverse=True)
        return compact

    def bridge_scores(self, communities: np.ndarray) -> np.ndarray:
        """Distinct communities among a node's neighbours, normalised."""
        order = np.argsort(self.src, kind="stable")
        s_sorted, d_sorted = self.src[order], self.dst[order]
        starts = np.searchsorted(s_sorted, np.arange(self.n))
        ends = np.searchsorted(s_sorted, np.arange(self.n), side="right")
        out = np.zeros(self.n)
        n_comms = max(int(communities.max()) + 1, 1) if self.n else 1
        for v in range(self.n):
            nbr = d_sorted[starts[v]:ends[v]]
            if nbr.size == 0:
                continue
            distinct = len(np.unique(communities[nbr]))
            out[v] = (distinct - 1) / max(n_comms - 1, 1)
        return out


def _community_rows(nodes, comms: np.ndarray, pr: np.ndarray,
                    bridge: np.ndarray) -> list[tuple]:
    """Per-community summary, computed from the arrays already in memory. Same
    figures the console's communities() used to aggregate from node_metrics:
    rounded the same way, so the API output does not change."""
    if comms.size == 0:
        return []
    k = int(comms.max()) + 1
    is_acct = np.fromiter((t == "account" for t, _ in nodes), bool, len(nodes))
    is_tag = np.fromiter((t == "tag" for t, _ in nodes), bool, len(nodes))
    members = np.bincount(comms, minlength=k)
    accts = np.bincount(comms, weights=is_acct, minlength=k)
    tags = np.bincount(comms, weights=is_tag, minlength=k)
    pr_sum = np.bincount(comms, weights=pr, minlength=k)
    max_bridge = np.full(k, -np.inf)
    np.maximum.at(max_bridge, comms, bridge)
    return [(c, int(members[c]), int(accts[c]), int(tags[c]),
             round(float(pr_sum[c] / members[c]), 6), round(float(max_bridge[c]), 4))
            for c in range(k) if members[c]]


def rebuild(verbose: bool = True, **kw) -> dict[str, object]:
    t = time.perf_counter()
    g = Graph(**kw)
    if g.n == 0:
        if verbose:
            print("  no edges — run `analyse edges` first")
        return {"nodes": 0, "edges": 0}

    deg, wdeg = g.degree(), g.weighted_degree()
    pr = g.pagerank()
    core = g.core_numbers()
    comms = g.communities()
    bridge = g.bridge_scores(comms)

    rows = [(t_, i_, int(deg[k]), float(wdeg[k]), float(pr[k]), int(core[k]),
             int(comms[k]), float(bridge[k]))
            for k, (t_, i_) in enumerate(g.nodes)]
    with engine.transaction():
        engine.execute("DELETE FROM edge_type_stats")
        engine.execute(
            """INSERT INTO edge_type_stats (edge_type, n, avg_weight, max_weight)
               SELECT edge_type, COUNT(*), ROUND(AVG(weight), 4), ROUND(MAX(weight), 4)
                 FROM edges GROUP BY edge_type""")
        engine.execute("DELETE FROM community_stats")
        engine.connect().executemany(
            """INSERT INTO community_stats (community, members, accounts, tags,
                                            avg_pagerank, max_bridge)
               VALUES (?,?,?,?,?,?)""", _community_rows(g.nodes, comms, pr, bridge))
        engine.execute("DELETE FROM node_metrics")
        engine.connect().executemany(
            """INSERT INTO node_metrics (node_type, node_id, degree, weighted_degree,
                                         pagerank, core_number, community, bridge_score)
               VALUES (?,?,?,?,?,?,?,?)""", rows)

    sizes = np.bincount(comms)
    out = {"nodes": g.n, "edges": g.m, "communities": int(comms.max()) + 1,
           "largest_community": int(sizes.max()) if sizes.size else 0,
           "max_core": int(core.max()) if core.size else 0,
           "elapsed": round(time.perf_counter() - t, 2)}
    if verbose:
        print(f"    {out['nodes']:,} nodes, {out['edges']:,} edges, "
              f"{out['communities']} communities "
              f"(largest {out['largest_community']}), max core {out['max_core']} "
              f"in {out['elapsed']}s")
    return out


def top(node_type: str, metric: str = "pagerank", limit: int = 15) -> list[dict]:
    col = {"pagerank": "pagerank", "degree": "degree", "core": "core_number",
           "bridge": "bridge_score"}.get(metric, "pagerank")
    label = {"account": "(SELECT handle FROM accounts WHERE id = m.node_id)",
             "tag": "(SELECT '#' || tag FROM tags WHERE id = m.node_id)",
             "sound": "(SELECT title FROM sounds WHERE id = m.node_id)",
             "item": "(SELECT substr(caption,1,50) FROM items WHERE id = m.node_id)"}.get(
        node_type, "m.node_id")
    return [dict(r) for r in engine.query(
        f"""SELECT m.node_id, {label} AS label, m.degree, m.pagerank, m.core_number,
                   m.community, m.bridge_score
              FROM node_metrics m WHERE m.node_type = ?
             ORDER BY {col} DESC LIMIT ?""", (node_type, limit))]

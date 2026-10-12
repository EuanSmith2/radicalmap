"""Username pattern recognition: finding the same operator behind several handles.

The lexicon already flags a bio that says "third account". This finds the other
two. Ban evasion is the normal state of these networks — an account is removed,
the operator returns under a near-identical handle, and to the platform those
are two unrelated users. To a researcher counting actors, treating them as two
is simply wrong.

Four signals, each weak alone and strong in combination:

  shared rare token   handles decompose into tokens (camelCase, digits,
                      separators). Two handles sharing a token that almost
                      nobody else uses is the strongest single signal here.
  version suffix      trailing v2, .v3, _alt, 2nd, backup, new — an operator
                      announcing the relationship themselves.
  numbered series     the same stem with different trailing digits.
  character shape     Jaccard over character trigrams, which catches the
                      deliberate near-misses that token splitting misses.

Everything is scored and clustered, never asserted: two accounts land in a
cluster with the evidence that put them there, for a human to accept or reject.
An attribution claim about a real person is not something a heuristic gets to
make on its own.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

from ..db import engine

SPLIT_RE = re.compile(r"[._\-]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
VERSION_RE = re.compile(
    r"(?:[._\-]?v\d+|[._\-]?\d+(?:nd|rd|th)?|[._\-]?(?:alt|alt\d*|new|back(?:up)?|"
    r"real|official|reborn|again|two|second|third))$", re.IGNORECASE)
NOISE_TOKENS = {"official", "real", "the", "its", "im", "tiktok", "tt", "yt", "xx"}

# Below this many accounts, rarity cannot be estimated and is not applied.
MIN_POPULATION = 20


def tokens(handle: str) -> list[str]:
    parts = [p.lower() for p in SPLIT_RE.split(handle or "") if p]
    return [p for p in parts if len(p) >= 3 and p not in NOISE_TOKENS and not p.isdigit()]


def stem(handle: str) -> str:
    """Handle with any version/number suffix removed."""
    h = (handle or "").strip().lower()
    prev = None
    while prev != h:
        prev = h
        h = VERSION_RE.sub("", h)
    return h.strip("._-")


def trigrams(s: str) -> set[str]:
    s = f"^{(s or '').lower()}$"
    return {s[i:i + 3] for i in range(max(len(s) - 2, 0))}


def shape_similarity(a: str, b: str) -> float:
    ta, tb = trigrams(a), trigrams(b)
    return len(ta & tb) / max(len(ta | tb), 1)


class Union:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def detect(min_score: float = 0.55, *, verbose: bool = True) -> dict[str, object]:
    accounts = [(int(r["id"]), r["handle"] or "") for r in engine.query(
        "SELECT id, handle FROM accounts WHERE handle IS NOT NULL AND handle != ''")]
    if len(accounts) < 2:
        return {"clusters": 0, "accounts": 0}

    n = len(accounts)
    token_index: dict[str, list[int]] = defaultdict(list)
    stem_index: dict[str, list[int]] = defaultdict(list)
    tok_of: dict[int, set[str]] = {}
    for aid, handle in accounts:
        t = set(tokens(handle))
        tok_of[aid] = t
        for tk in t:
            token_index[tk].append(aid)
        st = stem(handle)
        if len(st) >= 4:
            stem_index[st].append(aid)

    pairs: dict[tuple[int, int], tuple[float, list[str]]] = {}

    def add(a: int, b: int, score: float, why: str) -> None:
        key = (min(a, b), max(a, b))
        cur = pairs.get(key, (0.0, []))
        pairs[key] = (min(1.0, cur[0] + score), cur[1] + [why])

    by_id = dict(accounts)

    # ── candidate generation by trigram blocking ────────────────────────────
    #
    # Token splitting alone cannot see the concatenated case: "lanternmothv4"
    # and "lanternmothpostn" share the stem "lanternmoth", but there is no case,
    # digit or separator boundary to split on, so neither yields a shared token.
    # Trigram blocking finds them, and blocking keeps it out of O(n^2) — only
    # handles sharing rare trigrams are ever compared.
    tri_index: dict[str, list[int]] = defaultdict(list)
    tri_of: dict[int, set[str]] = {}
    for aid, handle in accounts:
        t = trigrams(handle)
        tri_of[aid] = t
        for g in t:
            tri_index[g].append(aid)

    candidates: set[tuple[int, int]] = set()
    ubiquitous = max(4, n // 3)
    for g, ids in tri_index.items():
        if len(ids) < 2 or len(ids) > ubiquitous:
            continue
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                candidates.add((min(a, b), max(a, b)))

    # How many accounts share each prefix length. A prefix that half the
    # population shares is a naming convention, not evidence: run against a
    # corpus where every handle was "user<N>", the unweighted rule claimed 178
    # of 180 accounts as alts of one another.
    prefix_counts: dict[str, int] = defaultdict(int)
    for _, handle in accounts:
        h = (handle or "").lower()
        for k in range(4, min(len(h), 16) + 1):
            prefix_counts[h[:k]] += 1

    # Same treatment for stems and for version suffixes themselves. Every
    # handle in a corpus named "user<N>" stems to "user" and ends in digits, so
    # both rules fire on every pair unless their population frequency is checked.
    stem_counts: dict[str, int] = defaultdict(int)
    versioned = 0
    for _, handle in accounts:
        st = stem(handle)
        if len(st) >= 4:
            stem_counts[st] += 1
        if VERSION_RE.search(handle or ""):
            versioned += 1
    version_is_convention = versioned > max(3, n * 0.4)

    def rarity_of(shared: int) -> float:
        """Fraction of the population NOT sharing the feature.

        Returns 1.0 below MIN_POPULATION. Rarity is a population statistic and
        needs a population: in a three-account corpus every shared feature is
        "2 of 3", which says nothing about whether it is a convention, and
        discounting by it suppressed a genuine 9-character shared prefix on a
        coined word.

        A log ratio was used first and is badly behaved on small populations:
        with three accounts, a prefix shared by two scored 0.37 — treating a
        genuine match as near-convention — because the denominator log1p(n-1)
        collapses. A plain fraction gives 0.5 there and still goes to 0 when
        the whole platform shares it, which is the property that matters.
        """
        if shared <= 1 or n < MIN_POPULATION:
            return 1.0
        return max(0.0, 1.0 - (shared - 1) / max(n - 1, 1))

    def stem_rarity(st: str) -> float:
        return rarity_of(stem_counts.get(st, 1))

    def prefix_rarity(prefix: str) -> float:
        return rarity_of(prefix_counts.get(prefix.lower(), 1))

    def common_prefix(a: str, b: str) -> int:
        k = 0
        for x, y in zip(a.lower(), b.lower()):
            if x != y:
                break
            k += 1
        return k

    for a, b in candidates:
        ha, hb = by_id[a], by_id[b]
        # 1. character shape
        shared_tris = tri_of[a] & tri_of[b]
        sim = len(shared_tris) / max(len(tri_of[a] | tri_of[b]), 1)
        # Weight by how rare the SHARED trigrams are, so two handles alike only
        # in the platform's common shape score nothing.
        if shared_tris:
            rare = 1.0 if n < MIN_POPULATION else sum(
                1.0 for g in shared_tris
                if len(tri_index[g]) <= max(3, n // 10)) / len(shared_tris)
        else:
            rare = 0.0
        if sim >= 0.40 and rare >= 0.3:
            add(a, b, 0.55 * sim * rare,
                f"handle shape similarity {sim:.2f} on {rare:.0%} rare trigrams")
        # 2. shared prefix — an operator rebuilding a handle usually keeps the front
        pre = common_prefix(ha, hb)
        if pre >= 6:
            rarity = prefix_rarity(ha[:pre])
            if rarity >= 0.25:
                add(a, b, min(0.5, 0.06 * pre) * rarity,
                    f"shared {pre}-character prefix {ha[:pre]!r} "
                    f"({prefix_counts[ha[:pre].lower()]} accounts, rarity {rarity:.2f})")
        # 3. same stem once a version suffix is stripped
        sa = stem(ha)
        if len(sa) >= 4 and sa == stem(hb):
            r = stem_rarity(sa)
            if r >= 0.25:
                add(a, b, 0.5 * r,
                    f"identical stem {sa!r} after removing a version suffix "
                    f"({stem_counts[sa]} accounts, rarity {r:.2f})")
        # 4. explicit version suffix — worthless if the whole platform's handles
        #    carry one, which is the case whenever handles are auto-numbered
        if not version_is_convention:
            for side in (ha, hb):
                if VERSION_RE.search(side):
                    add(a, b, 0.12, f"@{side} carries a version suffix")
                    break

    # 5. shared rare token, where the handles DO decompose
    for tk, ids in token_index.items():
        if len(ids) < 2 or len(ids) > max(3, n // 8):
            continue
        rarity = 1.0 - (len(ids) - 2) / max(n, 1)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                add(a, b, 0.5 * rarity, f"shared rare token {tk!r} ({len(ids)} accounts)")

    uf = Union()
    kept = {k: v for k, v in pairs.items() if v[0] >= min_score}
    for (a, b) in kept:
        uf.union(a, b)

    clusters: dict[int, list[int]] = defaultdict(list)
    for a, b in kept:
        clusters[uf.find(a)].append(a)
        clusters[uf.find(b)].append(b)

    rows = []
    for cid, members in clusters.items():
        for aid in set(members):
            ev = "; ".join(sorted({w for (x, y), (_, ws) in kept.items()
                                   if aid in (x, y) for w in ws}))[:400]
            sc = max(v[0] for k, v in kept.items() if aid in k)
            rows.append((cid, aid, sc, ev))

    with engine.transaction():
        engine.execute("DELETE FROM account_clusters")
        engine.connect().executemany(
            "INSERT INTO account_clusters (cluster_id, account_id, similarity, evidence) "
            "VALUES (?,?,?,?)", rows)
        # An alt_of edge so propagation can traverse the relationship.
        engine.execute("DELETE FROM edges WHERE edge_type = 'alt_of'")
        engine.connect().executemany(
            """INSERT INTO edges (src_type, src_id, dst_type, dst_id, edge_type, weight)
               VALUES ('account',?,'account',?,'alt_of',?)
               ON CONFLICT DO NOTHING""",
            [(a, b, v[0]) for (a, b), v in kept.items()])

    out = {"clusters": len(clusters), "accounts": len({a for m in clusters.values() for a in m}),
           "pairs": len(kept)}
    if verbose:
        print(f"    {out['clusters']} cluster(s) covering {out['accounts']} accounts "
              f"from {out['pairs']} scored pairs")
    return out


def clusters(limit: int = 20) -> list[dict]:
    rows = engine.query(
        """SELECT c.cluster_id, a.handle, c.similarity, c.evidence
             FROM account_clusters c JOIN accounts a ON a.id = c.account_id
            ORDER BY c.cluster_id, c.similarity DESC LIMIT ?""", (limit * 6,))
    grouped: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[int(r["cluster_id"])].append(
            {"handle": r["handle"], "similarity": round(float(r["similarity"] or 0), 2),
             "evidence": r["evidence"]})
    return [{"cluster_id": k, "members": v} for k, v in list(grouped.items())[:limit]]

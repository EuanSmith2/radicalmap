"""Choosing what a human should label next.

Annotation is the scarce resource. A random sample of 300 comments from this
corpus is mostly ordinary content that the model already handles, so most of
that afternoon buys nothing. The whole value of active learning is spending
those same 300 labels where they change the model.

Four strategies, blended rather than chosen, because each is blind in a
different way:

  uncertainty   items nearest the decision boundary. The classic. Blind to
                labels the model has never seen — it cannot be uncertain about
                a class it has no concept of.
  disagreement  the lexicon says one thing and the model says another. Cheap,
                and it points precisely at where curated knowledge and learned
                knowledge diverge — which is where one of them is wrong.
  coverage      deliberately over-sample labels with too little support to
                report a metric. Directly attacks the "withheld: low support"
                lines in the evaluation.
  density       prefer items in dense regions of the corpus, so a label buys
                generalisation rather than covering one oddity.

Then a diversity filter, because uncertainty sampling alone will happily hand
back fifty near-identical comments.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from ..classify import lexicon as L
from ..db import engine
from .features import build, extract
from .model import Model


@dataclass
class Pick:
    target_type: str
    target_id: int
    text: str
    score: float
    reason: str


def pool(limit: int = 20000, exclude_annotated: bool = True) -> list[tuple[str, int, str]]:
    """Unlabelled candidates, across every target type."""
    done: set[tuple[str, int]] = set()
    if exclude_annotated:
        done = {(r["target_type"], int(r["target_id"])) for r in engine.query(
            "SELECT DISTINCT target_type, target_id FROM gold_annotations")}
    out: list[tuple[str, int, str]] = []
    for ttype, table, col in (("comment", "comments", "text"),
                              ("item", "items", "caption"),
                              ("account", "accounts", "bio"),
                              ("tag", "tags", "tag")):
        for r in engine.query(f"SELECT id, {col} AS t FROM {table} LIMIT ?", (limit,)):
            key = (ttype, int(r["id"]))
            if key in done or not (r["t"] or "").strip():
                continue
            out.append((ttype, int(r["id"]), r["t"]))
    return out


def uncertainty(P: dict[str, np.ndarray]) -> np.ndarray:
    """Mean distance from the decision boundary, across heads. Lower is more
    uncertain, so this returns 1 - that, to keep 'higher = select me'."""
    parts = []
    for probs in P.values():
        if probs.size:
            parts.append(np.abs(probs - 0.5).min(axis=1))
    if not parts:
        return np.zeros(0)
    return 1.0 - 2.0 * np.minimum.reduce(parts)


def disagreement(P: dict[str, np.ndarray], labels: dict[str, list[str]],
                 lex_domains: list[set[str]], lex_harms: list[set[str]]) -> np.ndarray:
    """Where the lexicon and the model part company."""
    n = len(lex_domains)
    out = np.zeros(n)
    for head, field in (("domain", lex_domains), ("harm", lex_harms)):
        probs = P.get(head)
        if probs is None or not probs.size:
            continue
        idx = {lab: k for k, lab in enumerate(labels[head])}
        for i in range(n):
            lex_set = {f for f in field[i] if f in idx}
            model_set = {labels[head][k] for k in np.where(probs[i] >= 0.5)[0]}
            if not lex_set and not model_set:
                continue
            union = lex_set | model_set
            out[i] += len(union - (lex_set & model_set)) / max(len(union), 1)
    return out / 2.0


def coverage(P: dict[str, np.ndarray], labels: dict[str, list[str]],
             support: dict[str, dict[str, int]], target: int = 8) -> np.ndarray:
    """Favour items the model thinks belong to under-supported labels."""
    n = next((p.shape[0] for p in P.values() if p.size), 0)
    out = np.zeros(n)
    for head, probs in P.items():
        if not probs.size:
            continue
        need = np.array([max(0, target - support.get(head, {}).get(lab, 0)) / target
                         for lab in labels[head]])
        out += (probs * need).max(axis=1)
    return out / max(len(P), 1)


def density(X, sample: int = 400, seed: int = 1848) -> np.ndarray:
    """Average cosine similarity to a random subsample — a cheap stand-in for
    how typical an item is of the corpus."""
    n = X.n_rows
    if n == 0:
        return np.zeros(0)
    rng = np.random.default_rng(seed)
    idx = rng.choice(n, size=min(sample, n), replace=False)
    dense = np.zeros((len(idx), X.dim))
    rows = X.rows
    for j, i in enumerate(idx):
        m = rows == i
        dense[j, X.indices[m]] = X.data[m]
    W = dense.T                                     # dim x sample
    sims = X.dot(W)
    return sims.mean(axis=1)


def _vectors(X, keep: list[int]) -> dict[int, dict[int, float]]:
    rows = X.rows
    out: dict[int, dict[int, float]] = {}
    want = set(keep)
    for i in want:
        m = rows == i
        out[i] = dict(zip(X.indices[m].tolist(), X.data[m].tolist()))
    return out


def _cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    num = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = np.sqrt(sum(v * v for v in a.values())) or 1.0
    nb = np.sqrt(sum(v * v for v in b.values())) or 1.0
    return float(num / (na * nb))


def select(model: Model | None, items: list[tuple[str, int, str]], n: int = 50, *,
           weights: dict[str, float] | None = None,
           support: dict[str, dict[str, int]] | None = None,
           max_similarity: float = 0.8) -> list[Pick]:
    """Blend the strategies, then drop near-duplicates."""
    if not items:
        return []
    weights = weights or {"uncertainty": 0.4, "disagreement": 0.3,
                          "coverage": 0.2, "density": 0.1}
    texts = [t for _, _, t in items]
    scans = [L.scan(t) for t in texts]
    matches = [s.matches for s in scans]
    X = build([extract(t, m) for t, m in zip(texts, matches)])

    components: dict[str, np.ndarray] = {"density": density(X)}
    if model is not None and model.heads:
        P = model.predict(texts, matches)
        labels = {h: head.labels for h, head in model.heads.items()}
        components["uncertainty"] = uncertainty(P)
        components["disagreement"] = disagreement(
            P, labels,
            [{m.domain for m in ms} for ms in matches],
            [{m.harm for m in ms} for ms in matches])
        components["coverage"] = coverage(P, labels, support or {})
    else:
        # Cold start: no model yet. Fall back to lexicon signal + density, which
        # is still far better than random — it puts the first annotations where
        # the lexicon is already firing and therefore where the labels matter.
        components["uncertainty"] = np.array(
            [min(len(s.matches), 4) / 4.0 for s in scans])
        components["disagreement"] = np.zeros(len(items))
        components["coverage"] = np.zeros(len(items))

    total = np.zeros(len(items))
    for name, w in weights.items():
        comp = components.get(name)
        if comp is None or not comp.size:
            continue
        rng = comp.max() - comp.min()
        total += w * ((comp - comp.min()) / rng if rng > 1e-12 else comp * 0)

    order = np.argsort(-total)
    chosen: list[Pick] = []
    vecs = _vectors(X, order[: min(len(order), n * 12)].tolist())
    seen_hash: set[str] = set()
    for i in order:
        if len(chosen) >= n:
            break
        i = int(i)
        h = hashlib.blake2b(texts[i].strip().lower().encode(), digest_size=8).hexdigest()
        if h in seen_hash:
            continue
        v = vecs.get(i)
        if v is not None and any(_cosine(v, vecs[j]) > max_similarity
                                 for j in [c_i for c_i, _ in _picked(chosen, items)]
                                 if j in vecs):
            continue
        seen_hash.add(h)
        top = max(("uncertainty", "disagreement", "coverage", "density"),
                  key=lambda k: weights.get(k, 0) * float(components.get(k, np.zeros(1))[i])
                  if components.get(k) is not None and components[k].size else 0)
        ttype, tid, text = items[i]
        chosen.append(Pick(ttype, tid, text, float(total[i]), top))
    return chosen


def _picked(chosen: list[Pick], items: list[tuple[str, int, str]]):
    index = {(t, i): n for n, (t, i, _) in enumerate(items)}
    return [(index[(p.target_type, p.target_id)], p) for p in chosen
            if (p.target_type, p.target_id) in index]


def current_support() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {"domain": {}, "harm": {}, "stance": {}}
    for head, col in (("domain", "domain"), ("harm", "harm_type"), ("stance", "stance")):
        for r in engine.query(
                f"SELECT {col} AS v, count(*) n FROM gold_annotations "
                f"WHERE is_positive = 1 AND {col} IS NOT NULL GROUP BY 1"):
            out[head][r["v"]] = int(r["n"])
    return out

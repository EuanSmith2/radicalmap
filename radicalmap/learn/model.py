"""A small, interpretable, multi-head classifier.

Deliberately linear. A gradient-boosted or neural model would score better on a
benchmark and would be unusable here: a finding has to be justifiable, a
reviewer has to be able to ask why, and the model's own parameters are one of
the two mechanisms this project uses to propose new lexicon terms. A linear
model over interpretable features gives all three — every prediction decomposes
into "these features contributed this much", which is a rationale you can put in
a UI and defend in a paper.

Three heads, matching the taxonomy:

    domain    multi-label — content can be ethnonationalist AND antisemitic
    harm      multi-label — content can dehumanise AND incite
    stance    single-label — one relation to the material at a time

Each head is one-vs-rest logistic regression with class weighting, because in
this corpus almost everything is negative for almost every label and an
unweighted model learns to predict "no" and score 99% accurate.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .features import DIM, FeatureSpec, Matrix, build, extract


def sigmoid(z: np.ndarray) -> np.ndarray:
    out = np.empty_like(z)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


@dataclass
class Head:
    name: str
    labels: list[str]
    W: np.ndarray                      # dim x K
    b: np.ndarray                      # K
    temperature: float = 1.0           # >1 softens overconfident scores
    calibrated: bool = False

    def scores(self, X: Matrix) -> np.ndarray:
        return X.dot(self.W) + self.b

    def proba(self, X: Matrix) -> np.ndarray:
        return sigmoid(self.scores(X) / max(self.temperature, 1e-6))


@dataclass
class Model:
    spec: FeatureSpec
    heads: dict[str, Head] = field(default_factory=dict)
    version: str = ""
    n_train: int = 0
    train_source: str = "gold"
    metrics: dict = field(default_factory=dict)

    # ── training ─────────────────────────────────────────────────────────────

    @staticmethod
    def _fit_head(name: str, X: Matrix, Y: np.ndarray, labels: list[str], *,
                  l2: float = 1e-3, epochs: int = 300, lr: float = 0.5,
                  class_weight: bool = True, tol: float = 1e-5,
                  patience: int = 15) -> Head:
        n, K = Y.shape
        W = np.zeros((X.dim, K))
        b = np.zeros(K)

        # Inverse-frequency positive weighting, capped so a label seen twice in
        # three hundred examples does not dominate the objective entirely.
        if class_weight:
            pos = Y.sum(axis=0)
            pw = np.where(pos > 0, np.minimum((n - pos) / np.maximum(pos, 1), 20.0), 1.0)
        else:
            pw = np.ones(K)

        mW = np.zeros_like(W); vW = np.zeros_like(W)
        mb = np.zeros_like(b); vb = np.zeros_like(b)
        beta1, beta2, eps = 0.9, 0.999, 1e-8

        # Early stopping. A fixed epoch budget is wasted on small label sets that
        # converge in fifty steps, and the learning curve fits over a hundred
        # models — the difference is minutes versus seconds, for the same answer.
        best, stale = np.inf, 0
        for t in range(1, epochs + 1):
            Z = X.dot(W) + b
            P = sigmoid(Z)
            E = (P - Y) * np.where(Y > 0, pw, 1.0)
            gW = X.rdot(E) / n + l2 * W
            gb = E.mean(axis=0)

            for g, m, v, prm in ((gW, mW, vW, W), (gb, mb, vb, b)):
                m *= beta1; m += (1 - beta1) * g
                v *= beta2; v += (1 - beta2) * (g * g)
                prm -= lr * (m / (1 - beta1 ** t)) / (np.sqrt(v / (1 - beta2 ** t)) + eps)

            if t % 5 == 0:
                Pc = np.clip(P, 1e-9, 1 - 1e-9)
                loss = float(-(Y * np.log(Pc) + (1 - Y) * np.log(1 - Pc)).mean()
                             + 0.5 * l2 * float((W * W).sum()))
                if best - loss < tol:
                    stale += 5
                    if stale >= patience:
                        break
                else:
                    stale = 0
                best = min(best, loss)
        return Head(name, labels, W, b)

    @classmethod
    def train(cls, texts: list[str], match_lists: list[list],
              targets: dict[str, tuple[list[str], np.ndarray]], *,
              spec: FeatureSpec = FeatureSpec(), train_source: str = "gold",
              **kw) -> "Model":
        X = build([extract(t, m, spec) for t, m in zip(texts, match_lists)], spec.dim)
        model = cls(spec=spec, n_train=len(texts), train_source=train_source,
                    version=f"m{int(time.time())}")
        for head_name, (labels, Y) in targets.items():
            if Y.size and Y.shape[1]:
                model.heads[head_name] = cls._fit_head(head_name, X, Y, labels, **kw)
        return model

    # ── inference ────────────────────────────────────────────────────────────

    def matrix(self, texts: list[str], match_lists: list[list]) -> Matrix:
        return build([extract(t, m, self.spec) for t, m in zip(texts, match_lists)],
                     self.spec.dim)

    def predict(self, texts: list[str], match_lists: list[list]) -> dict[str, np.ndarray]:
        X = self.matrix(texts, match_lists)
        return {name: head.proba(X) for name, head in self.heads.items()}

    def calibrate(self, texts: list[str], match_lists: list[list],
                  targets: dict[str, np.ndarray]) -> None:
        """Temperature scaling on held-out data.

        Logistic regression on a few hundred examples is badly overconfident,
        and an uncalibrated 0.99 reported to a researcher as "confidence" is a
        lie. One parameter per head, fitted by minimising held-out log loss.
        """
        X = self.matrix(texts, match_lists)
        for name, head in self.heads.items():
            Y = targets.get(name)
            if Y is None or not Y.size:
                continue
            Z = head.scores(X)
            best_t, best_loss = 1.0, float("inf")
            for T in np.linspace(0.5, 6.0, 45):
                P = np.clip(sigmoid(Z / T), 1e-9, 1 - 1e-9)
                loss = -(Y * np.log(P) + (1 - Y) * np.log(1 - P)).mean()
                if loss < best_loss:
                    best_t, best_loss = float(T), float(loss)
            head.temperature = best_t
            head.calibrated = True

    # ── interpretability ─────────────────────────────────────────────────────

    def explain(self, text: str, matches: list, head: str, label: str,
                top: int = 8) -> list[tuple[str, float]]:
        """Which features drove this prediction, and by how much.

        Hashing is one-way, so contributions are named by re-deriving the same
        token stream from the text and hashing it again, rather than by trying
        to invert the bucket.
        """
        h = self.heads.get(head)
        if h is None or label not in h.labels:
            return []
        k = h.labels.index(label)
        feats = extract(text, matches, self.spec)
        norm = np.linalg.norm(np.fromiter(feats.values(), dtype=float, count=len(feats))) or 1.0

        # Re-derive the token stream so each contribution can be named rather
        # than reported as a hash bucket.
        from .features import _h
        contrib: list[tuple[str, float]] = []
        for token in _tokens_of(text, matches, self.spec):
            idx = _h(token, self.spec.dim)
            if idx in feats:
                contrib.append((token, float(h.W[idx, k] * feats[idx] / norm)))
        agg: dict[str, float] = {}
        for token, c in contrib:
            agg[token] = agg.get(token, 0.0) + c
        return sorted(agg.items(), key=lambda kv: -abs(kv[1]))[:top]

    def top_features(self, head: str, label: str, top: int = 25) -> list[tuple[int, float]]:
        """Highest-weight buckets for a label — the raw material for proposing
        new lexicon terms from what the model learned."""
        h = self.heads.get(head)
        if h is None or label not in h.labels:
            return []
        k = h.labels.index(label)
        order = np.argsort(-h.W[:, k])[:top]
        return [(int(i), float(h.W[i, k])) for i in order if h.W[i, k] > 0]

    # ── persistence ──────────────────────────────────────────────────────────

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            meta=json.dumps({"spec": self.spec.__dict__, "version": self.version,
                             "n_train": self.n_train, "train_source": self.train_source,
                             "metrics": self.metrics,
                             "heads": {n: {"labels": h.labels, "temperature": h.temperature,
                                           "calibrated": h.calibrated}
                                       for n, h in self.heads.items()}}),
            **{f"W_{n}": h.W for n, h in self.heads.items()},
            **{f"b_{n}": h.b for n, h in self.heads.items()})
        return path

    @classmethod
    def load(cls, path: Path) -> "Model":
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        spec = FeatureSpec(**{k: tuple(v) if isinstance(v, list) else v
                              for k, v in meta["spec"].items()})
        model = cls(spec=spec, version=meta["version"], n_train=meta["n_train"],
                    train_source=meta["train_source"], metrics=meta.get("metrics", {}))
        for name, hm in meta["heads"].items():
            model.heads[name] = Head(name, hm["labels"], z[f"W_{name}"], z[f"b_{name}"],
                                     hm["temperature"], hm["calibrated"])
        return model


def _tokens_of(text: str, matches: list, spec: FeatureSpec) -> list[str]:
    """The token stream extract() hashes, reproduced so contributions can be
    named. Kept beside extract() in spirit; if one changes the other must."""
    from .features import WORD_RE, normalise
    words = WORD_RE.findall(normalise(text or ""))
    out: list[str] = []
    for n in spec.word_ngrams:
        out += ["w:" + " ".join(words[i:i + n]) for i in range(len(words) - n + 1)]
    for word in words:
        padded = f"^{word}$"
        for n in spec.char_ngrams:
            out += ["c:" + padded[i:i + n] for i in range(len(padded) - n + 1)]
    for m in matches or []:
        out += [f"lex:{m.slug}", f"tier:{m.tier}",
                f"dom:{getattr(m, 'domain', 'none')}", f"harm:{getattr(m, 'harm', 'none')}"]
    return out

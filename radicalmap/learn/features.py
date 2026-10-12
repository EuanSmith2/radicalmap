"""Feature extraction.

Character n-grams are the reason this module exists, and they are not a detail.
The entire game in this corpus is evasion: v1's lexicon was defeated by "sik
haj" for Sieg Heil, by "bazed" for based, by "sewerslide" for suicide. Every one
of those required a human to notice the variant and write a new pattern.

A character n-gram model generalises past the literal string on its own. Trained
on "sieg heil" it carries weight on "ieg", "eil", "g he" — so "zieg hiel",
"s13g heil" and "sieg  heil" all activate without anyone adding a rule. That is
the concrete sense in which the system learns: it covers the space around the
patterns, not just the patterns.

Three feature families, hashed into one space so there is no vocabulary to
persist and an unseen word at inference time is not a problem:

    word    unigrams and bigrams      topical content
    char    3-5 grams inside words    evasion, misspelling, non-English
    lex     which lexicon entries fired, by tier and domain
            — injects the curated knowledge so the model starts from it
              rather than having to rediscover it from a few hundred examples
    struct  length, caps ratio, digits, emoji, url presence
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

import numpy as np

DIM = 1 << 15                    # 32,768 hashed buckets
WORD_RE = re.compile(r"[#@]?[\w'À-ɏЀ-ӿ؀-ۿ]+", re.UNICODE)
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")

# Digit- and symbol-for-letter substitutions, folded so the model does not have
# to learn each one separately from a handful of examples.
LEET = str.maketrans({"3": "e", "1": "i", "0": "o", "4": "a",
                      "5": "s", "7": "t", "@": "a", "$": "s"})


@dataclass(frozen=True)
class FeatureSpec:
    dim: int = DIM
    word_ngrams: tuple[int, ...] = (1, 2)
    # 2-grams included deliberately. Measured on held-out evasion variants of a
    # phrase the model was trained on: 3-5 gets 7/9, 2-5 gets 8/9. The extra
    # short grams are what carry a transposition like "seig heil".
    char_ngrams: tuple[int, ...] = (2, 3, 4, 5)
    use_lexicon: bool = True
    use_struct: bool = True

    def describe(self) -> str:
        return (f"hashed{self.dim}:w{''.join(map(str, self.word_ngrams))}"
                f":c{''.join(map(str, self.char_ngrams))}"
                f":lex{int(self.use_lexicon)}:struct{int(self.use_struct)}")


def _h(token: str, dim: int) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(),
                          "little") % dim


def normalise(text: str) -> str:
    """Fold case and accents, and collapse digit-for-letter substitutions.

    Leetspeak is a deliberate evasion channel — "j3ws", "n1gger", "s13g" — and
    folding it here means the model does not have to spend capacity learning
    each substitution separately from a handful of examples.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    return text.translate(LEET)


def extract(text: str, matches: list | None = None,
            spec: FeatureSpec = FeatureSpec()) -> dict[int, float]:
    """Sparse feature dict for one document."""
    feats: dict[int, float] = {}
    raw = text or ""
    norm = normalise(raw)
    words = WORD_RE.findall(norm)

    def bump(token: str, weight: float = 1.0) -> None:
        idx = _h(token, spec.dim)
        feats[idx] = feats.get(idx, 0.0) + weight

    for n in spec.word_ngrams:
        for i in range(len(words) - n + 1):
            bump("w:" + " ".join(words[i:i + n]))

    # Char n-grams are taken INSIDE word boundaries (padded), not across the
    # whole string: cross-word grams mostly encode word order, which the word
    # bigrams already cover, and they triple the feature count for little gain.
    for word in words:
        padded = f"^{word}$"
        for n in spec.char_ngrams:
            for i in range(len(padded) - n + 1):
                bump("c:" + padded[i:i + n], 0.5)

    if spec.use_lexicon and matches:
        for m in matches:
            bump(f"lex:{m.slug}", 2.0)
            bump(f"tier:{m.tier}", 1.0)
            bump(f"dom:{getattr(m, 'domain', 'none')}", 1.5)
            bump(f"harm:{getattr(m, 'harm', 'none')}", 1.5)

    if spec.use_struct:
        n_chars = max(len(raw), 1)
        bump("s:len", min(len(raw) / 280.0, 4.0))
        bump("s:caps", sum(c.isupper() for c in raw) / n_chars)
        bump("s:digit", sum(c.isdigit() for c in raw) / n_chars)
        bump("s:emoji", min(len(EMOJI_RE.findall(raw)) / 5.0, 2.0))
        if "http" in raw or "t.me" in raw:
            bump("s:url", 1.0)
    return feats


@dataclass
class Matrix:
    """Minimal CSR. scipy is not installed and is not worth a dependency for
    the three operations this needs."""
    indptr: np.ndarray
    indices: np.ndarray
    data: np.ndarray
    n_rows: int
    dim: int

    _rows_cache: np.ndarray | None = None

    @property
    def rows(self) -> np.ndarray:
        if self._rows_cache is None:
            self._rows_cache = np.repeat(np.arange(self.n_rows), np.diff(self.indptr))
        return self._rows_cache

    # Both products scatter-add over all K classes in ONE bincount by flattening
    # (row, class) into a single index. The first version looped over classes in
    # Python, which put ~40 bincounts inside a 300-epoch training loop and made
    # a 5-point learning curve take minutes instead of seconds.

    def dot(self, W: np.ndarray) -> np.ndarray:
        """(n_rows x dim) @ (dim x K) without materialising the dense matrix."""
        K = W.shape[1]
        if self.indices.size == 0:
            return np.zeros((self.n_rows, K))
        contrib = self.data[:, None] * W[self.indices]          # nnz x K
        flat = (self.rows[:, None] * K + np.arange(K)).ravel()
        return np.bincount(flat, weights=contrib.ravel(),
                           minlength=self.n_rows * K).reshape(self.n_rows, K)

    def rdot(self, E: np.ndarray) -> np.ndarray:
        """X.T @ E — the gradient direction."""
        K = E.shape[1]
        if self.indices.size == 0:
            return np.zeros((self.dim, K))
        contrib = self.data[:, None] * E[self.rows]             # nnz x K
        flat = (self.indices[:, None] * K + np.arange(K)).ravel()
        return np.bincount(flat, weights=contrib.ravel(),
                           minlength=self.dim * K).reshape(self.dim, K)


def build(docs: list[dict[int, float]], dim: int = DIM) -> Matrix:
    indptr = np.zeros(len(docs) + 1, dtype=np.int64)
    indices, data = [], []
    for i, d in enumerate(docs):
        indptr[i + 1] = indptr[i] + len(d)
        if d:
            ks = np.fromiter(d.keys(), dtype=np.int64, count=len(d))
            vs = np.fromiter(d.values(), dtype=np.float64, count=len(d))
            # L2-normalise each document so a long comment does not simply
            # outweigh a short one.
            norm = np.linalg.norm(vs) or 1.0
            indices.append(ks)
            data.append(vs / norm)
    return Matrix(indptr,
                  np.concatenate(indices) if indices else np.zeros(0, dtype=np.int64),
                  np.concatenate(data) if data else np.zeros(0),
                  len(docs), dim)

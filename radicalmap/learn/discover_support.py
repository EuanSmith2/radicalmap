"""Corpus statistics shared by the two term-proposal mechanisms.

Both the keyness route (classify/discover.py) and the model-weight route
(learn/loop.py) need to know how often a term occurs and how it is distributed
across labels. Computing it once, here, keeps the two consistent — a term
proposed by both routes should be the same term.
"""

from __future__ import annotations

import re

from ..classify.discover import STOP as _KEYNESS_STOP
from ..db import engine

# Function words plus the conversational filler that dominates short-form
# comments. A term made only of these is never a lexicon candidate.
STOPWORDS = set(_KEYNESS_STOP) | set("""
she he they them his her its our your their you your mine ours theirs this that
these those here there what which who whom whose when where why how and but or
nor for yet so if then than because while although though since unless until
about above across after against along among around before behind below beneath
beside between beyond during except inside into near off onto outside over past
through throughout under underneath until upon within without again also just
very much many more most some any all both each few other another such own same
""".split())

TOKEN_RE = re.compile(r"[\w'\u00c0-\u024f]{3,}", re.UNICODE)


def corpus_stats() -> tuple[dict, dict]:
    """Document frequency overall, and per gold label.

    Returns (df, label_df) where df[term] is the number of documents containing
    it, df["__total__"] the document count, label_df[(label, term)] the count
    within that label's documents and label_df[(label, "__n__")] that label's
    document count.
    """
    labels_of: dict[tuple[str, int], set[str]] = {}
    for r in engine.query(
            "SELECT target_type, target_id, domain, harm_type FROM gold_annotations "
            "WHERE is_positive = 1"):
        key = (r["target_type"], int(r["target_id"]))
        s = labels_of.setdefault(key, set())
        if r["domain"]:
            s.add(r["domain"])
        if r["harm_type"]:
            s.add(r["harm_type"])

    df: dict[str, int] = {}
    label_df: dict[tuple[str, str], int] = {}
    total = 0
    for ttype, table, col in (("comment", "comments", "text"),
                              ("item", "items", "caption"),
                              ("account", "accounts", "bio"),
                              ("tag", "tags", "tag")):
        for r in engine.query(f"SELECT id, {col} AS t FROM {table}"):
            text = (r["t"] or "").lower()
            if not text.strip():
                continue
            total += 1
            words = TOKEN_RE.findall(text)
            terms = set(words)
            terms |= {f"{a} {b}" for a, b in zip(words, words[1:])}
            for term in terms:
                df[term] = df.get(term, 0) + 1
            for label in labels_of.get((ttype, int(r["id"])), ()):
                label_df[(label, "__n__")] = label_df.get((label, "__n__"), 0) + 1
                for term in terms:
                    label_df[(label, term)] = label_df.get((label, term), 0) + 1
    df["__total__"] = total
    return df, label_df

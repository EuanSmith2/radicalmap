"""Finding coded terms the lexicon does not yet contain.

At 100k items the dictionary cannot be maintained by someone noticing a new
word. This compares vocabulary in adjudicated-positive text against the rest of
the corpus and ranks by log-likelihood keyness — the standard corpus-linguistics
measure for "this word is unusually characteristic of this subcorpus".

It proposes; a human disposes. Nothing here is ever promoted automatically,
because a term that merely correlates with flagged content is not yet evidence
of anything — and a lexicon that grows itself would launder the classifier's
own errors into its rules.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from ..db import engine
from . import lexicon as L, runs

TOKEN_RE = re.compile(r"[#@]?[\wÀ-ɏЀ-ӿ']{3,}", re.UNICODE)

# Function words carry no signal and would dominate any keyness ranking.
STOP = set("""the a an and or but if then than that this these those there here
what which who whom whose when where why how all any both each few more most
other some such not only own same too very can will just should now been have
has had was were are is am be being do does did doing for with about against
between into through during before after above below from up down out off over
under again further once you your yours our ours their theirs his her hers its
they them she him his mine yours ours i me my we us it he like get got one two
really actually literally people think know see said say says going want need
because also even still back way make made time day year good bad thing things
""".split())


def _tokens(text: str) -> list[str]:
    return [t for t in (m.group(0).lower() for m in TOKEN_RE.finditer(text or ""))
            if t not in STOP and not t.isdigit()]


def _log_likelihood(a: int, b: int, c: int, d: int) -> float:
    """Dunning's G2 for a term appearing `a` times in a corpus of `c` tokens vs
    `b` times in a reference corpus of `d` tokens."""
    if a == 0 or c == 0 or d == 0:
        return 0.0
    e1 = c * (a + b) / (c + d)
    e2 = d * (a + b) / (c + d)
    g = 0.0
    if a > 0 and e1 > 0:
        g += a * math.log(a / e1)
    if b > 0 and e2 > 0:
        g += b * math.log(b / e2)
    return 2 * g


def discover(verdict_run: int | None = None, *, min_count: int = 3,
             top: int = 40, verbose: bool = True) -> list[dict[str, object]]:
    verdict_run = verdict_run or runs.latest("llm")
    if verdict_run is None:
        raise RuntimeError("no adjudication run — run `classify adjudicate` first")

    positive_ids = {(r["target_type"], int(r["target_id"])) for r in engine.query(
        "SELECT DISTINCT target_type, target_id FROM classifications "
        "WHERE run_id = ? AND verdict = 'confirmed'", (verdict_run,))}
    if not positive_ids:
        if verbose:
            print("  no confirmed positives to learn from yet")
        return []

    pos, bg = Counter(), Counter()
    for ttype, table, col in (("comment", "comments", "text"),
                              ("item", "items", "caption"),
                              ("account", "accounts", "bio")):
        for r in engine.query(f"SELECT id, {col} AS t FROM {table}"):
            toks = _tokens(r["t"] or "")
            target = pos if (ttype, int(r["id"])) in positive_ids else bg
            target.update(set(toks))     # document frequency, not raw count

    pos_total, bg_total = sum(pos.values()) or 1, sum(bg.values()) or 1
    known = {e.slug for e in L.ALL_ENTRIES}
    known_words = set()
    for e in L.ALL_ENTRIES:
        known_words |= {w.lower() for w in re.findall(r"[a-zA-Z]{4,}", e.pattern)}

    scored = []
    for term, a in pos.items():
        if a < min_count or term in known_words:
            continue
        b = bg.get(term, 0)
        g = _log_likelihood(a, b, pos_total, bg_total)
        # Keyness must be in the POSITIVE direction: a term can be statistically
        # distinctive by being rare in the flagged set, which is not what we want.
        if (a / pos_total) <= (b / bg_total):
            continue
        scored.append({"term": term, "positive_count": a, "background_count": b,
                       "log_likelihood": round(g, 2),
                       "ratio": round((a / pos_total) / max(b / bg_total, 1e-9), 2)})

    scored.sort(key=lambda d: -d["log_likelihood"])
    scored = scored[:top]

    if scored:
        with engine.transaction():
            engine.connect().executemany(
                """INSERT INTO term_candidates
                       (term, run_id, positive_count, background_count, log_likelihood, ratio)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT (term, run_id) DO UPDATE SET
                       positive_count=excluded.positive_count,
                       background_count=excluded.background_count,
                       log_likelihood=excluded.log_likelihood, ratio=excluded.ratio""",
                [(d["term"], verdict_run, d["positive_count"], d["background_count"],
                  d["log_likelihood"], d["ratio"]) for d in scored])
    if verbose:
        print(f"  {len(scored)} term(s) proposed from {len(positive_ids)} confirmed target(s)")
    return scored

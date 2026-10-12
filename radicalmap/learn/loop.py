"""One round of the learning loop.

    score the pool  ->  select what to label  ->  a human labels it
         ^                                                  |
         |                          retrain, measure  <------+

Each round is a row in active_rounds with its strategy, its selections, and the
metrics that resulted — so the loop's own history is data, and "did round 4
actually help?" is a query rather than a memory.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .. import taxonomy as T
from ..db import engine
from . import active, train
from .model import Model

WEIGHTS_DIR = Path(__file__).resolve().parent.parent.parent / "db" / "models"


def latest_model() -> tuple[Model | None, int | None]:
    row = engine.query_one(
        "SELECT id, weights_path FROM model_versions ORDER BY trained_at DESC LIMIT 1")
    if not row or not row["weights_path"]:
        return None, None
    path = Path(row["weights_path"])
    if not path.exists():
        return None, None
    return Model.load(path), int(row["id"])


def start_round(n: int = 50, *, strategy: str = "blended",
                pool_limit: int = 20000, verbose: bool = True) -> dict:
    model, model_id = latest_model()
    items = active.pool(limit=pool_limit)
    picks = active.select(model, items, n=n, support=active.current_support())

    round_no = int(engine.scalar(
        "SELECT coalesce(max(round_no), 0) + 1 FROM active_rounds") or 1)
    round_id = int(engine.execute(
        """INSERT INTO active_rounds (round_no, strategy, model_version_id,
                                      n_pool, n_selected, notes)
           VALUES (?,?,?,?,?,?) RETURNING id""",
        (round_no, strategy if model else f"{strategy} (cold start)", model_id,
         len(items), len(picks),
         "no model yet — selection by lexicon signal and density"
         if model is None else "")).fetchone()[0])

    with engine.transaction():
        engine.connect().executemany(
            """INSERT INTO active_queue (round_id, target_type, target_id, score, reason)
               VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING""",
            [(round_id, p.target_type, p.target_id, p.score, p.reason) for p in picks])

    by_reason: dict[str, int] = {}
    for p in picks:
        by_reason[p.reason] = by_reason.get(p.reason, 0) + 1
    if verbose:
        print(f"  round {round_no} (id {round_id}) — selected {len(picks)} "
              f"of {len(items):,} unlabelled")
        print(f"    model: {'none yet, cold start' if model is None else model.version}")
        print(f"    chosen because: {by_reason}")
    return {"round_id": round_id, "round_no": round_no, "selected": len(picks),
            "pool": len(items), "by_reason": by_reason}


def queue(round_id: int | None = None, only_pending: bool = True) -> list[dict]:
    round_id = round_id or engine.scalar("SELECT max(id) FROM active_rounds")
    if round_id is None:
        return []
    where = "AND q.annotated = 0" if only_pending else ""
    return [dict(r) for r in engine.query(
        f"""SELECT q.id, q.target_type, q.target_id, q.score, q.reason
              FROM active_queue q WHERE q.round_id = ? {where}
             ORDER BY q.score DESC""", (round_id,))]


def mark_annotated(round_id: int, target_type: str, target_id: int) -> None:
    engine.execute(
        "UPDATE active_queue SET annotated = 1 WHERE round_id=? AND target_type=? "
        "AND target_id=?", (round_id, target_type, target_id))


def close_round(round_id: int | None = None, *, include_adjudicated: bool = False,
                verbose: bool = True) -> dict:
    """Retrain on everything labelled so far and record what changed."""
    round_id = round_id or engine.scalar("SELECT max(id) FROM active_rounds")
    ts = train.assemble(include_adjudicated=include_adjudicated)
    if len(ts.texts) < 10:
        msg = f"only {len(ts.texts)} labelled examples — need ~10 to train"
        if verbose:
            print(f"  {msg}")
        return {"error": msg}

    metrics = train.cross_validate(ts)
    curve = train.learning_curve(ts)

    model = Model.train(ts.texts, ts.matches, ts.targets, train_source=ts.source)
    # Calibrate on the human rows so reported confidence means something.
    import numpy as np
    human = np.where(ts.human_mask)[0]
    if len(human) >= 12:
        model.calibrate([ts.texts[i] for i in human], [ts.matches[i] for i in human],
                        {h: Y[human] for h, (labels, Y) in ts.targets.items()})
    model.metrics = metrics

    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    path = model.save(WEIGHTS_DIR / f"{model.version}.npz")
    model_id = train.register(model, metrics, str(path),
                              notes=f"closed round {round_id}")

    n_annotated = int(engine.scalar(
        "SELECT count(*) FROM active_queue WHERE round_id=? AND annotated=1",
        (round_id,)) or 0)
    engine.execute(
        """UPDATE active_rounds SET finished_at=unixepoch(), n_annotated=?,
               model_version_id=?, metrics_json=? WHERE id=?""",
        (n_annotated, model_id, json.dumps({"metrics": metrics, "curve": curve}), round_id))

    if verbose:
        print(f"  trained {model.version} on {len(ts.texts)} examples "
              f"({ts.provenance.get('human',0)} human labels, source={ts.source})")
        for head in train.HEADS:
            if head in metrics:
                rows = metrics[head]["labels"]
                held = metrics[head]["withheld_low_support"]
                print(f"    {head:<8} {len(rows)} label(s) measurable, "
                      f"{held} withheld below n={train.MIN_SUPPORT}")
                for lab, m in sorted(rows.items(), key=lambda kv: -kv[1]["support"])[:5]:
                    print(f"      {lab:<30} P {m['precision']:.2f}  R {m['recall']:.2f}  "
                          f"F1 {m['f1']:.2f}  n={m['support']}")
        if curve:
            pts = "  ".join(f"n={c['n']}:{c['macro_f1']}" for c in curve if c["macro_f1"] is not None)
            print(f"    learning curve: {pts}")
    return {"model_version": model.version, "model_id": model_id,
            "metrics": metrics, "curve": curve, "n_train": len(ts.texts)}


def propose_terms_from_model(model: Model | None = None, *, top: int = 20,
                             verbose: bool = True, persist: bool = True) -> list[dict]:
    """Read the model's own weights back as candidate lexicon entries.

    This is the second half of the loop and the reason the model is linear: a
    high positive weight on a feature the lexicon does not contain is the model
    telling you about a term it learned from data. Nothing is promoted — these
    land in term_candidates as proposals for a human, exactly like the keyness
    ones, and are tagged with a different source so the two lines of evidence
    stay separable.

    Weights live in hashed buckets, which are not human-readable. The corpus
    vocabulary is re-hashed to invert them, so a proposal names an actual term
    rather than a number — an unreadable proposal is not a proposal.
    """
    if model is None:
        model, _ = latest_model()
    if model is None:
        return []
    train.guard_not_second_generation(model)

    from ..classify import lexicon as L
    from .features import WORD_RE, _h, normalise

    # Invert the hash over the vocabulary that actually occurs in the corpus.
    bucket_to_token: dict[int, str] = {}
    for table, col in (("comments", "text"), ("items", "caption"),
                       ("accounts", "bio"), ("tags", "tag")):
        for r in engine.query(f"SELECT {col} AS t FROM {table}"):
            words = WORD_RE.findall(normalise(r["t"] or ""))
            for i, w in enumerate(words):
                for tok in (f"w:{w}",
                            f"w:{w} {words[i+1]}" if i + 1 < len(words) else None):
                    if tok:
                        bucket_to_token.setdefault(_h(tok, model.spec.dim), tok[2:])
    known_buckets = {_h(f"lex:{e.slug}", model.spec.dim) for e in L.ALL_ENTRIES}
    known_words = set()
    for e in L.ALL_ENTRIES:
        known_words |= {w.lower() for w in __import__("re").findall(r"[a-z]{4,}", e.pattern.lower())}

    # Two independent signals are required, not one.
    #
    # The first version of this ranked purely by model weight and proposed "is
    # the", "she", "of" and a hash collision — because with a few hundred
    # training examples the top weights are mostly noise, and a function word
    # that happens to co-occur with a class gets a large coefficient. A term
    # only survives here if the MODEL weights it highly AND the CORPUS shows it
    # concentrated in that label's documents. Agreement between a learned
    # signal and a frequency signal is much harder to get by accident.
    from .discover_support import corpus_stats, STOPWORDS

    df, label_df = corpus_stats()
    total_docs = max(df.get("__total__", 1), 1)

    seen: set[str] = set()
    proposals: list[dict] = []
    for head_name in ("domain", "harm"):
        head = model.heads.get(head_name)
        if head is None:
            continue
        for label in head.labels:
            for bucket, weight in model.top_features(head_name, label, top=top * 6):
                if bucket in known_buckets:
                    continue
                term = bucket_to_token.get(bucket)
                if (not term or term in known_words or term in seen
                        or len(term) < 4 or term in STOPWORDS
                        or all(w in STOPWORDS for w in term.split())):
                    continue
                n_docs = df.get(term, 0)
                if n_docs < 3:            # seen once or twice is not evidence
                    continue
                in_label = label_df.get((label, term), 0)
                if in_label < 2:
                    continue
                # Enrichment: how much more often this term appears in the
                # label's documents than in the corpus at large.
                enrich = (in_label / max(label_df.get((label, "__n__"), 1), 1)) / \
                         (n_docs / total_docs)
                if enrich < 2.0:
                    continue
                seen.add(term)
                proposals.append({"term": term, "weight": round(weight, 4),
                                  "head": head_name, "label": label,
                                  "docs": n_docs, "in_label": in_label,
                                  "enrichment": round(enrich, 2),
                                  "score": round(weight * enrich, 3)})
    proposals.sort(key=lambda d: -d["score"])
    proposals = proposals[:top]

    if persist and proposals:
        with engine.transaction():
            engine.connect().executemany(
                """INSERT INTO term_candidates
                       (term, run_id, positive_count, background_count, log_likelihood,
                        ratio, source, weight, domain)
                   VALUES (?,NULL,0,0,NULL,NULL,'model_weight',?,?)
                   ON CONFLICT (term, run_id) DO UPDATE SET
                       weight=excluded.weight, domain=excluded.domain""",
                [(p["term"], p["weight"],
                  p["label"] if p["head"] == "domain" else None) for p in proposals])
    if verbose:
        print(f"  {len(proposals)} term(s) proposed from model weights "
              f"(proposals only — promotion is a human decision)")
    return proposals


def history() -> list[dict]:
    return [dict(r) for r in engine.query(
        """SELECT r.round_no, r.strategy, r.n_pool, r.n_selected, r.n_annotated,
                  m.version AS model, m.n_train, m.train_source, r.finished_at
             FROM active_rounds r LEFT JOIN model_versions m ON m.id = r.model_version_id
            ORDER BY r.round_no""")]

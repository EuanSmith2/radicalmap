"""Assembling training data, and the guard that keeps the loop honest.

The danger in any self-improving system is the degenerate case: a model trained
on its own predictions gets more confident about what it already believed, and
its errors compound silently. For a research instrument that is disqualifying —
the resulting corpus would encode the lexicon's original bias with a statistical
veneer over it, and nothing in the data would reveal that had happened.

Three safeguards, all enforced in code rather than described in a comment:

  Provenance is recorded per label, and a model's `train_source` reflects the
  actual mix rather than the caller's intention.

  A model whose training data came from another MODEL cannot be used to produce
  training labels again. One generation of distillation is a legitimate
  technique; two is a rumour with a confidence score.

  Every reported metric is computed on HUMAN-annotated held-out data, never on
  adjudicated or model-derived labels, no matter what the model trained on.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import numpy as np

from .. import taxonomy as T
from ..classify import lexicon as L
from ..db import engine
from .features import FeatureSpec
from .model import Model

HEADS = ("domain", "harm", "stance")
MIN_SUPPORT = 8          # below this, a per-label metric is noise, not a result


@dataclass
class TrainingSet:
    texts: list[str]
    matches: list[list]
    targets: dict[str, tuple[list[str], np.ndarray]]
    keys: list[tuple[str, int]]
    provenance: dict[str, int]
    human_mask: np.ndarray

    @property
    def source(self) -> str:
        human = self.provenance.get("human", 0)
        machine = self.provenance.get("adjudicated", 0)
        if machine and human:
            return "gold+adjudicated"
        return "adjudicated" if machine else "gold"


def _text_for(target_type: str, target_id: int) -> str | None:
    table, col = {"comment": ("comments", "text"), "item": ("items", "caption"),
                  "account": ("accounts", "bio"), "tag": ("tags", "tag")}.get(
        target_type, (None, None))
    if table is None:
        return None
    row = engine.query_one(f"SELECT {col} AS t FROM {table} WHERE id = ?", (target_id,))
    return row["t"] if row else None


def assemble(*, include_adjudicated: bool = False,
             annotators: list[str] | None = None) -> TrainingSet:
    """Gather labelled examples.

    Human gold is always included. Adjudicated (LLM) labels are opt-in and are
    marked, never silently blended — the difference between "80% F1 against
    human judgement" and "80% agreement with another model" is the difference
    between a result and a restatement.
    """
    rows: dict[tuple[str, int], dict] = {}
    prov = {"human": 0, "adjudicated": 0}

    where = ""
    params: list = []
    if annotators:
        where = f"AND annotator IN ({','.join('?' * len(annotators))})"
        params = list(annotators)
    for r in engine.query(
            f"""SELECT target_type, target_id, label, domain, harm_type, stance,
                       is_positive, annotator
                  FROM gold_annotations WHERE 1=1 {where}""", tuple(params)):
        if str(r["annotator"] or "").startswith("model:"):
            # A model's own output recorded as gold would silently close the
            # loop. Refuse rather than quietly drop it — someone wrote it there.
            raise RuntimeError(
                f"gold_annotations contains model-authored rows "
                f"(annotator={r['annotator']!r}). Model output is not ground truth; "
                f"store it as a classification, not an annotation.")
        key = (r["target_type"], int(r["target_id"]))
        d = rows.setdefault(key, {"domain": set(), "harm": set(), "stance": None,
                                  "human": True})
        if r["is_positive"]:
            if r["domain"]:
                d["domain"].add(r["domain"])
            if r["harm_type"]:
                d["harm"].add(r["harm_type"])
            if r["stance"]:
                d["stance"] = r["stance"]
        prov["human"] += 1

    if include_adjudicated:
        for r in engine.query(
                """SELECT c.target_type, c.target_id, c.domain, c.harm_type, c.stance
                     FROM classifications c JOIN classifier_runs r ON r.id = c.run_id
                    WHERE c.stage = 'llm' AND c.verdict = 'confirmed'
                      AND r.model NOT LIKE 'stub%'"""):
            key = (r["target_type"], int(r["target_id"]))
            if key in rows and rows[key]["human"]:
                continue                      # human labels win
            d = rows.setdefault(key, {"domain": set(), "harm": set(), "stance": None,
                                      "human": False})
            if r["domain"]:
                d["domain"].add(r["domain"])
            if r["harm_type"]:
                d["harm"].add(r["harm_type"])
            if r["stance"]:
                d["stance"] = r["stance"]
            prov["adjudicated"] += 1

    keys, texts, matches, human = [], [], [], []
    payload: list[dict] = []
    for key, d in rows.items():
        text = _text_for(*key)
        if not text or not text.strip():
            continue
        keys.append(key)
        texts.append(text)
        matches.append(L.scan(text).matches)
        human.append(d["human"])
        payload.append(d)

    allowed_domains = [d.slug for d in T.DOMAINS
                       if T.gate_open() or d.slug not in T.GATED_DOMAINS]
    allowed_harms = [h.slug for h in T.HARM_TYPES
                     if T.gate_open() or h.slug not in T.GATED_HARMS]
    stance_labels = [s.slug for s in T.STANCES]

    def onehot(field: str, labels: list[str], multi: bool) -> np.ndarray:
        Y = np.zeros((len(payload), len(labels)))
        for i, d in enumerate(payload):
            vals = d[field] if multi else ({d[field]} if d[field] else set())
            for v in vals:
                if v in labels:
                    Y[i, labels.index(v)] = 1.0
        return Y

    targets = {
        "domain": (allowed_domains, onehot("domain", allowed_domains, True)),
        "harm": (allowed_harms, onehot("harm", allowed_harms, True)),
        "stance": (stance_labels, onehot("stance", stance_labels, False)),
    }
    return TrainingSet(texts, matches, targets, keys, prov, np.array(human, dtype=bool))


# ─── evaluation ──────────────────────────────────────────────────────────────

def _prf(y: np.ndarray, p: np.ndarray, thresh: float = 0.5) -> tuple[float, float, float, int]:
    pred = p >= thresh
    tp = float((pred & (y > 0)).sum())
    fp = float((pred & (y == 0)).sum())
    fn = float((~pred & (y > 0)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1, int(y.sum())


def cross_validate(ts: TrainingSet, folds: int = 5, *, seed: int = 1848,
                   spec: FeatureSpec = FeatureSpec(), **kw) -> dict:
    """K-fold CV, scored on HUMAN rows only.

    A single train/test split on a few hundred examples produces a number that
    moves by ten points depending on the seed. Metrics for labels below
    MIN_SUPPORT are withheld rather than reported, because an F1 computed on
    three examples is not a measurement.
    """
    n = len(ts.texts)
    if n < folds * 2:
        return {"error": f"only {n} labelled examples — need at least {folds * 2}"}

    rng = np.random.default_rng(seed)
    order = rng.permutation(n)
    fold_of = np.zeros(n, dtype=int)
    fold_of[order] = np.arange(n) % folds

    acc: dict[str, dict[str, list]] = {h: {} for h in ts.targets}
    for f in range(folds):
        tr, te = fold_of != f, fold_of == f
        # Evaluate on human rows only, whatever the model trained on.
        te = te & ts.human_mask
        if not te.any() or not tr.any():
            continue
        sub = {h: (labels, Y[tr]) for h, (labels, Y) in ts.targets.items()}
        m = Model.train([ts.texts[i] for i in np.where(tr)[0]],
                        [ts.matches[i] for i in np.where(tr)[0]],
                        sub, spec=spec, train_source=ts.source, **kw)
        P = m.predict([ts.texts[i] for i in np.where(te)[0]],
                      [ts.matches[i] for i in np.where(te)[0]])
        for head, (labels, Y) in ts.targets.items():
            if head not in P:
                continue
            for k, lab in enumerate(labels):
                prec, rec, f1, sup = _prf(Y[te][:, k], P[head][:, k])
                d = acc[head].setdefault(lab, {"p": [], "r": [], "f1": [], "n": 0})
                d["p"].append(prec); d["r"].append(rec); d["f1"].append(f1)
                d["n"] += sup

    out: dict[str, dict] = {}
    for head, labs in acc.items():
        rows = {}
        for lab, d in labs.items():
            if d["n"] < MIN_SUPPORT:
                continue                      # withheld: not enough support
            rows[lab] = {"precision": round(float(np.mean(d["p"])), 3),
                         "recall": round(float(np.mean(d["r"])), 3),
                         "f1": round(float(np.mean(d["f1"])), 3),
                         "support": d["n"]}
        withheld = sum(1 for d in labs.values() if 0 < d["n"] < MIN_SUPPORT)
        out[head] = {"labels": rows, "withheld_low_support": withheld,
                     "min_support": MIN_SUPPORT}
    out["_n"] = n
    out["_human"] = int(ts.human_mask.sum())
    out["_source"] = ts.source
    return out


def learning_curve(ts: TrainingSet, points: int = 5, *, seed: int = 1848,
                   repeats: int = 3) -> list[dict]:
    """Macro-F1 against training-set size.

    The point of this is not the chart. It answers the only question an
    annotator actually cares about — is another afternoon of labelling going to
    help? — with evidence instead of a guess.

    Two things the first version of this got wrong, both of which made the curve
    slope the wrong way:

      It took the FIRST n examples rather than a random subsample. Annotation
      arrives grouped — by round, by domain, by whatever the annotator was
      looking at — so a prefix is a biased sample, and the early points scored a
      homogeneous easy subset.

      It averaged over whatever labels happened to clear MIN_SUPPORT at that
      size. Since more data pushes more labels over the threshold, later points
      were averaged over a larger and harder label set, so the curve measured
      the changing label set rather than the model. The label set is now fixed
      to the labels measurable at FULL size, and every point is scored on those
      same labels.
    """
    n = len(ts.texts)
    if n < 20:
        return []

    full = cross_validate(ts, folds=3, seed=seed)
    fixed: dict[str, list[str]] = {
        head: sorted(full[head]["labels"]) for head in HEADS
        if head in full and full[head]["labels"]}
    if not fixed:
        return []

    rng = np.random.default_rng(seed)
    out = []
    for frac in np.linspace(0.3, 1.0, points):
        take = max(12, int(n * frac))
        scores: list[float] = []
        for rep in range(repeats if take < n else 1):
            idx = rng.permutation(n)[:take]
            sub = TrainingSet([ts.texts[i] for i in idx], [ts.matches[i] for i in idx],
                              {h: (labels, Y[idx]) for h, (labels, Y) in ts.targets.items()},
                              [ts.keys[i] for i in idx], ts.provenance,
                              ts.human_mask[idx])
            res = cross_validate(sub, folds=3, seed=seed + rep)
            # Score on the FIXED label set. A label absent at this size counts
            # as F1 0 rather than being dropped from the average.
            f1s = [res[head]["labels"].get(lab, {}).get("f1", 0.0)
                   for head, labs in fixed.items() if head in res
                   for lab in labs]
            if f1s:
                scores.append(float(np.mean(f1s)))
        out.append({"n": take,
                    "macro_f1": round(float(np.mean(scores)), 3) if scores else None,
                    "sd": round(float(np.std(scores)), 3) if len(scores) > 1 else None,
                    "labels_scored": sum(len(v) for v in fixed.values())})
    return out


def register(model: Model, metrics: dict, weights_path: str, notes: str = "") -> int:
    return int(engine.execute(
        """INSERT INTO model_versions
               (version, algorithm, feature_spec, n_train, n_labels, metrics_json,
                weights_path, train_source, notes)
           VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
        (model.version, "ovr_logreg_l2", model.spec.describe(), model.n_train,
         sum(len(h.labels) for h in model.heads.values()), json.dumps(metrics),
         weights_path, model.train_source, notes)).fetchone()[0])


def guard_not_second_generation(model: Model) -> None:
    """Refuse to use a distilled model as a label source for another model."""
    if model.train_source == "adjudicated":
        raise RuntimeError(
            "this model was trained entirely on machine labels; using it to "
            "generate training labels would be second-generation self-training. "
            "Add human annotations first.")

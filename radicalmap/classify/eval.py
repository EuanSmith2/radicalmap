"""Measurement.

Without this module the two-stage design is just an opinion. Everything here
produces a number that can be put in a paper, an award entry, or a reply to a
reviewer who asks how the classifier performs.

Four things are measured:

  Precision / recall / F1 per category, against a human-annotated gold set.
  Per-pattern precision, so a lexicon entry can be retired on evidence rather
  than on taste.
  Agreement between two runs, including Cohen's kappa — which is how you show
  a change actually changed something.
  A direct v1-vs-v2 comparison on constructed cases, because on the 18-video
  development corpus v1's worst pattern never happens to fire, and reporting
  "v1 produced no false positives here" would be true and deeply misleading.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..db import engine
from . import lexicon as L

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ─── precision / recall / F1 ─────────────────────────────────────────────────

@dataclass
class Metrics:
    label: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p and r and (p + r) else None

    @property
    def support(self) -> int:
        return self.tp + self.fn

    def row(self) -> str:
        fmt = lambda v: f"{v:.3f}" if v is not None else "  -  "
        return (f"{self.label:<20} {fmt(self.precision)}  {fmt(self.recall)}  "
                f"{fmt(self.f1)}  {self.support:>5}  {self.tp:>4} {self.fp:>4} {self.fn:>4}")


def gold_metrics(run_id: int, *, annotator: str | None = None,
                 stage: str = "llm") -> dict[str, Metrics]:
    """Score a run against gold_annotations.

    Gold is keyed on (target_type, target_id, label). A run's positive for a
    label is a row with verdict='confirmed'; 'uncertain' counts as a negative,
    which is the conservative reading — an uncertain result is not a finding.
    """
    where_ann = "AND annotator = ?" if annotator else ""
    params: list = [annotator] if annotator else []
    gold = {(r["target_type"], int(r["target_id"]), r["label"]): bool(r["is_positive"])
            for r in engine.query(
                f"SELECT target_type, target_id, label, is_positive FROM gold_annotations "
                f"WHERE 1=1 {where_ann}", tuple(params))}
    if not gold:
        return {}

    predicted = {(r["target_type"], int(r["target_id"]), r["label"])
                 for r in engine.query(
                     "SELECT target_type, target_id, label FROM classifications "
                     "WHERE run_id = ? AND stage = ? AND verdict = 'confirmed' "
                     "AND label IS NOT NULL", (run_id, stage))}

    out: dict[str, Metrics] = {}
    for (ttype, tid, label), is_pos in gold.items():
        m = out.setdefault(label, Metrics(label))
        hit = (ttype, tid, label) in predicted
        if is_pos and hit:
            m.tp += 1
        elif is_pos:
            m.fn += 1
        elif hit:
            m.fp += 1
        else:
            m.tn += 1
    return out


def report(metrics: dict[str, Metrics]) -> str:
    if not metrics:
        return ("  no gold annotations yet — run `classify sample` to draw a "
                "stratified set, then `classify annotate` to label it")
    lines = [f"  {'label':<20} {'prec':>5}  {'rec':>5}  {'F1':>5}  {'n':>5}  "
             f"{'tp':>4} {'fp':>4} {'fn':>4}", "  " + "-" * 66]
    tp = fp = fn = 0
    for m in sorted(metrics.values(), key=lambda x: -x.support):
        lines.append("  " + m.row())
        tp, fp, fn = tp + m.tp, fp + m.fp, fn + m.fn
    micro = Metrics("MICRO AVG", tp, fp, fn)
    lines += ["  " + "-" * 66, "  " + micro.row()]
    return "\n".join(lines)


# ─── per-pattern precision ───────────────────────────────────────────────────

def per_pattern(candidate_run: int, verdict_run: int) -> list[dict[str, object]]:
    """How each lexicon entry performed.

    This is the lexicon-maintenance loop: an entry generating many candidates
    and few confirmations is costing adjudication budget and injecting noise,
    and should be retired or demoted a tier. v1 had no way to know this, which
    is how a rule matching one specific video survived into a public release.
    """
    rows = engine.query(
        """SELECT e.slug, e.tier, e.category,
                  count(DISTINCT c.id) AS candidates,
                  sum(CASE WHEN v.verdict = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
                  sum(CASE WHEN v.verdict = 'rejected'  THEN 1 ELSE 0 END) AS rejected,
                  sum(CASE WHEN v.verdict = 'uncertain' THEN 1 ELSE 0 END) AS uncertain
             FROM classifications c
             JOIN lexicon_entries e ON e.id = c.lexicon_entry_id
             LEFT JOIN classifications v ON v.parent_id = c.id AND v.run_id = ?
            WHERE c.run_id = ? AND c.verdict = 'candidate'
            GROUP BY e.slug, e.tier, e.category
            ORDER BY candidates DESC""", (verdict_run, candidate_run))
    out = []
    for r in rows:
        judged = (r["confirmed"] or 0) + (r["rejected"] or 0) + (r["uncertain"] or 0)
        out.append({
            "slug": r["slug"], "tier": r["tier"], "category": r["category"],
            "candidates": int(r["candidates"]), "confirmed": int(r["confirmed"] or 0),
            "rejected": int(r["rejected"] or 0), "uncertain": int(r["uncertain"] or 0),
            "precision": (int(r["confirmed"] or 0) / judged) if judged else None,
        })
    return out


# ─── run comparison ──────────────────────────────────────────────────────────

def compare_runs(run_a: int, run_b: int) -> dict[str, object]:
    """Agreement between two runs over the targets either one touched."""
    # 'uncategorised' is not a finding. v1 stored a category on every hashtag
    # row, including the 21 it could not categorise, and counting those as
    # flags made v1 look far more productive than it was.
    NON_FINDING = ("uncategorised", "username_signal")

    def positives(run_id: int) -> set[tuple[str, int]]:
        marks = ",".join("?" * len(NON_FINDING))
        return {(r["target_type"], int(r["target_id"])) for r in engine.query(
            f"SELECT DISTINCT target_type, target_id FROM classifications "
            f"WHERE run_id = ? AND verdict IN ('candidate','confirmed') "
            f"AND label NOT IN ({marks})", (run_id, *NON_FINDING))}

    a, b = positives(run_a), positives(run_b)
    universe = _universe()
    both, only_a, only_b = a & b, a - b, b - a
    neither = universe - (a | b)

    n = len(universe) or 1
    po = (len(both) + len(neither)) / n
    pe = ((len(a) / n) * (len(b) / n)) + ((1 - len(a) / n) * (1 - len(b) / n))
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0

    return {"run_a": run_a, "run_b": run_b, "universe": len(universe),
            "a_flagged": len(a), "b_flagged": len(b), "both": len(both),
            "only_a": len(only_a), "only_b": len(only_b), "neither": len(neither),
            "observed_agreement": round(po, 4), "cohens_kappa": round(kappa, 4),
            "only_a_examples": _examples(only_a), "only_b_examples": _examples(only_b)}


def _universe() -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for ttype, table in (("comment", "comments"), ("item", "items"),
                         ("account", "accounts"), ("tag", "tags")):
        out |= {(ttype, int(r["id"])) for r in engine.query(f"SELECT id FROM {table}")}
    return out


def _examples(keys: set[tuple[str, int]], n: int = 5) -> list[str]:
    out = []
    for ttype, tid in list(sorted(keys))[:n]:
        if ttype == "comment":
            row = engine.query_one("SELECT text AS t FROM comments WHERE id=?", (tid,))
        elif ttype == "item":
            row = engine.query_one("SELECT caption AS t FROM items WHERE id=?", (tid,))
        elif ttype == "tag":
            row = engine.query_one("SELECT '#' || tag AS t FROM tags WHERE id=?", (tid,))
        else:
            row = engine.query_one("SELECT bio AS t FROM accounts WHERE id=?", (tid,))
        if row and row["t"]:
            out.append(f"{ttype} {tid}: {str(row['t'])[:90]}")
    return out


# ─── v1 vs v2, on constructed cases ──────────────────────────────────────────

def v1_flagger():
    """v1's real flag_comment(), imported from the untouched v1 tree."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from scraper.flag_patterns import flag_comment       # noqa: PLC0415
    return flag_comment


def head_to_head(cases: Iterable[dict[str, str]]) -> list[dict[str, object]]:
    """Run v1's classifier and v2's lexicon over the same sentences.

    The 18-video development corpus does not happen to contain a plain mention
    of Jewish people, so v1's broadest pattern never fires there. Measuring only
    on that corpus would report zero false positives for a rule that maps a
    neutral demonym to high-severity antisemitism. These constructed cases show
    what each system does with text the corpus does not contain but a hundred
    thousand videos certainly will.
    """
    flag = v1_flagger()
    out = []
    for case in cases:
        text = case["text"]
        v1_flagged, v1_reason, v1_cats, v1_sev = flag(text)
        cand = L.scan(text)
        out.append({
            "text": text,
            "why": case.get("why", ""),
            "v1_verdict": f"{v1_cats} / {v1_sev}" if v1_flagged else "not flagged",
            "v1_is_finding": bool(v1_flagged),
            "v1_severity": v1_sev if v1_flagged else None,
            "v2_candidate": cand.is_candidate,
            "v2_tier": cand.top_tier if cand.matches else None,
            # The structural difference: v2 never emits a severity at this stage.
            "v2_is_finding": False,
            "v2_terms": sorted({m.slug for m in cand.matches}),
        })
    return out

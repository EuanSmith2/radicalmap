"""`radicalmap classify eval-report` — measured performance, written to disk.

Wraps classify/eval.py's gold-set scoring and writes report.md + report.json
with everything a reader needs to reproduce the numbers: which adjudication run,
which lexicon version, how many gold labels from how many annotators.

It refuses rather than guesses. No gold set, no adjudication run, or a run made
by the deterministic stub all exit non-zero with the reason, and no metric is
printed in any of those cases. A placeholder number in a report is worse than
no report, because it gets quoted.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..classify import eval as E
from ..classify import runs
from ..db import engine


class ReportRefused(RuntimeError):
    pass


def build(run_id: int | None = None) -> dict[str, Any]:
    gold_n = int(engine.scalar("SELECT count(*) FROM gold_annotations") or 0)
    if gold_n == 0:
        raise ReportRefused(
            "no gold annotations exist, so precision and recall cannot be measured. "
            "Draw a sample with `radicalmap classify sample` and label it in the console's "
            "Annotate tab.")
    run_id = run_id or runs.latest("llm")
    if run_id is None:
        raise ReportRefused("no adjudication run exists to evaluate.")
    run = engine.query("SELECT * FROM classifier_runs WHERE id = ?", (run_id,))
    if not run:
        raise ReportRefused(f"classifier run {run_id} does not exist.")
    run = dict(run[0])
    if (run.get("model") or "").startswith("stub"):
        raise ReportRefused(
            f"run {run_id} was made by the deterministic stub adjudicator; its output is a "
            "pipeline test, not research output, and is not reported.")

    metrics = E.gold_metrics(run_id, stage=run["stage"])
    tp = sum(m.tp for m in metrics.values())
    fp = sum(m.fp for m in metrics.values())
    fn = sum(m.fn for m in metrics.values())
    micro = E.Metrics("micro", tp, fp, fn)
    annotators = int(engine.scalar(
        "SELECT count(DISTINCT annotator) FROM gold_annotations") or 0)
    r = lambda v: None if v is None else round(v, 4)
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": {k: run.get(k) for k in ("id", "stage", "version", "model", "prompt_hash",
                                         "lexicon_version_id", "started_at", "finished_at",
                                         "target_count")},
        "gold": {"annotations": gold_n, "annotators": annotators},
        "labels": [{"label": m.label, "precision": r(m.precision), "recall": r(m.recall),
                    "f1": r(m.f1), "support": m.support,
                    # per-label 2x2 confusion: the classification is multi-label,
                    # so a single NxN matrix would misstate it
                    "confusion": {"tp": m.tp, "fp": m.fp, "fn": m.fn, "tn": m.tn}}
                   for m in sorted(metrics.values(), key=lambda x: -x.support)],
        "micro": {"precision": r(micro.precision), "recall": r(micro.recall),
                  "f1": r(micro.f1), "tp": tp, "fp": fp, "fn": fn},
    }


def write(out_dir: Path, data: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    f = lambda v: "-" if v is None else f"{v:.3f}"
    run = data["run"]
    lines = [
        f"# Classifier evaluation: run {run['id']}",
        "",
        f"Generated {data['generated']}. Stage `{run['stage']}`, version `{run['version']}`, "
        f"model `{run['model'] or '-'}`, lexicon version {run['lexicon_version_id'] or '-'}.",
        "",
        f"Scored against {data['gold']['annotations']:,} gold annotations from "
        f"{data['gold']['annotators']} annotator(s). 'Uncertain' verdicts count as negative.",
        "",
        "| label | precision | recall | F1 | support | tp | fp | fn | tn |",
        "|---|---|---|---|---|---|---|---|---|",
        *[f"| {x['label']} | {f(x['precision'])} | {f(x['recall'])} | {f(x['f1'])} | "
          f"{x['support']} | {x['confusion']['tp']} | {x['confusion']['fp']} | "
          f"{x['confusion']['fn']} | {x['confusion']['tn']} |" for x in data["labels"]],
        f"| **micro** | {f(data['micro']['precision'])} | {f(data['micro']['recall'])} | "
        f"{f(data['micro']['f1'])} | | {data['micro']['tp']} | {data['micro']['fp']} | "
        f"{data['micro']['fn']} | |",
        "",
    ]
    if any(x["support"] < 20 for x in data["labels"]):
        lines += ["Labels with support under 20 have wide uncertainty; treat their figures "
                  "as indicative only.", ""]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")

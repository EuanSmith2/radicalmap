"""Gold-set annotation over HTTP.

This is the only write path in the UI, and it exists because the ~300-item gold
set is what every precision/recall number in the project depends on — and it
cannot be produced by the model being evaluated.

Two rules are enforced here rather than left to the front end:

1. **Blinding.** The sample rows carry the lexicon's *matched terms* but never
   its verdict, and never any model output. An annotator who can see what the
   classifier decided is no longer producing an independent standard, so the
   verdict is not merely hidden in the UI — it is never sent over the wire.
2. **Provenance.** Writes go through classify.annotate.record(), which refuses
   an annotator id beginning with "model:". Re-implementing the insert here
   would route around that guard, which is exactly how a learning loop becomes
   self-confirming.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ... import taxonomy as T
from ...classify import annotate as A
from ...db import engine

router = APIRouter(tags=["annotate"])

# Fields a SampleRow may expose. Deliberately an allow-list: if draw() ever
# grows a field carrying the classifier's opinion, it stays out of the response
# until someone adds it here on purpose.
_SAFE_FIELDS = ("target_type", "target_id", "stratum", "text", "terms")


class Judgement(BaseModel):
    target_type: str
    target_id: int
    label: str
    is_positive: bool
    annotator: str = Field(min_length=1)
    severity: str | None = None
    notes: str = ""
    domain: str | None = None
    harm_type: str | None = None
    stance: str | None = None


@router.get("/annotate/options")
def options() -> dict:
    """The three axes, gated exactly as the CLI gates them — a domain behind
    GDPR Art. 9 is not offered unless RM_DPIA_REF names a real assessment."""
    gate = T.gate_open()
    return {
        "gate_open": gate,
        "domains": [{"slug": d.slug, "name": d.name, "family": d.family}
                    for d in T.DOMAINS if gate or not d.requires_dpia],
        "harm_types": [{"slug": h.slug, "name": h.name,
                        "severity_ceiling": h.severity_ceiling}
                       for h in T.HARM_TYPES if gate or not h.requires_dpia],
        "stances": [{"slug": s.slug, "name": s.name,
                     "finding_eligible": bool(s.finding_eligible)}
                    for s in T.STANCES],
    }


@router.get("/annotate/sample")
def sample(n: int = 50, seed: int = 1848, run: int | None = None,
           skip_done: bool = True, annotator: str | None = None) -> dict:
    if not 1 <= n <= 500:
        raise HTTPException(400, "n must be between 1 and 500")
    rows = A.draw(n=n, run_id=run, seed=seed)

    done: set[tuple[str, int]] = set()
    if skip_done:
        sql = "SELECT target_type, target_id FROM gold_annotations"
        params: list = []
        if annotator:
            sql += " WHERE annotator = ?"
            params.append(annotator)
        done = {(r["target_type"], int(r["target_id"])) for r in engine.query(sql, params)}

    out = []
    for r in rows:
        if (r.target_type, int(r.target_id)) in done:
            continue
        out.append({f: getattr(r, f) for f in _SAFE_FIELDS})
    return {"drawn": len(rows), "remaining": len(out), "skipped": len(rows) - len(out),
            "rows": out}


@router.get("/annotate/coverage")
def coverage(annotator: str | None = None) -> dict:
    return A.coverage(annotator)


def _validate_axes(j: "Judgement") -> None:
    """Reject unknown axis slugs before they reach the insert.

    gold_annotations has foreign keys onto domains/harm_types/stances, so an
    unknown slug surfaces as a bare sqlite3.IntegrityError and a 500. A typo in
    a slug is a client error and has to say so — and the FK message names
    neither the field nor the legal values.
    """
    for field, value, valid in (
            ("domain", j.domain, {d.slug for d in T.DOMAINS}),
            ("harm_type", j.harm_type, {h.slug for h in T.HARM_TYPES}),
            ("stance", j.stance, {s.slug for s in T.STANCES})):
        if value is not None and value not in valid:
            raise HTTPException(
                422, f"unknown {field}={value!r}; expected one of "
                     f"{', '.join(sorted(valid))}")


@router.post("/annotate")
def record(j: Judgement) -> dict:
    _validate_axes(j)
    try:
        A.record(j.target_type, j.target_id, j.label, is_positive=j.is_positive,
                 annotator=j.annotator, severity=j.severity, notes=j.notes,
                 domain=j.domain, harm_type=j.harm_type, stance=j.stance)
    except PermissionError as exc:
        # The GDPR Art. 9 gate. It raises PermissionError, not ValueError — a
        # single `except ValueError` here let it escape as a 500 with a stack
        # trace. The write was still refused, but a governance control has to
        # report itself as a deliberate refusal, not as a server fault.
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        # Model-provenance refusal from annotate.record().
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "coverage": A.coverage(j.annotator)}

"""The gold set: sampling, recording, and why it has to be a human.

A classifier cannot be evaluated against its own output, so this is the one
part of the pipeline that cannot be automated. What CAN be automated is drawing
a sample that makes the resulting numbers meaningful.

The sampling design matters more than it looks. Annotating only the items the
lexicon flagged would measure precision and make recall unmeasurable — there
would be no way to count what was missed. So the sample is stratified across
four strata, including text the lexicon said nothing about:

    A-candidates   near-certain matches; expect high precision
    B-candidates   the contested middle; this is where the work is
    C-only         matched weak signals but did not qualify as a candidate
    clean          no lexicon match at all — the ONLY source of false negatives
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable

from ..db import engine
from . import lexicon as L, runs

STRATA = ("A-candidate", "B-candidate", "C-only", "clean")


@dataclass
class SampleRow:
    target_type: str
    target_id: int
    stratum: str
    text: str
    terms: list[str]


def draw(n: int = 300, *, run_id: int | None = None, seed: int = 1848,
         weights: dict[str, float] | None = None) -> list[SampleRow]:
    run_id = run_id or runs.latest("lexicon")
    weights = weights or {"A-candidate": 0.15, "B-candidate": 0.45,
                          "C-only": 0.15, "clean": 0.25}
    rng = random.Random(seed)

    cand_tiers: dict[tuple[str, int], str] = {}
    cand_terms: dict[tuple[str, int], list[str]] = {}
    if run_id:
        for r in engine.query(
                """SELECT c.target_type, c.target_id, e.tier, e.slug
                     FROM classifications c LEFT JOIN lexicon_entries e
                       ON e.id = c.lexicon_entry_id
                    WHERE c.run_id = ? AND c.verdict = 'candidate'""", (run_id,)):
            key = (r["target_type"], int(r["target_id"]))
            tier = r["tier"] or "C"
            cand_tiers[key] = min(cand_tiers.get(key, "Z"), tier)
            cand_terms.setdefault(key, []).append(r["slug"] or "?")

    buckets: dict[str, list[SampleRow]] = {s: [] for s in STRATA}
    for ttype, table, col in (("comment", "comments", "text"),
                              ("item", "items", "caption"),
                              ("account", "accounts", "bio")):
        for r in engine.query(f"SELECT id, {col} AS t FROM {table}"):
            key = (ttype, int(r["id"]))
            text = r["t"] or ""
            if not text.strip():
                continue
            if key in cand_tiers:
                stratum = f"{cand_tiers[key]}-candidate"
                if stratum not in buckets:
                    stratum = "B-candidate"
                terms = sorted(set(cand_terms.get(key, [])))
            else:
                scan = L.scan(text)
                stratum = "C-only" if scan.matches else "clean"
                terms = sorted({m.slug for m in scan.matches})
            buckets[stratum].append(SampleRow(ttype, int(r["id"]), stratum, text, terms))

    out: list[SampleRow] = []
    for stratum, weight in weights.items():
        pool = buckets.get(stratum, [])
        rng.shuffle(pool)
        out += pool[: max(0, round(n * weight))]
    # Backfill from the largest remaining pool if a stratum was short, so the
    # sample still reaches n rather than silently shrinking.
    if len(out) < n:
        rest = [row for s in STRATA for row in buckets[s] if row not in out]
        rng.shuffle(rest)
        out += rest[: n - len(out)]
    rng.shuffle(out)
    return out[:n]


def record(target_type: str, target_id: int, label: str, *, is_positive: bool,
           annotator: str, severity: str | None = None, notes: str = "",
           domain: str | None = None, harm_type: str | None = None,
           stance: str | None = None) -> None:
    """Store one human judgement on the three axes.

    Model output must never arrive here. An `annotator` beginning with "model:"
    is refused rather than stored, because a model's prediction recorded as
    ground truth is how a learning loop quietly becomes a self-confirming one —
    and the refusal belongs at the write, not at the read.
    """
    if str(annotator).startswith("model:"):
        raise ValueError(
            "model output is not ground truth — store it as a classification "
            "(stage='model'), never as a gold annotation")
    from .. import taxonomy as T
    T.check_gate(domain, harm_type)
    severity = T.cap_severity(harm_type, severity)
    engine.execute(
        """INSERT INTO gold_annotations
               (target_type, target_id, label, severity, is_positive, annotator,
                notes, domain, harm_type, stance)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (target_type, target_id, label, annotator) DO UPDATE SET
               is_positive=excluded.is_positive, severity=excluded.severity,
               notes=excluded.notes, domain=excluded.domain,
               harm_type=excluded.harm_type, stance=excluded.stance,
               annotated_at=unixepoch()""",
        (target_type, target_id, label, severity, int(is_positive), annotator, notes,
         domain, harm_type, stance))


def coverage(annotator: str | None = None) -> dict[str, object]:
    where = "WHERE annotator = ?" if annotator else ""
    params = (annotator,) if annotator else ()
    rows = engine.query(
        f"SELECT label, is_positive, count(*) n FROM gold_annotations {where} "
        f"GROUP BY 1, 2", params)
    by_label: dict[str, dict[str, int]] = {}
    for r in rows:
        d = by_label.setdefault(r["label"], {"positive": 0, "negative": 0})
        d["positive" if r["is_positive"] else "negative"] += int(r["n"])
    total = int(engine.scalar(
        f"SELECT count(DISTINCT target_type || ':' || target_id) "
        f"FROM gold_annotations {where}", params) or 0)
    return {"annotated_targets": total, "by_label": by_label}


LABELS = ["antisemitic", "white_nationalist", "conspiracy", "radicalisation",
          "dehumanisation", "esoteric", "incel", "islamophobic", "accelerationist"]


def interactive(rows: Iterable[SampleRow], annotator: str,
                round_id: int | None = None) -> int:
    """Terminal annotation on the three axes.

    Shows the lexicon's matched terms but NOT its verdict, and never any model
    output: an annotator who can see what the classifier decided is no longer
    producing an independent gold standard.

    Asking for domain, harm and stance separately is deliberate. Collapsing them
    into one question is what produced v1's taxonomy, where "dehumanisation"
    (a harm) sat beside "esoteric" (a domain) beside "radicalisation" (a
    process) in a single list.
    """
    from .. import taxonomy as T
    doms = [d for d in T.DOMAINS if T.gate_open() or not d.requires_dpia]
    harms = [h for h in T.HARM_TYPES if T.gate_open() or not h.requires_dpia]

    def menu(options, label_attr="name"):
        return "  ".join(f"{i+1}={getattr(o, label_attr)}" for i, o in enumerate(options))

    done = 0
    rows = list(rows)
    for i, row in enumerate(rows, 1):
        print("\n" + "=" * 74)
        print(f"  {i}/{len(rows)}   {row.target_type} {row.target_id}")
        print(f"  lexicon terms present: {', '.join(row.terms) or '(none)'}")
        print("-" * 74)
        print("  " + row.text[:900].replace("\n", "\n  "))
        print("-" * 74)

        try:
            print(f"\n  DOMAIN — what is this? (blank = none)")
            for n, d in enumerate(doms, 1):
                if n % 3 == 1:
                    print("   ", end="")
                print(f"{n:>2}={d.slug:<28}", end="\n" if n % 3 == 0 else "")
            print()
            a = input("  domain > ").strip().lower()
            if a == "q":
                break
            if a == "s":
                continue
            domain = doms[int(a) - 1].slug if a.isdigit() and 1 <= int(a) <= len(doms) else "none"

            print(f"\n  HARM — what, if anything, makes this a finding? (blank = none)")
            for n, h in enumerate(harms, 1):
                print(f"    {n:>2}={h.slug}")
            b = input("  harm > ").strip().lower()
            harm = harms[int(b) - 1].slug if b.isdigit() and 1 <= int(b) <= len(harms) else "none"

            print(f"\n  STANCE — the speaker's relation to it")
            for n, st in enumerate(T.STANCES, 1):
                mark = "*" if st.finding_eligible else " "
                print(f"    {n:>2}={st.slug:<14}{mark} {st.note[:52]}")
            c = input("  stance > ").strip().lower()
            stance = (T.STANCES[int(c) - 1].slug if c.isdigit()
                      and 1 <= int(c) <= len(T.STANCES) else "discussing")

            severity = None
            if T.is_finding(domain, harm, stance):
                severity = input(f"  severity [low/medium/high, cap "
                                 f"{T.severity_ceiling(harm)}] > ").strip() or "medium"
        except (EOFError, KeyboardInterrupt):
            print("\n  stopped")
            break
        except (ValueError, IndexError):
            print("  not understood — skipping")
            continue

        record(row.target_type, row.target_id, T.label(domain, harm),
               is_positive=T.is_finding(domain, harm, stance),
               annotator=annotator, severity=severity,
               domain=domain, harm_type=harm, stance=stance)
        if round_id is not None:
            from ..learn import loop as _loop
            _loop.mark_annotated(round_id, row.target_type, row.target_id)
        verdict = "FINDING" if T.is_finding(domain, harm, stance) else "not a finding"
        print(f"  recorded: {domain} / {harm} / {stance}  -> {verdict}")
        done += 1
    return done

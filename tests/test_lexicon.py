#!/usr/bin/env python
"""Behavioural tests for the detection lexicon.

The hard_negative block is the point of the whole exercise. v1's
flag_patterns.py mapped \\bjew\\b straight to "antisemitic / high", so every
sentence in that block would have been recorded as a high-severity finding.
These tests lock in that a neutral demonym can produce a candidate for review
and can never, by itself, produce a verdict.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from radicalmap.classify import lexicon as L

CASES = json.loads((Path(__file__).parent / "fixtures/lexicon_cases.json").read_text())
fails = []

def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label[:72]:<72}" + ("" if ok else f" {got!r} != {want!r}"))

def section(s): print(f"\n  ── {s} " + "─" * max(0, 60 - len(s)))

section("recall: extremist content must reach adjudication")
for case in CASES["must_be_candidate"]:
    c = L.scan(case["text"])
    check(f"{case['text'][:44]!r} -> candidate ({case['why']})", c.is_candidate)

section("precision: ordinary content must not reach adjudication")
for case in CASES["must_not_be_candidate"]:
    c = L.scan(case["text"])
    check(f"{case['text'][:44]!r} -> not a candidate ({case['why']})",
          c.is_candidate, False)

section("hard negatives: flagged for review, NEVER auto-confirmed")
for case in CASES["hard_negative"]:
    c = L.scan(case["text"])
    # These may legitimately be candidates — recall requires it. What must hold
    # is that none is tier A, i.e. none can be treated as near-certain without
    # an adjudicator having looked at the context.
    check(f"{case['text'][:44]!r} not tier A ({case['why'][:26]})",
          c.top_tier != "A" if c.is_candidate else True)

section("a neutral demonym is never a high-confidence finding")
for case in CASES["must_never_be_tier_a"]:
    c = L.scan(case["text"])
    check(f"{case['text'][:44]!r} -> tier {c.top_tier if c.matches else '-'}",
          c.top_tier != "A" if c.matches else True)

section("the v1 regression, stated directly")
c = L.scan("my jewish grandmother survived the camps")
check("the sentence matches the demonym entry (recall preserved)",
      any(m.slug == "demonym-jewish" for m in c.matches))
check("...at tier B, not tier A", c.top_tier, "B")
check("...and the lexicon assigns NO severity",
      not hasattr(L.Match("x", "B", "y", "z", 0, 1, ""), "severity"))
check("v1's most dangerous pattern is retired with a reason",
      any("i support israel" in r["pattern"] for r in L.RETIRED))
check("the single-video rule is retired",
      any("beer belly" in r["pattern"] for r in L.RETIRED))
check("every retirement records why", all(len(r["reason"]) > 40 for r in L.RETIRED))

section("gate mechanics")
check("one tier-C alone is not a candidate", L.scan("this is based").is_candidate, False)
check("the same tier-C twice is still one entry",
      L.scan("based based based").is_candidate, False)
check("two DISTINCT tier-C entries qualify",
      L.scan("this is based, link in bio").is_candidate)
check("one tier-B qualifies", L.scan("remigration").is_candidate)
check("one tier-A qualifies", L.scan("1488").is_candidate)

section("lexicon integrity")
check("no duplicate slugs", len(L.BY_SLUG), len(L.ALL_ENTRIES))
check("every entry carries a research note",
      all(len(e.note) > 30 for e in L.ALL_ENTRIES))
check("every entry compiles", all(e.compiled() for e in L.ALL_ENTRIES))
check("every tier is valid", all(e.tier in "ABC" for e in L.ALL_ENTRIES))
check("fingerprint is stable across calls", L.fingerprint(), L.fingerprint())
bad = [e.slug for e in L.ALL_ENTRIES if e.tier == "A" and e.category == "conspiracy"]
check("no conspiracy-category entry is tier A (all are discussed in journalism)", bad, [])

print()
if fails: sys.exit(f"FAILED: {len(fails)} check(s):\n    " + "\n    ".join(fails))
print(f"  all checks passed")

# ── v1 comparison, against v1's real code ───────────────────────────────────
section("v1 head-to-head (running v1's own flag_comment)")
from radicalmap.classify import eval as E
rows = E.head_to_head(CASES["hard_negative"])
for r, case in zip(rows, CASES["hard_negative"]):
    # Lock in that the recorded v1 behaviour in the fixture is accurate. If v1
    # is ever changed, this fails rather than letting the write-up drift.
    expected = case.get("v1_actual")
    if expected:
        got = r["v1_verdict"]
        check(f"v1 on {r['text'][:36]!r} is {expected!r}", got, expected)
v1_findings = sum(r["v1_is_finding"] for r in rows)
check("v1 turns several hard negatives into findings", v1_findings >= 8)
check("v2 turns NONE of them into findings",
      sum(r["v2_is_finding"] for r in rows), 0)
check("v2 still surfaces most of them for review (recall preserved)",
      sum(r["v2_candidate"] for r in rows) >= 8)

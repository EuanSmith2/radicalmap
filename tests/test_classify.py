#!/usr/bin/env python
"""The classification pipeline: runs, adjudication, evaluation, discovery.

Uses a throwaway database and the deterministic stub adjudicator, so nothing
here touches a paid API.
"""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_DB"] = str(tmp / "c.db")
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")

from radicalmap.classify import adjudicate as A, annotate, discover, eval as E, lexicon as L, runs
from radicalmap.collect.adapters.base import CollectResult, ParsedAccount, ParsedComment, ParsedItem
from radicalmap.collect.persist import persist
from radicalmap.db import engine, migrate
from radicalmap.db.repo import writers as W

fails = []
def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label[:70]:<70}" + ("" if ok else f" {got!r} != {want!r}"))
def section(s): print(f"\n  ── {s} " + "─" * max(0, 58 - len(s)))

migrate.migrate(verbose=False)
src = W.ensure_source("t", "import", "")
persist(CollectResult(items=[ParsedItem(
    platform_item_id="x1", caption="remigration now #1488 #fyp",
    account=ParsedAccount("a1", handle="user88", bio="sieg heil lads"),
    comments=[ParsedComment("c1", "my jewish grandmother survived the camps", author_handle="u1"),
              ParsedComment("c2", "this recipe is based", author_handle="u2"),
              ParsedComment("c3", "1488 brothers", author_handle="u3"),
              ParsedComment("c4", "lovely sunset over the harbour", author_handle="u4"),
              ParsedComment("c5", "soros funded my scholarship", author_handle="u5")])]),
    "tiktok", src, "test")

section("lexicon versioning")
lex_id, added = runs.sync_lexicon()
check("entries written", added, len(L.ALL_ENTRIES))
check("re-syncing is a no-op", runs.sync_lexicon()[1], 0)
try:
    engine.execute("UPDATE lexicon_versions SET hash='tampered' WHERE id=?", (lex_id,))
    runs.sync_lexicon(); check("refuses to redefine a published version", False)
except RuntimeError as e:
    check("refuses to redefine a published version", "different fingerprint" in str(e))
engine.execute("UPDATE lexicon_versions SET hash=? WHERE id=?", (L.fingerprint(), lex_id))

section("candidate generation")
stats = runs.scan_corpus(verbose=False)
cand_run = stats["run_id"]
check("candidates found", stats["candidates"] >= 3)
check("every stored row is a candidate, never a verdict",
      engine.scalar("SELECT count(*) FROM classifications WHERE run_id=? AND verdict!='candidate'",
                    (cand_run,)), 0)
check("the lexicon assigns NO severity",
      engine.scalar("SELECT count(*) FROM classifications WHERE run_id=? AND severity IS NOT NULL",
                    (cand_run,)), 0)
check("each candidate names the pattern that fired",
      engine.scalar("SELECT count(*) FROM classifications WHERE run_id=? AND lexicon_entry_id IS NULL",
                    (cand_run,)), 0)
flagged = {r["text"] for r in engine.query(
    "SELECT cm.text FROM classifications c JOIN comments cm ON cm.id=c.target_id "
    "WHERE c.run_id=? AND c.target_type='comment'", (cand_run,))}
check("extremist comment reaches adjudication", "1488 brothers" in flagged)
check("hard negative also reaches adjudication (recall)",
      "my jewish grandmother survived the camps" in flagged)
check("mainstream slang alone does not", "this recipe is based" not in flagged)
check("innocuous text does not", "lovely sunset over the harbour" not in flagged)

section("adjudication")
res = A.run(cand_run, adjudicator=A.StubAdjudicator(), verbose=False)
verdict_run = res["run_id"]
check("verdicts written", res["rows"] >= 1)
check("verdicts link back to their candidate",
      engine.scalar("SELECT count(*) FROM classifications WHERE run_id=? AND parent_id IS NULL",
                    (verdict_run,)), 0)
check("rejections are stored, not discarded",
      engine.scalar("SELECT count(*) FROM classifications WHERE run_id=? AND verdict='rejected'",
                    (verdict_run,)) >= 1)
check("the stub is labelled as such in the run record",
      "stub" in engine.scalar("SELECT model FROM classifier_runs WHERE id=?", (verdict_run,)))
check("the prompt hash is recorded",
      len(engine.scalar("SELECT prompt_hash FROM classifier_runs WHERE id=?", (verdict_run,)) or ""), 64)

section("response parsing")
P = A.AnthropicAdjudicator.parse_response
check("plain JSON array", len(P('[{"id":"k","verdict":"confirmed","label":"antisemitic","confidence":0.9}]')), 1)
check("fenced JSON", len(P('```json\n[{"id":"k","verdict":"rejected"}]\n```')), 1)
check("prose around JSON", len(P('Here you go:\n[{"id":"k","verdict":"uncertain"}]\nhope that helps')), 1)
check("invalid verdict dropped", P('[{"id":"k","verdict":"maybe"}]'), [])
check("garbage yields nothing, not a crash", P("not json at all"), [])
v = P('[{"id":"k","verdict":"confirmed","label":"not_a_real_label","confidence":5}]')[0]
check("unknown label + confirmed downgrades to uncertain", v.verdict, "uncertain")
check("confidence clamped to [0,1]", v.confidence, 1.0)
req = A.AnthropicAdjudicator().build_request([A.Unit("k", "t", "c", [])])
check("request carries the system prompt", "adjudicating candidate flags" in req["system"])
check("request is buildable with no API key", req["model"], "claude-sonnet-5")

section("the paid path never starts implicitly")
os.environ.pop("RM_ALLOW_LLM", None)
try:
    A.AnthropicAdjudicator()._client_or_fail(); check("refuses without RM_ALLOW_LLM", False)
except RuntimeError as e:
    check("refuses without RM_ALLOW_LLM", "never runs implicitly" in str(e))

section("evaluation")
check("no gold yet -> honest empty report", "no gold annotations" in E.report(E.gold_metrics(verdict_run)))
cid = engine.scalar("SELECT id FROM comments WHERE text LIKE '1488%'")
nid = engine.scalar("SELECT id FROM comments WHERE text LIKE 'my jewish%'")
annotate.record("comment", cid, "white_nationalist", is_positive=True, annotator="test")
annotate.record("comment", nid, "antisemitic", is_positive=False, annotator="test")
m = E.gold_metrics(verdict_run, annotator="test")
check("gold produces per-label metrics", len(m) >= 1)
check("a true negative is not counted as a hit",
      m["antisemitic"].fp if "antisemitic" in m else 0, 0)
check("report renders", "MICRO AVG" in E.report(m))

section("per-pattern precision")
rows = E.per_pattern(cand_run, verdict_run)
check("every entry that fired is reported", len(rows) >= 1)
check("candidate counts are positive", all(r["candidates"] > 0 for r in rows))

section("sampling for a gold set")
sample = annotate.draw(30)
strata = {r.stratum for r in sample}
check("sample is drawn", len(sample) > 0)
check("includes text the lexicon did NOT flag — the only source of false negatives",
      "clean" in strata or len(sample) < 30)

section("term discovery")
terms = discover.discover(verdict_run, min_count=1, top=10, verbose=False)
check("returns proposals, not promotions", all(
    engine.scalar("SELECT status FROM term_candidates WHERE term=?", (t["term"],)) == "proposed"
    for t in terms) if terms else True)
check("never proposes a term already in the lexicon",
      all("1488" != t["term"] for t in terms))

print()
if fails: sys.exit(f"FAILED: {len(fails)} check(s):\n    " + "\n    ".join(fails))
print("  all checks passed")

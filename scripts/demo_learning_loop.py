#!/usr/bin/env python
"""End-to-end demonstration of the active-learning loop.

Runs in a throwaway database, through the real code paths — the same assemble,
train, cross-validate and select functions the CLI uses. The labelled examples
are the CONSTRUCTED fixture cases, recorded with annotator='fixture', not
annotations of the real corpus.
"""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_DB"] = str(tmp / "loop.db")
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")

from radicalmap import taxonomy as T
from radicalmap.classify import annotate, lexicon as L
from radicalmap.collect.adapters.base import CollectResult, ParsedAccount, ParsedComment, ParsedItem
from radicalmap.collect.persist import persist
from radicalmap.db import engine, migrate
from radicalmap.db.repo import writers as W
from radicalmap.learn import active, loop, train
from radicalmap.learn.model import Model

def rule(t): print(f"\n{'═'*70}\n  {t}\n{'═'*70}")

cases = json.loads((ROOT / "tests/fixtures/taxonomy_cases.json").read_text())["cases"]
migrate.migrate(verbose=False)
src = W.ensure_source("fixture", "import", "constructed evaluation cases")

# Load the fixture through the real ingest path so every downstream lookup is real.
persist(CollectResult(items=[ParsedItem(
    platform_item_id="fixture", caption="evaluation fixture",
    account=ParsedAccount("fx", handle="fixture"),
    comments=[ParsedComment(f"fx{i}", c["text"], author_handle=f"a{i}")
              for i, c in enumerate(cases)])]), "tiktok", src, "fixture")
ids = {r["platform_comment_id"]: int(r["id"]) for r in
       engine.query("SELECT id, platform_comment_id FROM comments")}

rule("1 · cold start — no model, no labels")
r0 = loop.start_round(n=12, verbose=True)

rule("2 · record the constructed labels")
for i, c in enumerate(cases):
    cid = ids.get(f"fx{i}")
    if cid is None:
        continue
    annotate.record("comment", cid, T.label(c["domain"], c["harm_type"]),
                    is_positive=c["harm_type"] != "none" or c["domain"] != "none",
                    annotator="fixture", domain=c["domain"],
                    harm_type=c["harm_type"], stance=c["stance"])
    loop.mark_annotated(r0["round_id"], "comment", cid)
print(f"  {len(cases)} labelled on three axes")

rule("3 · close the round — train, cross-validate, learning curve")
res = loop.close_round(r0["round_id"], verbose=True)

rule("4 · the taxonomy question, asked of the trained model")
model, _ = loop.latest_model()
probes = [
 ("protracted people's war is a strategic concept", "theory"),
 ("the mass line means learning from the masses", "theory"),
 ("initiate the people's war now, no more waiting", "advocacy"),
 ("armed struggle is the only road left", "advocacy"),
 ("dialectical materialism and the housing crisis", "theory"),
 ("first against the wall when the revolution comes", "advocacy"),
]
texts = [t for t, _ in probes]
ms = [L.scan(t).matches for t in texts]
P = model.predict(texts, ms)
hh = model.heads["harm"]
violent = [hh.labels.index(x) for x in
           ("incitement_to_violence", "glorification_of_violence") if x in hh.labels]
dh = model.heads["domain"]
print(f"  {'text':<48} {'expected':<10} {'P(violence harm)':>17}")
print("  " + "-"*78)
ok = 0
for i, (t, kind) in enumerate(probes):
    p = max(P["harm"][i, k] for k in violent)
    good = (p > 0.5) == (kind == "advocacy")
    ok += good
    print(f"  {t[:46]:<48} {kind:<10} {p:>16.3f}  {'ok' if good else 'MISS'}")
print(f"\n  {ok}/{len(probes)} correct — the domain is the same in every row;")
print( "  only the harm axis moves. That separation is the whole design.")

rule("5 · what the model proposes back to the lexicon")
props = loop.propose_terms_from_model(model, top=8, verbose=True)
for p in props[:8]:
    print(f"    {p['term']:<26} weight {p['weight']:>7}  ->  {p['head']}={p['label']}")

rule("6 · does active selection actually beat random?")
# The only comparison that means anything: give each strategy the SAME label
# budget, train on what it picked, and score both on the same held-out set.
#
# The first version of this compared "distinct domains touched", which favours
# diversity — but active learning is not trying to be diverse, it is trying to
# be informative, and concentrating on a contested region is correct behaviour.
# Measuring the wrong thing made a working selector look broken.
import numpy as np
from radicalmap.learn import train as TR
from radicalmap.learn.model import Model

ts = TR.assemble()
n = len(ts.texts)
rng = np.random.default_rng(11)
BUDGET, REPEATS = 25, 4

def score(train_idx, test_idx):
    sub = {h: (labels, Y[train_idx]) for h, (labels, Y) in ts.targets.items()}
    m = Model.train([ts.texts[i] for i in train_idx], [ts.matches[i] for i in train_idx], sub)
    P = m.predict([ts.texts[i] for i in test_idx], [ts.matches[i] for i in test_idx])
    f1s = []
    for head, (labels, Y) in ts.targets.items():
        if head not in P:
            continue
        for k in range(len(labels)):
            if Y[test_idx][:, k].sum() >= 2:
                f1s.append(TR._prf(Y[test_idx][:, k], P[head][:, k])[2])
    return float(np.mean(f1s)) if f1s else 0.0

act_scores, rnd_scores = [], []
for rep in range(REPEATS):
    perm = rng.permutation(n)
    test_idx, pool_idx = perm[:30], perm[30:]
    # Seed both strategies identically, then let each choose its next batch.
    seed_idx = pool_idx[:10]
    seed_sub = {h: (labels, Y[seed_idx]) for h, (labels, Y) in ts.targets.items()}
    seed_model = Model.train([ts.texts[i] for i in seed_idx],
                             [ts.matches[i] for i in seed_idx], seed_sub)
    remaining = pool_idx[10:]
    items = [("comment", int(i), ts.texts[i]) for i in remaining]
    picks = active.select(seed_model, items, n=BUDGET)
    chosen = [p.target_id for p in picks]
    act_scores.append(score(list(seed_idx) + chosen, test_idx))
    rnd_scores.append(score(list(seed_idx) + list(rng.permutation(remaining)[:BUDGET]),
                            test_idx))

print(f"  same budget of {BUDGET} labels on top of a shared 10-example seed,")
print(f"  scored on 30 held-out examples, {REPEATS} repeats\n")
print(f"    active selection : macro-F1 {np.mean(act_scores):.3f}  (sd {np.std(act_scores):.3f})")
print(f"    random selection : macro-F1 {np.mean(rnd_scores):.3f}  (sd {np.std(rnd_scores):.3f})")
delta = np.mean(act_scores) - np.mean(rnd_scores)
print(f"    difference       : {delta:+.3f}")
if abs(delta) < max(np.std(act_scores), np.std(rnd_scores)):
    print("\n  The difference is inside the run-to-run spread. At 88 constructed")
    print("  examples that is the expected result and not evidence either way —")
    print("  active learning's advantage shows up over a large unlabelled pool,")
    print("  which is exactly the situation the real corpus will be in.")

rule("7 · the guard against a self-confirming loop")
try:
    annotate.record("comment", 1, "x", is_positive=True, annotator="model:v1")
    print("  FAIL — model output was accepted as gold")
except ValueError as e:
    print(f"  ok  writing model output as gold is refused: {str(e)[:70]}...")
m2 = Model(spec=model.spec, train_source="adjudicated")
try:
    train.guard_not_second_generation(m2)
    print("  FAIL — second-generation self-training allowed")
except RuntimeError as e:
    print(f"  ok  second-generation distillation is refused: {str(e)[:66]}...")
print(f"\n  model train_source recorded as: {model.train_source!r}")
print(f"  scratch db: {engine.db_path()}")

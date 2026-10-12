#!/usr/bin/env python
"""The learning loop: features, model, training guards, selection."""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_DB"] = str(tmp / "l.db")
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")

from radicalmap import taxonomy as T
from radicalmap.classify import annotate, lexicon as L
from radicalmap.collect.adapters.base import CollectResult, ParsedAccount, ParsedComment, ParsedItem
from radicalmap.collect.persist import persist
from radicalmap.db import engine, migrate
from radicalmap.db.repo import writers as W
from radicalmap.learn import active, features as F, loop, train
from radicalmap.learn.model import Model

fails = []
def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label[:70]:<70}" + ("" if ok else f" {got!r} != {want!r}"))
def section(s): print(f"\n  ── {s} " + "─" * max(0, 58 - len(s)))

section("features")
ov = lambda a, b: len(set(a) & set(b)) / max(len(set(a) | set(b)), 1)
d1, d2, d3 = F.extract("sieg heil"), F.extract("siegheil"), F.extract("lovely harbour")
check("char n-grams survive concatenation", ov(d1, d2) > 3 * ov(d1, d3))
check("leetspeak folds", F.normalise("s13g h31l b4sed"), "sieg heil based")
check("lexicon matches become features",
      any(k in F.extract("1488", L.scan("1488").matches) for k in
          [F._h("lex:num-1488", F.DIM)]))
X = F.build([F.extract("a b c"), F.extract(""), F.extract("d e")])
check("empty documents do not break the matrix", X.n_rows, 3)
Wt = np.ones((X.dim, 3))
check("dot handles an empty row", X.dot(Wt).shape, (3, 3))
# The vectorised products must equal the naive per-class form.
Wr = np.random.default_rng(0).normal(size=(X.dim, 3)) * 0.1
ref = np.column_stack([np.bincount(X.rows, weights=X.data * Wr[X.indices, k],
                                   minlength=X.n_rows) for k in range(3)])
check("vectorised dot == naive form", bool(np.allclose(X.dot(Wr), ref)))

section("model")
pos = ["sieg heil lads", "sieg heil 1488", "heil hitler now", "sieg heil brothers"]
neg = ["lovely sunset", "great recipe", "my dog is cute", "football tonight"]
texts = pos + neg
ms = [L.scan(t).matches for t in texts]
Y = np.array([[1]] * 4 + [[0]] * 4, dtype=float)
m = Model.train(texts, ms, {"harm": (["x"], Y)})
p = m.predict(["sieg heil comrades", "beautiful morning"], 
              [L.scan(t).matches for t in ["sieg heil comrades", "beautiful morning"]])["harm"]
check("separates positive from negative", bool(p[0, 0] > 0.5 and p[1, 0] < 0.5))
path = m.save(tmp / "m.npz")
m2 = Model.load(path)
check("round-trips through disk",
      bool(np.allclose(m.predict(texts, ms)["harm"], m2.predict(texts, ms)["harm"])))
check("temperature defaults to uncalibrated", m.heads["harm"].calibrated, False)
m.calibrate(texts, ms, {"harm": Y})
check("calibration sets a temperature", m.heads["harm"].calibrated)
ex = m.explain("sieg heil lads", L.scan("sieg heil lads").matches, "harm", "x")
check("explains a prediction with named features", len(ex) > 0)
check("...and the features are readable, not hashes",
      all(isinstance(t, str) and not t.isdigit() for t, _ in ex))

section("provenance guards")
migrate.migrate(verbose=False)
src = W.ensure_source("t", "import", "")
persist(CollectResult(items=[ParsedItem(
    platform_item_id="p1", caption="cap",
    account=ParsedAccount("a", handle="h"),
    comments=[ParsedComment(f"c{i}", t, author_handle=f"u{i}")
              for i, t in enumerate(texts)])]), "tiktok", src, "t")
cid = engine.scalar("SELECT min(id) FROM comments")
try:
    annotate.record("comment", cid, "x", is_positive=True, annotator="model:v1")
    check("model output refused as gold", False)
except ValueError:
    check("model output refused as gold", True)
engine.execute("INSERT INTO gold_annotations (target_type,target_id,label,is_positive,"
               "annotator) VALUES ('comment',?,'x',1,'model:sneaky')", (cid,))
try:
    train.assemble(); check("assemble refuses model-authored gold", False)
except RuntimeError as e:
    check("assemble refuses model-authored gold", "not ground truth" in str(e))
engine.execute("DELETE FROM gold_annotations WHERE annotator LIKE 'model:%'")
try:
    train.guard_not_second_generation(Model(spec=F.FeatureSpec(), train_source="adjudicated"))
    check("second-generation self-training refused", False)
except RuntimeError:
    check("second-generation self-training refused", True)
train.guard_not_second_generation(Model(spec=F.FeatureSpec(), train_source="gold"))
check("a gold-trained model may propose terms", True)

section("gating flows into training")
ts = train.assemble()
check("gated domains excluded from the label set while shut",
      any(d in ts.targets["domain"][0] for d in T.GATED_DOMAINS), False)
os.environ["RM_DPIA_REF"] = "DPIA-TEST"
ts2 = train.assemble()
check("...and included once the gate is open",
      all(d in ts2.targets["domain"][0] for d in T.GATED_DOMAINS))
del os.environ["RM_DPIA_REF"]

section("metric discipline")
check("min support is enforced", train.MIN_SUPPORT >= 8)
res = train.cross_validate(train.assemble())
check("too little data reports an error, not a number", "error" in res)

section("selection")
items = [("comment", i, t) for i, t in enumerate(texts * 3)]
picks = active.select(None, items, n=5)
check("cold start selects without a model", len(picks) > 0)
check("every pick records why it was chosen", all(p.reason for p in picks))
check("near-duplicates are filtered",
      len({p.text.strip().lower() for p in picks}), len(picks))
picks2 = active.select(m, items, n=5)
check("selects with a model too", len(picks2) > 0)
u = active.uncertainty({"h": np.array([[0.5], [0.99], [0.01]])})
check("uncertainty peaks at the boundary", int(np.argmax(u)), 0)

print()
if fails: sys.exit(f"FAILED: {len(fails)} check(s):\n    " + "\n    ".join(fails))
print("  all checks passed")

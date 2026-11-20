#!/usr/bin/env python
"""Dataset export and evaluation report.

The export must not leak handles by default and must describe itself honestly.
The report must refuse rather than print a number it cannot stand behind.
"""
from __future__ import annotations

import hashlib, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")
os.environ.pop("RM_ALLOW_NETWORK", None)

from radicalmap import demo
demo.run(tmp / "demo.db", verbose=False)

from radicalmap.core import identity
from radicalmap.classify import runs
from radicalmap.db import engine
from radicalmap.research import evalreport as R
from radicalmap.research.package import export

fails = []
def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label:<56} {got!r}" + ("" if ok else f" != {want!r}"))

# ── export ──────────────────────────────────────────────────────────────────
print("\n  ── dataset package")
out = tmp / "pkg"
meta = export(out, version="test.1")
files = {p.name for p in out.iterdir()}
check("datapackage + datasheet written", {"datapackage.json", "DATASHEET.md"} <= files)
check("one CSV per declared resource", {r["path"] for r in meta["resources"]} <= files)
check("every hash matches its file", all(
    r["hash"] == "sha256:" + hashlib.sha256((out / r["path"]).read_bytes()).hexdigest()
    for r in meta["resources"]))
blob = "".join((out / r["path"]).read_text() for r in meta["resources"])
check("no handle survives a default export",
      [h for h in ("lanternmoth88", "quietharbor", "harbourcook", "historyreader") if h in blob], [])
check("item urls dropped by default", "example.invalid/demo" in blob, False)
check("metadata records pseudonymisation", meta["radicalmap"]["pseudonymised"], True)
salt = identity.load_salt()
accounts = (out / "accounts.csv").read_text()
check("account_ref is the same keyed hash commenters carry",
      identity.author_hash("lanternmoth88", salt) in accounts)
sheet = (out / "DATASHEET.md").read_text()
check("datasheet says pseudonymised, not anonymous", "Pseudonymised, not anonymous" in sheet)
check("datasheet refuses accuracy claims without gold", "no accuracy figures can be claimed" in sheet)

meta2 = export(tmp / "pkg2", include_handles=True)
check("--include-handles keeps handles", "lanternmoth88" in (tmp / "pkg2" / "items.csv").read_text()
      or "lanternmoth88" in (tmp / "pkg2" / "accounts.csv").read_text())
check("--include-handles is recorded", meta2["radicalmap"]["include_handles"], True)

# ── eval report ─────────────────────────────────────────────────────────────
print("\n  ── eval report")
def refused(run_id=None):
    try:
        R.build(run_id)
    except R.ReportRefused as e:
        return str(e)[:40]
    return None

check("refuses with no gold set", refused() is not None)

items = [int(r["id"]) for r in engine.query("SELECT id FROM items ORDER BY id LIMIT 4")]
for i, (iid, pos) in enumerate(zip(items, (1, 1, 0, 0))):
    engine.execute("INSERT INTO gold_annotations (target_type, target_id, label, is_positive, annotator)"
                   " VALUES ('item', ?, 'white_nationalist', ?, 'tester')", (iid, pos))
check("refuses with gold but no adjudication run", refused() is not None)

stub = runs.start("llm", "t", model="stub:deterministic")
check("refuses a stub run", "stub" in (refused(stub) or ""))

real = runs.start("llm", "t", model="test-model")
for iid in (items[0], items[2]):   # one true positive, one false positive
    engine.execute("INSERT INTO classifications (target_type, target_id, run_id, stage, label, verdict)"
                   " VALUES ('item', ?, ?, 'llm', 'white_nationalist', 'confirmed')", (iid, real))
data = R.build(real)
wn = data["labels"][0]
check("precision = 1 tp / (1 tp + 1 fp)", wn["precision"], 0.5)
check("recall = 1 tp / 2 positives", wn["recall"], 0.5)
check("confusion counted", wn["confusion"], {"tp": 1, "fp": 1, "fn": 1, "tn": 1})
check("run identity recorded", (data["run"]["id"], data["run"]["model"]), (real, "test-model"))
R.write(tmp / "rep", data)
check("report.md and report.json written",
      {p.name for p in (tmp / "rep").iterdir()}, {"report.md", "report.json"})
check("small support flagged", "indicative only" in (tmp / "rep" / "report.md").read_text())

if fails: sys.exit(f"FAILED: {len(fails)} check(s): {fails}")
print("\n  all checks passed")

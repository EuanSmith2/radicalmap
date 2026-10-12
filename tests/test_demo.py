#!/usr/bin/env python
"""`radicalmap demo` must demonstrate detection, not just boot.

Runs the demo into a temp file and checks the properties a reviewer is told to
look for: candidates at every tier, silent clean controls, hard negatives
surfaced for adjudication, and real network structure. Offline throughout.
"""
from __future__ import annotations

import os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")
os.environ.pop("RM_ALLOW_NETWORK", None)

from radicalmap import demo

fails = []
def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label:<56} {got!r}" + ("" if ok else f" != {want!r}"))

sentinel = tmp / "research.db"
sentinel.write_text("not a database; the demo must never touch this")
os.environ["RADICALMAP_DB"] = str(sentinel)

out = demo.run(tmp / "demo.db", verbose=False)
c = out["candidates"]
check("demo bound its own database", out["db"], str(tmp / "demo.db"))
check("pre-existing RADICALMAP_DB file untouched",
      sentinel.read_text(), "not a database; the demo must never touch this")
check("every tier-A case proposed at A", all(c.get(t) == "A" for t in demo.TIER_A))
check("every tier-B case proposed at B", all(c.get(t) == "B" for t in demo.TIER_B))
check("two tier-C signals pass the gate", all(c.get(t) == "C" for t in demo.TWO_C))
check("clean controls produce nothing", [t for t in demo.CLEAN if t in c], [])
check("hard negatives are surfaced for adjudication", all(t in c for t in demo.HARD_NEGATIVE))
from radicalmap.db import engine
check("lexicon writes candidates only, never findings",
      engine.scalar("SELECT count(*) FROM classifications WHERE verdict != 'candidate'"), 0)
check("shared sound forms a tie", out["edges"].get("shares_sound", 0) > 0)
check("@-mention forms a tie", out["edges"].get("mentions", 0) > 0)
check("obfuscated link + link-in-bio form links_to", out["edges"].get("links_to", 0) > 0)
check("more than one community", (out["network"].get("communities") or 0) > 1)

if fails: sys.exit(f"FAILED: {len(fails)} check(s): {fails}")
print("\n  all checks passed")

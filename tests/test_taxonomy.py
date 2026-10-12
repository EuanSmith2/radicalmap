#!/usr/bin/env python
"""The three-axis taxonomy.

The central property under test: a political domain alone is never a finding.
This is what separates a research instrument from a political surveillance one,
so it is asserted rather than assumed.
"""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_DB"] = str(tmp / "t.db")
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")
os.environ.pop("RM_DPIA_REF", None)

from radicalmap import taxonomy as T
from radicalmap.classify import lexicon as L
from radicalmap.db import engine, migrate

fails = []
def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label[:70]:<70}" + ("" if ok else f" {got!r} != {want!r}"))
def section(s): print(f"\n  ── {s} " + "─" * max(0, 58 - len(s)))

section("a political domain alone is never a finding")
for dom in ("marxist_leninist", "maoist", "insurrectionary_anarchist",
            "ethnonationalist", "manosphere_incel", "mainstream_political"):
    check(f"{dom} + no harm + promoting", T.is_finding(dom, "none", "promoting"), False)
    check(f"{dom} + harm=None", T.is_finding(dom, None, "promoting"), False)

section("a harm in a non-promoting stance is never a finding")
for stance in ("discussing", "reporting", "countering", "recovery", "help_seeking"):
    check(f"incitement + {stance}",
          T.is_finding("maoist", "incitement_to_violence", stance), False)

section("all three axes together")
check("maoist + incitement + promoting", T.is_finding("maoist", "incitement_to_violence", "promoting"))
check("neo_nazi + dehumanisation + promoting", T.is_finding("neo_nazi", "dehumanisation", "promoting"))
check("accelerationist_lw + glorification + glorifying",
      T.is_finding("accelerationist_lw", "glorification_of_violence", "glorifying"))
check("mainstream_political can never be a finding",
      T.is_finding("mainstream_political", "incitement_to_violence", "promoting"), False)

section("self-directed harm: the stances that protect people")
check("ED promotion in a recovery stance is not a finding",
      T.is_finding("eating_disorder", "disordered_eating_promotion", "recovery"), False)
check("self-harm content from someone seeking help is not a finding",
      T.is_finding("self_harm", "self_harm_promotion", "help_seeking"), False)
check("help_seeking is not finding-eligible anywhere",
      "help_seeking" in T.FINDING_STANCES, False)
check("recovery is not finding-eligible anywhere",
      "recovery" in T.FINDING_STANCES, False)

section("the Art. 9 gate")
check("gate is shut by default", T.gate_open(), False)
for dom in ("self_harm", "eating_disorder", "nihilistic_violent_extremism"):
    try:
        T.check_gate(dom); check(f"{dom} refused while shut", False)
    except PermissionError:
        check(f"{dom} refused while shut", True)
check("gated entries are excluded from scanning",
      any(e.gated for e in L.active_entries()), False)
check("...and are present in the full lexicon",
      len([e for e in L.ALL_ENTRIES if e.gated]) > 0)
ed = L.scan("edtwt ugw 95 thinspo")
check("ED text produces no matches while gated", len(ed.matches), 0)

os.environ["RM_DPIA_REF"] = "DPIA-TEST-001"
check("gate opens with a reference", T.gate_open())
T.check_gate("eating_disorder")
check("gated domain permitted once open", True)
ed2 = L.scan("edtwt ugw 95 thinspo")
check("ED text now matches", len(ed2.matches) >= 1)
check("...and is tier B, never A", ed2.top_tier, "B")
del os.environ["RM_DPIA_REF"]

section("severity ceilings")
check("conspiracy_targeting_group caps at medium",
      T.cap_severity("conspiracy_targeting_group", "high"), "medium")
check("incitement may be high", T.cap_severity("incitement_to_violence", "high"), "high")
check("a low severity is left alone", T.cap_severity("incitement_to_violence", "low"), "low")

section("the lexicon carries the axes")
maoist_theory = L.scan("protracted people's war and the mass line")
check("maoist theory matches a domain marker",
      any(m.domain == "maoist" for m in maoist_theory.matches))
check("...with no harm on any match",
      all(m.harm == "none" for m in maoist_theory.matches))
advocacy = L.scan("initiate the people's war now, armed struggle is the only road")
check("advocacy carries a harm",
      any(m.harm == "incitement_to_violence" for m in advocacy.matches))
check("jihadist entries do not match ordinary religious vocabulary",
      len(L.scan("the ummah gathered for jummah prayers, alhamdulillah").matches), 0)
check("...nor a neutral mention of jihad as a concept",
      len(L.scan("the greater jihad is the struggle against one's own ego").matches), 0)
check("counter-speech still matches (recall is preserved)",
      len(L.scan("the great replacement is a debunked conspiracy").matches) >= 1)

section("database sync")
migrate.migrate(verbose=False)
check("domains synced", engine.scalar("SELECT count(*) FROM domains"), len(T.DOMAINS))
check("harm types synced", engine.scalar("SELECT count(*) FROM harm_types"), len(T.HARM_TYPES))
check("stances synced", engine.scalar("SELECT count(*) FROM stances"), len(T.STANCES))
check("gated domains flagged in the table",
      engine.scalar("SELECT count(*) FROM domains WHERE requires_dpia=1"), len(T.GATED_DOMAINS))
check("sync is idempotent", (T.sync(), engine.scalar("SELECT count(*) FROM domains"))[1],
      len(T.DOMAINS))

print()
if fails: sys.exit(f"FAILED: {len(fails)} check(s):\n    " + "\n    ".join(fails))
print("  all checks passed")

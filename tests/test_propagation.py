#!/usr/bin/env python
"""Propagation: edges, network measures, drift, saturation, identity, scoring."""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_DB"] = str(tmp / "p.db")
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")

from radicalmap.analyse import metrics, network
from radicalmap.collect.adapters.base import CollectResult, ParsedAccount, ParsedComment, ParsedItem, ParsedSound
from radicalmap.collect.adapters.synthetic import SyntheticAdapter
from radicalmap.collect.persist import persist
from radicalmap.db import engine, migrate
from radicalmap.db.repo import writers as W
from radicalmap.propagate import edges as E, identity, scoring
from radicalmap.propagate.control import DriftMonitor, SaturationMonitor
from radicalmap.propagate.engine import Propagator, already_fetched, text_of

fails = []
def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label[:70]:<70}" + ("" if ok else f" {got!r} != {want!r}"))
def section(s): print(f"\n  ── {s} " + "─" * max(0, 58 - len(s)))

migrate.migrate(verbose=False)
src = W.ensure_source("t", "import", "")
items = []
for i in range(6):
    items.append(ParsedItem(
        platform_item_id=f"v{i}",
        caption=f"remigration great replacement europe #tag{i % 3} #fyp",
        account=ParsedAccount(f"acct{i % 3}", handle=["lanternmothalt", "lanternmothpost", "cookingfan"][i % 3]),
        sound=ParsedSound(f"snd{i % 2}", title=f"sound {i % 2}"),
        tags=[f"tag{i % 3}", "fyp"],
        comments=[ParsedComment(f"c{i}", "noticing the pattern", author_handle=f"u{i}")]))
persist(CollectResult(items=items), "tiktok", src, "t")
metrics.rebuild_all(verbose=False)

section("edge construction")
built = E.rebuild_all(verbose=False, with_audio=False)
check("authored edges exist", built["authored"] > 0)
check("tag edges exist", built["uses_tag"] > 0)
check("sound edges exist", built["uses_sound"] > 0)
check("accounts linked through shared sounds", built["shares_sound"] > 0)
check("all weights normalised to [0,1]",
      engine.scalar("SELECT count(*) FROM edges WHERE weight < 0 OR weight > 1"), 0)
fyp = engine.scalar("SELECT id FROM tags WHERE normalised='fyp'")
rare = engine.scalar("SELECT id FROM tags WHERE normalised='tag0'")
w_fyp = engine.scalar("SELECT max(weight) FROM edges WHERE edge_type='uses_tag' AND dst_id=?", (fyp,))
w_rare = engine.scalar("SELECT max(weight) FROM edges WHERE edge_type='uses_tag' AND dst_id=?", (rare,))
check("a ubiquitous tag is a WEAKER tie than a rare one (IDF)", w_fyp < w_rare)
check("no self-loops", engine.scalar(
    "SELECT count(*) FROM edges WHERE src_type=dst_type AND src_id=dst_id"), 0)

section("network measures")
g = network.Graph()
check("graph loads", g.n > 0)
pr = g.pagerank()
check("pagerank sums to 1", bool(abs(pr.sum() - 1.0) < 1e-6))
check("pagerank is non-negative", bool((pr >= 0).all()))
deg = g.degree()
check("degree matches the edge list", int(deg.sum()), g.src.size)
core = g.core_numbers()
check("core number never exceeds degree", bool((core <= deg).all()))
comms = g.communities()
check("every node gets a community", comms.size, g.n)
check("community ids are compact", int(comms.max()) + 1, len(np.unique(comms)))
bridge = g.bridge_scores(comms)
check("bridge scores are in [0,1]", bool(((bridge >= 0) & (bridge <= 1)).all()))
res = network.rebuild(verbose=False)
check("metrics persisted", engine.scalar("SELECT count(*) FROM node_metrics"), g.n)

section("drift control")
d = DriftMonitor()
check("an UNFITTED monitor is permissive, not blocking", d.drift("anything at all"), 0.0)
check("...and reports itself unfitted", d.fitted, False)
d.fit(["remigration great replacement europe", "1488 sieg heil", "kalergi plan noticing"])
check("fitted monitor knows its seed families", "far_right" in d.families)
on = d.drift("defend europe from the great replacement")
off = d.drift("my sourdough starter finally worked this time")
check("on-topic text scores low drift", on < 0.4)
check("off-topic text scores high drift", off > 0.7)
check("on-topic is clearly separated from off-topic", off - on > 0.4)
other = d.drift("dialectical materialism and the vanguard party")
check("a DIFFERENT flagged family sits between the two", on < other <= 1.0)
check("empty text is maximum drift", d.drift(""), 1.0)

section("saturation")
s = SaturationMonitor(interval=1, floor=0, patience=2)
s.tick(); s.tick(); s.tick()
check("samples recorded", len(s.samples) >= 3)
check("no new discoveries -> saturated", s.saturated)
check("...and persisted", engine.scalar("SELECT count(*) FROM saturation_samples") >= 3)

section("username identity")
identity.detect(verbose=False)
pairs = engine.query("SELECT a.handle FROM account_clusters c JOIN accounts a ON a.id=c.account_id")
handles = {r["handle"] for r in pairs}
check("the two 'lanternmoth' handles are clustered",
      {"lanternmothalt", "lanternmothpost"} <= handles)
check("the unrelated handle is not", "cookingfan" in handles, False)
check("an alt_of edge was written",
      engine.scalar("SELECT count(*) FROM edges WHERE edge_type='alt_of'") >= 1)
check("stem rarity gates a population-wide convention",
      identity.stem("user94"), "user")
check("version suffixes are recognised", identity.stem("handle.v2"), "handle")
check("shape similarity is symmetric",
      round(identity.shape_similarity("abcdef", "abcdeg"), 4),
      round(identity.shape_similarity("abcdeg", "abcdef"), 4))

section("scoring")
aid = engine.scalar("SELECT id FROM accounts LIMIT 1")
cands = scoring.expand("account", aid, 0)
check("expansion produces candidates", len(cands) > 0)
scoring.score(cands)
check("scores are ordered", all(cands[i].score >= cands[i+1].score for i in range(len(cands)-1)))
check("every candidate records its components", all(c.components for c in cands))
y = scoring.measured_yield()
check("priors are used before evidence exists", y["shares_sound"], scoring.PRIOR_YIELD["shares_sound"])
check("a shared sound is prior-weighted above a shared hashtag",
      scoring.PRIOR_YIELD["shares_sound"] > scoring.PRIOR_YIELD["shares_tag"])
scoring.note_fetched("shares_sound", items=5, candidates=5)
y2 = scoring.measured_yield()
check("one observation barely moves the prior (shrinkage)",
      abs(y2["shares_sound"] - y["shares_sound"]) < 0.05)

section("targets and re-fetch avoidance")
syn = SyntheticAdapter()
check("adapter builds its own target shape",
      syn.target_for("account", 1, {"platform_account_id": "synacct7"}),
      ("account_timeline", "syn://timeline/7"))
check("...and declines a node it cannot address",
      syn.target_for("account", 1, {"platform_account_id": "6812345"}), None)
from radicalmap.collect.adapters.tiktok import TikTokAdapter
check("tiktok builds a handle URL",
      TikTokAdapter().target_for("account", 1, {"handle": "x"}),
      ("account_timeline", "https://www.tiktok.com/@x"))
check("a derived sound id is not a fetchable page",
      TikTokAdapter().target_for("sound", 1, {"platform_sound_id": "tt:derived|x"}), None)
check("already_fetched is empty with nothing done", already_fetched(["a", "b"]), set())

section("node text is judged by produced content")
acct = engine.scalar("SELECT id FROM accounts WHERE handle='lanternmothalt'")
t = text_of("account", acct)
check("an account's text includes its captions, not only its bio",
      "remigration" in t.lower())

section("a wave runs end to end")
p = Propagator(adapter=syn, frontier_size=5, per_wave=10, verbose=False)
w = p.wave()
check("wave completes", w.wave, 1)
check("candidates were considered", w.candidates > 0)
check("every decision was recorded",
      engine.scalar("SELECT count(*) FROM propagation_events") > 0)
check("decisions carry a reason", engine.scalar(
    "SELECT count(*) FROM propagation_events WHERE decision IS NULL"), 0)

print()
if fails: sys.exit(f"FAILED: {len(fails)} check(s):\n    " + "\n    ".join(fails))
print("  all checks passed")

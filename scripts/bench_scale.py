#!/usr/bin/env python
"""Synthetic scale harness.

The plan's Phase 0 exit criterion: prove the foundation reaches the target scale
before any of it is built on. Generates a corpus at the size the goldmine
implies and times the queries the UI will actually issue.

Writes to a throwaway DB; never touches the real one.
"""
from __future__ import annotations

import os, random, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

N_ITEMS    = int(os.environ.get("BENCH_ITEMS", 100_000))
N_ACCOUNTS = int(os.environ.get("BENCH_ACCOUNTS", 8_000))
N_TAGS     = int(os.environ.get("BENCH_TAGS", 3_000))
N_SOUNDS   = int(os.environ.get("BENCH_SOUNDS", 6_000))
COMMENTS_PER_ITEM = int(os.environ.get("BENCH_CPI", 10))

tmp = Path(tempfile.mkdtemp()) / "bench.db"
os.environ["RADICALMAP_DB"] = str(tmp)
os.environ["RADICALMAP_SALT_FILE"] = str(tmp.parent / ".salt")

from radicalmap.db import engine, migrate
from radicalmap.db.repo import writers as W

random.seed(1848)

WORDS = ("replacement great reset noticing based heritage europe tradition folk "
         "remigration clown world sunset reclaim awakening symbolism cartoons "
         "kalergi globalist pipeline cooking football recipe travel dog cat music "
         "tutorial review vlog").split()

def caption(rng: random.Random) -> str:
    return " ".join(rng.choices(WORDS, k=rng.randint(6, 20)))

def stage(label: str):
    class _T:
        def __enter__(self): self.t = time.perf_counter(); return self
        def __exit__(self, *a):
            self.dt = time.perf_counter() - self.t
            print(f"  {label:<34} {self.dt:7.2f}s")
    return _T()

print(f"building synthetic corpus: {N_ITEMS:,} items, {N_ACCOUNTS:,} accounts, "
      f"{N_TAGS:,} tags, {N_SOUNDS:,} sounds, ~{N_ITEMS*COMMENTS_PER_ITEM:,} comments")
migrate.migrate(verbose=False)
pid = W.platform_id("tiktok")
rng = random.Random(1848)
t_total = time.perf_counter()

with stage("accounts") as _:
    with engine.bulk_load():
        src = W.ensure_source("bench", "import", "synthetic")
        W.upsert_accounts([{
            "platform_id": pid, "platform_account_id": f"acct{i}", "handle": f"user{i}",
            "bio": caption(rng), "follower_count": rng.randint(10, 500_000),
            "source_id": src, "collection_method": "bench",
        } for i in range(N_ACCOUNTS)])
acct_ids = list(W.account_ids(pid, [f"acct{i}" for i in range(N_ACCOUNTS)]).values())

with stage("tags + sounds") as _:
    with engine.bulk_load():
        tag_ids = list(W.upsert_tags([f"tag{i}" for i in range(N_TAGS)]).values())
        sound_ids = list(W.upsert_sounds([{
            "platform_id": pid, "platform_sound_id": f"snd{i}",
            "title": caption(rng), "author_name": f"user{rng.randrange(N_ACCOUNTS)}",
            "duration_s": rng.uniform(5, 60), "source_id": src,
        } for i in range(N_SOUNDS)]).values())

BATCH = 10_000
with stage(f"items ({N_ITEMS:,})") as s_items:
    now = int(time.time())
    for start in range(0, N_ITEMS, BATCH):
        with engine.bulk_load():
            W.upsert_items([{
                "platform_id": pid, "platform_item_id": f"vid{i}",
                "account_id": rng.choice(acct_ids), "url": f"https://tiktok.com/@u/video/{i}",
                "caption": caption(rng), "posted_at": now - rng.randrange(365*24*3600),
                "view_count": rng.randint(100, 5_000_000), "like_count": rng.randint(0, 200_000),
                "comment_count": rng.randint(0, 5_000), "share_count": rng.randint(0, 50_000),
                "save_count": rng.randint(0, 20_000),
                "source_id": src, "collection_method": "bench",
                # ~5% of items @-mention another account, in the shape
                # persist() writes, so the mentions edge build is exercised.
                "raw_json": ({"mentions": [f"User{rng.randrange(N_ACCOUNTS)}"]}
                             if rng.random() < 0.05 else None),
            } for i in range(start, min(start + BATCH, N_ITEMS))])
item_ids = [r["id"] for r in engine.query("SELECT id FROM items")]

with stage("item_tags + item_sounds") as _:
    for start in range(0, len(item_ids), BATCH):
        chunk = item_ids[start:start + BATCH]
        with engine.bulk_load():
            W.link_item_tags([(i, rng.choice(tag_ids)) for i in chunk for _ in range(4)])
            W.link_item_sounds([(i, rng.choice(sound_ids)) for i in chunk])

n_comments = 0
with stage(f"comments (~{N_ITEMS*COMMENTS_PER_ITEM:,})") as s_comm:
    for start in range(0, len(item_ids), BATCH // 4):
        chunk = item_ids[start:start + BATCH // 4]
        rows = []
        for iid in chunk:
            for k in range(COMMENTS_PER_ITEM):
                rows.append({"item_id": iid, "platform_comment_id": f"c{iid}_{k}",
                             "author_hash": f"{rng.randrange(16**32):032x}",
                             "text": caption(rng), "like_count": rng.randint(0, 900),
                             "posted_at": now - rng.randrange(365*24*3600),
                             "source_id": src, "collection_method": "bench"})
        with engine.bulk_load():
            n_comments += W.upsert_comments(rows)

with stage("ANALYZE") as _:
    engine.execute("ANALYZE")

build_s = time.perf_counter() - t_total
size_mb = sum(p.stat().st_size for p in tmp.parent.glob("bench.db*")) / 1e6
print(f"\n  built in {build_s:.1f}s — {size_mb:.0f} MB on disk "
      f"({engine.scalar('SELECT count(*) FROM items'):,} items, {n_comments:,} comments)")

# ── the queries the UI actually issues ──────────────────────────────────────
QUERIES = [
  ("FTS: caption search + rank",
   "SELECT i.id, i.caption FROM items_fts f JOIN items i ON i.id=f.rowid "
   "WHERE items_fts MATCH 'replacement' ORDER BY rank LIMIT 50", ()),
  ("FTS: comment search",
   "SELECT c.id FROM comments_fts f JOIN comments c ON c.id=f.rowid "
   "WHERE comments_fts MATCH 'kalergi OR noticing' LIMIT 50", ()),
  ("Explorer: page by recency",
   "SELECT id, caption, view_count FROM items ORDER BY posted_at DESC LIMIT 100", ()),
  ("Explorer: deep page (offset 50k)",
   "SELECT id, caption FROM items ORDER BY posted_at DESC LIMIT 100 OFFSET 50000", ()),
  ("Explorer: keyset page (the right way)",
   "SELECT id, caption FROM items WHERE posted_at < ? ORDER BY posted_at DESC LIMIT 100",
   (int(time.time()) - 86400*180,)),
  ("Dashboard: volume by day",
   "SELECT posted_at/86400 AS d, count(*) FROM items GROUP BY d ORDER BY d DESC LIMIT 90", ()),
  ("Accounts: top by followers",
   "SELECT id, handle, follower_count FROM accounts ORDER BY follower_count DESC LIMIT 50", ()),
  ("Sounds: recurrence across accounts",
   "SELECT s.id, s.title, count(DISTINCT i.account_id) AS accts, count(*) AS uses "
   "FROM item_sounds isx JOIN items i ON i.id=isx.item_id JOIN sounds s ON s.id=isx.sound_id "
   "GROUP BY s.id ORDER BY accts DESC LIMIT 50", ()),
  ("Item detail: comments for one item",
   "SELECT id, text, like_count FROM comments WHERE item_id=? ORDER BY like_count DESC LIMIT 100",
   (item_ids[len(item_ids)//2],)),
  ("Graph: tag co-occurrence (top 500)",
   "SELECT a.tag_id AS a, b.tag_id AS b, count(*) AS n FROM item_tags a "
   "JOIN item_tags b ON a.item_id=b.item_id AND a.tag_id<b.tag_id "
   "GROUP BY a, b ORDER BY n DESC LIMIT 500", ()),
]

def plan_verdict(sql, params):
    """Read EXPLAIN QUERY PLAN properly.

    A line is a genuine full scan only if it starts with SCAN and names no
    index. 'SCAN items USING INDEX ...' is an ordered index walk, not a scan of
    the table, and a temp b-tree means SQLite had to sort because no index
    supplied the order.
    """
    rows = [r["detail"] for r in engine.query("EXPLAIN QUERY PLAN " + sql, params)]
    # An FTS5 MATCH always reports as "SCAN <alias> VIRTUAL TABLE INDEX 0:M1".
    # That IS the inverted index doing its job, not a table scan — reporting it
    # as a full scan was a false alarm in the first version of this harness.
    if any("VIRTUAL TABLE INDEX" in d for d in rows):
        return "fts index"
    full = [d for d in rows if d.startswith("SCAN") and "USING" not in d]
    sort = [d for d in rows if "TEMP B-TREE" in d]
    if full:
        return "FULL SCAN: " + full[0].split()[1]
    if sort:
        return "indexed + sort"
    return "indexed"

def measure(sql, params, reps=5):
    engine.query(sql, params)
    ts = sorted(time.perf_counter() - t
                for t in (0,) * 0) or []
    ts = []
    for _ in range(reps):
        t = time.perf_counter(); engine.query(sql, params); ts.append(time.perf_counter() - t)
    return sorted(ts)[reps // 2] * 1000

print("\n  query timings (median of 5, warm cache)")
worst, worst_label = 0.0, ""
for label, sql, params in QUERIES:
    med = measure(sql, params)
    if med > worst:
        worst, worst_label = med, label
    flag = "!" if med > 100 else " "
    print(f"   {flag}{label:<40} {med:8.2f} ms   {plan_verdict(sql, params)}")

# ── the same co-occurrence question, answered from precomputed metrics ──────
from radicalmap.analyse import metrics
print("\n  rebuilding derived metrics (what the analyse phase does)")
t = time.perf_counter()
built = metrics.rebuild_all(verbose=True)
rebuild_s = time.perf_counter() - t

PRECOMPUTED = [
  ("Graph: co-occurrence, precomputed",
   "SELECT tag_a_id, tag_b_id, count, jaccard FROM tag_cooccurrence "
   "ORDER BY count DESC LIMIT 500", ()),
  ("Graph: co-occurrence by Jaccard",
   "SELECT tag_a_id, tag_b_id, jaccard FROM tag_cooccurrence ORDER BY jaccard DESC LIMIT 500", ()),
  ("Sounds: reach, precomputed",
   "SELECT sound_id, account_count, item_count FROM sound_stats "
   "ORDER BY account_count DESC LIMIT 50", ()),
  ("Dashboard: volume by day, precomputed",
   "SELECT day, item_count FROM daily_volume ORDER BY day DESC LIMIT 90", ()),
]
print(f"\n  precomputed equivalents (rebuild took {rebuild_s:.1f}s for the whole corpus)")
pre_worst = 0.0
for label, sql, params in PRECOMPUTED:
    med = measure(sql, params)
    pre_worst = max(pre_worst, med)
    print(f"    {label:<40} {med:8.2f} ms   {plan_verdict(sql, params)}")

# ── network edges (the analyse phase's other half) ──────────────────────────
from radicalmap.propagate import edges
# BENCH_EDGES: "all" (default), "none", or a comma list of builder names.
# shares_tag is O(sum of reach^2) over tags and dominates at scale (17M edges,
# ~640s at 100k items on the uniform synthetic corpus), so large runs can
# measure everything else without waiting hours on it.
which = os.environ.get("BENCH_EDGES", "all")
print(f"\n  rebuilding network edges (no audio; BENCH_EDGES={which})")
t = time.perf_counter()
if which == "all":
    edges.rebuild_all(verbose=True, with_audio=False)
elif which != "none":
    for name in which.split(","):
        t1 = time.perf_counter()
        n = getattr(edges, f"build_{name.strip()}")()
        print(f"    {name.strip():<14} {n:>9,} edges  {time.perf_counter()-t1:6.2f}s")
print(f"    total {time.perf_counter() - t:.1f}s")
plan = [r["detail"] for r in engine.query(
    "EXPLAIN QUERY PLAN SELECT i.id, a.id FROM items i, json_each(i.raw_json, '$.mentions') m "
    "JOIN accounts a ON a.platform_id = i.platform_id AND lower(a.handle) = lower(m.value) "
    "WHERE i.raw_json IS NOT NULL")]
print("    mentions join plan: " + " | ".join(plan))

# ── the console's own query functions, as the API calls them ────────────────
# The SQL list above approximates the UI; these are the real calls behind each
# tab, timed against the full store including edges.
from radicalmap.api import query as Q
from radicalmap.analyse import network as _net
t = time.perf_counter()
_net_out = _net.rebuild(verbose=False)
print(f"\n  network metrics (pagerank, k-core, communities, bridges): "
      f"{_net_out.get('nodes', 0):,} nodes in {time.perf_counter() - t:.1f}s")
some_item = item_ids[len(item_ids) // 2]
some_tag = engine.scalar("SELECT tag_id FROM item_tags LIMIT 1")
CONSOLE = [
    ("Dashboard: overview()", lambda: Q.overview()),
    ("Explorer: items() default page", lambda: Q.items()),
    ("Explorer: items(q='replacement')", lambda: Q.items(q="replacement")),
    ("Explorer: items(tag=...)", lambda: Q.items(tag=some_tag)),
    ("Item: item_detail()", lambda: Q.item_detail(some_item)),
    ("Findings: classifications()", lambda: Q.classifications()),
    ("Network: nodes()", lambda: Q.nodes()),
    ("Network: communities()", lambda: Q.communities()),
    ("Network: edge_types()", lambda: Q.edge_types()),
]
def time_call(fn, reps=5):
    fn()
    ts = []
    for _ in range(reps):
        t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
    return sorted(ts)[reps // 2] * 1000
print("\n  console API calls (median of 5)")
console_worst = 0.0
for label, fn in CONSOLE:
    med = time_call(fn)
    console_worst = max(console_worst, med)
    print(f"   {'!' if med > 100 else ' '}{label:<40} {med:8.2f} ms")
print(f"  worst console call: {console_worst:.1f} ms "
      f"({'PASS' if console_worst < 100 else 'FAIL'} — target <100ms)")

live = next(m for l, m in [(l, measure(s, p)) for l, s, p in QUERIES]
            if "co-occurrence" in l)
pre = measure(PRECOMPUTED[0][1], PRECOMPUTED[0][2])
print(f"\n  co-occurrence live {live:.1f} ms -> precomputed {pre:.2f} ms "
      f"({live/max(pre,1e-9):.0f}x)")

served = [(l, s, p) for l, s, p in QUERIES if "co-occurrence" not in l] + PRECOMPUTED
w = max(measure(s, p) for _, s, p in served)
print(f"\n  worst query actually served by the UI: {w:.1f} ms  "
      f"({'PASS' if w < 100 else 'FAIL'} — target <100ms)")
print(f"  (deep OFFSET is excluded from the UI path by design: keyset pagination)")
print(f"  scratch db: {tmp}")

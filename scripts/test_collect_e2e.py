#!/usr/bin/env python
"""Phase 1 milestone: 10k items collected end-to-end, surviving kill -9.

Runs the real engine, real frontier, real persistence path against a simulated
platform (see collect/adapters/synthetic.py) with transient failures, rate
limiting and permanently-gone targets injected — so retry, backoff, circuit
breaking and crash recovery are all exercised rather than assumed.

Also asserts the v1 defect is actually fixed: links posted in COMMENT text, not
captions, must end up in the links table. v1's external_links had 0 rows because
its Telegram regex never read comment text.
"""
from __future__ import annotations

import os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TARGET_ITEMS = int(os.environ.get("E2E_ITEMS", 10_000))
CORPUS = int(TARGET_ITEMS * 1.3)

tmp = Path(tempfile.mkdtemp())
DB = tmp / "e2e.db"
ENV = {**os.environ,
       "RADICALMAP_DB": str(DB),
       "RADICALMAP_SALT_FILE": str(tmp / ".salt"),
       "RM_WORKERS": "8",
       "RM_RPS": "100000", "RM_BURST": "500",     # pacing proven separately
       "RM_MAX_DEPTH": "2",
       "RM_EXPAND_ACCOUNTS": "0", "RM_EXPAND_TAGS": "0", "RM_EXPAND_SOUNDS": "0",
       "PYTHONPATH": str(ROOT)}

os.environ.update({k: v for k, v in ENV.items() if k.startswith(("RADICALMAP", "RM_"))})
from radicalmap.db import engine, migrate
from radicalmap.db.repo import jobs as Q, writers as W

fails = []
def check(label, got, want=True, op="=="):
    ok = (got == want) if op == "==" else (got >= want)
    if not ok: fails.append(label)
    sym = "==" if op == "==" else ">="
    print(f"  {'ok  ' if ok else 'FAIL'} {label:<50} {got!r}" + ("" if ok else f" {sym} {want!r}"))

# ── seed ────────────────────────────────────────────────────────────────────
migrate.migrate(verbose=False)
pid = W.platform_id("tiktok")
src = W.ensure_source("e2e", "seed_list", "synthetic")
t0 = time.perf_counter()
Q.enqueue([{"platform_id": pid, "job_type": "item", "target": f"syn://item/{i}",
            "source_id": src} for i in range(CORPUS)])
print(f"  seeded {CORPUS:,} targets in {time.perf_counter()-t0:.1f}s")
engine.close()

COLLECT = [sys.executable, "-m", "radicalmap", "collect",
           "--synthetic", str(CORPUS), "--max-items", str(TARGET_ITEMS),
           "--failure-rate", "0.04", "--rate-limit-rate", "0.02",
           "--gone-rate", "0.01", "--latency", "0.001", "--idle-timeout", "3"]

# ── run 1: killed mid-flight ────────────────────────────────────────────────
print("\n  run 1 — will be killed mid-collection")
p = subprocess.Popen(COLLECT, cwd=ROOT, env=ENV,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
t0 = time.perf_counter()
while time.perf_counter() - t0 < 12:
    time.sleep(0.5)
    n = int(engine.scalar("SELECT count(*) FROM items") or 0)
    if n > TARGET_ITEMS * 0.2:
        break
p.kill(); p.wait()
killed_items = int(engine.scalar("SELECT count(*) FROM items") or 0)
stranded = int(engine.scalar("SELECT count(*) FROM fetch_jobs WHERE status='running'") or 0)
print(f"  killed after {time.perf_counter()-t0:.1f}s — {killed_items:,} items stored, "
      f"{stranded} jobs stranded mid-flight")
check("kill landed mid-run, not after", 0 < killed_items < TARGET_ITEMS)

# ── run 2: resume to completion ─────────────────────────────────────────────
print("\n  run 2 — resuming")
t0 = time.perf_counter()
r = subprocess.run(COLLECT, cwd=ROOT, env=ENV, capture_output=True, text=True)
elapsed = time.perf_counter() - t0
tail = [l for l in r.stdout.splitlines() if l.strip()][-6:]
print("   " + "\n   ".join(tail))

# ── assertions ──────────────────────────────────────────────────────────────
print()
items = int(engine.scalar("SELECT count(*) FROM items") or 0)
check(f"collected >= {TARGET_ITEMS:,} items", items, TARGET_ITEMS, op=">=")
check("no duplicate platform_item_ids",
      int(engine.scalar("SELECT count(*) FROM (SELECT platform_item_id FROM items "
                        "GROUP BY platform_id, platform_item_id HAVING count(*)>1)") or 0), 0)
# Jobs still held by the killed worker are NOT lost — they are leased, and a
# lease cannot be reclaimed before it expires or two workers would fetch the
# same target. So the property to assert is recoverability, not that recovery
# has already happened: pin the clock past the lease and require every one back.
stranded = int(engine.scalar("SELECT count(*) FROM fetch_jobs WHERE status='running'") or 0)
if stranded:
    lease_end = int(engine.scalar(
        "SELECT max(lease_expires_at) FROM fetch_jobs WHERE status='running'") or 0)
    check("crashed worker's leases are all recoverable", Q.reap(now=lease_end + 1), stranded)
check("nothing stranded once leases expire",
      int(engine.scalar("SELECT count(*) FROM fetch_jobs WHERE status='running'") or 0), 0)
check("every item has an account",
      int(engine.scalar("SELECT count(*) FROM items WHERE account_id IS NULL") or 0), 0)
check("sounds captured (v1 discarded these)",
      int(engine.scalar("SELECT count(*) FROM sounds") or 0), 1, op=">=")
check("every item linked to a sound",
      int(engine.scalar("SELECT count(*) FROM items i LEFT JOIN item_sounds s "
                        "ON s.item_id=i.id WHERE s.item_id IS NULL") or 0), 0)
check("comments stored", int(engine.scalar("SELECT count(*) FROM comments") or 0), 1000, op=">=")
check("comment authors pseudonymised, none in plaintext",
      int(engine.scalar("SELECT count(*) FROM comments WHERE author_hash IS NULL") or 0), 0)
check("posted_at populated",
      int(engine.scalar("SELECT count(*) FROM items WHERE posted_at IS NULL") or 0), 0)
check("transcripts stored", int(engine.scalar("SELECT count(*) FROM transcripts") or 0), 1, op=">=")

# THE v1 BUG
from_comments = int(engine.scalar(
    "SELECT count(*) FROM links WHERE extracted_from='comment'") or 0)
check("links extracted from COMMENT text (v1 had 0)", from_comments, 1, op=">=")
obf = int(engine.scalar("SELECT count(*) FROM links WHERE context LIKE '%[obfuscated]%'") or 0)
check("obfuscated links recovered ('t dot me slash ...')", obf, 1, op=">=")
check("links from bios too",
      int(engine.scalar("SELECT count(*) FROM links WHERE extracted_from='bio'") or 0), 1, op=">=")

# failure handling actually exercised
dead = int(engine.scalar("SELECT count(*) FROM fetch_jobs WHERE status='skipped'") or 0)
check("permanent failures dead-lettered, not retried forever", dead, 1, op=">=")

# Transient failures back off before retrying, so within a ~20s run almost none
# come due again. The property is that each was recorded and re-scheduled, not
# that it was re-run inside the same short window.
rescheduled = int(engine.scalar(
    "SELECT count(*) FROM fetch_jobs WHERE status='pending' AND last_error IS NOT NULL") or 0)
check("transient failures recorded and re-queued", rescheduled, 1, op=">=")
check("each carries its error for triage",
      int(engine.scalar("SELECT count(*) FROM fetch_jobs WHERE status='pending' "
                        "AND attempts>0 AND last_error IS NULL") or 0), 0)
# Advance the clock past the backoff and confirm a retry is genuinely claimable.
# Isolate one known retry first: claim() orders across every due job, and with
# ~11k untouched targets still pending, a general claim returns a mix of fresh
# work and retries, which proves nothing either way.
retry_id = engine.scalar("SELECT id FROM fetch_jobs WHERE status='pending' "
                         "AND last_error IS NOT NULL AND attempts > 0 LIMIT 1")
engine.execute("UPDATE fetch_jobs SET next_attempt_at = unixepoch() + 86400 "
               "WHERE status='pending' AND id != ?", (retry_id,))
engine.execute("UPDATE fetch_jobs SET next_attempt_at = unixepoch() WHERE id = ?", (retry_id,))
claimed_retry = Q.claim(limit=1)
check("a backed-off job becomes claimable again",
      [j["id"] for j in claimed_retry], [retry_id])
check("the retry carries its prior attempt count forward",
      claimed_retry[0]["attempts"] > 1, True)
check("no job exceeded the attempt cap",
      int(engine.scalar("SELECT count(*) FROM fetch_jobs WHERE attempts>5") or 0), 0)

# metrics rebuild over the collected corpus
from radicalmap.analyse import metrics
t0 = time.perf_counter()
built = metrics.rebuild_all(verbose=False)
print(f"\n  metrics rebuilt in {time.perf_counter()-t0:.1f}s: "
      + ", ".join(f"{k}={v:,}" for k, v in built.items()))
check("sound reach measured across accounts", built["sound_stats"], 1, op=">=")

size_mb = sum(p.stat().st_size for p in tmp.glob("e2e.db*")) / 1e6
print(f"\n  {items:,} items, {int(engine.scalar('SELECT count(*) FROM comments')):,} comments, "
      f"{size_mb:.0f} MB")
print(f"  run 2 collected {items - killed_items:,} items in {elapsed:.1f}s "
      f"({(items - killed_items)/elapsed:.0f} items/s)")
print(f"  v1 equivalent at ~20s/video: {items*20/3600:.0f} hours of browser time")

print()
if fails:
    sys.exit(f"FAILED: {len(fails)} check(s): {fails}")
print("  all checks passed")

#!/usr/bin/env python
"""Prove the collection queue survives a worker being killed mid-fetch.

This is the property v1 had no way to provide: it had no job state at all, so a
crash lost everything in flight with no record that it had ever been attempted.
The plan's Phase 1 exit criterion is `kill -9` a worker and resume clean, so the
mechanism is verified here, before anything is built on it.
"""
from __future__ import annotations
import os, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = Path(tempfile.mkdtemp()) / "queue.db"
os.environ["RADICALMAP_DB"] = str(tmp)

from radicalmap.db import engine, migrate
from radicalmap.db.repo import jobs, writers as W

migrate.migrate(verbose=False)
pid = W.platform_id("tiktok")
fails = []

def check(label, got, want):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label:<52} {got!r}" + ("" if ok else f" != {want!r}"))

# ── enqueue, with duplicate suppression ─────────────────────────────────────
jobs.enqueue([{"platform_id": pid, "job_type": "item", "target": f"vid{i}"} for i in range(100)])
jobs.enqueue([{"platform_id": pid, "job_type": "item", "target": f"vid{i}"} for i in range(50)])
check("enqueue dedupes re-proposed targets", engine.scalar("SELECT count(*) FROM fetch_jobs"), 100)

# ── claim is atomic: no job goes to two workers ─────────────────────────────
a = jobs.claim(limit=10); b = jobs.claim(limit=10)
check("two claims return disjoint jobs", len({j["id"] for j in a} & {j["id"] for j in b}), 0)
check("20 jobs now running", engine.scalar("SELECT count(*) FROM fetch_jobs WHERE status='running'"), 20)

# ── simulate a worker dying with 10 jobs leased ─────────────────────────────
for j in a: jobs.complete(j["id"])
check("completed jobs recorded", engine.scalar("SELECT count(*) FROM fetch_jobs WHERE status='done'"), 10)

# Worker B dies: its leases are still held and will expire.
engine.execute("UPDATE fetch_jobs SET lease_expires_at = unixepoch() - 1 "
               "WHERE status='running'")
reaped = jobs.reap()
check("reap returns the dead worker's jobs", reaped, 10)
check("reaped jobs are pending again",
      engine.scalar("SELECT count(*) FROM fetch_jobs WHERE status='pending'"), 90)
check("reaped jobs record why",
      engine.scalar("SELECT last_error FROM fetch_jobs WHERE id=?", (b[0]["id"],)),
      "lease expired — worker died")
check("attempt count survived the crash",
      engine.scalar("SELECT attempts FROM fetch_jobs WHERE id=?", (b[0]["id"],)), 1)

# ── retry with backoff, then dead-letter ────────────────────────────────────
job = jobs.claim(1)[0]
outcomes = []
for _ in range(6):
    row = engine.query_one("SELECT id FROM fetch_jobs WHERE id=?", (job["id"],))
    outcomes.append(jobs.fail(job["id"], "HTTP 429"))
    engine.execute("UPDATE fetch_jobs SET next_attempt_at=unixepoch() WHERE id=?", (job["id"],))
    c = jobs.claim(1)
    if c and c[0]["id"] == job["id"]: continue
    break
check("retries then dead-letters", outcomes[-1], "dead")
check("dead job keeps its last error",
      engine.scalar("SELECT last_error FROM fetch_jobs WHERE id=?", (job["id"],)), "HTTP 429")
check("dead job is not re-claimable",
      any(j["id"] == job["id"] for j in jobs.claim(200)), False)

# ── backoff actually grows ──────────────────────────────────────────────────
engine.execute("DELETE FROM fetch_jobs")
jobs.enqueue([{"platform_id": pid, "job_type": "item", "target": "backoff"}])
delays = []
for _ in range(4):
    j = jobs.claim(1)
    if not j: break
    jobs.fail(j[0]["id"], "boom")
    nxt = engine.scalar("SELECT next_attempt_at - unixepoch() FROM fetch_jobs WHERE id=?", (j[0]["id"],))
    delays.append(nxt)
    engine.execute("UPDATE fetch_jobs SET next_attempt_at=unixepoch() WHERE id=?", (j[0]["id"],))
check("backoff is exponential", delays, [30, 60, 120, 240])

# ── real process kill -9, to be sure it is not just simulated ───────────────
engine.execute("DELETE FROM fetch_jobs")
jobs.enqueue([{"platform_id": pid, "job_type": "item", "target": f"real{i}"} for i in range(20)])
engine.close()

child = subprocess.Popen(
    [sys.executable, "-c", f'''
import os, sys, time
os.environ["RADICALMAP_DB"] = {str(tmp)!r}
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
from radicalmap.db.repo import jobs
claimed = jobs.claim(limit=5, lease_s=1)
print(len(claimed), flush=True)
time.sleep(60)
'''], stdout=subprocess.PIPE, text=True)
n = int(child.stdout.readline().strip())
child.kill(); child.wait()
print(f"  ---- child claimed {n} jobs, then killed -9 ----")

check("after kill -9, jobs are stranded in running",
      engine.scalar("SELECT count(*) FROM fetch_jobs WHERE status='running'"), 5)
# Pin the clock rather than sleeping: leases are stored in whole seconds, so a
# 1s lease can take up to 2s of wall time to read as expired. Sleeping "long
# enough" makes the test flaky for no benefit.
lease_end = engine.scalar("SELECT max(lease_expires_at) FROM fetch_jobs WHERE status='running'")
check("reap recovers them", jobs.reap(now=lease_end + 1), 5)
check("all work is available again",
      engine.scalar("SELECT count(*) FROM fetch_jobs WHERE status='pending'"), 20)
check("no duplicates created", engine.scalar("SELECT count(*) FROM fetch_jobs"), 20)

print()
if fails:
    sys.exit(f"FAILED: {len(fails)} check(s): {fails}")
print(f"  all checks passed — queue survives kill -9 with no lost or duplicated work")

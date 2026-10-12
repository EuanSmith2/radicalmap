"""The collection queue.

v1 had no job state of any kind: no status, no attempt count, no error column.
A failed URL was print()ed to stdout and forgotten, and the ingest queue itself
was a hand-pasted 19-line text file. Nothing could resume, and nothing could
tell you what had been tried.

Claiming is lease-based rather than a plain status flip, so a worker that is
kill -9'd mid-fetch does not strand its jobs in 'running' forever — the lease
expires and reap() returns them to the queue.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping, Sequence

from .. import engine

MAX_ATTEMPTS = 5
BASE_BACKOFF_S = 30
LEASE_S = 600


def enqueue(records: Iterable[Mapping[str, Any]]) -> int:
    """Add jobs. Idempotent on (platform, job_type, target), so the frontier can
    re-propose a target it has already seen without creating duplicate work."""
    rows = []
    for r in records:
        rows.append((
            r["platform_id"], r["job_type"], str(r["target"]),
            r.get("priority", 100), r.get("depth", 0),
            r.get("parent_job_id"), r.get("source_id"),
        ))
    if not rows:
        return 0
    cur = engine.connect().executemany(
        """INSERT INTO fetch_jobs (platform_id, job_type, target, priority, depth,
                                   parent_job_id, source_id)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT (platform_id, job_type, target) DO NOTHING""",
        rows)
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def claim(limit: int = 1, lease_s: int = LEASE_S) -> list[dict[str, Any]]:
    """Atomically take up to `limit` due jobs.

    BEGIN IMMEDIATE + UPDATE..RETURNING means two workers racing for the same
    row cannot both win it; the loser blocks on the write lock, then sees the
    row already claimed.
    """
    now = int(time.time())
    with engine.transaction() as conn:
        rows = conn.execute(
            """UPDATE fetch_jobs
                  SET status = 'running',
                      attempts = attempts + 1,
                      started_at = ?,
                      lease_expires_at = ?
                WHERE id IN (
                      SELECT id FROM fetch_jobs
                       WHERE status = 'pending' AND next_attempt_at <= ?
                       ORDER BY priority ASC, next_attempt_at ASC, id ASC
                       LIMIT ?)
            RETURNING id, platform_id, job_type, target, priority, attempts, depth,
                      parent_job_id, source_id""",
            (now, now + lease_s, now, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def complete(job_id: int) -> None:
    engine.execute(
        "UPDATE fetch_jobs SET status='done', finished_at=unixepoch(), "
        "lease_expires_at=NULL, last_error=NULL WHERE id=?",
        (job_id,),
    )


def fail(job_id: int, error: str, max_attempts: int = MAX_ATTEMPTS) -> str:
    """Record a failure. Retries with exponential backoff until max_attempts,
    then dead-letters — so a permanently gone video stops consuming budget but
    stays visible as a known outcome rather than vanishing."""
    row = engine.query_one("SELECT attempts FROM fetch_jobs WHERE id=?", (job_id,))
    attempts = int(row["attempts"]) if row else max_attempts
    if attempts >= max_attempts:
        engine.execute(
            "UPDATE fetch_jobs SET status='dead', last_error=?, finished_at=unixepoch(), "
            "lease_expires_at=NULL WHERE id=?",
            (error[:2000], job_id),
        )
        return "dead"
    delay = BASE_BACKOFF_S * (2 ** (attempts - 1))
    engine.execute(
        "UPDATE fetch_jobs SET status='pending', last_error=?, "
        "next_attempt_at=unixepoch()+?, lease_expires_at=NULL WHERE id=?",
        (error[:2000], delay, job_id),
    )
    return "retry"


def skip(job_id: int, reason: str) -> None:
    engine.execute(
        "UPDATE fetch_jobs SET status='skipped', last_error=?, finished_at=unixepoch(), "
        "lease_expires_at=NULL WHERE id=?",
        (reason[:2000], job_id),
    )


def reap(now: int | None = None) -> int:
    """Return jobs whose worker died holding the lease. This is what makes
    kill -9 mid-run safe."""
    now = int(time.time()) if now is None else now
    cur = engine.execute(
        """UPDATE fetch_jobs
              SET status = 'pending',
                  next_attempt_at = ?,
                  last_error = coalesce(last_error, 'lease expired — worker died'),
                  lease_expires_at = NULL
            WHERE status = 'running' AND lease_expires_at IS NOT NULL
              AND lease_expires_at < ?""",
        (now, now),
    )
    return cur.rowcount or 0


def stats() -> dict[str, Any]:
    rows = engine.query(
        "SELECT status, job_type, count(*) AS n FROM fetch_jobs GROUP BY status, job_type")
    by_status: dict[str, int] = {}
    by_type: dict[str, dict[str, int]] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + int(r["n"])
        by_type.setdefault(r["job_type"], {})[r["status"]] = int(r["n"])
    return {
        "by_status": by_status,
        "by_type": by_type,
        "total": sum(by_status.values()),
        "due_now": int(engine.scalar(
            "SELECT count(*) FROM fetch_jobs WHERE status='pending' "
            "AND next_attempt_at <= unixepoch()") or 0),
    }


def log(job_id: int | None, event: str, detail: str = "", level: str = "info") -> None:
    engine.execute(
        "INSERT INTO collection_events (job_id, level, event, detail) VALUES (?,?,?,?)",
        (job_id, level, event, detail[:4000]),
    )

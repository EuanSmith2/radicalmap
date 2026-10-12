"""Connection management for the v2 store.

v1's db/database.py opened a connection, ran one statement, committed and closed
it — for every single row. link_video_hashtag() did two statements plus a commit
per call; record_cooccurrence() one full connect/commit/fsync cycle per hashtag
pair, so a video with n tags cost O(n^2) fsyncs. That is the hard ceiling this
module removes.

Here: one pooled connection per thread, WAL, and explicit transactions that
callers hold open across many writes.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PACKAGE_ROOT.parent

DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "radicalmap.v2.db"
SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


def db_path() -> Path:
    """Active database file. RADICALMAP_DB overrides, which is how the test
    suite and the synthetic-scale harness point at a throwaway file."""
    return Path(os.environ.get("RADICALMAP_DB", DEFAULT_DB_PATH))


_local = threading.local()


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # WAL lets readers (the API) work while a writer (a collect worker) is busy.
    # v1 ran journal_mode=delete, so every commit locked the whole database.
    conn.execute("PRAGMA journal_mode = WAL")
    # NORMAL is the right durability trade for a corpus that is re-derivable by
    # re-running collection; FULL costs an fsync per commit for no real benefit.
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -64000")      # 64 MB page cache
    conn.execute("PRAGMA mmap_size = 268435456")    # 256 MB
    conn.execute("PRAGMA foreign_keys = ON")


def connect(path: Path | str | None = None, *, fresh: bool = False) -> sqlite3.Connection:
    """Return this thread's pooled connection, creating it on first use."""
    target = Path(path) if path is not None else db_path()
    if fresh:
        conn = sqlite3.connect(target, isolation_level=None, check_same_thread=False)
        _configure(conn)
        return conn

    existing = getattr(_local, "conn", None)
    if existing is not None and getattr(_local, "path", None) == str(target):
        return existing

    if existing is not None:
        existing.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: we drive transactions explicitly rather than letting
    # sqlite3 open one implicitly behind our back.
    conn = sqlite3.connect(target, isolation_level=None, check_same_thread=False)
    _configure(conn)
    _local.conn = conn
    _local.path = str(target)
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.execute("PRAGMA optimize")
        except sqlite3.Error:
            pass
        conn.close()
    _local.conn = None
    _local.path = None


@contextmanager
def transaction(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """Explicit unit of work. Nests safely: an inner `with transaction()` joins
    the outer one rather than committing halfway through it."""
    conn = conn or connect()
    depth = getattr(_local, "depth", 0)
    if depth == 0:
        conn.execute("BEGIN IMMEDIATE")
    _local.depth = depth + 1
    try:
        yield conn
    except Exception:
        _local.depth = depth
        if depth == 0:
            conn.execute("ROLLBACK")
        raise
    else:
        _local.depth = depth
        if depth == 0:
            conn.execute("COMMIT")


# ─── query helpers ───────────────────────────────────────────────────────────

def query(sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def query_one(sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, params).fetchone()


def scalar(sql: str, params: Sequence[Any] = ()) -> Any:
    row = connect().execute(sql, params).fetchone()
    return row[0] if row else None


def execute(sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
    return connect().execute(sql, params)


def executemany(sql: str, rows: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
    """Batch write. Every bulk path goes through here rather than looping
    execute() — this is the difference between minutes and hours at 100k."""
    return connect().executemany(sql, rows)


@contextmanager
def bulk_load() -> Iterator[sqlite3.Connection]:
    """Wrap a large import. Trades a little crash-safety for throughput, then
    restores the normal settings and leaves the FTS indexes consistent."""
    conn = connect()
    conn.execute("PRAGMA synchronous = OFF")
    try:
        with transaction(conn):
            yield conn
    finally:
        conn.execute("PRAGMA synchronous = NORMAL")


def rebuild_fts() -> None:
    """Rebuild the external-content FTS indexes from their source tables."""
    conn = connect()
    for table in ("items_fts", "comments_fts", "transcripts_fts"):
        conn.execute(f"INSERT INTO {table}({table}) VALUES('rebuild')")


def vacuum_analyze() -> None:
    conn = connect()
    conn.execute("ANALYZE")
    conn.execute("VACUUM")

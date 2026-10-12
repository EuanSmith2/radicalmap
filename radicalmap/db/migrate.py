"""Numbered SQL migrations.

Replaces v1's approach, which was analyse_text.migrate_db() wrapping each
ALTER TABLE in `try/except: pass`. That silently tolerated failure, and the
result was real drift: `save_count` exists in the live v1 database but not in
db/schema.sql, so the file and the artefact disagree with no record of why.

Here every migration is a numbered file applied exactly once inside one
transaction, recorded with its hash. No framework, no dependency.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

from . import engine

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
_FILENAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


def _ensure_table(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version    INTEGER PRIMARY KEY,
               name       TEXT NOT NULL,
               hash       TEXT NOT NULL,
               applied_at INTEGER NOT NULL DEFAULT (unixepoch())
           ) STRICT"""
    )


def discover() -> list[tuple[int, str, Path]]:
    found = []
    for path in sorted(SCHEMA_DIR.glob("*.sql")):
        m = _FILENAME.match(path.name)
        if not m:
            raise ValueError(f"migration filename must be NNN_name.sql: {path.name}")
        found.append((int(m.group(1)), m.group(2), path))
    versions = [v for v, _, _ in found]
    if len(set(versions)) != len(versions):
        raise ValueError(f"duplicate migration version among {versions}")
    return found


def applied(conn) -> dict[int, str]:
    _ensure_table(conn)
    return {r["version"]: r["hash"] for r in conn.execute(
        "SELECT version, hash FROM schema_migrations").fetchall()}


def migrate(verbose: bool = True) -> int:
    conn = engine.connect()
    done = applied(conn)
    count = 0

    for version, name, path in discover():
        sql = path.read_text()
        digest = hashlib.sha256(sql.encode()).hexdigest()

        if version in done:
            if done[version] != digest:
                # An applied migration was edited after the fact. Silently
                # re-running or ignoring it both produce a database that does
                # not match its own recorded history, so refuse.
                raise RuntimeError(
                    f"migration {version:03d}_{name}.sql changed after being applied "
                    f"(recorded {done[version][:12]}, file {digest[:12]}). "
                    f"Write a new migration instead of editing an applied one."
                )
            continue

        if verbose:
            print(f"  applying {version:03d}_{name}")
        # executescript() issues an implicit COMMIT before it runs, which would
        # end a transaction opened out here. So the transaction lives inside the
        # script, and the bookkeeping INSERT rides along in it — the migration
        # and the record of the migration commit together or not at all.
        # Inlining is injection-safe by construction: version is an int, name is
        # [a-z0-9_] from the filename regex, digest is hex from sha256.
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{sql}\n"
            "INSERT INTO schema_migrations (version, name, hash) VALUES "
            f"({version:d}, '{name}', '{digest}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        count += 1

    # Reference data derived from code. Synced after every migration so the
    # tables and radicalmap/taxonomy.py cannot drift apart. Imported here rather
    # than at module level to keep the migration runner free of dependencies.
    try:
        from .. import taxonomy
        taxonomy.sync()
    except Exception as exc:                        # noqa: BLE001
        if verbose:
            print(f"  taxonomy sync skipped: {exc}")

    if verbose:
        current = max(applied(conn), default=0)
        print(f"  schema at version {current:03d} ({count} applied this run)")
    return count


def status() -> None:
    conn = engine.connect()
    done = applied(conn)
    print(f"database: {engine.db_path()}")
    for version, name, _ in discover():
        mark = "applied" if version in done else "PENDING"
        print(f"  {version:03d}_{name:<28} {mark}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        migrate()

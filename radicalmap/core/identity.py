"""Pseudonymisation of commenter handles.

GDPR Art. 89(1) — the basis LEGAL.md relies on — expects safeguards, and names
data minimisation and pseudonymisation as the archetypal ones. At 18 videos the
point was academic. At hundreds of thousands of comments the corpus becomes a
large collection of identifiable personal data about private individuals who are
not the subject of the research, and pseudonymisation stops being optional.

Design: comments carry a keyed hash. The plaintext mapping lives in exactly one
table (author_identities) and one file (the salt). Withhold or drop either and
the corpus stays fully analysable while re-identification becomes infeasible —
which is precisely the property that makes an export or a deposit defensible.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from ..db import engine

SALT_ENV = "RADICALMAP_SALT_FILE"


def salt_path() -> Path:
    override = os.environ.get(SALT_ENV)
    if override:
        return Path(override)
    return engine.db_path().parent / ".author_salt"


def load_salt() -> bytes:
    """Read the local salt, creating it on first use with 0600 permissions.

    The salt is deliberately NOT stored in the database: a leaked or shared
    .db file must not itself be enough to reverse the hashes.
    """
    path = salt_path()
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(32)
    path.write_bytes(salt)
    path.chmod(0o600)
    return salt


def author_hash(handle: str, salt: bytes | None = None) -> str:
    """Keyed hash of a handle. blake2b in keyed mode rather than sha256(salt+x):
    it is a proper MAC, so it is not vulnerable to length-extension and there is
    no rainbow table for it without the key."""
    if salt is None:
        salt = load_salt()
    normalised = handle.strip().lstrip("@").lower()
    return hashlib.blake2b(normalised.encode("utf-8"), key=salt, digest_size=16).hexdigest()


def record_identity(handle: str, platform_id: int | None = None, salt: bytes | None = None) -> str:
    """Hash a handle and remember the mapping in the restricted side table."""
    h = author_hash(handle, salt)
    engine.execute(
        "INSERT OR IGNORE INTO author_identities (author_hash, handle, platform_id) VALUES (?,?,?)",
        (h, handle.strip().lstrip("@"), platform_id),
    )
    return h


def drop_identities() -> int:
    """Irreversibly sever re-identification. Run before any export or deposit
    that leaves this machine."""
    n = engine.scalar("SELECT count(*) FROM author_identities") or 0
    engine.execute("DELETE FROM author_identities")
    return int(n)

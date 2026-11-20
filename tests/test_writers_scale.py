#!/usr/bin/env python
"""Key lookups must not depend on SQLite's bound-parameter limit.

writers.account_ids/item_ids/upsert_tags/upsert_sounds used one placeholder
per key, so a lookup over 32,766 keys (999 on older SQLite) raised "too many
SQL variables". A 1M-item benchmark with 80k accounts died on its first lookup.
"""
from __future__ import annotations

import os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_DB"] = str(tmp / "w.db")
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")

from radicalmap.db import engine, migrate
from radicalmap.db.repo import writers as W

fails = []
def check(label, got, want):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label:<52} {got!r}" + ("" if ok else f" != {want!r}"))

N = 40_000   # above SQLite's 32,766 default limit
migrate.migrate(verbose=False)
pid = W.platform_id("tiktok")
with engine.bulk_load():
    src = W.ensure_source("t", "import", "t")
    W.upsert_accounts([{"platform_id": pid, "platform_account_id": f"a{i}", "handle": f"h{i}",
                        "source_id": src, "collection_method": "t"} for i in range(N)])
    ids = W.account_ids(pid, [f"a{i}" for i in range(N)])
    tags = W.upsert_tags([f"t{i}" for i in range(N)])
    W.upsert_items([{"platform_id": pid, "platform_item_id": f"v{i}", "source_id": src,
                     "collection_method": "t"} for i in range(N)])
    items = W.item_ids(pid, [f"v{i}" for i in range(N)])
    sounds = W.upsert_sounds([{"platform_id": pid, "platform_sound_id": f"s{i}",
                               "source_id": src} for i in range(N)])
check("account_ids over 40k keys", len(ids), N)
check("upsert_tags over 40k tags", len(tags), N)
check("item_ids over 40k keys", len(items), N)
check("upsert_sounds over 40k sounds", len(sounds), N)
check("ids map to the right rows",
      engine.scalar("SELECT platform_account_id FROM accounts WHERE id = ?", (ids["a39999"],)), "a39999")

if fails: sys.exit(f"FAILED: {len(fails)} check(s): {fails}")
print("\n  all checks passed")

"""`radicalmap demo` — the whole pipeline on a synthetic corpus, offline.

A fresh clone with no data, no network and no API key should still show what
the instrument does. This builds a throwaway database from a small invented
corpus, runs lexicon candidate generation and the network build over it, and
leaves it ready for `serve`.

The corpus is constructed, not collected: every handle and sentence is made up
(the sentences are the lexicon's own unit-test cases). It is shaped to show the
four things a reviewer needs to see:

  * candidates at every lexicon tier (A, B, and the two-C gate);
  * clean controls that produce nothing;
  * hard negatives — self-description, scholarship, unrelated homonyms — which
    the lexicon DOES propose, by design, because it is tuned for recall. Those
    are what the adjudication stage exists to reject, and showing them is more
    honest than a demo where the first stage looks perfect;
  * network structure: a shared sound, shared tags, an @-mention, and an
    obfuscated Telegram link in a reply, so communities and links_to edges form.
"""

from __future__ import annotations

import os
from pathlib import Path

from .core.config import PROJECT_ROOT

DEFAULT_DEMO_DB = PROJECT_ROOT / "db" / "radicalmap.demo.db"

# Captions, grouped by what the lexicon should do with them. All taken from the
# lexicon's own unit-test cases (tests/fixtures/lexicon_cases.json).
TIER_A = ["1488 brothers", "sik haj lads", "day of the rope is coming"]
TIER_B = ["have you started noticing yet", "StreSS test",
          "the great replacement is real, remigration now", "clown world honk honk"]
TWO_C = ["based, link in bio, deleting this later", "they always knew, wake up, iykyk"]
CLEAN = ["lovely sunset over the harbour", "this recipe is based",
         "wake up early for the gym", "link in bio for the tutorial"]
HARD_NEGATIVE = ["I am a jew", "the aryan languages of the Indo-Iranian branch",
                 "black sun photography at the eclipse",
                 "reading about the great replacement conspiracy for my dissertation"]


def build_corpus():
    from .collect.adapters.base import (CollectResult, ParsedAccount, ParsedComment,
                                        ParsedItem, ParsedSound)
    accounts = {
        "cluster": ParsedAccount("demo-acct-1", handle="lanternmoth88", display_name="Lantern Moth",
                                 bio="synthetic demo account", bio_link="t.me/examplechannel"),
        "cluster2": ParsedAccount("demo-acct-2", handle="lanternmothpostn",
                                  bio="third account, banned at 40k"),
        "bridge": ParsedAccount("demo-acct-3", handle="quietharbor", bio="synthetic"),
        "controls": ParsedAccount("demo-acct-4", handle="harbourcook", bio="recipes and sunsets"),
        "scholar": ParsedAccount("demo-acct-5", handle="historyreader", bio="synthetic reading notes"),
    }
    shared_sound = ParsedSound("demo-sound-1", title="original sound - lanternmoth88",
                               author_name="lanternmoth88", is_original=1)
    other_sound = ParsedSound("demo-sound-2", title="ambient harbour", author_name="harbourcook")

    items, n = [], 0
    def add(acct, caption, tags, sound, comments=(), mentions=()):
        nonlocal n
        n += 1
        items.append(ParsedItem(
            platform_item_id=f"demo-{n:03d}", url=f"https://example.invalid/demo/{n}",
            caption=caption, posted_at=1720000000 + n * 3600, account=accounts[acct],
            sound=sound, tags=list(tags), mentions=list(mentions),
            view_count=1000 * n, like_count=50 * n,
            comments=[ParsedComment(f"demo-c{n}-{i}", t, author_handle=f"demo-commenter-{i % 4}")
                      for i, t in enumerate(comments)]))

    for t in TIER_A:
        add("cluster", t, ["examplecoded", "fyp"], shared_sound)
    for i, t in enumerate(TIER_B):
        add("cluster2" if i % 2 else "cluster", t, ["examplecoded", "noticing"], shared_sound,
            comments=["telegram group in comments", "t dot me slash examplechannel"] if i == 0 else ())
    for t in TWO_C:
        add("bridge", t, ["fyp", "noticing"], shared_sound, mentions=["lanternmoth88"])
    for t in CLEAN:
        add("controls", t, ["cooking", "sunset"], other_sound, comments=["looks lovely"])
    for t in HARD_NEGATIVE:
        add("scholar", t, ["history", "reading"], other_sound)

    return CollectResult(items=items, accounts=list(accounts.values()))


def run(db_path: Path | None = None, *, verbose: bool = True) -> dict[str, object]:
    db_path = Path(db_path or DEFAULT_DEMO_DB)
    # The demo never touches the research database: it binds its own file
    # before the engine opens a connection, and only ever deletes that file.
    os.environ["RADICALMAP_DB"] = str(db_path)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()

    from .analyse import metrics, network
    from .classify import runs
    from .collect.persist import persist
    from .db import engine, migrate
    from .db.repo import writers as W
    from .propagate import edges, identity

    engine.close()
    migrate.migrate(verbose=False)
    src = W.ensure_source("demo:synthetic", "manual", "radicalmap/demo.py")
    stored = persist(build_corpus(), "tiktok", src, "demo")
    scan = runs.scan_corpus(verbose=False, notes="demo corpus")
    metrics.rebuild_all(verbose=False)
    built = edges.rebuild_all(verbose=False, with_audio=False)
    identity.detect(verbose=False)
    net = network.rebuild(verbose=False)

    # Which items the run proposed comes from the stored classifications. The
    # tier shown is recomputed from the same scan, because detector matches
    # (hidden capitalisation, username codes) carry no lexicon_entry row to
    # join a tier from.
    from .classify import lexicon as L
    candidates = {r["caption"]: L.scan(r["caption"]).top_tier for r in engine.query(
        """SELECT DISTINCT i.caption FROM classifications c
             JOIN items i ON c.target_type = 'item' AND i.id = c.target_id
            WHERE c.run_id = ?""", (scan["run_id"],))}
    out = {"db": str(db_path), "stored": stored, "scan": scan, "edges": built,
           "network": net, "candidates": candidates}
    if verbose:
        _report(out)
    return out


def _report(out: dict) -> None:
    c = out["candidates"]
    print(f"  demo database: {out['db']}")
    print(f"  stored: {out['stored']['items']} items, {out['stored']['accounts']} accounts, "
          f"{out['stored']['comments']} comments, {out['stored']['links']} links")
    print(f"  lexicon candidates by top tier: {out['scan']['by_tier']}")
    groups = (("tier A", TIER_A), ("tier B", TIER_B), ("two tier-C signals", TWO_C),
              ("clean controls (should be silent)", CLEAN),
              ("hard negatives (proposed; adjudication should reject)", HARD_NEGATIVE))
    for label, texts in groups:
        print(f"\n  {label}")
        for t in texts:
            print(f"    {'CAND ' + c[t] if t in c else '  -   '}  {t}")
    print("\n  edges: " + ", ".join(f"{k} {v}" for k, v in out["edges"].items() if v))
    print(f"\n  explore it:  RADICALMAP_DB={out['db']} radicalmap serve")

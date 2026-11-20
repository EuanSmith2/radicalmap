"""`radicalmap export` — a versioned, pseudonymised dataset package for deposit.

Output is a Frictionless Data Package (datapackage.json + one CSV per table,
each with a sha256) and a DATASHEET.md generated from the data itself, so the
description of a deposit cannot drift from what is in it.

What is pseudonymised, and what is not
--------------------------------------
Pseudonymised by default:
  * account handles and platform account ids -> `account_ref`, the same keyed
    hash commenter handles already carry, so an author who also comments keeps
    one consistent pseudonym across both tables;
  * @-mentions inside caption and comment text -> `@[account]`;
  * any collected account's handle appearing anywhere in exported text —
    including sound titles and sound authors, because TikTok names an
    "original sound" after its creator — -> `[account]`;
  * item URLs, which embed the author's handle, are dropped.

Kept, deliberately:
  * platform item ids. A dataset nobody can check against the source is not
    reproducible, so ids stay (the "dehydrated dataset" convention used for
    Twitter corpora). The consequence is stated in the datasheet: this is
    PSEUDONYMISED data under GDPR, not anonymous data, because a platform
    lookup of an item id reveals its author.
  * external link destinations (Telegram channels, sites). They are the
    off-platform network the research is about.

`--include-handles` keeps handles and URLs. It exists for a vetted data-sharing
arrangement, prints a warning, and is recorded in the package metadata.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from ..core import identity
from ..db import engine

MENTION_RE = re.compile(r"@[A-Za-z0-9_.]{2,30}")


def _ref(handle: str | None, platform_account_id: str | None, salt: bytes) -> str | None:
    """One pseudonym per account, matching commenter hashes where possible."""
    if handle:
        return identity.author_hash(handle, salt)
    if platform_account_id:
        return identity.author_hash(f"id:{platform_account_id}", salt)
    return None


class _Scrubber:
    """Masks @-mentions and every known handle, case-insensitively, as whole
    tokens. Built once per export from the accounts table."""

    def __init__(self, handles: list[str]) -> None:
        hs = sorted({h for h in handles if h and len(h) >= 3}, key=len, reverse=True)
        self.known = (re.compile(r"(?<![A-Za-z0-9_.])(?:" + "|".join(map(re.escape, hs))
                                 + r")(?![A-Za-z0-9_])", re.I) if hs else None)

    def __call__(self, text: str | None) -> str | None:
        if not text:
            return text
        text = MENTION_RE.sub("@[account]", text)
        return self.known.sub("[account]", text) if self.known else text


def _type(values: list[Any]) -> str:
    for v in values:
        if v is None:
            continue
        if isinstance(v, bool):
            return "boolean"
        if isinstance(v, int):
            return "integer"
        if isinstance(v, float):
            return "number"
        return "string"
    return "string"


def _tables(include_handles: bool, salt: bytes) -> dict[str, tuple[str, list[dict]]]:
    """name -> (description, rows). Rows are plain dicts, already pseudonymised."""
    accts = {int(r["id"]): r for r in engine.query("SELECT * FROM accounts")}
    scrub = (lambda t: t) if include_handles else _Scrubber([r["handle"] for r in accts.values()])
    aref = {i: (r["handle"] if include_handles else _ref(r["handle"], r["platform_account_id"], salt))
            for i, r in accts.items()}
    platform = {int(r["id"]): r["name"] for r in engine.query("SELECT id, name FROM platforms")}

    items = [{
        "item_id": int(r["id"]), "platform": platform.get(int(r["platform_id"])),
        "platform_item_id": r["platform_item_id"],
        **({"url": r["url"]} if include_handles else {}),
        "account_id": r["account_id"], "account_ref": aref.get(r["account_id"]), "kind": r["kind"],
        "posted_at": r["posted_at"], "language": r["language"],
        "caption": scrub(r["caption"]),
        "view_count": r["view_count"], "like_count": r["like_count"],
        "comment_count": r["comment_count"], "share_count": r["share_count"],
        "save_count": r["save_count"],
    } for r in engine.query("SELECT * FROM items WHERE is_deleted = 0 ORDER BY id")]

    accounts = [{
        # account_id is the store's internal row number (not identifying); it is
        # what classifications and edges reference, so it must be joinable.
        "account_id": i, "account_ref": aref[i], "platform": platform.get(int(r["platform_id"])),
        "bio": scrub(r["bio"]),
        "follower_count": r["follower_count"], "following_count": r["following_count"],
        "item_count": r["item_count"], "verified": int(r["verified"] or 0),
    } for i, r in sorted(accts.items()) if not r["is_deleted"]]

    comments = [{
        "comment_id": int(r["id"]), "item_id": int(r["item_id"]),
        "parent_comment_id": r["parent_comment_id"],
        # author_hash is already the commenter's pseudonym; with handles
        # included it stays a hash, because the store never had the handle
        # outside the restricted author_identities table.
        "author_ref": r["author_hash"],
        "text": scrub(r["text"]),
        "posted_at": r["posted_at"], "like_count": r["like_count"],
        "reply_count": r["reply_count"],
    } for r in engine.query("SELECT * FROM comments ORDER BY id")]

    return {
        "items": ("One row per collected post.", items),
        "accounts": ("Authors, pseudonymised.", accounts),
        "comments": ("Comments; author_ref is a keyed hash, replies point at parent_comment_id.",
                     comments),
        "tags": ("Hashtags.", [dict(r) for r in engine.query(
            "SELECT id AS tag_id, tag FROM tags ORDER BY id")]),
        "item_tags": ("Item-hashtag links.", [dict(r) for r in engine.query(
            "SELECT item_id, tag_id FROM item_tags ORDER BY item_id, tag_id")]),
        "sounds": ("Audio tracks. Ids are the platform's own sound ids.", [
            {**dict(r), "title": scrub(r["title"]), "author_name": scrub(r["author_name"])}
            for r in engine.query(
                """SELECT id AS sound_id, platform_sound_id, title, author_name, duration_s,
                          is_original, fingerprint FROM sounds ORDER BY id""")]),
        "item_sounds": ("Item-sound links.", [dict(r) for r in engine.query(
            "SELECT item_id, sound_id FROM item_sounds ORDER BY item_id")]),
        "links": ("External destinations found in text, with the surface they came from.",
                  [dict(r) for r in engine.query(
                      """SELECT id AS link_id, source_type, source_id, extracted_from,
                                platform, normalised_url, domain FROM links ORDER BY id""")]),
        "classifications": ("Every verdict, attributable to the classifier run that made it.",
                            [dict(r) for r in engine.query(
                                """SELECT c.id AS classification_id, c.run_id, c.target_type,
                                          c.target_id, c.stage, c.label, c.domain, c.harm_type,
                                          c.stance, c.verdict, c.confidence, e.slug AS lexicon_entry
                                     FROM classifications c
                                     LEFT JOIN lexicon_entries e ON e.id = c.lexicon_entry_id
                                    ORDER BY c.id""")]),
        "classifier_runs": ("Versioned runs. A verdict without its run is not reproducible.",
                            [dict(r) for r in engine.query(
                                """SELECT id AS run_id, stage, version, lexicon_version_id, model,
                                          prompt_hash, params_json, started_at, finished_at,
                                          target_count, notes FROM classifier_runs ORDER BY id""")]),
        "edges": ("Derived network ties (recomputable from the tables above).",
                  [dict(r) for r in engine.query(
                      """SELECT src_type, src_id, dst_type, dst_id, edge_type, weight,
                                observed_count FROM edges ORDER BY id""")]),
    }


def _write_csv(path: Path, rows: list[dict]) -> tuple[list[dict], str]:
    cols = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    fields = [{"name": c, "type": _type([r[c] for r in rows[:200]])} for c in cols]
    return fields, hashlib.sha256(path.read_bytes()).hexdigest()


def export(out_dir: Path, *, version: str | None = None,
           include_handles: bool = False) -> dict[str, Any]:
    if include_handles:
        print("  WARNING: --include-handles writes account handles and item URLs. Only for a\n"
              "  vetted data-sharing arrangement; the package metadata records that it was used.",
              file=sys.stderr)
    out_dir.mkdir(parents=True, exist_ok=True)
    salt = identity.load_salt()
    version = version or time.strftime("%Y.%m.%d", time.gmtime())
    tables = _tables(include_handles, salt)

    resources = []
    for name, (desc, rows) in tables.items():
        path = out_dir / f"{name}.csv"
        fields, digest = _write_csv(path, rows)
        resources.append({"name": name, "path": path.name, "format": "csv",
                          "mediatype": "text/csv", "encoding": "utf-8",
                          "description": desc, "hash": f"sha256:{digest}",
                          "rows": len(rows), "schema": {"fields": fields}})

    migrations = [r["name"] for r in engine.query("SELECT name FROM schema_migrations ORDER BY name")] \
        if engine.scalar("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'") else []
    meta = {
        "profile": "tabular-data-package",
        "name": "radicalmap-corpus",
        "title": "RadicalMap research corpus",
        "version": version,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "licenses": [{"name": "restricted", "title": "Research use under the depositor's terms"}],
        "radicalmap": {
            "pseudonymised": not include_handles,
            "include_handles": include_handles,
            "schema_migrations": migrations,
        },
        "resources": resources,
    }
    (out_dir / "datapackage.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out_dir / "DATASHEET.md").write_text(_datasheet(meta, tables), encoding="utf-8")
    return meta


def _datasheet(meta: dict, tables: dict) -> str:
    n = {k: len(v[1]) for k, v in tables.items()}
    runs = tables["classifier_runs"][1]
    verdicts: dict[str, int] = {}
    for r in tables["classifications"][1]:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    posted = [r["posted_at"] for r in tables["items"][1] if r["posted_at"]]
    span = (time.strftime("%Y-%m-%d", time.gmtime(min(posted))) + " to "
            + time.strftime("%Y-%m-%d", time.gmtime(max(posted)))) if posted else "unknown"
    gold = engine.scalar("SELECT count(*) FROM gold_annotations") or 0
    pseud = meta["radicalmap"]["pseudonymised"]
    lines = [
        f"# Datasheet: {meta['title']} v{meta['version']}",
        "",
        f"Generated {meta['created']} by `radicalmap export`, from the data in this package.",
        "",
        "## Composition",
        "",
        "| table | rows |", "|---|---|",
        *[f"| {k} | {v:,} |" for k, v in n.items()],
        "",
        f"Items posted between {span}.",
        "",
        "## Personal data",
        "",
        ("Pseudonymised, not anonymous. Handles and account ids are replaced by keyed "
         "hashes, @-mentions in text are masked and item URLs are removed. Platform item "
         "ids are kept so the corpus can be verified against the source, which means a "
         "platform lookup can re-identify an item's author. Treat the package as personal "
         "data under GDPR.") if pseud else
        ("**Handles and URLs are included** (`--include-handles`). This package identifies "
         "individuals directly and must only travel under a vetted data-sharing agreement."),
        "",
        "Accounts the platform flags as under 18 are never stored, so none appear here.",
        "",
        "## Labels",
        "",
        f"{n['classifications']:,} classification rows across {len(runs)} classifier run(s). "
        f"Verdicts: {', '.join(f'{k} {v:,}' for k, v in sorted(verdicts.items())) or 'none'}.",
        "",
        "Lexicon rows are candidates for review, not findings: the lexicon is tuned for "
        "recall and deliberately proposes text that adjudication then rejects.",
        "",
        (f"{gold:,} human gold annotations exist. Run `radicalmap classify eval-report` for "
         "measured precision and recall.") if gold else
        "No gold annotations exist yet, so **no accuracy figures can be claimed** for any label.",
        "",
        "## Runs",
        "",
        "| run | stage | version | model | targets |", "|---|---|---|---|---|",
        *[f"| {r['run_id']} | {r['stage']} | {r['version']} | {r['model'] or '-'} | "
          f"{r['target_count']:,} |" for r in runs],
        "",
        "## Integrity",
        "",
        "Every CSV's sha256 is in `datapackage.json`. Validate with "
        "`frictionless validate datapackage.json`.",
        "",
    ]
    return "\n".join(lines)

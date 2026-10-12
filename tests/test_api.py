#!/usr/bin/env python
"""The console's HTTP surface.

Two properties carry the weight here, and neither is cosmetic:

  * **Blinding** — the annotation sample must never carry the classifier's
    verdict. An annotator who can see what the model decided is not producing
    an independent gold standard, and every precision/recall number downstream
    inherits that contamination silently.
  * **Refusals report as refusals** — the GDPR Art. 9 gate and the
    model-provenance guard must surface as 403/422, not as a 500. A governance
    control that looks like a server fault gets retried, then worked around.

Also asserted: the sort control cannot inject SQL, because it is the one place
a caller-supplied string reaches a query outside a bound parameter.
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp())
os.environ["RADICALMAP_DB"] = str(tmp / "t.db")
os.environ["RADICALMAP_SALT_FILE"] = str(tmp / ".salt")
os.environ.pop("RM_DPIA_REF", None)

from fastapi.testclient import TestClient

from radicalmap import taxonomy as T
from radicalmap.api import query as Q
from radicalmap.api.app import create_app
from radicalmap.db import engine, migrate
from radicalmap.db.repo import writers as W

fails = []
def check(label, got, want=True):
    ok = got == want
    if not ok: fails.append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label[:70]:<70}" + ("" if ok else f" {got!r} != {want!r}"))
def section(s): print(f"\n  ── {s} " + "─" * max(0, 58 - len(s)))

migrate.migrate(verbose=False)

# ── a minimal corpus ─────────────────────────────────────────────────────────
pid = W.platform_id("tiktok")
src = W.ensure_source("test", "seed_list", "test")
with engine.transaction():
    engine.execute(
        """INSERT INTO accounts (id, platform_id, platform_account_id, handle, source_id)
           VALUES (1, ?, 'a1', 'tester', ?)""", (pid, src))
    engine.execute(
        """INSERT INTO items (id, platform_id, platform_item_id, account_id, kind,
                              caption, posted_at, view_count, source_id)
           VALUES (1, ?, 'i1', 1, 'video', 'a caption about hyperborea', 1750000000, 10, ?)""",
        (pid, src))
    engine.execute(
        """INSERT INTO comments (id, item_id, platform_comment_id, author_hash, text, posted_at)
           VALUES (1, 1, 'c1', 'hash1', 'a comment body', 1750000100)""")
    engine.execute(
        """INSERT INTO classifier_runs (id, stage, version, target_count)
           VALUES (1, 'lexicon', 'test-1', 1)""")
    engine.execute(
        """INSERT INTO classifications (id, target_type, target_id, run_id, stage,
                                        label, verdict, evidence_span, is_finding)
           VALUES (1, 'comment', 1, 1, 'lexicon', 'x', 'candidate', 'comment body', 0)""")
engine.rebuild_fts()

client = TestClient(create_app())

section("every read endpoint answers")
for path, params in [
    ("/healthz", {}), ("/api/overview", {}), ("/api/items", {}),
    ("/api/items/1", {}), ("/api/classifications", {}),
    ("/api/classifications/facets", {}), ("/api/target/comment/1", {}),
    ("/api/nodes", {}), ("/api/communities", {}), ("/api/edge-types", {}),
    ("/api/lexicon", {}), ("/api/taxonomy", {}), ("/api/jobs", {}),
    ("/api/propagation", {}), ("/api/annotate/options", {}),
    ("/api/annotate/coverage", {}),
]:
    check(f"GET {path}", client.get(path, params=params).status_code, 200)

check("GET /api/items/999999 is 404", client.get("/api/items/999999").status_code, 404)
check("GET /api/target/item/999999 is 404",
      client.get("/api/target/item/999999").status_code, 404)

section("search and paging")
check("FTS finds the item", client.get("/api/items", params={"q": "hyperborea"}).json()["total"], 1)
check("FTS miss returns none", client.get("/api/items", params={"q": "zzzznope"}).json()["total"], 0)
check("malformed FTS is a 400, not a 500",
      client.get("/api/items", params={"q": 'unclosed"'}).status_code, 400)
check("limit is capped at MAX_LIMIT",
      client.get("/api/items", params={"limit": 99999}).status_code, 422)
check("negative offset refused", client.get("/api/items", params={"offset": -1}).status_code, 422)

section("the sort control is an allow-list, not interpolation")
evil = "id; DROP TABLE items--"
check("injection string is accepted but ignored",
      client.get("/api/items", params={"sort": evil}).status_code, 200)
check("items table survives it", engine.scalar("SELECT COUNT(*) FROM items"), 1)
check("unknown sort falls back to the default",
      Q._order(evil, Q._ITEM_SORT, "posted", "DESC"), "i.posted_at DESC")
check("descending prefix honoured", Q._order("-views", Q._ITEM_SORT, "posted"), "i.view_count DESC")
check("ascending honoured", Q._order("views", Q._ITEM_SORT, "posted"), "i.view_count ASC")

section("BLINDING: the sample carries no verdict")
rows = client.get("/api/annotate/sample", params={"n": 5}).json()["rows"]
check("sample returned rows", len(rows) > 0)
keys = {k for r in rows for k in r}
# Assert against the router's own allow-list rather than a copy, so widening
# _SAFE_FIELDS without thinking fails here instead of silently leaking.
from radicalmap.api.routers.annotate import _SAFE_FIELDS
check("fields are exactly the router's allow-list", keys, set(_SAFE_FIELDS))
for leaked in ("verdict", "label", "is_finding", "confidence", "severity", "tier", "rationale"):
    check(f"'{leaked}' is not sent to the annotator", leaked in keys, False)

section("REFUSALS: guards report themselves, and never 500")
target = rows[0]
def post(**kw):
    body = {"target_type": target["target_type"], "target_id": target["target_id"],
            "label": "clean", "is_positive": False, "annotator": "tester"}
    body.update(kw)
    return client.post("/api/annotate", json=body)

check("model-authored annotation is refused 422", post(annotator="model:v1").status_code, 422)
gated = [d.slug for d in T.DOMAINS if d.requires_dpia]
check("there are gated domains to test", len(gated) > 0)
r = post(domain=gated[0])
check("gated domain is refused 403 (not 500)", r.status_code, 403)
check("the refusal names the gate", "gated" in r.json()["detail"])
check("unknown domain is refused 422", post(domain="not_a_domain").status_code, 422)
check("unknown harm is refused 422", post(harm_type="not_a_harm").status_code, 422)
check("unknown stance is refused 422", post(stance="vibing").status_code, 422)
check("nothing was written by any refusal",
      engine.scalar("SELECT COUNT(*) FROM gold_annotations"), 0)

section("a real human annotation is stored")
open_domain = next(d.slug for d in T.DOMAINS if not d.requires_dpia and d.slug != "none")
r = post(domain=open_domain, stance="discussing", is_positive=True, label=open_domain)
check("accepted 200", r.status_code, 200)
check("one gold row exists", engine.scalar("SELECT COUNT(*) FROM gold_annotations"), 1)
check("domain round-tripped",
      engine.scalar("SELECT domain FROM gold_annotations"), open_domain)
check("coverage reports it", r.json()["coverage"]["annotated_targets"], 1)
check("re-posting the same target upserts rather than duplicating",
      (post(domain=open_domain, label=open_domain).status_code,
       engine.scalar("SELECT COUNT(*) FROM gold_annotations")), (200, 1))

section("sample skips what is already annotated")
d = client.get("/api/annotate/sample", params={"n": 50, "annotator": "tester"}).json()
check("skipped count is reported", d["skipped"] >= 1)
done = {(target["target_type"], target["target_id"])}
check("the annotated target is not offered again",
      any((r["target_type"], r["target_id"]) in done for r in d["rows"]), False)

section("gated taxonomy is not even offered")
opts = client.get("/api/annotate/options").json()
check("gate reported closed", opts["gate_open"], False)
offered = {d["slug"] for d in opts["domains"]}
for g in gated:
    check(f"gated domain '{g}' not offered", g in offered, False)

print(f"\n  {'ALL PASS' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)

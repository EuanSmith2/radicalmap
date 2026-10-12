"""radicalmap — command line.

    python -m radicalmap migrate
    python -m radicalmap seed     --file seeds.txt [--type item]
    python -m radicalmap collect  [--workers 4] [--max-items 10000] [--dry-run]
    python -m radicalmap import   corpus.jsonl [--mapping map.json] [--limit N]
    python -m radicalmap analyse
    python -m radicalmap status
    python -m radicalmap serve    [--host H] [--port P]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .core.config import settings
from .db import engine, migrate as migrate_mod
from .db.repo import jobs as Q
from .db.repo import writers as W


def _fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def cmd_migrate(args) -> int:
    migrate_mod.status() if args.status else migrate_mod.migrate()
    return 0


def cmd_seed(args) -> int:
    from .collect.adapters.base import Target
    from .collect import frontier

    migrate_mod.migrate(verbose=False)
    lines = [l.strip() for l in Path(args.file).read_text().splitlines()]
    targets = [Target(args.type, l) for l in lines if l and not l.startswith("#")]
    src = W.ensure_source(args.source or f"seed:{Path(args.file).name}", "seed_list", args.file)
    n = frontier.seed(W.platform_id(args.platform), targets, src)
    print(f"  queued {n} new target(s) of {len(targets)} read "
          f"({len(targets) - n} already known)")
    return 0


def cmd_discover(args) -> int:
    """Queue discovery jobs from bare terms.

    Seeding by hand meant writing a file of URLs; a keyword is not a URL and a
    tag is only one by convention, so the URL shape is built here rather than
    asked of the user.
    """
    from .collect.adapters.base import Target
    from .collect.adapters.tiktok_browser import search_url, tag_url
    from .collect import frontier

    migrate_mod.migrate(verbose=False)
    build = search_url if args.type == "search" else tag_url
    targets = [Target(args.type, build(t)) for t in args.terms]
    src = W.ensure_source(args.source or f"discover:{args.type}", "seed_list",
                          " ".join(args.terms))
    n = frontier.seed(W.platform_id(args.platform), targets, src)
    print(f"  queued {n} {args.type} job(s) of {len(targets)} "
          f"({len(targets) - n} already known)")
    for t in targets:
        print(f"    {t.target}")
    return 0


def cmd_collect(args) -> int:
    from .collect.frontier import Budget
    from .collect.worker import Engine

    migrate_mod.migrate(verbose=False)

    if args.synthetic:
        from .collect.adapters.synthetic import SyntheticAdapter
        adapters = [SyntheticAdapter(corpus_size=args.synthetic,
                                     latency=args.latency,
                                     failure_rate=args.failure_rate,
                                     rate_limit_rate=args.rate_limit_rate,
                                     gone_rate=args.gone_rate)]
        method = "synthetic"
    else:
        from .collect.adapters.tiktok import TikTokAdapter
        if not settings.allow_network:
            print("  refusing to start: live collection is disabled.\n"
                  "  Collection sends requests to a live platform, so it never starts\n"
                  "  implicitly. Set RM_ALLOW_NETWORK=1 when you intend that.",
                  file=sys.stderr)
            return 2
        adapters = [TikTokAdapter()]
        # Discovery (tag/sound/search) needs a real browser: TikTok serves those
        # listings over XHR, not in the page, and yt-dlp's route to them needs
        # app credentials it does not ship. Ordered second so the fast adapter
        # claims every job type it can and the browser only sees what is left.
        if not args.no_browser:
            from .collect.adapters.tiktok_browser import TikTokBrowserAdapter
            adapters.append(TikTokBrowserAdapter(headless=not args.headed))
        method = "collect"

    src = W.ensure_source(args.source or method, "frontier", "collect run")
    engine_ = Engine(adapters, args.platform, source_id=src, method=method,
                     workers=args.workers or settings.workers,
                     budget=Budget(max_items=args.max_items, max_depth=args.max_depth))

    async def _run_then_close():
        # Engine has no adapter lifecycle hook, and a leaked chromium outlives
        # the process invisibly, so teardown is the CLI's job.
        try:
            return await engine_.run(max_jobs=args.max_jobs,
                                     idle_timeout=args.idle_timeout)
        finally:
            for a in adapters:
                if hasattr(a, "aclose"):
                    await a.aclose()

    stats = asyncio.run(_run_then_close())

    print(f"\n  {stats.line()}")
    print(f"  elapsed {stats.elapsed:.1f}s")
    pacing = engine_.pacer.snapshot()
    if pacing:
        print("  pacing:")
        for host, st in pacing.items():
            print(f"    {host:<28} rate={st['rate']}/s requests={st['requests']} "
                  f"429s={st['rate_limited']} breaker={'OPEN' if st['breaker_open'] else 'closed'}")
    return 0


def cmd_import(args) -> int:
    from .collect.importers.corpus import import_file

    migrate_mod.migrate(verbose=False)
    if args.mapping == "apify":
        # Built in, so an Apify export imports without hunting for a file.
        from .collect.adapters.tiktok_web import APIFY_IMPORT_MAPPING
        mapping = dict(APIFY_IMPORT_MAPPING)
    else:
        mapping = json.loads(Path(args.mapping).read_text()) if args.mapping else {}
    totals = import_file(Path(args.path), args.platform, mapping=mapping,
                         source_name=args.source, limit=args.limit)
    print("  imported:")
    for k, v in sorted(totals.items()):
        print(f"    {k:<18} {_fmt(v)}")
    return 0


def cmd_demo(args) -> int:
    from . import demo
    demo.run(Path(args.db) if args.db else None)
    return 0


def cmd_export_apify(args) -> int:
    from .collect.export_apify import export

    migrate_mod.migrate(verbose=False)
    n = export(Path(args.out), args.platform, args.limit)
    print(f"  wrote {_fmt(n)} items in Apify TikTok dataset shape -> {args.out}")
    return 0


def cmd_classify(args) -> int:
    from .classify import adjudicate as A, annotate, discover, eval as E, lexicon as L, runs
    migrate_mod.migrate(verbose=False)
    op = args.op

    if op == "lexicon":
        import json as _j
        print(_j.dumps(L.summary(), indent=2))
        if args.retired:
            print("\n  retired from v1:")
            for r in L.RETIRED:
                print(f"    {r['pattern'][:44]:<46} {r['reason'][:96]}")
        return 0

    if op == "scan":
        stats = runs.scan_corpus(verbose=True)
        print(f"    by tier     {stats['by_tier']}")
        print(f"    by category {dict(sorted(stats['by_category'].items(), key=lambda kv: -kv[1]))}")
        return 0

    if op == "adjudicate":
        if args.live:
            if not _bool_env("RM_ALLOW_LLM"):
                print("  refusing to start: live adjudication calls a paid API.\n"
                      "  Set RM_ALLOW_LLM=1 when you intend to spend on it.", file=sys.stderr)
                return 2
            adj = A.AnthropicAdjudicator(model=args.model)
        else:
            adj = A.StubAdjudicator()
            print("  using the deterministic STUB adjudicator — results are not research output")
        A.run(args.run, adjudicator=adj, limit=args.limit)
        return 0

    if op == "eval":
        run_id = args.run or runs.latest("llm")
        if run_id is None:
            print("  no adjudication run yet", file=sys.stderr); return 1
        print(E.report(E.gold_metrics(run_id)))
        return 0

    if op == "patterns":
        cand = args.run or runs.latest("lexicon")
        verd = args.against or runs.latest("llm")
        rows = E.per_pattern(cand, verd)
        print(f"  per-pattern performance (candidates from run {cand}, verdicts from run {verd})")
        print(f"  {'entry':<26} {'tier':<5} {'cand':>5} {'conf':>5} {'rej':>5} {'unc':>5} {'prec':>6}")
        print("  " + "-" * 62)
        for r in rows:
            prec = f"{r['precision']:.2f}" if r["precision"] is not None else "   -"
            print(f"  {r['slug'][:25]:<26} {r['tier']:<5} {r['candidates']:>5} "
                  f"{r['confirmed']:>5} {r['rejected']:>5} {r['uncertain']:>5} {prec:>6}")
        return 0

    if op == "compare":
        res = E.compare_runs(args.run, args.against)
        print(f"  run {res['run_a']} vs run {res['run_b']} over {res['universe']} targets")
        print(f"    flagged by A only : {res['only_a']}")
        print(f"    flagged by B only : {res['only_b']}")
        print(f"    flagged by both   : {res['both']}")
        print(f"    flagged by neither: {res['neither']}")
        print(f"    observed agreement: {res['observed_agreement']}")
        print(f"    Cohen's kappa     : {res['cohens_kappa']}")
        for side, key in (("A", "only_a_examples"), ("B", "only_b_examples")):
            if res[key]:
                print(f"    only {side}:")
                for ex in res[key]:
                    print(f"      {ex}")
        return 0

    if op == "discover":
        for d in discover.discover(args.run, top=args.limit or 40):
            print(f"    {d['term']:<24} G2={d['log_likelihood']:>8}  "
                  f"pos={d['positive_count']:<4} bg={d['background_count']:<5} x{d['ratio']}")
        return 0

    if op == "sample":
        rows = annotate.draw(args.limit or 300)
        from collections import Counter
        print(f"  drew {len(rows)} targets: {dict(Counter(r.stratum for r in rows))}")
        print("  'clean' rows carry no lexicon match — they are the only way to")
        print("  observe false negatives, and therefore the only way to measure recall.")
        if args.out:
            import json as _j
            Path(args.out).write_text(_j.dumps(
                [{"target_type": r.target_type, "target_id": r.target_id,
                  "stratum": r.stratum, "terms": r.terms, "text": r.text} for r in rows],
                indent=1, ensure_ascii=False))
            print(f"  written to {args.out}")
        return 0

    if op == "annotate":
        rows = annotate.draw(args.limit or 300)
        n = annotate.interactive(rows, args.annotator or "euan")
        print(f"\n  annotated {n} targets")
        print(f"  coverage: {annotate.coverage(args.annotator or 'euan')}")
        return 0

    if op == "coverage":
        print(f"  {annotate.coverage(args.annotator)}")
        return 0

    print(f"unknown classify op {op!r}", file=sys.stderr)
    return 1


def _bool_env(name: str) -> bool:
    import os
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def cmd_graph(args) -> int:
    from .analyse import metrics, network
    from .propagate import edges, identity
    migrate_mod.migrate(verbose=False)
    op = args.op

    if op == "build":
        print("  derived metrics:"); metrics.rebuild_all()
        print("  edges:"); edges.rebuild_all(with_audio=not args.no_audio)
        print("  alt accounts:"); identity.detect()
        print("  network:"); network.rebuild()
        return 0
    if op == "top":
        rows = network.top(args.type, args.metric, args.limit or 15)
        print(f"  top {args.type}s by {args.metric}")
        print(f"    {'label':<30} {'pagerank':>9} {'deg':>5} {'core':>5} {'comm':>5} {'bridge':>7}")
        for r in rows:
            print(f"    {str(r['label'])[:29]:<30} {r['pagerank'] or 0:>9.5f} "
                  f"{r['degree']:>5} {r['core_number']:>5} {r['community']:>5} "
                  f"{r['bridge_score'] or 0:>7.2f}")
        return 0
    if op == "communities":
        for r in engine.query(
                """SELECT community, count(*) n,
                          sum(CASE WHEN node_type='account' THEN 1 ELSE 0 END) accounts,
                          sum(CASE WHEN node_type='tag' THEN 1 ELSE 0 END) tags
                     FROM node_metrics WHERE community IS NOT NULL
                    GROUP BY community ORDER BY n DESC LIMIT ?""", (args.limit or 15,)):
            print(f"    community {r['community']:<4} {r['n']:>6} nodes  "
                  f"({r['accounts']} accounts, {r['tags']} tags)")
        return 0
    if op == "alts":
        for c in identity.clusters(args.limit or 15):
            print(f"    cluster {c['cluster_id']}:")
            for m in c["members"]:
                print(f"      @{m['handle']:<24} {m['similarity']:.2f}  {m['evidence'][:70]}")
        return 0
    if op == "edges":
        for r in engine.query("SELECT edge_type, count(*) n, round(avg(weight),3) w "
                              "FROM edges GROUP BY 1 ORDER BY n DESC"):
            print(f"    {r['edge_type']:<16} {r['n']:>8,} edges  avg weight {r['w']}")
        return 0
    print(f"unknown graph op {op!r}", file=sys.stderr)
    return 1


def cmd_propagate(args) -> int:
    from .propagate import scoring
    from .propagate.engine import Propagator, decisions
    migrate_mod.migrate(verbose=False)

    adapter = None
    if args.synthetic:
        from .collect.adapters.synthetic import SyntheticAdapter
        adapter = SyntheticAdapter(corpus_size=args.synthetic)
    else:
        from .collect.adapters.tiktok import TikTokAdapter
        adapter = TikTokAdapter()

    prop = Propagator(adapter=adapter, platform=args.platform,
                      per_wave=args.limit or 200, frontier_size=args.frontier,
                      drift_threshold=args.drift)
    if args.op == "wave":
        prop.wave()
        return 0
    if args.op == "report":
        rep = prop.report()
        print(f"  drift      : {rep['drift']}")
        print(f"  saturation : {rep['saturation']}")
        print(f"  queue      : {rep['queue']['by_status']}")
        print("  edge yield :")
        for r in sorted(rep["edge_yield"], key=lambda r: -r["proposed"])[:10]:
            print(f"    {r['edge_type']:<16} proposed={r['proposed']:<7} "
                  f"fetched={r['fetched']:<7} prior={r['prior']}  effective={r['effective_yield']}")
        print("  decisions  :")
        for d in decisions(10):
            print(f"    {d['edge_type']:<14} {d['decision']:<16} n={d['n']:<6} "
                  f"score={d['avg_score']} drift={d['avg_drift']}")
        return 0
    print(f"unknown propagate op {args.op!r}", file=sys.stderr)
    return 1


def cmd_taxonomy(args) -> int:
    from . import taxonomy as T
    migrate_mod.migrate(verbose=False)
    print(json.dumps(T.summary(), indent=2))
    if args.governance:
        print("\n  governance:")
        print(json.dumps(T.governance(), indent=2))
    if args.verbose:
        print("\n  domains:")
        for d in T.DOMAINS:
            gate = "  [GATED — Art. 9]" if d.requires_dpia else ""
            print(f"    {d.family:<14} {d.slug:<30} {d.name}{gate}")
        print("\n  harm types (only these can make something a finding):")
        for h in T.HARM_TYPES:
            print(f"    {h.slug:<30} ceiling={h.severity_ceiling}"
                  f"{'  [GATED]' if h.requires_dpia else ''}")
        print("\n  stances (* = can support a finding):")
        for st in T.STANCES:
            print(f"    {'*' if st.finding_eligible else ' '} {st.slug:<16} {st.note[:60]}")
    return 0


def cmd_learn(args) -> int:
    from .classify import annotate
    from .learn import loop, train
    migrate_mod.migrate(verbose=False)
    op = args.op

    if op == "round":
        loop.start_round(n=args.limit or 50)
        return 0
    if op == "queue":
        for q in loop.queue(args.run)[:args.limit or 20]:
            print(f"    {q['target_type']:<9} {q['target_id']:<7} "
                  f"score={q['score']:.3f}  {q['reason']}")
        return 0
    if op == "annotate":
        rows = []
        round_id = args.run or engine.scalar("SELECT max(id) FROM active_rounds")
        for q in loop.queue(round_id):
            text = train._text_for(q["target_type"], int(q["target_id"])) or ""
            from .classify.lexicon import scan
            rows.append(annotate.SampleRow(q["target_type"], int(q["target_id"]),
                                           q["reason"], text,
                                           sorted({m.slug for m in scan(text).matches})))
        if not rows:
            print("  nothing queued — run `learn round` first", file=sys.stderr)
            return 1
        n = annotate.interactive(rows, args.annotator or "euan", round_id)
        print(f"\n  annotated {n}")
        return 0
    if op == "close":
        loop.close_round(args.run, include_adjudicated=args.include_adjudicated)
        return 0
    if op == "propose":
        for p_ in loop.propose_terms_from_model(top=args.limit or 20):
            print(f"    {p_['term']:<26} weight {p_['weight']:>7}  "
                  f"enrich x{p_['enrichment']:<6} {p_['head']}={p_['label']}")
        return 0
    if op == "history":
        for r in loop.history():
            print(f"    round {r['round_no']:<3} {r['strategy']:<24} "
                  f"selected={r['n_selected']:<5} annotated={r['n_annotated']:<5} "
                  f"model={r['model'] or '-'} ({r['train_source'] or '-'})")
        return 0
    if op == "status":
        for r in engine.query("SELECT version, algorithm, n_train, train_source, "
                              "trained_at FROM model_versions ORDER BY trained_at DESC LIMIT 10"):
            print(f"    {r['version']:<16} {r['algorithm']:<18} n={r['n_train']:<6} "
                  f"source={r['train_source']}")
        return 0
    print(f"unknown learn op {op!r}", file=sys.stderr)
    return 1


def cmd_audio(args) -> int:
    import asyncio as _a
    from .collect import audio
    migrate_mod.migrate(verbose=False)
    if not settings.allow_network:
        print("  refusing to start: audio fetching downloads from a live platform.\n"
              "  Set RM_ALLOW_NETWORK=1 when you intend that.", file=sys.stderr)
        return 2
    stats = _a.run(audio.fetch_pending(limit=args.limit))
    print("  audio:")
    for k, v in stats.items():
        print(f"    {k:<22} {_fmt(v)}")
    return 0


def cmd_analyse(args) -> int:
    from .analyse import metrics
    migrate_mod.migrate(verbose=False)
    print("  rebuilding derived metrics")
    metrics.rebuild_all()
    return 0


def cmd_status(args) -> int:
    migrate_mod.migrate(verbose=False)
    print(f"  database: {engine.db_path()}")
    rows = [("items", "SELECT count(*) FROM items"),
            ("accounts", "SELECT count(*) FROM accounts"),
            ("tags", "SELECT count(*) FROM tags"),
            ("sounds", "SELECT count(*) FROM sounds"),
            ("comments", "SELECT count(*) FROM comments"),
            ("transcripts", "SELECT count(*) FROM transcripts"),
            ("links", "SELECT count(*) FROM links"),
            ("classifications", "SELECT count(*) FROM classifications")]
    print("  corpus:")
    for label, sql in rows:
        print(f"    {label:<16} {_fmt(int(engine.scalar(sql) or 0))}")
    st = Q.stats()
    print(f"  queue: {_fmt(st['total'])} jobs, {_fmt(st['due_now'])} due now")
    for status, n in sorted(st["by_status"].items()):
        print(f"    {status:<16} {_fmt(n)}")
    dead = engine.query("SELECT job_type, target, last_error FROM fetch_jobs "
                        "WHERE status IN ('dead','failed') ORDER BY finished_at DESC LIMIT 5")
    if dead:
        print("  recent failures:")
        for r in dead:
            print(f"    {r['job_type']:<16} {str(r['target'])[:44]:<44} {str(r['last_error'])[:60]}")
    return 0


def cmd_serve(args) -> int:
    """Run the local console.

    Binds 127.0.0.1 unless --host says otherwise. The corpus holds material
    collected precisely because it is extremist, plus pseudonymised commenter
    handles; putting that on a LAN interface should be a decision someone typed,
    not a default.
    """
    import uvicorn

    from .db import migrate as _m
    _m.migrate(verbose=False)

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  ! binding {args.host} — the console has no authentication.\n"
              f"    Anyone who can reach this port can read the corpus.")
    print(f"  console  http://{args.host}:{args.port}")
    print(f"  api docs http://{args.host}:{args.port}/api/docs")
    uvicorn.run("radicalmap.api.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level=args.log_level)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("radicalmap", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("migrate"); m.add_argument("--status", action="store_true")
    m.set_defaults(func=cmd_migrate)

    s = sub.add_parser("seed")
    s.add_argument("--file", required=True)
    s.add_argument("--type", default="item")
    s.add_argument("--platform", default="tiktok")
    s.add_argument("--source", default=None)
    s.set_defaults(func=cmd_seed)

    d = sub.add_parser("discover",
                       help="queue tag/keyword discovery jobs directly")
    d.add_argument("terms", nargs="+")
    d.add_argument("--type", default="search", choices=["search", "tag_page"])
    d.add_argument("--platform", default="tiktok")
    d.add_argument("--source", default=None)
    d.set_defaults(func=cmd_discover)

    c = sub.add_parser("collect")
    c.add_argument("--platform", default="tiktok")
    c.add_argument("--workers", type=int, default=0)
    c.add_argument("--max-items", type=int, default=0)
    c.add_argument("--max-depth", type=int, default=None)
    c.add_argument("--max-jobs", type=int, default=0)
    c.add_argument("--idle-timeout", type=float, default=5.0)
    c.add_argument("--source", default=None)
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--no-browser", action="store_true",
                   help="disable browser discovery (tag/sound/search jobs will be skipped)")
    c.add_argument("--headed", action="store_true",
                   help="show the discovery browser instead of running it headless")
    c.add_argument("--synthetic", type=int, default=0,
                   help="run against a simulated platform of N items instead of a real one")
    c.add_argument("--latency", type=float, default=0.0)
    c.add_argument("--failure-rate", type=float, default=0.0)
    c.add_argument("--rate-limit-rate", type=float, default=0.0)
    c.add_argument("--gone-rate", type=float, default=0.0)
    c.set_defaults(func=cmd_collect)

    i = sub.add_parser("import")
    i.add_argument("path")
    i.add_argument("--platform", default="tiktok")
    i.add_argument("--mapping", default=None,
                   help="JSON mapping file, or 'apify' for an Apify TikTok scraper export")
    i.add_argument("--source", default=None)
    i.add_argument("--limit", type=int, default=0)
    i.set_defaults(func=cmd_import)

    dm = sub.add_parser("demo", help="build a synthetic demo database, offline")
    dm.add_argument("--db", default=None, help="demo database path (default db/radicalmap.demo.db)")
    dm.set_defaults(func=cmd_demo)

    ea = sub.add_parser("export-apify",
                        help="write collected items in the Apify TikTok dataset shape")
    ea.add_argument("--out", required=True)
    ea.add_argument("--platform", default="tiktok")
    ea.add_argument("--limit", type=int, default=0)
    ea.set_defaults(func=cmd_export_apify)

    cl = sub.add_parser("classify")
    cl.add_argument("op", choices=["lexicon", "scan", "adjudicate", "eval", "patterns",
                                   "compare", "discover", "sample", "annotate", "coverage"])
    cl.add_argument("--run", type=int, default=None)
    cl.add_argument("--against", type=int, default=None)
    cl.add_argument("--limit", type=int, default=0)
    cl.add_argument("--live", action="store_true", help="use the real LLM adjudicator (costs money)")
    cl.add_argument("--model", default="claude-sonnet-5")
    cl.add_argument("--annotator", default=None)
    cl.add_argument("--retired", action="store_true")
    cl.add_argument("--out", default=None)
    cl.set_defaults(func=cmd_classify)

    gr = sub.add_parser("graph")
    gr.add_argument("op", choices=["build", "top", "communities", "alts", "edges"])
    gr.add_argument("--type", default="account")
    gr.add_argument("--metric", default="pagerank",
                    choices=["pagerank", "degree", "core", "bridge"])
    gr.add_argument("--limit", type=int, default=0)
    gr.add_argument("--no-audio", action="store_true")
    gr.set_defaults(func=cmd_graph)

    pg = sub.add_parser("propagate")
    pg.add_argument("op", choices=["wave", "report"])
    pg.add_argument("--platform", default="tiktok")
    pg.add_argument("--limit", type=int, default=0)
    pg.add_argument("--frontier", type=int, default=60)
    pg.add_argument("--drift", type=float, default=0.80)
    pg.add_argument("--synthetic", type=int, default=0)
    pg.set_defaults(func=cmd_propagate)

    tx = sub.add_parser("taxonomy")
    tx.add_argument("--verbose", action="store_true")
    tx.add_argument("--governance", action="store_true")
    tx.set_defaults(func=cmd_taxonomy)

    ln = sub.add_parser("learn")
    ln.add_argument("op", choices=["round", "queue", "annotate", "close", "propose",
                                   "history", "status"])
    ln.add_argument("--run", type=int, default=None)
    ln.add_argument("--limit", type=int, default=0)
    ln.add_argument("--annotator", default=None)
    ln.add_argument("--include-adjudicated", action="store_true")
    ln.set_defaults(func=cmd_learn)

    au = sub.add_parser("audio")
    au.add_argument("--limit", type=int, default=50)
    au.set_defaults(func=cmd_audio)

    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=5173)
    sv.add_argument("--reload", action="store_true")
    sv.add_argument("--log-level", default="info")
    sv.set_defaults(func=cmd_serve)

    a = sub.add_parser("analyse"); a.set_defaults(func=cmd_analyse)
    st = sub.add_parser("status"); st.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    if getattr(args, "dry_run", False):
        import os
        os.environ["RM_DRY_RUN"] = "1"
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

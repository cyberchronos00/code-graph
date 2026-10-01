"""code-graph CLI.

  python -m codegraph.cli index   <project_root> --db out/x.db [--scip index.scip] [--gates config/x.gates.json]
  python -m codegraph.cli reaches <spec>... --db out/x.db [--min-confidence resolved] [--json] [--no-paths]
  python -m codegraph.cli siblings <symbol> --db ...
  python -m codegraph.cli writers <table> --db ...
  python -m codegraph.cli impact <Class::method> --db ...
  python -m codegraph.cli stats --db ...
  python -m codegraph.cli detect <project_root>
  python -m codegraph.cli link --backend out/api.db --frontend out/web.db --db out/combined.db [--report out/x]
  python -m codegraph.cli downstream <spec> --db out/combined.db        (forward: page -> ... -> routes/tables)
  python -m codegraph.cli api-calls <all|unmatched|substring|glob*> --db out/combined.db
  python -m codegraph.cli plan list|load|validate|check|baseline [<name>] --db out/combined.db [--verify] [--summary] [--json]
  python -m codegraph.cli routes --db ... [--writes [TABLE]] [--reaches SPEC...] [--missing NAME] [--unguarded]
  python -m codegraph.cli search <name> --db ... [--kind route]
  python -m codegraph.cli channels [PATTERN] --db ...   (who can join, which events publish, which client code listens)
  python -m codegraph.cli tests <spec> --db ...          (tests covering a symbol / route / table: direct + transitive)
spec forms: table.column | connection:<name> (glob *) | env:<KEY*> | config:<a.b> | Class::method | Class
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .core.store import GraphStore
from . import query as Q


def main(argv=None):
    ap = argparse.ArgumentParser(prog="codegraph")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index"); p.add_argument("root"); p.add_argument("--db", required=True); p.add_argument("--name"); p.add_argument("--scip", action="append"); p.add_argument("--gates", help="gate scenarios JSON (e.g. examples/bookstore.gates.json)")
    p = sub.add_parser("detect"); p.add_argument("root")
    p = sub.add_parser("coverage", help="which source files / languages the index covers: exact, heuristic, skipped (indexer missing) or unsupported")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true")
    p = sub.add_parser("link", help="combine a backend and a frontend graph and match client HTTP calls to backend routes")
    p.add_argument("--backend", required=True); p.add_argument("--frontend", required=True); p.add_argument("--db", required=True)
    p.add_argument("--backend-name", default="backend"); p.add_argument("--frontend-name", default="frontend")
    p.add_argument("--report", help="write <prefix>.json/.md match report")
    p = sub.add_parser("path"); p.add_argument("src"); p.add_argument("dst"); p.add_argument("--db", required=True)
    p.add_argument("--min-confidence", default="heuristic", choices=["heuristic", "resolved", "exact"])
    p = sub.add_parser("resolutions", help="every place a concept (e.g. timezone) is resolved, with fallback chains and divergence")
    p.add_argument("concept"); p.add_argument("--db", required=True); p.add_argument("--within", help="substring filter on the owning function fqn (e.g. Report)")
    p.add_argument("--no-client", action="store_true"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("serve", help="local web UI over a graph DB")
    p.add_argument("--db", required=True); p.add_argument("--port", type=int, default=8177); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--plans-dir")
    p.add_argument("--presets", help="JSON list of canned queries for the UI (default: the bundled sample presets)")
    p = sub.add_parser("viz-export", help="self-contained HTML view of one query (opens from disk, no server)")
    p.add_argument("mode", choices=["reaches", "impact", "downstream", "path"]); p.add_argument("specs", nargs="+")
    p.add_argument("--db", required=True); p.add_argument("-o", "--out", required=True)
    p.add_argument("--sinks", help="downstream sink kinds, comma separated (e.g. table,column)")
    p.add_argument("--min-confidence", default="heuristic", choices=["heuristic", "resolved", "exact"])
    p = sub.add_parser("plan", help="planned-change layer: plans/<name>.yaml overlaid on the graph (list/load/validate/check/baseline)")
    p.add_argument("action", choices=["list", "load", "validate", "check", "baseline"]); p.add_argument("name", nargs="?")
    p.add_argument("--db"); p.add_argument("--plans-dir"); p.add_argument("--verify", action="store_true", help="after implement + re-index: planned nodes/edges must now exist")
    p.add_argument("--json", action="store_true"); p.add_argument("-o", "--out", help="also write the report to this file")
    p.add_argument("--max-items", type=int, default=60)
    p.add_argument("--summary", action="store_true", help="compact report: counts per section and check plus the top --max-items items")
    p = sub.add_parser("routes", help="routes with their middleware / guards / auth; scope by what they write or reach, filter by missing guards")
    p.add_argument("--db", required=True)
    p.add_argument("--writes", nargs="?", const="*", metavar="TABLE", help="routes reaching a DB write (any table, or TABLE)")
    p.add_argument("--reaches", nargs="+", metavar="SPEC", help="routes reaching any of these nodes (table, column, connection:, env:, Class::method)")
    p.add_argument("--missing", metavar="NAME", help="keep routes with no guard whose name contains NAME (e.g. auth:api, ApiKeyGuard)")
    p.add_argument("--unguarded", action="store_true", help="keep routes with no auth-like guard (name-based, see --auth-pattern)")
    p.add_argument("--auth-pattern", help="extra regex for guard names that count as auth")
    p.add_argument("--min-confidence", default="heuristic", choices=["heuristic", "resolved", "exact"])
    p.add_argument("--max-items", type=int, default=200); p.add_argument("--no-paths", action="store_true"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("search", help="nodes by name / FQN substring, plus route middleware / guard / auth names")
    p.add_argument("name"); p.add_argument("--db", required=True); p.add_argument("--kind"); p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("channels", help="broadcast channels: who can join (auth callback + checks), which events publish on it, which client code / pages listen")
    p.add_argument("pattern", nargs="?", help="channel pattern or concrete name (orders.{id}, orders.42, orders.*); omit to list all")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true"); p.add_argument("--no-source", action="store_true")
    p = sub.add_parser("tests", help="tests covering a symbol / route / table: direct (test code calls it) and transitive (through app code)")
    p.add_argument("spec", help="Class::method, Class, route:VERB /uri, `VERB /path`, /path, table.column ...")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true"); p.add_argument("--no-paths", action="store_true")
    p.add_argument("--min-confidence", default="heuristic", choices=["heuristic", "resolved", "exact"])
    p = sub.add_parser("viz-plan", help="self-contained HTML overlay of a plan on the real graph")
    p.add_argument("name"); p.add_argument("--db", required=True); p.add_argument("-o", "--out", required=True); p.add_argument("--plans-dir")
    for name in ("reaches", "siblings", "writers", "impact", "stats", "node", "downstream", "api-calls"):
        p = sub.add_parser(name)
        if name == "reaches":
            p.add_argument("specs", nargs="+")
        elif name == "api-calls":
            p.add_argument("spec", help="all | unmatched | a substring | a * glob ('GET /v1/*/orders*', '*useOrders*')")
        elif name != "stats":
            p.add_argument("spec")
        p.add_argument("--db", required=True)
        p.add_argument("--json", action="store_true")
        p.add_argument("--min-confidence", default="heuristic", choices=["heuristic", "resolved", "exact"])
        p.add_argument("--no-paths", action="store_true")
        p.add_argument("--max-depth", type=int, default=30)
        p.add_argument("--gate", default="auto", help="gate scenario for live/gated split (default: the one indexed; 'none' to disable)")
        if name == "impact":
            p.add_argument("--plans-dir", help="also list external clients from the snapshot files in this directory (e.g. examples/plans)")
    a = ap.parse_args(argv)

    if a.cmd == "index":
        from .indexer import index_project
        st = index_project(a.root, a.db, a.name, a.scip, gates=a.gates)
        print(json.dumps(st, indent=2, default=str))
        from .coverage import render
        print(render({"": st.get("coverage")}), file=sys.stderr)  # stdout stays pure JSON
        return
    if a.cmd == "coverage":
        from .core.store import GraphStore as _GS
        from .coverage import for_graph, render
        if not os.path.isfile(a.db) or os.path.getsize(a.db) == 0:
            print(f"no graph at {a.db}: run `cg index` first", file=sys.stderr)
            return 2
        covs = for_graph(_GS(a.db))
        print(json.dumps(covs, indent=2) if a.json else render(covs))
        return
    if a.cmd == "link":
        from .link import link, write_match_report
        res = link(a.backend, a.frontend, a.db, a.backend_name, a.frontend_name)
        if a.report:
            write_match_report(res, a.report)
        print(json.dumps(res["stats"], indent=2))
        return
    if a.cmd == "detect":
        from .core.detect import detect
        print(json.dumps(detect(a.root), indent=2))
        return
    if a.cmd == "viz-export":
        from .viz.server import export_html
        print(export_html(a.db, a.out, a.mode, a.specs, a.min_confidence, a.sinks.split(",") if a.sinks else None))
        return
    if a.cmd == "plan":
        return plan_cmd(a)
    if a.cmd == "viz-plan":
        from .viz.server import export_plan_html
        print(export_plan_html(a.db, a.out, a.name, plans_root=a.plans_dir))
        return
    if a.cmd == "serve":
        from .viz.server import serve
        serve(a.db, a.host, a.port, plans=a.plans_dir, presets=a.presets)
        return
    st = GraphStore(a.db)
    if a.cmd == "resolutions":
        from .concepts import resolutions, render_resolutions
        res = resolutions(st, a.concept, within=a.within, client=not a.no_client)
        print(json.dumps(res, indent=1, default=str) if a.json else render_resolutions(res))
        return
    if a.cmd == "routes":
        from . import routes as R
        res = R.routes_report(st, writes=a.writes, reaches=a.reaches, missing=a.missing, unguarded=a.unguarded,
                              auth_pattern=a.auth_pattern, min_conf=a.min_confidence)
        print(json.dumps(res, indent=1, default=str) if a.json else R.render_routes(res, st, max_items=a.max_items, paths=not a.no_paths))
        return
    if a.cmd == "channels":
        from .realtime import channels, render_channels
        res = channels(st, a.pattern, with_source=not a.no_source)
        print(json.dumps(res, indent=1, default=str) if a.json else render_channels(res))
        return
    if a.cmd == "tests":
        res = Q.tests_covering(st, a.spec, min_conf=a.min_confidence)
        print(json.dumps(res, indent=1, default=str) if a.json else Q.render_tests_covering(res, show_paths=not a.no_paths))
        return
    if a.cmd == "search":
        res = Q.search(st, a.name, kind=a.kind, limit=a.limit)
        print(json.dumps(res, indent=1, default=str) if a.json else Q.render_search(res))
        return
    if a.cmd == "reaches":
        res = Q.reaches(st, a.specs, min_conf=a.min_confidence, max_depth=a.max_depth, gate=None if a.gate == "none" else a.gate)
        print(json.dumps(res, indent=1, default=str) if a.json else Q.render_reaches(res, show_paths=not a.no_paths))
    elif a.cmd == "impact":
        res = Q.impact(st, a.spec, min_conf=a.min_confidence)
        if a.json:
            print(json.dumps(res, indent=1, default=str)); return
        print(f"targets: {res['targets'][:5]}")
        if not res["targets"]:
            print(f"no method matches {a.spec!r}; try `search` with part of the name"); return
        if not res["callers"] and not res["entry_points"]:
            print(Q.explain_no_callers(st, a.spec, res["targets"])); return
        print(f"callers (transitive): {len(res['callers'])}")
        for c in res["callers"]:
            loc = f"  @ {c['file']}:{c['line']}" if Q.NATIVE_FILE_RE.search(c.get("file") or "") else ""
            print(f"  d={c['depth']} [{c['module'] or c['kind']}] {c['fqn']}{loc}")
        print(f"entry points: {len(res['entry_points'])}")
        for e in res["entry_points"]:
            native = Q.NATIVE_FILE_RE.search(e.get("file") or "")
            nm = f"{e.get('fqn') or e['name']}  @ {e['file']}:{e['line']}" if native else e["name"]
            print(f"  {e['entry_kind']:16} {nm}  conf={e['path_confidence']}")
            if not a.no_paths:
                print(f"        path: {Q.fmt_path(e['path'])}")
        if a.plans_dir:
            from . import plans as P
            hits = P.snapshot_clients(st, [e["id"] for e in res["entry_points"] if e["id"].startswith("route:")], a.plans_dir)
            print(f"external clients (snapshot, not indexed): {len(hits)}")
            for h in hits:
                ev = f"{h['repo']}@{h['commit']}:{h['file']}" + (f":{h['line']}" if h.get("line") else "")
                print(f"  {h['method']} {h['path']} -> {h['route'].split(':', 1)[1]}  @ {ev}"
                      + (f"  sends {', '.join(h['sends'])}" if h["sends"] else "") + f"  [{h['snapshot']}]")
    elif a.cmd == "writers":
        rows = Q.writers(st, a.spec)
        if a.json:
            print(json.dumps(rows, indent=1)); return
        if not rows:
            print(Q.explain_no_writers(st, a.spec)); return
        for r in rows:
            ek = ",".join(sorted(r["entry_kinds"]))
            print(f"[{r['module']}] {r['fqn']}  {r['kind']} {r['dst']}  @{r['file']}:{r['line']} ({r['confidence']})  entries: {ek}")
        print(f"{len(rows)} write edges, {len({r['src'] for r in rows})} writers")
    elif a.cmd == "siblings":
        res = Q.siblings(st, a.spec)
        print(json.dumps(res, indent=1, default=str) if a.json else Q.render_siblings(st, a.spec, res))
    elif a.cmd == "node":
        for nid in Q.resolve_targets(st, a.spec)[:20]:
            n = st.node(nid)
            print(json.dumps(dict(n), indent=1))
            for e in st.q("SELECT kind,dst,file,line,confidence FROM edges WHERE src=? ORDER BY kind,line", (nid,)):
                print(f"   -> {e['kind']} {e['dst']} @{e['file']}:{e['line']} {e['confidence']}")
            for e in st.q("SELECT kind,src,file,line,confidence FROM edges WHERE dst=? ORDER BY kind,line", (nid,)):
                print(f"   <- {e['kind']} {e['src']} @{e['file']}:{e['line']} {e['confidence']}")
    elif a.cmd == "path":
        p = Q.path_between(st, a.src, a.dst, min_conf=a.min_confidence)
        print(Q.fmt_path(p) if p else Q.explain_no_path(st, a.src, a.dst, a.min_confidence))
        for n in Q.path_notes(st, p) if p else []:
            print(f"note: {n}")
        if not p:
            raise SystemExit(1)  # scripts can tell "no path" apart from a path
    elif a.cmd == "downstream":
        res = Q.downstream(st, a.spec, min_conf=a.min_confidence, max_depth=a.max_depth, gate=None if a.gate == "none" else a.gate)
        print(json.dumps(res, indent=1, default=str) if a.json else Q.render_downstream(res, show_paths=not a.no_paths))
    elif a.cmd == "api-calls":
        rows = Q.api_calls(st, a.spec)
        print(json.dumps(rows, indent=1, default=str) if a.json else Q.render_api_calls(rows))
    elif a.cmd == "stats":
        m = st.meta()
        print(json.dumps(m.get("stats"), indent=1, default=str))
        print("nodes by kind:")
        for r in st.q("SELECT kind, COUNT(*) c FROM nodes GROUP BY kind ORDER BY c DESC"):
            print(f"  {r['kind']:16} {r['c']}")
        print("edges by kind / confidence:")
        for r in st.q("SELECT kind, confidence, COUNT(*) c FROM edges GROUP BY kind, confidence ORDER BY kind, confidence"):
            print(f"  {r['kind']:22} {r['confidence']:10} {r['c']}")


def plan_cmd(a) -> int:
    from . import plans as P
    if a.action == "list":
        rows = P.list_plans(a.plans_dir)
        if a.json:
            print(json.dumps(rows, indent=1)); return 0
        for r in rows:
            if r.get("error"):
                print(f"{r['name']}: ERROR {r['error']}"); continue
            c = r["counts"]
            print(f"{r['name']} [{r['status']}] {r['title']}  (+{c['add_nodes']} nodes, ~{c['modify']} modified, +{c['add_edges']} edges, "
                  f"{c['forbid']} forbidden, {c['require']} required; schema errors {r['schema_errors']})  {r['file']}")
        return 0
    if not a.name:
        raise SystemExit("plan name (or path) required")
    plan = P.load_plan(a.name, a.plans_dir)
    if a.action == "load":
        if a.json:
            print(json.dumps({k: v for k, v in plan.items() if not k.startswith("_") or k == "_schema_errors"}, indent=1, default=str))
        else:
            print(P.render_load(plan))
        return 0
    if not a.db:
        raise SystemExit("--db required")
    st = GraphStore(a.db)
    if a.action == "validate":
        v = P.validate(st, plan)
        print(json.dumps(v, indent=1, default=str) if a.json else P.render_validate(v))
        return 0 if v["ok"] else 1
    if a.action == "baseline":
        b = P.make_baseline(st, plan)
        P.baseline_path(plan).write_text(json.dumps(b, indent=1) + "\n")
        print(f"baseline for {len(b['targets'])} modified targets -> {P.baseline_path(plan)}")
        return 0
    res = P.check(st, plan, verify=a.verify, baseline=P.load_baseline(plan) if a.verify else None)
    if a.json:
        txt = json.dumps(res, indent=1, default=str)
    elif a.summary:
        txt = P.render_check_summary(res, max_items=a.max_items if a.max_items != 60 else 5)
    else:
        txt = P.render_check(res, max_items=a.max_items)
    print(txt)
    if a.out:
        from pathlib import Path
        Path(a.out).write_text(txt + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

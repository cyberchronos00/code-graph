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
  python -m codegraph.cli bridges [PATTERN] --db ... [--protocol capacitor] [--unmatched]   (web / native bridge calls)
  python -m codegraph.cli external [PATTERN] --db ... [--protocol P] [--source S] [--tls-off]   (databases, caches, brokers, mail ... and who reaches them)
  python -m codegraph.cli tools [PATTERN] --db ... [--framework F] [--agent A] [--unmatched]   (LLM / MCP tools: handler, tables, agents, checks)
  python -m codegraph.cli protocols [PATTERN] --db ... [--protocol P] [--side send|receive] [--unmatched]   (every protocol endpoint: senders, receivers, checks)
  python -m codegraph.cli platforms [summary|divergence] --db ... [--target ios]   (platform-specific code, gaps between variants)
  reaches / impact / downstream / path / routes / search take --platform TARGET: only code built for that target
spec forms: table.column | connection:<name> (glob *) | env:<KEY*> | config:<a.b> | Class.method or Class::method (either
  separator, every language) | Class
  | src/app.ts#listOrders (TS / JS symbol in one file; path suffix ok)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .core.store import GraphStore
from . import query as Q
from .bridges import PROTOCOLS as BRIDGE_PROTOCOLS


def main(argv=None):
    from . import __version__
    ap = argparse.ArgumentParser(prog="cg")
    ap.add_argument("--version", action="version", version=f"cg {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index", help="detect languages / frameworks under ROOT and build the graph DB (stats JSON on stdout, coverage on stderr)"); p.add_argument("root"); p.add_argument("--db", required=True); p.add_argument("--name"); p.add_argument("--scip", action="append"); p.add_argument("--gates", help="gate scenarios JSON (e.g. examples/bookstore.gates.json)")
    p.add_argument("--python-root", action="append", metavar="DIR",
                   help="Python source root, relative to ROOT (repeatable); replaces detection and python.source_roots in .cg.yaml")
    p.add_argument("--no-apps", action="store_true",
                   help="index ROOT as one project although its .cg.yaml lists monorepo apps")
    p.add_argument("--include-generated", action="store_true",
                   help="also index generated, copied and vendored files (labelled attrs.generated); default: excluded and listed by `cg coverage`")
    p = sub.add_parser("detect", help="the languages and frameworks detected under ROOT, without indexing"); p.add_argument("root")
    p = sub.add_parser("config", help="project config: `show` the effective configuration and where each value comes from, "
                                      "`validate` a .cg.yaml")
    p.add_argument("action", choices=["show", "validate"]); p.add_argument("root", nargs="?", default=".", help="indexed root (or, for validate, a config file)")
    p.add_argument("--python-root", action="append", metavar="DIR", help="as for index")
    p.add_argument("--gates", help="as for index"); p.add_argument("--auth-pattern", help="as for routes")
    p.add_argument("--plans-dir", help="as for plan / serve"); p.add_argument("--presets", help="as for serve")
    p.add_argument("--no-apps", action="store_true",
                   help="index ROOT as one project although its .cg.yaml lists monorepo apps")
    p.add_argument("--include-generated", action="store_true", help="as for index")
    p.add_argument("--json", action="store_true", help="the effective configuration as JSON")
    p = sub.add_parser("starters", help="starter queries derived from the graph (unguarded write routes, most-reached tables, ...)")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true")
    p = sub.add_parser("doctor", help="what this installation can index: tool versions, extractor dependencies, exact or "
                                      "heuristic mode per language and why, and what to install")
    p.add_argument("root", nargs="?", help="project root: also check project conditions and list only its languages")
    p.add_argument("--scip", action="append", metavar="FILE",
                   help="check a SCIP index: documents, occurrences with a usable position, definitions (repeatable)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("clean", help="remove cache entries: one project's (ROOT), stale ones (--stale) or all (--all); "
                                     "--db also deletes a graph DB")
    p.add_argument("root", nargs="?", help="project root: remove the cache entries of this project and of every "
                                           "project indexed below it")
    p.add_argument("--all", action="store_true", help="empty the whole cache root (keeps the extractors)")
    p.add_argument("--extractors", action="store_true", help="with --all: also remove the extractor installs")
    p.add_argument("--stale", action="store_true",
                   help="only entries of older cache versions / layouts and orphaned .tmp / .lock files")
    p.add_argument("--db", help="also delete this graph DB and its -wal / -shm files")
    p.add_argument("--dry-run", action="store_true", help="list what would be removed, delete nothing")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("setup", help="install the Node / PHP / Dart extractor dependencies (into the user cache; "
                                     "otherwise done on the first index)")
    p.add_argument("languages", nargs="*", metavar="LANG",
                   help="typescript, php, dart (default: every one whose toolchain is installed)")
    p.add_argument("--quiet", action="store_true")
    p = sub.add_parser("coverage", help="which source files / languages the index covers: exact, heuristic, skipped (indexer missing) or unsupported")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true")
    p.add_argument("--details", action="store_true", help="the full report: file lists (the first 5 per bucket), fix hints, "
                                                          "syntax error lines, Python source roots (default: a short summary)")
    p.add_argument("--all-files", action="store_true", help="the full report with every file per bucket, excluded files too")
    p = sub.add_parser("link", help="combine a backend and a frontend graph and match client HTTP calls to backend routes")
    p.add_argument("--backend", required=True); p.add_argument("--frontend", required=True); p.add_argument("--db", required=True)
    p.add_argument("--backend-name", default="backend"); p.add_argument("--frontend-name", default="frontend")
    p.add_argument("--report", help="write <prefix>.json/.md match report")
    p = sub.add_parser("path", help="one shortest evidence chain from SRC to DST (exit status 1 when there is none)"); p.add_argument("src"); p.add_argument("dst"); p.add_argument("--db", required=True)
    p.add_argument("--min-confidence", default="heuristic", choices=["heuristic", "resolved", "exact"])
    p.add_argument("--platform", help="only code built for this target (windows, linux, macos, ios, android, web; see docs/platforms.md)")
    p = sub.add_parser("platforms", help="platform-specific code: targets, tagged symbols per target, and divergence "
                                         "(variants missing a target, API differences, references to code missing on a target)")
    p.add_argument("action", nargs="?", default="summary", choices=["summary", "divergence"])
    p.add_argument("--db", required=True); p.add_argument("--target", help="divergence: only findings that affect this target")
    p.add_argument("--kind", choices=["variants", "api_surface", "missing_callee"], help="divergence: one finding kind")
    p.add_argument("--max-items", type=int, default=40); p.add_argument("--json", action="store_true")
    p = sub.add_parser("resolutions", help="every place a concept (e.g. timezone) is resolved, with fallback chains and divergence")
    p.add_argument("concept"); p.add_argument("--db", required=True); p.add_argument("--within", help="substring filter on the owning function fqn (e.g. Report)")
    p.add_argument("--no-client", action="store_true"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("serve", help="local web UI over a graph DB")
    p.add_argument("--db", required=True); p.add_argument("--port", type=int, default=8177); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--plans-dir")
    p.add_argument("--presets", help="JSON list of canned queries for the starter cards (default: viz.presets in .cg.yaml, then the sample presets that resolve, then the starter queries)")
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
    p.add_argument("--reaches", nargs="+", metavar="SPEC", help="routes reaching any of these nodes (table, column, connection:, env:, Class.method)")
    p.add_argument("--missing", metavar="NAME", help="keep routes with no guard whose name contains NAME (e.g. auth:api, ApiKeyGuard)")
    p.add_argument("--unguarded", action="store_true", help="keep routes with no auth guard (framework presets, the auth name pattern, .cg.yaml auth.extra_patterns and --auth-pattern)")
    p.add_argument("--auth-pattern", help="extra regex for guard names that count as auth")
    p.add_argument("--min-confidence", default="heuristic", choices=["heuristic", "resolved", "exact"])
    p.add_argument("--max-items", type=int, default=200); p.add_argument("--no-paths", action="store_true"); p.add_argument("--json", action="store_true")
    p.add_argument("--platform", help="only code built for this target (windows, linux, macos, ios, android, web; see docs/platforms.md)")
    p = sub.add_parser("search", help="nodes by name / FQN substring, plus route middleware / guard / auth names")
    p.add_argument("name"); p.add_argument("--db", required=True); p.add_argument("--kind"); p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true"); p.add_argument("--platform", help="only code built for this target (windows, linux, macos, ios, android, web; see docs/platforms.md)")
    p = sub.add_parser("channels", help="broadcast channels: who can join (auth callback + checks), which events publish on it, which client code / pages listen")
    p.add_argument("pattern", nargs="?", help="channel pattern or concrete name (orders.{id}, orders.42, orders.*); omit to list all")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true"); p.add_argument("--no-source", action="store_true")
    p = sub.add_parser("bridges", help="web / native bridge calls (Capacitor plugins, React Native / Expo modules, Flutter platform "
                                        "channels): JS / Dart senders, native receivers per platform, methods missing on a platform")
    p.add_argument("pattern", nargs="?", help="endpoint name, substring or glob (Echo#echo, Echo, samples.flutter.dev/*); omit to list all")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true")
    p.add_argument("--protocol", choices=sorted(BRIDGE_PROTOCOLS))
    p.add_argument("--unmatched", action="store_true", help="only endpoints with a check: missing on a platform, no receiver, no sender, external")
    p = sub.add_parser("protocols", help="protocol endpoints (HTTP, Pusher channels, Nest messages, jobs, events, bridges, MQTT / "
                                          "Socket.IO ...): summary per protocol, or senders, receivers, guards, matches and checks per endpoint")
    p.add_argument("pattern", nargs="?", help="endpoint name, id, substring or glob (`orders.*`, `http:GET /api/*`); omit for the summary")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true")
    p.add_argument("--protocol", help="one protocol (http, pusher, nest-rpc, bull, laravel-queue, socketio, mqtt, capacitor, ...)")
    p.add_argument("--side", choices=["send", "receive"]); p.add_argument("--max-items", type=int, default=60)
    p.add_argument("--unmatched", action="store_true", help="only endpoints with a check (no_receiver, no_sender, ambiguous, "
                                                            "schema_mismatch, unguarded) or an external peer")
    p = sub.add_parser("external", help="external systems (databases, caches, brokers, mail, directories, file transfer, object "
                                         "stores, third-party HTTP hosts): users, entry points, address and credential sources")
    p.add_argument("pattern", nargs="?", help="external id, substring or glob (`external:postgres:*`, `redis`)")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true")
    p.add_argument("--protocol", help="postgres, mysql, redis, smtp, amqp, mongodb, ldap, ssh, ftp, s3, https, ...")
    p.add_argument("--source", help="address or credential source: literal, env, env-example, compose, config")
    p.add_argument("--tls-off", action="store_true", help="only systems known to be reached without TLS")
    p.add_argument("--max-items", type=int, default=60)
    p = sub.add_parser("tools", help="LLM tools and MCP tools / resources / prompts: handler, tables it reaches, agents "
                                      "offering it, callers, checks; agents, dynamic dispatch, model calls")
    p.add_argument("pattern", nargs="?", help="tool name, substring or glob")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true")
    p.add_argument("--framework", help="mcp, openai, anthropic, langchain, openai-agents, llamaindex, custom")
    p.add_argument("--agent", help="only tools this agent offers (name or glob)")
    p.add_argument("--unmatched", action="store_true", help="only tools with a check (no_receiver, no_sender, name_collision)")
    p.add_argument("--max-items", type=int, default=60)
    p = sub.add_parser("tests", help="tests covering a symbol / route / table: direct (test code calls it) and transitive (through app code)")
    p.add_argument("spec", help="Class.method or Class::method (either separator, any language), Class, route:VERB /uri, `VERB /path`, /path, table.column ...")
    p.add_argument("--db", required=True); p.add_argument("--json", action="store_true"); p.add_argument("--no-paths", action="store_true")
    p.add_argument("--min-confidence", default="heuristic", choices=["heuristic", "resolved", "exact"])
    p.add_argument("--max-depth", type=int, default=3, help="transitive tests at most N hops from the target (0: any depth)")
    p.add_argument("--unit-only", action="store_true", help="leave out UI / snapshot / screenshot tests")
    p.add_argument("--exclude-root", action="append", default=[], help="a symbol transitive paths must not run through (repeatable)")
    p.add_argument("--through-roots", action="store_true", help="keep paths through app entry points (@main, App.body, MainActivity)")
    p = sub.add_parser("parity", help="port gap report: types, functions, enum cases and constants of --db with no "
                                       "counterpart in --against (e.g. an iOS app and its Android port)")
    p.add_argument("--db", required=True, help="source graph"); p.add_argument("--against", required=True, help="target graph")
    p.add_argument("--map", help="JSON file {\"source name\": \"target name\"} for renames")
    p.add_argument("--no-fuzzy", action="store_true", help="no fuzzy (shortened / plural word) name matches")
    p.add_argument("--strip-prefix", action="append", default=[], help="a name prefix one side adds (`Vault` in "
                   "VaultAddEditState for AddEditState), ignored when matching (repeatable)")
    p.add_argument("--json", action="store_true"); p.add_argument("--max-items", type=int, default=200)
    p = sub.add_parser("viz-plan", help="self-contained HTML overlay of a plan on the real graph")
    p.add_argument("name"); p.add_argument("--db", required=True); p.add_argument("-o", "--out", required=True); p.add_argument("--plans-dir")
    helps = {"reaches": "everything that depends on the targets, grouped by entry classification",
             "siblings": "code related to a symbol: class hierarchy, the same method in sibling classes, shared resources, co-callers",
             "writers": "code that writes a table (or column)",
             "impact": "callers of a method up to their entry points (reverse walk), overrides listed apart",
             "stats": "node / edge counts of a graph DB",
             "node": "one node's details and its incoming / outgoing edges",
             "downstream": "forward dependencies of a symbol or page (calls, HTTP, routes, services, tables)",
             "api-calls": "client HTTP calls with call sites, request keys and the matched route"}
    for name in ("reaches", "siblings", "writers", "impact", "stats", "node", "downstream", "api-calls"):
        p = sub.add_parser(name, help=helps[name])
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
        if name in ("reaches", "impact", "downstream"):
            p.add_argument("--platform", help="only code built for this target (windows, linux, macos, ios, android, web; see docs/platforms.md)")
        if name == "impact":
            p.add_argument("--plans-dir", help="also list external clients from the snapshot files in this directory (e.g. examples/plans)")
    a = ap.parse_args(argv)

    if a.cmd == "index":
        try:
            from .indexer import index_project
        except (SyntaxError, ImportError) as ex:   # a plugin this interpreter cannot load: name it, point to doctor
            from .doctor import _describe
            print(f"cg index: cannot load the indexer on Python {sys.version.split()[0]}: {_describe(ex)}\n"
                  "run `cg doctor` for details; upgrading cg (`uv tool upgrade codegraph`) usually fixes it", file=sys.stderr)
            return 2
        from .config import ConfigError, load as load_config
        try:
            cfg = load_config(a.root) if not a.no_apps else {}
            if cfg.get("apps"):        # monorepo: index each app, link each frontend / backend pair
                from .apps import index_apps, render as render_apps
                summary = index_apps(a.root, a.db, cfg, a.scip, python_roots=a.python_root,
                                     include_generated=a.include_generated)
                print(json.dumps({k: v for k, v in summary.items()}, indent=2, default=str))
                print(render_apps(summary), file=sys.stderr)
                return
            st = index_project(a.root, a.db, a.name, a.scip, gates=a.gates, python_roots=a.python_root,
                               include_generated=a.include_generated)
        except ConfigError as ex:
            print(f"cg index: {ex}", file=sys.stderr)
            return 2
        print(json.dumps(st, indent=2, default=str))
        from .coverage import render_summary
        print(render_summary({"": st.get("coverage")}, db=a.db), file=sys.stderr)  # stdout stays pure JSON
        return
    if a.cmd == "doctor":
        from .doctor import render, report
        r = report(a.root, scip=a.scip)
        print(json.dumps(r, indent=2) if a.json else render(r))
        broken = bool((r.get("modules") or {}).get("failed"))
        return 1 if broken or any(x.get("warning") for x in r.get("scip") or ()) else 0
    if a.cmd == "clean":
        from .core import cache as C
        try:
            pl = C.plan(a.root, all_=a.all, extractors=a.extractors, stale=a.stale, db=a.db)
        except C.CacheError as ex:
            print(f"cg clean: {ex}", file=sys.stderr)
            return 2
        errors = [] if a.dry_run else C.execute(pl)
        if a.json:
            print(json.dumps({"root": str(pl.root), "dry_run": a.dry_run, "bytes": pl.bytes, "projects": pl.projects,
                              "removed" if not a.dry_run else "would_remove":
                                  [{"path": str(e.path), "kind": e.kind, "bytes": e.size,
                                    **({"stale": e.stale} if e.stale else {})} for e in pl.remove]
                                  + [{"path": str(x), "kind": "db", "bytes": C._size(x)} for x in pl.db_files],
                              "skipped": [{"path": str(x), "reason": r} for x, r in pl.skipped],
                              "errors": [{"path": str(x), "error": r} for x, r in errors]}, indent=2))
        else:
            verb = "would remove" if a.dry_run else "removed"
            for e in pl.remove:
                print(f"  {C.human(e.size):>9}  {e.kind:<13} {e.path}" + (f"  ({e.stale})" if e.stale else ""))
            for x in pl.db_files:
                print(f"  {C.human(C._size(x)):>9}  {'db':<13} {x}")
            for x, r in pl.skipped:
                print(f"  skipped {x} ({r})")
            for x, r in errors:
                print(f"  error {x}: {r}", file=sys.stderr)
            n = len(pl.remove) + len(pl.db_files) - len(errors)
            print(f"cg clean: {verb} {n} entr{'y' if n == 1 else 'ies'}, {C.human(pl.bytes)} (cache root {pl.root})"
                  + (f"; projects: {', '.join(pl.projects)}" if pl.projects else ""))
        return 1 if errors else 0
    if a.cmd == "setup":
        from .doctor import setup
        bad = [x for x in a.languages if x not in ("typescript", "php", "dart")]
        if bad:
            print(f"cg setup: unknown language {bad[0]!r} (typescript, php, dart)", file=sys.stderr)
            return 2
        return setup(a.languages or None, quiet=a.quiet)
    if a.cmd == "coverage":
        from .core.store import GraphStore as _GS
        from .coverage import for_graph, render, render_summary
        if not os.path.isfile(a.db) or os.path.getsize(a.db) == 0:
            print(f"no graph at {a.db}: run `cg index` first", file=sys.stderr)
            return 2
        covs = for_graph(_GS(a.db))
        if a.json:
            print(json.dumps(covs, indent=2))
        else:
            print(render(covs, all_files=a.all_files) if a.details or a.all_files else render_summary(covs))
        return
    if a.cmd == "link":
        from .link import link, write_match_report
        res = link(a.backend, a.frontend, a.db, a.backend_name, a.frontend_name)
        if a.report:
            write_match_report(res, a.report)
        print(json.dumps(res["stats"], indent=2))
        return
    if a.cmd == "config":
        return config_cmd(a)
    if a.cmd == "starters":
        from .starters import for_graph, render as render_starters
        rows = for_graph(GraphStore(a.db))
        print(json.dumps(rows, indent=1) if a.json else render_starters(rows))
        return
    if getattr(a, "plans_dir", None) is None and a.cmd in ("plan", "serve", "viz-plan", "impact") and getattr(a, "db", None):
        from .plans import resolve_plans_dir
        a.plans_dir = resolve_plans_dir(None, a.db)     # plans.dir of the indexed project's .cg.yaml
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
    if a.cmd == "parity":
        from . import parity as PA
        mapping = json.loads(open(a.map, encoding="utf-8").read()) if a.map else None
        res = PA.parity(a.db, a.against, mapping=mapping, fuzzy=not a.no_fuzzy, strip_prefixes=a.strip_prefix)
        print(json.dumps(res, indent=1, default=str) if a.json else PA.render(res, max_items=a.max_items))
        return
    if a.cmd == "serve":
        from .viz.server import serve
        serve(a.db, a.host, a.port, plans=a.plans_dir, presets=a.presets)
        return
    if getattr(a, "platform", None):
        from .platforms import resolve_platform
        try:
            a.platform = resolve_platform(a.platform)
        except ValueError as ex:
            print(f"cg {a.cmd}: {ex}", file=sys.stderr)
            return 2
    st = GraphStore(a.db)
    if a.cmd == "platforms":
        from . import platforms as PF
        if a.action == "summary":
            s = PF.summary(st)
            print(json.dumps(s, indent=1, default=str) if a.json else PF.render_summary(s))
            return
        tgt = None
        if a.target:
            try:
                tgt = PF.resolve_platform(a.target)
            except ValueError as ex:
                print(f"cg platforms: {ex}", file=sys.stderr)
                return 2
        res = PF.divergence(st, kind=a.kind, target=tgt)
        print(json.dumps(res, indent=1, default=str) if a.json else PF.render_divergence(res, limit=a.max_items))
        return
    if a.cmd == "resolutions":
        from .concepts import resolutions, render_resolutions
        res = resolutions(st, a.concept, within=a.within, client=not a.no_client)
        print(json.dumps(res, indent=1, default=str) if a.json else render_resolutions(res))
        return
    if a.cmd == "routes":
        from . import routes as R
        res = R.routes_report(st, writes=a.writes, reaches=a.reaches, missing=a.missing, unguarded=a.unguarded,
                              auth_pattern=a.auth_pattern, min_conf=a.min_confidence, platform=a.platform)
        if a.json:
            res["completeness"] = R.route_completeness(st)
        print(json.dumps(res, indent=1, default=str) if a.json else R.render_routes(res, st, max_items=a.max_items, paths=not a.no_paths))
        return
    if a.cmd == "channels":
        from .realtime import channels, render_channels
        res = channels(st, a.pattern, with_source=not a.no_source)
        print(json.dumps(res, indent=1, default=str) if a.json else render_channels(res))
        return
    if a.cmd == "bridges":
        from .bridges import bridges, render_bridges
        res = bridges(st, a.pattern, protocol=a.protocol, unmatched=a.unmatched)
        print(json.dumps(res, indent=1, default=str) if a.json else render_bridges(res))
        return
    if a.cmd == "external":
        from .external import external, render_external
        res = external(st, a.pattern, protocol=a.protocol, source=a.source, tls_off=a.tls_off,
                       max_items=max(a.max_items, 200) if a.json else a.max_items)
        print(json.dumps(res, indent=1, default=str) if a.json else render_external(res, max_items=a.max_items))
        return
    if a.cmd == "tools":
        from .aitools import render_tools, tools
        res = tools(st, a.pattern, framework=a.framework, unmatched=a.unmatched, agent=a.agent,
                    max_items=max(a.max_items, 200) if a.json else a.max_items)
        print(json.dumps(res, indent=1, default=str) if a.json else render_tools(res, max_items=a.max_items))
        return
    if a.cmd == "protocols":
        from .protocols.view import protocols, render_protocols
        res = protocols(st, a.pattern, protocol=a.protocol, side=a.side, unmatched=a.unmatched, max_items=max(a.max_items, 200) if a.json else a.max_items)
        print(json.dumps(res, indent=1, default=str) if a.json else render_protocols(res, max_items=a.max_items))
        return
    if a.cmd == "tests":
        res = Q.tests_covering(st, a.spec, min_conf=a.min_confidence, near_depth=a.max_depth or None,
                               unit_only=a.unit_only, exclude_roots=a.exclude_root, through_roots=a.through_roots)
        res["completeness"] = _completeness(st, res.get("targets"))
        print(json.dumps(res, indent=1, default=str) if a.json else Q.render_tests_covering(res, show_paths=not a.no_paths))
        _note(res["completeness"], a.json)
        return
    if a.cmd == "search":
        res = Q.search(st, a.name, kind=a.kind, limit=a.limit, platform=a.platform)
        print(json.dumps(res, indent=1, default=str) if a.json else Q.render_search(res))
        return
    if a.cmd == "reaches":
        res = Q.reaches(st, a.specs, min_conf=a.min_confidence, max_depth=a.max_depth, gate=None if a.gate == "none" else a.gate,
                        platform=a.platform)
        res["completeness"] = _completeness(st, [x for t in res["targets"].values() for x in t] + [i["id"] for i in res["items"]])
        print(json.dumps(res, indent=1, default=str) if a.json else Q.render_reaches(res, show_paths=not a.no_paths))
        _note(res["completeness"], a.json)
    elif a.cmd == "impact":
        res = Q.impact(st, a.spec, min_conf=a.min_confidence, platform=a.platform)
        if a.json:
            res["completeness"] = _completeness(st, list(res["targets"]) + [c["id"] for c in res["callers"]])
            print(json.dumps(res, indent=1, default=str)); return
        if res.get("platform"):
            from .platforms import render_filter
            print(render_filter(res["platform"]))
            if res["platform"].get("targets_not_built"):
                print(f"not built for {a.platform}: {', '.join(res['platform']['targets_not_built'][:6])}")
        print(f"targets: {res['targets'][:5]}")
        if not res["targets"]:
            print(f"no method matches {a.spec!r}; try `search` with part of the name"); return
        if a.platform and res["targets"] and set(res["platform"]["targets_not_built"]) == set(res["targets"]):
            print(f"{a.spec} is not built for {a.platform}: nothing calls it there (`cg platforms divergence --target "
                  f"{a.platform}` lists references to it that would not build)"); return
        for line in Q.override_lines(res):
            print(line)
        if not res["callers"] and not res["entry_points"]:
            print(Q.explain_no_callers(st, a.spec, res["targets"], a.min_confidence)); return
        print(f"callers (transitive): {len(res['callers'])}")
        for c in res["callers"]:
            loc = f"  @ {c['file']}:{c['line']}" if Q.NATIVE_FILE_RE.search(c.get("file") or "") else ""
            print(f"  d={c['depth']} [{c['module'] or c['kind']}] {c['fqn']}{loc}{Q.caller_label(c)}")
        print(f"entry points: {len(res['entry_points'])}")
        for e in res["entry_points"]:
            native = Q.NATIVE_FILE_RE.search(e.get("file") or "")
            nm = f"{e.get('fqn') or e['name']}  @ {e['file']}:{e['line']}" if native else e["name"]
            print(f"  {e['entry_kind']:16} {nm}  conf={e['path_confidence']}{Q.CANDIDATE_LABEL if e.get('candidate') else ''}"
                  f"{Q.generated_label(e)}{Q.platform_label(e)}")
            if not a.no_paths:
                print(f"        path: {Q.fmt_path(e['path'])}")
        if any(x.get("candidate") for x in res["callers"] + res["entry_points"]):
            print(Q.CANDIDATE_NOTE)
        if a.plans_dir:
            from . import plans as P
            hits = P.snapshot_clients(st, [e["id"] for e in res["entry_points"] if e["id"].startswith("route:")], a.plans_dir)
            print(f"external clients (snapshot, not indexed): {len(hits)}")
            for h in hits:
                ev = f"{h['repo']}@{h['commit']}:{h['file']}" + (f":{h['line']}" if h.get("line") else "")
                print(f"  {h['method']} {h['path']} -> {h['route'].split(':', 1)[1]}  @ {ev}"
                      + (f"  sends {', '.join(h['sends'])}" if h["sends"] else "") + f"  [{h['snapshot']}]")
        _note(_completeness(st, list(res["targets"]) + [c["id"] for c in res["callers"]]), False)
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
        ids = list(dict.fromkeys(Q.resolve_targets(st, a.spec)))[:20]
        docs = [node_doc(st, nid) for nid in ids]
        if a.json:
            print(json.dumps(docs, indent=1, default=str))
        elif not docs:
            print(f"no node matches {a.spec!r}; try `search` with part of the name")
        else:
            print("\n\n".join(render_node(d) for d in docs))
    elif a.cmd == "path":
        p = Q.path_between(st, a.src, a.dst, min_conf=a.min_confidence, platform=a.platform)
        if a.platform:
            from .platforms import filter_info, render_filter
            print(render_filter(filter_info(st, a.platform)))
        print(Q.fmt_path(p) if p else (f"on {a.platform}: " if a.platform else "") + Q.explain_no_path(st, a.src, a.dst, a.min_confidence))
        for n in Q.path_notes(st, p) if p else []:
            print(f"note: {n}")
        if not p:
            raise SystemExit(1)  # scripts can tell "no path" apart from a path
    elif a.cmd == "downstream":
        res = Q.downstream(st, a.spec, min_conf=a.min_confidence, max_depth=a.max_depth, gate=None if a.gate == "none" else a.gate,
                           platform=a.platform)
        print(json.dumps(res, indent=1, default=str) if a.json else Q.render_downstream(res, show_paths=not a.no_paths))
    elif a.cmd == "api-calls":
        rows = Q.api_calls(st, a.spec)
        print(json.dumps(rows, indent=1, default=str) if a.json else Q.render_api_calls(rows))
    elif a.cmd == "stats":
        doc = stats_doc(st)
        print(json.dumps(doc, indent=1, default=str) if a.json else render_stats(st, doc))

def node_doc(st, nid: str) -> dict:
    """One node with its attrs parsed and its outgoing / incoming edges (`cg node --json`, #75)."""
    n = dict(st.node(nid))
    try:
        n["attrs"] = json.loads(n.get("attrs") or "{}")
    except ValueError:
        pass

    def edges(col, other):
        out = []
        for e in st.q(f"SELECT kind, {other}, file, line, confidence, attrs FROM edges WHERE {col}=? ORDER BY kind, line, {other}", (nid,)):
            d = {"kind": e["kind"], other: e[other], "file": e["file"], "line": e["line"], "confidence": e["confidence"]}
            if e["attrs"] and e["attrs"] != "{}":
                d["attrs"] = json.loads(e["attrs"])
            out.append(d)
        return out
    return {"node": n, "out": edges("src", "dst"), "in": edges("dst", "src")}


def render_node(d: dict) -> str:
    """`cg node` text: the node's location, kind and attrs, then its edges; the same edge from one site (one row per
    gate scenario or accessor) is listed once with a count."""
    n, at = d["node"], d["node"].get("attrs") if isinstance(d["node"].get("attrs"), dict) else {}
    span = f"{n['file']}:{n['line']}" + (f"-{n['end_line']}" if n.get("end_line") and n["end_line"] != n["line"] else "")
    out = [f"{n['id']}  ({n['kind']}{', ' + n['lang'] if n.get('lang') else ''})",
           f"  at: {span}" if n.get("file") else "  at: (no source location)"]
    if n.get("fqn") and n["fqn"] != n.get("name"):
        out.append(f"  fqn: {n['fqn']}")
    if n.get("module"):
        out.append(f"  module: {n['module']}")
    if n.get("entry_kind"):
        out.append(f"  entry: {n['entry_kind']}")
    if "platforms" in at:
        unk = at.get("platform_unknown") or []
        ps = ", ".join(p + ("?" if p in unk else "") for p in at["platforms"]) or "none of the targets"
        out.append(f"  platforms: {ps}" + (f"  ({at['platform_expr']})" if at.get("platform_expr") else ""))
    rest = {k: v for k, v in at.items() if k not in ("platforms", "platform_unknown", "platform_expr", "platform_at", "platforms_other")}
    if rest:
        out.append("  attrs: " + ", ".join(f"{k}={v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)}"
                                         for k, v in rest.items()))
    if n.get("doc"):
        out.append("  doc: " + " ".join(str(n["doc"]).split())[:200])
    for key, arrow, other in (("out", "->", "dst"), ("in", "<-", "src")):
        es = d[key]
        out.append(f"  {'outgoing' if key == 'out' else 'incoming'} ({len(es)}):" if es else f"  {'outgoing' if key == 'out' else 'incoming'}: none")
        seen: dict = {}
        for e in es:
            k = (e["kind"], e[other], e["file"], e["line"], e["confidence"])
            seen[k] = seen.get(k, 0) + 1
        for (kind, o, f, line, conf), c in seen.items():
            out.append(f"    {arrow} {kind} {o}  @{f}:{line} {conf}" + (f"  ×{c}" if c > 1 else ""))
    return "\n".join(out)


def stats_doc(st) -> dict:
    """`cg stats --json`: one document, the index stats plus node / edge counts (#75)."""
    m = st.meta()
    nodes = {r["kind"]: r["c"] for r in st.q("SELECT kind, COUNT(*) c FROM nodes GROUP BY kind ORDER BY c DESC")}
    edges: dict = {}
    for r in st.q("SELECT kind, confidence, COUNT(*) c FROM edges GROUP BY kind, confidence ORDER BY kind, confidence"):
        edges.setdefault(r["kind"], {})[r["confidence"]] = r["c"]
    return {"project": m.get("project"), "root": m.get("root"), "indexed_at": m.get("indexed_at"),
            "nodes": sum(nodes.values()), "edges": sum(sum(v.values()) for v in edges.values()),
            "nodes_by_kind": nodes, "edges_by_kind": edges, "stats": m.get("stats")}


def render_stats(st, doc: dict) -> str:
    s = doc.get("stats") or {}
    det = s.get("detected") or {}
    out = [f"project {doc.get('project') or '?'}  root {doc.get('root') or '?'}  indexed {doc.get('indexed_at') or '?'}"
           + (f" in {s['index_seconds']} s" if s.get("index_seconds") is not None else "")]
    langs = ", ".join(det.get("languages") or {}) or ", ".join((s.get("presets") or {}).get("languages") or []) or "none"
    fws = ", ".join(sorted((s.get("presets") or {}).get("frameworks") or det.get("frameworks") or {})) or "none"
    out.append(f"languages: {langs} | frameworks: {fws} | nodes {doc['nodes']} | edges {doc['edges']}")
    from .coverage import for_graph, summary_line
    for repo, cov in for_graph(st).items():
        out.append(summary_line(cov, repo or None))
    out.append("nodes by kind:")
    out += [f"  {k:16} {c}" for k, c in doc["nodes_by_kind"].items()]
    out.append("edges by kind / confidence:")
    out += [f"  {k:22} {conf:10} {c}" for k, v in doc["edges_by_kind"].items() for conf, c in v.items()]
    out.append("full index stats (plugins, presets, platforms, coverage): cg stats --json")
    return "\n".join(out)


def _completeness(st, ids) -> dict:
    from .coverage import completeness_for
    return completeness_for(st, ids or [])


def _note(comp: dict, as_json: bool) -> None:
    """The scoped `coverage note:` line after a partial text answer (nothing for complete answers or --json)."""
    from .coverage import answer_note
    if not as_json and not comp.get("complete"):
        n = answer_note(comp)
        if n:
            print(n)


def config_cmd(a) -> int:
    from .config import ConfigError, effective, find, load_file, render_effective, unknown_key_warnings
    if a.action == "validate":
        path = a.root if os.path.isfile(a.root) else find(a.root)
        if path is None:
            print(f"no .cg.yaml / .cg.yml in {a.root}: nothing to validate (the file is optional)")
            return 0
        try:
            cfg = load_file(path)
        except ConfigError as ex:
            print(f"invalid: {ex}", file=sys.stderr)
            return 2
        keys = [k for k in cfg if k not in ("file", "ignored_keys")]
        print(f"ok: {path} ({', '.join(keys) or 'no keys'})")
        for w in unknown_key_warnings(cfg):
            print(f"warning: {w}")
        return 0
    try:
        eff = effective(a.root, a.python_root, a.gates, a.auth_pattern, a.plans_dir, a.presets, a.include_generated)
    except ConfigError as ex:
        print(f"cg config: {ex}", file=sys.stderr)
        return 2
    print(json.dumps(eff, indent=1, default=str) if a.json else render_effective(eff))
    return 0


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

"""code-graph MCP server (stdio). Exposes the SQLite graph to agents with compact, token-efficient output.

Run:  .venv/bin/python -m codegraph.mcp_server --db out/graph.db [--root path/to/project --gates path/to/gates.json] [--plans plans/]

Tools: reaches, impact, siblings, writers, routes, node, search, stats, index, downstream, path, api_calls, resolutions,
channels, tests_covering, plan_list, plan_load, plan_validate, plan_check, plan_baseline (planned-change layer,
plans/<name>.yaml).
Point --db at a combined graph (codegraph.cli link ...) to query across repos (frontend pages -> backend routes -> tables).
All results are plain text: grouped by module / entry-point kind, one line per item, each with the
shortest evidence path (KIND@file:line hops). Every edge comes from parsers and static rules, so the same graph always
gives the same answer. Paths in replies are repo-relative.
"""
from __future__ import annotations

import argparse
import functools
import sqlite3
import json
import os
import threading
from collections import defaultdict
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from .core.store import GraphStore
from . import query as Q

ROOT = Path(__file__).resolve().parents[1]
STATE = {"db": str(ROOT / "out" / "graph.db"), "root": None, "gates": None, "plans": None, "lock": threading.Lock()}

server = MCPServer(
    name="code-graph",
    instructions=(
        "Code graph of a project: classes, functions, routes, commands, jobs, pages, DB tables/columns, connections, "
        "config/env keys, each edge with file:line evidence and a confidence (exact / resolved / heuristic). Stacks: "
        "Laravel, Django (django-ninja, DRF), NestJS, Next.js, Express/Fastify/Koa/Hono, Nuxt/Vue, Flutter/Dart, Rust, C, C++. "
        "Check the blast radius before editing: `reaches` lists everything that depends on a symbol/column/connection "
        "(grouped runtime / library / operator / UI / dev / gated), `impact` gives callers up to entry points, `siblings` "
        "finds parallel code that usually needs the same change, `writers` shows who writes a table, `routes` lists routes "
        "with their middleware / guards / auth (filter to routes reaching a write, a table or any target, and to routes "
        "missing a given middleware or any auth), `node` / `search` look things up (search also matches middleware and "
        "guard names). Specs: table.column | table:<t> | connection:<name|glob*> | env:<KEY> | config:<a.b> | "
        "Class::method | Class | pkg.module.func (Python) | Class.method (Dart/TS). "
        "On a combined graph (backend + frontend) also: page:/route/path | app/pages/x.vue | useComposable.fn | "
        "route:<METHOD> <uri>; `downstream` follows a page/component forward to backend routes and tables, `path` gives "
        "one evidence chain (and flags keys a call site passes that the request never sends), `api_calls` lists frontend "
        "HTTP calls with their matched backend routes. "
        "Rust / C / C++: specs are paths like crate::module::Type::method, <Type as Trait>::method, ns::Class::method, a "
        "bare function name, a file (src/x.rs, src/x.c), env:<KEY>, unsafe:<crate>, feature:<pkg>/<feature>, "
        "cfg:<atom>, define:<MACRO>; entry kinds are main / ffi_export (RUNTIME), public_api (LIBRARY) and test / bench / "
        "example / build_script (DEV); trait/virtual dispatch hops are IMPLEMENTED_BY / OVERRIDDEN_BY. "
        "`resolutions` lists every place a concept (e.g. timezone) is resolved from request input / settings / columns / "
        "literal fallbacks, groups them into fallback chains, shows where chains diverge, which routes reach each, and "
        "whether the frontend sends the key (plus client-side fallbacks and keys a helper drops before the request). "
        "Realtime: `channels` lists broadcast channels (Laravel Broadcast::channel) with who can join (auth route + "
        "middleware, the callback and the checks it calls), which events publish on each (broadcastOn, dispatch sites, "
        "entry points) and, on a combined graph, which client code / pages subscribe (Echo / pusher-js) and listen for "
        "which events; spec channel:<pattern>. "
        "Tests: test code (tests/, *.spec.ts / *.test.ts, e2e specs) is indexed as `test` nodes kept out of every other "
        "query (TEST_* edges never propagate); `tests_covering` lists the tests that exercise a symbol / route / table, "
        "direct (the test calls or requests it) and transitive (through application code). "
        "When a query finds nothing, the reply says why and which query to run instead. "
        "COVERAGE: `coverage` says which languages / files the index covers (exact, heuristic only, skipped because an "
        "indexer is missing, or unsupported, e.g. Go/Java/Kotlin/Swift/Ruby/C# files). For anything not covered or only "
        "heuristic, fall back to your normal search and file reading: an empty cg answer there is not proof of absence; "
        "empty and unknown-symbol replies end with a coverage note. "
        "PLANS: an agreed change scope lives in plans/<name>.yaml (add_nodes / modify / add_edges / forbid / require, "
        "issue links) and overlays the graph without changing it. Workflow: write the plan -> `plan_check` (compact "
        "summary with counts and the top items; details=true for every item with file:line, forbidden paths and open "
        "findings) -> refine the plan -> `plan_baseline` -> implement -> `index` -> `plan_check(verify=true)` (planned "
        "nodes/edges exist, forbidden paths gone or guarded, modified targets changed)."),
)


def _rel_text(txt: str) -> str:
    """Make every absolute path in a reply repo-relative: first the working directory / this checkout, then the indexed
    repo roots (whose files the combined graph already prefixes with the repo name) and the DB directory."""
    if not isinstance(txt, str):
        return txt
    first = {str(Path.cwd()), str(ROOT)}
    other = set()
    for k in ("root", "plans"):
        if STATE.get(k):
            other.add(str(Path(STATE[k]).resolve().parent))
    other.add(str(Path(STATE["db"]).resolve().parent))
    try:
        m = GraphStore(STATE["db"]).meta()
        for r in [m.get("root")] + list((m.get("sources") or {}).values()):
            if r:
                other.add(str(Path(r).resolve().parent))
    except Exception:
        pass
    for group in (first, other):
        for b in sorted((b for b in group if b and b != os.sep), key=len, reverse=True):
            txt = txt.replace(b + os.sep, "")
    home = str(Path.home())
    return txt.replace(home + os.sep, "~/") if home and home != os.sep else txt


def _display(p) -> str | None:
    """A path as shown to the agent: relative to the working directory or this checkout, else to the indexed repo."""
    if not p:
        return p
    return _rel_text(str(Path(p).resolve()))


EMPTY_MARKERS = ("no method matches", "no symbol matches", "not found:", "no node matches", "no matches for",
                 "nothing depends", "no writers recorded", "no table ", "no path", "no forward path",
                 "has no recorded callers", "no siblings found", "no routes, tables", "no indexed test reaches",
                 "nothing matched the spec", "no channel matches", "no broadcast channels")


def _coverage_note() -> str:
    from .coverage import for_graph, note
    try:
        return note(for_graph(_st()))
    except Exception:  # noqa: BLE001
        return ""


def tool(fn):
    """Register an MCP tool whose text reply goes through _rel_text; empty / unknown-symbol replies get a coverage note."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        from .plans import PlanError
        try:
            txt = fn(*args, **kwargs)
        except PlanError as e:  # missing plan, invalid YAML: a readable reply, not a bare tool error
            txt = f"plan error: {e}"
        if isinstance(txt, str) and fn.__name__ != "coverage" and not txt.startswith("plan error:") and any(m in txt[:600] for m in EMPTY_MARKERS):
            cn = _coverage_note()
            if cn:
                txt = txt.rstrip() + "\n" + cn
        return _rel_text(txt)
    return server.tool()(wrapped)


def _st() -> GraphStore:
    return GraphStore(STATE["db"])


CODE_PREFIXES = ("method:", "class:", "function:", "interface:", "trait:", "enum:", "struct:", "union:", "typedef:",
                 "type_alias:", "macro:", "global:", "ffi:", "field:", "const:", "static:")


def short(x: str | None) -> str:
    """Drop the node-kind prefix of code ids and the root 'App\\' namespace; other ids (column:, connection:...) stay."""
    if not x:
        return "?"
    if x.startswith(CODE_PREFIXES + ("composable:", "store:", "module:")) and "#" in x:
        f, _, q = x.split(":", 1)[1].partition("#")
        return f"{q} ({os.path.basename(f)})"
    if x.startswith(CODE_PREFIXES):
        x = x.split(":", 1)[1]
    return x[4:] if x.startswith("App\\") else x


def at(s: str) -> str:
    """'app/Services/X.php:12' -> 'X.php:12' (directory is implied by the module/symbol)."""
    if not s:
        return "?"
    f, _, ln = s.rpartition(":")
    return f"{os.path.basename(f)}:{ln}"


def fmt_path(path: list[dict], limit=8) -> str:
    if not path:
        return "(target)"
    hops = []
    for p in path[:limit]:
        g = f"!GATED({at(p.get('guard') or '')})" if p.get("gated") else ""
        c = "" if p["confidence"] == "exact" else f"~{p['confidence'][0]}"
        hops.append(f"{p['kind']}@{at(p['at'])}{c}{g}")
    more = f" …+{len(path) - limit}" if len(path) > limit else ""
    return " → ".join(hops) + more + f" → {short(path[-1]['to'])}"


def ek_str(ek: dict) -> str:
    abbrev = {"http_route": "route", "websocket": "ws", "artisan_command": "cmd", "management_command": "cmd", "scheduled": "sched",
              "queue_job": "job", "listener": "listener", "admin_panel": "admin", "observer": "observer", "ui_page": "page",
              "ui_global": "ui-shell", "public_api": "api", "ffi_export": "ffi", "build_script": "build",
              "message_handler": "msg", "cli_command": "cli", "channel_auth": "channel"}
    return ",".join(f"{abbrev.get(k, k)}×{v}" for k, v in sorted(ek.items())) or "-"


def _ekind(e: dict) -> str:
    return e["kind"] if e["kind"] in Q.ENTRY_NODE_KINDS else (e.get("entry_kind") or e["kind"])


def _ename(e: dict) -> str:
    """Entry display name: native code entries (main, exported fns, tests) use the qualified name + file:line."""
    if e["kind"] in Q.CODE_KINDS and Q.NATIVE_FILE_RE.search(e.get("file") or ""):
        return f"{short(e.get('fqn') or e['id'])} @{at((e.get('file') or '?') + ':' + str(e.get('line')))}"
    return e["name"]


@tool
def reaches(targets: list[str], min_confidence: str = "heuristic", group_by: str = "module",
            include_gated: bool = True, max_per_group: int = 25, paths: bool = True) -> str:
    """Reverse transitive dependents of one or more targets (union), e.g.
    ["orders.customer_id", "connection:warehouse", "connection:tenant_*"].

    Groups: RUNTIME (live; reached from http routes/schedules/jobs/listeners, or main()/exported FFI symbols in
    Rust/C/C++), LIBRARY (only via a library's public API), DEV (only tests/benches/examples/build scripts), OPERATOR (artisan commands /
    admin panels only: one-off import & provisioning), GATED (dead under the indexed gate scenario, e.g.
    code that only runs while a feature flag is off), NO-ENTRY. Inside each group items are grouped by
    `group_by` = module | entry_kind | class. Each line: symbol, entry kinds, depth, shortest evidence path
    (hops KIND@file:line; ~r = resolved, ~h = heuristic confidence). At most `max_per_group` lines per top-level
    group (shallowest first). min_confidence: heuristic | resolved | exact."""
    st = _st()
    res = Q.reaches(st, targets, min_conf=min_confidence)
    items = [i for i in res["items"] if not i["is_target"]]
    code = [i for i in items if i["kind"] in Q.CODE_KINDS]
    entries = [i for i in items if i["kind"] in Q.ENTRY_NODE_KINDS or (i["kind"] in Q.CODE_KINDS and i.get("entry_kind"))]
    groups = defaultdict(list)
    for i in code:
        g = "GATED" if i.get("gate_status", "live") != "live" and i["class"] != "no_entry" else {
            "runtime": "RUNTIME", "library": "LIBRARY", "operator": "OPERATOR", "ui": "UI", "dev": "DEV",
            "other_entry": "OBSERVER", "no_entry": "NO-ENTRY"}[i["class"]]
        groups[g].append(i)
    tl = ", ".join(f"{s}→{len(t)}" for s, t in res["targets"].items())
    missing = [s for s, t in res["targets"].items() if not t]
    if missing and len(missing) == len(res["targets"]):
        return f"targets: {tl}\nno node matches {', '.join(map(repr, missing))}; try search() with part of the name (spec forms are listed in the server instructions)"
    if not items:
        return (f"targets: {tl}\nnothing depends on the target(s) over dependency edges (min_confidence={min_confidence}). "
                f"try: node() for its direct edges; downstream() for what it reaches; a lower min_confidence")
    live_e = sum(1 for e in entries if e.get("gate_status", "live") == "live")
    out = [f"targets: {tl} | gate={res.get('gate')} | conf>={min_confidence}",
           f"dependents: {len(code)} code ({', '.join(f'{k.lower()} {len(v)}' for k, v in groups.items())}); "
           f"entry points {len(entries)} ({live_e} live): " + ", ".join(f"{k}×{n}" for k, n in sorted(
               defaultdict(int, {k: sum(1 for e in entries if _ekind(e) == k) for k in {_ekind(e) for e in entries}}).items()))]
    for gname in ("RUNTIME", "LIBRARY", "OPERATOR", "UI", "DEV", "GATED", "OBSERVER", "NO-ENTRY"):
        if gname == "GATED" and not include_gated:
            continue
        g = groups.get(gname)
        if not g:
            continue
        out.append(f"\n## {gname} ({len(g)})")
        keep = {id(i) for i in sorted(g, key=lambda x: (x["depth"], x.get("fqn") or ""))[:max_per_group]}
        buckets = defaultdict(list)
        for i in g:
            if id(i) not in keep:
                continue
            if group_by == "entry_kind":
                k = ek_str(i.get("live_entry_kinds") if gname != "GATED" and i.get("live_entry_kinds") else i["entry_kinds"]).split(",")[0].split("×")[0]
            elif group_by == "class":
                k = short((i.get("fqn") or i["id"]).split("::")[0])
            else:
                k = i.get("module") or "?"
            buckets[k].append(i)
        for k in sorted(buckets):
            out.append(f"[{k}]")
            for i in sorted(buckets[k], key=lambda x: (x["depth"], x.get("fqn") or "")):
                line = f"  {short(i.get('fqn') or i['id'])}  {ek_str(i['entry_kinds'])} d{i['depth']}"
                if gname == "GATED":
                    ev = i.get("gate_evidence") or {}
                    line += f"  {i['gate_status']} guard {at(ev.get('guard') or '')} hop {ev.get('kind')}@{at(ev.get('at') or '')}"
                elif paths:
                    line += "  " + fmt_path(i["path"])
                out.append(line)
        if len(g) > max_per_group:
            out.append(f"  … +{len(g) - max_per_group} more in {gname} (raise max_per_group)")
    out.append(f"\n## ENTRY POINTS ({len(entries)})")
    byk = defaultdict(list)
    for e in entries:
        byk[_ekind(e)].append(e)
    for k in sorted(byk):
        names = sorted(f"{_ename(e)}{'' if e.get('gate_status', 'live') == 'live' else ' [gated]'}" for e in byk[k])
        out.append(f"{k} ({len(names)}): " + "; ".join(names[:40]) + (f"; …+{len(names) - 40}" if len(names) > 40 else ""))
    return "\n".join(out)


@tool
def impact(method: str, min_confidence: str = "heuristic", max_items: int = 60) -> str:
    """Reverse callers of a method (Class::method, short or FQN) up to entry points, with the shortest call path
    from each entry point."""
    st = _st()
    r = Q.impact(st, method, min_conf=min_confidence)
    if not r["targets"]:
        return f"no method matches {method!r}; try search() with part of the name"
    out = [f"targets: {', '.join(short(t) for t in r['targets'][:5])}", f"transitive callers: {len(r['callers'])}; entry points: {len(r['entry_points'])}"]
    if not r["callers"] and not r["entry_points"]:
        return "\n".join(out + [Q.explain_no_callers(st, method, r["targets"])])
    byk = defaultdict(list)
    for e in r["entry_points"]:
        byk[e["entry_kind"]].append(e)
    n = 0
    for k in sorted(byk):
        out.append(f"\n## {k} ({len(byk[k])})")
        for e in byk[k]:
            if n >= max_items:
                break
            n += 1
            out.append(f"  {_ename(e)}  {fmt_path(e['path'])}")
    mods = defaultdict(int)
    for c in r["callers"]:
        mods[c.get("module") or "?"] += 1
    out.append("\ncallers by module: " + ", ".join(f"{m}×{c}" for m, c in sorted(mods.items(), key=lambda x: -x[1])))
    out += _snapshot_client_lines([e["id"] for e in r["entry_points"] if e["id"].startswith("route:")])
    return "\n".join(out)


def _snapshot_client_lines(route_ids) -> list[str]:
    """External (not indexed) client call sites from the snapshot files next to the plans that hit these routes."""
    from . import plans as P
    try:
        hits = P.snapshot_clients(_st(), route_ids, STATE["plans"])
    except Exception:  # noqa: BLE001  (a broken snapshot file must not break impact)
        return []
    if not hits:
        return []
    out = [f"\nexternal clients (snapshot, not indexed): {len(hits)}"]
    for h in hits:
        ev = f"{h['repo']}@{h['commit']}:{h['file']}" + (f":{h['line']}" if h.get("line") else "")
        out.append(f"  {h['method']} {h['path']} -> {h['route'].split(':', 1)[1]}  @{ev}"
                   + (f"  sends {', '.join(h['sends'])}" if h["sends"] else "") + f"  [{h['snapshot']}]")
    return out


@tool
def siblings(symbol: str, limit: int = 15) -> str:
    """Code parallel to a symbol that often needs the same change: classes sharing its parent/interface/trait,
    the same method in sibling classes, other code touching the same tables/columns/config/connections, and
    co-callers (methods calling the same helpers; Jaccard on callees)."""
    st = _st()
    r = Q.siblings(st, symbol, limit=limit)
    if not r["targets"]:
        return f"no symbol matches {symbol!r}; try search() with part of the name"
    out = [f"target: {short(r['targets'][0])}"]
    if not any(r[k] for k in ("hierarchy", "same_method_in_siblings", "shared_resources", "co_callers")):
        return "\n".join(out + [Q.explain_siblings(st, symbol, r)])
    if r["hierarchy"]:
        out.append("hierarchy: " + "; ".join(f"{h['kind']} {short(h['parent'])}: {short(h['sibling'])}" for h in r["hierarchy"][:limit]))
    if r["same_method_in_siblings"]:
        out.append("same method in siblings: " + "; ".join(f"{short(m['id'])} ({at(m['file'] + ':' + str(m['line']))})" for m in r["same_method_in_siblings"][:limit]))
    if r["shared_resources"]:
        out.append("shared resources:")
        for s in r["shared_resources"][:limit]:
            out.append(f"  {short(s['node'])}: {', '.join(short(x) for x in s['shared'][:6])}{' …' if len(s['shared']) > 6 else ''}")
    if r["co_callers"]:
        out.append("co-callers:")
        for s in r["co_callers"][:limit]:
            out.append(f"  {short(s['node'])} J={s['jaccard']}: {', '.join(short(x) for x in s['shared_callees'][:5])}")
    return "\n".join(out)


@tool
def resolutions(concept: str, within: str | None = None, client: bool = True, detail: bool = False,
                max_chars: int = 40000) -> str:
    """Every place a concept (e.g. 'timezone', 'locale') is resolved, deterministically: assignment/return sites
    whose value is a `??`/`?:` chain or an ordered sequence of early returns, expanded into a fallback chain of
    request input keys (FormRequest rules / $request->input / $filters['x'] through helper calls), settings
    (getSetting(key, default)), model columns, config/env and literal defaults. Sites are grouped into lettered
    chains by signature; the divergence section shows pairwise where chains first differ; each chain lists the
    routes that reach it. On a combined graph, also the frontend side: whether each matched client call sends the
    key (builder keys + call-site argument keys) and client literal fallbacks (`x ?? 'UTC'`).
    `within` filters owning functions by substring (e.g. 'Report'); `detail=True` lists every client call site and
    the keys it passes (default: one line per client request with its verdict)."""
    from .concepts import resolutions as R, render_resolutions
    out = render_resolutions(R(_st(), concept, within=within, client=client), compact=not detail)
    return out if len(out) <= max_chars else out[:max_chars] + f"\n… truncated ({len(out)} chars; narrow with `within`)"


@tool
def writers(table: str, limit: int = 60) -> str:
    """Who writes a DB table (WRITES_TABLE / WRITES_COLUMN edges), grouped by module, with the columns written,
    evidence lines and the entry-point kinds that reach each writer."""
    st = _st()
    rows = Q.writers(st, table)
    if not rows:
        return Q.explain_no_writers(st, table)
    by = defaultdict(lambda: {"cols": set(), "lines": set(), "ek": {}, "module": None, "conf": set()})
    for r in rows:
        b = by[r["src"]]
        if r["kind"] == "WRITES_COLUMN":
            b["cols"].add(r["dst"].split(".", 1)[1])
        b["lines"].add(f"{os.path.basename(r['file'] or '?')}:{r['line']}")
        b["ek"] = r["entry_kinds"]
        b["module"] = r["module"]
        b["conf"].add(r["confidence"])
    out = [f"table {table}: {len(rows)} write edges from {len(by)} writers"]
    mods = defaultdict(list)
    for src, b in by.items():
        mods[b["module"] or "?"].append((src, b))
    n = 0
    for m in sorted(mods):
        out.append(f"[{m}]")
        for src, b in sorted(mods[m]):
            if n >= limit:
                break
            n += 1
            cols = ",".join(sorted(b["cols"])[:10]) or "(row)"
            out.append(f"  {short(src)}  {ek_str(b['ek'])}  cols: {cols}  @ {', '.join(sorted(b['lines'])[:4])}  conf={'/'.join(sorted(b['conf']))}")
    return "\n".join(out)


@tool
def channels(pattern: str | None = None, source: bool = True) -> str:
    """Broadcast channels (Laravel Broadcast::channel, events' broadcastOn, Echo / pusher-js subscriptions on a combined
    graph). Without a pattern: one line per channel with its auth callback / checks, publishers and subscribers. With
    a pattern (`orders.{id}`, a concrete name like `orders.42`, or a glob `orders.*`): WHO CAN JOIN (broadcasting auth
    route + middleware, the callback with its source and every check it calls), PUBLISHED BY (events, the evaluated
    channel name, dispatch sites and the entry points that reach them) and LISTENED TO BY (client code, pages, events
    listened for). Flags private channels without a callback and subscriptions that match no backend channel."""
    from .realtime import channels as _ch, render_channels
    return render_channels(_ch(_st(), pattern, with_source=source))


@tool
def tests_covering(target: str, min_confidence: str = "heuristic", paths: bool = True) -> str:
    """Tests that exercise a symbol, route or table. DIRECT: the test code itself calls / instantiates it or sends an
    HTTP request to the route ($this->getJson('/x'), Playwright request.get). TRANSITIVE: through application code
    (test -> route -> controller -> service -> target). target: Class::method | Class | route:VERB /uri | `VERB /path`
    or /path (matched against route URIs) | table.column | any node id. Tests are PHPUnit / Pest (tests/), Vitest / Jest
    / Playwright / Cypress spec files; they never count as callers in the other queries."""
    return Q.render_tests_covering(Q.tests_covering(_st(), target, min_conf=min_confidence), show_paths=paths)


@tool
def node(id_or_symbol: str) -> str:
    """Details of one node: kind, FQN, file:line span, module, entry kinds, docblock (PHPDoc), and edge counts
    by kind (in/out) with a few neighbours."""
    st = _st()
    n = st.node(id_or_symbol)
    if not n:
        ids = Q.resolve_targets(st, id_or_symbol)
        n = st.node(ids[0]) if ids else None
    if not n:
        return f"not found: {id_or_symbol!r}; try search()"
    out = [f"{n['id']}", f"kind={n['kind']} module={n['module']} at {n['file']}:{n['line']}-{n['end_line']}"]
    ek = {r["entry_kind"]: r["entry_count"] for r in st.q("SELECT * FROM node_entry WHERE node_id=?", (n["id"],))}
    if ek:
        out.append(f"reached by: {ek_str(ek)}")
    if n["attrs"]:
        out.append(f"attrs: {n['attrs'][:300]}")
    if n["doc"]:
        out.append("doc:\n" + n["doc"].strip()[:1200])
    for direction, col, other in (("out", "src", "dst"), ("in", "dst", "src")):
        rows = st.q(f"SELECT kind, {other} AS o, file, line, confidence, gate FROM edges WHERE {col}=? ORDER BY kind, line", (n["id"],))
        byk = defaultdict(list)
        for r in rows:
            byk[r["kind"]].append(r)
        if byk:
            out.append(f"{direction}: " + ", ".join(f"{k}×{len(v)}" for k, v in sorted(byk.items())))
            for k, v in sorted(byk.items()):
                if k == "CONTAINS":
                    continue
                s = "; ".join(f"{short(r['o'])}@{r['line']}{'!g' if r['gate'] else ''}" for r in v[:6])
                out.append(f"  {k}: {s}{' …' if len(v) > 6 else ''}")
    return "\n".join(out)


@tool
def search(name: str, kind: str | None = None, limit: int = 20) -> str:
    """Find nodes by name / FQN substring (case-insensitive), optionally filtered by kind
    (class, method, route, command, table, column, connection, config, env, job, admin...). Also matches route
    middleware, guards, auth and access checks (e.g. "auth" finds `auth:api`, `ApiKeyGuard`, `IsAuthenticated`) and
    lists the routes that carry them."""
    return Q.render_search(Q.search(_st(), name, kind=kind, limit=limit))


@tool
def routes(writes: str | None = None, reaches: list[str] | None = None, missing: str | None = None,
           unguarded: bool = False, auth_pattern: str | None = None, max_items: int = 40, paths: bool = True,
           min_confidence: str = "heuristic") -> str:
    """Routes with their middleware / guards / auth, in one call. Optional scope: writes="*" (routes that reach any
    DB write) or writes="<table>", reaches=[specs] (routes that reach any of these nodes: table, column, connection,
    method, env key...). Optional filters: missing="<name>" keeps routes with no guard whose name contains it (e.g.
    "auth:api", "ApiKeyGuard"), unguarded=true keeps routes with no auth-like guard (name-based; extend with
    auth_pattern, a regex). Each route: guards, what it reaches with one evidence chain, and the frontend callers on a
    combined graph. Guards come from Laravel middleware, Nest guards/interceptors, Express/Koa/Fastify/Hono
    middleware, Next.js middleware.ts / handler wrappers, django-ninja auth= and Django/DRF view access checks.
    min_confidence: keep the default (heuristic) for reviews: every edge still shows its own label, and a stricter
    level names the routes it hides."""
    from . import routes as R
    st = _st()
    res = R.routes_report(st, writes=writes, reaches=reaches, missing=missing, unguarded=unguarded,
                          auth_pattern=auth_pattern, min_conf=min_confidence)
    return R.render_routes(res, st, max_items=max_items, paths=paths, compact=True)


@tool
def coverage(path: str | None = None) -> str:
    """Which languages and files this index covers: exact, heuristic only (an exact-mode indexer such as rust-analyzer
    or scip-clang is missing), skipped (the language's indexer / toolchain is missing; with the install hint) or
    unsupported (no plugin, e.g. .go .java .kt .swift .rb .cs). path (optional): a file or directory; says whether cg
    has it in the graph and which language status applies. Not covered or heuristic means: use your normal search and
    file reading for that part."""
    from .coverage import for_graph, render, SUPPORTED, UNSUPPORTED, FALLBACK
    st = _st()
    covs = for_graph(st)
    out = [render(covs)]
    if path:
        p = path.strip().removeprefix("./")
        ext = os.path.splitext(p)[1].lower()
        hit = st.q("SELECT file FROM nodes WHERE file = ? OR file LIKE ? OR file LIKE ? LIMIT 1", (p, f"%/{p}", f"{p}/%"))
        lang = next((k for k, v in SUPPORTED.items() if ext in v), None) or UNSUPPORTED.get(ext)
        status = None
        for c in covs.values():
            for e in (c or {}).get("languages", []):
                if e["language"] == lang:
                    status = e["status"]
        if hit:
            out.append(f"{path}: in the graph ({hit[0]['file']})" + (f"; {lang} {status}" if lang and status else ""))
        else:
            why = (f"{lang} files are {status.replace('_', ' ')} in this index" if lang and status else
                   f"{lang} is not supported by cg" if lang else "no node of this graph comes from that path")
            out.append(f"{path}: NOT in the graph ({why}); {FALLBACK}.")
    return "\n".join(out)


@tool
def stats() -> str:
    """Index metadata and node/edge counts by kind."""
    st = _st()
    m = st.meta()
    nodes = st.q("SELECT kind, count(*) c FROM nodes GROUP BY kind ORDER BY c DESC")
    edges = st.q("SELECT kind, count(*) c, sum(gate IS NOT NULL) g FROM edges GROUP BY kind ORDER BY c DESC")
    s = m.get("stats", {})
    out = [f"project={m.get('project')} root={_display(m.get('root'))} indexed_at={m.get('indexed_at')} index_seconds={s.get('index_seconds')}",
           "nodes: " + ", ".join(f"{r['kind']}×{r['c']}" for r in nodes),
           "edges: " + ", ".join(f"{r['kind']}×{r['c']}" + (f"(gated {r['g']})" if r["g"] else "") for r in edges)]
    gp = st.q("SELECT scenario, method, value FROM gate_predicates")
    if gp:
        out.append("gate predicates: " + "; ".join(f"[{r['scenario']}] {short(r['method'])}={r['value']}" for r in gp))
    from .coverage import for_graph, summary_line
    for repo, cov in for_graph(st).items():
        out.append(summary_line(cov, repo or None))
    return "\n".join(out)


@tool
def downstream(target: str, max_per_kind: int = 25, paths: bool = True, min_confidence: str = "heuristic") -> str:
    """Forward dependencies of a node: what it ends up calling/reading. On a combined graph a frontend page goes
    page -> composables/components -> HTTP endpoints -> backend routes -> controllers/services -> tables.
    Lists backend routes, tables touched (directly or via columns), columns, connections, config/env keys, each
    with one shortest evidence path. target: page:/reports/:id | app/pages/x.vue | Class::method | ..."""
    st = _st()
    r = Q.downstream(st, target, min_conf=min_confidence)
    if not r["targets"]:
        return f"no node matches {target!r}; try search()"
    out = [f"targets: {', '.join(short(t) for t in r['targets'][:4])} | reached {r['reached']} nodes | gate={r.get('gate')}"]
    if not r["sinks"]:
        out.append(f"no routes, tables, columns, connections, config/env keys or jobs reachable from {target}: it calls nothing "
                   f"that touches data or crosses a boundary (or only through edges below min_confidence={min_confidence}). "
                   f"try: reaches(['{target}']) for what depends on it; node('{target}') for its direct edges.")
    tt = r.get("tables_touched") or []
    if tt:
        out.append(f"tables touched ({len(tt)}): " + ", ".join(f"{t['table']}{'' if t['live'] else '[gated-only]'}" for t in tt))
    for k in ("route", "table", "connection", "config", "env", "job", "column", "unsafe", "ffi", "feature", "cfg", "define"):
        items = r["sinks"].get(k) or []
        if not items:
            continue
        out.append(f"\n## {k} ({len(items)})")
        for i in items[:max_per_kind]:
            line = f"  {short(i['id'])} d{i['depth']}{'' if i['live'] else ' [gated-only]'}"
            if paths and k != "column":
                line += "  " + fmt_path(i["path"], limit=10)
            out.append(line)
        if len(items) > max_per_kind:
            out.append(f"  … +{len(items) - max_per_kind} more")
    return "\n".join(out)


@tool
def path(source: str, target: str, min_confidence: str = "heuristic") -> str:
    """Shortest forward evidence chain from source to target (e.g. page:/reports/:id ->
    SalesReportService::report), one hop per line with file:line and confidence."""
    st = _st()
    p = Q.path_between(st, source, target, min_conf=min_confidence)
    if not p:
        return Q.explain_no_path(st, source, target, min_confidence)
    out = [short(p[0]["from"])]
    for h in p:
        out.append(f"  -{h['kind']}[{h['confidence']} @ {h['at']}]-> {short(h['to'])}")
    out += [f"note: {n}" for n in Q.path_notes(st, p)]
    return "\n".join(out)


@tool
def api_calls(filter: str = "all", max_items: int = 60) -> str:
    """Frontend HTTP calls (method + path template) with call sites, request keys and the matched backend
    route + controller (combined graph). filter: all | unmatched | any substring (endpoint, route, caller, file) |
    a `*` glob (`GET /v1/*/orders*`, `*/staff/*`, `*useOrders*`) on the endpoint, its path, route, controller,
    caller or call-site file."""
    st = _st()
    rows = Q.api_calls(st, filter)
    gaps = defaultdict(list)
    for g in Q.forwarding_gaps(st):
        gaps[g["endpoint"]].append(g)
    if not rows:
        total = st.q("SELECT count(*) c FROM nodes WHERE kind='http'")[0]["c"]
        return (f"no client endpoints match {filter!r} ({total} in the graph). " + (
            "This graph has no frontend HTTP calls: link a frontend DB to a backend DB with `cg link` and point the server at "
            "the combined DB." if not total else "try filter='all', 'unmatched' or a shorter substring of the path."))
    out = [f"{len(rows)} client endpoints ({sum(1 for r in rows if r['routes'])} matched)"]
    for r in rows[:max_items]:
        m = "; ".join(f"{short(x['route'])} [{x['confidence']}] → {', '.join(short(c) for c in x['controller']) or '?'}" for x in r["routes"]) or "UNMATCHED"
        out.append(f"{r['endpoint'][5:]}  ⇒ {m}")
        for c in r["calls"][:3]:
            h = f" via {short(c['via_helper']['fn'])}" if c.get("via_helper") else ""
            out.append(f"   ← {short(c['caller'])} @ {at(c['at'])}{h}")
        for g in gaps.get(r["endpoint"], [])[:3]:
            out.append(f"   ! {short(g['caller'])} @ {at(g['call_at'])} passes {', '.join(g['dropped'])}: sent but not forwarded "
                       f"(request sends {', '.join(g['request_keys'])})")
    if len(rows) > max_items:
        out.append(f"… +{len(rows) - max_items} more")
    return "\n".join(out)


def _plan(name: str):
    from . import plans as P
    return P, P.load_plan(name, STATE["plans"])


@tool
def plan_list() -> str:
    """List planned-change files (plans/*.yaml): name, status, title, counts, schema errors."""
    from . import plans as P
    rows = P.list_plans(STATE["plans"])
    out = []
    for r in rows:
        if r.get("error"):
            out.append(f"{r['name']}: ERROR {r['error']}")
            continue
        c = r["counts"]
        out.append(f"{r['name']} [{r['status']}] {r['title']} | +{c['add_nodes']} nodes ~{c['modify']} modified +{c['add_edges']} edges "
                   f"{c['forbid']} forbidden {c['require']} required | issues {', '.join(r['issues']) or '-'} | schema errors {r['schema_errors']}")
    return "\n".join(out) or "no plans"


@tool
def plan_load(name: str) -> str:
    """Show one plan (name or path): planned nodes (+), modified targets with intent (~), planned edges, forbidden
    paths (x), requirements (!), covers/out_of_scope, precedents, linked issues, schema errors."""
    P, plan = _plan(name)
    return P.render_load(plan)


@tool
def plan_validate(name: str) -> str:
    """Schema check + does every referenced existing node resolve in the graph (unresolved / ambiguous specs with
    candidates); planned nodes that already exist are flagged."""
    P, plan = _plan(name)
    return P.render_validate(P.validate(_st(), plan))


@tool
def plan_check(name: str, verify: bool = False, details: bool = False, max_items: int = 5, review: bool = True,
               max_chars: int = 30000) -> str:
    """Deterministic check of a plan against the real graph. Default reply: a compact summary (counts per section and
    per check, the top `max_items` missing items, failed requirements, middleware gaps, forbidden paths, open findings,
    verify counts); details=true gives the full report (every item with file:line evidence and call chains, up to
    max_items per list).
    plan mode: 1 RESOLVE (references, route middleware requirements, middleware differences between entry routes of
    the same modified code), 2 MISSING FROM PLAN (writers/readers of changed tables, Filament/API resources, $fillable,
    form requests, entry points + callers of modified methods, frontend pages and external client snapshots calling
    affected routes, parallel/legacy mirrors, precedents), 3 CONFLICTS (forbidden paths still in the code with their
    call chain, open findings touching the same nodes, linked or not), plus entry call chains.
    verify=true (after implementation + re-index): also 4 VERIFY: planned nodes/edges exist now (with evidence),
    modified targets changed vs baseline, forbidden paths gone or guarded, requirements met."""
    P, plan = _plan(name)
    res = P.check(_st(), plan, verify=verify, baseline=P.load_baseline(plan) if verify else None)
    res["file"] = _display(res.get("file"))
    if not review:
        res["items"] = [i for i in res["items"] if i["severity"] != "review"]
    if not details:
        return P.render_check_summary(res, max_items=max_items)
    txt = P.render_check(res, max_items=max(max_items, 1))
    return txt if len(txt) <= max_chars else txt[:max_chars] + f"\n… truncated ({len(txt)} chars; lower max_items or review=false)"


@tool
def plan_baseline(name: str) -> str:
    """Fingerprint (sha1 of source lines) every modified target of a plan before implementing it, so
    plan_check(verify=true) can report changed/UNCHANGED. Writes plans/<name>.baseline.json (only file it writes)."""
    P, plan = _plan(name)
    b = P.make_baseline(_st(), plan)
    P.baseline_path(plan).write_text(json.dumps(b, indent=1) + "\n")
    return f"baseline: {len(b['targets'])} modified targets fingerprinted -> {_display(P.baseline_path(plan))}"


def _relink() -> dict:
    from .link import link
    m = _st().meta()
    src = m["sources"]
    be, fe = m["repos"]
    tmp = STATE["db"] + ".tmp"
    res = link(src[be], src[fe], tmp, be, fe)
    os.replace(tmp, STATE["db"])
    return res["stats"]


def _under(p: Path, base: Path) -> bool:
    try:
        p.relative_to(base)
        return True
    except ValueError:
        return False


def _pick_repos(meta: dict, root: str | None, repo: str | None) -> tuple[list[tuple[str, str]], str | None]:
    """Which repo slots of a combined graph to re-index, each with the root to index it from: (slots, error).
    repo picks one slot; root is matched against the recorded repo roots (equal, inside one, or containing some);
    neither re-indexes every repo from its recorded root. A root never lands in a slot it does not belong to."""
    repos = list(meta["repos"])
    roots = {}
    for r in repos:
        try:
            rr = GraphStore(meta["sources"][r]).meta().get("root")
        except Exception:
            rr = None
        roots[r] = Path(rr).resolve() if rr else None
    known = ", ".join(f"{r} ({_display(str(roots[r])) if roots[r] else 'no recorded root'})" for r in repos)
    if repo is not None and repo not in repos:
        return [], f"unknown repo '{repo}'; this combined graph has: {known}"
    if root is None:
        picked = [repo] if repo else repos
        missing = [r for r in picked if roots[r] is None]
        if missing:
            return [], f"no recorded root for {', '.join(missing)}; pass root (one of the repo directories)"
        return [(r, str(roots[r])) for r in picked], None
    want = Path(root).resolve()
    if repo is not None:
        rr = roots[repo]
        if rr is None or want == rr or _under(want, rr):
            return [(repo, str(rr or want))], None
        return [], (f"root {_display(str(want))} is not the recorded root of repo '{repo}' ({_display(str(rr))}); "
                    f"omit root to re-index {repo} from its recorded root")
    exact = [r for r in repos if roots[r] is not None and (want == roots[r] or _under(want, roots[r]))]
    if exact:  # the repo directory itself or a path inside it
        return [(r, str(roots[r])) for r in exact[:1]], None
    inside = [r for r in repos if roots[r] is not None and _under(roots[r], want)]
    if inside:  # a parent directory: every repo below it
        return [(r, str(roots[r])) for r in inside], None
    return [], (f"root {_display(str(want))} matches no repo of this combined graph; repos: {known}. "
                f"Pass repo=<name>, or a root inside one of those directories")


@tool
def index(root: str | None = None, gates: str | None = None, repo: str | None = None) -> str:
    """Re-index after editing (static analysis only: never boots the app or touches a database).
    On a combined graph (backend + frontend): with no arguments every repo is re-indexed from its recorded root and the
    cross-repo link is rebuilt; repo (a name used at link time) re-indexes just that repo; root is matched to the
    recorded repo roots (a repo directory, a path inside one, or a parent of several). A root that matches no repo, or
    a result with 0 nodes, is refused and the graph is left unchanged.
    On a single-repo graph root defaults to the indexed root; gates is a gate-scenario JSON path (defaults to the
    server's --gates)."""
    from .indexer import index_project
    try:
        meta = _st().meta()
    except sqlite3.OperationalError:
        meta = {}  # empty / not yet indexed DB
    if meta.get("repos"):
        slots, err = _pick_repos(meta, root, repo)
        if err:
            return "index refused: " + err
        done, tmps = [], []
        with STATE["lock"]:
            try:
                for r, r_root in slots:
                    src_db = meta["sources"][r]
                    tmp = src_db + ".tmp"
                    tmps.append(tmp)
                    g = gates or (STATE["gates"] if r == meta["repos"][0] else None)
                    st = index_project(r_root, tmp, r, gates=g)
                    if not st.get("nodes"):
                        return (f"index refused: re-indexing {r} from {_display(r_root)} produced 0 nodes; "
                                f"the graph is unchanged (is that the right directory?)")
                    done.append((r, r_root, src_db, tmp, st))
                for r, r_root, src_db, tmp, st in done:
                    os.replace(tmp, src_db)
                ls = _relink()
            finally:
                for tmp in tmps:
                    if os.path.exists(tmp):
                        os.remove(tmp)
        parts = "; ".join(f"re-indexed {r} ({_display(rr)}): {st['nodes']} nodes, {st['edges']} edges in "
                          f"{st['index_seconds']}s" for r, rr, _, _, st in done)
        return f"{parts}; relinked: {ls['call_sites_matched']}/{ls['call_sites']} call sites matched"
    root = root or STATE["root"] or meta.get("root")
    gates = gates or STATE["gates"]
    if not root:
        return "no project root known; pass root"
    with STATE["lock"]:
        tmp = STATE["db"] + ".tmp"
        st = index_project(root, tmp, Path(root).name, gates=gates)
        if not st.get("nodes"):
            os.remove(tmp)
            return f"index refused: {_display(root)} produced 0 nodes; the graph is unchanged (is that the right directory?)"
        os.replace(tmp, STATE["db"])
    return (f"indexed {_display(root)} -> {_display(STATE['db'])}: {st['nodes']} nodes, {st['edges']} edges in {st['index_seconds']}s; "
            f"gated edges {st.get('gated_edges')}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="codegraph-mcp")
    ap.add_argument("--db", default=STATE["db"])
    ap.add_argument("--root")
    ap.add_argument("--gates")
    ap.add_argument("--plans", help="plans directory (default: <repo>/plans)")
    a = ap.parse_args(argv)
    STATE["plans"] = str(Path(a.plans).resolve()) if a.plans else None
    STATE["db"] = str(Path(a.db).resolve())
    STATE["root"] = a.root
    STATE["gates"] = str(Path(a.gates).resolve()) if a.gates else None
    server.run("stdio")


if __name__ == "__main__":
    main()

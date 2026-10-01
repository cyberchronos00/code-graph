"""code-graph MCP server (stdio). Exposes the SQLite graph to agents with compact, token-efficient output.

Run:  .venv/bin/python -m codegraph.mcp_server --db out/graph.db [--root path/to/project --gates path/to/gates.json] [--plans plans/]

Tools: reaches, impact, siblings, writers, node, search, stats, index, downstream, path, api_calls, resolutions,
plan_list, plan_load, plan_validate, plan_check, plan_baseline (planned-change layer, plans/<name>.yaml).
Point --db at a combined graph (codegraph.cli link ...) to query across repos (frontend pages -> backend routes -> tables).
All results are plain text: grouped by module / entry-point kind, one line per item, each with the
shortest evidence path (KIND@file:line hops). Edges are deterministic (parsers + static rules); nothing here
calls an LLM.
"""
from __future__ import annotations

import argparse
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
        "Deterministic code graph of a project (classes, methods, routes, commands, jobs, DB tables/columns, "
        "connections, config/env keys). Use `reaches` to see everything that depends on a symbol/column/connection "
        "(grouped runtime vs operator vs gated, with evidence file:line), `impact` for callers up to entry points, "
        "`siblings` for parallel code that may need the same change, `writers` for who writes a table, `node`/`search` "
        "to look things up. Specs: table.column | table:<t> | connection:<name|glob*> | env:<KEY> | config:<a.b> | Class::method | Class. "
        "On a combined graph (backend + frontend) also: page:/route/path | app/pages/x.vue | useComposable.fn | route:<METHOD> <uri>; "
        "`downstream` follows a page/component forward to backend routes and tables, `path` gives one evidence chain, "
        "`api_calls` lists frontend HTTP calls with their matched backend routes. "
        "`resolutions` lists every place a concept (e.g. timezone) is resolved from request input / settings / columns / "
        "literal fallbacks, groups them into distinct fallback chains, shows where chains diverge, which routes reach "
        "each, and whether the frontend actually sends the key (plus client-side literal fallbacks). "
        "PLANS: an agreed change scope lives in plans/<name>.yaml (add_nodes / modify / add_edges / forbid / require, "
        "issue links); it overlays the graph without changing it. Workflow: write the plan -> `plan_check` (lists what "
        "the plan misses with file:line, forbidden paths still present, open findings on the same nodes) -> fix the plan "
        "-> `plan_baseline` -> implement -> `index` -> `plan_check(verify=true)` (planned nodes/edges must now exist, "
        "forbidden paths gone or guarded, modified targets changed)."),
)


def _st() -> GraphStore:
    return GraphStore(STATE["db"])


CODE_PREFIXES = ("method:", "class:", "function:", "interface:", "trait:", "enum:")


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
    abbrev = {"http_route": "route", "artisan_command": "cmd", "scheduled": "sched", "queue_job": "job", "listener": "listener",
              "admin_panel": "admin", "observer": "observer", "ui_page": "page", "ui_global": "ui-shell"}
    return ",".join(f"{abbrev.get(k, k)}×{v}" for k, v in sorted(ek.items())) or "-"


@server.tool()
def reaches(targets: list[str], min_confidence: str = "heuristic", group_by: str = "module",
            include_gated: bool = True, max_per_group: int = 25, paths: bool = True) -> str:
    """Reverse transitive dependents of one or more targets (union), e.g.
    ["orders.customer_id", "connection:warehouse", "connection:tenant_*"].

    Groups: RUNTIME (live; reached from http routes/schedules/jobs/listeners), OPERATOR (artisan commands /
    admin panels only: one-off import & provisioning), GATED (dead under the indexed gate scenario, e.g.
    code that only runs while a feature flag is off), NO-ENTRY. Inside each group items are grouped by
    `group_by` = module | entry_kind | class. Each line: symbol, entry kinds, depth, shortest evidence path
    (hops KIND@file:line; ~r = resolved, ~h = heuristic confidence). At most `max_per_group` lines per top-level
    group (shallowest first). min_confidence: heuristic | resolved | exact."""
    st = _st()
    res = Q.reaches(st, targets, min_conf=min_confidence)
    items = [i for i in res["items"] if not i["is_target"]]
    code = [i for i in items if i["kind"] in Q.CODE_KINDS]
    entries = [i for i in items if i["kind"] in Q.ENTRY_NODE_KINDS]
    groups = defaultdict(list)
    for i in code:
        g = "GATED" if i.get("gate_status", "live") != "live" and i["class"] != "no_entry" else {
            "runtime": "RUNTIME", "operator": "OPERATOR", "ui": "UI", "other_entry": "OBSERVER", "no_entry": "NO-ENTRY"}[i["class"]]
        groups[g].append(i)
    tl = ", ".join(f"{s}→{len(t)}" for s, t in res["targets"].items())
    live_e = sum(1 for e in entries if e.get("gate_status", "live") == "live")
    out = [f"targets: {tl} | gate={res.get('gate')} | conf>={min_confidence}",
           f"dependents: {len(code)} code ({', '.join(f'{k.lower()} {len(v)}' for k, v in groups.items())}); "
           f"entry points {len(entries)} ({live_e} live): " + ", ".join(f"{k}×{n}" for k, n in sorted(
               defaultdict(int, {k: sum(1 for e in entries if e['kind'] == k) for k in {e['kind'] for e in entries}}).items()))]
    for gname in ("RUNTIME", "OPERATOR", "UI", "GATED", "OBSERVER", "NO-ENTRY"):
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
        byk[e["kind"]].append(e)
    for k in sorted(byk):
        names = sorted(f"{e['name']}{'' if e.get('gate_status', 'live') == 'live' else ' [gated]'}" for e in byk[k])
        out.append(f"{k} ({len(names)}): " + "; ".join(names[:40]) + (f"; …+{len(names) - 40}" if len(names) > 40 else ""))
    return "\n".join(out)


@server.tool()
def impact(method: str, min_confidence: str = "heuristic", max_items: int = 60) -> str:
    """Reverse callers of a method (Class::method, short or FQN) up to entry points, with the shortest call path
    from each entry point."""
    st = _st()
    r = Q.impact(st, method, min_conf=min_confidence)
    if not r["targets"]:
        return f"no method matches {method!r}; try search()"
    out = [f"targets: {', '.join(short(t) for t in r['targets'][:5])}", f"transitive callers: {len(r['callers'])}; entry points: {len(r['entry_points'])}"]
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
            out.append(f"  {e['name']}  {fmt_path(e['path'])}")
    mods = defaultdict(int)
    for c in r["callers"]:
        mods[c.get("module") or "?"] += 1
    out.append("\ncallers by module: " + ", ".join(f"{m}×{c}" for m, c in sorted(mods.items(), key=lambda x: -x[1])))
    return "\n".join(out)


@server.tool()
def siblings(symbol: str, limit: int = 15) -> str:
    """Code parallel to a symbol that often needs the same change: classes sharing its parent/interface/trait,
    the same method in sibling classes, other code touching the same tables/columns/config/connections, and
    co-callers (methods calling the same helpers; Jaccard on callees)."""
    st = _st()
    r = Q.siblings(st, symbol, limit=limit)
    if not r["targets"]:
        return f"no symbol matches {symbol!r}; try search()"
    out = [f"target: {short(r['targets'][0])}"]
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


@server.tool()
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


@server.tool()
def writers(table: str, limit: int = 60) -> str:
    """Who writes a DB table (WRITES_TABLE / WRITES_COLUMN edges), grouped by module, with the columns written,
    evidence lines and the entry-point kinds that reach each writer."""
    st = _st()
    rows = Q.writers(st, table)
    if not rows:
        return f"no writers recorded for table {table!r}"
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


@server.tool()
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


@server.tool()
def search(name: str, kind: str | None = None, limit: int = 20) -> str:
    """Find nodes by name / FQN substring (case-insensitive), optionally filtered by kind
    (class, method, route, command, table, column, connection, config, env, job, admin...)."""
    st = _st()
    q = "SELECT id, kind, file, line FROM nodes WHERE (name LIKE ? OR fqn LIKE ? OR id LIKE ?)"
    p = [f"%{name}%"] * 3
    if kind:
        q += " AND kind=?"
        p.append(kind)
    q += " ORDER BY length(id) LIMIT ?"
    p.append(limit)
    rows = st.q(q, p)
    if not rows:
        return "no matches"
    return "\n".join(f"{r['kind']:10} {r['id']}  {at((r['file'] or '?') + ':' + str(r['line']))}" for r in rows)


@server.tool()
def stats() -> str:
    """Index metadata and node/edge counts by kind."""
    st = _st()
    m = st.meta()
    nodes = st.q("SELECT kind, count(*) c FROM nodes GROUP BY kind ORDER BY c DESC")
    edges = st.q("SELECT kind, count(*) c, sum(gate IS NOT NULL) g FROM edges GROUP BY kind ORDER BY c DESC")
    s = m.get("stats", {})
    out = [f"project={m.get('project')} root={m.get('root')} indexed_at={m.get('indexed_at')} index_seconds={s.get('index_seconds')}",
           "nodes: " + ", ".join(f"{r['kind']}×{r['c']}" for r in nodes),
           "edges: " + ", ".join(f"{r['kind']}×{r['c']}" + (f"(gated {r['g']})" if r["g"] else "") for r in edges)]
    gp = st.q("SELECT scenario, method, value FROM gate_predicates")
    if gp:
        out.append("gate predicates: " + "; ".join(f"[{r['scenario']}] {short(r['method'])}={r['value']}" for r in gp))
    return "\n".join(out)


@server.tool()
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
    tt = r.get("tables_touched") or []
    if tt:
        out.append(f"tables touched ({len(tt)}): " + ", ".join(f"{t['table']}{'' if t['live'] else '[gated-only]'}" for t in tt))
    for k in ("route", "table", "connection", "config", "env", "job", "column"):
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


@server.tool()
def path(source: str, target: str, min_confidence: str = "heuristic") -> str:
    """Shortest forward evidence chain from source to target (e.g. page:/reports/:id ->
    SalesReportService::report), one hop per line with file:line and confidence."""
    st = _st()
    p = Q.path_between(st, source, target, min_conf=min_confidence)
    if not p:
        return "no path"
    out = [short(p[0]["from"])]
    for h in p:
        out.append(f"  -{h['kind']}[{h['confidence']} @ {h['at']}]-> {short(h['to'])}")
    return "\n".join(out)


@server.tool()
def api_calls(filter: str = "all", max_items: int = 60) -> str:
    """Frontend HTTP calls (method + path template) with call sites, request keys and the matched backend
    route + controller (combined graph). filter: all | unmatched | any substring (endpoint, route, caller, file)."""
    st = _st()
    rows = Q.api_calls(st, filter)
    out = [f"{len(rows)} client endpoints ({sum(1 for r in rows if r['routes'])} matched)"]
    for r in rows[:max_items]:
        m = "; ".join(f"{short(x['route'])} [{x['confidence']}] → {', '.join(short(c) for c in x['controller']) or '?'}" for x in r["routes"]) or "UNMATCHED"
        out.append(f"{r['endpoint'][5:]}  ⇒ {m}")
        for c in r["calls"][:3]:
            h = f" via {short(c['via_helper']['fn'])}" if c.get("via_helper") else ""
            out.append(f"   ← {short(c['caller'])} @ {at(c['at'])}{h}")
    if len(rows) > max_items:
        out.append(f"… +{len(rows) - max_items} more")
    return "\n".join(out)


def _plan(name: str):
    from . import plans as P
    return P, P.load_plan(name, STATE["plans"])


@server.tool()
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


@server.tool()
def plan_load(name: str) -> str:
    """Show one plan (name or path): planned nodes (+), modified targets with intent (~), planned edges, forbidden
    paths (x), requirements (!), covers/out_of_scope, precedents, linked issues, schema errors."""
    P, plan = _plan(name)
    return P.render_load(plan)


@server.tool()
def plan_validate(name: str) -> str:
    """Schema check + does every referenced existing node resolve in the graph (unresolved / ambiguous specs with
    candidates); planned nodes that already exist are flagged."""
    P, plan = _plan(name)
    return P.render_validate(P.validate(_st(), plan))


@server.tool()
def plan_check(name: str, verify: bool = False, max_items: int = 40, review: bool = True, max_chars: int = 30000) -> str:
    """Deterministic check of a plan against the real graph.
    plan mode: 1 RESOLVE (references, route middleware requirements, middleware differences between entry routes of
    the same modified code), 2 MISSING FROM PLAN (writers/readers of changed tables, Filament/API resources, $fillable,
    form requests, entry points + callers of modified methods, frontend pages and external client snapshots calling
    affected routes, parallel/legacy mirrors, precedents) each with file:line, 3 CONFLICTS (forbidden paths still in
    the code with their call chain, open findings touching the same nodes, linked or not), plus entry call chains.
    verify=true (after implementation + re-index): also 4 VERIFY: planned nodes/edges exist now (with evidence),
    modified targets changed vs baseline, forbidden paths gone or guarded, requirements met."""
    P, plan = _plan(name)
    res = P.check(_st(), plan, verify=verify, baseline=P.load_baseline(plan) if verify else None)
    if not review:
        res["items"] = [i for i in res["items"] if i["severity"] != "review"]
    txt = P.render_check(res, max_items=max_items)
    return txt if len(txt) <= max_chars else txt[:max_chars] + f"\n… truncated ({len(txt)} chars; lower max_items or review=false)"


@server.tool()
def plan_baseline(name: str) -> str:
    """Fingerprint (sha1 of source lines) every modified target of a plan before implementing it, so
    plan_check(verify=true) can report changed/UNCHANGED. Writes plans/<name>.baseline.json (only file it writes)."""
    P, plan = _plan(name)
    b = P.make_baseline(_st(), plan)
    P.baseline_path(plan).write_text(json.dumps(b, indent=1) + "\n")
    return f"baseline: {len(b['targets'])} modified targets fingerprinted -> {P.baseline_path(plan)}"


def _relink() -> dict:
    from .link import link
    m = _st().meta()
    src = m["sources"]
    be, fe = m["repos"]
    tmp = STATE["db"] + ".tmp"
    res = link(src[be], src[fe], tmp, be, fe)
    os.replace(tmp, STATE["db"])
    return res["stats"]


@server.tool()
def index(root: str | None = None, gates: str | None = None, repo: str | None = None) -> str:
    """Re-index the project into this server's DB (static analysis only: never boots the app or touches a database).
    root defaults to the indexed root; gates is a gate-scenario JSON path (defaults to the server's --gates).
    On a combined graph pass repo (the frontend or backend name used at link time): that repo's own DB is re-indexed from its
    recorded root, then the cross-repo link is rebuilt."""
    from .indexer import index_project
    try:
        meta = _st().meta()
    except sqlite3.OperationalError:
        meta = {}  # empty / not yet indexed DB
    if meta.get("repos"):
        repo = repo or meta["repos"][-1]
        src_db = meta["sources"][repo]
        sub = GraphStore(src_db).meta()
        r_root = root or sub.get("root")
        with STATE["lock"]:
            tmp = src_db + ".tmp"
            g = gates or (STATE["gates"] if repo == meta["repos"][0] else None)
            st = index_project(r_root, tmp, repo, gates=g)
            os.replace(tmp, src_db)
            ls = _relink()
        return (f"re-indexed {repo} ({r_root}): {st['nodes']} nodes, {st['edges']} edges in {st['index_seconds']}s; relinked: "
                f"{ls['call_sites_matched']}/{ls['call_sites']} call sites matched")
    root = root or STATE["root"] or meta.get("root")
    gates = gates or STATE["gates"]
    if not root:
        return "no project root known; pass root"
    with STATE["lock"]:
        tmp = STATE["db"] + ".tmp"
        st = index_project(root, tmp, Path(root).name, gates=gates)
        os.replace(tmp, STATE["db"])
    return (f"indexed {root} -> {STATE['db']}: {st['nodes']} nodes, {st['edges']} edges in {st['index_seconds']}s; "
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

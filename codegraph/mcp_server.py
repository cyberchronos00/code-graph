"""code-graph MCP server (stdio). Exposes the SQLite graph to agents with compact, token-efficient output.

Run:  .venv/bin/python -m codegraph.mcp_server --db out/graph.db [--root path/to/project --gates path/to/gates.json] [--plans plans/]

Tools: reaches, impact, callers, siblings, writers, routes, node, search, stats, starters, index, downstream, path,
api_calls, resolutions, channels, bridges, protocol_links, llm_tools, external_systems, tests_covering, coverage, platforms, platform_divergence, plan_list, plan_load, plan_validate, plan_check, plan_baseline (planned-change layer,
plans/<name>.yaml).
Point --db at a combined graph (codegraph.cli link ...) to query across repos (frontend pages -> backend routes -> tables).
All results are plain text: grouped by module / entry-point kind, one line per item, each with the
shortest evidence path (KIND@file:line hops). Every edge comes from parsers and static rules, so the same graph always
gives the same answer. Paths in replies are repo-relative.
"""
from __future__ import annotations

import argparse
import contextvars
import functools
import inspect
import sqlite3
import json
import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent
from typing_extensions import NotRequired, TypedDict

from .core.store import GraphStore
from . import query as Q

ROOT = Path(__file__).resolve().parents[1]
STATE = {"db": str(ROOT / "out" / "graph.db"), "root": None, "gates": None, "plans": None, "lock": threading.Lock()}

server = MCPServer(
    name="code-graph",
    instructions=(
        "Code graph of a project: classes, functions, routes, commands, jobs, pages, DB tables/columns, connections, "
        "config/env keys, each edge with file:line evidence and a confidence (exact / resolved / heuristic). Stacks: "
        "Laravel, Django (django-ninja, DRF), FastAPI/Starlette, Flask, NestJS, Next.js, Express/Fastify/Koa/Hono, Nuxt/Vue, Flutter/Dart, Rust, C, C++. "
        "Check the blast radius before editing: `reaches` lists everything that depends on a symbol/column/connection "
        "(grouped runtime / library / operator / UI / dev / gated), `impact` gives callers up to entry points, `siblings` "
        "finds parallel code that usually needs the same change, `writers` shows who writes a table, `routes` lists routes "
        "with their middleware / guards / auth (filter to routes reaching a write, a table or any target, and to routes "
        "missing a given middleware or any auth), `node` / `search` look things up (search also matches middleware and "
        "guard names). Specs: table.column | table:<t> | connection:<name|glob*> | env:<KEY> | config:<a.b> | "
        "Class.method or Class::method (either separator in every language) | Class | pkg.module.func (Python) | file#name (TS/JS: src/app.ts#listOrders, "
        "svc.ts#OrderService.create; the file part may be a path suffix). "
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
        "Platforms: code under a platform condition (#[cfg(windows)], cfg!, #ifdef _WIN32, Platform.OS / Platform.select, "
        "Platform.isIOS / kIsWeb, .ios.ts / .android.ts / .native.ts files, Dart conditional imports) is tagged with "
        "the targets it is built for (`[ios, android]` after a symbol); pass platform=\"ios\" (windows, linux, macos, ios, "
        "android, web) to reaches / impact / downstream / path / routes / search to see that target's build only. "
        "`platforms` lists the targets and tagged code, `platform_divergence` the gaps: a target no variant covers, "
        "API differences between variants, calls into code that is not built on a target. "
        "Bridges: web / native bridge calls (Capacitor plugins, React Native / Expo modules, Flutter platform channels) go "
        "JS / Dart call -SENDS_TO-> endpoint:<protocol>:<module>#<method> -RECEIVED_BY-> Kotlin / Java / Swift / ObjC "
        "method (each receiver tagged with its platform), so impact / reaches cross the bridge; `bridges` lists them with "
        "methods missing on a platform, unreceived sends and external modules. "
        "When a query finds nothing, the reply says why and which query to run instead. "
        "`starters` lists first questions derived from this graph (unguarded write routes, most-reached tables, "
        "most-called functions), each with the call to run. "
        "COVERAGE: `coverage` says which languages / files the index covers (exact, heuristic only, skipped because an "
        "indexer is missing, or unsupported, e.g. Go/Java/Kotlin/Swift/QML/shell files), how many files of each language "
        "are indexed (parse failures, unmapped files) and the blind spots: route / handler registrations cg does not "
        "model, with file:line. Partial answers end with a `coverage note:` line, and every reply's structured content "
        "has a `completeness` object (complete: true/false). For anything not covered, heuristic only or at a blind spot, "
        "fall back to your normal search and file reading: an empty cg answer there is not proof of absence. "
        "`callers` lists direct callers (one level); `impact` follows them to entry points. "
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
                 "has no recorded callers", "no callers found in indexed code", "no direct callers", "no siblings found", "no routes, tables", "no indexed test reaches",
                 "nothing matched the spec", "no channel matches", "no broadcast channels", "no bridge endpoint matches",
                 "no web / native bridge calls")


def _coverage_note() -> str:
    from .coverage import for_graph, note
    try:
        return note(for_graph(_st()))
    except Exception:  # noqa: BLE001
        return ""


class ToolReply(TypedDict):
    """Structured content of every tool reply: the text reply plus machine-readable completeness."""
    result: str
    completeness: dict[str, Any]
    platform: NotRequired[dict[str, Any]]     # the --platform filter a reply applied (target, excluded, unevaluated)
    overrides: NotRequired[dict[str, Any]]    # impact: the override relation of the targets ({overrides, overridden_by})


# scope of the answer a tool is producing, set by the tool while it runs (see _scope); read by the tool() wrapper
_SCOPE: contextvars.ContextVar = contextvars.ContextVar("cg_answer_scope", default=None)
# the platform filter the current reply applied (codegraph/platforms.py filter_info), for the structured content
_PF: contextvars.ContextVar = contextvars.ContextVar("cg_platform_filter", default=None)
# extra machine-readable parts of the current reply (e.g. impact's override relation), merged into the structured content
_EXTRA: contextvars.ContextVar = contextvars.ContextVar("cg_reply_extra", default=None)


class PlatformError(ValueError):
    pass


def _platform(st: GraphStore, platform: str | None) -> tuple[str | None, list[str]]:
    """Resolve a `platform` argument; returns (target, [the reply's filter line]). Records the filter for the
    structured content. Unknown names raise PlatformError (a readable reply listing the known targets)."""
    if not platform:
        return None, []
    from .platforms import filter_info, render_filter, resolve_platform
    try:
        p = resolve_platform(platform)
    except ValueError as e:
        raise PlatformError(str(e)) from None
    info = filter_info(st, p)
    _PF.set(info)
    return p, [render_filter(info)]


def _scope(ids=None, categories=("route", "handler"), whole: bool = False, note: bool = True, unsupported=None) -> None:
    """Declare what the current answer is about: node ids (their languages / directories / repos scope the
    completeness), or the whole index. note=False: structured completeness only, no text note."""
    _SCOPE.set({"ids": list(ids or []), "categories": categories, "whole": whole, "note": note, "unsupported": unsupported})


def _completeness(scope: dict | None) -> dict:
    from .coverage import completeness_for
    try:
        st = _st()
        if scope is None:
            return completeness_for(st, whole=True)
        kw = {} if scope["unsupported"] is None else {"unsupported": scope["unsupported"]}
        return completeness_for(st, scope["ids"], categories=scope["categories"], whole=scope["whole"] or not scope["ids"], **kw)
    except Exception:  # noqa: BLE001  (no graph yet, older DB: completeness unknown, never a crash)
        return {"complete": False, "recorded": False, "languages": {}}


def _run(fn, args, kwargs) -> tuple[str, dict]:
    """Run a tool: text reply (repo-relative paths, coverage notes) + its completeness object."""
    from .coverage import answer_note
    from .plans import PlanError
    token, ptoken, xtoken = _SCOPE.set(None), _PF.set(None), _EXTRA.set(None)
    try:
        try:
            txt = fn(*args, **kwargs)
        except PlanError as e:  # missing plan, invalid YAML: a readable reply, not a bare tool error
            txt = f"plan error: {e}"
        except PlatformError as e:
            txt = f"platform error: {e}"
        scope, pf, extra = _SCOPE.get(), _PF.get(), _EXTRA.get()
    finally:
        _SCOPE.reset(token)
        _PF.reset(ptoken)
        _EXTRA.reset(xtoken)
    comp = _completeness(scope)
    if pf:
        comp = {**comp, "_platform": pf}
    if extra:
        comp = {**comp, "_extra": extra}
    if not isinstance(txt, str) or txt.startswith(("plan error:", "platform error:")):
        return _rel_text(txt), comp
    scoped = ""
    if scope and scope["note"] and not comp.get("complete") and "coverage note:" not in txt:
        scoped = answer_note(comp)
        if scoped:
            txt = txt.rstrip() + "\n" + scoped
    if fn.__name__ != "coverage" and any(m in txt[:600] for m in EMPTY_MARKERS):
        cn = _coverage_note()
        claims_all = cn.startswith("coverage: every source file")
        if cn and not ((scoped or "coverage note:" in txt) and claims_all):
            txt = txt.rstrip() + "\n" + cn
    return _rel_text(txt), comp


def tool(fn):
    """Register an MCP tool. The text reply goes through _rel_text; empty / unknown-symbol replies get a coverage note,
    partial answers a scoped `coverage note:` line; the structured content carries {result, completeness}."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        return _run(fn, args, kwargs)[0]

    @functools.wraps(fn)
    def served(*args, **kwargs):
        txt, comp = _run(fn, args, kwargs)
        pf, extra = comp.pop("_platform", None), comp.pop("_extra", None)
        return CallToolResult(content=[TextContent(type="text", text=txt)],
                              structured_content={"result": txt, "completeness": _rel_obj(comp), **({"platform": pf} if pf else {}),
                                                  **_rel_obj(extra or {})})
    served.__signature__ = inspect.signature(fn, eval_str=True).replace(return_annotation=Annotated[CallToolResult, ToolReply])
    wrapped.completeness = lambda *a, **k: {k2: v for k2, v in _run(fn, a, k)[1].items() if k2 not in ("_platform", "_extra")}
    wrapped.structured = lambda *a, **k: served(*a, **k).structured_content   # tests / scripts: the full structured reply
    server.tool()(served)
    return wrapped


def _rel_obj(o):
    if isinstance(o, str):
        return _rel_text(o)
    if isinstance(o, dict):
        return {k: _rel_obj(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_rel_obj(v) for v in o]
    return o


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
        return f"{short(e.get('fqn') or e['id'])} @{at((e.get('file') or '?') + ':' + str(e.get('line')))}" + Q.generated_label(e)
    return e["name"] + Q.generated_label(e)


@tool
def reaches(targets: list[str], min_confidence: str = "heuristic", group_by: str = "module",
            include_gated: bool = True, max_per_group: int = 25, paths: bool = True, platform: str | None = None) -> str:
    """Reverse transitive dependents of one or more targets (union), e.g.
    ["orders.customer_id", "connection:warehouse", "connection:tenant_*"].

    Groups: RUNTIME (live; reached from http routes/schedules/jobs/listeners, or main()/exported FFI symbols in
    Rust/C/C++), LIBRARY (only via a library's public API), DEV (only tests/benches/examples/build scripts), OPERATOR (artisan commands /
    admin panels only: one-off import & provisioning), GATED (dead under the indexed gate scenario, e.g.
    code that only runs while a feature flag is off), NO-ENTRY. Inside each group items are grouped by
    `group_by` = module | entry_kind | class. Each line: symbol, entry kinds, depth, shortest evidence path
    (hops KIND@file:line; ~r = resolved, ~h = heuristic confidence). At most `max_per_group` lines per top-level
    group (shallowest first). min_confidence: heuristic | resolved | exact.
    platform: only code built for that target (windows, linux, macos, ios, android, web): code under a platform
    condition that is false there (#[cfg], #if, Platform.OS / Platform.isX, kIsWeb, .ios.ts / .android.ts files,
    Dart conditional imports) is left out; the reply's first line names the filter and how many conditions could not
    be evaluated (those stay in)."""
    st = _st()
    pf, pline = _platform(st, platform)
    res = Q.reaches(st, targets, min_conf=min_confidence, platform=pf)
    items = [i for i in res["items"] if not i["is_target"]]
    _scope([x for t in res["targets"].values() for x in t] + [i["id"] for i in items])
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
    if pf and res["platform"].get("targets_not_built"):
        pline.append(f"not built for {pf}: {', '.join(short(t) for t in res['platform']['targets_not_built'][:6])}")
    if missing and len(missing) == len(res["targets"]):
        return "\n".join(pline + [f"targets: {tl}"]) + f"\nno node matches {', '.join(map(repr, missing))}; try search() with part of the name (spec forms are listed in the server instructions)"
    if not items:
        return "\n".join(pline) + ("\n" if pline else "") + (f"targets: {tl}\nnothing depends on the target(s) over dependency edges (min_confidence={min_confidence}). "
                f"try: node() for its direct edges; downstream() for what it reaches; a lower min_confidence")
    live_e = sum(1 for e in entries if e.get("gate_status", "live") == "live")
    extra = Q.inherited_lines(res) + ([f"overrides followed (their dependents count, marked via override): "
                                       f"{', '.join(short(x) for x in res['overrides_followed'][:8])}"]
                                      if res.get("overrides_followed") else [])
    out = pline + [f"targets: {tl} | gate={res.get('gate')} | conf>={min_confidence}", *extra,
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
                line = f"  {short(i.get('fqn') or i['id'])}{Q.platform_label(i)}  {ek_str(i['entry_kinds'])} d{i['depth']}"
                if i.get("via_override"):
                    line += f" (via override {short(i['via_override'])})"
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
def impact(method: str, min_confidence: str = "heuristic", max_items: int = 60, platform: str | None = None) -> str:
    """Reverse callers of a method (Class::method, short or FQN) up to entry points, with the shortest call path
    from each entry point.
    platform: only code built for that target (windows, linux, macos, ios, android, web): code under a platform
    condition that is false there (#[cfg], #if, Platform.OS / Platform.isX, kIsWeb, .ios.ts / .android.ts files,
    Dart conditional imports) is left out; the reply's first line names the filter and how many conditions could not
    be evaluated (those stay in)."""
    st = _st()
    pf, pline = _platform(st, platform)
    r = Q.impact(st, method, min_conf=min_confidence, platform=pf)
    if not r["targets"]:
        return f"no method matches {method!r}; try search() with part of the name"
    _scope(list(r["targets"]) + [c["id"] for c in r["callers"]])
    if pf and set(r["platform"]["targets_not_built"]) == set(r["targets"]):
        return "\n".join(pline + [f"{method} is not built for {pf}: nothing calls it there; platform_divergence(target='{pf}') "
                                   f"lists references to it that would not build"])
    if r["overrides"] or r["overridden_by"]:   # the relation, apart from the callers
        _EXTRA.set({"overrides": {k: [{"id": x["id"], "fqn": x["fqn"], "of": x["of"], "edge": x["edge"]} for x in r[k]]
                                  for k in ("overrides", "overridden_by")}})
    out = pline + [f"targets: {', '.join(short(t) for t in r['targets'][:5])}", *Q.override_lines(r),
                   f"transitive callers: {len(r['callers'])}; entry points: {len(r['entry_points'])}"]
    if not r["callers"] and not r["entry_points"]:
        return "\n".join(out + [Q.explain_no_callers(st, method, r["targets"], min_confidence)])
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
            out.append(f"  {_ename(e)}{Q.CANDIDATE_LABEL if e.get('candidate') else ''}{Q.platform_label(e)}  {fmt_path(e['path'])}")
    mods = defaultdict(int)
    for c in r["callers"]:
        mods[c.get("module") or "?"] += 1
    out.append("\ncallers by module: " + ", ".join(f"{m}×{c}" for m, c in sorted(mods.items(), key=lambda x: -x[1])))
    gen = [c for c in r["callers"] if c.get("generated")]
    if gen:    # indexed with --include-generated: callers in files a generator writes
        out.append(f"in generated / copied files ({len(gen)}): " + ", ".join(f"{short(c['id'])} [{c['generated']}]" for c in gen[:8])
                   + (f" …+{len(gen) - 8}" if len(gen) > 8 else ""))
    refs = [c for c in r["callers"] if c.get("edge") == "REFERENCES_FN"]
    if refs:   # code that holds the function as a value (dispatch table, callback, decorator) rather than calling it
        out.append(f"by reference ({len(refs)}): " + ", ".join(f"{short(c['id'])} ({c.get('how') or 'ref'})" for c in refs[:12])
                   + (f" …+{len(refs) - 12}" if len(refs) > 12 else ""))
    vo = [c for c in r["callers"] if c.get("via_override")]
    if vo:     # calls an override; the base API is reached through it
        out.append(f"via override ({len(vo)}): " + ", ".join(f"{short(c['id'])} -> {', '.join(c['via_override'][:3])}"
                                                             + (f" +{len(c['via_override']) - 3}" if len(c['via_override']) > 3 else "")
                                                             for c in vo[:8]) + (f" …+{len(vo) - 8}" if len(vo) > 8 else ""))
    cand = [c for c in r["callers"] if c.get("candidate")]
    if cand or any(e.get("candidate") for e in r["entry_points"]):   # reached through a candidate call edge (#83)
        out.append(f"candidate callers ({len(cand)}): " + ", ".join(short(c["id"]) for c in cand[:12])
                   + (f" …+{len(cand) - 12}" if len(cand) > 12 else "") + "\n" + Q.CANDIDATE_NOTE)
    vb = [c for c in r["callers"] if c.get("via_base")]
    if vb:     # calls the base declaration; the target is reached through the override
        out.append(f"via base ({len(vb)}): " + ", ".join(f"{short(c['id'])} -> {c['via_base']}" for c in vb[:8])
                   + (f" …+{len(vb) - 8}" if len(vb) > 8 else ""))
    out += _snapshot_client_lines([e["id"] for e in r["entry_points"] if e["id"].startswith("route:")])
    return "\n".join(out)


def _snapshot_client_lines(route_ids) -> list[str]:
    """External (not indexed) client call sites from the snapshot files next to the plans that hit these routes."""
    from . import plans as P
    try:
        hits = P.snapshot_clients(_st(), route_ids, _plans_dir())
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


CALLER_KINDS = ("CALLS", "INSTANTIATES", "IMPLEMENTED_BY", "OVERRIDDEN_BY", "BOUND_TO", "ROUTES_TO", "USES_MIDDLEWARE",
                "HANDLED_BY", "SCHEDULES", "DISPATCHES", "LISTENED_BY", "RENDERS", "USES_COMPOSABLE", "USES_STORE",
                "REFERENCES_FN", "AUTHORIZES_CHANNEL")


@tool
def callers(symbol: str, min_confidence: str = "heuristic", limit: int = 60) -> str:
    """Direct callers of a function / method / class (one level: CALLS, INSTANTIATES, dispatch and framework edges
    into it), each with the call site file:line and confidence. For the full chain up to entry points use impact()."""
    st = _st()
    ids = Q.resolve_targets(st, symbol)
    if not ids:
        return f"no symbol matches {symbol!r}; try search() with part of the name"
    _scope(ids[:5])
    rank = {"exact": 3, "resolved": 2, "heuristic": 1}
    rows = [r for r in st.q(
        f"SELECT src, dst, kind, file, line, confidence, attrs FROM edges WHERE dst IN ({','.join('?' * len(ids[:5]))}) "
        f"AND kind IN ({','.join('?' * len(CALLER_KINDS))}) ORDER BY file, line", tuple(ids[:5]) + CALLER_KINDS)
            if rank.get(r["confidence"], 1) >= rank.get(min_confidence, 1)]
    # a container binding next to the override / implementation edge of the same pair is one relation: show it once
    disp = {(r["src"], r["dst"]) for r in rows if r["kind"] in Q.DISPATCH_KINDS}
    rows = [r for r in rows if not (r["kind"] == "BOUND_TO" and (r["src"], r["dst"]) in disp)]
    head = f"targets: {', '.join(short(t) for t in ids[:5])}"
    _scope(ids[:5] + [r["src"] for r in rows])
    if not rows:
        return head + f"\nno direct callers of {symbol!r} in indexed code (min_confidence={min_confidence}). try: impact() for " \
                      f"entry points, reaches() for every dependent over all edge kinds, node() for its other edges"
    out = [head, f"direct callers: {len({r['src'] for r in rows})} ({len(rows)} sites)"]
    for r in rows[:limit]:
        c = "" if r["confidence"] == "exact" else f" ~{r['confidence'][0]}"
        k = "ref" if r["kind"] == "REFERENCES_FN" else r["kind"]
        cand = Q.CANDIDATE_LABEL if r["attrs"] and '"binding": "candidate"' in r["attrs"] else ""
        out.append(f"  {short(r['src'])}  {k}@{at((r['file'] or '?') + ':' + str(r['line']))}{c}{cand}")
    if len(rows) > limit:
        out.append(f"  … +{len(rows) - limit} more (raise limit)")
    return "\n".join(out)


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


def _prop_text(st, spec: str, what: str, rows: list, limit: int) -> str:
    """`readers` / `writers Type.prop` of a stored property (#88): one line per access site, tests last."""
    if not rows:
        return Q.explain_no_writers(st, spec, what)
    nt = sum(1 for r in rows if r.get("test"))
    out = [f"{spec}: {len(rows)} {'write' if what == 'writers' else 'read'} edges from {len({r['src'] for r in rows})} "
           f"{what}" + (f" ({nt} from test code)" if nt else "")]
    for r in rows[:limit]:
        a = r.get("attrs") or {}
        ex = " ".join(f"{k}={a[k]}" for k in ("receiver", "accessor", "storage") if k in a)
        ek = ",".join(sorted(r["entry_kinds"])) or "-"
        out.append(f"  {'[test] ' if r.get('test') else ''}{r['fqn']}  @{os.path.basename(r['file'] or '?')}:{r['line']}"
                   f"  {ex}  entries: {ek}")
    if len(rows) > limit:
        out.append(f"  … +{len(rows) - limit} more")
    return "\n".join(out)


@tool
def readers(prop: str, limit: int = 60) -> str:
    """Who reads a stored property `Type.prop` (READS_PROP edges, Swift): each site with its receiver (`self`, a
    typed variable), accessor and the entry-point kinds that reach it; test code's reads come last."""
    st = _st()
    return _prop_text(st, prop, "readers", Q.readers(st, prop), limit)


@tool
def writers(table: str, limit: int = 60) -> str:
    """Who writes a DB table (WRITES_TABLE / WRITES_COLUMN edges), grouped by module, with the columns written,
    evidence lines and the entry-point kinds that reach each writer. `Type.prop` instead of a table: who writes
    that stored property (WRITES_PROP edges, Swift)."""
    st = _st()
    if Q.prop_fields(st, table):
        return _prop_text(st, table, "writers", Q.writers(st, table), limit)
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
def bridges(pattern: str | None = None, protocol: str | None = None, unmatched: bool = False) -> str:
    """Web / native bridge calls: Capacitor plugins (registerPlugin / Plugins.X -> @CapacitorPlugin @PluginMethod,
    CAPPlugin), React Native and Expo native modules (NativeModules / TurboModuleRegistry / requireNativeModule ->
    @ReactMethod, Native*Spec overrides, RCT_EXPORT_METHOD / RCT_EXTERN_METHOD, Expo Function / AsyncFunction) and
    Flutter platform channels (MethodChannel.invokeMethod / EventChannel -> setMethodCallHandler / setStreamHandler).
    One endpoint per module method (endpoint:<protocol>:<module>#<method>) with its JS / Dart senders and the native
    receivers per platform; flags methods missing on a target (missing_on), sent methods of a module implemented here
    that no native code receives (no_receiver), native methods nothing sends (no_sender) and modules implemented outside
    the repo (external). Desktop process boundaries too: Electron IPC channels (endpoint:electron-ipc:<channel>,
    ipcRenderer.invoke / send / webContents.send -> ipcMain.handle / on, ipcRenderer.on), the context bridge
    (endpoint:electron-preload:<key>#<member>) and Tauri commands (endpoint:tauri:<command>, invoke -> #[tauri::command]);
    their receivers carry the process role (main / preload / renderer / webview / core). Native -> JS events
    (react-native-event, capacitor-event) and Cordova actions (cordova) too; calls with a dynamic name are listed as
    unresolved. pattern: endpoint name, substring or glob; protocol: capacitor | capacitor-event | cordova |
    react-native | react-native-event | flutter | flutter-event | pigeon | electron-ipc | electron-preload | tauri;
    unmatched: only endpoints with a check."""
    from .bridges import bridges as _br, render_bridges
    return render_bridges(_br(_st(), pattern, protocol=protocol, unmatched=unmatched))


@tool
def protocol_links(pattern: str | None = None, protocol: str | None = None, side: str | None = None,
                   unmatched: bool = False) -> str:
    """Every protocol endpoint in one view (#31 model): HTTP client endpoints and routes (http / ws / graphql), Pusher
    broadcast channels and subscriptions, NestJS microservice messages (nest-rpc / nest-event / nest-ws / grpc), job
    queues (bull, laravel-queue, celery), application events (laravel-event, nest-event-emitter, django-signal), web /
    native bridges and desktop IPC (capacitor, react-native, flutter, electron-ipc, tauri ...) and the generic
    endpoint:<protocol>:<name> nodes (code -SENDS_TO-> endpoint -RECEIVED_BY-> handler, MATCHES_ENDPOINT for
    wildcard / template matches; MQTT, NATS, AMQP, Kafka, Redis pub/sub, Socket.IO). Without arguments: one line per
    protocol (endpoints, send / receive sides, linked, check counts). With a pattern (name, id, substring or glob) /
    protocol / side (send | receive) / unmatched: one block per endpoint with senders (and, for a few endpoints, the
    entry points reaching them), receivers, guards, matches and checks: no_receiver, no_sender, test_sender_only,
    ambiguous, schema_mismatch, unguarded, external (.cg.yaml protocols.external, third-party origins, bridge
    packages). impact / reaches / path / downstream follow SENDS_TO / RECEIVED_BY / MATCHES_ENDPOINT as usual."""
    from .protocols.view import protocols as _pr, render_protocols
    return render_protocols(_pr(_st(), pattern, protocol=protocol, side=side, unmatched=unmatched))


@tool
def external_systems(pattern: str | None = None, protocol: str | None = None, source: str | None = None,
                     tls_off: bool = False) -> str:
    """External systems the code connects to (#40): databases, caches, brokers, mail relays, directories, file-transfer
    hosts, object stores (external:<protocol>:<target>, target host:port when known from a DSN, .env.example or a
    docker-compose service, else env:<KEY>) and third-party HTTP hosts; per system the code and logical connections
    using it (CONNECTS_TO), the entry points reaching them, the address source and the credential source (location
    only, never the value), TLS when known. protocol: postgres | mysql | redis | smtp | amqp | mongodb | ldap | ssh |
    ftp | s3 | https ...; source: literal | env | env-example | compose | config; tls_off: only plaintext systems."""
    from .external import external, render_external
    return render_external(external(_st(), pattern, protocol=protocol, source=source, tls_off=tls_off))


@tool
def llm_tools(pattern: str | None = None, framework: str | None = None, unmatched: bool = False, agent: str | None = None) -> str:
    """LLM tools and MCP primitives (#66): tools offered to a model (OpenAI / Anthropic schema literals, LangChain @tool /
    StructuredTool / BaseTool, Agents SDK @function_tool, LlamaIndex FunctionTool, dict registries and if / match branches
    of agent loops) and MCP server tools / resources / prompts (FastMCP / MCPServer, low-level call_tool) with their
    handler, the tables the handler reaches, the agents offering them and the code calling them (MCP client call_tool /
    read_resource / get_prompt). Checks: no_receiver, no_sender, name_collision; plus agents (model, tools, handoffs),
    dynamic_dispatch (an agent loop picking the tool by a runtime name, not linked) and model calls. framework: mcp |
    openai | anthropic | langchain | openai-agents | llamaindex | custom."""
    from .aitools import render_tools, tools as _tools
    return render_tools(_tools(_st(), pattern, framework=framework, unmatched=unmatched, agent=agent))


@tool
def tests_covering(target: str, min_confidence: str = "heuristic", paths: bool = True, max_depth: int = 3,
                   unit_only: bool = False, exclude_roots: list[str] | None = None, through_roots: bool = False) -> str:
    """Tests that exercise a symbol, route or table. DIRECT: the test code itself calls / instantiates it or sends an
    HTTP request to the route ($this->getJson('/x'), Playwright request.get). TRANSITIVE: through application code
    (test -> route -> controller -> service -> target). target: Class::method | Class | route:VERB /uri | `VERB /path`
    or /path (matched against route URIs) | table.column | any node id. Tests are PHPUnit / Pest (tests/), Vitest / Jest
    / Playwright / Cypress spec files; they never count as callers in the other queries. A base / interface method
    also lists the tests of its overrides, marked `via override X`; `Sub.method` for an inherited method resolves to
    the definition it inherits (noted). Transitive tests are kept near the target: at most max_depth hops (0: any
    depth), not through an app entry point (`@main` types and their members such as `App.body`, Android `*Activity`
    classes, exclude_roots symbols; through_roots=true keeps them). UI / snapshot / screenshot tests (XCUITest,
    Compose UI / Espresso, Playwright / Cypress, *UITests / androidTest folders) are listed apart, or left out
    with unit_only=true. The summary line counts what was left out and why."""
    res = Q.tests_covering(_st(), target, min_conf=min_confidence, near_depth=max_depth or None, unit_only=unit_only,
                           exclude_roots=exclude_roots, through_roots=through_roots)
    _scope(res.get("targets") or [])
    return Q.render_tests_covering(res, show_paths=paths)


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
def search(name: str, kind: str | None = None, limit: int = 20, platform: str | None = None) -> str:
    """Find nodes by name / FQN substring (case-insensitive), optionally filtered by kind
    (class, method, route, command, table, column, connection, config, env, job, admin...). Also matches route
    middleware, guards, auth and access checks (e.g. "auth" finds `auth:api`, `ApiKeyGuard`, `IsAuthenticated`) and
    lists the routes that carry them. Platform-specific symbols carry their targets, e.g. `[ios, android]`.
    platform: only code built for that target (windows, linux, macos, ios, android, web): code under a platform
    condition that is false there (#[cfg], #if, Platform.OS / Platform.isX, kIsWeb, .ios.ts / .android.ts files,
    Dart conditional imports) is left out; the reply's first line names the filter and how many conditions could not
    be evaluated (those stay in)."""
    st = _st()
    pf, _ = _platform(st, platform)
    return Q.render_search(Q.search(st, name, kind=kind, limit=limit, platform=pf))


@tool
def routes(writes: str | None = None, reaches: list[str] | None = None, missing: str | None = None,
           unguarded: bool = False, auth_pattern: str | None = None, max_items: int = 40, paths: bool = True,
           min_confidence: str = "heuristic", platform: str | None = None) -> str:
    """Routes with their middleware / guards / auth, in one call. Optional scope: writes="*" (routes that reach any
    DB write) or writes="<table>", reaches=[specs] (routes that reach any of these nodes: table, column, connection,
    method, env key...). Optional filters: missing="<name>" keeps routes with no guard whose name contains it (e.g.
    "auth:api", "ApiKeyGuard"), unguarded=true keeps routes with no auth guard (the framework presets' auth guards and
    the auth name pattern; extend with auth_pattern, a regex, or .cg.yaml auth.extra_patterns). Each route: guards, what it reaches with one evidence chain, and the frontend callers on a
    combined graph. Guards come from Laravel middleware, Nest guards/interceptors, Express/Koa/Fastify/Hono
    middleware, Next.js middleware.ts / handler wrappers, django-ninja auth= and Django/DRF view access checks.
    min_confidence: keep the default (heuristic) for reviews: every edge still shows its own label, and a stricter
    level names the routes it hides.
    platform: only code built for that target (windows, linux, macos, ios, android, web): code under a platform
    condition that is false there (#[cfg], #if, Platform.OS / Platform.isX, kIsWeb, .ios.ts / .android.ts files,
    Dart conditional imports) is left out; the reply's first line names the filter and how many conditions could not
    be evaluated (those stay in)."""
    from . import routes as R
    st = _st()
    pf, _ = _platform(st, platform)
    _scope(whole=True, categories=("route",), unsupported=False)
    res = R.routes_report(st, writes=writes, reaches=reaches, missing=missing, unguarded=unguarded,
                          auth_pattern=auth_pattern, min_conf=min_confidence, platform=pf)
    return R.render_routes(res, st, max_items=max_items, paths=paths, compact=True)


@tool
def doctor(root: str | None = None, scip: list[str] | None = None, json_output: bool = False) -> str:
    """What this cg installation can index: versions of cg and the tools it uses, whether the Node / PHP / Dart
    extractor dependencies are installed, and per language whether indexing runs in exact or heuristic mode, why, and
    the command that installs what is missing. `root`: a project directory; also checks project conditions (a
    compile_commands.json, `.cg.yaml` rust.targets) and lists only its languages. `scip`: SCIP index files to check
    (documents, occurrences with a usable position, definitions; a warning when cg could not use them)."""
    from .doctor import render, report
    r = report(root, scip=scip)
    return json.dumps(r, indent=2) if json_output else render(r)


@tool
def coverage(path: str | None = None, all_files: bool = False, json_output: bool = False) -> str:
    """Which languages and files this index covers. Per language: parser mode (exact, heuristic only when an
    exact-mode indexer such as rust-analyzer or scip-clang is missing, skipped when the toolchain is missing, with the
    install hint) and file completeness (discovered vs indexed, with parse failures, files over the size limit,
    unmapped files outside the source roots, excluded files); unsupported source types by extension or shebang
    (.go .java .kt .swift .qml .sh ...); blind spots: route / handler registrations cg does not model, with file:line.
    path (optional): a file or directory; says whether cg has it in the graph. all_files: list every file per bucket
    (default: the first 5). json_output: the completeness object as JSON. Not covered, heuristic or a blind spot
    means: use your normal search and file reading for that part."""
    from .coverage import for_graph, render, completeness, SUPPORTED, UNSUPPORTED, FALLBACK
    st = _st()
    covs = for_graph(st)
    _scope(whole=True, note=False)
    if json_output:
        comp = completeness(covs)
        roots = {r or "": [{k: v for k, v in e.items() if k in ("roots_mode", "source_roots", "roots_warnings", "roots_ambiguous",
                                                                  "module_name_collisions")}
                           for e in (c or {}).get("languages", []) if e["language"] == "python" and e.get("source_roots")]
                 for r, c in covs.items()}
        roots = {r: v[0] for r, v in roots.items() if v}
        if roots:
            comp["python_source_roots"] = next(iter(roots.values())) if len(covs) == 1 else roots
        return json.dumps(comp, indent=1)
    out = [render(covs, all_files=all_files)]
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
        gen = None
        for c in covs.values():
            g = (c or {}).get("generated") or {}
            for reason, ps in (g.get("paths") or {}).items():
                if p in ps or any(x.startswith(p.rstrip("/") + "/") for x in ps):
                    gen = gen or reason
            for cp in g.get("copies") or []:
                if p == cp["target"] or p.startswith(cp["target"] + "/"):
                    gen = gen or (f"copy of {cp['source']}/" if cp.get("source") else "copied web assets")
        if hit:
            out.append(f"{path}: in the graph ({hit[0]['file']})" + (f"; {lang} {status}" if lang and status else "")
                       + (f"; generated ({gen}), labelled attrs.generated" if gen else ""))
        elif gen:
            out.append(f"{path}: NOT in the graph: generated / copied file ({gen}), kept out of the graph by default; "
                       f"edit its source instead, or re-index with `cg index --include-generated` to see it.")
        else:
            why = (f"{lang} files are {status.replace('_', ' ')} in this index" if lang and status else
                   f"{lang} is not supported by cg" if lang else "no node of this graph comes from that path")
            out.append(f"{path}: NOT in the graph ({why}); {FALLBACK}.")
    return "\n".join(out)


@tool
def starters() -> str:
    """Starter queries derived from this graph, each with the tool call to run: the write route without an auth guard
    that writes the most tables, the most-written / most-read table, the most-used DB connection and env key, the page
    with the largest backend reach, the most-called functions. A good first call on an unfamiliar repository."""
    from .starters import for_graph, render
    _scope(whole=True, note=False)
    return render(for_graph(_st()))


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
def downstream(target: str, max_per_kind: int = 25, paths: bool = True, min_confidence: str = "heuristic",
               platform: str | None = None) -> str:
    """Forward dependencies of a node: what it ends up calling/reading. On a combined graph a frontend page goes
    page -> composables/components -> HTTP endpoints -> backend routes -> controllers/services -> tables.
    Lists backend routes, tables touched (directly or via columns), columns, connections, config/env keys, each
    with one shortest evidence path. target: page:/reports/:id | app/pages/x.vue | Class::method | ...
    platform: only code built for that target (windows, linux, macos, ios, android, web): code under a platform
    condition that is false there (#[cfg], #if, Platform.OS / Platform.isX, kIsWeb, .ios.ts / .android.ts files,
    Dart conditional imports) is left out; the reply's first line names the filter and how many conditions could not
    be evaluated (those stay in)."""
    st = _st()
    pf, pline = _platform(st, platform)
    r = Q.downstream(st, target, min_conf=min_confidence, platform=pf)
    if not r["targets"]:
        return f"no node matches {target!r}; try search()"
    out = pline + [f"targets: {', '.join(short(t) for t in r['targets'][:4])} | reached {r['reached']} nodes | gate={r.get('gate')}"]
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
def path(source: str, target: str, min_confidence: str = "heuristic", platform: str | None = None) -> str:
    """Shortest forward evidence chain from source to target (e.g. page:/reports/:id ->
    SalesReportService::report), one hop per line with file:line and confidence.
    platform: only code built for that target (windows, linux, macos, ios, android, web): code under a platform
    condition that is false there (#[cfg], #if, Platform.OS / Platform.isX, kIsWeb, .ios.ts / .android.ts files,
    Dart conditional imports) is left out; the reply's first line names the filter and how many conditions could not
    be evaluated (those stay in)."""
    st = _st()
    pf, pline = _platform(st, platform)
    p = Q.path_between(st, source, target, min_conf=min_confidence, platform=pf)
    if not p:
        return "\n".join(pline + [(f"on {pf}: " if pf else "") + Q.explain_no_path(st, source, target, min_confidence)])
    out = pline + [short(p[0]["from"])]
    for h in p:
        pl = f" only on {', '.join(h['platforms']) or 'no known target'}" if h.get("platforms") is not None else ""
        out.append(f"  -{h['kind']}[{h['confidence']} @ {h['at']}{pl}]-> {short(h['to'])}")
    out += [f"note: {n}" for n in Q.path_notes(st, p)]
    return "\n".join(out)


@tool
def platforms() -> str:
    """Platform-specific code in this graph: the targets (declared in .cg.yaml platforms.targets, or detected from
    Flutter platform folders, Expo app.json, React Native, Electron / Tauri, or the conditions themselves), how many
    conditions (#[cfg], cfg!, #if, Platform.OS / Platform.select, Platform.isX / kIsWeb, .ios.ts / .android.ts /
    .native.ts files, Dart conditional imports) were found and evaluated, and files / symbols per target. Pass
    `platform` to reaches / impact / downstream / path / routes / search to see one target's build."""
    from .platforms import render_summary, summary
    _scope(whole=True, note=False)
    return render_summary(summary(_st()))


@tool
def platform_divergence(target: str | None = None, kind: str | None = None, max_items: int = 40) -> str:
    """Where per-platform implementations diverge: VARIANTS (a symbol implemented per platform, by platform files,
    conditional imports or #[cfg] / #if alternatives, with the declared targets no variant covers), API SURFACE (a
    variant lacks a public symbol its siblings define) and REFERENCED WHERE THE CALLEE IS NOT BUILT (a call or
    import that is live on a target where the callee does not exist: a build or runtime failure there). Each finding
    has file:line evidence. target: only findings that affect this target. kind: variants | api_surface |
    missing_callee."""
    from .platforms import divergence, render_divergence, resolve_platform
    st = _st()
    _scope(whole=True, note=False)
    t = None
    if target:
        try:
            t = resolve_platform(target)
        except ValueError as e:
            raise PlatformError(str(e)) from None
    if kind and kind not in ("variants", "api_surface", "missing_callee"):
        return f"unknown kind {kind!r}: use variants, api_surface or missing_callee"
    return render_divergence(divergence(st, kind=kind, target=t), limit=max_items)


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


def _plans_dir() -> str | None:
    """--plans, else plans.dir of the indexed project's .cg.yaml, else the default plans/."""
    if STATE["plans"]:
        return STATE["plans"]
    from .plans import resolve_plans_dir
    return resolve_plans_dir(None, STATE["db"])


def _plan(name: str):
    from . import plans as P
    return P, P.load_plan(name, _plans_dir())


@tool
def plan_list() -> str:
    """List planned-change files (plans/*.yaml): name, status, title, counts, schema errors."""
    from . import plans as P
    rows = P.list_plans(_plans_dir())
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
    _scope(list(res.get("modified") or []) + [i["node"] for i in res.get("items") or [] if i.get("node")])
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


def _flag_roots(db: str) -> list[str] | None:
    """`cg index --python-root` values recorded in a graph DB, so a re-index keeps them (.cg.yaml is re-read anyway)."""
    try:
        return (GraphStore(db).meta().get("stats") or {}).get("python_roots_flag")
    except Exception:  # noqa: BLE001  (missing / older DB: detection or .cg.yaml applies)
        return None


def _flag_generated(db: str) -> bool:
    """`cg index --include-generated` recorded in a graph DB, so a re-index keeps it."""
    try:
        return bool((GraphStore(db).meta().get("stats") or {}).get("include_generated_flag"))
    except Exception:  # noqa: BLE001
        return False


def _index(root: str | None = None, gates: str | None = None, repo: str | None = None) -> str:
    """Implementation of index(); see its docstring."""
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
                    st = index_project(r_root, tmp, r, gates=g, python_roots=_flag_roots(src_db),
                                       include_generated=_flag_generated(src_db))
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
        same_root = meta.get("root") and Path(meta["root"]).resolve() == Path(root).resolve()
        flag_roots = (meta.get("stats") or {}).get("python_roots_flag") if same_root else None
        flag_gen = bool((meta.get("stats") or {}).get("include_generated_flag")) if same_root else False
        st = index_project(root, tmp, Path(root).name, gates=gates, python_roots=flag_roots, include_generated=flag_gen)
        if not st.get("nodes"):
            os.remove(tmp)
            return f"index refused: {_display(root)} produced 0 nodes; the graph is unchanged (is that the right directory?)"
        os.replace(tmp, STATE["db"])
    return (f"indexed {_display(root)} -> {_display(STATE['db'])}: {st['nodes']} nodes, {st['edges']} edges in {st['index_seconds']}s; "
            f"gated edges {st.get('gated_edges')}")


@tool
def index(root: str | None = None, gates: str | None = None, repo: str | None = None) -> str:
    """Re-index after editing (static analysis only: never boots the app or touches a database).
    On a combined graph (backend + frontend): with no arguments every repo is re-indexed from its recorded root and the
    cross-repo link is rebuilt; repo (a name used at link time) re-indexes just that repo; root is matched to the
    recorded repo roots (a repo directory, a path inside one, or a parent of several). A root that matches no repo, or
    a result with 0 nodes, is refused and the graph is left unchanged.
    On a single-repo graph root defaults to the indexed root; gates is a gate-scenario JSON path (defaults to the
    server's --gates). The project config file (.cg.yaml at the root) is read on every
    re-index; Python source roots given with `cg index --python-root` and `--include-generated` are kept.
    Generated, copied and vendored files stay out of the graph (the coverage tool lists them) unless the graph was
    indexed with --include-generated or .cg.yaml sets generated.include."""
    from .config import ConfigError
    try:
        return _index(root, gates, repo)
    except ConfigError as ex:
        return f"index refused: {ex}; the graph is unchanged"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="codegraph-mcp")
    ap.add_argument("--db", default=STATE["db"])
    ap.add_argument("--root")
    ap.add_argument("--gates")
    ap.add_argument("--plans", help="plans directory (default: plans.dir of the project's .cg.yaml, else <repo>/plans)")
    a = ap.parse_args(argv)
    STATE["plans"] = str(Path(a.plans).resolve()) if a.plans else None
    STATE["db"] = str(Path(a.db).resolve())
    STATE["root"] = a.root
    STATE["gates"] = str(Path(a.gates).resolve()) if a.gates else None
    server.run("stdio")


if __name__ == "__main__":
    main()

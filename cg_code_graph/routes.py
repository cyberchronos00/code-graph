"""Routes with their access guards, optionally restricted to routes that reach a write, a table or any sink.

`routes_report(st, writes="*")` answers "which routes reach a DB write, and what protects each of them?" in one call.
Guards are read from what the framework plugins record on route nodes, so the same query works across stacks:

  Laravel                       attrs.middleware (route and group middleware, e.g. `auth:api`)
  NestJS                        attrs.guards / interceptors / pipes (+ USES_MIDDLEWARE to canActivate/intercept)
  Express / Koa / Fastify / Hono attrs.middleware (route and router-level, in mount order) + USES_MIDDLEWARE
  Next.js                       USES_MIDDLEWARE from `middleware.ts` matchers, attrs.wrapped_by (`withSession(handler)`)
  django-ninja                  attrs.auth (operation, router or API level `auth=`)
  Django / DRF                  attrs.access (view decorators, access mixins, permission_classes / authentication_classes)

A guard counts as *auth* when its name is a guard of a framework preset applied at index time (cg_code_graph/presets:
`login_required`, `IsAuthenticated`, `auth:sanctum`, `password.confirm` ...) or matches AUTH_PATTERN, tested on the
name's word tokens (name-based, and labelled as such in the output). Project-specific names come from .cg.yaml
`auth.extra_patterns` or `auth_pattern` (regexes tested on the raw name); preset `not_auth` names (`AllowAny`,
`csrf_protect`, `ThrottlerGuard`) never count. Each auth guard records `auth_by` (preset, name pattern, project pattern).
A route without auth whose guards verify a shared secret or signature (webhook signature checks, HMAC, Laravel `signed`
URLs; SECRET_PATTERN) is reported as SECRET-CHECKED instead of NO AUTH, and `unguarded` leaves it out (and says how
many it left out). The Laravel broadcasting auth route counts as auth: it rejects private / presence subscriptions
without an authenticated user and runs each channel's callback.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict

from . import presets
from .core.model import PROPAGATING
from .core.store import GraphStore
from .coverage import answer_note, completeness_for, possibly_more
from . import query as Q

# name-based fallback for every stack (cg_code_graph/presets/common.yaml): matched against the guard name split into
# lower-case word tokens joined by "_" (ApiKeyGuard -> api_key_guard, auth:api -> auth_api, IsAuthenticated ->
# is_authenticated), so AuditLogInterceptor does not count as "login"
AUTH_PATTERN = presets.values("common", "auth", "token_pattern")
# shared-secret / signature verification (webhooks, signed URLs): the caller proves it knows a secret, not who it is
SECRET_PATTERN = presets.values("common", "secret", "token_pattern")
SECRET_RE = re.compile(SECRET_PATTERN)
WRITE_KINDS = ("WRITES_TABLE", "WRITES_COLUMN")
GUARD_ATTRS = (("guards", "guard"), ("interceptors", "interceptor"), ("pipes", "pipe"), ("auth", "auth"),
               ("access", "access"), ("wrapped_by", "wrapper"), ("middleware", "middleware"))
GUARD_SOURCES = ("Laravel route/group middleware, Nest guards/interceptors/pipes, Express/Koa/Fastify/Hono route and router "
                 "middleware, Next.js middleware.ts matchers and handler wrappers, django-ninja auth=, Django view decorators / "
                 "access mixins and DRF permission_classes, FastAPI Depends()/Security() dependencies and Flask view decorators")


def _names(v) -> list[str]:
    if v is None or v is False or v == "" or v == []:
        return []
    if isinstance(v, (list, tuple)):
        return [x for i in v for x in _names(i)]
    if isinstance(v, dict):
        return _names(v.get("name"))
    s = str(v).strip()
    return [] if s in ("None", "null", "NOT_SET") else [s]


def tokens(name: str) -> str:
    """'ApiKeyGuard' -> 'api_key_guard', 'auth:api' -> 'auth_api', 'login_required' -> 'login_required'."""
    return "_".join(t.lower() for t in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name or ""))


def _meta_of(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        g = GraphStore(path)
        try:
            return g.meta()
        finally:
            g.db.close()
    except Exception:  # noqa: BLE001
        return {}


def guard_setup(st: GraphStore) -> dict:
    """Presets and .cg.yaml patterns recorded at index time, for the guard classification of `st` (a combined graph:
    the union over its repos). A graph indexed before presets were recorded gets every built-in preset."""
    metas = []
    try:
        m = st.meta()
    except Exception:  # noqa: BLE001
        m = {}
    if m.get("repos"):
        for r in m["repos"]:
            src = (m.get("sources") or {}).get(r)
            metas.append(_meta_of(src))
    else:
        metas.append(m)
    applied, auth_x, secret_x = [], [], []
    for mm in metas:
        stt = mm.get("stats") or {}
        pr = (stt.get("presets") or {}).get("applied")
        applied += pr if pr else presets.available()
        cfg = stt.get("config") or {}
        auth_x += (cfg.get("auth") or {}).get("extra_patterns") or []
        secret_x += (cfg.get("secret") or {}).get("extra_patterns") or []
    return {"applied": list(dict.fromkeys(applied or presets.available())),
            "auth_patterns": list(dict.fromkeys(auth_x)), "secret_patterns": list(dict.fromkeys(secret_x))}


class AuthMatcher:
    """Classifies guard names: project patterns (.cg.yaml auth.extra_patterns, --auth-pattern; regexes on the raw
    name), then the applied presets' not_auth and guards names, then the common token pattern."""

    def __init__(self, extra: str | None = None, applied: list[str] | None = None, auth_patterns=(), secret_patterns=()):
        self.applied = list(applied or presets.available())
        self.names = presets.guard_names(self.applied, "auth")
        self.not_auth = presets.guard_names(self.applied, "auth", "not_auth")
        self.secret_names = presets.guard_names(self.applied, "secret")
        self.base = re.compile(AUTH_PATTERN)
        pats = [*auth_patterns, *([extra] if extra else [])]
        self.project = [re.compile(p, re.I) for p in pats]
        self.secret_project = [re.compile(p, re.I) for p in secret_patterns]
        self.extra = re.compile(extra, re.I) if extra else None
        self.pattern = AUTH_PATTERN + "".join(f" | /{p}/i" for p in pats)

    def why(self, name: str) -> str | None:
        """'project pattern', 'preset <name>', 'name pattern', or None (not auth)."""
        if any(rx.search(name or "") for rx in self.project):
            return "project pattern"
        if self.not_auth.contains(name):
            return None
        hit = self.names.lookup(name)
        if hit:
            return f"preset {hit[1]}"
        return "name pattern" if self.base.search(tokens(name)) else None

    def __call__(self, name: str) -> bool:
        return self.why(name) is not None

    def secret(self, name: str) -> bool:
        return bool(SECRET_RE.search(tokens(name)) or self.secret_names.lookup(name)
                    or any(rx.search(name or "") for rx in self.secret_project))


def route_guards(attrs: dict, mw_edges: list[dict], is_auth) -> list[dict]:
    """Every guard-like fact recorded on a route, deduplicated by name, each with kind, source and an auth flag."""
    out, seen = [], set()

    def add(name, kind, source, display=None, auth=None, checks=None, secret=None):
        k = (name or "").lower()
        if not name or k in seen:
            return
        seen.add(k)
        why = (is_auth.why(name) if hasattr(is_auth, "why") else ("name pattern" if is_auth(name) else None)) \
            if auth is None else ("framework" if auth else None)
        if why is None and checks and checks.get("effect") == "rejects":
            # a FastAPI dependency whose source rejects the request (HTTPException 401 / 403, a security scheme,
            # or a nested dependency that does), whatever its name
            how = f"security scheme {checks['scheme']}" if checks.get("scheme") else \
                f"via {checks['rejects_via']}" if checks.get("rejects_via") and not checks.get("rejects") else \
                "raises " + "/".join(str(x) for x in checks.get("rejects", []))
            if checks.get("checked_in") and not checks.get("scheme"):
                how += f" in {checks['checked_in'][0]}"       # a helper the dependency / middleware calls (#59)
            why = f"dependency check ({how})"
        g = {"name": display or name, "kind": kind, "source": source, "auth": why is not None,
             "secret": secret if secret is not None else
             is_auth.secret(name) if hasattr(is_auth, "secret") else bool(SECRET_RE.search(tokens(name)))}
        if why:
            g["auth_by"] = why
        if checks:
            g["checks"] = checks
        out.append(g)
    wh = attrs.get("webhook") or {}
    if wh.get("verified"):
        # a webhook receiver whose handler verifies the provider's signature (#37, cg_code_graph/webhooks.py)
        add(f"webhook signature ({wh.get('how')})", "webhook", wh.get("check") or "attrs.webhook", auth=False, secret=True)
    if attrs.get("broadcast_auth"):
        add("channel callbacks", "broadcast-auth", "Laravel BroadcastController: authenticated user + Broadcast::channel callback",
            auth=True)
    for key, kind in GUARD_ATTRS:
        v = attrs.get(key)
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, dict) and x.get("checks"):
                add(_names(x)[0] if _names(x) else None, kind, f"attrs.{key}", checks=x["checks"])
        for n in _names(v):
            add(n, kind, f"attrs.{key}")
    for c in attrs.get("conditions") or []:
        if isinstance(c, str) and c.startswith("wrapped:"):
            add(c.split(":", 1)[1], "wrapper", "urlconf wrapper")
    for e in mw_edges:
        a = json.loads(e.get("attrs") or "{}")
        nm = a.get("name") or Q.short_id(e["dst"])
        add(nm, "middleware", f"USES_MIDDLEWARE {e['dst']}", display=Q.short_id(e["dst"]) if "#" in e["dst"] else nm)
    return out


def _hop(p: dict) -> str:
    f, _, ln = (p.get("at") or "?").rpartition(":")
    c = "" if p["confidence"] == "exact" else f"~{p['confidence'][0]}"
    return f"{p['kind']}@{os.path.basename(f)}:{ln}{c}"


def fmt_chain(path: list[dict], limit=8) -> str:
    if not path:
        return "(direct)"
    hops = [_hop(p) for p in path[:limit]]
    more = f" …+{len(path) - limit}" if len(path) > limit else ""
    return " → ".join(hops) + more + f" → {Q.short_id(path[-1]['to'])}"


def _route_loc(n: dict, attrs: dict) -> str:
    f = n.get("file") or "?"
    repo = attrs.get("repo")
    return f"{repo}/{f}:{n.get('line')}" if repo and not f.startswith(repo + "/") else f"{f}:{n.get('line')}"


def _clients(st: GraphStore, rid: str, limit=3) -> list[str]:
    out = []
    for m in st.q("SELECT src FROM edges WHERE kind='MATCHES_ROUTE' AND dst=?", (rid,)):
        for h in st.q("SELECT src, file, line FROM edges WHERE kind='HTTP_CALLS' AND dst=?", (m["src"],)):
            out.append(f"{Q.short_id(h['src'])} @{os.path.basename(h['file'] or '?')}:{h['line']}")
    return sorted(set(out))[:limit]


def _groups_for_writes(st: GraphStore, table: str | None) -> dict[str, dict]:
    """table -> {writer node -> first write edge}."""
    if table and table not in ("*", "any", "all"):
        t = table.split(":", 1)[1] if table.startswith("table:") else table
        rows = st.q("""SELECT src, kind, dst, file, line, confidence FROM edges
                       WHERE (kind='WRITES_TABLE' AND dst=?) OR (kind='WRITES_COLUMN' AND dst LIKE ?) ORDER BY file, line""",
                    (f"table:{t}", f"column:{t}.%"))
    else:
        rows = st.q("SELECT src, kind, dst, file, line, confidence FROM edges WHERE kind IN ('WRITES_TABLE','WRITES_COLUMN') ORDER BY file, line")
    groups: dict[str, dict] = defaultdict(dict)
    for r in rows:
        t = r["dst"].split(":", 1)[1].split(".")[0]
        groups[t].setdefault(r["src"], dict(r))
    return groups


def routes_report(st: GraphStore, writes: str | None = None, reaches: list[str] | None = None, missing: str | None = None,
                  unguarded: bool = False, auth_pattern: str | None = None, min_conf: str = "heuristic",
                  gate: str | None = "auto", platform: str | None = None, deadline: float | None = None) -> dict:
    """deadline: a time.time() value; past it the traversal stops with query.Deadline (starters' time budget)."""
    gs = guard_setup(st)
    is_auth = AuthMatcher(auth_pattern, gs["applied"], gs["auth_patterns"], gs["secret_patterns"])
    if gate == "auto":
        gate = Q.default_gate(st)
    routes = {r["id"]: dict(r) for r in st.q("SELECT id, name, file, line, module, attrs FROM nodes WHERE kind='route'")}
    if platform:
        from .platforms import exclusions
        xn = exclusions(st, platform)["nodes"]
        routes = {k: v for k, v in routes.items() if k not in xn}       # routes not registered on that target
    mw = defaultdict(list)
    for e in st.q("SELECT src, dst, attrs FROM edges WHERE kind='USES_MIDDLEWARE' AND src LIKE 'route:%'"):
        mw[e["src"]].append(dict(e))
    # what each route reaches: group label -> (targets, extra final hop per target)
    groups: dict[str, tuple[list[str], dict]] = {}
    mode, unresolved = "all", []
    if writes:
        mode = "writes"
        for t, ws in _groups_for_writes(st, writes).items():
            groups[f"writes {t}"] = (list(ws), ws)
    if reaches:
        mode = "reaches" if not writes else "writes+reaches"
        for spec in reaches:
            ids = Q.resolve_targets(st, spec)
            if not ids:
                unresolved.append(spec)
            groups[f"reaches {spec}"] = (ids, {})
    reached: dict[str, list[dict]] = defaultdict(list)
    labels = [k for k, (targets, _) in groups.items() if targets]
    if labels and routes:
        # one walk for all groups, restricted to what the routes reach: scales with the routes' reach, not with
        # routes x write targets (one reverse closure per table)
        gc = Q.GroupClosures(st, [groups[k][0] for k in labels], kinds=PROPAGATING, min_conf=min_conf,
                             exclude_gate=gate, platform=platform, within=list(routes), deadline=deadline)
        pairs = [(rid, i) for rid in routes for i in range(len(labels)) if gc.groups_of(rid) >> i & 1]
        pairs.sort(key=lambda x: x[1])
        pe = {pr: gc.path_edges(*pr) for pr in pairs}
        hops = gc.hops([e for v in pe.values() for e in v])
        for rid, i in pairs:
            label, final = labels[i], groups[labels[i]][1]
            p = [hops[e] for e in pe[(rid, i)]]
            end = p[-1]["to"] if p else rid
            w = final.get(end)
            if w:
                p.append({"from": w["src"], "kind": w["kind"], "to": w["dst"], "at": f"{w['file']}:{w['line']}", "confidence": w["confidence"]})
            reached[rid].append({"what": label, "via": Q.short_id(w["src"]) if w else None, "depth": gc.depth(rid, i), "path": p, "path_confidence": Q.path_confidence(p),
                                 "gated_only": bool(gate) and not gc.reached(rid, i, live=True)})
    items = []
    for rid, n in routes.items():
        if mode != "all" and rid not in reached:
            continue
        a = json.loads(n.get("attrs") or "{}")
        g = route_guards(a, mw.get(rid, []), is_auth)
        it = {"route": rid, "name": n["name"], "at": _route_loc(n, a), "framework": a.get("framework"), "guards": g,
              "has_auth": any(x["auth"] for x in g), "secret_checked": any(x.get("secret") for x in g),
              **({"webhook": a["webhook"]} if a.get("webhook") else {}),
              "reaches": sorted(reached.get(rid, []), key=lambda x: x["what"]),
              "clients": _clients(st, rid)}
        items.append(it)
    total = len(items)
    flt = []
    if missing:
        m = missing.lower()
        items = [i for i in items if not any(m in x["name"].lower() for x in i["guards"])]
        flt.append(f"missing a guard matching '{missing}'")
    if unguarded:
        secret = [i for i in items if not i["has_auth"] and i["secret_checked"]]
        items = [i for i in items if not i["has_auth"] and not i["secret_checked"]]
        flt.append("no auth guard" + (f" ({len(secret)} secret-checked route(s) left out: "
                                      f"{', '.join(i['name'] for i in secret[:4])}{' …' if len(secret) > 4 else ''})" if secret else ""))
    items.sort(key=lambda i: (i["has_auth"], i["secret_checked"], i["at"], i["name"]))
    below = []
    if mode != "all" and min_conf != "heuristic":
        # a stricter confidence level silently drops routes whose only chain has a resolved / heuristic hop: name them
        mine = {i["route"] for i in items}
        loose = routes_report(st, writes=writes, reaches=reaches, missing=missing, unguarded=unguarded,
                              auth_pattern=auth_pattern, min_conf="heuristic", gate=gate, platform=platform)
        below = [i["name"] for i in loose["items"] if i["route"] not in mine]
    return {"below_confidence": below, "mode": mode, "writes": writes, "reaches": reaches or [], "unresolved": unresolved, "filters": flt,
            "total_routes": len(routes), "matched": total, "items": items, "auth_pattern": is_auth.pattern, "gate": gate, "guard_presets": is_auth.applied,
            "write_tables": sorted({k.split(" ", 1)[1] for k in groups if k.startswith("writes ")}),
            "min_confidence": min_conf, **({"platform": _pinfo(st, platform)} if platform else {})}


def _pinfo(st: GraphStore, platform: str) -> dict:
    from .platforms import filter_info
    return filter_info(st, platform)


def explain_empty(st: GraphStore, res: dict) -> str:
    """Why a routes query came back empty, and what to run instead."""
    if not res["total_routes"]:
        pages = st.q("SELECT lang, count(*) c FROM nodes WHERE kind='page' GROUP BY lang ORDER BY c DESC")
        if pages:       # a mobile / desktop app: screens and navigations, not HTTP routes (#75)
            nav = st.q("SELECT count(*) c FROM edges WHERE kind='NAVIGATES_TO'")[0]["c"]
            via = {"swift": "SwiftUI WindowGroup / NavigationLink / navigationDestination", "dart": "Flutter routes",
                   "kotlin": "Compose navigation", "ts": "file-based or router pages"}
            kinds = "; ".join(f"{r['c']} {via.get(r['lang'], r['lang'] or 'app')}" for r in pages)
            first = (st.q("SELECT n.id FROM nodes n WHERE n.kind='page' AND EXISTS (SELECT 1 FROM edges e WHERE e.dst=n.id AND "
                          "e.kind='NAVIGATES_TO') ORDER BY n.file, n.line LIMIT 1")
                     or st.q("SELECT id FROM nodes WHERE kind='page' ORDER BY file, line LIMIT 1"))[0]["id"]
            np = sum(r['c'] for r in pages)
            return (f"`routes` lists server-side HTTP routes, and this graph has none. It has {np} app "
                    f"screen{'s' if np != 1 else ''} ({kinds}) and {nav} navigation edge{'s' if nav != 1 else ''} to them: `search '' --kind page` "
                    f"lists the screens, `downstream {first}` follows one, `node {first}` shows what navigates to it.")
        return ("no route nodes in this graph (the indexed project has no HTTP routes, or its framework is not detected; "
                "`stats` shows the node kinds). Try `reaches` / `impact` from the entry points that do exist.")
    if res["unresolved"]:
        return f"no node matches {', '.join(map(repr, res['unresolved']))}; try `search` to find the exact name."
    if res["mode"].startswith("writes") and not res["write_tables"]:
        t = res["writes"]
        if t and t not in ("*", "any", "all"):
            base = t.split(":", 1)[-1]
            near = [r["id"][6:] for r in st.q("SELECT id FROM nodes WHERE kind='table' AND id LIKE ? ORDER BY id LIMIT 6", (f"%{base}%",))]
            if not near:
                near = [r["id"][6:] for r in st.q("SELECT id FROM nodes WHERE kind='table' ORDER BY id LIMIT 12")]
            reads = st.q("SELECT count(*) c FROM edges WHERE kind IN ('READS_TABLE','READS_COLUMN') AND (dst=? OR dst LIKE ?)",
                         (f"table:{base}", f"column:{base}.%"))[0]["c"]
            if not st.q("SELECT 1 FROM nodes WHERE id=?", (f"table:{base}",)):
                return (f"no table '{base}' in the graph" + (f"; tables: {', '.join(near)}" if near else "") +
                        ". Table names are the DB names (e.g. Django `app_model`); `search` with kind=table lists them.")
            return (f"table '{base}' has no recorded writers ({reads} read edges). Writes through raw SQL strings, bulk "
                    f"helpers or admin form saves may not be modelled; try `reaches(['table:{base}'])` to see every dependent.")
        return "no write edges (WRITES_TABLE / WRITES_COLUMN) in this graph; try `reaches` with a table or column instead."
    if res["matched"] and res["filters"]:
        return (f"{res['matched']} route(s) match before filtering, and every one of them has a guard that satisfies the filter "
                f"({'; '.join(res['filters'])}). Drop the filter to see their guards.")
    if res["mode"] != "all":
        what = ", ".join(res["write_tables"]) if res["mode"].startswith("writes") else ", ".join(res["reaches"])
        return (f"no route reaches {what}: the code involved is reached only from other entry points (commands, jobs, "
                f"listeners, pages) or not at all. `reaches` on the same target shows which entry kinds do reach it.")
    return "no routes match."


def render_routes(res: dict, st: GraphStore | None = None, max_items: int = 60, paths: bool = True, compact: bool = False) -> str:
    what = {"all": "all routes", "writes": "routes reaching a write" + (f" to {res['writes']}" if res["writes"] not in ("*", "any", "all", None) else " (any table)"),
            "reaches": f"routes reaching {', '.join(res['reaches'])}",
            "writes+reaches": f"routes reaching a write to {res['writes']} and {', '.join(res['reaches'])}"}[res["mode"]]
    items = res["items"]
    comp = route_completeness(st) if st is not None else {"complete": True}
    res["completeness"] = comp
    more = possibly_more(comp)
    if not more:
        head = f"{what}: {res['matched']} of {res['total_routes']} routes"
    elif res["mode"] == "all":
        head = f"{what}: {res['matched']} indexed (possibly more: {more})"
    else:
        head = f"{what}: {res['matched']} of {res['total_routes']} indexed routes (possibly more: {more})"
    if res["filters"]:
        head += f" | filter: {'; '.join(res['filters'])} -> {len(items)}"
    na = sum(1 for i in items if not i["has_auth"] and not i.get("secret_checked"))
    ns = sum(1 for i in items if not i["has_auth"] and i.get("secret_checked"))
    out = [head, f"auth guard: {len(items) - na - ns} with, {na} without" + (f", {ns} secret-checked (signature / shared secret, no user auth)" if ns else "")
           + " (auth = a framework preset auth guard or a name matching the auth pattern)"]
    if res.get("platform"):
        from .platforms import render_filter
        out.insert(0, render_filter(res["platform"]))
    by = defaultdict(int)
    for i in items:
        for g in i["guards"]:
            if g.get("auth_by"):
                by[g["auth_by"]] += 1
    if any(k.startswith(("preset", "project")) for k in by):
        out.append("auth guards by source: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))))
    if res.get("below_confidence"):
        b = res["below_confidence"]
        out.append(f"+{len(b)} more route(s) match only through lower-confidence edges, hidden by "
                   f"min_confidence={res['min_confidence']}: {', '.join(b[:5])}{' …' if len(b) > 5 else ''}; "
                   f"min_confidence=heuristic (the default) includes them, each with its confidence label")
    if not items:
        out.append("")
        out.append(explain_empty(st, res) if st is not None else "no routes match")
        if more:
            out.append(answer_note(comp))
        return "\n".join(out)
    for i in items[:max_items]:
        gs = ", ".join(f"{g['name']}{' [auth]' if g['auth'] else (' [secret]' if g.get('secret') else '')}" for g in i["guards"]) or "(none)"
        flag = "" if i["has_auth"] else ("  SECRET-CHECKED" if i.get("secret_checked") else "  NO AUTH")
        if i.get("webhook") and not i["webhook"].get("verified"):
            flag += f"  WEBHOOK UNVERIFIED ({i['webhook'].get('provider')})"
        out.append("")
        out.append(f"{i['name']}  @{i['at']}{flag}")
        out.append(f"    guards: {gs}")
        if i.get("webhook"):
            w = i["webhook"]
            ev = w.get("events") or []
            out.append(f"    webhook: {w.get('provider')}, " + (f"verified at {w.get('check')}" if w.get("verified") else
                       "no signature check (reads " + ", ".join(w.get("headers") or []) + ")")
                       + (f"; events: {', '.join(ev[:8])}{' …' if len(ev) > 8 else ''}" if ev else ""))
        for r in i["reaches"][:6 if not compact else 3]:
            g = " [gated-only]" if r["gated_only"] else ""
            line = f"    {r['what']}{' via ' + r['via'] if r.get('via') else ''}{g} conf={r['path_confidence']}"
            if paths:
                line += "  " + fmt_chain(r["path"])
            out.append(line)
        if len(i["reaches"]) > (6 if not compact else 3):
            out.append(f"    … +{len(i['reaches']) - (6 if not compact else 3)} more")
        if i["clients"]:
            out.append(f"    called from: {'; '.join(i['clients'])}")
    if len(items) > max_items:
        out.append(f"\n… +{len(items) - max_items} more routes (raise max_items)")
    out.append("")
    if compact:
        out.append("guards: route-level and global enhancers per framework; Laravel kernel middleware and Django's MIDDLEWARE setting "
                   "apply to every route and are not repeated per route.")
    else:
        out.append(f"guards come from route-level facts ({GUARD_SOURCES}); Laravel kernel middleware and Django's MIDDLEWARE "
                   f"setting apply to every route and are not repeated per route.")
    if more:
        out.append(answer_note(comp))
    return "\n".join(out)


def route_completeness(st: GraphStore) -> dict:
    """Completeness of a route list: route blind spots and files not indexed in every language of the index
    (unsupported languages are listed by `coverage`, not here)."""
    return completeness_for(st, categories=("route",), whole=True, unsupported=False)

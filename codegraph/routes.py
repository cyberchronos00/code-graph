"""Routes with their access guards, optionally restricted to routes that reach a write, a table or any sink.

`routes_report(st, writes="*")` answers "which routes reach a DB write, and what protects each of them?" in one call.
Guards are read from what the framework plugins record on route nodes, so the same query works across stacks:

  Laravel                       attrs.middleware (route and group middleware, e.g. `auth:api`)
  NestJS                        attrs.guards / interceptors / pipes (+ USES_MIDDLEWARE to canActivate/intercept)
  Express / Koa / Fastify / Hono attrs.middleware (route and router-level, in mount order) + USES_MIDDLEWARE
  Next.js                       USES_MIDDLEWARE from `middleware.ts` matchers, attrs.wrapped_by (`withSession(handler)`)
  django-ninja                  attrs.auth (operation, router or API level `auth=`)
  Django / DRF                  attrs.access (view decorators, access mixins, permission_classes / authentication_classes)

A guard counts as *auth* when its name matches AUTH_PATTERN, tested on the name's word tokens (name-based, and labelled
as such in the output); pass `auth_pattern` (a regex tested on the raw name) to add project-specific names.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict

from .core.model import PROPAGATING
from .core.store import GraphStore
from . import query as Q

# matched against the guard name split into lower-case word tokens joined by "_" (ApiKeyGuard -> api_key_guard,
# auth:api -> auth_api, IsAuthenticated -> is_authenticated), so AuditLogInterceptor does not count as "login"
AUTH_PATTERN = (r"(^|_)(auth|authn|authz|authed|authenticate[ds]?|authentication|authori[sz](e[ds]?|ation|er)|login|logged|jwt|token|session|sanctum|passport|bearer|api_key|permissions?|"
                r"permission_[a-z]+|admin|staff|superuser|signed|can|roles?|acl|verified|protected|protect|require_user|"
                r"current_user|oauth[a-z0-9]*|oidc|saml|clerk|firebase_auth)(_|$)")
WRITE_KINDS = ("WRITES_TABLE", "WRITES_COLUMN")
GUARD_ATTRS = (("guards", "guard"), ("interceptors", "interceptor"), ("pipes", "pipe"), ("auth", "auth"),
               ("access", "access"), ("wrapped_by", "wrapper"), ("middleware", "middleware"))
GUARD_SOURCES = ("Laravel route/group middleware, Nest guards/interceptors/pipes, Express/Koa/Fastify/Hono route and router "
                 "middleware, Next.js middleware.ts matchers and handler wrappers, django-ninja auth=, Django view decorators / "
                 "access mixins and DRF permission_classes")


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


class AuthMatcher:
    def __init__(self, extra: str | None = None):
        self.base = re.compile(AUTH_PATTERN)
        self.extra = re.compile(extra, re.I) if extra else None
        self.pattern = AUTH_PATTERN + (f" | /{extra}/i" if extra else "")

    def __call__(self, name: str) -> bool:
        return bool(self.base.search(tokens(name)) or (self.extra and self.extra.search(name)))


def route_guards(attrs: dict, mw_edges: list[dict], is_auth) -> list[dict]:
    """Every guard-like fact recorded on a route, deduplicated by name, each with kind, source and an auth flag."""
    out, seen = [], set()

    def add(name, kind, source, display=None):
        k = (name or "").lower()
        if not name or k in seen:
            return
        seen.add(k)
        out.append({"name": display or name, "kind": kind, "source": source, "auth": is_auth(name)})
    for key, kind in GUARD_ATTRS:
        for n in _names(attrs.get(key)):
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
                  gate: str | None = "auto") -> dict:
    is_auth = AuthMatcher(auth_pattern)
    if gate == "auto":
        gate = Q.default_gate(st)
    routes = {r["id"]: dict(r) for r in st.q("SELECT id, name, file, line, module, attrs FROM nodes WHERE kind='route'")}
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
    for label, (targets, final) in groups.items():
        if not targets:
            continue
        depth = Q.reverse_closure(st, targets, kinds=PROPAGATING, min_conf=min_conf)
        hit = [r for r in depth if r in routes]
        if not hit:
            continue
        paths = Q.shortest_paths(st, depth, min_conf=min_conf)
        live = Q.reverse_closure(st, targets, kinds=PROPAGATING, min_conf=min_conf, exclude_gate=gate) if gate else depth
        for rid in hit:
            p = list(paths.get(rid) or [])
            end = p[-1]["to"] if p else rid
            w = final.get(end)
            if w:
                p.append({"from": w["src"], "kind": w["kind"], "to": w["dst"], "at": f"{w['file']}:{w['line']}", "confidence": w["confidence"]})
            reached[rid].append({"what": label, "via": Q.short_id(w["src"]) if w else None, "depth": depth[rid], "path": p, "path_confidence": Q.path_confidence(p),
                                 "gated_only": rid not in live})
    items = []
    for rid, n in routes.items():
        if mode != "all" and rid not in reached:
            continue
        a = json.loads(n.get("attrs") or "{}")
        g = route_guards(a, mw.get(rid, []), is_auth)
        it = {"route": rid, "name": n["name"], "at": _route_loc(n, a), "framework": a.get("framework"), "guards": g,
              "has_auth": any(x["auth"] for x in g), "reaches": sorted(reached.get(rid, []), key=lambda x: x["what"]),
              "clients": _clients(st, rid)}
        items.append(it)
    total = len(items)
    flt = []
    if missing:
        m = missing.lower()
        items = [i for i in items if not any(m in x["name"].lower() for x in i["guards"])]
        flt.append(f"missing a guard matching '{missing}'")
    if unguarded:
        items = [i for i in items if not i["has_auth"]]
        flt.append("no auth guard")
    items.sort(key=lambda i: (i["has_auth"], i["at"], i["name"]))
    below = []
    if mode != "all" and min_conf != "heuristic":
        # a stricter confidence level silently drops routes whose only chain has a resolved / heuristic hop: name them
        mine = {i["route"] for i in items}
        loose = routes_report(st, writes=writes, reaches=reaches, missing=missing, unguarded=unguarded,
                              auth_pattern=auth_pattern, min_conf="heuristic", gate=gate)
        below = [i["name"] for i in loose["items"] if i["route"] not in mine]
    return {"below_confidence": below, "mode": mode, "writes": writes, "reaches": reaches or [], "unresolved": unresolved, "filters": flt,
            "total_routes": len(routes), "matched": total, "items": items, "auth_pattern": is_auth.pattern, "gate": gate,
            "write_tables": sorted({k.split(" ", 1)[1] for k in groups if k.startswith("writes ")}),
            "min_confidence": min_conf}


def explain_empty(st: GraphStore, res: dict) -> str:
    """Why a routes query came back empty, and what to run instead."""
    if not res["total_routes"]:
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
    head = f"{what}: {res['matched']} of {res['total_routes']} routes"
    if res["filters"]:
        head += f" | filter: {'; '.join(res['filters'])} -> {len(items)}"
    na = sum(1 for i in items if not i["has_auth"])
    out = [head, f"auth guard: {len(items) - na} with, {na} without (auth = guard name matches the auth pattern; name-based)"]
    if res.get("below_confidence"):
        b = res["below_confidence"]
        out.append(f"+{len(b)} more route(s) match only through lower-confidence edges, hidden by "
                   f"min_confidence={res['min_confidence']}: {', '.join(b[:5])}{' …' if len(b) > 5 else ''}; "
                   f"min_confidence=heuristic (the default) includes them, each with its confidence label")
    if not items:
        out.append("")
        out.append(explain_empty(st, res) if st is not None else "no routes match")
        return "\n".join(out)
    for i in items[:max_items]:
        gs = ", ".join(f"{g['name']}{' [auth]' if g['auth'] else ''}" for g in i["guards"]) or "(none)"
        flag = "" if i["has_auth"] else "  NO AUTH"
        out.append("")
        out.append(f"{i['name']}  @{i['at']}{flag}")
        out.append(f"    guards: {gs}")
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
    return "\n".join(out)

"""Cross-repo linking: client HTTP endpoints -> route nodes of other repos.

link(backend_db, frontend_db, out_db) is the two-repo form. link_many(repos, out_db) merges N
graphs (role backend, frontend or both):
  * nodes/edges of every graph (`file` prefixed with the repo name, attrs.repo set),
  * an id that occurs in more than one repo is stored as `<repo>:<id>` and its edges are rewritten;
    unique ids are unchanged, so a two-repo link with no shared ids keeps the same ids,
  * MATCHES_ROUTE from every repo's http endpoints to routes of every other server repo,
  * entry tagging once over the union, plus the live-under-gate closure for the first server scenario.

Matching is deterministic (no guessing):
  - the client path is the extractor's URL template with the origin (scheme://host or the
    unknown server-URL placeholder) and query string removed;
  - backend URIs are tried as declared and, for routes from routes/api.php, with the `/api`
    prefix Laravel adds;
  - segments match when literal == literal, client placeholder <-> route {param},
    client literal -> route {param} (resolved), or a client segment with an embedded
    placeholder fully matches a route literal (heuristic, e.g. export.{format} ~ export.csv);
  - method must match (HEAD ~ GET); the most specific route (most literal==literal segments)
    wins; ties are kept and marked heuristic (ambiguous).

Realtime: client channel subscriptions (channel_sub:<name>, from Echo / pusher-js / useEcho) get MATCHES_CHANNEL ->
the backend channel pattern they fit (`orders.{id}` ~ `orders.{orderId}`, a literal fits a {param} segment), and
LISTENS_FOR -> the backend broadcast event named by `.listen('X')` (`App\\Events\\X` by Echo's default namespace,
`.listen('.name')` by broadcastAs()). Endpoints called only from tests (attrs.test_only) are matched too, so
`tests` can follow them to routes, but they do not count in the match statistics or the uncalled-routes list.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from .core.model import CONFIDENCE_RANK
from .core.store import GraphStore

PARAM = re.compile(r"^\{(\w+)(\?)?\}$")
CATCH_ALL = re.compile(r"^\{(\w+)\*(\?)?\}$")   # {rest*} one or more segments, {rest*?} zero or more (TS routers, Next.js)
PH = re.compile(r"\{[^{}]*\}")


def _segs(p: str) -> list[str]:
    return [s for s in p.split("/") if s != ""]


def match_path(client: str, route: str) -> tuple[bool, dict]:
    c, r = _segs(client), _segs(route)
    info = {"lit": 0, "param": 0, "lit_into_param": 0, "ph_into_lit": 0}
    if len(c) > len(r) and not (r and CATCH_ALL.match(r[-1])):
        return False, info
    for i, rs in enumerate(r):
        cm = CATCH_ALL.match(rs)
        if cm and i == len(r) - 1:
            rest = c[i:]
            if not rest and not cm.group(2):
                return False, info
            info["lit_into_param" if any(not PH.search(x) for x in rest) else "param"] += 1
            return True, info
        pm = PARAM.match(rs)
        if i >= len(c):
            if pm and pm.group(2):
                continue  # optional trailing param
            return False, info
        cs = c[i]
        c_is_ph = bool(PARAM.match(cs)) or cs == "{?}"
        if pm:
            if c_is_ph:
                info["param"] += 1
            else:
                info["lit_into_param"] += 1
            continue
        if cs == rs:
            info["lit"] += 1
            continue
        if PH.search(rs):  # route segment with embedded params, e.g. export.{format}
            if PH.sub("{}", cs) == PH.sub("{}", rs):
                info["param"] += 1
                continue
            rrx = "^" + "".join("[^/]+" if PH.fullmatch(part) else re.escape(part) for part in re.split(r"(\{[^{}]*\})", rs)) + "$"
            if not PH.search(cs) and re.match(rrx, cs):
                info["lit_into_param"] += 1
                continue
        if PH.search(cs):
            rx = "^" + "".join("[^/]+" if PH.fullmatch(part) else re.escape(part) for part in re.split(r"(\{[^{}]*\})", cs)) + "$"
            if re.match(rx, rs):
                info["ph_into_lit"] += 1
                continue
        return False, info
    return True, info


def _method_ok(cm: str, rm: str) -> bool:
    rms = set(rm.upper().replace("|", ",").split(","))
    return cm in rms or "ANY" in rms or (cm == "HEAD" and "GET" in rms)


def suffix_candidates(path: str, routes: list[dict]):
    """Origin unknown: align the client path with the END of route URIs (prefix assumed to be the base)."""
    cs = _segs(path)
    out = []
    for r in routes:
        us = _segs(r["uri"])
        if len(us) <= len(cs):
            continue
        ok, info = match_path("/" + "/".join(cs), "/" + "/".join(us[len(us) - len(cs):]))
        if ok and info["lit"] > 0:
            out.append((r, "suffix", info))
    return out


def match_endpoint(method: str, path: str, routes: list[dict], origin_kind: str = "api", origin: str | None = None,
                   base_prefix: str | None = None) -> dict:
    """base_prefix: path part of a configured base URL already folded into `path` (http node attrs.base); when the
    full path matches nothing, the path without it is tried as an unknown-origin path (heuristic)."""
    if base_prefix and path.startswith(base_prefix + "/"):
        res = match_endpoint(method, path, routes, origin_kind, origin)
        if res["matched"]:
            return res
        alt = match_endpoint(method, path[len(base_prefix):], routes, "unknown", origin)
        if alt["matched"]:
            for m in alt["matched"]:
                m["uri_variant"] = f"{m['uri_variant']}, without configured base {base_prefix}"
                m["confidence"] = "heuristic"
            alt["path"] = path
            return alt
        return res
    if "{?}" in path or path in ("", "/") or re.fullmatch(r"(/\{[^{}]*\})*", path or ""):
        return {"matched": [], "reason": "dynamic URL: path not statically resolvable", "path": path}
    if origin_kind == "other":
        return {"matched": [], "reason": f"not a backend URL: other origin {origin}", "path": path}
    if origin_kind == "same-origin" and not path.startswith("/api/"):
        return {"matched": [], "reason": "not a backend URL: same-origin relative URL (served by the frontend itself)", "path": path}
    hits, method_miss = [], []
    for r in routes:
        for variant, uri in r["uris"]:
            ok, info = match_path(path, uri)
            if not ok or info["lit"] == 0:
                continue  # at least one literal segment must agree (no "/x" ~ "/{param}" matches)
            if _method_ok(method, r["method"]):
                hits.append((r, variant, info))
            else:
                method_miss.append(r)
            break
    if not hits and origin_kind in ("unknown", "env"):
        sx = [h for h in suffix_candidates(path, routes) if _method_ok(method, h[0]["method"])]
        if len(sx) > 1:   # prefer the most specific alignment (literal over param, param over placeholder-into-literal)
            rank = lambda h: (h[2]["lit"], h[2]["param"], -h[2]["lit_into_param"], -h[2]["ph_into_lit"])
            best = max(rank(h) for h in sx)
            if sum(1 for h in sx if rank(h) == best) == 1:
                sx = [h for h in sx if rank(h) == best]
        if len(sx) == 1:
            r, variant, info = sx[0]
            return {"matched": [{"route": r["id"], "uri_variant": "suffix (origin unknown)", "confidence": "heuristic", "segments": info}], "path": path}
        if sx:
            return {"matched": [], "path": path, "reason": f"origin unknown and suffix match ambiguous ({len(sx)} routes)", "near": [h[0]["id"] for h in sx][:3]}
    if not hits:
        if method_miss:
            return {"matched": [], "path": path, "reason": f"method mismatch: path exists only as {sorted({m['method'] for m in method_miss})}",
                    "near": [m["id"] for m in method_miss][:3]}
        first = _segs(path)[:1]
        roots = {_segs(u)[0] for r in routes for _, u in r["uris"] if _segs(u)}
        if not first or first[0] not in roots:
            return {"matched": [], "path": path, "reason": "not a backend URL (no route shares its first segment: local asset / device bridge / external)"}
        best, bl = None, -1
        cs = _segs(path)
        for r in routes:
            us = _segs(r["uri"])
            k = 0
            while k < min(len(cs), len(us)) and (cs[k] == us[k] or PARAM.match(us[k]) or PARAM.match(cs[k])):
                k += 1
            if k > bl:
                best, bl = r, k
        return {"matched": [], "path": path, "reason": "no backend route matches this path",
                "closest": best["id"] if best else None, "common_prefix_segments": bl}
    # most specific: fewest placeholder->literal fits, then most literal==literal segments
    key = lambda h: (-h[2]["ph_into_lit"], h[2]["lit"])
    top = max(key(h) for h in hits)
    best = [h for h in hits if key(h) == top]
    out = []
    for r, variant, info in best:
        conf = "exact"
        if info["lit_into_param"]:
            conf = "resolved"
        if info["ph_into_lit"]:
            conf = "heuristic"
        # several routes equally specific -> ambiguous, unless the placeholder fans out over literals (export.{format})
        if len(best) > 1 and not info["ph_into_lit"]:
            conf = "heuristic"
        if origin_kind == "unknown":
            conf = "heuristic"  # base not traced to the API client config
        elif origin_kind == "env" and conf == "exact":
            conf = "resolved"  # base is a configured server URL (env key) whose value is not in the repo
        out.append({"route": r["id"], "uri_variant": variant, "confidence": conf, "segments": info})
    return {"matched": out, "path": path}


def tag_entries_rows(nodes: list, edges: list, prop: set, skip_gate: str | None = None):
    from .indexer import tag_entries
    b = SimpleNamespace(nodes={n.id: n for n in nodes}, edges={i: e for i, e in enumerate(edges) if e.kind in prop})
    return tag_entries(b, skip_gate=skip_gate)


_ROLES = ("backend", "frontend", "both")
# These ids name one logical thing across repos (one database, one protocol endpoint). Other collisions
# (two classes, two routes, two client calls with the same id) are stored as `<repo>:<id>`.
_SHARED_ID_KINDS = ("external", "endpoint")


def _id_kind(nid: str) -> str:
    return nid.split(":", 1)[0]


def _is_server(role: str) -> bool:
    return role in ("backend", "both")


def _is_client(role: str, mode: str) -> bool:
    """`frontend` mode (the two-repo `link`) matches frontend and both.
    `all` matches every repo, so a backend can call another backend."""
    return role in ("frontend", "both") if mode == "frontend" else True


def link(backend_db: str, frontend_db: str, out_db: str, backend_name="backend", frontend_name="frontend") -> dict:
    """Two-repo shorthand. Same ids and edges as link_many when no node id is shared."""
    return link_many(
        [(backend_name, backend_db, "backend"), (frontend_name, frontend_db, "frontend")],
        out_db, clients="frontend")


def link_many(repos: list[tuple[str, str, str]], out_db: str, *, allow: dict | None = None,
              clients: str = "all") -> dict:
    """Merge N graphs into `out_db`. `repos` is `(name, db_path, role)` with role backend, frontend or both.
    `allow` maps a client repo to the server names it may call (others call every other server).
    `clients` is `all` (every repo's http nodes) or `frontend` (frontend role only)."""
    if clients not in ("all", "frontend"):
        raise ValueError(f"clients must be 'all' or 'frontend', got {clients!r}")
    for name, _path, role in repos:
        if not name:
            raise ValueError("repo names must be unique and non-empty")
        if role not in _ROLES:
            raise ValueError(f"role {role!r} for {name} must be backend, frontend or both")
    # The same checkout linked to itself (one DB, one name, both roles) keeps a single copy of each id
    # and matches its own HTTP calls to its own routes.
    folded_self = False
    if len({n for n, _, _ in repos}) != len(repos):
        merged: dict[str, list] = {}
        order: list[str] = []
        for name, path, role in repos:
            resolved = str(Path(path).resolve())
            prev = merged.get(name)
            if prev is None:
                merged[name] = [resolved, role]
                order.append(name)
            elif prev[0] != resolved:
                raise ValueError("repo names must be unique and non-empty")
            elif prev[1] != role:
                prev[1] = "both"
                folded_self = True
        repos = [(n, merged[n][0], merged[n][1]) for n in order]
    names = [n for n, _, _ in repos]
    t0 = time.time()
    store = GraphStore.create(out_db)
    db = store.db
    server_names = [n for n, _, role in repos if _is_server(role)]
    client_names = [n for n, _, role in repos if _is_client(role, clients)]
    stats = {
        "backend": server_names[0] if len(server_names) == 1 else ",".join(server_names),
        "frontend": client_names[0] if len(client_names) == 1 else ",".join(client_names),
    }
    allow = allow or {}

    def q(sql, params=()):
        cur = db.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    per_ids: dict[str, list] = {}
    externals: list = []
    for name, path, _role in repos:
        db.execute("ATTACH DATABASE ? AS src", (str(path),))
        try:
            per_ids[name] = [r[0] for r in q("SELECT id FROM src.nodes")]
            try:
                got = q("SELECT value FROM src.meta WHERE key='stats'")
            except sqlite3.OperationalError:
                got = []
            if got:
                externals += ((json.loads(got[0][0]).get("config") or {}).get("protocols") or {}).get("external") or []
        finally:
            db.commit()
            db.execute("DETACH DATABASE src")
    collided = {i for i, n in Counter(i for ids in per_ids.values() for i in set(ids)).items() if n > 1}
    collisions = {i for i in collided if _id_kind(i) not in _SHARED_ID_KINDS}
    db.execute("CREATE TEMP TABLE cg_id_collisions(id TEXT PRIMARY KEY)")
    if collisions:
        db.executemany("INSERT INTO cg_id_collisions VALUES (?)", [(i,) for i in collisions])

    def mid(repo: str, nid: str) -> str:
        return f"{repo}:{nid}" if nid in collisions else nid

    routes: list[dict] = []
    http_rows: list[tuple] = []          # (client, id, attrs, test_only)
    calls_by_ep: dict = defaultdict(list)
    fe_classes: dict[str, dict] = {}
    be_classes: dict[str, dict] = {}
    chans, subs, events, on = [], [], {}, defaultdict(set)
    scen: list = []
    for name, path, role in repos:
        db.execute("ATTACH DATABASE ? AS src", (str(path),))
        try:
            q("""INSERT OR IGNORE INTO nodes
                SELECT CASE WHEN id IN (SELECT id FROM cg_id_collisions) THEN ? || ':' || id ELSE id END,
                       kind, name, fqn, CASE WHEN file IS NULL THEN NULL ELSE ? || '/' || file END,
                       line, end_line, module, doc, lang, entry_kind,
                       json_set(coalesce(attrs, '{}'), '$.repo', ?)
                FROM src.nodes""", (name, name, name))
            q("""INSERT INTO edges(src,dst,kind,file,line,confidence,conf_rank,attrs,gate)
                SELECT CASE WHEN src IN (SELECT id FROM cg_id_collisions) THEN ? || ':' || src ELSE src END,
                       CASE WHEN dst IN (SELECT id FROM cg_id_collisions) THEN ? || ':' || dst ELSE dst END,
                       kind, CASE WHEN file IS NULL THEN NULL ELSE ? || '/' || file END,
                       line, confidence, conf_rank, attrs, gate
                FROM src.edges""", (name, name, name))
            stats[f"{name}_nodes"] = len(per_ids[name])
            stats[f"{name}_id_collisions"] = sum(1 for i in per_ids[name] if i in collided)
            stats[f"{name}_edges"] = q("SELECT count(*) FROM src.edges")[0][0]
            if _is_server(role):
                q("INSERT OR REPLACE INTO gate_predicates SELECT * FROM src.gate_predicates")
                scen += [r[0] for r in q("SELECT DISTINCT scenario FROM src.node_entry_live")]
                be_classes[name] = {r[0]: json.loads(r[1] or "{}") for r in q(
                    "SELECT id, attrs FROM src.nodes WHERE kind='class' AND attrs LIKE '%schema_fields%'")}
                for r in q("SELECT id, file, attrs, line FROM src.nodes WHERE kind='route'"):
                    a = json.loads(r[2] or "{}")
                    uri, method = a.get("uri"), a.get("method")
                    if not uri or not method:
                        continue
                    uris = [("as-declared", uri)]
                    if (r[1] or "").startswith("routes/api.php") or (r[1] or "").startswith("routes/api/"):
                        uris.append(("api-prefixed", "/api" + uri))
                    routes.append({"id": mid(name, r[0]), "uri": uri, "method": method, "uris": uris, "server": name,
                                   "file": f"{name}/{r[1]}" if r[1] else None, "line": r[3],
                                   "attrs": dict(a, _file=r[1], _line=r[3])})
                for r in q("SELECT id, attrs, file, line FROM src.nodes WHERE kind='channel'"):
                    chans.append((mid(name, r[0]), json.loads(r[1] or "{}"), f"{name}/{r[2]}" if r[2] else None, r[3]))
                for r in q("SELECT id, attrs FROM src.nodes WHERE kind='event'"):
                    a = json.loads(r[1] or "{}")
                    if a.get("broadcast"):
                        events[mid(name, r[0])] = a
                for r in q("SELECT src, dst FROM src.edges WHERE kind='BROADCASTS_ON'"):
                    on[mid(name, r[1])].add(mid(name, r[0]))
            if _is_client(role, clients):
                fe_classes[name] = {r[0]: json.loads(r[1] or "{}") for r in q(
                    "SELECT id, attrs FROM src.nodes WHERE kind='class' AND (attrs LIKE '%json_from%' OR attrs LIKE '%enum_values%' OR attrs LIKE '%json_to%')")}
                for r in q("SELECT id, attrs FROM src.nodes WHERE kind='http'"):
                    a = json.loads(r[1] or "{}")
                    http_rows.append((name, mid(name, r[0]), a, a.get("test_only") in (1, True)))
                for e in q("SELECT src, dst, file, line, confidence, attrs FROM src.edges WHERE kind='HTTP_CALLS'"):
                    calls_by_ep[mid(name, e[1])].append({"src": mid(name, e[0]), "at": f"{name}/{e[2]}:{e[3]}",
                                                        "confidence": e[4], **json.loads(e[5] or "{}")})
                for r in q("SELECT id, attrs FROM src.nodes WHERE kind='channel_sub'"):
                    subs.append((mid(name, r[0]), json.loads(r[1] or "{}")))
        finally:
            db.commit()
            db.execute("DETACH DATABASE src")
    db.commit()
    # ---- match each client's endpoints against every other allowed server's routes
    from .plugins.ts.baseurl import base_path
    by_server: dict[str, list] = defaultdict(list)
    for rt in routes:
        by_server[rt["server"]].append(rt)
    results = []
    new_edges = []
    for client, ep_id, a, _test in http_rows:
        targets = [s for s in server_names if (s != client or folded_self) and (client not in allow or s in allow[client])]
        pool = [rt for s in targets for rt in by_server[s]]
        bp = base_path((a.get("base") or {}).get("value") or "") if a.get("base") else None
        res = match_endpoint(a["method"], a["path"], pool, a.get("origin_kind", "api"), a.get("origin"), base_prefix=bp or None)
        res.update({"endpoint": ep_id, "method": a["method"], "calls": calls_by_ep.get(ep_id, []),
                    "_client": client, "_servers": targets})
        results.append(res)
        rmap = {r["id"]: r for r in pool}
        for m in res["matched"]:
            rr = rmap[m["route"]]
            new_edges.append((ep_id, m["route"], "MATCHES_ROUTE", rr["file"], rr["line"], m["confidence"], CONFIDENCE_RANK[m["confidence"]],
                              json.dumps({"client_path": a["path"], "uri_variant": m["uri_variant"], "segments": m["segments"]}), None))
    db.executemany("INSERT INTO edges(src,dst,kind,file,line,confidence,conf_rank,attrs,gate) VALUES (?,?,?,?,?,?,?,?,?)", new_edges)
    db.commit()
    stats.update(link_channels(db, chans, subs, events, on))
    if externals:
        db.execute("INSERT OR REPLACE INTO meta VALUES ('stats', ?)", (json.dumps({"config": {"protocols": {"external": externals}}}),))
        db.commit()
    from .protocols import link_db as link_protocols
    if (pr := link_protocols(db)):      # only graphs with endpoints of the #31 protocols (MQTT, Socket.IO, ...)
        stats["protocols"] = pr
    test_only = {ep for _client, ep, _a, test in http_rows if test}
    test_results = [r for r in results if r["endpoint"] in test_only]
    results = [r for r in results if r["endpoint"] not in test_only]
    # ---- payload / field contract checks + routes without a client caller
    from .payload import Checker
    rmap = {r["id"]: r for r in routes}
    issues = []
    called = set()
    for res in results:
        for m in res["matched"]:
            called.add(m["route"])
        if len(res["matched"]) != 1:
            continue
        rr = rmap[res["matched"][0]["route"]]
        checker = Checker(fe_classes.get(res["_client"], {}), be_classes.get(rr["server"], {}), res["_client"], rr["server"])
        for c in res["calls"]:
            try:
                issues += checker.check(res["endpoint"], rr["id"], rr["attrs"], c)
            except Exception as ex:  # a malformed fact must not abort linking
                issues.append({"endpoint": res["endpoint"], "route": rr["id"], "kind": "checker_error", "severity": "info", "message": str(ex)[:200]})
    db.execute("""CREATE TABLE IF NOT EXISTS payload_checks (endpoint TEXT, route TEXT, kind TEXT, severity TEXT, message TEXT,
                  client_at TEXT, server_at TEXT, details TEXT)""")
    db.executemany("INSERT INTO payload_checks VALUES (?,?,?,?,?,?,?,?)",
                   [(i.get("endpoint"), i.get("route"), i["kind"], i["severity"], i["message"], i.get("client_at"), i.get("server_at"),
                     json.dumps({k: v for k, v in i.items() if k not in ("endpoint", "route", "kind", "severity", "message", "client_at", "server_at")}))
                    for i in issues])
    uncalled = [{"route": r["id"], "at": f"{r['file']}:{r['line']}", "framework": r["attrs"].get("framework"), "auth": r["attrs"].get("auth")}
                for r in routes if r["id"] not in called and r["attrs"].get("mounted", True) is not False
                and r["attrs"].get("framework") not in ("django-admin",)]
    db.commit()
    # ---- entry tagging over the union
    prop = {r[0] for r in db.execute("SELECT kind FROM edge_kinds WHERE propagates=1")}
    nodes = [SimpleNamespace(id=r[0], entry_kind=r[1]) for r in db.execute("SELECT id, entry_kind FROM nodes")]
    edges = [SimpleNamespace(src=r[0], dst=r[1], kind=r[2], gate=r[3]) for r in db.execute("SELECT src, dst, kind, gate FROM edges")]
    db.executemany("INSERT INTO node_entry VALUES (?,?,?,?)", tag_entries_rows(nodes, edges, prop))
    for sc in scen[:1]:
        db.executemany("INSERT INTO node_entry_live VALUES (?,?,?,?,?)", [(sc, *r) for r in tag_entries_rows(nodes, edges, prop, skip_gate=sc)])
    db.commit()
    # ---- stats: per call site and per distinct endpoint
    n_ep = len(results)
    m_ep = sum(1 for r in results if r["matched"])
    site_match = {}
    for r in results:
        for c in r["calls"]:
            site_match[c["at"]] = site_match.get(c["at"], False) or bool(r["matched"])
    sites = list(site_match)
    m_sites = sum(1 for v in site_match.values() if v)
    conf_ct = defaultdict(int)
    for r in results:
        if r["matched"]:
            conf_ct[min((m["confidence"] for m in r["matched"]), key=lambda c: CONFIDENCE_RANK[c])] += 1
    reasons = defaultdict(int)
    for r in results:
        if not r["matched"]:
            reasons[r["reason"].split(":")[0].split(" (")[0]] += 1
    stats.update({"test_endpoints": len(test_results), "test_endpoints_matched": sum(1 for r in test_results if r["matched"])})
    pair_stats = []
    for client in client_names:
        for server in server_names:
            if (server == client and not folded_self) or (client in allow and server not in allow[client]):
                continue
            eps = [r for r in results if r["_client"] == client and server in r["_servers"]]
            sm = {}
            for r in eps:
                hit = any(rmap[m["route"]]["server"] == server for m in r["matched"])
                for c in r["calls"]:
                    sm[c["at"]] = sm.get(c["at"], False) or hit
            pair_stats.append({"client": client, "server": server, "endpoints": len(eps),
                               "endpoints_matched": sum(1 for r in eps if any(rmap[m["route"]]["server"] == server for m in r["matched"])),
                               "call_sites": len(sm), "call_sites_matched": sum(1 for v in sm.values() if v)})
    for r in results:
        r.pop("_client", None)
        r.pop("_servers", None)
    stats.update({"endpoints": n_ep, "endpoints_matched": m_ep, "call_sites": len(sites), "call_sites_matched": m_sites,
                  "endpoint_match_confidence": dict(conf_ct), "unmatched_reasons": dict(reasons),
                  "ambiguous_endpoints": sum(1 for r in results if len(r["matched"]) > 1),
                  "payload_issues": dict(sorted({k: sum(1 for i in issues if i["severity"] == k) for k in ("high", "medium", "low", "info")}.items())),
                  "routes_without_client_call": len(uncalled), "pairs": pair_stats,
                  "link_seconds": round(time.time() - t0, 2)})
    from .coverage import for_graph
    cov = {}
    for name, path, _role in repos:
        got = for_graph(GraphStore(path))
        cov.update({name: next(iter(got.values()))} if len(got) == 1 else got)
    store.set_meta(project="+".join(names), stats=stats, repos=names, coverage=cov,
                   sources={name: str(Path(path).resolve()) for name, path, _role in repos},
                   repo_roles={name: role for name, _path, role in repos}, link_clients=clients, link_allow=allow,
                   indexed_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    db.close()
    return {"stats": stats, "results": results, "payload_issues": issues, "uncalled_routes": uncalled}


def _kind_key(nid: str, kind: str) -> str:
    """Key after the last `kind:` marker, so a repo-prefixed id `repo:channel:name` still yields `name`."""
    mark = kind + ":"
    i = nid.rfind(mark)
    return nid[i + len(mark):] if i >= 0 else nid


def channel_links(chans, subs, events, on):
    """Pure matching shared by cg link and single-repo indexing (backend + Echo client in one checkout).
    chans: [(id, attrs, file, line)], subs: [(id, attrs)], events: {event id: attrs}, on: {channel id: {event ids}}.
    Returns (edges [(src, dst, kind, confidence, attrs, file, line)], stats)."""
    from .plugins.php.strings import channel_match, same_shape
    rows, st = [], {"channel_subscriptions": len(subs), "channel_subscriptions_matched": 0, "listened_events": 0,
                    "listened_events_matched": 0}
    for sid, a in subs:
        name = a.get("name") or _kind_key(sid, "channel_sub")
        exact = [c for c in chans if same_shape(name, c[1].get("pattern") or _kind_key(c[0], "channel"))]
        hits = exact or [c for c in chans if channel_match(name, c[1].get("pattern") or _kind_key(c[0], "channel"))]
        conf = "exact" if exact and len(exact) == 1 else ("resolved" if len(hits) == 1 else "heuristic")
        for cid, ca, cf, cl in hits:
            ea = {"client_name": name}
            cv, sv = ca.get("visibility"), a.get("visibility")
            if cv and sv and cv != sv and not (cv == "private" and sv == "private-encrypted"):
                ea["visibility_mismatch"] = f"client subscribes as {sv}, backend channel is {cv}"
            rows.append((sid, cid, "MATCHES_CHANNEL", conf, ea, cf, cl))
        if hits:
            st["channel_subscriptions_matched"] += 1
        # events: .listen('OrderShipped') -> App\\Events\\OrderShipped; .listen('.custom') -> broadcastAs() == 'custom'
        near = set().union(*(on.get(c[0], set()) for c in hits)) if hits else set()
        for ev in a.get("events") or []:
            if ev == "*" or ev.startswith(("(", "whisper:", "client-", "pusher:")):
                continue
            st["listened_events"] += 1
            if ev.startswith("."):
                cands = [e for e, ea in events.items() if ea.get("broadcast_as") == ev[1:]]
            else:
                fq = ev.replace(".", "\\")
                cands = [e for e in events if e == f"event:{fq}" or e == f"event:App\\Events\\{fq}"]
                if not cands:
                    cands = [e for e, ea in events.items() if not ea.get("broadcast_as") and e.rsplit("\\", 1)[-1] == ev.rsplit(".", 1)[-1].rsplit("\\", 1)[-1]]
                if not cands:   # broadcastAs() names used without the leading dot (pusher-js bind / useEcho)
                    cands = [e for e, ea in events.items() if ea.get("broadcast_as") == ev]
            pref = [e for e in cands if e in near] or cands
            for e in pref:
                c = "exact" if e in near and len(pref) == 1 else "resolved" if len(pref) == 1 else "heuristic"
                rows.append((sid, e, "LISTENS_FOR", c, {"event_name": ev}, None, None))
            if pref:
                st["listened_events_matched"] += 1
    return rows, st


def link_channels(db, chans, subs, events, on) -> dict:
    """MATCHES_CHANNEL / LISTENS_FOR over already-copied channel rows (ids mapped, file repo-prefixed)."""
    if not subs:
        return {}
    rows, st = channel_links(chans, subs, events, on)
    db.executemany("INSERT INTO edges(src,dst,kind,file,line,confidence,conf_rank,attrs,gate) VALUES (?,?,?,?,?,?,?,?,?)",
                   [(s, d, k, f, l, c, CONFIDENCE_RANK[c], json.dumps(a), None)
                    for s, d, k, c, a, f, l in rows])
    db.commit()
    return st


def write_match_report(res: dict, out_prefix: str) -> None:
    Path(out_prefix + ".json").write_text(json.dumps(res, indent=1, default=str))
    s = res["stats"]
    pairs = s.get("pairs") or []
    head = "; ".join(f"{p['client']} -> {p['server']}" for p in pairs) if len(pairs) > 1 else f"{s['frontend']} -> {s['backend']}"
    L = [f"# API-call matching: {head}", "",
         f"- call sites: {s['call_sites_matched']}/{s['call_sites']} matched "
         f"({100 * s['call_sites_matched'] / max(1, s['call_sites']):.1f}%)",
         f"- distinct client endpoints: {s['endpoints_matched']}/{s['endpoints']} matched "
         f"({100 * s['endpoints_matched'] / max(1, s['endpoints']):.1f}%); ambiguous: {s['ambiguous_endpoints']}",
         f"- match confidence (per endpoint, weakest route): {s['endpoint_match_confidence']}",
         f"- unmatched by reason: {s['unmatched_reasons']}", "", "## Unmatched endpoints", ""]
    for r in sorted((r for r in res["results"] if not r["matched"]), key=lambda r: (r["reason"], r["endpoint"])):
        at = ", ".join(sorted({c["at"] for c in r["calls"]})[:3])
        extra = f" closest={r['closest']}" if r.get("closest") else (f" near={r['near']}" if r.get("near") else "")
        L.append(f"- `{r['endpoint']}` - {r['reason']}{extra} - at {at}")
    L += ["", "## Ambiguous / heuristic matches", ""]
    for r in res["results"]:
        if r["matched"] and (len(r["matched"]) > 1 or any(m["confidence"] == "heuristic" for m in r["matched"])):
            L.append(f"- `{r['endpoint']}` -> " + ", ".join(f"`{m['route']}` ({m['confidence']})" for m in r["matched"]))
    L += ["", "## Payload / contract issues", ""]
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    for i in sorted(res.get("payload_issues") or [], key=lambda i: (order.get(i["severity"], 9), i["kind"], i.get("endpoint") or "")):
        ev = ", ".join(x for x in (i.get("client_field") or i.get("client_at"), i.get("server_field") or i.get("server_at")) if x)
        L.append(f"- **{i['severity']}** `{i['kind']}` `{i.get('endpoint')}` -> `{i.get('route')}`: {i['message']} ({ev})")
    L += ["", "## Backend routes without a client call", ""]
    for u in res.get("uncalled_routes") or []:
        L.append(f"- `{u['route']}` ({u.get('framework')}, auth={u.get('auth')}) at {u['at']}")
    Path(out_prefix + ".md").write_text("\n".join(L) + "\n")

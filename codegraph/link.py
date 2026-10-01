"""Cross-repo linking: client HTTP endpoints (frontend graph) -> backend Route nodes.

link(backend_db, frontend_db, out_db) builds ONE combined SQLite graph:
  * nodes/edges of both graphs (ids are kept; `file` is prefixed with the repo name and
    attrs.repo is set, so evidence stays unambiguous),
  * MATCHES_ROUTE edges  http:<METHOD> <client path>  ->  route:<METHOD> <uri>,
  * entry tagging recomputed over the union (frontend pages become entry points that reach
    backend tables), plus the live-under-gate closure for the backend's gate scenario.

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
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict
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


def match_endpoint(method: str, path: str, routes: list[dict], origin_kind: str = "api", origin: str | None = None) -> dict:
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


def link(backend_db: str, frontend_db: str, out_db: str, backend_name="backend", frontend_name="frontend") -> dict:
    t0 = time.time()
    store = GraphStore.create(out_db)
    db = store.db
    stats = {"backend": backend_name, "frontend": frontend_name}
    for alias, path, repo in (("b", backend_db, backend_name), ("f", frontend_db, frontend_name)):
        db.execute(f"ATTACH DATABASE ? AS {alias}", (str(path),))
        before = db.execute("SELECT count(*) FROM nodes").fetchone()[0]
        db.execute(f"""INSERT OR IGNORE INTO nodes
            SELECT id, kind, name, fqn, CASE WHEN file IS NULL THEN NULL ELSE ? || '/' || file END, line, end_line, module, doc, lang,
                   entry_kind, json_set(coalesce(attrs, '{{}}'), '$.repo', ?) FROM {alias}.nodes""", (repo, repo))
        n_in = db.execute(f"SELECT count(*) FROM {alias}.nodes").fetchone()[0]
        added = db.execute("SELECT count(*) FROM nodes").fetchone()[0] - before
        stats[f"{repo}_nodes"] = n_in
        stats[f"{repo}_id_collisions"] = n_in - added
        db.execute(f"""INSERT INTO edges(src,dst,kind,file,line,confidence,conf_rank,attrs,gate)
            SELECT src, dst, kind, CASE WHEN file IS NULL THEN NULL ELSE ? || '/' || file END, line, confidence, conf_rank, attrs, gate
            FROM {alias}.edges""", (repo,))
        stats[f"{repo}_edges"] = db.execute(f"SELECT count(*) FROM {alias}.edges").fetchone()[0]
    db.execute("INSERT OR REPLACE INTO gate_predicates SELECT * FROM b.gate_predicates")
    db.commit()
    # ---- match client endpoints to routes
    routes = []
    for r in db.execute("SELECT id, file, attrs, line FROM b.nodes WHERE kind='route'"):
        a = json.loads(r[2] or "{}")
        uri, method = a.get("uri"), a.get("method")
        if not uri or not method:
            continue
        uris = [("as-declared", uri)]
        if (r[1] or "").startswith("routes/api.php") or (r[1] or "").startswith("routes/api/"):
            uris.append(("api-prefixed", "/api" + uri))
        routes.append({"id": r[0], "uri": uri, "method": method, "uris": uris, "file": f"{backend_name}/{r[1]}" if r[1] else None, "line": r[3],
                       "attrs": dict(a, _file=r[1], _line=r[3])})
    calls_by_ep = defaultdict(list)
    for e in db.execute("SELECT src, dst, file, line, confidence, attrs FROM f.edges WHERE kind='HTTP_CALLS'"):
        calls_by_ep[e[1]].append({"src": e[0], "at": f"{frontend_name}/{e[2]}:{e[3]}", "confidence": e[4], **json.loads(e[5] or "{}")})
    results = []
    new_edges = []
    for ep_id, attrs in db.execute("SELECT id, attrs FROM f.nodes WHERE kind='http'"):
        a = json.loads(attrs or "{}")
        res = match_endpoint(a["method"], a["path"], routes, a.get("origin_kind", "api"), a.get("origin"))
        res.update({"endpoint": ep_id, "method": a["method"], "calls": calls_by_ep.get(ep_id, [])})
        results.append(res)
        rmap = {r["id"]: r for r in routes}
        for m in res["matched"]:
            rr = rmap[m["route"]]
            new_edges.append((ep_id, m["route"], "MATCHES_ROUTE", rr["file"], rr["line"], m["confidence"], CONFIDENCE_RANK[m["confidence"]],
                              json.dumps({"client_path": a["path"], "uri_variant": m["uri_variant"], "segments": m["segments"]}), None))
    db.executemany("INSERT INTO edges(src,dst,kind,file,line,confidence,conf_rank,attrs,gate) VALUES (?,?,?,?,?,?,?,?,?)", new_edges)
    db.commit()
    # ---- payload / field contract checks + routes without a client caller
    from .payload import Checker
    fe_nodes = {r[0]: json.loads(r[1] or "{}") for r in db.execute(
        "SELECT id, attrs FROM f.nodes WHERE kind='class' AND (attrs LIKE '%json_from%' OR attrs LIKE '%enum_values%' OR attrs LIKE '%json_to%')")}
    be_nodes = {r[0]: json.loads(r[1] or "{}") for r in db.execute("SELECT id, attrs FROM b.nodes WHERE kind='class' AND attrs LIKE '%schema_fields%'")}
    checker = Checker(fe_nodes, be_nodes, frontend_name, backend_name)
    rmap = {r["id"]: r for r in routes}
    issues = []
    called = set()
    for res in results:
        for m in res["matched"]:
            called.add(m["route"])
        if len(res["matched"]) != 1:
            continue
        rr = rmap[res["matched"][0]["route"]]
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
    scen = [r[0] for r in db.execute("SELECT DISTINCT scenario FROM b.node_entry_live")]
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
    stats.update({"endpoints": n_ep, "endpoints_matched": m_ep, "call_sites": len(sites), "call_sites_matched": m_sites,
                  "endpoint_match_confidence": dict(conf_ct), "unmatched_reasons": dict(reasons),
                  "ambiguous_endpoints": sum(1 for r in results if len(r["matched"]) > 1),
                  "payload_issues": dict(sorted({k: sum(1 for i in issues if i["severity"] == k) for k in ("high", "medium", "low", "info")}.items())),
                  "routes_without_client_call": len(uncalled),
                  "link_seconds": round(time.time() - t0, 2)})
    db.execute("DETACH DATABASE b")
    db.execute("DETACH DATABASE f")
    store.set_meta(project=f"{backend_name}+{frontend_name}", stats=stats, repos=[backend_name, frontend_name],
                   sources={backend_name: str(Path(backend_db).resolve()), frontend_name: str(Path(frontend_db).resolve())},
                   indexed_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    db.close()
    return {"stats": stats, "results": results, "payload_issues": issues, "uncalled_routes": uncalled}


def write_match_report(res: dict, out_prefix: str) -> None:
    Path(out_prefix + ".json").write_text(json.dumps(res, indent=1, default=str))
    s = res["stats"]
    L = [f"# API-call matching: {s['frontend']} -> {s['backend']}", "",
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

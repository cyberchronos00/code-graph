"""Graph queries. Traversals are recursive CTEs over the SQLite edge table."""
from __future__ import annotations

import json
import re
from collections import defaultdict

from .core.model import CONFIDENCE_RANK, OPERATOR_ENTRY_KINDS, PROPAGATING, RUNTIME_ENTRY_KINDS, UI_ENTRY_KINDS
from .core.store import GraphStore

CALL_LIKE = ["CALLS", "IMPLEMENTED_BY", "OVERRIDDEN_BY", "BOUND_TO", "ROUTES_TO", "HANDLED_BY", "SCHEDULES",
             "DISPATCHES", "LISTENED_BY", "USES_MIDDLEWARE",
             # TypeScript / Vue / Nuxt + cross-repo link
             "USES_COMPOSABLE", "USES_STORE", "RENDERS", "HTTP_CALLS", "MATCHES_ROUTE"]
TS_CODE_KINDS = ("composable", "store", "component", "module")
CODE_KINDS = ("method", "function", "script") + TS_CODE_KINDS
ENTRY_NODE_KINDS = ("route", "command", "schedule", "job", "listener", "admin", "observer", "page", "layout", "app")


def resolve_targets(st: GraphStore, spec: str) -> list[str]:
    """spec: kind:key (glob * allowed) | table.column | Class::method | Class (short or FQN)
    | page:/route/path | a source file path (repo-relative, or repo/... in a combined DB)
    | TS symbol (useX, useX.fn, fn)."""
    if spec.startswith("page:/"):
        rows = st.q("SELECT id FROM nodes WHERE kind='page' AND json_extract(attrs,'$.route')=?", (spec[5:],))
        return [r["id"] for r in rows]
    if st.q("SELECT 1 FROM nodes WHERE id=? LIMIT 1", (spec,)):  # an exact node id (e.g. method:App\X::y)
        return [spec]
    if re.search(r"\.(vue|ts|tsx|js|mjs)$", spec) and "::" not in spec:
        rows = st.q("SELECT id FROM nodes WHERE kind IN ('page','component','layout','app','module') AND (file=? OR file LIKE ?)",
                    (spec, "%/" + spec))
        if rows:
            return [r["id"] for r in rows]
    if ":" in spec and not "::" in spec:
        kind, key = spec.split(":", 1)
        pat = f"{kind}:{key}".replace("*", "%")
        rows = st.q("SELECT id FROM nodes WHERE id LIKE ?", (pat,)) if "%" in pat else st.q("SELECT id FROM nodes WHERE id=?", (pat,))
        return [r["id"] for r in rows]
    if "::" not in spec and "." in spec and "\\" not in spec:
        rows = st.q("SELECT id FROM nodes WHERE id=?", (f"column:{spec}",))
        if rows:
            return [rows[0]["id"]]
        rows = st.q("SELECT id FROM nodes WHERE id=?", (f"config:{spec}",))
        if rows:
            return [r["id"] for r in rows]
    if re.fullmatch(r"[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*", spec) and "\\" not in spec:
        rows = st.q("""SELECT id FROM nodes WHERE lang='ts' AND kind IN ('function','composable','store','method','class','type')
                       AND (name=? OR name LIKE ?)""", (spec, "%." + spec))
        if rows:
            return [r["id"] for r in rows]
    spec = spec.lstrip("\\")
    if "::" in spec:
        rows = st.q("SELECT id FROM nodes WHERE kind='method' AND (fqn=? OR fqn LIKE ?)", (spec, "%\\" + spec))
        return [r["id"] for r in rows]
    rows = st.q("SELECT id, fqn FROM nodes WHERE kind IN ('class','interface','trait','enum') AND (fqn=? OR fqn LIKE ?)", (spec, "%\\" + spec))
    out = []
    for r in rows:
        out.append(r["id"])
        out += [x["id"] for x in st.q("SELECT id FROM nodes WHERE kind='method' AND fqn LIKE ?", (r["fqn"] + "::%",))]
    return out


def default_gate(st: GraphStore) -> str | None:
    """Gate scenario the DB was indexed with (first one), if any."""
    r = st.q("SELECT DISTINCT scenario FROM node_entry_live LIMIT 1")
    return r[0]["scenario"] if r else None


def reverse_closure(st: GraphStore, targets: list[str], kinds=None, min_conf="heuristic", max_depth=30,
                    exclude_gate: str | None = None) -> dict[str, int]:
    kinds = kinds or PROPAGATING
    if not targets:
        return {}
    st.db.execute("DROP TABLE IF EXISTS temp.t_targets")
    st.db.execute("CREATE TEMP TABLE t_targets(id TEXT PRIMARY KEY)")
    st.db.executemany("INSERT OR IGNORE INTO t_targets VALUES (?)", [(t,) for t in targets])
    kq = ",".join("?" * len(kinds))
    sql = f"""
    WITH RECURSIVE r(id, depth) AS (
        SELECT id, 0 FROM t_targets
        UNION
        SELECT e.src, r.depth + 1 FROM edges e JOIN r ON e.dst = r.id
        WHERE e.kind IN ({kq}) AND e.conf_rank >= ? AND r.depth < ? AND (e.gate IS NULL OR e.gate != ?)
    )
    SELECT id, MIN(depth) AS depth FROM r GROUP BY id"""
    rows = st.q(sql, (*kinds, CONFIDENCE_RANK[min_conf], max_depth, exclude_gate or "\x00"))
    return {r["id"]: r["depth"] for r in rows}


def shortest_paths(st: GraphStore, depth: dict[str, int], kinds=None, min_conf="heuristic", exclude_gate: str | None = None) -> dict[str, list[dict]]:
    """For each reached node pick an outgoing edge to a node one step closer to a target."""
    kinds = set(kinds or PROPAGATING)
    best = {}
    ids = list(depth)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for e in st.q(f"SELECT src,dst,kind,file,line,confidence,conf_rank,gate,attrs FROM edges WHERE src IN ({q})", chunk):
            if e["kind"] not in kinds or e["conf_rank"] < CONFIDENCE_RANK[min_conf]:
                continue
            if exclude_gate and e["gate"] == exclude_gate:
                continue
            s, d = e["src"], e["dst"]
            if d in depth and depth[d] == depth[s] - 1:
                cur = best.get(s)
                # prefer stronger confidence, then live (ungated) edges
                rank = (e["conf_rank"], e["gate"] is None)
                if cur is None or rank > (cur["conf_rank"], cur["gate"] is None):
                    best[s] = dict(e)
    paths = {}
    for n in depth:
        path, x, guard = [], n, 0
        while depth.get(x, 0) > 0 and x in best and guard < 60:
            e = best[x]
            hop = {"from": e["src"], "kind": e["kind"], "to": e["dst"], "at": f"{e['file']}:{e['line']}", "confidence": e["confidence"]}
            if e["gate"]:
                hop["gated"] = e["gate"]
                hop["guard"] = json.loads(e["attrs"] or "{}").get("guard")
            path.append(hop)
            x = e["dst"]
            guard += 1
        paths[n] = path
    return paths


def entry_info(st: GraphStore, ids: list[str], gate: str | None = None) -> dict[str, dict[str, tuple[int, str]]]:
    """Entry kinds reaching each node; with `gate`, only over edges live under that scenario."""
    out = defaultdict(dict)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        if gate:
            rows = st.q(f"SELECT * FROM node_entry_live WHERE scenario=? AND node_id IN ({q})", (gate, *chunk))
        else:
            rows = st.q(f"SELECT * FROM node_entry WHERE node_id IN ({q})", chunk)
        for r in rows:
            out[r["node_id"]][r["entry_kind"]] = (r["entry_count"], r["sample_entry"])
    return out


def nearest_gated_inbound(st: GraphStore, nid: str, gate: str, max_depth=6) -> dict | None:
    """Walk callers (reverse over propagating edges) until the first edge gated under `gate`."""
    kinds = set(PROPAGATING)
    seen, frontier = {nid}, [nid]
    for _ in range(max_depth):
        nxt = []
        for x in frontier:
            for e in st.q("SELECT src,dst,kind,file,line,gate,attrs FROM edges WHERE dst=?", (x,)):
                if e["kind"] not in kinds:
                    continue
                if e["gate"] == gate:
                    a = json.loads(e["attrs"] or "{}")
                    return {"from": e["src"], "kind": e["kind"], "to": e["dst"], "at": f"{e['file']}:{e['line']}",
                            "guard": a.get("guard"), "guard_expr": a.get("guard_expr")}
                if e["src"] not in seen:
                    seen.add(e["src"])
                    nxt.append(e["src"])
        frontier = nxt
    return None


def classify(kinds: dict) -> str:
    if any(k in kinds for k in RUNTIME_ENTRY_KINDS):
        return "runtime"
    if any(k in kinds for k in OPERATOR_ENTRY_KINDS):
        return "operator"
    if any(k in kinds for k in UI_ENTRY_KINDS):
        return "ui"
    if kinds:
        return "other_entry"
    return "no_entry"


def path_confidence(path: list[dict]) -> str:
    if not path:
        return "exact"
    return min((p["confidence"] for p in path), key=lambda c: CONFIDENCE_RANK[c])


def reaches(st: GraphStore, specs: list[str], min_conf="heuristic", max_depth=30, gate: str | None = "auto") -> dict:
    """Reverse transitive dependents of the targets.

    With a gate scenario (default: the one the DB was indexed with), every dependent also gets
    gate_status:
      live          - some entry point -> node -> target path has no edge that is dead under the scenario
      gated_target  - every path from the node to the target passes a gated edge
      gated_entry   - node reaches the target live, but every entry point reaches the node only through a gated edge
    """
    if gate == "auto":
        gate = default_gate(st)
    targets, resolved = [], {}
    for s in specs:
        t = resolve_targets(st, s)
        resolved[s] = t
        targets += t
    depth = reverse_closure(st, targets, min_conf=min_conf, max_depth=max_depth)
    paths = shortest_paths(st, depth, min_conf=min_conf)
    ids = list(depth)
    ents = entry_info(st, ids)
    live_depth = reverse_closure(st, targets, min_conf=min_conf, max_depth=max_depth, exclude_gate=gate) if gate else depth
    live_ents = entry_info(st, ids, gate=gate) if gate else ents
    live_paths = shortest_paths(st, live_depth, min_conf=min_conf, exclude_gate=gate) if gate else paths
    nodes = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in st.q(f"SELECT id,kind,name,fqn,file,line,module,entry_kind FROM nodes WHERE id IN ({q})", chunk):
            nodes[r["id"]] = dict(r)
    items = []
    for nid, d in depth.items():
        n = nodes.get(nid, {"id": nid, "kind": nid.split(":")[0]})
        ek = {k: v[0] for k, v in ents.get(nid, {}).items()}
        if n.get("entry_kind"):
            ek[n["entry_kind"]] = ek.get(n["entry_kind"], 0) or 1
        it = {**n, "depth": d, "entry_kinds": ek, "class": classify(ek),
              "path": paths.get(nid, []), "path_confidence": path_confidence(paths.get(nid, [])),
              "is_target": d == 0, "gate_status": "live"}
        if gate and d > 0:
            lek = {k: v[0] for k, v in live_ents.get(nid, {}).items()}
            if n.get("entry_kind"):
                lek[n["entry_kind"]] = lek.get(n["entry_kind"], 0) or 1
            it["live_entry_kinds"] = lek
            if nid not in live_depth:
                it["gate_status"] = "gated_target"
                it["gate_evidence"] = next(({k: h.get(k) for k in ("from", "kind", "to", "at", "guard")} for h in it["path"] if h.get("gated")), None)
            elif ek and not lek:
                it["gate_status"] = "gated_entry"
                it["gate_evidence"] = nearest_gated_inbound(st, nid, gate)
            elif lek:
                it["class"] = classify(lek)  # classify by entry points that still reach it live
                it["path"] = live_paths.get(nid, it["path"])
                it["depth"] = live_depth.get(nid, d)
                it["path_confidence"] = path_confidence(it["path"])
        items.append(it)
    items.sort(key=lambda x: (x["class"], x.get("module") or "", x.get("fqn") or x["id"]))
    return {"targets": resolved, "min_confidence": min_conf, "gate": gate, "items": items}


def fmt_path(path: list[dict]) -> str:
    if not path:
        return "(target)"
    s = path[0]["from"]
    for p in path:
        g = f" GATED:{p['gated']} guard {p.get('guard')}" if p.get("gated") else ""
        s += f"\n          -{p['kind']}[{p['confidence']} @ {p['at']}{g}]-> {p['to']}"
    return s


def render_reaches(res: dict, show_paths=True, kinds=CODE_KINDS + ENTRY_NODE_KINDS) -> str:
    out = []
    out.append("targets:")
    for s, t in res["targets"].items():
        out.append(f"  {s} -> {len(t)} node(s): {', '.join(t[:6])}{' ...' if len(t) > 6 else ''}")
    items = [i for i in res["items"] if not i["is_target"]]
    code = [i for i in items if i["kind"] in CODE_KINDS]
    entries = [i for i in items if i["kind"] in ENTRY_NODE_KINDS]
    other = [i for i in items if i["kind"] not in CODE_KINDS + ENTRY_NODE_KINDS]
    out.append(f"dependents: {len(items)} nodes  (code: {len(code)}, entry points: {len(entries)}, other: {len(other)}) min_confidence={res['min_confidence']}")
    labels = {"runtime": "RUNTIME (reached from http_route / scheduled / queue_job / listener)",
              "operator": "OPERATOR-ONLY (artisan_command / admin_panel; one-off import & provisioning)",
              "ui": "UI-ONLY (reached from frontend pages / layouts / app shell; no backend entry)",
              "other_entry": "OTHER ENTRY (observer only)", "no_entry": "NOT REACHED FROM ANY INDEXED ENTRY POINT"}
    gated = [i for i in code if i.get("gate_status", "live") != "live" and i["class"] != "no_entry"]
    code = [i for i in code if i not in gated]
    for cls in ("runtime", "operator", "ui", "other_entry", "no_entry"):
        group = [i for i in code if i["class"] == cls]
        if not group:
            continue
        out.append("")
        out.append(f"== {labels[cls]}: {len(group)} functions/methods")
        bymod = defaultdict(list)
        for i in group:
            bymod[i.get("module") or "?"].append(i)
        for mod in sorted(bymod):
            out.append(f"  [{mod}]")
            for i in bymod[mod]:
                ek = ", ".join(f"{k}({v})" for k, v in sorted(i["entry_kinds"].items()))
                out.append(f"    {i.get('fqn') or i['id']}  depth={i['depth']} conf={i['path_confidence']}  {ek}"
                           + (f"  [{i['kind']} @ {i.get('file')}]" if i['kind'] in TS_CODE_KINDS or (i.get('file') or '').endswith(('.ts', '.vue')) else ""))
                if show_paths:
                    out.append(f"        path: {fmt_path(i['path'])}")
    if gated:
        out.append("")
        out.append(f"== GATED UNDER SCENARIO '{res.get('gate')}' (dead when the scenario holds; live otherwise): {len(gated)} functions/methods")
        bymod = defaultdict(list)
        for i in gated:
            bymod[i.get("module") or "?"].append(i)
        for mod in sorted(bymod):
            out.append(f"  [{mod}]")
            for i in bymod[mod]:
                ev = i.get("gate_evidence") or {}
                ek = ", ".join(f"{k}({v})" for k, v in sorted(i["entry_kinds"].items()))
                out.append(f"    {i.get('fqn') or i['id']}  {i['gate_status']}  entry-when-off: {ek}")
                out.append(f"        guard: {ev.get('guard')}  gated hop: {ev.get('kind')} @ {ev.get('at')} -> {ev.get('to')}")
    if entries:
        live_e = [i for i in entries if i.get("gate_status", "live") == "live"]
        out.append("")
        out.append(f"== ENTRY POINTS THAT REACH THE TARGET(S): {len(entries)} (live under gate: {len(live_e)})")
        byk = defaultdict(list)
        for i in entries:
            byk[i["kind"]].append(i)
        for k in sorted(byk):
            out.append(f"  {k}: {len(byk[k])}")
            for i in sorted(byk[k], key=lambda x: x["id"]):
                g = "" if i.get("gate_status", "live") == "live" else f"  [{i['gate_status']}]"
                out.append(f"    {i['name']}  ({i.get('file')}:{i.get('line')})  depth={i['depth']} conf={i['path_confidence']}{g}")
    return "\n".join(out)


def impact(st: GraphStore, spec: str, min_conf="heuristic") -> dict:
    targets = resolve_targets(st, spec)
    depth = reverse_closure(st, targets, kinds=CALL_LIKE, min_conf=min_conf)
    paths = shortest_paths(st, depth, kinds=CALL_LIKE, min_conf=min_conf)
    rows = {}
    ids = list(depth)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in st.q(f"SELECT id,kind,name,fqn,file,line,module,entry_kind FROM nodes WHERE id IN ({q})", chunk):
            rows[r["id"]] = dict(r)
    entries = [dict(rows[n], depth=d, path=paths[n], path_confidence=path_confidence(paths[n])) for n, d in depth.items()
               if n in rows and rows[n]["entry_kind"]]
    callers = [dict(rows[n], depth=d) for n, d in depth.items() if n in rows and rows[n]["kind"] in CODE_KINDS + ("http",) and d > 0]
    return {"targets": targets, "entry_points": sorted(entries, key=lambda x: (x["entry_kind"], x["name"])),
            "callers": sorted(callers, key=lambda x: (x["depth"], x["fqn"] or ""))}


def writers(st: GraphStore, table: str) -> list[dict]:
    rows = st.q("""SELECT e.src, e.kind, e.dst, e.file, e.line, e.confidence, n.module, n.fqn
                   FROM edges e JOIN nodes n ON n.id = e.src
                   WHERE (e.kind='WRITES_TABLE' AND e.dst=?) OR (e.kind='WRITES_COLUMN' AND e.dst LIKE ?)
                   ORDER BY n.module, n.fqn, e.line""", (f"table:{table}", f"column:{table}.%"))
    ents = entry_info(st, list({r["src"] for r in rows}))
    out = []
    for r in rows:
        d = dict(r)
        d["entry_kinds"] = {k: v[0] for k, v in ents.get(r["src"], {}).items()}
        out.append(d)
    return out


def siblings(st: GraphStore, spec: str, limit=40) -> dict:
    ids = resolve_targets(st, spec)
    res = {"targets": ids, "hierarchy": [], "same_method_in_siblings": [], "shared_resources": [], "co_callers": []}
    if not ids:
        return res
    main = ids[0]
    node = st.node(main)
    cls_fqn = node["fqn"].split("::")[0] if node["fqn"] else None
    cls_row = st.q("SELECT id FROM nodes WHERE fqn=? AND kind IN ('class','interface','trait','enum')", (cls_fqn,))
    if cls_row:
        cid = cls_row[0]["id"]
        for r in st.q("""SELECT e.kind, e.dst AS parent, s.src AS sibling FROM edges e
                         JOIN edges s ON s.dst = e.dst AND s.kind = e.kind AND s.src != e.src
                         WHERE e.src=? AND e.kind IN ('EXTENDS','IMPLEMENTS','USES_TRAIT')""", (cid,)):
            res["hierarchy"].append(dict(r))
        if node["kind"] == "method":
            sib_classes = {r["sibling"] for r in res["hierarchy"]}
            for sc in sib_classes:
                fq = sc.split(":", 1)[1]
                for m in st.q("SELECT id, file, line FROM nodes WHERE kind='method' AND fqn=?", (f"{fq}::{node['name']}",)):
                    res["same_method_in_siblings"].append(dict(m))
    if node["kind"] in ("method", "function"):
        res_kinds = ("READS_COLUMN", "WRITES_COLUMN", "MENTIONS_COLUMN", "READS_TABLE", "WRITES_TABLE", "READS_CONFIG",
                     "WRITES_CONFIG", "READS_ENV", "USES_CONNECTION", "REGISTERS_CONNECTION")
        kq = ",".join("?" * len(res_kinds))
        mine = {r["dst"] for r in st.q(f"SELECT DISTINCT dst FROM edges WHERE src=? AND kind IN ({kq})", (main, *res_kinds))}
        if mine:
            q = ",".join("?" * len(mine))
            agg = defaultdict(set)
            for r in st.q(f"SELECT src, dst FROM edges WHERE dst IN ({q}) AND kind IN ({kq}) AND src != ?", (*mine, *res_kinds, main)):
                agg[r["src"]].add(r["dst"])
            res["shared_resources"] = sorted(({"node": k, "shared": sorted(v)} for k, v in agg.items()), key=lambda x: -len(x["shared"]))[:limit]
        callees = {r["dst"] for r in st.q("SELECT DISTINCT dst FROM edges WHERE src=? AND kind='CALLS' AND dst LIKE 'method:%'", (main,))}
        callees = {c for c in callees if not c.startswith(f"method:{cls_fqn}::")}
        if callees:
            q = ",".join("?" * len(callees))
            agg = defaultdict(set)
            for r in st.q(f"SELECT src, dst FROM edges WHERE dst IN ({q}) AND kind='CALLS' AND src != ?", (*callees, main)):
                agg[r["src"]].add(r["dst"])
            res["co_callers"] = sorted(({"node": k, "shared_callees": sorted(v), "jaccard": round(len(v) / len(callees | v), 2)}
                                        for k, v in agg.items()), key=lambda x: (-len(x["shared_callees"]), x["node"]))[:limit]
    return res


DOWNSTREAM_SINKS = ("table", "column", "connection", "config", "env", "route", "http", "job", "command")


def downstream(st: GraphStore, spec: str, min_conf="heuristic", max_depth=30, kinds=None, sinks=DOWNSTREAM_SINKS,
               gate: str | None = "auto") -> dict:
    """Forward closure: everything the target depends on (e.g. a frontend page -> composables ->
    HTTP endpoints -> backend routes -> controllers/services -> tables). Returns reached sink nodes
    (tables, columns, routes, ...) with one shortest evidence path each. With a gate scenario,
    sinks reachable only through gated edges are flagged."""
    if gate == "auto":
        gate = default_gate(st)
    targets = resolve_targets(st, spec)
    kinds = kinds or PROPAGATING
    kset = set(kinds)
    rank = CONFIDENCE_RANK[min_conf]
    prev, depth = {}, {t: 0 for t in targets}
    frontier = list(targets)
    live = set(targets)
    while frontier:
        nxt = []
        chunk_all = frontier
        for i in range(0, len(chunk_all), 500):
            chunk = chunk_all[i:i + 500]
            q = ",".join("?" * len(chunk))
            for e in st.q(f"SELECT src,dst,kind,file,line,confidence,conf_rank,gate FROM edges WHERE src IN ({q})", chunk):
                if e["kind"] not in kset or e["conf_rank"] < rank:
                    continue
                d = e["dst"]
                if d not in depth and depth[e["src"]] < max_depth:
                    depth[d] = depth[e["src"]] + 1
                    prev[d] = dict(e)
                    nxt.append(d)
                if e["src"] in live and not (gate and e["gate"] == gate):
                    live.add(d)
        frontier = nxt
    # live set needs a fixpoint (edges seen before their src became live)
    changed = True
    while changed and gate:
        changed = False
        ids = list(live)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            for e in st.q(f"SELECT src,dst,kind,conf_rank,gate FROM edges WHERE src IN ({q})", chunk):
                if e["kind"] in kset and e["conf_rank"] >= rank and e["gate"] != gate and e["dst"] in depth and e["dst"] not in live:
                    live.add(e["dst"]); changed = True
    out = defaultdict(list)
    nodes = {}
    ids = [n for n in depth if n.split(":", 1)[0] in sinks]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in st.q(f"SELECT id,kind,name,file,line FROM nodes WHERE id IN ({q})", chunk):
            nodes[r["id"]] = dict(r)
    for nid in ids:
        path, x = [], nid
        while x in prev:
            e = prev[x]
            path.append({"from": e["src"], "kind": e["kind"], "to": e["dst"], "at": f"{e['file']}:{e['line']}", "confidence": e["confidence"],
                         **({"gated": e["gate"]} if e["gate"] else {})})
            x = e["src"]
        path.reverse()
        n = nodes.get(nid, {"id": nid, "kind": nid.split(":", 1)[0], "name": nid})
        out[n["kind"]].append({**n, "depth": depth[nid], "path": path, "path_confidence": path_confidence(path),
                               "live": (nid in live) if gate else True})
    for k in out:
        out[k].sort(key=lambda x: (x["depth"], x["id"]))
    # tables touched directly (READS/WRITES_TABLE) or through any of their columns
    touched = {}
    for c in out.get("column", []) + out.get("table", []):
        t = c["id"].split(":", 1)[1].split(".")[0] if c["kind"] == "column" else c["id"].split(":", 1)[1]
        cur = touched.get(t)
        if cur is None or c["depth"] < cur["depth"]:
            touched[t] = {"table": t, "depth": c["depth"], "via": c["id"], "live": c["live"], "path_confidence": c["path_confidence"]}
        elif c["live"]:
            cur["live"] = True
    return {"targets": targets, "gate": gate, "reached": len(depth), "sinks": dict(out),
            "tables_touched": sorted(touched.values(), key=lambda x: (x["depth"], x["table"]))}


def path_between(st: GraphStore, src_spec: str, dst_spec: str, min_conf="heuristic", max_depth=30) -> list[dict]:
    """Shortest forward dependency path from any node of src_spec to any node of dst_spec."""
    srcs, dsts = resolve_targets(st, src_spec), set(resolve_targets(st, dst_spec))
    kset, rank = set(PROPAGATING), CONFIDENCE_RANK[min_conf]
    prev, seen, frontier = {}, set(srcs), list(srcs)
    for _ in range(max_depth):
        nxt = []
        for i in range(0, len(frontier), 500):
            chunk = frontier[i:i + 500]
            q = ",".join("?" * len(chunk))
            for e in st.q(f"SELECT src,dst,kind,file,line,confidence,conf_rank,gate FROM edges WHERE src IN ({q})", chunk):
                if e["kind"] in kset and e["conf_rank"] >= rank and e["dst"] not in seen:
                    seen.add(e["dst"]); prev[e["dst"]] = dict(e); nxt.append(e["dst"])
                    if e["dst"] in dsts:
                        path, x = [], e["dst"]
                        while x in prev:
                            p = prev[x]
                            path.append({"from": p["src"], "kind": p["kind"], "to": p["dst"], "at": f"{p['file']}:{p['line']}",
                                         "confidence": p["confidence"], **({"gated": p["gate"]} if p["gate"] else {})})
                            x = p["src"]
                        return path[::-1]
        frontier = nxt
        if not frontier:
            break
    return []


def render_downstream(res: dict, show_paths=True, max_per_kind=60, kinds_order=("route", "table", "column", "connection", "config", "env", "job", "command", "http")) -> str:
    out = [f"targets: {', '.join(res['targets'][:6])}", f"reached nodes: {res['reached']}"]
    tt = res.get("tables_touched") or []
    if tt:
        out.append("")
        out.append(f"== TABLES TOUCHED (directly or via columns): {len(tt)}")
        out.append("  " + ", ".join(f"{t['table']}{'' if t['live'] else '[GATED-ONLY]'}" for t in tt))
    for k in kinds_order:
        items = res["sinks"].get(k) or []
        if not items:
            continue
        out.append("")
        out.append(f"== {k.upper()}: {len(items)}" + (f" (gated-only for '{res['gate']}': {sum(1 for i in items if not i['live'])})" if res.get("gate") else ""))
        for i in items[:max_per_kind]:
            g = "" if i["live"] else "  [GATED-ONLY]"
            out.append(f"  {i['id']}  depth={i['depth']} conf={i['path_confidence']}{g}")
            if show_paths:
                s = i["path"][0]["from"] if i["path"] else i["id"]
                for p in i["path"]:
                    s += f"\n          -{p['kind']}[{p['confidence']} @ {p['at']}{' GATED' if p.get('gated') else ''}]-> {p['to']}"
                out.append(f"      path: {s}")
        if len(items) > max_per_kind:
            out.append(f"  ... {len(items) - max_per_kind} more")
    return "\n".join(out)


def api_calls(st: GraphStore, flt: str = "all") -> list[dict]:
    """Client HTTP endpoints with call sites and matched backend routes (combined DB).
    flt: 'all' | 'unmatched' | substring of the endpoint/route/caller."""
    eps = {r["id"]: dict(r) for r in st.q("SELECT id, name, attrs FROM nodes WHERE kind='http'")}
    calls = defaultdict(list)
    for e in st.q("SELECT src, dst, file, line, confidence, attrs FROM edges WHERE kind='HTTP_CALLS'"):
        a = json.loads(e["attrs"] or "{}")
        calls[e["dst"]].append({"caller": e["src"], "at": f"{e['file']}:{e['line']}", "confidence": e["confidence"],
                                "url": a.get("url"), "via_helper": a.get("via_helper")})
    routes = defaultdict(list)
    for e in st.q("SELECT src, dst, confidence, attrs FROM edges WHERE kind='MATCHES_ROUTE'"):
        ctl = [r["dst"] for r in st.q("SELECT dst FROM edges WHERE src=? AND kind='ROUTES_TO'", (e["dst"],))]
        routes[e["src"]].append({"route": e["dst"], "confidence": e["confidence"], "controller": ctl,
                                 "uri_variant": json.loads(e["attrs"] or "{}").get("uri_variant")})
    out = []
    for nid, n in eps.items():
        row = {"endpoint": nid, "attrs": json.loads(n["attrs"] or "{}"), "calls": calls.get(nid, []), "routes": routes.get(nid, [])}
        if flt == "unmatched" and row["routes"]:
            continue
        if flt not in ("all", "unmatched") and flt not in json.dumps(row):
            continue
        out.append(row)
    return sorted(out, key=lambda r: r["endpoint"])


def render_api_calls(rows: list[dict], max_calls=4) -> str:
    out = [f"{len(rows)} client endpoints ({sum(1 for r in rows if r['routes'])} matched)"]
    for r in rows:
        out.append(f"{r['endpoint']}")
        for m in r["routes"]:
            out.append(f"   => {m['route']} [{m['confidence']}] -> {', '.join(m['controller']) or '?'}")
        if not r["routes"]:
            out.append("   => (unmatched)")
        for c in r["calls"][:max_calls]:
            h = f" via {c['via_helper']['fn']}" if c.get("via_helper") else ""
            out.append(f"   <- {c['caller']} @ {c['at']} [{c['confidence']}]{h}")
        if len(r["calls"]) > max_calls:
            out.append(f"   <- ... {len(r['calls']) - max_calls} more")
    return "\n".join(out)

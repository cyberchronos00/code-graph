"""Subgraph extraction for the visual view. Everything here is a projection of deterministic query results
(codegraph.query): a subgraph is the union of the evidence paths those queries already return, so every edge drawn
has a file:line and a confidence from the index."""
from __future__ import annotations

import json
from pathlib import Path

from .. import query as Q
from ..core.model import PROPAGATING
from ..core.store import GraphStore

NODE_COLS = "id,kind,name,fqn,file,line,module,entry_kind,lang,doc,attrs"


def repo_of(n: dict) -> str:
    try:
        r = json.loads(n.get("attrs") or "{}").get("repo")
    except (TypeError, ValueError):
        r = None
    if r:
        return r
    f = n.get("file") or ""
    return f.split("/", 1)[0] if "/" in f else ""


def side_of(n: dict) -> str:
    """'fe' for frontend (TS/Vue) nodes, 'be' otherwise; used only for box colours."""
    return "fe" if (n.get("lang") in ("ts", "vue", "js") or n.get("kind") in ("http", "page", "component", "composable", "store", "layout")) else "be"


def group_of(n: dict) -> tuple[str, str]:
    """(group id, label): code is grouped by module; columns/tables by table; the rest by kind."""
    k, repo = n["kind"], repo_of(n)
    if k in ("column", "table"):
        t = n["id"].split(":", 1)[1].split(".")[0]
        return f"table:{t}", f"table {t}"
    if k == "http":
        return f"{repo or 'frontend'}:http", f"{repo or 'frontend'} · HTTP calls"
    if k in ("setting", "request_key", "config", "env", "connection"):
        return f"{repo}:{k}", f"{repo} · {k}s"
    mod = n.get("module")
    if k == "route":
        mod = "routes · " + (mod or "?")
    if not mod:
        mod = k
    return f"{repo}:{mod}", f"{repo} · {mod}"


def load_nodes(st: GraphStore, ids) -> dict[str, dict]:
    ids, out = list(ids), {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in st.q(f"SELECT {NODE_COLS} FROM nodes WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            out[r["id"]] = dict(r)
    return out


def _edge_key(h):
    return (h["from"], h["to"], h["kind"])


def build(st: GraphStore, mode: str, specs: list[str], min_conf="heuristic", sinks: list[str] | None = None,
          max_nodes=1500) -> dict:
    """mode: reaches | impact | downstream | path. Returns {nodes, edges, groups, meta}."""
    gate = Q.default_gate(st)
    info: dict[str, dict] = {}
    hops: dict[tuple, dict] = {}
    targets: list[str] = []

    def add_path(path):
        for h in path:
            hops.setdefault(_edge_key(h), h)
            info.setdefault(h["from"], {}); info.setdefault(h["to"], {})

    if mode == "reaches":
        r = Q.reaches(st, specs, min_conf=min_conf)
        for spec, t in r["targets"].items():
            targets += t
        for it in r["items"]:
            info[it["id"]] = {"depth": it["depth"], "class": it["class"], "gate_status": it.get("gate_status", "live"),
                              "entry_kinds": it.get("entry_kinds") or {}, "live_entry_kinds": it.get("live_entry_kinds"),
                              "path_confidence": it["path_confidence"], "gate_evidence": it.get("gate_evidence")}
            add_path(it["path"])
    elif mode == "impact":
        for s in specs:
            targets += Q.resolve_targets(st, s)
        depth = Q.reverse_closure(st, targets, kinds=Q.CALL_LIKE, min_conf=min_conf)
        paths = Q.shortest_paths(st, depth, kinds=Q.CALL_LIKE, min_conf=min_conf)
        ents = Q.entry_info(st, list(depth))
        for nid, d in depth.items():
            info[nid] = {"depth": d, "entry_kinds": {k: v[0] for k, v in ents.get(nid, {}).items()}}
            add_path(paths.get(nid, []))
    elif mode == "downstream":
        kinds = tuple(sinks) if sinks else Q.DOWNSTREAM_SINKS
        for s in specs:
            r = Q.downstream(st, s, min_conf=min_conf, sinks=kinds)
            targets += r["targets"]
            for k, items in r["sinks"].items():
                for it in items:
                    info.setdefault(it["id"], {}).update({"depth": it["depth"], "sink": True, "live": it["live"],
                                                          "path_confidence": it["path_confidence"]})
                    add_path(it["path"])
            for t in r["targets"]:
                info.setdefault(t, {"depth": 0})
    elif mode == "path":
        if len(specs) < 2:
            raise ValueError("path needs a source and a target (optionally waypoints in between: src, via…, dst)")
        p, cur = [], specs[0]
        for nxt in specs[1:]:  # shortest forward path per segment; a waypoint pins which route/method the path takes
            seg = Q.path_between(st, cur, nxt, min_conf=min_conf)  # a table target falls back to its columns
            if not seg:
                p = []
                break
            p += seg
            cur = seg[-1]["to"]
        targets = [p[-1]["to"]] if p else []
        if p:
            info[p[0]["from"]] = {"depth": 0}
        add_path(p)
    else:
        raise ValueError(f"unknown mode {mode!r}")
    for t in targets:
        info.setdefault(t, {}).update({"is_target": True, "depth": info.get(t, {}).get("depth", 0)})

    truncated = False
    if len(info) > max_nodes:  # keep the closest nodes (paths stay connected: a path's prefix is always closer)
        keep = set(sorted(info, key=lambda n: (info[n].get("depth", 99), n))[:max_nodes])
        info = {k: v for k, v in info.items() if k in keep}
        hops = {k: v for k, v in hops.items() if k[0] in keep and k[1] in keep}
        truncated = True
    rows = load_nodes(st, info)
    nodes, groups = [], {}
    for nid, extra in info.items():
        n = rows.get(nid, {"id": nid, "kind": nid.split(":", 1)[0], "name": nid})
        gid, glabel = group_of(n)
        g = groups.setdefault(gid, {"id": gid, "label": glabel, "repo": repo_of(n), "side": side_of(n), "count": 0, "kinds": {}, "targets": 0})
        g["count"] += 1
        g["kinds"][n["kind"]] = g["kinds"].get(n["kind"], 0) + 1
        g["targets"] += bool(extra.get("is_target"))
        nodes.append({"id": nid, "kind": n["kind"], "name": n.get("name") or nid, "fqn": n.get("fqn"), "file": n.get("file"),
                      "line": n.get("line"), "module": n.get("module"), "entry_kind": n.get("entry_kind"), "repo": repo_of(n),
                      "group": gid, "has_doc": bool(n.get("doc")), **extra})
    edges = [{"src": h["from"], "dst": h["to"], "kind": h["kind"], "confidence": h["confidence"], "at": h.get("at"),
              "gated": h.get("gated"), "guard": h.get("guard")} for h in hops.values()]
    return {"nodes": nodes, "edges": edges, "groups": list(groups.values()),
            "meta": {"mode": mode, "specs": specs, "min_confidence": min_conf, "gate": gate, "targets": targets,
                     "truncated": truncated, "sinks": sinks}}


class Sources:
    """Read-only source snippets from the indexed repo roots (recorded in each source DB's meta)."""

    def __init__(self, st: GraphStore, extra: dict[str, str] | None = None):
        self.roots: dict[str, Path] = {}
        m = st.meta()
        for repo, dbp in (m.get("sources") or {}).items():
            try:
                root = GraphStore(dbp).meta().get("root")
            except Exception:  # noqa: BLE001
                root = None
            if root:
                self.roots[repo] = Path(root)
        if m.get("root"):
            self.roots.setdefault(m.get("project") or "", Path(m["root"]))
        for k, v in (extra or {}).items():
            self.roots[k] = Path(v)

    def snippet(self, file: str | None, line, ctx=4) -> dict | None:
        if not file or not line:
            return None
        repo, _, rel = file.partition("/")
        root = self.roots.get(repo)
        p = (root / rel) if root else None
        if p is None or not p.is_file():
            for r in self.roots.values():  # single-repo DB: file is repo-relative
                if (r / file).is_file():
                    p = r / file
                    break
            else:
                return None
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            return None
        ln = int(line)
        a, b = max(1, ln - ctx), min(len(lines), ln + ctx)
        return {"file": file, "line": ln, "start": a, "lines": lines[a - 1:b]}


def node_detail(st: GraphStore, src: Sources, nid: str, limit=25) -> dict | None:
    rows = load_nodes(st, [nid])
    if nid not in rows:
        return None
    n = rows[nid]
    n["attrs"] = json.loads(n.get("attrs") or "{}")
    ents = Q.entry_info(st, [nid]).get(nid, {})
    gate = Q.default_gate(st)
    live = Q.entry_info(st, [nid], gate=gate).get(nid, {}) if gate else None

    def edges(direction):
        col, other = ("src", "dst") if direction == "out" else ("dst", "src")
        rs = st.q(f"SELECT src,dst,kind,file,line,confidence,gate,attrs FROM edges WHERE {col}=? ORDER BY conf_rank DESC, kind LIMIT ?",
                  (nid, limit + 1))
        out = []
        for e in rs[:limit]:
            a = json.loads(e["attrs"] or "{}")
            out.append({"other": e[other], "kind": e["kind"], "at": f"{e['file']}:{e['line']}", "confidence": e["confidence"],
                        "gated": e["gate"], "propagates": e["kind"] in PROPAGATING,
                        "attrs": {k: v for k, v in a.items() if k in ("guard", "via", "default", "arg_keys", "flow", "order", "rule", "owner", "target")}})
        cnt = st.q(f"SELECT COUNT(*) c FROM edges WHERE {col}=?", (nid,))[0]["c"]
        return out, cnt

    out_e, n_out = edges("out")
    in_e, n_in = edges("in")
    return {**n, "entry_kinds": {k: v[0] for k, v in ents.items()}, "entry_samples": {k: v[1] for k, v in ents.items()},
            "live_entry_kinds": {k: v[0] for k, v in live.items()} if live is not None else None, "gate": gate,
            "snippet": src.snippet(n.get("file"), n.get("line")), "out_edges": out_e, "in_edges": in_e,
            "out_count": n_out, "in_count": n_in}


def search(st: GraphStore, q: str, kind: str | None = None, limit=30, fuzzy: bool = False) -> list[dict]:
    """Substring match on id / name / fqn; `fuzzy` adds names holding the query's characters in order (`mcget` ->
    MastodonClient.get), ranked after the substring hits."""
    like = f"%{q}%"
    cols = "id,kind,name,fqn,file,line,module,entry_kind"
    sql = (f"SELECT {cols} FROM nodes WHERE (id LIKE ? OR name LIKE ? OR fqn LIKE ?)"
           + (" AND kind=?" if kind else "") +
           " ORDER BY (name = ?) DESC, (kind IN ('page','route','method','table','column','connection')) DESC, length(id) LIMIT ?")
    args = [like, like, like] + ([kind] if kind else []) + [q, limit]
    out = [dict(r) for r in st.q(sql, args)]
    chars = [c for c in q if not c.isspace()]
    if fuzzy and len(out) < limit and len(chars) >= 3:
        pat = "%" + "%".join(c.replace("%", "").replace("_", "") for c in chars) + "%"
        have = {r["id"] for r in out}
        rows = st.q(f"SELECT {cols} FROM nodes WHERE (name LIKE ? OR fqn LIKE ?)" + (" AND kind=?" if kind else "") +
                    " ORDER BY length(coalesce(fqn, name)), id LIMIT ?", [pat, pat] + ([kind] if kind else []) + [limit * 3])
        out += [dict(r) for r in rows if r["id"] not in have][:limit - len(out)]
    return out


def stats(st: GraphStore) -> dict:
    """Landing-page overview: node / edge counts by kind, entry points by kind, edges by confidence."""
    m = st.meta()
    nk = {r["kind"]: r["c"] for r in st.q("SELECT kind, COUNT(*) c FROM nodes GROUP BY kind ORDER BY c DESC, kind")}
    ek = {r["kind"]: r["c"] for r in st.q("SELECT kind, COUNT(*) c FROM edges GROUP BY kind ORDER BY c DESC, kind")}
    ent = {r["entry_kind"]: r["c"] for r in st.q(
        "SELECT entry_kind, COUNT(*) c FROM nodes WHERE entry_kind IS NOT NULL AND entry_kind != '' GROUP BY entry_kind ORDER BY c DESC, entry_kind")}
    conf = {r["confidence"]: r["c"] for r in st.q("SELECT confidence, COUNT(*) c FROM edges GROUP BY confidence")}
    return {"project": m.get("project"), "indexed_at": m.get("indexed_at"), "nodes": sum(nk.values()), "edges": sum(ek.values()),
            "node_kinds": nk, "edge_kinds": ek, "entry_points": sum(ent.values()), "entry_kinds": ent,
            "confidence": {k: conf.get(k, 0) for k in ("exact", "resolved", "heuristic")}}


PSEUDO = {"client": ("ext:clients", "external clients (snapshot)"), "issue": ("ext:issues", "filed issues (snapshot)"),
          "file": None}


def _fold_col(nid: str) -> str:
    """Columns are drawn as their table in the plan overlay (except planned ones)."""
    if nid.startswith("column:"):
        return "table:" + nid[7:].split(".")[0]
    return nid


def build_plan(st: GraphStore, plan_name: str, plans_root=None, verify: bool = False, include_review: bool = True) -> dict:
    """Plan overlay: the planned change drawn on top of the real graph. Every real edge keeps its file:line and
    confidence; planned edges, forbidden paths and check relations are marked (`plan`: added | forbidden | gap |
    touches) so they can never be mistaken for indexed code edges."""
    from .. import plans as P
    plan = P.load_plan(plan_name, plans_root)
    res = P.check(st, plan, verify=verify, baseline=P.load_baseline(plan) if verify else None)
    info: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}

    rank = {"added": 6, "modified": 5, "missing": 4, "forbidden": 3, "review": 2, "covered": 1, "context": 0}

    def node(nid, **kw):
        cur = info.setdefault(nid, {})
        if kw.get("plan_check"):
            cur.setdefault("plan_items", []).append(f"[{kw.get('plan_role')}] {kw['plan_check']}: {kw.get('plan_why')}")
        if "plan_role" in kw and rank.get(kw["plan_role"], 0) < rank.get(cur.get("plan_role"), -1):
            return  # a stronger role (e.g. missing over review) already describes this node
        cur.update(kw)

    def edge(src, dst, kind, at=None, confidence="exact", plan=None, why=None):
        src, dst = (_fold_col(src) if src not in planned else src), (_fold_col(dst) if dst not in planned else dst)
        if src == dst:
            return
        node(src, plan_role="context"); node(dst, plan_role="context")
        k = (src, dst, kind)
        if k not in edges:
            edges[k] = {"src": src, "dst": dst, "kind": kind, "confidence": confidence, "at": at, "plan": plan, "why": why}

    planned = {it["id"] for it in plan.get("add_nodes") or []}
    for it in plan.get("add_nodes") or []:
        node(it["id"], plan_role="added", plan_why="planned new node", plan_attrs=it.get("attrs"))
        if it["id"].startswith("column:"):
            edge("table:" + it["id"][7:].split(".")[0], it["id"], "CONTAINS", plan="added", why="planned column")
    for it in plan.get("modify") or []:
        for nid in P.Ctx(st, plan).resolve(it["target"])["ids"]:
            node(nid, plan_role="modified", plan_why=it["intent"], plan_guard=it.get("role") == "guard")
    cx = P.Ctx(st, plan)
    for it in plan.get("add_edges") or []:
        for s in cx.resolve(it["from"])["ids"]:
            for d in cx.resolve(it["to"])["ids"]:
                ev = P.edge_evidence(cx, it)
                edge(s, d, it["kind"], at=", ".join(ev) or None, plan="added", why=it.get("intent") or "planned edge")
    for c in res["conflicts"]:
        for h in c.get("hops") or []:
            edge(h["from"], h["to"], h["kind"], h.get("at"), h.get("confidence", "exact"), plan="forbidden", why=f"forbidden: {c['id']}")
        if c.get("hops"):
            node(_fold_col(c["hops"][-1]["to"]), plan_role="forbidden", plan_why=f"forbidden target ({c['id']}): {c.get('when') or ''}")
    for r in res.get("require") or []:
        node(r["route"], plan_role="covered", plan_why=f"require {', '.join(r['required'])}: {'OK' if not r['missing'] else 'MISSING'}")
    for i in res["items"]:
        if i["severity"] == "review" and not include_review and not i["covered"]:
            continue
        role = "covered" if i["covered"] else i["severity"]
        nid = i["node"]
        node(nid, plan_role=role, plan_check=i["check"], plan_why=i["why"], plan_evidence=i["evidence"])
        if i.get("hops"):
            for h in i["hops"]:
                edge(h["from"], h["to"], h["kind"], h.get("at"), h.get("confidence", "exact"))
        elif i.get("anchor"):
            edge(nid, i["anchor"], "PLAN_" + i["check"].upper(), ", ".join(i["evidence"][:2]), "heuristic", plan="gap", why=i["why"])
    for f in res["findings"]:
        if not f["touches"] or f["state"] != "open":
            continue
        iid = f"issue:{f['id']}"
        node(iid, plan_role="context", plan_why=f"{f['title']} ({'linked in plan' if f['linked'] else 'NOT linked in plan'})", plan_url=f.get("url"),
             plan_linked=f["linked"])
        for nid, lines in f["touches"].items():
            if nid in info:
                edge(iid, nid, "TOUCHES", ",".join(lines), "exact", plan="touches", why=f["id"])
    rows = load_nodes(st, [n for n in info if not n.split(":", 1)[0] in ("client", "issue", "file")])
    nodes, groups = [], {}
    for nid, extra in info.items():
        k = nid.split(":", 1)[0]
        n = rows.get(nid)
        if n is None:
            if k == "column" and nid in planned:
                n = {"id": nid, "kind": "column", "name": nid[7:], "attrs": "{}"}
            elif k == "table":
                n = {"id": nid, "kind": "table", "name": nid[6:]}
            else:
                n = {"id": nid, "kind": k, "name": nid.split(":", 1)[1] if ":" in nid else nid,
                     "file": nid.split(":", 1)[1] if k == "file" else None}
        if k == "client":
            gid, glabel = PSEUDO["client"]
        elif k == "issue":
            gid, glabel = PSEUDO["issue"]
        elif k == "file":
            f = nid.split(":", 1)[1]
            gid, glabel = f"{f.split('/', 1)[0]}:views", f"{f.split('/', 1)[0]} · views/templates (text-match)"
        else:
            gid, glabel = group_of(n)
        g = groups.setdefault(gid, {"id": gid, "label": glabel, "repo": repo_of(n) if k not in ("client", "issue") else k, "side": side_of(n), "count": 0, "kinds": {},
                                    "targets": 0, "plan": 0})
        g["count"] += 1
        g["kinds"][n["kind"]] = g["kinds"].get(n["kind"], 0) + 1
        g["plan"] += extra.get("plan_role") in ("added", "modified", "missing", "forbidden")
        nodes.append({"id": nid, "kind": n["kind"], "name": n.get("name") or nid, "fqn": n.get("fqn"), "file": n.get("file"), "line": n.get("line"),
                      "module": n.get("module"), "entry_kind": n.get("entry_kind"), "repo": repo_of(n), "group": gid, **extra})
    return {"nodes": nodes, "edges": list(edges.values()), "groups": list(groups.values()),
            "meta": {"mode": "plan", "specs": [plan_name], "plan": plan.get("name"), "title": plan.get("title"), "summary": res["summary"],
                     "verify": verify, "min_confidence": "heuristic", "gate": None, "targets": [], "truncated": False, "sinks": None},
            "report": P.render_check(res, max_items=200)}

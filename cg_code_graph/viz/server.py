"""Local web UI over a code-graph DB: `python -m cg_code_graph.cli serve --db out/combined.db [--port 8177]`.

Stdlib HTTP server, read-only. Static page + vendored Cytoscape.js/fcose (no CDN). JSON API:
  /api/search?q=&kind=&fuzzy=1    node lookup (fuzzy: the query's characters in order)
  /api/stats                      node / edge counts by kind, entry points by kind, edges by confidence (landing page)
  /api/graph?mode=&spec=&spec=&min_conf=&sinks=   subgraph = union of the query's evidence paths
  /api/node?id=                   docblock, file:line, source snippet, in/out edges with evidence
  /api/presets                    canned queries: --presets FILE or .cg.yaml viz.presets, the sample-app presets that
                                  resolve in this graph, and starter queries derived from the graph
  /api/graph?mode=plan&spec=<plan>[&verify=1]   planned-change overlay (plans/<name>.yaml) + check report
  /api/plans                      plan files
"""
from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..core.store import GraphStore
from . import graph as G

STATIC = Path(__file__).parent / "static"
# Canned queries for the bundled sample apps (examples/bookstore-*), shown when they resolve in the served graph. Your
# own project: .cg.yaml `viz.presets` or `serve --presets presets.json` (a JSON list of {id, label, mode, specs[, sinks]});
# starter queries derived from the graph (cg_code_graph/starters.py) are always offered after them.
PRESETS = [
    {"id": "warehouse_reaches", "label": "what reaches the warehouse connection / warehouse_stock (runtime vs operator vs gated)",
     "mode": "reaches", "specs": ["connection:warehouse", "table:warehouse_stock"]},
    {"id": "report_page_tables", "label": "report page -> backend -> tables (columns folded per table)", "mode": "downstream",
     "specs": ["page:/reports/:id"], "sinks": ["table", "column"]},
    {"id": "report_page_orders", "label": "path: report page -> ReportController::top -> table orders",
     "mode": "path", "specs": ["page:/reports/:id", "ReportController::top", "table:orders"]},
    {"id": "timezone_inputs", "label": "who reads request key 'timezone' (incl. resolutions)", "mode": "reaches",
     "specs": ["request_key:timezone"]},
    {"id": "plan_preorders", "label": "PLAN overlay: pre-orders (planned vs real graph, uncovered siblings)", "mode": "plan",
     "specs": ["preorders"]},
    {"id": "impact_report_top", "label": "impact of ReportController::top", "mode": "impact",
     "specs": ["ReportController::top"]},
]


class App:
    def __init__(self, db: str, roots: dict[str, str] | None = None, plans: str | None = None, presets: str | None = None):
        self.db = db
        self.plans = plans
        self.presets_file = presets
        self.local = threading.local()
        self.sources = G.Sources(GraphStore(db), roots)
        self._presets = None

    def st(self) -> GraphStore:
        if not hasattr(self.local, "st"):
            self.local.st = GraphStore(self.db)
        return self.local.st

    def api(self, path: str, qs: dict) -> object:
        one = lambda k, d=None: (qs.get(k) or [d])[0]  # noqa: E731
        if path == "/api/search":
            return G.search(self.st(), one("q", ""), one("kind") or None, int(one("limit", 30)), fuzzy=one("fuzzy") == "1")
        if path == "/api/stats":
            return G.stats(self.st())
        if path == "/api/graph" and one("mode") == "plan":
            return G.build_plan(self.st(), (qs.get("spec") or [""])[0], self.plans, verify=one("verify") == "1")
        if path == "/api/plans":
            from .. import plans as P
            return P.list_plans(self.plans)
        if path == "/api/graph":
            sinks = [s for s in (one("sinks") or "").split(",") if s] or None
            return G.build(self.st(), one("mode", "reaches"), qs.get("spec") or [], one("min_conf", "heuristic"), sinks)
        if path == "/api/node":
            return G.node_detail(self.st(), self.sources, one("id", ""))
        if path == "/api/presets":
            if self._presets is None:
                self._presets = menu(self.st(), self.presets_file, self.plans)
            return self._presets
        if path == "/api/meta":
            m = self.st().meta()
            return {"project": m.get("project"), "repos": m.get("repos"), "indexed_at": m.get("indexed_at"), "db": self.db}
        raise KeyError(path)


def _resolves(st: GraphStore, p: dict, plans: str | None) -> bool:
    from .. import query as Q
    if p.get("mode") == "plan":
        from .. import plans as P
        try:
            return all(P.find_plan(s, plans) for s in p.get("specs") or [])
        except P.PlanError:
            return False
    return bool(p.get("specs")) and all(Q.resolve_targets(st, s) for s in p["specs"])


def menu(st: GraphStore, presets_file: str | None = None, plans: str | None = None) -> list[dict]:
    """Preset menu: the project's presets (--presets FILE, else .cg.yaml viz.presets), then the sample-app presets that
    resolve in this graph, then the starter queries derived from it. Each entry carries `source`."""
    from ..config import graph_configs
    from ..starters import for_graph
    own, src = [], None
    if presets_file:
        own, src = json.loads(Path(presets_file).read_text()), "--presets"
    else:
        for _, cfg, _ in graph_configs(st):
            if (cfg.get("viz") or {}).get("presets"):
                own, src = cfg["viz"]["presets"], cfg.get("file", ".cg.yaml")
                break
    out = [dict(p, source=src) for p in own]
    out += [dict(p, source="examples") for p in PRESETS if _resolves(st, p, plans)]
    out += [dict(p, source="starter") for p in for_graph(st)]
    seen, menu_ = set(), []
    for p in out:
        if p.get("id") not in seen:
            seen.add(p.get("id"))
            menu_.append(p)
    return menu_


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            if u.path.startswith("/api/"):
                try:
                    res = app.api(u.path, parse_qs(u.query))
                    self._send(200, json.dumps(res, default=str).encode())
                except KeyError:
                    self._send(404, b'{"error":"not found"}')
                except Exception as e:  # noqa: BLE001
                    self._send(400, json.dumps({"error": str(e)}).encode())
                return
            rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            p = (STATIC / rel).resolve()
            if not str(p).startswith(str(STATIC.resolve())) or not p.is_file():
                self._send(404, b"not found", "text/plain")
                return
            self._send(200, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")
    return H


def serve(db: str, host="127.0.0.1", port=8177, roots: dict[str, str] | None = None, plans: str | None = None,
          presets: str | None = None):
    app = App(str(Path(db).resolve()), roots, plans, presets)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"code-graph view: http://{host}:{port}/  (db {app.db}; Ctrl-C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def export_plan_html(db: str, out: str, plan: str, plans_root=None, verify=False) -> str:
    """Self-contained HTML of a plan overlay (same view as mode=plan in `serve`)."""
    st = GraphStore(db)
    g = G.build_plan(st, plan, plans_root, verify=verify)
    return _write_html(st, g, out, f"plan {g['meta']['plan']}: {g['meta']['title']}")


def export_html(db: str, out: str, mode: str, specs: list[str], min_conf="heuristic", sinks=None, title=None) -> str:
    """Self-contained HTML (data + vendored JS inlined) for one query; opens from disk without a server."""
    st = GraphStore(db)
    g = G.build(st, mode, specs, min_conf, sinks)
    return _write_html(st, g, out, title or f"{mode} {' '.join(specs)}")


def _write_html(st: GraphStore, g: dict, out: str, title: str) -> str:
    src = G.Sources(st)
    details = {n["id"]: G.node_detail(st, src, n["id"], limit=12) for n in g["nodes"] if st.node(n["id"])}
    for d in details.values():          # no local paths in a file meant to be shared
        if d and d.get("snippet"):
            d["snippet"].pop("abs", None)
    html = (STATIC / "index.html").read_text()
    for name in ("cytoscape.min.js", "layout-base.js", "cose-base.js", "cytoscape-fcose.js"):
        js = (STATIC / "vendor" / name).read_text().replace("</script", "<\\/script")
        html = html.replace(f'<script src="vendor/{name}"></script>', f"<script>{js}</script>")
    js = (STATIC / "layered.js").read_text().replace("</script", "<\\/script")
    html = html.replace('<script src="layered.js"></script>', f"<script>{js}</script>")
    css = (STATIC / "app.css").read_text()
    html = html.replace('<link rel="stylesheet" href="app.css">', f"<style>{css}</style>")
    data = json.dumps({"graph": g, "details": details, "title": title}, default=str)
    app_js = (STATIC / "app.js").read_text().replace("</script", "<\\/script")
    data = data.replace("</", "<\\/")
    html = html.replace('<script src="app.js"></script>', f"<script>window.__STATIC__ = {data};</script><script>{app_js}</script>")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(html)
    return out

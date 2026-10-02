"""Visual view (codegraph/viz): subgraph projection + HTTP server smoke test + static export, on the sample apps."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.viz import graph as G  # noqa: E402
from codegraph.viz.server import App, make_handler, export_html  # noqa: E402
from sample import build, EXTRACTOR_DEPS, PLANS, needs_php  # noqa: E402


@needs_php
def test_subgraph_is_union_of_evidence_paths():
    st = GraphStore(build()["api"])
    g = G.build(st, "downstream", ["ReportController::top"], sinks=["table", "column"])
    ids = {n["id"] for n in g["nodes"]}
    assert "column:orders.placed_at" in ids
    assert all(e["at"] and e["confidence"] in ("exact", "resolved", "heuristic") for e in g["edges"])
    assert all(e["src"] in ids and e["dst"] in ids for e in g["edges"])
    grp = {n["id"]: n["group"] for n in g["nodes"]}
    assert grp["column:orders.placed_at"] == "table:orders"   # columns fold into their table
    p = G.build(st, "path", ["ReportController::top", "SalesReportService::build", "table:orders"])
    assert [e["kind"] for e in p["edges"]][:2] == ["CALLS", "CALLS"] and p["meta"]["targets"][0].startswith("column:orders.")


@needs_php
def test_reaches_view_runtime_operator_gated():
    g = G.build(GraphStore(build()["api"]), "reaches", ["connection:warehouse", "table:warehouse_stock"])
    assert {n["entry_kind"] for n in g["nodes"]} >= {"http_route", "artisan_command"}
    n = next(x for x in g["nodes"] if x["id"].endswith("InventoryController::index"))
    assert n["gate_status"] not in (None, "live")
    assert any(e["gated"] for e in g["edges"])


@pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")
@needs_php
def test_server_and_static_export(tmp_path):
    db = str(build()["combined"])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(db, plans=str(PLANS))))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        get = lambda p: urllib.request.urlopen(base + p).read()  # noqa: E731
        assert b"cytoscape.min.js" in get("/")
        assert len(get("/vendor/cytoscape.min.js")) > 100_000
        g = json.loads(get("/api/graph?mode=reaches&spec=request_key:timezone"))
        assert any(n["id"].endswith("SalesReportService::build") for n in g["nodes"])
        assert json.loads(get("/api/search?q=reports&kind=page"))[0]["kind"] == "page"
        assert {p["id"] for p in json.loads(get("/api/presets"))} >= {"warehouse_reaches", "plan_preorders"}
        pg = json.loads(get("/api/graph?mode=plan&spec=preorders"))
        assert any(n.get("plan_role") == "missing" for n in pg["nodes"])
        fe_groups = [x for x in g["groups"] if x.get("side") == "fe"]
        assert fe_groups, "frontend groups are marked by side, not by a hardcoded repo name"
        with pytest.raises(urllib.error.HTTPError):
            get("/../../examples/bookstore.gates.json")
    finally:
        httpd.shutdown()
    out = export_html(db, str(tmp_path / "v.html"), "path", ["page:/reports/:id", "table:orders"])
    html = Path(out).read_text()
    assert "window.__STATIC__" in html and 'src="vendor/' not in html

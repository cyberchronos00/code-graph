"""Visual view (cg_code_graph/viz): subgraph projection + HTTP server smoke test + static export, on the sample apps."""
import json
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.viz import graph as G  # noqa: E402
from cg_code_graph.viz.server import App, make_handler, export_html  # noqa: E402
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


@pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
@needs_php
def test_server_and_static_export(tmp_path):
    db = str(build()["combined"])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(db, plans=str(PLANS))))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        get = lambda p: urllib.request.urlopen(base + p).read()  # noqa: E731
        page = get("/")
        assert b"cytoscape.min.js" in page and b"layered.js" in page and b'rel="icon"' in page
        assert b'id="presets"' not in page  # starter queries live on the landing page
        assert b"wheelSensitivity" not in get("/app.js") and b"CGLayered" in get("/layered.js")
        st = json.loads(get("/api/stats"))
        assert st["nodes"] > 0 and st["entry_points"] > 0 and set(st["confidence"]) == {"exact", "resolved", "heuristic"}
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
    assert "window.__STATIC__" in html and 'src="vendor/' not in html and 'src="layered.js"' not in html
    assert "CGLayered" in html


@needs_php
def test_stats_and_fuzzy_search():
    st = GraphStore(str(build()["api"]))
    s = G.stats(st)
    assert {"project", "indexed_at", "nodes", "edges", "node_kinds", "edge_kinds", "entry_points", "entry_kinds",
            "confidence"} <= set(s)
    assert s["nodes"] == sum(s["node_kinds"].values()) and s["edges"] == sum(s["edge_kinds"].values())
    assert s["entry_points"] == sum(s["entry_kinds"].values()) > 0
    assert set(s["confidence"]) == {"exact", "resolved", "heuristic"} and sum(s["confidence"].values()) == s["edges"]
    assert not G.search(st, "RptCtrl")
    assert any(h["name"].startswith("ReportController") for h in G.search(st, "RptCtrl", fuzzy=True))
    exact = G.search(st, "ReportController")
    assert G.search(st, "ReportController", fuzzy=True)[:len(exact)] == exact  # substring hits stay first


LAYERED = ROOT / "cg_code_graph" / "viz" / "static" / "layered.js"
NODE_CHECK = r"""
const L = require(process.argv[1])
// impact view: target at depth 0, 40 callers at depth 1 in 25 modules over 6 folders, 8 entry points at depth 2
const nodes = [{ id: 'T', kind: 'method', depth: 0, is_target: true, group: 'net', file: 'Packages/Net/Client.swift' }]
const edges = []
for (let i = 0; i < 40; i++) {
  const id = 'c' + String(i).padStart(2, '0')
  nodes.push({ id, kind: 'method', depth: 1, group: 'm' + (i % 25), file: 'Packages/F' + (i % 6) + '/m' + (i % 25) + '/x.swift',
    entry_kind: i % 10 === 0 ? 'UI page' : null })
  edges.push({ src: id, dst: 'T' })
}
for (let i = 0; i < 8; i++) { nodes.push({ id: 'e' + i, kind: 'class', depth: 2, group: 'app', file: 'App/e.swift', entry_kind: 'main' }); edges.push({ src: 'e' + i, dst: 'c0' + i }) }
const data = { meta: { mode: 'impact' }, nodes, edges }
const b = L.build(data)
const top = b.units.filter((u) => !u.lane)
const clusters = b.units.filter((u) => u.type === 'cluster')
const covered = clusters.reduce((a, u) => a + u.count, 0) + b.units.filter((u) => u.type === 'node').length
const repOk = nodes.every((n) => b.rep.has(n.id)) && clusters.every((u) => u.members.every((m) => b.rep.get(m) === u.id))
const xs = [...new Set(top.map((u) => b.pos.get(u.id).x))].sort((a, c) => a - c)
const c = clusters.slice().sort((a, d) => d.count - a.count)[0]
const o = L.build(data, { open: new Map([[c.id, 20]]) })
let moved = 0
for (const u of top) { const p = b.pos.get(u.id); const q = o.pos.get(u.id); moved = Math.max(moved, Math.hypot(p.x - q.x, p.y - q.y)) }
const lane = o.units.filter((u) => u.lane === c.id)
console.log(JSON.stringify({ n: nodes.length, covered, repOk, top: top.length, clusters: clusters.length,
  entries: clusters.reduce((a, u) => a + u.entries, 0), layers: xs.length, targetRight: b.pos.get('T').x === xs[xs.length - 1],
  moved, lane: lane.length, opened: c.count, again: JSON.stringify([...L.build(data).pos]) === JSON.stringify([...b.pos]) }))
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_layered_clusters_and_lanes():
    out = subprocess.run(["node", "-e", NODE_CHECK, str(LAYERED)], capture_output=True, text=True, check=True).stdout
    r = json.loads(out)
    assert r["covered"] == r["n"] and r["repOk"], r                  # cluster counts equal the underlying nodes
    assert r["top"] <= 20 and r["clusters"] >= 2, r                   # 49 nodes fold into a few counted clusters
    assert r["entries"] == 4, r                                       # entry points counted inside the clusters
    assert r["layers"] == 3 and r["targetRight"], r                   # one column per depth, target on the right
    assert r["moved"] < 40 and r["lane"] == min(20, r["opened"]), r   # expanding keeps everything else in place
    assert r["again"], r                                              # deterministic


APP_JS = ROOT / "cg_code_graph" / "viz" / "static" / "app.js"


def _lum(h: str) -> float:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _style_colours() -> tuple[dict, list, list]:
    """Constants, edge / border colours and text colours of the Cytoscape style table in app.js (written in light
    colours as c('#hex'); the dark theme maps each through DARK)."""
    import re
    src = APP_JS.read_text()
    const = dict(re.findall(r"const (CANVAS|MODULE_FILL|GATED|PARTGATED) = '(#[0-9a-fA-F]{3,6})'", src))
    conf = {c: (col, w, st) for c, col, w, st in re.findall(
        r"(exact|resolved|heuristic): \{ color: '(#[0-9a-fA-F]{6})', width: ([\d.]+), style: '(\w+)' \}", src)}
    dark_src = src[src.index("const DARK = {"):]
    dark = dict(re.findall(r"'(#[0-9a-fA-F]{3,6})': '(#[0-9a-fA-F]{3,6})'", dark_src[:dark_src.index("}")]))
    fam_src = src[src.index("const FAMILY = {"):]
    family = dict(re.findall(r"(\w+): '(#[0-9a-fA-F]{6})'", fam_src[:fam_src.index("}")]))
    style = src[src.index("const makeStyle = () => ["):]
    style = style[:style.index("\n  ]\n")]
    lines = re.findall(r"'((?:mid-)?(?:target-arrow|line|border)-color)': c\('(#[0-9a-fA-F]{3,6})'\)", style)
    lines += [("conf " + c, v[0]) for c, v in conf.items()] + [(k, const[k]) for k in ("GATED", "PARTGATED")]
    texts = re.findall(r"[{ ,]color: c\('(#[0-9a-fA-F]{3,6})'\)", style)
    return {"const": const, "conf": conf, "dark": dark, "family": family}, lines, texts


def test_edge_and_border_contrast():
    """#82 items 7 / 13 / 14: in the light and the dark theme, every edge and border colour is >= 3:1 (WCAG 1.4.11)
    on the canvas and on a module box's fill, node fills (kind families) >= 3:1 on the canvas, label text >= 4.5:1
    (1.4.3) on its text background; the three confidences differ by dash pattern and width, not only by colour."""
    meta, lines, texts = _style_colours()
    dark = meta["dark"]
    assert set(meta["conf"]) == {"exact", "resolved", "heuristic"} and len(lines) > 20 and len(meta["family"]) == 8
    themes = {"light": (lambda h: h), "dark": (lambda h: dark.get(h, h))}
    bad = []
    for name, f in themes.items():
        canvas, fill = f(meta["const"]["CANVAS"]), f(meta["const"]["MODULE_FILL"])
        bad += [(name, k, c, round(_contrast(f(c), bg), 2)) for k, c in lines for bg in (canvas, fill) if _contrast(f(c), bg) < 3]
        bad += [(name, "family " + k, c, round(_contrast(c, canvas), 2)) for k, c in meta["family"].items() if _contrast(c, canvas) < 3]
        bad += [(name, "text", c, round(_contrast(f(c), f("#fff")), 2)) for c in texts if _contrast(f(c), f("#fff")) < 4.5]
    assert not bad, bad
    assert all(c in dark for _, c in lines), [c for _, c in lines if c not in dark]   # every colour has a dark mapping
    assert len({v[2] for v in meta["conf"].values()}) == 3                        # solid / dashed / dotted
    assert float(meta["conf"]["exact"][1]) > float(meta["conf"]["heuristic"][1])   # and width


NODE_BUDGET = r"""
const L = require(process.argv[1])
// nine layers (depth 0..8), six of them wide: unbounded per-layer folding gave 46 top-level items
const nodes = [{ id: 'T', kind: 'method', depth: 0, is_target: true, group: 'net', file: 'Net/Client.swift' }]
const edges = []
const widths = [1, 6, 9, 19, 59, 93, 79, 1]
widths.forEach((w, i) => { for (let j = 0; j < w; j++) {
  const id = `d${i + 1}_${j}`
  nodes.push({ id, kind: 'method', depth: i + 1, group: 'm' + (j % 31), file: `Pkg${j % 9}/m${j % 31}/x.swift` })
  edges.push({ src: id, dst: i ? `d${i}_0` : 'T' })
} })
const data = { meta: { mode: 'impact' }, nodes, edges }
const top = (o) => L.build(data, o).units.filter((u) => !u.lane)
const b = L.build(data, {})
console.log(JSON.stringify({ top: top({}).length, unbounded: top({ maxItems: 1e9 }).length, flat: top({ flat: true }).length,
  covered: nodes.every((n) => b.rep.has(n.id)), single: top({}).filter((u) => u.type === 'cluster' && u.count === 1).length,
  target: top({}).some((u) => u.id === 'T'),
  gapClosed: L.build(data, { fitWidth: 1600 }).gap, gapWide: L.build(data, {}).gap,
  gapOpen: L.build(data, { fitWidth: 1600, open: new Map([[b.units.find((u) => u.type === 'cluster').id, 20]]) }).gap,
  floor: L.MIN_GAP_CLUSTER, full: L.GAP_X }))
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_layered_total_item_budget():
    """#82 item 6 / 15: the folded layers share one budget of top-level items (30), so a deep impact view stays
    readable; every node is still represented and the target stays a node of its own."""
    out = subprocess.run(["node", "-e", NODE_BUDGET, str(LAYERED)], capture_output=True, text=True, check=True).stdout
    r = json.loads(out)
    assert r["unbounded"] > 40 and r["top"] <= 30, r
    assert r["covered"] and r["single"] == 0 and r["target"] and r["flat"] == 268, r
    # with every cluster closed the nine layers narrow to fit the canvas width, so the first fit shows every column
    # (IceCubesApp screenshots cut the leftmost one off); an open cluster's member lane needs the full gap
    assert r["floor"] <= r["gapClosed"] < r["full"] and 8 * r["gapClosed"] + 140 <= 1600, r
    assert r["gapWide"] == r["full"] and r["gapOpen"] == r["full"], r

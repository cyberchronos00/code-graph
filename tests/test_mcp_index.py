"""The MCP `index` tool on a combined (backend + frontend) graph: a root is matched to the recorded repo roots and never
lands in another repo's slot; unknown roots, mismatched repo/root pairs and 0-node results are refused and leave the
graph unchanged. Runs on a throwaway copy of the bookstore samples (the planted bugs in examples/ are not touched)."""
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from sample import API, WEB, GATES, EXTRACTOR_DEPS  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph import mcp_server as M  # noqa: E402

pytestmark = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")
DELETE = "route:DELETE /v1/{store}/admin/reports/{report}"


@pytest.fixture()
def copy(monkeypatch):
    from codegraph.indexer import index_project
    from codegraph.link import link
    d = Path(tempfile.mkdtemp(prefix="codegraph-index-"))
    api, web = d / "bookstore-api", d / "bookstore-web"
    shutil.copytree(API, api)
    shutil.copytree(WEB, web)
    index_project(api, d / "api.db", "bookstore-api", gates=str(GATES))
    index_project(web, d / "web.db", "bookstore-web")
    link(str(d / "api.db"), str(d / "web.db"), str(d / "graph.db"), backend_name="bookstore-api", frontend_name="bookstore-web")
    monkeypatch.setitem(M.STATE, "db", str(d / "graph.db"))
    monkeypatch.setitem(M.STATE, "gates", str(GATES))
    monkeypatch.setitem(M.STATE, "root", None)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def digest(d: Path) -> str:
    return "".join(hashlib.sha256((d / f).read_bytes()).hexdigest() for f in ("api.db", "web.db", "graph.db"))


def middleware(d: Path) -> list:
    return json.loads(GraphStore(d / "graph.db").node(DELETE)["attrs"])["middleware"]


def web_nodes(d: Path) -> int:
    return GraphStore(d / "graph.db").meta()["stats"]["bookstore-web_nodes"]


def guard_delete_route(d: Path):
    p = d / "bookstore-api" / "routes" / "api.php"
    s = p.read_text()
    old = "    Route::delete('admin/reports/{report}', [ReportController::class, 'destroy']);\n"
    assert old in s
    p.write_text(s.replace(old, "    Route::middleware(['auth:api'])->group(function () {\n"
                                "        Route::delete('admin/reports/{report}', [ReportController::class, 'destroy']);\n"
                                "    });\n"))


def test_backend_root_reindexes_backend_slot_only(copy):
    web_before = (copy / "web.db").read_bytes()
    n_web = web_nodes(copy)
    assert middleware(copy) == []
    guard_delete_route(copy)
    out = M.index(root=str(copy / "bookstore-api"))
    assert out.startswith("re-indexed bookstore-api (") and "bookstore-web" not in out.split(";")[0], out
    assert middleware(copy) == ["auth:api"]
    assert (copy / "web.db").read_bytes() == web_before and web_nodes(copy) == n_web
    st = GraphStore(copy / "graph.db").meta()["stats"]
    assert st["bookstore-web_id_collisions"] == 0 and st["call_sites_matched"] > 0


def test_path_inside_a_repo_picks_that_repo(copy):
    out = M.index(root=str(copy / "bookstore-api" / "routes"))
    assert out.startswith("re-indexed bookstore-api (") and out.count("re-indexed") == 1, out
    out = M.index(root=str(copy / "bookstore-web" / "app" / "pages"))
    assert out.startswith("re-indexed bookstore-web (") and out.count("re-indexed") == 1, out


def test_parent_root_and_no_args_reindex_every_repo(copy):
    for out in (M.index(root=str(copy)), M.index()):
        assert "re-indexed bookstore-api" in out and "re-indexed bookstore-web" in out, out
        assert "0 nodes" not in out
    assert web_nodes(copy) > 0


def test_refusals_leave_the_graph_unchanged(copy):
    before = digest(copy)
    elsewhere = Path(tempfile.mkdtemp(prefix="codegraph-elsewhere-"))
    try:
        out = M.index(root=str(elsewhere))
        assert out.startswith("index refused:") and "matches no repo" in out and "bookstore-api" in out, out
        out = M.index(repo="bookstore-web", root=str(copy / "bookstore-api"))
        assert out.startswith("index refused:") and "not the recorded root of repo 'bookstore-web'" in out, out
        out = M.index(repo="bookstore-mobile")
        assert out.startswith("index refused:") and "unknown repo" in out, out
        assert digest(copy) == before
        # a repo whose sources vanished would index to 0 nodes: refused, nothing swapped in
        shutil.rmtree(copy / "bookstore-web" / "app")
        out = M.index(repo="bookstore-web")
        assert out.startswith("index refused:") and "0 nodes" in out, out
        assert digest(copy) == before and not list(copy.glob("*.tmp"))
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)


def test_single_repo_graph_refuses_empty_root(tmp_path, monkeypatch):
    from codegraph.indexer import index_project
    shutil.copytree(API, tmp_path / "api")
    index_project(tmp_path / "api", tmp_path / "api.db", "bookstore-api")
    monkeypatch.setitem(M.STATE, "db", str(tmp_path / "api.db"))
    monkeypatch.setitem(M.STATE, "gates", None)
    monkeypatch.setitem(M.STATE, "root", None)
    before = (tmp_path / "api.db").read_bytes()
    (tmp_path / "empty").mkdir()
    out = M.index(root=str(tmp_path / "empty"))
    assert out.startswith("index refused:") and "0 nodes" in out, out
    assert (tmp_path / "api.db").read_bytes() == before
    assert M.index().startswith("indexed ")


def test_impact_lists_snapshot_clients(monkeypatch):
    """impact shows external (not indexed) callers from the client snapshot next to the plans, like plan_check does."""
    from sample import build, PLANS
    monkeypatch.setitem(M.STATE, "db", str(build()["combined"]))
    monkeypatch.setitem(M.STATE, "plans", str(PLANS))
    out = M.impact("StockService::reserve")
    assert "external clients (snapshot, not indexed): 1" in out
    assert "POST /stock/reserve -> POST /v1/stock/reserve  @example/bookstore-mobile@4f2c9e1:pages/cart.vue:12" in out
    assert "external clients" not in M.impact("SalesReportService::remove")  # its route has no snapshot client
    from codegraph import cli
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(["impact", "StockService::reserve", "--db", str(build()["combined"]), "--plans-dir", str(PLANS)])
    assert "external clients (snapshot, not indexed): 1" in buf.getvalue() and "pages/cart.vue:12" in buf.getvalue()

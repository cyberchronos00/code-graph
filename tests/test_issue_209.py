"""path walks NAVIGATES_TO / ROUTES_TO, the edges impact uses for a page entry (#209)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.model import Edge, Node  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402


def test_page_navigation_is_a_path(tmp_path):
    st = GraphStore.create(tmp_path / "p.db")
    page = Node("page:ShelfPage", "page", "ShelfPage", fqn="ShelfPage", file="src/shelf/ShelfPage.tsx", line=1,
                lang="ts", entry_kind="ui_page")
    screen = Node("function:src/shelf/Screen.tsx#openShelf", "function", "openShelf", fqn="openShelf",
                  file="src/shelf/Screen.tsx", line=4, lang="ts")
    route = Node("route:GET /shelf", "route", "GET /shelf", file="src/routes.ts", line=2, lang="ts", entry_kind="http_route")
    target = Node("method:Inventory.reload", "method", "reload", fqn="Inventory.reload", file="src/Inventory.ts", line=9, lang="ts")
    st.write([page, screen, route, target], [
        Edge(page.id, screen.id, "NAVIGATES_TO", page.file, 6, "resolved"),
        Edge(route.id, screen.id, "ROUTES_TO", route.file, 2, "exact"),
        Edge(screen.id, target.id, "CALLS", screen.file, 8, "exact"),
    ])
    imp = Q.impact(st, "method:Inventory.reload")
    kinds = {e["entry_kind"] for e in imp["entry_points"]}
    assert "ui_page" in kinds and "http_route" in kinds
    path = Q.path_between(st, "page:ShelfPage", "method:Inventory.reload")
    assert [h["kind"] for h in path] == ["NAVIGATES_TO", "CALLS"], path
    route_path = Q.path_between(st, "route:GET /shelf", "method:Inventory.reload")
    assert [h["kind"] for h in route_path] == ["ROUTES_TO", "CALLS"]
    p = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "path", "page:ShelfPage", "method:Inventory.reload",
                        "--db", str(tmp_path / "p.db")], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and "NAVIGATES_TO" in p.stdout, p.stdout
    import cg_code_graph.mcp_server as M
    old = M.STATE["db"]
    M.STATE["db"] = str(tmp_path / "p.db")
    try:
        assert "NAVIGATES_TO" in M.path("page:ShelfPage", "method:Inventory.reload")
    finally:
        M.STATE["db"] = old

"""Selector-only candidate callers are counted apart from resolved callers; free functions are grouped by file (#204)."""
import pytest

pytest.importorskip("tree_sitter_swift")
from cg_code_graph import mcp_server as M  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

SRC = """\
final class LabelReader {
    var current = ""
    func refreshStatus() { current = "x" }
}

protocol Refreshable { func refreshStatus() }

final class ShelfBridge {
    func refreshStatus() {}
}

final class ShelfPanel {
    let bridge = ShelfBridge()
    func poll() { bridge.refreshStatus() }
}

func makeRefresher() -> AnyObject { LabelReader() }

func reloadAll() {
    let r = makeRefresher()
    r.refreshStatus()
}

func resyncShelf(_ b: ShelfBridge) {
    b.refreshStatus()
}
"""


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("i204")
    root = tmp / "app"
    (root / "Sources").mkdir(parents=True)
    (root / "Sources" / "Shelf.swift").write_text(SRC, encoding="utf-8")
    db = tmp / "g.db"
    index_project(root, db, "shelf")
    old = M.STATE.get("db")
    M.STATE["db"] = str(db)
    yield db
    M.STATE["db"] = old


def test_impact_counts_candidates_apart(graph):
    r = Q.impact(GraphStore(graph), "ShelfBridge.refreshStatus")
    ids = {c["id"]: c for c in r["callers"]}
    assert "function:reloadAll" in ids and ids["function:reloadAll"].get("candidate")
    assert ids["function:reloadAll"]["candidate_of"]["count"] >= 2
    assert not ids["method:ShelfPanel.poll"].get("candidate")
    assert r["callers_candidate"] == sum(1 for c in r["callers"] if c.get("candidate"))
    assert r["callers_resolved"] == len(r["callers"]) - r["callers_candidate"]
    out = M.impact("ShelfBridge.refreshStatus")
    assert f"transitive callers: {r['callers_resolved']} (+{r['callers_candidate']} candidate)" in out, out
    assert "methods named refreshStatus" in out


def test_free_functions_are_grouped_by_file_not_question_mark(graph):
    out = M.impact("ShelfBridge.refreshStatus")
    line = next(x for x in out.splitlines() if x.startswith("callers by module:"))
    assert "?" not in line, line
    assert "Shelf.swift (top level)" in line, line
    assert "reloadAll" not in line            # candidates are not counted per module


def test_callers_lists_candidates_apart(graph):
    out = M.callers("ShelfBridge.refreshStatus")
    head = out.splitlines()[1]
    assert head.startswith("direct callers: 2 (2 sites); candidate callers: 1"), out
    assert "(candidate, 1 of" in out

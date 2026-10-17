"""A call that leaves out a defaulted argument runs the default; one that passes it does not (#212, Swift)."""
import pytest

pytest.importorskip("tree_sitter_swift")
from cg_code_graph import mcp_server as M  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

SRC = """\
enum Prefs {
    static var currentGamut: Int { 1 }
    static func fallbackTint() -> Int { 0 }
}

func render(_ c: Int, gamut: Int = Prefs.currentGamut, tint: Int = Prefs.fallbackTint()) {}

func preview() { render(1) }

func export() { render(1, gamut: 2) }

func exportTinted() { render(1, gamut: 2, tint: 3) }
"""


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("i212")
    root = tmp / "app"
    (root / "Sources").mkdir(parents=True)
    (root / "Sources" / "Render.swift").write_text(SRC, encoding="utf-8")
    db = tmp / "g.db"
    index_project(root, db, "render")
    old = M.STATE.get("db")
    M.STATE["db"] = str(db)
    yield db
    M.STATE["db"] = old


def test_only_the_omitting_caller_reaches_the_default(graph):
    st = GraphStore(graph)
    gamut = {c["id"]: c for c in Q.impact(st, "currentGamut")["callers"]}
    assert "function:preview" in gamut and gamut["function:preview"]["omits_default"] == "gamut"
    assert "function:export" not in gamut and "function:exportTinted" not in gamut
    assert "function:render" not in gamut            # the declaration itself stays a default argument (#208)
    tint = {c["id"] for c in Q.impact(st, "fallbackTint")["callers"]}
    assert {"function:preview", "function:export"} <= tint
    assert "function:exportTinted" not in tint


def test_mcp_labels_the_omitted_default(graph):
    out = M.callers("currentGamut")
    assert "preview" in out and "(runs the default of gamut: in render)" in out, out
    assert "export" not in out
    assert "(parameter default gamut:)" in out
    imp = M.impact("currentGamut")
    assert "through an omitted default argument (1): preview (gamut: of render)" in imp, imp


def test_exact_mode_keeps_the_default_argument_marks(tmp_path, monkeypatch):
    from cg_code_graph.plugins.swift import exact, indexstore
    swift = exact.find_swift()
    if not (swift and indexstore.find_lib(swift)):
        pytest.skip("no Swift toolchain with libIndexStore")
    for v in ("CG_SWIFT_INDEX_STORE", "CG_NO_CACHE", "CG_LIBINDEXSTORE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("CG_SWIFT", swift)
    monkeypatch.setenv("CG_SWIFT_INDEX", "1")
    root = tmp_path / "app"
    (root / "Sources" / "Render").mkdir(parents=True)
    (root / "Package.swift").write_text('// swift-tools-version:5.9\nimport PackageDescription\n'
                                        'let package = Package(name: "Render", targets: [.target(name: "Render")])\n',
                                        encoding="utf-8")
    (root / "Sources" / "Render" / "Render.swift").write_text(SRC, encoding="utf-8")
    st = index_project(root, tmp_path / "g.db", "render-exact")
    if st["plugins"]["swift"]["mode"] != "indexstore":
        pytest.skip(f"swift build failed here: {st['plugins']['swift'].get('index', {}).get('status')}")
    gs = GraphStore(tmp_path / "g.db")
    imp = Q.impact(gs, "currentGamut")
    assert {c["id"] for c in imp["callers"]} == {"function:preview"}, imp["callers"]
    assert any(d["src"] == "function:render" for d in imp["default_arguments"])

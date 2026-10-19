"""Swift platform files and availability (#225), initializer recall (#108). Invented fixtures."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.platforms import swift_path_convention  # noqa: E402

pytest.importorskip("tree_sitter_swift")


def _db(files):
    tmp = Path(tempfile.mkdtemp(prefix="cg-swiftpf-"))
    for rel, body in files.items():
        (tmp / "p" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / "p" / rel).write_text(body)
    index_project(tmp / "p", tmp / "g.db", "p")
    return sqlite3.connect(tmp / "g.db")


def test_path_convention():
    assert swift_path_convention("App/Shared/Toolbar+iOS.swift") == ("ios", "file name Toolbar+iOS.swift")
    assert swift_path_convention("App/Face.watchOS.swift")[0] == "watchos"
    assert swift_path_convention("App/macOS/MenuBarItem.swift") == ("macos", "directory macOS/")
    assert swift_path_convention("App/Shared/Toolbar.swift") is None
    assert swift_path_convention("App/Kiosk.swift") is None          # no separator before the platform word


def test_platform_files_are_tagged():
    c = _db({"App/Shared/Toolbar.swift": "struct Toolbar {\n    func show() {}\n}\n",
             "App/Shared/Toolbar+iOS.swift": "struct HapticButton {\n    func tap() {}\n}\n",
             "App/macOS/MenuBarItem.swift": "struct MenuBarItem {\n    func open() {}\n}\n"})
    attrs = {i: json.loads(a or "{}") for i, a in c.execute("SELECT id, attrs FROM nodes")}
    assert "platforms" not in attrs["class:Toolbar"]
    h = attrs["class:HapticButton"]
    assert "ios" in h["platforms"] and "ios" not in h["platform_unknown"] and "macos" in h["platform_unknown"]
    assert "platform file name" in h["platform_expr"]
    m = attrs["class:MenuBarItem"]
    assert "macos" in m["platforms"] and "macos" not in m["platform_unknown"]


AVAIL = """\
func newAPI() {}
func oldAPI() {}
func after() {}
var ready = true
func f() {
    if #available(iOS 99, *) { newAPI() } else { oldAPI() }
    if #available(iOS 98, *), ready {
        newAPI()
    } else { oldAPI() }
}
func g() {
    guard #available(iOS 97, *) else { oldAPI(); return }; after()
}
"""


def test_available_regions_by_column():
    c = _db({"A.swift": AVAIL})
    got = {(s, d, l): json.loads(a or "{}").get("available") for s, d, l, a in
           c.execute("SELECT src, dst, line, attrs FROM edges WHERE kind IN ('CALLS', 'READS_PROP')")}
    assert got[("function:f", "function:newAPI", 6)] == {"iOS": "99"}
    assert got[("function:f", "function:oldAPI", 6)] is None          # the else branch on the same line
    assert got[("function:f", "function:oldAPI", 9)] is None          # `} else { oldAPI() }` on the closing line
    assert got[("function:g", "function:oldAPI", 12)] is None         # guard's else
    assert got[("function:g", "function:after", 12)] == {"iOS": "97"}


INITS = """\
struct Point {
    init(x: Int) {}
    static func origin() -> Point { Self(x: 0) }
    func moved() -> Point { .init(x: 1) }
    struct Delta {
        init(dx: Int) {}
    }
}
struct Use {
    func a() {
        let d = Point.Delta(dx: 1)
        let p: Point = .init(x: 3)
        _ = (d, p)
    }
}
"""


def test_initializers_through_self_implicit_init_and_nested_types():
    c = _db({"P.swift": INITS})
    e = {(s, d, k) for s, d, k in c.execute("SELECT src, dst, kind FROM edges")}
    assert ("method:Point.origin", "method:Point.init", "CALLS") in e
    assert ("method:Point.origin", "class:Point", "INSTANTIATES") in e
    assert ("method:Point.moved", "method:Point.init", "CALLS") in e
    assert ("method:Use.a", "class:Point.Delta", "INSTANTIATES") in e
    assert ("method:Use.a", "method:Point.Delta.init", "CALLS") in e
    assert ("method:Use.a", "method:Point.init", "CALLS") in e

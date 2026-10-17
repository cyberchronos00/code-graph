"""Swift stored properties declared per `#if` branch: one node each, and reads / writes narrowed by platform (#219)."""
import sqlite3

import pytest

pytest.importorskip("tree_sitter_swift")
from cg_code_graph import mcp_server as M  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

from test_issue_202 import PBX  # noqa: E402

CANVAS = """\
import SwiftUI

struct CanvasView {
#if os(iOS)
    final class Coordinator {
        let scale: Double
        var offset: Double = 0
        init(scale: Double) { self.scale = scale }
        func draw() {
            print(scale)
            offset = 1
        }
    }
#elseif os(macOS)
    final class Coordinator {
        let scale: Double
        var offset: Double = 0
        init(scale: Double) { self.scale = scale }
        func render() {
            print(scale)
            offset = 2
        }
    }
#endif
}

final class Ruler {
#if os(iOS)
    var unit: Int = 1
#else
    var unit: Int = 2
#endif
    var label: String = ""

#if os(macOS)
    func macTick() {
        print(unit)
    }
#endif

    func tick() {
        unit = 3
        label = "x"
    }
}
"""


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("i219")
    root = tmp / "app"
    (root / "App.xcodeproj").mkdir(parents=True)
    (root / "Sources" / "App").mkdir(parents=True)
    (root / "App.xcodeproj" / "project.pbxproj").write_text(PBX, encoding="utf-8")
    (root / "Sources" / "App" / "CanvasView.swift").write_text(CANVAS, encoding="utf-8")
    db = tmp / "g.db"
    index_project(root, db, "canvas")
    old = M.STATE.get("db")
    M.STATE["db"] = str(db)
    yield db
    M.STATE["db"] = old


def _ids(db, kind):
    con = sqlite3.connect(db)
    return {r[0] for r in con.execute("SELECT id FROM nodes WHERE kind=?", (kind,))}


def _edges(db, dst):
    con = sqlite3.connect(db)
    return {(s, k) for s, k in con.execute("SELECT src, kind FROM edges WHERE dst=?", (dst,))}


def test_each_branch_field_is_its_own_node(graph):
    fields = _ids(graph, "field")
    assert {"field:CanvasView.Coordinator.scale", "field:CanvasView.Coordinator.scale@16",
            "field:CanvasView.Coordinator.offset", "field:CanvasView.Coordinator.offset@17"} <= fields
    assert {"field:Ruler.unit", "field:Ruler.unit@31", "field:Ruler.label"} <= fields
    assert not any(f.startswith("field:Ruler.label@") for f in fields)


def test_variant_type_members_bind_their_own_fields(graph):
    ios, mac = "field:CanvasView.Coordinator.scale", "field:CanvasView.Coordinator.scale@16"
    assert ("method:CanvasView.Coordinator.draw", "READS_PROP") in _edges(graph, ios)
    assert ("method:CanvasView.Coordinator.render", "READS_PROP") in _edges(graph, mac)
    assert ("method:CanvasView.Coordinator.render", "READS_PROP") not in _edges(graph, ios)
    assert ("method:CanvasView.Coordinator.draw", "READS_PROP") not in _edges(graph, mac)
    assert ("method:CanvasView.Coordinator.init@18", "WRITES_PROP") in _edges(graph, mac)
    assert ("method:CanvasView.Coordinator.init@18", "WRITES_PROP") not in _edges(graph, ios)
    assert ("method:CanvasView.Coordinator.render", "WRITES_PROP") in \
        _edges(graph, "field:CanvasView.Coordinator.offset@17")
    assert ("method:CanvasView.Coordinator.render", "WRITES_PROP") not in \
        _edges(graph, "field:CanvasView.Coordinator.offset")


def test_platform_only_method_reads_the_field_built_there(graph):
    assert ("method:Ruler.macTick", "READS_PROP") in _edges(graph, "field:Ruler.unit@31")
    assert ("method:Ruler.macTick", "READS_PROP") not in _edges(graph, "field:Ruler.unit")


def test_unnarrowed_access_keeps_every_variant(graph):
    assert ("method:Ruler.tick", "WRITES_PROP") in _edges(graph, "field:Ruler.unit")
    assert ("method:Ruler.tick", "WRITES_PROP") in _edges(graph, "field:Ruler.unit@31")
    assert ("method:Ruler.tick", "WRITES_PROP") in _edges(graph, "field:Ruler.label")


def test_readers_lists_only_the_platform_branch(graph):
    out = M.readers("CanvasView.Coordinator.scale")       # every declaration, each site with its platform
    assert "Coordinator.draw  @CanvasView.swift:10  receiver=self platforms=ios" in out, out
    assert "Coordinator.render  @CanvasView.swift:20  receiver=self platforms=macos" in out, out
    out = M.readers("field:CanvasView.Coordinator.scale@16")   # the macOS declaration alone
    assert "render" in out and "draw" not in out, out

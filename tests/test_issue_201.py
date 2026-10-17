"""Swift `#if` / `#else` variants: a platform-narrowed reference binds only to the variants built where it is (#201)."""
import sqlite3

import pytest

pytest.importorskip("tree_sitter_swift")
from cg_code_graph import mcp_server as M  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

from test_issue_202 import PBX  # noqa: E402

BRIDGE = """\
import Foundation
#if os(macOS)
import AppKit

final class PrinterBridge {
    static let shared = PrinterBridge()

    init() {
        print("mac init")
    }

    func refreshStatus() {
        print("mac refresh")
    }

    func sendJob(_ name: String) {
        refreshStatus()
    }
}
#else
final class PrinterBridge {
    static let shared = PrinterBridge()

    func refreshStatus() {}

    func sendJob(_ name: String) {}
}
#endif
"""

PANEL = """\
import Foundation

#if os(macOS)
import AppKit

final class PrintPanel {
    func printNow() {
        PrinterBridge.shared.sendJob("label")
    }

    func poll() {
        PrinterBridge.shared.refreshStatus()
    }
}
#endif

final class LabelReader {
    var current: String = ""

    func refreshStatus() {
        current = "x"
    }
}

final class Settings {
    func apply(_ thing: AnyObject) {
        let anything = thing as! Refreshable
        anything.refreshStatus()
    }
}

protocol Refreshable {
    func refreshStatus()
}
"""


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("i201")
    root = tmp / "app"
    (root / "App.xcodeproj").mkdir(parents=True)
    (root / "Sources" / "App").mkdir(parents=True)
    (root / "App.xcodeproj" / "project.pbxproj").write_text(PBX, encoding="utf-8")
    (root / "Sources" / "App" / "PrinterBridge.swift").write_text(BRIDGE, encoding="utf-8")
    (root / "Sources" / "App" / "PrintPanel.swift").write_text(PANEL, encoding="utf-8")
    db = tmp / "g.db"
    index_project(root, db, "print")
    old = M.STATE.get("db")
    M.STATE["db"] = str(db)
    yield db
    M.STATE["db"] = old


def _edges(db, dst):
    con = sqlite3.connect(db)
    return {(s, k) for s, k in con.execute("SELECT src, kind FROM edges WHERE dst=?", (dst,))}


def test_macos_only_code_does_not_reach_the_else_stub(graph):
    into_stub = _edges(graph, "method:PrinterBridge.refreshStatus@24")
    assert ("method:PrintPanel.poll", "CALLS") not in into_stub
    assert ("method:PrinterBridge.sendJob", "CALLS") not in into_stub
    into_mac = _edges(graph, "method:PrinterBridge.refreshStatus")
    assert ("method:PrintPanel.poll", "CALLS") in into_mac
    assert ("method:PrinterBridge.sendJob", "CALLS") in into_mac
    shared_stub = _edges(graph, "constant:PrinterBridge.shared@22")
    assert not any(s.startswith("method:PrintPanel.") for s, _k in shared_stub)


def test_unnarrowed_site_keeps_every_variant(graph):
    # Settings.apply is built everywhere: its (candidate) call still reaches both definitions
    assert ("method:Settings.apply", "CALLS") in _edges(graph, "method:PrinterBridge.refreshStatus")
    assert ("method:Settings.apply", "CALLS") in _edges(graph, "method:PrinterBridge.refreshStatus@24")


def test_else_construction_uses_its_own_implicit_init(graph):
    assert ("class:PrinterBridge@21", "CALLS") not in _edges(graph, "method:PrinterBridge.init")
    assert ("class:PrinterBridge@21", "INSTANTIATES") in _edges(graph, "class:PrinterBridge@21")
    assert ("class:PrinterBridge@21", "INSTANTIATES") not in _edges(graph, "class:PrinterBridge")
    out = M.platform_divergence()
    assert "PrinterBridge.init" not in out


def test_callers_count_sites_once(graph):
    out = M.callers("PrinterBridge.refreshStatus")
    assert "direct callers: 2 (2 sites); candidate callers: 1 (1 site" in out, out
    assert out.count("PrintPanel.poll") == 1


def test_node_neighbours_render_line_apart_from_variant_suffix(graph):
    out = M.node("PrinterBridge.sendJob")
    assert "PrinterBridge.refreshStatus :17" in out, out
    assert "@24@" not in out

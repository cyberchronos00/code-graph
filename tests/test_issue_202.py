"""impact --platform drops targets that are not built for that platform (#202)."""
import pytest

pytest.importorskip("tree_sitter_swift")
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

PBX = """\
// !$*UTF8*$!
{
	archiveVersion = 1;
	objectVersion = 77;
	objects = {
		A1 = {isa = PBXFileSystemSynchronizedRootGroup; path = Sources; sourceTree = "<group>"; };
		A2 = {isa = PBXGroup; children = (A1); sourceTree = "<group>"; };
		A3 = {isa = PBXNativeTarget; buildConfigurationList = A5; name = App; productName = App; fileSystemSynchronizedGroups = (A1); };
		A4 = {isa = PBXProject; buildConfigurationList = A6; mainGroup = A2; targets = (A3); };
		A5 = {isa = XCConfigurationList; buildConfigurations = (A7); defaultConfigurationIsVisible = 0; };
		A6 = {isa = XCConfigurationList; buildConfigurations = (A8); defaultConfigurationIsVisible = 0; };
		A7 = {isa = XCBuildConfiguration; name = Release; buildSettings = {SUPPORTED_PLATFORMS = "iphoneos iphonesimulator macosx xros xrsimulator"; }; };
		A8 = {isa = XCBuildConfiguration; name = Release; buildSettings = {}; };
	};
	rootObject = A4;
}
"""

BRIDGE = """\
import Foundation
#if os(macOS)
import AppKit

final class PrinterBridge {
    static let shared = PrinterBridge()
    init() {}
    func refreshStatus() { print("mac refresh") }
    func sendJob(_ name: String) { refreshStatus() }
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
    func printNow() { PrinterBridge.shared.sendJob("label") }
    func poll() { PrinterBridge.shared.refreshStatus() }
}
#endif
"""


def test_platform_keeps_only_the_built_variant(tmp_path):
    root = tmp_path / "app"
    (root / "App.xcodeproj").mkdir(parents=True)
    (root / "Sources" / "App").mkdir(parents=True)
    (root / "App.xcodeproj" / "project.pbxproj").write_text(PBX, encoding="utf-8")
    (root / "Sources" / "App" / "PrinterBridge.swift").write_text(BRIDGE, encoding="utf-8")
    (root / "Sources" / "App" / "PrintPanel.swift").write_text(PANEL, encoding="utf-8")
    index_project(root, tmp_path / "g.db", "print")
    st = GraphStore(tmp_path / "g.db")
    ios = Q.impact(st, "PrinterBridge.refreshStatus", platform="ios")
    macos = Q.impact(st, "PrinterBridge.refreshStatus", platform="macos")
    assert ios["targets"], ios
    assert macos["targets"], macos
    assert all("@" in t for t in ios["targets"]), ios["targets"]
    assert all("@" not in t for t in macos["targets"]), macos["targets"]
    assert set(ios["targets"]).isdisjoint(macos["targets"])
    assert set(ios["platform"]["targets_not_built"]) == set(macos["targets"])
    ids = {c["id"] for c in ios["callers"]}
    assert "method:PrintPanel.poll" not in ids
    assert not any(c["id"] in set(macos["targets"]) for c in ios["callers"])

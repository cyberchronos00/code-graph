"""divergence variant 'used at' keeps sites built for the target (#211)."""
import pytest

pytest.importorskip("tree_sitter_swift")
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.platforms import divergence  # noqa: E402

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

SWIFT = """\
enum Exporter {
    #if os(iOS)
    static func share(from view: AnyObject?) {}
    #elseif os(macOS)
    static func share(from view: AnyObject?) {}
    #endif
}

#if os(iOS)
func shareFromPhone() { Exporter.share(from: nil) }
#endif

#if os(iOS) || os(visionOS)
func shareFromHeadset() { Exporter.share(from: nil) }
#endif

#if os(macOS)
func shareFromMac() { Exporter.share(from: nil) }
#endif
"""


def test_used_at_for_visionos_is_the_headset_site(tmp_path):
    root = tmp_path / "app"
    (root / "App.xcodeproj").mkdir(parents=True)
    (root / "Sources").mkdir()
    (root / "App.xcodeproj" / "project.pbxproj").write_text(PBX, encoding="utf-8")
    (root / "Sources" / "Exporter.swift").write_text(SWIFT, encoding="utf-8")
    index_project(root, tmp_path / "g.db", "export")
    st = GraphStore(tmp_path / "g.db")
    res = divergence(st, kind="variants", target="visionos")
    variants = [v for v in res["variants"] if "share" in v["name"]]
    assert variants, res["variants"]
    ats = []
    for v in variants:
        for u in v["used_at"]:
            ats.append(u["at"] if isinstance(u, dict) else u)
    assert any("Exporter.swift" in a for a in ats)
    phone = SWIFT.splitlines().index("func shareFromPhone() { Exporter.share(from: nil) }") + 1
    headset = SWIFT.splitlines().index("func shareFromHeadset() { Exporter.share(from: nil) }") + 1
    mac = SWIFT.splitlines().index("func shareFromMac() { Exporter.share(from: nil) }") + 1
    assert any(a.endswith(f":{headset}") for a in ats), ats
    assert not any(a.endswith(f":{phone}") or a.endswith(f":{mac}") for a in ats), ats

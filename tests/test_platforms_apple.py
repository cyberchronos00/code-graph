"""Apple targets from the build (#74): Xcode project platforms (SDKROOT, SUPPORTED_PLATFORMS, SUPPORTS_MACCATALYST)
and target membership (build phases, synchronized folders and their exceptions), tvOS / watchOS / visionOS as their
own targets, Catalyst-aware `os(iOS)` / `os(macOS)` / `targetEnvironment(macCatalyst)`, `#else` listing the project's
other targets, and per-platform variants reached by every caller."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from cg_code_graph import platforms as PF  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.xcode import apple_build, parse_pbxproj  # noqa: E402

XC = ROOT / "tests" / "platform_fixtures" / "xcode_app"
_S: dict = {}


def build(root: Path, name: str):
    d = Path(tempfile.mkdtemp(prefix="codegraph-apple-"))
    st = index_project(root, d / "g.db", name)
    return st, sqlite3.connect(d / "g.db")


def xc():
    if "xc" not in _S:
        _S["xc"] = build(XC, "xcode-app")
    return _S["xc"]


def attrs(db, nid):
    r = db.execute("SELECT attrs FROM nodes WHERE id=?", (nid,)).fetchone()
    return json.loads(r[0] or "{}") if r else None


def write(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return root


def test_pbxproj_parser_and_apple_build():
    d = parse_pbxproj((XC / "App.xcodeproj" / "project.pbxproj").read_text())
    assert d["objectVersion"] == "77" and d["objects"][d["rootObject"]]["isa"] == "PBXProject"
    ab = apple_build(XC)
    assert list(ab["targets"]) == ["ios", "macos"] and ab["mac"] == "catalyst"
    assert ab["targets"]["macos"] == "App.xcodeproj target App: SUPPORTS_MACCATALYST"
    assert ab["membership"]["Widget/Widget.swift"] == {"platforms": ["ios"], "targets": ["AppWidget"]}
    # a synchronized folder's membership exception for another target adds the file there
    assert ab["membership"]["Widget/Shared.swift"]["targets"] == ["App", "AppWidget"]
    assert ab["membership"]["App/Toolbar.swift"] == {"platforms": ["macos", "ios"], "targets": ["App"]}
    with pytest.raises(ValueError):
        parse_pbxproj("{ objects = { a = (1, 2; }; }")


def test_xcode_app_targets_come_from_the_project():
    st, db = xc()
    pl = st["platforms"]
    assert pl["targets"] == ["macos", "ios"]            # no windows / linux "desktop default"
    assert pl["target_sources"]["ios"] == "App.xcodeproj target App: SDKROOT / SUPPORTED_PLATFORMS"


def test_catalyst_conditions_and_else_branches():
    _, db = xc()
    assert attrs(db, "method:ToolbarItems.close")["platforms"] == ["macos"]        # targetEnvironment(macCatalyst)
    assert attrs(db, "method:ToolbarItems.close@7")["platforms"] == ["ios"]        # #else: the other target
    assert attrs(db, "function:appKitOnly")["platforms"] == []                     # os(macOS): not in a Catalyst app
    assert attrs(db, "function:iosAndCatalyst")["platforms"] == ["macos", "ios"]   # os(iOS): Catalyst too
    assert attrs(db, "function:haptic")["platforms"] == ["macos", "ios"]           # !os(visionOS)
    assert attrs(db, "function:haptic@6")["platforms"] == []                       # visionOS is no target here


def test_target_membership_tags_files_one_platform_builds():
    _, db = xc()
    a = attrs(db, "class:AppWidgetEntryView")
    assert a["platforms"] == ["ios"] and a["platform_expr"] == "Xcode target membership (AppWidget)"
    assert "platforms" not in attrs(db, "function:sharedTitle")      # also in the App target: everywhere
    assert "platforms" not in attrs(db, "class:URL")                  # an SDK type's extension node
    assert attrs(db, "method:URL.widgetDeepLink")["platforms"] == ["ios"]


def test_unconditioned_callers_reach_every_variant_and_nothing_is_missing():
    st, db = xc()
    e = {d: json.loads(a or "{}") for d, a in db.execute(
        "SELECT dst, attrs FROM edges WHERE src='method:ContentView.body' AND dst LIKE 'method:ToolbarItems.close%'")}
    assert e["method:ToolbarItems.close"]["variant_platforms"] == ["macos"]
    assert e["method:ToolbarItems.close@7"]["variant_platforms"] == ["ios"]
    d = st["platforms"]["divergence"]
    assert d["missing_callee"] == []
    v = next(x for x in d["variants"] if x["name"] == "ToolbarItems.close")
    assert v["covered"] == ["macos", "ios"] and [m["platforms"] for m in v["members"]] == [["macos"], ["ios"]]


def test_swiftpm_else_lists_the_other_declared_target(tmp_path):
    root = write(tmp_path / "spm", {
        "Package.swift": 'let package = Package(name: "App", platforms: [.iOS(.v17), .macOS(.v14)], targets: [])\n',
        "Sources/App/Haptics.swift": "#if os(iOS)\nimport UIKit\nfunc tap() { }\n#else\nfunc tap() {}\n#endif\n\n"
                                     "func press() { tap() }\n\n#if targetEnvironment(macCatalyst)\nfunc cat() {}\n#endif\n"})
    st, db = build(root, "spm")
    v = next(x for x in st["platforms"]["divergence"]["variants"] if x["name"] == "tap")
    assert [m["platforms"] for m in v["members"]] == [["ios"], ["macos"]]
    assert attrs(db, "function:cat")["platforms"] == []      # no Catalyst build: the native macOS target only


def test_tvos_watchos_visionos_are_their_own_targets(tmp_path):
    root = write(tmp_path / "vis", {
        "Package.swift": 'let package = Package(name: "V", platforms: [.iOS(.v17), .visionOS(.v1), .watchOS(.v10)], '
                         'targets: [])\n',
        "Sources/V/V.swift": "#if os(visionOS)\nfunc immersive() {}\n#endif\n\n#if !os(visionOS)\nfunc flat() {}\n#endif\n\n"
                             "#if canImport(UIKit)\nfunc uikit() {}\n#endif\n"})
    st, db = build(root, "vis")
    assert st["platforms"]["targets"] == ["ios", "watchos", "visionos"]
    assert attrs(db, "function:immersive")["platforms"] == ["visionos"]
    assert attrs(db, "function:flat")["platforms"] == ["ios", "watchos"]
    assert attrs(db, "function:uikit")["platforms"] == ["ios", "visionos"]
    assert PF.resolve_platform("xros") == "visionos" and PF.resolve_platform("tvOS") == "tvos"


def test_non_target_platform_filter_still_uses_the_condition(tmp_path):
    root = write(tmp_path / "spm2", {
        "Package.swift": 'let package = Package(name: "A", platforms: [.iOS(.v17)], targets: [])\n',
        "Sources/A/A.swift": "#if os(Linux)\nfunc linuxOnly() {}\n#endif\n"})
    st, db = build(root, "spm2")
    a = attrs(db, "function:linuxOnly")
    assert a["platforms"] == ["linux"] and "platforms_other" not in a     # SwiftPM: linux named by the condition
    root = write(tmp_path / "spm3", {
        "Package.swift": 'let package = Package(name: "A", platforms: [.iOS(.v17), .macOS(.v14)], targets: [])\n',
        "Sources/A/A.swift": "#if os(iOS)\nfunc ios() {}\n#else\nfunc other() {}\n#endif\n"})
    st, db = build(root, "spm3")
    a = attrs(db, "function:other")
    assert a["platforms"] == ["macos"] and "windows" in a["platforms_other"]


def test_kmp_targets_win_over_its_ios_app_project(tmp_path):
    """A Kotlin Multiplatform project's iosApp/ Xcode project is one consumer; the kotlin { } block names the targets."""
    import shutil
    shutil.copytree(XC / "App.xcodeproj", tmp_path / "iosApp" / "iosApp.xcodeproj")
    write(tmp_path, {"shared/build.gradle.kts": 'plugins { kotlin("multiplatform") }\nkotlin {\n  androidTarget()\n  iosArm64()\n}\n'})
    targets, src = PF.declared_targets(tmp_path, {}, [], {"kotlin", "swift"})
    assert targets == ["ios", "android"], src


def test_target_os_iphone_names_ios_in_the_desktop_default(tmp_path):
    marks = [{"file": "src/a.c", "line": 3, "cond": PF.Cond("c", "TARGET_OS_IPHONE", "#if TARGET_OS_IPHONE")}]
    targets, src = PF.declared_targets(tmp_path, {}, marks, {"c_cpp"})
    assert targets == ["windows", "linux", "macos", "ios"], src


def test_xcode_project_next_to_an_android_module_keeps_android(tmp_path):
    """Capacitor-style repo: ios/ Xcode project and an android/ Java module; the Java side names android."""
    import shutil
    shutil.copytree(XC / "App.xcodeproj", tmp_path / "ios" / "App.xcodeproj")
    marks = [{"file": "android/src/Plugin.java", "line": 1, "cond": PF.Cond("tree", ("atom", "platform", "android"), "android/ directory")},
             {"file": "ios/App/Plugin.swift", "line": 1, "cond": PF.Cond("tree", ("atom", "platform", "linux"), "os(Linux)")}]
    targets, src = PF.declared_targets(tmp_path, {}, marks, {"java", "swift"})
    assert targets == ["macos", "ios", "android"], src


def test_test_code_on_project_default_platforms_is_listed_apart(tmp_path):
    """#91: a test file that calls code excluded on watchOS without any `#if` can't build for watchOS, so its test target
    isn't built there. Findings from such test code (whose platforms are only the project default) go to
    `missing_callee_tests`, not `missing_callee`, and the same call from app code is still reported."""
    root = write(tmp_path / "spm", {
        "Package.swift": 'let package = Package(name: "Lib", platforms: [.iOS(.v17), .watchOS(.v10)], targets: [])\n',
        "Sources/Lib/Cache.swift": "#if !os(watchOS)\nfunc diskCache() {}\n#endif\n",
        "Sources/Lib/Use.swift": "func load() { diskCache() }\n",
        "Tests/LibTests/CacheTests.swift": "import XCTest\nfinal class CacheTests: XCTestCase {\n"
                                           "  func testDisk() { diskCache() }\n}\n"})
    st, db = build(root, "spm")
    d = st["platforms"]["divergence"]
    assert [m["at"] for m in d["missing_callee"]] == ["Sources/Lib/Use.swift:1"]
    t = d["missing_callee_tests"]
    assert [(m["at"], m["missing_on"], m["platform_source"]) for m in t] == [
        ("Tests/LibTests/CacheTests.swift:3", ["watchos"], "project default (test target)")]
    assert d["counts"]["missing_callee"] == 1 and d["counts"]["missing_callee_tests"] == 1
    from cg_code_graph.core.store import GraphStore
    g = GraphStore(db.execute("PRAGMA database_list").fetchone()[2])
    txt = PF.render_divergence(PF.divergence(g))
    assert "FROM TEST CODE WHOSE PLATFORMS ARE THE PROJECT DEFAULT: 1" in txt
    only = PF.divergence(g, kind="missing_callee")
    assert only["missing_callee_tests"] == [] and len(only["missing_callee"]) == 1

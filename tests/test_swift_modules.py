"""SwiftPM module visibility (#90): code in a package target binds only to its own module and the targets it depends
on (local `.product(name:package:)` included, transitively), never to app, extension, preview or test code; app code
sees every package; a test or app conformer is still reached through a protocol the package declares."""
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.plugins.swift.packages import SwiftPM  # noqa: E402

CORE_PKG = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(
  name: "Core",
  products: [.library(name: "Core", targets: ["Core"]), .library(name: "Feature", targets: ["Feature"])],
  dependencies: [.package(path: "../Base")],
  targets: [
    .target(name: "Core", dependencies: [.product(name: "Base", package: "Base")]),
    .target(name: "Feature", dependencies: ["Core"], path: "Sources/FeatureKit"),
    .target(name: "Other"),
    .testTarget(name: "CoreTests", dependencies: ["Core"]),
  ]
)
"""
BASE_PKG = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "Base", products: [.library(name: "Base", targets: ["Base"])],
                      targets: [.target(name: "Base")])
"""

FILES = {
    "Packages/Core/Package.swift": CORE_PKG,
    "Packages/Base/Package.swift": BASE_PKG,
    "Packages/Base/Sources/Base/Log.swift": "public func baseLog(_ s: String) {}\n",
    "Packages/Core/Sources/Core/Monitor.swift": "public protocol Monitor {\n    func requestDidFinish(_ id: Int)\n}\n"
                                                "public final class Session {\n    var monitor: Monitor?\n"
                                                "    public func finish() {\n        monitor?.requestDidFinish(1)\n"
                                                "        store.previewOnly()\n        baseLog(\"x\")\n        otherOnly()\n"
                                                "        helperFromTests()\n    }\n    func each(_ ms: [Monitor]) { ms.forEach { $0.requestDidFinish(2) } }\n"
                                                "    var store = Store()\n}\n"
                                                "public struct Store {}\n",
    "Packages/Core/Sources/FeatureKit/Feature.swift": "import Core\nfunc feature() { Session().finish(); baseLog(\"f\") }\n",
    "Packages/Core/Sources/Other/Other.swift": "func otherOnly() {}\n",
    "Packages/Core/Tests/CoreTests/CoreTests.swift": "import XCTest\n@testable import Core\nfunc helperFromTests() {}\n"
                                                    "final class Spy: Monitor {\n    func requestDidFinish(_ id: Int) {}\n}\n"
                                                    "final class CoreTests: XCTestCase {\n    func testFinish() { Session().finish() }\n}\n",
    "App/Previews/StorePreview.swift": "import Core\nextension Store {\n    func previewOnly() {}\n}\n"
                                       "func preview() { Store().previewOnly(); Session().finish(); baseLog(\"p\") }\n",
}


def build(tmp: Path):
    for rel, body in FILES.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    d = Path(tempfile.mkdtemp(prefix="codegraph-spm-"))
    st = index_project(tmp, d / "g.db", "spm")
    return st, sqlite3.connect(d / "g.db")


def calls(db, src):
    return {d for (d,) in db.execute("SELECT dst FROM edges WHERE src=? AND kind IN ('CALLS','TEST_CALLS')", (src,))}


def test_swiftpm_targets_paths_and_dependencies(tmp_path):
    build(tmp_path)
    s = SwiftPM(tmp_path)
    assert s.module_of("Packages/Core/Sources/FeatureKit/Feature.swift") == "Packages/Core:Feature"   # path:
    assert s.module_of("Packages/Core/Tests/CoreTests/CoreTests.swift") == "Packages/Core:CoreTests"
    assert s.module_of("App/Previews/StorePreview.swift") is None
    assert s.visible["Packages/Core:Feature"] == {"Packages/Core:Feature", "Packages/Core:Core", "Packages/Base:Base"}
    assert s.sees("App/Previews/StorePreview.swift", "Packages/Core/Sources/Core/Monitor.swift")
    assert not s.sees("Packages/Core/Sources/Core/Monitor.swift", "App/Previews/StorePreview.swift")


def test_package_code_never_binds_to_app_or_test_code(tmp_path):
    st, db = build(tmp_path)
    got = calls(db, "method:Session.finish")
    assert "function:baseLog" in got                         # a local package product it depends on
    assert "method:Store.previewOnly" not in got             # declared only in an app preview
    assert "function:otherOnly" not in got                   # a target it does not depend on
    assert "function:helperFromTests" not in got             # the package's own test target
    assert "method:Monitor.requestDidFinish" in got
    # an unknown receiver: the test conformer of the package's protocol stays a candidate (dynamic dispatch)
    assert "method:Spy.requestDidFinish" in calls(db, "method:Session.each")
    assert calls(db, "function:preview") >= {"method:Store.previewOnly", "method:Session.finish", "function:baseLog"}
    assert "method:Session.finish" in calls(db, "method:CoreTests.testFinish")
    assert st["plugins"]["swift"]["candidates_outside_module"] >= 3


def test_no_package_manifest_changes_nothing(tmp_path):
    p = tmp_path / "App" / "A.swift"
    p.parent.mkdir(parents=True)
    p.write_text("func a() { b() }\n")
    (tmp_path / "App" / "B.swift").write_text("func b() {}\n")
    d = Path(tempfile.mkdtemp(prefix="codegraph-spm-"))
    index_project(tmp_path, d / "g.db", "app")
    assert calls(sqlite3.connect(d / "g.db"), "function:a") == {"function:b"}


def test_manifest_details(tmp_path):
    """A `.target(name:)` inside another target's dependencies is no target of its own; dependencies the manifest
    computes leave the target free to name any package target (still not app code)."""
    (tmp_path / "Package.swift").write_text(
        'import PackageDescription\nlet shared: [Target.Dependency] = ["A"]\nlet package = Package(name: "P", targets: [\n'
        '  .systemLibrary(name: "Clib", path: "Sources/CLib"),\n'
        '  .target(name: "A", dependencies: [.target(name: "Clib")]),\n'
        '  .target(name: "B", dependencies: shared),\n'
        '  .target(name: "C", dependencies: ["A"] + shared),\n'
        '  .target(name: "D", dependencies: ["A", .product(name: "X", package: "x", condition: .when(platforms: [.iOS]))]),\n'
        '])\n')
    s = SwiftPM(tmp_path)
    assert s.targets[":Clib"]["path"] == "Sources/CLib"
    assert s.visible[":A"] == {":A", ":Clib"}
    assert s.targets[":B"]["deps_unknown"] and s.targets[":C"]["deps_unknown"]
    assert not s.targets[":D"]["deps_unknown"] and s.visible[":D"] == {":D", ":A", ":Clib"}
    assert s.sees("Sources/B/b.swift", "Sources/D/d.swift") and not s.sees("Sources/B/b.swift", "App/a.swift")


def test_test_conformer_of_a_nested_protocol_is_reached(tmp_path):
    """IceCubesApp's shape: test fakes conform to `StatusEditor.AutocompleteService.Client` (several nested
    `Client` protocols exist); the qualified name resolves, so the fakes implement it and stay dispatch targets."""
    files = {
        "Package.swift": 'import PackageDescription\nlet package = Package(name: "K", targets: [\n'
                         '  .target(name: "K"), .testTarget(name: "KTests", dependencies: ["K"])])\n',
        "Sources/K/S.swift": "public enum Editor {}\nextension Editor {\n  public final class Search {\n"
                            "    public protocol Client { func find(query: String) -> [Int] }\n"
                            "    func run(client: Client) -> [Int] { client.find(query: \"a\") }\n  }\n"
                            "  public final class Post {\n    public protocol Client { func send() }\n  }\n}\n",
        "Tests/KTests/T.swift": "@testable import K\nfinal class Fake: Editor.Search.Client, Editor.Post.Client {\n"
                                "  func find(query: String) -> [Int] { [] }\n  func send() {}\n}\n",
    }
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    d = Path(tempfile.mkdtemp(prefix="codegraph-spm-"))
    index_project(tmp_path, d / "g.db", "k")
    db = sqlite3.connect(d / "g.db")
    sup = {x for (x,) in db.execute("SELECT dst FROM edges WHERE src='class:Fake' AND kind IN ('IMPLEMENTS','TEST_USES')")}
    assert sup >= {"class:Editor.Search.Client", "class:Editor.Post.Client"}
    assert "method:Fake.find" in calls(db, "method:Editor.Search.run")

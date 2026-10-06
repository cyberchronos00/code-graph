"""A type defined once per platform branch (#86): Swift `#if os(macOS) struct T { } #else struct T { } #endif` gives
two type nodes (`class:T`, `class:T@<line>`) whose members belong to their own variant, so a caller outside the `#if`
reaches both and divergence reports nothing missing. The same shape in Kotlin (`expect` / `actual`), C (`#ifdef`) and
Rust (`#[cfg]`) binds common code to every definition too."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402


def build(root: Path, files: dict, name: str):
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    d = Path(tempfile.mkdtemp(prefix="codegraph-tv-"))
    st = index_project(root, d / "g.db", name)
    return st, sqlite3.connect(d / "g.db")


def attrs(db, nid):
    r = db.execute("SELECT attrs FROM nodes WHERE id=?", (nid,)).fetchone()
    return json.loads(r[0] or "{}") if r else None


def edges(db, src, kind):
    return {d for (d,) in db.execute("SELECT dst FROM edges WHERE src=? AND kind=?", (src, kind))}


SWIFT_TOOLBAR = """#if os(macOS)
struct Toolbar {
    func show() {}
    struct Item { func tap() {} }
}
#else
struct Toolbar {
    func show() {}
    func hide() {}
    struct Item { func tap() {} }
}
#endif
"""

SWIFT_MAIN = """func run() {
    let t = Toolbar()
    t.show()
    #if os(iOS)
    t.hide()
    #endif
    Toolbar.Item().tap()
}
"""


def test_swift_type_redefined_per_branch(tmp_path):
    pytest.importorskip("tree_sitter_swift")
    st, db = build(tmp_path / "sw", {
        "Package.swift": 'let package = Package(name: "App", platforms: [.iOS(.v17), .macOS(.v14)], targets: [])\n',
        "Sources/App/Toolbar.swift": SWIFT_TOOLBAR, "Sources/App/main.swift": SWIFT_MAIN}, "sw")
    assert attrs(db, "class:Toolbar")["platforms"] == ["macos"]
    assert attrs(db, "class:Toolbar@7")["platforms"] == ["ios"]
    assert attrs(db, "method:Toolbar.show")["platforms"] == ["macos"]
    assert attrs(db, "method:Toolbar.show@8")["platforms"] == ["ios"]
    assert attrs(db, "class:Toolbar.Item@10")["platforms"] == ["ios"]          # a nested type follows its variant
    assert edges(db, "class:Toolbar@7", "CONTAINS") >= {"method:Toolbar.show@8", "method:Toolbar.hide"}
    assert "method:Toolbar.show@8" not in edges(db, "class:Toolbar", "CONTAINS")
    assert edges(db, "function:run", "INSTANTIATES") >= {"class:Toolbar", "class:Toolbar@7"}
    assert edges(db, "function:run", "CALLS") >= {"method:Toolbar.show", "method:Toolbar.show@8", "method:Toolbar.hide"}
    d = st["platforms"]["divergence"]
    assert d["missing_callee"] == [], d["missing_callee"]
    v = next(x for x in d["variants"] if x["name"] == "Toolbar.show")
    assert v["covered"] == ["macos", "ios"]


def test_swift_same_type_in_one_branch_shares_one_node(tmp_path):
    """Not a per-branch definition: a type declared again without a `#if` between (e.g. a merge leftover) and a
    method overloaded inside one variant keep their single node."""
    pytest.importorskip("tree_sitter_swift")
    st, db = build(tmp_path / "sw2", {
        "Package.swift": 'let package = Package(name: "A", platforms: [.iOS(.v17), .macOS(.v14)], targets: [])\n',
        "Sources/A/A.swift": "#if DEBUG\nstruct Log {\n    func w(_ s: String) {}\n}\n#else\nstruct Log {\n"
                             "    func w(_ s: String) {}\n    func w(_ i: Int) {}\n}\n#endif\nfunc go() { Log().w(\"x\") }\n"}, "sw2")
    ids = {i for (i,) in db.execute("SELECT id FROM nodes WHERE id LIKE '%Log%'")}
    assert ids == {"class:Log", "class:Log@6", "method:Log.w", "method:Log.w@7"}
    assert ((st.get("platforms") or {}).get("divergence") or {}).get("missing_callee", []) == []


def test_kotlin_common_code_binds_to_the_expect_class(tmp_path):
    pytest.importorskip("tree_sitter_kotlin")
    st, db = build(tmp_path / "kt", {
        "settings.gradle.kts": 'rootProject.name = "tb"\ninclude(":shared")\n',
        "shared/build.gradle.kts": 'plugins { kotlin("multiplatform") }\nkotlin {\n    androidTarget()\n    iosArm64()\n}\n',
        "shared/src/commonMain/kotlin/tb/Toolbar.kt": "package tb\n\nexpect class Toolbar() {\n    fun show()\n}\n\n"
                                                      "fun run() {\n    val t = Toolbar()\n    t.show()\n}\n",
        "shared/src/androidMain/kotlin/tb/Toolbar.android.kt": "package tb\n\nactual class Toolbar actual constructor() {\n"
                                                               "    actual fun show() {}\n    fun hide() {}\n}\n",
        "shared/src/iosMain/kotlin/tb/Toolbar.ios.kt": "package tb\n\nactual class Toolbar actual constructor() {\n"
                                                       "    actual fun show() {}\n    fun close() { show() }\n}\n\n"
                                                       "fun iosOnly() {}\n",
        "shared/src/iosTest/kotlin/tb/ToolbarTest.kt": "package tb\n\nfun testShow() { iosOnly() }\n"}, "kt")
    assert edges(db, "function:tb.run", "INSTANTIATES") == {"class:tb.Toolbar"}
    # a member called from inside an actual class is that actual's own member, never another platform's
    assert edges(db, "method:tb.Toolbar.close", "CALLS") == {"method:tb.Toolbar.show@ios"}
    # a platform's test source set builds for that platform only
    assert attrs(db, "function:tb.testShow")["platforms"] == ["ios"]
    assert edges(db, "function:tb.testShow", "TEST_CALLS") == {"function:tb.iosOnly"}
    assert edges(db, "class:tb.Toolbar", "IMPLEMENTED_BY") >= {"class:tb.Toolbar@android", "class:tb.Toolbar@ios"}
    assert st["platforms"]["divergence"]["missing_callee"] == []


def test_c_and_rust_types_per_branch(tmp_path):
    pytest.importorskip("tree_sitter_c")
    st, db = build(tmp_path / "c", {
        "CMakeLists.txt": "cmake_minimum_required(VERSION 3.10)\nproject(tb C)\nadd_library(tb src/main.c)\n",
        "src/tb.h": "#ifdef _WIN32\ntypedef struct toolbar { int hwnd; } toolbar_t;\nvoid toolbar_show(toolbar_t *t);\n"
                    "#else\ntypedef struct toolbar { int fd; } toolbar_t;\nvoid toolbar_show(toolbar_t *t);\n"
                    "void toolbar_hide(toolbar_t *t);\n#endif\n",
        "src/main.c": '#include "tb.h"\nvoid run(void) {\n    toolbar_t t;\n    toolbar_show(&t);\n#ifndef _WIN32\n'
                      "    toolbar_hide(&t);\n#endif\n}\n"}, "c")
    assert edges(db, "function:run", "USES_TYPE") >= {"typedef:toolbar_t", "typedef:toolbar_t@src/tb.h:5"}
    assert st["platforms"]["divergence"]["missing_callee"] == []
    pytest.importorskip("tree_sitter_rust")
    import os
    os.environ["CG_RUST_SCIP"] = "0"
    try:
        st, db = build(tmp_path / "rs", {
            "Cargo.toml": '[package]\nname = "tb"\nversion = "0.1.0"\nedition = "2021"\n',
            "src/lib.rs": "#[cfg(windows)]\npub struct Toolbar { hwnd: u32 }\n#[cfg(windows)]\nimpl Toolbar {\n"
                          "    pub fn new() -> Self { Toolbar { hwnd: 0 } }\n}\n#[cfg(unix)]\npub struct Toolbar { fd: i32 }\n"
                          "#[cfg(unix)]\nimpl Toolbar {\n    pub fn new() -> Self { Toolbar { fd: 0 } }\n}\n"
                          "pub fn run() {\n    let _t = Toolbar::new();\n}\n"}, "rs")
    finally:
        os.environ.pop("CG_RUST_SCIP", None)
    assert edges(db, "function:tb::run", "CALLS") == {"method:tb::Toolbar::new", "method:tb::Toolbar::new@11"}
    assert st["platforms"]["divergence"]["missing_callee"] == []


def test_swift_branches_with_comments_imports_and_several_declarations(tmp_path):
    """Kingfisher's shape: a comment between `#else` and the second type; a branch with two functions (the second
    one is a variant too); an unconditional overload after an unrelated `#if DEBUG` block stays one node."""
    pytest.importorskip("tree_sitter_swift")
    st, db = build(tmp_path / "sw3", {
        "Package.swift": 'let package = Package(name: "A", platforms: [.iOS(.v17), .macOS(.v14)], targets: [])\n',
        "Sources/A/T.swift": "#if os(iOS)\nimport UIKit\n/// docs\npublic enum Transition {\n    case none\n    case flip\n}\n"
                             "func a() {}\nfunc b() {}\n#else\n// placeholder on macOS\npublic enum Transition {\n"
                             "    case none\n}\nfunc a() {}\nfunc b() {}\n#endif\n\nfunc f(_ x: Int) {}\n#if DEBUG\n"
                             "func dbg() {}\n#endif\nfunc f(_ s: String) {}\n\nfunc use() { _ = Transition.none; a(); b(); f(1) }\n"},
        "sw3")
    assert attrs(db, "class:Transition@12")["platforms"] == ["macos"]
    assert attrs(db, "function:b@16")["platforms"] == ["macos"]
    assert db.execute("SELECT count(*) FROM nodes WHERE id LIKE 'function:f%'").fetchone()[0] == 1
    assert st["platforms"]["divergence"]["missing_callee"] == []


def test_swift_variant_members_from_a_protocol_or_the_sdk_superclass(tmp_path):
    """SwiftUIX / Kingfisher shapes: the macOS variant of a type gets `isShown` from NSPopover (the protocol both
    conform to requires it) and its `init()` from its SDK superclass; neither is missing there."""
    pytest.importorskip("tree_sitter_swift")
    st, db = build(tmp_path / "sw4", {
        "Package.swift": 'let package = Package(name: "A", platforms: [.iOS(.v17), .macOS(.v14)], targets: [])\n',
        "Sources/A/P.swift": "protocol PopoverType {\n    var isShown: Bool { get }\n}\n#if os(iOS)\n"
                             "open class Popover: NSObject, PopoverType {\n    public var isShown: Bool { false }\n}\n"
                             "#elseif os(macOS)\nopen class Popover: NSPopover, PopoverType {\n}\n#endif\n\n"
                             "open class ImageView: PlatformImageView {\n#if os(macOS)\n    init() { super.init(frame: .zero) }\n"
                             "#endif\n}\n\nfunc use(_ p: Popover) -> Bool {\n    _ = ImageView()\n    return p.isShown\n}\n"}, "sw4")
    assert attrs(db, "class:Popover@9")["external_supers"] == ["NSPopover"]
    assert "isShown" in attrs(db, "class:PopoverType")["property_requirements"]
    assert st["platforms"]["divergence"]["missing_callee"] == []


def test_swift_parenthesised_conditions():
    pytest.importorskip("tree_sitter_swift")
    from cg_code_graph.plugins.swift.plugin import SwiftPlugin
    p = object.__new__(SwiftPlugin)
    p.mac = None
    ios, tvos = ("atom", "platform", "ios"), ("atom", "platform", "tvos")
    assert p._os_expr("(os(iOS) && canImport(CoreTelephony)) || os(tvOS)") == ("any", [ios, tvos])
    assert p._os_expr("os(iOS) && DEBUG") == ios
    assert p._os_expr("!(os(iOS) && DEBUG)") is None          # leaving DEBUG out under a `!` would claim too much
    assert p._os_expr("DEBUG || os(iOS)") is None
    assert p._os_expr("!(os(iOS) || os(tvOS))") == ("not", ("any", [ios, tvos]))
    assert p._os_expr("swift(>=5.9) && os(iOS) // note") == ios

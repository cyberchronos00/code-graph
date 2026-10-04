"""#68: `navigationDestination(for: T.self) { switch ... }` matched to `NavigationLink(value: T.x(...))` and
`router.navigate(to: .x)`; URLComponents endpoints."""
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_swift")
from codegraph.indexer import index_project  # noqa: E402

FIX = Path(__file__).resolve().parent / "swift_nav_values"


def test_value_navigation(tmp_path):
    index_project(FIX, tmp_path / "g.db", "nav")
    c = sqlite3.connect(tmp_path / "g.db")
    nav = {(s, d, ln, how) for s, d, ln, how in c.execute(
        "SELECT src, dst, line, json_extract(attrs, '$.how') FROM edges WHERE kind='NAVIGATES_TO'")}
    body = "method:ListsView.body"
    assert {(d, ln) for s, d, ln, _ in nav if s == body} == {
        ("page:swift:TimelineView", 52),        # NavigationLink(value: RouterDestination.list(...)), shared case
        ("page:swift:AccountDetailView", 53),   # navigate(to: .accountDetail(...)): one destination type has it
        ("page:swift:AboutView", 54),           # .about: OtherRoute's `default:` branch
        ("page:swift:SettingsView", 56),        # OtherRoute.settings; the untyped `.settings` (55) is ambiguous
    }
    router = {d for s, d, _, how in nav if s == "method:View.withAppRouter"}
    assert router == {"page:swift:AccountDetailView", "page:swift:TimelineView", "page:swift:SettingsView"}


def test_urlcomponents_endpoints(tmp_path):
    """`URLComponents(string:)` + `.path`, and `URLComponents()` + `.scheme` / `.host` / `.path` (#68)."""
    index_project(FIX.parent / "swift_components", tmp_path / "g.db", "comp")
    c = sqlite3.connect(tmp_path / "g.db")
    calls = set(c.execute("SELECT src, dst FROM edges WHERE kind='HTTP_CALLS'"))
    assert calls == {("method:UsersAPI.user", "http:GET https://api.example.com/v1/users/{id}"),
                     ("method:UsersAPI.search", "http:POST https://search.example.com/search")}


def test_previews_run_as_tests(tmp_path):
    """#100: snapshot / generated preview tests reach a view through its preview provider: `X_Previews._allPreviews`
    and, in a test file, a string naming the provider (`performAccessibilityAudit(named: "X_Previews")`)."""
    import json
    from codegraph import query as Q
    from codegraph.core.store import GraphStore
    root = tmp_path / "app"
    (root / "App").mkdir(parents=True)
    (root / "PreviewTests").mkdir()
    (root / "App" / "LockScreen.swift").write_text(
        "import SwiftUI\n\nstruct LockScreen: View {\n    var body: some View { Text(\"locked\") }\n}\n\n"
        "struct LockScreen_Previews: PreviewProvider {\n    static var previews: some View {\n        LockScreen()\n    }\n}\n")
    (root / "PreviewTests" / "GeneratedPreviewTests.swift").write_text(
        "// Generated using Sourcery 2.3.0 — https://github.com/krzysztofzablocki/Sourcery\n// DO NOT EDIT\n\n"
        "import XCTest\n\nfinal class PreviewTests: XCTestCase {\n    func testLockScreen() throws {\n"
        "        for preview in LockScreen_Previews._allPreviews {\n            assertSnapshot(preview)\n        }\n    }\n\n"
        "    func testLockScreenAudit() throws {\n        performAccessibilityAudit(named: \"LockScreen_Previews\")\n    }\n"
        "\n    func assertSnapshot(_ p: Any) {}\n    func performAccessibilityAudit(named: String) {}\n}\n")
    stats = index_project(root, tmp_path / "g.db", "prev")
    st = GraphStore(tmp_path / "g.db")
    tc = Q.tests_covering(st, "LockScreen")
    names = {t.get("name") for t in tc["direct"]}
    assert {"testLockScreen", "testLockScreenAudit"} <= names, tc["direct"]
    assert stats["plugins"]["swift"]["preview_refs"] == 2
    gen = [json.loads(r["attrs"])["generated"] for r in st.q(
        "SELECT attrs FROM nodes WHERE file = 'PreviewTests/GeneratedPreviewTests.swift' AND attrs LIKE '%generated%'")]
    assert gen and all(g["test"] is True and g["reason"] == "Sourcery banner" for g in gen)


def test_availability_checks_met_by_the_deployment_target(tmp_path):
    """#100: with `platforms: [.iOS(.v17)]` an `@available(iOS 16, *)` / `if #available(iOS 15, *)` always holds:
    no attrs.available, listed as availability_always_true; a higher requirement keeps its attrs.available."""
    import json
    from codegraph.plugins.swift.plugin import deployment_targets
    root = tmp_path / "pkg"
    (root / "Sources" / "App").mkdir(parents=True)
    (root / "Package.swift").write_text(
        "// swift-tools-version:5.9\nimport PackageDescription\n\nlet package = Package(\n    name: \"App\",\n"
        "    platforms: [.iOS(.v17), .macOS(\"14.2\")],\n    targets: [.target(name: \"App\")]\n)\n")
    (root / "Sources" / "App" / "A.swift").write_text(
        "@available(iOS 16, *)\nfunc old() {}\n\n@available(iOS 18, *)\nfunc new() {}\n\nfunc run() {\n"
        "    if #available(iOS 15, *) {\n        old()\n    }\n    if #available(iOS 18, macOS 15, *) {\n        new()\n    }\n}\n")
    assert deployment_targets(root) == {"iOS": "17", "macOS": "14.2"}
    stats = index_project(root, tmp_path / "g.db", "dep")
    sw = stats["plugins"]["swift"]
    assert sw["deployment_targets"] == {"iOS": "17", "macOS": "14.2"}
    assert [(x["line"], x["check"]) for x in sw["availability_always_true"]] == [(1, "@available"), (8, "#available")]
    c = sqlite3.connect(tmp_path / "g.db")
    at = {i: json.loads(a or "{}") for i, a in c.execute("SELECT id, attrs FROM nodes WHERE id LIKE 'function:%'")}
    assert "available" not in at["function:old"] and at["function:old"]["available_declared"] == {"iOS": "16"}
    assert at["function:new"]["available"] == {"iOS": "18"}
    ed = {(d, ln): json.loads(a or "{}").get("available") for d, ln, a in c.execute(
        "SELECT dst, line, attrs FROM edges WHERE src = 'function:run' AND kind = 'CALLS'")}
    assert ed[("function:old", 9)] is None and ed[("function:new", 12)] == {"iOS": "18", "macOS": "15"}

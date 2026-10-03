"""`cg tests` keeps transitive results near the target (#87): app depth (hops through application code; test edges and
route wiring do not count) at most --max-depth, no path through an app root (`@main` type and its members, an
Android `*Activity`, --exclude-root), UI / snapshot tests in their own list, and a count of what was left out."""
import sqlite3  # noqa: F401
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

FILES = {
 "App/App.swift": "import SwiftUI\n\n@main\nstruct ShopApp: App {\n    var body: some Scene {\n        WindowGroup { Text(\"x\") }\n    }\n    func launch() -> Int { Screen().render() }\n}\n\nstruct Screen {\n    func render() -> Int { Cart().total() }\n}\n\nstruct Cart {\n    func total() -> Int { Pricing.sum([1, 2]) }\n    func checkout() -> Int { Ledger().post(total()) }\n    func checkoutAudited() -> Int { Ledger().postAudited(1) }\n}\n\nstruct Ledger {\n    func post(_ v: Int) -> Int { Audit().record(v) }\n    func postAudited(_ v: Int) -> Int { post(v) }\n}\n\nstruct Audit {\n    func record(_ v: Int) -> Int { Formatter.round(v) }\n}\n\nenum Pricing {\n    static func sum(_ xs: [Int]) -> Int { Formatter.round(xs.reduce(0, +)) }\n}\n\nenum Formatter {\n    static func round(_ v: Int) -> Int { v }\n}\n",
 "AppTests/FormatterTests.swift": "import XCTest\n@testable import App\n\nfinal class FormatterTests: XCTestCase {\n    func testRound() { XCTAssertEqual(Formatter.round(1), 1) }\n    func testSum() { XCTAssertEqual(Pricing.sum([1]), 1) }\n    func testCheckout() { XCTAssertEqual(Cart().checkout(), 3) }\n    func testAudited() { XCTAssertEqual(Cart().checkoutAudited(), 1) }\n    func testBoot() { XCTAssertEqual(ShopApp().launch(), 3) }\n    func testScreen() { XCTAssertEqual(Screen().render(), 3) }\n}\n",
 "AppUITests/LaunchTests.swift": "import XCTest\n@testable import App\n\nfinal class LaunchTests: XCTestCase {\n    func testTotalOnScreen() { _ = Cart().total() }\n}\n"
}


@pytest.fixture(scope="module")
def db():
    tmp = Path(tempfile.mkdtemp(prefix="codegraph-tests-scope-"))
    for rel, body in FILES.items():
        p = tmp / "app" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    index_project(tmp / "app", tmp / "g.db", "app")
    return tmp / "g.db"


def names(res, key):
    return [t["name"] for t in res[key]]


def test_default_scope(db):
    res = Q.tests_covering(GraphStore(str(db)), "Formatter.round")
    assert names(res, "direct") == ["testRound"]
    assert names(res, "transitive") == ["testSum", "testCheckout", "testScreen"]       # app depth 1, 3, 3
    assert [t["app_depth"] for t in res["transitive"]] == [1, 3, 3]
    assert names(res, "ui") == ["testTotalOnScreen"]                                   # an AppUITests target
    assert res["ui"][0]["class"] == "ui" and res["direct"][0]["class"] == "unit"
    assert res["omitted"] == {"deeper": 1, "through_roots": 1, "ui": 0}               # testAudited, testBoot


def test_options(db):
    st = GraphStore(str(db))
    res = Q.tests_covering(st, "Formatter.round", exclude_roots=["Screen"], unit_only=True)
    assert names(res, "transitive") == ["testSum", "testCheckout"] and res["ui"] == []
    assert res["omitted"] == {"deeper": 1, "through_roots": 2, "ui": 1}
    res = Q.tests_covering(st, "Formatter.round", near_depth=None, through_roots=True)
    assert sorted(names(res, "transitive")) == ["testAudited", "testBoot", "testCheckout", "testScreen", "testSum"]
    assert sum(res["omitted"].values()) == 0
    res = Q.tests_covering(st, "Formatter.round", near_depth=None, through_roots=True, exclude_roots=["Screen"])
    assert names(res, "transitive") == ["testSum", "testCheckout", "testAudited"]   # testBoot runs through Screen


def test_cli_and_summary(db):
    out = subprocess.run([sys.executable, "-m", "codegraph.cli", "tests", "Formatter.round", "--db", str(db),
                          "--no-paths", "--unit-only"], capture_output=True, text=True, cwd=ROOT).stdout
    assert out.splitlines()[1].startswith("tests: 1 direct, 3 nearby transitive (app depth <= 3); "
                                          "3 more not listed (1 deeper, 1 through roots, 1 ui)")
    out = subprocess.run([sys.executable, "-m", "codegraph.cli", "tests", "Formatter.round", "--db", str(db),
                          "--no-paths", "--max-depth", "0", "--through-roots"], capture_output=True, text=True,
                         cwd=ROOT).stdout
    assert "5 nearby transitive, 1 UI / snapshot (of" in out and "not listed" not in out


def test_classification():
    assert Q.test_class({"file": "App/AppUITests/LaunchTests.swift"}) == "ui"
    assert Q.test_class({"file": "app/src/androidTest/kotlin/a/MainTest.kt"}) == "ui"
    assert Q.test_class({"file": "e2e/login.spec.ts", "framework": "playwright"}) == "ui"
    assert Q.test_class({"file": "Tests/ViewSnapshotTests.swift"}) == "ui"
    assert Q.test_class({"file": "app/src/test/kotlin/a/RepoTest.kt"}) == "unit"
    assert Q.test_class({"file": "Tests/PricingTests.swift", "name": "testSum"}) == "unit"


def test_mcp_options(db, monkeypatch):
    from codegraph import mcp_server as M
    monkeypatch.setattr(M, "_st", lambda: GraphStore(db))
    out = M.tests_covering("Formatter.round", paths=False)
    assert "1 direct, 3 nearby transitive (app depth <= 3), 1 UI / snapshot; 2 more not listed" in out
    assert "== UI / SNAPSHOT" in out and "testTotalOnScreen" in out and "testBoot" not in out
    out = M.tests_covering("Formatter.round", paths=False, max_depth=0, unit_only=True, through_roots=True)
    assert "testBoot" in out and "testTotalOnScreen" not in out and "1 ui" in out
    out = M.tests_covering("Formatter.round", paths=False, exclude_roots=["Cart.checkout"])
    assert "testCheckout" not in out and "testScreen" in out and "(1 deeper, 2 through roots)" in out

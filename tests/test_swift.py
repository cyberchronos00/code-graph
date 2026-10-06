"""Swift plugin (tree-sitter heuristic layer): declarations and calls, Vapor routes, groups and guards, URLSession /
Alamofire endpoints, SwiftUI / UIKit navigation pages, entry points, `#if os(...)` platform tags, coverage, and the
SwiftUI sample linked to the Django sample (examples/bookstore-ios)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.link import link  # noqa: E402

FIX = ROOT / "tests" / "swift_fixture"
IOS = ROOT / "examples" / "bookstore-ios"
DJ = ROOT / "examples" / "bookstore-django"
_S: dict = {}


def db(name):
    if name not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-swift-"))
        if name == "fix":
            _S["fix_stats"] = index_project(FIX, d / "fix.db", "swift-fixture")
            _S[name] = d / "fix.db"
        else:
            index_project(DJ, d / "dj.db", "bookstore-django")
            _S["ios_stats"] = index_project(IOS, d / "ios.db", "bookstore-ios")
            _S["link"] = link(str(d / "dj.db"), str(d / "ios.db"), str(d / "l.db"),
                              backend_name="bookstore-django", frontend_name="bookstore-ios")
            _S[name] = d / "l.db"
    return sqlite3.connect(_S[name])


def rows(name, sql, *a):
    return db(name).execute(sql, a).fetchall()


def attrs(name, nid):
    r = rows(name, "SELECT attrs FROM nodes WHERE id=?", nid)
    return json.loads(r[0][0] or "{}") if r else None


def test_vapor_routes_groups_guards_and_handlers():
    routes = {r[0] for r in rows("fix", "SELECT id FROM nodes WHERE kind='route'")}
    assert routes == {"route:GET /health", "route:GET /v1/orders/{id}", "route:POST /admin/reindex", "route:GET /books",
                      "route:DELETE /books/{bookId}"}
    assert attrs("fix", "route:GET /v1/orders/{id}")["middleware"] == ["User.authenticator", "User.guardMiddleware"]
    assert "middleware" not in attrs("fix", "route:GET /health")
    h = rows("fix", "SELECT dst FROM edges WHERE src='route:GET /v1/orders/{id}' AND kind='ROUTES_TO'")[0][0]
    assert ("method:OrderService.find",) in rows("fix", "SELECT dst FROM edges WHERE src=? AND kind='CALLS'", h)
    assert rows("fix", "SELECT dst FROM edges WHERE src='route:DELETE /books/{bookId}' AND kind='ROUTES_TO'") == \
        [("method:BooksController.delete",)]


def test_alamofire_endpoints():
    eps = {r[0]: json.loads(r[1]) for r in rows("fix", "SELECT id, attrs FROM nodes WHERE kind='http'")}
    assert eps["http:POST https://inventory.example.com/v2/stock/{sku}"]["client"] == "alamofire"
    assert eps["http:GET /v1/ping"]["origin"] == "{base}"


def test_platform_blocks_and_variants():
    assert attrs("fix", "function:deviceName")["platforms"] == ["ios"]
    assert attrs("fix", "function:deviceName@20")["platforms"] == ["macos"]
    assert not attrs("fix", "function:sharedGreeting").get("platforms")
    assert attrs("fix", "class:OrdersViewController")["platforms"] == ["ios"]
    # canImport(AppKit / UIKit) name the platform; targetEnvironment(macCatalyst) is macOS, (simulator) no platform
    assert attrs("fix", "function:pasteboardName")["platforms"] == ["macos"]
    assert attrs("fix", "function:pasteboardName@45")["platforms"] == ["ios"]
    assert not attrs("fix", "function:isSimulator").get("platforms")
    # Package.swift platforms: [.macOS(.v13), .iOS(.v16)] are the targets, plus linux for the Vapor server dependency
    assert _S["fix_stats"]["platforms"]["targets"] == ["linux", "macos", "ios"]


def test_uikit_navigation_tests_and_entries():
    assert rows("fix", "SELECT dst FROM edges WHERE src='method:OrdersViewController.showDetail' AND kind='NAVIGATES_TO'") == \
        [("page:swift:OrderDetailController",)]
    ek = dict(rows("fix", "SELECT id, entry_kind FROM nodes WHERE entry_kind IS NOT NULL"))
    assert ek["class:OrdersViewController"] == "ui_page" and ek["method:OrderTests.testReindex"] == "test"
    assert ("method:OrderService.reindex",) in rows("fix", "SELECT dst FROM edges WHERE src='method:OrderTests.testReindex' AND kind='TEST_CALLS'")


def test_coverage_reports_swift_heuristic():
    db("fix")
    cov = [c for c in _S["fix_stats"]["coverage"]["languages"] if c["language"] == "swift"][0]
    assert cov["status"] == "heuristic" and "Xcode" in cov["reason"] and cov["files"] == 4
    assert not any(o.get("language") == "swift" for o in _S["fix_stats"]["coverage"].get("other", []))


def test_ios_sample_pages_entries_and_link():
    ek = dict(rows("ios", "SELECT id, entry_kind FROM nodes WHERE entry_kind IS NOT NULL AND (id LIKE 'class:%' OR id LIKE 'page:swift%')"))
    assert ek["class:BookstoreApp"] == "main"
    assert {k for k, v in ek.items() if k.startswith("page:")} == {"page:swift:BookListView", "page:swift:BookDetailView",
                                                                   "page:swift:CheckoutView"}
    nav = set(rows("ios", "SELECT src, dst FROM edges WHERE kind='NAVIGATES_TO'"))
    assert nav == {("method:BookListView.body", "page:swift:BookDetailView"),
                   ("method:BookDetailView.body", "page:swift:CheckoutView")}
    m = dict(rows("ios", "SELECT src, dst FROM edges WHERE kind='MATCHES_ROUTE'"))
    assert m == {"http:GET /api/books/": "route:GET /api/books/", "http:GET /api/books/{id}/": "route:GET /api/books/{book_id}/",
                 "http:POST /api/orders/": "route:POST /api/orders/"}


def test_ios_sample_path_from_swiftui_view_to_table():
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph import query as Q
    db("ios")
    text = json.dumps(Q.path_between(GraphStore(_S["ios"]), "page:swift:CheckoutView", "table:catalog_order"), default=str)
    assert "method:BookStore.checkout" in text and "http:POST /api/orders/" in text and "function:catalog.api.place_order" in text

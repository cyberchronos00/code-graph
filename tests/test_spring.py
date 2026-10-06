"""Spring facts shared by the Java and Kotlin syntax layers (cg_code_graph/plugins/jvm/spring.py)."""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_java")
from cg_code_graph.core.detect import detect  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.link import link  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402

FIX = ROOT / "tests" / "spring_fixture"
SHOP = ROOT / "examples" / "bookstore-spring"
AND = ROOT / "examples" / "bookstore-android"


def _index(tmp_path, root, name):
    db = tmp_path / f"{name}.db"
    st = index_project(root, db, name)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return st, con


def _attrs(con, nid):
    row = con.execute("SELECT attrs FROM nodes WHERE id=?", (nid,)).fetchone()
    assert row is not None, nid
    return json.loads(row["attrs"] or "{}")


def test_detection_sees_gradle_plugin_and_maven_starter():
    assert "spring" in detect(FIX / "forms")["frameworks"]
    assert "spring" in detect(FIX / "maven")["frameworks"]
    assert "spring" in detect(SHOP)["frameworks"]
    assert "spring" not in detect(ROOT / "tests" / "kotlin_fixture")["frameworks"]


def test_properties_context_path_ignores_profiles(tmp_path):
    root = tmp_path / "props"
    shutil.copytree(FIX / "maven", root)
    res = root / "src/main/resources"
    (res / "application.yml").unlink()
    (res / "application.properties").write_text("server.servlet.context-path=/store\n")
    (res / "application-dev.properties").write_text("server.servlet.context-path=/dev\n")
    (res / "application-prod.yml").write_text("server:\n  servlet:\n    context-path: /other\n")
    _, con = _index(tmp_path, root, "props-shop")
    row = con.execute("SELECT id FROM nodes WHERE kind='route'").fetchone()
    assert row["id"] == "route:GET /store/books/{id}"


def test_maven_context_path_and_preset(tmp_path):
    st, con = _index(tmp_path, FIX / "maven", "maven-shop")
    assert st["presets"]["applied"] == ["common", "java", "spring"]
    assert st["presets"]["frameworks"]["spring"] == "detected"
    row = con.execute("SELECT id, lang, attrs FROM nodes WHERE kind='route'").fetchone()
    assert row["id"] == "route:GET /shop/books/{id}"
    assert row["lang"] == "java"
    assert json.loads(row["attrs"])["framework"] == "spring"
    langs = {e["language"] for e in st["coverage"]["languages"]}
    assert "kotlin" not in langs


def test_cross_language_filter_chains(tmp_path):
    st, con = _index(tmp_path, FIX / "forms", "spring-forms")
    assert st["presets"]["applied"] == ["common", "kotlin", "java", "spring"]
    kt = _attrs(con, "route:GET /kt/items")
    jv = _attrs(con, "route:GET /jv/items")
    assert kt["middleware"] == ["authenticated"]
    assert kt["security"] == "SecurityFilterChain (src/main/java/shop/KtSecurity.java)"
    assert jv["middleware"] == ["hasRole(ADMIN)"]
    assert jv["security"] == "SecurityFilterChain (src/main/kotlin/shop/KtApi.kt)"


def test_beans_queries_clients_and_runner(tmp_path):
    _, con = _index(tmp_path, FIX / "forms", "spring-forms")
    bound = {(r["src"], r["dst"]) for r in con.execute("SELECT src, dst FROM edges WHERE kind='BOUND_TO'")}
    assert ("class:shop.Price", "class:shop.FastPrice") in bound
    assert ("class:shop.Mail", "class:shop.ApiMail") in bound
    assert con.execute(
        "SELECT 1 FROM edges WHERE src='method:shop.BookQuery.search' AND dst='table:store_books' AND kind='READS_TABLE'"
    ).fetchone()
    assert con.execute(
        "SELECT 1 FROM edges WHERE src='method:shop.BookQuery.rename' AND dst='table:store_books' AND kind='WRITES_TABLE'"
    ).fetchone()
    assert con.execute(
        "SELECT 1 FROM edges WHERE src='method:shop.BookReader.load' AND dst='table:store_books' AND kind='READS_TABLE'"
    ).fetchone()
    http = {r["id"]: json.loads(r["attrs"])["client"]
            for r in con.execute("SELECT id, attrs FROM nodes WHERE kind='http'")}
    assert http["http:GET /api/books/{id}"] == "rest-template"
    assert http["http:GET /api/orders/{id}"] == "web-client"
    assert http["http:POST /api/orders"] == "rest-client"
    assert http["http:GET https://files.example/raw/{name}"] == "rest-template"
    assert http["http:GET https://cdn.example/api/books/{id}"] == "web-client"
    assert http["http:GET https://orders.example/v1/items/{id}"] == "rest-client"
    assert con.execute("SELECT id FROM nodes WHERE id='route:GET /api/books/'").fetchone()
    assert http["http:GET https://catalog.example/api/books/{id}"] == "feign"
    assert http["http:POST /v1/sync"] == "http-exchange"
    assert con.execute("SELECT entry_kind FROM nodes WHERE id='method:shop.Boot.run'").fetchone()["entry_kind"] == "main"


def test_junit_testng_and_test_http(tmp_path):
    _, con = _index(tmp_path, FIX / "forms", "spring-forms")
    fw = {}
    for r in con.execute("SELECT id, attrs FROM nodes WHERE entry_kind='test'"):
        fw[r["id"]] = json.loads(r["attrs"] or {}).get("framework")
    assert fw["method:shop.OldTest.runs"] == "junit4"
    assert fw["method:shop.NgTest.runs"] == "testng"
    assert fw["method:shop.HttpTests.mockMvc"] == "junit5"
    vias = {json.loads(r["attrs"])["via"]: r["dst"]
            for r in con.execute("SELECT dst, attrs FROM edges WHERE kind='TEST_HTTP'")}
    assert vias["mock-mvc"] == "route:GET /kt/items"
    assert vias["web-test-client"] == "route:GET /jv/items"
    assert vias["test-rest-template"] == "route:GET /jv/items"


def test_bookstore_spring_example_and_android_link(tmp_path):
    st, con = _index(tmp_path, SHOP, "bookstore-spring")
    assert st["coverage"]["setup"]["frameworks"] == ["spring"]
    assert st["coverage"]["setup"]["presets"] == ["common", "kotlin", "java", "spring"]
    routes = {r["id"] for r in con.execute("SELECT id FROM nodes WHERE kind='route'")}
    assert routes == {
        "route:GET /api/books", "route:GET /api/books/{id}", "route:GET /api/books/{id}/availability",
        "route:POST /api/books", "route:POST /api/orders", "route:GET /api/orders/{id}",
    }
    assert _attrs(con, "route:POST /api/books")["middleware"] == ["PreAuthorize(hasRole('ADMIN'))"]
    # Same label the Kotlin extractor stores: authenticated, not authenticated().
    assert _attrs(con, "route:POST /api/orders")["middleware"] == ["authenticated"]
    imp = Q.impact(GraphStore(tmp_path / "bookstore-spring.db"), "table:store_books")
    callers = {c["id"] for c in imp["callers"]}
    assert "method:com.example.bookstore.order.OrderService.place" in callers
    entries = {(e["entry_kind"], e["name"]) for e in imp["entry_points"]}
    assert ("http_route", "POST /api/orders") in entries
    assert ("scheduled", "run") in entries
    # OrderEvents.onPlaced calls Java OrderService.find. That call stays unresolved until part C,
    # so the listener is an entry node and is not an impact entry point of store_books.
    assert con.execute(
        "SELECT entry_kind FROM nodes WHERE id='method:com.example.bookstore.order.OrderEvents.onPlaced'"
    ).fetchone()["entry_kind"] == "listener"
    assert ("listener", "onPlaced") not in entries
    assert ("class:com.example.bookstore.pricing.PricingService",
            "class:com.example.bookstore.pricing.DefaultPricingService") in {
        (r["src"], r["dst"]) for r in con.execute("SELECT src, dst FROM edges WHERE kind='BOUND_TO'")}
    adb = tmp_path / "and.db"
    index_project(AND, adb, "bookstore-android")
    out = link(str(tmp_path / "bookstore-spring.db"), str(adb), str(tmp_path / "link.db"),
               backend_name="bookstore-spring", frontend_name="bookstore-android")
    assert out["stats"]["endpoints_matched"] == 3
    pairs = set(sqlite3.connect(tmp_path / "link.db").execute(
        "SELECT src, dst FROM edges WHERE kind='MATCHES_ROUTE'"))
    assert ("http:GET /api/books/", "route:GET /api/books") in pairs
    assert ("http:POST /api/orders/", "route:POST /api/orders") in pairs
    assert ("http:GET /api/books/{bookId}/", "route:GET /api/books/{id}") in pairs

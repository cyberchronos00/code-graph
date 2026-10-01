"""Python/Django + Dart/Flutter plugins and the Dart -> Django link on the sample apps
(examples/bookstore-django, examples/bookstore-flutter).

The Flutter sample carries deliberate contract drift (documented in examples/bookstore-flutter/README.md) so the payload
checker has something to report. Dart tests are skipped when no Dart SDK is available.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.indexer import index_project  # noqa: E402
from codegraph.link import link, match_path  # noqa: E402
from codegraph.plugins.dart.plugin import find_dart  # noqa: E402

DJ = ROOT / "examples" / "bookstore-django"
FL = ROOT / "examples" / "bookstore-flutter"
_S: dict = {}
needs_dart = pytest.mark.skipif(find_dart() is None, reason="Dart SDK not found (set $DART or put dart on PATH)")


def build(dart=False) -> dict:
    if "dj" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-dj-"))
        index_project(DJ, d / "dj.db", "bookstore-django")
        _S.update(dir=d, dj=d / "dj.db")
    if dart and "fl" not in _S:
        d = _S["dir"]
        index_project(FL, d / "fl.db", "bookstore-flutter")
        _S["link"] = link(str(_S["dj"]), str(d / "fl.db"), str(d / "combined.db"),
                          backend_name="bookstore-django", frontend_name="bookstore-flutter")
        _S.update(fl=d / "fl.db", combined=d / "combined.db")
    return _S


def q(db, sql, *a):
    return sqlite3.connect(build(db != "dj")[db]).execute(sql, a).fetchall()


def attrs(db, nid):
    r = q(db, "SELECT attrs FROM nodes WHERE id=?", nid)
    return json.loads(r[0][0] or "{}") if r else None


# ------------------------------------------------------------------ django
def test_django_routes_and_entry_kinds():
    rows = dict(q("dj", "SELECT id, entry_kind FROM nodes WHERE kind='route'"))
    expect = {
        "route:GET /api/books/": "http_route", "route:GET /api/books/{book_id}/": "http_route",
        "route:POST /api/books/": "http_route", "route:POST /api/orders/": "http_route",
        "route:GET /api/orders/{order_id}/": "http_route", "route:GET /drf/reviews/": "http_route",
        "route:DELETE /drf/reviews/{pk}/": "http_route", "route:POST /drf/reviews/{pk}/upvote/": "http_route",
        "route:GET /drf/health/": "http_route", "route:GET /shop/books/{pk}/": "http_route",
        "route:ANY /shop/featured/": "http_route", "route:WS /ws/stock/": "websocket", "route:ANY /admin/": "admin_panel",
    }
    for rid, kind in expect.items():
        assert rows.get(rid) == kind, rid
    assert attrs("dj", "route:POST /api/orders/")["auth"] == "TokenAuth()"
    assert attrs("dj", "route:GET /api/books/").get("auth") is None


def test_ninja_schema_and_handler_edges():
    h = q("dj", "SELECT dst FROM edges WHERE src='route:POST /api/orders/' AND kind='ROUTES_TO'")
    assert h == [("function:catalog.api.place_order",)]
    req = attrs("dj", "route:POST /api/orders/")["request"]
    assert req["schemas"][0]["class"] == "catalog.schemas.OrderIn"
    fields = {f["name"]: f for f in attrs("dj", "class:catalog.schemas.OrderIn")["schema_fields"]}
    assert fields["customer_email"]["required"] and not fields["gift"]["required"] and fields["lines"]["many"]
    # ModelSchema: Meta.fields from the model + the declared annotation override
    bo = {f["name"]: f for f in attrs("dj", "class:catalog.schemas.BookOut")["schema_fields"]}
    assert set(bo) == {"id", "title", "price", "format", "subtitle", "author"}
    assert bo["subtitle"]["nullable"] is True


def test_python_calls_resolve_through_imports():
    e = set(q("dj", "SELECT dst, confidence FROM edges WHERE src='function:catalog.api.place_order' AND kind='CALLS'"))
    assert ("function:catalog.pricing.line_total", "exact") in e
    assert ("method:catalog.pricing.Discount.apply", "resolved") in set(
        q("dj", "SELECT dst, confidence FROM edges WHERE src='function:catalog.pricing.line_total' AND kind='CALLS'"))


def test_models_tables_columns_relations():
    tables = {r[0] for r in q("dj", "SELECT id FROM nodes WHERE kind='table'")}
    assert {"table:store_books", "table:catalog_order", "table:catalog_orderline", "table:catalog_author"} <= tables
    cols = {r[0] for r in q("dj", "SELECT id FROM nodes WHERE kind='column' AND id LIKE 'column:store_books.%'")}
    assert {"column:store_books.author_id", "column:store_books.price", "column:store_books.id"} <= cols
    assert q("dj", "SELECT count(*) FROM edges WHERE kind='HAS_RELATION'")[0][0] == 4
    w = {r[0] for r in q("dj", "SELECT dst FROM edges WHERE src='function:catalog.api.place_order' AND kind='WRITES_TABLE'")}
    assert {"table:catalog_order", "table:catalog_orderline"} <= w


def test_settings_env_celery_signals_channels_commands_admin():
    env = {r[0] for r in q("dj", "SELECT id FROM nodes WHERE kind='env'")}
    assert {"env:DJANGO_SECRET_KEY", "env:DJANGO_DEBUG", "env:ALLOWED_HOSTS", "env:STORE_CURRENCY", "env:DATABASE_URL"} <= env
    kinds = dict(q("dj", "SELECT id, entry_kind FROM nodes WHERE entry_kind IS NOT NULL AND kind != 'route'"))
    assert kinds["job:catalog.tasks.send_order_confirmation"] == "queue_job"
    assert kinds["command:import_books"] == "management_command"
    assert kinds["listener:catalog.signals.review_saved"] == "listener"
    assert kinds["admin:catalog.models.Book"] == "admin_panel"
    disp = {(s, d) for s, d in q("dj", "SELECT src, dst FROM edges WHERE kind='DISPATCHES'")}
    assert ("function:catalog.api.place_order", "job:catalog.tasks.send_order_confirmation") in disp
    assert ("function:catalog.signals.review_saved", "job:catalog.tasks.recompute_rating") in disp


# ------------------------------------------------------------------ dart / flutter
@needs_dart
def test_dart_http_calls_base_url_and_websocket():
    calls = dict(q("fl", "SELECT src, dst FROM edges WHERE kind='HTTP_CALLS'"))
    repo = "method:lib/repositories/"
    assert calls[repo + "book_repository.dart#BookRepository.fetchBook"] == "http:GET {env:API_URL}/api/books/{id}"
    assert calls[repo + "order_repository.dart#OrderRepository.placeOrder"] == "http:POST {env:API_URL}/api/orders"
    assert calls[repo + "review_repository.dart#ReviewRepository.stockUpdates"] == "http:WS {env:WS_URL}/ws/stock"
    a = json.loads(q("fl", "SELECT attrs FROM edges WHERE kind='HTTP_CALLS' AND src=?",
                     repo + "order_repository.dart#OrderRepository.placeOrder")[0][0])
    assert {k["key"] for k in a["body_keys"]} == {"customerEmail", "lines", "gift", "note"}
    bk = {k["key"]: k for k in a["body_keys"]}
    assert bk["gift"]["type"] == "bool?" and not bk["gift"]["conditional"]  # `c ? true : null` sends an explicit null
    assert bk["note"]["type"] == "String" and bk["note"]["conditional"]  # `if (note != null)` promotes, key omitted
    assert a["response_models"][0]["model"].endswith("order.dart#Order")


@needs_dart
def test_dart_models_generated_and_handwritten():
    jf = {k["key"]: k for k in attrs("fl", "class:lib/models/book.dart#Book")["json_from"]}
    assert jf["author_name"]["type"] == "String" and jf["author_name"]["nullable"] is False
    assert {k["key"] for k in attrs("fl", "class:lib/models/order.dart#Order")["json_from"]} == {"id", "status", "total_cents"}
    ev = attrs("fl", "class:lib/models/book.dart#BookFormat")["enum_values"]
    assert [v["wire"] for v in ev] == ["paperback", "hardcover", "audiobook"] and ev[0]["how"] == "@JsonValue"


@needs_dart
def test_bloc_state_flow_and_navigation():
    sf = attrs("fl", "class:lib/blocs/books/books_bloc.dart#BooksBloc")["state_flow"]
    assert [e["event"] for e in sf["events_dispatched_unhandled"]] == ["BookSelected"]
    assert [e["event"] for e in sf["handlers_never_dispatched"]] == ["RefreshBooks"]
    assert "BooksStatus.failure" in sf["emitted_not_handled_by_ui"]
    pages = dict(q("fl", "SELECT id, entry_kind FROM nodes WHERE kind='page'"))
    assert pages == {"page:dart:bookstore_app:/": "ui_page", "page:dart:bookstore_app:/books/{id}": "ui_page",
                     # nested GoRoute joined to its parent path; NoTransitionPage looked through to its child
                     "page:dart:bookstore_app:/books/{id}/reviews": "ui_page",
                     # Navigator.push through the project's `buildPageRoute(page: ...)` helper
                     "page:dart:lib/pages/author_page.dart#AuthorPage": "ui_page"}
    renders = dict(q("fl", "SELECT src, dst FROM edges WHERE kind='RENDERS' AND src LIKE 'page:%'"))
    assert renders["page:dart:bookstore_app:/books/{id}/reviews"] == "class:lib/pages/book_reviews_page.dart#BookReviewsPage"
    assert sorted(q("fl", "SELECT dst FROM edges WHERE kind='NAVIGATES_TO'")) == [
        ("page:dart:bookstore_app:/books/{id}",), ("page:dart:lib/pages/author_page.dart#AuthorPage",)]


@needs_dart
def test_dart_primary_constructors_parse(tmp_path):
    """Dart 3.13 primary constructors only parse with the experiment flag; the extractor retries with it."""
    (tmp_path / "pubspec.yaml").write_text("name: shelf_app\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "shelf.dart").write_text(
        "class Shelf(final String label, var int count) {\n"
        "  int twice() => count * 2;\n"
        "}\n\n"
        "int useShelf() => Shelf('a', 1).twice();\n")
    res = index_project(tmp_path, tmp_path / "s.db", "shelf")
    assert not res["plugins"]["dart"].get("parse_error_files")
    c = sqlite3.connect(tmp_path / "s.db")
    names = {r[0] for r in c.execute("SELECT name FROM nodes WHERE file='lib/shelf.dart'")}
    assert {"Shelf", "twice", "useShelf"} <= names
    assert c.execute("SELECT count(*) FROM edges WHERE kind='CALLS' AND src LIKE '%useShelf' "
                     "AND dst LIKE '%Shelf.twice'").fetchone()[0] == 1


# ------------------------------------------------------------------ link + payload
@needs_dart
def test_link_matches_routes_and_reports_unmatched():
    s = build(True)["link"]
    assert s["stats"]["call_sites"] == 7 and s["stats"]["call_sites_matched"] == 6
    un = [r["endpoint"] for r in s["results"] if not r["matched"]]
    assert un == ["http:GET {env:API_URL}/api/books/{bookId}/reviews"]
    m = dict(q("combined", "SELECT src, dst FROM edges WHERE kind='MATCHES_ROUTE'"))
    assert m["http:POST {env:API_URL}/drf/reviews/{reviewId}/upvote"] == "route:POST /drf/reviews/{pk}/upvote/"
    assert m["http:WS {env:WS_URL}/ws/stock"] == "route:WS /ws/stock/"


@needs_dart
def test_payload_checks_find_the_seeded_drift():
    rows = q("combined", "SELECT kind, severity, message, client_at, server_at FROM payload_checks")
    kinds = {(k, m.split(":")[0]) for k, _, m, _, _ in rows}
    got = {k for k, _ in kinds}
    assert got == {"trailing_slash", "request_case_mismatch", "request_nullability", "response_missing_key",
                   "response_nullability", "response_type", "response_enum_values"}
    nl = [r for r in rows if r[0] == "request_nullability"]
    assert len(nl) == 1 and "`gift`" in nl[0][2] and nl[0][1] == "high"
    msgs = " | ".join(r[2] for r in rows)
    assert "`customerEmail`" in msgs and "`customer_email`" in msgs
    assert "reads `author_name`" in msgs and "reads `total_cents`" in msgs
    assert "Book.`subtitle`" in msgs and "Book.`price`: client expects num, server sends str" in msgs
    assert all(r[3] and r[4] for r in rows)  # file:line evidence on both sides


def test_match_path_param_names_differ():
    assert match_path("/api/books/{id}", "/api/books/{book_id}")[0]
    assert not match_path("/api/books/{id}/reviews", "/api/books/{book_id}")[0]


def test_query_targets_python_symbols_and_files():
    from codegraph.core.store import GraphStore
    from codegraph.query import resolve_targets
    st = GraphStore.open(str(build()["dj"])) if hasattr(GraphStore, "open") else GraphStore(str(build()["dj"]))
    assert resolve_targets(st, "catalog.api.place_order") == ["function:catalog.api.place_order"]
    assert resolve_targets(st, "line_total") == ["function:catalog.pricing.line_total"]
    assert resolve_targets(st, "catalog/api.py") == ["module:catalog.api"]

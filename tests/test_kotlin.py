"""Kotlin plugin (tree-sitter heuristic layer): declarations and calls, Ktor / Spring routes and guards, Retrofit /
Ktor client / OkHttp endpoints, Compose Navigation pages, AndroidManifest entry points, KMP expect / actual and source
sets, coverage, and the Android sample linked to the Django sample (examples/bookstore-android)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_kotlin")
from codegraph.indexer import index_project  # noqa: E402
from codegraph.link import link  # noqa: E402

FIX = ROOT / "tests" / "kotlin_fixture"
AND = ROOT / "examples" / "bookstore-android"
DJ = ROOT / "examples" / "bookstore-django"
_S: dict = {}


def db(name):
    if name not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-kt-"))
        if name == "fix":
            _S["fix_stats"] = index_project(FIX, d / "fix.db", "kotlin-fixture")
            _S[name] = d / "fix.db"
        else:
            index_project(DJ, d / "dj.db", "bookstore-django")
            _S["and_stats"] = index_project(AND, d / "and.db", "bookstore-android")
            _S["link"] = link(str(d / "dj.db"), str(d / "and.db"), str(d / "l.db"),
                              backend_name="bookstore-django", frontend_name="bookstore-android")
            _S[name] = d / "l.db"
    return sqlite3.connect(_S[name])


def rows(name, sql, *a):
    return db(name).execute(sql, a).fetchall()


def attrs(name, nid):
    r = rows(name, "SELECT attrs FROM nodes WHERE id=?", nid)
    return json.loads(r[0][0] or "{}") if r else None


def test_ktor_routes_guards_and_handlers():
    routes = {r[0]: r[1] for r in rows("fix", "SELECT id, entry_kind FROM nodes WHERE kind='route'")}
    for rid in ("route:GET /health", "route:GET /v1/orders/{id}", "route:POST /admin/orders"):
        assert routes.get(rid) == "http_route", rid
    assert attrs("fix", "route:GET /v1/orders/{id}")["middleware"] == ["authenticate(jwt)"]
    assert "middleware" not in attrs("fix", "route:GET /health")
    h = rows("fix", "SELECT dst FROM edges WHERE src='route:GET /v1/orders/{id}' AND kind='ROUTES_TO'")[0][0]
    assert ("method:demo.OrderService.find",) in rows("fix", "SELECT dst FROM edges WHERE src=? AND kind='CALLS'", h)


def test_spring_routes_guards_and_entries():
    ids = {r[0] for r in rows("fix", "SELECT id FROM nodes WHERE kind='route' AND json_extract(attrs,'$.framework')='spring'")}
    assert ids == {"route:GET /api/books/{id}", "route:POST /api/books", "route:GET /api/books/search",
                   "route:POST /api/books/search"}
    assert attrs("fix", "route:POST /api/books")["middleware"] == ["PreAuthorize(hasRole('ADMIN'))"]
    assert rows("fix", "SELECT dst FROM edges WHERE src='route:GET /api/books/{id}' AND kind='ROUTES_TO'") == \
        [("method:demo.BookController.one",)]
    assert ("method:demo.BookRepo.load",) in rows("fix", "SELECT dst FROM edges WHERE src='method:demo.BookController.one' AND kind='CALLS'")
    ek = dict(rows("fix", "SELECT id, entry_kind FROM nodes WHERE id IN ('method:demo.Jobs.cleanup','method:demo.Jobs.onOrder')"))
    assert ek == {"method:demo.Jobs.cleanup": "scheduled", "method:demo.Jobs.onOrder": "listener"}


def test_http_clients():
    eps = {r[0]: json.loads(r[1]) for r in rows("fix", "SELECT id, attrs FROM nodes WHERE kind='http'")}
    assert eps["http:GET https://inventory.example.com/v2/stock/{sku}"]["client"] == "ktor"
    assert eps["http:POST /v1/ping"]["origin"] == "{base}"
    assert eps["http:DELETE https://legacy.example.com/orders/{id}"]["client"] == "okhttp"


def test_expect_actual_and_source_sets():
    impl = {r[0] for r in rows("fix", "SELECT dst FROM edges WHERE src='function:demo.platformName' AND kind='IMPLEMENTED_BY'")}
    assert impl == {"function:demo.platformName@android", "function:demo.platformName@ios"}
    assert ("function:demo.platformName",) in rows("fix", "SELECT dst FROM edges WHERE src='function:demo.greeting' AND kind='CALLS'")
    plat = attrs("fix", "function:demo.sdkLevel").get("platforms")
    assert plat and "android" in json.dumps(plat) and "ios" not in json.dumps(plat)
    assert not attrs("fix", "function:demo.greeting").get("platforms")


def test_coverage_reports_kotlin_heuristic():
    cov = [c for c in _S["fix_stats"]["coverage"]["languages"] if c["language"] == "kotlin"][0]
    assert cov["status"] == "heuristic" and cov["reason"] and cov["files"] == 7
    assert not any(o.get("language") == "kotlin" for o in _S["fix_stats"]["coverage"].get("other", []))


def test_android_sample_entries_pages_and_link():
    ek = dict(rows("and", "SELECT id, entry_kind FROM nodes WHERE entry_kind IS NOT NULL AND id LIKE '%bookstore%' OR id LIKE 'page:kotlin%'"))
    assert ek["class:com.example.bookstore.MainActivity"] == "ui_page"
    assert ek["class:com.example.bookstore.work.SyncWorker"] == "queue_job"
    assert ek["class:com.example.bookstore.work.BootReceiver"] == "listener"
    assert ek["page:kotlin:books/{bookId}"] == "ui_page"
    assert ek["page:kotlin:deeplink:https://bookstore.example.com/books"] == "ui_page"
    nav = {r[0] for r in rows("and", "SELECT dst FROM edges WHERE kind='NAVIGATES_TO'")}
    assert nav == {"page:kotlin:books/{bookId}", "page:kotlin:checkout/{bookId}"}
    m = {r[0]: r[1] for r in rows("and", "SELECT src, dst FROM edges WHERE kind='MATCHES_ROUTE'")}
    assert m == {"http:GET /api/books/": "route:GET /api/books/", "http:GET /api/books/{bookId}/": "route:GET /api/books/{book_id}/",
                 "http:POST /api/orders/": "route:POST /api/orders/"}


def test_android_sample_path_from_compose_screen_to_table():
    from codegraph.core.store import GraphStore
    from codegraph import query as Q
    db("and")
    st = GraphStore(_S["and"])
    res = Q.path_between(st, "page:kotlin:checkout/{bookId}", "table:catalog_order")
    text = json.dumps(res, default=str)
    assert "http:POST /api/orders/" in text and "function:catalog.api.place_order" in text


FACTS = ROOT / "tests" / "kotlin_facts_fixture"


def facts():
    if "facts" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-kt-"))
        _S["facts_stats"] = index_project(FACTS, d / "facts.db", "kotlin-facts")
        _S["facts"] = sqlite3.connect(d / "facts.db")
    return _S["facts"]


def _attrs(nid):
    r = facts().execute("select attrs from nodes where id=?", (nid,)).fetchone()
    assert r is not None, nid
    return json.loads(r[0] or "{}")


def _edge(src, dst, kind):
    return facts().execute("select confidence, attrs from edges where src=? and dst=? and kind=?", (src, dst, kind)).fetchone()


def test_spring_data_and_exposed_tables():
    svc = "method:facts.OwnerService"
    # Spring Data: the repository's entity table (@Table(name) or Spring Boot's snake_case default), read / write by
    # method name, inherited CrudRepository methods included
    assert _edge(f"{svc}.rename", "table:owners", "READS_TABLE")[0] == "resolved"
    assert json.loads(_edge(f"{svc}.rename", "table:owners", "WRITES_TABLE")[1])["via"] == "OwnerRepository.save"
    assert _edge(f"{svc}.typeCount", "table:pet_type", "READS_TABLE") is not None
    # Exposed: object X : IntIdTable("name") / qualified Table() (default name: the object name)
    assert _edge(f"{svc}.users", "table:app_users", "WRITES_TABLE") is not None
    assert _edge(f"{svc}.users", "table:app_users", "READS_TABLE") is not None
    assert _edge(f"{svc}.users", "table:AuditLog", "WRITES_TABLE") is not None
    assert _edge(f"{svc}.users", "table:AuditLog", "READS_TABLE") is None


def test_spring_security_filter_chain_guards():
    assert _attrs("route:GET /admin/users/{id}")["middleware"] == ["hasRole(ADMIN)"]
    assert _attrs("route:POST /api/orders")["middleware"] == ["authenticated"]      # anyRequest().authenticated()
    assert "middleware" not in _attrs("route:GET /public/health")                    # permitAll matched first
    assert _attrs("route:GET /admin/users/{id}")["security"].startswith("SecurityFilterChain")


def test_typed_navigation_and_navigation3_entries():
    for key in ("TopicRoute", "SearchKey", "ForYouKey"):
        assert _attrs(f"page:kotlin:{key}")["via"] == "compose-navigation (typed)"
    # composable<Route>(deepLinks = ...) { } and entry<Key>(metadata = ...) { }: the lambda belongs to the page
    assert _edge("page:kotlin:ForYouKey", "page:kotlin:TopicRoute", "NAVIGATES_TO")[0] == "exact"
    assert facts().execute("select count(*) from edges where src='page:kotlin:TopicRoute' and kind='CALLS' "
                           "and dst='function:facts.TopicScreen'").fetchone()[0] == 1


def test_retrofit_base_urls_and_ktor_client_builders():
    # baseUrl(BuildConfig.API_URL) resolves through buildConfigField; each interface keeps its own Retrofit base URL
    assert _attrs("http:GET /v2/topics")["origin"] == "https://api.example.com"
    assert _attrs("http:POST /login")["origin"] == "https://auth.example.com"
    # client.get { url("...") } and client.request { method = HttpMethod.Post; url("...") }
    assert _edge("method:facts.DogApi.breeds", "http:GET https://dog.example.com/api/breeds/list/all", "HTTP_CALLS")
    assert _edge("method:facts.DogApi.vote", "http:POST https://dog.example.com/api/votes", "HTTP_CALLS")


def test_ktor_type_safe_resources():
    st = _S.get("facts_stats") or (facts() and _S["facts_stats"])
    assert st["plugins"]["kotlin"]["routes_ktor_resources"] == 3
    for r in ("GET /articles", "GET /articles/{id}", "POST /articles/new"):
        a = _attrs(f"route:{r}")
        assert a["framework"] == "ktor"

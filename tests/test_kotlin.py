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


def test_suspend_lambda_expression_parses(tmp_path):
    """#81: `val b = suspend { 1 }` / `X to suspend { ... }` no longer swallows the enclosing class."""
    from codegraph.plugins.kotlin.plugin import _suspend_lambdas
    d = tmp_path / "src" / "test" / "kotlin"
    d.mkdir(parents=True)
    src = ("package demo\n\nimport kotlin.test.Test\n\nclass SuspendTest {\n    @Test\n    fun usesSuspendLambda() {\n"
           "        val block = suspend { 1 }\n        val pairs = listOf(1 to suspend { 2 }, 3 to suspend { 4 })\n    }\n\n"
           "    suspend fun keep() {}\n\n    @Test\n    fun plain() {}\n}\n")
    (d / "SuspendTest.kt").write_text(src)
    out, n = _suspend_lambdas(src.encode())
    assert n == 3 and len(out) == len(src.encode()) and b"suspend fun keep" in out
    dbp = tmp_path / "g.db"
    stats = index_project(tmp_path, dbp, "susp")
    c = sqlite3.connect(dbp)
    rows = {r[0]: (r[1], r[2]) for r in c.execute("SELECT id, line, entry_kind FROM nodes WHERE kind IN ('class', 'method')")}
    assert "class:demo.SuspendTest" in rows
    assert rows["method:demo.SuspendTest.usesSuspendLambda"] == (6, "test")
    assert rows["method:demo.SuspendTest.plain"] == (14, "test")
    assert "method:demo.SuspendTest.keep" in rows
    assert stats["plugins"]["kotlin"]["suspend_lambdas_rewritten"] == 3


def test_route_call_after_property_and_dynamic_named_call_parse(tmp_path):
    """#104: `get("/x") { }` on the line after a `val` was read as that property's getter, and a call or function
    named `dynamic` hit the Kotlin/JS type keyword; both swallowed the enclosing function. The parsed copy is
    rewritten byte for byte; names, lines and routes come from the original source."""
    from codegraph.plugins.kotlin.plugin import _keyword_calls, _route_calls
    d = tmp_path / "src" / "main" / "kotlin"
    d.mkdir(parents=True)
    src = ("package demo\n\nimport io.ktor.server.application.*\nimport io.ktor.server.response.*\n"
           "import io.ktor.server.routing.*\n\nfun Application.module() {\n    routing {\n"
           "        val greeting = \"hi\"\n        // the listing\n        get(\"/listing\") {\n"
           "            call.respondText(greeting)\n        }\n        val count: Int = 2\n"
           "        post(\"/items\") {\n            call.respondText(\"ok\")\n        }\n        dynamic()\n    }\n}\n\n"
           "fun Route.dynamic() {\n    get(\"/dyn\") {\n        call.respondText(\"d\")\n    }\n}\n\n"
           "class Holder {\n    val name: String\n        get() = \"x\"\n    var size = 0\n        set(value) { field = value }\n"
           "    val handle = mutableMapOf<String, Int>().apply {\n        set(\"k\", 1)\n    }\n}\n")
    (d / "App.kt").write_text(src)
    b = src.encode()
    out, n = _route_calls(b)
    assert n == 1 and len(out) == len(b) and b";get(\"/listing\")" in out
    assert b"get() = " in out and b"    set(value)" in out and b"        set(\"k\", 1)" in out
    out2, k = _keyword_calls(out)
    assert k == 2 and len(out2) == len(b) and b"Route.dynamiC()" in out2
    dbp = tmp_path / "g.db"
    stats = index_project(tmp_path, dbp, "acc")
    c = sqlite3.connect(dbp)
    ids = {r[0] for r in c.execute("SELECT id FROM nodes")}
    assert {"function:demo.module", "function:demo.dynamic", "class:demo.Holder"} <= ids
    assert {"route:GET /listing", "route:POST /items", "route:GET /dyn"} <= ids
    assert ("function:demo.module", "function:demo.dynamic") in set(c.execute("SELECT src, dst FROM edges WHERE kind = 'CALLS'"))
    ks = stats["plugins"]["kotlin"]
    assert ks["accessor_like_calls_rewritten"] == 1 and ks["keyword_named_calls_rewritten"] == 2
    assert ks.get("files_with_syntax_errors", 0) == 0


def test_member_reparse_keeps_the_members_around_a_parse_error(tmp_path):
    """#104: a construct the grammar lacks (here a `$$"..."` multi-dollar string) used to turn the rest of the class
    into an ERROR. Each member is now parsed on its own inside the file's skeleton; the one that still errors is
    blanked and reported as an error span, and the members around it keep their nodes, lines and calls."""
    from codegraph.plugins.kotlin.reparse import reparse_members
    import tree_sitter_kotlin as tsk
    from tree_sitter import Language, Parser
    d = tmp_path / "src" / "main" / "kotlin"
    d.mkdir(parents=True)
    src = ("package demo\n\nimport kotlin.math.max\n\nclass Sender(\n    private val name: String,\n) : Base() {\n"
           "    fun first(): Int = helper()\n\n    /** Docs. */\n    @Deprecated(\"x\")\n    fun broken(): String {\n"
           "        val t = \"text\".let { s ->\n            s.indexOf($$\"%1$s\")\n        }\n        return t.toString()\n"
           "    }\n\n    fun after(): Int {\n        return helper() + max(1, 2)\n    }\n\n"
           "    private fun helper(): Int = 1\n\n    companion object {\n        fun make() = Sender(\"a\")\n    }\n}\n\n"
           "open class Base\n\nfun topLevel() = Sender(\"b\").after()\n")
    (d / "Sender.kt").write_text(src)
    p = Parser(Language(tsk.language()))
    b = src.encode()
    t = p.parse(b)
    assert t.root_node.has_error
    nt, dropped, n = reparse_members(p, b, t)
    assert nt is not None and not nt.root_node.has_error and dropped == [(10, 17)]
    dbp = tmp_path / "g.db"
    stats = index_project(tmp_path, dbp, "rep")
    c = sqlite3.connect(dbp)
    rows = {r[0]: r[1] for r in c.execute("SELECT id, line FROM nodes WHERE kind IN ('class', 'method', 'function')")}
    for nid, line in (("class:demo.Sender", 5), ("method:demo.Sender.first", 8), ("method:demo.Sender.after", 19),
                      ("method:demo.Sender.helper", 23), ("class:demo.Base", 30), ("function:demo.topLevel", 32)):
        assert rows.get(nid) == line, nid
    assert "method:demo.Sender.broken" not in rows
    calls = set(c.execute("SELECT src, dst FROM edges WHERE kind = 'CALLS'"))
    assert ("method:demo.Sender.after", "method:demo.Sender.helper") in calls
    assert ("function:demo.topLevel", "method:demo.Sender.after") in calls
    ks = stats["plugins"]["kotlin"]
    assert ks["files_reparsed_by_member"] == 1 and ks["members_dropped_by_reparse"] == 1
    se = next(e for e in stats["coverage"]["languages"] if e["language"] == "kotlin")["syntax_errors"]
    assert se[0]["spans"] == [[10, 17]] and se[0]["lost"] == ["broken:12"]


def test_statement_suspend_lambda_parses():
    """#104: `suspend { ... }.runCatching(x)` starting a statement; `;` keeps it off the previous line's call."""
    from codegraph.plugins.kotlin.plugin import _suspend_lambdas
    src = (b"fun f() {\n    val a = g()\n    suspend {\n        h()\n    }.runCatching(a)\n    val b =\n"
           b"        suspend { 1 }\n    suspend fun k() {}\n}\n")
    out, n = _suspend_lambdas(src)
    assert n == 2 and len(out) == len(src)
    assert b"    ;       {\n        h()" in out and b"suspend fun k" in out

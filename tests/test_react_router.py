"""React Router v6/v7 and Remix: code routers, framework routes, navigation and forms."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sample import EXTRACTOR_DEPS  # noqa: E402

pytestmark = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")

EX = ROOT / "examples"
FX = ROOT / "tests" / "ts_fixtures"
_S: dict = {}


def build() -> dict:
    if _S:
        return _S
    from cg_code_graph.indexer import index_project
    from cg_code_graph.link import link
    d = Path(tempfile.mkdtemp(prefix="codegraph-rr-"))
    for name, root in (
        ("spa", FX / "react-router-spa"),
        ("flat", FX / "remix-flat"),
        ("prefix", FX / "rr-prefix"),
        ("next", FX / "next-link"),
        ("nextdep", FX / "next-rr-dep"),
        ("nextused", FX / "next-rr-used"),
        ("ex", EX / "bookstore-react-router"),
        ("dj", EX / "bookstore-django"),
    ):
        _S[name + "_stats"] = index_project(root, d / f"{name}.db", root.name)
        _S[name] = d / f"{name}.db"
    _S["linked"] = d / "linked.db"
    link(str(d / "dj.db"), str(d / "ex.db"), str(d / "linked.db"),
         backend_name="bookstore-django", frontend_name="bookstore-react-router")
    return _S


def db(name):
    return sqlite3.connect(build()[name])


def edge(name, src, kind, dst):
    r = db(name).execute(
        "SELECT confidence, attrs FROM edges WHERE src=? AND kind=? AND dst=?", (src, kind, dst)).fetchone()
    return (r[0], json.loads(r[1] or "{}")) if r else None


def ids(name, kind):
    return {r[0] for r in db(name).execute("SELECT id FROM nodes WHERE kind=?", (kind,))}


def test_detect_package_config_and_routes_file(tmp_path):
    from cg_code_graph.plugins.ts.react_router import app_directory, extractor_cfg, framework_mode, is_react_router
    assert not is_react_router(tmp_path)
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react-router-dom": "6.28.0"}}), encoding="utf-8")
    assert is_react_router(tmp_path) and not framework_mode(tmp_path)
    assert extractor_cfg(tmp_path) == {"app_dir": "app", "framework": False}
    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {"@remix-run/dev": "2.15.0"}}), encoding="utf-8")
    assert is_react_router(tmp_path) and framework_mode(tmp_path)
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "react-router.config.ts").write_text('export default { appDirectory: "src" };\n', encoding="utf-8")
    assert is_react_router(bare) and app_directory(bare) == "src"
    routes_only = tmp_path / "routes-only"
    (routes_only / "app").mkdir(parents=True)
    (routes_only / "app" / "routes.ts").write_text("export default [];\n", encoding="utf-8")
    assert is_react_router(routes_only) and not framework_mode(routes_only)


def test_code_router_nested_lazy_and_jsx():
    pages = ids("spa", "page")
    assert pages == {
        "page:react-router:/",
        "page:react-router:/about",
        "page:react-router:/absolute",
        "page:react-router:/admin",
        "page:react-router:/admin/users",
        "page:react-router:/books/:id",
        "page:react-router:/books/:id/reviews",
        "page:react-router:/dash",
        "page:react-router:/files/*",
        "page:react-router:/hashed",
        "page:react-router:/hooked",
        "page:react-router:/legacy",
        "page:react-router:/memory",
        "page:react-router:/users/:id?",
    }
    assert ids("spa", "layout") == {
        "layout:react-router:/",
        "layout:react-router:/admin",
        "layout:react-router:/books/:id",
        "layout:react-router:/dash",
        "layout:react-router:/legacy",
    }
    assert ids("spa", "route") == set()
    c, a = edge("spa", "page:react-router:/books/:id", "RENDERS", "function:src/book.tsx#Book")
    assert c == "exact"
    assert edge("spa", "page:react-router:/books/:id", "CALLS", "function:src/book.tsx#bookLoader")[1]["via"] == "loader"
    assert edge("spa", "page:react-router:/books/:id", "CALLS", "function:src/book.tsx#bookAction")[1]["via"] == "action"
    assert edge("spa", "page:react-router:/", "USES_LAYOUT", "layout:react-router:/")
    assert edge("spa", "page:react-router:/books/:id/reviews", "USES_LAYOUT", "layout:react-router:/books/:id")
    assert edge("spa", "function:src/router.tsx#Home", "NAVIGATES_TO", "page:react-router:/books/:id")[1]["via"] == "link"
    review_rows = db("spa").execute(
        "SELECT attrs FROM edges WHERE src=? AND kind='NAVIGATES_TO' AND dst=?",
        ("function:src/book.tsx#Book", "page:react-router:/books/:id/reviews")).fetchall()
    review_attrs = [json.loads(r[0]) for r in review_rows]
    assert {a["via"] for a in review_attrs} == {"navigate", "link"}
    assert {a["target"] for a in review_attrs} == {"/books/{id}/reviews"}
    assert edge("spa", "function:src/book.tsx#Book", "NAVIGATES_TO", "page:react-router:/about")[1]["target"] == "/about"
    assert edge("spa", "function:src/book.tsx#Book", "NAVIGATES_TO", "page:react-router:/hashed")[1] == {
        "via": "navigate", "target": "/hashed"}
    assert edge("spa", "function:src/book.tsx#Book", "NAVIGATES_TO", "page:react-router:/books/:id/reviews")[1]["target"] == "/books/{id}/reviews"
    assert edge("spa", "function:src/book.tsx#Book", "NAVIGATES_TO", "page:react-router:/users/:id?")[1]["target"] == "/users/5"
    users = json.loads(db("spa").execute("SELECT attrs FROM nodes WHERE id=?", ("page:react-router:/users/:id?",)).fetchone()[0])
    files = json.loads(db("spa").execute("SELECT attrs FROM nodes WHERE id=?", ("page:react-router:/files/*",)).fetchone()[0])
    absolute = json.loads(db("spa").execute("SELECT attrs FROM nodes WHERE id=?", ("page:react-router:/absolute",)).fetchone()[0])
    assert users["uri"] == "/users/{id?}"
    assert files["uri"] == "/files/{wildcard*?}"
    assert absolute["uri"] == "/absolute"
    assert "page:react-router:/dash/absolute" not in pages
    nav = build()["spa_stats"]["plugins"]["typescript"]["navigation"]
    assert nav["edges"] == 6 and nav["unresolved"] == 2
    assert build()["spa_stats"]["plugins"]["typescript"]["react_router"]["http_routes"] == 0


def test_remix_flat_routes_forms_and_fetchers():
    assert "page:app/routes/books.$id.tsx" in ids("flat", "page")
    assert "page:app/routes/_index.tsx" in ids("flat", "page")
    assert "page:app/routes/files.$.tsx" in ids("flat", "page")
    assert "page:app/routes/sitemap[.]xml.tsx" in ids("flat", "page")
    assert "page:app/routes/($lang).about.tsx" in ids("flat", "page")
    assert "layout:app/routes/_auth.tsx" in ids("flat", "layout")
    assert "layout:app/root.tsx" in ids("flat", "layout")
    assert "page:app/routes/_auth.tsx" not in ids("flat", "page")
    book = "page:app/routes/books.$id.tsx"
    assert edge("flat", book, "CALLS", "function:app/routes/books.$id.tsx#loader")[1]["via"] == "loader"
    assert edge("flat", book, "CALLS", "function:app/routes/books.$id.tsx#clientLoader")[1]["via"] == "clientLoader"
    assert edge("flat", book, "CALLS", "function:app/routes/books.$id.tsx#clientAction")[1]["via"] == "clientAction"
    assert "route:GET /books/{id}" in ids("flat", "route")
    assert "route:POST /books/{id}" in ids("flat", "route")
    assert "route:GET /client" not in ids("flat", "route")
    assert edge("flat", "route:POST /books/{id}", "ROUTES_TO", "function:app/routes/books.$id.tsx#action")
    assert edge("flat", "http:POST /books/{id}", "MATCHES_ROUTE", "route:POST /books/{id}")[1]["in_repo"] is True
    assert edge("flat", "http:GET /books/1", "MATCHES_ROUTE", "route:GET /books/{id}")
    assert "route:DELETE /books/{id}" in ids("flat", "route")
    assert "route:PUT /books/{id}" in ids("flat", "route")
    assert edge("flat", "route:DELETE /books/{id}", "ROUTES_TO", "function:app/routes/books.$id.tsx#action")
    assert edge("flat", "http:DELETE /books/{id}", "MATCHES_ROUTE", "route:DELETE /books/{id}")[1]["in_repo"] is True
    assert edge("flat", "http:PUT /books/1", "MATCHES_ROUTE", "route:PUT /books/{id}")[1]["in_repo"] is True
    assert edge("flat", "http:POST /books/{id}/edit", "MATCHES_ROUTE", "route:POST /books/{id}/edit")[1]["in_repo"] is True
    assert edge("flat", "page:app/routes/books.$id.edit.tsx", "USES_LAYOUT", "layout:app/routes/books.$id.tsx")
    assert edge("flat", "page:app/routes/_auth.login.tsx", "USES_LAYOUT", "layout:app/routes/_auth.tsx")
    assert edge("flat", "page:app/routes/concerts/trending.tsx", "USES_LAYOUT", "layout:app/routes/concerts/route.tsx")
    about = db("flat").execute("SELECT attrs FROM nodes WHERE id=?", ("page:app/routes/($lang).about.tsx",)).fetchone()
    assert json.loads(about[0])["uri"] == "/{lang?}/about"
    splat = db("flat").execute("SELECT attrs FROM nodes WHERE id=?", ("page:app/routes/files.$.tsx",)).fetchone()
    assert json.loads(splat[0])["uri"] == "/files/{wildcard*?}"
    sitemap = db("flat").execute("SELECT attrs FROM nodes WHERE id=?", ("page:app/routes/sitemap[.]xml.tsx",)).fetchone()
    assert json.loads(sitemap[0])["uri"] == "/sitemap.xml"
    inbox = db("flat").execute("SELECT attrs FROM nodes WHERE id=?", ("page:app/routes/_layout.inbox.tsx",)).fetchone()
    assert json.loads(inbox[0])["uri"] == "/inbox"
    assert "layout:app/routes/_layout.tsx" in ids("flat", "layout")
    assert "page:app/routes/_layout.tsx" not in ids("flat", "page")
    assert edge("flat", "page:app/routes/_layout.inbox.tsx", "USES_LAYOUT", "layout:app/routes/_layout.tsx")
    trending = db("flat").execute("SELECT attrs FROM nodes WHERE id=?", ("page:app/routes/concerts/trending.tsx",)).fetchone()
    assert json.loads(trending[0])["uri"] == "/concerts/trending"


def test_framework_prefix_and_app_directory():
    assert ids("prefix", "page") == {
        "page:src/routes/home.tsx", "page:src/routes/book.tsx",
        "page:src/routes/dash.tsx", "page:src/routes/login.tsx",
    }
    assert "layout:src/routes/shop.tsx" in ids("prefix", "layout")
    assert "layout:src/root.tsx" in ids("prefix", "layout")
    assert "route:GET /shop/books/{id}" in ids("prefix", "route")
    assert "route:POST /shop/books/{id}" in ids("prefix", "route")
    login = json.loads(db("prefix").execute("SELECT attrs FROM nodes WHERE id=?", ("page:src/routes/login.tsx",)).fetchone()[0])
    assert login["uri"] == "/login"
    assert "route:GET /login" in ids("prefix", "route")
    assert "route:GET /dash/login" not in ids("prefix", "route")
    assert edge("prefix", "function:src/routes/home.tsx#Home", "NAVIGATES_TO", "page:src/routes/book.tsx")[1]["target"] == "/shop/books/1"
    assert build()["prefix_stats"]["coverage"]["setup"]["frameworks"] == ["react-router"]
    assert "react-router" in build()["prefix_stats"]["coverage"]["setup"]["presets"]


def test_next_dependency_without_routes_is_not_react_router():
    assert build()["nextdep_stats"]["coverage"]["setup"]["frameworks"] == ["nextjs"]
    assert "react-router" not in build()["nextdep_stats"]["coverage"]["setup"]["presets"]
    assert "page:react-router:/rr" in ids("nextused", "page")
    frameworks = build()["nextused_stats"]["coverage"]["setup"]["frameworks"]
    assert "nextjs" in frameworks and "react-router" in frameworks


def test_next_link_href_navigates():
    rows = db("next").execute(
        "SELECT attrs FROM edges WHERE src=? AND kind='NAVIGATES_TO' AND dst=?",
        ("function:app/page.tsx#Home", "page:app/books/[id]/page.tsx")).fetchall()
    vias = {json.loads(r[0])["via"] for r in rows}
    assert vias == {"link", "push"}


def test_bookstore_example_routes_pages_and_django_link():
    st = build()["ex_stats"]
    assert st["coverage"]["languages"][0]["files"] == 8
    assert st["coverage"]["languages"][0]["status"] == "exact"
    assert st["coverage"]["setup"]["frameworks"] == ["react-router"]
    assert st["coverage"]["setup"]["presets"] == ["common", "typescript", "react-router"]
    assert ids("ex", "page") == {
        "page:app/routes/home.tsx",
        "page:app/routes/book.tsx",
        "page:app/routes/cart.tsx",
    }
    assert ids("ex", "layout") == {"layout:app/root.tsx", "layout:app/routes/shop-layout.tsx"}
    assert ids("ex", "route") == {"route:GET /", "route:GET /books/{id}", "route:POST /books/{id}", "route:GET /cart"}
    assert edge("ex", "page:app/routes/book.tsx", "CALLS", "function:app/routes/book.tsx#loader")[1]["via"] == "loader"
    assert edge("ex", "page:app/routes/book.tsx", "CALLS", "function:app/routes/book.tsx#action")[1]["via"] == "action"
    assert edge("ex", "page:app/routes/book.tsx", "USES_LAYOUT", "layout:app/routes/shop-layout.tsx")
    assert edge("ex", "function:app/routes/home.tsx#Home", "NAVIGATES_TO", "page:app/routes/book.tsx")[1]["via"] == "link"
    assert edge("ex", "function:app/routes/book.tsx#Book", "NAVIGATES_TO", "page:app/routes/cart.tsx")[1]["via"] == "navigate"
    assert edge("ex", "function:app/routes/cart.tsx#loader", "NAVIGATES_TO", "page:app/routes/home.tsx")[1]["via"] == "redirect"
    assert edge("ex", "http:POST /books/{id}", "MATCHES_ROUTE", "route:POST /books/{id}")[1]["in_repo"] is True
    assert edge("linked", "http:GET /api/books/{id}", "MATCHES_ROUTE", "route:GET /api/books/{book_id}/")
    assert edge("linked", "http:GET /api/books", "MATCHES_ROUTE", "route:GET /api/books/")
    assert edge("linked", "http:POST /api/orders", "MATCHES_ROUTE", "route:POST /api/orders/")
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph.query import impact
    from cg_code_graph.routes import render_routes, routes_report
    text = render_routes(routes_report(GraphStore(build()["ex"])), GraphStore(build()["ex"]))
    assert "GET /books/{id}" in text and "POST /books/{id}" in text and "GET /cart" in text and "GET /" in text
    assert "Book (book.tsx)" in text
    imp = impact(GraphStore(build()["linked"]), "table:store_books")
    entries = {(e["entry_kind"], e["id"]) for e in imp["entry_points"]}
    assert ("ui_page", "page:app/routes/book.tsx") in entries
    assert ("ui_page", "page:app/routes/home.tsx") in entries
    assert ("http_route", "route:GET /") in entries
    # get_book uses get_object_or_404 and does not record a table read, so the book loader
    # route is linked (MATCHES_ROUTE above) without becoming an entry for table:store_books.
    assert ("http_route", "route:GET /books/{id}") not in entries

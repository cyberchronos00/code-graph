"""NestJS / Next.js / Express-style framework layers on the TypeScript plugin, on the generic bookstore samples
(examples/bookstore-nest, examples/bookstore-next, examples/bookstore-express) and the small fixtures under
tests/ts_fixtures (Fastify, Koa, Hono, Nest URI versioning, Next basePath)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.link import match_path  # noqa: E402
from cg_code_graph.plugins.tsweb.common import express_path, next_segment  # noqa: E402
from sample import EXTRACTOR_DEPS, WEB  # noqa: E402

pytestmark = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")

EX = ROOT / "examples"
FX = ROOT / "tests" / "ts_fixtures"
_S: dict = {}


def build() -> dict:
    if _S:
        return _S
    from cg_code_graph.indexer import index_project
    from cg_code_graph.link import link
    d = Path(tempfile.mkdtemp(prefix="codegraph-tsfw-"))
    for name, root in (("nest", EX / "bookstore-nest"), ("next", EX / "bookstore-next"), ("express", EX / "bookstore-express"),
                       ("web", WEB), ("fastify", FX / "fastify-api"), ("koa", FX / "koa-api"), ("hono", FX / "hono-api"),
                       ("nest_uri", FX / "nest-uri"), ("next_base", FX / "next-basepath"),
                       ("express_tests", FX / "express-tests")):
        _S[name + "_stats"] = index_project(root, d / f"{name}.db", root.name)
        _S[name] = d / f"{name}.db"
    _S["web_nest"] = link(str(d / "nest.db"), str(d / "web.db"), str(d / "web_nest.db"), backend_name="bookstore-nest", frontend_name="bookstore-web")
    _S["next_express"] = link(str(d / "express.db"), str(d / "next.db"), str(d / "next_express.db"),
                              backend_name="bookstore-express", frontend_name="bookstore-next")
    _S.update(web_nest_db=d / "web_nest.db", next_express_db=d / "next_express.db")
    return _S


def db(name):
    return sqlite3.connect(build()[name])


def edge(name, src, kind, dst):
    r = db(name).execute("SELECT confidence, attrs FROM edges WHERE src=? AND kind=? AND dst=?", (src, kind, dst)).fetchone()
    return (r[0], json.loads(r[1] or "{}")) if r else None


def routes(name):
    return {r[0]: r[1] for r in db(name).execute(
        "SELECT n.id, e.dst FROM nodes n LEFT JOIN edges e ON e.src=n.id AND e.kind='ROUTES_TO' WHERE n.kind='route'")}


def attrs(name, nid):
    r = db(name).execute("SELECT attrs FROM nodes WHERE id=?", (nid,)).fetchone()
    return json.loads(r[0] or "{}") if r else None


# ---------------------------------------------------------------- path helpers

def test_path_helpers():
    assert express_path("/:id/movements") == "/{id}/movements"
    assert express_path("/:id?") == "/{id?}"
    assert express_path("/files/:path+") == "/files/{path*}" and express_path("/files/:path*") == "/files/{path*?}"
    assert express_path("*") == "/{wildcard*?}" and express_path("/a/*rest") == "/a/{rest*}"
    assert express_path("top/export.:format") == "/top/export.{format}"
    assert [next_segment(s) for s in ("[id]", "[...slug]", "[[...q]]", "(shop)", "@modal")] == ["{id}", "{slug*}", "{q*?}", "", ""]
    assert next_segment("_components") is None
    assert match_path("/docs/a/b", "/docs/{slug*}")[0] and not match_path("/docs", "/docs/{slug*}")[0]
    assert match_path("/search", "/search/{q*?}")[0] and match_path("/search/x/y", "/search/{q*?}")[0]


def test_suffix_match_prefers_literal_segments():
    from cg_code_graph.link import match_endpoint
    routes = [{"id": "route:GET /api/articles/feed", "method": "GET", "uri": "/api/articles/feed", "uris": [("uri", "/api/articles/feed")]},
              {"id": "route:GET /api/articles/{slug}", "method": "GET", "uri": "/api/articles/{slug}", "uris": [("uri", "/api/articles/{slug}")]}]
    assert match_endpoint("GET", "/articles/feed", routes, "unknown")["matched"][0]["route"] == "route:GET /api/articles/feed"
    assert match_endpoint("GET", "/articles/{id}", routes, "unknown")["matched"][0]["route"] == "route:GET /api/articles/{slug}"


# ---------------------------------------------------------------- NestJS

NEST_REPORTS = "method:src/reports/reports.controller.ts#ReportsController"


def test_nest_routes_global_prefix_exclude_router_module():
    r = routes("nest")
    assert r == {
        "route:GET /v1/{store}/admin/reports/top": f"{NEST_REPORTS}.top",
        "route:GET /v1/{store}/admin/reports/top/export.{format}": f"{NEST_REPORTS}.export",
        "route:DELETE /v1/{store}/admin/reports/{id}": f"{NEST_REPORTS}.remove",
        "route:POST /v1/stock/reserve": "method:src/stock/stock.controller.ts#StockController.reserve",
        "route:POST /v1/orders": "method:src/orders/orders.controller.ts#OrdersController.create",
        "route:POST /v1/admin/books": "method:src/admin/books.controller.ts#BooksController.create",
        "route:PUT /v1/admin/books/{id}": "method:src/admin/books.controller.ts#BooksController.update",
        "route:GET /health": "method:src/common/health.controller.ts#HealthController.check",
    }
    a = attrs("nest", "route:POST /v1/stock/reserve")
    assert a["guards"] == ["ThrottleGuard", "ApiKeyGuard"] and a["body_dto"] == "ReserveStockDto"
    assert a["body_fields"] == ["sku", "quantity"]
    assert attrs("nest", "route:GET /v1/{store}/admin/reports/top")["query_fields"] == ["mode", "date_from?", "category_id?"]
    assert attrs("nest", "route:POST /v1/admin/books")["router_module_path"] == "/admin"
    assert edge("nest", "route:POST /v1/stock/reserve", "USES_MIDDLEWARE", "method:src/common/api-key.guard.ts#ApiKeyGuard.canActivate")


def test_nest_di_chain_controller_service_repository_table():
    st = build()["nest_stats"]["plugins"]["typescript/nest"]
    assert st["di_unresolved"] == 0 and st["di_resolved"] >= 14
    # interface token -> provider class (useClass) -> call chain into the repository and its table
    c, a = edge("nest", "class:src/reports/reports.service.ts#ReportsService", "INJECTS",
                "class:src/reports/typeorm-report.repository.ts#TypeOrmReportRepository")
    assert c == "resolved" and a["via"] == "@Inject"
    assert edge("nest", f"{NEST_REPORTS}.top", "CALLS", "method:src/reports/reports.service.ts#ReportsService.top")
    assert edge("nest", "method:src/reports/reports.service.ts#ReportsService.top", "CALLS",
                "method:src/reports/typeorm-report.repository.ts#TypeOrmReportRepository.topSellers")[1]["via"] == ["nest-di"]
    assert edge("nest", "method:src/reports/typeorm-report.repository.ts#TypeOrmReportRepository.topSellers", "READS_TABLE", "table:orders")
    # useFactory token (Symbol) -> class constructed by the factory
    assert edge("nest", "class:src/stock/stock.service.ts#StockService", "INJECTS", "class:src/stock/warehouse.client.ts#WarehouseClient")


def test_nest_dynamic_module_and_client_tokens():
    # NotificationsModule.forRoot() returns { provide: NOTIFY_OPTIONS, useValue } (dynamic module); ClientsModule tokens
    st = build()["nest_stats"]["plugins"]["typescript/nest"]
    assert st["di_unresolved"] == 0, st["di_unresolved_samples"]
    assert edge("nest", "class:src/orders/orders.service.ts#OrdersService", "INJECTS",
                "class:src/notifications/notifications.service.ts#NotificationsService")
    # ClientProxy.emit to a pattern handled by another service -> outgoing message node
    assert edge("nest", "method:src/orders/orders.service.ts#OrdersService.place", "DISPATCHES", "message:event:order.placed")
    assert attrs("nest", "message:event:order.placed")["outgoing"] is True


def test_nest_entities_and_entry_points():
    assert edge("nest", "class:src/reports/order.entity.ts#Order", "MAPS_TO_TABLE", "table:orders")[0] == "exact"
    assert edge("nest", "class:src/audit/audit-event.schema.ts#AuditEvent", "MAPS_TO_TABLE", "table:audit_events")
    assert edge("nest", "method:src/audit/audit.service.ts#AuditService.record", "WRITES_TABLE", "table:audit_events")
    kinds = dict(db("nest").execute("SELECT id, entry_kind FROM nodes WHERE entry_kind IS NOT NULL AND kind != 'route'").fetchall())
    assert kinds["job:stock"] == "queue_job"
    assert kinds["message:ws:inventory:watch"] == "message_handler"
    assert kinds["message:event:order.shipped"] == "message_handler"
    assert kinds["schedule:src/tasks/tasks.service.ts#TasksService.nightlySync@Cron"] == "scheduled"
    assert kinds["listener:src/stock/stock.listener.ts#StockListener.onReserved"] == "listener"
    assert kinds["command:nest:sync-warehouse"] == "cli_command"   # operator-only entry point
    assert edge("nest", "method:src/stock/stock.service.ts#StockService.reserve", "DISPATCHES", "job:stock")
    assert edge("nest", "method:src/stock/stock.service.ts#StockService.reserve", "DISPATCHES", "event:stock.reserved")


def test_nest_uri_versioning():
    assert routes("nest_uri") == {"route:GET /api/v1/authors/{id}": "method:src/app.module.ts#AuthorsController.findOne",
                                  "route:GET /api/v2/authors": "method:src/app.module.ts#AuthorsController.listV2"}


def test_link_nuxt_frontend_to_nest_backend():
    res = build()["web_nest"]
    assert res["stats"]["endpoints_matched"] >= 4
    c = sqlite3.connect(build()["web_nest_db"])
    m = dict(c.execute("SELECT src, dst FROM edges WHERE kind='MATCHES_ROUTE'").fetchall())
    assert m["http:GET /api/v1/main/admin/reports/top"] == "route:GET /v1/{store}/admin/reports/top"
    assert m["http:GET /api/v1/main/admin/reports/top/export.csv"] == "route:GET /v1/{store}/admin/reports/top/export.{format}"


# ---------------------------------------------------------------- Next.js

def test_next_app_and_pages_router():
    r = routes("next")
    assert r == {
        "route:GET /api/books": "function:app/api/books/route.ts#GET",
        "route:POST /api/books": "function:app/api/books/route.ts#POST",
        "route:GET /api/books/{id}": "function:app/api/books/[id]/route.ts#GET",
        "route:DELETE /api/books/{id}": "function:app/api/books/[id]/route.ts#remove",   # export { remove as DELETE }
        "route:GET /api/search/{q*?}": "function:app/api/search/[[...q]]/route.ts#GET",
        "route:GET /api/legacy/orders": "function:pages/api/legacy/orders.ts#handler",
        "route:POST /api/legacy/orders": "function:pages/api/legacy/orders.ts#handler",
        "route:GET /api/legacy/wishlist": "function:pages/api/legacy/wishlist.ts#wishlist",   # switch (method) on a
        "route:PUT /api/legacy/wishlist": "function:pages/api/legacy/wishlist.ts#wishlist",   # destructured req.method
        "route:ACTION app/actions.ts#addToCart": "function:app/actions.ts#addToCart",
    }
    assert attrs("next", "function:app/api/books/route.ts#POST")["wrapped_by"] == "withSession"
    pages = dict(db("next").execute("SELECT id, json_extract(attrs, '$.uri') FROM nodes WHERE kind='page'").fetchall())
    assert pages["page:app/(shop)/books/[id]/page.tsx"] == "/books/{id}"           # route group dropped
    assert pages["page:app/docs/[...slug]/page.tsx"] == "/docs/{slug*}"
    assert pages["page:app/@modal/(.)books/[id]/page.tsx"] == "/books/{id}"        # intercepting route, parallel slot
    assert pages["page:pages/about.tsx"] == "/about"
    assert edge("next", "page:app/page.tsx", "USES_LAYOUT", "layout:app/layout.tsx")
    assert edge("next", "function:app/page.tsx#HomePage", "RENDERS", "function:components/BookList.tsx#BookList")


def test_next_middleware_rewrites_env_prisma():
    for nid in ("route:GET /api/books", "route:DELETE /api/books/{id}", "page:app/account/page.tsx"):
        assert edge("next", nid, "USES_MIDDLEWARE", "function:middleware.ts#middleware"), nid
    assert not edge("next", "route:GET /api/legacy/orders", "USES_MIDDLEWARE", "function:middleware.ts#middleware")
    assert attrs("next", "route:GET /api/books/{id}")["uri_variants"] == ["/catalog/{id}"]
    assert attrs("next", "env:NEXT_PUBLIC_WAREHOUSE_URL")["public"] is True
    assert edge("next", "function:app/actions.ts#addToCart", "WRITES_TABLE", "table:cart_items")
    assert edge("next", "function:lib/books.ts#listBooks", "READS_TABLE", "table:books")


def test_next_in_repo_client_links():
    c, a = edge("next", "http:DELETE /api/books/{bookId}", "MATCHES_ROUTE", "route:DELETE /api/books/{id}")
    assert c == "exact" and a["in_repo"] is True
    assert edge("next", "http:GET /api/books", "MATCHES_ROUTE", "route:GET /api/books")   # useSWR key


def test_next_base_path():
    assert routes("next_base") == {"route:ANY /store/api/ping": "function:pages/api/ping.ts#handler"}
    assert attrs("next_base", "page:app/shelf/page.tsx")["uri"] == "/store/shelf"


# ---------------------------------------------------------------- Express / Fastify / Koa / Hono

def test_express_commonjs_mount_chain():
    r = routes("express")
    assert r == {
        "route:GET /health": "function:src/app.js#app.get('/health')",
        "route:GET /v1/stock/{sku}": "function:src/controllers/stock.js#show",
        "route:POST /v1/stock/reserve": "function:src/controllers/stock.js#reserve",
        "route:GET /v1/stock/{sku}/movements": "function:src/controllers/stock.js#movements",
        "route:POST /v1/stock/{sku}/movements": "function:src/controllers/stock.js#recordMovement",
        "route:DELETE /v1/stock/reservations/{id}": "function:src/routes/stock.js#router.delete('/reservations/:id')",
        "route:GET /v1/warehouses": "function:src/controllers/warehouses.js#list",
        "route:PUT /v1/warehouses/{id}": "function:src/controllers/warehouses.js#update",
        # const router = Router().get(..).get(..) + handlers looked up through a lazy require() registry
        "route:GET /v1/reports/low-stock": "function:src/controllers/reports.js#lowStock",
        "route:GET /v1/reports/movements/daily": "function:src/controllers/reports.js#dailyMovements",
        # router.use('/suppliers', suppliersRoutes()): a factory re-exported by index.js from a .ts module that assigns
        # module.exports; handlers come from the ES module's named export through the registry
        "route:GET /v1/suppliers": "function:src/controllers/suppliers.ts#controller.list",
        "route:POST /v1/suppliers/{id}/orders": "function:src/controllers/suppliers.ts#controller.placeOrder",
    }
    assert edge("express", "route:GET /v1/stock/{sku}", "ROUTES_TO", "function:src/controllers/stock.js#show")[0] == "exact"
    assert edge("express", "route:POST /v1/stock/reserve", "ROUTES_TO", "function:src/controllers/stock.js#reserve")[0] == "resolved"
    # mount-level middleware (router.use('/warehouses', authenticate, ...)) and app-level middleware
    assert edge("express", "route:GET /v1/warehouses", "USES_MIDDLEWARE", "function:src/middleware/auth.js#authenticate")
    assert not edge("express", "route:GET /v1/stock/{sku}", "USES_MIDDLEWARE", "function:src/middleware/auth.js#authenticate")
    assert edge("express", "route:GET /health", "USES_MIDDLEWARE", "function:src/middleware/request-id.js#requestId")
    # app.use(notFound) is registered after every route in the same file: not middleware of any route
    assert not db("express").execute("SELECT 1 FROM edges WHERE kind='USES_MIDDLEWARE' AND dst LIKE '%not-found%'").fetchone()
    assert edge("express", "function:src/services/stock-service.js#reserve", "WRITES_TABLE", "table:reservations")[0] == "heuristic"
    assert edge("express", "function:src/services/stock-service.js#level", "READS_TABLE", "table:stock_items")


def test_fastify_register_prefix_hooks_and_route_options():
    r = routes("fastify")
    assert set(r) == {"route:GET /api/books", "route:POST /api/books", "route:DELETE /api/books/{id}", "route:GET /ping"}
    a = attrs("fastify", "route:POST /api/books")
    assert a["middleware"] == ["verifyToken", "adminOnly"] and a["body_fields"] == ["isbn", "title"]
    assert attrs("fastify", "route:GET /ping")["middleware"] == ["verifyToken"]


def test_koa_router_prefix_and_hono_base_path():
    assert routes("koa") == {"route:GET /shelves": "function:src/app.ts#listShelves",
                             "route:PUT /shelves/{id}": "function:src/app.ts#router.put('/:id')"}
    assert set(routes("hono")) == {"route:GET /api/authors/{id}", "route:POST /api/authors", "route:GET /api/health"}


def test_routes_built_in_test_files_are_not_application_routes():
    # test/app.spec.ts and src/__tests__/orders.test.ts build their own express() apps and routers
    assert routes("express_tests") == {"route:GET /orders": "function:src/app.ts#listOrders",
                                       "route:POST /orders": "function:src/app.ts#app.post('/orders')"}
    con = db("express_tests")
    # the test files stay in the graph as test code
    test_files = {r[0] for r in con.execute("SELECT DISTINCT file FROM nodes WHERE kind='test'")}
    assert test_files == {"test/app.spec.ts", "src/__tests__/orders.test.ts"}
    assert not con.execute("SELECT 1 FROM nodes WHERE kind='route' AND (file LIKE 'test/%' OR file LIKE '%__tests__%')").fetchone()


def test_link_next_frontend_to_express_backend_via_ky():
    c = sqlite3.connect(build()["next_express_db"])
    m = {s: (d, conf) for s, d, conf in c.execute("SELECT src, dst, confidence FROM edges WHERE kind='MATCHES_ROUTE'")}
    # ky.create({prefixUrl: process.env.NEXT_PUBLIC_WAREHOUSE_URL}): origin unknown -> heuristic (path-suffix) matches
    assert m["http:GET /v1/stock/BOOK-1"] == ("route:GET /v1/stock/{sku}", "heuristic")
    assert m["http:POST /v1/stock/reserve"] == ("route:POST /v1/stock/reserve", "heuristic")
    # OpenAPI-generated client: this.request({ path, method }) with a configured base URL
    assert m["http:GET /v1/warehouses"] == ("route:GET /v1/warehouses", "heuristic")
    assert m["http:PUT /v1/warehouses/{id}"] == ("route:PUT /v1/warehouses/{id}", "heuristic")
    # in-repo Next route handler calls stay matched inside the frontend graph
    assert m["http:DELETE /api/books/{bookId}"] == ("route:DELETE /api/books/{id}", "exact")

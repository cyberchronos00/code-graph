"""`unguarded` precision (#47 part 2d): framework-wide defaults, checks at the top of a handler, public-by-design routes,
the confidence / severity rule, and `--strict` (route-level facts only).

Fixtures (tests/unguarded_fixture, invented bookstore names): drf, express, nest, next, pyinline, laravel,
laravel_kernel, laravel_bootstrap, sockets."""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import guard_facts as F  # noqa: E402
from cg_code_graph.cli import main  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "unguarded_fixture"
TS = {"express", "nest", "next", "sockets"}
PHP = {"laravel", "laravel_kernel", "laravel_bootstrap"}


def _need(name):
    if name in TS and (shutil.which("node") is None or not (ROOT / "cg_code_graph/plugins/ts/extractor/node_modules").exists()):
        pytest.skip("TS extractor needs node and `npm ci` in cg_code_graph/plugins/ts/extractor")
    if name in PHP and shutil.which("php") is None:
        pytest.skip("php is not installed (PHP extractor)")


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("unguarded")
    cache: dict = {}

    def get(name):
        _need(name)
        if name not in cache:
            cache[name] = d / f"{name}.db"
            index_project(FIX / name, cache[name], name)
        return cache[name]
    return get


def surface(capsys, db, *args):
    main(["surface", "--db", str(db), "--format", "json", "--inbound", *args])
    return json.loads(capsys.readouterr().out)


def states(capsys, db, *args):
    return {i["node"]: i for i in surface(capsys, db, *args)["inbound"]}


def findings(capsys, db, *args):
    return {f["node"]: f for f in surface(capsys, db, "--finding", "unguarded", *args)["findings"]}


# ------------------------------------------------------------------ framework-wide defaults
def test_drf_default_permission_guards_views_but_not_an_allowany_or_empty_override(dbs, capsys):
    s = states(capsys, dbs("drf"))
    default = s["route:POST /api/shelf/"]
    assert default["guard_state"] == "guarded" and default["guards"] == ["drf-default:ShelfTokenPermissions"]
    assert s["route:GET /api/"]["guard_state"] == "guarded"                    # the router's API root view uses the default
    for node in ("route:GET /api/window/", "route:GET /api/flyers/"):         # permission_classes = [AllowAny] / []
        assert s[node]["guard_state"] == "unguarded", node
    assert set(findings(capsys, dbs("drf"))) == {"route:GET /api/window/", "route:GET /api/window/{pk}/",
                                                 "route:GET /api/flyers/", "route:GET /api/flyers/{pk}/"}


def test_drf_default_shows_in_cg_routes(dbs, capsys):
    main(["routes", "--db", str(dbs("drf")), "--missing", "drf-default"])
    out = capsys.readouterr().out
    assert "/api/window/" in out and "/api/shelf/" not in out
    main(["routes", "--db", str(dbs("drf")), "--unguarded", "--strict"])
    assert "/api/shelf/" in capsys.readouterr().out                           # strict: route-level facts only


def test_laravel_group_middleware_and_a_route_outside_it(dbs, capsys):
    s = states(capsys, dbs("laravel"))
    assert s["route:POST /shelves"]["guard_state"] == "guarded"
    assert s["route:GET /shelves"]["guard_state"] == "guarded"
    assert s["route:POST /pamphlets"]["guard_state"] == "unguarded"
    assert s["route:GET /pamphlets"]["guard_state"] == "unguarded"


@pytest.mark.parametrize("name, guard", [("laravel_kernel", "laravel-group:api:auth:sanctum"),
                                         ("laravel_bootstrap", "laravel-group:api:EnsureApiToken")])
def test_laravel_global_and_group_middleware_from_kernel_and_bootstrap(dbs, capsys, name, guard):
    s = states(capsys, dbs(name))
    assert s["route:POST /parcels"]["guard_state"] == "guarded" and s["route:POST /parcels"]["guards"] == [guard]
    assert s["route:GET /storefront"]["guard_state"] == "unguarded"           # the web group has no auth middleware
    strict = states(capsys, dbs(name), "--strict")
    assert strict["route:POST /parcels"]["guard_state"] == "unguarded"


def test_nest_app_guard_and_public_opt_out(dbs, capsys):
    s = states(capsys, dbs("nest"))
    assert s["route:GET /books"]["guard_state"] == "guarded" and s["route:GET /books"]["guards"] == ["JwtAuthGuard"]
    assert s["route:POST /books"]["guard_state"] == "guarded"
    pub = s["route:GET /books/featured"]
    assert pub["guard_state"] == "public" and "@Public()" in pub["public"]
    strict = states(capsys, dbs("nest"), "--strict")
    assert strict["route:GET /books/featured"]["guard_state"] == "guarded"    # strict does not read opt-outs


def test_express_app_use_order_and_mount_path(dbs, capsys):
    s = states(capsys, dbs("express"))
    assert s["route:POST /orders"]["guard_state"] == "guarded"                # after app.use(requireLogin)
    assert s["route:GET /staff/reports"]["guard_state"] == "guarded"          # router.use(requireLogin)
    assert s["route:GET /catalog/open"]["guard_state"] == "unguarded"         # registered before app.use(requireLogin)
    assert s["route:GET /admin/orders"]["guard_state"] == "guarded"           # app.use('/admin', requireStaffLogin)
    assert s["route:GET /shelf-notes"]["guard_state"] == "unguarded"          # outside the mount path


def test_express_session_alone_is_not_an_auth_guard(dbs, capsys):
    s = states(capsys, dbs("express"))
    for node in ("route:POST /login", "route:POST /signup", "route:GET /healthz"):
        assert "session()" in s[node]["guards"] and s[node]["guard_state"] == "public", node
    assert s["route:GET /catalog/open"]["guards"] == ["session()"] and s["route:GET /catalog/open"]["guard_state"] == "unguarded"


# ------------------------------------------------------------------ checks at the top of a handler
def test_typescript_early_return_after_get_server_session_and_a_role_check(dbs, capsys):
    s = states(capsys, dbs("next"))
    assert s["route:GET /api/orders"]["guard_state"] == "inline-guarded"
    assert s["route:GET /api/orders"]["guards"] == ["inline-check:session"]
    role = s["route:GET /api/admin/export"]
    assert role["guard_state"] == "inline-guarded" and role["guards"][0].startswith("inline-check:")
    assert s["route:GET /api/helper"]["guard_state"] == "unguarded"
    assert s["route:POST /api/stock"]["guard_state"] == "unguarded"
    assert states(capsys, dbs("next"), "--strict")["route:GET /api/orders"]["guard_state"] == "unguarded"


def test_python_early_raise_on_missing_user_permission_and_staff(dbs, capsys):
    s = states(capsys, dbs("pyinline"))
    for node in ("route:ANY /orders/", "route:ANY /ledger/", "route:ANY /reports/"):
        assert s[node]["guard_state"] == "inline-guarded", node
    assert s["route:ANY /catalog/"]["guard_state"] == "unguarded"
    assert s["route:ANY /sitemap.xml"]["guard_state"] == "public"


def test_php_abort_unless_auth_check(dbs, capsys):
    s = states(capsys, dbs("laravel"))
    assert s["route:POST /stock"]["guard_state"] == "inline-guarded"
    assert s["route:POST /stock"]["guards"] == ["auth()->check()"]
    assert s["route:GET /audit"]["guard_state"] == "unguarded"


def test_socketio_connect_check_guards_its_namespace_and_handler_checks(dbs, capsys):
    s = states(capsys, dbs("sockets"))
    assert s["endpoint:socketio:/members#shelf:join"]["guard_state"] == "inline-guarded"
    assert s["endpoint:socketio:/members#shelf:join"]["guards"] == ["socket-connect:connection"]
    assert s["endpoint:socketio:/carts#cart:update"]["guard_state"] == "inline-guarded"      # first statements check the user
    assert s["endpoint:socketio:/promo#promo:ping"]["guard_state"] == "unguarded"
    assert states(capsys, dbs("sockets"), "--strict")["endpoint:socketio:/members#shelf:join"]["guard_state"] == "unguarded"


# ------------------------------------------------------------------ findings: public routes, confidence and severity
def test_public_routes_are_not_findings_and_the_rest_keep_their_severity(dbs, capsys):
    f = findings(capsys, dbs("express"))
    assert "route:POST /login" not in f and "route:GET /healthz" not in f
    assert set(f) == {"route:GET /catalog/open", "route:GET /shelf-notes"}
    assert f["route:GET /catalog/open"]["severity"] == "low"                  # no write reached


def test_write_reached_is_medium_and_unclear_is_heuristic_low(dbs, capsys):
    f = findings(capsys, dbs("pyinline"))
    assert f["route:ANY /catalog/"]["severity"] == "medium" and "reaches a write" in f["route:ANY /catalog/"]["detail"]
    assert f["route:ANY /catalog/"]["confidence"] == "resolved"
    lf = findings(capsys, dbs("laravel"))
    assert lf["route:POST /pamphlets"]["severity"] == "medium"


def test_strict_keeps_medium_and_route_level_facts(dbs, capsys):
    f = findings(capsys, dbs("express"), "--strict")
    assert f["route:GET /catalog/open"]["severity"] == "medium"
    assert "route:POST /login" in f                                           # public routes are not filtered in strict mode


def test_uncertain_call_in_the_first_lines_is_heuristic_low():
    lines = ["export async function GET() {", "  const who = await loadVisitor()", "  const rows = await db.all()",
             "  return Response.json(rows)", "}"]
    assert F.scan_body(lines, 1)["guard"] is None
    lines = ["export async function GET() {", "  const who = await fetchSessionToken()", "  return Response.json(who)", "}"]
    res = F.scan_body(lines, 1)
    assert res["guard"] is None and "fetchSessionToken" in res["unsure"]


@pytest.mark.parametrize("uri, reason", [
    ("/login", "sign-in"), ("/{?}logout/", "sign-in"), ("/api/auth/forgot-password", "sign-in"), ("/healthz", "health"),
    ("/_health", "health"), ("/ready", "health"), ("/ping", "health"), ("/robots.txt", "robots"), ("/sitemap.xml", "robots"),
    ("/favicon.ico", "robots"), ("/static/{path}", "static"), ("/.well-known/jwks.json", "jwks"), ("/oauth/token", "OAuth"),
    ("/auth/callback", "OAuth"), ("/s/{shareId}", "shared"), ("/", "site root"), ("/thumbnail/{id}/", "media"),
    ("/api/schema/swagger-ui/", "API schema"), ("/api/trpc/viewer/{trpc}", "tRPC")])
def test_public_route_table(uri, reason):
    got, hidden = F.public_reason("GET", uri)
    assert got and reason in got and hidden


def test_metrics_is_low_not_hidden_and_ordinary_routes_are_not_public():
    assert F.public_reason("GET", "/metrics") == ("metrics endpoint", False)
    for uri in ("/orders", "/admin/purge", "/pingpong-orders", "/api/users/{id}", "/loginhistory"):
        assert F.public_reason("GET", uri) == (None, True), uri
    assert F.public_reason("POST", "/public/{id}")[0] is None                  # a write under a public-looking prefix

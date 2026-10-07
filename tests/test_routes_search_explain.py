"""Route guard report (`routes`), guard-aware `search`, explanations for empty results, the compact plan check, and
"sent but not forwarded" request keys. Runs on the bundled samples (bookstore Laravel + Nuxt, Nest, Express, Django)
and tests/django_access_fixture."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from sample import build, PLANS, EXTRACTOR_DEPS, needs_php  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph import query as Q, routes as R, plans as P  # noqa: E402
from cg_code_graph.concepts import resolutions, render_resolutions  # noqa: E402

needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
_S: dict = {}


def db(name: str) -> GraphStore:
    if name in ("api", "combined"):
        return GraphStore(build()[name])
    if name not in _S:
        from cg_code_graph.indexer import index_project
        root = {"nest": ROOT / "examples" / "bookstore-nest", "express": ROOT / "examples" / "bookstore-express",
                "django": ROOT / "examples" / "bookstore-django", "access": ROOT / "tests" / "django_access_fixture"}[name]
        p = Path(tempfile.mkdtemp(prefix="codegraph-routes-")) / f"{name}.db"
        index_project(root, p, root.name)
        _S[name] = p
    return GraphStore(_S[name])


def by_route(res):
    return {i["name"]: i for i in res["items"]}


def guards(item):
    return {g["name"]: g["auth"] for g in item["guards"]}


# ------------------------------------------------------------------ routes
@needs_ts
@needs_php
def test_routes_reaching_writes_bookstore():
    st = db("combined")
    res = R.routes_report(st, writes="*")
    r = by_route(res)
    assert res["total_routes"] == 13 and set(r) == {"DELETE /v1/{store}/admin/reports/{report}", "POST /v1/admin/books",
                                                    "PUT /v1/admin/books/{id}", "POST /v1/orders",
                                                    "POST /v1/orders/{order}/refund", "POST /v1/warehouse/sync"}
    assert r["POST /v1/orders"]["has_auth"] and guards(r["POST /v1/orders"]) == {"auth:api": True}
    assert not r["DELETE /v1/{store}/admin/reports/{report}"]["has_auth"]
    assert r["POST /v1/orders/{order}/refund"]["has_auth"]
    assert r["POST /v1/orders/{order}/refund"]["inline_guards"][0]["name"] == "hasPermission('orders.refund')"
    assert r["POST /v1/warehouse/sync"]["inline_auth"] and not r["POST /v1/warehouse/sync"]["has_auth"]
    txt = R.render_routes(res, st)
    assert "6 of 13 routes" in txt and "NO AUTH" in txt and "WRITES_TABLE@SalesReportService.php:24" in txt
    assert "called from: page:app/pages/index.vue" in txt  # frontend caller on the combined graph
    assert "inline: hasPermission('orders.refund') [permission] RefundController::store" in txt
    assert "inline: ensureValidSecret (hash_equals config:bookstore.sync_secret) [secret]" in txt
    unguarded = R.routes_report(st, writes="*", unguarded=True)
    assert set(by_route(unguarded)) == set(r) - {"POST /v1/orders", "POST /v1/orders/{order}/refund", "POST /v1/warehouse/sync"}
    strict = R.routes_report(st, writes="*", unguarded=True, strict=True)
    assert "POST /v1/warehouse/sync" in by_route(strict) and "POST /v1/orders/{order}/refund" not in by_route(strict)
    missing = R.routes_report(st, writes="books", missing="auth:api")
    assert set(by_route(missing)) == {"POST /v1/admin/books", "PUT /v1/admin/books/{id}"}


@needs_ts
@needs_php
def test_routes_names_routes_hidden_by_min_confidence():
    st = db("api")
    strict = R.routes_report(st, writes="*", unguarded=True, min_conf="exact")
    loose = R.routes_report(st, writes="*", unguarded=True)
    hidden = set(by_route(loose)) - set(by_route(strict))
    assert hidden and set(strict["below_confidence"]) == hidden and loose["below_confidence"] == []
    txt = R.render_routes(strict, st)
    assert "hidden by min_confidence=exact" in txt.splitlines()[2] and next(iter(hidden)) in txt


@needs_php
def test_routes_reaching_connection_and_gated_only():
    st = db("combined")
    r = by_route(R.routes_report(st, reaches=["connection:warehouse"]))
    assert {"GET /v1/{store}/admin/inventory", "POST /v1/stock/reserve"} <= set(r)
    inv = r["GET /v1/{store}/admin/inventory"]["reaches"][0]
    assert inv["gated_only"]


@needs_ts
def test_routes_nest_guards_and_interceptors():
    st = db("nest")
    r = by_route(R.routes_report(st, writes="*"))
    assert guards(r["POST /v1/stock/reserve"])["ApiKeyGuard"] is True
    assert guards(r["DELETE /v1/{store}/admin/reports/{id}"])["AuditLogInterceptor"] is False
    assert not r["POST /v1/admin/books"]["has_auth"]
    un = by_route(R.routes_report(st, writes="*", unguarded=True))
    assert "POST /v1/stock/reserve" not in un and "POST /v1/admin/books" in un


@needs_ts
def test_routes_express_router_middleware():
    st = db("express")
    r = by_route(R.routes_report(st))
    assert guards(r["POST /v1/stock/reserve"]).get("authenticate") is True
    assert not r["GET /v1/suppliers"]["has_auth"] and "requestId" in guards(r["GET /v1/suppliers"])
    m = by_route(R.routes_report(st, missing="authenticate"))
    assert "GET /v1/suppliers" in m and "POST /v1/stock/reserve" not in m
    # a user pattern can make any guard count as auth
    assert by_route(R.routes_report(st, auth_pattern="requestId"))["GET /v1/suppliers"]["has_auth"]


def test_routes_django_ninja_auth():
    st = db("django")
    r = by_route(R.routes_report(st))
    assert guards(r["POST /api/orders/"]) == {"TokenAuth()": True}
    w = by_route(R.routes_report(st, writes="*", unguarded=True))
    assert "POST /api/books/" in w and "POST /api/orders/" not in w


def test_django_view_access_facts():
    st = db("access")
    r = by_route(R.routes_report(st))
    assert guards(r["ANY /redeem/{code}/"]) == {"login_required": True}
    assert guards(r["ANY /create/"]) == {"permission_required": True}
    assert guards(r["GET /account/"]) == {"LoginRequiredMixin": True}
    assert guards(r["GET /wrapped/"]) == {"login_required": True}  # urlconf wrapper login_required(view)
    assert guards(r["POST /api/coupons/"]) == {"IsAuthenticated": True}  # DRF permission_classes
    assert not r["GET /stats/"]["has_auth"] and not r["ANY /reset/"]["has_auth"]  # csrf_exempt is no access check


@needs_php
def test_routes_empty_result_explains():
    st = db("api")
    res = R.routes_report(st, writes="no_such_table")
    assert not res["items"]
    txt = R.render_routes(res, st)
    assert "books" in txt and "orders" in txt  # suggests the tables that exist


# ------------------------------------------------------------------ search
@needs_php
def test_search_matches_route_guards():
    st = db("api")
    res = Q.search(st, "auth")
    assert "auth:api" in res["guards"] and res["guards"]["auth:api"][0]["route"] == "route:POST /v1/orders"
    assert "auth:api" in Q.render_search(res)
    assert "no matches for 'zzz_nothing'" in Q.render_search(Q.search(st, "zzz_nothing"))
    assert "ApiKeyGuard" in Q.search(db("nest"), "ApiKey")["guards"]


# ------------------------------------------------------------------ empty results explain why
@needs_php
def test_siblings_empty_explains_and_suggests():
    st = db("api")
    res = Q.siblings(st, "StockService::reserve")
    txt = Q.render_siblings(st, "StockService::reserve", res)
    assert "no siblings found" in txt and "reserveLocal" in txt and "reserveFromWarehouse" in txt and "impact(" in txt


@needs_php
def test_writers_impact_path_empty_explain():
    st = db("api")
    assert "similar: books" in Q.explain_no_writers(st, "book")
    assert "tables: books, orders" in Q.explain_no_writers(st, "zzz")
    r = Q.impact(st, "OrderController::__construct")
    assert not r["callers"] and not r["entry_points"]
    msg = Q.explain_no_callers(st, "OrderController::__construct", r["targets"])
    assert "no recorded callers" in msg and "CONTAINS" in msg and "reaches(" in msg
    assert "no method matches" in Q.explain_no_callers(st, "Nope::nope", [])
    msg = Q.explain_no_path(st, "SalesReportService::remove", "ReportController::destroy")
    assert "other direction" in msg
    assert "matches no node" in Q.explain_no_path(st, "Nope::nope", "ReportController::destroy")


def test_cli_path_exit_code_and_message():
    d = build()["api"]
    p = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "path", "SalesReportService::remove", "ReportController::destroy",
                        "--db", str(d)], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 1 and p.stdout.startswith("no path")


# ------------------------------------------------------------------ plan check compact
@needs_ts
@needs_php
def test_plan_check_summary_is_compact():
    st = db("combined")
    plan = P.load_plan("preorders", str(PLANS))
    res = P.check(st, plan)
    full, short = P.render_check(res, max_items=30), P.render_check_summary(res, max_items=3)
    for frag in ("MISSING FROM PLAN", "REVIEW (", "STILL PRESENT", "#8 [open, NOT LINKED in plan]", "lacks auth:api", "by check:"):
        assert frag in short, frag
    assert len(short) < len(full) / 2 and "… +7 more" in short


# ------------------------------------------------------------------ sent but not forwarded
@needs_ts
@needs_php
def test_forwarding_gap_date_from():
    st = db("combined")
    gaps = Q.forwarding_gaps(st)
    g = next(g for g in gaps if g["dropped"] == ["date_from"])
    assert g["call_at"].endswith("[id].vue:10") and g["request_at"].endswith("useReports.ts:9")
    assert "date_from" not in g["request_keys"] and "category_id" in g["request_keys"]
    p = Q.path_between(st, "page:/reports/:id", "ReportController::top")
    assert any("sent but not forwarded: date_from" in n for n in Q.path_notes(st, p))
    txt = render_resolutions(resolutions(st, "date_from"))
    assert "SENT BUT NOT FORWARDED" in txt and "passes date_from" in txt
    assert "SENT BUT NOT FORWARDED" in render_resolutions(resolutions(st, "timezone"))

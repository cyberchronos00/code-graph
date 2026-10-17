"""Laravel inline guards: checks inside the action, separate from route middleware."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("php"), reason="php not installed")

from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph import routes as R  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.protocols.view import protocols  # noqa: E402

FIX = ROOT / "tests" / "inline_guards_fixture"
_S: dict = {}


def db() -> Path:
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="cg-inline-"))
        index_project(FIX, d / "g.db", "inline-guards")
        _S["db"] = d / "g.db"
    return _S["db"]


def items():
    return {i["name"]: i for i in R.routes_report(GraphStore(db()))["items"]}


def names(route):
    return [(g["name"], g["kind"], g["at"], g["conditional"]) for g in items()[route]["inline_guards"]]


def test_permission_policy_role_and_ignored_after_write():
    assert names("POST /api/orders/{order}/refund") == [
        ("hasPermission('orders.refund')", "permission", "RefundController::store", False)]
    assert names("POST /api/orders/{order}/policy") == [
        ("authorize('update')", "policy", "PolicyController::update", False)]
    assert names("POST /api/orders/{order}/gate") == [
        ("Gate::authorize('refund')", "policy", "PolicyController::gate", False)]
    assert names("POST /api/orders/{order}/denies") == [
        ("Gate::denies('update')", "policy", "PolicyController::denies", True)]
    assert names("POST /api/orders/{order}/maybe") == [
        ("cannot('orders.refund')", "permission", "ConditionalController::update", True)]
    assert names("POST /api/staff/role") == [
        ("hasRole('admin')", "role", "RoleController::update", False)]
    assert names("POST /api/orders/{order}/after") == []


def test_secret_form_request_and_project_checks():
    assert names("POST /api/warehouse/sync") == [
        ("ensureValidSecret (hash_equals config:bookstore.sync_secret)", "secret",
         "WarehouseSyncController::ensureValidSecret", False)]
    assert names("POST /api/sync/compare") == [
        ("=== config:bookstore.sync_secret", "secret", "SecretCompareController::store", True)]
    assert names("POST /api/orders/{order}/form") == [
        ("RefundRequest::authorize (can('orders.refund'))", "permission", "RefundRequest::authorize", False)]
    assert names("POST /api/orders/{order}/open") == []
    assert names("POST /api/orders/{order}/trait") == [
        ("assertCanManage", "permission", "ManagesOrders::assertCanManage", False)]
    assert names("POST /api/orders/{order}/plain") == [
        ("assertOwns", "permission", "ManagesOrders::assertOwns", False)]
    assert names("POST /api/orders/{order}/deny") == [
        ("reject", "permission", "DenyController::reject", False)]


def test_controller_middleware_depth_and_reach():
    assert names("POST /api/mw/show") == [
        ("can:orders.refund", "permission", "MwController::__construct", False)]
    assert names("DELETE /api/mw/show") == [
        ("can:orders.refund", "permission", "MwController::__construct", False),
        ("role:admin", "role", "MwController::__construct", False)]
    assert names("GET /api/has/show") == [
        ("auth", "role", "HasMwController::middleware", False),
        ("can:orders.view", "permission", "HasMwController::middleware", False)]
    assert names("GET /api/has/index") == [
        ("auth", "role", "HasMwController::middleware", False)]
    assert names("POST /api/deep/ok") == [
        ("authorize('update')", "policy", "DeepController::level2", False)]
    assert names("POST /api/deep/late") == []


def test_unguarded_hides_unconditional_inline_and_strict_does_not():
    st = GraphStore(db())
    plain = {i["name"] for i in R.routes_report(st, unguarded=True)["items"]}
    strict = {i["name"] for i in R.routes_report(st, unguarded=True, strict=True)["items"]}
    hidden = {"POST /api/orders/{order}/refund", "POST /api/warehouse/sync", "POST /api/mw/show", "POST /api/orders/{order}/trait",
              "POST /api/orders/{order}/form"}
    assert hidden.isdisjoint(plain)
    assert hidden <= strict
    assert "POST /api/orders/{order}/maybe" in plain  # conditional only
    assert "POST /api/orders/{order}/after" in plain
    assert "POST /api/sync/compare" in plain
    text = R.render_routes(R.routes_report(st), st)
    assert "inline: hasPermission('orders.refund') [permission] RefundController::store" in text
    assert "[permission, conditional]" in text
    refund = protocols(st, pattern="orders/{order}/refund")["endpoints"]
    assert refund and "unguarded" not in refund[0]["checks"]
    after = protocols(st, pattern="orders/{order}/after")["endpoints"]
    assert after and "unguarded" in after[0]["checks"]


def test_discarded_checks_do_not_hide_routes():
    """A check that does not gate the action stays visible to --unguarded."""
    open_routes = [
        "POST /api/orders/{order}/ignored",
        "POST /api/orders/{order}/allows",
        "POST /api/orders/{order}/allows-return",
        "POST /api/orders/{order}/ui",
        "POST /api/orders/{order}/flag",
        "POST /api/orders/{order}/preview",
        "POST /api/orders/{order}/after-callee",
        "POST /api/sync/headers",
        "POST /api/sync/loose",
        "POST /api/orders/{order}/branched",
        "POST /api/orders/{order}/branch-call",
    ]
    for route in open_routes:
        assert names(route) == [] or all(g[3] for g in names(route)), (route, names(route))
        assert all(g[1] != "secret" for g in names(route)), (route, names(route))
    assert names("POST /api/orders/{order}/ignored") == []
    assert names("POST /api/orders/{order}/allows") == []
    assert names("POST /api/orders/{order}/allows-return") == []
    assert names("POST /api/orders/{order}/ui") == []
    assert names("POST /api/orders/{order}/flag") == []
    assert names("POST /api/orders/{order}/preview") == []
    assert names("POST /api/orders/{order}/after-callee") == []
    assert names("POST /api/sync/headers") == []
    assert names("POST /api/sync/loose") == []
    assert names("POST /api/orders/{order}/branched") == [
        ("ensureAccessOrContinue", "permission", "FalseGuardController::ensureAccessOrContinue", True)]
    assert names("POST /api/orders/{order}/branch-call") == [
        ("ensureAccess", "permission", "FalseGuardController::branchCall", True)]
    assert names("POST /api/orders/{order}/allows-abort") == [
        ("Gate::allows('update')", "policy", "FalseGuardController::allowsAbort", False)]
    assert names("POST /api/orders/{order}/gated") == [
        ("ensureAccess", "permission", "FalseGuardController::gated", False)]
    st = GraphStore(db())
    plain = {i["name"] for i in R.routes_report(st, unguarded=True)["items"]}
    for route in open_routes:
        assert route in plain, route
    assert "POST /api/orders/{order}/allows-abort" not in plain
    assert "POST /api/orders/{order}/gated" not in plain


def test_resource_and_invokable_middleware_only_except():
    assert names("GET /api/orders") == [
        ("auth", "role", "OrderResourceController::__construct", False),
        ("can:orders.view", "permission", "OrderResourceController::__construct", False)]
    assert names("GET /api/orders/create") == names("GET /api/orders")
    assert names("PUT /api/orders/{order}") == [
        ("auth", "role", "OrderResourceController::__construct", False),
        ("can:orders.update", "permission", "OrderResourceController::__construct", False)]
    assert names("DELETE /api/orders/{order}") == names("PUT /api/orders/{order}")
    assert names("GET /api/orders/{order}/edit") == names("GET /api/orders")
    assert names("GET /api/shipments") == [
        ("can:orders.view", "permission", "ShipmentController::middleware", False)]
    assert names("PUT /api/shipments/{shipment}") == [
        ("can:orders.update", "permission", "ShipmentController::middleware", False)]
    assert names("DELETE /api/shipments/{shipment}") == names("PUT /api/shipments/{shipment}")
    assert names("GET /api/shipments/{shipment}") == names("GET /api/shipments")
    assert names("POST /api/invoke/sync") == [
        ("can:orders.refund", "permission", "InvokeMwController::__construct", False)]
    st = GraphStore(db())
    plain = {i["name"] for i in R.routes_report(st, unguarded=True)["items"]}
    assert "GET /api/orders" not in plain and "PUT /api/orders/{order}" not in plain
    assert "GET /api/shipments" not in plain and "PUT /api/shipments/{shipment}" not in plain
    assert "POST /api/invoke/sync" not in plain


def test_json_and_search_include_inline_guards():
    proc = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "routes", "--db", str(db()), "--json"],
                          check=True, capture_output=True, text=True)
    data = json.loads(proc.stdout)
    refund = next(i for i in data["items"] if i["name"] == "POST /api/orders/{order}/refund")
    assert refund["inline_guards"] == [{
        "name": "hasPermission('orders.refund')", "kind": "permission",
        "at": "RefundController::store", "conditional": False}]
    found = Q.search(GraphStore(db()), "orders.refund")["guards"]
    assert any(v[0]["via"] == "inline" for v in found.values())

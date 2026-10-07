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
    assert names("POST /orders/{order}/refund") == [
        ("hasPermission('orders.refund')", "permission", "RefundController::store", False)]
    assert names("POST /orders/{order}/policy") == [
        ("authorize('update')", "policy", "PolicyController::update", False)]
    assert names("POST /orders/{order}/gate") == [
        ("Gate::authorize('refund')", "policy", "PolicyController::gate", False)]
    assert names("POST /orders/{order}/denies") == [
        ("Gate::denies('update')", "policy", "PolicyController::denies", True)]
    assert names("POST /orders/{order}/maybe") == [
        ("cannot('orders.refund')", "permission", "ConditionalController::update", True)]
    assert names("POST /staff/role") == [
        ("hasRole('admin')", "role", "RoleController::update", False)]
    assert names("POST /orders/{order}/after") == []


def test_secret_form_request_and_project_checks():
    assert names("POST /warehouse/sync") == [
        ("ensureValidSecret (hash_equals config:bookstore.sync_secret)", "secret",
         "WarehouseSyncController::ensureValidSecret", False)]
    assert names("POST /sync/compare") == [
        ("=== config:bookstore.sync_secret", "secret", "SecretCompareController::store", True)]
    assert names("POST /orders/{order}/form") == [
        ("RefundRequest::authorize (can('orders.refund'))", "permission", "RefundRequest::authorize", False)]
    assert names("POST /orders/{order}/open") == []
    assert names("POST /orders/{order}/trait") == [
        ("assertCanManage", "permission", "ManagesOrders::assertCanManage", False)]
    assert names("POST /orders/{order}/plain") == [
        ("assertOwns", "permission", "ManagesOrders::assertOwns", False)]
    assert names("POST /orders/{order}/deny") == [
        ("reject", "permission", "DenyController::reject", False)]


def test_controller_middleware_depth_and_reach():
    assert names("POST /mw/show") == [
        ("can:orders.refund", "permission", "MwController::__construct", False)]
    assert names("DELETE /mw/show") == [
        ("can:orders.refund", "permission", "MwController::__construct", False),
        ("role:admin", "role", "MwController::__construct", False)]
    assert names("GET /has/show") == [
        ("auth", "role", "HasMwController::middleware", False),
        ("can:orders.view", "permission", "HasMwController::middleware", False)]
    assert names("GET /has/index") == [
        ("auth", "role", "HasMwController::middleware", False)]
    assert names("POST /deep/ok") == [
        ("authorize('update')", "policy", "DeepController::level2", False)]
    assert names("POST /deep/late") == []


def test_unguarded_hides_unconditional_inline_and_strict_does_not():
    st = GraphStore(db())
    plain = {i["name"] for i in R.routes_report(st, unguarded=True)["items"]}
    strict = {i["name"] for i in R.routes_report(st, unguarded=True, strict=True)["items"]}
    hidden = {"POST /orders/{order}/refund", "POST /warehouse/sync", "POST /mw/show", "POST /orders/{order}/trait",
              "POST /orders/{order}/form"}
    assert hidden.isdisjoint(plain)
    assert hidden <= strict
    assert "POST /orders/{order}/maybe" in plain  # conditional only
    assert "POST /orders/{order}/after" in plain
    assert "POST /sync/compare" in plain
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
        "POST /orders/{order}/ignored",
        "POST /orders/{order}/allows",
        "POST /orders/{order}/allows-return",
        "POST /orders/{order}/ui",
        "POST /orders/{order}/flag",
        "POST /orders/{order}/preview",
        "POST /orders/{order}/after-callee",
        "POST /sync/headers",
        "POST /sync/loose",
        "POST /orders/{order}/branched",
        "POST /orders/{order}/branch-call",
    ]
    for route in open_routes:
        assert names(route) == [] or all(g[3] for g in names(route)), (route, names(route))
        assert all(g[1] != "secret" for g in names(route)), (route, names(route))
    assert names("POST /orders/{order}/ignored") == []
    assert names("POST /orders/{order}/allows") == []
    assert names("POST /orders/{order}/allows-return") == []
    assert names("POST /orders/{order}/ui") == []
    assert names("POST /orders/{order}/flag") == []
    assert names("POST /orders/{order}/preview") == []
    assert names("POST /orders/{order}/after-callee") == []
    assert names("POST /sync/headers") == []
    assert names("POST /sync/loose") == []
    assert names("POST /orders/{order}/branched") == [
        ("ensureAccessOrContinue", "permission", "FalseGuardController::ensureAccessOrContinue", True)]
    assert names("POST /orders/{order}/branch-call") == [
        ("ensureAccess", "permission", "FalseGuardController::branchCall", True)]
    assert names("POST /orders/{order}/allows-abort") == [
        ("Gate::allows('update')", "policy", "FalseGuardController::allowsAbort", False)]
    assert names("POST /orders/{order}/gated") == [
        ("ensureAccess", "permission", "FalseGuardController::gated", False)]
    st = GraphStore(db())
    plain = {i["name"] for i in R.routes_report(st, unguarded=True)["items"]}
    for route in open_routes:
        assert route in plain, route
    assert "POST /orders/{order}/allows-abort" not in plain
    assert "POST /orders/{order}/gated" not in plain


def test_resource_and_invokable_middleware_only_except():
    assert names("GET /orders") == [
        ("auth", "role", "OrderResourceController::__construct", False),
        ("can:orders.view", "permission", "OrderResourceController::__construct", False)]
    assert names("GET /orders/create") == names("GET /orders")
    assert names("PUT /orders/{order}") == [
        ("auth", "role", "OrderResourceController::__construct", False),
        ("can:orders.update", "permission", "OrderResourceController::__construct", False)]
    assert names("DELETE /orders/{order}") == names("PUT /orders/{order}")
    assert names("GET /orders/{order}/edit") == names("GET /orders")
    assert names("GET /shipments") == [
        ("can:orders.view", "permission", "ShipmentController::middleware", False)]
    assert names("PUT /shipments/{shipment}") == [
        ("can:orders.update", "permission", "ShipmentController::middleware", False)]
    assert names("DELETE /shipments/{shipment}") == names("PUT /shipments/{shipment}")
    assert names("GET /shipments/{shipment}") == names("GET /shipments")
    assert names("POST /invoke/sync") == [
        ("can:orders.refund", "permission", "InvokeMwController::__construct", False)]
    st = GraphStore(db())
    plain = {i["name"] for i in R.routes_report(st, unguarded=True)["items"]}
    assert "GET /orders" not in plain and "PUT /orders/{order}" not in plain
    assert "GET /shipments" not in plain and "PUT /shipments/{shipment}" not in plain
    assert "POST /invoke/sync" not in plain


def test_json_and_search_include_inline_guards():
    proc = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "routes", "--db", str(db()), "--json"],
                          check=True, capture_output=True, text=True)
    data = json.loads(proc.stdout)
    refund = next(i for i in data["items"] if i["name"] == "POST /orders/{order}/refund")
    assert refund["inline_guards"] == [{
        "name": "hasPermission('orders.refund')", "kind": "permission",
        "at": "RefundController::store", "conditional": False}]
    found = Q.search(GraphStore(db()), "orders.refund")["guards"]
    assert any(v[0]["via"] == "inline" for v in found.values())

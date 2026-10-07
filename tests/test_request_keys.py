"""Request keys: Laravel route schema, normalised client keys, api-calls output."""
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from cg_code_graph.payload import Checker  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.plugins.laravel.values import parse_laravel_rule  # noqa: E402

pytestmark_php = pytest.mark.skipif(not shutil.which("php"), reason="php not installed")


def _issues(ra, call):
    checker = Checker({}, {}, "web", "api")
    found = []

    def add(kind, sev, msg, **kw):
        found.append({"kind": kind, "severity": sev, "message": msg, **kw})

    checker.check_request(ra, call, add)
    return found


RULES = {"request": {"keys": [
    {"name": "book_id", "location": "body", "required": True, "nullable": False, "sometimes": False,
     "type": "integer", "file": "app/Http/Requests/StoreOrderRequest.php", "line": 12},
    {"name": "quantity", "location": "body", "required": True, "type": "integer",
     "file": "app/Http/Requests/StoreOrderRequest.php", "line": 13},
    {"name": "gift_note", "location": "body", "required": False, "nullable": True, "type": "string",
     "file": "app/Http/Requests/StoreOrderRequest.php", "line": 14},
]}}


def test_parse_laravel_rule_types():
    assert parse_laravel_rule("required|integer|min:1") == {
        "required": True, "nullable": False, "sometimes": False, "conditional": False, "type": "integer"}
    assert parse_laravel_rule(["nullable", "string", "max:64"])["nullable"] is True
    assert parse_laravel_rule("sometimes|string")["sometimes"] is True
    got = parse_laravel_rule("required|in:open,closed")
    assert got["type"] == "enum" and got["enum"] == ["open", "closed"] and got["required"] is True
    for rule in ("required_if:status,open|string", "required_with:nickname|string"):
        parsed = parse_laravel_rule(rule)
        assert parsed["required"] is False and parsed["conditional"] is True and parsed["sometimes"] is True
    cond = parse_laravel_rule(None, conditional=True)
    assert cond["required"] is False and cond["conditional"] is True


def test_ts_dict_and_dart_list_share_one_compare():
    call = {"at": "web/app/composables/useOrders.ts:7",
            "body_keys": {"keys": ["bookId", "quantity"], "conditional": []}}
    kinds = {i["kind"] for i in _issues(RULES, call)}
    assert kinds == {"request_case_mismatch"}
    msg = next(i["message"] for i in _issues(RULES, call) if i["kind"] == "request_case_mismatch")
    assert "bookId" in msg and "book_id" in msg and "StoreOrderRequest.php" in msg

    dart = {"body_keys": [{"key": "bookId", "type": "int", "line": 3, "file": "order.dart"},
                          {"key": "quantity", "type": "int", "line": 3, "file": "order.dart"}]}
    assert {i["kind"] for i in _issues(RULES, dart)} == {"request_case_mismatch"}

    missing = {"body_keys": {"keys": ["note"], "conditional": []}, "at": "web/a.ts:1"}
    kinds = {i["kind"] for i in _issues(RULES, missing)}
    assert kinds == {"request_missing_required", "request_unknown_field"}
    assert any(i["kind"] == "request_missing_required" and "book_id" in i["message"] for i in _issues(RULES, missing))

    opaque = {"body_keys": {"keys": [], "opaque": True, "conditional": []}}
    assert _issues(RULES, opaque) == []
    partial = {"body_keys": {"keys": ["bookId", "note"], "opaque": True, "conditional": []}, "at": "web/a.ts:3"}
    assert {i["kind"] for i in _issues(RULES, partial)} == {"request_case_mismatch"}


def test_negative_cases_do_not_flag():
    """Opaque bodies, conditional rules, route params, query/body mix, and case folding stay quiet."""
    soft = {"request": {"keys": [
        {"name": "nickname", "required": False, "sometimes": True, "type": "string"},
        {"name": "gift_note", "required": True, "nullable": True, "type": "string"},
        {"name": "coupon", "required": False, "conditional": True, "sometimes": True, "type": "string"},
        {"name": "extra", "required": False, "conditional": True, "type": "string"},
        {"name": "book_id", "required": True, "type": "integer"},
    ]}}
    call = {"body_keys": {"keys": ["book_id"], "conditional": []}, "at": "web/a.ts:1"}
    assert _issues(soft, call) == []

    assert _issues({"request": {"closed": False, "keys": [
        {"name": "note", "required": False, "via": "input"},
    ]}}, {"body_keys": {"keys": ["other"], "conditional": []}}) == []

    assert _issues({}, {"body_keys": {"keys": ["bookId"], "conditional": []}}) == []

    routed = {"uri": "/orders/{id}", "request": {"keys": [
        {"name": "id", "required": True, "type": "integer"},
        {"name": "qty", "required": True, "type": "integer"},
    ]}}
    got = _issues(routed, {"body_keys": {"keys": ["id"], "conditional": []}, "at": "web/a.ts:1"})
    assert {i["kind"] for i in got} == {"request_missing_required"}
    assert all(i.get("key") != "id" for i in got)

    swapped = {"request": {"keys": [
        {"name": "q", "location": "query", "required": True, "type": "string"},
    ]}}
    assert _issues(swapped, {"body_keys": {"keys": ["q"], "conditional": []},
                             "query_keys": {"keys": [], "conditional": []}}) == []

    both = {"request": {"keys": [
        {"name": "book_id", "required": True, "type": "integer"},
        {"name": "bookId", "required": True, "type": "integer"},
    ]}}
    assert _issues(both, {"body_keys": {"keys": ["bookId"], "conditional": []}}) == []

    folded = {"middleware": ["ConvertCase"], "request": {"keys": [
        {"name": "book_id", "required": True, "type": "integer"},
        {"name": "quantity", "required": True, "type": "integer"},
    ]}}
    kinds = {i["kind"] for i in _issues(folded, {"body_keys": {"keys": ["bookId", "quantity"], "conditional": []}})}
    assert "request_case_mismatch" not in kinds and "request_missing_required" not in kinds

    nested = {"request": {"keys": [
        {"name": "items.*.book_id", "required": True, "type": "integer"},
        {"name": "address.city", "required": True, "type": "string"},
    ]}}
    covered = _issues(nested, {"body_keys": {"keys": ["items", "address.city"], "conditional": []}})
    assert covered == []
    dotted = _issues(nested, {"body_keys": {"keys": ["items.*.bookId", "address.city"], "conditional": []}})
    assert {i["kind"] for i in dotted} == {"request_case_mismatch"}
    wrong = _issues(nested, {"body_keys": {"keys": ["items.*.title"], "conditional": []}})
    assert {i["kind"] for i in wrong} == {"request_missing_required", "request_unknown_field"}
    assert all(i["severity"] == "medium" for i in wrong if i["kind"] == "request_unknown_field")


def test_query_keys_are_compared_for_get_routes():
    ra = {"request": {"keys": [
        {"name": "q", "location": "query", "required": True, "type": "string", "file": "FilterRequest.php", "line": 9},
    ]}}
    issues = _issues(ra, {"query_keys": {"keys": ["Q"], "conditional": []}, "at": "web/a.ts:2"})
    assert [i["kind"] for i in issues] == ["request_case_mismatch"]
    assert _issues(ra, {"query_keys": {"keys": [], "conditional": []}})[0]["kind"] == "request_missing_required"


def test_api_calls_prints_body_and_query_keys_under_the_call():
    rows = [{
        "endpoint": "http:POST /api/orders",
        "attrs": {},
        "routes": [{"route": "route:POST /api/orders", "confidence": "exact",
                    "controller": ["method:App\\Http\\Controllers\\OrderController::place"]}],
        "calls": [{
            "caller": "function:app/composables/useOrders.ts#useOrders.placeOrder",
            "at": "app/composables/useOrders.ts:7",
            "confidence": "exact",
            "body_keys": {"keys": ["bookId", "quantity"], "conditional": []},
            "query_keys": {"keys": ["page"], "conditional": ["coupon"]},
        }],
    }]
    text = Q.render_api_calls(rows)
    assert "   <- function:app/composables/useOrders.ts#useOrders.placeOrder @ app/composables/useOrders.ts:7 [exact]" in text
    assert "      body keys: bookId, quantity" in text
    assert "      query keys: page, coupon?" in text


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_ts_follows_payload_var_and_typed_parameter(tmp_path):
    from cg_code_graph.indexer import index_project
    root = ROOT / "tests" / "ts_fixtures" / "request-keys"
    index_project(root, tmp_path / "g.db", "keys")
    c = sqlite3.connect(tmp_path / "g.db")
    rows = {r[0]: json.loads(r[1] or "{}") for r in c.execute(
        "SELECT dst, attrs FROM edges WHERE kind='HTTP_CALLS'")}
    var = rows["http:POST /orders"]["body_keys"]
    assert var["keys"] == ["bookId", "quantity"] and not var.get("opaque")
    typed = rows["http:POST /orders/typed"]["body_keys"]
    assert typed["keys"] == ["bookId", "quantity"]
    assert typed["conditional"] == ["giftWrap"]
    nested = rows["http:POST /orders/nested"]["body_keys"]
    assert nested["keys"] == ["address.city", "items.*.book_id"] and not nested.get("opaque")
    assert rows["http:POST /orders/items"]["body_keys"]["keys"] == ["items"]
    spread = rows["http:POST /orders/spread"]["body_keys"]
    assert spread.get("opaque") and "quantity" in spread["keys"]
    assert rows["http:POST /orders/assign"]["body_keys"].get("opaque")
    assert rows["http:POST /orders/form"]["body_keys"].get("opaque")
    assert rows["http:POST /orders/across"]["body_keys"].get("opaque")
    assert not (ROOT / "tests" / "ts_fixtures" / "request-keys" / "node_modules").exists()


@pytest.mark.skipif(not shutil.which("php"), reason="php not installed")
def test_laravel_route_request_keys(tmp_path):
    from cg_code_graph.indexer import index_project
    root = ROOT / "tests" / "request_keys_fixture"
    index_project(root, tmp_path / "g.db", "req")
    c = sqlite3.connect(tmp_path / "g.db")
    routes = {r[0]: json.loads(r[1] or "{}") for r in c.execute("SELECT id, attrs FROM nodes WHERE kind='route'")}
    store = {k["name"]: k for k in routes["route:POST /orders"]["request"]["keys"]}
    assert store["book_id"]["required"] is True and store["book_id"]["type"] == "integer"
    assert store["book_id"]["location"] == "body"
    assert store["quantity"]["required"] is True and store["quantity"]["type"] == "integer"
    assert store["gift_note"]["nullable"] is True and store["gift_note"]["required"] is False
    assert store["nickname"]["sometimes"] is True and store["nickname"]["required"] is False
    assert store["items.*.id"]["required"] is True and store["items.*.id"]["type"] == "integer"
    assert store["address.city"]["required"] is True
    assert store["coupon"]["required"] is False and store["coupon"]["conditional"] is True
    assert store["extra"]["conditional"] is True and store["extra"]["required"] is False
    assert store["when"]["conditional"] is True and store["when"]["required"] is False
    assert store["status"]["type"] == "enum" and store["status"]["enum"] == ["open", "closed"]
    assert store["book_id"]["file"].endswith("StoreOrderRequest.php")

    update = {k["name"]: k for k in routes["route:PUT /orders/{id}"]["request"]["keys"]}
    assert update["sku"]["required"] is True and update["sku"]["type"] == "string"
    assert "items.*.id" in update and update["nickname"]["sometimes"] is True

    adjust = {k["name"]: k for k in routes["route:PATCH /orders/{id}"]["request"]["keys"]}
    assert adjust["qty"]["type"] == "integer" and adjust["qty"]["required"] is True
    assert adjust["status"]["enum"] == ["open", "closed"] and adjust["status"]["required"] is False

    coupon = routes["route:POST /coupons"]["request"]["keys"]
    assert len(coupon) == 1 and coupon[0]["name"] == "coupon" and coupon[0]["via"] == "validated"
    assert coupon[0]["required"] is False and coupon[0]["location"] == "body"

    index = {k["name"]: k for k in routes["route:GET /orders"]["request"]["keys"]}
    assert index["q"]["location"] == "query" and index["q"]["required"] is True and index["q"]["type"] == "string"

    loose = routes["route:POST /loose"]["request"]
    assert loose["closed"] is False
    assert loose["keys"][0]["name"] == "note" and loose["keys"][0]["required"] is False
    assert "request" not in routes["route:GET /plain/{id}"]
    cased = routes["route:POST /cased"]
    assert "ConvertCase" in (cased.get("middleware") or [])
    assert cased["request"]["closed"] is True

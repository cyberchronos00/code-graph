"""Elysia (Bun) routes on the Express router layer: prefixed sub-apps, chain methods, hook scope."""
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

EX = ROOT / "examples" / "bookstore-payments"
FX = ROOT / "tests" / "ts_fixtures" / "elysia-api"
_S: dict = {}


def build() -> dict:
    if _S:
        return _S
    from cg_code_graph.indexer import index_project
    d = Path(tempfile.mkdtemp(prefix="codegraph-elysia-"))
    for name, root in (("pay", EX), ("fx", FX)):
        _S[name + "_stats"] = index_project(root, d / f"{name}.db", root.name)
        _S[name] = d / f"{name}.db"
    return _S


def db(name):
    return sqlite3.connect(build()[name])


def routes(name):
    return {r[0]: json.loads(r[1] or "{}") for r in db(name).execute(
        "SELECT id, attrs FROM nodes WHERE kind='route'")}


def conf(name, rid):
    r = db(name).execute(
        "SELECT confidence FROM edges WHERE src=? AND kind='ROUTES_TO'", (rid,)).fetchone()
    return r[0] if r else None


def names(attrs):
    out = []
    for m in attrs.get("middleware") or []:
        out.append(m.get("name") if isinstance(m, dict) else m)
    return out


def test_bookstore_payments_routes_and_coverage():
    r = routes("pay")
    assert set(r) == {
        "route:GET /",
        "route:POST /payments",
        "route:GET /payments/{id}",
        "route:POST /payments/{id}/cancel",
        "route:POST /refunds",
        "route:POST /refunds/note",
    }
    # onRequest is copied onto every ancestor, so the root route is guarded too
    assert names(r["route:GET /"]) == ["signed-requests (onRequest)"]
    assert r["route:GET /"]["middleware"][0]["checks"]["rejects"] == [401]
    for rid in ("route:POST /payments", "route:GET /payments/{id}", "route:POST /payments/{id}/cancel"):
        assert names(r[rid]) == ["signed-requests (onRequest)"], rid
        mw = r[rid]["middleware"][0]
        assert mw["checks"]["effect"] == "rejects"
        assert r[rid]["framework"] == "elysia"
    assert r["route:POST /payments"]["body_fields"] == ["order_id", "amount"]
    refund = r["route:POST /refunds"]
    assert refund["body_fields"] == ["order_id", "reason?"]
    keys = {(k["name"], k["optional"], k.get("type")) for k in refund["request"]["keys"]}
    assert ("order_id", False, "string") in keys and ("reason", True, "string") in keys
    note = {(k["name"], k["optional"], k.get("type")) for k in r["route:POST /refunds/note"]["request"]["keys"]}
    assert note == {("text", False, "string")}
    assert r["route:GET /"]["framework"] == "elysia"
    post = db("pay").execute("SELECT file FROM nodes WHERE id='route:POST /payments'").fetchone()[0]
    assert post == "src/routes/payments.ts"
    assert db("pay").execute("SELECT file FROM nodes WHERE id='route:GET /'").fetchone()[0] == "src/index.ts"
    cov = build()["pay_stats"]["coverage"]["setup"]
    assert cov["frameworks"] == ["elysia"]
    assert cov["presets"] == ["common", "typescript", "express"]
    detected = build()["pay_stats"]["detected"]["frameworks"]
    assert "elysia" in detected and "express" not in detected


def test_hook_scope_local_scoped_global_and_order():
    r = routes("fx")
    assert "localGate (onBeforeHandle)" in names(r["route:GET /local/ping"])
    assert "localGate (onBeforeHandle)" not in names(r["route:GET /parent"])
    assert "innerGuard (onBeforeHandle)" in names(r["route:GET /mid/inner"])
    assert "innerGuard (onBeforeHandle)" in names(r["route:GET /mid/mine"])
    assert "innerGuard (onBeforeHandle)" not in names(r["route:GET /top"])
    assert "traceRequests (onBeforeHandle)" in names(r["route:GET /gated/child"])
    assert r["route:GET /gated/child"]["middleware"]
    child = next(m for m in r["route:GET /gated/child"]["middleware"] if isinstance(m, dict) and m["name"].startswith("traceRequests"))
    assert child["checks"]["rejects"] == [403]
    assert "traceRequests (onBeforeHandle)" not in names(r["route:GET /top"])
    assert "traceRequests (onBeforeHandle)" not in names(r["route:GET /local/ping"])
    assert "traceRequests (onBeforeHandle)" in names(r["route:GET /after-global/x"])
    # onRequest affects routes registered before it; onBeforeHandle does not
    assert "earlyRequest (onRequest)" in names(r["route:GET /before"])
    assert "traceId (onBeforeHandle)" not in names(r["route:GET /before"])
    assert "traceId (onBeforeHandle)" in names(r["route:GET /after-handle"])
    assert not any(isinstance(m, dict) and m["name"].startswith("traceId") and m.get("checks") for m in r["route:GET /after-handle"]["middleware"])
    assert "guardAll (beforeHandle)" in names(r["route:GET /closed"])
    assert "onlyInside (beforeHandle)" in names(r["route:GET /in"])
    assert "onlyInside (beforeHandle)" not in names(r["route:GET /out"])
    assert "guardAll (beforeHandle)" not in names(r["route:GET /open"])


def test_chain_group_factory_lazy_and_schemas():
    r = routes("fx")
    post = r["route:POST /orders"]
    assert post["body_fields"] == ["order_id", "amount", "note?"]
    assert post["query_fields"] == ["cursor?"]
    keys = {(k["location"], k["name"], k["optional"]) for k in post["request"]["keys"]}
    assert ("body", "order_id", False) in keys and ("body", "note", True) in keys
    assert ("query", "cursor", True) in keys and ("params", "id", False) in keys
    named = {(k["name"], k["optional"], k.get("type")) for k in r["route:POST /orders/named"]["request"]["keys"]}
    assert ("sku", False, "string") in named and ("qty", True, "numeric") in named
    missing = r["route:POST /orders/missing"]["request"]
    assert missing.get("unknown") == ["body"]
    assert not any(k.get("name") == "password" for k in missing.get("keys") or [])
    assert "payGate (beforeHandle)" in names(post)
    assert "route:GET /orders/files/{wildcard*?}" in r
    assert "route:GET /orders/books/{isbn?}" in r
    assert "route:GET /mounted-orders" in r
    assert "route:GET /v1/orders/{id}" in r
    assert "route:GET /shelf/{code}" in r
    assert conf("fx", "route:GET /shelf/{code}") == "heuristic"
    assert conf("fx", "route:POST /orders") == "exact"
    # a plugin with no routes is not a router, and chain methods do not drop the routes above
    ids = set(r)
    assert not any("openapi" in i for i in ids)


def _mw(attrs, label):
    for m in attrs.get("middleware") or []:
        if m == label or (isinstance(m, dict) and m.get("name") == label):
            return m
    raise AssertionError(label)


def test_prefix_composition_cycles_and_auth_codes():
    r = routes("fx")
    assert "route:GET /cmp/a/b/c" in r
    assert "route:GET /cmp/sub/item" in r
    assert "route:GET /cmp/v2/sub/item" in r
    assert "route:GET /cmp/shelf/code" in r
    assert "route:GET /cmp/box/n" in r
    assert "route:GET /cmp/shelf-default/d" in r
    assert "route:GET /cmp/cycle-a/ping" in r
    assert "route:GET /cmp/cycle-a/cycle-b/ping" in r
    assert not any(i.count("cycle-") > 2 for i in r)
    assert "route:GET /slash/trail/z" in r
    header = _mw(r["route:GET /cmp/after-header"], "headerOnly (onBeforeHandle)")
    assert "checks" not in header
    assert _mw(r["route:GET /cmp/denied"], "hook (beforeHandle)")["checks"]["rejects"] == [403]
    assert _mw(r["route:GET /cmp/blocked"], "hook (beforeHandle)")["checks"]["rejects"] == [401]
    assert "headerOnly (onBeforeHandle)" not in names(r["route:GET /cmp/a/b/c"])

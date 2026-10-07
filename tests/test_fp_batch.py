"""False-positive batch #194-#198: axios `data` bodies, handler-read query keys, Elysia model maps through
identifiers, verifying webhook middleware, and repo-attributed coverage fragments on combined graphs.

Fixtures live in tests/fp_batch and are hermetic: type stubs are committed, nothing needs node_modules / vendor.
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sample import EXTRACTOR_DEPS, PHP_EXTRACTOR_DEPS  # noqa: E402
from cg_code_graph import coverage as C  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.link import link_many  # noqa: E402
from cg_code_graph.payload import Checker  # noqa: E402
from cg_code_graph.routes import render_routes, routes_report  # noqa: E402

FX = ROOT / "tests" / "fp_batch"

needs_ts = pytest.mark.skipif(not (EXTRACTOR_DEPS.exists() and shutil.which("node")),
                              reason="run `npm ci` in cg_code_graph/plugins/ts/extractor (needs node)")
needs_php = pytest.mark.skipif(not (PHP_EXTRACTOR_DEPS.exists() and shutil.which("php")),
                               reason="run `composer install` in cg_code_graph/plugins/php/extractor (needs php)")


def _index(tmp, name, root=None):
    root = root or FX / name
    index_project(root, tmp / f"{name}.db", name)
    return tmp / f"{name}.db"


def _issues(res, endpoint_part):
    return [i for i in res["payload_issues"] if endpoint_part in i["endpoint"]]


def _http_attrs(db, fn_suffix):
    c = sqlite3.connect(db)
    for src, attrs in c.execute("SELECT src, attrs FROM edges WHERE kind='HTTP_CALLS'"):
        if src.endswith(fn_suffix):
            return json.loads(attrs or "{}")
    raise AssertionError(f"no HTTP_CALLS edge from {fn_suffix}")


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fp-batch-shop")
    api = _index(tmp, "bookstore-api")
    web = _index(tmp, "bookstore-web")
    res = link_many([("bookstore-api", str(api), "backend"), ("bookstore-web", str(web), "frontend")], str(tmp / "all.db"))
    return {"api": api, "web": web, "all": tmp / "all.db", "res": res}


# ---------------------------------------------------------------- #194
@needs_ts
@needs_php
def test_194_axios_delete_request_and_fetch_bodies(shop):
    text = Q.render_api_calls(Q.api_calls(GraphStore(shop["all"]), "wishlist"))
    assert "body keys: book_id, reason?" in text
    assert text.count("body keys: book_id") >= 3
    attrs = {fn: _http_attrs(shop["web"], fn) for fn in ("#removeItem", "#removeAll", "#removeViaFetch", "#removeViaModule", "#probe")}
    assert attrs["#removeItem"]["body_keys"]["keys"] == ["book_id"]
    assert attrs["#removeItem"]["body_keys"]["conditional"] == ["reason"]
    assert attrs["#removeAll"]["body_keys"]["keys"] == ["book_id"]
    assert attrs["#removeViaFetch"]["body_keys"]["keys"] == ["book_id"]
    assert attrs["#removeViaModule"]["body_keys"]["keys"] == ["book_id", "reason"]
    assert attrs["#probe"]["body_keys"]["keys"] == ["book_id"]   # head / options / get take `data` from the config too


@needs_ts
@needs_php
def test_194_axios_request_reads_params_as_query(shop):
    attrs = _http_attrs(shop["web"], "#viaRequest")
    assert attrs["query_keys"]["keys"] == ["genre", "page"]


@needs_ts
@needs_php
def test_194_no_false_body_missing_but_real_missing_key_reported(shop):
    wl = _issues(shop["res"], "/wishlist/items")
    assert not [i for i in wl if i["kind"] == "request_body_missing"]
    missing = [i for i in wl if i["kind"] == "request_missing_required"]
    assert len(missing) == 1 and missing[0]["key"] == "book_id" and missing[0]["severity"] == "high"
    assert missing[0]["client_field"].endswith("useWishlist.ts:15")


@needs_ts
@needs_php
def test_194_unknown_body_key_stays_medium(shop):
    unknown = [i for i in _issues(shop["res"], "/wishlist/items") if i["kind"] == "request_unknown_field"]
    assert [(i["key"], i["severity"]) for i in unknown] == [("coupon", "medium")]


# ---------------------------------------------------------------- #195
@needs_ts
@needs_php
def test_195_handler_reads_and_paginate_are_known(shop):
    books = [i for i in _issues(shop["res"], "GET /api/books") if not i["endpoint"].endswith("/browse")]
    keys_at = {(i["key"], i["client_field"].rsplit(":", 1)[1]) for i in books if i["kind"] == "request_unknown_field"}
    flagged = {k for k, _ in keys_at}
    assert not flagged & {"page", "per_page", "in_stock_only", "search", "genre"}


@needs_ts
@needs_php
def test_195_unvalidated_extra_param_is_one_low_finding(shop):
    unknown = [i for i in _issues(shop["res"], "GET /api/books") if i["kind"] == "request_unknown_field" and "/browse" not in i["endpoint"]]
    assert [(i["key"], i["severity"]) for i in unknown] == [("utm_source", "low")]


@needs_ts
@needs_php
def test_195_every_read_form_counts_and_per_page_needs_a_read(shop):
    unknown = {i["key"]: i["severity"] for i in _issues(shop["res"], "/api/books/browse") if i["kind"] == "request_unknown_field"}
    # author/lang/edition/internal/year/rating/published/shelf/series/sort are read (filled/get/has/string/only/except/
    # integer/float/date/request()/property/private method); simplePaginate reads `page` only, nothing reads per_page
    assert unknown == {"cursor": "low", "per_page": "low", "utm_source": "low"}


@needs_ts
@needs_php
def test_195_reads_only_route_has_no_unknown_query_findings(shop):
    assert not _issues(shop["res"], "GET /api/shelves")
    c = sqlite3.connect(shop["api"])
    attrs = json.loads(c.execute("SELECT attrs FROM nodes WHERE id='route:GET /shelves'").fetchone()[0])
    assert attrs["request"]["closed"] is False
    assert {k["name"]: k["required"] for k in attrs["request"]["keys"]} == {"room": False, "per_page": False, "page": False}


@needs_ts
@needs_php
def test_195_reads_are_never_required(shop):
    c = sqlite3.connect(shop["api"])
    for rid in ("route:GET /books", "route:GET /books/browse"):
        keys = json.loads(c.execute("SELECT attrs FROM nodes WHERE id=?", (rid,)).fetchone()[0])["request"]["keys"]
        assert keys and not any(k["required"] for k in keys)


def test_195_query_unknown_is_low_body_unknown_is_medium():
    ra = {"request": {"keys": [
        {"name": "genre", "location": "query", "required": False},
        {"name": "title", "location": "body", "required": False}]}}
    call = {"at": "web/a.ts:1", "query_keys": {"keys": ["genre", "utm_source"], "conditional": []},
            "body_keys": {"keys": ["title", "extra"], "conditional": []}}
    found = []
    Checker({}, {}, "web", "api").check_request(ra, call, lambda kind, sev, msg, **kw: found.append((kind, sev, kw.get("key"))))
    assert sorted(found) == [("request_unknown_field", "low", "utm_source"), ("request_unknown_field", "medium", "extra")]


# ---------------------------------------------------------------- #196
@pytest.fixture(scope="module")
def payments(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fp-batch-pay")
    pay = _index(tmp, "bookstore-payments")
    web = _index(tmp, "refund-web")
    res = link_many([("bookstore-payments", str(pay), "backend"), ("refund-web", str(web), "frontend")], str(tmp / "all.db"))
    c = sqlite3.connect(pay)
    routes = {r[0]: json.loads(r[1] or "{}").get("request") or {} for r in c.execute("SELECT id, attrs FROM nodes WHERE kind='route'")}
    return {"routes": routes, "res": res}


def _fields(req):
    return {(k["name"], k["location"]): k["optional"] for k in req.get("keys", [])}


@needs_ts
def test_196_model_map_value_through_import_and_reexport(payments):
    req = payments["routes"]["route:POST /refunds"]
    assert _fields(req) == {("order_id", "body"): False, ("amount", "body"): False, ("note", "body"): True}
    assert not req.get("unknown")


@needs_ts
def test_196_t_ref_query_and_params_models(payments):
    assert _fields(payments["routes"]["route:POST /refunds/again"]) == {
        ("order_id", "body"): False, ("amount", "body"): False, ("note", "body"): True}
    assert _fields(payments["routes"]["route:GET /refunds"]) == {("status", "query"): False, ("cursor", "query"): True}
    assert _fields(payments["routes"]["route:GET /refunds/{id}"]) == {("id", "params"): False}


@needs_ts
def test_196_client_body_is_compared(payments):
    got = {(i["kind"], i["key"], i["severity"]) for i in payments["res"]["payload_issues"]}
    assert got == {("request_missing_required", "amount", "high"), ("request_unknown_field", "reason", "medium")}


@needs_ts
def test_196_same_module_const_inline_map_and_inline_object_unchanged(payments):
    assert _fields(payments["routes"]["route:POST /vouchers"]) == {("code", "body"): False, ("cents", "body"): False}
    assert _fields(payments["routes"]["route:POST /inline-map"]) == {("to", "body"): False, ("message", "body"): True}
    assert _fields(payments["routes"]["route:POST /vouchers/inline"]) == {("sku", "body"): False}


@needs_ts
def test_196_only_one_hop_is_followed(payments):
    req = payments["routes"]["route:POST /vouchers/deep"]
    assert req.get("unknown") == ["body"] and not req.get("keys")


# ---------------------------------------------------------------- #197
@pytest.fixture(scope="module")
def sms(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fp-batch-sms")
    db = _index(tmp, "bookstore-sms")
    st = GraphStore(db)
    res = routes_report(st)
    return {"items": {i["name"]: i for i in res["items"]}, "text": render_routes(res, st)}


@needs_php
def test_197_alias_class_and_name_only_middleware_are_verified(sms):
    for name in ("POST /webhooks/sms/status", "POST /webhooks/sms/inbound", "POST /webhooks/partner/events", "POST /webhooks/carrier/delivery"):
        wh = sms["items"][name]["webhook"]
        assert wh["verified"] is True and wh["how"] and wh["check"], name
    status = sms["items"]["POST /webhooks/sms/status"]["webhook"]
    assert status["provider"] == "twilio"
    assert status["check"].startswith("app/Http/Middleware/VerifySmsSignature.php:")
    assert sms["items"]["POST /webhooks/partner/events"]["webhook"]["check"] == "routes/api.php:16"


@needs_php
def test_197_unverified_control_routes_stay_flagged(sms):
    for name in ("POST /webhooks/sms/unchecked", "POST /webhooks/sms/keyed"):
        assert sms["items"][name]["webhook"]["verified"] is False
    assert "POST /webhooks/sms/unchecked  @routes/api.php:13  NO AUTH  WEBHOOK UNVERIFIED (twilio)" in sms["text"]
    assert "webhook: twilio, verified at app/Http/Middleware/VerifySmsSignature.php:" in sms["text"]
    assert "webhook" not in sms["items"]["POST /internal/sms/resend"] or not sms["items"]["POST /internal/sms/resend"].get("webhook")


@needs_php
def test_197_no_route_carries_both_labels(sms):
    blocks = [b for b in sms["text"].split("\n\n") if "  @" in b]
    assert blocks
    for b in blocks:
        head = b.splitlines()[0]
        assert not ("SECRET-CHECKED" in head and "WEBHOOK UNVERIFIED" in head), head


def test_197_secret_guard_never_prints_next_to_unverified():
    item = {"name": "POST /hooks/x", "at": "routes/api.php:3", "has_auth": False, "secret_checked": True, "inline_auth": False,
            "guards": [{"name": "shared.secret", "auth": False, "secret": True}], "reaches": [], "clients": [],
            "webhook": {"provider": "twilio", "verified": False, "headers": ["x-twilio-signature"]}}
    res = {"mode": "all", "items": [item], "writes": None, "reaches": [], "matched": 1, "total_routes": 1, "filters": []}
    head = next(line for line in render_routes(res).splitlines() if line.startswith("POST /hooks/x"))
    assert "SECRET-CHECKED" not in head and "WEBHOOK UNVERIFIED (twilio)" in head


# ---------------------------------------------------------------- #198
def _lang(mode="exact", unmapped=0, complete=False):
    return {"complete": complete, "mode": mode, "unmapped": unmapped, "parse_failed": 0, "skipped_oversize": 0}


def test_198_combined_fragments_name_their_repo():
    comp = {"complete": False, "languages": {"bookstore-api/typescript": _lang(unmapped=3), "bookstore-web/typescript": _lang(unmapped=3)}}
    text = C.possibly_more(comp)
    frags = text.split(", ")
    assert len(frags) == len(set(frags)) == 2
    assert "3 TypeScript / JavaScript files not indexed (bookstore-api)" in text
    assert "3 TypeScript / JavaScript files not indexed (bookstore-web)" in text
    note = C.answer_note(comp)
    assert "(bookstore-api)" in note and "(bookstore-web)" in note


def test_198_heuristic_fragments_are_attributed_too():
    comp = {"complete": False, "languages": {"api/php": _lang("heuristic"), "web/typescript": _lang("heuristic")}}
    text = C.possibly_more(comp)
    assert text.endswith("(web)") and "heuristic only (api)" in text


def test_198_single_repo_output_is_unchanged():
    comp = {"complete": False, "languages": {"typescript": _lang(unmapped=3), "python": _lang("heuristic")}}
    assert C.possibly_more(comp) == "3 TypeScript / JavaScript files not indexed, Python heuristic only"
    assert C.answer_note(comp).startswith("coverage note: 3 TypeScript / JavaScript files not indexed, Python heuristic only.")


@needs_ts
@needs_php
def test_198_routes_header_on_a_real_combined_graph(shop):
    st = GraphStore(shop["all"])
    head = render_routes(routes_report(st, unguarded=True), st).splitlines()[0]
    assert "3 TypeScript / JavaScript files not indexed (bookstore-api)" in head
    assert "3 TypeScript / JavaScript files not indexed (bookstore-web)" in head


# ---------------------------------------------------------------- hermetic fixtures
def test_fixtures_ship_no_node_modules_or_vendor():
    assert not [p for p in FX.rglob("*") if p.is_dir() and p.name in ("node_modules", "vendor")]

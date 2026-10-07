"""Webhooks part B (#152): stored subscription event names, subscriber-URL pairing, REGISTERS_CALLBACK and
payment-gateway drivers resolved at run time."""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.link import link_many, write_match_report  # noqa: E402
from cg_code_graph.routes import render_routes, routes_report  # noqa: E402

FX = ROOT / "tests" / "webhooks_152b"
NEEDS_PHP = pytest.mark.skipif(shutil.which("php") is None, reason="php is not installed (PHP extractor)")
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed (TS extractor)")
APPS = ("sender-php", "receiver-php", "config-php", "seed-php", "unresolved-php", "driver-php", "driver-none-php", "ts-app", "py-app")


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("wh152b")
    out = {}
    for name in APPS:
        out[name] = d / f"{name}.db"
        index_project(FX / name, out[name], name)
    return out


def _rows(db, sql):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _sends(db):
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in _rows(
        db, "select src, dst, confidence, attrs from edges where kind='SENDS_TO' and dst like 'endpoint:webhook:%'")}


def _cb(db):
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in _rows(
        db, "select src, dst, confidence, attrs from edges where kind='REGISTERS_CALLBACK'")}


# ------------------------------------------------------------------ 1. stored subscription event names
@NEEDS_PHP
def test_stored_events_from_enum(dbs):
    s = _sends(dbs["sender-php"])
    disp = "method:App\\Services\\WebhookDispatcher::dispatch"
    for ev in ("order.paid", "order.shipped", "book.restocked"):
        conf, a = s[(disp, f"endpoint:webhook:sender-php:{ev}")]
        assert conf == "heuristic" and a["via"] == "stored subscription"
        assert a["source"] == "enum WebhookEvent (app/Enums/WebhookEvent.php:5)"
    assert (disp, "endpoint:webhook:sender-php:{event}") not in s


@NEEDS_PHP
def test_stored_events_from_config_and_seed(dbs):
    cfg = _sends(dbs["config-php"])
    assert {k[1] for k in cfg} == {"endpoint:webhook:config-php:invoice.created", "endpoint:webhook:config-php:invoice.voided"}
    assert all(a["source"] == "config/webhooks.php:4" for _c, a in cfg.values())
    seed = _sends(dbs["seed-php"])
    assert {k[1] for k in seed} == {"endpoint:webhook:seed-php:cart.abandoned", "endpoint:webhook:seed-php:wishlist.shared"}
    assert all(a["via"] == "stored subscription" and a["source"].startswith("database/seeders/HookSeeder.php") for _c, a in seed.values())


@NEEDS_PHP
def test_unresolved_stored_event_stays_placeholder(dbs):
    s = _sends(dbs["unresolved-php"])
    assert list(s) == [("method:App\\Services\\Hooks::push", "endpoint:webhook:unresolved-php:{event}")]
    conf, a = s[next(iter(s))]
    assert conf == "heuristic" and "source" not in a and a.get("via") is None


@NEEDS_NODE
def test_stored_events_ts_and_python(dbs):
    ts = _sends(dbs["ts-app"])
    assert {k[1] for k in ts} == {"endpoint:webhook:ts-app:order.paid", "endpoint:webhook:ts-app:order.shipped"}
    assert all(a["via"] == "stored subscription" for _c, a in ts.values())
    py = _sends(dbs["py-app"])
    assert {k[1] for k in py} == {"endpoint:webhook:py-app:order.paid", "endpoint:webhook:py-app:book.restocked"}


# ------------------------------------------------------------------ 2. subscriber URLs
@NEEDS_PHP
def test_subscriber_urls_recorded(dbs):
    rows = _rows(dbs["sender-php"], "select json_extract(attrs,'$.subscriber_urls') from nodes where id='endpoint:webhook:sender-php:order.paid'")
    urls = {u["url"]: u["at"] for u in json.loads(rows[0][0])}
    assert urls["https://bookstore-api.test/webhooks/orders"] == "database/seeders/WebhookSubscriptionSeeder.php:13"
    assert urls["https://bookstore-api.test/webhooks/inventory"] == ".env.example:2"
    assert "https://elsewhere.test/hooks/none" in urls


@pytest.fixture(scope="module")
def linked(dbs, tmp_path_factory):
    d = tmp_path_factory.mktemp("wh152b-link")
    res = link_many([("sender-php", str(dbs["sender-php"]), "both"), ("receiver-php", str(dbs["receiver-php"]), "backend")],
                    str(d / "link.db"))
    write_match_report(res, str(d / "link"))
    return d / "link.db", res, (d / "link.md").read_text()


@NEEDS_PHP
def test_link_pairs_subscriber_url(linked):
    db, res, md = linked
    m = {(r[0], r[1]): (r[2], json.loads(r[3])) for r in _rows(db, "select src, dst, confidence, attrs from edges where kind='MATCHES_ROUTE'")}
    conf, a = m[("endpoint:webhook:sender-php:order.paid", "route:POST /webhooks/orders")]
    assert conf == "heuristic" and a["via"] == "subscriber url" and a["url"] == "https://bookstore-api.test/webhooks/orders"
    assert ("endpoint:webhook:sender-php:order.paid", "route:POST /webhooks/inventory") in m
    ep = {(r[0], r[1]): r[2] for r in _rows(db, "select src, dst, confidence from edges where kind='MATCHES_ENDPOINT'")}
    assert ep[("endpoint:webhook:sender-php:order.paid", "endpoint:webhook:hmac:order.paid")] == "heuristic"
    sec = md.split("## Webhook subscriptions paired by URL")[1].split("\n## ")[0]
    assert "`route:POST /webhooks/orders` <- subscriber URL https://bookstore-api.test/webhooks/orders" in sec
    assert "unpaired: https://elsewhere.test/hooks/none" in sec
    assert not any(u["route"] in ("route:POST /webhooks/orders", "route:POST /webhooks/inventory") for u in res["uncalled_routes"])


# ------------------------------------------------------------------ 3. REGISTERS_CALLBACK
@NEEDS_PHP
def test_callbacks_php(dbs):
    cb = _cb(dbs["sender-php"])
    pay, refund = "route:POST /webhooks/payments/{store}", "route:POST /webhooks/refunds/{store}"
    c, a = cb[("method:App\\Services\\PaymentsClient::createPayment", pay)]
    assert c == "exact" and a["body_key"] == "callback_url" and a["endpoint"] == "http:POST https://pay.provider.test/v1/payments"
    assert cb[("method:App\\Services\\PaymentsClient::createRefund", refund)][1]["body_key"] == "notify_url"
    sdk = cb[("method:App\\Services\\PaymentsClient::registerHook", pay)][1]
    assert sdk["body_key"] == "url" and "endpoint" not in sdk
    # a third-party host and a path that is not one of the app's routes are not callbacks
    assert not [k for k in cb if k[0].endswith(("createThirdParty", "createUnknownPath"))]
    assert len(cb) == 3


@NEEDS_NODE
def test_callbacks_ts_and_python(dbs):
    ts = _cb(dbs["ts-app"])
    r = "route:POST /api/webhooks/shipping"
    assert ts[("function:src/webhooks/register.ts#createShipment", r)][1]["body_key"] == "notify_url"
    assert ("function:src/webhooks/register.ts#registerWithSdk", r) in ts
    assert not [k for k in ts if k[0].endswith("createPartnerShipment")]
    py = _cb(dbs["py-app"])
    assert py[("function:app.payments.create_payment", "route:POST /api/webhooks/payments/{store}")][1]["body_key"] == "callback_url"
    assert ("function:app.payments.register_sdk", "route:POST /api/webhooks/payments/{store}") in py
    assert not [k for k in py if k[0].endswith("create_partner_payment")]


@NEEDS_PHP
def test_routes_and_link_report_for_callbacks(dbs, linked):
    st = GraphStore(str(dbs["sender-php"]))
    txt = render_routes(routes_report(st), st)
    assert "client: external callback (PaymentsClient::createPayment; PaymentsClient::registerHook)" in txt
    assert "client: external callback (PaymentsClient::createRefund)" in txt
    _db, res, md = linked
    sec = md.split("## Routes called back by external parties")[1].split("\n## ")[0]
    assert ("`route:POST /webhooks/payments/{store}` <- callback URL registered in `PaymentsClient::createPayment` "
            "(body key `callback_url`, outbound `http:POST https://pay.provider.test/v1/payments`)") in sec
    without = md.split("## Backend routes without a client call")[1]
    assert "/webhooks/payments" not in without and "/webhooks/refunds" not in without and "GET /books" in without


# ------------------------------------------------------------------ 4. drivers resolved at run time
@NEEDS_PHP
def test_driver_resolved_gateway_routes(dbs):
    st = GraphStore(str(dbs["driver-php"]))
    by = {i["name"]: i for i in routes_report(st)["items"]}
    for name in ("POST /webhooks/gateway/{gateway}", "POST /webhooks/managed"):
        w = by[name]["webhook"]
        assert w["verified"] and w["how"] == "driver StripeGateway" and w["via"] == "driver" and w["provider"] == "stripe"
        assert w["events"] == ["charge.refunded", "invoice.paid"]
        assert sorted(d.rsplit("\\", 1)[-1] for d in w["drivers"]) == sorted(
            ["LedgerGateway::handleWebhook", "StripeGateway::handleWebhook"] if "gateway" in name else
            ["LedgerGateway::processWebhookRequest", "StripeGateway::processWebhookRequest"])
    rx = {(r[0], r[1]): (r[2], json.loads(r[3])) for r in _rows(
        dbs["driver-php"], "select src, dst, confidence, attrs from edges where kind='RECEIVED_BY' and src like 'endpoint:webhook:%'")}
    c, a = rx[("endpoint:webhook:stripe:invoice.paid", "method:App\\Gateways\\StripeGateway::handleWebhook")]
    assert c == "heuristic" and a["via"] == "driver"
    # a class that has the method but no interface / driver base is not an implementation
    assert not [k for k in rx if "OrphanExporter" in k[1]]
    routes_to = {r[0] for r in _rows(dbs["driver-php"], "select dst from edges where kind='ROUTES_TO' and json_extract(attrs,'$.via')='driver'")}
    assert not [x for x in routes_to if "OrphanExporter" in x]


@NEEDS_PHP
def test_driver_without_implementations_is_unchanged(dbs):
    st = GraphStore(str(dbs["driver-none-php"]))
    (item,) = routes_report(st)["items"]
    assert "webhook" not in item
    assert not _rows(dbs["driver-none-php"], "select 1 from edges where kind='ROUTES_TO' and json_extract(attrs,'$.via')='driver'")

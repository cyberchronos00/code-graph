"""Webhook receivers part A (#152): Cashier and webhook-client, route attributes, plugin RPC paths,
signature constants, Kotlin `when` and Rust `match`."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from native_util import have_tree_sitter, rust_analyzer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.routes import render_routes, routes_report  # noqa: E402

FX = ROOT / "tests" / "webhooks_152"


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("wh152")
    out = {}
    for name, path in (("php", FX / "php"), ("ts", FX / "ts"), ("kt", FX / "kt"), ("rs", FX / "rs")):
        out[name] = d / f"{name}.db"
        index_project(path, out[name], "bookstore-hooks" if name == "php" else name)
    return out


def _wh(db):
    con = sqlite3.connect(db)
    return {r[0]: json.loads(r[1] or "{}").get("webhook") for r in
            con.execute("select id, attrs from nodes where kind='route'")}


def _attrs(db):
    con = sqlite3.connect(db)
    return {r[0]: json.loads(r[1] or "{}") for r in con.execute("select id, attrs from nodes where kind='route'")}


def _recv(db):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): r[2] for r in con.execute(
        "select src, dst, confidence from edges where kind='RECEIVED_BY' and src like 'endpoint:webhook:%'")}


def _sends(db):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in con.execute(
        "select src, dst, confidence, attrs from edges where kind='SENDS_TO' and dst like 'endpoint:webhook:%'")}


def test_cashier_and_webhook_client(dbs):
    wh, rx = _wh(dbs["php"]), _recv(dbs["php"])
    stripe = wh["route:POST /stripe/webhook"]
    assert stripe["provider"] == "stripe" and stripe["verified"] and stripe["how"] == "Laravel Cashier"
    assert stripe["events"] == ["customer.subscription.created", "invoice.paid", "invoice.payment_action_required"]
    c = "method:App\\Http\\Controllers\\CashierWebhookController::"
    assert rx[(f"endpoint:webhook:stripe:customer.subscription.created", c + "handleCustomerSubscriptionCreated")] == "heuristic"
    assert rx[(f"endpoint:webhook:stripe:invoice.paid", c + "handleInvoicePaid")] == "heuristic"
    assert rx[(f"endpoint:webhook:stripe:invoice.payment_action_required", c + "handleInvoicePaymentActionRequired")] == "heuristic"
    billing = wh["route:POST /billing/webhook"]
    assert billing["provider"] == "stripe" and billing["how"] == "Laravel Cashier"
    assert billing["events"] == ["customer.updated"]
    assert wh["route:POST /invoices/paid"] is None
    plain = "method:App\\Http\\Controllers\\InvoiceActionsController::"
    assert not any(dst.startswith(plain) for _src, dst in rx)
    pay = wh["route:POST /payments/hooks"]
    assert pay["verified"] and pay["how"] == "spatie/laravel-webhook-client"
    assert pay["events"] == ["payment.captured", "payment.failed"]
    assert ("endpoint:webhook:webhook:payment.captured", "method:App\\Jobs\\ProcessPaymentsWebhook::handle") in rx
    assert ("endpoint:webhook:webhook:payment.captured", "method:App\\Jobs\\ProcessPaymentsWebhook::capture") in rx
    assert ("endpoint:webhook:webhook:payment.failed", "method:App\\Webhooks\\PaymentsProfile::shouldProcess") in rx
    checked = wh["route:POST /checked/hooks"]
    assert checked["verified"] and checked["how"] == "spatie/laravel-webhook-client custom validator"
    assert wh["route:POST /open/hooks"] is None
    assert wh["route:POST /blank/hooks"] is None
    assert wh["route:POST /missing/hooks"] is None
    assert wh["route:POST /hooks/loose"] is None


def test_route_attributes_and_signature_constant(dbs):
    attrs, wh, rx = _attrs(dbs["php"]), _wh(dbs["php"]), _recv(dbs["php"])
    post = attrs["route:POST /api/github/webhooks"]
    assert post["framework"] == "laravel-route-attributes"
    assert post["name"] == "github.webhooks"
    assert post["middleware"] == ["api", "verify.github"]
    assert post["inline_guards"][0]["name"] == "authorize('deploy')"
    assert attrs["route:GET /api/github/webhooks"]["middleware"] == ["api"]
    saleor_attr = attrs["route:POST /api/hooks/saleor"]
    assert saleor_attr["middleware"] == ["api", "auth"]
    assert saleor_attr["inline_guards"][0]["name"] == "authorize('saleor')"
    con = sqlite3.connect(dbs["php"])
    n_edges = con.execute(
        "select count(*) from edges where kind='ROUTES_TO' and src=?",
        ("route:POST /api/github/webhooks",)).fetchone()[0]
    assert n_edges == 1
    assert wh["route:POST /api/github/webhooks"]["provider"] == "github"
    assert wh["route:POST /api/github/webhooks"]["verified"] is False
    h = "method:App\\Http\\Controllers\\GithubHookController::"
    assert rx[("endpoint:webhook:github:push", h + "handle")] == "exact"
    assert rx[("endpoint:webhook:github:push", h + "deploy")] == "exact"
    saleor = wh["route:POST /webhooks/saleor"]
    assert saleor["verified"] and saleor["how"] == "HMAC signature" and saleor["provider"] == "hmac"
    send = _sends(dbs["php"])[("method:App\\Services\\SaleorNotifier::send", "endpoint:webhook:bookstore-hooks:order.paid")]
    assert send[0] == "exact" and send[1]["how"] == "signed POST (saleor-signature)"
    st = GraphStore(str(dbs["php"]))
    txt = render_routes(routes_report(st), st)
    assert "WEBHOOK UNVERIFIED (github)" in txt
    assert "webhook signature (Laravel Cashier) [secret]" in txt
    assert "webhook signature (spatie/laravel-webhook-client) [secret]" in txt


def test_plugin_rpc_and_ts_constant(dbs):
    attrs, wh, rx = _attrs(dbs["ts"]), _wh(dbs["ts"]), _recv(dbs["ts"])
    rpc = attrs["route:POST /github.webhooks"]
    assert rpc["plugin_rpc"] is True and rpc["unmounted"] is True
    assert wh["route:POST /github.webhooks"]["events"] == ["push"]
    assert wh["route:POST /github.webhooks"]["verified"] is False
    assert rx[("endpoint:webhook:github:push", "function:src/plugin.ts#githubWebhook")] == "exact"
    assert rx[("endpoint:webhook:github:push", "function:src/plugin.ts#deploy")] == "exact"
    assert wh["route:POST /hooks/partner"]["verified"] and wh["route:POST /hooks/partner"]["how"] == "HMAC signature"
    send = _sends(dbs["ts"])[("function:src/outbound.ts#notifyOrderPaid", "endpoint:webhook:ts:order.paid")]
    assert send[0] == "exact" and send[1]["how"] == "signed POST (saleor-signature)"
    orders = attrs["route:POST /orders"]
    dotted = attrs["route:POST /acme.events"]
    assert "plugin_rpc" not in orders and "plugin_rpc" not in dotted
    assert orders["framework"] == "express" and dotted["framework"] == "express"
    con = sqlite3.connect(dbs["ts"])
    for rid in ("route:POST /orders", "route:POST /acme.events", "route:POST /hooks/partner"):
        n_edges = con.execute("select count(*) from edges where kind='ROUTES_TO' and src=?", (rid,)).fetchone()[0]
        assert n_edges == 1
    assert wh["route:POST /hooks/loose"] is None


def test_kotlin_when_and_rust_match(dbs):
    wh, rx = _wh(dbs["kt"]), _recv(dbs["kt"])
    assert wh["route:POST /webhooks/github"]["events"] == ["issues", "pull_request", "push"]
    assert wh["route:POST /webhooks/stripe"]["events"] == ["checkout.session.completed", "invoice.paid"]
    gh = next(k for k in rx if k[0] == "endpoint:webhook:github:push" and "POST /webhooks/github" in k[1])
    assert rx[gh] == "exact"
    assert ("endpoint:webhook:github:pull_request", "function:hooks.paid") in rx
    assert ("endpoint:webhook:stripe:checkout.session.completed", "function:hooks.deploy") in rx
    assert ("endpoint:webhook:stripe:invoice.paid", "function:hooks.paid") in rx
    rwh, rrx = _wh(dbs["rs"]), _recv(dbs["rs"])
    assert rwh["route:POST /webhooks/github"]["events"] == ["issues", "pull_request", "push"]
    assert rwh["route:POST /webhooks/stripe"]["events"] == ["checkout.session.completed", "invoice.paid"]
    assert rrx[("endpoint:webhook:github:push", "function:hooks_rs::github_hook")] in ("resolved", "exact")
    assert rrx[("endpoint:webhook:github:issues", "function:hooks_rs::issues")] in ("resolved", "exact")
    assert rrx[("endpoint:webhook:stripe:checkout.session.completed", "function:hooks_rs::stripe_hook")] == "heuristic"
    assert rrx[("endpoint:webhook:stripe:invoice.paid", "function:hooks_rs::issues")] == "heuristic"


@pytest.mark.skipif(not have_tree_sitter() or not rust_analyzer(), reason="rust-analyzer / tree-sitter not installed")
def test_rust_match_exact(dbs):
    rrx = _recv(dbs["rs"])
    assert rrx[("endpoint:webhook:github:push", "function:hooks_rs::github_hook")] == "exact"
    assert rrx[("endpoint:webhook:github:issues", "function:hooks_rs::issues")] == "exact"

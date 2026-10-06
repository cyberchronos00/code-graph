"""Webhook receivers and senders (#37) over tests/webhooks_fixture: Stripe, svix / standardwebhooks, GitHub (HMAC and
@octokit/webhooks), Twilio, GitLab and a generic HMAC partner hook in TS, Python and PHP, each verified and unverified;
provider events from `switch (event.type)` / `event["type"] ==` / the `X-GitHub-Event` header; `cg routes` markers."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.link import link  # noqa: E402
from cg_code_graph.routes import render_routes, routes_report  # noqa: E402

FX = ROOT / "tests" / "webhooks_fixture"


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("wh")
    out = {}
    for r in ("hooks-ts", "hooks-py", "hooks-php"):
        out[r + "-stats"] = index_project(FX / r, d / f"{r}.db", r)
        out[r] = d / f"{r}.db"
    out["link"] = d / "link.db"
    link(str(out["hooks-py"]), str(out["hooks-ts"]), str(out["link"]), backend_name="hooks-py", frontend_name="hooks-ts")
    return out


def _wh(db):
    con = sqlite3.connect(db)
    return {r[0]: json.loads(r[1] or "{}").get("webhook") for r in con.execute("select id, attrs from nodes where kind='route'")}


def _recv(db):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in
            con.execute("select src, dst, confidence, attrs from edges where kind='RECEIVED_BY' and src like 'endpoint:webhook:%'")}


def test_ts_receivers(dbs):
    wh = _wh(dbs["hooks-ts"])
    assert wh["route:POST /webhooks/stripe"]["verified"] and wh["route:POST /webhooks/stripe"]["how"] == "Stripe constructEvent"
    assert wh["route:POST /webhooks/stripe"]["check"] == "src/app.ts:14"
    assert wh["route:POST /webhooks/svix"]["provider"] == "svix"                          # not the octokit import
    assert wh["route:POST /webhooks/github"] == {**wh["route:POST /webhooks/github"], "provider": "github", "verified": True,
                                                  "how": "HMAC signature"}
    assert wh["route:POST /hooks/partner"]["provider"] == "hmac" and wh["route:POST /hooks/partner"]["verified"]
    for r, p in (("route:POST /hooks/stripe-legacy", "stripe"), ("route:POST /hooks/github-open", "github")):
        assert wh[r]["provider"] == p and wh[r]["verified"] is False
    assert wh["route:POST /api/orders"] is None


def test_ts_events(dbs):
    rx = _recv(dbs["hooks-ts"])
    h = "function:src/app.ts#app.post('/webhooks/stripe')"
    assert rx[("endpoint:webhook:stripe:checkout.session.completed", h)][0] == "exact"
    assert ("endpoint:webhook:stripe:checkout.session.completed", "function:src/billing.ts#activateSubscription") in rx
    assert ("endpoint:webhook:stripe:invoice.paid", "function:src/billing.ts#markInvoicePaid") in rx
    # audit() runs in every branch: not a handler of one event
    assert ("endpoint:webhook:stripe:invoice.paid", "function:src/billing.ts#audit") not in rx
    assert ("endpoint:webhook:github:push", "function:src/app.ts#app.post('/webhooks/github')") in rx   # header var
    assert ("endpoint:webhook:github:issues", "function:src/app.ts#app.post('/hooks/github-open')") in rx  # header index
    assert rx[("endpoint:webhook:stripe:customer.subscription.deleted",
               "function:src/app.ts#app.post('/hooks/stripe-legacy')")][0] == "heuristic"           # unverified body
    assert ("endpoint:webhook:github:pull_request", "function:src/app.ts#onPullRequest") in rx        # webhooks.on


def test_py_receivers(dbs):
    wh, rx = _wh(dbs["hooks-py"]), _recv(dbs["hooks-py"])
    assert wh["route:POST /webhooks/stripe"]["verified"]                                    # Header() parameter
    assert wh["route:POST /webhooks/standard"]["provider"] == "standard-webhooks"
    assert wh["route:POST /webhooks/github"]["how"] == "HMAC signature"
    assert wh["route:POST /hooks/twilio"]["provider"] == "twilio" and wh["route:POST /hooks/twilio"]["verified"]
    assert wh["route:POST /hooks/twilio-open"]["verified"] is False
    assert wh["route:GET /health"] is None
    assert ("endpoint:webhook:stripe:charge.refunded", "function:app.billing.refund") in rx           # elif branch
    assert ("endpoint:webhook:stripe:checkout.session.completed", "function:app.billing.activate") in rx
    assert ("endpoint:webhook:github:release", "function:app.main.github_webhook") in rx


def test_php_receivers(dbs):
    wh, rx = _wh(dbs["hooks-php"]), _recv(dbs["hooks-php"])
    c = "method:App\\Http\\Controllers\\"
    assert wh["route:POST /webhooks/stripe"]["verified"] and wh["route:POST /webhooks/stripe-open"]["verified"] is False
    gh = wh["route:POST /webhooks/github"]
    assert gh["verified"] and gh["check"] == "app/Http/Controllers/GithubWebhookController.php:36"   # in a helper method
    assert wh["route:POST /webhooks/gitlab"] == {**wh["route:POST /webhooks/gitlab"], "provider": "gitlab", "verified": False}
    assert ("endpoint:webhook:stripe:invoice.payment_failed", c + "StripeWebhookController::paymentFailed") in rx
    assert ("endpoint:webhook:stripe:customer.subscription.updated", c + "StripeWebhookController::subscriptionUpdated") in rx
    assert ("endpoint:webhook:github:push", c + "GithubWebhookController::deploy") in rx
    for ev in ("Merge Request Hook", "Push Hook"):                                          # PHP match arms
        assert ("endpoint:webhook:gitlab:" + ev, c + "GithubWebhookController::deploy") in rx


def test_routes_report(dbs):
    st = GraphStore(str(dbs["hooks-php"]))
    res = routes_report(st)
    by = {i["name"]: i for i in res["items"]}
    assert by["POST /webhooks/stripe"]["secret_checked"] and not by["POST /webhooks/stripe-open"]["secret_checked"]
    txt = render_routes(res, st)
    assert "WEBHOOK UNVERIFIED (stripe)" in txt and "WEBHOOK UNVERIFIED (gitlab)" in txt
    assert "webhook signature (Stripe constructEvent) [secret]" in txt
    un = routes_report(st, unguarded=True)
    assert {i["name"] for i in un["items"]} == {"POST /webhooks/stripe-open", "POST /webhooks/gitlab"}


def _sends(db):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in
            con.execute("select src, dst, confidence, attrs from edges where kind='SENDS_TO' and dst like 'endpoint:webhook:%'")}


def test_senders(dbs):
    ts, py, php = _sends(dbs["hooks-ts"]), _sends(dbs["hooks-py"]), _sends(dbs["hooks-php"])
    assert ts[("function:src/outbound.ts#announceInvoice", "endpoint:webhook:hooks-ts:invoice.paid")][1]["library"] == "svix"
    c, a = ts[("function:src/outbound.ts#orderCreated", "endpoint:webhook:hooks-ts:order.created")]   # deliver('order.created')
    assert a["via"] == "function:src/outbound.ts#deliver" and a["how"] == "signed POST (X-Acme-Signature)"
    assert ("function:app.outbound.notify_shipped", "endpoint:webhook:hooks-py:order.shipped") in py
    assert py[("function:app.outbound.notify_shipped", "endpoint:webhook:hooks-py:order.shipped")][0] == "exact"
    assert php[("method:App\\Services\\Notifier::invoiceSent", "endpoint:webhook:hooks-php:invoice.sent")][1]["library"] == \
        "spatie/laravel-webhook-server"
    # receivers that read a signature header are not senders
    assert not [k for k in py if "acme_hook" in k[0] or "github_webhook" in k[0]]


def test_pairing(dbs):
    con = sqlite3.connect(dbs["link"])
    m = {(r[0], r[1]): r[2] for r in con.execute("select src, dst, confidence from edges where kind='MATCHES_ENDPOINT'")}
    assert m.get(("endpoint:webhook:hooks-ts:order.created", "endpoint:webhook:hmac:order.created")) == "heuristic"
    assert not [k for k in m if k[0].endswith("invoice.paid")]                                # no receiver of that event

"""PHP outbound HTTP: Laravel Http / Factory / PendingRequest and Guzzle as client endpoints."""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("php"), reason="php not installed")
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "php_http_fixture"
_S: dict = {}


def db():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="cg-php-http-"))
        _S["stats"] = index_project(FIX, d / "g.db", "php-http")
        _S["db"] = d / "g.db"
    return _S["db"]


def st():
    return GraphStore(db())


def http_nodes():
    return {r["id"]: json.loads(r["attrs"] or "{}") for r in st().q("SELECT id, attrs FROM nodes WHERE kind='http'")}


def calls():
    out = []
    for r in st().q("SELECT src, dst, confidence, attrs FROM edges WHERE kind='HTTP_CALLS'"):
        out.append((r["src"], r["dst"], r["confidence"], json.loads(r["attrs"] or "{}")))
    return out


def test_helper_expands_to_the_caller_with_env_base_and_body_keys():
    nodes = http_nodes()
    pay = nodes["http:POST /payments"]
    assert pay["origin_kind"] == "env"
    assert pay["origin"] == "{env.PAYMENTS_BASE_URL}"
    assert pay["base"]["placeholder"] == "{env.PAYMENTS_BASE_URL}"
    assert pay["base"]["value"] == "https://payments.bookstore.test"
    assert pay["base"]["from"] == "config/services.php"
    cancel = nodes["http:POST /payments/{id}/cancel"]
    assert cancel["origin_kind"] == "env" and cancel["base"]["from"] == "config/services.php"
    by_src = {s: (d, c, a) for s, d, c, a in calls()}
    src = "method:App\\Services\\PaymentsClient::createPayment"
    assert src in by_src
    dst, conf, attrs = by_src[src]
    assert dst == "http:POST /payments" and conf == "resolved"
    assert attrs["via_helper"]["fn"] == "PaymentsClient::request"
    assert attrs["body_keys"]["keys"] == ["order_id", "amount"]
    assert "method:App\\Services\\PaymentsClient::request" not in by_src
    text = Q.render_api_calls(Q.api_calls(st(), "POST /payments"))
    assert "body keys: order_id, amount" in text
    assert "via PaymentsClient::request" in text
    assert "{env.PAYMENTS_BASE_URL}" in text


def test_facade_guzzle_contextual_verb_and_unresolved_base():
    nodes = http_nodes()
    assert nodes["http:GET /health"]["origin_kind"] == "env"
    assert nodes["http:GET /v1/catalog"]["client"] == "guzzle"
    assert nodes["http:GET /v1/catalog"]["origin_kind"] == "env"
    assert nodes["http:PUT /v1/catalog"]["origin_kind"] == "env"
    put = next(a for s, d, c, a in calls() if d == "http:PUT /v1/catalog")
    assert put["body_keys"]["keys"] == ["sku"]
    assert nodes["http:GET /v1/ping"]["origin_kind"] == "env"
    assert nodes["http:GET /ping"]["method"] == "GET"
    assert nodes["http:POST /ping"]["method"] == "POST"
    loose = nodes["http:POST /loose"]
    assert loose["origin_kind"] == "unknown"
    loose_edge = next(c for s, d, c, a in calls() if d == "http:POST /loose")
    assert loose_edge == "heuristic"
    assert nodes["http:ANY /forward"]["method"] == "ANY"
    any_edge = next(c for s, d, c, a in calls() if d == "http:ANY /forward")
    assert any_edge == "heuristic"


def test_unrelated_calls_are_not_client_endpoints():
    ids = " ".join(http_nodes())
    assert "not-a-client" not in ids
    blob = json.dumps(http_nodes())
    assert "secret.internal" not in blob
    assert "supersecretvalue" not in blob
    assert "evil.example" not in blob


def test_base_resolution_and_path_building():
    nodes = http_nodes()
    assigned = nodes["http:HEAD /assigned"]
    assert assigned["origin_kind"] == "env"
    assert assigned["base"]["value"] == "https://payments.bookstore.test"
    assert assigned["base"]["from"] == "config/services.php"
    plain = nodes["http:GET /api/v1/status"]
    assert plain["origin_kind"] == "env"
    assert plain["base"]["value"] == "https://plain.bookstore.test/api/v1"
    assert plain["base"]["from"] == "config/services.php"
    gone = nodes["http:DELETE /api/gone"]
    assert gone["origin_kind"] == "env"
    assert gone["base"]["value"] == "https://fallback.bookstore.test/api"
    show = nodes["http:POST /v1/orders/{id}"]
    assert show["origin_kind"] == "env" and show["base"]["value"].endswith("/v1")
    assert nodes["http:GET /v1/orders/{id}/note"]["path"] == "/v1/orders/{id}/note"
    notes = next(a for s, d, c, a in calls() if d == "http:PUT /v1/notes")
    assert notes["via_helper"]["fn"] == "PathClient::send"
    assert "http:DELETE /saved" in nodes
    php_stats = _S["stats"]["plugins"]["php"]
    assert php_stats["http_calls"] == len(calls())
    assert php_stats["http_fake_links"] >= 1


def test_config_array_binding_encoded_segments_and_empty_env():
    nodes = http_nodes()
    blob = json.dumps(nodes)
    assert "supersecretvalue" not in blob
    assert "should-not-win.example" not in blob
    assert "app.bookstore.test" not in blob
    for path in ("/array-bind", "/array-index", "/config-get", "/make-config", "/facade-config", "/local-cfg"):
        node = nodes[f"http:POST {path}"]
        assert node["origin"] == "{env.PAYMENTS_BASE_URL}"
        assert node["origin_kind"] == "env"
        assert node["base"]["value"] == "https://payments.bookstore.test"
        assert node["base"]["from"] == "config/services.php"
    assert nodes["http:GET /payments/{paymentId}/status"]["path"] == "/payments/{paymentId}/status"
    assert nodes["http:GET /orders/{id}/view"]["path"] == "/orders/{id}/view"
    assert nodes["http:GET /refunds/{paymentId}/start"]["path"] == "/refunds/{paymentId}/start"
    assert nodes["http:GET /trim/{paymentId}/end"]["path"] == "/trim/{paymentId}/end"
    assert nodes["http:GET /cast/{paymentId}/end"]["path"] == "/cast/{paymentId}/end"
    assert nodes["http:GET /str/{paymentId}/end"]["path"] == "/str/{paymentId}/end"
    assert nodes["http:GET /pay/{id}/x"]["path"] == "/pay/{id}/x"
    blank = nodes["http:GET /blank"]
    assert blank["origin"] == "{env.BLANK_BASE_URL}"
    assert blank["base"]["value"] == ""
    text = Q.render_api_calls(Q.api_calls(st(), "GET /blank"))
    assert "{env.BLANK_BASE_URL}" in text
    assert "= ," not in text
    rel = nodes["http:GET /relative-only"]
    assert rel["path"] == "/relative-only"
    assert rel["origin_kind"] == "unknown"
    assert not (rel.get("base") or {}).get("value")


def test_http_fake_links_the_test_and_is_not_an_app_call():
    rows = st().q("SELECT src, dst, confidence, attrs FROM edges WHERE kind='TEST_HTTP'")
    assert rows
    srcs = {r["src"] for r in rows}
    assert "method:Tests\\Feature\\PaymentsFakeTest::test_create_payment" in srcs
    dsts = {r["dst"] for r in rows}
    assert "method:App\\Services\\PaymentsClient::createPayment" in dsts
    assert "method:App\\Services\\PaymentsClient::cancelPayment" in dsts
    app = [r for r in calls() if "PaymentsFakeTest" in r[0]]
    assert app == []


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_bookstore_links_payments_service(tmp_path):
    """examples/bookstore-api PaymentsClient matches examples/bookstore-payments routes."""
    from cg_code_graph.link import link_many

    api = ROOT / "examples" / "bookstore-api"
    pay = ROOT / "examples" / "bookstore-payments"
    index_project(api, tmp_path / "api.db", "api")
    index_project(pay, tmp_path / "payments.db", "payments")
    api_st = GraphStore(tmp_path / "api.db")
    text = Q.render_api_calls(Q.api_calls(api_st, "all"))
    assert "http:POST /payments" in text
    assert "http:POST /payments/{id}/cancel" in text
    assert "PaymentsClient::createPayment" in text
    assert "via PaymentsClient::request" in text
    assert "body keys: order_id, amount" in text
    assert "{env.PAYMENTS_BASE_URL}" in text and "config/services.php" in text
    res = link_many(
        [("api", str(tmp_path / "api.db"), "both"), ("payments", str(tmp_path / "payments.db"), "both")],
        str(tmp_path / "ws.db"))
    assert res["stats"]["call_sites_matched"] == res["stats"]["call_sites"] == 2
    ws = GraphStore(tmp_path / "ws.db")
    impact = Q.impact(ws, "route:POST /payments")
    callers = {c["id"] for c in impact["callers"]}
    assert any(c.endswith("PaymentsClient::createPayment") for c in callers)
    assert any(c.endswith("OrderController::checkout") for c in callers)
    entries = {e["id"] for e in impact["entry_points"]}
    assert any("orders/{order}/checkout" in e for e in entries)


def _write_express(root: Path):
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text(
        '{ "name": "fixture-express-recv", "private": true, "dependencies": { "express": "^4.19.0" } }\n')
    (root / "tsconfig.json").write_text(
        '{ "compilerOptions": { "target": "ES2022", "module": "commonjs", "strict": false, "esModuleInterop": true, "skipLibCheck": true }, "include": ["src"] }\n')
    (root / "src" / "app.ts").write_text(
        "import express from 'express'\n"
        "export const app = express()\n"
        "app.get('/catalog', (_req, res) => { res.end('ok') })\n"
        "app.get('/forward', (_req, res) => { res.end('ok') })\n"
        "app.get('/shared', (_req, res) => { res.end('ok') })\n"
        "app.post('/shared', (_req, res) => { res.end('ok') })\n"
        "app.post('/orders/:id', (_req, res) => { res.end('ok') })\n"
        "app.get('/payments/:paymentId/status', (_req, res) => { res.end('ok') })\n")


def _write_laravel(root: Path):
    (root / "routes").mkdir(parents=True)
    (root / "app" / "Http" / "Controllers").mkdir(parents=True)
    (root / "composer.json").write_text('{ "name": "fixture/laravel-recv", "require": { "laravel/framework": "^11.0" } }\n')
    (root / "artisan").write_text("<?php\n")
    (root / "app" / "Http" / "Controllers" / "PaymentsController.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers;\nclass PaymentsController {\n"
        "    public function store() {}\n    public function health() {}\n}\n")
    (root / "routes" / "api.php").write_text(
        "<?php\nuse App\\Http\\Controllers\\PaymentsController;\nuse Illuminate\\Support\\Facades\\Route;\n"
        "Route::post('payments', [PaymentsController::class, 'store']);\n"
        "Route::get('health', [PaymentsController::class, 'health']);\n")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_php_clients_link_express_and_laravel(tmp_path):
    """PHP clients match an Express app and a Laravel app. ANY matches one path only, at heuristic.
    Http::fake does not add a call site. A base path prefix is applied when linking."""
    from cg_code_graph.link import link_many

    express, laravel = tmp_path / "express", tmp_path / "laravel"
    _write_express(express)
    _write_laravel(laravel)
    index_project(FIX, tmp_path / "php.db", "php")
    index_project(express, tmp_path / "ex.db", "express")
    index_project(laravel, tmp_path / "lv.db", "laravel")
    res = link_many(
        [("php", str(tmp_path / "php.db"), "both"),
         ("express", str(tmp_path / "ex.db"), "both"),
         ("laravel", str(tmp_path / "lv.db"), "both")],
        str(tmp_path / "ws.db"))

    def one(ep):
        hit = next(r for r in res["results"] if r["endpoint"].endswith(ep) or r["endpoint"] == ep)
        return hit

    catalog = one("http:GET /v1/catalog")
    assert len(catalog["matched"]) == 1
    assert catalog["matched"][0]["route"].endswith("route:GET /catalog")
    assert catalog["matched"][0]["confidence"] == "heuristic"
    assert "without configured base" in catalog["matched"][0]["uri_variant"]
    orders = one("http:POST /v1/orders/{id}")
    assert len(orders["matched"]) == 1
    assert orders["matched"][0]["route"].endswith("route:POST /orders/{id}")
    assert orders["matched"][0]["confidence"] == "heuristic"
    forward = one("http:ANY /forward")
    assert len(forward["matched"]) == 1
    assert forward["matched"][0]["confidence"] == "heuristic"
    assert forward["matched"][0]["route"].endswith("route:GET /forward")
    shared = one("http:ANY /shared")
    assert shared["matched"] == []
    payments = one("http:POST /payments")
    assert any(m["route"].endswith("route:POST /payments") for m in payments["matched"])
    health = one("http:GET /health")
    assert any(m["route"].endswith("route:GET /health") for m in health["matched"])
    status = one("http:GET /payments/{paymentId}/status")
    assert len(status["matched"]) == 1
    assert status["matched"][0]["route"].endswith("route:GET /payments/{paymentId}/status")
    for row in res["results"]:
        for call in row["calls"]:
            assert "PaymentsFakeTest" not in call["at"]
    assert res["stats"]["call_sites"] == len({c["at"] for r in res["results"] for c in r["calls"]})

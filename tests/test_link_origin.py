"""Origin-aware route matching (#185)."""
from cg_code_graph.core.model import Edge, Node
from cg_code_graph.core.store import GraphStore
from cg_code_graph.link import link_many, match_endpoint, suffix_candidates


def test_suffix_keeps_leading_literals():
    routes = [{"id": "route:POST /payments/{paymentId}/status", "method": "POST",
               "uri": "/payments/{paymentId}/status",
               "uris": [("uri", "/payments/{paymentId}/status")]}]
    assert suffix_candidates("/status", routes) == []
    res = match_endpoint("POST", "/status", routes, "env", "{env.DELIVERY_BASE_URL}")
    assert not res["matched"]
    base = [{"id": "route:GET /v1/orders", "method": "GET", "uri": "/v1/orders",
             "uris": [("uri", "/v1/orders")]}]
    hit = match_endpoint("GET", "/orders", base, "unknown")
    assert hit["matched"] and hit["matched"][0]["route"] == "route:GET /v1/orders"


def test_other_origin_is_external(tmp_path):
    pay = tmp_path / "payments.db"
    api = tmp_path / "api.db"
    st = GraphStore.create(pay)
    st.write([Node(id="route:POST /payments/{paymentId}/status", kind="route", name="status", file="r.py", line=1,
                   attrs={"uri": "/payments/{paymentId}/status", "method": "POST"})], [])
    st.db.close()
    st = GraphStore.create(api)
    st.write([
        Node(id="function:send", kind="function", name="send", file="c.py", line=1),
        Node(id="http:POST /status", kind="http", name="POST /status", file="c.py", line=4,
             attrs={"method": "POST", "path": "/status", "origin_kind": "env",
                    "origin": "{env.DELIVERY_BASE_URL}",
                    "base": {"placeholder": "{env.DELIVERY_BASE_URL}"}}),
    ], [Edge("function:send", "http:POST /status", "HTTP_CALLS", file="c.py", line=4)])
    st.db.close()
    res = link_many([("api", str(api), "both"), ("payments", str(pay), "both")], str(tmp_path / "ws.db"))
    rows = [r for r in res["results"] if r["endpoint"].endswith("http:POST /status")]
    assert rows and not rows[0]["matched"]
    assert rows[0]["reason"] == "external (other origin)"

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


def test_generic_config_origins_name_no_service():
    """runtimeConfig / Vite / server-style base names are generic; `_` and camelCase separate like `-`."""
    from cg_code_graph.link import repos_for_origin
    servers = ["orders-api", "shop-api"]
    for generic in ("{runtimeConfig.SERVER_API_URL}", "{runtimeConfig.public.apiBase}", "{env.VITE_API_BASE_URL}",
                    "{env.NUXT_PUBLIC_BACKEND_URL}"):
        assert repos_for_origin(generic, servers) is None, generic
    assert repos_for_origin("{env.ORDERS_API_URL}", servers) == ["orders-api"]
    assert repos_for_origin("{runtimeConfig.public.shopApiBase}", servers) == ["shop-api"]
    assert repos_for_origin("https://orders-api.internal/v1", servers) == ["orders-api"]
    assert repos_for_origin("{env.PAYMENTS_URL}", servers) == []


def test_origin_with_a_matched_call_is_not_external(tmp_path):
    """A client's origin that matched a linked server for one call is that backend: its unmatched calls stay
    unmatched for their own reason, not `external (other origin)`."""
    srv = tmp_path / "srv.db"
    web = tmp_path / "web.db"
    st = GraphStore.create(srv)
    st.write([Node(id="route:GET /books", kind="route", name="books", file="r.py", line=1,
                   attrs={"uri": "/books", "method": "GET"})], [])
    st.db.close()
    st = GraphStore.create(web)
    calls = []
    nodes = [Node(id="function:load", kind="function", name="load", file="c.ts", line=1)]
    for i, path in enumerate(("/books", "/session")):
        nid = f"http:GET {path}"
        nodes.append(Node(id=nid, kind="http", name=f"GET {path}", file="c.ts", line=2 + i,
                          attrs={"method": "GET", "path": path, "origin_kind": "env", "origin": "{env.SHELF_URL}",
                                 "base": {"placeholder": "{env.SHELF_URL}"}}))
        calls.append(Edge("function:load", nid, "HTTP_CALLS", file="c.ts", line=2 + i))
    st.write(nodes, calls)
    st.db.close()
    res = link_many([("web", str(web), "both"), ("srv", str(srv), "both")], str(tmp_path / "ws.db"))
    by = {r["endpoint"].split(":", 1)[-1]: r for r in res["results"] if "http:" in r["endpoint"]}
    books = next(r for k, r in by.items() if k.endswith("/books"))
    session = next(r for k, r in by.items() if k.endswith("/session"))
    assert books["matched"]
    assert not session["matched"] and session["reason"] != "external (other origin)"

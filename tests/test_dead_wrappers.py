"""Dead client wrappers in api-calls (#180)."""
from cg_code_graph.core.model import Edge, Node
from cg_code_graph.core.store import GraphStore
from cg_code_graph.query import api_calls, render_api_calls


def test_dead_wrapper_is_marked_and_filtered(tmp_path):
    st = GraphStore.create(tmp_path / "g.db")
    st.write([
        Node(id="route:DELETE /orders/{order}", kind="route", name="DELETE /orders/{order}",
             attrs={"uri": "/orders/{order}", "method": "DELETE"}),
        Node(id="http:DELETE /orders/{orderId}", kind="http", name="DELETE /orders/{orderId}",
             attrs={"method": "DELETE", "path": "/orders/{orderId}"}),
        Node(id="function:useOrders.cancelOrder", kind="function", name="cancelOrder", file="useOrders.ts", line=5),
        Node(id="function:useOrders.placeOrder", kind="function", name="placeOrder", file="useOrders.ts", line=3,
             entry_kind="ui_page"),
        Node(id="http:POST /orders", kind="http", name="POST /orders", attrs={"method": "POST", "path": "/orders"}),
        Node(id="route:POST /orders", kind="route", name="POST /orders", attrs={"uri": "/orders", "method": "POST"}),
    ], [
        Edge("function:useOrders.cancelOrder", "http:DELETE /orders/{orderId}", "HTTP_CALLS", file="useOrders.ts", line=5,
             confidence="resolved"),
        Edge("http:DELETE /orders/{orderId}", "route:DELETE /orders/{order}", "MATCHES_ROUTE", confidence="resolved"),
        Edge("function:useOrders.placeOrder", "http:POST /orders", "HTTP_CALLS", file="useOrders.ts", line=3,
             confidence="resolved"),
        Edge("http:POST /orders", "route:POST /orders", "MATCHES_ROUTE", confidence="resolved"),
    ])
    st.db.execute("INSERT INTO node_entry VALUES (?,?,?,?)",
                  ("function:useOrders.placeOrder", "ui_page", 1, "page:orders"))
    st.db.commit()
    rows = api_calls(st, "all")
    dead = [r for r in rows if r["endpoint"].startswith("http:DELETE")][0]
    assert dead["live"] is False
    assert dead["calls"][0]["live"] is False
    text = render_api_calls(rows)
    assert "1 with no live caller" in text.splitlines()[0]
    assert "(no live caller)" in text
    only = api_calls(st, "dead")
    assert [r["endpoint"] for r in only] == ["http:DELETE /orders/{orderId}"]
    st.db.close()

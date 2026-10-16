"""Exact route specs (#187)."""
import json
from pathlib import Path

from cg_code_graph.cli import main
from cg_code_graph.core.model import Edge, Node
from cg_code_graph.core.store import GraphStore
from cg_code_graph.query import api_calls, match_route_spec, route_failure


def _store(tmp_path: Path) -> GraphStore:
    st = GraphStore.create(tmp_path / "g.db")
    routes = [
        "route:POST /payments",
        "route:POST /payments/{id}/cancel",
        "route:GET /payments/{id}",
    ]
    nodes = [Node(id=rid, kind="route", name=rid.split(":", 1)[1]) for rid in routes]
    nodes.append(Node(id="http:POST /payments", kind="http", name="POST /payments",
                      attrs={"method": "POST", "path": "/payments"}))
    nodes.append(Node(id="http:POST /payments/{id}/cancel", kind="http", name="POST /payments/{id}/cancel",
                      attrs={"method": "POST", "path": "/payments/{id}/cancel"}))
    nodes.append(Node(id="method:Pay::create", kind="method", name="create", fqn="Pay::create"))
    edges = [
        Edge(src="http:POST /payments", dst="route:POST /payments", kind="MATCHES_ROUTE", confidence="resolved"),
        Edge(src="http:POST /payments/{id}/cancel", dst="route:POST /payments/{id}/cancel", kind="MATCHES_ROUTE",
             confidence="resolved"),
        Edge(src="method:Pay::create", dst="http:POST /payments", kind="HTTP_CALLS", file="pay.php", line=3,
             confidence="resolved"),
    ]
    st.write(nodes, edges)
    return st


def test_exact_route_does_not_include_routes_below(tmp_path):
    st = _store(tmp_path)
    assert match_route_spec(st, "route:post /payments/:id/cancel") == ["route:POST /payments/{id}/cancel"]
    assert match_route_spec(st, "route:POST /payments") == ["route:POST /payments"]
    rows = api_calls(st, "route:POST /payments")
    assert [r["endpoint"] for r in rows] == ["http:POST /payments"]
    wider = api_calls(st, "route:POST /payments/*")
    assert [r["endpoint"] for r in wider] == ["http:POST /payments/{id}/cancel"]


def test_unknown_route_names_nearest(tmp_path, capsys):
    st = _store(tmp_path)
    miss = route_failure(st, "route:POST /payment")
    assert miss["error"] == "no_such_route"
    assert miss["suggestions"][0] == "POST /payments"
    assert "POST /payments/{id}/cancel" in miss["suggestions"]
    try:
        main(["api-calls", "route:POST /payment", "--db", str(tmp_path / "g.db")])
        raised = None
    except SystemExit as ex:
        raised = ex.code
    err = capsys.readouterr().out
    assert raised == 1
    assert "no such route: POST /payment" in err
    assert "nearest:" in err
    assert "route:POST /payment/*" in err


def test_unknown_route_json(tmp_path, capsys):
    _store(tmp_path)
    try:
        main(["impact", "route:POST /payment", "--db", str(tmp_path / "g.db"), "--json"])
    except SystemExit as ex:
        assert ex.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "no_such_route"
    assert payload["suggestions"]

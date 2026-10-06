"""N-repo workspace link: 3 backends + 2 frontends, backend-to-backend HTTP, colliding class ids, links allow-list."""
import json
import sqlite3
from pathlib import Path

import pytest

from cg_code_graph.apps import index_apps
from cg_code_graph.cli import main
from cg_code_graph.config import ConfigError, load
from cg_code_graph.core.model import Edge, Node
from cg_code_graph.core.store import GraphStore
from cg_code_graph.link import link, link_many
from cg_code_graph.query import path_between, resolve_targets


def _save(path, nodes, edges):
    st = GraphStore.create(path)
    st.write(nodes, edges)
    st.db.close()


def _graph(db):
    c = sqlite3.connect(db)
    nodes = set(c.execute("SELECT id, kind, file, line FROM nodes"))
    edges = set(c.execute("SELECT src, dst, kind, file, line, confidence FROM edges"))
    return nodes, edges


def _cls(repo_file):
    return [
        Node(id="class:Order", kind="class", name="Order", fqn="Order", file=repo_file, line=1, lang="python"),
        Node(id="method:Order.total", kind="method", name="total", fqn="Order.total", file=repo_file, line=4, lang="python"),
    ]


def _contains():
    return [Edge("class:Order", "method:Order.total", "CONTAINS", file="models.py", line=4)]


def _route(uri, handler, line):
    rid = f"route:GET {uri}"
    return [
        Node(id=rid, kind="route", name=f"GET {uri}", file="routes.py", line=line, lang="python",
             attrs={"uri": uri, "method": "GET", "framework": "fastapi"}),
        Node(id=handler, kind="function", name=handler.split(":", 1)[1], fqn=handler.split(":", 1)[1],
             file="routes.py", line=line + 1, lang="python"),
        Edge(rid, handler, "ROUTES_TO", file="routes.py", line=line),
    ]


def _http(path, src, file, line):
    hid = f"http:GET {path}"
    return [
        Node(id=hid, kind="http", name=f"GET {path}", file=file, line=line, lang="python",
             attrs={"method": "GET", "path": path, "origin_kind": "api"}),
        Edge(src, hid, "HTTP_CALLS", file=file, line=line),
    ]


def _nodes_edges_orders():
    nodes = [
        *_route("/orders", "function:list_orders", 10)[0:2],
        *_route("/items", "function:list_items", 20)[0:2],
        Node(id="table:orders", kind="table", name="orders", file="routes.py", line=12),
        *_cls("models.py"),
    ]
    edges = [
        *_route("/orders", "function:list_orders", 10)[2:],
        *_route("/items", "function:list_items", 20)[2:],
        Edge("function:list_orders", "table:orders", "WRITES_TABLE", file="routes.py", line=12),
        *_contains(),
    ]
    return nodes, edges


def test_workspace_five_repos(tmp_path):
    orders_n, orders_e = _nodes_edges_orders()
    billing_n = [
        *_route("/items", "function:bill_items", 5)[0:2],
        *_cls("models.py"),
    ]
    billing_e = [*_route("/items", "function:bill_items", 5)[2:], *_contains()]
    gw_n = [
        *_route("/gw/orders", "function:gw_orders", 1)[0:2],
        *_http("/orders", "function:gw_orders", "client.py", 8)[0:1],
    ]
    gw_e = [
        *_route("/gw/orders", "function:gw_orders", 1)[2:],
        *_http("/orders", "function:gw_orders", "client.py", 8)[1:],
    ]
    web_n = [
        Node(id="page:shop", kind="page", name="shop", file="shop.vue", line=1, lang="ts", attrs={"route": "/shop"}, entry_kind="page"),
        Node(id="function:web_load", kind="function", name="web_load", fqn="web_load", file="api.py", line=1, lang="python"),
        *_http("/gw/orders", "function:web_load", "api.py", 2)[0:1],
        *_http("/items", "function:web_load", "api.py", 3)[0:1],
    ]
    web_e = [
        Edge("page:shop", "http:GET /gw/orders", "HTTP_CALLS", file="shop.vue", line=2),
        *_http("/gw/orders", "function:web_load", "api.py", 2)[1:],
        *_http("/items", "function:web_load", "api.py", 3)[1:],
    ]
    shop_n = [
        Node(id="function:shop_load", kind="function", name="shop_load", fqn="shop_load", file="api.py", line=1, lang="python"),
        *_http("/items", "function:shop_load", "api.py", 2)[0:1],
    ]
    shop_e = _http("/items", "function:shop_load", "api.py", 2)[1:]
    dbs = {}
    for name, nodes, edges in (
            ("orders", orders_n, orders_e), ("billing", billing_n, billing_e),
            ("gateway", gw_n, gw_e), ("web", web_n, web_e), ("shop", shop_n, shop_e)):
        dbs[name] = tmp_path / f"{name}.db"
        _save(dbs[name], nodes, edges)
    repos = [(n, str(dbs[n]), "backend" if n in ("orders", "billing", "gateway") else "frontend")
             for n in ("orders", "billing", "gateway", "web", "shop")]
    out = tmp_path / "ws.db"
    res = link_many(repos, str(out), allow={"web": ["gateway", "orders"], "shop": ["billing"]})
    st = GraphStore(out)
    ids = {r["id"] for r in st.q("SELECT id FROM nodes WHERE fqn='Order'")}
    assert ids == {"orders:class:Order", "billing:class:Order"}
    assert st.node("function:list_orders")["id"] == "function:list_orders"
    assert st.node("orders:method:Order.total")
    me = {(r["src"], r["dst"]) for r in st.q("SELECT src, dst FROM edges WHERE kind='MATCHES_ROUTE'")}
    assert ("http:GET /orders", "route:GET /orders") in me
    assert ("web:http:GET /items", "orders:route:GET /items") in me
    assert ("shop:http:GET /items", "billing:route:GET /items") in me
    assert ("http:GET /gw/orders", "route:GET /gw/orders") in me or ("web:http:GET /gw/orders", "route:GET /gw/orders") in me
    assert ("web:http:GET /items", "billing:route:GET /items") not in me
    assert ("shop:http:GET /items", "orders:route:GET /items") not in me
    contains = {(r["src"], r["dst"]) for r in st.q("SELECT src, dst FROM edges WHERE kind='CONTAINS'")}
    assert ("orders:class:Order", "orders:method:Order.total") in contains
    assert ("billing:class:Order", "billing:method:Order.total") in contains
    assert set(resolve_targets(st, "orders:Order")) == {"orders:class:Order"}
    assert set(resolve_targets(st, "orders:class:Order")) == {"orders:class:Order"}
    assert "billing:class:Order" in resolve_targets(st, "Order")
    assert set(resolve_targets(st, "orders:Order.total")) == {"orders:method:Order.total"}
    path = path_between(st, "page:/shop", "table:orders")
    assert path and path[0]["from"] == "page:shop" and path[-1]["to"] == "table:orders"
    assert any(h["kind"] == "MATCHES_ROUTE" for h in path)
    pairs = {(p["client"], p["server"]): p for p in res["stats"]["pairs"]}
    assert ("web", "billing") not in pairs and ("shop", "orders") not in pairs
    assert pairs[("web", "orders")]["endpoints_matched"] == 1
    assert pairs[("web", "gateway")]["endpoints_matched"] == 1
    assert pairs[("shop", "billing")]["endpoints_matched"] == 1
    assert pairs[("gateway", "orders")]["endpoints_matched"] == 1
    assert pairs[("gateway", "billing")]["endpoints_matched"] == 0
    assert res["stats"]["orders_id_collisions"] == 3
    assert res["stats"]["web_id_collisions"] == 1
    assert res["stats"]["gateway_id_collisions"] == 0
    meta = st.meta()
    assert meta["repos"] == ["orders", "billing", "gateway", "web", "shop"]
    assert set(meta["sources"]) == set(meta["repos"])
    assert meta["repo_roles"]["gateway"] == "backend" and meta["link_clients"] == "all"
    st.db.close()


def test_two_repo_without_collisions_matches_link(tmp_path):
    be, fe = tmp_path / "be.db", tmp_path / "fe.db"
    _save(be, [Node(id="route:GET /v1/items", kind="route", name="items", file="routes.py", line=1,
                    attrs={"uri": "/v1/items", "method": "GET", "framework": "fastapi"}),
               Node(id="class:Item", kind="class", name="Item", fqn="Item", file="m.py", line=1, lang="python")], [])
    _save(fe, [Node(id="http:GET /v1/items", kind="http", name="items", file="api.ts", line=2, lang="ts",
                    attrs={"method": "GET", "path": "/v1/items", "origin_kind": "api"}),
               Node(id="function:load", kind="function", name="load", fqn="load", file="api.ts", line=1, lang="ts")],
          [Edge("function:load", "http:GET /v1/items", "HTTP_CALLS", file="api.ts", line=2)])
    link(str(be), str(fe), str(tmp_path / "a.db"), "api", "web")
    link_many([("api", str(be), "backend"), ("web", str(fe), "frontend")], str(tmp_path / "b.db"), clients="frontend")
    assert _graph(tmp_path / "a.db") == _graph(tmp_path / "b.db")
    stats = GraphStore(tmp_path / "a.db").meta()["stats"]
    assert stats["api_id_collisions"] == 0 and stats["endpoints_matched"] == 1
    assert stats["backend"] == "api" and stats["frontend"] == "web"


def test_link_cli_repo_flags(tmp_path, capsys):
    assert main(["link", "--db", str(tmp_path / "o.db"), "--repo", "only=x.db"]) == 2
    assert main(["link", "--db", str(tmp_path / "o.db"), "--backend", "a.db", "--repo", "b=c.db"]) == 2
    err = capsys.readouterr().err
    assert "--repo" in err


def test_missing_outside_root_keeps_the_message(tmp_path):
    cfg = {"file": ".cg.yaml", "apps": [{"name": "api", "root": "../missing-api", "role": "backend"}]}
    with pytest.raises(ConfigError, match=r"apps\[api\].root: '../missing-api' is not a directory under"):
        index_apps(tmp_path, tmp_path / "o.db", cfg)


def test_app_root_outside_the_workspace(tmp_path):
    ext = tmp_path / "other"
    ext.mkdir()
    (ext / "app.py").write_text("def ping():\n    return 1\n")
    ws = tmp_path / "ws"
    (ws / "local").mkdir(parents=True)
    (ws / "local" / "app.py").write_text("def view():\n    return 1\n")
    (ws / ".cg.yaml").write_text(
        "version: 1\napps:\n  - {name: other, root: ../other, role: backend}\n"
        "  - {name: local, root: local, role: frontend}\n")
    summary = index_apps(ws, tmp_path / "out" / "mono.db", load(ws))
    assert summary["apps"][0]["nodes"] > 0 and summary["db_is"].startswith("combined graph")
    meta = GraphStore(tmp_path / "out" / "mono.db").meta()
    assert meta["repos"] == ["other", "local"]
    assert json.loads(json.dumps(meta["sources"]))["other"].endswith("/other") or "other" in meta["sources"]["other"]

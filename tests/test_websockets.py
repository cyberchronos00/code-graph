"""Raw WebSocket and SSE (#32 part 2) over tests/ws_fixture: ws servers, express-ws, @fastify/websocket, Hono
upgradeWebSocket / streamSSE, python websockets, FastAPI SSE responses; browser WebSocket / ReconnectingWebSocket /
EventSource / fetchEventSource clients, linked by cg link."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.link import link  # noqa: E402
from codegraph.protocols.view import protocols  # noqa: E402

FX = ROOT / "tests" / "ws_fixture"


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("ws")
    out = {}
    for r in ("live-server", "live-web", "py-live"):
        out[r + "-stats"] = index_project(FX / r, d / f"{r}.db", r)
        out[r] = d / f"{r}.db"
    out["link"] = d / "link.db"
    link(str(out["live-server"]), str(out["live-web"]), str(out["link"]), backend_name="live-server", frontend_name="live-web")
    return out


def _nodes(db, kind):
    con = sqlite3.connect(db)
    return {r[0]: (r[1], json.loads(r[2] or "{}")) for r in con.execute("select id, entry_kind, attrs from nodes where kind=?", (kind,))}


def _edges(db, kind):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): r[2] for r in con.execute("select src, dst, confidence from edges where kind=?", (kind,))}


def test_ws_servers(dbs):
    db = dbs["live-server"]
    routes = _nodes(db, "route")
    rt = _edges(db, "ROUTES_TO")
    assert routes["route:WS /live"][0] == "websocket" and routes["route:WS /live"][1]["framework"] == "ws"
    assert ("route:WS /live", "function:src/handlers.ts#onLive") in rt                    # wss.on('connection', onLive)
    port = routes["route:WS /"][1]                                                          # new WebSocketServer({ port })
    assert port["ports"] == [9100] and port["any_path"] and port["handler_unresolved"]      # inline callback: no module handler
    assert not [k for k in rt if k[0] == "route:WS /"]
    assert routes["route:WS /api/echo"][1]["framework"] == "express"                         # express-ws on a mounted router
    assert ("route:WS /api/echo", "function:src/handlers.ts#onEcho") in rt
    for k in ("route:WS /ws/chat", "route:WS /ws/hono"):                                    # { websocket: true }, upgradeWebSocket
        assert routes[k][0] == "websocket"
    assert "route:GET /ws/chat" not in routes and "route:GET /ws/hono" not in routes
    assert routes["route:GET /health"][0] == "http_route"
    # noServer + handleUpgrade behind `req.url.startsWith(path)`: a prefix (heuristic), the upgrade function handles it
    assert rt[("route:WS /collab/{rest*}", "function:src/collab.ts#attachCollab")] == "heuristic"


def test_sse_routes(dbs):
    ts = _nodes(dbs["live-server"], "route")
    assert ts["route:GET /events"][1]["stream"] == "sse"                                    # Content-Type text/event-stream
    assert ts["route:GET /sse/hono"][1]["stream"] == "sse"                                  # streamSSE(c, ..)
    assert "stream" not in ts["route:GET /plain"][1]                                         # only an Accept header
    py = _nodes(dbs["py-live"], "route")
    assert py["route:GET /stream"][1]["stream"] == "sse"                                    # EventSourceResponse
    assert py["route:GET /raw"][1]["stream"] == "sse"                                       # media_type=
    assert "stream" not in py["route:GET /csv"][1]
    assert py["route:WS /"][1]["framework"] == "websockets" and py["route:WS /"][1]["ports"] == [8765]
    assert ("route:WS /", "function:app.main.handler") in _edges(dbs["py-live"], "ROUTES_TO")
    assert py["route:WS /ws"][1]["framework"] == "fastapi"                                  # unchanged


def test_clients(dbs):
    http = _nodes(dbs["live-web"], "http")
    assert http["http:WS /live"][1]["client"] == "websocket"                                # `${proto}://${location.host}/live`
    assert http["http:WS /api/echo"][1]["client"] == "reconnecting-websocket"               # location.origin.replace(/^http/, 'ws')
    assert http["http:WS ws://localhost:9100/"][1]["origin_kind"] == "other"
    assert http["http:GET /events"][1]["stream"] == "sse" and http["http:GET /events"][1]["client"] == "eventsource"
    assert http["http:GET /sse/hono"][1]["client"] == "fetch-event-source"
    assert http["http:WS /collab/{doc}"][1]["origin"] == "{env.VITE_API_URL}"               # API.replace(/^http/, 'ws')
    assert not [k for k in http if "token" in k or k.startswith("http:WS {")]               # `${tokenData.url}?token=..`: opaque


def test_link_and_view(dbs):
    db = dbs["link"]
    mr = _edges(db, "MATCHES_ROUTE")
    assert ("http:WS /live", "route:WS /live") in mr
    assert ("http:WS /api/echo", "route:WS /api/echo") in mr
    assert ("http:GET /events", "route:GET /events") in mr
    assert ("http:WS /collab/{doc}", "route:WS /collab/{rest*}") in mr
    ws = {e["name"]: e for e in protocols(GraphStore(db), protocol="ws")["endpoints"]}
    assert "WS /live" in ws and "WS /ws/hono" in ws
    sse = {(e["kind"], e["name"]) for e in protocols(GraphStore(db), protocol="sse")["endpoints"]}
    assert ("http", "GET /events") in sse and ("route", "GET /events") in sse and ("route", "GET /plain") not in sse

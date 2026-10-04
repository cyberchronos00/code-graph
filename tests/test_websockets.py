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
    for r in ("live-server", "live-web", "py-live", "nest-ws", "nest-ws-web", "nest-ws-adapter", "ws-switch-other"):
        out[r + "-stats"] = index_project(FX / r, d / f"{r}.db", r)
        out[r] = d / f"{r}.db"
    out["link"] = d / "link.db"
    link(str(out["live-server"]), str(out["live-web"]), str(out["link"]), backend_name="live-server", frontend_name="live-web")
    out["link-nest"] = d / "link-nest.db"
    link(str(out["nest-ws"]), str(out["nest-ws-web"]), str(out["link-nest"]), backend_name="nest-ws", frontend_name="nest-ws-web")
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
    # noServer: switch on a URL pathname variable, object lookup, Map lookup
    for path, fn in (("/gamma", "onSwitch"), ("/delta", "onTable"), ("/eps", "onTable"), ("/map", "onMap")):
        assert "any_path" not in routes[f"route:WS {path}"][1]
        assert rt[(f"route:WS {path}", f"function:src/upgrade-forms.ts#attachForms.{fn}")] == "exact"


def test_switch_on_other_value(dbs):
    # a `case '/x':` in a switch on another value (a header) is not a path check: the server keeps taking any path
    db = dbs["ws-switch-other"]
    routes = _nodes(db, "route")
    assert "route:WS /legacy" not in routes and routes["route:WS /"][1]["any_path"]
    assert ("route:WS /", "function:src/modes.ts#attachModes.onMode") in _edges(db, "ROUTES_TO")


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


def test_nest_platform_ws(dbs):
    db = dbs["nest-ws"]
    routes = _nodes(db, "route")
    rt = _edges(db, "ROUTES_TO")
    ev = routes["route:WS /events"]
    assert ev[0] == "websocket" and ev[1]["framework"] == "nest" and ev[1]["uri"] == "/events"
    assert "any_path" not in ev[1] and "ports" not in ev[1]
    assert rt[("route:WS /events", "method:src/events.gateway.ts#EventsGateway.handleConnection")] == "exact"
    port = routes["route:WS /"][1]
    assert port["any_path"] and port["ports"] == [8081] and port["handler_unresolved"] and port["framework"] == "nest"
    assert not [k for k in rt if k[0] == "route:WS /"]
    stats = routes["route:WS /stats"]
    assert stats[1]["ports"] == [8080] and stats[1]["framework"] == "nest"
    assert rt[("route:WS /stats", "method:src/events.gateway.ts#StatsGateway.handleConnection")] == "exact"
    assert routes["route:WS /{path}"][1]["framework"] == "nest"
    assert rt[("route:WS /{path}", "method:src/events.gateway.ts#ConfigGateway.handleConnection")] == "heuristic"
    assert _nodes(db, "message")["message:ws:events"][0] == "message_handler"
    mr = _edges(dbs["link-nest"], "MATCHES_ROUTE")
    assert ("http:WS /events", "route:WS /events") in mr


def test_nest_ws_adapter_without_socketio_twins(dbs):
    # both adapters in package.json, but main.ts calls useWebSocketAdapter(new WsAdapter(app)): the raw WebSocket adapter
    db = dbs["nest-ws-adapter"]
    routes = _nodes(db, "route")
    assert routes["route:WS /live"][1]["framework"] == "nest"
    assert ("route:WS /live", "method:src/live.gateway.ts#LiveGateway.handleConnection") in _edges(db, "ROUTES_TO")
    assert _nodes(db, "message")["message:ws:chat"][1]["adapter"] == "ws"                   # the message node stays
    assert not [k for k in _nodes(db, "endpoint") if k.startswith("endpoint:socketio:")]     # no Socket.IO twins

"""Socket.IO and WebSocket in Dart / Kotlin / Swift / Rust (#32 part 3) over tests/sio_native_fixture: socketioxide and
rust_socketio, Dart socket_io_client, socket.io-client-java, socket.io-client-swift; Ktor / Vapor / axum WebSocket
routes; Ktor, OkHttp and URLSession WebSocket clients; Rust route uri attrs."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "sio_native_fixture"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    p = tmp_path_factory.mktemp("sion") / "x.db"
    st = index_project(FX, p, "sio-native")
    assert st["realtime_native"]["socketio_receivers"] == 10 and st["realtime_native"]["socketio_sends"] == 11
    return p


def _edges(db, kind):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): json.loads(r[2] or "{}") for r in con.execute("select src, dst, attrs from edges where kind=?", (kind,))}


def _nodes(db, kind):
    con = sqlite3.connect(db)
    return {r[0]: (r[1], json.loads(r[2] or "{}")) for r in con.execute("select id, entry_kind, attrs from nodes where kind=?", (kind,))}


E = "endpoint:socketio:"


def test_rust_socketioxide_server(db):
    rx, tx = _edges(db, "RECEIVED_BY"), _edges(db, "SENDS_TO")
    assert rx[(E + "/#chat:send", "function:chat_server::on_send")]["library"] == "socketioxide"
    assert (E + "/#chat:history", "function:chat_server::on_connect") in rx
    assert tx[("function:chat_server::on_send", E + "/#chat:message")]["room"] == "lobby"         # .to("lobby").emit
    assert ("function:chat_server::on_admin", E + "/admin#kick") in tx                             # io.ns("/admin", ..)
    assert tx[("function:chat_server::on_connect", E + "/#room:joined")]["process"] == "server"


def test_rust_socketio_client(db):
    rx, tx = _edges(db, "RECEIVED_BY"), _edges(db, "SENDS_TO")
    assert rx[(E + "/#chat:message", "function:bot::on_message")]["process"] == "client"         # ClientBuilder .on
    assert tx[("function:bot::main", E + "/#chat:send")]["library"] == "rust_socketio"


@pytest.mark.parametrize("recv,send,lib", [
    ("method:mobile-dart/lib/chat.dart#ChatService._onMessage", "method:mobile-dart/lib/chat.dart#ChatService.send", "socket_io_client"),
    ("method:chat.ChatClient.start", "method:chat.ChatClient.send", "socket.io-client-java"),
    ("method:ChatClient.start", "method:ChatClient.send", "socket.io-client-swift"),
])
def test_mobile_clients(db, recv, send, lib):
    rx, tx = _edges(db, "RECEIVED_BY"), _edges(db, "SENDS_TO")
    assert rx[(E + "/#chat:message", recv)]["library"] == lib
    assert tx[(send, E + "/#chat:send")]["role"] == "emit"
    assert tx[(send, E + "/#chat:history")]["role"] == "request"                                    # emitWithAck / ack callback


def test_namespaces_and_interpolation(db):
    rx = _edges(db, "RECEIVED_BY")
    assert (E + "/admin#kick", "method:mobile-dart/lib/chat.dart#ChatService._onKick") in rx       # '$base/admin'
    assert (E + "/admin#kick", "method:ChatClient.start") in rx                                     # socket(forNamespace:)
    assert (E + "/#room:joined", "method:chat.ChatClient.start") in rx                              # Events.JOINED constant


def test_ws_routes(db):
    routes, rt = _nodes(db, "route"), _edges(db, "ROUTES_TO")
    for rid, fw in (("route:WS /api/chat", "ktor"), ("route:WS /echo", "vapor"), ("route:WS /ws", "axum")):
        assert routes[rid][0] == "websocket" and routes[rid][1]["framework"] == fw and routes[rid][1]["method"] == "WS"
    assert ("route:WS /ws", "function:chat_server::http::ws_handler") in rt                        # handler takes WebSocketUpgrade
    assert routes["route:GET /health"][0] == "http_route"
    assert routes["route:GET /users/:id"][1]["uri"] == "/users/{id}"                                # Rust routes carry uri / method


def test_ws_clients(db):
    calls = _edges(db, "HTTP_CALLS")
    http = _nodes(db, "http")
    assert ("method:chat.LiveClient.chat", "http:WS ws://chat.example.com/api/chat") in calls
    assert http["http:WS ws://chat.example.com/api/chat"][1]["client"] == "ktor-websockets"
    assert http["http:WS wss://chat.example.com/live"][1]["client"] == "okhttp-websocket"
    assert http["http:GET https://chat.example.com/feed"][1]["client"] == "okhttp"                 # newCall stays GET
    assert http["http:WS wss://chat.example.com/echo"][1]["client"] == "urlsession-websocket"
    assert ("method:LiveClient.connect", "http:WS wss://chat.example.com/echo") in calls

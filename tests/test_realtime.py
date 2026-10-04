"""Socket.IO in JS / TS (#32 part 1): codegraph/realtime_events.py over tests/realtime_fixture (a socket.io server with a
namespace, middleware, rooms, an ack, a template event and an emit wrapper; a socket.io-client web app; a Nest
gateway with a guard), linked by cg link."""
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

FX = ROOT / "tests" / "realtime_fixture"
E = "endpoint:socketio:"


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("realtime")
    out = {}
    for r in ("chat-server", "chat-web", "nest-gw"):
        out[r + "-stats"] = index_project(FX / r, d / f"{r}.db", r)
        out[r] = d / f"{r}.db"
    out["link"] = d / "link.db"
    link(str(out["chat-server"]), str(out["chat-web"]), str(out["link"]), backend_name="chat-server", frontend_name="chat-web")
    out["link-nest"] = d / "link-nest.db"
    link(str(out["nest-gw"]), str(out["chat-web"]), str(out["link-nest"]), backend_name="nest-gw", frontend_name="chat-web")
    return out


def _edges(db, kind):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in con.execute(
        "select src, dst, confidence, attrs from edges where kind=?", (kind,))}


def _attrs(db, nid):
    r = sqlite3.connect(db).execute("select attrs from nodes where id=?", (nid,)).fetchone()
    return json.loads(r[0] or "{}") if r else None


def _view(db):
    return {e["name"]: e for e in protocols(GraphStore(db), protocol="socketio")["endpoints"]}


def test_server(dbs):
    db = dbs["chat-server"]
    rb = _edges(db, "RECEIVED_BY")
    assert rb[(E + "/#message:send", "function:src/server.ts#onSend")][1]["process"] == "server"   # named handler
    assert (E + "/#order:create", "function:src/orders.ts#registerOrderHandlers") in rb          # `socket: Socket` param
    assert (E + "/admin#kick", "module:src/server.ts") in rb                                    # io.of('/admin')
    assert _attrs(db, E + "/admin#kick")["guards"] == ["authenticate"]                          # admin.use(mw)
    assert _attrs(db, E + "/#message:send")["guards"] == []                                     # unguarded
    st = _edges(db, "SENDS_TO")
    assert st[("module:src/server.ts", E + "/#member:joined")][1]["room"] == "room"            # TS enum, socket.to(room)
    assert st[("function:src/orders.ts#registerOrderHandlers", E + "/#order:{status}")][0] == "heuristic"   # template
    w = st[("function:src/server.ts#orderShipped", E + "/#order:shipped")][1]                   # notify(event, room, ..)
    assert w["via"] == "function:src/server.ts#notify" and w["room"] == "`orders:${id}`"
    assert "lifecycle" not in json.dumps(dbs["chat-server-stats"].get("realtime"))
    assert not any(k[0].endswith("#connection") for k in rb)                                    # lifecycle events
    assert not any("src/tcp.ts" in k[1] for k in rb) and not any("src/tcp.ts" in k[0] for k in st)   # net.Socket


def test_client(dbs):
    db = dbs["chat-web"]
    st = _edges(db, "SENDS_TO")
    assert st[("function:src/chat.ts#sendMessage", E + "/#message:send")][1]["process"] == "client"
    assert st[("function:src/chat.ts#joinRoom", E + "/#room:join")][1]["role"] == "request"     # timeout().emitWithAck
    assert ("function:src/chat.ts#kick", E + "/admin#kick") in st                               # io('http://h/admin')
    rb = _edges(db, "RECEIVED_BY")
    assert (E + "/#member:joined", "function:src/chat.ts#showJoined") in rb
    for ev in ("admin:stats", "admin:alert"):                                                   # admin.on(..).on(..)
        assert (E + "/admin#" + ev, "function:src/chat.ts#connectAdmin") in rb


def test_nest_gateway(dbs):
    db = dbs["nest-gw"]
    rb = _edges(db, "RECEIVED_BY")
    h = "method:src/events.gateway.ts#EventsGateway.handleKick"
    assert rb[(E + "/admin#kick", h)][1]["via"] == "message:ws:admin:kick"                    # twin of the message node
    assert _attrs(db, E + "/admin#kick")["guards"] == ["WsAuthGuard"]
    st = _edges(db, "SENDS_TO")
    assert (h, E + "/admin#server:notice") in st and (h, E + "/admin#kicked") in st             # this.server / client: Socket


def test_link(dbs):
    v = _view(dbs["link"])
    for ev in ("/#message:send", "/#room:join", "/#member:joined", "/#order:shipped", "/#server:notice", "/admin#kick"):
        assert v[ev]["linked"], ev
    me = _edges(dbs["link"], "MATCHES_ENDPOINT")
    assert me[(E + "/#order:{status}", E + "/#order:shipped")][0] == "heuristic"               # template -> listener
    assert "no_sender" in v["/#order:create"]["checks"]                                       # no client emits it
    n = _view(dbs["link-nest"])
    assert n["/admin#kick"]["linked"]


def test_python_server_under_if(tmp_path):
    """python-socketio: a module-level server assigned under `if` / `else` (a Redis manager or not) is found."""
    (tmp_path / "app.py").write_text(
        "import os\nimport socketio\n\n"
        "if os.environ.get('REDIS_URL'):\n"
        "    sio = socketio.AsyncServer(client_manager=socketio.AsyncRedisManager(os.environ['REDIS_URL']))\n"
        "else:\n"
        "    sio = socketio.AsyncServer()\n\n\n"
        "@sio.on('ydoc:document:join')\n"
        "async def join(sid, data):\n"
        "    await sio.emit('ydoc:document:state', data, to=sid)\n")
    index_project(tmp_path, tmp_path / "g.db", "py")
    assert (E + "/#ydoc:document:join", "function:app.join") in _edges(tmp_path / "g.db", "RECEIVED_BY")
    assert ("function:app.join", E + "/#ydoc:document:state") in _edges(tmp_path / "g.db", "SENDS_TO")

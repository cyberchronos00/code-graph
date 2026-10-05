"""Unix domain sockets, named pipes / FIFOs and D-Bus (#38 part 2) over tests/local_sockets_fixture (Rust std /
tokio / zbus, Python socket / asyncio / socketserver / dbus-next, Node net / http) and tests/local_sockets_c_fixture
(sockaddr_un + bind / connect)."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.indexer import index_project  # noqa: E402

FX = ROOT / "tests"


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("lsock")
    out = {}
    for r in ("local_sockets_fixture", "local_sockets_c_fixture"):
        out[r + "-stats"] = index_project(FX / r, d / f"{r}.db", r)
        out[r] = d / f"{r}.db"
    return out


def _edges(db, kind):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in
            con.execute("select src, dst, confidence, attrs from edges where kind=?", (kind,))}


def test_unix_python(dbs):
    db = dbs["local_sockets_fixture"]
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    agent = "endpoint:unix:/run/worker/agent.sock"                    # os.path.join(RUN_DIR, ..) imported constant
    assert r[(agent, "function:pyapp.server.on_agent")][1]["mode"] == "0o660"
    assert s[("function:pyapp.client.ask_agent", agent)][0] == "resolved"
    st = "endpoint:unix:/tmp/status.sock"
    assert (st, "method:pyapp.server.StatusHandler.handle") in r       # socketserver handler class
    assert ("function:pyapp.client.ask_status", st) in s
    assert ("endpoint:unix:/tmp/raw.sock", "function:pyapp.server.raw_server") in r
    assert s[("function:pyapp.client.grpc_agent", agent)][1]["library"] == "grpc"      # 'unix:///..' gRPC target


def test_unix_rust_node(dbs):
    db = dbs["local_sockets_fixture"]
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    ctl = "endpoint:unix:/run/ipcd/control.sock"
    assert r[(ctl, "function:ipcd::serve_control")][1]["mode"] == "0o600"
    assert ("function:ipcd::send_command", ctl) in s
    assert s[("function:ipcd::grpc_client", ctl)][1]["how"] == "Endpoint::try_from"   # tonic unix: URI
    n = "endpoint:unix:/tmp/node-ipc.sock"
    assert (n, "function:node/server.ts#startIpc") in r
    assert ("function:node/client.ts#callIpc", n) in s
    assert s[("function:node/client.ts#dockerVersion", "endpoint:unix:/var/run/docker.sock")][1]["how"] == "http socketPath"
    assert ("endpoint:tcp:8080", "function:node/server.ts#startPort") in r     # a port is still TCP
    assert not any(k[0] == "endpoint:unix:8080" for k in r)


def test_pipes_and_fifos(dbs):
    db = dbs["local_sockets_fixture"]
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    p = "endpoint:pipe:ipcd-events"                                     # r"\\.\pipe\ipcd-events" constant
    assert (p, "function:ipcd::pipes::pipe_server") in r
    assert ("function:ipcd::pipes::pipe_client", p) in s
    assert ("endpoint:pipe:node-ipc", "function:node/server.ts#startPipe") in r
    f = "endpoint:pipe:/tmp/events.fifo"
    assert (f, "function:pyapp.server.make_fifo") in r
    assert ("function:pyapp.client.send_event", f) in s
    assert not any("events.log" in k[1] for k in s)                     # plain file writes are not FIFOs


def test_dbus(dbs):
    db = dbs["local_sockets_fixture"]
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    g = "endpoint:dbus:org.example.Greeter1.SayHello"
    assert (g, "method:ipcd::bus::Greeter::say_hello") in r             # snake_case -> PascalCase
    assert ("endpoint:dbus:org.example.Greeter1.Reset", "method:ipcd::bus::Greeter::reset_counter") in r   # zbus(name)
    assert ("method:ipcd::bus::GreeterClient::say_hello", g) in s
    n = "endpoint:dbus:org.example.Notifier1.Notify"
    assert (n, "method:pyapp.bus.Notifier.Notify") in r
    assert ("function:pyapp.bus.notify", n) in s                        # call_notify on get_interface(..)
    assert s[("method:pyapp.bus.Notifier.Closed", "endpoint:dbus:org.example.Notifier1.Closed")][1]["role"] == "emit"
    lv = "endpoint:dbus:org.example.Settings1.Level"                    # property getter and setter
    assert (lv, "method:ipcd::bus::Settings::level") in r and (lv, "method:ipcd::bus::Settings::set_level") in r
    assert ("endpoint:dbus:org.example.Settings1.Match", "method:ipcd::bus::Settings::r#match") in r
    assert not any("Clamp" in k[0] for k in r)                          # a fn nested in a method is no member


def test_c(dbs):
    db = dbs["local_sockets_c_fixture"]
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    c = "endpoint:unix:/run/ctl.sock"
    assert (c, "function:ctl_listen") in r
    assert ("function:ctl_connect", c) in s


def test_stats(dbs):
    st = dbs["local_sockets_fixture-stats"]["local_sockets"]
    assert st["unix_listeners"] == 5 and st["unix_connectors"] == 7
    assert st["pipe_listeners"] == 3 and st["dbus_members"] == 6 and st["pipe_connectors"] == 2
    assert "unix_path_unknown" not in dbs["local_sockets_c_fixture-stats"]["local_sockets"]

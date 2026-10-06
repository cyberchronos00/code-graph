"""Raw TCP / UDP sockets paired by port (#39): cg_code_graph/sockets.py over tests/sockets_fixture (Python, TypeScript,
Rust, Kotlin, Swift) and tests/sockets_c_fixture (BSD sockets, libuv)."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "sockets_fixture"
CFIX = ROOT / "tests" / "sockets_c_fixture"


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    db = tmp_path_factory.mktemp("so") / "g.db"
    st = index_project(FIX, db, "sockets")
    return st, sqlite3.connect(db)


def _edges(con, kind, ep):
    col, other = ("dst", "src") if kind == "SENDS_TO" else ("src", "dst")
    return {r[0]: (r[1], json.loads(r[2] or "{}")) for r in con.execute(
        f"select {other}, confidence, attrs from edges where kind=? and {col}=?", (kind, ep))}


def _node(con, nid):
    r = con.execute("select attrs from nodes where id=?", (nid,)).fetchone()
    return json.loads(r[0]) if r else None


def test_tcp_pair_across_languages(graph):
    _, con = graph
    # Python socketserver.TCPServer(("0.0.0.0", JOBS_PORT), JobHandler); JOBS_PORT = int(os.environ.get("JOBS_PORT", "7000"))
    recv = _edges(con, "RECEIVED_BY", "endpoint:tcp:7000")
    assert set(recv) == {"method:pyapp.server.JobHandler.handle"}
    assert recv["method:pyapp.server.JobHandler.handle"][1]["exposure"] == "all"
    send = _edges(con, "SENDS_TO", "endpoint:tcp:7000")
    assert set(send) == {"function:node/client.ts#askJobs", "function:pyapp.client.submit_job"}
    assert send["function:node/client.ts#askJobs"][0] == "resolved"          # literal port
    assert send["function:pyapp.client.submit_job"][0] == "heuristic"        # env default
    assert send["function:pyapp.client.submit_job"][1]["host"] == "jobs.internal"
    assert "JOBS_PORT" in _node(con, "endpoint:tcp:7000")["port_envs"]


def test_listener_handlers_and_exposure(graph):
    _, con = graph
    recv = _edges(con, "RECEIVED_BY", "endpoint:tcp:6380")
    # net.createServer(onClient).listen(process.env.CACHE_PORT || CACHE_PORT, "127.0.0.1"); Rust
    # TcpListener::bind(format!("0.0.0.0:{}", port)) handed to run(listener)
    assert set(recv) == {"function:node/server.ts#onClient", "function:peer[bin]::run"}
    assert recv["function:node/server.ts#onClient"][1]["exposure"] == "loopback"
    assert recv["function:peer[bin]::run"][1]["exposure"] == "all"
    n = _node(con, "endpoint:tcp:6380")
    assert n["bind_addresses"] == ["0.0.0.0", "127.0.0.1"] and n["exposure"] == "all"
    # Kotlin ServerSocket(CONTROL_PORT) and Swift NWListener(using: .tcp, on: 4040): all interfaces
    assert set(_edges(con, "RECEIVED_BY", "endpoint:tcp:4040")) == {"function:demo.controlServer", "function:startControl"}
    assert set(_edges(con, "SENDS_TO", "endpoint:tcp:4040")) == {"function:demo.controlClient"}
    # socket.socket() without a type is TCP; the port is a parameter default
    r = _edges(con, "RECEIVED_BY", "endpoint:tcp:9900")
    assert r["function:pyapp.server.admin_console"][1]["how"] == "parameter default"
    assert not con.execute("select 1 from nodes where id='endpoint:udp:9900'").fetchone()


def test_wrapper_call_sites(graph):
    _, con = graph
    # peer::client::connect(addr) wraps TcpStream::connect(addr): its call site names the port
    s = _edges(con, "SENDS_TO", "endpoint:tcp:6380")
    assert s["function:ping::main"][1]["how"] == "call site"
    assert not con.execute("select 1 from edges where kind='SENDS_TO' and src like '%client::connect%'").fetchone()


def test_udp_multicast_and_senders(graph):
    st, con = graph
    n = _node(con, "endpoint:udp:8125")
    assert n["multicast_group"] == "239.1.2.3"
    assert set(_edges(con, "RECEIVED_BY", "endpoint:udp:8125")) == {"function:node/server.ts#onMetric",
                                                                     "function:pyapp.server.listen_metrics"}
    send = _edges(con, "SENDS_TO", "endpoint:udp:8125")
    assert set(send) == {"function:demo.sendMetric", "function:node/client.ts#sendMetric", "method:pyapp.client.MetricsClient.send"}
    assert send["method:pyapp.client.MetricsClient.send"][1]["how"] == "parameter default"
    assert set(_edges(con, "SENDS_TO", "endpoint:udp:5683")) == {"function:ping::main", "function:pingCoap"}
    assert set(_edges(con, "RECEIVED_BY", "endpoint:udp:5683")) == {"function:peer[bin]::main"}
    assert st["sockets"]["ephemeral"] == 2                # bind(("127.0.0.1", 0)) and UdpSocket::bind("0.0.0.0:0")


def test_env_port_without_value_matches_the_same_key(graph):
    _, con = graph
    # asyncio.open_connection("cache", os.environ.get("CACHE_PORT")) -> endpoint:tcp:env:CACHE_PORT, matched to the
    # listener whose port is read from CACHE_PORT with a default
    assert set(_edges(con, "SENDS_TO", "endpoint:tcp:env:CACHE_PORT")) == {"function:pyapp.client.ask_cache"}
    m = con.execute("select dst, confidence from edges where kind='MATCHES_ENDPOINT' and src='endpoint:tcp:env:CACHE_PORT'").fetchall()
    assert m == [("endpoint:tcp:6380", "heuristic")]


def test_protocols_view_lists_sockets(graph):
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph.protocols.view import protocols
    _, con = graph
    db = con.execute("pragma database_list").fetchone()[2]
    res = protocols(GraphStore(db), protocol="tcp")
    names = {e["name"] for e in res["endpoints"]}
    assert {"7000", "6380", "4040", "9900", "env:CACHE_PORT"} <= names


def test_c_bsd_sockets_and_libuv(tmp_path):
    db = tmp_path / "c.db"
    index_project(CFIX, db, "sockets-c")
    con = sqlite3.connect(db)
    assert set(_edges(con, "RECEIVED_BY", "endpoint:tcp:9123")) == {"function:echo_server"}
    assert set(_edges(con, "SENDS_TO", "endpoint:tcp:9123")) == {"function:echo_client"}
    assert _edges(con, "RECEIVED_BY", "endpoint:tcp:9123")["function:echo_server"][1]["exposure"] == "all"
    assert set(_edges(con, "RECEIVED_BY", "endpoint:udp:5353")) == {"function:beacon_listener"}
    assert set(_edges(con, "SENDS_TO", "endpoint:udp:5353")) == {"function:beacon_send"}


def test_value_parsing():
    from cg_code_graph.sockets import parse_addr, split_args
    assert parse_addr("127.0.0.1:6379") == ("127.0.0.1", "6379")
    assert parse_addr("[::]:8080") == ("::", "8080")
    assert parse_addr("tcp://0.0.0.0:9000") == ("0.0.0.0", "9000")
    assert parse_addr("localhost") == ("localhost", None)
    assert split_args('a, (b, c), "d,e", f(g, h)') == ["a", "(b, c)", '"d,e"', "f(g, h)"]


def _pairs(con, ep):
    return set(_edges(con, "SENDS_TO", ep)), set(_edges(con, "RECEIVED_BY", ep))


def test_mdns_service_types(graph):
    _, con = graph
    # zeroconf ServiceInfo(.., port=) advertises; ServiceBrowser([types]) / bonjour find / NWBrowser .bonjour browse
    assert _pairs(con, "endpoint:mdns:_printer._tcp") == (
        {"function:pyapp.apps.browse", "function:node/apps.ts#findPrinters", "function:browsePrinters"},
        {"function:pyapp.apps.advertise"})
    # bonjour publish({ type: "http" }) -> _http._tcp; NWListener.Service(type:)
    assert _pairs(con, "endpoint:mdns:_http._tcp") == (
        {"function:pyapp.apps.browse"}, {"function:node/apps.ts#publishWeb", "function:advertiseWeb"})


def test_osc_coap_ssdp(graph):
    _, con = graph
    assert _pairs(con, "endpoint:osc:/mixer/volume") == (
        {"function:pyapp.apps.osc_sender", "function:node/apps.ts#oscVolume"}, {"function:pyapp.apps.volume"})
    # the python-osc server and client are UDP sockets too
    assert _pairs(con, "endpoint:udp:9000") == ({"function:pyapp.apps.osc_sender"}, {"function:pyapp.apps.osc_receiver"})
    assert _pairs(con, "endpoint:coap:/time") == ({"function:pyapp.apps.coap_client"}, {"method:pyapp.apps.TimeResource.render_get"})
    assert _pairs(con, "endpoint:ssdp:urn:schemas-upnp-org:device:BinaryLight:1") == (
        {"function:pyapp.apps.ssdp_search", "function:node/apps.ts#findLights"}, {"function:pyapp.apps.ssdp_advertise"})


def test_keyword_only_start_server(tmp_path):
    # asyncio.start_server(client_connected_cb=.., port=..) without positional arguments crashed the scan (#39 fix)
    (tmp_path / "app").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "kw"\nversion = "0.1.0"\n')
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "serve.py").write_text(
        "import asyncio\n\n\nasync def on_client(reader, writer):\n    writer.close()\n\n\nasync def main():\n"
        "    server = await asyncio.start_server(client_connected_cb=on_client, host=\"127.0.0.1\", port=7123)\n"
        "    await server.serve_forever()\n")
    db = tmp_path / "g.db"
    st = index_project(tmp_path, db, "kw")
    assert st["sockets"]["tcp_listeners"] == 1 and "scan_errors" not in st["sockets"]
    con = sqlite3.connect(db)
    assert set(_edges(con, "RECEIVED_BY", "endpoint:tcp:7123")) == {"function:app.serve.on_client"}

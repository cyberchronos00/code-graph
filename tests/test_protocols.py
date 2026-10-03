"""Protocol links (#31): shared matchers, the registry and builder helpers, MATCHES_ENDPOINT at index and link time,
the adapters over existing kinds (Nest messages, Bull jobs, HTTP, Pusher), checks, `cg protocols` / MCP, and the
first endpoint protocol end to end (python-socketio, two services linked by cg link)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import protocols as P  # noqa: E402
from codegraph.protocols import matchers as M  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.link import link  # noqa: E402

FX = ROOT / "tests" / "protocol_fixtures"


def cli(*a):
    return subprocess.run([sys.executable, "-m", "codegraph.cli", *a], cwd=ROOT, capture_output=True, text=True)


# ------------------------------------------------------------------ matchers
def test_path_matcher():
    assert M.path("/orders/42", "/orders/{id}")["wild"] == 1
    assert M.path("/orders/{x}", "/orders/{id}") is not None
    assert M.path("/orders", "/users") is None
    assert M.path("/a/b/c", "/a/{rest*}") is not None
    assert M.path("/x", "/{id}") is None              # at least one literal segment


def test_mqtt_matcher():
    assert M.mqtt("devices/7/state", "devices/+/state") == {"lit": 2, "wild": 1, "multi": 0, "ph_into_lit": 0}
    assert M.mqtt("devices/7/state", "devices/#")["multi"] == 1
    assert M.mqtt("devices", "devices/#") is not None   # `#` also matches the parent level
    assert M.mqtt("devices/7", "devices/+/state") is None
    assert M.mqtt("a/b", "a/#/b") is None               # `#` only last
    assert M.mqtt("devices/{id}/state", "devices/+/state")["wild"] == 1


def test_nats_matcher():
    assert M.nats("orders.eu.42", "orders.>")["multi"] == 1
    assert M.nats("orders", "orders.>") is None          # `>` needs one or more tokens
    assert M.nats("orders.eu", "orders.*")["wild"] == 1
    assert M.nats("orders.eu.42", "orders.*") is None


def test_amqp_topic_matcher():
    assert M.amqp_topic("quick.orange.rabbit", "*.orange.*") is not None
    assert M.amqp_topic("lazy.pink.rabbit", "lazy.#") is not None
    assert M.amqp_topic("a.b.c", "a.#.c") is not None and M.amqp_topic("a.c", "a.#.c") is not None   # `#` = zero or more
    assert M.amqp_topic("quick.brown.fox", "*.orange.*") is None


def test_glob_template_exact_and_rank():
    assert M.glob("audit.login", "audit.*")["wild"] == 1
    assert M.glob("audit.login", "billing.*") is None
    assert M.template("/#order:42", "/#order:{id}")["wild"] == 1
    assert M.template("/#order:{s}", "/#order:paid")["ph_into_lit"] == 1
    assert M.exact("a", "a") and M.exact("a", "b") is None
    assert M.rank(M.nats("o.eu", "o.eu")) > M.rank(M.nats("o.eu", "o.*")) > M.rank(M.nats("o.eu", "o.>"))
    assert M.confidence(M.nats("o.eu", "o.*"), tied=False) == "resolved"
    assert M.confidence(M.template("/#o:{s}", "/#o:x"), tied=False) == "heuristic"


def test_normalise():
    assert P.normalise("orders.${order.id}") == "orders.{id}"
    assert P.normalise("/users/:id/x") == "/users/{id}/x"
    assert P.normalise("a.{{ id }}") == "a.{id}"


# ------------------------------------------------------------------ registry + builder + index-time matching
def _builder():
    from codegraph.core.plugin import GraphBuilder
    return GraphBuilder()


def test_registry_builder_and_matching():
    b = _builder()
    P.register(P.Protocol("test-rpc", "tcp", "test request protocol", matcher=M.nats))   # a plugin's own protocol
    for f in ("function:pub", "function:pub2", "function:sub_eu", "function:sub_all", "function:dead", "function:req",
              "function:h1", "function:h2"):
        b.add_node(f.split(":")[0], f.split(":")[1])
    P.protocol_send(b, "nats", "orders.eu.42", "function:pub", "a.py", 1, "exact", role="publish")
    P.protocol_send(b, "nats", "billing.x", "function:pub2", "a.py", 2, "exact")
    P.protocol_receive(b, "nats", "orders.*.42", "function:sub_eu", "b.py", 1, "exact")
    P.protocol_receive(b, "nats", "orders.>", "function:sub_all", "b.py", 2, "exact")
    P.protocol_receive(b, "nats", "never.sent", "function:dead", "b.py", 3, "exact", guards=[])
    P.protocol_send(b, "test-rpc", "svc.get", "function:req", "c.py", 1, "exact", role="request")
    P.protocol_receive(b, "test-rpc", "svc.*", "function:h1", "d.py", 1, "exact")
    P.protocol_receive(b, "test-rpc", "*.get", "function:h2", "d.py", 2, "exact")
    st = P.apply(b)
    me = {(e.src, e.dst): e for e in b.edges.values() if e.kind == "MATCHES_ENDPOINT"}
    # fan-out (NATS): every matching subscription
    assert ("endpoint:nats:orders.eu.42", "endpoint:nats:orders.*.42") in me
    assert ("endpoint:nats:orders.eu.42", "endpoint:nats:orders.>") in me
    assert me[("endpoint:nats:orders.eu.42", "endpoint:nats:orders.>")].confidence == "resolved"
    assert me[("endpoint:nats:orders.eu.42", "endpoint:nats:orders.*.42")].file == "b.py"
    # request protocol: equally specific receivers -> ambiguous (heuristic, both kept)
    amb = [e for (s, d), e in me.items() if s == "endpoint:test-rpc:svc.get"]
    assert len(amb) == 2 and all(e.confidence == "heuristic" and e.attrs["ambiguous"] == 2 for e in amb)
    assert st["nats"]["no_receiver"] == 1 and st["nats"]["no_sender"] == 1 and st["test-rpc"]["ambiguous"] == 1
    n = b.nodes["endpoint:nats:orders.>"]
    assert n.attrs["pattern"] == "orders.>" and n.entry_kind == "message_handler" and n.attrs["side"] == "receive"
    assert b.nodes["endpoint:nats:never.sent"].attrs["guards"] == []
    P.REGISTRY.pop("test-rpc")


def test_external_match():
    assert P.external_match(["kafka:audit.*"], "kafka", "audit.login") == "kafka:audit.*"
    assert P.external_match(["kafka:audit.*"], "kafka", "orders") is None
    assert P.external_match(["socketio:*"], "socketio", "/#x") == "socketio:*"


def test_config_protocols_external(tmp_path):
    from codegraph.config import ConfigError, load
    (tmp_path / ".cg.yaml").write_text("version: 1\nprotocols:\n  external: [\"kafka:audit.*\"]\n")
    assert load(tmp_path)["protocols"] == {"external": ["kafka:audit.*"]}
    (tmp_path / ".cg.yaml").write_text("version: 1\nprotocols:\n  external: [\"audit\"]\n")
    with pytest.raises(ConfigError):
        load(tmp_path)


# ------------------------------------------------------------------ adapters over existing kinds (ids unchanged)
@pytest.fixture(scope="module")
def nest_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("nest") / "nest.db"
    index_project(ROOT / "examples" / "bookstore-nest", db, "bookstore-nest")
    return db


def test_nest_messages_and_bull_jobs_adapted(nest_db):
    from codegraph.protocols.view import protocols
    st = GraphStore(nest_db)
    before = {r[0] for r in st.q("SELECT id FROM nodes WHERE kind IN ('message','job')")}
    res = protocols(st)
    s = res["summary"]
    assert {"http", "bull", "nest-rpc"} <= set(s)
    assert s["bull"]["receive"] >= 1 and s["nest-rpc"]["receive"] >= 1
    jobs = protocols(st, protocol="bull")["endpoints"]
    assert jobs and all(j["id"].startswith("job:") for j in jobs) and all(j["receivers"] for j in jobs)
    msgs = protocols(st, protocol="nest-rpc")["endpoints"] + protocols(st, protocol="nest-ws")["endpoints"]
    assert msgs and all(m["id"].startswith("message:") for m in msgs)
    assert before == {r[0] for r in st.q("SELECT id FROM nodes WHERE kind IN ('message','job')")}
    assert not list(st.q("SELECT 1 FROM nodes WHERE kind='endpoint'"))       # no new nodes for adapted kinds
    r = cli("protocols", "--db", str(nest_db))
    assert r.returncode == 0 and "bull" in r.stdout and "nest-rpc" in r.stdout


def test_nest_message_guards(nest_db, tmp_path):
    """#69: @UseGuards / APP_GUARD on message handlers are recorded and classified like route guards."""
    from codegraph.protocols.view import protocols
    msgs = {m["name"]: m for p in ("nest-rpc", "nest-ws") for m in protocols(GraphStore(nest_db), protocol=p)["endpoints"]}
    rpc = msgs['{"cmd":"inventory.check"}']
    assert rpc["guards"] == ["ThrottleGuard"] and "unguarded" in rpc["checks"]        # APP_GUARD, not auth
    proj = tmp_path / "bookstore-nest"
    shutil.copytree(ROOT / "examples" / "bookstore-nest", proj, ignore=shutil.ignore_patterns("node_modules"))
    gw = proj / "src" / "inventory" / "inventory.gateway.ts"
    gw.write_text("import { UseGuards } from '@nestjs/common';\n" + gw.read_text().replace(
        "  @SubscribeMessage('watch')", "  @UseGuards(WsJwtGuard)\n  @SubscribeMessage('watch')"))
    db = tmp_path / "g.db"
    index_project(proj, db, "bookstore-nest")
    ws = protocols(GraphStore(db), protocol="nest-ws")["endpoints"]
    assert len(ws) == 1 and ws[0]["guards"] == ["ThrottleGuard", "WsJwtGuard"] and "unguarded" not in ws[0]["checks"]

# ------------------------------------------------------------------ python-socketio end to end, two services
@pytest.fixture(scope="module")
def linked(tmp_path_factory):
    d = tmp_path_factory.mktemp("sio")
    api, worker, out = d / "api.db", d / "worker.db", d / "link.db"
    sa = index_project(FX / "orders-api", api, "orders-api")
    sw = index_project(FX / "worker", worker, "worker")
    res = link(str(worker), str(api), str(out), backend_name="worker", frontend_name="orders-api")
    return {"api": api, "worker": worker, "db": out, "sa": sa, "sw": sw, "res": res}


def test_socketio_extraction(linked):
    st = GraphStore(linked["worker"])
    rb = {(r["src"], r["dst"]) for r in st.q("SELECT src, dst FROM edges WHERE kind='RECEIVED_BY'")}
    assert ("endpoint:socketio:/orders#order:created", "function:worker.sockets.on_order_created") in rb
    assert ("endpoint:socketio:/#ping", "function:worker.sockets.ping") in rb
    assert not any("connect" in s for s, _ in rb)                          # lifecycle callbacks are not endpoints
    n = json.loads(st.node("endpoint:socketio:/orders#order:created")["attrs"])
    assert n["guards"] == ["connect (connect handler rejects)"] and n["namespace"] == "/orders"
    assert json.loads(st.node("endpoint:socketio:/#ping")["attrs"])["guards"] == []
    sa = GraphStore(linked["api"])
    sends = {r["dst"]: json.loads(r["attrs"]) for r in sa.q("SELECT dst, attrs FROM edges WHERE kind='SENDS_TO'")}
    assert sends["endpoint:socketio:/orders#order:created"]["role"] == "emit"
    assert "endpoint:socketio:/orders#order:{status}" in sends               # f-string -> template
    assert linked["sw"]["plugins"]["python"]["socketio"]["receivers"] == 3


def test_link_path_from_producer_route_to_consumer_table(linked):
    st = GraphStore(linked["db"])
    stats = linked["res"]["stats"]["protocols"]["socketio"]
    assert stats["match_edges"] == 2 and stats["ambiguous"] == 1
    # #69: the .cg.yaml protocols.external emit (/#audit:order) is `external`, not `no_receiver`, in the link stats
    assert stats["no_receiver"] == 1 and stats["external"] == 1
    assert linked["sa"]["protocols"]["socketio"]["external"] == 1
    r = cli("path", "route:POST /orders", "table:orders", "--db", str(linked["db"]))
    assert r.returncode == 0, r.stderr
    for hop in ("SENDS_TO", "endpoint:socketio:/orders#order:created", "RECEIVED_BY", "WRITES_TABLE", "table:orders"):
        assert hop in r.stdout
    # the template sender reaches both receivers through MATCHES_ENDPOINT (impact / reaches cross the boundary)
    r = cli("reaches", "table:shipments", "--db", str(linked["db"]))
    assert "app.main.set_status" in r.stdout and "MATCHES_ENDPOINT" in r.stdout
    me = list(st.q("SELECT src, dst, confidence, file FROM edges WHERE kind='MATCHES_ENDPOINT'"))
    assert {m["dst"] for m in me} == {"endpoint:socketio:/orders#order:created", "endpoint:socketio:/orders#order:shipped"}
    assert all(m["confidence"] == "heuristic" and m["file"].startswith("worker/") for m in me)


def test_protocols_view_checks(linked):
    from codegraph.protocols.view import protocols
    st = GraphStore(linked["db"])
    res = protocols(st, protocol="socketio")
    by = {e["name"]: e for e in res["endpoints"]}
    assert by["/orders#order:created"]["linked"] and by["/orders#order:created"]["checks"] == []
    assert by["/orders#order:cancelled"]["checks"] == ["no_receiver"]
    assert by["/#ping"]["checks"] == ["no_sender", "unguarded"]
    assert by["/orders#order:{status}"]["checks"] == ["ambiguous"]
    assert "protocols.external" in by["/#audit:order"]["external"] and by["/#audit:order"]["checks"] == []
    assert res["summary"]["socketio"]["linked"] == 3
    one = protocols(st, "/orders#order:created")["endpoints"][0]
    assert one["senders"][0]["entry_kinds"] == {"http_route": 1}
    # the single repos: a side is judged only when the graph holds the other side of the protocol
    alone = protocols(GraphStore(linked["api"]), protocol="socketio")["endpoints"]
    assert not any("no_receiver" in e["checks"] for e in alone)
    r = cli("protocols", "--db", str(linked["db"]), "--protocol", "socketio", "--unmatched")
    assert r.returncode == 0 and "no_receiver" in r.stdout and "[socketio] /orders#order:created" not in r.stdout
    j = json.loads(cli("protocols", "--db", str(linked["db"]), "--side", "receive", "--json").stdout)
    assert j["endpoints"] and all(e["side"] in ("receive", "both") for e in j["endpoints"])
    assert "no protocol endpoint matches" in cli("protocols", "nothing-here", "--db", str(linked["db"])).stdout
    # Django signals the framework sends (model signals, request_finished, ...) are not senderless; custom ones are
    sig = {e["name"]: e for e in protocols(st, protocol="django-signal")["endpoints"]}
    assert sig["django.db.models.signals.post_save[Order]"]["linked"]          # the handler's Order write fires it
    assert sig["django.db.models.signals.pre_delete[Shipment]"]["external"] == "sent by the framework"
    assert sig["django.db.models.signals.pre_delete[Shipment]"]["checks"] == []
    assert sig["worker.signals.shipment_ready"]["checks"] == ["no_sender"]


def test_mcp_protocol_links(linked, monkeypatch):
    from codegraph import mcp_server
    monkeypatch.setattr(mcp_server, "_st", lambda: GraphStore(linked["db"]))
    out = mcp_server.protocol_links(protocol="socketio", unmatched=True)
    out = out if isinstance(out, str) else str(out)
    assert "order:cancelled" in out and "no_receiver" in out


def test_socketio_direction(tmp_path):
    """#69: a Socket.IO client emit reaches server handlers only; a client's own handler of that name is not its
    receiver (and a server emit reaches client handlers)."""
    from codegraph.protocols.view import protocols
    (tmp_path / "requirements.txt").write_text("python-socketio\n")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "client.py").write_text(
        "import socketio\n\nsio = socketio.Client()\n\n\n@sio.on('chat')\ndef on_chat(data):\n    print(data)\n\n\n"
        "def say(text):\n    sio.emit('chat', text)\n\n\ndef ping():\n    sio.emit('ping', 1)\n")
    (tmp_path / "app" / "server.py").write_text(
        "import socketio\n\nsrv = socketio.Server()\n\n\n@srv.on('ping')\ndef on_ping(sid, data):\n    srv.emit('pong', data)\n\n\n"
        "@srv.on('hello')\ndef on_hello(sid, data):\n    pass\n")
    st = index_project(tmp_path, tmp_path / "g.db", "sio")
    s = st["protocols"]["socketio"]
    assert s["matched"] == 1 and s["no_receiver"] == 2        # ping -> server; chat (client-only) and pong (no client)
    by = {e["name"]: e for e in protocols(GraphStore(tmp_path / "g.db"), protocol="socketio")["endpoints"]}
    assert by["/#ping"]["linked"] and "no_receiver" not in by["/#ping"]["checks"]
    assert not by["/#chat"]["linked"] and {"no_receiver", "no_sender"} <= set(by["/#chat"]["checks"])
    assert by["/#hello"]["checks"][:1] == ["no_sender"]

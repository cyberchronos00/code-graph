"""gRPC services from `.proto` contracts paired with their servers and clients (#33): codegraph/rpc.py over
tests/rpc_fixture (Python, Node / TypeScript, Connect, Rust tonic, Kotlin) and tests/rpc_cpp_fixture (grpc++)."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "rpc_fixture"
CFIX = ROOT / "tests" / "rpc_cpp_fixture"
RG = "endpoint:grpc:routeguide.RouteGuide/"


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    db = tmp_path_factory.mktemp("rpc") / "g.db"
    st = index_project(FIX, db, "rpc")
    return st, sqlite3.connect(db)


@pytest.fixture(scope="module")
def cgraph(tmp_path_factory):
    db = tmp_path_factory.mktemp("rpcc") / "g.db"
    st = index_project(CFIX, db, "rpcc")
    return st, sqlite3.connect(db)


def _edges(con, kind, ep):
    col, other = ("dst", "src") if kind == "SENDS_TO" else ("src", "dst")
    return {r[0]: (r[1], json.loads(r[2] or "{}")) for r in con.execute(
        f"select {other}, confidence, attrs from edges where kind=? and {col}=?", (kind, ep))}


def _node(con, nid):
    r = con.execute("select kind, attrs from nodes where id=?", (nid,)).fetchone()
    return (r[0], json.loads(r[1])) if r else (None, None)


def test_contract_endpoints(graph):
    st, con = graph
    g = st["rpc"]["grpc"]
    assert g["services"] == 3 and g["methods"] == 6 and g["proto_files"] == 2
    kind, a = _node(con, RG + "RouteChat")
    assert kind == "endpoint" and a["protocol"] == "grpc"
    assert (a["service"], a["package"], a["method"], a["streaming"]) == ("routeguide.RouteGuide", "routeguide", "RouteChat", "bidi")
    assert (a["request"], a["response"]) == ("RouteNote", "RouteNote")
    assert a["declared_in"] == "protos/route_guide.proto:10"
    assert _node(con, RG + "ListFeatures")[1]["streaming"] == "server"
    assert _node(con, RG + "RecordRoute")[1]["streaming"] == "client"
    assert _node(con, RG + "GetFeature")[1]["streaming"] == "unary"
    # the commented-out method is not a contract
    assert _node(con, "endpoint:grpc:fleet.v1.Dispatch/Retired") == (None, None)


def test_servers_across_languages(graph):
    _, con = graph
    recv = _edges(con, "RECEIVED_BY", RG + "GetFeature")
    assert set(recv) == {"method:pyapp.route_server.RouteGuideServicer.GetFeature", "function:node/server.js#getFeature",
                         "method:guide::<Guide as RouteGuide>::get_feature"}
    assert recv["method:pyapp.route_server.RouteGuideServicer.GetFeature"][0] == "exact"     # generated base class
    assert recv["function:node/server.js#getFeature"][0] == "resolved"                     # addService handler map
    assert set(_edges(con, "RECEIVED_BY", RG + "RouteChat")) == {"function:node/server.js#routeChat"}  # shorthand key
    assert set(_edges(con, "RECEIVED_BY", RG + "RecordRoute")) == {"method:guide::<Guide as RouteGuide>::record_route"}
    disp = _edges(con, "RECEIVED_BY", "endpoint:grpc:fleet.v1.Dispatch/Assign")
    assert set(disp) == {"method:pyapp.dispatch_server.Dispatcher.Assign", "method:demo.DispatchService.assign",
                         "function:node/connect.ts#assignRide"}
    assert disp["method:pyapp.dispatch_server.Dispatcher.Assign"][0] == "resolved"   # add_DispatchServicer_to_server(svc, ..)
    assert disp["method:demo.DispatchService.assign"][0] == "exact"                  # DispatchCoroutineImplBase
    # a servicer method that is not in the contract gets no edge
    assert not con.execute("select 1 from edges where dst='method:pyapp.route_server.RouteGuideServicer.describe'"
                           " and kind='RECEIVED_BY'").fetchone()


def test_clients_across_languages(graph):
    _, con = graph
    assert set(_edges(con, "SENDS_TO", RG + "ListFeatures")) == {"function:pyapp.client.run", "function:guide::ask"}
    assert set(_edges(con, "SENDS_TO", RG + "RouteChat")) == {"function:pyapp.client.run"}
    # a stub handed to a helper: the file's own stubs name the service
    one = _edges(con, "SENDS_TO", RG + "GetFeature")
    assert set(one) == {"function:pyapp.client.get_one"} and one["function:pyapp.client.get_one"][0] == "resolved"
    assert set(_edges(con, "SENDS_TO", "endpoint:grpc:fleet.v1.Dispatch/Assign")) == {
        "function:pyapp.client.assign", "method:node/dispatch.ts#Fleet.assign", "function:node/connect.ts#reassign",
        "method:demo.FleetClient.send"}


def test_same_short_name_in_two_packages(graph):
    _, con = graph
    # fleet_pb2_grpc.RouteGuideStub(..) is fleet.v1.RouteGuide, not routeguide.RouteGuide
    assert set(_edges(con, "SENDS_TO", "endpoint:grpc:fleet.v1.RouteGuide/GetFeature")) == {"function:pyapp.fleet_client.lookup"}
    kind, a = _node(con, "endpoint:grpc:fleet.v1.RouteGuide/GetFeature")
    assert a["package"] == "fleet.v1" and a["declared_in"] == "protos/fleet/fleet.proto:7"


def test_grpcpp_server_and_client(cgraph):
    st, con = cgraph
    recv = _edges(con, "RECEIVED_BY", RG + "GetFeature")
    assert set(recv) == {"method:RouteGuideImpl::GetFeature"}          # declared in the class, defined out of line
    assert set(_edges(con, "RECEIVED_BY", RG + "ListFeatures")) == {"method:RouteGuideImpl::ListFeatures"}
    # stub_(RouteGuide::NewStub(channel)) in the constructor's member initializer, used by every method
    assert set(_edges(con, "SENDS_TO", RG + "GetFeature")) == {"method:RouteGuideClient::Lookup"}
    assert set(_edges(con, "SENDS_TO", RG + "RouteChat")) == {"method:RouteGuideClient::Chat"}
    assert not any(v.get("samples") for v in st["rpc"].values() if isinstance(v, dict))


TH = "endpoint:thrift:calc.Calculator/"
SH = "endpoint:thrift:shared.SharedService/getStruct"


def test_thrift_contracts(graph):
    st, con = graph
    t = st["rpc"]["thrift"]
    assert t["services"] == 2 and t["methods"] == 5
    kind, a = _node(con, TH + "divide")
    assert kind == "endpoint" and a["protocol"] == "thrift" and a["throws"] == ["InvalidOperation"]
    assert a["declared_in"] == "idl/calc.thrift:14"
    assert _node(con, TH + "zip")[1]["streaming"] == "oneway"
    # an inherited method keeps the declaring service; the commented-out one is not a contract
    assert _node(con, SH)[1]["service"] == "shared.SharedService"
    assert _node(con, TH + "retired") == (None, None) and _node(con, TH + "getStruct") == (None, None)


def test_thrift_servers_and_clients(graph, cgraph):
    _, con = graph
    _, ccon = cgraph
    assert set(_edges(con, "RECEIVED_BY", TH + "add")) == {"method:pyapp.calc_server.CalculatorHandler.add"}  # Processor(handler)
    assert set(_edges(con, "RECEIVED_BY", SH)) == {"method:pyapp.calc_server.CalculatorHandler.getStruct"}
    assert set(_edges(con, "RECEIVED_BY", TH + "divide")) == {"function:node/calc_server.js#divide"}   # createServer map
    assert set(_edges(con, "RECEIVED_BY", TH + "zip")) == {"function:node/calc_server.js#zip"}
    assert set(_edges(con, "SENDS_TO", SH)) == {"function:pyapp.calc_client.run"}
    assert set(_edges(ccon, "RECEIVED_BY", TH + "add")) == {"method:CalculatorHandler::add"}           # : public CalculatorIf
    assert set(_edges(ccon, "RECEIVED_BY", SH)) == {"method:CalculatorHandler::getStruct"}
    assert set(_edges(ccon, "SENDS_TO", TH + "ping")) == {"function:ask"}                               # CalculatorClient client(..)


TR = "endpoint:trpc:"


def test_trpc_router_tree(graph):
    st, con = graph
    t = st["rpc"]["trpc"]
    assert t["procedures"] == 4 and t["resolvers"] == 4 and not t.get("samples")
    kind, a = _node(con, TR + "post.create")
    assert kind == "endpoint" and a["protocol"] == "trpc" and a["procedure"] == "mutation"
    assert a["root"] == "appRouter" and a["declared_in"] == "node/trpc/post.ts:13"
    # the inline resolver is its own node, named after the router variable and key
    assert set(_edges(con, "RECEIVED_BY", TR + "post.create")) == {"function:node/trpc/post.ts#postRouter.create"}
    assert _node(con, "function:node/trpc/post.ts#postRouter.create")[1]["inline_handler"]
    assert set(_edges(con, "RECEIVED_BY", TR + "admin.stats")) == {"function:node/trpc/root.ts#appRouter.admin.stats"}
    # a shorthand mount of a procedure variable from another file
    assert set(_edges(con, "RECEIVED_BY", TR + "ping")) == {"function:node/trpc/health.ts#ping"}


def test_trpc_clients(graph):
    _, con = graph
    assert set(_edges(con, "SENDS_TO", TR + "post.all")) == {"function:node/trpc/page.tsx#Posts"}
    assert set(_edges(con, "SENDS_TO", TR + "admin.stats")) == {"function:node/trpc/page.tsx#prefetchStats"}
    assert set(_edges(con, "SENDS_TO", TR + "ping")) == {"function:node/trpc/page.tsx#serverPing"}
    # a path that no router declares gets no endpoint
    assert _node(con, TR + "post.missing") == (None, None)
    # the resolver body still calls its helper
    assert con.execute("select 1 from edges where kind='CALLS' and src='function:node/trpc/post.ts#postRouter.all'"
                       " and dst='function:node/trpc/post.ts#loadAll'").fetchone()


JR = "endpoint:jsonrpc:"


def test_jsonrpc(graph):
    _, con = graph
    assert set(_edges(con, "RECEIVED_BY", JR + "add")) == {"function:node/jr_server.js#add"}            # jayson method map
    assert set(_edges(con, "RECEIVED_BY", JR + "echo")) == {"function:node/jr_server.js#methods.echo"}
    assert set(_edges(con, "SENDS_TO", JR + "add")) == {"function:node/jr_client.js#sum"}               # client.request("add")
    assert set(_edges(con, "RECEIVED_BY", JR + "math.double")) == {"function:pyapp.jr_server.double"}   # @method(name=..)
    assert set(_edges(con, "SENDS_TO", JR + "ping")) == {"function:pyapp.jr_client.remote_ping"}        # request payload
    assert set(_edges(con, "SENDS_TO", JR + "math.double")) == {"function:pyapp.jr_client.remote_double"}
    recv = _edges(con, "RECEIVED_BY", JR + "state_getKeys")                                            # jsonrpsee namespace
    assert set(recv) == {"method:guide::jr::<RpcServerImpl as RpcServer>::storage_keys"} and recv[
        "method:guide::jr::<RpcServerImpl as RpcServer>::storage_keys"][0] == "exact"
    hello = _edges(con, "RECEIVED_BY", JR + "say_hello")
    assert set(hello) == {"function:guide::jr::build_module"} and hello["function:guide::jr::build_module"][0] == "heuristic"

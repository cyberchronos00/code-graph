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
    assert st["rpc"]["services"] == 3 and st["rpc"]["methods"] == 6 and st["rpc"]["proto_files"] == 2
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
    assert not st["rpc"].get("samples")

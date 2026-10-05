"""XPC services and Darwin notifications (#38 part 3) over tests/xpc_fixture: a privileged helper listening on a
mach service with an exported @objc protocol, a client connecting and calling through remoteObjectProxy, and a Darwin
notification posted by the helper and observed by the client."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from codegraph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "xpc_fixture"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("xpc") / "x.db"
    st = index_project(FX, d, "xpc")
    con = sqlite3.connect(d)
    e = {(k, s, t): (c, json.loads(a or "{}")) for k, s, t, c, a in
         con.execute("select kind, src, dst, confidence, attrs from edges where kind in ('SENDS_TO', 'RECEIVED_BY')")}
    return e, st


def test_service(db):
    e = db[0]
    assert e[("SENDS_TO", "method:HelperClient.connect", "endpoint:xpc:com.example.Helper")][1]["role"] == "connect"
    assert ("RECEIVED_BY", "endpoint:xpc:com.example.Helper", "method:Helper.listener") in e   # the delegate method


def test_protocol_methods(db):
    e = db[0]
    for m in ("version", "setFanSpeed"):
        assert ("RECEIVED_BY", f"endpoint:xpc:HelperProtocol.{m}", f"method:Helper.{m}") in e
    assert e[("SENDS_TO", "method:HelperClient.checkVersion", "endpoint:xpc:HelperProtocol.version")][0] == "resolved"
    assert ("SENDS_TO", "method:HelperClient.speedUp", "endpoint:xpc:HelperProtocol.setFanSpeed") in e
    assert ("SENDS_TO", "method:HelperClient.setFanSpeed", "endpoint:xpc:HelperProtocol.setFanSpeed") in e
    assert not any(k[1] == "method:HelperClient.reset" for k in e)                  # self.setFanSpeed: the wrapper
    assert not any(k[1].startswith("method:Helper.") and k[2].startswith("endpoint:xpc:HelperProtocol") for k in e)


def test_darwin_notifications(db):
    e = db[0]
    n = "endpoint:darwin-notification:com.example.helper.updated"
    assert e[("SENDS_TO", "method:Helper.setFanSpeed", n)][0] == "resolved"        # a constant
    assert e[("RECEIVED_BY", n, "method:HelperClient.observe")][0] == "exact"       # "x" as CFString


def test_stats(db):
    assert db[1]["apple_ipc"] == {"connections": 1, "listeners": 1, "xpc_protocols": 1, "xpc_methods": 2,
                                  "xpc_calls": 3, "darwin_observers": 1, "darwin_posts": 1}

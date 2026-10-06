"""Local IPC in JS / TS (#38 part 1) over tests/local_ipc_fixture: Web Workers (postMessage / onmessage both ways,
comlink wrap / expose), a service worker, BroadcastChannel, window.postMessage with and without an origin check,
browser-extension runtime / tabs messaging and ports, and a native messaging host manifest."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "local_ipc_fixture"


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("ipc")
    out = {}
    for r in ("web-app", "web-ext"):
        out[r + "-stats"] = index_project(FX / r, d / f"{r}.db", r)
        out[r] = d / f"{r}.db"
    return out


def _edges(db, kind):
    con = sqlite3.connect(db)
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in
            con.execute("select src, dst, confidence, attrs from edges where kind=?", (kind,))}


def _node(db, nid):
    con = sqlite3.connect(db)
    r = con.execute("select attrs from nodes where id=?", (nid,)).fetchone()
    return json.loads(r[0] or "{}") if r else None


def test_workers(dbs):
    s, r = _edges(dbs["web-app"], "SENDS_TO"), _edges(dbs["web-app"], "RECEIVED_BY")
    w = "endpoint:worker:src/workers/resize.worker.ts"
    assert ("function:src/main.ts#resize", w) in s
    assert (w, "module:src/workers/resize.worker.ts") in r                      # inline self.onmessage
    assert ("module:src/workers/resize.worker.ts", w + ":out") in s            # self.postMessage back
    assert (w + ":out", "function:src/main.ts#onResult") in r                  # worker.onmessage = onResult
    m = "endpoint:worker:src/workers/math.worker.ts"
    assert s[("module:src/math.ts", m)][1]["library"] == "comlink"
    assert r[(m, "module:src/workers/math.worker.ts")][1]["how"] == "comlink expose"


def test_service_worker(dbs):
    s, r = _edges(dbs["web-app"], "SENDS_TO"), _edges(dbs["web-app"], "RECEIVED_BY")
    assert ("function:src/offline.ts#cacheNow", "endpoint:worker:service-worker") in s
    assert ("endpoint:worker:service-worker", "function:src/sw.ts#handleSwMessage") in r
    assert ("function:src/sw.ts#handleSwMessage", "endpoint:worker:service-worker:out") in s
    assert ("endpoint:worker:service-worker:out", "function:src/offline.ts#onSwMessage") in r


def test_broadcastchannel(dbs):
    s, r = _edges(dbs["web-app"], "SENDS_TO"), _edges(dbs["web-app"], "RECEIVED_BY")
    assert s[("function:src/sync.ts#publishLogout", "endpoint:broadcastchannel:app-sync")][0] == "resolved"   # via const
    assert ("endpoint:broadcastchannel:app-sync", "function:src/tabs.ts#onSync") in r


def test_postmessage(dbs):
    db = dbs["web-app"]
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    snd = s[("function:src/embed.ts#reportHeight", "endpoint:postmessage:resize")]
    assert snd[1]["target_origin"] == "*" and snd[1]["target"] == "parent"
    assert "target_origin" not in s[("function:src/embed.ts#openPicker", "endpoint:postmessage:pick")][1]
    assert {k[0] for k in r if k[1] == "module:src/host.ts"} == {"endpoint:postmessage:resize", "endpoint:postmessage:close"}
    assert _node(db, "endpoint:postmessage:resize")["guards"] == ["origin check"]
    assert ("endpoint:postmessage:*", "function:src/unsafe.ts#onAnyMessage") in r
    assert _node(db, "endpoint:postmessage:*")["guards"] == []                 # no origin check
    m = _edges(db, "MATCHES_ENDPOINT")
    assert ("endpoint:postmessage:pick", "endpoint:postmessage:*") in m       # any-type listener


def test_extension(dbs):
    db = dbs["web-ext"]
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    assert ("function:src/popup.ts#askSettings", "endpoint:extension:getSettings") in s
    assert ("function:src/popup.ts#highlight", "endpoint:extension:highlight") in s
    assert ("function:src/popup.ts#openDevtools", "endpoint:extension:port:devtools") in s
    assert {k[0] for k in r if k[1] == "module:src/background.ts"} == {
        "endpoint:extension:getSettings", "endpoint:extension:save", "endpoint:extension:port:devtools"}
    assert ("endpoint:extension:highlight", "function:src/content.ts#onMessage") in r
    assert "guards" not in _node(db, "endpoint:extension:getSettings")         # internal messages: no guard verdict


def test_native_messaging(dbs):
    db = dbs["web-ext"]
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    assert ("module:src/background.ts", "endpoint:native-messaging:com.acme.host") in s
    e = r[("endpoint:native-messaging:com.acme.host", "module:host.main")]
    assert e[1]["program"] == "host/main.py"


def test_extension_enum_types_and_callee_switch(dbs):
    db = dbs["web-ext"]
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    assert s[("function:src/popup.ts#saveAll", "endpoint:extension:ui-bg-save-all")][0] == "resolved"   # enum member
    h = "method:src/messenger.ts#Messenger.onUiMessage"                       # the callee that switches on the type
    for t in ("ui-bg-save-all", "ui-bg-reset"):
        assert r[(f"endpoint:extension:{t}", h)][1]["via"] == "method:src/messenger.ts#Messenger.listener"


def test_worker_getter_and_private_fields(dbs):
    db = dbs["web-app"]
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    assert ("function:src/getter.ts#shrinkLater", "endpoint:worker:src/workers/resize.worker.ts") in s   # getWorker()
    assert not [k for k in r if "getter.ts" in k[1]]                          # `this.#onmessage = h` is no listener


def test_extension_ports_match_only_port_listeners():
    from cg_code_graph.protocols import matchers as M
    assert M.extension("port:devtools", "port:*") and M.extension("save", "*")
    assert M.extension("port:devtools", "*") is None and M.extension("save", "port:*") is None

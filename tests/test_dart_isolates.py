"""Dart isolates (#38 part 3) over tests/isolate_fixture: Isolate.spawn / run / spawnUri and compute to the entry
function, and the entry's SendPort.send / Isolate.exit back to the spawner's port listener (`<entry>:out`)."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "isolate_fixture"
W = "lib/worker.dart"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("iso") / "i.db"
    st = index_project(FX, d, "isolates")
    con = sqlite3.connect(d)
    e = {}
    for k, s, t, ln, a in con.execute("select kind, src, dst, line, attrs from edges where kind in ('SENDS_TO', 'RECEIVED_BY')"):
        e[(k, s, t)] = (ln, json.loads(a or "{}"))
    return e, st


def test_spawns(db):
    e = db[0]
    ep = f"endpoint:isolate:{W}#HashWorker._entry"                    # a static method of the spawning class
    assert e[("SENDS_TO", f"method:{W}#HashWorker.start", ep)][1]["how"] == "Isolate.spawn"
    assert ("RECEIVED_BY", ep, f"method:{W}#HashWorker._entry") in e
    assert e[("SENDS_TO", f"function:{W}#parseInBackground", f"endpoint:isolate:{W}#parseAll")][1]["how"] == "compute"
    assert e[("SENDS_TO", f"function:{W}#sumInBackground", f"endpoint:isolate:{W}#sumAll")][1]["how"] == "Isolate.run"
    assert ("RECEIVED_BY", f"endpoint:isolate:{W}#sumAll", f"function:{W}#sumAll") in e      # () => sumAll(xs)
    tool = "endpoint:isolate:bin/tool.dart#main"
    assert e[("SENDS_TO", "function:lib/launcher.dart#launchTool", tool)][1]["how"] == "Isolate.spawnUri"
    assert ("RECEIVED_BY", tool, "function:bin/tool.dart#main") in e


def test_replies(db):
    e = db[0]
    out = f"endpoint:isolate:{W}#HashWorker._entry:out"
    assert ("SENDS_TO", f"method:{W}#HashWorker._entry", out) in e
    assert e[("RECEIVED_BY", out, f"method:{W}#HashWorker._onMessage")][1]["how"] == "ReceivePort.listen"
    heavy = f"endpoint:isolate:{W}#heavyTask:out"
    assert e[("SENDS_TO", f"function:{W}#heavyTask", heavy)][1]["how"] == "Isolate.exit"
    assert e[("RECEIVED_BY", heavy, f"method:{W}#HashWorker.startRaw")][1]["how"] == "RawReceivePort.handler"
    assert ("SENDS_TO", "function:bin/tool.dart#main", "endpoint:isolate:bin/tool.dart#main:out") in e
    assert e[("RECEIVED_BY", "endpoint:isolate:bin/tool.dart#main:out", "function:lib/launcher.dart#launchTool")][1]["how"] \
        == "await for"


def test_stream_queue_and_tests(db):
    e = db[0]
    out = "endpoint:isolate:lib/launcher.dart#replyService:out"
    assert ("SENDS_TO", "function:lib/launcher.dart#replyService", out) in e
    assert e[("RECEIVED_BY", out, "function:lib/launcher.dart#pullReplies")][1]["how"] == "StreamQueue(port)"
    assert not any("_square" in k[1] + k[2] for k in e)                  # a test's own compute(..)


def test_stats(db):
    assert db[1]["dart_isolates"] == {"spawns": 6, "entries": 6, "entry_sends": 4, "port_listeners": 4,
                                      "test_isolates": 1}

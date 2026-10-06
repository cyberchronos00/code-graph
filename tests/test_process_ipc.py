"""Child processes (#38 part 3) over tests/process_ipc_fixture: application code starting an in-repo program
(`python -m`, `spawn('node', [script])`, `fork(script)`, Electron `utilityProcess.fork`) as `endpoint:process:<program>`,
Node fork / utilityProcess messages both ways, and worker_threads `parentPort` messaging."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "process_ipc_fixture"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("pipc") / "p.db"
    st = index_project(FX, d, "process-ipc")
    return sqlite3.connect(d), st


def _edges(db, kind, dst=None):
    q = "select src, dst, line, attrs from edges where kind=?" + (" and dst=?" if dst else "")
    return {(r[0], r[1], r[2]): json.loads(r[3] or "{}") for r in db[0].execute(q, (kind, dst) if dst else (kind,))}


def test_spawns(db):
    s = _edges(db, "SENDS_TO")
    assert s[("function:app.runner.run_worker", "endpoint:process:app/worker.py", 6)]["how"] == "-m"
    assert s[("function:src/main.js#runTool", "endpoint:process:src/tool.js", 18)]["role"] == "spawn"
    assert s[("function:src/main.js#startChild", "endpoint:process:src/child.js", 7)]["how"] == "node script"
    assert s[("function:src/utility-main.js#startUtility", "endpoint:process:src/utility-child.js", 5)]["how"] == \
        "utilityProcess.fork"
    r = {(k[0], k[1]) for k in _edges(db, "RECEIVED_BY")}
    assert ("endpoint:process:app/worker.py", "script:app.worker") in r            # the __main__ block
    assert ("endpoint:process:src/tool.js", "module:src/tool.js") in r
    assert ("endpoint:process:src/utility-child.js", "module:src/utility-child.js") in r


def test_test_spawns_stay_test_calls(db):
    t = _edges(db, "TEST_CALLS")
    assert ("function:tests.test_worker_cli.test_worker_runs", "script:app.worker", 6) in t
    assert not any(k[1].startswith("endpoint:process:") for k in t)     # running the CLI under test: no endpoint


def test_fork_messages(db):
    s, r = _edges(db, "SENDS_TO"), {(k[0], k[1]): v for k, v in _edges(db, "RECEIVED_BY").items()}
    assert s[("function:src/main.js#startChild", "endpoint:process:src/child.js", 9)]["how"] == "child.send"
    assert ("module:src/child.js", "endpoint:process:src/child.js:out", 2) in s          # process.send in the child
    assert r[("endpoint:process:src/child.js:out", "function:src/main.js#startChild")]["how"] == "child.on('message')"
    assert ("endpoint:process:src/child.js", "module:src/child.js") in r                 # process.on('message')
    assert ("function:src/utility-main.js#startUtility", "endpoint:process:src/utility-child.js", 7) in s
    assert ("module:src/utility-child.js", "endpoint:process:src/utility-child.js:out", 2) in s   # parentPort


def test_worker_threads(db):
    s, r = _edges(db, "SENDS_TO"), {(k[0], k[1]): v for k, v in _edges(db, "RECEIVED_BY").items()}
    w = "endpoint:worker:src/hash-worker.js"                             # new Worker(path.join(__dirname, ..))
    assert ("function:src/main.js#startWorker", w, 24) in s
    assert r[(w, "module:src/hash-worker.js")]["how"] == "parentPort.on('message')"
    assert ("module:src/hash-worker.js", w + ":out", 4) in s
    assert r[(w + ":out", "function:src/main.js#onHash")]["how"] == "worker.onmessage"   # w.on('message', onHash)
    p = "endpoint:worker:src/pool-worker.js"            # const u = pathToFileURL(path.join(..)); new Worker(u)
    assert ("function:src/main.js#startPool", p, 34) in s
    assert (p, "function:src/pool-worker.js#lintAll") in r                    # parentPort?.once('message', lintAll)


def test_stats(db):
    assert db[1]["process_endpoints"] == {"spawns": 3, "programs": 3}

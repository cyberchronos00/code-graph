"""Concurrent indexing of the same native project (#24): several processes with one cache directory get the exact
layer in every run and the graph a single run gives. The runner is checked with a stand-in indexer (always runs);
the rust-analyzer and scip-clang cases are skipped when those tools are not installed."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from native_util import ROOT, cmake_compdb, have_tree_sitter, rust_analyzer, scip_clang
from codegraph import coverage as C  # noqa: E402
from codegraph.plugins.native import runner  # noqa: E402

WORKERS = 4
WORKER = ("import sys, json; sys.path.insert(0, sys.argv[1]); from codegraph.indexer import index_project; "
          "r = index_project(sys.argv[2], sys.argv[3], 'p'); p = r['plugins'].get(sys.argv[4], {}); "
          "print(json.dumps({'mode': p.get('mode'), 'scip': p.get('scip')}, default=str))")


# ------------------------------------------------------------------ the runner, with a stand-in indexer
FAKE = '''import sys, time
out = sys.argv[sys.argv.index("--out") + 1]
open(sys.argv[1], "a").write("run\\n")         # one line per indexer run
time.sleep(0.6)
open(out, "wb").write(b"scip-bytes")
'''


def _fake(tmp_path):
    tool = tmp_path / "fake_indexer.py"
    tool.write_text(FAKE)
    return [sys.executable, str(tool), str(tmp_path / "runs.log")]


def _parallel(fn, n=WORKERS):
    res = [None] * n
    ts = [threading.Thread(target=lambda i=i: res.__setitem__(i, fn())) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return res


def test_runner_runs_the_indexer_once_for_concurrent_callers(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEGRAPH_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CODEGRAPH_NO_CACHE", raising=False)
    cmd = _fake(tmp_path)
    res = _parallel(lambda: runner.run_cached("fake", "k" * 20, cmd, tmp_path, "--out", 60))
    assert all(p is not None and p.read_bytes() == b"scip-bytes" for p, _ in res), res
    assert not [i for _, i in res if i.get("error")]
    assert sorted(i["cache"] for _, i in res) == ["hit"] * (WORKERS - 1) + ["miss"]
    assert (tmp_path / "runs.log").read_text().count("run") == 1
    assert not list((tmp_path / "cache" / "scip").glob("*.tmp.scip"))       # no temporary output left behind


def test_runner_without_file_locks_still_gives_every_caller_a_result(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEGRAPH_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CODEGRAPH_NO_CACHE", raising=False)
    monkeypatch.setattr(runner, "fcntl", None)      # platforms without flock: private temporary files only
    cmd = _fake(tmp_path)
    res = _parallel(lambda: runner.run_cached("fake", "j" * 20, cmd, tmp_path, "--out", 60))
    assert all(p is not None and p.read_bytes() == b"scip-bytes" for p, _ in res), res
    assert not [i for _, i in res if i.get("error")]


def test_single_run_and_cache_hit_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEGRAPH_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CODEGRAPH_NO_CACHE", raising=False)
    cmd = _fake(tmp_path)
    p1, i1 = runner.run_cached("fake", "m" * 20, cmd, tmp_path, "--out", 60)
    p2, i2 = runner.run_cached("fake", "m" * 20, cmd, tmp_path, "--out", 60)
    assert i1["cache"] == "miss" and i2["cache"] == "hit" and p1 == p2 and p1.name == f"fake-v{runner.fsutil.CACHE_VERSION}-{'m' * 20}.scip"
    assert "lock_wait_seconds" not in i2


def test_no_cache_runs_are_serialised_not_lost(tmp_path, monkeypatch):
    """CODEGRAPH_NO_CACHE=1: every caller runs the indexer itself (one at a time) and gets its own result."""
    monkeypatch.setenv("CODEGRAPH_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("CODEGRAPH_NO_CACHE", "1")
    cmd = _fake(tmp_path)
    res = _parallel(lambda: runner.run_cached("fake", "n" * 20, cmd, tmp_path, "--out", 60))
    assert all(p is not None and not i.get("error") and i["cache"] == "miss" for p, i in res), res
    assert (tmp_path / "runs.log").read_text().count("run") == WORKERS


def test_failed_indexer_run_is_explained_in_coverage():
    st = {"mode": "heuristic", "scip": {"error": "exit 101", "stderr_tail": ["error: failed to load workspace", ""]}}
    assert C._status("rust", st) == ("heuristic", "exact indexer run failed (exit 101: error: failed to load workspace); "
                                                  "heuristic layer used")
    assert C._status("rust", {"mode": "heuristic"}) == ("heuristic", None)


# ------------------------------------------------------------------ real indexers
def _graph(db: Path):
    c = sqlite3.connect(db)
    return ({r[0] for r in c.execute("SELECT id FROM nodes")},
            {tuple(r) for r in c.execute("SELECT src, dst, kind, confidence FROM edges")})


def _concurrent(tmp_path, src: Path, plugin: str, env: dict):
    root = tmp_path / src.name
    shutil.copytree(src, root, ignore=shutil.ignore_patterns("target", "build"))
    base = {k: v for k, v in dict(os.environ, **env).items() if k != "CODEGRAPH_NO_CACHE"}   # (tests/sample.py sets it)
    single = subprocess.run([sys.executable, "-c", WORKER, str(ROOT), str(root), str(tmp_path / "single.db"), plugin],
                            env=dict(base, CODEGRAPH_CACHE=str(tmp_path / "cache-single")), capture_output=True, text=True)
    assert single.returncode == 0, single.stderr[-2000:]
    one = json.loads(single.stdout.strip().splitlines()[-1])
    assert one["mode"] == "scip", one
    shared = dict(base, CODEGRAPH_CACHE=str(tmp_path / "cache-shared"))
    ps = [subprocess.Popen([sys.executable, "-c", WORKER, str(ROOT), str(root), str(tmp_path / f"w{i}.db"), plugin],
                           env=shared, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(WORKERS)]
    outs = [p.communicate() for p in ps]
    assert all(p.returncode == 0 for p in ps), [e[-1500:] for _, e in outs]
    runs = [json.loads(o.strip().splitlines()[-1]) for o, _ in outs]
    assert [r["mode"] for r in runs] == ["scip"] * WORKERS, runs
    assert sorted(r["scip"]["cache"] for r in runs) == ["hit"] * (WORKERS - 1) + ["miss"], runs
    want = _graph(tmp_path / "single.db")
    for i in range(WORKERS):
        assert _graph(tmp_path / f"w{i}.db") == want


@pytest.mark.skipif(not have_tree_sitter() or not rust_analyzer(), reason="rust-analyzer / tree-sitter not installed")
def test_rust_concurrent_runs_are_exact(tmp_path):
    _concurrent(tmp_path, ROOT / "tests" / "rust_routes_fixture", "rust", {"CODEGRAPH_RUST_SCIP": "1"})


@pytest.mark.skipif(not have_tree_sitter() or not scip_clang() or not shutil.which("cmake"),
                    reason="scip-clang / cmake / tree-sitter not installed")
def test_c_concurrent_runs_are_exact(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(ROOT / "examples" / "c-ringbuf", src / "c-ringbuf", ignore=shutil.ignore_patterns("build"))
    cdb = cmake_compdb(src / "c-ringbuf")
    if cdb is None:
        pytest.skip("cmake configure failed (no C compiler?)")
    _concurrent(tmp_path, src / "c-ringbuf", "c_cpp", {"CODEGRAPH_COMPDB": str(cdb)})

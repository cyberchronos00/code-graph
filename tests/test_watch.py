"""Tests for `cg watch` and the MCP staleness notice (issue #145, part 2)."""
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from codegraph import hooks
from codegraph.indexer import index_project


def _git(repo, *args):
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def _init(repo: Path):
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def world():\n    return 2\n", encoding="utf-8")
    (repo / "c.py").write_text("VALUE = 3\n", encoding="utf-8")
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git(repo, "add", "a.py", "b.py", "c.py", ".gitignore")
    _git(repo, "commit", "-m", "init")


def _run_watch(repo, db, monkeypatch, **kw):
    from codegraph import watch as W
    calls = []

    def fake(*a, **k):
        calls.append(time.monotonic())
        return 0

    monkeypatch.setattr(hooks, "refresh", fake)
    evt = threading.Event()
    box = {}

    def target():
        box["rc"] = W.watch(str(repo), str(db), backend=kw.get("backend", "poll"),
                            interval=kw.get("interval", 0.1), debounce=kw.get("debounce", 0.3),
                            stop=evt, out=lambda *_: None)

    th = threading.Thread(target=target)
    th.start()
    return calls, evt, th, box


def _join(evt, th):
    evt.set()
    th.join(timeout=5)
    assert not th.is_alive()


def _wait_until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_db_inside_root_second_refresh_is_up_to_date(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    db = repo / "graph.db"
    lines = []
    assert hooks.refresh(str(repo), str(db), out=lines.append) == 0
    assert any(line.startswith("refreshed:") for line in lines)
    lines.clear()
    assert hooks.refresh(str(repo), str(db), out=lines.append) == 0
    assert lines == ["up to date"]


def test_poll_debounce_coalesces_a_burst(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    calls, evt, th, _box = _run_watch(repo, db, monkeypatch)
    try:
        assert _wait_until(lambda: len(calls) >= 1)
        base = len(calls)
        for i in range(3):
            (repo / "a.py").write_text(f"def hello():\n    return {i}\n", encoding="utf-8")
            time.sleep(0.05)
        assert _wait_until(lambda: len(calls) >= base + 1, timeout=3)
        time.sleep(0.6)
        assert len(calls) == base + 1
    finally:
        _join(evt, th)


def test_ignored_and_db_sidecars_do_not_refresh(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init(repo)
    db = repo / "graph.db"
    db.write_text("", encoding="utf-8")
    calls, evt, th, _box = _run_watch(repo, db, monkeypatch)
    try:
        assert _wait_until(lambda: len(calls) >= 1)
        base = len(calls)
        nm = repo / "node_modules"
        nm.mkdir()
        (nm / "x.js").write_text("var x = 1\n", encoding="utf-8")
        db.write_text("x", encoding="utf-8")
        (repo / "graph.db.refresh.log").write_text("log\n", encoding="utf-8")
        time.sleep(0.8)
        assert len(calls) == base
    finally:
        _join(evt, th)


def test_index_lock_pauses_then_one_refresh(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    calls, evt, th, _box = _run_watch(repo, db, monkeypatch)
    try:
        assert _wait_until(lambda: len(calls) >= 1)
        base = len(calls)
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path", "index.lock"],
                              check=True, capture_output=True, text=True)
        lock = Path(proc.stdout.strip())
        if not lock.is_absolute():
            lock = repo / lock
        lock.write_text("locked", encoding="utf-8")
        (repo / "a.py").write_text("def hello():\n    return 9\n", encoding="utf-8")
        time.sleep(0.8)
        assert len(calls) == base
        lock.unlink()
        assert _wait_until(lambda: len(calls) == base + 1, timeout=3)
        time.sleep(0.5)
        assert len(calls) == base + 1
    finally:
        _join(evt, th)


def test_watchfiles_burst_then_stop(tmp_path, monkeypatch):
    pytest.importorskip("watchfiles")
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    calls, evt, th, _box = _run_watch(repo, db, monkeypatch, backend="watchfiles", debounce=0.4)
    try:
        assert _wait_until(lambda: len(calls) >= 1, timeout=4)
        base = len(calls)
        for i in range(3):
            (repo / "a.py").write_text(f"def hello():\n    return {i}\n", encoding="utf-8")
            time.sleep(0.05)
        assert _wait_until(lambda: len(calls) >= base + 1, timeout=4)
        time.sleep(0.7)
        assert len(calls) == base + 1
    finally:
        _join(evt, th)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT")
def test_ctrl_c_stops_cleanly(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "codegraph.cli", "watch", str(repo), "--db", str(db), "--poll", "--interval", "0.2"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
    lines = []

    def reader():
        for line in proc.stdout:
            lines.append(line)

    th = threading.Thread(target=reader)
    th.start()
    try:
        assert _wait_until(lambda: any("watching" in line for line in lines), timeout=8)
        proc.send_signal(signal.SIGINT)
        rc = proc.wait(timeout=8)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        th.join(timeout=2)
    text = "".join(lines)
    assert rc == 0, text
    assert "watch stopped" in text
    assert list(db.parent.glob("*.refresh.tmp")) == []
    assert not Path(str(db) + ".refresh.tmp").exists()


def test_poll_non_git_returns_2(tmp_path):
    from codegraph import watch as W
    root = tmp_path / "plain"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    lines = []
    assert W.watch(str(root), str(tmp_path / "g.db"), backend="poll", out=lines.append) == 2
    assert any("not a git repository" in line for line in lines)


def test_mcp_staleness_note_cache_and_index(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    assert hooks.refresh(str(repo), str(db), quiet=True) == 0
    from codegraph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE["db"] = str(db)
        M.STATE["root"] = str(repo)
        M._STALE["key"] = None
        assert "index note:" not in M.stats()
        src = repo / "a.py"
        future = time.time() + 3600
        os.utime(src, (future, future))
        M._STALE["key"] = None
        assert "index note:" in M.stats()
        assert M.stats.structured()["stale"] is True
        calls = {"n": 0}
        real = hooks.staleness

        def wrapped(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(hooks, "staleness", wrapped)
        M._STALE["key"] = None
        M.stats()
        M.stats()
        assert calls["n"] == 1
        os.utime(src, (1, 1))
        assert "index note:" not in M.index()
        assert "index note:" not in M.stats()
    finally:
        M.STATE.clear()
        M.STATE.update(old)
        M._STALE["key"] = None


def test_staleness_mtime_fallback_without_state_file(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "plain.db"
    index_project(str(repo), str(db), "repo")
    assert not Path(str(db) + ".refresh.state").exists()
    assert hooks.staleness(repo, db) is False
    future = time.time() + 3600
    os.utime(repo / "b.py", (future, future))
    assert hooks.staleness(repo, db) is True


def test_edit_during_refresh_gets_one_follow_up(tmp_path, monkeypatch):
    from codegraph import watch as W
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    calls = []

    def fake(*a, **k):
        calls.append(time.monotonic())
        if len(calls) == 2:     # the refresh for the first edit: another save lands while it runs
            (repo / "b.py").write_text("def world():\n    return 7\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(hooks, "refresh", fake)
    evt = threading.Event()
    th = threading.Thread(target=lambda: W.watch(str(repo), str(db), backend="poll", interval=0.1,
                                                 debounce=0.2, stop=evt, out=lambda *_: None))
    th.start()
    try:
        assert _wait_until(lambda: len(calls) >= 1)
        (repo / "a.py").write_text("def hello():\n    return 5\n", encoding="utf-8")
        assert _wait_until(lambda: len(calls) >= 3, timeout=4)
        time.sleep(0.6)
        assert len(calls) == 3
    finally:
        _join(evt, th)


def test_stale_note_never_breaks_a_reply(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    assert hooks.refresh(str(repo), str(db), quiet=True) == 0
    from codegraph import mcp_server as M
    old = dict(M.STATE)

    def boom(*a, **k):
        raise RuntimeError("boom")

    try:
        M.STATE["db"] = str(db)
        M.STATE["root"] = str(repo)
        M._STALE["key"] = None
        monkeypatch.setattr(hooks, "staleness", boom)
        txt = M.stats()
        assert "index note:" not in txt and "boom" not in txt
        assert "stale" not in M.stats.structured()
        monkeypatch.setenv("CODEGRAPH_NO_STALE_CHECK", "1")
        monkeypatch.setattr(hooks, "staleness", lambda *a, **k: True)
        M._STALE["key"] = None
        assert "index note:" not in M.stats()
    finally:
        M.STATE.clear()
        M.STATE.update(old)
        M._STALE["key"] = None


def test_staleness_state_without_snapshot_uses_mtimes(tmp_path):
    import json
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    assert hooks.refresh(str(repo), str(db), quiet=True) == 0
    state = Path(str(db) + ".refresh.state")
    saved = json.loads(state.read_text(encoding="utf-8"))
    saved["files"] = None          # refresh stores None when an app root lies outside the checkout
    state.write_text(json.dumps(saved), encoding="utf-8")
    old = time.time() - 3600
    for name in ("a.py", "b.py", "c.py", ".gitignore"):
        os.utime(repo / name, (old, old))
    assert hooks.staleness(repo, db) is False
    future = time.time() + 3600
    os.utime(repo / "a.py", (future, future))
    assert hooks.staleness(repo, db) is True

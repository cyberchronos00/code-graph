"""Tests for `cg hooks` and `cg refresh` (issue #145, part 1)."""
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from codegraph import hooks
from codegraph.core.store import GraphStore

BEGIN = hooks.BEGIN


def _git(repo, *args, env=None, timeout=20):
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


def _init(repo: Path):
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def world():\n    return 2\n", encoding="utf-8")
    (repo / "c.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(repo, "add", "a.py", "b.py", "c.py")
    _git(repo, "commit", "-m", "init")


def _hooks(repo: Path) -> Path:
    proc = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=repo,
                          check=True, capture_output=True, text=True)
    path = Path(proc.stdout.strip())
    return path if path.is_absolute() else (repo / path).resolve()


def _wait_for(path: Path, timeout: float = 20.0) -> str:
    """The hook backgrounds the refresh, so its side effect lands a moment after the hook returns."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if text:
                return text
        time.sleep(0.05)
    raise AssertionError(f"{path} was not written within {timeout}s")


def _nodes(db: Path) -> int:
    store = GraphStore(db)
    try:
        return store.db.execute("select count(*) c from nodes").fetchone()["c"]
    finally:
        store.db.close()


def test_install_creates_executable_hooks_and_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    rc = hooks.run("install", root=str(repo), db=str(db), assume_yes=True, out=lambda *a: None)
    assert rc == 0
    hdir = _hooks(repo)
    first = {}
    for name in hooks.HOOKS:
        path = hdir / name
        data = path.read_bytes()
        first[name] = data
        assert data.startswith(b"#!/bin/sh\n")
        assert data.count(BEGIN.encode()) == 1
        assert stat.S_IMODE(path.stat().st_mode) & 0o111
    rc = hooks.run("install", root=str(repo), db=str(db), assume_yes=True, out=lambda *a: None)
    assert rc == 0
    for name in hooks.HOOKS:
        assert (hdir / name).read_bytes() == first[name]
        assert (hdir / name).read_bytes().count(BEGIN.encode()) == 1


def test_existing_hook_is_chained_and_uninstall_restores_bytes(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    hdir = _hooks(repo)
    orig = b"#!/bin/sh\necho USER >> \"$CG_MARK\"\nexit 0\n"
    hook = hdir / "post-commit"
    hook.write_bytes(orig)
    os.chmod(hook, 0o755)
    db = tmp_path / "graph.db"
    rc = hooks.run("install", root=str(repo), db=str(db), assume_yes=True, out=lambda *a: None)
    assert rc == 0
    text = hook.read_bytes()
    assert text.startswith(b"#!/bin/sh\n")
    assert BEGIN.encode() in text
    rest = text.split(hooks.END.encode() + b"\n", 1)[1]
    assert rest == b"echo USER >> \"$CG_MARK\"\nexit 0\n"
    marker = tmp_path / "marker"
    env = os.environ.copy()
    env["CG_MARK"] = str(marker)
    env["PATH"] = "/usr/bin:/bin"
    proc = subprocess.run(["sh", str(hook)], capture_output=True, text=True, env=env, timeout=20)
    assert proc.returncode == 0
    assert marker.read_text(encoding="utf-8").strip() == "USER"
    rc = hooks.run("uninstall", root=str(repo), assume_yes=True, out=lambda *a: None)
    assert rc == 0
    assert hook.read_bytes() == orig


def test_non_shell_hook_is_left_unchanged(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    hdir = _hooks(repo)
    py = b"#!/usr/bin/env python3\nprint(1)\n"
    bare = b"echo hi\n"
    (hdir / "post-commit").write_bytes(py)
    (hdir / "post-merge").write_bytes(bare)
    lines = []
    rc = hooks.run("install", root=str(repo), db=str(tmp_path / "graph.db"), assume_yes=True,
                   out=lines.append)
    assert rc == 0
    assert (hdir / "post-commit").read_bytes() == py
    assert (hdir / "post-merge").read_bytes() == bare
    assert any("skipped (not a shell hook)" in line for line in lines)
    status = []
    hooks.run("status", root=str(repo), out=status.append)
    joined = "\n".join(status)
    assert "post-commit: skipped (not a shell hook)" in joined
    assert "post-merge: skipped (not a shell hook)" in joined
    assert "post-checkout: installed" in joined
    assert f"db={tmp_path / 'graph.db'}" in joined
    assert f"root={repo.resolve()}" in joined


def test_hooks_path_is_honoured(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    custom = repo / ".githooks"
    custom.mkdir()
    _git(repo, "config", "core.hooksPath", ".githooks")
    rc = hooks.run("install", root=str(repo), db=str(tmp_path / "graph.db"), assume_yes=True,
                   out=lambda *a: None)
    assert rc == 0
    for name in hooks.HOOKS:
        assert BEGIN.encode() in (custom / name).read_bytes()
        assert not (repo / ".git" / "hooks" / name).exists()


def test_hook_failure_does_not_break_git(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    rc = hooks.run("install", root=str(repo), db="/no/such/dir/graph.db", assume_yes=True,
                   interpreter="/no/such/cg-python", out=lambda *a: None)
    assert rc == 0
    env = os.environ.copy()
    env["PATH"] = "/usr/bin:/bin"
    t0 = time.monotonic()
    commit = _git(repo, "commit", "--allow-empty", "-m", "empty", env=env)
    assert commit.returncode == 0
    assert time.monotonic() - t0 < 20
    t0 = time.monotonic()
    branch = _git(repo, "checkout", "-b", "feature", env=env)
    assert branch.returncode == 0
    assert time.monotonic() - t0 < 20
    (repo / "a.py").write_text("def hello():\n    return 9\n", encoding="utf-8")
    _git(repo, "add", "a.py", env=env)
    made = _git(repo, "commit", "-m", "edit", env=env)
    assert made.returncode == 0
    _git(repo, "checkout", "main", env=env)
    t0 = time.monotonic()
    merged = _git(repo, "merge", "feature", "-m", "merge", env=env)
    assert merged.returncode == 0, merged.stderr
    assert time.monotonic() - t0 < 20


def test_codegraph_no_hooks_and_checkout_skip(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "ran"
    cg = bindir / "cg"
    cg.write_text(f"#!/bin/sh\necho RAN >> {marker}\n", encoding="utf-8")
    os.chmod(cg, 0o755)
    hooks.run("install", root=str(repo), db=str(tmp_path / "graph.db"), assume_yes=True,
              interpreter=str(tmp_path / "no-such-python"), out=lambda *a: None)
    hook = _hooks(repo) / "post-commit"
    env = os.environ.copy()
    env["PATH"] = str(bindir) + os.pathsep + "/usr/bin:/bin"
    env.pop("CODEGRAPH_NO_HOOKS", None)
    subprocess.run(["sh", str(hook)], check=True, env=env, timeout=20)
    assert "RAN" in _wait_for(marker)
    marker.unlink()
    env["CODEGRAPH_NO_HOOKS"] = "1"
    subprocess.run(["sh", str(hook)], check=True, env=env, timeout=20)
    time.sleep(0.3)
    assert not marker.exists()

    checkout = _hooks(repo) / "post-checkout"
    env.pop("CODEGRAPH_NO_HOOKS")
    subprocess.run(["sh", str(checkout), "abc", "abc"], check=True, env=env, timeout=20)
    time.sleep(0.3)
    assert not marker.exists()
    subprocess.run(["sh", str(checkout), "abc", "def"], check=True, env=env, timeout=20)
    assert _wait_for(marker).strip() == "RAN"


def test_refresh_skip_reindex_refuse_and_pending(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    cmd = [sys.executable, "-m", "codegraph.cli", "refresh", str(repo), "--db", str(db)]
    first = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert first.returncode == 0, first.stderr + first.stdout
    assert db.is_file()
    assert _nodes(db) > 0
    assert "refreshed:" in first.stdout

    second = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr + second.stdout
    assert "up to date" in second.stdout
    before = db.stat()

    src = repo / "a.py"
    src.write_text("def hello():\n    return 4\n", encoding="utf-8")
    os.utime(src, None)
    third = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert third.returncode == 0, third.stderr + third.stdout
    assert "refreshed:" in third.stdout
    after = db.stat()
    assert after.st_ino != before.st_ino or after.st_mtime_ns != before.st_mtime_ns

    kept = db.read_bytes()
    before = _nodes(db)
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).unlink()
    refused = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert refused.returncode != 0
    assert "0 nodes" in refused.stdout
    assert db.read_bytes() == kept
    assert _nodes(db) == before

    fresh = tmp_path / "other.db"
    lock = Path(str(fresh) + ".refresh.lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        t0 = time.monotonic()
        pending = subprocess.run(
            [sys.executable, "-m", "codegraph.cli", "refresh", str(repo), "--db", str(fresh)],
            capture_output=True, text=True, timeout=20)
        assert time.monotonic() - t0 < 20
        assert pending.returncode == 0
        assert "marked pending" in pending.stdout
        assert Path(str(fresh) + ".refresh.pending").is_file()
        assert not fresh.exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_uninstall_deletes_files_cg_created(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    hooks.run("install", root=str(repo), db=str(tmp_path / "graph.db"), assume_yes=True, out=lambda *a: None)
    rc = hooks.run("uninstall", root=str(repo), assume_yes=True, out=lambda *a: None)
    assert rc == 0
    for name in hooks.HOOKS:
        assert not (_hooks(repo) / name).exists()
    status = []
    hooks.run("status", root=str(repo), out=status.append)
    assert all(line.endswith("not installed") for line in status)


def test_worktree_install_uses_common_hooks_dir(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    wt = tmp_path / "wt"
    assert _git(repo, "worktree", "add", "-q", str(wt), "-b", "wt").returncode == 0
    rc = hooks.run("install", root=str(wt), db=str(tmp_path / "graph.db"), assume_yes=True, out=lambda *a: None)
    assert rc == 0
    text = (repo / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")
    assert f"# cg-root: {wt.resolve()}" in text


def test_install_refuses_line_break_in_paths(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    lines = []
    rc = hooks.run("install", root=str(repo), db=str(tmp_path / "x\nrm -rf" / "graph.db"), assume_yes=True,
                   out=lines.append)
    assert rc == 2
    assert not (_hooks(repo) / "post-commit").exists()


def test_refresh_sees_rename_that_keeps_mtime(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "graph.db"
    assert hooks.refresh(str(repo), str(db), quiet=True) == 0
    lines = []
    assert hooks.refresh(str(repo), str(db), out=lines.append) == 0
    assert lines == ["up to date"]
    os.rename(repo / "b.py", repo / "renamed.py")          # rename keeps the file's mtime and size
    lines = []
    assert hooks.refresh(str(repo), str(db), out=lines.append) == 0
    assert lines and lines[0].startswith("refreshed:"), lines
    store = GraphStore(db)
    try:
        files = {r["file"] for r in store.db.execute("select distinct file from nodes where file is not null")}
    finally:
        store.db.close()
    assert any("renamed.py" in f for f in files)
    assert not any(f.endswith("b.py") for f in files)


def test_refresh_ignores_git_env_from_the_hook(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init(repo)
    other = tmp_path / "other"
    _init(other)
    (other / "d.py").write_text("D = 4\n", encoding="utf-8")
    _git(other, "add", "d.py")
    _git(other, "commit", "-m", "d")               # a different file list: a leaked GIT_DIR would show
    db = tmp_path / "graph.db"
    assert hooks.refresh(str(repo), str(db), quiet=True) == 0
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", ".git/index")
    lines = []
    assert hooks.refresh(str(repo), str(db), out=lines.append) == 0
    assert lines == ["up to date"]
    assert "GIT_DIR" not in os.environ


def test_refresh_refuses_combined_graph(tmp_path):
    repo = tmp_path / "repo"
    _init(repo)
    db = tmp_path / "combined.db"
    store = GraphStore.create(db)
    store.set_meta(project="api+web", repos=["api", "web"], stats={})
    store.db.close()
    kept = db.read_bytes()
    lines = []
    assert hooks.refresh(str(repo), str(db), out=lines.append) == 1
    assert "combined graph" in lines[0]
    assert db.read_bytes() == kept


def test_pending_request_is_not_lost(tmp_path, monkeypatch):
    """A request that arrives while a pass runs (it touches the pending flag) gets one more pass."""
    db = tmp_path / "graph.db"
    pending = Path(str(db) + ".refresh.pending")
    calls = []

    def fake_once(root, db_, name, quiet, out):
        calls.append(1)
        if len(calls) == 1:
            pending.touch()            # what a concurrent `cg refresh` does before it fails the lock
        return 0

    monkeypatch.setattr(hooks, "_refresh_once", fake_once)
    assert hooks.refresh(str(tmp_path), str(db), quiet=True) == 0
    assert len(calls) == 2
    assert not pending.exists()


def test_lock_is_released_after_an_error(tmp_path, monkeypatch):
    db = tmp_path / "graph.db"

    def boom(*a):
        raise RuntimeError("boom")

    monkeypatch.setattr(hooks, "_refresh_once", boom)
    try:
        hooks.refresh(str(tmp_path), str(db), quiet=True)
    except RuntimeError:
        pass
    with hooks._refresh_lock(Path(str(db) + ".refresh.lock")) as held:
        assert held


def test_recorded_interpreter_wins_over_cg_on_path(tmp_path):
    """An older `cg` earlier on PATH (a global tool install) may lack `refresh`: the installing Python runs."""
    repo = tmp_path / "repo"
    _init(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "ran"
    (bindir / "cg").write_text(f"#!/bin/sh\necho PATH-CG >> {marker}\n", encoding="utf-8")
    os.chmod(bindir / "cg", 0o755)
    py = tmp_path / "py"
    py.write_text(f"#!/bin/sh\necho RECORDED \"$@\" >> {marker}\n", encoding="utf-8")
    os.chmod(py, 0o755)
    hooks.run("install", root=str(repo), db=str(tmp_path / "graph.db"), assume_yes=True,
              interpreter=str(py), out=lambda *a: None)
    env = os.environ.copy()
    env["PATH"] = str(bindir) + os.pathsep + "/usr/bin:/bin"
    env.pop("CODEGRAPH_NO_HOOKS", None)
    subprocess.run(["sh", str(_hooks(repo) / "post-commit")], check=True, env=env, timeout=20)
    text = _wait_for(marker)
    assert text.startswith("RECORDED -m codegraph.cli refresh ")
    assert "PATH-CG" not in text

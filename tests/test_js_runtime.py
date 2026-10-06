"""JavaScript runtime lookup for the TypeScript extractor: node, then bun, or CODEGRAPH_NODE."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from sample import WEB, EXTRACTOR_DEPS  # noqa: E402
from codegraph.core import extractors  # noqa: E402
from codegraph import doctor  # noqa: E402


def _which(found: dict):
    def which(name):
        if name in found:
            return found[name]
        return None
    return which


def test_path_precedence(monkeypatch):
    monkeypatch.delenv("CODEGRAPH_NODE", raising=False)
    monkeypatch.setattr(extractors.shutil, "which", _which({"node": "/usr/bin/node", "bun": "/usr/bin/bun"}))
    assert extractors.js_runtime()["kind"] == "node"
    monkeypatch.setattr(extractors.shutil, "which", _which({"bun": "/usr/bin/bun"}))
    rt = extractors.js_runtime()
    assert rt["kind"] == "bun" and rt["source"] == "PATH"
    monkeypatch.setattr(extractors.shutil, "which", _which({}))
    assert extractors.js_runtime() is None
    problem = extractors.js_runtime_problem()
    assert "node not installed" in problem and "CODEGRAPH_NODE" in problem


def test_codegraph_node_override(monkeypatch):
    monkeypatch.setenv("CODEGRAPH_NODE", "/x/bun")
    monkeypatch.setattr(extractors.shutil, "which", _which({"/x/bun": "/x/bun", "node": "/usr/bin/node"}))
    rt = extractors.js_runtime()
    assert rt["kind"] == "bun" and rt["source"] == "CODEGRAPH_NODE" and rt["path"] == "/x/bun"
    monkeypatch.setenv("CODEGRAPH_NODE", "/nope")
    monkeypatch.setattr(extractors.shutil, "which", _which({"node": "/usr/bin/node"}))
    assert extractors.js_runtime() is None
    assert extractors.js_runtime_problem().startswith("CODEGRAPH_NODE=/nope")


def test_install_command_npm_or_bun(monkeypatch):
    monkeypatch.delenv("CODEGRAPH_NODE", raising=False)
    monkeypatch.setattr(extractors.shutil, "which", _which({"npm": "/usr/bin/npm", "bun": "/usr/bin/bun", "node": "/usr/bin/node"}))
    cmd = extractors.install_command("typescript")
    assert cmd[0] == "/usr/bin/npm" and "ci" in cmd
    monkeypatch.setattr(extractors.shutil, "which", _which({"bun": "/opt/bun", "node": None}))
    assert extractors.install_command("typescript") == ["/opt/bun", "install", "--frozen-lockfile", "--no-progress"]


def test_doctor_bun_only(monkeypatch):
    monkeypatch.delenv("CODEGRAPH_NODE", raising=False)

    def which(name):
        if name == "bun":
            return "/opt/bun"
        return None

    monkeypatch.setattr(extractors.shutil, "which", which)
    monkeypatch.setattr(doctor.shutil, "which", which)
    tools = {k: {"path": v} for k, v in {
        "node": None, "npm": None, "bun": "/opt/bun", "php": None, "composer": None, "dart": None,
        "rust-analyzer": None, "cargo": None, "scip-clang": None, "scip-java": None, "java": None,
        "swift": None, "git": None,
    }.items()}
    # keys _languages reads via has(); fill any extra from a real _tools() shape by calling the function's keys
    for key in doctor._tools().keys():
        tools.setdefault(key, {"path": "/opt/bun" if key == "bun" else None})
    langs = {e["language"]: e for e in doctor._languages(tools, None, {})}
    ts = langs["typescript"]
    assert ts["mode"] == "exact" and ts["runtime"]["kind"] == "bun"
    assert "bun" in doctor._tools()


def _real_bun() -> str | None:
    """The first Bun binary that runs ($BUN, bun on PATH, /tmp/bun, ~/.bun): a broken wrapper on PATH is skipped."""
    for cand in (os.environ.get("BUN"), shutil.which("bun"), "/tmp/bun/bin/bun", str(Path.home() / ".bun/bin/bun")):
        if not cand or not Path(cand).exists():
            continue
        bun = os.path.realpath(cand)
        try:
            r = subprocess.run([bun, "--version"], env={"PATH": "/nonexistent"}, capture_output=True, text=True)
        except OSError:
            continue
        if r.returncode == 0:
            return bun
    return None


_BUN = _real_bun()
needs_bun = pytest.mark.skipif(
    not EXTRACTOR_DEPS.exists() or not _BUN, reason="extractor deps and a working bun binary")


def _cg(args, path, extra_env=None, cwd=ROOT):
    env = dict(os.environ, CODEGRAPH_NO_CACHE="1", PATH=path)
    env.pop("CODEGRAPH_NODE", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, "-m", "codegraph.cli", *map(str, args)], cwd=cwd, env=env,
                          capture_output=True, text=True)


def _graph(db: Path):
    con = sqlite3.connect(db)
    nodes = {r[0] for r in con.execute("select id from nodes")}
    edges = {tuple(r) for r in con.execute("select src, dst, kind from edges")}
    con.close()
    return nodes, edges


@needs_bun
def test_bun_only_matches_node(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    os.symlink(_BUN, bindir / "bun")
    git = shutil.which("git")
    assert git
    os.symlink(git, bindir / "git")
    bun_db = tmp_path / "bun.db"
    node_db = tmp_path / "node.db"
    rb = _cg(["index", WEB, "--name", "bookstore-web", "--db", bun_db], str(bindir))
    assert rb.returncode == 0, rb.stderr[-800:]
    rn = _cg(["index", WEB, "--name", "bookstore-web", "--db", node_db], os.environ["PATH"])
    assert rn.returncode == 0, rn.stderr[-800:]
    bun_st = json.loads(rb.stdout)
    assert bun_st["plugins"]["typescript"]["runtime"]["kind"] == "bun"
    bn, be = _graph(bun_db)
    nn, ne = _graph(node_db)
    assert bn == nn and be == ne
    (tmp_path / "counts.txt").write_text(f"nodes {len(bn)} edges {len(be)}\n")


@needs_bun
def test_codegraph_node_selects_bun_and_missing_skips(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not on PATH")
    db = tmp_path / "over.db"
    r = _cg(["index", WEB, "--name", "bookstore-web", "--db", db], os.environ["PATH"],
            {"CODEGRAPH_NODE": _BUN})
    assert r.returncode == 0, r.stderr[-800:]
    assert json.loads(r.stdout)["plugins"]["typescript"]["runtime"]["kind"] == "bun"
    empty = tmp_path / "bin"
    empty.mkdir()
    r2 = _cg(["index", WEB, "--name", "bookstore-web", "--db", tmp_path / "miss.db"], str(empty),
             {"CODEGRAPH_NODE": "/nonexistent"})
    assert r2.returncode == 0, r2.stderr[-800:]
    ts = next(e for e in json.loads(r2.stdout)["coverage"]["languages"] if e["language"] == "typescript")
    assert ts["status"] == "skipped" and "CODEGRAPH_NODE" in ts["reason"]


def test_codegraph_node_name_path_and_symlink(tmp_path, monkeypatch):
    real_bun = tmp_path / "bun-1.4"
    real_bun.write_text("#!/bin/sh\n")
    real_bun.chmod(0o755)
    (tmp_path / "node").symlink_to(real_bun)          # a Bun image's `node` -> bun
    (tmp_path / "bun").symlink_to(real_bun)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("CODEGRAPH_NODE", raising=False)
    rt = extractors.js_runtime()
    assert rt["path"] == str(tmp_path / "node") and rt["kind"] == "bun"   # no --max-old-space-size for it
    monkeypatch.setenv("CODEGRAPH_NODE", "bun")                           # a name on PATH
    rt = extractors.js_runtime()
    assert rt["path"] == str(tmp_path / "bun") and rt["kind"] == "bun" and rt["source"] == "CODEGRAPH_NODE"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEGRAPH_NODE", "~/bun-1.4")                     # a path, ~ expanded
    assert extractors.js_runtime()["path"] == str(real_bun)
    monkeypatch.setenv("CODEGRAPH_NODE", str(tmp_path / "missing-node"))
    assert extractors.js_runtime() is None
    problem = extractors.js_runtime_problem()
    assert problem.startswith(f"CODEGRAPH_NODE={tmp_path / 'missing-node'} is not an executable")
    assert "node not installed" not in problem

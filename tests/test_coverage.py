"""Language coverage and graceful degradation: a missing indexer (simulated by an empty PATH) skips that language with
an install hint instead of failing the index; unsupported-language files are counted; `cg coverage`, the MCP
`coverage` tool and the notes on empty MCP replies tell an agent to fall back to normal search for uncovered parts."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from sample import API, WEB, EXTRACTOR_DEPS, needs_php  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph import coverage as C  # noqa: E402


def cg(*args, path=None, cwd=ROOT):
    env = dict(os.environ, CG_NO_CACHE="1")
    if path is not None:
        env["PATH"] = path
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", *map(str, args)], cwd=cwd, env=env,
                          capture_output=True, text=True)


def lang(cov, name):
    return next(e for e in cov["languages"] if e["language"] == name)


def test_missing_php_is_skipped_not_fatal(tmp_path):
    empty = tmp_path / "bin"
    empty.mkdir()
    r = cg("index", API, "--name", "bookstore-api", "--db", tmp_path / "api.db", path=str(empty))
    assert r.returncode == 0, r.stderr[-800:]
    st = json.loads(r.stdout)
    assert st["plugins"]["php"]["status"] == "skipped" and "php not installed" in st["plugins"]["php"]["reason"]
    php = lang(st["coverage"], "php")
    assert php["status"] == "skipped" and php["files"] == 30 and "cg setup php" in php["hint"]
    assert "not fully covered: php 30 skipped" in r.stderr and "not proof of absence" in r.stderr
    out = cg("coverage", "--db", tmp_path / "api.db").stdout
    assert "  php 30 skipped: php not installed" in out and "; fix: install PHP 8.2+" in out          # the summary (#75)
    out = cg("coverage", "--db", tmp_path / "api.db", "--details").stdout
    assert "php: 30 files (.php 30) skipped: php not installed" in out and "fix: install PHP 8.2+" in out


@pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
def test_missing_node_skips_typescript(tmp_path):
    empty = tmp_path / "bin"
    empty.mkdir()
    r = cg("index", WEB, "--name", "bookstore-web", "--db", tmp_path / "web.db", path=str(empty))
    assert r.returncode == 0, r.stderr[-800:]
    ts = lang(json.loads(r.stdout)["coverage"], "typescript")
    assert ts["status"] == "skipped" and "node not installed" in ts["reason"] and "cg setup typescript" in ts["hint"]


@needs_php
def test_unsupported_languages_are_counted(tmp_path):
    proj = tmp_path / "mixed"
    shutil.copytree(API, proj)
    for f in ("svc/main.go", "svc/util.go", "android/App.kt", "ios/App.swift", "tools/x.rb", "win/Y.cs", "legacy/Z.java"):
        (proj / f).parent.mkdir(parents=True, exist_ok=True)
        (proj / f).write_text("// not analysed\n")
    (proj / "node_modules" / "dep").mkdir(parents=True)
    (proj / "node_modules" / "dep" / "ignored.go").write_text("")
    r = cg("index", proj, "--name", "mixed", "--db", tmp_path / "m.db")
    assert r.returncode == 0, r.stderr[-800:]
    cov = json.loads(r.stdout)["coverage"]
    assert lang(cov, "php")["status"] == "exact"
    got = {e["language"]: (e["status"], e["files"]) for e in cov["languages"] if e["status"] == "unsupported"}
    assert got == {"go": ("unsupported", 2),
                   "ruby": ("unsupported", 1), "csharp": ("unsupported", 1)}
    assert (lang(cov, "java")["status"], lang(cov, "java")["files"]) == ("heuristic", 1)       # Java plugin (#164)
    assert (lang(cov, "kotlin")["status"], lang(cov, "kotlin")["files"]) == ("heuristic", 1)   # Kotlin plugin (#9)
    assert (lang(cov, "swift")["status"], lang(cov, "swift")["files"]) == ("heuristic", 1)     # Swift plugin (#10)
    assert "scip-go" in lang(cov, "go")["hint"]
    from cg_code_graph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE["db"] = str(tmp_path / "m.db")
        txt = M.coverage("svc/main.go")
        assert "go: 2 files (.go 2) unsupported" in txt and "svc/main.go: NOT in the graph" in txt
        assert "not proof of absence" in txt
        assert "in the graph" in M.coverage("app/Services/StockService.php")
        miss = M.impact("PaymentGateway::charge")
        assert miss.startswith("no method matches") and "not fully covered here: " in miss and "go (2 files, unsupported)" in miss
        assert "normal search and file reading" in miss
        assert "not fully covered" in M.stats()
    finally:
        M.STATE.clear(); M.STATE.update(old)


def test_rust_without_rust_analyzer_is_heuristic(tmp_path):
    env_root = ROOT / "examples" / "rust-kvstore"
    r = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "index", str(env_root), "--db", str(tmp_path / "kv.db")],
                       cwd=ROOT, capture_output=True, text=True, env=dict(os.environ, CG_RUST_SCIP="0"))
    assert r.returncode == 0, r.stderr[-800:]
    rs = lang(json.loads(r.stdout)["coverage"], "rust")
    assert rs["status"] == "heuristic" and "rust-analyzer" in rs["hint"]


def test_note_when_everything_is_covered():
    cov = {"languages": [{"language": "php", "files": 3, "status": "exact", "by_ext": {".php": 3}}], "gaps": 0}
    assert C.note({"api": cov}).startswith("coverage: every source file cg found is indexed (php)")
    assert "not recorded" in C.note({"api": None})
    assert "every source file cg found is indexed" in C.render({"": cov})

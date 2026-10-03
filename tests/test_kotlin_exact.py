"""Kotlin exact layer (scip-java): a SCIP index of the Gradle build replaces the heuristic call edges with
compiler-resolved ones (`--scip`, CODEGRAPH_KOTLIN_SCIP_FILE, or an opted-in scip-java run through the native runner
cache), the heuristic layer stays the fallback without a JDK / scip-java, and `cg coverage` says which mode ran and
why. tests/kotlin_exact_fixture/index.scip was produced by scip-java 0.12.3 (Kotlin 2.1.20) from that directory."""
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_kotlin")
from codegraph import coverage  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "kotlin_exact_fixture"
SCIP = FIX / "index.scip"


def _index(tmp_path, **kw):
    db = tmp_path / "g.db"
    st = index_project(FIX, db, "kotlin-exact", **kw)
    return st, sqlite3.connect(db)


def _edges(con, kind="CALLS"):
    return {(s, d): c for s, d, c in con.execute("select src, dst, confidence from edges where kind=?", (kind,))}


def _kotlin_cov(st):
    return next(e for e in st["coverage"]["languages"] if e["language"] == "kotlin")


@pytest.fixture
def no_toolchain(tmp_path, monkeypatch):
    """No JDK and no scip-java anywhere: empty PATH dir, no JAVA_HOME, HOME without ~/tools."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.delenv("CODEGRAPH_SCIP_JAVA", raising=False)
    monkeypatch.delenv("CODEGRAPH_KOTLIN_SCIP", raising=False)
    monkeypatch.delenv("CODEGRAPH_KOTLIN_SCIP_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEGRAPH_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CODEGRAPH_NO_CACHE", raising=False)    # tests/sample.py sets it for the whole session
    return empty


def _exe(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def fake_tools(no_toolchain, monkeypatch):
    """A `java` on PATH and a scip-java stand-in (CODEGRAPH_SCIP_JAVA) whose behaviour the test picks."""
    _exe(no_toolchain / "java", "exit 0\n")
    monkeypatch.setenv("PATH", f"{no_toolchain}{os.pathsep}/usr/bin{os.pathsep}/bin")

    def make(body: str) -> Path:
        tool = _exe(no_toolchain / "scip-java", body)
        monkeypatch.setenv("CODEGRAPH_SCIP_JAVA", str(tool))
        return tool
    return make


def test_exact_mode_with_scip_flag(tmp_path, no_toolchain):
    st, con = _index(tmp_path, scip=[str(SCIP)])
    k = st["plugins"]["kotlin"]
    assert k["mode"] == "scip" and k["scip"]["source"] == "--scip"
    # the Kotlin plugin consumed the index; the generic SCIP importer did not add a second copy of the graph
    assert st["plugins"][f"scip:{SCIP}"]["status"].startswith("imported by the Kotlin plugin")
    calls = _edges(con)
    # compiler-resolved: the overload `add(2.0)`, `it.area()` inside a lambda, the extension function `label()`
    for e in [("function:demo.build", "method:demo.Registry.add"),
              ("method:demo.Registry.getShapes", "method:demo.Shape.area"),
              ("method:demo.Report.render", "function:demo.label"),
              ("method:demo.Report.render", "method:demo.Store.load"),
              ("function:demo.main", "method:demo.Report.render")]:
        assert calls.get(e) == "exact", e
    # Report.render calls Store.load, not the same-named Cache.load
    assert ("method:demo.Report.render", "method:demo.Cache.load") not in calls
    # the property access `shapes.add(shape)` reports the synthetic getter getShapes(), which collides with the
    # declared `fun getShapes()`: no call edge from it
    assert ("method:demo.Registry.add", "method:demo.Registry.getShapes") not in calls
    # no heuristic call edges are left in the indexed Kotlin files
    assert set(calls.values()) == {"exact"}
    inst = _edges(con, "INSTANTIATES")
    assert inst.get(("function:demo.main", "class:demo.Store")) == "exact"
    # the overload add(side: Double) is the source of `Square(side)`, not the class
    assert inst.get(("method:demo.Registry.add", "class:demo.Square")) == "exact"
    assert k["scip_defs_matched"] >= 18 and k["scip_defs_unmatched"] == 0 and k["scip_files"] == 3
    evh = k["exact_vs_heuristic"]
    assert evh["precision"] == 1.0 and evh["exact_edges"] > evh["heuristic_edges"] and evh["recall"] < 1.0
    cov = _kotlin_cov(st)
    assert cov["status"] == "exact" and "scip-java index (--scip)" in cov["reason"]
    assert "kotlin: 5 files exact: scip-java index (--scip)" in coverage.render({"": st["coverage"]})


def test_exact_mode_from_env_file(tmp_path, no_toolchain, monkeypatch):
    monkeypatch.setenv("CODEGRAPH_KOTLIN_SCIP_FILE", str(SCIP))
    st, con = _index(tmp_path)
    assert st["plugins"]["kotlin"]["mode"] == "scip"
    assert st["plugins"]["kotlin"]["scip"]["source"] == "CODEGRAPH_KOTLIN_SCIP_FILE"
    assert _edges(con).get(("function:demo.build", "method:demo.Registry.add")) == "exact"
    monkeypatch.setenv("CODEGRAPH_KOTLIN_SCIP_FILE", str(tmp_path / "missing.scip"))
    st, _ = _index(tmp_path)
    assert st["plugins"]["kotlin"]["mode"] == "heuristic"
    assert "does not exist" in _kotlin_cov(st)["reason"]


def test_heuristic_fallback_without_jdk(tmp_path, no_toolchain, monkeypatch):
    monkeypatch.setenv("CODEGRAPH_KOTLIN_SCIP", "1")     # opted in, but there is nothing to run
    st, con = _index(tmp_path)
    k = st["plugins"]["kotlin"]
    assert k["mode"] == "heuristic" and k["scip"]["status"].startswith("no JDK")
    cov = _kotlin_cov(st)
    assert cov["status"] == "heuristic" and "no JDK" in cov["reason"]
    calls = _edges(con)
    assert calls and set(calls.values()) == {"heuristic"}
    assert calls.get(("method:demo.Report.render", "method:demo.Store.load")) == "heuristic"
    text = coverage.render({"": st["coverage"]})
    assert "kotlin: 5 files (.kt 3, .kts 2) heuristic" in text and "CODEGRAPH_KOTLIN_SCIP=1" in text


def test_heuristic_reasons_scip_java_missing_and_not_opted_in(tmp_path, fake_tools, monkeypatch):
    monkeypatch.setenv("CODEGRAPH_SCIP_JAVA", str(tmp_path / "no-such-scip-java"))
    st, _ = _index(tmp_path)
    assert st["plugins"]["kotlin"]["scip"]["status"].startswith("scip-java not installed")
    fake_tools("exit 1\n")
    st, _ = _index(tmp_path)
    # running scip-java executes the project's Gradle build scripts: only on request
    assert st["plugins"]["kotlin"]["mode"] == "heuristic"
    assert "set CODEGRAPH_KOTLIN_SCIP=1" in _kotlin_cov(st)["reason"]


def test_scip_java_run_failure_keeps_heuristic(tmp_path, fake_tools, monkeypatch):
    fake_tools('echo "error: Plugin com.sourcegraph.semanticdb_kotlinc.AnalyzerRegistrar is incompatible" >&2\n'
               "exit 1\n")
    monkeypatch.setenv("CODEGRAPH_KOTLIN_SCIP", "1")
    st, con = _index(tmp_path)
    k = st["plugins"]["kotlin"]
    assert k["mode"] == "heuristic" and k["scip"]["error"].startswith("exit 1")
    reason = _kotlin_cov(st)["reason"]
    assert "scip-java run failed (exit 1" in reason and "does not load into this Kotlin version" in reason
    assert set(_edges(con).values()) == {"heuristic"}


def test_scip_java_run_through_runner_cache(tmp_path, fake_tools, monkeypatch):
    log = tmp_path / "runs.log"
    fake_tools(f'[ "$1" = version ] && {{ echo 0.0-test; exit 0; }}\n'
               f'echo run >> "{log}"\n'
               'for a in "$@"; do case "$a" in --output=*) cp "' + str(SCIP) + '" "${a#--output=}";; esac; done\n'
               "exit 0\n")
    monkeypatch.setenv("CODEGRAPH_KOTLIN_SCIP", "1")
    st, con = _index(tmp_path)
    k = st["plugins"]["kotlin"]
    assert k["mode"] == "scip" and k["scip"]["source"] == "scip-java" and k["scip"]["cache"] == "miss"
    assert k["scip"]["build_file"] == "build.gradle.kts"
    assert _edges(con).get(("method:demo.Report.render", "function:demo.label")) == "exact"
    assert "scip-java index (scip-java)" in _kotlin_cov(st)["reason"]
    st, _ = _index(tmp_path)                 # unchanged sources: the cached index, no second build
    assert st["plugins"]["kotlin"]["scip"]["cache"] == "hit"
    assert log.read_text().count("run") == 1
    json.dumps(st)                           # stats stay serialisable

"""Swift exact layer (index store): `swift build --enable-index-store` (opt-in, CG_SWIFT_INDEX=1) or an
existing store (CG_SWIFT_INDEX_STORE) replaces the heuristic call edges with the compiler's, read through the
toolchain's libIndexStore; without a toolchain the heuristic layer stays and `cg coverage` says why. The exact-mode
tests need a Swift toolchain (swift on PATH, CG_SWIFT or ~/tools/swift-*) and are skipped without one."""
import os
import shutil
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from cg_code_graph import coverage  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.plugins.swift import exact, indexstore  # noqa: E402

FIX = ROOT / "tests" / "swift_exact_fixture"
SWIFT = exact.find_swift()
LIB = indexstore.find_lib(SWIFT) if SWIFT else None
needs_toolchain = pytest.mark.skipif(not (SWIFT and LIB), reason="no Swift toolchain with libIndexStore")


def _index(tmp_path, root=FIX):
    db = tmp_path / f"g{len(list(tmp_path.glob('g*.db')))}.db"
    st = index_project(root, db, "swift-exact")
    return st, sqlite3.connect(db)


def _edges(con, kind="CALLS"):
    return {(s, d): c for s, d, c in con.execute("select src, dst, confidence from edges where kind=?", (kind,))}


def _cov(st):
    return next(e for e in st["coverage"]["languages"] if e["language"] == "swift")


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    for v in ("CG_SWIFT_INDEX", "CG_SWIFT_INDEX_STORE", "CG_NO_CACHE", "CG_LIBINDEXSTORE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))
    return monkeypatch


@pytest.fixture
def no_toolchain(clean_env, tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    clean_env.setenv("PATH", str(empty))
    clean_env.setenv("HOME", str(tmp_path / "home"))
    clean_env.delenv("CG_SWIFT", raising=False)
    return empty


def _fake_toolchain(tmp_path, monkeypatch, body: str) -> Path:
    """usr/bin/swift (a shell script) with an (empty) usr/lib/libIndexStore.so next to it."""
    usr = tmp_path / "fake-swift" / "usr"
    (usr / "bin").mkdir(parents=True)
    (usr / "lib").mkdir()
    (usr / "lib" / "libIndexStore.so").write_bytes(b"")
    sw = usr / "bin" / "swift"
    sw.write_text("#!/bin/sh\n" + body)
    sw.chmod(sw.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CG_SWIFT", str(sw))
    return sw


@needs_toolchain
def test_exact_mode_swift_build(tmp_path, clean_env):
    clean_env.setenv("CG_SWIFT", SWIFT)
    clean_env.setenv("CG_SWIFT_INDEX", "1")
    st, con = _index(tmp_path)
    k = st["plugins"]["swift"]
    assert k["mode"] == "indexstore", k["index"]
    assert k["index"]["source"] == "swift build" and k["index"]["cache"] == "miss"
    calls = _edges(con)
    for e in [("function:build", "method:Registry.add"),           # both overloads, one node
              ("method:Registry.total", "method:Shape.area"),       # `$0.area()` in a closure, protocol requirement
              ("method:Report.render", "method:Shape.label"),       # protocol extension method
              ("method:Report.render", "method:Store.load"),
              ("file:swift:Sources/Demo/main.swift", "method:Report.render")]:      # top-level code
        assert calls.get(e) == "exact", e
    assert ("method:Report.render", "method:Cache.load") not in calls
    assert set(calls.values()) == {"exact"}
    inst = _edges(con, "INSTANTIATES")
    # the body of the overload add(side:) belongs to method:Registry.add, not to the class
    assert inst.get(("method:Registry.add", "class:Square")) == "exact"
    assert inst.get(("file:swift:Sources/Demo/main.swift", "class:Store")) == "exact"
    assert k["index_files"] == 3 and k["index_defs_unmatched"] == 0
    evh = k["exact_vs_heuristic"]
    assert evh["exact_edges"] > 0 and evh["recall"] < 1.0
    cov = _cov(st)
    assert cov["status"] == "exact" and cov["reason"] == "Swift index store (swift build)"   # Package.swift not counted
    assert "swift: 4 files exact: Swift index store (swift build)" in coverage.render({"": st["coverage"]})
    st2, _ = _index(tmp_path)                    # unchanged sources: no second build
    assert st2["plugins"]["swift"]["index"]["cache"] == "hit"


@needs_toolchain
def test_existing_store_from_another_checkout(tmp_path, clean_env):
    """CG_SWIFT_INDEX_STORE built in a different directory: source paths are matched by project suffix."""
    clean_env.setenv("CG_SWIFT", SWIFT)
    clean_env.setenv("CG_SWIFT_INDEX", "1")
    other = tmp_path / "elsewhere" / "Demo"
    shutil.copytree(FIX, other)
    st, _ = _index(tmp_path, other)
    store = next(Path(st["plugins"]["swift"]["index"]["build_path"]).glob("*/debug/index/store"))
    clean_env.delenv("CG_SWIFT_INDEX")
    clean_env.setenv("CG_SWIFT_INDEX_STORE", str(store))
    st, con = _index(tmp_path)
    assert st["plugins"]["swift"]["mode"] == "indexstore"
    assert st["plugins"]["swift"]["index"]["source"] == "CG_SWIFT_INDEX_STORE"
    assert _edges(con).get(("method:Registry.total", "method:Shape.area")) == "exact"


def test_heuristic_without_toolchain(tmp_path, no_toolchain):
    clean = no_toolchain
    st, con = _index(tmp_path)
    k = st["plugins"]["swift"]
    assert k["mode"] == "heuristic" and k["index"]["status"].startswith("no Swift toolchain")
    cov = _cov(st)
    assert cov["status"] == "heuristic" and "no Swift toolchain" in cov["reason"]
    calls = _edges(con)
    assert calls and set(calls.values()) == {"heuristic"}
    assert calls.get(("method:Report.render", "method:Store.load")) == "heuristic"
    assert "CG_SWIFT_INDEX=1" in coverage.render({"": st["coverage"]})
    assert clean is not None


def test_heuristic_reasons(tmp_path, no_toolchain, monkeypatch):
    monkeypatch.setenv("CG_SWIFT_INDEX_STORE", str(tmp_path / "missing"))
    st, _ = _index(tmp_path)
    assert "is not a directory" in _cov(st)["reason"]
    monkeypatch.delenv("CG_SWIFT_INDEX_STORE")
    _fake_toolchain(tmp_path, monkeypatch, "exit 0\n")
    st, _ = _index(tmp_path)
    # the build runs Package.swift and package plugins: only on request
    assert st["plugins"]["swift"]["mode"] == "heuristic" and "set CG_SWIFT_INDEX=1" in _cov(st)["reason"]
    # an Xcode project (no Package.swift) needs an existing store
    app = tmp_path / "XcodeApp"
    (app / "App").mkdir(parents=True)
    (app / "App" / "App.swift").write_text("struct A { func f() {} }\n")
    st, _ = _index(tmp_path, app)
    assert "no Package.swift" in _cov(st)["reason"] and "DerivedData" in _cov(st)["reason"]


def test_swift_build_failure_keeps_heuristic(tmp_path, no_toolchain, monkeypatch):
    _fake_toolchain(tmp_path, monkeypatch, '[ "$1" = --version ] && { echo "Swift version 0.0"; exit 0; }\n'
                    'echo "/src/Sources/Demo/main.swift:1:8: error: no such module \'SwiftUI\'" >&2\nexit 1\n')
    monkeypatch.setenv("CG_SWIFT_INDEX", "1")
    st, con = _index(tmp_path)
    k = st["plugins"]["swift"]
    assert k["mode"] == "heuristic" and k["index"]["error"] == "exit 1"
    assert "swift build failed (exit 1: main.swift:1:8: error: no such module 'SwiftUI')" in _cov(st)["reason"]
    assert set(_edges(con).values()) == {"heuristic"}


@needs_toolchain
def test_exact_extension_initializer_of_sdk_type(tmp_path, clean_env):
    """An initializer the project declares in an extension of an SDK type (`extension String { init(order:) }`):
    the compiler's call is an INSTANTIATES / CALLS edge (its parent is the extension, mapped to the type's node), the
    SDK's own initializers (`String(repeating:count:)`) are not, and the heuristic layer agrees on both."""
    root = tmp_path / "proj"
    shutil.copytree(FIX, root)
    (root / "Sources" / "Demo" / "Ext.swift").write_text(
        "extension String {\n    init(order: Int) {\n        self = \"o\\(order)\"\n    }\n}\n\n"
        "func ext() -> String {\n    let a = String(order: 2)\n    let b = String(repeating: \"x\", count: 2)\n"
        "    return a + b\n}\n")
    clean_env.setenv("CG_SWIFT", SWIFT)
    clean_env.setenv("CG_SWIFT_INDEX", "1")
    st, con = _index(tmp_path, root)
    assert st["plugins"]["swift"]["mode"] == "indexstore"
    rows = {(s, d, k, ln, c) for s, d, k, ln, c in con.execute(
        "select src, dst, kind, line, confidence from edges where file='Sources/Demo/Ext.swift' "
        "and kind in ('CALLS', 'INSTANTIATES')")}
    assert ("function:ext", "class:String", "INSTANTIATES", 8, "exact") in rows
    assert ("function:ext", "method:String.init", "CALLS", 8, "exact") in rows
    assert not any(r[3] == 9 for r in rows)

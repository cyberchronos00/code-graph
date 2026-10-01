"""C / C++ plugin on the generic samples (examples/c-ringbuf, examples/cpp-eventbus).

Heuristic mode (tree-sitter, no compile database) always runs. Exact mode needs scip-clang and cmake (to generate
compile_commands.json into a temp dir) and is skipped with a message when either is missing."""
import json
import shutil

import pytest

from native_util import DB, ROOT, TS_SKIP, cmake_compdb, have_tree_sitter, index, scip_clang, stats_of

pytestmark = pytest.mark.skipif(not have_tree_sitter(), reason=TS_SKIP)

C = ROOT / "examples" / "c-ringbuf"
CPP = ROOT / "examples" / "cpp-eventbus"
SKIP = ("scip-clang and/or cmake not installed (see docs/native.md: download scip-clang from "
        "github.com/sourcegraph/scip-clang/releases, or set CODEGRAPH_SCIP_CLANG); exact-mode C/C++ tests skipped")
_CACHE = {}


def heur(src):
    k = ("h", src)
    if k not in _CACHE:
        db, res = index(src, CODEGRAPH_C_SCIP="0", CODEGRAPH_COMPDB=None)
        _CACHE[k] = (DB(db), res)
    return _CACHE[k]


def exact(src):
    if not scip_clang() or not shutil.which("cmake"):
        pytest.skip(SKIP)
    k = ("s", src)
    if k not in _CACHE:
        cdb = cmake_compdb(src)
        if cdb is None:
            pytest.skip("cmake configure failed (no C/C++ compiler?); exact-mode tests skipped")
        db, res = index(src, CODEGRAPH_C_SCIP=None, CODEGRAPH_COMPDB=str(cdb))
        _CACHE[k] = (DB(db), res)
    return _CACHE[k]


# ---------------------------------------------------------------- C, heuristic

def test_c_heuristic_entries_and_static_keys():
    g, res = heur(C)
    assert stats_of(res, "c_cpp")["mode"] == "heuristic"
    assert g.entry("function:tools/rbtool.c#main") == "main"
    assert g.entry("function:tests/test_ringbuf.c#main") == "test"
    assert g.entry("function:rb_create") == "public_api"          # RB_API export macro in include/
    assert g.node("function:src/ringbuf.c#rb_lock")["attrs"] and json.loads(g.node("function:src/ringbuf.c#rb_lock")["attrs"])["static"]
    assert g.entry("function:rb_compact_legacy") is None           # not exported, never called
    assert not g.edges("CALLS", dst="function:rb_compact_legacy")


def test_c_heuristic_calls_includes_env_gates():
    g, _ = heur(C)
    e = g.edges("CALLS", "function:tools/rbtool.c#main", "function:rb_create")
    assert e and e[0]["confidence"] == "heuristic"
    assert g.has("CALLS", "function:rb_write", "function:src/ringbuf.c#rb_lock")
    assert g.has("INCLUDES", "file:src/ringbuf.c", "file:include/ringbuf/ringbuf.h")
    assert g.has("READS_ENV", "function:rb_debug_enabled", "env:RB_DEBUG")
    assert g.has("GATED_BY", "function:src/ringbuf.c#rb_lock", "define:RB_THREADSAFE")
    assert g.has("GATED_BY", "field:rb_buffer::lock", "define:RB_THREADSAFE")
    # include guards are not gates
    assert not g.q("SELECT 1 FROM nodes WHERE id LIKE 'define:%_H'")


def test_c_gating_defines_off():
    db, _ = index(C, gates=True, CODEGRAPH_C_SCIP="0", CODEGRAPH_COMPDB=None)
    g = DB(db)
    e = g.edges("CONTAINS", "struct:rb_buffer", "field:rb_buffer::lock")
    assert e and e[0]["gate"] == "minimal_build"
    assert json.loads(e[0]["attrs"])["guard_expr"] == "#if defined(RB_THREADSAFE)" or "RB_THREADSAFE" in json.loads(e[0]["attrs"])["guard_expr"]


# ---------------------------------------------------------------- C++, heuristic

def test_cpp_heuristic_virtual_dispatch():
    g, _ = heur(CPP)
    impl = g.edges("IMPLEMENTED_BY", "method:bus::Handler::handle", "method:bus::LogHandler::handle")
    assert impl and impl[0]["confidence"] == "heuristic"
    assert g.has("OVERRIDDEN_BY", "method:bus::Handler::accepts", "method:bus::CounterHandler::accepts")
    assert g.has("EXTENDS", "class:bus::LogHandler", "class:bus::Handler")
    call = g.edges("CALLS", "method:bus::Bus::publish", "method:bus::Handler::handle")
    assert call and json.loads(call[0]["attrs"])["dispatch"] == "virtual"
    assert g.entry("function:app/main.cpp#main") == "main"
    assert g.has("READS_ENV", "function:src/bus.cpp#bus::verbose_from_env", "env:BUS_VERBOSE")


def test_cpp_reaches_through_virtual_dispatch():
    from codegraph import query as Q
    from codegraph.core.store import GraphStore
    g, _ = heur(CPP)
    res = Q.reaches(GraphStore(str(g.path)), ["bus::LogHandler::handle"])
    cls = {i["id"]: i["class"] for i in res["items"]}
    assert cls.get("function:app/main.cpp#main") == "runtime"
    assert cls.get("function:tests/bus_test.cpp#main") == "dev"


def test_cpp_gating_defines_off():
    db, _ = index(CPP, gates=True, CODEGRAPH_C_SCIP="0", CODEGRAPH_COMPDB=None)
    g = DB(db)
    e = g.edges("CALLS", "function:app/main.cpp#main", "method:bus::Bus::published")
    assert e and e[0]["gate"] == "minimal_build"
    assert json.loads(e[0]["attrs"])["guard_expr"].startswith("#if")


# ---------------------------------------------------------------- exact mode (scip-clang + compile_commands.json)

def test_c_exact_calls():
    g, res = exact(C)
    assert stats_of(res, "c_cpp")["mode"] == "scip"
    e = g.edges("CALLS", "function:tools/rbtool.c#main", "function:rb_create")
    assert e and e[0]["confidence"] == "exact"
    assert g.has("CALLS", "function:tests/test_ringbuf.c#test_wraparound", "function:rb_read")


def test_cpp_exact_dispatch_and_ids():
    g, res = exact(CPP)
    assert stats_of(res, "c_cpp")["mode"] == "scip"
    impl = g.edges("IMPLEMENTED_BY", "method:bus::Handler::handle", "method:bus::CounterHandler::handle")
    assert impl and impl[0]["confidence"] == "resolved"
    call = g.edges("CALLS", "method:bus::Bus::publish", "method:bus::Handler::handle")
    assert call and json.loads(call[0]["attrs"])["dispatch"] == "virtual"
    gh, _ = heur(CPP)
    code = "kind IN ('function','method','class','struct')"
    assert {r[0] for r in gh.q(f"SELECT id FROM nodes WHERE {code}")} == {r[0] for r in g.q(f"SELECT id FROM nodes WHERE {code}")}


def test_mcp_tools_on_native_graph():
    import codegraph.mcp_server as M
    g, _ = heur(CPP)
    old = M.STATE["db"]
    M.STATE["db"] = str(g.path)
    try:
        out = M.reaches(["bus::LogHandler::handle"])
        assert "## RUNTIME" in out and "## DEV" in out and "main @main.cpp:7" in out
        imp = M.impact("bus::Handler::handle")
        assert "## main" in imp and "bus::Bus::publish" in imp
        down = M.downstream("app/main.cpp")
        assert "env:BUS_VERBOSE" in down and "define:BUS_WITH_METRICS" in down
    finally:
        M.STATE["db"] = old

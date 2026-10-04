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


def test_c_static_inline_in_header(tmp_path):
    """#92: a header's static (inline) function is compiled into every file that includes it, directly or through
    another header, so those files' calls bind to it. A file with its own static of that name binds to its own, and a
    file that includes neither gets no edge."""
    (tmp_path / "tb.h").write_text("static inline void toolbar_show(int *t) { (void)t; }\n")
    (tmp_path / "wrap.h").write_text('#include "tb.h"\n')
    (tmp_path / "main.c").write_text('#include "tb.h"\nvoid run(void) { int t; toolbar_show(&t); }\n')
    (tmp_path / "deep.c").write_text('#include "wrap.h"\nvoid deep(void) { int t; toolbar_show(&t); }\n')
    (tmp_path / "other.c").write_text("static void toolbar_show(int *t) { (void)t; }\n"
                                      "void own(void) { int t; toolbar_show(&t); }\n")
    (tmp_path / "lone.c").write_text("void lone(void) { int t; toolbar_show(&t); }\n")
    db, _ = index(tmp_path, CODEGRAPH_C_SCIP="0", CODEGRAPH_COMPDB=None)
    g = DB(db)
    tgt = "function:tb.h#toolbar_show"
    assert g.node(tgt)
    assert g.has("CALLS", "function:run", tgt) and g.has("CALLS", "function:deep", tgt)
    assert g.has("CALLS", "function:own", "function:other.c#toolbar_show") and not g.has("CALLS", "function:own", tgt)
    assert not g.edges("CALLS", "function:lone")


def test_cpp_file_level_macro_call_keeps_next_class(tmp_path):
    """#131: `ABSL_FLAG(uint16_t, port, 50051, "..");` at file level parsed as a broken function definition that ran
    to the next `}` and swallowed the class after it. The statement is blanked before parsing (same positions); its
    call is still a file-level call, and a macro call inside a function is untouched."""
    (tmp_path / "server.cc").write_text(
        '#include "absl/flags/flag.h"\n'
        "\n"
        'ABSL_FLAG(uint16_t, port, 50051, "Server port for the service");\n'
        "\n"
        "// Logic and data behind the server's behavior.\n"
        "class GreeterImpl final : public Greeter::Service {\n"
        "  Status SayHello(ServerContext* context, const HelloRequest* request,\n"
        "                  HelloReply* reply) override {\n"
        "    return Helper(reply);\n"
        "  }\n"
        "  Status Helper(HelloReply* reply) { return Status::OK; }\n"
        "};\n"
        "\n"
        "void RunServer(uint16_t port) {\n"
        "  GreeterImpl service;\n"
        "  LOG_EVERY(port);\n"
        "}\n")
    db, _ = index(tmp_path, CODEGRAPH_C_SCIP="0", CODEGRAPH_COMPDB=None)
    g = DB(db)
    assert g.node("class:GreeterImpl") and g.node("method:GreeterImpl::SayHello")
    assert g.node("function:RunServer")["line"] == 14
    assert g.has("CALLS", "method:GreeterImpl::SayHello", "method:GreeterImpl::Helper")
    assert not g.node("function:ABSL_FLAG")


def test_cpp_member_call_does_not_reach_a_class_of_another_source_file(tmp_path):
    """A member call with an unknown receiver used to bind to the only project method of that name. A class defined
    only in another .cc (not a header, not included) is out of reach: `client.ping()` in the client program is not
    the server program's handler. A class from an included header still is."""
    (tmp_path / "server.cc").write_text("class Handler {\n public:\n  void ping() {}\n};\nstruct Reply { int n; };\n")
    (tmp_path / "shared.h").write_text("class Shared {\n public:\n  void touch() {}\n};\n")
    (tmp_path / "client.cc").write_text('#include "shared.h"\n'
                                        "void run(Client& client, Shared& s) {\n  Reply r;\n  Shared t;\n  client.ping();\n  s.touch();\n}\n")
    db, res = index(tmp_path, CODEGRAPH_C_SCIP="0", CODEGRAPH_COMPDB=None)
    g = DB(db)
    assert g.node("method:Handler::ping") and not g.edges("CALLS", "function:run", "method:Handler::ping")
    assert g.has("CALLS", "function:run", "method:Shared::touch") and g.has("USES_TYPE", "function:run", "class:Shared")
    assert g.node("struct:Reply") and not g.edges("USES_TYPE", "function:run", "struct:Reply")


def test_cpp_thread_safety_annotations_are_not_declarators(tmp_path):
    """`void Write() ABSL_EXCLUSIVE_LOCKS_REQUIRED(&mu_) {` was a method `mu_`, and `bool done_ GUARDED_BY(mu_)` a
    second field `mu_`; the annotations are blanked before parsing."""
    (tmp_path / "reactor.cc").write_text(
        "class Reactor {\n"
        " public:\n"
        "  void Start() { Write(); }\n"
        "\n"
        " private:\n"
        "  void Write() ABSL_EXCLUSIVE_LOCKS_REQUIRED(&mu_) { Flush(); }\n"
        "  void Flush() GTEST_LOCK_EXCLUDED_(mutex_) {}\n"
        "  Mutex mu_;\n"
        "  bool done_ GUARDED_BY(mu_) = false;\n"
        "};\n"
        "\n"
        "class SCOPED_LOCKABLE MutexLock {\n"
        " public:\n"
        "  explicit MutexLock(Mutex* mu) EXCLUSIVE_LOCK_FUNCTION(mu) : mu_(mu) {}\n"
        "  ~MutexLock() UNLOCK_FUNCTION() {}\n"
        "\n"
        " private:\n"
        "  Mutex* const mu_;\n"
        "};\n")
    db, _ = index(tmp_path, CODEGRAPH_C_SCIP="0", CODEGRAPH_COMPDB=None)
    g = DB(db)
    assert g.has("CALLS", "method:Reactor::Start", "method:Reactor::Write")
    assert g.has("CALLS", "method:Reactor::Write", "method:Reactor::Flush")
    assert not g.node("method:Reactor::mu_") and g.node("field:Reactor::done_") and g.node("field:Reactor::mu_")
    assert g.q("select count(*) from nodes where id like 'field:Reactor::mu_%'")[0][0] == 1
    assert g.node("class:MutexLock") and g.node("method:MutexLock::~MutexLock")

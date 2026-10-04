"""Rust plugin on the generic sample workspace (examples/rust-kvstore) and a syntactic routes fixture.

Heuristic mode (tree-sitter only) always runs. SCIP mode needs rust-analyzer (`rustup component add rust-analyzer`)
and is skipped with a message when it is not installed."""
import json

import pytest

from native_util import DB, ROOT, TS_SKIP, have_tree_sitter, index, rust_analyzer, stats_of

pytestmark = pytest.mark.skipif(not have_tree_sitter(), reason=TS_SKIP)

SAMPLE = ROOT / "examples" / "rust-kvstore"
ROUTES = ROOT / "tests" / "rust_routes_fixture"
RA_SKIP = "rust-analyzer not installed (rustup component add rust-analyzer, or set CODEGRAPH_RUST_ANALYZER); SCIP-mode Rust tests skipped"
_CACHE = {}


def heur():
    if "h" not in _CACHE:
        db, res = index(SAMPLE, CODEGRAPH_RUST_SCIP="0")
        _CACHE["h"] = (DB(db), res)
    return _CACHE["h"]


def scip():
    if not rust_analyzer():
        pytest.skip(RA_SKIP)
    if "s" not in _CACHE:
        db, res = index(SAMPLE, CODEGRAPH_RUST_SCIP=None)
        _CACHE["s"] = (DB(db), res)
    return _CACHE["s"]


MEM_GET = "method:kv_core::store::<MemoryStore as Store>::get"
STORE_GET = "method:kv_core::store::Store::get"


def test_structure_crates_modules_features():
    g, res = heur()
    assert stats_of(res, "rust")["mode"] == "heuristic"
    for c in ("kv_core", "kv", "kv_ffi"):
        assert g.node(f"crate:{c}"), c
    assert g.node("mod:kv_core::store::file")
    assert g.node("feature:kv-core/compression")
    assert g.has("CONTAINS", "mod:kv_core::store", MEM_GET) or g.edges("CONTAINS", dst=MEM_GET)


def test_entry_kinds():
    g, _ = heur()
    assert g.entry("function:kv::main") == "main"
    assert g.entry("function:kv_ffi::kv_put") == "ffi_export"
    assert g.entry("function:kv_core[test:roundtrip]::put_then_get") == "test"
    assert g.entry("function:kv_core::util::tests::checksum_adds_bytes") == "test"
    assert g.entry("function:kv_core[bench:put]::main") == "bench"
    assert g.entry("function:kv_core[example:basic]::main") == "example"
    assert g.entry("function:kv_core[build]::main") == "build_script"
    assert g.entry("function:kv_core::migrate") == "public_api"
    # private helper never reached: dead code candidate
    n = g.node("function:kv_core::util::legacy_hash")
    assert n and n["entry_kind"] is None
    assert not g.q("SELECT 1 FROM node_entry WHERE node_id=?", "function:kv_core::util::legacy_hash")


def test_trait_impls_heuristic():
    g, _ = heur()
    e = g.edges("IMPLEMENTED_BY", STORE_GET, MEM_GET)
    assert e and e[0]["confidence"] == "heuristic"
    assert g.has("IMPLEMENTED_BY", "method:kv_core::codec::Codec::encode", "method:kv_core::codec::<Rle as Codec>::encode")


def test_facts_env_unsafe_ffi_gates():
    g, _ = heur()
    assert g.has("READS_ENV", "function:kv::main", "env:KV_VERBOSE")
    assert g.has("READS_ENV", "function:kv::run", "env:KV_CODEC")           # option_env!
    assert g.has("READS_ENV", "const:kv_core::BUILD_ID", "env:KV_BUILD_ID")  # env! in a const
    assert g.has("USES_UNSAFE", "function:kv_core::util::checksum", "unsafe:kv_core")
    assert g.node("ffi:kv_ffi::strlen")
    assert g.has("GATED_BY", "struct:kv_core::codec::Rle", "feature:kv-core/compression")
    assert g.has("GATED_BY", "mod:kv_core::store::file", "feature:kv-core/fs")
    # calls inside macro arguments (assert_eq!(checksum(..), ..)) and `use super::*`
    assert g.has("CALLS", "function:kv_core::util::tests::checksum_adds_bytes", "function:kv_core::util::checksum")


def test_reaches_groups_runtime_library_dev():
    from codegraph import query as Q
    from codegraph.core.store import GraphStore
    g, _ = heur()
    res = Q.reaches(GraphStore(str(g.path)), ["unsafe:kv_core"])
    cls = {i["id"]: i["class"] for i in res["items"]}
    assert cls.get("function:kv_core::util::checksum") == "runtime"
    assert cls.get("function:kv_core[bench:put]::main") == "dev"
    out = Q.render_reaches(res)
    assert "RUNTIME" in out and "DEV/BUILD-ONLY" in out


def test_gating_features_off():
    db, _ = index(SAMPLE, gates=True, CODEGRAPH_RUST_SCIP="0")
    g = DB(db)
    # statement-level #[cfg(feature = "fs")] block inside open_default
    e = g.edges("CALLS", "function:kv_core::open_default", "function:kv_core::store::file::data_dir")
    assert e and e[0]["gate"] == "minimal_build"
    assert "cfg(feature" in json.loads(e[0]["attrs"])["guard_expr"]
    # the ungated fallback stays live
    live = g.edges("CALLS", "function:kv::main", "function:kv_core::open_default")
    assert live and live[0]["gate"] is None


def test_routes_fixture():
    db, res = index(ROUTES, CODEGRAPH_RUST_SCIP="0")
    g = DB(db)
    assert g.entry("function:routes_fixture::main") == "main"
    attrs = json.loads(g.node("function:routes_fixture::main")["attrs"])
    assert attrs.get("runtime") == "tokio"
    assert g.has("ROUTES_TO", "route:GET /items", "function:routes_fixture::handlers::list_items")
    assert g.has("ROUTES_TO", "route:POST /items", "function:routes_fixture::handlers::create_item")
    assert g.has("ROUTES_TO", "route:GET /stats", "function:routes_fixture::admin::stats")           # aliased verb
    assert g.has("ROUTES_TO", "route:GET /legacy/ping", "function:routes_fixture::admin::legacy_ping")  # actix attribute
    assert g.has("READS_ENV", "function:routes_fixture::main", "env:APP_PORT")                      # via const
    assert g.has("GATED_BY", "function:routes_fixture::admin::router", "feature:routes-fixture/admin")  # inner #![cfg]


# ---------------------------------------------------------------- SCIP mode (rust-analyzer)

def test_scip_exact_calls_and_dispatch():
    g, res = scip()
    st = stats_of(res, "rust")
    assert st["mode"] == "scip", st
    e = g.edges("CALLS", "function:kv_core[test:roundtrip]::put_then_get", MEM_GET)
    assert e and e[0]["confidence"] in ("exact", "resolved")
    d = g.edges("CALLS", "function:kv_core::migrate", STORE_GET)
    assert d and json.loads(d[0]["attrs"] or "{}").get("dispatch") == "trait"
    impl = g.edges("IMPLEMENTED_BY", STORE_GET, MEM_GET)
    assert impl and impl[0]["confidence"] == "resolved"


def test_scip_and_heuristic_share_node_ids():
    gs, _ = scip()
    gh, _ = heur()
    code = "kind IN ('function','method','struct','trait','enum')"
    ids_s = {r[0] for r in gs.q(f"SELECT id FROM nodes WHERE {code}")}
    ids_h = {r[0] for r in gh.q(f"SELECT id FROM nodes WHERE {code}")}
    assert ids_h <= ids_s
    assert len(ids_s - ids_h) <= 2


def test_scip_path_main_to_unsafe():
    from codegraph import query as Q
    from codegraph.core.store import GraphStore
    g, _ = scip()
    p = Q.path_between(GraphStore(str(g.path)), "kv::main", "unsafe:kv_core")
    assert p and p[-1]["to"] == "unsafe:kv_core", p
    assert any(h["kind"] == "IMPLEMENTED_BY" for h in p)  # goes through trait dispatch


# ---------------------------------------------------------------- tests generated by project macro_rules! (#106)

MACROS = ROOT / "tests" / "rust_macro_tests_fixture"
IT = "mtest_fixture[test:integration]"


@pytest.mark.parametrize("scip_mode", ["0", None], ids=["heuristic", "scip"])
def test_macro_generated_tests(scip_mode):
    if scip_mode is None and not rust_analyzer():
        pytest.skip(RA_SKIP)
    db, res = index(MACROS, CODEGRAPH_RUST_SCIP=scip_mode)
    g = DB(db)
    st = stats_of(res, "rust")
    assert st["test_macros"] == 2 and st["macro_tests"] == 4, st
    for t in (f"function:{IT}::cli::prints_hello", f"function:{IT}::cli::no_args", "function:mtest_fixture::tests::greets_world"):
        assert "test" in json.loads(g.node(t)["attrs"])["attributes"], t
    # the macro body's `crate::util::setup(..)` is a call of every generated test; setup runs the binary
    for t in ("prints_hello", "no_args"):
        e = g.edges("CALLS", f"function:{IT}::cli::{t}", f"function:{IT}::util::setup")
        assert e and json.loads(e[0]["attrs"])["via"] == "test macro body", t
    assert g.has("CALLS", f"function:{IT}::util::setup", "function:mtest_fixture::main")
    assert g.has("CALLS", "function:mtest_fixture::tests::greets_world", "function:mtest_fixture::greet")
    # calls in the invocation's own tokens (the closure) belong to the generated test
    assert g.has("CALLS", f"function:{IT}::cli::prints_hello", f"method:{IT}::util::Cli::arg")

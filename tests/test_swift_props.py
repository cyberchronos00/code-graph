"""Swift computed properties, observers and lazy properties as nodes (#72), on tests/swift_props_fixture: the calls
inside a property's body come from `method:<Type>.<name>` (with `accessor: get | set | willSet | didSet` where the
body has separate clauses), and a read of a computed / lazy property or a write of a computed / observed one is a
CALLS edge to it (`property: read | write`) with the receiver rules of method calls. A plain stored property stays
out of the graph. The exact layer (index store) yields the same edges. A Swift / Kotlin type node that calls the
target itself (a stored property's initializer, a Kotlin custom getter) is listed by impact `(in a property)`."""
import json
import shutil
import sqlite3
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.plugins.swift import exact, indexstore  # noqa: E402

FIX = ROOT / "tests" / "swift_props_fixture"
SWIFT = exact.find_swift()
LIB = indexstore.find_lib(SWIFT) if SWIFT else None
needs_toolchain = pytest.mark.skipif(not (SWIFT and LIB), reason="no Swift toolchain with libIndexStore")
_S: dict = {}


def db():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-swift-props-"))
        _S["stats"] = index_project(FIX, d / "g.db", "swift-props")
        _S["db"] = d / "g.db"
    return _S["db"]


def edges(path, kinds=("CALLS", "TEST_CALLS")):
    q = f"select src, dst, kind, line, attrs from edges where kind in ({','.join('?' * len(kinds))})"
    return {(s, d, ln): json.loads(a or "{}") for s, d, k, ln, a in sqlite3.connect(path).execute(q, kinds)}


def nodes(path):
    return {i: json.loads(a or "{}") for i, a in sqlite3.connect(path).execute("select id, attrs from nodes")}


def test_property_nodes():
    n = nodes(db())
    assert n["method:Cart.label"]["swift_kind"] == "computed property"
    assert n["method:Cart.subtitle"]["accessors"] == ["get", "set"]
    assert n["method:Cart.items"]["swift_kind"] == "property observers" and n["method:Cart.items"]["accessors"] == ["didSet"]
    assert n["method:Store.total"]["accessors"] == ["willSet", "didSet"]
    assert n["method:Store.formatter"]["property"] == "lazy"
    assert n["method:Store.current"]["static"] is True
    assert n["method:Int.asPrice"]["property"] == "computed"
    assert "method:Store.name" not in n and "method:Checkout.cart" not in n      # stored: no node
    assert n["method:Store.label"]["swift_kind"] == "method"                      # a method of that name elsewhere


def test_calls_inside_properties_come_from_the_property():
    e = edges(db())
    assert e[("method:Cart.items", "function:formatPrice", 5)] == {"accessor": "didSet"}
    assert e[("method:Cart.items", "method:Cart.log", 5)] == {"accessor": "didSet"}
    assert ("method:Cart.label", "function:formatPrice", 7) in e
    assert e[("method:Cart.subtitle", "function:formatPrice", 9)] == {"accessor": "get"}
    assert e[("method:Cart.subtitle", "method:Cart.log", 10)] == {"accessor": "set"}
    assert e[("method:Store.total", "method:Store.audit", 12)] == {"accessor": "willSet"}
    assert ("method:Store.formatter", "function:formatPrice", 9) in e
    assert not [k for k in e if k[0] in ("class:Cart", "class:Store")]    # nothing left on the type nodes


def test_reads_and_writes_are_calls():
    e = edges(db())
    assert e[("method:Checkout.summary", "method:Cart.label", 18)] == {"property": "read"}          # cart.label
    assert e[("method:Checkout.render", "method:Checkout.summary", 19)] == {"property": "read"}     # implicit self
    assert e[("method:CartTests.testLabel", "method:Cart.label", 5)]["property"] == "read"
    assert e[("method:Store.checkout", "method:Cart.subtitle", 21)] == {"property": "write"}        # setter
    assert e[("method:Store.checkout", "method:Cart.items", 22)] == {"property": "write"}           # observers
    assert e[("method:Store.checkout", "method:Store.total", 23)] == {"property": "write"}          # total += 1
    assert ("method:Store.checkout", "method:Store.total", 24) not in e      # a read of an observed property
    for dst in ("method:Store.formatter", "method:Store.current", "method:Store.banner"):
        assert e[("method:Store.checkout", dst, 25)] == {"property": "read"}
    assert ("method:Store.banner", "method:Int.asPrice", 15) in e            # `total.asPrice`: Int extension
    assert not [k for k in e if k[0] == "method:Store.shadowed"]            # a parameter and a local of that name


def test_impact_and_tests_through_properties():
    st = GraphStore(db())
    res = Q.impact(st, "formatPrice")
    got = {(c["fqn"], c["depth"], Q.caller_label(c)) for c in res["callers"]}
    for c in [("Cart.items", 1, "  (didSet)"), ("Cart.label", 1, ""), ("Cart.subtitle", 1, "  (get)"),
              ("Checkout.summary", 2, ""), ("Checkout.render", 3, "")]:
        assert c in got, (c, got)
    t = Q.tests_covering(st, "formatPrice")
    assert [x["name"] for x in t["transitive"]] == ["testLabel"] and not t["direct"]


def test_kotlin_type_level_calls_count_as_callers(tmp_path):
    pytest.importorskip("tree_sitter_kotlin")
    src = tmp_path / "k" / "src" / "main" / "kotlin"
    src.mkdir(parents=True)
    (src / "Cart.kt").write_text(textwrap.dedent("""\
        package app

        fun formatPrice(c: Int): String = "${c / 100}"

        class Cart {
            val label: String get() = formatPrice(3)
        }
        """))
    index_project(tmp_path / "k", tmp_path / "k.db", "k")
    res = Q.impact(GraphStore(tmp_path / "k.db"), "formatPrice")
    assert [(c["fqn"], Q.caller_label(c)) for c in res["callers"]] == [("app.Cart", "  (in a property)")]


@needs_toolchain
def test_exact_mode_same_edges(tmp_path, monkeypatch):
    for v in ("CODEGRAPH_SWIFT_INDEX_STORE", "CODEGRAPH_NO_CACHE", "CODEGRAPH_LIBINDEXSTORE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("CODEGRAPH_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("CODEGRAPH_SWIFT", SWIFT)
    monkeypatch.setenv("CODEGRAPH_SWIFT_INDEX", "1")
    root = tmp_path / "proj"
    shutil.copytree(FIX, root)
    st = index_project(root, tmp_path / "x.db", "swift-props-exact")
    assert st["plugins"]["swift"]["mode"] == "indexstore"
    heur = {k: {a: v for a, v in x.items() if a != "binding"} for k, x in edges(db()).items()
            if not k[0].startswith("method:CartTests")}
    ex = {k: {a: v for a, v in x.items() if a != "source"} for k, x in edges(tmp_path / "x.db").items()
          if not k[0].startswith("method:CartTests")}
    assert ex == heur

"""Kotlin properties that run code as nodes (#89), on tests/kotlin_props_fixture: a custom `get()` / `set(value)`,
`by lazy { }` or a delegate makes `method:<Type>.<name>` (top level: `function:<package>.<name>`; an extension
property keeps its receiver, as extension functions do) with `kotlin_kind: property`; the calls inside come from it
(`accessor: get | set | lazy | delegate`), and a read (or a write through a setter / delegate) is a CALLS edge with
`property: read | write` on the receiver rules of method calls. Plain stored properties stay out of the graph."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_kotlin")
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "kotlin_props_fixture"
_S: dict = {}


def db():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-kotlin-props-"))
        _S["stats"] = index_project(FIX, d / "g.db", "kotlin-props")
        _S["db"] = d / "g.db"
    return _S["db"]


def edges(kinds=("CALLS", "TEST_CALLS")):
    q = f"select src, dst, kind, line, attrs from edges where kind in ({','.join('?' * len(kinds))})"
    return {(s, d, ln): json.loads(a or "{}") for s, d, k, ln, a in sqlite3.connect(db()).execute(q, kinds)}


def nodes():
    return {i: json.loads(a or "{}") for i, a in sqlite3.connect(db()).execute("select id, attrs from nodes")}


def test_property_nodes():
    n = nodes()
    assert n["method:app.Cart.items"]["accessors"] == ["set"] and n["method:app.Cart.items"]["property"] == "custom"
    assert n["method:app.Cart.label"]["accessors"] == ["get"] and n["method:app.Cart.label"]["kotlin_kind"] == "property"
    assert n["method:app.Cart.cached"]["property"] == "lazy"
    assert n["method:app.Cart.count"]["property"] == "delegated"
    assert n["function:app.asPrice"]["receiver"] == "Int" and n["function:app.banner"]["accessors"] == ["get"]
    assert n["method:app.Checkout.summary"]["accessors"] == ["get"]
    assert "method:app.Checkout.cart" not in n                                    # constructor val: no node
    assert _S["stats"]["plugins"]["kotlin"]["property_nodes"] == 7


def test_calls_inside_properties_and_reads():
    e = edges()
    fp = "function:app.formatPrice"
    for src, ln, acc in (("method:app.Cart.items", 9, "set"), ("method:app.Cart.label", 10, "get"),
                         ("method:app.Cart.cached", 11, "lazy"), ("method:app.Cart.count", 12, "delegate"),
                         ("function:app.asPrice", 16, "get"), ("function:app.banner", 17, "get")):
        assert e[(src, fp, ln)]["accessor"] == acc, src
    assert ("method:app.Cart.items", "method:app.Cart.log", 9) in e and ("method:app.Cart.count", "method:app.Cart.log", 12) in e
    assert e[("method:app.Checkout.summary", "method:app.Cart.label", 20)]["property"] == "read"
    for dst in ("method:app.Checkout.summary", "function:app.banner", "function:app.asPrice"):
        a = e[("method:app.Checkout.render", dst, 21)]
        assert a["property"] == "read" and "binding" not in a, dst                 # bound by type, not by name
    assert e[("method:app.Checkout.reset", "method:app.Cart.items", 22)]["property"] == "write"
    assert not [k for k in e if k[0] in ("class:app.Cart", "file:kotlin:src/main/kotlin/Cart.kt")]


def test_impact_and_tests():
    st = GraphStore(db())
    res = Q.impact(st, "formatPrice")
    d = {c["fqn"]: c["depth"] for c in res["callers"]}
    for fq in ("app.Cart.items", "app.Cart.label", "app.Cart.cached", "app.Cart.count", "app.asPrice", "app.banner"):
        assert d[fq] == 1, fq
    assert d["app.Checkout.summary"] == 2 and d["app.Checkout.render"] == 2      # render reads banner directly
    t = Q.tests_covering(st, "formatPrice")
    assert [x["name"] for x in t["transitive"]] == ["label"] and not t["direct"]

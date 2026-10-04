"""Swift stored properties and construction branches (#88 phase 1), on tests/swift_fields_fixture.

A stored instance property is a `field:<Type>.<name>` node (attrs `property: stored`, `binding: let | var`, `wrapper`
for a property wrapper). `self.x` / a bare `x` inside the type and `v.x` with a known type of `v` (a parameter, a
local built by an initializer) are READS_PROP / WRITES_PROP edges (`receiver`, `accessor`, `storage: wrapper` for
`_x = Wrapper(...)`); an unknown receiver binds nothing. Test code's accesses are TEST_USES (orig READS_PROP /
WRITES_PROP). An INSTANTIATES edge inside a switch case / if / else / guard / ternary carries `branch` and
`branch_line`, in the heuristic and the exact layer. Mutating calls (`items.append(x)`, a project `mutating func`), `&x`
and `$x` (a Binding handed out) are writes with `via`; a key path `\\Type.x` is a read with `via: keypath`;
`@AppStorage("k")` / `@SceneStorage` fields carry `key`. `cg readers` / `cg writers Type.prop` list them. Call edges are
unchanged."""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.plugins.swift import exact, indexstore  # noqa: E402

FIX = ROOT / "tests" / "swift_fields_fixture"
SWIFT = exact.find_swift()
LIB = indexstore.find_lib(SWIFT) if SWIFT else None
needs_toolchain = pytest.mark.skipif(not (SWIFT and LIB), reason="no Swift toolchain with libIndexStore")
_S: dict = {}


def db():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-swift-fields-"))
        _S["stats"] = index_project(FIX, d / "g.db", "swift-fields")
        _S["db"] = d / "g.db"
    return _S["db"]


def edges(path, kinds):
    q = f"select src, dst, kind, line, attrs from edges where kind in ({','.join('?' * len(kinds))})"
    return {(s, d, k, ln): json.loads(a or "{}") for s, d, k, ln, a in sqlite3.connect(path).execute(q, kinds)}


def test_field_nodes():
    n = {i: json.loads(a or "{}") for i, a in sqlite3.connect(db()).execute(
        "select id, attrs from nodes where kind='field'")}
    assert n["field:Store.level"] == {"property": "stored", "binding": "var", "wrapper": "Clamped"}
    assert n["field:Store.limit"]["binding"] == "let" and "wrapper" not in n["field:Store.limit"]
    assert "field:Store.shared" not in n                       # static: a constant (#84), not a field
    assert "field:Store._level" not in n                        # the wrapper's storage is the same node
    st = _S["stats"]["plugins"]["swift"]
    assert st["stored_property_nodes"] == len(n) and st["stored_property_writes"] >= 6


def test_reads_and_writes():
    e = edges(db(), ("READS_PROP", "WRITES_PROP", "TEST_USES"))
    assert e[("method:Store.save", "field:Store.level", "WRITES_PROP", 22)] == {"receiver": "self"}
    assert ("method:Store.save", "field:Store.level", "READS_PROP", 23) in e
    assert e[("method:Store.save", "field:Store.history", "WRITES_PROP", 23)]["via"] == "mutating"   # `.append`
    assert e[("method:Store.init", "field:Store.level", "WRITES_PROP", 18)]["storage"] == "wrapper"
    assert e[("method:Clamped.wrappedValue", "field:Clamped.v", "WRITES_PROP", 7)]["accessor"] == "set"
    assert e[("method:Picker.init", "field:Store.level", "READS_PROP", 33)]["receiver"] == "store"    # a parameter
    assert ("method:Picker.reset", "field:Store.level", "WRITES_PROP", 38) in e                        # a local
    assert ("method:Picker.reset", "field:Store.limit", "READS_PROP", 39) in e
    t = e[("method:StoreTests.testSave", "field:Store.level", "TEST_USES", 8)]
    assert t["orig"] == "READS_PROP"
    assert not any(k[0] == "method:StoreTests.testSave" and k[2] != "TEST_USES" for k in e)


def test_mutations_bindings_keypaths():
    e = edges(db(), ("READS_PROP", "WRITES_PROP"))
    w = lambda ln, f: e[("method:Basket.add", f, "WRITES_PROP", ln)]["via"]  # noqa: E731
    assert w(9, "field:Basket.items") == "mutating" and w(10, "field:Basket.tags") == "mutating"
    assert w(11, "field:Basket.open") == "mutating" and w(12, "field:Basket.items") == "inout"
    assert "via" not in e[("method:Basket.add", "field:Basket.items", "READS_PROP", 13)]     # `items.count`
    b = "method:BasketView.body"
    assert e[(b, "field:BasketView.compact", "WRITES_PROP", 26)]["via"] == "binding"           # `$compact`
    assert e[(b, "field:BasketView.shown", "WRITES_PROP", 27)]["via"] == "binding"
    assert (b, "field:BasketView.compact", "READS_PROP", 28) in e
    assert e[(b, "field:BasketView.basket", "WRITES_PROP", 29)]["via"] == "mutating"           # a project mutating func
    assert e[(b, "field:Basket.items", "READS_PROP", 31)] == {"receiver": "\\Basket", "via": "keypath"}
    assert not any(k[3] == 30 and k[2] == "WRITES_PROP" for k in e)                            # `\\.self`, a read
    f = dict(sqlite3.connect(db()).execute("select id, attrs from nodes where id = 'field:BasketView.compact'"))
    assert json.loads(f["field:BasketView.compact"]) == {"property": "stored", "binding": "var", "wrapper": "AppStorage",
                                                         "key": "compact"}


def test_subscript_writes_and_inferred_keypaths():
    """`slots[0] = v` / `self.labels[k] = v` write with `via: item`; `slots[0]` reads; `\\.labels` (an inferred root)
    binds the one type with a stored `labels`, heuristic, `binding: name`."""
    e = edges(db(), ("READS_PROP", "WRITES_PROP"))
    f = "method:Shelf.fill"
    assert e[(f, "field:Shelf.slots", "WRITES_PROP", 44)] == {"receiver": "self", "via": "item"}
    assert e[(f, "field:Shelf.labels", "WRITES_PROP", 45)] == {"receiver": "self", "via": "item"}
    assert (f, "field:Shelf.slots", "READS_PROP", 46) in e and (f, "field:Shelf.slots", "WRITES_PROP", 46) not in e
    assert e[(f, "field:Shelf.labels", "READS_PROP", 48)] == {"receiver": "\\", "via": "keypath", "binding": "name"}
    c = sqlite3.connect(db()).execute("select confidence from edges where src=? and line=48 and kind='READS_PROP'",
                                      (f,)).fetchone()
    assert c[0] == "heuristic"


def test_construction_branches():
    e = edges(db(), ("INSTANTIATES",))
    assert e[("function:screen", "class:Picker", "INSTANTIATES", 50)] == {"branch": "case .settings", "branch_line": 49}
    assert e[("function:screen", "class:About", "INSTANTIATES", 54)]["branch"] == "if editing"
    assert e[("function:screen", "class:Picker", "INSTANTIATES", 54)]["branch"] == "else of if editing"
    assert "branch" not in e[("method:Picker.reset", "class:Store", "INSTANTIATES", 37)]


def test_readers_writers_queries():
    st = GraphStore(db())
    w = Q.writers(st, "Store.level")
    assert {(r["src"], r["line"]) for r in w} == {("method:Store.init", 18), ("method:Store.save", 22),
                                                  ("method:Store.save", 24), ("method:Picker.reset", 38)}
    r = Q.readers(st, "Store.level")
    assert [x["src"] for x in r if x["test"]] == ["method:StoreTests.testSave"]
    assert {x["src"] for x in r if not x["test"]} == {"method:Store.save", "method:Picker.init"}
    assert Q.writers(st, "field:Store.limit") == [] and Q.readers(st, "Store.limit")
    assert "no " in Q.explain_no_writers(st, "Store.limit", "writers")
    out = subprocess.run([sys.executable, "-m", "codegraph.cli", "readers", "Store.level", "--db", str(db())],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert "(1 from test code)" in out and "receiver=store" in out


def test_call_edges_unchanged():
    """The stored-property pass adds field nodes and READS/WRITES_PROP edges, never a call edge to a field."""
    e = edges(db(), ("CALLS", "TEST_CALLS"))
    assert not any(k[1].startswith("field:") for k in e)


@needs_toolchain
def test_exact_mode_keeps_branches_and_fields(tmp_path, monkeypatch):
    for v in ("CODEGRAPH_SWIFT_INDEX_STORE", "CODEGRAPH_NO_CACHE", "CODEGRAPH_LIBINDEXSTORE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("CODEGRAPH_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("CODEGRAPH_SWIFT", SWIFT)
    monkeypatch.setenv("CODEGRAPH_SWIFT_INDEX", "1")
    root = tmp_path / "proj"
    shutil.copytree(FIX, root)
    st = index_project(root, tmp_path / "x.db", "swift-fields-exact")
    assert st["plugins"]["swift"]["mode"] == "indexstore"
    e = edges(tmp_path / "x.db", ("INSTANTIATES",))
    assert e[("function:screen", "class:Picker", "INSTANTIATES", 50)]["branch"] == "case .settings"
    assert e[("function:screen", "class:About", "INSTANTIATES", 54)]["branch"] == "if editing"
    h = edges(db(), ("READS_PROP", "WRITES_PROP"))
    assert edges(tmp_path / "x.db", ("READS_PROP", "WRITES_PROP")) == h

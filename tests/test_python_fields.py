"""Python stored attributes (#88 phase 1), on tests/python_fields_fixture.

`self.x = ...` in a method and an annotated class-level attribute (a dataclass field; not `ClassVar`) are
`field:<Class>.<attr>` nodes (`property: stored`, `declared: self | class`). Accesses through a receiver whose type the
inference knows are READS_PROP / WRITES_PROP (`receiver`; `via: mutating` for `self.items.append(x)`); properties,
methods and plain class constants are never fields. Test code's accesses are TEST_USES. Call edges are unchanged."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "python_fields_fixture"
_S: dict = {}
C = "fieldshop.cart."


def db():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-python-fields-"))
        _S["stats"] = index_project(FIX, d / "g.db", "python-fields")
        _S["db"] = d / "g.db"
    return _S["db"]


def edges(kinds):
    q = f"select src, dst, kind, line, attrs from edges where kind in ({','.join('?' * len(kinds))})"
    return {(s, d, k, ln): json.loads(a or "{}") for s, d, k, ln, a in sqlite3.connect(db()).execute(q, kinds)}


def test_field_nodes():
    n = {i: json.loads(a or "{}") for i, a in sqlite3.connect(db()).execute("select id, attrs from nodes where kind='field'")}
    assert set(n) == {f"field:{C}{x}" for x in ("Item.name", "Item.qty", "Cart.owner", "Cart.items", "Cart.total",
                                                  "Registry.cache")}
    assert n[f"field:{C}Item.qty"]["declared"] == "class" and n[f"field:{C}Cart.items"]["declared"] == "self"


def test_reads_and_writes():
    e = edges(("READS_PROP", "WRITES_PROP", "TEST_USES"))
    add, co = f"method:{C}Cart.add", f"function:{C}checkout"
    assert e[(f"method:{C}Cart.__init__", f"field:{C}Cart.owner", "WRITES_PROP", 16)] == {"receiver": "self"}
    assert e[(add, f"field:{C}Cart.items", "WRITES_PROP", 25)] == {"receiver": "self", "via": "mutating"}
    assert (add, f"field:{C}Cart.total", "WRITES_PROP", 26) in e                       # +=
    assert e[(add, f"field:{C}Item.qty", "READS_PROP", 26)] == {"receiver": "it"}       # a parameter annotation
    assert (add, f"field:{C}Item.qty", "WRITES_PROP", 27) in e
    assert "via" not in e[(add, f"field:{C}Cart.items", "READS_PROP", 28)]             # `.count()` reads
    assert (f"method:{C}Cart.size", f"field:{C}Cart.items", "READS_PROP", 22) in e      # inside a property
    assert e[(co, f"field:{C}Cart.items", "WRITES_PROP", 39)] == {"receiver": "d", "via": "mutating"}   # a local
    assert (co, f"field:{C}Cart.total", "WRITES_PROP", 40) in e                         # del
    assert e[(f"method:{C}Registry.put", f"field:{C}Registry.cache", "WRITES_PROP", 50)]["via"] == "item"
    assert (f"method:{C}Registry.put", f"field:{C}Registry.cache", "READS_PROP", 51) in e
    t = [k for k in e if k[2] == "TEST_USES" and k[1] == f"field:{C}Cart.owner"]
    assert t and e[t[0]]["orig"] == "READS_PROP"
    st = _S["stats"]["plugins"]["python"]
    assert st["stored_attribute_nodes"] == 6 and st["stored_attribute_writes_mutating"] == 2


def test_queries_and_calls():
    st = GraphStore(db())
    assert {(r["src"], r["line"]) for r in Q.writers(st, "Cart.total")} == {
        (f"method:{C}Cart.__init__", 18), (f"method:{C}Cart.add", 26), (f"function:{C}checkout", 36),
        (f"function:{C}checkout", 40)}
    assert not any(k[1].startswith("field:") for k in edges(("CALLS", "TEST_CALLS")))

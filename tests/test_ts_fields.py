"""TypeScript stored class fields (#88 phase 1), on tests/ts_fields_fixture.

A non-static class property that is not a function (`items: Item[] = []`, `#secret`) and a constructor parameter
property (`constructor(private api: Api)`) are `field:<file>#<Class>.<name>` nodes (`property: stored`, `declared:
class | constructor`, `readonly`). A property access the type checker resolves to one is READS_PROP / WRITES_PROP
(`exact`, `receiver`): a write is an assignment / compound assignment / `++` / `delete` target, an item assignment
(`this.cache[k] = v`, `via: item`) or an in-place array / Map / Set method (`this.items.push(x)`, `via: mutating`)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

EXTRACTOR_DEPS = ROOT / "codegraph" / "plugins" / "ts" / "extractor" / "node_modules" / "typescript"
pytestmark = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")
FIX = ROOT / "tests" / "ts_fields_fixture"
F = "src/cart.ts#"
_S: dict = {}


def db():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-ts-fields-"))
        _S["stats"] = index_project(FIX, d / "g.db", "ts-fields")
        _S["db"] = d / "g.db"
    return _S["db"]


def edges(kinds):
    q = f"select src, dst, kind, line, attrs from edges where kind in ({','.join('?' * len(kinds))})"
    return {(s, d, k, ln): json.loads(a or "{}") for s, d, k, ln, a in sqlite3.connect(db()).execute(q, kinds)}


def test_field_nodes():
    n = {i: json.loads(a or "{}") for i, a in sqlite3.connect(db()).execute("select id, attrs from nodes where kind='field'")}
    n = {i: a for i, a in n.items() if a.get("property") == "stored"}
    assert set(n) == {f"field:{F}{x}" for x in ("Item.name", "Item.qty", "Cart.items", "Cart.total", "Cart.#secret",
                                                "Registry.cache")}    # not LIMIT (static), onChange (a function), label
    assert n[f"field:{F}Item.qty"]["declared"] == "constructor" and n[f"field:{F}Item.qty"]["readonly"] is True
    assert n[f"field:{F}Cart.items"]["declared"] == "class"


def test_reads_and_writes():
    e = edges(("READS_PROP", "WRITES_PROP"))
    add, co = f"method:{F}Cart.add", f"function:{F}checkout"
    assert e[(add, f"field:{F}Cart.items", "WRITES_PROP", 14)] == {"receiver": "this", "via": "mutating"}
    assert (add, f"field:{F}Cart.total", "WRITES_PROP", 15) in e                        # +=
    assert "via" not in e[(add, f"field:{F}Cart.items", "READS_PROP", 16)]              # .length
    assert (add, f"field:{F}Cart.#secret", "READS_PROP", 17) in e
    assert (f"method:{F}Cart.onChange", f"field:{F}Cart.total", "WRITES_PROP", 11) in e  # ++ in an arrow property
    assert e[(co, f"field:{F}Cart.total", "WRITES_PROP", 22)] == {"receiver": "c"}
    assert e[(co, f"field:{F}Cart.items", "WRITES_PROP", 25)]["via"] == "mutating"       # a local `new Cart()`
    assert (co, f"field:{F}Item.name", "READS_PROP", 23) in e
    put = f"method:{F}Registry.put"
    assert e[(put, f"field:{F}Registry.cache", "WRITES_PROP", 32)]["via"] == "item"
    assert (put, f"field:{F}Registry.cache", "READS_PROP", 33) in e
    assert all(v for v in e)
    st = _S["stats"]["plugins"]["typescript"]
    assert st["stored_field_nodes"] == 6


def test_react_and_vue_state():
    """`const [count, setCount] = useState(0)` and Vue `ref()` / `reactive()` are `property: state` field nodes of the
    component / composable; `setCount(...)` / `total.value++` / `state.open = true` / `state.items.push()` write."""
    n = {i: json.loads(a or "{}") for i, a in sqlite3.connect(db()).execute(
        "select id, attrs from nodes where kind='field' and attrs like '%state%'")}
    assert {i: a["hook"] for i, a in n.items() if a.get("property") == "state"} == {
        "field:src/Counter.tsx#Counter.count": "useState", "field:src/Counter.tsx#Counter.open": "useState",
        "field:src/useCart.ts#useCart.total": "ref", "field:src/useCart.ts#useCart.state": "reactive"}
    e = edges(("READS_PROP", "WRITES_PROP"))
    inc, comp = "function:src/Counter.tsx#Counter.inc", "function:src/Counter.tsx#Counter"
    assert e[(inc, "field:src/Counter.tsx#Counter.count", "WRITES_PROP", 6)] == {"via": "setter"}
    assert (inc, "field:src/Counter.tsx#Counter.count", "READS_PROP", 6) in e
    assert (comp, "field:src/Counter.tsx#Counter.open", "WRITES_PROP", 7) in e         # setOpen in a JSX handler
    assert (comp, "field:src/Counter.tsx#Counter.count", "READS_PROP", 7) in e
    add = "function:src/useCart.ts#useCart.add"
    assert e[(add, "field:src/useCart.ts#useCart.total", "WRITES_PROP", 7)] == {"via": "value"}
    assert e[(add, "field:src/useCart.ts#useCart.state", "WRITES_PROP", 8)] == {"via": "property"}
    assert e[(add, "field:src/useCart.ts#useCart.state", "WRITES_PROP", 9)] == {"via": "mutating"}
    assert (add, "field:src/useCart.ts#useCart.total", "READS_PROP", 10) in e


def test_queries_and_calls():
    st = GraphStore(db())
    assert {(r["src"], r["line"]) for r in Q.writers(st, "Cart.total")} == {
        (f"method:{F}Cart.onChange", 11), (f"method:{F}Cart.add", 15), (f"function:{F}checkout", 22)}
    assert not any(k[1].startswith("field:") for k in edges(("CALLS", "TEST_CALLS")))

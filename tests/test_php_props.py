"""PHP class properties (#88 phase 1), on tests/php_props_fixture.

A declared instance property or a constructor-promoted one is a `property:Class::$x` node with `property: stored`
(`promoted: true` for a promoted one); a docblock `@property` (Laravel's magic attributes) and a static property are
not. `$this->x` binds exactly, `$v->x` with a known type of `$v` binds resolved, an unknown receiver binds nothing.
`= v` is a WRITES_PROP; `+=` / `++` (`via: compound`), `[]=` / `unset($this->x[k])` (`via: item`) and `unset()`
(`via: unset`) are writes with `via`, plus the read the walk records. `cg readers / writers Class.x` list them."""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytestmark = pytest.mark.skipif(not shutil.which("php"), reason="php not installed")
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "php_props_fixture"
_S: dict = {}


def db():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-php-props-"))
        _S["stats"] = index_project(FIX, d / "g.db", "php-props")
        _S["db"] = d / "g.db"
    return _S["db"]


def edges():
    q = "select src, dst, kind, line, confidence, attrs from edges where kind in ('READS_PROP', 'WRITES_PROP')"
    return {(s, d, k, ln): (c, json.loads(a or "{}")) for s, d, k, ln, c, a in sqlite3.connect(db()).execute(q)}


def test_property_nodes():
    n = {i: json.loads(a or "{}") for i, a in sqlite3.connect(db()).execute("select id, attrs from nodes where kind='property'")}
    assert n["property:Fx\\Cart::$items"]["property"] == "stored"
    assert n["property:Fx\\Cart::$owner"]["promoted"] is True and n["property:Fx\\Cart::$pricer"]["property"] == "stored"
    assert "property" not in n["property:Fx\\Cart::$count"]                 # static


def test_this_reads_and_writes():
    e = edges()
    add, cart = "method:Fx\\Cart::add", "property:Fx\\Cart::$"
    assert e[(add, cart + "items", "WRITES_PROP", 17)] == ("exact", {"receiver": "this", "via": "item"})
    assert e[(add, cart + "total", "WRITES_PROP", 18)][1]["via"] == "compound"
    assert (add, cart + "total", "READS_PROP", 18) in e and (add, cart + "pricer", "READS_PROP", 18) in e
    assert e[(add, cart + "owner", "WRITES_PROP", 19)] == ("exact", {"receiver": "this"})        # promoted
    assert e[(add, cart + "items", "WRITES_PROP", 20)][1]["via"] == "item"                       # unset($this->items[0])
    assert ("method:Fx\\Cart::label", cart + "owner", "READS_PROP", 25) in e


def test_typed_receivers():
    e = edges()
    f, rate = "function:Fx\\checkout", "property:Fx\\Pricer::$rate"
    assert e[(f, rate, "WRITES_PROP", 41)] == ("resolved", {"receiver": "$p"})                   # a typed parameter
    assert e[(f, rate, "WRITES_PROP", 42)][1]["via"] == "compound"                               # `$p->rate++`
    assert (f, rate, "WRITES_PROP", 44) in e                                                     # `new Pricer()`
    assert (f, rate, "WRITES_PROP", 46) not in e                                                 # unknown receiver
    assert (f, "property:Fx\\Cart::$owner", "READS_PROP", 47) in e
    assert _S["stats"]["plugins"]["php"]["property_refs_unresolved"] >= 1


def test_readers_writers():
    st = GraphStore(db())
    assert [r["line"] for r in Q.writers(st, "Pricer.rate")] == [41, 42, 44]
    assert sorted(r["line"] for r in Q.readers(st, "Cart::$owner")) == [25, 47]
    assert Q.readers(st, "Fx\\Cart::$owner") == Q.readers(st, "Cart.owner")

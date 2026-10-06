"""#88 phase 1, TS/JS side: plain JS `this.x = …` fields, JSX / `new X` construction branches
(tests/js_fields_fixture), Pinia options stores and Vue Options API `data()` (tests/vue_options_fixture).

A `.js` class's `this.x = v` (x undeclared) is a `field:<file>#Class.x` (`declared: this`); constructor writes come
from the class node. A RENDERS / INSTANTIATES edge inside a switch case / if / else / ternary / `&&` carries `branch`
and `branch_line`. `defineStore('id', {state: () => ({...})})` keys are `field:<file>#<store>.<key>` (`hook: pinia`),
an Options API `data()` key is `field:<file.vue>#<key>` (`hook: data`); `this.x` in actions / methods, `s.x` with
`const s = useStore()` and `useStore().x` read and write them."""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
from cg_code_graph.indexer import index_project  # noqa: E402

_S: dict = {}


def db(name):
    if name not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-ts88-"))
        index_project(ROOT / "tests" / name, d / "g.db", name)
        _S[name] = d / "g.db"
    return _S[name]


def edges(name, kinds):
    q = f"select src, dst, kind, line, attrs from edges where kind in ({','.join('?' * len(kinds))})"
    return {(s, d, k, ln): json.loads(a or "{}") for s, d, k, ln, a in sqlite3.connect(db(name)).execute(q, kinds)}


def nodes(name):
    return {i: json.loads(a or "{}") for i, a in sqlite3.connect(db(name)).execute(
        "select id, attrs from nodes where kind='field'")}


def test_plain_js_this_fields():
    n = nodes("js_fields_fixture")
    c = "field:src/cmd.js#Command."
    assert set(n) == {c + "name", c + "options", c + "_done"}
    assert n[c + "name"]["declared"] == "this"
    e = edges("js_fields_fixture", ("READS_PROP", "WRITES_PROP"))
    assert e[("class:src/cmd.js#Command", c + "name", "WRITES_PROP", 3)] == {"receiver": "this"}
    assert e[("method:src/cmd.js#Command.option", c + "options", "WRITES_PROP", 9)]["via"] == "mutating"
    assert ("method:src/cmd.js#Command.option", c + "name", "READS_PROP", 11) in e


def test_jsx_and_new_branches():
    e = edges("js_fields_fixture", ("RENDERS", "INSTANTIATES"))
    app, p, em = "function:src/App.jsx#App", "function:src/App.jsx#Panel", "function:src/App.jsx#Empty"
    assert e[(app, p, "RENDERS", 9)]["branch"] == "case 'x'" and e[(app, p, "RENDERS", 9)]["branch_line"] == 8
    assert e[(app, p, "RENDERS", 11)]["branch"] == "user ?"
    assert e[(app, em, "RENDERS", 11)]["branch"] == "else of user ?"
    mk, cmd = "function:src/cmd.js#make", "class:src/cmd.js#Command"
    assert e[(mk, cmd, "INSTANTIATES", 17)] == {"branch": "if (kind === 'a')", "branch_line": 16}
    assert e[(mk, cmd, "INSTANTIATES", 19)]["branch"] == "kind ?"


def test_pinia_options_store_and_vue_data():
    n = nodes("vue_options_fixture")
    s, v = "field:src/stores/cart.ts#useCartStore.", "field:src/components/Counter.vue#"
    assert n[s + "items"] == {"property": "state", "hook": "pinia", "parent": "store:src/stores/cart.ts#useCartStore"}
    assert n[v + "count"]["hook"] == "data" and v + "label" in n
    e = edges("vue_options_fixture", ("READS_PROP", "WRITES_PROP"))
    add, comp = "function:src/stores/cart.ts#useCartStore.add", "component:src/components/Counter.vue"
    assert e[(add, s + "items", "WRITES_PROP", 7)]["via"] == "mutating"
    assert (add, s + "total", "WRITES_PROP", 8) in e and (add, s + "total", "READS_PROP", 9) in e
    assert e[(comp, v + "count", "WRITES_PROP", 14)] == {"receiver": "this"}
    assert e[(comp, s + "total", "WRITES_PROP", 16)] == {"receiver": "cart"}
    assert (comp, s + "items", "READS_PROP", 17) in e
    assert e[(comp, s + "total", "READS_PROP", 17)] == {"receiver": "useCartStore()"}

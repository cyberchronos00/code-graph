"""Kotlin stored properties (#88 phase 1), on tests/kotlin_fields_fixture.

A stored property of a class / enum (a body `val` / `var` without accessors or a delegate, or a constructor `val` /
`var` parameter) is a `field:<Type>.<name>` node (attrs `property: stored`, `binding`). `x` / `this.x` inside the
class and `v.x` where the type of `v` is known (a parameter, a property, a local `val v = T(...)`) are READS_PROP /
WRITES_PROP edges; `items.add(x)` is a write with `via: mutating`, `_state.value = x` one with `via: value`. An object's
`val`s stay constants (#84); a plain constructor parameter is not a field. Call edges are unchanged."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "kotlin_fields_fixture"
_S: dict = {}


def db():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-kotlin-fields-"))
        _S["stats"] = index_project(FIX, d / "g.db", "kotlin-fields")
        _S["db"] = d / "g.db"
    return _S["db"]


def edges(kinds):
    q = f"select src, dst, kind, line, attrs from edges where kind in ({','.join('?' * len(kinds))})"
    return {(s, d, k, ln): json.loads(a or "{}") for s, d, k, ln, a in sqlite3.connect(db()).execute(q, kinds)}


def test_field_nodes():
    n = {i: json.loads(a or "{}") for i, a in sqlite3.connect(db()).execute("select id, attrs from nodes where kind='field'")}
    assert set(n) == {"field:app.Cart.owner", "field:app.Cart.total", "field:app.Cart.items", "field:app.Cart.tags",
                      "field:app.Vm._state", "field:app.UiState.loading", "field:app.UiState.title", "field:app.Screen.ui", "field:app.Banner.text"}                      # not `note` (no val / var), not `label`, not Config.limit
    assert n["field:app.Cart.owner"]["binding"] == "val" and n["field:app.Cart.total"]["binding"] == "var"


def test_reads_and_writes():
    e = edges(("READS_PROP", "WRITES_PROP"))
    c = "method:app.Cart.add"
    assert e[(c, "field:app.Cart.items", "WRITES_PROP", 11)] == {"receiver": "this", "via": "mutating"}
    assert e[(c, "field:app.Cart.tags", "WRITES_PROP", 12)]["via"] == "mutating"           # this.tags.add
    assert (c, "field:app.Cart.total", "WRITES_PROP", 13) in e                              # +=
    assert (c, "field:app.Cart.total", "WRITES_PROP", 14) in e and (c, "field:app.Cart.total", "READS_PROP", 14) in e
    assert "via" not in e[(c, "field:app.Cart.items", "READS_PROP", 15)]                    # items.size
    assert e[("method:app.Shop.reset", "field:app.Cart.total", "WRITES_PROP", 21)] == {"receiver": "c"}   # a parameter
    assert ("method:app.Shop.reset", "field:app.Cart.owner", "READS_PROP", 22) in e
    assert e[("method:app.Shop.reset", "field:app.Cart.items", "WRITES_PROP", 24)]["receiver"] == "d"     # a local
    assert e[("method:app.Vm.bump", "field:app.Vm._state", "WRITES_PROP", 32)]["via"] == "value"
    assert ("method:app.Cart.label", "field:app.Cart.owner", "READS_PROP", 7) in e          # inside a custom getter
    st = _S["stats"]["plugins"]["kotlin"]
    assert st["stored_property_nodes"] == 9


def test_data_class_copy_writes():
    """`s.copy(loading = true)` writes UiState.loading (`via: copy`); `it.copy(title = ...)` with an unknown receiver
    binds the one data class with every named field, `heuristic`, `binding: name`."""
    e = edges(("READS_PROP", "WRITES_PROP"))
    ld = "method:app.Screen.load"
    assert e[(ld, "field:app.UiState.loading", "WRITES_PROP", 39)] == {"receiver": "s", "via": "copy"}
    assert e[(ld, "field:app.UiState.title", "WRITES_PROP", 40)] == {"receiver": "it", "via": "copy", "binding": "name"}
    assert (ld, "field:app.Screen.ui", "WRITES_PROP", 39) in e        # `ui = …` opening the body is not a local


def test_call_edges_never_reach_fields():
    assert not any(k[1].startswith("field:") for k in edges(("CALLS", "TEST_CALLS")))


def test_readers_writers_queries():
    from cg_code_graph import query as Q
    from cg_code_graph.core.store import GraphStore
    st = GraphStore(db())
    assert {(r["src"], r["line"]) for r in Q.writers(st, "Cart.total")} == {
        ("method:app.Cart.add", 13), ("method:app.Cart.add", 14), ("method:app.Shop.reset", 21)}
    assert {r["src"] for r in Q.readers(st, "Cart.owner")} == {"method:app.Cart.label", "method:app.Shop.reset"}


def test_compose_construction_branches():
    """A composable call or a construction inside `if` / `else` / a `when` entry carries attrs.branch / branch_line."""
    e = edges(("CALLS", "INSTANTIATES"))
    h = "function:app.Home"
    assert e[(h, "function:app.Spinner", "CALLS", 16)] == {"branch": "if (loading)", "branch_line": 15}
    assert e[(h, "function:app.Feed", "CALLS", 18)]["branch"] == "else of if (loading)"
    assert e[(h, "class:app.Banner", "INSTANTIATES", 21)] == {"branch": "when 1", "branch_line": 21}
    assert e[(h, "function:app.Feed", "CALLS", 22)]["branch"] == "else of when (tab)"
    assert "branch" not in e[(h, "function:app.Spinner", "CALLS", 24)]


def test_copy_guesses_are_heuristic_in_db_and_viz():
    """A name-bound `copy()` write is `heuristic` (never exact) in the edge confidence and in the web view's graph
    data, which the legend counts and styles (dotted) by `confidence`; a known receiver is `resolved`."""
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph.viz import graph as G
    rows = sqlite3.connect(db()).execute(
        "select line, confidence, attrs from edges where kind='WRITES_PROP' and attrs like '%\"via\": \"copy\"%'").fetchall()
    conf = {ln: c for ln, c, a in rows}
    assert conf == {39: "resolved", 40: "heuristic"}
    assert all(("binding" in json.loads(a)) == (c == "heuristic") for _, c, a in rows)
    d = G.build(GraphStore(db()), "reaches", ["field:app.UiState.title"])
    (e,) = [x for x in d["edges"] if x["kind"] == "WRITES_PROP"]
    assert e["confidence"] == "heuristic"

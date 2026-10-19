"""Exact-layer Kotlin property edges (#105) on an invented booth app.

scip-java reports `Cart#label.` together with `getLabel().` / `setItems().` at the property's spelling.
Exact mode maps that spelling onto the property node and drops the heuristic accessor call. A declared
`fun getItems()` stays a call. Java `getLabel` / `setItems` use the same nodes.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_kotlin")
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "kotlin_prop_exact_fixture"


def _pos(rel: str, needle: str, nth: int = 1) -> tuple[int, int]:
    text = (FIX / rel).read_text()
    start = -1
    for _ in range(nth):
        start = text.find(needle, start + 1)
    assert start >= 0, needle
    line = text.count("\n", 0, start)
    col = start - text.rfind("\n", 0, start) - 1
    return line, col


def _occ(doc, rel, needle, symbol, definition, nth=1):
    sys.path.insert(0, str(ROOT / "cg_code_graph" / "plugins" / "scip"))
    import scip_pb2
    line, col = _pos(rel, needle, nth)
    token = needle.split(".")[-1].split()[0].rstrip("()")
    if "." in needle:
        col += needle.rfind(".") + 1
    o = doc.occurrences.add()
    o.range.extend([line, col, line, col + len(token)])
    o.symbol = symbol
    if definition:
        o.symbol_roles = scip_pb2.SymbolRole.Definition


def _index_bytes(path: Path) -> None:
    sys.path.insert(0, str(ROOT / "cg_code_graph" / "plugins" / "scip"))
    import scip_pb2
    idx = scip_pb2.Index()
    idx.metadata.tool_info.name = "scip-java"
    idx.metadata.tool_info.version = "synthetic"
    kt = idx.documents.add()
    kt.relative_path = "src/main/kotlin/booth/Cart.kt"
    kt.language = "kotlin"
    P = "semanticdb maven . . booth/"
    specs = [
        ("Cart", P + "Cart#", True, 1),
        ("read", P + "Cart#read().", True, 1),
        ("bump", P + "Cart#bump().", True, 1),
        ("replace", P + "Cart#replace().", True, 1),
        ("getItems", P + "Cart#getItems().", True, 1),
        ("show", P + "show().", True, 1),
        ("plain", P + "plain().", True, 1),
        ("label", P + "Cart#label.", False, 2),          # `fun read(): String = label`
        ("label", P + "Cart#getLabel().", False, 2),
        ("items +=", P + "Cart#items.", False, 1),
        ("items +=", P + "Cart#getItems().", False, 1),
        ("items +=", P + "Cart#setItems().", False, 1),
        ("items =", P + "Cart#items.", False, 1),
        ("items =", P + "Cart#setItems().", False, 1),
        ("c.label", P + "Cart#label.", False, 1),
        ("c.label", P + "Cart#getLabel().", False, 1),
        ("c.getItems()", P + "Cart#getItems().", False, 1),
    ]
    for needle, sym, definition, nth in specs:
        _occ(kt, kt.relative_path, needle, sym, definition, nth)
    java = idx.documents.add()
    java.relative_path = "src/main/java/booth/FromJava.java"
    java.language = "java"
    for needle, sym, definition in (
        ("read", P + "FromJava#read().", True),
        ("write", P + "FromJava#write().", True),
        ("getLabel", P + "Cart#getLabel().", False),
        ("setItems", P + "Cart#setItems().", False),
    ):
        _occ(java, java.relative_path, needle, sym, definition)
    path.write_bytes(idx.SerializeToString())


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("CG_KOTLIN_SCIP", raising=False)
    monkeypatch.delenv("CG_KOTLIN_SCIP_FILE", raising=False)
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _index(tmp_path, scip=None):
    db = tmp_path / "g.db"
    st = index_project(FIX, db, "kotlin-prop-exact", scip=scip or [])
    return st, sqlite3.connect(db)


def _edges(con, kind):
    return {(s, d, ln): (c, a) for s, d, ln, c, a in con.execute(
        "select src, dst, line, confidence, attrs from edges where kind=?", (kind,))}


def test_heuristic_java_reads_custom_and_stored_properties(tmp_path, isolated):
    _st, con = _index(tmp_path)
    calls = _edges(con, "CALLS")
    assert calls[("method:booth.FromJava.read", "method:booth.Cart.label", 4)][0] == "heuristic"
    assert '"property": "read"' in calls[("method:booth.FromJava.read", "method:booth.Cart.label", 4)][1]
    writes = _edges(con, "WRITES_PROP")
    assert writes[("method:booth.FromJava.write", "field:booth.Cart.items", 6)][0] == "resolved"


def test_exact_property_edges_and_getter_clash(tmp_path, isolated):
    scip = tmp_path / "index.scip"
    _index_bytes(scip)
    st, con = _index(tmp_path, scip=[str(scip)])
    assert st["plugins"]["kotlin"]["mode"] == "scip"
    assert st["plugins"]["kotlin"]["scip_property_edges"] >= 4
    calls = _edges(con, "CALLS")
    label = calls[("method:booth.Cart.read", "method:booth.Cart.label", 9)]
    assert label[0] == "exact" and '"property": "read"' in label[1]
    shown = calls[("function:booth.show", "method:booth.Cart.label", 18)]
    assert shown[0] == "exact" and '"property": "read"' in shown[1]
    bump = calls.get(("method:booth.Cart.bump", "field:booth.Cart.items", 11))
    assert bump is None
    reads = _edges(con, "READS_PROP")
    writes = _edges(con, "WRITES_PROP")
    assert reads[("method:booth.Cart.bump", "field:booth.Cart.items", 11)][0] == "exact"
    assert writes[("method:booth.Cart.bump", "field:booth.Cart.items", 11)][0] == "exact"
    assert writes[("method:booth.Cart.replace", "field:booth.Cart.items", 13)][0] == "exact"
    assert ("method:booth.Cart.replace", "field:booth.Cart.items", 13) not in reads
    plain = calls[("function:booth.plain", "method:booth.Cart.getItems", 20)]
    assert plain[0] == "exact" and "property" not in plain[1]
    assert ("function:booth.plain", "field:booth.Cart.items", 20) not in reads
    java = calls[("method:booth.FromJava.read", "method:booth.Cart.label", 4)]
    assert java[0] == "exact" and '"property": "read"' in java[1]
    assert writes[("method:booth.FromJava.write", "field:booth.Cart.items", 6)][0] == "exact"
    heur = [k for k, (c, a) in calls.items() if c == "heuristic" and '"property"' in (a or "")]
    assert heur == []

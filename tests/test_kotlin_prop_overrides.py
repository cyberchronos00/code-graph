"""Abstract and interface Kotlin properties (#105).

An interface or abstract `val` with no accessor becomes a property node when an override has one
(`property: abstract`). The override is IMPLEMENTED_BY from an interface and OVERRIDDEN_BY from an
abstract or open property. A stored `override val` in a constructor does not invent that edge.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_kotlin")
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "kotlin_prop_override_fixture"
_S: dict = {}


def db():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-kotlin-prop-override-"))
        _S["stats"] = index_project(FIX, d / "g.db", "kotlin-prop-override")
        _S["db"] = d / "g.db"
    return _S["db"]


def nodes():
    return {i: json.loads(a or "{}") for i, a in sqlite3.connect(db()).execute("select id, attrs from nodes")}


def edges(kind):
    return {(s, d) for s, d in sqlite3.connect(db()).execute(
        "select src, dst from edges where kind=?", (kind,))}


def test_interface_property_node_when_an_override_has_one():
    n = nodes()
    assert n["method:shelf.Labeled.title"]["property"] == "abstract"
    assert n["method:shelf.Labeled.title"]["kotlin_kind"] == "property"
    assert n["method:shelf.Tag.title"]["property"] == "custom"
    assert ("method:shelf.Labeled.title", "method:shelf.Tag.title") in edges("IMPLEMENTED_BY")
    assert "method:shelf.Note.title" not in n


def test_abstract_and_open_property_overrides():
    n = nodes()
    assert n["method:shelf.Card.stock"]["property"] == "abstract"
    assert n["method:shelf.Card.note"]["property"] == "custom"
    assert ("method:shelf.Card.stock", "method:shelf.Note.stock") in edges("OVERRIDDEN_BY")
    assert ("method:shelf.Card.note", "method:shelf.Note.note") in edges("OVERRIDDEN_BY")
    assert ("method:shelf.Labeled.title", "field:shelf.Note.title") not in edges("IMPLEMENTED_BY")
    assert _S["stats"]["plugins"]["kotlin"]["abstract_property_nodes"] == 2

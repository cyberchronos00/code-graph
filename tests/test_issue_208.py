"""Default-argument calls are marked and left out of impact and siblings (#208)."""
import sqlite3

import pytest

pytest.importorskip("tree_sitter_swift")
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

SRC = """\
enum Prefs {
    static var currentGamut: Int { 1 }
}

func renderSwatch(_ c: Int, gamut: Int = Prefs.currentGamut) {}

func paintWide(_ c: Int) { renderSwatch(c, gamut: 2) }

func paintDefault(_ c: Int) { renderSwatch(c) }

func otherSwatch() { let g = Prefs.currentGamut }
"""


def _index(tmp_path):
    root = tmp_path / "app"
    (root / "Sources").mkdir(parents=True)
    (root / "Sources" / "Swatch.swift").write_text(SRC, encoding="utf-8")
    index_project(root, tmp_path / "g.db", "swatch")
    return GraphStore(tmp_path / "g.db")


def test_default_argument_is_marked_and_not_an_unconditional_caller(tmp_path):
    st = _index(tmp_path)
    rows = sqlite3.connect(tmp_path / "g.db").execute(
        "SELECT src, json_extract(attrs, '$.default_arg') FROM edges WHERE dst LIKE '%currentGamut' AND kind='CALLS'"
    ).fetchall()
    marked = {src: arg for src, arg in rows if arg}
    assert marked.get("function:renderSwatch") == "gamut", rows
    impact = Q.impact(st, "currentGamut")
    assert "function:renderSwatch" not in {c["id"] for c in impact["callers"]}
    assert any(d["src"] == "function:renderSwatch" for d in impact["default_arguments"])
    sib = Q.siblings(st, "otherSwatch")
    assert all(s["node"] != "function:renderSwatch" for s in sib["co_callers"])

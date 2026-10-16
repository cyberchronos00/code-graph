"""Bare names resolve in every language, including Swift methods and computed properties (#206)."""
import pytest

pytest.importorskip("tree_sitter_swift")
pytest.importorskip("tree_sitter_typescript")
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402


def test_bare_name_finds_typescript_and_swift_and_a_computed_property(tmp_path):
    root = tmp_path / "mix"
    (root / "pkg").mkdir(parents=True)
    (root / "Sources").mkdir()
    (root / "pkg" / "color.ts").write_text("export function convertColor(hex: string) { return hex }\n", encoding="utf-8")
    (root / "Sources" / "Convert.swift").write_text(
        "enum Conversion { static func convertColor(_ hex: String) -> String { hex } }\n"
        "enum Prefs { static var currentGamut: Int { 1 } }\n"
        "enum Exporter { static func share() {} }\n",
        encoding="utf-8")
    index_project(root, tmp_path / "g.db", "mix")
    st = GraphStore(tmp_path / "g.db")
    both = set(Q.resolve_targets(st, "convertColor"))
    assert any(i.startswith("function:") and i.endswith("convertColor") for i in both), both
    assert any("Conversion.convertColor" in i for i in both), both
    gamut = Q.resolve_targets(st, "currentGamut")
    assert any("currentGamut" in i for i in gamut), gamut
    share = Q.resolve_targets(st, "share")
    assert any("Exporter.share" in i for i in share), share
    impact = Q.impact(st, "convertColor")
    langs = {st.node(t)["lang"] for t in impact["targets"]}
    assert langs >= {"typescript", "swift"}

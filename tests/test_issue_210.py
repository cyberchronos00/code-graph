"""SwiftUI NAVIGATES_TO evidence is the construction line (#210)."""
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_swift")
from cg_code_graph.indexer import index_project  # noqa: E402


def test_sheet_and_link_evidence_is_the_construction(tmp_path):
    root = tmp_path / "app"
    (root / "Sources").mkdir(parents=True)
    src = "\n".join([
        "import SwiftUI",
        "struct LabelSheet: View {",
        "    var body: some View { Text(\"label\") }",
        "}",
        "struct DetailPage: View {",
        "    var body: some View { Text(\"detail\") }",
        "}",
        "struct ShelfPage: View {",
        "    @State private var showLabel = false",
        "    var body: some View {",
        "        List {",
        "            Text(\"row\")",
        "        }",
        "        .navigationBarBackButtonHidden(true)",
        "        .sheet(isPresented: $showLabel) {",
        "            LabelSheet(item: \"x\")",
        "        }",
        "        NavigationLink(destination: DetailPage()) {",
        "            Text(\"open\")",
        "        }",
        "    }",
        "}",
        "",
    ])
    (root / "Sources" / "Shelf.swift").write_text(src, encoding="utf-8")
    lines = src.splitlines()
    sheet = lines.index("            LabelSheet(item: \"x\")") + 1
    link = lines.index("        NavigationLink(destination: DetailPage()) {") + 1
    body = lines.index("    var body: some View {") + 1
    modifier = lines.index("        .navigationBarBackButtonHidden(true)") + 1
    index_project(root, tmp_path / "g.db", "shelf")
    nav = list(sqlite3.connect(tmp_path / "g.db").execute(
        "SELECT dst, line FROM edges WHERE src='method:ShelfPage.body' AND kind='NAVIGATES_TO'"))
    by = {d: ln for d, ln in nav}
    assert by.get("page:swift:LabelSheet") == sheet, nav
    assert by.get("page:swift:DetailPage") == link, nav
    assert sheet not in (body, modifier) and link not in (body, modifier)

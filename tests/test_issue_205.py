"""Empty-tuple parameter types stay valid Swift for the extractor (#205)."""
import sqlite3

import pytest

pytest.importorskip("tree_sitter_swift")
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.plugins.swift.plugin import _preprocess  # noqa: E402

SRC = """\
#if os(macOS)
import AppKit
final class PrintPanel {
    static func dismantleNSView(_ nsView: NSView, coordinator: ()) {
        nsView.removeFromSuperview()
    }
}
#endif
"""


def test_parameter_type_is_not_rewritten():
    out, what = _preprocess(SRC.encode())
    assert b"coordinator: ()" in out
    assert "empty_tuples" not in what


def test_file_indexes_without_a_syntax_error(tmp_path):
    root = tmp_path / "pkg"
    (root / "Sources" / "App").mkdir(parents=True)
    (root / "Sources" / "App" / "PrintPanel.swift").write_text(SRC, encoding="utf-8")
    stats = index_project(root, tmp_path / "g.db", "panel")
    assert stats["plugins"]["swift"].get("files_with_syntax_errors", 0) == 0
    ids = {r[0] for r in sqlite3.connect(tmp_path / "g.db").execute("SELECT id FROM nodes")}
    assert "method:PrintPanel.dismantleNSView" in ids
    assert "class:PrintPanel" in ids

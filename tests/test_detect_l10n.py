"""Swift/Xcode detection (#241) and localization key nodes (#245).

The fixture is an invented AcmeNotes tree: an Xcode project and a nested Package.swift, Apple
string catalogs, Android strings.xml, and a JSON locale file. Nothing is downloaded.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.detect import detect  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.l10n import _strings_entries  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "acme_notes"


def test_strings_parser_skips_comments():
    text = '/* "hidden.key" = "no"; */\n// "also.hidden" = "no";\n"acme_orders.note" = "Note";\n'
    assert _strings_entries(text) == [("acme_orders.note", 3)]


def test_xcode_tree_detects_swift_without_a_root_manifest():
    found = detect(FX)
    langs = found["languages"]
    assert "swift" in langs
    assert "ios/Package.swift" in langs["swift"]
    assert "ios/AcmeNotes.xcodeproj" in langs["swift"]
    assert "*.swift" in langs["swift"]
    assert "kotlin" in langs and langs["kotlin"] == ["*.kt"]
    assert "java" in langs and langs["java"] == ["*.java"]
    assert set(found["frameworks"]) == {"swiftui", "uikit", "vapor"}
    mode = found["modes"]["swift"]
    assert mode["indexer"] == "heuristic"
    assert mode["exact"] == "unavailable"
    assert mode["reason"]


def test_root_package_swift_still_detected(tmp_path):
    (tmp_path / "Package.swift").write_text("// swift-tools-version:5.9\nimport PackageDescription\n")
    (tmp_path / "Sources").mkdir()
    (tmp_path / "Sources" / "Note.swift").write_text("struct Note {}\n")
    found = detect(tmp_path)
    assert "Package.swift" in found["languages"]["swift"]
    assert "*.swift" in found["languages"]["swift"]
    assert found["frameworks"] == {}


def test_empty_tree_has_no_languages(tmp_path):
    found = detect(tmp_path)
    assert found == {"languages": {}, "frameworks": {}}


def test_detect_cli_matches_the_library():
    proc = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "detect", str(FX)],
                          check=True, capture_output=True, text=True)
    assert json.loads(proc.stdout)["languages"]["swift"]


def test_detect_agrees_with_index(tmp_path):
    stats = index_project(FX, tmp_path / "g.db", "acme-notes")
    detected = set(detect(FX)["languages"])
    indexed = set(stats["presets"]["languages"])
    assert "swift" in indexed
    assert indexed <= detected
    assert stats["plugins"]["swift"]["mode"] == "heuristic"


def test_localization_keys_and_literal_uses(tmp_path):
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph import query

    db = tmp_path / "g.db"
    index_project(FX, db, "acme-notes")
    con = sqlite3.connect(db)
    rows = {r[0]: (r[1], r[2], json.loads(r[3] or "{}"))
            for r in con.execute("SELECT id, file, line, attrs FROM nodes WHERE kind='i18n'")}
    assert "i18n:vendor_only" not in rows
    assert "i18n:acme_orders.commented" not in rows
    assert not any("${" in key or key.endswith(".") for key in rows)
    submit = rows["i18n:acme_orders.submit"]
    files = {d["file"] for d in submit[2]["definitions"]}
    assert "ios/Resources/Localizable.xcstrings" in files
    assert "web/locales/en.json" in files
    assert "missing_locales" not in submit[2]
    android_files = {d["file"] for d in rows["i18n:acme_orders_submit"][2]["definitions"]}
    assert "android/app/src/main/res/values/strings.xml" in android_files
    assert "android/app/src/main/res/values-es/strings.xml" in android_files
    cancel = rows["i18n:acme_orders.cancel"][2]
    assert "es" in cancel["missing_locales"]
    assert rows["i18n:acme_orders.only_json"][2]["missing_locales"] == ["es"]
    assert rows["i18n:acme_orders_empty"][2]["missing_locales"] == ["es"]
    note_files = {d["file"] for d in rows["i18n:acme_orders.note"][2]["definitions"]}
    assert note_files == {
        "ios/Resources/en.lproj/Localizable.strings",
        "ios/Resources/es.lproj/Localizable.strings",
    }

    edges = list(con.execute(
        "SELECT src, dst, file, line, attrs FROM edges WHERE kind='USES_I18N'"))
    by_key: dict[str, list] = {}
    for src, dst, file, line, attrs in edges:
        by_key.setdefault(dst, []).append((src, file, line, json.loads(attrs or "{}")))
    vias = {a["via"] for _, _, _, a in by_key["i18n:acme_orders.submit"]}
    assert {"Text", "t()"} <= vias
    android_vias = {a["via"] for _, _, _, a in by_key["i18n:acme_orders_submit"]}
    assert {"R.string", "@string"} <= android_vias
    cancel_vias = {a["via"] for _, _, _, a in by_key["i18n:acme_orders.cancel"]}
    assert "String(localized:)" in cancel_vias and "$t()" in cancel_vias
    note_vias = {a["via"] for _, _, _, a in by_key["i18n:acme_orders.note"]}
    assert note_vias == {"NSLocalizedString"}

    st = GraphStore(db)
    found = query.search(st, "acme_orders.submit")
    assert any(n["id"] == "i18n:acme_orders.submit" for n in found["nodes"])
    impact = query.impact(st, "i18n:acme_orders.submit")
    callers = {c["id"] for c in impact["callers"]}
    assert any("OrderView" in c for c in callers)
    assert "function:web/orders.js#submitLabel" in callers

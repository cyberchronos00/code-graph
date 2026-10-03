"""Swift enum cases and constants (#84): `enum_case:<Type>.<case>` and `constant:<Type>.<name>` / `constant:<name>`
nodes under their type, USES_VALUE edges where the type is certain, and no change to call edges."""
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from codegraph.indexer import index_project  # noqa: E402

VALUES = """let maxRetries = 3
private let ray = 2

enum Mode: String {
    case fast, slow = "s"
    case custom(Int)

    static let fallback = Mode.slow
    static var counter = 0
    static var computed: Int { 1 }

    var label: String {
        switch self {
        case .fast: return "f"
        case .slow, .custom: return "s"
        }
    }
}

struct Config {
    static let shared = Config()
    let local = 1
    var maxRetries = 9
    func own() -> Int { maxRetries }
}

func run(mode: Mode = .fast) -> Int {
    let m: Mode = .slow
    if mode == .custom(1) { return maxRetries + ray }
    _ = Config.shared
    _ = Mode.custom(2)
    _ = m
    Mode.counter += 1
    let maxRetries = 5
    return maxRetries
}
"""
OTHER = "let ray = 7\nfunc other() -> Int { ray }\n"
PLATFORM = """#if os(iOS)
enum Transition { case none, fade }
#else
enum Transition { case none }
#endif
#if os(iOS)
func animate() { let t: Transition = .fade; _ = t }
#endif
"""


def build(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    d = Path(tempfile.mkdtemp())
    index_project(tmp_path, d / "g.db", "values")
    return sqlite3.connect(d / "g.db")


def test_enum_cases_constants_and_value_edges(tmp_path):
    db = build(tmp_path, {"Package.swift": 'import PackageDescription\nlet package = Package(name: "P")\n',
                          "Sources/P/Values.swift": VALUES, "Sources/P/Other.swift": OTHER})
    nodes = {(k, i) for k, i in db.execute("SELECT kind, id FROM nodes WHERE kind IN ('enum_case','constant')")}
    assert nodes == {("enum_case", "enum_case:Mode.fast"), ("enum_case", "enum_case:Mode.slow"),
                     ("enum_case", "enum_case:Mode.custom"), ("constant", "constant:Mode.fallback"),
                     ("constant", "constant:Mode.counter"), ("constant", "constant:Config.shared"),
                     ("constant", "constant:maxRetries"), ("constant", "constant:ray"),
                     ("constant", "constant:ray#Sources/P/Values.swift")}
    assert ("class:Mode", "enum_case:Mode.fast") in set(db.execute("SELECT src, dst FROM edges WHERE kind='CONTAINS'"))
    uses = set(db.execute("SELECT src, dst, line FROM edges WHERE kind='USES_VALUE'"))
    assert uses == {
        ("class:Mode", "enum_case:Mode.slow", 8),                     # Mode.slow
        ("method:Mode.label", "enum_case:Mode.fast", 14),             # switch self { case .fast
        ("method:Mode.label", "enum_case:Mode.slow", 15),
        ("method:Mode.label", "enum_case:Mode.custom", 15),
        ("function:run", "enum_case:Mode.fast", 27),                  # a parameter default
        ("function:run", "enum_case:Mode.slow", 28),                  # let m: Mode = .slow
        ("function:run", "enum_case:Mode.custom", 29),                # mode == .custom(1)
        ("function:run", "constant:maxRetries", 29),                  # not the local declared below
        ("function:run", "constant:ray#Sources/P/Values.swift", 29),  # this file's private one
        ("function:run", "constant:Config.shared", 30),
        ("function:run", "enum_case:Mode.custom", 31),                # Mode.custom(2): a case, not a call
        ("function:run", "constant:Mode.counter", 33),
        ("function:other", "constant:ray", 2),
    }                                   # Config.own reads its property, not the file-level constant
    assert not set(db.execute("SELECT src, dst FROM edges WHERE kind='CALLS' AND (dst LIKE 'enum_case:%' "
                              "OR dst LIKE 'constant:%')"))


def test_cases_of_platform_variants_are_linked(tmp_path):
    """Cases of an enum declared once per `#if` branch are variants of each other, like its members (#86)."""
    db = build(tmp_path, {"T.swift": PLATFORM})
    ids = {i for (i,) in db.execute("SELECT id FROM nodes WHERE kind='enum_case'")}
    assert ids == {"enum_case:Transition.none", "enum_case:Transition.fade", "enum_case:Transition.none@4"}
    assert ("function:animate", "enum_case:Transition.fade") in set(
        db.execute("SELECT src, dst FROM edges WHERE kind='USES_VALUE'"))

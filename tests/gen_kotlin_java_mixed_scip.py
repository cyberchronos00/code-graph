"""Write the synthetic scip-java index for tests/kotlin_java_mixed_fixture.

The fixture is not built with scip-java. Occurrences use the same symbol shape scip-java writes
(`semanticdb maven . . demo/Widget#getName().`) and sit on the source line of the name. Regenerate
with `python tests/gen_kotlin_java_mixed_scip.py` from the repo root.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cg_code_graph" / "plugins" / "scip"))
import scip_pb2  # noqa: E402

MIX = ROOT / "tests" / "kotlin_java_mixed_fixture"

# (relative path, language, (line 0-based, symbol, definition))
DOCS = [
    ("src/main/kotlin/demo/Widget.kt", "kotlin", [
        (3, "semanticdb maven . . demo/top().", True),
        (5, "semanticdb maven . . demo/Widget#", True),
        (6, "semanticdb maven . . demo/Widget#Companion#", True),
        (7, "semanticdb maven . . demo/Widget#Companion#make().", True),
        (9, "semanticdb maven . . demo/Widget#Companion#stat().", True),
        (13, "semanticdb maven . . demo/Widget#name.", True),
        (14, "semanticdb maven . . demo/Widget#count.", True),
        (15, "semanticdb maven . . demo/Widget#ready.", True),
    ]),
    ("src/main/kotlin/demo/OrderEvents.kt", "kotlin", [
        (2, "semanticdb maven . . demo/OrderEvents#", True),
        (3, "semanticdb maven . . demo/OrderEvents#onPlaced().", True),
        (4, "semanticdb maven . . demo/OrderService#find().", False),
    ]),
    ("src/main/java/demo/OrderService.java", "java", [
        (2, "semanticdb maven . . demo/OrderService#", True),
        (3, "semanticdb maven . . demo/OrderService#find().", True),
    ]),
    ("src/main/java/demo/FromJava.java", "java", [
        (2, "semanticdb maven . . demo/FromJava#", True),
        (3, "semanticdb maven . . demo/FromJava#a().", True),
        (3, "semanticdb maven . . demo/WidgetKit#top().", False),
        (5, "semanticdb maven . . demo/FromJava#b().", True),
        (5, "semanticdb maven . . demo/Widget#Companion#make().", False),
        (7, "semanticdb maven . . demo/FromJava#c().", True),
        (7, "semanticdb maven . . demo/Widget#Companion#stat().", False),
        (9, "semanticdb maven . . demo/FromJava#d().", True),
        (9, "semanticdb maven . . demo/Widget#getName().", False),
        (11, "semanticdb maven . . demo/FromJava#e().", True),
        (11, "semanticdb maven . . demo/Widget#setCount().", False),
        (13, "semanticdb maven . . demo/FromJava#f().", True),
        (13, "semanticdb maven . . demo/Widget#isReady().", False),
    ]),
]


def add_occ(doc, line: int, symbol: str, definition: bool) -> None:
    o = doc.occurrences.add()
    o.range.extend([line, 0, line, 1])
    o.enclosing_range.extend([line, 0, line, 1])
    o.symbol = symbol
    if definition:
        o.symbol_roles = scip_pb2.SymbolRole.Definition


def write_index(path: Path, docs, *, tool: str = "scip-java", version: str = "synthetic") -> None:
    idx = scip_pb2.Index()
    idx.metadata.tool_info.name = tool
    idx.metadata.tool_info.version = version
    idx.metadata.project_root = "."
    for rel, lang, occs in docs:
        doc = idx.documents.add()
        doc.relative_path = rel
        doc.language = lang
        for line, symbol, definition in occs:
            add_occ(doc, line, symbol, definition)
    path.write_bytes(idx.SerializeToString())


def main() -> None:
    write_index(MIX / "index.scip", DOCS)
    print(f"wrote {MIX / 'index.scip'}")


if __name__ == "__main__":
    main()

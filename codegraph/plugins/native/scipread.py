"""SCIP reading helpers shared by the native-language plugins (Rust, C/C++).

Unlike the generic importer (plugins/scip/importer.py), the native plugins own their node ids: definitions are
matched to syntactic items by (file, line, name) so the SCIP layer and the tree-sitter layer agree. This module only
loads an index, merges duplicate documents (scip-clang emits one per translation unit that touches a header) and
parses symbol descriptors.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scip"))
import scip_pb2  # noqa: E402

DEFINITION = scip_pb2.SymbolRole.Definition
FORWARD_DEFINITION = getattr(scip_pb2.SymbolRole, "ForwardDefinition", 64)
WRITE_ACCESS = scip_pb2.SymbolRole.WriteAccess


@dataclass
class Occ:
    line: int          # 0-based
    col: int
    end_col: int
    symbol: str
    roles: int
    enclosing: tuple   # (start_line, end_line) 0-based, or ()


@dataclass
class Doc:
    path: str
    occs: list[Occ] = field(default_factory=list)
    symbols: dict = field(default_factory=dict)   # symbol -> SymbolInformation


@dataclass
class Index:
    tool: str
    version: str
    docs: dict[str, Doc]
    # symbol -> list of (path, Occ) definitions (scip-clang: also forward definitions/declarations)
    defs: dict[str, list] = field(default_factory=dict)
    occurrences: int = 0     # non-local occurrences read
    positioned: int = 0      # ... of which carried a range cg can read


def has_position(o) -> bool:
    """The occurrence carries a range in a form cg reads (packed `range` of 3 / 4 ints, or a typed range)."""
    return len(o.range) in (3, 4) or o.HasField("single_line_range") or o.HasField("multi_line_range")


def health_warning(name: str, occurrences: int, positioned: int, definitions: int | None = None,
                   matched: int | None = None, what: str = "the syntax layer's declarations") -> str | None:
    """A warning for an index cg could not use although it is not empty, else None. Reported in `cg coverage`
    (`warnings`) and by `cg doctor --scip`, instead of an exact layer that silently adds nothing."""
    if occurrences and not positioned:
        return (f"SCIP index {name}: {occurrences} occurrences but none with a usable position (range missing or in a "
                "form cg does not read): no edges could be placed; regenerate it, or report the indexer version")
    if occurrences and definitions == 0:
        return (f"SCIP index {name}: {occurrences} occurrences but 0 definitions (symbols cg maps to nodes): nothing "
                "imported; check the indexer and that the index belongs to this project")
    if definitions and matched == 0:
        return (f"SCIP index {name}: 0 of {definitions} definitions matched {what}: the exact layer added no edges; "
                "check that the index was built from these sources (same paths, same revision)")
    return None


def _rng(r) -> tuple[int, int, int, int]:
    r = list(r)
    if len(r) == 3:
        return r[0], r[1], r[0], r[2]
    if len(r) == 4:
        return r[0], r[1], r[2], r[3]
    return (0, 0, 0, 0)


def _typed(o, legacy: str, single: str, multi: str) -> tuple[int, int, int, int] | None:
    """An occurrence range: the legacy packed `range` / `enclosing_range`, else the typed one newer indexers write
    (SCIP 0.9 `single_line_range` / `multi_line_range`, e.g. scip-java 0.13); None when absent."""
    r = getattr(o, legacy)
    if len(r):
        return _rng(r)
    if o.HasField(single):
        t = getattr(o, single)
        return t.line, t.start_character, t.line, t.end_character
    if o.HasField(multi):
        t = getattr(o, multi)
        return t.start_line, t.start_character, t.end_line, t.end_character
    return None


def occ_range(o) -> tuple[int, int, int, int]:
    return _typed(o, "range", "single_line_range", "multi_line_range") or (0, 0, 0, 0)


def occ_enclosing(o) -> tuple[int, int, int, int] | None:
    return _typed(o, "enclosing_range", "single_line_enclosing_range", "multi_line_enclosing_range")


def load(path: str | Path) -> Index:
    idx = scip_pb2.Index()
    idx.ParseFromString(Path(path).read_bytes())
    docs: dict[str, Doc] = {}
    seen: dict[str, set] = {}
    out_occ = [0, 0]
    for d in idx.documents:
        doc = docs.get(d.relative_path)
        if doc is None:
            doc = docs[d.relative_path] = Doc(d.relative_path)
            seen[d.relative_path] = set()
        s = seen[d.relative_path]
        for o in d.occurrences:
            if o.symbol.startswith("local ") or not o.symbol:
                continue
            out_occ[0] += 1
            if has_position(o):
                out_occ[1] += 1
            sl, sc, el, ec = occ_range(o)
            key = (sl, sc, ec, o.symbol, o.symbol_roles)
            if key in s:
                continue
            s.add(key)
            enc = ()
            er = occ_enclosing(o)
            if er:
                enc = (er[0], er[2])
            doc.occs.append(Occ(sl, sc, ec, o.symbol, o.symbol_roles, enc))
        for si in d.symbols:
            if not si.symbol.startswith("local "):
                doc.symbols.setdefault(si.symbol, si)
    out = Index(idx.metadata.tool_info.name, idx.metadata.tool_info.version, docs, occurrences=out_occ[0],
                positioned=out_occ[1])
    for doc in docs.values():
        doc.occs.sort(key=lambda o: (o.line, o.col))
        for o in doc.occs:
            if o.roles & DEFINITION:
                out.defs.setdefault(o.symbol, []).append((doc.path, o))
    return out


_DESC = re.compile(r"(`(?:[^`]|``)*`|[^\s/#.()\[\]:!`]+)?(/|#|\.|:|!|\(([^)]*)\)\.|\[)")


def descriptors(symbol: str) -> tuple[str, list[tuple[str, str]]] | None:
    """'<scheme> <manager> <package> <version> <descriptors>' -> (package, [(name, suffix)]).
    suffix: '/' namespace, '#' type, '.' term, '(' method, '!' macro, '[' type parameter / impl disambiguator,
    ')' parameter."""
    if symbol.startswith("local "):
        return None
    parts = symbol.split(" ", 4)
    if len(parts) < 5:
        return None
    pkg, desc = parts[2], parts[4]
    out = []
    i = 0
    while i < len(desc):
        if desc[i] == "(":  # (name) parameter: older rust-analyzer releases (1.83) give parameters global symbols
            j = desc.find(")", i + 1)
            if j < 0:
                break
            out.append((desc[i + 1:j].strip("`"), ")"))
            i = j + 1
            continue
        if desc[i] == "[":  # [name] type parameter (rust-analyzer uses it for impl#[Type][Trait])
            j = i + 1
            depth = 1
            while j < len(desc) and depth:
                if desc[j] == "`":
                    k = desc.find("`", j + 1)
                    j = (k if k > 0 else len(desc) - 1) + 1
                    continue
                depth += {"[": 1, "]": -1}.get(desc[j], 0)
                j += 1
            out.append((desc[i + 1:j - 1].strip("`"), "["))
            i = j
            continue
        m = _DESC.match(desc, i)
        if not m or m.end() == i:
            break
        name = (m.group(1) or "").strip("`").replace("``", "`")
        suf = m.group(2)
        if suf.startswith("("):
            suf = "("
        out.append((name, suf))
        i = m.end()
    return pkg, out


def is_project_symbol(idx: Index, symbol: str) -> bool:
    return symbol in idx.defs


def native_warning(path, idx: Index, stats: dict) -> str | None:
    """health_warning for a native plugin's import (rust-analyzer, scip-clang): matched = definitions placed on a
    syntax-layer item, the rest became synthetic nodes."""
    m = stats.get("scip_defs_matched", 0)
    return health_warning(Path(path).name, idx.occurrences, idx.positioned,
                          (m + stats.get("scip_defs_synthetic", 0)) or None, m, "the syntax layer's items")

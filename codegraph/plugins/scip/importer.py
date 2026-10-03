"""SCIP import path: any SCIP-producing indexer (scip-php, scip-typescript, scip-clang,
rust-analyzer scip, scip-go, scip-python, scip-java) can feed the same graph.

Mapping
  definition occurrence -> node (kind from the SCIP descriptor suffix: '#' type, '().' method,
                           '.' term, '/' namespace); id uses the same FQN scheme as native
                           plugins when the separator is known, so native + SCIP data merge.
  reference occurrence  -> edge from the innermost enclosing definition in the same document
                           (enclosing_range when the indexer provides it, else the nearest
                           preceding definition; confidence 'resolved' resp. 'heuristic').
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scip_pb2  # noqa: E402  (generated from scip.proto with grpc_tools.protoc)

from ...core.model import HEURISTIC, RESOLVED  # noqa: E402
from ...core.plugin import GraphBuilder  # noqa: E402
from ..native.scipread import has_position, health_warning, occ_enclosing, occ_range  # noqa: E402

NS_SEP = {"php": "\\", "go": "/", "rust": "::", "c_cpp": "::", "typescript": "/", "python": ".", "java": "."}


def parse_symbol(sym: str):
    """'scheme manager pkg ver descriptors' -> (descriptor string, kind, fqn parts)."""
    if sym.startswith("local "):
        return None
    parts = sym.split(" ", 4)
    if len(parts) < 5:
        return None
    desc = parts[4]
    toks = re.findall(r"(`[^`]*`|[^/#.()\[\]:!]+)([/#.:!]|\(\)\.|\([^)]*\)\.|\[[^\]]*\])", desc)
    if not toks:
        return None
    names = [(t[0].strip("`"), t[1]) for t in toks]
    last_name, last_suf = names[-1]
    if last_suf.startswith("("):
        kind = "method"
    elif last_suf == "#":
        kind = "class"
    elif last_suf == "/":
        kind = "namespace"
    elif last_suf in (".", ":"):
        kind = "term"
    else:
        kind = "other"
    return desc, kind, names


def to_node(sym: str, lang: str):
    p = parse_symbol(sym)
    if not p:
        return None
    desc, kind, names = p
    sep = NS_SEP.get(lang, "/")
    ns = [n for n, s in names if s == "/"]
    types = [n for n, s in names if s == "#"]
    cls = sep.join(ns + types[:1]) if types else None
    if kind == "class":
        return "class", sep.join(ns + types), names[-1][0]
    if kind == "method":
        owner = sep.join(ns + types) if types else sep.join(ns)
        return "method", f"{owner}::{names[-1][0]}" if types else f"{owner}{sep}{names[-1][0]}", names[-1][0]
    if kind == "term" and types:
        n = names[-1][0]
        if n.startswith("$"):
            return "property", f"{sep.join(ns + types)}::{n}", n
        return "const", f"{sep.join(ns + types)}::{n}", n
    return None


def import_scip(path: str | Path, builder: GraphBuilder, lang: str = "php", skip_prefixes=("tests/",)) -> dict:
    idx = scip_pb2.Index()
    idx.ParseFromString(Path(path).read_bytes())
    stats = defaultdict(int)
    for doc in idx.documents:
        rel = doc.relative_path
        if rel.startswith(skip_prefixes):
            stats["docs_skipped"] += 1
            continue
        stats["docs"] += 1
        defs = []  # (start_line, end_line or None, node_id)
        for o in doc.occurrences:
            if o.symbol and not o.symbol.startswith("local "):
                stats["occurrences"] += 1
                stats["positioned"] += has_position(o)
            if o.symbol_roles & scip_pb2.SymbolRole.Definition:
                n = to_node(o.symbol, lang)
                if not n:
                    continue
                kind, key, name = n
                start = occ_range(o)[0]
                nid = builder.add_node(kind, key, name=name, fqn=key, file=rel, line=start + 1, lang=lang,
                                       attrs={"scip_symbol": o.symbol})
                er = occ_enclosing(o)
                defs.append((start, er[2] if er else None, nid, kind))
                stats["definitions"] += 1
        defs.sort(key=lambda d: (d[0], -1 if d[1] is None else d[1], d[2]))
        callables = [d for d in defs if d[3] in ("method",)]
        for o in doc.occurrences:
            if o.symbol_roles & scip_pb2.SymbolRole.Definition:
                continue
            n = to_node(o.symbol, lang)
            if not n:
                continue
            kind, key, _ = n
            line = occ_range(o)[0]
            src, conf = None, HEURISTIC
            for (sl, el, nid, k) in callables:
                if sl <= line and (el is None or line <= el):
                    src = nid
                    conf = RESOLVED if el is not None else HEURISTIC
            if src is None:
                stats["refs_without_enclosing"] += 1
                continue
            dst = f"{kind}:{key}"
            builder.add_edge(src, dst, "CALLS" if kind == "method" else "REFERENCES", rel, line + 1, conf, source="scip")
            stats["references"] += 1
    out = dict(stats)
    w = health_warning(Path(path).name, out.get("occurrences", 0), out.get("positioned", 0),
                       out.get("definitions", 0))
    if w:
        out["warning"] = w
    return out

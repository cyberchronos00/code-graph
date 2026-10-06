"""Java exact layer: scip-java symbols mapped onto the Java plugin's node ids.

The index comes from `kotlin.exact.find_index` (one run shared with Kotlin). Definitions match tree-sitter
declarations by (file, line of the name, name). Overload disambiguators (`add().` and `add(+1).`) collapse onto
the shared method id. Exact `CALLS`, constructor `INSTANTIATES`, and field `REFERENCES` replace the heuristic
edges of files the index covers. A file it misses keeps heuristic edges.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ...core.model import EXACT, HEURISTIC
from ..native import scipread

CALL_KINDS = ("CALLS", "INSTANTIATES")


def java_ids(symbol: str) -> tuple[str, str] | None:
    """`(node id, kind)` for a scip-java symbol, in the Java plugin's id scheme.

    `demo/Formatter#Box#make().` is `method:demo.Formatter.Box.make`. `<init>` is
    `constructor:demo.Formatter.<init>`. A term is `field:demo.Formatter.value`. Package
    descriptors are `package:demo`. Overload markers (`(+1)`) are not part of the id.
    """
    dd = scipread.descriptors(symbol)
    if not dd or not dd[1]:
        return None
    names = list(dd[1])
    while names and names[-1][1] == ")":
        names = names[:-1]
    if not names:
        return None
    last, suf = names[-1]
    ns = [n for n, s in names if s == "/"]
    types = [n for n, s in names if s == "#"]
    if suf == "/":
        fq = ".".join(ns)
        return (f"package:{fq}", "package") if fq else None
    if suf == "#":
        fq = ".".join(ns + types)
        return (f"class:{fq}", "class") if fq else None
    owner = ".".join(ns + types)
    if not owner:
        return None
    if suf == "(" and last == "<init>":
        return f"constructor:{owner}.<init>", "constructor"
    if suf == "(":
        return f"method:{owner}.{last}", "method"
    if suf == ".":
        return f"field:{owner}.{last}", "field"
    return None


_ANON_SEG = re.compile(r"^\$?anon\d*$")
_ACCESSOR = re.compile(r"^(get|set|is)([A-Z]\w*)$")


def is_anon_id(nid: str) -> bool:
    """True when a mapped id still contains a scip-java anonymous type segment (`$anon`)."""
    return any(_ANON_SEG.match(part) for part in nid.split(":", 1)[-1].split("."))


def _anon_class(nodes, file: str, line: int):
    """The numbered anonymous class (`Foo.1`, `Foo.method.1`) whose range contains `line`."""
    cands = [n for n in nodes.values()
             if n.lang == "java" and n.kind == "class" and n.file == file and (n.name or "").isdigit()
             and n.line and n.line <= line <= (n.end_line or n.line)]
    if not cands:
        return None
    return min(cands, key=lambda n: ((n.end_line or n.line) - n.line, n.line))


def bind_java_symbol(symbol: str, nodes, file: str, line: int) -> tuple[str, str] | None:
    """`(node id, kind)` for a scip-java symbol, preferring nodes the Java plugin already created.

    A term that names an enum constant is `enum_case:…`, not a field. An anonymous type (`$anon`)
    binds to `class:….1` (and its methods) instead of a parallel `$anon` id.
    """
    mapped = java_ids(symbol)
    if not mapped:
        return None
    nid, kind = mapped
    if is_anon_id(nid):
        cls = _anon_class(nodes, file, line)
        if cls is None:
            return None
        if kind == "class":
            return cls.id, "class"
        member = nid.rsplit(".", 1)[-1]
        if kind == "constructor" or member == "<init>":
            cid = f"constructor:{cls.fqn}.<init>"
            return (cid, "constructor") if cid in nodes else (cls.id, "class")
        if kind == "field":
            fid = f"field:{cls.fqn}.{member}"
            return (fid, "field") if fid in nodes else None
        mid = f"method:{cls.fqn}.{member}"
        return (mid, "method") if mid in nodes else None
    if kind == "field":
        enum_id = "enum_case:" + nid.split(":", 1)[1]
        node = nodes.get(enum_id)
        if node is not None and node.file == file and (nid not in nodes or node.line == line):
            return enum_id, "enum_case"
    return nid, kind


def kotlin_accessor_target(symbol: str, nodes) -> tuple[str, str] | None:
    """`(kotlin node id, edge kind)` for a JVM accessor call (`getX` / `setX` / `isX`).

    `Widget#getName().` is `field:demo.Widget.name` with `READS_PROP`. `setX` is `WRITES_PROP`.
    A custom accessor that is already a Kotlin method node is `CALLS`.
    """
    dd = scipread.descriptors(symbol)
    if not dd or not dd[1]:
        return None
    names = [n for n in dd[1] if n[1] != ")"]
    if not names or names[-1][1] != "(":
        return None
    m = _ACCESSOR.fullmatch(names[-1][0])
    if not m:
        return None
    kind, raw = m.group(1), m.group(2)
    prop = raw[:1].lower() + raw[1:]
    owner = ".".join([n for n, s in names if s == "/"] + [n for n, s in names[:-1] if s == "#"])
    if not owner:
        return None
    props = [prop]
    if kind == "is":
        props.append("is" + raw)
    edge = "WRITES_PROP" if kind == "set" else "READS_PROP"
    for p in props:
        fid = f"field:{owner}.{p}"
        node = nodes.get(fid)
        if node is not None and node.lang == "kotlin" and node.kind == "field":
            return fid, edge
    for p in props:
        for n in nodes.values():
            if (n.lang == "kotlin" and n.attrs.get("property") and (n.fqn or "").rsplit(".", 1)[0] == owner
                    and n.name == p):
                return n.id, "CALLS"
    return None


class ExactLayer:
    def __init__(self, plugin):
        self.p = plugin
        self.b = plugin.b

    def apply(self, path: Path, st: dict) -> bool:
        idx = scipread.load(path)
        self.doc_paths = list(idx.docs)
        jdocs = {p: d for p, d in idx.docs.items() if p.endswith(".java")}
        st["scip_documents"] = len(jdocs)
        if not jdocs:
            return False
        decl_pos = {}
        by_file = defaultdict(list)
        for d in self._decls():
            decl_pos[(d.file, d.line, d.name)] = d.id
            by_file[d.file].append(d)
        for n in self.b.nodes.values():
            if n.lang == "java" and n.kind == "field" and n.file and n.line:
                decl_pos.setdefault((n.file, n.line, n.name), n.id)
        for n in self.b.nodes.values():
            if n.lang == "java" and n.kind == "enum_case" and n.file and n.line:
                decl_pos[(n.file, n.line, n.name)] = n.id
        sym = {}
        matched = unmatched = 0
        for s, defs in idx.defs.items():
            if s.endswith((")", "]")):
                continue
            mapped = java_ids(s)
            if not mapped:
                continue
            kind_guess = mapped[1]
            for rel, o in defs:
                if rel not in jdocs:
                    continue
                nid = self._match(rel, o, s, mapped, decl_pos, by_file)
                if nid is None:
                    if kind_guess in ("method", "class", "constructor", "field"):
                        name = mapped[0].rsplit(".", 1)[-1]
                        if not name.startswith("<") or name == "<init>":
                            unmatched += 1
                            if len(st.setdefault("scip_defs_unmatched_samples", [])) < 10:
                                st["scip_defs_unmatched_samples"].append(
                                    f"{rel}:{o.line + 1}:{o.col + 1} {mapped[0]}")
                    continue
                sym[s] = nid
                matched += 1
        st["scip_defs_matched"] = matched
        st["scip_defs_unmatched"] = unmatched
        w = scipread.health_warning(Path(path).name, idx.occurrences, idx.positioned,
                                    None if not matched + unmatched else matched + unmatched, matched,
                                    "the Java declarations")
        if w:
            st["scip_warning"] = w
        covered = set(jdocs)
        heur = self._remove_heuristic(covered)
        exact = set()
        owners = self._owner_index(covered)
        for rel, doc in jdocs.items():
            for o in doc.occs:
                if o.roles & scipread.DEFINITION:
                    continue
                mapped = bind_java_symbol(o.symbol, self.b.nodes, rel, o.line + 1) or java_ids(o.symbol)
                if not mapped:
                    continue
                nid, kind = mapped
                if is_anon_id(nid):
                    continue
                dst = sym.get(o.symbol) or (nid if nid in self.b.nodes else None)
                if dst is not None and self.b.nodes[dst].kind == "enum_case":
                    kind = "enum_case"
                if dst is None and kind == "constructor":
                    cls = nid.replace("constructor:", "class:", 1).rsplit(".<init>", 1)[0]
                    dst_cls = cls if cls in self.b.nodes else None
                    if dst_cls:
                        self._emit_init(owners, rel, o, dst_cls, nid if nid in self.b.nodes else None, st, exact)
                    else:
                        st["scip_refs_external"] = st.get("scip_refs_external", 0) + 1
                    continue
                if dst is None:
                    if kotlin_accessor_target(o.symbol, self.b.nodes):
                        continue
                    st["scip_refs_external"] = st.get("scip_refs_external", 0) + 1
                    continue
                if kind == "constructor":
                    cls = "class:" + nid.split(":", 1)[1].rsplit(".<init>", 1)[0]
                    self._emit_init(owners, rel, o, cls if cls in self.b.nodes else dst, dst, st, exact)
                    continue
                src = self._owner(owners, rel, o.line + 1)
                if src is None or src == dst:
                    continue
                if kind == "method":
                    edge = "REFERENCES_FN" if self._is_method_ref(rel, o) else "CALLS"
                    attrs = {"how": "method-reference"} if edge == "REFERENCES_FN" else {}
                    self.b.add_edge(src, dst, edge, rel, o.line + 1, EXACT, source="scip", **attrs)
                    if edge == "CALLS":
                        exact.add((src, dst, "CALLS"))
                elif kind in ("field", "enum_case"):
                    self.b.add_edge(src, dst, "REFERENCES", rel, o.line + 1, EXACT, source="scip")
                elif kind == "class":
                    continue
                else:
                    continue
                st["scip_references"] = st.get("scip_references", 0) + 1
        agree = len(heur & exact)
        st["exact_vs_heuristic"] = {
            "heuristic_edges": len(heur), "exact_edges": len(exact), "agree": agree,
            "precision": round(agree / len(heur), 3) if heur else None,
            "recall": round(agree / len(exact), 3) if exact else None,
            "candidate_edges": len(self.candidates), "candidate_agree": len(self.candidates & exact)}
        rep = getattr(self.p, "file_report", None) or {}
        seen = set(rep.get("seen") or covered)
        st["scip_files"] = len({f for f in covered & seen if f.endswith(".java")})
        return True

    def _emit_init(self, owners, rel, o, cls: str, ctor: str | None, st: dict, exact: set) -> None:
        src = self._owner(owners, rel, o.line + 1)
        if src is None or src == cls:
            return
        self.b.add_edge(src, cls, "INSTANTIATES", rel, o.line + 1, EXACT, source="scip")
        exact.add((src, cls, "INSTANTIATES"))
        if ctor and ctor != src:
            self.b.add_edge(src, ctor, "CALLS", rel, o.line + 1, EXACT, source="scip", binding="constructor")
            exact.add((src, ctor, "CALLS"))
        st["scip_references"] = st.get("scip_references", 0) + 1

    def _match(self, rel, o, symbol, mapped, decl_pos, by_file):
        nid, kind = mapped
        bound = bind_java_symbol(symbol, self.b.nodes, rel, o.line + 1)
        if bound and (is_anon_id(nid) or bound[1] == "enum_case") and bound[0] in self.b.nodes:
            return bound[0]
        name = "<init>" if kind == "constructor" else nid.split(":", 1)[1].rsplit(".", 1)[-1]
        hit = decl_pos.get((rel, o.line + 1, name))
        if hit is not None:
            return hit
        if kind in ("method", "constructor", "class"):
            for d in by_file.get(rel, ()):
                if d.name == name and d.line <= o.line + 1 <= d.end:
                    if kind == "class" and d.kind != "class":
                        continue
                    if kind == "constructor" and d.kind != "constructor":
                        continue
                    if kind == "method" and d.kind != "method":
                        continue
                    return d.id
        if nid in self.b.nodes:
            return nid
        return None

    def _decls(self) -> list:
        seen, out = set(), []
        pools = [getattr(self.p, "bodies", []), list(getattr(self.p, "decls", {}).values())]
        for pool in pools:
            for d in pool:
                if id(d) not in seen:
                    seen.add(id(d))
                    out.append(d)
        return out

    def _remove_heuristic(self, covered: set) -> set:
        out, cand = set(), set()
        for key, e in list(self.b.edges.items()):
            if e.confidence != HEURISTIC or e.file not in covered:
                continue
            if e.kind == "REFERENCES_FN" and e.attrs.get("how") == "method-reference":
                n = self.b.nodes.get(e.src)
                if n is not None and n.lang == "java":
                    del self.b.edges[key]
                continue
            if e.kind in CALL_KINDS:
                n = self.b.nodes.get(e.src)
                if n is not None and n.lang == "java":
                    (cand if e.attrs.get("binding") == "candidate" else out).add((e.src, e.dst, e.kind))
                    del self.b.edges[key]
        self.candidates = cand
        return out

    def _owner_index(self, covered: set) -> dict:
        idx = defaultdict(list)
        for d in self._decls():
            if d.file in covered and d.line and d.id in self.b.nodes:
                idx[d.file].append((d.line, d.end or d.line, 0 if d.kind != "class" else 1, d.id))
        return idx

    def _owner(self, owners: dict, rel: str, line: int) -> str | None:
        best = None
        for sl, el, cls, nid in owners.get(rel, ()):
            if sl <= line <= el and nid in self.b.nodes:
                k = (cls, el - sl)
                if best is None or k < best[0]:
                    best = (k, nid)
        if best:
            return best[1]
        fid = f"file:java:{rel}"
        return fid if fid in self.b.nodes else None

    def _is_method_ref(self, rel: str, o) -> bool:
        jf = getattr(self.p, "_jf_by_rel", {}).get(rel)
        if jf is None:
            return False
        lines = jf.src.splitlines()
        if o.line < 0 or o.line >= len(lines):
            return False
        line = lines[o.line]
        col = o.col
        if col >= 2 and line[col - 2:col] == b"::":
            return True
        return col < len(line) and line[col:col + 2] == b"::"

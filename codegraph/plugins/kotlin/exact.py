"""Kotlin exact layer: compiler-resolved references from a scip-java SCIP index (SemanticDB compiler plugin).

Where the index comes from, in order:
  1. a `--scip` file whose documents include Kotlin sources (consumed here, not by the generic SCIP importer);
  2. $CODEGRAPH_KOTLIN_SCIP_FILE;
  3. running scip-java on the Gradle / Maven build when $CODEGRAPH_KOTLIN_SCIP=1 (opt-in: indexing runs the build,
     which executes the project's build scripts, and writes build/ output into the project). The run is cached
     like the Rust / C indexers (codegraph/plugins/native/runner.py).
Without an index the heuristic layer stays; `stats["scip"]` says why (no JDK, no scip-java, not opted in, the run
failed), and `cg coverage` reports it.

Mapping: SCIP definitions are matched to the tree-sitter declarations by (file, line of the name, name), so node ids
do not change. Every reference to a project method / function becomes a CALLS edge (a constructor reference
`Foo#<init>().` an INSTANTIATES edge) from the innermost declaration around it (a Ktor route handler lambda or
Compose page, else the function / method, else the class, else the file), confidence exact. The heuristic call edges
of the files the index covers are replaced; files it does not cover keep them. The overlap of both layers is kept
as precision / recall of the heuristic layer against the exact one (`stats["exact_vs_heuristic"]`).
"""
from __future__ import annotations

import os
import re
import shutil
from collections import defaultdict
from pathlib import Path

from ...core.model import EXACT, HEURISTIC
from ..native import runner, scipread

BUILD_FILES = ("build.gradle.kts", "build.gradle", "settings.gradle.kts", "settings.gradle", "pom.xml")
CALL_KINDS = ("CALLS", "INSTANTIATES")


def _has_kotlin_docs(path: str) -> bool:
    try:
        idx = scipread.load(path)
    except Exception:
        return False
    return any(p.endswith((".kt", ".kts")) for p in idx.docs)


def find_java() -> str | None:
    jh = os.environ.get("JAVA_HOME")
    if jh and (Path(jh) / "bin" / "java").exists():
        return str(Path(jh) / "bin" / "java")
    return shutil.which("java")


def find_index(project, files: list[str]) -> tuple[Path | None, dict]:
    """(SCIP path or None, info). info["status"] explains a missing index."""
    for s in project.options.get("scip") or []:
        if _has_kotlin_docs(s):
            consumed = project.options.setdefault("scip_consumed", [])
            consumed.append(s)
            return Path(s), {"source": "--scip", "path": str(s)}
    pre = os.environ.get("CODEGRAPH_KOTLIN_SCIP_FILE")
    if pre:
        if Path(pre).exists():
            return Path(pre), {"source": "CODEGRAPH_KOTLIN_SCIP_FILE", "path": pre}
        return None, {"status": f"CODEGRAPH_KOTLIN_SCIP_FILE={pre} does not exist"}
    root = Path(project.root)
    build = next((b for b in BUILD_FILES if (root / b).exists()), None)
    tool = runner.find_tool("CODEGRAPH_SCIP_JAVA", ["scip-java"],
                            [Path.home() / "tools", Path.home() / ".local" / "bin",
                             Path.home() / ".local" / "share" / "coursier" / "bin"])
    java = find_java()
    if not build:
        return None, {"status": "no Gradle / Maven build file at the project root"}
    if not java:
        return None, {"status": "no JDK (java not on PATH, JAVA_HOME unset)"}
    if not tool:
        return None, {"status": "scip-java not installed (docs/kotlin.md#exact-mode)"}
    if os.environ.get("CODEGRAPH_KOTLIN_SCIP") != "1":
        return None, {"status": "scip-java found but not run: indexing runs the Gradle / Maven build (the project's "
                                "build scripts); set CODEGRAPH_KOTLIN_SCIP=1 or pass --scip", "indexer": tool}
    env = dict(os.environ)          # the JDK found for the run, without changing this process's environment
    jbin = str(Path(java).parent)
    if jbin not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = jbin + os.pathsep + env.get("PATH", "")
    env.setdefault("JAVA_HOME", str(Path(java).resolve().parent.parent))
    srcs = list(files) + [b for b in BUILD_FILES if (root / b).exists()]
    key = runner.fingerprint(root, srcs, f"scip-java|{runner.tool_version(tool, ('version',))}")
    timeout = int(os.environ.get("CODEGRAPH_INDEXER_TIMEOUT", "3600"))
    path, info = runner.run_cached("kotlin", key, [tool, "index"], root, "--output", timeout, out_flag_style="eq",
                                   env=env, notes=r"incompatible|^e: |^error: |Could not resolve|SDK location not found")
    info["source"] = "scip-java"
    info["build_file"] = build
    if path is None:
        notes = sorted(info.get("notes") or [], key=lambda x: "incompatible" not in x)   # the cause first
        tail = " ".join(notes + (info.get("stderr_tail") or []))
        last = (notes[0] if notes else
                next((x.strip() for x in reversed(info.get("stderr_tail") or []) if x.strip()), ""))
        last = re.sub(r"file://\S*/", "", last)
        hint = ""
        if "semanticdb_kotlinc" in tail:
            hint = ("; the bundled semanticdb-kotlinc compiler plugin does not load into this Kotlin version "
                    "(docs/kotlin.md#exact-mode)")
        elif "incompatible version of Kotlin" in tail:
            hint = "; dependencies need a newer Kotlin compiler than the build uses (docs/kotlin.md#exact-mode)"
        info["status"] = ("scip-java run failed (" + info.get("error", "?") + (f": {last[:160]}" if last else "")
                          + ")" + hint)
    return path, info


class ExactLayer:
    def __init__(self, plugin):
        self.p = plugin
        self.b = plugin.b

    def apply(self, path: Path, st: dict) -> bool:
        idx = scipread.load(path)
        kdocs = {p: d for p, d in idx.docs.items() if p.endswith((".kt", ".kts"))}
        st["scip_documents"] = len(kdocs)
        if not kdocs:
            return False
        decl_pos = {}
        by_file = defaultdict(list)
        for d in self._all_decls():          # overloads share a node id but keep their own Decl
            decl_pos[(d.file, d.name_line or d.line, d.name)] = d.id
            by_file[d.file].append(d)
        # symbol -> node id
        sym = {}
        matched = unmatched = 0
        # Kotlin properties: the term `C#x.` and its synthetic `getX()` / `setX()` share the definition position
        term_pos = {(rel, o.line, o.col) for s, defs in idx.defs.items() if s.endswith(".") and not s.endswith(").")
                    for rel, o in defs}
        for s, defs in idx.defs.items():
            if s.endswith((")", "]")):       # parameters `m().(x)`, type parameters `C#[T]`
                continue
            for rel, o in defs:
                if rel not in kdocs:
                    continue
                dd = scipread.descriptors(s)
                if not dd or not dd[1]:
                    continue
                name, suf = dd[1][-1]
                nid = decl_pos.get((rel, o.line + 1, name))
                if nid is None and suf in ("(", "#"):
                    # a declaration whose name sits on another line than the tree-sitter node expects
                    for d in by_file.get(rel, ()):
                        if d.name == name and d.line <= o.line + 1 <= d.end and (suf == "#") == (d.kind == "class"):
                            nid = d.id
                            break
                if nid is None:
                    if suf in ("(", "#") and name != "<init>" and (rel, o.line, o.col) not in term_pos:
                        unmatched += 1
                    continue
                sym[s] = nid
                matched += 1
        st["scip_defs_matched"] = matched
        st["scip_defs_unmatched"] = unmatched
        covered = set(kdocs)
        heur = self._remove_heuristic(covered)
        exact = set()
        owners = self._owner_index(covered)
        for rel, doc in kdocs.items():
            # a property access `x.pets` is reported as the term `C#pets.` plus the synthetic accessor `C#getPets().`,
            # which collides with a declared `fun getPets()`: accessors at a property reference are not calls
            prop_at = {(o.line, o.col) for o in doc.occs if not (o.roles & scipread.DEFINITION)
                       and o.symbol.endswith(".") and not o.symbol.endswith(").")}
            for o in doc.occs:
                if o.roles & scipread.DEFINITION:
                    continue
                if o.symbol.endswith((")", "]")) or (o.line, o.col) in prop_at:
                    continue
                dd = scipread.descriptors(o.symbol)
                if not dd or not dd[1]:
                    continue
                name, suf = dd[1][-1]
                if suf != "(":
                    continue
                if name == "<init>":
                    cls_sym = o.symbol[:o.symbol.rfind("`<init>`")] if "`<init>`" in o.symbol else None
                    dst = sym.get(cls_sym) if cls_sym else None
                    kind = "INSTANTIATES"
                else:
                    dst = sym.get(o.symbol)
                    kind = "CALLS"
                if dst is None:
                    st["scip_refs_external"] = st.get("scip_refs_external", 0) + 1
                    continue
                src = self._owner(owners, rel, o.line + 1)
                if src == dst:
                    continue
                self.b.add_edge(src, dst, kind, rel, o.line + 1, EXACT, source="scip")
                if kind == "INSTANTIATES":
                    self.b.add_edge(src, dst, "USES_TYPE", rel, o.line + 1, EXACT, how="constructor call", source="scip")
                exact.add((src, dst, kind))
                st["scip_references"] = st.get("scip_references", 0) + 1
        agree = len(heur & exact)
        st["exact_vs_heuristic"] = {
            "heuristic_edges": len(heur), "exact_edges": len(exact), "agree": agree,
            "precision": round(agree / len(heur), 3) if heur else None,
            "recall": round(agree / len(exact), 3) if exact else None}
        seen = set(getattr(self.p, "file_report", {}).get("seen") or covered)
        st["scip_files"] = len({f for f in covered & seen if f.endswith(".kt")})
        return True

    def _remove_heuristic(self, covered: set) -> set:
        """Drop the heuristic call edges made in covered files; returns their (src, dst, kind) set."""
        out = set()
        for key, e in list(self.b.edges.items()):
            if e.confidence != HEURISTIC or e.file not in covered:
                continue
            if e.kind in CALL_KINDS or (e.kind == "USES_TYPE" and e.attrs.get("how") == "constructor call"):
                n = self.b.nodes.get(e.src)
                if n is not None and n.lang == "kotlin":
                    if e.kind in CALL_KINDS:
                        out.add((e.src, e.dst, e.kind))
                    del self.b.edges[key]
        return out

    def _all_decls(self) -> list:
        seen, out = set(), []
        for d in list(self.p.decls.values()) + [d for ds in self.p.by_name.values() for d in ds]:
            if id(d) not in seen:
                seen.add(id(d))
                out.append(d)
        return out

    def _owner_index(self, covered: set) -> dict:
        """Per file, the line ranges of functions / methods / classes (each overload separately) and pages."""
        idx = defaultdict(list)
        for d in self._all_decls():
            if d.file in covered and d.line and d.id in self.b.nodes:
                idx[d.file].append((d.line, d.end or d.line, 0 if d.kind != "class" else 1, d.id))
        decl_ids = {d.id for d in self._all_decls()}
        for nid, n in self.b.nodes.items():      # pages and route-handler lambdas own the calls inside them
            if (n.lang == "kotlin" and n.kind in ("page", "function") and nid not in decl_ids and n.file in covered
                    and n.line):
                idx[n.file].append((n.line, n.end_line or n.line, 0, nid))
        return idx

    def _owner(self, owners: dict, rel: str, line: int) -> str:
        best = None
        for sl, el, cls, nid in owners.get(rel, ()):
            if sl <= line <= el:
                k = (cls, el - sl)
                if best is None or k < best[0]:
                    best = (k, nid)
        return best[1] if best else f"file:kotlin:{rel}"

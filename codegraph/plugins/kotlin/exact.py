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

Mixed Kotlin / Java modules: the Java documents of the same index (scip-java indexes both) are imported too, since
the generic SCIP importer skips an index this layer consumed. Java classes and methods become `java` nodes (ids from
the generic importer's symbol mapping, codegraph/plugins/scip/importer.py), Kotlin -> Java and Java -> Kotlin / Java
calls and constructor calls become exact edges, the Java caller being the innermost Java method / class around the
reference (SCIP enclosing ranges). `stats["java"]` counts them.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from ...core.model import EXACT, HEURISTIC
from ..native import runner, scipread

BUILD_FILES = ("build.gradle.kts", "build.gradle", "settings.gradle.kts", "settings.gradle", "pom.xml")
CALL_KINDS = ("CALLS", "INSTANTIATES")
# compiler-generated members with no declaration of their own: data class `copy` / `componentN`, enum `values` /
# `valueOf` / `entries` (`<init>` and `<anonymous object at ...>` start with "<")
SYNTHETIC = re.compile(r"copy|component\d+|values|valueOf|entries")
FACADE = re.compile(r"(?<=/)\w+Kt#(?=[^#]+\.$)")   # the JVM facade class of a Kotlin file's top-level functions
ACCESSOR = re.compile(r"[gs]et[A-Z]\w*")                # a property accessor's JVM name


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
    tools = scip_java_candidates()
    java = find_java()
    if not build:
        return None, {"status": "no Gradle / Maven build file at the project root"}
    if not java:
        return None, {"status": "no JDK (java not on PATH, JAVA_HOME unset)"}
    if not tools:
        return None, {"status": "scip-java not installed (docs/kotlin.md#exact-mode)"}
    if os.environ.get("CODEGRAPH_KOTLIN_SCIP") != "1":
        return None, {"status": "scip-java found but not run: indexing runs the Gradle / Maven build (the project's "
                                "build scripts); set CODEGRAPH_KOTLIN_SCIP=1 or pass --scip", "indexer": tools[0]}
    env = dict(os.environ)          # the JDK found for the run, without changing this process's environment
    jbin = str(Path(java).parent)
    if jbin not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = jbin + os.pathsep + env.get("PATH", "")
    env.setdefault("JAVA_HOME", str(Path(java).resolve().parent.parent))
    kv = kotlin_version(root)
    andr = android_modules(root)
    if len(tools) > 1:              # several scip-java releases: the one whose compiler plugin fits the build first
        tools.sort(key=lambda t: not _supports(_generation(t), kv))
    srcs = list(files) + [b for b in BUILD_FILES if (root / b).exists()]
    timeout = int(os.environ.get("CODEGRAPH_INDEXER_TIMEOUT", "3600"))
    attempts = []
    for i, tool in enumerate(tools):
        key = runner.fingerprint(root, srcs, f"scip-java|{tool}|{runner.tool_version(tool, ('version',))}")
        path, info = runner.run_cached("kotlin", key, [tool, "index"], root, "--output", timeout, out_flag_style="eq",
                                       env=env, notes=r"incompatible|^e: |^error: |Could not resolve|"
                                                      r"SDK location not found|NoSuchMethodError|AbstractMethodError|"
                                                      r"prefer settings repositories")
        info.update(source="scip-java", build_file=build, indexer=tool)
        if andr:
            info["android_modules"] = andr
        if kv:
            info["kotlin_version"] = ".".join(map(str, kv))
        if path is not None:
            if attempts:
                info["attempts"] = attempts
            return path, info
        tail = " ".join((info.get("notes") or []) + (info.get("stderr_tail") or []))
        mismatch = _plugin_mismatch(tail)
        attempts.append({"indexer": tool, "error": info.get("error", "?"), "plugin_mismatch": mismatch})
        if not (mismatch and i + 1 < len(tools)):
            break
    notes = sorted(info.get("notes") or [], key=_note_rank)   # the cause first
    tail = " ".join(notes + (info.get("stderr_tail") or []))
    last = (notes[0] if notes else
            next((x.strip() for x in reversed(info.get("stderr_tail") or []) if x.strip()), ""))
    last = re.sub(r"file://\S*/", "", last)
    hint = ""
    if _plugin_mismatch(tail):
        hint = "; the scip-java Kotlin compiler plugin does not load into this Kotlin version"
        if kv and kv >= (2, 2, 20):
            hint += (f" ({'.'.join(map(str, kv))}: no released scip-java supports it yet; 0.12 covers Kotlin <= 2.1, "
                     "0.13 covers 2.2.0 - 2.2.10)")
        elif kv and kv >= (2, 2):
            hint += f" ({'.'.join(map(str, kv))} needs scip-java 0.13)"
        elif kv:
            hint += f" ({'.'.join(map(str, kv))} needs scip-java 0.12)"
        hint += " (docs/kotlin.md#exact-mode)"
    elif "incompatible version of Kotlin" in tail:
        hint = "; dependencies need a newer Kotlin compiler than the build uses (docs/kotlin.md#exact-mode)"
    elif "prefer settings repositories" in tail:
        hint = ("; the build's settings forbid project repositories (dependencyResolutionManagement "
                "FAIL_ON_PROJECT_REPOS) and scip-java's Gradle plugin adds one (docs/kotlin.md#exact-mode)")
    if andr:
        hint += (f"; Android modules ({', '.join(andr[:5])}) need the Android SDK (ANDROID_HOME) and scip-java's "
                 "Gradle plugin compiles no Android variant")
    info["status"] = ("scip-java run failed (" + info.get("error", "?") + (f": {last[:160]}" if last else "")
                      + ")" + hint)
    if len(attempts) > 1:
        info["attempts"] = attempts
    return None, info


def scip_java_candidates() -> list[str]:
    """scip-java launchers: $CODEGRAPH_SCIP_JAVA (one path, or several joined by os.pathsep), else `scip-java` on
    PATH / in the tool directories plus versioned installs beside it (`scip-java-0.13.1/scip-java`, `scip-java-0.13.1`),
    since each release's Kotlin compiler plugin loads into a narrow range of Kotlin versions."""
    v = os.environ.get("CODEGRAPH_SCIP_JAVA")
    if v:
        return [p for p in v.split(os.pathsep) if p and (Path(p).exists() or shutil.which(p))]
    out = []
    home = Path.home()
    dirs = [home / "tools", home / ".local" / "bin", home / ".local" / "share" / "coursier" / "bin"]
    main = runner.find_tool("CODEGRAPH_SCIP_JAVA", ["scip-java"], dirs)
    if main:
        out.append(main)
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("scip-java-*/scip-java")) + sorted(d.glob("scip-java-*")):
            if p.is_file() and os.access(p, os.X_OK):
                out.append(str(p))
    seen, uniq = set(), []
    for p in out:
        r = os.path.realpath(p)
        if r not in seen:
            seen.add(r)
            uniq.append(p)
    return uniq


_GEN: dict = {}


def _generation(tool: str) -> int:
    """13 for scip-java >= 0.13 (the Kotlin rewrite: `aggregate` command, SCIP shards), else 12."""
    if tool not in _GEN:
        try:
            r = subprocess.run([tool, "--help"], capture_output=True, text=True, timeout=120)
            _GEN[tool] = 13 if "aggregate" in (r.stdout + r.stderr) else 12
        except Exception:
            _GEN[tool] = 12
    return _GEN[tool]


def _supports(gen: int, kv: tuple | None) -> bool:
    """Kotlin versions whose compiler the release's Kotlin plugin loads into (checked on Gradle builds): 0.12.x
    bundles semanticdb-kotlinc for Kotlin <= 2.1; 0.13.x (built against 2.2.0) works on 2.2.0 - 2.2.10, and fails
    on 2.1 (AbstractMethodError), 2.2.20+ (NoSuchMethodError) and 2.3+ (incompatible registrar)."""
    if kv is None:
        return gen == 12
    if gen >= 13:
        return (2, 2) <= kv < (2, 2, 20)
    return kv < (2, 2)


def _note_rank(line: str) -> int:
    if re.search(r"is incompatible with the current|NoSuchMethodError|AbstractMethodError|SDK location not found|"
                 r"prefer settings repositories", line):
        return 0
    return 1 if "incompatible" in line and "Deprecated Gradle" not in line else 2


def _plugin_mismatch(text: str) -> bool:
    return bool(re.search(r"semanticdb_kotlinc|kotlinc\.AnalyzerRegistrar|NoSuchMethodError: '[^']*kotlin|"
                          r"AbstractMethodError: [^\n]*kotlin", text))


_KV = [re.compile(r'kotlin\(\s*"[\w-]+"\s*\)\s*version\s*"(\d+\.\d+(?:\.\d+)?)'),
       re.compile(r'id\s*\(?\s*["\']org\.jetbrains\.kotlin\.[\w.-]+["\']\s*\)?\s*version\s*["\'](\d+\.\d+(?:\.\d+)?)'),
       re.compile(r'kotlin-gradle-plugin:(\d+\.\d+(?:\.\d+)?)'),
       re.compile(r'<kotlin\.version>(\d+\.\d+(?:\.\d+)?)'),
       re.compile(r'^\s*kotlin(?:[-_]?[vV]ersion)?\s*=\s*["\']?(\d+\.\d+(?:\.\d+)?)', re.M)]
KV_FILES = ("build.gradle.kts", "build.gradle", "settings.gradle.kts", "settings.gradle", "gradle/libs.versions.toml",
            "gradle.properties", "pom.xml", "buildSrc/build.gradle.kts")


ANDROID_PLUGIN = re.compile(r"com\.android\.(?:application|library|dynamic-feature|test)|"
                            r"plugins\.android\.(?:application|library|dynamic\.feature|test)\b")


def android_modules(root: Path) -> list[str]:
    """Gradle modules (up to two levels deep) that apply the Android Gradle plugin."""
    out = []
    for pat in ("*/build.gradle.kts", "*/build.gradle", "*/*/build.gradle.kts", "*/*/build.gradle"):
        for f in sorted(root.glob(pat)):
            rel = f.parent.relative_to(root).as_posix()
            if rel.startswith(("build", ".", "buildSrc")) or rel in out:
                continue
            try:
                if ANDROID_PLUGIN.search(f.read_text(errors="replace")):
                    out.append(rel)
            except OSError:
                pass
    return out


def skipped_modules(modules: list[str] | None, doc_paths) -> list[dict]:
    """Android modules none of whose sources the index contains."""
    paths = list(doc_paths)
    return [{"module": m, "reason": "Android module: scip-java's Gradle plugin compiles no Android variant "
                                    "(docs/kotlin.md#exact-mode)"}
            for m in modules or () if not any(p.startswith(m + "/") for p in paths)]


def kotlin_version(root: Path) -> tuple | None:
    """The Kotlin (Gradle plugin) version the build declares, from the root build files / version catalog."""
    for f in KV_FILES:
        try:
            t = (root / f).read_text(errors="replace")
        except OSError:
            continue
        for rx in _KV:
            m = rx.search(t)
            if m:
                return tuple(int(x) for x in m.group(1).split("."))
    return None


class ExactLayer:
    def __init__(self, plugin):
        self.p = plugin
        self.b = plugin.b

    def apply(self, path: Path, st: dict) -> bool:
        idx = scipread.load(path)
        self.doc_paths = list(idx.docs)
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
                if nid is None and suf == "(" and ACCESSOR.fullmatch(name):
                    # an explicit `get()` / `set(v)` accessor: scip-java defines `getX().` at the keyword (0.13)
                    pn = name[3].lower() + name[4:]
                    for d in by_file.get(rel, ()):
                        if d.name == pn and d.id in self.p.props and d.line <= o.line + 1 <= d.end:
                            nid = d.id
                            break
                if nid is None:
                    if (suf in ("(", "#") and not name.startswith("<") and not SYNTHETIC.fullmatch(name)
                            and (rel, o.line, o.col) not in term_pos):
                        unmatched += 1
                        if len(st.setdefault("scip_defs_unmatched_samples", [])) < 10:
                            st["scip_defs_unmatched_samples"].append(f"{rel}:{o.line + 1}:{o.col + 1} {name}{suf}")
                    continue
                sym[s] = nid
                matched += 1
        st["scip_defs_matched"] = matched
        st["scip_defs_unmatched"] = unmatched
        w = scipread.health_warning(Path(path).name, idx.occurrences, idx.positioned, None if not matched + unmatched
                                    else matched + unmatched, matched, "the Kotlin declarations")
        if w:
            st["scip_warning"] = w
        jdocs = {p: d for p, d in idx.docs.items() if p.endswith(".java")}
        jowners = self._java_nodes(jdocs, sym, st) if jdocs else {}
        covered = set(kdocs)
        heur = self._remove_heuristic(covered)
        exact = set()
        owners = self._owner_index(covered)
        owners.update(jowners)
        for rel, doc in list(kdocs.items()) + list(jdocs.items()):
            java = rel in jdocs
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
                    if dst is None and java:     # a top-level Kotlin function seen from Java: `pkg/FileKt#f().`
                        dst = sym.get(FACADE.sub("", o.symbol))
                    kind = "CALLS"
                if dst is None:
                    if java:
                        st["java"]["references_external"] += 1
                    else:
                        st["scip_refs_external"] = st.get("scip_refs_external", 0) + 1
                    continue
                src = self._owner(owners, rel, o.line + 1)
                if src is None or src == dst:
                    continue
                self.b.add_edge(src, dst, kind, rel, o.line + 1, EXACT, source="scip")
                if kind == "INSTANTIATES":
                    self.b.add_edge(src, dst, "USES_TYPE", rel, o.line + 1, EXACT, how="constructor call", source="scip")
                if java:
                    st["java"]["references"] += 1
                    continue
                exact.add((src, dst, kind))
                st["scip_references"] = st.get("scip_references", 0) + 1
        agree = len(heur & exact)
        st["exact_vs_heuristic"] = {
            "heuristic_edges": len(heur), "exact_edges": len(exact), "agree": agree,
            "precision": round(agree / len(heur), 3) if heur else None,
            "recall": round(agree / len(exact), 3) if exact else None,
            # candidate edges (receiver type unknown, one per same-name method, #83) are compared on their own
            "candidate_edges": len(self.candidates), "candidate_agree": len(self.candidates & exact)}
        seen = set(getattr(self.p, "file_report", {}).get("seen") or covered)
        st["scip_files"] = len({f for f in covered & seen if f.endswith(".kt")})
        return True

    def _java_nodes(self, jdocs: dict, sym: dict, st: dict) -> dict:
        """Nodes for the Java classes / methods the index defines (added to `sym`); returns their owner ranges."""
        from ..scip.importer import to_node
        js = st["java"] = {"documents": len(jdocs), "classes": 0, "methods": 0, "references": 0,
                           "references_external": 0}
        owners = defaultdict(list)
        for rel, doc in jdocs.items():
            fid = self.b.add_node("file", f"java:{rel}", name=rel, file=rel, line=1, lang="java")
            for o in doc.occs:
                if not (o.roles & scipread.DEFINITION) or o.symbol.endswith((")", "]")):
                    continue
                n = to_node(o.symbol, "java")
                if not n or n[0] not in ("class", "method") or n[2] == "<init>":
                    continue
                kind, key, name = n
                sl, el = (o.enclosing[0] + 1, o.enclosing[1] + 1) if o.enclosing else (o.line + 1, o.line + 1)
                pkg = key.rsplit(".", 1)[0] if kind == "class" and "." in key else None
                nid = self.b.add_node(kind, key, name=name, fqn=key, file=rel, line=o.line + 1, end_line=el,
                                      lang="java", module=pkg, attrs={"scip_symbol": o.symbol, "source": "scip"})
                sym[o.symbol] = nid
                js["classes" if kind == "class" else "methods"] += 1
                parent = f"class:{key.split('::')[0]}" if kind == "method" else None
                outer = to_node(o.symbol[:o.symbol.rstrip('#').rfind('#') + 1], "java") if kind == "class" else None
                if parent is None and outer and outer[0] == "class" and outer[1] != key:
                    parent = f"class:{outer[1]}"
                self.b.add_edge(parent or fid, nid, "CONTAINS", rel, o.line + 1, EXACT)
                owners[rel].append((sl, el, 1 if kind == "class" else 0, nid))
            owners.setdefault(rel, [])
        return owners

    def _remove_heuristic(self, covered: set) -> set:
        """Drop the heuristic call edges made in covered files; returns their (src, dst, kind) set, without the
        candidate edges (self.candidates)."""
        out, cand = set(), set()
        for key, e in list(self.b.edges.items()):
            if e.confidence != HEURISTIC or e.file not in covered:
                continue
            if e.attrs.get("property"):
                continue      # property reads / writes (#89): scip-java reports them as accessors, not mapped yet

            if e.kind in CALL_KINDS or (e.kind == "USES_TYPE" and e.attrs.get("how") == "constructor call"):
                n = self.b.nodes.get(e.src)
                if n is not None and n.lang == "kotlin":
                    if e.kind in CALL_KINDS:
                        (cand if e.attrs.get("binding") == "candidate" else out).add((e.src, e.dst, e.kind))
                    del self.b.edges[key]
        self.candidates = cand
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
        if best:
            return best[1]
        return f"file:java:{rel}" if rel.endswith(".java") else f"file:kotlin:{rel}"

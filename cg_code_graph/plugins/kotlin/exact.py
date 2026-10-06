"""Kotlin exact layer: compiler-resolved references from a scip-java SCIP index (SemanticDB compiler plugin).

Where the index comes from, in order:
  1. a `--scip` file whose documents include this plugin's language (`.kt` / `.kts` or `.java`);
  2. $CG_JAVA_SCIP_FILE (a missing path is an error and does not fall through);
  3. $CG_KOTLIN_SCIP_FILE;
  4. one scip-java run when $CG_JAVA_SCIP=1 or $CG_KOTLIN_SCIP=1 (opt-in: indexing runs the build,
     which executes the project's build scripts, and writes build/ output into the project). The run is cached
     once per project root, like the Rust / C indexers (cg_code_graph/plugins/native/runner.py), and both plugins
     read it.
Without an index the heuristic layer stays; `stats["scip"]` says why (no JDK, no scip-java, not opted in, the run
failed), and `cg coverage` reports it.

Mapping: SCIP definitions are matched to the tree-sitter declarations by (file, line of the name, name), so node ids
do not change. Every reference to a project method / function becomes a CALLS edge (a constructor reference
`Foo#<init>().` an INSTANTIATES edge) from the innermost declaration around it (a Ktor route handler lambda or
Compose page, else the function / method, else the class, else the file), confidence exact. A callable reference
(`recv::fn`, `::fn`, `Type::fn`, `::Foo`) is a REFERENCES_FN edge (`how: callback`) instead. The heuristic call
and callable-reference edges of the files the index covers are replaced; files it does not cover keep them. The
overlap of both layers is kept as precision / recall of the heuristic layer against the exact one
(`stats["exact_vs_heuristic"]`).

Mixed Kotlin / Java modules: the Java documents of the same index (scip-java indexes both) are imported too, since
the generic SCIP importer skips an index this layer consumed. Classes, methods, constructors and fields use the Java
plugin's node ids (`method:pkg.Foo.bar`, `constructor:pkg.Foo.<init>`, `field:pkg.Foo.x`), not a parallel
`method:pkg.Foo::bar` id. When the Java plugin already created the node, this layer only records the symbol. Kotlin ->
Java and Java -> Kotlin / Java calls and constructor calls become exact edges, the Java caller being the innermost
Java method / class around the reference (SCIP enclosing ranges). `stats["java"]` counts them.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from ...core.model import EXACT, HEURISTIC
from ...core.env import get as cg_env
from ..native import runner, scipread

BUILD_FILES = ("build.gradle.kts", "build.gradle", "settings.gradle.kts", "settings.gradle", "pom.xml")
CALL_KINDS = ("CALLS", "INSTANTIATES")
# compiler-generated members with no declaration of their own: data class `copy` / `componentN`, enum `values` /
# `valueOf` / `entries` (`<init>` and `<anonymous object at ...>` start with "<")
SYNTHETIC = re.compile(r"copy|component\d+|values|valueOf|entries")
FACADE = re.compile(r"(?<=/)\w+Kt#(?=[^#]+\.$)")   # the JVM facade class of a Kotlin file's top-level functions
ACCESSOR = re.compile(r"[gs]et[A-Z]\w*")                # a property accessor's JVM name


_LANG_EXT = {"kotlin": (".kt", ".kts"), "java": (".java",)}
_FINGERPRINT_SKIP = {
    ".git", "build", "target", ".gradle",
    "node_modules", "out", ".idea",
}


def _has_lang_docs(path: str, lang: str) -> bool:
    try:
        idx = scipread.load(path)
    except Exception:
        return False
    return any(p.endswith(_LANG_EXT[lang]) for p in idx.docs)


def _consume(project, path: str, lang: str) -> None:
    consumed = project.options.setdefault("scip_consumed", [])
    if path not in consumed:
        consumed.append(path)
    by = project.options.setdefault("scip_consumed_by", {})
    langs = by.setdefault(path, [])
    if lang not in langs:
        langs.append(lang)


def jvm_fingerprint_files(root: Path) -> list[str]:
    """Sources and build files both plugins hash, so one project root shares one runner-cache entry."""
    out = []
    if not root.is_dir():
        return out
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in _FINGERPRINT_SKIP and not d.startswith("."))
        rel = os.path.relpath(dp, root).replace(os.sep, "/")
        rel = "" if rel == "." else rel
        for f in sorted(fns):
            if f.endswith((".java", ".kt", ".kts", ".xml", ".toml", ".properties")) or f in BUILD_FILES:
                out.append(f"{rel}/{f}" if rel else f)
    return out


def java_major(java: str) -> int | None:
    """Major version of a `java` binary (`1.8` is 8), or None when the binary does not print one."""
    try:
        r = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'version "(?:1\.)?(\d+)', (r.stderr or "") + (r.stdout or ""))
    return int(m.group(1)) if m else None


_BUILD_JDK = re.compile(
    r"(?:sourceCompatibility|targetCompatibility|jvmTarget|maven\.compiler\.(?:source|target|release)|"
    r"java\.version|release)\s*[=:>]\s*['\"]?(1\.8|8|11)\b")


def declared_build_jdk(root: Path) -> int | None:
    """8 or 11 when a root build file sets the compilation JDK to one scip-java does not support."""
    for f in ("pom.xml", "build.gradle", "build.gradle.kts", "gradle.properties"):
        try:
            t = (root / f).read_text(errors="replace")
        except OSError:
            continue
        m = _BUILD_JDK.search(t)
        if m:
            return 8 if m.group(1) in ("1.8", "8") else 11
    return None


def find_java() -> str | None:
    jh = os.environ.get("JAVA_HOME")
    if jh and (Path(jh) / "bin" / "java").exists():
        return str(Path(jh) / "bin" / "java")
    return shutil.which("java")


def find_index(project, files: list[str], lang: str = "kotlin") -> tuple[Path | None, dict]:
    """(SCIP path or None, info). info["status"] explains a missing index.

    `--scip` is chosen per language (a file with `.kt` / `.kts` documents for Kotlin, `.java` for Java).
    A prebuilt file or a scip-java run is shared: `CG_JAVA_SCIP_FILE` wins over `CG_KOTLIN_SCIP_FILE` when
    both are set; either `CG_JAVA_SCIP=1` or `CG_KOTLIN_SCIP=1` starts the one run, cached per project root.
    The second plugin reads that result instead of starting another build.
    """
    for s in project.options.get("scip") or []:
        if _has_lang_docs(s, lang):
            _consume(project, s, lang)
            return Path(s), {"source": "--scip", "path": str(s)}
    shared = project.options.get("jvm_scip_shared")
    if shared is not None:
        path, info = shared
        return path, dict(info)
    path, info = _find_shared(project, files)
    project.options["jvm_scip_shared"] = (path, dict(info))
    return path, dict(info)


def _find_shared(project, files: list[str]) -> tuple[Path | None, dict]:
    """The prebuilt index or the one scip-java run both JVM plugins share."""
    java_file = cg_env("JAVA_SCIP_FILE")
    if java_file:
        if Path(java_file).exists():
            return Path(java_file), {"source": "CG_JAVA_SCIP_FILE", "path": java_file}
        return None, {"status": f"CG_JAVA_SCIP_FILE={java_file} does not exist"}
    pre = cg_env("KOTLIN_SCIP_FILE")
    if pre:
        if Path(pre).exists():
            return Path(pre), {"source": "CG_KOTLIN_SCIP_FILE", "path": pre}
        return None, {"status": f"CG_KOTLIN_SCIP_FILE={pre} does not exist"}
    root = Path(project.root)
    build = next((b for b in BUILD_FILES if (root / b).exists()), None)
    tools = scip_java_candidates()
    java = find_java()
    if not build:
        return None, {"status": "no Gradle / Maven build file at the project root"}
    if not java:
        return None, {"status": "no JDK (java not on PATH, JAVA_HOME unset)"}
    if not tools:
        if cg_env("JAVA_SCIP") == "1" or cg_env("KOTLIN_SCIP") == "1":
            return None, {"status": "scip-java not installed; CG_JAVA_SCIP=1 / CG_KOTLIN_SCIP=1 left exact mode off "
                                    "(install.sh --with java, JDK 17, 21 or 25)"}
        return None, {"status": "scip-java not installed (docs/kotlin.md#exact-mode)"}
    if cg_env("KOTLIN_SCIP") != "1" and cg_env("JAVA_SCIP") != "1":
        return None, {"status": "scip-java found but not run: indexing runs the Gradle / Maven build (the project's "
                                "build scripts); set CG_KOTLIN_SCIP=1 or CG_JAVA_SCIP=1 or pass --scip",
                      "indexer": tools[0]}
    major = java_major(java)
    if major is not None and major < 17:
        status = f"JDK too old (found {major}; scip-java needs JDK 17, 21 or 25)"
        if major in (8, 11):
            status += "; unsupported Java 8/11 build JDK"
        return None, {"status": status, "indexer": tools[0]}
    env = dict(os.environ)          # the JDK found for the run, without changing this process's environment
    jbin = str(Path(java).parent)
    if jbin not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = jbin + os.pathsep + env.get("PATH", "")
    env.setdefault("JAVA_HOME", str(Path(java).resolve().parent.parent))
    kv = kotlin_version(root)
    andr = android_modules(root)
    if len(tools) > 1:              # several scip-java releases: the one whose compiler plugin fits the build first
        tools.sort(key=lambda t: not _supports(_generation(t), kv))
    srcs = jvm_fingerprint_files(root) or list(files)
    timeout = int(cg_env("INDEXER_TIMEOUT", "3600"))
    attempts = []
    for i, tool in enumerate(tools):
        key = runner.fingerprint(root, srcs, scip_cache_extra(tool, java, root, build))
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
    bjdk = declared_build_jdk(root)
    if bjdk in (8, 11):
        hint += f"; unsupported Java {bjdk} build JDK (scip-java indexes with JDK 17, 21 or 25)"
    info["status"] = ("scip-java run failed (" + info.get("error", "?") + (f": {last[:160]}" if last else "")
                      + ")" + hint)
    if len(attempts) > 1:
        info["attempts"] = attempts
    return None, info


def scip_cache_extra(tool: str, java: str, root: Path, build: str) -> str:
    """Cache-key material besides the sources: the scip-java binary, the JDK, and the build tool.

    A different JDK or a switch between Maven and Gradle must miss the cache. The source list already
    hashes the build files themselves.
    """
    return (f"scip-java|{tool}|{runner.tool_version(tool, ('version',))}"
            f"|jdk={java}|{java_major(java)}|build={build}|bjdk={declared_build_jdk(root)}")


def scip_java_candidates() -> list[str]:
    """scip-java launchers: $CG_SCIP_JAVA (one path, or several joined by os.pathsep), else `scip-java` on
    PATH / in the tool directories plus versioned installs beside it (`scip-java-0.13.1/scip-java`, `scip-java-0.13.1`),
    since each release's Kotlin compiler plugin loads into a narrow range of Kotlin versions."""
    v = cg_env("SCIP_JAVA")
    if v:
        return [p for p in v.split(os.pathsep) if p and (Path(p).exists() or shutil.which(p))]
    out = []
    home = Path.home()
    dirs = [home / "tools", home / ".local" / "bin", home / ".local" / "share" / "coursier" / "bin"]
    main = runner.find_tool("CG_SCIP_JAVA", ["scip-java"], dirs)
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
    if re.search(r"JDK too old|unsupported Java (?:8|11)|is incompatible with the current|NoSuchMethodError|"
                 r"AbstractMethodError|SDK location not found|prefer settings repositories", line):
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
                    if dst is None and java:     # @file:JvmName("WidgetKit") does not end in `Kt`
                        dst = self._jvm_name_facade(o.symbol, sym)
                    if dst is None and java:
                        acc = self._java_accessor(o.symbol)
                        if acc is not None:
                            src = self._owner(owners, rel, o.line + 1)
                            if src is not None and src != acc[0]:
                                self._upgrade(src, acc[0], acc[1], rel, o.line + 1)
                                st["java"]["references"] += 1
                            continue
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
                fnref = not java and self._is_fn_ref(rel, o)
                if fnref:
                    kind = "REFERENCES_FN"
                if kind == "REFERENCES_FN":
                    self.b.add_edge(src, dst, kind, rel, o.line + 1, EXACT, source="scip", how="callback")
                else:
                    self.b.add_edge(src, dst, kind, rel, o.line + 1, EXACT, source="scip")
                if kind == "INSTANTIATES":
                    self.b.add_edge(src, dst, "USES_TYPE", rel, o.line + 1, EXACT, how="constructor call", source="scip")
                if java:
                    st["java"]["references"] += 1
                    continue
                if kind in CALL_KINDS:
                    exact.add((src, dst, kind))
                st["scip_references"] = st.get("scip_references", 0) + 1
        agree = len(heur & exact)
        st["exact_vs_heuristic"] = {
            "heuristic_edges": len(heur), "exact_edges": len(exact), "agree": agree,
            "precision": round(agree / len(heur), 3) if heur else None,
            "recall": round(agree / len(exact), 3) if exact else None,
            # candidate edges (receiver type unknown, one per same-name method, #83) are compared on their own
            "candidate_edges": len(self.candidates), "candidate_agree": len(self.candidates & exact)}
        rep = getattr(self.p, "file_report", None) or {}
        seen = set(getattr(self.p, "_seen_files", None) or rep.get("seen") or covered)
        st["scip_files"] = len({f for f in covered & seen if f.endswith(".kt")})
        return True

    def _java_accessor(self, symbol: str) -> tuple[str, str] | None:
        from ..java.exact import kotlin_accessor_target
        return kotlin_accessor_target(symbol, self.b.nodes)

    def _upgrade(self, src: str, dst: str, kind: str, file: str, line: int) -> None:
        """Replace a heuristic property edge in a covered file with the exact accessor edge."""
        key = (src, dst, kind, file, line, None)
        e = self.b.edges.get(key)
        if e is not None:
            e.confidence = EXACT
            e.attrs["source"] = "scip"
            return
        self.b.add_edge(src, dst, kind, file, line, EXACT, source="scip", binding="kotlin-accessor")

    def _jvm_name_facade(self, symbol: str, sym: dict) -> str | None:
        """`pkg/JvmName#fn().` → the top-level `pkg/fn().` when `JvmName` is a Kotlin file's JVM facade."""
        dd = scipread.descriptors(symbol)
        if not dd or not dd[1]:
            return None
        names = [n for n in dd[1] if n[1] != ")"]
        if len(names) < 2 or names[-1][1] != "(" or names[-2][1] != "#":
            return None
        facade = names[-2][0]
        if not any(n.lang == "kotlin" and n.kind == "file" and n.attrs.get("jvm_name") == facade
                   for n in self.b.nodes.values()):
            return None
        return sym.get(symbol.replace(f"/{facade}#", "/", 1))

    def _java_nodes(self, jdocs: dict, sym: dict, st: dict) -> dict:
        """Map Java symbols onto the Java plugin's node ids (added to `sym`); returns their owner ranges.

        A node the Java plugin already created is reused. One that exists only in the index (the Java plugin did
        not index that file) is added with the same id scheme, including fields.
        """
        from ..java.exact import bind_java_symbol, is_anon_id, java_ids
        js = st["java"] = {"documents": len(jdocs), "classes": 0, "methods": 0, "fields": 0, "references": 0,
                           "references_external": 0}
        owners = defaultdict(list)
        for rel, doc in jdocs.items():
            fid = f"file:java:{rel}"
            if fid not in self.b.nodes:
                self.b.add_node("file", f"java:{rel}", name=rel, file=rel, line=1, lang="java")
            for o in doc.occs:
                if not (o.roles & scipread.DEFINITION) or o.symbol.endswith((")", "]")):
                    continue
                mapped = bind_java_symbol(o.symbol, self.b.nodes, rel, o.line + 1)
                if mapped is None:
                    raw = java_ids(o.symbol)
                    if raw and is_anon_id(raw[0]):
                        continue
                    mapped = raw
                if not mapped:
                    continue
                nid, kind = mapped
                if is_anon_id(nid):
                    continue
                if kind == "package":
                    sym[o.symbol] = nid
                    continue
                if kind == "constructor":
                    sym[o.symbol] = nid
                    sl, el = (o.enclosing[0] + 1, o.enclosing[1] + 1) if o.enclosing else (o.line + 1, o.line + 1)
                    owners[rel].append((sl, el, 0, nid))
                    continue
                if kind == "enum_case":
                    sym[o.symbol] = nid
                    if nid not in self.b.nodes:
                        key = nid.split(":", 1)[1]
                        self.b.add_node("enum_case", key, name=key.rsplit(".", 1)[-1], fqn=key, file=rel,
                                        line=o.line + 1, lang="java", attrs={"scip_symbol": o.symbol, "source": "scip"})
                    continue
                if kind == "class":
                    js["classes"] += 1
                elif kind == "method":
                    js["methods"] += 1
                elif kind == "field":
                    js["fields"] += 1
                else:
                    continue
                sl, el = (o.enclosing[0] + 1, o.enclosing[1] + 1) if o.enclosing else (o.line + 1, o.line + 1)
                fresh = nid not in self.b.nodes
                key = nid.split(":", 1)[1]
                name = key.rsplit(".", 1)[-1]
                pkg = key.rsplit(".", 1)[0] if "." in key else None
                self.b.add_node(kind, key, name=name, fqn=key, file=rel, line=o.line + 1, end_line=el,
                                lang="java", module=pkg, attrs={"scip_symbol": o.symbol, "source": "scip"})
                sym[o.symbol] = nid
                if fresh:
                    parent = None
                    if kind != "class" and "." in key:
                        parent = f"class:{key.rsplit('.', 1)[0]}"
                    elif kind == "class" and key.count(".") >= 1:
                        outer = f"class:{key.rsplit('.', 1)[0]}"
                        if outer in self.b.nodes:
                            parent = outer
                    self.b.add_edge(parent or fid, nid, "CONTAINS", rel, o.line + 1, EXACT)
                if kind in ("class", "method"):
                    owners[rel].append((sl, el, 1 if kind == "class" else 0, nid))
            if rel not in owners:
                for n in self.b.nodes.values():
                    if n.lang == "java" and n.file == rel and n.kind in ("method", "constructor", "class") and n.line:
                        owners[rel].append((n.line, n.end_line or n.line, 1 if n.kind == "class" else 0, n.id))
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
            if e.kind == "REFERENCES_FN" and e.attrs.get("how") == "callback":
                n = self.b.nodes.get(e.src)
                if n is not None and n.lang == "kotlin":
                    del self.b.edges[key]
                continue

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

    def _is_fn_ref(self, rel: str, o) -> bool:
        """True when the occurrence sits on a callable reference (`::name` / `recv::name`)."""
        kf = getattr(self.p, "_kf_by_rel", {}).get(rel)
        if kf is None:
            return False
        lines = kf.src.splitlines()
        if o.line < 0 or o.line >= len(lines):
            return False
        line = lines[o.line]
        col = o.col
        if col >= 2 and line[col - 2:col] == b"::":
            return True
        return col < len(line) and line[col:col + 2] == b"::"

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

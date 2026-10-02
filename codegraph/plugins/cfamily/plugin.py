"""C / C++ language plugin.

Exact mode: scip-clang over a compile database (compile_commands.json). Every reference is resolved by clang with
the project's real flags; definitions are matched to syntactic items by (file, line, name), and scip-clang's
relationships give virtual-dispatch edges (base method -> override) and class inheritance.
Heuristic mode (no compile database, or scip-clang not installed): name-based resolution over the tree-sitter
layer; every resolved reference is labelled `heuristic`.

The compile database is looked up at $CODEGRAPH_COMPDB, <root>/compile_commands.json, <root>/build*/,
<root>/out/, <root>/cmake-build-*/. Generate one with `cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` or
`bear -- make`. See docs/native.md.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import time
from collections import defaultdict
from pathlib import Path

from ... import presets
from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...core.fsutil import content_key, keep_file
from ...core.paths import rules as path_rules
from ...core.plugin import FrameworkPlugin, GraphBuilder, LanguagePlugin, Project
from ..native import gates as G
from ..native import runner, scipread
from ..native.ts import TreeSitterMissing
from .syntax import CFile, CItem, annotation_macros, annotation_regex, extract

C_EXT = {".c"}
CPP_EXT = {".cc", ".cpp", ".cxx", ".c++", ".cp", ".C"}
HDR_EXT = {".h", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".inl", ".tcc", ".ixx", ".cuh"}
# vendored third-party trees, test frameworks, build output (codegraph/presets/c_cpp.yaml; .cg.yaml skip_dirs adjusts them)
SKIP_DIRS = presets.skip_dirs("c_cpp")
SKIP_PREFIXES = tuple(presets.values("c_cpp", "skip_dir_prefixes", default=[]))
CODE = {"function", "method"}
TYPES = {"class", "struct", "union", "enum", "typedef"}
VALUES = {"global", "enumerator", "macro"}
SCOPES = CODE | TYPES | {"global", "macro"}
TEST_DIR = re.compile(r"(^|/)(tests?|testing|unittests?|gtest|spec|check)(/|$)", re.I)
TEST_FILE = re.compile(r"(^|/)(test_[^/]*|[^/]*_(unit)?tests?\.[^/]+|[^/]*Tests?\.[^/]+|tests?\.[^/]+)$")
EXAMPLE_DIR = re.compile(r"(^|/)(examples?|samples?|demos?|tutorials?)(/|$)", re.I)
BENCH_DIR = re.compile(r"(^|/)(bench|benches|benchmarks?|perf)(/|$)", re.I)
INCLUDE_DIR = re.compile(r"(^|/)include/")
MAIN_NAMES = {"main", "wmain", "WinMain", "wWinMain", "_tmain"}
# C std / POSIX names: never resolved heuristically to a project function even if the project defines one
LIBC = set("""malloc calloc realloc free memcpy memmove memset memcmp strlen strcmp strncmp strcpy strncpy strcat strdup
printf fprintf sprintf snprintf vsnprintf vfprintf puts fputs fputc fopen fclose fread fwrite fflush exit abort assert
open close read write lseek getenv atoi atol strtol strtoul qsort bsearch time clock sleep usleep pthread_create""".split())


def _skip_dirs() -> set:
    extra = {d.strip() for d in os.environ.get("CODEGRAPH_EXCLUDE_DIRS", "").split(",") if d.strip()}
    keep = {d.strip() for d in os.environ.get("CODEGRAPH_INCLUDE_DIRS", "").split(",") if d.strip()}
    return (SKIP_DIRS | extra) - keep


def source_files(root: Path, limit: int | None = None, project=None):
    rules = path_rules(project, "c_cpp", base=_skip_dirs())
    n = 0
    for dp, dn, fn in os.walk(root):
        rel_dir = Path(dp).relative_to(root).as_posix()
        rd = "" if rel_dir == "." else rel_dir
        dn[:] = [d for d in rules.prune(rd, dn, dot=True) if rules.on_include_path(f"{rd}/{d}" if rd else d)
                 or not (d.startswith(SKIP_PREFIXES) or (Path(dp) / d / "CMakeCache.txt").exists())]
        for f in sorted(fn):
            ext = os.path.splitext(f)[1]
            rel = f if rel_dir == "." else f"{rel_dir}/{f}"
            if (ext in C_EXT or ext in CPP_EXT or ext in HDR_EXT) and keep_file(os.path.join(dp, f)) and not rules.excluded(rel):
                yield rel
                n += 1
                if limit and n >= limit:
                    return


def find_compdb(root: Path) -> Path | None:
    v = os.environ.get("CODEGRAPH_COMPDB")
    if v:
        p = Path(v)
        if p.is_dir():
            p = p / "compile_commands.json"
        return p.resolve() if p.exists() else None
    cands = [root / "compile_commands.json"]
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.is_dir() and (d.name.startswith(("build", "cmake-build", "out")) or d.name in ("builddir", "_build")):
            cands.append(d / "compile_commands.json")
            cands += sorted(d.glob("*/compile_commands.json"))
    return next((c.resolve() for c in cands if c.exists()), None)


def _kind_ok(suffix: str, kind: str) -> bool:
    """SCIP descriptor suffix vs syntactic kind: a namespace (`/`) never matches an item, methods/functions are `(`,
    types `#`, fields / globals / enumerators `.`, macros `!`."""
    if suffix == "(":
        return kind in CODE or kind == "macro"
    if suffix == "#":
        return kind in TYPES or kind in CODE   # constructors of class templates sometimes surface as types
    if suffix == ".":
        return kind in ("field", "global", "enumerator", "typedef") or kind in CODE  # `f(<hash>).` may parse as a term
    if suffix == "!":
        return kind == "macro"
    return False


def scip_cache_key(root: Path, files: list[str], indexer_version: str, compdb: Path) -> str:
    """SCIP cache key: the sources' content plus the compile_commands.json content (flags and defines change the
    index even when no source does)."""
    return runner.fingerprint(root, files, f"{indexer_version}|{compdb}|{content_key(compdb)}")


class CFamilyPlugin(LanguagePlugin):
    name = "c_cpp"

    def detect(self, project: Project) -> bool:
        if os.environ.get("CODEGRAPH_CFAMILY") == "0":
            return False
        root = project.root
        if find_compdb(root):
            return True
        # another language owns the repo (incl. Python/Django and Dart/Flutter, whose desktop runners ship C++ sources)
        other_lang = any(project.exists(m) for m in ("Cargo.toml", "package.json", "composer.json", "go.mod", "pyproject.toml",
                                                     "setup.py", "setup.cfg", "requirements.txt", "manage.py", "Pipfile",
                                                     "pubspec.yaml"))
        if other_lang and os.environ.get("CODEGRAPH_CFAMILY") != "1":
            return False
        return next(source_files(root, limit=1, project=project), None) is not None

    # ------------------------------------------------------------------ main
    def index(self, project: Project, builder: GraphBuilder, frameworks: list[FrameworkPlugin]) -> dict:
        t0 = time.time()
        self.root, self.b = project.root, builder
        stats: dict = defaultdict(int)
        try:
            from ..native.ts import parser
            parser("c")
            parser("cpp")
        except TreeSitterMissing as e:
            return {"status": "error", "reason": str(e)}
        files = list(source_files(self.root, project=project))
        self.file_report = {"seen": files, "skipped_oversize": []}
        is_cpp = any(os.path.splitext(f)[1] in CPP_EXT for f in files)
        self.compdb_path = find_compdb(self.root)
        self.compdb = self._read_compdb(self.compdb_path) if self.compdb_path else {}
        stats["compile_database"] = str(self.compdb_path.relative_to(self.root) if self.compdb_path and self.compdb_path.is_relative_to(self.root)
                                        else self.compdb_path) if self.compdb_path else None
        stats["translation_units_in_compdb"] = len(self.compdb)
        scen = (project.options.get("gates") or [None])[0]
        self.scen = G.Scenario(scen)
        self.files: dict[str, CFile] = {}
        srcs = {}
        limit = int(os.environ.get("CODEGRAPH_MAX_FILE_BYTES", str(30 * 1024 * 1024)))
        for rel in files:
            try:
                src = (self.root / rel).read_bytes()
            except OSError:
                continue
            if len(src) > limit:
                stats["skipped_large_files"] += 1
                self.file_report["skipped_oversize"].append(rel)
                continue
            srcs[rel] = src
        annot = annotation_macros(srcs.values()) if os.environ.get("CODEGRAPH_C_MASK_ANNOTATIONS", "1") != "0" else set()
        stats["annotation_macros_masked"] = len(annot)
        blank = annotation_regex(annot)
        for rel, src in srcs.items():
            ext = os.path.splitext(rel)[1]
            lang = "c" if ext in C_EXT or (ext == ".h" and not is_cpp) else "cpp"
            mod = os.path.dirname(rel) or "."
            self.files[rel] = extract(rel, src, lang, mod, blank)
        stats["files"] = len(self.files)
        stats["files_c"] = sum(1 for f in self.files.values() if f.lang == "c")
        stats["files_cpp"] = sum(1 for f in self.files.values() if f.lang == "cpp")
        self._finalize_items(stats)
        self._emit_structure(stats)
        mode = "heuristic"
        scip_info = None
        if os.environ.get("CODEGRAPH_C_SCIP", "1") != "0":
            if self.compdb_path:
                scip_path, scip_info = self._run_scip()
                if scip_path:
                    self._import_scip(scip_path, stats)
                    mode = "scip"
            else:
                scip_info = {"status": "no compile_commands.json found; heuristic mode. Generate one with "
                                       "`cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` or `bear -- make` (docs/native.md)"}
        if mode == "heuristic":
            self._heuristic_refs(stats)
            self._heuristic_dispatch(stats)
        self._includes(stats)
        self._facts(stats)
        self._entries(stats)
        self._gating(stats)
        out = dict(stats)
        out.update({"mode": mode, "seconds": round(time.time() - t0, 2)})
        if scip_info:
            out["scip"] = scip_info
        return out

    def _read_compdb(self, p: Path) -> dict:
        try:
            entries = json.loads(p.read_text())
        except Exception:
            return {}
        out = {}
        for e in entries:
            d = Path(e.get("directory") or ".")
            f = Path(e["file"])
            f = (d / f) if not f.is_absolute() else f
            try:
                rel = f.resolve().relative_to(self.root).as_posix()
            except ValueError:
                continue
            args = e.get("arguments") or shlex.split(e.get("command", ""))
            incs, defs = [], []
            i = 0
            while i < len(args):
                a = args[i]
                if a in ("-I", "-isystem", "-iquote") and i + 1 < len(args):
                    incs.append(args[i + 1]); i += 2; continue
                if a.startswith("-I") or a.startswith("-iquote"):
                    incs.append(a[2:] if a.startswith("-I") else a[7:])
                elif a.startswith("-isystem"):
                    incs.append(a[8:])
                elif a.startswith("-D"):
                    defs.append(a[2:])
                i += 1
            inc_rel = []
            for x in incs:
                xp = (d / x).resolve() if not Path(x).is_absolute() else Path(x)
                try:
                    inc_rel.append(xp.relative_to(self.root).as_posix())
                except ValueError:
                    pass
            out[rel] = {"includes": inc_rel, "defines": defs}
        return out

    # ------------------------------------------------------------------ items
    def _finalize_items(self, stats):
        all_items = [it for f in self.files.values() for it in f.items]
        class_quals = {it.qual for it in all_items if it.kind in ("class", "struct", "union")}
        class_names = defaultdict(set)
        for q in class_quals:
            class_names[q.split("::")[-1]].add(q)
        for it in all_items:
            if it.kind == "method" and it.scope and not it.parent:
                # out-of-line definition A::f: method if A names a known class (qualified or by short name)
                owner = it.qual.rsplit("::", 1)[0]
                if owner in class_quals:
                    it.parent = owner
                elif class_names.get(it.scope[-1]):
                    it.parent = sorted(class_names[it.scope[-1]])[0]
                else:
                    it.kind = "function"
        # declarations: map to definitions by qualified name
        self.decls_by_qual = defaultdict(list)
        for f in self.files.values():
            for d in f.decls:
                self.decls_by_qual[d.qual].append(d)
        # key collisions: overloads / same-named non-static C functions in different programs
        by_key = defaultdict(list)
        for it in all_items:
            by_key[(it.kind in CODE and "code" or it.kind, it.key)].append(it)
        for (k, key), lst in by_key.items():
            if len(lst) < 2:
                continue
            if k == "code":
                sigs = {x.sig for x in lst}
                files = {x.file for x in lst}
                for x in lst:
                    if len(sigs) == len(lst):
                        x.key = f"{x.key}({x.sig})"
                    elif len(files) == len(lst):
                        x.key = f"{x.file}#{x.key}"
                    else:
                        x.key = f"{x.key}@{x.file}:{x.line}"
                stats["code_key_collisions"] += len(lst)
            else:
                for x in lst[1:]:
                    x.key = f"{x.key}@{x.file}:{x.line}"
        self.items = all_items
        self.by_key = {}
        for it in all_items:
            self.by_key.setdefault(it.key, it)
        self.by_type_key = {}
        for it in all_items:
            if it.kind in ("class", "struct", "union", "enum"):
                self.by_type_key.setdefault(it.key, it)
        self.by_pos = {}
        for it in all_items:
            self.by_pos.setdefault((it.file, it.line, it.name), it)
            if it.kind == "macro":
                self.by_pos.setdefault((it.file, it.line, "!macro"), it)
            if it.attrs.get("test_macro"):
                self.by_pos.setdefault((it.file, it.line, "!test"), it)
        self.by_line = defaultdict(list)
        for it in all_items:
            self.by_line[(it.file, it.line)].append(it)
        self.by_name = defaultdict(list)
        for it in all_items:
            self.by_name[it.name].append(it)

    def nid(self, it: CItem) -> str:
        return f"{it.kind}:{it.key}"

    def _lang(self, rel: str) -> str:
        return self.files[rel].lang

    def _emit_structure(self, stats):
        b = self.b
        for rel, f in self.files.items():
            ext = os.path.splitext(rel)[1]
            b.add_node("file", rel, name=os.path.basename(rel), file=rel, line=1, module=os.path.dirname(rel) or ".", lang=f.lang,
                       attrs={"header": ext in HDR_EXT, "translation_unit": rel in self.compdb})
        for it in self.items:
            attrs = dict(it.attrs)
            if it.static:
                attrs["static"] = True
            if it.sig and it.kind in CODE:
                attrs["signature"] = it.sig
            decls = self.decls_by_qual.get(it.qual) if it.kind in CODE else None
            if decls:
                attrs["declared_in"] = sorted({f"{d.file}:{d.line}" for d in decls})[:4]
                if any(d.export for d in decls):
                    attrs["export"] = True
                for d in decls:
                    if d.virtual:
                        attrs["virtual"] = True
                    if d.override:
                        attrs["override"] = True
                    if d.pure:
                        attrs["pure"] = True
                    if d.access and "access" not in attrs:
                        attrs["access"] = d.access
                    if d.extern_c:
                        attrs["extern_c"] = True
            n = b.add_node(it.kind, it.key, name=it.name, fqn=it.qual or it.key, file=it.file, line=it.line, end_line=it.end,
                           module=it.module, doc=it.doc, lang=self._lang(it.file), attrs=attrs)
            par = (self.by_type_key.get(it.parent) or self.by_key.get(it.parent)) if it.parent else None
            if par is not None:
                b.add_edge(self.nid(par), n, "CONTAINS", it.file, it.line, EXACT)
            else:
                b.add_edge(f"file:{it.file}", n, "CONTAINS", it.file, it.line, EXACT)
            stats[f"items_{it.kind}"] += 1
        self.decl_items = []
        # pure virtual declarations have no definition: give them a node so overrides can attach
        for qual, decls in self.decls_by_qual.items():
            for d in decls:
                if d.kind == "method" and d.pure and qual not in self.by_key:
                    owner = qual.rsplit("::", 1)[0]
                    oit = self.by_type_key.get(owner) or self.by_key.get(owner)
                    n = b.add_node("method", qual, name=d.name, fqn=qual, file=d.file, line=d.line, module=os.path.dirname(d.file) or ".",
                                   lang=self._lang(d.file), attrs={"pure": True, "virtual": True, "access": d.access, "declaration_only": True})
                    if oit is not None:
                        b.add_edge(self.nid(oit), n, "CONTAINS", d.file, d.line, EXACT)
                    self.decl_items.append(CItem("method", d.name, qual, d.file, d.line, d.col, d.line, d.line,
                                                 os.path.dirname(d.file) or ".", qual=qual, attrs={"pure": True}))
                    stats["pure_virtual_decls"] += 1

    # ------------------------------------------------------------------ scip
    def _run_scip(self):
        tool = runner.find_tool("CODEGRAPH_SCIP_CLANG", ["scip-clang"], [Path.home() / ".local" / "bin"])
        pre = os.environ.get("CODEGRAPH_C_SCIP_FILE")
        if pre:
            return (Path(pre) if Path(pre).exists() else None), {"source": "CODEGRAPH_C_SCIP_FILE", "path": pre}
        if not tool:
            return None, {"status": "scip-clang not installed; heuristic mode (docs/native.md)"}
        ver = runner.tool_version(tool)
        key = scip_cache_key(self.root, list(self.files), ver, self.compdb_path)
        jobs = os.environ.get("CODEGRAPH_JOBS")
        cmd = [tool, f"--compdb-path={self.compdb_path}"] + ([f"--jobs={jobs}"] if jobs else [])
        timeout = int(os.environ.get("CODEGRAPH_INDEXER_TIMEOUT", "3600"))
        path, info = runner.run_cached("cfamily", key, cmd, self.root, "--index-output-path", timeout, out_flag_style="eq")
        info["indexer"] = ver
        if info.get("stderr_tail"):
            m = [l for l in info["stderr_tail"] if "num errored TUs" in l or "Finished indexing" in l]
            if m:
                info["summary"] = m[-1].strip()
        return path, info

    def _painter(self, rel: str):
        f = self.files.get(rel)
        if f is None:
            return None
        n = len(f.lines) + 2
        paint = [None] * n
        for it in sorted((x for x in f.items if x.kind in SCOPES), key=lambda x: -(x.end - x.start)):
            for ln in range(max(0, it.start), min(n, it.end + 1)):
                paint[ln] = it
        return paint

    def _import_scip(self, path: Path, stats):
        idx = scipread.load(path)
        stats["scip_documents"] = len(idx.docs)
        sym_nodes: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for sym, defs in idx.defs.items():
            d = scipread.descriptors(sym)
            if not d or not d[1]:
                continue
            name, suf = d[1][-1]
            for rel, o in defs:
                it = self.by_pos.get((rel, o.line + 1, name))
                if it is None and "<" in name:
                    it = self.by_pos.get((rel, o.line + 1, name.split("<")[0]))
                if it is None and suf in ("(", "#", "."):
                    # operators / conversion functions / constructors of templates print differently: accept the single
                    # syntactic item of a compatible kind declared on that line
                    want = CODE if suf == "(" else (TYPES if suf == "#" else {"field", "global", "enumerator"})
                    cands = [x for x in self.by_line.get((rel, o.line + 1), []) if x.kind in want]
                    if len(cands) == 1:
                        it = cands[0]
                if it is None and suf == "!":
                    it = self.by_pos.get((rel, o.line + 1, "!macro"))
                if it is None:
                    it = self.by_pos.get((rel, o.line + 1, "!test"))
                if it is not None and not _kind_ok(suf, it.kind):
                    stats[f"scip_kind_mismatch_{suf}_{it.kind}"] += 1
                    it = None
                if it is None:
                    if suf == "!" or rel not in self.files:
                        continue
                    nid = self._synthetic(rel, o, sym, d)
                    if nid is None:
                        continue
                    stats["scip_defs_synthetic"] += 1
                else:
                    nid = self.nid(it)
                    stats["scip_defs_matched"] += 1
                    # namespaces hidden behind macros (BEGIN_NAMESPACE-style) are only known to clang: adopt its
                    # qualified name for display / spec matching (the node id stays syntactic, stable across modes)
                    sq = "::".join(n for n, s in d[1] if s in ("/", "#", "(", ".") and not n.startswith(("<file>", "$")))
                    node = self.b.nodes.get(nid)
                    if node is not None and sq and len(sq) > len(node.fqn or "") and sq.endswith("::" + (node.fqn or "").split("<")[0]):
                        node.fqn = sq
                        stats["fqn_from_scip"] += 1
                if (rel, nid) not in sym_nodes[sym]:
                    sym_nodes[sym].append((rel, nid))
        # pure-virtual / declaration-only methods: the symbol is only ever declared
        for rel, doc in idx.docs.items():
            f = self.files.get(rel)
            if f is None:
                continue
            for o in doc.occs:
                if o.symbol in sym_nodes or (o.line + 1, o.col) not in f.decl_pos:
                    continue
                d = scipread.descriptors(o.symbol)
                if not d or not d[1]:
                    continue
                qual = "::".join(n for n, s in d[1] if s in ("/", "#", "(") and not n.startswith("<file>") and not n.startswith("$"))
                if f"method:{qual}" in self.b.nodes:
                    sym_nodes[o.symbol].append((rel, f"method:{qual}"))
        self.sym_nodes = sym_nodes
        # relationships: overrides and inheritance
        virtual = set()
        for rel, doc in idx.docs.items():
            for sym, si in doc.symbols.items():
                if sym not in sym_nodes:
                    continue
                for r in si.relationships:
                    if not r.is_implementation or r.symbol not in sym_nodes:
                        continue
                    for _, child in sym_nodes[sym]:
                        for _, base in sym_nodes[r.symbol][:1]:
                            if child.startswith("method:") and base.startswith("method:"):
                                bn = self.b.nodes.get(base)
                                ek = "IMPLEMENTED_BY" if bn is not None and bn.attrs.get("pure") else "OVERRIDDEN_BY"
                                cn = self.b.nodes.get(child)
                                self.b.add_edge(base, child, ek, cn.file if cn else rel, cn.line if cn else None, RESOLVED)
                                virtual.add(base)
                                stats["virtual_dispatch_edges"] += 1
                            elif not child.startswith("method:") and not base.startswith("method:"):
                                cn = self.b.nodes.get(child)
                                self.b.add_edge(child, base, "EXTENDS", cn.file if cn else rel, cn.line if cn else None, EXACT)
                                stats["inheritance_edges"] += 1
        self.virtual = virtual | {nid for nid, n in self.b.nodes.items() if n.kind == "method" and n.attrs.get("virtual")}
        for rel, doc in idx.docs.items():
            f = self.files.get(rel)
            if f is None:
                stats["scip_docs_outside_indexed_files"] += 1
                continue
            paint = self._painter(rel)
            lines = f.lines
            # references reported at a macro expansion site (the token under the range is the macro, not the target):
            # real dependencies (TEST/REGISTER-style macros), but type-check / dispatch macros can expand to dozens of
            # symbols per use. Keep small expansions (labelled via_macro, resolved), drop large ones.
            exp_groups = defaultdict(set)
            name_cache = {}
            for o in doc.occs:
                if o.roles & scipread.DEFINITION or o.symbol not in sym_nodes:
                    continue
                tok = lines[o.line][o.col:o.end_col] if o.line < len(lines) and o.end_col > o.col else ""
                nm = name_cache.get(o.symbol)
                if nm is None:
                    d = scipread.descriptors(o.symbol)
                    nm = name_cache[o.symbol] = (d[1][-1][0].split("<")[0] if d and d[1] else "")
                if tok and nm and tok != nm and not nm.startswith(("operator", "~")) and not tok.startswith(("operator", "~")) \
                        and re.fullmatch(r"[A-Za-z_]\w*", tok):
                    exp_groups[(o.line, o.col)].add(o.symbol)
            max_exp = int(os.environ.get("CODEGRAPH_C_MAX_MACRO_REFS", "8"))
            for o in doc.occs:
                if o.roles & scipread.DEFINITION:
                    continue
                if (o.line + 1, o.col) in f.decl_pos:
                    continue
                cands = sym_nodes.get(o.symbol)
                if not cands:
                    continue
                ln = o.line + 1
                owner = paint[ln] if ln < len(paint) else None
                if owner is None:
                    stats["scip_refs_outside_items"] += 1
                    continue
                if len(cands) > 1:
                    same = [c for c in cands if c[0] == rel]
                    cands = same[:1] if same else cands[:3]
                line_txt = lines[o.line] if o.line < len(lines) else ""
                before = line_txt[:o.col].rstrip()
                after = line_txt[o.end_col:].lstrip()
                grp = exp_groups.get((o.line, o.col))
                via = {}
                if grp and o.symbol in grp:
                    if len(grp) > max_exp:
                        stats["scip_macro_expansion_refs_dropped"] += 1
                        continue
                    via = {"via_macro": line_txt[o.col:o.end_col]}
                    stats["scip_macro_expansion_refs"] += 1
                for _, dst in cands:
                    conf = HEURISTIC if len(cands) > 1 else (RESOLVED if via or before.endswith((".", "->")) else EXACT)
                    self._ref_edge(self.nid(owner), dst, rel, ln, conf, "(" if via and dst.split(":", 1)[0] in CODE else after, stats, **via)

    def _synthetic(self, rel, o, sym, d) -> str | None:
        names = [(n, s) for n, s in d[1] if not n.startswith("<file>") and not n.startswith("$")]
        if not names:
            return None
        last, suf = names[-1]
        if suf not in ("(", "#", "."):
            return None
        qual = "::".join(n for n, s in names if s in ("/", "#", "(", "."))
        f = self.files[rel]
        if suf == "(":
            kind = "method" if any(s == "#" for _, s in names[:-1]) else "function"
        elif suf == "#":
            kind = "class"
        else:
            kind = "field" if any(s == "#" for _, s in names[:-1]) else "global"
        key = qual if any(s == "/" for _, s in d[1][:1]) or kind in ("method", "class", "field") else qual
        if any(n.startswith("<file>") or n.startswith("$anonymous") for n, _ in d[1]):
            key = f"{rel}#{qual}"
        if f"{kind}:{key}" in self.b.nodes:
            return f"{kind}:{key}"
        nid = self.b.add_node(kind, key, name=last, fqn=qual, file=rel, line=o.line + 1, module=os.path.dirname(rel) or ".",
                              lang=f.lang, attrs={"from": "scip"})
        return nid

    def _ref_edge(self, src: str, dst: str, rel: str, ln: int, conf: str, after: str, stats, **attrs):
        if src == dst:
            return
        kind = dst.split(":", 1)[0]
        node = self.b.nodes.get(dst)
        if kind in CODE:
            ek = "CALLS" if after.startswith(("(", "<")) else "REFERENCES_FN"
            if dst in getattr(self, "virtual", ()) and ek == "CALLS":
                attrs["dispatch"] = "virtual"
                stats["calls_virtual_dispatch"] += 1
        elif kind == "macro":
            ek = "CALLS" if (node is not None and node.attrs.get("function_like")) else "USES_VALUE"
        elif kind in TYPES:
            ek = "USES_TYPE"
        elif kind == "field":
            ek = "ACCESSES_FIELD"
        elif kind in ("global", "enumerator"):
            ek = "USES_VALUE"
        else:
            return
        self.b.add_edge(src, dst, ek, rel, ln, conf, **attrs)
        stats[f"edges_{ek}"] += 1

    # ------------------------------------------------------------------ heuristic
    def _heuristic_refs(self, stats):
        from ...platforms import c_relevant, path_convention

        def _platform_scoped(it) -> bool:
            if path_convention(it.file):
                return True
            f = self.files.get(it.file)
            return bool(f) and any(a <= it.start and it.end <= z and c_relevant(c) for a, z, c, _l, _b in f.regions)

        def _alternatives(cands):
            if (1 < len(cands) <= 8 and len({c.key.split("@", 1)[0] for c in cands}) == 1 and len({c.kind for c in cands}) == 1
                    and all(_platform_scoped(c) for c in cands)):
                return list(cands)
            return []

        funcs = defaultdict(list)
        methods = defaultdict(list)
        for it in self.items + self.decl_items:
            if it.kind == "function":
                funcs[it.name].append(it)
            elif it.kind == "method":
                methods[it.name].append(it)
        quals = {it.qual: it for it in self.items if it.kind in CODE}
        macros = {it.name: it for it in self.items if it.kind == "macro"}
        globals_ = defaultdict(list)
        for it in self.items:
            if it.kind in ("global", "enumerator"):
                globals_[it.name].append(it)
        types = defaultdict(list)
        for it in self.items:
            if it.kind in TYPES:
                types[it.name].append(it)

        def pick(cands, rel):
            if not cands:
                return []
            same = [c for c in cands if c.file == rel]
            if same:
                return _alternatives(same) or same[:1]
            nonstatic = [c for c in cands if not c.static]
            if len(nonstatic) == 1:
                return nonstatic
            # one symbol defined once per build configuration (#ifdef _WIN32 / #else, src/win/x.c + src/unix/x.c):
            # every definition is a target (each is tagged with its platforms)
            return _alternatives(nonstatic)

        for rel, f in self.files.items():
            for owner_key, form, txt, name, line, col in f.calls:
                owner = owner_key
                if owner is None:
                    continue
                targets = []
                if form == "name":
                    if name in LIBC:
                        continue
                    if name in macros and macros[name].attrs.get("function_like"):
                        targets = [macros[name]]
                    else:
                        targets = pick(funcs.get(name), rel) or (pick(methods.get(name), rel) if owner.kind == "method" else [])
                elif form == "qualified":
                    q = quals.get(txt) or next((v for k, v in quals.items() if k.endswith("::" + txt)), None)
                    targets = [q] if q else []
                else:
                    c = methods.get(name, [])
                    if len(c) > 1:
                        # several classes define the method: if exactly one is the virtual root, dispatch through it
                        roots = [m for m in c if self._is_virtual(m) and not self._is_override(m)]
                        c = roots if len(roots) == 1 else c
                    targets = c if len(c) == 1 else []
                if not targets:
                    stats["heuristic_unresolved_calls"] += 1
                    continue
                for t in targets:
                    self._ref_edge(self.nid(owner), self.nid(t), rel, line, HEURISTIC, "(", stats,
                                   **({"dispatch": "virtual"} if form == "member" and self._is_virtual(t) else {}))
            for owner_key, name, line, col in f.idents:
                owner = owner_key
                if owner is None:
                    continue
                if name in funcs:
                    t = pick(funcs[name], rel)
                    for x in t:
                        self._ref_edge(self.nid(owner), self.nid(x), rel, line, HEURISTIC, "", stats)
                elif name in globals_:
                    for x in pick(globals_[name], rel):
                        self._ref_edge(self.nid(owner), self.nid(x), rel, line, HEURISTIC, "", stats)
                elif name in macros and not macros[name].attrs.get("function_like"):
                    self._ref_edge(self.nid(owner), self.nid(macros[name]), rel, line, HEURISTIC, "", stats)
            for owner_key, name, line, col in f.type_refs:
                owner = owner_key
                if owner is None:
                    continue
                for x in pick(types.get(name), rel):
                    self._ref_edge(self.nid(owner), self.nid(x), rel, line, HEURISTIC, "", stats)

    def _is_virtual(self, it) -> bool:
        n = self.b.nodes.get(self.nid(it))
        return bool(n is not None and n.attrs.get("virtual"))

    def _is_override(self, it) -> bool:
        n = self.b.nodes.get(self.nid(it))
        return bool(n is not None and n.attrs.get("override"))

    def _heuristic_dispatch(self, stats):
        cls_by_name = defaultdict(list)
        for it in self.items:
            if it.kind in ("class", "struct"):
                cls_by_name[it.name].append(it)
        parents = defaultdict(list)
        for f in self.files.values():
            for ckey, base, line, access in f.bases:
                cands = cls_by_name.get(base.split("::")[-1], [])
                if len(cands) == 1 and ckey in self.by_key:
                    parents[ckey].append(cands[0].key)
                    self.b.add_edge(self.nid(self.by_key[ckey]), self.nid(cands[0]), "EXTENDS", f.path, line, HEURISTIC)
                    stats["inheritance_edges"] += 1
        methods_of = defaultdict(dict)
        for nid, n in self.b.nodes.items():
            if n.kind == "method" and n.fqn and "::" in n.fqn:
                methods_of[n.fqn.rsplit("::", 1)[0]][n.name] = nid
        for ckey, ps in parents.items():
            cq = self.by_key[ckey].qual
            for pkey in ps:
                pq = self.by_key[pkey].qual
                for mname, child in methods_of.get(cq, {}).items():
                    base = methods_of.get(pq, {}).get(mname)
                    if base:
                        bn = self.b.nodes[base]
                        if bn.attrs.get("virtual") or self.b.nodes[child].attrs.get("override"):
                            ek = "IMPLEMENTED_BY" if bn.attrs.get("pure") else "OVERRIDDEN_BY"
                            cn = self.b.nodes[child]
                            self.b.add_edge(base, child, ek, cn.file, cn.line, HEURISTIC)
                            stats["virtual_dispatch_edges"] += 1

    # ------------------------------------------------------------------ includes, env, gates
    def _includes(self, stats):
        by_base = defaultdict(list)
        for rel in self.files:
            by_base[os.path.basename(rel)].append(rel)
        global_incs = set()
        for e in self.compdb.values():
            global_incs.update(e["includes"])
        default_incs = [d for d in ("include", "src", "inc", "") if d == "" or (self.root / d).is_dir()]
        for rel, f in self.files.items():
            dirs = (self.compdb.get(rel) or {}).get("includes") or []
            for path, line, system in f.includes:
                hit, conf = None, EXACT
                cand = [os.path.normpath(os.path.join(os.path.dirname(rel), path))] if not system else []
                cand += [os.path.normpath(os.path.join(d, path)) for d in dirs]
                cand += [os.path.normpath(os.path.join(d, path)) for d in sorted(global_incs) + default_incs]
                for c in cand:
                    c = c.replace("\\", "/").lstrip("./") if not c.startswith("../") else c
                    if c in self.files:
                        hit = c
                        conf = EXACT if c == cand[0] or dirs else RESOLVED
                        break
                if hit is None:
                    bb = [x for x in by_base.get(os.path.basename(path), []) if x.endswith("/" + path) or x == path]
                    if len(bb) == 1:
                        hit, conf = bb[0], HEURISTIC
                if hit is None:
                    if not system:
                        stats["includes_unresolved"] += 1
                    continue
                self.b.add_edge(f"file:{rel}", f"file:{hit}", "INCLUDES", rel, line, conf)
                stats["includes_resolved"] += 1

    def _facts(self, stats):
        b = self.b
        for rel, f in self.files.items():
            for key, line, owner_key in f.env:
                owner = owner_key
                if owner is None:
                    continue
                b.add_node("env", key, name=key, lang=f.lang)
                b.add_edge(self.nid(owner), f"env:{key}", "READS_ENV", rel, line, EXACT, how="getenv")
                stats["env_reads"] += 1
            # preprocessor gates: items overlapping each conditional region
            if not f.regions:
                continue
            self._platform_marks(rel, f)
            scoped = [it for it in f.items if it.kind in CODE | TYPES | {"global", "field", "macro"}]
            for start, end, cond, dline, branch in f.regions:
                macros = [m for m in G.pp_macros(re.sub(r"__has_\w+\s*\([^)]*\)", "", cond)) if m != "__cplusplus" and m not in f.guards]
                if not macros:
                    continue
                for it in scoped:
                    if it.start > end or it.end < start:
                        continue
                    inside = start <= it.start and it.end <= end
                    if not inside and it.kind not in CODE:
                        continue
                    for m in macros:
                        b.add_node("define", m, name=m, lang=f.lang)
                        b.add_edge(self.nid(it), f"define:{m}", "GATED_BY", rel, dline, EXACT, cond=cond,
                                   scope="item" if inside else "block")
                    stats["gated_by_edges"] += 1

    def _platform_marks(self, rel: str, f) -> None:
        """Platform conditions (codegraph/platforms.py): #if regions over platform macros (_WIN32, __APPLE__, ...)."""
        from ...platforms import Cond, c_relevant, mark
        for start, end, cond, dline, branch in f.regions:
            if c_relevant(cond):
                txt = f"#{'if' if branch == 'if' else branch} {cond}" if branch in ("if", "elif") else f"#{branch} ({cond})"
                mark(self.b, rel, start, end, Cond("c", cond, txt[:200]), line=dline)

    def _entries(self, stats):
        b = self.b
        for it in self.items:
            if it.kind not in CODE:
                continue
            n = b.nodes.get(self.nid(it))
            if n is None:
                continue
            path = it.file
            kinds = []
            is_test_file = bool(TEST_DIR.search(path) or TEST_FILE.search(path))
            if it.attrs.get("test_macro"):
                kinds.append("bench" if it.attrs["test_macro"] == "BENCHMARK" else "test")
            if it.kind == "function" and it.name in MAIN_NAMES and not it.qual.count("::"):
                if is_test_file:
                    kinds.append("test")
                elif EXAMPLE_DIR.search(path):
                    kinds.append("example")
                elif BENCH_DIR.search(path):
                    kinds.append("bench")
                else:
                    kinds.append("main")
            elif is_test_file and re.match(r"(?i)test", it.name) and it.kind == "function":
                kinds.append("test")
            if not it.static and not is_test_file and not EXAMPLE_DIR.search(path) and "#" not in it.key.split("(")[0]:
                decls = self.decls_by_qual.get(it.qual, [])
                public = n.attrs.get("export") or any(d.export for d in decls)
                if not public:
                    hdr = [d for d in decls if INCLUDE_DIR.search(d.file)]
                    public = bool(hdr) and all(d.access in (None, "public") for d in hdr)
                if not public and it.kind == "method" and INCLUDE_DIR.search(it.file) and it.attrs.get("access", "public") == "public":
                    public = True
                if public and n.attrs.get("access") in ("private",):
                    public = False
                if public:
                    kinds.append("public_api")
            if not kinds:
                continue
            order = ["main", "test", "bench", "example", "public_api"]
            n.entry_kind = min(kinds, key=order.index)
            stats[f"entry_{n.entry_kind}"] += 1

    def _gating(self, stats):
        sc = self.scen
        if not sc.active or not (sc.defines or sc.undefined):
            return
        dead = defaultdict(list)
        for rel, f in self.files.items():
            for start, end, cond, dline, branch in f.regions:
                if sc.eval_pp(cond) is False:
                    dead[rel].append((start, end, cond, dline))
        n = 0
        for e in self.b.edges.values():
            if e.file in dead and e.line is not None and e.gate is None and e.kind != "GATED_BY":
                for a, z, cond, dline in dead[e.file]:
                    if a <= e.line <= z:
                        e.gate = sc.name
                        e.attrs = {**e.attrs, "guard": f"{e.file}:{dline}", "guard_expr": f"#if {cond}"}
                        n += 1
                        break
        stats["gated_edges"] = n

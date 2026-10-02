"""Rust language plugin.

Two layers, merged onto the same node ids:
  1. syntactic (tree-sitter-rust, always): Cargo packages / targets / features, the module tree of every target,
     items with keys, visibility, attributes, cfg gates, impls, unsafe, FFI, env keys, entry points, routes;
  2. semantic (rust-analyzer `scip`, when installed): every reference resolved by the compiler front-end.
     Definitions are matched to syntactic items by (file, line, name), references become CALLS / USES_TYPE /
     ACCESSES_FIELD / USES_VALUE / REFERENCES_FN edges from the innermost enclosing item.
Without rust-analyzer (or with CODEGRAPH_RUST_SCIP=0) a name-based resolver over the syntactic layer is used:
full-path matches are `resolved`, name-only matches `heuristic`.

Safety: rust-analyzer runs with build scripts and proc-macros disabled by default (they execute project code);
set CODEGRAPH_RUST_BUILD_SCRIPTS=1 to enable both (more macro-generated code gets resolved).
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...core.fsutil import keep_file
from ...core.plugin import FrameworkPlugin, GraphBuilder, LanguagePlugin, Project
from ..native import gates as G
from ..native import runner, scipread
from ..native.ts import TreeSitterMissing
from ...core.paths import PathRules, rel_dir, rules as path_rules
from .cargo import SKIP_DIRS, discover
from .syntax import ENTRY_ATTRS, ROUTE_ATTRS, RFile, RItem, extract

CODE = {"function", "method", "ffi"}
TYPES = {"struct", "enum", "union", "trait", "type_alias"}
VALUES = {"const", "static"}
SCOPES = CODE | TYPES | VALUES | {"variant", "field"}
ENTRY_PRIORITY = ["main", "ffi_export", "test", "bench", "example", "build_script", "public_api"]
# std / prelude method names: a heuristic `.name()` match on these is almost always a std call
STD_METHODS = set("""new default clone to_string to_owned into from as_ref as_mut borrow len is_empty iter iter_mut into_iter
map map_err and_then ok ok_or unwrap expect unwrap_or unwrap_or_else unwrap_or_default get get_mut insert remove push pop
contains extend collect filter fold next take skip chain zip rev count sum min max sort sort_by dedup join split trim
starts_with ends_with parse fmt write write_all read read_to_string flush lock send recv spawn await poll clear
first last keys values entry or_insert or_default with_capacity capacity reserve truncate drain retain append
is_some is_none is_ok is_err as_str as_bytes as_ptr to_vec cmp eq ne partial_cmp hash drop deref deref_mut index
call build run close open start stop find position any all display debug copied cloned""".split())
SCIP_KIND = {17: "function", 26: "method", 70: "method", 66: "method", 80: "method", 49: "struct", 11: "enum",
             59: "union", 53: "trait", 55: "type_alias", 54: "type_alias", 8: "const", 82: "static", 25: "macro",
             15: "field", 12: "variant", 29: "mod"}


def _rust_files(root: Path, rules: PathRules | None = None):
    rules = rules or PathRules(SKIP_DIRS)
    for dp, dn, fn in os.walk(root):
        rd = rel_dir(root, dp)
        dn[:] = rules.prune(rd, dn, dot=True)
        for f in sorted(fn):
            rel = f"{rd}/{f}" if rd else f
            if (f.endswith(".rs") or f in ("Cargo.toml", "Cargo.lock")) and keep_file(os.path.join(dp, f)) and not rules.excluded(rel):
                yield rel


class RustPlugin(LanguagePlugin):
    name = "rust"

    def detect(self, project: Project) -> bool:
        return project.exists("Cargo.toml")

    # ------------------------------------------------------------------ main
    def index(self, project: Project, builder: GraphBuilder, frameworks: list[FrameworkPlugin]) -> dict:
        t0 = time.time()
        root = project.root
        self.root, self.b = root, builder
        self._tm = None
        self.rules = path_rules(project, "rust")
        stats: dict = defaultdict(int)
        try:
            from ..native.ts import parser
            parser("rust")
        except TreeSitterMissing as e:
            return {"status": "error", "reason": str(e)}
        pkgs, how = discover(root)
        stats["workspace_from"] = how
        stats["packages"] = len(pkgs)
        self.pkgs = pkgs
        self.pkg_features = {p.name: p.features for p in pkgs}
        scen = (project.options.get("gates") or [None])[0]
        self.scen = G.Scenario(scen, self.pkg_features)
        self.files: dict[str, RFile] = {}
        self.file_meta: dict[str, dict] = {}
        self._module_trees(pkgs, stats)
        stats["files"] = len(self.files)
        orphans = [f for f in _rust_files(root, self.rules) if f.endswith(".rs") and f not in self.files
                   and "/target/" not in f and not f.startswith("target/")]
        stats["orphan_rs_files"] = len(orphans)
        # coverage: .rs files outside every crate's module tree are not in the graph
        self.file_report = {"seen": list(self.files) + orphans, "unmapped": orphans}
        self._indexes()
        self._reexports()
        self._emit_structure(stats)
        # semantic layer
        mode = "heuristic"
        scip_info = None
        if os.environ.get("CODEGRAPH_RUST_SCIP", "1") != "0":
            scip_path, scip_info = self._run_scip()
            if scip_path:
                self._import_scip(scip_path, stats)
                mode = "scip"
        if mode == "heuristic":
            self._heuristic_refs(stats)
        else:
            self._cfg_inactive_refs(stats)
        self._impls(stats, mode)
        self._facts(stats, mode)
        self._entries(stats)
        self._gating(stats)
        self._platform_marks()
        out = dict(stats)
        out.update({"mode": mode, "seconds": round(time.time() - t0, 2)})
        if scip_info:
            out["scip"] = scip_info
        return out

    # ------------------------------------------------------------------ module trees
    def _module_trees(self, pkgs, stats):
        order = {"lib": 0, "bin": 1, "build": 2, "example": 3, "bench": 4, "test": 5}
        for pkg in pkgs:
            for tgt in sorted(pkg.targets, key=lambda t: (order.get(t.kind, 9), t.name)):
                if not (self.root / tgt.src).exists():
                    stats["missing_target_roots"] += 1
                    continue
                todo = [(tgt.src, tgt.crate, [], False, tgt.kind == "lib", True)]
                while todo:
                    rel, module, cfgs, in_test, pub_chain, is_root = todo.pop(0)
                    if rel in self.files:
                        self.file_meta[rel].setdefault("also_in", []).append(tgt.crate)
                        continue
                    if self.rules.excluded(rel):          # .cg.yaml exclude: the file and the modules it declares
                        stats["excluded_files"] += 1
                        continue
                    try:
                        src = (self.root / rel).read_bytes()
                    except OSError:
                        continue
                    rf = extract(rel, src, tgt.crate, module, cfgs, in_test or tgt.kind in ("test", "bench"), pub_chain)
                    self.files[rel] = rf
                    self.file_meta[rel] = {"pkg": pkg, "target": tgt, "is_root": is_root, "cfgs": cfgs}
                    p = Path(rel)
                    mod_rs = is_root or p.name in ("mod.rs", "lib.rs", "main.rs")
                    base = p.parent if mod_rs else p.parent / p.stem
                    for md in rf.mods:
                        if getattr(md, "inline", False):
                            continue
                        extra = md.module.split("::")[len(module.split("::")):-1]
                        if md.path_attr:
                            cand = [(p.parent if not extra else base.joinpath(*extra)) / md.path_attr]
                        else:
                            d = base.joinpath(*extra) if extra else base
                            cand = [d / f"{md.name}.rs", d / md.name / "mod.rs"]
                        hit = next((c for c in cand if (self.root / c).exists()), None)
                        if hit is None:
                            stats["unresolved_mod_decls"] += 1
                            continue
                        todo.append((hit.as_posix(), md.module, md.cfgs, md.in_test, md.pub_chain, False))

    def _indexes(self):
        self.items: list[RItem] = [it for rf in self.files.values() for it in rf.items]
        self.by_key: dict[str, RItem] = {}
        self.dupes = 0
        for it in self.items:
            if it.kind == "impl":
                continue
            if it.key in self.by_key:
                prev = self.by_key[it.key]
                if prev.kind == it.kind or {prev.kind, it.kind} <= CODE:
                    # cfg-alternative definitions (#[cfg(unix)] fn x / #[cfg(windows)] fn x) or macro-repeated names:
                    # the first keeps the plain key, later ones get @line
                    self.dupes += 1
                    it.key = f"{it.key}@{it.line}" if it.file == prev.file else f"{it.key}@{it.file}:{it.line}"
            self.by_key.setdefault(it.key, it)
        self.by_pos: dict[tuple, RItem] = {(it.file, it.line, it.name): it for it in self.items if it.kind != "impl"}
        self.by_name: dict[str, list[RItem]] = defaultdict(list)
        for it in self.items:
            if it.kind != "impl":
                self.by_name[it.name].append(it)
        self.children: dict[str, dict[str, list[RItem]]] = defaultdict(lambda: defaultdict(list))
        for it in self.items:
            if it.kind == "impl":
                continue
            par = it.parent or it.module
            if it.kind == "mod":
                par = it.key.rsplit("::", 1)[0]
            self.children[par][it.name].append(it)
        self.lib_crates = {t.crate for p in self.pkgs for t in p.targets if t.kind == "lib"}
        self.global_uses: dict[tuple, str] = {}
        for rf in self.files.values():
            for k, v in rf.use_map.items():
                self.global_uses.setdefault(k, v)

    def nid(self, it: RItem) -> str:
        return f"{it.kind}:{it.key}"

    def _reexports(self):
        """`pub use a::b::C` / `pub use m::*` in a library makes the target public API even if its module is private."""
        for _ in range(2):
            for rf in self.files.values():
                if self.file_meta[rf.path]["target"].kind != "lib":
                    continue
                for module, path, _line in rf.pub_uses:
                    glob = path.endswith("::*")
                    full = self._abs_path(path[:-3] if glob else path, module, rf.crate)
                    if not full:
                        continue
                    if glob:
                        for lst in self.children.get(full, {}).values():
                            for it in lst:
                                if it.vis and it.vis.strip() == "pub":
                                    self._mark_pub(it)
                        continue
                    it = self.by_key.get(full)
                    if it is not None:
                        self._mark_pub(it)

    def _mark_pub(self, it: RItem):
        if it.pub_chain:
            return
        it.pub_chain = True
        if it.kind in TYPES or it.kind == "mod":
            pre = it.key
            mod = it.key.rsplit("::", 1)[0]
            for x in self.items:
                if x.kind in ("method", "const") and (x.parent == pre or (x.parent or "").startswith(f"{mod}::<{it.name} as ")):
                    if (x.vis and x.vis.strip() == "pub") or (x.parent or "").startswith(f"{mod}::<") or it.kind == "trait":
                        x.pub_chain = True
                if it.kind == "mod" and x.module == it.key and x.vis and x.vis.strip() == "pub":
                    x.pub_chain = True

    def _abs_path(self, path: str, module: str, crate: str) -> str | None:
        """Resolve a path written in `module` to an item / module key (workspace crates only; follows `use`
        re-exports a few levels)."""
        segs = [s for s in path.split("::") if s]
        if not segs:
            return None
        mod_parts = module.split("::")
        root = mod_parts[0]
        if segs[0] == "crate":
            segs = [root] + segs[1:]
        elif segs[0] == "self":
            segs = mod_parts + segs[1:]
        elif segs[0] == "super":
            up = mod_parts[:]
            while segs and segs[0] == "super":
                up = up[:-1] if len(up) > 1 else up
                segs = segs[1:]
            segs = up + segs
        elif segs[0] not in self.lib_crates:
            for base in (mod_parts, [root]):
                c = self._canon("::".join(base + segs))
                if c:
                    return c
            return None
        return self._canon("::".join(segs)) or "::".join(segs)

    def _canon(self, full: str, depth: int = 0) -> str | None:
        if full in self.by_key or full in self.children:
            return full
        if depth > 4 or "::" not in full:
            return None
        mod, name = full.rsplit("::", 1)
        base = mod if mod in self.children else self._canon(mod, depth + 1)
        if base is None:
            return None
        if f"{base}::{name}" in self.by_key:
            return f"{base}::{name}"
        alias = self.global_uses.get((base, name))
        if alias:
            segs = [s for s in alias.split("::") if s]
            mp = base.split("::")
            if segs[0] == "crate":
                segs = mp[:1] + segs[1:]
            elif segs[0] == "self":
                segs = mp + segs[1:]
            elif segs[0] == "super":
                segs = mp[:-1] + segs[1:]
            elif segs[0] not in self.lib_crates:
                segs = mp + segs
            return self._canon("::".join(segs), depth + 1)
        return None

    # ------------------------------------------------------------------ structure nodes
    def _emit_structure(self, stats):
        b = self.b
        for pkg in self.pkgs:
            for f, enables in sorted(pkg.features.items()):
                b.add_node("feature", f"{pkg.name}/{f}", name=f"{pkg.name}/{f}", file=pkg.manifest, lang="rust",
                           module=pkg.name, attrs={"enables": enables, "package": pkg.name})
            for t in pkg.targets:
                b.add_node("crate", t.crate, name=t.crate, file=t.src, line=1, lang="rust", module=t.crate,
                           attrs={"package": pkg.name, "target_kind": t.kind, "crate_types": t.crate_types,
                                  "required_features": t.required_features, "version": pkg.version})
        libs = {p.name: next((t.crate for t in p.targets if t.kind == "lib"), None) for p in self.pkgs}
        for pkg in self.pkgs:
            for t in pkg.targets:
                lib_self = libs.get(pkg.name)
                if lib_self and t.kind != "lib":
                    b.add_edge(f"crate:{t.crate}", f"crate:{lib_self}", "IMPORTS", pkg.manifest, None, EXACT)
                for d in pkg.deps:
                    if libs.get(d):
                        b.add_edge(f"crate:{t.crate}", f"crate:{libs[d]}", "IMPORTS", pkg.manifest, None, EXACT)
        for rf in self.files.values():
            meta = self.file_meta[rf.path]
            if meta["is_root"]:
                b.add_node("crate", rf.crate, doc=rf.file_doc)
            else:
                b.add_node("mod", rf.module, name=rf.module, file=rf.path, line=1, lang="rust", module=rf.module,
                           doc=rf.file_doc, attrs={"crate": rf.crate})
        for it in self.items:
            if it.kind == "impl":
                continue
            attrs = {}
            if it.vis:
                attrs["vis"] = it.vis
            if it.cfgs:
                attrs["cfg"] = [c for c, _ in it.cfgs]
            if it.unsafe:
                attrs["unsafe"] = True
            if it.is_async:
                attrs["async"] = True
            if it.abi:
                attrs["abi"] = it.abi
            if it.in_test:
                attrs["test_only"] = True
            if it.impl_trait and it.kind == "method" and it.impl_self:
                attrs["trait"] = it.impl_trait
            sel = [a for a in it.attrs if a in ("no_mangle", "export_name", "unsafe", "inline", "deprecated", "derive", "mut")
                   or a in ENTRY_ATTRS or a.split("::")[-1] in ROUTE_ATTRS]
            if sel:
                attrs["attributes"] = sel
            if it.pub_chain:
                attrs["public"] = True
            fqn = it.key
            n = self.b.add_node(it.kind, it.key, name=it.name, fqn=fqn, file=it.file, line=it.line if it.kind != "mod" else it.start,
                                end_line=it.end, module=it.module, doc=it.doc, lang="rust", attrs=attrs)
            par = it.parent if it.parent and it.parent in self.by_key else None
            if par:
                self.b.add_edge(self.nid(self.by_key[par]), n, "CONTAINS", it.file, it.line, EXACT)
            elif it.kind != "mod":
                owner = f"mod:{it.module}" if not self.file_meta[it.file]["is_root"] or it.module != self.files[it.file].crate else f"crate:{it.module}"
                if it.module == self.files[it.file].crate:
                    owner = f"crate:{it.module}"
                elif f"mod:{it.module}" not in self.b.nodes:
                    owner = None
                if owner:
                    self.b.add_edge(owner, n, "CONTAINS", it.file, it.line, EXACT)
            stats[f"items_{it.kind}"] += 1
        stats["duplicate_keys"] = self.dupes

    # ------------------------------------------------------------------ SCIP
    def _run_scip(self):
        ra = runner.find_tool("CODEGRAPH_RUST_ANALYZER", ["rust-analyzer"], [Path.home() / ".cargo" / "bin"])
        pre = os.environ.get("CODEGRAPH_RUST_SCIP_FILE")
        if pre:
            return (Path(pre) if Path(pre).exists() else None), {"source": "CODEGRAPH_RUST_SCIP_FILE", "path": pre}
        if not ra:
            return None, {"status": "rust-analyzer not installed; heuristic mode (see docs/native.md)"}
        unsafe_ok = os.environ.get("CODEGRAPH_RUST_BUILD_SCRIPTS") == "1"
        cfg = {"cargo": {"buildScripts": {"enable": unsafe_ok}, "features": "all"},
               "procMacro": {"enable": unsafe_ok}}
        cfg_path = runner.cache_dir() / f"ra-config-{int(unsafe_ok)}.json"
        cfg_path.write_text(json.dumps(cfg))
        ver = runner.tool_version(ra)
        files = list(_rust_files(self.root, getattr(self, "rules", None)))
        key = runner.fingerprint(self.root, files, f"{ver}|{json.dumps(cfg, sort_keys=True)}")
        timeout = int(os.environ.get("CODEGRAPH_INDEXER_TIMEOUT", "3600"))
        env_path = os.environ.get("PATH", "")
        cargo_bin = str(Path.home() / ".cargo" / "bin")
        if cargo_bin not in env_path.split(os.pathsep) and Path(cargo_bin).is_dir():
            os.environ["PATH"] = cargo_bin + os.pathsep + env_path
        path, info = runner.run_cached("rust", key, [ra, "scip", str(self.root), "--config-path", str(cfg_path)],
                                       self.root, "--output", timeout)
        info.update({"indexer": ver, "build_scripts_and_proc_macros": unsafe_ok})
        return path, info

    def _scope_painter(self, rel: str):
        """line -> innermost item (by syntactic range) for a file."""
        rf = self.files.get(rel)
        if rf is None:
            return None
        n = len(rf.lines) + 2
        paint = [None] * n
        scopes = [it for it in rf.items if it.kind in SCOPES]
        for it in sorted(scopes, key=lambda x: -(x.end - x.start)):
            for ln in range(max(0, it.start), min(n, it.end + 1)):
                paint[ln] = it
        return paint

    def _import_scip(self, path: Path, stats):
        idx = scipread.load(path)
        stats["scip_documents"] = len(idx.docs)
        # symbol -> [(file, nid)]
        sym_nodes: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for sym, defs in idx.defs.items():
            for rel, o in defs:
                d = scipread.descriptors(sym)
                if not d or not d[1]:
                    continue
                name = d[1][-1][0]
                it = self.by_pos.get((rel, o.line + 1, name))
                if it is None:
                    nid = self._synthetic_node(rel, o, sym, d, idx)
                    if nid is None:
                        continue
                    stats["scip_defs_synthetic"] += 1
                else:
                    nid = self.nid(it)
                    stats["scip_defs_matched"] += 1
                sym_nodes[sym].append((rel, nid))
        self.sym_nodes = sym_nodes
        self.pos_sym: dict[tuple, str] = {}
        for rel, doc in idx.docs.items():
            rf = self.files.get(rel)
            if rf is None:
                stats["scip_docs_outside_module_tree"] += 1
                continue
            paint = self._scope_painter(rel)
            uses = rf.use_ranges
            lines = rf.lines
            target = self.file_meta[rel]["target"].crate
            for o in doc.occs:
                self.pos_sym[(rel, o.line + 1, o.col)] = o.symbol
                if o.roles & scipread.DEFINITION:
                    continue
                cands = sym_nodes.get(o.symbol)
                if not cands:
                    continue
                ln = o.line + 1
                if any(a <= ln <= b for a, b in uses):
                    continue
                owner = paint[ln] if ln < len(paint) else None
                if owner is None:
                    stats["scip_refs_outside_items"] += 1
                    continue
                if len(cands) > 1:
                    same = [c for c in cands if c[0] == rel] or [c for c in cands if self.file_meta.get(c[0], {}).get("target") and self.file_meta[c[0]]["target"].crate == target]
                    cands2 = same[:1] if same else cands[:5]
                else:
                    cands2 = cands
                line_txt = lines[o.line] if o.line < len(lines) else ""
                before = line_txt[:o.col].rstrip()
                after = line_txt[o.end_col:].lstrip()
                for _, dst in cands2:
                    conf = HEURISTIC if len(cands2) > 1 else (RESOLVED if before.endswith(".") else EXACT)
                    self._ref_edge(self.nid(owner), dst, rel, ln, conf, after, stats)

    def _ref_edge(self, src: str, dst: str, rel: str, ln: int, conf: str, after: str, stats, **attrs):
        kind = dst.split(":", 1)[0]
        if kind in CODE:
            called = after.startswith("(") or after.startswith("::<")
            ek = "CALLS" if called else "REFERENCES_FN"
            node = self.b.nodes.get(dst)
            if node is not None and node.kind == "method" and node.attrs.get("trait") is None and dst in self._trait_methods:
                attrs["dispatch"] = "trait"
                stats["calls_trait_dispatch"] += 1
        elif kind == "macro":
            ek = "CALLS"
        elif kind in TYPES:
            ek = "USES_TYPE"
        elif kind in ("field", "variant"):
            ek = "ACCESSES_FIELD"
        elif kind in VALUES:
            ek = "USES_VALUE"
        else:
            return
        if src == dst:
            return
        self.b.add_edge(src, dst, ek, rel, ln, conf, **attrs)
        stats[f"edges_{ek}"] += 1

    @property
    def _trait_methods(self) -> set:
        tm = getattr(self, "_tm", None)
        if tm is None:
            tm = self._tm = {self.nid(it) for it in self.items if it.kind == "method" and it.parent and
                             self.by_key.get(it.parent) is not None and self.by_key[it.parent].kind == "trait"}
        return tm

    def _synthetic_node(self, rel, o, sym, d, idx) -> str | None:
        """A SCIP definition with no syntactic item (macro-generated, unusual syntax): derive a key from the
        descriptor path under the file's crate."""
        rf = self.files.get(rel)
        if rf is None:
            return None
        si = idx.docs[rel].symbols.get(sym)
        kind = SCIP_KIND.get(si.kind if si is not None else 0)
        names = d[1]
        last, suf = names[-1]
        if kind is None:
            kind = {"(": "function", "#": "struct", "!": "macro"}.get(suf)
        if kind is None or kind == "mod":
            return None
        parts, i = [], 0
        while i < len(names):
            nm, s = names[i]
            if s == "#" and nm == "impl":
                tps = []
                while i + 1 < len(names) and names[i + 1][1] == "[":
                    tps.append(names[i + 1][0])
                    i += 1
                parts.append(f"<{tps[0]} as {tps[1]}>" if len(tps) > 1 else (tps[0] if tps else "impl"))
            elif s != "[":
                parts.append(nm)
            i += 1
        crate = rf.crate
        key = "::".join([crate] + parts)
        if kind == "function" and any(s == "#" for _, s in names[:-1]):
            kind = "method"
        nid = self.b.add_node(kind, key, name=last, fqn=key, file=rel, line=o.line + 1, module=rf.module, lang="rust",
                              attrs={"from": "scip", "macro_generated": True})
        return nid

    # ------------------------------------------------------------------ heuristic resolution
    def _resolve_path(self, rf: RFile, owner: RItem, path: str) -> tuple[list[RItem], str]:
        segs = [s.split("<")[0] for s in path.split("::") if s]
        if not segs:
            return [], HEURISTIC
        module = owner.module
        if segs[0] == "Self" and owner.impl_self:
            segs = [owner.impl_self] + segs[1:]
        alias = rf.use_map.get((module, segs[0]))
        if alias:
            segs = alias.split("::") + segs[1:]
        full = self._abs_path("::".join(segs), module, rf.crate)
        if full and full in self.by_key and self.by_key[full].kind in CODE | {"variant"}:
            return [self.by_key[full]], RESOLVED
        name = segs[-1]
        cands = [c for c in self.by_name.get(name, []) if c.kind in CODE]
        if len(segs) >= 2:
            ty = segs[-2]
            tc = [c for c in cands if c.parent and (c.parent.endswith(f"::{ty}") or f"<{ty} as " in c.parent)]
            if tc:
                return (tc if len(tc) <= 3 else []), HEURISTIC
            if ty[:1].isupper():
                return [], HEURISTIC
        same_mod = [c for c in cands if c.module == module and c.kind != "method"]
        if same_mod:
            return same_mod[:1], RESOLVED
        if len(segs) == 1:
            # `use super::*;` / `use crate::m::*;` glob imports (typical in `mod tests`)
            for (m, a), gpath in rf.use_map.items():
                if m != module or not a.startswith("*"):
                    continue
                gmod = self._abs_path(gpath, module, rf.crate)
                hit = [c for c in cands if c.module == gmod and c.kind != "method"]
                if hit:
                    return hit[:1], RESOLVED
        free = [c for c in cands if c.kind in ("function", "ffi")]
        if len(free) == 1:
            return free, HEURISTIC
        return [], HEURISTIC

    def _resolve_call(self, rf: RFile, owner: RItem, form: str, path: str, name: str, stats) -> tuple[list[RItem], str]:
        """Syntactic call resolution (heuristic mode, and code rust-analyzer does not analyse)."""
        if form == "path":
            return self._resolve_path(rf, owner, path)
        if name in STD_METHODS:
            stats["heuristic_skipped_std_method"] += 1
            return None, HEURISTIC
        cands = [c for c in self.by_name.get(name, []) if c.kind == "method"]
        traits = {c.parent for c in cands if c.parent and self.by_key.get(c.parent) is not None and self.by_key[c.parent].kind == "trait"}
        if len(cands) == 1:
            return cands, HEURISTIC
        if len(traits) == 1:
            return [c for c in cands if c.parent in traits][:1], HEURISTIC
        return [], HEURISTIC

    def _call_edges(self, rf: RFile, owner: RItem, form, path, name, line, col, stats, **attrs) -> bool:
        if form == "macro":
            m = [c for c in self.by_name.get(name, []) if c.kind == "macro"]
            if len(m) == 1:
                self.b.add_edge(self.nid(owner), self.nid(m[0]), "CALLS", rf.path, line, HEURISTIC, **attrs)
                return True
            return False
        targets, conf = self._resolve_call(rf, owner, form, path, name, stats)
        if targets is None:
            return None
        if not targets:
            return False
        line_txt = rf.lines[line - 1] if line - 1 < len(rf.lines) else ""
        for t in targets:
            self._ref_edge(self.nid(owner), self.nid(t), rf.path, line, conf, line_txt[col + len(name):].lstrip() or "(", stats, **attrs)
        return True

    def _cfg_inactive_refs(self, stats):
        """rust-analyzer analyses the host target: calls inside code compiled only for other targets (and calls to
        items that exist only there) have no SCIP occurrence. Those call sites are resolved syntactically instead,
        so `#[cfg(windows)]` code keeps its callees and callers on every target."""
        pos = getattr(self, "pos_sym", {})
        for rf in self.files.values():
            regions = [(a, z) for a, z, _p, _l, _o in rf.cfg_regions]
            file_cfg = bool(self.file_meta[rf.path]["cfgs"] or rf.inner_cfgs)
            for owner_key, form, path, name, line, col in rf.calls:
                if (rf.path, line, col) in pos or form == "macro" or (form == "method" and name in STD_METHODS):
                    continue
                owner = self.by_key.get(owner_key)
                if owner is None:
                    continue
                site_cfg = file_cfg or bool(owner.cfgs) or any(a <= line <= z for a, z in regions)
                if not site_cfg:
                    targets, _ = self._resolve_call(rf, owner, form, path, name, stats)
                    if not any(t.cfgs or self.file_meta.get(t.file, {}).get("cfgs") for t in targets or []):
                        continue
                if self._call_edges(rf, owner, form, path, name, line, col, stats, via="cfg-inactive"):
                    stats["cfg_inactive_calls"] += 1

    def _platform_marks(self):
        """Platform conditions (codegraph/platforms.py): #[cfg] on items, mod declarations, #![cfg] and statements."""
        from ...platforms import cfg_cond, mark
        b = self.b
        for rf in self.files.values():
            for pred, line in self.file_meta[rf.path]["cfgs"] + rf.inner_cfgs:
                mark(b, rf.path, 1, 10 ** 9, cfg_cond(pred), line=line)
            for it in rf.items:
                for pred, line in it.cfgs:
                    mark(b, rf.path, it.start, it.end, cfg_cond(pred), line=line)
            for start, end, pred, aline, _owner in rf.cfg_regions:
                mark(b, rf.path, start, end, cfg_cond(pred), line=aline, nodes=False)

    def _heuristic_refs(self, stats):
        for rf in self.files.values():
            for owner_key, form, path, name, line, col in rf.calls:
                owner = self.by_key.get(owner_key)
                if owner is None:
                    continue
                if self._call_edges(rf, owner, form, path, name, line, col, stats) is False and form != "macro":
                    stats["heuristic_unresolved_calls"] += 1
            for owner_key, name, line, col in rf.type_refs:
                owner = self.by_key.get(owner_key)
                if owner is None:
                    continue
                cands = [c for c in self.by_name.get(name, []) if c.kind in TYPES]
                local = [c for c in cands if c.module == owner.module]
                alias = rf.use_map.get((owner.module, name))
                if alias:
                    full = self._abs_path(alias, owner.module, rf.crate)
                    if full in self.by_key and self.by_key[full].kind in TYPES:
                        local = [self.by_key[full]]
                pick = local[:1] or (cands if len(cands) == 1 else [])
                for t in pick:
                    if self.nid(t) != self.nid(owner):
                        self.b.add_edge(self.nid(owner), self.nid(t), "USES_TYPE", rf.path, line, RESOLVED if local else HEURISTIC)
                        stats["edges_USES_TYPE"] += 1

    def _lookup_at(self, rel: str, line: int, col: int) -> list[str]:
        sym = getattr(self, "pos_sym", {}).get((rel, line, col))
        if sym:
            return [n for _, n in self.sym_nodes.get(sym, [])]
        return []

    # ------------------------------------------------------------------ impls / dispatch
    def _impls(self, stats, mode):
        conf = RESOLVED if mode == "scip" else HEURISTIC
        for rf in self.files.values():
            for imp in (it for it in rf.items if it.kind == "impl"):
                self_ids = self._lookup_at(rf.path, *imp.impl_self_pos) if imp.impl_self_pos else []
                if not self_ids:
                    c = [x for x in self.by_name.get(imp.impl_self, []) if x.kind in TYPES - {"trait"}]
                    loc = [x for x in c if x.module == imp.module]
                    self_ids = [self.nid(x) for x in (loc[:1] or (c if len(c) == 1 else []))]
                if not imp.impl_trait:
                    continue
                stats["trait_impls"] += 1
                tname = imp.impl_trait.split("<")[0]
                trait_ids = [t for t in (self._lookup_at(rf.path, *imp.impl_trait_pos) if imp.impl_trait_pos else []) if t.startswith("trait:")]
                tconf = conf
                if not trait_ids:
                    alias = rf.use_map.get((imp.module, tname))
                    cands = [x for x in self.by_name.get(tname, []) if x.kind == "trait"]
                    if alias:
                        full = self._abs_path(alias, imp.module, rf.crate)
                        cands = [x for x in cands if x.key == full] or cands
                    loc = [x for x in cands if x.module == imp.module]
                    pick = loc[:1] or (cands if len(cands) == 1 else [])
                    trait_ids = [self.nid(x) for x in pick]
                    tconf = HEURISTIC if mode == "scip" and pick else conf
                if not trait_ids:
                    # impl of a std / dependency trait (Default, From, Display, Drop, Iterator, a framework's Widget...):
                    # its methods are invoked implicitly by code outside the workspace, so link them from the Self type
                    # (heuristic): whatever uses the type may run them.
                    stats["trait_impls_external_trait"] += 1
                    for m in self.items:
                        if m.kind == "method" and m.parent == imp.key:
                            n = self.b.nodes.get(self.nid(m))
                            if n is not None:
                                n.attrs["external_trait"] = imp.impl_trait
                                for sid in self_ids:
                                    self.b.add_edge(sid, self.nid(m), "IMPLEMENTED_BY", rf.path, m.line, HEURISTIC,
                                                    via="external_trait", trait=imp.impl_trait)
                                    stats["external_trait_impl_edges"] += 1
                    continue
                tid = trait_ids[0]
                for sid in self_ids:
                    self.b.add_edge(sid, tid, "IMPLEMENTS", rf.path, imp.line, tconf)
                tkey = tid.split(":", 1)[1]
                for m in self.items:
                    if m.kind != "method" or m.parent != imp.key:
                        continue
                    tm = f"method:{tkey}::{m.name}"
                    if tm in self.b.nodes:
                        self.b.add_edge(tm, self.nid(m), "IMPLEMENTED_BY", rf.path, m.line, tconf)
                        stats["implemented_by_edges"] += 1
                    else:
                        stats["impl_methods_without_trait_decl"] += 1

    # ------------------------------------------------------------------ facts: env, unsafe, ffi, routes, cfg
    def _facts(self, stats, mode):
        b = self.b
        for rf in self.files.values():
            pkg = self.file_meta[rf.path]["pkg"].name
            for key, line, owner_key, how in rf.env:
                owner = self.by_key.get(owner_key)
                if owner is None:
                    continue
                b.add_node("env", key, name=key, lang="rust", attrs={"source": "compile-time" if how.endswith("!") else "runtime"})
                b.add_edge(self.nid(owner), f"env:{key}", "READS_ENV", rf.path, line, EXACT, how=how)
                stats["env_reads"] += 1
            for owner_key, path, line, how in rf.attr_refs:
                owner = self.by_key.get(owner_key)
                if owner is None:
                    continue
                targets, conf = self._resolve_path(rf, owner, path)
                if not targets and how == "with":
                    for fn in ("serialize", "deserialize"):
                        t, conf = self._resolve_path(rf, owner, f"{path}::{fn}")
                        targets += t
                for t in targets:
                    b.add_edge(self.nid(owner), self.nid(t), "REFERENCES_FN", rf.path, line, HEURISTIC if conf == HEURISTIC else RESOLVED,
                               via=f"attribute {how}=")
                    stats["attribute_fn_refs"] += 1
            for line, owner_key in rf.unsafe_blocks:
                owner = self.by_key.get(owner_key)
                if owner is None:
                    continue
                crate = rf.crate
                b.add_node("unsafe", crate, name=f"unsafe in {crate}", lang="rust", module=crate)
                b.add_edge(self.nid(owner), f"unsafe:{crate}", "USES_UNSAFE", rf.path, line, EXACT, how="unsafe block")
                stats["unsafe_blocks"] += 1
            for it in rf.items:
                if it.kind in ("function", "method") and it.unsafe:
                    b.add_node("unsafe", rf.crate, name=f"unsafe in {rf.crate}", lang="rust", module=rf.crate)
                    b.add_edge(self.nid(it), f"unsafe:{rf.crate}", "USES_UNSAFE", rf.path, it.line, EXACT, how="unsafe fn")
                    stats["unsafe_fns"] += 1
                if it.kind == "impl" and it.unsafe:
                    stats["unsafe_impls"] += 1
                if it.kind == "ffi":
                    stats["ffi_imports"] += 1
                # cfg gates
                own = it.cfgs
                if own and it.kind in CODE | TYPES | VALUES | {"mod", "macro"}:
                    for pred, line in own:
                        self._gate_edges(self.nid(it), pred, rf.path, line, pkg)
                # actix / rocket attribute routes
                for a in it.attrs:
                    short = a.split("::")[-1]
                    if it.kind in ("function", "method") and short in ROUTE_ATTRS:
                        self._attr_route(rf, it, a)
            for start, end, pred, aline, owner_key in rf.cfg_regions:
                owner = self.by_key.get(owner_key)
                if owner is not None:
                    self._gate_edges(self.nid(owner), pred, rf.path, aline, pkg)
            for verb, path, handler, line, col, owner_key, fw in rf.routes:
                owner = self.by_key.get(owner_key)
                hid = self._lookup_at(rf.path, line, col)
                conf = EXACT if hid else HEURISTIC
                if not hid and owner is not None:
                    t, c = self._resolve_path(rf, owner, handler)
                    hid = [self.nid(x) for x in t]
                    conf = c
                rid = b.add_node("route", f"{verb} {path}", name=f"{verb} {path}", file=rf.path, line=line, lang="rust",
                                 module=rf.module, attrs={"framework": fw, "handler": handler})
                b.nodes[rid].entry_kind = "http_route"
                for h in hid[:1]:
                    b.add_edge(rid, h, "ROUTES_TO", rf.path, line, conf)
                if owner is not None:
                    b.add_edge(self.nid(owner), rid, "REFERENCES_FN", rf.path, line, EXACT, how="router registration")
                stats["routes"] += 1

    def _attr_route(self, rf: RFile, it: RItem, attr: str):
        line_txt = "\n".join(rf.lines[max(0, it.start - 1 - 6):it.line])
        import re
        m = None
        for m in re.finditer(r"#\[\s*(?:[\w:]+::)?(get|post|put|delete|patch|head|options|route)\s*\(\s*\"([^\"]*)\"", line_txt):
            pass
        if not m:
            return
        verb = m.group(1).upper() if m.group(1) != "route" else "ANY"
        rid = self.b.add_node("route", f"{verb} {m.group(2)}", name=f"{verb} {m.group(2)}", file=rf.path, line=it.line, lang="rust",
                              module=rf.module, attrs={"framework": "actix/rocket attribute", "handler": it.key})
        self.b.nodes[rid].entry_kind = "http_route"
        self.b.add_edge(rid, self.nid(it), "ROUTES_TO", rf.path, it.line, EXACT)

    def _gate_edges(self, src: str, pred: str, rel: str, line: int, pkg: str):
        p = G.parse_cfg(pred)
        for key, val in G.cfg_atoms(p):
            if key == "test":
                continue
            if key == "feature" and val is not None:
                tgt = self.b.add_node("feature", f"{pkg}/{val}", name=f"{pkg}/{val}", lang="rust", module=pkg,
                                      attrs={"package": pkg})
                if val not in self.pkg_features.get(pkg, {}):
                    self.b.nodes[tgt].attrs["undeclared"] = True
            else:
                t = G.atom_text(key, val)
                tgt = self.b.add_node("cfg", t, name=t, lang="rust")
            self.b.add_edge(src, tgt, "GATED_BY", rel, line, EXACT, cfg=pred)

    # ------------------------------------------------------------------ entry points
    def _entries(self, stats):
        b = self.b
        for it in self.items:
            if it.kind not in ("function", "method"):
                continue
            meta = self.file_meta[it.file]
            tgt = meta["target"]
            kinds = []
            attrs = set(a.lstrip(":") for a in it.attrs)
            for a in attrs:
                if a in ENTRY_ATTRS:
                    k, rt = ENTRY_ATTRS[a]
                    if k == "main" and a == "main":
                        continue
                    kinds.append(k)
                    if rt:
                        b.nodes[self.nid(it)].attrs["runtime"] = rt
            root_main = it.kind == "function" and it.name == "main" and it.key == f"{tgt.crate}::main"
            if root_main:
                kinds.append({"bin": "main", "example": "example", "bench": "bench", "build": "build_script",
                              "test": "test"}.get(tgt.kind, "main"))
            if attrs & {"no_mangle", "export_name", "unsafe"} and it.kind == "function" and (it.abi or "no_mangle" in attrs or "export_name" in attrs):
                if "no_mangle" in attrs or "export_name" in attrs or any("no_mangle" in x for x in it.attrs):
                    kinds.append("ffi_export")
            if tgt.kind == "lib" and it.pub_chain and not it.in_test:
                kinds.append("public_api")
            if not kinds:
                continue
            k = min(kinds, key=ENTRY_PRIORITY.index)
            n = b.nodes.get(self.nid(it))
            if n is not None:
                n.entry_kind = k
                if len(set(kinds)) > 1:
                    n.attrs["entry_kinds"] = sorted(set(kinds), key=ENTRY_PRIORITY.index)
                stats[f"entry_{k}"] += 1

    # ------------------------------------------------------------------ gate scenario
    def _gating(self, stats):
        sc = self.scen
        if not sc.active:
            return
        dead: dict[str, list] = defaultdict(list)
        for rf in self.files.values():
            pkg = self.file_meta[rf.path]["pkg"].name
            for pred, line in self.file_meta[rf.path]["cfgs"] + rf.inner_cfgs:
                if sc.eval_cfg(G.parse_cfg(pred), pkg) is False:
                    dead[rf.path].append((1, 10 ** 9, pred, line))
            for it in rf.items:
                for pred, line in it.cfgs:
                    if sc.eval_cfg(G.parse_cfg(pred), pkg) is False:
                        dead[rf.path].append((it.start, it.end, pred, line))
            for start, end, pred, aline, _ in rf.cfg_regions:
                if sc.eval_cfg(G.parse_cfg(pred), pkg) is False:
                    dead[rf.path].append((start, end, pred, aline))
        n = 0
        for e in self.b.edges.values():
            if e.file in dead and e.line is not None and e.gate is None and e.kind != "GATED_BY":
                for a, z, pred, line in dead[e.file]:
                    if a <= e.line <= z:
                        e.gate = sc.name
                        e.attrs = {**e.attrs, "guard": f"{e.file}:{line}", "guard_expr": f"cfg({pred})"}
                        n += 1
                        break
        stats["gated_edges"] = n

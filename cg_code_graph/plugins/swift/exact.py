"""Swift exact layer: compiler-resolved call edges from the Swift index store.

The index store is what `swift build --enable-index-store` (SwiftPM) or Xcode (DerivedData `Index.noindex/DataStore`)
writes; it is read through the toolchain's libIndexStore (indexstore.py). Index-store definitions are matched to the
syntax layer's declarations by file, line and name, so node ids stay those of the heuristic layer; the heuristic
CALLS / INSTANTIATES edges of the covered files are replaced by the compiler's call occurrences (the caller comes from
the `calledBy` / `containedBy` relation).

Where the store comes from, in order:
  CG_SWIFT_INDEX_STORE=/path/to/store    an existing store (CI, Xcode DerivedData, a previous build)
  CG_SWIFT_INDEX=1                       run `swift build --enable-index-store` for a SwiftPM package (the build
                                                runs the package manifest and plugins, hence opt-in), with the build
                                                directory under the cg cache and a source fingerprint so an
                                                unchanged package is not rebuilt
Without one of those (or without a toolchain / libIndexStore) the heuristic layer stays and coverage says why.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from ...core.model import EXACT, HEURISTIC
from ...core import cache, fsutil
from ...core.env import get as cg_env
from ..native import runner
from . import indexstore as ix

# index-store names of accessor definitions (`getter:label`, `didSet:items`): owned by their property
ACCESSORS = ("getter:", "setter:", "_modify:", "modify:", "read:", "_read:", "willSet:", "didSet:")
ACCESSOR_ATTR = {"getter": "get", "setter": "set", "willSet": "willSet", "didSet": "didSet", "modify": "modify",
                 "_modify": "modify"}

CALL_KINDS = ("CALLS", "INSTANTIATES")


def find_swift() -> str | None:
    env = cg_env("SWIFT")
    if env:
        return env if (Path(env).exists() or shutil.which(env)) else None
    w = shutil.which("swift")
    if w:
        return w
    for pat in (str(Path.home() / "tools" / "swift-*" / "usr" / "bin" / "swift"), "/usr/share/swift/usr/bin/swift",
                "/opt/swift/usr/bin/swift"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def _store_dirs(build: Path) -> list[Path]:
    return [Path(p) for p in sorted(glob.glob(str(build / "*" / "debug" / "index" / "store")))
            + sorted(glob.glob(str(build / "debug" / "index" / "store")))]


def find_store(project, files: list[str]) -> tuple[Path | None, dict]:
    """(index store directory or None, info); info["status"] explains a missing store."""
    pre = cg_env("SWIFT_INDEX_STORE")
    swift = find_swift()
    lib = ix.find_lib(swift)
    if pre:
        if not Path(pre).is_dir():
            return None, {"status": f"CG_SWIFT_INDEX_STORE={pre} is not a directory"}
        if not lib:
            return None, {"status": "libIndexStore not found (a Swift toolchain or CG_LIBINDEXSTORE is needed "
                                    "to read the index store)"}
        return Path(pre), {"source": "CG_SWIFT_INDEX_STORE", "path": pre, "lib": lib}
    root = Path(project.root)
    if not (root / "Package.swift").is_file():
        return None, {"status": "no Package.swift at the project root (Xcode projects: set CG_SWIFT_INDEX_STORE "
                                "to DerivedData/<app>/Index.noindex/DataStore)"}
    if not swift:
        return None, {"status": "no Swift toolchain (swift not on PATH; CG_SWIFT)"}
    if not lib:
        return None, {"status": f"libIndexStore not found next to {swift} (CG_LIBINDEXSTORE)"}
    if cg_env("SWIFT_INDEX") != "1":
        return None, {"status": "Swift toolchain found but the package was not built: `swift build` runs the package "
                                "manifest and plugins; set CG_SWIFT_INDEX=1, or CG_SWIFT_INDEX_STORE to an "
                                "existing store (Xcode DerivedData)",
                      "toolchain": swift}
    return _build(root, files, swift, lib)


def _read_stamp(build: Path) -> dict:
    """The build stamp: `cg-stamp.json`, then the previous `codegraph-stamp.json` name."""
    for name in ("cg-stamp.json", "codegraph-stamp.json"):
        try:
            parsed = json.loads((build / name).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("key"):
            return parsed
    return {}


def _build(root: Path, files: list[str], swift: str, lib: str) -> tuple[Path | None, dict]:
    """`swift build --enable-index-store` into a per-project build directory under the cache; skipped when the sources
    and manifests are unchanged since the last successful build."""
    srcs = list(files) + [f for f in ("Package.swift", "Package.resolved") if (root / f).exists()]
    srcs += [p.name for p in root.glob("Package@swift-*.swift")]
    key = runner.fingerprint(root, srcs, f"swift-index|{runner.tool_version(swift, ('--version',))}")
    build = cache.subdir("swift-build") / cache.swift_build_key(root)
    stamp = build / "cg-stamp.json"
    info = {"source": "swift build", "toolchain": swift, "lib": lib, "build_path": str(build)}
    timeout = int(cg_env("INDEXER_TIMEOUT", "3600"))
    use_cache = cg_env("NO_CACHE") != "1"
    with runner.key_lock("swift", build.name, timeout) as waited:     # one build per build directory at a time
        if waited:
            info["lock_wait_seconds"] = waited
        done = _read_stamp(build)

        stores = _store_dirs(build)
        if use_cache and done.get("key") == key and stores:
            info["cache"] = "hit"
            return stores[0], info
        info["cache"] = "miss"
        build.mkdir(parents=True, exist_ok=True)
        cache.note_project(root)
        cmd = [swift, "build", "--enable-index-store", "--build-path", str(build)]
        info["command"] = " ".join(cmd[1:])
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            info["error"] = f"timeout after {timeout}s"
            info["status"] = f"swift build failed ({info['error']})"
            return None, info
        except OSError as e:
            info["error"] = str(e)
            info["status"] = f"swift build failed ({e})"
            return None, info
        info["seconds"] = round(time.time() - t0, 2)
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        info["stderr_tail"] = [x for x in out.strip().splitlines()[-8:]]
        stores = _store_dirs(build)
        if r.returncode != 0:
            errs = [x.strip() for x in out.splitlines() if re.search(r"\berror:", x)]
            info["error"] = f"exit {r.returncode}"
            info["notes"] = list(dict.fromkeys(errs))[:6]
            first = re.sub(r"^/\S*/", "", errs[0].replace(str(root) + os.sep, ""))[:160] if errs else ""
            if not stores:
                info["status"] = "swift build failed (" + info["error"] + (f": {first}" if first else "") + ")"
                return None, info
            # a partial build still indexed the modules that compiled
            info["partial"] = True
            info["build_error"] = first
        else:
            stamp.write_text(json.dumps({"key": key, "cache_version": fsutil.CACHE_VERSION}))
        return (stores[0] if stores else None), info


class ExactLayer:
    def __init__(self, plugin):
        self.p = plugin
        self.b = plugin.b

    def _all_decls(self):
        return list(self.p.decls.values())

    def apply(self, store: Path, lib: str, root: Path, files: list[str], st: dict) -> bool:
        occ_by_path = ix.load(store, None, lib)
        rels = self._map_paths(occ_by_path, root, files)
        st["index_files"] = len(rels)
        if not rels:
            st["index_units_outside_project"] = len(occ_by_path)
            return False
        by_line = defaultdict(list)
        for d in self._all_decls():
            by_line[(d.file, d.line)].append(d)
        usr_node: dict[str, str] = {}
        ctor_type: dict[str, str] = {}
        matched = unmatched = 0
        accessor_of: dict[str, str] = {}
        accessor_kind: dict[str, str] = {}   # accessor usr -> "getter" | "setter" | "didSet" | ...
        prop_def: dict[str, str] = {}
        prop_how: dict[str, str] = {}        # property usr -> "computed" | "observed" | "lazy" (property nodes)
        parent_of: dict[str, str] = {}       # definition usr -> enclosing type / extension usr (childOf)
        ext_of: dict[str, str] = {}          # extension usr -> extended type usr
        for rel, occs in rels.items():
            for o in occs:
                if not o.roles & ix.DEFINITION:
                    for rr, ru, _rn, _rk in o.rels:
                        if rr & ix.REL_EXTENDEDBY and o.kind in ix.TYPES:      # `extension DispatchQueue { }`
                            ext_of[ru] = o.usr
                            if o.usr not in usr_node:
                                nid = self._decl(rel, o.line, o.name, ("class",), by_line) or \
                                    (f"class:{o.name}" if f"class:{o.name}" in self.b.nodes else None)
                                if nid:
                                    usr_node[o.usr] = nid
                    continue
                for rr, ru, _rn, _rk in o.rels:
                    if rr & ix.REL_CHILDOF:
                        parent_of.setdefault(o.usr, ru)
                if o.kind in ("instancemethod", "function", "staticmethod", "classmethod") and \
                        o.name.startswith(ACCESSORS):
                    for rr, ru, _rn, _rk in o.rels:
                        if rr & ix.REL_ACCESSOROF:
                            accessor_of[o.usr] = ru
                            accessor_kind[o.usr] = o.name.split(":", 1)[0]
                    continue
                if o.kind in ("instanceproperty", "staticproperty", "classproperty", "variable"):
                    if o.name == "body":
                        prop_def[o.usr] = self._decl(rel, o.line, "body", ("method",), by_line) or ""
                    else:
                        d = self._prop_decl(rel, o.line, o.name, by_line)
                        if d is not None:             # a computed / observed / lazy property node (#72)
                            prop_def[o.usr] = d.id
                            prop_how[o.usr] = next(m for m in ("computed", "observed", "lazy") if m in d.modifiers)
                    continue
                if o.kind not in ix.CALLABLE and o.kind not in ix.TYPES:
                    continue
                base = "init" if o.kind == "constructor" else o.name.split("(")[0]
                kinds = ("class",) if o.kind in ix.TYPES else ("function", "method")
                nid = self._decl(rel, o.line, base, kinds, by_line)
                if nid is None and o.kind != "constructor":
                    nid = self._decl_by_name(rel, base, kinds, o)
                if o.kind == "constructor":
                    parent = next((ru for rr, ru, _rn, rk in o.rels if rr & ix.REL_CHILDOF), None)
                    if parent:
                        ctor_type[o.usr] = parent
                if nid is None:
                    if o.kind != "constructor":
                        unmatched += 1
                    continue
                usr_node[o.usr] = nid
                matched += 1
        for acc, prop in accessor_of.items():         # SwiftUI `body` getter -> method:<View>.body
            if prop_def.get(prop):
                usr_node[acc] = prop_def[prop]
        st["index_defs_matched"] = matched
        st["index_defs_unmatched"] = unmatched
        def owner(usr: str, depth: int = 0) -> str | None:
            """Node of a caller symbol: itself, the `body` property of an accessor, else its enclosing type
            (accessors -> property -> type; extension -> extended type)."""
            if not usr or depth > 6:
                return None
            if usr in usr_node:
                return usr_node[usr]
            prop = accessor_of.get(usr)
            if prop:
                if prop_def.get(prop):
                    return prop_def[prop]
                return owner(prop, depth + 1)
            if usr in ext_of:
                return owner(ext_of[usr], depth + 1)
            return owner(parent_of.get(usr, ""), depth + 1)

        covered = set(rels)
        lambdas = defaultdict(list)     # Vapor route handlers / BGTask closures own the calls inside them
        for nid, n in self.b.nodes.items():
            if n.lang == "swift" and n.kind == "function" and n.attrs.get("lambda") and n.file in covered and n.line:
                lambdas[n.file].append((n.line, n.end_line or n.line, nid))
        seen_lines = {(rel, o.line) for rel, occs in rels.items() for o in occs}
        heur, kept = self._remove_heuristic(covered, seen_lines)
        st["heuristic_kept_not_compiled"] = kept
        exact = set()
        refs = ext = 0
        for rel, occs in rels.items():
            fid = f"file:swift:{rel}"
            for o in occs:
                if o.roles & ix.DEFINITION or not (o.roles & ix.CALL or o.kind == "constructor"):
                    continue
                if o.kind not in ix.CALLABLE:
                    continue
                prop = accessor_of.get(o.usr) if o.name.startswith(ACCESSORS) else None
                if o.name.startswith(ACCESSORS) and not (prop in prop_how and self._runs(accessor_kind.get(o.usr, ""),
                                                                                         prop_how[prop])):
                    continue
                if prop is not None:
                    # a read (getter) or write (setter / modify) of a property node: a call of it (#72)
                    targets = [(prop_def[prop], "CALLS")]
                elif o.kind == "constructor":
                    tt = ctor_type.get(o.usr, "")
                    # an initializer declared in an extension: its parent is the extension, whose node is the type's
                    tnode = usr_node.get(tt) or usr_node.get(ext_of.get(tt, ""))
                    if tnode is None:
                        ext += 1
                        continue
                    targets = [(tnode, "INSTANTIATES")]
                    if o.usr in usr_node:
                        targets.append((usr_node[o.usr], "CALLS"))
                else:
                    t = usr_node.get(o.usr)
                    if t is None:
                        ext += 1
                        continue
                    targets = [(t, "CALLS")]
                src = None
                acc = {}
                for rr, ru, _rn, _rk in o.rels:
                    if rr & (ix.REL_CALLEDBY | ix.REL_CONTAINEDBY):
                        src = owner(ru)
                        if src is not None:
                            acc = self._accessor_attr(src, accessor_kind.get(ru))
                            break
                if src is None:
                    src = self._owner_type(rel, o.line) or fid
                if src.startswith("class:"):
                    # the initializer of a `lazy var` belongs to the type in the store; here to the property node
                    pd = next((d for d in self.p.props_in.get(rel, ()) if d.line <= o.line <= d.end
                               and f"class:{d.cls}" == src), None)
                    src = pd.id if pd is not None else src
                lam = min((x for x in lambdas.get(rel, ()) if x[0] <= o.line <= x[1]),
                          key=lambda x: x[1] - x[0], default=None)
                if lam is not None:
                    src = lam[2]
                for dst, kind in targets:
                    if (src == dst and kind == "CALLS") or src not in self.b.nodes or dst not in self.b.nodes:
                        continue
                    if prop is not None:
                        acc = {**acc, "property": "read" if accessor_kind.get(o.usr) == "getter" else "write"}
                    br = self.p.branch_at.get((rel, o.line, self.b.nodes[dst].name), {}) \
                        if kind == "INSTANTIATES" else {}
                    self.b.add_edge(src, dst, kind, rel, o.line, EXACT, source="indexstore",
                                    **({"dynamic": True} if o.roles & ix.DYNAMIC else {}), **acc, **br)
                    exact.add((src, dst, kind))
                    refs += 1
        st["index_references"] = refs
        st["index_refs_external"] = ext
        agree = len(heur & exact)
        st["exact_vs_heuristic"] = {
            "heuristic_edges": len(heur), "exact_edges": len(exact), "agree": agree,
            "precision": round(agree / len(heur), 3) if heur else None,
            "recall": round(agree / len(exact), 3) if exact else None,
            # candidate edges (receiver type unknown, one per same-name method, #83) are compared on their own
            "candidate_edges": len(self.candidates), "candidate_agree": len(self.candidates & exact)}
        return True

    def _map_paths(self, occ_by_path: dict, root: Path, files: list[str]) -> dict:
        """Index-store source paths -> project-relative paths: under the project root, else by the longest
        project path suffix (a store built in another checkout or on another machine)."""
        rootr = str(Path(root).resolve()) + os.sep
        fset = set(files)
        by_name = defaultdict(list)
        for f in files:
            by_name[f.rsplit("/", 1)[-1]].append(f)
        out = {}
        for p, occs in occ_by_path.items():
            rel = None
            if p.startswith(rootr) and p[len(rootr):] in fset:
                rel = p[len(rootr):]
            else:
                cands = [f for f in by_name.get(p.rsplit("/", 1)[-1], []) if p.endswith("/" + f)]
                rel = max(cands, key=len) if cands else None
            if rel is not None:
                out[rel] = occs
        return out

    @staticmethod
    def _runs(accessor: str, how: str) -> bool:
        """Does a use of this accessor run code of a property node? A read of a computed or lazy property, a write of
        a computed or observed one (the getter of a stored property with observers is the plain storage)."""
        if accessor in ("getter", "read", "_read"):
            return how in ("computed", "lazy")
        if accessor in ("setter", "modify", "_modify"):
            return how in ("computed", "observed")
        return False

    def _accessor_attr(self, src: str, accessor: str | None) -> dict:
        """{"accessor": "didSet"} for a call inside an explicit accessor of a property node (as the heuristic pass)."""
        name = ACCESSOR_ATTR.get(accessor or "")
        n = self.b.nodes.get(src)
        return {"accessor": name} if name and n is not None and name in (n.attrs.get("accessors") or ()) else {}

    def _prop_decl(self, rel, line, name, by_line):
        """The property node of a property definition: by its line, else (attributes on the lines above the name)
        the one of that name whose range holds the line."""
        for d in by_line.get((rel, line), ()):
            if d.name == name and "property" in d.modifiers:
                return d
        for d in self.p.props_in.get(rel, ()):
            if d.name == name and d.line <= line <= d.end:
                return d
        return None

    def _decl(self, rel, line, name, kinds, by_line):
        for d in by_line.get((rel, line), ()):
            if d.name == name and d.kind in kinds:
                return d.id
        return None

    def _decl_by_name(self, rel, name, kinds, o):
        """An overload (one node per name) or a declaration whose name line differs from the node's line."""
        parent = next((rn for rr, _ru, rn, _rk in o.rels if rr & ix.REL_CHILDOF), None)
        for d in self._all_decls():
            if d.file == rel and d.name == name and d.kind in kinds and "property" not in d.modifiers and \
                    (parent is None or (d.cls or "").split(".")[-1] == parent or d.kind == "class"):
                if d.line <= o.line <= d.end or d.kind != "class":
                    return d.id
        return None

    def _owner_type(self, rel, line):
        best = None
        for d in self._all_decls():
            if d.file == rel and d.kind == "class" and d.line <= line <= d.end:
                if best is None or d.end - d.line < best.end - best.line:
                    best = d
        return best.id if best else None

    def _remove_heuristic(self, covered: set, seen_lines: set) -> tuple[set, int]:
        """Drop the heuristic call edges of the covered files; returns their (src, dst, kind) set. An edge on a line
        without any index occurrence is code the compiler did not see (an inactive `#if` branch on this platform):
        it is kept, marked `via: not-compiled`, and left out of the comparison. Candidate edges (#83) go to
        self.candidates, compared on their own."""
        out, cand, kept = set(), set(), 0
        for key, e in list(self.b.edges.items()):
            if e.confidence != HEURISTIC or e.file not in covered or e.kind not in CALL_KINDS:
                continue
            n = self.b.nodes.get(e.src)
            if n is None or n.lang != "swift":
                continue
            if (e.file, e.line) not in seen_lines:
                e.attrs["via"] = "not-compiled"
                kept += 1
                continue
            (cand if e.attrs.get("binding") == "candidate" else out).add((e.src, e.dst, e.kind))
            del self.b.edges[key]
        self.candidates = cand
        return out, kept

"""Python source roots: the directories whose contents are importable by top-level name (what `sys.path` holds when
the code runs), each with the reason it was chosen.

Detection (no configuration needed), in this order of evidence:
  * the indexed root;
  * packaging config at the root and in nested projects: `pyproject.toml` (`[tool.setuptools] package-dir`,
    `packages.find.where`, `[tool.hatch.build] packages / sources`, `[tool.poetry] packages = [{from = ...}]`,
    `[tool.pdm.build] package-dir`, `[tool.maturin] python-source`), `setup.cfg` (`[options] package_dir`,
    `[options.packages.find] where`) and literal `package_dir=` / `find_packages(where)` in `setup.py`;
  * conventional directories `src/`, `lib/`, `python/` that are not packages themselves;
  * nested projects: directories holding `pyproject.toml`, `setup.py`, `setup.cfg` or `manage.py` (depth <= 4);
  * namespace packages (PEP 420): a directory without `__init__.py` that other files import as a package
    (`import acme.core` -> the parent of `acme/` is a root);
  * the parent of every top-level package (a directory with `__init__.py` whose parent has none).
Configured roots (`python.source_roots` in `.cg.yaml`, or `cg index --python-root`) replace detection.

A file reachable from several roots gets one canonical module name: the one under the root whose top-level name the
project's own imports use most (ties: the deepest root). The other names stay importable aliases."""
from __future__ import annotations

import ast
import configparser
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

NESTED_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "manage.py")
CONVENTIONAL = ("src", "lib", "python")
NESTED_DEPTH = 4
MAX_RECORDED = 200


@dataclass
class SourceRoot:
    path: str                     # repo-relative directory, "" = the indexed root
    origin: str                   # detected | configured | flag
    why: list = field(default_factory=list)
    prefix: str = ""              # package name the directory maps to (setuptools package-dir {"pkg": "dir"})

    @property
    def key(self) -> tuple:
        return (self.path, self.prefix)

    @property
    def depth(self) -> int:
        return len(PurePosixPath(self.path).parts) if self.path else 0

    def label(self) -> str:
        return (self.path or ".") + "/"


def _rel(base: str, sub: str) -> str | None:
    """Join a packaging-config path to the project dir; None when it leaves the indexed root."""
    p = PurePosixPath(base) / PurePosixPath(str(sub).strip().replace("\\", "/") or ".")
    parts = []
    for x in p.parts:
        if x in (".", ""):
            continue
        if x == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(x)
    return "/".join(parts)


def _pyproject_roots(path: Path) -> list[tuple[str, str, str]]:
    """[(dir, prefix, why)] from a pyproject.toml (dirs relative to its directory)."""
    try:
        import tomllib
        d = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001  (unreadable / invalid TOML: no packaging evidence)
        return []
    tool = d.get("tool") if isinstance(d.get("tool"), dict) else {}
    out = []
    st = tool.get("setuptools") if isinstance(tool.get("setuptools"), dict) else {}
    pd = st.get("package-dir")
    if isinstance(pd, dict):
        for k, v in pd.items():
            if isinstance(k, str) and isinstance(v, str):
                out.append(_mapping(k, v, "pyproject.toml [tool.setuptools] package-dir"))
    pk = st.get("packages")
    find = pk.get("find") if isinstance(pk, dict) else None
    if isinstance(find, dict):
        where = find.get("where")
        for w in ([where] if isinstance(where, str) else where if isinstance(where, list) else []):
            if isinstance(w, str):
                out.append((w, "", "pyproject.toml [tool.setuptools.packages.find] where"))
    hatch = tool.get("hatch") if isinstance(tool.get("hatch"), dict) else {}
    build = hatch.get("build") if isinstance(hatch.get("build"), dict) else {}
    targets = build.get("targets") if isinstance(build.get("targets"), dict) else {}
    for where, sect in [("[tool.hatch.build]", build)] + [(f"[tool.hatch.build.targets.{t}]", v) for t, v in targets.items()
                                                          if isinstance(v, dict)]:
        for p in sect.get("packages") or []:
            if isinstance(p, str):
                parent = str(PurePosixPath(p).parent)
                out.append((parent, "", f"pyproject.toml {where} packages"))
        src = sect.get("sources")
        if isinstance(src, list):
            out += [(s, "", f"pyproject.toml {where} sources") for s in src if isinstance(s, str)]
        elif isinstance(src, dict):
            for k, v in src.items():
                if isinstance(k, str) and isinstance(v, str):
                    out.append((k, v.strip("/").replace("/", "."), f"pyproject.toml {where} sources"))
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    for p in poetry.get("packages") or []:
        if isinstance(p, dict) and isinstance(p.get("from"), str):
            out.append((p["from"], "", "pyproject.toml [tool.poetry] packages from"))
    pdm = tool.get("pdm") if isinstance(tool.get("pdm"), dict) else {}
    for where, sect in (("[tool.pdm.build]", pdm.get("build")), ("[tool.pdm]", pdm)):
        if isinstance(sect, dict) and isinstance(sect.get("package-dir"), str):
            out.append((sect["package-dir"], "", f"pyproject.toml {where} package-dir"))
    mat = tool.get("maturin") if isinstance(tool.get("maturin"), dict) else {}
    if isinstance(mat.get("python-source"), str):
        out.append((mat["python-source"], "", "pyproject.toml [tool.maturin] python-source"))
    return out


def _mapping(pkg: str, d: str, why: str) -> tuple[str, str, str]:
    """setuptools package_dir entry: {"": "src"} -> root src; {"core": "lib/core"} -> root lib; {"core": "lib"} ->
    lib/ holds the contents of package core."""
    pkg = pkg.strip()
    d = d.strip()
    if not pkg:
        return (d, "", why)
    last = pkg.split(".")[-1]
    if PurePosixPath(d).name == last:
        parent = str(PurePosixPath(d).parent)
        return (parent, ".".join(pkg.split(".")[:-1]), why)
    return (d, pkg, why)


def _setup_cfg_roots(path: Path) -> list[tuple[str, str, str]]:
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    if cp.has_option("options", "package_dir"):
        for line in cp.get("options", "package_dir").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    out.append(_mapping(k, v, "setup.cfg [options] package_dir"))
    if cp.has_option("options.packages.find", "where"):
        for w in cp.get("options.packages.find", "where").split():
            out.append((w, "", "setup.cfg [options.packages.find] where"))
    return out


def _setup_py_roots(path: Path) -> list[tuple[str, str, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, RecursionError, OSError):
        return []
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fname = n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else ""
        if fname == "setup":
            for k in n.keywords:
                if k.arg == "package_dir" and isinstance(k.value, ast.Dict):
                    for kk, vv in zip(k.value.keys, k.value.values):
                        if isinstance(kk, ast.Constant) and isinstance(kk.value, str) and \
                                isinstance(vv, ast.Constant) and isinstance(vv.value, str):
                            out.append(_mapping(kk.value, vv.value, "setup.py package_dir"))
        elif fname in ("find_packages", "find_namespace_packages"):
            w = n.args[0] if n.args else next((k.value for k in n.keywords if k.arg == "where"), None)
            if isinstance(w, ast.Constant) and isinstance(w.value, str):
                out.append((w.value, "", f"setup.py {fname}({w.value!r})"))
    return out


def packaging_roots(root: Path, project_dir: str) -> list[tuple[str, str, str]]:
    """Source roots stated by the packaging config of the project at `project_dir` (repo-relative)."""
    base = root / project_dir if project_dir else root
    out = []
    for fn, fnc in (("pyproject.toml", _pyproject_roots), ("setup.cfg", _setup_cfg_roots), ("setup.py", _setup_py_roots)):
        p = base / fn
        if p.is_file():
            for d, prefix, why in fnc(p):
                rel = _rel(project_dir, d)
                if rel is not None:
                    where = f"{project_dir}/{why}" if project_dir else why
                    out.append((rel, prefix, where))
    return out


def import_names(trees) -> Counter:
    """Absolute dotted names the given module ASTs import, with how often: `import a.b` -> a.b; `from a.b import c`
    -> a.b and a.b.c (c may be a submodule)."""
    out: Counter = Counter()
    for imps in trees:
        for st in imps:
            if isinstance(st, ast.Import):
                for a in st.names:
                    out[a.name] += 1
            elif isinstance(st, ast.ImportFrom) and not st.level and st.module:
                out[st.module] += 1
                for a in st.names:
                    if a.name != "*":
                        out[f"{st.module}.{a.name}"] += 1
    return out


class RootPlan:
    """The chosen source roots, plus the canonical module name of every file."""

    def __init__(self, root: Path, files: list[str], imports: Counter, configured: list[str] | None = None,
                 origin: str = "configured", skip_dirs=frozenset()):
        self.root = root
        self.files = files
        self.imports = imports
        self.warnings: list[str] = []
        self.ambiguous: list[dict] = []
        self.collisions: list[dict] = []
        self.n_collisions = 0       # files whose preferred module name another file holds
        self.n_requalified = 0      # files named by their path from the indexed root because their tree clashed
        self.mode = origin if configured is not None else "detected"
        self.roots: dict[tuple, SourceRoot] = {}
        self._skip = skip_dirs
        mods = [f[:-3] for f in files]
        self.file_set = set(files)
        self.dirs: set[str] = set()
        for f in files:
            p = PurePosixPath(f).parent
            while str(p) != ".":
                self.dirs.add(str(p))
                p = p.parent
        self.pkg_dirs = {str(PurePosixPath(m).parent) for m in mods if PurePosixPath(m).name == "__init__"}
        self.pkg_dirs.discard(".")
        self.root_is_pkg = "__init__.py" in self.file_set
        if configured is not None:
            for c in configured:
                if c and not (root / c).is_dir():
                    self.warnings.append(f"configured source root {c}/ does not exist")
                    continue
                self._add(c, origin, "configured in .cg.yaml" if origin == "configured" else "--python-root")
            if not self.roots:
                self.warnings.append("no source root: none of the configured source roots exists")
        else:
            self._detect()

    # ---- detection
    def _add(self, path: str, origin: str, why: str, prefix: str = "") -> None:
        if path and path not in self.dirs and origin == "detected":
            return  # no .py file below it
        r = self.roots.get((path, prefix))
        if r is None:
            self.roots[(path, prefix)] = SourceRoot(path, origin, [why], prefix)
        elif why not in r.why:
            r.why.append(why)

    def _nested_projects(self) -> list[str]:
        """Directories (depth <= NESTED_DEPTH) holding a project marker, not packages themselves, with .py files below."""
        out = []
        for d in sorted(self.dirs):
            if d.count("/") + 1 > NESTED_DEPTH or d in self.pkg_dirs:
                continue
            if any(x in self._skip for x in d.split("/")):
                continue
            if f"{d}/manage.py" in self.file_set or any((self.root / d / m).is_file() for m in NESTED_MARKERS[:3]):
                out.append(d)
        return out

    def _detect(self) -> None:
        self._add("", "detected", "indexed root")
        projects = [""] + self._nested_projects()
        for pd in projects:
            if pd:
                marker = next(m for m in NESTED_MARKERS if (self.root / pd / m).exists())
                self._add(pd, "detected", f"nested project ({pd}/{marker})")
            for d, prefix, why in packaging_roots(self.root, pd):
                self._add(d, "detected", why, prefix)
            for c in CONVENTIONAL:
                d = f"{pd}/{c}" if pd else c
                if d in self.dirs and d not in self.pkg_dirs:
                    self._add(d, "detected", f"{c}/ directory")
        # namespace packages: a directory without __init__.py imported as a package (acme.core -> parent of acme/)
        by_name: dict[str, list[str]] = {}
        for d in self.dirs:
            if d not in self.pkg_dirs:
                by_name.setdefault(PurePosixPath(d).name, []).append(d)
        ns_dirs = set()
        for name in sorted(self.imports):
            parts = name.split(".")
            if len(parts) < 2 or parts[0] not in by_name:
                continue
            for d in by_name[parts[0]]:
                sub = f"{d}/{parts[1]}"
                if sub not in self.dirs and f"{sub}.py" not in self.file_set:
                    continue
                parent = str(PurePosixPath(d).parent)
                parent = "" if parent == "." else parent
                if parent in self.pkg_dirs or (not parent and self.root_is_pkg):
                    continue
                ns_dirs.add(d)
                self._add(parent, "detected", f"namespace package {parts[0]} (imported as {parts[0]}.{parts[1]})")
        # the parent of every top-level package (several package roots in one repo)
        for d in sorted(self.pkg_dirs):
            name = PurePosixPath(d).name
            parent = str(PurePosixPath(d).parent)
            parent = "" if parent == "." else parent
            if not name.isidentifier() or parent in self.pkg_dirs or parent in ns_dirs:
                continue
            if any(parent == n or parent.startswith(n + "/") for n in ns_dirs):
                continue  # inside a namespace package: the namespace's parent is the root
            self._add(parent, "detected", f"parent of top-level package {name}")

    # ---- naming
    def candidates(self, rel: str) -> list[tuple[str, bool, SourceRoot]]:
        """Importable (name, is_pkg, root) of a file under every root that contains it."""
        p = PurePosixPath(rel)
        mp = list(p.with_suffix("").parts)
        out = []
        for i in range(len(mp)):  # i = number of leading path parts that form the root directory
            base = "/".join(mp[:i])
            for prefix in self._prefixes.get(base, ()):
                r = self.roots[(base, prefix)]
                sub = mp[i:]
                is_pkg = sub[-1] == "__init__"
                if is_pkg:
                    sub = sub[:-1]
                if prefix:
                    sub = prefix.split(".") + sub
                if sub and all(x.isidentifier() for x in sub):
                    out.append((".".join(sub), is_pkg, r))
        return out

    def assign(self) -> tuple[dict, list[str]]:
        """{file: (canonical name, is_pkg, [alias names])} and the files no root maps (unmapped)."""
        self._prefixes: dict[str, list[str]] = {}
        for (path, prefix) in self.roots:
            self._prefixes.setdefault(path, []).append(prefix)
        cands = {f: self.candidates(f) for f in self.files}
        # import evidence per (root, top-level name): how often the project's own imports use names under it
        index: dict[str, list[tuple]] = {}
        for f, cs in cands.items():
            for name, _, r in cs:
                index.setdefault(name, []).append((r.key, name.split(".")[0]))
        score: Counter = Counter()
        for name, n in self.imports.items():
            for k in set(index.get(name, ())):
                score[k] += n
        prefs: dict[str, list] = {}
        out, unmapped = {}, []
        for f, cs in cands.items():
            ranked = sorted(cs, key=lambda c: (-score[(c[2].key, c[0].split(".")[0])], -c[2].depth, len(c[0])))
            prefs[f] = ranked
            used = [c for c in ranked if score[(c[2].key, c[0].split(".")[0])] > 0]
            if len(used) > 1 and len(self.ambiguous) < MAX_RECORDED:
                self.ambiguous.append({"file": f, "chosen": used[0][0], "also": [c[0] for c in used[1:3]],
                                       "imports": [score[(c[2].key, c[0].split(".")[0])] for c in used[:3]]})
        # one module name per file. A package tree (files preferring the same root and top-level name) keeps its names
        # together: when another tree already holds one of them, the whole tree falls back to its path from the indexed
        # root (unique, and relative imports inside the tree still resolve). Trees with more import evidence go first,
        # then trees without another importable name, then the shallower root.
        groups: dict[tuple, list[str]] = {}
        for f, ranked in prefs.items():
            if ranked:
                groups.setdefault((ranked[0][2].key, ranked[0][0].split(".")[0]), []).append(f)
            else:
                unmapped.append(f)

        def gkey(item):
            (rk, anchor), fs = item
            alt = any(len(prefs[f]) > 1 for f in fs)
            return (-score[(rk, anchor)], alt, len(PurePosixPath(rk[0]).parts) if rk[0] else 0, rk, anchor)

        taken: dict[str, str] = {}
        for (rk, anchor), fs in sorted(groups.items(), key=gkey):
            fs = sorted(fs, key=lambda f: (not prefs[f][0][1], f))       # pkg/__init__.py before a pkg.py twin
            clash = next((f for f in fs if prefs[f][0][0] in taken), None)
            for f in fs:
                c = prefs[f][0]
                name, is_pkg, root = c[0], c[1], c[2]
                if clash is not None or name in taken:
                    mp = list(PurePosixPath(f).with_suffix("").parts)
                    if mp[-1] == "__init__":
                        mp = mp[:-1]
                    fallback = ".".join(mp)
                    ok = bool(fallback) and fallback not in taken
                    if name in taken:
                        self.n_collisions += 1
                        if len(self.collisions) < MAX_RECORDED:
                            self.collisions.append({"file": f, "name": name, "with": taken[name],
                                                    **({"named": fallback} if ok else {})})
                    if not ok:
                        unmapped.append(f)
                        continue
                    self.n_requalified += 1
                    name, root = fallback, None
                taken[name] = f
                out[f] = (name, is_pkg, [x[0] for x in prefs[f] if x[0] != name], root)
        return out, sorted(unmapped)

    def report(self, owners: Counter) -> list[dict]:
        """Roots that own at least one canonical module, with origin and reason (for stats / coverage)."""
        out = []
        for r in sorted(self.roots.values(), key=lambda r: (r.path, r.prefix)):
            n = owners.get(r.key, 0)
            if not n and self.mode == "detected":
                continue
            e = {"path": r.label(), "origin": r.origin, "why": "; ".join(r.why[:2]) + (f" (+{len(r.why) - 2})" if len(r.why) > 2 else ""),
                 "modules": n}
            if r.prefix:
                e["package"] = r.prefix
            out.append(e)
        return out[:MAX_RECORDED]

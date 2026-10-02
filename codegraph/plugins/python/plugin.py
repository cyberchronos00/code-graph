"""Python language plugin (stdlib `ast`, no code execution).

Parses every .py file once and builds:
  * a module table (dotted names under each source root, detected or configured: see `roots.py`), with import
    resolution (absolute, relative, `import a.b as c`, re-exports through `__init__`, `from x import *`);
  * symbol tables for classes / functions / methods (nested functions and lambdas collapse into their
    enclosing definition) and class bases resolved to local classes or external dotted names
    (`django.db.models.Model`);
  * a flow-insensitive per-function type inference: assignments, annotations (incl. Optional/list),
    `self.x = ...` attributes, `for x in ...`, return annotations; framework plugins extend it with
    `attr_rules` / `call_rules` (e.g. Django `Model.objects.filter()` -> queryset of Model);
  * edges: IMPORTS, CALLS (exact: direct names / self / class instantiation; resolved: inferred
    receiver type; heuristic: unique method name), INSTANTIATES, EXTENDS, CONTAINS, READS_ENV
    (os.environ / os.getenv / django-environ / python-decouple).

Framework plugins (Django) get the `PyProgram` as their context and walk function bodies with
`prog.infer()` / `prog.resolve_call()`.
"""
from __future__ import annotations

import ast
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...core.fsutil import keep_file
from ...core.plugin import FrameworkPlugin, GraphBuilder, LanguagePlugin, Project
from .roots import RootPlan, import_names

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", ".env", "__pycache__", "site-packages", ".tox", ".nox",
             "build", "dist", ".mypy_cache", ".eggs", ".pytest_cache", ".ruff_cache", "htmlcov", "static", "media"}
# directories that never hold project code when deciding whether a repository has Python at all
DETECT_SKIP = SKIP_DIRS | {"vendor", "target", "out", "Pods", ".next", ".output"}
MAX_FILE_BYTES = 1_500_000
# method names too generic for the unique-name fallback
STOP_METHODS = {"get", "set", "save", "delete", "update", "filter", "all", "items", "keys", "values", "append", "extend",
                "add", "remove", "pop", "clear", "copy", "run", "call", "send", "close", "open", "read", "write", "start",
                "stop", "create", "list", "retrieve", "destroy", "format", "join", "split", "strip", "replace", "encode",
                "decode", "load", "loads", "dump", "dumps", "first", "last", "count", "exists", "order_by", "exclude",
                "next", "iter", "log", "info", "debug", "warning", "error", "exception", "handle", "process", "execute",
                "render", "clean", "validate", "is_valid", "to_representation", "to_internal_value", "as_view", "dispatch",
                "setup", "teardown", "setUp", "tearDown", "main", "lower", "upper", "sort", "index", "insert", "connect",
                "disconnect", "receive", "emit", "apply", "map", "reduce", "flush", "reset", "refresh", "build", "parse",
                "serialize", "deserialize", "post", "put", "patch", "head", "options", "partial_update", "perform_create",
                "perform_update", "perform_destroy", "get_queryset", "get_object", "get_serializer", "get_context_data",
                "form_valid", "form_invalid", "has_permission", "has_object_permission", "authenticate", "init", "submit"}


ASYNC_WRAPPERS = {"sync_to_async", "async_to_sync", "database_sync_to_async", "run_sync"}


@dataclass
class FuncInfo:
    name: str
    qual: str            # dotted fqn (module.Class.method / module.func)
    module: "ModInfo"
    node: Any
    cls: "ClassInfo | None" = None
    kind: str = "function"   # function | method
    decorators: list = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.qual}"

    @property
    def line(self) -> int:
        return self.node.lineno

    @property
    def file(self) -> str:
        return self.module.file


@dataclass
class ClassInfo:
    name: str
    qual: str
    module: "ModInfo"
    node: Any
    bases: list = field(default_factory=list)          # ast exprs
    methods: dict = field(default_factory=dict)        # name -> FuncInfo
    attrs: dict = field(default_factory=dict)          # class-level name -> (value ast, line, annotation ast)
    inner: dict = field(default_factory=dict)          # nested classes (Meta, Config) -> ast.ClassDef
    decorators: list = field(default_factory=list)
    self_attrs: dict = field(default_factory=dict)     # self.x = value  -> [value ast]
    outer: "ClassInfo | None" = None

    @property
    def id(self) -> str:
        return f"class:{self.qual}"

    @property
    def line(self) -> int:
        return self.node.lineno

    @property
    def file(self) -> str:
        return self.module.file


@dataclass
class ModInfo:
    name: str
    file: str
    tree: Any
    is_pkg: bool = False
    imports: dict = field(default_factory=dict)       # local name -> ("mod", dotted) | ("sym", module, name) | ("modprefix", dotted)
    star: list = field(default_factory=list)          # modules star-imported
    classes: dict = field(default_factory=dict)       # top-level name -> ClassInfo
    funcs: dict = field(default_factory=dict)         # top-level name -> FuncInfo
    vars: dict = field(default_factory=dict)          # module-level name -> [(value ast, line, annotation)]
    all_classes: list = field(default_factory=list)   # incl. nested
    import_nodes: list = field(default_factory=list)  # every Import / ImportFrom statement (ast.walk order)

    @property
    def id(self) -> str:
        return f"module:{self.name}"

    @property
    def package(self) -> str:
        return self.name if self.is_pkg else self.name.rpartition(".")[0]


# --------------------------------------------------------------------------- helpers

def dotted(e) -> str | None:
    """a.b.c for Name/Attribute chains, else None."""
    parts = []
    while isinstance(e, ast.Attribute):
        parts.append(e.attr)
        e = e.value
    if isinstance(e, ast.Name):
        parts.append(e.id)
        return ".".join(reversed(parts))
    return None


def const_str(e) -> str | None:
    if isinstance(e, ast.Constant) and isinstance(e.value, str):
        return e.value
    return None


def kwarg(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def walk_body(node) -> Iterator[ast.AST]:
    """ast.walk over a def's body including nested defs/lambdas (they collapse into the enclosing def),
    but not into nested classes (those are their own symbols)."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, ast.ClassDef):
            continue
        stack.extend(ast.iter_child_nodes(n))


def ann_base(e) -> Any:
    """Strip Optional[X] / X | None / Annotated[X, ...] / 'X' strings. Returns (expr, container) where
    container is 'list' for list[X]/List[X]/Sequence[X]/QuerySet[X], else None."""
    if isinstance(e, ast.Constant) and isinstance(e.value, str):
        try:
            e = ast.parse(e.value, mode="eval").body
        except SyntaxError:
            return None, None
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.BitOr):
        for side in (e.left, e.right):
            if not (isinstance(side, ast.Constant) and side.value is None):
                return ann_base(side)
    if isinstance(e, ast.Subscript):
        head = (dotted(e.value) or "").split(".")[-1]
        sl = e.slice
        if head in ("Optional", "Annotated", "Required", "NotRequired", "Final", "ClassVar", "Type"):
            inner = sl.elts[0] if isinstance(sl, ast.Tuple) else sl
            return ann_base(inner)
        if head == "Union" and isinstance(sl, ast.Tuple):
            non_none = [x for x in sl.elts if not (isinstance(x, ast.Constant) and x.value is None)]
            if len(non_none) == 1:
                return ann_base(non_none[0])
            return None, None
        if head in ("list", "List", "Sequence", "Iterable", "Iterator", "QuerySet", "set", "Set", "tuple", "Tuple",
                    "Generator", "AsyncIterator", "AsyncGenerator"):
            inner = sl.elts[0] if isinstance(sl, ast.Tuple) else sl
            b, _ = ann_base(inner)
            return b, "list"
        return None, None
    return e, None


def is_nullable_ann(e) -> bool:
    if isinstance(e, ast.Constant) and isinstance(e.value, str):
        try:
            e = ast.parse(e.value, mode="eval").body
        except SyntaxError:
            return False
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.BitOr):
        return any(isinstance(s, ast.Constant) and s.value is None for s in (e.left, e.right)) or \
            is_nullable_ann(e.left) or is_nullable_ann(e.right)
    if isinstance(e, ast.Subscript):
        head = (dotted(e.value) or "").split(".")[-1]
        if head == "Optional":
            return True
        if head == "Union":
            sl = e.slice
            return isinstance(sl, ast.Tuple) and any(isinstance(x, ast.Constant) and x.value is None for x in sl.elts)
        if head == "Annotated":
            sl = e.slice
            return is_nullable_ann(sl.elts[0] if isinstance(sl, ast.Tuple) else sl)
    return False


def ann_text(e) -> str | None:
    if e is None:
        return None
    try:
        return ast.unparse(e)
    except Exception:
        return None


# --------------------------------------------------------------------------- program

class PyProgram:
    def __init__(self, project: Project):
        self.project = project
        self.root = project.root
        self.modules: dict[str, ModInfo] = {}     # canonical name -> ModInfo
        self.alias: dict[str, ModInfo] = {}       # every importable name -> ModInfo
        self.by_file: dict[str, ModInfo] = {}
        self.classes: dict[str, ClassInfo] = {}   # qual -> ClassInfo
        self.funcs: dict[str, FuncInfo] = {}      # qual -> FuncInfo (functions + methods)
        self.method_index: dict[str, list[FuncInfo]] = {}
        self.parse_errors: list[dict] = []
        self.attr_rules: list[Callable] = []      # (prog, base_type, attr, ctx) -> type | None
        self.call_rules: list[Callable] = []      # (prog, call, func_type, ctx) -> type | None
        self._var_cache: dict[int, dict] = {}
        self._infer_depth = 0

    # ---- loading
    def source_roots(self) -> list[Path]:
        """Directories whose contents are importable by top-level name (after load())."""
        plan = getattr(self, "root_plan", None)
        if plan is None:
            return [self.root]
        return [self.root / r.path if r.path else self.root for r in plan.roots.values()]

    def configured_roots(self) -> tuple[list[str] | None, str]:
        """(roots, origin): `--python-root` beats `python.source_roots` in .cg.yaml; (None, "detected") otherwise."""
        opts = self.project.options
        if opts.get("python_roots"):
            return list(opts["python_roots"]), "flag"
        cfg = ((opts.get("config") or {}).get("python") or {}).get("source_roots")
        if cfg:
            return list(cfg), "configured"
        return None, "detected"

    def files(self) -> list[str]:
        out = []
        for dp, dns, fns in os.walk(self.root):
            dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith("."))
            for fn in sorted(fns):
                if fn.endswith(".py"):
                    p = os.path.join(dp, fn)
                    if keep_file(p):
                        out.append(os.path.relpath(p, self.root).replace(os.sep, "/"))
        return out

    def load(self, skip_migrations=True) -> dict:
        files = self.files()
        n_skipped = 0
        # per-file outcome for coverage (codegraph/coverage.py): which discovered files did not become graph nodes, and why
        self.file_report = rep = {"seen": files, "parse_failed": [], "skipped_oversize": [], "excluded": [], "unmapped": []}
        parsed: dict[str, tuple] = {}
        for rel in files:
            parts = Path(rel).parts
            if skip_migrations and "migrations" in parts[:-1]:
                n_skipped += 1
                rep["excluded"].append(rel)
                continue
            p = self.root / rel
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    n_skipped += 1
                    rep["skipped_oversize"].append(rel)
                    continue
                src = p.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src, filename=rel)
            except (SyntaxError, ValueError, RecursionError, OSError) as ex:
                self.parse_errors.append({"file": rel, "error": f"{type(ex).__name__}: {getattr(ex, 'msg', str(ex))}",
                                          "line": getattr(ex, "lineno", None)})
                rep["parse_failed"].append(rel)
                continue
            parsed[rel] = (tree, [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))])
        configured, origin = self.configured_roots()
        # every discovered file shapes the layout (a namespace package may only hold files that failed to parse)
        plan = RootPlan(self.root, files, import_names(v[1] for v in parsed.values()), configured, origin,
                        skip_dirs=SKIP_DIRS)
        self.root_plan = plan
        names, unmapped = plan.assign()
        owners: dict = {}
        for rel in unmapped:
            if rel in parsed:
                rep["unmapped"].append(rel)  # no importable module path (directory name not an identifier, outside the source roots, name taken)
        for rel, (tree, imps) in parsed.items():
            if rel not in names:
                continue
            name, is_pkg, _, root = names[rel]
            m = ModInfo(name=name, file=rel, tree=tree, is_pkg=is_pkg, import_nodes=imps)
            self.modules[name] = m
            self.by_file[rel] = m
            self.alias[name] = m
            if root is not None:
                owners[root.key] = owners.get(root.key, 0) + 1
        for rel, (name, _, aliases, _) in names.items():   # other importable names, after every canonical one
            m = self.by_file.get(rel)
            for nm in aliases if m else ():
                self.alias.setdefault(nm, m)
        self.roots_report = plan.report(owners)
        for m in self.modules.values():
            self._collect(m)
        return {"files": len(files), "parsed": len(self.modules), "skipped": n_skipped, "parse_errors": len(self.parse_errors)}

    def _collect(self, m: ModInfo) -> None:
        for st in m.tree.body:
            self._collect_stmt(m, st, top=True)
        top = {id(st) for st in m.tree.body}
        for st in m.import_nodes:
            if id(st) not in top:
                self._import(m, st, local_only=True)

    def _collect_stmt(self, m: ModInfo, st, top: bool):
        if isinstance(st, (ast.Import, ast.ImportFrom)):
            self._import(m, st)
        elif isinstance(st, ast.ClassDef):
            c = self._class(m, st, None)
            m.classes[st.name] = c
        elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            f = FuncInfo(st.name, f"{m.name}.{st.name}", m, st, decorators=st.decorator_list)
            m.funcs[st.name] = f
            self.funcs[f.qual] = f
        elif isinstance(st, ast.Assign):
            for t in st.targets:
                for nm in self._target_names(t):
                    m.vars.setdefault(nm, []).append((st.value, st.lineno, None))
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            m.vars.setdefault(st.target.id, []).append((st.value, st.lineno, st.annotation))
        elif isinstance(st, (ast.If, ast.Try, ast.With)) or (hasattr(ast, "TryStar") and isinstance(st, getattr(ast, "TryStar"))):
            for sub in ast.iter_child_nodes(st):
                if isinstance(sub, ast.stmt):
                    self._collect_stmt(m, sub, top)
                elif isinstance(sub, ast.ExceptHandler):
                    for s2 in sub.body:
                        self._collect_stmt(m, s2, top)

    @staticmethod
    def _target_names(t) -> list[str]:
        if isinstance(t, ast.Name):
            return [t.id]
        if isinstance(t, (ast.Tuple, ast.List)):
            return [n for e in t.elts for n in PyProgram._target_names(e)]
        return []

    def _class(self, m: ModInfo, node: ast.ClassDef, outer: ClassInfo | None) -> ClassInfo:
        qual = f"{outer.qual}.{node.name}" if outer else f"{m.name}.{node.name}"
        c = ClassInfo(node.name, qual, m, node, bases=list(node.bases), decorators=node.decorator_list, outer=outer)
        self.classes[qual] = c
        m.all_classes.append(c)
        for st in node.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                f = FuncInfo(st.name, f"{qual}.{st.name}", m, st, cls=c, kind="method", decorators=st.decorator_list)
                c.methods[st.name] = f
                self.funcs[f.qual] = f
                self.method_index.setdefault(st.name, []).append(f)
                for sub in walk_body(st):
                    if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                        tg = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                        for t in tg:
                            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                                c.self_attrs.setdefault(t.attr, []).append(
                                    (sub.value, getattr(sub, "annotation", None), f))
            elif isinstance(st, ast.ClassDef):
                if st.name in ("Meta", "Config", "Media", "Params", "Input", "Output"):
                    c.inner[st.name] = st
                ic = self._class(m, st, c)
                c.attrs.setdefault(st.name, (None, st.lineno, None))
                c.inner.setdefault(st.name, st)
                c.inner_infos = getattr(c, "inner_infos", {})
                c.inner_infos[st.name] = ic
            elif isinstance(st, ast.Assign):
                for t in st.targets:
                    for nm in self._target_names(t):
                        c.attrs[nm] = (st.value, st.lineno, None)
            elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                c.attrs[st.target.id] = (st.value, st.lineno, st.annotation)
        return c

    def _import(self, m: ModInfo, st, local_only=False):
        target = m.imports
        if isinstance(st, ast.Import):
            for a in st.names:
                if a.asname:
                    target.setdefault(a.asname, ("mod", a.name)) if local_only else target.__setitem__(a.asname, ("mod", a.name))
                else:
                    head = a.name.split(".")[0]
                    if not local_only or head not in target:
                        target[head] = ("modprefix", head)
        else:
            base = self._abs_from(m, st.module, st.level)
            if base is None:
                return
            for a in st.names:
                if a.name == "*":
                    if base not in m.star:
                        m.star.append(base)
                    continue
                nm = a.asname or a.name
                if local_only and nm in target:
                    continue
                target[nm] = ("sym", base, a.name)

    def _abs_from(self, m: ModInfo, mod: str | None, level: int) -> str | None:
        if not level:
            return mod
        pkg = m.package.split(".") if m.package else []
        if level > 1:
            pkg = pkg[:len(pkg) - (level - 1)] if level - 1 <= len(pkg) else []
        base = ".".join(pkg)
        if mod:
            base = f"{base}.{mod}" if base else mod
        return base

    # ---- symbol resolution
    def module(self, name: str) -> ModInfo | None:
        return self.alias.get(name)

    def lookup(self, modname: str, name: str, depth=0):
        """Resolve `name` in module `modname`. Returns a type tuple: ("type", ClassInfo) | ("func", FuncInfo) |
        ("mod", ModInfo) | ("var", ModInfo, name) | ("ext", dotted) | None."""
        if depth > 8:
            return None
        m = self.module(modname)
        if m is None:
            sub = self.module(f"{modname}.{name}")
            if sub:
                return ("mod", sub)
            return ("ext", f"{modname}.{name}")
        if name in m.classes:
            return ("type", m.classes[name])
        if name in m.funcs:
            return ("func", m.funcs[name])
        if name in m.imports:
            return self._resolve_import(m.imports[name], depth + 1)
        if name in m.vars:
            return ("var", m, name)
        sub = self.module(f"{m.name}.{name}")
        if sub:
            return ("mod", sub)
        for s in m.star:
            r = self.lookup(s, name, depth + 1)
            if r and not (r[0] == "ext" and self.module(s) is not None):
                return r
        return None

    def _resolve_import(self, imp, depth=0):
        if imp[0] == "mod":
            m = self.module(imp[1])
            return ("mod", m) if m else ("ext", imp[1])
        if imp[0] == "modprefix":
            m = self.module(imp[1])
            return ("mod", m) if m else ("ext", imp[1])
        _, base, name = imp
        sub = self.module(f"{base}.{name}")
        if sub and (self.module(base) is None or name not in self.module(base).classes and name not in self.module(base).funcs):
            return ("mod", sub)
        return self.lookup(base, name, depth)

    def resolve_name(self, m: ModInfo, name: str):
        if name in m.classes:
            return ("type", m.classes[name])
        if name in m.funcs:
            return ("func", m.funcs[name])
        if name in m.imports:
            return self._resolve_import(m.imports[name])
        if name in m.vars:
            return ("var", m, name)
        for s in m.star:
            r = self.lookup(s, name)
            if r:
                return r
        if name in BUILTINS:
            return ("ext", f"builtins.{name}")
        return None

    def member(self, t, attr: str, ctx=None):
        """Type of `<t>.attr`."""
        if t is None:
            return None
        for rule in self.attr_rules:
            r = rule(self, t, attr, ctx)
            if r is not None:
                return r
        k = t[0]
        if k == "mod":
            return self.lookup(t[1].name, attr)
        if k == "ext":
            sub = self.module(f"{t[1]}.{attr}")
            return ("mod", sub) if sub else ("ext", f"{t[1]}.{attr}")
        if k == "var":
            return self.member(self.var_type(t[1], t[2]), attr, ctx)
        if k in ("type", "inst"):
            c = t[1]
            f = self.find_method(c, attr)
            if f:
                return ("bound", f, t)
            v = self.class_attr(c, attr)
            if v is not None:
                return v
            if k == "inst":
                sa = self.self_attr_type(c, attr)
                if sa is not None:
                    return sa
        return None

    def class_attr(self, c: ClassInfo, attr: str, depth=0):
        if depth > 10:
            return None
        if attr in c.attrs:
            val, _, ann = c.attrs[attr]
            if hasattr(c, "inner_infos") and attr in c.inner_infos:
                return ("type", c.inner_infos[attr])
            if ann is not None:
                t = self.ann_type(c.module, ann)
                if t:
                    return t
            if val is not None:
                return self.infer(val, Ctx(c.module, None, c))
            return None
        for b in self.bases(c):
            if b[0] == "type":
                r = self.class_attr(b[1], attr, depth + 1)
                if r is not None:
                    return r
        return None

    def self_attr_type(self, c: ClassInfo, attr: str, depth=0):
        if depth > 10:
            return None
        for val, ann, f in c.self_attrs.get(attr, []):
            if ann is not None:
                t = self.ann_type(c.module, ann)
                if t:
                    return t
            if val is not None:
                t = self.infer(val, Ctx(c.module, f, c))
                if t:
                    return t
        for b in self.bases(c):
            if b[0] == "type":
                r = self.self_attr_type(b[1], attr, depth + 1)
                if r is not None:
                    return r
        return None

    def bases(self, c: ClassInfo) -> list:
        cached = getattr(c, "_bases", None)
        if cached is not None:
            return cached
        c._bases = []
        out = []
        for b in c.bases:
            if isinstance(b, ast.Subscript):  # Generic[...] / Model[T]
                b = b.value
            t = self.infer(b, Ctx(c.module, None, c.outer))
            if t and t[0] == "var":
                t = self.var_type(t[1], t[2])
            if t:
                out.append(t)
        c._bases = out
        return out

    def mro(self, c: ClassInfo, depth=0) -> list:
        """Local classes and external dotted names in (approximate) MRO order."""
        out, seen = [], set()

        def rec(x, d):
            if d > 15:
                return
            for b in self.bases(x):
                key = b[1].qual if b[0] == "type" else str(b[1])
                if key in seen:
                    continue
                seen.add(key)
                out.append(b)
                if b[0] == "type":
                    rec(b[1], d + 1)
        rec(c, 0)
        return out

    def ext_bases(self, c: ClassInfo) -> set[str]:
        return {b[1] for b in self.mro(c) if b[0] == "ext"}

    def lineage(self, c: ClassInfo) -> list[str]:
        """Dotted names of the class's ancestors: external bases plus local ancestor quals (so indexing a
        framework's own source, where `ninja.Router` is a local class, still recognises its subclasses)."""
        cached = getattr(c, "_lineage", None)
        if cached is None:
            cached = [b[1] if b[0] == "ext" else b[1].qual for b in self.mro(c) if b[0] in ("ext", "type")]
            c._lineage = cached
        return cached

    def subclass_of(self, c: ClassInfo, *names: str, include_self: bool = False) -> bool:
        """True when an ancestor's dotted name equals / ends with one of `names` (e.g. 'models.Model', 'Schema')."""
        cands = self.lineage(c) + ([c.qual] if include_self else [])
        for b in cands:
            for n in names:
                if b == n or b.endswith("." + n):
                    return True
        return False

    def find_method(self, c: ClassInfo, name: str, depth=0) -> FuncInfo | None:
        if name in c.methods:
            return c.methods[name]
        if depth > 15:
            return None
        for b in self.bases(c):
            if b[0] == "type":
                r = self.find_method(b[1], name, depth + 1)
                if r:
                    return r
        return None

    def var_type(self, m: ModInfo, name: str):
        key = (id(m), name)
        if key in self._var_cache:
            return self._var_cache[key]
        self._var_cache[key] = None
        t = None
        for val, _, ann in m.vars.get(name, []):
            if ann is not None:
                t = self.ann_type(m, ann)
            if t is None and val is not None:
                t = self.infer(val, Ctx(m, None, None))
            if t:
                break
        self._var_cache[key] = t
        return t

    def var_value(self, m: ModInfo, name: str):
        vs = m.vars.get(name) or []
        return vs[-1][0] if vs else None

    def ann_type(self, m: ModInfo, ann):
        e, cont = ann_base(ann)
        if e is None:
            return None
        t = self.infer(e, Ctx(m, None, None))
        if t and t[0] == "type":
            t = ("inst", t[1])
        elif t and t[0] == "ext":
            t = ("einst", t[1])
        else:
            return None
        return ("list", t) if cont == "list" else t

    # ---- inference
    def local_vars(self, ctx: "Ctx") -> dict:
        """name -> list of (value ast | None, annotation ast | None, kind) for the current def."""
        f = ctx.func
        if f is None:
            return {}
        key = id(f.node)
        if key in self._var_cache:
            return self._var_cache[key]
        lv: dict[str, list] = {}
        args = f.node.args
        allargs = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
        for i, a in enumerate(allargs):
            lv.setdefault(a.arg, []).append((None, a.annotation, "param"))
        for sub in walk_body(f.node):
            if isinstance(sub, ast.Assign):
                for t in sub.targets:
                    if isinstance(t, ast.Name):
                        lv.setdefault(t.id, []).append((sub.value, None, "assign"))
                    elif isinstance(t, ast.Tuple) and isinstance(sub.value, ast.Call):
                        for i, el in enumerate(t.elts):
                            if isinstance(el, ast.Name):
                                lv.setdefault(el.id, []).append((sub.value, None, f"unpack{i}"))
            elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                lv.setdefault(sub.target.id, []).append((sub.value, sub.annotation, "assign"))
            elif isinstance(sub, (ast.For, ast.AsyncFor, ast.comprehension)) and isinstance(sub.target, ast.Name):
                lv.setdefault(sub.target.id, []).append((sub.iter, None, "iter"))
            elif isinstance(sub, (ast.With, ast.AsyncWith)):
                for it in sub.items:
                    if isinstance(it.optional_vars, ast.Name):
                        lv.setdefault(it.optional_vars.id, []).append((it.context_expr, None, "with"))
            elif isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name):
                lv.setdefault(sub.target.id, []).append((sub.value, None, "assign"))
        self._var_cache[key] = lv
        return lv

    def infer(self, e, ctx: "Ctx"):
        if e is None or self._infer_depth > 40:
            return None
        self._infer_depth += 1
        try:
            return self._infer(e, ctx)
        finally:
            self._infer_depth -= 1

    def _infer(self, e, ctx: "Ctx"):
        if isinstance(e, ast.Name):
            n = e.id
            if ctx.func is not None:
                if n == "self" and ctx.cls is not None:
                    return ("inst", ctx.cls)
                if n == "cls" and ctx.cls is not None:
                    return ("type", ctx.cls)
                lv = self.local_vars(ctx)
                if n in lv:
                    key = (id(ctx.func.node), n)
                    if key in ctx.visiting:
                        return None
                    ctx.visiting.add(key)
                    try:
                        for val, ann, kind in lv[n]:
                            t = None
                            if ann is not None:
                                t = self.ann_type(ctx.mod, ann)
                            if t is None and val is not None:
                                t = self.infer(val, ctx)
                                if t is not None and kind == "iter":
                                    t = t[1] if t[0] == "list" else self.iter_type(t)
                                elif t is not None and kind.startswith("unpack"):
                                    t = self.unpack_type(t, int(kind[6:]))
                            if t is not None:
                                return t
                    finally:
                        ctx.visiting.discard(key)
                    return None
            if ctx.cls is not None and ctx.func is None and n in ctx.cls.attrs:
                return self.class_attr(ctx.cls, n)
            r = self.resolve_name(ctx.mod, n)
            if r and r[0] == "var":
                vt = self.var_type(r[1], r[2])
                return vt if vt is not None else r
            return r
        if isinstance(e, ast.Attribute):
            base = self.infer(e.value, ctx)
            return self.member(base, e.attr, ctx)
        if isinstance(e, ast.Call):
            ft = self.infer(e.func, ctx)
            for rule in self.call_rules:
                r = rule(self, e, ft, ctx)
                if r is not None:
                    return r
            if ft is None:
                if isinstance(e.func, ast.Name) and e.func.id == "super" and ctx.cls is not None:
                    return ("super", ctx.cls)
                return None
            k = ft[0]
            if k == "type":
                return ("inst", ft[1])
            if k == "ext":
                return ("einst", ft[1])
            if k in ("func", "bound"):
                f = ft[1]
                ann = f.node.returns
                if ann is not None:
                    t = self.ann_type(f.module, ann)
                    if t:
                        return t
                if ft[0] == "bound" and isinstance(f.node, ast.FunctionDef) and any(
                        dotted(d) == "classmethod" for d in f.decorators):
                    return ("inst", ft[2][1]) if f.name in ("create", "build", "from_dict", "new") else None
                return None
            return None
        if isinstance(e, ast.Await):
            return self.infer(e.value, ctx)
        if isinstance(e, (ast.List, ast.ListComp)) and isinstance(e, ast.ListComp):
            t = self.infer(e.elt, ctx)
            return ("list", t) if t else None
        if isinstance(e, ast.Subscript):
            t = self.infer(e.value, ctx)
            if t and t[0] == "list":
                return t[1]
            if t and t[0] == "qs":
                return ("inst", t[1]) if not isinstance(e.slice, ast.Slice) else t
            return None
        if isinstance(e, ast.IfExp):
            return self.infer(e.body, ctx) or self.infer(e.orelse, ctx)
        if isinstance(e, ast.BoolOp):
            for v in e.values:
                t = self.infer(v, ctx)
                if t:
                    return t
        return None

    def iter_type(self, t):
        if t and t[0] == "qs":
            return ("inst", t[1])
        return None

    def unpack_type(self, t, i):
        if t and t[0] == "tuple":
            return t[1][i] if i < len(t[1]) else None
        return None

    # ---- call resolution
    def resolve_call(self, call: ast.Call, ctx: "Ctx"):
        """-> list of (target FuncInfo|ClassInfo, confidence, via)"""
        fn = call.func
        out = []
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Call) and isinstance(fn.value.func, ast.Name) \
                and fn.value.func.id == "super" and ctx.cls is not None:
            for b in self.bases(ctx.cls):
                if b[0] == "type":
                    f = self.find_method(b[1], fn.attr)
                    if f:
                        return [(f, EXACT, "super")]
            return []
        # sync_to_async(f)(...), database_sync_to_async(f)(...), async_to_sync(f)(...): calls f
        if isinstance(fn, ast.Call) and fn.args and (
                (isinstance(fn.func, ast.Name) and fn.func.id in ASYNC_WRAPPERS) or
                (isinstance(fn.func, ast.Attribute) and fn.func.attr in ASYNC_WRAPPERS)) \
                and not isinstance(fn.args[0], ast.Lambda):
            inner = ast.Call(func=fn.args[0], args=call.args, keywords=call.keywords)
            ast.copy_location(inner, call)
            return [(tgt, conf, via or "async_wrapper") for tgt, conf, via in self.resolve_call(inner, ctx)]
        t = self.infer(fn, ctx)
        if t is not None:
            if t[0] == "func":
                return [(t[1], EXACT, None)]
            if t[0] == "bound":
                recv = t[2]
                conf = EXACT if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and \
                    fn.value.id in ("self", "cls") else RESOLVED
                if isinstance(fn, ast.Attribute):
                    vt = self.infer(fn.value, ctx)
                    if vt and vt[0] == "type" and isinstance(fn.value, (ast.Name, ast.Attribute)):
                        conf = EXACT
                out.append((t[1], conf, None))
                # dispatch to overrides in subclasses (self.m() in a base class)
                if recv[0] == "inst" and isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "self":
                    for sub in self.subclasses(recv[1]):
                        if fn.attr in sub.methods:
                            out.append((sub.methods[fn.attr], RESOLVED, "override"))
                return out
            if t[0] == "type":
                return [(t[1], EXACT, "instantiate")]
            return []
        if isinstance(fn, ast.Attribute):
            cands = self.method_index.get(fn.attr) or []
            if len(cands) == 1 and fn.attr not in STOP_METHODS and not fn.attr.startswith("__"):
                vt = self.infer(fn.value, ctx)
                if vt is None:
                    return [(cands[0], HEURISTIC, "unique-method-name")]
        return []

    def subclasses(self, c: ClassInfo) -> list[ClassInfo]:
        if not hasattr(self, "_subs"):
            self._subs = {}
            for x in self.classes.values():
                for b in self.mro(x):
                    if b[0] == "type":
                        self._subs.setdefault(b[1].qual, []).append(x)
        return self._subs.get(c.qual, [])

    def all_defs(self) -> Iterator[FuncInfo]:
        yield from self.funcs.values()


@dataclass
class Ctx:
    mod: ModInfo
    func: FuncInfo | None
    cls: ClassInfo | None
    visiting: set = field(default_factory=set)


BUILTINS = {"len", "str", "int", "float", "dict", "list", "set", "tuple", "print", "isinstance", "getattr", "setattr",
            "hasattr", "super", "open", "range", "enumerate", "zip", "map", "filter", "sorted", "min", "max", "sum",
            "any", "all", "bool", "type", "repr", "iter", "next", "round", "abs", "format", "id", "vars", "callable",
            "Exception", "ValueError", "KeyError", "TypeError", "RuntimeError", "NotImplementedError", "object",
            "property", "staticmethod", "classmethod", "bytes", "frozenset", "reversed", "divmod", "hash", "input"}

ENV_FUNCS = {"os.getenv", "os.environ.get", "os.environ.setdefault", "os.environ.pop", "decouple.config",
             "environ.Env", "os.environ.__getitem__"}


def module_of(path: str | None) -> str | None:
    if not path:
        return None
    return "/".join(path.split("/")[:-1]) or None


class PythonPlugin(LanguagePlugin):
    name = "python"

    def __init__(self):
        self.program: PyProgram | None = None

    def detect(self, project: Project) -> bool:
        """A project marker at the root, or a .py file anywhere outside dependency / build / cache directories."""
        if any(project.exists(m) for m in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "manage.py", "Pipfile")):
            return True
        for dp, dns, fns in os.walk(project.root):
            if any(fn.endswith(".py") for fn in fns):
                return True
            dns[:] = [d for d in dns if d not in DETECT_SKIP and not d.startswith(".")]
        return False

    def index(self, project: Project, builder: GraphBuilder, frameworks: list[FrameworkPlugin]) -> dict:
        t0 = time.time()
        prog = PyProgram(project)
        self.program = prog
        st = prog.load(skip_migrations=not project.options.get("python_include_migrations"))
        self.file_report = prog.file_report
        for fw in frameworks:
            fw.register_hooks(prog)
        b = builder
        for m in prog.modules.values():
            b.add_node("module", m.name, name=m.name, fqn=m.name, file=m.file, line=1, module=module_of(m.file), lang="python")
        for c in prog.classes.values():
            b.add_node("class", c.qual, name=c.name, fqn=c.qual, file=c.file, line=c.line,
                       end_line=getattr(c.node, "end_lineno", None), module=module_of(c.file), lang="python",
                       doc=ast.get_docstring(c.node))
            owner = c.outer.id if c.outer else c.module.id
            b.add_edge(owner, c.id, "CONTAINS", c.file, c.line, EXACT)
        for f in prog.funcs.values():
            b.add_node(f.kind, f.qual, name=f.name, fqn=f.qual, file=f.file, line=f.line,
                       end_line=getattr(f.node, "end_lineno", None), module=module_of(f.file), lang="python",
                       doc=ast.get_docstring(f.node),
                       attrs={"decorators": [ann_text(d) for d in f.decorators]} if f.decorators else None)
            b.add_edge(f.cls.id if f.cls else f.module.id, f.id, "CONTAINS", f.file, f.line, EXACT)
        # inheritance
        n_ext = 0
        for c in prog.classes.values():
            for bt in prog.bases(c):
                if bt[0] == "type":
                    b.add_edge(c.id, bt[1].id, "EXTENDS", c.file, c.line, EXACT)
                    n_ext += 1
                elif bt[0] == "ext":
                    b.nodes[c.id].attrs.setdefault("ext_bases", []).append(bt[1])
            # overrides: base method -> child override (dispatch)
            for name, f in c.methods.items():
                for bt in prog.mro(c):
                    if bt[0] == "type" and name in bt[1].methods and not name.startswith("__"):
                        b.add_edge(bt[1].methods[name].id, f.id, "OVERRIDDEN_BY", f.file, f.line, RESOLVED)
                        break
        # imports
        n_imp = 0
        for m in prog.modules.values():
            for st_ in m.import_nodes:
                if isinstance(st_, ast.Import):
                    for a in st_.names:
                        tm = prog.module(a.name)
                        if tm:
                            b.add_edge(m.id, tm.id, "IMPORTS", m.file, st_.lineno, EXACT); n_imp += 1
                elif isinstance(st_, ast.ImportFrom):
                    base = prog._abs_from(m, st_.module, st_.level)
                    tm = prog.module(base) if base else None
                    for a in st_.names:
                        sub = prog.module(f"{base}.{a.name}") if base else None
                        if sub:
                            b.add_edge(m.id, sub.id, "IMPORTS", m.file, st_.lineno, EXACT); n_imp += 1
                        elif tm:
                            b.add_edge(m.id, tm.id, "IMPORTS", m.file, st_.lineno, EXACT); n_imp += 1
        # calls + env reads
        conf_ct = {EXACT: 0, RESOLVED: 0, HEURISTIC: 0}
        n_env = 0
        for f in list(prog.funcs.values()):
            ctx = Ctx(f.module, f, f.cls)
            for sub in walk_body(f.node):
                if isinstance(sub, ast.Call):
                    for tgt, conf, via in prog.resolve_call(sub, ctx):
                        if isinstance(tgt, ClassInfo):
                            b.add_edge(f.id, tgt.id, "INSTANTIATES", f.file, sub.lineno, conf)
                            init = prog.find_method(tgt, "__init__")
                            if init:
                                b.add_edge(f.id, init.id, "CALLS", f.file, sub.lineno, conf, via="constructor")
                        else:
                            b.add_edge(f.id, tgt.id, "CALLS", f.file, sub.lineno, conf, **({"via": via} if via else {}))
                        conf_ct[conf] += 1
            n_env += self.env_reads(prog, b, f.id, f.node, ctx)
        for m in prog.modules.values():
            n_env += self.env_reads(prog, b, m.id, m.tree, Ctx(m, None, None), top_only=True)
        st.update({"classes": len(prog.classes), "functions": sum(1 for f in prog.funcs.values() if f.kind == "function"),
                   "methods": sum(1 for f in prog.funcs.values() if f.kind == "method"), "imports": n_imp,
                   "extends": n_ext, "calls": conf_ct, "env_reads": n_env,
                   "parse_error_files": [e["file"] for e in prog.parse_errors[:20]],
                   "roots_mode": prog.root_plan.mode, "source_roots": prog.roots_report,
                   **({"roots_warnings": prog.root_plan.warnings} if prog.root_plan.warnings else {}),
                   **({"roots_ambiguous": {"count": len(prog.root_plan.ambiguous), "samples": prog.root_plan.ambiguous[:5]}}
                      if prog.root_plan.ambiguous else {}),
                   **({"module_name_collisions": {"count": prog.root_plan.n_collisions,
                                                  "path_named": prog.root_plan.n_requalified,
                                                  "samples": prog.root_plan.collisions[:5]}}
                      if prog.root_plan.n_collisions else {}),
                   "seconds": round(time.time() - t0, 2)})
        return st

    @staticmethod
    def env_key(prog: PyProgram, call: ast.Call, ctx: Ctx) -> tuple[str, str] | None:
        """(key, via) when `call` reads an environment variable."""
        fn = call.func
        d = dotted(fn)
        if not call.args:
            return None
        key = const_str(call.args[0])
        if key is None:
            return None
        if isinstance(fn, ast.Attribute):
            base = prog.infer(fn.value, ctx)
            bname = base[1] if base and base[0] in ("ext", "einst") else None
            if bname in ("os.environ",) and fn.attr in ("get", "setdefault", "pop"):
                return key, f"os.environ.{fn.attr}"
            if bname in ("environ.Env",) and base[0] == "einst":
                return key, f"env.{fn.attr}"
            if base and base[0] == "einst" and bname and bname.endswith("environ.Env"):
                return key, f"env.{fn.attr}"
            if d and d.endswith("environ.get") and d.split(".")[0] == "os":
                return key, "os.environ.get"
        t = prog.infer(fn, ctx)
        if t and t[0] == "ext":
            if t[1] in ("os.getenv", "os.environ.get", "decouple.config", "os.environ.setdefault"):
                return key, t[1]
        if t and t[0] == "einst" and t[1].endswith("environ.Env"):
            return key, "env()"
        return None

    def env_reads(self, prog, b, src, node, ctx, top_only=False) -> int:
        n = 0
        it = node.body if top_only else None
        nodes = []
        if top_only:
            for st in it:
                if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                nodes.extend(ast.walk(st))
        else:
            nodes = walk_body(node)
        for sub in nodes:
            if isinstance(sub, ast.Call):
                r = self.env_key(prog, sub, ctx)
                if r:
                    b.add_edge(src, b.add_node("env", r[0], lang="env"), "READS_ENV", ctx.mod.file, sub.lineno, EXACT, via=r[1])
                    n += 1
            elif isinstance(sub, ast.Subscript) and isinstance(sub.ctx, ast.Load):
                k = const_str(sub.slice)
                if k:
                    t = prog.infer(sub.value, ctx)
                    if t and t[0] == "ext" and t[1] == "os.environ":
                        b.add_edge(src, b.add_node("env", k, lang="env"), "READS_ENV", ctx.mod.file, sub.lineno, EXACT, via="os.environ[]")
                        n += 1
        return n

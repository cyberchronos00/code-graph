"""Dart program model built from the analyzer-based extractor's facts (bin/extract.dart).

Unresolved (syntactic) ASTs only, so names are resolved here:
  * packages: every pubspec.yaml under the root (monorepos, example/ apps); `package:<name>/x.dart`
    maps to `<pkg dir>/lib/x.dart`, other packages are external;
  * libraries: a file plus its `part` files share one scope; exports (show/hide) are followed;
  * lexical lookup: locals/params -> class members (incl. inherited) -> library -> imports/prefixes;
  * flow-insensitive type inference from declarations, initialisers, constructor calls,
    return types (Future<T> unwrapped by await), `context.read<T>()`, `BlocProvider.of<T>()`,
    `GetIt.I<T>()`/`getIt<T>()` and fields initialised from constructor parameters.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ...core.model import EXACT, HEURISTIC, RESOLVED


# supertypes of well-known external classes (the analysed project's dependencies are not parsed)
EXTERNAL_SUPERS = {
    "BaseClient": {"Client"}, "IOClient": {"BaseClient", "Client"}, "RetryClient": {"BaseClient", "Client"},
    "BrowserClient": {"BaseClient", "Client"}, "MockClient": {"BaseClient", "Client"},
    "DioForNative": {"Dio", "DioMixin"}, "DioForBrowser": {"Dio", "DioMixin"}, "DioMixin": {"Dio"},
    "Bloc": {"BlocBase"}, "Cubit": {"BlocBase"}, "HydratedBloc": {"Bloc", "BlocBase"}, "HydratedCubit": {"Cubit", "BlocBase"},
    "StatelessWidget": {"Widget"}, "StatefulWidget": {"Widget"}, "ConsumerWidget": {"StatelessWidget", "Widget"},
    "ConsumerStatefulWidget": {"StatefulWidget", "Widget"}, "HookWidget": {"StatelessWidget", "Widget"},
}


@dataclass
class DLib:
    file: str
    pkg: "Pkg | None"
    parts: list[str] = field(default_factory=list)
    decls: dict[str, Any] = field(default_factory=dict)  # top-level name -> DClass | DFunc | DVar
    imports: list[dict] = field(default_factory=list)    # {"lib": DLib|None, "uri", "prefix", "show", "hide", "ext": bool}
    exports: list[dict] = field(default_factory=list)
    _ns: dict | None = None

    @property
    def id(self) -> str:
        return f"module:{self.file}"


@dataclass
class Pkg:
    name: str
    dir: str  # relative to project root ('' for root)


@dataclass
class DClass:
    name: str
    file: str
    lib: DLib
    kind: str
    raw: dict
    methods: dict[str, "DFunc"] = field(default_factory=dict)
    ctors: dict[str, "DFunc"] = field(default_factory=dict)
    fields: dict[str, "DVar"] = field(default_factory=dict)
    supers: list[tuple[str, str]] = field(default_factory=list)  # (relation, type text)

    @property
    def id(self) -> str:
        return f"class:{self.file}#{self.name}"

    @property
    def line(self) -> int:
        return self.raw.get("line", 1)

    @property
    def ann(self) -> list[str]:
        return [a["name"] for a in self.raw.get("ann") or []]


@dataclass
class DFunc:
    name: str
    file: str
    lib: DLib
    cls: DClass | None
    kind: str  # function | method | getter | setter | ctor | handler | operator
    raw: dict

    @property
    def id(self) -> str:
        if self.cls is None:
            return f"function:{self.file}#{self.name}"
        return f"method:{self.file}#{self.cls.name}.{self.name}"

    @property
    def qual(self) -> str:
        return self.id.split(":", 1)[1]

    @property
    def line(self) -> int:
        return self.raw.get("line", 1)

    @property
    def facts(self) -> list[dict]:
        return self.raw.get("facts") or []

    @property
    def params(self) -> list[dict]:
        return self.raw.get("params") or []


@dataclass
class DVar:
    name: str
    file: str
    lib: DLib
    cls: DClass | None
    type: str | None
    init: dict | None
    line: int
    static: bool = False
    const: bool = False
    final: bool = False
    ann: list = field(default_factory=list)
    facts: list = field(default_factory=list)


def split_type(t: str | None) -> tuple[str, list[str], bool]:
    """'Future<List<Foo>>?' -> ('Future', ['List<Foo>'], True)."""
    if not t:
        return "", [], False
    t = t.strip()
    nullable = t.endswith("?")
    if nullable:
        t = t[:-1]
    if "<" not in t:
        return t, [], nullable
    i = t.index("<")
    base, inner = t[:i], t[i + 1: t.rindex(">")] if ">" in t else ""
    args, depth, cur = [], 0, ""
    for ch in inner:
        if ch == "<" or ch == "(":
            depth += 1
        elif ch == ">" or ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return base, args, nullable


def walk_repr(r) -> Iterator[dict]:
    """All nested expression reprs (calls/new/closures) of an expression repr."""
    if isinstance(r, dict):
        yield r
        for k, v in r.items():
            if k in ("l",):
                continue
            if isinstance(v, (dict, list)):
                yield from walk_repr(v)
    elif isinstance(r, list):
        for x in r:
            yield from walk_repr(x)


def root_name(r) -> str | None:
    while isinstance(r, dict):
        k = r.get("k")
        if k == "id":
            return r["v"]
        if k in ("prop", "idx", "call"):
            r = r.get("t")
        elif k in ("nn", "as", "await"):
            r = r.get("e")
        else:
            return None
    return None


def last_name(r) -> str | None:
    """Name a value would be called in a URL placeholder: $id -> id, ${p.id} -> id, ${m()} -> m."""
    if not isinstance(r, dict):
        return None
    k = r.get("k")
    if k == "id":
        return r["v"]
    if k == "prop":
        return r["n"]
    if k == "call":
        if r.get("n") in ("toString", "toLowerCase", "toUpperCase", "trim") and r.get("t"):
            return last_name(r["t"])
        return r.get("n") or last_name(r.get("t"))
    if k == "idx":
        i = r.get("i") or {}
        return i.get("v") if i.get("k") == "str" else last_name(r.get("t"))
    if k in ("nn", "as", "await"):
        return last_name(r.get("e"))
    return None


def ctor_type(r) -> str | None:
    """Type name of an instance creation. Without resolution `Foo(..)` parses as a call, so a capitalised
    unqualified callee (or `prefix.Foo(..)`) is treated as a constructor call."""
    if not isinstance(r, dict):
        return None
    if r.get("k") == "new":
        return r.get("type")
    if r.get("k") == "call" and r.get("n") and r["n"][:1].isupper():
        t = r.get("t")
        if t is None:
            return r["n"]
        if t.get("k") == "id" and t["v"][:1].islower():
            return f"{t['v']}.{r['n']}"
    return None


STOP_METHODS = {"get", "set", "add", "remove", "map", "where", "toList", "toString", "toJson", "fromJson", "copyWith", "build",
                "dispose", "init", "initState", "call", "then", "listen", "close", "clear", "update", "load", "fetch", "save",
                "delete", "create", "put", "post", "send", "read", "write", "open", "start", "stop", "reset", "refresh", "of",
                "contains", "forEach", "first", "last", "length", "isEmpty", "isNotEmpty", "addAll", "insert", "emit", "on",
                "setState", "notifyListeners", "toMap", "fromMap", "parse", "format", "value", "submit", "cancel", "run", "execute",
                "handle", "process", "validate", "dispatch", "show", "hide", "push", "pop", "go", "of", "watch", "select", "when",
                "maybeWhen", "props", "hashCode", "noSuchMethod", "runtimeType", "toggle", "play", "pause", "seek", "error"}

PROVIDER_LOOKUPS = {"read", "watch", "select", "get", "of", "call", "getIt", "locator", "sl", "inject", "find", "put", "lazyPut",
                    "Get.find", "instance", "I", "getAsync", "readOrNull"}
LIST_TYPES = {"List", "Iterable", "Set", "Queue"}
FUTURE_TYPES = {"Future", "FutureOr"}


class Ctx:
    def __init__(self, prog: "DartProgram", lib: DLib, fn: DFunc | None, cls: DClass | None, bindings: dict | None = None):
        self.prog, self.lib, self.fn, self.cls = prog, lib, fn, cls
        self.bindings = bindings or {}
        self._locals: dict | None = None

    @property
    def locals(self) -> dict:
        """name -> ('param', pdict) | ('var', fact)"""
        if self._locals is None:
            loc = {}
            if self.fn:
                for p in self.fn.params:
                    loc[p["name"]] = ("param", p)
                for f in self.fn.facts:
                    if f["ft"] == "var" and f["name"] not in loc:
                        loc[f["name"]] = ("var", f)
                    elif f["ft"] == "var":
                        loc.setdefault(f["name"] + "#2", ("var", f))
            self._locals = loc
        return self._locals


class DartProgram:
    def __init__(self, root: Path, facts: dict):
        self.root = Path(root)
        self.facts = facts
        self.files: dict[str, dict] = {f["file"]: f for f in facts.get("files") or []}
        self.pkgs: dict[str, Pkg] = {}
        self.pkg_dirs: list[Pkg] = []
        self.libs: dict[str, DLib] = {}
        self.part_owner: dict[str, str] = {}
        self.classes: dict[str, DClass] = {}
        self.funcs: dict[str, DFunc] = {}
        self.vars: list[DVar] = []
        self.by_name: dict[str, list[DClass]] = {}
        self.methods_by_name: dict[str, list[DFunc]] = {}
        self.callers: dict[str, list[tuple[Ctx, dict]]] = {}
        self.subs: dict[str, list[DClass]] = {}
        self.type_rules: list = []  # framework hooks: f(prog, repr, ctx) -> type | None
        self._infer_stack: set = set()

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        self._packages()
        for f in self.files.values():
            self._norm(f)
        for rel, f in self.files.items():
            for p in f.get("parts") or []:
                tgt = self._rel_uri(rel, p.get("uri") if isinstance(p, dict) else p)
                if tgt:
                    self.part_owner[tgt] = rel
        lib_names = {f["library"]: rel for rel, f in self.files.items() if f.get("library")}
        for rel, f in self.files.items():
            if f.get("part_of") and rel not in self.part_owner:
                po = f["part_of"]
                tgt = self._rel_uri(rel, po) if isinstance(po, str) and po.endswith(".dart") else lib_names.get(po)
                if tgt:
                    self.part_owner[rel] = tgt
        for rel in self.files:
            if rel not in self.part_owner:
                self.libs[rel] = DLib(file=rel, pkg=self.pkg_of(rel))
        for rel, owner in self.part_owner.items():
            if owner in self.libs:
                self.libs[owner].parts.append(rel)
            else:
                self.libs[rel] = DLib(file=rel, pkg=self.pkg_of(rel))
        for rel, f in self.files.items():
            lib = self.lib_of(rel)
            self._decls(lib, rel, f)
        for lib in self.libs.values():
            for rel in [lib.file] + lib.parts:
                f = self.files.get(rel) or {}
                for imp in f.get("imports") or []:
                    lib.imports.append(self._imp(rel, imp))
                for ex in f.get("exports") or []:
                    lib.exports.append(self._imp(rel, ex))
        for c in self.classes.values():
            for rel_, t in c.supers:
                sc = self.resolve_class(c.lib, split_type(t)[0])
                if sc:
                    self.subs.setdefault(sc.id, []).append(c)

    @staticmethod
    def _norm(f: dict) -> None:
        """Body facts: 'ft' is the fact type ('t' of a call fact is its receiver, as in expression reprs)."""
        def facts_of(x):
            for fact in x.get("facts") or []:
                fact["ft"] = "call" if fact.get("k") == "call" else fact.get("t")
        for c in f.get("classes") or []:
            for m in c.get("members") or []:
                facts_of(m)
        for fn in f.get("functions") or []:
            facts_of(fn)
        for v in f.get("vars") or []:
            facts_of(v)

    def _packages(self) -> None:
        for dp, dns, fns in os.walk(self.root):
            dns[:] = [d for d in dns if not d.startswith(".") and d not in ("build", "node_modules", "Pods", "ios", "android", "windows",
                                                                                "linux", "macos", "web")]
            if "pubspec.yaml" in fns:
                try:
                    txt = (Path(dp) / "pubspec.yaml").read_text(errors="replace")
                except OSError:
                    continue
                m = re.search(r"^name:\s*['\"]?([A-Za-z0-9_]+)", txt, re.M)
                if m:
                    rel = os.path.relpath(dp, self.root)
                    pk = Pkg(m.group(1), "" if rel == "." else rel)
                    self.pkgs.setdefault(pk.name, pk)
                    self.pkg_dirs.append(pk)
        self.pkg_dirs.sort(key=lambda p: -len(p.dir))

    def pkg_of(self, rel: str) -> Pkg | None:
        for p in self.pkg_dirs:
            if p.dir == "" or rel == p.dir or rel.startswith(p.dir + "/"):
                return p
        return None

    def _rel_uri(self, frm: str, uri: str | None) -> str | None:
        if not uri:
            return None
        if uri.startswith("package:"):
            name, _, rest = uri[8:].partition("/")
            pk = self.pkgs.get(name)
            if not pk:
                return None
            return os.path.normpath(os.path.join(pk.dir, "lib", rest)) if pk.dir else os.path.normpath(os.path.join("lib", rest))
        if uri.startswith("dart:") or "://" in uri:
            return None
        return os.path.normpath(os.path.join(os.path.dirname(frm), uri))

    def _imp(self, frm: str, imp: dict) -> dict:
        uri = imp.get("uri") or ""
        tgt = self._rel_uri(frm, uri)
        lib = self.libs.get(tgt) if tgt else None
        return {"lib": lib, "uri": uri, "prefix": imp.get("prefix"), "show": imp.get("show") or [], "hide": imp.get("hide") or [],
                "ext": lib is None, "l": imp.get("l"), "file": frm}

    def lib_of(self, rel: str) -> DLib:
        return self.libs[self.part_owner.get(rel, rel)] if self.part_owner.get(rel, rel) in self.libs else self.libs[rel]

    def _decls(self, lib: DLib, rel: str, f: dict) -> None:
        for c in f.get("classes") or []:
            dc = DClass(name=c["name"], file=rel, lib=lib, kind=c.get("kind", "class"), raw=c)
            if c.get("extends"):
                dc.supers.append(("extends", c["extends"]))
            for w in c.get("with") or []:
                dc.supers.append(("with", w))
            for w in c.get("implements") or []:
                dc.supers.append(("implements", w))
            for w in c.get("on") or []:
                dc.supers.append(("on", w))
            for m in c.get("members") or []:
                k = m.get("kind")
                if k == "field":
                    for v in m.get("vars") or []:
                        dc.fields[v["name"]] = DVar(v["name"], rel, lib, dc, m.get("type"), v.get("init"), v.get("l", m.get("line", 1)),
                                                    bool(m.get("static")), bool(m.get("const")), bool(m.get("final")), m.get("ann") or [])
                elif k == "ctor":
                    nm = m.get("name") or "new"
                    fn = DFunc(f"{dc.name}.{nm}" if nm != "new" else f"{dc.name}.new", rel, lib, dc, "ctor", m)
                    fn.name = nm if nm else "new"
                    dc.ctors[fn.name] = fn
                    self.funcs[fn.id] = fn
                elif k in ("method", "getter", "setter", "operator", "handler"):
                    nm = m["name"] + ("=" if k == "setter" else "")
                    fn = DFunc(nm, rel, lib, dc, k, m)
                    dc.methods[nm] = fn
                    self.funcs[fn.id] = fn
                    self.methods_by_name.setdefault(nm, []).append(fn)
            if c.get("kind") == "enum":
                for v in c.get("values") or []:
                    dc.fields[v["name"]] = DVar(v["name"], rel, lib, dc, dc.name, None, v.get("l", 1), True, True, True, v.get("ann") or [])
            lib.decls.setdefault(dc.name, dc)
            self.classes[dc.id] = dc
            self.by_name.setdefault(dc.name, []).append(dc)
        for fn_ in f.get("functions") or []:
            k = fn_.get("kind", "function")
            fn = DFunc(fn_["name"], rel, lib, None, "getter" if k == "getter" else "function", fn_)
            lib.decls.setdefault(fn.name, fn)
            self.funcs[fn.id] = fn
        for v in f.get("vars") or []:
            dv = DVar(v["name"], rel, lib, None, v.get("type"), v.get("init"), v.get("l", 1), True, bool(v.get("const")),
                      bool(v.get("final")), [], v.get("facts") or [])
            lib.decls.setdefault(dv.name, dv)
            self.vars.append(dv)

    # ------------------------------------------------------------------ scopes
    def namespace(self, lib: DLib, depth=0) -> dict:
        """Exported namespace of a library: public own decls + re-exports."""
        if lib._ns is not None:
            return lib._ns
        lib._ns = {}
        ns = {k: v for k, v in lib.decls.items() if not k.startswith("_")}
        if depth < 12:
            for ex in lib.exports:
                if ex["lib"] is None:
                    continue
                for k, v in self.namespace(ex["lib"], depth + 1).items():
                    if (ex["show"] and k not in ex["show"]) or k in ex["hide"]:
                        continue
                    ns.setdefault(k, v)
        lib._ns = ns
        return ns

    def lookup(self, lib: DLib, name: str):
        if name in lib.decls:
            return lib.decls[name]
        for imp in lib.imports:
            if imp["prefix"] or imp["lib"] is None:
                continue
            if (imp["show"] and name not in imp["show"]) or name in imp["hide"]:
                continue
            v = self.namespace(imp["lib"]).get(name)
            if v is not None:
                return v
        return None

    def prefix_imports(self, lib: DLib, prefix: str) -> list[dict]:
        return [i for i in lib.imports if i["prefix"] == prefix]

    def lookup_prefixed(self, lib: DLib, prefix: str, name: str):
        for imp in self.prefix_imports(lib, prefix):
            if imp["lib"] is not None:
                v = self.namespace(imp["lib"]).get(name)
                if v is not None:
                    return v
        return None

    def imports_ext(self, lib: DLib, pattern: str, prefix: str | None = "*", name: str | None = None) -> bool:
        """Library imports an external uri matching pattern (prefix='*': any; None: unprefixed only).
        With `name`, the import must also make that name visible (show/hide combinators)."""
        for imp in lib.imports:
            if imp["ext"] and re.search(pattern, imp["uri"]) and (prefix == "*" or imp["prefix"] == prefix):
                if name is not None and ((imp["show"] and name not in imp["show"]) or name in imp["hide"]):
                    continue
                return True
        return False

    def resolve_class(self, lib: DLib, tname: str) -> DClass | None:
        if not tname:
            return None
        if "." in tname:
            p, _, n = tname.partition(".")
            v = self.lookup_prefixed(lib, p, n)
        else:
            v = self.lookup(lib, tname)
        return v if isinstance(v, DClass) else None

    # ------------------------------------------------------------------ class hierarchy
    def supers(self, c: DClass, rels=("extends", "with", "implements", "on")) -> list:
        out = []
        for rel, t in c.supers:
            if rel not in rels:
                continue
            base = split_type(t)[0]
            sc = self.resolve_class(c.lib, base)
            out.append(("type", sc) if sc else ("ext", t))
        return out

    def mro(self, c: DClass, depth=0, rels=("extends", "with", "implements", "on")) -> list[DClass]:
        out, seen = [], {c.id}
        stack = [c]
        while stack and len(out) < 60:
            x = stack.pop(0)
            for kind, s in self.supers(x, rels):
                if kind == "type" and s.id not in seen:
                    seen.add(s.id)
                    out.append(s)
                    stack.append(s)
        return out

    def lineage(self, c: DClass) -> list[str]:
        """External base type texts (generic args kept) of c and its project ancestors."""
        out = []
        for x in [c] + self.mro(c):
            for kind, s in self.supers(x):
                if kind == "ext":
                    out.append(s)
        return out

    def ext_base_names(self, c: DClass) -> set[str]:
        names = {split_type(t)[0].split(".")[-1] for t in self.lineage(c)}
        for n in list(names):
            names |= EXTERNAL_SUPERS.get(n, set())
        return names

    def is_sub(self, c: DClass, *names: str) -> bool:
        if c.name in names:
            return True
        if self.ext_base_names(c) & set(names):
            return True
        return any(x.name in names for x in self.mro(c))

    def find_member(self, c: DClass, name: str) -> tuple[Any, bool]:
        """(member, inherited) — method/getter/field searched along extends/with/implements."""
        for i, x in enumerate([c] + self.mro(c)):
            if name in x.methods:
                return x.methods[name], i > 0
            if name in x.fields:
                return x.fields[name], i > 0
        return None, False

    def ctor(self, c: DClass, name: str = "new") -> DFunc | None:
        return c.ctors.get(name or "new")

    # ------------------------------------------------------------------ types
    def type_of_text(self, t: str | None, lib: DLib, depth=0):
        if t and " Function(" in t:
            return ("fnret", self.type_of_text(t.split(" Function(")[0], lib, depth + 1))
        base, args, _ = split_type(t)
        if not base or depth > 6:
            return None
        bname = base.split(".")[-1]
        if bname in FUTURE_TYPES:
            return ("fut", self.type_of_text(args[0], lib, depth + 1) if args else None)
        if bname in LIST_TYPES:
            return ("list", self.type_of_text(args[0], lib, depth + 1) if args else None)
        if bname == "Stream":
            return ("stream", self.type_of_text(args[0], lib, depth + 1) if args else None)
        if bname == "Map":
            return ("map", args)
        c = self.resolve_class(lib, base)
        if c:
            return ("inst", c, args)
        if bname in ("dynamic", "void", "Object", "var", "Never", "Null"):
            return None
        return ("einst", base, args)

    def var_type(self, v: DVar, depth=0):
        if v.type:
            return self.type_of_text(v.type, v.lib)
        if v.cls and v.cls.kind == "enum" and v.init is None and v.type == v.cls.name:
            return ("inst", v.cls, [])
        if v.init is not None and depth < 4:
            key = ("dv", v.file, v.name, v.line)
            if key in self._infer_stack:
                return None
            self._infer_stack.add(key)
            try:
                return self.infer(v.init, Ctx(self, v.lib, None, v.cls))
            finally:
                self._infer_stack.discard(key)
        # field initialised from a constructor parameter (this.x / x = param)
        if v.cls:
            for ct in v.cls.ctors.values():
                for p in ct.params:
                    if p["name"] == v.name and p.get("this") and p.get("type"):
                        return self.type_of_text(p["type"], v.lib)
                for ini in ct.raw.get("inits") or []:
                    if ini.get("field") == v.name:
                        return self.infer(ini["v"], Ctx(self, v.lib, ct, v.cls))
        return None

    def func_ret(self, f: DFunc, depth=0):
        if f.kind == "ctor":
            return ("inst", f.cls, [])
        if f.raw.get("ret"):
            t = self.type_of_text(f.raw["ret"], f.lib)
            return t
        if depth < 3:
            for x in f.facts:
                if x["ft"] == "return":
                    key = ("ret", f.id)
                    if key in self._infer_stack:
                        return None
                    self._infer_stack.add(key)
                    try:
                        t = self.infer(x["v"], Ctx(self, f.lib, f, f.cls))
                    finally:
                        self._infer_stack.discard(key)
                    if t:
                        return ("fut", t) if f.raw.get("async") else t
        return None

    def infer(self, r, ctx: Ctx, depth=0):
        if not isinstance(r, dict) or depth > 12:
            return None
        for rule in self.type_rules:
            t = rule(self, r, ctx)
            if t is not None:
                return t
        k = r.get("k")
        if k == "id":
            return self.infer_name(r["v"], ctx, depth)
        if k == "this":
            return ("inst", ctx.cls, []) if ctx.cls else None
        if k == "super":
            if ctx.cls:
                for kind, s in self.supers(ctx.cls, ("extends",)):
                    return ("inst", s, []) if kind == "type" else ("einst", s, [])
            return None
        if k == "prop":
            if r["t"].get("k") == "id":
                # prefixed class / top-level: p.X
                pfx = r["t"]["v"]
                if pfx not in ctx.locals and self.prefix_imports(ctx.lib, pfx):
                    v = self.lookup_prefixed(ctx.lib, pfx, r["n"])
                    return self._decl_type(v)
            tt = self.infer(r["t"], ctx, depth + 1)
            return self.member_type(tt, r["n"])
        if k == "call":
            for tgt, conf, via in self.resolve_call(r, ctx):
                if isinstance(tgt, DClass):
                    return ("inst", tgt, [])
                t = self.func_ret(tgt)
                if t:
                    return t
            if r.get("t") is None and r.get("n"):
                ft = self.infer_name(r["n"], ctx, depth + 1)
                if ft and ft[0] == "fnret":
                    return ft[1]
            elif r.get("n") is None and isinstance(r.get("t"), dict):
                ft = self.infer(r["t"], ctx, depth + 1)
                if ft and ft[0] == "fnret":
                    return ft[1]
            ta = r.get("ta")
            if ta and r.get("n") in PROVIDER_LOOKUPS | {"getIt", "locator", "sl"}:
                return self.type_of_text(ta[0], ctx.lib)
            if ta and r.get("n") is None and root_name(r.get("t")) in ("getIt", "locator", "sl", "GetIt", "injector", "inject"):
                return self.type_of_text(ta[0], ctx.lib)
            if r.get("n") is None and isinstance(r.get("t"), dict):
                return None
            ct = ctor_type(r)
            if ct:
                return ("einst", ct, r.get("ta") or [])
            # external method of an external type: Response.data etc. are handled by callers
            return None
        if k == "new":
            c = self.resolve_class(ctx.lib, r["type"])
            if c:
                return ("inst", c, r.get("ta") or [])
            return ("einst", r["type"], r.get("ta") or [])
        if k == "await":
            t = self.infer(r["e"], ctx, depth + 1)
            return t[1] if t and t[0] == "fut" else t
        if k == "as":
            return self.type_of_text(r["type"], ctx.lib)
        if k == "nn":
            return self.infer(r["e"], ctx, depth + 1)
        if k == "cond":
            return self.infer(r["a"], ctx, depth + 1) or self.infer(r["b"], ctx, depth + 1)
        if k == "bin" and r.get("op") == "??":
            return self.infer(r["l"], ctx, depth + 1) or self.infer(r["r"], ctx, depth + 1)
        if k == "cascade":
            return self.infer(r["t"], ctx, depth + 1)
        if k in ("str", "tpl", "cat"):
            return ("einst", "String", [])
        if k == "idx":
            tt = self.infer(r["t"], ctx, depth + 1)
            if tt and tt[0] == "list":
                return tt[1]
            return None
        return None

    def _decl_type(self, v):
        if isinstance(v, DClass):
            return ("type", v)
        if isinstance(v, DFunc):
            return self.func_ret(v) if v.kind == "getter" else ("func", v)
        if isinstance(v, DVar):
            return self.var_type(v)
        return None

    def infer_name(self, name: str, ctx: Ctx, depth=0):
        if name in ctx.bindings:
            br, bctx = ctx.bindings[name]
            return self.infer(br, bctx, depth + 1) if br is not None else None
        loc = ctx.locals.get(name)
        if loc:
            kind, d = loc
            if kind == "param":
                if d.get("type"):
                    return self.type_of_text(d["type"], ctx.lib)
                if d.get("this") and ctx.cls and name in ctx.cls.fields:
                    return self.var_type(ctx.cls.fields[name])
                return None
            if d.get("type"):
                return self.type_of_text(d["type"], ctx.lib)
            if d.get("init") is not None:
                key = ("loc", ctx.fn.id if ctx.fn else "", name)
                if key in self._infer_stack:
                    return None
                self._infer_stack.add(key)
                try:
                    return self.infer(d["init"], ctx, depth + 1)
                finally:
                    self._infer_stack.discard(key)
            return None
        if ctx.cls:
            m, _ = self.find_member(ctx.cls, name)
            if isinstance(m, DVar):
                return self.var_type(m)
            if isinstance(m, DFunc):
                return self.func_ret(m) if m.kind == "getter" else ("func", m)
            if name == "widget" and ctx.cls:
                w = self.state_widget(ctx.cls)
                if w:
                    return ("inst", w, [])
        v = self.lookup(ctx.lib, name)
        if v is not None:
            return self._decl_type(v)
        if name[:1].isupper():
            return ("etype", name)
        return None

    def state_widget(self, c: DClass) -> DClass | None:
        """State<MyWidget> -> MyWidget (for `widget.x`)."""
        for t in self.lineage(c):
            base, args, _ = split_type(t)
            if base.split(".")[-1] == "State" and args:
                return self.resolve_class(c.lib, split_type(args[0])[0])
        return None

    def member_type(self, tt, name: str):
        if not tt:
            return None
        if tt[0] == "inst" and tt[1] is not None:
            m, _ = self.find_member(tt[1], name)
            if isinstance(m, DVar):
                return self.var_type(m)
            if isinstance(m, DFunc):
                return self.func_ret(m) if m.kind == "getter" else ("bound", m)
            return None
        if tt[0] == "type":
            c = tt[1]
            if name in c.fields:
                f = c.fields[name]
                return ("inst", c, []) if c.kind == "enum" and f.init is None and f.type == c.name else self.var_type(f)
            if name in c.methods:
                m = c.methods[name]
                return self.func_ret(m) if m.kind == "getter" else ("func", m)
            if name in c.ctors:
                return ("ctor", c.ctors[name])
        if tt[0] == "einst" and name == "data":
            return ("json", tt[1])
        return None

    # ------------------------------------------------------------------ calls
    def resolve_call(self, r: dict, ctx: Ctx) -> list[tuple[Any, str, str | None]]:
        """Targets of a call/new repr: [(DFunc | DClass, confidence, via)]."""
        k = r.get("k")
        if k == "new":
            c = self.resolve_class(ctx.lib, r["type"])
            if not c:
                return []
            return [(c, EXACT, r.get("ctor"))]
        if k != "call":
            return []
        n, t = r.get("n"), r.get("t")
        if n is None:  # f(...) where f is an expression
            if isinstance(t, dict) and t.get("k") == "id":
                return self._unqualified(t["v"], ctx)
            if isinstance(t, dict) and t.get("k") == "prop":
                return self.resolve_call({"k": "call", "t": t["t"], "n": t["n"], "a": r.get("a"), "na": r.get("na")}, ctx)
            return []
        if t is None:
            return self._unqualified(n, ctx)
        if t.get("k") == "super" and ctx.cls:
            for s in self.mro(ctx.cls, rels=("extends", "with")):
                if n in s.methods:
                    return [(s.methods[n], EXACT, "super")]
            return []
        if t.get("k") == "this" and ctx.cls:
            m, inh = self.find_member(ctx.cls, n)
            return [(m, EXACT if not inh else RESOLVED, None)] if isinstance(m, DFunc) else []
        if t.get("k") == "id" and t["v"] not in ctx.locals and t["v"] not in ctx.bindings and self.prefix_imports(ctx.lib, t["v"]):
            v = self.lookup_prefixed(ctx.lib, t["v"], n)
            if isinstance(v, DFunc):
                return [(v, EXACT, None)]
            if isinstance(v, DClass):
                return [(v, EXACT, None)]
            return []
        tt = self.infer(t, ctx)
        if tt and tt[0] == "type":
            c = tt[1]
            if n in c.ctors:
                return [(c, EXACT, n)]
            if n in c.methods:
                return [(c.methods[n], EXACT, None)]
            for s in self.mro(c):
                if n in s.methods and s.methods[n].raw.get("static"):
                    return [(s.methods[n], RESOLVED, None)]
            return []
        if tt and tt[0] == "inst" and tt[1] is not None:
            m, inh = self.find_member(tt[1], n)
            if isinstance(m, DFunc):
                return [(m, RESOLVED, None)]
            if isinstance(m, DVar):
                return []  # calling a function-typed field
            # extension methods on a project type or any of its supertypes (incl. well-known external ones)
            names = {tt[1].name} | {x.name for x in self.mro(tt[1])} | self.ext_base_names(tt[1])
            return self._extension(names, n, ctx)
        if tt and tt[0] in ("einst", "etype", "list", "map", "fut", "json", "stream"):
            return self._extension(tt[1] if tt[0] in ("einst", "etype") else tt[0], n, ctx) if tt[0] in ("einst", "etype") else []
        # unknown receiver: unique project method name (heuristic)
        if n in STOP_METHODS or n.startswith("_") is False and len(n) < 4:
            return []
        rn = root_name(t)
        if rn and rn not in ctx.locals and not (ctx.cls and self.find_member(ctx.cls, rn)[0]) and self.prefix_imports(ctx.lib, rn):
            return []
        cands = [m for m in self.methods_by_name.get(n, []) if not m.raw.get("static") and m.kind in ("method", "getter")]
        if len(cands) == 1:
            return [(cands[0], HEURISTIC, "unique-method-name")]
        # abstract + implementations: prefer the abstract declaration
        abstract = [m for m in cands if m.raw.get("abstract")]
        if len(abstract) == 1:
            return [(abstract[0], HEURISTIC, "unique-abstract-method-name")]
        return []

    def _extension(self, tname, n: str, ctx: Ctx):
        if getattr(self, "_ext_index", None) is None:
            self._ext_index = {}
            for lib_c in self.classes.values():
                if lib_c.kind == "extension":
                    on = split_type(lib_c.raw.get("on_type") or "")[0].split(".")[-1]
                    for mn, m in lib_c.methods.items():
                        self._ext_index.setdefault(mn, []).append((on, m))
        names = {x.split(".")[-1] for x in tname} if isinstance(tname, (set, frozenset, list, tuple)) else {tname.split(".")[-1]}
        for on, m in self._ext_index.get(n, []):
            if on in names:
                return [(m, RESOLVED, "extension")]
        return []

    def _unqualified(self, n: str, ctx: Ctx):
        if n in ctx.locals or n in ctx.bindings:
            return []
        if ctx.cls:
            m, inh = self.find_member(ctx.cls, n)
            if isinstance(m, DFunc):
                return [(m, EXACT if not inh else RESOLVED, None)]
            if isinstance(m, DVar):
                return []
            if ctx.cls.kind == "extension":
                pass
        v = self.lookup(ctx.lib, n)
        if isinstance(v, DFunc):
            return [(v, EXACT, None)]
        if isinstance(v, DClass):
            return [(v, EXACT, None)]
        return []

    # ------------------------------------------------------------------ iteration
    def all_funcs(self) -> Iterator[DFunc]:
        yield from self.funcs.values()

    def ctx_of(self, f: DFunc, bindings=None) -> Ctx:
        return Ctx(self, f.lib, f, f.cls, bindings)

    def calls_in(self, f: DFunc) -> Iterator[dict]:
        for x in f.facts:
            if x["ft"] in ("call", "new"):
                yield x

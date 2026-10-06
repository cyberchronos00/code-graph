"""Java language plugin (heuristic).

Declarations become nodes with the same id shape as the Kotlin plugin, so a later scip-java exact
layer (#164 part C) can map compiler symbols onto them:

    class:com.example.Foo              class, interface, enum, record (attrs.java_kind)
    class:com.example.Foo.Bar          nested type; an anonymous class is Foo.1, Foo.2, ...
    method:com.example.Foo.bar         overloads share this id (as Kotlin does)
    constructor:com.example.Foo.<init>
    field:com.example.Foo.name
    enum_case:com.example.Color.RED
    package:com.example
    file:java:<repo-relative path>

Calls are CALLS edges labelled heuristic. The receiver is resolved from locals, parameters,
fields and `this` / `super`, a call or `new` whose return type is known, then an explicit or
wildcard import, the same package, then a method name that is unique in the project (two to
five names: a candidate edge each). A known type that is not in the project is not name-matched.
EXTENDS / IMPLEMENTS and IMPLEMENTED_BY / OVERRIDDEN_BY are heuristic too.

Spring facts are extracted by ``cg_code_graph/plugins/jvm/spring.py`` from ``Decl.annotations`` and these trees.
Part C should replace `_exact` and keep these ids. This part does not run scip-java.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ...core.fsutil import keep_file
from ...core.model import EXACT, HEURISTIC
from ...core.paths import rules as path_rules
from ...core.plugin import GraphBuilder, LanguagePlugin, Project
from ...core.syntax_errors import merge, tree_spans
from ..native.ts import TreeSitterMissing

EXTS = (".java",)
MAX_CANDIDATES = 5
CLASSISH = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration")
KIND_OF = {"class_declaration": "class", "interface_declaration": "interface",
           "enum_declaration": "enum", "record_declaration": "record"}
MODS = {"public", "private", "protected", "static", "final", "abstract", "sealed", "native",
        "synchronized", "default", "strictfp", "transitive"}
TEST_PATH = ("/src/test/", "/src/androidTest/")


def parser():
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_java as m
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise TreeSitterMissing(
            f"tree-sitter grammar for java not installed ({e}); pip install tree-sitter tree-sitter-java") from e
    return Parser(Language(m.language()))


def source_files(root: Path, project=None) -> list[str]:
    rules = path_rules(project, "java")
    out = []
    for dp, dn, fn in os.walk(root):
        rd = os.path.relpath(dp, root).replace(os.sep, "/")
        rd = "" if rd == "." else rd
        dn[:] = sorted(rules.prune(rd, dn, dot=True))
        for f in sorted(fn):
            if f.endswith(EXTS):
                rel = f"{rd}/{f}" if rd else f
                if keep_file(os.path.join(dp, f)) and not rules.excluded(rel):
                    out.append(rel)
    return out


@dataclass
class Decl:
    id: str
    kind: str                 # class | method | constructor
    name: str
    fqn: str
    file: str
    line: int
    end: int
    cls: str | None = None    # enclosing type fqn
    supers: list = field(default_factory=list)
    annotations: list = field(default_factory=list)   # (simple name, first string arg or None, raw text)
    modifiers: set = field(default_factory=set)
    types: dict = field(default_factory=dict)         # param / local / field name -> type text
    java_kind: str | None = None
    ret: str | None = None        # method return type text; None when overloads disagree
    test: bool = False
    field_anns: dict = field(default_factory=dict)    # field name -> annotations (constructor injection uses param_anns)
    param_anns: dict = field(default_factory=dict)    # parameter name -> annotations


class JFile:
    def __init__(self, rel: str, src: bytes, tree):
        self.rel, self.src, self.tree = rel, src, tree
        self.package = ""
        self.imports: dict[str, str] = {}          # simple name -> fqn
        self.star: list[str] = []                  # packages of wildcard imports
        self.static_imports: dict[str, str] = {}   # member name -> class fqn
        self.static_star: list[str] = []           # class fqns of static wildcard imports
        self.test = any(p in f"/{rel}" for p in TEST_PATH)


class JavaPlugin(LanguagePlugin):
    name = "java"

    def detect(self, project: Project) -> bool:
        self._files = source_files(project.root, project)
        return bool(self._files)

    def t(self, n) -> str:
        return self.cur.src[n.start_byte:n.end_byte].decode("utf-8", "replace")

    def index(self, project: Project, builder: GraphBuilder, frameworks) -> dict:
        t0 = time.time()
        p = parser()
        self.b = builder
        files = getattr(self, "_files", None)
        if files is None:
            files = source_files(project.root, project)
        self.decls: dict[str, Decl] = {}
        self.by_name: dict[str, list[Decl]] = defaultdict(list)
        self.classes: dict[str, Decl] = {}
        self.class_short: dict[str, list[Decl]] = defaultdict(list)
        self.members: dict[str, dict[str, list[Decl]]] = defaultdict(lambda: defaultdict(list))
        self.fields: dict[str, dict[str, str]] = defaultdict(dict)     # class fqn -> name -> type text
        self.calls: list[tuple] = []
        self.news: list[tuple] = []
        self.fnrefs: list[tuple] = []
        self.bodies: list[Decl] = []          # every method / constructor, including overloads that share an id
        self._anon_n: dict[str, int] = defaultdict(int)
        self.st: dict = defaultdict(int)
        from ..jvm import spring as spring_mod
        spring_mod.prepare(self, project.root)
        jfiles, failed = [], []
        errs: dict[str, list] = {}
        for rel in files:
            try:
                raw = (project.root / rel).read_bytes()
            except OSError:
                failed.append(rel)
                continue
            src, utf8 = _as_utf8(raw)
            if not utf8:
                self.st["non_utf8_files"] += 1
            try:
                tree = p.parse(src)
            except Exception:
                failed.append(rel)
                continue
            jf = JFile(rel, src, tree)
            if tree.root_node.has_error:
                self.st["files_with_syntax_errors"] += 1
                errs[rel] = merge(tree_spans(tree.root_node))
            jfiles.append(jf)
        self._jf_by_rel = {jf.rel: jf for jf in jfiles}
        for jf in jfiles:
            self.cur = jf
            self._header(jf)
            self._decls(jf.tree.root_node, jf, None)
        from ..jvm import spring as spring_mod
        spring_mod.index_java_repos(self, jfiles)
        for jf in jfiles:
            self.cur = jf
            fid = self._file_node(jf)
            methods, types = self._spans(jf)
            for d in self.bodies:
                if d.file != jf.rel:
                    continue
                node = methods.get(d.line)
                if node is None:
                    continue
                body = next((c for c in node.children if c.type in ("block", "constructor_body")), None)
                if body is not None:
                    self._refs(body, d.id, d, jf)
            for d in list(self.classes.values()):
                if d.file == jf.rel:
                    self._class_level_refs(d, jf, fid, types.get(d.line))
        self._agree_returns()
        self._resolve_calls()
        self._hierarchy()
        from ..jvm import spring as spring_mod
        spring_mod.finish(self, jfiles, "java")
        mode = self._exact(project, files)
        self.file_report = {"seen": [jf.rel for jf in jfiles] + failed, "parse_failed": failed, "syntax_errors": errs}
        st = dict(self.st)
        st.update({"mode": mode, "files": len(jfiles), "declarations": len(self.decls),
                   "seconds": round(time.time() - t0, 2)})
        return st

    def _exact(self, project: Project, files: list[str]) -> str:
        """Part C: opt-in scip-java (reuse kotlin.exact.find_java / scip_java_candidates) mapped onto these ids.

        A `--scip` file is still imported by the generic importer until that lands. One scip-java run
        should serve Java and Kotlin together.
        """
        return "heuristic"

    def _file_node(self, jf: JFile) -> str:
        attrs = {"test": True} if jf.test else {}
        fid = self.b.add_node("file", f"java:{jf.rel}", name=jf.rel, file=jf.rel, line=1, lang="java",
                              module=jf.package or None, attrs=attrs)
        if jf.package:
            pid = self.b.add_node("package", jf.package, name=jf.package, fqn=jf.package, file=jf.rel, line=1,
                                  lang="java", module=jf.package)
            self.b.add_edge(fid, pid, "CONTAINS", jf.rel, 1, EXACT)
        return fid

    def _header(self, jf: JFile):
        for c in jf.tree.root_node.children:
            if c.type == "package_declaration":
                q = next((x for x in c.children if x.type in ("scoped_identifier", "identifier")), None)
                jf.package = self.t(q) if q else ""
            elif c.type == "import_declaration":
                self._import(jf, c)

    def _import(self, jf: JFile, c):
        static = any(x.type == "static" for x in c.children)
        star = any(x.type == "asterisk" for x in c.children)
        q = next((x for x in c.children if x.type in ("scoped_identifier", "identifier")), None)
        if q is None:
            return
        fq = self.t(q)
        if star:
            (jf.static_star if static else jf.star).append(fq)
            return
        if static and "." in fq:
            cls, name = fq.rsplit(".", 1)
            jf.static_imports[name] = cls
            return
        jf.imports[fq.rsplit(".", 1)[-1]] = fq

    # ------------------------------------------------------------------ declarations
    def _decls(self, n, jf: JFile, cls: Decl | None, scope: str | None = None):
        """`scope` is the fqn prefix for a type declared inside a method (`Foo.place.Local`, `Foo.place.1`)."""
        for c in n.children:
            if c.type in CLASSISH:
                self._type_decl(c, jf, cls, scope)
            elif c.type in ("method_declaration", "constructor_declaration", "compact_constructor_declaration") and cls:
                self._callable(c, jf, cls)
            elif c.type == "field_declaration" and cls:
                self._fields(c, jf, cls)
            elif c.type == "enum_constant" and cls:
                self._enum_const(c, jf, cls)
            elif c.type == "object_creation_expression":
                self._anon(c, jf, cls, scope)
                for ch in c.children:
                    if ch.type != "class_body":
                        self._decls(ch, jf, cls, scope)
            else:
                self._decls(c, jf, cls, scope)

    def _type_decl(self, c, jf: JFile, cls: Decl | None, scope: str | None = None):
        nm = self._name(c)
        if not nm:
            self._decls(c, jf, cls, scope)
            return
        prefix = scope or (cls.fqn if cls else (jf.package or ""))
        fq = f"{prefix}.{nm}" if prefix else nm
        anns, mods = self._annotations(c)
        supers = self._supers(c)
        jk = KIND_OF[c.type]
        dc = Decl(f"class:{fq}", "class", nm, fq, jf.rel, c.start_point[0] + 1, c.end_point[0] + 1,
                  cls.fqn if cls else None, supers, anns, mods, java_kind=jk, test=jf.test)
        if c.type == "record_declaration":
            params = next((x for x in c.children if x.type == "formal_parameters"), None)
            dc.types.update(self._params(params)[0])
        self._add_decl(dc)
        body = next((x for x in c.children if x.type in ("class_body", "interface_body", "enum_body")), None)
        if c.type == "record_declaration":
            params = next((x for x in c.children if x.type == "formal_parameters"), None)
            for name, ty in self._params(params)[0].items():
                self._field_node(dc, name, ty, jf, params or c)
        if body is not None:
            self._decls(body, jf, dc, None)
        if jk != "interface" and "<init>" not in self.members.get(fq, {}):
            self._callable_decl(dc, "<init>", "constructor", c.start_point[0] + 1, c.end_point[0] + 1,
                                jf, {}, set(), [], source=False)

    def _anon(self, c, jf: JFile, cls: Decl | None, scope: str | None = None):
        body = next((x for x in c.children if x.type == "class_body"), None)
        if body is None or cls is None:
            return
        owner = scope or cls.fqn
        self._anon_n[owner] += 1
        nm = str(self._anon_n[owner])
        fq = f"{owner}.{nm}"
        ty = self._created_type(c)
        dc = Decl(f"class:{fq}", "class", nm, fq, jf.rel, c.start_point[0] + 1, c.end_point[0] + 1,
                  cls.fqn, [ty] if ty else [], [], set(), java_kind="class", test=jf.test)
        self._add_decl(dc)
        self._decls(body, jf, dc, None)
        if "<init>" not in self.members.get(fq, {}):
            self._callable_decl(dc, "<init>", "constructor", dc.line, dc.end, jf, {}, set(), [], source=False)

    def _enum_const(self, c, jf: JFile, cls: Decl):
        nm = self._name(c)
        if not nm:
            return
        body = next((x for x in c.children if x.type == "class_body"), None)
        line = c.start_point[0] + 1
        if body is not None:
            fq = f"{cls.fqn}.{nm}"
            dc = Decl(f"class:{fq}", "class", nm, fq, jf.rel, line, c.end_point[0] + 1, cls.fqn, [cls.name],
                      [], set(), java_kind="class", test=jf.test)
            self._add_decl(dc)
            self._decls(body, jf, dc)
        nid = self.b.add_node("enum_case", f"{cls.fqn}.{nm}", name=nm, fqn=f"{cls.fqn}.{nm}", file=jf.rel,
                              line=line, end_line=c.end_point[0] + 1, module=jf.package or None, lang="java",
                              attrs={"test": True} if jf.test else {})
        self.b.add_edge(cls.id, nid, "CONTAINS", jf.rel, line, EXACT)

    def _callable(self, c, jf: JFile, cls: Decl):
        if c.type == "compact_constructor_declaration":
            nm, kind = "<init>", "constructor"
        elif c.type == "constructor_declaration":
            nm, kind = "<init>", "constructor"
        else:
            nm, kind = self._name(c), "method"
        if not nm:
            return
        anns, mods = self._annotations(c)
        params = next((x for x in c.children if x.type == "formal_parameters"), None)
        types, panns = self._params(params)
        ret = self._decl_type(c) if kind == "method" else None
        dc = self._callable_decl(cls, nm, kind, c.start_point[0] + 1, c.end_point[0] + 1, jf, types, mods, anns,
                                 ret=ret, param_anns=panns)
        self._locals_into(c, dc.types)
        # local and anonymous types inside the body (`Foo.place.Local`, `Foo.place.1`)
        host = f"{cls.fqn}.{nm}" if kind == "method" else f"{cls.fqn}.init"
        self._decls(c, jf, cls, host)

    def _callable_id(self, cls: Decl, name: str, kind: str) -> str:
        if kind == "constructor":
            return f"constructor:{cls.fqn}.<init>"
        return f"method:{cls.fqn}.{name}"

    def _callable_decl(self, cls: Decl, name: str, kind: str, line: int, end: int, jf: JFile, types: dict,
                       mods: set, anns: list, source: bool = True, ret: str | None = None,
                       param_anns: dict | None = None) -> Decl:
        fq = f"{cls.fqn}.{name}" if kind == "method" else f"{cls.fqn}.<init>"
        dc = Decl(self._callable_id(cls, name, kind), kind, name if kind == "method" else "<init>", fq, jf.rel,
                  line, end, cls.fqn, [], anns, mods, types=dict(types), ret=ret, test=jf.test,
                  param_anns=dict(param_anns or {}))
        self._add_decl(dc)
        if source:
            self.bodies.append(dc)
        return dc

    def _fields(self, c, jf: JFile, cls: Decl):
        ty = self._decl_type(c)
        anns, _ = self._annotations(c)
        for d in c.children:
            if d.type != "variable_declarator":
                continue
            nm = self._name(d)
            if nm and ty:
                self._field_node(cls, nm, ty, jf, d)
                if anns:
                    cls.field_anns[nm] = anns

    def _field_node(self, cls: Decl, name: str, ty: str, jf: JFile, n):
        if name in self.fields[cls.fqn]:
            return
        line = n.start_point[0] + 1
        fid = self.b.add_node("field", f"{cls.fqn}.{name}", name=name, fqn=f"{cls.fqn}.{name}", file=jf.rel,
                              line=line, end_line=n.end_point[0] + 1, module=jf.package or None, lang="java",
                              attrs={"type": ty.split("<")[0], **({"test": True} if jf.test else {})})
        self.b.add_edge(cls.id, fid, "CONTAINS", jf.rel, line, EXACT)
        self.fields[cls.fqn][name] = ty
        cls.types.setdefault(name, ty)

    def _add_decl(self, d: Decl):
        attrs = {}
        if d.java_kind:
            attrs["java_kind"] = d.java_kind
        if d.test:
            attrs["test"] = True
        for a in ("static", "abstract", "final"):
            if a in d.modifiers:
                attrs[a] = True
        if d.annotations:
            attrs["annotations"] = [a[0] for a in d.annotations][:12]
        if d.kind == "class":
            if d.fqn in self.classes:
                return
            self.classes[d.fqn] = d
            self.class_short[d.name].append(d)
        key = d.id.split(":", 1)[1]
        self.b.add_node(d.kind, key, name=d.name, fqn=d.fqn, file=d.file, line=d.line, end_line=d.end,
                        module=(d.fqn.rsplit(".", 1)[0] if "." in d.fqn else None), lang="java", attrs=attrs)
        self.decls[d.id] = d
        if d.kind == "class":
            if d.cls:
                self.b.add_edge(f"class:{d.cls}", d.id, "CONTAINS", d.file, d.line, EXACT)
            elif d.fqn.rsplit(".", 1)[0] == self._jf_by_rel[d.file].package and self._jf_by_rel[d.file].package:
                self.b.add_edge(f"package:{self._jf_by_rel[d.file].package}", d.id, "CONTAINS", d.file, d.line, EXACT)
            return
        self.by_name[d.name].append(d)
        if d.cls:
            self.members[d.cls][d.name].append(d)
            self.b.add_edge(f"class:{d.cls}", d.id, "CONTAINS", d.file, d.line, EXACT)

    # ------------------------------------------------------------------ references
    def _spans(self, jf: JFile):
        """One walk: start line -> method node, start line -> type node. Avoids a full rescan per declaration."""
        methods, types = {}, {}
        stack = [jf.tree.root_node]
        while stack:
            n = stack.pop()
            line = n.start_point[0] + 1
            if n.type in ("method_declaration", "constructor_declaration", "compact_constructor_declaration"):
                methods.setdefault(line, n)
            elif n.type in CLASSISH:
                types.setdefault(line, n)
            stack.extend(n.children)
        return methods, types

    def _class_level_refs(self, cls: Decl, jf: JFile, file_id: str, node):
        if node is None:
            return
        body = next((x for x in node.children if x.type in ("class_body", "interface_body", "enum_body")), None)
        if body is None:
            return
        self._level_refs(body, cls.id, cls, jf)

    def _level_refs(self, n, owner: str, decl: Decl, jf: JFile):
        for c in n.children:
            if c.type in CLASSISH or c.type in ("method_declaration", "constructor_declaration",
                                                "compact_constructor_declaration"):
                continue
            if c.type == "enum_body_declarations":
                self._level_refs(c, owner, decl, jf)
            elif c.type in ("field_declaration", "static_initializer", "block", "enum_constant"):
                self._refs(c, owner, decl, jf)

    def _refs(self, n, owner: str, decl: Decl | None, jf: JFile):
        ty = n.type
        if ty in CLASSISH or ty in ("method_declaration", "constructor_declaration", "compact_constructor_declaration"):
            return
        if ty in ("annotation", "marker_annotation"):
            return
        if ty == "method_invocation":
            self._note_call(n, owner, decl, jf)
            return
        elif ty == "object_creation_expression":
            self._note_new(n, owner, decl, jf)
            for c in n.children:
                if c.type != "class_body":
                    self._refs(c, owner, decl, jf)
            return
        elif ty == "method_reference":
            self._note_ref(n, owner, decl, jf)
            return
        for c in n.children:
            self._refs(c, owner, decl, jf)

    def _note_call(self, n, owner: str, decl: Decl | None, jf: JFile):
        name, recv = self._call_parts(n)
        if name:
            self.calls.append((owner, name, recv, n.start_point[0] + 1, jf, decl))
            from ..jvm import spring as spring_mod
            rtext = self.t(recv) if recv is not None else None
            spring_mod.observe_call(self, owner, name, rtext, self._call_strings(n), self.t(n),
                                    n.start_point[0] + 1, jf.rel, decl, bool(decl.test) if decl is not None else jf.test)
        for c in n.children:
            if c.type == "argument_list" or (recv is not None and c == recv):
                self._refs(c, owner, decl, jf)

    def _note_new(self, n, owner: str, decl: Decl | None, jf: JFile):
        ty = self._created_type(n)
        if ty:
            self.news.append((owner, ty, n.start_point[0] + 1, jf, decl))

    def _note_ref(self, n, owner: str, decl: Decl | None, jf: JFile):
        parts = [c for c in n.children if c.type != "::"]
        if not parts:
            return
        name_n = parts[-1]
        if name_n.type == "identifier":
            name = self.t(name_n)
        elif name_n.type == "new":
            name = "<init>"
        else:
            return
        recv = None
        if len(parts) > 1:
            r = parts[-2]
            recv = r.type if r.type in ("this", "super") else self.t(r)
        if name:
            self.fnrefs.append((owner, name, recv, n.start_point[0] + 1, jf, decl))

    def _call_parts(self, n):
        parts = []
        for c in n.children:
            if c.type == "argument_list":
                break
            if c.type in (".", "type_arguments"):
                continue
            parts.append(c)
        if not parts:
            return None, None
        if len(parts) == 1 and parts[0].type == "identifier":
            return self.t(parts[0]), None
        if parts[-1].type == "identifier":
            return self.t(parts[-1]), parts[0]
        return None, None

    def _resolve_calls(self):
        from ..jvm import spring as spring_mod
        for owner, name, recv, line, jf, decl in self.calls:
            self.cur = jf
            rtext = self.t(recv) if recv is not None else None
            spring_mod.repository_access(self, owner, name, rtext, line, jf.rel, decl)
            self._how = None
            targets = self._targets(name, recv, jf, decl)
            if not targets:
                self.st["calls_unresolved"] += 1
                continue
            how = {"binding": self._how} if self._how else {}
            if self._how == "candidate":
                how["candidates"] = len(targets)
                self.st["call_candidate_edges"] += len(targets)
            limit = MAX_CANDIDATES if self._how == "candidate" else 3
            for t in targets[:limit]:
                self.b.add_edge(owner, t.id, "CALLS", jf.rel, line, HEURISTIC, **how)
            self.st["calls_resolved"] += 1
        for owner, ty, line, jf, decl in self.news:
            self.cur = jf
            tc = self._class_of_type(ty, jf, decl)
            if tc is None:
                self.st["inits_unresolved"] += 1
                continue
            self.b.add_edge(owner, tc.id, "INSTANTIATES", jf.rel, line, HEURISTIC)
            ctor = self._uniq(self.members.get(tc.fqn, {}).get("<init>", []))
            for m in ctor[:1]:
                self.b.add_edge(owner, m.id, "CALLS", jf.rel, line, HEURISTIC, binding="constructor")
            self.st["inits_resolved"] += 1
        for owner, name, recv, line, jf, decl in self.fnrefs:
            self.cur = jf
            if recv:
                tc = self._ref_class(recv, jf, decl)
                targets = self._member(tc, name) if tc is not None else []
            else:
                targets = self._targets(name, None, jf, decl)
            for t in self._uniq(targets)[:3]:
                if t.kind == "class":
                    continue
                self.b.add_edge(owner, t.id, "REFERENCES_FN", jf.rel, line, HEURISTIC, how="method-reference")
                self.st["method_references"] += 1

    def _agree_returns(self):
        """Overloads share one method id. A chain uses the return type only when every overload agrees.

        A short name is qualified from the method's own type, so `Inner b()` inside `Box` stays `Box.Inner`
        when the call site is another class.
        """
        for methods in self.members.values():
            for ds in methods.values():
                for d in ds:
                    if not d.ret:
                        continue
                    jf = self._jf_by_rel.get(d.file)
                    owner = self.classes.get(d.cls) if d.cls else None
                    if jf is None:
                        continue
                    hit = self._class_of_type(d.ret, jf, owner)
                    if hit is not None:
                        d.ret = hit.fqn
                rets = {d.ret for d in ds if d.ret}
                agreed = next(iter(rets)) if len(rets) == 1 else None
                for d in ds:
                    d.ret = agreed

    def _targets(self, name: str, recv, jf: JFile, decl: Decl | None, bind: bool = True) -> list[Decl]:
        prev = self._how
        self._how = None
        try:
            return self._targets_body(name, recv, jf, decl, bind)
        finally:
            if not bind:
                self._how = prev

    def _targets_body(self, name: str, recv, jf: JFile, decl: Decl | None, bind: bool) -> list[Decl]:
        if recv is None:
            return self._unqualified(name, jf, decl, bind)
        ty, how = self._recv_type(recv, jf, decl)
        # A receiver whose type we know (a project class, or a library / primitive type) does not
        # fall through to a name match. That keeps an interface parameter on the interface method.
        if how in ("type", "chain", "local", "field", "this", "super") and ty:
            tc = self._class_of_type(ty, jf, decl)
            if tc is not None:
                r = self._member(tc, name)
                if r:
                    self._how = "this" if how == "this" else "receiver"
                    return self._uniq(r)
            if bind:
                self.st["calls_library_receiver"] += 1
            return []
        if how in ("field", "this", "super", "type", "chain"):
            if bind:
                self.st["calls_library_receiver"] += 1
            return []
        return self._by_unique_name(name, bind)

    def _unqualified(self, name: str, jf: JFile, decl: Decl | None, bind: bool = True) -> list[Decl]:
        for tc in self._enclosing(decl):
            r = self._member(tc, name)
            if r:
                self._how = "this"
                return self._uniq(r)
        cls_fq = jf.static_imports.get(name)
        if cls_fq:
            tc = self.classes.get(cls_fq) or self._class_of(cls_fq.split(".")[-1], jf, decl)
            if tc is not None:
                r = self._member(tc, name)
                if r:
                    self._how = "static-import"
                    return self._uniq(r)
        hits = []
        for fq in jf.static_star:
            tc = self.classes.get(fq)
            if tc is not None:
                hits += self._member(tc, name)
        hits = self._uniq(hits)
        if len(hits) == 1:
            self._how = "static-import"
            return hits
        if len(hits) > 1:
            self._how = "candidate"
            return hits[:MAX_CANDIDATES]
        fq = jf.imports.get(name)
        if fq and fq in self.classes and name[:1].isupper():
            return [self.classes[fq]]
        return self._by_unique_name(name, bind)

    def _by_unique_name(self, name: str, bind: bool = True) -> list[Decl]:
        if len(name) <= 3:
            return []
        cands = self._uniq([d for d in self.by_name.get(name, []) if d.kind == "method"])
        if not cands:
            return []
        if len(cands) > MAX_CANDIDATES:
            if bind:
                self.st["calls_too_ambiguous"] += 1
            return []
        self._how = "name" if len(cands) == 1 else "candidate"
        return cands

    def _recv_type(self, recv, jf: JFile, decl: Decl | None) -> tuple[str | None, str]:
        if recv is None:
            return None, "implicit"
        if recv.type == "this":
            tc = self.classes.get(decl.cls) if decl is not None and decl.cls else None
            return (tc.name, "this") if tc is not None else (None, "this")
        if recv.type == "super":
            tc = self.classes.get(decl.cls) if decl is not None and decl.cls else None
            return (tc.supers[0], "super") if tc is not None and tc.supers else (None, "super")
        if recv.type == "parenthesized_expression":
            inner = next((x for x in recv.children if x.is_named), None)
            return self._recv_type(inner, jf, decl) if inner is not None else (None, "expr")
        if recv.type == "identifier":
            name = self.t(recv)
            if decl is not None and name in decl.types:
                return decl.types[name], "local"
            tc = self.classes.get(decl.cls) if decl is not None and decl.cls else None
            if tc is not None:
                ft = self._field_type(tc, name)
                if ft:
                    return ft, "field"
            if name[:1].isupper() or self._class_of(name, jf, decl) is not None:
                return name, "type"
            return None, "name"
        if recv.type == "field_access":
            kids = [c for c in recv.children if c.type != "."]
            if not kids:
                return None, "expr"
            field_name = self.t(kids[-1]) if kids[-1].type == "identifier" else None
            obj = kids[0]
            if obj.type == "this":
                tc = self.classes.get(decl.cls) if decl is not None and decl.cls else None
                ft = self._field_type(tc, field_name) if tc is not None and field_name else None
                return (ft, "field") if ft else (None, "field")
            ty, _how = self._recv_type(obj, jf, decl)
            if ty and field_name:
                tc = self._class_of_type(ty, jf, decl)
                ft = self._field_type(tc, field_name) if tc is not None else None
                return (ft, "field") if ft else (None, "field")
            text = self.t(recv)
            last = text.rsplit(".", 1)[-1]
            if "." in text and last[:1].isupper():
                return text, "type"
            return None, "expr"
        if recv.type == "method_invocation":
            name, inner = self._call_parts(recv)
            if not name:
                return None, "expr"
            targets = self._targets(name, inner, jf, decl, bind=False)
            rets = list(dict.fromkeys(t.ret for t in targets if t.ret))
            if len(rets) == 1:
                return rets[0], "chain"
            return None, "expr"
        if recv.type == "object_creation_expression":
            ty = self._created_type(recv)
            return (ty, "chain") if ty else (None, "expr")
        if recv.type == "cast_expression":
            ty = self._decl_type(recv)
            return (ty, "chain") if ty else (None, "expr")
        return None, "expr"

    def _ref_class(self, recv: str, jf: JFile, decl: Decl | None) -> Decl | None:
        """The type a method reference is bound through: `Foo::bar`, `this::bar`, `p::bar`, `Foo::new`."""
        if recv == "this":
            return self.classes.get(decl.cls) if decl is not None and decl.cls else None
        if recv == "super":
            tc = self.classes.get(decl.cls) if decl is not None and decl.cls else None
            if tc is not None and tc.supers:
                return self._class_of_type(tc.supers[0], jf, decl)
            return None
        if decl is not None and recv in decl.types and not recv[:1].isupper():
            return self._class_of_type(decl.types[recv], jf, decl)
        return self._class_of_type(recv, jf, decl)

    def _enclosing(self, decl: Decl | None) -> list[Decl]:
        """The decl itself when it is a type, then each enclosing type (inner to outer)."""
        out = []
        seen = set()
        if decl is not None and decl.kind == "class":
            out.append(decl)
            seen.add(decl.fqn)
            c = self.classes.get(decl.cls) if decl.cls else None
        else:
            c = self.classes.get(decl.cls) if decl is not None and decl.cls else None
        while c is not None and c.fqn not in seen:
            seen.add(c.fqn)
            out.append(c)
            c = self.classes.get(c.cls) if c.cls else None
        return out

    def _class_of(self, short: str, jf: JFile, decl: Decl | None = None) -> Decl | None:
        for tc in self._enclosing(decl):
            nest = f"{tc.fqn}.{short}"
            if nest in self.classes:
                return self.classes[nest]
        fq = jf.imports.get(short)
        if fq and fq in self.classes:
            return self.classes[fq]
        if jf.package and f"{jf.package}.{short}" in self.classes:
            return self.classes[f"{jf.package}.{short}"]
        hits = [self.classes[f"{p}.{short}"] for p in jf.star if f"{p}.{short}" in self.classes]
        if len(hits) == 1:
            return hits[0]
        cands = self.class_short.get(short) or []
        return cands[0] if len(cands) == 1 else None

    def _class_of_type(self, text: str, jf: JFile, decl: Decl | None) -> Decl | None:
        text = text.split("<", 1)[0].strip()
        if text in self.classes:
            return self.classes[text]
        return self._class_of(text.split(".")[-1], jf, decl)

    def _member(self, cls: Decl | None, name: str, depth: int = 0) -> list[Decl]:
        if cls is None or depth > 8:
            return []
        hit = self.members.get(cls.fqn, {}).get(name)
        if hit:
            return hit
        for s in cls.supers:
            sc = self._super_decl(s, cls)
            if sc is not None and sc is not cls:
                r = self._member(sc, name, depth + 1)
                if r:
                    return r
        return []

    def _field_type(self, cls: Decl | None, name: str | None, depth: int = 0) -> str | None:
        if cls is None or not name or depth > 8:
            return None
        if name in self.fields.get(cls.fqn, {}):
            return self.fields[cls.fqn][name]
        for s in cls.supers:
            sc = self._super_decl(s, cls)
            if sc is not None:
                r = self._field_type(sc, name, depth + 1)
                if r:
                    return r
        return None

    def _super_decl(self, name: str, cls: Decl) -> Decl | None:
        short = name.split("<", 1)[0].split(".")[-1]
        if cls.cls and f"{cls.cls}.{short}" in self.classes:
            return self.classes[f"{cls.cls}.{short}"]
        jf = self._jf_by_rel.get(cls.file)
        if jf is None:
            return self.classes.get(name) or (self.class_short.get(short) or [None])[0]
        return self._class_of_type(name, jf, cls)

    def _hierarchy(self):
        for d in list(self.classes.values()):
            for s in d.supers:
                sc = self._super_decl(s, d)
                if sc is None or sc is d:
                    continue
                iface = (self.b.nodes.get(sc.id) is not None
                         and self.b.nodes[sc.id].attrs.get("java_kind") == "interface")
                kind = "IMPLEMENTS" if iface else "EXTENDS"
                self.b.add_edge(d.id, sc.id, kind, d.file, d.line, HEURISTIC)
                for nm, ms in self.members.get(d.fqn, {}).items():
                    if nm == "<init>":
                        continue
                    for base in self.members.get(sc.fqn, {}).get(nm, []):
                        for m in self._uniq(ms):
                            if m.id == base.id:
                                continue
                            ek = "IMPLEMENTED_BY" if iface or "abstract" in base.modifiers else "OVERRIDDEN_BY"
                            self.b.add_edge(base.id, m.id, ek, m.file, m.line, HEURISTIC)

    # ------------------------------------------------------------------ tree helpers
    def _annotations(self, n) -> tuple[list, set]:
        anns, mods = [], set()
        for c in n.children:
            if c.type != "modifiers":
                continue
            for m in c.children:
                if m.type in MODS:
                    mods.add(m.type)
                elif m.type in ("marker_annotation", "annotation"):
                    nm = next((self.t(x) for x in m.children if x.type in ("identifier", "scoped_identifier")), None)
                    if not nm:
                        continue
                    arg = None
                    stack = list(m.children)
                    while stack:
                        x = stack.pop(0)
                        if x.type == "string_literal":
                            raw = self.t(x)
                            arg = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'" else raw
                            break
                        stack[0:0] = list(x.children)
                    anns.append((nm.split(".")[-1], arg, self.t(m)))
        return anns, mods

    def _supers(self, n) -> list[str]:
        out = []
        for c in n.children:
            if c.type in ("superclass", "super_interfaces", "extends_interfaces"):
                out.extend(self._type_list(c))
        return out

    def _type_list(self, n) -> list[str]:
        out = []
        for c in n.children:
            if c.type == "type_list":
                out.extend(self._type_list(c))
            elif c.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                tx = self._type_text(c)
                if tx:
                    out.append(tx)
        return out

    def _type_text(self, n) -> str | None:
        if n is None:
            return None
        if n.type == "generic_type":
            base = next((c for c in n.children if c.type != "type_arguments"), None)
            return self._type_text(base)
        if n.type == "array_type":
            base = next((c for c in n.children if c.is_named), None)
            return self._type_text(base)
        if n.type in ("type_identifier", "scoped_type_identifier", "identifier", "integral_type",
                      "floating_point_type", "boolean_type", "void_type"):
            return self.t(n)
        return None

    def _decl_type(self, n) -> str | None:
        for c in n.children:
            if c.type in ("type_identifier", "scoped_type_identifier", "generic_type", "array_type",
                          "integral_type", "floating_point_type", "boolean_type", "void_type"):
                return self._type_text(c)
        return None

    def _created_type(self, n) -> str | None:
        for c in n.children:
            if c.type in ("type_identifier", "scoped_type_identifier", "generic_type", "array_type"):
                return self._type_text(c)
        return None

    def _name(self, n) -> str | None:
        if n is None:
            return None
        for c in n.children:
            if c.type == "identifier":
                return self.t(c)
        return None

    def _params(self, n) -> tuple[dict, dict]:
        out, anns = {}, {}
        for p in (n.children if n is not None else []):
            if p.type in ("formal_parameter", "spread_parameter"):
                nm = self._name(p)
                ty = self._decl_type(p)
                if nm and ty:
                    out[nm] = ty
                    pa, _ = self._annotations(p)
                    if pa:
                        anns[nm] = pa
        return out, anns

    def _call_strings(self, n) -> list[str]:
        """String literals that are direct arguments of this call (a nested call keeps its own)."""
        out = []
        for c in n.children:
            if c.type != "argument_list":
                continue
            stack = list(c.children)
            while stack:
                x = stack.pop(0)
                if x.type == "string_literal":
                    raw = self.t(x)
                    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                        out.append(raw[1:-1])
                elif x.type == "method_invocation":
                    continue
                else:
                    stack[0:0] = list(x.children)
        return out

    def _locals_into(self, fn, types: dict):
        stack = list(fn.children)
        while stack:
            n = stack.pop()
            if n.type in CLASSISH or n.type in ("method_declaration", "constructor_declaration",
                                                "compact_constructor_declaration"):
                continue
            if n.type == "local_variable_declaration":
                ty = self._decl_type(n)
                for d in n.children:
                    if d.type == "variable_declarator" and ty:
                        nm = self._name(d)
                        if nm:
                            types.setdefault(nm, ty)
            elif n.type == "enhanced_for_statement":
                ty = next((self._type_text(c) for c in n.children
                           if c.type in ("type_identifier", "scoped_type_identifier", "generic_type")), None)
                nm = next((self.t(c) for c in n.children if c.type == "identifier"), None)
                if nm and ty:
                    types.setdefault(nm, ty)
            elif n.type == "lambda_expression":
                params = next((c for c in n.children if c.type == "formal_parameters"), None)
                if params is not None:
                    for k, v in self._params(params)[0].items():
                        types.setdefault(k, v)
            stack.extend(n.children)

    @staticmethod
    def _uniq(decls: list[Decl]) -> list[Decl]:
        out, seen = [], set()
        for d in decls:
            if d.id not in seen:
                seen.add(d.id)
                out.append(d)
        return out


def _as_utf8(raw: bytes) -> tuple[bytes, bool]:
    try:
        raw.decode("utf-8")
        return raw, True
    except UnicodeDecodeError:
        return raw.decode("latin-1").encode("utf-8"), False

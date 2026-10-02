"""Kotlin language plugin (Android, Kotlin Multiplatform, Ktor, Spring).

Heuristic mode: a tree-sitter-kotlin syntax layer. Declarations (packages, classes, interfaces, objects, companion
objects, top-level / extension functions, methods) become nodes; calls are resolved by name: the enclosing class and
its supertypes, then a parameter / property type (`api.order()` with `api: OrdersApi`), imports, the same package, and
finally a project-wide unique name. Every resolved reference is labelled `heuristic`, as for Rust and C / C++ without
their indexers. An exact layer can be added with a scip-java index (`--scip`).

Framework facts read from the same syntax tree:
  HTTP clients    Retrofit interfaces (@GET("users/{id}") ...; base URL from Retrofit.Builder().baseUrl("...")),
                  Ktor client (client.get("...")), OkHttp Request.Builder().url("...") -> http:<METHOD> <path>
  servers         Ktor routing { route("/a") { get("/{id}") { } } } with authenticate("x") { } as a guard;
                  Spring @RestController / @RequestMapping / @GetMapping ... with @PreAuthorize / @Secured /
                  @RolesAllowed -> route:<METHOD> <uri>; @Scheduled (scheduled) and @KafkaListener / @RabbitListener /
                  @JmsListener / @EventListener (listener) entry points
  Android         AndroidManifest.xml activities (ui_page), services / receivers / providers (listener) and deep links
                  (<data scheme/host/path>) as pages; Worker / CoroutineWorker / JobService subclasses (queue_job);
                  Jetpack Compose Navigation composable("orders/{id}") / composable<OrderRoute> as pages and
                  navController.navigate(...) as NAVIGATES_TO
  Multiplatform   KMP source sets (androidMain, iosMain, jvmMain, jsMain ...) as platform conditions (#7 tags), and
                  `expect` declarations -> their `actual` implementations (IMPLEMENTED_BY)
Test code (src/test, src/androidTest, *Test source sets, *Test.kt) carries attrs.test; @Test functions are `test`
entries.
"""
from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ...core.fsutil import keep_file
from ...core.model import EXACT, HEURISTIC
from ...core.paths import rules as path_rules
from ...core.plugin import GraphBuilder, LanguagePlugin, Project
from ..native.ts import TreeSitterMissing

EXTS = (".kt", ".kts")
VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}
RETROFIT = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
SPRING_MAP = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
              "PatchMapping": "PATCH", "RequestMapping": None}
SPRING_GUARDS = {"PreAuthorize", "Secured", "RolesAllowed", "PostAuthorize"}
LISTENERS = {"KafkaListener", "RabbitListener", "JmsListener", "SqsListener", "EventListener", "StreamListener"}
WORKER_BASES = {"Worker", "CoroutineWorker", "ListenableWorker", "RxWorker", "JobService", "JobIntentService"}
LIFECYCLE = re.compile(r"^(on[A-Z]\w*|doWork|startWork|createWork|query|insert|update|delete|getType)$")
TEST_PATH = re.compile(r"(^|/)(src/(test|androidTest|testDebug|testRelease|\w+Test)/|tests?/)|(Test|Tests|Spec)\.kt$")
SOURCE_SET = re.compile(r"(?:^|/)src/(\w+?)Main/")
SET_PLATFORM = {"android": "android", "ios": "ios", "iosArm64": "ios", "iosX64": "ios", "iosSimulatorArm64": "ios",
                "apple": "ios", "macos": "macos", "macosArm64": "macos", "macosX64": "macos", "js": "web",
                "wasmJs": "web", "wasm": "web", "linux": "linux", "linuxX64": "linux", "mingw": "windows",
                "mingwX64": "windows"}
TEMPLATE = re.compile(r"\$\{([^{}]*)\}|\$([A-Za-z_]\w*)")


def parser():
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_kotlin as m
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise TreeSitterMissing(f"tree-sitter grammar for kotlin not installed ({e}); "
                                f"pip install tree-sitter tree-sitter-kotlin") from e
    return Parser(Language(m.language()))


def source_files(root: Path, project=None) -> list[str]:
    rules = path_rules(project, "kotlin")
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


def template(raw: str) -> str:
    """Kotlin string literal source -> URL template: "orders/$id" -> orders/{id}, "${base}/x" -> {base}/x."""
    s = raw
    if s.startswith('"""'):
        s = s[3:-3]
    elif s.startswith('"'):
        s = s[1:-1]

    def rep(m):
        expr = (m.group(1) or m.group(2) or "").strip()
        name = re.sub(r"[^\w.]", "", expr).split(".")[-1] or "?"
        return "{" + name + "}"
    return TEMPLATE.sub(rep, s)


def split_url(t: str) -> tuple[str | None, str]:
    """(origin, path) of a URL template; origin = scheme://host or a leading {placeholder}; query dropped."""
    t = t.split("?")[0].split("#")[0]
    m = re.match(r"^([a-zA-Z][\w+.-]*://[^/]*)(.*)$", t)
    origin = None
    if m:
        origin, t = m.group(1), m.group(2)
    else:
        m = re.match(r"^(\{[^{}/]*\})(/.*|)$", t)
        if m and not t.startswith("{/"):
            origin, t = m.group(1), m.group(2)
    if not t.startswith("/"):
        t = "/" + t
    return origin, t


def join_path(base: str, p: str) -> str:
    if not p:
        return base or "/"
    if p.startswith("/") and base:
        return base.rstrip("/") + p
    return (base.rstrip("/") + "/" + p) if base else ("/" + p.lstrip("/"))


@dataclass
class Decl:
    id: str
    kind: str
    name: str
    fqn: str
    file: str
    line: int
    end: int
    cls: str | None = None             # enclosing class fqn
    supers: list = field(default_factory=list)
    annotations: list = field(default_factory=list)   # (name, first string arg or None, raw text)
    modifiers: set = field(default_factory=set)
    receiver: str | None = None
    types: dict = field(default_factory=dict)          # param / property name -> type name
    test: bool = False


class KFile:
    def __init__(self, rel: str, src: bytes, tree):
        self.rel, self.src, self.tree = rel, src, tree
        self.package = ""
        self.imports: dict[str, str] = {}     # short name -> fqn
        self.star: list[str] = []
        self.test = bool(TEST_PATH.search(rel))
        m = SOURCE_SET.search(rel)
        self.source_set = m.group(1) if m else None


class KotlinPlugin(LanguagePlugin):
    name = "kotlin"

    def detect(self, project: Project) -> bool:
        self._files = source_files(project.root, project)
        return any(f.endswith(".kt") for f in self._files)

    def t(self, n) -> str:
        return self.cur.src[n.start_byte:n.end_byte].decode("utf-8", "replace")

    # ------------------------------------------------------------------ index
    def index(self, project: Project, builder: GraphBuilder, frameworks) -> dict:
        t0 = time.time()
        p = parser()
        self.b = builder
        files = getattr(self, "_files", None)
        if files is None:
            files = source_files(project.root, project)
        self.decls: dict[str, Decl] = {}
        self._fd = None
        self.by_name: dict[str, list[Decl]] = defaultdict(list)
        self.classes: dict[str, Decl] = {}
        self.class_short: dict[str, list[Decl]] = defaultdict(list)
        self.members: dict[str, dict[str, list[Decl]]] = defaultdict(lambda: defaultdict(list))
        self.calls: list[tuple] = []          # (owner_id, name, receiver, line, file obj, decl)
        self.http: list[dict] = []
        self.navs: list[tuple] = []
        self.base_urls: set[str] = set()
        self.st = defaultdict(int)
        kfiles, failed = [], []
        for rel in files:
            try:
                src = (project.root / rel).read_bytes()
            except OSError:
                failed.append(rel)
                continue
            tree = p.parse(src)
            kf = KFile(rel, src, tree)
            if tree.root_node.has_error:
                self.st["files_with_syntax_errors"] += 1
            kfiles.append(kf)
        for kf in kfiles:            # pass 1: declarations
            self.cur = kf
            self._header(kf)
            self._decls(kf.tree.root_node, kf, None, None)
        for kf in kfiles:            # pass 2: references and framework facts
            self.cur = kf
            fid = self._file_node(kf)
            self._refs(kf.tree.root_node, kf, fid, None, {"prefix": "", "guards": [], "routing": False})
            if kf.source_set and kf.source_set in SET_PLATFORM:
                self._mark_platform(kf)
        self._resolve_calls()
        self._hierarchy()
        self._emit_http()
        self._link_navs()
        self._manifests(project)
        self.file_report = {"seen": [kf.rel for kf in kfiles] + failed, "parse_failed": failed}
        st = dict(self.st)
        st.update({"mode": "heuristic", "files": len(kfiles), "declarations": len(self.decls),
                   "seconds": round(time.time() - t0, 2)})
        return st

    def _file_node(self, kf: KFile) -> str:
        return self.b.add_node("file", f"kotlin:{kf.rel}", name=kf.rel, file=kf.rel, line=1, lang="kotlin",
                               module=kf.package or None, attrs={"test": True} if kf.test else {})

    def _header(self, kf: KFile):
        for c in kf.tree.root_node.children:
            if c.type == "package_header":
                q = next((x for x in c.children if x.type == "qualified_identifier"), None)
                kf.package = self.t(q) if q else ""
            elif c.type == "import":
                q = next((x for x in c.children if x.type == "qualified_identifier"), None)
                if q is None:
                    continue
                fq = self.t(q)
                alias = next((self.t(x) for x in c.children if x.type == "identifier"), None)
                if self.t(c).rstrip().endswith("*"):
                    kf.star.append(fq)
                else:
                    kf.imports[alias or fq.rsplit(".", 1)[-1]] = fq
            elif c.type == "imports" or c.type == "import_list":
                for x in c.children:
                    if x.type == "import":
                        q = next((y for y in x.children if y.type == "qualified_identifier"), None)
                        if q is not None:
                            fq = self.t(q)
                            kf.imports[fq.rsplit(".", 1)[-1]] = fq

    # ------------------------------------------------------------------ pass 1
    def _annotations(self, n) -> tuple[list, set]:
        anns, mods = [], set()
        # tree-sitter-kotlin quirk: annotations right after the package header (no imports) parse as a sibling
        # annotated_expression in front of the declaration
        prev = n.prev_named_sibling
        if prev is not None and prev.type == "annotated_expression" and n.start_point[0] - prev.end_point[0] <= 1:
            for m in re.finditer(r'@([\w.]+)(\s*\((?:[^()]|\([^()]*\))*\))?', self.t(prev)):
                s = re.search(r'"((?:[^"\\]|\\.)*)"', m.group(2) or "")
                anns.append((m.group(1).split(".")[-1], s.group(1) if s else None, m.group(0)))
        for c in n.children:
            if c.type != "modifiers":
                continue
            for m in c.children:
                if m.type == "annotation":
                    txt = self.t(m)
                    mm = re.match(r"@(?:[\w]+:)?([\w.]+)", txt)
                    nm = mm.group(1).split(".")[-1] if mm else txt
                    s = re.search(r'"((?:[^"\\]|\\.)*)"', txt)
                    anns.append((nm, s.group(1) if s else None, txt))
                else:
                    mods.update(self.t(m).split())
        return anns, mods

    def _name(self, n) -> str | None:
        x = n.child_by_field_name("name")
        if x is None:
            x = next((c for c in n.children if c.type in ("identifier", "type_identifier", "simple_identifier")), None)
        return self.t(x) if x is not None else None

    def _decls(self, n, kf: KFile, cls: Decl | None, fn: Decl | None):
        for c in n.children:
            ty = c.type
            if ty in ("class_declaration", "object_declaration", "companion_object"):
                nm = self._name(c) or ("Companion" if ty == "companion_object" else None)
                if not nm:
                    self._decls(c, kf, cls, fn)
                    continue
                fq = f"{cls.fqn}.{nm}" if cls else (f"{kf.package}.{nm}" if kf.package else nm)
                anns, mods = self._annotations(c)
                head = self.t(c).split("{", 1)[0]
                kk = "interface" if re.search(r"\binterface\b", head) else ("object" if ty != "class_declaration" else
                     ("enum" if "enum" in mods else "class"))
                supers = []
                for d in c.children:
                    if d.type == "delegation_specifiers":
                        for ut in d.children:
                            m = re.match(r"\s*([\w.]+)", self.t(ut))
                            if m:
                                supers.append(m.group(1).split(".")[-1])
                key = fq + self._variant(kf, mods)
                types = {}
                for d in c.children:
                    if d.type == "primary_constructor":
                        types.update(self._params(d))
                dc = Decl(f"class:{key}", "class", nm, fq, kf.rel, c.start_point[0] + 1, c.end_point[0] + 1,
                          cls.fqn if cls else None, supers, anns, mods, types=types, test=kf.test)
                self._add_decl(dc, kk)
                body = next((d for d in c.children if d.type in ("class_body", "enum_class_body")), None)
                if body is not None:
                    for d in body.children:
                        if d.type == "property_declaration":
                            dc.types.update(self._prop_type(d))
                    self._decls(body, kf, dc, None)
            elif ty == "function_declaration":
                nm = self._name(c)
                if not nm:
                    continue
                anns, mods = self._annotations(c)
                recv = None
                for d in c.children:
                    if d.type == "identifier":
                        break
                    if d.type in ("user_type", "nullable_type"):
                        recv = self.t(d).split("<")[0].rstrip("?")
                owner = cls.fqn if cls else (kf.package or "")
                fq = f"{owner}.{nm}" if owner else nm
                kind = "method" if cls else "function"
                params = next((d for d in c.children if d.type == "function_value_parameters"), None)
                dc = Decl(f"{kind}:{fq}{self._variant(kf, mods)}", kind, nm, fq, kf.rel, c.start_point[0] + 1,
                          c.end_point[0] + 1, cls.fqn if cls else None, [], anns, mods, recv,
                          self._params(params) if params is not None else {}, kf.test)
                self._add_decl(dc, kind)
            elif ty in ("property_declaration",) and cls is None:
                pass
            else:
                self._decls(c, kf, cls, fn)

    def _variant(self, kf: KFile, mods: set) -> str:
        return f"@{kf.source_set}" if "actual" in mods and kf.source_set else ""

    def _params(self, n) -> dict:
        out = {}
        for p in (n.children if n is not None else []):
            if p.type in ("parameter", "class_parameter", "class_parameters", "function_value_parameter"):
                if p.type == "class_parameters":
                    out.update(self._params(p))
                    continue
                ids = [c for c in p.children if c.type == "identifier"]
                ty = next((c for c in p.children if c.type in ("user_type", "nullable_type")), None)
                if ids and ty is not None:
                    out[self.t(ids[0])] = self.t(ty).split("<")[0].rstrip("?").split(".")[-1]
        return out

    def _prop_type(self, d) -> dict:
        v = next((c for c in d.children if c.type == "variable_declaration"), None)
        if v is None:
            return {}
        ids = [c for c in v.children if c.type == "identifier"]
        ty = next((c for c in v.children if c.type in ("user_type", "nullable_type")), None)
        if ids and ty is not None:
            return {self.t(ids[0]): self.t(ty).split("<")[0].rstrip("?").split(".")[-1]}
        # val api = Retrofit...create(OrdersApi::class.java) / val repo = OrderRepository(...)
        txt = self.t(d)
        m = re.search(r"create\(\s*(\w+)::class", txt) or re.search(r"=\s*([A-Z]\w*)\s*\(", txt)
        if ids and m:
            return {self.t(ids[0]): m.group(1)}
        return {}

    def _add_decl(self, d: Decl, display_kind: str):
        attrs = {"kotlin_kind": display_kind}
        if d.test:
            attrs["test"] = True
        for a in ("expect", "actual", "suspend", "override", "abstract", "data", "sealed"):
            if a in d.modifiers:
                attrs[a] = True
        if d.receiver:
            attrs["receiver"] = d.receiver
        if d.annotations:
            attrs["annotations"] = [a[0] for a in d.annotations][:12]
        pkg = d.fqn.rsplit(".", 1)[0] if "." in d.fqn else ""
        self.b.add_node(d.kind, d.id.split(":", 1)[1], name=d.name, fqn=d.fqn, file=d.file, line=d.line, end_line=d.end,
                        module=pkg or None, lang="kotlin", attrs=attrs)
        if d.test and any(a[0] == "Test" for a in d.annotations):
            self.b.nodes[d.id].entry_kind = "test"
        self.decls[d.id] = d
        self.by_name[d.name].append(d)
        if d.kind == "class":
            self.classes.setdefault(d.fqn, d)
            self.class_short[d.name].append(d)
        if d.cls:
            self.members[d.cls][d.name].append(d)
            self.b.add_edge(f"class:{d.cls}", d.id, "CONTAINS", d.file, d.line, EXACT)
        self._decl_facts(d)

    def _decl_facts(self, d: Decl):
        names = {a[0] for a in d.annotations}
        if d.kind != "class":
            if "Scheduled" in names:
                self.b.nodes[d.id].entry_kind = "scheduled"
                self.st["scheduled"] += 1
            elif names & LISTENERS:
                self.b.nodes[d.id].entry_kind = "listener"
                self.st["listeners"] += 1

    # ------------------------------------------------------------------ pass 2
    def _owner_at(self, kf: KFile, line: int) -> Decl | None:
        best = None
        for d in self._file_decls(kf):
            if d.kind != "class" and d.line <= line <= d.end and (best is None or d.end - d.line < best.end - best.line):
                best = d
        return best

    def _file_decls(self, kf: KFile):
        fd = getattr(self, "_fd", None)
        if fd is None:
            fd = self._fd = defaultdict(list)
            for d in self.decls.values():
                fd[d.file].append(d)
        return fd.get(kf.rel, ())

    def _decl_at(self, kf: KFile, n, kinds) -> Decl | None:
        line = n.start_point[0] + 1
        for d in self._file_decls(kf):
            if d.line == line and d.kind in kinds and d.name == self._name(n):
                return d
        return None

    def _refs(self, n, kf: KFile, owner: str, decl: Decl | None, ctx: dict):
        for c in n.children:
            ty = c.type
            if ty == "function_declaration":
                d = self._decl_at(kf, c, ("function", "method"))
                if d is not None:
                    self._fn_facts(d)
                    rctx = {"prefix": "", "guards": [], "routing": (d.receiver or "").split(".")[-1] in ("Route", "Routing")}
                    self._refs(c, kf, d.id, d, rctx)
                    continue
            elif ty in ("class_declaration", "object_declaration", "companion_object"):
                d = self._decl_at(kf, c, ("class",)) if ty != "companion_object" else None
                self._refs(c, kf, d.id if d else owner, d or decl, ctx)
                continue
            elif ty == "call_expression":
                if self._call(c, kf, owner, decl, ctx):
                    continue
            elif ty == "binary_expression":
                m = re.match(r"composable\s*<\s*([\w.]+)\s*>", self.t(c))
                lam = next((x for x in c.children if x.type in ("lambda_literal", "annotated_lambda")), None)
                if m and lam is not None:
                    pid = self._page(kf, m.group(1).split(".")[-1], c, typed=True)
                    self._refs(lam, kf, pid, decl, ctx)
                    continue
            self._refs(c, kf, owner, decl, ctx)

    def _call(self, c, kf: KFile, owner: str, decl: Decl | None, ctx: dict) -> bool:
        """Record one call; returns True when it walked the children itself (route / page lambdas)."""
        line = c.start_point[0] + 1
        callee = c.children[0] if c.children else None
        lam = next((x for x in c.children if x.type == "annotated_lambda"), None)
        args = next((x for x in c.children if x.type == "value_arguments"), None)
        if callee is not None and callee.type == "call_expression" and lam is not None:   # f("x") { ... }
            args = next((x for x in callee.children if x.type == "value_arguments"), None)
            callee = callee.children[0] if callee.children else None
        if callee is None:
            return False
        recv, name = None, None
        if callee.type == "identifier":
            name = self.t(callee)
        elif callee.type == "navigation_expression":
            ids = callee.children
            name = self.t(ids[-1]) if ids and ids[-1].type == "identifier" else None
            recv = ids[0] if ids else None
        if not name:
            return False
        sarg = self._first_string(args)
        rtext = self.t(recv) if recv is not None else None
        # ---- Ktor server routing
        if lam is not None and recv is None:
            if name == "routing":
                self._refs(lam, kf, owner, decl, {"prefix": ctx["prefix"], "guards": ctx["guards"], "routing": True})
                return True
            if ctx.get("routing") and name == "route" and sarg is not None:
                self._refs(lam, kf, owner, decl, {**ctx, "prefix": join_path(ctx["prefix"], template(sarg))})
                return True
            if ctx.get("routing") and name == "authenticate":
                g = f"authenticate({template(sarg) if sarg else ''})"
                self._refs(lam, kf, owner, decl, {**ctx, "guards": ctx["guards"] + [g]})
                return True
            if ctx.get("routing") and name in VERBS:
                uri = join_path(ctx["prefix"], template(sarg) if sarg is not None else "")
                hid = self._ktor_route(kf, name.upper(), uri, c, owner, ctx)
                self._refs(lam, kf, hid, decl, {**ctx, "routing": False})
                return True
            if name == "composable" and sarg is not None:
                pid = self._page(kf, template(sarg), c)
                self._refs(lam, kf, pid, decl, ctx)
                return True
        # ---- HTTP clients
        if name in VERBS and recv is not None and re.search(r"(?i)client|http", rtext or "") and args is not None:
            first = self._first_arg(args)
            if first is not None and first.type == "string_literal":
                self.http.append({"src": owner, "method": name.upper(), "url": template(self.t(first)), "client": "ktor",
                                  "file": kf.rel, "line": line})
        if name == "url" and rtext and "Request.Builder" in rtext and sarg is not None:
            whole = c
            while whole.parent is not None and whole.parent.type in ("navigation_expression", "call_expression"):
                whole = whole.parent
            tail = self.t(whole)[len(self.t(c)):]
            m = re.search(r"\.(post|put|delete|patch|head)\s*\(", tail)
            self.http.append({"src": owner, "method": m.group(1).upper() if m else "GET", "url": template(self._raw_first(args)),
                              "client": "okhttp", "file": kf.rel, "line": line})
        if name == "baseUrl" and sarg is not None:
            self.base_urls.add(template(self._raw_first(args)))
        if name == "navigate" and args is not None:
            first = self._first_arg(args)
            if first is not None:
                if first.type == "string_literal":
                    self.navs.append((owner, template(self.t(first)), None, kf.rel, line))
                else:
                    m = re.match(r"([A-Z]\w*)", self.t(first))
                    if m:
                        self.navs.append((owner, None, m.group(1), kf.rel, line))
        self.calls.append((owner, name, rtext, line, kf, decl))
        return False

    def _first_arg(self, args):
        if args is None:
            return None
        for a in args.children:
            if a.type == "value_argument":
                for x in a.children:
                    if x.is_named:
                        return x
        return None

    def _first_string(self, args) -> str | None:
        x = self._first_arg(args)
        return self.t(x) if x is not None and x.type == "string_literal" else None

    def _raw_first(self, args) -> str:
        return self._first_string(args) or '""'

    # ------------------------------------------------------------------ framework facts
    def _fn_facts(self, d: Decl):
        anns = {a[0]: a for a in d.annotations}
        cls = self.classes.get(d.cls) if d.cls else None
        # Retrofit interface methods
        for nm, a in anns.items():
            if nm in RETROFIT and a[1] is not None:
                self.http.append({"src": d.id, "method": nm, "url": template('"' + a[1] + '"'), "client": "retrofit",
                                  "file": d.file, "line": d.line, "relative": True})
            elif nm == "HTTP":
                m = re.search(r'method\s*=\s*"(\w+)"', a[2])
                pm = re.search(r'path\s*=\s*"([^"]*)"', a[2])
                if m and pm:
                    self.http.append({"src": d.id, "method": m.group(1).upper(), "url": template('"' + pm.group(1) + '"'),
                                      "client": "retrofit", "file": d.file, "line": d.line, "relative": True})
        # Spring MVC / WebFlux annotations
        if cls is not None and any(nm in SPRING_MAP for nm in anns):
            canns = {a[0]: a for a in cls.annotations}
            if not ({"RestController", "Controller"} & set(canns)) and "RequestMapping" not in canns:
                return
            prefix = self._mapping_path(canns.get("RequestMapping"))
            guards = [self._guard(a) for nm2, a in canns.items() if nm2 in SPRING_GUARDS]
            guards += [self._guard(a) for nm2, a in anns.items() if nm2 in SPRING_GUARDS]
            for nm, a in anns.items():
                if nm not in SPRING_MAP:
                    continue
                verbs = [SPRING_MAP[nm]] if SPRING_MAP[nm] else (re.findall(r"RequestMethod\.(\w+)", a[2]) or ["ANY"])
                paths = self._mapping_paths(a)
                for v in verbs:
                    for p in paths:
                        uri = join_path(prefix or "", p) if (prefix or p) else "/"
                        self._route(v, uri, d.id, d.file, d.line, "spring", guards, EXACT)

    def _mapping_paths(self, a) -> list[str]:
        if a is None:
            return [""]
        inner = a[2][a[2].find("(") + 1:a[2].rfind(")")] if "(" in a[2] else ""
        m = re.search(r"(?:value|path)\s*=\s*(\[[^\]]*\]|arrayOf\([^)]*\)|\"[^\"]*\")", inner)
        part = m.group(1) if m else (inner if inner and not re.match(r"\s*\w+\s*=", inner) else "")
        ps = re.findall(r'"((?:[^"\\]|\\.)*)"', part)
        return ps or [""]

    def _mapping_path(self, a) -> str:
        return self._mapping_paths(a)[0] if a is not None else ""

    def _guard(self, a) -> str:
        return f"{a[0]}({a[1]})" if a[1] is not None else a[0]

    def _route(self, method: str, uri: str, handler: str | None, file: str, line: int, fw: str, guards: list,
               conf: str) -> str:
        uri = re.sub(r"\{(\w+)(?::[^{}]*)?\}", r"{\1}", uri)          # Spring {id:\d+} -> {id}
        if not uri.startswith("/"):
            uri = "/" + uri
        key = f"{method} {uri}"
        attrs = {"uri": uri, "method": method, "framework": fw}
        if guards:
            attrs["middleware"] = list(dict.fromkeys(guards))
        rid = self.b.add_node("route", key, name=key, file=file, line=line, lang="kotlin", entry_kind="http_route",
                              attrs=attrs)
        if self.b.nodes[rid].entry_kind is None:
            self.b.nodes[rid].entry_kind = "http_route"
        if handler:
            self.b.add_edge(rid, handler, "ROUTES_TO", file, line, conf)
            self.b.nodes[rid].attrs.setdefault("handler", self.b.nodes[handler].name if handler in self.b.nodes else handler)
        self.st["routes"] += 1
        self.st[f"routes_{fw}"] += 1
        return rid

    def _ktor_route(self, kf: KFile, method: str, uri: str, c, owner: str, ctx: dict) -> str:
        line = c.start_point[0] + 1
        hk = f"{kf.package + '.' if kf.package else ''}<{method} {uri}>@{kf.rel}:{line}"
        hid = self.b.add_node("function", hk, name=f"{method} {uri} handler", fqn=hk, file=kf.rel, line=line,
                              end_line=c.end_point[0] + 1, module=kf.package or None, lang="kotlin",
                              attrs={"lambda": True, "kotlin_kind": "route handler", **({"test": True} if kf.test else {})})
        self._route(method, uri, hid, kf.rel, line, "ktor", ctx["guards"], EXACT)
        self.b.add_edge(owner, f"route:{method} {re.sub(r'{(\w+)(?::[^{}]*)?}', r'{\1}', uri)}", "REFERENCES_FN", kf.rel,
                        line, EXACT, how="router registration")
        return hid

    def _page(self, kf: KFile, route: str, c, typed: bool = False) -> str:
        line = c.start_point[0] + 1
        key = f"kotlin:{route}"
        pid = self.b.add_node("page", key, name=route, fqn=key, file=kf.rel, line=line, end_line=c.end_point[0] + 1,
                              lang="kotlin", entry_kind="ui_page",
                              attrs={"route": route, "via": "compose-navigation" + (" (typed)" if typed else "")})
        if self.b.nodes[pid].entry_kind is None:
            self.b.nodes[pid].entry_kind = "ui_page"
        self.st["compose_pages"] += 1
        return pid

    def _mark_platform(self, kf: KFile):
        from ...platforms import Cond, _plat_atom, mark
        plat = SET_PLATFORM[kf.source_set]
        lines = kf.src.count(b"\n") + 1
        mark(self.b, kf.rel, 1, lines, Cond("tree", _plat_atom(plat), f"source set {kf.source_set}Main"))
        self.st["platform_source_set_files"] += 1

    # ------------------------------------------------------------------ resolution
    def _class_of(self, short: str, kf: KFile) -> Decl | None:
        fq = kf.imports.get(short)
        if fq and fq in self.classes:
            return self.classes[fq]
        if kf.package and f"{kf.package}.{short}" in self.classes:
            return self.classes[f"{kf.package}.{short}"]
        cands = self.class_short.get(short) or []
        return cands[0] if len(cands) == 1 else None

    def _member(self, cls: Decl, name: str, depth: int = 0) -> list[Decl]:
        hit = self.members.get(cls.fqn, {}).get(name)
        if hit:
            return hit
        if depth > 6:
            return []
        for s in cls.supers:
            sc = self.classes.get(s) or (self.class_short.get(s) or [None])[0]
            if sc is not None and sc is not cls:
                r = self._member(sc, name, depth + 1)
                if r:
                    return r
        return []

    def _resolve_calls(self):
        for owner, name, recv, line, kf, decl in self.calls:
            targets = self._targets(name, recv, kf, decl)
            if not targets:
                self.st["calls_unresolved"] += 1
                continue
            for t in targets[:3]:
                if t.kind == "class":
                    self.b.add_edge(owner, t.id, "INSTANTIATES", kf.rel, line, HEURISTIC)
                    ctor = [m for m in self.members.get(t.fqn, {}).get("init", [])]
                    self.b.add_edge(owner, t.id, "USES_TYPE", kf.rel, line, HEURISTIC, how="constructor call")
                    for m in ctor:
                        self.b.add_edge(owner, m.id, "CALLS", kf.rel, line, HEURISTIC)
                else:
                    self.b.add_edge(owner, t.id, "CALLS", kf.rel, line, HEURISTIC)
            self.st["calls_resolved"] += 1

    def _targets(self, name: str, recv: str | None, kf: KFile, decl: Decl | None) -> list[Decl]:
        cls = self.classes.get(decl.cls) if decl is not None and decl.cls else None
        if recv is None or recv == "this":
            if cls is not None:
                r = [d for d in self._member(cls, name) if d.kind != "class"]
                if r:
                    return r
            if decl is not None and decl.receiver:                         # extension function: members of its receiver
                rc = self._class_of(decl.receiver.split(".")[-1], kf)
                if rc is not None:
                    r = self._member(rc, name)
                    if r:
                        return r
            if name[:1].isupper():
                c = self._class_of(name, kf)
                if c is not None:
                    return [c]
            fq = kf.imports.get(name)
            if fq:
                r = [d for d in self.by_name.get(name, []) if d.fqn == fq and d.kind == "function"]
                if r:
                    return r
            same = [d for d in self.by_name.get(name, []) if d.kind == "function" and d.fqn == f"{kf.package}.{name}"
                    and "actual" not in d.modifiers]
            if same:
                return same
            cands = [d for d in self.by_name.get(name, []) if d.kind == "function" and "actual" not in d.modifiers]
            return cands if len(cands) == 1 else []
        rname = re.sub(r"[?!]", "", recv).split(".")[-1].strip()
        tyname = None
        if decl is not None:
            tyname = decl.types.get(rname)
        if tyname is None and cls is not None:
            tyname = self._field_type(cls, rname)
        if tyname is None and rname[:1].isupper():
            tyname = rname
        if tyname:
            tc = self._class_of(tyname, kf)
            if tc is not None:
                r = self._member(tc, name)
                if not r:
                    comp = self.classes.get(f"{tc.fqn}.Companion")
                    r = self._member(comp, name) if comp else []
                if r:
                    return r
                return []
        cands = [d for d in self.by_name.get(name, []) if d.kind == "method" and "actual" not in d.modifiers]
        return cands if len(cands) == 1 and len(name) > 3 else []

    def _field_type(self, cls: Decl, name: str, depth=0):
        if name in cls.types:
            return cls.types[name]
        if cls.cls and depth < 4 and cls.cls in self.classes:
            return self._field_type(self.classes[cls.cls], name, depth + 1)
        return None

    def _hierarchy(self):
        for d in list(self.decls.values()):
            if d.kind != "class":
                continue
            for s in d.supers:
                sc = self.classes.get(s) or (self.class_short.get(s) or [None])[0]
                if sc is None:
                    if s in WORKER_BASES:
                        self._entry_class(d, "queue_job", f"{s} subclass")
                    continue
                kind = "IMPLEMENTS" if self.b.nodes[sc.id].attrs.get("kotlin_kind") == "interface" else "EXTENDS"
                self.b.add_edge(d.id, sc.id, kind, d.file, d.line, HEURISTIC)
                for nm, ms in self.members.get(d.fqn, {}).items():
                    for base in self.members.get(sc.fqn, {}).get(nm, []):
                        for m in ms:
                            ek = "IMPLEMENTED_BY" if kind == "IMPLEMENTS" or "abstract" in base.modifiers else "OVERRIDDEN_BY"
                            self.b.add_edge(base.id, m.id, ek, m.file, m.line, HEURISTIC)
        # expect -> actual
        for d in list(self.decls.values()):
            if "actual" in d.modifiers:
                base = self.decls.get(d.id.split("@")[0])
                if base is not None and "expect" in base.modifiers:
                    self.b.add_edge(base.id, d.id, "IMPLEMENTED_BY", d.file, d.line, EXACT, how="expect/actual")
                    self.st["expect_actual"] += 1

    def _entry_class(self, d: Decl, kind: str, why: str):
        n = self.b.nodes[d.id]
        n.entry_kind = n.entry_kind or kind
        n.attrs["entry_reason"] = why
        for nm, ms in self.members.get(d.fqn, {}).items():
            if LIFECYCLE.match(nm):
                for m in ms:
                    self.b.add_edge(d.id, m.id, "REFERENCES_FN", m.file, m.line, EXACT, how="framework lifecycle")
        self.st[f"entries_{kind}"] += 1

    def _emit_http(self):
        bases = sorted(self.base_urls)
        base = bases[0] if len(bases) == 1 else None
        for r in self.http:
            url = r["url"]
            if r.get("relative") and base and not re.match(r"^[a-zA-Z][\w+.-]*://", url):
                bo, bp = split_url(base)
                origin, path = bo, (join_path(bp, url) if not url.startswith("/") else url)
            else:
                origin, path = split_url(url)
            okind = "api" if origin is None or (base and origin == split_url(base)[0]) else (
                "unknown" if origin.startswith("{") else "other")
            key = f"{r['method']} {path}" if okind in ("api", "unknown") else f"{r['method']} {origin}{path}"
            nid = self.b.add_node("http", key, key, fqn=key, lang="kotlin",
                                  attrs={"method": r["method"], "path": path, "client": r["client"], "origin": origin,
                                         "origin_kind": okind})
            self.b.add_edge(r["src"], nid, "HTTP_CALLS", r["file"], r["line"], HEURISTIC if okind != "api" else EXACT,
                            client=r["client"], url=url, origin=origin)
            self.st[f"http_{r['client']}"] += 1

    def _link_navs(self):
        from ...link import match_path
        pages = [(nid, n.attrs.get("route")) for nid, n in self.b.nodes.items() if n.kind == "page" and n.lang == "kotlin"]
        for owner, route, typ, file, line in self.navs:
            hits = []
            for nid, pr in pages:
                if typ is not None:
                    if pr == typ:
                        hits.append(nid)
                elif route is not None and pr is not None:
                    ok, _ = match_path("/" + route.split("?")[0].lstrip("/"), "/" + pr.split("?")[0].lstrip("/"))
                    if ok:
                        hits.append(nid)
            for h in hits[:2]:
                self.b.add_edge(owner, h, "NAVIGATES_TO", file, line, EXACT if len(hits) == 1 else HEURISTIC)
                self.st["navigations"] += 1

    # ------------------------------------------------------------------ AndroidManifest.xml
    def _manifests(self, project: Project):
        ns = "{http://schemas.android.com/apk/res/android}"
        rules = path_rules(project, "kotlin")
        for dp, dn, fn in os.walk(project.root):
            rd = os.path.relpath(dp, project.root).replace(os.sep, "/")
            rd = "" if rd == "." else rd
            dn[:] = rules.prune(rd, dn, dot=True)
            if "AndroidManifest.xml" not in fn:
                continue
            rel = f"{rd}/AndroidManifest.xml" if rd else "AndroidManifest.xml"
            if TEST_PATH.search(rel):
                continue
            try:
                root = ET.parse(os.path.join(dp, "AndroidManifest.xml")).getroot()
            except (ET.ParseError, OSError):
                continue
            pkg = root.get("package") or self._gradle_namespace(Path(dp))
            app = root.find("application")
            if app is None:
                continue
            for kind_tag, entry in (("activity", "ui_page"), ("activity-alias", "ui_page"), ("service", "listener"),
                                    ("receiver", "listener"), ("provider", "listener")):
                for el in app.findall(kind_tag):
                    nm = el.get(ns + "name") or el.get(ns + "targetActivity")
                    if not nm:
                        continue
                    fq = (pkg + nm) if nm.startswith(".") and pkg else (nm if "." in nm else f"{pkg}.{nm}" if pkg else nm)
                    d = self.classes.get(fq) or ((self.class_short.get(fq.split(".")[-1]) or [None])
                                                  if len(self.class_short.get(fq.split(".")[-1]) or []) == 1 else [None])[0]
                    if d is None:
                        self.st["manifest_unresolved"] += 1
                        continue
                    self._entry_class(d, entry, f"AndroidManifest <{kind_tag}>")
                    self.b.nodes[d.id].attrs["android_component"] = kind_tag
                    for data in el.iter("data"):
                        scheme, host = data.get(ns + "scheme"), data.get(ns + "host")
                        path = data.get(ns + "path") or data.get(ns + "pathPrefix") or data.get(ns + "pathPattern") or ""
                        if not scheme and not host:
                            continue
                        route = f"{scheme or '*'}://{host or '*'}{path}"
                        pid = self.b.add_node("page", f"kotlin:deeplink:{route}", name=route, file=rel, lang="kotlin",
                                              entry_kind="ui_page", attrs={"route": route, "via": "android deep link",
                                                                           "activity": d.fqn})
                        self.b.add_edge(pid, d.id, "ROUTES_TO", rel, None, EXACT)
                        self.st["deep_links"] += 1

    def _gradle_namespace(self, d: Path) -> str | None:
        for up in (d, d.parent, d.parent.parent, d.parent.parent.parent):
            for g in ("build.gradle.kts", "build.gradle"):
                f = up / g
                if f.is_file():
                    m = re.search(r"namespace\s*=?\s*[\"']([\w.]+)[\"']", f.read_text(errors="replace"))
                    if m:
                        return m.group(1)
        return None

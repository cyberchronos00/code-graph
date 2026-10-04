"""Kotlin language plugin (Android, Kotlin Multiplatform, Ktor, Spring).

Heuristic mode: a tree-sitter-kotlin syntax layer. Declarations (packages, classes, interfaces, objects, companion
objects, top-level / extension functions, methods) become nodes; calls are resolved by name: the enclosing class and
its supertypes, then a parameter / property type (`api.order()` with `api: OrdersApi`), imports, the same package, and
finally the name alone (one method: `binding: name`; two to five: a `candidate` edge to each). Every resolved reference is labelled `heuristic`, as for Rust and C / C++ without
their indexers. Exact mode (exact.py): a scip-java index of the Gradle / Maven build (`--scip`,
CODEGRAPH_KOTLIN_SCIP_FILE, or an opted-in scip-java run with CODEGRAPH_KOTLIN_SCIP=1) replaces the call edges with
compiler-resolved ones; without a JDK / scip-java the heuristic layer stays, and coverage says why.

Framework facts read from the same syntax tree:
  HTTP clients    Retrofit interfaces (@GET("users/{id}") ...; base URL from Retrofit.Builder().baseUrl("...")),
                  per-interface / BuildConfig base URLs, Ktor client (client.get("..."), client.get { url("...") },
                  client.request { method = HttpMethod.Post }), OkHttp Request.Builder().url("...") -> http:<METHOD> <path>
  servers         Ktor routing { route("/a") { get("/{id}") { } } } and type-safe resources get<Res> { } with
                  authenticate("x") { } as a guard; SecurityFilterChain requestMatchers(...).hasRole(...) as guards;
                  Spring @RestController / @RequestMapping / @GetMapping ... with @PreAuthorize / @Secured /
                  @RolesAllowed -> route:<METHOD> <uri>; @Scheduled (scheduled) and @KafkaListener / @RabbitListener /
                  @JmsListener / @EventListener (listener) entry points
  Android         AndroidManifest.xml activities (ui_page), services / receivers / providers (listener) and deep links
                  (<data scheme/host/path>) as pages; Worker / CoroutineWorker / JobService subclasses (queue_job);
                  Jetpack Compose Navigation composable("orders/{id}") / composable<OrderRoute>(...) { } / Navigation 3
                  entry<Key> { } as pages and
                  navController.navigate(...) as NAVIGATES_TO
  Multiplatform   KMP source sets (androidMain, iosMain, jvmMain, jsMain ...) as platform conditions (#7 tags), and
                  `expect` declarations -> their `actual` implementations (IMPLEMENTED_BY)
  tables          Spring Data repositories (JpaRepository<Entity, ID> ...) and Exposed table objects -> READS_TABLE /
                  WRITES_TABLE
Test code (src/test, src/androidTest, *Test source sets, *Test.kt) carries attrs.test; @Test / @ParameterizedTest /
@RepeatedTest / @TestFactory functions are `test` entries with attrs.framework (junit5, junit4, kotlin-test, testng, kotest)
from the file's imports; they count as test cases in `cg tests`.
"""
from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ...core.syntax_errors import merge, tree_spans
from .reparse import reparse_members
from ...core.fsutil import keep_file
from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...core.paths import rules as path_rules
from ...core.plugin import GraphBuilder, LanguagePlugin, Project
from ..native.ts import TreeSitterMissing

EXTS = (".kt", ".kts")
# an unknown receiver whose method name is declared on more classes than this gets no candidate edges (#83 item 6)
MAX_CANDIDATES = 5
VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}
RETROFIT = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
SPRING_MAP = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
              "PatchMapping": "PATCH", "RequestMapping": None}
# Spring Data repository interfaces (`interface OwnerRepository : JpaRepository<Owner, Int>`) and Exposed tables
# (`object Users : IntIdTable("users")`): calls on them read / write the table
REPO_SUPERS = r"(?:Jpa|Crud|ListCrud|PagingAndSorting|ListPagingAndSorting|CoroutineCrud|CoroutineSorting|ReactiveCrud|" \
              r"ReactiveSorting|R2dbc|Mongo|ReactiveMongo|Kotlin)?Repository"
EXPOSED_TABLES = r"(?:Table|IdTable|IntIdTable|LongIdTable|UUIDTable|UIntIdTable|ULongIdTable|CompositeIdTable)"
DATA_WRITE = re.compile(r"^(save|delete|remove|insert|update|upsert|batchInsert|batchUpsert|replace|persist|merge|"
                        r"flush|truncate)")
DATA_READ = re.compile(r"^(find|get|read|query|search|stream|count|exists|select|selectAll|all|slice|fetch|load)")
# Spring Security SecurityFilterChain rules: requestMatchers("/admin/**").hasRole("ADMIN") / Kotlin DSL authorize(...)
SEC_AUTH = r"(hasRole|hasAnyRole|hasAuthority|hasAnyAuthority|authenticated|fullyAuthenticated|permitAll|denyAll|access)"
SPRING_GUARDS = {"PreAuthorize", "Secured", "RolesAllowed", "PostAuthorize"}
LISTENERS = {"KafkaListener", "RabbitListener", "JmsListener", "SqsListener", "EventListener", "StreamListener"}
WORKER_BASES = {"Worker", "CoroutineWorker", "ListenableWorker", "RxWorker", "JobService", "JobIntentService"}
LIFECYCLE = re.compile(r"^(on[A-Z]\w*|doWork|startWork|createWork|query|insert|update|delete|getType)$")
TEST_ANNOTATIONS = {"Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate"}
TEST_FRAMEWORKS = (("org.junit.jupiter.", "junit5"), ("org.junit.", "junit4"), ("kotlin.test.", "kotlin-test"),
                   ("org.testng.", "testng"), ("io.kotest.", "kotest"))
TEST_PATH = re.compile(r"(^|/)(src/(test|androidTest|testDebug|testRelease|\w+Test)/|tests?/)|(Test|Tests|Spec)\.kt$")
# `iosMain`, and the test sets built for one platform (`iosTest`, `androidUnitTest`, `androidInstrumentedTest`, #86)
SOURCE_SET = re.compile(r"(?:^|/)src/((\w+?)(?:Main|Test|UnitTest|InstrumentedTest))/")
SET_PLATFORM = {"android": "android", "androidHost": "android", "androidDevice": "android", "ios": "ios",
                "iosArm64": "ios", "iosX64": "ios", "iosSimulatorArm64": "ios",
                "apple": "ios", "macos": "macos", "macosArm64": "macos", "macosX64": "macos", "js": "web",
                "wasmJs": "web", "wasm": "web", "linux": "linux", "linuxX64": "linux", "mingw": "windows",
                "mingwX64": "windows"}
TEMPLATE = re.compile(r"\$\{([^{}]*)\}|\$([A-Za-z_]\w*)")
# Ktor path parameters `{id}` / `{id:regex}` normalized to `{id}` for route keys
_KTOR_PARAM_RE = re.compile(r"\{(\w+)(?::[^{}]*)?\}")


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
    name_line: int | None = None                        # line of the name identifier (SCIP definitions sit there)


def _module_root(rel: str) -> str:
    """The Gradle module directory of a source file: the path before its `src/` directory."""
    i = rel.find("/src/")
    return "" if rel.startswith("src/") or i < 0 else rel[:i]



# tree-sitter-kotlin 1.1.0 cannot parse a suspend lambda used as an expression (`val b = suspend { 1 }`,
# `X to suspend { ... }`): the error swallows the declarations around it (#81). `suspend` becomes seven spaces in
# front of the `{`, the same length, so byte offsets and the names read from the original source stay as written.
_SUSPEND_LAMBDA = re.compile(rb"(=|\(|,|\[|\bto|\breturn|->|&&|\|\||\?:)(\s*)suspend(\s*\{)")


# A suspend lambda that starts a statement (`suspend { ... }.runCatchingUpdatingState(state)`) errors the same way;
# blanked, the lambda would become the trailing lambda of the previous line's call, so `suspend` becomes `;` and six
# spaces (#104). Not after a line that continues into it (an operator, `=`, `(`, `,`, `.`).
_SUSPEND_STMT = re.compile(rb"(?m)^([ \t]*)suspend([ \t]*\{)")
_CONTINUES = (b"=", b"(", b",", b"[", b".", b"->", b"&&", b"||", b"?:", b"+", b"-", b"*", b"/", b"to", b"return")


def _suspend_lambdas(src: bytes) -> tuple[bytes, int]:
    if b"suspend" not in src:
        return src, 0
    out, n = _SUSPEND_LAMBDA.subn(lambda m: m.group(1) + m.group(2) + b" " * 7 + m.group(3), src)

    def stmt(m):
        nonlocal n
        before = out[:m.start()].rstrip()
        prev = before[before.rfind(b"\n") + 1:].split(b"//")[0].rstrip()
        if prev.endswith(_CONTINUES):
            return m.group(0)
        n += 1
        return m.group(1) + b";" + b" " * 6 + m.group(2)
    if b"suspend" in out:
        out = _SUSPEND_STMT.sub(stmt, out)
    return out, n


# A statement line starting with `get` / `set` right after a `val` / `var` (Ktor routing: `val x = ...` then
# `get("/path") { }`) parses as that property's accessor and the error swallows the whole function (#104). A getter
# or setter takes `()` / `(value)`, never a string, a lambda or type arguments, so for `get("...")`, `get { }` and
# `get<T>` the whitespace character in front of `get` / `set` becomes `;`. That ends the declaration and keeps every
# byte offset.
_ROUTE_ACCESSOR = re.compile(rb"([ \t]*)[ \t]((?:get|set)\s*(?:\(\s*(?:\"|\"\"\")|\{|<))")
_DECL_LINE = re.compile(rb"^\s*(?:(?:private|internal|public|protected|const|lateinit|override|open)\s+)*(?:val|var)\s")


def _route_calls(src: bytes) -> tuple[bytes, int]:
    if b"get" not in src and b"set" not in src:
        return src, 0
    lines = src.split(b"\n")
    n, prev = 0, b""
    for i, ln in enumerate(lines):
        m = _ROUTE_ACCESSOR.match(ln)
        # only after a complete declaration line: `val h = X().apply {` + `set("k", v)` is a call inside the lambda
        if m and _DECL_LINE.match(prev) and not prev.rstrip().endswith((b"{", b"(", b"[", b",", b"=", b"->", b".")):
            lines[i] = m.group(1) + b";" + ln[m.end(1) + 1:]
            n += 1
        t = ln.strip()
        if t and not t.startswith((b"//", b"/*", b"*", b"@")):
            prev = ln
    return (b"\n".join(lines), n) if n else (src, 0)


# `dynamic` is the Kotlin/JS type keyword in the grammar, so a call or declaration named `dynamic` (`fun
# Route.dynamic()`, `dynamic()` in a Ktor module) is a parse error. Its last letter is upper-cased in the parsed copy
# only; names come from the original bytes (#104).
_DYNAMIC_CALL = re.compile(rb"(?<![\w$])(dynami)c(?=\s*\()")


def _keyword_calls(src: bytes) -> tuple[bytes, int]:
    if b"dynamic" not in src:
        return src, 0
    return _DYNAMIC_CALL.subn(rb"\1C", src)


class KFile:
    def __init__(self, rel: str, src: bytes, tree):
        self.rel, self.src, self.tree = rel, src, tree
        self.package = ""
        self.imports: dict[str, str] = {}     # short name -> fqn
        self.star: list[str] = []
        self.test = bool(TEST_PATH.search(rel))
        m = SOURCE_SET.search(rel)
        self.source_set = m.group(2) if m else None
        self.source_set_dir = m.group(1) if m else None


# mutating calls on a stored MutableList / MutableSet / MutableMap / StateFlow field: `items.add(x)` writes `items`
KOTLIN_MUTATING = frozenset({
    "add", "addAll", "remove", "removeAll", "removeAt", "removeIf", "removeFirst", "removeLast", "retainAll", "clear",
    "put", "putAll", "set", "getOrPut", "compute", "computeIfAbsent", "merge", "sort", "sortBy", "sortWith",
    "sortByDescending", "shuffle", "reverse", "fill", "update"})


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
        self.test_fw: dict[str, str | None] = {}
        self._fd = None
        self.by_name: dict[str, list[Decl]] = defaultdict(list)
        self.classes: dict[str, Decl] = {}
        self.class_short: dict[str, list[Decl]] = defaultdict(list)
        self.members: dict[str, dict[str, list[Decl]]] = defaultdict(lambda: defaultdict(list))
        self.calls: list[tuple] = []          # (owner_id, name, receiver, line, file obj, decl)
        self.call_branch: dict = {}           # (owner_id, line, name) -> {branch, branch_line} (#88)
        # properties with a custom accessor, `by lazy` or a delegate are nodes (#89); kept out of by_name / members so
        # a call `x()` never binds to one
        self.props: dict[str, dict] = {}                                   # decl id -> {"property", "accessors"}
        self.props_by_name: dict[str, list[Decl]] = defaultdict(list)
        self.prop_members: dict[str, dict[str, list[Decl]]] = defaultdict(lambda: defaultdict(list))
        self.prop_at: dict[tuple, Decl] = {}                               # (file, start byte) -> decl
        self.stored_names: set[str] = set()                                # plain stored properties / ctor vals
        # stored properties of a class / enum are `field:<Type>.<name>` nodes with READS_PROP / WRITES_PROP (#88)
        self.fields: dict[str, dict[str, str]] = defaultdict(dict)        # class fqn -> name -> field id
        self.field_names: set[str] = set()
        self.copies: list[tuple] = []         # (owner, receiver (type, text), [(arg name, line)], kf, decl): `x.copy(a = 1)`
        self.frefs: list[tuple] = []          # (owner_id, name, receiver (type, text) | None, line, file, decl, write)
        self.reads: list[tuple] = []          # (owner_id, name, receiver node, line, file obj, decl, read | write)
        self.http: list[dict] = []
        self.navs: list[tuple] = []
        self.base_urls: set[str] = set()
        self.api_base: dict[str, set] = defaultdict(set)    # Retrofit interface short name -> base URLs it is built with
        self.build_config = self._build_config(project.root, files)
        self.st = defaultdict(int)
        self.values: dict[str, dict[str, str]] = defaultdict(dict)   # owner fqn / pkg:<package> -> name -> id
        self.value_fq: dict[str, str] = {}                           # fqn -> id (imports name them)
        self.const_str: dict[str, tuple] = {}     # constant id -> (string literal source, KFile, owner Decl) (#67)
        kfiles, failed = [], []
        errs: dict[str, list] = {}
        for rel in files:
            try:
                src = (project.root / rel).read_bytes()
            except OSError:
                failed.append(rel)
                continue
            psrc, n_susp = _suspend_lambdas(src)
            if n_susp:
                self.st["suspend_lambdas_rewritten"] += n_susp
                self.st["files_with_suspend_lambdas"] += 1
            psrc, n_route = _route_calls(psrc)
            if n_route:
                self.st["accessor_like_calls_rewritten"] += n_route
            psrc, n_kw = _keyword_calls(psrc)
            if n_kw:
                self.st["keyword_named_calls_rewritten"] += n_kw
            tree = p.parse(psrc)
            dropped: list = []
            if tree.root_node.has_error:
                # #104: parse member by member; a member that still errors is blanked, the rest keep their nodes
                nt, dropped, _ = reparse_members(p, psrc, tree)
                if nt is not None:
                    tree = nt
                    self.st["files_reparsed_by_member"] += 1
                    self.st["members_dropped_by_reparse"] += len(dropped)
            kf = KFile(rel, src, tree)            # names and text from the original bytes; offsets are unchanged
            if tree.root_node.has_error or dropped:
                self.st["files_with_syntax_errors"] += 1
                errs[rel] = merge(tree_spans(tree.root_node) + [list(x) for x in dropped])
            kfiles.append(kf)
        for kf in kfiles:            # pass 1: declarations
            self.cur = kf
            self._header(kf)
            if kf.test:
                self.test_fw[kf.rel] = self._test_framework(kf)
            self._decls(kf.tree.root_node, kf, None, None)
        self._data_models(kfiles)
        for kf in kfiles:            # pass 2: references and framework facts
            self.cur = kf
            fid = self._file_node(kf)
            self._refs(kf.tree.root_node, kf, fid, None, {"prefix": "", "guards": [], "routing": False})
            if kf.source_set and kf.source_set in SET_PLATFORM:
                self._mark_platform(kf)
        self._resolve_calls()
        self._hierarchy()
        self._security_rules(kfiles)
        self._emit_http()
        self._link_navs()
        self._manifests(project)
        self.file_report = {"seen": [kf.rel for kf in kfiles] + failed, "parse_failed": failed, "syntax_errors": errs}
        mode = self._exact(project, files)
        st = dict(self.st)
        st.update({"mode": mode, "files": len(kfiles), "kt_files": sum(1 for kf in kfiles if kf.rel.endswith(".kt")),
                   "declarations": len(self.decls),
                   "seconds": round(time.time() - t0, 2)})
        return st

    def _exact(self, project: Project, files: list[str]) -> str:
        """The scip-java layer when an index is available (codegraph/plugins/kotlin/exact.py), else heuristic."""
        from .exact import ExactLayer, find_index, skipped_modules
        t1 = time.time()
        path, info = find_index(project, [f for f in files if f.endswith(EXTS)])
        mode = "heuristic"
        if path is not None:
            sst: dict = {}
            try:
                layer = ExactLayer(self)
                if layer.apply(path, sst):
                    mode = "scip"
                    sk = skipped_modules(info.get("android_modules"), layer.doc_paths)
                    if sk:
                        info["skipped_modules"] = sk
                else:
                    info["status"] = "the SCIP index has no Kotlin documents"
            except Exception as e:          # a corrupt / foreign index must not lose the heuristic graph
                info["status"] = f"SCIP import failed ({type(e).__name__}: {e})"
            for k in ("exact_vs_heuristic", "scip_documents", "scip_files", "scip_defs_matched", "scip_defs_unmatched",
                      "scip_references", "scip_refs_external", "java"):
                if k in sst:
                    self.st[k] = sst[k]
            if sst.get("scip_warning"):
                info["warning"] = sst["scip_warning"]
        info["seconds"] = round(time.time() - t1, 2)
        self.st["scip"] = info
        return mode

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

    def _test_framework(self, kf: KFile) -> str | None:
        """The test framework a test file's `@Test` comes from: the explicit `Test` import, else a star import, else
        the first framework import in the file (JUnit 5 > JUnit 4 > kotlin.test > TestNG)."""
        fq = kf.imports.get("Test") or kf.imports.get("ParameterizedTest") or ""
        cands = [fq] if fq else []
        cands += [f + ".*" for f in kf.star]
        cands += list(kf.imports.values())
        for c in cands:
            for prefix, fw in TEST_FRAMEWORKS:
                if c.startswith(prefix):
                    return fw
        return None

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

    def _name_node(self, n):
        x = n.child_by_field_name("name")
        if x is None:
            x = next((c for c in n.children if c.type in ("identifier", "type_identifier", "simple_identifier")), None)
        return x

    def _name(self, n) -> str | None:
        x = self._name_node(n)
        return self.t(x) if x is not None else None

    def _name_line(self, n) -> int:
        x = self._name_node(n)
        return (x if x is not None else n).start_point[0] + 1

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
                          cls.fqn if cls else None, supers, anns, mods, types=types, test=kf.test,
                          name_line=self._name_line(c))
                self._add_decl(dc, kk)
                body = next((d for d in c.children if d.type in ("class_body", "enum_class_body")), None)
                if kk in ("class", "enum"):
                    for d in c.children:
                        if d.type == "primary_constructor":
                            for cp in d.children:
                                for q in (cp.children if cp.type == "class_parameters" else []):
                                    kw = next((self.t(x) for x in q.children if x.type in ("val", "var")), None)
                                    ids = [x for x in q.children if x.type == "identifier"]
                                    if q.type == "class_parameter" and kw and ids:
                                        self._field(dc, self.t(ids[0]), kw, kf, q)
                if body is not None:
                    for d in body.children:
                        if d.type == "property_declaration":
                            dc.types.update(self._prop_type(d))
                            if not self._prop_kind(d):
                                nm_ = self._name(next((x for x in d.children if x.type == "variable_declaration"), d))
                                if nm_:
                                    self.stored_names.add(nm_)
                                    kw = next((self.t(x).split()[-1] for x in d.children if x.type == "binding_pattern_kind"
                                               or self.t(x) in ("val", "var")), "val")
                                    if kk in ("class", "enum") and not self._is_constant(d, dc):
                                        self._field(dc, nm_, kw, kf, d)
                    self._decls(body, kf, dc, None)
                self.stored_names.update(types)
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
                          self._params(params) if params is not None else {}, kf.test, self._name_line(c))
                self._local_types(c, dc.types)
                self._add_decl(dc, kind)
            elif ty == "enum_entry" and cls is not None:
                nm = self._name(c)
                if nm:
                    self._value(cls, "enum_case", nm, kf, c)
                body = next((d for d in c.children if d.type == "class_body"), None)
                if body is not None:                 # `GREEN { override fun label() = "g" }`: the enum's members
                    self._decls(body, kf, cls, fn)
            elif ty == "property_declaration" and fn is None and self._prop_kind(c):
                self._prop_decl(c, kf, cls)
            elif ty == "property_declaration" and fn is None and self._is_constant(c, cls):
                nm = self._name(next((d for d in c.children if d.type == "variable_declaration"), c))
                if nm:
                    nid = self._value(cls, "constant", nm, kf, c)
                    eq = next((i for i, x in enumerate(c.children) if x.type == "="), None)
                    init = c.children[eq + 1] if eq is not None and eq + 1 < len(c.children) else None
                    if init is not None and init.type == "string_literal":
                        self.const_str[nid] = (self.t(init), kf, cls)
            elif ty in ("property_declaration",) and cls is None:
                pass
            else:
                self._decls(c, kf, cls, fn)

    def _field(self, cls: Decl, nm: str, kw: str, kf: KFile, n):
        """A stored property of a class (`var items = ...`, a constructor `val owner`): `field:<Type>.<name>` (#88)."""
        if nm in self.fields[cls.fqn]:
            return
        a = {"property": "stored", "binding": "var" if kw.endswith("var") else "val"}
        if kf.test:
            a["test"] = True
        fid = self.b.add_node("field", f"{cls.fqn}.{nm}", name=nm, fqn=f"{cls.fqn}.{nm}", file=kf.rel,
                              line=n.start_point[0] + 1, end_line=n.end_point[0] + 1,
                              module=getattr(self.b.nodes.get(cls.id), "module", None), lang="kotlin", attrs=a)
        self.b.add_edge(cls.id, fid, "CONTAINS", kf.rel, n.start_point[0] + 1, EXACT)
        self.fields[cls.fqn][nm] = fid
        self.field_names.add(nm)
        self.st["stored_property_nodes"] += 1

    def _prop_kind(self, c) -> tuple | None:
        """(property kind, accessors) of a property that runs code when read or written (#89): a custom `get()` /
        `set(value)` (custom), `by lazy { }` (lazy) or another delegate (delegated); None for a stored property."""
        acc = []
        for x in c.children:
            if x.type == "getter":
                acc.append("get")
            elif x.type == "setter":
                acc.append("set")
            elif x.type == "property_delegate":
                acc.append("lazy" if re.match(r"by\s+lazy\b", self.t(x)) else "delegate")
        if not acc:
            return None
        kind = "custom" if ("get" in acc or "set" in acc) else ("lazy" if "lazy" in acc else "delegated")
        return kind, acc

    def _prop_decl(self, c, kf: KFile, cls: Decl | None):
        """`method:<Type>.<name>` (in a class), `function:<package>.<name>` (top level; an extension property keeps
        its receiver, as extension functions do), kotlin_kind property."""
        v = next((x for x in c.children if x.type == "variable_declaration"), None)
        nm = self._name(v) if v is not None else None
        if not nm:
            return
        kind, acc = self._prop_kind(c)
        recv = None
        for d in c.children:
            if d.type == "variable_declaration":
                break
            if d.type in ("user_type", "nullable_type"):
                recv = self.t(d).split("<")[0].rstrip("?")
        anns, mods = self._annotations(c)
        owner = cls.fqn if cls else (kf.package or "")
        fq = f"{owner}.{nm}" if owner else nm
        k = "method" if cls else "function"
        types = {}
        ty = next((x for x in v.children if x.type in ("user_type", "nullable_type")), None)
        dc = Decl(f"{k}:{fq}{self._variant(kf, mods)}", k, nm, fq, kf.rel, c.start_point[0] + 1, c.end_point[0] + 1,
                  cls.fqn if cls else None, [], anns, mods, recv, types, kf.test, self._name_line(v))
        if dc.id in self.decls:
            return
        self.props[dc.id] = {"property": kind, "accessors": acc,
                             "type": self.t(ty).split("<")[0].rstrip("?").split(".")[-1] if ty is not None else None}
        self._add_decl(dc, "property", prop=True)
        self.b.nodes[dc.id].attrs.update(property=kind, accessors=acc)
        self.props_by_name[nm].append(dc)
        if dc.cls:
            self.prop_members[dc.cls][nm].append(dc)
        self.prop_at[(kf.rel, c.start_byte)] = dc
        self.st["property_nodes"] += 1

    def _is_constant(self, c, cls: Decl | None) -> bool:
        """`const val` anywhere; a `val` / `var` of an `object` or `companion object`; a file-level `val` (#84). No
        custom getter (that is computed each read)."""
        if any(x.type == "getter" for x in c.children) or re.search(r"\bget\s*\(", self.t(c).split("=", 1)[0]):
            return False
        _anns, mods = self._annotations(c)
        if "const" in mods:
            return True
        if cls is None:
            vb = next((x for x in c.children if x.type in ("val", "var") or self.t(x) in ("val", "var")), None)
            return vb is not None and self.t(vb) == "val" and any(x.type == "=" for x in c.children)
        return self.b.nodes.get(cls.id) is not None and \
            self.b.nodes[cls.id].attrs.get("kotlin_kind") == "object"

    def _value(self, cls: Decl | None, kind: str, nm: str, kf: KFile, n):
        """An enum entry (`enum_case:<Enum>.<NAME>`) or a constant (`constant:<Type>.<NAME>`; a companion object's
        is its class's, as code names it: `K.A`; `constant:<package>.<NAME>` at file level): the node USES_VALUE
        edges point at (#84). Not a call target."""
        owner = cls
        if cls is not None and cls.name == "Companion" and cls.cls:
            owner = self.classes.get(cls.cls) or cls
        ofq = owner.fqn if owner is not None else (kf.package or "")
        fq = f"{ofq}.{nm}" if ofq else nm
        key = f"{fq}@{cls.id.split('@', 1)[1]}" if cls is not None and "@" in cls.id else fq
        line = n.start_point[0] + 1
        nid = self.b.add_node(kind, key, name=nm, fqn=fq, file=kf.rel, line=line, end_line=n.end_point[0] + 1,
                              module=(fq.rsplit(".", 1)[0] if "." in fq else None), lang="kotlin",
                              attrs={"test": True} if kf.test else {})
        if cls is not None:
            self.b.add_edge(cls.id, nid, "CONTAINS", kf.rel, line, EXACT)
        self.values[owner.fqn if owner is not None else f"pkg:{kf.package}"].setdefault(nm, nid)
        self.value_fq.setdefault(fq, nid)
        return nid

    def _const_id(self, expr: str, kf: KFile, cls: Decl | None) -> str | None:
        """The constant `NAME` / `Obj.NAME` / `a.b.Obj.NAME` names, seen from a class (its own and its companion's
        constants) in file kf: imports, then the file's package."""
        expr = expr.strip()
        if not re.fullmatch(r"[A-Za-z_][\w.]*", expr):
            return None
        if "." in expr:
            tgt, nm = expr.rsplit(".", 1)
            t = self.classes.get(tgt) or (self._class_of(tgt, kf) if "." not in tgt and tgt[:1].isupper() else None)
            if t is not None:
                if t.name == "Companion" and t.cls:
                    t = self.classes.get(t.cls) or t
                return self.values.get(t.fqn, {}).get(nm)
            return self.value_fq.get(expr)
        c = cls
        while c is not None:                       # own constants, then those of the enclosing classes
            if c.name == "Companion" and c.cls:
                c = self.classes.get(c.cls) or c
            vid = self.values.get(c.fqn, {}).get(expr)
            if vid is not None:
                return vid
            c = self.classes.get(c.cls) if c.cls else None
        fq = kf.imports.get(expr)
        if fq:
            return self.value_fq.get(fq)
        return self.values.get(f"pkg:{kf.package}", {}).get(expr)

    def _expand(self, raw: str, kf: KFile, cls: Decl | None, depth: int = 0) -> str:
        """A string literal's source with the `$NAME` / `${Obj.NAME}` of string constants put in (#67:
        `const val TASKS_ROUTE = "$TASKS_SCREEN?$ARG={$ARG}"`); other templates stay for template()."""
        def rep(m):
            expr = m.group(1) if m.group(1) is not None else m.group(2)
            vid = self._const_id(expr, kf, cls) if depth < 4 else None
            got = self.const_str.get(vid) if vid else None
            if got is None:
                return m.group(0)
            body = self._expand(got[0], got[1], got[2], depth + 1)
            return body[3:-3] if body.startswith('"""') else body[1:-1]
        return TEMPLATE.sub(rep, raw)

    def _route_arg(self, args, kf: KFile, decl: Decl | None, named: str | None = None) -> str | None:
        """The route string of `composable(...)` / `navigate(...)`: a string literal (constants in it expanded) or a
        string constant (`Destinations.TASKS_ROUTE`), positional or `route = ...`."""
        if args is None:
            return None
        x = None
        for a in args.children:
            if a.type != "value_argument":
                continue
            kids = [k for k in a.children if k.is_named]
            if a.children and any(k.type == "=" for k in a.children):
                if named and kids and self.t(kids[0]) == named and len(kids) > 1:
                    x = kids[-1]
                    break
                continue
            if x is None and kids:
                x = kids[0]
                if not named:
                    break
        if x is None:
            return None
        cls = self.classes.get(decl.cls) if decl is not None and decl.kind != "class" and decl.cls else \
            (decl if decl is not None and decl.kind == "class" else None)
        if x.type == "string_literal":
            return template(self._expand(self.t(x), kf, cls))
        if x.type in ("identifier", "simple_identifier", "navigation_expression"):
            vid = self._const_id(self.t(x), kf, cls)
            got = self.const_str.get(vid) if vid else None
            if got is not None:
                self.st["routes_from_constants"] += 1
                return template(self._expand(got[0], got[1], got[2]))
        return None

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

    def _local_types(self, fn, types: dict):
        """`val repo = OrderRepository(..)` / `val api: OrdersApi = ..` in a function body (not in nested classes or
        functions): the local's type, so `repo.save()` binds through it (#96). Parameters win; flow-insensitive."""
        body = next((c for c in fn.children if c.type == "function_body"), None)
        stack = list(body.children) if body is not None else []
        while stack:
            n = stack.pop()
            if n.type in ("class_declaration", "object_declaration", "function_declaration"):
                continue
            if n.type == "property_declaration":
                for k, v in self._prop_type(n).items():
                    types.setdefault(k, v)
            stack.extend(n.children)

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
        m = re.search(r"create\(\s*(\w+)::class", txt) or re.match(r"[^=]*=(?!=)\s*([A-Z]\w*)\s*\(", txt) \
            or re.search(r"\bby\s+lazy\s*(?:\([^)]*\))?\s*\{\s*([A-Z]\w*)\s*\(", txt) \
            or re.match(r"[^=]*=(?!=)\s*(?:mockk|spyk|mock|spy)\s*<\s*([A-Z]\w*)", txt)   # = mockk<Repo> { .. }
        if ids and m:
            return {self.t(ids[0]): m.group(1)}
        return {}

    def _add_decl(self, d: Decl, display_kind: str, prop: bool = False):
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
        if d.test and d.kind in ("function", "method") and any(a[0] in TEST_ANNOTATIONS for a in d.annotations):
            n = self.b.nodes[d.id]
            n.entry_kind = "test"
            fw = self.test_fw.get(d.file)
            if fw:
                n.attrs["framework"] = fw
            if any(a[0] == "ParameterizedTest" for a in d.annotations):
                n.attrs["parameterized"] = True
        self.decls[d.id] = d
        if prop:
            if d.cls:
                self.b.add_edge(f"class:{d.cls}", d.id, "CONTAINS", d.file, d.line, EXACT)
            return
        self.by_name[d.name].append(d)
        if d.kind == "class":
            if "expect" in d.modifiers:
                self.classes[d.fqn] = d     # common code names the `expect` class, not the actual of whichever source
            else:                           # set happened to be read first (#86)
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
            if self.values and ty in ("navigation_expression", "identifier"):
                self._value_ref(c, kf, owner, decl)
            if (self.props_by_name or self.field_names) and ty in ("navigation_expression", "identifier"):
                self._prop_ref(c, kf, owner, decl)
            if ty == "property_declaration" and (kf.rel, c.start_byte) in self.prop_at:
                d = self.prop_at[(kf.rel, c.start_byte)]
                self._refs(c, kf, d.id, d, {"prefix": "", "guards": [], "routing": False})
                continue
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
                self._copy_call(c, kf, owner, decl)
                if self._call(c, kf, owner, decl, ctx):
                    continue
            elif ty == "binary_expression":
                m = re.match(r"(composable|entry|dialog|(?:get|post|put|delete|patch|head|options))\s*<\s*([\w.]+)\s*>",
                             self.t(c))
                lam = next((x for x in c.children if x.type in ("lambda_literal", "annotated_lambda")), None)
                if m and lam is not None and self._typed_block(m.group(1), m.group(2), c, lam, kf, owner, decl, ctx):
                    continue
            self._refs(c, kf, owner, decl, ctx)

    def _call(self, c, kf: KFile, owner: str, decl: Decl | None, ctx: dict) -> bool:
        """Record one call; returns True when it walked the children itself (route / page lambdas)."""
        line = c.start_point[0] + 1
        callee = c.children[0] if c.children else None
        lam = next((x for x in c.children if x.type == "annotated_lambda"), None)
        args = next((x for x in c.children if x.type == "value_arguments"), None)
        targs = None
        if callee is not None and callee.type == "call_expression" and lam is not None:   # f("x") { ... }
            args = next((x for x in callee.children if x.type == "value_arguments"), None)
            targs = next((x for x in callee.children if x.type == "type_arguments"), None)
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
            route = self._route_arg(args, kf, decl, "route") if name in ("composable", "dialog") and targs is None else None
            if route is not None:
                pid = self._page(kf, route, c)
                self._refs(lam, kf, pid, decl, ctx)
                return True
            # composable<Route>(deepLinks = ...) { }, Navigation 3 entry<Key>(metadata = ...) { }, Ktor get<Res>(...) { }
            if targs is not None and self._typed_block(name, self.t(targs).strip("<> "), c, lam, kf, owner, decl, ctx):
                return True
        # ---- HTTP clients
        if (name in VERBS or name == "request") and recv is not None and re.search(r"(?i)client|http", rtext or ""):
            first = self._first_arg(args)
            if first is not None and first.type == "string_literal":
                self.http.append({"src": owner, "method": "GET" if name == "request" else name.upper(),
                                  "url": template(self.t(first)), "client": "ktor", "file": kf.rel, "line": line})
            elif first is None and lam is not None:
                # builder block: client.get { url("...") } / client.request { method = HttpMethod.Post; url { path("...") } }
                body = self.t(lam)
                mu = re.search(r'\burl\s*\(\s*("(?:[^"\\]|\\.)*")', body) or \
                    re.search(r'\b(?:path|encodedPath\s*=|appendPathSegments)\s*\(?\s*("(?:[^"\\]|\\.)*")', body)
                mm = re.search(r"\bmethod\s*=\s*HttpMethod\.(\w+)", body)
                if mu:
                    verb = mm.group(1).upper() if mm else ("GET" if name == "request" else name.upper())
                    self.http.append({"src": owner, "method": verb, "url": template(mu.group(1)), "client": "ktor",
                                      "file": kf.rel, "line": line})
        if name == "url" and rtext and "Request.Builder" in rtext and sarg is not None:
            whole = c
            while whole.parent is not None and whole.parent.type in ("navigation_expression", "call_expression"):
                whole = whole.parent
            tail = self.t(whole)[len(self.t(c)):]
            m = re.search(r"\.(post|put|delete|patch|head)\s*\(", tail)
            self.http.append({"src": owner, "method": m.group(1).upper() if m else "GET", "url": template(self._raw_first(args)),
                              "client": "okhttp", "file": kf.rel, "line": line})
        if name == "baseUrl" and args is not None:
            first = self._first_arg(args)
            raw = sarg
            if raw is None and first is not None:
                bc = re.fullmatch(r"(?:[\w.]+\.)?BuildConfig\.(\w+)", self.t(first).strip())
                vals = self.build_config.get(bc.group(1)) if bc else None
                raw = f'"{vals[0]}"' if vals and len(vals) == 1 else None
            if raw is not None:
                base = template(raw)
                self.base_urls.add(base)
                whole = c                  # Retrofit.Builder().baseUrl(X)...build().create(Api::class.java)
                while whole.parent is not None and whole.parent.type in ("navigation_expression", "call_expression",
                                                                         "value_argument", "value_arguments"):
                    whole = whole.parent
                for api in re.findall(r"create\s*\(\s*(\w+)::class", self.t(whole)) + \
                        re.findall(r"create<\s*(\w+)\s*>", self.t(whole)):
                    self.api_base[api].add(base)
        if name == "navigate" and args is not None:
            first = self._first_arg(args)
            route = self._route_arg(args, kf, decl, "route") if first is not None else None
            if first is not None:
                if route is not None:
                    self.navs.append((owner, route, None, kf.rel, line))
                else:
                    m = re.match(r"([A-Z]\w*)", self.t(first))
                    if m:
                        self.navs.append((owner, None, m.group(1), kf.rel, line))
        self.calls.append((owner, name, rtext, line, kf, decl, self._accessor(c) if owner in self.props else None))
        if name[:1].isupper():                      # a constructor or a composable: its branch (#88)
            br = self._branch(c)
            if br:
                self.call_branch[(owner, line, name)] = br
        return False

    def _branch(self, c) -> dict:
        """The innermost `if` / `else` / `when` entry around a construction or a composable call inside its function
        (#88): attrs.branch (`if (loading)`, `else of if (loading)`, `when Tab.Home`, `else of when (tab)`) and
        attrs.branch_line, so a screen that swaps content under a condition shows it on the edge."""
        x, prev = c.parent, c
        while x is not None and x.type not in ("function_declaration", "class_body", "source_file",
                                                "secondary_constructor", "anonymous_initializer", "getter", "setter"):
            if x.type == "when_entry":
                ch = x.children
                if ch and ch[0].type == "else":
                    w = x.parent
                    subj = next((k for k in w.children if k.type == "when_subject"), None) if w is not None else None
                    lab = "else of when" + (f" {self.t(subj)}" if subj is not None else "")
                else:
                    conds = []
                    for k in ch:
                        if k.type == "->":
                            break
                        if k.type != ",":
                            conds.append(self.t(k))
                    lab = "when " + ", ".join(conds)
                if prev is not ch[0]:
                    return {"branch": re.sub(r"\s+", " ", lab)[:80], "branch_line": x.start_point[0] + 1}
            if x.type == "if_expression":
                ch = x.children
                lp = next((i for i, k in enumerate(ch) if k.type == "("), None)
                rp = next((i for i, k in enumerate(ch) if k.type == ")"), None)
                cond = " ".join(self.t(k) for k in ch[lp + 1:rp]) if lp is not None and rp is not None else ""
                els = next((k for k in ch if k.type == "else"), None)
                inside = rp is not None and prev.start_byte > ch[rp].start_byte
                if inside:
                    lab = ("else of " if els is not None and prev.start_byte > els.start_byte else "") + f"if ({cond})"
                    return {"branch": re.sub(r"\s+", " ", lab)[:80], "branch_line": x.start_point[0] + 1}
            prev, x = x, x.parent
        return {}

    @staticmethod
    def _accessor(c) -> str | None:
        """get / set / lazy / delegate: the part of a property node a call sits in (#89)."""
        a = c.parent
        while a is not None and a.type not in ("property_declaration", "function_declaration", "class_body"):
            if a.type == "getter":
                return "get"
            if a.type == "setter":
                return "set"
            if a.type == "property_delegate":
                return "lazy" if a.children and re.match(r"by\s+lazy\b", a.text.decode("utf-8", "replace")) else "delegate"
            a = a.parent
        return "init" if a is not None and a.type == "property_declaration" else None

    def _prop_ref(self, c, kf: KFile, owner: str, decl: Decl | None):
        """A read (or write) of a property node: `cart.label`, `summary`, `3.asPrice`, `cart.items = x` (#89)."""
        p, pre = c.parent, False
        if c.type == "navigation_expression":
            kids = [x for x in c.children if x.type != "."]
            if len(kids) != 2 or kids[1].type != "identifier":
                return
            nm, recv = self.t(kids[1]), kids[0]
            if (recv.type == "unary_expression" and len(recv.children) == 2
                    and recv.children[0].type in ("++", "--")):
                pre, recv = True, recv.children[1]   # `--c.n` parses as `(--c).n`
        else:
            if p is None or p.type in ("variable_declaration", "parameter", "class_parameter", "function_value_parameter",
                                       "import", "qualified_identifier", "package_header", "user_type", "type_identifier",
                                       "enum_entry", "function_declaration", "class_declaration", "object_declaration",
                                       "lambda_parameters", "annotation", "constructor_invocation", "label",
                                       "callable_reference"):
                return
            if p.type == "navigation_expression" and p.children and p.children[0] != c:
                return
            if p.type == "value_argument" and c.next_sibling is not None and c.next_sibling.type == "=":
                return
            nm, recv = self.t(c), None
        if nm not in self.props_by_name and nm not in self.field_names:
            return
        if p is not None and p.type == "call_expression" and p.children and p.children[0] == c:
            return                                   # `x.label()`: a call, not a property read
        mode = "read"
        if p is not None and p.type == "assignment" and p.children and p.children[0] == c:
            # `x = v` writes; `x += v` / `x -= v` run the getter and the setter (#105)
            mode = "write" if len(p.children) > 1 and p.children[1].type == "=" else "read_write"
        elif pre or p is not None and p.type == "unary_expression" and any(x.type in ("++", "--") for x in p.children):
            mode = "read_write"                      # `count++` / `--cart.count`
        rv = (recv.type, self.t(recv)) if recv is not None else None     # text now: self.t reads the current file
        if nm in self.props_by_name:
            self.reads.append((owner, nm, rv, c.start_point[0] + 1, kf, decl, mode))
        if nm in self.field_names:
            if mode == "read_write":                 # a stored property read and written in place (#105)
                self.frefs.append((owner, nm, rv, c.start_point[0] + 1, kf, decl, False))
            w = mode != "read"
            if not w and p is not None and p.type == "navigation_expression" and p.children and p.children[0] == c:
                ids = [x for x in p.children if x.type == "identifier"]
                gp = p.parent
                if (len(ids) >= 1 and ids[-1] is not c and self.t(ids[-1]) in KOTLIN_MUTATING and gp is not None
                        and gp.type == "call_expression" and gp.children and gp.children[0] == p):
                    w = "mutating"                   # `items.add(x)` on a MutableList / Set / Map field
                elif (len(ids) >= 1 and ids[-1] is not c and self.t(ids[-1]) == "value" and gp is not None
                        and gp.type == "assignment" and gp.children and gp.children[0] == p):
                    w = "value"                      # `_state.value = x` on a MutableStateFlow / LiveData field
            self.frefs.append((owner, nm, rv, c.start_point[0] + 1, kf, decl, w))

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
                                  "file": d.file, "line": d.line, "relative": True,
                                  "iface": (d.cls or "").split(".")[-1]})
            elif nm == "HTTP":
                m = re.search(r'method\s*=\s*"(\w+)"', a[2])
                pm = re.search(r'path\s*=\s*"([^"]*)"', a[2])
                if m and pm:
                    self.http.append({"src": d.id, "method": m.group(1).upper(), "url": template('"' + pm.group(1) + '"'),
                                      "client": "retrofit", "file": d.file, "line": d.line, "relative": True,
                                      "iface": (d.cls or "").split(".")[-1]})
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
        norm_uri = _KTOR_PARAM_RE.sub(r"{\1}", uri)
        self.b.add_edge(owner, f"route:{method} {norm_uri}", "REFERENCES_FN", kf.rel,
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
        mark(self.b, kf.rel, 1, lines, Cond("tree", _plat_atom(plat), f"source set {kf.source_set_dir}"))
        self.st["platform_source_set_files"] += 1

    # ------------------------------------------------------------------ resolution
    def _value_ref(self, c, kf: KFile, owner: str, decl: Decl | None):
        """USES_VALUE to an enum entry or constant named for certain (#84): `Color.RED`, `K.A` (a companion's),
        `a.b.MAX`, or a bare `RED` / `MAX` that is imported, of the enclosing class or of the file's package, and not
        a local or parameter of that name."""
        line = c.start_point[0] + 1
        if c.type == "navigation_expression":
            kids = [x for x in c.children if x.type != "."]
            if len(kids) != 2 or kids[1].type != "identifier":
                return
            tgt, nm = self.t(kids[0]), self.t(kids[1])
            vid = None
            if re.fullmatch(r"[A-Za-z_][\w.]*", tgt):
                t = self.classes.get(tgt) or (self._class_of(tgt, kf) if "." not in tgt and tgt[:1].isupper() else None)
                if t is not None:
                    if t.name == "Companion" and t.cls:
                        t = self.classes.get(t.cls) or t
                    vid = self.values.get(t.fqn, {}).get(nm)
                elif "." in tgt:
                    vid = self.value_fq.get(f"{tgt}.{nm}")
            if vid is not None and vid != owner:
                self.b.add_edge(owner, vid, "USES_VALUE", kf.rel, line, EXACT, how="member")
                self.st["value_refs"] += 1
            return
        p = c.parent
        if p is None or p.type in ("variable_declaration", "parameter", "class_parameter", "function_value_parameter",
                                   "import", "qualified_identifier", "package_header", "user_type", "type_identifier",
                                   "enum_entry", "function_declaration", "class_declaration", "object_declaration",
                                   "lambda_parameters", "annotation", "constructor_invocation", "label"):
            return
        if p.type == "navigation_expression" and p.children and p.children[0] != c:
            return
        if p.type == "call_expression" and p.children and p.children[0] == c:
            return
        if p.type == "value_argument" and c.next_sibling is not None and c.next_sibling.type == "=":
            return
        nm = self.t(c)
        cls = self.classes.get(decl.cls) if decl is not None and decl.kind != "class" and decl.cls else \
            (decl if decl is not None and decl.kind == "class" else None)
        if cls is not None and cls.name == "Companion" and cls.cls:
            cls = self.classes.get(cls.cls) or cls
        vid = self.values.get(cls.fqn, {}).get(nm) if cls is not None else None
        how = "own type"
        if vid is None:
            fq = kf.imports.get(nm)
            vid, how = (self.value_fq.get(fq), "import") if fq else (None, how)
        if vid is None and not kf.imports.get(nm):
            vid, how = self.values.get(f"pkg:{kf.package}", {}).get(nm), "package"
            vn = self.b.nodes.get(vid) if vid else None
            if vn is not None and _module_root(vn.file) != _module_root(kf.rel):
                vid = None                   # the same package in another Gradle module is not visible
        if vid is None or vid == owner or self._shadowed(decl, nm, kf, line):
            return
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", nm) and self._in_lambda(c):
            return                           # `testApplication { client }`: a receiver's member may be meant
        self.b.add_edge(owner, vid, "USES_VALUE", kf.rel, line, EXACT, how=how)
        self.st["value_refs"] += 1

    @staticmethod
    def _in_lambda(c) -> bool:
        a = c.parent
        while a is not None and a.type not in ("function_declaration", "class_body", "source_file"):
            if a.type in ("lambda_literal", "annotated_lambda"):
                return True
            a = a.parent
        return False

    def _shadowed(self, decl: Decl | None, nm: str, kf: KFile, line: int) -> bool:
        """A parameter, local `val` / `var`, lambda parameter or destructured name `nm` in the function around `line`."""
        if decl is None or decl.kind == "class":
            return False
        if nm in decl.types:
            return True
        lines = kf.src.decode("utf-8", "replace").split("\n")[decl.line - 1:decl.end]
        return bool(re.search(rf"(?:\b(?:val|var)\s+(?:\([^)]*)?|[(,]\s*|\{{\s*(?:[\w\s,]*,\s*)?){re.escape(nm)}\b\s*(?:[:=),]|->|in\b)",
                              "\n".join(lines)))

    def _shadowed_local(self, decl: Decl | None, nm: str, kf: KFile) -> bool:
        """`_shadowed` for stored-property refs (#88), without its argument-shaped false positives: `{ x = …` (an
        assignment opening a body) and `f(x)` (an argument) do not declare `x`. A parameter / local / lambda parameter
        / destructured name / `for (x in …)` does."""
        if decl is None or decl.kind == "class":
            return False
        if nm in decl.types:
            return True
        body = "\n".join(kf.src.decode("utf-8", "replace").split("\n")[decl.line - 1:decl.end])
        n = re.escape(nm)
        return bool(re.search(rf"\b(?:val|var)\s+(?:\([^)]*?)?\b{n}\b\s*(?:[:=),]|by\b)"   # val x = / val (a, x) = / var x by
                              rf"|\bfor\s*\(\s*(?:\([^)]*?)?\b{n}\b[^)]*?\bin\b"               # for (x in / for ((a, x) in
                              rf"|[(,]\s*{n}\s*:"                                         # (x: T  a parameter
                              rf"|\{{\s*(?:\(?\s*[\w\s,:<>?]*,\s*)?{n}\b\s*(?:,[\w\s,:<>?]*)?\)?\s*->", body))  # { a, x -> / { (a, x) ->

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
        for owner, name, recv, line, kf, decl, acc in self.calls:
            self._data_access(owner, name, recv, line, kf, decl)
            self._how = self._recv = None
            targets = self._targets(name, recv, kf, decl)
            if not targets:
                self.st["calls_unresolved"] += 1
                continue
            how = {"binding": self._how} if self._how else {}
            if acc:
                how["accessor"] = acc
            # the receiver's class when the member was found on an ancestor: `impact Sub.m` narrows by it (#62)
            if self._recv is not None and any(t.kind != "class" and t.cls and t.cls != self._recv.fqn for t in targets):
                how["recv"] = [self._recv.id]
            if self._how == "candidate":
                how["candidates"] = len(targets)
                self.st["call_candidate_edges"] += len(targets)
            br = self.call_branch.get((owner, line, name), {})
            for t in targets[:MAX_CANDIDATES if self._how == "candidate" else 3]:
                if t.kind == "class":
                    self.b.add_edge(owner, t.id, "INSTANTIATES", kf.rel, line, HEURISTIC, **br)
                    ctor = [m for m in self.members.get(t.fqn, {}).get("init", [])]
                    self.b.add_edge(owner, t.id, "USES_TYPE", kf.rel, line, HEURISTIC, how="constructor call")
                    for m in ctor:
                        self.b.add_edge(owner, m.id, "CALLS", kf.rel, line, HEURISTIC)
                else:
                    composable = br and any(a[0] == "Composable" for a in t.annotations)
                    self.b.add_edge(owner, t.id, "CALLS", kf.rel, line, HEURISTIC, **how, **(br if composable else {}))
            self.st["calls_resolved"] += 1
        self._resolve_reads()
        self._resolve_fields()
        self._resolve_copies()

    def _copy_call(self, c, kf: KFile, owner: str, decl: Decl | None):
        """`state.copy(loading = true)` on a data class: a write of each named field, `via: copy` (#88)."""
        if not self.field_names or not c.children or c.children[0].type != "navigation_expression":
            return
        nav = c.children[0]
        kids = [x for x in nav.children if x.type != "."]
        if len(kids) != 2 or kids[1].type != "identifier" or self.t(kids[1]) != "copy":
            return
        args = next((x for x in c.children if x.type == "value_arguments"), None)
        named = []
        for a in (args.children if args is not None else []):
            if a.type == "value_argument":
                ids = [x for x in a.children if x.type == "identifier"]
                eq = next((x for x in a.children if x.type == "="), None)
                if ids and eq is not None and ids[0].start_byte < eq.start_byte:
                    named.append((self.t(ids[0]), a.start_point[0] + 1))
        if named:
            self.copies.append((owner, (kids[0].type, self.t(kids[0])), named, kf, decl))

    def _resolve_copies(self):
        """A receiver of known type binds `resolved`. Otherwise (`it.copy(...)` in `update { }`) the one data class
        whose fields include every named argument binds `heuristic`, `binding: name`; none or several bind nothing."""
        for owner, (rtype, rt), named, kf, decl in self.copies:
            cls = self.classes.get(decl.cls) if decl is not None and decl.cls else None
            rname = re.sub(r"[?!]", "", rt).split(".")[-1].strip()
            tyname = decl.types.get(rname) if decl is not None else None
            if tyname is None and cls is not None:
                tyname = self._field_type(cls, rname)
            if tyname is None and decl is not None and decl.kind != "class" and rtype == "identifier":
                tyname = self._local_type(decl, rname, kf)
            tc = self._class_of(re.sub(r"[<(].*", "", tyname, flags=re.S).rstrip("?! ").split(".")[-1], kf) if tyname else None
            conf, extra = RESOLVED, {}
            if tc is None:
                want = {n for n, _ in named}
                cands = [k for k, f in self.fields.items() if want <= set(f)
                         and "data" in getattr(self.classes.get(k), "modifiers", set())]
                if len(cands) != 1:
                    self.st["copy_unresolved"] += 1
                    continue
                tc, conf, extra = self.classes.get(cands[0]), HEURISTIC, {"binding": "name"}
            for n, line in named:
                fid = self._field_member(tc, n)
                if fid and fid != owner:
                    self.b.add_edge(owner, fid, "WRITES_PROP", kf.rel, line, conf, receiver=rt[:40], via="copy", **extra)
                    self.st["stored_property_writes"] += 1

    def _field_member(self, cls: Decl | None, name: str, depth: int = 0) -> str | None:
        if cls is None or depth > 6:
            return None
        if name in self.fields.get(cls.fqn, {}):
            return self.fields[cls.fqn][name]
        for s_ in cls.supers:
            sc = self.classes.get(s_) or (self.class_short.get(s_) or [None])[0]
            if sc is not None and sc is not cls:
                r = self._field_member(sc, name, depth + 1)
                if r:
                    return r
        return None

    def _resolve_fields(self):
        """READS_PROP / WRITES_PROP to stored-property field nodes (#88): `x` / `this.x` inside the class (or a
        subclass), `v.x` where the type of `v` is known (a parameter, a property, a local `val v = T(...)`). An
        unknown receiver binds nothing: stored names are far too common."""
        for owner, name, recv, line, kf, decl, write in self.frefs:
            cls = self.classes.get(decl.cls) if decl is not None and decl.cls else (
                decl if decl is not None and decl.kind == "class" else None)
            fid = None
            rt = recv[1] if recv is not None else None
            if recv is None or rt == "this":
                if recv is None and self._shadowed_local(decl, name, kf):
                    continue
                fid = self._field_member(cls, name)
            elif recv[0] in ("identifier", "this_expression") or re.fullmatch(r"[\w.]+", rt or ""):
                rname = rt.split(".")[-1].strip()
                tyname = decl.types.get(rname) if decl is not None else None
                if tyname is None and cls is not None:
                    tyname = self._field_type(cls, rname)
                if tyname is None and decl is not None and decl.kind != "class":
                    tyname = self._local_type(decl, rname, kf)
                if tyname:
                    tc = self._class_of(re.sub(r"[<(].*", "", tyname, flags=re.S).rstrip("?! ").split(".")[-1], kf)
                    fid = self._field_member(tc, name)
            if fid is None or fid == owner:
                self.st["stored_property_refs_unresolved"] += 1
                continue
            self.b.add_edge(owner, fid, "WRITES_PROP" if write else "READS_PROP", kf.rel, line, RESOLVED,
                            receiver="this" if recv is None or rt == "this" else rt[:40],
                            **({"via": write} if isinstance(write, str) else {}))
            self.st["stored_property_writes" if write else "stored_property_reads"] += 1

    def _local_type(self, decl: Decl, name: str, kf: KFile) -> str | None:
        """`val d = Cart(...)` inside the function body: the constructed type of a local."""
        src = "\n".join(kf.src.decode("utf-8", "replace").split("\n")[decl.line - 1:decl.end])
        m = re.search(r"\b(?:val|var)\s+" + re.escape(name) + r"\s*(?::\s*([A-Z][\w.]*))?\s*=\s*([A-Z][\w.]*)\s*\(", src)
        return (m.group(1) or m.group(2)) if m else None

    def _prop_member(self, cls: Decl, name: str, depth: int = 0) -> list[Decl]:
        hit = self.prop_members.get(cls.fqn, {}).get(name)
        if hit:
            return hit
        if depth > 6:
            return []
        for s_ in cls.supers:
            sc = self.classes.get(s_) or (self.class_short.get(s_) or [None])[0]
            if sc is not None and sc is not cls:
                r = self._prop_member(sc, name, depth + 1)
                if r:
                    return r
        return []

    def _ext_props(self, name: str, ty: str) -> list[Decl]:
        return [d for d in self.props_by_name.get(name, []) if d.receiver and d.receiver.split(".")[-1] == ty]

    def _resolve_reads(self):
        """Property reads / writes -> CALLS with `property: read | write`, on the receiver rules of calls (#83): a known
        receiver type binds exactly or not at all; an unknown receiver binds only a name no stored property shares."""
        LIT = {"integer_literal": "Int", "long_literal": "Long", "string_literal": "String", "real_literal": "Double",
               "boolean_literal": "Boolean", "character_literal": "Char"}
        for owner, name, recv, line, kf, decl, mode in self.reads:
            cls = self.classes.get(decl.cls) if decl is not None and decl.cls else (
                decl if decl is not None and decl.kind == "class" else None)
            how, r = None, []
            rt = recv[1] if recv is not None else None
            if recv is None or rt == "this":
                if recv is None and self._shadowed_local(decl, name, kf):
                    continue                         # not `_shadowed`: `{ x = 1` and `f(x)` do not declare `x` (#105)
                if cls is not None:
                    r = self._prop_member(cls, name)
                if not r and decl is not None and decl.receiver:
                    rc = self._class_of(decl.receiver.split(".")[-1], kf)
                    r = self._prop_member(rc, name) if rc is not None else []
                    r = r or self._ext_props(name, decl.receiver.split(".")[-1])
                if not r and recv is None:
                    if cls is not None and (name in cls.types or self._field_type(cls, name)):
                        continue                     # a stored property of the class shadows a top-level one
                    fq = kf.imports.get(name)
                    r = [d for d in self.props_by_name[name] if d.kind == "function" and not d.receiver
                         and (d.fqn == fq if fq else d.fqn == f"{kf.package}.{name}")]
            else:
                rname = re.sub(r"[?!]", "", rt).split(".")[-1].strip()
                tyname = LIT.get(recv[0])
                if recv[0] == "number_literal":
                    tyname = "Long" if rt.rstrip().endswith(("L", "l")) else ("Double" if "." in rt else "Int")
                if tyname is None and decl is not None:
                    tyname = decl.types.get(rname)
                if tyname is None and cls is not None:
                    tyname = self._field_type(cls, rname)
                if tyname is None and rname[:1].isupper():
                    tyname = re.sub(r"\s*[({].*$", "", rname, flags=re.S)
                if tyname is None and rname in self.props_by_name:      # `cart.label.length` style chains
                    pt = {self.props[d.id].get("type") for d in self.props_by_name[rname]} - {None}
                    tyname = pt.pop() if len(pt) == 1 else None
                if tyname:
                    short = re.sub(r"[<(].*", "", tyname, flags=re.S).rstrip("?! ").split(".")[-1]
                    tc = self._class_of(short, kf)
                    r = (self._prop_member(tc, name) if tc is not None else []) or self._ext_props(name, short)
                    if not r and tc is not None:
                        comp = self.classes.get(f"{tc.fqn}.Companion")
                        r = self._prop_member(comp, name) if comp else []
                else:
                    # extension / top-level properties never bind by name: `x.size` is far more often the library's
                    cands = list({d.id: d for d in self.props_by_name[name] if d.cls}.values())
                    if len(cands) == 1 and name not in self.stored_names and len(name) > 3:
                        r, how = cands, "name"
            if mode == "write":
                r = [d for d in r if {"set", "delegate"} & set(self.props[d.id]["accessors"])]
            for t in r[:3]:
                if t.id == owner:
                    continue
                m = mode
                if m == "read_write" and not {"set", "delegate"} & set(self.props[t.id]["accessors"]):
                    m = "read"                       # only the getter is a node's code: the setter is the default
                self.b.add_edge(owner, t.id, "CALLS", kf.rel, line, HEURISTIC, property=m,
                                **({"binding": how} if how else {}))
                self.st[{"read": "property_reads", "write": "property_writes"}.get(m, "property_read_writes")] += 1

    def _targets(self, name: str, recv: str | None, kf: KFile, decl: Decl | None) -> list[Decl]:
        cls = self.classes.get(decl.cls) if decl is not None and decl.cls else None
        if recv is None or recv == "this":
            if cls is not None:
                r = [d for d in self._member(cls, name) if d.kind != "class"]
                if r:
                    self._recv = cls
                    return self._same_set(r, kf)
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
            tyname = re.sub(r"\s*[({].*$", "", rname, flags=re.S)     # `WishController().add(x)`: a WishController
        if tyname:
            tc = self._class_of(tyname, kf)
            if tc is not None:
                r = self._member(tc, name)
                if not r:
                    comp = self.classes.get(f"{tc.fqn}.Companion")
                    r = self._member(comp, name) if comp else []
                if r:
                    self._recv = tc
                    return self._same_set(r, kf)
                return []
            short = re.sub(r"[<(].*", "", tyname, flags=re.S).rstrip("?! ").split(".")[-1]
            if len(short) > 1 and short[:1].isupper() and not self.class_short.get(short):
                # a library type (`DataStoreFactory.create(...)`, `Headers.build { }`, `client: HttpClient`): a known
                # receiver type binds exactly or not at all (#83), never by name to a project method
                self.st["calls_library_receiver"] += 1
                return []
        # unknown receiver: the name decides. One method of that name is a `name` binding; several (on up to
        # MAX_CANDIDATES classes) are `candidate` edges to each (#83 item 6): kept, never a confident edge, left out
        # of divergence and flagged by `cg tests` / impact
        cands = list({d.id: d for d in self.by_name.get(name, []) if d.kind == "method"
                      and "actual" not in d.modifiers}.values())
        if not cands or len(name) <= 3:
            return []
        if len(cands) > MAX_CANDIDATES:
            self.st["calls_too_ambiguous"] += 1
            return []
        self._how = "name" if len(cands) == 1 else "candidate"
        return cands

    @staticmethod
    def _same_set(cands: list[Decl], kf: KFile) -> list[Decl]:
        """Members of an `expect` class and its `actual`s share one name (#86): code in a platform source set calls the
        member of its own `actual`, common code the `expect` one; an `actual` from another platform's set never."""
        if len(cands) < 2 or not any("actual" in d.modifiers for d in cands):
            return cands
        same = [d for d in cands if d.file == kf.rel]
        if same:
            return same
        own = [d for d in cands if "actual" in d.modifiers and kf.source_set and d.id.endswith(f"@{kf.source_set}")]
        return own or [d for d in cands if "actual" not in d.modifiers] or cands

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

    # ------------------------------------------------------------------ typed navigation / resources
    def _typed_block(self, name: str, typ: str, c, lam, kf: KFile, owner: str, decl: Decl | None, ctx: dict) -> bool:
        """`composable<Route>(...) { }` / Navigation 3 `entry<Key> { }` as typed pages; Ktor type-safe resources
        `get<Articles> { }` inside routing as routes (path from the @Resource class)."""
        short = typ.split(".")[-1].split("<")[0].strip()
        if not re.fullmatch(r"[A-Z]\w*", short or ""):
            return False
        if name in ("composable", "entry", "dialog"):
            pid = self._page(kf, short, c, typed=True)
            self._refs(lam, kf, pid, decl, ctx)
            return True
        if name in VERBS and ctx.get("routing"):
            path = self._resource_path(short, kf)
            if path is None:
                return False
            hid = self._ktor_route(kf, name.upper(), join_path(ctx["prefix"], path), c, owner, ctx)
            self.st["routes_ktor_resources"] += 1
            self._refs(lam, kf, hid, decl, {**ctx, "routing": False})
            return True
        return False

    def _resource_path(self, short: str, kf: KFile, depth: int = 0) -> str | None:
        """Path of a Ktor @Resource class; a `parent` property typed with another resource prefixes it."""
        cls = self._class_of(short, kf)
        if cls is None or depth > 6:
            return None
        ann = next((a for a in cls.annotations if a[0] == "Resource"), None)
        if ann is None or ann[1] is None:
            return None
        path = template('"' + ann[1] + '"')
        ptype = cls.types.get("parent")
        if ptype:
            pp = self._resource_path(ptype.split(".")[-1].rstrip("?"), kf, depth + 1)
            if pp is not None:
                return join_path(pp, path)
        if cls.cls and depth == 0 and not path.startswith("/"):      # nested class of a resource without `parent`
            outer = self.classes.get(cls.cls)
            pp = self._resource_path(outer.name, kf, depth + 1) if outer else None
            if pp is not None:
                return join_path(pp, path)
        return path

    # ------------------------------------------------------------------ Gradle buildConfigField
    def _build_config(self, root: Path, files: list[str]) -> dict[str, list[str]]:
        """String buildConfigField values of the Gradle build files (`buildConfigField("String", "BASE_URL",
        "\\"https://...\\"")`): Retrofit `baseUrl(BuildConfig.BASE_URL)` resolves through them."""
        out: dict[str, list[str]] = defaultdict(list)
        cands = [f for f in files if f.endswith(("build.gradle.kts",))]
        for d in {Path(f).parent for f in files if f.endswith(".kt")}:
            for up in [d, *d.parents][:8]:
                g = up / "build.gradle"
                if (root / g).is_file():
                    cands.append(str(g))
        rx = re.compile(r'buildConfigField\s*\(?\s*["\']String["\']\s*,\s*["\'](\w+)["\']\s*,\s*'
                        r'(?:"\\"([^"\\]*)\\""|\'"([^"\']*)"\')')
        for f in sorted(set(cands)):
            try:
                txt = (root / f).read_text(errors="replace")
            except OSError:
                continue
            for m in rx.finditer(txt):
                v = m.group(2) if m.group(2) is not None else m.group(3)
                if v not in out[m.group(1)]:
                    out[m.group(1)].append(v)
        return dict(out)

    # ------------------------------------------------------------------ Spring Data / Exposed tables
    def _data_models(self, kfiles: list[KFile]):
        self.repos: dict[str, str] = {}       # repository interface short name -> table
        self.exposed: dict[str, str] = {}     # Exposed table object short name -> table
        rx_repo = re.compile(r"\binterface\s+(\w+)\s*(?:<[^>{]*>)?\s*:[^{]*?\b" + REPO_SUPERS + r"\s*<\s*([\w.]+)")
        rx_tab = re.compile(r"\bobject\s+(\w+)\s*:\s*(?:[\w.]+\.)?" + EXPOSED_TABLES + r"(?:<[^>]*>)?\s*\(\s*(?:name\s*=\s*)?"
                            r"(\"[^\"]*\")?")
        for kf in kfiles:
            txt = kf.src.decode("utf-8", "replace")
            if "Repository" in txt:
                for m in rx_repo.finditer(txt):
                    self.repos[m.group(1)] = self._entity_table(m.group(2).split(".")[-1], kf)
            if "Table" in txt and "exposed" in txt:
                for m in rx_tab.finditer(txt):
                    name = m.group(2).strip('"') if m.group(2) else re.sub(r"Table$", "", m.group(1))
                    self.exposed[m.group(1)] = name or m.group(1)

    def _entity_table(self, entity: str, kf: KFile) -> str:
        """@Table(name = "owners") on the entity, else Spring Boot's default naming (CamelCase -> snake_case)."""
        cls = self._class_of(entity, kf)
        if cls is not None:
            ann = next((a for a in cls.annotations if a[0] in ("Table", "Document")), None)
            if ann is not None and ann[1]:
                return ann[1]
        return re.sub(r"(?<!^)(?=[A-Z])", "_", entity).lower()

    def _table(self, name: str, via: str) -> str:
        tid = self.b.add_node("table", name, lang="sql", attrs={"inferred": True, "via": via})
        return tid

    def _data_access(self, owner: str, name: str, recv: str | None, line: int, kf: KFile, decl: Decl | None):
        if not (self.repos or self.exposed):
            return
        kind = "WRITES_TABLE" if DATA_WRITE.match(name) else "READS_TABLE" if DATA_READ.match(name) else None
        if kind is None:
            return
        rname = re.sub(r"[?!]", "", recv).split(".")[-1].strip() if recv else None
        if rname in self.exposed:                                       # Users.selectAll() / Users.insert { }
            self.b.add_edge(owner, self._table(self.exposed[rname], "exposed"), kind, kf.rel, line, RESOLVED,
                            via=f"exposed {name}")
            self.st["table_access_exposed"] += 1
            return
        repo = None
        if recv is None or recv == "this":
            if decl is not None and decl.cls and decl.cls.split(".")[-1] in self.repos:   # default method of the repo
                repo = decl.cls.split(".")[-1]
        else:
            cls = self.classes.get(decl.cls) if decl is not None and decl.cls else None
            ty = decl.types.get(rname) if decl is not None else None
            if ty is None and cls is not None:
                ty = self._field_type(cls, rname)
            ty = (ty or "").split(".")[-1].split("<")[0].rstrip("?")
            if ty in self.repos:
                repo = ty
        if repo is not None:                                            # owners.findById(id) on an OwnerRepository
            self.b.add_edge(owner, self._table(self.repos[repo], "spring-data"), kind, kf.rel, line, RESOLVED,
                            via=f"{repo}.{name}")
            self.st["table_access_spring_data"] += 1

    # ------------------------------------------------------------------ Spring Security
    def _security_rules(self, kfiles: list[KFile]):
        """URL rules of SecurityFilterChain beans (`requestMatchers("/admin/**").hasRole("ADMIN")`, Kotlin DSL
        `authorize("/admin/**", hasRole("ADMIN"))`), first match wins, as guards on the Spring routes they cover."""
        rules = []
        rx_chain = re.compile(r"\b(?:requestMatchers|antMatchers|mvcMatchers|pathMatchers)\s*\(([^()]*)\)\s*\.\s*"
                              + SEC_AUTH + r"\s*\(([^()]*)\)")
        rx_dsl = re.compile(r"\bauthorize\s*\(\s*(?:HttpMethod\.(\w+)\s*,\s*)?(\"[^\"]*\"|anyRequest)\s*,\s*"
                            + SEC_AUTH + r"\b(?:\s*\(([^()]*)\))?")
        rx_any = re.compile(r"\banyRequest\s*\(\s*\)\s*\.\s*" + SEC_AUTH + r"\s*\(([^()]*)\)")
        for kf in kfiles:
            txt = kf.src.decode("utf-8", "replace")
            if "SecurityFilterChain" not in txt and "SecurityWebFilterChain" not in txt:
                continue
            found = []
            for m in rx_chain.finditer(txt):
                meth = re.search(r"HttpMethod\.(\w+)", m.group(1))
                pats = re.findall(r'"([^"]*)"', m.group(1))
                found.append((m.start(), meth.group(1).upper() if meth else None, pats, m.group(2), m.group(3)))
            for m in rx_dsl.finditer(txt):
                pats = ["/**"] if m.group(2) == "anyRequest" else [m.group(2).strip('"')]
                found.append((m.start(), (m.group(1) or "").upper() or None, pats, m.group(3), m.group(4) or ""))
            for m in rx_any.finditer(txt):
                found.append((m.start(), None, ["/**"], m.group(1), m.group(2)))
            for _, meth, pats, auth, arg in sorted(found, key=lambda x: x[0]):
                roles = ",".join(re.findall(r'"([^"]*)"', arg))
                g = None if auth == "permitAll" else (f"{auth}({roles})" if roles else auth)
                rules.append((meth, [self._ant(p) for p in pats if p], g, kf.rel))
        if not rules:
            return
        self.st["security_rules"] = len(rules)
        for nid, n in self.b.nodes.items():
            if n.kind != "route" or n.lang != "kotlin" or n.attrs.get("framework") != "spring":
                continue
            uri, meth = n.attrs.get("uri", ""), n.attrs.get("method")
            probe = re.sub(r"\{\w+\}", "x", uri)
            for rm, rxs, g, rel in rules:
                if rm and rm != meth:
                    continue
                if any(r.fullmatch(probe) for r in rxs):
                    if g:
                        mw = n.attrs.setdefault("middleware", [])
                        if g not in mw:
                            mw.append(g)
                        n.attrs.setdefault("security", f"SecurityFilterChain ({rel})")
                        self.st["routes_guarded_by_security_chain"] += 1
                    break

    @staticmethod
    def _ant(p: str):
        """Spring path pattern -> regex: `**` any depth, `*` one segment part, `{x}` one segment."""
        out, i = "", 0
        while i < len(p):
            if p.startswith("/**", i):
                out += r"(?:/.*)?"
                i += 3
            elif p.startswith("**", i):
                out += r".*"
                i += 2
            elif p[i] == "*":
                out += r"[^/]*"
                i += 1
            elif p[i] == "{":
                j = p.find("}", i)
                out += r"[^/]+"
                i = j + 1 if j > 0 else len(p)
            else:
                out += re.escape(p[i])
                i += 1
        return re.compile(out)

    def _emit_http(self):
        bases = sorted(self.base_urls)
        base = bases[0] if len(bases) == 1 else None
        for r in self.http:
            url = r["url"]
            own = self.api_base.get(r.get("iface") or "")
            base = sorted(own)[0] if own and len(own) == 1 else (bases[0] if len(bases) == 1 else None)
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

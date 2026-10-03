"""Swift language plugin (iOS / macOS apps, SwiftUI, Vapor).

Heuristic mode: a tree-sitter-swift syntax layer, so repositories index on Linux without Xcode or a Swift toolchain.
Declarations (classes, structs, enums, actors, protocols, extensions, functions, methods, initializers, SwiftUI `body`
properties) become nodes; calls are resolved by name: the enclosing type, its extensions and supertypes, then a
parameter / property type (`api.book(id)` with `let api: BooksAPI`), then a project-wide unique name. Every resolved
reference is labelled `heuristic`. Extensions are merged into the type they extend.

Exact mode (exact.py, indexstore.py): the compiler's index store (`swift build --enable-index-store`, run with
CODEGRAPH_SWIFT_INDEX=1 for a SwiftPM package, or an existing store / Xcode DerivedData via
CODEGRAPH_SWIFT_INDEX_STORE) read through libIndexStore replaces the call / constructor edges of every file the store
covers; files it does not cover keep heuristic edges, and `cg coverage` names the mode and the reason.

Framework facts read from the same syntax tree:
  entry points  `@main` types (main), `UIApplicationDelegate` / `UISceneDelegate` / `App` lifecycle callbacks,
                `BGTaskScheduler.shared.register(forTaskWithIdentifier:)` handlers (queue_job), XCTest `test*` methods
  SwiftUI       `NavigationLink(destination: V())`, `.navigationDestination { V() }`, `.sheet` / `.fullScreenCover` /
                `.popover { V() }`, `TabView` children and the `WindowGroup` root: the target views become page nodes
                (`page:swift:<View>`, ROUTES_TO its `body`) with NAVIGATES_TO edges; UIKit
                `pushViewController(V(), ...)` / `present(V(), ...)` likewise
  HTTP clients  URLSession (`data(from:)`, `data(for:)`, `dataTask`, `upload(for:)`) with the URL built in the same
                function (`URL(string: "...")`, `appendingPathComponent("...")`, `"\\(baseURL)/..."` templates) and
                `httpMethod = "POST"`; Alamofire `AF.request(url, method: .post)` -> http:<METHOD> <path>
  Vapor         `app.get("orders", ":id") { }`, `routes.post("x", use: handler)`, `grouped("v1")` / `group("v1") { }`
                prefixes, middleware passed to `grouped(...)` (`User.authenticator()`, `User.guardMiddleware()`) as
                route guards, `RouteCollection.boot(routes:)` -> route:<METHOD> <uri>
  Moya          `TargetType` enums (baseURL + per-case path / method) -> one http node per case (HTTP_CALLS from the
                enum), `provider.request(.case)` / `requestPublisher` call sites -> HTTP_CALLS from the caller
  Fluent        `Model` classes with `static let schema = "todos"` -> table:todos (MAPS_TO_TABLE); migrations'
                `database.schema("todos")...create()` -> WRITES_TABLE; `Todo.query(on:)` / `Todo.find` -> READS_TABLE
                (WRITES_TABLE when the chain deletes / updates); `todo.save(on:)` / `.delete(on:)` -> WRITES_TABLE
  platforms     `#if os(iOS)` / `#elseif os(macOS)` / `#else` blocks feed the platform tags (docs/platforms.md)
"""
from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ...core.fsutil import keep_file
from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...core.paths import rules as path_rules
from ...core.plugin import GraphBuilder, LanguagePlugin, Project
from ..native.ts import TreeSitterMissing

EXTS = (".swift",)
VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}
TEST_PATH = re.compile(r"(^|/)(Tests?|\w+Tests|\w+UITests)/|Tests?\.swift$")
TEMPLATE = re.compile(r"\\\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
LIFECYCLE = re.compile(r"^(application\w*|scene\w*|sceneDid\w*|sceneWill\w*|viewDidLoad|viewWillAppear|viewDidAppear|"
                       r"userNotificationCenter|perform|handle|body)$")
LIFECYCLE_BASES = {"UIApplicationDelegate", "UIWindowSceneDelegate", "UISceneDelegate", "NSApplicationDelegate",
                   "UIViewController", "AppIntent", "Widget", "WKApplicationDelegate"}
URLSESSION = re.compile(r"\b(data|dataTask|upload|uploadTask|download|downloadTask|bytes)\s*\(\s*(from|for|with)\s*:")
PRESENT = {"sheet", "fullScreenCover", "popover", "navigationDestination"}
OS_PLATFORM = {"iOS": "ios", "iPadOS": "ios", "watchOS": "ios", "tvOS": "ios", "visionOS": "ios", "macOS": "macos",
               "OSX": "macos", "Linux": "linux", "Windows": "windows", "Android": "android", "WASI": "web"}
# `#if canImport(X)`: the SDK framework implies the platform (UIKit: iOS family, AppKit: macOS)
# (Apple-only frameworks: iOS or macOS; swift-corelibs FoundationNetworking: not Apple)
IMPORT_PLATFORM = {"UIKit": "ios", "WatchKit": "ios", "MobileCoreServices": "ios", "AppKit": "macos", "Cocoa": "macos",
                   "Glibc": "linux", "Musl": "linux", "WinSDK": "windows", "ucrt": "windows", "Android": "android",
                   "WASILibc": "web"}
APPLE_ONLY = {"Darwin", "Security", "Network", "SystemConfiguration", "UniformTypeIdentifiers", "Combine", "SwiftUI",
              "CoreServices", "CoreLocation", "CoreData", "CoreGraphics", "CoreFoundation", "ObjectiveC", "os",
              "StoreKit", "AVFoundation", "Metal", "CryptoKit", "UserNotifications", "WidgetKit"}
TYPE_DECLS = ("class_declaration", "protocol_declaration")


def parser():
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_swift as m
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise TreeSitterMissing(f"tree-sitter grammar for swift not installed ({e}); "
                                f"pip install tree-sitter tree-sitter-swift") from e
    return Parser(Language(m.language()))


def source_files(root: Path, project=None) -> list[str]:
    rules = path_rules(project, "swift")
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
    """Swift string literal source -> URL template: "orders/\\(id)" -> orders/{id}, "\\(baseURL)/x" -> {baseURL}/x."""
    s = raw.strip()
    if s.startswith('"""'):
        s = s[3:-3]
    elif s.startswith('"'):
        s = s[1:-1]

    def rep(m):
        name = re.sub(r"[^\w.]", "", m.group(1).split("(")[0]).split(".")[-1] or "?"
        return "{" + name + "}"
    return TEMPLATE.sub(rep, s)


def split_url(t: str) -> tuple[str | None, str]:
    t = t.split("?")[0].split("#")[0]
    m = re.match(r"^([a-zA-Z][\w+.-]*://[^/]*)(.*)$", t)
    origin = None
    if m:
        origin, t = m.group(1), m.group(2)
    else:
        m = re.match(r"^(\{[^{}/]*\})(/.*|)$", t)
        if m:
            origin, t = m.group(1), m.group(2)
    return origin, ("/" + t.lstrip("/")) if t else "/"


def join_path(base: str, p: str) -> str:
    if not p:
        return base or ""
    return (base.rstrip("/") + "/" + p.lstrip("/")) if base else p


def vapor_seg(s: str) -> str:
    return "{" + s[1:] + "}" if s.startswith(":") else s


@dataclass
class Decl:
    id: str
    kind: str
    name: str
    fqn: str
    file: str
    line: int
    end: int
    cls: str | None = None
    supers: list = field(default_factory=list)
    attributes: list = field(default_factory=list)
    modifiers: set = field(default_factory=set)
    types: dict = field(default_factory=dict)
    test: bool = False


class SFile:
    def __init__(self, rel: str, src: bytes, tree):
        self.rel, self.src, self.tree = rel, src, tree
        self.test = bool(TEST_PATH.search(rel))
        self.imports: set[str] = set()


class SwiftPlugin(LanguagePlugin):
    name = "swift"

    def detect(self, project: Project) -> bool:
        self._files = source_files(project.root, project)
        return bool(self._files)

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
        self._overloads: dict[tuple, Decl] = {}
        self.by_name: dict[str, list[Decl]] = defaultdict(list)
        self.types: dict[str, Decl] = {}
        self.members: dict[str, dict[str, list[Decl]]] = defaultdict(lambda: defaultdict(list))
        self.calls: list[tuple] = []
        self.http: list[dict] = []
        self.navs: list[tuple] = []          # (owner, view type name, how, file, line)
        self.handler_refs: list[tuple] = []  # (route id, method name, type fqn, file, line)
        self.st = defaultdict(int)
        sfiles, failed = [], []
        for rel in files:
            try:
                src = (project.root / rel).read_bytes()
            except OSError:
                failed.append(rel)
                continue
            tree = p.parse(src)
            if tree.root_node.has_error:
                self.st["files_with_syntax_errors"] += 1
            sfiles.append(SFile(rel, src, tree))
        self._fd: dict[str, list[Decl]] = defaultdict(list)
        self._avail_regions: dict[str, list] = {}
        for sf in sfiles:            # pass 1: declarations
            self.cur = sf
            for c in sf.tree.root_node.children:
                if c.type == "import_declaration":
                    sf.imports.add(self.t(c).split()[-1])
            self._decls(sf.tree.root_node, sf, None)
        for d in self.decls.values():
            self._fd[d.file].append(d)
        self._moya_targets(sfiles)
        self._fluent_models(sfiles)
        for sf in sfiles:            # pass 2: references and framework facts
            self.cur = sf
            fid = self.b.add_node("file", f"swift:{sf.rel}", name=sf.rel, file=sf.rel, line=1, lang="swift",
                                  attrs={"test": True} if sf.test else {})
            self._refs(sf.tree.root_node, sf, fid, None, {})
            self._directives(sf)
            self._available(sf)
        self._resolve_calls()
        self._hierarchy()
        self._fluent_migrations(sfiles)
        self._moya_endpoints()
        self._emit_http()
        self._link_navs()
        for rid, mname, tfq, file, line in self.handler_refs:
            t = self.types.get(tfq)
            ms = self._member(t, mname) if t else []
            if ms:
                self.b.add_edge(rid, ms[0].id, "ROUTES_TO", file, line, HEURISTIC)
                self.b.nodes[rid].attrs.setdefault("handler", ms[0].fqn)
            else:
                self.st["routes_unresolved_handler"] += 1
        self.file_report = {"seen": [sf.rel for sf in sfiles] + failed, "parse_failed": failed}
        mode = self._exact(project, [sf.rel for sf in sfiles])
        self._apply_available()
        st = dict(self.st)
        st.update({"mode": mode, "files": len(sfiles), "declarations": len(self.decls),
                   "source_files": sum(1 for sf in sfiles if not re.match(r"(.*/)?Package(@swift-[\d.]+)?\.swift$", sf.rel)),
                   "seconds": round(time.time() - t0, 2)})
        return st

    def _exact(self, project: Project, files: list[str]) -> str:
        """Exact layer from the Swift index store (exact.py); the heuristic graph stays when there is none or it
        cannot be read."""
        from . import exact
        t0 = time.time()
        try:
            store, info = exact.find_store(project, files)
        except Exception as e:  # noqa: BLE001 - a toolchain problem must not lose the heuristic graph
            store, info = None, {"status": f"index store lookup failed: {e}"}
        mode = "heuristic"
        if store is not None:
            try:
                if exact.ExactLayer(self).apply(store, info["lib"], project.root, files, self.st):
                    mode = "indexstore"
                else:
                    info["status"] = "the index store has no units for this project's files"
            except Exception as e:  # noqa: BLE001
                info["status"] = f"index store import failed: {type(e).__name__}: {e}"
        info["seconds"] = round(time.time() - t0, 2)
        self.st["index"] = info
        return mode

    # ------------------------------------------------------------------ pass 1
    def _in_directive(self, sf: SFile, n) -> bool:
        """Does a `#if` / `#elseif` / `#else` line sit between the previous sibling declaration and `n`?"""
        p = n.prev_sibling
        while p is not None:
            if p.type == "directive":
                return True
            if p.is_named:
                return False
            p = p.prev_sibling
        return False

    def _mods(self, n) -> tuple[list, set]:
        attrs, mods = [], set()
        for c in n.children:
            if c.type == "modifiers":
                for m in c.children:
                    if m.type == "attribute":
                        mm = re.match(r"@([\w.]+)", self.t(m))
                        if mm:
                            attrs.append(mm.group(1).split(".")[-1])
                    else:
                        mods.update(self.t(m).split())
        return attrs, mods

    def _name(self, n) -> str | None:
        x = n.child_by_field_name("name")
        return self.t(x).split("<")[0].strip() if x is not None else None

    def _type_name(self, n) -> str | None:
        if n is None:
            return None
        m = re.match(r"\s*(?:some\s+|any\s+)?\[?([A-Za-z_][\w.]*)", self.t(n))
        return m.group(1).split(".")[-1] if m else None

    def _decls(self, n, sf: SFile, cls: Decl | None):
        for c in n.children:
            ty = c.type
            if ty in TYPE_DECLS:
                nm = self._name(c)
                if not nm:
                    continue
                head = self.t(c).split("{", 1)[0]
                kw = re.match(r"\s*(?:@\w+(?:\([^)]*\))?\s+|\w+\s+)*?(class|struct|enum|actor|extension|protocol)\b", head)
                kw = kw.group(1) if kw else ("protocol" if ty == "protocol_declaration" else "class")
                attrs, mods = self._mods(c)
                supers = [self._type_name(x) for x in c.children if x.type == "inheritance_specifier"]
                supers = [s for s in supers if s]
                fq = f"{cls.fqn}.{nm}" if cls and kw != "extension" else nm
                body = next((x for x in c.children if x.type in ("class_body", "enum_class_body", "protocol_body")), None)
                if kw == "extension":
                    base = self.types.get(nm)
                    if base is None:
                        base = Decl(f"class:{nm}", "class", nm, nm, sf.rel, c.start_point[0] + 1, c.end_point[0] + 1,
                                    None, [], [], {"extension-only"}, test=sf.test)
                        self._add(base, "extension")
                    base.supers += [s for s in supers if s not in base.supers]
                    self.st["extensions"] += 1
                    if body is not None:
                        self._props(body, base, sf)
                        self._decls(body, sf, base)
                    continue
                d = self.types.get(fq)
                if d is not None and "extension-only" in d.modifiers:     # extension seen before the type
                    d.modifiers.discard("extension-only")
                    d.file, d.line, d.end = sf.rel, c.start_point[0] + 1, c.end_point[0] + 1
                    d.supers = supers + [s for s in d.supers if s not in supers]
                    d.attributes = attrs
                    n0 = self.b.nodes[d.id]
                    n0.file, n0.line, n0.end_line = d.file, d.line, d.end
                    n0.attrs["swift_kind"] = kw
                else:
                    d = Decl(f"class:{fq}", "class", nm, fq, sf.rel, c.start_point[0] + 1, c.end_point[0] + 1,
                             cls.fqn if cls else None, supers, attrs, mods, test=sf.test)
                    self._add(d, kw)
                if body is not None:
                    self._props(body, d, sf)
                    self._decls(body, sf, d)
            elif ty in ("function_declaration", "init_declaration", "protocol_function_declaration"):
                nm = "init" if ty == "init_declaration" else self._name(c)
                if not nm:
                    continue
                attrs, mods = self._mods(c)
                kind = "method" if cls else "function"
                fq = f"{cls.fqn}.{nm}" if cls else nm
                params = {}
                for x in c.children:
                    if x.type == "parameter":
                        names = [self.t(y) for y in x.children if y.type == "simple_identifier"]
                        tn = self._type_name(next((y for y in x.children if y.is_named and y.type not in
                                                   ("simple_identifier",)), None))
                        if names and tn:
                            params[names[-1]] = tn
                did = f"{kind}:{fq}"
                if did in self.decls:
                    prev = self.decls[did]
                    if prev.file != sf.rel or not self._in_directive(sf, c):  # overloads share one node
                        prev.types.update(params)
                        self._overloads[(sf.rel, c.start_point[0] + 1, nm)] = prev    # its body's calls are prev's
                        continue
                    did = f"{did}@{c.start_point[0] + 1}"                     # per-platform definition (#if os)
                d = Decl(did, kind, nm, fq, sf.rel, c.start_point[0] + 1, c.end_point[0] + 1,
                         cls.fqn if cls else None, [], attrs, mods, params, sf.test)
                self._add(d, "protocol requirement" if ty == "protocol_function_declaration" else kind)
            elif ty == "property_declaration" and cls is not None:
                nm = self._name(c)
                if nm == "body" and any(x.type == "computed_property" for x in c.children):
                    d = Decl(f"method:{cls.fqn}.body", "method", "body", f"{cls.fqn}.body", sf.rel,
                             c.start_point[0] + 1, c.end_point[0] + 1, cls.fqn, [], [], set(), test=sf.test)
                    self._add(d, "view body")
            elif ty not in ("lambda_literal", "statements", "function_body"):
                self._decls(c, sf, cls)

    def _props(self, body, d: Decl, sf: SFile):
        for x in body.children:
            if x.type != "property_declaration":
                continue
            nm = self._name(x)
            ta = next((y for y in x.children if y.type == "type_annotation"), None)
            tn = self._type_name(ta.child_by_field_name("name") if ta is not None else None)
            if tn is None:
                m = re.search(r"=\s*([A-Z]\w*)\s*[.(]", self.t(x))
                tn = m.group(1) if m else None
            if nm and tn:
                d.types[nm] = tn

    def _add(self, d: Decl, display_kind: str):
        attrs = {"swift_kind": display_kind}
        if d.test:
            attrs["test"] = True
        for a in ("static", "override", "async", "mutating", "private"):
            if a in d.modifiers:
                attrs[a] = True
        if d.attributes:
            attrs["attributes"] = d.attributes[:12]
        mod = d.cls or None
        self.b.add_node(d.kind, d.id.split(":", 1)[1], name=d.name, fqn=d.fqn, file=d.file, line=d.line,
                        end_line=d.end, module=mod, lang="swift", attrs=attrs)
        n = self.b.nodes[d.id]
        if d.kind == "class" and "main" in d.attributes:
            n.entry_kind = "main"
            self.st["entries_main"] += 1
        if d.test and d.kind == "method" and d.name.startswith("test"):
            n.entry_kind = "test"
        self.decls[d.id] = d
        self.by_name[d.name].append(d)
        if d.kind == "class":
            self.types.setdefault(d.fqn, d)
        if d.cls:
            self.members[d.cls][d.name].append(d)
            self.b.add_edge(f"class:{d.cls}", d.id, "CONTAINS", d.file, d.line, EXACT)

    # ------------------------------------------------------------------ pass 2
    def _decl_at(self, sf: SFile, n, kinds, name=None) -> Decl | None:
        line = n.start_point[0] + 1
        nm = name or self._name(n)
        for d in self._fd.get(sf.rel, ()):
            if d.line == line and d.kind in kinds and d.name == nm:
                return d
        ov = self._overloads.get((sf.rel, line, nm))
        if ov is not None and ov.kind in kinds:
            return ov
        if name and kinds == ("class",):
            d = self.types.get(name)
            return d
        return None

    def _refs(self, n, sf: SFile, owner: str, decl: Decl | None, ctx: dict):
        for c in n.children:
            ty = c.type
            if ty in ("function_declaration", "init_declaration"):
                d = self._decl_at(sf, c, ("function", "method"), "init" if ty == "init_declaration" else None)
                if d is not None:
                    self._fn_http(c, sf, d)
                    self._refs(c, sf, d.id, d, {"routers": {}})
                    continue
            elif ty in TYPE_DECLS:
                nm = self._name(c)
                d = self.types.get(nm) if nm else None
                inner = next((x for x in self._fd.get(sf.rel, ()) if x.kind == "class" and x.name == nm
                              and x.line == c.start_point[0] + 1), None)
                d = inner or d
                self._refs(c, sf, d.id if d else owner, d or decl, ctx)
                continue
            elif ty == "property_declaration" and decl is not None and decl.kind == "class" and self._name(c) == "body":
                b = self.decls.get(f"method:{decl.fqn}.body")
                if b is not None:
                    self._refs(c, sf, b.id, b, ctx)
                    continue
            elif ty == "call_expression":
                if self._call(c, sf, owner, decl, ctx):
                    continue
            elif ty == "property_declaration" and decl is not None:
                self._router_binding(c, ctx)
            self._refs(c, sf, owner, decl, ctx)

    def _callee(self, c):
        """(receiver text, name, value_arguments node, trailing lambda) of a call_expression."""
        if not c.children:
            return None, None, None, None
        f = c.children[0]
        suf = next((x for x in c.children if x.type == "call_suffix"), None)
        args = next((x for x in suf.children if x.type == "value_arguments"), None) if suf is not None else None
        lam = next((x for x in suf.children if x.type in ("lambda_literal", "annotated_lambda")), None) if suf is not None else None
        if f.type == "simple_identifier":
            return None, self.t(f), args, lam
        if f.type == "navigation_expression":
            target = f.child_by_field_name("target") or f.children[0]
            sfx = next((x for x in f.children if x.type == "navigation_suffix"), None)
            nm = self.t(sfx).lstrip(".").strip() if sfx is not None else None
            return (self.t(target) if target is not None and target.type != "navigation_suffix" else ""), nm, args, lam
        return None, None, args, lam

    def _args(self, args) -> list[tuple[str | None, object]]:
        out = []
        for a in (args.children if args is not None else []):
            if a.type == "value_argument":
                lab = next((x for x in a.children if x.type == "value_argument_label"), None)
                val = a.child_by_field_name("value") or next((x for x in reversed(a.children) if x.is_named), None)
                out.append((self.t(lab).strip() if lab is not None else None, val))
        return out

    def _call(self, c, sf: SFile, owner: str, decl: Decl | None, ctx: dict) -> bool:
        line = c.start_point[0] + 1
        recv, name, args, lam = self._callee(c)
        if not name or not re.match(r"^\w+$", name):
            return False
        al = self._args(args)
        # ---- SwiftUI / UIKit navigation
        if name == "NavigationLink":
            for lab, v in al:
                if lab == "destination" and v is not None:
                    self._nav_target(v, owner, sf, line, "NavigationLink")
        if name in PRESENT and lam is not None:
            self._lambda_views(lam, owner, sf, line, f".{name}")
        if name in ("pushViewController", "present", "show") and al and al[0][1] is not None:
            self._nav_target(al[0][1], owner, sf, line, name)
        if name in ("WindowGroup", "TabView") and lam is not None and recv is None:
            self._lambda_views(lam, owner, sf, line, name, page_only=name == "WindowGroup")
        # ---- Alamofire
        if name == "request" and recv is not None and re.match(r"^(AF|Alamofire|session|\w*[Ss]ession)$", recv) and al:
            url = self._url_text(al[0][1])
            meth = next((self.t(v).lstrip(".").upper() for lab, v in al if lab == "method" and v is not None), "GET")
            if url is not None:
                self.http.append({"src": owner, "method": meth, "url": url, "client": "alamofire", "file": sf.rel,
                                  "line": line})
        # ---- Moya: provider.request(.case) -> the TargetType case's endpoint
        self._moya_call(name, recv, al, owner, sf, line)
        # ---- Fluent: Todo.query(on:) / .find / todo.save(on:)
        self._fluent_call(name, recv, owner, decl, sf, line, c)
        # ---- BGTaskScheduler
        if name == "register" and "BGTaskScheduler" in (recv or "") and lam is not None:
            hid = self.b.add_node("function", f"<bgtask>@{sf.rel}:{line}", name="background task handler", file=sf.rel,
                                  line=line, lang="swift", entry_kind="queue_job", attrs={"lambda": True})
            self.st["background_tasks"] += 1
            self._refs(lam, sf, hid, decl, ctx)
            return True
        # ---- Vapor routing
        routers = ctx.get("routers")
        if routers is not None and recv is not None:
            base = self._router(recv, routers)
            if base is not None:
                if name in VERBS or name == "on":
                    self._vapor_route(name, al, lam, base, sf, owner, decl, c, ctx)
                    return lam is not None
                if name in ("group", "grouped") and lam is not None:
                    pfx, guards = self._group_args(al, base)
                    pname = self._lambda_param(lam)
                    inner = dict(routers)
                    if pname:
                        inner[pname] = (pfx, guards)
                    self._refs(lam, sf, owner, decl, {**ctx, "routers": inner})
                    return True
        self.calls.append((owner, name, recv, line, sf, decl))
        return False

    # ---- Vapor helpers
    def _router(self, recv: str, routers: dict):
        """(prefix, guards) of a router expression: app / routes / req.application, a variable bound to a group, or
        a chained `x.grouped(...)`."""
        recv = recv.strip()
        if recv in routers:
            return routers[recv]
        if re.match(r"^(app|application|routes|router|req\.application|self\.app)$", recv):
            return ("", [])
        m = re.match(r"^(\w+)\s*\.\s*grouped\s*\((.*)\)\s*$", recv, re.S)
        if m:
            base = self._router(m.group(1), routers)
            if base is not None:
                return self._group_text(m.group(2), base)
        return None

    def _group_text(self, inner: str, base):
        pfx, guards = base
        segs = re.findall(r'"([^"]*)"', inner)
        mws = [re.sub(r"\(\s*\)$", "", x.strip()) for x in re.split(r",(?![^()]*\))", inner) if x.strip() and '"' not in x]
        return join_path(pfx, "/".join(vapor_seg(s) for s in segs)), guards + [m for m in mws if m]

    def _group_args(self, al, base):
        return self._group_text(", ".join(self.t(v) for _, v in al if v is not None), base)

    def _router_binding(self, c, ctx: dict):
        """let protected = app.grouped("v1").grouped(User.authenticator()) -> routers["protected"]."""
        routers = ctx.get("routers")
        if routers is None:
            return
        nm = self._name(c)
        txt = self.t(c)
        m = re.match(r"^\s*(?:let|var)\s+\w+\s*(?::[^=]+)?=\s*(.+)$", txt, re.S)
        if not nm or not m:
            return
        expr = m.group(1).strip()
        parts = re.match(r"^([\w.]+)((?:\s*\.\s*grouped\s*\((?:[^()]|\([^()]*\))*\))+)\s*$", expr, re.S)
        if not parts:
            return
        base = self._router(parts.group(1), routers)
        if base is None:
            return
        for g in re.findall(r"grouped\s*\(((?:[^()]|\([^()]*\))*)\)", parts.group(2)):
            base = self._group_text(g, base)
        routers[nm] = base

    def _lambda_param(self, lam) -> str | None:
        m = re.match(r"\{\s*\(?\s*(\w+)", self.t(lam))
        return m.group(1) if m and re.search(r"\bin\b", self.t(lam).split("\n")[0]) else None

    def _vapor_route(self, verb, al, lam, base, sf, owner, decl, c, ctx):
        pfx, guards = base
        segs, handler, method = [], None, verb.upper()
        for lab, v in al:
            if v is None:
                continue
            if lab is None and v.type == "line_string_literal":
                segs.append(vapor_seg(template(self.t(v))))
            elif lab == "use":
                handler = self.t(v)
            elif lab is None and verb == "on":
                method = self.t(v).lstrip(".").upper()
        uri = "/" + join_path(pfx, "/".join(segs)).strip("/")
        line = c.start_point[0] + 1
        key = f"{method} {uri}"
        attrs = {"uri": uri, "method": method, "framework": "vapor"}
        if guards:
            attrs["middleware"] = list(dict.fromkeys(guards))
        rid = self.b.add_node("route", key, name=key, file=sf.rel, line=line, lang="swift", entry_kind="http_route",
                              attrs=attrs)
        self.b.nodes[rid].entry_kind = self.b.nodes[rid].entry_kind or "http_route"
        self.st["routes"] += 1
        self.st["routes_vapor"] += 1
        self.b.add_edge(owner, rid, "REFERENCES_FN", sf.rel, line, EXACT, how="router registration")
        if lam is not None:
            hk = f"<{key}>@{sf.rel}:{line}"
            hid = self.b.add_node("function", hk, name=f"{key} handler", fqn=hk, file=sf.rel, line=line,
                                  end_line=c.end_point[0] + 1, lang="swift",
                                  attrs={"lambda": True, "swift_kind": "route handler", **({"test": True} if sf.test else {})})
            self.b.add_edge(rid, hid, "ROUTES_TO", sf.rel, line, EXACT)
            self._refs(lam, sf, hid, decl, {**ctx, "routers": None})
        elif handler:
            h = re.sub(r"^self\.", "", handler).split("(")[0]
            tfq = decl.cls if decl is not None and decl.cls else (decl.fqn if decl is not None and decl.kind == "class" else None)
            if tfq:
                self.handler_refs.append((rid, h, tfq, sf.rel, line))

    # ---- navigation helpers
    def _nav_target(self, v, owner, sf, line, how):
        m = re.match(r"\s*(?:\w+\s*\.\s*)?([A-Z]\w*)\s*\(", self.t(v))
        if m:
            self.navs.append((owner, m.group(1), how, sf.rel, line))

    def _lambda_views(self, lam, owner, sf, line, how, page_only=False):
        stm = next((x for x in lam.children if x.type == "statements"), None)
        for x in (stm.children if stm is not None else []):
            node = x
            while node.type in ("call_expression", "navigation_expression") and node.children and \
                    node.children[0].type in ("call_expression", "navigation_expression"):
                node = node.children[0]          # View(...).tabItem { } -> View(...)
            m = re.match(r"\s*([A-Z]\w*)\s*\(", self.t(node))
            if m and m.group(1) not in ("Text", "Image", "Label", "Button", "VStack", "HStack", "ZStack", "List",
                                        "Group", "NavigationStack", "NavigationView", "ScrollView", "Form"):
                self.navs.append((owner, m.group(1), how, sf.rel, line, page_only))

    # ---- URLSession
    def _url_text(self, v) -> str | None:
        if v is None:
            return None
        txt = self.t(v)
        if v.type == "line_string_literal":
            return template(txt)
        m = re.search(r'URL\s*\(\s*string\s*:\s*("(?:[^"\\]|\\.)*")', txt)
        if m:
            return template(m.group(1))
        return None

    def _fn_http(self, fn, sf: SFile, d: Decl):
        txt = self.t(fn)
        if not URLSESSION.search(txt) or "URLSession" not in txt and "session" not in txt:
            return
        url = None
        for m in re.finditer(r'URL\s*\(\s*string\s*:\s*("(?:[^"\\]|\\.)*")', txt):
            url = template(m.group(1))
        if url is None:
            base = re.search(r"(\w+)\s*\.\s*appending(?:PathComponent|Path)?\s*\(\s*(?:path\s*:\s*)?(\"(?:[^\"\\]|\\.)*\")", txt)
            if base:
                url = "{" + base.group(1) + "}/" + template(base.group(2)).lstrip("/")
        if url is None:
            self.st["urlsession_url_unknown"] += 1
            return
        mm = re.search(r'httpMethod\s*=\s*"(\w+)"', txt)
        self.http.append({"src": d.id, "method": (mm.group(1) if mm else "GET").upper(), "url": url, "client": "urlsession",
                          "file": sf.rel, "line": d.line})

    # ---- @available(macOS, unavailable): the declaration (and its members) does not exist on that platform;
    # @available(*, unavailable): on none. Version forms (`@available(iOS 17, *)`, `@available(iOS, introduced: 15)`,
    # `if #available(iOS 17, *)`, `guard #available`, `if #unavailable`) keep the code on every target and record the
    # minimum OS versions: node attrs.available on the declaration (members inherit the type's), edge attrs.available
    # on the references in the guarded branch. `deprecated` is kept as attrs.deprecated.
    AVAILABLE = re.compile(r"@available\s*\(\s*(iOS|macOS|OSX)\s*,\s*unavailable\b")
    AVAIL_ATTR = re.compile(r"@available\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
    AVAIL_VER = re.compile(r"\b(iOS|iPadOS|macOS|OSX|watchOS|tvOS|visionOS|macCatalyst)(?:ApplicationExtension)?\s*"
                           r"(?:,\s*introduced\s*:\s*)?(\d+(?:\.\d+)*)")

    @staticmethod
    def _vmax(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            if k not in out or tuple(int(x) for x in v.split(".")) > tuple(int(x) for x in out[k].split(".")):
                out[k] = v
        return out

    def _available(self, sf: SFile):
        if b"available" not in sf.src:
            return
        from ...platforms import KNOWN, Cond, _plat_atom, mark
        lines = sf.src.decode("utf-8", "replace").split("\n")
        decl_av = []
        for d in sorted(self._fd.get(sf.rel, ()), key=lambda d: d.line - d.end):     # outer declarations first
            head = "\n".join(lines[d.line - 1:d.end]).split("{", 1)[0]
            for m in self.AVAILABLE.finditer(head):
                plat = OS_PLATFORM[m.group(1)]
                mark(self.b, sf.rel, d.line, d.end, Cond("tree", ("not", _plat_atom(plat)), m.group(0) + ")"))
                self.st["platform_unavailable"] += 1
            av, dep = {}, None
            for m in self.AVAIL_ATTR.finditer(head):
                args = m.group(1)
                if re.match(r"\s*\*\s*,\s*unavailable\b", args):
                    mark(self.b, sf.rel, d.line, d.end, Cond("tree", ("all", [("not", _plat_atom(p)) for p in KNOWN]),
                                                             "@available(*, unavailable)"))
                    self.st["platform_unavailable_everywhere"] += 1
                    continue
                if re.search(r"\bdeprecated\b", args):
                    mm = re.search(r'message\s*:\s*"((?:[^"\\]|\\.)*)"', args)
                    dep = mm.group(1)[:160] if mm else True
                if "unavailable" not in args and "obsoleted" not in args:
                    for v in self.AVAIL_VER.finditer(args):
                        av = self._vmax(av, {"macOS" if v.group(1) == "OSX" else v.group(1): v.group(2)})
            for a, z, pav in decl_av:                      # members inherit the enclosing type's availability
                if a <= d.line and d.end <= z and (a, z) != (d.line, d.end):
                    av = self._vmax(pav, av)
            if av:
                decl_av.append((d.line, d.end, av))
            n = self.b.nodes.get(d.id)
            if n is not None and (av or dep):
                n.attrs = dict(n.attrs or {})
                if av:
                    n.attrs["available"] = av
                    self.st["available_declarations"] += 1
                if dep:
                    n.attrs["deprecated"] = dep
        if b"#available" not in sf.src and b"#unavailable" not in sf.src:
            return
        regions = self._avail_regions.setdefault(sf.rel, [])

        def walk(n):
            for c in n.children:
                if c.type == "availability_condition":
                    self._avail_branch(c, regions)
                walk(c)
        walk(sf.tree.root_node)

    def _avail_branch(self, c, regions: list):
        txt = self.t(c)
        av = {}
        for v in self.AVAIL_VER.finditer(txt):
            av = self._vmax(av, {"macOS" if v.group(1) == "OSX" else v.group(1): v.group(2)})
        if not av:
            return
        neg = "#unavailable" in txt.replace(" ", "")
        p = c.parent
        if p is None:
            return
        kids = p.children
        i = next((k for k, x in enumerate(kids) if x.id == c.id), None)
        if i is None:
            return
        if p.type == "if_statement":
            opens = [k for k in range(i + 1, len(kids)) if kids[k].type == "{"]
            closes = [k for k in range(i + 1, len(kids)) if kids[k].type == "}"]
            if not opens or not closes:
                return
            if not neg:
                a, z = kids[opens[0]].start_point[0] + 1, kids[closes[0]].start_point[0] + 1
            else:                              # `if #unavailable(iOS 17) { old } else { new }`: the else branch
                if len(opens) < 2:
                    return
                a, z = kids[opens[1]].start_point[0] + 1, p.end_point[0] + 1
        elif p.type == "guard_statement" and not neg:
            stmts = p.parent
            a, z = p.end_point[0] + 2, (stmts.end_point[0] + 1 if stmts is not None else p.end_point[0] + 1)
        else:
            return
        if z >= a:
            regions.append((a, z, av))
            self.st["available_branches"] += 1

    def _apply_available(self):
        """References inside `if #available(...)` / after `guard #available(...)`: edge attrs.available."""
        if not any(self._avail_regions.values()):
            return
        for e in self.b.edges.values():
            regs = self._avail_regions.get(e.file)
            if not regs or e.line is None:
                continue
            av = {}
            for a, z, v in regs:
                if a <= e.line <= z:
                    av = self._vmax(av, v)
            if av:
                e.attrs = {**(e.attrs or {}), "available": av}
                self.st["available_references"] += 1

    # ---- #if os(...)
    def _directives(self, sf: SFile):
        from ...platforms import Cond, _plat_atom, mark
        dirs = []
        def walk(n):
            for c in n.children:
                if c.type == "directive":
                    dirs.append(c)
                else:
                    walk(c)
        walk(sf.tree.root_node)
        stack = []        # [(start line, cond text, previous conds)]
        for d in dirs:
            txt = self.t(d).strip()
            line = d.start_point[0] + 1
            kw = re.match(r"#(if|elseif|else|endif)\b\s*(.*)", txt)
            if not kw:
                continue
            k, expr = kw.group(1), kw.group(2)
            if k in ("elseif", "else", "endif") and stack:
                start, cur, prev, ctext = stack.pop()
                if cur is not None:
                    mark(self.b, sf.rel, start, line - 1, Cond("tree", cur, ctext))
                    self.st["platform_blocks"] += 1
                prev = prev + ([cur] if cur is not None else [])
                if k == "endif":
                    continue
                new = self._os_expr(expr) if k == "elseif" else None
                if k == "else":
                    new = ("all", [("not", p) for p in prev]) if prev else None
                elif new is not None and prev:
                    new = ("all", [new] + [("not", p) for p in prev])
                stack.append((line + 1, new, prev, txt))
            elif k == "if":
                stack.append((line + 1, self._os_expr(expr), [], txt))

    def _os_expr(self, expr: str):
        from ...platforms import _plat_atom
        expr = re.sub(r"//.*|/\*.*?\*/", "", expr).strip()
        if "||" in expr:
            parts = [self._os_expr(x) for x in expr.split("||")]
            return ("any", parts) if all(parts) else None
        if "&&" in expr:
            parts = [self._os_expr(x) for x in expr.split("&&")]
            parts = [p for p in parts if p]
            return ("all", parts) if parts else None
        neg = expr.startswith("!")
        m = re.match(r"!?\s*(os|canImport|targetEnvironment)\s*\(\s*([\w.]+)\s*\)$", expr)
        if not m:
            return None
        fn, arg = m.groups()
        if fn == "os":
            plat = OS_PLATFORM.get(arg)
        elif fn == "canImport":
            plat = IMPORT_PLATFORM.get(arg)
            if arg in APPLE_ONLY or arg == "FoundationNetworking":
                apple = ("any", [_plat_atom("ios"), _plat_atom("macos")])
                return apple if (arg in APPLE_ONLY) != neg else ("not", apple)
        else:                       # Mac Catalyst: the iOS app built for macOS; simulator: not a platform
            plat = "macos" if arg == "macCatalyst" else None
        if not plat:
            return None
        a = _plat_atom(plat)
        return ("not", a) if neg else a

    # ------------------------------------------------------------------ resolution
    def _member(self, cls: Decl | None, name: str, depth: int = 0) -> list[Decl]:
        if cls is None:
            return []
        hit = self.members.get(cls.fqn, {}).get(name)
        if hit:
            return hit
        if depth > 6:
            return []
        for s in cls.supers:
            sc = self.types.get(s)
            if sc is not None and sc is not cls:
                r = self._member(sc, name, depth + 1)
                if r:
                    return r
        return []

    def _resolve_calls(self):
        for owner, name, recv, line, sf, decl in self.calls:
            targets = self._targets(name, recv, decl)
            if not targets:
                self.st["calls_unresolved"] += 1
                continue
            for t in targets[:3]:
                if t.kind == "class":
                    self.b.add_edge(owner, t.id, "INSTANTIATES", sf.rel, line, HEURISTIC)
                    for m in self.members.get(t.fqn, {}).get("init", []):
                        self.b.add_edge(owner, m.id, "CALLS", sf.rel, line, HEURISTIC)
                else:
                    self.b.add_edge(owner, t.id, "CALLS", sf.rel, line, HEURISTIC)
            self.st["calls_resolved"] += 1

    def _encl_type(self, decl: Decl | None) -> Decl | None:
        if decl is None:
            return None
        if decl.kind == "class":
            return decl
        return self.types.get(decl.cls) if decl.cls else None

    def _targets(self, name: str, recv: str | None, decl: Decl | None) -> list[Decl]:
        cls = self._encl_type(decl)
        if recv is None or recv in ("self", "Self", "super"):
            if cls is not None:
                r = [d for d in self._member(cls, name) if d.kind != "class"]
                if r:
                    return r
            if name[:1].isupper():
                t = self.types.get(name)
                return [t] if t is not None else []
            cands = [d for d in self.by_name.get(name, []) if d.kind == "function"]
            if len(cands) > 1 and len({d.id.split("@")[0] for d in cands}) == 1:
                # one function defined per `#if os(...)` branch: the call reaches the base definition, and the
                # platform pass links its sibling variants (attrs.platform_variant_of)
                return [min(cands, key=lambda d: ("@" in d.id, d.line))]
            return cands if len(cands) == 1 else []
        rname = re.sub(r"[?!]|\(.*\)$", "", recv).split(".")[-1].strip()
        tyname = None
        if decl is not None:
            tyname = decl.types.get(rname)
        if tyname is None and cls is not None:
            tyname = cls.types.get(rname)
        if tyname is None and rname[:1].isupper():
            tyname = rname
        if tyname:
            tc = self.types.get(tyname)
            if tc is not None:
                return [d for d in self._member(tc, name) if d.kind != "class"]
            return []
        cands = [d for d in self.by_name.get(name, []) if d.kind == "method"]
        return cands if len(cands) == 1 and len(name) > 3 else []

    def _hierarchy(self):
        for d in list(self.decls.values()):
            if d.kind != "class":
                continue
            for s in d.supers:
                sc = self.types.get(s)
                if sc is None:
                    if s in LIFECYCLE_BASES or s == "App":
                        self._entry_class(d, "ui_page" if s == "UIViewController" else "main", f"{s} conformance")
                    continue
                kind = "IMPLEMENTS" if self.b.nodes[sc.id].attrs.get("swift_kind") == "protocol" else "EXTENDS"
                self.b.add_edge(d.id, sc.id, kind, d.file, d.line, HEURISTIC)
                for nm, ms in self.members.get(d.fqn, {}).items():
                    for base in self.members.get(sc.fqn, {}).get(nm, []):
                        for m in ms:
                            if m is not base:
                                ek = "IMPLEMENTED_BY" if kind == "IMPLEMENTS" else "OVERRIDDEN_BY"
                                self.b.add_edge(base.id, m.id, ek, m.file, m.line, HEURISTIC)

    def _entry_class(self, d: Decl, kind: str, why: str):
        n = self.b.nodes[d.id]
        if n.entry_kind == kind == "main":          # @main App: counted once
            self.st["entries_main"] -= 1
        n.entry_kind = n.entry_kind or kind
        n.attrs["entry_reason"] = why
        for nm, ms in self.members.get(d.fqn, {}).items():
            if LIFECYCLE.match(nm):
                for m in ms:
                    self.b.add_edge(d.id, m.id, "REFERENCES_FN", m.file, m.line, EXACT, how="framework lifecycle")
        self.st[f"entries_{kind}"] += 1

    # ------------------------------------------------------------------ Moya / Fluent (whole-file facts)
    @staticmethod
    def _block(txt: str, start: int) -> str:
        """The brace-balanced block whose `{` is at or after `start`."""
        i = txt.find("{", start)
        if i < 0:
            return ""
        depth = 0
        for j in range(i, len(txt)):
            if txt[j] == "{":
                depth += 1
            elif txt[j] == "}":
                depth -= 1
                if depth == 0:
                    return txt[i:j + 1]
        return txt[i:]

    def _prop_block(self, body: str, name: str) -> str | None:
        m = re.search(r"\bvar\s+" + name + r"\s*:\s*[\w.]+\s*\{", body)
        return self._block(body, m.start()) if m else None

    def _per_case(self, block: str | None, value_rx: str) -> tuple[dict, str | None]:
        """`switch self { case .a, .b(let x): return "..." }` -> ({case: value}, default value)."""
        if not block:
            return {}, None
        out, default = {}, None
        parts = re.split(r"\n\s*(case\s+[^\n:]*?:|default\s*:)", block)
        if len(parts) == 1:
            m = re.search(value_rx, block)
            return {}, (m.group(1) if m else None)
        for head, body in zip(parts[1::2], parts[2::2]):
            m = re.search(value_rx, body)
            if not m:
                continue
            if head.startswith("default"):
                default = m.group(1)
                continue
            for c in re.findall(r"\.(\w+)", head.split("case", 1)[1]):
                out.setdefault(c, m.group(1))
        return out, default

    def _moya_targets(self, sfiles):
        """Moya `TargetType` enums: baseURL + per-case path / method -> endpoint templates per case."""
        self.moya: dict[str, dict] = {}            # enum name -> {"base", "cases": {case: (METHOD, path)}, decl}
        texts = {sf.rel: sf.src.decode("utf-8", "replace") for sf in sfiles if b"TargetType" in sf.src}
        names = set()
        for txt in texts.values():
            names.update(re.findall(r"\b(?:enum|extension|struct)\s+(\w+)\s*:[^{]*\bTargetType\b", txt))
        for name in sorted(names):
            body = ""
            for txt in texts.values():
                for m in re.finditer(r"\b(?:enum|extension)\s+" + name + r"\b[^{]*\{", txt):
                    body += self._block(txt, m.start()) + "\n"
            d = self.types.get(name)
            if d is not None:
                sf = next((s for s in sfiles if s.rel == d.file), None)
                if sf is not None:
                    lines = sf.src.decode("utf-8", "replace").split("\n")[d.line - 1:d.end]
                    body += "\n".join(lines)
            cases = re.findall(r"^\s*case\s+(\w+(?:\s*\([^)]*\))?(?:\s*,\s*\w+(?:\s*\([^)]*\))?)*)\s*$", body, re.M)
            case_names = []
            for c in cases:
                case_names += [re.match(r"\w+", x.strip()).group(0) for x in re.split(r",(?![^(]*\))", c) if x.strip()]
            bb = self._prop_block(body, "baseURL") or ""
            mb = re.search(r'URL\s*\(\s*string\s*:\s*("(?:[^"\\]|\\.)*")', bb)
            base = template(mb.group(1)) if mb else "{baseURL}"
            paths, pdef = self._per_case(self._prop_block(body, "path"), r'("(?:[^"\\]|\\.)*")')
            meths, mdef = self._per_case(self._prop_block(body, "method"), r"\.(get|post|put|delete|patch|head|options)\b")
            out = {}
            for c in dict.fromkeys(case_names or list(paths)):
                p = paths.get(c) or pdef
                if p is None:
                    continue
                out[c] = ((meths.get(c) or mdef or "get").upper(), template(p))
            if out:
                self.moya[name] = {"base": base, "cases": out, "decl": d}
                self.st["moya_targets"] += 1
        # provider variables: `let provider = MoyaProvider<GitHub>()` / `var api: MoyaProvider<GitHub>`
        self.moya_vars: dict[str, str] = {}
        for txt in (sf.src.decode("utf-8", "replace") for sf in sfiles if b"Provider<" in sf.src):
            for m in re.finditer(r"\b(\w+)\s*(?::\s*\w*Provider\s*<\s*(\w+)\s*>|=\s*\w*Provider\s*<\s*(\w+)\s*>)", txt):
                self.moya_vars.setdefault(m.group(1), m.group(2) or m.group(3))

    def _moya_call(self, name, recv, al, owner, sf, line) -> bool:
        if not self.moya or name not in ("request", "requestPublisher", "requestWithProgress") or not al:
            return False
        arg = self.t(al[0][1]) if al[0][1] is not None else ""
        m = re.match(r"\s*(?:(\w+))?\.(\w+)", arg)
        if not m:
            return False
        case, tname = m.group(2), m.group(1)
        if tname is None:
            rv = re.sub(r"\.(rx|reactive)$", "", recv or "").split(".")[-1]
            tname = self.moya_vars.get(rv)
        targets = [tname] if tname in self.moya else [t for t, v in self.moya.items() if case in v["cases"]]
        if len(targets) != 1 or case not in self.moya[targets[0]]["cases"]:
            return False
        t = self.moya[targets[0]]
        meth, path = t["cases"][case]
        self.http.append({"src": owner, "method": meth, "url": join_path(t["base"], path), "client": "moya",
                          "file": sf.rel, "line": line, "target": f"{targets[0]}.{case}"})
        self.st["moya_calls"] += 1
        return True

    def _moya_endpoints(self):
        """Every Moya case is an endpoint of its TargetType, called or not (the enum -> http edge)."""
        for name, t in self.moya.items():
            d = t["decl"]
            if d is None:
                continue
            for case, (meth, path) in t["cases"].items():
                self.http.append({"src": d.id, "method": meth, "url": join_path(t["base"], path), "client": "moya",
                                  "file": d.file, "line": d.line, "target": f"{name}.{case}", "declared": True})

    def _fluent_models(self, sfiles):
        """Fluent `Model` classes (`static let schema = "todos"`) -> table nodes (MAPS_TO_TABLE)."""
        self.fluent: dict[str, str] = {}
        if not any(b"Fluent" in sf.src for sf in sfiles):
            return
        bysrc = {sf.rel: sf.src.decode("utf-8", "replace").split("\n") for sf in sfiles if b"schema" in sf.src}
        for d in list(self.types.values()):
            if "Model" not in d.supers or d.file not in bysrc:
                continue
            body = "\n".join(bysrc[d.file][d.line - 1:d.end])
            m = re.search(r'static\s+(?:let|var)\s+schema\s*(?::\s*String)?\s*(?:=\s*|\{\s*(?:return\s+)?)"([^"]+)"', body)
            if not m:
                continue
            self.fluent[d.name] = m.group(1)
            tid = self.b.add_node("table", m.group(1), lang="sql", attrs={"via": "fluent"})
            self.b.add_edge(d.id, tid, "MAPS_TO_TABLE", d.file, d.line, EXACT, via="Fluent schema")
            self.st["fluent_models"] += 1

    def _local_types(self, decl, sf) -> dict:
        """`let todo = Todo(...)`, `guard let todo = try await Todo.find(...)`, `let t: Todo = ...` in a function."""
        key = (decl.id, decl.line) if decl is not None else None
        if key is None:
            return {}
        cache = self.__dict__.setdefault("_lt_cache", {})
        if key not in cache:
            lines = sf.src.decode("utf-8", "replace").split("\n")[decl.line - 1:decl.end]
            out = {}
            for m in re.finditer(r"\b(?:let|var)\s+(\w+)\s*(?::\s*(\w+))?\s*=\s*(?:try\s*[?!]?\s+)?(?:await\s+)?([A-Z]\w*)\s*[.(]",
                                 "\n".join(lines)):
                out.setdefault(m.group(1), m.group(2) or m.group(3))
            cache[key] = out
        return cache[key]

    def _fluent_call(self, name, recv, owner, decl, sf, line, c):
        if not self.fluent or recv is None:
            return
        rv = re.sub(r"[?!]|\(.*\)$", "", recv).split(".")[-1].strip()
        if rv in self.fluent and name in ("query", "find"):                    # Todo.query(on:) / Todo.find(id, on:)
            whole = c
            while whole.parent is not None and whole.parent.type in ("navigation_expression", "call_expression",
                                                                     "call_suffix", "await_expression", "try_expression"):
                whole = whole.parent
            chain = self.t(whole)
            kind = "WRITES_TABLE" if re.search(r"\.(delete|update|set|create)\s*\(", chain) else "READS_TABLE"
            tid = f"table:{self.fluent[rv]}"
            self.b.add_edge(owner, tid, kind, sf.rel, line, RESOLVED, via=f"{rv}.{name}")
            self.st["fluent_queries"] += 1
            return
        if name in ("save", "create", "update", "delete", "forceDelete", "restore"):  # todo.save(on: req.db)
            ty = (decl.types.get(rv) if decl is not None else None) or self._local_types(decl, sf).get(rv)
            if ty in self.fluent:
                self.b.add_edge(owner, f"table:{self.fluent[ty]}", "WRITES_TABLE", sf.rel, line, RESOLVED,
                                via=f"{ty}.{name}")
                self.st["fluent_writes"] += 1
            return

    def _fluent_migrations(self, sfiles):
        """`database.schema("todos")....create()` / `.delete()` / `.update()` in a Migration -> WRITES_TABLE
        (via migration) from the prepare / revert method."""
        for sf in sfiles:
            if b".schema(" not in sf.src:
                continue
            for d in self._fd.get(sf.rel, ()):
                if d.kind != "method" or d.name not in ("prepare", "revert"):
                    continue
                body = "\n".join(sf.src.decode("utf-8", "replace").split("\n")[d.line - 1:d.end])
                for m in re.finditer(r'\.schema\s*\(\s*"([^"]+)"\s*\)', body):
                    tail = body[m.end():m.end() + 600]
                    op = re.search(r"\.(create|update|delete)\s*\(\s*\)", tail)
                    tid = self.b.add_node("table", m.group(1), lang="sql", attrs={"via": "fluent"})
                    self.b.add_edge(d.id, tid, "WRITES_TABLE", d.file, d.line, EXACT,
                                    via=f"migration {op.group(1) if op else 'schema'}")
                    self.st["fluent_migrations"] += 1

    def _emit_http(self):
        for r in self.http:
            origin, path = split_url(r["url"])
            okind = "api" if origin is None else ("unknown" if origin.startswith("{") else "other")
            key = f"{r['method']} {path}" if okind in ("api", "unknown") else f"{r['method']} {origin}{path}"
            nid = self.b.add_node("http", key, key, fqn=key, lang="swift",
                                  attrs={"method": r["method"], "path": path, "client": r["client"], "origin": origin,
                                         "origin_kind": okind})
            self.b.add_edge(r["src"], nid, "HTTP_CALLS", r["file"], r["line"], HEURISTIC if okind != "api" else EXACT,
                            client=r["client"], url=r["url"], origin=origin,
                            **({"target": r["target"]} if "target" in r else {}),
                            **({"how": "moya target"} if r.get("declared") else {}))
            self.st[f"http_{r['client']}"] += 1

    def _page(self, view: Decl, how: str) -> str:
        key = f"swift:{view.name}"
        pid = self.b.add_node("page", key, name=view.name, fqn=key, file=view.file, line=view.line, lang="swift",
                              entry_kind="ui_page", attrs={"route": view.name, "via": how, "view": view.fqn})
        n = self.b.nodes[pid]
        n.entry_kind = n.entry_kind or "ui_page"
        m = self.members.get(view.fqn, {})
        body = m.get("body") or m.get("viewDidLoad") or m.get("loadView")
        self.b.add_edge(pid, body[0].id if body else view.id, "ROUTES_TO", view.file, view.line, EXACT)
        return pid

    def _is_controller(self, v: Decl, depth: int = 0) -> bool:
        if any(s.endswith("ViewController") for s in v.supers):
            return True
        return depth < 4 and any(self._is_controller(self.types[s], depth + 1) for s in v.supers if s in self.types)

    def _link_navs(self):
        pages = {}
        for nav in self.navs:
            owner, tname, how, file, line = nav[:5]
            page_only = nav[5] if len(nav) > 5 else False
            v = self.types.get(tname)
            if v is None or not ("body" in self.members.get(v.fqn, {}) or self._is_controller(v)):
                continue
            if v.fqn not in pages:
                pages[v.fqn] = self._page(v, how)
                self.st["swiftui_pages"] += 1
            if not page_only:
                self.b.add_edge(owner, pages[v.fqn], "NAVIGATES_TO", file, line, EXACT, how=how)
                self.st["navigations"] += 1

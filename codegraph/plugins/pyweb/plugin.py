"""FastAPI / Starlette and Flask routes (framework plugins on the Python plugin; stdlib `ast`, no code runs).

Applications and routers are module- or function-level assignments of `FastAPI(...)`, `APIRouter(prefix=...)`,
`Starlette(routes=[...])`, `starlette.routing.Router(...)`, `Flask(__name__)` and `Blueprint(name, __name__,
url_prefix=...)` (subclasses too). Routes:
  * FastAPI / Starlette: `@x.get/post/put/patch/delete/head/options/trace(path)`, `@x.api_route(path, methods=)`,
    `@x.websocket(path)`, `@x.route(path, methods=)`, `x.add_api_route / add_route / add_websocket_route(path, fn)`,
    `Route(path, fn, methods=)` / `WebSocketRoute` / `Mount(path, routes=[...] | app=...)` lists passed as
    `routes=`; mounting through `x.include_router(child, prefix=)`, `x.mount(path, child)`;
  * Flask: `@x.route(rule, methods=, endpoint=)`, `@x.get/post/...(rule)`, `x.add_url_rule(rule, endpoint,
    view_func, methods=)` (a `View.as_view("name")` view_func routes to its get / post / ... methods), blueprints
    mounted with `register_blueprint(bp, url_prefix=)` (nested blueprints too).
Path, prefix and endpoint strings are evaluated statically (pyweb/values.py: literals, f-strings, module constants,
settings class defaults such as `settings.API_V1_STR`); a part that cannot be evaluated becomes `{?}`. Flask
converters `<int:id>` and Starlette `{id:int}` become `{id}`. Route names: FastAPI `name=` or the function name,
Flask `<blueprint>.<endpoint>` (for `url_for`). Each route is an http_route entry point (`route:<METHOD> <uri>`) with
ROUTES_TO to its handler; FastAPI `Depends(...)` / `Security(...)` dependencies (parameters, `Annotated` aliases,
`dependencies=[...]` on the decorator, router, app or include) and handler decorators (`@login_required`) are
recorded as attrs.access for `cg routes`, with what each dependency checks (dep_checks: statuses raised, security
schemes, reads, nested dependencies). Test-client requests then link to these routes (python/tests.py).

Objects not assigned in place: a function parameter used as an app / router / blueprint is a param object, bound to
the objects passed at call sites (`register(app)`) or returned by the pytest fixture of that name; calls to a
function that returns an object (`create_app()`) resolve to it. Also read: fastapi-utils `@cbv` / `InferringRouter`,
classy-fastapi `Routable`, flask-restful / flask-restx `Api` / `Namespace` resources, `MethodView.methods`,
endpoint-only `add_url_rule`, `@x.endpoint`, `x.view_functions[...] = f`, werkzeug `url_map.add(Rule | Submount)`,
Flask's built-in static route, Starlette `Host` / `x.host()` (a `host` attribute, not a path prefix).
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict

from ...core.model import EXACT, RESOLVED
from ...core.plugin import FrameworkPlugin, GraphBuilder, Project
from ...core.paths import rules as path_rules
from ... import presets
from ..python.plugin import ClassInfo, Ctx, FuncInfo, PyProgram, dotted, kwarg, walk_body
from .values import UNKNOWN, str_value

VERBS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
VIEW_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
# constructor (last name) -> (framework, kind); matched on the imported name (fastapi.*, starlette.*, flask.*)
FACTORIES = {"FastAPI": ("fastapi", "app"), "APIRouter": ("fastapi", "router"), "Starlette": ("starlette", "app"),
             "Router": ("starlette", "router"), "Flask": ("flask", "app"), "Blueprint": ("flask", "blueprint"),
             "InferringRouter": ("fastapi", "router"),                   # fastapi-utils / fastapi-restful
             "Api": ("flask", "api"), "Namespace": ("flask", "namespace")}  # flask-restful / flask-restx
FACTORY_PKGS = ("fastapi", "starlette", "flask", "fastapi_utils", "fastapi_restful", "flask_restful", "flask_restx")
# registration calls that only exist on one framework's objects (used to tell what a parameter `app` is)
FLASK_ONLY = {"add_url_rule", "register_blueprint", "endpoint", "add_resource", "add_namespace"}
FASTAPI_ONLY = {"include_router", "add_api_route", "api_route", "add_api_websocket_route", "websocket"}
REG_CALLS = {"add_url_rule", "register_blueprint", "add_resource", "add_namespace", "include_router", "add_api_route",
             "add_route", "add_websocket_route", "add_api_websocket_route", "mount", "host"}
ROUTE_DECOS = set(VERBS) | {"route", "api_route", "websocket", "websocket_route", "endpoint"}
# FastAPI security schemes: a dependency on one of these rejects requests without credentials
SECURITY_SCHEMES = {"OAuth2PasswordBearer", "OAuth2AuthorizationCodeBearer", "OAuth2", "HTTPBearer", "HTTPBasic",
                    "HTTPDigest", "APIKeyHeader", "APIKeyCookie", "APIKeyQuery", "OpenIdConnect"}
REJECT_STATUS = {401, 403}
PKG = {"fastapi": ("fastapi", "starlette"), "flask": ("flask",)}
DEP_FUNCS = {"Depends", "Security"}
SKIP_DECOS = {"staticmethod", "classmethod", "property", "wraps", "cache", "lru_cache"}
MANIFESTS = ("requirements.txt", "requirements.in", "requirements/base.txt", "requirements/prod.txt", "pyproject.toml",
             "setup.py", "setup.cfg", "Pipfile")
TEST_FILE = re.compile(r"(test_.*|.*_test|conftest)\.py$")


def _mentions(project: Project, pkgs: tuple[str, ...]) -> bool:
    """A dependency manifest (root or two levels down) or an import names one of `pkgs`."""
    dep = re.compile(r"(?im)^\s*[\"']?(" + "|".join(pkgs) + r")\b|[\"'](" + "|".join(pkgs) + r")[\[<>=~!\"' ]")
    imp = re.compile(r"(?m)^\s*(from|import)\s+(" + "|".join(pkgs) + r")\b")
    root = project.root
    rules = path_rules(project, "python")
    test_dirs = set(presets.values("python", "test_dirs", default=[]))
    for pat in ("", "*/", "*/*/"):
        for name in MANIFESTS:
            for p in root.glob(pat + name):
                if any(part in rules.names for part in p.relative_to(root).parts[:-1]):
                    continue
                try:
                    if dep.search(p.read_text(errors="replace")):
                        return True
                except OSError:
                    continue
    n = 0
    stack = [root]
    while stack and n < 3000:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.is_dir():
                r = p.relative_to(root).as_posix()
                if not rules.skip(r.rpartition("/")[0], p.name, dot=True) and (p.name not in test_dirs or rules.on_include_path(r)):
                    stack.append(p)
            elif p.suffix == ".py" and not TEST_FILE.match(p.name):   # test fixtures quote `from flask import ...`
                n += 1
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        if imp.search(fh.read(20000)):
                            return True
                except OSError:
                    continue
    return False


def flask_path(rule: str) -> str:
    return re.sub(r"<(?:[^:<>]+:)?(\w+)>", r"{\1}", rule)


def starlette_path(p: str) -> str:
    return re.sub(r"\{(\w+):[^{}]+\}", r"{\1}", p)


def join(*parts: str) -> str:
    out = ""
    for p in parts:
        if not p:
            continue
        out = out.rstrip("/") + "/" + p.lstrip("/") if out else p
    out = out or "/"
    return out if out.startswith("/") else "/" + out


class Obj:
    """An application or router object."""

    def __init__(self, key, fw, kind, call, mod, func, line):
        self.key, self.fw, self.kind, self.call, self.mod, self.func, self.line = key, fw, kind, call, mod, func, line
        self.prefix = ""          # APIRouter(prefix=) / Blueprint(url_prefix=)
        self.name = None          # blueprint name
        self.deps: list[dict] = []
        self.children: list[dict] = []   # {obj, prefix (None: child's own), deps, line, file, host}
        self.parents = 0
        self.host = None          # Starlette Host(...) / Flask host= (host matching)
        self.subdomain = None     # Flask Blueprint(subdomain=)
        self.param = None         # parameter name, for an object received as a parameter / fixture


class Routes:
    def __init__(self, prog: PyProgram, frameworks: set[str]):
        self.prog = prog
        self.fws = frameworks
        self.objs: dict = {}
        self.ops: list[dict] = []
        self.unresolved: list[str] = []
        self.endpoints: dict = {}         # (obj key, endpoint name) -> handler, from @x.endpoint / view_functions[...]
        self.stats = defaultdict(int)
        self._ret_seen: set = set()

    # ------------------------------------------------------------------ objects
    def _factory(self, call, ctx):
        if not isinstance(call, ast.Call):
            return None
        try:
            t = self.prog.infer(call.func, ctx)
        except RecursionError:
            return None
        names = []
        if t and t[0] == "ext":
            names = [t[1]]
        elif t and t[0] == "type":
            names = [t[1].qual] + list(self.prog.lineage(t[1]))
        elif t is None and isinstance(call.func, ast.Name) and ctx.func is not None:
            # a class defined in the same function (`class MyFlask(flask.Flask): ...; app = MyFlask(__name__)`)
            for sub in walk_body(ctx.func.node):
                if isinstance(sub, ast.ClassDef) and sub.name == call.func.id:
                    for b in sub.bases:
                        try:
                            bt = self.prog.infer(b, ctx)
                        except RecursionError:
                            bt = None
                        if bt and bt[0] == "ext":
                            names.append(bt[1])
                        elif bt and bt[0] == "type":
                            names += [bt[1].qual] + list(self.prog.lineage(bt[1]))
                    break
        for n in names:
            last, top = n.rsplit(".", 1)[-1], n.split(".", 1)[0]
            f = FACTORIES.get(last)
            if f and top in FACTORY_PKGS and f[0] in self.fws:
                return f
        return None

    def _scopes(self):
        """(module, function or None, the nodes of that scope): module level without def / class bodies, then each
        def's own body."""
        for m in self.prog.modules.values():
            yield m, None, list(_module_level(m.tree))
        for f in self.prog.funcs.values():
            yield f.module, f, list(walk_body(f.node))

    def collect(self):
        prog = self.prog
        pending_ns = []
        for m, f, stmts in self._scopes():
            ctx = Ctx(m, f, f.cls if f else None)
            for st in stmts:
                if not isinstance(st, (ast.Assign, ast.AnnAssign)) or st.value is None:
                    continue
                tgt = st.targets[0] if isinstance(st, ast.Assign) else st.target
                if not isinstance(tgt, ast.Name):
                    continue
                key = (m.name, f.qual if f else "", tgt.id)
                fk = self._factory(st.value, ctx)
                if not fk:
                    pending_ns.append((key, st, ctx))
                    continue
                o = self.objs[key] = Obj(key, fk[0], fk[1], st.value, m, f, st.lineno)
                c = st.value
                if fk[1] == "namespace":         # flask-restx Namespace(name, path=): default path /<name>
                    nm = str_value(prog, c.args[0] if c.args else kwarg(c, "name"), ctx)
                    pe = kwarg(c, "path")
                    o.prefix = (str_value(prog, pe, ctx) or UNKNOWN) if pe is not None else ("/" + nm if nm else UNKNOWN)
                elif fk[0] == "flask":
                    o.prefix = str_value(prog, kwarg(c, "url_prefix") or kwarg(c, "prefix"), ctx) or ""
                    o.subdomain = str_value(prog, kwarg(c, "subdomain"), ctx)
                    if fk[1] == "blueprint":
                        o.name = str_value(prog, c.args[0] if c.args else kwarg(c, "name"), ctx)
                else:
                    pe = kwarg(c, "prefix")
                    o.prefix = (str_value(prog, pe, ctx) or UNKNOWN) if pe is not None else ""
                o.deps = self.deps_of(kwarg(c, "dependencies"), ctx, "router dependencies")
        for key, st, ctx in pending_ns:
            self._namespace(key, st, ctx)
        self._param_objects()
        self._routables()
        for o in list(self.objs.values()):        # flask-restful / restx `Api(app)` / `Api(bp)`
            if o.kind == "api" and o.call is not None and (o.call.args or kwarg(o.call, "app") is not None):
                parent = self.obj_of(o.call.args[0] if o.call.args else kwarg(o.call, "app"),
                                     Ctx(o.mod, o.func, o.func.cls if o.func else None))
                if parent is not None and parent is not o:
                    self._attach(parent, o, None, o.line, o.mod.file)
        # mounts and calls
        pfuncs = {k[1].rsplit(".", 1)[-1] for k, o in self.objs.items() if o.param}
        for m, f, stmts in self._scopes():
            ctx = Ctx(m, f, f.cls if f else None)
            for st in stmts:
                if isinstance(st, ast.Call) and isinstance(st.func, ast.Attribute):
                    self._call(st, ctx)
                if isinstance(st, ast.Call) and pfuncs and (dotted(st.func) or "").rsplit(".", 1)[-1] in pfuncs:
                    self._param_alias(st, ctx)
                if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Subscript):
                    self._view_functions(st, ctx)
        self._fixture_aliases()
        self._static()
        for o in list(self.objs.values()):
            rl = kwarg(o.call, "routes") if o.call is not None else None
            if rl is not None:
                self._route_list(o, rl, Ctx(o.mod, o.func, o.func.cls if o.func else None), "")
        # decorators (flask-restx `@ns.route("/x")` decorates a Resource class)
        for f in list(prog.funcs.values()) + list(prog.classes.values()):
            for d in f.decorators:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                    self._decorator(f, d)
            if isinstance(f, ClassInfo):
                continue
            # a view defined inside an app factory (`def create_app(): ... @app.route("/hello") def hello()`): nested
            # defs collapse into the enclosing def, which becomes the handler
            for sub in walk_body(f.node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for d in sub.decorator_list:
                        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                            self._decorator(f, d, nested=sub.name, node=sub)

    def obj_of(self, e, ctx, depth=0):
        """The application / router object an expression names (local, module-level, imported, `mod.router`), also
        through a call to a function that returns one (`app = create_app()`, `app.mount("/x", make_sub())`)."""
        if e is None or depth > 4:
            return None
        if isinstance(e, ast.Name):
            if ctx.func is not None and (ctx.mod.name, ctx.func.qual, e.id) in self.objs:
                return self.objs[(ctx.mod.name, ctx.func.qual, e.id)]
            if ctx.func is not None:
                lv = self.prog.local_vars(ctx).get(e.id)
                if lv:
                    for v, _a, k in lv:
                        if k == "assign" and isinstance(v, ast.Call):
                            o = self.obj_of(v, ctx, depth + 1)
                            if o is not None:
                                return o
                    return None
            if (ctx.mod.name, "", e.id) in self.objs:
                return self.objs[(ctx.mod.name, "", e.id)]
            r = self.prog.resolve_name(ctx.mod, e.id)
            if r and r[0] == "var":
                o = self.objs.get((r[1].name, "", r[2]))
                if o is not None:
                    return o
                for v, _ln, _a in r[1].vars.get(r[2], ())[:2]:
                    if isinstance(v, ast.Call):
                        o = self.obj_of(v, Ctx(r[1], None, None), depth + 1)
                        if o is not None:
                            return o
            return None
        if isinstance(e, ast.Call):
            try:
                t = self.prog.infer(e.func, ctx)
            except RecursionError:
                return None
            if t and t[0] in ("func", "bound") and isinstance(t[1], FuncInfo):
                return self.returned_obj(t[1], depth + 1)
            return None
        if isinstance(e, ast.Attribute):
            try:
                t = self.prog.infer(e.value, ctx)
            except RecursionError:
                return None
            if t and t[0] == "mod":
                return self.objs.get((t[1].name, "", e.attr))
            if t and t[0] == "inst" and e.attr == "router":          # classy-fastapi `Items().router`
                return self.objs.get((t[1].module.name, "", t[1].qual + ".router"))
        return None

    def returned_obj(self, f: FuncInfo, depth=0):
        """The application / router object a function returns or yields (app factories, pytest fixtures)."""
        if depth > 4 or f.qual in self._ret_seen:
            return None
        self._ret_seen.add(f.qual)
        try:
            ctx = Ctx(f.module, f, f.cls)
            for sub in walk_body(f.node):
                if isinstance(sub, (ast.Return, ast.Yield)) and isinstance(sub.value, (ast.Name, ast.Call)):
                    o = self.obj_of(sub.value, ctx, depth + 1)
                    if o is not None:
                        return o
            return None
        finally:
            self._ret_seen.discard(f.qual)

    def _attach(self, parent, child, prefix, line, file, mount=False, deps=(), **ext):
        """Register `child` under `parent` (a blueprint may be registered twice, under other prefixes / names)."""
        ext = {k: v for k, v in ext.items() if v}
        if child is parent or any(c["obj"] is child and c["prefix"] == prefix and all(c.get(k) == v for k, v in ext.items())
                                  for c in parent.children):
            return
        parent.children.append({"obj": child, "prefix": prefix, "line": line, "file": file, "deps": list(deps),
                                **({"mount": True} if mount else {}), **ext})
        child.parents += 1

    # ------------------------------------------------------------------ objects received as parameters
    def _module_fw(self, m) -> str | None:
        """'flask' / 'fastapi' from the framework a module imports, else the only one the project imports."""
        def fws(tree):
            out = set()
            for n in ast.walk(tree):
                names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    [n.module] if isinstance(n, ast.ImportFrom) and n.module and not n.level else []
                for x in names:
                    top = x.split(".")[0]
                    if top.startswith("flask"):
                        out.add("flask")
                    elif top in ("fastapi", "starlette") or top.startswith("fastapi_"):
                        out.add("fastapi")
            return out
        own = fws(m.tree)
        if len(own) == 1:
            return next(iter(own))
        if own:
            return None
        if not hasattr(self, "_proj_fws"):
            self._proj_fws = set()
            for mm in self.prog.modules.values():
                self._proj_fws |= fws(mm.tree)
        return next(iter(self._proj_fws)) if len(self._proj_fws) == 1 else None

    def _param_kind(self, f: FuncInfo, p, used: set):
        ann = p.annotation
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            last = ann.value.rsplit(".", 1)[-1]
        else:
            last = (dotted(ann) or "").rsplit(".", 1)[-1] if ann is not None else ""
        if last in FACTORIES:
            fk = FACTORIES[last]
            return fk if fk[0] in self.fws or (fk[0] == "starlette" and "fastapi" in self.fws) else None
        if last not in ("", "Any", "object"):
            return None                       # typed as something else
        if used & FLASK_ONLY:
            fw = "flask"
        elif used & FASTAPI_ONLY:
            fw = "fastapi"
        else:
            fw = self._module_fw(f.module)
        if fw is None or fw not in self.fws:
            return None
        n = p.arg.lower()
        if fw == "flask" and (n in ("bp", "blueprint") or n.endswith(("_bp", "_blueprint"))):
            return (fw, "blueprint")
        if fw == "flask" and used & {"add_resource", "add_namespace"}:
            return (fw, "api")
        return (fw, "router" if "router" in n else "app")

    def _param_objects(self):
        """`def register_routes(app): @app.get(...)`, `def init_app(app): app.add_url_rule(...)`, and tests that add
        routes to the `app` fixture they receive: the parameter becomes an object of its own (an app unless its
        name or annotation says router / blueprint); calls that pass a known object and pytest fixtures of the same
        name attach it to that object (_param_alias, _fixture_aliases)."""
        for f in list(self.prog.funcs.values()):
            a = f.node.args
            params = {p.arg: p for p in a.posonlyargs + a.args + a.kwonlyargs if p.arg not in ("self", "cls")}
            if not params:
                continue
            uses = defaultdict(set)
            for sub in walk_body(f.node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and \
                        isinstance(sub.func.value, ast.Name) and sub.func.value.id in params and sub.func.attr in REG_CALLS:
                    uses[sub.func.value.id].add(sub.func.attr)
                elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    for d in sub.decorator_list:
                        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and \
                                isinstance(d.func.value, ast.Name) and d.func.value.id in params and \
                                d.func.attr in ROUTE_DECOS:
                            uses[d.func.value.id].add(d.func.attr)
            for pn, used in uses.items():
                key = (f.module.name, f.qual, pn)
                if key in self.objs:
                    continue
                fk = self._param_kind(f, params[pn], used)
                if fk is None:
                    continue
                o = self.objs[key] = Obj(key, fk[0], fk[1], None, f.module, f, f.line)
                o.param = pn
                self.stats["param_objects"] += 1

    def _param_alias(self, c: ast.Call, ctx):
        """`register_routes(app)` with a known `app`: the function's parameter object hangs under it."""
        try:
            t = self.prog.infer(c.func, ctx)
        except RecursionError:
            return
        if not t or t[0] not in ("func", "bound") or not isinstance(t[1], FuncInfo):
            return
        fn = t[1]
        pobjs = {k[2]: o for k, o in self.objs.items() if o.param and k[0] == fn.module.name and k[1] == fn.qual}
        if not pobjs:
            return
        a = fn.node.args
        names = [x.arg for x in a.posonlyargs + a.args]
        if names and names[0] in ("self", "cls") and fn.cls is not None:
            names = names[1:]
        bind = dict(zip(names, c.args))
        bind.update({k.arg: k.value for k in c.keywords if k.arg})
        for pn, o in pobjs.items():
            parent = self.obj_of(bind.get(pn), ctx)
            if parent is not None and parent is not o and not parent.param:
                self._attach(parent, o, "", c.lineno, ctx.mod.file)
                self.stats["param_objects_bound"] += 1

    def _fixture_aliases(self):
        """A test's `app` parameter is the pytest fixture of that name (same module, else a conftest.py up the tree)
        when that fixture returns a known object."""
        fixtures = defaultdict(list)
        for f in self.prog.funcs.values():
            if f.cls is None and any((dotted(d.func if isinstance(d, ast.Call) else d) or "").endswith("fixture")
                                     for d in f.decorators):
                fixtures[f.name].append(f)
        for o in list(self.objs.values()):
            if not o.param or o.parents or o.param not in fixtures:
                continue
            fdir = o.mod.file.rpartition("/")[0]
            best = None
            for fx in fixtures[o.param]:
                if fx.module is o.mod:
                    best = fx
                    break
                d = fx.module.file.rpartition("/")[0]
                if fx.module.file.endswith("conftest.py") and (fdir == d or fdir.startswith(d + "/") or not d):
                    if best is None or len(d) > len(best.module.file):
                        best = fx
            parent = self.returned_obj(best) if best is not None else None
            if parent is not None and parent is not o:
                self._attach(parent, o, "", o.line, o.mod.file)
                self.stats["param_objects_from_fixture"] += 1

    def _namespace(self, key, st, ctx):
        """flask-restx `ns = api.namespace("todos", path="/todos")`."""
        c = st.value
        if not (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "namespace"):
            return
        api = self.obj_of(c.func.value, ctx)
        if api is None or api.kind != "api":
            return
        o = self.objs[key] = Obj(key, "flask", "namespace", c, ctx.mod, ctx.func, st.lineno)
        nm = str_value(self.prog, c.args[0] if c.args else kwarg(c, "name"), ctx)
        pe = kwarg(c, "path")
        o.prefix = (str_value(self.prog, pe, ctx) or UNKNOWN) if pe is not None else ("/" + nm if nm else UNKNOWN)
        self._attach(api, o, None, st.lineno, ctx.mod.file)

    def _routables(self):
        """classy-fastapi: methods of a `Routable` subclass decorated with `@get("/x")` / `@post(...)` form a router,
        `Items().router`, included like any other (`app.include_router(items.router)`)."""
        if "fastapi" not in self.fws:
            return
        for c in list(self.prog.classes.values()):
            if not self.prog.subclass_of(c, "Routable"):
                continue
            key = (c.module.name, "", c.qual + ".router")
            o = None
            for fn in c.methods.values():
                for d in fn.decorators:
                    if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in VERBS + ("api_route",)):
                        continue
                    if o is None:
                        o = self.objs[key] = Obj(key, "fastapi", "router", None, c.module, None, c.line)
                    ctx = Ctx(c.module, None, None)
                    pe = d.args[0] if d.args else kwarg(d, "path")
                    meth = [d.func.id.upper()] if d.func.id in VERBS else self._methods(kwarg(d, "methods"), ctx)
                    self._op(o, str_value(self.prog, pe, ctx) if pe is not None else "", meth, fn, ctx, d.lineno,
                             f"@{d.func.id} (Routable)", str_value(self.prog, kwarg(d, "name"), ctx),
                             self.deps_of(kwarg(d, "dependencies"), ctx, "route dependencies"))

    def _view_functions(self, st: ast.Assign, ctx):
        """`app.view_functions["index"] = index`: the view of an endpoint registered without one."""
        t = st.targets[0]
        if not (isinstance(t.value, ast.Attribute) and t.value.attr == "view_functions"):
            return
        obj = self.obj_of(t.value.value, ctx)
        nm = str_value(self.prog, t.slice, ctx)
        if obj is not None and nm:
            h = self.handler(st.value, ctx) or self._local_def(st.value, ctx)
            if h is not None:
                self.endpoints[(obj.key, nm)] = h

    def _local_def(self, e, ctx):
        """A def nested in the current function (it collapses into that function, which handles the route)."""
        if ctx.func is not None and isinstance(e, ast.Name):
            for sub in walk_body(ctx.func.node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == e.id:
                    return ctx.func
        return None

    def deps_of(self, e, ctx, via) -> list[dict]:
        out = []
        if isinstance(e, (ast.List, ast.Tuple)):
            for x in e.elts:
                ent = self.dep_entry(x, ctx, via)
                if ent:
                    out.append(ent)
        return out

    @staticmethod
    def dep_expr(x):
        """`Depends(get_current_user)` / `Security(scheme, scopes=...)` -> the dependency expression."""
        if isinstance(x, ast.Call) and (dotted(x.func) or "").rsplit(".", 1)[-1] in DEP_FUNCS:
            return x.args[0] if x.args else kwarg(x, "dependency")
        return None

    def dep_name(self, x, ctx, depth=0):
        """`Depends(get_current_user)` / `Security(scheme, scopes=...)` -> 'get_current_user'."""
        a = self.dep_expr(x)
        return (dotted(a) or "").rsplit(".", 1)[-1] or None if a is not None else None

    def dep_entry(self, x, ctx, via) -> dict | None:
        n = self.dep_name(x, ctx)
        if not n:
            return None
        ent = {"name": n, "via": via}
        chk = self.dep_checks(self.dep_expr(x), ctx)
        if chk:
            ent["checks"] = chk
        return ent

    def ann_deps(self, ann, ctx, depth=0) -> list[tuple]:
        """`Depends(...)` calls in a parameter annotation, with the context they live in: `Annotated[User,
        Depends(f)]`, or a module alias of one (`CurrentUser = Annotated[User, Depends(get_current_user)]`)."""
        if ann is None or depth > 3:
            return []
        if isinstance(ann, ast.Subscript) and (dotted(ann.value) or "").rsplit(".", 1)[-1] == "Annotated":
            sl = ann.slice
            elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
            return [(x, ctx) for x in elts[1:] if self.dep_expr(x) is not None]
        if isinstance(ann, (ast.Name, ast.Attribute)):
            r = None
            if isinstance(ann, ast.Name):
                r = self.prog.resolve_name(ctx.mod, ann.id)
            else:
                try:
                    t = self.prog.infer(ann.value, ctx)
                except RecursionError:
                    t = None
                if t and t[0] == "mod":
                    r = ("var", t[1], ann.attr) if ann.attr in t[1].vars else None
            if r and r[0] == "var":
                for v, _ln, _a in r[1].vars.get(r[2], ())[:1]:
                    return self.ann_deps(v, Ctx(r[1], None, None), depth + 1)
        return []

    def _param_deps(self, fn: FuncInfo) -> list[tuple]:
        """(parameter, Depends call, ctx) for every dependency a function declares in its parameters."""
        a = fn.node.args
        params = a.posonlyargs + a.args + a.kwonlyargs
        defaults = [None] * (len(a.posonlyargs + a.args) - len(a.defaults)) + list(a.defaults) + list(a.kw_defaults)
        ctx = Ctx(fn.module, fn, fn.cls)
        out = []
        for p, dv in zip(params, defaults):
            calls = ([(dv, ctx)] if dv is not None and self.dep_expr(dv) is not None else []) + self.ann_deps(p.annotation, ctx)
            out += [(p, x, cx) for x, cx in calls]
        return out

    def _value_of(self, e, ctx):
        """The assigned value of a local or module-level name (one assignment)."""
        if isinstance(e, ast.Name):
            if ctx.func is not None:
                lv = self.prog.local_vars(ctx).get(e.id)
                if lv:
                    vals = [v for v, _a, k in lv if k == "assign" and v is not None]
                    return (vals[0], ctx) if len(vals) == 1 else (None, ctx)
            r = self.prog.resolve_name(ctx.mod, e.id)
            if r and r[0] == "var":
                vals = [v for v, _ln, _a in r[1].vars.get(r[2], ()) if v is not None]
                return (vals[0], Ctx(r[1], None, None)) if len(vals) == 1 else (None, ctx)
        return None, ctx

    @staticmethod
    def _status(e) -> int | None:
        if isinstance(e, ast.Constant) and isinstance(e.value, int):
            return e.value
        m = re.search(r"HTTP_(\d{3})", dotted(e) or "")
        return int(m.group(1)) if m else None

    def dep_checks(self, a, ctx, depth=0, seen=None) -> dict | None:
        """What a dependency checks, read from its source: the HTTP statuses it raises (`HTTPException(401)`,
        `status.HTTP_403_FORBIDDEN`, an exception held in a variable), the FastAPI security schemes it rests on
        (`OAuth2PasswordBearer`, `HTTPBearer`, `APIKeyHeader` ...; these reject requests without credentials unless
        `auto_error=False`), the headers / cookies / request it reads, and the dependencies nested in its own
        parameters (followed, up to 4 levels). effect: 'rejects' (401 / 403 here or in a nested dependency),
        'raises' (other statuses only), 'reads' (no rejection found)."""
        if a is None or depth > 4:
            return None
        seen = seen or set()
        val, vctx = self._value_of(a, ctx)
        if isinstance(val, ast.Call):            # oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
            last = (dotted(val.func) or "").rsplit(".", 1)[-1]
            if last in SECURITY_SCHEMES:
                ae = kwarg(val, "auto_error")
                rej = not (isinstance(ae, ast.Constant) and ae.value is False)
                return {"scheme": last, "effect": "rejects" if rej else "reads", **({"rejects": [401]} if rej else {})}
        try:
            t = self.prog.infer(a, ctx)
        except RecursionError:
            return None
        fn = None
        if t and t[0] in ("func", "bound") and isinstance(t[1], FuncInfo):
            fn = t[1]
        elif t and t[0] == "inst":
            fn = self.prog.find_method(t[1], "__call__")
        elif t and t[0] == "type":
            fn = self.prog.find_method(t[1], "__call__") if isinstance(a, ast.Call) else self.prog.find_method(t[1], "__init__")
        if fn is None or fn.qual in seen:
            return None
        seen = seen | {fn.qual}
        fctx = Ctx(fn.module, fn, fn.cls)
        statuses, raises, reads, nested = set(), [], [], []
        rejects_via = None
        for sub in walk_body(fn.node):
            if isinstance(sub, ast.Raise) and sub.exc is not None:
                exc = sub.exc
                if isinstance(exc, ast.Name):
                    exc = self._value_of(exc, fctx)[0] or exc
                if isinstance(exc, ast.Call):
                    nm = (dotted(exc.func) or "").rsplit(".", 1)[-1]
                    st = self._status(kwarg(exc, "status_code") or (exc.args[0] if exc.args else None))
                    if st is not None:
                        statuses.add(st)
                    elif nm:
                        raises.append(nm)
        for p, x, cx in self._param_deps(fn):
            sub_e = self.dep_expr(x)
            nn = (dotted(sub_e) or "").rsplit(".", 1)[-1] if sub_e is not None else ""
            sc = self.dep_checks(sub_e, cx, depth + 1, seen)
            if nn:
                nested.append(nn)
            if sc and sc.get("effect") == "rejects" and rejects_via is None:
                rejects_via = nn
            for n2 in (sc or {}).get("nested", []):
                if n2 not in nested:
                    nested.append(n2)
        a2 = fn.node.args
        defaults = [None] * (len(a2.posonlyargs + a2.args) - len(a2.defaults)) + list(a2.defaults) + list(a2.kw_defaults)
        for p, dv in zip(a2.posonlyargs + a2.args + a2.kwonlyargs, defaults):
            kind = (dotted(dv.func) or "").rsplit(".", 1)[-1] if isinstance(dv, ast.Call) else ""
            ann = (dotted(p.annotation) or "").rsplit(".", 1)[-1] if p.annotation is not None else ""
            if kind in ("Header", "Cookie", "Query"):
                reads.append(f"{kind.lower()} {p.arg}")
            elif ann in ("Request", "HTTPConnection", "WebSocket"):
                reads.append(f"{ann.lower()} {p.arg}")
        out = {}
        if statuses:
            out["rejects" if statuses & REJECT_STATUS else "raises_status"] = sorted(statuses)
            if statuses & REJECT_STATUS and statuses - REJECT_STATUS:
                out["raises_status"] = sorted(statuses - REJECT_STATUS)
                out["rejects"] = sorted(statuses & REJECT_STATUS)
        if raises:
            out["raises"] = sorted(set(raises))
        if reads:
            out["reads"] = reads
        if nested:
            out["nested"] = nested
        if rejects_via:
            out["rejects_via"] = rejects_via
        out["effect"] = "rejects" if (statuses & REJECT_STATUS or rejects_via) else ("raises" if statuses or raises else "reads")
        self.stats["dependencies_evaluated"] += 1
        return out

    def handler_deps(self, f: FuncInfo) -> list[dict]:
        out = []
        for p, x, cx in self._param_deps(f):
            n = self.dep_name(x, cx)
            if n and not any(o["name"] == n for o in out):
                ent = self.dep_entry(x, cx, f"parameter {p.arg}")
                if ent:
                    out.append(ent)
        ctx = Ctx(f.module, f, f.cls)
        for d in f.decorators:
            fn = d.func if isinstance(d, ast.Call) else d
            nm = (dotted(fn) or "").rsplit(".", 1)[-1]
            if isinstance(fn, ast.Attribute) and (fn.attr in ROUTE_DECOS or self.obj_of(fn.value, ctx) is not None):
                continue        # the route decorator itself
            if nm and nm not in SKIP_DECOS and not any(o["name"] == nm for o in out):
                out.append({"name": nm, "via": "decorator"})
        return out

    def handler(self, e, ctx):
        """A view function, or (class, [methods]) for `View.as_view(...)` / an HTTPEndpoint class."""
        if e is None:
            return None
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and e.func.attr == "as_view":
            e = e.func.value
        try:
            t = self.prog.infer(e, ctx)
        except RecursionError:
            return None
        if t and t[0] == "func":
            return t[1]
        if t and t[0] == "bound":
            return t[1]
        if t and t[0] == "type":
            return t[1]
        return None

    def _op(self, obj, path, methods, h, ctx, line, how, name=None, extra_deps=(), ws=False, **ext):
        if path is None:
            self.unresolved.append(f"{ctx.mod.file}:{line} {how}")
            path = UNKNOWN
        path = flask_path(path) if obj.fw == "flask" else starlette_path(path)
        self.ops.append({"obj": obj, "path": path, "methods": methods, "handler": h, "file": ctx.mod.file, "line": line,
                         "how": how, "name": name, "deps": list(extra_deps), "ws": ws,
                         **{k: v for k, v in ext.items() if v}})

    def _flask_ext(self, c, ctx) -> dict:
        """Flask `subdomain=` / `host=` (host matching) / `defaults=` of a rule."""
        out = {}
        for k in ("subdomain", "host"):
            v = kwarg(c, k)
            if v is not None:
                out[k] = str_value(self.prog, v, ctx) or UNKNOWN
        dv = kwarg(c, "defaults")
        if isinstance(dv, ast.Dict):
            out["defaults"] = sorted(str_value(self.prog, k, ctx) or "?" for k in dv.keys if k is not None)
        return out

    def _methods(self, e, ctx, default=("GET",)):
        if e is None:
            return list(default)
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            out = [str_value(self.prog, x, ctx) for x in e.elts]
            out = [x.upper() for x in out if x]
            return out or list(default)
        return list(default)

    def _decorator(self, f, d: ast.Call, nested: str | None = None, node=None):
        is_cls = isinstance(f, ClassInfo)
        ctx = Ctx(f.module, None, None) if is_cls else Ctx(f.module, f, f.cls)
        if nested:          # resolves in the enclosing def's scope (its local `app`)
            octx = ctx
            obj = self.obj_of(d.func.value, ctx)
        else:
            octx = Ctx(f.module, None, None)
            obj = self.obj_of(d.func.value, octx)
        if obj is None:
            return
        attr = d.func.attr
        pe = d.args[0] if d.args else kwarg(d, "path") or kwarg(d, "rule")
        path = str_value(self.prog, pe, octx) if pe is not None else ""
        deps = self.deps_of(kwarg(d, "dependencies"), octx, "route dependencies")
        nm = str_value(self.prog, kwarg(d, "name"), octx) or str_value(self.prog, kwarg(d, "endpoint"), octx) or nested
        ext = self._flask_ext(d, octx) if obj.fw == "flask" else {}
        if node is not None:      # the nested view's own parameters (Depends) and decorators count as its access
            ext["deps_fn"] = FuncInfo(node.name, f"{f.qual}.{node.name}", f.module, node,
                                      decorators=list(node.decorator_list))
        if attr == "endpoint":            # Flask `@app.endpoint("index")`: the view of an endpoint name
            ep = str_value(self.prog, d.args[0] if d.args else None, octx)
            if ep:
                self.endpoints[(obj.key, ep)] = f
            return
        if is_cls and obj.kind in ("api", "namespace") and attr == "route":
            # flask-restx `@ns.route("/a", "/b")` on a Resource class: one rule per path, methods from the class
            for pe2 in d.args or [kwarg(d, "path")]:
                self._op(obj, str_value(self.prog, pe2, octx) if pe2 is not None else "",
                         self._methods(kwarg(d, "methods"), octx, default=()), f, octx, d.lineno, "@route (Resource)",
                         str_value(self.prog, kwarg(d, "endpoint"), octx) or f.name.lower(), deps, **ext)
            return
        if attr in VERBS:
            self._op(obj, path, [attr.upper()], f, octx, d.lineno, f"@{attr}", nm, deps, **ext)
        elif attr in ("route", "api_route"):
            self._op(obj, path, self._methods(kwarg(d, "methods"), octx, default=() if is_cls else ("GET",)), f, octx,
                     d.lineno, f"@{attr}", nm, deps, **ext)
        elif attr in ("websocket", "websocket_route"):
            self._op(obj, path, ["WS"], f, octx, d.lineno, f"@{attr}", nm, deps, ws=True)

    def _call(self, c: ast.Call, ctx):
        attr = c.func.attr
        if attr == "add" and isinstance(c.func.value, ast.Attribute) and c.func.value.attr == "url_map":
            obj = self.obj_of(c.func.value.value, ctx)       # werkzeug `app.url_map.add(Rule(...) | Submount(...))`
            if obj is not None and obj.fw == "flask" and c.args:
                self._werkzeug_rule(obj, c.args[0], ctx, "")
            return
        if attr not in ("include_router", "register_blueprint", "mount", "add_api_route", "add_route", "add_url_rule",
                        "add_websocket_route", "add_api_websocket_route", "host", "add_resource", "add_namespace",
                        "init_app"):
            return
        obj = self.obj_of(c.func.value, ctx)
        if obj is None:
            return
        prog = self.prog
        if attr in ("include_router", "register_blueprint"):
            child = self.obj_of(c.args[0], ctx) if c.args else None
            if child is None:
                return
            pk = kwarg(c, "prefix") if attr == "include_router" else kwarg(c, "url_prefix")
            pre = (str_value(prog, pk, ctx) or UNKNOWN) if pk is not None else None
            sd = kwarg(c, "subdomain")
            self._attach(obj, child, pre, c.lineno, ctx.mod.file,
                         deps=self.deps_of(kwarg(c, "dependencies"), ctx, "include dependencies"),
                         subdomain=(str_value(prog, sd, ctx) or UNKNOWN) if sd is not None else None,
                         bp_name=str_value(prog, kwarg(c, "name"), ctx) if attr == "register_blueprint" else None)
        elif attr == "mount":
            pe = c.args[0] if c.args else kwarg(c, "path")
            ae = c.args[1] if len(c.args) > 1 else kwarg(c, "app")
            child = self.obj_of(ae, ctx) if ae is not None else None
            if child is not None:
                self._attach(obj, child, str_value(prog, pe, ctx) or UNKNOWN, c.lineno, ctx.mod.file, mount=True)
        elif attr == "host":              # Starlette `app.host("api.example.com", app=sub)`
            he = c.args[0] if c.args else kwarg(c, "host")
            ae = c.args[1] if len(c.args) > 1 else kwarg(c, "app")
            child = self.obj_of(ae, ctx) if ae is not None else None
            if child is not None:
                self._attach(obj, child, "", c.lineno, ctx.mod.file, mount=True,
                             host=str_value(prog, he, ctx) or UNKNOWN)
        elif attr == "init_app":          # flask-restful `api.init_app(app)`
            parent = self.obj_of(c.args[0] if c.args else kwarg(c, "app"), ctx)
            if obj.kind == "api" and parent is not None:
                self._attach(parent, obj, None, c.lineno, ctx.mod.file)
        elif attr == "add_namespace":     # flask-restx `api.add_namespace(ns, path="/x")`
            child = self.obj_of(c.args[0], ctx) if c.args else None
            if child is not None:
                pe = c.args[1] if len(c.args) > 1 else kwarg(c, "path")
                self._attach(obj, child, (str_value(prog, pe, ctx) or UNKNOWN) if pe is not None else None,
                             c.lineno, ctx.mod.file)
        elif attr == "add_resource":      # flask-restful `api.add_resource(Todo, "/todos/<id>", endpoint="todo")`
            if not c.args:
                return
            h = self.handler(c.args[0], ctx)
            ep = str_value(prog, kwarg(c, "endpoint"), ctx) or (h.name.lower() if isinstance(h, ClassInfo) else None)
            for pe in c.args[1:] or [kwarg(c, "urls")]:
                self._op(obj, str_value(prog, pe, ctx), [], h, ctx, c.lineno, "add_resource", ep)
        elif attr in ("add_api_route", "add_route", "add_websocket_route", "add_api_websocket_route"):
            pe = c.args[0] if c.args else kwarg(c, "path")
            he = c.args[1] if len(c.args) > 1 else kwarg(c, "endpoint") or kwarg(c, "route")
            h = self.handler(he, ctx) or self._local_def(he, ctx)
            ws = "websocket" in attr
            self._op(obj, str_value(prog, pe, ctx), ["WS"] if ws else self._methods(kwarg(c, "methods"), ctx), h, ctx,
                     c.lineno, attr, str_value(prog, kwarg(c, "name"), ctx), ws=ws)
        elif attr == "add_url_rule":
            pe = c.args[0] if c.args else kwarg(c, "rule")
            ve = c.args[2] if len(c.args) > 2 else kwarg(c, "view_func")
            ee = c.args[1] if len(c.args) > 1 else kwarg(c, "endpoint")
            if ve is None:
                # an endpoint without a view function: its view comes from `@app.endpoint(name)`,
                # `app.view_functions[name] = f` or another rule with that endpoint (resolved in routes())
                nm = str_value(prog, ee, ctx)
                if nm is not None:
                    self._op(obj, str_value(prog, pe, ctx), self._methods(kwarg(c, "methods"), ctx, default=()), None,
                             ctx, c.lineno, "add_url_rule (endpoint)", nm, endpoint_only=True,
                             **self._flask_ext(c, ctx))
                return
            h = self.handler(ve, ctx) or self._local_def(ve, ctx)
            nm = str_value(prog, ee, ctx)
            if nm is None and isinstance(ve, ast.Call) and ve.args:
                nm = str_value(prog, ve.args[0], ctx)       # View.as_view("name")
            if nm is None and isinstance(ve, ast.Name):
                nm = ve.id                                  # Flask's default endpoint: the view function's name
            self._op(obj, str_value(prog, pe, ctx), self._methods(kwarg(c, "methods"), ctx, default=()), h, ctx,
                     c.lineno, "add_url_rule", nm, **self._flask_ext(c, ctx))

    def _werkzeug_rule(self, obj, e, ctx, prefix, depth=0):
        """werkzeug `Rule(path, endpoint=, methods=)` / `Submount(prefix, [rules])` added to a Flask url_map: the
        endpoint's view comes from `@app.endpoint` / `view_functions[...]`, as for add_url_rule without view_func."""
        if not isinstance(e, ast.Call) or depth > 4:
            return
        kind = (dotted(e.func) or "").rsplit(".", 1)[-1]
        p = str_value(self.prog, e.args[0] if e.args else kwarg(e, "string"), ctx)
        if kind == "Submount" and len(e.args) > 1 and isinstance(e.args[1], (ast.List, ast.Tuple)):
            for x in e.args[1].elts:
                self._werkzeug_rule(obj, x, ctx, join(prefix, p or UNKNOWN), depth + 1)
        elif kind == "Rule":
            ep = str_value(self.prog, kwarg(e, "endpoint"), ctx)
            if ep:
                self._op(obj, join(prefix, p) if p is not None else None,
                         self._methods(kwarg(e, "methods"), ctx, default=()), None, ctx, e.lineno,
                         "url_map.add(Rule)", ep, endpoint_only=True)

    def _static(self):
        """Flask's built-in static route: `GET /static/<path:filename>` on every app (`static_url_path=`, none with
        `static_folder=None`), and on blueprints created with `static_folder=` (under their prefix)."""
        for o in list(self.objs.values()):
            if o.fw != "flask" or o.call is None or o.kind not in ("app", "blueprint"):
                continue
            sf = kwarg(o.call, "static_folder")
            if o.kind == "app" and isinstance(sf, ast.Constant) and sf.value is None:
                continue
            if o.kind == "blueprint" and sf is None:
                continue
            ctx = Ctx(o.mod, o.func, o.func.cls if o.func else None)
            su = kwarg(o.call, "static_url_path")
            base = str_value(self.prog, su, ctx) if su is not None else "/static"
            self._op(o, join(base or UNKNOWN, "<path:filename>"), ["GET"], None, ctx, o.line, "static", "static",
                     static=True)
            self.stats["static_routes"] += 1

    def _route_list(self, obj, e, ctx, prefix, depth=0, host=None):
        """Starlette `routes=[Route(...), WebSocketRoute(...), Mount(path, routes=[...] | app=x),
        Host("api.example.com", routes=[...] | app=x)]`."""
        if depth > 4:
            return
        if isinstance(e, ast.Name):
            r = self.prog.resolve_name(ctx.mod, e.id)
            if ctx.func is not None:
                lv = self.prog.local_vars(ctx).get(e.id)
                if lv:
                    e = next((v for v, _a, k in lv if k == "assign" and v is not None), None)
                    return self._route_list(obj, e, ctx, prefix, depth + 1, host)
            if r and r[0] == "var":
                v = next((v for v, _ln, _a in r[1].vars.get(r[2], ()) if v is not None), None)
                return self._route_list(obj, v, Ctx(r[1], None, None), prefix, depth + 1, host)
            return
        if not isinstance(e, (ast.List, ast.Tuple)):
            return
        for x in e.elts:
            if not isinstance(x, ast.Call):
                continue
            kind = (dotted(x.func) or "").rsplit(".", 1)[-1]
            pe = x.args[0] if x.args else kwarg(x, "path")
            p = str_value(self.prog, pe, ctx)
            if kind in ("Route", "APIRoute", "WebSocketRoute", "APIWebSocketRoute"):
                he = x.args[1] if len(x.args) > 1 else kwarg(x, "endpoint")
                ws = "WebSocket" in kind
                self._op(obj, join(prefix, p) if p is not None else None, ["WS"] if ws else self._methods(kwarg(x, "methods"), ctx),
                         self.handler(he, ctx), ctx, x.lineno, kind, str_value(self.prog, kwarg(x, "name"), ctx), ws=ws,
                         host=host)
            elif kind in ("Mount", "Host"):
                if kind == "Host":           # the first argument is a host name, not a path
                    h2 = str_value(self.prog, x.args[0] if x.args else kwarg(x, "host"), ctx) or UNKNOWN
                    sub_prefix, sub_host = prefix, h2
                else:
                    sub_prefix, sub_host = join(prefix, p or UNKNOWN), host
                sub = kwarg(x, "routes")
                if sub is not None:
                    self._route_list(obj, sub, ctx, sub_prefix, depth + 1, sub_host)
                else:
                    ae = x.args[1] if len(x.args) > 1 else kwarg(x, "app")
                    child = self.obj_of(ae, ctx) if ae is not None else None
                    if child is not None:
                        self._attach(obj, child, sub_prefix, x.lineno, ctx.mod.file, mount=True, host=sub_host)

    # ------------------------------------------------------------------ routes
    def placements(self):
        """obj key -> [(full prefix incl. the object's own, deps, chain, mounted, blueprint-name chain)], walking from
        the roots (objects nothing mounts). FastAPI: parent prefix + include prefix + the router's own prefix; Flask:
        `register_blueprint(url_prefix=)` replaces the blueprint's own url_prefix."""
        out = defaultdict(list)

        def visit(o, full, deps, chain, names, depth, ext, over, bp_name=None):
            if depth > 12 or o.key in chain:
                return
            names = names + ([bp_name or o.name] if (bp_name or o.name) else [])
            deps = deps + o.deps
            chain = chain + [o.key]
            ext = {**ext, **{k: v for k, v in (("host", o.host), ("subdomain", o.subdomain)) if v}, **over}
            out[o.key].append((full, deps, chain, True, names, ext))
            for c in o.children:
                ch, cp = c["obj"], c["prefix"]
                if ch.fw == "flask" and not c.get("mount"):
                    sub = join(full, cp if cp is not None else ch.prefix)
                else:
                    sub = join(full, cp or "", ch.prefix)
                visit(ch, sub, deps + c["deps"], chain, names, depth + 1, ext,
                      {k: c[k] for k in ("host", "subdomain") if c.get(k)}, c.get("bp_name"))

        for o in self.objs.values():
            if o.parents == 0:
                visit(o, join(o.prefix), [], [], [], 0, {}, {})
        for o in self.objs.values():
            if o.key not in out:      # only reachable through a cycle
                out[o.key].append((join(o.prefix), o.deps, [o.key], False, [o.name] if o.name else [], {}))
        return out

    def _declared_methods(self, c: ClassInfo) -> list[str]:
        """`methods = ["GET", "POST"]` on a Flask View / MethodView class or a local base class."""
        for k in [c] + [b[1] for b in self.prog.mro(c) if b[0] == "type"]:
            if "methods" in k.attrs:
                v = k.attrs["methods"][0]
                if isinstance(v, (ast.List, ast.Tuple, ast.Set)):
                    return [x.upper() for x in (str_value(self.prog, e, Ctx(k.module, None, k)) for e in v.elts) if x]
                return []
        return []

    def routes(self) -> list[dict]:
        pl = self.placements()
        rows = []
        named = {}
        for op in self.ops:
            if op["handler"] is not None and op["name"]:
                named.setdefault((op["obj"].key, op["name"]), op)
        for op in self.ops:
            o = op["obj"]
            h = op["handler"]
            methods = op["methods"]
            if op.get("endpoint_only"):      # add_url_rule(rule, endpoint="x") without a view_func
                h = self.endpoints.get((o.key, op["name"]))
                other = named.get((o.key, op["name"]))
                if h is None and other is not None:
                    h = other["handler"]
                    methods = methods or other["methods"]
                self.stats["endpoint_rules"] += 1
                if h is not None:
                    self.stats["endpoint_rules_resolved"] += 1
            if isinstance(h, ClassInfo):     # View.as_view() / HTTPEndpoint: one route per HTTP method it defines
                defined = [m.upper() for m in VIEW_METHODS if self.prog.find_method(h, m)]
                allowed = methods or self._declared_methods(h)     # MethodView `methods = [...]` override
                if defined:
                    want = [m for m in defined if m in allowed] if allowed else defined
                    handlers = [(m, self.prog.find_method(h, m.lower())) for m in want]
                else:                         # a plain View: dispatch_request serves every allowed method
                    disp = self.prog.find_method(h, "dispatch_request") or h
                    handlers = [(m, disp) for m in (allowed or ["GET"])]
                handlers = handlers or [(m, h) for m in (methods or ["GET"])]
            else:
                handlers = [(m, h) for m in (methods or ["GET"])]
            for full, deps, chain, mounted, names, ext in pl[o.key]:
                uri = join(full, op["path"])
                ep = op["name"] or (h.name if isinstance(h, (FuncInfo, ClassInfo)) else None)
                name = ".".join(names + [ep]) if o.fw == "flask" and ep else ep
                dfn = op.get("deps_fn") or h
                fdeps = deps + op["deps"] + (self.handler_deps(dfn) if isinstance(dfn, FuncInfo) else [])
                for m, hh in handlers:
                    rows.append({"method": m, "uri": uri, "handler": hh, "file": op["file"], "line": op["line"],
                                 "framework": o.fw, "name": name, "mounted": mounted and o.kind == "app" or
                                 mounted and len(chain) > 1 and self.objs[chain[0]].kind == "app",
                                 "how": op["how"], "access": _dedupe(fdeps),
                                 "chain": [".".join(x for x in k if x) for k in chain],
                                 "host": op.get("host") or ext.get("host"),
                                 "subdomain": op.get("subdomain") or ext.get("subdomain"),
                                 "defaults": op.get("defaults"), "endpoint_only": op.get("endpoint_only"),
                                 "static": op.get("static")})
        return rows


def _module_level(tree):
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(n))


def _dedupe(xs):
    out = []
    for x in xs:
        if not any(o["name"] == x["name"] for o in out):
            out.append(x)
    return out


def emit(b: GraphBuilder, rows: list[dict]) -> int:
    from ..django.plugin import mod_of
    n = 0
    for r in rows:
        uri = re.sub(r"/{2,}", "/", r["uri"])
        key = f"{r['method']} {uri}"
        h = r["handler"]
        attrs = {"uri": uri, "method": r["method"], "framework": r["framework"], "name": r["name"],
                 "mounted": r["mounted"], "registration": r["how"], "router_chain": r["chain"] or None,
                 "trailing_slash": uri.endswith("/") and uri != "/", "path_params": re.findall(r"\{(\w+)\}", uri),
                 "host": r.get("host"), "subdomain": r.get("subdomain"), "defaults": r.get("defaults"),
                 "endpoint_alias": True if r.get("endpoint_only") else None, "static": r.get("static")}
        if r["access"]:
            attrs["access"] = r["access"]
        if isinstance(h, (FuncInfo, ClassInfo)):
            attrs["handler"] = h.id
        rid = b.add_node("route", key, name=key, file=r["file"], line=r["line"], module=mod_of(r["file"]), lang="python",
                         entry_kind="websocket" if r["method"] == "WS" else ("http_route" if r["mounted"] else None),
                         attrs={k: v for k, v in attrs.items() if v is not None})
        if isinstance(h, (FuncInfo, ClassInfo)):
            b.add_edge(rid, h.id, "ROUTES_TO", r["file"], r["line"], EXACT if r["how"].startswith("@") else RESOLVED)
        n += 1
    return n


class _PyWebPlugin(FrameworkPlugin):
    language = "python"
    frameworks: set = set()

    def detect(self, project: Project) -> bool:
        return _mentions(project, PKG[self.name])

    def contribute(self, project: Project, builder: GraphBuilder, prog: PyProgram) -> dict:
        r = Routes(prog, self.frameworks)
        r.collect()
        rows = r.routes()
        n = emit(builder, rows)
        st = {"apps": sum(1 for o in r.objs.values() if o.kind == "app"),
              "routers": sum(1 for o in r.objs.values() if o.kind != "app"), "routes": n,
              "unmounted_routes": sum(1 for x in rows if not x["mounted"]), **r.stats}
        if r.unresolved:
            st["paths_unresolved"] = r.unresolved[:20]
        return st


class FastAPIPlugin(_PyWebPlugin):
    """FastAPI and Starlette."""
    name = "fastapi"
    frameworks = {"fastapi", "starlette"}


class FlaskPlugin(_PyWebPlugin):
    name = "flask"
    frameworks = {"flask"}

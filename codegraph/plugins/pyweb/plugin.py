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
recorded as attrs.access for `cg routes`. Test-client requests then link to these routes (python/tests.py).
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
             "Router": ("starlette", "router"), "Flask": ("flask", "app"), "Blueprint": ("flask", "blueprint")}
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
        self.children: list[dict] = []   # {obj, prefix (None: child's own), deps, line, file}
        self.parents = 0


class Routes:
    def __init__(self, prog: PyProgram, frameworks: set[str]):
        self.prog = prog
        self.fws = frameworks
        self.objs: dict = {}
        self.ops: list[dict] = []
        self.unresolved: list[str] = []

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
        for n in names:
            last, top = n.rsplit(".", 1)[-1], n.split(".", 1)[0]
            f = FACTORIES.get(last)
            if f and top in ("fastapi", "starlette", "flask") and f[0] in self.fws:
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
        for m, f, stmts in self._scopes():
            ctx = Ctx(m, f, f.cls if f else None)
            for st in stmts:
                if not isinstance(st, (ast.Assign, ast.AnnAssign)) or st.value is None:
                    continue
                tgt = st.targets[0] if isinstance(st, ast.Assign) else st.target
                if not isinstance(tgt, ast.Name):
                    continue
                fk = self._factory(st.value, ctx)
                if not fk:
                    continue
                key = (m.name, f.qual if f else "", tgt.id)
                o = self.objs[key] = Obj(key, fk[0], fk[1], st.value, m, f, st.lineno)
                c = st.value
                if fk[0] == "flask":
                    o.prefix = str_value(prog, kwarg(c, "url_prefix"), ctx) or ""
                    if fk[1] == "blueprint":
                        o.name = str_value(prog, c.args[0] if c.args else kwarg(c, "name"), ctx)
                else:
                    pe = kwarg(c, "prefix")
                    o.prefix = (str_value(prog, pe, ctx) or UNKNOWN) if pe is not None else ""
                o.deps = self.deps_of(kwarg(c, "dependencies"), ctx, "router dependencies")
        # mounts and calls
        for m, f, stmts in self._scopes():
            ctx = Ctx(m, f, f.cls if f else None)
            for st in stmts:
                if isinstance(st, ast.Call) and isinstance(st.func, ast.Attribute):
                    self._call(st, ctx)
        for o in list(self.objs.values()):
            rl = kwarg(o.call, "routes")
            if rl is not None:
                self._route_list(o, rl, Ctx(o.mod, o.func, o.func.cls if o.func else None), "")
        # decorators
        for f in prog.funcs.values():
            for d in f.decorators:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                    self._decorator(f, d)
            # a view defined inside an app factory (`def create_app(): ... @app.route("/hello") def hello()`): nested
            # defs collapse into the enclosing def, which becomes the handler
            for sub in walk_body(f.node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for d in sub.decorator_list:
                        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                            self._decorator(f, d, nested=sub.name)

    def obj_of(self, e, ctx):
        """The application / router object an expression names (local, module-level, imported, `mod.router`)."""
        if isinstance(e, ast.Name):
            if ctx.func is not None and (ctx.mod.name, ctx.func.qual, e.id) in self.objs:
                return self.objs[(ctx.mod.name, ctx.func.qual, e.id)]
            if (ctx.mod.name, "", e.id) in self.objs:
                return self.objs[(ctx.mod.name, "", e.id)]
            r = self.prog.resolve_name(ctx.mod, e.id)
            if r and r[0] == "var":
                return self.objs.get((r[1].name, "", r[2]))
            return None
        if isinstance(e, ast.Attribute):
            try:
                t = self.prog.infer(e.value, ctx)
            except RecursionError:
                return None
            if t and t[0] == "mod":
                return self.objs.get((t[1].name, "", e.attr))
        return None

    def deps_of(self, e, ctx, via) -> list[dict]:
        out = []
        if isinstance(e, (ast.List, ast.Tuple)):
            for x in e.elts:
                n = self.dep_name(x, ctx)
                if n:
                    out.append({"name": n, "via": via})
        return out

    def dep_name(self, x, ctx, depth=0):
        """`Depends(get_current_user)` / `Security(scheme, scopes=...)` -> 'get_current_user'."""
        if depth > 3:
            return None
        if isinstance(x, ast.Call) and (dotted(x.func) or "").rsplit(".", 1)[-1] in DEP_FUNCS:
            a = x.args[0] if x.args else kwarg(x, "dependency")
            return (dotted(a) or "").rsplit(".", 1)[-1] or None if a is not None else None
        return None

    def ann_deps(self, ann, ctx, depth=0) -> list[str]:
        """Dependencies in a parameter annotation: `Annotated[User, Depends(f)]`, or a module alias of one
        (`CurrentUser = Annotated[User, Depends(get_current_user)]`), imported or local."""
        if ann is None or depth > 3:
            return []
        if isinstance(ann, ast.Subscript) and (dotted(ann.value) or "").rsplit(".", 1)[-1] == "Annotated":
            sl = ann.slice
            elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
            return [n for n in (self.dep_name(x, ctx) for x in elts[1:]) if n]
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

    def handler_deps(self, f: FuncInfo) -> list[dict]:
        out = []
        a = f.node.args
        params = a.posonlyargs + a.args + a.kwonlyargs
        defaults = [None] * (len(a.posonlyargs + a.args) - len(a.defaults)) + list(a.defaults) + list(a.kw_defaults)
        ctx = Ctx(f.module, f, f.cls)
        for p, dv in zip(params, defaults):
            names = ([self.dep_name(dv, ctx)] if dv is not None else []) + self.ann_deps(p.annotation, ctx)
            for n in names:
                if n and not any(o["name"] == n for o in out):
                    out.append({"name": n, "via": f"parameter {p.arg}"})
        for d in f.decorators:
            fn = d.func if isinstance(d, ast.Call) else d
            nm = (dotted(fn) or "").rsplit(".", 1)[-1]
            if isinstance(fn, ast.Attribute) and self.obj_of(fn.value, ctx) is not None:
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

    def _op(self, obj, path, methods, h, ctx, line, how, name=None, extra_deps=(), ws=False):
        if path is None:
            self.unresolved.append(f"{ctx.mod.file}:{line} {how}")
            path = UNKNOWN
        path = flask_path(path) if obj.fw == "flask" else starlette_path(path)
        self.ops.append({"obj": obj, "path": path, "methods": methods, "handler": h, "file": ctx.mod.file, "line": line,
                         "how": how, "name": name, "deps": list(extra_deps), "ws": ws})

    def _methods(self, e, ctx, default=("GET",)):
        if e is None:
            return list(default)
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            out = [str_value(self.prog, x, ctx) for x in e.elts]
            out = [x.upper() for x in out if x]
            return out or list(default)
        return list(default)

    def _decorator(self, f: FuncInfo, d: ast.Call, nested: str | None = None):
        ctx = Ctx(f.module, f, f.cls)
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
        if attr in VERBS:
            self._op(obj, path, [attr.upper()], f, octx, d.lineno, f"@{attr}", nm, deps)
        elif attr in ("route", "api_route"):
            self._op(obj, path, self._methods(kwarg(d, "methods"), octx), f, octx, d.lineno, f"@{attr}", nm, deps)
        elif attr in ("websocket", "websocket_route"):
            self._op(obj, path, ["WS"], f, octx, d.lineno, f"@{attr}", nm, deps, ws=True)

    def _call(self, c: ast.Call, ctx):
        attr = c.func.attr
        if attr not in ("include_router", "register_blueprint", "mount", "add_api_route", "add_route", "add_url_rule",
                        "add_websocket_route", "add_api_websocket_route", "host"):
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
            obj.children.append({"obj": child, "prefix": pre, "line": c.lineno, "file": ctx.mod.file,
                                 "deps": self.deps_of(kwarg(c, "dependencies"), ctx, "include dependencies")})
            child.parents += 1
        elif attr == "mount":
            pe = c.args[0] if c.args else kwarg(c, "path")
            ae = c.args[1] if len(c.args) > 1 else kwarg(c, "app")
            child = self.obj_of(ae, ctx) if ae is not None else None
            if child is not None:
                obj.children.append({"obj": child, "prefix": str_value(prog, pe, ctx) or UNKNOWN, "mount": True,
                                     "line": c.lineno, "file": ctx.mod.file, "deps": []})
                child.parents += 1
        elif attr in ("add_api_route", "add_route", "add_websocket_route", "add_api_websocket_route"):
            pe = c.args[0] if c.args else kwarg(c, "path")
            he = c.args[1] if len(c.args) > 1 else kwarg(c, "endpoint") or kwarg(c, "route")
            h = self.handler(he, ctx)
            ws = "websocket" in attr
            self._op(obj, str_value(prog, pe, ctx), ["WS"] if ws else self._methods(kwarg(c, "methods"), ctx), h, ctx,
                     c.lineno, attr, str_value(prog, kwarg(c, "name"), ctx), ws=ws)
        elif attr == "add_url_rule":
            pe = c.args[0] if c.args else kwarg(c, "rule")
            ve = c.args[2] if len(c.args) > 2 else kwarg(c, "view_func")
            ee = c.args[1] if len(c.args) > 1 else kwarg(c, "endpoint")
            if ve is None:
                return                  # an endpoint without a view function (served by another rule)
            h = self.handler(ve, ctx)
            nm = str_value(prog, ee, ctx)
            if nm is None and isinstance(ve, ast.Call) and ve.args:
                nm = str_value(prog, ve.args[0], ctx)       # View.as_view("name")
            self._op(obj, str_value(prog, pe, ctx), self._methods(kwarg(c, "methods"), ctx, default=()), h, ctx,
                     c.lineno, "add_url_rule", nm)

    def _route_list(self, obj, e, ctx, prefix, depth=0):
        """Starlette `routes=[Route(...), WebSocketRoute(...), Mount(path, routes=[...] | app=x)]`."""
        if depth > 4:
            return
        if isinstance(e, ast.Name):
            r = self.prog.resolve_name(ctx.mod, e.id)
            if ctx.func is not None:
                lv = self.prog.local_vars(ctx).get(e.id)
                if lv:
                    e = next((v for v, _a, k in lv if k == "assign" and v is not None), None)
                    return self._route_list(obj, e, ctx, prefix, depth + 1)
            if r and r[0] == "var":
                v = next((v for v, _ln, _a in r[1].vars.get(r[2], ()) if v is not None), None)
                return self._route_list(obj, v, Ctx(r[1], None, None), prefix, depth + 1)
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
                         self.handler(he, ctx), ctx, x.lineno, kind, str_value(self.prog, kwarg(x, "name"), ctx), ws=ws)
            elif kind in ("Mount", "Host"):
                sub = kwarg(x, "routes")
                if sub is not None:
                    self._route_list(obj, sub, ctx, join(prefix, p or UNKNOWN), depth + 1)
                else:
                    ae = x.args[1] if len(x.args) > 1 else kwarg(x, "app")
                    child = self.obj_of(ae, ctx) if ae is not None else None
                    if child is not None:
                        obj.children.append({"obj": child, "prefix": join(prefix, p or UNKNOWN), "mount": True,
                                             "line": x.lineno, "file": ctx.mod.file, "deps": []})
                        child.parents += 1

    # ------------------------------------------------------------------ routes
    def placements(self):
        """obj key -> [(full prefix incl. the object's own, deps, chain, mounted, blueprint-name chain)], walking from
        the roots (objects nothing mounts). FastAPI: parent prefix + include prefix + the router's own prefix; Flask:
        `register_blueprint(url_prefix=)` replaces the blueprint's own url_prefix."""
        out = defaultdict(list)

        def visit(o, full, deps, chain, names, depth):
            if depth > 12 or o.key in chain:
                return
            names = names + ([o.name] if o.name else [])
            deps = deps + o.deps
            chain = chain + [o.key]
            out[o.key].append((full, deps, chain, True, names))
            for c in o.children:
                ch, cp = c["obj"], c["prefix"]
                if ch.fw == "flask" and not c.get("mount"):
                    sub = join(full, cp if cp is not None else ch.prefix)
                else:
                    sub = join(full, cp or "", ch.prefix)
                visit(ch, sub, deps + c["deps"], chain, names, depth + 1)

        for o in self.objs.values():
            if o.parents == 0:
                visit(o, join(o.prefix), [], [], [], 0)
        for o in self.objs.values():
            if o.key not in out:      # only reachable through a cycle
                out[o.key].append((join(o.prefix), o.deps, [o.key], False, [o.name] if o.name else []))
        return out

    def routes(self) -> list[dict]:
        pl = self.placements()
        rows = []
        for op in self.ops:
            o = op["obj"]
            h = op["handler"]
            methods = op["methods"]
            if isinstance(h, ClassInfo):     # View.as_view() / HTTPEndpoint: one route per HTTP method it defines
                defined = [m.upper() for m in VIEW_METHODS if self.prog.find_method(h, m)]
                want = [m for m in methods if m in defined] if methods else defined
                handlers = [(m, self.prog.find_method(h, m.lower())) for m in want] or [(m, h) for m in (methods or ["GET"])]
            else:
                handlers = [(m, h) for m in (methods or ["GET"])]
            for full, deps, chain, mounted, names in pl[o.key]:
                uri = join(full, op["path"])
                ep = op["name"] or (h.name if isinstance(h, (FuncInfo, ClassInfo)) else None)
                name = ".".join(names + [ep]) if o.fw == "flask" and ep else ep
                fdeps = deps + op["deps"] + (self.handler_deps(h) if isinstance(h, FuncInfo) else [])
                for m, hh in handlers:
                    rows.append({"method": m, "uri": uri, "handler": hh, "file": op["file"], "line": op["line"],
                                 "framework": o.fw, "name": name, "mounted": mounted and o.kind == "app" or
                                 mounted and len(chain) > 1 and self.objs[chain[0]].kind == "app",
                                 "how": op["how"], "access": _dedupe(fdeps),
                                 "chain": [".".join(x for x in k if x) for k in chain]})
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
                 "trailing_slash": uri.endswith("/") and uri != "/", "path_params": re.findall(r"\{(\w+)\}", uri)}
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
              "unmounted_routes": sum(1 for x in rows if not x["mounted"])}
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

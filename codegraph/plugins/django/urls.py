"""URL resolution: Django urlconfs (path/re_path/url/include, namespaces, nested includes), DRF routers
(register/@action, trailing_slash, nested routers) and django-ninja (NinjaAPI/Router, add_router chains,
@router.<verb>, auth inheritance). Produces route dicts; plugin.py turns them into nodes/edges.

Route dict: {method, uri ('/a/{id}/'), file, line, handler (FuncInfo|ClassInfo|None), view (text),
framework, name, namespace, conf (exact|resolved|heuristic), mounted, extra...}
"""
from __future__ import annotations

import ast
import re

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ..python.plugin import ClassInfo, Ctx, FuncInfo, ModInfo, PyProgram, ann_text, const_str, dotted, kwarg

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
PATH_FUNCS = {"path", "re_path", "url"}
CONV = re.compile(r"<(?:(\w+):)?(\w+)>")
# DRF / Django generic views -> HTTP methods they implement (when not overridden locally)
GENERIC_METHODS = {
    "View": set(), "APIView": set(), "GenericAPIView": set(), "TemplateView": {"GET"}, "RedirectView": {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"},
    "ListView": {"GET"}, "DetailView": {"GET"}, "FormView": {"GET", "POST", "PUT"}, "CreateView": {"GET", "POST", "PUT"},
    "UpdateView": {"GET", "POST", "PUT"}, "DeleteView": {"GET", "POST", "DELETE"}, "ArchiveIndexView": {"GET"},
    "LoginView": {"GET", "POST"}, "LogoutView": {"POST"}, "PasswordChangeView": {"GET", "POST"},
    "ListAPIView": {"GET"}, "CreateAPIView": {"POST"}, "RetrieveAPIView": {"GET"}, "DestroyAPIView": {"DELETE"},
    "UpdateAPIView": {"PUT", "PATCH"}, "ListCreateAPIView": {"GET", "POST"}, "RetrieveUpdateAPIView": {"GET", "PUT", "PATCH"},
    "RetrieveDestroyAPIView": {"GET", "DELETE"}, "RetrieveUpdateDestroyAPIView": {"GET", "PUT", "PATCH", "DELETE"},
    "ObtainAuthToken": {"POST"}, "TokenObtainPairView": {"POST"}, "TokenRefreshView": {"POST"}, "TokenVerifyView": {"POST"},
}
VIEWSET_ACTIONS = {
    "ModelViewSet": {"list", "create", "retrieve", "update", "partial_update", "destroy"},
    "ReadOnlyModelViewSet": {"list", "retrieve"},
    "ListModelMixin": {"list"}, "CreateModelMixin": {"create"}, "RetrieveModelMixin": {"retrieve"},
    "UpdateModelMixin": {"update", "partial_update"}, "DestroyModelMixin": {"destroy"},
}
ACTION_ROUTES = [("list", "GET", False), ("create", "POST", False), ("retrieve", "GET", True), ("update", "PUT", True),
                 ("partial_update", "PATCH", True), ("destroy", "DELETE", True)]
ACTION_METHOD = {"list": "get", "retrieve": "get", "create": "post", "update": "put", "partial_update": "patch", "destroy": "delete"}
NINJA_API = ("NinjaAPI", "NinjaExtraAPI")
NINJA_ROUTER = ("Router",)


def django_path_to_template(route: str) -> tuple[str, list[str]]:
    params = []

    def rep(m):
        params.append(m.group(2))
        return "{" + m.group(2) + "}"
    return CONV.sub(rep, route), params


def regex_to_template(rx: str) -> tuple[str, bool]:
    """'^books/(?P<pk>[0-9]+)/$' -> ('books/{pk}/', exact?)."""
    s = rx
    if s.startswith("^"):
        s = s[1:]
    if s.endswith("$"):
        s = s[:-1]
    if s.endswith(r"\Z"):
        s = s[:-2]
    out, i, n_unnamed, clean = [], 0, 0, True
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nx = s[i + 1]
            if nx in "./-_~":
                out.append(nx); i += 2; continue
            clean = False
            out.append("{_}"); i += 2
            while i < len(s) and s[i] in "+*?":
                i += 1
            continue
        if ch == "(":
            depth, j = 1, i + 1
            while j < len(s) and depth:
                if s[j] == "\\":
                    j += 2; continue
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                j += 1
            grp = s[i:j]
            m = re.match(r"\(\?P<(\w+)>", grp)
            opt = j < len(s) and s[j] == "?"
            if m:
                out.append("{" + m.group(1) + "}")
            elif grp.startswith("(?:"):
                inner, ex = regex_to_template(grp[3:-1])
                if opt:
                    clean = False
                    if inner.strip("/") == "" or inner == "/":
                        j += 1
                        i = j
                        continue
                out.append(inner)
                clean = clean and ex
            else:
                out.append("{arg%d}" % n_unnamed)
                n_unnamed += 1
            if opt:
                j += 1
                clean = False
            i = j
            continue
        if ch in "?*+|[]":
            clean = False
            if ch == "?" and out and out[-1] == "/":
                i += 1
                continue
            if ch == "[":
                j = s.find("]", i)
                out.append("{_}")
                i = j + 1 if j > 0 else len(s)
                while i < len(s) and s[i] in "+*?":
                    i += 1
                continue
            i += 1
            continue
        if ch in ".":
            clean = False
            out.append("{_}"); i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), clean


def join(*parts: str) -> str:
    s = "/".join(p.strip("/") if k < len(parts) - 1 else p.lstrip("/") for k, p in enumerate(parts) if p not in ("", None))
    s = re.sub(r"/{2,}", "/", s)
    return "/" + s.lstrip("/")


class UrlResolver:
    def __init__(self, prog: PyProgram, plugin):
        self.prog = prog
        self.p = plugin
        self.routes: list[dict] = []
        self.unresolved: list[dict] = []
        self.walked: set = set()
        self.mounts: dict = {}           # ninja api key -> [mount prefixes]
        self.included: set[str] = set()  # urlconf modules reached from a root

    # ---------------- generic evaluation of module-level lists
    def module_list(self, m: ModInfo, var: str, depth=0) -> list[tuple]:
        """Items of a module-level list built with =, +=, .append/.extend/.insert. -> [(item ast, cond)]"""
        items: list[tuple] = []

        def visit(stmts, cond):
            nonlocal items
            for st in stmts:
                if isinstance(st, ast.Assign) and any(isinstance(t, ast.Name) and t.id == var for t in st.targets):
                    new = self.eval_list(m, st.value, depth, cond)
                    if not new and any(isinstance(x, ast.Name) and x.id == var for x in ast.walk(st.value)):
                        continue  # urlpatterns = wrap(urlpatterns): an unknown pass-through keeps what we have
                    items = (items + new) if cond else new
                elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.target.id == var and st.value is not None:
                    items = self.eval_list(m, st.value, depth, cond)
                elif isinstance(st, ast.AugAssign) and isinstance(st.target, ast.Name) and st.target.id == var:
                    items = items + self.eval_list(m, st.value, depth, cond)
                elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and isinstance(st.value.func, ast.Attribute) \
                        and isinstance(st.value.func.value, ast.Name) and st.value.func.value.id == var:
                    a = st.value.func.attr
                    if a == "append" and st.value.args:
                        items.append((st.value.args[0], cond))
                    elif a == "extend" and st.value.args:
                        items += self.eval_list(m, st.value.args[0], depth, cond)
                    elif a == "insert" and len(st.value.args) > 1:
                        items.insert(0, (st.value.args[1], cond))
                elif isinstance(st, ast.If):
                    c = ann_text(st.test)
                    visit(st.body, c)
                    visit(st.orelse, f"not ({c})")
                elif isinstance(st, ast.Try):
                    visit(st.body, cond)
                    for h in st.handlers:
                        visit(h.body, "except")
        visit(m.tree.body, None)
        return items

    def eval_list(self, m: ModInfo, e, depth=0, cond=None) -> list[tuple]:
        if depth > 12 or e is None:
            return []
        if isinstance(e, (ast.List, ast.Tuple)):
            out = []
            for x in e.elts:
                if isinstance(x, ast.Starred):
                    out += self.eval_list(m, x.value, depth + 1, cond)
                else:
                    out.append((x, cond))
            return out
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            return self.eval_list(m, e.left, depth + 1, cond) + self.eval_list(m, e.right, depth + 1, cond)
        if isinstance(e, ast.Name):
            t = self.prog.resolve_name(m, e.id)
            if t and t[0] == "var":
                return self.module_list(t[1], t[2], depth + 1)
            return []
        if isinstance(e, ast.Attribute) and e.attr == "urls":
            return [(e, cond)]
        if isinstance(e, ast.Call):
            fn = (dotted(e.func) or "").split(".")[-1]
            if fn in ("format_suffix_patterns", "i18n_patterns", "list") and e.args:
                out = []
                for a in e.args:
                    if isinstance(a, ast.Starred):
                        out += self.eval_list(m, a.value, depth + 1, cond)
                    elif fn == "i18n_patterns":
                        out.append((a, cond))
                    else:
                        out += self.eval_list(m, a, depth + 1, cond)
                if fn == "i18n_patterns":
                    return [(("i18n", x), c) for x, c in out]
                return out
            if fn in ("static", "staticfiles_urlpatterns", "debug_toolbar_urls"):
                return []
            if fn in PATH_FUNCS or fn in ("include",):
                return [(e, cond)]
        if isinstance(e, ast.Attribute):
            t = self.prog.infer(e, Ctx(m, None, None))
            if t and t[0] == "var":
                return self.module_list(t[1], t[2], depth + 1)
        return []

    # ---------------- urlconf walk
    def walk(self, m: ModInfo, prefix: str, ns: list, depth: int, chain: list, var="urlpatterns", items=None, conds=()):
        key = (m.name, prefix, var)
        if depth > 15 or key in self.walked:
            return
        self.walked.add(key)
        self.included.add(m.name)
        items = items if items is not None else self.module_list(m, var)
        for it, cond in items:
            i18n = False
            if isinstance(it, tuple) and it[0] == "i18n":
                it, i18n = it[1], True
            pre = prefix + "{language}/" if i18n else prefix  # Django concatenates pattern strings
            cs = conds + ((cond,) if cond else ())
            if isinstance(it, ast.Attribute) and it.attr == "urls":
                self.mount_urls(m, it, pre, ns, chain, cs, it.lineno)
                continue
            if not isinstance(it, ast.Call):
                continue
            fname = (dotted(it.func) or "").split(".")[-1]
            if fname not in PATH_FUNCS or not it.args:
                continue
            raw = const_str(it.args[0])
            regex = fname != "path"
            if raw is None:
                tpl, ok = "{?}", False
                self.unresolved.append({"file": m.file, "line": it.lineno, "reason": "non-literal route"})
            elif regex:
                tpl, ok = regex_to_template(raw)
            else:
                tpl, _ = django_path_to_template(raw)
                ok = True
            full = pre + tpl
            view = it.args[1] if len(it.args) > 1 else kwarg(it, "view")
            name = const_str(kwarg(it, "name")) or (const_str(it.args[3]) if len(it.args) > 3 else None)
            where = {"file": m.file, "line": it.lineno}
            self.view(m, view, full, ns, chain + [f"{m.file}:{it.lineno}"], cs, where, name, ok, raw)

    def mount_urls(self, m, attr: ast.Attribute, prefix, ns, chain, conds, line):
        """`api.urls` / `router.urls` / `admin.site.urls` used as a view or include target."""
        ctx = Ctx(m, None, None)
        base = attr.value
        t = self.prog.infer(base, ctx)
        rk = self.p.ninja.api_key(m, base)
        if rk:
            self.mounts.setdefault(rk, []).append({"prefix": prefix, "file": m.file, "line": line, "chain": chain, "conds": conds})
            return
        rr = self.p.drf.router_key(m, base)
        if rr:
            self.p.drf.mounts.setdefault(rr, []).append({"prefix": prefix, "file": m.file, "line": line, "chain": chain, "conds": conds, "ns": ns})
            return
        d = dotted(base) or ""
        if d.endswith("site") or (t and t[0] in ("ext", "einst") and "admin" in t[1]):
            self.routes.append({"method": "ANY", "uri": "/" + prefix,
                                "file": m.file, "line": line, "handler": None, "view": "django.contrib.admin.site.urls",
                                "framework": "django-admin", "conf": EXACT, "entry_kind": "admin_panel", "chain": chain,
                                "conds": list(conds), "mounted": True, "name": "admin"})
            return
        self.unresolved.append({"file": m.file, "line": line, "reason": f"unknown .urls target {d}"})

    def view(self, m: ModInfo, view, full: str, ns: list, chain: list, conds, where, name, ok, raw):
        prog = self.prog
        ctx = Ctx(m, None, None)
        if view is None:
            return
        if isinstance(view, ast.Attribute) and view.attr == "urls":
            self.mount_urls(m, view, full, ns, chain, conds, where["line"])
            return
        if isinstance(view, ast.Call):
            fn = view.func
            fname = (dotted(fn) or "").split(".")[-1]
            if fname == "include":
                self.include(m, view, full, ns, chain, conds, where)
                return
            if isinstance(fn, ast.Attribute) and fn.attr in ("as_view", "as_asgi"):
                t = prog.infer(fn.value, ctx)
                actions = None
                if view.args and isinstance(view.args[0], ast.Dict):
                    actions = {const_str(k): const_str(v) for k, v in zip(view.args[0].keys, view.args[0].values) if const_str(k)}
                self.p.emit_cbv(t, fn.value, full, where, name, ns, chain, conds, ok, actions, raw)
                return
            # decorator-style wrappers: csrf_exempt(view), login_required(View.as_view()), cache_page(60)(view)
            inner = view.args[-1] if view.args else None
            if isinstance(fn, ast.Call) and view.args:
                inner = view.args[0]
            if inner is not None:
                self.view(m, inner, full, ns, chain, conds + (f"wrapped:{fname or ann_text(fn)}",), where, name, ok, raw)
                return
        t = prog.infer(view, ctx)
        if t and t[0] == "func":
            self.p.emit_fbv(t[1], full, where, name, ns, chain, conds, ok, raw)
            return
        if t and t[0] == "type":
            self.p.emit_cbv(t, view, full, where, name, ns, chain, conds, ok, None, raw)
            return
        if t and t[0] == "var":
            # e.g. `my_view = SomeView.as_view()` at module level
            val = prog.var_value(t[1], t[2])
            if val is not None:
                self.view(t[1], val, full, ns, chain, conds, where, name, ok, raw)
                return
        if isinstance(view, ast.Constant) and isinstance(view.value, str):
            mod, _, fn = view.value.rpartition(".")
            r = prog.lookup(mod, fn) if mod else None
            if r and r[0] == "func":
                self.p.emit_fbv(r[1], full, where, name, ns, chain, conds, ok, raw)
                return
        self.routes.append({"method": "ANY", "uri": "/" + full, "file": where["file"], "line": where["line"], "handler": None,
                            "view": ann_text(view), "framework": "django", "conf": EXACT if ok else HEURISTIC, "chain": chain,
                            "conds": list(conds), "mounted": True, "name": name, "namespace": ":".join(ns) or None,
                            "external_view": (t[1] if t and t[0] in ("ext", "einst") else None)})

    def include(self, m: ModInfo, call: ast.Call, prefix, ns, chain, conds, where):
        prog = self.prog
        arg = call.args[0] if call.args else kwarg(call, "arg")
        nsp = const_str(kwarg(call, "namespace"))
        app_name = None
        if isinstance(arg, ast.Tuple) and arg.elts:
            app_name = const_str(arg.elts[1]) if len(arg.elts) > 1 else None
            arg = arg.elts[0]
        ns2 = ns + [nsp or app_name] if (nsp or app_name) else ns
        s = const_str(arg)
        if s is not None:
            tm = prog.module(s)
            if tm is None:
                self.unresolved.append({**where, "reason": f"include('{s}') not found"})
                return
            self.walk(tm, prefix, ns2, len(chain), chain)
            return
        if isinstance(arg, ast.Attribute) and arg.attr == "urls":
            self.mount_urls(m, arg, prefix, ns2, chain, conds, where["line"])
            return
        if isinstance(arg, (ast.List, ast.Tuple, ast.BinOp)):
            self.walk(m, prefix, ns2, len(chain), chain, var=f"<inline@{where['line']}>", items=self.eval_list(m, arg), conds=conds)
            return
        t = prog.infer(arg, Ctx(m, None, None))
        if t and t[0] == "mod":
            self.walk(t[1], prefix, ns2, len(chain), chain)
            return
        if t and t[0] == "var":
            self.walk(t[1], prefix, ns2, len(chain), chain, var=t[2])
            return
        self.unresolved.append({**where, "reason": f"include({ann_text(arg)}) unresolved"})


# --------------------------------------------------------------------------------------- DRF routers

class DrfRouters:
    ROUTER_NAMES = ("DefaultRouter", "SimpleRouter", "ExtendedDefaultRouter", "ExtendedSimpleRouter", "NestedSimpleRouter",
                    "NestedDefaultRouter", "OptionalSlashRouter", "BulkRouter")

    def __init__(self, prog: PyProgram):
        self.prog = prog
        self.routers: dict = {}    # key -> {file, line, trailing_slash, default, parent, lookup, regs: []}
        self.mounts: dict = {}
        self._collect()

    def _is_router_call(self, m, call) -> bool:
        if not isinstance(call, ast.Call):
            return False
        t = self.prog.infer(call.func, Ctx(m, None, None))
        if t and t[0] == "ext" and t[1].split(".")[-1] in self.ROUTER_NAMES and "ninja" not in t[1]:
            return True
        if t and t[0] == "type":
            lin = self.prog.lineage(t[1]) + [t[1].qual]
            if any("ninja" in x for x in lin):
                return False
            return any(x.split(".")[-1] in self.ROUTER_NAMES + ("BaseRouter",) for x in lin)
        return False

    def router_key(self, m: ModInfo, e):
        t = self.prog.infer(e, Ctx(m, None, None)) if isinstance(e, ast.Name) else None
        if isinstance(e, ast.Name):
            r = self.prog.resolve_name(m, e.id)
            if r and r[0] == "var" and (r[1].name, r[2]) in self.routers:
                return (r[1].name, r[2])
        if isinstance(e, ast.Attribute):
            t = self.prog.infer(e.value, Ctx(m, None, None))
            if t and t[0] == "mod" and (t[1].name, e.attr) in self.routers:
                return (t[1].name, e.attr)
        return None

    def _collect(self):
        prog = self.prog
        for m in prog.modules.values():
            for st in m.tree.body:
                if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name) and self._is_router_call(m, st.value):
                    call = st.value
                    ts = kwarg(call, "trailing_slash")
                    tsv = True
                    if isinstance(ts, ast.Constant):
                        tsv = ts.value if isinstance(ts.value, bool) else (ts.value != "")
                    cls = (dotted(call.func) or "").split(".")[-1]
                    info = {"file": m.file, "line": st.lineno, "trailing_slash": tsv, "class": cls,
                            "default": "Default" in cls, "regs": [], "parent": None}
                    if cls.startswith("Nested") and len(call.args) >= 2:
                        info["parent"] = self.router_key(m, call.args[0])
                        info["parent_prefix"] = const_str(call.args[1])
                        info["lookup"] = const_str(kwarg(call, "lookup")) or "parent"
                    self.routers[(m.name, st.targets[0].id)] = info
        for m in prog.modules.values():
            for st in ast.walk(m.tree):
                if isinstance(st, ast.Call) and isinstance(st.func, ast.Attribute) and st.func.attr == "register" and len(st.args) >= 2:
                    k = self.router_key(m, st.func.value)
                    if not k:
                        continue
                    prefix = const_str(st.args[0])
                    self.routers[k]["regs"].append({"prefix": prefix if prefix is not None else "{?}", "viewset": st.args[1],
                                                    "module": m, "line": st.lineno, "file": m.file,
                                                    "basename": const_str(kwarg(st, "basename") or kwarg(st, "base_name"))})
                elif isinstance(st, ast.Call) and isinstance(st.func, ast.Attribute) and st.func.attr == "extend" and \
                        isinstance(st.func.value, ast.Attribute) and st.func.value.attr == "registry" and st.args:
                    k = self.router_key(m, st.func.value.value)
                    src = st.args[0]
                    k2 = self.router_key(m, src.value) if isinstance(src, ast.Attribute) else None
                    if k and k2:
                        self.routers[k].setdefault("extends", []).append(k2)

    def regs(self, key, depth=0):
        r = self.routers.get(key)
        if not r or depth > 5:
            return []
        out = list(r["regs"])
        for k2 in r.get("extends", []):
            out += self.regs(k2, depth + 1)
        return out


# --------------------------------------------------------------------------------------- django-ninja

class Ninja:
    def __init__(self, prog: PyProgram):
        self.prog = prog
        self.objs: dict = {}        # (mod, var) -> {kind: api|router, file, line, auth, call}
        self.children: dict = {}    # parent key -> [(prefix, child key, file, line, auth)]
        self.ops: list[dict] = []
        self.controllers: list = []
        self._collect()

    def _kind_of_call(self, m, call):
        if not isinstance(call, ast.Call):
            return None
        t = self.prog.infer(call.func, Ctx(m, None, None))
        names = []
        if t and t[0] == "ext":
            names = [t[1]]
        elif t and t[0] == "type":
            names = [t[1].qual] + self.prog.lineage(t[1])
        for n in names:
            last = n.split(".")[-1]
            if ("ninja" in n or "." not in n) and last in NINJA_API:
                return "api"
            if ("ninja" in n) and last in NINJA_ROUTER:
                return "router"
        return None

    def api_key(self, m: ModInfo, e):
        if isinstance(e, ast.Name):
            r = self.prog.resolve_name(m, e.id)
            if r and r[0] == "var" and (r[1].name, r[2]) in self.objs:
                return (r[1].name, r[2])
        if isinstance(e, ast.Attribute):
            t = self.prog.infer(e.value, Ctx(m, None, None))
            if t and t[0] == "mod" and (t[1].name, e.attr) in self.objs:
                return (t[1].name, e.attr)
        return None

    def _collect(self):
        prog = self.prog
        for m in prog.modules.values():
            for st in m.tree.body:
                if isinstance(st, (ast.Assign, ast.AnnAssign)):
                    tgt = st.targets[0] if isinstance(st, ast.Assign) else st.target
                    val = st.value
                    if isinstance(val, ast.IfExp):  # api = NinjaAPI() if settings.DEBUG else NinjaAPI(docs_url=None)
                        val = val.body if self._kind_of_call(m, val.body) else val.orelse
                    k = self._kind_of_call(m, val) if val is not None else None
                    if k and isinstance(tgt, ast.Name):
                        call = val
                        self.objs[(m.name, tgt.id)] = {"kind": k, "file": m.file, "line": st.lineno,
                                                       "auth": kwarg(call, "auth"), "module": m,
                                                       "csrf": kwarg(call, "csrf"), "by_alias": kwarg(call, "by_alias"),
                                                       "urls_namespace": const_str(kwarg(call, "urls_namespace")),
                                                       "version": const_str(kwarg(call, "version"))}
        for m in prog.modules.values():
            for st in ast.walk(m.tree):
                if isinstance(st, ast.Call) and isinstance(st.func, ast.Attribute) and st.func.attr == "add_router" and len(st.args) >= 2:
                    pk = self.api_key(m, st.func.value)
                    if not pk:
                        continue
                    pref = const_str(st.args[0])
                    child = st.args[1]
                    ck = None
                    s = const_str(child)
                    if s:
                        mod, _, var = s.rpartition(".")
                        if (mod, var) in self.objs:
                            ck = (mod, var)
                        else:
                            mm = prog.module(mod)
                            if mm and (mm.name, var) in self.objs:
                                ck = (mm.name, var)
                    else:
                        ck = self.api_key(m, child)
                    self.children.setdefault(pk, []).append({"prefix": pref if pref is not None else "{?}", "child": ck,
                                                             "file": m.file, "line": st.lineno, "auth": kwarg(st, "auth"),
                                                             "child_text": ann_text(child)})
        # operations
        for f in prog.funcs.values():
            for d in f.decorators:
                if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)):
                    continue
                verb = d.func.attr
                if verb not in HTTP_METHODS and verb != "api_operation":
                    continue
                key = self.api_key(f.module, d.func.value)
                if not key:
                    continue
                if verb == "api_operation":
                    ms = d.args[0] if d.args else kwarg(d, "methods")
                    methods = [const_str(x).upper() for x in (ms.elts if isinstance(ms, (ast.List, ast.Tuple)) else []) if const_str(x)]
                    pth = d.args[1] if len(d.args) > 1 else kwarg(d, "path")
                else:
                    methods = [verb.upper()]
                    pth = d.args[0] if d.args else kwarg(d, "path")
                p = const_str(pth)
                self.ops.append({"key": key, "methods": methods, "path": p if p is not None else "{?}", "func": f,
                                 "line": d.lineno, "file": f.file, "dec": d, "auth": kwarg(d, "auth"),
                                 "auth_set": any(k.arg == "auth" for k in d.keywords),
                                 "response": kwarg(d, "response"), "by_alias": kwarg(d, "by_alias"),
                                 "url_name": const_str(kwarg(d, "url_name"))})
        # ninja-extra controllers
        for c in prog.classes.values():
            for d in c.decorators:
                if isinstance(d, ast.Call) and (dotted(d.func) or "").split(".")[-1] in ("api_controller", "Controller"):
                    pref = const_str(d.args[0]) if d.args else (const_str(kwarg(d, "prefix_or_class")) or "")
                    self.controllers.append({"cls": c, "prefix": pref or "", "auth": kwarg(d, "auth"), "line": d.lineno})

    def mount_chains(self, mounts: dict) -> dict:
        """object key -> list of (prefix chain [str], auth chain [ast|None], evidence chain, mounted)."""
        out = {}
        roots = [k for k, v in self.objs.items() if v["kind"] == "api"]
        for r in roots:
            ms = mounts.get(r) or []
            base = [(m["prefix"], m) for m in ms] or [(None, None)]
            for pre, mev in base:
                stack = [(r, [pre or ""], [self.objs[r]["auth"]], [mev] if mev else [], 0)]
                while stack:
                    k, pres, auths, ev, d = stack.pop()
                    out.setdefault(k, []).append({"prefixes": pres, "auths": auths, "evidence": ev, "mounted": pre is not None,
                                                  "api": r})
                    if d > 8:
                        continue
                    for ch in self.children.get(k, []):
                        if ch["child"] is None:
                            continue
                        stack.append((ch["child"], pres + [ch["prefix"]],
                                      auths + [ch["auth"], self.objs.get(ch["child"], {}).get("auth")], ev + [ch], d + 1))
        return out

    @staticmethod
    def full_path(mount_prefix: str, router_prefixes: list[str], op_path: str) -> str:
        # ninja: "/".join([prefix, path]) then collapse '//' and lstrip('/'); Django mount prefix is prepended as-is
        inner = "/".join(x for x in router_prefixes + [op_path] if x)
        while "//" in inner:
            inner = inner.replace("//", "/")
        inner = inner.lstrip("/")
        s = (mount_prefix or "") + inner  # Django concatenates the mount pattern and ninja's route
        s = re.sub(r"\{(?:\w+:)?(\w+)\}", r"{\1}", s)
        return "/" + s

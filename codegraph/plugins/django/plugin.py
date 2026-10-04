"""Django framework plugin (on the Python plugin).

register_hooks: builds the model registry (models.py) and adds ORM type rules to the Python inference
  (Model.objects.* -> queryset, .get()/.first()/.create() -> instance, FK attribute -> related model,
  reverse relations, get_object_or_404).
contribute:
  * settings modules (DJANGO_SETTINGS_MODULE or */settings*.py): config:settings.KEY nodes, READS_ENV from the
    keys whose value reads the environment, READS_CONFIG for `settings.X` / getattr(settings, 'X');
  * models -> table/column nodes (Meta.db_table or <app_label>_<model>), FK/M2M relations, ORM
    READS/WRITES_TABLE and READS/WRITES_COLUMN edges;
  * urlconfs from ROOT_URLCONF (or every unincluded urls module): path/re_path/url/include (nested, namespaces,
    i18n_patterns), function views (require_http_methods / api_view), class-based views (method handlers,
    generic views), DRF routers (ViewSets, @action, trailing_slash, nested routers), model view registries
    (register_model_view / include(get_model_urls(...))), django-ninja (NinjaAPI,
    Router, add_router chains, @router.<verb>, auth inheritance, request/response schemas), ninja-extra
    controllers; route nodes are http_route entry points with ROUTES_TO edges;
  * wire schemas (ninja/pydantic Schema, DRF serializers) as class attrs.schema_fields, USES_SCHEMA edges;
  * Channels websocket routes (websocket entry points) and group_send -> consumer handler DISPATCHES;
  * Celery tasks (queue_job entries, .delay/.apply_async/.s DISPATCHES, beat schedules = scheduled entries);
  * signals (@receiver / .connect -> listener entries; model writes that fire post_save/post_delete; custom
    Signal.send);
  * management commands (management_command entries = operator-only), call_command DISPATCHES;
  * admin registrations (admin_panel entries = operator-only) with table reads/writes.
"""
from __future__ import annotations

import ast
import re

from ... import presets
from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...core.plugin import FrameworkPlugin, GraphBuilder, Project
from ..python.plugin import ClassInfo, Ctx, FuncInfo, ModInfo, PyProgram, ann_text, const_str, dotted, kwarg, walk_body
from .models import Models, emit_models, orm_edges
from .schemas import Schemas
from .urls import ACTION_ROUTES, GENERIC_METHODS, HTTP_METHODS, VIEWSET_ACTIONS, DrfRouters, ModelViewRegistry, Ninja, UrlResolver, regex_to_template
from . import extras

# view-level access checks recorded on route nodes (attrs.access); `routes` / `search` read them
# (codegraph/presets/django.yaml `access`)
ACCESS_DECORATORS = set(presets.values("django", "access", "decorators", default=[]))
ACCESS_NAME = re.compile(presets.values("django", "access", "name_pattern"), re.I)
ACCESS_OPT_OUT = re.compile(presets.values("django", "access", "opt_out_pattern"), re.I)   # login_not_required, csrf_exempt
ACCESS_MIXINS = set(presets.values("django", "access", "mixins", default=[]))
SECRET_NAME = re.compile(presets.values("common", "secret", "token_pattern"))      # on word tokens (verify_signature)


def project_access_rx(project) -> re.Pattern | None:
    """The project's .cg.yaml auth / secret extra_patterns: decorators matching them are recorded as access checks."""
    cfg = (project.options.get("config") or {}) if project is not None else {}
    pats = [p for k in ("auth", "secret") for p in ((cfg.get(k) or {}).get("extra_patterns") or [])]
    return re.compile("|".join(f"(?:{p})" for p in pats), re.I) if pats else None

CONSUMER_BASES = ("WebsocketConsumer", "AsyncWebsocketConsumer", "JsonWebsocketConsumer", "AsyncJsonWebsocketConsumer",
                  "AsyncConsumer", "SyncConsumer", "AsyncHttpConsumer", "GenericAsyncAPIConsumer", "ObserverModelInstanceMixin")
CONSUMER_HANDLERS = ("connect", "receive", "receive_json", "disconnect", "websocket_connect", "websocket_receive",
                     "websocket_disconnect", "handle", "http_request")
DRF_HOOKS = ("get_queryset", "get_serializer_class", "get_object", "perform_create", "perform_update", "perform_destroy",
             "filter_queryset", "get_serializer_context", "get_permissions", "initial", "paginate_queryset", "get_serializer")


def mod_of(path):
    return ("/".join(path.split("/")[:-1]) or None) if path else None


class DjangoPlugin(FrameworkPlugin):
    name, language = "django", "python"

    def detect(self, project: Project) -> bool:
        if project.exists("manage.py") or any(project.root.glob("*/manage.py")) or any(project.root.glob("*/*/manage.py")):
            return True
        for f in ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements/base.txt"):
            p = project.root / f
            if p.exists():
                try:
                    txt = p.read_text(errors="replace")
                except OSError:
                    continue
                if re.search(r"(?im)^\s*[\"']?(django|djangorestframework|django-ninja)\b", txt) or \
                        re.search(r"(?i)[\"'](django|django-ninja|djangorestframework)[<>=~\"' ]", txt):
                    return True
        return False

    def register_hooks(self, prog: PyProgram) -> None:
        self.prog = prog
        self.models = Models(prog)
        self.models.build()
        prog.attr_rules.append(self.models.attr_rule)
        prog.call_rules.append(self.models.call_rule)

    def contribute(self, project: Project, builder: GraphBuilder, prog: PyProgram) -> dict:
        self.b = b = builder
        self.prog = prog
        self.access_rx = project_access_rx(project)
        st = {}
        self.schemas = Schemas(prog, self.models)
        from .shapes import ShapeBuilder
        self.shapes = ShapeBuilder(self)
        st["models"] = emit_models(self.models, b)
        st["settings"] = extras.settings(self)
        self.drf = DrfRouters(prog)
        self.viewreg = ModelViewRegistry(prog, self.models)
        self.ninja = Ninja(prog)
        self.urls = UrlResolver(prog, self)
        self.route_list = self.urls.routes
        roots = self.url_roots()
        for m in roots:
            self.urls.walk(m, "", [], 0, [])
        orphan_roots = []
        if self.root_urlconf_found:
            for m in prog.modules.values():
                if m.name not in self.urls.included and "urlpatterns" in m.vars and "test" not in m.name:
                    orphan_roots.append(m.name)
        self.ninja_routes()
        self.drf_routes()
        self.registry_routes()
        self.channels()
        n_routes = self.emit_routes()
        ops = {"orm_reads": 0, "orm_writes": 0}
        for f in prog.funcs.values():
            orm_edges(self.models, b, f, ops)
        st["orm"] = ops
        st["settings_reads"] = extras.settings_reads(self)
        st["admin"] = extras.admin(self)
        st["signals"] = extras.signals(self)
        st["celery"] = extras.celery(self)
        st["channels_dispatch"] = extras.group_sends(self)
        st["commands"] = extras.commands(self)
        st["schemas"] = self.emit_schemas()
        st["routes"] = n_routes
        st["root_urlconf"] = [m.name for m in roots]
        self.urls.unresolved.extend(self.viewreg.unresolved)
        st["urlconf_unresolved"] = self.urls.unresolved[:30]
        prog.url_unresolved = self.urls.unresolved  # read by the blind-spot detectors (codegraph/blindspots.py)
        st["urlconf_unincluded"] = orphan_roots[:30]
        st["ninja"] = {"apis": sum(1 for v in self.ninja.objs.values() if v["kind"] == "api"),
                       "routers": sum(1 for v in self.ninja.objs.values() if v["kind"] == "router"),
                       "operations": len(self.ninja.ops), "unmounted_ops": self._unmounted_ops}
        st["drf"] = {"routers": len(self.drf.routers), "registrations": sum(len(r["regs"]) for r in self.drf.routers.values())}
        st["view_registry"] = {"registrations": sum(len(v) for v in self.viewreg.regs.values()), "mounts": len(self.viewreg.mounts)}
        return st

    def setting_str(self, key: str) -> str | None:
        for m, v in reversed(self.settings_values.get(key, [])):
            s = const_str(v)
            if s:
                return s
        return None

    def url_roots(self) -> list[ModInfo]:
        prog = self.prog
        self.root_urlconf_found = False
        rcs = []
        for m, v in self.settings_values.get("ROOT_URLCONF", []):
            s = const_str(v)
            if s and prog.module(s) and prog.module(s) not in rcs:
                rcs.append(prog.module(s))
        if rcs:  # one per Django project in the repo
            self.root_urlconf_found = True
            return rcs
        cands = [m for m in prog.modules.values() if "urlpatterns" in m.vars]
        included = set()
        for m in cands:
            for sub in ast.walk(m.tree):
                if isinstance(sub, ast.Call) and (dotted(sub.func) or "").split(".")[-1] == "include" and sub.args:
                    a = sub.args[0]
                    if isinstance(a, ast.Tuple) and a.elts:
                        a = a.elts[0]
                    s = const_str(a)
                    if s:
                        tm = prog.module(s)
                        included.add(tm.name if tm else s)
                    else:
                        t = prog.infer(a, Ctx(m, None, None))
                        if t and t[0] == "mod":
                            included.add(t[1].name)
        return sorted((m for m in cands if m.name not in included), key=lambda m: m.name)

    # ------------------------------------------------------------------ views -> routes
    def http_methods_of_func(self, f: FuncInfo) -> tuple[list[str], str | None]:
        for d in f.decorators:
            name = (dotted(d.func if isinstance(d, ast.Call) else d) or "").split(".")[-1]
            if name in ("require_GET", "require_safe"):
                return ["GET"], name
            if name == "require_POST":
                return ["POST"], name
            if name in ("require_http_methods", "api_view"):
                a = (d.args[0] if d.args else kwarg(d, "http_method_names")) if isinstance(d, ast.Call) else None
                if isinstance(a, (ast.List, ast.Tuple)):
                    return [const_str(x).upper() for x in a.elts if const_str(x)], name
                if name == "api_view":
                    return ["GET"], name
        return ["ANY"], None

    def _route(self, **kw):
        kw.setdefault("framework", "django")
        kw.setdefault("mounted", True)
        if not kw["uri"].startswith("/"):
            kw["uri"] = "/" + kw["uri"]
        self.route_list.append(kw)

    def emit_fbv(self, f: FuncInfo, full, where, name, ns, chain, conds, ok, raw):
        methods, how = self.http_methods_of_func(f)
        fw = "drf" if how == "api_view" else "django"
        for mth in methods:
            self._route(method=mth, uri=full, file=where["file"], line=where["line"], handler=f, view=f.qual, framework=fw,
                        name=name, namespace=":".join(n for n in ns if n) or None, conf=EXACT if ok else HEURISTIC,
                        chain=chain, conds=list(conds), method_source=how or "any (function view)", raw=raw)

    def emit_cbv(self, t, expr, full, where, name, ns, chain, conds, ok, actions, raw):
        prog = self.prog
        base = dict(uri=full, file=where["file"], line=where["line"], name=name, namespace=":".join(n for n in ns if n) or None,
                    conf=EXACT if ok else HEURISTIC, chain=chain, conds=list(conds), raw=raw)
        if not t or t[0] != "type":
            ext = t[1] if t and t[0] in ("ext", "einst") else ann_text(expr)
            last = (ext or "").split(".")[-1]
            for mth in sorted(GENERIC_METHODS.get(last) or []) or ["ANY"]:
                self._route(method=mth, handler=None, view=ext, external_view=ext, **base)
            return
        c: ClassInfo = t[1]
        if prog.subclass_of(c, *CONSUMER_BASES):
            hs = [prog.find_method(c, h) for h in CONSUMER_HANDLERS]
            self._route(method="WS", handler=c, handlers=[h for h in hs if h], view=c.qual, framework="channels",
                        entry_kind="websocket", **base)
            return
        if actions:
            for verb, act in actions.items():
                h = prog.find_method(c, act) if act else None
                self._route(method=verb.upper(), handler=h or c, view=f"{c.qual}.{act}", framework="drf", action=act,
                            viewset=c, generic=h is None, **base)
            return
        found = [(v.upper(), prog.find_method(c, v)) for v in HTTP_METHODS if prog.find_method(c, v)]
        ext_ms = set()
        for e in prog.ext_bases(c):
            ext_ms |= GENERIC_METHODS.get(e.split(".")[-1], set())
        hm = c.attrs.get("http_method_names")
        allowed = {const_str(x).upper() for x in hm[0].elts if const_str(x)} if hm and isinstance(hm[0], (ast.List, ast.Tuple)) else None
        verbs = {v for v, _ in found} | ext_ms
        if allowed:
            verbs &= allowed
        fw = "drf" if prog.subclass_of(c, "APIView", "GenericAPIView", "ViewSet", "GenericViewSet") else "django"
        if not verbs:
            self._route(method="ANY", handler=c, view=c.qual, framework=fw, **base)
            return
        hmap = dict(found)
        for v in sorted(verbs):
            if v in ("HEAD", "OPTIONS") and v not in hmap:
                continue
            self._route(method=v, handler=hmap.get(v) or c, view=c.qual, framework=fw, generic=v not in hmap, viewset=c, **base)

    # ---- DRF routers
    def viewset_actions(self, c: ClassInfo) -> set[str]:
        acts = set()
        for e in self.prog.ext_bases(c):
            acts |= VIEWSET_ACTIONS.get(e.split(".")[-1], set())
        for a, _, _ in ACTION_ROUTES:
            if self.prog.find_method(c, a):
                acts.add(a)
        return acts

    def class_attr_str(self, c, name):
        for k in [c] + [x[1] for x in self.prog.mro(c) if x[0] == "type"]:
            if name in k.attrs:
                return const_str(k.attrs[name][0])
        return None

    def all_methods(self, c: ClassInfo):
        seen = set()
        for k in [c] + [x[1] for x in self.prog.mro(c) if x[0] == "type"]:
            for n, f in k.methods.items():
                if n not in seen:
                    seen.add(n)
                    yield f

    def registry_routes(self):
        """Expand model-view registry mounts into the same class-based routes path() would emit. A (verb, path) that a
        handwritten path() (or an earlier mount) already routes is kept as it was."""
        start = len(self.route_list)
        seen = {(r["method"], re.sub(r"/{2,}", "/", r["uri"])) for r in self.route_list}
        for mt in self.viewreg.mounts:
            for reg in self.viewreg.matching(mt["app"], mt["model"], mt["detail"]):
                sub = f"{reg['path']}/" if reg["path"] else ""
                full = mt["prefix"] + sub
                name = f"{mt['model']}_{reg['name']}" if reg["name"] else mt["model"]
                where = {"file": reg["file"], "line": reg["line"]}
                chain = list(mt["chain"])
                if not chain or chain[-1] != f"{mt['file']}:{mt['line']}":
                    chain.append(f"{mt['file']}:{mt['line']}")
                target = reg["target"]
                if isinstance(target, FuncInfo):
                    self.emit_fbv(target, full, where, name, mt["ns"], chain, mt["conds"], True, None)
                else:
                    self.emit_cbv(("type", target), None, full, where, name, mt["ns"], chain, mt["conds"], True, None, None)
        kept = []
        for r in self.route_list[start:]:
            k = (r["method"], re.sub(r"/{2,}", "/", "/" + r["uri"].lstrip("/")))
            if k not in seen:
                seen.add(k)
                kept.append(r)
        self.route_list[start:] = kept

    def drf_routes(self):
        prog = self.prog
        for key, r in self.drf.routers.items():
            mounts = self.drf.mounts.get(key) or []
            if r.get("parent") and not mounts:
                mounts = [dict(pm) for pm in self.drf.mounts.get(r["parent"]) or []]
            mounted = bool(mounts)
            if not mounts:
                mounts = [{"prefix": "", "file": r["file"], "line": r["line"], "chain": [], "conds": (), "ns": []}]
            slash = "/" if r["trailing_slash"] else ""
            for mt in mounts:
                pre = mt["prefix"]
                if r.get("parent"):
                    pre = pre + (r.get("parent_prefix") or "") + "/{" + r.get("lookup", "parent") + "_pk}/"
                if r["default"] and not r.get("parent"):
                    self._route(method="GET", uri=pre or "/", file=r["file"], line=r["line"], handler=None, view="api-root",
                                framework="drf", conf=EXACT, chain=mt["chain"], conds=list(mt["conds"]), mounted=mounted,
                                name="api-root", namespace=None)
                for reg in self.drf.regs(key):
                    t = prog.infer(reg["viewset"], Ctx(reg["module"], None, None))
                    kw = dict(file=reg["file"], line=reg["line"], framework="drf", conf=EXACT if reg["prefix"] != "{?}" else HEURISTIC,
                              chain=mt["chain"] + [f"{reg['file']}:{reg['line']}"], conds=list(mt["conds"]), mounted=mounted,
                              namespace=":".join(n for n in mt.get("ns", []) if n) or None, router=f"{r['file']}:{r['line']}")
                    pfx = reg["prefix"]
                    if not t or t[0] != "type":
                        self._route(method="ANY", uri=pre + pfx + slash, handler=None, view=ann_text(reg["viewset"]), **kw)
                        continue
                    c = t[1]
                    lk = self.class_attr_str(c, "lookup_url_kwarg") or self.class_attr_str(c, "lookup_field") or "pk"
                    acts = self.viewset_actions(c)
                    base = reg["basename"] or self.drf_basename(c) or pfx
                    for act, verb, detail in ACTION_ROUTES:
                        if act not in acts:
                            continue
                        h = prog.find_method(c, act)
                        self._route(method=verb, uri=pre + pfx + ("/{" + lk + "}" if detail else "") + slash, handler=h or c,
                                    view=f"{c.qual}.{act}", action=act, viewset=c, generic=h is None,
                                    name=f"{base}-{'detail' if detail else 'list'}", **kw)
                    for f in self.all_methods(c):
                        for d in f.decorators:
                            dn = (dotted(d.func if isinstance(d, ast.Call) else d) or "").split(".")[-1]
                            if dn not in ("action", "detail_route", "list_route"):
                                continue
                            detail, ms, url_path, uname = dn == "detail_route", ["GET"], f.name, None
                            if isinstance(d, ast.Call):
                                dv = kwarg(d, "detail")
                                if isinstance(dv, ast.Constant):
                                    detail = bool(dv.value)
                                mv = kwarg(d, "methods")
                                if isinstance(mv, (ast.List, ast.Tuple)):
                                    ms = [const_str(x).upper() for x in mv.elts if const_str(x)] or ms
                                url_path = const_str(kwarg(d, "url_path")) or url_path
                                if "(" in url_path or "\\" in url_path:  # regex url_path: versions/(?P<version_id>\d+)
                                    url_path = regex_to_template(url_path)[0].strip("/")
                                uname = const_str(kwarg(d, "url_name"))
                            uri = pre + pfx + ("/{" + lk + "}" if detail else "") + "/" + url_path + slash
                            # DRF names an extra action's route '<basename>-<url_name>' (url_name defaults to the
                            # method name with '-' for '_'), so reverse('review-upvote') finds it
                            rname = f"{base}-{uname or f.name.replace('_', '-')}"
                            for mth in ms:
                                self._route(method=mth, uri=uri, handler=f, view=f.qual, action=f.name, viewset=c,
                                            name=rname, extra_action=True, **kw)

    # ---- ninja
    def ninja_routes(self):
        chains = self.ninja.mount_chains(self.urls.mounts)
        self._unmounted_ops = 0
        orphan = [{"prefixes": [""], "auths": [None], "evidence": [], "mounted": False, "api": None}]
        for op in self.ninja.ops:
            for ch in chains.get(op["key"]) or orphan:
                uri = Ninja.full_path(ch["prefixes"][0] or "", ch["prefixes"][1:], op["path"])
                if op["auth_set"]:
                    auth, src = op["auth"], "operation"
                else:
                    auth, src = None, None
                    for a in reversed(ch["auths"]):
                        if a is not None:
                            auth, src = a, "router/api"
                            break
                auth_txt = None if auth is None or (isinstance(auth, ast.Constant) and auth.value is None) else ann_text(auth)
                if not ch["mounted"]:
                    self._unmounted_ops += 1
                ev = [f"{e['file']}:{e['line']}" for e in ch["evidence"] if e]
                for mth in op["methods"]:
                    self._route(method=mth, uri=uri, file=op["file"], line=op["line"], handler=op["func"], view=op["func"].qual,
                                framework="ninja", conf=EXACT if "{?}" not in uri else HEURISTIC, chain=ev, conds=[],
                                mounted=ch["mounted"], name=op["url_name"], namespace=None, auth=auth_txt,
                                auth_source=src or "none", ninja_op=op,
                                api=f"{ch['api'][0]}.{ch['api'][1]}" if ch.get("api") else None)
        for ctl in self.ninja.controllers:
            c = ctl["cls"]
            for f in c.methods.values():
                for d in f.decorators:
                    if not isinstance(d, ast.Call):
                        continue
                    dn = dotted(d.func) or ""
                    last = dn.split(".")[-1]
                    verb = last[5:] if last.startswith("http_") else (last if dn.startswith("route.") else None)
                    if verb not in HTTP_METHODS:
                        continue
                    p = const_str(d.args[0]) if d.args else (const_str(kwarg(d, "path")) or "")
                    self._route(method=verb.upper(), uri=Ninja.full_path("", [ctl["prefix"]], p or ""), file=f.file, line=d.lineno,
                                handler=f, view=f.qual, framework="ninja-extra", conf=RESOLVED, chain=[], conds=[], mounted=False,
                                name=None, namespace=None, auth=ann_text(kwarg(d, "auth") or ctl["auth"]), auth_source="controller")

    # ---- channels
    def channels(self):
        prog = self.prog
        seen = set()
        for m in prog.modules.values():
            roots = [("websocket_urlpatterns", None)] if "websocket_urlpatterns" in m.vars else []
            for sub in ast.walk(m.tree):
                if isinstance(sub, ast.Call) and (dotted(sub.func) or "").split(".")[-1] == "URLRouter" and sub.args:
                    roots.append((None, sub.args[0]))
            for var, expr in roots:
                if var:
                    if (m.name, var) not in seen:
                        seen.add((m.name, var))
                        self.urls.walk(m, "", [], 0, [], var=var)
                    continue
                t = prog.infer(expr, Ctx(m, None, None)) if isinstance(expr, (ast.Name, ast.Attribute)) else None
                if t and t[0] == "var":
                    if (t[1].name, t[2]) not in seen:
                        seen.add((t[1].name, t[2]))
                        self.urls.walk(t[1], "", [], 0, [], var=t[2])
                elif isinstance(expr, (ast.List, ast.Tuple)):
                    self.urls.walk(m, "", [], 0, [], var=f"<urlrouter@{expr.lineno}>", items=self.urls.eval_list(m, expr))

    # ------------------------------------------------------------------ route nodes
    def emit_routes(self) -> int:
        b = self.b
        n = 0
        for r in self.route_list:
            uri = re.sub(r"/{2,}", "/", r["uri"])
            key = f"{r['method']} {uri}"
            h = r.get("handler")
            attrs = {"uri": uri, "method": r["method"], "framework": r.get("framework"), "name": r.get("name"),
                     "namespace": r.get("namespace"), "view": r.get("view"), "mounted": r.get("mounted", True),
                     "urlconf_chain": r.get("chain") or None, "conditions": r.get("conds") or None,
                     "trailing_slash": uri.endswith("/"), "path_params": re.findall(r"\{(\w+)\}", uri)}
            for k in ("auth", "auth_source", "action", "external_view", "method_source", "generic", "router", "api", "raw", "extra_action"):
                if r.get(k) is not None:
                    attrs[k] = r[k]
            access = self.access_of(h, r.get("viewset"))
            if access:
                attrs["access"] = access
            if isinstance(h, (FuncInfo, ClassInfo)):
                attrs["handler"] = h.id
            entry = r.get("entry_kind") or ("http_route" if r.get("mounted", True) else None)
            if r.get("framework") == "django-admin":
                entry = "admin_panel"
            rid = b.add_node("route", key, name=key, file=r["file"], line=r["line"], module=mod_of(r["file"]), lang="python",
                             entry_kind=entry, attrs=attrs)
            n += 1
            conf = r.get("conf", EXACT)
            if isinstance(h, (FuncInfo, ClassInfo)):
                b.add_edge(rid, h.id, "ROUTES_TO", r["file"], r["line"], conf)
            if isinstance(h, ClassInfo):
                for hh in r.get("handlers") or []:
                    b.add_edge(rid, hh.id, "ROUTES_TO", r["file"], r["line"], RESOLVED, via="consumer-handler")
                self.class_hooks(h)
            elif isinstance(h, FuncInfo) and h.cls is not None:
                self.class_hooks(h.cls)
            self.payload_facts(rid, r, h)
        return n

    access_rx = None

    def access_of(self, h, viewset=None) -> list[dict]:
        """Access checks declared on the view: auth decorators (login_required, permission_required, DRF
        @permission_classes, method_decorator(...)), access mixins (LoginRequiredMixin, ...) and DRF
        permission_classes / authentication_classes (class attribute or @action kwarg). Project-wide defaults
        (REST_FRAMEWORK DEFAULT_PERMISSION_CLASSES, middleware) are not per-view and are not listed."""
        out: list[dict] = []

        def add(name, via):
            if name and not any(o["name"] == name for o in out):
                out.append({"name": name, "via": via})

        def elems(v):
            if isinstance(v, (ast.List, ast.Tuple, ast.Set)):
                return [ann_text(x.func if isinstance(x, ast.Call) else x) for x in v.elts]
            return [ann_text(v)] if v is not None else []

        def decos(decs, via):
            for d in decs or []:
                fn = d.func if isinstance(d, ast.Call) else d
                nm = (dotted(fn) or "").split(".")[-1]
                if nm == "method_decorator" and isinstance(d, ast.Call) and d.args:
                    for x in elems(d.args[0]):
                        add(x.split(".")[-1], f"{via} method_decorator")
                elif nm in ("permission_classes", "authentication_classes") and isinstance(d, ast.Call) and d.args:
                    for x in elems(d.args[0]):
                        add(x.split(".")[-1], f"@{nm}")
                elif nm == "action" and isinstance(d, ast.Call):
                    for kw in ("permission_classes", "authentication_classes"):
                        for x in elems(kwarg(d, kw)):
                            add(x.split(".")[-1], f"@action {kw}")
                elif (nm in ACCESS_DECORATORS or ACCESS_NAME.search(nm) or SECRET_NAME.search(presets.name_tokens(nm))
                      or (self.access_rx and self.access_rx.search(nm))) and not ACCESS_OPT_OUT.search(nm):
                    add(nm, via)

        cls = h if isinstance(h, ClassInfo) else (h.cls if isinstance(h, FuncInfo) else None)
        if isinstance(h, FuncInfo):
            decos(h.decorators, "decorator")
        for c in [x for x in (cls, viewset) if isinstance(x, ClassInfo)][:1]:
            decos(c.decorators, "class decorator")
            for b in self.prog.lineage(c):
                last = b.split(".")[-1]
                if last in ACCESS_MIXINS or last.endswith("RequiredMixin"):
                    add(last, "mixin")
            for k in [c] + [x[1] for x in self.prog.mro(c) if x[0] == "type"]:
                for attr in ("permission_classes", "authentication_classes"):
                    if attr in k.attrs and not any(o["via"] == attr for o in out):
                        for x in elems(k.attrs[attr][0]):
                            add(x.split(".")[-1], attr)
        return out

    def class_hooks(self, c: ClassInfo):
        """Generic (inherited) view actions run the class's hook overrides and use its queryset/serializer."""
        if getattr(c, "_hooked", False):
            return
        c._hooked = True
        b, prog = self.b, self.prog
        for hname in DRF_HOOKS + ("get_context_data", "form_valid", "get_success_url", "dispatch", "setup"):
            f = prog.find_method(c, hname)
            if f:
                b.add_edge(c.id, f.id, "CALLS", f.file, f.line, RESOLVED, via="framework-hook")
        for attr, kind in (("queryset", "qs"), ("model", "type")):
            x = self.class_attr_expr(c, attr)
            if x is None:
                continue
            t = prog.infer(x[0], Ctx(x[1].module, None, x[1]))
            if t and t[0] == kind and self.models.table(t[1].qual):
                b.add_edge(c.id, f"table:{self.models.table(t[1].qual)}", "READS_TABLE", x[1].file, x[0].lineno, RESOLVED, via=attr)
        ser = self.class_attr_expr(c, "serializer_class")
        if ser is not None:
            t = prog.infer(ser[0], Ctx(ser[1].module, None, ser[1]))
            if t and t[0] == "type":
                b.add_edge(c.id, t[1].id, "USES_SCHEMA", ser[1].file, ser[0].lineno, RESOLVED, role="serializer_class")

    def drf_basename(self, c: ClassInfo) -> str | None:
        """DRF's default router basename: the lowercased model name of the viewset's `queryset`."""
        x = self.class_attr_expr(c, "queryset")
        if x is None:
            return None
        t = self.prog.infer(x[0], Ctx(x[1].module, None, x[1]))
        return t[1].name.lower() if t and t[0] == "qs" else None

    def class_attr_expr(self, c: ClassInfo, name):
        for k in [c] + [x[1] for x in self.prog.mro(c) if x[0] == "type"]:
            if name in k.attrs and k.attrs[name][0] is not None:
                return k.attrs[name][0], k
        return None

    # ---- payload facts (request/response shape per route)
    def schema_ref(self, e, ctx) -> dict | None:
        if e is None:
            return None
        if isinstance(e, ast.Subscript):
            head = (dotted(e.value) or "").split(".")[-1]
            sl = e.slice.elts[0] if isinstance(e.slice, ast.Tuple) else e.slice
            if head in ("Form", "Body", "Query", "Path", "File", "Optional", "List", "list", "Sequence", "Annotated", "Iterable"):
                r = self.schema_ref(sl, ctx)
                if r:
                    if head in ("List", "list", "Sequence", "Iterable"):
                        r["many"] = True
                    if head in ("Form", "Body", "Query", "File", "Path"):
                        r["location"] = head.lower()
                return r
            return None
        if isinstance(e, ast.List) and e.elts:
            r = self.schema_ref(e.elts[0], ctx)
            if r:
                r["many"] = True
            return r
        t = self.prog.infer(e, ctx)
        if t and t[0] == "type" and self.schemas.kind(t[1]):
            return {"class": t[1].qual, "many": False}
        return None

    def payload_facts(self, rid, r, h):
        b, prog = self.b, self.prog
        a = b.nodes[rid].attrs
        req, resp = {"schemas": [], "keys": [], "query": [], "files": []}, {"schemas": [], "keys": []}
        op = r.get("ninja_op")
        if isinstance(h, FuncInfo):
            ctx = Ctx(h.module, h, h.cls)
            if op is not None:
                self.ninja_signature(h, op, a, req, resp)
            self.body_facts(h, ctx, req, resp)
        vs = r.get("viewset")
        if isinstance(vs, ClassInfo) and r.get("framework") == "drf" and (r.get("generic") or not isinstance(h, FuncInfo) or
                                                                         r.get("action") in ("list", "create", "retrieve", "update", "partial_update")):
            ser = self.class_attr_expr(vs, "serializer_class")
            if ser is not None and not r.get("extra_action"):
                t = prog.infer(ser[0], Ctx(ser[1].module, None, ser[1]))
                if t and t[0] == "type" and self.schemas.kind(t[1]):
                    if r["method"] in ("POST", "PUT", "PATCH"):
                        req["schemas"].append({"class": t[1].qual, "location": "body", "via": "serializer_class",
                                               "partial": r["method"] == "PATCH"})
                    if r["method"] != "DELETE":
                        resp["schemas"].append({"class": t[1].qual, "many": r.get("action") == "list", "via": "serializer_class", "status": 200})
        if any(req.values()):
            a["request"] = req
        if any(resp.values()):
            a["response"] = resp

    def ninja_signature(self, h: FuncInfo, op, a, req, resp):
        b = self.b
        args = h.node.args
        params = list(args.posonlyargs) + list(args.args)
        defaults = [None] * (len(params) - len(args.defaults)) + list(args.defaults)
        params += list(args.kwonlyargs)
        defaults += list(args.kw_defaults)
        pp = set(a.get("path_params") or [])
        mctx = Ctx(h.module, None, None)
        for i, p in enumerate(params[1:], start=1):
            if p.arg in pp:
                continue
            d = defaults[i] if i < len(defaults) else None
            loc = None
            if isinstance(d, ast.Call):
                loc = {"Form": "form", "Body": "body", "Query": "query", "File": "file", "Path": "path"}.get((dotted(d.func) or "").split(".")[-1])
            ref = self.schema_ref(p.annotation, mctx) if p.annotation is not None else None
            ann = ann_text(p.annotation)
            if ref:
                ref["param"] = p.arg
                ref["location"] = ref.get("location") or loc or "body"
                req["schemas"].append(ref)
                b.add_edge(h.id, f"class:{ref['class']}", "USES_SCHEMA", h.file, h.line, EXACT, role="request", param=p.arg)
            elif ann and ("UploadedFile" in ann or loc == "file"):
                req["files"].append({"name": p.arg, "line": h.line, "file": h.file})
            else:
                req["query" if loc in (None, "query") else "keys"].append(
                    {"name": p.arg, "type": ann, "required": d is None, "file": h.file, "line": h.line, "location": loc or "query"})
        rsp = op.get("response")
        if isinstance(rsp, ast.Dict):
            for k, v in zip(rsp.keys, rsp.values):
                ref = self.schema_ref(v, mctx)
                if ref:
                    ref["status"] = k.value if isinstance(k, ast.Constant) else ann_text(k)
                    resp["schemas"].append(ref)
        elif rsp is not None:
            ref = self.schema_ref(rsp, mctx)
            if ref:
                ref["status"] = 200
                resp["schemas"].append(ref)
        for ref in resp["schemas"]:
            b.add_edge(h.id, f"class:{ref['class']}", "USES_SCHEMA", h.file, op["line"], EXACT, role="response", status=ref.get("status"))
        if op.get("by_alias") is not None:
            a["by_alias"] = ann_text(op["by_alias"])

    REQ_BASE = re.compile(r"(self\.)?request\.(data|POST|GET|query_params|FILES)")

    def body_facts(self, h: FuncInfo, ctx, req, resp):
        """Literal dict keys returned (status codes kept); keys read from request data; DRF serializers in/out."""
        prog = self.prog
        jvars = set()
        for sub in ast.walk(h.node):
            if isinstance(sub, ast.Assign) and isinstance(sub.targets[0], ast.Name):
                txt = ann_text(sub.value) or ""
                if re.search(r"json\.loads\(\s*(self\.)?request\.body", txt) or re.fullmatch(r"(self\.)?request\.(data|POST|GET|query_params)(\.copy\(\)|\.dict\(\))?", txt):
                    jvars.add(sub.targets[0].id)

        def loc(base):
            return "query" if base.endswith(("GET", "query_params")) else ("file" if base.endswith("FILES") else "body")
        for sub in ast.walk(h.node):
            if isinstance(sub, ast.Return) and sub.value is not None:
                status = None
                v = sub.value
                if isinstance(v, ast.Tuple) and len(v.elts) == 2 and isinstance(v.elts[0], ast.Constant):
                    status = v.elts[0].value
                for keys, line in self.dict_keys(v, ctx):
                    for k in keys:
                        resp["keys"].append({"name": k, "file": h.file, "line": line, **({"status": status} if status is not None else {})})
                try:
                    shp = self.shapes.shape(v.elts[1] if status is not None else v, ctx)
                except RecursionError:
                    shp = None
                if shp:
                    resp.setdefault("shapes", []).append({"status": status, "file": h.file, "line": sub.lineno, "keys": shp})
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in ("get", "pop", "getlist") and sub.args:
                k = const_str(sub.args[0])
                base = ann_text(sub.func.value) or ""
                if k and (self.REQ_BASE.fullmatch(base) or base in jvars):
                    req["keys"].append({"name": k, "file": h.file, "line": sub.lineno, "via": f"{base}.{sub.func.attr}",
                                        "optional": True, "location": loc(base)})
            if isinstance(sub, ast.Subscript) and isinstance(sub.ctx, ast.Load):
                k = const_str(sub.slice)
                base = ann_text(sub.value) or ""
                if k and (self.REQ_BASE.fullmatch(base) or base in jvars):
                    req["keys"].append({"name": k, "file": h.file, "line": sub.lineno, "via": f"{base}[]", "optional": False, "location": loc(base)})
            if isinstance(sub, ast.Call):
                t = prog.infer(sub.func, ctx)
                if t and t[0] == "type" and self.schemas.kind(t[1]) in ("drf", "drf_model"):
                    if kwarg(sub, "data") is not None:
                        pv = kwarg(sub, "partial")
                        req["schemas"].append({"class": t[1].qual, "location": "body", "via": f"{t[1].name}(data=...)", "line": sub.lineno,
                                               "partial": isinstance(pv, ast.Constant) and pv.value is True})
                        self.b.add_edge(h.id, t[1].id, "USES_SCHEMA", h.file, sub.lineno, RESOLVED, role="request")
                    else:
                        mv = kwarg(sub, "many")
                        resp["schemas"].append({"class": t[1].qual, "many": isinstance(mv, ast.Constant) and mv.value is True,
                                                "via": f"{t[1].name}(...)", "line": sub.lineno})
                        self.b.add_edge(h.id, t[1].id, "USES_SCHEMA", h.file, sub.lineno, RESOLVED, role="response")

    def dict_keys(self, e, ctx, depth=0) -> list:
        """[(keys, line)] for a returned expression: dict literal, (status, dict), JsonResponse/Response(dict),
        a local var assigned a dict literal, or a local helper returning one (one level)."""
        out = []
        if isinstance(e, ast.Tuple) and len(e.elts) == 2:
            e = e.elts[1]
        if isinstance(e, ast.Dict):
            ks = [const_str(k) for k in e.keys if const_str(k)]
            return [(ks, e.lineno)] if ks else []
        if isinstance(e, ast.Call):
            fn = (dotted(e.func) or "").split(".")[-1]
            if fn == "dict" and e.keywords:
                return [([k.arg for k in e.keywords if k.arg], e.lineno)]
            if fn in ("JsonResponse", "Response", "JSONResponse") and e.args:
                return self.dict_keys(e.args[0], ctx, depth)
            if depth < 1:
                t = self.prog.infer(e.func, ctx)
                if t and t[0] in ("func", "bound"):
                    f = t[1]
                    c2 = Ctx(f.module, f, f.cls)
                    for sub in ast.walk(f.node):
                        if isinstance(sub, ast.Return) and sub.value is not None:
                            out += self.dict_keys(sub.value, c2, depth + 1)
            return out
        if isinstance(e, ast.Name) and ctx.func is not None:
            for val, ann, kind in self.prog.local_vars(ctx).get(e.id, []):
                if isinstance(val, ast.Dict):
                    out += self.dict_keys(val, ctx, depth)
        return out

    def emit_schemas(self) -> dict:
        b = self.b
        n = 0
        for c in self.prog.classes.values():
            k = self.schemas.kind(c)
            if not k:
                continue
            fs = self.schemas.fields(c) or []
            b.nodes[c.id].attrs.update({"schema_kind": k, "schema_fields": fs})
            n += 1
            for fd in fs:
                fid = b.add_node("field", f"{c.qual}.{fd['name']}", name=fd["name"], fqn=f"{c.qual}.{fd['name']}", file=fd["file"],
                                 line=fd["line"], lang="python", attrs={k2: v for k2, v in fd.items() if k2 not in ("file", "line")})
                b.add_edge(c.id, fid, "CONTAINS", fd["file"], fd["line"], EXACT)
                if fd.get("ref"):
                    b.add_edge(c.id, f"class:{fd['ref']}", "USES_SCHEMA", fd["file"], fd["line"], EXACT, role="nested", field=fd["name"])
                if fd.get("from_model"):
                    tbl = self.models.table(fd["from_model"])
                    mf = self.models.field(fd["from_model"], fd["name"])
                    if tbl and mf and mf.get("column"):
                        b.add_edge(fid, f"column:{tbl}.{mf['column']}", "REFERS_TO", fd["file"], fd["line"], RESOLVED)
        return {"schema_classes": n}

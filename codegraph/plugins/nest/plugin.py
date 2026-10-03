"""NestJS framework layer (on the TypeScript plugin).

Reads the extractor's decorator facts (extractor/fw.mjs) and adds:

  modules     @Module({imports, controllers, providers, exports}) -> class attrs.nest_module; REFERENCES/BINDS edges
  DI          constructor / property injection resolved to a provider: class tokens, @Inject('TOKEN') / @Inject(SYM),
              {provide, useClass | useExisting | useFactory (its `new X()`) | useValue}; INJECTS consumer -> provider,
              calls through an injected member (this.repo.find()) to the provider's method (CALLS, resolved, via di)
              when the type checker could not (interface / token typed members); abstract class token -> BOUND_TO
  routes      @Controller(path | {path, version}) + @Get/@Post/@Put/@Patch/@Delete/@All/@Options/@Head/@Sse(path),
              app.setGlobalPrefix(p, {exclude}), app.enableVersioning({type: URI, prefix, defaultVersion}) + @Version,
              RouterModule.register([{path, module, children}]); route attrs: guards/interceptors/pipes/filters
              (@UseGuards ... and global useGlobalGuards / APP_GUARD), body DTO + fields (@Body() dto: Dto), query
              fields, custom decorators; USES_MIDDLEWARE -> guard canActivate / interceptor intercept / pipe transform
  GraphQL     @Resolver + @Query/@Mutation/@Subscription (from @nestjs/graphql) -> route `GRAPHQL <Type>.<field>`
  entries     @Cron/@Interval/@Timeout -> schedule (scheduled); Bull/BullMQ @Processor(+@Process / WorkerHost.process)
              -> job (queue_job), producers @InjectQueue('q') + this.q.add('name') -> DISPATCHES; @OnEvent -> listener,
              EventEmitter2.emit('evt') -> DISPATCHES; @MessagePattern/@EventPattern and @WebSocketGateway +
              @SubscribeMessage -> message (message_handler), ClientProxy.send/emit -> DISPATCHES;
              nest-commander @Command/@SubCommand -> command (cli_command, operator-only)
"""
from __future__ import annotations

import json
import re

from ...core.plugin import FrameworkPlugin, GraphBuilder, Project
from ..tsweb.common import (TEST_SKIP_RE, add_route, express_path, finish, fw_facts, has_server_framework, join_path,
                            last_name, merge_extractor_cfg, module_of, obj, ref_nodes, register, sval, svals)

ROUTE_DECOS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "All": "ANY",
               "Options": "OPTIONS", "Head": "HEAD", "Search": "SEARCH", "Sse": "GET"}
ENHANCERS = {"UseGuards": ("guards", "canActivate"), "UseInterceptors": ("interceptors", "intercept"),
             "UsePipes": ("pipes", "transform"), "UseFilters": ("filters", "catch")}
GLOBAL_ENHANCERS = {"useGlobalGuards": "UseGuards", "useGlobalInterceptors": "UseInterceptors", "useGlobalPipes": "UsePipes",
                    "useGlobalFilters": "UseFilters"}
APP_TOKENS = {"APP_GUARD": "UseGuards", "APP_INTERCEPTOR": "UseInterceptors", "APP_PIPE": "UsePipes", "APP_FILTER": "UseFilters"}
INJECTABLE = {"Injectable", "Controller", "Resolver", "Processor", "WebSocketGateway", "Command", "SubCommand", "Catch", "Module"}


ORM_INJECT = ("InjectRepository", "InjectModel", "InjectDataSource", "InjectEntityManager", "InjectConnection", "InjectRedis")


def _is_nest(d) -> bool:
    m = d.get("mod")
    return m is None or m.startswith("@nestjs/") or m == "nest-commander"


def _decos(x) -> dict:
    out = {}
    for d in x.get("decorators") or []:
        out.setdefault(last_name(d["name"]), d)
    return out


def _token(d) -> str | None:
    """Described provider token -> stable key."""
    if not isinstance(d, dict):
        return None
    if d.get("node"):
        return d["node"]
    if isinstance(d.get("s"), str):
        return "str:" + d["s"]
    if d.get("key"):
        return "key:" + d["key"]
    if d.get("ref"):
        return "ref:" + d["ref"]
    return None


def _pattern(d) -> str | None:
    if not isinstance(d, dict):
        return None
    if isinstance(d.get("s"), str):
        return d["s"]
    if d.get("obj") is not None:
        return json.dumps({k: _pattern(v) for k, v in sorted(d["obj"].items())}, separators=(",", ":"))
    if d.get("n") is not None:
        return str(d["n"])
    return d.get("ref")


class NestPlugin(FrameworkPlugin):
    name, language = "nest", "typescript"

    def detect(self, project: Project) -> bool:
        return has_server_framework(project, "nest") or project.exists("nest-cli.json")

    def register_hooks(self, ctx) -> None:
        root = ctx.project.root
        dirs = [d for d in ("src", "apps", "libs", "packages") if (root / d).is_dir()] or ["."]
        merge_extractor_cfg(ctx.extractor_cfg, src_dirs=dirs, skip_re=TEST_SKIP_RE)
        register(ctx, self.name)

    def contribute(self, project: Project, b: GraphBuilder, ctx) -> dict:
        F = fw_facts(ctx)
        st = {"modules": 0, "controllers": 0, "routes": 0, "providers": 0, "di_params": 0, "di_resolved": 0, "di_external": 0,
              "di_unresolved": 0, "di_calls": 0, "schedules": 0, "jobs": 0, "listeners": 0, "messages": 0, "commands": 0,
              "graphql_ops": 0}
        if not F:
            return {**st, "status": "no facts"}
        self.b, self.F = b, F
        self.classes = {c["id"]: c for c in F.get("classes") or []}
        self.by_name = {}
        for c in self.classes.values():
            if c.get("name"):
                self.by_name.setdefault(c["name"], []).append(c["id"])
        self.supers = {c["id"]: (c.get("extends") or {}).get("id") for c in self.classes.values()}
        self._modules(st)
        self._providers(st)
        self._di(st)
        self._routes(st)
        self._entries(st)
        st.update(finish(project, b, ctx, self.name))
        if st["di_params"]:
            st["di_resolution_rate"] = round(st["di_resolved"] / max(1, st["di_params"] - st["di_external"]), 3)
        st["di_unresolved_samples"] = getattr(self, "unresolved_samples", [])[:15]
        return st

    # ------------------------------------------------------------ helpers
    def method_of(self, cls: str | None, name: str):
        for _ in range(8):
            c = self.classes.get(cls)
            if not c:
                return None
            for m in c.get("methods") or []:
                if m["name"] == name and m.get("id"):
                    return m["id"]
            for p in c.get("props") or []:   # arrow-function class properties
                if p["name"] == name and p.get("node"):
                    return p["node"]
            cls = self.supers.get(cls)
        return None

    def class_ids(self, d) -> list[str]:
        """Classes a described value names: X, forwardRef(() => X), X.forRoot(...) (the module class)."""
        if not isinstance(d, dict):
            return []
        if d.get("node", "").startswith("class:"):
            return [d["node"]]
        if d.get("body"):
            return self.class_ids(d["body"])
        if "call" in d:
            if last_name(d["call"]) == "forwardRef":
                return [x for a in d.get("args") or [] for x in self.class_ids(a)]
            if d.get("recv"):
                return self.class_ids(d["recv"])
        if "new" in d and (d.get("node") or "").startswith("class:"):
            return [d["node"]]
        return []

    # ------------------------------------------------------------ modules
    def _modules(self, st):
        b = self.b
        self.module_of_controller = {}
        self.module_paths = {}       # module class -> RouterModule path prefix
        self.module_providers = []   # (module class, provider desc)
        self.client_tokens = set()   # ClientsModule.register([{ name }]) tokens -> ClientProxy
        self.modules = {}
        for c in self.classes.values():
            d = _decos(c).get("Module")
            if not d or not _is_nest(d):
                continue
            o = obj((d.get("args") or [None])[0])
            info = {}
            for role in ("imports", "controllers", "providers", "exports"):
                items = (o.get(role) or {}).get("arr") or []
                info[role] = items
            self.modules[c["id"]] = info
            st["modules"] += 1
            names = {}
            for role, items in info.items():
                ids = []
                for it in items:
                    for cid in self.class_ids(it):
                        ids.append(cid)
                        if role == "controllers":
                            self.module_of_controller.setdefault(cid, c["id"])
                        if b.has(c["id"]) and b.has(cid):
                            b.add_edge(c["id"], cid, "BINDS" if role == "providers" else "REFERENCES", file=c["file"],
                                       line=(it or {}).get("line") or c["line"], confidence="exact", role=role)
                    if role == "providers":
                        self.module_providers.append((c["id"], it))
                    if role == "imports" and "call" in (it or {}) and last_name(it["call"]) == "register" and "RouterModule" in it["call"]:
                        self._router_module(it.get("args") or [], "")
                    if role == "imports" and "call" in (it or {}) and "ClientsModule" in it["call"]:
                        for e in ((it.get("args") or [{}])[0] or {}).get("arr") or []:   # ClientsModule.register([{ name: TOKEN }])
                            tok = _token(obj(e).get("name"))
                            if tok:
                                self.client_tokens.add(tok)
                names[role] = [b.nodes[x].name for x in ids if b.has(x)]
            if b.has(c["id"]):
                b.nodes[c["id"]].attrs["nest_module"] = names

    def _router_module(self, args, prefix):
        for a in args:
            for e in (a or {}).get("arr") or []:
                o = obj(e)
                if not o:
                    for cid in self.class_ids(e):
                        self.module_paths.setdefault(cid, prefix)
                    continue
                p = join_path(prefix, sval(o.get("path")) or "")
                for cid in self.class_ids(o.get("module")):
                    self.module_paths.setdefault(cid, p)
                if o.get("children"):
                    self._router_module([o["children"]], p)

    # ------------------------------------------------------------ providers + DI
    def _providers(self, st):
        self.providers = {}     # token -> [(impl node, kind, desc)]
        self.app_enhancers = []  # (decorator name, class id, evidence)
        seen = set()
        extra = [(None, po["desc"]) for po in self.F.get("provide_objs") or [] if isinstance(po.get("desc"), dict)]
        for mod, it in self.module_providers + extra:
            o = obj(it)
            if o and (it.get("line"), _token(o.get("provide"))) in seen:
                continue
            if o:
                seen.add((it.get("line"), _token(o.get("provide"))))
            if not o:
                for cid in self.class_ids(it):
                    self.providers.setdefault(cid, []).append((cid, "class", it))
                    st["providers"] += 1
                continue
            tok = _token(o.get("provide"))
            if not tok:
                continue
            impls = []
            if o.get("useClass"):
                impls = [(x, "useClass") for x in self.class_ids(o["useClass"])]
            elif o.get("useExisting"):
                impls = [(x, "useExisting") for x in self.class_ids(o["useExisting"])]
            elif o.get("useFactory"):
                f = o["useFactory"]
                news = self._news(f)
                impls = [(x, "useFactory") for x in news] or [(f.get("fn") or f.get("node"), "useFactory")]
            elif "useValue" in o:
                v = o["useValue"]
                impls = [(x, "useValue") for x in self.class_ids(v)] or [(None, "useValue")]
            for x, k in impls:
                self.providers.setdefault(tok, []).append((x, k, it))
                st["providers"] += 1
            ref = (o.get("provide") or {}).get("ref")
            if ref in APP_TOKENS:
                for x, _ in impls:
                    if x:
                        self.app_enhancers.append((APP_TOKENS[ref], x))

    def _news(self, d, depth=0) -> list[str]:
        if not isinstance(d, dict) or depth > 5:
            return []
        out = []
        if "new" in d and (d.get("node") or "").startswith("class:"):
            out.append(d["node"])
        for k in ("body", "ret", "args", "arr", "cond"):
            v = d.get(k)
            for x in (v if isinstance(v, list) else [v]):
                out += self._news(x, depth + 1)
        return out

    def _di(self, st):
        b = self.b
        self.member_impl = {}   # (class id, member name) -> [impl class or fn ids]
        self.member_meta = {}   # (class id, member name) -> {"queue": q, "client": bool, "emitter": bool}
        self.unresolved_samples = []
        for c in self.classes.values():
            cdecos = _decos(c)
            injectable = any(n in cdecos for n in INJECTABLE) or c["id"] in self.module_of_controller
            if not injectable:
                continue
            members = [("ctor", p) for p in c.get("ctor") or []] + [("prop", p) for p in c.get("props") or [] if _decos(p).get("Inject")]
            deps = (cdecos.get("Dependencies") or {}).get("args") or []   # JS: @Dependencies(A, B) -> constructor params
            for idx, (where, p) in enumerate(members):
                pd = _decos(p)
                t = p.get("type") or {}
                st["di_params"] += 1
                tok, how = None, None
                meta = {}
                custom_inject = [n for n in pd if n.startswith("Inject") and n not in ("Inject", "InjectQueue", *ORM_INJECT)]
                inj = (pd.get("Inject") or {}).get("args") or []
                if inj and (inj[0] or {}).get("mod") and not str(inj[0]["mod"]).startswith(".") and not (inj[0] or {}).get("node"):
                    how = "custom-inject"   # @Inject(LibraryClass): token imported from a package
                elif inj:
                    tok, how = _token(inj[0]), "@Inject"
                elif where == "ctor" and idx < len(deps) and _token(deps[idx]):
                    tok, how = _token(deps[idx]), "@Dependencies"
                elif custom_inject:
                    how = "custom-inject"   # @InjectDrizzle(), @InjectStripe(), ...: provided by a library module
                elif pd.get("InjectQueue"):
                    meta["queue"] = sval((pd["InjectQueue"].get("args") or [{}])[0]) or "default"
                    how = "@InjectQueue"
                elif any(pd.get(n) for n in ORM_INJECT):
                    how = "orm"
                elif pd.get("Optional") and not t.get("id"):
                    how = "optional"
                elif t.get("id"):
                    tok, how = t["id"], "type"
                if t.get("name") == "EventEmitter2":
                    meta["emitter"] = True
                if t.get("name") in ("ClientProxy", "ClientKafka", "ClientGrpc", "ClientRMQ", "ClientRedis", "ClientNats"):
                    meta["client"] = True
                if meta:
                    self.member_meta[(c["id"], p["name"])] = meta
                if how in ("@InjectQueue", "orm"):
                    st["di_resolved"] += 1
                    continue
                if how == "custom-inject":
                    st["di_external"] += 1
                    continue
                if tok and tok in self.client_tokens:
                    self.member_meta.setdefault((c["id"], p["name"]), {})["client"] = True
                    st["di_resolved"] += 1
                    continue
                if not tok:
                    if t.get("mod") or (t.get("name") and not t.get("id")) or how == "optional":
                        st["di_external"] += 1   # library provider (ConfigService, JwtService, ...): not in this repo
                    else:
                        st["di_unresolved"] += 1
                        self.unresolved_samples.append(f"{c.get('name')}.{p['name']}: {t.get('text')}")
                    continue
                impls = [x for x, _, _ in self.providers.get(tok, []) if x]
                kind = "provider"
                if not impls and tok.startswith("class:") and tok in self.classes:
                    impls, kind = [tok], "class-token"
                if not impls and self.providers.get(tok):
                    st["di_resolved"] += 1   # useValue / factory without a class: resolved, nothing to link
                    continue
                if not impls:
                    if tok.startswith("type:"):
                        st["di_unresolved"] += 1
                        self.unresolved_samples.append(f"{c.get('name')}.{p['name']}: interface {t.get('text')} without @Inject token")
                    elif tok.startswith(("str:", "key:", "ref:")):
                        st["di_unresolved"] += 1
                        self.unresolved_samples.append(f"{c.get('name')}.{p['name']}: token {tok} has no provider")
                    else:
                        st["di_external"] += 1
                    continue
                st["di_resolved"] += 1
                self.member_impl[(c["id"], p["name"])] = impls
                for x in impls:
                    if b.has(c["id"]) and b.has(x) and x != c["id"]:
                        b.add_edge(c["id"], x, "INJECTS", file=c["file"], line=p.get("line") or c["line"],
                                   confidence="exact" if kind == "class-token" and how == "type" else "resolved",
                                   token=tok.split(":", 1)[1], member=p["name"], via=how)
                # abstract class token bound to a concrete provider: dispatch its methods
                if tok.startswith("class:") and tok in self.classes:
                    for x in impls:
                        if x == tok or x not in self.classes:
                            continue
                        for m in self.classes[tok].get("methods") or []:
                            impl_m = self.method_of(x, m["name"])
                            if m.get("id") and impl_m and impl_m != m["id"]:
                                b.add_edge(m["id"], impl_m, "BOUND_TO", file=c["file"], line=p.get("line") or c["line"], confidence="resolved", via="nest-provider")
        # calls through injected members: this.<member>.<method>()
        existing = {(e.src, e.dst, e.line) for e in b.edges.values() if e.kind == "CALLS"}
        for mc in self.F.get("member_calls") or []:
            if not mc.get("prop") or not mc.get("src") or not b.has(mc["src"]):
                continue
            cls = mc.get("cls")
            impls = None
            for _ in range(6):
                if not cls:
                    break
                impls = self.member_impl.get((cls, mc["prop"]))
                if impls:
                    break
                cls = self.supers.get(cls)
            for x in impls or []:
                tgt = self.method_of(x, mc["method"]) if x in self.classes else (x if mc["method"] in ("call", "apply") else None)
                if tgt and (mc["src"], tgt, mc["line"]) not in existing:
                    b.add_edge(mc["src"], tgt, "CALLS", file=mc["file"], line=mc["line"], confidence="resolved", via=["nest-di"], member=mc["prop"])
                    st["di_calls"] += 1

    # ------------------------------------------------------------ routes
    def _app_config(self):
        prefix, exclude, versioning = "", [], None
        enhancers = list(self.app_enhancers)
        for call in self.F.get("calls") or []:
            m = call["method"]
            args = call.get("args") or []
            if m == "setGlobalPrefix" and args and sval(args[0]) is not None:
                prefix = sval(args[0])
                for e in (obj(args[1]).get("exclude") or {}).get("arr") or [] if len(args) > 1 else []:
                    p = sval(e) or sval(obj(e).get("path"))
                    if p is not None:
                        exclude.append(express_path(p))
            elif m == "enableVersioning":
                o = obj(args[0]) if args else {}
                typ = (o.get("type") or {}).get("ref") or ""
                versioning = {"type": "uri" if "URI" in typ or not typ else typ.split(".")[-1].lower(),
                              "prefix": "v" if "prefix" not in o else (sval(o["prefix"]) or ""),
                              "default": svals(o.get("defaultVersion")) or ([None] if o.get("defaultVersion") else [])}
            elif m in GLOBAL_ENHANCERS:
                for a in args:
                    for cid in self.class_ids(a):
                        enhancers.append((GLOBAL_ENHANCERS[m], cid))
        return prefix, exclude, versioning, enhancers

    def _enhancer_refs(self, decos: list) -> list[tuple[str, str, str | None]]:
        """[(role, name, class id)] from @UseGuards/... decorators."""
        out = []
        for d in decos:
            n = last_name(d["name"])
            if n not in ENHANCERS:
                continue
            for a in d.get("args") or []:
                cids = self.class_ids(a)
                name = (self.b.nodes[cids[0]].name if cids and self.b.has(cids[0]) else (a or {}).get("ref") or (a or {}).get("call") or (a or {}).get("new") or "?")
                out.append((n, str(name).split("#")[-1], cids[0] if cids else None))
        return out

    def _dto_fields(self, cid: str | None) -> list[str] | None:
        c = self.classes.get(cid)
        if not c:
            return None
        out = []
        seen = set()
        while c and len(seen) < 6:
            seen.add(c["id"])
            for p in c.get("props") or []:
                if not p.get("static") and p["name"] not in out:
                    out.append(p["name"] + ("?" if p.get("optional") else ""))
            ext = c.get("extends") or {}
            nxt = ext.get("id")
            if not nxt and ext.get("call"):   # PartialType(CreateDto) / PickType(...)
                ids = [x for a in ext["call"].get("args") or [] for x in self.class_ids(a)]
                nxt = ids[0] if ids else None
            c = self.classes.get(nxt) if nxt not in seen else None
        return out

    def _routes(self, st):
        b = self.b
        prefix, exclude, versioning, global_enh = self._app_config()
        self.global_prefix = prefix
        st["global_prefix"] = prefix or None
        st["versioning"] = versioning
        for c in self.classes.values():
            cd = _decos(c)
            ctl = cd.get("Controller")
            if not ctl or not _is_nest(ctl):
                if cd.get("Resolver") and _is_nest(cd["Resolver"]):
                    self._graphql(c, st)
                continue
            st["controllers"] += 1
            a0 = (ctl.get("args") or [None])[0]
            cpaths = svals(a0) or svals(obj(a0).get("path")) or [""]
            cver = svals(obj(a0).get("version")) if a0 else []
            mod = self.module_of_controller.get(c["id"])
            mpath = self.module_paths.get(mod, "") if mod else ""
            cenh = self._enhancer_refs(c.get("decorators") or [])
            for m in c.get("methods") or []:
                md = _decos(m)
                hits = [(n, d) for n, d in md.items() if n in ROUTE_DECOS and _is_nest(d)]
                if not hits or not m.get("id"):
                    continue
                menh = self._enhancer_refs(m.get("decorators") or [])
                mver = svals((md.get("Version") or {}).get("args", [None])[0]) if md.get("Version") else []
                versions = mver or cver or ((versioning or {}).get("default") or [None])
                enh = [(r, n, x) for r, n, x in global_enh_named(b, global_enh)] + cenh + menh
                attrs = {"controller": c.get("name"), "action": m["name"]}
                for role_deco, (role, _) in ENHANCERS.items():
                    names = [n for r, n, _ in enh if r == role_deco]
                    if names:
                        attrs[role] = names
                custom = [d["name"] for d in m.get("decorators") or [] if last_name(d["name"]) not in ROUTE_DECOS and last_name(d["name"]) not in ENHANCERS and last_name(d["name"]) not in ("Version", "HttpCode", "Header", "Redirect", "Render")]
                if custom:
                    attrs["decorators"] = custom
                for p in m.get("params") or []:
                    pd = _decos(p)
                    if "Body" in pd:
                        key = sval((pd["Body"].get("args") or [None])[0])
                        if key:
                            attrs.setdefault("body_fields", []).append(key)
                        elif (p.get("type") or {}).get("id"):
                            attrs["body_dto"] = (p["type"].get("name") or p["type"]["text"])
                            attrs["body_fields"] = self._dto_fields(p["type"]["id"])
                    if "Query" in pd:
                        key = sval((pd["Query"].get("args") or [None])[0])
                        if key:
                            attrs.setdefault("query_fields", []).append(key)
                        elif (p.get("type") or {}).get("id"):
                            attrs["query_dto"] = p["type"].get("name")
                            attrs["query_fields"] = self._dto_fields(p["type"]["id"])
                mw = []
                for role_deco, name, cid in enh:
                    hook = ENHANCERS[role_deco][1]
                    tgt = self.method_of(cid, hook) if cid else None
                    mw.append((tgt, name, "resolved"))
                for n, d in hits:
                    method = ROUTE_DECOS[n]
                    for mp in (svals((d.get("args") or [None])[0]) or [""]) if d.get("args") else [""]:
                        for cp in cpaths:
                            for v in versions:
                                vseg = ""
                                if versioning and versioning["type"] == "uri" and v and not str(v).startswith("VERSION_NEUTRAL"):
                                    vseg = f"{versioning['prefix']}{v}"
                                local = join_path(mpath, cp, mp)
                                excluded = express_path(local) in exclude
                                uri = express_path(join_path("" if excluded else prefix, vseg, mpath, cp, mp))
                                ra = dict(attrs)
                                if v:
                                    ra["version"] = v
                                if mpath:
                                    ra["router_module_path"] = mpath
                                add_route(b, method, uri, [m["id"]], c["file"], d.get("line") or m["line"], "nest", "exact", ra, mw)
                                st["routes"] += 1

    def _graphql(self, c, st):
        for m in c.get("methods") or []:
            md = _decos(m)
            for n in ("Query", "Mutation", "Subscription", "ResolveField"):
                d = md.get(n)
                if not d or not (d.get("mod") or "").startswith("@nestjs/graphql") or not m.get("id"):
                    continue
                a = d.get("args") or []
                name = next((sval(obj(x).get("name")) for x in a if sval(obj(x).get("name"))), None) or (sval(a[0]) if a and sval(a[0]) else m["name"])
                key = f"GRAPHQL {n}.{name}"
                rid = self.b.add_node("route", key, name=key, file=c["file"], line=d.get("line") or m["line"], module=module_of(c["file"]), lang="ts",
                                      entry_kind="http_route", attrs={"graphql": n, "field": name, "framework": "nest", "resolver": c.get("name")})
                self.b.add_edge(rid, m["id"], "ROUTES_TO", file=c["file"], line=d.get("line") or m["line"], confidence="exact")
                st["graphql_ops"] += 1

    # ------------------------------------------------------------ other entry points
    def _entries(self, st):
        b = self.b
        events, messages, jobs = {}, {}, {}
        for c in self.classes.values():
            cd = _decos(c)
            proc = cd.get("Processor") if cd.get("Processor") and _is_nest(cd["Processor"]) and "microservices" not in (cd["Processor"].get("mod") or "") else None
            queue = None
            if proc:
                a0 = (proc.get("args") or [None])[0]
                queue = sval(a0) or sval(obj(a0).get("name")) or "default"
            gw = cd.get("WebSocketGateway")
            ns = None
            if gw:
                for a in gw.get("args") or []:
                    ns = ns or sval(obj(a).get("namespace"))
            cmd = cd.get("Command") or cd.get("SubCommand")
            if cmd and (cmd.get("mod") or "").startswith("nest-commander") or (cmd and cmd.get("mod") is None and any((x.get("text") or "").startswith("CommandRunner") for x in [c.get("extends") or {}])):
                o = obj((cmd.get("args") or [None])[0])
                name = sval(o.get("name")) or c.get("name")
                run = self.method_of(c["id"], "run")
                cid = b.add_node("command", f"nest:{name}", name=name, file=c["file"], line=cmd.get("line") or c["line"], module=module_of(c["file"]),
                                 lang="ts", entry_kind="cli_command", attrs={"class": c.get("name"), "framework": "nest-commander", "description": sval(o.get("description"))})
                if run:
                    b.add_edge(cid, run, "HANDLED_BY", file=c["file"], line=cmd.get("line") or c["line"], confidence="exact")
                st["commands"] += 1
            if proc and "WorkerHost" in ((c.get("extends") or {}).get("text") or ""):
                h = self.method_of(c["id"], "process")
                jid = b.add_node("job", f"{queue}", name=f"queue {queue}", file=c["file"], line=proc.get("line") or c["line"], module=module_of(c["file"]),
                                 lang="ts", entry_kind="queue_job", attrs={"queue": queue, "framework": "bullmq", "class": c.get("name")})
                if h:
                    b.add_edge(jid, h, "HANDLED_BY", file=c["file"], line=proc.get("line") or c["line"], confidence="exact")
                jobs.setdefault(queue, {})[None] = jid
                st["jobs"] += 1
            for m in c.get("methods") or []:
                if not m.get("id"):
                    continue
                md = _decos(m)
                line = m["line"]
                for n in ("Cron", "Interval", "Timeout"):
                    d = md.get(n)
                    if d and _is_nest(d):
                        a = d.get("args") or []
                        expr = sval(a[0]) if a and sval(a[0]) is not None else ((a[0] or {}).get("ref") if a else None) or (str(a[0].get("n")) if a and a[0] and a[0].get("n") is not None else None)
                        if n != "Cron" and len(a) > 1:
                            expr = str((a[1] or {}).get("n") or (a[1] or {}).get("ref"))
                        sid = b.add_node("schedule", f"{m['id'].split(':', 1)[1]}@{n}", name=f"{n.lower()} {expr} {c.get('name')}.{m['name']}",
                                         file=c["file"], line=d.get("line") or line, module=module_of(c["file"]), lang="ts", entry_kind="scheduled",
                                         attrs={"trigger": n, "expr": expr, "framework": "nest"})
                        b.add_edge(sid, m["id"], "SCHEDULES", file=c["file"], line=d.get("line") or line, confidence="exact")
                        st["schedules"] += 1
                if proc and md.get("Process"):
                    d = md["Process"]
                    a0 = (d.get("args") or [None])[0]
                    jname = sval(a0) or sval(obj(a0).get("name"))
                    jid = b.add_node("job", f"{queue}:{jname}" if jname else queue, name=f"queue {queue}" + (f" job {jname}" if jname else ""),
                                     file=c["file"], line=d.get("line") or line, module=module_of(c["file"]), lang="ts", entry_kind="queue_job",
                                     attrs={"queue": queue, "job": jname, "framework": "bull"})
                    b.add_edge(jid, m["id"], "HANDLED_BY", file=c["file"], line=d.get("line") or line, confidence="exact")
                    jobs.setdefault(queue, {})[jname] = jid
                    st["jobs"] += 1
                if md.get("OnEvent"):
                    d = md["OnEvent"]
                    lid = b.add_node("listener", m["id"].split(":", 1)[1], name=f"{c.get('name')}.{m['name']}", file=c["file"], line=d.get("line") or line,
                                     module=module_of(c["file"]), lang="ts", entry_kind="listener", attrs={"framework": "nest-event-emitter"})
                    b.add_edge(lid, m["id"], "HANDLED_BY", file=c["file"], line=d.get("line") or line, confidence="exact")
                    for ev in svals((d.get("args") or [None])[0]) or [((d.get("args") or [{}])[0] or {}).get("ref") or "?"]:
                        eid = b.add_node("event", ev, name=ev, lang="ts", attrs={"framework": "nest-event-emitter"})
                        b.add_edge(eid, m["id"], "LISTENED_BY", file=c["file"], line=d.get("line") or line, confidence="exact")
                        events.setdefault(ev, eid)
                    st["listeners"] += 1
                for n, transport in (("MessagePattern", "rpc"), ("EventPattern", "event"), ("SubscribeMessage", "ws"), ("GrpcMethod", "grpc")):
                    d = md.get(n)
                    if not d or not _is_nest(d):
                        continue
                    # @UseGuards on the class / handler and APP_GUARD providers (#69); app.useGlobalGuards() binds the
                    # HTTP app only, not microservice / gateway handlers
                    guards = [nm for r, nm, _ in list(global_enh_named(b, self.app_enhancers))
                              + self._enhancer_refs(c.get("decorators") or []) + self._enhancer_refs(m.get("decorators") or [])
                              if r == "UseGuards"]
                    a = d.get("args") or []
                    pat = _pattern(a[0]) if a else m["name"]
                    if n == "GrpcMethod":
                        pat = ".".join(x for x in [sval(a[0]) if a else c.get("name"), sval(a[1]) if len(a) > 1 else m["name"]] if x)
                    key = f"{transport}:{ns + ':' if ns and transport == 'ws' else ''}{pat}"
                    mid = b.add_node("message", key, name=f"{transport} {pat}", file=c["file"], line=d.get("line") or line, module=module_of(c["file"]),
                                     lang="ts", entry_kind="message_handler", attrs={"transport": transport, "pattern": pat, "namespace": ns, "framework": "nest",
                                                                                     "guards": list(dict.fromkeys(guards))})
                    b.add_edge(mid, m["id"], "HANDLED_BY", file=c["file"], line=d.get("line") or line, confidence="exact")
                    messages.setdefault(pat, []).append(mid)
                    st["messages"] += 1
        # producers
        for mc in self.F.get("member_calls") or []:
            if not mc.get("prop") or not mc.get("src") or not b.has(mc["src"]):
                continue
            meta = None
            cls = mc.get("cls")
            for _ in range(6):
                if not cls:
                    break
                meta = self.member_meta.get((cls, mc["prop"]))
                if meta:
                    break
                cls = self.supers.get(cls)
            meta = meta or {}
            a0 = mc.get("a0")
            if meta.get("queue") and mc["method"] in ("add", "addBulk"):
                q = jobs.get(meta["queue"], {})
                tgt = q.get(sval(a0)) or q.get(None)
                if tgt:
                    b.add_edge(mc["src"], tgt, "DISPATCHES", file=mc["file"], line=mc["line"], confidence="resolved", queue=meta["queue"], job=sval(a0))
            elif mc["method"] in ("emit", "emitAsync") and sval(a0) and (meta.get("emitter") or re.search(r"event", mc["prop"], re.I)) and not meta.get("client"):
                ev = sval(a0)
                eid = events.get(ev) or b.add_node("event", ev, name=ev, lang="ts", attrs={"framework": "nest-event-emitter"})
                b.add_edge(mc["src"], eid, "DISPATCHES", file=mc["file"], line=mc["line"], confidence="resolved")
            elif mc["method"] in ("send", "emit") and (meta.get("client") or re.search(r"client|proxy", mc["prop"], re.I)) and a0:
                pat = _pattern(a0)
                targets = messages.get(pat, [])
                if not targets and pat:   # handled by another service: an outgoing message node (same id as a handler's)
                    transport = "event" if mc["method"] == "emit" else "rpc"
                    targets = [b.add_node("message", f"{transport}:{pat}", name=pat, lang="ts",
                                          attrs={"transport": transport, "pattern": pat, "framework": "nest", "outgoing": True})]
                for mid in targets:
                    b.add_edge(mc["src"], mid, "DISPATCHES", file=mc["file"], line=mc["line"], confidence="resolved", pattern=pat)


def global_enh_named(b, enh):
    for role, cid in enh:
        yield role, (b.nodes[cid].name.split("#")[-1] if b.has(cid) else cid), cid

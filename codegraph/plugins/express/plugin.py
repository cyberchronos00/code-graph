"""Express / Koa (+ @koa/router) / Fastify / Hono router layer (on the TypeScript plugin; plain-JS projects too).

Router instances are declarations (variables, class properties, function parameters) whose initialiser is a known
factory (express(), express.Router(), Router(), new Router({prefix}), fastify(), new Koa(), new Hono(), app.basePath(p),
...), identified across files through imports, require(), module.exports and factory functions that return them.

  routes     x.get|post|put|patch|delete|all|head|options(path, ...middleware, handler), x.route(path).get(h).post(h),
             fastify.route({method, url, handler, preHandler}), fastify.get(path, {preHandler, schema}, handler),
             hono.on(methods, path, h); paths `:id` -> `{id}`, `*` -> `{wildcard*}`
  mounting   x.use(prefix?, ...mw, sub) (Express, koa-router sub.routes()), fastify.register(plugin, {prefix}) (the
             plugin's first parameter is the child instance), hono.route(prefix, sub), basePath; chains across files are
             joined into full paths; a function called with a router argument (`routes(app)`) binds its parameter
  middleware route-level middleware, x.use(mw) router-level middleware and middleware passed with a mount are recorded on
             the route (attrs.middleware) with USES_MIDDLEWARE edges where the function resolves
  confidence exact: literal path, known factory, direct handler; resolved: through wrappers / handler objects / aliases
             / factory functions / params; heuristic: router never mounted from an app (prefix unknown) or receiver only
             recognised by name
"""
from __future__ import annotations

import re
from collections import defaultdict

from ...core.plugin import FrameworkPlugin, GraphBuilder, Project
from ..tsweb.common import (TEST_SKIP_RE, add_route, express_path, finish, fw_facts, has_server_framework, join_path,
                            last_name, merge_extractor_cfg, obj, ref_nodes, register, sval, svals)

VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "del": "DELETE", "head": "HEAD",
         "options": "OPTIONS", "all": "ANY", "any": "ANY"}
MODS = {"express": "express", "koa": "koa", "@koa/router": "koa-router", "koa-router": "koa-router", "fastify": "fastify",
        "hono": "hono", "elysia": "elysia", "polka": "polka", "restify": "restify", "h3": "h3", "@hono/zod-openapi": "hono"}
APP_FACTORIES = {"express": "express", "Fastify": "fastify", "fastify": "fastify", "Koa": "koa", "Hono": "hono", "OpenAPIHono": "hono",
                 "Elysia": "elysia", "polka": "polka", "createServer": "restify", "createApp": "h3"}
ROUTER_FACTORIES = {"Router": "express", "KoaRouter": "koa-router", "createRouter": "h3"}
HOOKS = ("onRequest", "preParsing", "preValidation", "preHandler")
MW_OPTS = ("preHandler", "onRequest", "preValidation", "preParsing", "beforeHandler", "middleware")
NAME_HINT = re.compile(r"(^|\.)(app|server|router|routes?|api|fastify|instance|r)$", re.I)


class ExpressPlugin(FrameworkPlugin):
    name, language = "express", "typescript"

    def detect(self, project: Project) -> bool:
        return has_server_framework(project, "express")

    def register_hooks(self, ctx) -> None:
        root = ctx.project.root
        dirs = [d for d in ("src", "server", "api", "app", "lib", "routes", "core") if (root / d).is_dir()]
        merge_extractor_cfg(ctx.extractor_cfg, src_dirs=dirs or ["."], skip_re=TEST_SKIP_RE, walk_src=True, allow_js=True)
        if dirs:   # entry files at the root (index.js, app.js, server.ts)
            merge_extractor_cfg(ctx.extractor_cfg, extra_files=[f for f in ("index.js", "index.ts", "app.js", "app.ts", "server.js", "server.ts", "main.ts", "main.js") if (root / f).exists()],
                                src_dirs=["."] if any((root / f).exists() for f in ("index.js", "app.js", "server.js", "index.ts", "app.ts", "server.ts")) else None)
        register(ctx, self.name)

    # ------------------------------------------------------------
    def contribute(self, project: Project, b: GraphBuilder, ctx) -> dict:
        st = {"routers": 0, "apps": 0, "mounts": 0, "routes": 0, "routes_unmounted": 0, "routes_unresolved_handler": 0}
        F = fw_facts(ctx)
        if not F:
            return {**st, "status": "no facts"}
        self.b, self.F = b, F
        self._route_keys = None
        self.I = F.get("instances") or {}
        calls = F.get("calls") or []
        self.kinds = {}        # key -> (framework, "app"|"router"|"derived"|"param"|"name")
        self.own_prefix = defaultdict(str)
        self.mounts = defaultdict(list)     # child -> [(parent, prefix, conf, mw)]
        self.router_mw = defaultdict(list)  # key -> [(prefix, (node, name, conf), file, line)]
        for k, inst in self.I.items():
            c = self.classify(k)
            if c:
                self.kinds[k] = c
        route_calls, mount_calls = [], []
        for c in calls:
            m = c["method"]
            keys = self.recv_keys(c)
            if not keys:
                continue
            if m in VERBS or (m == "on" and len(c.get("args") or []) >= 3) or (m == "route" and len(c["args"]) == 1 and obj(c["args"][0]).get("method")):
                route_calls.append((c, keys))
            elif m in ("use", "lazyUse", "mount", "register", "route", "group"):
                mount_calls.append((c, keys))
            elif m == "prefix" and c.get("args") and sval(c["args"][0]):
                for k, _ in keys:
                    self.own_prefix[k] = sval(c["args"][0])
            elif m == "addHook" and len(c.get("args") or []) >= 2 and sval(c["args"][0]) in HOOKS:
                for k, kp in keys:   # fastify hooks apply to the instance's routes and its child plugins
                    self.router_mw[k].append((kp, self.mw_of(c["args"][1]), c["file"], c["line"]))
        for k, inst in self.I.items():   # new Router({ prefix: '/x' }) (koa-router)
            init = (inst or {}).get("init") or {}
            if last_name(init.get("new") or init.get("call") or "") in ("Router", "KoaRouter") and init.get("args"):
                pfx = sval(obj(init["args"][0]).get("prefix"))
                if pfx:
                    self.own_prefix[k] = pfx
        # routerish = known factories + keys that receive route calls (and the apps mounting them)
        for c, keys in route_calls:
            for k, _ in keys:
                if k not in self.kinds:
                    inst = self.I.get(k) or {}
                    if inst.get("kind") == "param":
                        self.kinds[k] = ("?", "param")
                    elif NAME_HINT.search(inst.get("name") or c.get("recv_text") or ""):
                        self.kinds[k] = ("?", "name")
        for c, keys in mount_calls:
            self.mount(c, keys, st)
        for bc in F.get("bind_calls") or []:
            for k, inst in self.I.items():
                if inst and inst.get("kind") == "param" and inst.get("fn") == bc["fn"] and inst.get("index") is not None and inst["index"] < len(bc["args"]):
                    for pk, pfx in self.inst_keys(bc["args"][inst["index"]]):
                        if pk in self.kinds and pk != k:
                            self.mounts[k].append((pk, pfx, "resolved", [], f"{bc['file']}:{bc['line']}"))
                            st["mounts"] += 1
        st["apps"] = sum(1 for v in self.kinds.values() if v[1] == "app")
        st["routers"] = len({k for k, v in self.kinds.items() if v[1] in ("router", "derived", "param", "name")})
        for c, keys in route_calls:
            self.route(c, keys, st)
        st["frameworks"] = sorted({v[0] for v in self.kinds.values() if v[0] != "?"})
        st.update(finish(project, b, ctx, self.name))
        return st

    # ------------------------------------------------------------ instances
    def classify(self, k):
        inst = self.I.get(k) or {}
        init = inst.get("init") or {}
        if inst.get("kind") == "param":
            t = inst.get("type") or ""
            for n, fw in (("FastifyInstance", "fastify"), ("Hono", "hono"), ("Router", "express"), ("Express", "express"), ("Application", "express")):
                if re.search(rf"\b{n}\b", t):
                    return (fw, "param")
            return None
        return self._factory(init)

    def _factory(self, d, depth=0):
        if not isinstance(d, dict) or depth > 4:
            return None
        callee = d.get("call") or d.get("new")
        if callee is None:
            if d.get("cond"):
                for x in d["cond"]:
                    r = self._factory(x, depth + 1)
                    if r:
                        return r
            return None
        ln = last_name(callee)
        mod = d.get("mod") or ""
        fw = MODS.get(mod) or (MODS.get(mod.split("/")[0]) if mod.startswith("hono/") else None)
        if ln in ("basePath", "route", "use", "get", "post", "put", "patch", "delete", "on", "prefix") and d.get("recv"):
            parent = self.inst_keys(d["recv"])
            if parent:
                return (self.kinds.get(parent[0][0], ("?", ""))[0], "derived")
            inner = self._factory(d["recv"], depth + 1)   # new Hono().basePath('/api')
            if inner:
                return inner
        if ln in APP_FACTORIES and (fw or "new" in d or ln in ("express", "fastify", "Fastify", "polka")):
            return (fw or APP_FACTORIES[ln], "app" if ln not in ("Router",) else "router")
        if ln == "Router" or ln in ROUTER_FACTORIES:
            if "new" in d:
                return ("koa-router" if fw in (None, "koa-router", "koa") else fw, "router")
            return (fw or ("express" if callee.startswith("express") or not mod else MODS.get(mod, "express")), "router")
        if fw and ln not in ("json", "urlencoded", "static", "text", "raw"):
            return (fw, "app")
        return None

    def recv_keys(self, c):
        d = c.get("recv") or {}
        keys = self.inst_keys(d)
        if not keys and c.get("owner") and ("new" in d or "call" in d) and self._factory(d):
            k = c["owner"]   # chain owned by a declaration: const api = Router().use(...)
            self.kinds.setdefault(k, self._factory(d))
            keys = [(k, "")]
        if not keys and ("new" in d or "call" in d) and self._factory(d):
            k = f"inline:{c['file']}:{c['line']}"
            self.kinds.setdefault(k, self._factory(d))
            keys = [(k, "")]
        out = []
        for k, pfx in keys:
            for ch in c.get("chain") or []:
                if ch["method"] in ("basePath", "prefix") and ch.get("args") and sval(ch["args"][0]):
                    pfx = join_path(pfx, sval(ch["args"][0]))
            out.append((k, pfx))
        return out

    def inst_keys(self, d, depth=0) -> list[tuple[str, str]]:
        """Router instances a described value evaluates to: [(key, extra prefix)]."""
        if not isinstance(d, dict) or depth > 6:
            return []
        if d.get("key"):
            k = d["key"]
            init = (self.I.get(k) or {}).get("init") or {}
            if "call" in init and last_name(init["call"]) == "basePath" and init.get("recv"):
                bp = sval((init.get("args") or [None])[0]) or ""
                return [(pk, join_path(pp, bp)) for pk, pp in self.inst_keys(init["recv"], depth + 1)] or [(k, bp)]
            return [(k, "")]
        if "call" in d:
            ln = last_name(d["call"])
            if ln in ("routes", "middleware", "allowedMethods", "callback", "getRouter", "router", "fetch") and d.get("recv"):
                return self.inst_keys(d["recv"], depth + 1)
            if ln in ("basePath",) and d.get("recv"):
                return [(k, join_path(p, sval((d.get("args") or [None])[0]) or "")) for k, p in self.inst_keys(d["recv"], depth + 1)]
            out = []
            for r in d.get("ret") or []:
                out += self.inst_keys(r, depth + 1)
            if not out and last_name(d["call"]) in ("fp", "fastifyPlugin") and d.get("args"):
                out = self.inst_keys(d["args"][0], depth + 1)
            return out
        if "require" in d:
            return self.inst_keys(d.get("exp"), depth + 1)
        if d.get("fn"):
            out = []
            for r in (d.get("ret") or []) + ([d["body"]] if d.get("body") else []):
                out += self.inst_keys(r, depth + 1)
            return out
        if d.get("of") and d.get("ref", "").endswith(".router"):
            return []
        return []

    def plugin_params(self, d, depth=0) -> list[str]:
        """fastify.register(plugin): the plugin function's first parameter instance."""
        if not isinstance(d, dict) or depth > 4:
            return []
        fns = []
        if d.get("fn"):
            fns.append(d["fn"])
        if d.get("node"):
            fns.append(d["node"])
        if "require" in d:
            return self.plugin_params(d.get("exp"), depth + 1)
        if "call" in d:
            for a in d.get("args") or []:
                fns += self.plugin_params(a, depth + 1)
            return fns
        out = [k for k, inst in self.I.items() if inst and inst.get("kind") == "param" and inst.get("index") == 0 and inst.get("fn") in fns]
        return out or [x for x in fns if x.startswith("param:")]

    # ------------------------------------------------------------ mounts
    def mw_of(self, d):
        """(node, name, confidence) for a described middleware argument."""
        if not isinstance(d, dict):
            return (None, "?", "heuristic")
        if d.get("fn"):
            return (d["fn"], "inline", "exact")
        if d.get("ref") is not None:
            return (d.get("node") or (d.get("obj_fns") or [None])[0], d["ref"], "resolved")
        if "call" in d:
            return (d.get("node"), d["call"] + "()", "resolved" if d.get("node") else "heuristic")
        return (None, d.get("expr") or "?", "heuristic")

    def mount(self, c, keys, st):
        args = c.get("args") or []
        m = c["method"]
        if m == "register":
            if not args:
                return
            pfx = sval(obj(args[1]).get("prefix")) if len(args) > 1 else None
            for child in self.plugin_params(args[0]):
                self.kinds.setdefault(child, (self.kinds.get(keys[0][0], ("fastify", ""))[0], "param"))
                for k, kp in keys:
                    self.mounts[child].append((k, join_path(kp, pfx or ""), "resolved", [], f"{c['file']}:{c['line']}"))
                    st["mounts"] += 1
            return
        prefixes = [""]
        rest = args
        if args and (svals(args[0]) and "re" not in args[0]):
            prefixes, rest = svals(args[0]), args[1:]
        flat = []
        for a in rest:
            flat += a.get("arr") if isinstance(a, dict) and a.get("arr") is not None else [a]
        children, mws = [], []
        for a in flat:
            ks = [x for x in self.inst_keys(a) if x[0] in self.kinds or self._has_routes(x[0])]
            if ks:
                children += ks
            else:
                mws.append(self.mw_of(a))
        if children:
            for ck, cp in children:
                for k, kp in keys:
                    if ck == k:
                        continue
                    for p in prefixes:
                        self.mounts[ck].append((k, join_path(kp, p, cp), "exact" if self.kinds.get(ck, ("", ""))[1] in ("router", "app", "derived") else "resolved",
                                                mws, f"{c['file']}:{c['line']}"))
                        st["mounts"] += 1
        elif m in ("use", "lazyUse"):
            for k, kp in keys:
                for p in prefixes:
                    for mw in mws:
                        self.router_mw[k].append((join_path(kp, p), mw, c["file"], c["line"]))

    def _has_routes(self, k) -> bool:
        if self._route_keys is None:
            self._route_keys = {kk for c in self.F.get("calls") or [] if c["method"] in VERBS for kk, _ in self.inst_keys(c.get("recv") or {})}
        return k in self._route_keys

    def _mw_before(self, k, at, pfx=None):
        """Router-level middleware of k registered before `at` (file, line) (order known only within one file)."""
        out = []
        for p, x, f, ln in self.router_mw.get(k, []):
            if pfx is None and p not in ("", "/"):
                continue
            if pfx is not None and (p in ("", "/") or not (pfx or "/").startswith(p)):
                continue   # prefix-scoped middleware only (global ones come through the parent's own walk)
            if at and f == at[0] and ln > at[1]:
                continue   # registered after the route / mount: does not run for it
            if x[1] in ("?", "inline") and not x[0]:
                continue
            out.append(x)
        return out

    def prefixes(self, k, depth=0, seen=frozenset(), at=None):
        """[(prefix, confidence, middleware, mounted_from_app)] for router k (`at`: the route call's file, line)."""
        own = self.own_prefix.get(k, "")
        if k in seen or depth > 8:
            return [(own, "heuristic", [], False)]
        if k not in self.mounts:
            kind = self.kinds.get(k, ("?", "name"))[1]
            mw = self._mw_before(k, at)
            return [(own, "exact" if kind == "app" else "heuristic", mw, kind == "app")]
        out = []
        for parent, pfx, conf, mws, ev in self.mounts[k][:8]:
            f, _, ln = ev.rpartition(":")
            at_ev = (f, int(ln)) if ln.isdigit() else None
            for pp, pc, pmw, ok in self.prefixes(parent, depth + 1, seen | {k}, at=at_ev):
                inherited = self._mw_before(parent, at_ev, pfx)
                out.append((join_path(pp, pfx, own), _weaker(conf, pc), pmw + inherited + mws + self._mw_before(k, at), ok))
                if len(out) >= 8:
                    return out
        return out

    # ------------------------------------------------------------ routes
    def route(self, c, keys, st):
        b = self.b
        args = list(c.get("args") or [])
        m = c["method"]
        methods, paths, opts = [], [], {}
        if m == "route":   # fastify.route({ method, url, handler })
            o = obj(args[0])
            methods = [x.upper() for x in svals(o.get("method"))] or ["GET"]
            paths = svals(o.get("url")) or svals(o.get("path"))
            opts = o
            handlers = [o.get("handler")]
            mids = []
        else:
            if m == "on":
                methods = [x.upper() for x in svals(args[0])] or ["ANY"]
                args = args[1:]
            else:
                methods = [VERBS[m]]
            chain_path = None
            for ch in c.get("chain") or []:
                if ch["method"] == "route" and ch.get("args") and svals(ch["args"][0]):
                    chain_path = svals(ch["args"][0])
            if args and (svals(args[0]) or (isinstance(args[0], dict) and "re" in args[0])) and not (chain_path is not None and not svals(args[0])):
                a0 = args.pop(0)
                paths = svals(a0) or ["{regex}"]
            elif chain_path is not None:
                paths = chain_path
            elif args and isinstance(args[0], dict) and args[0].get("ref") and not args[0].get("node"):
                args.pop(0)
                paths = ["{" + re.sub(r"\W", "_", args and "path" or "path") + "}"]
            else:
                paths = [""] if chain_path is None else chain_path
            flat = []
            for a in args:
                flat += a.get("arr") if isinstance(a, dict) and a.get("arr") is not None else [a]
            opt_objs = [a for a in flat if isinstance(a, dict) and a.get("obj") is not None]
            for o in opt_objs:
                opts.update(o["obj"])
            fns = [a for a in flat if not (isinstance(a, dict) and a.get("obj") is not None)]
            handlers = fns[-1:] or [opts.get("handler")]
            mids = fns[:-1]
        mw = [self.mw_of(x) for x in mids]
        for k in MW_OPTS:
            v = opts.get(k)
            for x in (v.get("arr") if isinstance(v, dict) and v.get("arr") is not None else [v] if v else []):
                mw.append(self.mw_of(x))
        handler_nodes, hconf = [], "exact"
        for h in handlers:
            if not isinstance(h, dict):
                continue
            ns = [n for n in ref_nodes(h) if b.has(n)]
            if not ns and h.get("node") and b.has(h["node"]):
                ns = [h["node"]]
            handler_nodes += ns
            if not h.get("fn") and not (h.get("ref") is not None and h.get("node")):
                hconf = "resolved"
        attrs = {}
        schema = obj(opts.get("schema"))
        if schema:
            props = obj(obj(schema.get("body")).get("properties"))
            if props:
                attrs["body_fields"] = sorted(props)
            q = obj(obj(schema.get("querystring")).get("properties"))
            if q:
                attrs["query_fields"] = sorted(q)
        if not handler_nodes:
            attrs["handler_unresolved"] = True
        counted = False
        literal = any(p == "" or p.startswith(("/", "*")) for p in paths if "{" not in p[:1])
        for k, kp in keys:
            fw, kind = self.kinds.get(k, ("?", "name"))
            if kind == "name" and not handler_nodes:
                continue
            if kind in ("name", "param") and fw == "?" and not literal:
                continue   # untyped receiver + non-literal first argument: a map/cache/http-client .get(key), not a route
            for pfx, pconf, pmw, mounted in self.prefixes(k, at=(c["file"], c["line"])):
                for p in paths:
                    uri = express_path(join_path(pfx, kp, p))
                    conf = _weaker(hconf, pconf if mounted else "heuristic")
                    if kind == "name":
                        conf = "heuristic"
                    if any("{" in s and not re.fullmatch(r"\{\w+\*?\??\}", s) for s in uri.split("/") if s) or "{regex}" in uri:
                        conf = "heuristic"
                    ra = {**attrs, "router": (self.I.get(k) or {}).get("name") or c.get("recv_text"), "router_framework": fw}
                    if not mounted:
                        ra["unmounted"] = True
                        st["routes_unmounted"] += 1
                    for method in methods:
                        add_route(b, method, uri, handler_nodes, c["file"], c["line"], fw if fw != "?" else "express", conf, ra, pmw + mw)
                        st["routes"] += 1
                        if not handler_nodes and not counted:
                            st["routes_unresolved_handler"] += 1   # once per route call that produced routes
                            counted = True


def _weaker(a, b):
    order = {"exact": 3, "resolved": 2, "heuristic": 1}
    return a if order[a] <= order[b] else b

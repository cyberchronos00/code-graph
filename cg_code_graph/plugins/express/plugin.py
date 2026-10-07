"""Express / Koa (+ @koa/router) / Fastify / Hono / Elysia router layer (on the TypeScript plugin; plain-JS projects too).

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
         "options": "OPTIONS", "all": "ANY", "any": "ANY", "ws": "WS"}   # ws: express-ws `app.ws(path, h)`, Elysia
MODS = {"express": "express", "koa": "koa", "@koa/router": "koa-router", "koa-router": "koa-router", "fastify": "fastify",
        "hono": "hono", "elysia": "elysia", "polka": "polka", "restify": "restify", "h3": "h3", "@hono/zod-openapi": "hono"}
APP_FACTORIES = {"express": "express", "Fastify": "fastify", "fastify": "fastify", "Koa": "koa", "Hono": "hono", "OpenAPIHono": "hono",
                 "Elysia": "elysia", "polka": "polka", "createServer": "restify", "createApp": "h3"}
ROUTER_FACTORIES = {"Router": "express", "KoaRouter": "koa-router", "createRouter": "h3"}
HOOKS = ("onRequest", "preParsing", "preValidation", "preHandler")
MW_OPTS = ("preHandler", "onRequest", "preValidation", "preParsing", "beforeHandler", "middleware")
NAME_HINT = re.compile(r"(^|\.)(app|server|router|routes?|api|fastify|instance|r)$", re.I)
# Elysia methods that stay on the instance chain and are not routes (see CHAIN_SKIP in extractor/fw.mjs)
ELYSIA_PASS = {"model", "decorate", "state", "derive", "resolve", "macro", "onError", "listen", "onRequest",
               "onBeforeHandle", "onAfterHandle", "onParse", "onTransform", "onAfterResponse", "mapResponse",
               "guard", "as", "group", "trace", "error", "headers", "onStart", "onStop", "use", "get", "post",
               "put", "patch", "delete", "all", "head", "options", "ws", "route"}
ELYSIA_HOOKS = {"onRequest", "onBeforeHandle", "guard", "as"}
_AUTH_RE = re.compile(
    r"set\.status\s*=\s*(40[13])\b"
    r"|\berror\(\s*(40[13])\b"
    r"|\bstatus\(\s*(40[13])\b"
    r"|\bthrow\b[^\n;]{0,160}\b(401|403|Unauthorized|Forbidden|AuthError)\b")
_SCOPE_RANK = {"local": 0, "scoped": 1, "global": 2}


class ExpressPlugin(FrameworkPlugin):
    name, language = "express", "typescript"

    def detect(self, project: Project) -> bool:
        return has_server_framework(project, "express") or has_server_framework(project, "elysia")

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
        self.b, self.F, self.root = b, F, project.root
        self._route_keys = None
        self.I = F.get("instances") or {}
        calls = F.get("calls") or []
        self.kinds = {}        # key -> (framework, "app"|"router"|"derived"|"param"|"name")
        self.own_prefix = defaultdict(str)
        self.elysia_names = {}
        self.elysia_hooks = defaultdict(list)   # key -> [hook dict]
        self.children = defaultdict(list)       # parent -> [child]
        self.mounts = defaultdict(list)     # child -> [(parent, prefix, conf, mw, ev)]
        self.router_mw = defaultdict(list)  # key -> [(prefix, (node, name, conf), file, line)]
        for k, inst in self.I.items():
            c = self.classify(k)
            if c:
                self.kinds[k] = c
        route_calls, mount_calls, hook_calls = [], [], []
        for c in calls:
            m = c["method"]
            keys = self.recv_keys(c)
            if not keys:
                continue
            if m in ELYSIA_HOOKS and any(self._fw(k) == "elysia" for k, _ in keys):
                hook_calls.append((c, keys))
            elif m in VERBS or (m == "on" and len(c.get("args") or []) >= 3) or (m == "route" and len(c["args"]) == 1 and obj(c["args"][0]).get("method")):
                route_calls.append((c, keys))
            elif m in ("use", "lazyUse", "mount", "register", "route", "group"):
                mount_calls.append((c, keys))
            elif m == "prefix" and c.get("args") and sval(c["args"][0]):
                for k, _ in keys:
                    self.own_prefix[k] = sval(c["args"][0])
            elif m == "addHook" and len(c.get("args") or []) >= 2 and sval(c["args"][0]) in HOOKS:
                for k, kp in keys:   # fastify hooks apply to the instance's routes and its child plugins
                    self.router_mw[k].append((kp, self.mw_of(c["args"][1]), c["file"], c["line"]))
        for k, inst in self.I.items():   # new Router({ prefix }) (koa-router), new Elysia({ prefix, name })
            ctor = self._root_ctor((inst or {}).get("init") or {})
            if not ctor:
                continue
            ln = last_name(ctor.get("new") or ctor.get("call") or "")
            if ln in ("Router", "KoaRouter", "Elysia") and ctor.get("args"):
                opts = obj(ctor["args"][0])
                pfx = sval(opts.get("prefix"))
                if pfx:
                    self.own_prefix[k] = pfx
                if ln == "Elysia":
                    self.kinds.setdefault(k, ("elysia", "app"))
                    nm = sval(opts.get("name"))
                    if nm:
                        self.elysia_names[k] = nm
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
        self._elysia_collect_hooks(hook_calls)
        self._index_elysia_models()
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
        if not isinstance(d, dict) or depth > 16:
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
        if (ln in ("basePath", "route", "use", "get", "post", "put", "patch", "delete", "on", "prefix") or ln in ELYSIA_PASS) and d.get("recv"):
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
        keys = []
        if c.get("owner") and self._is_elysia_value(d):
            k = c["owner"]   # const payments = new Elysia({ prefix }).model().post()
            self.kinds.setdefault(k, ("elysia", "app"))
            self._note_elysia_opts(k, d)
            keys = [(k, "")]
        if not keys:
            keys = self.inst_keys(d, file_hint=c.get("file"))
        if not keys and c.get("owner") and ("new" in d or "call" in d) and self._factory(d):
            k = c["owner"]   # chain owned by a declaration: const api = Router().use(...)
            self.kinds.setdefault(k, self._factory(d))
            keys = [(k, "")]
        if not keys and ("new" in d or "call" in d) and self._factory(d):
            ctor = self._root_ctor(d) or d
            if last_name(ctor.get("new") or "") == "Elysia" and c.get("file"):
                k = self._inline_key(ctor, c["file"])
                self.kinds.setdefault(k, ("elysia", "app"))
                self._note_elysia_opts(k, ctor)
            else:
                ln = ctor.get("line") or c["line"]
                k = f"inline:{c['file']}:{ln}"
                self.kinds.setdefault(k, self._factory(d))
            keys = [(k, "")]
        out = []
        for k, pfx in keys:
            for ch in c.get("chain") or []:
                if ch["method"] in ("basePath", "prefix") and ch.get("args") and sval(ch["args"][0]):
                    pfx = join_path(pfx, sval(ch["args"][0]))
            out.append((k, pfx))
        return out

    def inst_keys(self, d, depth=0, file_hint=None, bindings=None) -> list[tuple[str, str]]:
        """Router instances a described value evaluates to: [(key, extra prefix)]."""
        if not isinstance(d, dict) or depth > 8:
            return []
        bindings = bindings or {}
        if d.get("new") and last_name(d.get("new")) == "Elysia":
            return self._elysia_inline(d, file_hint, bindings)
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
            hint = self._file_of_node(d.get("node")) or file_hint
            bind = dict(bindings)
            if d.get("node") and d.get("args"):
                bind.update(self._arg_bindings(d))
            if d.get("recv"):
                el = self._root_ctor(d)
                if el and el.get("new") and last_name(el.get("new")) == "Elysia":
                    got = self._elysia_inline(el, hint, bind)
                    if got:
                        return got
                got = self.inst_keys(d["recv"], depth + 1, hint, bind)
                if got and any(self._fw(k) == "elysia" or k.startswith("inline:") for k, _ in got):
                    return got
            out = []
            for r in d.get("ret") or []:
                out += self.inst_keys(r, depth + 1, hint, bind)
            if not out and last_name(d["call"]) in ("fp", "fastifyPlugin") and d.get("args"):
                out = self.inst_keys(d["args"][0], depth + 1, hint)
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
        elysia = any(self._fw(k) == "elysia" for k, _ in keys)
        if m == "group" and elysia:
            self._elysia_group(c, keys, st)
            return
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
            ks = [x for x in self.inst_keys(a, file_hint=c.get("file")) if x[0] in self.kinds or self._has_routes(x[0])]
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
                        conf = "exact" if self.kinds.get(ck, ("", ""))[1] in ("router", "app", "derived") else "resolved"
                        if any(isinstance(a, dict) and a.get("dyn_import") for a in flat):
                            conf = "heuristic"
                        self.mounts[ck].append((k, join_path(kp, p, cp), conf, mws, f"{c['file']}:{c['line']}"))
                        self.children[k].append(ck)
                        st["mounts"] += 1
        elif m in ("use", "lazyUse") and elysia:
            self._elysia_use_unresolved(c, keys, flat, st)
        elif m in ("use", "lazyUse"):
            for k, kp in keys:
                for p in prefixes:
                    for mw in mws:
                        self.router_mw[k].append((join_path(kp, p), mw, c["file"], c["line"]))

    def _index_elysia_models(self):
        """`.model({ name: t.Object(...) })` and `.model(importedMap)` on an Elysia instance."""
        self.model_by_key = defaultdict(dict)
        for rec in self.F.get("elysia_models") or []:
            mapping = obj(rec.get("arg"))
            if not mapping:
                continue
            keys = []
            if rec.get("owner"):
                keys.append(rec["owner"])
            keys += [k for k, _ in self.inst_keys(rec.get("recv") or {}, file_hint=rec.get("file"))]
            for k in keys:
                for name, val in mapping.items():
                    if str(name).startswith("..."):
                        continue
                    self.model_by_key[k].setdefault(name, val)

    def _models_visible(self, keys) -> dict:
        """Models on this instance and on plugins it `.use()`s."""
        out, seen, stack = {}, set(), [k for k, _ in keys]
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            for name, val in (getattr(self, "model_by_key", {}) or {}).get(k, {}).items():
                out.setdefault(name, val)
            for child in self.children.get(k, []):
                stack.append(child)
        return out

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
        """[(prefix, confidence, middleware, mounted_from_app)] for router k (`at`: the route call's file, line).

        A cycle contributes no prefix. The caller falls back to this instance's own prefix when every mount is cyclic,
        so `a.use(b); b.use(a)` does not grow `/a/b/a/b/...`.
        """
        own = self.own_prefix.get(k, "")
        if k in seen or depth > 8:
            return []
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
        # websocket upgrades declared on an HTTP verb: @fastify/websocket `{ websocket: true }`, Hono `upgradeWebSocket(h)`
        if obj_b(opts.get("websocket")) or any(isinstance(h, dict) and last_name(h.get("call")) == "upgradeWebSocket" for h in handlers):
            methods = ["WS"]
        mw = [self.mw_of(x) for x in mids]
        for kopt in MW_OPTS:
            v = opts.get(kopt)
            for x in (v.get("arr") if isinstance(v, dict) and v.get("arr") is not None else [v] if v else []):
                mw.append(self.mw_of(x))
        route_hooks = []
        if opts.get("beforeHandle"):
            route_hooks = self._hook_mws(opts.get("beforeHandle"), "beforeHandle", None, c["file"], c["line"])
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
        elysia_models = {}
        if any(self._fw(k) == "elysia" for k, _ in keys):
            # one map per instance: a route does not see models from a sibling plugin
            elysia_models = {k: self._models_visible([(k, "")]) for k, _ in keys}
        if not handler_nodes:
            attrs["handler_unresolved"] = True
        counted = False
        base_attrs = attrs
        literal = any(p == "" or p.startswith(("/", "*")) or self._plugin_rpc(p) for p in paths if "{" not in p[:1])
        for k, kp in keys:
            fw, kind = self.kinds.get(k, ("?", "name"))
            attrs = dict(base_attrs)
            if kind == "name" and not handler_nodes:
                continue
            if kind in ("name", "param") and fw == "?" and not literal:
                continue   # untyped receiver + non-literal first argument: a map/cache/http-client .get(key), not a route
            if fw == "elysia":
                tb, unknown = _typebox_request(opts, c["file"], c["line"], elysia_models.get(k) or {})
                if tb or unknown:
                    req = {"keys": tb}
                    if unknown:
                        req["unknown"] = unknown
                    attrs["request"] = req
                    body = [f["name"] + ("?" if f.get("optional") else "") for f in tb if f["location"] == "body"]
                    query = [f["name"] + ("?" if f.get("optional") else "") for f in tb if f["location"] == "query"]
                    if body:
                        attrs["body_fields"] = body
                    if query:
                        attrs["query_fields"] = query
            guards = self._elysia_route_mw(k, c["file"], c["line"]) + route_hooks if fw == "elysia" else []
            prefs = self.prefixes(k, at=(c["file"], c["line"]))
            if not prefs:
                kind_fb = self.kinds.get(k, ("?", "name"))[1]
                prefs = [(self.own_prefix.get(k, ""), "heuristic", self._mw_before(k, (c["file"], c["line"])), kind_fb == "app")]
            for pfx, pconf, pmw, mounted in prefs:
                for p in paths:
                    uri = express_path(join_path(pfx, kp, p))
                    conf = _weaker(hconf, pconf if mounted else "heuristic")
                    if kind == "name":
                        conf = "heuristic"
                    if any("{" in s and not re.fullmatch(r"\{\w+\*?\??\}", s) for s in uri.split("/") if s) or "{regex}" in uri:
                        conf = "heuristic"
                    ra = {**attrs, "router": (self.I.get(k) or {}).get("name") or c.get("recv_text"), "router_framework": fw}
                    rpc = self._plugin_rpc(p)
                    if not mounted:
                        ra["unmounted"] = True
                        st["routes_unmounted"] += 1
                    for method in methods:
                        prior = b.nodes.get(f"route:{method} {uri}")
                        if rpc and prior is not None and not prior.attrs.get("plugin_rpc"):
                            continue
                        route_attrs = {**ra, "plugin_rpc": True} if rpc else ra
                        add_route(b, method, uri, handler_nodes, c["file"], c["line"], fw if fw != "?" else "express", conf, route_attrs, pmw + mw + guards)
                        if not rpc and str(p).startswith("/"):
                            node = b.nodes.get(f"route:{method} {uri}")
                            if node is not None and node.attrs.get("plugin_rpc"):
                                node.attrs.pop("plugin_rpc", None)
                        st["routes"] += 1
                        if not handler_nodes and not counted:
                            st["routes_unresolved_handler"] += 1   # once per route call that produced routes
                            counted = True

    def _plugin_rpc(self, path: str) -> bool:
        """Plugin RPC: `router.post("github.webhooks", handler)`. A leading slash is an HTTP path, even with a dot."""
        if not path or path[:1] in "/.*":
            return False
        return bool(re.fullmatch(r"[A-Za-z_][\w]*(\.[\w]+)+", path))

    # ------------------------------------------------------------ Elysia
    def _fw(self, k) -> str:
        return self.kinds.get(k, ("?",))[0]

    def _root_ctor(self, d, depth=0):
        if not isinstance(d, dict) or depth > 16:
            return None
        if d.get("new") or (d.get("call") and not d.get("recv")):
            return d
        if d.get("recv"):
            return self._root_ctor(d["recv"], depth + 1)
        if d.get("cond"):
            for x in d["cond"]:
                r = self._root_ctor(x, depth + 1)
                if r:
                    return r
        return None

    def _is_elysia_value(self, d) -> bool:
        ctor = self._root_ctor(d)
        if not ctor:
            return False
        ln = last_name(ctor.get("new") or ctor.get("call") or "")
        mod = ctor.get("mod") or ""
        return ln == "Elysia" or mod == "elysia" or self._factory(d) == ("elysia", "app")

    def _note_elysia_opts(self, k, d, bindings=None):
        """Record a literal `{ prefix, name }`. A prefix that is a factory parameter becomes the call-site extra prefix
        (returned), not `own_prefix`, so two `makeShelf('/a')` / `makeShelf('/b')` mounts stay distinct."""
        bindings = bindings or {}
        ctor = self._root_ctor(d) or (d if d.get("new") else None)
        if not ctor or not ctor.get("args"):
            return ""
        opts = obj(ctor["args"][0])
        raw = opts.get("prefix")
        literal = sval(raw)
        extra = ""
        if literal:
            self.own_prefix[k] = literal
        elif isinstance(raw, dict) and raw.get("key") in bindings:
            extra = sval(bindings[raw["key"]]) or ""
        nm = sval(opts.get("name"))
        if nm:
            self.elysia_names[k] = nm
        return extra

    def _inline_key(self, d, file) -> str:
        opts = obj((d.get("args") or [None])[0]) if isinstance(d, dict) and d.get("args") else {}
        name = sval(opts.get("name")) or ""
        pfx = sval(opts.get("prefix")) or ""
        return f"inline:{file}:{d.get('line') or 0}:{name}:{pfx}"

    def _arg_bindings(self, d) -> dict:
        """Call-site arguments of a factory, keyed by the function's parameter instance id."""
        node = d.get("node")
        args = d.get("args") or []
        if not node or not args:
            return {}
        bind = {}
        for k, inst in self.I.items():
            if not inst or inst.get("kind") != "param" or inst.get("fn") != node:
                continue
            idx = inst.get("index")
            if isinstance(idx, int) and 0 <= idx < len(args):
                bind[k] = args[idx]
        return bind

    def _file_of_node(self, node) -> str | None:
        if not isinstance(node, str) or ":" not in node:
            return None
        body = node.split(":", 1)[1]
        file = body.split("#", 1)[0]
        return file or None

    def _elysia_inline(self, d, file_hint, bindings=None):
        line = d.get("line")
        if not file_hint or not line:
            return []
        k = self._inline_key(d, file_hint)
        self.kinds.setdefault(k, ("elysia", "app"))
        extra = self._note_elysia_opts(k, d, bindings)
        return [(k, extra or "")]

    def _callback_param(self, d, file=None):
        if not isinstance(d, dict):
            return None
        fn = d.get("fn") or d.get("node")
        if fn:
            for k, inst in self.I.items():
                if inst and inst.get("kind") == "param" and inst.get("index") == 0 and inst.get("fn") == fn:
                    return k
        line = d.get("line")
        if file and line:
            hits = [k for k, inst in self.I.items()
                    if inst and inst.get("kind") == "param" and inst.get("index") == 0
                    and inst.get("file") == file and inst.get("line") == line]
            if len(hits) == 1:
                return hits[0]
        return None

    def _elysia_group(self, c, keys, st):
        args = c.get("args") or []
        prefixes = svals(args[0]) if args else []
        param = self._callback_param(args[1] if len(args) > 1 else None, c["file"])
        if not param or not prefixes:
            return
        self.kinds[param] = ("elysia", "param")
        for k, kp in keys:
            for p in prefixes:
                self.mounts[param].append((k, join_path(kp, p), "exact", [], f"{c['file']}:{c['line']}"))
                self.children[k].append(param)
                st["mounts"] += 1

    def _elysia_use_unresolved(self, c, keys, flat, st):
        """`.use(import('./x'))` mounts that module's Elysia export (heuristic). A function plugin's parameter is a child
        instance. `use(openapi())` / `use(cors())` add no router and no guard."""
        for a in flat:
            if not isinstance(a, dict):
                continue
            if a.get("dyn_import"):
                for ck, cp in self._file_elysia(a.get("file")):
                    for k, kp in keys:
                        if ck == k:
                            continue
                        self.mounts[ck].append((k, join_path(kp, cp), "heuristic", [], f"{c['file']}:{c['line']}"))
                        self.children[k].append(ck)
                        st["mounts"] += 1
                continue
            for child in self.plugin_params(a):
                if not child or child in {k for k, _ in keys}:
                    continue
                self.kinds[child] = ("elysia", "param")
                for k, kp in keys:
                    self.mounts[child].append((k, kp, "resolved", [], f"{c['file']}:{c['line']}"))
                    self.children[k].append(child)
                    st["mounts"] += 1

    def _file_elysia(self, file):
        if not file:
            return []
        out = []
        for k, inst in self.I.items():
            if not inst or inst.get("file") != file:
                continue
            if self._fw(k) == "elysia" or self._is_elysia_value(inst.get("init") or {}):
                self.kinds.setdefault(k, ("elysia", "app"))
                self._note_elysia_opts(k, inst.get("init") or {})
                out.append((k, ""))
        return out

    def _read_span(self, file, line, n=40) -> str:
        """The hook function body, so a later function in the same file is not part of the check.

        `onBeforeHandle({ as }, function f({ set }) { ... })` has a `)` before the function, so the body starts
        after that function's parameter list (or after `=>`), not at the first `)` on the call line.
        """
        if not file or not line:
            return ""
        try:
            text = (self.root / file).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        i = max(0, int(line) - 1)
        src = "\n".join(text[i:i + n])
        func = re.search(r"\bfunction\b", src)
        arrow = src.find("=>")
        if func and (arrow < 0 or func.start() < arrow):
            j = func.end()
            while j < len(src) and src[j] not in "({":
                j += 1
            if j < len(src) and src[j] == "(":
                depth = 0
                for k in range(j, len(src)):
                    if src[k] == "(":
                        depth += 1
                    elif src[k] == ")":
                        depth -= 1
                        if depth == 0:
                            j = k + 1
                            break
            s = src[j:].lstrip()
        elif arrow >= 0:
            s = src[arrow + 2:].lstrip()
        else:
            return ""
        if not s.startswith("{"):
            return s.splitlines()[0] if s else ""
        depth = 0
        for i, ch in enumerate(s):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[:i + 1]
        return s

    def _rejects_auth(self, fn, file, line) -> list[int]:
        src_file, src_line = file, line
        if isinstance(fn, dict):
            if fn.get("key"):
                inst = self.I.get(fn["key"]) or {}
                if inst.get("file"):
                    src_file, src_line = inst["file"], inst.get("line") or line
            elif fn.get("line"):
                src_line = fn["line"]
        found = []
        for m in _AUTH_RE.finditer(self._read_span(src_file, src_line)):
            tok = next((g for g in m.groups() if g), "")
            code = 403 if tok in ("403", "Forbidden") else 401 if tok in ("401", "Unauthorized", "AuthError") else None
            if code and code not in found:
                found.append(code)
        return found

    def _hook_label(self, fn, owner) -> str:
        if isinstance(fn, dict):
            ref = fn.get("ref") or ""
            if ref and "=>" not in ref and not ref.startswith("(") and not ref.startswith("function"):
                return ref.split(".")[-1].split("(")[0]
        return self.elysia_names.get(owner) or (self.I.get(owner) or {}).get("name") or "hook"

    def _hook_mws(self, fn, kind, owner, file, line):
        fns = fn.get("arr") if isinstance(fn, dict) and fn.get("arr") is not None else [fn]
        out = []
        for f in fns:
            if not isinstance(f, dict):
                continue
            label = f"{self._hook_label(f, owner)} ({kind})"
            node, _name, conf = self.mw_of(f)
            codes = self._rejects_auth(f, file, line)
            checks = {"effect": "rejects", "rejects": codes} if codes else None
            out.append((node, label, conf if node else "exact", checks))
        return out

    def _elysia_collect_hooks(self, hook_calls):
        pending_as = []
        for c, keys in sorted(hook_calls, key=lambda x: (x[0]["file"], x[0]["line"])):
            m = c["method"]
            args = c.get("args") or []
            for k, _kp in keys:
                if m == "as":
                    scope = sval(args[0]) if args else None
                    if scope in ("local", "scoped", "global"):
                        pending_as.append((k, scope, c["file"], c["line"]))
                    continue
                if m == "guard":
                    self._elysia_guard(c, k, args)
                    continue
                scope, explicit, fn = _hook_fn(args)
                if fn is None:
                    continue
                for item in self._hook_mws(fn, m, k, c["file"], c["line"]):
                    self.elysia_hooks[k].append({"mw": item, "kind": m, "scope": scope or "local", "explicit": explicit,
                                                 "file": c["file"], "line": c["line"]})
        for k, scope, file, line in pending_as:
            # `.as()` promotes hooks registered before it. A later `.as('scoped')` does not demote `global`,
            # and `.as('global')` promotes both `local` and `scoped` (Elysia promoteEvent).
            for h in self.elysia_hooks.get(k, []):
                if h["explicit"] or h.get("propagated"):
                    continue
                if h["file"] == file and h["line"] > line:
                    continue
                if _SCOPE_RANK.get(scope, 0) > _SCOPE_RANK.get(h["scope"], 0):
                    h["scope"] = scope
        self._propagate_elysia_hooks()

    def _hook_sig(self, h):
        return (h.get("kind"), h["mw"][1], h.get("file"), h.get("line"), h.get("scope"))

    def _propagate_elysia_hooks(self):
        """Copy hooks the way `.use()` merges lifecycle.

        `onRequest` is spread onto every ancestor regardless of scope (Elysia `filterGlobalHook` keeps `request`),
        so it guards every route in that app, including routes and plugins registered earlier.
        A `global` hook is copied onto ancestors at the mount, so routes and plugins registered after that mount
        see it, and earlier ones do not.
        """
        changed, steps = True, 0
        while changed and steps < 12:
            steps += 1
            changed = False
            snapshot = [(src, h) for src, hs in self.elysia_hooks.items() for h in list(hs)]
            for src, h in snapshot:
                if h["kind"] != "onRequest" and h["scope"] != "global":
                    continue
                for parent, _pfx, _conf, _mws, ev in self.mounts.get(src, []):
                    via = _ev_at(ev)
                    if h["kind"] == "onRequest":
                        copy = {**h, "explicit": True, "propagated": True}
                    else:
                        if not via:
                            continue
                        copy = {**h, "explicit": True, "propagated": True, "file": via[0], "line": via[1]}
                    sig = self._hook_sig(copy)
                    if any(self._hook_sig(x) == sig for x in self.elysia_hooks.get(parent, [])):
                        continue
                    self.elysia_hooks[parent].append(copy)
                    changed = True

    def _elysia_guard(self, c, k, args):
        opts = obj(args[0]) if args else {}
        bh = opts.get("beforeHandle")
        if not bh:
            return
        scope = sval(opts.get("as")) if isinstance(opts.get("as"), dict) or opts.get("as") else None
        explicit = bool(scope)
        scope = scope or "local"
        cb = args[1] if len(args) > 1 else None
        param = self._callback_param(cb, c["file"]) if cb else None
        target = k
        if param:
            self.kinds[param] = ("elysia", "param")
            self.mounts[param].append((k, "", "exact", [], f"{c['file']}:{c['line']}"))
            self.children[k].append(param)
            target = param
            # routes inside the callback are the guard's whole instance; a non-global guard stays there
            if scope == "local":
                scope, explicit = "local", True
        for item in self._hook_mws(bh, "beforeHandle", k, c["file"], c["line"]):
            self.elysia_hooks[target].append({"mw": item, "kind": "beforeHandle", "scope": scope, "explicit": explicit,
                                              "file": c["file"], "line": c["line"]})

    def _hook_visible(self, h, route_file, route_line, via_mount=None) -> bool:
        # onRequest runs before routing, so it affects every route in scope. Other hooks affect routes registered after them.
        if h["kind"] == "onRequest":
            return True
        if via_mount:
            mf, ml = via_mount
            if route_file == mf and route_line < ml:
                return False
            return True
        return not (h["file"] == route_file and h["line"] > route_line)

    def _elysia_route_mw(self, k, file, line):
        """Guards Elysia applies to a route on instance k: own hooks, scoped/global plugins, ancestor hooks, global anywhere in the tree."""
        found = []
        seen = set()

        def add(h, via=None):
            label = h["mw"][1]
            if label in seen or not self._hook_visible(h, file, line, via):
                return
            seen.add(label)
            found.append(h["mw"])

        for h in self.elysia_hooks.get(k, []):
            add(h)
        for child in self.children.get(k, []):
            # scoped reaches the direct parent only; global is collected from the whole tree below
            ev = next((e[4] for e in self.mounts.get(child, []) if e[0] == k), None)
            via = _ev_at(ev)
            for h in self.elysia_hooks.get(child, []):
                if h["scope"] in ("scoped", "global"):
                    add(h, via)
        # local / scoped hooks on ancestors registered before this instance was mounted affect descendants
        for parent, _pfx, _conf, _mws, ev in self.mounts.get(k, []):
            via = _ev_at(ev)
            if not via:
                continue
            for h in self.elysia_hooks.get(parent, []):
                if h["scope"] in ("local", "scoped", "global") and self._hook_visible(h, via[0], via[1]):
                    add(h, via)
        return found


def _ev_at(ev):
    if not ev:
        return None
    f, _, ln = ev.rpartition(":")
    return (f, int(ln)) if ln.isdigit() else None


def _hook_fn(args):
    """(scope, explicit, fn) from onRequest(fn) / onBeforeHandle({ as }, fn)."""
    if not args:
        return "local", False, None
    a0 = args[0]
    if isinstance(a0, dict) and a0.get("obj") is not None and "as" in (a0.get("obj") or {}):
        return sval(a0["obj"].get("as")) or "local", True, args[1] if len(args) > 1 else None
    return "local", False, a0


def _typebox_type(val):
    if not isinstance(val, dict):
        return None
    call = last_name(val.get("call") or "")
    if call == "Optional":
        return _typebox_type((val.get("args") or [None])[0])
    return {"String": "string", "Number": "numeric", "Numeric": "numeric", "Integer": "integer",
            "Boolean": "boolean", "Array": "array"}.get(call)


def _typebox_fields(d):
    if not isinstance(d, dict):
        return []
    if last_name(d.get("call") or "") != "Object":
        return []
    args = d.get("args") or []
    fields = []
    for name, val in obj(args[0] if args else None).items():
        opt = isinstance(val, dict) and last_name(val.get("call") or "") == "Optional"
        fields.append({"name": name, "optional": opt, "type": _typebox_type(val)})
    return fields


def _resolve_typebox(d, models, seen=None):
    """Inline `t.Object`, a model name, or `t.Ref('name')`."""
    seen = seen or set()
    if not isinstance(d, dict):
        return None
    name = sval(d)
    if name:
        if name in seen or name not in models:
            return None
        return _resolve_typebox(models[name], models, seen | {name})
    call = last_name(d.get("call") or "")
    if call == "Ref":
        ref = sval((d.get("args") or [None])[0])
        if not ref or ref in seen or ref not in models:
            return None
        return _resolve_typebox(models[ref], models, seen | {ref})
    if call == "Object":
        return d
    return None


def _typebox_request(opts, file, line, models=None):
    """(keys, unknown locations). A named model or `t.Ref` that does not resolve is unknown, not an empty object."""
    models = models or {}
    out, unknown = [], []
    for loc, key in (("body", "body"), ("query", "query"), ("params", "params")):
        raw = opts.get(key)
        if not isinstance(raw, dict):
            continue
        if sval(raw) or last_name(raw.get("call") or "") == "Ref":
            schema = _resolve_typebox(raw, models)
            if schema is None:
                unknown.append(loc)
                continue
        else:
            schema = _resolve_typebox(raw, models)
        for f in _typebox_fields(schema):
            row = {"name": f["name"], "location": loc, "optional": f["optional"], "file": file, "line": line}
            if f.get("type"):
                row["type"] = f["type"]
            out.append(row)
    return out, unknown


def obj_b(d) -> bool:
    return isinstance(d, dict) and d.get("b") is True


def _weaker(a, b):
    order = {"exact": 3, "resolved": 2, "heuristic": 1}
    return a if order[a] <= order[b] else b

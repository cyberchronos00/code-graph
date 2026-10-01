"""Next.js framework layer (on the TypeScript plugin).

  app router   app/**/page.* -> page node (route in Next notation, e.g. /books/[id]; entry ui_page) RENDERS the default
               export; layout/template/error/loading/not-found/default -> layout node (ui_global), pages USES_LAYOUT every
               enclosing layout; route groups (x), parallel slots @x and private _folders are dropped, intercepting
               routes (.)x (..)x (...)x are resolved to the URL they intercept (best effort)
               app/**/route.* -> one route per exported HTTP verb (GET/POST/...; `export const GET = withAuth(fn)` and
               `export { h as GET }` resolve to the wrapped function); dynamic segments [id] -> {id}, [...slug] ->
               {slug*}, [[...slug]] -> {slug*?}
  pages router pages/**/*.tsx -> page (ui_page; getServerSideProps/getStaticProps CALLS), _app/_document -> ui_global,
               pages/api/** -> route per `req.method === 'X'` / switch case, else ANY
  server actions  'use server' modules (exported functions) and inline 'use server' functions -> route
               `ACTION <file>#<fn>` (entry http_route, method POST), clients reach them through ordinary CALLS
  middleware   middleware.ts / proxy.ts: USES_MIDDLEWARE from every route/page its `config.matcher` covers (regex matchers
               and no matcher: all, heuristic)
  next.config  static basePath (prefixed to every route/page) and literal rewrites (recorded; internal destinations are
               added as uri variants of the destination route)
  links        fetch/axios/ky/SWR calls in the same repo -> route handlers (MATCHES_ROUTE, see tsweb.common.link_in_repo);
               env keys incl. NEXT_PUBLIC_ (tsweb.data)
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from ...core.plugin import FrameworkPlugin, GraphBuilder, Project
from ..tsweb.common import (HTTP_VERBS, TEST_SKIP_RE, add_route, express_path, finish, fw_facts, has_server_framework,
                            join_path, merge_extractor_cfg, module_of, next_segment, obj, ref_nodes, register, sval, svals)

EXT = r"\.(tsx|ts|jsx|js|mdx)$"
SPECIAL = {"layout", "template", "error", "loading", "not-found", "default", "global-error", "forbidden", "unauthorized"}
CONFIG_FILES = ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs")


class NextPlugin(FrameworkPlugin):
    name, language = "nextjs", "typescript"

    def detect(self, project: Project) -> bool:
        return has_server_framework(project, "next") or any(project.exists(f) for f in CONFIG_FILES)

    def register_hooks(self, ctx) -> None:
        root = ctx.project.root
        merge_extractor_cfg(ctx.extractor_cfg, src_dirs=["."], skip_re=TEST_SKIP_RE + r"|(^|/)(public|scripts)(/|$)",
                            extra_files=[f for f in CONFIG_FILES if (root / f).exists()] +
                            [f for f in ("middleware.ts", "middleware.js", "src/middleware.ts", "src/middleware.js", "proxy.ts", "src/proxy.ts") if (root / f).exists()],
                            walk_src=not (root / "tsconfig.json").exists())
        register(ctx, self.name)

    # ------------------------------------------------------------
    def contribute(self, project: Project, b: GraphBuilder, ctx) -> dict:
        st = {"pages": 0, "layouts": 0, "route_handlers": 0, "api_routes": 0, "server_actions": 0, "middleware": None,
              "base_path": None, "rewrites": 0}
        F = fw_facts(ctx)
        if not F:
            return {**st, "status": "no facts"}
        self.b, self.F = b, F
        mods = F.get("modules") or {}
        self.mods = mods
        root = project.root
        app_dirs = [d for d in ("app", "src/app") if (root / d).is_dir()]
        page_dirs = [d for d in ("pages", "src/pages") if (root / d).is_dir()]
        base_path, rewrites = self._config(mods)
        st["base_path"], st["rewrites"] = base_path, len(rewrites)
        self.base = base_path or ""
        files = sorted({n.file for n in b.nodes.values() if n.lang == "ts" and n.kind == "module" and n.file})
        self.routes, self.pages = [], []
        layouts_by_dir = {}
        for f in files:
            for ad in app_dirs:
                if not f.startswith(ad + "/") or not re.search(EXT, f):
                    continue
                stem = re.sub(EXT, "", PurePosixPath(f).name)
                rel_dir = str(PurePosixPath(f).parent)[len(ad):].strip("/")
                url = self._app_url(rel_dir)
                if url is None:
                    continue
                if stem == "page":
                    self._page(f, url, "app", st)
                elif stem == "route":
                    self._route_handlers(f, url, st)
                elif stem in SPECIAL:
                    lid = self._layout(f, stem, st)
                    layouts_by_dir.setdefault(str(PurePosixPath(f).parent), []).append(lid)
            for pd in page_dirs:
                if not f.startswith(pd + "/") or not re.search(EXT, f):
                    continue
                rel = re.sub(EXT, "", f[len(pd) + 1:])
                parts = rel.split("/")
                if parts[0] == "api":
                    url = self._pages_url(parts)
                    if url is not None:
                        self._api_route(f, url, st)
                elif parts[-1] in ("_app", "_document", "_error") and len(parts) == 1:
                    lid = self._layout(f, parts[-1], st)
                    layouts_by_dir.setdefault(pd, []).append(lid)
                elif not any(p.startswith("_") for p in parts):
                    url = self._pages_url(parts)
                    if url is not None:
                        self._page(f, url, "pages", st)
        # pages use every enclosing layout (app router) / _app (pages router)
        for pid, f in self.pages:
            d = PurePosixPath(f).parent
            while True:
                for lid in layouts_by_dir.get(str(d), []):
                    if lid != pid:
                        b.add_edge(pid, lid, "USES_LAYOUT", file=f, line=1, confidence="exact")
                if str(d) in app_dirs + page_dirs or str(d) in (".", ""):
                    break
                d = d.parent
        self._server_actions(st)
        self._middleware(st)
        for src, dst in rewrites:
            for r in self.routes:
                n = b.nodes[r]
                if n.attrs.get("uri") == express_path(self.base + dst) and not re.search(r"^https?:", dst):
                    n.attrs.setdefault("uri_variants", []).append(express_path(self.base + src))
                    n.attrs.setdefault("rewrites", []).append({"source": src, "destination": dst})
        st.update(finish(project, b, ctx, self.name))
        return st

    # ------------------------------------------------------------ paths
    def _app_url(self, rel_dir: str) -> str | None:
        segs = []
        for s in [x for x in rel_dir.split("/") if x]:
            m = re.match(r"^(\((?:\.{1,3}|\.\.\)\(\.\.)\))(.*)$", s)   # intercepting route
            if m:
                marker, rest = m.group(1), m.group(2)
                if marker == "(.)":
                    pass
                elif marker == "(...)":
                    segs = []
                else:
                    ups = marker.count("..")
                    segs = segs[:max(0, len(segs) - ups)]
                s = rest
            ns = next_segment(s)
            if ns is None:
                return None
            if ns:
                segs.append(ns)
        return "/" + "/".join(segs)

    def _pages_url(self, parts: list[str]) -> str | None:
        segs = []
        for i, p in enumerate(parts):
            if p == "index" and i == len(parts) - 1:
                continue
            ns = next_segment(p)
            if ns is None:
                return None
            if ns:
                segs.append(ns)
        return "/" + "/".join(segs)

    @staticmethod
    def _page_name(url: str) -> str:
        return re.sub(r"\{(\w+)\*\?\}", r"[[...\1]]", re.sub(r"\{(\w+)\*\}", r"[...\1]", re.sub(r"\{(\w+)\}", r"[\1]", url)))

    def _exports(self, f) -> dict:
        return {e["name"]: e for e in (self.mods.get(f) or {}).get("exports") or []}

    def _targets(self, ex: dict | None) -> tuple[list[str], str]:
        if not ex:
            return [], "exact"
        if ex.get("node") and self.b.has(ex["node"]):
            return [ex["node"]], "exact"
        nodes = [n for n in ref_nodes(ex.get("desc")) if self.b.has(n)]
        return nodes, "resolved"

    # ------------------------------------------------------------ nodes
    def _page(self, f, url, router, st):
        b = self.b
        name = self._page_name(join_path(self.base, url) if self.base else url)
        pid = b.add_node("page", f, name=name, fqn=name, file=f, line=1, module=module_of(f), lang="ts",
                         attrs={"route": name, "uri": express_path(join_path(self.base, url)), "framework": "nextjs", "router": router})
        b.nodes[pid].entry_kind = "ui_page"
        ex = self._exports(f)
        tg, conf = self._targets(ex.get("default"))
        for t in tg:
            b.add_edge(pid, t, "RENDERS", file=f, line=b.nodes[t].line or 1, confidence=conf)
        b.add_edge(pid, f"module:{f}", "RENDERS", file=f, line=1, confidence="exact") if not tg and b.has(f"module:{f}") else None
        for data_fn in ("getServerSideProps", "getStaticProps", "getStaticPaths", "generateMetadata", "generateStaticParams", "getInitialProps"):
            tg2, c2 = self._targets(ex.get(data_fn))
            for t in tg2:
                b.add_edge(pid, t, "CALLS", file=f, line=b.nodes[t].line or 1, confidence=c2, data_fetching=data_fn)
        self.pages.append((pid, f))
        st["pages"] += 1

    def _layout(self, f, stem, st):
        b = self.b
        lid = b.add_node("layout", f, name=f, file=f, line=1, module=module_of(f), lang="ts", attrs={"framework": "nextjs", "next_kind": stem})
        b.nodes[lid].entry_kind = "ui_global"
        tg, conf = self._targets(self._exports(f).get("default"))
        for t in tg:
            b.add_edge(lid, t, "RENDERS", file=f, line=b.nodes[t].line or 1, confidence=conf)
        st["layouts"] += 1
        return lid

    def _route_handlers(self, f, url, st):
        ex = self._exports(f)
        uri = express_path(join_path(self.base, url))
        for verb in HTTP_VERBS:
            if verb not in ex:
                continue
            tg, conf = self._targets(ex[verb])
            e = ex[verb]
            attrs = {"file_route": True}
            d = e.get("desc") or {}
            if "call" in d:
                attrs["wrapped_by"] = d["call"]
            if not tg:
                attrs["handler_unresolved"] = True
            mw = [(n, d["call"], "resolved") for n in [d.get("node")] if n and "call" in d and self.b.has(n)]
            rid = add_route(self.b, verb, uri, tg, f, e.get("line") or 1, "nextjs", conf, attrs, mw)
            self.routes.append(rid)
            st["route_handlers"] += 1

    def _api_route(self, f, url, st):
        ex = self._exports(f)
        tg, conf = self._targets(ex.get("default"))
        checks = (self.mods.get(f) or {}).get("method_checks") or []
        methods = sorted({c["method"] for c in checks}) or ["ANY"]
        uri = express_path(join_path(self.base, "/api", url[len("/api"):] if url.startswith("/api") else url))
        d = (ex.get("default") or {}).get("desc") or {}
        mw = [(n, d["call"], "resolved") for n in [d.get("node")] if n and "call" in d and self.b.has(n)]
        for m in methods:
            rid = add_route(self.b, m, uri, tg, f, (ex.get("default") or {}).get("line") or 1, "nextjs", conf,
                            {"pages_api": True, "method_from": "req.method check" if checks else "any"}, mw)
            self.routes.append(rid)
            st["api_routes"] += 1

    def _server_actions(self, st):
        b = self.b
        seen = set()
        for f, m in self.mods.items():
            fns = list(m.get("server_fns") or [])
            if "use server" in (m.get("directives") or []):
                fns += [e["node"] for e in m.get("exports") or [] if e.get("node") and e["node"].startswith("function:")]
            for fn in fns:
                if fn in seen or not b.has(fn):
                    continue
                seen.add(fn)
                n = b.nodes[fn]
                key = f"ACTION {fn.split(':', 1)[1]}"
                rid = b.add_node("route", key, name=key, file=n.file, line=n.line, module=module_of(n.file), lang="ts", entry_kind="http_route",
                                 attrs={"method": "POST", "server_action": True, "framework": "nextjs", "handler": n.name})
                b.add_edge(rid, fn, "ROUTES_TO", file=n.file, line=n.line, confidence="exact")
                st["server_actions"] += 1

    def _middleware(self, st):
        b = self.b
        for f in ("middleware.ts", "middleware.js", "src/middleware.ts", "src/middleware.js", "proxy.ts", "proxy.js", "src/proxy.ts"):
            m = self.mods.get(f)
            if not m:
                continue
            ex = {e["name"]: e for e in m.get("exports") or []}
            fn = (ex.get("middleware") or ex.get("proxy") or ex.get("default") or {}).get("node")
            if not fn or not b.has(fn):
                tg = ref_nodes((ex.get("default") or {}).get("desc"))
                fn = tg[0] if tg else None
            if not fn:
                continue
            cfg = obj((ex.get("config") or {}).get("desc"))
            matchers = svals(cfg.get("matcher")) or [sval(obj(x).get("source")) for x in (cfg.get("matcher") or {}).get("arr") or [] if sval(obj(x).get("source"))]
            regex = [p for p in matchers if re.search(r"[()|\\^$]", p)]
            pats = [p for p in matchers if p not in regex]
            st["middleware"] = {"file": f, "matchers": matchers}
            targets = [(r, b.nodes[r].attrs.get("uri")) for r in self.routes] + [(p, b.nodes[p].attrs.get("uri")) for p, _ in self.pages]
            for nid, uri in targets:
                if not uri:
                    continue
                if not matchers or regex:
                    hit, conf = (not regex or self._regex_hit(regex, uri)), "heuristic"
                else:
                    hit, conf = any(self._matcher_hit(p, uri) for p in pats), "resolved"
                if hit:
                    b.add_edge(nid, fn, "USES_MIDDLEWARE", file=f, line=b.nodes[fn].line or 1, confidence=conf, name="middleware")

    def _matcher_hit(self, pat: str, uri: str) -> bool:
        p = express_path(pat)
        ps, us = [s for s in p.split("/") if s], [s for s in uri.split("/") if s]
        for i, s in enumerate(ps):
            if re.fullmatch(r"\{\w+\*\??\}", s):
                return len(us) >= i + (0 if s.endswith("?}") else 1)
            if i >= len(us):
                return bool(re.fullmatch(r"\{\w+\?\}", s))
            if s.startswith("{") or us[i].startswith("{") or s == us[i]:
                continue
            return False
        return len(us) == len(ps)

    @staticmethod
    def _regex_hit(regex: list[str], uri: str) -> bool:
        probe = re.sub(r"\{(\w+)\*?\??\}", r"x", uri)
        for r in regex:
            try:
                if re.fullmatch(r.replace("/:path*", "(/.*)?"), probe) or re.match(r, probe):
                    return True
            except re.error:
                return True
        return False

    def _config(self, mods):
        base, rewrites = None, []
        for f in CONFIG_FILES:
            m = mods.get(f)
            if not m:
                continue
            ex = {e["name"]: e for e in m.get("exports") or []}
            d = (ex.get("default") or ex.get("export=") or {}).get("desc") or {}
            o = self._config_obj(d)
            base = sval(o.get("basePath")) or None
            rw = o.get("rewrites") or {}
            for x in (rw.get("ret") or [rw.get("body")] if (rw.get("ret") or rw.get("body")) else []):
                for it in (x or {}).get("arr") or []:
                    s, t = sval(obj(it).get("source")), sval(obj(it).get("destination"))
                    if s and t:
                        rewrites.append((s, t))
        return base, rewrites

    def _config_obj(self, d, depth=0) -> dict:
        if not isinstance(d, dict) or depth > 4:
            return {}
        if d.get("obj") is not None:
            return d["obj"]
        if d.get("key"):
            inst = (self.F.get("instances") or {}).get(d["key"]) or {}
            return self._config_obj(inst.get("init"), depth + 1)
        if "call" in d:   # withPlugins(nextConfig) / withBundleAnalyzer({...})
            for a in d.get("args") or []:
                o = self._config_obj(a, depth + 1)
                if o:
                    return o
        if d.get("body") or d.get("ret"):
            return self._config_obj(d.get("body") or (d.get("ret") or [None])[0], depth + 1)
        return {}

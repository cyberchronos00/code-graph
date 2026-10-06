"""Astro framework layer (on the TypeScript plugin).

  pages        src/pages/**.astro -> page node (route `/`, `/books/{slug}`, `/docs/{rest*}`; entry ui_page).
               A segment starting with `_` is skipped. Frontmatter and <script> blocks are TypeScript at
               their real lines (the extractor); template expressions are not code.
  endpoints    src/pages/**.{ts,js,mts,mjs} exporting GET/POST/... (`ALL` -> ANY) -> one route each
  components   <Card /> in the template -> RENDERS; imports from .ts and other .astro files resolve
  getStaticPaths  recorded as attrs.get_static_paths when the frontmatter exports it (not evaluated)
  is:inline    <script is:inline> counted on the file node as attrs.inline_scripts
  config       literal srcDir, base, trailingSlash, redirects and i18n from astro.config affect page and route names
  redirects    config entries become redirect pages; Astro.redirect, rewrite and context.redirect are navigation
  middleware   onRequest is USES_MIDDLEWARE from every page and route, in sequence order
  actions      POST /_actions/<name> routes, plus CALLS from actions.x() and <form action={actions.x}>
  markdown     .md / .mdx / .html pages under pages/ (frontmatter layout is RENDERS; MDX imports and calls)
  collections  content.config.ts (or legacy content/config.ts) -> table nodes; getCollection / getEntry / render read them
"""
from __future__ import annotations

import re
from pathlib import Path

from ...core.plugin import FrameworkPlugin, GraphBuilder, Project
from ..nuxt.plugin import _JS
from ..tsweb.common import HTTP_VERBS, add_route, finish, fw_facts, merge_extractor_cfg, register, pkg_deps

CONFIG_FILES = ("astro.config.mjs", "astro.config.js", "astro.config.ts", "astro.config.mts", "astro.config.cjs")
_PAGE_EXT = (".markdown", ".mdown", ".mdwn", ".mkdn", ".mdx", ".astro", ".html", ".mts", ".mjs", ".mkd", ".md", ".ts", ".js")
_GSP = re.compile(r"export\s+(?:async\s+)?function\s+getStaticPaths\b|export\s+const\s+getStaticPaths\b")
_FM_OPEN = re.compile(r"\ufeff?\s*---[ \t]*\r?\n")
_INLINE = re.compile(r"<script\b([^>]*)>", re.I)
_CATCHALL = re.compile(r"\[\.\.\.([^\]]+)\]")
_PARAM = re.compile(r"\[([^\]]+)\]")
_PRERENDER = re.compile(r"export\s+const\s+prerender\s*=\s*(true|false)\b")
_FORM_ACTION = re.compile(r"action\s*=\s*\{\s*actions\.((?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*)\s*\}")
_ACTION_INDEX = ("index.ts", "index.js", "index.mts")
_MW_FILES = ("middleware.ts", "middleware.js", "middleware.mts", "middleware/index.ts", "middleware/index.js")
_I18N_URL = ("getRelativeLocaleUrl", "getAbsoluteLocaleUrl")
_SENTINEL = ("expr", "bad", "num")


def _sentinel(v) -> bool:
    return isinstance(v, tuple) and bool(v) and v[0] in _SENTINEL


def _norm_src_dir(v) -> str:
    """`./app/` -> `app`; `.` or `./` (the project root) -> `""`; a non-literal keeps the default `src`."""
    if not isinstance(v, str):
        return "src"
    s = v.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    s = s.strip("/")
    return "" if s in ("", ".") else s


def _pages_prefix(src_dir) -> str:
    root = "src" if src_dir is None else src_dir.strip("/")
    return f"{root}/pages/" if root else "pages/"


_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.I)


def _norm_base(v) -> str:
    if not isinstance(v, str):
        return ""
    s = v.strip().replace("\\", "/")
    if s.startswith("./"):
        s = s[2:]
    s = s.strip("/")
    return ("/" + s) if s else ""


def _route_token(path: str, base: str) -> str:
    """A config path: leading `/`, `[x]` → `{x}`, `[...x]` → `{x*}`, then the base prefix."""
    path = str(path).replace("\\", "/").strip()
    if not path.startswith("/"):
        path = "/" + path
    path = _PARAM.sub(r"{\1}", _CATCHALL.sub(r"{\1*}", path))
    b = _norm_base(base)
    if not b or path == b or path.startswith(b + "/"):
        return path
    return b if path == "/" else b + path


def _key_line(src: str, key: str) -> int:
    m = re.search(r"(['\"])" + re.escape(key) + r"\1\s*:", src)
    return src.count("\n", 0, m.start()) + 1 if m else 1


def _config_object(src: str):
    js = _JS(src)
    m = re.search(r"\bdefineConfig\s*\(", src)
    if not m:
        m = re.search(r"\bexport\s+default\b", src)
    if m:
        js.i = m.end()
    val = js.parse_value()
    return val if isinstance(val, dict) else None


def _locales_of(v) -> list[str] | None:
    if _sentinel(v) or not isinstance(v, list):
        return None
    out = []
    for item in v:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            out.append(item["path"])
    return out


def read_astro_config(root) -> dict:
    """Literal astro.config fields. Non-literals are ignored. `redirect_status` / `redirect_lines` sit beside `redirects`."""
    out = {
        "file": None, "src_dir": "src", "base": "", "trailing_slash": None,
        "redirects": {}, "redirect_status": {}, "redirect_lines": {},
        "locales": None, "default_locale": None, "prefix_default_locale": False,
    }
    root = Path(root)
    path = next((root / f for f in CONFIG_FILES if (root / f).is_file()), None)
    if path is None:
        return out
    out["file"] = path.name
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    val = _config_object(src)
    if not val:
        return out
    if isinstance(val.get("srcDir"), str):
        out["src_dir"] = _norm_src_dir(val["srcDir"])
    if isinstance(val.get("base"), str):
        out["base"] = _norm_base(val["base"])
    if isinstance(val.get("trailingSlash"), str):
        out["trailing_slash"] = val["trailingSlash"]
    redirs = val.get("redirects")
    if isinstance(redirs, dict):
        for key, dest in redirs.items():
            if not isinstance(key, str) or _sentinel(dest):
                continue
            status = None
            if isinstance(dest, str):
                to = dest
            elif isinstance(dest, dict) and isinstance(dest.get("destination"), str):
                to = dest["destination"]
                st = dest.get("status")
                if isinstance(st, (int, float)) and not isinstance(st, bool):
                    status = int(st)
            else:
                continue
            out["redirects"][key] = to
            out["redirect_lines"][key] = _key_line(src, key)
            if status is not None:
                out["redirect_status"][key] = status
    i18n = val.get("i18n")
    if isinstance(i18n, dict):
        locs = _locales_of(i18n.get("locales"))
        if locs is not None:
            out["locales"] = locs
        if isinstance(i18n.get("defaultLocale"), str):
            out["default_locale"] = i18n["defaultLocale"]
        routing = i18n.get("routing")
        if isinstance(routing, dict) and isinstance(routing.get("prefixDefaultLocale"), bool):
            out["prefix_default_locale"] = routing["prefixDefaultLocale"]
    return out


def astro_route(rel: str, src_dir: str = "src", base: str = "") -> str | None:
    """`<srcDir>/pages` path -> `/`, `/books/{slug}`, `/docs/{rest*}`, with `base` prefixed. None when a segment starts with `_`."""
    rel = rel.replace("\\", "/")
    prefix = _pages_prefix(src_dir)
    if not rel.startswith(prefix) or rel.endswith(".d.ts"):
        return None
    rest = rel[len(prefix):]
    for ext in _PAGE_EXT:
        if rest.endswith(ext):
            rest = rest[: -len(ext)]
            break
    segs = [s for s in rest.split("/") if s]
    if any(s.startswith("_") for s in segs):
        return None
    if segs and segs[-1] == "index":
        segs.pop()
    segs = [_PARAM.sub(r"{\1}", _CATCHALL.sub(r"{\1*}", s)) for s in segs]
    route = "/" + "/".join(segs)
    b = _norm_base(base)
    if not b:
        return route
    return b if route == "/" else b + route


def _endpoint(rel: str, src_dir: str) -> bool:
    rel = rel.replace("\\", "/")
    prefix = _pages_prefix(src_dir)
    return bool(re.match(r"^.+\.(?:ts|js|mts|mjs)$", rel[len(prefix):] if rel.startswith(prefix) else "")) and not rel.endswith(".d.ts")


def _split(text: str) -> tuple[str, str]:
    """(frontmatter, template), as the extractor reads them: an empty frontmatter counts, an unclosed one runs to
    the end of the file."""
    m = _FM_OPEN.match(text)
    if not m:
        return "", text
    start = m.end()
    end = text.find("\n---", start - 1)
    if end < 0:
        return text[start:], ""
    return text[start:max(start, end)], text[end + 4:]


def _inline_scripts(template: str) -> int:
    return sum(1 for m in _INLINE.finditer(template) if re.search(r"\bis:inline\b", m.group(1)))


def _prerender(text: str):
    m = _PRERENDER.search(text)
    if not m:
        return None
    return m.group(1) == "true"


def _prefix_nav(value: str, base: str) -> str:
    if not base or not isinstance(value, str) or not value.startswith("/"):
        return value
    if value == base or value.startswith(base + "/"):
        return value
    return base + value


def _locale_of(route: str, base: str, locales: list[str], default: str | None, prefix_default: bool) -> str | None:
    rest = route
    if base and (rest == base or rest.startswith(base + "/")):
        rest = rest[len(base):] or "/"
    segs = [s for s in rest.split("/") if s]
    if segs and segs[0] in locales:
        return segs[0]
    if not prefix_default and default:
        return default
    return None


def _walk_actions(desc, prefix=""):
    if not isinstance(desc, dict):
        return
    if desc.get("call") == "defineAction":
        if prefix:
            yield prefix, desc
        return
    obj = desc.get("obj")
    if isinstance(obj, dict):
        for key, child in obj.items():
            if str(key).startswith("..."):
                continue
            yield from _walk_actions(child, f"{prefix}.{key}" if prefix else str(key))


def _action_meta(call: dict) -> tuple[str | None, str, int]:
    arg0 = (call.get("args") or [None])[0]
    obj = arg0.get("obj") if isinstance(arg0, dict) else None
    if not isinstance(obj, dict):
        obj = {}
    handler = obj.get("handler") if isinstance(obj.get("handler"), dict) else {}
    accept = obj.get("accept") if isinstance(obj.get("accept"), dict) else {}
    acc = accept.get("s") if isinstance(accept.get("s"), str) else "json"
    line = arg0.get("line") if isinstance(arg0, dict) and isinstance(arg0.get("line"), int) else (handler.get("line") or 1)
    return handler.get("fn") or handler.get("node"), acc, line   # inline handler, or a named function


def _sequence_fns(desc, nested=False):
    if not isinstance(desc, dict) or desc.get("call") != "sequence":
        return None
    out = []
    for arg in desc.get("args") or []:
        if not isinstance(arg, dict):
            continue
        if arg.get("call") == "sequence" and not nested:
            out.extend(_sequence_fns(arg, True) or [])
        elif arg.get("node"):
            out.append((arg["node"], arg.get("ref") or "middleware"))
    return out


def _middleware_fns(ex: dict) -> list[tuple[str, str]]:
    desc = ex.get("desc") if isinstance(ex.get("desc"), dict) else {}
    seq = _sequence_fns(desc)
    if seq is not None:
        return seq
    node = ex.get("node") or desc.get("node")
    if node:
        return [(node, desc.get("ref") or ex.get("name") or "onRequest")]
    return []


def _form_actions(text: str):
    _fm, template = _split(text)
    if template and text.endswith(template):
        prefix = text[:len(text) - len(template)]
    else:
        prefix = ""
    base_lines = prefix.count("\n")
    for m in _FORM_ACTION.finditer(template):
        yield m.group(1), base_lines + template[:m.start()].count("\n") + 1


def _i18n_path(base: str, locale: str, path: str, default: str | None, prefix_default: bool) -> str:
    rel = path if path.startswith("/") else "/" + path
    if locale == default and not prefix_default:
        return (base or "") + rel
    return (base or "") + "/" + locale + rel


class AstroPlugin(FrameworkPlugin):
    name, language = "astro", "typescript"

    def detect(self, project: Project) -> bool:
        return "astro" in pkg_deps(project.root) or any(project.exists(f) for f in CONFIG_FILES)

    def register_hooks(self, ctx) -> None:
        root = ctx.project.root
        cfg = ctx.extractor_cfg
        ac = read_astro_config(root)
        src_dir = ac["src_dir"]
        pre = f"{src_dir}/" if src_dir else ""
        kinds = cfg.setdefault("kinds", [])
        kinds.extend([(pre + "pages/", "page"), (pre + "layouts/", "layout"), (pre + "components/", "component")])
        merge_extractor_cfg(cfg, src_dirs=[src_dir] if src_dir else ["."], walk_src=not (root / "tsconfig.json").exists())
        cfg["astro"] = {"src_dir": src_dir, "base": ac["base"]}
        register(ctx, self.name)

    def contribute(self, project: Project, b: GraphBuilder, ctx) -> dict:
        ac = read_astro_config(project.root)
        src_dir, base = ac["src_dir"], ac["base"]
        locales = ac["locales"] or []
        st = {"pages": 0, "endpoints": 0, "layouts": 0, "components": 0, "inline_scripts": 0, "get_static_paths": 0,
              "redirects": 0, "actions": 0, "middleware": 0, "locales": len(locales)}
        for n in b.nodes.values():
            if not n.file or not n.file.endswith(".astro") or n.kind not in ("page", "layout", "component"):
                continue
            try:
                text = (project.root / n.file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if n.kind == "page":
                route = astro_route(n.file, src_dir, base)
                if route is not None:
                    n.name = n.fqn = route
                    n.attrs.update(route=route, uri=route, framework="astro")
                    if ac["trailing_slash"]:
                        n.attrs["trailing_slash"] = ac["trailing_slash"]
                    loc = _locale_of(route, base, locales, ac["default_locale"], ac["prefix_default_locale"]) if locales else None
                    if loc:
                        n.attrs["locale"] = loc
                    pre = _prerender(_split(text)[0])
                    if pre is not None:
                        n.attrs["prerender"] = pre
                    n.entry_kind = "ui_page"
                    st["pages"] += 1
                    if _GSP.search(_split(text)[0]):
                        n.attrs["get_static_paths"] = True
                        st["get_static_paths"] += 1
            elif n.kind == "layout":
                st["layouts"] += 1
            else:
                st["components"] += 1
            inline = _inline_scripts(_split(text)[1])
            if inline:
                n.attrs["inline_scripts"] = inline
                st["inline_scripts"] += inline
        mods = (fw_facts(ctx).get("modules") or {})
        for f, mod in mods.items():
            if f.endswith(".d.ts") or not _endpoint(f, src_dir):
                continue
            uri = astro_route(f, src_dir, base)
            if uri is None:
                continue
            try:
                body = (project.root / f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                body = ""
            prer = _prerender(body)
            for ex in mod.get("exports") or []:
                name = ex.get("name") or ""
                if name == "ALL":
                    verb = "ANY"
                elif name in HTTP_VERBS:
                    verb = name
                else:
                    continue
                node = ex.get("node")
                handlers = [node] if node and b.has(node) else []
                attrs = {"file_route": True}
                if ac["trailing_slash"]:
                    attrs["trailing_slash"] = ac["trailing_slash"]
                if prer is not None:
                    attrs["prerender"] = prer
                add_route(b, verb, uri, handlers, f, ex.get("line") or 1, "astro",
                          "exact" if handlers else "resolved", attrs)
                st["endpoints"] += 1
        facts = ctx.facts if ctx and ctx.facts is not None else {}
        sites = facts.setdefault("nav_sites", [])
        cfg_file = ac["file"] or ""
        for src, dest in ac["redirects"].items():
            route = _route_token(src, base)
            external = bool(_EXTERNAL.match(dest.strip()))
            to = dest.strip() if external else _route_token(dest, base)
            line = ac["redirect_lines"].get(src) or 1
            attrs = {"route": route, "uri": route, "redirect": to, "framework": "astro", "via": "redirects"}
            if src in ac["redirect_status"]:
                attrs["status"] = ac["redirect_status"][src]
            nid = b.add_node("page", f"astro:redirect:{route}", name=route, file=cfg_file, line=line, lang="ts", attrs=attrs)
            if not external:    # an external destination is not a page of this site
                sites.append({"src": nid, "file": cfg_file, "line": line, "via": "redirect",
                              "locs": [{"kind": "path", "value": to, "conf": "exact"}]})
            st["redirects"] += 1
        calls = facts.get("astro_calls") or []
        for call in calls:
            fn = call.get("fn")
            args = call.get("args") or []
            if fn in _I18N_URL and len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], str):
                value = _i18n_path(base, args[0], args[1], ac["default_locale"], ac["prefix_default_locale"])
                sites.append({"src": call.get("src"), "file": call.get("file"), "line": call.get("line"), "via": "i18n",
                              "locs": [{"kind": "path", "value": value, "conf": "exact"}]})
            elif fn == "url" and args and isinstance(args[0], str) and args[0].startswith("/"):
                sites.append({"src": call.get("src"), "file": call.get("file"), "line": call.get("line"), "via": "url",
                              "locs": [{"kind": "path", "value": args[0], "conf": "exact"}]})
        # Markdown pages before the base prefix (their links are sites too) and before middleware (it covers them)
        from .content import contribute_content
        contribute_content(project, b, ac, calls, sites, st)
        if base:
            for site in sites:
                for loc in site.get("locs") or []:
                    if loc.get("kind") == "path":
                        loc["value"] = _prefix_nav(loc.get("value"), base)
        handlers = self._actions(project, b, mods, src_dir, base, st)
        self._action_calls(b, calls, handlers)
        self._forms(project, b, handlers, src_dir)
        self._middleware(b, mods, src_dir, st)
        st.update(finish(project, b, ctx, self.name))
        return st

    def _actions(self, project, b, mods, src_dir, base, st) -> dict:
        names = [f"{src_dir}/actions/{n}" if src_dir else f"actions/{n}" for n in _ACTION_INDEX]
        names.append(f"{src_dir}/actions.ts" if src_dir else "actions.ts")
        handlers = {}
        for f in names:
            mod = mods.get(f)
            if not mod:
                continue
            for ex in mod.get("exports") or []:
                if ex.get("name") != "server":
                    continue
                for path, call in _walk_actions(ex.get("desc") or {}):
                    fn, accept, line = _action_meta(call)
                    hs = [fn] if fn and b.has(fn) else []
                    uri = f"{base}/_actions/{path}" if base else f"/_actions/{path}"
                    add_route(b, "POST", uri, hs, f, line, "astro", "exact" if hs else "resolved",
                              {"action": path, "accept": accept})
                    if fn:
                        handlers[path] = fn
                    st["actions"] += 1
            break
        return handlers

    def _action_calls(self, b, calls, handlers) -> None:
        for call in calls:
            if call.get("fn") != "action":
                continue
            dst = handlers.get(call.get("path") or "")
            src = call.get("src")
            if not dst or not src or not b.has(src) or not b.has(dst):
                continue
            b.add_edge(src, dst, "CALLS", call.get("file"), call.get("line") or 1, "resolved",
                       via="astro-action", how=call.get("how") or "call")

    def _forms(self, project, b, handlers, src_dir) -> None:
        # pages, layouts and components: a form often lives in a component
        for n in list(b.nodes.values()):
            if n.kind not in ("page", "layout", "component") or not n.file or not n.file.endswith(".astro"):
                continue
            try:
                text = (project.root / n.file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for path, line in _form_actions(text):
                dst = handlers.get(path)
                if dst and b.has(dst):
                    b.add_edge(n.id, dst, "CALLS", n.file, line, "resolved", via="astro-action", how="form")

    def _middleware(self, b, mods, src_dir, st) -> None:
        names = [f"{src_dir}/{n}" if src_dir else n for n in _MW_FILES]
        mod = ex = f = None
        for name in names:
            if name in mods:
                found = next((e for e in (mods[name].get("exports") or []) if e.get("name") == "onRequest"), None)
                if found:
                    f, mod, ex = name, mods[name], found
                    break
        if not ex:
            return
        fns = [(nid, nm) for nid, nm in _middleware_fns(ex) if nid and b.has(nid)]
        if not fns:
            return
        st["middleware"] = len(fns)
        pages = [n.id for n in b.nodes.values() if n.entry_kind == "ui_page" and (n.attrs or {}).get("framework") == "astro"]
        routes = [n.id for n in b.nodes.values() if n.kind == "route" and (n.attrs or {}).get("framework") == "astro"]
        for src in pages + routes:
            for i, (nid, nm) in enumerate(fns):
                fn = b.nodes[nid]   # the function may be imported into the middleware file
                b.add_edge(src, nid, "USES_MIDDLEWARE", fn.file or f, fn.line or 1, "exact", name=nm, order=i)

"""Shared helpers for the TypeScript server-framework layers (NestJS, Next.js, Express/Fastify/Koa/Hono).

All three sit on the TypeScript language plugin. The extractor's `fw` facts (extractor/fw.mjs) describe decorators,
router calls, exports and data-access calls as JSON values; these helpers turn them into graph nodes and edges:

  * route paths in one notation (`{param}`, `{rest*}` catch-all, `{rest*?}` optional catch-all) so `link` can match
    client calls against any backend,
  * route nodes (`route:<METHOD> <uri>`, entry kind http_route) with ROUTES_TO / USES_MIDDLEWARE evidence,
  * the post-pass shared by all TS server frameworks (env/config keys, ORM tables, in-repo client -> route links),
    run once after the last framework contributed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ...core.plugin import GraphBuilder, Project

# package.json dependencies that mark a TS/JS server framework (any of them enables the TS plugin, even without a
# tsconfig.json: plain-JS projects are indexed with allowJs)
SERVER_DEPS = {
    "nest": ("@nestjs/core", "@nestjs/common"),
    "next": ("next",),
    "express": ("express", "koa", "@koa/router", "koa-router", "fastify", "hono", "@hono/node-server", "restify", "polka", "h3", "elysia"),
}
# test trees: anywhere for the unambiguous names; `test/`, `tests/`, `e2e/` only at the top (or under src/), since
# deeper directories with those names are often real route segments (app/api/test/route.ts)
TEST_SKIP_RE = (r"(^|/)(node_modules|\.next|\.nuxt|\.output|\.svelte-kit|\.turbo|\.vercel|dist|build|out|coverage|"
                r"__tests__|__mocks__|__fixtures__|cypress|playwright|storybook-static|\.storybook)(/|$)"
                r"|^(src/)?(test|tests|e2e|spec)(/|$)"
                r"|\.(test|spec|e2e-spec|stories)\.(t|j)sx?$")
HTTP_VERBS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def pkg_deps(root: Path) -> dict:
    p = Path(root) / "package.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text())
    except Exception:
        return {}
    return {**(d.get("dependencies") or {}), **(d.get("devDependencies") or {}), **(d.get("peerDependencies") or {})}


def has_server_framework(project: Project, which: str | None = None) -> bool:
    deps = pkg_deps(project.root)
    names = SERVER_DEPS[which] if which else [d for v in SERVER_DEPS.values() for d in v]
    return any(n in deps for n in names)


def merge_extractor_cfg(cfg: dict, src_dirs=None, skip_re: str | None = None, extra_files=None, allow_js=False, walk_src=False):
    """Several TS framework plugins may configure the one extractor run: union of source dirs, OR of skip regexes."""
    if src_dirs:
        cur = cfg.get("src_dirs") or []
        cfg["src_dirs"] = list(dict.fromkeys([*cur, *src_dirs]))
    if skip_re:
        cfg["skip_re"] = f"(?:{cfg['skip_re']})|(?:{skip_re})" if cfg.get("skip_re") and skip_re not in cfg["skip_re"] else skip_re
    if extra_files:
        cfg["extra_files"] = list(dict.fromkeys([*(cfg.get("extra_files") or []), *extra_files]))
    if allow_js:
        cfg["allow_js"] = True
    if walk_src:
        cfg["walk_src"] = True


# ---------------------------------------------------------------- paths
def join_path(*parts: str | None) -> str:
    segs = []
    for p in parts:
        if not p:
            continue
        segs += [s for s in str(p).split("/") if s]
    return "/" + "/".join(segs)


_COLON = re.compile(r":(\w+)(\([^)]*\))?([?*+])?")


def express_path(p: str) -> str:
    """Express / Koa / Fastify / Hono / NestJS path -> uri template: `:id` -> `{id}`, `:id?` -> `{id?}`,
    `:id(\\d+)` -> `{id}`, catch-alls `:p+` / `*p` -> `{p*}` (one or more segments), `*` / `:p*` / `{*p}` -> `{p*?}`
    (zero or more)."""
    if p is None:
        return ""
    out = []
    for seg in str(p).split("/"):
        if not seg:
            continue
        if seg in ("*", "(.*)", "(.*)*"):
            out.append("{wildcard*?}")
            continue
        m = re.fullmatch(r"\{/?\*(\w+)\}", seg)      # Express 5 optional wildcard {*rest}: zero or more
        if m:
            out.append("{%s*?}" % m.group(1))
            continue
        m = re.fullmatch(r"\*(\w+)", seg)              # Express 5 wildcard *rest: one or more
        if m:
            out.append("{%s*}" % m.group(1))
            continue
        m = re.fullmatch(r"\{/?:(\w+)\}", seg)   # Express 5 optional `{:id}`
        if m:
            out.append("{%s?}" % m.group(1))
            continue

        def sub(m):
            name, mod = m.group(1), m.group(3)
            if mod == "*":
                return "{%s*?}" % name    # path-to-regexp `:p*` zero or more, `:p+` one or more
            if mod == "+":
                return "{%s*}" % name
            return "{%s?}" % name if mod == "?" else "{%s}" % name
        out.append(_COLON.sub(sub, seg))
    return "/" + "/".join(out)


def next_segment(seg: str) -> str | None:
    """Next.js app/pages directory segment -> uri segment ('' = not part of the URL, None = private folder)."""
    if seg.startswith("_"):
        return None
    if re.fullmatch(r"\(.*\)", seg) or seg.startswith("@"):
        return ""      # route group / parallel-route slot
    m = re.fullmatch(r"\[\[\.\.\.(\w+)\]\]", seg)
    if m:
        return "{%s*?}" % m.group(1)
    m = re.fullmatch(r"\[\.\.\.(\w+)\]", seg)
    if m:
        return "{%s*}" % m.group(1)
    return re.sub(r"\[(\w+)\]", r"{\1}", seg)


# ---------------------------------------------------------------- values described by fw.mjs
def sval(d) -> str | None:
    """String value of a described expression ({s}), else None."""
    return d.get("s") if isinstance(d, dict) and isinstance(d.get("s"), str) else None


def svals(d) -> list[str]:
    if not isinstance(d, dict):
        return []
    if isinstance(d.get("s"), str):
        return [d["s"]]
    if d.get("ss"):
        return list(d["ss"])
    if d.get("arr") is not None:
        return [x for e in d["arr"] for x in svals(e)]
    return []


def obj(d) -> dict:
    return d.get("obj") or {} if isinstance(d, dict) else {}


def last_name(name: str | None) -> str:
    return (name or "").split(".")[-1].split("(")[0]


def ref_nodes(d, depth=0) -> list[str]:
    """Code nodes a described value points at (function/method/class), through wrappers and handler objects."""
    if not isinstance(d, dict) or depth > 4:
        return []
    if d.get("fn"):
        return [d["fn"]]
    if d.get("node") and d.get("ref") is not None:
        return [d["node"]]
    if d.get("obj_fns"):
        return list(d["obj_fns"])
    if "call" in d:
        out = []
        if last_name(d["call"]) == "bind" and d.get("recv"):
            return ref_nodes(d["recv"], depth + 1)
        for a in d.get("args") or []:
            out += ref_nodes(a, depth + 1)
        return out
    return []


def snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    s = re.sub(r"(?<=[A-Z])([A-Z][a-z])", r"_\1", s)
    return s.lower()


def plural(word: str) -> str:
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    return word + "s"


def module_of(path: str | None) -> str | None:
    from ..ts.plugin import module_of as m
    return m(path)


# ---------------------------------------------------------------- routes
def add_route(b: GraphBuilder, method: str, uri: str, handlers: list[str], file: str, line: int, framework: str,
              confidence: str = "exact", attrs: dict | None = None, middleware: list[tuple[str, str, str]] | None = None) -> str:
    """route:<METHOD> <uri>, entry kind http_route; ROUTES_TO each handler; USES_MIDDLEWARE (node, name, conf)."""
    method = method.upper()
    key = f"{method} {uri}"
    a = {"uri": uri, "method": method, "framework": framework, **(attrs or {})}
    if middleware:
        a["middleware"] = list(dict.fromkeys([m[1] for m in middleware if m[1]]))
    rid = b.add_node("route", key, name=key, file=file, line=line, module=module_of(file), lang="ts", entry_kind="http_route", attrs=a)
    n = b.nodes[rid]
    if n.entry_kind is None:
        n.entry_kind = "http_route"
    for h in handlers:
        if h and b.has(h):
            b.add_edge(rid, h, "ROUTES_TO", file=file, line=line, confidence=confidence)
            hn = b.nodes[h]
            if hn.file and not n.attrs.get("handler"):
                n.attrs["handler"] = hn.name
                n.module = module_of(hn.file)
    for node, name, conf in middleware or []:
        if node and b.has(node):
            b.add_edge(rid, node, "USES_MIDDLEWARE", file=file, line=line, confidence=conf, name=name)
    return rid


def route_specs(b: GraphBuilder) -> list[dict]:
    """Route nodes in the shape link.match_endpoint expects."""
    out = []
    for n in b.nodes.values():
        if n.kind != "route" or n.lang != "ts":
            continue
        uri, method = n.attrs.get("uri"), n.attrs.get("method")
        if not uri or not method:
            continue
        uris = [("as-declared", uri)] + [(f"variant:{v}", v) for v in n.attrs.get("uri_variants") or []]
        out.append({"id": n.id, "uri": uri, "method": method, "uris": uris, "file": n.file, "line": n.line})
    return out


def link_in_repo(b: GraphBuilder, ctx) -> dict:
    """Client HTTP calls in the same repo (Next.js pages/components, a bundled SPA) -> this repo's routes, with the
    same deterministic matcher `cg link` uses across repos. Same-origin relative URLs are in scope here."""
    from ...link import match_endpoint
    from ...core.model import CONFIDENCE_RANK
    routes = route_specs(b)
    st = {"endpoints": 0, "matched": 0}
    if not routes:
        return st
    rmap = {r["id"]: r for r in routes}
    for n in list(b.nodes.values()):
        if n.kind != "http" or n.lang != "ts":
            continue
        a = n.attrs
        ok = a.get("origin_kind", "api")
        if ok == "other":
            continue
        st["endpoints"] += 1
        res = match_endpoint(a.get("method", "GET"), a.get("path", ""), routes, "api" if ok == "same-origin" else ok, a.get("origin"))
        for m in res["matched"]:
            r = rmap[m["route"]]
            b.add_edge(n.id, m["route"], "MATCHES_ROUTE", file=r["file"], line=r["line"], confidence=m["confidence"],
                       client_path=a.get("path"), uri_variant=m["uri_variant"], segments=m["segments"], in_repo=True)
        if res["matched"]:
            st["matched"] += 1
    return st


def finish(project: Project, b: GraphBuilder, ctx, name: str) -> dict:
    """Called at the end of each TS server framework's contribute(); the shared post-pass runs once, after the last."""
    pending = getattr(ctx, "tsweb_pending", None)
    if pending is None:
        return {}
    pending.discard(name)
    if pending:
        return {}
    from .data import contribute_data
    st = contribute_data(project, b, ctx)
    st["in_repo_links"] = link_in_repo(b, ctx)
    return {"shared": st}


def register(ctx, name: str) -> None:
    if not hasattr(ctx, "tsweb_pending"):
        ctx.tsweb_pending = set()
    ctx.tsweb_pending.add(name)


def fw_facts(ctx) -> dict:
    return (ctx.facts or {}).get("fw") or {} if ctx else {}

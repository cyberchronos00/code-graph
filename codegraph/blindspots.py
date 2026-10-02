"""Blind-spot detectors: patterns cg knows it does not model, found in the indexed repository at index time.

Each detector reports the places where a route or handler is registered in a way no plugin turns into graph edges
(a route decorator wrapped in a helper, URL patterns built by a function, routes registered in a loop, handlers
registered through a decorator or a registry). Query answers use them to say when a route list or a caller list may
be partial, and where to look (file:line samples), so an agent can fall back to text search exactly there.

A finding: {kind, category ("route" = a route registration, "handler" = a function reached through registration),
language (coverage language key), count, sample ("file:line"), samples [...], what (short description)}; handler
findings also carry `nodes` (graph ids of the registered functions, aligned with `samples`) so an answer is flagged
only when it involves one of them.
"""
from __future__ import annotations

import ast
import os
import re
from collections import defaultdict
from pathlib import Path

MAX_SAMPLES = 10
MAX_NODES = 2000
MAX_BYTES = 1_500_000

KINDS = {
    "nest_wrapped_route_decorator": ("route", "typescript", "NestJS route decorator wrapped by applyDecorators / a decorator factory"),
    "django_dynamic_urlpatterns": ("route", "python", "Django urlpatterns built by a function call, comprehension or loop"),
    "django_unresolved_include": ("route", "python", "Django include() of a target cg could not resolve"),
    "express_loop_routes": ("route", "typescript", "Express / Fastify / Koa / Hono routes registered in a loop or with a computed method"),
    "laravel_loop_routes": ("route", "php", "Laravel Route:: calls inside a loop or a collection callback"),
    "python_decorator_routes": ("route", "python", "route decorator of a framework cg has no plugin for"),
    "python_decorator_registration": ("handler", "python", "function registered through a decorator no plugin models"),
    "python_registry_assignment": ("handler", "python", "function stored in a registry (registry[key] = fn)"),
}


def _finding(kind: str, hits: list[tuple]) -> dict | None:
    """hits: (file, line) or (file, line, node_id) tuples."""
    if not hits:
        return None
    hits = sorted(set(hits))
    cat, lang, what = KINDS[kind]
    s = [f"{h[0]}:{h[1]}" for h in hits]
    out = {"kind": kind, "category": cat, "language": lang, "count": len(hits), "sample": s[0], "samples": s[:MAX_SAMPLES],
           "what": what}
    if hits and all(len(h) > 2 for h in hits):
        out["nodes"] = [h[2] for h in hits][:MAX_NODES]
        out["node_samples"] = s[:MAX_NODES]
    return out


def _read(root: Path, rel: str) -> str | None:
    p = root / rel
    try:
        if p.stat().st_size > MAX_BYTES:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _line(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


_C_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\"'`\\])//[^\n]*", re.S)


def _strip_comments(src: str) -> str:
    """Blank out /* */ and // comments, keeping offsets (so line numbers stay right)."""
    return _C_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), src)


def _match(src: str, i: int, open_: str, close: str) -> int:
    """Index of the bracket closing the one at src[i] (or len(src)); string literals are skipped."""
    depth, n, q = 0, len(src), None
    while i < n:
        c = src[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'`":
            q = c
        elif c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _loop_bodies(src: str, heads: re.Pattern) -> list[tuple[int, int, int]]:
    """(start, body_start, body_end) of each loop: `for (...) {...}` / `foreach (...) {...}` / `.forEach(...)` style
    callbacks (heads must end at the opening parenthesis). Statement-bodied loops run to the next `;`."""
    out = []
    for m in heads.finditer(src):
        p = m.end() - 1
        close = _match(src, p, "(", ")")
        if m.group(0).lstrip(".").startswith(("for", "foreach", "while")) and not m.group(0).startswith("."):
            j = close + 1
            while j < len(src) and src[j] in " \t\r\n":
                j += 1
            if j < len(src) and src[j] == "{":
                out.append((m.start(), j, _match(src, j, "{", "}")))
            else:
                out.append((m.start(), j, src.find(";", j) if src.find(";", j) >= 0 else len(src)))
        else:
            out.append((m.start(), p, close))
    return out


# ------------------------------------------------------------------------------------------- NestJS

NEST_VERBS = r"(?:Get|Post|Put|Patch|Delete|All|Options|Head|Search)"
_NEST_DEF = re.compile(r"(?:export\s+)?(?:function\s+(\w+)\s*(?:<[^>]*>)?\s*\(|(?:const|let|var)\s+(\w+)\s*=)")


def nest_wrapped_route_decorators(root: Path, files: list[str]) -> dict | None:
    """Decorators defined as applyDecorators(Get(...), ...) or as a factory returning Get(...): the Nest plugin
    only reads the built-in route decorators, so every method using such a decorator is a route cg does not see."""
    defs: dict[str, tuple[str, int]] = {}
    texts = {}
    for rel in files:
        src = _read(root, rel)
        if not src or "@" not in src:
            continue
        src = _strip_comments(src)
        texts[rel] = src   # decorator uses can be in any file
        if "@nestjs/" not in src and "applyDecorators" not in src:
            continue
        for m in _NEST_DEF.finditer(src):
            name = m.group(1) or m.group(2)
            if not name or not name[0].isupper():
                continue  # decorators are PascalCase by convention; keeps local helpers out
            head_end = m.end()
            # body of the function / initializer: up to the end of the matching block or statement
            if m.group(1):
                p = _match(src, head_end - 1, "(", ")")
                b = src.find("{", p)
                body = src[b:_match(src, b, "{", "}") + 1] if b >= 0 else ""
            else:
                semi = src.find(";", head_end)
                nxt = re.search(r"\n(?:export\s+)?(?:const|let|var|function|class)\s", src[head_end:])
                end = head_end + nxt.start() if nxt else (semi if semi >= 0 else len(src))
                body = src[head_end:end]
            if re.search(rf"\bapplyDecorators\s*\([^;]*?\b{NEST_VERBS}\s*\(", body, re.S) or \
                    re.search(rf"(?:\breturn\s+|=>\s*){NEST_VERBS}\s*\(", body):
                defs.setdefault(name, (rel, _line(src, m.start())))
    if not defs:
        return None
    use = re.compile(r"@(" + "|".join(map(re.escape, defs)) + r")\s*\(")
    hits = []
    for rel, src in texts.items():
        hits += [(rel, _line(src, m.start())) for m in use.finditer(src)]
    return _finding("nest_wrapped_route_decorator", hits or list(defs.values()))


# ------------------------------------------------------------------------------------------- Express-style

_JS_ROUTER_DECL = re.compile(r"(?:const|let|var)\s+(\w+)\s*(?::\s*[\w.<>]+\s*)?=\s*(?:await\s+)?(?:new\s+)?"
                             r"(?:express(?:\.Router)?|Router|KoaRouter|Koa|Fastify|fastify|Hono|createRouter)\s*\(")
_JS_LOOPS = re.compile(r"\bfor\s*(?:await\s*)?\(|\.\s*(?:forEach|map|flatMap)\s*\(")
JS_ROUTER_NAMES = {"app", "router", "server", "fastify"}
_JS_SERVER_IMPORT = re.compile(r"""(?:from\s+|require\s*\(\s*|import\s*\(\s*)['"](?:express|fastify|koa|@koa/router|koa-router|hono|@hono/[\w-]+)['"]""")
JS_SERVER_DEPS = ("express", "fastify", "koa", "@koa/router", "koa-router", "hono")


def _server_project(root: Path) -> bool:
    """package.json depends on an Express-style server framework (then route modules that only receive `app` /
    `router` as a parameter are checked too)."""
    import json
    try:
        d = json.loads((root / "package.json").read_text())
    except Exception:  # noqa: BLE001
        return False
    deps = {**(d.get("dependencies") or {}), **(d.get("devDependencies") or {})}
    return any(k in deps for k in JS_SERVER_DEPS)
JS_VERBS = r"(?:get|post|put|patch|delete|del|all|head|options|route|use|register)"


def express_loop_routes(root: Path, files: list[str]) -> dict | None:
    """Routes registered inside a loop or callback over a list (`for (const r of routes) router[r.method](r.path, ...)`,
    `['get', 'post'].forEach(...)`): Express-style plugins read literal registrations only."""
    hits = []
    project = _server_project(root)
    for rel in files:
        src = _read(root, rel)
        if not src or not (_JS_SERVER_IMPORT.search(src) or (project and re.search(r"\b(?:app|router|server|fastify)\b", src))):
            continue
        src = _strip_comments(src)
        names = JS_ROUTER_NAMES | {m.group(1) for m in _JS_ROUTER_DECL.finditer(src)}
        reg = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(names))) + rf")\s*(?:\.\s*{JS_VERBS}\s*\(|\[\s*[^\]\n]+\]\s*\()")
        done_until = -1
        for start, b0, b1 in _loop_bodies(src, _JS_LOOPS):
            if start < done_until:
                continue  # nested loop of a loop already reported
            if reg.search(src, b0, b1):
                hits.append((rel, _line(src, start)))
                done_until = b1
        # computed method outside a loop: router[method](path, handler)
        creg = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(names))) + r")\s*\[\s*(?!['\"`])[^\]\n]+\]\s*\(")
        for m in creg.finditer(src):
            ln = _line(src, m.start())
            if not any(f == rel and abs(l0 - ln) <= 3 for f, l0 in hits):
                hits.append((rel, ln))
    return _finding("express_loop_routes", hits)


# ------------------------------------------------------------------------------------------- Laravel

_PHP_LOOPS = re.compile(r"\b(?:foreach|for|while)\s*\(|->\s*(?:each|map|flatMap|eachSpread)\s*\(|\barray_(?:map|walk)\s*\(")


def laravel_loop_routes(root: Path, files: list[str]) -> dict | None:
    """Route::get(...) inside foreach / for / ->each(...) / array_map(...): the URIs come from data, so the Laravel
    plugin cannot list them."""
    hits = []
    for rel in files:
        src = _read(root, rel)
        if not src or "Route::" not in src:
            continue
        src = re.sub(r"#[^\n]*", lambda m: " " * len(m.group(0)), _strip_comments(src))
        done_until = -1
        for start, b0, b1 in _loop_bodies(src, _PHP_LOOPS):
            if start < done_until:
                continue
            if re.search(r"\bRoute::\s*(?:get|post|put|patch|delete|any|match|resource|apiResource|resources|apiResources|view|redirect)\s*\(",
                         src[b0:b1]):
                hits.append((rel, _line(src, start)))
                done_until = b1
    return _finding("laravel_loop_routes", hits)


# ------------------------------------------------------------------------------------------- Django (AST)

URL_FUNCS = {"path", "re_path", "url", "include", "static", "staticfiles_urlpatterns", "debug_toolbar_urls",
             "format_suffix_patterns", "i18n_patterns", "list"}


def _dotted(e) -> str | None:
    if isinstance(e, ast.Name):
        return e.id
    if isinstance(e, ast.Attribute):
        b = _dotted(e.value)
        return f"{b}.{e.attr}" if b else None
    return None


def _dynamic_items(e) -> list[ast.AST]:
    """Parts of a urlpatterns value that Django URL resolution in cg does not evaluate: calls to functions other
    than the URL helpers (also as `*call(...)`), comprehensions."""
    out = []
    if isinstance(e, (ast.List, ast.Tuple)):
        for x in e.elts:
            out += _dynamic_items(x.value if isinstance(x, ast.Starred) else x)
    elif isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
        out += _dynamic_items(e.left) + _dynamic_items(e.right)
    elif isinstance(e, (ast.ListComp, ast.GeneratorExp)):
        out.append(e)
    elif isinstance(e, ast.Call):
        fn = (_dotted(e.func) or "").split(".")[-1]
        if fn in ("format_suffix_patterns", "i18n_patterns", "list"):
            for a in e.args:
                out += _dynamic_items(a.value if isinstance(a, ast.Starred) else a)
        elif fn not in URL_FUNCS and not (isinstance(e.func, ast.Attribute) and e.func.attr in ("as_view", "as_asgi")):
            out.append(e)
    return out


def django_dynamic_urlpatterns(modules) -> dict | None:
    """modules: [(file, tree)]. urlpatterns entries produced by a function call, a comprehension or a loop."""
    hits = []
    for rel, tree in modules:
        def visit(stmts, in_loop=False):
            for st in stmts:
                tgt_is = lambda t: isinstance(t, ast.Name) and t.id == "urlpatterns"  # noqa: E731
                if isinstance(st, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    tgts = st.targets if isinstance(st, ast.Assign) else [st.target]
                    if any(tgt_is(t) for t in tgts) and st.value is not None:
                        if in_loop:
                            hits.append((rel, st.lineno))
                            continue
                        for x in _dynamic_items(st.value):
                            if any(isinstance(n, ast.Name) and n.id == "urlpatterns" for n in ast.walk(x)):
                                continue  # urlpatterns = wrap(urlpatterns): a pass-through, not new routes
                            hits.append((rel, x.lineno))
                elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and isinstance(st.value.func, ast.Attribute) \
                        and tgt_is(st.value.func.value) and st.value.func.attr in ("append", "extend", "insert"):
                    if in_loop:
                        hits.append((rel, st.lineno))
                    elif st.value.func.attr == "extend" and st.value.args:
                        hits.extend((rel, x.lineno) for x in _dynamic_items(st.value.args[0]))
                    elif st.value.args:
                        hits.extend((rel, x.lineno) for x in _dynamic_items(ast.List(elts=[st.value.args[-1]])))
                elif isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
                    visit(st.body, True)
                    visit(st.orelse, in_loop)
                elif isinstance(st, ast.If):
                    visit(st.body, in_loop)
                    visit(st.orelse, in_loop)
                elif isinstance(st, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                    visit(st.body, in_loop)
                    for h in st.handlers:
                        visit(h.body, in_loop)
                    visit(st.orelse, in_loop)
                    visit(st.finalbody, in_loop)
                elif isinstance(st, ast.With):
                    visit(st.body, in_loop)
        visit(tree.body)
    return _finding("django_dynamic_urlpatterns", hits)


def django_unresolved_includes(unresolved: list[dict]) -> dict | None:
    """include(<non-literal>) / unknown `.urls` targets the Django URL resolver could not follow. A literal include of a
    package outside the repository (`include("allauth.urls")`) is third-party code, not a blind spot of this repo."""
    hits = [(u["file"], u["line"]) for u in unresolved or []
            if str(u.get("reason", "")).endswith(") unresolved") or str(u.get("reason", "")).startswith("unknown .urls")]
    return _finding("django_unresolved_include", hits)


# ------------------------------------------------------------------------------------------- Python registrations

ROUTE_DECOS = {"route", "get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket", "add_route"}
REGISTER_DECOS = {"register", "command", "group", "task", "shared_task", "hook", "hookimpl", "handler", "on", "listener",
                  "subscriber", "subscribe", "callback", "event", "job", "signal", "receiver", "connect", "action",
                  "plugin", "provider", "expose", "export", "tool", "resource", "prompt", "cli"}


def _deco_name(text: str) -> tuple[str, bool]:
    """'@app.route("/x")' text -> ('route', has_receiver)."""
    head = text.split("(", 1)[0].strip().lstrip("@")
    parts = head.split(".")
    if "mock" in parts or parts[0] == "patch":
        return "", False  # unittest.mock.patch / patch.object: test doubles, not registrations
    return parts[-1], len(parts) > 1


def python_registrations(builder) -> list[dict]:
    """Python functions registered through a decorator (`@app.route`, `@registry.register`, `@click.command`) that no
    plugin turned into an entry point or an incoming edge: they look unused / uncalled in the graph."""
    incoming = set()
    for e in builder.edges.values():
        if e.kind != "CONTAINS":
            incoming.add(e.dst)
    routes, regs, decos = [], [], {}
    local_fns = {(n.file, n.name): n.id for n in builder.nodes.values() if n.lang == "python" and n.kind == "function"}
    for n in builder.nodes.values():
        if n.lang != "python" or n.kind not in ("function", "method") or n.entry_kind or n.id in incoming:
            continue
        for d in (n.attrs or {}).get("decorators") or []:
            name, recv = _deco_name(str(d))
            if recv and name in ROUTE_DECOS:
                routes.append((n.file, n.line, n.id))
                break
            if name in REGISTER_DECOS or (recv and name.startswith("register")):
                regs.append((n.file, n.line, n.id))
                if not recv and (n.file, name) in local_fns:  # a same-file decorator: its uses are not calls either
                    decos.setdefault(local_fns[(n.file, name)], f"{n.file}:{n.line}")
                break
    reg = _finding("python_decorator_registration", regs)
    if reg and decos and "nodes" in reg:
        reg["nodes"] += list(decos)
        reg["node_samples"] += list(decos.values())
    return [f for f in (_finding("python_decorator_routes", routes), reg) if f]


def python_registry_assignments(prog, builder) -> dict | None:
    """`registry[key] = fn` (module level or inside a function) where fn is a function of the same module that
    has no caller in the graph: dispatch through the registry is not followed."""
    incoming = set()
    for e in builder.edges.values():
        if e.kind != "CONTAINS":
            incoming.add(e.dst)
    hits = []
    for m in prog.modules.values():
        local = {f.name: f for f in prog.funcs.values() if f.module is m and f.cls is None}
        if not local:
            continue
        for st in ast.walk(m.tree):
            if isinstance(st, ast.Assign) and isinstance(st.value, ast.Name) and st.value.id in local \
                    and any(isinstance(t, ast.Subscript) for t in st.targets):
                f = local[st.value.id]
                if f.id not in incoming and not (builder.nodes.get(f.id) and builder.nodes[f.id].entry_kind):
                    hits.append((m.file, st.lineno, f.id))
    return _finding("python_registry_assignment", hits)


# ------------------------------------------------------------------------------------------- driver

def detect(root: str | Path, files_by_ext: dict[str, list[str]], builder=None, programs: dict | None = None) -> list[dict]:
    """Run every detector that applies; never raises (a detector that fails is skipped)."""
    root = Path(root)
    programs = programs or {}
    js = [f for e in (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs") for f in files_by_ext.get(e, [])]
    js = [f for f in js if not f.endswith(".d.ts")]
    php = files_by_ext.get(".php", [])
    out = []

    def run(fn, *a):
        try:
            r = fn(*a)
        except Exception as ex:  # noqa: BLE001  (a detector bug must never fail the index)
            import sys
            print(f"codegraph: blind-spot detector {fn.__name__} failed: {type(ex).__name__}: {ex}", file=sys.stderr)
            return
        if isinstance(r, list):
            out.extend(r)
        elif r:
            out.append(r)

    run(nest_wrapped_route_decorators, root, js)
    run(express_loop_routes, root, js)
    run(laravel_loop_routes, root, php)
    py = programs.get("python")
    if py is not None:
        run(django_dynamic_urlpatterns, [(m.file, m.tree) for m in py.modules.values()])
        run(django_unresolved_includes, getattr(py, "url_unresolved", None))
        if builder is not None:
            run(python_registrations, builder)
            run(python_registry_assignments, py, builder)
    return out

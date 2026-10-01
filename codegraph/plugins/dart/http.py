"""HTTP client calls in Dart: package:http, Dio (+ BaseOptions.baseUrl), Retrofit / Chopper
annotations, dart:io HttpClient and WebSocket channels.

URLs are evaluated to templates: string interpolation becomes a {name} placeholder; constants,
static fields, getters, local variables, `Uri.parse/https/http`, `dotenv.env['X']` /
`String.fromEnvironment('X')` (resolved through .env files when present) and small URL-building
helpers are followed. A URL that depends on a parameter of the enclosing function marks a helper:
it is expanded at its call sites (up to three levels), like the TS plugin does.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ...core.model import EXACT, HEURISTIC, RESOLVED, CONFIDENCE_RANK
from .program import Ctx, DartProgram, DClass, DFunc, DVar, ctor_type, last_name, root_name, split_type, walk_repr

HTTP_PKG_METHODS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "head": "HEAD",
                    "read": "GET", "readBytes": "GET"}
DIO_METHODS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "head": "HEAD",
               "getUri": "GET", "postUri": "POST", "putUri": "PUT", "patchUri": "PATCH", "deleteUri": "DELETE",
               "download": "GET", "request": None, "requestUri": None}
IO_METHODS = {"getUrl": "GET", "postUrl": "POST", "putUrl": "PUT", "patchUrl": "PATCH", "deleteUrl": "DELETE", "openUrl": None}
IO_HOST_METHODS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "head": "HEAD", "open": None}
HTTP_CLIENT_TYPES = {"Client", "BaseClient", "IOClient", "RetryClient", "BrowserClient", "MockClient", "InterceptedClient"}
DIO_TYPES = {"Dio", "DioMixin", "DioForNative", "DioForBrowser"}
WS_TYPES = {"WebSocketChannel", "IOWebSocketChannel", "HtmlWebSocketChannel", "WebSocket"}
RETROFIT_VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
CHOPPER_VERBS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "Head": "HEAD"}
TOKEN = re.compile(r"\{@param:(\w+)\}")
MAX_CTX = 40
HELPER_PARAM = re.compile(r"(?i)(path|url|uri|endpoint|route|resource|segment|base|prefix|api)s?$")


def min_conf(*cs):
    return min((c for c in cs if c), key=lambda c: CONFIDENCE_RANK[c])


@dataclass
class Tpl:
    text: str = ""
    conf: str = EXACT
    named: bool = False      # leftmost part came through a named constant/variable/getter
    env: list = field(default_factory=list)
    unknown: bool = False

    def __add__(self, o: "Tpl") -> "Tpl":
        return Tpl(self.text + o.text, min_conf(self.conf, o.conf), self.named if self.text else o.named,
                   self.env + o.env, self.unknown or o.unknown)


def load_env_files(root: Path, pkg_dirs: list[str]) -> dict[str, tuple[str, str]]:
    """KEY -> (value, file) from .env files of the Flutter packages (first definition wins; .env before .env.*)."""
    out: dict[str, tuple[str, str]] = {}
    for d in pkg_dirs:
        base = root / d if d else root
        cands = sorted(set(list(base.glob(".env")) + list(base.glob(".env*")) + list(base.glob("assets/.env*")) + list(base.glob("env/.env*"))),
                       key=lambda p: (p.name != ".env", p.name.endswith((".example", ".sample", ".template")), str(p)))
        for p in cands:
            if not p.is_file() or p.stat().st_size > 200_000:
                continue
            try:
                txt = p.read_text(errors="replace")
            except OSError:
                continue
            for line in txt.splitlines():
                m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
                if m:
                    v = m.group(2).strip()
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                        v = v[1:-1]
                    else:
                        v = v.split(" #")[0].strip()
                    out.setdefault(m.group(1), (v, str(p.relative_to(root))))
    return out


class UrlEval:
    def __init__(self, prog: DartProgram, env_values: dict):
        self.prog = prog
        self.env_values = env_values
        self.stack: set = set()

    def ph(self, r, conf=EXACT) -> Tpl:
        n = last_name(r) or "?"
        return Tpl("{" + n + "}", conf, unknown=False)

    def eval(self, r, ctx: Ctx, depth=0) -> Tpl:
        if not isinstance(r, dict) or depth > 14:
            return Tpl("{?}", HEURISTIC, unknown=True)
        k = r.get("k")
        if k == "str":
            return Tpl(r["v"])
        if k in ("num", "bool"):
            return Tpl(str(r["v"]).lower() if k == "bool" else str(r["v"]))
        if k in ("tpl",):
            out = Tpl()
            for p in r["parts"]:
                if isinstance(p, str):
                    out = out + Tpl(p)
                elif isinstance(p, dict) and "e" in p:
                    out = out + self.interp(p["e"], ctx, depth)
                elif isinstance(p, dict) and p.get("k") == "str":
                    out = out + Tpl(p["v"])
            return out
        if k == "cat":
            out = Tpl()
            for p in r["parts"]:
                out = out + self.eval(p, ctx, depth + 1)
            return out
        if k == "bin" and r.get("op") == "+":
            return self.interp(r["l"], ctx, depth) + self.interp(r["r"], ctx, depth)
        if k == "bin" and r.get("op") == "??":
            t = self.eval(r["l"], ctx, depth + 1)
            return t if not t.unknown and "{" not in t.text[:1] else self.eval(r["r"], ctx, depth + 1)
        if k in ("nn", "as", "await"):
            return self.eval(r["e"], ctx, depth + 1)
        if k == "cond":
            t = self.eval(r["a"], ctx, depth + 1)
            t.conf = min_conf(t.conf, HEURISTIC)
            return t
        if k == "id":
            return self.name(r["v"], r, ctx, depth)
        if k == "prop":
            return self.prop(r, ctx, depth)
        if k == "idx":
            src = self.env_key(r)
            if src:
                return self.env(src)
            return self.ph(r)
        if k == "call" and ctor_type(r) != "Uri":
            return self.call(r, ctx, depth)
        if k == "new" or (k == "call" and ctor_type(r) == "Uri"):
            base = (ctor_type(r) or "").split(".")[-1]
            if base == "Uri":
                na = r.get("na") or {}
                out = Tpl()
                if "scheme" in na and "host" in na:
                    out = self.eval(na["scheme"], ctx, depth + 1) + Tpl("://") + self.eval(na["host"], ctx, depth + 1)
                    if "port" in na:
                        out = out + Tpl(":") + self.interp(na["port"], ctx, depth + 1)
                if "path" in na:
                    p = self.eval(na["path"], ctx, depth + 1)
                    out = out + (p if p.text.startswith("/") or not out.text else Tpl("/") + p)
                return out if out.text else Tpl("{?}", HEURISTIC, unknown=True)
            return Tpl("{?}", HEURISTIC, unknown=True)
        return Tpl("{?}", HEURISTIC, unknown=True)

    def interp(self, e, ctx: Ctx, depth) -> Tpl:
        """Value used inside a template: a resolved string is inlined, anything else is a {name} placeholder."""
        t = self.eval(e, ctx, depth + 1)
        m = TOKEN.fullmatch(t.text)
        if m and not HELPER_PARAM.search(m.group(1)):
            return Tpl("{" + m.group(1) + "}", t.conf)  # `/items/$id/`: a path parameter, not a URL-building helper
        if t.unknown or (t.text.startswith("{") and t.text.endswith("}") and t.text.count("{") == 1 and not t.text.startswith(("{env:", "{@param:"))):
            return self.ph(e)
        return t

    def env_key(self, r) -> str | None:
        if r.get("k") == "idx" and (r.get("i") or {}).get("k") == "str":
            t = r.get("t") or {}
            if t.get("k") == "prop" and t.get("n") in ("env", "environment"):
                return r["i"]["v"]
        return None

    def env(self, key: str) -> Tpl:
        if key in self.env_values:
            v = self.env_values[key][0]
            return Tpl(v, RESOLVED, named=True, env=[key])
        return Tpl("{env:" + key + "}", EXACT, named=True, env=[key])

    def name(self, n: str, r, ctx: Ctx, depth) -> Tpl:
        if n in ctx.bindings:
            br, bctx = ctx.bindings[n]
            if br is None:
                return Tpl("{" + n + "}")
            t = self.eval(br, bctx, depth + 1)
            return t
        loc = ctx.locals.get(n)
        if loc:
            kind, d = loc
            if kind == "param":
                return Tpl("{@param:" + n + "}")
            init = d.get("init")
            if init is None and ctx.fn:
                for f in ctx.fn.facts:
                    if f["ft"] == "assign" and (f.get("lhs") or {}).get("k") == "id" and f["lhs"]["v"] == n:
                        init = f["rhs"]
                        break
            if init is not None:
                key = ("loc", id(ctx), n)
                if key in self.stack:
                    return self.ph(r)
                self.stack.add(key)
                try:
                    t = self.eval(init, ctx, depth + 1)
                finally:
                    self.stack.discard(key)
                return t
            return self.ph(r)
        if ctx.cls:
            m, _ = self.prog.find_member(ctx.cls, n)
            if m is not None:
                return self.member_value(m, r, depth)
        v = self.prog.lookup(ctx.lib, n)
        if v is not None:
            return self.member_value(v, r, depth)
        return self.ph(r)

    def member_value(self, m, r, depth) -> Tpl:
        key = ("m", id(m))
        if key in self.stack:
            return self.ph(r)
        self.stack.add(key)
        try:
            if isinstance(m, DVar):
                if m.init is not None:
                    t = self.eval(m.init, Ctx(self.prog, m.lib, None, m.cls), depth + 1)
                    t.named = True
                    return t
                # field set from a constructor parameter with a default / initializer list
                if m.cls:
                    for ct in m.cls.ctors.values():
                        for p in ct.params:
                            if p["name"] == m.name and p.get("this") and p.get("default") is not None:
                                t = self.eval(p["default"], Ctx(self.prog, m.lib, ct, m.cls), depth + 1)
                                t.named, t.conf = True, min_conf(t.conf, RESOLVED)
                                return t
                        for ini in ct.raw.get("inits") or []:
                            if ini.get("field") == m.name:
                                t = self.eval(ini["v"], Ctx(self.prog, m.lib, ct, m.cls), depth + 1)
                                if TOKEN.search(t.text):
                                    continue
                                t.named, t.conf = True, min_conf(t.conf, RESOLVED)
                                return t
                    v = self.from_construction_sites(m, depth)
                    if v is not None:
                        return v
                return self.ph(r)
            if isinstance(m, DFunc) and m.kind == "getter":
                for f in m.facts:
                    if f["ft"] == "return":
                        t = self.eval(f["v"], Ctx(self.prog, m.lib, m, m.cls), depth + 1)
                        t.named = True
                        return t
            return self.ph(r)
        finally:
            self.stack.discard(key)

    def from_construction_sites(self, m: DVar, depth) -> Tpl | None:
        """Field initialised by a constructor parameter (`this.baseUrl`): value passed at the construction sites."""
        vals = {}
        for ct in m.cls.ctors.values():
            pos = [p for p in ct.params if not p.get("named")]
            for i, p in enumerate(ct.params):
                if p["name"] != m.name or not (p.get("this") or any(ini.get("field") == m.name for ini in ct.raw.get("inits") or [])):
                    continue
                for caller, fact, cctx in self.prog.callers.get(ct.id, [])[:30]:
                    arg = (fact.get("na") or {}).get(p["name"]) if p.get("named") else (
                        (fact.get("a") or [])[pos.index(p)] if p in pos and pos.index(p) < len(fact.get("a") or []) else None)
                    if arg is None:
                        continue
                    t = self.eval(arg, cctx, depth + 1)
                    if t.unknown or TOKEN.search(t.text):
                        continue
                    vals.setdefault(t.text, (t, f"{caller.file if caller else '?'}:{fact.get('l')}"))
        if not vals:
            return None
        t, at = next(iter(vals.values()))
        t.named = True
        t.conf = min_conf(t.conf, RESOLVED if len(vals) == 1 else HEURISTIC)
        return t

    def prop(self, r, ctx: Ctx, depth) -> Tpl:
        t, n = r["t"], r["n"]
        if n in ("path", "toString") and self.prog.infer(t, ctx) == ("einst", "Uri", []):
            return self.eval(t, ctx, depth + 1)
        if t.get("k") == "id":
            tv = t["v"]
            if tv not in ctx.locals and tv not in ctx.bindings:
                if self.prog.prefix_imports(ctx.lib, tv):
                    v = self.prog.lookup_prefixed(ctx.lib, tv, n)
                    return self.member_value(v, r, depth) if v is not None else self.ph(r)
                c = self.prog.resolve_class(ctx.lib, tv)
                if c is None and ctx.cls:
                    c = None
                if c is not None:
                    m, _ = self.prog.find_member(c, n)
                    return self.member_value(m, r, depth) if m is not None else self.ph(r)
        if t.get("k") == "this" and ctx.cls:
            m, _ = self.prog.find_member(ctx.cls, n)
            return self.member_value(m, r, depth) if m is not None else self.ph(r)
        tt = self.prog.infer(t, ctx)
        if tt and tt[0] == "inst" and tt[1] is not None:
            m, _ = self.prog.find_member(tt[1], n)
            if m is not None:
                v = self.member_value(m, r, depth)
                v.conf = min_conf(v.conf, RESOLVED)
                return v
        return self.ph(r)

    def call(self, r, ctx: Ctx, depth) -> Tpl:
        n, t = r.get("n"), r.get("t")
        a, na = r.get("a") or [], r.get("na") or {}
        rn = root_name(t) if t else None
        if n in ("parse", "tryParse") and rn == "Uri" and a:
            return self.eval(a[0], ctx, depth + 1)
        if n in ("https", "http") and rn == "Uri" and a:
            out = Tpl(n + "://") + self.eval(a[0], ctx, depth + 1)
            if len(a) > 1:
                p = self.eval(a[1], ctx, depth + 1)
                out = out + (p if p.text.startswith("/") else Tpl("/") + p)
            return out
        if n in ("toString", "trim", "toLowerCase") and t:
            return self.eval(t, ctx, depth + 1)
        if n == "fromEnvironment" and a and a[0].get("k") == "str":
            return self.env(a[0]["v"])
        if n in ("get", "maybeGet", "getOrElse") and t and (rn or "").lower() in ("dotenv", "env") and a and a[0].get("k") == "str":
            return self.env(a[0]["v"])
        if n == "replace" and t and "path" in na:
            base = self.eval(t, ctx, depth + 1)
            m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*://[^/]*)", base.text)
            return Tpl(m.group(1) if m else "", base.conf, base.named) + self.eval(na["path"], ctx, depth + 1)
        if n == "resolve" and t and a:
            return join(self.eval(t, ctx, depth + 1), self.eval(a[0], ctx, depth + 1))
        if n == "join" and a:
            if a[0].get("k") == "list" and t:  # [...].join('/')? (t is the list)
                pass
            out = None
            for x in a:
                v = self.eval(x, ctx, depth + 1)
                out = v if out is None else join(out, v)
            if out is not None and rn in ("p", "path", "posix", "url"):
                return out
        if t and t.get("k") == "list" and n == "join":
            sep = a[0]["v"] if a and a[0].get("k") == "str" else ""
            out = Tpl()
            for i, x in enumerate(t.get("items") or []):
                out = out + (Tpl(sep) if i else Tpl()) + self.interp(x, ctx, depth)
            return out
        if depth < 10:
            for tgt, conf, via in self.prog.resolve_call(r, ctx):
                if isinstance(tgt, DFunc) and tgt.kind in ("function", "method", "getter"):
                    rets = [f for f in tgt.facts if f["ft"] == "return"]
                    if not rets:
                        continue
                    key = ("call", tgt.id, id(ctx))
                    if key in self.stack:
                        continue
                    self.stack.add(key)
                    try:
                        b = bind_args(tgt, r, ctx)
                        v = self.eval(rets[0]["v"], Ctx(self.prog, tgt.lib, tgt, tgt.cls, b), depth + 1)
                    finally:
                        self.stack.discard(key)
                    v.conf = min_conf(v.conf, conf)
                    v.named = True
                    return v
        return self.ph(r)


def join(a: Tpl, b: Tpl) -> Tpl:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", b.text):
        return b
    if not a.text:
        return b
    return Tpl(a.text.rstrip("/") + "/" + b.text.lstrip("/"), min_conf(a.conf, b.conf), a.named, a.env + b.env, a.unknown or b.unknown)


def bind_args(fn: DFunc, call: dict, cctx: Ctx) -> dict:
    """Parameter name -> (argument repr, caller ctx)."""
    b = {}
    pos = [p for p in fn.params if not p.get("named")]
    a = call.get("a") or []
    na = call.get("na") or {}
    for i, p in enumerate(pos):
        if i < len(a):
            b[p["name"]] = (a[i], cctx)
        elif p.get("default") is not None:
            b[p["name"]] = (p["default"], Ctx(cctx.prog, fn.lib, fn, fn.cls))
    for p in fn.params:
        if p.get("named"):
            if p["name"] in na:
                b[p["name"]] = (na[p["name"]], cctx)
            elif p.get("default") is not None:
                b[p["name"]] = (p["default"], Ctx(cctx.prog, fn.lib, fn, fn.cls))
            else:
                b[p["name"]] = (None, cctx)  # omitted optional named argument
    return b


def type_label(r, ctx: Ctx) -> str | None:
    if not isinstance(r, dict):
        return None
    k = r.get("k")
    if k in ("str", "tpl", "cat"):
        return "String"
    if k == "num":
        return "int" if isinstance(r.get("v"), int) else "double"
    if k == "bool":
        return "bool"
    if k == "null":
        return "Null"
    if k == "cond":
        a, b = type_label(r.get("a"), ctx), type_label(r.get("b"), ctx)
        if a == "Null" and b:
            return b.rstrip("?") + "?"
        if b == "Null" and a:
            return a.rstrip("?") + "?"
        return a if a == b else None
    if k in ("map",):
        return "Map"
    if k == "list":
        return "List"
    if k == "id":
        loc = ctx.locals.get(r["v"])
        if loc and loc[1].get("type"):
            return loc[1]["type"]
        if r["v"] in ctx.bindings and ctx.bindings[r["v"]][0] is not None:
            br, bctx = ctx.bindings[r["v"]]
            return type_label(br, bctx)
    if k == "prop" or k == "id":
        tt = ctx.prog.infer(r["t"], ctx) if k == "prop" else None
        if k == "prop" and tt and tt[0] == "inst" and tt[1] is not None:
            m, _ = ctx.prog.find_member(tt[1], r["n"])
            if isinstance(m, DVar) and m.type:
                return m.type
        if k == "id" and ctx.cls:
            m, _ = ctx.prog.find_member(ctx.cls, r["v"])
            if isinstance(m, DVar) and m.type:
                return m.type
    if k == "call" and r.get("n") in ("toString", "toIso8601String"):
        return "String"
    if k == "call" and r.get("n") in ("toJson", "toMap"):
        return "Map"
    if k == "nn":
        t = type_label(r["e"], ctx)
        return t[:-1] if t and t.endswith("?") else t
    t = ctx.prog.infer(r, ctx)
    if t and t[0] == "inst" and t[1] is not None:
        return t[1].name
    if t and t[0] == "einst":
        return t[1]
    if t and t[0] == "list":
        return "List"
    return None


class HttpExtractor:
    def __init__(self, prog: DartProgram, ev: UrlEval, model_keys):
        self.prog, self.ev = prog, ev
        self.model_keys = model_keys  # (DClass, "to"|"from") -> list[dict]
        self.calls: list[dict] = []
        self.dio_bases: list[tuple[Tpl, DFunc | None, DClass | None, str]] = []
        self._returns_json: dict[str, bool] = {}

    # ---------------------------------------------------------------- receivers
    def client_of(self, t, ctx: Ctx):
        """('http'|'dio'|'io', confidence) for a call receiver, or None."""
        if t is None:
            return None
        if t.get("k") == "id" and t["v"] not in ctx.locals and t["v"] not in ctx.bindings:
            for imp in self.prog.prefix_imports(ctx.lib, t["v"]):
                if imp["ext"] and imp["uri"].startswith("package:http/"):
                    return ("http", EXACT)
                if imp["ext"] and imp["uri"].startswith("package:dio/"):
                    return None
        tt = self.prog.infer(t, ctx)
        if tt and tt[0] == "einst":
            b = tt[1].split(".")[-1]
            if b in HTTP_CLIENT_TYPES and self.prog.imports_ext(ctx.lib, r"^package:http") or b in ("IOClient", "RetryClient"):
                return ("http", RESOLVED)
            if b in DIO_TYPES:
                return ("dio", RESOLVED)
            if b == "HttpClient":
                return ("io", RESOLVED)
        if tt and tt[0] == "inst" and tt[1] is not None:
            names = self.prog.ext_base_names(tt[1])
            if names & DIO_TYPES:
                return ("dio", RESOLVED)
            if names & HTTP_CLIENT_TYPES:
                return ("http", RESOLVED)
            return None
        if tt is None:
            nm = (last_name(t) or "")
            if re.search(r"(?i)dio$", nm) and self.prog.imports_ext(ctx.lib, r"^package:dio"):
                return ("dio", HEURISTIC)
            if re.search(r"(?i)(http|client)$", nm) and self.prog.imports_ext(ctx.lib, r"^package:http/"):
                return ("http", HEURISTIC)
        return None

    def returns_json(self, f: DFunc, depth=0) -> bool:
        if f.id in self._returns_json:
            return self._returns_json[f.id]
        self._returns_json[f.id] = False
        ctx = self.prog.ctx_of(f)
        out = False
        for x in f.facts:
            if x["ft"] == "return" and self.is_json_value(x["v"], ctx, depth + 1):
                out = True
                break
        self._returns_json[f.id] = out
        return out

    def wrap_key(self, f: DFunc, depth=0):
        """'' when f returns decoded JSON, 'body' when it returns {'statusCode': .., 'body': <json>}, else None."""
        key = ("wrap", f.id)
        if key in self._returns_json:
            return self._returns_json[key]
        self._returns_json[key] = None
        ctx = self.prog.ctx_of(f)
        out = None
        for x in f.facts:
            if x["ft"] != "return":
                continue
            v = x["v"]
            while v.get("k") in ("await", "nn", "as"):
                v = v["e"]
            if self.is_json_value(v, ctx, 0):
                out = ""
                break
            if v.get("k") == "map":
                for el in v.get("entries") or []:
                    if (el.get("key") or {}).get("k") == "str" and self.is_json_value(el.get("value"), ctx, 0):
                        out = el["key"]["v"]
                if out is not None:
                    break
            if v.get("k") == "call":
                for tgt, conf, via in self.prog.resolve_call(v, ctx):
                    if isinstance(tgt, DFunc):
                        k = self.wrap_key(tgt)
                        if k is not None:
                            out = k
                            break
                if out is not None:
                    break
        self._returns_json[key] = out
        return out

    def wrapper_var(self, r, ctx: Ctx):
        """Wrapper key if r is (a variable holding) the result of a helper returning {'body': json, ...}."""
        while isinstance(r, dict) and r.get("k") in ("await", "nn", "as"):
            r = r["e"]
        if not isinstance(r, dict):
            return None
        if r.get("k") == "id":
            loc = ctx.locals.get(r["v"])
            if loc and loc[0] == "var" and loc[1].get("init") is not None:
                return self.wrapper_var(loc[1]["init"], ctx)
            return None
        if r.get("k") == "call":
            for tgt, conf, via in self.prog.resolve_call(r, ctx):
                if isinstance(tgt, DFunc):
                    k = self.wrap_key(tgt)
                    if k:
                        return k
        return None

    def is_json_value(self, r, ctx: Ctx, depth=0) -> bool:
        if not isinstance(r, dict) or depth > 4:
            return False
        k = r.get("k")
        if k in ("await", "nn", "as"):
            return self.is_json_value(r["e"], ctx, depth)
        if k == "call" and r.get("n") in ("jsonDecode", "decode") and (r.get("n") == "jsonDecode" or root_name(r.get("t")) in ("json", "jsonCodec", "convert")):
            return True
        if k == "call" and r.get("n") is None and root_name(r.get("t")) == "jsonDecode":
            return True
        if k == "idx" and (r.get("i") or {}).get("k") == "str" and depth < 4:
            wk = self.wrapper_var(r.get("t"), ctx)
            if wk and wk == r["i"]["v"]:
                return True
        if k == "prop" and r["n"] == "data":
            tt = self.prog.infer(r["t"], ctx)
            return tt is None or (tt[0] == "einst" and "Response" in tt[1])
        if k == "id":
            loc = ctx.locals.get(r["v"])
            if loc and loc[0] == "var" and loc[1].get("init") is not None:
                return self.is_json_value(loc[1]["init"], ctx, depth + 1)
        if k == "call":
            for tgt, conf, via in self.prog.resolve_call(r, ctx):
                if isinstance(tgt, DFunc) and self.wrap_key(tgt) == "":
                    return True
        return False

    # ---------------------------------------------------------------- bodies
    def keys_of(self, r, ctx: Ctx, depth=0, cond=False) -> list[dict]:
        if not isinstance(r, dict) or depth > 6:
            return []
        k = r.get("k")
        out: list[dict] = []
        if k == "map":
            for el in r.get("entries") or []:
                if "key" in el:
                    kk = el["key"]
                    if kk.get("k") == "str":
                        out.append({"key": kk["v"], "line": el.get("l") or r.get("l"), "file": ctx.fn.file if ctx.fn else ctx.lib.file,
                                    "type": type_label(el.get("value"), ctx), "conditional": cond or None,
                                    "value": (el.get("value") or {}).get("v") if (el.get("value") or {}).get("k") in ("str", "num", "bool") else None})
                elif el.get("k") == "spread":
                    out += self.keys_of(el["e"], ctx, depth + 1, cond)
                elif el.get("k") == "if":
                    then = self.keys_of({"k": "map", "entries": [el["then"]], "l": r.get("l")}, ctx, depth + 1, True)
                    # `if (x != null) 'k': x` - flow promotion makes the value non-null
                    m = re.fullmatch(r"\s*([\w.]+)\s*!=\s*null\s*", el.get("c") or "")
                    v = (el.get("then") or {}).get("value") or {}
                    if m and v.get("k") in ("id", "prop") and then and (v.get("v") == m.group(1) or v.get("n") == m.group(1).split(".")[-1]):
                        for kd in then:
                            if kd.get("type"):
                                kd["type"] = kd["type"].rstrip("?")
                    out += then
                    if el.get("else"):
                        out += self.keys_of({"k": "map", "entries": [el["else"]], "l": r.get("l")}, ctx, depth + 1, True)
            return out
        if k in ("await", "nn", "as"):
            return self.keys_of(r["e"], ctx, depth + 1, cond)
        if k == "cond":
            return self.keys_of(r["a"], ctx, depth + 1, True) + self.keys_of(r["b"], ctx, depth + 1, True)
        if k == "call":
            n = r.get("n")
            if n in ("jsonEncode", "encode", "fromMap") or (n is None and root_name(r.get("t")) == "jsonEncode"):
                a = r.get("a") or []
                return self.keys_of(a[0], ctx, depth + 1, cond) if a else []
            if n in ("toJson", "toMap") and r.get("t"):
                tt = self.prog.infer(r["t"], ctx)
                if tt and tt[0] == "inst" and tt[1] is not None:
                    return [dict(x, model=tt[1].id) for x in self.model_keys(tt[1], "to")]
            for tgt, conf, via in self.prog.resolve_call(r, ctx):
                if isinstance(tgt, DFunc) and depth < 4:
                    for x in tgt.facts:
                        if x["ft"] == "return":
                            return self.keys_of(x["v"], Ctx(self.prog, tgt.lib, tgt, tgt.cls, bind_args(tgt, r, ctx)), depth + 1, cond)
            return []
        if k == "id":
            n = r["v"]
            if n in ctx.bindings:
                br, bctx = ctx.bindings[n]
                return self.keys_of(br, bctx, depth + 1, cond) if br is not None else []
            loc = ctx.locals.get(n)
            if loc and loc[0] == "var":
                out = self.keys_of(loc[1].get("init"), ctx, depth + 1, cond) if loc[1].get("init") is not None else []
                for f in (ctx.fn.facts if ctx.fn else []):
                    if f["ft"] == "index" and f.get("write") and (f.get("target") or {}).get("k") == "id" and f["target"]["v"] == n \
                            and (f.get("key") or {}).get("k") == "str":
                        out.append({"key": f["key"]["v"], "line": f["l"], "file": ctx.fn.file, "type": None, "conditional": True})
                    if f["ft"] == "call" and f.get("n") in ("addAll", "putIfAbsent") and (f.get("t") or {}).get("k") == "id" and f["t"]["v"] == n:
                        out += self.keys_of((f.get("a") or [None])[0], ctx, depth + 1, True)
                return out
            if loc and loc[0] == "param":
                tt = self.prog.infer(r, ctx)
                if tt and tt[0] == "inst" and tt[1] is not None:
                    return [dict(x, model=tt[1].id) for x in self.model_keys(tt[1], "to")]
            return []
        if k == "cascade":
            out = self.keys_of(r["t"], ctx, depth + 1, cond)
            for s in r.get("sections") or []:
                if s.get("k") == "assign" and (s.get("lhs") or {}).get("k") == "idx" and (s["lhs"].get("i") or {}).get("k") == "str":
                    out.append({"key": s["lhs"]["i"]["v"], "line": s["lhs"].get("l"), "file": ctx.fn.file if ctx.fn else None,
                                "type": type_label(s.get("rhs"), ctx), "conditional": cond or None})
            return out
        if (ctor_type(r) or "").split(".")[-1] == "FormData":
            return []
        tt = self.prog.infer(r, ctx)
        if tt and tt[0] == "inst" and tt[1] is not None:
            return [dict(x, model=tt[1].id) for x in self.model_keys(tt[1], "to")]
        return []

    # ---------------------------------------------------------------- responses
    def response_facts(self, fn: DFunc, ctx: Ctx) -> dict:
        """JSON keys read, models parsed (X.fromJson) and status codes checked in the function handling the response."""
        roots: dict[str, str] = {}  # var -> json path
        for f in fn.facts:
            if f["ft"] == "var" and f.get("init") is not None:
                if self.is_json_value(f["init"], ctx):
                    roots[f["name"]] = ""
                else:
                    init = f["init"]
                    while init.get("k") in ("as", "nn", "await"):
                        init = init["e"]
                    if init.get("k") == "idx" and (init.get("t") or {}).get("k") == "id" and init["t"]["v"] in roots \
                            and (init.get("i") or {}).get("k") == "str":
                        p = roots[init["t"]["v"]]
                        roots[f["name"]] = (p + "." if p else "") + init["i"]["v"]
        # `(json['items'] as List).map((e) => X.fromJson(e))`: closure parameter e = items[]
        clos: dict[str, str] = {}
        for f in fn.facts:
            if f["ft"] == "call" and f.get("n") in ("map", "forEach", "where", "expand") and f.get("a") and (f["a"][0] or {}).get("k") == "fn":
                tg = f.get("t") or {}
                while tg.get("k") in ("as", "nn", "await"):
                    tg = tg["e"]
                path = None
                if tg.get("k") == "id" and tg["v"] in roots:
                    path = roots[tg["v"]]
                elif tg.get("k") == "idx" and (tg.get("i") or {}).get("k") == "str":
                    base = tg.get("t") or {}
                    while base.get("k") in ("as", "nn"):
                        base = base["e"]
                    if base.get("k") == "id" and base["v"] in roots:
                        path = ((roots[base["v"]] + ".") if roots[base["v"]] else "") + tg["i"]["v"]
                    elif self.is_json_value(base, ctx):
                        path = tg["i"]["v"]
                if path is not None and f["a"][0].get("params"):
                    clos.setdefault(f["a"][0]["params"][0], []).append((f["a"][0].get("l") or f.get("l") or 0, (path + "[]") if path else "[]"))
        keys, models, status = [], [], []
        for f in fn.facts:
            if f["ft"] == "index" and not f.get("write") and (f.get("key") or {}).get("k") == "str":
                tg = f.get("target") or {}
                while tg.get("k") in ("as", "nn", "await"):
                    tg = tg["e"]
                path = None
                if tg.get("k") == "id" and tg["v"] in roots:
                    path = roots[tg["v"]]
                elif self.is_json_value(tg, ctx):
                    path = ""
                if path is not None:
                    keys.append({"key": f["key"]["v"], "path": path or None, "line": f["l"], "file": fn.file, "cast": f.get("cast"),
                                 "coalesce": f.get("coalesce"), "post": f.get("post"), "wrap": f.get("wrap")})
            elif f["ft"] == "call" and f.get("n") in ("fromJson", "fromMap") and (f.get("t") or {}).get("k") == "id":
                c = self.prog.resolve_class(ctx.lib, f["t"]["v"])
                if c:
                    arg = (f.get("a") or [None])[0]
                    p = None
                    if isinstance(arg, dict):
                        a2 = arg
                        while a2.get("k") in ("as", "nn"):
                            a2 = a2["e"]
                        if a2.get("k") == "id" and a2["v"] in clos and a2["v"] not in roots:
                            prior = [c_ for c_ in clos[a2["v"]] if c_[0] <= (f.get("l") or 0)]
                            p = max(prior)[1] if prior else clos[a2["v"]][0][1]
                        elif a2.get("k") == "id" and a2["v"] in roots:
                            p = roots[a2["v"]] or ""
                        elif a2.get("k") == "idx" and (a2.get("t") or {}).get("k") == "id" and a2["t"]["v"] in roots and (a2.get("i") or {}).get("k") == "str":
                            p = ((roots[a2["t"]["v"]] + ".") if roots[a2["t"]["v"]] else "") + a2["i"]["v"]
                        elif self.is_json_value(a2, ctx):
                            p = ""
                    models.append({"model": c.id, "name": c.name, "path": p, "line": f["l"], "file": fn.file})
            elif f["ft"] == "status":
                status.append({"op": f["op"], "v": f["v"], "line": f["l"], "file": fn.file, "expr": f.get("e")})
        return {"keys": keys, "models": models, "status": status}

    # ---------------------------------------------------------------- scanning
    def collect_dio_bases(self):
        for fn in self.prog.all_funcs():
            ctx = self.prog.ctx_of(fn)
            for f in fn.facts:
                if (ctor_type(f) or "").split(".")[-1] == "BaseOptions" and "baseUrl" in (f.get("na") or {}):
                    self.dio_bases.append((self.ev.eval(f["na"]["baseUrl"], ctx), fn, fn.cls, fn.file))
                if f["ft"] == "assign" and (f.get("lhs") or {}).get("k") == "prop" and f["lhs"]["n"] == "baseUrl":
                    self.dio_bases.append((self.ev.eval(f["rhs"], ctx), fn, fn.cls, fn.file))
        for c in self.prog.classes.values():
            for v in c.fields.values():
                for x in walk_repr(v.init):
                    if (ctor_type(x) or "").split(".")[-1] == "BaseOptions" and "baseUrl" in (x.get("na") or {}):
                        self.dio_bases.append((self.ev.eval(x["na"]["baseUrl"], Ctx(self.prog, c.lib, None, c)), None, c, c.file))
        for v in self.prog.vars:
            for x in walk_repr(v.init):
                if (ctor_type(x) or "").split(".")[-1] == "BaseOptions" and "baseUrl" in (x.get("na") or {}):
                    self.dio_bases.append((self.ev.eval(x["na"]["baseUrl"], Ctx(self.prog, v.lib, None, None)), None, None, v.file))

    def dio_base_for(self, fn: DFunc) -> list[tuple[Tpl, str]]:
        if not self.dio_bases:
            return []
        same = [b for b in self.dio_bases if fn.cls is not None and b[2] is fn.cls] or [b for b in self.dio_bases if b[3] == fn.file]
        cands = same or self.dio_bases
        uniq = {}
        for t, *_ in cands:
            uniq.setdefault(t.text, t)
        conf = RESOLVED if len(uniq) == 1 else HEURISTIC
        if len(uniq) > 3:
            return []
        return [(t, conf) for t in uniq.values()]

    def scan(self):
        self.collect_dio_bases()
        for fn in list(self.prog.all_funcs()):
            ctx = self.prog.ctx_of(fn)
            for f in fn.facts:
                ct = ctor_type(f)
                if ct and f["ft"] in ("new", "call"):
                    self.http_new(fn, ctx, f, ct)
                elif f["ft"] == "call":
                    self.http_call(fn, ctx, f)
        self.retrofit()
        return self.calls

    def http_call(self, fn, ctx, f):
        n, t = f.get("n"), f.get("t")
        a, na = f.get("a") or [], f.get("na") or {}
        if n is None:
            return
        rn = root_name(t) if t else None
        if n == "connect" and t and t.get("k") == "id" and t["v"] in WS_TYPES and a:
            return self.add(fn, ctx, f, "WS", a[0], "websocket", EXACT)
        if t is None:
            if n in HTTP_PKG_METHODS and a and not self.prog._unqualified(n, ctx) and n not in ctx.locals \
                    and self.prog.imports_ext(ctx.lib, r"^package:http/", None, name=n):
                return self.add(fn, ctx, f, HTTP_PKG_METHODS[n], a[0], "http", EXACT, body=na.get("body"))
            return
        cl = None
        if n in HTTP_PKG_METHODS or n in DIO_METHODS or n in IO_METHODS or n in IO_HOST_METHODS or n in ("send", "fetch"):
            cl = self.client_of(t, ctx)
        if not cl:
            return
        kind, conf = cl
        if kind == "http" and n in HTTP_PKG_METHODS and a:
            return self.add(fn, ctx, f, HTTP_PKG_METHODS[n], a[0], "http", conf, body=na.get("body"))
        if kind == "dio" and n in DIO_METHODS and a:
            method = DIO_METHODS[n]
            if method is None:
                opts = na.get("options") or {}
                m = ((opts.get("na") or {}).get("method") or {}) if isinstance(opts, dict) else {}
                method = m.get("v", "GET").upper() if m.get("k") == "str" else "GET"
            return self.add(fn, ctx, f, method, a[0], "dio", conf, body=na.get("data"), query=na.get("queryParameters"))
        if kind == "io" and n in IO_HOST_METHODS and len(a) >= 3:
            method = IO_HOST_METHODS[n]
            off = 0
            if method is None:
                method = a[0].get("v", "GET").upper() if a[0].get("k") == "str" else "GET"
                off = 1
            if len(a) >= off + 3:
                url = {"k": "tpl", "parts": ["http://", {"e": a[off]}, ":", {"e": a[off + 1]}, {"e": a[off + 2]}]}
                return self.add(fn, ctx, f, method, url, "dart:io", conf)
        if kind == "io" and n in IO_METHODS and a:
            method = IO_METHODS[n]
            uarg = a[0]
            if method is None and len(a) > 1:
                method = a[0].get("v", "GET").upper() if a[0].get("k") == "str" else "GET"
                uarg = a[1]
            return self.add(fn, ctx, f, method or "GET", uarg, "dart:io", conf)

    def http_new(self, fn, ctx, f, ctype):
        base = ctype.split(".")[-1]
        a = f.get("a") or []
        if base in ("Request", "MultipartRequest", "StreamedRequest") and len(a) >= 2 and (
                "." in ctype or self.prog.imports_ext(ctx.lib, r"^package:http/")):
            mt = self.ev.eval(a[0], ctx)
            method = mt.text.upper() if mt.text and "{" not in mt.text else None
            body_keys = []
            # request.fields['k'] = v / request.body = jsonEncode(...) on the variable holding the request
            var = None
            for x in fn.facts:
                if x["ft"] == "var" and isinstance(x.get("init"), dict) and x["init"].get("l") == f["l"] and ctor_type(x["init"]):
                    var = x["name"]
            if var:
                for x in fn.facts:
                    if x["ft"] == "index" and x.get("write") and (x.get("key") or {}).get("k") == "str":
                        tg = x.get("target") or {}
                        if tg.get("k") == "prop" and tg.get("n") == "fields" and root_name(tg) == var:
                            body_keys.append({"key": x["key"]["v"], "line": x["l"], "file": fn.file, "type": None, "form": True})
                    if x["ft"] == "assign" and (x.get("lhs") or {}).get("k") == "prop" and x["lhs"]["n"] in ("body", "bodyFields") \
                            and root_name(x["lhs"]) == var:
                        body_keys += self.keys_of(x["rhs"], ctx)
                    if x["ft"] == "call" and x.get("n") == "addAll" and (x.get("t") or {}).get("k") == "prop" and x["t"]["n"] == "fields" \
                            and root_name(x["t"]) == var:
                        body_keys += [dict(k, form=True) for k in self.keys_of((x.get("a") or [None])[0], ctx)]
            self.add(fn, ctx, f, method, a[1], "http", EXACT, body_keys=body_keys, method_repr=a[0] if method is None else None,
                     multipart=base == "MultipartRequest")

    def add(self, fn, ctx, f, method, url_repr, client, conf, body=None, query=None, body_keys=None, method_repr=None, multipart=False):
        self.calls.append({"fn": fn, "ctx": ctx, "fact": f, "method": method, "url": url_repr, "client": client, "conf": conf,
                           "body": body, "query": query, "body_keys": body_keys, "method_repr": method_repr, "multipart": multipart,
                           "line": f.get("l"), "file": fn.file})

    def retrofit(self):
        for c in self.prog.classes.values():
            anns = {a["name"].split(".")[-1]: a for a in c.raw.get("ann") or []}
            rest = anns.get("RestApi")
            chop = anns.get("ChopperApi")
            if not rest and not chop:
                continue
            ann = rest or chop
            base = (ann.get("na") or {}).get("baseUrl")
            ctx0 = Ctx(self.prog, c.lib, None, c)
            base_t = self.ev.eval(base, ctx0) if base else None
            for m in c.methods.values():
                for a in m.raw.get("ann") or []:
                    an = a["name"].split(".")[-1]
                    verb = an if (rest and an in RETROFIT_VERBS) else (CHOPPER_VERBS.get(an) if chop else None)
                    if not verb:
                        continue
                    pr = (a.get("a") or [None])[0] if rest else (a.get("na") or {}).get("path")
                    path = self.ev.eval(pr, ctx0) if pr else Tpl("")
                    body_keys, query = [], []
                    for p in m.params:
                        for pa in p.get("ann") or []:
                            pn = pa["name"].split(".")[-1]
                            if pn == "Body":
                                pt = self.prog.type_of_text(p.get("type"), m.lib)
                                if pt and pt[0] == "inst":
                                    body_keys += [dict(x, model=pt[1].id) for x in self.model_keys(pt[1], "to")]
                            if pn in ("Field", "Part"):
                                kk = (pa.get("a") or [{}])[0] or {}
                                body_keys.append({"key": kk.get("v") or p["name"], "line": pa.get("l"), "file": m.file, "type": p.get("type"),
                                                  "form": True})
                            if pn == "Query":
                                kk = (pa.get("a") or [{}])[0] or {}
                                query.append(kk.get("v") or p["name"])
                    full = join(base_t, path) if base_t else path
                    self.calls.append({"fn": m, "ctx": self.prog.ctx_of(m), "fact": a, "method": verb, "url": None, "tpl": full,
                                       "client": "retrofit" if rest else "chopper", "conf": EXACT, "body": None, "body_keys": body_keys,
                                       "query_list": query, "line": a.get("l"), "file": m.file, "declared": True,
                                       "base_from_dio": base_t is None})

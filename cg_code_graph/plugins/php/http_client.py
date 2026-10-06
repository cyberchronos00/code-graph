"""Laravel and Guzzle outbound HTTP as client endpoints.

`Illuminate\\Support\\Facades\\Http`, an injected `Factory` / `PendingRequest`, and Guzzle
`Client` calls become `http:` nodes with `HTTP_CALLS`, the same shape the TypeScript and
Kotlin clients use. A base URL read from a container binding through `config()` / `env()`
is `origin_kind` `env` with `attrs.base`, so `cg link` can strip that base path. The sample
value comes from `.env.example` or an `env()` default; `.env` is never read, and a value that is
not a URL is not stored. A verb or base that does not resolve is still an endpoint (`ANY`,
`origin_kind` `unknown`, `heuristic`).

`Http::fake([...])` in a test links the test to the client method (`TEST_HTTP`). It is not
an application call.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from ...core.model import CONFIDENCE_RANK, EXACT, HEURISTIC, RESOLVED
from ..ts.baseurl import EXAMPLE_ENV, base_path, is_config_ph, parse_env
from ..ts.plugin import join_url, normalize_client_url
from .plugin import is_test_path

VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "head": "HEAD"}
SEND = {"send", "request"}
KNOWN = set(VERBS.values())
BUILDERS = {"baseurl", "withheaders", "withtoken", "withbody", "asjson", "acceptjson", "timeout", "retry",
            "asform", "accept", "contenttype", "withoptions", "throw", "withoutverifying", "withqueryparameters"}
HTTP_FACADE = "Illuminate\\Support\\Facades\\Http"
FACTORY = "Illuminate\\Http\\Client\\Factory"
PENDING = "Illuminate\\Http\\Client\\PendingRequest"
GUZZLE = "GuzzleHttp\\Client"
CLIENT_TYPES = {FACTORY, PENDING, GUZZLE}
MAX_VERBS = 6
_SLASH = re.compile(r"^[/\\]+$")


def _example_env(root) -> dict[str, tuple[str, str]]:
    """Sample values from example env files only. A real `.env` is never opened."""
    out: dict[str, tuple[str, str]] = {}
    root = Path(root)
    for name in EXAMPLE_ENV:
        f = root / name
        if f.is_file():
            for key, val in parse_env(f.read_text(errors="replace")).items():
                out.setdefault(key, (val, name))
    return out


def _safe_base(value: str | None) -> str | None:
    """Keep a base URL. Anything that is not a URL (a key, a token, a password) is dropped."""
    v = (value or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].strip()
    if not v or any(c.isspace() for c in v):
        return None
    if re.match(r"^https?://", v, re.I):
        parts = urlsplit(v)
        if parts.username or parts.password or not parts.hostname:
            return None
        return v
    if v.startswith("/") and not v.startswith("//"):
        return v
    return None


def _weak_url(v: str) -> bool:
    """A placeholder that is not a resolved env/config base and not a path with a static segment."""
    return (not v or v == "{?}" or v.startswith("{config.") or v.startswith("{this.")
            or bool(re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*\}", v)))


def min_conf(*cs: str) -> str:
    return min(cs, key=lambda c: CONFIDENCE_RANK.get(c, 0))


def _is_facade(cls: str | None) -> bool:
    return bool(cls) and (cls == HTTP_FACADE or cls.endswith("\\Facades\\Http"))


def _base_leaf(key: str) -> bool:
    """True when the last segment of a config key is a base URL, so sibling keys (a secret) are not read."""
    leaf = (key or "").split(".")[-1].replace("-", "_").replace("_", "").lower()
    return leaf in {"baseurl", "baseuri", "url"}


def _is_config_class(cls: str | None) -> bool:
    return bool(cls) and (cls == "Config" or cls.endswith("\\Config") or cls.endswith("\\Facades\\Config"))


def _is_str_class(cls: str | None) -> bool:
    return bool(cls) and (cls == "Str" or cls.endswith("\\Support\\Str") or cls.endswith("\\Facades\\Str"))


def _is_app(d: dict | None) -> bool:
    if not d:
        return False
    if d.get("k") == "var" and d.get("n") == "app":
        return True
    return d.get("k") == "prop" and d.get("n") == "app" and (d.get("of") or {}).get("k") == "this"


def _is_config_repo(d: dict | None) -> bool:
    """`config()`, `$app['config']`, or `$app->make('config')`."""
    if not d:
        return False
    if d.get("k") == "func" and _fn_name(d) == "config" and not (d.get("args") or []):
        return True
    if d.get("k") == "dim" and _key_name(d.get("key")) == "config" and _is_app(d.get("of")):
        return True
    if d.get("k") == "mcall" and (d.get("m") or "").lower() in ("make", "offsetget") and _is_app(d.get("of")):
        args = d.get("args") or []
        return bool(args and args[0].get("k") == "str" and args[0].get("v") == "config")
    return False


def _key_name(key: dict | None) -> str | None:
    if not key:
        return None
    if key.get("k") == "str" and key.get("v"):
        return key["v"]
    if key.get("k") in ("var", "prop") and key.get("n"):
        return key["n"]
    if key.get("k") == "dim":
        return _key_name(key.get("key"))
    return None


def _seg_placeholder(d: dict) -> str:
    """Last name of a property or index, so rawurlencode($order->id) is `{id}`."""
    if d.get("k") in ("var", "prop") and d.get("n"):
        return "{%s}" % d["n"]
    leaf = _key_name(d.get("key")) if d.get("k") == "dim" else None
    return "{%s}" % leaf if leaf else "{?}"


def _bound(item) -> tuple:
    if isinstance(item, tuple) and len(item) >= 3:
        return item[0], item[1], item[2]
    return item[0], item[1], None


def _assigns(fn, name: str, ctx) -> list:
    """Assignments of `name` in this function. A closure only sees assignments in that same closure."""
    if not fn or not name:
        return []
    out = []
    for f in fn.facts:
        if f.get("t") != "assign" or f.get("var") != name:
            continue
        if ctx is None:
            if f.get("ctx"):
                continue
        elif f.get("ctx") != ctx:
            continue
        out.append(f)
    return out


def _fn_name(d: dict | None) -> str:
    if not d:
        return ""
    return ((d.get("n") or d.get("m") or "").split("\\")[-1]).lower()


def _short(cls: str | None, name: str) -> str:
    leaf = (cls or "").split("\\")[-1] or "?"
    return f"{leaf}::{name}"


class ClientEval:
    def __init__(self, prog, builder, project):
        self.prog, self.b = prog, builder
        self.example_env = _example_env(project.root)
        self.env_from: dict[str, str] = {}
        self.env_defaults: dict[str, tuple[str, str]] = {}
        self._prop_cache: dict[tuple, list[str] | None] = {}

    # ---------------------------------------------------------------- expressions
    def strings(self, d, fn, bind=None, depth=0, seen=None, ctx=None) -> list[str]:
        if not d or depth > 8:
            return ["{?}"]
        seen = seen or set()
        k = d.get("k")
        if k == "str":
            return [d.get("v") or ""]
        if k == "int":
            return [str(d.get("v"))]
        if k == "interp":
            return self._prod([self.strings(p, fn, bind, depth + 1, seen, ctx) for p in d.get("parts") or []], depth)
        if k == "concat":
            return self._prod([self.strings(d.get("l"), fn, bind, depth + 1, seen, ctx),
                               self.strings(d.get("r"), fn, bind, depth + 1, seen, ctx)], depth)
        if k == "alt" and d.get("op") == "coalesce":
            opts = d.get("opts") or []
            left = self.strings(opts[0], fn, bind, depth + 1, seen, ctx) if opts else []
            strong = [v for v in left if not _weak_url(v)]
            if strong:
                return strong[:8]
        if k == "alt":
            out = []
            for o in d.get("opts") or []:
                out += self.strings(o, fn, bind, depth + 1, seen, ctx)
            strong = [v for v in out if not _weak_url(v)]
            return (strong or out)[:8]
        if k == "var":
            return self._var(d.get("n"), fn, bind, depth, seen, ctx)
        if k == "prop" and (d.get("of") or {}).get("k") == "this" and fn and fn.cls:
            got = self.resolve_prop(fn.cls, d.get("n") or "")
            return got if got else ["{this.%s}" % (d.get("n") or "?")]
        if k == "prop":
            return [_seg_placeholder(d)]
        if k == "dim":
            ck = self._read_config_key(d, fn, bind, depth, seen, ctx)
            if ck and _base_leaf(ck):
                return [self.config_placeholder(ck)]
            return [_seg_placeholder(d)]
        if k in ("func", "mcall", "scall"):
            hit = self._call_strings(d, fn, bind, depth, seen, ctx)
            if hit is not None:
                return hit
        return ["{?}"]

    def _var(self, name, fn, bind, depth, seen, ctx=None):
        if not name:
            return ["{?}"]
        if bind and name in bind:
            arg, owner, bctx = _bound(bind[name])
            return self.strings(arg, owner, None, depth + 1, seen, bctx)
        key = (fn.id if fn else "", name)
        if key in seen:
            return ["{%s}" % name]
        assigns = _assigns(fn, name, ctx)
        if assigns:
            out = []
            for a in assigns[:3]:
                out += self.strings(a.get("expr"), fn, bind, depth + 1, seen | {key}, a.get("ctx") or ctx)
            return out[:8] or ["{%s}" % name]
        return ["{%s}" % name]

    def _call_strings(self, d, fn, bind, depth, seen, ctx=None):
        name = _fn_name(d)
        args = d.get("args") or []
        if name in ("rtrim", "ltrim", "trim", "rawurlencode", "urlencode", "strval") and args:
            return self.strings(args[0], fn, bind, depth + 1, seen, ctx)
        if name in ("tostring", "__tostring") and d.get("k") == "mcall":
            return self.strings(d.get("of"), fn, bind, depth + 1, seen, ctx)
        if name == "of" and d.get("k") == "scall" and _is_str_class(d.get("class")) and args:
            return self.strings(args[0], fn, bind, depth + 1, seen, ctx)
        if name == "json_encode" and args:
            return None
        ck = self._config_call_key(d)
        if ck is not None and (name == "config" or (name in ("get", "string") and _base_leaf(ck))):
            ph = self.config_placeholder(ck)
            if _weak_url(ph) and len(args) > 1:
                return self.strings(args[1], fn, bind, depth + 1, seen, ctx)
            return [ph]
        if name == "env" and args and args[0].get("k") == "str":
            key = args[0]["v"]
            self.env_from.setdefault(key, "env()")
            if len(args) > 1 and args[1].get("k") == "str" and args[1].get("v"):
                self.env_defaults.setdefault(key, (args[1]["v"], "env() default"))
            return ["{env.%s}" % key]
        if name in ("sprintf", "vsprintf") and args and args[0].get("k") == "str":
            return self._sprintf(args[0]["v"], args[1:], fn, bind, depth, seen, ctx)
        return None

    def _sprintf(self, fmt, args, fn, bind, depth, seen, ctx=None):
        pieces = re.split(r"(%(?:\d+\$)?[-+ 0']*\d*(?:\.\d+)?[sdfuxX])", fmt)
        lists, i = [], 0
        for p in pieces:
            m = re.fullmatch(r"%(?:(\d+)\$)?[-+ 0']*\d*(?:\.\d+)?[sdfuxX]", p)
            if m:
                idx = int(m.group(1)) - 1 if m.group(1) else i
                i += 1
                lists.append(self.strings(args[idx], fn, bind, depth + 1, seen, ctx) if idx < len(args) else ["{?}"])
            else:
                lists.append([p.replace("%%", "%")])
        return self._prod(lists, depth)

    @staticmethod
    def _prod(lists, depth) -> list[str]:
        if not lists:
            return [""]
        out = [""]
        for part in lists:
            nxt = []
            for a in out:
                for b in part or ["{?}"]:
                    nxt.append(a + b)
                    if len(nxt) >= 8:
                        return nxt
            out = nxt
        return out

    def config_placeholder(self, key: str) -> str:
        n = self.b.nodes.get(f"config:{key}")
        if n is not None:
            envs = (n.attrs or {}).get("env") or []
            if envs:
                self.env_from[envs[0]] = n.file or f"config:{key}"
                defaults = (n.attrs or {}).get("env_default") or []
                if defaults and isinstance(defaults[0], str) and defaults[0]:
                    self.env_defaults.setdefault(envs[0], (defaults[0], n.file or f"config:{key}"))
                return "{env.%s}" % envs[0]
            lit = (n.attrs or {}).get("value")
            if isinstance(lit, str) and lit:
                return lit
        return "{config.%s}" % key

    def _read_config_key(self, d, fn, bind, depth, seen, ctx=None) -> str | None:
        """Dotted config key a config() / Config::get / $app['config']->get expression reads, including a later index."""
        if not isinstance(d, dict) or depth > 8:
            return None
        k = d.get("k")
        if k == "var":
            name = d.get("n")
            if bind and name in bind:
                arg, owner, bctx = _bound(bind[name])
                return self._read_config_key(arg, owner, None, depth + 1, seen, bctx)
            token = (fn.id if fn else "", "cfg", name)
            if token in seen:
                return None
            assigns = _assigns(fn, name, ctx)
            if len(assigns) == 1:
                a = assigns[0]
                return self._read_config_key(a.get("expr"), fn, bind, depth + 1, seen | {token}, a.get("ctx") or ctx)
            return None
        if k == "dim":
            leaf = _key_name(d.get("key"))
            if not leaf:
                return None
            prefix = self._read_config_key(d.get("of"), fn, bind, depth + 1, seen, ctx)
            if prefix is None:
                return None
            return f"{prefix}.{leaf}"
        if k == "alt":
            opts = d.get("opts") or []
            return self._read_config_key(opts[0], fn, bind, depth + 1, seen, ctx) if opts else None
        if k in ("func", "mcall", "scall"):
            return self._config_call_key(d)
        return None

    def _config_call_key(self, d) -> str | None:
        """Key argument of config()/Config::get()/$app['config']->get(), or None when this call is not a config read."""
        if not d:
            return None
        name = _fn_name(d)
        args = d.get("args") or []
        if not args or args[0].get("k") != "str" or not args[0].get("v"):
            return None
        key = args[0]["v"]
        if d.get("k") == "func" and name == "config":
            return key
        if name not in ("get", "string"):
            return None
        if d.get("k") == "scall" and _is_config_class(d.get("class")):
            return key
        if d.get("k") == "mcall" and _is_config_repo(d.get("of")):
            return key
        return None

    # ---------------------------------------------------------------- base URL through the container
    def resolve_prop(self, cls: str, name: str) -> list[str] | None:
        key = (cls, name)
        if key in self._prop_cache:
            return self._prop_cache[key]
        self._prop_cache[key] = None  # recursion guard
        got = self._resolve_prop(cls, name)
        self._prop_cache[key] = got
        return got

    def _resolve_prop(self, cls: str, name: str) -> list[str] | None:
        ctor = self.prog.find_method(cls, "__construct")
        sources = self._param_sources(cls, ctor)
        vals: list[str] = []
        if ctor:
            for f in ctor.facts:
                if f.get("t") == "fetch" and f.get("write") and f.get("prop") == name and (f.get("recv") or {}).get("k") == "this":
                    vals += self._with_params(f.get("expr"), ctor, sources)
        for item in sources.get(name, []):
            expr, owner, ctx = _bound(item)
            vals += [v for v in self.strings(expr, owner, ctx=ctx) if v and v != "{?}" ]
        strong = [v for v in vals if not _weak_url(v)]
        out, seen = [], set()
        for v in (strong or vals):
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out or None

    def _param_sources(self, cls: str, ctor) -> dict[str, list]:
        """Constructor parameter name -> expressions supplied at `new` or `when()->needs()->give()`."""
        out: dict[str, list] = {}
        if not ctor:
            return out
        for p in ctor.params:
            out[p["name"]] = []
        for fn in self.prog.all_funcs:
            for f in fn.facts:
                if f.get("t") == "new" and f.get("class") == cls:
                    for i, p in enumerate(ctor.params):
                        arg = self._arg_at(f.get("args") or [], p["name"], i)
                        if arg is not None:
                            out[p["name"]].append((arg, fn, f.get("ctx")))
                if f.get("t") != "call" or (f.get("m") or "").lower() != "give":
                    continue
                bound, needs = self._when_needs(f.get("recv"))
                if bound != cls:
                    continue
                expr = self._give_expr(fn, f)
                target = self._needs_param(ctor, needs)
                if expr is not None and target:
                    out.setdefault(target, []).append((expr, fn, f.get("ctx")))
        return out

    @staticmethod
    def _needs_param(ctor, needs: str | None) -> str | None:
        if not ctor or not needs:
            return None
        raw = needs[1:] if needs.startswith("$") else needs
        names = [p["name"] for p in ctor.params]
        if raw in names:
            return raw
        want = needs.lstrip("\\")
        for p in ctor.params:
            types = [t.lstrip("\\") for t in (p.get("types") or [])]
            if want in types or raw in types:
                return p["name"]
        return None

    def _with_params(self, expr, ctor, sources: dict[str, list]) -> list[str]:
        if expr is None:
            return []
        used = [n for n, items in sources.items() if items and self._mentions(expr, n)]
        if not used:
            return [v for v in self.strings(expr, ctor) if v and v != "{?}"]
        combos: list[list] = [[]]
        for n in used:
            nxt = []
            for combo in combos:
                for item in sources[n][:3]:
                    nxt.append(combo + [(n, item)])
                    if len(nxt) >= 6:
                        break
            combos = nxt[:6]
        out = []
        for combo in combos:
            out += [v for v in self.strings(expr, ctor, dict(combo)) if v and v != "{?}"]
        return out

    @staticmethod
    def _mentions(expr, name: str) -> bool:
        found = False

        def walk(d):
            nonlocal found
            if found or not isinstance(d, dict):
                return
            if d.get("k") == "var" and d.get("n") == name:
                found = True
                return
            for v in d.values():
                if isinstance(v, dict):
                    walk(v)
                elif isinstance(v, list):
                    for i in v:
                        walk(i)

        walk(expr)
        return found

    @staticmethod
    def _arg_at(args, name, idx):
        for a in args:
            if a.get("named") == name:
                return a
        if idx < len(args) and not args[idx].get("named"):
            return args[idx]
        return None

    @staticmethod
    def _when_needs(recv) -> tuple[str | None, str | None]:
        cls, needs = None, None
        d = recv
        while d and d.get("k") == "mcall":
            m = (d.get("m") or "").lower()
            args = d.get("args") or []
            if m == "needs" and args:
                a0 = args[0]
                if a0.get("k") == "str":
                    needs = a0.get("v")
                elif a0.get("k") == "classconst":
                    needs = a0.get("class")
            if m == "when" and args and args[0].get("k") == "classconst":
                cls = args[0].get("class")
            d = d.get("of")
        return cls, needs

    def _give_expr(self, fn, fact):
        args = fact.get("args") or []
        if args and args[0].get("k") != "closure":
            return args[0]
        line = fact.get("line")
        for g in fn.facts:
            if g.get("t") == "return" and (g.get("ctx") or {}).get("line") == line and (g.get("ctx") or {}).get("m", "").lower() == "give":
                return g.get("expr")
        return None

    # ---------------------------------------------------------------- call recognition
    def peel(self, recv):
        chain = []
        d = recv
        while d and d.get("k") in ("mcall", "scall"):
            chain.append(d)
            if d.get("k") == "scall":
                return d, chain
            d = d.get("of")
        return d or {}, chain

    def client_of(self, fn, fact) -> str | None:
        if fact.get("kind") == "static":
            return "laravel-http" if _is_facade(fact.get("class")) else None
        if fact.get("kind") != "method":
            return None
        root, _chain = self.peel(fact.get("recv"))
        if root.get("k") == "scall" and _is_facade(root.get("class")):
            return "laravel-http"
        if root.get("k") == "new" and root.get("class") == GUZZLE:
            return "guzzle"
        types = self.prog.type_of(root, fn, fn.env) if root else set()
        if FACTORY in types or PENDING in types:
            return "laravel-http"
        if GUZZLE in types:
            return "guzzle"
        # A typed repository, mailer, collection or request is not the HTTP client, even when the
        # method is send / post / get.
        if types:
            return None
        if root.get("k") == "var":
            for f in fn.facts:
                if f.get("t") == "assign" and f.get("var") == root.get("n") and not f.get("ctx"):
                    kind = self._expr_client(fn, f.get("expr"))
                    if kind:
                        return kind
        return None

    def _expr_client(self, fn, d) -> str | None:
        if not d:
            return None
        if d.get("k") == "new" and d.get("class") == GUZZLE:
            return "guzzle"
        if d.get("k") in ("mcall", "scall"):
            root, _ = self.peel(d)
            if root.get("k") == "scall" and _is_facade(root.get("class")):
                return "laravel-http"
            if root.get("k") == "new" and root.get("class") == GUZZLE:
                return "guzzle"
            d = root
        if d.get("k") in ("mcall", "scall", "prop", "var", "new"):
            if d.get("k") == "new" and d.get("class") == GUZZLE:
                return "guzzle"
            types = self.prog.type_of(d, fn, fn.env)
            if FACTORY in types or PENDING in types:
                return "laravel-http"
            if GUZZLE in types:
                return "guzzle"
        return None

    def chain_base(self, fn, recv) -> list[str]:
        d = recv
        while d and d.get("k") in ("mcall", "scall"):
            if (d.get("m") or "").lower() == "baseurl" and d.get("args"):
                return [v for v in self.strings(d["args"][0], fn) if v]
            d = d.get("of") if d.get("k") == "mcall" else None
        return []

    def guzzle_base(self, fn, recv) -> list[str]:
        root, _ = self.peel(recv)
        exprs = []
        if root.get("k") == "var":
            for f in fn.facts:
                if f.get("t") == "assign" and f.get("var") == root.get("n") and (f.get("expr") or {}).get("k") == "new":
                    exprs.append(f["expr"])
        if root.get("k") == "prop" and (root.get("of") or {}).get("k") == "this" and fn.cls:
            ctor = self.prog.find_method(fn.cls, "__construct")
            if ctor:
                for i, p in enumerate(ctor.params):
                    if p["name"] != root.get("n"):
                        continue
                    for src_fn in self.prog.all_funcs:
                        for f in src_fn.facts:
                            if f.get("t") == "new" and f.get("class") == fn.cls:
                                arg = self._arg_at(f.get("args") or [], p["name"], i)
                                if arg and arg.get("k") == "new":
                                    exprs.append(arg)
            for f in (ctor.facts if ctor else []):
                if f.get("t") == "fetch" and f.get("write") and f.get("prop") == root.get("n") and (f.get("expr") or {}).get("k") == "new":
                    exprs.append(f["expr"])
        if root.get("k") == "new" and root.get("class") == GUZZLE:
            exprs.append(root)
        out = []
        for e in exprs:
            if e.get("class") != GUZZLE and e.get("k") == "new":
                continue
            for v in self._base_uri(e, fn):
                if v not in out:
                    out.append(v)
        return out

    def _base_uri(self, new_d, fn) -> list[str]:
        args = new_d.get("args") or []
        if not args or args[0].get("k") != "arr":
            return []
        for it in args[0].get("items") or []:
            key = it.get("key") or {}
            if key.get("k") == "str" and key.get("v") in ("base_uri", "base_url"):
                return self.strings(it.get("v"), fn)
        return []

    def body_expr(self, fn, fact, client: str):
        """The expression whose keys are the request body, if it is written at this call."""
        recv, args = fact.get("recv"), fact.get("args") or []
        d = recv
        while d and d.get("k") == "mcall":
            if (d.get("m") or "").lower() == "withbody" and d.get("args"):
                return self._unwrap_json(d["args"][0])
            d = d.get("of")
        name = (fact.get("m") or "").lower()
        if name in VERBS and client != "guzzle" and len(args) > 1:
            return args[1]
        if client == "guzzle":
            opt = args[2] if name in SEND and len(args) > 2 else (args[1] if name in VERBS and len(args) > 1 else None)
            return self._guzzle_body(opt)
        return None

    @staticmethod
    def _unwrap_json(d):
        if d and d.get("k") == "func" and _fn_name(d) == "json_encode" and d.get("args"):
            return d["args"][0]
        return d

    def _guzzle_body(self, opt):
        if not opt or opt.get("k") != "arr":
            return self._unwrap_json(opt) if opt else None
        for it in opt.get("items") or []:
            key = it.get("key") or {}
            if key.get("k") == "str" and key.get("v") in ("json", "form_params", "body"):
                return self._unwrap_json(it.get("v"))
        return None

    def body_keys(self, expr, fn, bind=None) -> dict | None:
        if expr is None:
            return None
        if bind and expr.get("k") == "var" and expr.get("n") in bind:
            arg, owner, _bctx = _bound(bind[expr["n"]])
            return self.body_keys(arg, owner, None)
        expr = self._follow_var(expr, fn, bind)
        if expr and expr.get("k") == "var" and fn:
            names = [p["name"] for p in fn.params]
            if expr.get("n") in names and not any(f.get("t") == "assign" and f.get("var") == expr["n"] for f in fn.facts):
                return {"keys": [], "conditional": [], "opaque": True, "forwarded": expr["n"],
                        "forwarded_index": names.index(expr["n"])}
        if not expr or expr.get("k") != "arr":
            return {"keys": [], "conditional": [], "opaque": True} if expr else None
        keys, cond = [], []
        opaque = False
        for it in expr.get("items") or []:
            key = it.get("key")
            if not key:
                opaque = True
                continue
            if key.get("k") == "str" and key.get("v") not in keys:
                keys.append(key["v"])
            elif key.get("k") == "alt":
                for o in key.get("opts") or []:
                    if o.get("k") == "str" and o.get("v") not in cond and o.get("v") not in keys:
                        cond.append(o["v"])
            else:
                opaque = True
        out = {"keys": keys, "conditional": cond}
        if opaque and not keys:
            out["opaque"] = True
        return out

    def _follow_var(self, expr, fn, bind, seen=None):
        seen = seen or set()
        if not expr or expr.get("k") != "var" or not fn:
            return expr
        name = expr.get("n")
        if bind and name in bind:
            arg, owner, _bctx = _bound(bind[name])
            return self._follow_var(arg, owner, None, seen)
        if (fn.id, name) in seen:
            return expr
        assigns = [f for f in fn.facts if f.get("t") == "assign" and f.get("var") == name and not f.get("ctx")]
        if len(assigns) == 1:
            return self._follow_var(assigns[0].get("expr"), fn, bind, seen | {(fn.id, name)})
        return expr

    def verbs_of(self, expr, fn, bind=None) -> list[tuple[str, str]]:
        lits = []
        for s in self.strings(expr, fn, bind):
            if re.fullmatch(r"[A-Za-z]+", s or "") and s.upper() in KNOWN:
                if s.upper() not in lits:
                    lits.append(s.upper())
            else:
                return [("ANY", HEURISTIC)]
        if not lits or len(lits) > MAX_VERBS:
            return [("ANY", HEURISTIC)]
        conf = EXACT if len(lits) == 1 and not self._uses_bind(expr, bind) else RESOLVED
        return [(v, conf) for v in lits]

    @staticmethod
    def _uses_bind(expr, bind) -> bool:
        if not bind or not expr:
            return False
        found = False

        def walk(d):
            nonlocal found
            if found or not isinstance(d, dict):
                return
            if d.get("k") == "var" and d.get("n") in bind:
                found = True
            for v in d.values():
                if isinstance(v, dict):
                    walk(v)
                elif isinstance(v, list):
                    for i in v:
                        walk(i)

        walk(expr)
        return found

    def param_deps(self, exprs, fn) -> set[str]:
        names = {p["name"] for p in fn.params}
        reassigned = {f.get("var") for f in fn.facts if f.get("t") == "assign"}
        names -= reassigned
        found = set()

        def walk(d):
            if not isinstance(d, dict):
                return
            if d.get("k") == "var" and d.get("n") in names:
                found.add(d["n"])
            for v in d.values():
                if isinstance(v, dict):
                    walk(v)
                elif isinstance(v, list):
                    for i in v:
                        if isinstance(i, dict):
                            walk(i)

        for e in exprs:
            walk(e)
        return found

    def url_arg(self, fact, client: str):
        args = fact.get("args") or []
        name = (fact.get("m") or "").lower()
        if name in VERBS:
            return args[0] if args else None
        if name in SEND:
            return args[1] if len(args) > 1 else (args[0] if args else None)
        return None

    def verb_arg(self, fact):
        name = (fact.get("m") or "").lower()
        if name in VERBS:
            return None
        args = fact.get("args") or []
        return args[0] if args else None

    def consider(self, fn, fact) -> dict | None:
        name = (fact.get("m") or "").lower()
        if name not in VERBS and name not in SEND:
            return None
        if name in BUILDERS:
            return None
        client = self.client_of(fn, fact)
        if not client:
            return None
        url_e = self.url_arg(fact, client)
        verb_e = self.verb_arg(fact)
        body_e = self.body_expr(fn, fact, client)
        if name in VERBS:
            verbs = [(VERBS[name], EXACT)]
        else:
            verbs = self.verbs_of(verb_e, fn) if verb_e is not None else [("ANY", HEURISTIC)]
        bases = self.chain_base(fn, fact.get("recv"))
        if not bases and client == "guzzle":
            bases = self.guzzle_base(fn, fact.get("recv"))
        return {"fn": fn, "fact": fact, "client": client, "verbs": verbs, "url": url_e, "verb_expr": verb_e,
                "body": body_e, "bases": bases, "line": fact.get("line")}

    def call_sites(self, helper) -> list[tuple]:
        sites = []
        for fn in self.prog.all_funcs:
            if fn.id == helper.id:
                continue
            for f in fn.facts:
                if f.get("t") != "call" or f.get("kind") != "method":
                    continue
                if (f.get("m") or "").lower() != helper.name.lower():
                    continue
                recv = f.get("recv") or {}
                if recv.get("k") == "this" and fn.cls == helper.cls:
                    sites.append((fn, f))
                    continue
                if helper.cls and helper.cls in self.prog.type_of(recv, fn, fn.env):
                    sites.append((fn, f))
        return sites

    def bind_of(self, helper, fact, caller):
        args = fact.get("args") or []
        bind = {}
        for i, p in enumerate(helper.params):
            a = self._arg_at(args, p["name"], i)
            if a is not None:
                bind[p["name"]] = (a, caller)
        return bind

    # ---------------------------------------------------------------- emit
    def classify(self, origin: str | None):
        if is_config_ph(origin) and origin.startswith("{env."):
            key = origin[len("{env."):-1]
            hit = self.example_env.get(key)
            sample = _safe_base(hit[0]) if hit else None
            src = self.env_from.get(key) or (hit[1] if hit else None)
            if sample:
                return "env", {"placeholder": origin, "value": sample, "from": src or ".env.example"}
            if key in self.env_defaults:
                val, dsrc = self.env_defaults[key]
                safe = _safe_base(val)
                if safe:
                    return "env", {"placeholder": origin, "value": safe, "from": src or dsrc}
            # Present in an example file but not a URL, or missing: keep the placeholder, store no secret.
            return "env", {"placeholder": origin, "value": "", "from": src or "env"}
        if origin and "://" in origin:
            return "other", None
        if origin and origin.startswith("{"):
            return "unknown", None
        if not origin:
            return "unknown", None
        return "unknown", None

    def emit_one(self, src, file, line, client, verb, vconf, url, uconf, body, via, bases) -> dict | None:
        urls, seen_u = [], set()
        for b in bases or [None]:
            for u in url:
                joined = join_url(b, u)
                if joined not in seen_u:
                    seen_u.add(joined)
                    urls.append(joined)
        made = None
        for raw in urls:
            path, info = normalize_client_url(raw)
            origin = info.get("origin")
            okind, base = self.classify(origin)
            if base and base.get("value"):
                bp = base_path(base["value"])
                if bp:
                    path = (bp + path) if path != "/" else (bp or "/")
            if path in ("", "/"):
                # a URL with no static path is still an endpoint, labelled with the placeholder
                label = origin or (raw if raw.startswith("{") else "/{?}")
                path = label if label.startswith("/") else "/" + label
            if not path.startswith("/") and not path.startswith("{"):
                path = "/" + path
            key = f"{verb} {path}" if okind in ("api", "unknown", "env") else f"{verb} {origin or ''}{path}"
            nid = self.b.add_node("http", key, key, fqn=key, lang="php",
                                  attrs={"method": verb, "path": path, "client": client, "origin": origin,
                                         "origin_kind": okind})
            if base and (base.get("value") or base.get("placeholder")):
                # an empty sample value still records the placeholder; link strips a path only when value is set
                if base.get("value"):
                    self.b.nodes[nid].attrs["base"] = base
                else:
                    self.b.nodes[nid].attrs["base"] = {"placeholder": base["placeholder"], "value": "",
                                                      "from": base.get("from")}
            conf = min_conf(vconf, uconf, RESOLVED if (base and base.get("value")) else EXACT)
            if okind == "unknown" or verb == "ANY":
                conf = HEURISTIC
            attrs = {"client": client, "url": raw, "origin": origin}
            if via:
                attrs["via_helper"] = via
            if body:
                attrs["body_keys"] = body
            self.b.add_edge(src, nid, "HTTP_CALLS", file=file, line=line, confidence=conf, **attrs)
            made = {"src": src, "method": verb, "path": path, "origin": origin, "origin_kind": okind,
                    "base": self.b.nodes[nid].attrs.get("base"), "file": file, "line": line, "conf": conf,
                    "partial": okind == "unknown" or verb == "ANY"}
        return made

    def materialise(self, rec, bind=None, src=None, file=None, line=None, via=None) -> list[dict]:
        fn = rec["fn"]
        url_vals = self.strings(rec["url"], fn, bind) if rec["url"] is not None else ["{?}"]
        if rec["verb_expr"] is not None:
            verbs = self.verbs_of(rec["verb_expr"], fn, bind)
        else:
            verbs = rec["verbs"]
        bases = list(rec["bases"] or [])
        uconf = RESOLVED if bind else (HEURISTIC if any("{?" in u or u.startswith("{this.") for u in url_vals) else EXACT)
        if any(u.startswith("{") and not u.startswith("{env.") and not u.startswith("{config.") and "/" not in u for u in url_vals):
            uconf = HEURISTIC
        body = self.body_keys(rec["body"], fn, bind)
        out = []
        for verb, vconf in verbs:
            made = self.emit_one(src or fn.id, file or fn.file, line if line is not None else rec["line"],
                                 rec["client"], verb, vconf, url_vals, uconf, body, via, bases)
            if made:
                out.append(made)
        return out


def emit_http_clients(prog, builder, project) -> dict:
    ev = ClientEval(prog, builder, project)
    raw = []
    for fn in prog.all_funcs:
        if is_test_path(fn.file):
            continue
        for fact in fn.facts:
            if fact.get("t") != "call" or fact.get("ctx"):
                continue
            rec = ev.consider(fn, fact)
            if rec:
                raw.append(rec)
    helpers = {}
    for rec in raw:
        deps = ev.param_deps([rec["url"], rec["verb_expr"], rec["body"]], rec["fn"])
        if deps:
            helpers.setdefault(rec["fn"].id, rec)
    emitted: list[dict] = []
    expanded_ids = set()
    for rec in raw:
        if rec["fn"].id not in helpers:
            continue
        sites = ev.call_sites(rec["fn"])
        grew = False
        via = {"fn": _short(rec["fn"].cls, rec["fn"].name), "at": f"{rec['fn'].file}:{rec['line']}"}
        for caller, fact in sites:
            bind = ev.bind_of(rec["fn"], fact, caller)
            if not bind:
                continue
            before = len(emitted)
            emitted += ev.materialise(rec, bind=bind, src=caller.id, file=caller.file, line=fact.get("line"), via=via)
            grew = grew or len(emitted) > before
        if grew:
            expanded_ids.add(rec["fn"].id)
    for rec in raw:
        if rec["fn"].id in expanded_ids:
            continue
        # a helper call that was expanded is not also emitted from the helper
        emitted += ev.materialise(rec)
    fakes = _http_fakes(prog, builder, emitted)
    return {"http_calls": len(emitted), "http_partial": sum(1 for e in emitted if e["partial"]),
            "http_fake_links": fakes}


def _http_fakes(prog, builder, endpoints: list[dict]) -> int:
    """Http::fake(['pattern' => ...]) in a test -> TEST_HTTP to the client method whose URL fits the pattern."""
    if not endpoints:
        return 0
    n = 0
    for fn in prog.test_funcs:
        for fact in fn.facts:
            if fact.get("t") != "call" or fact.get("kind") != "static" or (fact.get("m") or "").lower() != "fake":
                continue
            if not _is_facade(fact.get("class")):
                continue
            args = fact.get("args") or []
            if not args or args[0].get("k") != "arr":
                continue
            for it in args[0].get("items") or []:
                key = it.get("key") or {}
                if key.get("k") != "str" or not key.get("v"):
                    continue
                hits = [e for e in endpoints if _fake_match(key["v"], e)]
                conf = EXACT if len(hits) == 1 else HEURISTIC
                for e in hits:
                    builder.add_edge(fn.id, e["src"], "TEST_HTTP", fn.file, fact.get("line"), conf,
                                     via="Http::fake", pattern=key["v"], method=e["method"], path=e["path"])
                    n += 1
    return n


def _fake_match(pattern: str, ep: dict) -> bool:
    pat = pattern.split("?")[0].split("#")[0]
    if not pat or pat.strip("*") == "":
        return False
    if not re.search(r"[A-Za-z0-9]", pat):
        return False
    path = ep["path"]
    cands = [path]
    origin, base = ep.get("origin") or "", (ep.get("base") or {}).get("value") or ""
    if origin and "://" in origin:
        cands.append(origin.rstrip("/") + "/" + path.lstrip("/"))
    if base:
        cands.append(base.rstrip("/") + "/" + path.lstrip("/"))
    rx = "^" + ".*".join(re.escape(p) for p in pat.split("*")) + "$"
    return any(re.match(rx, c) for c in cands)

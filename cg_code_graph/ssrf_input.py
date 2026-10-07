"""Outbound URLs built from request input (SSRF candidates) and DNS lookups of input (#47 part 2b, closes #42 "URLs from input").

An index-time pass over the source of every non-test function / method, run next to the 2a transport pass. It is a small
text-level taint walk, not a type analysis: in each function it collects request-input sources, follows local variables
assigned from them, and looks at the URL argument of outbound HTTP calls and DNS lookups.

    sources   TS / JS   req.query|body|params|headers|cookies, getQuery / readBody / getRouterParam (h3, Nuxt), c.req.query()
                        / param() / json() (Hono), request.nextUrl.searchParams.get, await request.json(), Nest @Query() /
                        @Body() / @Param() / @Headers() parameters, Elysia `{ query, body, params }`
              PHP       $request->input|query|get|post|json|all|route|header|cookie, request('k'), $_GET / $_POST /
                        $_REQUEST / $_COOKIE, route parameters of controller actions
              Python    Django request.GET|POST|data|query_params|headers|META, Flask request.args|form|json|values|headers,
                        FastAPI / Starlette handler parameters and request.query_params, Django URL kwargs
    sinks     TS / JS   fetch / $fetch / axios / got / ky / needle / undici / node http(s), dns.lookup / resolve*
              PHP       Http:: (Laravel), Guzzle clients, curl_init / CURLOPT_URL, file_get_contents / fopen, gethostbyname /
                        dns_get_record
              Python    requests / httpx / aiohttp (modules and sessions / clients), urllib urlopen, socket.gethostbyname /
                        getaddrinfo
    flow      same function (direct use, local variables, template / f-string / concatenation / sprintf / format,
              `new URL(x)` / urljoin) and one helper level: a handler passes an input-derived value to a function (a CALLS
              edge) whose URL uses that parameter

The fact is `url_from_input = {source, key, part, via, checked}` (+ `caller` for a helper flow): on the HTTP_CALLS /
CONNECTS_TO edge of the call when there is one (mirrors `tls_verify`), otherwise an `insecure_transport` fact of kind `ssrf`
/ `dns-input` on the enclosing function. `part` says what the input controls: `host` or `url` (a candidate), `path` or
`query` on a fixed host (recorded, not a finding). `checked` is true when an allow-list comparison precedes the call.
Only request-input key names are stored, never a value. Test files and test callers are skipped and counted.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

from .insecure_transport import _call_end, _is_comment, _is_test_path
from .tests_index import is_test_node

MAX_BYTES = 1_500_000
JS_EXT = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte"}
LANG = {".py": "py", ".php": "php", **{e: "js" for e in JS_EXT}}
FN_KINDS = ("function", "method", "constructor")
SCALARS = {"str", "int", "float", "bool", "string", "number", "boolean", "bytes", "uuid", "uuid.uuid", "any", "optional[str]",
           "str | none", "none", "mixed", "array", "integer", "double", "path"}
HOST_PARTS = ("host", "url")

SRC_NAME = {"query": "query", "body": "body", "params": "param", "param": "param", "headers": "header", "header": "header",
            "cookies": "cookie", "cookie": "cookie", "path": "path"}


# ------------------------------------------------------------------ source patterns
def _q(name: str) -> str:
    return rf"""(?:\.\s*({name})\b|\[\s*['"`]([^'"`]+)['"`]\s*\])"""


_KEY = r"""\s*\(\s*(?:['"`]([^'"`]+)['"`])?"""
JS_SOURCES: list[tuple[re.Pattern, callable]] = [
    # req.query.k / req.body['k'] / req.params.id / req.headers['x']; Express, Koa-ish, Next pages API, Nest @Req
    (re.compile(r"""(?<![\w$.])(?:req|request|ctx\.request|ctx)\s*\.\s*(query|body|params|headers|cookies)\b(?:\s*\.\s*([A-Za-z_$][\w$]*)(?!\s*\()|\s*\[\s*['"`]([^'"`]+)['"`]\s*\])?"""),
     lambda m: (SRC_NAME[m.group(1)], m.group(2) or m.group(3))),
    (re.compile(r"""(?<![\w$.])(?:req|request)\s*\.\s*(?:get|header)\s*\(\s*['"`]([^'"`]+)['"`]"""), lambda m: ("header", m.group(1))),
    (re.compile(r"""(?<![\w$.])(?:req|request)\s*\.\s*(?:json|formData|text)\s*\(\s*\)"""), lambda m: ("body", None)),
    # h3 / Nuxt
    (re.compile(r"""\bget(?:Validated)?Query\s*\(\s*\w+\s*(?:,[^)]*)?\)(?:\s*\.\s*([A-Za-z_$][\w$]*))?"""), lambda m: ("query", m.group(1))),
    (re.compile(r"""\b(?:readBody|readValidatedBody|readRawBody|readFormData|readMultipartFormData)\s*\(\s*\w+\s*(?:,[^)]*)?\)(?:\s*\.\s*([A-Za-z_$][\w$]*))?"""), lambda m: ("body", m.group(1))),
    (re.compile(r"""\bgetRouterParam\s*\(\s*\w+\s*,\s*['"`]([^'"`]+)['"`]"""), lambda m: ("param", m.group(1))),
    (re.compile(r"""\bgetRouterParams\s*\(\s*\w+\s*\)(?:\s*\.\s*([A-Za-z_$][\w$]*))?"""), lambda m: ("param", m.group(1))),
    (re.compile(r"""\bgetHeader\s*\(\s*\w+\s*,\s*['"`]([^'"`]+)['"`]"""), lambda m: ("header", m.group(1))),
    (re.compile(r"""\bgetCookie\s*\(\s*\w+\s*,\s*['"`]([^'"`]+)['"`]"""), lambda m: ("cookie", m.group(1))),
    # Hono
    (re.compile(r"""\bc\s*\.\s*req\s*\.\s*(query|param|header|queries)\s*\(\s*(?:['"`]([^'"`]+)['"`])?\s*\)"""), lambda m: (SRC_NAME.get(m.group(1), "query"), m.group(2))),
    (re.compile(r"""\bc\s*\.\s*req\s*\.\s*(?:json|parseBody|text|formData)\s*\(\s*\)"""), lambda m: ("body", None)),
    # Next: searchParams.get('k')
    (re.compile(r"""\bsearchParams\s*\.\s*get(?:All)?\s*\(\s*['"`]([^'"`]+)['"`]"""), lambda m: ("query", m.group(1))),
]
PHP_RECV = r"(?:\$\w*[Rr]eq(?:uest)?\w*|request\s*\(\s*\)|\$this\s*->\s*request)"
PHP_SOURCES: list[tuple[re.Pattern, callable]] = [
    (re.compile(rf"""{PHP_RECV}\s*->\s*(input|query|get|post|json|all|route|header|cookie|string|str|integer|boolean|only|collect|bearerToken)\s*\(\s*(?:['"]([^'"]+)['"])?"""),
     lambda m: ({"query": "query", "get": "query", "route": "param", "header": "header", "cookie": "cookie",
                 "bearerToken": "header"}.get(m.group(1), "body"), m.group(2))),
    (re.compile(rf"""{PHP_RECV}\s*->\s*(\w+)\b(?!\s*\()"""), lambda m: ("body", m.group(1))),
    (re.compile(rf"""{PHP_RECV}\s*\[\s*['"]([^'"]+)['"]\s*\]"""), lambda m: ("body", m.group(1))),
    (re.compile(r"""(?<![\w>$:])request\s*\(\s*['"]([^'"]+)['"]"""), lambda m: ("body", m.group(1))),
    (re.compile(r"""\$_(GET|POST|REQUEST|COOKIE)\b(?:\s*\[\s*['"]([^'"]+)['"]\s*\])?"""),
     lambda m: ({"GET": "query", "POST": "body", "REQUEST": "body", "COOKIE": "cookie"}[m.group(1)], m.group(2))),
    (re.compile(r"""\$_SERVER\s*\[\s*['"]HTTP_([A-Z_]+)['"]\s*\]"""), lambda m: ("header", m.group(1).lower())),
]
PY_SOURCES: list[tuple[re.Pattern, callable]] = [
    (re.compile(r"""\brequest\s*\.\s*(GET|POST|data|query_params|args|form|values|headers|META|COOKIES|cookies|json|files)\b(?:\s*\.\s*(?:get|getlist|pop)\s*\(\s*['"]([^'"]+)['"]|\s*\[\s*['"]([^'"]+)['"]\s*\])?"""),
     lambda m: ({"GET": "query", "query_params": "query", "args": "query", "headers": "header", "META": "header",
                 "COOKIES": "cookie", "cookies": "cookie"}.get(m.group(1), "body"), m.group(2) or m.group(3))),
    (re.compile(r"""\brequest\s*\.\s*(?:get_json|json)\s*\(\s*(?:[^)]*)\)"""), lambda m: ("body", None)),
    (re.compile(r"""\brequest\s*\.\s*(?:get_data|body|text)\b"""), lambda m: ("body", None)),
    (re.compile(r"""\bawait\s+request\s*\.\s*(?:json|form|body)\s*\(\s*\)"""), lambda m: ("body", None)),
]
SOURCES = {"js": JS_SOURCES, "php": PHP_SOURCES, "py": PY_SOURCES}

# ------------------------------------------------------------------ sinks: (kind, lib, regex, url arg index, tail)
SinkT = tuple
JS_SINKS: list[SinkT] = [
    ("ssrf", "fetch", re.compile(r"(?<![\w$.])(?:fetch|\$fetch|ofetch)\s*\("), 0),
    ("ssrf", "axios", re.compile(r"(?<![\w$.])axios\s*(?:\.\s*(?:get|post|put|patch|delete|head|options|request)\s*)?\("), 0),
    ("ssrf", "got", re.compile(r"(?<![\w$.])(?:got|ky|needle|superagent)\s*(?:\.\s*(?:get|post|put|patch|delete|head|request|stream)\s*)?\("), 0),
    ("ssrf", "undici", re.compile(r"\bundici\s*\.\s*(?:request|fetch|stream)\s*\("), 0),
    ("ssrf", "node-http", re.compile(r"\b(?:https?|http2)\s*\.\s*(?:get|request)\s*\("), 0),
    ("dns", "dns", re.compile(r"\bdns\w*\s*(?:\.\s*promises\s*)?\.\s*(?:lookup|resolve\w*|reverse)\s*\("), 0),
]
VERBS = r"(?:get|post|put|patch|delete|head|options)"
PHP_SINKS: list[SinkT] = [
    ("ssrf", "laravel-http", re.compile(r"\bHttp\s*::\s*(?:\w+\s*\((?:[^()]|\([^()]*\))*\)\s*->\s*)*" + VERBS + r"\s*\("), 0),
    ("ssrf", "laravel-http", re.compile(r"\bHttp\s*::\s*(?:\w+\s*\((?:[^()]|\([^()]*\))*\)\s*->\s*)*send\s*\("), 1),
    ("ssrf", "guzzle", re.compile(r"\$(?:this\s*->\s*)?\w*(?:client|http|guzzle)\w*\s*->\s*" + VERBS + r"(?:Async)?\s*\(", re.I), 0),
    ("ssrf", "guzzle", re.compile(r"\$(?:this\s*->\s*)?\w*(?:client|http|guzzle)\w*\s*->\s*request(?:Async)?\s*\(", re.I), 1),
    ("ssrf", "curl", re.compile(r"\bcurl_init\s*\("), 0),
    ("ssrf", "curl", re.compile(r"\bcurl_setopt\s*\(\s*\$\w+\s*,\s*CURLOPT_URL\s*,\s*", re.I), "tail"),
    ("ssrf", "curl", re.compile(r"\bCURLOPT_URL\s*=>\s*", re.I), "tail"),
    ("ssrf", "php-stream", re.compile(r"\b(?:file_get_contents|fopen|get_headers|readfile|simplexml_load_file)\s*\("), 0),
    ("dns", "dns", re.compile(r"\b(?:gethostbyname|gethostbynamel|dns_get_record|checkdnsrr|dns_check_record|getmxrr)\s*\("), 0),
]
PY_SINKS: list[SinkT] = [
    ("ssrf", "requests", re.compile(r"\brequests\s*\.\s*" + VERBS + r"\s*\("), 0),
    ("ssrf", "requests", re.compile(r"\brequests\s*\.\s*request\s*\("), 1),
    ("ssrf", "httpx", re.compile(r"\bhttpx\s*\.\s*" + VERBS + r"\s*\("), 0),
    ("ssrf", "httpx", re.compile(r"\bhttpx\s*\.\s*(?:request|stream)\s*\("), 1),
    ("ssrf", "aiohttp", re.compile(r"\baiohttp\s*\.\s*request\s*\("), 1),
    ("ssrf", "@client", re.compile(r"(?<![\w.])(?:self\s*\.\s*)?\w*(?:session|client|http)\w*\s*\.\s*" + VERBS + r"\s*\(", re.I), 0),
    ("ssrf", "@client", re.compile(r"(?<![\w.])(?:self\s*\.\s*)?\w*(?:session|client|http)\w*\s*\.\s*(?:request|stream)\s*\(", re.I), 1),
    ("ssrf", "urllib", re.compile(r"\b(?:urllib\s*\.\s*request\s*\.\s*)?(?:urlopen|urlretrieve)\s*\("), 0),
    ("ssrf", "urllib", re.compile(r"\burllib\s*\.\s*request\s*\.\s*Request\s*\("), 0),
    ("dns", "socket", re.compile(r"\bsocket\s*\.\s*(?:gethostbyname|gethostbyname_ex|getaddrinfo|getfqdn|gethostbyaddr)\s*\("), 0),
    ("dns", "dnspython", re.compile(r"\bdns\s*\.\s*resolver\s*\.\s*(?:resolve|query)\s*\(|\bresolver\s*\.\s*(?:resolve|query)\s*\("), 0),
]
SINKS = {"js": JS_SINKS, "php": PHP_SINKS, "py": PY_SINKS}

ALLOW_WORD = re.compile(r"allow|white_?list|trusted|safe_?host|permitted|known_?host", re.I)
CONST_NAME = re.compile(r"(?<![\w$.])[A-Z][A-Z0-9_]{3,}\b|self::[A-Z][A-Z0-9_]+")
COMPARE = re.compile(r"\.has\s*\(|\.includes\s*\(|\.indexOf\s*\(|\bin\b|in_array\s*\(|array_key_exists\s*\(|===|==|!==|!=|\.endsWith\s*\(|\.get\s*\(|isset\s*\(|\bmatch\b")


# ------------------------------------------------------------------ small text helpers
def split_args(raw: str) -> list[str]:
    out, depth, quote, cur, i = [], 0, None, [], 0
    while i < len(raw):
        c = raw[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(raw):
                cur.append(raw[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
            cur.append(c)
        elif c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def call_args(text: str, open_at: int) -> list[str]:
    end = _call_end(text, open_at)
    return split_args(text[open_at + 1:end - 1])


def tail_expr(text: str, at: int) -> str:
    depth, quote = 0, None
    for i in range(at, min(len(text), at + 600)):
        c = text[i]
        if quote:
            quote = None if c == quote else quote
        elif c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return text[at:i].strip()
            depth -= 1
        elif depth == 0 and c in ",;\n":
            return text[at:i].strip()
    return text[at:at + 600].strip()


def _var_rx(name: str, lang: str) -> re.Pattern:
    if lang == "php":
        return re.compile(rf"\${re.escape(name)}\b")
    return re.compile(rf"(?<![\w$.]){re.escape(name)}(?![\w$])")


def _kwarg(args: list[str], name: str) -> str | None:
    for a in args:
        m = re.match(rf"""^{name}\s*[=:]\s*(.+)$""", a, re.S)
        if m:
            return m.group(1).strip()
    return None


# ------------------------------------------------------------------ taint environment
class Env:
    """Tainted variables of one function: scalars carry (source, key); containers (a whole `req.query`) carry the source."""

    def __init__(self, lang: str, parent: "Env | None" = None):
        self.lang = lang
        self.vars: dict[str, tuple[str, str | None]] = dict(parent.vars) if parent else {}
        self.cont: dict[str, str] = dict(parent.cont) if parent else {}
        self.parts: dict[str, str] = dict(parent.parts) if parent else {}
        self._rx: dict[str, re.Pattern] = {}

    def rx(self, name: str) -> re.Pattern:
        if name not in self._rx:
            self._rx[name] = _var_rx(name, self.lang)
        return self._rx[name]

    def spans(self, expr: str) -> list[tuple[int, int, str, str | None]]:
        """Taint spans in `expr`: (start, end, source, key), source patterns first and variables after, by position."""
        out: list[tuple[int, int, str, str | None]] = []
        taken: list[tuple[int, int]] = []

        def free(a: int, b: int) -> bool:
            return not any(a < y and x < b for x, y in taken)

        for rx, fn in SOURCES[self.lang]:
            for m in rx.finditer(expr):
                if free(m.start(), m.end()):
                    src, key = fn(m)
                    out.append((m.start(), m.end(), src, key))
                    taken.append((m.start(), m.end()))
        for name, src in self.cont.items():
            for m in self.rx(name).finditer(expr):
                if not free(m.start(), m.end()):
                    continue
                key, end = None, m.end()
                a = re.compile(r"""\s*(?:\.\s*(?:get|getlist|pop|input|query|post)\s*\(\s*['"]([^'"]+)['"]\s*\)?|\[\s*['"]([^'"]+)['"]\s*\]|(?:->|\.)\s*([A-Za-z_]\w*)\b(?!\s*\())""").match(expr, m.end())
                if a:
                    key, end = a.group(1) or a.group(2) or a.group(3), a.end()
                out.append((m.start(), end, src, key))
                taken.append((m.start(), end))
        for name, (src, key) in self.vars.items():
            for m in self.rx(name).finditer(expr):
                if free(m.start(), m.end()):
                    out.append((m.start(), m.end(), src, key))
                    taken.append((m.start(), m.end()))
        return sorted(out)


def _is_whole_source(expr: str, env: Env) -> str | None:
    """The source when the whole expression is a bare container (`req.query`, `$request`, `request.GET`)."""
    e = re.sub(r"^(?:await\s+)", "", expr.strip())
    sp = env.spans(e)
    if len(sp) == 1 and sp[0][0] == 0 and sp[0][1] == len(e) and sp[0][3] is None:
        return sp[0][2]
    return None


ASSIGN = {
    "js": re.compile(r"""^[ \t]*(?:export\s+)?(?:(?:const|let|var)\s+)?(?P<lhs>[A-Za-z_$][\w$]*|\{[^}=\n]*\}|\[[^\]=\n]*\])\s*(?::\s*[^=\n]+?)?\s*=(?![=>])\s*(?P<rhs>[^\n]+)$""", re.M),
    "py": re.compile(r"""^[ \t]*(?P<lhs>[A-Za-z_]\w*)\s*(?::\s*[^=\n]+?)?\s*=(?!=)\s*(?P<rhs>[^\n]+)$""", re.M),
    "php": re.compile(r"""^[ \t]*\$(?P<lhs>\w+)\s*=(?![=>])\s*(?P<rhs>[^\n]+)$""", re.M),
}


def _lhs_names(lhs: str) -> list[tuple[str, str | None]]:
    """[(variable, source key)] of a destructuring pattern; a plain name gives [(name, None)]."""
    if lhs.startswith(("{", "[")):
        out = []
        for part in lhs.strip("{}[] ").split(","):
            part = part.strip()
            if not part or part.startswith("..."):
                continue
            part = part.split("=")[0].strip()
            key, _, alias = part.partition(":")
            out.append(((alias or key).strip(), key.strip() if lhs.startswith("{") else None))
        return out
    return [(lhs, None)]


def _rhs_only_source(rhs: str, env: Env) -> str | None:
    r = rhs.strip().rstrip(";")
    r = re.sub(r"^(?:\(\s*)?(?:await\s+)", "", r)
    if r.count(")") > r.count("("):
        r = r[:-1]
    return _is_whole_source(r, env)


_NORMALIZED = re.compile(
    r"^(?:\\?(?:realpath|basename|intval|floatval)|\(\s*(?:int|float)\s*\)\s*\(?|parseInt|parseFloat|Number|int|float|os\.path\.basename)\s*\(")


def _normalized(rhs: str) -> bool:
    """The whole right-hand side is a path-normalising or numeric call, so the variable is no longer a URL."""
    if rhs.startswith(("(int)", "(float)")):
        return True
    m = _NORMALIZED.match(rhs)
    if not m:
        return False
    depth = 0
    for i in range(m.end() - 1, len(rhs)):
        depth += {"(": 1, ")": -1}.get(rhs[i], 0)
        if depth == 0:
            return not rhs[i + 1:].strip()
    return False


def _rhs_part(rhs: str, env: Env) -> str | None:
    """The URL part the tainted piece of an assignment's right-hand side controls, when it builds a URL string."""
    fmt = _format_parts(rhs, env)
    if fmt:
        text, start = fmt
        sp = env.spans(text[start:])
        return url_part(text, sp[0][0] + start, env) if sp else None
    sp = env.spans(rhs)
    return url_part(rhs, sp[0][0], env) if sp else None


def run_assignments(body: str, env: Env, lang: str) -> None:
    rx = ASSIGN[lang]
    for _ in range(3):
        before = (len(env.vars), len(env.cont))
        for m in rx.finditer(body):
            lhs, rhs = m.group("lhs"), m.group("rhs").strip().rstrip(";").strip()
            if lang == "py" and re.search(r"\blambda\b|^\s*(?:def|class|if|elif|for|while)\b", m.group(0)):
                continue
            if _normalized(rhs):
                continue
            whole = _rhs_only_source(rhs, env)
            sp = env.spans(rhs)
            if not sp:
                continue
            names = _lhs_names(lhs)
            if lhs.startswith(("{", "[")):
                src = whole or sp[0][2]
                for nm, key in names:
                    env.vars.setdefault(nm, (src, sp[0][3] if src == "arg" else key))
                continue
            nm = names[0][0]
            if whole == "arg":
                first = sp[0]
                env.vars.setdefault(nm, (first[2], first[3]))
            elif whole:
                env.cont.setdefault(nm, whole)
            else:
                first = sp[0]
                env.vars.setdefault(nm, (first[2], first[3]))
                part = _rhs_part(rhs, env)
                if part in ("path", "query"):
                    env.parts.setdefault(nm, part)


# ------------------------------------------------------------------ URL part
def _literal_prefix(prefix: str) -> str:
    """String-literal text of the part of an expression before the tainted piece; `\\0` marks an unknown piece."""
    out, i, n = [], 0, len(prefix)
    while i < n:
        c = prefix[i]
        if c in "\"'`":
            j = i + 1
            while j < n and prefix[j] != c:
                if prefix[j] == "\\":
                    j += 1
                    if j >= n:
                        break
                elif (c == "`" and prefix[j:j + 2] == "${") or (c in "\"'" and prefix[j] == "{" and re.search(r"[fF]$", prefix[:i + 0] or "")):
                    k = j + 2 if prefix[j] == "$" else j + 1
                    depth = 1
                    while k < n and depth:
                        depth += {"{": 1, "}": -1}.get(prefix[k], 0)
                        k += 1
                    out.append("\0")
                    j = k
                    continue
                elif c == '"' and prefix[j] == "$" and j + 1 < n and (prefix[j + 1].isalpha() or prefix[j + 1] == "{"):
                    k = j + 1
                    if prefix[k] == "{":
                        k = prefix.find("}", k) + 1 or n
                    else:
                        while k < n and (prefix[k].isalnum() or prefix[k] == "_"):
                            k += 1
                    out.append("\0")
                    j = k
                    continue
                out.append(prefix[j])
                j += 1
            i = j + 1
            continue
        m = re.match(r"\$?[A-Za-z_][\w$]*(?:(?:->|::|\.)[A-Za-z_]\w*)*(?!\w|\s*\()", prefix[i:])
        if m and not re.match(r"(?:await|new|return|and|or|not)$", m.group(0)):
            out.append("\0")
            i += m.end()
            continue
        i += 1
    return "".join(out)


def url_part(expr: str, start: int, env: Env | None = None) -> str:
    """What the tainted piece starting at `start` controls in the URL expression: host / url / path / query."""
    e = expr.strip()
    lead = re.match(r"(?:await\s+)?(?:new\s+URL\s*\(|urljoin\s*\(|urllib\.parse\.urljoin\s*\(|new\s+Request\s*\()", e)
    if lead and start >= lead.end():
        return "url"
    prefix = re.sub(r"\$?\{$", "", expr[:start])
    lit = _literal_prefix(prefix)
    if "://" in lit:
        after = lit.split("://", 1)[1]
        if re.search(r"[/?#]", after):
            return "query" if re.search(r"[?#]", after) else "path"
        return "host"
    if lit == "":
        return "url"
    return "query" if "?" in lit else "path"


def _format_parts(expr: str, env: Env) -> tuple[str, int] | None:
    """For sprintf / .format / '%s' % x: an equivalent expression text and the start of the first tainted piece."""
    m = re.match(r"""^\s*(?:sprintf|printf)\s*\(""", expr)
    if m:
        args = call_args(expr, m.end() - 1)
        if args:
            return _fill_format(args[0], args[1:], env)
    m = re.match(r"""^\s*(?P<fmt>[fFrRbB]?(?:"[^"\n]*"|'[^'\n]*'))\s*\.\s*format\s*\(""", expr)
    if m:
        return _fill_format(m.group("fmt"), call_args(expr, m.end() - 1), env)
    m = re.match(r"""^\s*(?P<fmt>"[^"\n]*"|'[^'\n]*')\s*%\s*(?P<rest>.+)$""", expr)
    if m and "%" in m.group("fmt"):
        rest = m.group("rest").strip()
        args = split_args(rest[1:-1]) if rest.startswith("(") and rest.endswith(")") else [rest]
        return _fill_format(m.group("fmt"), args, env)
    return None


def _fill_format(fmt: str, args: list[str], env: Env) -> tuple[str, int] | None:
    q = fmt[:1] if fmt[:1] in "\"'" else fmt[1:2]
    body = fmt[fmt.find(q) + 1:fmt.rfind(q)]
    ph = list(re.finditer(r"%(?:\d+\$)?[-+ 0#]*\d*(?:\.\d+)?[sdfuxXeEgGcr]|\{[^{}]*\}", body))
    for i, p in enumerate(ph):
        if i < len(args) and env.spans(args[i]):
            return q + body[:p.start()] + q + " + " + args[i], len(q + body[:p.start()] + q + " + ")
    return None


# ------------------------------------------------------------------ the analysis of one function
def _signature(lines: list[str], name: str, lang: str) -> tuple[str, str] | None:
    """(parameter text, text before the parameters) of the function defined at lines[0]."""
    head = "\n".join(lines[:8])
    short = re.escape(name.split(".")[-1].split("::")[-1]) if name else ""
    rx = {"py": rf"\bdef\s+{short}\s*\(", "php": rf"\bfunction\s+&?{short}\s*\(", "js": rf"(?<![\w$.]){short}\s*[<(]"}[lang]
    m = re.search(rx, head) if short else None
    if not m:
        m = re.search(r"(?:=|:|\()\s*(?:async\s*)?\(", head) if lang == "js" else None
        if not m:
            return None
        at = m.end() - 1
    else:
        at = head.find("(", m.start())
        if at < 0:
            return None
    end = _call_end(head, at)
    return head[at + 1:end - 1], head[:at]


def params_of(sig: str, lang: str) -> list[str]:
    out = []
    for a in split_args(sig):
        if lang == "php":
            m = re.search(r"\$(\w+)", a)
        elif lang == "py":
            m = re.match(r"\s*\*{0,2}(\w+)", a)
        else:
            m = re.match(r"\s*(?:@\w+\s*\([^)]*\)\s*)*(?:public\s+|private\s+|readonly\s+)*(?:\.\.\.)?([A-Za-z_$][\w$]*)", a)
        out.append(m.group(1) if m else "")
    return out


def _signature_sources(sig: str, head: str, lang: str, env: Env, route_paths: list[str], is_route: bool,
                       file_text: str) -> None:
    """Handler parameters that carry request input: Nest decorators, Elysia / Next destructuring, route parameters."""
    args = split_args(sig)
    if lang == "js":
        for a in args:
            m = re.match(r"""\s*@(Query|Body|Param|Headers|Header|Req|Request)\s*\(\s*(?:['"`]([^'"`]+)['"`])?\s*\)\s*(?:readonly\s+)?([A-Za-z_$][\w$]*)""", a)
            if m:
                src = {"Query": "query", "Body": "body", "Param": "param", "Headers": "header", "Header": "header"}.get(m.group(1))
                if src is None:
                    continue
                if m.group(2):
                    env.vars[m.group(3)] = (src, m.group(2))
                else:
                    env.cont[m.group(3)] = src
                continue
            d = re.match(r"\s*\{([^}]*)\}", a)
            if d:
                for nm in re.findall(r"\b(query|body|params|headers|cookies|cookie)\b", d.group(1)):
                    env.cont[nm] = SRC_NAME[nm]
    elif lang == "php":
        for a in args:
            m = re.match(r"\s*(?:(?P<t>\??[\w\\|]+)\s+)?&?\$(?P<n>\w+)", a)
            if not m:
                continue
            t, n = (m.group("t") or "").lower().lstrip("?"), m.group("n")
            if t.endswith("request"):
                env.cont[n] = "body"
            elif is_route and (t in ("", "string", "int", "float", "mixed", "integer", "array")):
                env.vars[n] = ("param", n)
    elif lang == "py":
        fastapi = bool(re.search(r"\b(?:fastapi|starlette)\b", file_text))
        path = " ".join(route_paths)
        names = params_of(sig, "py")
        for a, n in zip(args, names):
            if not n or n in ("self", "cls", "request", "req", "args", "kwargs") or not is_route:
                continue
            ann = re.match(r"\s*\*{0,2}\w+\s*:\s*([^=]+?)\s*(?:=\s*(.*))?$", a, re.S)
            ann_t = ann.group(1).strip() if ann else ""
            default = (ann.group(2) or "") if ann else ""
            if re.search(r"\bHeader\s*\(", default) or re.search(r"\bHeader\b", ann_t):
                env.vars[n] = ("header", n.replace("_", "-"))
            elif re.search(r"\bCookie\s*\(", default):
                env.vars[n] = ("cookie", n)
            elif re.search(r"\bBody\s*\(", default) or re.search(r"\bBody\b", ann_t):
                env.vars[n] = ("body", n)
            elif re.search(r"[{<][^}>]*\b" + re.escape(n) + r"\b", path):
                env.vars[n] = ("param", n)
            elif fastapi and ann_t and ann_t.lower().split("[")[0] not in SCALARS and not re.match(r"(?:list|set)\b", ann_t.lower()):
                env.cont[n] = "body"
            elif fastapi:
                env.vars[n] = ("query", n)
            elif not fastapi:
                env.vars[n] = ("param", n)


PREFIX_CHECK = re.compile(r"""(?:\.\s*(?:startsWith|startswith)\s*\(\s*|\bstr_starts_with\s*\([^,]+,\s*)['"]https?://[^'"]+/?['"]""")


def _guard_before(body_lines: list[str], upto: int, env: Env, lang: str, extra_names=()) -> bool:
    """An allow-list comparison on a tainted value before line index `upto` (heuristic, textual)."""
    for ln in body_lines[:upto]:
        s = ln.strip()
        if not s or s.startswith(("#", "//", "*", "/*")):
            continue
        if not (env.spans(ln) or any(_var_rx(n, lang).search(ln) for n in extra_names)):
            continue
        if PREFIX_CHECK.search(ln) and re.search(r"\b(?:if|abort|assert|unless|when|validate|return|throw)\b", ln):
            return True
        if not (ALLOW_WORD.search(ln) or CONST_NAME.search(ln)):
            continue
        if COMPARE.search(ln) or re.search(r"\b(?:if|abort|assert|unless|when|validate)\b", ln):
            return True
    return False


class Sink:
    __slots__ = ("kind", "lib", "line", "expr", "spans", "part", "checked", "idx")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _url_expr(args: list[str], idx, lang: str, tail: str | None = None) -> str | None:
    if tail is not None:
        return tail
    if idx < len(args):
        a = args[idx]
        if a.startswith("{"):
            m = re.search(r"""\burl\s*:\s*([^,}\n]+)""", a)
            return m.group(1).strip() if m else None
        if lang == "py" and re.match(r"\w+\s*=", a):
            a = _kwarg(args, "url") or ""
        return a or None
    return _kwarg(args, "url")


def has_sink(text: str, lang: str) -> bool:
    """Cheap pre-test: some sink pattern occurs in `text` (a superset of what `find_sinks` reports). A function or file
    without one cannot yield a fact, so its taint environment is never built."""
    return any(rx.search(text) for _k, _l, rx, _i in SINKS[lang])


def find_sinks(body: str, base_line: int, lang: str, env: Env, py_lib: str | None, owned) -> list[Sink]:
    out: list[Sink] = []
    lines = body.split("\n")
    for kind, lib, rx, idx in SINKS[lang]:
        for m in rx.finditer(body):
            if _is_comment(body, m.start(), lang if lang != "py" else "py"):
                continue
            line = base_line + body.count("\n", 0, m.start())
            if not owned(line):
                continue
            tail = None
            if idx == "tail":
                tail, args = tail_expr(body, m.end()), []
            else:
                args = call_args(body, m.end() - 1)
            expr = _url_expr(args, idx, lang, tail)
            if not expr:
                continue
            if lib == "@client":
                lib = py_lib
                if not lib:
                    continue
            fmt = _format_parts(expr, env)
            if fmt:
                text, start = fmt
                sp = env.spans(text[start:])
                spans = [(s + start, e + start, a, b) for s, e, a, b in sp]
                expr = text
            else:
                spans = env.spans(expr)
            if not spans:
                continue
            first = spans[0]
            part = "host" if kind == "dns" else url_part(expr, first[0], env)
            if kind != "dns" and part == "url" and expr.strip() in env.parts:
                part = env.parts[expr.strip()]
            rel = line - base_line
            out.append(Sink(kind=kind, lib=lib, line=line, expr=expr, spans=spans, part=part, idx=rel,
                            checked=_guard_before(lines, rel, env, lang)))
    return out


def _py_client_lib(text: str) -> str | None:
    for lib in ("httpx", "aiohttp", "requests", "urllib3"):
        if re.search(rf"^\s*(?:import|from)\s+{lib}\b", text, re.M):
            return lib
    return None


def url_input_marker(u: dict | None) -> str:
    """`url from input (query.url, url)`: the request input that builds an outbound URL (#47 part 2b), with `checked` /
    `via helper` when an allow-list precedes the call or a helper passes the value."""
    if not u:
        return ""
    bits = [f"{u.get('source')}.{u.get('key')}", str(u.get("part"))]
    tags = (["via helper"] if u.get("via") == "helper" else []) + (["host checked"] if u.get("checked") else [])
    return f"url from input ({', '.join(bits)}{'; ' + ', '.join(tags) if tags else ''})"


# ------------------------------------------------------------------ the pass
class _Fn:
    def __init__(self, node, lang: str):
        self.node, self.lang = node, lang
        self.id, self.file = node.id, (node.file or "").replace("\\", "/")
        self.lo, self.hi = node.line, node.end_line or node.line


def _edges_at(builder, owner: str, rel: str, line: int) -> list:
    """CONNECTS_TO / HTTP_CALLS edges of the call starting on `line` (a chained call may report the next line)."""
    cands = [e for e in builder.edges.values()
             if e.kind in ("CONNECTS_TO", "HTTP_CALLS") and e.src == owner and e.file == rel and e.line is not None
             and 0 <= e.line - line <= 1]
    if not cands:
        return []
    near = min(e.line for e in cands)
    return [e for e in cands if e.line == near]


def apply(project, builder) -> dict:
    st: dict = defaultdict(int)
    fns: dict[str, list[_Fn]] = defaultdict(list)
    routes_of: dict[str, list[str]] = defaultdict(list)
    calls: dict[str, set] = defaultdict(set)
    for e in builder.edges.values():
        if e.kind == "ROUTES_TO":
            routes_of[e.dst].append(e.src)
        elif e.kind == "CALLS":
            calls[e.src].add(e.dst)
    for n in builder.nodes.values():
        f = (n.file or "").replace("\\", "/")
        if n.kind in FN_KINDS and n.line and f and LANG.get(os.path.splitext(f)[1].lower()):
            fns[f].append(_Fn(n, LANG[os.path.splitext(f)[1].lower()]))
    texts: dict[str, str] = {}
    for rel in list(fns):
        p = project.root / rel
        if _is_test_path(rel):
            st["tests_skipped"] += len(fns.pop(rel))
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                fns.pop(rel)
                continue
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            fns.pop(rel)
            continue
        if max((len(x) for x in t[:200000].split("\n")), default=0) > 3000:
            fns.pop(rel)
            continue
        texts[rel] = t
    byid: dict[str, _Fn] = {fn.id: fn for lst in fns.values() for fn in lst}

    def owner_of(rel: str, line: int) -> str | None:
        best = None
        for fn in fns.get(rel, ()):
            if fn.lo <= line <= fn.hi and (best is None or fn.hi - fn.lo < best[0]):
                best = (fn.hi - fn.lo, fn.id)
        return best[1] if best else None

    helper_sinks: dict[str, list] = defaultdict(list)
    direct: list[tuple[_Fn, list[Sink], Env, str]] = []
    for rel, lst in fns.items():
        text = texts[rel].split("\n")
        lang = lst[0].lang
        if not has_sink(texts[rel], lang):
            st["tests_skipped"] += sum(1 for fn in lst if is_test_node(fn.node) or (fn.node.attrs or {}).get("test_only"))
            continue
        py_lib = _py_client_lib(texts[rel]) if lang == "py" else None
        envs: dict[str, Env] = {}
        for fn in sorted(lst, key=lambda x: (x.lo, -x.hi)):
            if is_test_node(fn.node) or (fn.node.attrs or {}).get("test_only"):
                st["tests_skipped"] += 1
                continue
            parent = None
            for cand in lst:
                if cand is not fn and cand.lo <= fn.lo and fn.hi <= cand.hi and (cand.hi - cand.lo) > (fn.hi - fn.lo) and cand.id in envs:
                    if parent is None or (cand.hi - cand.lo) < (parent.hi - parent.lo):
                        parent = cand
            body = "\n".join(text[fn.lo - 1:fn.hi])
            if not has_sink(body, lang):
                continue
            sig = _signature(text[fn.lo - 1:fn.lo + 7], fn.node.name or "", lang)
            sig_text, head = sig if sig else ("", "")
            owned = lambda ln, fn=fn: owner_of(rel, ln) == fn.id  # noqa: E731
            # direct: request-input sources and route parameters
            env = Env(lang, envs.get(parent.id) if parent else None)
            is_route = fn.id in routes_of
            if sig:
                _signature_sources(sig_text, head, lang, env, routes_of.get(fn.id, []), is_route, texts[rel])
            run_assignments(body, env, lang)
            envs[fn.id] = env
            sinks = find_sinks(body, fn.lo, lang, env, py_lib, owned)
            if sinks:
                direct.append((fn, sinks, env, rel))
            # helper view: every parameter is a potential carrier
            if sig:
                names = [n for n in params_of(sig_text, lang) if n and n not in ("self", "cls", "this")]
                if names:
                    penv = Env(lang)
                    for i, nm in enumerate(params_of(sig_text, lang)):
                        if nm and nm not in ("self", "cls", "this"):
                            penv.vars[nm] = ("arg", str(i))
                    run_assignments(body, penv, lang)
                    for s in find_sinks(body, fn.lo, lang, penv, py_lib, owned):
                        a = [sp for sp in s.spans if sp[2] == "arg" and str(sp[3]).isdigit()]
                        if a:
                            helper_sinks[fn.id].append((int(a[0][3]), s))

    facts: dict[tuple, dict] = {}

    def record(owner_id: str, rel: str, s: Sink, src: str, key: str | None, part: str, via: str, checked: bool,
               caller: str | None) -> None:
        k = (owner_id, s.line, s.kind)
        if k in facts and (facts[k]["via"] == "direct" or via == "helper"):
            return
        u = {"source": src, "key": key or "*", "part": part, "via": via, "checked": bool(checked)}
        if caller:
            u["caller"] = caller
        facts[k] = {"owner": owner_id, "file": rel, "sink": s, "u": u, "via": via}

    for fn, sinks, env, rel in direct:
        for s in sinks:
            src = [sp for sp in s.spans if sp[2] != "arg"]
            if not src:
                continue
            record(fn.id, rel, s, src[0][2], src[0][3], s.part, "direct", s.checked, None)

    # one helper level: handler -> function whose URL uses a parameter
    for rel, lst in fns.items():
        lang = lst[0].lang
        text = texts[rel]
        for g in lst:
            if is_test_node(g.node) or (g.node.attrs or {}).get("test_only"):
                continue
            targets = [t for t in calls.get(g.id, ()) if t in helper_sinks and t != g.id]
            if not targets:
                continue
            body_lines = text.split("\n")[g.lo - 1:g.hi]
            body = "\n".join(body_lines)
            env = Env(lang)
            sig = _signature(text.split("\n")[g.lo - 1:g.lo + 7], g.node.name or "", lang)
            if sig:
                _signature_sources(sig[0], sig[1], lang, env, routes_of.get(g.id, []), g.id in routes_of, text)
            run_assignments(body, env, lang)
            for t in targets:
                callee = byid.get(t)
                if callee is None:
                    continue
                nm = (callee.node.name or "").split(".")[-1].split("::")[-1]
                if not nm:
                    continue
                for m in re.finditer(rf"(?<![\w$])\b{re.escape(nm)}\s*\(", body):
                    line = g.lo + body.count("\n", 0, m.start())
                    if owner_of(rel, line) != g.id:
                        continue
                    args = call_args(body, m.end() - 1)
                    for idx, hs in helper_sinks[t]:
                        if idx >= len(args):
                            continue
                        sp = env.spans(args[idx])
                        if not sp:
                            continue
                        a_part = url_part(args[idx], sp[0][0], env)
                        part = a_part if hs.part in HOST_PARTS else hs.part
                        if hs.part in HOST_PARTS and a_part not in HOST_PARTS:
                            part = a_part
                        checked = hs.checked or _guard_before(body_lines, line - g.lo, env, lang)
                        if hs.kind == "ssrf":
                            expanded = [e for e in _edges_at(builder, g.id, rel, line) if (e.attrs or {}).get("via_helper")]
                            if expanded:
                                u = {"source": sp[0][2], "key": sp[0][3] or "*", "part": part, "via": "helper", "checked": bool(checked)}
                                for e in expanded:
                                    e.attrs = {**(e.attrs or {}), "url_from_input": u}
                                st["edge_facts"] += 1
                                st["via_helper"] += 1
                                st["checked" if checked else "host_controlled" if part in HOST_PARTS else "path_only"] += 1
                                continue
                        record(callee.id, callee.file, hs, sp[0][2], sp[0][3], part, "helper", checked, g.id)

    for (owner_id, line, kind), f in sorted(facts.items(), key=lambda kv: (kv[1]["file"], kv[0][1])):
        n = builder.nodes.get(owner_id)
        if n is None:
            continue
        s, u, rel = f["sink"], f["u"], f["file"]
        conf = "resolved" if u["via"] == "direct" else "heuristic"
        edges = _edges_at(builder, owner_id, rel, s.line) if s.kind == "ssrf" else []
        if edges:
            for e in edges:
                e.attrs = {**(e.attrs or {}), "url_from_input": u}
            st["edge_facts"] += 1
        else:
            what = ("DNS lookup of request input" if s.kind == "dns" else
                    f"outbound request {u['part']} comes from request input")
            fact = {"kind": "dns-input" if s.kind == "dns" else "ssrf", "line": s.line, "lib": s.lib,
                    "detail": f"{what} ({u['source']}.{u['key']})", "confidence": conf, "url_from_input": u}
            if n.attrs is None:
                n.attrs = {}
            lst = n.attrs.setdefault("insecure_transport", [])
            if any(x["kind"] == fact["kind"] and x["line"] == fact["line"] for x in lst):
                continue
            lst.append(fact)
            st["function_facts"] += 1
        st["checked" if u["checked"] else ("dns" if s.kind == "dns" else "host_controlled" if u["part"] in HOST_PARTS else "path_only")] += 1
        if u["via"] == "helper":
            st["via_helper"] += 1
    return {k: v for k, v in st.items() if v}

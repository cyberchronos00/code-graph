"""Guard facts beyond a route's own middleware: project-wide defaults, checks at the top of a handler, and routes
that are public by design (#47 part 2d). They feed `routes_report` (as guards, named for where they come from) and
the `unguarded` finding of `cg surface`.

Three things live here:

- `Facts.project_guards(...)`: framework-wide defaults read from the source tree (Django REST framework
  `REST_FRAMEWORK` defaults in settings, Laravel global / group middleware in `app/Http/Kernel.php` and
  `bootstrap/app.php`). Express / Koa `app.use(auth)` order and mount paths and Nest `APP_GUARD` / `useGlobalGuards`
  are already route facts from the extractors; the Nest `@Public()` opt-out is applied here.
- `scan_body(...)`: a line-level read of the first statements of a handler (TypeScript, JavaScript, Python, PHP):
  an early `return` / `throw` / `abort` on a missing user, session or permission is an inline guard; a call that
  might be an auth check but is not recognised makes the route uncertain.
- `public_reason(...)`: the one table of public-by-design routes (`PUBLIC_ROUTES`).

Nothing here runs under `--strict` (route-level facts only).
"""
from __future__ import annotations

import json
import os
import re

from . import presets
from .presets import skip_dirs

# --------------------------------------------------------------------------- public by design
# (reason, regex over the lower-cased path segments joined by "/"; a segment matches whole, leading "_" ignored)
_SEG = r"(?:^|/)"
PUBLIC_ROUTES: list[tuple[str, str]] = [
    ("sign-in / sign-up / password reset", _SEG + r"(?:login|logout|signin|sign-in|signout|sign-out|signup|sign-up|register|"
     r"registration|forgot-password|forgot_password|forgot|password-reset|reset-password|password_reset|reset_password|"
     r"verify-email|verify_email|confirm-email|csrf|csrf-token)(?:$|[/.])"),
    ("health check", _SEG + r"(?:health|healthz|healthcheck|health-check|health_check|ready|readyz|readiness|live|livez|"
     r"liveness|ping|status|up|alive)(?:$|[/.])"),
    ("robots / sitemap / favicon / manifest", _SEG + r"(?:robots\.txt|sitemap[\w-]*\.xml|favicon\.ico|opensearch\.xml|"
     r"manifest\.json|manifest\.webmanifest|security\.txt|humans\.txt|ads\.txt|apple-touch-icon[\w.-]*|browserconfig\.xml)$"),
    ("static assets", _SEG + r"(?:static|assets|public|media|_next|locales|fonts|images|img|css|js|dist)(?:$|[/.])"),
    ("jwks / .well-known", _SEG + r"(?:\.well-known|jwks(?:\.json)?|jwks[\w{}_]*json)(?:$|[/.])|jwks"),
    ("OAuth / OIDC callback or token endpoint", _SEG + r"(?:oauth\d*|oidc|openid|openid-configuration|callback|callbacks|"
     r"authorize|sso|saml|nextauth|\[\.\.\.nextauth\])(?:$|[/.])|/token$|/auth/callback"),
    ("media / thumbnails", _SEG + r"(?:thumbnails?|image|avatars?|favicons?|emoji|logo)(?:$|[/.])"),
    ("API schema / documentation", _SEG + r"(?:swagger(?:-ui)?|redoc|openapi(?:\.json|\.ya?ml)?|api-docs|apidocs)(?:$|[/.])|^/api/schema(?:$|/)"),
    ("utility endpoint (version / client IP / geolocation)", _SEG + r"(?:version|ip|geolocation|whoami-public)(?:$|[/.])"),
    ("scheduled job endpoint (cron key)", _SEG + r"cron(?:$|[/.])|^/api/tasks/(?:cron|cleanup)$"),
    ("test / fixture code", r"(?:^|/)(?:test|tests|__tests__|e2e|fixtures?|dummy_plugin)(?:$|[/.])|\.(?:test|spec)(?:$|[/.])"),
    ("tRPC adapter: access is checked per procedure (procedure middleware)", r"(?:^|/)api/trpc(?:/|$)"),
    ("Auth.js / NextAuth handler", r"\{nextauth\*?\}|\[\.\.\.nextauth\]"),
    ("embed pages", _SEG + r"embeds(?:$|[/.])"),
    ("shared link", r"^/(?:s|share|shared|shares|public)/"),
    ("site root", r"^/$"),
]
# reported at low severity, not hidden: an unauthenticated metrics endpoint is worth a look
LOW_ROUTES: list[tuple[str, str]] = [("metrics endpoint", _SEG + r"(?:metrics|prometheus|stats)(?:$|[/.])")]

TEST_FILE = re.compile(r"(?:^|/)(?:tests?|__tests__|e2e|spec|dummy_plugin)/|\.(?:test|spec)\.[jt]sx?$|(?:^|/)test_[^/]*\.py$|Test\.php$")
_PUB_RX = [(r, re.compile(p)) for r, p in PUBLIC_ROUTES]
_LOW_RX = [(r, re.compile(p)) for r, p in LOW_ROUTES]
_PARAM = re.compile(r"\{[^}]*\}|<[^>]*>|:[A-Za-z_]\w*|\[[^\]]*\]")


def _norm(path: str) -> str:
    p = (path or "").replace("{?}", "").split("?", 1)[0].lower()
    p = re.sub(r"/_(?=[a-z])", "/", p)
    return "/" + "/".join(s for s in p.split("/") if s)


def public_reason(method: str | None, uri: str | None, file: str | None = None) -> tuple[str | None, bool]:
    """(reason, hidden): why `uri` is public by design. `hidden` is False for the low-severity kinds (metrics)."""
    if file and TEST_FILE.search(file.replace("\\", "/")):
        return "test / fixture code", True
    if not uri:
        return None, True
    if uri.startswith("app/") or " " in uri.strip():
        return ("cache revalidation action", True) if re.search(r"#revalidate\w*$", uri) else (None, True)
    p = _norm(uri)
    for reason, rx in _PUB_RX:
        if rx.search(p) and not _write_with_param(method, uri, reason):
            return reason, True
    for reason, rx in _LOW_RX:
        if rx.search(p):
            return reason, False
    return None, True


def _write_with_param(method, uri, reason) -> bool:
    """A parameterised POST / PUT / DELETE under a public-looking prefix (`/static/{path}` is fine, `POST /public/{id}`
    is not)."""
    return reason in ("static assets", "shared link", "media / thumbnails") and (method or "").upper() in ("POST", "PUT", "PATCH", "DELETE")


# --------------------------------------------------------------------------- handler bodies
STRONG = (r"(?:session|authenticated|authentication|is_anonymous|isanonymous|logged|login|auth\b|auth\(|authorize|jwt|bearer|"
          r"token|permission|has_perm|hasperm|\bperm\b|\brole|isadmin|is_admin|is_staff|isstaff|is_superuser|superuser|"
          r"admin|current_?user|req\.user|request\.user|ctx\.state\.user|ctx\.user|socket\.(?:data\.)?user|"
          r"socket\.request\.(?:user|session)|get_\w*user|getuser|get_\w*session|\bcan\(|\bcannot\(|ability|"
          r"x-api-key|api[_-]?key|authorization)")
STRONG_RX = re.compile(STRONG, re.I)
EXIT_RX = re.compile(r"\b(?:return|throw|raise|abort|abort_if|abort_unless|redirect|disconnect|next)\b|res\.(?:status|sendStatus|"
                     r"redirect)|HTTPException|PermissionDenied|Unauthorized|Forbidden|ctx\.(?:throw|status)|"
                     r"(?:status|sendStatus)\(\s*(?:401|403)|status_code\s*=\s*(?:401|403)|\b40[13]\b", re.I)
IF_RX = re.compile(r"^\s*(?:\}\s*)?(?:else\s+)?(?:if|elif|unless)\b\s*(?P<cond>.*)$")
DIRECT_RX = re.compile(
    r"^\s*(?:(?:const|let|var|final|val)\s+[\w${}\[\],\s]+=\s*|[\w$.]+\s*=\s*)?(?:await\s+)?(?:[\w$.]+(?:->|::|\.))?"
    r"(?P<fn>(?:require|assert|ensure|authorize|authenticate|verify|check|protect|guard|validate|must|need|enforce)\w*|"
    r"\w*auth\w*(?:middleware|ize|enticate|required|check)\w*|abort_unless|abort_if|(?:\$this->)?authorize)\s*\(", re.I)
DIRECT_AUTH_RX = re.compile(r"(user|auth|login|admin|session|permission|role|token|access|policy|ability|signed|staff|member|"
                            r"owner|can$|abort_unless|abort_if|authorize|request|signature|apikey|api_key|secret|cron|header|"
                            r"credential|middleware)", re.I)
UNSURE_RX = re.compile(
    r"\b(?P<fn>[\w$]*(?:session|auth|login|token|permission|credential|current_?user|get_?user|verify|authorize|is_?admin|"
    r"role|principal|account)[\w$]*)\s*\(", re.I)
_NOT_UNSURE = re.compile(r"^(?:res|resp|response|req|request|json|console|logger|log|print|isinstance|len|str|int|"
                         r"setdefault|get_object_or_404|render|redirect|reverse)$", re.I)
_DEF_RX = re.compile(r"^\s*(?:@|(?:export\s+)?(?:async\s+)?(?:def|function)\b|(?:public|private|protected)\s+function|"
                     r"class\b|from\b|import\b)")
_COMMENT = re.compile(r"^\s*(?:#|//|\*|/\*|<\?php)")
BODY_LINES = 14
# middleware / decorators that are known not to check who the caller is: they do not make a route uncertain
PLAUSIBLE_CHECK = re.compile(r"auth|login|session|token|permission|guard|protect|secur|verif|acl|role|admin|user|jwt|"
                             r"access|allow|deny|restrict|owner|member|signed|secret", re.I)
BENIGN_GUARDS = re.compile(
    r"^(?:helmet|cors|compression|morgan|bodyparser|body-parser|multer|rate-?limit|throttle|cookie-?parser|favicon|serve-?static|"
    r"static|session|json|urlencoded|csrf|cache|etag|logger|nocache|methoddecorator|defaultresponder|express\.|asynchandler|"
    r"apexredirect|sharedomains|bodyparser|cache_page|never_cache|require_http_methods|require_get|require_post|require_safe|"
    r"csrf_exempt|ensure_csrf_cookie|vary_on|gzip_page|condition|xframe|x_frame|allowany|proxy|requesttracer|request_tracer|"
    r"middleware\b)", re.I)


def _code_lines(lines: list[str], start: int, limit: int, py: bool = False) -> list[tuple[int, str]]:
    """Up to `limit` code lines from 1-based `start`, ending where the handler ends: indentation drops back to the `def`
    (Python), or brackets opened by the handler's first line close again (everything else)."""
    out, depth, opened, base = [], 0, False, None
    ln = start
    while ln <= len(lines) and len(out) < limit:
        raw = lines[ln - 1]
        ln += 1
        if not raw.strip() or _COMMENT.match(raw):
            continue
        if py:
            ind = len(raw) - len(raw.lstrip())
            if base is None and re.match(r"\s*(?:async\s+)?def\b", raw):
                base = ind
            elif base is not None and ind <= base:
                break
        out.append((ln - 1, raw.rstrip()))
        if not py:
            code = re.sub(r"(['\"`])(?:\\.|(?!\1).)*\1", "", raw)
            depth += sum(code.count(c) for c in "{([") - sum(code.count(c) for c in "})]")
            opened = opened or any(c in code for c in "{([")
            if opened and depth <= 0:
                break
    return out


def scan_body(lines: list[str], start: int, end: int | None = None, limit: int = BODY_LINES, py: bool = False) -> dict:
    """`{"guard": {name, at, how} | None, "unsure": str | None}` for the handler starting at 1-based line `start`: the
    first statements are read for an early exit on a failed auth / permission check (`if (!session) return ...`,
    `if not request.user.is_authenticated: raise ...`, `abort_unless(auth()->check(), 403)`, a call to
    `requireUser()` / `authorize()`), or a wrapper `if (isAuthenticated(...)) { ... }` around the body."""
    code = _code_lines(lines, start, limit, py)
    if end:
        code = [(n, t) for n, t in code if n <= end]
    auth_vars: set[str] = set()
    unsure = None
    stmt = -1
    for i, (n, text) in enumerate(code):
        if not _DEF_RX.match(text):
            stmt += 1
        asg = re.match(r"\s*(?:const|let|var|final|val)?\s*(?:\{[^}]*\}|\[[^\]]*\]|[\w$.]+)\s*(?:\)|:\s*[\w\[\]|<>. ]+)?\s*=\s*(?P<rhs>.*)", text)
        if asg and STRONG_RX.search(asg.group("rhs")):
            for v in re.findall(r"[\w$]+", text.split("=", 1)[0]):
                if v not in ("const", "let", "var", "final", "val", "await", "async"):
                    auth_vars.add(v)
        m = IF_RX.match(text)
        if m:
            cond = m.group("cond")
            mentions = STRONG_RX.search(cond) or any(re.search(rf"(?<![\w$.]){re.escape(v)}(?![\w$])", cond) for v in auth_vars)
            tail = " ".join(t for _, t in code[i:i + 5])
            wrapper = bool(mentions) and not re.search(r"(?:^|[(\s])(?:!|not\b|\bnot\s)", cond) and stmt < 3 \
                and not re.search(r"==\s*null|===\s*null|is None|undefined|== false|=== false", cond)
            if mentions and EXIT_RX.search(tail):
                return {"guard": {"name": f"inline-check:{_label(cond)}", "at": f"{n}", "how": "early exit"}, "unsure": None}
            if mentions and wrapper:
                return {"guard": {"name": f"inline-check:{_label(cond)}", "at": f"{n}", "how": "wraps the body"}, "unsure": None}
        d = DIRECT_RX.match(text)
        if d and DIRECT_AUTH_RX.search(d.group("fn")) and not _DEF_RX.match(text):
            return {"guard": {"name": f"inline-check:{d.group('fn')}()", "at": f"{n}", "how": "call"}, "unsure": None}
        if unsure is None and not _DEF_RX.match(text) and (n - code[0][0]) < 10:
            u = UNSURE_RX.search(text)
            if u and not _NOT_UNSURE.match(u.group("fn")):
                unsure = f"calls {u.group('fn')}() at line {n}"
    return {"guard": None, "unsure": unsure}


def _label(cond: str) -> str:
    c = re.sub(r"\s+", " ", cond).strip()
    if c.startswith("("):
        depth = 0
        for i, ch in enumerate(c):
            depth += (ch == "(") - (ch == ")")
            if depth == 0:
                c = c[1:i]
                break
    c = c.rstrip("{:").strip()
    return re.sub(r"^(?:!|not\s+)\s*", "", c)[:48].strip()


# --------------------------------------------------------------------------- connection-level checks (Socket.IO)
_CONN_RX = re.compile(r"""\.on\(\s*['"](?:connection|connect)['"]""")
_USE_RX = re.compile(r"(?P<var>[\w$]+)\.use\(")
_OF_RX = re.compile(r"""(?P<var>[\w$]+)\s*=\s*[\w$.]+\.of\(\s*['"](?P<ns>[^'"]+)['"]""")
_REJECT_RX = re.compile(r"disconnect|next\(\s*new\s+Error|next\(\s*err|return\s+false|ConnectionRefusedError|raise\s+\w*Refused|"
                        r"socket\.close|emit\(\s*['\"]unauthori", re.I)


def connection_guard(lines: list[str], at_line: int, namespace: str | None, py: bool = False) -> dict | None:
    """A `connection` / `connect` handler or `ns.use(...)` middleware in this file that rejects unauthenticated sockets,
    for the namespace of the event handler at `at_line`."""
    ns = namespace or "/"
    var_ns = {m["var"]: m["ns"] for m in _OF_RX.finditer("\n".join(lines))}
    if py:
        for i, ln in enumerate(lines):
            if re.match(r"\s*@\w+\.(?:event|on\(\s*['\"]connect['\"]\s*\))", ln) and re.search(r"\bconnect\b", " ".join(lines[i:i + 3])):
                body = " ".join(t for _, t in _code_lines(lines, i + 1, 40, True))
                if re.search(r"ConnectionRefusedError|return\s+False", body) and STRONG_RX.search(body):
                    return {"name": "socket-connect:connect", "at": str(i + 1), "how": "connect handler rejects"}
        return None
    for i, ln in enumerate(lines):
        if _CONN_RX.search(ln):
            m = re.search(r"([\w$]+)\s*\.on\(", ln)
            v = m.group(1) if m else ""
            if var_ns.get(v, "/") != ns:
                continue
            block = _code_lines(lines, i + 1, 12)
            body = " ".join(t for _, t in block)
            if _REJECT_RX.search(body) and STRONG_RX.search(body):
                return {"name": "socket-connect:connection", "at": str(i + 1), "how": "connection handler rejects"}
        u = _USE_RX.search(ln)
        if u and var_ns.get(u["var"], "/") == ns and re.search(r"\(\s*(?:socket|sock|client)\b", ln):
            body = " ".join(t for _, t in _code_lines(lines, i + 1, 14))
            if _REJECT_RX.search(body) and STRONG_RX.search(body):
                return {"name": "socket-middleware:use", "at": str(i + 1), "how": "ns.use middleware rejects"}
    return None


# --------------------------------------------------------------------------- project-wide defaults
_SKIP = skip_dirs("common") | {"vendor", "venv", "dist", "build", "site-packages", "tests", "test"}
_NAME_LIST = re.compile(r"""['"]([\w.]+)['"]""")


def _walk(root: str, suffixes: tuple[str, ...], pick, limit: int = 4000):
    seen = 0
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in _SKIP and not x.startswith(".")]
        for f in files:
            if f.endswith(suffixes):
                seen += 1
                if seen > limit:
                    return
                p = os.path.join(d, f)
                if pick(p, f):
                    yield p


def _read(p: str) -> str:
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _drf_defaults(root: str) -> dict:
    out = {"permission": [], "authentication": [], "at": None}
    for p in _walk(root, (".py",), lambda p, f: "settings" in p.lower() or f in ("base.py", "config.py")):
        txt = _read(p)
        if "REST_FRAMEWORK" not in txt:
            continue
        for key, k in (("DEFAULT_PERMISSION_CLASSES", "permission"), ("DEFAULT_AUTHENTICATION_CLASSES", "authentication")):
            m = re.search(rf"['\"]{key}['\"]\s*:\s*[\[(]([^\])]*)[\])]", txt, re.S)
            if m:
                body = re.sub(r"#.*", "", m.group(1))
                out[k] += [n.split(".")[-1] for n in _NAME_LIST.findall(body)]
                out["at"] = out["at"] or os.path.relpath(p, root)
    return out


def _php_names(blob: str) -> list[str]:
    out = []
    for m in re.finditer(r"""(?:\\?(?:[\w]+\\)*(\w+)::class|['"]([\w:.,\-]+)['"])""", blob):
        out.append(m.group(1) or m.group(2))
    return out


def _laravel_defaults(root: str) -> dict:
    """{"global": [names], "groups": {"api": [names], "web": [...]}} from Kernel.php and bootstrap/app.php."""
    out: dict = {"global": [], "groups": {}, "at": {}}
    kernel = os.path.join(root, "app", "Http", "Kernel.php")
    if os.path.isfile(kernel):
        txt = _read(kernel)
        m = re.search(r"\$middleware\s*=\s*\[(.*?)\];", txt, re.S)
        if m:
            out["global"] += _php_names(m.group(1))
        g = re.search(r"\$middlewareGroups\s*=\s*\[(.*)\];", txt, re.S)
        if g:
            for gm in re.finditer(r"""['"](\w+)['"]\s*=>\s*\[(.*?)\]""", g.group(1), re.S):
                out["groups"].setdefault(gm.group(1), []).extend(_php_names(gm.group(2)))
        out["at"]["kernel"] = "app/Http/Kernel.php"
    boot = os.path.join(root, "bootstrap", "app.php")
    if os.path.isfile(boot):
        txt = _read(boot)
        i = txt.find("withMiddleware")
        if i >= 0:
            t = txt[i:]
            for m in re.finditer(r"->(?:append|prepend)\(\s*(\[.*?\]|[^;]*?)\)\s*;", t, re.S):
                out["global"] += _php_names(m.group(1))
            for m in re.finditer(r"->(web|api)\(([^;]*?)\)\s*;", t, re.S):
                out["groups"].setdefault(m.group(1), []).extend(_php_names(m.group(2)))
            for m in re.finditer(r"->(?:append|prepend)ToGroup\(\s*['\"](\w+)['\"]\s*,\s*(\[.*?\]|[^;]*?)\)\s*;", t, re.S):
                out["groups"].setdefault(m.group(1), []).extend(_php_names(m.group(2)))
            out["at"]["bootstrap"] = "bootstrap/app.php"
    return out


PUBLIC_DECORATORS = re.compile(r"^(?:public|ispublic|skipauth|skip_auth|noauth|no_auth|allowanonymous|allow_anonymous|anonymous|"
                               r"unprotected|skipjwtauth|publicroute|allowunauthenticated|optionalauth)$", re.I)


class Facts:
    """Per-graph cache: the source roots, project-wide defaults and file contents."""

    def __init__(self, st):
        from .routes import _meta_of
        meta = st.meta()
        self.st = st
        self.roots: dict[str | None, str] = {}
        if meta.get("repos"):
            for r in meta["repos"]:
                m = _meta_of((meta.get("sources") or {}).get(r))
                if m.get("root"):
                    self.roots[r] = m["root"]
            self.repos = list(meta["repos"])
        else:
            self.repos = []
            if meta.get("root"):
                self.roots[None] = meta["root"]
        self._lines: dict[str, list[str] | None] = {}
        self._defaults: dict = {}

    def _split(self, file: str | None) -> tuple[str | None, str | None]:
        if not file:
            return None, None
        if self.repos and "/" in file and file.split("/", 1)[0] in self.repos:
            r, rest = file.split("/", 1)
            return r, rest
        return None, file

    def lines(self, file: str | None) -> list[str] | None:
        repo, rel = self._split(file)
        root = self.roots.get(repo)
        if not root or not rel:
            return None
        key = f"{repo}\0{rel}"
        if key not in self._lines:
            p = os.path.join(root, rel)
            self._lines[key] = _read(p).splitlines() if os.path.isfile(p) else None
        return self._lines[key]

    def _for(self, repo: str | None, name: str, fn):
        key = (repo, name)
        if key not in self._defaults:
            root = self.roots.get(repo)
            self._defaults[key] = fn(root) if root else None
        return self._defaults[key]

    def repo_of(self, file: str | None) -> str | None:
        return self._split(file)[0]

    def drf(self, file: str | None):
        return self._for(self.repo_of(file), "drf", _drf_defaults)

    def laravel(self, file: str | None):
        return self._for(self.repo_of(file), "laravel", _laravel_defaults)

    # ------------------------------------------------------------------ route-level refinement
    def project_guards(self, attrs: dict, file: str | None, is_auth) -> list[dict]:
        """Framework-wide default guards that apply to a route: dicts with name, kind, source, auth."""
        out: list[dict] = []
        fw = attrs.get("framework")
        if fw == "drf" or attrs.get("drf"):
            d = self.drf(file)
            access = [a for a in attrs.get("access") or [] if isinstance(a, dict)]
            if d and not any("permission_classes" in str(a.get("via")) for a in access):
                real = [n for n in d["permission"] if n != "AllowAny"]
                if real:
                    out.append(self._guard(f"drf-default:{real[0]}", f"REST_FRAMEWORK DEFAULT_PERMISSION_CLASSES in {d['at']}"))
                elif not d["permission"]:
                    auths = [n for n in d["authentication"] if is_auth(n)]
                    if auths and not access:
                        out.append(self._guard(f"drf-default:{auths[0]}",
                                               f"REST_FRAMEWORK DEFAULT_AUTHENTICATION_CLASSES in {d['at']}"))
        if fw == "laravel" or (str(file).endswith(".php") and "routes/" in str(file)):
            d = self.laravel(file)
            if d:
                base = os.path.basename(file or "")
                group = "api" if base.startswith("api") else "web" if base.startswith("web") else None
                where = ", ".join(d["at"].values())
                for nm in d["global"]:
                    if is_auth(nm):
                        out.append(self._guard(f"laravel-global:{nm}", f"global middleware ({where})"))
                for nm in d["groups"].get(group, []) if group else []:
                    if is_auth(nm):
                        out.append(self._guard(f"laravel-group:{group}:{nm}", f"'{group}' middleware group ({where})"))
        return out

    @staticmethod
    def _guard(name: str, source: str, auth: bool = True) -> dict:
        g = {"name": name, "kind": "framework-default", "source": source, "auth": auth, "secret": False}
        if auth:
            g["auth_by"] = "framework default"
        return g

    def nest_public(self, attrs: dict, guards: list[dict], guard_file) -> tuple[list[dict], str | None]:
        """A Nest route marked @Public()-style whose guards read that decorator: the guards that do are dropped and the
        decorator is the reason it is public. `guard_file(name)` returns the guard's source path."""
        decos = [d for d in attrs.get("decorators") or [] if isinstance(d, str) and PUBLIC_DECORATORS.match(d)]
        if attrs.get("framework") != "nest" or not decos:
            return guards, None
        keep = []
        opted = False
        for g in guards:
            src = guard_file(g["name"]) if g.get("auth") else None
            txt = "\n".join(self.lines(src) or []) if src else ""
            reads = bool(txt) and (re.search(rf"\b{re.escape(decos[0])}\b", txt, re.I) or re.search(r"IS_PUBLIC|isPublic|IS_PUBLIC_KEY", txt))
            if reads:
                opted = True
            else:
                keep.append(g)
        return (keep, f"@{decos[0]}() opt-out of the global guard") if opted else (guards, None)

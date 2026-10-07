"""Webhook subscriptions and callbacks (#152 part B), called from `webhooks.py`.

  stored events      a sender whose event name comes from a stored subscription row (`$subscription->event`,
                     `webhook.event_type`, a `sub.events` loop) sends one `endpoint:webhook:<app>:<event>` per event the
                     repo declares for those rows: a validation enum / const list (`$casts`, `Rule::enum`, `z.enum`,
                     `WEBHOOK_EVENTS`), a config `events` list, or seeder / factory / fixture / migration data. The first
                     of these three sources that names events wins; no source: the sender keeps `{event}`.
  subscriber URLs    URLs that seed data, config and `.env.example` give as subscriber targets are recorded on the sender
                     endpoints (`attrs.subscriber_urls`); `cg link` pairs them with the other repo's routes.
  REGISTERS_CALLBACK a URL built from the app's own base (`config('app.url') . '/…'`, `url('/…')`, `route('name')`,
                     `${process.env.APP_URL}/…`, `settings.BASE_URL + '/…'`) that resolves to one of the app's routes and is
                     sent out under a callback body key or to an SDK `webhooks.create(url=…)` call.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

from . import presets
from .core.model import EXACT

SKIP_DIRS = presets.skip_dirs("common", "scan_skip_dirs")
DATA_EXT = (".php", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".py", ".json", ".yml", ".yaml", ".sql")
MAX_BYTES = 300_000
MAX_FILES = 6000
MAX_EVENTS = 25
STR = r"""(?:'([^'\\\n]*)'|"([^"\\\n]*)")"""
EVENT_KEYS = r"event_type|eventType|trigger_?[Ee]vent|event_name|eventName|events|event"
TEST_PATH = re.compile(r"(?:^|/)(?:tests?|__tests__|spec)/|(?:^|/)test_[^/]*\.py$|_tests?\.py$|\.(?:spec|test)\.[cm]?[jt]sx?$|Test\.php$")
NAMEISH = re.compile(r"(?i)(webhook|subscri|hook)")
EVENTISH = re.compile(r"(?i)(event|topic)")
SUB_OBJ = re.compile(r"(?i)sub|hook|endpoint|listener|target|receiver|row|record|registration|dest|client|consumer")
HINT_RX = re.compile(
    rf"(?<![\w$])\$?(\w+)\s*(?:->|\?->|\?\.|\.)\s*({EVENT_KEYS})\b(?!\s*\()|"
    rf"(?<![\w$])\$?(\w+)\s*\[\s*['\"]({EVENT_KEYS})['\"]\s*\]")
HINT_SKIP = {"this", "self", "cls", "request", "req", "payload", "data", "body", "event", "evt", "message", "msg", "props", "config"}
URL_KEYS = r"url|target_url|targetUrl|subscriber_url|subscriberUrl|webhook_url|webhookUrl|endpoint|endpoint_url|endpointUrl|callback_url|destination_url|payload_url"
URL_VAL = r"https?://[^\s'\"`,)\]}]+"
ENV_URL = re.compile(r"(?m)^[ \t]*([A-Z0-9_]*(?:WEBHOOK|SUBSCRIBER)[A-Z0-9_]*URL[A-Z0-9_]*|[A-Z0-9_]*(?:WEBHOOK|SUBSCRIBER)_(?:TARGET|ENDPOINT)[A-Z0-9_]*)\s*=\s*['\"]?(" + URL_VAL + ")")
CALLBACK_KEYS = ("callback_url", "callbackUrl", "callback", "notify_url", "notifyUrl", "notification_url", "notificationUrl",
                 "webhook_url", "webhookUrl", "webhook", "postback_url", "ipn_url", "hook_url")
KEY_SITE = re.compile(r"(?<![\w$>.])['\"]?(" + "|".join(CALLBACK_KEYS) + r")['\"]?\s*(=>|:|=)(?![=>])\s*")
SDK_REG = re.compile(r"(?:\.|->)\s*webhooks?\s*(?:\.|->)\s*(?:create|register|subscribe|add|set|update)\s*\(")
SDK_URL = re.compile(r"(?<![\w$>.])['\"]?url['\"]?\s*(=>|:|=)(?![=>])\s*")
BASE_PHP = (r"(?:config\(\s*['\"]app\.url['\"][^()]*\)|env\(\s*['\"]APP_URL['\"][^()]*\)|getenv\(\s*['\"]APP_URL['\"]\s*\)|"
            r"\$_ENV\[\s*['\"]APP_URL['\"]\s*\])")
BASE_TS = (r"(?:process\.env\.APP_URL|process\.env\[\s*['\"]APP_URL['\"]\s*\]|env\.APP_URL|"
           r"config\.(?:appUrl|baseUrl|APP_URL|BASE_URL))")
BASE_PY = (r"(?:settings\.(?:BASE_URL|SITE_URL|APP_URL|PUBLIC_URL)|os\.environ\[\s*['\"]APP_URL['\"]\s*\]|"
           r"os\.(?:environ\.get|getenv)\(\s*['\"]APP_URL['\"][^()]*\))")
BASES = {".php": re.compile(BASE_PHP), ".py": re.compile(BASE_PY)}
BASE_JS = re.compile(BASE_TS)
OUTBOUND = re.compile(r"\bfetch\s*\(|\baxios\b|\bgot\s*\(|\brequests\s*\.\s*\w+\s*\(|\bhttpx\b|\bHttp\s*::|->\s*(?:post|put|patch)\s*\(|"
                      r"\.\s*(?:post|put|patch)\s*\(|\bcurl_setopt|\burlopen\s*\(|\bClient\s*\(|\bsession\s*\.\s*\w+\s*\(")


def _strs(text: str) -> list[str]:
    return [a or b for a, b in re.findall(STR, text) if (a or b)]


def _top_split(text: str, sep: str) -> list[str]:
    out, cur, depth, q = [], "", 0, None
    for ch in text:
        if q:
            cur += ch
            if ch == q:
                q = None
            continue
        if ch in "'\"`":
            q = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    out.append(cur)
    return out


def _expr_after(text: str, i: int, limit: int = 400) -> str:
    """The expression starting at `i` up to a top-level `,` / closer / `;` / end of line outside brackets."""
    depth, q, j = 0, None, i
    end = min(len(text), i + limit)
    while j < end:
        ch = text[j]
        if q:
            if ch == "\\":
                j += 2
                continue
            if ch == q:
                q = None
        elif ch in "'\"`":
            q = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and ch in ",;":
            break
        elif depth == 0 and ch == "\n" and text[i:j].strip() and not text[i:j].rstrip().endswith((".", "+", "(")) \
                and not text[j + 1:j + 40].lstrip().startswith((".", "+")):
            break
        j += 1
    return text[i:j].strip()


def _depth_at(text: str, lo: int, pos: int) -> int:
    d, q = 0, None
    for ch in text[lo:pos]:
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch in "([{":
            d += 1
        elif ch in ")]}":
            d -= 1
    return d


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


class Subs:
    def __init__(self, wh):
        self.wh = wh
        self.root = wh.root
        self._files = None
        self._events = None
        self._urls = None
        self.callers = {}
        self.sender_eps: set[str] = set()

    # ------------------------------------------------------------ files
    def files(self) -> dict[str, str]:
        if self._files is None:
            self._files = {}
            n = 0
            for dp, dn, fn in os.walk(self.root):
                dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))
                for x in sorted(fn):
                    rel = os.path.relpath(os.path.join(dp, x), self.root).replace(os.sep, "/")
                    if not (x.endswith(DATA_EXT) or x.startswith(".env") or x.endswith(".env")):
                        continue
                    if TEST_PATH.search(rel) or x.endswith((".d.ts", ".min.js", ".lock")):
                        continue
                    try:
                        if os.path.getsize(os.path.join(dp, x)) > MAX_BYTES:
                            continue
                        self._files[rel] = open(os.path.join(dp, x), encoding="utf-8", errors="replace").read()
                    except OSError:
                        continue
                    n += 1
                    if n >= MAX_FILES:
                        return self._files
        return self._files

    # ------------------------------------------------------------ 1. stored event names
    def _declared(self):
        """name -> (values, file, line, kind) for enums, const lists and union types of strings."""
        decl = {}
        for rel, t in self.files().items():
            if rel.endswith(".php"):
                for m in re.finditer(r"\benum\s+(\w+)\s*(?::\s*\w+)?[^{;]*\{(.*?)^\}", t, re.S | re.M):
                    vals = [a or b for a, b in re.findall(r"\bcase\s+\w+\s*=\s*" + STR, m.group(2))]
                    if vals:
                        decl.setdefault(m.group(1), (vals, rel, _line(t, m.start()), "enum"))
                for m in re.finditer(r"\bconst\s+(\w+)\s*=\s*\[([^\]]*)\]|\$(\w+)\s*=\s*\[([^\]]*)\]\s*;", t):
                    name, body = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                    vals = _strs(body)
                    if vals:
                        decl.setdefault(name, (vals, rel, _line(t, m.start()), "const list"))
            elif rel.endswith((".ts", ".tsx", ".js", ".mjs", ".cjs")):
                for m in re.finditer(r"\benum\s+(\w+)\s*\{([^}]*)\}", t):
                    vals = [a or b for a, b in re.findall(r"\w+\s*=\s*" + STR, m.group(2))]
                    if vals:
                        decl.setdefault(m.group(1), (vals, rel, _line(t, m.start()), "enum"))
                for m in re.finditer(r"\bconst\s+(\w+)\s*(?::[^=\n]+)?=\s*\[([^\]]*)\]", t):
                    vals = _strs(m.group(2))
                    if vals:
                        decl.setdefault(m.group(1), (vals, rel, _line(t, m.start()), "const list"))
                for m in re.finditer(r"\bconst\s+(\w+)\s*=\s*\{([^}]*)\}\s*as\s+const", t):
                    vals = [a or b for a, b in re.findall(r":\s*" + STR, m.group(2))]
                    if vals:
                        decl.setdefault(m.group(1), (vals, rel, _line(t, m.start()), "const object"))
                for m in re.finditer(r"\btype\s+(\w+)\s*=\s*((?:\s*\|?\s*" + STR + r")+)\s*;?", t):
                    vals = _strs(m.group(2))
                    if vals:
                        decl.setdefault(m.group(1), (vals, rel, _line(t, m.start()), "union type"))
            elif rel.endswith(".py"):
                for m in re.finditer(r"^class\s+(\w+)\s*\([^)]*Enum[^)]*\)\s*:\s*\n((?:[ \t]+[^\n]*\n?)+)", t, re.M):
                    vals = [a or b for a, b in re.findall(r"(?m)^\s+\w+\s*=\s*" + STR, m.group(2))]
                    if vals:
                        decl.setdefault(m.group(1), (vals, rel, _line(t, m.start()), "enum"))
                for m in re.finditer(r"(?m)^[ \t]*(\w+)\s*(?::[^=\n]+)?=\s*[\[\(]([^\]\)]*)[\]\)]", t):
                    vals = _strs(m.group(2))
                    if vals:
                        decl.setdefault(m.group(1), (vals, rel, _line(t, m.start()), "const list"))
        return decl

    def _linked(self) -> set[str]:
        """Enum / list names the repo ties to subscription rows: model `$casts`, `Rule::enum`, `Rule::in`, `z.enum`."""
        out = set()
        for rel, t in self.files().items():
            if rel.endswith(".php"):
                for m in re.finditer(rf"['\"](?:{EVENT_KEYS})['\"]\s*=>\s*(?:AsEnumCollection::of\(\s*|AsEnumArrayObject::of\(\s*)?(\w+)::class", t):
                    out.add(m.group(1))
                for m in re.finditer(r"Rule::enum\(\s*(\w+)::class", t):
                    out.add(m.group(1))
                for m in re.finditer(r"Rule::in\(\s*(?:\w+::)?(\w+)\s*\)|Rule::in\(\s*\w+::(\w+)\s*\)", t):
                    out.add(m.group(1) or m.group(2))
                for m in re.finditer(r"in_array\([^;]*?(?:self|static|\w+)::(\w+)", t):
                    out.add(m.group(1))
            elif rel.endswith((".ts", ".tsx", ".js", ".mjs")):
                for m in re.finditer(r"\bz\.(?:native)?[eE]num\(\s*(\w+)", t):
                    out.add(m.group(1))
                for m in re.finditer(rf"\b(?:{EVENT_KEYS})\s*:\s*(\w+)\b", t):
                    out.add(m.group(1))
            elif rel.endswith(".py"):
                for m in re.finditer(rf"(?m)^\s+(?:{EVENT_KEYS})\s*:\s*(?:list\[|List\[|Optional\[)?(\w+)", t):
                    out.add(m.group(1))
        return out

    def _seed_events(self) -> list[tuple[str, int, str]]:
        found = []
        pat = re.compile(rf"""(?<![\w$])['"]?({EVENT_KEYS})['"]?\s*(?:=>|:|=)\s*(\[[^\]]*\]|{STR})""")
        enum_rx = re.compile(rf"""->(?:enum|set)\(\s*['"](?:event|event_type|events)['"]\s*,\s*\[([^\]]*)\]""")
        default_rx = re.compile(rf"""->string\(\s*['"](?:event|event_type)['"][^;]*?->default\(\s*{STR}\s*\)""")
        for rel, t in self.files().items():
            if not re.search(r"(?i)seed|factor|fixture|migrat|database/|\.sql$|\.json$|\.ya?ml$", rel):
                continue
            if not re.search(r"(?i)webhook|subscription", t + rel):
                continue
            if rel.endswith(("package.json", "composer.json", "composer.lock", "package-lock.json", "tsconfig.json")):
                continue
            for m in enum_rx.finditer(t):
                for v in _strs(m.group(1)):
                    found.append((v, _line(t, m.start()), rel))
            for m in default_rx.finditer(t):
                found.append((m.group(1) or m.group(2), _line(t, m.start()), rel))
            for m in pat.finditer(t):
                val = m.group(2)
                vals = _strs(val) if val.startswith("[") else [m.group(3) or m.group(4)]
                for v in vals:
                    if v:
                        found.append((v, _line(t, m.start()), rel))
        return found

    def _config_events(self) -> list[tuple[str, int, str]]:
        found = []
        pat = re.compile(r"""(?<![\w$])['"]?events['"]?\s*(?:=>|:|=)\s*\[([^\]]*)\]""")
        for rel, t in self.files().items():
            if not re.search(r"(?i)(?:^|/)(?:config|settings)[^/]*(?:/|\.)|webhook", rel):
                continue
            if "webhook" not in (t + rel).lower() or re.search(r"(?i)seed|factor|fixture|migrat", rel):
                continue
            if rel.endswith(("package.json", "composer.json")):
                continue
            for m in pat.finditer(t):
                body = m.group(1)
                vals = re.findall(STR + r"\s*=>", body) and [a or b for a, b in re.findall(STR + r"\s*=>", body)] or _strs(body)
                for v in vals:
                    found.append((v, _line(t, m.start()), rel))
        return found

    def events(self) -> tuple[list[str], str] | None:
        if self._events is None:
            self._events = ([], "")
            decl, linked = self._declared(), self._linked()
            pick = []
            for name, (vals, rel, line, kind) in decl.items():
                if (NAMEISH.search(name) and EVENTISH.search(name)) or name in linked:
                    pick.append((name, vals, rel, line, kind))
            pick.sort(key=lambda x: (x[2], x[3]))
            if pick:
                names = list(dict.fromkeys(v for _n, vs, *_ in pick for v in vs))
                src = ", ".join(f"{kind} {name} ({rel}:{line})" for name, _vs, rel, line, kind in pick[:3])
                self._events = (names, src)
            else:
                for rows in (self._config_events(), self._seed_events()):
                    rows = [r for r in rows if re.fullmatch(r"[A-Za-z][\w.:/-]{0,79}", r[0])]
                    if rows:
                        names = list(dict.fromkeys(r[0] for r in rows))
                        srcs = list(dict.fromkeys(f"{r[2]}:{r[1]}" for r in rows))[:3]
                        self._events = (names, ", ".join(srcs))
                        break
        names, src = self._events
        return (names[:MAX_EVENTS], src) if names else None

    def stored(self, src: str) -> tuple[list[str], str] | None:
        """Event names for a sender whose event comes from a stored subscription row; None when nothing hints at one."""
        if not self._hint(src):
            return None
        return self.events()

    def _hint(self, nid: str) -> bool:
        bd = self.wh.body(nid)
        if bd is None:
            return False
        file, text, lo, hi = bd
        if self._hit(text[lo:hi]):
            return True
        for c in self.callers.get(nid, ()):
            cn = self.wh.b.nodes.get(c)
            if cn is None or not cn.file:
                continue
            for d, _k, ln, _cf in self.wh.callees.get(c, ()):
                if d == nid and ln:
                    csrc = self.wh.s.text(cn.file)
                    pos = self.wh.s.off(cn.file, ln)
                    if self._hit(csrc[pos:pos + 400]):
                        return True
        return False

    @staticmethod
    def _hit(text: str) -> bool:
        for m in HINT_RX.finditer(text):
            obj, field = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            if obj in HINT_SKIP:
                continue
            if field != "event" or SUB_OBJ.search(obj):
                return True
        return False

    # ------------------------------------------------------------ 2. subscriber URLs
    def urls(self) -> list[dict]:
        if self._urls is None:
            self._urls, seen = [], set()
            kv = re.compile(rf"""(?<![\w$])['"]?(?:{URL_KEYS})['"]?\s*(?:=>|:|=)\s*['"`]({URL_VAL})""")
            for rel, t in self.files().items():
                base = rel.rsplit("/", 1)[-1]
                if base.startswith(".env") or base.endswith(".env"):
                    hits = [(m.group(2), m.start()) for m in ENV_URL.finditer(t)]
                elif re.search(r"(?i)seed|factor|fixture|migrat|database/|config|webhook|subscri|\.json$|\.ya?ml$", rel) \
                        and re.search(r"(?i)webhook|subscri", t + rel) and not base.endswith(("package.json", "composer.json", "tsconfig.json")):
                    hits = [(m.group(1), m.start()) for m in kv.finditer(t)]
                else:
                    continue
                for url, pos in hits:
                    url = url.rstrip("'\"`;")
                    if url in seen:
                        continue
                    seen.add(url)
                    self._urls.append({"url": url, "at": f"{rel}:{_line(t, pos)}"})
        return self._urls

    def finish(self):
        """Subscriber URLs onto the sender endpoints; REGISTERS_CALLBACK edges."""
        urls = self.urls()
        if urls and self.sender_eps:
            for nid in sorted(self.sender_eps):
                self.wh.b.nodes[nid].attrs["subscriber_urls"] = urls[:10]
            self.wh.st["webhook_subscriber_urls"] = len(urls[:10])
        self.callbacks()

    # ------------------------------------------------------------ 3. REGISTERS_CALLBACK
    def _routes(self):
        out = []
        for rid, n in self.wh.b.nodes.items():
            if n.kind != "route":
                continue
            a = n.attrs or {}
            uri, method = a.get("uri"), a.get("method")
            if not uri or not method:
                continue
            uris = [("as-declared", uri)]
            if (n.file or "").startswith(("routes/api.php", "routes/api/")):
                uris.append(("api-prefixed", "/api" + uri))
            out.append({"id": rid, "uri": uri, "method": method, "uris": uris, "name": a.get("name")})
        return out

    def _own_path(self, expr: str, ext: str, ctx: str):
        """('path', '/a/{x}') or ('route', name) for an expression built from the app's own base; None otherwise."""
        e = expr.strip()
        if not e:
            return None
        if ext == ".php":
            m = re.search(r"\broute\(\s*['\"]([\w.\-]+)['\"]", e)
            if m and not re.search(r"https?://", e):
                return ("route", m.group(1))
            m = re.search(r"(?:\burl\(\s*\)\s*->\s*(?:to|secure)\s*\(|(?<![\w>])(?:url|secure_url)\(|\bURL::(?:to|secure)\()", e)
            if m:
                inner = self._call_args(e, m.end() - 1)
                parts = _top_split(inner, ",")[0] if inner else ""
                path = self._concat(parts, ".", "")
                return ("path", path) if path else None
        base = (BASES.get(ext) or BASE_JS)
        if ext in (".ts", ".tsx", ".js", ".mjs", ".cjs") and e.startswith("`"):
            m = re.match(r"`\$\{\s*" + BASE_TS + r"\s*\}(.*)`$", e, re.S)
            if m:
                return self._tmpl(m.group(1), r"\$\{[^}]*\}")
            return None
        if ext == ".py":
            m = re.match(r"""f(['"])\{\s*""" + BASE_PY + r"""\s*\}(.*)\1$""", e, re.S)
            if m:
                return self._tmpl(m.group(2), r"\{[^}]*\}")
            m = re.match(r"urljoin\(\s*" + BASE_PY + r"\s*,\s*(.+)\)$", e, re.S)
            if m:
                path = self._concat(m.group(1), "+", "")
                return ("path", path) if path else None
        m = base.search(e)
        if not m:
            return None
        rest = e[m.end():]
        rest = re.sub(r"^\s*(?:,[^()]*)?\)*", "", rest)
        rest = rest.lstrip()
        sep = "." if ext == ".php" else "+"
        if not rest.startswith(sep):
            return None
        path = self._concat(rest[1:], sep, "")
        return ("path", path) if path else None

    @staticmethod
    def _call_args(text: str, open_idx: int) -> str:
        depth, q = 0, None
        for j in range(open_idx, len(text)):
            ch = text[j]
            if q:
                if ch == q:
                    q = None
            elif ch in "'\"":
                q = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[open_idx + 1:j]
        return text[open_idx + 1:]

    @staticmethod
    def _tmpl(rest: str, ph: str):
        p = re.sub(ph, "{x}", rest).split("?")[0]
        return ("path", p) if p.startswith("/") else None

    @staticmethod
    def _concat(text: str, sep: str, _unused: str) -> str | None:
        out = ""
        for piece in _top_split(text, sep):
            p = piece.strip()
            m = re.fullmatch(STR, p)
            out += (m.group(1) or m.group(2) or "") if m else "{x}"
        out = out.split("?")[0]
        return out if out.startswith("/") else None

    def _resolve_var(self, expr: str, text: str, lo: int, pos: int) -> str:
        """`$url` / `callbackUrl`: the last assignment before `pos` in the same function."""
        m = re.fullmatch(r"\$?([A-Za-z_]\w*)", expr.strip())
        if not m:
            return expr
        v = re.escape(m.group(1))
        last = None
        for a in re.finditer(rf"(?:\$|\b(?:const|let|var)\s+|(?<![\w$.>]))({v})\s*(?::[^=\n]+)?=(?![=>])\s*", text[lo:pos]):
            last = lo + a.end()
        return _expr_after(text, last) if last else expr

    def _own_route(self, parsed, routes):
        from .link import match_endpoint
        kind, val = parsed
        if kind == "route":
            hit = [r for r in routes if r["name"] == val]
            return (hit[0]["id"], EXACT) if len(hit) == 1 else None
        res = match_endpoint("POST", val, routes, "api")
        if len(res["matched"]) == 1:
            m = res["matched"][0]
            return m["route"], m["confidence"]
        return None

    def callbacks(self):
        routes = self._routes()
        if not routes:
            return
        http_by_fn = defaultdict(list)
        for e in self.wh.b.edges.values():
            if e.kind == "HTTP_CALLS":
                http_by_fn[e.src].append(e)
        for nid, n in list(self.wh.b.nodes.items()):
            if n.kind not in ("function", "method") or self.wh.is_test_node(n):
                continue
            bd = self.wh.body(nid)
            if bd is None:
                continue
            file, src, lo, hi = bd
            ext = os.path.splitext(file)[1]
            if ext not in (".php", ".py", ".ts", ".tsx", ".js", ".mjs"):
                continue
            text = src[lo:hi]
            if not (KEY_SITE.search(text) or SDK_REG.search(text)):
                continue
            sites = []
            for m in KEY_SITE.finditer(text):
                if m.group(2) == "=" and _depth_at(text, 0, m.start()) <= 0:
                    continue
                sites.append((m.group(1), lo + m.end(), lo + m.start(), None))
            for m in SDK_REG.finditer(text):
                args = self._call_args(src, lo + m.end() - 1)
                a0 = lo + m.end()
                for u in SDK_URL.finditer(args):
                    sites.append(("url", a0 + u.end(), a0 + u.start(), "sdk"))
            for key, vpos, kpos, mode in sites:
                if self.wh.s.masked(file, kpos):
                    continue
                expr = self._resolve_var(_expr_after(src, vpos), src, lo, kpos)
                parsed = self._own_path(expr, ext, text)
                if not parsed:
                    continue
                hit = self._own_route(parsed, routes)
                if not hit:
                    continue
                rid, conf = hit
                ep = None
                if mode != "sdk":
                    ep = self._outbound(nid, key, http_by_fn)
                    if ep is False:
                        continue
                if (nid, rid, key) in self.wh.done:
                    continue
                self.wh.done.add((nid, rid, key))
                attrs = {"body_key": key}
                if ep:
                    attrs["endpoint"] = ep
                if mode == "sdk":
                    attrs["via"] = "SDK webhook registration"
                line = self.wh.s.line_of(file, kpos)
                self.wh.b.add_edge(nid, rid, "REGISTERS_CALLBACK", file=file, line=line, confidence=conf,
                                   **attrs)
                self.wh.st["webhook_callbacks"] += 1

    def _outbound(self, nid: str, key: str, http_by_fn):
        """The outbound endpoint a body key goes with (a node id), None when sent but unknown, False when not sent out."""
        for e in http_by_fn.get(nid, ()):
            keys = ((e.attrs or {}).get("body_keys") or {}).get("keys") or []
            names = {k if isinstance(k, str) else (k.get("name") or k.get("key")) for k in keys}
            if key in names:
                return e.dst
        own = [e.dst for e in http_by_fn.get(nid, ())]
        if len(set(own)) == 1:
            return own[0]
        bd = self.wh.body(nid)
        if bd and OUTBOUND.search(bd[1], bd[2], bd[3]):
            return None
        for f in self.wh.tree([nid], 2)[1:]:
            if http_by_fn.get(f):
                return None
            b2 = self.wh.body(f)
            if b2 and OUTBOUND.search(b2[1], b2[2], b2[3]):
                return None
        return False

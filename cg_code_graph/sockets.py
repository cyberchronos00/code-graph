"""Raw TCP / UDP sockets paired by port (#39, epic #29): `endpoint:tcp:<port>` / `endpoint:udp:<port>`.

A source scan over the built graph (every language with function nodes) finds listening and connecting sockets:

  listen    Python `socket.bind((h, p))` / `create_server` / `socketserver.TCPServer((h, p), Handler)` /
            `asyncio.start_server(cb, h, p)` / `loop.create_server(F, h, p)` / `create_datagram_endpoint(F,
            local_addr=(h, p))`; Node `net.createServer(cb).listen(p, h)` / `dgram.createSocket(..).bind(p, h)`;
            Rust `TcpListener::bind(a)` / `UdpSocket::bind(a)` (std, tokio, async-std); Java / Kotlin
            `ServerSocket(p)` / `DatagramSocket(p)` / Ktor `aSocket(..).tcp().bind(h, p)`; C / C++ `bind()` with
            `htons(p)`, libuv `uv_ip4_addr(h, p, &a)` + `uv_tcp_bind` / `uv_udp_bind`; Swift `NWListener(using:
            .tcp, on: p)`; Dart `ServerSocket.bind(h, p)` / `RawDatagramSocket.bind(h, p)`; PHP
            `stream_socket_server('tcp://h:p')` / `socket_bind($s, h, p)`
  send      the matching connect / send calls (`connect((h, p))`, `sendto(d, (h, p))`, `create_connection`,
            `open_connection(h, p)`, `net.connect(p, h)`, `socket.send(m, [o, l,] p, h)`, `TcpStream::connect(a)`,
            `send_to(b, a)`, `Socket(h, p)`, `DatagramPacket(.., h, p)`, `NWConnection(host:, port:, using:)`,
            `Socket.connect(h, p)`, `stream_socket_client` / `fsockopen('udp://h', p)`, `uv_tcp_connect` / `uv_udp_send`)

Port values are read from literals (`"127.0.0.1:6379"`, `8125`), format strings (`format!("127.0.0.1:{port}")`,
f-strings, template literals, concatenation), the last assignment in the function, fallbacks (`cli.port
.unwrap_or(DEFAULT_PORT)`, `config.port || 8125`, `?:`), constants of the file or a unique one of the project, `self.x`
fields, parameter defaults, CLI option defaults (clap `default_value_t`, argparse / click `default=`) and environment
reads (`os.environ.get("PORT", 8125)` -> port 8125 with `port_env`; without a default the name is `env:PORT`). When the
port is a parameter of the enclosing function, the function is a wrapper and its call sites (CALLS / INSTANTIATES
edges) are resolved instead (two levels). Port 0 (ephemeral) and unresolved ports are counted, not linked.

The receiver of a listener is its handler when one is named (callback, `Handler.handle`, protocol factory, the
function the listener is passed to: `server::run(listener, ..)`), else the function that listens. Listeners record
their bind address and `exposure` (all interfaces / loopback / specific address) for the attack-surface view (#47).
"""
from __future__ import annotations

import re
from collections import defaultdict

from .core.model import HEURISTIC, RESOLVED
from .process_runs import LIT, _args_text

EXTS = {".py": "py", ".js": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".mts": "js", ".cts": "js", ".tsx": "js",
        ".jsx": "js", ".rs": "rs", ".java": "jvm", ".kt": "jvm", ".kts": "jvm", ".scala": "jvm", ".c": "c", ".h": "c",
        ".cc": "c", ".cpp": "c", ".cxx": "c", ".hpp": "c", ".swift": "swift", ".dart": "dart", ".php": "php"}
# cheap pre-filter per language: a file without any of these words is not scanned
HINTS = {"py": ("socket", "asyncio", "create_server", "create_datagram_endpoint", "open_connection"),
         "js": ("net", "dgram"), "rs": ("TcpListener", "TcpStream", "UdpSocket"),
         "jvm": ("ServerSocket", "Socket(", "DatagramSocket", "DatagramChannel", "DatagramPacket", "aSocket", "SocketChannel"),
         "c": ("bind", "connect", "sendto", "uv_tcp_", "uv_udp_"), "swift": ("NWListener", "NWConnection"),
         "dart": ("ServerSocket", "Socket.connect", "RawDatagramSocket", "SecureSocket"),
         "php": ("stream_socket_", "socket_bind", "socket_connect", "socket_sendto", "fsockopen")}
ALL_IFACES = {"0.0.0.0", "::", "", "*", "[::]", "0:0:0:0:0:0:0:0"}
LOOPBACK = {"localhost", "::1", "[::1]", "ip6-localhost"}
INT = re.compile(r"\d{1,5}")
MCAST = re.compile(r"(?:22[4-9]|23\d)\.\d+\.\d+\.\d+|ff[0-9a-f]{2}:[0-9a-f:]+", re.I)
ENV_RX = [  # (regex, group of the key); the default is read from the rest of the expression
    re.compile(r"""os\.environ\.get\(\s*['"](\w+)['"]"""), re.compile(r"""os\.getenv\(\s*['"](\w+)['"]"""),
    re.compile(r"""\b(?:getenv|env)\(\s*['"](\w+)['"]"""), re.compile(r"""os\.environ\[\s*['"](\w+)['"]\s*\]"""),
    re.compile(r"""process\.env\.(\w+)"""), re.compile(r"""process\.env\[\s*['"](\w+)['"]\s*\]"""),
    re.compile(r"""env::var\(\s*"(\w+)"\s*\)"""), re.compile(r"""System\.getenv\(\s*"(\w+)"\s*\)"""),
    re.compile(r"""Platform\.environment\[\s*['"](\w+)['"]\s*\]"""), re.compile(r"""\$_ENV\[\s*['"](\w+)['"]\s*\]"""),
    re.compile(r"""ProcessInfo\.processInfo\.environment\[\s*"(\w+)"\s*\]"""),
]
SEMI = {"rs", "jvm_java", "c", "php", "dart"}


def split_args(text: str) -> list[str]:
    """Top-level comma split (brackets, strings, closures)."""
    out, depth, cur, i = [], 0, [], 0
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            m = LIT.match(text, i)
            if m:
                cur.append(m.group(0))
                i = m.end()
                continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "|" and depth == 0 and text[i:i + 2] == "||" and not "".join(cur).strip():
            pass
        if c == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def _top_split(text: str, seps: tuple) -> list[str]:
    """Split on top-level operators (`||`, `??`, ` or `, `?:`, `+`)."""
    out, depth, start, i = [], 0, 0, 0
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            m = LIT.match(text, i)
            if m:
                i = m.end()
                continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0:
            for s in seps:
                if text.startswith(s, i) and not (s == "+" and (text[i + 1:i + 2] in "+=" or text[i - 1:i] in "+e")):
                    out.append(text[start:i])
                    i += len(s)
                    start = i
                    break
            else:
                i += 1
            continue
        i += 1
    out.append(text[start:])
    return [p.strip() for p in out]


def statement(src: str, pos: int, semi: bool, limit: int = 800) -> str:
    """The expression from `pos` to the end of its statement (`;` at depth 0, or a newline that does not continue)."""
    depth, i, n = 0, pos, min(len(src), pos + limit)
    while i < n:
        c = src[i]
        if c in "'\"`":
            m = LIT.match(src, i)
            if m:
                i = m.end()
                continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return src[pos:i]
        elif depth == 0 and (c == ";" or (c == "," and not semi)):
            return src[pos:i]
        elif c == "\n" and depth == 0:
            before = src[pos:i].rstrip()
            nxt = src[i:i + 200].lstrip()
            if not (before.endswith(("=", "(", "||", "??", "+", "?:", "or", "\\", ".")) or nxt.startswith((".", "?", "||", "+", ":", "?:", "or "))) \
                    and (not semi or not nxt.startswith((".", "?"))):
                if not semi:
                    return src[pos:i]
        i += 1
    return src[pos:n]


def _strip(e: str) -> str:
    e = e.strip()
    while True:
        old = e
        e = re.sub(r"^(?:&mut\s+|&|\*|await\s+|new\s+|try\s+|return\s+)", "", e).strip()
        e = re.sub(r"(?:\.await|\?|\.unwrap\(\)|\.expect\([^()]*\)|\.to_string\(\)|\.to_owned\(\)|\.into\(\)|"
                   r"\.as_str\(\)|\.as_ref\(\)|\.clone\(\)|\.parse(?:::<[^>]*>)?\(\)|\.toInt\(\)|\.toShort\(\)|!!|"
                   r"\.toString\(\)|\.intValue\(\)|\.trim\(\))$", "", e).strip()
        m = re.fullmatch(r"(?:int|str|Number|parseInt|String|Integer\.parseInt|Integer\.valueOf|Int|UInt16|NWEndpoint\.Port|"
                         r"intval|int\.parse|NWEndpoint\.Port\(rawValue:|InetAddress\.getByName|InetAddress\.getByAddress|"
                         r"IPAddress|Ipv4Addr::from_str|InternetAddress)\s*\((.*)\)!?", e, re.S)
        if m:
            e = split_args(m.group(1))[0] if m.group(1).strip() else ""
            e = re.sub(r"^rawValue:\s*", "", e)
        if e.startswith("(") and e.endswith(")") and len(split_args(e[1:-1])) == 1 and _balanced(e[1:-1]):
            e = e[1:-1].strip()
        if e == old:
            return e


def _balanced(s: str) -> bool:
    d = 0
    for c in LIT.sub('""', s):
        d += c in "([{"
        d -= c in ")]}"
        if d < 0:
            return False
    return d == 0


def _lit(e: str) -> str | None:
    m = LIT.fullmatch(e.strip())
    return m.group("s") if m else None


def parse_addr(s: str) -> tuple[str | None, str | None]:
    """'127.0.0.1:6379' / 'tcp://h:p' / '[::]:80' / ':8080' -> (host, port text); a host alone -> (host, None)."""
    s = re.sub(r"^(?:tcp|udp|tcp4|tcp6|udp4|udp6|tls|ssl|ws|wss|http|https|coap|osc\.udp|osc\.tcp)://", "", s.strip())
    s = s.split("/", 1)[0]
    m = re.fullmatch(r"\[([^\]]*)\]:(.+)", s)
    if m:
        return m.group(1) or "::", m.group(2)
    if s.count(":") == 1:
        h, p = s.split(":")
        return h, p
    return (s or None), None


class Addr(dict):
    """host, port (int), env, how, param (unresolved parameter name), default (the port is a fallback)."""


class Scan:
    def __init__(self, project, b):
        self.root, self.b = project.root, b
        self.st = defaultdict(int)
        self.samples = defaultdict(list)
        self.spans = defaultdict(list)               # file -> [(line, end, nid)]
        self.by_name = defaultdict(list)             # short name -> [nid]
        self.src = {}
        self.lines = {}
        self.consts = None
        self.wrappers = []                           # (fn nid, proto, role, param, file, line, api)
        self.files = set()
        self.apps = None
        self.masks = {}
        self.envs = []                               # (protocol, endpoint name, env key)
        self.sig_end = {}                            # function nid -> offset after its parameter list
        for nid, n in b.nodes.items():
            if n.file:
                self.files.add(n.file)
            if n.kind in ("function", "method", "test") and n.file and n.line:
                self.spans[n.file].append((n.line, n.end_line or n.line, nid))
                self.by_name[_short(n)].append(nid)
        for v in self.spans.values():
            v.sort()

    # ------------------------------------------------------------ files and functions
    MASK_PY = re.compile(r'"""[\s\S]*?"""' "|" r"'''[\s\S]*?'''" "|" r"#[^\n]*" "|" r'"(?:\\.|[^"\\\n])*"' "|" r"'(?:\\.|[^'\\\n])*'")
    MASK_C = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*" "|" r'"(?:\\.|[^"\\\n])*"' "|" r"'(?:\\.|[^'\\\n])*'" "|" r"`(?:\\.|[^`\\])*`")

    def masked(self, file, pos) -> bool:
        """`pos` lies in a comment or a Python docstring / triple-quoted string (doctest examples, commented-out code)."""
        if file not in self.masks:
            src, out = self.text(file), []
            rx = self.MASK_PY if file.endswith(".py") else self.MASK_C
            for m in rx.finditer(src):
                t = m.group(0)
                if t.startswith(("#", "//", "/*", '"""', "\'\'\'")):
                    out.append((m.start(), m.end()))
            self.masks[file] = out
        from bisect import bisect_right
        ms = self.masks[file]
        i = bisect_right(ms, (pos, float("inf"))) - 1
        return i >= 0 and ms[i][0] <= pos < ms[i][1]

    def text(self, file):
        if file not in self.src:
            try:
                self.src[file] = (self.root / file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                self.src[file] = ""
            t, offs = self.src[file], [0]
            for i, c in enumerate(t):
                if c == "\n":
                    offs.append(i + 1)
            self.lines[file] = offs
        return self.src[file]

    def off(self, file, line):
        offs = self.lines[file]
        return offs[min(max(line, 1), len(offs)) - 1]

    def line_of(self, file, pos):
        from bisect import bisect_right
        return bisect_right(self.lines[file], pos)

    def enclosing(self, file, line):
        best = None
        for ln, end, nid in self.spans.get(file, ()):
            if ln <= line <= end and (best is None or end - ln < best[0]):
                best = (end - ln, nid, ln, end)
        return best

    def fn_bounds(self, file, pos):
        e = self.enclosing(file, self.line_of(file, pos))
        if not e:
            return None, 0, len(self.src[file])
        return e[1], self.off(file, e[2]), self.off(file, e[3] + 1) if e[3] < len(self.lines[file]) else len(self.src[file])

    def params(self, file, nid) -> list[tuple[str, str | None]]:
        """[(name, default expression)] of a function node, from the text after its name (self / &self dropped)."""
        n = self.b.nodes[nid]
        src = self.text(file)
        lo = self.off(file, n.line)
        name = _short(n)
        m = re.compile(rf"\b{re.escape(name)}\s*(?:<[^()]*?>)?\s*\(").search(src, lo, lo + 2000)
        if not m and name in ("__init__", "constructor", "init"):
            m = re.compile(r"\(").search(src, lo, lo + 400)
        if not m:
            return []
        out = []
        at = _args_text(src, m.end() - 1, 2000)
        self.sig_end[nid] = m.end() + len(at) + 1
        for a in split_args(at):
            a = re.sub(r"@\w+(?:\([^()]*\))?\s*", "", a).strip()
            nm, _, dflt = a.partition("=")
            nm = nm.strip()
            if nm in ("self", "&self", "&mut self", "mut self", "cls", "this") or nm.startswith(("*", "...")) and not nm[1:2].isalpha():
                continue
            nm = re.sub(r"^(?:mut\s+|val\s+|var\s+|final\s+|private\s+|public\s+|protected\s+|readonly\s+|\$)", "", nm)
            if ":" in nm:
                left = nm.split(":", 1)[0].strip().split()
                nm = left[-1] if left else ""
            else:
                ws = re.findall(r"[A-Za-z_$]\w*", nm)
                nm = ws[-1] if ws else ""
            out.append((nm.lstrip("$"), dflt.strip() or None))
        return out

    def miss(self, key, text):
        self.st[key] += 1
        if text and len(self.samples[key]) < 6 and text not in self.samples[key]:
            self.samples[key].append(text[:90])

    # ------------------------------------------------------------ values
    def const_index(self):
        if self.consts is None:
            self.consts = defaultdict(list)
            rx = re.compile(r"(?m)^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:export\s+)?(?:(?:const|static|final|val|let|var|"
                            r"private|public|internal|protected|readonly)\s+)*(?:const\s+val\s+)?([A-Z][A-Z0-9_]{2,})\s*"
                            r"(?::\s*[\w<>:&' ]+)?\s*=\s*([^\n;]+)|^#define\s+([A-Z][A-Z0-9_]{2,})\s+([^\n]+)")
            for file in sorted(self.files):
                if not any(file.endswith(x) for x in EXTS):
                    continue
                t = self.text(file)
                for m in rx.finditer(t):
                    nm, v = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                    self.consts[nm].append((file, v.strip()))
        return self.consts

    def resolve(self, e: str, ctx: dict, depth: int = 0) -> Addr:
        """An address / port expression -> Addr (empty when nothing is known)."""
        a = Addr()
        if depth > 6 or not e:
            return a
        e = _strip(e)
        if not e:
            return a
        # fallbacks: `a || b`, `a ?? b`, `a or b`, `a ?: b`, `.unwrap_or(b)` / `.unwrap_or_else(|| b)` / `.or(b)`
        alts = _top_split(e, ("||", "??", " or ", "?:"))
        m = re.fullmatch(r"(.+?)\.(?:unwrap_or|unwrap_or_else|or_else|or|unwrapOr|getOrDefault|getOrElse)\s*\((.*)\)", e, re.S)
        if m and _balanced(m.group(2)):
            alts = [m.group(1), re.sub(r"^\|\|\s*|^\{\s*|\s*\}$", "", m.group(2).strip())]
        if len(alts) > 1:
            parts = [self.resolve(x, ctx, depth + 1) for x in alts]
            for i, p in enumerate(parts):
                for k, v in p.items():
                    if k not in a and v is not None:
                        a[k] = v
                        if k == "port" and i > 0:
                            a["default"] = True
            if a.get("port") is not None:
                a.pop("param", None)
                if a.get("env"):
                    a["how"], a["default"] = "env default", True
            return a
        for rx in ENV_RX:
            mm = rx.search(e)
            if mm:
                a["env"], a["how"] = mm.group(1), "env"
                rest = e[mm.end():]
                d = re.match(r"""\s*,\s*(.+)\)\s*$""", rest, re.S) if "(" in e[:mm.start() + 30] else None
                if d:
                    a.update({k: v for k, v in self.resolve(d.group(1), ctx, depth + 1).items() if k in ("port", "host")})
                    a["default"], a["how"] = True, "env default"
                return a
        s = _lit(e)
        fm = re.fullmatch(r"(?:format!|format)\s*\(\s*(" + LIT.pattern + r")\s*(.*)\)", e, re.S)
        if s is None and e[:1] in "fF" and _lit(e[1:]) is not None:
            s = _lit(e[1:])
        if fm:
            s = fm.group("s")
            pos_args = split_args(fm.group(fm.lastindex)[1:] if fm.group(fm.lastindex).startswith(",") else fm.group(fm.lastindex))
            k = iter(pos_args)
            s = re.sub(r"\{\}|\{:\??\}", lambda _m: "{" + (next(k, "") or "?") + "}", s)
        if s is None:
            pieces = _top_split(e, ("+", " . "))
            if len(pieces) > 1 and any(_lit(p) is not None for p in pieces):
                s = "".join(_lit(p) if _lit(p) is not None else "{" + p + "}" for p in pieces)
        if s is not None:
            def sub(mm):
                name = mm.group(1) or mm.group(2) or mm.group(3) or mm.group(4)
                r = self.resolve(name, ctx, depth + 1)
                for k in ("env", "param", "default", "how"):
                    if r.get(k) is not None and k not in a:
                        a[k] = r[k]
                if r.get("port") is not None:
                    return str(r["port"])
                if r.get("host") is not None:
                    return r["host"]
                return "{?}"
            s = re.sub(r"\$\{([^{}]+)\}|\{([A-Za-z_][\w.]*)(?::[^{}]*)?\}|\$([A-Za-z_]\w*)|#\{([^{}]+)\}", sub, s)
            host, port = (None, s.strip()) if INT.fullmatch(s.strip()) else parse_addr(s)
            if host and host != "{?}":
                a["host"] = host
            if port and INT.fullmatch(port):
                a["port"] = int(port)
                a.pop("param", None) if a.get("param") and "{" not in s else None
            a.setdefault("how", "literal")
            return a
        if INT.fullmatch(e):
            a["port"], a["how"] = int(e), "literal"
            return a
        # (host, port) tuples / constructors / option objects
        tm = re.fullmatch(r"(?:new\s+)?(?:InetSocketAddress|SocketAddr(?:V[46])?::(?:new|from)|InetSocketAddress\.createUnresolved|"
                          r"NWEndpoint\.hostPort)?\s*[\(\[](.*)[\)\]]", e, re.S)
        if tm and len(split_args(tm.group(1))) >= 2:
            xs = split_args(tm.group(1))
            if xs and xs[0].startswith(("(", "[")) and len(xs) == 1:
                xs = split_args(xs[0][1:-1])
            xs = [re.sub(r"^(?:host|port):\s*", "", x) for x in xs]
            h = self.resolve(xs[0], ctx, depth + 1)
            p = self.resolve(xs[-1], ctx, depth + 1)
            if h.get("host") or _lit(xs[0]) is not None:
                a["host"] = h.get("host") or ""
            elif re.fullmatch(r"\[?\s*(?:0\s*,\s*){3}0\s*\]?|Ipv4Addr::UNSPECIFIED|IpAddr::V4\(Ipv4Addr::UNSPECIFIED\)", xs[0]):
                a["host"] = "0.0.0.0"
            elif re.fullmatch(r"\[?\s*127\s*,\s*0\s*,\s*0\s*,\s*1\s*\]?|Ipv4Addr::LOCALHOST|.*LOCALHOST.*", xs[0]):
                a["host"] = "127.0.0.1"
            for k in ("port", "env", "param", "default", "how"):
                if p.get(k) is not None:
                    a[k] = p[k]
            return a
        om = re.fullmatch(r"\{(.*)\}", e, re.S)
        if om:
            kv = {}
            for x in split_args(om.group(1)):
                k, _, v = x.partition(":")
                kv[k.strip().strip("'\"")] = v.strip() or k.strip()
            if "port" in kv:
                p = self.resolve(kv["port"], ctx, depth + 1)
                a.update({k: v for k, v in p.items() if k != "host"})
                if "host" in kv or "address" in kv:
                    h = self.resolve(kv.get("host") or kv.get("address"), ctx, depth + 1)
                    if h.get("host"):
                        a["host"] = h["host"]
            return a
        # names
        nm = re.fullmatch(r"(?:(?:self|this|Self|crate|super)(?:\.|::))?((?:[A-Za-z_]\w*(?:\.|::))*)([A-Za-z_$]\w*)", e)
        if nm:
            return self.lookup(nm.group(2), bool(nm.group(1)) or e.startswith(("self.", "this.")), e, ctx, depth)
        return a

    def lookup(self, name: str, qualified: bool, expr: str, ctx: dict, depth: int) -> Addr:
        file, src, lo, pos = ctx["file"], ctx["src"], ctx["lo"], ctx["pos"]
        if ctx.get("fn") and ctx["fn"] in self.sig_end and ctx["fn"] in self.b.nodes and self.b.nodes[ctx["fn"]].file == file:
            lo = max(lo, min(self.sig_end[ctx["fn"]], pos))      # parameter defaults are not assignments
        body = src[lo:pos]
        semi = ctx["lang"] in ("rs", "c", "php", "dart") or (ctx["lang"] in ("jvm", "js") and ";" in body)
        if not qualified:
            rx = re.compile(rf"(?<![\w.$])(?:\$)?{re.escape(name)}\s*(?::\s*[^=\n;]+?)?\s*(?<![=!<>])=(?![=>])\s*")
            hits = list(rx.finditer(body))
            if hits:
                h = hits[-1]
                v = statement(src, lo + h.end(), semi)
                return self.resolve(v, {**ctx, "pos": lo + h.start()}, depth + 1)
            for i, (p, d) in enumerate(ctx.get("params") or []):
                if p == name:
                    a = self.resolve(d, ctx, depth + 1) if d else Addr()
                    if a.get("port") is not None:
                        a["how"], a["default"] = "parameter default", True
                    a["param"] = name
                    return a
        else:
            last = name
            # self.x = ... / this.x = ... (any function of the file), Kotlin / Java / Swift fields
            rx = re.compile(rf"(?:self|this)\.{re.escape(last)}\s*=(?!=)\s*|(?:val|var|let|final|private|public|protected)\s+"
                            rf"(?:[\w<>?]+\s+)?{re.escape(last)}\s*(?::\s*[^=\n]+?)?\s*=(?!=)\s*")
            for h in reversed(list(rx.finditer(src))):
                fn, flo, _fhi = self.fn_bounds(file, h.start())
                c2 = {**ctx, "lo": flo, "pos": h.start(), "fn": fn, "params": self.params(file, fn) if fn else []}
                r = self.resolve(statement(src, h.end(), semi), c2, depth + 1)
                if r:
                    if r.get("param") and fn:
                        r["param_of"] = fn
                    return r
            od = self.option_default(src, last)
            if od:
                r = self.resolve(od, ctx, depth + 1)
                if r.get("port") is not None:
                    r["how"], r["default"] = "option default", True
                    return r
        # constants: this file, then a unique one in the project
        crx = re.compile(rf"(?m)^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:export\s+)?(?:(?:const|static|final|val|let|var|private|"
                         rf"public|internal|protected|readonly|int|short|u16|char\s*\*)\s+)*{re.escape(name)}\s*(?::\s*[\w<>:&' ]+)?"
                         rf"\s*=\s*([^\n;]+)|^#define\s+{re.escape(name)}\s+([^\n]+)")
        m = crx.search(src)
        cands = [(file, (m.group(1) or m.group(2)).strip())] if m else []
        if not cands:
            cs = self.const_index().get(name) or []
            vals = {v for _f, v in cs}
            if len(vals) == 1:
                cands = cs[:1]
        if cands:
            f2, v = cands[0]
            r = self.resolve(v, {**ctx, "file": f2, "src": self.text(f2), "lo": 0, "pos": 0, "params": []}, depth + 1)
            if r.get("port") is not None or r.get("host"):
                r.setdefault("how", "constant")
                if r["how"] == "literal":
                    r["how"] = "constant"
                r.setdefault("const", name)
            return r
        if qualified:
            od = self.option_default(src, name)
            if od:
                r = self.resolve(od, ctx, depth + 1)
                if r.get("port") is not None:
                    r["how"], r["default"] = "option default", True
                    return r
        return Addr()

    @staticmethod
    def option_default(src: str, name: str) -> str | None:
        """CLI option defaults: clap `#[arg(.., default_value_t = X)] port: u16`, argparse
        `add_argument('--port', .. default=X)`, click `@click.option('--port', default=X)`."""
        m = re.search(rf"default_value(?:_t)?\s*=\s*([^,)\]]+)[^\n]*\)\]\s*(?:pub\s+)?{re.escape(name)}\s*:", src)
        if m:
            return m.group(1).strip()
        m = re.search(rf"""(?:add_argument|option)\(\s*['"]--{re.escape(name).replace('_', '[-_]')}['"][^)]*?default\s*=\s*([^,)]+)""", src)
        return m.group(1).strip() if m else None

    # ------------------------------------------------------------ edges
    def emit(self, proto, role, file, pos, addr: Addr, api, ctx, handler=None, how_site=None, src_override=None):
        port, env = addr.get("port"), addr.get("env")
        line = self.line_of(file, pos)
        if self.masked(file, pos):
            self.st["in_comment"] += 1
            return False
        src = src_override or ctx.get("fn")
        if src is None:
            self.miss("no_enclosing_function", f"{file}:{line}")
            return False
        if port == 0:
            self.st["ephemeral"] += 1
            return False
        if port is None and env and not re.search(r"PORT", env, re.I):
            env = None          # an address / path from the environment (SSH_AUTH_SOCK, SERVER_ADDR): no port to pair
        if port is None and not env:
            if addr.get("param") and not src_override:
                self.wrappers.append((src, proto, role, addr["param"], file, line, api, addr.get("param_of")))
                self.st["wrapper_sites"] += 1
                return False
            self.miss(f"{role}_unresolved", f"{file}:{line} {api}")
            return False
        name = str(port) if port is not None else f"env:{env}"
        conf = RESOLVED if port is not None and not addr.get("default") and addr.get("how") in ("literal", "constant", "call site") else HEURISTIC
        host = addr.get("host")
        attrs = {"library": api, "how": how_site or addr.get("how"), "port_env": env}
        node = {"port": port}
        from .protocols import protocol_receive, protocol_send
        from .tests_index import is_test_node
        group = self.multicast(ctx) if proto == "udp" else None
        if group:
            node["multicast_group"] = group
        elif proto == "udp" and host and MCAST.fullmatch(host):
            node["multicast_group"] = host
        if env:
            self.envs.append((proto, name, env))
        if role == "listen":
            h = host if host is not None else ("" if ctx["lang"] in ("js", "jvm", "dart", "swift", "c") else None)
            exp = None if h is None else "all" if h in ALL_IFACES else "loopback" if h in LOOPBACK or h.startswith("127.") else "specific"
            hid = handler or src
            nid = protocol_receive(self.b, proto, name, hid, file, line, conf, node_attrs=node, bind_address=h,
                                   exposure=exp, **attrs)
            n = self.b.nodes[nid]
            if h is not None:
                n.attrs["bind_addresses"] = sorted(set(n.attrs.get("bind_addresses") or []) | {h})
                ex = n.attrs.get("exposure")
                n.attrs["exposure"] = exp if ex in (None, exp) else "all" if "all" in (ex, exp) else "mixed"
            self.st[f"{proto}_listeners"] += 1
        else:
            sn = self.b.nodes.get(src)
            protocol_send(self.b, proto, name, src, file, line, conf, test=bool(sn and is_test_node(sn)), role=role,
                          node_attrs=node, host=host, **attrs)
            self.st[f"{proto}_senders"] += 1
        return True

    def multicast(self, ctx) -> str | None:
        """The multicast group a UDP socket joins in the same function (addMembership / join_multicast_v4 /
        IP_ADD_MEMBERSHIP with inet_aton / joinGroup / ip_mreq)."""
        _fn, lo, hi = self.fn_bounds(ctx["file"], ctx["pos"])
        body = ctx["src"][lo:hi]
        if not re.search(r"addMembership|join_multicast|IP_ADD_MEMBERSHIP|joinGroup|joinMulticast|imr_multiaddr|mcast", body):
            return None
        m = re.search(r"Ipv4Addr::new\(\s*(2[23]\d)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", body)
        if m:
            return ".".join(m.groups())
        for lit in re.findall(r"['\"]((?:22[4-9]|23\d)\.\d+\.\d+\.\d+|ff[0-9a-f]{2}:[0-9a-f:]+)['\"]", body):
            return lit
        for nm in re.findall(r"(?:addMembership|join_multicast_v[46]|inet_aton|joinGroup|getByName|inet_addr)\s*\(\s*&?([A-Za-z_][\w.]*)", body):
            r = self.resolve(nm, ctx)
            if r.get("host") and MCAST.fullmatch(r["host"]):
                return r["host"]
        return None

    def ctx(self, file, lang, pos):
        fn, lo, _hi = self.fn_bounds(file, pos)
        return {"file": file, "src": self.src[file], "lang": lang, "lo": lo, "pos": pos, "fn": fn,
                "params": self.params(file, fn) if fn else []}

    def handler(self, expr: str | None, file: str, method: str | None = None) -> str | None:
        """Node of a named callback / handler class method."""
        if not expr:
            return None
        e = _strip(expr)
        e = re.sub(r"^(?:lambda\s*:\s*|\(\)\s*=>\s*|::)", "", e)
        e = re.sub(r"\(\s*\)$", "", e).strip()
        m = re.fullmatch(r"(?:self\.|this\.|[A-Za-z_]\w*\.)*([A-Za-z_]\w*)", e)
        if not m:
            return None
        nm = m.group(1)
        if method:
            for nid in self.by_name.get(method, ()):
                if re.search(rf"[.:#]{re.escape(nm)}[.:#]+{re.escape(method)}$", nid):
                    return nid
            return None
        ids = self.by_name.get(nm) or []
        same = [i for i in ids if self.b.nodes[i].file == file]
        return (same or ids)[0] if len(same or ids) == 1 else None

    def passed_to(self, file, var, start, hi, fn) -> str | None:
        """The function a listener variable is handed to later in the same function (`server::run(listener, ..)`)."""
        if not var or not fn:
            return None
        src = self.src[file]
        for m in re.finditer(rf"([A-Za-z_][\w:.]*)\s*\(\s*(?:&|&mut\s+)?{re.escape(var)}\b(?!\s*\.)", src[start:hi]):
            callee = m.group(1).split("::")[-1].split(".")[-1]
            if callee in ("Some", "Ok", "Arc", "Box", "spawn", "Rc", "drop", "print", "println", "len", "str", "repr"):
                continue
            line = self.line_of(file, start + m.start())
            for e in self.b.edges.values():
                if e.kind == "CALLS" and e.src == fn and e.line == line and _short(self.b.nodes.get(e.dst)) == callee:
                    return e.dst
        return None

    # ------------------------------------------------------------ per language
    def scan(self, file, lang):
        src = self.text(file)
        try:      # a source shape the patterns did not expect never stops the index: counted, with a sample
            if self.apps is not None:
                self.apps.run_file(file, lang, src)
            if any(h in src for h in HINTS[lang]):
                getattr(self, "s_" + lang)(file, src)
        except (IndexError, KeyError, ValueError, TypeError, AttributeError) as e:
            self.miss("scan_errors", f"{file}: {type(e).__name__} {e}")

    def _calls(self, src, rx):
        for m in rx.finditer(src):
            yield m, split_args(_args_text(src, m.end() - 1))

    def _var_before(self, src, pos):
        m = re.search(r"(?:let|var|val|const|final|auto)?\s*(?:mut\s+)?\$?([A-Za-z_]\w*)\s*(?::\s*[^=\n]+)?=\s*(?:await\s+|try\s+)?[^=\n;]*$",
                      src[max(0, pos - 160):pos])
        return m.group(1) if m else None

    def s_py(self, file, src):
        def dgram_near(var, pos, lo):
            """The socket type of `var`: its `socket.socket(..)` call in the function (no type: SOCK_STREAM), else the
            file's only socket type."""
            body = src[lo:pos]
            v = re.escape(var.split(".")[-1])
            ms = list(re.finditer(rf"\b{v}\s*=\s*(?:socket\.)?socket\s*\(([^)]*)\)", body))
            if ms:
                return "udp" if "SOCK_DGRAM" in ms[-1].group(1) else "tcp"
            if "SOCK_DGRAM" in body:
                return "udp"
            if "SOCK_STREAM" in body or re.search(r"(?:socket\.)?socket\s*\(\s*\)", body):
                return "tcp"
            return "udp" if "SOCK_DGRAM" in src and "SOCK_STREAM" not in src else "tcp"
        for m, args in self._calls(src, re.compile(r"(?<![\w.])(?:socketserver\.)?((?:Threading|Forking)?(?:TCP|UDP)Server)\s*\(")):
            if len(args) < 2:
                continue
            c = self.ctx(file, "py", m.start())
            proto = "udp" if "UDP" in m.group(1) else "tcp"
            h = self.handler(args[1], file, "handle")
            self.emit(proto, "listen", file, m.start(), self.resolve(args[0], c), "socketserver." + m.group(1), c, h)
        for m, args in self._calls(src, re.compile(r"\b(?:asyncio\.)?start_server\s*\(|\.create_server\s*\(")):
            if not args:
                continue
            c = self.ctx(file, "py", m.start())
            kw = {x.split("=", 1)[0].strip(): x.split("=", 1)[1] for x in args if re.match(r"\w+\s*=[^=]", x)}
            pos = [x for x in args if not re.match(r"\w+\s*=[^=]", x)]
            port = kw.get("port") or (pos[2] if len(pos) > 2 else None)
            host = kw.get("host") or (pos[1] if len(pos) > 1 else None)
            if "start_server" in m.group(0) and m.group(0).startswith(("socket.", "create")):
                continue
            if port is None and len(pos) == 1 and "socket.create_server" in src[max(0, m.start() - 8):m.end()]:
                continue
            a = self.resolve(port, c) if port else Addr()
            if host:
                hh = self.resolve(host, c)
                if hh.get("host") is not None:
                    a["host"] = hh["host"]
                elif _strip(host) in ("None", ""):
                    a["host"] = ""
            elif port:
                a["host"] = ""
            cb = pos[0] if pos else kw.get("client_connected_cb") or kw.get("protocol_factory")
            hdl = self.handler(cb, file) if "start_server" in m.group(0) else self.handler(cb, file, "data_received")
            api = "asyncio.start_server" if "start_server" in m.group(0) else "loop.create_server"
            self.emit("tcp", "listen", file, m.start(), a, api, c, hdl)
        for m, args in self._calls(src, re.compile(r"\bsocket\.create_server\s*\(")):
            if args:
                c = self.ctx(file, "py", m.start())
                self.emit("tcp", "listen", file, m.start(), self.resolve(args[0], c), "socket.create_server", c)
        for m, args in self._calls(src, re.compile(r"\.create_datagram_endpoint\s*\(")):
            c = self.ctx(file, "py", m.start())
            kw = {x.split("=", 1)[0].strip(): x.split("=", 1)[1] for x in args if re.match(r"\w+\s*=[^=]", x)}
            if kw.get("local_addr"):
                self.emit("udp", "listen", file, m.start(), self.resolve(kw["local_addr"], c), "loop.create_datagram_endpoint", c,
                          self.handler(args[0], file, "datagram_received") if args else None)
            if kw.get("remote_addr"):
                self.emit("udp", "send", file, m.start(), self.resolve(kw["remote_addr"], c), "loop.create_datagram_endpoint", c)
        for m, args in self._calls(src, re.compile(r"\b(?:asyncio\.)?open_connection\s*\(")):
            c = self.ctx(file, "py", m.start())
            kw = {x.split("=", 1)[0].strip(): x.split("=", 1)[1] for x in args if re.match(r"\w+\s*=[^=]", x)}
            pos = [x for x in args if not re.match(r"\w+\s*=[^=]", x)]
            port = kw.get("port") or (pos[1] if len(pos) > 1 else None)
            if port is None:
                continue
            a = self.resolve(port, c)
            host = kw.get("host") or (pos[0] if pos else None)
            if host and self.resolve(host, c).get("host"):
                a["host"] = self.resolve(host, c)["host"]
            self.emit("tcp", "connect", file, m.start(), a, "asyncio.open_connection", c)
        if "socket" not in src:
            return
        for m, args in self._calls(src, re.compile(r"\bsocket\.create_connection\s*\(")):
            if args:
                c = self.ctx(file, "py", m.start())
                self.emit("tcp", "connect", file, m.start(), self.resolve(args[0], c), "socket.create_connection", c)
        for m, args in self._calls(src, re.compile(r"\b([A-Za-z_][\w.]*)\.(bind|connect|connect_ex|sendto)\s*\(")):
            want = 2 if m.group(2) == "sendto" else 1
            if len(args) < want:
                continue
            arg = args[-1] if m.group(2) == "sendto" else args[0]
            if not (arg.startswith("(") or re.fullmatch(r"[\w.]+", arg)):
                continue
            c = self.ctx(file, "py", m.start())
            if "AF_UNIX" in src[c["lo"]:m.start()]:
                continue                       # Unix domain sockets: local IPC (#38)
            a = self.resolve(arg, c)
            if not a and not arg.startswith("("):
                continue                       # an unrelated .bind(x) / .connect(x): only (host, port) or resolvable names
            proto = "udp" if m.group(2) == "sendto" else dgram_near(m.group(1), m.start(), c["lo"])
            role = "listen" if m.group(2) == "bind" else "send" if proto == "udp" else "connect"
            hdl = self.passed_to(file, m.group(1).split(".")[-1], m.end(), len(src), c["fn"]) if role == "listen" else None
            self.emit(proto, role, file, m.start(), a, f"socket.{m.group(2)}", c, hdl)

    def s_js(self, file, src):
        if not re.search(r"""(?:require\(\s*|from\s+|import\s*\(\s*)['"](?:node:)?(?:net|dgram|tls)['"]""", src):
            return
        srv_vars, udp_vars = {}, {}
        for m, args in self._calls(src, re.compile(r"\b(?:net|tls)\.createServer\s*\(|(?<![\w.])createServer\s*\(")):
            v = self._var_before(src, m.start())
            cb = next((a for a in args if not a.startswith("{")), None)
            if v:
                srv_vars[v] = (m.start(), cb)
            tail = src[m.end() - 1:]
            end = m.end() - 1 + len(_args_text(src, m.end() - 1)) + 2
            lm = re.match(r"\s*\)?\s*\.listen\s*\(", src[end - 1:end + 40])
            if lm:
                self._js_listen(file, src, end - 1 + lm.end() - 1, cb, "net.createServer")
            del tail
        for v, (_p, cb) in srv_vars.items():
            for lm in re.finditer(rf"(?<![\w.]){re.escape(v)}\.listen\s*\(", src):
                self._js_listen(file, src, lm.end() - 1, cb, "net.createServer")
        for m, args in self._calls(src, re.compile(r"\b(?:net|tls)\.(?:connect|createConnection)\s*\(|(?<![\w.])(?:createConnection)\s*\(")):
            if not args:
                continue
            c = self.ctx(file, "js", m.start())
            if args[0].startswith("{"):
                a = self.resolve(args[0], c)
            else:
                a = self.resolve(args[0], c)
                if len(args) > 1 and not args[1].startswith(("(", "function")):
                    h = self.resolve(args[1], c)
                    if h.get("host"):
                        a["host"] = h["host"]
            self.emit("tcp", "connect", file, m.start(), a, "net.connect", c)
        for m, args in self._calls(src, re.compile(r"\bdgram\.createSocket\s*\(|(?<![\w.])createSocket\s*\(")):
            v = self._var_before(src, m.start())
            if v:
                udp_vars[v] = args[1] if len(args) > 1 else None
        for v, cb in udp_vars.items():
            msg = None
            om = re.search(rf"""(?<![\w.]){re.escape(v)}\.on\(\s*['"]message['"]\s*,\s*([^)\n]+)""", src)
            if om:
                msg = om.group(1)
            for bm in re.finditer(rf"(?<![\w.]){re.escape(v)}\.bind\s*\(", src):
                args = split_args(_args_text(src, bm.end() - 1))
                if not args or args[0].startswith(("(", "function")):
                    continue
                c = self.ctx(file, "js", bm.start())
                a = self.resolve(args[0], c)
                if len(args) > 1 and not args[1].startswith(("(", "function")) and not args[0].startswith("{"):
                    h = self.resolve(args[1], c)
                    if h.get("host") is not None:
                        a["host"] = h["host"]
                self.emit("udp", "listen", file, bm.start(), a, "dgram.bind", c, self.handler(msg or cb, file))
            for sm in re.finditer(rf"(?<![\w.]){re.escape(v)}\.send\s*\(", src):
                args = [x for x in split_args(_args_text(src, sm.end() - 1)) if not re.match(r"(?:function\b|\(?[\w, ]*\)?\s*=>|cb$|callback$|logerror$)", x)]
                if len(args) >= 5:
                    pe, he = args[3], args[4]
                elif len(args) >= 2:
                    pe, he = args[1], args[2] if len(args) > 2 else None
                else:
                    continue
                c = self.ctx(file, "js", sm.start())
                a = self.resolve(pe, c)
                if he:
                    h = self.resolve(he, c)
                    if h.get("host"):
                        a["host"] = h["host"]
                self.emit("udp", "send", file, sm.start(), a, "dgram.send", c)

    def _js_listen(self, file, src, paren, cb, api):
        args = split_args(_args_text(src, paren))
        if not args or args[0].startswith(("(", "function")):
            return
        c = self.ctx(file, "js", paren)
        a = self.resolve(args[0], c)
        if args[0].startswith("{"):
            pass
        elif len(args) > 1 and not args[1].startswith(("(", "function")) and not INT.fullmatch(args[1].strip()):
            h = self.resolve(args[1], c)
            a["host"] = h.get("host") if h.get("host") is not None else a.get("host")
        if isinstance(a.get("port"), int) or a.get("env") or a.get("param"):
            self.emit("tcp", "listen", file, paren, a, api, c, self.handler(cb, file))
        else:
            self.miss("listen_unresolved", f"{file}:{self.line_of(file, paren)} {api}")

    def s_rs(self, file, src):
        for m, args in self._calls(src, re.compile(r"\b(TcpListener|TcpStream|UdpSocket)::(bind|connect)\s*\(")):
            if not args:
                continue
            c = self.ctx(file, "rs", m.start())
            a = self.resolve(args[0], c)
            kind, op = m.group(1), m.group(2)
            proto = "udp" if kind == "UdpSocket" else "tcp"
            role = "listen" if op == "bind" else "connect"
            hdl = None
            if role == "listen":
                v = self._var_before(src, m.start())
                _fn, _lo, hi = self.fn_bounds(file, m.start())
                hdl = self.passed_to(file, v, m.end(), hi, c["fn"])
            self.emit(proto, role, file, m.start(), a, f"{kind}::{op}", c, hdl)
        for m, args in self._calls(src, re.compile(r"\.(send_to)\s*\(")):
            if len(args) >= 2:
                c = self.ctx(file, "rs", m.start())
                self.emit("udp", "send", file, m.start(), self.resolve(args[1], c), "UdpSocket::send_to", c)
        for m, args in self._calls(src, re.compile(r"\b([a-z_]\w*)\.connect\s*\(")):
            v = m.group(1)
            c = self.ctx(file, "rs", m.start())
            if not args or not re.search(rf"let\s+(?:mut\s+)?{re.escape(v)}\s*(?::[^=]+)?=\s*UdpSocket::bind", src[c["lo"]:m.start()]):
                continue
            self.emit("udp", "send", file, m.start(), self.resolve(args[0], c), "UdpSocket::connect", c)

    def s_jvm(self, file, src):
        for m, args in self._calls(src, re.compile(r"(?<![\w.])(ServerSocket|DatagramSocket|MulticastSocket|Socket|DatagramPacket)\s*\(")):
            kind = m.group(1)
            c = self.ctx(file, "jvm", m.start())
            if kind in ("ServerSocket", "DatagramSocket", "MulticastSocket"):
                if not args:
                    continue
                a = self.resolve(args[0], c)
                if len(args) >= 3:
                    h = self.resolve(args[2], c)
                    if h.get("host") is not None:
                        a["host"] = h["host"]
                self.emit("tcp" if kind == "ServerSocket" else "udp", "listen", file, m.start(), a, kind, c)
            elif kind == "Socket" and len(args) >= 2:
                a = self.resolve(args[1], c)
                h = self.resolve(args[0], c)
                if h.get("host"):
                    a["host"] = h["host"]
                self.emit("tcp", "connect", file, m.start(), a, "Socket", c)
            elif kind == "DatagramPacket" and len(args) >= 3:
                a = self.resolve(args[-1], c) if len(args) >= 4 else self.resolve(args[2], c)
                if len(args) >= 4:
                    h = self.resolve(args[-2], c)
                    if h.get("host"):
                        a["host"] = h["host"]
                self.emit("udp", "send", file, m.start(), a, "DatagramPacket", c)
        for m, args in self._calls(src, re.compile(r"\.(tcp|udp)\(\)\s*\.(bind|connect)\s*\(")):
            c = self.ctx(file, "jvm", m.start())
            if not args:
                continue
            a = self.resolve(args[-1] if len(args) >= 2 else args[0], c)
            if len(args) >= 2:
                h = self.resolve(args[0], c)
                if h.get("host") is not None:
                    a["host"] = h["host"]
            role = "listen" if m.group(2) == "bind" else "send" if m.group(1) == "udp" else "connect"
            self.emit(m.group(1), role, file, m.start(), a, f"ktor {m.group(1)}().{m.group(2)}", c)
        for m, args in self._calls(src, re.compile(r"\b(\w+)\.(send|connect|bind)\s*\(")):
            v, op = m.group(1), m.group(2)
            decl = re.search(rf"\b{re.escape(v)}\s*(?::\s*\w+)?\s*=\s*(DatagramChannel|ServerSocketChannel|SocketChannel|AsynchronousServerSocketChannel|AsynchronousSocketChannel)\.open", src)
            if not decl or not args:
                continue
            kind = decl.group(1)
            proto = "udp" if kind == "DatagramChannel" else "tcp"
            if op == "send" and proto == "udp" and len(args) >= 2:
                role, e = "send", args[1]
            elif op == "bind":
                role, e = "listen", args[0]
            elif op == "connect":
                role, e = ("send" if proto == "udp" else "connect"), args[0]
            else:
                continue
            c = self.ctx(file, "jvm", m.start())
            self.emit(proto, role, file, m.start(), self.resolve(e, c), f"{kind}.{op}", c)

    def s_c(self, file, src):
        # libuv: uv_ip4_addr(h, p, &a) / uv_ip6_addr, then uv_tcp_bind / uv_udp_bind / uv_tcp_connect / uv_udp_send /
        # uv_udp_connect with &a: each call takes the last assignment of its address variable before it
        assigns = defaultdict(list)                  # (fn, var) -> [(pos, Addr, ctx)]
        for m, args in self._calls(src, re.compile(r"\buv_ip[46]_addr\s*\(")):
            if len(args) < 3:
                continue
            c = self.ctx(file, "c", m.start())
            a = self.resolve(args[1], c)
            h = self.resolve(args[0], c)
            if h.get("host") is not None:
                a["host"] = h["host"]
            assigns[(c["fn"], args[2].lstrip("&").strip())].append((m.start(), a, c))
        if assigns:
            for um in re.finditer(r"\buv_(tcp|udp)_(bind|connect|send)\s*\(", src):
                args = split_args(_args_text(src, um.end() - 1))
                fn, _lo, _hi = self.fn_bounds(file, um.start())
                for x in args:
                    v = re.sub(r"^\([^()]*\)\s*", "", x).lstrip("&").strip()
                    prev = [t for t in assigns.get((fn, v), ()) if t[0] < um.start()]
                    if prev:
                        _p, a, c = prev[-1]
                        proto, op = um.group(1), um.group(2)
                        role = "listen" if op == "bind" else "send" if proto == "udp" else "connect"
                        self.emit(proto, role, file, um.start(), Addr(a), f"uv_{proto}_{op}", {**c, "pos": um.start()})
                        break
        for m in re.finditer(r"\bhtons\s*\(", src):
            args = split_args(_args_text(src, m.end() - 1))
            if not args:
                continue
            c = self.ctx(file, "c", m.start())
            _fn, lo, hi = self.fn_bounds(file, m.start())
            body = src[lo:hi]
            if "uv_" in body:
                continue
            proto = "udp" if "SOCK_DGRAM" in body else "tcp"
            role = "listen" if re.search(r"(?<![\w.>])bind\s*\(", body) else ("send" if proto == "udp" else "connect") \
                if re.search(r"(?<![\w.>])(?:connect|sendto)\s*\(", body) else None
            if role is None:
                continue
            a = self.resolve(args[0], c)
            hm = re.search(r"inet_(?:addr|pton)\s*\([^\"]*\"([^\"]+)\"", body)
            if hm:
                a["host"] = hm.group(1)
            elif "INADDR_ANY" in body or "in6addr_any" in body:
                a["host"] = "0.0.0.0"
            elif "INADDR_LOOPBACK" in body:
                a["host"] = "127.0.0.1"
            self.emit(proto, role, file, m.start(), a, "bind" if role == "listen" else "sendto" if proto == "udp" else "connect", c)

    def s_swift(self, file, src):
        for m, args in self._calls(src, re.compile(r"\b(NWListener|NWConnection)\s*\(")):
            kw = {x.split(":", 1)[0].strip(): x.split(":", 1)[1].strip() for x in args if ":" in x}
            using = kw.get("using", "")
            proto = "udp" if ".udp" in using else "tcp" if ".tcp" in using or "tls" in using.lower() or not using else None
            if proto is None or "port" not in kw and "on" not in kw:
                continue
            c = self.ctx(file, "swift", m.start())
            a = self.resolve(kw.get("on") or kw.get("port"), c)
            if m.group(1) == "NWListener":
                self.emit(proto, "listen", file, m.start(), a, "NWListener", c)
            else:
                h = self.resolve(kw.get("host", ""), c)
                if h.get("host"):
                    a["host"] = h["host"]
                self.emit(proto, "send" if proto == "udp" else "connect", file, m.start(), a, "NWConnection", c)

    def s_dart(self, file, src):
        for m, args in self._calls(src, re.compile(r"\b(ServerSocket|SecureServerSocket|RawDatagramSocket|Socket|SecureSocket)\.(bind|connect)\s*\(")):
            if len(args) < 2:
                continue
            c = self.ctx(file, "dart", m.start())
            a = self.resolve(args[1], c)
            h = args[0]
            hv = "0.0.0.0" if "anyIPv4" in h or "anyIPv6" in h else "127.0.0.1" if "loopback" in h else self.resolve(h, c).get("host")
            if hv is not None:
                a["host"] = hv
            kind, op = m.group(1), m.group(2)
            proto = "udp" if kind == "RawDatagramSocket" else "tcp"
            role = "listen" if op == "bind" and (proto == "tcp" or a.get("port")) else "connect"
            self.emit(proto, role, file, m.start(), a, f"{kind}.{op}", c)
        for m, args in self._calls(src, re.compile(r"\b(\w+)\.send\s*\(")):
            if len(args) == 3 and "RawDatagramSocket" in src:
                c = self.ctx(file, "dart", m.start())
                self.emit("udp", "send", file, m.start(), self.resolve(args[2], c), "RawDatagramSocket.send", c)

    def s_php(self, file, src):
        for m, args in self._calls(src, re.compile(r"\b(stream_socket_server|stream_socket_client|fsockopen|pfsockopen)\s*\(")):
            if not args:
                continue
            c = self.ctx(file, "php", m.start())
            a = self.resolve(args[0], c)
            s = _lit(args[0]) or ""
            proto = "udp" if s.startswith("udp") else "tcp"
            if m.group(1).endswith("sockopen") and len(args) >= 2 and a.get("port") is None:
                a.update({k: v for k, v in self.resolve(args[1], c).items() if k != "host"})
            role = "listen" if m.group(1) == "stream_socket_server" else "send" if proto == "udp" else "connect"
            self.emit(proto, role, file, m.start(), a, m.group(1), c)
        for m, args in self._calls(src, re.compile(r"\bsocket_(bind|connect|sendto)\s*\(")):
            op = m.group(1)
            if (op != "sendto" and len(args) < 3) or (op == "sendto" and len(args) < 6):
                continue
            c = self.ctx(file, "php", m.start())
            body = src[c["lo"]:m.start()]
            proto = "udp" if "SOCK_DGRAM" in body or op == "sendto" else "tcp"
            pe, he = (args[5], args[4]) if op == "sendto" else (args[2], args[1])
            a = self.resolve(pe, c)
            h = self.resolve(he, c)
            if h.get("host") is not None:
                a["host"] = h["host"]
            role = "listen" if op == "bind" else "send" if proto == "udp" else "connect"
            self.emit(proto, role, file, m.start(), a, f"socket_{op}", c)

    # ------------------------------------------------------------ wrappers: resolve at the call sites
    def call_sites(self, depth=0):
        todo, self.wrappers = self.wrappers, []
        if not todo:
            return
        into = defaultdict(list)
        targets = {}
        for w in todo:
            fn = w[7] or w[0]                   # the constructor whose parameter fills `self.addr`
            targets[fn] = w
            n = self.b.nodes.get(fn)
            if n is not None and _short(n) in ("__init__", "constructor", "init"):
                cls = fn.rsplit(".", 1)[0].replace("method:", "class:", 1)
                targets.setdefault(cls, w)
        for e in self.b.edges.values():
            if e.kind in ("CALLS", "INSTANTIATES") and e.dst in targets and e.file and e.line:
                into[e.dst].append(e)
        done = set()
        for tgt, edges in into.items():
            w = targets[tgt]
            fn, proto, role, param, wfile, wline, api, owner = w
            pfn = owner or fn
            params = [p for p, _d in self.params(self.b.nodes[pfn].file, pfn)]
            if param not in params:
                continue
            idx = params.index(param)
            nm = _short(self.b.nodes[tgt]) if not tgt.startswith("class:") else tgt.rsplit(".", 1)[-1].split(":")[-1]
            for e in edges:
                key = (e.src, tgt, e.line)
                if key in done:
                    continue
                done.add(key)
                src = self.text(e.file)
                lo = self.off(e.file, e.line)
                cm = re.compile(rf"\b{re.escape(nm)}\s*(?:::<[^>]*>)?\(").search(src, lo, lo + 400)
                if not cm:
                    continue
                args = split_args(_args_text(src, cm.end() - 1))
                kw = {x.split("=", 1)[0].strip(): x.split("=", 1)[1] for x in args if re.match(r"\w+\s*=[^=]", x)}
                kw.update({x.split(":", 1)[0].strip(): x.split(":", 1)[1] for x in args if re.match(r"\w+\s*:[^:]", x)})
                pos = [x for x in args if not re.match(r"\w+\s*[=:][^=:]", x)]
                arg = kw.get(param) or (pos[idx] if idx < len(pos) else None)
                if arg is None:
                    continue
                lang = EXTS.get("." + e.file.rsplit(".", 1)[-1])
                c = self.ctx(e.file, lang, cm.start())
                a = self.resolve(arg, c)
                if a.get("port") is None and not a.get("env") and not a.get("param"):
                    self.miss("call_site_unresolved", f"{e.file}:{e.line} {nm}")
                    continue
                if a.get("how") in ("literal", "constant"):
                    a["how"] = "call site"
                if self.emit(proto, role, e.file, cm.start(), a, api, {**c, "lang": lang}, how_site=None,
                             src_override=None if a.get("param") else e.src):
                    self.st["via_wrapper"] += 1
        if depth < 1 and self.wrappers:
            self.call_sites(depth + 1)
        self.st["wrappers"] += len({w[0] for w in todo})

    def env_matches(self):
        """`env:KEY` endpoints (the port comes from KEY, no value in the repo) match the endpoints whose port is read
        from the same KEY with a default (MATCHES_ENDPOINT, heuristic): `connect(os.environ["CACHE_PORT"])` ->
        `listen(process.env.CACHE_PORT || 6380)`."""
        from .protocols import _endpoint
        by_key = defaultdict(set)
        for proto, name, env in self.envs:
            by_key[(proto, env)].add(name)
            nid = _endpoint(self.b, proto, name, None)
            n = self.b.nodes[nid]
            n.attrs["port_envs"] = sorted(set(n.attrs.get("port_envs") or []) | {env})
        side, loc = defaultdict(set), {}
        for e in self.b.edges.values():
            if e.kind == "SENDS_TO" or (e.kind == "TEST_CALLS" and e.attrs.get("orig") == "SENDS_TO"):
                side[e.dst].add("send")
            elif e.kind == "RECEIVED_BY":
                side[e.src].add("recv")
                loc.setdefault(e.src, (e.file, e.line))
        for (proto, env), names in by_key.items():
            ids = {nm: f"endpoint:{proto}:{nm}" for nm in names}
            bare = ids.get(f"env:{env}")
            if not bare:
                continue
            for nm, nid in ids.items():
                if nid == bare:
                    continue
                for s_, d_ in ((bare, nid), (nid, bare)):
                    if "send" in side[s_] and "recv" in side[d_]:
                        self.b.add_edge(s_, d_, "MATCHES_ENDPOINT", *loc.get(d_, (None, None)), HEURISTIC, sender_name=s_.split(":", 2)[2],
                                        pattern=d_.split(":", 2)[2], via=f"env {env}")
                        self.st["env_matches"] += 1

    def run(self) -> dict:
        from .udp_apps import Apps
        self.apps = Apps(self)
        for file in sorted(self.spans):
            ext = "." + file.rsplit(".", 1)[-1] if "." in file else ""
            lang = EXTS.get(ext)
            if lang:
                self.scan(file, lang)
        self.call_sites()
        self.env_matches()
        out = {k: v for k, v in self.st.items() if v}
        if self.apps.st:
            out["applications"] = dict(sorted(self.apps.st.items()))
        if self.samples:
            out["samples"] = dict(self.samples)
        return out


def _short(n) -> str:
    if n is None:
        return ""
    nm = n.name or ""
    return re.split(r"[.:#]", nm.split("(")[0])[-1] if nm else ""


def apply(project, builder) -> dict:
    """Add the socket endpoints; returns stats (empty when nothing was found)."""
    return Scan(project, builder).run()

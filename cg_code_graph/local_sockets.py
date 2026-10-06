"""Unix domain sockets, named pipes / FIFOs and D-Bus (#38 part 2).

  unix   endpoint:unix:<path>   listeners: Python `socket(AF_UNIX).bind(p)`, `asyncio.start_unix_server(cb, p)`,
                                `socketserver.UnixStreamServer(p, Handler)`, `multiprocessing.connection.Listener(p)`,
                                `uvicorn.run(app, uds=p)`, aiohttp `UnixSite(runner, p)`; Node `server.listen(p)` /
                                `.listen({ path: p })` with a socket path; Rust `UnixListener::bind(p)` /
                                `UnixDatagram::bind(p)` (std, tokio); C `uv_pipe_bind(h, p)`, `bind()` after
                                `sun_path` is set; PHP `stream_socket_server('unix://p')`.
                                connectors: `socket(AF_UNIX).connect(p)`, `asyncio.open_unix_connection(p)`,
                                `multiprocessing.connection.Client(p)`, aiohttp `UnixConnector(path=p)`, httpx
                                `HTTPTransport(uds=p)`; Node `net.connect(p)` / `createConnection({ path })`,
                                `http.request({ socketPath })`; Rust `UnixStream::connect(p)`; C `uv_pipe_connect`,
                                `connect()` after `sun_path`; PHP `stream_socket_client('unix://p')`.
                                Listener attrs: `mode` when the path is chmod-ed in the same function.
  pipe   endpoint:pipe:<name>   Windows named pipes (`\\\\.\\pipe\\x`: Rust tokio `ServerOptions::new()..create(p)` /
                                `ClientOptions::new()..open(p)`, Node `listen` / `connect` on a pipe path, C
                                `CreateNamedPipe(p)` / `CreateFile(p)`), and FIFOs (`os.mkfifo(p)` / `mkfifo(p)`: the
                                creator receives; `open(p, 'w')` sends).
  dbus   endpoint:dbus:<interface>.<member>   services: zbus `#[interface(name = "..")]` impl methods (snake_case
                                -> PascalCase, `#[zbus(name = "..")]`), dbus-next / dasbus `ServiceInterface` `@method()`,
                                dbus-python `@dbus.service.method('iface')`; clients: zbus `#[proxy(interface = "..")]`
                                trait methods, dbus-next `call_<member>` on `get_interface('iface')`, dbus-python
                                `dbus.Interface(obj, 'iface').Member()`, GDBus `g_dbus_connection_call(.., "iface",
                                "Member", ..)`; signals (`#[zbus(signal)]`, `@signal()`) are sent by the service.

Paths are literals, constants, environment variables (`env:NAME`) or templates (`{dir}/app.sock`, heuristic);
a path from configuration only is counted under `unix_path_unknown`.
"""
from __future__ import annotations

import re

from .brokers import Scan, _args, _kw, _key, _strlit
from .core.model import EXACT, HEURISTIC, RESOLVED
from .protocols import protocol_receive, protocol_send

PY_EXT = (".py",)
JS_EXT = (".ts", ".tsx", ".js", ".mjs", ".cjs", ".mts", ".cts")
C_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp")
PIPE_RX = re.compile(r"^(?:\\\\|//)[.?](?:\\|/)pipe(?:\\|/)(.+)$", re.I)


def pascal(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in name.split("_") if p)


class LocalSockets(Scan):
    def run(self) -> dict:
        self.fifos = set()                       # FIFO paths made with mkfifo: their writers are found afterwards
        for f in sorted(self.s.files):
            src = self.s.text(f)
            if not src:
                continue
            try:
                if f.endswith(PY_EXT):
                    self.py(f, src)
                elif f.endswith(JS_EXT) and not f.endswith(".d.ts"):
                    self.js(f, src)
                elif f.endswith(".rs"):
                    self.rs(f, src)
                elif f.endswith(C_EXT):
                    self.c(f, src)
                elif f.endswith(".php"):
                    self.php(f, src)
            except (IndexError, KeyError, ValueError, TypeError, AttributeError) as e:
                self.miss("local_socket_scan_errors", f"{f}: {type(e).__name__} {e}")
        if self.fifos:
            self.fifo_writers()
        out = {k: v for k, v in self.st.items() if v}
        if out and self.samples:
            out["samples"] = dict(self.samples)
        return out

    # ---------------------------------------------------------------- helpers
    def is_test(self, file, fn):
        """Also Rust items inside `mod tests` (their ids carry `::tests::`) and in `tests.rs` modules."""
        return (super().is_test(file, fn) or bool(fn and "::tests::" in fn)
                or (file.endswith(".rs") and file.rsplit("/", 1)[-1] in ("tests.rs", "test.rs")))

    def lit(self, e):
        """A string literal with escapes unescaped, or a Rust raw string (r"..."), else None."""
        m = re.fullmatch(r'r(#*)"([^"]*)"\1', e)
        if m:
            return m.group(2)
        m = re.fullmatch(r"""[bu]?(['"`])((?:\\.|(?!\1).)*)\1""", e, re.S)
        if m and "${" not in m.group(2):
            return re.sub(r"\\(.)", r"\1", m.group(2))
        return None

    def path(self, file, pos, expr, depth=0):
        """(protocol, name, confidence) of a socket / pipe path expression, or (None, None, None)."""
        e = (expr or "").strip()
        lit = self.lit(e)
        if lit is not None:
            e = None
            v, conf = lit, EXACT
            return self._classify(v, conf)
        m = re.fullmatch(r"(?:[\w]+\.|self\.|Self::|crate::(?:\w+::)*)?([A-Z][A-Z0-9_]{2,})", e)
        if m and depth < 4:
            own = re.findall(rf"(?m)^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:export\s+)?(?:const|static|let|var|final)?\s*{m.group(1)}\s*(?::\s*[\w<>:&' ]+)?\s*=\s*([^\n;]+)", self.s.text(file))
            cands = [(file, own[0].strip())] if len(own) == 1 else [(f2, v2) for f2, v2 in self.s.const_index().get(m.group(1), ())]
            if len({v2 for _f, v2 in cands}) == 1:
                r = self.path(cands[0][0], 0, cands[0][1], depth + 1)
                if r[1] is not None:
                    return r
        m = re.fullmatch(r"(?:os\.path\.join|path\.(?:join|resolve)|posixpath\.join|Path)\s*\(([\s\S]*)\)", e)
        if m:
            parts = [x.strip() for x in _args(e, e.index("("))]
            vals, conf = [], RESOLVED
            for p in parts:
                v, c = self.value(file, pos, p)
                if v is None:
                    v, c = "{" + re.sub(r"\W+", "_", p).strip("_")[:30] + "}", HEURISTIC
                vals.append(v.rstrip("/"))
                conf = HEURISTIC if c == HEURISTIC else conf
            v = "/".join(vals)
        else:
            m = re.fullmatch(r"([\s\S]+?)\s*/\s*(['\"][^'\"]+['\"])", e)        # pathlib: base / 'x.sock'
            if m and not e.startswith(("'", '"')):
                a, ca = self.value(file, pos, m.group(1))
                v = (a if a is not None else "{" + re.sub(r"\W+", "_", m.group(1)).strip("_")[:30] + "}") + "/" + _strlit(m.group(2))
                conf = HEURISTIC if a is None or ca == HEURISTIC else RESOLVED
            else:
                v, conf = self.value(file, pos, e)
        return self._classify(v, conf)

    def _classify(self, v, conf):
        if v is None or not v.strip("{}"):
            return None, None, None
        v = re.sub(r"^unix:(?://)?", "", v)
        pm = PIPE_RX.match(v)
        if pm:
            return "pipe", pm.group(1), conf or EXACT
        return "unix", v, conf or EXACT

    def emit(self, role, file, pos, expr, how, handler=None, lib=None, proto=None, **attrs):
        p, name, conf = self.path(file, pos, expr) if expr is not None else (None, None, None)
        if name is None:
            self.miss("unix_path_unknown", f"{file}:{self.s.line_of(file, pos)} {how} {(expr or '')[:50]}")
            return
        p = proto or p
        fn = self.fn_at(file, pos)
        line = self.s.line_of(file, pos)
        key = (role, p, name, fn, line)
        if key in self.done or fn is None:
            return
        self.done.add(key)
        if role == "listen" and "mkfifo" in how:
            self.fifos.add(name)
        if role == "listen":
            h = handler or fn
            if self.is_test(file, h):
                return
            protocol_receive(self.b, p, name, h, file, line, conf, how=how, library=lib, **attrs)
        else:
            protocol_send(self.b, p, name, fn, file, line, conf, test=self.is_test(file, fn), role="connect" if p != "dbus" else "invoke",
                          library=lib, how=how, **attrs)
        self.st[f"{p}_{'listeners' if role == 'listen' else 'connectors'}"] += 1

    def unix_uri(self, role, f, pos, expr, how, lib):
        """A gRPC target / port that is a `unix:` URI (`unix:///run/x.sock`, `unix:rel.sock`); other targets are not ours."""
        e = (expr or "").strip()
        v = self.lit(e)
        if v is None:
            v, _c = self.value(f, pos, e)
        if v and v.startswith("unix:"):
            self.emit(role, f, pos, e, how, lib=lib)

    def mode_near(self, src, pos, file):
        _fn, lo, hi = self.s.fn_bounds(file, pos)
        m = re.search(r"\b(?:os\.chmod|chmod|fs\.chmodSync|fs\.promises\.chmod|set_permissions)\s*\([^)]*?(0o?[0-7]{3,4}|from_mode\(\s*0o?[0-7]{3,4})", src[lo:hi])
        return re.sub(r"from_mode\(\s*", "", m.group(1)) if m else None

    def calls(self, src, rx):
        for m in rx.finditer(src):
            if src[m.end() - 1] == "(":
                yield m, _args(src, m.end() - 1)

    def fifo_writers(self):
        """`open(p, 'w')` / `os.open(p, os.O_WRONLY)` / C `open(p, O_WRONLY)` / `fopen(p, "w")` on a path some code
        made with mkfifo: the writer sends to the FIFO's creator."""
        rx = re.compile(r"(?<![\w])(?:os\.open|open|fopen)\s*\(")
        for f in sorted(self.s.files):
            if not f.endswith(PY_EXT + C_EXT):
                continue
            src = self.s.text(f) or ""
            if "open" not in src:
                continue
            for m, args in self.calls(src, rx):
                if len(args) < 2 or self.s.masked(f, m.start()):
                    continue
                mode = _kw(args, "mode") or args[1]
                ml = self.lit(mode.strip())
                if not ((ml is not None and re.match(r"[wa]", ml)) or re.search(r"O_WRONLY|O_RDWR", mode)):
                    continue
                p, name, _c = self.path(f, m.start(), args[0])
                if name in self.fifos:
                    self.emit("connect", f, m.start(), args[0], m.group(0).rstrip("( ") + " (FIFO writer)",
                              lib="fifo", proto="pipe")

    # ---------------------------------------------------------------- Python
    def py(self, f, src):
        if not re.search(r"AF_UNIX|unix_server|unix_connection|UnixStreamServer|UnixDatagramServer|multiprocessing\.connection|"
                         r"uds\s*=|UnixSite|UnixConnector|mkfifo|dbus|unix:", src):
            return
        if "AF_UNIX" in src:
            socks = {m.group(1) for m in re.finditer(r"\b([\w.]+)\s*=\s*(?:socket\.)?socket\s*\(\s*(?:socket\.)?AF_UNIX", src)}
            for m, args in self.calls(src, re.compile(r"\b([\w.]+)\s*\.\s*(bind|connect|connect_ex)\s*\(")):
                if m.group(1) in socks and args and not self.s.masked(f, m.start()):
                    role = "listen" if m.group(2) == "bind" else "connect"
                    self.emit(role, f, m.start(), args[0], f"socket(AF_UNIX).{m.group(2)}", lib="socket",
                              mode=self.mode_near(src, m.start(), f) if role == "listen" else None)
        for m, args in self.calls(src, re.compile(r"\basyncio\.start_unix_server\s*\(|\bstart_unix_server\s*\(")):
            p = _kw(args, "path") or (args[1] if len(args) > 1 and "=" not in args[1] else None)
            h = self.handler(f, m.start(), args[0]) if args else None
            self.emit("listen", f, m.start(), p, "asyncio.start_unix_server", handler=h, lib="asyncio",
                      mode=self.mode_near(src, m.start(), f))
        for m, args in self.calls(src, re.compile(r"\b(?:asyncio\.)?open_unix_connection\s*\(")):
            self.emit("connect", f, m.start(), _kw(args, "path") or (args[0] if args else None), "asyncio.open_unix_connection", lib="asyncio")
        for m, args in self.calls(src, re.compile(r"\b(?:socketserver\.)?(?:Threading|Forking)?Unix(?:Stream|Datagram)Server\s*\(")):
            h = self.s.handler(args[1] + ".handle", f) if len(args) > 1 else None
            self.emit("listen", f, m.start(), args[0] if args else None, "socketserver.UnixStreamServer",
                      handler=h or (self.handler(f, m.start(), args[1]) if len(args) > 1 else None), lib="socketserver")
        if "multiprocessing.connection" in src or re.search(r"from\s+multiprocessing\.connection\s+import", src):
            for m, args in self.calls(src, re.compile(r"(?<![\w.])(?:connection\.)?(Listener|Client)\s*\(")):
                a = _kw(args, "address") or (args[0] if args and "=" not in args[0] else None)
                if a and not a.lstrip().startswith("("):
                    self.emit("listen" if m.group(1) == "Listener" else "connect", f, m.start(), a,
                              f"multiprocessing.connection.{m.group(1)}", lib="multiprocessing")
        for m, args in self.calls(src, re.compile(r"\buvicorn\.run\s*\(|\bweb\.run_app\s*\(|\bUnixSite\s*\(")):
            p = _kw(args, "uds") or _kw(args, "path") or (args[1] if "UnixSite" in m.group(0) and len(args) > 1 else None)
            if p:
                self.emit("listen", f, m.start(), p, m.group(0).rstrip("( "), lib="http")
        for m, args in self.calls(src, re.compile(r"\bUnixConnector\s*\(|\b(?:Async)?HTTPTransport\s*\(")):
            p = _kw(args, "path") or _kw(args, "uds")
            if p:
                self.emit("connect", f, m.start(), p, m.group(0).rstrip("( "), lib="http")
        for m, args in self.calls(src, re.compile(r"\bos\.mkfifo\s*\(")):
            self.emit("listen", f, m.start(), args[0] if args else None, "os.mkfifo", lib="fifo", proto="pipe")
        if "grpc" in src:                            # grpc.insecure_channel('unix:..') / server.add_insecure_port('unix:..')
            for m, args in self.calls(src, re.compile(r"\b(?:grpc\.)?(?:aio\.)?(?:insecure|secure)_channel\s*\(|\.add_(?:insecure|secure)_port\s*\(")):
                if args:
                    self.unix_uri("connect" if "channel" in m.group(0) else "listen", f, m.start(), args[0],
                                  m.group(0).strip(".( "), "grpc")
        if "dbus" in src:
            self.py_dbus(f, src)

    def py_dbus(self, f, src):
        # dbus-next / dasbus: class X(ServiceInterface): super().__init__('org.x.Y'); @method() def Member(..)
        for c in re.finditer(r"^class\s+(\w+)\s*\(\s*(?:[\w.]*\.)?ServiceInterface\s*\)\s*:", src, re.M):
            end = re.search(r"^\S", src[c.end():], re.M)
            body_hi = c.end() + (end.start() if end else len(src) - c.end())
            iface = re.search(r"super\(\)\s*\.\s*__init__\s*\(\s*([^)]+)\)", src[c.end():body_hi])
            name, _c = self.value(f, c.end(), iface.group(1)) if iface else (None, None)
            if not name:
                self.miss("dbus_interface_unknown", f"{f}:{self.s.line_of(f, c.start())}")
                continue
            for d in re.finditer(r"^\s*@(?:[\w.]*\.)?(method|signal|dbus_property)\s*\(([^)]*)\)\s*\n\s*(?:async\s+)?def\s+(\w+)", src[c.end():body_hi], re.M):
                member = _strlit(_kw(_args(d.group(0), d.group(0).index("(")), "name") or "") or d.group(3)
                pos = c.end() + d.start()
                h = self.s.handler(f"{c.group(1)}.{d.group(3)}", f) or self.fn_at(f, pos)
                ep = f"{name}.{member}"
                if d.group(1) == "signal":
                    self.dbus_send(f, pos, ep, h, "dbus-next @signal", role="emit")
                else:
                    self.dbus_recv(f, pos, ep, h, f"dbus-next @{d.group(1)}")
        # dbus-python: @dbus.service.method('org.x.Y', ...) def Member
        for d in re.finditer(r"^\s*@dbus\.service\.(method|signal)\s*\(\s*([^,)]+)[^)]*\)\s*\n\s*def\s+(\w+)", src, re.M):
            iface, _c = self.value(f, d.start(), d.group(2))
            if not iface:
                continue
            h = self.fn_at(f, d.end())
            if d.group(1) == "signal":
                self.dbus_send(f, d.start(), f"{iface}.{d.group(3)}", h, "dbus.service.signal", role="emit")
            else:
                self.dbus_recv(f, d.start(), f"{iface}.{d.group(3)}", h, "dbus.service.method")
        # clients: iface = proxy.get_interface('org.x.Y'); iface.call_member(..)  |  dbus.Interface(obj, 'org.x.Y').Member(..)
        for v in re.finditer(r"\b(\w+)\s*=\s*(?:await\s+)?[\w.]*\.get_interface\s*\(\s*([^)]+)\)", src):
            iface, _c = self.value(f, v.start(), v.group(2))
            if not iface:
                continue
            for c in re.finditer(rf"\b{re.escape(v.group(1))}\s*\.\s*call_(\w+)\s*\(", src[v.end():]):
                pos = v.end() + c.start()
                self.dbus_send(f, pos, f"{iface}.{pascal(c.group(1))}", self.fn_at(f, pos), "dbus-next call_")
        for v in re.finditer(r"\b(\w+)\s*=\s*dbus\.Interface\s*\(\s*[^,]+,\s*(?:dbus_interface\s*=\s*)?([^)]+)\)", src):
            iface, _c = self.value(f, v.start(), v.group(2))
            if not iface:
                continue
            for c in re.finditer(rf"\b{re.escape(v.group(1))}\s*\.\s*([A-Z]\w*)\s*\(", src[v.end():]):
                pos = v.end() + c.start()
                self.dbus_send(f, pos, f"{iface}.{c.group(1)}", self.fn_at(f, pos), "dbus.Interface")

    def dbus_recv(self, f, pos, ep, h, how):
        line = self.s.line_of(f, pos)
        if ("r", ep, h) in self.done or h is None or self.is_test(f, h):
            return
        self.done.add(("r", ep, h))
        protocol_receive(self.b, "dbus", ep, h, f, line, EXACT, how=how)
        self.st["dbus_members"] += 1

    def dbus_send(self, f, pos, ep, src_fn, how, role="invoke"):
        line = self.s.line_of(f, pos)
        if ("s", ep, src_fn, line) in self.done or src_fn is None:
            return
        self.done.add(("s", ep, src_fn, line))
        protocol_send(self.b, "dbus", ep, src_fn, f, line, EXACT, test=self.is_test(f, src_fn), role=role, how=how)
        self.st["dbus_calls" if role == "invoke" else "dbus_signals"] += 1

    # ---------------------------------------------------------------- Node
    def js(self, f, src):
        if not re.search(r"""(?:require\(\s*|from\s+)['"](?:node:)?(?:net|http|https)['"]|socketPath|\bnet\.""", src):
            return
        for m, args in self.calls(src, re.compile(r"\.\s*listen\s*\(")):
            if not args or self.s.masked(f, m.start()):
                continue
            a = args[0]
            p = _key(a, "path") if a.strip().startswith("{") else a
            if not p:
                continue
            proto, name, _c = self.path(f, m.start(), p)
            if name is None or not (proto == "pipe" or name.startswith(("/", "{", "env:", "~")) or name.endswith((".sock", ".socket", ".ipc"))):
                continue                       # a port number / host
            self.emit("listen", f, m.start(), p, "server.listen(path)", lib="net", mode=self.mode_near(src, m.start(), f))
        for m, args in self.calls(src, re.compile(r"\b(?:net\.)?(?:connect|createConnection)\s*\(")):
            if not args or self.s.masked(f, m.start()):
                continue
            a = args[0]
            p = _key(a, "path") if a.strip().startswith("{") else (a if len(args) == 1 or args[1].strip().startswith(("(", "function", "async")) else None)
            if not p:
                continue
            proto, name, _c = self.path(f, m.start(), p)
            if name is None or not (proto == "pipe" or name.startswith(("/", "env:")) or name.endswith((".sock", ".socket", ".ipc"))):
                continue
            self.emit("connect", f, m.start(), p, "net.connect(path)", lib="net")
        for m in re.finditer(r"\bsocketPath\s*:\s*", src):
            if self.s.masked(f, m.start()):
                continue
            e = re.match(r"[^,}\n]+", src[m.end():])
            if e:
                self.emit("connect", f, m.start(), e.group(0).strip(), "http socketPath", lib="http")

    # ---------------------------------------------------------------- Rust
    def rs(self, f, src):
        if not re.search(r"Unix(?:Listener|Stream|Datagram)|named_pipe|zbus|dbus|unix:", src):
            return
        for m, args in self.calls(src, re.compile(r"\b(?:(?:Endpoint|Channel)::(?:try_from|from_static|from_shared)|\w+Client::connect)\s*\(")):
            if args:                                 # tonic: Endpoint::try_from / XClient::connect("unix:///x.sock")
                self.unix_uri("connect", f, m.start(), args[0].lstrip("&"), m.group(0).rstrip("( "), "tonic")
        for m, args in self.calls(src, re.compile(r"\bUnix(Listener|Datagram)::bind(?:_addr)?\s*\(")):
            if args and not self.s.masked(f, m.start()):
                self.emit("listen", f, m.start(), args[0].lstrip("&"), f"Unix{m.group(1)}::bind", lib="std/tokio",
                          mode=self.mode_near(src, m.start(), f))
        for m, args in self.calls(src, re.compile(r"\bUnixStream::connect\s*\(")):
            if args and not self.s.masked(f, m.start()):
                self.emit("connect", f, m.start(), args[0].lstrip("&"), "UnixStream::connect", lib="std/tokio")
        for m, args in self.calls(src, re.compile(r"\bServerOptions::new\(\)[\s\S]{0,200}?\.create\s*\(")):
            if args:
                self.emit("listen", f, m.start(), args[0].lstrip("&"), "named_pipe::ServerOptions.create", lib="tokio", proto="pipe")
        for m, args in self.calls(src, re.compile(r"\bClientOptions::new\(\)[\s\S]{0,200}?\.open\s*\(")):
            if args:
                self.emit("connect", f, m.start(), args[0].lstrip("&"), "named_pipe::ClientOptions.open", lib="tokio", proto="pipe")
        if "zbus" in src or "#[interface" in src or "#[proxy" in src:
            self.zbus(f, src)

    def zbus(self, f, src):
        attr = r"#\[\s*(?:zbus::)?(interface|dbus_interface|proxy|dbus_proxy)\s*\(([^\]]*)\)\s*\]"
        for a in re.finditer(attr, src):
            iface = re.search(r"\b(?:name|interface)\s*=\s*\"([^\"]+)\"", a.group(2))
            if not iface:
                self.miss("dbus_interface_unknown", f"{f}:{self.s.line_of(f, a.start())}")
                continue
            kind = "service" if "interface" in a.group(1) else "proxy"
            block = re.search(r"\b(?:impl(?:<[^>]*>)?\s+[\w:<>]+|(?:pub(?:\([^)]*\))?\s+)?trait\s+\w+)[^{]*\{", src[a.end():a.end() + 600])
            if not block:
                continue
            lo = a.end() + block.end()
            depth, j = 1, lo
            while j < len(src) and depth:
                depth += {"{": 1, "}": -1}.get(src[j], 0)
                j += 1
            body = src[lo:j]
            depth_at, d = [], 0                      # brace depth per offset: only the block's own fns count
            for ch in body:
                depth_at.append(d)
                d += {"{": 1, "}": -1}.get(ch, 0)
            for fm in re.finditer(r"((?:#\[[^\]]*\]\s*|///[^\n]*\n\s*)*)(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+((?:r#)?\w+)", body):
                attrs, fname = fm.group(1), fm.group(2).removeprefix("r#")
                if depth_at[fm.start(2)] != 0 or fname in ("new",) or \
                        re.search(r"#\[\s*(?:zbus|dbus_interface)\s*\(\s*(?:out_args|skip)", attrs):
                    continue
                nm = re.search(r"\bname\s*=\s*\"([^\"]+)\"", attrs)
                if not nm and re.search(r"\bproperty\b", attrs) and fname.startswith("set_"):
                    fname = fname[4:]                # #[zbus(property)] fn set_x(..): the setter of property X
                member = nm.group(1) if nm else pascal(fname)
                pos = lo + fm.start(2)
                fn = self.fn_at(f, pos)
                ep = f"{iface.group(1)}.{member}"
                signal = re.search(r"\bsignal\b", attrs)
                if kind == "service":
                    if signal:
                        self.dbus_send(f, pos, ep, fn, "zbus #[zbus(signal)]", role="emit")
                    else:
                        self.dbus_recv(f, pos, ep, fn, "zbus #[interface]")
                elif signal:
                    self.dbus_recv(f, pos, ep, fn, "zbus #[proxy] signal")
                else:
                    self.dbus_send(f, pos, ep, fn, "zbus #[proxy]")

    # ---------------------------------------------------------------- C / C++
    def c(self, f, src):
        if not re.search(r"sun_path|uv_pipe_|CreateNamedPipe|mkfifo|g_dbus_connection_call|sd_bus_call_method", src):
            return
        for m in re.finditer(r"\b(?:strn?cpy|snprintf|memcpy|strlcpy)\s*\(\s*[\w.>\-]*sun_path\s*,\s*", src):
            args = _args(src, src.rfind("(", 0, m.end()))
            if "printf" in m.group(0):                 # snprintf(sun_path, n, "%s", p) / snprintf(sun_path, n, "/run/x.sock")
                fmt = self.lit(args[2]) if len(args) > 2 else None
                p = args[3] if fmt == "%s" and len(args) > 3 else (args[2] if fmt and "%" not in fmt else None)
            else:                                      # strncpy / strcpy / strlcpy / memcpy(sun_path, p, ..)
                p = args[1] if len(args) > 1 else None
            fn, lo, hi = self.s.fn_bounds(f, m.start())
            seg = src[m.end():hi]
            role = "listen" if re.search(r"\bbind\s*\(", seg) else "connect" if re.search(r"\bconnect\s*\(", seg) else None
            if role and p:
                self.emit(role, f, m.start(), p, f"sockaddr_un + {role if role == 'connect' else 'bind'}", lib="libc")
        for m, args in self.calls(src, re.compile(r"\buv_pipe_bind2?\s*\(")):
            if len(args) > 1:
                self.emit("listen", f, m.start(), args[1], "uv_pipe_bind", lib="libuv")
        for m, args in self.calls(src, re.compile(r"\buv_pipe_connect2?\s*\(")):
            if len(args) > 2:
                self.emit("connect", f, m.start(), args[2], "uv_pipe_connect", lib="libuv")
        for m, args in self.calls(src, re.compile(r"\bCreateNamedPipe[AW]?\s*\(")):
            if args:
                self.emit("listen", f, m.start(), re.sub(r'^[LTu8]+(?=")|^TEXT\((.*)\)$', r"\1", args[0]), "CreateNamedPipe", lib="win32", proto="pipe")
        for m, args in self.calls(src, re.compile(r"(?<![\w.])mkfifo\s*\(")):
            if args:
                self.emit("listen", f, m.start(), args[0], "mkfifo", lib="libc", proto="pipe")
        for m, args in self.calls(src, re.compile(r"\bg_dbus_connection_call(?:_sync)?\s*\(")):
            if len(args) > 4:
                iface, _c = self.value(f, m.start(), args[3])
                member, _c2 = self.value(f, m.start(), args[4])
                if iface and member:
                    self.dbus_send(f, m.start(), f"{iface}.{member}", self.fn_at(f, m.start()), "g_dbus_connection_call")
        for m, args in self.calls(src, re.compile(r"\bsd_bus_call_method\s*\(")):
            if len(args) > 4:
                iface, _c = self.value(f, m.start(), args[3])
                member, _c2 = self.value(f, m.start(), args[4])
                if iface and member:
                    self.dbus_send(f, m.start(), f"{iface}.{member}", self.fn_at(f, m.start()), "sd_bus_call_method")

    # ---------------------------------------------------------------- PHP
    def php(self, f, src):
        if "unix://" not in src:
            return
        for m, args in self.calls(src, re.compile(r"\bstream_socket_(server|client)\s*\(")):
            if args:
                self.emit("listen" if m.group(1) == "server" else "connect", f, m.start(), args[0],
                          f"stream_socket_{m.group(1)}", lib="php")


def apply(project, builder, sock=None) -> dict:
    """Unix domain sockets, named pipes / FIFOs and D-Bus (#38 part 2)."""
    return LocalSockets(project, builder, sock).run()

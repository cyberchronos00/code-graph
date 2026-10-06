"""Socket.IO in Dart, Kotlin / Java, Swift and Rust (#32 part 3) -> endpoint:socketio:<namespace>#<event>, the model of
the JS / TS (cg_code_graph/realtime_events.py) and python-socketio sides, so a mobile app links with its server.

Files that import a Socket.IO library (and only those) are scanned:
  Dart    socket_io_client (client): `io(url, ..)` / `IO.io(..)`, `IO.Socket` / `Socket` typed names
  Kotlin / Java  socket.io-client-java `io.socket.client` (client): `IO.socket(url)`, `Socket` typed names
  Swift   socket.io-client-swift `import SocketIO` (client): `SocketManager(..)`, `manager.defaultSocket`,
          `manager.socket(forNamespace: "/ns")`, `SocketIOClient` typed names
  Rust    socketioxide (server): `io.ns("/ns", handler)` (the handler's events are in that namespace),
          `socket.to(room).emit(..)`, `io.emit(..)`; rust_socketio (client): `ClientBuilder::new(url).namespace("/ns")
          .on("e", cb)`, `client.emit("e", ..)`
  receive `X.on("event", handler)` / `once` (a trailing closure or an inline callback: the function around it)
  send    `X.emit("event", ..)`; `emitWithAck` / an ack callback as the last argument (role request)
Objects are the names above or, in such a file, receivers called `socket` / `io` / `sio` / `client` / `s`. Lifecycle
events (connect, disconnect, error, ...) and `Socket.EVENT_*` / `clientEvent:` constants are not endpoints.
"""
from __future__ import annotations

import re

from .brokers import Scan as BrokerScan, _args, _whole_ph
from .core.model import EXACT, HEURISTIC, RESOLVED
from .realtime_events import LIFECYCLE

LIBS = {
    ".dart": [(re.compile(r"""import\s+['"]package:socket_io_client/"""), "client", "socket_io_client")],
    ".kt": [(re.compile(r"^\s*import\s+io\.socket\.(?:client|emitter)\b", re.M), "client", "socket.io-client-java")],
    ".java": [(re.compile(r"^\s*import\s+io\.socket\.(?:client|emitter)\b", re.M), "client", "socket.io-client-java")],
    ".swift": [(re.compile(r"^\s*import\s+SocketIO\b", re.M), "client", "socket.io-client-swift")],
    ".rs": [(re.compile(r"\bsocketioxide\b"), "server", "socketioxide"), (re.compile(r"\brust_socketio\b"), "client", "rust_socketio")],
}
NAME_OK = re.compile(r"socket|^io$|^sio$|^s$|^client$|^ws$", re.I)
CALL = re.compile(r"((?:self\.|this\.)?[A-Za-z_]\w*(?:\s*[?!]?\.\s*[A-Za-z_]\w*(?:\(\s*\))?)*?)"
                  r"((?:\s*[?!]?\.\s*(?:to|within|except|broadcast|local|timeout|volatile)\s*\([^()]*\))*)"
                  r"\s*[?!]?\.\s*(on|once|emit|emitWithAck)\s*(?:::<[^>]*>)?\s*\(")
LIFE_EXTRA = {"connect_error", "reconnect", "reconnecting", "statusChange", "ping", "pong", "websocketUpgrade"}


class Native(BrokerScan):
    def __init__(self, project, b, sock=None):
        super().__init__(project, b, sock)
        self._consts = {}

    def val(self, f, pos, expr):
        """value() plus Dart / Kotlin `'$x/${y}'` and Swift `"\\(x)"` interpolation and `Owner.CONST` string
        constants of a class / object / struct / enum in a file of the same language."""
        e = (expr or "").strip()
        m = re.fullmatch(r"'([^'\n]*)'|\"([^\"\n]*)\"", e)
        if m and re.search(r"\$|\\\(", m.group(1) or m.group(2) or ""):
            body = m.group(1) if m.group(1) is not None else m.group(2)
            body = re.sub(r"\$\{([^{}]*)\}|\$([A-Za-z_]\w*)|\\\(([^()]*)\)",
                          lambda mm: "{" + re.sub(r"\W+", "_", (mm.group(1) or mm.group(2) or mm.group(3)).split(".")[-1]) + "}", body)
            return body, HEURISTIC if body.strip("{}") != body else EXACT
        m = re.fullmatch(r"([A-Z]\w*)\s*(?:\.|::)\s*([A-Za-z_]\w*)", e)
        if m:
            v = self.const(f, m.group(1), m.group(2))
            if v is not None:
                return v, RESOLVED
        return self.value(f, pos, e)

    def const(self, f, owner, member):
        ext = "." + f.rsplit(".", 1)[-1]
        key = (ext, owner, member)
        if key not in self._consts:
            rx = re.compile(rf"\b(?:static\s+)?(?:const\s+(?:val\s+)?|let\s+|final\s+)(?:[\w<>?]+\s+)?{re.escape(member)}\s*"
                            rf"(?::\s*(?:&(?:'static\s+)?)?\w+\s*)?=\s*(?:'([^'\n]*)'|\"([^\"\n]*)\")")
            own = re.compile(rf"\b(?:class|object|struct|enum|impl|abstract\s+class)\s+{re.escape(owner)}\b")
            hits = set()
            for g in self.s.files:
                if not g.endswith(ext):
                    continue
                t = self.s.text(g)
                if member not in t or not own.search(t):
                    continue
                for mm in rx.finditer(t):
                    hits.add(mm.group(1) if mm.group(1) is not None else mm.group(2))
            self._consts[key] = hits.pop() if len(hits) == 1 else None
        return self._consts[key]

    def run(self) -> dict:
        for f in sorted(self.s.files):
            ext = "." + f.rsplit(".", 1)[-1] if "." in f else ""
            libs = LIBS.get(ext)
            if not libs:
                continue
            src = self.s.text(f)
            if ".on" not in src and ".emit" not in src:
                continue
            hits = [(role, lib) for rx, role, lib in libs if rx.search(src)]
            if len(hits) != 1:
                continue                                    # none, or a Rust file with both server and client
            self.file(f, src, ext, *hits[0])
        out = {k: v for k, v in self.st.items() if v}
        if out and self.samples:
            out["samples"] = dict(self.samples)
        return out

    # ------------------------------------------------------------ objects and namespaces
    def objects(self, f, src, ext):
        objs = {}
        if ext == ".dart":
            for m in re.finditer(r"\b(\w+)\s*=\s*(?:IO\s*\.\s*)?io\s*\(", src):
                a = _args(src, src.index("(", m.end() - 1))
                objs[m.group(1)] = self.ns_of_url(f, m.start(), a[0] if a else "")
            for m in re.finditer(r"\b(?:IO\s*\.\s*)?Socket\??\s+(\w+)\b", src):
                objs.setdefault(m.group(1), "/")
        elif ext in (".kt", ".java"):
            for m in re.finditer(r"\b(\w+)\s*(?::\s*Socket\??\s*)?=\s*IO\s*\.\s*socket\s*\(", src):
                a = _args(src, src.index("(", m.end() - 1))
                objs[m.group(1)] = self.ns_of_url(f, m.start(), a[0] if a else "")
            for m in re.finditer(r"\b(\w+)\s*:\s*Socket\b|\bSocket\s+(\w+)\s*[;=]", src):
                objs.setdefault(m.group(1) or m.group(2), "/")
        elif ext == ".swift":
            for m in re.finditer(r"\b(\w+)\s*(?::\s*SocketIOClient!?\??\s*)?=\s*[\w.]*?\s*\.?\s*defaultSocket\b", src):
                objs[m.group(1)] = "/"
            for m in re.finditer(r"\b(\w+)\s*(?::\s*SocketIOClient!?\??\s*)?=\s*[\w.]+\s*\.\s*socket\s*\(\s*forNamespace\s*:\s*([^)]+)\)", src):
                v, _c = self.val(f, m.start(), m.group(2))
                objs[m.group(1)] = (v if v.startswith("/") else "/" + v) if v and "{" not in v else ""
            for m in re.finditer(r"\b(\w+)\s*:\s*SocketIOClient\b", src):
                objs.setdefault(m.group(1), "/")
        return objs

    def ns_of_url(self, f, pos, arg):
        a = (arg or "").strip()
        v, _c = self.val(f, pos, a) if a else (None, None)
        if v is None:
            return "/"                                      # a configured server origin: no namespace path
        m = re.match(r"^(?:[a-z]+:)?//[^/]*(/[^?#]*)?|^\{[^{}/]*\}(/[^?#]*)?|^(/[^?#]*)", v)
        p = ((m.group(1) or m.group(2) or m.group(3)) if m else None) or "/"
        p = p.rstrip("/") or "/"
        return "" if "{" in p else p

    def rust_ns_spans(self, f, src):
        """`io.ns("/chat", on_connect)`: (lo, hi, namespace) of the handler's body."""
        out = []
        for m in re.finditer(r"\.\s*ns\s*\(", src):
            a = _args(src, m.end() - 1)
            if len(a) < 2:
                continue
            v, _c = self.val(f, m.start(), a[0])
            if not v or "{" in v:
                continue
            ns = v if v.startswith("/") else "/" + v
            h = a[1].strip()
            if re.fullmatch(r"[A-Za-z_][\w:]*", h):
                for n in self.b.nodes.values():
                    if n.file == f and n.kind == "function" and (n.name or "").split("::")[-1].split(".")[-1] == h.split("::")[-1] and n.line:
                        out.append((self.s.off(f, n.line), self.s.off(f, (n.end_line or n.line) + 1) if (n.end_line or n.line) < len(self.s.lines[f]) else len(src), ns))
            else:
                out.append((m.end(), self._close(src, m.end() - 1), ns))
        # handlers registered inside a namespace's handler (`socket.on("send", on_send)`) are in that namespace too
        for lo, hi, ns in list(out):
            for hm in re.finditer(r"\.\s*on\s*\(\s*\"[^\"\n]*\"\s*,\s*([A-Za-z_]\w*)\s*\)", src[lo:hi]):
                for n in self.b.nodes.values():
                    if n.file == f and n.kind == "function" and (n.name or "").split("::")[-1] == hm.group(1) and n.line:
                        end = (n.end_line or n.line) + 1
                        out.append((self.s.off(f, n.line), self.s.off(f, end) if end <= len(self.s.lines[f]) else len(src), ns))
        return out

    @staticmethod
    def _close(src, i):
        depth = 0
        for j in range(i, min(len(src), i + 200000)):
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    return j
        return len(src)

    # ------------------------------------------------------------ calls
    def file(self, f, src, ext, role, lib):
        objs = self.objects(f, src, ext)
        spans = self.rust_ns_spans(f, src) if ext == ".rs" and role == "server" else []
        extra = []
        chain_ns = []                                       # rust_socketio ClientBuilder::new(..).namespace("/x") chains
        if ext == ".rs" and role == "client":
            for m in re.finditer(r"ClientBuilder\s*::\s*new\s*\(", src):
                end = src.find(".connect", m.end())
                end = end if end > 0 else len(src)
                nm = re.search(r"\.\s*namespace\s*\(\s*([^()]+)\)", src[m.end():end])
                v = self.val(f, m.start(), nm.group(1))[0] if nm else "/"
                ns = (v if v.startswith("/") else "/" + v) if v and "{" not in v else ("" if nm else "/")
                chain_ns.append((m.start(), end, ns))
                lv = re.search(r"\blet\s+(?:mut\s+)?(\w+)\s*(?::[^=]+)?=\s*$", src[max(0, m.start() - 80):m.start()])
                if lv:
                    objs[lv.group(1)] = ns
                for om in re.finditer(r"\.\s*on\s*\(", src[m.end():end]):
                    extra.append(m.end() + om.start())       # `.on("e", cb)` in the builder chain
        found = [(m.start(), m.group(1), m.group(2) or "", m.group(3), m.start(3), m.end() - 1) for m in CALL.finditer(src)]
        seen = {x[5] for x in found}
        for at in extra:
            paren = src.index("(", at)
            if paren not in seen:
                found.append((at, ")", "", "on", src.index("on", at), paren))
        for start, otext, chain, meth, pos, paren in found:
            if self.s.masked(f, start):
                continue
            otext = re.sub(r"\s+|[?!](?=\.)", "", otext)
            obj = re.sub(r"^(?:self|this)\.", "", otext)
            last = re.sub(r"\(\)$", "", obj.split(".")[-1])
            ns, oconf = None, None
            for lo, hi, n in spans + chain_ns:
                if lo <= start <= hi:
                    ns, oconf = n, EXACT
            if ns is None:
                if obj in objs or last in objs:
                    ns, oconf = objs.get(obj, objs.get(last)), EXACT if obj in objs else RESOLVED
                elif NAME_OK.search(last):
                    ns, oconf = "/", HEURISTIC
                else:
                    continue
            a = _args(src, paren)
            if not a:
                continue
            first = a[0].strip()
            if re.match(r"(?:clientEvent\s*:|Socket\s*\.\s*EVENT_|SocketClientEvent\.|\.)", first):
                continue                                    # Swift `on(clientEvent: .connect)`, Kotlin Socket.EVENT_CONNECT
            ev, econf = self.val(f, pos, first)
            if ev is None or _whole_ph(ev):
                self.miss(f"socketio_{'receive' if meth in ('on', 'once') else 'send'}_unresolved", f"{f}:{self.s.line_of(f, pos)}")
                continue
            if ev in LIFECYCLE or ev in LIFE_EXTRA:
                continue
            if ns == "":
                self.miss("socketio_namespace_unknown", f"{f}:{self.s.line_of(f, pos)}")
                continue
            conf = HEURISTIC if HEURISTIC in (oconf, econf) or "{" in ev else (RESOLVED if RESOLVED in (oconf, econf) else EXACT)
            name = f"{ns}#{ev}"
            line = self.s.line_of(f, pos)
            if meth in ("on", "once"):
                h = a[1].strip() if len(a) > 1 else ""
                hid = self.handler(f, pos, h) if re.fullmatch(r"[A-Za-z_][\w.:]*", h) else self.fn_at(f, pos)
                if hid is None or self.is_test(f, hid):
                    continue
                key = ("r", name, hid, line)
                if key in self.done:
                    continue
                self.done.add(key)
                from .protocols import protocol_receive
                protocol_receive(self.b, "socketio", name, hid, f, line, conf, guards=[] if role == "server" else None,
                                 library=lib, process=role, node_attrs={"namespace": ns, "event": ev})
                self.st["socketio_receivers"] += 1
            else:
                fn = self.fn_at(f, pos)
                if fn is None:
                    continue
                key = ("s", name, fn, line)
                if key in self.done:
                    continue
                self.done.add(key)
                room = re.search(r"\.\s*(?:to|within)\s*\(\s*([^()]*)\)", chain)
                rv = None
                if room:
                    rv = self.val(f, pos, room.group(1))[0] or room.group(1).strip()[:60]
                ack = meth == "emitWithAck" or (len(a) > 1 and re.match(r"\s*(?:Ack\s*\{|object\s*:\s*Ack\b|\|[^|]*\|\s*(?:async\s+)?(?:move\s+)?\{?|\([^()]*\)\s*\{|\{\s*\w+\s*->)", a[-1]))
                from .protocols import protocol_send
                protocol_send(self.b, "socketio", name, fn, f, line, conf, test=self.is_test(f, fn), role="request" if ack else "emit",
                              library=lib, process=role, room=rv, broadcast=True if re.search(r"\.\s*broadcast\b", chain) else None,
                              node_attrs={"namespace": ns, "event": ev})
                self.st["socketio_sends"] += 1


def apply(project, builder, sock=None) -> dict:
    """Socket.IO endpoints of Dart, Kotlin / Java, Swift and Rust code (#32 part 3); empty without any."""
    return Native(project, builder, sock).run()

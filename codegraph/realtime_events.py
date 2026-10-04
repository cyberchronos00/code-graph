"""Socket.IO events in JS / TS (#32 part 1) -> endpoint:socketio:<namespace>#<event>, the model the python-socketio
extractor (codegraph/plugins/python/socketio.py) uses, so a Node / browser side links with a Python side.

Objects (a file that imports `socket.io` / `socket.io-client`, or whose package.json depends on exactly one of them):
  server   `new Server(http)` / `require('socket.io')(http)` / `socketIo(http)`, `io.of('/ns')` namespaces, and the
           socket parameter of `io.on('connection', (socket) => ..)`
  client   `io(url)` / `io.connect(url)` / `new Manager(url).socket('/ns')` (namespace = the URL path), and
           `this.socket` / `socket` / `*.socket` names in a client file
  receive  `socket.on('event', handler)` / `socket.once(..)` (inline callbacks: the enclosing function)
  send     `emit('event', ..)` on io / a namespace / a socket, through `.to(room)` / `.in(room)` / `.except(..)` /
           `.broadcast` / `.volatile` / `.timeout(ms)` (attrs.room); `emitWithAck` or a callback as the last argument
           is role `request`; `socket.send(..)` is the event `message`
  guards   `io.use(mw)` / `ns.use(mw)` on the server: the guard of that namespace's server receivers; without one
           they record guards [] (`unguarded`)
Lifecycle events (connect, connection, disconnect, disconnecting, connect_error, error, reconnect*) are not endpoints.
Event names: literals, constants, enum members, `${x}` templates (`{x}`, heuristic).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from .brokers import JS_EXT, Scan as BrokerScan, _args, _whole_ph
from .core.model import EXACT, HEURISTIC, RESOLVED

LIB = {"server": "socket.io", "client": "socket.io-client"}
LIFECYCLE = {"connect", "connection", "disconnect", "disconnecting", "connect_error", "error", "reconnect",
             "reconnect_attempt", "reconnect_error", "reconnect_failed", "ping", "newListener", "removeListener"}
IMP = {"server": r"""(?:from\s+|require\(\s*|import\s*\(\s*)['"]socket\.io['"]""",
       "client": r"""(?:from\s+|require\(\s*|import\s*\(\s*)['"]socket\.io-client['"]"""}
MODIFIERS = r"(?:\s*\??\.\s*(?:to|in|except|timeout|compress)\s*\([^()]*(?:\([^()]*\)[^()]*)*\)|\s*\??\.\s*(?:broadcast|volatile|local))*"


def _chain_head(src, i):
    """The object a chained call `x.on(..).on(..)` starts from, given the index of a closing `)` (or None)."""
    for _ in range(40):
        depth, j = 0, i
        while j >= 0:
            c = src[j]
            if c == ")":
                depth += 1
            elif c == "(":
                depth -= 1
                if depth == 0:
                    break
            j -= 1
        if j < 0:
            return None
        m = re.search(r"([\w$]+(?:\s*\??\.\s*[\w$]+)*)\s*$", src[max(0, j - 300):j])
        if not m:
            return None
        k = max(0, j - 300) + m.start()
        pre = re.search(r"\)\s*\??\.\s*$", src[max(0, k - 50):k])
        if pre:                                 # `).on(` again: keep walking back
            i = max(0, k - 50) + pre.start()
            continue
        head = re.sub(r"\s+|\?(?=\.)", "", m.group(1)).split(".")
        return head[0] if head[0] not in ("on", "once", "emit") else None
    return None


def _ternary(e):
    """`c ? a : b` at the top level of an expression -> [a, b], else None."""
    e = (e or "").strip()
    depth, q, qi = 0, None, -1
    for i, ch in enumerate(e):
        if q:
            if ch == q and e[i - 1] != "\\":
                q = None
            continue
        if ch in "'\"`":
            q = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "?" and depth == 0 and qi < 0 and e[i + 1:i + 2] not in (".", "?") and e[i - 1:i] != "?":
            qi = i
        elif ch == ":" and depth == 0 and qi >= 0:
            return [e[qi + 1:i].strip(), e[i + 1:].strip()]
    return None


class RT(BrokerScan):
    def __init__(self, project, b, sock=None):
        super().__init__(project, b, sock)
        self.pkg_role = {}
        self.near = set()
        self.wrappers = []          # (fn, event param, namespace, role, file, pos, chain)

    # ------------------------------------------------------------ roles
    def role_of_file(self, f, src):
        hits = [r for r, rx in IMP.items() if re.search(rx, src)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) == 2:
            return None
        if re.search(r"""(?:from\s+|require\(\s*|import\s*\(\s*)['"](?:node:)?(?:net|tls|dgram|http2|ws)['"]""", src):
            return None                         # `socket.on('data')` on a net / ws socket, not Socket.IO
        return self.package_role(f)

    def package_role(self, f):
        d = (self.root / f).parent
        while True:
            if d in self.pkg_role:
                return self.pkg_role[d]
            pj = d / "package.json"
            if pj.is_file():
                try:
                    j = json.loads(pj.read_text(errors="replace"))
                    deps = {**(j.get("dependencies") or {}), **(j.get("devDependencies") or {}), **(j.get("peerDependencies") or {})}
                except (OSError, ValueError, AttributeError):
                    deps = {}
                has = [r for r, n in (("server", "socket.io"), ("client", "socket.io-client")) if n in deps]
                if "server" in has and "client" in has:
                    # both: the client lib only in devDependencies (tests) leaves a server package
                    dev = (j.get("devDependencies") or {}) if isinstance(j, dict) else {}
                    has = ["server"] if "socket.io-client" in dev and "socket.io" not in dev else []
                r = has[0] if len(has) == 1 else None
                if r is not None or d == self.root:
                    self.pkg_role[d] = r
                    return r
            if d == self.root or d.parent == d:
                self.pkg_role[d] = None
                return None
            d = d.parent

    # ------------------------------------------------------------ run
    def run(self) -> dict:
        files = [f for f in sorted(self.s.files) if f.endswith(JS_EXT + (".jsx", ".vue", ".svelte")) and not f.endswith(".d.ts")]
        # files that import socket.io / socket.io-client, and the files importing one of them (`this.portal.socket`)
        sio = {f for f in files if any(re.search(rx, self.s.text(f)) for rx in IMP.values())}
        mods = {self.module_of(f): f for f in sio}
        self.near = set(sio)
        for e in self.b.edges.values():
            if e.kind == "IMPORTS" and e.dst in mods:
                n = self.b.nodes.get(e.src)
                if n is not None and n.file:
                    self.near.add(n.file)
        stems = {re.sub(r"(?:\.svelte)?\.[cm]?[jt]sx?$", "", f.rsplit("/", 1)[-1]) for f in sio} - {"index"}
        for f in files:
            src = self.s.text(f)
            if not re.search(r"\.\s*(?:emit|emitWithAck|on|once)\s*\(", src):
                continue
            if f not in self.near and stems:
                # an alias import the TS program could not resolve (`$lib/stores/websocket` without .svelte-kit)
                for m in re.finditer(r"""\bfrom\s+['"]([$@~#][^'"]*)['"]""", src):
                    if re.sub(r"\.[cm]?[jt]sx?$", "", m.group(1).rsplit("/", 1)[-1]) in stems:
                        self.near.add(f)
                        break
            role = self.role_of_file(f, src)
            if role is None:
                continue
            self.file(f, src, role)
        self.call_sites()
        out = {k: v for k, v in self.st.items() if v}
        if out and self.samples:
            out["samples"] = dict(self.samples)
        return out

    def objects(self, f, src, role):
        """name -> namespace of the Socket.IO objects of a file ('' = unknown, default '/')."""
        objs = {}
        if role == "server":
            for m in re.finditer(r"\b(?:const|let|var)\s+(\w+)\s*(?::\s*[\w<>.]+)?\s*=\s*(?:new\s+(?:Server|SocketIO|SocketIOServer|"
                                 r"IOServer)\b|require\(\s*['\"]socket\.io['\"]\s*\)\s*\(|socketIo\s*\(|socketio\s*\()", src):
                objs[m.group(1)] = "/"
            for m in re.finditer(r"\b(?:this\.)?(\w+)\s*=\s*new\s+(?:Server|SocketIO|SocketIOServer)\s*\(", src):
                objs.setdefault(m.group(1), "/")
        else:
            for m in re.finditer(r"\b(?:const|let|var)?\s*(?:this\.)?(\w+)\s*(?::\s*[\w<>.|\s]+?)?\s*=\s*(?:io|ioClient|socketIOClient|"
                                 r"connect|io\.connect|Manager\s*\([^()]*\)\s*\.socket|\w+\.socket)\s*\(", src):
                if m.group(1) in ("const", "let", "var"):
                    continue
                a = _args(src, src.index("(", m.end() - 1))
                objs[m.group(1)] = self.client_ns(f, m.start(), a[0] if a else "", "Manager" in m.group(0) or ".socket(" in m.group(0))
        if role == "server":
            # Nest gateways: `@WebSocketServer() server: Server` and handler parameters typed `Socket`
            gw = re.search(r"@WebSocketGateway\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)", src)
            gns = "/"
            if gw:
                nm = re.search(r"\bnamespace\s*:\s*([^,}]+)", gw.group(1))
                if nm:
                    v, _c = self.value(f, gw.start(), nm.group(1).strip())
                    gns = ("/" + v.lstrip("/")) if v and not v.startswith("/") else (v or "")
            for m in re.finditer(r"@WebSocketServer\s*\(\s*\)\s*(?:(?:public|private|protected|readonly)\s+)*(\w+)", src):
                objs[m.group(1)] = gns
            for m in re.finditer(r"[(,]\s*(?:@ConnectedSocket\s*\(\s*\)\s*)?(\w+)\s*:\s*(?:Socket|Namespace|Server)\b", src):
                objs.setdefault(m.group(1), gns)
        else:
            for m in re.finditer(r"\b(?:(?:private|public|protected|readonly)\s+)*(?:this\.)?(\w+)\s*[?!]?\s*:\s*(?:Socket|SocketIOClient\.Socket)\b", src):
                objs.setdefault(m.group(1), "/")
        # namespaces: const chat = io.of('/chat')
        for m in re.finditer(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(\w+)\s*\.\s*of\s*\(", src):
            a = _args(src, m.end() - 1)
            v, _c = self.value(f, m.start(), a[0]) if a else (None, None)
            objs[m.group(1)] = v if v and v.startswith("/") else ""
        return objs

    def client_ns(self, f, pos, arg, is_ns):
        a = (arg or "").strip()
        if not a or a.startswith("{"):
            return "/"
        v, _c = self.value(f, pos, a)
        if v is None:
            # `io(baseUrl)`: a server origin from config, which carries no namespace path
            return "/" if not is_ns and re.fullmatch(r"[\w$.]*(?:url|uri|host|origin|server)", a, re.I) else ""
        if is_ns:
            return v if v.startswith("/") else "/" + v
        m = re.match(r"^(?:[a-z]+:)?//[^/]*(/[^?#]*)?|^(/[^?#]*)", v)
        if not m:
            return "/"
        p = (m.group(1) or m.group(2) or "/").rstrip("/") or "/"
        return "" if "{" in p else p

    def conn_sockets(self, f, src, objs):
        """socket parameters of `X.on('connection', (socket) => ..)` with X's namespace, and the span of the callback."""
        out = []
        for m in re.finditer(r"\b(\w+)\s*\.\s*on\s*\(\s*['\"]connect(?:ion)?['\"]\s*,\s*(?:async\s*)?(?:\(\s*(\w+)[^()]*\)|(\w+))\s*(?::\s*[\w<>]+\s*)?=>|"
                             r"\b(\w+)\s*\.\s*on\s*\(\s*['\"]connect(?:ion)?['\"]\s*,\s*(?:async\s+)?function\s*\w*\s*\(\s*(\w+)", src):
            obj = m.group(1) or m.group(4)
            p = m.group(2) or m.group(3) or m.group(5)
            if obj not in objs and not (f in self.near and re.fullmatch(r"io|sio|nsp|namespace|\w*(?:io|socket)Server", obj, re.I)):
                continue                         # `httpServer.on('connection', (socket: Duplex) => ..)`
            ns = objs.get(obj, "/")
            end = m.end()
            a_end = self._close(src, src.index("(", m.start() + len(obj)))
            out.append((p, ns, m.start(), a_end if a_end > end else len(src)))
        return out

    @staticmethod
    def _close(src, i):
        depth = 0
        for j in range(i, min(len(src), i + 200000)):
            c = src[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return j
        return len(src)

    def file(self, f, src, role):
        objs = self.objects(f, src, role)
        conns = self.conn_sockets(f, src, objs) if role == "server" else []
        guards = defaultdict(list)
        if role == "server":
            for m in re.finditer(r"\b(\w+)\s*\.\s*use\s*\(", src):
                if m.group(1) not in objs or self.s.masked(f, m.start()):
                    continue
                a = _args(src, m.end() - 1)
                g = a[0].strip() if a else ""
                name = g if re.fullmatch(r"[\w.$]+", g) else f"{m.group(1)}.use"
                guards[objs[m.group(1)] or "/"].append(name)

        imported = f in self.near

        def ns_of(obj, pos):
            last = obj.split(".")[-1]
            if obj in objs:
                return objs[obj], EXACT
            if last in objs:
                return objs[last], RESOLVED
            for p, ns, lo, hi in conns:
                if last == p and lo <= pos <= hi:
                    return ns, EXACT
            if imported and re.search(r"socket|^io$|^sio$|^nsp$|^client$", last, re.I) and (role == "client" or last != "client"):
                # by name only near a socket.io / socket.io-client import (a `TcpSocket` field is not one)
                return "/", HEURISTIC
            return None, None

        rx = re.compile(r"((?:this\.)?[\w$]+(?:\s*\??\.\s*[\w$]+)*?)" + MODIFIERS + r"\s*\??\.\s*(emit|emitWithAck|on|once|send)\s*\(")
        calls = []                               # (start, object text, method, chain text, '(' index, method pos)
        for m in rx.finditer(src):
            calls.append((m.start(), m.group(1), m.group(2), m.group(0)[len(m.group(1)):], m.end() - 1, m.start(2)))
        # chained listeners: `socket\n .on('a', ..)\n .on('b', ..)`: the head of the chain is the object
        seen = {c[4] for c in calls}
        for m in re.finditer(r"\)\s*\??\.\s*(on|once|emit)\s*\(", src):
            if m.end() - 1 in seen:
                continue                         # `io.to(room).emit(..)`: already taken with its modifiers
            head = _chain_head(src, m.start())
            if head:
                calls.append((m.start(), head, m.group(1), "", m.end() - 1, m.start(1)))
        for start, otext, meth, chain, paren, pos in calls:
            if self.s.masked(f, start):
                continue
            obj = re.sub(r"\s+|\?(?=\.)", "", otext)
            ns, oconf = ns_of(obj, start)
            if ns is None:
                continue
            if meth == "send" and not re.search(r"socket", obj, re.I):
                continue
            a = _args(src, paren)
            if not a and meth != "send":
                continue
            if meth == "send":
                evs = [("message", EXACT)]
            else:
                br = _ternary(a[0])               # `volatile ? EV.A : EV.B`: both events (heuristic)
                evs = [(v, HEURISTIC if br else c) for v, c in (self.value(f, pos, x) for x in (br or [a[0]]))]
            for ev, econf in evs:
                self.one(f, meth, a, pos, ev, econf, ns, oconf, chain, role, guards)

    def one(self, f, meth, a, pos, ev, econf, ns, oconf, chain, role, guards):
        if (ev is None or _whole_ph(ev)) and meth not in ("on", "once", "send") and a and re.fullmatch(r"[A-Za-z_$][\w$]*", a[0].strip()):
            # `emit(event, ...)` with the enclosing function's parameter: resolved at its call sites (wrappers)
            fn, _lo, _hi = self.s.fn_bounds(f, pos)
            if fn and a[0].strip() in [p for p, _d in self.s.params(f, fn)]:
                self.wrappers.append((fn, a[0].strip(), ns, role, f, pos, chain))
                return
        if ev is None or _whole_ph(ev):
            self.miss(f"socketio_{'receive' if meth in ('on', 'once') else 'send'}_unresolved", f"{f}:{self.s.line_of(f, pos)}")
            return
        if ev in LIFECYCLE:
            return
        nsv = ns or "/"
        if ns == "":
            self.miss("socketio_namespace_unknown", f"{f}:{self.s.line_of(f, pos)}")
            return
        conf = HEURISTIC if HEURISTIC in (oconf, econf) or "{" in ev else (RESOLVED if RESOLVED in (oconf, econf) else EXACT)
        name = f"{nsv}#{ev}"
        if meth in ("on", "once"):
            h = self.handler(f, pos, a[1] if len(a) > 1 else "")
            self.listen(name, nsv, ev, conf, f, pos, h, role, guards.get(nsv, []) if role == "server" else None)
        else:
            room = re.search(r"\.\s*(?:to|in)\s*\(\s*([^()]*(?:\([^()]*\)[^()]*)*)\)", chain)
            rv = None
            if room:
                re_ = room.group(1).strip()
                lit = re.fullmatch(r"""['"`][^'"`]*['"`]""", re_) or re.fullmatch(r"[A-Z][A-Z0-9_]{2,}|[A-Z]\w*\.\w+", re_)
                rv = (self.value(f, pos, re_)[0] if lit else None) or re_[:60]
            ack = meth == "emitWithAck" or (len(a) > 1 and re.match(r"\s*(?:async\s*)?(?:\([^()]*\)|\w+)\s*=>|\s*(?:async\s+)?function\b", a[-1]))
            self.emit(name, nsv, ev, conf, f, pos, role, "request" if ack else "emit", room=rv,
                      broadcast=True if re.search(r"\.\s*broadcast\b", chain) else None)

    def call_sites(self):
        """Events of wrapper functions (`clientSend(event, room, ...data) { this.server.to(room).emit(event, ...data) }`)
        from the literal arguments of their callers."""
        if not self.wrappers:
            return
        by_fn = defaultdict(list)
        for w in self.wrappers:
            by_fn[w[0]].append(w)
        into = defaultdict(list)
        for e in self.b.edges.values():
            if e.kind in ("CALLS", "TEST_CALLS") and e.dst in by_fn and e.file and e.line:
                into[e.dst].append(e)
        for fn, ws in by_fn.items():
            n = self.b.nodes.get(fn)
            if n is None:
                continue
            params = [p for p, _d in self.s.params(n.file, fn)]
            short = re.split(r"[.#:]", n.name or fn)[-1]
            self.st["socketio_wrappers"] += 1
            sites = []                       # (caller, file, line, call match, confidence)
            for e in into.get(fn, ()):
                src = self.s.text(e.file)
                lo = self.s.off(e.file, e.line)
                cm = re.compile(rf"\b{re.escape(short)}\s*(?:<[^()]*?>)?\s*\(").search(src, lo, lo + 600)
                if cm:
                    sites.append((e.src, e.file, e.line, cm, None, e.kind == "TEST_CALLS"))
            if not sites and n.kind == "method":
                # no resolved calls (an injected repository the checker could not type): `this.<camelCase class>.m(..)`
                cls = re.split(r"[.#:]", n.name or fn)[-2] if len(re.split(r"[.#:]", n.name or fn)) > 1 else ""
                crx = re.compile(rf"(?:this\.)?(\w+)\s*\??\.\s*{re.escape(short)}\s*(?:<[^()]*?>)?\s*\(")
                for f2 in sorted(self.s.files):
                    if not f2.endswith(JS_EXT) or f2 == n.file:
                        continue
                    t2 = self.s.text(f2)
                    if short not in t2:
                        continue
                    for cm in crx.finditer(t2):
                        if cls and cm.group(1).lower() == cls.lower() and not self.s.masked(f2, cm.start()):
                            caller = self.fn_at(f2, cm.start())
                            if caller:
                                sites.append((caller, f2, self.s.line_of(f2, cm.start()), cm, HEURISTIC, False))
            for caller, efile, eline, cm, sconf, is_t in sites:
                src = self.s.text(efile)
                args = _args(src, cm.end() - 1)
                for fn_, param, ns, role, wf, wpos, chain in ws:
                    if param not in params or params.index(param) >= len(args):
                        continue
                    ev, c = self.value(efile, cm.start(), args[params.index(param)])
                    if ev is None or _whole_ph(ev) or ev in LIFECYCLE or not ns:
                        self.miss("socketio_send_unresolved", f"{efile}:{eline} via {short}")
                        continue
                    conf = HEURISTIC if HEURISTIC in (c, sconf) or "{" in ev else RESOLVED
                    room = re.search(r"\.\s*(?:to|in)\s*\(\s*([^()]*)\)", chain)
                    rtext = room.group(1).strip() if room else None
                    if rtext in params and params.index(rtext) < len(args):
                        rtext = args[params.index(rtext)].strip()      # the caller's room argument
                    fsrc = caller
                    line = eline
                    key = ("s", f"{ns}#{ev}", fsrc, line)
                    if key in self.done:
                        continue
                    self.done.add(key)
                    from .protocols import protocol_send
                    protocol_send(self.b, "socketio", f"{ns}#{ev}", fsrc, efile, line, conf, test=is_t or self.is_test(efile, fsrc),
                                  role="emit", library=LIB[role], process=role, via=fn, room=rtext[:60] if rtext else None,
                                  node_attrs={"namespace": ns, "event": ev})
                    self.st["socketio_sends_via_wrapper"] += 1

    # ------------------------------------------------------------ edges
    def emit(self, name, ns, ev, conf, f, pos, role, how, room=None, broadcast=None):
        from .protocols import protocol_send
        fn = self.fn_at(f, pos)
        if fn is None:
            return
        line = self.s.line_of(f, pos)
        key = ("s", name, fn, line)
        if key in self.done:
            return
        self.done.add(key)
        protocol_send(self.b, "socketio", name, fn, f, line, conf, test=self.is_test(f, fn), role=how, library=LIB[role],
                      process=role, room=room, broadcast=broadcast, node_attrs={"namespace": ns, "event": ev})
        self.st["socketio_sends"] += 1

    def listen(self, name, ns, ev, conf, f, pos, h, role, guards):
        from .protocols import protocol_receive
        if h is None:
            return
        if self.is_test(f, h):
            self.st["test_receivers_skipped"] += 1
            return
        line = self.s.line_of(f, pos)
        key = ("r", name, h, line)
        if key in self.done:
            return
        self.done.add(key)
        protocol_receive(self.b, "socketio", name, h, f, line, conf, guards=guards, library=LIB[role], process=role,
                         node_attrs={"namespace": ns, "event": ev})
        self.st["socketio_receivers"] += 1


def nest_twins(project, b, st) -> None:
    """Nest `@SubscribeMessage('e')` handlers (message:ws nodes) of a Socket.IO gateway also receive
    endpoint:socketio:<namespace>#e, so a socket.io-client emit links to them (the message node stays)."""
    from .plugins.tsweb.common import pkg_deps
    from .protocols import protocol_receive
    deps = pkg_deps(project.root)
    if "@nestjs/platform-ws" in deps and "@nestjs/platform-socket.io" not in deps:
        return                                  # the raw WebSocket adapter
    handled = defaultdict(list)
    for e in b.edges.values():
        if e.kind == "HANDLED_BY" and e.src.startswith("message:ws:"):
            handled[e.src].append(e)
    for nid, n in list(b.nodes.items()):
        if n.kind != "message" or (n.attrs or {}).get("transport") != "ws" or (n.attrs or {}).get("framework") != "nest":
            continue
        pat = n.attrs.get("pattern")
        if not pat or pat in LIFECYCLE:
            continue
        ns = n.attrs.get("namespace") or "/"
        ns = ns if ns.startswith("/") else "/" + ns
        for e in handled.get(nid, ()):
            protocol_receive(b, "socketio", f"{ns}#{pat}", e.dst, e.file, e.line, EXACT, guards=list(n.attrs.get("guards") or []),
                             library="@nestjs/websockets", process="server", via=nid, node_attrs={"namespace": ns, "event": pat})
            st["socketio_nest_handlers"] += 1


def apply(project, builder, sock=None) -> dict:
    """Socket.IO endpoints of JS / TS code (#32); empty without any."""
    rt = RT(project, builder, sock)
    nest_twins(project, builder, rt.st)
    return rt.run()

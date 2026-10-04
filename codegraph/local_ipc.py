"""Local IPC between execution contexts of a JS / TS app (#38 part 1).

  worker            endpoint:worker:<script>      `new Worker(new URL('./w.ts', import.meta.url))`, `new Worker('w.js')`,
                                                  `new SharedWorker(..)`, Vite `import W from './w?worker'; new W()`:
                                                  `w.postMessage(..)` (and comlink `wrap(w)`) send to the script; its
                                                  `self.onmessage = h` / `addEventListener('message', h)` / comlink
                                                  `expose(obj)` receive. The way back is `<script>:out`: the worker's
                                                  `self.postMessage(..)` -> `w.onmessage = h` / `w.addEventListener(..)`.
                    endpoint:worker:service-worker  `navigator.serviceWorker.controller.postMessage(..)` /
                                                  `registration.active.postMessage(..)` -> the service worker's
                                                  `self.addEventListener('message', h)`; `client.postMessage(..)` in the
                                                  service worker -> `navigator.serviceWorker.addEventListener('message')`
                                                  (`service-worker:out`).
  broadcastchannel  endpoint:broadcastchannel:<name>  `new BroadcastChannel('x')`: `.postMessage` -> `.onmessage` /
                                                  `.addEventListener('message', h)` on any channel of that name.
  postmessage       endpoint:postmessage:<type>   `window.parent / opener / top / iframe.contentWindow / event.source
                                                  .postMessage(msg, origin)` -> `window.addEventListener('message', h)`.
                                                  `<type>` is the message's `type` / `action` / `event` / `kind` literal,
                                                  `*` when unknown; a listener receives each type it compares
                                                  (`event.data.type === 'x'`, `case 'x':`), else `*` (all types).
                                                  Receivers record an `event.origin` / `event.source` check as a guard
                                                  (`[]`: none); senders record `target_origin: "*"`.
  extension         endpoint:extension:<type>     `chrome.runtime.sendMessage(msg)` / `chrome.tabs.sendMessage(tab,
                                                  msg)` (and `browser.*`) -> `runtime.onMessage(.External).addListener`;
                                                  `runtime.connect({ name })` -> `runtime.onConnect.addListener` as
                                                  `port:<name>`.
  native-messaging  endpoint:native-messaging:<host>  `runtime.connectNative('com.app.host')` /
                                                  `sendNativeMessage(..)` -> the in-repo program a native messaging
                                                  host manifest (`"type": "stdio"`, `"name"`, `"path"`) names.

Message types are heuristic (a sender whose type is unknown reaches only `*` listeners). Electron / Tauri IPC and
web-to-native bridges are codegraph/bridges.py; Unix sockets, named pipes, D-Bus, Android intents and child processes
are later parts of #38.
"""
from __future__ import annotations

import json
import posixpath
import re
from collections import defaultdict

from .brokers import Scan, _args, _key, _strlit
from .core.model import EXACT, HEURISTIC, RESOLVED
from .protocols import protocol_receive, protocol_send

JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")
JS_TYPEOF = {"string", "number", "object", "undefined", "function", "boolean", "bigint", "symbol"}
TYPE_KEYS = ("type", "action", "event", "kind", "messageType", "cmd", "command")
WORKER_NEW = re.compile(r"\bnew\s+(Shared)?Worker\s*\(")
VITE_WORKER = re.compile(r"""\bimport\s+(\w+)\s+from\s+['"]([^'"]+?)\?(?:shared)?worker(?:&[\w=&]*)?['"]""")
BC_NEW = re.compile(r"\bnew\s+BroadcastChannel\s*\(")
POST = re.compile(r"""([\w$][\w$.\[\]'"?!]*?)\s*\??\.\s*postMessage\s*\(""")
WIN_TARGET = re.compile(r"(?:^|\.)(?:parent|opener|top|contentWindow|source)$|^(?:window|globalThis)$|^frames\[")
MSG_LISTEN = re.compile(r"""\baddEventListener\s*\(\s*['"]message['"]\s*,\s*""")
SW_FILE = re.compile(r"""\bself\.addEventListener\s*\(\s*['"](?:install|activate|fetch)['"]""")
SW_SEND = re.compile(r"""\b(?:navigator\.serviceWorker|[\w$#.]*[sS]erviceWorker)\s*\??\.\s*controller\s*\??\.\s*postMessage\s*\(|"""
                     r"""\b(?:registration|reg|swRegistration|r)\s*\??\.\s*(?:active|waiting|installing)\s*\??\.\s*postMessage\s*\(""")
SW_VAR = re.compile(r"""(?:const|let|var)\s+(\w+)\s*=\s*(?:await\s+)?[^;\n]*?\bnavigator\.serviceWorker\s*\??\.\s*(?:controller|ready)\b[^;\n]*""")
SW_LISTEN = re.compile(r"""\bnavigator\.serviceWorker\s*\??\.\s*(?:addEventListener\s*\(\s*['"]message['"]\s*,\s*|onmessage\s*=\s*(?!=))""")
EXT_SEND = re.compile(r"""\b(?:chrome|browser)\.(runtime|tabs)\.sendMessage(?:<[^()]*?>)?\s*\(""")
EXT_RECV = re.compile(r"""\b(?:chrome|browser)\.runtime\.onMessage(External)?\.addListener\s*\(""")
EXT_CONNECT = re.compile(r"""\b(?:chrome|browser)\.(?:runtime|tabs)\.connect(?:<[^()]*?>)?\s*\(""")
EXT_ONCONNECT = re.compile(r"""\b(?:chrome|browser)\.runtime\.onConnect(?:External)?\.addListener\s*\(""")
NATIVE = re.compile(r"""\b(?:chrome|browser)\.runtime\.(connectNative|sendNativeMessage)(?:<[^()]*?>)?\s*\(""")
TYPE_CMP = re.compile(r"""(?:\.|\b)(?:type|action|event|kind|messageType|cmd|command)\s*(?:===?|!==?)\s*(['"])([\w:./ -]{1,80})\1|"""
                      r"""(['"])([\w:./ -]{1,80})\3\s*===?\s*[\w$.?]*\b(?:type|action|event|kind|messageType|cmd|command)\b""")
CONST = r"(?:[A-Za-z_$][\w$]*\.[A-Z_][\w$]*|[A-Z][A-Z0-9_]{2,})"     # Enum.MEMBER, obj.KEY, UPPER_CASE
MEMBER_CMP = re.compile(r"""\b(?:type|action|event|kind|messageType|cmd|command)\s*===?\s*(""" + CONST + r""")\b|"""
                        r"""(?<![\w$.])(""" + CONST + r""")\s*===?\s*[\w$.?]*\b(?:type|action|event|kind|messageType|cmd|command)\b""")
CASE_MEMBER = re.compile(r"""\bcase\s+(""" + CONST + r""")\s*:""")
SWITCH = re.compile(r"""\bswitch\s*\(\s*[\w$.?]*\b(?:type|action|event|kind|messageType|cmd|command)\s*\)\s*\{""")
CASE = re.compile(r"""\bcase\s+(['"])([\w:./ -]{1,80})\1\s*:""")
ORIGIN = re.compile(r"""\.origin\b|\{\s*[^}]*\borigin\b[^}]*\}\s*=\s*\w+|\.source\s*[!=]==?""")


def _call_end(src, paren):
    """Offset after the `)` matching the `(` at `paren`."""
    depth, j, n = 0, paren, len(src)
    while j < n:
        c = src[j]
        if c in "'\"`":
            k = j + 1
            while k < n and src[k] != c:
                k += 2 if src[k] == "\\" else 1
            j = k + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


class LocalIpc(Scan):
    def __init__(self, project, b, sock=None):
        super().__init__(project, b, sock)
        self._callees = None
        self.files = [f for f in sorted(self.s.files) if f.endswith(JS_EXT) and not f.endswith(".d.ts")
                      and not re.search(r"(?:^|/)(?:node_modules|dist|build|vendor)/|\.min\.js$|\.bundle\.js$", f)]

    def run(self) -> dict:
        texts = {}
        for f in self.files:
            t = self.s.text(f)
            if t and any(k in t for k in ("postMessage", "onMessage", "sendMessage", "connect", "onmessage", "Worker",
                                          "BroadcastChannel", "serviceWorker", "'message'", '"message"')):
                if max((len(x) for x in t[:200000].split("\n")), default=0) > 3000:
                    continue                        # minified / bundled
                texts[f] = t
        self.texts = texts
        self.workers(texts)
        self.service_workers(texts)
        self.channels(texts)
        self.windows(texts)
        self.extension(texts)
        self.native(texts)
        out = {k: v for k, v in self.st.items() if v}
        if out and self.samples:
            out["samples"] = dict(self.samples)
        return out

    # ---------------------------------------------------------------- helpers
    def lhs(self, src, pos):
        """`x` / `this.x` / `self.x` that `... = <expr at pos>` assigns."""
        m = re.search(r"(?:\b(?:const|let|var)\s+([\w$]+)|((?:this|self)\.[\w$#]+|[\w$]+))\s*(?::\s*[\w<>\[\] |.]+)?\s*=\s*(?:await\s+)?$",
                      src[max(0, pos - 160):pos])
        if not m:
            return None
        return m.group(1) or m.group(2)

    def var_rx(self, var):
        v = re.escape(var).replace(r"this\.", r"(?:this|self)\.")
        return re.compile(rf"(?<![\w$.])(?:{v})\s*[?!]?\s*\.\s*")

    def body_of(self, file, src, pos, expr):
        """(handler node, text of its body) for a callback expression at `pos`."""
        e = (expr or "").strip()
        if re.fullmatch(r"(?:this\.|self\.)?[\w$]+(?:\.[\w$]+)?(?:\.bind\(\s*this\s*\))?", e):
            e = re.sub(r"\.bind\(\s*this\s*\)$", "", e)
            h = self.node_named(file, pos, e) or self.handler(file, pos, e)
            n = self.b.nodes.get(h)
            if n is not None and n.file and n.line and n.kind in ("function", "method"):
                t = self.s.text(n.file)
                lo = self.s.off(n.file, n.line)
                end = n.end_line or n.line
                hi = self.s.off(n.file, end + 1) if end < len(self.s.lines[n.file]) else len(t)
                return h, t[lo:hi]
            return h, ""
        return self.fn_at(file, pos), e

    def node_named(self, file, pos, e):
        """`Cls.method` / `this.method` / `fn` defined in `file`."""
        parts = e.split(".")
        if parts[0] in ("this", "self"):
            fn = self.fn_at(file, pos) or ""
            m = re.search(r"#((?:[\w$]+\.)*?)([\w$]+)\.[\w$]+$", fn)
            if not m:
                return None
            parts = [m.group(2), parts[1]]
        for kind in ("method", "function"):
            nid = f"{kind}:{file}#{'.'.join(parts)}"
            if nid in self.b.nodes:
                return nid
        return None

    def getters(self, src, ret_rx):
        """Names of the functions in `src` whose body ends with a return matching `ret_rx`."""
        out = []
        for g in re.finditer(r"\bfunction\s+([\w$]+)\s*\([^)]*\)\s*(?::[^{]*)?\{|"
                             r"\b(?:const|let)\s+([\w$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::[^=]*)?=>\s*\{", src):
            end = _call_end(src, g.end() - 1)
            if re.search(ret_rx, src[g.end():end - 1].rstrip()):
                out.append(g.group(1) or g.group(2))
        return out

    def resolve_script(self, file, text):
        t = text.split("?")[0].replace("\\", "/")
        if not t:
            return None
        base = posixpath.dirname(file)
        cands = []
        if t.startswith(("./", "../")):
            cands.append(posixpath.normpath(posixpath.join(base, t)))
        else:
            cands.append(posixpath.normpath(posixpath.join(base, t)))
            cands.append(posixpath.normpath(t.lstrip("/")))
        for c in cands:
            if c.startswith(".."):
                continue
            stem = re.sub(r"\.(?:m?js|cjs)$", "", c)
            for x in (c, stem + ".ts", stem + ".js", stem + ".mts", stem + ".mjs", stem + ".tsx", c + ".ts", c + ".js"):
                if x in self.s.files or (self.root / x).is_file():
                    return x
        tail = re.sub(r"^[@~]/", "", re.sub(r"^\$lib/", "lib/", t)).lstrip("./")   # `$lib/x` (SvelteKit), `@/x`, `~/x`
        while tail.startswith("../"):
            tail = tail[3:]
        stem = re.sub(r"\.(?:m?js|cjs|ts)$", "", tail)
        hits = [f for f in self.files if re.sub(r"\.\w+$", "", f) == stem or re.sub(r"\.\w+$", "", f).endswith("/" + stem)]
        return hits[0] if len(hits) == 1 else None

    def msg_type(self, file, pos, payload):
        p = (payload or "").strip()
        m = re.fullmatch(r"JSON\.stringify\s*\(([\s\S]*)\)", p)
        if m:
            p = m.group(1).strip()
        for k in TYPE_KEYS:
            v = _key(p, k)
            if v is None or v == k:
                continue
            lit = _strlit(v)
            if lit is not None:
                return lit, EXACT
            val, c = self.value(file, pos, v)
            if val is not None and "{" not in val:
                return val, c or RESOLVED
            return None, None
        return None, None

    def recv_types(self, body, file=None, pos=0):
        """Message types a listener body compares: string literals, and enum / constant members when `file` is given."""
        types = set()
        for m in TYPE_CMP.finditer(body):
            v = m.group(2) or m.group(4)
            if v not in JS_TYPEOF and not re.search(r"\btypeof\s+[\w$.?]*$", body[max(0, m.start() - 40):m.start() + 1]):
                types.add(v)                    # not `typeof data.type !== 'string'`
        members = [m.group(1) or m.group(2) for m in MEMBER_CMP.finditer(body)]
        for m in SWITCH.finditer(body):
            end = _call_end(body, m.end() - 1)
            types.update(c.group(2) for c in CASE.finditer(body, m.end(), end))
            members += [c.group(1) for c in CASE_MEMBER.finditer(body, m.end(), end)]
        if file:
            for x in members:
                v, _c = self.value(file, pos, x)
                if v is not None and "{" not in v:
                    types.add(v)
        return sorted(types)

    def typed(self, file, pos, h, body):
        """[(type, handler, via)]: the types the listener compares and those of the functions it calls (one level; the
        callee that switches on the type receives them, `via` the listener), else [("*", h, None)]."""
        own = [(t, h, None) for t in self.recv_types(body, file, pos)]
        if self._callees is None:
            self._callees = defaultdict(list)
            for e in self.b.edges.values():
                if e.kind == "CALLS":
                    self._callees[e.src].append(e.dst)
        out = []
        # only callees handed the message: `onUiMessage(message)`, `handle(event.data)`, `f({ type, data })`
        pm = re.search(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*(?::[^={]*)?(?:=>|\{)", body[:400])
        names = [x for x in re.findall(r"[A-Za-z_$][\w$]*", pm.group(1) if pm else "") if x not in ("async", "any", "unknown")]
        for c in dict.fromkeys(self._callees.get(h, ())):
            n = self.b.nodes.get(c)
            if n is None or not n.file or not n.line or n.kind not in ("function", "method") or self.is_test(n.file, c):
                continue
            short = re.split(r"[#.]", c)[-1]
            if re.match(r"(?:is|has|can|should|check|validate)[A-Z_]", short):
                continue                        # a type guard (`isWidgetResizeAction(event.data)`), not the handler
            if not names or not re.search(rf"\b{re.escape(short)}\s*\(\s*[^)]*\b(?:{'|'.join(map(re.escape, names))})\b", body):
                continue
            t = self.s.text(n.file)
            if not t:
                continue
            lo = self.s.off(n.file, n.line)
            end = n.end_line or n.line
            hi = self.s.off(n.file, end + 1) if end < len(self.s.lines[n.file]) else len(t)
            out += [(x, c, h) for x in self.recv_types(t[lo:hi], n.file, lo)]
        seen = {t for t, _h, _v in own}
        out = own + [x for x in out if x[0] not in seen]
        return out or [("*", h, None)]

    def send(self, proto, name, src, file, pos, conf, how, **attrs):
        line = self.s.line_of(file, pos)
        key = ("s", proto, name, src, line)
        if key in self.done:
            return
        self.done.add(key)
        protocol_send(self.b, proto, name, src, file, line, conf, test=self.is_test(file, src), role="send",
                      library=attrs.pop("library", proto), how=how, **attrs)
        self.st[f"{proto}_sends"] += 1

    def recv(self, proto, name, h, file, pos, conf, how, guards=None, **attrs):
        line = self.s.line_of(file, pos)
        key = ("r", proto, name, h, line)
        if key in self.done or h is None or self.is_test(file, h):
            return
        self.done.add(key)
        protocol_receive(self.b, proto, name, h, file, line, conf, guards=guards, how=how, **attrs)
        self.st[f"{proto}_receivers"] += 1

    def listeners(self, src, start_rx, lo=0, hi=None):
        """(pos, callback expr) of `<rx> h` listener registrations: `addEventListener('message', h)` / `onmessage = h`."""
        hi = len(src) if hi is None else hi
        for m in start_rx.finditer(src, lo, hi):
            j = m.end()
            if src[m.end() - 1:m.end()] == "(" or re.search(r",\s*$", src[m.start():m.end()]):
                # inside a call: the callback runs to the next top-level `,` / `)`
                paren = src.rfind("(", m.start(), m.end())
                args = _args(src, paren)
                yield m.start(), args[1] if len(args) > 1 else (args[0] if args else "")
            else:
                e = re.match(r"\s*(async\s+)?(function\b[\s\S]*?|\([^)]*\)\s*=>|[\w$]+\s*=>)", src[j:j + 200])
                if e:
                    k = src.find("{", j)
                    end = _call_end(src, k) if 0 <= k < j + 200 else src.find("\n", j)
                    yield m.start(), src[j:end]
                else:
                    e = re.match(r"\s*((?:this\.|self\.)?[\w$]+)", src[j:j + 120])
                    yield m.start(), e.group(1) if e else ""

    # ---------------------------------------------------------------- workers
    def workers(self, texts):
        self.worker_files = {}
        for f in self.files:
            src = self.s.text(f)
            if "Worker" not in src:
                continue
            binds = {}
            vite = {m.group(1): m.group(2) for m in VITE_WORKER.finditer(src)}
            sites = []
            for m in WORKER_NEW.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                args = _args(src, m.end() - 1)
                a0 = args[0] if args else ""
                u = re.fullmatch(r"new\s+URL\s*\(\s*([\s\S]+?)\s*,\s*import\.meta\.url\s*\)", a0.strip())
                lit = _strlit(u.group(1)) if u else _strlit(a0)
                if lit is None and a0:
                    v, _c = self.value(f, m.start(), u.group(1) if u else a0)
                    lit = v if v and "{" not in v else None
                script = self.resolve_script(f, lit) if lit else None
                if not script:
                    self.miss("worker_script_unresolved", f"{f}:{self.s.line_of(f, m.start())} {a0[:60]}")
                    continue
                sites.append((m.start(), m.end(), script))
            for name, spec in vite.items():
                script = self.resolve_script(f, spec)
                if not script:
                    continue
                for m in re.finditer(rf"\bnew\s+{re.escape(name)}\s*\(", src):
                    sites.append((m.start(), m.end(), script))
            for start, end, script in sites:
                self.worker_files.setdefault(script, set()).add(f)
                var = self.lhs(src, start)
                if var:
                    binds[var] = script
                # `new Worker(..).postMessage(..)`, comlink `wrap(new Worker(..))`
                ce = _call_end(src, end - 1)
                if re.match(r"\s*\.\s*postMessage\s*\(", src[ce:ce + 30]):
                    self.send("worker", script, self.fn_at(f, start), f, start, EXACT, "new Worker().postMessage")
                if re.search(r"\bwrap\s*(?:<[^>]*>)?\s*\(\s*$", src[max(0, start - 60):start]):
                    self.send("worker", script, self.fn_at(f, start), f, start, EXACT, "comlink wrap", library="comlink")
            # getters that return the worker: `function getWorker() { ...; return worker }`
            for var, script in list(binds.items()):
                for g in self.getters(src, rf"\breturn\s+{re.escape(var)}\s*;?\s*$"):
                    binds[g + "()"] = script
            for var, script in binds.items():
                rx = re.compile(rf"(?<![\w$.]){re.escape(var).replace(chr(92) + 'this' + chr(92) + '.', '(?:this|self)' + chr(92) + '.')}\s*[?!]?\s*\.\s*(postMessage\s*\(|onmessage\s*=(?!=)|addEventListener\s*\(\s*['\"]message['\"]\s*,)")
                for m in rx.finditer(src):
                    if self.s.masked(f, m.start()):
                        continue
                    if m.group(1).startswith("postMessage"):
                        self.send("worker", script, self.fn_at(f, m.start()), f, m.start(), EXACT, "worker.postMessage")
                    else:
                        cb = next(self.listeners(src, re.compile(re.escape(m.group(0))), m.start(), m.end()), (m.start(), ""))[1]
                        h, _b = self.body_of(f, src, m.start(), cb)
                        self.recv("worker", script + ":out", h, f, m.start(), EXACT, "worker.onmessage")
                if re.search(rf"\bwrap\s*(?:<[^>]*>)?\s*\(\s*{re.escape(var)}\s*[,)]", src):
                    m = re.search(rf"\bwrap\s*(?:<[^>]*>)?\s*\(\s*{re.escape(var)}\s*[,)]", src)
                    self.send("worker", script, self.fn_at(f, m.start()), f, m.start(), EXACT, "comlink wrap", library="comlink")
        for script in sorted(self.worker_files):
            self.worker_side(script, script, "worker")

    def worker_side(self, file, name, how):
        src = self.s.text(file)
        if not src:
            return
        rx = re.compile(r"""(?<![\w$.#])(?:self\.|globalThis\.)?(?:addEventListener\s*\(\s*['"]message['"]\s*,\s*|onmessage\s*=\s*(?!=))""")
        for pos, cb in self.listeners(src, rx):
            if self.s.masked(file, pos):
                continue
            h, _b = self.body_of(file, src, pos, cb)
            self.recv("worker", name, h, file, pos, EXACT, f"{how} onmessage")
        for m in re.finditer(r"""(?<![\w$.])(?:self\.|globalThis\.)?postMessage\s*\(""", src):
            if not self.s.masked(file, m.start()):
                self.send("worker", name + ":out", self.fn_at(file, m.start()), file, m.start(), EXACT, f"{how} postMessage")
        if "comlink" in src:
            for m in re.finditer(r"\b(?:Comlink\.)?expose\s*\(\s*([\w$.]+)", src):
                if not self.s.masked(file, m.start()):
                    h = self.handler(file, m.start(), m.group(1))
                    self.recv("worker", name, h, file, m.start(), EXACT, "comlink expose", library="comlink")

    def service_workers(self, texts):
        sw = [f for f in self.files if SW_FILE.search(self.s.text(f) or "")]
        # the modules a service worker is built from: `self.addEventListener(..)` in its directory
        dirs = {posixpath.dirname(f) for f in sw}
        sw += [f for f in self.files if f not in sw and posixpath.dirname(f) in dirs
               and re.search(r"\bself\.addEventListener\s*\(", self.s.text(f) or "")]
        name = "service-worker"
        self.sw_files = set(sw)
        for f in sw:
            src = self.s.text(f)
            rx = re.compile(r"""(?<![\w$.#])(?:self\.)?(?:addEventListener\s*\(\s*['"]message['"]\s*,\s*|onmessage\s*=\s*(?!=))""")
            for pos, cb in self.listeners(src, rx):
                if not self.s.masked(f, pos):
                    h, _b = self.body_of(f, src, pos, cb)
                    self.recv("worker", name, h, f, pos, EXACT, "service worker message listener")
            for m in POST.finditer(src):
                obj = m.group(1).rstrip("?!")
                if obj in ("self", "globalThis") or self.s.masked(f, m.start()):
                    continue
                self.send("worker", name + ":out", self.fn_at(f, m.start()), f, m.start(), HEURISTIC, "client.postMessage")
        for f, src in texts.items():
            if f in self.sw_files:
                continue
            for m in SW_SEND.finditer(src):
                if not self.s.masked(f, m.start()):
                    self.send("worker", name, self.fn_at(f, m.start()), f, m.start(), EXACT, "serviceWorker postMessage")
            # `const sw = getSW()` with `function getSW() { return navigator.serviceWorker?.controller }`
            sw_getters = [g for g in self.getters(src, r"\breturn\s+[^;\n]*\bserviceWorker\s*\??\.\s*controller\b[^;\n]*;?$")]
            sw_getters += re.findall(r"(?:const|let)\s+([\w$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::[^=\n]*)?=>\s*(?!\{)"
                                     r"[^\n]*\bserviceWorker\s*\??\.\s*controller\b", src)    # `const getSW = () => ..`
            sw_vars = [v.group(1) for v in SW_VAR.finditer(src)]
            for g in sw_getters:
                sw_vars += re.findall(rf"(?:const|let|var)\s+([\w$]+)\s*=\s*{re.escape(g)}\s*\(\s*\)", src)
            for v in dict.fromkeys(sw_vars):
                for m in re.finditer(rf"(?<![\w$.]){re.escape(v)}\s*[?!]?\s*\.\s*(?:active\s*[?!]?\s*\.\s*)?postMessage\s*\(", src, ):
                    if not self.s.masked(f, m.start()):
                        self.send("worker", name, self.fn_at(f, m.start()), f, m.start(), RESOLVED, "serviceWorker postMessage")
            for pos, cb in self.listeners(src, SW_LISTEN):
                if not self.s.masked(f, pos):
                    h, _b = self.body_of(f, src, pos, cb)
                    self.recv("worker", name + ":out", h, f, pos, EXACT, "serviceWorker message listener")

    # ---------------------------------------------------------------- BroadcastChannel
    def channels(self, texts):
        for f, src in texts.items():
            if "BroadcastChannel" not in src:
                continue
            for m in BC_NEW.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                args = _args(src, m.end() - 1)
                name, conf = self.value(f, m.start(), args[0]) if args else (None, None)
                if name is None:
                    self.miss("broadcastchannel_name_unknown", f"{f}:{self.s.line_of(f, m.start())}")
                    continue
                var = self.lhs(src, m.start())
                if not var:
                    continue
                rx = re.compile(rf"(?<![\w$.]){re.escape(var)}\s*[?!]?\s*\.\s*(postMessage\s*\(|onmessage\s*=(?!=)|addEventListener\s*\(\s*['\"]message['\"]\s*,)")
                for u in rx.finditer(src):
                    if self.s.masked(f, u.start()):
                        continue
                    if u.group(1).startswith("postMessage"):
                        self.send("broadcastchannel", name, self.fn_at(f, u.start()), f, u.start(), conf or EXACT, "BroadcastChannel.postMessage")
                    else:
                        cb = next(self.listeners(src, re.compile(re.escape(u.group(0))), u.start(), u.end()), (u.start(), ""))[1]
                        h, _b = self.body_of(f, src, u.start(), cb)
                        self.recv("broadcastchannel", name, h, f, u.start(), conf or EXACT, "BroadcastChannel message listener")

    # ---------------------------------------------------------------- window.postMessage
    def windows(self, texts):
        skip = set(self.worker_files) | getattr(self, "sw_files", set())
        for f, src in texts.items():
            if f in skip:
                continue
            for m in POST.finditer(src):
                obj = re.sub(r"[?!]", "", m.group(1))
                obj = re.sub(r"^(?:window|globalThis)\.(?=(?:parent|opener|top)\b)", "", obj)
                if not WIN_TARGET.search(obj) or self.s.masked(f, m.start()):
                    continue
                args = _args(src, m.end() - 1)
                typ, conf = self.msg_type(f, m.start(), args[0] if args else "")
                attrs = {"target": obj}
                if len(args) > 1 and _strlit(args[1]) == "*":
                    attrs["target_origin"] = "*"
                self.send("postmessage", typ or "*", self.fn_at(f, m.start()), f, m.start(), conf if typ else HEURISTIC,
                          f"{obj}.postMessage", **attrs)
            rx = re.compile(r"""(?<![\w$.#])(?:(?:window\.|globalThis\.)?addEventListener\s*\(\s*['"]message['"]\s*,\s*|(?:window|globalThis)\.onmessage\s*=\s*(?!=))""")
            for pos, cb in self.listeners(src, rx):
                if self.s.masked(f, pos):
                    continue
                h, body = self.body_of(f, src, pos, cb)
                guards = ["origin check"] if ORIGIN.search(body) else []
                for t, hh, via in self.typed(f, pos, h, body):
                    self.recv("postmessage", t, hh, f, pos, EXACT if t == "*" else HEURISTIC, "window message listener",
                              guards=guards, via=via)

    # ---------------------------------------------------------------- browser extensions
    def extension(self, texts):
        for f, src in texts.items():
            if "chrome." not in src and "browser." not in src:
                continue
            for m in EXT_SEND.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                args = _args(src, m.end() - 1)
                msg = next((a for a in args if a.strip().startswith(("{", "JSON.stringify"))), args[1 if m.group(1) == "tabs" else 0] if args else "")
                typ, conf = self.msg_type(f, m.start(), msg)
                self.send("extension", typ or "*", self.fn_at(f, m.start()), f, m.start(), conf if typ else HEURISTIC,
                          f"{m.group(1)}.sendMessage")
            for m in EXT_RECV.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                args = _args(src, m.end() - 1)
                h, body = self.body_of(f, src, m.start(), args[0] if args else "")
                # messages from other extensions / web pages (onMessageExternal): a sender check is the guard
                guards = (["sender check"] if re.search(r"\bsender\s*\.\s*(?:id|origin|url|tab)\b", body) else []) if m.group(1) else None
                for t, hh, via in self.typed(f, m.start(), h, body):
                    self.recv("extension", t, hh, f, m.start(), EXACT if t == "*" else HEURISTIC,
                              "runtime.onMessage" + ("External" if m.group(1) else ""), guards=guards, via=via)
            for m in EXT_CONNECT.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                args = _args(src, m.end() - 1)
                obj = next((a for a in args if a.strip().startswith("{")), "")
                v = _key(obj, "name")
                name, conf = (self.value(f, m.start(), v) if v else (None, None))
                self.send("extension", f"port:{name or '*'}", self.fn_at(f, m.start()), f, m.start(),
                          conf if name else HEURISTIC, "runtime.connect")
            for m in EXT_ONCONNECT.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                args = _args(src, m.end() - 1)
                h, body = self.body_of(f, src, m.start(), args[0] if args else "")
                names = sorted({x.group(2) or x.group(4) for x in re.finditer(
                    r"""\.name\s*===?\s*(['"])([\w:./-]+)\1|(['"])([\w:./-]+)\3\s*===?\s*[\w$.]*\.name\b""", body)})
                for n in names or ["*"]:
                    self.recv("extension", f"port:{n}", h, f, m.start(), HEURISTIC if names else EXACT, "runtime.onConnect")

    def native(self, texts):
        hosts = {}
        if not any(NATIVE.search(src) for src in texts.values()):
            return
        import os
        manifests = []
        for d, dirs, fs in os.walk(self.root):
            dirs[:] = [x for x in dirs if x not in ("node_modules", ".git", "dist", "build", "vendor", "target") and not x.startswith(".")]
            manifests += [os.path.relpath(os.path.join(d, x), self.root).replace(os.sep, "/") for x in fs if x.endswith(".json")]
        for f in sorted(manifests):
            try:
                t = (self.root / f).read_text(encoding="utf-8", errors="replace") if (self.root / f).stat().st_size < 20000 else ""
            except OSError:
                continue
            if '"stdio"' not in t:
                continue
            try:
                d = json.loads(t)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("type") == "stdio" and d.get("name") and d.get("path"):
                hosts[d["name"]] = (f, d["path"])
        for f, src in texts.items():
            for m in NATIVE.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                args = _args(src, m.end() - 1)
                name, conf = self.value(f, m.start(), args[0]) if args else (None, None)
                if name is None:
                    self.miss("native_messaging_host_unknown", f"{f}:{self.s.line_of(f, m.start())}")
                    continue
                self.send("native-messaging", name, self.fn_at(f, m.start()), f, m.start(), conf or EXACT,
                          f"runtime.{m.group(1)}")
        for name, (mf, path) in hosts.items():
            prog = self.resolve_script(mf, path) if not path.startswith("/") else None
            mod = self.module_of(prog) if prog else None
            if mod:
                self.s.text(mf)
                self.recv("native-messaging", name, mod, mf, 0, RESOLVED, "native messaging host manifest", program=prog)
            else:
                self.miss("native_messaging_host_outside", f"{mf} {path}")


def apply(project, builder, sock=None) -> dict:
    """Workers, service workers, BroadcastChannel, window.postMessage and browser-extension messaging (#38 part 1)."""
    return LocalIpc(project, builder, sock).run()

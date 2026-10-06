"""UDP application protocols with a name both ends share (#39): mDNS / DNS-SD service types, OSC address patterns,
CoAP resource paths and SSDP / UPnP search targets, as `endpoint:<protocol>:<name>` (the #31 model).

  mdns   advertise (RECEIVED_BY the advertising function) vs browse (SENDS_TO): python-zeroconf `ServiceInfo(type,
         name, port=..)` (with a port / addresses: advertised; without: a lookup) and `ServiceBrowser` /
         `AsyncServiceBrowser`; bonjour-service `publish({ type })` / `find({ type })`; Swift `NWListener.Service(type:)`
         / `NetService(.., type:)` vs `NWBrowser(for: .bonjour(type:))` / `searchForServices(ofType:)`; Android
         `NsdServiceInfo.serviceType` + `registerService` vs `discoverServices(type)`, JmDNS `ServiceInfo.create(type,
         ..)` vs `addServiceListener(type, ..)`; Dart bonsoir `BonsoirService(type:)` vs `BonsoirDiscovery(type:)`.
         Name: the service type without `.local.` (`_http._tcp`); bonjour-service `type: 'http'` -> `_http._tcp`.
  osc    python-osc `dispatcher.map('/addr', handler)` vs `client.send_message('/addr', ..)`; node-osc / osc.js
         `send('/addr', ..)` / `send({ address: '/addr' })`. Address patterns (`/filter*`) match as globs. The
         OSC servers / clients of python-osc are also UDP sockets (`ThreadingOSCUDPServer((ip, port), d)`,
         `SimpleUDPClient(ip, port)`).
  coap   aiocoap `root.add_resource(['time'], TimeResource())` (RECEIVED_BY its `render_*` method) vs
         `Message(code=GET, uri='coap://host/time')`; Californium `CoapResource("hello")` vs `CoapClient(uri)`;
         node-coap `coap.request('coap://host/x')`. Paths match with the HTTP path matcher.
  ssdp   node-ssdp `server.addUSN(urn)` vs `client.search(urn)`, ssdpy `SSDPServer(.., device_type=urn)` vs
         `m_search(urn)`, async_upnp_client `search_target=urn`.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .core.model import HEURISTIC, RESOLVED
from .process_runs import LIT, _args_text
from .sockets import _lit, _strip, split_args

SVC = re.compile(r"_[\w-]+\._(?:tcp|udp)", re.I)
HINT = re.compile(r"zeroconf|ServiceBrowser|bonjour|NWBrowser|NetService|NsdManager|NsdServiceInfo|JmDNS|jmdns|Bonsoir|"
                  r"pythonosc|node-osc|osc\.js|['\"]osc['\"]|aiocoap|californium|CoapResource|CoapClient|['\"]coap['\"]|"
                  r"node-ssdp|ssdpy|async_upnp_client|NWListener\.Service")


def norm_service(t: str) -> str | None:
    m = SVC.search(t or "")
    return m.group(0).lower() if m else None


def _kw(args):
    out = {}
    for x in args:
        m = re.match(r"\s*([A-Za-z_]\w*)\s*[=:](?![=:])\s*(.*)", x, re.S)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def _pos(args):
    return [x for x in args if not re.match(r"\s*[A-Za-z_]\w*\s*[=:](?![=:])", x)]


def _concat_parts(e: str) -> list[str]:
    """Split `a + "b" + c` on top-level `+` (outside quotes and brackets)."""
    parts, depth, q, cur = [], 0, None, ""
    i = 0
    while i < len(e):
        ch = e[i]
        if q:
            cur += ch
            if ch == "\\" and i + 1 < len(e):
                cur += e[i + 1]
                i += 1
            elif ch == q:
                q = None
        elif ch in "\"'`":
            q = ch
            cur += ch
        elif ch in "([{":
            depth += 1
            cur += ch
        elif ch in ")]}":
            depth -= 1
            cur += ch
        elif ch == "+" and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur.strip())
    return [p for p in parts if p] if all(parts) else [e]


class Apps:
    def __init__(self, scan):
        self.s = scan
        self.st = defaultdict(int)

    def string(self, expr: str | None, ctx: dict, depth: int = 0) -> str | None:
        """A string literal, f-string / template (parts become `{x}`) or a name assigned one (function, file,
        unique project constant)."""
        if not expr or depth > 3:
            return None
        e = _strip(expr)
        s = _lit(e) if _lit(e) is not None else _lit(e[1:]) if e[:1] in "fF" else None
        if s is not None:
            return re.sub(r"\$\{([^{}]+)\}|\{([^{}]+)\}", lambda m: "{" + (m.group(1) or m.group(2)).split(".")[-1] + "}", s)
        parts = _concat_parts(e)
        if len(parts) > 1:               # "coap://" + self.netloc + "/path": unknown parts become `{name}`
            out = []
            for p in parts:
                v = self.string(p, ctx, depth + 1)
                out.append(v if v is not None else "{" + re.sub(r"\W+", "_", p.split(".")[-1]).strip("_") + "}")
            return "".join(out) if any(_lit(_strip(p)) is not None for p in parts) else None
        m = re.fullmatch(r"(?:self\.|this\.)?([A-Za-z_]\w*)", e)
        if not m:
            return None
        name = m.group(1)
        src, lo, pos = ctx["src"], ctx["lo"], ctx["pos"]
        rx = re.compile(rf"(?<![\w.$]){re.escape(name)}\s*(?::\s*[^=\n;]+?)?\s*(?<![=!<>])=(?![=>])\s*(" + LIT.pattern + r"(?:\s*\+\s*[\w.]+)*(?:\s*\+\s*" + LIT.pattern.replace("(?P<q>", "(?P<q2>").replace("(?P=q)", "(?P=q2)").replace("(?P<s>", "(?P<s2>") + r")?)(?=[ \t]*(?:$|[\n;)#,]|//))", re.M)
        hits = list(rx.finditer(src[lo:pos])) or list(rx.finditer(src))
        if hits:
            return self.string(hits[-1].group(1), ctx, depth + 1)
        vals = {v for _f, v in self.s.const_index().get(name) or []}
        if len(vals) == 1:
            return self.string(next(iter(vals)), ctx, depth + 1)
        return None

    def recv(self, proto, name, handler, file, pos, ctx, api, conf=RESOLVED, **attrs):
        from .protocols import protocol_receive
        if self.s.masked(file, pos):
            self.st["in_comment"] += 1
            return
        h = handler or ctx.get("fn")
        if not h or not name:
            self.st[f"{proto}_unresolved"] += 1
            return
        protocol_receive(self.s.b, proto, name, h, file, self.s.line_of(file, pos), conf, library=api, **attrs)
        self.st[f"{proto}_receivers"] += 1

    def send(self, proto, name, file, pos, ctx, api, conf=RESOLVED, role="send", **attrs):
        from .protocols import protocol_send
        from .tests_index import is_test_node
        src = ctx.get("fn")
        if self.s.masked(file, pos):
            self.st["in_comment"] += 1
            return
        if not src or not name:
            self.st[f"{proto}_unresolved"] += 1
            return
        n = self.s.b.nodes.get(src)
        protocol_send(self.s.b, proto, name, src, file, self.s.line_of(file, pos), conf, test=bool(n and is_test_node(n)),
                      role=role, library=api, **attrs)
        self.st[f"{proto}_senders"] += 1

    def calls(self, src, rx):
        for m in rx.finditer(src):
            yield m, split_args(_args_text(src, m.end() - 1))

    # ------------------------------------------------------------ per file
    def run_file(self, file, lang, src):
        if not HINT.search(src):
            return
        if lang == "py":
            self.py(file, src)
        elif lang == "js":
            self.js(file, src)
        elif lang == "swift":
            self.swift(file, src)
        elif lang == "jvm":
            self.jvm(file, src)
        elif lang == "dart":
            self.dart(file, src)

    def _svc(self, expr, c):
        s = self.string(expr, c)
        return norm_service(s) if s else None

    def py(self, file, src):
        s = self.s
        if "zeroconf" in src:
            for m, args in self.calls(src, re.compile(r"(?<![\w.])(?:Async)?ServiceInfo\s*\(")):
                if not args:
                    continue
                c = s.ctx(file, "py", m.start())
                kw, pos = _kw(args), _pos(args)
                t = self._svc(kw.get("type_") or (pos[0] if pos else None), c)
                if "port" in kw or "addresses" in kw or "parsed_addresses" in kw or len(pos) >= 4:
                    self.recv("mdns", t, None, file, m.start(), c, "zeroconf.ServiceInfo", role="advertise")
                else:
                    self.send("mdns", t, file, m.start(), c, "zeroconf.ServiceInfo", role="lookup")
            for m, args in self.calls(src, re.compile(r"(?<![\w.])(?:Async)?ServiceBrowser\s*\(")):
                if len(args) < 2:
                    continue
                c = s.ctx(file, "py", m.start())
                kw, pos = _kw(args), _pos(args)
                te = kw.get("type_") or (pos[1] if len(pos) > 1 else None)
                types = [x for x in split_args(te[1:-1])] if te and te[:1] in "[(" else [te]
                for t in types:
                    self.send("mdns", self._svc(t, c), file, m.start(), c, "zeroconf.ServiceBrowser", role="browse")
        if "pythonosc" in src:
            for m, args in self.calls(src, re.compile(r"\.map\s*\(")):
                if len(args) < 2:
                    continue
                c = s.ctx(file, "py", m.start())
                addr = self.string(args[0], c)
                if addr and addr.startswith("/"):
                    self.recv("osc", addr, s.handler(args[1], file), file, m.start(), c, "pythonosc.Dispatcher.map")
            for m, args in self.calls(src, re.compile(r"\.send_message\s*\(")):
                if args:
                    c = s.ctx(file, "py", m.start())
                    addr = self.string(args[0], c)
                    if addr and addr.startswith("/"):
                        self.send("osc", addr, file, m.start(), c, "pythonosc.send_message")
            for m, args in self.calls(src, re.compile(r"(?<![\w.])(?:osc_server\.)?(?:Threading|Forking|Blocking|AsyncIO)OSCUDPServer\s*\(")):
                if args:
                    c = s.ctx(file, "py", m.start())
                    s.emit("udp", "listen", file, m.start(), s.resolve(args[0], c), "pythonosc OSCUDPServer", c)
            for m, args in self.calls(src, re.compile(r"(?<![\w.])(?:udp_client\.)?(?:Simple)?UDPClient\s*\(")):
                if len(args) >= 2:
                    c = s.ctx(file, "py", m.start())
                    a = s.resolve(args[1], c)
                    h = s.resolve(args[0], c)
                    if h.get("host"):
                        a["host"] = h["host"]
                    s.emit("udp", "send", file, m.start(), a, "pythonosc UDPClient", c)
        if "aiocoap" in src:
            for m, args in self.calls(src, re.compile(r"\.add_resource\s*\(")):
                if len(args) < 2:
                    continue
                c = s.ctx(file, "py", m.start())
                segs = [self.string(x, c) for x in split_args(args[0].strip()[1:-1])] if args[0].strip()[:1] in "[(" else []
                if not segs or None in segs:
                    self.st["coap_unresolved"] += 1
                    continue
                cls = re.match(r"\s*([A-Za-z_][\w.]*)\s*\(", args[1])
                h = None
                if cls:
                    for meth in ("render_get", "render_post", "render_put", "render_delete", "render", "render_observe"):
                        h = s.handler(cls.group(1), file, meth)
                        if h:
                            break
                    if h is None:          # the resource class itself (render_* inherited or not found)
                        cn = cls.group(1).split(".")[-1]
                        ids = [i for i, n in s.b.nodes.items() if n.kind == "class" and n.name.split(".")[-1] == cn]
                        h = ids[0] if len(ids) == 1 else None
                self.recv("coap", "/" + "/".join(segs), h, file, m.start(), c, "aiocoap add_resource")
            for m, args in self.calls(src, re.compile(r"(?<![\w.])(?:aiocoap\.)?Message\s*\(")):
                kw = _kw(args)
                if "uri" in kw:
                    c = s.ctx(file, "py", m.start())
                    u = self.string(kw["uri"], c)
                    if u:
                        self._coap_send(u, file, m.start(), c, "aiocoap Message", kw.get("code"))
        if "ssdpy" in src or "async_upnp_client" in src:
            for m, args in self.calls(src, re.compile(r"(?<![\w.])SSDPServer\s*\(")):
                kw = _kw(args)
                c = s.ctx(file, "py", m.start())
                t = self.string(kw.get("device_type"), c)
                if t:
                    self.recv("ssdp", t, None, file, m.start(), c, "ssdpy.SSDPServer", role="advertise")
            for m, args in self.calls(src, re.compile(r"\.m_search\s*\(|(?<![\w.])async_search\s*\(")):
                kw, pos = _kw(args), _pos(args)
                c = s.ctx(file, "py", m.start())
                t = self.string(kw.get("st") or kw.get("search_target") or (pos[0] if pos and "m_search" in m.group(0) else None), c)
                if t:
                    self.send("ssdp", t, file, m.start(), c, "ssdp search", role="search")

    def _coap_send(self, u, file, pos, c, api, code=None):
        mm = re.match(r"coaps?(?:\+tcp)?://([^/]*)(/[^?#]*)?", u)
        if not mm:
            return
        path = mm.group(2) or "/"
        meth = re.sub(r"^.*\.", "", code or "").upper() or None
        self.send("coap", path, file, pos, c, api, HEURISTIC if "{" in path else RESOLVED, role="request",
                  host=mm.group(1) or None, method=meth)

    def js(self, file, src):
        s = self.s
        if re.search(r"""['"]bonjour(?:-service|-hap)?['"]""", src):
            for m, args in self.calls(src, re.compile(r"\.(publish|find|findOne)\s*\(")):
                if not args or not args[0].startswith("{"):
                    continue
                c = s.ctx(file, "js", m.start())
                kv = _kw(split_args(args[0][1:-1]))
                t = self.string(kv.get("type"), c)
                if not t:
                    self.st["mdns_unresolved"] += 1
                    continue
                pr = (self.string(kv.get("protocol"), c) or "tcp").lower()
                name = norm_service(t) or f"_{t.lower()}._{pr}"
                if m.group(1) == "publish":
                    self.recv("mdns", name, None, file, m.start(), c, "bonjour.publish", role="advertise")
                else:
                    self.send("mdns", name, file, m.start(), c, f"bonjour.{m.group(1)}", role="browse")
        if re.search(r"""['"](?:node-osc|osc|osc-js)['"]""", src):
            for m, args in self.calls(src, re.compile(r"\.send\s*\(")):
                if not args:
                    continue
                c = s.ctx(file, "js", m.start())
                if args[0].startswith("{"):
                    addr = self.string(_kw(split_args(args[0][1:-1])).get("address"), c)
                else:
                    addr = self.string(args[0], c)
                if addr and addr.startswith("/"):
                    self.send("osc", addr, file, m.start(), c, "osc send")
            for m, args in self.calls(src, re.compile(r"\.on\s*\(")):
                if len(args) >= 2:
                    c = s.ctx(file, "js", m.start())
                    addr = _lit(args[0])
                    if addr and addr.startswith("/"):
                        self.recv("osc", addr, s.handler(args[1], file), file, m.start(), c, "osc on")
        if re.search(r"""['"]coap['"]""", src):
            for m, args in self.calls(src, re.compile(r"\bcoap\.request\s*\(")):
                if args:
                    c = s.ctx(file, "js", m.start())
                    u = self.string(args[0], c)
                    if u:
                        self._coap_send(u, file, m.start(), c, "coap.request")
        if re.search(r"""['"]node-ssdp['"]""", src):
            for m, args in self.calls(src, re.compile(r"\.(addUSN|search)\s*\(")):
                if args:
                    c = s.ctx(file, "js", m.start())
                    t = self.string(args[0], c)
                    if t and t != "ssdp:all":
                        if m.group(1) == "addUSN":
                            self.recv("ssdp", t, None, file, m.start(), c, "node-ssdp addUSN", role="advertise")
                        else:
                            self.send("ssdp", t, file, m.start(), c, "node-ssdp search", role="search")

    def swift(self, file, src):
        s = self.s
        for m, args in self.calls(src, re.compile(r"\b(NWListener\.Service|NetService|NWBrowser|searchForServices)\s*\(")):
            c = s.ctx(file, "swift", m.start())
            kw = _kw(args)
            if m.group(1) == "NWBrowser":
                tm = re.search(r"\.bonjour(?:WithTXTRecord)?\(\s*type:\s*([^,)]+)", args[0] if args else "")
                t = self._svc(tm.group(1), c) if tm else None
                self.send("mdns", t, file, m.start(), c, "NWBrowser", role="browse")
            elif m.group(1) == "searchForServices":
                self.send("mdns", self._svc(kw.get("ofType"), c), file, m.start(), c, "NetServiceBrowser", role="browse")
            elif "type" in kw:
                self.recv("mdns", self._svc(kw["type"], c), None, file, m.start(), c, m.group(1), role="advertise")

    def jvm(self, file, src):
        s = self.s
        if "NsdManager" in src or "NsdServiceInfo" in src:
            for m in re.finditer(r"""(?:serviceType\s*=|setServiceType\()\s*["']([^"']+)["']""", src):
                c = s.ctx(file, "jvm", m.start())
                if re.search(r"registerService\s*\(", src[c["lo"]:]):
                    self.recv("mdns", norm_service(m.group(1)), None, file, m.start(), c,
                              "NsdManager.registerService", role="advertise")
            for m, args in self.calls(src, re.compile(r"\.discoverServices\s*\(")):
                if args:
                    c = s.ctx(file, "jvm", m.start())
                    self.send("mdns", self._svc(args[0], c), file, m.start(), c, "NsdManager.discoverServices", role="browse")
        if "jmdns" in src.lower():
            for m, args in self.calls(src, re.compile(r"ServiceInfo\.create\s*\(")):
                if args:
                    c = s.ctx(file, "jvm", m.start())
                    self.recv("mdns", self._svc(args[0], c), None, file, m.start(), c, "JmDNS ServiceInfo.create", role="advertise")
            for m, args in self.calls(src, re.compile(r"\.(addServiceListener|list)\s*\(")):
                if args:
                    c = s.ctx(file, "jvm", m.start())
                    t = self._svc(args[0], c)
                    if t:
                        self.send("mdns", t, file, m.start(), c, f"JmDNS {m.group(1)}", role="browse")
        if "CoapResource" in src or "CoapClient" in src:
            for m in re.finditer(r"class\s+(\w+)[^{]*?CoapResource\s*\(\s*(" + LIT.pattern + ")", src):
                c = s.ctx(file, "jvm", m.start())
                h = None
                for meth in ("handleGET", "handlePOST", "handlePUT", "handleDELETE", "handleRequest"):
                    h = s.handler(m.group(1), file, meth)
                    if h:
                        break
                self.recv("coap", "/" + _lit(m.group(2)).strip("/"), h, file, m.start(), {**c, "fn": h or c["fn"]},
                          "Californium CoapResource")
            for m, args in self.calls(src, re.compile(r"(?<![\w.])CoapClient\s*\(")):
                if args:
                    c = s.ctx(file, "jvm", m.start())
                    u = self.string(args[0], c)
                    if u:
                        self._coap_send(u, file, m.start(), c, "Californium CoapClient")

    def dart(self, file, src):
        s = self.s
        for m, args in self.calls(src, re.compile(r"(?<![\w.])(BonsoirService|BonsoirDiscovery)\s*\(")):
            c = s.ctx(file, "dart", m.start())
            t = self._svc(_kw(args).get("type"), c)
            if m.group(1) == "BonsoirService":
                self.recv("mdns", t, None, file, m.start(), c, "bonsoir BonsoirService", role="advertise")
            else:
                self.send("mdns", t, file, m.start(), c, "bonsoir BonsoirDiscovery", role="browse")
        for m, args in self.calls(src, re.compile(r"ResourceRecordQuery\.serverPointer\s*\(")):
            if args:
                c = s.ctx(file, "dart", m.start())
                self.send("mdns", self._svc(args[0], c), file, m.start(), c, "multicast_dns query", role="browse")

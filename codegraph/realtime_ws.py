"""Raw WebSocket servers and Server-Sent-Events routes (#32 part 2).

  ws servers   JS / TS `new WebSocketServer({ path, port, server })` / `new WebSocket.Server(..)` / `new Server(..)`
               imported from `ws` (also `isomorphic-ws`), with the handler of `wss.on('connection', h)` (an inline
               callback: the function around it); Python `websockets.serve(handler, host, port)` (also
               `websockets.server.serve` / `websockets.asyncio.server.serve`, `from websockets.. import serve`).
               Each is a `route:WS <path>` websocket entry point (path `/` when the server takes any path, with
               `attrs.any_path`; `attrs.port` when the port is a literal). The express-ws / Elysia `app.ws(path, h)`,
               @fastify/websocket `{ websocket: true }` and Hono `upgradeWebSocket(h)` routes come from the express
               plugin, FastAPI / Starlette and Django Channels WS routes from their plugins.
  clients      `new WebSocket(url)` / `ReconnectingWebSocket` / `Sockette` are `http:WS <path>` client endpoints and
               `new EventSource(url)` / `fetchEventSource(url)` `http:GET <path>` ones with `stream: sse` (the TS
               extractor); this module links the WS ones to the ws server routes found here (in-repo, as HTTP calls).
  sse routes   a route whose handler sends `text/event-stream` (a Content-Type header / media_type / mimetype, not an
               `Accept` header), returns `EventSourceResponse(..)` (sse-starlette) or calls Hono `streamSSE(..)` gets
               `attrs.stream = "sse"`; Nest `@Sse()` routes get it from the Nest plugin.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .brokers import JS_EXT, Scan as BrokerScan, _args, _key, _kw, _strlit
from .core.model import EXACT, HEURISTIC

WS_IMP = re.compile(r"""(?:from\s+|require\(\s*|import\s*\(\s*)['"](?:ws|isomorphic-ws)['"]""")
WS_NEW = re.compile(r"\b(?:(?:const|let|var)\s+(\w+)|(?:this\.)?(\w+))\s*(?::\s*[\w.<>]+\s*)?=\s*new\s+"
                    r"((?:WebSocket|WS|ws)\s*\.\s*(?:WebSocket)?Server|WebSocketServer|Server)\s*\(")
PY_SERVE = re.compile(r"\b(?:websockets\s*\.\s*(?:asyncio\s*\.\s*)?(?:server\s*\.\s*)?serve|(?<![.\w])serve)\s*\(")
SSE_HINT = re.compile(r"text/event-stream|EventSourceResponse|streamSSE|ServerSentEvent")
SSE_RX = re.compile(r"""\bEventSourceResponse\s*\(|\bstreamSSE\s*\(|response_class\s*=\s*EventSourceResponse|"""
                    r"""text/event-stream""")


class WS(BrokerScan):
    def run(self) -> dict:
        new_routes = []
        for f in sorted(self.s.files):
            if f.endswith(JS_EXT + (".jsx",)) and not f.endswith(".d.ts"):
                src = self.s.text(f)
                if "Server" in src and WS_IMP.search(src):
                    new_routes += self.js_file(f, src)
            elif f.endswith(".py"):
                src = self.s.text(f)
                if "websockets" in src and "serve" in src:
                    new_routes += self.py_file(f, src)
        if new_routes:
            self.link(new_routes)
        self.sse_routes()
        out = {k: v for k, v in self.st.items() if v}
        if out and self.samples:
            out["samples"] = dict(self.samples)
        return out

    # ------------------------------------------------------------ ws (JS / TS)
    def js_file(self, f, src):
        out = []
        imported_server = re.search(r"""\{[^}]*\bServer\b[^}]*\}\s*(?:from\s*['"](?:ws|isomorphic-ws)['"]|=\s*require\(\s*['"]ws['"])""", src)
        for m in WS_NEW.finditer(src):
            if self.s.masked(f, m.start()):
                continue
            ctor = re.sub(r"\s+", "", m.group(3))
            if ctor == "Server" and not imported_server:
                continue                                   # `new Server(..)` of socket.io / http / net
            var = m.group(1) or m.group(2)
            if self.is_test(f, self.fn_at(f, m.start())):
                self.st["ws_test_servers"] += 1
                continue
            a = _args(src, m.end() - 1)
            opts = a[0].strip() if a else ""
            path, pconf, port = "/", EXACT, None
            any_path = True
            if opts.startswith("{"):
                pv = _key(opts, "path")
                if pv is not None:
                    v, c = self.value(f, m.start(), pv)
                    any_path = False
                    if v is None or not v.strip("{}") or v.startswith("env:"):
                        path, pconf = "/{path}", HEURISTIC
                        self.miss("ws_path_unresolved", pv)
                    else:
                        path = ("/" + v.lstrip("/")) if not v.startswith(("/", "{")) else v
                        pconf = EXACT if _strlit(pv) is not None else (c or HEURISTIC)
                        if "{" in path:
                            pconf = HEURISTIC
                pt = _key(opts, "port")
                if pt is not None and re.fullmatch(r"\d{2,5}", pt.strip()):
                    port = int(pt.strip())
            handler = self.connection_handler(f, src, var, m.end())
            up = self.upgrade(f, src, var, m.end()) if any_path and re.search(r"\bnoServer\s*:\s*true", opts) else None
            if up:
                handler = handler or up[0]
                if up[1]:
                    path, pconf, any_path = up[1], up[2], False
            rid = self.route(f, m.start(), "WS", path, handler, pconf, "ws", any_path=any_path or None, port=port, lang="ts")
            out.append(rid)
            self.st["ws_servers"] += 1
        return out

    def connection_handler(self, f, src, var, after):
        if not var:
            return None
        rx = re.compile(rf"\b(?:this\.)?{re.escape(var)}\s*\.\s*on\s*\(\s*['\"]connection['\"]\s*,\s*")
        m = rx.search(src, after) or rx.search(src)
        if not m:
            return None
        a = _args(src, src.index("(", m.start() + len(var)))
        h = a[1].strip() if a and len(a) > 1 else ""
        if re.fullmatch(r"[\w$.]+(?:\.bind\(\s*this\s*\))?", h):
            return self.handler(f, m.start(), h)
        # inline callback: its own node when the extractor made one, else the function around the listener
        body = src.find("{", m.end())
        return self.fn_at(f, body + 1 if 0 <= body < m.end() + 400 else m.start())

    def upgrade(self, f, src, var, after):
        """`noServer` servers: the function calling `wss.handleUpgrade(..)` and the path it checks first
        (`pathname === '/x'`, `req.url.startsWith(path)`: a prefix, heuristic) -> (handler, path, conf)."""
        m = re.compile(rf"\b(?:this\.)?{re.escape(var)}\s*\.\s*handleUpgrade\s*\(").search(src, after)
        if not m:
            return None
        fn, lo, _hi = self.s.fn_bounds(f, m.start())
        seg = src[lo:m.start()]
        ex = r"([\w$.]+|'[^'\n]*'|\"[^\"\n]*\")"
        hit = None
        for c in re.finditer(rf"(?:pathname|\.url)\s*(?:\?\.\s*)?(?:===?\s*{ex}|\.?\s*startsWith\s*\(\s*{ex}\s*\))|{ex}\s*===?\s*[\w$.?]*(?:pathname|\.url)\b", seg):
            hit = c
        path, conf = None, HEURISTIC
        if hit:
            e = hit.group(1) or hit.group(2) or hit.group(3)
            v, _c = self.value(f, lo + hit.start(), e)
            if v and v.startswith("/") and not v.startswith("{"):
                path = v.rstrip("/") or "/"
                if hit.group(2):
                    path = path.rstrip("/") + "/{rest*}"
                conf = EXACT if hit.group(1) or hit.group(3) else HEURISTIC
                if _strlit(e) is None:
                    conf = HEURISTIC if hit.group(2) else conf
        return fn, path, conf

    # ------------------------------------------------------------ websockets (Python)
    def py_file(self, f, src):
        if not re.search(r"^\s*(?:import\s+websockets|from\s+websockets[\w.]*\s+import)", src, re.M):
            return []
        bare = re.search(r"^\s*from\s+websockets[\w.]*\s+import\s+[^\n]*\bserve\b", src, re.M)
        out = []
        for m in PY_SERVE.finditer(src):
            if self.s.masked(f, m.start()) or (not m.group(0).startswith("websockets") and not bare):
                continue
            a = _args(src, m.end() - 1)
            if not a:
                continue
            pos_args = [x.strip() for x in a if not re.match(r"\s*\w+\s*=", x)]
            h = pos_args[0] if pos_args else (_kw(a, "handler") or "")
            pm = re.fullmatch(r"(?:functools\.)?partial\(\s*([\w.]+)[\s\S]*\)", h.strip())
            if pm:
                h = pm.group(1)                            # functools.partial(self._on_conn, scheme=..)
            if self.is_test(f, self.fn_at(f, m.start())):
                self.st["ws_test_servers"] += 1            # a server a test starts is not an entry point
                continue
            port = pos_args[2] if len(pos_args) > 2 else _kw(a, "port")
            port = int(port) if port and re.fullmatch(r"\d{2,5}", port.strip()) else None
            handler = self.handler(f, m.start(), h)
            out.append(self.route(f, m.start(), "WS", "/", handler, EXACT, "websockets", any_path=True, port=port, lang="py"))
            self.st["ws_servers"] += 1
        return out

    # ------------------------------------------------------------ nodes
    def route(self, f, pos, method, path, handler, conf, framework, any_path=None, port=None, lang="ts"):
        from .plugins.tsweb.common import module_of
        line = self.s.line_of(f, pos)
        key = f"{method} {path}"
        attrs = {"uri": path, "method": method, "framework": framework}
        rid = self.b.add_node("route", key, name=key, file=f, line=line, module=module_of(f) if lang == "ts" else None,
                              lang=lang, entry_kind="websocket", attrs=attrs)
        n = self.b.nodes[rid]
        n.entry_kind = n.entry_kind or "websocket"
        if any_path:
            n.attrs["any_path"] = True
        if port:
            n.attrs["ports"] = sorted(set(n.attrs.get("ports") or []) | {port})
        if handler and self.b.has(handler) and self.b.nodes[handler].kind in ("function", "method"):
            self.b.add_edge(rid, handler, "ROUTES_TO", file=f, line=line, confidence=conf)
            n.attrs.setdefault("handler", self.b.nodes[handler].name)
        elif not any(e.src == rid and e.kind == "ROUTES_TO" for e in self.b.edges.values()):
            n.attrs["handler_unresolved"] = True        # an inline callback without its own node (not the module)
        return rid

    def link(self, rids):
        """The repo's own WebSocket clients (`new WebSocket('/live')`) -> the ws server routes found here."""
        from .link import match_endpoint
        routes = []
        for rid in dict.fromkeys(rids):
            n = self.b.nodes[rid]
            if n.lang == "ts" and "{" not in n.attrs["uri"] and not n.attrs.get("any_path"):
                routes.append({"id": rid, "uri": n.attrs["uri"], "method": "WS", "uris": [("as-declared", n.attrs["uri"])],
                               "file": n.file, "line": n.line})
        if not routes:
            return
        rmap = {r["id"]: r for r in routes}
        for n in list(self.b.nodes.values()):
            a = n.attrs or {}
            if n.kind != "http" or n.lang != "ts" or a.get("method") != "WS" or a.get("origin_kind", "api") == "other":
                continue
            ok = a.get("origin_kind", "api")
            res = match_endpoint("WS", a.get("path", ""), routes, "api" if ok == "same-origin" else ok, a.get("origin"))
            for m in res["matched"]:
                r = rmap[m["route"]]
                self.b.add_edge(n.id, m["route"], "MATCHES_ROUTE", file=r["file"], line=r["line"], confidence=m["confidence"],
                                client_path=a.get("path"), uri_variant=m["uri_variant"], segments=m["segments"], in_repo=True)
                self.st["ws_in_repo_links"] += 1

    # ------------------------------------------------------------ SSE
    def sse_routes(self):
        handlers = defaultdict(list)
        for e in self.b.edges.values():
            if e.kind == "ROUTES_TO":
                handlers[e.src].append(e.dst)
        for rid, n in self.b.nodes.items():
            if n.kind != "route" or (n.attrs or {}).get("method") in ("WS", None) or n.attrs.get("stream"):
                continue
            for h in handlers.get(rid, ()):
                hn = self.b.nodes.get(h)
                if hn is None or not hn.file or not hn.line or hn.kind not in ("function", "method"):
                    continue
                src = self.s.text(hn.file)
                if not SSE_HINT.search(src):
                    continue
                lo_line = n.line if n.file == hn.file and n.line and hn.line - 3 <= n.line <= hn.line else hn.line
                lo = self.s.off(hn.file, lo_line)
                hi = self.s.off(hn.file, (hn.end_line or hn.line) + 1) if (hn.end_line or hn.line) < len(self.s.lines[hn.file]) else len(src)
                for m in SSE_RX.finditer(src, lo, hi):
                    if self.s.masked(hn.file, m.start()):
                        continue
                    if m.group(0) == "text/event-stream" and re.search(r"accept['\"]?\s*[:=,]?\s*['\"]?[^'\"\n]{0,40}$", src[max(lo, m.start() - 60):m.start()], re.I):
                        continue                            # `Accept: text/event-stream`: a request for a stream
                    n.attrs["stream"] = "sse"
                    self.st["sse_routes"] += 1
                    break
                if n.attrs.get("stream"):
                    break


def apply(project, builder, sock=None) -> dict:
    """WebSocket servers, their in-repo clients and SSE routes (#32 part 2); empty without any."""
    return WS(project, builder, sock).run()

"""RPC contracts (#33, epic #29): gRPC services declared in `.proto` files, as `endpoint:grpc:<package>.<Service>/<Method>`.

Every `rpc` of every `service` in the project's `.proto` files becomes an endpoint (attrs: `service`, `package`,
`request`, `response`, `streaming` unary / server / client / bidi, `declared_in`), whether or not code implements or
calls it, so `cg protocols --protocol grpc` lists unimplemented and uncalled methods. A source scan over the built
graph then pairs code with the contract:

  server  the class that extends / implements the generated base, one RECEIVED_BY per method whose name is an rpc of
          the service (`GetFeature` / `getFeature` / `get_feature` all match): Python `class X(pb2_grpc.SvcServicer)`
          (or the class handed to `add_SvcServicer_to_server`), Kotlin / Java `SvcCoroutineImplBase` / `SvcImplBase`,
          C++ `Svc::Service` / `AsyncService` / `CallbackService`, Rust tonic `impl svc_server::Svc for X`, Dart
          `SvcServiceBase`, Swift grpc-swift `Pkg_SvcAsyncProvider` / `Pkg_Svc.SimpleServiceProtocol`, PHP `extends
          SvcStub` / `implements SvcInterface`; and registrations: grpc-js `server.addService(pkg.Svc.service, { getFeature:
          fn })` (also `SvcService` of static codegen) and Connect `router.service(Svc, impl)`
  client  calls on a stub held in a variable or field: Python `SvcStub(channel)`, Kotlin `SvcCoroutineStub`, Java
          `SvcGrpc.newBlockingStub`, C++ `Svc::NewStub(channel)` (`stub_->GetFeature`, `AsyncGetFeature`,
          `async()->GetFeature`), Rust `SvcClient::connect(..)`, TS / JS `new SvcClient(..)` (ts-proto, static
          codegen), grpc-js `new pkg.Svc(addr, creds)`, Connect `createClient(Svc, transport)`, Dart `SvcClient(channel)`,
          Swift `Pkg_SvcAsyncClient(..)` / `Pkg_Svc.Client(..)`, PHP `new Pkg\\SvcClient(..)`. A stub handed to a helper as
          a parameter (`def get_one(stub): stub.GetFeature(..)`) is matched by the method name when the file creates
          stubs of one service only, else by a method name unique among the project's services (heuristic).

Generated code (`*_pb2_grpc.py`, `*_grpc_pb.js`, `*.grpc.pb.cc`, ...) is skipped: the IDL is the contract.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

from .core.model import EXACT, HEURISTIC, RESOLVED

GENERATED = re.compile(r"(?:_pb2(?:_grpc)?\.pyi?|_grpc_pb\.[jt]s|_pb\.[jt]s|\.pb\.(?:h|cc|go|swift|dart)|\.grpc\.pb\.(?:h|cc)|"
                       r"\.grpc\.swift|\.pbgrpc\.dart|\.pbenum\.dart|\.pbjson\.dart|_connect\.[jt]s|_grpc\.pb\.go|Grpc(?:Kt)?\.(?:java|kt))$")
SKIP_PARTS = {"node_modules", "vendor", "third_party", "third-party", ".git", "build", "dist", "target", ".dart_tool", "Pods",
              ".build", "__pycache__", ".venv", "venv"}
HINT = re.compile(r"grpc|Grpc|GRPC|Servicer|Stub|tonic|ServiceBase|ImplBase|AsyncProvider|ServiceProtocol|connectrpc|"
                  r"@bufbuild|addService|NewStub")


def norm(s: str) -> str:
    return re.sub(r"[\W_]", "", s or "").lower()


def _mask_proto(t: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", lambda m: re.sub(r"[^\n]", " ", m.group(0)), t)


def _block(src: str, at: int) -> tuple[int, int]:
    """(start, end) offsets of the `{ ... }` block opening at or after `at` (end: after the closing brace)."""
    i = src.find("{", at)
    if i < 0:
        return at, at
    depth, j, q = 0, i, None
    while j < len(src):
        c = src[j]
        if q:
            if c == "\\":
                j += 2
                continue
            if c == q:
                q = None
        elif c in "\"'`":
            q = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i, j + 1
        j += 1
    return i, len(src)


def _py_body(src: str, at: int) -> tuple[int, int]:
    """(start, end) of the indented body of the Python `class` / `def` statement starting at `at`."""
    ls = src.rfind("\n", 0, at) + 1
    ind = len(src[ls:at]) - len(src[ls:at].lstrip())
    nl = src.find("\n", at)
    if nl < 0:
        return at, len(src)
    for m in re.finditer(r"\n([ \t]*)(\S)", src[nl:]):
        if len(m.group(1)) <= ind and m.group(2) not in "#)":
            return nl, nl + m.start() + 1
    return nl, len(src)


class Contracts:
    """The project's `.proto` services."""

    RPC = re.compile(r"\brpc\s+(\w+)\s*\(\s*(stream\s+)?([\w.]+)\s*\)\s*returns\s*\(\s*(stream\s+)?([\w.]+)\s*\)")

    def __init__(self, root):
        self.root = root
        self.services: dict[str, dict] = {}          # full name -> {name, package, file, line, methods: {norm: {...}}}
        self.by_short: dict[str, list[str]] = defaultdict(list)
        self.files = 0
        for rel in self._protos():
            try:
                t = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            self.files += 1
            self._parse(rel, t)

    def _protos(self):
        out = []
        for dp, dns, fns in os.walk(self.root):
            dns[:] = sorted(d for d in dns if d not in SKIP_PARTS and not d.startswith("."))
            for f in fns:
                if f.endswith(".proto"):
                    out.append(os.path.relpath(os.path.join(dp, f), self.root))
            if len(out) > 2000:
                break
        return sorted(out)

    def _parse(self, rel, text):
        t = _mask_proto(text)
        pm = re.search(r"(?m)^\s*package\s+([\w.]+)\s*;", t)
        pkg = pm.group(1) if pm else ""
        for sm in re.finditer(r"(?m)^\s*service\s+(\w+)\s*\{", t):
            a, b = _block(t, sm.end() - 1)
            full = f"{pkg}.{sm.group(1)}" if pkg else sm.group(1)
            line = t.count("\n", 0, sm.start(1)) + 1
            svc = self.services.get(full)
            if svc is None:
                svc = self.services[full] = {"name": sm.group(1), "package": pkg, "full": full, "decl": [], "methods": {}}
                self.by_short[sm.group(1)].append(full)
            svc["decl"].append(f"{rel}:{line}")
            for rm in self.RPC.finditer(t, a, b):
                cs, ss = bool(rm.group(2)), bool(rm.group(4))
                kind = "bidi" if cs and ss else "client" if cs else "server" if ss else "unary"
                m = svc["methods"].setdefault(norm(rm.group(1)), {"name": rm.group(1), "request": rm.group(3),
                                                                  "response": rm.group(5), "streaming": kind, "decl": []})
                m["decl"].append(f"{rel}:{t.count(chr(10), 0, rm.start(1)) + 1}")

    def endpoint(self, full: str, method: str) -> str:
        return f"{full}/{self.services[full]['methods'][norm(method)]['name']}"

    def pick(self, short: str, text: str = "", hint: str = "") -> str | None:
        """The service called `short`: the only one, else the one whose package the hint / file text names."""
        c = self.by_short.get(short) or []
        if len(c) <= 1:
            return c[0] if c else None
        if hint:        # a qualifier like `fleet_pb2_grpc` / `fleet::v1` names a package segment
            nh = norm(hint)
            got = [f for f in c if any(len(seg) > 2 and not re.fullmatch(r"v\d+\w*", seg) and norm(seg) in nh
                                       for seg in self.services[f]["package"].split("."))]
            if len(got) == 1:
                return got[0]
        for probe in (hint, text):
            if not probe:
                continue
            got = [f for f in c if self.services[f]["package"] and (
                self.services[f]["package"] in probe or norm(self.services[f]["package"]) in norm(probe)[:400]
                or re.search(rf"\b{re.escape(self.services[f]['package'].split('.')[-1])}\b", probe))]
            if len(got) == 1:
                return got[0]
        return None


class Scan:
    def __init__(self, project, b):
        from .sockets import Scan as SockScan
        self.b = b
        self.s = SockScan(project, b)
        self.c = Contracts(project.root)
        self.st = defaultdict(int)
        self.samples = defaultdict(list)
        self.method_owner = {}      # norm(method) -> {service full names}
        for full, svc in self.c.services.items():
            for k in svc["methods"]:
                self.method_owner.setdefault(k, set()).add(full)
        self.done = set()

    def miss(self, key, text):
        self.st[key] += 1
        if len(self.samples[key]) < 6:
            self.samples[key].append(text[:100])

    # ------------------------------------------------------------ contract endpoints
    def declare(self):
        from .protocols import _endpoint
        for full, svc in sorted(self.c.services.items()):
            for m in svc["methods"].values():
                nid = _endpoint(self.b, "grpc", f"{full}/{m['name']}", {
                    "service": full, "package": svc["package"] or None, "method": m["name"], "request": m["request"],
                    "response": m["response"], "streaming": m["streaming"], "declared_in": m["decl"][0],
                    "declared_also": m["decl"][1:] or None})
                self.b.nodes[nid].attrs.setdefault("contract", "proto")
            self.st["services"] += 1
            self.st["methods"] += len(svc["methods"])

    # ------------------------------------------------------------ helpers
    def methods_in(self, file, lo_line, hi_line, cls=None):
        """Function / method nodes of `file` between two lines (a class body), plus out-of-line `Cls::m` definitions."""
        out = [(nid, ln) for ln, end, nid in self.s.spans.get(file, ()) if lo_line <= ln <= hi_line]
        if cls and file.endswith((".h", ".hh", ".hpp", ".hxx", ".cc", ".cpp", ".cxx", ".c")):
            # C++ methods defined out of line (`Status Impl::GetFeature(..) {..}`), in this file or a sibling one
            seen = {n for n, _ in out}
            d = os.path.dirname(file)
            rx = re.compile(rf"(?:^|[#:\s]){re.escape(cls)}::\w+(?:\(.*\))?$")
            for nid, n in self.b.nodes.items():
                if n.kind in ("function", "method") and nid not in seen and n.file and \
                        os.path.dirname(n.file) == d and rx.search(nid):
                    out.append((nid, n.line))
        return out

    def short(self, nid):
        from .sockets import _short
        return _short(self.b.nodes.get(nid))

    def recv(self, full, method_nid, file, line, conf, api, how):
        from .protocols import protocol_receive
        k = norm(self.short(method_nid))
        svc = self.c.services[full]
        if k not in svc["methods"]:
            return False
        key = ("r", full, k, method_nid)
        if key in self.done:
            return True
        self.done.add(key)
        protocol_receive(self.b, "grpc", self.c.endpoint(full, k), method_nid, file, line, conf, library=api, how=how)
        self.st["server_methods"] += 1
        return True

    def server_class(self, full, file, src, cls, body, conf, api, how):
        if self.s.masked(file, body[0]):
            return
        lo, hi = self.s.line_of(file, body[0]), self.s.line_of(file, max(body[0], body[1] - 1))
        hit = 0
        for nid, ln in self.methods_in(file, lo, hi, cls):
            hit += self.recv(full, nid, file, ln, conf, api, how)
        self.st["server_classes"] += 1
        if not hit:
            self.miss("server_class_without_methods", f"{file}:{lo} {cls}")

    def send(self, full, method, file, pos, conf, api, how):
        from .protocols import protocol_send
        from .tests_index import is_test_node
        k = norm(method)
        if k not in self.c.services[full]["methods"] or self.s.masked(file, pos):
            return False
        fn, _lo, _hi = self.s.fn_bounds(file, pos)
        if fn is None:
            self.miss("client_call_outside_function", f"{file}:{self.s.line_of(file, pos)}")
            return False
        line = self.s.line_of(file, pos)
        key = ("s", full, k, fn, line)
        if key in self.done:
            return True
        self.done.add(key)
        n = self.b.nodes.get(fn)
        protocol_send(self.b, "grpc", self.c.endpoint(full, k), fn, file, line, conf, test=bool(n and is_test_node(n)),
                      role="request", library=api, how=how)
        self.st["client_calls"] += 1
        return True

    # ------------------------------------------------------------ servers
    def servers(self, file, lang, src):
        c = self.c
        if lang == "py":
            for m in re.finditer(r"(?m)^[ \t]*class\s+(\w+)\s*\(([^)]*)\)\s*:", src):
                for base in m.group(2).split(","):
                    bm = re.fullmatch(r"\s*(?:[\w.]+\.)?(\w+)Servicer\s*", base)
                    full = bm and c.pick(bm.group(1), src, base)
                    if full:
                        self.server_class(full, file, src, m.group(1), _py_body(src, m.start(1)), EXACT, "grpcio",
                                          "servicer base")
            for m in re.finditer(r"\badd_(\w+)Servicer_to_server\s*\(\s*([\w.]+)(\(\s*\))?", src):
                full = c.pick(m.group(1), src, src[max(0, m.start() - 60):m.start()])
                if not full:
                    continue
                cls = m.group(2) if m.group(3) else None
                if not cls:
                    am = list(re.finditer(rf"\b{re.escape(m.group(2).split('.')[-1])}\s*=\s*([\w.]+)\s*\(", src[:m.start()]))
                    cls = am[-1].group(1) if am else None
                if cls:
                    cls = cls.split(".")[-1]
                    cm = re.search(rf"(?m)^[ \t]*class\s+{re.escape(cls)}\b", src)
                    if cm:
                        self.server_class(full, file, src, cls, _py_body(src, cm.start()), RESOLVED, "grpcio",
                                          "add_servicer_to_server")
        elif lang == "jvm":
            for m in re.finditer(r"\bclass\s+(\w+)[^{;]*?(?::|\bextends\b)\s*([^{;]*?)\{", src):
                bm = re.search(r"(?:\b(\w+)Grpc(?:Kt)?\.)?(\w+?)(?:CoroutineImplBase|ImplBase)\b", m.group(2))
                full = bm and c.pick(bm.group(2), src, m.group(2))
                if full:
                    self.server_class(full, file, src, m.group(1), _block(src, m.end() - 1), EXACT, "grpc-java",
                                      "ImplBase")
        elif lang == "c":
            for m in re.finditer(r"\b(?:class|struct)\s+(\w+)(?:\s+final)?\s*:\s*([^{;]*?)\{", src):
                for bm in re.finditer(r"(?:[\w:]+::)?(\w+)::(?:Service|AsyncService|CallbackService|ExperimentalCallbackService|"
                                      r"StreamedUnaryService|SplitStreamedService|StreamedService|WithAsyncMethod_\w+|"
                                      r"WithCallbackMethod_\w+)\b", m.group(2)):
                    full = c.pick(bm.group(1), src, m.group(2))
                    if full:
                        self.server_class(full, file, src, m.group(1), _block(src, m.end() - 1), EXACT, "grpc++",
                                          "Service base")
                        break
        elif lang == "rs":
            if "tonic" not in src and "_server" not in src:
                return
            for m in re.finditer(r"\bimpl(?:\s*<[^{>]*>)?\s+((?:[\w]+::)*)(\w+)\s+for\s+(\w+)", src):
                full = c.pick(m.group(2), src, m.group(1))
                if full:
                    self.server_class(full, file, src, m.group(3), _block(src, m.end()), EXACT, "tonic",
                                      "service trait impl")
        elif lang == "dart":
            for m in re.finditer(r"\bclass\s+(\w+)\s+extends\s+(?:[\w.]+\.)?(\w+)ServiceBase\b", src):
                full = c.pick(m.group(2), src)
                if full:
                    self.server_class(full, file, src, m.group(1), _block(src, m.end()), EXACT, "grpc-dart",
                                      "ServiceBase")
        elif lang == "swift":
            for m in re.finditer(r"\b(?:class|struct|actor|extension)\s+(\w+)\s*:\s*([^{]*)\{", src):
                bm = re.search(r"\b(\w+?)_(\w+?)(?:AsyncProvider|Provider)\b", m.group(2)) or \
                    re.search(r"\b(?:(\w+)_)?(\w+)\.(?:SimpleServiceProtocol|ServiceProtocol)\b", m.group(2))
                full = bm and c.pick(bm.group(2), src, bm.group(1) or "")
                if full:
                    self.server_class(full, file, src, m.group(1), _block(src, m.end() - 1), EXACT, "grpc-swift",
                                      "service provider")
        elif lang == "php":
            for m in re.finditer(r"\bclass\s+(\w+)\s+(?:extends\s+\\?((?:\w+\\)*)(\w+)Stub\b|[^{]*?implements\s+[^{]*?"
                                 r"\\?((?:\w+\\)*)(\w+)Interface\b)", src):
                short, hint = (m.group(3), m.group(2)) if m.group(3) else (m.group(5), m.group(4))
                full = c.pick(short, src, (hint or "").replace("\\", "."))
                if full:
                    self.server_class(full, file, src, m.group(1), _block(src, m.end()), EXACT, "grpc-php",
                                      "service stub / interface")
        elif lang == "js":
            self.js_servers(file, src)

    def js_servers(self, file, src):
        c = self.c
        regs = []
        for m in re.finditer(r"\.addService\s*\(\s*([\w.$\[\]'\"]+?)\s*,\s*", src):
            ref = m.group(1)
            sm = re.fullmatch(r"(?:([\w.$]+)\.)?(\w+?)\.service", ref) or re.fullmatch(r"(?:([\w.$]+)\.)?(\w+?)Service", ref)
            full = None
            if sm:
                full = c.pick(sm.group(2), src, sm.group(1) or "")
                if not full and ref.endswith("Service") and not ref.endswith(".service"):
                    full = c.pick(sm.group(2) + "Service", src, sm.group(1) or "")
            if full:
                regs.append((full, m.end(), "@grpc/grpc-js addService"))
        for m in re.finditer(r"\b(?:router|r)\.service\s*\(\s*(\w+)\s*,\s*", src):
            full = c.pick(m.group(1), src)
            if full:
                regs.append((full, m.end(), "connect router.service"))
        for full, at, api in regs:
            if self.s.masked(file, at):
                continue
            self.st["server_registrations"] += 1
            rest = src[at:at + 200].lstrip()
            obj = None
            if rest.startswith("{"):
                obj = _block(src, at)
            else:
                im = re.match(r"(?:new\s+)?([\w$]+)", rest)
                if im:
                    dm = re.search(rf"(?:const|let|var)\s+{re.escape(im.group(1))}\b[^=\n]*=\s*\{{", src)
                    if dm:
                        obj = _block(src, dm.end() - 1)
                    else:
                        cm = re.search(rf"\bclass\s+{re.escape(im.group(1))}\b[^{{]*\{{", src)
                        if cm:
                            lo, hi = self.s.line_of(file, cm.end()), self.s.line_of(file, _block(src, cm.end() - 1)[1])
                            for nid, ln in self.methods_in(file, lo, hi, im.group(1)):
                                self.recv(full, nid, file, ln, RESOLVED, api, "implementation class")
                            continue
            if obj is None:
                self.miss("registration_unresolved", f"{file}:{self.s.line_of(file, at)}")
                continue
            body = src[obj[0] + 1:obj[1] - 1]
            depth, start = 0, 0
            parts = []
            for i, ch in enumerate(body + ","):
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "," and depth == 0:
                    parts.append((start, body[start:i]))
                    start = i + 1
            svc = c.services[full]
            for off, p in parts:
                km = re.match(r"\s*(?:async\s+)?\*?\s*['\"]?(\w+)['\"]?\s*(:\s*(.+)|\(|$)", p, re.S)
                if not km or norm(km.group(1)) not in svc["methods"]:
                    continue
                pos = obj[0] + 1 + off + km.start(1)
                line = self.s.line_of(file, pos)
                h = None
                val = (km.group(3) or "").strip()
                if val and re.fullmatch(r"[\w$.]+", val):
                    h = self.s.handler(val, file)
                if h is None and (not val or km.group(2) == "("):
                    h = self.s.handler(km.group(1), file) if not val and km.group(2) != "(" else None
                if h is None:
                    for ln, end, nid in self.s.spans.get(file, ()):
                        if ln == line or (val and ln == self.s.line_of(file, pos + len(km.group(0)) - len(val))):
                            h = nid
                            break
                if h is None:   # an inline arrow / imported handler without a node: no edge rather than the registrar
                    self.miss("handler_unresolved", f"{file}:{line} {km.group(1)}")
                    continue
                from .protocols import protocol_receive
                protocol_receive(self.b, "grpc", c.endpoint(full, km.group(1)), h, file, line, RESOLVED, library=api,
                                 how="registration")
                self.st["server_methods"] += 1

    # ------------------------------------------------------------ clients
    STUBS = [
        # (regex, group of the service, group of a package hint, api)
        (re.compile(r"\b(?:([\w.]+)\.)?(\w+?)(?:Coroutine|Blocking|Future)?Stub\s*\("), 2, 1, "stub"),
        (re.compile(r"\b(\w+)Grpc(?:Kt)?\.new(?:Blocking|Future)?Stub\s*\("), 1, None, "grpc-java stub"),
        (re.compile(r"\b(?:(\w+)::)?(\w+)::NewStub\s*\("), 2, 1, "grpc++ NewStub"),
        (re.compile(r"\b(?:(\w+)_client::)?(\w+?)Client\s*::\s*(?:connect|new|with_origin|with_interceptor)\s*\("), 2, 1, "tonic client"),
        (re.compile(r"\bnew\s+\\?((?:\w+\\)*)(\w+?)Client\s*\("), 2, 1, "generated client"),
        (re.compile(r"(?<![\w.])(?:([\w$.]+)\.)?(\w+?)Client\s*\((?!\s*\))"), 2, 1, "generated client"),
        (re.compile(r"\b(\w+?)_(\w+?)(?:AsyncClient|NIOClient)\s*\("), 2, 1, "grpc-swift client"),
        (re.compile(r"\b(?:(\w+)_)?(\w+)\.Client\s*\("), 2, 1, "grpc-swift client"),
        (re.compile(r"\bcreate(?:Promise|Callback|Grpc(?:Web)?)?Client\s*\(\s*()(\w+)\s*,"), 2, 1, "connect createClient"),
        (re.compile(r"\bnew\s+([\w$.]+)\.(\w+)\s*\("), 2, 1, "grpc-js client"),
    ]
    TYPED = re.compile(r"\b\$?(\w+)\s*:\s*(?:[\w.]+\.)?(\w+?)(?:CoroutineStub|BlockingStub|AsyncClient|Client|Stub)\b")

    def clients(self, file, lang, src):
        c = self.c
        stubs = []          # (var, full, pos, api, scope (lo, hi))
        for rx, g, hg, api in self.STUBS:
            for m in rx.finditer(src):
                short = m.group(g)
                full = c.pick(short, src, m.group(hg) or "" if hg else "")
                if not full and api in ("generated client", "grpc-swift client"):
                    full = c.pick(short + "Service", src, m.group(hg) or "") if short and not short.endswith("Service") else None
                if not full:
                    continue
                if api == "grpc-js client" and not re.search(r"loadPackageDefinition|grpc\.|@grpc", src):
                    continue
                var = self.var_of(src, m.start())
                if not var:
                    if api != "generated client":       # `class GreeterClient(..)` declarations look alike: not counted
                        self.miss("stub_without_variable", f"{file}:{self.s.line_of(file, m.start())}")
                    continue
                stubs.append((var, full, m.start(), api))
        for m in self.TYPED.finditer(src):
            name = m.group(2)
            full = c.pick(name, src) or c.pick(name + "Service", src)
            if not full and "_" in name:         # grpc-swift `Pkg_SvcAsyncClient`
                full = c.pick(name.rsplit("_", 1)[1], src, name.rsplit("_", 1)[0])
            here = self.s.fn_bounds(file, m.start())[0]
            if full and not any(v == m.group(1) and self.s.fn_bounds(file, p)[0] == here for v, _f, p, _a in stubs):
                stubs.append((m.group(1), full, m.start(), "typed stub"))      # one per function / member
        if not stubs and not re.search(r"(?i)stub|client", src):
            return
        resolved = set()
        for var, full, pos, api in stubs:
            self.st["stubs"] += 1
            fn, lo, hi = self.s.fn_bounds(file, pos)
            before = src[max(0, pos - 200):pos]
            member = (fn is None or re.search(rf"(?:self|this)\s*\.\s*{re.escape(var)}\s*(?::[^=\n]*)?=\s*[^=\n]*$", before)
                      or re.search(rf"\b{re.escape(var)}\s*\(\s*$", before) or var.endswith("_")
                      or re.search(rf"\b(?:val|var|let|private|public|final|late)\b[^\n]*\b{re.escape(var)}\s*:", before[-120:])
                      and self.b.nodes.get(fn) is not None and self.b.nodes[fn].kind in ("class",))
            if member or fn is None:
                lo, hi = 0, len(src)
            elif self.b.nodes.get(fn) is not None and self.short(fn) in ("__init__", "constructor", "init", "initState", "new"):
                lo, hi = 0, len(src)
            dollar = r"\$" if lang == "php" else ""
            call = re.compile(r"(?<![\w$])" + dollar + re.escape(var) +
                              r"\s*(?:\??\.|->)\s*(?:async\s*\(\s*\)\s*(?:->|\.)\s*)?(\w+)\s*\(")
            for cm in call.finditer(src, lo, hi):
                mname = re.sub(r"^make(\w+)Call$", r"\1", re.sub(r"^(?:PrepareAsync|Async)(?=[A-Z])", "", cm.group(1)))
                if self.send(full, mname, file, cm.start(1), RESOLVED, api, "stub variable"):
                    resolved.add(cm.start(1))
        # a stub handed to a helper as a parameter: `def get_one(stub, ..): stub.GetFeature(..)`
        if not re.search(r"grpc|Grpc|_pb2|pb\b|Stub|Client", src):
            return
        made = {full for _v, full, _p, _a in stubs}
        for cm in re.finditer(r"(?<![\w$])\$?(\w*(?:stub|Stub|client|Client)\w*)\s*(?:\??\.|->)\s*(\w+)\s*\(", src):
            if cm.start(2) in resolved:
                continue
            mname = re.sub(r"^(?:PrepareAsync|Async)(?=[A-Z])", "", cm.group(2))
            owners = self.method_owner.get(norm(mname)) or set()
            if not owners or len(norm(mname)) < 5:
                continue
            here = owners & made
            if len(here) == 1:
                self.send(next(iter(here)), mname, file, cm.start(2), RESOLVED, "stub parameter", "service of the file's stubs")
            elif len(owners) == 1 and (made or re.search(r"_pb2|grpc", src)):
                self.send(next(iter(owners)), mname, file, cm.start(2), HEURISTIC, "stub parameter", "unique method name")

    @staticmethod
    def var_of(src, pos):
        before = src[max(0, pos - 200):pos]
        m = re.search(r"(?:(?:self|this)\s*\.\s*)?\$?([A-Za-z_]\w*)[ \t]*(?::[ \t]*[^=\n]+?)?[ \t]*(?<![=!<>])=\s*(?:await\s+|try\s+|new\s+|"
                      r"std::move\(\s*)?[\w$.:\\]*$", before)
        if m:
            return m.group(1)
        m = re.search(r"\b([A-Za-z_]\w*)\s*[({]\s*$", before)          # C++ member initializer `stub_(Svc::NewStub(ch))`
        if m and m.group(1) not in ("return", "if", "while", "for", "move", "make_shared", "make_unique", "Some", "Ok"):
            return m.group(1)
        m = re.search(r"\bas\s+(\w+)\s*:?\s*$", src[pos:src.find("\n", pos) if src.find("\n", pos) > 0 else len(src)])
        return m.group(1) if m else None

    # ------------------------------------------------------------ run
    def run(self) -> dict:
        from .sockets import EXTS
        if not self.c.services:
            return {}
        self.declare()
        for file in sorted(self.s.spans):
            if GENERATED.search(file):
                continue
            ext = "." + file.rsplit(".", 1)[-1] if "." in file else ""
            lang = EXTS.get(ext)
            if not lang:
                continue
            src = self.s.text(file)
            if not HINT.search(src):
                continue
            try:      # an unexpected source shape is counted, never fatal
                self.servers(file, lang, src)
                self.clients(file, lang, src)
            except (IndexError, KeyError, ValueError, TypeError, AttributeError) as e:
                self.miss("scan_errors", f"{file}: {type(e).__name__} {e}")
        out = {k: v for k, v in self.st.items() if v}
        out["proto_files"] = self.c.files
        if self.samples:
            out["samples"] = dict(self.samples)
        return out


def apply(project, builder) -> dict:
    """Add the gRPC contract endpoints and their servers / clients; stats (empty without `.proto` services)."""
    return Scan(project, builder).run()

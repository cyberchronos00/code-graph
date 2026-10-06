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
TEST_FILE = re.compile(r"(?:^|/)(?:tests?|__tests__|spec|e2e)/|[._-](?:test|spec)\.[cm]?[jt]sx?$|(?:^|/)test_[^/]*\.py$|"
                       r"_test\.\w+$|Tests?\.\w+$")
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

    EXT = ".proto"

    def _protos(self):
        out = []
        for dp, dns, fns in os.walk(self.root):
            dns[:] = sorted(d for d in dns if d not in SKIP_PARTS and not d.startswith("."))
            for f in fns:
                if f.endswith(self.EXT):
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
        m = self.services[full]["methods"][norm(method)]
        return f"{m.get('owner') or full}/{m['name']}"     # an inherited (Thrift `extends`) method: the declaring service

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
    PROTO, CONTRACT, CONTRACTS = "grpc", "proto", Contracts
    GENERATED, HINT = GENERATED, HINT

    def __init__(self, project, b, sock=None):
        from .sockets import Scan as SockScan
        self.b = b
        self.s = sock or SockScan(project, b)
        self.c = self.CONTRACTS(project.root)
        self.st = defaultdict(int)
        self.samples = defaultdict(list)
        self.method_owner = {}      # norm(method) -> {service full names}
        for full, svc in self.c.services.items():
            for k in svc["methods"]:
                self.method_owner.setdefault(k, set()).add(full)
        self.done = set()
        self._modules = None

    def miss(self, key, text):
        self.st[key] += 1
        if len(self.samples[key]) < 6:
            self.samples[key].append(text[:100])

    # ------------------------------------------------------------ contract endpoints
    def declare(self):
        from .protocols import _endpoint
        for full, svc in sorted(self.c.services.items()):
            own = [m for m in svc["methods"].values() if not m.get("owner")]
            for m in own:
                nid = _endpoint(self.b, self.PROTO, f"{full}/{m['name']}", {
                    "service": full, "package": svc["package"] or None, "method": m["name"], "request": m["request"],
                    "response": m["response"], "streaming": m["streaming"], "declared_in": m["decl"][0],
                    "declared_also": m["decl"][1:] or None, "throws": m.get("throws")})
                self.b.nodes[nid].attrs.setdefault("contract", self.CONTRACT)
            self.st["services"] += 1
            self.st["methods"] += len(own)

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

    def module_of(self, file):
        if self._modules is None:
            self._modules = {}
            for nid, n in self.b.nodes.items():
                if n.kind == "module" and n.file:
                    self._modules.setdefault(n.file, nid)
        return self._modules.get(file)

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
        protocol_receive(self.b, self.PROTO, self.c.endpoint(full, k), method_nid, file, line, conf, library=api, how=how)
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
        top = False
        if fn is None:          # a script's top-level code: its module node, where the plugin has one
            fn = self.module_of(file)
            top = True
        if fn is None:
            self.miss("client_call_outside_function", f"{file}:{self.s.line_of(file, pos)}")
            return False
        line = self.s.line_of(file, pos)
        key = ("s", full, k, fn, line)
        if key in self.done:
            return True
        self.done.add(key)
        n = self.b.nodes.get(fn)
        test = bool(n and is_test_node(n)) or (top and bool(TEST_FILE.search(file)))
        protocol_send(self.b, self.PROTO, self.c.endpoint(full, k), fn, file, line, conf, test=test,
                      role="request", library=api, how=how + (" (module level)" if top else ""))
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
        self.register(file, src, regs)

    def register(self, file, src, regs):
        """Handlers of `(service, offset of the implementation argument, api)` registrations: an object literal, a
        variable holding one, or a class (its methods)."""
        c = self.c
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
                lead = re.match(r"(?:\s*(?://[^\n]*|/\*.*?\*/))*", p, re.S).end()     # leading comments
                off, p = off + lead, p[lead:]
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
                protocol_receive(self.b, self.PROTO, c.endpoint(full, km.group(1)), h, file, line, RESOLVED, library=api,
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

    def find_stubs(self, file, lang, src):
        """[(variable, service, offset, api)] of the client stubs created or typed in `src`."""
        c = self.c
        stubs = []
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
        return stubs

    def clients(self, file, lang, src):
        c = self.c
        stubs = self.find_stubs(file, lang, src)
        if not stubs and not re.search(r"(?i)stub|client", src):
            return
        resolved = set()
        for var, full, pos, api in stubs:
            self.st["stubs"] += 1
            fn, lo, hi = self.s.fn_bounds(file, pos)
            before = src[max(0, pos - 200):pos]
            member = (fn is None or re.search(rf"(?:self|this)\s*\.\s*{re.escape(var)}\s*(?::[^=\n]*)?=\s*[^=\n]*$", before)
                      or re.search(rf"\b{re.escape(var)}\s*\(\s*$", before) or var.endswith("_")
                      or re.search(rf"(?m)^[ \t]*(?:(?:late|final|private|protected|public|static|readonly|const)\s+)*"
                                   rf"[\w.:<>, *&]+?[ \t*&]{re.escape(var)}[ \t]*;", src))   # a field `Type var;`
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
        for file in sorted(self.s.files):
            if self.GENERATED.search(file):
                continue
            ext = "." + file.rsplit(".", 1)[-1] if "." in file else ""
            lang = EXTS.get(ext)
            if not lang:
                continue
            src = self.s.text(file)
            if not self.HINT.search(src):
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


# ---------------------------------------------------------------------------------------------------- Thrift
def _mask_thrift(t: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/|//[^\n]*|#[^\n]*", lambda m: re.sub(r"[^\n]", " ", m.group(0)), t)


class ThriftContracts(Contracts):
    """The project's `.thrift` services, named `<file stem>.<Service>` (the IDL's include prefix: `shared.SharedService`);
    a service that `extends` another one inherits its methods (`owner` = the declaring service)."""
    EXT = ".thrift"
    FN = re.compile(r"(?:^|[,;{]|\))\s*(oneway\s+)?([\w.]+\s*(?:<[^()]*?>)?)\s+(\w+)\s*\(([^()]*)\)"
                    r"(\s*throws\s*\([^()]*\))?")

    def __init__(self, root):
        self.extends = {}
        super().__init__(root)
        for full in list(self.services):
            self._inherit(full, set())

    def _parse(self, rel, text):
        t = _mask_thrift(text)
        mod = os.path.basename(rel)[:-len(self.EXT)]
        for sm in re.finditer(r"(?m)^\s*service\s+(\w+)(?:\s+extends\s+([\w.]+))?\s*\{", t):
            a, b = _block(t, sm.end() - 1)
            full = f"{mod}.{sm.group(1)}"
            line = t.count("\n", 0, sm.start(1)) + 1
            svc = self.services.get(full)
            if svc is None:
                svc = self.services[full] = {"name": sm.group(1), "package": mod, "full": full, "decl": [], "methods": {}}
                self.by_short[sm.group(1)].append(full)
            svc["decl"].append(f"{rel}:{line}")
            if sm.group(2):
                base = sm.group(2) if "." in sm.group(2) else f"{mod}.{sm.group(2)}"
                self.extends[full] = base
            body = t[a + 1:b - 1]
            for fm in self.FN.finditer(body):
                if fm.group(3) == "throws":
                    continue
                m = svc["methods"].setdefault(norm(fm.group(3)), {
                    "name": fm.group(3), "request": ", ".join(x.strip() for x in fm.group(4).split(",") if x.strip()) or None,
                    "response": re.sub(r"\s+", "", fm.group(2)), "streaming": "oneway" if fm.group(1) else "unary",
                    "throws": re.findall(r"(?:\d+\s*:\s*)?([\w.]+)\s+\w+\s*[,;]?", re.sub(r"^\s*throws\s*\(|\)\s*$", "", fm.group(5) or "")) or None, "decl": []})
                m["decl"].append(f"{rel}:{t.count(chr(10), 0, a + 1 + fm.start(3)) + 1}")

    def _inherit(self, full, seen):
        base = self.extends.get(full)
        if not base or base in seen or base not in self.services:
            return
        seen.add(full)
        self._inherit(base, seen)
        for k, m in self.services[base]["methods"].items():
            self.services[full]["methods"].setdefault(k, {**m, "owner": m.get("owner") or base})


class ThriftScan(Scan):
    """Apache Thrift: `endpoint:thrift:<module>.<Service>/<method>` from `.thrift` IDL files.

    server  Python a handler class handed to `Svc.Processor(handler)` (or `class X(Svc.Iface)`), Java / Kotlin
            `implements Svc.Iface` / `Svc.AsyncIface`, C++ `: public SvcIf` / `SvcCobSvIf` (and `SvcProcessor(
            std::make_shared<Handler>())`), Rust `impl SvcSyncHandler for X`, PHP `implements ..\\SvcIf`, Node
            `thrift.createServer(Svc, { method: fn })` / `new Svc.Processor(handler)`
    client  Python `Svc.Client(protocol)`, Java `new Svc.Client(protocol)` (and `Svc.Client client` parameters), C++
            `SvcClient client(protocol)` / `make_shared<SvcClient>(..)`, Rust `SvcSyncClient::new(i, o)` (also through a
            function returning one), PHP / Dart `new ..SvcClient(..)`, Node `thrift.createClient(Svc, connection)`
    """
    PROTO, CONTRACT, CONTRACTS = "thrift", "thrift", ThriftContracts
    GENERATED = re.compile(r"(?:^|/)(?:gen-[\w-]+|gen_[\w-]+)/|_types\.(?:py|js|php|h|cpp)$|ttypes\.py$|_constants\.\w+$")
    HINT = re.compile(r"thrift|Thrift|Processor|Iface|SyncHandler|SyncClient|\bTProtocol|TBinaryProtocol|TCompactProtocol")
    STUBS = [
        (re.compile(r"(?<![\w.])(?:([\w.]+)\.)?(\w+)\.(?:Client|AsyncClient)\s*\("), 2, 1, "thrift client"),
        (re.compile(r"\b(?:([\w:]+)::)?(\w+?)(?:SyncClient|ConcurrentClient|Client)\s*::\s*new\s*\("), 2, 1, "thrift client"),
        (re.compile(r"\bnew\s+\\?((?:\w+\\)*)(\w+?)Client\s*\("), 2, 1, "thrift client"),
        (re.compile(r"\bmake_(?:shared|unique)\s*<\s*(?:([\w:]+)::)?(\w+?)(?:Concurrent)?Client\s*>\s*\("), 2, 1, "thrift client"),
        (re.compile(r"\bcreate(?:Http|XHR|WS|Web)?Client\s*\(\s*()(\w+)\s*,"), 2, 1, "thrift createClient"),
    ]
    TYPED = re.compile(r"\b\$?(\w+)\s*:\s*(?:[\w.]+\.)?(\w+?)(?:SyncClient|Client)\b")

    def find_stubs(self, file, lang, src):
        stubs = super().find_stubs(file, lang, src)
        c = self.c
        # C++ `CalculatorClient client(protocol);`, Java `Calculator.Client client` (declaration or parameter)
        for m in re.finditer(r"\b(?:([\w:]+)::)?(\w+?)(?:Concurrent)?Client\s+(\w+)\s*[({;,)=]", src):
            full = c.pick(m.group(2), src, m.group(1) or "")
            if full and not any(v == m.group(3) for v, *_ in stubs):
                stubs.append((m.group(3), full, m.start(3), "thrift client"))
        for m in re.finditer(r"\b(?:([\w.]+)\.)?(\w+)\.(?:Client|AsyncClient|Iface)\s+(\w+)\s*[({;,)=]", src):
            full = c.pick(m.group(2), src, m.group(1) or "")
            if full and not any(v == m.group(3) for v, *_ in stubs):
                stubs.append((m.group(3), full, m.start(3), "thrift client"))
        # a function returning a client: `fn construct_client(..) -> Result<CalculatorSyncClient<..>>`, then
        # `let mut client = construct_client(..)?`
        for m in re.finditer(r"\bfn\s+(\w+)\s*(?:<[^>]*>)?\s*\([^)]*\)\s*->[^{;]*?\b(\w+?)SyncClient\b", src):
            full = c.pick(m.group(2), src)
            if not full:
                continue
            for am in re.finditer(rf"\b(?:let\s+(?:mut\s+)?)?(\w+)\s*=\s*{re.escape(m.group(1))}\s*\(", src):
                stubs.append((am.group(1), full, am.start(1), "client factory"))
        return stubs

    def servers(self, file, lang, src):
        c = self.c
        api = "thrift"
        if lang == "py":
            for m in re.finditer(r"(?m)^[ \t]*class\s+(\w+)\s*\(([^)]*)\)\s*:", src):
                bm = re.search(r"(?:([\w.]+)\.)?(\w+)\.Iface\b", m.group(2))
                full = bm and c.pick(bm.group(2), src, bm.group(1) or "")
                if full:
                    self.server_class(full, file, src, m.group(1), _py_body(src, m.start(1)), EXACT, api, "Iface base")
            for m in re.finditer(r"\b(?:([\w.]+)\.)?(\w+)\.Processor\s*\(\s*([\w.]+)(\(\s*\))?", src):
                full = c.pick(m.group(2), src, m.group(1) or "")
                cls = self._class_of(src, m.group(3), m.group(4), m.start())
                cm = cls and re.search(rf"(?m)^[ \t]*class\s+{re.escape(cls)}\b", src)
                if full and cm:
                    self.server_class(full, file, src, cls, _py_body(src, cm.start()), RESOLVED, api, "Processor(handler)")
        elif lang == "jvm":
            for m in re.finditer(r"\bclass\s+(\w+)[^{;]*?(?:\bimplements\b|:)\s*([^{;]*?)\{", src):
                for bm in re.finditer(r"(?:([\w.]+)\.)?(\w+)\.(?:Iface|AsyncIface)\b", m.group(2)):
                    full = c.pick(bm.group(2), src, bm.group(1) or "")
                    if full:
                        self.server_class(full, file, src, m.group(1), _block(src, m.end() - 1), EXACT, api, "Iface")
                        break
        elif lang == "c":
            for m in re.finditer(r"\b(?:class|struct)\s+(\w+)(?:\s+final)?\s*:\s*([^{;]*?)\{", src):
                for bm in re.finditer(r"(?:([\w:]+)::)?(\w+?)(?:CobSvIf|If)\b(?!Factory)", m.group(2)):
                    full = c.pick(bm.group(2), src, bm.group(1) or "")
                    if full:
                        self.server_class(full, file, src, m.group(1), _block(src, m.end() - 1), EXACT, api, "If base")
                        break
        elif lang == "rs":
            for m in re.finditer(r"\bimpl(?:\s*<[^{>]*>)?\s+((?:\w+::)*)(\w+?)SyncHandler\s+for\s+(\w+)", src):
                full = c.pick(m.group(2), src, m.group(1))
                if full:
                    self.server_class(full, file, src, m.group(3), _block(src, m.end()), EXACT, api, "SyncHandler impl")
        elif lang == "php":
            for m in re.finditer(r"\bclass\s+(\w+)[^{]*?\bimplements\s+[^{]*?\\?((?:\w+\\)*)(\w+?)If\b", src):
                full = c.pick(m.group(3), src, (m.group(2) or "").replace("\\", "."))
                if full:
                    self.server_class(full, file, src, m.group(1), _block(src, m.end()), EXACT, api, "If interface")
        elif lang == "dart":
            for m in re.finditer(r"\bclass\s+(\w+)[^{]*?\bimplements\s+(?:[\w.]+\.)?(\w+)\b", src):
                full = c.pick(m.group(2), src)
                if full and re.search(r"thrift", src):
                    self.server_class(full, file, src, m.group(1), _block(src, m.end()), EXACT, api, "service interface")
        elif lang == "js":
            regs = []
            for m in re.finditer(r"\bcreate(?:Web|Multiplex)?Server\s*\(\s*([\w$.]+)\s*,\s*", src):
                full = c.pick(m.group(1).split(".")[-1], src)
                if full:
                    regs.append((full, m.end(), "thrift createServer"))
            for m in re.finditer(r"\bnew\s+(?:([\w$.]+)\.)?(\w+)\.Processor\s*\(\s*", src):
                full = c.pick(m.group(2), src, m.group(1) or "")
                if full:
                    regs.append((full, m.end(), "thrift Processor"))
            self.register(file, src, regs)

    def short(self, nid):
        name = super().short(nid)
        n = self.b.nodes.get(nid)
        if n is not None and (n.file or "").endswith(".rs") and name.startswith("handle_"):
            return name[len("handle_"):]                 # Rust `SvcSyncHandler::handle_<method>`
        return name

    @staticmethod
    def _class_of(src, ref, called, before):
        """The class behind `Processor(handler)`: `Handler()` itself, or the last `handler = Handler(..)`."""
        if called:
            return ref.split(".")[-1]
        am = list(re.finditer(rf"\b{re.escape(ref.split('.')[-1])}\s*=\s*([\w.]+)\s*\(", src[:before]))
        return am[-1].group(1).split(".")[-1] if am else None


# ---------------------------------------------------------------------------------------------------- tRPC
JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
TRPC_CALL = ("useQuery|useSuspenseQuery|useInfiniteQuery|useSuspenseInfiniteQuery|useMutation|useSubscription|"
             "usePrefetchQuery|usePrefetchInfiniteQuery|query|mutate|mutation|subscribe|prefetch|prefetchInfinite|fetch|"
             "fetchInfinite|ensureData|queryOptions|infiniteQueryOptions|mutationOptions|subscriptionOptions")
TRPC_CLIENTS = {"api", "trpc", "caller", "serverClient", "helpers", "client", "trpcClient", "serverCaller"}


def _parts(body: str):
    """Top-level `,`-separated parts of an object literal body: [(offset, text)], strings and comments respected."""
    out, depth, start, q, i = [], 0, 0, None, 0
    while i < len(body):
        ch = body[i]
        if q:
            if ch == "\\":
                i += 2
                continue
            if ch == q:
                q = None
        elif ch in "\"'`":
            q = ch
        elif body.startswith("//", i):
            j = body.find("\n", i)
            i = len(body) if j < 0 else j
            continue
        elif body.startswith("/*", i):
            j = body.find("*/", i + 2)
            i = len(body) if j < 0 else j + 2
            continue
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append((start, body[start:i]))
            start = i + 1
        i += 1
    out.append((start, body[start:]))
    res = []
    for o, t in out:      # leading comments dropped (offsets kept pointing into `body`)
        while (cm := re.match(r"\s*(?://[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)", t)):
            o, t = o + cm.end(), t[cm.end():]
        if t.strip():
            res.append((o, t))
    return res


class TrpcScan:
    """tRPC (`endpoint:trpc:<path>`): router objects (`createTRPCRouter({..})`, `router({..})`, `t.router`, plain
    `{..} satisfies TRPCRouterRecord`, `mergeRouters`) whose entries are procedures (`publicProcedure..query(fn)`) or
    other routers mounted under a key; the path of a procedure is the chain of keys from a root router (one no other
    router mounts). The receiver is the resolver (the TypeScript plugin gives an inline resolver its own node). Callers:
    `api.post.hello.useQuery(..)`, `trpc.post.all.queryOptions()`, `client.post.byId.query(..)`, `utils.x.y.fetch()`,
    and server-side callers `api.post.hello(..)`, matched against the known procedure paths only."""
    PROTO = "trpc"

    def __init__(self, project, b, sock):
        self.b, self.s = b, sock
        self.st = defaultdict(int)
        self.samples = defaultdict(list)
        self.routers = defaultdict(list)       # var name -> [router dict]
        self.procvars = defaultdict(list)      # `const get = authedProcedure..query(fn)` -> [proc dict]

    miss = Scan.miss

    def files(self):
        for f in sorted(self.s.files):
            if f.endswith(JS_EXT) and "node_modules/" not in f and not f.endswith(".d.ts"):
                yield f

    # ------------------------------------------------------------ routers
    def scan_routers(self, file, src):
        for m in re.finditer(r"(?:^|[\s;(])(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=\s*"
                             r"(?:([\w.]+)\s*\(\s*)?\{", src):
            call = m.group(2)
            if call and not re.search(r"(?i)router$", call):
                continue
            if self.s.masked(file, m.start(1)):
                continue
            r = self.parse(file, src, m.end() - 1, m.group(1), "")
            if r["procs"] or (r["mounts"] and call):
                self.routers[m.group(1)].append(r)
        for m in re.finditer(r"(?:^|[\s;])(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=\s*"
                             r"(\w*[Pp]rocedure\b|\w+\.procedure\b)", src):
            end = src.find("\n\n", m.end())
            seg = src[m.end():end if end > 0 else len(src)]
            tm = list(re.finditer(r"\.(query|mutation|subscription)\s*\(", seg))
            if tm and not re.search(r"(?m)^(?:export\s+)?(?:const|let|var|function)\s", seg[:tm[-1].start()]):
                nid = f"function:{file}#{m.group(1)}"
                fake = {"name": m.group(1), "file": file, "line": self.s.line_of(file, m.start(1)), "procs": [], "mounts": [],
                        "src": src}
                self.procvars[m.group(1)].append({"kind": tm[-1].group(1), "nid": nid if nid in self.b.nodes else None,
                                                  "line": fake["line"], "conf": RESOLVED, "router": fake, "file": file})
        for m in re.finditer(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:\w+\.)?mergeRouters\s*\(([^)]*)\)", src):
            r = {"name": m.group(1), "file": file, "line": self.s.line_of(file, m.start(1)), "procs": [],
                 "mounts": [("", a.strip()) for a in m.group(2).split(",") if re.fullmatch(r"\s*\w+\s*", a)], "src": src}
            self.routers[m.group(1)].append(r)

    def parse(self, file, src, brace, var, prefix, r=None):
        r = r or {"name": var, "file": file, "line": self.s.line_of(file, brace), "procs": [], "mounts": [], "src": src}
        a, b = _block(src, brace)
        for off, part in _parts(src[a + 1:b - 1]):
            pos = a + 1 + off
            km = re.match(r"\s*(?:['\"](\w+)['\"]|(\w+))\s*(?::\s*([\s\S]*))?$", part)
            if not km:
                continue
            key = km.group(1) or km.group(2)
            val = (km.group(3) or "").strip() if km.group(3) is not None else key
            path = f"{prefix}{key}"
            tm = list(re.finditer(r"\.(query|mutation|subscription)\s*\(", val))
            if re.match(r"\w*[Pp]rocedure\b|\w+\.procedure\b", val) and tm:
                kind = tm[-1].group(1)
                vpos = pos + part.index(val) if val in part else pos
                nid = f"function:{file}#{var}.{path}"
                conf = RESOLVED
                if nid not in self.b.nodes:
                    hm = re.match(r"\s*([\w$.]+)\s*\)", val[tm[-1].end():])
                    nid = self.s.handler(hm.group(1), file) if hm else None
                r["procs"].append({"path": path, "kind": kind, "nid": nid, "line": self.s.line_of(file, vpos), "conf": conf})
            elif re.fullmatch(r"[\w$]+", val):
                r["mounts"].append((path, val))
            elif (nm := re.match(r"(?:[\w.]*[Rr]outer\s*\(\s*)?\{", val)):
                vpos = pos + part.index(val)
                self.parse(file, src, vpos + nm.end() - 1, var, path + ".", r)
            elif re.match(r"lazy\s*\(", val):
                self.miss("lazy_router_unresolved", f"{file}:{self.s.line_of(file, pos)} {path}")
        return r

    def resolve(self, mounter, name, reg=None):
        reg = self.routers if reg is None else reg
        cands = reg.get(name) or []
        im = None
        if not cands:           # `import { eventTypesRouter as heavyEventTypesRouter } from "./eventTypes/heavy/_router"`
            im = re.search(rf"import\s*(?:type\s*)?\{{[^}}]*\b(\w+)\s+as\s+{re.escape(name)}\b[^}}]*\}}\s*from\s*"
                           rf"['\"]([^'\"]+)['\"]", mounter["src"])
            if not im:
                return []
            cands = reg.get(im.group(1)) or []
            spec = im.group(2)
        if len(cands) <= 1:
            return cands
        if im is None:
            im = re.search(rf"import\s*(?:type\s*)?\{{[^}}]*\b{re.escape(name)}\b[^}}]*\}}\s*from\s*['\"]([^'\"]+)['\"]",
                           mounter["src"])
            if not im:
                same = [c for c in cands if c["file"] == mounter["file"]]
                return same or cands
            spec = im.group(1)
        if spec.startswith("."):
            spec = os.path.normpath(os.path.join(os.path.dirname(mounter["file"]), spec))
        else:
            spec = re.sub(r"^(?:~|@)/", "", spec)
        got = []
        for c in cands:
            f = re.sub(r"\.\w+$", "", c["file"])
            f2 = re.sub(r"/index$", "", f)
            if any(x == spec or x.endswith("/" + spec) for x in (f, f2)):
                got.append(c)
        return got or cands

    def paths(self):
        """{full path: [(proc, router)]} from every root router."""
        for name in list(self.routers):         # `createRouter({ routeTree, .. })` of other libraries: no procedure,
            keep = [r for r in self.routers[name]     # no known router mounted
                    if r["procs"] or any(self.routers.get(n) for _p, n in r["mounts"] if n != name)]
            if keep:
                self.routers[name] = keep
            else:
                del self.routers[name]
        mounted = set()
        for rs in self.routers.values():
            for r in rs:
                for _p, name in r["mounts"]:
                    for c in self.resolve(r, name):
                        mounted.add(id(c))
        out = defaultdict(list)

        def walk(r, prefix, seen, root):
            if id(r) in seen:
                return
            seen = seen | {id(r)}
            for p in r["procs"]:
                out[prefix + p["path"]].append(({**p, "root": root}, r))
            for path, name in r["mounts"]:
                cs = self.resolve(r, name)
                if not cs and (ps := self.resolve(r, name, self.procvars)):
                    for p in ps:        # `get,` -> `export const get = authedProcedure..query(..)` in another file
                        out[prefix + path].append(({**p, "path": path, "root": root}, p["router"]))
                    continue
                if not cs:
                    self.miss("mount_unresolved", f"{r['file']}:{r['line']} {path}: {name}")
                for c in cs:
                    walk(c, prefix + (path + "." if path else ""), seen, root)
        for rs in self.routers.values():
            for r in rs:
                if id(r) not in mounted:      # a root router (the app's, or one nothing mounts: its paths are partial)
                    walk(r, "", frozenset(), r["name"])
        return out

    # ------------------------------------------------------------ run
    def run(self) -> dict:
        from .protocols import _endpoint, protocol_receive, protocol_send
        from .tests_index import is_test_node
        srcs = {}
        for f in self.files():
            t = self.s.text(f)
            if re.search(r"[Pp]rocedure\b|[Rr]outer\s*\(|mergeRouters", t):
                srcs[f] = t
                try:
                    self.scan_routers(f, t)
                except (IndexError, KeyError, ValueError, TypeError, AttributeError) as e:
                    self.miss("scan_errors", f"{f}: {type(e).__name__} {e}")
        if not self.routers:
            return {}
        procs = self.paths()
        if not self.routers:
            return {}
        for path, lst in sorted(procs.items()):
            p0, r0 = lst[0]
            _endpoint(self.b, self.PROTO, path, {"procedure": p0["kind"], "router": r0["name"], "root": p0.get("root"),
                                                 "declared_in": f"{r0['file']}:{p0['line']}",
                                                 "declared_also": [f"{r['file']}:{p['line']}" for p, r in lst[1:]][:10] or None})
            self.st["procedures"] += 1
            for p, r in lst:
                if p["nid"]:
                    protocol_receive(self.b, self.PROTO, path, p["nid"], r["file"], p["line"], p["conf"], library="trpc",
                                     how="router procedure")
                    self.st["resolvers"] += 1
                else:
                    self.miss("resolver_without_node", f"{r['file']}:{p['line']} {path}")
        self.st["routers"] = sum(len(v) for v in self.routers.values())
        call = re.compile(rf"(?<![\w$.])([A-Za-z_$][\w$]*)((?:\s*\??\.\s*[A-Za-z_$][\w$]*)+?)\s*\??\.\s*({TRPC_CALL})\s*(?:<[^()]*?>)?\s*\(")
        direct = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)((?:\s*\.\s*[A-Za-z_$][\w$]*)+)\s*\(")
        done = set()
        for f in self.files():
            t = srcs.get(f) or self.s.text(f)
            if not re.search(r"(?i)trpc|\bapi\b", t):
                continue
            callers = set(TRPC_CLIENTS) | {m.group(1) for m in re.finditer(
                r"(?:const|let|var)\s+(\w+)\s*=\s*(?:await\s+)?(?:create(?:TRPC\w*|Caller\w*)|\w*[Cc]aller)\s*[(<]", t)}
            hits = []
            for m in call.finditer(t):
                path = re.sub(r"[\s?]", "", m.group(2)).lstrip(".")
                if path in procs:
                    hits.append((m.start(2), path, m.group(3)))
            for m in direct.finditer(t):
                path = re.sub(r"\s", "", m.group(2)).lstrip(".")
                if m.group(1) in callers and path in procs:
                    hits.append((m.start(2), path, "call"))
            for pos, path, how in hits:
                if self.s.masked(f, pos):
                    continue
                fn, _lo, _hi = self.s.fn_bounds(f, pos)
                top = fn is None
                if top:
                    fn = Scan.module_of(self, f)
                if fn is None:
                    self.miss("client_call_outside_function", f"{f}:{self.s.line_of(f, pos)}")
                    continue
                line = self.s.line_of(f, pos)
                if (fn, path, line) in done:
                    continue
                done.add((fn, path, line))
                n = self.b.nodes.get(fn)
                test = bool(n and is_test_node(n)) or (top and bool(TEST_FILE.search(f)))
                protocol_send(self.b, self.PROTO, path, fn, f, line, RESOLVED, test=test, role="request",
                              library="trpc", how=how)
                self.st["client_calls"] += 1
        out = {k: v for k, v in self.st.items() if v}
        if self.samples:
            out["samples"] = dict(self.samples)
        return out

    _modules = None


# ---------------------------------------------------------------------------------------------------- JSON-RPC
JSONRPC_HINT = re.compile(r"(?i)json-?rpc|jayson|jsonrpsee")
JR_NAME = r"[\w./:$-]+"


class JsonRpcScan:
    """JSON-RPC 2.0 methods by name (`endpoint:jsonrpc:<method>`), in files that name a JSON-RPC library or payload.

    server  jayson `new jayson.Server({ add: fn })`, json-rpc-2.0 `server.addMethod("echo", fn)`, vscode-jsonrpc
            `connection.onRequest("x", fn)` / `onNotification`, Python jsonrpcserver `@method` (`@method(name=..)`),
            json-rpc `@dispatcher.add_method`, Flask-JSONRPC `@jsonrpc.method("App.x")`, Rust jsonrpsee
            `#[method(name = "x")]` in an `#[rpc(server)]` trait (the `impl XServer for T` method) and
            `module.register_method("x", |..| ..)` (the registering function: the closure has no node, heuristic)
    client  `client.request("x", ..)` / `call` / `notify` / `sendRequest` / `sendNotification`, jsonrpcclient
            `request("x")`, and request payloads `{"jsonrpc": "2.0", "method": "x"}` in any language (an external API)
    """
    PROTO = "jsonrpc"

    def __init__(self, project, b, sock):
        self.b, self.s = b, sock
        self.st = defaultdict(int)
        self.samples = defaultdict(list)
        self.done = set()

    miss = Scan.miss
    _modules = None

    def recv(self, name, nid, file, line, conf, api, how):
        from .protocols import protocol_receive
        if not nid or ("r", name, nid) in self.done:
            return
        self.done.add(("r", name, nid))
        protocol_receive(self.b, self.PROTO, name, nid, file, line, conf, library=api, how=how)
        self.st["server_methods"] += 1

    def send(self, name, file, pos, conf, api, how):
        from .protocols import protocol_send
        from .tests_index import is_test_node
        if self.s.masked(file, pos):
            return
        fn, _lo, _hi = self.s.fn_bounds(file, pos)
        top = fn is None
        if top:
            fn = Scan.module_of(self, file)
        if fn is None:
            self.miss("client_call_outside_function", f"{file}:{self.s.line_of(file, pos)}")
            return
        line = self.s.line_of(file, pos)
        if ("s", name, fn, line) in self.done:
            return
        self.done.add(("s", name, fn, line))
        n = self.b.nodes.get(fn)
        test = bool(n and is_test_node(n)) or (top and bool(TEST_FILE.search(file)))
        protocol_send(self.b, self.PROTO, name, fn, file, line, conf, test=test, role="request", library=api, how=how)
        self.st["client_calls"] += 1

    def at_line(self, file, line):
        for ln, _end, nid in self.s.spans.get(file, ()):
            if ln == line:
                return nid
        return None

    def handler_at(self, file, src, pos, name):
        """The handler argument starting at `pos`: a named function, else an inline function's node on that line."""
        rest = src[pos:pos + 200]
        im = re.match(r"\s*([\w$.]+)\s*[,)]", rest)
        if im:
            return self.s.handler(im.group(1), file), RESOLVED
        nid = self.at_line(file, self.s.line_of(file, pos))
        return nid, RESOLVED

    def run(self) -> dict:
        from .sockets import EXTS
        for file in sorted(self.s.files):
            ext = "." + file.rsplit(".", 1)[-1] if "." in file else ""
            lang = EXTS.get(ext)
            if not lang or "node_modules/" in file:
                continue
            src = self.s.text(file)
            if not JSONRPC_HINT.search(src) or re.search(r"modelcontextprotocol|\b(?:fast)?mcp(?:_\w+)?\b", src):
                continue           # MCP speaks JSON-RPC too: its own endpoints (ai tools)
            try:
                self.scan(file, lang, src)
            except (IndexError, KeyError, ValueError, TypeError, AttributeError) as e:
                self.miss("scan_errors", f"{file}: {type(e).__name__} {e}")
        out = {k: v for k, v in self.st.items() if v}
        if self.samples:
            out["samples"] = dict(self.samples)
        return out

    def scan(self, file, lang, src):
        if lang == "js":
            maps = [m.end() - 1 for m in re.finditer(r"(?:new\s+)?jayson\.(?:server|Server)\s*\(\s*\{", src)]
            for m in re.finditer(r"(?:new\s+)?jayson\.(?:server|Server)\s*\(\s*([\w$]+)\s*[,)]", src):
                dm = re.search(rf"(?:const|let|var)\s+{re.escape(m.group(1))}\s*(?::[^=\n]+)?=\s*\{{", src)
                if dm:
                    maps.append(dm.end() - 1)
            for brace in maps:
                a, b = _block(src, brace)
                for off, part in _parts(src[a + 1:b - 1]):
                    km = re.match(r"\s*(?:async\s+)?['\"]?([\w./:$-]+)['\"]?\s*(:\s*([\s\S]*)|\()", part)
                    if not km:
                        continue
                    pos = a + 1 + off + km.start(1)
                    val = (km.group(3) or "").strip()
                    nid = self.s.handler(val, file) if val and re.fullmatch(r"[\w$.]+", val) else \
                        self.at_line(file, self.s.line_of(file, pos))
                    if nid:
                        self.recv(km.group(1), nid, file, self.s.line_of(file, pos), RESOLVED, "jayson", "method map")
                    else:
                        self.miss("handler_unresolved", f"{file}:{self.s.line_of(file, pos)} {km.group(1)}")
            for m in re.finditer(rf"\.(addMethod(?:Advanced)?|on(?:Request|Notification))\s*\(\s*(['\"])({JR_NAME})\2\s*,", src):
                nid, conf = self.handler_at(file, src, m.end(), m.group(3))
                line = self.s.line_of(file, m.start())
                if nid:
                    self.recv(m.group(3), nid, file, line, conf, "json-rpc-2.0" if "add" in m.group(1) else "vscode-jsonrpc",
                              m.group(1))
                else:
                    self.miss("handler_unresolved", f"{file}:{line} {m.group(3)}")
        elif lang == "py":
            for m in re.finditer(r"(?m)^[ \t]*@(?:(?:\w+\.)?(?:method|add_method))(?:\s*\(\s*(?:name\s*=\s*)?(?:['\"]([^'\"]+)['\"])?[^)]*\))?"
                                 r"\s*\n(?:[ \t]*@[^\n]*\n)*[ \t]*(?:async\s+)?def\s+(\w+)", src):
                if not re.search(r"jsonrpc|json_rpc|dispatcher", src):
                    break
                name = m.group(1) or m.group(2)
                line = self.s.line_of(file, m.start(2))
                nid = self.at_line(file, line) or self.s.handler(m.group(2), file)
                if nid:
                    self.recv(name, nid, file, line, RESOLVED, "jsonrpcserver", "@method")
        elif lang == "rs":
            self.rust(file, src)
        # clients
        for m in re.finditer(rf"\.(request|call|notify|notification|sendRequest|sendNotification)\s*(?:::<[^>]*>)?\s*\(\s*"
                             rf"(['\"])({JR_NAME})\2", src):
            if lang == "py" and m.group(1) == "request" and "jsonrpc" not in src.lower():
                continue
            self.send(m.group(3), file, m.start(3), RESOLVED, "json-rpc client", m.group(1))
        if "jsonrpcclient" in src:
            for m in re.finditer(rf"(?<![\w.])(?:request|notification)(?:_json|_uuid|_random)?\s*\(\s*(['\"])({JR_NAME})\1", src):
                self.send(m.group(2), file, m.start(2), RESOLVED, "jsonrpcclient", "request()")
        for m in re.finditer(r"['\"]?\bjsonrpc['\"]?\s*(?::|=>|=)\s*['\"]2\.0['\"]", src):
            w0 = max(0, m.start() - 300)
            win = src[w0:m.end() + 300]
            mm = [x for x in re.finditer(rf"['\"]?\bmethod['\"]?\s*(?::|=>|=)\s*['\"]({JR_NAME})['\"]", win)]
            if mm:
                x = min(mm, key=lambda x: abs(w0 + x.start() - m.start()))
                self.send(x.group(1), file, w0 + x.start(1), RESOLVED, "payload", "request payload")

    def rust(self, file, src):
        for m in re.finditer(r"#\[rpc\((.*?)\)\]\s*(?:pub(?:\([\w ]+\))?\s+)?trait\s+(\w+)[^{]*\{", src, re.S):
            ns = re.search(r"namespace\s*=\s*\"([^\"]+)\"", m.group(1))
            a, b = _block(src, m.end() - 1)
            names = {}
            for mm in re.finditer(r"#\[(?:method|subscription)\(\s*name\s*=\s*\"([^\"]+)\"[^\]]*\]\s*(?:async\s+)?fn\s+(\w+)", src[a:b]):
                names[mm.group(2)] = (f"{ns.group(1)}_{mm.group(1)}" if ns else mm.group(1))
            if not names:
                continue
            self.st["rpc_traits"] += 1
            trait = m.group(2)
            # servers: `impl TraitServer for T { async fn say_hello(..) }` anywhere in the project
            for f in sorted(self.s.files):
                if not f.endswith(".rs"):
                    continue
                t = self.s.text(f)
                if f != file and re.search(rf"\btrait\s+{re.escape(trait)}\b", t):
                    continue        # that file declares its own trait of the same name
                for im in re.finditer(rf"\bimpl(?:\s*<[^{{]*?>)?\s+(?:\w+::)*{re.escape(trait)}Server\s*(?:<[^{{]*?>)?\s+for\s+(\w+)[^{{]*\{{", t):
                    lo, hi = _block(t, im.end() - 1)
                    l1, l2 = self.s.line_of(f, lo), self.s.line_of(f, hi - 1)
                    for ln, _e, nid in self.s.spans.get(f, ()):
                        if l1 <= ln <= l2:
                            short = Scan.short(self, nid)
                            if short in names:
                                self.recv(names[short], nid, f, ln, EXACT, "jsonrpsee", "#[rpc] trait impl")
                # clients: calls of the generated `TraitClient` methods in files that use it
                if re.search(rf"\b{re.escape(trait)}Client\b", t):
                    for cm in re.finditer(r"\.\s*(\w+)\s*\(", t):
                        if cm.group(1) in names and not re.search(r"fn\s+$", t[max(0, cm.start() - 10):cm.start()]):
                            self.send(names[cm.group(1)], f, cm.start(1), HEURISTIC, "jsonrpsee", "generated client method")
        for m in re.finditer(rf"\.register_(?:async_)?(?:method|subscription|blocking_method)\s*\(\s*\"({JR_NAME})\"", src):
            fn, _lo, _hi = self.s.fn_bounds(file, m.start())
            if fn:      # the closure has no node: the function that registers it
                self.recv(m.group(1), fn, file, self.s.line_of(file, m.start()), HEURISTIC, "jsonrpsee",
                          "registering function (inline closure)")


def apply(project, builder) -> dict:
    """Add the gRPC and Thrift contract endpoints and their servers / clients; stats per protocol (empty without
    contracts)."""
    out = {}
    g = Scan(project, builder)
    if (st := g.run()):
        out["grpc"] = st
    if (st := ThriftScan(project, builder, sock=g.s).run()):
        out["thrift"] = st
    if (st := TrpcScan(project, builder, g.s).run()):
        out["trpc"] = st
    if (st := JsonRpcScan(project, builder, g.s).run()):
        out["jsonrpc"] = st
    return out

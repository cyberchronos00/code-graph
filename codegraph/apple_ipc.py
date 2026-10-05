"""Apple local IPC (#38 part 3): XPC services and Darwin notifications in Swift.

  xpc                  endpoint:xpc:<service>          `NSXPCConnection(machServiceName: "x")` /
                                                       `NSXPCConnection(serviceName: "x")` connect (role connect);
                                                       `NSXPCListener(machServiceName: "x")` listens: the listener
                                                       delegate's `listener(_:shouldAcceptNewConnection:)` receives.
                       endpoint:xpc:<Protocol>.<method>  methods of an in-repo `@objc protocol` used with
                                                       `NSXPCInterface(with: P.self)`: the exported object (a class
                                                       adopting P, in a file that exports it) receives; calls of the
                                                       methods on a proxy in files that name P and a
                                                       `remoteObjectProxy..` send.
  darwin-notification  endpoint:darwin-notification:<name>  `CFNotificationCenterPostNotification(center, name, ..)`
                                                       -> `CFNotificationCenterAddObserver(center, observer, callback,
                                                       name, ..)` on the Darwin notify center; names from literals
                                                       (`"x" as CFString`, `CFNotificationName("x" as CFString)`) or
                                                       constants. Fan-out.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .brokers import Scan, _args
from .core.model import EXACT, HEURISTIC, RESOLVED
from .protocols import protocol_receive, protocol_send

CONNECT = re.compile(r"\bNSXPCConnection\s*\(\s*(machServiceName|serviceName)\s*:")
LISTEN = re.compile(r"\bNSXPCListener\s*\(\s*machServiceName\s*:")
IFACE = re.compile(r"\bNSXPCInterface\s*\(\s*with\s*:\s*([A-Za-z_]\w*)\s*\.\s*self\s*\)")
POST = re.compile(r"\bCFNotificationCenterPostNotification\s*\(")
OBSERVE = re.compile(r"\bCFNotificationCenterAddObserver\s*\(")


class AppleIpc(Scan):
    def run(self) -> dict:
        files = [f for f in sorted(self.s.files) if f.endswith(".swift")]
        texts = {f: self.s.text(f) or "" for f in files}
        texts = {f: t for f, t in texts.items() if "NSXPC" in t or "CFNotificationCenter" in t}
        if not texts:
            return {}
        self.services(texts)
        self.interfaces(texts)
        self.darwin(texts)
        out = {k: v for k, v in self.st.items() if v}
        if out and self.samples:
            out["samples"] = dict(self.samples)
        return out

    def name_arg(self, f, pos, expr):
        e = (expr or "").strip()
        e = re.sub(r"^(?:CFNotificationName|NSNotification\.Name|Notification\.Name)\s*\(\s*(?:rawValue\s*:\s*)?([\s\S]+)\)$", r"\1", e)
        e = re.sub(r"\s+as\s+CFString\s*$", "", e.strip()).strip()
        m = re.fullmatch(r'"([^"\\]+)"', e)
        if m:
            return m.group(1), EXACT
        if re.fullmatch(r"[A-Za-z_][\w.]*", e):
            for nid in (f"constant:{e}",) + tuple(k for k in self.b.nodes if k.endswith("." + e) and k.startswith("constant:")):
                n = self.b.nodes.get(nid)
                if n is not None and n.file:
                    line = (self.s.text(n.file) or "").split("\n")[(n.line or 1) - 1]
                    v = re.search(rf'\b{re.escape(e.split(".")[-1])}\b[^=]*=\s*"([^"\\]+)"', line)
                    if v:
                        return v.group(1), RESOLVED
            v, conf = self.value(f, pos, e)
            if v and re.fullmatch(r"[\w.\-]+", v):
                return v, conf or RESOLVED
        return None, None

    def cls_at(self, f, pos):
        fn = self.fn_at(f, pos)
        if fn and fn.startswith("method:"):
            return fn.split(":", 1)[1].rsplit(".", 1)[0]
        return None

    # ---------------------------------------------------------------- XPC services
    def services(self, texts):
        for f, src in texts.items():
            for m in CONNECT.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                args = _args(src, src.find("(", m.start()))
                name, conf = self.name_arg(f, m.start(), args[0].split(":", 1)[1] if args and ":" in args[0] else "")
                fn = self.fn_at(f, m.start())
                if not name or not fn:
                    self.miss("xpc_service_unknown", f"{f}:{self.s.line_of(f, m.start())}")
                    continue
                protocol_send(self.b, "xpc", name, fn, f, self.s.line_of(f, m.start()), conf,
                              test=self.is_test(f, fn), role="connect", how=f"NSXPCConnection({m.group(1)}:)")
                self.st["connections"] += 1
            for m in LISTEN.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                args = _args(src, src.find("(", m.start()))
                name, conf = self.name_arg(f, m.start(), args[0].split(":", 1)[1] if args else "")
                if not name:
                    self.miss("xpc_service_unknown", f"{f}:{self.s.line_of(f, m.start())}")
                    continue
                cls = self.cls_at(f, m.start())
                h = f"method:{cls}.listener" if cls and f"method:{cls}.listener" in self.b.nodes else self.fn_at(f, m.start())
                if h and not self.is_test(f, h):
                    protocol_receive(self.b, "xpc", name, h, f, self.s.line_of(f, m.start()), conf, how="NSXPCListener")
                    self.st["listeners"] += 1

    # ---------------------------------------------------------------- XPC interfaces
    def interfaces(self, texts):
        used = defaultdict(set)                     # protocol name -> files using NSXPCInterface(with: P.self)
        for f, src in texts.items():
            for m in IFACE.finditer(src):
                if not self.s.masked(f, m.start()):
                    used[m.group(1)].add(f)
        for proto, files in sorted(used.items()):
            pnode = self.b.nodes.get(f"class:{proto}")
            if pnode is None or not pnode.file:
                continue                            # a protocol from a dependency
            methods = sorted({nid.split(".", 1)[1] for nid in self.b.nodes
                              if nid.startswith(f"method:{proto}.") and self.b.nodes[nid].file == pnode.file})
            if not methods:
                continue
            self.st["xpc_protocols"] += 1
            impls = set()
            for f in files:                          # exported objects: classes adopting P in an exporting file
                src = texts[f]
                if "exportedInterface" not in src:
                    continue
                for c in re.finditer(rf"\bclass\s+(\w+)\s*:[^{{]*\b{re.escape(proto)}\b", src):
                    impls.add(c.group(1))
            for cls in sorted(impls):
                for meth in methods:
                    h = f"method:{cls}.{meth}"
                    if h in self.b.nodes and not self.is_test(self.b.nodes[h].file or "", h):
                        n = self.b.nodes[h]
                        protocol_receive(self.b, "xpc", f"{proto}.{meth}", h, n.file, n.line, EXACT, how="exported object")
                        self.st["xpc_methods"] += 1
            mrx = re.compile(r"(?<![\w$])([\w$]+(?:\(\s*\))?)\s*[?!]?\s*\.\s*(" + "|".join(map(re.escape, methods)) + r")\s*[({]")
            for f, src in texts.items():
                if proto not in src or "remoteObjectProxy" not in src and "synchronousRemoteObjectProxy" not in src:
                    continue
                for m in mrx.finditer(src):
                    if self.s.masked(f, m.start()):
                        continue
                    if self.cls_at(f, m.start()) in impls:
                        continue
                    fn = self.fn_at(f, m.start())
                    if fn is None or fn == f"method:{proto}.{m.group(2)}":
                        continue
                    recv = m.group(1)
                    if recv in ("self", "super", "Self"):
                        continue                    # a local wrapper of the same name
                    conf = RESOLVED if re.search(rf"\b{re.escape(recv.rstrip('()'))}\b[^\n]*(?:as\??\s*{re.escape(proto)}|->\s*{re.escape(proto)}\??)", src) else HEURISTIC
                    protocol_send(self.b, "xpc", f"{proto}.{m.group(2)}", fn, f, self.s.line_of(f, m.start()), conf,
                                  test=self.is_test(f, fn), how="XPC proxy call")
                    self.st["xpc_calls"] += 1

    # ---------------------------------------------------------------- Darwin notifications
    def darwin(self, texts):
        for f, src in texts.items():
            if "CFNotificationCenter" not in src:
                continue
            for rx, idx, role in ((POST, 1, "send"), (OBSERVE, 3, "recv")):
                for m in rx.finditer(src):
                    if self.s.masked(f, m.start()):
                        continue
                    args = _args(src, m.end() - 1)
                    if len(args) <= idx:
                        continue
                    name, conf = self.name_arg(f, m.start(), args[idx])
                    fn = self.fn_at(f, m.start())
                    line = self.s.line_of(f, m.start())
                    if not name or not fn:
                        self.miss("darwin_name_unknown", f"{f}:{line} {args[idx][:50]}")
                        continue
                    if role == "send":
                        protocol_send(self.b, "darwin-notification", name, fn, f, line, conf, test=self.is_test(f, fn),
                                      how="CFNotificationCenterPostNotification")
                        self.st["darwin_posts"] += 1
                    elif not self.is_test(f, fn):
                        protocol_receive(self.b, "darwin-notification", name, fn, f, line, conf,
                                         how="CFNotificationCenterAddObserver")
                        self.st["darwin_observers"] += 1


def apply(project, builder, sock=None) -> dict:
    """XPC services and Darwin notifications (#38 part 3)."""
    return AppleIpc(project, builder, sock).run()

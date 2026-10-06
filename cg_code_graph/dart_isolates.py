"""Dart isolates (#38 part 3): an isolate started on an in-repo entry function as `endpoint:isolate:<entry>`.

  isolate  endpoint:isolate:<file>#<entry>      `Isolate.spawn(entry, msg)`, `Isolate.run(entry)` /
                                                `Isolate.run(() => entry(..))`, Flutter `compute(entry, msg)` send
                                                (role spawn) from the function that starts it; `Isolate.spawnUri(
                                                Uri.file('bin/x.dart') / Uri.parse(..), ..)` names that file's `main`.
                                                The entry function receives.
           endpoint:isolate:<entry>:out         the entry's `port.send(..)` / `Isolate.exit(port, result)` on a
                                                `SendPort` parameter -> the spawner's port listener: the
                                                `ReceivePort` / `RawReceivePort` whose `.sendPort` the spawn passes,
                                                `port.listen(h)`, `port.handler = h`, `RawReceivePort(h)`,
                                                `await for (.. in port)`, `port.first` and other stream reads,
                                                `StreamQueue(port)`. Entries in test code are skipped.

Messages into a running isolate (the entry sends its own `SendPort` back and the parent sends on it) are not
followed: the port travels as a message.
"""
from __future__ import annotations

import re

from .brokers import Scan, _args
from .core.model import EXACT, RESOLVED
from .protocols import protocol_receive, protocol_send

SPAWN = re.compile(r"\b(?:Isolate\s*\.\s*(spawn|run|spawnUri)|(compute))\s*(?:<[^()]*?>)?\s*\(")


class Isolates(Scan):
    def run(self) -> dict:
        files = [f for f in sorted(self.s.files) if f.endswith(".dart")]
        for f in files:
            src = self.s.text(f) or ""
            if "Isolate" in src or "compute(" in src:
                self.scan(f, src)
        return {k: v for k, v in self.st.items() if v}

    def entry(self, f, pos, name):
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?", name):
            return None
        fn = self.fn_at(f, pos)
        n = self.b.nodes.get(fn)
        if n is not None and n.kind == "method" and "." not in name:     # a static method of the same class
            cls = fn.split("#", 1)[1].rsplit(".", 1)[0]
            if f"method:{f}#{cls}.{name}" in self.b.nodes:
                return f"method:{f}#{cls}.{name}"
        for nid in (f"function:{f}#{name}", f"method:{f}#{name}"):
            if nid in self.b.nodes:
                return nid
        h = self.s.handler(name, f)
        return h if h and self.b.nodes[h].file and (self.b.nodes[h].file == f or self.imported(f, name.split(".")[-1])) \
            else None

    def scan(self, f, src):
        if "compute(" in src and not re.search(r"""import\s+['"]package:flutter/foundation\.dart['"]""", src):
            src_compute = False
        else:
            src_compute = True
        for m in SPAWN.finditer(src):
            if self.s.masked(f, m.start()) or (m.group(2) and not src_compute):
                continue
            if re.search(r"(?:\bFuture<[^>]*>|\bvoid|\bstatic)\s*$", src[max(0, m.start() - 30):m.start()]):
                continue                                      # a declaration named compute(..)
            kind = m.group(1) or "compute"
            args = _args(src, m.end() - 1)
            if not args:
                continue
            fn = self.fn_at(f, m.start())
            line = self.s.line_of(f, m.start())
            if kind == "spawnUri":
                u = re.fullmatch(r"""Uri\s*\.\s*(?:file|parse)\s*\(\s*(['"])([^'"]+)\1\s*\)""", args[0].strip())
                target = u.group(2).lstrip("./") if u else None
                h = f"function:{target}#main" if target and f"function:{target}#main" in self.b.nodes else None
                how, conf = "Isolate.spawnUri", RESOLVED
            else:
                a0 = args[0].strip()
                lam = re.fullmatch(r"\(\s*\)\s*(?:async\s*)?=>\s*([\w$.]+)\s*\([\s\S]*\)|\(\s*\)\s*(?:async\s*)?\{\s*return\s+([\w$.]+)\s*\([\s\S]*\)\s*;\s*\}", a0)
                name = (lam.group(1) or lam.group(2)) if lam else a0
                h = self.entry(f, m.start(), name)
                how, conf = f"Isolate.{kind}" if kind != "compute" else "compute", EXACT
            if h is None or fn is None:
                self.miss("isolate_entry_unresolved", f"{f}:{line} {args[0][:60]}")
                continue
            if self.is_test(self.b.nodes[h].file or f, h):
                self.st["test_isolates"] += 1           # a test's own isolate: not part of the application
                continue
            ep = h.split(":", 1)[1]
            protocol_send(self.b, "isolate", ep, fn, f, line, conf, test=self.is_test(f, fn), role="spawn", how=how)
            self.st["spawns"] += 1
            if ("r", ep) not in self.done:
                self.done.add(("r", ep))
                hn = self.b.nodes[h]
                protocol_receive(self.b, "isolate", ep, h, hn.file, hn.line, EXACT, how="isolate entry")
                self.st["entries"] += 1
                self.entry_sends(hn, ep)
            # the spawner's port: `X.sendPort` among the arguments
            for a in args[1:]:
                p = re.search(r"([\w$]+)\s*\.\s*sendPort\b", a)
                if p:
                    self.port_listeners(f, src, fn, p.group(1), ep)

    def entry_sends(self, hn, ep):
        src = self.s.text(hn.file) or ""
        lines = src.split("\n")
        lo = sum(len(x) + 1 for x in lines[:(hn.line or 1) - 1])
        hi = sum(len(x) + 1 for x in lines[:(hn.end_line or hn.line or 1)])
        body = src[lo:hi]
        params = re.search(r"\(([^()]*)\)", body)
        ports = re.findall(r"\bSendPort\??\s+([\w$]+)", params.group(1)) if params else []
        for p in ports:
            for m in re.finditer(rf"(?<![\w$.]){re.escape(p)}\s*[?!]?\s*\.\s*send\s*\(|\bIsolate\s*\.\s*exit\s*\(\s*{re.escape(p)}\b",
                                 body):
                pos = lo + m.start()
                if self.s.masked(hn.file, pos):
                    continue
                fn = self.fn_at(hn.file, pos)
                protocol_send(self.b, "isolate", ep + ":out", fn, hn.file, self.s.line_of(hn.file, pos), EXACT,
                              test=self.is_test(hn.file, fn), how="Isolate.exit" if "exit" in m.group(0) else "SendPort.send")
                self.st["entry_sends"] += 1

    def port_listeners(self, f, src, fn, port, ep):
        _fn, lo, hi = self.s.fn_bounds(f, src.find(port, 0))
        n = self.b.nodes.get(fn)
        lines = src.split("\n")
        lo = sum(len(x) + 1 for x in lines[:(n.line or 1) - 1]) if n else 0
        hi = sum(len(x) + 1 for x in lines[:(n.end_line or n.line or 1)]) if n else len(src)
        body = src[lo:hi]
        p = re.escape(port)
        rxs = ((rf"(?<![\w$.]){p}\s*\.\s*listen\s*\(\s*([\w$.]+)?", "ReceivePort.listen"),
               (rf"(?<![\w$.]){p}\s*\.\s*handler\s*=\s*([\w$.]+)?", "RawReceivePort.handler"),
               (rf"\bawait\s+for\s*\([^()]*\bin\s+{p}\s*\)", "await for"),
               (rf"(?<![\w$.]){p}\s*\.\s*(?:first|forEach|asBroadcastStream|map|where|take)\b", "ReceivePort stream"),
               (rf"\bStreamQueue\s*(?:<[^()]*?>)?\s*\(\s*{p}\s*\)", "StreamQueue(port)"),
               (rf"\b{p}\s*=\s*(?:[\w$.]+\s*=\s*)?RawReceivePort\s*\(\s*([\w$.]+)", "RawReceivePort(handler)"))
        for rx, how in rxs:
            for m in re.finditer(rx, body):
                pos = lo + m.start()
                if self.s.masked(f, pos):
                    continue
                cb = m.group(1) if m.groups() and m.group(1) else None
                h = (self.entry(f, pos, cb.replace("this.", "")) if cb else None) or self.fn_at(f, pos)
                protocol_receive(self.b, "isolate", ep + ":out", h, f, self.s.line_of(f, pos), EXACT, how=how)
                self.st["port_listeners"] += 1


def apply(project, builder, sock=None) -> dict:
    """Dart isolates (#38 part 3)."""
    return Isolates(project, builder, sock).run()

"""Flutter platform channels, send side: `MethodChannel('name').invokeMethod('m')` (also invokeMapMethod /
invokeListMethod, channels held in fields, top-level / static constants and locals) and
`EventChannel('name').receiveBroadcastStream()`. Each call becomes SENDS_TO endpoint:flutter:<channel>#<method> /
endpoint:flutter-event:<channel> (codegraph/bridges.py links the Kotlin / Swift handlers).

Also the Dart receiving side of native -> Dart calls: `channel.setMethodCallHandler(handler)` and the method names the
handler tests (RECEIVED_BY, side dart), and Pigeon: the @HostApi / @FlutterApi classes of the definition files, Dart
calls on host APIs (SENDS_TO endpoint:pigeon:<Api>#<method>) and the Dart classes implementing flutter APIs."""
from __future__ import annotations

import re

from ...core.model import EXACT, HEURISTIC, RESOLVED
from .program import Ctx, DartProgram, DClass, DFunc, DVar, ctor_type

METHOD_CHANNELS = {"MethodChannel", "OptionalMethodChannel"}
EVENT_CHANNELS = {"EventChannel"}
INVOKE = {"invokeMethod", "invokeMapMethod", "invokeListMethod"}


def _creation(prog: DartProgram, r, ctx: Ctx, depth: int = 0):
    """The MethodChannel / EventChannel creation an expression evaluates to: (kind, creation fact, ctx) or None."""
    if not isinstance(r, dict) or depth > 8:
        return None
    ct = ctor_type(r)
    if ct:
        base = ct.split(".")[-1]
        if base in METHOD_CHANNELS:
            return "method", r, ctx
        if base in EVENT_CHANNELS:
            return "event", r, ctx
        return None
    k = r.get("k")
    if k in ("nn", "await", "as") and isinstance(r.get("e"), dict):
        return _creation(prog, r["e"], ctx, depth + 1)
    if k == "cond":
        return _creation(prog, r.get("a"), ctx, depth + 1) or _creation(prog, r.get("b"), ctx, depth + 1)
    if k == "bin" and r.get("op") == "??":
        return _creation(prog, r.get("l"), ctx, depth + 1) or _creation(prog, r.get("r"), ctx, depth + 1)
    var = None
    if k == "id":
        loc = ctx.locals.get(r["v"])
        if loc:
            kind, d = loc
            if kind == "var" and d.get("init") is not None:
                return _creation(prog, d["init"], ctx, depth + 1)
            if kind == "param" and d.get("this") and ctx.cls and r["v"] in ctx.cls.fields:
                var = ctx.cls.fields[r["v"]]
            else:
                return None
        elif ctx.cls:
            m, _ = prog.find_member(ctx.cls, r["v"])
            if isinstance(m, DVar):
                var = m
            elif isinstance(m, DFunc) and m.kind == "getter":
                return _getter(prog, m, depth)
        if var is None:
            v = prog.lookup(ctx.lib, r["v"])
            if isinstance(v, DVar):
                var = v
            elif isinstance(v, DFunc) and v.kind == "getter":
                return _getter(prog, v, depth)
    elif k == "prop":
        t = r.get("t") or {}
        if t.get("k") == "this" and ctx.cls:
            m, _ = prog.find_member(ctx.cls, r["n"])
            var = m if isinstance(m, DVar) else None
        elif t.get("k") == "id" and t["v"][:1].isupper():          # Class.staticField
            c = prog.resolve_class(ctx.lib, t["v"])
            if isinstance(c, DClass):
                m, _ = prog.find_member(c, r["n"])
                var = m if isinstance(m, DVar) else None
        elif t.get("k") == "id" and t["v"] not in ctx.locals and prog.prefix_imports(ctx.lib, t["v"]):
            v = prog.lookup_prefixed(ctx.lib, t["v"], r["n"])
            var = v if isinstance(v, DVar) else None
    if var is not None and var.init is not None:
        return _creation(prog, var.init, Ctx(prog, var.lib, None, var.cls), depth + 1)
    if var is not None and var.cls is not None:
        # `late MethodChannel _channel;` assigned in initState / a constructor: `_channel = MethodChannel('x')`
        for m in list(var.cls.methods.values()) + list(var.cls.ctors.values()):
            for x in m.facts:
                lhs = x.get("lhs") or {}
                if x.get("ft") == "assign" and ((lhs.get("k") == "id" and lhs.get("v") == var.name) or
                                                (lhs.get("k") == "prop" and lhs.get("n") == var.name
                                                 and (lhs.get("t") or {}).get("k") == "this")):
                    cr = _creation(prog, x.get("rhs"), prog.ctx_of(m), depth + 1)
                    if cr:
                        return cr
    return None


def _getter(prog, f: DFunc, depth):
    ctx = prog.ctx_of(f)
    for x in f.facts:
        if x["ft"] == "return":
            return _creation(prog, x.get("v"), ctx, depth + 1)
    return None


def _channel_name(ev, creation, ctx) -> tuple[str | None, str]:
    a = creation.get("a") or []
    if not a:
        return None, HEURISTIC
    t = ev.eval(a[0], ctx)
    if t.unknown or not t.text or "{" in t.text:
        return None, HEURISTIC
    return t.text, (EXACT if t.conf == EXACT else RESOLVED)


_CH = {"method": "MethodChannel", "event": "EventChannel"}


def _dynamic(builder, fn, f, kind, what) -> None:
    """A channel call whose channel or method name does not evaluate to a string -> the bridges `unresolved` list
    (`cg bridges --unmatched`), like a dynamic React Native / Capacitor / Cordova name."""
    builder.__dict__.setdefault("bridge_dynamic", []).append(
        {"file": fn.file, "line": f.get("l"), "protocol": "flutter" if kind == "method" else "flutter-event", "what": what})


def emit(prog: DartProgram, ev, builder) -> dict:
    from ...bridges import protocol_send
    n = {"flutter": 0, "flutter-event": 0, "unresolved_channel": 0}
    texts: dict = {}
    for fn in list(prog.all_funcs()):
        ctx = None
        for f in fn.facts:
            if f.get("ft") == "call" and f.get("n") == "setMethodCallHandler" and f.get("t"):
                ctx = ctx or prog.ctx_of(fn)
                n["dart_handlers"] = n.get("dart_handlers", 0) + _dart_handler(prog, ev, builder, fn, f, ctx, texts)
                continue
            if f.get("ft") != "call" or f.get("n") not in INVOKE | {"receiveBroadcastStream"} or not f.get("t"):
                continue
            ctx = ctx or prog.ctx_of(fn)
            cr = _creation(prog, f["t"], ctx)
            if not cr:
                continue
            kind, creation, cctx = cr
            name, conf = _channel_name(ev, creation, cctx)
            if not name:
                n["unresolved_channel"] += 1
                _dynamic(builder, fn, f, kind, f"{_CH[kind]}(<dynamic>).{f['n']}")
                continue
            if kind == "method" and f["n"] in INVOKE:
                a = f.get("a") or []
                m = ev.eval(a[0], ctx) if a else None
                if m is None or m.unknown or not m.text or "{" in m.text:
                    n["unresolved_channel"] += 1
                    _dynamic(builder, fn, f, kind, f"{name}.{f['n']}(<dynamic>)")
                    continue
                protocol_send(builder, "flutter", name, m.text, fn.id, fn.file, f.get("l"),
                              conf if m.conf == EXACT else RESOLVED, via=f["n"])
                n["flutter"] += 1
            elif kind == "event" and f["n"] == "receiveBroadcastStream":
                protocol_send(builder, "flutter-event", name, None, fn.id, fn.file, f.get("l"), conf, via=f["n"])
                n["flutter-event"] += 1
    return n


# ------------------------------------------------------------------ Pigeon (generated BasicMessageChannels)
PIGEON_ANN = {"HostApi": "host", "FlutterApi": "flutter"}


def pigeon_apis(prog: DartProgram) -> dict:
    """`@HostApi()` / `@FlutterApi()` abstract classes of the Pigeon definition files (`import
    'package:pigeon/pigeon.dart'`): name -> {kind, methods, file, line, id}. The generated code (`*.g.dart`, `.g.kt`,
    `.g.swift`) is often not checked in; the definitions name the API and its methods either way."""
    out = {}
    for c in prog.classes.values() if isinstance(prog.classes, dict) else prog.classes:
        kinds = [PIGEON_ANN[a] for a in c.ann if a in PIGEON_ANN]
        if not kinds or not prog.imports_ext(c.lib, "package:pigeon/"):
            continue
        meths = sorted(n for n, m in c.methods.items() if m.kind == "method" and not n.startswith("_"))
        out[c.name] = {"kind": kinds[0], "methods": meths, "file": c.file, "line": c.line, "id": c.id}
    return out


def emit_pigeon(prog: DartProgram, builder, apis: dict) -> dict:
    """Dart side of Pigeon APIs: a call of a @HostApi method on the generated class (`NativeSyncApi().m()`, a field /
    provider typed `NativeSyncApi`) -> SENDS_TO endpoint:pigeon:<Api>#<method>; a class that extends / implements a
    @FlutterApi (`class BgService extends BackgroundWorkerFlutterApi`) -> RECEIVED_BY from the endpoint to its method
    (the native side calls it)."""
    from ...bridges import protocol_receive, protocol_send
    n = {"sends": 0, "dart_receivers": 0}
    host = {k: v for k, v in apis.items() if v["kind"] == "host"}
    defs = {v["file"] for v in apis.values()}
    if host:
        names = {m for v in host.values() for m in v["methods"]}
        for fn in list(prog.all_funcs()):
            if fn.file in defs:
                continue
            ctx = None
            for f in fn.facts:
                if f.get("ft") != "call" or f.get("n") not in names or not f.get("t"):
                    continue
                ctx = ctx or prog.ctx_of(fn)
                try:
                    tt = prog.infer(f["t"], ctx)
                except RecursionError:
                    continue
                api = None
                if not tt or tt[0] not in ("einst", "inst"):
                    pt = _provider_type(prog, f["t"], ctx)       # ref.read(nativeSyncApiProvider).m()
                    tt = ("einst", pt, []) if pt else tt
                if tt and tt[0] == "einst":
                    api = str(tt[1]).split(".")[-1].split("<")[0]
                elif tt and tt[0] == "inst" and tt[1] is not None and tt[1].name in host:
                    api = tt[1].name          # the definition class, or a checked-in generated class of that name
                if api in host and f["n"] in host[api]["methods"]:
                    protocol_send(builder, "pigeon", api, f["n"], fn.id, fn.file, f.get("l"), RESOLVED,
                                  via="Pigeon HostApi", direction="to_native")
                    n["sends"] += 1
    flutter = {k: v for k, v in apis.items() if v["kind"] == "flutter"}
    if flutter:
        for c in (prog.classes.values() if isinstance(prog.classes, dict) else prog.classes):
            if c.file in defs or c.file.startswith(("test/", "integration_test/")) or "/test/" in c.file \
                    or c.file.endswith("_test.dart"):
                continue          # mocks / fakes of the API in tests
            sup = {split_base(t) for _rel, t in c.supers}
            for api in sup & set(flutter):
                for m in flutter[api]["methods"]:
                    f = c.methods.get(m)
                    if f is None:
                        continue
                    protocol_receive(builder, "pigeon", api, m, f.id, f.file, f.line, RESOLVED,
                                     via="Pigeon FlutterApi implementation", side="dart")
                    n["dart_receivers"] += 1
    return n


def split_base(t: str) -> str:
    return (t or "").split("<")[0].split(".")[-1].strip().rstrip("?")


PROVIDER_CTORS = ("Provider", "FutureProvider", "StateProvider", "AutoDisposeProvider", "ChangeNotifierProvider")


def _provider_type(prog, r, ctx):
    """`ref.read(p)` / `ref.watch(p)` / `context.read<T>()` where `p = Provider<T>((ref) => ...)`: T."""
    if not isinstance(r, dict) or r.get("k") != "call" or r.get("n") not in ("read", "watch"):
        return None
    if r.get("ta"):
        return split_base(r["ta"][0])
    a = r.get("a") or []
    if not a or not isinstance(a[0], dict) or a[0].get("k") != "id":
        return None
    v = prog.lookup(ctx.lib, a[0]["v"])
    init = getattr(v, "init", None)
    if not isinstance(init, dict):
        return None
    ct = ctor_type(init) or ""
    if ct.split(".")[-1].split("<")[0] not in PROVIDER_CTORS:
        return None
    ta = init.get("ta") or []
    if ta:
        return split_base(ta[0])
    m = re.search(r"<\s*([\w.]+)", ct)
    return split_base(m.group(1)) if m else None


# ------------------------------------------------------------------ Dart as the receiving side (native invokeMethod)
DART_SITE = re.compile(r"\.method\s*==\s*['\"]([^'\"]+)['\"]|['\"]([^'\"]+)['\"]\s*==\s*\w+\.method\b")
DART_SWITCH = re.compile(r"\bswitch\s*\(\s*\w+\.method\s*\)\s*\{")
DART_CASE = re.compile(r"\bcase\s+['\"]([^'\"]+)['\"]\s*(?::|when\b|\|\|)|\|\|\s*['\"]([^'\"]+)['\"]")


def _match(text: str, i: int, o: str, c: str) -> int:
    depth = 0
    for j in range(i, len(text)):
        ch = text[j]
        if ch == o:
            depth += 1
        elif ch == c:
            depth -= 1
            if depth == 0:
                return j
    return len(text)


def _line_off(text: str, line: int) -> int:
    pos = 0
    for _ in range(max(0, line - 1)):
        pos = text.find("\n", pos) + 1
        if pos == 0:
            return len(text)
    return pos


def _dart_handler(prog, ev, builder, fn, f, ctx, texts) -> int:
    """`channel.setMethodCallHandler(handler)` on a channel with a known name: the method names the handler tests
    (`call.method == 'x'`, `switch (call.method) { case 'x': }`) are received in Dart (RECEIVED_BY from
    endpoint:flutter:<channel>#<x>; the native side sends them with invokeMethod)."""
    from ...bridges import protocol_receive
    cr = _creation(prog, f["t"], ctx)
    if not cr or cr[0] != "method":
        return 0
    name, conf = _channel_name(ev, cr[1], cr[2])
    if not name:
        return 0
    a = (f.get("a") or [None])[0]
    target, file = fn, fn.file
    if isinstance(a, dict) and a.get("k") in ("id", "prop"):
        nm = a.get("v") if a.get("k") == "id" else a.get("n")
        h = None
        if fn.cls is not None:
            h, _ = prog.find_member(fn.cls, nm)
        if h is None:
            h = prog.lookup(fn.lib, nm)
        if not isinstance(h, DFunc):
            return 0
        target, file = h, h.file
    root = getattr(prog, "root", None)
    if file not in texts:
        try:
            texts[file] = (root / file).read_text(encoding="utf-8", errors="replace") if root else ""
        except OSError:
            texts[file] = ""
    text = texts[file]
    if not text:
        return 0
    if target is fn:                         # a closure: the call's argument list
        lo = text.find("setMethodCallHandler", _line_off(text, f.get("l") or 1))
        if lo < 0:
            return 0
        lo = text.find("(", lo)
        hi = _match(text, lo, "(", ")")
    else:
        lo = _line_off(text, target.line)
        b0 = text.find("{", lo)
        hi = _match(text, b0, "{", "}") if b0 >= 0 else lo
    region = text[lo:hi]
    meths = []
    for m in DART_SITE.finditer(region):
        meths.append((m.group(1) or m.group(2), lo + m.start()))
    for m in DART_SWITCH.finditer(region):
        end = _match(region, m.end() - 1, "{", "}")
        for cm in DART_CASE.finditer(region, m.end(), end):
            meths.append((cm.group(1) or cm.group(2), lo + cm.start()))
    seen = set()
    for meth, pos in meths:
        if meth in seen:
            continue
        seen.add(meth)
        protocol_receive(builder, "flutter", name, meth, target.id, file, text.count("\n", 0, pos) + 1,
                         conf if conf != EXACT else RESOLVED, via="setMethodCallHandler (Dart)", side="dart")
    return len(seen)

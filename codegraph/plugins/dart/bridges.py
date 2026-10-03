"""Flutter platform channels, send side: `MethodChannel('name').invokeMethod('m')` (also invokeMapMethod /
invokeListMethod, channels held in fields, top-level / static constants and locals) and
`EventChannel('name').receiveBroadcastStream()`. Each call becomes SENDS_TO endpoint:flutter:<channel>#<method> /
endpoint:flutter-event:<channel> (codegraph/bridges.py links the Kotlin / Swift handlers)."""
from __future__ import annotations

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


def emit(prog: DartProgram, ev, builder) -> dict:
    from ...bridges import protocol_send
    n = {"flutter": 0, "flutter-event": 0, "unresolved_channel": 0}
    for fn in list(prog.all_funcs()):
        ctx = None
        for f in fn.facts:
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
                continue
            if kind == "method" and f["n"] in INVOKE:
                a = f.get("a") or []
                m = ev.eval(a[0], ctx) if a else None
                if m is None or m.unknown or not m.text or "{" in m.text:
                    n["unresolved_channel"] += 1
                    continue
                protocol_send(builder, "flutter", name, m.text, fn.id, fn.file, f.get("l"),
                              conf if m.conf == EXACT else RESOLVED, via=f["n"])
                n["flutter"] += 1
            elif kind == "event" and f["n"] == "receiveBroadcastStream":
                protocol_send(builder, "flutter-event", name, None, fn.id, fn.file, f.get("l"), conf, via=f["n"])
                n["flutter-event"] += 1
    return n

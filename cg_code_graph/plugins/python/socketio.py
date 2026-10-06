"""python-socketio / Flask-SocketIO events -> endpoint:socketio:<namespace>#<event> (the #31 protocol model).

Objects: module-level `sio = socketio.AsyncServer(...)` / `Server` / `flask_socketio.SocketIO(app)` (server) and
`socketio.AsyncClient()` / `Client` / `SimpleClient` (client), used in their module or imported by name elsewhere.

  receive   @sio.on('event', namespace='/ns') / @sio.event (the function name is the event) / sio.on('event', handler);
            class namespaces (socketio.Namespace / AsyncNamespace: on_<event> methods, namespace from
            register_namespace(Cls('/ns')))
  send      sio.emit('event', data, to=room, namespace='/ns') / sio.call(...) (role request) / sio.send(data) (event
            `message`); f-string event names become `{param}` templates
  guards    a server namespace's `connect` handler that rejects (raises ConnectionRefusedError / returns False) is the
            guard of every server receiver in that namespace; without one the receivers record guards [] (`unguarded`)
connect / disconnect / connect_error are lifecycle callbacks, not endpoints.
"""
from __future__ import annotations

import ast

from ...core.model import EXACT, RESOLVED
from ...protocols import protocol_receive, protocol_send

SERVER = {"socketio.Server", "socketio.AsyncServer", "flask_socketio.SocketIO"}
CLIENT = {"socketio.Client", "socketio.AsyncClient", "socketio.SimpleClient", "socketio.AsyncSimpleClient"}
NAMESPACE_BASES = ("socketio.Namespace", "socketio.AsyncNamespace", "socketio.ClientNamespace", "socketio.AsyncClientNamespace",
                   "flask_socketio.Namespace")
LIFECYCLE = {"connect", "disconnect", "connect_error"}
LIB = "python-socketio"


def _module_level(body):
    """Module-level statements, including those under `if` / `try` / `with` blocks (`if REDIS: sio = AsyncServer(..)`)."""
    for st in body:
        yield st
        if isinstance(st, (ast.If, ast.Try, ast.With)):
            for blk in (getattr(st, "body", []), getattr(st, "orelse", []), getattr(st, "finalbody", []),
                        *[h.body for h in getattr(st, "handlers", [])]):
                yield from _module_level(blk)


def _full(m, dn: str | None) -> str | None:
    if not dn:
        return None
    head, _, rest = dn.partition(".")
    imp = m.imports.get(head)
    if not imp:
        return None
    if imp[0] in ("mod", "modprefix"):
        base = imp[1]
    elif imp[0] == "sym":
        base = f"{imp[1]}.{imp[2]}"
    else:
        return None
    return f"{base}.{rest}" if rest else base


def _event(e) -> str | None:
    if isinstance(e, ast.Constant) and isinstance(e.value, str):
        return e.value
    if isinstance(e, ast.JoinedStr):
        out = []
        for v in e.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                d = v.value
                nm = d.attr if isinstance(d, ast.Attribute) else d.id if isinstance(d, ast.Name) else "param"
                out.append("{" + nm + "}")
        return "".join(out)
    return None


def _str(e) -> str | None:
    return e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None


def _kw(call, name):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _rejects(fn) -> bool:
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Raise) and sub.exc is not None:
            t = sub.exc.func if isinstance(sub.exc, ast.Call) else sub.exc
            if (getattr(t, "id", None) or getattr(t, "attr", None)) in ("ConnectionRefusedError",):
                return True
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Constant) and sub.value.value is False:
            return True
    return False


def index(prog, b, walk_body) -> dict:
    mods = [m for m in prog.modules.values()
            if any((v[1] if v[0] != "sym" else v[1]).split(".")[0] in ("socketio", "flask_socketio") for v in m.imports.values()
                   if isinstance(v, tuple) and len(v) > 1 and isinstance(v[1], str))]
    if not mods:
        return {}
    objs: dict[tuple[str, str], str] = {}
    for m in mods:
        for st in _module_level(m.tree.body):
            if isinstance(st, (ast.Assign, ast.AnnAssign)) and isinstance(st.value, ast.Call):
                full = _full(m, _dn(st.value.func))
                role = "server" if full in SERVER else "client" if full in CLIENT else None
                tgts = st.targets if isinstance(st, ast.Assign) else [st.target]
                if role:
                    for t in tgts:
                        if isinstance(t, ast.Name):
                            objs[(m.name, t.id)] = role
    if not objs:
        return {}

    def obj_of(m, name):
        if (m.name, name) in objs:
            return objs[(m.name, name)]
        imp = m.imports.get(name)
        if imp and imp[0] == "sym":
            return objs.get((imp[1], imp[2]))
        return None

    st = {"objects": len(objs), "receivers": 0, "sends": 0}
    recvs = []          # (namespace, event, func, line, conf, role)
    guards: dict[str, list] = {}
    servers_ns: set = set()
    ns_of_class: dict[str, str] = {}
    for m in prog.modules.values():
        for sub in ast.walk(m.tree):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "register_namespace" \
                    and sub.args and isinstance(sub.args[0], ast.Call):
                cn = _dn(sub.args[0].func)
                ns = _str(sub.args[0].args[0]) if sub.args[0].args else None
                if cn:
                    ns_of_class[cn.split(".")[-1]] = ns or "/"
    for f in prog.funcs.values():
        m = f.module
        for d in f.decorators:
            call = d if isinstance(d, ast.Call) else None
            fn = call.func if call else d
            if not isinstance(fn, ast.Attribute) or not isinstance(fn.value, ast.Name):
                continue
            role = obj_of(m, fn.value.id)
            if not role:
                continue
            ns = (_str(_kw(call, "namespace")) if call else None) or "/"
            if fn.attr == "on" and call and call.args:
                ev = _event(call.args[0])
            elif fn.attr == "event":
                ev = f.name
            else:
                continue
            if not ev:
                continue
            if ev in LIFECYCLE:
                if ev == "connect" and role == "server":
                    servers_ns.add(ns)
                    if _rejects(f.node):
                        guards.setdefault(ns, []).append(f"{f.name} (connect handler rejects)")
                continue
            recvs.append((ns, ev, f, d.lineno, EXACT, role))
    for c in prog.classes.values():
        if prog.subclass_of(c, *NAMESPACE_BASES):
            client = prog.subclass_of(c, "socketio.ClientNamespace", "socketio.AsyncClientNamespace")
            ns = ns_of_class.get(c.name, "/")
            for name, f in c.methods.items():
                if not name.startswith("on_"):
                    continue
                ev = name[3:]
                if ev in LIFECYCLE:
                    if ev == "connect" and not client:
                        servers_ns.add(ns)
                        if _rejects(f.node):
                            guards.setdefault(ns, []).append(f"{c.name}.{name} (connect handler rejects)")
                    continue
                recvs.append((ns, ev, f, f.line, RESOLVED, "client" if client else "server"))
    for f in prog.funcs.values():
        m = f.module
        for sub in walk_body(f.node):
            if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and isinstance(sub.func.value, ast.Name)):
                continue
            role = obj_of(m, sub.func.value.id)
            if not role:
                continue
            a = sub.func.attr
            ns = _str(_kw(sub, "namespace")) or "/"
            if a == "on" and len(sub.args) >= 2:          # sio.on('event', handler)
                ev = _event(sub.args[0])
                h = prog.infer(sub.args[1], _ctx(prog, f))
                if ev and ev not in LIFECYCLE and h and h[0] in ("func", "bound"):
                    recvs.append((ns, ev, h[1], sub.lineno, RESOLVED, role))
                continue
            if a not in ("emit", "call", "send"):
                continue
            ev = "message" if a == "send" else (_event(sub.args[0]) if sub.args else None)
            if not ev:
                continue
            room = _kw(sub, "to") or _kw(sub, "room")
            protocol_send(b, "socketio", f"{ns}#{ev}", f.id, f.file, sub.lineno, EXACT if "{" not in ev else RESOLVED,
                          role="request" if a == "call" else "emit", library=LIB, process=role,
                          room=_str(room) if room is not None else None, ack=a == "call" or None,
                          node_attrs={"namespace": ns, "event": ev})
            st["sends"] += 1
    for ns, ev, f, line, conf, role in recvs:
        g = (guards.get(ns, []) if role == "server" else None)
        protocol_receive(b, "socketio", f"{ns}#{ev}", f.id, f.file, line, conf, guards=g, library=LIB, process=role,
                         node_attrs={"namespace": ns, "event": ev})
        st["receivers"] += 1
    if guards:
        st["guarded_namespaces"] = sorted(guards)
    return st


def _dn(e):
    from .plugin import dotted
    return dotted(e)


def _ctx(prog, f):
    from .plugin import Ctx
    return Ctx(f.module, f, f.cls)

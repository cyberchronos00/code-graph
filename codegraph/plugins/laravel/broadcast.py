"""Laravel broadcasting: channel authorization, broadcast events and the auth route.

* `Broadcast::channel('orders.{orderId}', callback|ChannelClass, ['guards' => ...])` -> node `channel:<pattern>`
  (entry_kind channel_auth). A closure callback's calls / reads are re-attributed from the defining file to the
  channel node, so `callees channel:orders.{orderId}` lists exactly the checks that decide who may join. A channel
  class gets HANDLED_BY -> its join() method. The callback's first parameter (the authenticated user) is typed as
  the application's User model when it carries no type.
* The broadcasting auth route: `Broadcast::routes([...])`, `->withBroadcasting(path, [...])`,
  `->withRouting(channels: ...)` synthesize `POST /broadcasting/auth` (prefix + middleware, default `web`); an
  explicit route to BroadcastController / `.../broadcasting/auth` is used as is. Each gets AUTHORIZES_CHANNEL ->
  every declared channel.
* ShouldBroadcast(Now) events: `event:<FQCN>` (attrs broadcast, broadcast_as) with HANDLED_BY -> broadcastOn /
  broadcastWith / broadcastAs / broadcastWhen, and BROADCASTS_ON -> the channel(s) named in broadcastOn() (string
  names, `new Channel/PrivateChannel/PresenceChannel(...)`, helper methods that build the name, and, when the name
  is a constructor argument, the value passed at each `new Event(...)` / `Event::dispatch(...)` site). Names that
  match no declared pattern become `channel:<name>` nodes with declared=false.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ..php.plugin import PhpFunc, module_of
from ..php.strings import StrEval, channel_match, fully_dynamic, same_shape

BROADCAST_FACADES = {"illuminate\\support\\facades\\broadcast", "broadcast"}
CHANNEL_CLASSES = {"illuminate\\broadcasting\\channel": "public", "illuminate\\broadcasting\\privatechannel": "private",
                   "illuminate\\broadcasting\\presencechannel": "presence",
                   "illuminate\\broadcasting\\encryptedprivatechannel": "private-encrypted"}
SHOULD_BROADCAST = "Illuminate\\Contracts\\Broadcasting\\ShouldBroadcast"
SHOULD_BROADCAST_NOW = "Illuminate\\Contracts\\Broadcasting\\ShouldBroadcastNow"
USER_BASE = "Illuminate\\Foundation\\Auth\\User"
AUTH_URI_RE = re.compile(r"(^|/)broadcasting/auth$")
THIS_PH = re.compile(r"\{this\.(\w+)\}")


def _classconst(d):
    return d.get("class") if d and d.get("k") == "classconst" else None


def _opt(arr, key):
    if not arr or arr.get("k") != "arr":
        return None
    for it in arr.get("items") or []:
        k = it.get("key") or {}
        if k.get("k") == "str" and k.get("v") == key:
            return it.get("v")
    return None


class BroadcastAnalysis:
    def __init__(self, lp, prog):
        self.lp, self.prog = lp, prog
        self.sev = StrEval(prog)
        self.defs: list[tuple[PhpFunc, dict]] = []
        self.route_calls: list[tuple[PhpFunc, dict, str, object]] = []

    # ------------------------------------------------------------------ before inference
    def _user_model(self) -> str | None:
        prog = self.prog
        cands = [c.fqcn for c in prog.classes.values()
                 if c.kind == "class" and not prog.is_test_class(c.fqcn) and c.fqcn != USER_BASE and prog.is_a(c.fqcn, USER_BASE)]
        if not cands:
            return None
        cands.sort(key=lambda q: (q.split("\\")[-1] != "User", not q.startswith("App\\Models\\"), q))
        return cands[0]

    def register(self) -> None:
        for fn in self.prog.all_funcs:
            for f in fn.facts:
                if f.get("t") != "call":
                    continue
                m = (f.get("m") or "").lower()
                if f["kind"] == "static" and (f.get("class") or "").lower().lstrip("\\") in BROADCAST_FACADES:
                    if m == "channel" and f.get("args"):
                        self.defs.append((fn, f))
                    elif m == "routes":
                        self.route_calls.append((fn, f, "Broadcast::routes", 0))
                elif f["kind"] == "method" and m == "withbroadcasting":
                    self.route_calls.append((fn, f, "->withBroadcasting", 1))
                elif f["kind"] == "method" and m == "withrouting" and any(a.get("named") == "channels" for a in f.get("args") or []):
                    self.route_calls.append((fn, f, "->withRouting(channels:)", None))
        user = self._user_model()
        if not user:
            return
        for fn, f in self.defs:
            args = f["args"]
            if len(args) < 2 or args[1].get("k") != "closure":
                continue
            lo, hi = f["line"], f.get("end", f["line"])
            ctx = next((g["ctx"] for g in fn.facts if (g.get("ctx") or {}).get("line") == lo
                        and (g["ctx"].get("m") or "").lower() == "channel"), None)
            if not ctx or not ctx.get("cparams"):
                continue
            p = ctx["cparams"][0]
            typed = any(g.get("t") == "vartype" and g.get("var") == p and g.get("src") == "closure_param" and lo <= g.get("line", 0) <= hi
                        for g in fn.facts)
            if not typed:
                fn.facts.append({"t": "vartype", "var": p, "types": [user], "src": "channel_user", "line": lo})

    # ------------------------------------------------------------------ after reference emission
    def contribute(self) -> dict:
        b, prog = self.lp.b, self.prog
        st = defaultdict(int)
        declared: list[tuple[str, str]] = []
        moves = defaultdict(list)
        for fn, f in self.defs:
            args = f["args"]
            pats = self.sev.eval(args[0], fn)
            pat = pats[0] if pats else None
            if not pat or fully_dynamic(pat):
                st["channels_dynamic_name"] += 1
                continue
            pat = THIS_PH.sub(lambda m: "{" + m.group(1) + "}", pat)
            cb = args[1] if len(args) > 1 else None
            lo, hi = f["line"], f.get("end", f["line"])
            guards = []
            g = _opt(args[2] if len(args) > 2 else None, "guards")
            if g:
                guards = [x for x in self.sev.eval(g, fn) if "{" not in x] if g.get("k") != "arr" else \
                    [it["v"]["v"] for it in g.get("items") or [] if (it.get("v") or {}).get("k") == "str"]
            attrs = {"pattern": pat, "params": re.findall(r"\{(\w+)\}", pat), "declared": True}
            if guards:
                attrs["guards"] = guards
            presence, join = False, None
            if cb and cb.get("k") == "closure":
                attrs["callback"] = f"closure {fn.file}:{lo}"
                presence = any(x.get("t") == "return" and (x.get("expr") or {}).get("k") == "arr" and lo <= x.get("line", 0) <= hi
                               for x in fn.facts)
            elif _classconst(cb) and prog.cls(_classconst(cb)):
                cc = prog.cls(_classconst(cb))
                attrs["handler"] = cc.fqcn
                join = prog.find_method(cc.fqcn, "join")
                presence = bool(join and any(x.get("t") == "return" and (x.get("expr") or {}).get("k") == "arr" for x in join.facts))
            elif _classconst(cb):
                attrs["handler"] = _classconst(cb)
            attrs["visibility"] = "presence" if presence else "private"
            nid = f"channel:{pat}"
            if b.has(nid) and b.nodes[nid].attrs.get("declared"):
                st["channels_duplicate"] += 1
                b.nodes[nid].attrs.setdefault("also_defined", []).append(f"{fn.file}:{lo}")
                continue
            b.add_node("channel", pat, name=pat, file=fn.file, line=lo, end_line=hi, module=module_of(fn.file), lang="php",
                       entry_kind="channel_auth", attrs=attrs)
            b.nodes[nid].entry_kind = "channel_auth"
            declared.append((pat, nid))
            moves[fn.id].append((lo, hi, nid))
            if join:
                b.add_edge(nid, join.id, "HANDLED_BY", join.file, join.line, EXACT, via="channel class")
            st["channels"] += 1
        if moves:
            st["callback_edges_moved"] = b.move_edges(moves)
        st["auth_routes"] = self._auth_routes(declared)
        self._events(declared, st)
        return dict(st)

    def _auth_routes(self, declared) -> int:
        b, prog, lp = self.lp.b, self.prog, self.lp
        rids = []
        for f, r in lp.records.items():
            for rt in r.get("routes") or []:
                act = rt.get("action") or {}
                cls = (act.get("class") or "").lstrip("\\")
                if cls.endswith("BroadcastController") or AUTH_URI_RE.search(rt.get("full_uri") or ""):
                    for verb in rt["methods"]:
                        rid = f"route:{verb.upper()} {rt['full_uri']}"
                        if b.has(rid):
                            b.nodes[rid].attrs["broadcast_auth"] = True
                            rids.append(rid)
        aliases = lp._middleware_aliases()
        for fn, f, via, idx in self.route_calls:
            args = f.get("args") or []
            opts = None
            if idx is not None:
                opts = next((a for a in args if a.get("named") == "attributes"), None)
                if opts is None and idx < len(args) and not args[idx].get("named"):
                    opts = args[idx]
            prefix, mws = "", ["web"]
            if opts and opts.get("k") == "arr":
                pv = _opt(opts, "prefix")
                if pv:
                    prefix = (self.sev.eval(pv, fn) or [""])[0]
                mv = _opt(opts, "middleware")
                if mv is not None:
                    if mv.get("k") == "arr":
                        mws = [x for it in mv.get("items") or [] for x in self.sev.eval(it.get("v"), fn)[:1]]
                    else:
                        mws = self.sev.eval(mv, fn)[:1]
            uri = "/" + "/".join(x.strip("/") for x in (prefix, "broadcasting/auth") if x.strip("/"))
            key = f"POST {uri}"
            rid = b.add_node("route", key, name=key, file=fn.file, line=f["line"], module=module_of(fn.file), lang="php",
                             entry_kind="http_route",
                             attrs={"name": None, "middleware": mws, "uri": uri, "method": "POST", "broadcast_auth": True,
                                    "synthesized": f"{via} {fn.file}:{f['line']}"})
            b.nodes[rid].entry_kind = b.nodes[rid].entry_kind or "http_route"
            for mw in mws:
                alias = mw.split(":")[0]
                cls = aliases.get(alias) or (mw if prog.cls(mw) else None)
                if cls and prog.cls(cls):
                    h = prog.find_method(cls, "handle")
                    if h:
                        b.add_edge(rid, h.id, "USES_MIDDLEWARE", fn.file, f["line"], RESOLVED, alias=alias)
            rids.append(rid)
        for rid in dict.fromkeys(rids):
            for pat, nid in declared:
                n = b.nodes[nid]
                b.add_edge(rid, nid, "AUTHORIZES_CHANNEL", n.file, n.line, RESOLVED, via="broadcast auth")
        return len(set(rids))

    # ------------------------------------------------------------------ events
    def _channel_news(self, cls: str, start: PhpFunc) -> list[tuple[str, str, PhpFunc, int]]:
        """(pattern, visibility, fn, line) for each channel constructed / named in broadcastOn and the $this-methods
        it calls (two levels)."""
        out, seen, todo = [], set(), [(start, 0)]
        while todo:
            fn, depth = todo.pop(0)
            if fn.id in seen:
                continue
            seen.add(fn.id)
            for f in fn.facts:
                t = f.get("t")
                if t == "new" and (f.get("class") or "").lower().lstrip("\\") in CHANNEL_CLASSES and f.get("args"):
                    vis = CHANNEL_CLASSES[f["class"].lower().lstrip("\\")]
                    for p in self.sev.eval(f["args"][0], fn):
                        out.append((p, vis, fn, f["line"]))
                elif t == "return" and fn is start and not f.get("ctx"):
                    e = f.get("expr") or {}
                    items = [it.get("v") for it in e.get("items") or []] if e.get("k") == "arr" else [e]
                    for it in items:
                        if it and it.get("k") in ("str", "interp", "concat"):
                            for p in self.sev.eval(it, fn):
                                out.append((p, "public", fn, f["line"]))
                elif t == "call" and f["kind"] == "method" and (f.get("recv") or {}).get("k") == "this" and depth < 2:
                    m = self.prog.find_method(cls, f.get("m"))
                    if m and not m.abstract:
                        todo.append((m, depth + 1))
        return out

    def _sites(self, cls: str, prop: str) -> list[tuple[str, PhpFunc, int]]:
        """Values of constructor argument `prop` at each `new Cls(...)` / `Cls::dispatch(...)` site."""
        ctor = self.prog.find_method(cls, "__construct")
        if not ctor:
            return []
        idx = next((i for i, p in enumerate(ctor.params) if p["name"] == prop), None)
        if idx is None:
            return []
        out = []
        for fn in self.prog.all_funcs:
            for f in fn.facts:
                hit = (f.get("t") == "new" and f.get("class") == cls) or \
                      (f.get("t") == "call" and f["kind"] == "static" and f.get("class") == cls
                       and (f.get("m") or "").lower() in ("dispatch", "broadcast", "dispatchif", "dispatchunless"))
                if not hit:
                    continue
                args = f.get("args") or []
                a = next((x for x in args if x.get("named") == prop), None)
                if a is None and idx < len(args) and not args[idx].get("named"):
                    a = args[idx]
                if a is None:
                    continue
                for p in self.sev.eval(a, fn):
                    out.append((p, fn, f["line"]))
        return out

    def _events(self, declared, st):
        b, prog = self.lp.b, self.prog
        bevents = {}
        for c in list(prog.classes.values()):
            if c.kind != "class" or c.abstract or prog.is_test_class(c.fqcn):
                continue
            now = prog.is_a(c.fqcn, SHOULD_BROADCAST_NOW)
            if not (now or prog.is_a(c.fqcn, SHOULD_BROADCAST)):
                continue
            attrs = {"broadcast": True}
            if now:
                attrs["broadcast_now"] = True
            ba = prog.find_method(c.fqcn, "broadcastAs")
            if ba:
                names = [x for r in ba.facts if r.get("t") == "return" and not r.get("ctx") for x in self.sev.eval(r["expr"], ba)]
                if names and "{" not in names[0]:
                    attrs["broadcast_as"] = names[0]
            eid = b.add_node("event", c.fqcn, name=c.fqcn.split("\\")[-1], fqn=c.fqcn, file=c.file, line=c.line,
                             end_line=c.end_line, module=module_of(c.file), lang="php", attrs=attrs)
            n = b.nodes[eid]
            n.file, n.line = n.file or c.file, n.line or c.line
            bevents[f"{c.kind}:{c.fqcn}"] = eid
            st["broadcast_events"] += 1
            for mn in ("broadcastOn", "broadcastWith", "broadcastAs", "broadcastWhen"):
                m = prog.find_method(c.fqcn, mn)
                if m and not m.abstract:
                    b.add_edge(eid, m.id, "HANDLED_BY", m.file, m.line, EXACT, via="broadcast")
            bo = prog.find_method(c.fqcn, "broadcastOn")
            if not bo or bo.abstract:
                continue
            unresolved = []
            for pat, vis, fn, line in self._channel_news(c.fqcn, bo):
                resolved = []
                props = THIS_PH.findall(pat)
                if fully_dynamic(pat) and len(props) == 1:
                    for val, sfn, sline in self._sites(c.fqcn, props[0]):
                        resolved.append((THIS_PH.sub(lambda m: "{" + m.group(1) + "}", pat.replace("{this." + props[0] + "}", val)),
                                         {"site": f"{sfn.file}:{sline}", "site_fn": sfn.id}))
                if not resolved:
                    resolved = [(THIS_PH.sub(lambda m: "{" + m.group(1) + "}", pat), {})]
                # `{?}` (unknown) alternatives of a name that also resolved to a named placeholder add nothing
                resolved = [(nm, ex) for nm, ex in resolved
                            if "{?}" not in nm or not any(o != nm and "{?}" not in o and same_shape(nm, o) and ex == oe
                                                          for o, oe in resolved)]
                for name, extra in resolved:
                    if fully_dynamic(name):
                        unresolved.append(name)
                        st["channel_names_unresolved"] += 1
                        continue
                    hits = [nid for p, nid in declared if same_shape(name, p)]
                    conf = RESOLVED
                    if not hits:
                        hits = [nid for p, nid in declared if channel_match(name, p)]
                        conf = RESOLVED if len(hits) == 1 else HEURISTIC
                    if not hits:
                        nid = b.add_node("channel", name, name=name, lang="php",
                                         attrs={"pattern": name, "declared": False, "visibility": vis})
                        hits = [nid]
                        conf = RESOLVED
                        st["channels_undeclared"] += 1
                    ef, el = (extra["site"].rsplit(":", 1)[0], int(extra["site"].rsplit(":", 1)[1])) if extra else (fn.file, line)
                    for nid in hits:
                        b.add_edge(eid, nid, "BROADCASTS_ON", ef, el, conf, name=name, visibility=vis, at=f"{fn.file}:{line}", **extra)
                        st["broadcasts_on"] += 1
            if unresolved:
                n.attrs["unresolved_channels"] = sorted(set(unresolved))
        # a callback whose return is not an obvious array (arrow fn returning a model / query) still serves a presence
        # channel when every event publishes on it as a PresenceChannel
        pub_vis = {}
        for e in b.edges.values():
            if e.kind == "BROADCASTS_ON":
                pub_vis.setdefault(e.dst, set()).add(e.attrs.get("visibility"))
        for nid, vs in pub_vis.items():
            n = b.nodes.get(nid)
            if n and n.attrs.get("declared") and n.attrs.get("visibility") == "private" and vs == {"presence"}:
                n.attrs["visibility"] = "presence"
                n.attrs["visibility_from"] = "publishers (PresenceChannel)"
        # an instantiated broadcast event is (almost always) broadcast: `$e = new X(...); broadcast($e)->toOthers()`
        have = {(e.src, e.dst) for e in b.edges.values() if e.kind == "DISPATCHES"}
        for e in list(b.edges.values()):
            if e.kind == "INSTANTIATES" and e.dst in bevents and (e.src, bevents[e.dst]) not in have:
                if e.src.startswith("method:") and e.src.split(":", 1)[1].split("::")[0] == e.dst.split(":", 1)[1]:
                    continue
                b.add_edge(e.src, bevents[e.dst], "DISPATCHES", e.file, e.line, RESOLVED, via="instantiation (broadcast event)")
                have.add((e.src, bevents[e.dst]))
                st["dispatches_from_instantiation"] += 1

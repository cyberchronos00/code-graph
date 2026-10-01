"""Flutter framework plugin (on top of the Dart plugin).

contribute():
  * widgets: StatelessWidget/StatefulWidget/State/Consumer*/Hook* classes get lifecycle CALLS
    (createState/initState/didChangeDependencies/didUpdateWidget/build/dispose); creating a widget
    (`X(...)`) is a RENDERS edge; StatefulWidget -> its State class via createState;
  * bloc/cubit: `on<E>(handler)` registrations -> LISTENED_BY event class -> handler (subclasses of
    E included), `bloc.add(E(...))` / `context.read<B>().add(..)` -> DISPATCHES; `emit(S(..))` /
    `emit(state.copyWith(status: Enum.v))` -> EMITS_STATE; UI `is S`, `case S()`,
    `state.status == Enum.v`, switch cases -> HANDLES_STATE. Per bloc a state-flow summary is kept on
    the class node (attrs.state_flow): events dispatched without a handler, handlers never dispatched,
    emitted states/status values no UI code checks;
  * navigation: MaterialApp/CupertinoApp `routes:`/`home:`, go_router GoRoute trees (nested paths,
    :param -> {param}), auto_route @RoutePage, `Navigator.push(MaterialPageRoute(builder: ..))`,
    `pushNamed('/x')`, `context.go/push('/x')` -> page nodes (entry ui_page) + NAVIGATES_TO;
    `main()` in lib/ is ui_global.
"""
from __future__ import annotations

import re

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...core.plugin import FrameworkPlugin, GraphBuilder, Project
from ..dart.http import UrlEval
from ..dart.program import Ctx, DartProgram, DClass, DFunc, ctor_type, root_name, split_type, walk_repr

WIDGET_BASES = {"StatelessWidget", "StatefulWidget", "Widget", "HookWidget", "StatefulHookWidget", "ConsumerWidget",
                "ConsumerStatefulWidget", "HookConsumerWidget", "InheritedWidget", "InheritedNotifier", "InheritedModel",
                "RenderObjectWidget", "LeafRenderObjectWidget", "SingleChildRenderObjectWidget", "MultiChildRenderObjectWidget",
                "PreferredSizeWidget", "ProxyWidget", "ImplicitlyAnimatedWidget", "AnimatedWidget", "StatelessComponent",
                "StatefulComponent", "GetView", "GetWidget"}
STATE_BASES = {"State", "ConsumerState"}
LIFECYCLE = ("createState", "initState", "didChangeDependencies", "didUpdateWidget", "build", "dispose", "deactivate", "activate")
BLOC_BASES = {"Bloc", "HydratedBloc", "ReplayBloc"}
CUBIT_BASES = {"Cubit", "HydratedCubit", "ReplayCubit"}
ROUTE_BASES = {"Route", "PageRoute", "ModalRoute", "MaterialPageRoute", "CupertinoPageRoute", "PageRouteBuilder",
               "TransitionRoute", "PopupRoute"}
ROUTE_TYPES = {"MaterialPageRoute", "CupertinoPageRoute", "PageRouteBuilder", "MaterialWithModalsPageRoute", "DialogRoute",
               "ModalBottomSheetRoute", "FadeRoute", "SlideRoute"}
PUSH = {"push", "pushReplacement", "pushAndRemoveUntil", "pushAndRemoveAll", "popAndPush", "restorablePush", "to", "off", "offAll"}
PUSH_NAMED = {"pushNamed", "pushReplacementNamed", "popAndPushNamed", "pushNamedAndRemoveUntil", "restorablePushNamed", "toNamed",
              "offNamed", "offAllNamed"}
GO = {"go", "push", "replace", "pushReplacement", "goNamed", "pushNamed", "replaceNamed", "pushReplacementNamed"}
APP_TYPES = {"MaterialApp", "CupertinoApp", "WidgetsApp", "GetMaterialApp", "GetCupertinoApp"}


def gopath(p: str) -> str:
    return re.sub(r":(\w+)", r"{\1}", p)


class FlutterPlugin(FrameworkPlugin):
    name, language = "flutter", "dart"

    def detect(self, project: Project) -> bool:
        for p in [project.root / "pubspec.yaml"] + list(project.root.glob("*/pubspec.yaml")) + list(project.root.glob("*/*/pubspec.yaml")):
            try:
                if re.search(r"^\s+flutter:\s*$|sdk:\s*flutter", p.read_text(errors="replace"), re.M):
                    return True
            except OSError:
                continue
        return False

    def register_hooks(self, prog: DartProgram) -> None:
        def state_rule(prog_, r, ctx: Ctx):
            if r.get("k") == "id" and r["v"] == "state" and ctx.cls is not None and "state" not in ctx.locals:
                s = self.state_type(prog_, ctx.cls)
                if s is not None:
                    return ("inst", s, [])
            return None
        prog.type_rules.append(state_rule)

    @staticmethod
    def bloc_kind(prog: DartProgram, c: DClass) -> str | None:
        names = prog.ext_base_names(c)
        if names & BLOC_BASES:
            return "bloc"
        if names & CUBIT_BASES:
            return "cubit"
        return None

    @staticmethod
    def bloc_args(prog: DartProgram, c: DClass) -> list[str]:
        for t in prog.lineage(c):
            base, args, _ = split_type(t)
            if base.split(".")[-1] in BLOC_BASES | CUBIT_BASES:
                return args
        return []

    def state_type(self, prog: DartProgram, c: DClass) -> DClass | None:
        if self.bloc_kind(prog, c) is None:
            return None
        args = self.bloc_args(prog, c)
        if args:
            return prog.resolve_class(c.lib, split_type(args[-1])[0])
        return None

    # ------------------------------------------------------------------
    def contribute(self, project: Project, builder: GraphBuilder, prog: DartProgram) -> dict:
        if prog is None:
            return {"status": "no dart program"}
        b = builder
        st = {"widgets": 0, "states": 0, "renders": 0, "blocs": 0, "cubits": 0, "event_handlers": 0, "dispatches": 0,
              "dispatch_unhandled": 0, "emits": 0, "handles_state": 0, "pages": 0, "navigations": 0, "ui_global": 0}
        widget = {}
        for c in prog.classes.values():
            names = prog.ext_base_names(c) | {x.name for x in prog.mro(c)}
            if names & WIDGET_BASES:
                widget[c.id] = c
                b.nodes[c.id].attrs["flutter"] = "widget"
                st["widgets"] += 1
            elif names & STATE_BASES:
                b.nodes[c.id].attrs["flutter"] = "state"
                st["states"] += 1
            else:
                continue
            for m in LIFECYCLE:
                fm, inh = prog.find_member(c, m)
                if isinstance(fm, DFunc):
                    b.add_edge(c.id, fm.id, "CALLS", c.file, fm.line, RESOLVED, via="flutter-lifecycle")
        # creating a widget renders it; StatefulWidget -> State through createState
        for e in list(b.edges.values()):
            if e.kind == "INSTANTIATES" and e.dst in widget:
                b.add_edge(e.src, e.dst, "RENDERS", e.file, e.line, e.confidence)
                st["renders"] += 1
            if e.kind == "INSTANTIATES" and e.src.endswith(".createState") and b.nodes.get(e.dst) is not None:
                owner = e.src.rsplit(".", 1)[0].replace("method:", "class:", 1)
                b.add_edge(owner, e.dst, "RENDERS", e.file, e.line, EXACT, via="createState")
        self.blocs(prog, b, st)
        self.navigation(prog, b, st)
        for fn in prog.funcs.values():
            if fn.cls is None and fn.name == "main" and "/lib/" in "/" + fn.file and "test" not in fn.file.split("/")[0]:
                b.nodes[fn.id].entry_kind = "ui_global"
                st["ui_global"] += 1
        return st

    # ------------------------------------------------------------------ bloc
    def blocs(self, prog: DartProgram, b: GraphBuilder, st: dict) -> None:
        blocs: dict[str, dict] = {}
        for c in prog.classes.values():
            kind = self.bloc_kind(prog, c)
            if not kind:
                continue
            st["blocs" if kind == "bloc" else "cubits"] += 1
            args = self.bloc_args(prog, c)
            ev = prog.resolve_class(c.lib, split_type(args[0])[0]) if kind == "bloc" and len(args) >= 2 else None
            sc = self.state_type(prog, c)
            info = {"cls": c, "kind": kind, "event_base": ev, "state": sc, "handlers": [], "emits": [], "dispatched": []}
            blocs[c.id] = info
            b.nodes[c.id].attrs["flutter"] = kind
            for ct in c.ctors.values():
                for f in ct.facts:
                    if f["ft"] == "call" and f.get("n") == "on" and f.get("t") is None and f.get("ta"):
                        et = f["ta"][0]
                        ecls = prog.resolve_class(c.lib, split_type(et)[0])
                        a0 = (f.get("a") or [None])[0] or {}
                        h = None
                        if a0.get("k") == "id":
                            h = c.methods.get(a0["v"])
                            if h is None:
                                m, _ = prog.find_member(c, a0["v"])
                                h = m if isinstance(m, DFunc) else None
                        elif a0.get("k") == "fn":
                            h = c.methods.get(f"on<{et}>@{f.get('l')}")
                        if h is not None:
                            info["handlers"].append((ecls, et, h, f.get("l"), ct.file))
            if "mapEventToState" in c.methods:
                info["handlers"].append((ev, args[0] if args else "?", c.methods["mapEventToState"], c.methods["mapEventToState"].line, c.file))
            for ecls, et, h, line, file in info["handlers"]:
                st["event_handlers"] += 1
                if ecls is None:
                    continue
                b.add_edge(ecls.id, h.id, "LISTENED_BY", file, line, EXACT, via=f"on<{et}>", bloc=c.id)
                for sub in self.subclasses(prog, ecls):
                    b.add_edge(sub.id, h.id, "LISTENED_BY", file, line, RESOLVED, via=f"on<{et}> (subtype)", bloc=c.id)
        self._blocs = blocs
        event_owner: dict[str, list[str]] = {}
        for bid, info in blocs.items():
            for ecls, et, h, line, file in info["handlers"]:
                if ecls is not None:
                    for x in [ecls] + self.subclasses(prog, ecls):
                        event_owner.setdefault(x.id, []).append(bid)
            if info["event_base"] is not None:
                for x in [info["event_base"]] + self.subclasses(prog, info["event_base"]):
                    event_owner.setdefault(x.id, []).append(bid)
        # dispatch: bloc.add(Event(...))
        for fn in prog.funcs.values():
            ctx = prog.ctx_of(fn)
            for f in fn.facts:
                if f["ft"] != "call" or f.get("n") != "add" or not f.get("a"):
                    continue
                ev = None
                for tgt, conf, via in prog.resolve_call(f["a"][0], ctx) if (f["a"][0] or {}).get("k") in ("call", "new") else []:
                    if isinstance(tgt, DClass):
                        ev = tgt
                if ev is None:
                    continue
                bloc, conf = None, RESOLVED
                t = f.get("t")
                if t is None and fn.cls is not None and fn.cls.id in blocs:
                    bloc, conf = blocs[fn.cls.id], EXACT
                elif t is not None:
                    tt = prog.infer(t, ctx)
                    if tt and tt[0] == "inst" and tt[1] is not None and tt[1].id in blocs:
                        bloc = blocs[tt[1].id]
                if bloc is None:
                    owners = sorted(set(event_owner.get(ev.id, [])))
                    if len(owners) == 1:
                        bloc, conf = blocs[owners[0]], HEURISTIC
                    else:
                        continue
                handled = any(ecls is not None and (ecls is ev or ev in self.subclasses(prog, ecls)) for ecls, *_ in bloc["handlers"])
                if bloc["kind"] == "cubit":
                    continue
                b.add_edge(fn.id, ev.id, "DISPATCHES", fn.file, f.get("l"), conf, bloc=bloc["cls"].id,
                           **({"unhandled": True} if not handled else {}))
                bloc["dispatched"].append((ev, fn, f.get("l"), handled))
                st["dispatches"] += 1
                if not handled:
                    st["dispatch_unhandled"] += 1
        # emits
        for bid, info in blocs.items():
            c = info["cls"]
            for m in list(c.methods.values()) + list(c.ctors.values()):
                ctx = prog.ctx_of(m)
                for f in m.facts:
                    if f["ft"] != "call" or f.get("n") not in ("emit",) or not f.get("a"):
                        continue
                    if f.get("t") is not None and root_name(f.get("t")) not in ("emit", "emitter"):
                        continue
                    a0 = f["a"][0] or {}
                    cls, values = None, {}
                    if a0.get("k") in ("new", "call"):
                        for tgt, conf, via in prog.resolve_call(a0, ctx):
                            if isinstance(tgt, DClass):
                                cls = tgt
                        if cls is None and a0.get("k") == "call" and a0.get("n") == "copyWith":
                            tt = prog.infer(a0.get("t"), ctx)
                            cls = tt[1] if tt and tt[0] == "inst" else info["state"]
                        for k_, v in (a0.get("na") or {}).items():
                            if v and v.get("k") == "prop" and (v.get("t") or {}).get("k") == "id" and v["t"]["v"][:1].isupper():
                                values[k_] = f"{v['t']['v']}.{v['n']}"
                    if cls is None:
                        continue
                    b.add_edge(m.id, cls.id, "EMITS_STATE", m.file, f.get("l"), EXACT, **({"values": values} if values else {}))
                    info["emits"].append((cls, values, m, f.get("l")))
                    st["emits"] += 1
        # UI handling of states
        state_classes: dict[str, str] = {}
        status_enums: dict[str, str] = {}
        for bid, info in blocs.items():
            s = info["state"]
            if s is None:
                continue
            for x in [s] + self.subclasses(prog, s):
                state_classes[x.name] = bid
                for fv in x.fields.values():
                    ft = prog.resolve_class(x.lib, split_type(fv.type)[0]) if fv.type else None
                    if ft is not None and ft.kind == "enum":
                        status_enums[ft.name] = bid
            for cls, values, m, line in info["emits"]:
                for v in values.values():
                    status_enums.setdefault(v.split(".")[0], bid)
        handled: dict[str, set] = {bid: set() for bid in blocs}
        handled_at: dict[str, list] = {}
        for fn in prog.funcs.values():
            in_bloc = fn.cls is not None and fn.cls.id in blocs
            for f in fn.facts:
                names = []
                if f["ft"] == "is":
                    names = [split_type(f["type"])[0]]
                elif f["ft"] == "switch":
                    names = [re.match(r"^[\w.]+", c_).group(0) for c_ in f.get("cases") or [] if re.match(r"^[\w.]+", c_)]
                elif f["ft"] == "cmp":
                    names = [x for x in (f.get("a"), f.get("b")) if x and re.match(r"^[A-Z]\w*\.\w+$", x)]
                for nm in names:
                    base = nm.split(".")[0]
                    if nm in state_classes or base in state_classes and "." not in nm:
                        bid = state_classes.get(nm) or state_classes.get(base)
                        cls = prog.resolve_class(fn.lib, nm)
                        if cls is not None and not in_bloc:
                            b.add_edge(fn.id, cls.id, "HANDLES_STATE", fn.file, f.get("l"), RESOLVED, how=f["ft"])
                            handled[bid].add(cls.name)
                            handled_at.setdefault(cls.name, []).append(f"{fn.file}:{f.get('l')}")
                            st["handles_state"] += 1
                    elif base in status_enums and "." in nm:
                        bid = status_enums[base]
                        cls = prog.resolve_class(fn.lib, base)
                        if cls is not None and not in_bloc:
                            b.add_edge(fn.id, cls.id, "HANDLES_STATE", fn.file, f.get("l"), RESOLVED, how=f["ft"], value=nm)
                            handled[bid].add(nm)
                            handled_at.setdefault(nm, []).append(f"{fn.file}:{f.get('l')}")
                            st["handles_state"] += 1
        # per-bloc state flow summary
        for bid, info in blocs.items():
            c = info["cls"]
            emitted = {}
            for cls, values, m, line in info["emits"]:
                if values:
                    for v in values.values():
                        if v.split(".")[0] in status_enums:
                            emitted.setdefault(v, []).append(f"{m.file}:{line}")
                if cls is not info["state"] or not values:
                    emitted.setdefault(cls.name, []).append(f"{m.file}:{line}")
            unhandled_states = sorted(k for k in emitted if k not in handled[bid] and not (info["state"] is not None and k == info["state"].name))
            dispatched_ids = {ev.id for ev, *_ in info["dispatched"]}
            never = []
            for ecls, et, h, line, file in info["handlers"]:
                if ecls is None:
                    continue
                if not ({ecls.id} | {s.id for s in self.subclasses(prog, ecls)}) & dispatched_ids:
                    never.append({"event": et, "handler": h.id, "at": f"{file}:{line}"})
            flow = {"kind": info["kind"], "state": info["state"].id if info["state"] else None,
                    "events_dispatched_unhandled": [{"event": ev.name, "at": f"{fn.file}:{l}"} for ev, fn, l, h in info["dispatched"] if not h],
                    "handlers_never_dispatched": never,
                    "emitted": {k: v[:4] for k, v in emitted.items()},
                    "emitted_not_handled_by_ui": unhandled_states,
                    "handled_by_ui": {k: handled_at.get(k, [])[:4] for k in sorted(handled[bid])}}
            b.nodes[c.id].attrs["state_flow"] = flow

    @staticmethod
    def subclasses(prog: DartProgram, c: DClass) -> list[DClass]:
        out, seen, stack = [], {c.id}, [c]
        while stack:
            x = stack.pop()
            for s in prog.subs.get(x.id, []):
                if s.id not in seen:
                    seen.add(s.id)
                    out.append(s)
                    stack.append(s)
        return out

    # ------------------------------------------------------------------ navigation
    def page(self, b: GraphBuilder, prog: DartProgram, cls: DClass | None, route: str | None, file, line, via: str) -> str:
        pk = cls.lib.pkg if cls is not None else (prog.pkg_of(file) if file else None)
        pkg = pk.name if pk else None
        key = f"dart:{pkg or ''}:{route}" if route else f"dart:{cls.file}#{cls.name}"
        name = route or cls.name
        pid = b.add_node("page", key, name, fqn=key, file=cls.file if cls else file, line=cls.line if cls else line, lang="dart",
                         entry_kind="ui_page", attrs={"route": route, "widget": cls.id if cls else None, "via": via})
        if cls is not None:
            b.add_edge(pid, cls.id, "RENDERS", file, line, EXACT, via=via)
        return pid

    PAGE_WRAPPERS = {"Page", "MaterialPage", "CupertinoPage", "CustomTransitionPage", "NoTransitionPage"}

    def widgets_in(self, prog: DartProgram, r, ctx: Ctx) -> list[DClass]:
        out = []
        for x in walk_repr(r):
            if x.get("k") == "fn":
                for ret in x.get("ret") or []:
                    out.extend(self._screen_of(prog, ret, ctx))
        return out

    def _screen_of(self, prog: DartProgram, ret, ctx: Ctx, depth=0) -> list[DClass]:
        """The widget a route builder returns; a Page wrapper (`MaterialPage(child: X)`, a custom
        `CustomTransitionPage` subclass) is looked through to its `child:`."""
        if not isinstance(ret, dict) or ret.get("k") not in ("new", "call") or depth > 4:
            return []
        name = (ctor_type(ret) or "").split(".")[-1]
        tg = [t for t, conf, via in prog.resolve_call(ret, ctx) if isinstance(t, DClass)]
        is_page = name in self.PAGE_WRAPPERS or any(prog.ext_base_names(c) & self.PAGE_WRAPPERS for c in tg)
        child = (ret.get("na") or {}).get("child")
        if is_page and child is not None:
            inner = self._screen_of(prog, child, ctx, depth + 1)
            if not inner and isinstance(child, dict) and child.get("k") in ("new", "call") and \
                    (ctor_type(child) or "").split(".")[-1] == "Builder":
                inner = self.widgets_in(prog, (child.get("na") or {}).get("builder"), ctx)
            if inner:
                return inner
        return tg

    def navigation(self, prog: DartProgram, b: GraphBuilder, st: dict) -> None:
        widget_ids = {c.id for c in prog.classes.values()
                      if (prog.ext_base_names(c) | {x.name for x in prog.mro(c)}) & WIDGET_BASES}
        ev = UrlEval(prog, {})
        named_pages: dict[str, str] = {}
        go_names: dict[str, str] = {}

        # the extractor caps expression depth, so a deep route tree is truncated inside its root's repr;
        # every call is also emitted as its own fact, so swap each nested call for its own full repr.
        full_calls: dict = {}

        def go_tree(r, prefix: str, ctx: Ctx, file, depth=0):
            if not isinstance(r, dict) or depth > 60:
                return
            if r.get("k") in ("new", "call") and r.get("l"):
                r = full_calls.get((file, r.get("l"), r.get("n")), r)
            if (ctor_type(r) or "").split(".")[-1] == "GoRoute":
                na = r.get("na") or {}
                p = ev.eval(na["path"], ctx).text if na.get("path") else ""
                full = p if p.startswith("/") else (prefix.rstrip("/") + "/" + p if p else prefix)
                full = gopath(full or "/")
                ws = self.widgets_in(prog, {"k": "list", "items": [na.get("builder"), na.get("pageBuilder")]}, ctx)
                pid = None
                for w in ws[:1] or [None]:
                    pid = self.page(b, prog, w, full, file, r.get("l"), "GoRoute")
                    st["pages"] += 1
                named_pages[full] = pid
                if na.get("name") and (na["name"].get("k") == "str"):
                    go_names[na["name"]["v"]] = pid
                for ch in (na.get("routes") or {}).get("items") or []:
                    go_tree(ch, full, ctx, file, depth + 1)
                return
            for k_, v in r.items():
                if k_ in ("l", "k"):
                    continue
                if isinstance(v, dict):
                    go_tree(v, prefix, ctx, file, depth + 1)
                elif isinstance(v, list):
                    for x in v:
                        go_tree(x, prefix, ctx, file, depth + 1)

        # route tables (functions, field initialisers, top-level vars)
        sources = []
        for fn in prog.funcs.values():
            sources.append((fn.facts, prog.ctx_of(fn), fn.file, fn))
        for c in prog.classes.values():
            sources.append(([{"ft": "init", "v": v.init} for v in c.fields.values() if v.init], Ctx(prog, c.lib, None, c), c.file, None))
        for v in prog.vars:
            sources.append(([{"ft": "init", "v": v.init}], Ctx(prog, v.lib, None, None), v.file, None))
        for facts, ctx, file, fn in sources:
            for f in facts:
                if f.get("ft") in ("new", "call") and f.get("l"):
                    full_calls.setdefault((file, f.get("l"), f.get("n")), f)
        GO_TYPES = ("GoRouter", "ShellRoute", "StatefulShellRoute", "StatefulShellBranch", "GoRoute")
        # go_router trees: facts can list an inner GoRoute before the router that contains it, so first
        # collect every route constructor nested inside another one and only walk the outermost trees.
        nested_go: set = set()
        for facts, ctx, file, fn in sources:
            for f in facts:
                r = f["v"] if f["ft"] == "init" else (f if f["ft"] in ("new", "call") else None)
                if not isinstance(r, dict):
                    continue
                for x in walk_repr(r):
                    if (ctor_type(x) or "").split(".")[-1] in GO_TYPES:
                        for y in walk_repr(x):
                            if y is not x and (ctor_type(y) or "").split(".")[-1] in GO_TYPES and y.get("l"):
                                nested_go.add((file, y.get("l"), y.get("c")))
        seen_go = set()
        for facts, ctx, file, fn in sources:
            for f in facts:
                roots = [f["v"]] if f["ft"] == "init" else ([f] if f["ft"] in ("new", "call") else [])
                for r in roots:
                    if not isinstance(r, dict):
                        continue
                    for x in walk_repr(r):
                        k = x.get("k")
                        tname = (ctor_type(x) or "").split(".")[-1] or None
                        if tname in GO_TYPES:
                            key = (file, x.get("l"), x.get("c"))
                            if key in seen_go or key in nested_go:
                                continue
                            seen_go.add(key)
                            for y in walk_repr(x):
                                if y.get("k") in ("new", "call") and y.get("l"):
                                    seen_go.add((file, y.get("l"), y.get("c")))
                            go_tree(x, "", ctx, file)
                        if tname in APP_TYPES:
                            na = x.get("na") or {}
                            routes = na.get("routes") or {}
                            for el in routes.get("entries") or []:
                                kk = el.get("key") or {}
                                path = kk.get("v") if kk.get("k") == "str" else ev.eval(kk, ctx).text if kk else None
                                ws = self.widgets_in(prog, el.get("value"), ctx)
                                pid = self.page(b, prog, ws[0] if ws else None, path, file, el.get("l"), "routes") if (ws or path) else None
                                if pid:
                                    named_pages[path] = pid
                                    st["pages"] += 1
                            if na.get("home") and na["home"].get("k") in ("new", "call"):
                                for tgt, conf, via in prog.resolve_call(na["home"], ctx):
                                    if isinstance(tgt, DClass):
                                        named_pages.setdefault("/", self.page(b, prog, tgt, "/", file, x.get("l"), "home"))
                                        st["pages"] += 1
        for c in prog.classes.values():
            if any(a.split(".")[-1] in ("RoutePage", "AutoRoutePage") for a in c.ann):
                self.page(b, prog, c, None, c.file, c.line, "@RoutePage")
                st["pages"] += 1
        # navigation calls
        for fn in prog.funcs.values():
            ctx = prog.ctx_of(fn)
            for f in fn.facts:
                k = f.get("k")
                ct = (ctor_type(f) or "").split(".")[-1]
                if ct not in ROUTE_TYPES and f.get("ft") in ("new", "call"):
                    # project route classes (`class ScreenRoute extends MaterialPageRoute`) and route helpers
                    # (`Route<T> buildScreenRoute({required Widget screen})`) construct a route as well
                    for tgt, conf, via in prog.resolve_call(f, ctx):
                        if isinstance(tgt, DClass) and prog.ext_base_names(tgt) & ROUTE_BASES:
                            ct = tgt.name
                        elif isinstance(tgt, DFunc) and split_type(tgt.raw.get("ret") or "")[0].split(".")[-1] in ROUTE_BASES:
                            ct = tgt.name
                        else:
                            continue
                        break
                    else:
                        ct = None
                if ct:
                    na_vals = list((f.get("na") or {}).values()) + list(f.get("a") or [])
                    ws = self.widgets_in(prog, {"k": "list", "items": na_vals}, ctx)
                    if ct not in ROUTE_TYPES:
                        # a widget passed directly (`screen: GameScreen(...)`)
                        for v in na_vals:
                            if isinstance(v, dict) and v.get("k") in ("new", "call"):
                                ws += [t for t, _c, _v in prog.resolve_call(v, ctx) if isinstance(t, DClass) and t.id in widget_ids]
                    for w in ws:
                        pid = self.page(b, prog, w, None, fn.file, f.get("l"), ct)
                        b.add_edge(fn.id, pid, "NAVIGATES_TO", fn.file, f.get("l"), EXACT, via=ct)
                        st["navigations"] += 1
                    continue
                if f["ft"] != "call":
                    continue
                n, t, a = f.get("n"), f.get("t"), f.get("a") or []
                rn = root_name(t) if t else None
                path_r = None
                if n in PUSH_NAMED and (rn == "Navigator" or rn == "Get"):
                    path_r = a[1] if rn == "Navigator" and len(a) > 1 and t.get("k") == "id" else (a[0] if a else None)
                elif n in GO and t is not None and (rn in ("context", "ctx", "GoRouter", "router", "_router", "appRouter") or
                                                     (t.get("k") == "call" and t.get("n") == "of" and root_name(t.get("t")) == "GoRouter")):
                    path_r = a[0] if a else None
                if path_r is None:
                    continue
                if n.endswith("Named") and rn != "Navigator" and rn != "Get":
                    nm = path_r.get("v") if path_r.get("k") == "str" else None
                    pid = go_names.get(nm) if nm else None
                    route = None
                else:
                    route = gopath(ev.eval(path_r, ctx).text).split("?")[0]
                    pid = named_pages.get(route)
                    if pid is None:
                        for pth, p_ in named_pages.items():
                            if pth and re.fullmatch(re.sub(r"\\\{\w+\\\}", "[^/]+", re.escape(pth)), route):
                                pid = p_
                                break
                if pid is None and route and re.fullmatch(r"/[\w{}\-./:]*", route):
                    pid = b.add_node("page", f"dart::{route}", route, lang="dart", attrs={"route": route, "unresolved": True})
                if pid:
                    b.add_edge(fn.id, pid, "NAVIGATES_TO", fn.file, f.get("l"), EXACT if route in named_pages else HEURISTIC, via=n)
                    st["navigations"] += 1

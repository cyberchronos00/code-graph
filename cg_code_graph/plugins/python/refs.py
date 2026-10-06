"""Python entry points and function references (stdlib `ast` + `tomllib`, no code execution).

Entry points (`script` nodes, CALLS to what they run):
  * `if __name__ == "__main__":` blocks -> `script:<module>` (entry `main`, "python -m pkg.mod");
  * `pkg/__main__.py` -> `script:pkg.__main__` (entry `main`, "python -m pkg"), its whole module body;
  * packaging entry points: PEP 621 `[project.scripts]` / `[project.gui-scripts]` / `[project.entry-points.<group>]`,
    Poetry `[tool.poetry.scripts]` / `[tool.poetry.plugins.<group>]`, `setup.cfg [options.entry_points]` and a
    literal `setup(entry_points=...)` -> `script:<group>:<name>` (entry `main` for console / GUI scripts,
    `public_api` for plugin groups such as `pytest11`), resolved through the detected source roots.
  * registrations with well-known frameworks: MCP servers (`@mcp.tool()`, `mcp.tool()(fn)`, `add_tool`) ->
    `message_handler`; click / typer / Flask CLI commands -> `cli_command`. A local decorator that wraps its argument
    and registers the wrapper passes the registration on to every function it decorates.

Function references (REFERENCES_FN, propagating, attrs.how):
  * collection  - an element of a tuple / list / set / dict literal (dispatch tables, module constants);
  * callback    - an argument to a call (`map(f, xs)`, `Thread(target=f)`, `atexit.register(f)`, `run(f, x)`);
  * assignment  - assigned, returned or used as a default value (`handler = f`, `return f`);
  * decorator   - the decorated function, from the decorator that receives it (a local decorator function, a
                  local registry method, `@group.command()` on a function object) or, for an external registering
                  decorator (`@app.route`, `@registry.register`), from the module (heuristic).
Decorator applications are calls too: `@deco` adds CALLS module -> deco (attrs.via = "decorator").
"""
from __future__ import annotations

import ast
import configparser
import re
from pathlib import Path

from ...blindspots import REGISTER_DECOS, ROUTE_DECOS
from ...core.model import EXACT, HEURISTIC, RESOLVED

MCP_SERVERS = ("FastMCP", "MCPServer")
MCP_DECOS = {"tool", "resource", "prompt"}
MCP_ADDERS = {"add_tool": "tool", "add_resource": "resource", "add_prompt": "prompt"}
CLICK_LIBS = ("click", "asyncclick", "rich_click", "cloup")
COLLECTION_ADDS = {"append", "appendleft", "add", "insert", "extend", "setdefault"}
SCRIPT_GROUPS = {"console_scripts": "console script", "gui_scripts": "GUI script"}


def is_main_guard(test) -> bool:
    """`__name__ == "__main__"` (either side)."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
        return False
    sides = [test.left, test.comparators[0]]
    return any(isinstance(x, ast.Name) and x.id == "__name__" for x in sides) and \
        any(isinstance(x, ast.Constant) and x.value == "__main__" for x in sides)


# --------------------------------------------------------------------------- packaging entry points

def _line_of(text: str, name: str) -> int:
    pat = re.compile(r"^\s*['\"]?" + re.escape(name) + r"['\"]?\s*[=:]", re.M)
    m = pat.search(text)
    return text.count("\n", 0, m.start()) + 1 if m else 1


def _ini_entries(text: str) -> list[tuple[str, str]]:
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if "=" in ln and not ln.startswith(("#", ";", "[")):
            k, v = ln.split("=", 1)
            out.append((k.strip(), v.strip()))
    return out


def _pyproject_entries(text: str) -> list[tuple[str, str, str, str]]:
    """(group, name, target, declared_in)"""
    try:
        import tomllib
        d = tomllib.loads(text)
    except Exception:  # noqa: BLE001  (invalid TOML: no entry points from this file)
        return []
    out = []
    proj = d.get("project") if isinstance(d.get("project"), dict) else {}
    for key, group in (("scripts", "console_scripts"), ("gui-scripts", "gui_scripts")):
        for k, v in (proj.get(key) or {}).items() if isinstance(proj.get(key), dict) else ():
            if isinstance(v, str):
                out.append((group, k, v, f"[project.{key}]"))
    eps = proj.get("entry-points") if isinstance(proj.get("entry-points"), dict) else {}
    for group, items in eps.items():
        for k, v in (items or {}).items() if isinstance(items, dict) else ():
            if isinstance(v, str):
                out.append((group, k, v, f"[project.entry-points.{group}]"))
    tool = d.get("tool") if isinstance(d.get("tool"), dict) else {}
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    for k, v in (poetry.get("scripts") or {}).items() if isinstance(poetry.get("scripts"), dict) else ():
        if isinstance(v, dict):
            v = v.get("reference") or v.get("callable") if v.get("type", "console") == "console" else None
        if isinstance(v, str):
            out.append(("console_scripts", k, v, "[tool.poetry.scripts]"))
    for group, items in (poetry.get("plugins") or {}).items() if isinstance(poetry.get("plugins"), dict) else ():
        for k, v in (items or {}).items() if isinstance(items, dict) else ():
            if isinstance(v, str):
                out.append((group, k, v, f"[tool.poetry.plugins.{group}]"))
    flit = tool.get("flit") if isinstance(tool.get("flit"), dict) else {}
    for k, v in (flit.get("scripts") or {}).items() if isinstance(flit.get("scripts"), dict) else ():
        if isinstance(v, str):
            out.append(("console_scripts", k, v, "[tool.flit.scripts]"))
    return out


def _setup_cfg_entries(text: str) -> list[tuple[str, str, str, str]]:
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(text)
    except configparser.Error:
        return []
    if not cp.has_section("options.entry_points"):
        return []
    out = []
    for group, val in cp.items("options.entry_points"):
        for k, v in _ini_entries(val):
            out.append((group, k, v, "[options.entry_points]"))
    return out


def _setup_py_entries(text: str) -> list[tuple[str, str, str, str]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    consts = {}
    for st in tree.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            consts[st.targets[0].id] = st.value
    out = []

    def strs(v):
        if isinstance(v, ast.Name):
            v = consts.get(v.id)
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            return [x for x in v.value.splitlines() if x.strip()]
        if isinstance(v, (ast.List, ast.Tuple)):
            return [x.value for x in v.elts if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        return []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fname = n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else ""
        if fname != "setup":
            continue
        for kw in n.keywords:
            if kw.arg != "entry_points":
                continue
            v = consts.get(kw.value.id) if isinstance(kw.value, ast.Name) else kw.value
            if isinstance(v, ast.Dict):
                for k, items in zip(v.keys, v.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        for line in strs(items):
                            if "=" in line:
                                a, b = line.split("=", 1)
                                out.append((k.value, a.strip(), b.strip(), "setup(entry_points=...)"))
            elif isinstance(v, ast.Constant) and isinstance(v.value, str):   # ini-style string
                group = None
                for line in v.value.splitlines():
                    line = line.strip()
                    if line.startswith("[") and line.endswith("]"):
                        group = line[1:-1].strip()
                    elif group and "=" in line:
                        a, b = line.split("=", 1)
                        out.append((group, a.strip(), b.strip(), "setup(entry_points=...)"))
    return out


def packaging_entry_points(root: Path, project_dirs: list[str]) -> list[dict]:
    out = []
    for pd in project_dirs:
        base = root / pd if pd else root
        for fn, parse in (("pyproject.toml", _pyproject_entries), ("setup.cfg", _setup_cfg_entries),
                          ("setup.py", _setup_py_entries)):
            p = base / fn
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = f"{pd}/{fn}" if pd else fn
            for group, name, target, where in parse(text):
                out.append({"project": pd, "file": rel, "line": _line_of(text, name), "group": group, "name": name,
                            "target": target, "declared_in": f"{rel} {where}"})
    return out


# --------------------------------------------------------------------------- the pass

class RefPass:
    def __init__(self, prog, b, ctx_cls):
        self.prog, self.b, self.Ctx = prog, b, ctx_cls
        self.fn_by_node = {id(f.node): f for f in prog.funcs.values()}
        self.cls_by_node = {id(c.node): c for c in prog.classes.values()}
        self.names = {f.name for f in prog.funcs.values()}
        self.stats = {"references": {}, "decorator_calls": 0, "main_blocks": 0, "entry_points": 0,
                      "entry_points_unresolved": [], "registrations": {}}
        self.registrars: dict = {}       # FuncInfo.id -> (FuncInfo, kind, attrs): wraps and registers its argument
        self.decorated_by: dict = {}     # FuncInfo.id -> [FuncInfo] decorated with it
        self.passed: list = []           # (call, ctx, FuncInfo) first argument of a call is a known function
        self.var_regs: dict = {}         # (module name, var) -> [FuncInfo] registered on that object (typer app)
        self.obj_calls: list = []        # (script id, (module name, var), file, line): a script calls a CLI object

    # ---- helpers
    def ref(self, src, f, how, file, line, conf=RESOLVED, **attrs):
        if src == f.id:
            return
        self.b.add_edge(src, f.id, "REFERENCES_FN", file, line, conf, how=how, **attrs)
        self.stats["references"][how] = self.stats["references"].get(how, 0) + 1

    def func_of(self, t):
        if t and t[0] in ("func", "bound"):
            return t[1]
        return None

    def value_ref(self, t):
        """The function a value expression refers to; None for a property (reading it runs the getter)."""
        f = self.func_of(t)
        if f is None or (t[0] == "bound" and is_property(f)):
            return None
        return f

    def set_entry(self, f, kind, attrs):
        n = self.b.nodes.get(f.id)
        if n is None:
            return
        reg = dict(attrs)
        if n.attrs is None:
            n.attrs = {}
        n.attrs.setdefault("registrations", [])
        if reg not in n.attrs["registrations"]:
            n.attrs["registrations"].append(reg)
        if n.entry_kind is None:
            n.entry_kind = kind
        elif n.entry_kind != kind:
            n.attrs["entry_kinds"] = sorted(set(n.attrs.get("entry_kinds", [n.entry_kind])) | {kind})
        key = f"{attrs.get('framework')}:{kind}"
        self.stats["registrations"][key] = self.stats["registrations"].get(key, 0) + 1

    # ---- framework registrations
    def registration(self, e, ctx):
        """`e` is a decorator expression (without its call) or the callee of a registering call: (kind, attrs)."""
        p = self.prog
        if not isinstance(e, ast.Attribute):
            return None
        attr = e.attr
        recv = p.infer(e.value, ctx)
        rname = recv[1] if recv and recv[0] in ("einst", "ext") and isinstance(recv[1], str) else ""
        last = rname.rsplit(".", 1)[-1]
        if recv and recv[0] == "einst" and last in MCP_SERVERS and attr in (MCP_DECOS | set(MCP_ADDERS)):
            return "message_handler", {"framework": "mcp", "registration": MCP_ADDERS.get(attr, attr)}
        full = p.infer(e, ctx)
        if full and full[0] == "ext" and attr in ("command", "group") and full[1].split(".")[0] in CLICK_LIBS:
            return "cli_command", {"framework": full[1].split(".")[0], "registration": attr}
        if attr in ("command", "group"):
            if recv and recv[0] == "einst" and rname.split(".")[0] in CLICK_LIBS:
                return "cli_command", {"framework": rname.split(".")[0], "registration": attr}
            g = self.func_of(recv)
            if g is not None and any(self._is_click(d, g) for d in g.decorators):
                return "cli_command", {"framework": "click", "registration": attr}
        if recv and recv[0] == "einst" and rname.split(".")[0] == "typer" and attr in ("command", "callback"):
            return "cli_command", {"framework": "typer", "registration": attr}
        if attr == "command" and (dotted(e.value) or "").endswith(".cli"):
            return "cli_command", {"framework": "flask", "registration": "cli.command"}
        return None

    def _is_click(self, d, g) -> bool:
        de = d.func if isinstance(d, ast.Call) else d
        t = self.prog.infer(de, self.Ctx(g.module, None, None))
        if t and t[0] == "ext" and t[1].split(".")[0] in CLICK_LIBS and t[1].rsplit(".", 1)[-1] in ("group", "command"):
            return True
        return bool(isinstance(de, ast.Attribute) and de.attr in ("group", "command") and
                    self.func_of(self.prog.infer(de.value, self.Ctx(g.module, None, None))) is not None)

    def _recv_var(self, e, ctx):
        """(module, var) of a module-level object a decorator registers on (`app` in `@app.command()`)."""
        if isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name):
            r = self.prog.resolve_name(ctx.mod, e.value.id)
            if r and r[0] == "var":
                return (r[1].name, r[2])
        return None

    # ---- decorators
    def decorator(self, d, target, owner_id, ctx, file):
        """`target` is the decorated FuncInfo, or None for a nested def (it collapses into the owner)."""
        p = self.prog
        de = d.func if isinstance(d, ast.Call) else d
        reg = self.registration(de, ctx)
        if reg:
            if target is not None:
                self.set_entry(target, reg[0], {**reg[1], "decorator": ann(d), "at": f"{file}:{d.lineno}"})
                rv = self._recv_var(de, ctx)
                if rv:
                    self.var_regs.setdefault(rv, []).append(target)
            elif ctx.func is not None:
                self.registrars.setdefault(ctx.func.id, (ctx.func, reg[0], {**reg[1], "decorator": ann(d)}))
        t = p.infer(de, ctx)
        dfn = self.func_of(t)
        if dfn is not None:
            self.b.add_edge(owner_id, dfn.id, "CALLS", file, d.lineno, EXACT if t[0] == "func" else RESOLVED, via="decorator")
            self.stats["decorator_calls"] += 1
            if target is not None:
                self.ref(dfn.id, target, "decorator", file, d.lineno)
                self.decorated_by.setdefault(dfn.id, []).append(target)
            return
        if target is None:
            return
        if isinstance(de, ast.Attribute):
            g = self.func_of(p.infer(de.value, ctx))
            if g is not None:      # @group.command() / @dispatcher.register: the function object holds the target
                self.ref(g.id, target, "decorator", file, d.lineno, via=de.attr)
                return
        if reg:
            return
        name = de.attr if isinstance(de, ast.Attribute) else de.id if isinstance(de, ast.Name) else ""
        has_recv = isinstance(de, ast.Attribute)
        if (has_recv and (name in ROUTE_DECOS or name in REGISTER_DECOS or name.startswith("register"))) or \
                (not has_recv and name in REGISTER_DECOS and t is not None):
            if "mock" in (dotted(de) or "").split(".") or (dotted(de) or "").startswith("patch"):
                return
            self.ref(owner_id, target, "decorator", file, d.lineno, HEURISTIC, decorator=ann(d), registry="external")

    # ---- scope walk
    def scan(self, owner_id, roots, ctx, file, module_scope: bool, calls: list | None = None):
        """roots: [(node, parent)] or [(node, parent, refs)]. Module scope stops at def bodies (decorators and defaults
        stay); function scope walks like `walk_body` (nested defs collapse into the owner, nested classes do not), and
        appends every Call to `calls` in walk_body order. refs=False: walk for calls only (a def's own decorators and
        defaults belong to the enclosing scope)."""
        p, names = self.prog, self.names
        Name, Attribute, Load, Call = ast.Name, ast.Attribute, ast.Load, ast.Call
        defs = (ast.FunctionDef, ast.AsyncFunctionDef)
        stack = [r if len(r) == 3 else (r[0], r[1], True) for r in roots]
        while stack:
            n, par, on = stack.pop()
            t = type(n)
            if t is Name or t is Attribute:
                if t is Attribute:
                    stack.append((n.value, n, on))
                if not on or type(n.ctx) is not Load or (n.id if t is Name else n.attr) not in names:
                    continue
                how = self._how(n, par)
                if how:
                    f = self.value_ref(p.infer(n, ctx))
                    if f is not None:
                        self.ref(owner_id, f, how, file, n.lineno)
                        if how == "callback" and type(par) is Call and par.args and par.args[0] is n:
                            self.passed.append((par, ctx, f))
                continue
            if t in defs:
                if module_scope:
                    tgt = self.fn_by_node.get(id(n))
                    for dd in n.decorator_list:
                        self.decorator(dd, tgt, owner_id, ctx, file)
                        stack.append((dd, n, on))
                    stack.extend((x, n.args, on) for x in n.args.defaults + n.args.kw_defaults if x is not None)
                    continue
                if on:
                    for dd in n.decorator_list:
                        self.decorator(dd, None, owner_id, ctx, file)
                stack.extend((c, n, on) for c in ast.iter_child_nodes(n))
                continue
            if t is ast.ClassDef:
                if module_scope:
                    cctx = self.Ctx(ctx.mod, None, self.cls_by_node.get(id(n)))
                    inner = n.decorator_list + n.bases + [k.value for k in n.keywords] + n.body
                    self.scan(owner_id, [(x, n) for x in inner], cctx, file, True)
                continue
            if t is Call:
                if calls is not None:
                    calls.append(n)
                if on:
                    self._registration_call(n, owner_id, ctx, file)
            stack.extend((c, n, on) for c in ast.iter_child_nodes(n))

    @staticmethod
    def _how(n, par):
        """How the function-valued expression `n` is used by its parent node (None: not a reference)."""
        tp = type(par)
        if tp in (ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Starred):
            return "collection"
        if tp is ast.Call:
            if par.func is n:
                return None
            if isinstance(par.func, ast.Attribute) and par.func.attr in COLLECTION_ADDS:
                return "collection"      # HANDLERS.append(f), REGISTRY.setdefault(k, f)
            return "callback"
        if tp is ast.keyword:
            return "callback"
        if tp in (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.Return, ast.Yield):
            return "assignment" if par.value is n else None
        if tp is ast.Lambda:
            return "assignment" if par.body is n else None
        if tp is ast.arguments:
            return "assignment"
        if tp is ast.IfExp:
            return "assignment" if par.test is not n else None
        if tp is ast.BoolOp:
            return "assignment"
        return None

    def _registration_call(self, call, owner_id, ctx, file):
        """`mcp.tool()(fn)`, `mcp.add_tool(fn)`, `app.command()(fn)`: fn (or the closure that wraps the owner's
        argument) becomes an entry point."""
        fn = call.func
        inner = fn.func if isinstance(fn, ast.Call) else fn if isinstance(fn, ast.Attribute) and fn.attr in MCP_ADDERS else None
        if inner is None or not call.args:
            return
        reg = self.registration(inner, ctx)
        if not reg:
            return
        a = call.args[0]
        f = self.func_of(self.prog.infer(a, ctx))
        attrs = {**reg[1], "call": ann(call)[:120], "at": f"{file}:{call.lineno}"}
        if f is not None:
            self.set_entry(f, reg[0], attrs)
        elif ctx.func is not None and isinstance(a, (ast.Name, ast.Lambda)):
            self.registrars.setdefault(ctx.func.id, (ctx.func, reg[0], attrs))

    def object_calls(self, sid, stmts, ctx, file):
        """`app()` / `cli()` in a script body where `app` is a module-level object (typer app, click group object)."""
        for st in stmts:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for sub in ast.walk(st):
                if isinstance(sub, ast.Call) and isinstance(sub.func, (ast.Name, ast.Attribute)):
                    e = sub.func
                    if isinstance(e, ast.Name):
                        r = self.prog.resolve_name(ctx.mod, e.id)
                    else:
                        base = self.prog.infer(e.value, ctx)
                        r = self.prog.lookup(base[1].name, e.attr) if base and base[0] == "mod" else None
                    if r and r[0] == "var":
                        self.obj_calls.append((sid, (r[1].name, r[2]), file, sub.lineno))

    def finish(self):
        """Registering wrappers: every function decorated with (or passed to) one is registered; a wrapper with no
        such use is itself the registered handler (its closure collapses into it)."""
        p = self.prog
        for sid, key, file, line in self.obj_calls:
            for g in self.var_regs.get(key, []):
                self.ref(sid, g, "callback", file, line, via="registered command")
        for fid, (F, kind, attrs) in self.registrars.items():
            targets = list(self.decorated_by.get(fid, []))
            for call, ctx, g in self.passed:
                if any(t is F for t, _c, _v in p.resolve_call(call, ctx)):
                    targets.append(g)
            if targets:
                for g in targets:
                    self.set_entry(g, kind, {**attrs, "registered_by": F.qual})
            else:
                self.set_entry(F, kind, {**attrs, "closure": True})


PROPERTY_DECOS = {"property", "cached_property", "setter", "getter", "deleter", "abstractproperty", "hybrid_property",
                  "classproperty", "lazy_property", "reify", "computed_field"}


def is_property(f) -> bool:
    cached = getattr(f, "_is_property", None)
    if cached is None:
        cached = False
        for d in f.decorators:
            name = (dotted(d.func if isinstance(d, ast.Call) else d) or "").rsplit(".", 1)[-1]
            if name in PROPERTY_DECOS or name.endswith("_property"):
                cached = True
                break
        f._is_property = cached
    return cached


def ann(e) -> str:
    try:
        return ast.unparse(e)
    except Exception:  # noqa: BLE001
        return ""


def dotted(e) -> str | None:
    parts = []
    while isinstance(e, ast.Attribute):
        parts.append(e.attr)
        e = e.value
    if isinstance(e, ast.Name):
        parts.append(e.id)
        return ".".join(reversed(parts))
    return None

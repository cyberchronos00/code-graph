"""Programs a function runs in a subprocess, linked to the project's entry point (stdlib `ast`, `shlex`; no code runs).

A call that starts a process (`subprocess.run / call / check_call / check_output / Popen / getoutput`,
`asyncio.create_subprocess_exec / _shell`, `os.system / popen / exec*`) with an argument list or command line cg can
evaluate statically gets CALLS (attrs.via = "subprocess", attrs.how) to what it runs, when that is part of the project:
  * `[sys.executable | "python" | "python3", "-m", "pkg.mod", ...]` (also `-mpkg.mod`, interpreter options such as
    `-u` / `-X dev` / `-W error` before it) -> `script:pkg.mod` (the `__main__` block), `script:pkg.__main__` for a
    package, else the module itself (its body runs);
  * `[python, "-c", "<code>"]` -> the functions / classes the snippet calls, its imports resolved like a module's;
  * `[python, "path/to/tool.py"]` -> that file's `__main__` block or module (matched by path suffix; a bare
    `tool.py` / `./tool.py` only at the project root, since it runs from an unknown working directory);
  * `["mytool", ...]` / `[shutil.which("mytool"), ...]` where `mytool` is a console / GUI script the project
    declares -> its `script:console_scripts:mytool` node.
Argument lists are evaluated from literals, `sys.executable`, `shutil.which(...)`, `str()` / `os.fspath()` /
`os.path.join()` / `Path(...) / "x.py"`, `+` and `*` concatenation, local and module variables, the return value of a
project function (`self._args() + args`) and command strings (`shell=True`, `os.system`) split like a shell. When
the program is a parameter (`def run_cli(*args): subprocess.run([sys.executable, "-m", "pkg.cli", *args])` has it
fixed, `def run(module, *args)` does not), the function is a runner and each call site that passes it is evaluated
with its arguments bound, through up to 5 helpers, so a project's own CLI test helper links every test that uses it.
`CliRunner().invoke(app, ...)` (click / typer testing) on a typer app or click group object gets REFERENCES_FN to
the commands registered on it (a click group function is already referenced as a callback).
Test code's edges become TEST_CALLS (tests_index.isolate_tests). Programs outside the project (`git`, `-m pip`) and
argument lists that cannot be evaluated add no edge (counted in the stats).
"""
from __future__ import annotations

import ast
import re
import shlex
from collections import defaultdict

from ...core.model import RESOLVED

SPAWN_ARGV = {"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"}
SPAWN_MODS = {"subprocess", "asyncio", "os", "anyio", "trio"}
SPAWN_VARARGS = {"create_subprocess_exec", "execl", "execlp", "execle", "spawnl", "spawnlp"}   # program, *args
SPAWN_SHELL = {"system", "popen", "create_subprocess_shell"}
SPAWN_LIST_AT1 = {"execv", "execvp", "execve", "execvpe", "spawnv", "spawnvp"}               # (path, argv)
PY_NAMES = re.compile(r"^(?:.*[/\\])?(?:python|pypy)(?:\d+(?:\.\d+)?)?(?:\.exe)?$")
PY_FLAGS_ARG = {"-W", "-X", "--check-hash-based-pycs"}
PY_SHORT = set("uEIBOsSbqPdvRih")
MAX_HOPS = 5
# installed test-runner helpers whose program is a parameter: (class, method) -> argument shape. Their source is not in
# the project, so a small table stands in for following it.
EXT_RUNNERS = {
    ("scripttest.TestFileEnvironment", "run"): "varargs",          # env.run("mytool", "arg"); pip's script.run
    ("_pytest.pytester.Pytester", "run"): "varargs",              # pytester.run(sys.executable, "-m", "pkg")
    ("pytest.Pytester", "run"): "varargs",
    ("_pytest.pytester.Testdir", "run"): "varargs",
    ("pytest.Testdir", "run"): "varargs",
}
PYTESTER_FIXTURES = {"pytester", "testdir"}
COPY_FUNCS = {"copy", "copy2", "copyfile"}
UNK = ("unk",)
PY = ("py",)


class Thunk:
    """An argument expression to evaluate in its caller's context (with the caller's own bindings)."""
    __slots__ = ("e", "ctx", "env", "spread", "line")

    def __init__(self, e, ctx, env, spread=False, line=None):
        self.e, self.ctx, self.env, self.spread = e, ctx, env, spread
        self.line = line if line is not None else getattr(e, "lineno", None)


class Subprocesses:
    def __init__(self, prog, b, Ctx, FuncInfo, ClassInfo, dotted, walk_body, rp=None, root=None):
        self.rp, self.root = rp, root
        self._files_by_name = None
        self.prog, self.b, self.Ctx, self.FuncInfo, self.ClassInfo = prog, b, Ctx, FuncInfo, ClassInfo
        self.dotted, self.walk_body = dotted, walk_body
        self.st = defaultdict(int)
        self.samples = defaultdict(list)
        self.runners: dict = {}            # func id -> [(kind, payload)]: spawn argv / call to another runner
        self.by_file = {m.file: m for m in prog.modules.values()}
        self.scripts = defaultdict(list)   # console / GUI script name -> script node ids
        for nid, n in b.nodes.items():
            a = n.attrs or {}
            if n.kind == "script" and a.get("group") in ("console_scripts", "gui_scripts") and a.get("script"):
                self.scripts[a["script"]].append(nid)

    # ------------------------------------------------------------------ recognising a process start
    def spawn(self, call, ctx):
        """(argv thunk list | None, shell: bool) when `call` starts a process, else None."""
        fn = call.func
        got = self.lib_spawn(call, ctx)
        if got is not None:
            return got
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else None
        if name is None:
            return None
        allowed = SPAWN_ARGV | SPAWN_VARARGS | SPAWN_SHELL | SPAWN_LIST_AT1
        if name not in allowed:
            return None
        if isinstance(fn, ast.Attribute):
            head = self.dotted(fn.value)
            if head is None:
                return None
            r = self.prog.resolve_name(ctx.mod, head.split(".")[0])
            mod = head if r is None else (r[1].name if r[0] == "mod" else r[1] if r[0] == "ext" else None)
            if not isinstance(mod, str) or mod.split(".")[0] not in SPAWN_MODS:
                return None
            mod = mod.split(".")[0]
        else:
            imp = ctx.mod.imports.get(name)
            if not (imp and imp[0] == "sym" and imp[1].split(".")[0] in SPAWN_MODS):
                return None
            mod = imp[1].split(".")[0]
        if name in ("run", "call") and mod not in ("subprocess",):
            return None              # trio.run(f), anyio.run(f): run a coroutine, not a process
        if name in SPAWN_VARARGS:
            return ([Thunk(a.value if isinstance(a, ast.Starred) else a, ctx, None, isinstance(a, ast.Starred))
                     for a in call.args], False)
        if name in SPAWN_LIST_AT1:
            return ([Thunk(call.args[1], ctx, None, True)] if len(call.args) > 1 else None, False)
        argv = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg in ("args", "cmd")), None)
        if argv is None:
            return None
        shell = name in SPAWN_SHELL or name in ("getoutput", "getstatusoutput") or any(
            k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value for k in call.keywords)
        return ([Thunk(argv, ctx, None, True)], shell)

    def varargs(self, call, ctx, head=()):
        return (list(head) + [Thunk(a.value if isinstance(a, ast.Starred) else a, ctx, None, isinstance(a, ast.Starred))
                              for a in call.args], False)

    def lib_spawn(self, call, ctx):
        """Process starts through installed helpers: the EXT_RUNNERS table, `sh`, `plumbum`."""
        fn, prog = call.func, self.prog
        if isinstance(fn, ast.Attribute):
            # (class, method) table: the receiver's inferred class or one of its external bases
            if fn.attr in {m for _c, m in EXT_RUNNERS}:
                try:
                    t = prog.infer(fn.value, ctx)
                except RecursionError:
                    t = None
                classes = set()
                if isinstance(fn.value, ast.Call) and isinstance(fn.value.func, ast.Name) \
                        and fn.value.func.id == "super" and ctx.cls is not None:
                    classes |= prog.ext_bases(ctx.cls)          # super().run(...) in a subclass of the runner
                elif t and t[0] == "einst":
                    classes.add(t[1])
                elif t and t[0] in ("inst", "type") and prog.find_method(t[1], fn.attr) is None:
                    classes |= prog.ext_bases(t[1])         # not overridden in the project
                elif t and t[0] == "super":
                    classes |= prog.ext_bases(t[1])
                elif isinstance(fn.value, ast.Name) and fn.value.id in PYTESTER_FIXTURES and ctx.func is not None \
                        and any(a.arg == fn.value.id for a in ctx.func.node.args.args) \
                        and prog.module("_pytest.pytester") is None:
                    classes.add("_pytest.pytester.Pytester")      # the pytest fixture, pytest installed
                if any((c, fn.attr) in EXT_RUNNERS for c in classes):
                    self.st["external_runner_calls"] += 1
                    return self.varargs(call, ctx)
            # sh: sh.mytool(*args), sh.Command("mytool")(*args) handled below
            head = self.dotted(fn.value)
            if head and self.ext_root(head, ctx) == "sh" and fn.attr not in ("Command", "ErrorReturnCode"):
                self.st["sh_calls"] += 1
                return self.varargs(call, ctx, [Thunk(ast.Constant(fn.attr.replace("_", "-")), ctx, None)])
        if isinstance(fn, ast.Name):
            imp = ctx.mod.imports.get(fn.id)
            if imp and imp[0] == "sym" and imp[1].split(".")[0] == "sh" and fn.id != "Command":
                self.st["sh_calls"] += 1
                return self.varargs(call, ctx, [Thunk(ast.Constant(imp[2].replace("_", "-")),
                                                      ctx, None)])
        if isinstance(fn, ast.Call) and fn.args:
            d = self.dotted(fn.func) or ""
            if d.rsplit(".", 1)[-1] == "Command" and self.ext_root(d, ctx) == "sh":
                self.st["sh_calls"] += 1
                return self.varargs(call, ctx, [Thunk(fn.args[0], ctx, None)])
        # plumbum: local["prog"]["a", "b"](...), local["prog"].run(...) / .popen(...)
        chain, e = [], fn
        if isinstance(e, ast.Attribute) and e.attr in ("run", "popen", "run_fg", "run_bg", "run_retcode", "run_tee"):
            e = e.value
        while isinstance(e, ast.Subscript):
            chain.append(e.slice)
            e = e.value
        if chain:
            d = self.dotted(e) or ""
            if d.rsplit(".", 1)[-1] == "local" and self.ext_root(d, ctx) == "plumbum":
                head = []
                for sl in reversed(chain):
                    for el in (sl.elts if isinstance(sl, ast.Tuple) else [sl]):
                        head.append(Thunk(el.value if isinstance(el, ast.Starred) else el, ctx, None,
                                          isinstance(el, ast.Starred)))
                self.st["plumbum_calls"] += 1
                return self.varargs(call, ctx, head)
        return None

    def ext_root(self, dotted_name: str, ctx) -> str | None:
        """Top-level installed package a dotted name comes from (`sh.git` -> "sh", `local` from plumbum)."""
        first = dotted_name.split(".")[0]
        imp = ctx.mod.imports.get(first)
        if imp:
            if self.prog.module(imp[1]) is not None or self.prog.module(imp[1].split(".")[0]) is not None:
                return None                      # a project module of that name
            return imp[1].split(".")[0]
        return None

    # ------------------------------------------------------------------ evaluation
    def seq(self, t: Thunk, depth=0) -> list:
        """Items of a sequence-valued thunk: ("lit", s) | PY | UNK | ("param", name) (a parameter in runner mode)."""
        if not t.spread:
            return [self.scalar(t, depth)]
        e, ctx, env = t.e, t.ctx, t.env
        if depth > 12:
            return [UNK]
        if isinstance(e, (ast.List, ast.Tuple)):
            out = []
            for el in e.elts:
                if isinstance(el, ast.Starred):
                    out += self.seq(Thunk(el.value, ctx, env, True, t.line), depth + 1)
                else:
                    out.append(self.scalar(Thunk(el, ctx, env, False, t.line), depth + 1))
            return out
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            return self.seq(Thunk(e.left, ctx, env, True, t.line), depth + 1) + \
                self.seq(Thunk(e.right, ctx, env, True, t.line), depth + 1)
        if isinstance(e, ast.ListComp) and len(e.generators) == 1 and not e.generators[0].ifs:
            return self.seq(Thunk(e.generators[0].iter, ctx, env, True, t.line), depth + 1)
        if isinstance(e, ast.Name):
            got = self.name(e.id, ctx, env, True, depth, t.line)
            if got is not None:
                return got
            return [UNK]
        if isinstance(e, ast.Call):
            d = self.dotted(e.func) or ""
            if d in ("list", "tuple", "sorted") and e.args:
                a0 = e.args[0]
                if isinstance(a0, (ast.GeneratorExp, ast.ListComp)) and len(a0.generators) == 1 \
                        and not a0.generators[0].ifs:
                    a0 = a0.generators[0].iter      # tuple(os.fspath(a) for a in cmdargs): same items, converted
                return self.seq(Thunk(a0, ctx, env, True, t.line), depth + 1)
            if d in ("shlex.split",) and e.args:
                s = self.scalar(Thunk(e.args[0], ctx, env), depth + 1)
                return self.split(s)
            for rv, rctx in self.prog.returned(e, ctx)[:2]:
                return self.seq(Thunk(rv, rctx, {}, True), depth + 1)
            return [UNK]
        s = self.scalar(Thunk(e, ctx, env), depth + 1)
        return self.split(s) if s[0] == "lit" else [s]

    @staticmethod
    def split(s) -> list:
        if s[0] != "lit":
            return [s]
        try:
            parts = shlex.split(s[1])
        except ValueError:
            parts = s[1].split()
        return [PY if PY_NAMES.match(p) and not p.endswith(".py") else ("lit", p) for p in parts] or [UNK]

    def name(self, nm, ctx, env, as_seq, depth, line=None):
        if env is not None and nm in env:
            v = env[nm]
            if isinstance(v, list):          # a *args parameter: the call site's extra positional arguments
                out = []
                for th in v:
                    out += self.seq(th, depth + 1)
                return out if as_seq else None
            return self.seq(Thunk(v.e, v.ctx, v.env, as_seq, v.line), depth + 1) if as_seq else [self.scalar(v, depth + 1)]
        if ctx.func is not None:
            lv = self.prog.local_vars(ctx).get(nm)
            if not lv and self.is_vararg(ctx.func, nm):
                return [("param", nm)] if env is None else [UNK]
            if lv:
                # the last assignment before the use (`args = self._base() + args` reads the previous `args`)
                assigns = sorted(((val.lineno, i, val) for i, (val, _a, kind) in enumerate(lv)
                                  if kind == "assign" and val is not None), key=lambda x: (x[0], x[1]))
                before = [a for a in assigns if line is None or a[0] < line]
                if before:
                    val = before[-1][2]
                    th = Thunk(val, ctx, env, as_seq, val.lineno)
                    if not as_seq:
                        return [self.scalar(th, depth + 1)]
                    return self.mutated(nm, ctx, env, val.lineno, line, self.seq(th, depth + 1), depth)
                if any(kind == "param" for _v, _a, kind in lv) or self.is_vararg(ctx.func, nm):
                    return [("param", nm)] if env is None else [UNK]
                if assigns:
                    th = Thunk(assigns[0][2], ctx, env, as_seq)
                    return self.seq(th, depth + 1) if as_seq else [self.scalar(th, depth + 1)]
                return None
        r = self.prog.resolve_name(ctx.mod, nm)
        if r and r[0] == "var":
            vals = r[1].vars.get(r[2]) or ()
            if vals and vals[0][0] is not None:
                th = Thunk(vals[0][0], self.Ctx(r[1], None, None), {}, as_seq)
                return self.seq(th, depth + 1) if as_seq else [self.scalar(th, depth + 1)]
        return None

    def mutations(self, f) -> dict:
        """name -> [(line, op, args, loop)] for `x.append(a)`, `x.extend(s)`, `x.insert(i, a)`, `x += s` in `f`
        (`loop` is the enclosing for / while statement, None outside loops); nested defs are skipped."""
        cache = self.__dict__.setdefault("_mut", {})
        if id(f.node) in cache:
            return cache[id(f.node)]
        out = defaultdict(list)

        def visit(node, loop):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                if isinstance(ch, ast.AugAssign) and isinstance(ch.op, ast.Add) and isinstance(ch.target, ast.Name):
                    out[ch.target.id].append((ch.lineno, "extend", [ch.value], loop))
                elif isinstance(ch, ast.Call) and isinstance(ch.func, ast.Attribute) \
                        and isinstance(ch.func.value, ast.Name) and ch.func.attr in ("append", "extend", "insert"):
                    out[ch.func.value.id].append((ch.lineno, ch.func.attr, ch.args, loop))
                visit(ch, ch if isinstance(ch, (ast.For, ast.AsyncFor, ast.While)) else loop)
        visit(f.node, None)
        cache[id(f.node)] = out
        return out

    def mutated(self, nm, ctx, env, start, line, items, depth):
        """`items` (the list assigned at line `start`) after the appends / extends / `+=` between it and `line`.
        A mutation inside a loop adds the loop's items when it appends the loop variable of a `for` over a list
        cg can evaluate; otherwise it adds one unknown item (the count is not known)."""
        muts = [m for m in self.mutations(ctx.func).get(nm, ()) if m[0] > start and (line is None or m[0] < line)]
        if not muts:
            return items
        items = list(items)
        for _ln, op, args, loop in sorted(muts, key=lambda m: m[0]):
            if not args:
                continue
            if loop is not None:
                a0 = args[-1]
                if op == "append" and isinstance(loop, (ast.For, ast.AsyncFor)) and isinstance(loop.target, ast.Name) \
                        and isinstance(a0, ast.Name) and a0.id == loop.target.id:
                    got = self.seq(Thunk(loop.iter, ctx, env, True, loop.lineno), depth + 1)
                    items += got if UNK not in got else [UNK]
                else:
                    items.append(UNK)
                self.st["argv_loop_mutations"] += 1
                continue
            if op == "append":
                items.append(self.scalar(Thunk(args[0], ctx, env), depth + 1))
            elif op == "extend":
                items += self.seq(Thunk(args[0], ctx, env, True), depth + 1)
            elif op == "insert" and len(args) == 2:
                i = args[0].value if isinstance(args[0], ast.Constant) and isinstance(args[0].value, int) else None
                v = self.scalar(Thunk(args[1], ctx, env), depth + 1)
                if i is None:
                    items.append(UNK)
                else:
                    items.insert(i, v)
            self.st["argv_mutations"] += 1
        return items

    @staticmethod
    def is_vararg(f, nm) -> bool:
        a = f.node.args
        return any(x is not None and x.arg == nm for x in (a.vararg, a.kwarg))

    def scalar(self, t: Thunk, depth=0):
        e, ctx, env = t.e, t.ctx, t.env
        if depth > 12:
            return UNK
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            return ("lit", e.value)
        d = self.dotted(e) if isinstance(e, (ast.Name, ast.Attribute)) else None
        if d in ("sys.executable",) or (d and d.endswith(".executable") and d.split(".")[0] == "sys"):
            return PY
        if isinstance(e, ast.Name) and e.id == "__file__":
            return ("lit", ctx.mod.file)                       # project-relative path of this module
        if isinstance(e, ast.Attribute) and e.attr == "__file__" and isinstance(e.value, ast.Name):
            r = self.prog.resolve_name(ctx.mod, e.value.id)
            if r and r[0] == "mod":
                return ("lit", r[1].file)                       # `from django import conf; conf.__file__`
        if isinstance(e, ast.Attribute) and e.attr == "parent":
            a = self.scalar(Thunk(e.value, ctx, env, False, t.line), depth + 1)
            return ("lit", a[1].rsplit("/", 1)[0] if "/" in a[1] else "") if a[0] == "lit" and "{?}" not in a[1] else UNK
        if isinstance(e, ast.Name):
            got = self.name(e.id, ctx, env, False, depth, t.line)
            return got[0] if got else UNK
        if isinstance(e, ast.IfExp):          # a value or its fallback: the first one cg can evaluate
            a = self.scalar(Thunk(e.body, ctx, env, False, t.line), depth + 1)
            return a if a[0] == "lit" and "{?}" not in a[1] else self.scalar(Thunk(e.orelse, ctx, env, False, t.line), depth + 1)
        if isinstance(e, ast.JoinedStr):
            parts = []
            for v in e.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                else:
                    s = self.scalar(Thunk(v.value, ctx, env), depth + 1)
                    parts.append(sys_exe_text(s))
            return ("lit", "".join(parts))
        if isinstance(e, ast.BinOp) and isinstance(e.op, (ast.Add, ast.Div)):
            a, c = self.scalar(Thunk(e.left, ctx, env), depth + 1), self.scalar(Thunk(e.right, ctx, env), depth + 1)
            if c[0] != "lit":
                return UNK
            sep = "/" if isinstance(e.op, ast.Div) else ""
            return ("lit", (a[1] if a[0] == "lit" else "{?}") + sep + c[1])
        if isinstance(e, ast.Call):
            fd = self.dotted(e.func) or ""
            last = fd.rsplit(".", 1)[-1]
            if last == "which" and e.args:
                s = self.scalar(Thunk(e.args[0], ctx, env), depth + 1)
                return PY if s[0] == "lit" and PY_NAMES.match(s[1]) else s
            if last in ("str", "fspath", "abspath", "realpath", "normpath", "resolve", "Path", "PurePath", "expanduser") \
                    and e.args:
                return self.scalar(Thunk(e.args[0], ctx, env), depth + 1)
            if last == "dirname" and e.args:
                a = self.scalar(Thunk(e.args[0], ctx, env), depth + 1)
                return ("lit", a[1].rsplit("/", 1)[0] if "/" in a[1] else "") if a[0] == "lit" and "{?}" not in a[1] else UNK
            if last == "join" and fd.endswith("path.join") and e.args:
                parts = [self.scalar(Thunk(a, ctx, env), depth + 1) for a in e.args]
                if parts[-1][0] != "lit":
                    return UNK
                return ("lit", "/".join(p[1] if p[0] == "lit" else "{?}" for p in parts))
            if isinstance(e.func, ast.Attribute) and e.func.attr in ("resolve", "absolute") and not e.args:
                return self.scalar(Thunk(e.func.value, ctx, env), depth + 1)
            for rv, rctx in self.prog.returned(e, ctx)[:1]:
                return self.scalar(Thunk(rv, rctx, {}), depth + 1)
        return UNK

    # ------------------------------------------------------------------ what the argv runs
    def identify(self, items: list):
        """("ok", how, target id(s), detail) | ("param",) when the program is a parameter | ("no", reason, text)."""
        items = list(items)
        while items and items[0][0] == "lit" and re.match(r"^[A-Z_][A-Z0-9_]*=", items[0][1]):
            items.pop(0)                      # FOO=1 python -m x (shell)
        if not items:
            return ("no", "empty", "")
        first = items[0]
        if first[0] == "param":
            return ("param",)
        if first == PY or (first[0] == "lit" and PY_NAMES.match(first[1])):
            i = 1
            while i < len(items):
                it = items[i]
                if it[0] == "param":
                    return ("param",)
                if it[0] != "lit":
                    return ("no", "unknown argument", "")
                a = it[1]
                if a in PY_FLAGS_ARG:
                    i += 2
                    continue
                if a.startswith("-") and not a.startswith("--") and len(a) > 1:
                    # a cluster of short options: -u, -Im pip, -mpkg.cli, -Bc "code", -X dev, -Werror
                    j, opt, rest = 1, None, ""
                    while j < len(a):
                        ch = a[j]
                        if ch in "mcWX":
                            opt, rest = ch, a[j + 1:]
                            break
                        if ch not in PY_SHORT:
                            return ("no", "interpreter option", a)
                        j += 1
                    if opt is None:
                        i += 1
                        continue
                    if opt in "WX":
                        i += 1 if rest else 2
                        continue
                    if rest:
                        val = ("lit", rest)
                    elif i + 1 < len(items):
                        val = items[i + 1]
                    else:
                        return ("no", f"-{opt} without argument", "")
                    if val[0] == "param":
                        return ("param",)
                    if val[0] != "lit":
                        return ("no", "unknown module" if opt == "m" else "unknown code", f"-{opt} ?")
                    return self.module_target(val[1]) if opt == "m" else ("code", val[1])
                if a.startswith("-"):
                    return ("no", "interpreter option", a)
                return self.file_target(a)
            return ("no", "interpreter only", "")
        if first[0] == "lit":
            prog_name = re.split(r"[/\\]", first[1])[-1]
            prog_name = re.sub(r"\.exe$", "", prog_name)
            ids = self.scripts.get(prog_name)
            if ids:
                return ("ok", "console script", ids, prog_name)
            if prog_name.endswith(".py"):
                return self.file_target(first[1])
            return ("no", "not a project program", prog_name)
        return ("no", "unknown program", "")

    def module_target(self, mod: str):
        b, m = self.b, self.prog.module(mod)
        if m is None:
            return ("no", "module outside the project", f"-m {mod}")
        for key in (f"script:{mod}.__main__", f"script:{mod}"):
            if key in b.nodes:
                return ("ok", "-m", [key], mod)
        return ("ok", "-m", [m.id], mod)

    def file_target(self, path: str):
        p = path.replace("\\", "/")
        if not p.endswith(".py"):
            return ("no", "not a project program", p.rsplit("/", 1)[-1])
        tail = p.rsplit("{?}/", 1)[-1].lstrip("./")
        while tail.startswith("../"):
            tail = tail[3:]
        if "/" in tail:
            hits = [m for f, m in self.by_file.items() if f == tail or f.endswith("/" + tail)]
        else:      # a bare file name runs from the working directory: only the project root's file is known
            hits = [m for f, m in self.by_file.items() if f == tail]
        if len(hits) != 1:
            return ("no", "script file not found" if not hits else "script file ambiguous", tail)
        m = hits[0]
        key = f"script:{m.name}"
        return ("ok", "script", [key if key in self.b.nodes else m.id], m.file)

    def code_targets(self, code: str, ctx):
        """Functions / classes a `-c` snippet calls; imports resolved like a module's."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
        prog, names = self.prog, {}
        for st in ast.walk(tree):
            if isinstance(st, ast.Import):
                for a in st.names:
                    if a.asname:
                        names[a.asname] = ("mod", a.name)
                    else:
                        names[a.name.split(".")[0]] = ("prefix", a.name.split(".")[0])
            elif isinstance(st, ast.ImportFrom) and st.module and not st.level:
                for a in st.names:
                    if a.name != "*":
                        names[a.asname or a.name] = ("sym", st.module, a.name)
        out = []
        for c in ast.walk(tree):
            if not isinstance(c, ast.Call):
                continue
            d = self.dotted(c.func)
            if not d:
                continue
            parts = d.split(".")
            imp = names.get(parts[0])
            if imp is None:
                continue
            if imp[0] == "sym":
                t = prog.lookup(imp[1], imp[2])
                rest = parts[1:]
            else:
                t, rest = None, parts[1:]
                base = imp[1]
                m = prog.module(base)
                t = ("mod", m) if m else None
                while rest and t and t[0] == "mod" and prog.module(f"{t[1].name}.{rest[0]}"):
                    t = ("mod", prog.module(f"{t[1].name}.{rest[0]}"))
                    rest = rest[1:]
            for part in rest:
                if t is None:
                    break
                t = prog.lookup(t[1].name, part) if t[0] == "mod" else prog.member(t, part)
            if t and t[0] in ("func", "bound") and isinstance(t[1], self.FuncInfo):
                out.append(t[1].id)
            elif t and t[0] == "type":
                init = prog.find_method(t[1], "__init__")
                out.append(init.id if init else t[1].id)
        return list(dict.fromkeys(out))

    def code_imports(self, code: str) -> list:
        """Project modules a `-c` snippet imports (their bodies run) when it calls nothing of the project."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
        out = []
        for st in ast.walk(tree):
            mods = []
            if isinstance(st, ast.Import):
                mods = [a.name for a in st.names]
            elif isinstance(st, ast.ImportFrom) and st.module and not st.level:
                mods = [f"{st.module}.{a.name}" for a in st.names if a.name != "*"] + [st.module]
            for name in mods:
                m = self.prog.module(name)
                if m is not None:
                    out.append(m.id)
                    break                      # `from pkg import sub`: the submodule when it is one, else pkg
        return list(dict.fromkeys(out))

    def copies(self, f, ctx) -> list:
        """(destination text, source text) for `shutil.copy / copy2 / copyfile(src, dst)` and
        `dst.write_text(src.read_text()...)` in `f`."""
        out = []
        for sub in self.walk_body(f.node):
            if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Attribute):
                continue
            src = dst = None
            if sub.func.attr in COPY_FUNCS and len(sub.args) >= 2 and (self.dotted(sub.func.value) or "").endswith("shutil"):
                src, dst = sub.args[0], sub.args[1]
            elif sub.func.attr in ("write_text", "write_bytes") and sub.args:
                rd = sub.args[0]
                while isinstance(rd, ast.Call) and isinstance(rd.func, ast.Attribute) and rd.func.attr in (
                        "replace", "format", "strip", "lstrip"):
                    rd = rd.func.value                 # template.read_text().replace("{{ x }}", y)
                if isinstance(rd, ast.Call) and isinstance(rd.func, ast.Attribute) and rd.func.attr in (
                        "read_text", "read_bytes"):
                    src, dst = rd.func.value, sub.func.value
            if src is None:
                continue
            s = self.scalar(Thunk(src, ctx, None, False, sub.lineno))
            d = self.scalar(Thunk(dst, ctx, None, False, sub.lineno))
            if s[0] == "lit" and d[0] == "lit":
                out.append((d[1], s[1]))
        return out

    def copied_script(self, f, ctx, prog_text):
        """A program the function first copied from a project file (`shutil.copyfile(template, tmp / "manage.py")`):
        the source's entry when it is a module, else what the source (a template) calls, parsed as Python."""
        base = (prog_text or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not base:
            return None
        for dst, src in self.copies(f, ctx):
            if dst.replace("\\", "/").rsplit("/", 1)[-1] != base:
                continue
            self.st["copied_scripts"] += 1
            srcp = src.replace("\\", "/")
            if srcp.endswith(".py"):
                r = self.file_target(srcp)
                if r[0] == "ok":
                    return ("copied script", r[2], r[3])
            path = self.find_file(srcp)
            if path is None:
                continue
            try:
                code = (self.root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tgts = self.code_targets(code, ctx) or self.code_imports(code)
            if tgts:
                return ("copied template", tgts, path)
        return None

    def find_file(self, text: str):
        """Project-relative path of a non-module file whose path ends with `text` (minus unknown `{?}/` parts)."""
        if self.root is None:
            return None
        tail = text.rsplit("{?}/", 1)[-1].lstrip("./")
        name = tail.rsplit("/", 1)[-1]
        if self._files_by_name is None:
            import os
            self._files_by_name = defaultdict(list)
            from ... import presets
            skip = presets.skip_dirs("python")
            for dp, dns, fns in os.walk(self.root):
                dns[:] = [d for d in dns if d not in skip and not d.startswith(".")]
                rel = os.path.relpath(dp, self.root).replace(os.sep, "/")
                for fn in fns:
                    if fn.endswith(("-tpl", ".tpl", ".template", ".py.in", ".py_tmpl")):
                        self._files_by_name[fn].append(fn if rel == "." else f"{rel}/{fn}")
        hits = [p for p in self._files_by_name.get(name, ()) if p == tail or p.endswith("/" + tail)]
        return hits[0] if len(hits) == 1 else None

    # ------------------------------------------------------------------ binding call-site arguments
    def bind(self, f, call, ctx, env):
        """Parameter name -> Thunk (or list of Thunks for *args) for a call to runner `f`."""
        a = f.node.args
        pos = [x.arg for x in list(a.posonlyargs) + list(a.args)]
        if f.cls is not None and pos and isinstance(call.func, ast.Attribute) and not any(
                self.dotted(d) == "staticmethod" for d in f.node.decorator_list):
            pos = pos[1:]
        out, extra, i = {}, [], 0
        for arg in call.args:
            if isinstance(arg, ast.Starred):
                extra.append(Thunk(arg.value, ctx, env, True, call.lineno))
                i = len(pos) + 1
                continue
            if i < len(pos):
                out[pos[i]] = Thunk(arg, ctx, env, False, call.lineno)
            else:
                extra.append(Thunk(arg, ctx, env, False, call.lineno))
            i += 1
        if a.vararg is not None:
            out[a.vararg.arg] = extra
        elif extra and any(x.spread for x in extra) and len(pos) > 0:
            pass
        for k in call.keywords:
            if k.arg:
                out[k.arg] = Thunk(k.value, ctx, env, False, call.lineno)
        return out

    def run_items(self, fid, env, hops=0):
        """argv item lists runner `fid` produces with its parameters bound by `env`."""
        if hops > MAX_HOPS:
            return []
        out = []
        for kind, payload in self.runners.get(fid, ()):
            if kind == "spawn":
                thunks, shell, ctx = payload
                items = []
                for th in thunks:
                    items += self.seq(Thunk(th.e, ctx, env, th.spread, th.line))
                if shell and len(items) == 1 and items[0][0] == "lit":
                    items = self.split(items[0])
                out.append(items)
            else:
                callee, call, ctx = payload
                out += self.run_items(callee.id, self.bind(callee, call, ctx, env), hops + 1)
        return out

    # ------------------------------------------------------------------ driver
    def link(self) -> dict:
        prog, b, st = self.prog, self.b, self.st
        funcs = list(prog.funcs.values())
        direct = []                                  # (f, ctx, call, thunks, shell)
        spawn_names = SPAWN_ARGV | SPAWN_VARARGS | SPAWN_SHELL | SPAWN_LIST_AT1
        calls_by_name = defaultdict(list)            # callee's last name -> [(function, call)], one walk
        lib_mods = {id(m) for m in prog.modules.values()
                    if any(i[1].split(".")[0] in ("sh", "plumbum") for i in m.imports.values())}
        for f in funcs:
            ctx = None
            lib = id(f.module) in lib_mods
            for sub in self.walk_body(f.node):
                if not isinstance(sub, ast.Call):
                    continue
                fn = sub.func
                nm = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else None
                if lib and (nm is None or nm not in spawn_names):
                    ctx = ctx or self.Ctx(f.module, f, f.cls)
                    sp = self.lib_spawn(sub, ctx)
                    if sp is not None and sp[0]:
                        direct.append((f, ctx, sub, sp[0], sp[1]))
                        continue
                if nm is None:
                    continue
                calls_by_name[nm].append((f, sub))
                if nm == "invoke" and sub.args:
                    ctx = ctx or self.Ctx(f.module, f, f.cls)
                    self.cli_runner(f, ctx, sub)
                elif nm in spawn_names:
                    ctx = ctx or self.Ctx(f.module, f, f.cls)
                    sp = self.spawn(sub, ctx)
                    if sp is not None and sp[0]:
                        direct.append((f, ctx, sub, sp[0], sp[1]))
        st["process_starts"] = len(direct)
        pending = []                                 # (f, ctx, call, items list, via helper name | None)
        for f, ctx, call, thunks, shell in direct:
            items = []
            for th in thunks:
                items += self.seq(th)
            if shell and len(items) == 1 and items[0][0] == "lit":
                items = self.split(items[0])
            if self.identify(items)[0] == "param":
                self.runners.setdefault(f.id, []).append(("spawn", (thunks, shell, ctx)))
            else:
                pending.append((f, ctx, call, [items], None))
        # runners: functions whose program comes from a parameter; their call sites pass it (fixpoint, 5 hops)
        done_sites, resolved = set(), {}
        frontier = set(self.runners)
        for _hop in range(MAX_HOPS):
            if not frontier:
                break
            names = {fid.rsplit(".", 1)[-1] for fid in frontier}
            byid = {fid for fid in frontier}
            new = set()
            ctxs = {}
            for g, sub in (x for nm in sorted(names) for x in calls_by_name.get(nm, ())):
                if id(sub) in done_sites:
                    continue
                ctx = ctxs.get(g.id) or ctxs.setdefault(g.id, self.Ctx(g.module, g, g.cls))
                tgts = resolved.get(id(sub))
                if tgts is None:
                    try:
                        tgts = resolved[id(sub)] = prog.resolve_call(sub, ctx)
                    except RecursionError:
                        tgts = resolved[id(sub)] = []
                for tgt, _conf, _via in tgts:
                    if not isinstance(tgt, self.FuncInfo) or tgt.id not in byid or tgt.id == g.id:
                        continue
                    done_sites.add(id(sub))
                    probe = self.run_items(tgt.id, self.bind(tgt, sub, ctx, None))
                    if any(self.identify(it)[0] == "param" for it in probe):
                        if g.id not in self.runners:
                            new.add(g.id)
                        self.runners.setdefault(g.id, []).append(("call", (tgt, sub, ctx)))
                        st["runner_calls_forwarded"] += 1
                    res = [it for it in probe if self.identify(it)[0] != "param"]
                    if res:
                        pending.append((g, ctx, sub, res, tgt.name))
            frontier = new
        st["runners"] = len(self.runners)
        for f, ctx, call, item_lists, helper in pending:
            for items in item_lists:
                self.emit(f, ctx, call, items, helper)
        out = {k: v for k, v in st.items() if v}
        for k, v in self.samples.items():
            out.setdefault("samples", {})[k] = v[:5]
        return out

    def cli_runner(self, f, ctx, call):
        """`CliRunner().invoke(app, [...])` on a typer app / click group object: the commands registered on it run
        in process (a click group function is already referenced as a callback)."""
        if self.rp is None or not isinstance(call.args[0], (ast.Name, ast.Attribute)):
            return
        e = call.args[0]
        if isinstance(e, ast.Name):
            r = self.prog.resolve_name(ctx.mod, e.id)
        else:
            base = self.prog.infer(e.value, ctx)
            r = self.prog.lookup(base[1].name, e.attr) if base and base[0] == "mod" else None
        if not (r and r[0] == "var"):
            return
        regs = self.rp.var_regs.get((r[1].name, r[2]), [])
        for g in regs:
            self.b.add_edge(f.id, g.id, "REFERENCES_FN", f.file, call.lineno, RESOLVED, how="callback",
                            via="CliRunner.invoke")
        if regs:
            self.st["cli_runner_invokes"] += 1

    def emit(self, f, ctx, call, items, helper):
        st, b = self.st, self.b
        res = self.identify(items)
        via = {"via": "subprocess"}
        if helper:
            via["helper"] = helper
        cmd = " ".join("python" if x == PY else x[1] if x[0] == "lit" else "?" for x in items[:6])[:120]
        if res[0] == "no" and res[1] in ("script file not found", "not a project program"):
            got = self.copied_script(f, ctx, res[2])
            if got is not None:
                how, tgts, src = got
                for t in tgts:
                    b.add_edge(f.id, t, "CALLS", f.file, call.lineno, RESOLVED, how=how, command=cmd, copied_from=src,
                               **via)
                st["linked"] += 1
                st["linked_copied_script"] += 1
                st["linked_via_helper"] += bool(helper)
                return
        if res[0] == "code":
            tgts = self.code_targets(res[1], ctx)
            how = "-c"
            if not tgts:
                tgts, how = self.code_imports(res[1]), "-c import"
            if not tgts:
                st["code_without_project_calls"] += 1
                return
            for t in tgts:
                b.add_edge(f.id, t, "CALLS", f.file, call.lineno, RESOLVED, how=how, command=cmd, **via)
            st["linked"] += 1
            st["linked_" + how.replace(" ", "_")] += 1
            st["linked_via_helper"] += bool(helper)
            return
        if res[0] == "code":
            for t in tgts:
                b.add_edge(f.id, t, "CALLS", f.file, call.lineno, RESOLVED, how="-c", command=cmd, **via)
            st["linked"] += 1
            st["linked_-c"] += 1
            st["linked_via_helper"] += bool(helper)
            return
        if res[0] == "ok":
            _, how, ids, _detail = res
            for t in ids:
                b.add_edge(f.id, t, "CALLS", f.file, call.lineno, RESOLVED, how=how, command=cmd, **via)
            st["linked"] += 1
            st[f"linked_{how.replace(' ', '_')}"] += 1
            st["linked_via_helper"] += bool(helper)
            return
        if res[0] == "no":
            key = "outside_project" if res[1] in ("module outside the project", "not a project program") else "unresolved"
            st[key] += 1
            if res[2] and len(self.samples[key]) < 5 and res[2] not in self.samples[key]:
                self.samples[key].append(res[2])


def sys_exe_text(s) -> str:
    if s == PY:
        return "python"
    return s[1] if s[0] == "lit" else "{?}"

"""Enum members and module constants (#84): `enum_case:<module>.<Enum>.<MEMBER>` for the members of a class derived
from `enum.Enum` (IntEnum, StrEnum, Flag, IntFlag, a project enum base), `constant:<module>.<NAME>` for a module-level
UPPER_CASE or `Final` name, and USES_VALUE edges where the binding is certain: `Color.RED`, `mod.NAME`, an imported
`NAME`, or a bare `NAME` of the module that no local binding of the function shadows. Call edges are untouched."""
from __future__ import annotations

import ast
import re

from ...core.model import EXACT

CONST_NAME = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
ENUM_BASES = ("Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum")
NOT_CONSTANT_CALLS = {"TypeVar", "ParamSpec", "TypeVarTuple", "NewType", "namedtuple", "NamedTuple", "TypedDict"}


def _final(ann) -> bool:
    if ann is None:
        return False
    a = ann.value if isinstance(ann, ast.Subscript) else ann
    return (isinstance(a, ast.Name) and a.id == "Final") or (isinstance(a, ast.Attribute) and a.attr == "Final")


def _called(v) -> str | None:
    if isinstance(v, ast.Call):
        f = v.func
        return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
    return None


def _top_assigns(body):
    """Module-level assignments, including those under a top-level `if` / `try` (`try: X = 1 except: X = 2`)."""
    for st in body:
        if isinstance(st, (ast.Assign, ast.AnnAssign)):
            yield st
        elif isinstance(st, (ast.If, ast.Try)) or type(st).__name__ == "TryStar":
            yield from _top_assigns(getattr(st, "body", []) + getattr(st, "orelse", []) + getattr(st, "finalbody", []))
            for h in getattr(st, "handlers", []):
                yield from _top_assigns(h.body)


def _targets(st) -> list[str]:
    tg = st.targets if isinstance(st, ast.Assign) else [st.target]
    return [t.id for t in tg if isinstance(t, ast.Name)]


class Values:
    def __init__(self, prog, b):
        self.prog, self.b = prog, b
        self.cases: dict[str, dict[str, str]] = {}      # enum class qual -> member -> id
        self.consts: dict[str, dict[str, str]] = {}     # module name -> NAME -> id
        self.st = {"enum_cases": 0, "constants": 0, "value_refs": 0}

    def run(self) -> dict:
        prog = self.prog
        for c in prog.classes.values():
            if c.name in ENUM_BASES or not prog.subclass_of(c, *ENUM_BASES):
                continue
            for st in c.node.body:
                if not isinstance(st, (ast.Assign, ast.AnnAssign)) or getattr(st, "value", None) is None:
                    continue
                if isinstance(st.value, (ast.Lambda,)) or _called(st.value) in ("property", "staticmethod", "classmethod"):
                    continue
                for nm in _targets(st):
                    if nm.startswith("_"):
                        continue                    # `_ignore_`, `_order_`, private names: not members
                    self._node("enum_case", c.qual, nm, c.id, c.file, st)
                    self.cases.setdefault(c.qual, {})[nm] = f"enum_case:{c.qual}.{nm}"
        for m in prog.modules.values():
            for st in _top_assigns(m.tree.body):
                if getattr(st, "value", None) is None or _called(st.value) in NOT_CONSTANT_CALLS:
                    continue
                for nm in _targets(st):
                    if nm in m.classes or nm in m.funcs or nm.startswith("__") or \
                            not (CONST_NAME.match(nm) or _final(getattr(st, "annotation", None))):
                        continue
                    if nm in self.consts.get(m.name, {}):
                        continue                    # `X = 1` re-assigned later (or in an `else`): one constant
                    self._node("constant", m.name, nm, m.id, m.file, st)
                    self.consts.setdefault(m.name, {})[nm] = f"constant:{m.name}.{nm}"
        if self.cases or self.consts:
            self._refs()
        return self.st

    def _node(self, kind, owner_q, nm, owner_id, file, st):
        nid = self.b.add_node(kind, f"{owner_q}.{nm}", name=nm, fqn=f"{owner_q}.{nm}", file=file, line=st.lineno,
                              end_line=getattr(st, "end_lineno", None), lang="python")
        self.b.add_edge(owner_id, nid, "CONTAINS", file, st.lineno, EXACT)
        self.st["enum_cases" if kind == "enum_case" else "constants"] += 1

    # ------------------------------------------------------------------ references
    def _refs(self):
        prog = self.prog
        for f in list(prog.funcs.values()):
            self._scan(f.id, f.module, f.node, f.file, self._locals(f.node), prog.func_imports(f))
        for m in prog.modules.values():
            self._scan_body(m.id, m, m.tree.body, m.file)
            for c in m.all_classes:
                self._scan_body(c.id, m, c.node.body, m.file, cls=c)

    def _scan_body(self, owner, m, body, file, cls=None):
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for d in getattr(st, "decorator_list", []):
                    self._scan(owner, m, d, file, set(), {})
                continue
            # class body: its own names are class attributes (an enum's members among them), not module constants
            local = {t for s in body if isinstance(s, (ast.Assign, ast.AnnAssign)) for t in _targets(s)} \
                if cls is not None else set()
            self._scan(owner, m, st, file, local, {})

    @staticmethod
    def _locals(fn) -> set[str]:
        """Names a def binds (Python scoping: a name assigned anywhere in the function is local throughout)."""
        out, glob = set(), set()
        a = fn.args
        for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
            if x is not None:
                out.add(x.arg)
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                out.add(n.id)
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                glob.update(n.names)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn:
                out.add(n.name)
            elif isinstance(n, ast.arg):
                out.add(n.arg)                      # a nested def's / lambda's parameter: shadows conservatively
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                out.update((al.asname or al.name).split(".")[0] for al in n.names)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                out.add(n.name)
        return out - glob

    def _resolve(self, m, e, local, fimps, depth=0):
        """What expression `e` names: ("type", ClassInfo) / ("mod", ModInfo) / ("var", ModInfo, name) / None."""
        prog = self.prog
        if depth > 6:
            return None
        if isinstance(e, ast.Name):
            if e.id in local and e.id not in fimps:
                return None
            if e.id in fimps:
                return prog._resolve_import(fimps[e.id])
            return prog.resolve_name(m, e.id)
        if isinstance(e, ast.Attribute):
            base = self._resolve(m, e.value, local, fimps, depth + 1)
            if base is None:
                return None
            if base[0] == "mod":
                return prog.lookup(base[1].name, e.attr)
            if base[0] == "type":
                inner = getattr(base[1], "inner_infos", {}).get(e.attr)
                return ("type", inner) if inner is not None else None
        return None

    def _value_of(self, r, attr: str | None = None):
        if r is None:
            return None
        if attr is not None:
            if r[0] == "type":
                return self.cases.get(r[1].qual, {}).get(attr)
            if r[0] == "mod":
                return self.consts.get(r[1].name, {}).get(attr)
            return None
        if r[0] == "var":
            return self.consts.get(r[1].name, {}).get(r[2])
        return None

    def _scan(self, owner, m, node, file, local, fimps):
        for n in ast.walk(node):
            vid = None
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
                vid = self._value_of(self._resolve(m, n.value, local, fimps), n.attr)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and (n.id in fimps or n.id not in local):
                vid = self._value_of(self._resolve(m, n, local, fimps))
            if vid is not None and vid != owner:
                self.b.add_edge(owner, vid, "USES_VALUE", file, n.lineno, EXACT)
                self.st["value_refs"] += 1

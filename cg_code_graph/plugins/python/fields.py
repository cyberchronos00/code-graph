"""Python stored attributes as field nodes (#88 phase 1).

A class's instance attributes (`self.x = ...` in any of its methods) and its annotated class-level attributes
(`x: int = 0` in a dataclass / pydantic model / attrs class; not `ClassVar[...]`) are `field:<Class>.<attr>` nodes,
attrs `property: stored`, `declared: self | class`. Plain class-level assignments stay out: they are constants or
framework descriptors (Django model fields are the Django plugin's own `field:` nodes), and methods, properties and
nested classes are never fields.

Every `self.x` / `v.x` attribute access whose receiver type the inference knows (`self`, a parameter annotation, a
local `v = Cart()`, a typed attribute) and whose class (or a base) declares the field is a READS_PROP / WRITES_PROP
edge (`resolved`, attr `receiver`). A write is an assignment / augmented assignment / `del` target, or the receiver
of an in-place collection method (`self.items.append(x)`, `via: mutating`), or an item assignment / deletion
(`self.cache[k] = v`, `via: item`). An unknown receiver binds nothing. Test
code's accesses become TEST_USES (tests_index.py).
"""
from __future__ import annotations

import ast

from ...core.model import RESOLVED

MUTATING = frozenset({
    "append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse", "add", "discard", "update",
    "setdefault", "popitem", "difference_update", "intersection_update", "symmetric_difference_update", "appendleft",
    "extendleft", "popleft", "rotate", "put", "put_nowait"})


def _classvar(ann) -> bool:
    t = ast.unparse(ann) if ann is not None else ""
    return t.startswith(("ClassVar", "typing.ClassVar", "Final", "typing.Final", "InitVar", "dataclasses.InitVar"))


def index(prog, b, Ctx, walk_body) -> dict:
    fields: dict[str, dict[str, str]] = {}
    names: set[str] = set()
    n_nodes = 0
    for c in prog.classes.values():
        if c.id not in b.nodes:
            continue
        own: dict[str, tuple[int, str]] = {}
        for nm, (val, line, ann) in c.attrs.items():
            if ann is not None and not _classvar(ann):
                own[nm] = (line, "class")
        for nm, line in c.self_attr_lines.items():
            own.setdefault(nm, (line, "self"))
        inner = getattr(c, "inner_infos", {})
        for nm, (line, how) in own.items():
            if nm in c.methods or nm in inner:
                continue
            fid = b.add_node("field", f"{c.qual}.{nm}", name=nm, fqn=f"{c.qual}.{nm}", file=c.file, line=line,
                             module=b.nodes[c.id].module, lang="python",
                             attrs={"property": "stored", "declared": how})
            b.add_edge(c.id, fid, "CONTAINS", c.file, line, RESOLVED)
            fields.setdefault(c.qual, {})[nm] = fid
            names.add(nm)
            n_nodes += 1
    if not names:
        return {}

    def lookup(cls, attr):
        if cls.qual in fields and attr in fields[cls.qual]:
            return fields[cls.qual][attr]
        for bt in prog.mro(cls):
            if bt[0] == "type" and bt[1].qual in fields and attr in fields[bt[1].qual]:
                return fields[bt[1].qual][attr]
        return None

    reads = writes = mutating = unresolved = 0
    for f in list(prog.funcs.values()):
        ctx = Ctx(f.module, f, f.cls)
        parent: dict[int, ast.AST] = {}
        attrs = []
        for n in walk_body(f.node):
            for ch in ast.iter_child_nodes(n):
                parent[id(ch)] = n
            if isinstance(n, ast.Attribute) and n.attr in names:
                attrs.append(n)
        for a in attrs:
            p = parent.get(id(a))
            if isinstance(p, ast.Call) and p.func is a:
                continue                                   # `self.save()`: a call, not an attribute access
            try:
                t = prog.infer(a.value, ctx)
            except RecursionError:
                t = None
            fid = lookup(t[1], a.attr) if t is not None and t[0] == "inst" else None
            if fid is None:
                unresolved += 1
                continue
            via = None
            write = isinstance(a.ctx, (ast.Store, ast.Del))
            if not write and isinstance(p, ast.Subscript) and p.value is a and isinstance(p.ctx, (ast.Store, ast.Del)):
                write, via = True, "item"                  # `self.cache[k] = v` / `del self.cache[k]`
            elif not write and isinstance(p, ast.Attribute) and p.value is a and p.attr in MUTATING:
                gp = parent.get(id(p))
                if isinstance(gp, ast.Call) and gp.func is p:
                    write, via = True, "mutating"
            recv = ast.unparse(a.value)[:40] if not (isinstance(a.value, ast.Name) and a.value.id == "self") else "self"
            b.add_edge(f.id, fid, "WRITES_PROP" if write else "READS_PROP", f.file, a.lineno, RESOLVED,
                       receiver=recv, **({"via": via} if via else {}))
            if write:
                writes += 1
                mutating += via == "mutating"
            else:
                reads += 1
    return {"stored_attribute_nodes": n_nodes, "stored_attribute_reads": reads, "stored_attribute_writes": writes,
            "stored_attribute_writes_mutating": mutating, "stored_attribute_refs_unresolved": unresolved}

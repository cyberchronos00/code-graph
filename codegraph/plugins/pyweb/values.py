"""String values of Python expressions, evaluated statically (no code runs): literals, f-strings, `+` / `%` /
`.format`, module and imported constants, class attribute defaults read through an instance (`settings.API_V1_STR`
with `settings = Settings()` and `API_V1_STR: str = "/api/v1"`). Used for route prefixes / paths and test URLs."""
from __future__ import annotations

import ast

UNKNOWN = "{?}"


def str_value(prog, e, ctx, depth: int = 0) -> str | None:
    """The string value of `e`, or None when it cannot be determined. f-string parts that cannot be evaluated
    become `{name}` placeholders (a path parameter in a URL)."""
    from ..python.plugin import Ctx
    if e is None or depth > 8:
        return None
    if isinstance(e, ast.Constant):
        return e.value if isinstance(e.value, str) else None
    if isinstance(e, ast.JoinedStr):
        out = ""
        for v in e.values:
            if isinstance(v, ast.Constant):
                out += str(v.value)
                continue
            s = str_value(prog, v.value, ctx, depth + 1)
            out += s if s is not None else "{" + _ph(v.value) + "}"
        return out
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
        a, b = str_value(prog, e.left, ctx, depth + 1), str_value(prog, e.right, ctx, depth + 1)
        return a + b if a is not None and b is not None else None
    if isinstance(e, ast.Name):
        if ctx.func is not None:
            lv = prog.local_vars(ctx).get(e.id)
            if lv is not None:
                vals = [v for v, _a, kind in lv if kind == "assign" and v is not None]
                return str_value(prog, vals[0], ctx, depth + 1) if len(vals) == 1 else None
        r = prog.resolve_name(ctx.mod, e.id)
        if r and r[0] == "var":
            vals = [v for v, _ln, _ann in r[1].vars.get(r[2], ()) if v is not None]
            return str_value(prog, vals[0], Ctx(r[1], None, None), depth + 1) if len(vals) == 1 else None
        return None
    if isinstance(e, ast.Attribute):
        try:
            t = prog.infer(e.value, ctx)
        except RecursionError:
            return None
        if t and t[0] == "var":
            t = prog.var_type(t[1], t[2]) if hasattr(prog, "var_type") else None
        if t and t[0] == "mod":
            m = t[1]
            vals = [v for v, _ln, _ann in m.vars.get(e.attr, ()) if v is not None]
            return str_value(prog, vals[0], Ctx(m, None, None), depth + 1) if len(vals) == 1 else None
        if t and t[0] in ("inst", "type"):
            c = t[1]
            for k in [c] + [b[1] for b in prog.mro(c) if b[0] == "type"]:
                if e.attr in k.attrs:
                    val = k.attrs[e.attr][0]
                    return str_value(prog, val, Ctx(k.module, None, k), depth + 1) if val is not None else None
        return None
    return None


def _ph(e) -> str:
    if isinstance(e, ast.Name):
        return e.id
    if isinstance(e, ast.Attribute):
        return e.attr
    return "p"

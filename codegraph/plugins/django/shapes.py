"""Nested JSON shapes of values a Django/ninja view returns (dict literals, helper functions building
dicts, Schema.from_orm(...).dict(), list comprehensions), with value types inferred from model
fields (`obj.title` -> CharField -> str, null=True -> nullable) and literals.

shape = {key: {"line", "file", "type", "nullable", "many", "children": shape | None, "schema": qual | None}}
"""
from __future__ import annotations

import ast

from ..python.plugin import Ctx, FuncInfo, const_str, dotted

PY_CASTS = {"str": "str", "int": "int", "float": "float", "bool": "bool", "len": "int", "round": "float", "sum": "int",
            "Decimal": "decimal", "list": "list", "dict": "dict", "sorted": "list"}
STR_METHODS = {"isoformat", "strftime", "lower", "upper", "strip", "format", "join", "replace", "get_full_name", "__str__",
               "get_absolute_url", "build_absolute_uri", "hex"}


class ShapeBuilder:
    def __init__(self, plugin):
        self.p = plugin
        self.prog = plugin.prog
        self.models = plugin.models

    def returns(self, f: FuncInfo, depth=0) -> list[tuple[ast.AST, int | None]]:
        out = []
        for sub in ast.walk(f.node):
            if isinstance(sub, ast.Return) and sub.value is not None:
                v, status = sub.value, None
                if isinstance(v, ast.Tuple) and len(v.elts) == 2 and isinstance(v.elts[0], ast.Constant):
                    status, v = v.elts[0].value, v.elts[1]
                out.append((v, status, sub.lineno))
        return out

    def shape(self, e, ctx: Ctx, env=None, depth=0) -> dict | None:
        env = env or {}
        if depth > 5 or e is None:
            return None
        if isinstance(e, ast.Dict):
            out = {}
            for k, v in zip(e.keys, e.values):
                if k is None:  # **spread
                    sub = self.shape(v, ctx, env, depth + 1)
                    if sub:
                        out.update(sub)
                    continue
                name = const_str(k)
                if name is None:
                    continue
                out[name] = dict(self.value(v, ctx, env, depth + 1), line=v.lineno, file=ctx.mod.file)
            return out
        if isinstance(e, ast.Call):
            fn = (dotted(e.func) or "").split(".")[-1]
            if fn == "dict" and e.keywords:
                return {k.arg: dict(self.value(k.value, ctx, env, depth + 1), line=k.value.lineno, file=ctx.mod.file)
                        for k in e.keywords if k.arg}
            if fn in ("JsonResponse", "Response", "JSONResponse", "HttpResponse") and e.args:
                return self.shape(e.args[0], ctx, env, depth + 1)
            if isinstance(e.func, ast.Attribute) and e.func.attr in ("dict", "model_dump") and isinstance(e.func.value, ast.Call):
                return self.schema_shape(e.func.value, ctx)
            sc = self.schema_shape(e, ctx)
            if sc is not None:
                return sc
            t = self.prog.infer(e.func, ctx)
            if t and t[0] in ("func", "bound") and depth < 4:
                f = t[1]
                c2 = Ctx(f.module, f, f.cls)
                merged: dict = {}
                for v, status, line in self.returns(f):
                    s = self.shape(v, c2, {}, depth + 1)
                    if s:
                        for k, info in s.items():
                            merged.setdefault(k, info)
                return merged or None
            return None
        if isinstance(e, ast.Name) and ctx.func is not None:
            if e.id in env:
                return None
            out = {}
            for val, ann, kind in self.prog.local_vars(ctx).get(e.id, []):
                if val is not None and not isinstance(val, ast.Name):
                    s = self.shape(val, ctx, env, depth + 1)
                    if s:
                        out.update(s)
            for sub in ast.walk(ctx.func.node):  # d["k"] = v
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == e.id and const_str(t.slice):
                            out.setdefault(const_str(t.slice), dict(self.value(sub.value, ctx, env, depth + 1), line=sub.lineno,
                                                                    file=ctx.mod.file, conditional=True))
            return out or None
        return None

    def schema_shape(self, call: ast.Call, ctx: Ctx) -> dict | None:
        """Schema.from_orm(x) / Schema.model_validate(x) / Schema(**kw) / Serializer(x).data -> declared fields."""
        f = call.func
        tgt = f.value if isinstance(f, ast.Attribute) and f.attr in ("from_orm", "model_validate", "from_attributes", "validate") else f
        t = self.prog.infer(tgt, ctx)
        if t and t[0] == "type" and self.p.schemas.kind(t[1]):
            return self.fields_shape(t[1].qual)
        return None

    def fields_shape(self, qual: str, depth=0) -> dict:
        c = self.prog.classes.get(qual)
        out = {}
        for fd in (self.p.schemas.fields(c) if c else []) or []:
            ch = self.fields_shape(fd["ref"], depth + 1) if fd.get("ref") and depth < 4 else None
            out[fd.get("json") or fd["name"]] = {"type": fd.get("type"), "nullable": fd.get("nullable"), "many": fd.get("many"),
                                                 "children": ch, "schema": qual, "line": fd.get("line"), "file": fd.get("file"),
                                                 "choices": fd.get("choices")}
        return out

    def value(self, v, ctx: Ctx, env, depth) -> dict:
        if isinstance(v, ast.Constant):
            if v.value is None:
                return {"type": "null", "nullable": True}
            return {"type": {str: "str", int: "int", float: "float", bool: "bool"}.get(type(v.value), "any")}
        if isinstance(v, ast.JoinedStr):
            return {"type": "str"}
        if isinstance(v, (ast.Dict,)) or (isinstance(v, ast.Call) and (dotted(v.func) or "").split(".")[-1] == "dict"):
            return {"type": "dict", "children": self.shape(v, ctx, env, depth + 1)}
        if isinstance(v, (ast.List, ast.Tuple)):
            el = v.elts[0] if v.elts else None
            info = self.value(el, ctx, env, depth + 1) if el is not None else {}
            return {"type": "list", "many": True, "children": info.get("children"), "item_type": info.get("type")}
        if isinstance(v, ast.ListComp):
            env2 = dict(env)
            g = v.generators[0]
            if isinstance(g.target, ast.Name):
                it = self.prog.infer(g.iter, ctx)
                env2[g.target.id] = self.prog.iter_type(it) or (it[1] if it and it[0] == "list" else None)
            info = self.value(v.elt, ctx, env2, depth + 1)
            return {"type": "list", "many": True, "children": info.get("children"), "item_type": info.get("type")}
        if isinstance(v, ast.IfExp):
            a = self.value(v.body, ctx, env, depth + 1)
            b = self.value(v.orelse, ctx, env, depth + 1)
            base = a if a.get("type") not in (None, "null") else (b if b.get("type") not in (None, "null") else dict(a, type=None))
            return dict(base, nullable=bool(a.get("nullable") or b.get("nullable") or a.get("type") == "null" or b.get("type") == "null"))
        if isinstance(v, ast.BoolOp):
            return self.value(v.values[-1], ctx, env, depth + 1) if isinstance(v.op, ast.Or) else {"type": "any"}
        if isinstance(v, ast.Compare) or (isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not)):
            return {"type": "bool"}
        if isinstance(v, ast.BinOp):
            l, r = self.value(v.left, ctx, env, depth + 1), self.value(v.right, ctx, env, depth + 1)
            if "str" in (l.get("type"), r.get("type")):
                return {"type": "str"}
            if l.get("type") in ("int", "float") and r.get("type") in ("int", "float"):
                return {"type": "float" if "float" in (l.get("type"), r.get("type")) or isinstance(v.op, ast.Div) else "int"}
            return {"type": None}
        if isinstance(v, ast.Call):
            fn = dotted(v.func) or ""
            last = fn.split(".")[-1]
            if last in PY_CASTS and "." not in fn:
                return {"type": PY_CASTS[last]}
            if isinstance(v.func, ast.Attribute) and v.func.attr in STR_METHODS:
                inner = self.value(v.func.value, ctx, env, depth + 1)
                return {"type": "str", "nullable": False}
            if isinstance(v.func, ast.Attribute) and v.func.attr in ("count",):
                return {"type": "int"}
            if isinstance(v.func, ast.Attribute) and v.func.attr in ("dict", "model_dump") and isinstance(v.func.value, ast.Call):
                return {"type": "dict", "children": self.schema_shape(v.func.value, ctx)}
            s = self.shape(v, ctx, env, depth + 1)
            if s:
                return {"type": "dict", "children": s}
            t = self.prog.infer(v.func, ctx)
            if t and t[0] in ("func", "bound"):
                f = t[1]
                rets = self.returns(f)
                if rets:
                    vv = rets[0][0]
                    if isinstance(vv, (ast.List, ast.ListComp)):
                        return self.value(vv, Ctx(f.module, f, f.cls), {}, depth + 1)
            return {"type": None}
        if isinstance(v, ast.Attribute):
            return self.attr_value(v, ctx, env, depth)
        if isinstance(v, ast.Name):
            if v.id in env:
                return {"type": "model", "model": env[v.id][1].qual} if env[v.id] and env[v.id][0] == "inst" else {"type": None}
            vals = [val for val, ann, kind in self.prog.local_vars(ctx).get(v.id, []) if val is not None] if ctx.func else []
            if ctx.func and any(isinstance(x, ast.List) and not x.elts for x in vals):
                for sub in ast.walk(ctx.func.node):  # items = []; items.append({...})
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "append" \
                            and isinstance(sub.func.value, ast.Name) and sub.func.value.id == v.id and sub.args:
                        info = self.value(sub.args[0], ctx, {**env, v.id: None}, depth + 1)
                        return {"type": "list", "many": True, "children": info.get("children"), "item_type": info.get("type")}
            if len(vals) >= 1 and depth < 4:
                infos = [self.value(x, ctx, {**env, v.id: None}, depth + 1) for x in vals[:3]]
                base = next((i for i in infos if i.get("type") not in (None, "null")), infos[0])
                return dict(base, nullable=any(i.get("nullable") or i.get("type") == "null" for i in infos) or base.get("nullable"))
            return {"type": None}
        if isinstance(v, ast.Subscript):
            return {"type": None}
        return {"type": None}

    def attr_value(self, v: ast.Attribute, ctx: Ctx, env, depth) -> dict:
        base = v.value
        if isinstance(base, ast.Name) and base.id in env and env[base.id] and env[base.id][0] == "inst":
            t = env[base.id]
        else:
            t = self.prog.infer(base, ctx)
        if isinstance(base, ast.Attribute) and v.attr in ("url", "name", "path"):
            inner = self.attr_value(base, ctx, env, depth + 1)
            if inner.get("type") == "file":
                return {"type": "str", "nullable": inner.get("nullable"), "model_field": inner.get("model_field")}
        if t and t[0] == "inst":
            mf = self.models.field(t[1].qual, v.attr)
            if mf:
                nullable = bool(mf.get("null")) or (mf.get("type") == "file" and bool(mf.get("blank")))
                typ = mf.get("type")
                if typ == "fk":
                    typ = "model"
                return {"type": typ, "nullable": nullable, "model_field": f"{t[1].qual}.{v.attr}", "choices": mf.get("choices"),
                        "field_line": mf.get("line"), "field_file": mf.get("file")}
            if v.attr == "id" or v.attr == "pk":
                return {"type": "int"}
        return {"type": None}

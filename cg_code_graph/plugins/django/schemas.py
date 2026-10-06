"""Wire schemas: django-ninja / pydantic Schema classes and DRF serializers -> field lists.

Field dict: {name, json, type, nullable, required, read_only, write_only, many, ref (class qual of a nested
schema), source, file, line, choices}. Stored on the class node as attrs.schema_fields so the cross-repo
payload check can compare them with client JSON keys.
"""
from __future__ import annotations

import ast
import re

from ..python.plugin import ClassInfo, Ctx, PyProgram, ann_base, ann_text, const_str, dotted, is_nullable_ann, kwarg

PY_TYPES = {"str": "str", "int": "int", "float": "float", "bool": "bool", "dict": "dict", "Dict": "dict", "list": "list",
            "List": "list", "datetime": "datetime", "date": "date", "time": "time", "UUID": "uuid", "Decimal": "decimal",
            "Any": "any", "EmailStr": "str", "HttpUrl": "str", "AnyUrl": "str", "conint": "int", "constr": "str",
            "confloat": "float", "Json": "json", "UploadedFile": "file", "bytes": "bytes", "timedelta": "duration",
            "Literal": "str", "Enum": "str", "PositiveInt": "int", "NonNegativeInt": "int", "StrictStr": "str",
            "StrictInt": "int", "StrictBool": "bool", "set": "list", "tuple": "list", "Sequence": "list", "object": "any"}
DRF_TYPES = {"CharField": "str", "EmailField": "str", "RegexField": "str", "SlugField": "str", "URLField": "str",
             "UUIDField": "uuid", "IPAddressField": "str", "IntegerField": "int", "FloatField": "float",
             "DecimalField": "decimal", "BooleanField": "bool", "NullBooleanField": "bool", "DateTimeField": "datetime",
             "DateField": "date", "TimeField": "time", "DurationField": "duration", "ChoiceField": "str",
             "MultipleChoiceField": "list", "FileField": "file", "ImageField": "file", "ListField": "list",
             "DictField": "dict", "HStoreField": "dict", "JSONField": "json", "ReadOnlyField": "any",
             "HiddenField": "any", "SerializerMethodField": "any", "PrimaryKeyRelatedField": "int",
             "HyperlinkedRelatedField": "str", "SlugRelatedField": "str", "StringRelatedField": "str",
             "HyperlinkedIdentityField": "str", "ModelField": "any", "Field": "any", "ManyRelatedField": "list"}
MODEL_TO_WIRE = {"fk": "int", "m2m": "list", "uuid": "str", "decimal": "str", "datetime": "datetime", "date": "date",
                 "time": "str", "json": "json", "file": "str", "duration": "str"}


def camel(s: str) -> str:
    p = s.split("_")
    return p[0] + "".join(x[:1].upper() + x[1:] for x in p[1:])


class Schemas:
    def __init__(self, prog: PyProgram, models):
        self.prog = prog
        self.models = models
        self.cache: dict[str, list | None] = {}

    def kind(self, c: ClassInfo) -> str | None:
        p = self.prog
        if p.subclass_of(c, "ninja.ModelSchema", "ninja.orm.metaclass.ModelSchema", "ninja.orm.ModelSchema"):
            return "ninja_model"
        if p.subclass_of(c, "ninja.Schema", "ninja.schema.Schema", "ninja.FilterSchema", "ninja.filter_schema.FilterSchema"):
            return "ninja"
        if p.subclass_of(c, "pydantic.BaseModel", "pydantic.main.BaseModel", "pydantic.v1.BaseModel"):
            return "pydantic"
        if p.subclass_of(c, "serializers.ModelSerializer", "serializers.HyperlinkedModelSerializer"):
            return "drf_model"
        if p.subclass_of(c, "serializers.Serializer", "serializers.BaseSerializer", "serializers.ListSerializer"):
            return "drf"
        return None

    def fields(self, c: ClassInfo) -> list | None:
        if c.qual in self.cache:
            return self.cache[c.qual]
        self.cache[c.qual] = []
        k = self.kind(c)
        out = None
        if k in ("ninja", "pydantic", "ninja_model"):
            out = self._pyd(c, k)
        elif k in ("drf", "drf_model"):
            out = self._drf(c, k)
        self.cache[c.qual] = out
        return out

    # ---- pydantic / ninja
    def _alias_gen(self, c: ClassInfo) -> str | None:
        for k in [c] + [b[1] for b in self.prog.mro(c) if b[0] == "type"]:
            cfg = k.attrs.get("model_config")
            if cfg and isinstance(cfg[0], ast.Call):
                ag = kwarg(cfg[0], "alias_generator")
                if ag is not None:
                    return (dotted(ag) or "").split(".")[-1]
            for nm in ("Config", "Meta"):
                inner = k.inner.get(nm)
                if inner is None:
                    continue
                for st in inner.body:
                    if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name) and st.targets[0].id == "alias_generator":
                        return (dotted(st.value) or "").split(".")[-1]
        return None

    def _pyd(self, c: ClassInfo, kind: str) -> list:
        prog = self.prog
        chain = [c] + [b[1] for b in prog.mro(c) if b[0] == "type" and self.kind(b[1])]
        fields: dict[str, dict] = {}
        for k in reversed(chain):
            if self.kind(k) == "ninja_model" or kind == "ninja_model":
                for f in self._model_schema_fields(k):
                    fields[f["name"]] = f
            for name, (val, line, ann) in k.attrs.items():
                if ann is None or name.startswith("_") or name in ("model_config", "Config", "Meta"):
                    continue
                if (dotted(ann) or "").endswith("ClassVar") or (isinstance(ann, ast.Subscript) and (dotted(ann.value) or "").endswith("ClassVar")):
                    continue
                fd = {"name": name, "json": name, "file": k.file, "line": line}
                fd.update(self.ann_info(k, ann))
                fd["nullable"] = fd.get("nullable") or is_nullable_ann(ann)
                required = val is None
                if isinstance(val, ast.Call) and (dotted(val.func) or "").split(".")[-1] in ("Field", "Query", "Body", "Form", "Path", "File"):
                    d0 = val.args[0] if val.args else kwarg(val, "default")
                    required = d0 is None and kwarg(val, "default_factory") is None or (isinstance(d0, ast.Constant) and d0.value is Ellipsis)
                    al = const_str(kwarg(val, "alias")) or const_str(kwarg(val, "serialization_alias"))
                    if al:
                        fd["alias"] = al
                    if isinstance(d0, ast.Constant) and d0.value is None:
                        fd["default_none"] = True
                elif isinstance(val, ast.Constant) and val.value is None:
                    fd["default_none"] = True
                fd["required"] = bool(required)
                fields[name] = fd
            for name, f in k.methods.items():
                if name.startswith("resolve_") and name[8:] in fields:
                    fields[name[8:]]["resolver"] = f"{f.file}:{f.line}"
        ag = self._alias_gen(c)
        for fd in fields.values():
            if ag and "camel" in ag.lower():
                fd["json_in"] = camel(fd["name"])
                if kind == "pydantic":
                    fd["json"] = camel(fd["name"])
            if kind == "pydantic" and fd.get("alias"):
                fd["json"] = fd["alias"]
        return list(fields.values())

    def ann_info(self, k: ClassInfo, ann) -> dict:
        e, cont = ann_base(ann)
        out = {"type_text": ann_text(ann)}
        if cont == "list":
            out["many"] = True
        if e is None:
            out["type"] = "list" if cont == "list" else ("dict" if "Dict" in (ann_text(ann) or "") or "dict" in (ann_text(ann) or "") else "any")
            return out
        if isinstance(e, ast.Subscript):
            head = (dotted(e.value) or "").split(".")[-1]
            if head == "Literal":
                sl = e.slice
                vals = [x.value for x in (sl.elts if isinstance(sl, ast.Tuple) else [sl]) if isinstance(x, ast.Constant)]
                out["choices"] = vals
                out["type"] = "str" if all(isinstance(v, str) for v in vals) else "any"
                return out
            out["type"] = PY_TYPES.get(head, "any")
            return out
        d = dotted(e)
        t = self.prog.infer(e, Ctx(k.module, None, None))
        if t and t[0] == "type":
            if self.kind(t[1]):
                out["ref"] = t[1].qual
                out["type"] = "object"
                return out
            if self.prog.subclass_of(t[1], "Enum", "TextChoices", "IntegerChoices", "StrEnum", "IntEnum", "Choices"):
                out["type"] = "int" if self.prog.subclass_of(t[1], "IntegerChoices", "IntEnum") else "str"
                out["choices"] = self.models.enum_values(t[1]) if self.models else None
                out["enum"] = t[1].qual
                return out
        last = (d or "").split(".")[-1]
        out["type"] = PY_TYPES.get(last, "any")
        return out

    def str_list(self, m, e, depth=0):
        """Literal list/tuple of strings, possibly via a module var or `a + b` concatenation; else None."""
        if depth > 5 or e is None:
            return None
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            vals = [const_str(x) for x in e.elts]
            return [v for v in vals if v is not None]
        if isinstance(e, ast.Constant) and e.value == "__all__":
            return "__all__"
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            a, b = self.str_list(m, e.left, depth + 1), self.str_list(m, e.right, depth + 1)
            if isinstance(a, list) and isinstance(b, list):
                return a + b
            return None
        if isinstance(e, (ast.Name, ast.Attribute)):
            t = self.prog.infer(e, Ctx(m, None, None))
            if t and t[0] == "var":
                return self.str_list(t[1], self.prog.var_value(t[1], t[2]), depth + 1)
        return None

    def _meta_opts(self, c: ClassInfo) -> dict:
        out = {}
        for nm in ("Meta", "Config"):
            inner = c.inner.get(nm)
            if inner is None:
                continue
            for st in inner.body:
                if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name):
                    key = st.targets[0].id
                    v = st.value
                    if isinstance(v, ast.Constant):
                        out[key] = v.value
                    elif isinstance(v, ast.Dict):
                        out[key] = v
                    elif key in ("fields", "exclude", "read_only_fields", "model_fields", "model_exclude", "fields_optional",
                                 "model_fields_optional", "brief_fields"):
                        sl = self.str_list(c.module, v)
                        if sl is not None:
                            out[key] = sl
                        else:
                            out[key + "_unresolved"] = ann_text(v)
                    else:
                        out[key] = v
        return out

    def _model_of(self, c: ClassInfo, opts: dict):
        m = opts.get("model")
        if isinstance(m, ast.AST):
            t = self.prog.infer(m, Ctx(c.module, None, None))
            if t and t[0] == "type" and self.models and t[1].qual in self.models.models:
                return t[1].qual
        return None

    def _model_schema_fields(self, c: ClassInfo) -> list:
        opts = self._meta_opts(c)
        mq = self._model_of(c, opts)
        if not mq:
            return []
        mf = self.models.models[mq]["fields"]
        sel = opts.get("fields") if opts.get("fields") is not None else opts.get("model_fields")
        exclude = opts.get("exclude") or opts.get("model_exclude") or []
        exclude = set(exclude) if isinstance(exclude, list) else set()
        names = list(mf) if not isinstance(sel, list) else [x for x in sel if x in mf]
        opt = opts.get("fields_optional") or opts.get("model_fields_optional")
        out = []
        for n in names:
            if n in exclude or mf[n].get("virtual") or mf[n]["type"] == "m2m_rev":
                continue
            fd = mf[n]
            t = MODEL_TO_WIRE.get(fd["type"], fd["type"])
            out.append({"name": n, "json": n, "type": t, "nullable": bool(fd.get("null")), "required": not (fd.get("null") or fd.get("has_default") or fd.get("blank") or fd.get("auto") or opt == "__all__" or (isinstance(opt, list) and n in opt)),
                        "file": fd["file"], "line": fd["line"], "from_model": mq, **({"choices": fd["choices"]} if fd.get("choices") else {})})
        return out

    # ---- DRF
    def _drf(self, c: ClassInfo, kind: str) -> list:
        prog = self.prog
        chain = [c] + [b[1] for b in prog.mro(c) if b[0] == "type" and self.kind(b[1])]
        fields: dict[str, dict] = {}
        opts = {}
        for k in reversed(chain):
            o = self._meta_opts(k)
            if o:
                opts = {**opts, **o}
        mq = self._model_of(c, opts) or next((self._model_of(k, self._meta_opts(k)) for k in chain if self._meta_opts(k)), None)
        if kind == "drf_model" and mq:
            mf = self.models.models[mq]["fields"]
            sel = opts.get("fields")
            exclude = set(opts.get("exclude")) if isinstance(opts.get("exclude"), list) else set()
            names = [n for n in mf if not mf[n].get("virtual") and mf[n]["type"] != "m2m_rev"] if sel in ("__all__", None) else list(sel)
            ro = set(opts.get("read_only_fields") or []) if isinstance(opts.get("read_only_fields"), list) else set()
            if not (sel in ("__all__", None) or isinstance(sel, list)):
                sel = None
            xk = {}
            if isinstance(opts.get("extra_kwargs"), ast.Dict):
                for kk, vv in zip(opts["extra_kwargs"].keys, opts["extra_kwargs"].values):
                    s = const_str(kk)
                    if s and isinstance(vv, ast.Dict):
                        xk[s] = {const_str(a): (b.value if isinstance(b, ast.Constant) else None) for a, b in zip(vv.keys, vv.values)}
            for n in names:
                if n in exclude:
                    continue
                fd = mf.get(n)
                if fd is None:
                    fields[n] = {"name": n, "json": n, "type": "any", "file": c.file, "line": c.line, "unresolved": True,
                                 "required": False, "read_only": True}
                    continue
                t = MODEL_TO_WIRE.get(fd["type"], fd["type"])
                f2 = {"name": n, "json": n, "type": t, "nullable": bool(fd.get("null")), "file": fd["file"], "line": fd["line"],
                      "from_model": mq, "read_only": n in ro or bool(fd.get("auto")) or fd.get("editable") is False,
                      "required": not (fd.get("null") or fd.get("blank") or fd.get("has_default") or fd.get("auto"))}
                if fd.get("choices"):
                    f2["choices"] = fd["choices"]
                e = xk.get(n) or {}
                for kk in ("read_only", "write_only", "required", "allow_null"):
                    if kk in e and e[kk] is not None:
                        f2["nullable" if kk == "allow_null" else kk] = e[kk]
                fields[n] = f2
        for k in reversed(chain):
            for name, (val, line, ann) in k.attrs.items():
                if not isinstance(val, ast.Call) or name in ("Meta",):
                    continue
                t = prog.infer(val.func, Ctx(k.module, None, None))
                fcls = None
                ref = None
                if t and t[0] == "ext":
                    fcls = t[1].split(".")[-1]
                    if fcls not in DRF_TYPES and not fcls.endswith("Field") and not fcls.endswith("Serializer"):
                        continue
                elif t and t[0] == "type":
                    if self.kind(t[1]) in ("drf", "drf_model"):
                        ref = t[1].qual
                        fcls = "Serializer"
                    elif prog.subclass_of(t[1], "Field", "RelatedField", "serializers.Field"):
                        fcls = next((b.split(".")[-1] for b in prog.ext_bases(t[1]) if b.split(".")[-1] in DRF_TYPES), "Field")
                    else:
                        continue
                else:
                    continue
                kw = {x.arg: (x.value.value if isinstance(x.value, ast.Constant) else x.value) for x in val.keywords if x.arg}
                fd = {"name": name, "json": name, "file": k.file, "line": line, "field_class": fcls,
                      "type": "object" if ref else DRF_TYPES.get(fcls, "any"),
                      "read_only": kw.get("read_only") is True or fcls in ("SerializerMethodField", "ReadOnlyField", "HyperlinkedIdentityField", "StringRelatedField"),
                      "write_only": kw.get("write_only") is True,
                      "nullable": kw.get("allow_null") is True}
                fd["required"] = (kw.get("required") is not False) and not fd["read_only"] and "default" not in kw
                if ref:
                    fd["ref"] = ref
                if kw.get("many") is True or fcls in ("ListField", "ManyRelatedField", "MultipleChoiceField"):
                    fd["many"] = True
                src = kw.get("source")
                if isinstance(src, str):
                    fd["source"] = src
                    if mq and src.split(".")[0] in self.models.models[mq]["fields"]:
                        mfd = self.models.models[mq]["fields"][src.split(".")[0]]
                        if "." not in src:
                            fd["nullable"] = fd["nullable"] or bool(mfd.get("null"))
                elif mq and name in self.models.models[mq]["fields"] and fd["type"] in ("any",):
                    mfd = self.models.models[mq]["fields"][name]
                    fd["type"] = MODEL_TO_WIRE.get(mfd["type"], mfd["type"])
                ch = kw.get("choices")
                if isinstance(ch, ast.AST) and self.models:
                    vals = self.models.choices(k, ch)
                    if vals:
                        fd["choices"] = vals
                fields[name] = fd
        return list(fields.values())

    def describe(self, qual: str) -> dict | None:
        c = self.prog.classes.get(qual)
        if c is None:
            return None
        fs = self.fields(c)
        if fs is None:
            return None
        return {"class": qual, "kind": self.kind(c), "fields": fs, "file": c.file, "line": c.line}

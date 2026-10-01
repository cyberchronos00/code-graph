"""JSON wire shapes of Dart model classes.

Sources, strongest first:
  * generated `*.g.dart` (`_$XFromJson` / `_$XToJson`, freezed `_$$XImplFromJson`): exact keys,
    casts and nullability as compiled;
  * json_serializable / freezed annotations when the generated file is absent: field names with
    `@JsonKey(name:)`, `fieldRename` (annotation or build.yaml), `@Default`, `includeFromJson`;
  * hand-written `fromJson`/`fromMap` constructors (`json['key']` reads) and `toJson`/`toMap`
    map literals.
Enums: `@JsonValue('x')` values, `@JsonEnum(fieldRename:/valueField:)`, otherwise the value names.
"""
from __future__ import annotations

import re
from pathlib import Path

from .program import Ctx, DartProgram, DClass, DFunc, DVar, last_name, split_type, walk_repr


def rename(name: str, how: str | None) -> str:
    if not how or how == "none":
        return name
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").split()
    if how == "snake":
        return "_".join(w.lower() for w in words)
    if how == "kebab":
        return "-".join(w.lower() for w in words)
    if how == "screamingSnake":
        return "_".join(w.upper() for w in words)
    if how == "pascal":
        return name[:1].upper() + name[1:]
    return name


def ann_get(anns: list, *names: str) -> dict | None:
    for a in anns or []:
        if a["name"].split(".")[-1] in names:
            return a
    return None


def ann_str(a: dict | None, key: str):
    if not a:
        return None
    v = (a.get("na") or {}).get(key)
    if not v:
        return None
    if v.get("k") == "str":
        return v["v"]
    if v.get("k") in ("bool", "num"):
        return v["v"]
    if v.get("k") == "prop":
        return v["n"]
    if v.get("k") == "id":
        return v["v"]
    return None


class ModelIndex:
    def __init__(self, prog: DartProgram):
        self.prog = prog
        self.cache: dict[str, dict] = {}
        self.build_rename: dict[str, str] = {}
        for pk in prog.pkg_dirs:
            p = (prog.root / pk.dir / "build.yaml") if pk.dir else prog.root / "build.yaml"
            if p.exists():
                m = re.search(r"field_rename:\s*(\w+)", p.read_text(errors="replace"))
                if m:
                    self.build_rename[pk.dir] = m.group(1)

    def keys(self, c: DClass, direction: str) -> list[dict]:
        return self.model(c).get(direction) or []

    def is_model(self, c: DClass) -> bool:
        m = self.model(c)
        return bool(m.get("from") or m.get("to") or m.get("enum_values"))

    def field_type(self, c: DClass, name: str) -> str | None:
        if name in c.fields:
            return c.fields[name].type
        for ct in c.ctors.values():
            for p in ct.params:
                if p["name"] == name and p.get("type"):
                    return p["type"]
        for s in self.prog.mro(c):
            if name in s.fields:
                return s.fields[name].type
        return None

    def ref_of(self, c: DClass, t: str | None):
        tt = self.prog.type_of_text(t, c.lib) if t else None
        many = False
        if tt and tt[0] == "list":
            many, tt = True, tt[1]
        if tt and tt[0] == "inst" and tt[1] is not None:
            return tt[1].id, many
        return None, many

    def model(self, c: DClass) -> dict:
        if c.id in self.cache:
            return self.cache[c.id]
        self.cache[c.id] = {}
        out = {"id": c.id, "name": c.name, "file": c.file, "line": c.line}
        if c.kind == "enum":
            out["enum_values"] = self.enum_values(c)
            self.cache[c.id] = out
            return out
        anns = c.raw.get("ann") or []
        js = ann_get(anns, "JsonSerializable")
        fz = ann_get(anns, "freezed", "Freezed", "unfreezed")
        how = ann_str(js, "fieldRename") or ann_str(fz, "fieldRename")
        pk = c.lib.pkg.dir if c.lib.pkg else ""
        if how is None and (js or fz):
            how = self.build_rename.get(pk)
        gen_from, gen_to = self.generated(c)
        if gen_from is not None:
            out["from"], out["from_source"] = self.from_fn(c, gen_from, "g.dart"), "generated"
        if gen_to is not None:
            out["to"], out["to_source"] = self.to_fn(c, gen_to, "g.dart"), "generated"
        if "from" not in out or "to" not in out:
            hand_from = c.ctors.get("fromJson") or c.ctors.get("fromMap") or c.methods.get("fromJson") or c.methods.get("fromMap")
            if "from" not in out and hand_from is not None and not hand_from.raw.get("redirect") and not self._delegates(hand_from):
                out["from"], out["from_source"] = self.from_fn(c, hand_from, "hand-written"), "hand-written"
            hand_to = c.methods.get("toJson") or c.methods.get("toMap")
            if "to" not in out and hand_to is not None and not self._delegates(hand_to):
                out["to"], out["to_source"] = self.to_fn(c, hand_to, "hand-written"), "hand-written"
        if ("from" not in out or "to" not in out) and (js or fz):
            ann_keys = self.annotated(c, how, bool(fz))
            out.setdefault("from", [k for k in ann_keys if k.get("from", True)])
            out.setdefault("from_source", "annotations (no .g.dart)")
            out.setdefault("to", [k for k in ann_keys if k.get("to", True)])
            out.setdefault("to_source", "annotations (no .g.dart)")
        if how:
            out["field_rename"] = how
        self.cache[c.id] = out
        return out

    def _delegates(self, f: DFunc) -> bool:
        """fromJson => _$XFromJson(json) : generated code holds the keys."""
        for x in f.facts:
            if x["ft"] == "return":
                v = x["v"]
                if v.get("k") == "call" and v.get("n") and v["n"].startswith("_$"):
                    return True
                if v.get("k") == "call" and v.get("n") is None and (v.get("t") or {}).get("v", "").startswith("_$"):
                    return True
        return bool(f.raw.get("redirect"))

    def generated(self, c: DClass):
        names_from = {f"_${c.name}FromJson", f"_$$_{c.name}FromJson", f"_$${c.name}ImplFromJson", f"_$_{c.name}FromJson"}
        names_to = {f"_${c.name}ToJson", f"_$$_{c.name}ToJson", f"_$${c.name}ImplToJson", f"_$_{c.name}ToJson"}
        gf = gt = None
        for n in names_from:
            v = c.lib.decls.get(n)
            if isinstance(v, DFunc):
                gf = v
        for n in names_to:
            v = c.lib.decls.get(n)
            if isinstance(v, DFunc):
                gt = v
        return gf, gt

    def from_fn(self, c: DClass, f: DFunc, source: str) -> list[dict]:
        if not f.params:
            return []
        pn = f.params[0]["name"]
        field_of: dict[int, str] = {}
        # named args / initializer list / positional ctor args -> which field a key feeds
        exprs = []
        for x in f.facts:
            if x["ft"] == "return":
                exprs.append(x["v"])
        for ini in f.raw.get("inits") or []:
            if ini.get("field"):
                for y in walk_repr(ini["v"]):
                    if y.get("k") == "idx":
                        field_of[id(y)] = ini["field"]
        key_field: dict[tuple[str, int], str] = {}
        for e in exprs:
            for y in walk_repr(e):
                if y.get("k") in ("new", "call") and (y.get("na") or y.get("a")):
                    tgt_cls = y.get("type") if y.get("k") == "new" else last_name(y.get("t")) if y.get("n") is None else None
                    for nm, val in (y.get("na") or {}).items():
                        for z in walk_repr(val):
                            if z.get("k") == "idx" and (z.get("i") or {}).get("k") == "str":
                                key_field.setdefault((z["i"]["v"], z.get("l") or 0), nm)
                    ct = c.ctors.get("new") if tgt_cls in (c.name, None) else None
                    if ct:
                        pos = [p for p in ct.params if not p.get("named")]
                        for i, val in enumerate(y.get("a") or []):
                            if i < len(pos):
                                for z in walk_repr(val):
                                    if z.get("k") == "idx" and (z.get("i") or {}).get("k") == "str":
                                        key_field.setdefault((z["i"]["v"], z.get("l") or 0), pos[i]["name"])
        for ini in f.raw.get("inits") or []:
            if ini.get("field"):
                for z in walk_repr(ini["v"]):
                    if z.get("k") == "idx" and (z.get("i") or {}).get("k") == "str":
                        key_field.setdefault((z["i"]["v"], z.get("l") or 0), ini["field"])
        out, seen = [], set()
        for x in f.facts:
            if x["ft"] != "index" or x.get("write") or (x.get("key") or {}).get("k") != "str":
                continue
            tg = x.get("target") or {}
            if not (tg.get("k") == "id" and tg["v"] == pn):
                continue
            key = x["key"]["v"]
            if key in seen:
                continue
            seen.add(key)
            fld = key_field.get((key, x["l"])) or next((v for (k, _), v in key_field.items() if k == key), None)
            ftype = self.field_type(c, fld) if fld else None
            cast = x.get("cast")
            nullable = None
            if cast:
                nullable = cast.endswith("?") or cast == "dynamic"
            if x.get("coalesce") or (x.get("post") and x.get("post") in ("toString",) and False):
                nullable = True
            if nullable is None and ftype:
                nullable = ftype.endswith("?") or ftype == "dynamic"
            ref, many = self.ref_of(c, ftype)
            out.append({"key": key, "field": fld, "type": ftype or cast, "cast": cast, "nullable": nullable,
                        "required": not nullable if nullable is not None else None, "coalesce": x.get("coalesce"),
                        "post": x.get("post"), "wrap": x.get("wrap"), "line": x["l"], "file": f.file, "source": source,
                        "ref": ref, "many": many or None})
        return out

    def to_fn(self, c: DClass, f: DFunc, source: str) -> list[dict]:
        out = []
        ctx = Ctx(self.prog, f.lib, f, f.cls)
        for x in f.facts:
            if x["ft"] != "return":
                continue
            v = x["v"]
            maps = [y for y in walk_repr(v) if y.get("k") == "map"][:1]
            for m in maps:
                for el in m.get("entries") or []:
                    kk = el.get("key") or {}
                    if kk.get("k") != "str":
                        if el.get("k") == "if":
                            th = el.get("then") or {}
                            if (th.get("key") or {}).get("k") == "str":
                                fld = last_name(th.get("value"))
                                ft = self.field_type(c, fld) if fld else None
                                out.append({"key": th["key"]["v"], "field": fld, "type": ft, "nullable": True, "conditional": True,
                                            "line": th.get("l") or m.get("l"), "file": f.file, "source": source})
                        continue
                    fld = last_name(el.get("value"))
                    ft = self.field_type(c, fld) if fld else None
                    ref, many = self.ref_of(c, ft)
                    out.append({"key": kk["v"], "field": fld, "type": ft, "nullable": (ft.endswith("?") if ft else None),
                                "line": el.get("l") or m.get("l"), "file": f.file, "source": source, "ref": ref, "many": many or None})
            if maps:
                break
        return out

    def annotated(self, c: DClass, how: str | None, freezed: bool) -> list[dict]:
        items = []
        if freezed:
            for ct in c.ctors.values():
                if ct.raw.get("factory") and ct.name not in ("fromJson", "fromMap") and ct.raw.get("redirect"):
                    for p in ct.params:
                        items.append((p["name"], p.get("type"), p.get("ann") or [], p.get("default"), ct.line, ct.file, p.get("required")))
                    break
        else:
            for fv in c.fields.values():
                if fv.static:
                    continue
                items.append((fv.name, fv.type, fv.ann, fv.init, fv.line, fv.file, None))
        out = []
        for name, t, anns, default, line, file, req in items:
            jk = ann_get(anns, "JsonKey")
            if jk and (ann_str(jk, "ignore") is True or (ann_str(jk, "includeFromJson") is False and ann_str(jk, "includeToJson") is False)):
                continue
            key = ann_str(jk, "name") or rename(name, how)
            has_default = default is not None or ann_get(anns, "Default") is not None or (jk and "defaultValue" in (jk.get("na") or {}))
            nullable = bool(t and (t.endswith("?") or t == "dynamic"))
            ref, many = self.ref_of(c, t)
            out.append({"key": key, "field": name, "type": t, "nullable": nullable, "required": not nullable and not has_default,
                        "default": bool(has_default) or None, "line": line, "file": file, "source": "annotation", "ref": ref,
                        "many": many or None, "from": not (jk and ann_str(jk, "includeFromJson") is False),
                        "to": not (jk and ann_str(jk, "includeToJson") is False)})
        return out

    def enum_values(self, c: DClass) -> list[dict]:
        je = ann_get(c.raw.get("ann") or [], "JsonEnum")
        how = ann_str(je, "fieldRename")
        vf = ann_str(je, "valueField")
        out = []
        idx = None
        if vf:
            ct = c.ctors.get("new")
            if ct:
                for i, p in enumerate(ct.params):
                    if p["name"] == vf:
                        idx = i
        for v in c.raw.get("values") or []:
            jv = ann_get(v.get("ann") or [], "JsonValue")
            wire, how_ = None, "name"
            if jv and jv.get("a"):
                a0 = jv["a"][0]
                wire, how_ = a0.get("v"), "@JsonValue"
            elif idx is not None and v.get("a") and idx < len(v["a"]):
                wire, how_ = v["a"][idx].get("v"), f"valueField {vf}"
            else:
                wire = rename(v["name"], how)
            out.append({"name": v["name"], "wire": wire, "how": how_, "line": v.get("l"), "file": c.file})
        return out

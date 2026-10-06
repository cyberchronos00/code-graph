"""Django models -> tables/columns/relations, ORM type rules and ORM read/write edges."""
from __future__ import annotations

import ast
import re

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ..python.plugin import ClassInfo, Ctx, PyProgram, const_str, dotted, kwarg, walk_body

MODEL_BASES = ("django.db.models.Model", "django.db.models.base.Model", "models.Model")
MODEL_BASE_NAMES = {"Model", "AbstractUser", "AbstractBaseUser", "PermissionsMixin", "MPTTModel", "TimeStampedModel",
                    "PolymorphicModel", "ClusterableModel", "Page", "TranslatableMixin", "AbstractBaseSession",
                    "AbstractImage", "AbstractDocument", "AbstractRendition", "TitleSlugDescriptionModel",
                    "SoftDeletableModel", "StatusModel", "UUIDModel", "OrderedModel", "TreeNode", "MP_Node", "NS_Node",
                    "AL_Node", "PostgresModel", "ContentType"}
# Field class name -> wire/value type
FIELD_TYPES = {
    "AutoField": "int", "BigAutoField": "int", "SmallAutoField": "int", "IntegerField": "int", "BigIntegerField": "int",
    "SmallIntegerField": "int", "PositiveIntegerField": "int", "PositiveSmallIntegerField": "int",
    "PositiveBigIntegerField": "int", "FloatField": "float", "DecimalField": "decimal", "BooleanField": "bool",
    "NullBooleanField": "bool", "CharField": "str", "TextField": "str", "SlugField": "str", "EmailField": "str",
    "URLField": "str", "UUIDField": "uuid", "DateTimeField": "datetime", "DateField": "date", "TimeField": "time",
    "DurationField": "duration", "JSONField": "json", "FileField": "file", "ImageField": "file", "FilePathField": "str",
    "GenericIPAddressField": "str", "IPAddressField": "str", "BinaryField": "bytes", "ArrayField": "list",
    "HStoreField": "dict", "ForeignKey": "fk", "OneToOneField": "fk", "ManyToManyField": "m2m",
    "TreeForeignKey": "fk", "ParentalKey": "fk", "ParentalManyToManyField": "m2m", "CountryField": "str",
    "PhoneNumberField": "str", "MoneyField": "decimal", "RichTextField": "str", "StreamField": "json",
    "ColorField": "str", "TaggableManager": "m2m", "GenericForeignKey": "generic", "GenericRelation": "m2m_rev",
    "SearchVectorField": "str", "CICharField": "str", "CIEmailField": "str", "CITextField": "str", "MACAddressField": "str",
}
QS_METHODS = {"filter", "exclude", "all", "order_by", "select_related", "prefetch_related", "annotate", "distinct",
              "only", "defer", "using", "select_for_update", "reverse", "none", "union", "intersection", "difference",
              "extra", "alias", "filter_by", "active", "visible", "published", "for_user", "restrict"}
INST_METHODS = {"get", "first", "last", "latest", "earliest", "create", "get_by_natural_key"}
TUPLE_METHODS = {"get_or_create", "update_or_create"}
READ_METHODS = QS_METHODS | {"get", "first", "last", "latest", "earliest", "count", "exists", "values", "values_list",
                             "aggregate", "iterator", "in_bulk", "raw", "dates", "datetimes", "contains", "explain",
                             "aget", "afirst", "alast", "acount", "aexists", "aiterator", "aaggregate"}
WRITE_METHODS = {"create", "update", "delete", "bulk_create", "bulk_update", "get_or_create", "update_or_create",
                 "acreate", "aupdate", "adelete", "abulk_create", "abulk_update", "aget_or_create", "aupdate_or_create"}
LOOKUP_KW = {"filter", "exclude", "get", "get_or_create", "update_or_create", "aget", "aget_or_create", "aupdate_or_create"}
WRITE_KW = {"create", "update", "acreate", "aupdate"}


def snake(name: str) -> str:
    return name.lower()


class Models:
    """Model registry over a PyProgram."""

    def __init__(self, prog: PyProgram):
        self.prog = prog
        self.models: dict[str, dict] = {}       # class qual -> info
        self.by_name: dict[str, list[str]] = {}  # short / app_label.Name -> quals
        self.app_labels = self._app_labels()

    def _app_labels(self) -> dict[str, str]:
        """app package -> label (AppConfig.label overrides the last path component)."""
        out = {}
        for m in self.prog.modules.values():
            if not m.name.endswith(".apps") and m.name != "apps":
                continue
            for c in m.classes.values():
                if not self.prog.subclass_of(c, "AppConfig"):
                    continue
                name = const_str((c.attrs.get("name") or (None,))[0])
                label = const_str((c.attrs.get("label") or (None,))[0])
                pkg = name or m.name.rpartition(".")[0]
                out[pkg] = label or pkg.split(".")[-1]
                out[m.name.rpartition(".")[0]] = label or (name or pkg).split(".")[-1]
        return out

    def is_model(self, c: ClassInfo) -> bool:
        if c.qual in self.models:
            return True
        for b in self.prog.ext_bases(c):
            last = b.split(".")[-1]
            if b in MODEL_BASES or (last in MODEL_BASE_NAMES and ("models" in b or "django" in b or last == "Model")):
                return True
        return False

    def meta(self, c: ClassInfo) -> dict:
        out = {}
        m = c.inner.get("Meta")
        if m is None:
            return out
        for st in m.body:
            if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name):
                k = st.targets[0].id
                v = st.value
                if isinstance(v, ast.Constant):
                    out[k] = v.value
                elif isinstance(v, (ast.List, ast.Tuple)):
                    out[k] = [const_str(x) for x in v.elts if const_str(x)]
                else:
                    out[k] = v
        return out

    def app_label(self, c: ClassInfo) -> str:
        meta = self.meta(c)
        if isinstance(meta.get("app_label"), str):
            return meta["app_label"]
        parts = c.module.name.split(".")
        if "models" in parts:
            pkg = ".".join(parts[:parts.index("models")])
        else:
            pkg = ".".join(parts[:-1])
        if pkg in self.app_labels:
            return self.app_labels[pkg]
        return pkg.split(".")[-1] if pkg else c.module.name.split(".")[0]

    def build(self) -> None:
        prog = self.prog
        cands = [c for c in prog.classes.values() if c.outer is None and self.is_model(c)]
        for c in cands:
            meta = self.meta(c)
            abstract = meta.get("abstract") is True
            label = self.app_label(c)
            info = {"class": c, "abstract": abstract, "label": label, "meta": meta, "fields": {}, "managers": {},
                    "proxy": meta.get("proxy") is True}
            if not abstract:
                info["table"] = meta["db_table"] if isinstance(meta.get("db_table"), str) else f"{label}_{snake(c.name)}"
                info["table_explicit"] = isinstance(meta.get("db_table"), str)
            self.models[c.qual] = info
            self.by_name.setdefault(c.name, []).append(c.qual)
            self.by_name.setdefault(f"{label}.{c.name}", []).append(c.qual)
        for q, info in self.models.items():
            c = info["class"]
            if info["proxy"]:
                for b in prog.mro(c):
                    if b[0] == "type" and b[1].qual in self.models and "table" in self.models[b[1].qual] and not self.models[b[1].qual]["proxy"]:
                        info["table"] = self.models[b[1].qual]["table"]
                        break
            # own + abstract-inherited fields
            chain = [c] + [b[1] for b in prog.mro(c) if b[0] == "type" and b[1].qual in self.models and self.models[b[1].qual]["abstract"]]
            for k in reversed(chain):
                for name, (val, line, ann) in k.attrs.items():
                    fd = self.field_def(k, name, val, line)
                    if fd:
                        info["fields"][name] = fd
                    mgr = self.manager_def(k, val)
                    if mgr is not None:
                        info["managers"][name] = mgr
            # fields inherited from django.contrib.auth abstract bases (external, so not parsed)
            ext = {b.split(".")[-1] for b in prog.lineage(c)}
            implicit = []
            if ext & {"AbstractBaseUser", "AbstractUser"}:
                implicit += [("password", "CharField", "str", False), ("last_login", "DateTimeField", "datetime", True)]
            if ext & {"AbstractUser", "PermissionsMixin"}:
                implicit += [("is_superuser", "BooleanField", "bool", False)]
            if "AbstractUser" in ext:
                implicit += [("username", "CharField", "str", False), ("first_name", "CharField", "str", False),
                             ("last_name", "CharField", "str", False), ("email", "EmailField", "str", False),
                             ("is_staff", "BooleanField", "bool", False), ("is_active", "BooleanField", "bool", False),
                             ("date_joined", "DateTimeField", "datetime", False)]
            for name, cls_, typ, null in implicit:
                info["fields"].setdefault(name, {"name": name, "column": name, "type": typ, "class": cls_, "null": null, "line": c.line,
                                                 "file": c.file, "owner": c.qual, "inherited_from": "django.contrib.auth"})
            if not any(f.get("primary_key") for f in info["fields"].values()):
                parent = next((b[1] for b in prog.mro(c) if b[0] == "type" and b[1].qual in self.models and
                               not self.models[b[1].qual]["abstract"]), None)
                pk = {"name": "id", "column": "id", "type": "int", "auto": True, "primary_key": True, "line": c.line,
                      "file": c.file, "null": False, "class": "AutoField"}
                if parent is not None and not info["proxy"]:
                    pk = {**pk, "name": f"{snake(parent.name)}_ptr", "column": f"{snake(parent.name)}_ptr_id", "type": "fk",
                          "target": parent.qual}
                info["fields"].setdefault(pk["name"], pk)
                info["pk"] = pk["name"]
            else:
                info["pk"] = next(n for n, f in info["fields"].items() if f.get("primary_key"))
        # resolve relation targets
        for info in self.models.values():
            for f in info["fields"].values():
                if f.get("target_expr") is not None:
                    f["target"] = self.resolve_target(info["class"], f.pop("target_expr"))
                    f.pop("target_expr", None)

    def field_class(self, k: ClassInfo, val):
        if not isinstance(val, ast.Call):
            return None
        t = self.prog.infer(val.func, Ctx(k.module, None, None))
        name = None
        if t and t[0] == "ext":
            name = t[1].split(".")[-1]
        elif t and t[0] == "type":
            # local Field subclass: use the nearest external base
            for b in self.prog.ext_bases(t[1]):
                if b.split(".")[-1] in FIELD_TYPES:
                    name = b.split(".")[-1]
                    break
            name = name or (t[1].name if t[1].name.endswith("Field") else None)
        else:
            d = dotted(val.func) or ""
            last = d.split(".")[-1]
            if last.endswith("Field") or last in ("ForeignKey", "TaggableManager", "ParentalKey"):
                name = last
        if name and (name in FIELD_TYPES or name.endswith("Field") or name in ("ForeignKey",)):
            return name
        return None

    def field_def(self, k: ClassInfo, name: str, val, line: int) -> dict | None:
        fc = self.field_class(k, val)
        if not fc:
            return None
        call: ast.Call = val
        typ = FIELD_TYPES.get(fc, "any")
        fd = {"name": name, "class": fc, "type": typ, "line": line, "file": k.file, "owner": k.qual}
        for kw in ("null", "blank", "primary_key", "unique", "db_index", "editable"):
            v = kwarg(call, kw)
            if isinstance(v, ast.Constant):
                fd[kw] = v.value
        fd.setdefault("null", False)
        if kwarg(call, "default") is not None:
            fd["has_default"] = True
        dbc = const_str(kwarg(call, "db_column"))
        if typ == "fk":
            fd["column"] = dbc or f"{name}_id"
            fd["target_expr"] = call.args[0] if call.args else kwarg(call, "to")
            rn = const_str(kwarg(call, "related_name"))
            if rn:
                fd["related_name"] = rn
        elif typ in ("m2m",):
            fd["target_expr"] = call.args[0] if call.args else kwarg(call, "to")
            th = kwarg(call, "through")
            fd["through"] = const_str(th) or dotted(th)
            rn = const_str(kwarg(call, "related_name"))
            if rn:
                fd["related_name"] = rn
        elif typ in ("generic", "m2m_rev"):
            return {**fd, "virtual": True}
        else:
            fd["column"] = dbc or name
        ml = kwarg(call, "max_length")
        if isinstance(ml, ast.Constant):
            fd["max_length"] = ml.value
        ch = kwarg(call, "choices")
        if ch is not None:
            fd["choices"] = self.choices(k, ch)
        if fc == "ArrayField" and call.args:
            inner = self.field_class(k, call.args[0])
            fd["item_type"] = FIELD_TYPES.get(inner or "", "any")
        return fd

    def choices(self, k: ClassInfo, e) -> list | None:
        """Choice values from a literal list of pairs, a TextChoices/IntegerChoices class (X.choices), or a module var."""
        prog = self.prog
        if isinstance(e, ast.Attribute) and e.attr == "choices":
            t = prog.infer(e.value, Ctx(k.module, None, None))
            if t and t[0] == "type":
                return self.enum_values(t[1])
        if isinstance(e, ast.Name):
            t = prog.infer(e, Ctx(k.module, None, k))
            if t and t[0] == "var":
                e = prog.var_value(t[1], t[2])
            elif e.id in k.attrs:
                e = k.attrs[e.id][0]
        if isinstance(e, (ast.List, ast.Tuple)):
            out = []
            for it in e.elts:
                if isinstance(it, (ast.Tuple, ast.List)) and it.elts and isinstance(it.elts[0], ast.Constant):
                    out.append(it.elts[0].value)
            return out or None
        return None

    def enum_values(self, c: ClassInfo) -> list | None:
        out = []
        for name, (val, line, ann) in c.attrs.items():
            if name.startswith("_") or name in ("Meta",):
                continue
            if isinstance(val, ast.Tuple) and val.elts and isinstance(val.elts[0], ast.Constant):
                out.append(val.elts[0].value)
            elif isinstance(val, ast.Constant):
                out.append(val.value)
            elif isinstance(val, ast.Call) and dotted(val.func) in ("auto", "enum.auto"):
                out.append(name.lower())
        return out or None

    def manager_def(self, k: ClassInfo, val):
        if not isinstance(val, ast.Call):
            return None
        ctx = Ctx(k.module, None, None)
        f = val.func
        if isinstance(f, ast.Attribute) and f.attr in ("as_manager", "from_queryset"):
            t = self.prog.infer(f.value, ctx)
            return t[1] if t and t[0] == "type" else False
        if isinstance(f, ast.Call) and isinstance(f.func, ast.Attribute) and f.func.attr == "from_queryset":
            t = self.prog.infer(f.args[0], ctx) if f.args else None
            return t[1] if t and t[0] == "type" else False
        t = self.prog.infer(f, ctx)
        if t and t[0] == "type" and self.prog.subclass_of(t[1], "Manager", "BaseManager", "QuerySet"):
            return t[1]
        if t and t[0] == "ext" and t[1].split(".")[-1] in ("Manager", "QuerySet") :
            return False
        return None

    def resolve_target(self, c: ClassInfo, e):
        if e is None:
            return None
        s = const_str(e)
        if s is not None:
            if s == "self":
                return c.qual
            if s in self.by_name and len(self.by_name[s]) == 1:
                return self.by_name[s][0]
            short = s.split(".")[-1]
            qs = self.by_name.get(short) or []
            if "." in s:
                qs = [q for q in qs if self.models[q]["label"] == s.split(".")[0]] or qs
            return qs[0] if len(qs) == 1 else (f"?{s}" if not qs else qs[0])
        t = self.prog.infer(e, Ctx(c.module, None, None))
        if t and t[0] == "type":
            return t[1].qual
        if t and t[0] == "ext":
            return f"?{t[1]}"
        return None

    def table(self, qual: str) -> str | None:
        m = self.models.get(qual)
        return m.get("table") if m else None

    def field(self, qual: str, name: str) -> dict | None:
        m = self.models.get(qual)
        if not m:
            return None
        if name == "pk":
            name = m.get("pk", "id")
        f = m["fields"].get(name)
        if f is None and name.endswith("_id"):
            f = m["fields"].get(name[:-3])
            if f and f["type"] != "fk":
                f = None
        return f

    # ---- type rules
    def attr_rule(self, prog, t, attr, ctx):
        if t is None:
            return None
        if t[0] == "type" and t[1].qual in self.models:
            info = self.models[t[1].qual]
            if attr in ("objects", "_default_manager", "_base_manager") or attr in info["managers"]:
                return ("qs", t[1])
            return None
        if t[0] == "inst" and t[1].qual in self.models:
            info = self.models[t[1].qual]
            f = info["fields"].get(attr)
            if f and f["type"] == "fk" and isinstance(f.get("target"), str) and f["target"] in prog.classes:
                return ("inst", prog.classes[f["target"]])
            if f and f["type"] == "m2m" and isinstance(f.get("target"), str) and f["target"] in prog.classes:
                return ("qs", prog.classes[f["target"]])
            rev = self.reverse(t[1].qual).get(attr)
            if rev:
                return rev
            return None
        if t[0] == "qs":
            mc = t[1]
            for mgr in self.models.get(mc.qual, {}).get("managers", {}).values():
                if mgr:
                    f = prog.find_method(mgr, attr)
                    if f:
                        return ("bound", f, ("inst", mgr))
            return ("qsm", mc, attr)
        return None

    def reverse(self, qual: str) -> dict:
        if not hasattr(self, "_rev"):
            self._rev = {}
            for q, info in self.models.items():
                for f in info["fields"].values():
                    tg = f.get("target")
                    if not isinstance(tg, str) or tg not in self.prog.classes:
                        continue
                    c = info["class"]
                    if f["type"] == "fk":
                        rn = f.get("related_name") or (f"{snake(c.name)}_set" if f["class"] != "OneToOneField" else snake(c.name))
                        self._rev.setdefault(tg, {})[rn] = ("qs", c) if f["class"] != "OneToOneField" else ("inst", c)
                    elif f["type"] == "m2m":
                        rn = f.get("related_name") or f"{snake(c.name)}_set"
                        self._rev.setdefault(tg, {})[rn] = ("qs", c)
        return self._rev.get(qual, {})

    def call_rule(self, prog, call, ft, ctx):
        if ft is not None and ft[0] == "qsm":
            mc, m = ft[1], ft[2]
            if m in QS_METHODS:
                return ("qs", mc)
            if m in INST_METHODS or m in ("aget", "afirst", "alast", "acreate"):
                return ("inst", mc)
            if m in TUPLE_METHODS or m in ("aget_or_create", "aupdate_or_create"):
                return ("tuple", [("inst", mc), None])
            return ("qsr", mc, m)
        if ft is not None and ft[0] == "ext" and ft[1].split(".")[-1] in ("get_object_or_404", "get_list_or_404", "aget_object_or_404") and call.args:
            t = prog.infer(call.args[0], ctx)
            if t and t[0] == "type" and t[1].qual in self.models:
                return ("inst", t[1]) if "object" in ft[1] else ("qs", t[1])
            if t and t[0] == "qs":
                return ("inst", t[1]) if "object" in ft[1] else t
        return None


def orm_edges(models: Models, b, f, stats) -> None:
    """READS/WRITES_TABLE and READS/WRITES_COLUMN for one function."""
    prog = models.prog
    ctx = Ctx(f.module, f, f.cls)
    for sub in walk_body(f.node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            meth = sub.func.attr
            rt = prog.infer(sub.func.value, ctx)
            if rt and rt[0] == "qs" and rt[1].qual in models.models:
                tbl = models.table(rt[1].qual)
                if not tbl:
                    continue
                tid = f"table:{tbl}"
                if meth in WRITE_METHODS:
                    b.add_edge(f.id, tid, "WRITES_TABLE", f.file, sub.lineno, RESOLVED, via=f"orm.{meth}")
                    stats["orm_writes"] += 1
                if meth in READ_METHODS or meth in ("get_or_create", "update_or_create", "delete", "update"):
                    if meth not in ("create", "bulk_create", "acreate", "abulk_create"):
                        b.add_edge(f.id, tid, "READS_TABLE", f.file, sub.lineno, RESOLVED, via=f"orm.{meth}")
                        stats["orm_reads"] += 1
                kws = [k for k in sub.keywords if k.arg]
                for k in kws:
                    if k.arg == "defaults" and isinstance(k.value, ast.Dict):
                        for kk in k.value.keys:
                            s = const_str(kk)
                            if s:
                                _col(models, b, f, rt[1].qual, s, "WRITES_COLUMN", sub.lineno, via=f"orm.{meth}(defaults)")
                        continue
                    if meth in LOOKUP_KW or meth in QS_METHODS:
                        _col(models, b, f, rt[1].qual, k.arg.split("__")[0], "READS_COLUMN", sub.lineno, via=f"orm.{meth}")
                    if meth in WRITE_KW or (meth in TUPLE_METHODS and False):
                        _col(models, b, f, rt[1].qual, k.arg.split("__")[0], "WRITES_COLUMN", sub.lineno, via=f"orm.{meth}")
                if meth in ("order_by", "values", "values_list", "only", "defer"):
                    for a in sub.args:
                        s = const_str(a)
                        if s:
                            _col(models, b, f, rt[1].qual, s.lstrip("-").split("__")[0], "READS_COLUMN", sub.lineno, via=f"orm.{meth}")
            elif rt and rt[0] == "inst" and rt[1].qual in models.models:
                tbl = models.table(rt[1].qual)
                if tbl and meth in ("save", "delete", "asave", "adelete", "refresh_from_db", "arefresh_from_db"):
                    kind = "READS_TABLE" if "refresh" in meth else "WRITES_TABLE"
                    b.add_edge(f.id, f"table:{tbl}", kind, f.file, sub.lineno, RESOLVED, via=f"instance.{meth}")
                    stats["orm_writes" if kind == "WRITES_TABLE" else "orm_reads"] += 1
                    uf = kwarg(sub, "update_fields")
                    if isinstance(uf, (ast.List, ast.Tuple)):
                        for x in uf.elts:
                            s = const_str(x)
                            if s:
                                _col(models, b, f, rt[1].qual, s, "WRITES_COLUMN", sub.lineno, via="save(update_fields)")
        elif isinstance(sub, ast.Call):
            ft = prog.infer(sub.func, ctx)
            if ft and ft[0] == "type" and ft[1].qual in models.models:
                for k in sub.keywords:
                    if k.arg:
                        _col(models, b, f, ft[1].qual, k.arg, "WRITES_COLUMN", sub.lineno, via="Model(...)", conf=HEURISTIC)
        elif isinstance(sub, ast.Attribute) and not isinstance(getattr(sub, "_parent_call", None), ast.Call):
            if isinstance(sub.value, ast.Name) and sub.value.id in ("self", "cls") and f.cls is not None and f.cls.qual in models.models:
                rt = ("inst", f.cls)
            else:
                rt = prog.infer(sub.value, ctx) if isinstance(sub.value, (ast.Name, ast.Attribute)) else None
            if rt and rt[0] == "inst" and rt[1].qual in models.models:
                fd = models.field(rt[1].qual, sub.attr)
                if fd and fd.get("column"):
                    kind = "WRITES_COLUMN" if isinstance(sub.ctx, ast.Store) else "READS_COLUMN"
                    _col(models, b, f, rt[1].qual, sub.attr, kind, sub.lineno, via="attribute")


def _col(models: Models, b, f, qual, name, kind, line, via, conf=RESOLVED):
    fd = models.field(qual, name)
    tbl = models.table(qual)
    if not fd or not tbl or not fd.get("column"):
        return
    b.add_edge(f.id, f"column:{tbl}.{fd['column']}", kind, f.file, line, conf, via=via)


def emit_models(models: Models, b) -> dict:
    n_t = n_c = n_r = 0
    for q, info in models.models.items():
        c = info["class"]
        attrs = {"django_model": True, "abstract": info["abstract"], "app_label": info["label"],
                 "fields": [{k: v for k, v in fd.items() if k not in ("owner",)} for fd in info["fields"].values()]}
        b.nodes[c.id].attrs.update(attrs)
        tbl = info.get("table")
        if not tbl:
            continue
        tid = b.add_node("table", tbl, file=c.file, line=c.line, module=c.module.name, lang="sql",
                         attrs={"model": q, "explicit": info.get("table_explicit", False)})
        n_t += 1
        b.add_edge(c.id, tid, "MAPS_TO_TABLE", c.file, c.line, EXACT if info.get("table_explicit") else RESOLVED,
                   rule="Meta.db_table" if info.get("table_explicit") else "<app_label>_<model>")
        for fd in info["fields"].values():
            if fd.get("column"):
                cid = b.add_node("column", f"{tbl}.{fd['column']}", name=fd["column"], fqn=f"{tbl}.{fd['column']}",
                                 file=fd["file"], line=fd["line"], lang="sql",
                                 attrs={k: fd[k] for k in ("class", "type", "null", "choices", "max_length", "target") if k in fd})
                b.add_edge(tid, cid, "CONTAINS", fd["file"], fd["line"], EXACT)
                b.add_edge(c.id, cid, "DEFINES", fd["file"], fd["line"], EXACT)
                n_c += 1
            tg = fd.get("target")
            if fd["type"] in ("fk", "m2m") and isinstance(tg, str) and not tg.startswith("?"):
                b.add_edge(c.id, f"class:{tg}", "HAS_RELATION", fd["file"], fd["line"], EXACT, relation=fd["class"], field=fd["name"])
                n_r += 1
                if fd["type"] == "m2m" and not fd.get("through"):
                    tt = models.table(tg)
                    if tt:
                        b.add_node("table", f"{tbl}_{fd['name']}", file=fd["file"], line=fd["line"], lang="sql",
                                   attrs={"m2m_through": True, "between": [tbl, tt]})
    return {"models": len(models.models), "tables": n_t, "columns": n_c, "relations": n_r}

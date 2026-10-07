"""Column writes through mass assignment (#177).

`$book->update($request->validated())`, `Book::create($data)`, `$book->fill($request->only([...]))`, `new Book($data)` +
`save()` and `DB::table('books')->update($data)` write columns whose names are not in the call. The keys come from the
request array the argument carries:

* `validated()` / `safe()` -> the top-level keys of the FormRequest `rules()` (`items.*.qty` -> `items`) or of the inline
  `$request->validate([...])` of the same function;
* `only([...])` -> the literal keys; `except([...])` -> the request's keys minus the literal ones;
* `all()` / `input()` / `post()` / ... -> no key filter: the model's `$fillable` keys, at `heuristic`;
* a local or a parameter holding one is followed through assignments and call arguments (the request-array flow of
  `values.py`); `array_merge(a, b)` and `[...$a, 'k' => v]` contribute both key sets.

Then the model's mass-assignment rules apply, except for `forceFill` / `forceCreate` and for query-builder writes
(`->update`, `insert`, `upsert` never consult `$fillable`): keys outside a declared `$fillable` get no edge, and `$guarded`
keys are dropped (`$guarded = []` keeps every key). Edges are `WRITES_COLUMN` with `attrs.via` (`update(validated())`)
and `attrs.keys_from` (`["UpdateBookRequest::rules", "Book::$fillable"]`).
"""
from __future__ import annotations

from ...core.model import HEURISTIC, RESOLVED

SAVES = {"save", "savequietly", "saveorfail", "push"}
FORCE = {"forcecreate", "forcefill"}
BYPASS = {"insert", "insertgetid", "insertorignore", "upsert", "updateorinsert"}
ONE = {"firstorcreate", "updateorcreate", "firstornew", "updateorinsert"}   # (attributes, values): the values are written
ALL_INPUT = {"all", "input", "post", "query", "json", "toarray", "collect"}
PASS_THROUGH = {"toarray", "all", "collect", "values"}
MAX_DEPTH = 6


def _short(fqcn: str) -> str:
    return (fqcn or "").rsplit("\\", 1)[-1]


def _str_list(d) -> list[str] | None:
    """Literal string values of an array / string / varargs argument, or None when any is not literal."""
    if not isinstance(d, dict):
        return None
    if d.get("k") == "str":
        return [d["v"]]
    if d.get("k") != "arr":
        return None
    out = []
    for it in d.get("items") or []:
        v = it.get("v") or {}
        if v.get("k") != "str" or it.get("key") is not None and (it["key"] or {}).get("k") not in ("str", "int", None):
            return None
        out.append(v["v"])
    return out


class MassAssign:
    def __init__(self, va):
        self.va, self.lv, self.prog = va, va.lv, va.prog
        self._rules: dict[str, tuple[list[str], str] | None] = {}

    # ------------------------------------------------------------------ rules / model declarations
    def rules_of(self, cls: str):
        """([top-level keys], 'Class::rules') of a FormRequest class, or None."""
        if cls in self._rules:
            return self._rules[cls]
        res = None
        tc = self.prog.cls(cls)
        if tc and "rules" in tc.methods and any(a.lower() == "illuminate\\foundation\\http\\formrequest" for a in self.prog.ancestors(tc.fqcn)):
            keys = []
            for f in tc.methods["rules"].facts:
                if f["t"] != "return" or (f.get("expr") or {}).get("k") != "arr" or f.get("ctx"):
                    continue
                for it in f["expr"].get("items") or []:
                    kd = it.get("key") or {}
                    if kd.get("k") == "str" and kd.get("v"):
                        keys.append(kd["v"].split(".")[0])
            res = (list(dict.fromkeys(keys)), f"{_short(tc.fqcn)}::rules")
        self._rules[cls] = res
        return res

    def request_rules(self, recv, fn) -> tuple[list[str], str] | None:
        """Validated keys of a request receiver: the FormRequest type's rules plus the function's inline `validate([...])`."""
        keys, srcs = [], []
        for t in sorted(self.va.types(recv, fn)):
            r = self.rules_of(t)
            if r:
                keys += r[0]
                srcs.append(r[1])
        inline = self.va._inline_rule_keys(fn, "body")
        if inline:
            keys += [k["name"].split(".")[0] for k in inline]
            srcs.append(f"{_short(fn.cls) + '::' if fn.cls else ''}{fn.name} validate()")
        return (list(dict.fromkeys(keys)), " + ".join(srcs)) if srcs else None

    def declared(self, model: str | None, prop: str):
        """The literal list of `$fillable` / `$guarded`, or None when the model does not declare it."""
        p = self.prog.find_prop(model, prop) if model else None
        if not p or not isinstance(p.get("default"), list):
            return None
        return [x for x in p["default"] if isinstance(x, str)]

    # ------------------------------------------------------------------ key sets of an argument
    def parts(self, d, fn, line, depth=0, seen=None) -> list[dict]:
        """Key sets a descriptor evaluates to: `{keys: set|None, kind, via, srcs, alt}`. `keys` None = every input key (`all()`)."""
        if not isinstance(d, dict) or depth > MAX_DEPTH:
            return []
        seen = seen if seen is not None else set()
        k = d.get("k")
        if k == "var":
            return self._var_parts(d["n"], fn, line, depth, seen)
        if k == "alt":
            return [p for o in d.get("opts") or [] for p in self.parts(o, fn, line, depth + 1, seen)]
        if k == "arr":
            out = []
            for it in d.get("items") or []:
                key = it.get("key")
                if key is None:
                    out += self.parts(it.get("v"), fn, line, depth + 1, seen)
                elif key.get("k") == "str":
                    out.append({"keys": {key["v"]}, "kind": "literal", "via": None, "srcs": [], "alt": None})
            return out
        if k == "func":
            n = (d.get("n") or "").lower()
            if n not in ("array_merge", "array_replace", "array_filter", "array_values", "collect"):
                return []
            out = [p for a in d.get("args") or [] for p in self.parts(a, fn, line, depth + 1, seen)]
            if n in ("array_merge", "array_replace"):
                out = [{**p, "via": f"{n}({p['via'] or ''})"} for p in out]
            return out
        if k != "mcall":
            return []
        m = (d.get("m") or "").lower()
        of, args = d.get("of"), d.get("args") or []
        if self.va.is_request_recv(of, fn):
            return self._request_parts(d, m, of, args, fn)
        base = self.parts(of, fn, line, depth + 1, seen)
        if not base:
            return []
        if m in ("only", "except") and args:
            lits = _str_list(args[0]) if len(args) == 1 else [s for a in args for s in (_str_list(a) or [])]
            if lits is None:
                return []
            return [self._narrow(p, set(lits), m) for p in base]
        if m in PASS_THROUGH:
            return base
        return []

    def _narrow(self, p, lits: set, m: str) -> dict:
        p = dict(p)
        if m == "only":
            p["keys"] = lits if p["keys"] is None else p["keys"] & lits
            p["via"] = f"{p['via']}->only()" if p.get("via") else "only()"
            if p["kind"] == "all":
                p["kind"], p["alt"] = "only", None
        else:
            p["excluded"] = set(p.get("excluded") or ()) | lits
            p["via"] = f"{p['via']}->except()" if p.get("via") else "except()"
            if p["keys"] is not None:
                p["keys"] = p["keys"] - lits
        return p

    def _request_parts(self, d, m: str, of, args, fn) -> list[dict]:
        rules = self.request_rules(of, fn)
        if m in ("validated", "safe") or (m == "validate" and not args):
            if not rules:
                return []
            return [{"keys": set(rules[0]), "kind": "rules", "via": f"{m}()", "srcs": [rules[1]], "alt": None}]
        if m == "validate" and args and args[0].get("k") == "arr":
            ks = {(it.get("key") or {}).get("v", "").split(".")[0] for it in args[0].get("items") or []
                  if (it.get("key") or {}).get("k") == "str"}
            return [{"keys": ks - {""}, "kind": "rules", "via": "validate()", "srcs": [f"{_short(fn.cls) + '::' if fn.cls else ''}{fn.name} validate()"],
                     "alt": None}]
        if m in ("only", "except") and args:
            lits = [s for a in args for s in (_str_list(a) or [])]
            if _str_list(args[0]) is None and not lits:
                return []
            if m == "only":
                return [{"keys": set(lits), "kind": "only", "via": "only()", "srcs": [], "alt": None}]
            return [{"keys": set(rules[0]) - set(lits) if rules else None, "kind": "all" if not rules else "rules", "via": "except()",
                     "srcs": [rules[1]] if rules else [], "alt": None, "excluded": set(lits)}]
        if m in ALL_INPUT and not args:
            return [{"keys": None, "kind": "all", "via": f"{m}()", "srcs": [], "alt": rules}]
        return []

    def _var_parts(self, name: str, fn, line, depth, seen) -> list[dict]:
        key = (fn.id, name)
        if key in seen:
            return []
        seen = seen | {key}
        a = self.va.assign_for(fn, name, line)
        if a and a["var"] == name:
            ps = self.parts(a["expr"], fn, a.get("line") or line, depth + 1, seen)
            if ps:
                return ps
        pnames = [p["name"] for p in fn.params]
        if name not in pnames:
            return []
        pi = pnames.index(name)
        out = []
        for cfn, cf, _i, _c in self.va.callers.get(fn.id, []):
            for ai, arg in enumerate(cf.get("args") or []):
                if self.va.param_index(fn, ai, arg) != pi:
                    continue
                for p in self.parts(arg, cfn, cf.get("line"), depth + 1, seen):
                    out.append({**p, "from_param": f"${name}"})
        return out

    # ------------------------------------------------------------------ emission
    def emit(self, pending: list[dict]) -> int:
        n = 0
        for w in pending:
            n += self._emit_one(w)
        return n

    def _emit_one(self, w: dict) -> int:
        fn, f = w["fn"], w["f"]
        ml = (f.get("m") or w.get("m") or "").lower()
        args = f.get("args") or []
        if ml == "new":
            idx = 0
        else:
            idx = 1 if ml in ONE and len(args) > 1 else 0
        if len(args) <= idx:
            return 0
        line = f.get("line")
        parts = self.parts(args[idx], fn, line)
        if args[idx].get("k") == "arr":
            parts = [p for p in parts if p["kind"] != "literal"]   # the literal keys of the argument itself are `_col_args`'s
        if not parts:
            return 0
        kind, model = w["recv"]
        if ml == "new" and not self._saved(fn, model):
            return 0
        guard = bool(model) and ml not in FORCE and ml not in BYPASS and kind != "qb" and not (ml == "update" and kind != "model")
        fillable = (self.declared(model, "fillable") or None) if guard else None
        guarded = self.declared(model, "guarded") if guard else None
        cols: dict[str, dict] = {}
        for p in parts:
            keys, srcs, conf = p["keys"], list(p["srcs"]), RESOLVED
            if p["kind"] == "literal":
                pass
            else:
                if p["kind"] == "all":
                    conf = HEURISTIC
                    if fillable:
                        keys = set(fillable) - set(p.get("excluded") or ())
                    elif p.get("alt"):
                        keys, srcs = set(p["alt"][0]) - set(p.get("excluded") or ()), [p["alt"][1]]
                    else:
                        continue
                if keys is None:
                    continue
                if fillable:
                    keys = keys & set(fillable)
                    srcs.append(f"{_short(model)}::$fillable")
                if guarded is not None:
                    gset = set(guarded) - {"*"}
                    keys = set() if "*" in guarded and not fillable else keys - gset
                    if guarded:
                        srcs.append(f"{_short(model)}::$guarded")
            via = f"{w.get('label') or f.get('m')}({p['via']})" if p.get("via") else (w.get("label") or f.get("m"))
            for c in keys:
                cur = cols.setdefault(c, {"via": via, "srcs": [], "conf": conf, "param": p.get("from_param")})
                for src in srcs:
                    if src not in cur["srcs"]:
                        cur["srcs"].append(src)
                if conf == RESOLVED:
                    cur["conf"] = RESOLVED
        n = 0
        for c, info in sorted(cols.items()):
            attrs = {"via": info["via"], "keys_from": info["srcs"], "mass_assignment": True}
            if info["param"]:
                attrs["param"] = info["param"]
            before = len(self.lv.b.edges)
            self.lv._col_edge(fn, w["table"], c, "WRITES_COLUMN", line, info["conf"], **attrs)
            n += len(self.lv.b.edges) - before
        return n

    def _saved(self, fn, model: str) -> bool:
        ctx = self.va.ctx(fn)
        for f in fn.facts:
            if f.get("t") == "call" and f.get("kind") == "method" and (f.get("m") or "").lower() in SAVES:
                try:
                    if model in ctx.type_of(f.get("recv")):
                        return True
                except Exception:
                    continue
        return False


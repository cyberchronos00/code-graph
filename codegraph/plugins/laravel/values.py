"""Value facts for PHP/Laravel: JSON settings, HTTP request keys, argument flow and fallback chains.

All facts are deterministic, derived from the PHP extractor's expression descriptors and the resolver's types:

* settings      `$model->getSetting('a.b', default)` / `setSetting('a.b', v)` on any class that declares the accessor
                -> `setting:a.b` nodes, READS_SETTING / WRITES_SETTING edges (attrs: owner, default literal).
* request keys  `FormRequest::rules()` array keys -> VALIDATES; actions type-hinting a FormRequest -> VALIDATED_BY rules();
                reads `$request->input|query|get|string|integer|boolean|validated('k')`, `request('k')`,
                `$validated['k']` (where `$validated = $request->validated()`), and `$filters['k']` where `$filters`
                receives a request array through calls (argument flow, any depth: fixpoint over the call graph)
                -> READS_INPUT edges to `request_key:k` nodes (attrs: via, flow evidence).
* resolutions   a variable assigned from a fallback expression (`??`, `?:`), a fallback `return`, or a function's
                ordered early returns ("resolver" form) -> `resolution:` node with the expanded chain (request key,
                setting + its default, model attribute / queried column, config/env, literal), parameters bound to
                their call-site arguments (helper levels, depth <= 4), and FALLS_BACK_TO edges with attrs.order.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from ...core.model import CONFIDENCE_RANK

EXACT, RESOLVED, HEURISTIC = "exact", "resolved", "heuristic"
REQUEST_BASES = {"illuminate\\http\\request", "illuminate\\foundation\\http\\formrequest"}
FORM_REQUEST = "illuminate\\foundation\\http\\formrequest"
REQ_SCALAR = {"input", "query", "get", "post", "string", "str", "integer", "boolean", "float", "date", "enum", "validated",
              "has", "filled", "missing", "exists", "json", "array", "collect", "route"}
REQ_ARRAY = {"validated", "validate", "all", "input", "query", "post", "only", "except", "safe", "toarray", "collect", "json"}
ARRAY_FUNCS = {"array_merge", "array_replace", "array_filter", "array_intersect_key", "array_diff_key", "collect", "array_map"}
TRANSPARENT = {"strtoupper", "strtolower", "trim", "ltrim", "rtrim", "mb_strtoupper", "mb_strtolower", "ucfirst", "lcfirst",
               "strval", "intval", "floatval", "boolval", "mb_strtolower", "e", "htmlspecialchars"}
SETTING_READ, SETTING_WRITE = {"getsetting"}, {"setsetting"}
SOURCE_KINDS = ("input", "setting", "column", "config", "env")
MAX_DEPTH = 4


def lit(d):
    """Literal value of a descriptor, or a sentinel."""
    if not d:
        return None
    k = d.get("k")
    if k in ("str", "int"):
        return d["v"]
    if k == "cfetch":
        n = (d.get("n") or "").lower()
        return {"true": True, "false": False, "null": None}.get(n, f"<{d.get('n')}>")
    if k == "arr":
        return [] if not d.get("items") else "<array>"
    if k == "const":
        return f"{(d.get('class') or '').split(chr(92))[-1]}::{d.get('n')}"
    return "<expr>"


def is_literal(d) -> bool:
    return bool(d) and (d.get("k") in ("str", "int", "const") or (d.get("k") == "cfetch" and (d.get("n") or "").lower() in ("true", "false", "null")))


def short(fid: str) -> str:
    x = fid.split(":", 1)[-1]
    return x[4:] if x.startswith("App\\") else x


def head_token(ident: str) -> str:
    """Last word of an identifier/key: customerTimezone -> timezone, reports.default -> default."""
    toks = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", (ident or "").replace("$", ""))
    return toks[-1].lower() if toks else ""


def sig_of(atoms: list[dict]) -> list[str]:
    """Flattened, deduplicated signature of a chain (nulls / empty strings dropped)."""
    out = []

    def add(s):  # a source repeated later in the chain adds no new fallback: keep the first occurrence
        if s and s not in out:
            out.append(s)

    def walk(a):
        k = a["kind"]
        if k == "literal":
            v = a.get("value")
            if v not in (None, "", [], False):
                add(repr(v) if isinstance(v, str) else json.dumps(v))
        elif k == "input":
            add(f"input:{a['key']}")
        elif k == "setting":
            add(f"setting:{a['key']}")
        elif k == "column":
            add(f"column:{a['column']}")
        elif k in ("config", "env"):
            add(f"{k}:{a['key']}")
        elif k == "call":
            if a.get("atoms"):
                for x in a["atoms"]:
                    walk(x)
            else:
                add(f"call:{short(a['target'])}")
        elif k == "param":
            variants = []
            for s in a.get("sources") or []:
                v = sig_of(s["atoms"])
                if v not in variants:
                    variants.append(v)
            if len(variants) == 1:
                for x in variants[0]:
                    add(x)
            elif variants:
                add(f"param:${a['name']}{{" + " | ".join(" > ".join(v) for v in variants) + "}")
            else:
                add(f"param:${a['name']}")
        elif k == "expr":
            add(f"expr:{a.get('text')}")
    for a in atoms:
        walk(a)
    return out


class ValueAnalysis:
    def __init__(self, lv, prog, builder):
        self.lv, self.prog, self.b = lv, prog, builder
        self.stats = defaultdict(int)
        self.targets = {}            # (fn.id, fact idx) -> [(PhpFunc, conf)]
        self.callers = defaultdict(list)  # callee id -> [(caller fn, fact, idx, conf)]
        self.req_params = defaultdict(dict)  # fn.id -> {param: evidence}
        self.setting_owners = {c.fqcn for c in prog.classes.values() if "getsetting" in c.methods or "setsetting" in c.methods}
        self.fn_by_id = {f.id: f for f in prog.all_funcs}
        self.req_returns = {}        # fn.id -> evidence: function returns (an array of) request data
        self._rc = {}

    # ------------------------------------------------------------------ helpers
    def ctx(self, fn):
        from ..php.plugin import ResolveCtx
        return ResolveCtx(self.prog, fn)

    def types(self, d, fn):
        return self.prog.type_of(d, fn, fn.env) if d else set()

    def is_request_type(self, types) -> bool:
        for t in types:
            if any(a.lower() in REQUEST_BASES for a in self.prog.ancestors(t)):
                return True
        return False

    def is_request_recv(self, d, fn) -> bool:
        if not d:
            return False
        if d.get("k") == "func" and (d.get("n") or "").lower() == "request" and not d.get("args"):
            return True
        return self.is_request_type(self.types(d, fn))

    def call_fact(self, d):
        k = d.get("k")
        if k == "mcall":
            return {"t": "call", "kind": "method", "recv": d.get("of"), "m": d.get("m"), "args": d.get("args") or []}
        if k == "scall":
            return {"t": "call", "kind": "static", "class": d.get("class"), "m": d.get("m"), "args": d.get("args") or []}
        if k == "func":
            return {"t": "call", "kind": "func", "n": d.get("n"), "args": d.get("args") or []}
        return None

    def resolve_call(self, d, fn):
        f = self.call_fact(d)
        if not f:
            return []
        try:
            t, _ = self.prog.call_targets(fn, f, self.ctx(fn))
        except Exception:
            return []
        return [(m, c) for m, c in t if c != HEURISTIC]

    def param_index(self, m, ai, a):
        if a.get("named"):
            for i, p in enumerate(m.params):
                if p["name"] == a["named"]:
                    return i
            return None
        return ai if ai < len(m.params) else None

    # ------------------------------------------------------------------ call index + request-array flow
    def index_calls(self):
        for fn in self.prog.all_funcs:
            ctx = self.ctx(fn)
            for i, f in enumerate(fn.facts):
                if f["t"] != "call":
                    continue
                try:
                    t, _ = self.prog.call_targets(fn, f, ctx)
                except Exception:
                    t = []
                t = [(m, c) for m, c in t if c != HEURISTIC]
                self.targets[(fn.id, i)] = t
                for m, c in t:
                    self.callers[m.id].append((fn, f, i, c))

    def req_array(self, d, fn, rvars, depth=0):
        """Evidence string if descriptor d evaluates to (part of) the request's input array, else None."""
        if not d or depth > 6:
            return None
        k = d.get("k")
        if k == "var":
            return rvars.get(d["n"])
        if k == "mcall":
            m = (d.get("m") or "").lower()
            if m in REQ_ARRAY and (not d.get("args") or m in ("only", "except", "validate")) and self.is_request_recv(d.get("of"), fn):
                return f"$request->{d.get('m')}()"
            if m in ("toarray", "all", "only", "except", "collect") and self.req_array(d.get("of"), fn, rvars, depth + 1):
                return self.req_array(d.get("of"), fn, rvars, depth + 1)
        if k in ("mcall", "scall", "func"):
            for t, _ in self.resolve_call_cached(d, fn):
                if t.id in self.req_returns:
                    return f"{short(t.id)}() returns request data"
            return None
        if k == "arr":
            for it in d.get("items") or []:
                if self.contains_input(it.get("v"), fn, rvars):
                    return "array built from request keys"
            return None
        if k == "alt":
            for o in d.get("opts") or []:
                e = self.req_array(o, fn, rvars, depth + 1)
                if e:
                    return e
            return None
        if k == "func" and (d.get("n") or "").lower() in ARRAY_FUNCS:
            for a in d.get("args") or []:
                e = self.req_array(a, fn, rvars, depth + 1)
                if e:
                    return e
        return None

    def resolve_call_cached(self, d, fn):
        key = (fn.id, id(d))
        r = self._rc.get(key)
        if r is None:
            r = self._rc[key] = (self.resolve_call(d, fn), d)
        return r[0]

    def contains_input(self, d, fn, rvars, depth=0) -> bool:
        if not isinstance(d, dict) or depth > 6:
            return False
        k = d.get("k")
        if k == "dim" and (d.get("key") or {}).get("k") == "str" and self.req_array(d.get("of"), fn, rvars, depth + 1):
            return True
        if k == "mcall" and (d.get("m") or "").lower() in REQ_SCALAR and (d.get("args") or [{}])[0].get("k") == "str" \
                and self.is_request_recv(d.get("of"), fn):
            return True
        for v in d.values():
            if isinstance(v, dict) and self.contains_input(v, fn, rvars, depth + 1):
                return True
            if isinstance(v, list) and any(self.contains_input(x, fn, rvars, depth + 1) for x in v if isinstance(x, dict)):
                return True
        return False

    def rvars(self, fn) -> dict:
        rv = {p: f"param ${p}" for p in self.req_params.get(fn.id, {})}
        for _ in range(3):
            changed = False
            for f in fn.facts:
                if f["t"] == "assign" and f["var"] not in rv:
                    e = self.req_array(f["expr"], fn, rv)
                    if e:
                        rv[f["var"]] = e
                        changed = True
            if not changed:
                break
        return rv

    def flow_request_arrays(self, rounds=8):
        for _ in range(rounds):
            changed = 0
            for fn in self.prog.all_funcs:
                rv = None
                if fn.id not in self.req_returns:
                    rv = self.rvars(fn)
                    for f in fn.facts:
                        if f["t"] == "return" and not f.get("ctx"):
                            ev = self.req_array(f["expr"], fn, rv)
                            if ev:
                                self.req_returns[fn.id] = {"via": ev, "file": fn.file, "line": f.get("line")}
                                changed += 1
                                break
                for i, f in enumerate(fn.facts):
                    t = self.targets.get((fn.id, i))
                    if not t or not f.get("args"):
                        continue
                    if rv is None:
                        rv = self.rvars(fn)
                    for ai, a in enumerate(f["args"]):
                        ev = self.req_array(a, fn, rv)
                        if not ev:
                            continue
                        for m, conf in t:
                            pi = self.param_index(m, ai, a)
                            if pi is None:
                                continue
                            pn = m.params[pi]["name"]
                            if pn not in self.req_params[m.id]:
                                self.req_params[m.id][pn] = {"from": fn.id, "file": fn.file, "line": f.get("line"), "via": ev,
                                                             "gated": bool(fn.dead.get(i))}
                                changed += 1
            if not changed:
                break
        self.stats["request_array_params"] = sum(len(v) for v in self.req_params.values())
        self.stats["request_array_returns"] = len(self.req_returns)

    def flow_chain(self, fn_id, pname, limit=6) -> list[str]:
        """Human-readable evidence for a request-array parameter: `$filters <- X::report L33 <- Ctl::index L27 ($request->validated())`."""
        out, seen = [], set()
        while fn_id and (fn_id, pname) not in seen and len(out) < limit:
            seen.add((fn_id, pname))
            ev = self.req_params.get(fn_id, {}).get(pname)
            if not ev:
                break
            out.append(f"{short(ev['from'])} @{ev['file']}:{ev['line']} passes {ev['via']}")
            if ev["via"].startswith("param $"):
                fn_id, pname = ev["from"], ev["via"][len("param $"):]
            else:
                break
        return out

    # ------------------------------------------------------------------ settings + request keys
    def emit_settings_and_inputs(self):
        b = self.b
        for fn in self.prog.all_funcs:
            if not self.b.has(fn.id):
                continue
            rv = self.rvars(fn)
            for i, f in enumerate(fn.facts):
                b.current_gate = fn.dead.get(i)
                if f["t"] == "call":
                    self._call_fact(fn, f, rv)
                for d in self._descs(f):
                    self._walk_dims(fn, d, rv, f.get("line"))
            b.current_gate = None
        # FormRequest rules + actions validated by them
        prog = self.prog
        for c in prog.classes.values():
            if c.kind != "class" or not any(a.lower() == FORM_REQUEST for a in prog.ancestors(c.fqcn)):
                continue
            rules = c.methods.get("rules")
            if not rules:
                continue
            for f in rules.facts:
                if f["t"] != "return" or (f.get("expr") or {}).get("k") != "arr" or f.get("ctx"):
                    continue
                for it in f["expr"].get("items") or []:
                    kd = it.get("key") or {}
                    if kd.get("k") != "str":
                        continue
                    rule = it.get("v") or {}
                    rtxt = rule.get("v") if rule.get("k") == "str" else (
                        [x["v"].get("v") for x in rule.get("items") or [] if (x.get("v") or {}).get("k") == "str"] if rule.get("k") == "arr" else None)
                    kid = self._key_node(kd["v"])
                    b.add_edge(rules.id, kid, "VALIDATES", rules.file, f.get("line"), EXACT, rule=rtxt)
                    self.stats["validated_keys"] += 1
        for fn in prog.all_funcs:
            if not fn.cls:
                continue
            for p in fn.params:
                for t in prog.norm(p.get("types"), fn.cls):
                    tc = prog.cls(t)
                    if tc and "rules" in tc.methods and any(a.lower() == FORM_REQUEST for a in prog.ancestors(tc.fqcn)):
                        b.add_edge(fn.id, tc.methods["rules"].id, "VALIDATED_BY", fn.file, p.get("line"), EXACT, param=p["name"])
                        self.stats["validated_by"] += 1

    def _descs(self, f):
        t = f["t"]
        if t == "assign":
            yield f["expr"]
        elif t == "return":
            yield f["expr"]
        elif t == "call":
            if f.get("recv"):
                yield f["recv"]
            for a in f.get("args") or []:
                yield a

    def _key_node(self, key: str) -> str:
        return self.b.add_node("request_key", key, name=key, fqn=key, lang="php", module="http")

    def _call_fact(self, fn, f, rv):
        b, m, args, line = self.b, (f.get("m") or "").lower(), f.get("args") or [], f.get("line")
        a0 = args[0] if args else None
        if f["kind"] == "method" and m in SETTING_READ | SETTING_WRITE and a0:
            owners = sorted({t.split("\\")[-1] for t in self.types(f.get("recv"), fn) if t in self.setting_owners})
            key = self.lv.render(fn, a0)
            if not key:
                return
            sid = self.b.add_node("setting", key, name=key, fqn=key, lang="php", module="settings")
            n = self.b.nodes[sid]
            n.attrs.setdefault("owners", [])
            for o in owners:
                if o not in n.attrs["owners"]:
                    n.attrs["owners"].append(o)
            if m in SETTING_READ:
                dflt = lit(args[1]) if len(args) > 1 else None
                b.add_edge(fn.id, sid, "READS_SETTING", fn.file, line, EXACT if a0.get("k") == "str" else RESOLVED,
                           owner=owners or None, default=dflt)
                self.stats["setting_reads"] += 1
            else:
                b.add_edge(fn.id, sid, "WRITES_SETTING", fn.file, line, EXACT if a0.get("k") == "str" else RESOLVED, owner=owners or None)
                self.stats["setting_writes"] += 1
            return
        if f["kind"] == "method" and m in REQ_SCALAR and a0 and a0.get("k") == "str" and self.is_request_recv(f.get("recv"), fn):
            b.add_edge(fn.id, self._key_node(a0["v"]), "READS_INPUT", fn.file, line, EXACT, via=f"$request->{f.get('m')}()",
                       default=lit(args[1]) if len(args) > 1 else None)
            self.stats["input_reads"] += 1
        elif f["kind"] == "func" and (f.get("n") or "").lower() == "request" and a0 and a0.get("k") == "str":
            b.add_edge(fn.id, self._key_node(a0["v"]), "READS_INPUT", fn.file, line, EXACT, via="request()",
                       default=lit(args[1]) if len(args) > 1 else None)
            self.stats["input_reads"] += 1

    def _walk_dims(self, fn, d, rv, line, depth=0):
        if not isinstance(d, dict) or depth > 10:
            return
        if d.get("k") == "dim" and (d.get("key") or {}).get("k") == "str":
            ev = self.req_array(d.get("of"), fn, rv)
            if ev:
                of = d.get("of") or {}
                flow = self.flow_chain(fn.id, of.get("n")) if of.get("k") == "var" and of.get("n") in self.req_params.get(fn.id, {}) else []
                self.b.add_edge(fn.id, self._key_node(d["key"]["v"]), "READS_INPUT", fn.file, line,
                                EXACT if not flow else RESOLVED, via=f"${of.get('n')}[...]" if of.get("k") == "var" else ev,
                                flow=flow or None)
                self.stats["input_reads"] += 1
        for v in d.values():
            if isinstance(v, dict):
                self._walk_dims(fn, v, rv, line, depth + 1)
            elif isinstance(v, list):
                for x in v:
                    self._walk_dims(fn, x, rv, line, depth + 1)

    # ------------------------------------------------------------------ fallback chains
    def strip(self, d, norm=()):
        while d and d.get("k") == "func" and (d.get("n") or "").lower() in TRANSPARENT and d.get("args"):
            norm = norm + ((d.get("n") or "").lower(),)
            d = d["args"][0]
        return d, norm

    def has_fallback(self, d) -> bool:
        d, _ = self.strip(d)
        return bool(d) and d.get("k") == "alt" and d.get("op") in ("coalesce", "elvis")

    def assign_for(self, fn, var, line):
        cands = [f for f in fn.facts if f["t"] == "assign" and f["var"] == var and not f.get("ctx")]
        before = [f for f in cands if (f.get("line") or 0) <= (line or 10 ** 9)]
        return (before or cands)[-1] if (before or cands) else None

    def expand(self, d, fn, line, depth=0, bind=None, seen=None, norm=()):
        seen = seen if seen is not None else set()
        d, norm = self.strip(d, norm)
        at = f"{fn.file}:{line}"
        if not d:
            return []
        k = d.get("k")
        if k == "alt":
            out = []
            for o in d.get("opts") or []:
                out += self.expand(o, fn, line, depth, bind, seen, norm)
            return out
        if is_literal(d):
            return [{"kind": "literal", "value": lit(d), "at": at, "norm": list(norm) or None}]
        if k == "var":
            name = d["n"]
            pnames = [p["name"] for p in fn.params]
            asg = self.assign_for(fn, name, line)
            if asg and (fn.id, name) not in seen:
                return self.expand(asg["expr"], fn, asg.get("line"), depth, bind, seen | {(fn.id, name)}, norm)
            if name in pnames:
                return [self.param_atom(fn, name, pnames.index(name), line, depth, bind, seen, norm)]
            return [{"kind": "expr", "text": f"${name}", "at": at}]
        if k == "dim":
            key = (d.get("key") or {}).get("v") if (d.get("key") or {}).get("k") == "str" else None
            rv = self.rvars(fn)
            ev = self.req_array(d.get("of"), fn, rv) if key else None
            if ev:
                of = d.get("of") or {}
                flow = self.flow_chain(fn.id, of.get("n")) if of.get("k") == "var" else []
                if bind and of.get("k") == "var" and of.get("n") in bind:
                    flow = [f"bound at {bind[of['n']][2]}"] + flow
                return [{"kind": "input", "key": key, "via": f"${of.get('n')}[...]" if of.get("k") == "var" else ev,
                         "flow": flow or None, "at": at, "norm": list(norm) or None}]
            return [{"kind": "expr", "text": f"{self.text(d.get('of'))}[{key!r}]" if key else self.text(d), "at": at}]
        if k == "mcall":
            m = (d.get("m") or "").lower()
            args = d.get("args") or []
            if m in SETTING_READ and args:
                key = self.lv.render(fn, args[0])
                owners = sorted({t.split("\\")[-1] for t in self.types(d.get("of"), fn) if t in self.setting_owners})
                out = [{"kind": "setting", "key": key, "owner": owners or None, "default": lit(args[1]) if len(args) > 1 else None,
                        "at": at, "norm": list(norm) or None}]
                if len(args) > 1 and is_literal(args[1]):
                    out.append({"kind": "literal", "value": lit(args[1]), "at": at, "from": "getSetting default"})
                return out
            if m in REQ_SCALAR and args and args[0].get("k") == "str" and self.is_request_recv(d.get("of"), fn):
                out = [{"kind": "input", "key": args[0]["v"], "via": f"$request->{d.get('m')}()", "at": at}]
                if len(args) > 1 and is_literal(args[1]):
                    out.append({"kind": "literal", "value": lit(args[1]), "at": at, "from": "input default"})
                return out
            if m in ("value", "pluck") and args and args[0].get("k") == "str":
                tables = self.builder_tables(d.get("of"), fn, depth, bind)
                col = args[0]["v"]
                if len(tables) == 1:
                    return [{"kind": "column", "column": f"{tables[0][0]}.{col}", "how": f"->{d.get('m')}() ({tables[0][1]})", "at": at}]
                return [{"kind": "expr", "text": f"->{d.get('m')}('{col}')", "at": at}]
            return self.call_atom(d, fn, line, depth, bind, seen, norm)
        if k in ("scall", "func"):
            n = (d.get("n") or d.get("m") or "").lower()
            args = d.get("args") or []
            if k == "func" and n in ("config", "env") and args and args[0].get("k") == "str":
                out = [{"kind": n, "key": args[0]["v"], "default": lit(args[1]) if len(args) > 1 else None, "at": at}]
                if len(args) > 1 and is_literal(args[1]):
                    out.append({"kind": "literal", "value": lit(args[1]), "at": at, "from": f"{n}() default"})
                return out
            if k == "func" and n == "request" and args and args[0].get("k") == "str":
                return [{"kind": "input", "key": args[0]["v"], "via": "request()", "at": at}]
            return self.call_atom(d, fn, line, depth, bind, seen, norm)
        if k == "prop":
            prop = d.get("n")
            for t in self.types(d.get("of"), fn):
                if t in self.lv.models and prop and prop.lower() not in self.lv.models[t]["relations"]:
                    tbl = self.lv.table_of(t)
                    return [{"kind": "column", "column": f"{tbl}.{prop}", "how": f"{t.split(chr(92))[-1]}->{prop}", "at": at,
                             "norm": list(norm) or None}]
            return [{"kind": "expr", "text": self.text(d), "at": at}]
        return [{"kind": "expr", "text": self.text(d), "at": at}]

    def text(self, d, depth=0) -> str:
        if not d or depth > 4:
            return "…"
        k = d.get("k")
        if k == "var":
            return f"${d['n']}"
        if k == "this":
            return "$this"
        if k == "prop":
            return f"{self.text(d.get('of'), depth + 1)}->{d.get('n')}"
        if k == "mcall":
            return f"{self.text(d.get('of'), depth + 1)}->{d.get('m')}()"
        if k == "scall":
            return f"{(d.get('class') or '?').split(chr(92))[-1]}::{d.get('m')}()"
        if k == "func":
            return f"{d.get('n')}()"
        if k == "dim":
            return f"{self.text(d.get('of'), depth + 1)}[…]"
        if k in ("str", "int"):
            return repr(d["v"])
        return f"<{k}>"

    def builder_tables(self, d, fn, depth, bind) -> list[tuple[str, str]]:
        """Tables a query-builder expression targets; through bound / caller arguments for untyped Builder params."""
        out = []
        for t in self.types(d, fn):
            if t.startswith("builder:"):
                tb = self.lv.table_of(t.split(":", 1)[1])
                if tb:
                    out.append((tb, "builder type"))
            elif t.startswith("qb:"):
                out.append((t.split(":", 1)[1], "DB::table"))
        if out:
            return sorted(set(out))
        root = d
        while root and root.get("k") == "mcall":
            root = root.get("of")
        if root and root.get("k") == "var" and depth < MAX_DEPTH:
            name = root["n"]
            asg = self.assign_for(fn, name, None)
            if asg:
                return self.builder_tables(asg["expr"], fn, depth + 1, bind)
            pnames = [p["name"] for p in fn.params]
            if name in pnames:
                idx = pnames.index(name)
                srcs = [bind[name][:2]] if bind and name in bind else [(a, c) for c, a in self._caller_args(fn, idx)]
                for a, cf in srcs:
                    for tb, how in self.builder_tables(a, cf, depth + 1, None):
                        out.append((tb, f"argument of {short(cf.id)}"))
        return sorted(set(out))[:3]

    def _caller_args(self, fn, idx):
        out = []
        for cf, f, i, conf in self.callers.get(fn.id, []):
            args = f.get("args") or []
            for ai, a in enumerate(args):
                fnp = self.fn_by_id.get(fn.id)
                pi = self.param_index(fnp, ai, a) if fnp else None
                if pi == idx:
                    out.append((cf, a))
        return out

    def param_atom(self, fn, name, idx, line, depth, bind, seen, norm):
        atom = {"kind": "param", "name": name, "fn": fn.id, "at": f"{fn.file}:{line}", "norm": list(norm) or None, "sources": []}
        if bind and name in bind:
            a, cf, cat = bind[name]
            atom["sources"].append({"caller": cf.id, "at": cat, "atoms": self.expand(a, cf, int(cat.rsplit(":", 1)[1]), depth + 1, None, seen)})
            return atom
        if depth >= MAX_DEPTH:
            return atom
        for cf, f, i, conf in self.callers.get(fn.id, [])[:40]:
            args = f.get("args") or []
            for ai, a in enumerate(args):
                if self.param_index(fn, ai, a) == idx:
                    atom["sources"].append({"caller": cf.id, "at": f"{cf.file}:{f.get('line')}", "gated": bool(cf.dead.get(i)) or None,
                                            "atoms": self.expand(a, cf, f.get("line"), depth + 1, None, seen)})
        return atom

    def call_atom(self, d, fn, line, depth, bind, seen, norm):
        """Helper level: a call to an app method whose own value is a fallback chain is inlined (args bound)."""
        targets = self.resolve_call(d, fn)
        at = f"{fn.file}:{line}"
        if len(targets) == 1 and depth < MAX_DEPTH:
            m = targets[0][0]
            chain = self.fn_chain(m)
            if chain and (m.id, "fn") not in seen:
                b2 = {}
                for ai, a in enumerate(d.get("args") or []):
                    pi = self.param_index(m, ai, a)
                    if pi is not None:
                        b2[m.params[pi]["name"]] = (a, fn, at)
                atoms = []
                for e, ln in chain:
                    atoms += self.expand(e, m, ln, depth + 1, b2, seen | {(m.id, "fn")})
                return [{"kind": "call", "target": m.id, "at": at, "atoms": atoms, "norm": list(norm) or None}]
            return [{"kind": "call", "target": m.id, "at": at}]
        return [{"kind": "expr", "text": self.text(d), "at": at}]

    def fn_chain(self, m):
        """[(expr, line)] of a function's value: its single fallback return, or its ordered early returns."""
        rets = [f for f in m.facts if f["t"] == "return" and not f.get("ctx")]
        if not rets or any((f.get("expr") or {}).get("k") in ("arr", "new", "closure") for f in rets):
            return None
        if len(rets) == 1:
            return [(rets[0]["expr"], rets[0].get("line"))] if self.has_fallback(rets[0]["expr"]) else None
        return [(f["expr"], f.get("line")) for f in rets]

    def emit_resolutions(self):
        b = self.b
        for fn in self.prog.all_funcs:
            if not b.has(fn.id) or fn.file.startswith(("config/", "database/", "routes/", "lang/")):
                continue
            sites = []
            rets = [f for f in fn.facts if f["t"] == "return" and not f.get("ctx")]
            chain = self.fn_chain(fn) if len(rets) >= 2 else None
            if chain:
                sites.append(("return", "returns", fn.line, chain))
            for f in fn.facts:
                if f.get("ctx"):
                    continue
                if f["t"] == "assign" and self.has_fallback(f["expr"]):
                    sites.append((f"${f['var']}", "expr", f.get("line"), [(f["expr"], f.get("line"))]))
                elif f["t"] == "return" and not chain and self.has_fallback(f["expr"]):
                    sites.append(("return", "expr", f.get("line"), [(f["expr"], f.get("line"))]))
            for target, form, line, ch in sites:
                atoms = []
                for e, ln in ch:
                    atoms += self.expand(e, fn, ln)
                sig = sig_of(atoms)
                kinds = {s.split(":", 1)[0] for s in sig}
                if not (kinds & set(SOURCE_KINDS)) or len(sig) < 2:
                    continue
                tname = fn.name if target == "return" else target
                key = f"{fn.id.split(':', 1)[1]}#{target}@{line}"
                lits = [a for a in self._flat(atoms) if a["kind"] == "literal" and a.get("value") not in (None, "", [], False)]
                rid = b.add_node("resolution", key, name=f"{short(fn.id)} {target}", fqn=f"{fn.id.split(':', 1)[1]}#{target}",
                                 file=fn.file, line=line, module=self.prog.b.nodes[fn.id].module if fn.id in self.prog.b.nodes else None,
                                 lang="php", attrs={"fn": fn.id, "target": target, "target_name": tname, "form": form, "chain": atoms,
                                                    "signature": sig, "final_literal": lits[-1]["value"] if lits else None})
                b.add_edge(fn.id, rid, "HAS_RESOLUTION", fn.file, line, EXACT, target=target)
                order = 0
                for a in self._flat(atoms):
                    dst = self._source_node(a)
                    if dst:
                        order += 1
                        b.add_edge(rid, dst, "FALLS_BACK_TO", a.get("at", "").rsplit(":", 1)[0] or fn.file,
                                   int(a["at"].rsplit(":", 1)[1]) if a.get("at") and a["at"].rsplit(":", 1)[1].isdigit() else line,
                                   EXACT if a["kind"] in ("setting", "input", "config", "env") else RESOLVED,
                                   order=order, default=a.get("default"))
                self.stats["resolutions"] += 1

    def _flat(self, atoms):
        for a in atoms:
            if a["kind"] == "call" and a.get("atoms"):
                yield from self._flat(a["atoms"])
            elif a["kind"] == "param" and a.get("sources"):
                seen = []
                for s in a["sources"]:
                    for x in self._flat(s["atoms"]):
                        k = json.dumps({kk: vv for kk, vv in x.items() if kk in ("kind", "key", "column", "value")}, sort_keys=True)
                        if k not in seen:
                            seen.append(k)
                            yield x
            else:
                yield a

    def _source_node(self, a):
        k = a["kind"]
        if k == "setting" and a.get("key"):
            return self.b.add_node("setting", a["key"], name=a["key"], fqn=a["key"], lang="php", module="settings")
        if k == "input" and a.get("key"):
            return self._key_node(a["key"])
        if k == "column":
            tbl, col = a["column"].split(".", 1)
            if tbl and tbl != "None":
                return self.b.add_node("column", a["column"], name=col, fqn=a["column"], lang="sql")
        if k == "config":
            return self.b.add_node("config", a["key"], lang="php")
        if k == "env":
            return self.b.add_node("env", a["key"], lang="env")
        return None

    def run(self) -> dict:
        self.index_calls()
        self.flow_request_arrays()
        self.emit_settings_and_inputs()
        self.emit_resolutions()
        return dict(self.stats)

"""Inline access checks a Laravel action runs before its own work.

Route middleware stays on the route. These checks are stored on the route as
`inline_guards` and printed on their own line. A check inside a branch is
`conditional`. `--unguarded` hides a route with an unconditional inline check;
`--unguarded --strict` keeps the route-level result.

Detection walks the PHP control skeleton (`plugins/php/gating.py`) and reuses
`query._auth_node` / `_auth_name` plus `.cg.yaml` `auth.extra_patterns`.
"""
from __future__ import annotations

import re
from functools import lru_cache

from ... import query as Q
from ...config import ConfigError, load
from ...presets import available, guard_names
from ..laravel.plugin import PIVOT_WRITES, WRITE_TABLE
from .gating import abort_fn
from .plugin import PhpFunc, PhpProgram

_WRITE = set(WRITE_TABLE) | set(PIVOT_WRITES)
_PREDICATE = {
    "can", "cannot", "haspermission", "hasanypermission", "hasallpermissions", "hasdirectpermission",
    "hasrole", "hasanyrole", "hasallroles", "hasexactroles", "denies", "allows",
}
_PERM = {"can", "cannot", "check", "haspermission", "hasanypermission", "hasallpermissions", "hasdirectpermission"}
_ROLE = {"hasrole", "hasanyrole", "hasallroles", "hasexactroles"}
_GATE = {"authorize", "denies", "allows", "check", "any", "none"}
_REQ = {"header", "bearertoken", "input", "query", "post", "cookie", "json"}
_AUTH_EXC = ("AuthorizationException", "AccessDeniedHttpException")
_FAIL = (401, 403)


@lru_cache(maxsize=1)
def _guard_sets():
    applied = available()
    return guard_names(applied, "auth"), guard_names(applied, "secret")


def for_action(prog: PhpProgram, class_name: str | None, method: str | None) -> list[dict]:
    """Inline guards for one controller action. Empty when the class is unknown."""
    if not class_name:
        return []
    c = prog.cls(class_name)
    if not c:
        return []
    finder = getattr(prog, "_inline_finder", None)
    if finder is None:
        finder = Finder(prog, _patterns(str(prog.project.root)))
        prog._inline_finder = finder
    return finder.for_action(c.fqcn, method)


@lru_cache(maxsize=8)
def _patterns(root: str):
    try:
        cfg = load(root)
    except (ConfigError, OSError):
        return ()
    out = []
    for p in ((cfg.get("auth") or {}).get("extra_patterns") or []):
        try:
            out.append(re.compile(p, re.I))
        except re.error:
            continue
    return tuple(out)


def _int_of(a) -> int | None:
    if isinstance(a, dict) and a.get("k") in ("int", "lit") and isinstance(a.get("v"), int) and not isinstance(a.get("v"), bool):
        return a["v"]
    return None


def _str_of(a) -> str | None:
    if isinstance(a, dict) and a.get("k") in ("str", "lit") and isinstance(a.get("v"), str):
        return a["v"]
    return None


def _ability(args) -> str | None:
    for a in args or []:
        s = _str_of(a)
        if s:
            return s
    return None


def _unwrap(d):
    while isinstance(d, dict) and d.get("k") in ("cast", "not"):
        d = d.get("x")
    return d


def _children(d):
    if not isinstance(d, dict):
        return
    for key in ("x", "of", "l", "rr", "recv", "c", "t", "e"):
        v = d.get(key)
        if isinstance(v, dict):
            yield v
    for a in d.get("args") or []:
        if isinstance(a, dict):
            yield a
    for it in d.get("items") or []:
        if isinstance(it, dict):
            v = it.get("v")
            if isinstance(v, dict):
                yield v
    for ch in d.get("ch") or []:
        if isinstance(ch, dict):
            yield ch
    for o in d.get("opts") or []:
        if isinstance(o, dict):
            yield o


def _config_ref(d) -> str | None:
    d = _unwrap(d)
    if not isinstance(d, dict):
        return None
    k = d.get("k")
    n = (d.get("n") or d.get("fn") or d.get("m") or "")
    nl = n.lower()
    if k in ("func", "call") and nl == "config" and not d.get("m"):
        s = _ability(d.get("args"))
        return f"config:{s}" if s else "config"
    if k in ("func", "call") and nl == "env" and not d.get("m"):
        s = _ability(d.get("args"))
        return f"env:{s}" if s else "env"
    if k == "scall" and nl in ("get", "string") and str(d.get("class") or "").endswith("\\Config"):
        s = _ability(d.get("args"))
        return f"config:{s}" if s else "config"
    return None


def _request_ref(d) -> bool:
    d = _unwrap(d)
    if not isinstance(d, dict):
        return False
    m = (d.get("m") or "")
    if d.get("k") in ("mcall", "call") and m.lower() in _REQ:
        return True
    return False


def _pair(a, b, how: str) -> dict | None:
    ca, cb = _config_ref(a), _config_ref(b)
    if ca and _request_ref(b):
        return {"how": how, "key": ca}
    if cb and _request_ref(a):
        return {"how": how, "key": cb}
    return None


def secret_compare(d) -> dict | None:
    """A `hash_equals` or `===` between `config`/`env` and a request header or input."""
    if not isinstance(d, dict):
        return None
    k = d.get("k")
    if k == "func" and (d.get("n") or "").lower() == "hash_equals":
        args = d.get("args") or []
        return _pair(args[0] if args else None, args[1] if len(args) > 1 else None, "hash_equals")
    if k == "call" and d.get("fn") == "hash_equals":
        args = d.get("args") or []
        return _pair(args[0] if args else None, args[1] if len(args) > 1 else None, "hash_equals")
    if k == "cmp" and d.get("op") in ("===", "!=="):
        return _pair(d.get("l"), d.get("rr"), "===")
    for ch in _children(d):
        hit = secret_compare(ch)
        if hit:
            return hit
    return None


def _is_write(fact: dict) -> bool:
    t = fact.get("t")
    if t == "pwrite":
        return (fact.get("recv") or {}).get("k") != "this"
    if t == "fetch" and fact.get("write"):
        return (fact.get("recv") or {}).get("k") != "this"
    if t != "call":
        return False
    m = (fact.get("m") or fact.get("n") or "").lower()
    if m not in _WRITE:
        return False
    if fact.get("kind") == "method" and (fact.get("recv") or {}).get("k") == "this":
        return False
    if fact.get("kind") == "static" and (fact.get("class") or "") in ("self", "static", "parent"):
        return False
    return True


def _short(fn: PhpFunc) -> str:
    cls = fn.cls.split("\\")[-1] if fn.cls else ""
    return f"{cls}::{fn.name}" if cls else fn.name


def _quote(m: str, ability: str | None) -> str:
    return f"{m}('{ability}')" if ability else m


class Finder:
    def __init__(self, prog: PhpProgram, patterns):
        self.prog = prog
        self.patterns = patterns
        self.auth_names, self.secret_names = _guard_sets()

    def for_action(self, fqcn: str, method: str | None) -> list[dict]:
        guards = self._middleware(fqcn, method)
        fn = self.prog.find_method(fqcn, method)
        if fn:
            body, _wrote = self._walk(fn, 0, set(), False)
            guards.extend(body)
            guards.extend(self._form_requests(fn))
        return _dedupe(guards)

    # ----- naming -----
    def _project_name(self, name: str) -> bool:
        if not name or name.lower() in _PREDICATE or name.lower() == "authorize":
            return False
        if Q._auth_name(name):
            return True
        return any(rx.search(name) for rx in self.patterns)

    def _project_check(self, fn: PhpFunc) -> bool:
        if self._project_name(fn.name):
            return True
        if fn.name.lower() in _PREDICATE or fn.name.lower() == "authorize":
            return False
        return bool(Q._auth_node(fn.id))

    def _kind_name(self, name: str) -> str:
        w = set(Q._words(name))
        if w & {"role", "roles"}:
            return "role"
        if w & {"policy", "policies"}:
            return "policy"
        return "permission"

    def _mw_kind(self, name: str) -> str | None:
        head = name.split(":", 1)[0]
        if self.secret_names.lookup(name) or self.secret_names.lookup(head):
            return "secret"
        known = self.auth_names.lookup(name) or self.auth_names.lookup(head) or Q._auth_name(head) \
            or any(rx.search(name) for rx in self.patterns)
        if not known:
            return None
        hl = head.lower()
        if hl in ("role", "role_or_permission"):
            return "role"
        if hl in ("auth", "auth.basic", "auth.session", "verified"):
            return "role"
        return "permission"

    # ----- calls -----
    def _callee(self, fn: PhpFunc, fact: dict) -> PhpFunc | None:
        m = fact.get("m")
        if fact.get("kind") == "method" and (fact.get("recv") or {}).get("k") == "this":
            return self.prog.find_method(fn.cls, m)
        if fact.get("kind") == "static" and (fact.get("class") or "") in ("self", "static", "parent", fn.cls):
            return self.prog.find_method(fn.cls, m)
        return None

    def _gate(self, fact: dict) -> bool:
        cls = fact.get("class") or ""
        return cls == "Gate" or cls.endswith("\\Gate")

    def _perm_guard(self, m: str, args, at: str, conditional: bool) -> dict:
        ml = m.lower()
        kind = "role" if ml in _ROLE or "role" in ml else "permission"
        return {"name": _quote(m, _ability(args)), "kind": kind, "at": at, "conditional": conditional}

    def _policy_guard(self, name: str, at: str, conditional: bool) -> dict:
        return {"name": name, "kind": "policy", "at": at, "conditional": conditional}

    def _secret_guard(self, hit: dict, at: str, conditional: bool, prefix: str | None = None) -> dict:
        label = f"{hit['how']} {hit['key']}"
        if prefix:
            label = f"{prefix} ({label})"
        return {"name": label, "kind": "secret", "at": at, "conditional": conditional}

    def _abort_status_fact(self, fact: dict) -> int | None:
        if fact.get("kind") != "func":
            return None
        n = (fact.get("n") or "").lower()
        args = fact.get("args") or []
        if n == "abort":
            return _int_of(args[0]) if args else None
        if n in ("abort_if", "abort_unless") and len(args) > 1:
            return _int_of(args[1])
        return None

    def _predicate(self, pred, at: str, conditional: bool) -> dict | None:
        hit = secret_compare(pred)
        if hit:
            return self._secret_guard(hit, at, conditional)
        return self._auth_desc(pred, at, conditional)

    def _auth_desc(self, d, at: str, conditional: bool) -> dict | None:
        if not isinstance(d, dict):
            return None
        k = d.get("k")
        m = d.get("m") or ""
        ml = m.lower()
        if k == "mcall" and ml in _PREDICATE:
            return self._perm_guard(m, d.get("args"), at, conditional)
        if k == "scall" and self._gate({"class": d.get("class")}) and ml in _GATE:
            ab = _ability(d.get("args"))
            return self._policy_guard(_quote(f"Gate::{m}", ab), at, conditional)
        if k == "mcall" and ml == "authorize" and (d.get("of") or {}).get("k") == "this":
            return self._policy_guard(_quote("authorize", _ability(d.get("args"))), at, conditional)
        if k == "mcall" and (d.get("of") or {}).get("k") == "this" and self._project_name(m):
            return {"name": m, "kind": self._kind_name(m), "at": at, "conditional": conditional}
        for ch in _children(d):
            hit = self._auth_desc(ch, at, conditional)
            if hit:
                return hit
        return None

    def _from_fact(self, fn: PhpFunc, fact: dict, conditional: bool) -> dict | None:
        """A guard this fact is, by itself (abort predicate, authorize, or a project check)."""
        if fact.get("t") != "call":
            return None
        at = _short(fn)
        kind = fact.get("kind")
        if kind == "func" and (fact.get("n") or "").lower() in ("abort_if", "abort_unless"):
            if self._abort_status_fact(fact) not in _FAIL:
                return None
            args = fact.get("args") or []
            return self._predicate(args[0] if args else None, at, conditional)
        if kind == "method" and (fact.get("m") or "").lower() == "authorize" and (fact.get("recv") or {}).get("k") == "this":
            return self._policy_guard(_quote("authorize", _ability(fact.get("args"))), at, conditional)
        if kind == "static" and self._gate(fact) and (fact.get("m") or "").lower() == "authorize":
            return self._policy_guard(_quote(f"Gate::{fact.get('m')}", _ability(fact.get("args"))), at, conditional)
        cal = self._callee(fn, fact)
        if cal and cal.id != fn.id:
            sec = self._secret_info(cal)
            if sec:
                return self._secret_guard(sec, _short(cal), conditional or sec["conditional"], prefix=cal.name)
            fail = self._failure_info(cal)
            # A matching name whose return value is discarded does not gate the action. A method that
            # rejects only inside a branch stays conditional, so --unguarded still lists the route.
            if fail:
                kind = self._kind_name(cal.name) if self._project_check(cal) else "permission"
                return {"name": cal.name, "kind": kind, "at": _short(cal),
                        "conditional": conditional or fail["conditional"]}
        return None

    def _condition_fact(self, fn: PhpFunc, fact: dict) -> dict | None:
        """An access call used as a branch condition (the branch aborts)."""
        if fact.get("t") != "call":
            return None
        at = _short(fn)
        m = fact.get("m") or ""
        ml = m.lower()
        if fact.get("kind") == "method" and ml in _PREDICATE:
            return self._perm_guard(m, fact.get("args"), at, True)
        if fact.get("kind") == "method" and (fact.get("recv") or {}).get("k") == "this" and self._project_name(m):
            return {"name": m, "kind": self._kind_name(m), "at": at, "conditional": True}
        if fact.get("kind") == "static" and self._gate(fact) and ml in _GATE and ml != "authorize":
            return self._policy_guard(_quote(f"Gate::{m}", _ability(fact.get("args"))), at, True)
        if fact.get("kind") == "func" and (fact.get("n") or "").lower() == "hash_equals":
            hit = secret_compare({"k": "func", "n": "hash_equals", "args": fact.get("args")})
            if hit:
                return self._secret_guard(hit, at, True)
        return self._from_fact(fn, fact, True)

    # ----- secret methods -----
    def _abort_status_expr(self, expr) -> int | None:
        if not isinstance(expr, dict):
            return None
        name = abort_fn(expr)
        if name:
            args = expr.get("args") or []
            idx = 0 if name == "abort" else 1
            return _int_of(args[idx]) if len(args) > idx else None
        for ch in _children(expr):
            st = self._abort_status_expr(ch)
            if st in _FAIL:
                return st
        return None

    def _throws_auth(self, fn: PhpFunc, node: dict) -> bool:
        lo, hi = node.get("r") or (0, 0)
        for f in fn.facts[lo:hi]:
            if f.get("t") == "new" and any(str(f.get("class") or "").endswith(x) for x in _AUTH_EXC):
                return True
        return False

    def _fails(self, fn: PhpFunc, stmts) -> bool:
        for s in stmts or []:
            k = s.get("k")
            if k == "x" and self._abort_status_expr(s.get("x")) in _FAIL:
                return True
            if k == "term" and self._throws_auth(fn, s):
                return True
            if k == "if" and (self._fails(fn, s.get("t")) or self._fails(fn, s.get("e"))
                              or any(self._fails(fn, ei.get("b")) for ei in s.get("ei") or [])):
                return True
            if k == "seq" and self._fails(fn, s.get("b")):
                return True
            if k == "blk" and any(self._fails(fn, b) for b in s.get("b") or []):
                return True
            if k == "try" and self._fails(fn, s.get("b")):
                return True
        return False

    def _secret_sites(self, fn: PhpFunc) -> list[dict]:
        found = []

        def walk(stmts, cond: bool):
            for s in stmts or []:
                k = s.get("k")
                if k == "x":
                    hit = secret_compare(s.get("x"))
                    if hit:
                        found.append({**hit, "conditional": cond})
                elif k == "if":
                    hit = secret_compare(s.get("c"))
                    if hit:
                        found.append({**hit, "conditional": True})
                    walk(s.get("t"), True)
                    for ei in s.get("ei") or []:
                        hit = secret_compare(ei.get("c"))
                        if hit:
                            found.append({**hit, "conditional": True})
                        walk(ei.get("b"), True)
                    if s.get("e") is not None:
                        walk(s.get("e"), True)
                elif k == "seq":
                    walk(s.get("b"), cond)
                elif k == "blk":
                    for b in s.get("b") or []:
                        walk(b, True)
                elif k == "try":
                    walk(s.get("b"), cond)

        walk(fn.skel, False)
        return found

    def _prefix_fail(self, fn: PhpFunc, stmts) -> str:
        """`fail` when a straight-line statement aborts 401/403 or throws before any write."""
        for s in stmts or []:
            k = s.get("k")
            if k == "x":
                if self._abort_status_expr(s.get("x")) in _FAIL:
                    return "fail"
                lo, hi = s.get("r") or (0, 0)
                if any(_is_write(f) for f in fn.facts[lo:hi]):
                    return "write"
            elif k == "term" and self._throws_auth(fn, s):
                return "fail"
            elif k in ("if", "blk"):
                return "branch"
            elif k == "ret":
                return "stop"
            elif k == "seq":
                hit = self._prefix_fail(fn, s.get("b"))
                if hit != "stop":
                    return hit
            elif k == "try":
                return self._prefix_fail(fn, s.get("b"))
        return "stop"

    def _failure_info(self, fn: PhpFunc) -> dict | None:
        """A same-class method whose body rejects with 401/403 or an authorization exception."""
        if not fn.skel:
            return None
        hit = self._prefix_fail(fn, fn.skel)
        if hit == "fail":
            return {"conditional": False}
        if hit != "write" and self._fails(fn, fn.skel):
            return {"conditional": True}
        return None

    def _secret_info(self, fn: PhpFunc) -> dict | None:
        if not fn.skel or not self._fails(fn, fn.skel):
            return None
        sites = self._secret_sites(fn)
        if not sites:
            return None
        sites.sort(key=lambda s: (s["conditional"], 0 if s["how"] == "hash_equals" else 1))
        return sites[0]

    # ----- walk -----
    def _range(self, fn: PhpFunc, node: dict, depth: int, seen: set, conditional: bool):
        lo, hi = node.get("r") or (0, 0)
        guards = []
        for fact in fn.facts[lo:hi]:
            if _is_write(fact):
                return guards, True
            g = self._from_fact(fn, fact, conditional)
            if g:
                guards.append(g)
                return guards, False
            cal = self._callee(fn, fact) if fact.get("t") == "call" else None
            if cal and cal.id not in seen and depth < 2:
                sub, wrote = self._walk(cal, depth + 1, seen, conditional)
                guards.extend(sub)
                if wrote:
                    return guards, True
        return guards, False

    def _condition(self, fn: PhpFunc, expr) -> list[dict]:
        if not isinstance(expr, dict):
            return []
        lo, hi = expr.get("r") or (0, 0)
        for fact in fn.facts[lo:hi]:
            g = self._condition_fact(fn, fact)
            if g:
                return [g]
        hit = secret_compare(expr)
        if hit:
            return [self._secret_guard(hit, _short(fn), True)]
        return []

    def _walk(self, fn: PhpFunc, depth: int, seen: set, conditional: bool):
        if not fn or fn.id in seen:
            return [], False
        seen = seen | {fn.id}
        return self._stmts(fn, fn.skel, depth, seen, conditional)

    def _stmts(self, fn: PhpFunc, stmts, depth: int, seen: set, conditional: bool):
        guards = []
        for s in stmts or []:
            g, wrote = self._stmt(fn, s, depth, seen, conditional)
            guards.extend(g)
            if wrote and not conditional:
                return guards, True
            if wrote and conditional:
                return guards, False
        return guards, False

    def _stmt(self, fn: PhpFunc, s: dict, depth: int, seen: set, conditional: bool):
        k = s.get("k")
        if k == "if":
            guards = []
            arms = [(s.get("c"), s.get("t"))] + [(ei.get("c"), ei.get("b")) for ei in s.get("ei") or []]
            if s.get("e") is not None:
                arms.append((None, s.get("e")))
            failing = []
            for cond, body in arms:
                if self._fails(fn, body):
                    failing.append(cond)
            for cond in failing:
                if cond is not None:
                    guards.extend(self._condition(fn, cond))
            for _cond, body in arms:
                g, _w = self._stmts(fn, body, depth, seen, True)
                guards.extend(g)
            return guards, False
        if k == "x":
            return self._range(fn, s, depth, seen, conditional)
        if k == "ret":
            g, _w = self._range(fn, s, depth, seen, conditional)
            return g, True
        if k == "seq":
            return self._stmts(fn, s.get("b"), depth, seen, conditional)
        if k == "blk":
            guards = []
            for b in s.get("b") or []:
                g, _w = self._stmts(fn, b, depth, seen, True)
                guards.extend(g)
            return guards, False
        if k == "try":
            return self._stmts(fn, s.get("b"), depth, seen, conditional)
        if k == "term":
            return [], False
        return [], False

    # ----- controller middleware and form requests -----
    def _middleware(self, fqcn: str, action: str | None) -> list[dict]:
        out = []
        act = (action or "").lower()
        for ancestor in self.prog.ancestors(fqcn):
            c = self.prog.cls(ancestor)
            if not c:
                continue
            ctor = c.methods.get("__construct")
            if ctor:
                out.extend(self._ctor_mw(ctor, act))
            mw = c.methods.get("middleware")
            if mw:
                out.extend(self._has_mw(mw, act))
        return out

    def _keep(self, name: str, only, exc, act: str) -> dict | None:
        kind = self._mw_kind(name)
        if not kind:
            return None
        if only and act not in only:
            return None
        if exc and act in exc:
            return None
        return {"name": name, "kind": kind, "conditional": False}

    def _ctor_mw(self, fn: PhpFunc, act: str) -> list[dict]:
        # `$this->middleware('role:admin')->only('destroy')` emits the outer `only` call first and the
        # inner `middleware` call afterwards (the walker records a call, then its receiver).
        out = []
        at = _short(fn)
        chained = set()
        facts = [f for f in fn.facts if f.get("t") == "call"]
        for fact in facts:
            m = (fact.get("m") or "").lower()
            recv = fact.get("recv") or {}
            if m in ("only", "except") and recv.get("k") == "mcall" and (recv.get("m") or "").lower() == "middleware":
                names = {x.lower() for a in fact.get("args") or [] for x in _strs(a)}
                for spec in _mw_specs(recv.get("args") or []):
                    spec[m] = names
                    g = self._keep(spec["name"], spec["only"], spec["except"], act)
                    if g:
                        g["at"] = at
                        out.append(g)
                chained.add(fact.get("line"))
            elif m == "middleware" and recv.get("k") == "this" and fact.get("line") not in chained:
                for spec in _mw_specs(fact.get("args") or []):
                    g = self._keep(spec["name"], spec["only"], spec["except"], act)
                    if g:
                        g["at"] = at
                        out.append(g)
        return out

    def _has_mw(self, fn: PhpFunc, act: str) -> list[dict]:
        out = []
        at = _short(fn)
        for fact in fn.facts:
            if fact.get("t") == "return":
                for spec in _mw_specs([fact.get("expr")]):
                    g = self._keep(spec["name"], spec["only"], spec["except"], act)
                    if g:
                        g["at"] = at
                        out.append(g)
            elif fact.get("t") == "new" and str(fact.get("class") or "").endswith("\\Controllers\\Middleware"):
                for spec in _mw_specs([fact]):
                    g = self._keep(spec["name"], spec["only"], spec["except"], act)
                    if g:
                        g["at"] = at
                        out.append(g)
        if not out:
            out.extend(self._ctor_mw(fn, act))
        return out

    def _form_requests(self, fn: PhpFunc) -> list[dict]:
        out = []
        seen = set()
        for p in fn.params:
            for t in p.get("types") or []:
                c = self.prog.cls(t)
                if not c or c.fqcn in seen or not _is_form_request(self.prog, c.fqcn):
                    continue
                seen.add(c.fqcn)
                auth = self.prog.find_method(c.fqcn, "authorize")
                if not auth or auth.abstract or _returns_only_true(auth):
                    continue
                phrase, kind = _authorize_phrase(auth)
                short = c.fqcn.split("\\")[-1]
                name = f"{short}::authorize" + (f" ({phrase})" if phrase else "")
                out.append({"name": name, "kind": kind, "at": f"{short}::authorize", "conditional": False})
        return out


def _is_form_request(prog: PhpProgram, fqcn: str) -> bool:
    return any(a.split("\\")[-1] == "FormRequest" for a in prog.ancestors(fqcn))


def _returns_only_true(fn: PhpFunc) -> bool:
    sk = fn.skel or []
    if len(sk) != 1 or sk[0].get("k") != "ret":
        return False
    x = sk[0].get("x") or {}
    return x.get("k") == "lit" and x.get("v") is True


def _authorize_phrase(fn: PhpFunc) -> tuple[str | None, str]:
    for fact in fn.facts:
        if fact.get("t") != "call":
            continue
        m = fact.get("m") or ""
        ml = m.lower()
        if ml in _PERM or ml in _ROLE:
            ab = _ability(fact.get("args"))
            kind = "role" if ml in _ROLE else "permission"
            return _quote(m, ab), kind
    return None, "policy"


def _strs(a) -> list[str]:
    if not isinstance(a, dict):
        return []
    s = _str_of(a)
    if s:
        return [s]
    if a.get("k") == "arr":
        out = []
        for it in a.get("items") or []:
            out.extend(_strs(it.get("v")))
        return out
    return []


def _nameset(a) -> set[str] | None:
    if a is None:
        return None
    vals = {x.lower() for x in _strs(a)}
    return vals or None


def _mw_specs(nodes) -> list[dict]:
    out = []

    def walk(d, only=None, exc=None):
        if not isinstance(d, dict):
            return
        if d.get("k") == "str" or (d.get("k") == "lit" and isinstance(d.get("v"), str)):
            out.append({"name": d["v"], "only": only, "except": exc})
            return
        if d.get("k") == "classconst" and d.get("class"):
            out.append({"name": d["class"].split("\\")[-1], "only": only, "except": exc})
            return
        if d.get("k") in ("new", "call") or d.get("t") == "new":
            args = d.get("args") or []
            name = _str_of(args[0]) if args else None
            if not name and args and args[0].get("k") == "classconst":
                name = (args[0].get("class") or "").split("\\")[-1]
            named = {a.get("named"): a for a in args if isinstance(a, dict) and a.get("named")}
            o = _nameset(named.get("only")) if "only" in named else (_nameset(args[1]) if len(args) > 1 and "only" not in named and "except" not in named else None)
            e = _nameset(named.get("except")) if "except" in named else None
            if name:
                out.append({"name": name, "only": o, "except": e})
            return
        if d.get("k") == "arr":
            for it in d.get("items") or []:
                walk(it.get("v"), only, exc)
            return
        if d.get("k") == "mcall" and (d.get("m") or "").lower() == "middleware":
            walk_args = d.get("args") or []
            for a in walk_args:
                walk(a, only, exc)

    for n in nodes:
        walk(n)
    return out


def _dedupe(guards: list[dict]) -> list[dict]:
    out, seen = [], set()
    for g in guards:
        key = (g["name"], g["kind"], g["at"], bool(g["conditional"]))
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": g["name"], "kind": g["kind"], "at": g["at"], "conditional": bool(g["conditional"])})
    strong = {(g["name"], g["kind"], g["at"]) for g in out if not g["conditional"]}
    return [g for g in out if not g["conditional"] or (g["name"], g["kind"], g["at"]) not in strong]

"""Deterministic guard / gating analysis for PHP, computed from the source files alone.

Given a *gate scenario* (e.g. "new_inventory": the tenant's settings have
features.new_inventory.enabled = true),
an abstract interpreter walks each function's control skeleton (emitted by extract.php) and
marks the fact ranges that cannot execute under the scenario. Edges emitted from those facts
get `gate=<scenario>` and a guard evidence `file:line` (the predicate call that decided it).

Values are abstract: concrete PHP literals, TRUTHY / FALSY (known truthiness), or TOP (unknown).
Conditions become boolean formulas over unknown atoms (same canonical expression text = same
atom); a branch is dead when its path condition is unsatisfiable (brute-force over <=12 atoms)
*and* the decision depends on a scenario fact. Code that is dead regardless of the scenario is
never labelled gated.

Scenario facts are seeded from settings reads (`$x->getSetting('<key>')`, key may be a class
constant) and propagate through method summaries (a method whose every return evaluates to the
same value under the scenario becomes a derived predicate, e.g. FeatureGate::usesNewInventory).
Summaries are evaluated per literal-argument tuple (one level of call-site sensitivity, so
`browse($r, 'authors')` is evaluated with $action = 'authors').

Assumption "inputs present": a branch whose condition only tests a bare variable for
null/empty (`!$tenant`, `$x === null`, `is_null($x)`, `empty($x)`) while the variable's value is
unknown is treated as not taken (never marked dead). This lets `if (!$tenant) return false;`
prologues not spoil a predicate.
"""
from __future__ import annotations

import itertools
import json
from collections import defaultdict


class _S:
    def __init__(self, n):
        self.n = n

    def __repr__(self):
        return self.n


TOP, TRUTHY, FALSY, EMPTY = _S("TOP"), _S("TRUTHY"), _S("FALSY"), _S("EMPTY")
NONE = frozenset()
ABORT_FNS = ("abort", "abort_if", "abort_unless")


def abort_fn(expr) -> str | None:
    """`abort` / `abort_if` / `abort_unless` when `expr` is that control-skeleton call."""
    if isinstance(expr, dict) and expr.get("k") == "call" and expr.get("fn") in ABORT_FNS:
        return expr["fn"]
    return None
NONNULL_FUNCS = {"response", "redirect", "view", "collect", "now", "today", "back", "url", "route", "app", "request"}
SCALAR_RET = {"bool", "int", "float", "string", "array", "mixed", "void", "null", "false", "true", "iterable", "callable", "never", "object", "static", "self"}


def truth(v):
    if v is TOP:
        return None
    if v is TRUTHY:
        return True
    if v is FALSY or v is EMPTY:
        return False
    if isinstance(v, str):
        return v not in ("", "0")
    return bool(v)


def concrete(v):
    return not isinstance(v, _S)


def join(vals):
    vals = list(vals)
    if not vals:
        return (TOP, NONE)
    why = frozenset().union(*(w for _, w in vals))
    first = vals[0][0]
    if all((concrete(v) == concrete(first)) and (v == first if concrete(v) else v is first) and type(v) is type(first) for v, _ in vals):
        return (first, why)
    ts = {truth(v) for v, _ in vals}
    if ts == {True}:
        return (TRUTHY, why)
    if ts == {False}:
        return (FALSY, why)
    return (TOP, NONE)


# ---- boolean formulas over unknown atoms ----
# ("c", bool, why) | ("a", key) | ("n", f) | ("&", f, g) | ("|", f, g)

def f_atoms(f, out):
    t = f[0]
    if t == "a":
        out.add(f[1])
    elif t == "n":
        f_atoms(f[1], out)
    elif t in "&|":
        f_atoms(f[1], out)
        f_atoms(f[2], out)
    return out


def f_why(f, out=None):
    out = set() if out is None else out
    t = f[0]
    if t == "c":
        out |= f[2]
    elif t == "n":
        f_why(f[1], out)
    elif t in "&|":
        f_why(f[1], out)
        f_why(f[2], out)
    return out


def f_eval(f, asg):
    t = f[0]
    if t == "c":
        return f[1]
    if t == "a":
        return asg[f[1]]
    if t == "n":
        return not f_eval(f[1], asg)
    if t == "&":
        return f_eval(f[1], asg) and f_eval(f[2], asg)
    return f_eval(f[1], asg) or f_eval(f[2], asg)


def f_sat(f, max_atoms=12):
    atoms = sorted(f_atoms(f, set()))
    if len(atoms) > max_atoms:
        return True
    for bits in itertools.product((False, True), repeat=len(atoms)):
        if f_eval(f, dict(zip(atoms, bits))):
            return True
    return False


def f_and(fs):
    out = ("c", True, NONE)
    for f in fs:
        out = ("&", out, f)
    return out


def canon(e):
    if isinstance(e, dict):
        return "{" + ",".join(f"{k}:{canon(v)}" for k, v in sorted(e.items()) if k not in ("r", "f", "line")) + "}"
    if isinstance(e, list):
        return "[" + ",".join(canon(x) for x in e) + "]"
    return json.dumps(e)


class Frame:
    def __init__(self, fn, env, record):
        self.fn, self.env, self.record = fn, env, record
        self.returns = []
        self.decisions = set()   # whys of scenario-decided branches (control dependence)


class GateEvaluator:
    def __init__(self, prog, scenario: dict):
        self.prog = prog
        self.name = scenario["name"]
        self.accessors = {a.lower() for a in scenario.get("setting_accessors", ["getSetting"])}
        self.true_keys = set(scenario.get("true_settings", []))
        self.false_keys = set(scenario.get("false_settings", []))
        self.memo = {}
        self.active = set()
        self.target_cache = {}
        self.predicates = {}
        self.stats = defaultdict(int)

    # ---------- helpers ----------
    def const_value(self, cls, name):
        for a in self.prog.ancestors(cls) if cls else []:
            c = self.prog.cls(a)
            if not c:
                continue
            for k in c.consts:
                if k["name"] == name:
                    return k.get("value")
        return TOP

    def targets(self, fr, fi):
        key = (fr.fn.id, fi)
        if key not in self.target_cache:
            from .plugin import ResolveCtx
            f = fr.fn.facts[fi]
            self.target_cache[key] = self.prog.call_targets(fr.fn, f, ResolveCtx(self.prog, fr.fn))[0] if f.get("t") == "call" else []
        return self.target_cache[key]

    def mark(self, fr, r, why, lo_hi=None):
        if why:
            fr.decisions |= why
        if not fr.record or not why or not r:
            return
        lo, hi = r
        if hi <= lo:
            return
        w = sorted(why)[0]
        site = w.split("@", 1)[1] if "@" in w else f"{fr.fn.file}"
        info = {"gate": self.name, "guard": site, "expr": "; ".join(sorted(why))[:300], "src": fr.fn.id}
        for i in range(lo, hi):
            fr.fn.dead.setdefault(i, info)

    @staticmethod
    def span(stmts):
        rs = [s.get("r") for s in stmts or [] if s.get("r")]
        return (rs[0][0], rs[-1][1]) if rs else None

    # ---------- summaries ----------
    def summary(self, m, argvals, depth):
        key = (m.id, tuple(v if concrete(v) and not isinstance(v, (list, dict)) else repr(v) for v, _ in argvals))
        if key in self.memo:
            return self.memo[key]
        if key in self.active or depth > 5 or not m.skel:
            return (TOP, NONE)
        self.active.add(key)
        env = {}
        for i, p in enumerate(m.params):
            env[p["name"]] = argvals[i] if i < len(argvals) else (TOP, NONE)
        fr = Frame(m, env, record=False)
        self.exec_block(fr, m.skel, depth + 1)
        self.active.discard(key)
        res = join(fr.returns) if fr.returns else (TOP, NONE)
        if res[0] is not TOP and fr.decisions:
            res = (res[0], res[1] | frozenset(fr.decisions))
        self.memo[key] = res
        return res

    def call_value(self, fr, e, argvals, recv, depth):
        fi = e.get("f")
        name = (e.get("m") or "").lower()
        fnname = e.get("fn")
        if fi is None:
            return (TOP, NONE)
        fact = fr.fn.facts[fi]
        if not name and fact.get("kind") == "static":
            name = (fact.get("m") or "").lower()
        line = fact.get("line")
        if name in self.accessors and argvals:
            k = argvals[0][0]
            if isinstance(k, str) and (k in self.true_keys or k in self.false_keys):
                self.stats["setting_reads"] += 1
                return (k in self.true_keys, frozenset({f"setting '{k}'@{fr.fn.file}:{line}"}))
        tg = self.targets(fr, fi)
        if tg:
            vals = [self.summary(m, argvals, depth) for m, _ in tg]
            v, why = join(vals)
            if v is not TOP and why:
                label = "/".join(sorted({m.id.split(":", 1)[1].split("\\")[-1] for m, _ in tg}))
                return (v, frozenset({f"{label}()@{fr.fn.file}:{line}"}))
            if v is not TOP:
                return (v, NONE)
            if all(not m.returns_nullable and m.returns and all(t.lower() not in SCALAR_RET for t in m.returns) for m, _ in tg):
                return (TRUTHY, NONE)
            return (TOP, NONE)
        if fnname in NONNULL_FUNCS:
            return (TRUTHY, NONE)
        if recv is not None and recv[0] is TRUTHY and fr.fn.facts[fi].get("kind") == "method" and name in ("json", "make", "noContent", "download", "stream", "view", "to", "route", "with"):
            return (TRUTHY, NONE)
        return (TOP, NONE)

    # ---------- expressions ----------
    def is_presence_check(self, e, fr):
        k = e.get("k")
        x = None
        if k == "not":
            x = e["x"]
        elif k == "cmp" and e.get("op") in ("===", "=="):
            l, r = e["l"], e["rr"]
            if r.get("k") == "lit" and r.get("v") is None:
                x = l
            elif l.get("k") == "lit" and l.get("v") is None:
                x = r
        elif k == "call" and e.get("fn") in ("is_null", "empty") and e.get("args"):
            x = e["args"][0]
        return x is not None and x.get("k") == "var" and fr.env.get(x["n"], (TOP, NONE))[0] is TOP

    def cond(self, fr, e, depth):
        """Formula for the truthiness of e (also performs e's side effects / dead marking)."""
        k = e.get("k")
        if k == "not":
            return ("n", self.cond(fr, e["x"], depth))
        if k in ("and", "or"):
            fl = self.cond(fr, e["l"], depth)
            short_val = (k == "or")
            if not f_sat(("n", fl) if short_val else fl):  # left decides
                self.mark(fr, e["rr"].get("r"), f_why(fl))
                return fl
            env0 = dict(fr.env)
            fr_ = self.cond(fr, e["rr"], depth)
            # right side may not run: variables it assigned become unknown
            for kk in set(fr.env) | set(env0):
                if fr.env.get(kk) != env0.get(kk):
                    fr.env[kk] = join([fr.env.get(kk, (TOP, NONE)), env0.get(kk, (TOP, NONE))])
            return ("&" if k == "and" else "|", fl, fr_)
        v, why = self.val(fr, e, depth)
        t = truth(v)
        if t is not None:
            return ("c", t, why)
        return ("a", canon(e))

    def decide(self, f):
        if not f_sat(f):
            return False
        if not f_sat(("n", f)):
            return True
        return None

    def val(self, fr, e, depth=0):
        if e is None:
            return (None, NONE)
        k = e.get("k")
        if k == "lit":
            return (e.get("v"), NONE)
        if k == "const":
            v = self.const_value(e.get("class"), e.get("n"))
            return (v, NONE) if v is not None and v is not TOP else (TOP, NONE)
        if k == "var":
            return fr.env.get(e["n"], (TOP, NONE))
        if k in ("not", "and", "or"):
            f = self.cond(fr, e, depth)
            d = self.decide(f)
            return (TOP, NONE) if d is None else (d, frozenset(f_why(f)))
        if k == "cmp":
            (a, wa), (b, wb) = self.val(fr, e["l"], depth), self.val(fr, e["rr"], depth)
            op = e["op"]
            res = None
            if concrete(a) and concrete(b):
                eq = (a == b and type(a) is type(b)) if op in ("===", "!==") else (a == b or (truth(a) is False and truth(b) is False and (a is None or b is None)))
                res = eq if op in ("===", "==") else not eq
            elif (a is TRUTHY and b is None) or (b is TRUTHY and a is None):
                res = op in ("!==", "!=")
            return (TOP, NONE) if res is None else (res, wa | wb)
        if k == "asg":
            v = self.val(fr, e["x"], depth)
            fr.env[e["n"]] = v
            return v
        if k == "cast":
            v, w = self.val(fr, e["x"], depth)
            if e.get("to") == "bool":
                t = truth(v)
                return (TOP, NONE) if t is None else (t, w)
            return (v, w)
        if k == "tern":
            f = self.cond(fr, e["c"], depth)
            d = self.decide(f)
            why = frozenset(f_why(f))
            if d is True:
                if e.get("e"):
                    self.mark(fr, e["e"].get("r"), why)
                v, w = self.val(fr, e["t"], depth) if e.get("t") else (True, NONE)
                return (v, w | why)
            if d is False:
                if e.get("t"):
                    self.mark(fr, e["t"].get("r"), why)
                v, w = self.val(fr, e["e"], depth)
                return (v, w | why)
            env0 = dict(fr.env)
            v1 = self.val(fr, e["t"], depth) if e.get("t") else (TOP, NONE)
            env1, fr.env = fr.env, dict(env0)
            v2 = self.val(fr, e["e"], depth)
            fr.env = self.merge([env1, fr.env])
            return join([v1, v2])
        if k == "coal":
            l = self.val(fr, e["l"], depth)
            if l[0] is None:
                v, w = self.val(fr, e["rr"], depth)
                return (v, w | l[1])
            if (concrete(l[0]) and l[0] is not None) or l[0] is TRUTHY:
                self.mark(fr, e["rr"].get("r"), l[1])
                return l
            return join([l, self.val(fr, e["rr"], depth)])
        if k == "arr":
            for c in e.get("ch", []):
                self.val(fr, c, depth)
            return (EMPTY, NONE) if e.get("empty") else (TRUTHY, NONE)
        if k == "new":
            for a in e.get("args", []):
                self.val(fr, a, depth)
            return (TRUTHY, NONE)
        if k == "match":
            subj = self.val(fr, e["c"], depth)
            decided, possible, why = False, [], set(subj[1])
            for arm in e["arms"]:
                if decided:
                    self.mark(fr, arm.get("r"), frozenset(why))
                    continue
                if arm["conds"] is None:
                    st = True
                else:
                    sts = []
                    for c in arm["conds"]:
                        cv = self.val(fr, c, depth)
                        why |= cv[1]
                        if concrete(subj[0]) and concrete(cv[0]):
                            sts.append(subj[0] == cv[0] and type(subj[0]) is type(cv[0]))
                        else:
                            sts.append(None)
                    st = True if True in sts else (False if all(s is False for s in sts) else None)
                if st is False:
                    self.mark(fr, arm.get("r"), frozenset(why) if subj[1] else NONE)
                    continue
                possible.append(arm)
                if st is True:
                    decided = True
            vals = [self.val(fr, a["body"], depth) for a in possible]
            v = join(vals)
            return (v[0], v[1] | frozenset(why)) if v[0] is not TOP else v
        if k == "clo":
            # closure body: may run (0..n times) with the captured environment; its returns are its own
            sub = Frame(fr.fn, dict(fr.env), fr.record)
            sub.fn = fr.fn
            for p in e.get("params", []):
                sub.env[p] = (TOP, NONE)
            if "b" in e:
                self.exec_block(sub, e["b"], depth)
            elif e.get("x"):
                self.val(sub, e["x"], depth)
            fr.decisions |= sub.decisions
            return (TRUTHY, NONE)
        if k == "call":
            recv = self.val(fr, e["recv"], depth) if e.get("recv") else None
            args = [self.val(fr, a, depth) for a in e.get("args", [])]
            return self.call_value(fr, e, args, recv, depth)
        for c in e.get("ch", []):
            self.val(fr, c, depth)
        return (TOP, NONE)

    # ---------- statements ----------
    def merge(self, envs):
        if len(envs) == 1:
            return envs[0]
        out = {}
        for kk in set().union(*envs):
            out[kk] = join([en.get(kk, (TOP, NONE)) for en in envs])
        return out

    def exec_block(self, fr, stmts, depth):
        for i, s in enumerate(stmts or []):
            term, why = self.exec_stmt(fr, s, depth)
            if term:
                if why and i + 1 < len(stmts):
                    self.mark(fr, self.span(stmts[i + 1:]), why)
                return True, why
        return False, NONE

    def exec_stmt(self, fr, s, depth):
        k = s.get("k")
        if k == "x":
            x = s["x"]
            aborted = abort_fn(x)
            if aborted:
                args = [self.val(fr, a, depth) for a in x.get("args", [])]
                if aborted == "abort":
                    return True, NONE
                if args:
                    t = truth(args[0][0])
                    if (aborted == "abort_if" and t is True) or (aborted == "abort_unless" and t is False):
                        return True, args[0][1]
                return False, NONE
            self.val(fr, x, depth)
            return False, NONE
        if k == "ret":
            v = self.val(fr, s["x"], depth) if s.get("x") else (None, NONE)
            fr.returns.append(v)
            return True, NONE
        if k in ("term", "brk"):
            return True, NONE
        if k == "seq":
            return self.exec_block(fr, s["b"], depth)
        if k == "if":
            return self.exec_if(fr, s, depth)
        if k == "blk":
            for p in s.get("pre", []):
                self.val(fr, p, depth)
            for v in s.get("av", []):
                fr.env[v] = (TOP, NONE)
            base = dict(fr.env)
            for b in s.get("b", []):
                fr.env = dict(base)
                self.exec_block(fr, b, depth)
            fr.env = base
            return False, NONE
        if k == "try":
            t, w = self.exec_block(fr, s["b"], depth)
            after = dict(fr.env)
            for v in s.get("av", []):
                after[v] = (TOP, NONE)
            for c in s.get("catches", []):
                fr.env = dict(after)
                self.exec_block(fr, c, depth)
            fr.env = after
            if s.get("fin"):
                self.exec_block(fr, s["fin"], depth)
            return (t and not s.get("catches")), (w if t and not s.get("catches") else NONE)
        return False, NONE

    def exec_if(self, fr, s, depth):
        branches = [(s["c"], s["t"], None)] + [(ei["c"], ei["b"], ei.get("r")) for ei in s.get("ei", [])]
        else_b = s.get("e")
        prior = []          # formulas of earlier conditions (all false to reach here)
        outs, whys = [], set()
        base = fr.env
        taken = False
        for c, body, whole in branches:
            if taken:
                self.mark(fr, whole or self.span(body), frozenset(whys))
                continue
            fr.env = dict(base)
            if self.is_presence_check(c, fr):
                f = ("c", False, NONE)  # inputs-present assumption: not taken, never marked
            else:
                f = self.cond(fr, c, depth)
            base = fr.env  # condition side effects (assignments) persist
            path = f_and([("n", p) for p in prior] + [f])
            w = f_why(f)
            for p in prior:
                f_why(p, w)
            if not f_sat(path):
                whys |= w
                self.mark(fr, self.span(body), frozenset(w))
                prior.append(f)
                continue
            fr.env = dict(base)
            t, tw = self.exec_block(fr, body, depth)
            outs.append((fr.env, t))
            if t:
                whys |= tw
            if not f_sat(f_and([("n", p) for p in prior] + [("n", f)])):  # this branch is certain
                whys |= w
                taken = True
            prior.append(f)
        if not taken:
            rest = f_and([("n", p) for p in prior])
            if else_b is not None:
                if not f_sat(rest):
                    self.mark(fr, s.get("er") and tuple(s["er"]) or self.span(else_b), frozenset(whys | f_why(rest)))
                else:
                    fr.env = dict(base)
                    t, tw = self.exec_block(fr, else_b, depth)
                    outs.append((fr.env, t))
                    if t:
                        whys |= tw
            elif f_sat(rest):
                outs.append((dict(base), False))
            else:
                whys |= f_why(rest)
        elif else_b is not None:
            self.mark(fr, s.get("er") and tuple(s["er"]) or self.span(else_b), frozenset(whys))
        live = [en for en, t in outs if not t]
        if not live:
            fr.env = base
            return True, frozenset(whys)
        fr.env = self.merge(live)
        return False, NONE

    # ---------- driver ----------
    def run(self):
        for fn in self.prog.all_funcs:
            if not fn.skel:
                continue
            fr = Frame(fn, {p["name"]: (TOP, NONE) for p in fn.params}, record=True)
            try:
                self.exec_block(fr, fn.skel, 0)
            except RecursionError:
                self.stats["recursion_errors"] += 1
                continue
            if fn.dead:
                self.stats["functions_with_gated_code"] += 1
                self.stats["gated_facts"] += len(fn.dead)
        byid = {f.id: f for f in self.prog.all_funcs}
        for mid in sorted({mid for mid, _ in list(self.memo)}):
            m = byid.get(mid)
            if not m:
                continue
            v, why = self.summary(m, [(TOP, NONE)] * len(m.params), 0)
            if concrete(v) and isinstance(v, bool) and why:
                self.predicates[mid] = (v, "; ".join(sorted(why))[:300])
        self.stats["predicates"] = len(self.predicates)
        return dict(self.stats)

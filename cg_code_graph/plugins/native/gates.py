"""Compile-time gates for systems code: Rust `#[cfg(...)]` predicates and C/C++ preprocessor conditions.

Two uses:
  1. Always: every item / statement under a condition gets GATED_BY edges to `feature:<pkg>/<name>`, `cfg:<pred>`
     (Rust) or `define:<MACRO>` (C/C++) nodes, with the attribute / directive line as evidence.
  2. With a gate scenario (`index --gates FILE`, first scenario), conditions are evaluated three-valued
     (true / false / unknown) and references made inside a region whose condition is *false* get edge.gate set,
     exactly like the PHP feature-flag gating, so `reaches` lists them under GATED.

Scenario keys understood here (others are ignored, so one gates file can serve several languages):
  features_on / features_off   Cargo features ("fs" for any package, or "kv-core/fs")
  cargo_features               "default" | "none" | [list]: exactly these features (per package, plus what they
                               enable) are on; every other feature is off
  cfg_true / cfg_false         other cfg atoms: "unix", "windows", 'target_os="linux"', "test", "debug_assertions"
  defines_on / defines_off     C/C++ macros ("NAME" or "NAME=VALUE"); defines_on = defined, defines_off = undefined
"""
from __future__ import annotations

import re

TRUE, FALSE, UNKNOWN = True, False, None


# ---------------------------------------------------------------- Rust cfg predicates
def parse_cfg(s: str):
    """'all(unix, not(feature = "x"))' -> ('all', [('atom','unix',None), ('not', ('atom','feature','x'))])."""
    toks = re.findall(r'"(?:[^"\\]|\\.)*"|[A-Za-z_][\w:]*|[(),=]', s)
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        t = peek()
        pos[0] += 1
        return t

    def expr():
        t = take()
        if t is None:
            return ("atom", "?", None)
        if t in ("all", "any", "not") and peek() == "(":
            take()
            args = []
            while peek() not in (")", None):
                args.append(expr())
                if peek() == ",":
                    take()
            take()
            if t == "not":
                return ("not", args[0] if args else ("atom", "?", None))
            return (t, args)
        if peek() == "=":
            take()
            v = take() or '""'
            return ("atom", t, v.strip('"'))
        return ("atom", t, None)

    try:
        return expr()
    except Exception:
        return ("atom", s.strip(), None)


def cfg_atoms(p) -> list[tuple[str, str | None]]:
    if p[0] == "atom":
        return [(p[1], p[2])]
    if p[0] == "not":
        return cfg_atoms(p[1])
    return [a for x in p[1] for a in cfg_atoms(x)]


def atom_text(key: str, val: str | None) -> str:
    return key if val is None else f'{key}="{val}"'


class Scenario:
    """First gate scenario of the gates file, seen from native code."""

    def __init__(self, sc: dict | None, packages: dict[str, dict] | None = None):
        sc = sc or {}
        self.name = sc.get("name")
        self.raw = sc
        self.features_on = set(sc.get("features_on") or [])
        self.features_off = set(sc.get("features_off") or [])
        self.cfg_true = set(sc.get("cfg_true") or [])
        self.cfg_false = set(sc.get("cfg_false") or [])
        self.defines = {}
        for d in sc.get("defines_on") or []:
            k, _, v = str(d).partition("=")
            self.defines[k.strip()] = v.strip() or "1"
        self.undefined = set(sc.get("defines_off") or [])
        # cargo_features: compute the exact enabled set per package
        self.exact: dict[str, set] | None = None
        cf = sc.get("cargo_features")
        if cf is not None and packages:
            self.exact = {}
            for pkg, feats in packages.items():
                want = set(["default"] if cf == "default" else [] if cf == "none" else
                           [f.split("/", 1)[1] if "/" in f and f.split("/", 1)[0] == pkg else f for f in cf if "/" not in f or f.startswith(pkg + "/")])
                on, todo = set(), list(want)
                while todo:
                    f = todo.pop()
                    if f in on:
                        continue
                    on.add(f)
                    for g in feats.get(f, []):
                        if "/" not in g and not g.startswith("dep:"):
                            todo.append(g)
                self.exact[pkg] = on

    @property
    def active(self) -> bool:
        return bool(self.name)

    # Rust
    def feature(self, pkg: str, f: str):
        if f"{pkg}/{f}" in self.features_on or f in self.features_on:
            return TRUE
        if f"{pkg}/{f}" in self.features_off or f in self.features_off:
            return FALSE
        if self.exact is not None and pkg in self.exact:
            return f in self.exact[pkg]
        return UNKNOWN

    def eval_cfg(self, p, pkg: str):
        k = p[0]
        if k == "atom":
            key, val = p[1], p[2]
            if key == "feature" and val is not None:
                return self.feature(pkg, val)
            t = atom_text(key, val)
            if t in self.cfg_true:
                return TRUE
            if t in self.cfg_false:
                return FALSE
            return UNKNOWN
        if k == "not":
            v = self.eval_cfg(p[1], pkg)
            return None if v is None else (not v)
        vals = [self.eval_cfg(x, pkg) for x in p[1]]
        if k == "all":
            if any(v is False for v in vals):
                return FALSE
            return TRUE if all(v is True for v in vals) else UNKNOWN
        if any(v is True for v in vals):
            return TRUE
        return FALSE if vals and all(v is False for v in vals) else UNKNOWN

    # C / C++
    def eval_pp(self, expr: str):
        return eval_pp(expr, self.defines, self.undefined)


# ---------------------------------------------------------------- C preprocessor conditions
_PP_TOK = re.compile(r"\s*(defined|\|\||&&|==|!=|<=|>=|<<|>>|[!()<>+\-*/%]|0[xX][0-9a-fA-F]+[uUlL]*|\d+[uUlL]*|[A-Za-z_]\w*|.)")


def pp_macros(expr: str) -> list[str]:
    """Macro names a condition depends on (identifiers, `defined X`), in order, without duplicates."""
    out = []
    for m in re.finditer(r"[A-Za-z_]\w*", expr):
        n = m.group(0)
        if n != "defined" and n not in out and not n.startswith("__has_"):
            out.append(n)
    return out


def eval_pp(expr: str, defines: dict[str, str], undefined: set[str]):
    """Three-valued evaluation of a #if expression. Unknown macros make the result unknown (None)
    unless the other operand decides it (0 && x, 1 || x)."""
    toks = [t for t in _PP_TOK.findall(expr) if t.strip()]
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        t = peek()
        pos[0] += 1
        return t

    def num(t):
        try:
            return int(t.rstrip("uUlL"), 0)
        except ValueError:
            return None

    def primary():
        t = take()
        if t is None:
            return None
        if t == "(":
            v = lor()
            if peek() == ")":
                take()
            return v
        if t == "!":
            v = primary()
            return None if v is None else int(not v)
        if t == "-":
            v = primary()
            return None if v is None else -v
        if t == "defined":
            paren = peek() == "("
            if paren:
                take()
            name = take()
            if paren and peek() == ")":
                take()
            if name in defines:
                return 1
            if name in undefined:
                return 0
            return None
        n = num(t)
        if n is not None:
            return n
        if re.match(r"[A-Za-z_]", t):
            if peek() == "(":  # function-like macro / __has_include(...): unknown
                depth = 0
                while peek() is not None:
                    x = take()
                    depth += {"(": 1, ")": -1}.get(x, 0)
                    if depth == 0:
                        break
                return None
            if t in defines:
                return num(defines[t]) if num(defines[t]) is not None else None
            if t in undefined:
                return 0
            return None
        return None

    def binop(sub, ops):
        def f():
            v = sub()
            while peek() in ops:
                op = take()
                w = sub()
                if v is None or w is None:
                    v = None
                    continue
                v = {"==": int(v == w), "!=": int(v != w), "<": int(v < w), ">": int(v > w), "<=": int(v <= w),
                     ">=": int(v >= w), "+": v + w, "-": v - w, "*": v * w, "/": (v // w if w else None),
                     "%": (v % w if w else None), "<<": v << w if 0 <= w < 64 else None, ">>": v >> w if 0 <= w < 64 else None}[op]
            return v
        return f

    mul = binop(primary, ("*", "/", "%"))
    add = binop(mul, ("+", "-"))
    shift = binop(add, ("<<", ">>"))
    rel = binop(shift, ("<", ">", "<=", ">="))
    eq = binop(rel, ("==", "!="))

    def land():
        v = eq()
        while peek() == "&&":
            take()
            w = eq()
            v = 0 if (v == 0 or w == 0) else (None if v is None or w is None else 1)
        return v

    def lor():
        v = land()
        while peek() == "||":
            take()
            w = land()
            v = 1 if (v not in (None, 0) or w not in (None, 0)) else (None if v is None or w is None else 0)
        return v

    try:
        v = lor()
    except Exception:
        return UNKNOWN
    return UNKNOWN if v is None else bool(v)


def range_lookup(ranges: list[tuple[int, int, object]]):
    """ranges: (start_line, end_line, payload), 1-based inclusive. Returns f(line) -> list of payloads covering it
    (outermost first)."""
    ranges = sorted(ranges, key=lambda r: (r[0], -r[1]))

    def f(line: int):
        return [p for (a, b, p) in ranges if a <= line <= b]
    return f

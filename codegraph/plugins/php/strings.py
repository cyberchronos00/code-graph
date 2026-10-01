"""Static evaluation of PHP string expressions into name patterns.

Used for broadcast channel names (`"orders.{$this->order->id}"`, helper methods that build names, values passed
in at `new Event(...)` sites) and for URLs in HTTP tests. The result is a small set of patterns in which every
part that is unknown statically is a `{placeholder}`.
"""
from __future__ import annotations

import itertools
import re

MAX_ALTS = 8
PH_RE = re.compile(r"\{[^{}]*\}")


def _short(d) -> str:
    """Placeholder name for a dynamic expression: `$x` -> x, `$a->b->id` -> id, `$o->getKey()` -> key."""
    if not d:
        return "?"
    k = d.get("k")
    if k == "var":
        return d.get("n") or "?"
    if k in ("prop", "sprop"):
        return d.get("n") or "?"
    if k in ("mcall", "scall"):
        m = d.get("m") or "?"
        return m[3].lower() + m[4:] if m.startswith("get") and len(m) > 3 else m
    if k == "dim" and (d.get("key") or {}).get("k") == "str":
        return d["key"]["v"]
    if k == "func":
        a = d.get("args") or []
        return _short(a[0]) if a else (d.get("n") or "?")
    return "?"


class StrEval:
    def __init__(self, prog):
        self.prog = prog

    def eval(self, d, fn, bind=None, depth=0) -> list[str]:
        out = self._ev(d, fn, bind or {}, depth)
        seen, res = set(), []
        for s in out:
            if s not in seen:
                seen.add(s)
                res.append(s)
        return res[:MAX_ALTS]

    def _prod(self, lists) -> list[str]:
        out = []
        for combo in itertools.product(*lists):
            out.append("".join(combo))
            if len(out) >= MAX_ALTS:
                break
        return out

    def _ev(self, d, fn, bind, depth) -> list[str]:
        if not d or depth > 10:
            return ["{?}"]
        k = d.get("k")
        if k == "str":
            return [d.get("v") or ""]
        if k == "int":
            return [str(d.get("v"))]
        if k == "interp":
            return self._prod([self._ev(p, fn, bind, depth + 1) for p in d.get("parts") or []])
        if k == "concat":
            return self._prod([self._ev(d.get("l"), fn, bind, depth + 1), self._ev(d.get("r"), fn, bind, depth + 1)])
        if k == "alt":
            out = []
            for o in d.get("opts") or []:
                out += self._ev(o, fn, bind, depth + 1)
            return out[:MAX_ALTS]
        if k == "var":
            n = d.get("n")
            if n in bind:
                bd, bfn, bb = bind[n]
                return self._ev(bd, bfn, bb, depth + 1)
            assigns = [f for f in fn.facts if f.get("t") == "assign" and f.get("var") == n]
            if 0 < len(assigns) <= 3:
                out = []
                for a in assigns:
                    out += self._ev(a["expr"], fn, {}, depth + 1)
                return out[:MAX_ALTS]
            return ["{" + (n or "?") + "}"]
        if k == "prop":
            if (d.get("of") or {}).get("k") == "this":
                return ["{this." + (d.get("n") or "?") + "}"]
            return ["{" + _short(d) + "}"]
        if k == "const":
            v = self.class_const(d.get("class"), d.get("n"))
            return [str(v)] if isinstance(v, (str, int)) else ["{" + (d.get("n") or "?") + "}"]
        if k in ("mcall", "scall", "func"):
            r = self._call(d, fn, bind, depth)
            if r is not None:
                return r
            return ["{" + _short(d) + "}"]
        if k == "dim":
            return ["{" + _short(d) + "}"]
        return ["{?}"]

    def class_const(self, cls, name):
        if not cls or not name:
            return None
        for a in self.prog.ancestors(cls):
            c = self.prog.cls(a)
            if c:
                for k in c.consts:
                    if k.get("name") == name:
                        return k.get("value")
        return None

    def _call(self, d, fn, bind, depth):
        k = d.get("k")
        args = d.get("args") or []
        if k == "func":
            n = (d.get("n") or "").lower().lstrip("\\")
            if n in ("sprintf", "vsprintf") and args and args[0].get("k") == "str":
                return self._sprintf(args[0]["v"], args[1:], fn, bind, depth)
            if n in ("strtolower", "strtoupper", "trim", "strval", "str", "e") and args:
                return self._ev(args[0], fn, bind, depth + 1)
            if n == "implode" and len(args) == 2 and args[1].get("k") == "arr" and args[0].get("k") == "str":
                lists = []
                for j, it in enumerate(args[1].get("items") or []):
                    if j:
                        lists.append([args[0]["v"]])
                    lists.append(self._ev(it.get("v"), fn, bind, depth + 1))
                return self._prod(lists) if lists else [""]
            target = self.prog.functions.get(n)
        elif k == "scall":
            c = d.get("class")
            target = self.prog.find_method(c, d.get("m")) if c and self.prog.cls(c) else None
        else:
            target = None
            of = d.get("of") or {}
            if of.get("k") == "this" and fn.cls:
                target = self.prog.find_method(fn.cls, d.get("m"))
        if not target or target.abstract:
            return None
        rets = [f for f in target.facts if f.get("t") == "return" and not f.get("ctx")]
        if not rets:
            return None
        nb = {}
        for i, p in enumerate(target.params):
            a = next((x for x in args if x.get("named") == p["name"]), None)
            if a is None and i < len(args) and not args[i].get("named"):
                a = args[i]
            if a is not None:
                nb[p["name"]] = (a, fn, bind)
        out = []
        for r in rets[:4]:
            out += self._ev(r["expr"], target, nb, depth + 1)
        return out[:MAX_ALTS]

    def _sprintf(self, fmt, args, fn, bind, depth):
        pieces = re.split(r"(%(?:\d+\$)?[-+ 0']*\d*(?:\.\d+)?[sdfuxX])", fmt)
        lists, i = [], 0
        for p in pieces:
            m = re.fullmatch(r"%(?:(\d+)\$)?[-+ 0']*\d*(?:\.\d+)?[sdfuxX]", p)
            if m:
                idx = int(m.group(1)) - 1 if m.group(1) else i
                i += 1
                lists.append(self._ev(args[idx], fn, bind, depth + 1) if idx < len(args) else ["{?}"])
            else:
                lists.append([p.replace("%%", "%")])
        return self._prod(lists)


def pattern_regex(p: str) -> str:
    out = []
    for part in re.split(r"(\{[^{}]*\})", p):
        if not part:
            continue
        out.append("[^.]+" if part.startswith("{") and part.endswith("}") else re.escape(part))
    return "".join(out)


def concrete(p: str) -> str:
    return PH_RE.sub("X", p)


def fully_dynamic(p: str) -> bool:
    return not PH_RE.sub("", p).strip(". -_:/")


def same_shape(a: str, b: str) -> bool:
    return PH_RE.sub("{}", a) == PH_RE.sub("{}", b)


def channel_match(name: str, declared: str) -> bool:
    """A published / subscribed channel name (placeholders = runtime values) fits a declared channel pattern."""
    if fully_dynamic(name):
        return False
    if re.fullmatch(pattern_regex(declared), concrete(name)):
        return True
    # a placeholder on the publishing side may itself stand for a dotted value (`{channel}`): try the reverse fit
    return bool(re.fullmatch(pattern_regex(name), concrete(declared)))

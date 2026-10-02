"""Platform conditions in source text: runtime and compile-time platform checks in brace languages.

Finds the code regions that run only on some targets, for the platform tags (codegraph/platforms.py):

  TypeScript / JavaScript  `if (Platform.OS === 'ios') {...} else {...}`, `Platform.OS == 'web' ? a : b`,
                           `Platform.select({ios: ..., android: ..., native: ..., default: ...})`,
                           `switch (Platform.OS) { case 'ios': ... }`, Node / Electron `process.platform === 'win32'`
  Dart / Flutter           `Platform.isIOS` / `isAndroid` / `isMacOS` / `isWindows` / `isLinux`, `kIsWeb`,
                           `defaultTargetPlatform == TargetPlatform.iOS`, `switch (defaultTargetPlatform)` statements
                           and expressions
  Rust                     `if cfg!(windows) {...} else {...}`

Each region is (start_line, start_col, end_line, end_col, cond, text, line): `cond` is a cfg-style tree
(codegraph.plugins.native.gates.parse_cfg shape) over the atoms of codegraph/platforms.py, `text` the source
condition, `line` the line of the check. Comments are skipped; string contents are kept only where a check needs
them ('ios'). The scan is textual (no parser), so it reads only the shapes above and leaves anything else untagged.
"""
from __future__ import annotations

import bisect
import re

# ------------------------------------------------------------------ platform tests -> atoms
_TS_OS = r"Platform\s*\.\s*OS"
_NODE_OS = r"(?:process\s*\.\s*platform|os\s*\.\s*platform\s*\(\s*\))"
NODE_PLATFORMS = {"win32": "windows", "darwin": "macos", "linux": "linux", "android": "android", "freebsd": "freebsd",
                  "openbsd": "openbsd", "sunos": "sunos", "aix": "aix", "cygwin": "windows"}
DART_IS = {"isAndroid": "android", "isIOS": "ios", "isMacOS": "macos", "isWindows": "windows", "isLinux": "linux",
           "isFuchsia": "fuchsia"}
DART_TARGET = {"android": "android", "iOS": "ios", "macOS": "macos", "windows": "windows", "linux": "linux",
               "fuchsia": "fuchsia"}

TRIGGERS = {
    "ts": re.compile(r"Platform\s*\.\s*(?:OS|select)\b|process\s*\.\s*platform|os\s*\.\s*platform\s*\("),
    "dart": re.compile(r"\bPlatform\s*\.\s*is[A-Z]|\bkIsWeb\b|TargetPlatform\s*\."),
    "rust": re.compile(r"\bcfg!\s*\("),
}


def _cmp_atoms(lang: str):
    """(regex, fn(match) -> atom tree) for one-token platform tests of a language."""
    q = r"""(?:'([^'\\]*)'|"([^"\\]*)")"""
    if lang == "ts":
        def os_val(m, neg):
            v = (m.group(2) or m.group(3) or "").lower()
            a = ("atom", "platform", v)
            return ("not", a) if neg else a

        def node_val(m, neg):
            v = m.group(2) or m.group(3) or ""
            a = ("atom", "platform", NODE_PLATFORMS.get(v, v))
            return ("not", a) if neg else a
        return [
            (re.compile(rf"{_TS_OS}\s*(===|==|!==|!=)\s*{q}"), lambda m: os_val(m, m.group(1).startswith("!"))),
            (re.compile(rf"{q}\s*(===|==|!==|!=)\s*{_TS_OS}"),
             lambda m: ("not", ("atom", "platform", (m.group(1) or m.group(2)).lower())) if m.group(3).startswith("!")
             else ("atom", "platform", (m.group(1) or m.group(2)).lower())),
            (re.compile(rf"{_NODE_OS}\s*(===|==|!==|!=)\s*{q}"), lambda m: node_val(m, m.group(1).startswith("!"))),
        ]
    if lang == "dart":
        return [
            (re.compile(r"\b(?:io\.|universal_io\.)?Platform\s*\.\s*(is[A-Z]\w*)"),
             lambda m: ("atom", "platform", DART_IS[m.group(1)]) if m.group(1) in DART_IS else ("atom", "?", m.group(0))),
            (re.compile(r"\bkIsWeb\b"), lambda m: ("atom", "platform", "web")),
            (re.compile(r"(?:\bdefaultTargetPlatform|[\w.()]*\.platform)\s*(==|!=)\s*TargetPlatform\s*\.\s*(\w+)"),
             lambda m: _neg(m.group(1) == "!=", ("atom", "target_platform", DART_TARGET.get(m.group(2), m.group(2).lower())))),
            (re.compile(r"TargetPlatform\s*\.\s*(\w+)\s*(==|!=)\s*(?:\bdefaultTargetPlatform|[\w.()]*\.platform)\b"),
             lambda m: _neg(m.group(2) == "!=", ("atom", "target_platform", DART_TARGET.get(m.group(1), m.group(1).lower())))),
        ]
    if lang == "rust":
        return [(re.compile(r"\bcfg!\s*\(((?:[^()]|\([^()]*(?:\([^()]*\))*[^()]*\))*)\)"), lambda m: ("cfg", m.group(1)))]
    return []


def _neg(neg: bool, a):
    return ("not", a) if neg else a


# ------------------------------------------------------------------ masking
def mask(src: str, lang: str) -> str:
    """Same-length copy with comments blanked and string contents replaced by '_' (quotes kept), so brackets and
    operators inside them don't count. Newlines are kept."""
    out = list(src)
    i, n = 0, len(src)
    quotes = ('"', "'", "`") if lang in ("ts", "dart") else ('"',)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if c in quotes:
            triple = lang == "dart" and src.startswith(c * 3, i)
            end_tok = c * 3 if triple else c
            j = i + len(end_tok)
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src.startswith(end_tok, j):
                    break
                if src[j] == "\n" and c != "`" and not triple:
                    break
                j += 1
            for k in range(i + len(end_tok), min(j, n)):
                if out[k] != "\n":
                    out[k] = "_"
            i = j + len(end_tok)
            continue
        i += 1
    return "".join(out)


class _Text:
    def __init__(self, src: str, lang: str):
        self.src, self.lang = src, lang
        self.m = mask(src, lang)
        self.nl = [i for i, c in enumerate(src) if c == "\n"]

    def pos(self, off: int) -> tuple[int, int]:
        """offset -> (1-based line, 0-based col)."""
        k = bisect.bisect_left(self.nl, off)
        start = self.nl[k - 1] + 1 if k else 0
        return k + 1, off - start

    def match_close(self, i: int) -> int:
        """Index of the bracket closing the one at i (or len)."""
        pairs = {"(": ")", "{": "}", "[": "]"}
        stack = [pairs[self.m[i]]]
        j = i + 1
        while j < len(self.m) and stack:
            c = self.m[j]
            if c in pairs:
                stack.append(pairs[c])
            elif c in ")}]":
                if c == stack[-1]:
                    stack.pop()
                else:
                    return j
            j += 1
        return j - 1 if not stack else len(self.m)

    def skip_ws(self, i: int) -> int:
        while i < len(self.m) and self.m[i].isspace():
            i += 1
        return i


# ------------------------------------------------------------------ condition parsing
def parse_condition(text: str, lang: str):
    """Boolean expression of platform tests -> cfg tree, or None when no platform test occurs in it. Other operands
    become unknown atoms ('?'), so `Platform.OS === 'ios' && featureOn` is ios-only with an unknown part."""
    atoms = []
    s = text
    for rx, fn in _cmp_atoms(lang):
        def sub(m):
            atoms.append(fn(m))
            return f" \x00{len(atoms) - 1}\x00 "
        s = rx.sub(sub, s)
    if not atoms:
        return None
    toks = re.findall(r"\x00\d+\x00|&&|\|\||!(?!=)|\(|\)|[^\s()&|!\x00]+|[&|]", s)
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

    def primary():
        t = peek()
        if t is None:
            return ("atom", "?", None)
        if t == "!":
            take()
            return ("not", primary())
        if t == "(":
            take()
            v = lor()
            if peek() == ")":
                take()
            return v
        take()
        if t.startswith("\x00"):
            a = atoms[int(t.strip("\x00"))]
            if a[0] == "cfg":
                from .plugins.native.gates import parse_cfg
                return parse_cfg(a[1])
            return a
        # any other operand (a call, a flag, a comparison): consume up to the next boolean operator
        while peek() not in (None, "&&", "||", ")"):
            take()
        return ("atom", "?", t)

    def land():
        xs = [primary()]
        while peek() == "&&":
            take()
            xs.append(primary())
        return xs[0] if len(xs) == 1 else ("all", xs)

    def lor():
        xs = [land()]
        while peek() == "||":
            take()
            xs.append(land())
        return xs[0] if len(xs) == 1 else ("any", xs)

    try:
        return lor()
    except Exception:  # noqa: BLE001
        return None


def _all(*xs):
    xs = [x for x in xs if x is not None]
    return xs[0] if len(xs) == 1 else ("all", list(xs))


# ------------------------------------------------------------------ region scan
def scan(src: str, lang: str) -> list[tuple]:
    """Platform-conditional regions of one file (see the module docstring)."""
    trig = TRIGGERS.get(lang)
    if trig is None or not trig.search(src):
        return []
    T = _Text(src, lang)
    out: list[tuple] = []

    def region(a: int, z: int, cond, text: str, at: int):
        if z <= a:
            return
        sl, sc = T.pos(a)
        el, ec = T.pos(z)
        out.append((sl, sc, el, ec, cond, " ".join(text.split())[:160], T.pos(at)[0]))

    m = T.m
    # if / else if / else chains
    rx_if = re.compile(r"\bif\s*\(" if lang != "rust" else r"\bif\s+")
    for hit in rx_if.finditer(m):
        if lang != "rust":
            o = hit.end() - 1
            c = T.match_close(o)
            ctext = src[o + 1:c]
            body = T.skip_ws(c + 1)
        else:
            o = hit.end()
            j = o
            while j < len(m) and m[j] != "{":
                if m[j] in "([":
                    j = T.match_close(j)
                j += 1
            ctext, body = src[o:j], j
        cond = parse_condition(ctext, lang)
        if cond is None or (cond[0] == "atom" and cond[1] == "?"):
            continue
        # not the `else if` of a chain already handled: that one is scanned from its head
        before = m[max(0, hit.start() - 12):hit.start()].rstrip()
        if before.endswith("else"):
            continue
        prev = []
        cur_cond, cur_text = cond, ctext
        start = hit.start()
        while True:
            end = _stmt_end(T, body, lang)
            region(body, end, _all(*[("not", p) for p in prev], cur_cond) if prev else cur_cond,
                   cur_text if not prev else f"else if ({cur_text})", start)
            prev.append(cur_cond)
            k = T.skip_ws(end)
            if not m.startswith("else", k) or (k + 4 < len(m) and (m[k + 4].isalnum() or m[k + 4] == "_")):
                break
            k = T.skip_ws(k + 4)
            if m.startswith("if", k) and not (m[k + 2].isalnum() or m[k + 2] == "_"):
                k2 = T.skip_ws(k + 2)
                if lang != "rust" and k2 < len(m) and m[k2] == "(":
                    c2 = T.match_close(k2)
                    cur_text = src[k2 + 1:c2]
                    body = T.skip_ws(c2 + 1)
                elif lang == "rust":
                    j = k2
                    while j < len(m) and m[j] != "{":
                        j += 1
                    cur_text, body = src[k2:j], j
                else:
                    break
                cur_cond = parse_condition(cur_text, lang) or ("atom", "?", cur_text.strip()[:40])
                start = k
                continue
            region(k, _stmt_end(T, k, lang), _all(*[("not", p) for p in prev]), "else of " + " / ".join(
                " ".join(x.split())[:60] for x in [ctext]), start)
            break
    # ternaries: COND ? a : b
    if lang != "rust":
        for q in re.finditer(r"\?(?![.?:\[)\],;=])", m):
            i = q.start()
            a = _cond_start(T, i)
            ctext = src[a:i]
            cond = parse_condition(ctext, lang)
            if cond is None or (cond[0] == "atom" and cond[1] == "?"):
                continue
            colon = _ternary_colon(T, i + 1)
            if colon is None:
                continue
            end = _ternary_end(T, colon + 1)
            region(i + 1, colon, cond, ctext, a)
            region(colon + 1, end, ("not", cond), "not " + ctext.strip(), a)
    # Platform.select({ ios: ..., android: ..., default: ... })
    if lang == "ts":
        for s in re.finditer(r"Platform\s*\.\s*select\s*(?:<[^()]*>)?\s*\(\s*\{", m):
            o = s.end() - 1
            close = T.match_close(o)
            props = _split_props(T, o + 1, close)
            keys = [k for k, _, _ in props]
            for k, a, z in props:
                if k == "default":
                    cond = _all(*[("not", _rn_key(x)) for x in keys if x != "default"]) if len(keys) > 1 else None
                else:
                    cond = _rn_key(k)
                if cond is not None:
                    region(a, z, cond, f"Platform.select {k}", s.start())
    # switch (platform) { case X: ... }  /  Dart switch expressions { X => ..., _ => ... }
    if lang in ("ts", "dart"):
        sw = re.compile(r"\bswitch\s*\(")
        for s in sw.finditer(m):
            o = s.end() - 1
            c = T.match_close(o)
            subj = src[o + 1:c]
            kind = _switch_kind(subj, lang)
            if not kind:
                continue
            b = T.skip_ws(c + 1)
            if b >= len(m) or m[b] != "{":
                continue
            bend = T.match_close(b)
            _switch_regions(T, b + 1, bend, kind, lang, region, s.start())
    return out


def _rn_key(k: str):
    k = k.strip("'\"")
    if k == "native":
        return ("atom", "native", None)
    return ("atom", "platform", k.lower())


def _switch_kind(subject: str, lang: str) -> str | None:
    if lang == "ts" and re.search(_TS_OS, subject):
        return "rn"
    if lang == "ts" and re.search(_NODE_OS, subject):
        return "node"
    if lang == "dart" and re.search(r"\bdefaultTargetPlatform\b|\.platform\b", subject):
        return "target"
    return None


def _case_atom(label: str, kind: str):
    label = label.strip()
    if kind == "target":
        m = re.fullmatch(r"TargetPlatform\s*\.\s*(\w+)", label)
        return ("atom", "target_platform", DART_TARGET.get(m.group(1), m.group(1).lower())) if m else None
    m = re.fullmatch(r"""['"]([^'"]*)['"]""", label)
    if not m:
        return None
    v = m.group(1)
    return ("atom", "platform", NODE_PLATFORMS.get(v, v) if kind == "node" else v.lower())


def _switch_regions(T: _Text, a: int, z: int, kind: str, lang: str, region, at: int):
    m, src = T.m, T.src
    body = m[a:z]
    # statement form: case X: (case Y:) ... ; default:
    labels = [(x.start() + a, x.end() + a, x.group(1)) for x in
              re.finditer(r"\b(?:case\s+((?:[^:]|::)+?)|default)\s*:(?!:)", body) if _depth(T, a, x.start() + a) == 0]
    if labels:
        groups, cur = [], None
        seen = []
        for i, (s, e, lab) in enumerate(labels):
            nxt = labels[i + 1][0] if i + 1 < len(labels) else z
            text_between = m[e:nxt].strip()
            atom = None if lab is None else _case_atom(src[s:e].split("case", 1)[1].rstrip(":").strip(), kind)
            if cur is None:
                cur = {"atoms": [], "default": False, "start": s}
            if lab is None:
                cur["default"] = True
            elif atom is not None:
                cur["atoms"].append(atom)
            else:
                cur["unknown"] = True
            if text_between:
                cur["end"] = nxt
                cur["body"] = e
                groups.append(cur)
                cur = None
        for g in groups:
            if g.get("unknown") and not g["atoms"]:
                continue
            if g["default"]:
                cond = _all(*[("not", x) for x in seen]) if seen else None
            else:
                cond = g["atoms"][0] if len(g["atoms"]) == 1 else ("any", g["atoms"])
            if cond is not None:
                region(g["body"], g["end"], cond, src[g["start"]:g["body"]].strip(), at)
            seen += g["atoms"]
        return
    # expression form (Dart 3): PATTERN => expr, ...
    if lang != "dart":
        return
    props = _split_top(T, a, z)
    seen = []
    for s, e in props:
        seg = m[s:e]
        k = seg.find("=>")
        if k < 0:
            continue
        pat = src[s:s + k].strip()
        if pat == "_" or pat.startswith("_ "):
            cond = _all(*[("not", x) for x in seen]) if seen else None
            atoms = []
        else:
            atoms = [x for x in (_case_atom(p, kind) for p in pat.split("||")) if x is not None]
            if not atoms:
                continue
            cond = atoms[0] if len(atoms) == 1 else ("any", atoms)
        if cond is not None:
            region(s + k + 2, e, cond, pat + " =>", at)
        seen += atoms


def _depth(T: _Text, a: int, i: int) -> int:
    d = 0
    for c in T.m[a:i]:
        if c in "([{":
            d += 1
        elif c in ")]}":
            d -= 1
    return d


def _split_top(T: _Text, a: int, z: int) -> list[tuple[int, int]]:
    out, d, s = [], 0, a
    for i in range(a, z):
        c = T.m[i]
        if c in "([{":
            d += 1
        elif c in ")]}":
            d -= 1
        elif c == "," and d == 0:
            out.append((s, i))
            s = i + 1
    if T.m[s:z].strip():
        out.append((s, z))
    return out


def _split_props(T: _Text, a: int, z: int) -> list[tuple[str, int, int]]:
    """Object literal properties `key: value` -> (key, start of key, end of value)."""
    out = []
    for s, e in _split_top(T, a, z):
        seg = T.src[s:e]
        mm = re.match(r"""\s*(['"]?)(\w+)\1\s*:""", seg)
        if mm:
            out.append((mm.group(2), s + mm.start(2), e))
    return out


def _stmt_end(T: _Text, i: int, lang: str) -> int:
    """End offset (exclusive) of the block or single statement starting at i."""
    m = T.m
    if i < len(m) and m[i] == "{":
        return T.match_close(i) + 1
    d, j = 0, i
    while j < len(m):
        c = m[j]
        if c in "([{":
            d += 1
        elif c in ")]}":
            if d == 0:
                return j
            d -= 1
        elif c == ";" and d == 0:
            return j + 1
        j += 1
    return j


_STOP_WORDS = ("return", "await", "yield", "final", "var", "const", "let")


def _cond_start(T: _Text, i: int) -> int:
    """Start offset of the ternary condition that ends at i (the '?')."""
    m = T.m
    j = i - 1
    while j >= 0:
        c = m[j]
        if c in ")]}":
            # skip a balanced group backwards
            pairs = {")": "(", "]": "[", "}": "{"}
            d, k = 0, j
            while k >= 0:
                if m[k] in ")]}":
                    d += 1
                elif m[k] in "([{":
                    d -= 1
                    if d == 0:
                        break
                k -= 1
            if c == "}":
                return j + 1
            j = k - 1
            continue
        if c in "([{,;?:":
            return j + 1
        if c == "=" and (j == 0 or m[j - 1] not in "=!<>") and (j + 1 >= len(m) or m[j + 1] not in "="):
            return j + 1
        if c == ">" and j > 0 and m[j - 1] == "=":
            return j + 1
        if c == "\n" and m[j + 1:i].strip() == "":
            pass
        if c.isalpha():
            k = j
            while k >= 0 and (m[k].isalnum() or m[k] == "_"):
                k -= 1
            if m[k + 1:j + 1] in _STOP_WORDS:
                return j + 1
            j = k
            continue
        j -= 1
    return 0


def _ternary_colon(T: _Text, i: int) -> int | None:
    m = T.m
    d, pend = 0, 0
    j = i
    while j < len(m):
        c = m[j]
        if c in "([{":
            d += 1
        elif c in ")]}":
            if d == 0:
                return None
            d -= 1
        elif d == 0:
            if c == "?" and not (j + 1 < len(m) and m[j + 1] in ".?:[") and not (j > 0 and m[j - 1] == "?"):
                pend += 1
            elif c == ":":
                if pend == 0:
                    return j
                pend -= 1
            elif c in ";,":
                return None
        j += 1
    return None


def _ternary_end(T: _Text, i: int) -> int:
    m = T.m
    d, pend = 0, 0
    j = i
    while j < len(m):
        c = m[j]
        if c in "([{":
            d += 1
        elif c in ")]}":
            if d == 0:
                return j
            d -= 1
        elif d == 0:
            if c == "?" and not (j + 1 < len(m) and m[j + 1] in ".?:[") and not (j > 0 and m[j - 1] == "?"):
                pend += 1
            elif c == ":":
                if pend == 0:
                    return j
                pend -= 1
            elif c in ";,":
                return j
        j += 1
    return j

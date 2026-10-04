"""`cg roundtrip Type.prop` (#88 phase 2): state that round-trips through a lossy transform.

For each write site of a stored property (WRITES_PROP), report whether the written expression passes through a lossy
call (clamp, min / max, round / floor / ceil, truncating casts, quantize, `fit*` / gamut-map names, the project's
`.cg.yaml` `lossy:` names, functions tagged `@cg-lossy` in their doc comment). The data flow followed is the
statement itself, a local assigned earlier in the same function, and one hop through direct callers when the written
value is a parameter. For each read site (READS_PROP), report whether it seeds UI state: an initializer /
constructor, `onAppear` / `.task`, `remember` / `mutableStateOf`, `useState(initial)` / `useRef`, `mounted` /
`onMounted`, `State(initialValue:)`, or a view constructed on the same line. A read in a file that also draws a range
wider than the lossy bounds (`0...100` against `clamp(v, 0, 10)`) is flagged.

Everything here is a heuristic finding with file:line evidence, read from the indexed source text; it never adds
edges or changes counts. Proving the bug is a non-goal."""
from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path

from . import query as Q
from .core.store import GraphStore

# call names (or name globs) that narrow a value
DEFAULT_LOSSY = ("clamp*", "clamped", "coerceIn", "coerceAtMost", "coerceAtLeast", "min", "max", "fmin", "fmax",
                 "round*", "floor", "ceil", "trunc*", "toInt", "toLong", "toShort", "toByte", "roundToInt",
                 "roundToLong", "Int", "Int8", "Int16", "Int32", "UInt8", "UInt16", "int", "quantiz*", "fit*",
                 "gamut*", "narrow*", "snap*", "limit*", "bound*", "constrain*", "saturate*")
CALL_RE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*(?:<[^<>()]*>)?\s*\(|\.([A-Za-z_]\w*)\s*\(|\.(toInt|roundToInt|toLong)\b")
NUM_RE = re.compile(r"(?<![\w])(-?\d+(?:\.\d+)?)[fFdDL]?(?![\w])")
# a drawn / accepted range: Swift `0...100` / `0..<100`, Kotlin `0f..100f` / `valueRange = 0..100`, JSX / HTML
# `min={0} max={100}`, Python `range(0, 100)` is not a UI range and is skipped
RANGE_RES = (re.compile(r"(-?\d+(?:\.\d+)?)[fFdD]?\s*\.\.(?:\.|<)?\s*(-?\d+(?:\.\d+)?)"),
             re.compile(r"\bmin\s*[:=]\s*\{?\s*(-?\d+(?:\.\d+)?)\s*\}?[\s,]+max\s*[:=]\s*\{?\s*(-?\d+(?:\.\d+)?)"),
             re.compile(r"\b(?:from|minimum|minValue|valueFrom)\s*[:=]\s*(-?\d+(?:\.\d+)?)[^\n]{0,40}?\b(?:to|through|maximum|maxValue|valueTo)\s*[:=]\s*(-?\d+(?:\.\d+)?)"))
SEED_NAMES = {"init", "__init__", "constructor", "<init>", "mounted", "created", "setup", "beforeMount", "onCreate",
              "viewDidLoad", "awakeFromNib", "initState"}
SEED_TEXT = (("onAppear", re.compile(r"\.onAppear\b|\bonAppear\s*\(")), ("task", re.compile(r"\.task\s*[({]")),
             ("remember", re.compile(r"\bremember(?:Saveable)?\s*[({]|\bmutableStateOf\s*\(")),
             ("useState(initial)", re.compile(r"\buse(?:State|Ref|Reducer)\s*\(")),
             ("mounted", re.compile(r"\bonMounted\s*\(|\bmounted\s*\(")),
             ("State(initialValue:)", re.compile(r"\b(?:State|StateObject|Binding)\s*\(\s*(?:initialValue|wrappedValue)\s*:")),
             ("ref(initial)", re.compile(r"\b(?:ref|shallowRef|reactive)\s*\(")),
             ("LaunchedEffect", re.compile(r"\bLaunchedEffect\s*\(")))


def lossy_names(st: GraphStore, root: str | None) -> tuple[list[str], list[str]]:
    """(name globs, sources): built-in defaults, `.cg.yaml` `lossy:`, functions tagged `@cg-lossy`."""
    names, src = list(DEFAULT_LOSSY), ["built-in"]
    if root:
        try:
            from . import config
            extra = config.load(root).get("lossy") or []
        except Exception:     # noqa: BLE001 - a broken config is reported by `cg index`, not here
            extra = []
        if extra:
            names += extra
            src.append(".cg.yaml lossy")
    tagged = [r["name"] for r in st.q("SELECT DISTINCT name FROM nodes WHERE kind IN ('function','method') AND doc LIKE '%@cg-lossy%'")]
    if tagged:
        names += tagged
        src.append("@cg-lossy")
    return names, src


class _Src:
    def __init__(self, root: str | None):
        self.root, self.cache = root, {}

    def lines(self, rel: str | None) -> list[str]:
        if not rel or not self.root:
            return []
        if rel not in self.cache:
            try:
                self.cache[rel] = Path(self.root, rel).read_text("utf-8", "replace").split("\n")
            except OSError:
                self.cache[rel] = []
        return self.cache[rel]

    def stmt(self, rel: str, line: int, max_lines: int = 6) -> str:
        """The statement starting at `line`, continued while brackets are open."""
        ls = self.lines(rel)
        out, depth = [], 0
        for i in range(line - 1, min(len(ls), line - 1 + max_lines)):
            t = ls[i]
            out.append(t)
            depth += sum(t.count(c) for c in "([{") - sum(t.count(c) for c in ")]}")
            if depth <= 0:
                break
        return "\n".join(out)


def _lossy_calls(text: str, globs: list[str]) -> list[str]:
    out = []
    for m in CALL_RE.finditer(text):
        nm = m.group(1) or m.group(2) or m.group(3)
        if nm and any(fnmatch.fnmatchcase(nm, g) for g in globs) and nm not in out:
            out.append(nm)
    return out


def _rhs(stmt: str, prop: str) -> str:
    """The written expression: after `prop =` / `prop +=` (or after the first `=`), else the call arguments."""
    m = re.search(rf"{re.escape(prop)}\s*(?:\[[^\]]*\]\s*)?(?:[-+*/%|&^]|\?\?)?=(?!=)(.*)", stmt, re.S)
    if m:
        return m.group(1)
    m = re.search(r"(?<![=!<>])=(?!=)(.*)", stmt, re.S)
    if m:
        return m.group(1)
    m = re.search(r"\((.*)\)", stmt, re.S)
    return m.group(1) if m else stmt


def _bounds(text: str) -> tuple[float, float] | None:
    nums = [float(x) for x in NUM_RE.findall(text)]
    return (min(nums), max(nums)) if len(nums) >= 2 and min(nums) != max(nums) else None


def _fn_bounds(st: GraphStore, src: _Src, name: str) -> tuple[float, float] | None:
    """Bounds inside a project function `name` (`func clamp(_ v: Double) -> Double { min(max(v, 0), 10) }`)."""
    for r in st.q("SELECT file, line, end_line FROM nodes WHERE kind IN ('function','method') AND name=? LIMIT 3", (name,)):
        ls = src.lines(r["file"])
        body = "\n".join(ls[(r["line"] or 1) - 1:(r["end_line"] or r["line"] or 1)])
        b = _bounds(body.split("{", 1)[-1] if "{" in body else body.split(":", 1)[-1])
        if b:
            return b
    return None


def _ident(rhs: str) -> list[str]:
    return [w for w in dict.fromkeys(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)(?!\s*\()", rhs))
            if w not in ("self", "this", "true", "false", "nil", "null", "None", "True", "False", "it", "new", "let",
                         "var", "val", "const", "await", "try", "return")]


def _write_site(st, src, r, prop, globs) -> dict:
    stmt = src.stmt(r["file"], r["line"])
    rhs = _rhs(stmt, prop)
    site = {"at": f"{r['file']}:{r['line']}", "writer": r["fqn"] or r["src"], "code": stmt.strip()[:160],
            "test": bool(r.get("test")), "lossy": None, "_span": (r["src"], r["line"], r["line"] + stmt.count("\n"))}
    hit = _lossy_calls(rhs, globs)
    if hit:
        site["lossy"] = {"calls": hit, "how": "in the written expression", "at": site["at"], "code": rhs.strip()[:120],
                         "bounds": _bounds(rhs) or _fn_bounds(st, src, hit[0])}
        return site
    node = st.node(r["src"])
    if node is None or not node["line"]:
        return site
    ls = src.lines(r["file"])
    lo, hi = node["line"], min(r["line"], node["end_line"] or r["line"])
    for v in _ident(rhs):
        # a local assigned earlier in the same function
        for i in range(hi - 1, lo - 1, -1):
            t = ls[i - 1] if 0 < i <= len(ls) else ""
            m = re.search(rf"(?:\b(?:let|var|val|const|auto)\s+)?(?<![\w$.]){re.escape(v)}\s*(?::[^=]+)?=(?!=)(.*)", t)
            if m and i != r["line"]:
                h = _lossy_calls(m.group(1), globs)
                if h:
                    site["lossy"] = {"calls": h, "how": f"local `{v}`", "at": f"{r['file']}:{i}", "code": t.strip()[:120],
                                     "bounds": _bounds(m.group(1)) or _fn_bounds(st, src, h[0])}
                    return site
                break
        # a parameter: one hop through the direct callers
        sig = "\n".join(ls[lo - 1:lo + 2])
        sig = sig.split("{", 1)[0] if "{" in sig else sig.split("\n", 1)[0]
        if re.search(rf"(?<![\w$]){re.escape(v)}\b", sig):
            for c in st.q("SELECT src, file, line FROM edges WHERE dst=? AND kind IN ('CALLS','TEST_CALLS') ORDER BY file, line LIMIT 40",
                          (r["src"],)):
                ct = src.stmt(c["file"], c["line"])
                h = _lossy_calls(_call_args(st, ct, r["src"], v), globs)
                if h:
                    site["lossy"] = {"calls": h, "how": f"parameter `{v}`, from caller {c['src']}", "at": f"{c['file']}:{c['line']}",
                                     "code": ct.strip()[:120], "bounds": _bounds(ct) or _fn_bounds(st, src, h[0])}
                    return site
    return site


def _call_args(st, stmt: str, callee: str, param: str) -> str:
    """The argument text a caller statement passes for `param` of `callee` (the labelled argument when there is one),
    so a lossy call elsewhere in the statement (`rooms[Int(i)] = Room(r)`) is not blamed on it. Falls back to the
    whole statement when the call is not found."""
    node = st.node(callee)
    nm = re.split(r"[.#:]", (node["fqn"] or node["name"] or "") if node is not None else callee.split(":", 1)[-1])
    short = nm[-1] if nm[-1] not in ("init", "__init__", "constructor", "<init>") else (nm[-2] if len(nm) > 1 else "")
    m = re.search(rf"(?<![\w$]){re.escape(short)}\s*\(", stmt) if short else None
    if not m:
        return stmt
    depth, i = 1, m.end()
    while i < len(stmt) and depth:
        depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(stmt[i], 0)
        i += 1
    args = stmt[m.end():i - 1]
    lab = re.search(rf"(?<![\w$]){re.escape(param)}\s*[:=](?![:=])\s*", args)
    if lab:
        rest, depth, j = args[lab.end():], 0, 0
        while j < len(rest) and not (rest[j] == "," and depth == 0):
            depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(rest[j], 0)
            j += 1
        return rest[:j]
    return args


def _read_site(st, src, r) -> dict:
    node = st.node(r["src"])
    ls = src.lines(r["file"])
    t = ls[r["line"] - 1] if 0 < r["line"] <= len(ls) else ""
    seed = None
    nm = (node["name"] if node is not None else "") or ""
    if nm.split(".")[-1] in SEED_NAMES:
        seed = f"initializer ({nm.split('.')[-1]})"
    elif node is not None and node["kind"] == "class":
        seed = _class_level_seed(ls, r["line"])
    if seed is None:
        ctx = "\n".join(ls[max(0, r["line"] - 4):r["line"]])
        for label, rx in SEED_TEXT:
            if rx.search(t) or (label in ("onAppear", "task", "mounted", "LaunchedEffect") and rx.search(ctx)):
                seed = label
                break
    if seed is None:
        ins = st.q("SELECT dst FROM edges WHERE src=? AND line=? AND kind IN ('INSTANTIATES','RENDERS') LIMIT 1", (r["src"], r["line"]))
        if ins:
            seed = f"constructs {ins[0]['dst']}"
    return {"at": f"{r['file']}:{r['line']}", "reader": r["fqn"] or r["src"], "code": t.strip()[:160],
            "test": bool(r.get("test")), "seeds": seed, "_src": (r["src"], r["line"])}


PROP_INIT_RE = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:private|public|protected|internal|readonly|override|static|"
                          r"lateinit|final|open)\s+)*(?:val|var|let|const)?\s*[#\w]+\s*(?::[^=()]+)?=(?!=)")
INIT_BLOCK_RE = re.compile(r"^\s*(?:init\s*\{|(?:public\s+|private\s+)?constructor\s*\(|(?:convenience\s+)?init\s*[(?!])")
METHOD_RE = re.compile(r"^\s*(?:@\w+\s+)*(?:(?:private|public|protected|internal|override|static|async|open|final)\s+)*"
                       r"(?:get|set|fun|func|function|def)?\s*[\w$]+\s*(?:<[^>]*>)?\s*\([^)]*\)\s*(?::\s*[^{=]+)?\s*\{\s*$")


def _class_level_seed(ls: list[str], line: int) -> str | None:
    """A read attributed to the class itself: a property initializer or an `init` / constructor block seeds state;
    a getter / method body (whose code some languages also attribute to the class) does not."""
    for i in range(line, max(0, line - 15), -1):
        t = ls[i - 1] if i <= len(ls) else ""
        if INIT_BLOCK_RE.search(t):
            return "initializer (init block)"
        if METHOD_RE.search(t):
            return None
        if PROP_INIT_RE.search(t):
            return "property initializer"
    return None


def _ranges(src, rel: str, b: tuple[float, float]) -> list[dict]:
    out = []
    for i, t in enumerate(src.lines(rel), 1):
        for rx in RANGE_RES:
            for m in rx.finditer(t):
                lo, hi = float(m.group(1)), float(m.group(2))
                if lo < hi and (lo < b[0] or hi > b[1]) and not (lo >= b[1] or hi <= b[0]):
                    out.append({"at": f"{rel}:{i}", "range": [lo, hi], "code": t.strip()[:120]})
    return out


def roundtrip(st: GraphStore, spec: str, include_tests: bool = False) -> dict:
    fids = Q.prop_fields(st, spec)
    root = st.meta().get("root")
    globs, lsrc = lossy_names(st, root)
    src = _Src(root)
    res = {"spec": spec, "fields": fids, "confidence": "heuristic", "lossy_sources": lsrc, "writes": [], "reads": [],
           "findings": []}
    if not fids:
        return res
    prop = re.split(r"[.$:]", spec)[-1]
    for r in Q.prop_access(st, spec, "WRITES_PROP"):
        if include_tests or not r.get("test"):
            res["writes"].append(_write_site(st, src, r, prop, globs))
    for r in Q.prop_access(st, spec, "READS_PROP"):
        if include_tests or not r.get("test"):
            res["reads"].append(_read_site(st, src, r))
    lossy = [w for w in res["writes"] if w["lossy"]]
    seeds = [x for x in res["reads"] if x["seeds"]]
    for w in lossy:
        b = w["lossy"].get("bounds")
        for x in seeds:
            sp, rs = w["_span"], x["_src"]
            if rs[0] == sp[0] and sp[1] <= rs[1] <= sp[2]:
                continue                                # read inside the write statement itself: not a read-back
            if not _read_back(src, w, x, prop):
                continue
            f = {"kind": "lossy round trip", "confidence": "heuristic", "write": w["at"], "lossy": w["lossy"],
                 "read": x["at"], "seeds": x["seeds"], "wider_ranges": []}
            if b:
                f["wider_ranges"] = _ranges(src, x["at"].rsplit(":", 1)[0], tuple(b))[:5]
            res["findings"].append(f)
    for x in res["writes"]:
        x.pop("_span", None)
    for x in res["reads"]:
        x.pop("_src", None)
    return res


def _read_back(src, w, x, prop) -> bool:
    """Is this read a read-back of the stored value rather than the same setup using what it just built? Not when
    the writer reads it a few lines later (`scrollBar = ScrollBarView(...)` then `addView(scrollBar)` in one init),
    when the property is a callback (`this.onWheel = (e) => ...`), or when it is only used as an index
    (`bands[p.band]`)."""
    wf, wl = w["at"].rsplit(":", 1)
    xf, xl = x["at"].rsplit(":", 1)
    if w.get("writer") and w.get("writer") == x.get("reader") and wf == xf and 0 <= int(xl) - int(wl) <= 40:
        return False
    wt = src.lines(wf)[int(wl) - 1] if 0 < int(wl) <= len(src.lines(wf)) else ""
    if re.search(r"=\s*(?:async\s*)?(?:\([^()]*\)|\w+)\s*=>|=\s*(?:async\s+)?function\b|=\s*\{\s*(?:\[[^\]]*\]\s*)?[\w, ]*\bin\b", wt):
        return False
    xt = src.lines(xf)[int(xl) - 1] if 0 < int(xl) <= len(src.lines(xf)) else ""
    if re.search(rf"\[[^\[\]]*\b{re.escape(prop)}\s*\]", xt) and not re.search(rf"(?<![\[\w.]){re.escape(prop)}\b(?!\s*\])", xt):
        return False
    return True


def render(res: dict) -> str:
    if not res["fields"]:
        return f"no stored property {res['spec']!r} in the graph (try `cg search {res['spec'].split('.')[-1]}`)."
    out = [f"roundtrip {res['spec']} ({', '.join(res['fields'])}) — heuristic; lossy names: {', '.join(res['lossy_sources'])}"]
    out.append(f"writes ({len(res['writes'])}):")
    for w in res["writes"]:
        lz = w["lossy"]
        out.append(f"  {w['writer']}  @{w['at']}" + (f"  LOSSY {'/'.join(lz['calls'])} via {lz['how']} @{lz['at']}"
                                                     + (f" bounds {_fmt(lz['bounds'])}" if lz.get("bounds") else "")
                                                     if lz else "  not narrowed"))
    out.append(f"reads ({len(res['reads'])}):")
    for x in res["reads"]:
        out.append(f"  {x['reader']}  @{x['at']}" + (f"  SEEDS {x['seeds']}" if x["seeds"] else ""))
    if res["findings"]:
        out.append(f"findings ({len(res['findings'])}, heuristic):")
        for f in res["findings"]:
            out.append(f"  write @{f['write']} -> {'/'.join(f['lossy']['calls'])} @{f['lossy']['at']} -> {res['spec']} "
                       f"-> read @{f['read']} -> seeds {f['seeds']}")
            for g in f["wider_ranges"]:
                out.append(f"      un-narrowed range {_fmt(g['range'])} @{g['at']}: {g['code']}")
    else:
        out.append("no lossy round trip found (heuristic: a lossy write and a read that seeds UI state)")
    return "\n".join(out)


def _fmt(b) -> str:
    return "…".join(f"{x:g}" for x in b)


def to_json(res: dict) -> str:
    return json.dumps(res, indent=1, default=str)

"""`cg lint async-state` (#88 phase 3), rule `stale-async-result`.

Flags a write of stored / UI state (a WRITES_PROP edge) inside an async block — Swift `Task { }` / `Task.detached`,
Kotlin `launch { }` / `async { }`, a React `useEffect` callback, a JS / TS `async` function or `.then(...)`, Python
`async def` / `asyncio.create_task` — that comes after an `await` (Kotlin: `.await()`, `withContext`, `delay`,
`.first()`), with no cancellation check (`Task.isCancelled`, `checkCancellation`, `isActive`, `ensureActive`, a
`cancelled` / `ignore` flag, `signal.aborted`) and no comparison against a token, ID or generation captured before the
await between the await and the write. The write must use the awaited result, the awaited request must depend on an
input (a parameter, prop, state or loop variable: a fixed `client.post("/auth.config")` cannot be overtaken by a
newer request with other inputs), and the block must not be wrapped in a single-flight helper (`bundleAsync`,
`dedupe`, `debounce`, `throttle`, `once`). A result that lands after the user moved on overwrites newer state.

Findings are heuristic, read from the indexed source text with file:line evidence; they add no edges and change no
counts.

Rule `two-writers`: the same state is written with a real value both by lifecycle code (an initializer / `init`
block, `onAppear`, `.task` before its first await, `useEffect`, `onMounted`, `LaunchedEffect`, `viewDidLoad`) and
by async code after an await or by a completion / subscription callback (`.sink`, `.then`, `.collect`,
`completion: {`). Defaults and flag resets (`= []`, `setLoading(true)`, `setError(null)`) and in-place mutations are
not competing values and are skipped.

Not implemented yet: incomplete cache key, echo suppression."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .core.store import GraphStore

OPEN_RES = (
    ("async func", re.compile(r"\)\s*async\b(?:\s+throws)?(?:\s*->\s*[^{]+)?\s*\{")),
    ("Task", re.compile(r"\bTask(?:\.detached)?\s*(?:\([^)]*\))?\s*\{")),
    ("launch", re.compile(r"(?<![)\s])\s*\b(?:launch|async)\s*(?:\([^)]*\))?\s*\{|^\s*(?:launch|async)\s*(?:\([^)]*\))?\s*\{|[.=(]\s*(?:launch|async)\s*(?:\([^)]*\))?\s*\{")),
    ("useEffect", re.compile(r"\buse(?:Layout)?Effect\s*\(")),
    ("async function", re.compile(r"\basync\s+(?:function\b|\([^)]*\)\s*=>|\w+\s*=>|def\s+\w+|[\w$]+\s*\([^)]*\)\s*\{)")),
    ("then", re.compile(r"\.then\s*\(")),
    ("create_task", re.compile(r"\b(?:asyncio\.)?(?:create_task|ensure_future)\s*\(")),
)
AWAIT_RE = re.compile(r"\bawait\b|\.await\(\)|\bwithContext\s*\(|\bdelay\s*\(|\.first\s*\(\s*\)|\.single\s*\(\s*\)|\.then\s*\(")
GUARD_RE = re.compile(r"\bisCancelled\b|checkCancellation|\bisActive\b|ensureActive|\bcancel+ed\b|\bignore\b|\baborted\b|"
                      r"\bstale\b|\bisMounted\b|\bmounted\b|\bgeneration\b|\btoken\b|\blatest\w*\b|\brequestId\b|"
                      r"\bisCurrent\w*\b", re.I)
CMP_RE = re.compile(r"(?:===?|!==?)")
DECL_RE = re.compile(r"\b(?:let|var|val|const)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=|^\s*([a-z_]\w*)\s*=(?!=)")
UI_HINT = ("wrapper", "hook")
# the async block is wrapped in a single-flight / dedupe / rate limiter: concurrent runs cannot overtake each other
SINGLE_FLIGHT_RE = re.compile(r"\b(?:bundle\w*|dedup\w*|single[Ff]light\w*|debounce\w*|throttle\w*|exhaust\w*|once)\s*\(")
KEYWORDS = frozenset("""await try async let var val const self this it nil null None true false True False new return
in of is as await_ undefined void typeof""".split())


def _input_dependent(text: str) -> bool:
    """Does the awaited request depend on an input (a parameter, prop, state or loop variable) rather than being a
    fixed call (`client.post("/auth.config")`, `requestPhotoAccessIfNeeded()`)? Only then can a newer request
    overtake it with different inputs."""
    t = re.sub(r"""(['"`])(?:\\.|(?!\1).)*\1""", "''", text)       # string literals
    t = re.sub(r"(?<![:\w])([A-Za-z_$][\w$]*)\s*:(?!:)", " ", t)      # labels / object keys (`url:`, `id:`)
    t = re.sub(r"\b(?:this|self)\s*\.\s*", " SELFREF_", t)           # `this.x` / `self.x`: the instance's state
    t = re.sub(r"\.\s*[A-Za-z_$][\w$]*", " ", t)                    # member names
    t = re.sub(r"(?<![\w$])[A-Za-z_$][\w$]*\s*(?:<[^<>]*>)?\s*\(", " (", t)  # call names
    ids = re.findall(r"(?<![\w$])[A-Za-z_$][\w$]*", t)
    return any(i.startswith("SELFREF_") or (i not in KEYWORDS and not i[0].isupper()) for i in ids)
TEST_PATH_RE = re.compile(r"(?:^|/)(?:tests?|__tests__|spec|testing|fixtures?|factories|Tests|UITests)/|"
                          r"[._-](?:test|spec)s?\.\w+:|Tests?\.swift:|Test\.kt:|(?:^|/)test_\w+\.py:")


def _lines(root, rel, cache):
    if rel not in cache:
        try:
            cache[rel] = Path(root, rel).read_text("utf-8", "replace").split("\n")
        except OSError:
            cache[rel] = []
    return cache[rel]


def _opener(ls: list[str], line: int, lo: int):
    """The innermost async opener around `line` (1-based), not above `lo`: (kind, opener line) when `line` is still
    inside the block that opens there (brackets still open), else None. Python blocks are indentation-based."""
    for o in range(line, max(lo, line - 60, 1) - 1, -1):
        t = ls[o - 1] if o <= len(ls) else ""
        for kind, rx in OPEN_RES:
            m = rx.search(t)
            if not m:
                continue
            if re.search(r"\basync\s+def\b", t):      # Python: an `async def` whose body indents past it
                ind = len(t) - len(t.lstrip())
                body = ls[o:line]
                if all((not x.strip()) or (len(x) - len(x.lstrip())) > ind for x in body):
                    return ("async def", o)
                continue
            depth = 0
            for i in range(o, line + 1):
                x = ls[i - 1]
                if i == o:
                    x = x[m.start():]
                depth += sum(x.count(c) for c in "({[") - sum(x.count(c) for c in ")}]")
                if depth <= 0 and i < line:
                    break
            else:
                if depth > 0 or (o == line):
                    return (kind, o)
    return None


def stale_async(st: GraphStore, include_tests: bool = False, limit: int = 500) -> list[dict]:
    root = st.meta().get("root")
    cache: dict = {}
    fields = {r["id"]: json.loads(r["attrs"] or "{}") for r in st.q("SELECT id, attrs FROM nodes WHERE kind IN ('field','property')")}
    rows = st.q("""SELECT e.src, e.dst, e.file, e.line, e.attrs, n.line AS fl, n.end_line AS fe, n.fqn
                   FROM edges e JOIN nodes n ON n.id = e.src
                   WHERE e.kind = 'WRITES_PROP' ORDER BY e.file, e.line""")
    out, seen = [], set()
    for r in rows:
        if not r["file"] or not root:
            continue
        ls = _lines(root, r["file"], cache)
        if not ls or r["line"] > len(ls):
            continue
        op = _opener(ls, r["line"], r["fl"] or 1)
        if op is None:
            continue
        kind, o = op
        body = ls[o - 1:r["line"] - 1]
        aw = next((o + i for i, t in enumerate(body) if AWAIT_RE.search(t if i else t[t.find("{") + 1:] if "{" in t else t)), None)
        if aw is None:
            continue
        before = "\n".join(ls[o - 1:aw - 1]) + "\n" + ls[aw - 1].split("await")[0]
        after = "\n".join(ls[aw - 1:r["line"]])
        captured = {m.group(1) or m.group(2) for m in DECL_RE.finditer(before)} - {None}
        if GUARD_RE.search(after) or SINGLE_FLIGHT_RE.search(ls[o - 1]):
            continue
        aw_line = ls[aw - 1]
        k = max(aw_line.find("await"), 0)
        stmt, depth = [], 0
        for i in range(aw - 1, min(len(ls), aw + 5)):
            x = ls[i][k:] if i == aw - 1 else ls[i]
            stmt.append(x)
            depth += sum(x.count(c) for c in "({[") - sum(x.count(c) for c in ")}]")
            if depth <= 0:
                break
        if not _input_dependent(re.sub(r"^\s*await\b", "", "\n".join(stmt))):
            continue
        # the written value must come from the awaited result: a name bound on the await line (or derived from one
        # after it), or the await itself on the write line; a loading flag reset after the await is not a stale result
        res_vars = set()
        for t in ls[aw - 1:r["line"] - 1]:
            m = DECL_RE.search(t) or re.search(r"^\s*(?:self\.)?([A-Za-z_$][\w$]*)\s*=(?!=)", t)
            nm = m and (m.group(1) or (m.group(2) if m.lastindex and m.lastindex >= 2 else None))
            if nm and (AWAIT_RE.search(t) or any(re.search(rf"(?<![\w$.]){re.escape(v)}\b", t.split("=", 1)[-1]) for v in res_vars)):
                res_vars.add(nm)
        wl = ls[r["line"] - 1]
        if r["line"] - 1 == aw - 1:
            pass                                          # `results = await fetch()` on one line
        elif not any(re.search(rf"(?<![\w$.]){re.escape(v)}\b", wl.split("=", 1)[-1] if "=" in wl else wl) for v in res_vars):
            continue
        if any(CMP_RE.search(t) and any(re.search(rf"(?<![\w$.]){re.escape(c)}\b", t) for c in captured)
               for t in after.split("\n")):
            continue
        key = (r["src"], r["dst"], r["line"])
        if key in seen:
            continue
        seen.add(key)
        fa = fields.get(r["dst"], {})
        ea = json.loads(r["attrs"] or "{}")
        ui = bool(any(k in fa for k in UI_HINT) or ea.get("via") in ("setter", "value"))
        if not ui and ea.get("receiver") not in (None, "this", "self", "$this"):
            continue          # `doc.x = …` on an object the function just fetched: not shared or UI state
        out.append({"rule": "stale-async-result", "confidence": "heuristic", "writer": r["fqn"] or r["src"],
                    "state": r["dst"], "ui_state": ui, "async": kind, "async_at": f"{r['file']}:{o}",
                    "await_at": f"{r['file']}:{aw}", "write_at": f"{r['file']}:{r['line']}",
                    "await_code": ls[aw - 1].strip()[:120], "write_code": ls[r["line"] - 1].strip()[:120]})
        if len(out) >= limit:
            break
    if not include_tests:
        out = [f for f in out if not TEST_PATH_RE.search(f["write_at"])]
    return out


LIFECYCLE_RE = re.compile(r"\.onAppear\b|\bonAppear\s*\(|\.task\s*[({]|\bonMounted\s*\(|\bmounted\s*\(|\bLaunchedEffect\s*\(|"
                          r"\buse(?:Layout)?Effect\s*\(|\bviewDidLoad\b|\bviewWillAppear\b|\bonCreate\b|\bonStart\b|\binitState\b")
CALLBACK_RE = re.compile(r"\.sink\s*\{|\.onReceive\s*\(|\.subscribe\s*[({]|\.collect(?:Latest)?\s*\{|\.observe\w*\s*\(|"
                         r"\bcompletion(?:Handler)?\s*:\s*\{|\{\s*\[?\s*(?:weak|unowned)\s+self\s*\]?|\.then\s*\(|\.on\s*\(\s*['\"]")
LITERAL_RHS_RE = re.compile(r"(?<![=!<>])=\s*(?:\[\s*\]|\[:\]|\{\s*\}|nil|null|None|undefined|0(?:\.0+)?[fFdDL]?|false|False|true|True|"
                            r"''|\"\"|emptyList\(\)|emptyMap\(\)|mutableListOf\(\)|\.init\(\)|\w+\(\)|\.\w+)\s*;?\s*$")
LITERAL_ARG_RE = re.compile(r"\(\s*(?:nil|null|None|undefined|true|false|True|False|-?\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"|\[\s*\]|\{\s*\})\s*\)\s*;?\s*$")
INIT_NAMES = {"init", "__init__", "constructor", "<init>", "viewDidLoad", "onCreate", "initState", "mounted", "created", "setup"}


def _rhs(t: str) -> str | None:
    """Right-hand side of an assignment (comments dropped), or the argument of a `setX(...)` call."""
    t = re.sub(r"\s(?://|#).*$", "", t).strip().rstrip(";")
    m = re.search(r"(?<![=!<>])=(?!=)\s*(.+)$", t) or re.search(r"\bset[A-Z]\w*\s*\((.*)\)\s*$", t)
    return m.group(1).strip() if m else None


def _lifecycle(st, ls, r, node) -> str | None:
    """Is this write lifecycle code: an initializer with a real (non-default) value, or code directly inside
    onAppear / .task (before any await) / onMounted / LaunchedEffect / useEffect / viewDidLoad?"""
    t = ls[r["line"] - 1]
    if AWAIT_RE.search(t):
        return None
    nm = ((node["name"] if node is not None else "") or "").split(".")[-1]
    if nm in INIT_NAMES or (node is not None and node["kind"] == "class"):
        # constructor injection (`self.x = x`, `_x = State(initialValue: x)`, `this.y = AppState.currentState`)
        # seeds the state; only a computed / loaded value competes with a later async one
        rhs = _rhs(t)
        if rhs is None or not re.search(r"\w\s*\(", rhs) or re.match(r"(?:State|Published|StateObject|ObservedObject|"
                                                                        r"Binding)\s*\(\s*(?:initialValue|wrappedValue)\s*:", rhs):
            return None
        return f"initializer ({nm or 'class'})"
    lo = max(1, r["line"] - 6)
    for i in range(r["line"], lo - 1, -1):
        x = ls[i - 1]
        if i < r["line"] and AWAIT_RE.search(x):
            return None
        m = LIFECYCLE_RE.search(x)
        if m:
            return m.group(0).strip(" .({")
    return None


def _async_callback(ls, r, lo) -> str | None:
    """Is this write inside async code after an await, or inside a completion / subscription callback?"""
    op = _opener(ls, r["line"], lo)
    if op is not None:
        body = ls[op[1] - 1:r["line"] - 1]
        if any(AWAIT_RE.search(x) for x in body[1:]) or AWAIT_RE.search(body[0][body[0].find("{") + 1:] if body else ""):
            return f"after await in {op[0]}"
    for i in range(r["line"] - 1, max(lo, r["line"] - 8) - 1, -1):
        m = CALLBACK_RE.search(ls[i - 1]) if i >= 1 else None
        if m:
            return f"callback {m.group(0).strip()[:24]}"
    return None


def two_writers(st: GraphStore, include_tests: bool = False) -> list[dict]:
    root = st.meta().get("root")
    cache: dict = {}
    rows = st.q("""SELECT e.src, e.dst, e.file, e.line, e.attrs, n.line AS fl FROM edges e JOIN nodes n ON n.id = e.src
                   WHERE e.kind = 'WRITES_PROP' ORDER BY e.dst, e.file, e.line""")
    by: dict = {}
    for r in rows:
        if not r["file"] or not root or (not include_tests and TEST_PATH_RE.search(f"{r['file']}:")):
            continue
        ls = _lines(root, r["file"], cache)
        if not ls or r["line"] > len(ls):
            continue
        # a default / flag reset (`= []`, `setLoading(true)`, `setError(null)`) or an in-place mutation is not a
        # competing value: only real values written from both sides can make the UI jump
        t = re.sub(r"\s//.*$", "", ls[r["line"] - 1])
        if (LITERAL_RHS_RE.search(t) or LITERAL_ARG_RE.search(t) or re.search(r"&\s*[\w.]+|\.store\s*\(\s*in\s*:", t)
                or json.loads(r["attrs"] or "{}").get("via") in ("mutating", "item")):
            continue
        node = st.node(r["src"])
        d = by.setdefault(r["dst"], {"life": [], "async": []})
        lc = _lifecycle(st, ls, r, node)
        if lc:
            d["life"].append({"at": f"{r['file']}:{r['line']}", "how": lc, "code": ls[r["line"] - 1].strip()[:110]})
            continue
        ac = _async_callback(ls, r, r["fl"] or 1)
        if ac:
            d["async"].append({"at": f"{r['file']}:{r['line']}", "how": ac, "code": ls[r["line"] - 1].strip()[:110]})
    out = []
    for fid, d in by.items():
        # the same expression from both sides (`selectedID = ids.first` on appear and on change) is a refresh, not a race
        life_rhs = {_rhs(w["code"]) for w in d["life"]}
        short = fid.rsplit(".", 1)[-1].rsplit("#", 1)[-1]
        # ... and an update derived from the state itself (`presets = presets.filter(...)`) does not compete either
        d["async"] = [w for w in d["async"] if _rhs(w["code"]) not in life_rhs
                      and not re.search(rf"(?<![\w$]){re.escape(short)}\b", _rhs(w["code"]) or "")]
        if d["life"] and d["async"]:
            out.append({"rule": "two-writers", "confidence": "heuristic", "state": fid,
                        "lifecycle_writes": d["life"][:5], "async_writes": d["async"][:5]})
    return out


# ---- rule incomplete-cache-key
CACHE_NAME = r"[\w$.]*(?:[cC]ache|[mM]emo\w*|lru\w*|LRU\w*)[\w$]*"
STORE_RES = (
    re.compile(rf"(?P<c>{CACHE_NAME})\s*\.\s*(?:set|put|store|setItem|setObject|setValue)\s*\((?P<args>.*)\)"),
    re.compile(rf"(?P<c>{CACHE_NAME})\s*\[(?P<key>[^\]]+)\]\s*=(?!=)\s*(?P<val>.+)"),
)
WORD_RE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)")
SKIP_IDS = KEYWORDS | {"let", "var", "val", "const", "if", "else", "for", "while", "guard", "return", "self", "this",
                       "String", "Int", "Double", "Float", "Bool", "JSON", "Math", "Object", "Array", "str", "int", "len",
                       "f", "fun", "func", "def", "await", "try", "async"}


PAYLOAD_RE = re.compile(r"(?:data|response|resp|res|result|value|payload|body|json|item|entry|element|model|obj|object)")


def _split_args(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur); cur = ""
            continue
        cur += ch
    if cur.strip():
        out.append(cur)
    return out


INTERP_RE = re.compile(r"\$\{([^}]*)\}|\\\(([^)]*)\)|\$([A-Za-z_]\w*)")


def _strip_strings(expr: str) -> str:
    """Drop string literals but keep what they interpolate (`${id}`, `\\(id)`, `$id`, f-string `{id}`)."""
    def keep(m):
        body = m.group(0)
        inner = [g for x in INTERP_RE.finditer(body) for g in x.groups()[:3] if g]
        if body[:1] == "f":
            inner = re.findall(r"\{([^{}]*)\}", body)
        return " (" + ", ".join(inner) + ") " if inner else "''"
    return re.sub(r"""f?(['"`])(?:\\.|(?!\1).)*\1""", keep, expr)


def _idents(expr: str) -> set[str]:
    e = _strip_strings(expr)
    e = re.sub(r"(?<![:\w])([A-Za-z_$][\w$]*)\s*:(?!:)", " ", e)            # labels / keys
    e = re.sub(r"(?:\$this\s*->|\b(?:this|self)\s*\.)\s*([A-Za-z_$][\w$]*)", r" self_\1 ", e)  # instance state: `self_x`
    e = e.replace(".$", ". $")
    e = re.sub(r"(?<![\w$])[A-Za-z_$][\w$]*\s*(?:<[^<>]*>)?\s*\(", " (", e)    # call names
    e = re.sub(r"(?:\.|->|::)\s*[A-Za-z_][\w$]*", " ", e)                     # other member names (`.$x` is PHP concat)
    return {w for w in WORD_RE.findall(e) if w not in SKIP_IDS and not w[0].isupper()}


def _expand(ids: set[str], assigns: dict, depth: int = 0) -> set[str]:
    """Identifiers a value depends on, through locals assigned earlier in the function."""
    out = set()
    for i in ids:
        if i in assigns and depth < 3:
            out |= _expand(assigns[i], assigns, depth + 1) or {i}
        else:
            out.add(i)
    return out


def incomplete_cache_key(st: GraphStore, include_tests: bool = False) -> list[dict]:
    """A cache store whose key leaves out a parameter or instance field that the cached value's computation uses
    (within one function): `cache[userId] = render(userId, theme)` serves one theme's render for another."""
    root = st.meta().get("root")
    cache: dict = {}
    out = []
    fns = st.q("""SELECT id, fqn, file, line, end_line FROM nodes WHERE kind IN ('function','method')
                  AND file IS NOT NULL AND end_line > line ORDER BY file, line""")
    for fn in fns:
        if not root or (not include_tests and TEST_PATH_RE.search(f"{fn['file']}:")):
            continue
        ls = _lines(root, fn["file"], cache)
        body = ls[fn["line"] - 1:fn["end_line"]]
        if not any(re.search(CACHE_NAME, x) for x in body):
            continue
        sig = "\n".join(body[:3])
        sig = sig[sig.find("("):] if "(" in sig else ""
        params = _idents(_split_args(sig[1:]) and ",".join(re.sub(r"[:=].*", "", a, flags=re.S) for a in _split_args(sig[1:])) or "")
        assigns: dict = {}
        for off, t in enumerate(body):
            m = re.search(r"\b(?:let|var|val|const)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=(?!=)(.*)|^\s*([a-z_$][\w$]*)\s*=(?!=)(.*)", t)
            if m:
                assigns[m.group(1) or m.group(3)] = _idents(m.group(2) or m.group(4) or "")
            for rx in STORE_RES:
                s = rx.search(t)
                if not s or not re.fullmatch(CACHE_NAME, s.group("c").split(".")[-1] or ""):
                    continue
                if "args" in s.groupdict() and s.group("args") is not None:
                    a = _split_args(s.group("args"))
                    if len(a) < 2:
                        continue
                    if re.search(r"forKey\s*:", s.group("args")):          # setObject(v, forKey: k)
                        key = next(x for x in a if "forKey" in x)
                        val = next(x for x in a if "forKey" not in x)
                    else:
                        key, val = a[0], a[1]
                else:
                    key, val = s.group("key"), s.group("val")
                vb = val.strip().rstrip(";")
                if re.fullmatch(r"[A-Za-z_$][\w$]*", vb) and vb not in assigns:
                    continue                    # `put(key, value)` passes a value through; nothing is computed here
                kids = _expand(_idents(key), assigns)
                vids = _expand(_idents(val), assigns) - {vb}
                setter = re.match(r"(?:set|put|insert|store|save|cache|add|update|write|remember|record)(?![a-z])",
                                  (fn["fqn"] or "").split(".")[-1].split("(")[0])
                inputs = {i for i in vids if (i in params and not setter and not PAYLOAD_RE.fullmatch(i.lstrip("$")))
                          or i.startswith("self_")}
                missing = sorted(i for i in inputs - kids if i.replace("self_", "") not in {k.replace("self_", "") for k in kids})
                src = "\n".join(body[:off + 1])
                missing = [m for m in missing if not any(re.search(          # `xs[i]` / `xs.getOrNull(i)` keyed by i
                    rf"{re.escape(m)}\s*(?:\[\s*{re.escape(k)}\s*\]|\??\.\s*(?:get\w*|elementAt\w*|at|item)\s*\(\s*{re.escape(k)}\b)",
                    src) for k in kids)]
                kin = {i for i in kids if i in params or i.startswith("self_")}
                if missing and (kin or kids):
                    ln = fn["line"] + off
                    out.append({"rule": "incomplete-cache-key", "confidence": "heuristic", "function": fn["fqn"] or fn["id"],
                                "at": f"{fn['file']}:{ln}", "cache": s.group("c")[-40:], "key": key.strip()[:80],
                                "key_uses": sorted(kids)[:8], "value_uses": sorted(inputs)[:8],
                                "missing": [m.replace("self_", "self.") for m in missing], "code": t.strip()[:120]})
    return out


# ---- rule echo-suppression
GUARD_FIELD_RE = re.compile(r"(?:last(?:Written|Sent|Saved|Synced|Applied|Pushed|Emitted|Published|Local)\w*|"
                            r"isUpdating(?:From|Programmatic|Internal|Selection|Text|Value)\w*|is(?:Applying|Programmatic|Syncing|Internal|Setting|Restoring)\w*|"
                            r"(?:suppress|ignoreNext|skipNext|muteNext|ignoring|suppressing)\w*)", re.I)
EARLY_RE = re.compile(r"\b(?:return|continue|break)\b|\bguard\b.*\belse\b")


def _owner(fid: str) -> str:
    return fid.rsplit(".", 1)[0]


def echo_suppression(st: GraphStore, include_tests: bool = False, patterns: list | None = None) -> list[dict]:
    """Echo suppression: a guard field (`isApplyingRemote`, `lastSentText`, `suppressNextChange`) is set around
    programmatic writes of some state so the change observer, which checks the guard and returns early, does not
    echo them back. Reports state of that pattern that is also written from an async path / callback *without*
    setting the guard: that write reaches the observer and is sent back (a loop or a clobbered edit)."""
    root = st.meta().get("root")
    cache: dict = {}
    guards = [r["id"] for r in st.q("SELECT id, name FROM nodes WHERE kind = 'field'") if GUARD_FIELD_RE.fullmatch((r["name"] or "").rsplit(".", 1)[-1].replace("_", ""))]
    out = []

    def keep(r):
        return r["file"] and root and (include_tests or not TEST_PATH_RE.search(f"{r['file']}:"))

    for g in guards:
        name = g.rsplit(".", 1)[-1]
        sites = []
        for r in st.q("SELECT src, file, line FROM edges WHERE kind = 'READS_PROP' AND dst = ?", (g,)):
            if not keep(r):
                continue
            ls = _lines(root, r["file"], cache)
            if not ls or r["line"] > len(ls):
                continue
            t = ls[r["line"] - 1]
            if re.search(r"\b(?:if|guard|unless|when)\b", t) and EARLY_RE.search("\n".join(ls[r["line"] - 1:r["line"] + 2])):
                sites.append({"at": f"{r['file']}:{r['line']}", "fn": r["src"], "code": t.strip()[:110]})
        if not sites:
            if patterns is not None:
                patterns.append((g, None, 0, 0, 0))
            continue
        gw = []
        for r in st.q("SELECT src, file, line FROM edges WHERE kind = 'WRITES_PROP' AND dst = ?", (g,)):
            ls = _lines(root, r["file"], cache) if keep(r) else None
            # the guard's default / reset (`= false`, `= None`) does not suppress anything; setting it (or recording
            # the value about to be sent) does
            if ls and r["line"] <= len(ls) and not re.search(r"=\s*(?:false|False|nil|None|null|undefined|0)\s*;?\s*$",
                                                                  ls[r["line"] - 1]):
                gw.append(r)
        setters = {r["src"] for r in gw}
        observers = {s["fn"] for s in sites}
        targets: dict = {}
        for r in gw:
            for w in st.q("""SELECT dst, line FROM edges WHERE kind = 'WRITES_PROP' AND src = ? AND file = ?
                             AND line BETWEEN ? AND ?""", (r["src"], r["file"], r["line"] - 4, r["line"] + 4)):
                if w["dst"] != g and _owner(w["dst"]) == _owner(g) and not GUARD_FIELD_RE.fullmatch(w["dst"].rsplit(".", 1)[-1].replace("_", "")):
                    targets.setdefault(w["dst"], []).append(f"{r['file']}:{w['line']}")
        for t, sup in targets.items():
            bad = []
            for r in st.q("SELECT e.src, e.file, e.line, n.line AS fl FROM edges e JOIN nodes n ON n.id = e.src "
                          "WHERE e.kind = 'WRITES_PROP' AND e.dst = ?", (t,)):
                if not keep(r) or r["src"] in setters or r["src"] in observers:
                    continue
                ls = _lines(root, r["file"], cache)
                if not ls or r["line"] > len(ls):
                    continue
                how = _async_callback(ls, r, r["fl"] or 1)
                if how:
                    bad.append({"at": f"{r['file']}:{r['line']}", "how": how, "code": ls[r["line"] - 1].strip()[:110]})
            if patterns is not None:
                patterns.append((g, t, len(sites), len(sup), len(bad)))
            if bad:
                out.append({"rule": "echo-suppression", "confidence": "heuristic", "guard": g, "state": t,
                            "guard_sites": sites[:3], "suppressed_writes": sorted(set(sup))[:5], "unsuppressed_writes": bad[:5]})
    return out


RULES = {"stale-async-result": stale_async, "two-writers": two_writers, "incomplete-cache-key": incomplete_cache_key,
         "echo-suppression": echo_suppression}


def lint(st: GraphStore, include_tests: bool = False, rules: list[str] | None = None) -> dict:
    run = [r for r in RULES if rules is None or r in rules]
    unknown = sorted(set(rules or []) - set(RULES))
    if unknown:
        raise ValueError(f"unknown rule(s) {', '.join(unknown)}; known: {', '.join(RULES)}")
    f = [x for r in run for x in RULES[r](st, include_tests)]
    return {"lint": "async-state", "confidence": "heuristic", "rules": run, "not_implemented": [], "findings": f}


def render(res: dict) -> str:
    fs = res["findings"]
    nyi = f" (not yet: {', '.join(res['not_implemented'])})" if res.get("not_implemented") else ""
    out = [f"lint async-state — heuristic; rules: {', '.join(res['rules'])}{nyi}; {len(fs)} findings"]
    for f in fs:
        if f["rule"] == "echo-suppression":
            out.append(f"  [echo-suppression] {f['state']}: written with guard {f['guard'].rsplit('.', 1)[-1]} set at "
                       f"{', '.join(f['suppressed_writes'][:2])} (checked at {f['guard_sites'][0]['at']}), but not at:")
            for w in f["unsuppressed_writes"]:
                out.append(f"      {w['at']} ({w['how']}): {w['code']}")
            continue
        if f["rule"] == "incomplete-cache-key":
            out.append(f"  [incomplete-cache-key] {f['function']} @{f['at']}: {f['cache']} key `{f['key']}` leaves out "
                       f"{', '.join(f['missing'])} (the value uses {', '.join(f['value_uses'])})")
            out.append(f"      {f['code']}")
            continue
        if f["rule"] == "two-writers":
            out.append(f"  [two-writers] {f['state']}: written by lifecycle code and by an async callback")
            for w in f["lifecycle_writes"]:
                out.append(f"      lifecycle {w['how']} @{w['at']}: {w['code']}")
            for w in f["async_writes"]:
                out.append(f"      async {w['how']} @{w['at']}: {w['code']}")
            continue
        out.append(f"  [stale-async-result{' ui' if f['ui_state'] else ''}] {f['writer']} writes {f['state']} @{f['write_at']} "
                   f"after await @{f['await_at']} in {f['async']} @{f['async_at']}; no cancellation / token check")
        out.append(f"      await: {f['await_code']}")
        out.append(f"      write: {f['write_code']}")
    return "\n".join(out)

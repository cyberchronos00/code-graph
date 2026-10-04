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


def _lifecycle(st, ls, r, node) -> str | None:
    """Is this write lifecycle code: an initializer with a real (non-default) value, or code directly inside
    onAppear / .task (before any await) / onMounted / LaunchedEffect / useEffect / viewDidLoad?"""
    t = ls[r["line"] - 1]
    if AWAIT_RE.search(t):
        return None
    nm = ((node["name"] if node is not None else "") or "").split(".")[-1]
    if nm in INIT_NAMES or (node is not None and node["kind"] == "class"):
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
        t = ls[r["line"] - 1]
        if LITERAL_RHS_RE.search(t) or LITERAL_ARG_RE.search(t) or json.loads(r["attrs"] or "{}").get("via") in ("mutating", "item"):
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
        if d["life"] and d["async"]:
            out.append({"rule": "two-writers", "confidence": "heuristic", "state": fid,
                        "lifecycle_writes": d["life"][:5], "async_writes": d["async"][:5]})
    return out


def lint(st: GraphStore, include_tests: bool = False) -> dict:
    f = stale_async(st, include_tests) + two_writers(st, include_tests)
    return {"lint": "async-state", "confidence": "heuristic", "rules": ["stale-async-result", "two-writers"],
            "not_implemented": ["incomplete-cache-key", "echo-suppression"], "findings": f}


def render(res: dict) -> str:
    fs = res["findings"]
    out = [f"lint async-state — heuristic; rules: {', '.join(res['rules'])} "
           f"(not yet: {', '.join(res['not_implemented'])}); {len(fs)} findings"]
    for f in fs:
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

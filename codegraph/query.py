"""Graph queries. Traversals are recursive CTEs over the SQLite edge table."""
from __future__ import annotations

import contextvars
import json
import os
import re
import time
from collections import defaultdict

from .core.model import (CONFIDENCE_RANK, DEV_ENTRY_KINDS, LIBRARY_ENTRY_KINDS, OPERATOR_ENTRY_KINDS, PROPAGATING,
                         RUNTIME_ENTRY_KINDS, UI_ENTRY_KINDS)
from .core.store import GraphStore

CALL_LIKE = ["CALLS", "IMPLEMENTED_BY", "OVERRIDDEN_BY", "BOUND_TO", "ROUTES_TO", "HANDLED_BY", "SCHEDULES",
             "DISPATCHES", "LISTENED_BY", "USES_MIDDLEWARE",
             # TypeScript / Vue / Nuxt + cross-repo link
             "USES_COMPOSABLE", "USES_STORE", "RENDERS", "HTTP_CALLS", "MATCHES_ROUTE",
             # native code: function pointers / callbacks / dispatch tables
             "REFERENCES_FN",
             # realtime: broadcasting auth route -> channel callbacks; client subscriptions -> backend channels
             "AUTHORIZES_CHANNEL", "SUBSCRIBES_CHANNEL", "MATCHES_CHANNEL",
             # protocol endpoints: web / native bridges (JS / Dart call -> endpoint -> Kotlin / Swift handler)
             "SENDS_TO", "RECEIVED_BY"]
TS_CODE_KINDS = ("composable", "store", "component", "module")
CODE_KINDS = ("method", "function", "script") + TS_CODE_KINDS
ENTRY_NODE_KINDS = ("route", "command", "schedule", "job", "listener", "admin", "observer", "page", "layout", "app", "message",
                    "channel")
CLASS_KINDS = ("class", "interface", "trait", "enum")


def resolve_targets(st: GraphStore, spec: str) -> list[str]:
    """spec: kind:key (glob * allowed) | table.column | Class::method | Class (short or FQN)
    | page:/route/path | a source file path (repo-relative, or repo/... in a combined DB)
    | TS symbol (useX, useX.fn, fn). `Sub.method` for a method Sub inherits without redefining it resolves to the
    inherited definition (inherited_targets has the details)."""
    out = _resolve_direct(st, spec)
    if out:
        return out
    return [i["method"] for i in inherited_targets(st, spec)]



def snippet(st: GraphStore, spec: str, context: int = 0, max_lines: int = 200) -> dict:
    """Source of one symbol, read from disk via the node file / line / end_line. Returns a dict
    whose "status" is one of: ok (id, kind, file, start, end, shown_start, shown_end,
    lines [[no, text], ...], truncated, note), ambiguous (candidates [{id, kind, file, line}] up to
    20, count), none (no match), no_source (node has no file / line), or missing (source file not on
    disk, with the paths tried). context adds lines around the span; max_lines caps the body."""
    ids = list(dict.fromkeys(resolve_targets(st, spec)))
    if not ids:
        return {"status": "none", "spec": spec}
    if len(ids) > 1:
        cands = []
        for nid in ids[:20]:
            n = st.node(nid)
            cands.append({"id": nid, "kind": n["kind"] if n else "?",
                          "file": n["file"] if n else None, "line": n["line"] if n else None})
        return {"status": "ambiguous", "spec": spec, "count": len(ids), "candidates": cands}
    nid = ids[0]
    n = st.node(nid)
    if not n or not n["file"] or not n["line"]:
        return {"status": "no_source", "id": nid, "spec": spec}
    start = n["line"]
    end = n["end_line"] if (n["end_line"] and n["end_line"] >= start) else None
    note = None
    if end is None:
        end, note = start, "no end line in the index; showing the start line only (widen with --context)"
    f = n["file"]
    root = st.meta().get("root")
    tried = [f] if os.path.isabs(f) else ([os.path.join(str(root), f)] if root else []) + [f]
    path = next((q for q in tried if os.path.isfile(q)), None)
    if path is None:
        return {"status": "missing", "id": nid, "file": f, "root": root, "tried": tried}
    try:
        src = open(path, encoding="utf-8", errors="replace").read().split("\n")
    except OSError as ex:
        return {"status": "missing", "id": nid, "file": f, "root": root, "tried": tried, "error": str(ex)}
    total = len(src)
    lo, hi = max(1, start - context), min(total, end + context)
    body = [[i, src[i - 1]] for i in range(lo, hi + 1)]
    truncated = 0
    if len(body) > max_lines:
        truncated, body = len(body) - max_lines, body[:max_lines]
    return {"status": "ok", "id": nid, "kind": n["kind"], "file": f, "start": start, "end": end,
            "shown_start": lo, "shown_end": (body[-1][0] if body else lo), "lines": body,
            "truncated": truncated, "note": note, "context": context, "max_lines": max_lines}


def render_snippet(res: dict) -> str:
    """Text for `cg snippet`: a `file:start-end` header then line-numbered body, or the candidate /
    error line for a non-ok status."""
    status = res.get("status")
    if status == "none":
        return f"no node matches {res['spec']!r}; try `search` with part of the name"
    if status == "no_source":
        return f"{res['id']}: no source location recorded (nothing to show)"
    if status == "missing":
        why = res.get("error") or ("tried " + ", ".join(res.get("tried") or []))
        return f"{res['id']}: source file {res.get('file')!r} not found (indexed root {res.get('root')!r}); {why}"
    if status == "ambiguous":
        out = [f"{res['count']} nodes match {res['spec']!r}; name one (showing up to 20):"]
        for c in res["candidates"]:
            loc = f"{c['file']}:{c['line']}" if c.get("file") else "(no source location)"
            out.append(f"  {c['id']}  [{c['kind']}]  {loc}")
        return "\n".join(out)
    lines = res["lines"]
    out = [f"{res['file']}:{res['start']}-{res['end']}"]
    if res.get("note"):
        out.append(f"note: {res['note']}")
    w = max((len(str(no)) for no, _ in lines), default=1)
    out += [f"{no:>{w}}| {text}" for no, text in lines]
    if res["truncated"]:
        out.append(f"... (truncated, {res['truncated']} more lines; use --max-lines)")
    return "\n".join(out)


INHERIT_KINDS = ("EXTENDS", "IMPLEMENTS", "USES_TRAIT")
_INHERITED: dict = {}


def inherited_targets(st: GraphStore, spec: str) -> list[dict]:
    """`Sub.method` / `Sub::method` where Sub defines no `method` but an ancestor class does: one entry per matching
    class, {spec, class, class_fqn, method, method_fqn, defined_in}: the nearest ancestor's definition (breadth-first
    over EXTENDS / IMPLEMENTS / USES_TRAIT, in declaration order). [] when the spec names a defined symbol or no
    ancestor defines the method."""
    path = getattr(st, "path", None)
    try:
        key = (path, os.path.getmtime(path), spec) if path else None
    except OSError:
        key = None
    if key is None:
        return _inherited_uncached(st, spec)
    if key not in _INHERITED:
        if len(_INHERITED) > 256:
            _INHERITED.clear()
        _INHERITED[key] = _inherited_uncached(st, spec)
    return _INHERITED[key]


def _inherited_uncached(st: GraphStore, spec: str) -> list[dict]:
    out: list[dict] = []
    m = re.fullmatch(r"(.+?)(?:\.|::)([A-Za-z_$][\w$]*)", spec)
    if m and not spec.startswith(("page:", "/")) and not _resolve_direct(st, spec):
        cls_spec, meth = m.group(1), m.group(2)
        classes = [c for c in _resolve_direct(st, cls_spec) if c.split(":", 1)[0] in CLASS_KINDS]
        for cid in dict.fromkeys(classes):
            seen, frontier, hit = {cid}, [cid], None
            while frontier and not hit:
                nxt = []
                for c in frontier:
                    for r in st.q(f"SELECT dst FROM edges WHERE src=? AND kind IN ({','.join('?' * len(INHERIT_KINDS))}) "
                                  "ORDER BY line, dst", (c, *INHERIT_KINDS)):
                        if r["dst"] not in seen:
                            seen.add(r["dst"])
                            nxt.append(r["dst"])
                for a in nxt:
                    hit = _member_of(st, a, meth)
                    if hit:
                        hit = (a, hit)
                        break
                frontier = nxt
            if hit:
                fq = _fqn_of(st, [cid, hit[0], hit[1]])
                out.append({"spec": spec, "class": cid, "class_fqn": fq.get(cid, cid), "method": hit[1],
                            "method_fqn": fq.get(hit[1], hit[1]), "defined_in": hit[0]})
    return out


def _member_of(st: GraphStore, cls: str, name: str) -> str | None:
    """The method `name` a class node contains (Python `Base.run`, TS `Shape.area`, PHP `X::run`)."""
    for r in st.q("SELECT n.id, n.name FROM edges e JOIN nodes n ON n.id=e.dst WHERE e.src=? AND e.kind='CONTAINS' "
                  "AND n.kind IN ('method','function')", (cls,)):
        nm = r["name"] or ""
        if nm == name or nm.endswith("." + name) or nm.endswith("::" + name):
            return r["id"]
    fq = st.q("SELECT fqn FROM nodes WHERE id=?", (cls,))
    if fq and fq[0]["fqn"]:          # PHP classes hold their methods by fqn (Class::method), not CONTAINS
        r = st.q("SELECT id FROM nodes WHERE kind='method' AND fqn=?", (fq[0]["fqn"] + "::" + name,))
        if r:
            return r[0]["id"]
    return None


def _descendants(st: GraphStore, classes: list[str]) -> set[str]:
    seen, frontier = set(classes), list(classes)
    kq = ",".join("?" * len(INHERIT_KINDS))
    while frontier:
        nxt = []
        for c in frontier:
            for r in st.q(f"SELECT src FROM edges WHERE dst=? AND kind IN ({kq})", (c, *INHERIT_KINDS)):
                if r["src"] not in seen:
                    seen.add(r["src"])
                    nxt.append(r["src"])
        frontier = nxt
    return seen


def override_seeds(st: GraphStore, spec_or_specs, targets: list[str], min_conf: str) -> list[str]:
    """The overrides / implementations below method targets (impact, tests and reaches follow them: a call through a
    base-typed value or a collection lands on an override). For an inherited spec (`Sub.method` resolved to
    `Base.method`) only the overrides in Sub and its subclasses count: a Sub instance never dispatches to a sibling's."""
    if not targets or _has_class_target(targets):
        return []
    below = _all_overrides(st, targets, min_conf)
    specs = [spec_or_specs] if isinstance(spec_or_specs, str) else list(spec_or_specs or [])
    inh = [i for s in specs for i in inherited_targets(st, s)]
    if below and inh:
        sub = _descendants(st, [i["class"] for i in inh])
        owner = {r["dst"]: r["src"] for i in range(0, len(below), 500) for r in st.q(
            f"SELECT src, dst FROM edges WHERE kind='CONTAINS' AND dst IN ({','.join('?' * len(below[i:i + 500]))})",
            below[i:i + 500])}
        below = [b for b in below if owner.get(b) in sub]
    return below


def _ancestors(st: GraphStore, cls: str) -> set[str]:
    seen, frontier = {cls}, [cls]
    kq = ",".join("?" * len(INHERIT_KINDS))
    while frontier:
        nxt = []
        for c in frontier:
            for r in st.q(f"SELECT dst FROM edges WHERE src=? AND kind IN ({kq})", (c, *INHERIT_KINDS)):
                if r["dst"] not in seen:
                    seen.add(r["dst"])
                    nxt.append(r["dst"])
        frontier = nxt
    return seen


def narrow_inherited(st: GraphStore, inh: list[dict]) -> set[int]:
    """For `Sub.method` resolved to an ancestor's definition: the call edges into that definition whose receiver type
    is known (edge attrs.recv: the TypeScript checker type, a Python inferred instance or collection element) and can
    not be a Sub (neither Sub, a subclass, nor an ancestor of it). Each entry of `inh` gets `narrowed` counts."""
    allowed: dict[str, set] = defaultdict(set)
    known: dict[str, set] = {}
    for i in inh:
        allowed[i["method"]] |= _descendants(st, [i["class"]]) | _ancestors(st, i["class"])
        # receivers whose path to the defining class the graph knows; any other receiver (a mixin `extends mix(B)`,
        # a merged interface + class declaration) is kept: its hierarchy is incomplete, so it may still be a Sub
        known.setdefault(i["method"], _descendants(st, [i["defined_in"]]))
    skip, counts = set(), {}
    for meth, ok in allowed.items():
        n = typed = dropped = 0
        for r in st.q("SELECT id, attrs FROM edges WHERE dst=? AND kind IN ('CALLS','TEST_CALLS')", (meth,)):
            n += 1
            try:
                rc = (json.loads(r["attrs"]) if r["attrs"] else {}).get("recv")
            except ValueError:
                rc = None
            if not rc:
                continue
            typed += 1
            if not set(rc) & ok and set(rc) <= known[meth]:
                skip.add(r["id"])
                dropped += 1
        counts[meth] = {"calls": n, "receiver_typed": typed, "dropped": dropped}
    for i in inh:
        i["narrowed"] = counts[i["method"]]
    return skip


def override_bases(st: GraphStore, specs) -> list[dict]:
    """For a spec naming an override (`Sub.peek` defined in Sub, overriding / implementing `Base.peek`): one entry per
    base / interface method above it, shaped like inherited_targets' ({spec, class, method, defined_in, override}),
    so the calls into the base method whose receiver type is known and cannot be a Sub are narrowed the same way
    (`sib.peek()` with Sib inheriting Base.peek never runs Sub.peek)."""
    out: list[dict] = []
    dq = ",".join("?" * len(DISPATCH_KINDS))
    for spec in ([specs] if isinstance(specs, str) else list(specs or [])):
        for t in dict.fromkeys(x for x in _resolve_direct(st, spec) if x.startswith("method:")):
            own = st.q("SELECT src FROM edges WHERE dst=? AND kind='CONTAINS'", (t,))
            cls = own[0]["src"] if own and own[0]["src"].split(":", 1)[0] in CLASS_KINDS + ("type",) else None
            if not cls:
                continue
            seen, frontier = {t}, [t]
            while frontier:
                nxt = []
                for m in frontier:
                    for r in st.q(f"SELECT src FROM edges WHERE dst=? AND kind IN ({dq})", (m, *DISPATCH_KINDS)):
                        if r["src"] not in seen and r["src"].startswith("method:"):
                            seen.add(r["src"])
                            nxt.append(r["src"])
                frontier = nxt
            for b in sorted(seen - {t}):
                bo = st.q("SELECT src FROM edges WHERE dst=? AND kind='CONTAINS'", (b,))
                if not bo:
                    continue
                fq = _fqn_of(st, [cls, b])
                out.append({"spec": spec, "class": cls, "class_fqn": fq.get(cls, cls), "method": b,
                            "method_fqn": fq.get(b, b), "defined_in": bo[0]["src"], "override": True})
    return out


def _narrowed_bases(ob: list[dict]) -> list[dict]:
    return [o for o in ob if (o.get("narrowed") or {}).get("dropped")]


class narrowed:
    """`with narrowed(st, inh):` the traversals of one query leave out the calls `narrow_inherited` drops."""

    def __init__(self, st: GraphStore, inh: list[dict]):
        self.skip = narrow_inherited(st, inh) if inh else set()

    def __enter__(self):
        self.tok = _SKIP_EDGES.set(self.skip or None)
        return self

    def __exit__(self, *a):
        _SKIP_EDGES.reset(self.tok)


def _short(fqn: str) -> str:
    """`Class.method` of a dotted / `::` / file-qualified fqn (pkg.mod.Class.method -> Class.method)."""
    tail = re.split(r"[#/\\]", fqn)[-1]
    parts = re.split(r"\.|::", tail)
    return ".".join(parts[-2:]) if len(parts) >= 2 else tail


def inherited_lines(res: dict) -> list[str]:
    """'B.run -> inherited from Base.run' notes for a result whose spec resolved through a class's ancestors, with how
    the calls into the inherited definition were narrowed to receivers that can be a B (short names; JSON has fqns)."""
    out = []
    for i in res.get("override_narrowed") or []:
        cls, nw = _short(i["class_fqn"]).split(".")[-1], i["narrowed"]
        out.append(f"{i['spec']} overrides {_short(i['method_fqn'])} (its callers narrowed to {cls}: {nw['dropped']} of "
                   f"{nw['calls']} calls on other classes left out)")
    for i in res.get("inherited") or []:
        cls = _short(i["class_fqn"]).split(".")[-1]
        line = f"{i['spec']} -> inherited from {_short(i['method_fqn'])}"
        nw = i.get("narrowed")
        if nw and nw["dropped"]:
            line += f" (callers narrowed to {cls}: {nw['dropped']} of {nw['calls']} calls on other classes left out)"
        elif nw and nw["receiver_typed"] == nw["calls"]:
            line += f" (every call is on a receiver that can be a {cls})"
        else:
            line += f" (calls with an unknown receiver type count for {cls})"
        out.append(line)
    return out


# `Class::method` and `Class.method` name the same member in every language (#75): a spec that matches nothing with
# its own separator is retried with the other one (Swift / Kotlin / Python / TS / Dart fqns use `.`, PHP / Rust / C++ `::`)
MEMBER_PATH_RE = re.compile(r"\\?[A-Za-z_$][\w$\\]*(?:(?:\.|::)[A-Za-z_$][\w$]*)+")


def _resolve_direct(st: GraphStore, spec: str) -> list[str]:
    out = _resolve_direct_one(st, spec)
    if out or not MEMBER_PATH_RE.fullmatch(spec) or re.search(r"\.(vue|[cm]?[jt]sx?|py|dart|rs|c|h|cc|cpp|cxx|hh|hpp|hxx|m|mm|swift|kt|php)$", spec):
        return out
    alt = spec.replace("::", ".") if "::" in spec else spec.replace(".", "::")
    return _resolve_direct_one(st, alt)


def _resolve_direct_one(st: GraphStore, spec: str) -> list[str]:
    if spec.startswith("page:/"):
        rows = st.q("SELECT id FROM nodes WHERE kind='page' AND json_extract(attrs,'$.route')=?", (spec[5:],))
        return [r["id"] for r in rows]
    if st.q("SELECT 1 FROM nodes WHERE id=? LIMIT 1", (spec,)):  # an exact node id (e.g. method:App\X::y)
        return [spec]
    fn = _file_name_targets(st, spec)
    if fn is not None:
        return fn
    if re.search(r"\.(vue|ts|tsx|js|mjs|py|dart)$", spec) and "::" not in spec:
        rows = st.q("SELECT id FROM nodes WHERE kind IN ('page','component','layout','app','module') AND (file=? OR file LIKE ?)",
                    (spec, "%/" + spec))
        if rows:
            return [r["id"] for r in rows]
    if ":" in spec and not "::" in spec:
        kind, key = spec.split(":", 1)
        pat = f"{kind}:{key}".replace("*", "%")
        rows = st.q("SELECT id FROM nodes WHERE id LIKE ?", (pat,)) if "%" in pat else st.q("SELECT id FROM nodes WHERE id=?", (pat,))
        return [r["id"] for r in rows]
    nat = _native_targets(st, spec)
    if nat:
        return nat
    if "::" not in spec and "." in spec and "\\" not in spec:
        # python dotted path (pkg.mod.func / pkg.mod.Class.method) or Dart Class.method
        rows = st.q("""SELECT id FROM nodes WHERE lang IN ('python','dart') AND kind IN ('function','method','class','module')
                       AND (fqn=? OR fqn LIKE ?)""", (spec, "%#" + spec))
        if rows:
            return [r["id"] for r in rows]
        # a dotted suffix of a Python fqn: `PyProgram.load`, `plugin.PyProgram.load`, `checks.validate` in a package
        rows = st.q("""SELECT id FROM nodes WHERE lang='python' AND kind IN ('function','method','class')
                       AND fqn LIKE ? ESCAPE '\\'""", ("%." + spec.replace("_", "\\_"),))
        if rows:
            return [r["id"] for r in rows]
        # Kotlin / Swift (and the Java / ObjC bridge stubs): pkg.Class.method, Class.method
        rows = st.q("""SELECT id FROM nodes WHERE lang IN ('kotlin','swift','java','objc') AND kind IN ('function','method','class')
                       AND (fqn=? OR fqn LIKE ? ESCAPE '\\' OR fqn LIKE ? ESCAPE '\\')""",
                    (spec, "%." + _like(spec), "%:" + _like(spec)))
        if rows:
            return [r["id"] for r in rows]
        rows = st.q("SELECT id FROM nodes WHERE id=?", (f"column:{spec}",))
        if rows:
            return [rows[0]["id"]]
        rows = st.q("SELECT id FROM nodes WHERE id=?", (f"config:{spec}",))
        if rows:
            return [r["id"] for r in rows]
    if re.fullmatch(r"[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*", spec) and "\\" not in spec:
        rows = st.q("""SELECT id FROM nodes WHERE lang IN ('ts','python','dart')
                       AND kind IN ('function','composable','store','method','class','type')
                       AND (name=? OR name LIKE ?)""", (spec, "%." + spec))
        if rows:
            return [r["id"] for r in rows]
    spec = spec.lstrip("\\")
    if "::" in spec:
        rows = st.q("SELECT id FROM nodes WHERE kind='method' AND (fqn=? OR fqn LIKE ?)", (spec, "%\\" + spec))
        return [r["id"] for r in rows]
    rows = st.q("SELECT id, fqn FROM nodes WHERE kind IN ('class','interface','trait','enum') AND (fqn=? OR fqn LIKE ?)", (spec, "%\\" + spec))
    out = []
    for r in rows:
        out.append(r["id"])
        out += [x["id"] for x in st.q("SELECT id FROM nodes WHERE kind='method' AND fqn LIKE ?", (r["fqn"] + "::%",))]
        # an event / job class also selects its dispatch node (event:X, job:X): `impact OrderShipped` follows dispatches
        out += [x["id"] for x in st.q("SELECT id FROM nodes WHERE id IN (?, ?)", ("event:" + r["fqn"], "job:" + r["fqn"]))]
    if not out and re.fullmatch(r"[A-Za-z_][\w]*", spec):
        # a bare free-function name in any language, as `search` finds it (Swift / Kotlin / PHP / Rust / C functions:
        # `formatPrice`, Kotlin `pkg.formatPrice`, PHP `App\helpers\formatPrice`)
        rows = st.q("""SELECT id FROM nodes WHERE kind='function' AND (name=? OR fqn=?) ORDER BY file, line""",
                    (spec, spec))
        out = [r["id"] for r in rows]
    return out


FILE_NAME_RE = re.compile(r"(?P<file>[^#:]+\.(?:vue|ts|tsx|mts|cts|js|jsx|mjs|cjs|svelte))#(?P<name>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:~\d+)?)")
FILE_NAME_KINDS = ("function", "method", "class", "composable", "store", "type", "interface", "enum")


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _file_name_targets(st: GraphStore, spec: str) -> list[str] | None:
    """`file#name` for TypeScript / JavaScript / Vue nodes (ids `kind:file#name`): the file part matches the node's
    file exactly or as a path suffix (`app.ts#listOrders`, `src/app.ts#listOrders`), the name part the declared name,
    `Class.method` included; a bare member name (`svc.ts#create`) matches `Class.create` in that file when nothing
    is declared under the name itself. None when the spec is not of that form or nothing matches (the other forms
    are tried then)."""
    m = FILE_NAME_RE.fullmatch(spec)
    if not m:
        return None
    f, name = m["file"].removeprefix("./"), m["name"]
    rows = st.q("""SELECT id, substr(id, instr(id, '#') + 1) AS key FROM nodes
                   WHERE instr(id, '#') > 0 AND (file = ? OR file LIKE ? ESCAPE '\\') ORDER BY file, line""",
                (f, "%/" + _like(f)))
    # `name~2`: a second declaration of the name in that file, `name@line`: a per-branch platform variant
    keys = [(r["id"], r["key"] if "~" in name else re.sub(r"(~\d+|@\d+)$", "", r["key"])) for r in rows]
    hit = [i for i, k in keys if k == name] or [i for i, k in keys if k.endswith("." + name)]
    code = [i for i in hit if i.split(":", 1)[0] in FILE_NAME_KINDS]   # the declaration, not its listener / route node
    return code or hit or None


NATIVE_LANGS = ("rust", "c", "cpp")
NATIVE_CODE = ("function", "method", "ffi", "macro")
NATIVE_TYPES = ("struct", "enum", "union", "trait", "type_alias", "class", "typedef")
NATIVE_FILE_RE = re.compile(r"\.(rs|c|h|cc|cpp|cxx|hh|hpp|hxx|ipp|inl|m|mm)$")


def _native_targets(st: GraphStore, spec: str) -> list[str]:
    """Rust / C / C++ specs: a source file (all items defined in it, plus its file node), a path
    (crate::module::fn, Type::method, ns::Class::method; `Type::m` also matches `<Type as Trait>::m`), or a bare
    function / type name. Returns [] when nothing native matches (other resolvers take over)."""
    if not st.q("SELECT 1 FROM nodes WHERE lang IN ('rust','c','cpp') LIMIT 1"):
        return []
    langs = ",".join(f"'{x}'" for x in NATIVE_LANGS)
    if NATIVE_FILE_RE.search(spec) and "::" not in spec:
        rows = st.q(f"""SELECT id FROM nodes WHERE lang IN ({langs}) AND (file=? OR file LIKE ?)
                        AND kind IN ('file','function','method','ffi','struct','enum','union','trait','class','typedef')
                        ORDER BY kind='file' DESC, line""", (spec, "%/" + spec))
        return [r["id"] for r in rows]
    code = ",".join(f"'{k}'" for k in NATIVE_CODE)
    types = ",".join(f"'{k}'" for k in NATIVE_TYPES)
    if spec.startswith(("mod:", "crate:")) or st.q("SELECT 1 FROM nodes WHERE kind IN ('mod','crate') AND id IN (?, ?)",
                                                    ("mod:" + spec, "crate:" + spec)):
        # a Rust module / crate: every function and method defined in it (and its submodules)
        m = spec.split(":", 1)[1] if spec.startswith(("mod:", "crate:")) else spec
        rows = st.q(f"SELECT id FROM nodes WHERE lang='rust' AND kind IN ({code}) AND (module=? OR module LIKE ?) ORDER BY file, line",
                    (m, m + "::%"))
        return [r["id"] for r in rows]
    if "::" in spec:
        head, _, last = spec.rpartition("::")
        rows = st.q(f"""SELECT id FROM nodes WHERE lang IN ({langs}) AND kind IN ({code},{types})
                        AND (fqn=? OR fqn LIKE ? OR fqn LIKE ? OR fqn LIKE ?)""",
                    (spec, "%::" + spec, f"%<{head} as %>::{last}", f"%<{head.rsplit('::', 1)[-1]} as %>::{last}"))
        ids = [r["id"] for r in rows]
        tys = [i for i in ids if i.split(":", 1)[0] in NATIVE_TYPES]
        for t in tys:  # a type spec also selects its methods (like Class in PHP)
            fq = t.split(":", 1)[1]
            ids += [r["id"] for r in st.q("SELECT id FROM nodes WHERE kind='method' AND (fqn LIKE ? OR fqn LIKE ?)",
                                          (fq + "::%", fq.rsplit("::", 1)[0] + f"::<{fq.rsplit('::', 1)[-1]} as %"))]
        return list(dict.fromkeys(ids))
    if re.fullmatch(r"[A-Za-z_]\w*", spec):
        rows = st.q(f"SELECT id FROM nodes WHERE lang IN ({langs}) AND kind IN ({code}) AND name=?", (spec,))
        if rows:
            return [r["id"] for r in rows]
        rows = st.q(f"SELECT id FROM nodes WHERE lang IN ({langs}) AND kind IN ({types}) AND name=?", (spec,))
        return [r["id"] for r in rows]
    return []


def default_gate(st: GraphStore) -> str | None:
    """Gate scenario the DB was indexed with (first one), if any."""
    r = st.q("SELECT DISTINCT scenario FROM node_entry_live LIMIT 1")
    return r[0]["scenario"] if r else None


def _px(st: GraphStore, platform: str | None) -> set:
    """Edge ids that do not exist on `platform` (codegraph/platforms.py), empty without a platform filter."""
    if not platform:
        return set()
    from .platforms import exclusions
    return exclusions(st, platform)["edges"]


# edge ids a query leaves out (calls to an inherited definition on receivers that cannot be the spec's class):
# set by `narrowed()` for the duration of one impact / reaches / tests query
_SKIP_EDGES: contextvars.ContextVar = contextvars.ContextVar("cg_skip_edges", default=None)


def reverse_closure(st: GraphStore, targets: list[str], kinds=None, min_conf="heuristic", max_depth=30,
                    exclude_gate: str | None = None, seed_inst: bool = False, platform: str | None = None) -> dict[str, int]:
    """Reverse transitive closure over `kinds`. seed_inst: code that instantiates a targeted class (`new X`) is a
    dependent too (INSTANTIATES is followed into the targets only, never further up). platform: only over references
    that exist on that target (codegraph/platforms.py)."""
    kinds = kinds or PROPAGATING
    if not targets:
        return {}
    pclause = ""
    if platform and _px(st, platform):
        from .platforms import temp_table
        pclause = f"AND NOT EXISTS (SELECT 1 FROM {temp_table(st, platform)} x WHERE x.id = e.id)"
    st.db.execute("DROP TABLE IF EXISTS temp.t_targets")
    st.db.execute("CREATE TEMP TABLE t_targets(id TEXT PRIMARY KEY)")
    st.db.executemany("INSERT OR IGNORE INTO t_targets VALUES (?)", [(t,) for t in targets])
    kq = ",".join("?" * len(kinds))
    inst = "OR (e.kind = 'INSTANTIATES' AND r.depth = 0)" if seed_inst else ""
    sql = f"""
    WITH RECURSIVE r(id, depth) AS (
        SELECT id, 0 FROM t_targets
        UNION
        SELECT e.src, r.depth + 1 FROM edges e JOIN r ON e.dst = r.id
        WHERE (e.kind IN ({kq}) {inst}) AND e.conf_rank >= ? AND r.depth < ? AND (e.gate IS NULL OR e.gate != ?) {pclause}
    )
    SELECT id, MIN(depth) AS depth FROM r GROUP BY id"""
    skip = _SKIP_EDGES.get()
    if skip:
        st.db.execute("DROP TABLE IF EXISTS temp.t_skip_edges")
        st.db.execute("CREATE TEMP TABLE t_skip_edges(id INTEGER PRIMARY KEY)")
        st.db.executemany("INSERT OR IGNORE INTO t_skip_edges VALUES (?)", [(x,) for x in skip])
        sql = sql.replace(
            "AND (e.gate IS NULL OR e.gate != ?)", "AND (e.gate IS NULL OR e.gate != ?) AND e.id NOT IN (SELECT id FROM t_skip_edges)")
    rows = st.q(sql, (*kinds, CONFIDENCE_RANK[min_conf], max_depth, exclude_gate or "\x00"))
    return {r["id"]: r["depth"] for r in rows}


def shortest_paths(st: GraphStore, depth: dict[str, int], kinds=None, min_conf="heuristic", exclude_gate: str | None = None,
                   seed_inst: bool = False, platform: str | None = None) -> dict[str, list[dict]]:
    """For each reached node pick an outgoing edge to a node one step closer to a target."""
    kinds = set(kinds or PROPAGATING)
    px = _px(st, platform)
    skip = _SKIP_EDGES.get()
    best = {}
    ids = list(depth)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for e in st.q(f"SELECT id,src,dst,kind,file,line,confidence,conf_rank,gate,attrs FROM edges WHERE src IN ({q})", chunk):
            if e["conf_rank"] < CONFIDENCE_RANK[min_conf] or (px and e["id"] in px) or (skip and e["id"] in skip):
                continue
            if e["kind"] not in kinds and not (seed_inst and e["kind"] == "INSTANTIATES" and depth.get(e["dst"]) == 0):
                continue
            if exclude_gate and e["gate"] == exclude_gate:
                continue
            s, d = e["src"], e["dst"]
            if d in depth and depth[d] == depth[s] - 1:
                cur = best.get(s)
                # prefer stronger confidence, then live (ungated) edges
                rank = (e["conf_rank"], e["gate"] is None)
                if cur is None or rank > (cur["conf_rank"], cur["gate"] is None):
                    best[s] = dict(e)
    paths = {}
    for n in depth:
        path, x, guard = [], n, 0
        while depth.get(x, 0) > 0 and x in best and guard < 60:
            e = best[x]
            path.append(_hop_of(e))
            x = e["dst"]
            guard += 1
        paths[n] = path
    return paths


class Deadline(Exception):
    """A traversal ran past its deadline (time.time() value), e.g. a starter query over its time budget."""


class GroupClosures:
    """Reverse closures of many target groups in one pass: what reverse_closure + shortest_paths give for each group
    separately, from a single read of the edge table and one multi-source breadth-first walk (a bitmask of groups
    per node and level). `within`: entry nodes (e.g. routes); the walk stays on nodes they reach, which holds every
    node of every shortest path from them, so depths and paths of those nodes equal the per-group answers."""

    def __init__(self, st: GraphStore, groups: list[list[str]], kinds=None, min_conf="heuristic", max_depth=30,
                 exclude_gate: str | None = None, platform: str | None = None, within: list[str] | None = None,
                 deadline: float | None = None):
        kinds = list(kinds or PROPAGATING)
        self.st, self.deadline = st, deadline
        px = _px(st, platform)
        rev: dict[str, list] = defaultdict(list)
        fwd: dict[str, list] = defaultdict(list)
        kq = ",".join("?" * len(kinds))
        for eid, src, dst, kind, rank, gate in st.db.execute(
                f"SELECT id, src, dst, kind, conf_rank, gate FROM edges WHERE kind IN ({kq}) AND conf_rank >= ?",
                (*kinds, CONFIDENCE_RANK[min_conf])):
            if px and eid in px:
                continue
            rev[dst].append((src, gate))
            fwd[src].append((kind, eid, dst, rank, gate))
        for v in fwd.values():
            v.sort(key=lambda e: (e[0], e[1]))     # the (src, kind, id) order of the edge index, for equal ties
        self.fwd = fwd
        self._check()
        scope = None
        if within is not None:
            scope, todo = set(within), list(within)
            while todo:
                for e in fwd.get(todo.pop(), ()):
                    if e[2] not in scope:
                        scope.add(e[2])
                        todo.append(e[2])
            self._check()
        self.levels = self._walk(rev, groups, max_depth, None, scope)
        self.live = self._walk(rev, groups, max_depth, exclude_gate, scope) if exclude_gate else None

    def _check(self):
        if self.deadline is not None and time.time() > self.deadline:
            raise Deadline()

    def _walk(self, rev, groups, max_depth, exclude_gate, scope) -> dict[str, list[tuple[int, int]]]:
        """node -> [(depth, groups first reached at that depth as a bitmask)]."""
        frontier: dict[str, int] = {}
        for i, ts in enumerate(groups):
            for t in ts:
                if scope is None or t in scope:
                    frontier[t] = frontier.get(t, 0) | (1 << i)
        seen = dict(frontier)
        levels = {t: [(0, m)] for t, m in frontier.items()}
        d = 0
        while frontier and d < max_depth:
            self._check()
            nxt: dict[str, int] = {}
            for x, m in frontier.items():
                for src, gate in rev.get(x, ()):
                    if exclude_gate is not None and gate == exclude_gate:
                        continue
                    if scope is not None and src not in scope:
                        continue
                    new = m & ~seen.get(src, 0)
                    if new:
                        seen[src] = seen.get(src, 0) | new
                        nxt[src] = nxt.get(src, 0) | new
            d += 1
            for x, m in nxt.items():
                levels.setdefault(x, []).append((d, m))
            frontier = nxt
        return levels

    @staticmethod
    def _depth(levels, node: str, bit: int) -> int | None:
        for d, m in levels.get(node, ()):
            if m & bit:
                return d
        return None

    def depth(self, node: str, group: int) -> int | None:
        return self._depth(self.levels, node, 1 << group)

    def reached(self, node: str, group: int, live: bool = False) -> bool:
        lv = self.live if live and self.live is not None else self.levels
        return self._depth(lv, node, 1 << group) is not None

    def groups_of(self, node: str) -> int:
        m = 0
        for _, b in self.levels.get(node, ()):
            m |= b
        return m

    def path_edges(self, node: str, group: int) -> list[int]:
        """Edge ids of the path shortest_paths picks from `node` to the group (stronger confidence, then live edges)."""
        bit, out, x, guard = 1 << group, [], node, 0
        dx = self._depth(self.levels, x, bit) or 0
        while dx > 0 and guard < 60:
            best = None
            for kind, eid, dst, rank, gate in self.fwd.get(x, ()):
                if self._depth(self.levels, dst, bit) == dx - 1:
                    r = (rank, gate is None)
                    if best is None or r > best[0]:
                        best = (r, eid, dst)
            if best is None:
                break
            out.append(best[1])
            x, dx, guard = best[2], dx - 1, guard + 1
        return out

    def hops(self, ids: list[int]) -> dict[int, dict]:
        """Edge id -> path hop as shortest_paths renders it."""
        out = {}
        ids = list(dict.fromkeys(ids))
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for e in self.st.q(f"SELECT id,src,dst,kind,file,line,confidence,gate,attrs FROM edges WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                out[e["id"]] = _hop_of(e)
        return out


def _hop_of(e) -> dict:
    hop = {"from": e["src"], "kind": e["kind"], "to": e["dst"], "at": f"{e['file']}:{e['line']}", "confidence": e["confidence"]}
    if e["kind"] == "REFERENCES_FN" and e["attrs"]:
        hop["how"] = json.loads(e["attrs"]).get("how")
    elif e["kind"] == "CALLS" and e["attrs"] and '"collection"' in e["attrs"]:
        hop["via"] = json.loads(e["attrs"]).get("via")
    if e["kind"] in ("CALLS", "TEST_CALLS") and e["attrs"] and '"candidate"' in e["attrs"]:
        a = json.loads(e["attrs"])
        if a.get("binding") == "candidate":      # one of several same-name methods, receiver type unknown (#83)
            hop["candidate"] = a.get("candidates") or True
    if e["kind"] in ("CALLS", "TEST_CALLS") and e["attrs"] and '"accessor"' in e["attrs"]:
        hop["accessor"] = json.loads(e["attrs"]).get("accessor")   # inside `didSet { }` / `get { }` (Swift, #72)
    if e["gate"]:
        hop["gated"] = e["gate"]
        hop["guard"] = json.loads(e["attrs"] or "{}").get("guard")
    if e["attrs"] and '"platforms"' in e["attrs"]:
        a = json.loads(e["attrs"])
        if "platforms" in a:
            hop["platforms"] = a["platforms"]
    return hop


def entry_info(st: GraphStore, ids: list[str], gate: str | None = None) -> dict[str, dict[str, tuple[int, str]]]:
    """Entry kinds reaching each node; with `gate`, only over edges live under that scenario."""
    out = defaultdict(dict)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        if gate:
            rows = st.q(f"SELECT * FROM node_entry_live WHERE scenario=? AND node_id IN ({q})", (gate, *chunk))
        else:
            rows = st.q(f"SELECT * FROM node_entry WHERE node_id IN ({q})", chunk)
        for r in rows:
            out[r["node_id"]][r["entry_kind"]] = (r["entry_count"], r["sample_entry"])
    return out


def nearest_gated_inbound(st: GraphStore, nid: str, gate: str, max_depth=6) -> dict | None:
    """Walk callers (reverse over propagating edges) until the first edge gated under `gate`."""
    kinds = set(PROPAGATING)
    seen, frontier = {nid}, [nid]
    for _ in range(max_depth):
        nxt = []
        for x in frontier:
            for e in st.q("SELECT src,dst,kind,file,line,gate,attrs FROM edges WHERE dst=?", (x,)):
                if e["kind"] not in kinds:
                    continue
                if e["gate"] == gate:
                    a = json.loads(e["attrs"] or "{}")
                    return {"from": e["src"], "kind": e["kind"], "to": e["dst"], "at": f"{e['file']}:{e['line']}",
                            "guard": a.get("guard"), "guard_expr": a.get("guard_expr")}
                if e["src"] not in seen:
                    seen.add(e["src"])
                    nxt.append(e["src"])
        frontier = nxt
    return None


def classify(kinds: dict) -> str:
    if any(k in kinds for k in RUNTIME_ENTRY_KINDS):
        return "runtime"
    if any(k in kinds for k in LIBRARY_ENTRY_KINDS):
        return "library"
    if any(k in kinds for k in OPERATOR_ENTRY_KINDS):
        return "operator"
    if any(k in kinds for k in UI_ENTRY_KINDS):
        return "ui"
    if any(k in kinds for k in DEV_ENTRY_KINDS):
        return "dev"
    if kinds:
        return "other_entry"
    return "no_entry"


def path_candidate(path: list[dict] | None) -> bool:
    """The path goes through a candidate call edge: a call whose receiver type is unknown, bound to each of the
    project methods with that selector (Swift / Kotlin, #83), so it may reach a sibling instead of the target."""
    return any(p.get("candidate") for p in path or ())


def path_confidence(path: list[dict]) -> str:
    if not path:
        return "exact"
    return min((p["confidence"] for p in path), key=lambda c: CONFIDENCE_RANK[c])


def _platform_entries(st: GraphStore, depth: dict[str, int], platform: str, min_conf: str) -> dict[str, dict[str, tuple[int, str]]]:
    """entry_info restricted to `platform`: which entry points reach each dependent over references that exist there.
    Every entry point that reaches a dependent of the targets is itself a dependent, so the walk stays inside `depth`."""
    px = _px(st, platform)
    prop, rank = set(PROPAGATING), CONFIDENCE_RANK[min_conf]
    fwd = defaultdict(list)
    ids = list(depth)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for e in st.q(f"SELECT id, src, dst, kind, conf_rank FROM edges WHERE src IN ({q})", chunk):
            if e["kind"] in prop and e["conf_rank"] >= rank and e["id"] not in px and e["dst"] in depth:
                fwd[e["src"]].append(e["dst"])
    entries = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in st.q(f"SELECT id, entry_kind FROM nodes WHERE id IN ({q}) AND entry_kind IS NOT NULL", chunk):
            entries[r["id"]] = r["entry_kind"]
    out = defaultdict(dict)
    for en, ek in entries.items():
        seen, todo = {en}, [en]
        while todo:
            x = todo.pop()
            for y in fwd.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    todo.append(y)
        for x in seen:
            c, smp = out[x].get(ek, (0, en))
            out[x][ek] = (c + 1, smp)
    return out


def reaches(st: GraphStore, specs: list[str], min_conf="heuristic", max_depth=30, gate: str | None = "auto",
            platform: str | None = None) -> dict:
    """Reverse transitive dependents of the targets (see _reaches). An inherited `Sub.method` spec leaves out the
    calls into the inherited definition whose receiver cannot be a Sub (narrow_inherited)."""
    ob = override_bases(st, specs)
    with narrowed(st, [i for s in specs for i in inherited_targets(st, s)] + ob):
        res = _reaches(st, specs, min_conf, max_depth, gate, platform)
    if _narrowed_bases(ob):
        res["override_narrowed"] = _narrowed_bases(ob)
    return res


def _reaches(st: GraphStore, specs: list[str], min_conf="heuristic", max_depth=30, gate: str | None = "auto",
             platform: str | None = None) -> dict:
    """Reverse transitive dependents of the targets.

    With a gate scenario (default: the one the DB was indexed with), every dependent also gets
    gate_status:
      live          - some entry point -> node -> target path has no edge that is dead under the scenario
      gated_target  - every path from the node to the target passes a gated edge
      gated_entry   - node reaches the target live, but every entry point reaches the node only through a gated edge
    """
    if gate == "auto":
        gate = default_gate(st)
    targets, resolved = [], {}
    for s in specs:
        t = resolve_targets(st, s)
        resolved[s] = t
        targets += t
    si = _has_class_target(targets)
    pf = platform
    # the dependents of a base / interface method include those of its overrides (as in impact), marked via_override
    below = override_seeds(st, specs, targets, min_conf)
    seeds = targets + [b for b in below if b not in targets]
    depth = reverse_closure(st, seeds, min_conf=min_conf, max_depth=max_depth, seed_inst=si, platform=pf)
    own = set(reverse_closure(st, targets, min_conf=min_conf, max_depth=max_depth, seed_inst=si, platform=pf)) if below else None
    paths = shortest_paths(st, depth, min_conf=min_conf, seed_inst=si, platform=pf)
    ids = list(depth)
    ents = _platform_entries(st, depth, pf, min_conf) if pf else entry_info(st, ids)
    if pf and gate:
        gate = None      # one filter at a time: the platform view replaces the gate scenario split
    live_depth = reverse_closure(st, seeds, min_conf=min_conf, max_depth=max_depth, exclude_gate=gate, seed_inst=si) if gate else depth
    live_ents = entry_info(st, ids, gate=gate) if gate else ents
    live_paths = shortest_paths(st, live_depth, min_conf=min_conf, exclude_gate=gate, seed_inst=si) if gate else paths
    nodes = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in st.q(f"SELECT id,kind,name,fqn,file,line,module,entry_kind,attrs FROM nodes WHERE id IN ({q})", chunk):
            nodes[r["id"]] = with_generated(dict(r))
    items = []
    tset, bfq = set(targets), (_fqn_of(st, below) if below else {})
    for nid, d in depth.items():
        n = nodes.get(nid, {"id": nid, "kind": nid.split(":")[0]})
        ek = {k: v[0] for k, v in ents.get(nid, {}).items()}
        if n.get("entry_kind"):
            ek[n["entry_kind"]] = ek.get(n["entry_kind"], 0) or 1
        it = {**n, "depth": d, "entry_kinds": ek, "class": classify(ek),
              "path": paths.get(nid, []), "path_confidence": path_confidence(paths.get(nid, [])),
              "is_target": d == 0, "gate_status": "live"}
        if nid in bfq and nid not in tset:
            it["override_seed"] = True       # an override followed from a base / interface target, not a dependent
        elif own is not None and nid not in own and it["path"]:
            end = it["path"][-1]["to"]
            it["via_override"] = bfq.get(end, end)
        if gate and d > 0:
            lek = {k: v[0] for k, v in live_ents.get(nid, {}).items()}
            if n.get("entry_kind"):
                lek[n["entry_kind"]] = lek.get(n["entry_kind"], 0) or 1
            it["live_entry_kinds"] = lek
            if nid not in live_depth:
                it["gate_status"] = "gated_target"
                it["gate_evidence"] = next(({k: h.get(k) for k in ("from", "kind", "to", "at", "guard")} for h in it["path"] if h.get("gated")), None)
            elif ek and not lek:
                it["gate_status"] = "gated_entry"
                it["gate_evidence"] = nearest_gated_inbound(st, nid, gate)
            elif lek:
                it["class"] = classify(lek)  # classify by entry points that still reach it live
                it["path"] = live_paths.get(nid, it["path"])
                it["depth"] = live_depth.get(nid, d)
                it["path_confidence"] = path_confidence(it["path"])
        items.append(it)
    items.sort(key=lambda x: (x["class"], x.get("module") or "", x.get("fqn") or x["id"]))
    out = {"targets": resolved, "min_confidence": min_conf, "gate": gate, "items": items}
    if below:
        out["overrides_followed"] = sorted(bfq.get(b, b) for b in below)
    inh = [i for s in specs for i in inherited_targets(st, s)]
    if inh:
        out["inherited"] = inh
    if pf:
        from .platforms import filter_info
        out["platform"] = filter_info(st, pf)
        out["platform"]["targets_not_built"] = _not_built(st, targets, pf)
    return out


def _not_built(st: GraphStore, targets: list[str], platform: str) -> list[str]:
    from .platforms import exclusions
    x = exclusions(st, platform)["nodes"]
    return [t for t in targets if t in x]


def fmt_path(path: list[dict]) -> str:
    if not path:
        return "(target)"
    s = path[0]["from"]
    for p in path:
        g = f" GATED:{p['gated']} guard {p.get('guard')}" if p.get("gated") else ""
        pl = f" only on {', '.join(p['platforms']) or 'no known target'}" if p.get("platforms") is not None else ""
        s += f"\n          -{p['kind']}[{p['confidence']} @ {p['at']}{g}{pl}]-> {p['to']}"
    return s


def render_reaches(res: dict, show_paths=True, kinds=CODE_KINDS + ENTRY_NODE_KINDS) -> str:
    out = []
    if res.get("platform"):
        from .platforms import render_filter
        out.append(render_filter(res["platform"]))
        if res["platform"].get("targets_not_built"):
            out.append(f"  not built for {res['platform']['platform']}: {', '.join(res['platform']['targets_not_built'][:6])}")
    out.append("targets:")
    for s, t in res["targets"].items():
        out.append(f"  {s} -> {len(t)} node(s): {', '.join(t[:6])}{' ...' if len(t) > 6 else ''}")
    out += ["  " + x for x in inherited_lines(res)]
    if res.get("overrides_followed"):
        ov = res["overrides_followed"]
        out.append(f"  overrides followed (their dependents count, marked via override): {', '.join(ov[:8])}"
                   + (f" …+{len(ov) - 8}" if len(ov) > 8 else ""))
    items = [i for i in res["items"] if not i["is_target"]]
    code = [i for i in items if i["kind"] in CODE_KINDS]
    entries = [i for i in items if i["kind"] in ENTRY_NODE_KINDS or (i.get("entry_kind") and i["kind"] in CODE_KINDS)]
    other = [i for i in items if i["kind"] not in CODE_KINDS + ENTRY_NODE_KINDS]
    out.append(f"dependents: {len(items)} nodes  (code: {len(code)}, entry points: {len(entries)}, other: {len(other)}) min_confidence={res['min_confidence']}")
    labels = {"runtime": f"RUNTIME (reached from {' / '.join(RUNTIME_ENTRY_KINDS)})",
              "library": "LIBRARY API (reached only through the public API of a library: pub items / exported symbols)",
              "dev": "DEV/BUILD-ONLY (reached only from tests, benches, examples or build scripts)",
              "operator": f"OPERATOR-ONLY ({' / '.join(OPERATOR_ENTRY_KINDS)}; one-off import & provisioning)",
              "ui": "UI-ONLY (reached from frontend pages / layouts / app shell; no backend entry)",
              "other_entry": "OTHER ENTRY (observer only)", "no_entry": "NOT REACHED FROM ANY INDEXED ENTRY POINT"}
    gated = [i for i in code if i.get("gate_status", "live") != "live" and i["class"] != "no_entry"]
    code = [i for i in code if i not in gated]
    for cls in ("runtime", "library", "operator", "ui", "dev", "other_entry", "no_entry"):
        group = [i for i in code if i["class"] == cls]
        if not group:
            continue
        out.append("")
        out.append(f"== {labels[cls]}: {len(group)} functions/methods")
        bymod = defaultdict(list)
        for i in group:
            bymod[i.get("module") or "?"].append(i)
        for mod in sorted(bymod):
            out.append(f"  [{mod}]")
            for i in bymod[mod]:
                ek = ", ".join(f"{k}({v})" for k, v in sorted(i["entry_kinds"].items()))
                out.append(f"    {i.get('fqn') or i['id']}  depth={i['depth']} conf={i['path_confidence']}  {ek}"
                           + (f"  [{i['kind']} @ {i.get('file')}]" if i['kind'] in TS_CODE_KINDS or (i.get('file') or '').endswith(('.ts', '.vue')) else "")
                           + (f"  @ {i.get('file')}:{i.get('line')}" if NATIVE_FILE_RE.search(i.get('file') or '') else "")
                           + generated_label(i) + platform_label(i)
                           + (f"  (via override {i['via_override']})" if i.get("via_override") else ""))
                if show_paths:
                    out.append(f"        path: {fmt_path(i['path'])}")
    if gated:
        out.append("")
        out.append(f"== GATED UNDER SCENARIO '{res.get('gate')}' (dead when the scenario holds; live otherwise): {len(gated)} functions/methods")
        bymod = defaultdict(list)
        for i in gated:
            bymod[i.get("module") or "?"].append(i)
        for mod in sorted(bymod):
            out.append(f"  [{mod}]")
            for i in bymod[mod]:
                ev = i.get("gate_evidence") or {}
                ek = ", ".join(f"{k}({v})" for k, v in sorted(i["entry_kinds"].items()))
                out.append(f"    {i.get('fqn') or i['id']}  {i['gate_status']}  entry-when-off: {ek}")
                out.append(f"        guard: {ev.get('guard')}  gated hop: {ev.get('kind')} @ {ev.get('at')} -> {ev.get('to')}")
    if entries:
        live_e = [i for i in entries if i.get("gate_status", "live") == "live"]
        out.append("")
        out.append(f"== ENTRY POINTS THAT REACH THE TARGET(S): {len(entries)} (live under gate: {len(live_e)})")
        byk = defaultdict(list)
        for i in entries:
            byk[i["kind"] if i["kind"] in ENTRY_NODE_KINDS else i["entry_kind"]].append(i)
        for k in sorted(byk):
            out.append(f"  {k}: {len(byk[k])}")
            for i in sorted(byk[k], key=lambda x: x["id"]):
                g = "" if i.get("gate_status", "live") == "live" else f"  [{i['gate_status']}]"
                nm = (i.get("fqn") or i["name"]) if NATIVE_FILE_RE.search(i.get("file") or "") else i["name"]
                out.append(f"    {nm}  ({i.get('file')}:{i.get('line')})  depth={i['depth']} conf={i['path_confidence']}{g}{generated_label(i)}{platform_label(i)}")
    return "\n".join(out)


def _has_class_target(targets: list[str]) -> bool:
    return any(t.split(":", 1)[0] in CLASS_KINDS for t in targets)


def _first_hop(path: list[dict] | None) -> dict:
    """How a caller reaches the next node towards the target: `edge` kind, plus `how` for a function reference."""
    if not path:
        return {}
    h = path[0]
    return {"edge": h["kind"], **({"how": h["how"]} if h.get("how") else {}), **({"via": h["via"]} if h.get("via") else {}),
            **({"accessor": h["accessor"]} if h.get("accessor") else {})}


def with_generated(row: dict) -> dict:
    """A node row without its raw attrs, plus `generated` (the reason) for a node from a generated / copied / vendored
    file (indexed with --include-generated)."""
    raw = row.pop("attrs", None)
    if raw and ('"platforms"' in raw or '"available"' in raw):
        from .platforms import node_platforms
        row.update(node_platforms(raw))
    if raw and '"generated"' in raw:
        try:
            g = (json.loads(raw) or {}).get("generated")
        except ValueError:
            g = None
        if g:
            row["generated"] = g.get("reason") or g.get("kind") or "generated"
            if g.get("copy_of"):
                row["copy_of"] = g["copy_of"]
    return row


def platform_label(n: dict) -> str:
    """'  [ios, android]' for code that exists only on some targets (codegraph/platforms.py), else ''."""
    from .platforms import label
    return label(n)


def generated_label(n: dict) -> str:
    """'  [generated: protoc output (Python)]' / '  [copied: copy of dist/ ..., source dist/app.js]' or ''."""
    g = n.get("generated")
    if not g:
        return ""
    return f"  [generated: {g}" + (f", source {n['copy_of']}" if n.get("copy_of") else "") + "]"


CANDIDATE_LABEL = "  (candidate)"
CANDIDATE_NOTE = ("(candidate): through a call whose receiver type is unknown, bound to each project method with that "
                  "selector; it may reach a same-name sibling instead")


def caller_label(c: dict) -> str:
    """'' for a call; '  (ref: collection)' when the caller holds a reference to the function instead of calling it;
    '  (didSet)' when the call sits in that accessor of a property; plus the generated-file label of a caller in a generated file."""
    if c.get("edge") == "REFERENCES_FN":
        lab = f"  (ref: {c['how']})" if c.get("how") else "  (ref)"
    elif c.get("via") == "collection":
        lab = "  (call through a collection)"
    else:
        lab = ""
    if c.get("accessor"):                        # the call sits in that accessor of a property node (Swift, #72)
        lab += f"  ({c['accessor']})"
    if c.get("in_property"):                     # a type node: the call is in a property of that type (#72)
        lab += "  (in a property)"
    if c.get("candidate"):
        lab += CANDIDATE_LABEL
    if c.get("via_override"):
        v = c["via_override"]
        lab += f"  (via override {v[0]}" + (f" +{len(v) - 1}" if len(v) > 1 else "") + ")"
    elif c.get("via_base"):
        lab += f"  (via base {c['via_base']})"
    return lab + generated_label(c) + platform_label(c)


# override / implementation relation between methods (base -> override); a dispatch hop, not a call. A container
# binding between two methods (Nest `{ provide: AbstractRepo, useClass: Impl }`, Laravel `bind`) is one too
DISPATCH_KINDS = ("OVERRIDDEN_BY", "IMPLEMENTED_BY")
_DISPATCH_SQL = ("(kind IN ('OVERRIDDEN_BY','IMPLEMENTED_BY') OR (kind='BOUND_TO' AND src LIKE 'method:%' "
                 "AND dst LIKE 'method:%'))")


def is_dispatch_hop(kind: str, src: str, dst: str) -> bool:
    """An override / implementation hop, or a method-to-method container binding."""
    return kind in DISPATCH_KINDS or (kind == "BOUND_TO" and src.startswith("method:") and dst.startswith("method:"))


def _fqn_of(st: GraphStore, ids) -> dict[str, str]:
    ids, out = list(ids), {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in st.q(f"SELECT id, fqn FROM nodes WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            out[r["id"]] = r["fqn"] or r["id"]
    return out


def override_relations(st: GraphStore, targets: list[str], min_conf="heuristic") -> dict[str, list[dict]]:
    """The direct override relation of the targets: `overrides` (the base / interface declarations a target
    overrides or implements) and `overridden_by` (the overrides / implementations of a target)."""
    out = {"overrides": [], "overridden_by": []}
    if not targets:
        return out
    tq = ",".join("?" * len(targets))
    rank = CONFIDENCE_RANK[min_conf]
    order = "ORDER BY CASE kind WHEN 'BOUND_TO' THEN 1 ELSE 0 END"     # one entry per pair, the type relation first
    up = _first_per_pair(st.q(f"SELECT DISTINCT src, dst, kind FROM edges WHERE dst IN ({tq}) AND {_DISPATCH_SQL} "
                              f"AND conf_rank >= ? {order}", (*targets, rank)))
    down = _first_per_pair(st.q(f"SELECT DISTINCT src, dst, kind FROM edges WHERE src IN ({tq}) AND {_DISPATCH_SQL} "
                                f"AND conf_rank >= ? {order}", (*targets, rank)))
    fq = _fqn_of(st, {r["src"] for r in up} | {r["dst"] for r in down})
    tset = set(targets)
    out["overrides"] = sorted(({"id": r["src"], "fqn": fq.get(r["src"], r["src"]), "of": r["dst"], "edge": r["kind"]}
                               for r in up if r["src"] not in tset), key=lambda x: (x["fqn"], x["of"]))
    out["overridden_by"] = sorted(({"id": r["dst"], "fqn": fq.get(r["dst"], r["dst"]), "of": r["src"], "edge": r["kind"]}
                                   for r in down if r["dst"] not in tset), key=lambda x: (x["fqn"], x["of"]))
    return out


def _first_per_pair(rows) -> list:
    seen, out = set(), []
    for r in rows:
        if (r["src"], r["dst"]) not in seen:
            seen.add((r["src"], r["dst"]))
            out.append(r)
    return out


def _all_overrides(st: GraphStore, targets: list[str], min_conf: str) -> list[str]:
    """Every override / implementation below the targets (transitively), not counting the targets themselves."""
    seen, frontier = set(targets), list(targets)
    while frontier:
        nxt = []
        for i in range(0, len(frontier), 500):
            chunk = frontier[i:i + 500]
            for r in st.q(f"SELECT dst FROM edges WHERE src IN ({','.join('?' * len(chunk))}) AND {_DISPATCH_SQL} AND conf_rank >= ?",
                          (*chunk, CONFIDENCE_RANK[min_conf])):
                if r["dst"] not in seen:
                    seen.add(r["dst"])
                    nxt.append(r["dst"])
        frontier = nxt
    return [x for x in seen if x not in set(targets)]


def _dispatch_only(st: GraphStore, depth: dict[str, int], paths: dict[str, list[dict]], min_conf: str,
                   platform: str | None) -> set[str]:
    """Reached nodes whose every step towards the targets is an override hop (a base / interface method): they do not
    call the target; their callers do, through the base type."""
    cand = [n for n, d in depth.items() if d > 0 and paths.get(n)
            and is_dispatch_hop(paths[n][0]["kind"], paths[n][0]["from"], paths[n][0]["to"])]
    if not cand:
        return set()
    px, rank, kinds = _px(st, platform), CONFIDENCE_RANK[min_conf], set(CALL_LIKE)
    calls = set()
    for i in range(0, len(cand), 500):
        chunk = cand[i:i + 500]
        for e in st.q(f"SELECT id, src, dst, kind, conf_rank FROM edges WHERE src IN ({','.join('?' * len(chunk))})", chunk):
            if e["kind"] in kinds and not is_dispatch_hop(e["kind"], e["src"], e["dst"]) and e["conf_rank"] >= rank \
                    and not (px and e["id"] in px) \
                    and depth.get(e["dst"]) == depth[e["src"]] - 1:
                calls.add(e["src"])
    return {n for n in cand if n not in calls}


def impact(st: GraphStore, spec: str, min_conf="heuristic", platform: str | None = None) -> dict:
    """Callers up to entry points. The override relation is kept apart from the callers: a base method is listed
    under `overrides`, not as a caller of its override (its callers are, `via_base`), and the callers of a base
    method include the callers of its overrides (`via_override`): calls through a collection or a base-typed value
    land on the concrete overrides."""
    inh = inherited_targets(st, spec)
    ob = override_bases(st, spec)
    with narrowed(st, inh + ob):
        out = _impact(st, spec, min_conf, platform)
    if _narrowed_bases(ob):
        out["override_narrowed"] = _narrowed_bases(ob)
    if inh:
        out["inherited"] = inh
        keep = set(override_seeds(st, spec, out["targets"], min_conf))   # a sibling class's override is not a Sub's method
        out["overridden_by"] = [x for x in out["overridden_by"] if x["id"] in keep]
    return out


# impact on a data node (#77): its users are the code that connects to / uses / reads / writes it; these edges only
# point at data nodes, so following them never widens the walk above the first code hop
DATA_USE = ["CONNECTS_TO", "USES_CONNECTION", "READS_TABLE", "WRITES_TABLE", "MAPS_TO_TABLE", "READS_COLUMN", "WRITES_COLUMN"]
DATA_TARGET = ("external:", "connection:", "table:", "column:")


def _table_columns(st: GraphStore, targets: list) -> list:
    """The column nodes of table targets (table -CONTAINS-> column): code reading / writing only some columns
    (READS_COLUMN / WRITES_COLUMN, #103) uses the table too."""
    tabs = [t for t in targets if t.startswith("table:")]
    out = []
    for i in range(0, len(tabs), 500):
        chunk = tabs[i:i + 500]
        out += [r["dst"] for r in st.q(f"SELECT dst FROM edges WHERE kind='CONTAINS' AND dst LIKE 'column:%' "
                                       f"AND src IN ({','.join('?' * len(chunk))})", chunk)]
    return [c for c in dict.fromkeys(out) if c not in targets]


def _impact(st: GraphStore, spec: str, min_conf: str, platform: str | None) -> dict:
    targets = resolve_targets(st, spec)
    kinds = CALL_LIKE + DATA_USE if any(t.startswith(DATA_TARGET) for t in targets) else CALL_LIKE
    si = _has_class_target(targets)
    rel = override_relations(st, targets, min_conf)
    below = override_seeds(st, spec, targets, min_conf)
    cols = _table_columns(st, targets) if kinds is not CALL_LIKE else []
    seeds = targets + cols + below
    depth = reverse_closure(st, seeds, kinds=kinds, min_conf=min_conf, seed_inst=si, platform=platform)
    own = reverse_closure(st, targets + cols, kinds=kinds, min_conf=min_conf, seed_inst=si, platform=platform) if below else depth
    paths = shortest_paths(st, depth, kinds=kinds, min_conf=min_conf, seed_inst=si, platform=platform)
    bases = _dispatch_only(st, depth, paths, min_conf, platform)
    for b in list(depth):
        if b in bases or b in below:
            depth.pop(b)
    fq = _fqn_of(st, bases | set(below))
    direct = defaultdict(set)   # caller only through the overrides -> the overrides it calls directly
    if below:
        bset, kq = set(below), ",".join("?" * len(CALL_LIKE))
        for i in range(0, len(below), 500):
            chunk = below[i:i + 500]
            for e in st.q(f"SELECT src, dst FROM edges WHERE dst IN ({','.join('?' * len(chunk))}) AND kind IN ({kq}) "
                          f"AND conf_rank >= ?", (*chunk, *CALL_LIKE, CONFIDENCE_RANK[min_conf])):
                if e["src"] not in own and e["src"] not in bset and e["src"] in depth:
                    direct[e["src"]].add(e["dst"])
    rows = {}
    ids = list(depth)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in st.q(f"SELECT id,kind,name,fqn,file,line,module,entry_kind,attrs FROM nodes WHERE id IN ({q})", chunk):
            rows[r["id"]] = with_generated(dict(r))
    entries = [dict(rows[n], depth=d, path=paths[n], path_confidence=path_confidence(paths[n]),
                    **({"candidate": True} if path_candidate(paths[n]) else {})) for n, d in depth.items()
               if n in rows and rows[n]["entry_kind"]]
    callers = []
    typed = _type_level_callers(st, [n for n, d in depth.items() if d and n in rows and rows[n]["kind"] == "class"
                                     and (paths.get(n) or [{}])[0].get("kind") == "CALLS"])
    for n, d in depth.items():
        if n not in rows or (rows[n]["kind"] not in CODE_KINDS + ("http",) and n not in typed) or d == 0:
            continue
        c = dict(rows[n], depth=d, **_first_hop(paths.get(n)))
        if n in typed:                     # a call in a property initializer / Kotlin accessor of that type (#72)
            c["in_property"] = True
        p = paths.get(n) or []
        if path_candidate(p):
            c["candidate"] = True
        if p and p[0]["to"] in bases:      # calls the base declaration; reaches the target through the override
            c["via_base"] = fq.get(p[0]["to"], p[0]["to"])
        if n in direct:    # calls an override (not the target itself): reaches the target's API through it
            c["via_override"] = sorted(fq.get(x, x) for x in direct[n])
        callers.append(c)
    out = {"targets": targets, "entry_points": sorted(entries, key=lambda x: (x["entry_kind"], x["name"])),
           "callers": sorted(callers, key=lambda x: (x["depth"], x["fqn"] or "")), **rel}
    if platform:
        from .platforms import filter_info
        out["platform"] = filter_info(st, platform)
        out["platform"]["targets_not_built"] = _not_built(st, targets, platform)
    return out


TYPE_LEVEL_LANGS = ("swift", "kotlin")


def _type_level_callers(st: GraphStore, ids: list[str]) -> set[str]:
    """Swift / Kotlin type nodes that call the target themselves: the call sits in a stored property's initializer
    or (Kotlin) a custom getter / setter, which have no node of their own. They count as callers, labelled
    `(in a property)` (#72), rather than leaving "no callers"."""
    out = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        out |= {r["id"] for r in st.q(f"SELECT id FROM nodes WHERE id IN ({','.join('?' * len(chunk))}) "
                                      f"AND lang IN ({','.join('?' * len(TYPE_LEVEL_LANGS))})",
                                      (*chunk, *TYPE_LEVEL_LANGS))}
    return out


def _file_of_id(nid: str) -> str:
    """The file part of a node id (`method:src/a.ts#X.m` -> src/a.ts), else the id."""
    body = nid.split(":", 1)[1] if ":" in nid else nid
    return body.partition("#")[0] if "#" in body else nid


def override_lines(res: dict, limit: int = 8) -> list[str]:
    """'overrides: Base.m' / 'overridden by: A.m, B.m' lines for an impact result (the relation, not callers)."""
    out = inherited_lines(res)
    for key, label in (("overrides", "overrides"), ("overridden_by", "overridden by")):
        items = list({x["id"]: x for x in res.get(key) or []}.values())
        ids_of = defaultdict(set)
        for x in items:
            ids_of[x["fqn"]].add(x["id"])
        # two declarations with one name (two `FeedAPI` interfaces): each with its file (#62)
        xs = list(dict.fromkeys(x["fqn"] if len(ids_of[x["fqn"]]) < 2 else f"{x['fqn']} ({_file_of_id(x['id'])})"
                                for x in items))
        if xs:
            out.append(f"{label}: {', '.join(xs[:limit])}" + (f" …+{len(xs) - limit}" if len(xs) > limit else ""))
    return out


def prop_fields(st: GraphStore, spec: str) -> list[str]:
    """Stored-property field nodes for `Type.prop` / `field:Type.prop` / a qualified `Module.Type.prop` (#88)."""
    spec = spec[len("field:"):] if spec.startswith("field:") else spec
    spec = spec[len("property:"):] if spec.startswith("property:") else spec
    if ("." not in spec and "::" not in spec) or spec.startswith(("table:", "column:")):
        return []
    # PHP: `User.name`, `User::$name`, `App\Models\User::$name` -> the `property:` node (fqn `App\Models\User::$name`)
    cls, _, prop = spec.replace("::$", "::").replace("::", ".").rpartition(".")
    php = f"{cls}::${prop.lstrip('$')}"
    return [r["id"] for r in st.q("""SELECT id FROM nodes WHERE kind IN ('field', 'property')
                                     AND attrs LIKE '%"property": "stored"%'
                                     AND (fqn=? OR fqn LIKE ? OR fqn=? OR fqn LIKE ?) ORDER BY id""",
                                  (spec, f"%.{spec}", php, f"%\\{php}"))]


def prop_access(st: GraphStore, spec: str, kind: str) -> list[dict]:
    """READS_PROP / WRITES_PROP edges into the field nodes of `spec`, with the entry kinds reaching each site. Test code's
    accesses (retyped TEST_USES with attrs.orig = kind) come last, with `test: True`."""
    fids = prop_fields(st, spec)
    if not fids:
        return []
    q = ",".join("?" * len(fids))
    rows = st.q(f"""SELECT e.src, e.kind, e.dst, e.file, e.line, e.confidence, e.attrs, n.module, n.fqn
                    FROM edges e JOIN nodes n ON n.id = e.src
                    WHERE (e.kind=? OR (e.kind='TEST_USES' AND e.attrs LIKE ?)) AND e.dst IN ({q})
                    ORDER BY e.kind='TEST_USES', n.module, n.fqn, e.line""", (kind, f'%"orig": "{kind}"%', *fids))
    ents = entry_info(st, list({r["src"] for r in rows}))
    out = []
    for r in rows:
        d = dict(r)
        d["attrs"] = json.loads(d["attrs"] or "{}")
        d["test"] = r["kind"] == "TEST_USES"
        d["entry_kinds"] = {k: v[0] for k, v in ents.get(r["src"], {}).items()}
        out.append(d)
    return out


def readers(st: GraphStore, spec: str) -> list[dict]:
    """Code that reads a stored property `Type.prop` (READS_PROP)."""
    return prop_access(st, spec, "READS_PROP")


def writers(st: GraphStore, table: str) -> list[dict]:
    if prop_fields(st, table):          # `writers Type.prop`: a stored property (#88)
        return prop_access(st, table, "WRITES_PROP")
    table = table[len("table:"):] if table.startswith("table:") else table   # `writers table:X` == `writers X`
    rows = st.q("""SELECT e.src, e.kind, e.dst, e.file, e.line, e.confidence, n.module, n.fqn
                   FROM edges e JOIN nodes n ON n.id = e.src
                   WHERE (e.kind='WRITES_TABLE' AND e.dst=?) OR (e.kind='WRITES_COLUMN' AND e.dst LIKE ?)
                   ORDER BY n.module, n.fqn, e.line""", (f"table:{table}", f"column:{table}.%"))
    ents = entry_info(st, list({r["src"] for r in rows}))
    out = []
    for r in rows:
        d = dict(r)
        d["entry_kinds"] = {k: v[0] for k, v in ents.get(r["src"], {}).items()}
        out.append(d)
    return out


def siblings(st: GraphStore, spec: str, limit=40) -> dict:
    ids = resolve_targets(st, spec)
    res = {"targets": ids, "hierarchy": [], "same_method_in_siblings": [], "shared_resources": [], "co_callers": []}
    if not ids:
        return res
    main = ids[0]
    node = st.node(main)
    cls_fqn = node["fqn"].split("::")[0] if node["fqn"] else None
    cls_row = st.q("SELECT id FROM nodes WHERE fqn=? AND kind IN ('class','interface','trait','enum')", (cls_fqn,))
    if cls_row:
        cid = cls_row[0]["id"]
        for r in st.q("""SELECT e.kind, e.dst AS parent, s.src AS sibling FROM edges e
                         JOIN edges s ON s.dst = e.dst AND s.kind = e.kind AND s.src != e.src
                         WHERE e.src=? AND e.kind IN ('EXTENDS','IMPLEMENTS','USES_TRAIT')""", (cid,)):
            res["hierarchy"].append(dict(r))
        if node["kind"] == "method":
            sib_classes = {r["sibling"] for r in res["hierarchy"]}
            for sc in sib_classes:
                fq = sc.split(":", 1)[1]
                for m in st.q("SELECT id, file, line FROM nodes WHERE kind='method' AND fqn=?", (f"{fq}::{node['name']}",)):
                    res["same_method_in_siblings"].append(dict(m))
    if node["kind"] in ("method", "function"):
        res_kinds = ("READS_COLUMN", "WRITES_COLUMN", "MENTIONS_COLUMN", "READS_TABLE", "WRITES_TABLE", "READS_CONFIG",
                     "WRITES_CONFIG", "READS_ENV", "USES_CONNECTION", "REGISTERS_CONNECTION")
        kq = ",".join("?" * len(res_kinds))
        mine = {r["dst"] for r in st.q(f"SELECT DISTINCT dst FROM edges WHERE src=? AND kind IN ({kq})", (main, *res_kinds))}
        if mine:
            q = ",".join("?" * len(mine))
            agg = defaultdict(set)
            for r in st.q(f"SELECT src, dst FROM edges WHERE dst IN ({q}) AND kind IN ({kq}) AND src != ?", (*mine, *res_kinds, main)):
                agg[r["src"]].add(r["dst"])
            res["shared_resources"] = sorted(({"node": k, "shared": sorted(v)} for k, v in agg.items()), key=lambda x: -len(x["shared"]))[:limit]
        callees = {r["dst"] for r in st.q("SELECT DISTINCT dst FROM edges WHERE src=? AND kind='CALLS' AND dst LIKE 'method:%'", (main,))}
        callees = {c for c in callees if not c.startswith(f"method:{cls_fqn}::")}
        if callees:
            q = ",".join("?" * len(callees))
            agg = defaultdict(set)
            for r in st.q(f"SELECT src, dst FROM edges WHERE dst IN ({q}) AND kind='CALLS' AND src != ?", (*callees, main)):
                agg[r["src"]].add(r["dst"])
            res["co_callers"] = sorted(({"node": k, "shared_callees": sorted(v), "jaccard": round(len(v) / len(callees | v), 2)}
                                        for k, v in agg.items()), key=lambda x: (-len(x["shared_callees"]), x["node"]))[:limit]
    return res


DOWNSTREAM_SINKS = ("table", "column", "connection", "config", "env", "route", "http", "job", "command",
                    "unsafe", "ffi", "feature", "cfg", "define", "endpoint")


def downstream(st: GraphStore, spec: str, min_conf="heuristic", max_depth=30, kinds=None, sinks=DOWNSTREAM_SINKS,
               gate: str | None = "auto", platform: str | None = None) -> dict:
    """Forward closure: everything the target depends on (e.g. a frontend page -> composables ->
    HTTP endpoints -> backend routes -> controllers/services -> tables). Returns reached sink nodes
    (tables, columns, routes, ...) with one shortest evidence path each. With a gate scenario,
    sinks reachable only through gated edges are flagged."""
    if gate == "auto":
        gate = default_gate(st)
    targets = resolve_targets(st, spec)
    kinds = kinds or PROPAGATING
    kset = set(kinds)
    rank = CONFIDENCE_RANK[min_conf]
    px = _px(st, platform)
    prev, depth = {}, {t: 0 for t in targets}
    frontier = list(targets)
    live = set(targets)
    while frontier:
        nxt = []
        chunk_all = frontier
        for i in range(0, len(chunk_all), 500):
            chunk = chunk_all[i:i + 500]
            q = ",".join("?" * len(chunk))
            for e in st.q(f"SELECT id,src,dst,kind,file,line,confidence,conf_rank,gate FROM edges WHERE src IN ({q})", chunk):
                if e["kind"] not in kset or e["conf_rank"] < rank or (px and e["id"] in px):
                    continue
                d = e["dst"]
                if d not in depth and depth[e["src"]] < max_depth:
                    depth[d] = depth[e["src"]] + 1
                    prev[d] = dict(e)
                    nxt.append(d)
                if e["src"] in live and not (gate and e["gate"] == gate):
                    live.add(d)
        frontier = nxt
    # live set needs a fixpoint (edges seen before their src became live)
    changed = True
    while changed and gate:
        changed = False
        ids = list(live)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            for e in st.q(f"SELECT id,src,dst,kind,conf_rank,gate FROM edges WHERE src IN ({q})", chunk):
                if e["id"] in px:
                    continue
                if e["kind"] in kset and e["conf_rank"] >= rank and e["gate"] != gate and e["dst"] in depth and e["dst"] not in live:
                    live.add(e["dst"]); changed = True
    out = defaultdict(list)
    nodes = {}
    ids = [n for n in depth if n.split(":", 1)[0] in sinks]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in st.q(f"SELECT id,kind,name,file,line FROM nodes WHERE id IN ({q})", chunk):
            nodes[r["id"]] = dict(r)
    for nid in ids:
        path, x = [], nid
        while x in prev:
            e = prev[x]
            path.append({"from": e["src"], "kind": e["kind"], "to": e["dst"], "at": f"{e['file']}:{e['line']}", "confidence": e["confidence"],
                         **({"gated": e["gate"]} if e["gate"] else {})})
            x = e["src"]
        path.reverse()
        n = nodes.get(nid, {"id": nid, "kind": nid.split(":", 1)[0], "name": nid})
        out[n["kind"]].append({**n, "depth": depth[nid], "path": path, "path_confidence": path_confidence(path),
                               "live": (nid in live) if gate else True})
    for k in out:
        out[k].sort(key=lambda x: (x["depth"], x["id"]))
    # tables touched directly (READS/WRITES_TABLE) or through any of their columns
    touched = {}
    for c in out.get("column", []) + out.get("table", []):
        t = c["id"].split(":", 1)[1].split(".")[0] if c["kind"] == "column" else c["id"].split(":", 1)[1]
        cur = touched.get(t)
        if cur is None or c["depth"] < cur["depth"]:
            touched[t] = {"table": t, "depth": c["depth"], "via": c["id"], "live": c["live"], "path_confidence": c["path_confidence"]}
        elif c["live"]:
            cur["live"] = True
    res = {"targets": targets, "gate": gate, "reached": len(depth), "sinks": dict(out),
           "tables_touched": sorted(touched.values(), key=lambda x: (x["depth"], x["table"]))}
    if platform:
        from .platforms import filter_info
        res["platform"] = filter_info(st, platform)
        res["platform"]["targets_not_built"] = _not_built(st, targets, platform)
    return res


def path_between(st: GraphStore, src_spec: str, dst_spec: str, min_conf="heuristic", max_depth=30,
                 platform: str | None = None) -> list[dict]:
    """Shortest forward dependency path from any node of src_spec to any node of dst_spec.
    A table target with no direct path falls back to its columns (code mostly reaches a table through column
    reads/writes), the same rule the visual view uses."""
    srcs, dsts = resolve_targets(st, src_spec), set(resolve_targets(st, dst_spec))
    if platform:
        from .platforms import exclusions
        xn = exclusions(st, platform)["nodes"]
        srcs, dsts = [s for s in srcs if s not in xn], {d for d in dsts if d not in xn}
    p = _bfs_path(st, srcs, dsts, min_conf, max_depth, platform)
    if not p:
        cols = set()
        for t in [d for d in dsts if d.startswith("table:")]:
            pre = "column:" + t[6:] + "."
            cols.update(r["id"] for r in st.q("SELECT id FROM nodes WHERE kind='column' AND substr(id, 1, ?) = ?", (len(pre), pre)))
        if cols:
            p = _bfs_path(st, srcs, cols, min_conf, max_depth, platform)
    return p


def _bfs_path(st: GraphStore, srcs: list[str], dsts: set[str], min_conf: str, max_depth: int,
              platform: str | None = None) -> list[dict]:
    kset, rank = set(PROPAGATING), CONFIDENCE_RANK[min_conf]
    px = _px(st, platform)
    prev, seen, frontier = {}, set(srcs), list(srcs)
    for _ in range(max_depth):
        nxt = []
        for i in range(0, len(frontier), 500):
            chunk = frontier[i:i + 500]
            q = ",".join("?" * len(chunk))
            for e in st.q(f"SELECT id,src,dst,kind,file,line,confidence,conf_rank,gate,attrs FROM edges WHERE src IN ({q})", chunk):
                if e["kind"] in kset and e["conf_rank"] >= rank and e["dst"] not in seen and not (px and e["id"] in px):
                    seen.add(e["dst"]); prev[e["dst"]] = dict(e); nxt.append(e["dst"])
                    if e["dst"] in dsts:
                        path, x = [], e["dst"]
                        while x in prev:
                            p = prev[x]
                            pl = json.loads(p["attrs"]).get("platforms") if p["attrs"] and '"platforms"' in p["attrs"] else None
                            path.append({"from": p["src"], "kind": p["kind"], "to": p["dst"], "at": f"{p['file']}:{p['line']}",
                                         "confidence": p["confidence"], **({"gated": p["gate"]} if p["gate"] else {}),
                                         **({"platforms": pl} if pl is not None else {})})
                            x = p["src"]
                        return path[::-1]
        frontier = nxt
        if not frontier:
            break
    return []


def render_downstream(res: dict, show_paths=True, max_per_kind=60, kinds_order=("route", "table", "column", "connection", "config", "env", "job", "command", "http",
                                                                               "unsafe", "ffi", "feature", "cfg", "define", "endpoint")) -> str:
    out = [f"targets: {', '.join(res['targets'][:6])}", f"reached nodes: {res['reached']}"]
    if res.get("platform"):
        from .platforms import render_filter
        out.insert(0, render_filter(res["platform"]))
    tt = res.get("tables_touched") or []
    if tt:
        out.append("")
        out.append(f"== TABLES TOUCHED (directly or via columns): {len(tt)}")
        out.append("  " + ", ".join(f"{t['table']}{'' if t['live'] else '[GATED-ONLY]'}" for t in tt))
    for k in kinds_order:
        items = res["sinks"].get(k) or []
        if not items:
            continue
        out.append("")
        out.append(f"== {k.upper()}: {len(items)}" + (f" (gated-only for '{res['gate']}': {sum(1 for i in items if not i['live'])})" if res.get("gate") else ""))
        for i in items[:max_per_kind]:
            g = "" if i["live"] else "  [GATED-ONLY]"
            out.append(f"  {i['id']}  depth={i['depth']} conf={i['path_confidence']}{g}")
            if show_paths:
                s = i["path"][0]["from"] if i["path"] else i["id"]
                for p in i["path"]:
                    s += f"\n          -{p['kind']}[{p['confidence']} @ {p['at']}{' GATED' if p.get('gated') else ''}]-> {p['to']}"
                out.append(f"      path: {s}")
        if len(items) > max_per_kind:
            out.append(f"  ... {len(items) - max_per_kind} more")
    return "\n".join(out)


def api_calls(st: GraphStore, flt: str = "all") -> list[dict]:
    """Client HTTP endpoints with call sites and matched backend routes (combined DB).
    flt: 'all' | 'unmatched' | substring of the endpoint/route/caller/file | a glob with `*` (any characters, `/`
    included) matched against the endpoint (`GET /v1/*/orders*`), its path, a matched route, controller or caller,
    or a call-site file (`*useOrders*`); a glob without a verb matches every verb. Case-insensitive."""
    eps = {r["id"]: dict(r) for r in st.q("SELECT id, name, attrs FROM nodes WHERE kind='http'")}
    calls = defaultdict(list)
    for e in st.q("SELECT src, dst, file, line, confidence, attrs FROM edges WHERE kind='HTTP_CALLS'"):
        a = json.loads(e["attrs"] or "{}")
        calls[e["dst"]].append({"caller": e["src"], "at": f"{e['file']}:{e['line']}", "confidence": e["confidence"],
                                "url": a.get("url"), "via_helper": a.get("via_helper")})
    routes = defaultdict(list)
    for e in st.q("SELECT src, dst, confidence, attrs FROM edges WHERE kind='MATCHES_ROUTE'"):
        ctl = [r["dst"] for r in st.q("SELECT dst FROM edges WHERE src=? AND kind='ROUTES_TO'", (e["dst"],))]
        routes[e["src"]].append({"route": e["dst"], "confidence": e["confidence"], "controller": ctl,
                                 "uri_variant": json.loads(e["attrs"] or "{}").get("uri_variant")})
    out = []
    for nid, n in eps.items():
        row = {"endpoint": nid, "attrs": json.loads(n["attrs"] or "{}"), "calls": calls.get(nid, []), "routes": routes.get(nid, [])}
        if flt == "unmatched" and row["routes"]:
            continue
        if flt not in ("all", "unmatched") and not _api_row_matches(flt, row, n["name"]):
            continue
        out.append(row)
    return sorted(out, key=lambda r: r["endpoint"])


def _api_row_matches(flt: str, row: dict, name: str | None) -> bool:
    if "*" not in flt:
        return flt in json.dumps(row) or flt in (name or "")
    rx = re.compile("^" + ".*".join(re.escape(x) for x in flt.strip().split("*")) + "$", re.I)
    a = row["attrs"]
    fields = [name or "", row["endpoint"], row["endpoint"].split(":", 1)[-1], a.get("path") or ""]
    if re.match(r"^[A-Za-z]+\s", flt.strip()) is None:           # no verb: the path part alone also counts
        fields += [f"{a.get('method') or ''} {a.get('path') or ''}"]
    for r in row["routes"]:
        fields += [r["route"], r["route"].split(":", 1)[-1], *r["controller"]]
    for c in row["calls"]:
        fields += [c["caller"], c["at"], (c["at"] or "").rsplit(":", 1)[0], c.get("url") or ""]
        if c.get("via_helper"):
            fields += [c["via_helper"].get("fn") or "", (c["via_helper"].get("at") or "").rsplit(":", 1)[0]]
    return any(rx.match(f or "") for f in fields)


def render_api_calls(rows: list[dict], max_calls=4) -> str:
    out = [f"{len(rows)} client endpoints ({sum(1 for r in rows if r['routes'])} matched)"]
    for r in rows:
        a = r.get("attrs") or {}
        tag = "  (called from tests only)" if a.get("test_only") else ""
        if a.get("base"):
            tag += f"  (base {a['base']['placeholder']} = {a['base']['value']}, {a['base']['from']})"
        out.append(f"{r['endpoint']}{tag}")
        for m in r["routes"]:
            out.append(f"   => {m['route']} [{m['confidence']}] -> {', '.join(m['controller']) or '?'}")
        if not r["routes"]:
            out.append("   => (unmatched)")
        for c in r["calls"][:max_calls]:
            h = f" via {c['via_helper']['fn']}" if c.get("via_helper") else ""
            out.append(f"   <- {c['caller']} @ {c['at']} [{c['confidence']}]{h}")
        if len(r["calls"]) > max_calls:
            out.append(f"   <- ... {len(r['calls']) - max_calls} more")
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------- display helpers
SHORT_CODE_PREFIXES = ("method:", "class:", "function:", "interface:", "trait:", "enum:", "struct:", "union:", "typedef:",
                       "type_alias:", "macro:", "global:", "ffi:", "field:", "const:", "static:", "composable:", "store:")


def short_id(x: str | None) -> str:
    """Node id without the code-kind prefix and the root `App\\` namespace (routes, tables, pages keep their prefix)."""
    if not x:
        return "?"
    if x.startswith(SHORT_CODE_PREFIXES):
        x = x.split(":", 1)[1]
        if "#" in x:
            f, _, q = x.partition("#")
            return f"{q} ({os.path.basename(f)})"
    return x[4:] if x.startswith("App\\") else x


# ----------------------------------------------------------------------------------------------- search
GUARD_KEYS = ("middleware", "guards", "interceptors", "pipes", "auth", "access", "wrapped_by")


def search(st: GraphStore, name: str, kind: str | None = None, limit: int = 20, platform: str | None = None) -> dict:
    """Nodes whose name / FQN / id contains `name` (case-insensitive), plus routes whose middleware, guards, auth or
    access checks contain it (those are route attributes and USES_MIDDLEWARE edges, not nodes of their own)."""
    q = "SELECT id, kind, file, line, attrs FROM nodes WHERE (name LIKE ? OR fqn LIKE ? OR id LIKE ?)"
    p: list = [f"%{name}%"] * 3
    if kind:
        q += " AND kind=?"
        p.append(kind)
    xn = set()
    if platform:
        from .platforms import exclusions
        xn = exclusions(st, platform)["nodes"]
    q += " ORDER BY length(id) LIMIT ?"
    p.append(limit + len(xn))
    rows = st.q(q, p)
    hidden = sum(1 for r in rows if r["id"] in xn)
    nodes = [with_generated(dict(r)) for r in rows if r["id"] not in xn][:limit]
    guards: dict[str, list[dict]] = defaultdict(list)
    if kind in (None, "route", "middleware", "guard"):
        low = name.lower()
        for r in st.q("SELECT id, file, line, attrs FROM nodes WHERE kind='route' AND attrs LIKE ?", (f"%{name}%",)):
            if r["id"] in xn:
                continue
            a = json.loads(r["attrs"] or "{}")
            for k in GUARD_KEYS:
                v = a.get(k)
                vals = v if isinstance(v, list) else [v]
                for x in vals:
                    nm = x.get("name") if isinstance(x, dict) else x
                    if nm and low in str(nm).lower():
                        guards[str(nm)].append({"route": r["id"], "file": r["file"], "line": r["line"], "via": k})
            for c in a.get("conditions") or []:
                if isinstance(c, str) and c.startswith("wrapped:") and low in c.lower():
                    guards[c[8:]].append({"route": r["id"], "file": r["file"], "line": r["line"], "via": "urlconf wrapper"})
        for e in st.q("SELECT src, dst, file, line, attrs FROM edges WHERE kind='USES_MIDDLEWARE' AND src LIKE 'route:%' "
                      "AND (attrs LIKE ? OR dst LIKE ?)", (f"%{name}%", f"%{name}%")):
            nm = json.loads(e["attrs"] or "{}").get("name") or short_id(e["dst"])
            if low in nm.lower() or low in e["dst"].lower():
                if not any(g["route"] == e["src"] for g in guards.get(nm, [])):
                    guards[nm].append({"route": e["src"], "file": e["file"], "line": e["line"], "via": "USES_MIDDLEWARE"})
    out = {"query": name, "kind": kind, "nodes": nodes, "guards": dict(sorted(guards.items()))}
    if platform:
        from .platforms import filter_info
        out["platform"] = {**filter_info(st, platform), "matches_not_built": hidden}
    return out


def render_search(res: dict, limit_routes: int = 8) -> str:
    out = []
    if res.get("platform"):
        from .platforms import render_filter
        out.append(render_filter(res["platform"]))
        if res["platform"].get("matches_not_built"):
            out.append(f"{res['platform']['matches_not_built']} matching symbols are not built for {res['platform']['platform']} "
                       f"(search without platform lists them)")
    for r in res["nodes"]:
        out.append(f"{r['kind']:10} {r['id']}  {r['file'] or '?'}:{r['line']}{platform_label(r)}")
    if res["guards"]:
        if out:
            out.append("")
        n = sum(len(v) for v in res["guards"].values())
        out.append(f"middleware / guards / auth matching '{res['query']}' (route attributes): {len(res['guards'])} name(s) on {n} route(s)")
        for nm, rs in res["guards"].items():
            shown = ", ".join(x["route"].split(":", 1)[1] for x in rs[:limit_routes])
            out.append(f"  {nm}  ({rs[0]['via']}) on {len(rs)} route(s): {shown}{' …' if len(rs) > limit_routes else ''}")
    if not res["nodes"] and not res["guards"]:
        out.append(f"no matches for {res['query']!r}" + (f" with kind={res['kind']}" if res.get("kind") else "") +
                   " in node names, FQNs, ids or route middleware / guard / auth names. Try a shorter substring, drop the "
                   "kind filter, or use `routes` to list every route with its guards.")
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------- empty-result help
def explain_siblings(st: GraphStore, spec: str, res: dict) -> str:
    """Why siblings() found nothing for a resolved symbol, with the queries that answer the likely question."""
    main = res["targets"][0]
    n = dict(st.node(main) or {})
    s = short_id(main)
    why, tips = [], []
    fq = (n.get("fqn") or "").split("::")[0]
    if not st.q("SELECT 1 FROM edges e JOIN nodes c ON c.id=e.src WHERE c.fqn=? AND e.kind IN ('EXTENDS','IMPLEMENTS','USES_TRAIT') LIMIT 1", (fq,)):
        why.append("its class has no parent class, interface or trait shared with other classes")
    res_kinds = ("READS_COLUMN", "WRITES_COLUMN", "MENTIONS_COLUMN", "READS_TABLE", "WRITES_TABLE", "READS_CONFIG",
                 "WRITES_CONFIG", "READS_ENV", "USES_CONNECTION", "REGISTERS_CONNECTION")
    kq = ",".join("?" * len(res_kinds))
    if not st.q(f"SELECT 1 FROM edges WHERE src=? AND kind IN ({kq}) LIMIT 1", (main, *res_kinds)):
        why.append("it touches no table, column, config, env key or connection directly")
    callees = [r["dst"] for r in st.q("SELECT DISTINCT dst FROM edges WHERE src=? AND kind='CALLS'", (main,))]
    own = [c for c in callees if fq and c.startswith(f"method:{fq}::")]
    if callees and len(own) == len(callees):
        why.append(f"all {len(callees)} of its callees are in its own class, which co-caller matching skips")
    elif not callees:
        why.append("it calls no other indexed method")
    data_callees = [c for c in callees if st.q(f"SELECT 1 FROM edges WHERE src=? AND kind IN ({kq}) LIMIT 1", (c, *res_kinds))]
    for c in data_callees[:3]:
        tips.append(f"siblings('{short_id(c)}') (a callee that touches data)")
    tips.append(f"impact('{s}') for its callers and entry points")
    tips.append(f"downstream('{s}') for the tables, config and connections it reaches")
    return (f"no siblings found for {s}: " + ("; ".join(why) or "no shared parents, resources or callees") +
            ".\ntry: " + "; ".join(tips))


def explain_no_callers(st: GraphStore, spec: str, targets: list[str], min_conf: str = "heuristic") -> str:
    if not targets:
        return f"no method matches {spec!r}; try search() with part of the name."
    t = targets[0]
    n = dict(st.node(t) or {})
    s = short_id(t)
    if CONFIDENCE_RANK.get(min_conf, 1) > CONFIDENCE_RANK["heuristic"]:
        # callers exist, below the confidence threshold: name the filter, not "no callers" (#75)
        q = ",".join("?" * len(targets))
        low = st.q(f"SELECT kind, confidence, count(*) c FROM edges WHERE dst IN ({q}) AND kind IN ({','.join('?' * len(PROPAGATING))}) "
                   f"AND conf_rank < ? GROUP BY kind, confidence ORDER BY c DESC", (*targets, *PROPAGATING, CONFIDENCE_RANK[min_conf]))
        if low:
            langs = {r["lang"] for r in st.q(f"SELECT DISTINCT lang FROM nodes WHERE id IN ({q})", tuple(targets))}
            why = {"swift": "Swift resolved / exact edges need the compiler index store (docs/swift.md#exact-mode)",
                   "kotlin": "Kotlin resolved / exact edges need scip-java (docs/kotlin.md#exact-mode)",
                   "rust": "Rust exact edges need rust-analyzer (docs/native.md)",
                   "c": "C / C++ exact edges need scip-clang and a compile_commands.json (docs/native.md)",
                   "cpp": "C / C++ exact edges need scip-clang and a compile_commands.json (docs/native.md)"}
            notes = [why[x] for x in sorted(x for x in langs if x in why)]
            return (f"{s}: no callers at --min-confidence {min_conf}; "
                    + ", ".join(f"{r['c']} {r['kind']} edge{'s' if r['c'] != 1 else ''} ({r['confidence']})" for r in low)
                    + f" are below the threshold. Rerun with --min-confidence heuristic (min_confidence='heuristic') to see them"
                    + (f"; {'; '.join(notes)}" if notes else "") + ".")
    if n.get("entry_kind"):
        return f"{s} has no recorded callers; it is itself an entry point ({n['entry_kind']}). try: downstream('{s}') for what it reaches."
    refs = st.q("SELECT kind, count(*) c FROM edges WHERE dst=? GROUP BY kind", (t,))
    other = ", ".join(f"{r['kind']}×{r['c']}" for r in refs)
    from .coverage import answer_note, completeness_for, possibly_more
    comp = completeness_for(st, targets)
    more = possibly_more(comp)
    if more:
        return (f"{s}: no callers found in indexed code (blind spots: {more})" + (f"; other incoming edges: {other}" if other else "")
                + f". try: reaches('{s}') for every dependent over all edge kinds; search('{n.get('name') or spec}') for "
                f"similarly named code.\n" + answer_note(comp))
    tested = sum(r["c"] for r in refs if r["kind"] in ("TEST_CALLS", "TEST_USES", "TEST_HTTP"))
    return (f"{s} has no recorded callers" + (f" (other incoming edges: {other})" if other else "") +
            (f". Only test code uses it: tests('{s}') lists the tests" if tested else "") +
            ". It may be called dynamically (string callables, container lookups, framework hooks) or be unused. "
            f"try: reaches('{s}') for every dependent over all edge kinds; search('{n.get('name') or spec}') for similarly named code.")


def explain_no_writers(st: GraphStore, table: str, what: str = "writers") -> str:
    if prop_fields(st, table):
        return (f"no {what} recorded for property {table!r}. Recorded: `self.x` / bare `x` inside its type and `v.x` with "
                "a known type of `v`; access through an unknown receiver, a `$binding` or a key path is not modelled.")
    if what == "readers":
        return f"no stored property {table!r} in the graph (Swift stored properties are `field:` nodes; try `cg search {table}`)."
    t = table.split(":", 1)[1] if table.startswith("table:") else table
    if not st.q("SELECT 1 FROM nodes WHERE id=?", (f"table:{t}",)):
        near = [r["id"][6:] for r in st.q("SELECT id FROM nodes WHERE kind='table' AND id LIKE ? ORDER BY id LIMIT 6", (f"%{t}%",))]
        allt = [r["id"][6:] for r in st.q("SELECT id FROM nodes WHERE kind='table' ORDER BY id LIMIT 12")]
        return (f"no table {t!r} in the graph. " + (f"similar: {', '.join(near)}" if near else f"tables: {', '.join(allt) or '(none)'}")
                + ". Use the DB table name (Django: `<app>_<model>` or Meta.db_table).")
    reads = st.q("SELECT count(*) c FROM edges WHERE kind IN ('READS_TABLE','READS_COLUMN','MENTIONS_COLUMN') AND (dst=? OR dst LIKE ?)",
                 (f"table:{t}", f"column:{t}.%"))[0]["c"]
    return (f"no writers recorded for table {t!r} ({reads} read/mention edges). Writes through raw SQL, bulk helpers or admin "
            f"form saves may not be modelled. try: reaches(['table:{t}']) for every dependent; routes(writes='{t}') after "
            "indexing the code that writes it.")


def explain_no_path(st: GraphStore, src: str, dst: str, min_conf: str = "heuristic") -> str:
    a, b = resolve_targets(st, src), resolve_targets(st, dst)
    if not a:
        return f"no path: source {src!r} matches no node; try search()."
    if not b:
        return f"no path: target {dst!r} matches no node; try search()."
    rev = _bfs_path(st, b, set(a), min_conf, 30)
    if rev:
        return (f"no path from {src} to {dst}, but there is one in the other direction ({len(rev)} hops): "
                f"try path('{dst}', '{src}').")
    return (f"no forward path from {src} to {dst} over dependency edges (min_confidence={min_conf}). "
            f"try: downstream('{src}') for everything it reaches; reaches(['{dst}']) for everything that depends on the target"
            + ("; a lower min_confidence" if min_conf != "heuristic" else "") + ".")


# ----------------------------------------------------------------------------------------------- client keys dropped by a helper
def _known_keys(ha: dict) -> set[str] | None:
    """Keys a client request can send (always + conditional), or None when the request params are not statically known."""
    ks = [x for x in (ha.get("query_keys"), ha.get("body_keys")) if x]
    if not ks or any(k.get("opaque") for k in ks):
        return None
    return {x for k in ks for x in (k.get("keys") or []) + (k.get("conditional") or [])}


def forwarding_gaps(st: GraphStore, issuers: list[str] | None = None) -> list[dict]:
    """Call sites that pass keys to a request-issuing function which never puts them on the request ("sent but not
    forwarded"), e.g. a page passes {category_id, date_from} to fetchTop, whose params builder only sets category_id.
    Uses the HTTP_CALLS query/body key facts and the call-site argument keys (CALLS attrs.arg_keys)."""
    out = []
    sql = "SELECT src, dst, file, line, attrs FROM edges WHERE kind='HTTP_CALLS'"
    rows = st.q(sql) if issuers is None else [r for i in issuers for r in st.q(sql + " AND src=?", (i,))]
    for h in rows:
        ha = json.loads(h["attrs"] or "{}")
        known = _known_keys(ha)
        if known is None:
            continue
        fwd = next((x for x in (ha.get("query_keys"), ha.get("body_keys")) if x and x.get("forwarded_index") is not None), None)
        fi = fwd.get("forwarded_index") if fwd else None
        for c in st.q("SELECT src, file, line, attrs FROM edges WHERE kind='CALLS' AND dst=?", (h["src"],)):
            ca = json.loads(c["attrs"] or "{}")
            ak = ca.get("arg_keys")
            if not ak:
                continue
            if isinstance(fi, int):
                ak = [ak[fi]] if fi < len(ak) else []
            elif len([x for x in ak if x]) != 1:
                continue  # several object arguments and no recorded forwarded parameter: cannot tell which one is sent
            passed = sorted({k for ks in ak for k in (ks or [])})
            cond = sorted({k for ks in (ca.get("arg_keys_conditional") or []) for k in (ks or [])})
            dropped = [k for k in passed + cond if k not in known]
            if dropped:
                out.append({"endpoint": h["dst"], "issuer": h["src"], "request_at": f"{h['file']}:{h['line']}",
                            "caller": c["src"], "call_at": f"{c['file']}:{c['line']}", "passed": passed + cond,
                            "request_keys": sorted(known), "dropped": sorted(set(dropped))})
    return out


def path_notes(st: GraphStore, path: list[dict]) -> list[str]:
    """Notes for a path: keys a call site passes that the next hop's request never sends."""
    notes = []
    for a, b in zip(path, path[1:]):
        if a["kind"] == "CALLS" and b["kind"] == "HTTP_CALLS" and a["to"] == b["from"]:
            for g in forwarding_gaps(st, [b["from"]]):
                if g["caller"] == a["from"] and g["endpoint"] == b["to"]:
                    notes.append(f"sent but not forwarded: {', '.join(g['dropped'])} (passed @ {g['call_at']}; the request built "
                                 f"@ {g['request_at']} sends only {', '.join(g['request_keys'])})")
    return notes


def render_siblings(st: GraphStore, spec: str, res: dict, limit: int = 15) -> str:
    if not res["targets"]:
        return f"no symbol matches {spec!r}; try `search` with part of the name"
    out = [f"target: {short_id(res['targets'][0])}"]
    if not any(res[k] for k in ("hierarchy", "same_method_in_siblings", "shared_resources", "co_callers")):
        return "\n".join(out + [explain_siblings(st, spec, res)])
    for h in res["hierarchy"][:limit]:
        out.append(f"hierarchy   {h['kind']} {short_id(h['parent'])}: {short_id(h['sibling'])}")
    for m in res["same_method_in_siblings"][:limit]:
        out.append(f"same method {short_id(m['id'])}  @{m['file']}:{m['line']}")
    for s in res["shared_resources"][:limit]:
        out.append(f"shares      {short_id(s['node'])}: {', '.join(short_id(x) for x in s['shared'][:6])}{' …' if len(s['shared']) > 6 else ''}")
    for s in res["co_callers"][:limit]:
        out.append(f"co-caller   {short_id(s['node'])} J={s['jaccard']}: {', '.join(short_id(x) for x in s['shared_callees'][:5])}")
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------- tests covering
HTTP_SPEC_RE = re.compile(r"^(?:(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD|ANY)\s+)?(/\S*)$", re.I)


def route_targets(st: GraphStore, spec: str) -> list[str]:
    """`/path` or `VERB /path` (concrete or with {params}) -> backend route nodes it matches."""
    m = HTTP_SPEC_RE.match(spec.strip())
    if not m:
        return []
    from .link import _method_ok, match_path
    verb, path = (m.group(1) or "").upper(), m.group(2)
    out = []
    for r in st.q("SELECT id, file, attrs FROM nodes WHERE kind='route'"):
        a = json.loads(r["attrs"] or "{}")
        uri, meth = a.get("uri"), a.get("method")
        if not uri:
            continue
        if verb and meth and not _method_ok(verb, meth):
            continue
        uris = [uri] + (["/api" + uri] if re.search(r"(^|/)routes/api(\.php|/)", r["file"] or "") else [])
        if any(match_path(path, u)[0] and match_path(path, u)[1]["lit"] == len([s for s in u.split("/") if s and not s.startswith("{")])
               for u in uris):
            out.append(r["id"])
    return out


UI_TEST_PATH = re.compile(r"(^|/)(\w*UITests?|androidTest|\w*[Ss]napshot\w*|\w*[Ss]creenshot\w*|uiTest|"
                          r"integration_test|cypress|playwright)(/|$)", re.I)
UI_TEST_FRAMEWORKS = {"playwright", "cypress", "xcuitest", "espresso", "compose-ui", "snapshot"}


def test_class(t: dict) -> str:
    """"ui" for UI, snapshot and screenshot tests (XCUITest, Compose UI / Espresso, Playwright / Cypress, snapshot
    libraries; a test target or folder named *UITests, androidTest, e2e, *Snapshot*, *Screenshot*), else "unit"."""
    if (t.get("framework") or "") in UI_TEST_FRAMEWORKS or UI_TEST_PATH.search(t.get("file") or ""):
        return "ui"
    if re.search(r"(snapshot|screenshot)", f"{t.get('name') or ''} {(t.get('file') or '').rsplit('/', 1)[-1]}", re.I):
        return "ui"
    return "unit"


def app_roots(st: GraphStore, extra: list[str] | None = None, defaults: bool = True) -> set[str]:
    """App entry points a transitive test path should not run through: `@main` types (Swift), `*Activity` classes
    (Android) and their members (`App.body`) unless `defaults` is off, plus the `--exclude-root` symbols."""
    ids: set[str] = set()
    if defaults:
        ids |= {r["id"] for r in st.q("SELECT id FROM nodes WHERE entry_kind='main' AND kind IN ('class','struct')")}
        ids |= {r["id"] for r in st.q("SELECT id FROM nodes WHERE kind='class' AND lang='kotlin' AND name LIKE "
                                      "'%Activity' AND json_extract(attrs, '$.test') IS NULL")}
    for x in extra or ():
        ids |= set(resolve_targets(st, x))
    fqns = [r["fqn"] for r in st.q(f"SELECT fqn FROM nodes WHERE id IN ({','.join('?' * len(ids))})", list(ids))
            if r["fqn"]] if ids else []
    for fq in fqns:
        ids |= {r["id"] for r in st.q("SELECT id FROM nodes WHERE fqn LIKE ? ESCAPE '\\' AND kind IN ('method','function')",
                                      (fq.replace("_", "\\_").replace("%", "\\%") + ".%",))}
    return ids


def tests_covering(st: GraphStore, spec: str, min_conf="heuristic", max_depth=30, near_depth: int | None = 3,
                   unit_only=False, exclude_roots: list[str] | None = None, through_roots=False) -> dict:
    """Tests that exercise a symbol / route / table...: direct (the test code itself calls / requests it) and
    transitive (through application code: test -> route -> controller -> service -> target). An inherited
    `Sub.method` spec leaves out the calls whose receiver cannot be a Sub (narrow_inherited).
    Transitive results are kept near the target (#87): at most `near_depth` hops (None: any), not through an app
    root (`@main`, `App.body`, `MainActivity`, `exclude_roots`; `through_roots` keeps those), and UI / snapshot tests
    in their own `ui` list (`unit_only` drops it). What is left out is counted in `omitted`."""
    ob = override_bases(st, spec)
    with narrowed(st, inherited_targets(st, spec) + ob):
        res = _tests_covering(st, spec, min_conf, max_depth)
    out = _split_tests(st, res, near_depth, unit_only, exclude_roots, through_roots)
    if _narrowed_bases(ob):
        out["override_narrowed"] = _narrowed_bases(ob)
    return out


# hops that are wiring, not application code between the test and the target: the test's own calls and requests,
# route -> handler, cross-repo http -> route, command -> handle, middleware
WIRING_EDGES = {"ROUTES_TO", "MATCHES_ROUTE", "MATCHES_ENDPOINT", "MATCHES_CHANNEL", "HANDLED_BY", "USES_MIDDLEWARE"}


def app_depth(path: list[dict]) -> int:
    """Hops through application code on a test's path (#87): test edges (TEST_CALLS, TEST_HTTP...) and wiring
    (route -> controller) do not count, so `test -> route -> controller -> service` is 1."""
    return sum(1 for p in path or () if not p["kind"].startswith("TEST_") and p["kind"] not in WIRING_EDGES)


def _split_tests(st, res, near_depth, unit_only, exclude_roots, through_roots) -> dict:
    roots = app_roots(st, exclude_roots, defaults=not through_roots)
    targets = set(res.get("targets") or ())
    omitted = {"deeper": 0, "through_roots": 0, "ui": 0}
    out_direct, near, ui = [], [], []
    for t in res["direct"]:
        t["class"] = test_class(t)
        out_direct.append(t)
    for t in res["transitive"]:
        t["class"] = test_class(t)
        hops = {p["to"] for p in t.get("path") or ()} | {p["from"] for p in t.get("path") or ()}
        root = next((h for h in hops if h in roots and h not in targets and h != t["test"]), None)
        if root is not None:
            omitted["through_roots"] += 1
            continue
        t["app_depth"] = app_depth(t.get("path"))
        if near_depth is not None and t["app_depth"] > near_depth:
            omitted["deeper"] += 1
            continue
        if t["class"] == "ui":
            if unit_only:
                omitted["ui"] += 1
            else:
                ui.append(t)
            continue
        near.append(t)
    if unit_only:
        omitted["ui"] += sum(1 for t in out_direct if t["class"] == "ui")
        out_direct = [t for t in out_direct if t["class"] != "ui"]
    res.update(direct=out_direct, transitive=near, ui=ui, omitted=omitted,
               limits={"near_depth": near_depth, "unit_only": unit_only, "through_roots": through_roots,
                       "exclude_roots": exclude_roots or []})
    res["stats"].update(direct=len(out_direct), transitive=len(near), ui=len(ui), omitted=sum(omitted.values()))
    return res


def _tests_covering(st: GraphStore, spec: str, min_conf="heuristic", max_depth=30) -> dict:
    from .core.model import TEST_EDGE_KINDS
    targets = route_targets(st, spec) or resolve_targets(st, spec)
    # a cross-repo hop (frontend http call -> backend route) is still the test's own request
    tk = list(TEST_EDGE_KINDS) + ["MATCHES_ROUTE"]
    si = _has_class_target(targets)
    # a base / interface method: the tests of its overrides exercise it too (calls through a collection or a
    # base-typed value land on the overrides), as in impact; they are marked via_override
    below = override_seeds(st, spec, targets, min_conf)
    seeds = targets + below
    direct = reverse_closure(st, seeds, kinds=tk, min_conf=min_conf, max_depth=max_depth, seed_inst=si)
    allk = CALL_LIKE + tk
    trans = reverse_closure(st, seeds, kinds=allk, min_conf=min_conf, max_depth=max_depth, seed_inst=si)
    if below:
        own = (set(reverse_closure(st, targets, kinds=tk, min_conf=min_conf, max_depth=max_depth, seed_inst=si))
               | set(reverse_closure(st, targets, kinds=allk, min_conf=min_conf, max_depth=max_depth, seed_inst=si)))
        bfq = _fqn_of(st, below)
    else:
        own, bfq = None, {}
    ids = list(set(direct) | set(trans))
    tests = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in st.q(f"SELECT id, kind, name, fqn, file, line, attrs, entry_kind FROM nodes WHERE id IN ({q}) "
                      f"AND (kind='test' OR entry_kind='test')", chunk):
            tests[r["id"]] = dict(r)
    dpaths = shortest_paths(st, {k: v for k, v in direct.items()}, kinds=tk, min_conf=min_conf, seed_inst=si)
    tpaths = shortest_paths(st, trans, kinds=allk, min_conf=min_conf, seed_inst=si)
    out = {"targets": targets, "direct": [], "transitive": []}
    inh = inherited_targets(st, spec)
    if inh:
        out["inherited"] = inh
    for tid, t in sorted(tests.items(), key=lambda x: (x[1]["file"] or "", x[1]["line"] or 0)):
        a = json.loads(t["attrs"] or "{}")
        it = {"test": tid, "name": t["name"], "framework": a.get("framework"), "file": t["file"], "line": t["line"]}
        for k in ("display_name", "parameterized"):
            if a.get(k):
                it[k] = a[k]
        if tid in direct:
            it.update(depth=direct[tid], path=dpaths.get(tid, []))
            key = "direct"
        else:
            it.update(depth=trans[tid], path=tpaths.get(tid, []))
            key = "transitive"
        it["path_confidence"] = path_confidence(it["path"])
        if path_candidate(it["path"]):
            it["candidate"] = True
        if own is not None and tid not in own:    # reaches the target only through an override
            end = it["path"][-1]["to"] if it["path"] else None
            it["via_override"] = [bfq.get(end, end)] if end in bfq else sorted(set(bfq.values()))[:3]
        out[key].append(it)
    for k in ("direct", "transitive"):     # closest tests first
        out[k].sort(key=lambda t: (t["depth"], CONFIDENCE_RANK.get(t["path_confidence"], 0) * -1, t["file"] or "", t["line"] or 0))
    # test cases: `test` nodes (pytest / PHPUnit / Jest ...) and functions marked entry_kind=test (Swift XCTest and
    # Swift Testing, Kotlin JUnit / kotlin.test)
    by_fw = {(r["fw"] or "test"): r["c"] for r in
             st.q("SELECT json_extract(attrs, '$.framework') fw, count(*) c FROM nodes "
                  "WHERE kind='test' OR entry_kind='test' GROUP BY fw ORDER BY c DESC, fw")}
    test_files = 0
    if not by_fw:
        test_files = st.q("SELECT count(*) c FROM nodes WHERE kind='file' AND json_extract(attrs, '$.test')")[0]["c"]
    out["stats"] = {"direct": len(out["direct"]), "transitive": len(out["transitive"]),
                    "tests_in_graph": sum(by_fw.values()), "tests_by_framework": by_fw, "test_files": test_files}
    return out


def _path_short(path: list[dict]) -> str:
    if not path:
        return ""
    s = short_id(path[0]["from"])
    for p in path:
        s += f" -{p['kind']}-> {short_id(p['to'])}"
    return s


def render_tests_covering(res: dict, show_paths=True, limit=60) -> str:
    L = [f"targets: {len(res['targets'])} node(s): {', '.join(short_id(t) for t in res['targets'][:6])}"
         + (" ..." if len(res["targets"]) > 6 else "")]
    if not res["targets"]:
        return L[0] + "\n(nothing matched the spec)"
    st = res["stats"]
    fws = st.get("tests_by_framework") or {}
    per = (": " + ", ".join(f"{k} {v}" for k, v in fws.items())) if fws else ""
    L += inherited_lines(res)
    om = res.get("omitted") or {}
    lim = res.get("limits") or {}
    more = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in om.items() if v)
    nd = lim.get("near_depth")
    L.append(f"tests: {st['direct']} direct, {st['transitive']} nearby transitive"
             + (f" (app depth <= {nd})" if nd else "") + (f", {st['ui']} UI / snapshot" if st.get("ui") else "")
             + (f"; {sum(om.values())} more not listed ({more})" if more else "")
             + f" (of {st['tests_in_graph']} test cases in the graph{per})")
    for label, key in (("DIRECT (the test code itself calls / requests the target)", "direct"),
                       ("TRANSITIVE (through application code)", "transitive"),
                       ("UI / SNAPSHOT (through application code)", "ui")):
        group = res[key]
        if not group:
            continue
        L += ["", f"== {label}: {len(group)}"]
        for t in group[:limit]:
            vo = t.get("via_override")
            dn = (f' "{t["display_name"]}"' if t.get("display_name") else "") + (" (parameterized)" if t.get("parameterized") else "")
            L.append(f"  {t['name']}{dn}  [{t.get('framework') or 'test'}] {t['file']}:{t['line']}  depth={t['depth']} conf={t['path_confidence']}"
                     + (CANDIDATE_LABEL if t.get("candidate") else "")
                     + (f"  (via override {vo[0]}" + (f" +{len(vo) - 1}" if len(vo) > 1 else "") + ")" if vo else "")
                     + (f"  app_depth={t['app_depth']}" if t.get("app_depth", t["depth"]) != t["depth"] else ""))
            if show_paths and t["path"]:
                L.append(f"      {_path_short(t['path'][:8])}{' ...' if len(t['path']) > 8 else ''}")
        if len(group) > limit:
            L.append(f"  ... {len(group) - limit} more")
    if any(t.get("candidate") for k in ("direct", "transitive") for t in res[k]):
        L += ["", CANDIDATE_NOTE]
    if more:
        L += ["", "not listed: " + more + " (widen with --max-depth N or 0 for any depth, --through-roots, no --unit-only)"]
    if not res["direct"] and not res["transitive"] and not res.get("ui"):
        if st["tests_in_graph"]:
            why = (f" ({st['tests_in_graph']} test cases are indexed; none calls the target, directly or through"
                   " application code)")
        elif st.get("test_files"):
            why = (f" (the graph has {st['test_files']} test files but no recognised test cases; the supported"
                   " frameworks are listed in docs/channels-and-tests.md)")
        else:
            why = " (the graph has no test code: no test files or test cases were indexed)"
        L.append("no indexed test reaches the target" + why)
    return "\n".join(L)

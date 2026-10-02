"""PHP language plugin.

1. Runs extractor/extract.php (nikic/php-parser + NameResolver) -> per-file JSON facts.
2. Builds symbol tables (classes/interfaces/traits/enums, methods, properties, functions).
3. Flow-insensitive local type inference per function body (params, typed/promoted
   properties, `new`, assignments, @var/@param/@return docblocks, instanceof, closure
   params, inferred return types), iterated to a fixed point across the program.
4. Emits CALLS / IMPLEMENTED_BY / OVERRIDDEN_BY / INSTANTIATES / INJECTS / EXTENDS ...
   Framework plugins (e.g. Laravel) register type rules and fact handlers on the
   PhpProgram before step 3/4, so they can resolve facades, Eloquent builders, etc.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...core.fsutil import keep_file
from ...core.paths import rel_dir, rules as path_rules
from ...core.plugin import FrameworkPlugin, GraphBuilder, LanguagePlugin, Project

EXTRACTOR = Path(__file__).parent / "extractor" / "extract.php"
SCALARS = {"int", "string", "bool", "float", "array", "mixed", "void", "null", "callable", "iterable",
           "object", "false", "true", "never", "resource", "list"}
# Heuristic unique-name fallback never applies to these (framework-common names).
HEURISTIC_STOP = set("""handle get set first find all create update delete save make toarray render boot register with
where map filter each count exists value load refresh fresh push pull put send build run execute call apply resolve
validate authorize rules messages up down definition casts fill getkey tojson jsonserialize offsetget format parse process
store show index destroy edit label table form schema query toresponse broadcaston broadcastas broadcastwith via tomail
toarray failed middleware getname getid title description""".split())
# test code: Laravel/PHPUnit/Pest keep tests under tests/ (also Modules/<x>/Tests/ in modular apps)
TEST_PATH_RE = re.compile(r"(^|/)(tests|Tests)/")


def is_test_path(path: str | None) -> bool:
    return bool(path and TEST_PATH_RE.search(path))


def module_of(path: str | None) -> str | None:
    if not path:
        return None
    parts = path.split("/")
    if parts[0] == "app":
        segs = parts[1:-1]
        return "/".join(segs[:3]) if segs else "app"
    if parts[0] == "database":
        return "/".join(parts[:2])
    return parts[0] if len(parts) > 1 else "(root)"


@dataclass
class PhpFunc:
    id: str
    name: str
    cls: str | None
    file: str
    line: int
    end_line: int
    doc: str | None
    params: list
    returns: list
    facts: list
    static: bool = False
    abstract: bool = False
    visibility: str = "public"
    inferred: set = field(default_factory=set)
    env: dict = field(default_factory=dict)
    skel: list = field(default_factory=list)
    returns_nullable: bool = False
    dead: dict = field(default_factory=dict)   # fact index -> guard evidence, per gate scenario (see gating.py)
    attributes: list = field(default_factory=list)   # PHP 8 attribute names on a method (e.g. PHPUnit\Framework\Attributes\Test)


@dataclass
class PhpClass:
    fqcn: str
    kind: str
    file: str
    line: int
    end_line: int
    doc: str | None
    extends: list
    implements: list
    traits: list
    abstract: bool
    props: dict
    consts: list
    methods: dict  # lower name -> PhpFunc


class ResolveCtx:
    """Per-function resolution context handed to framework fact handlers."""

    def __init__(self, prog: "PhpProgram", fn: PhpFunc):
        self.prog, self.fn = prog, fn
        self.env = fn.env

    def type_of(self, d) -> set:
        return self.prog.type_of(d, self.fn, self.env)

    def strings_of(self, d) -> list[str]:
        """Literal string values an expression can take (str / alternatives)."""
        if not d:
            return []
        if d.get("k") == "str":
            return [d["v"]]
        if d.get("k") == "alt":
            out = []
            for o in d["opts"]:
                out += self.strings_of(o)
            return out
        return []


class PhpProgram:
    def __init__(self, project: Project, records: list[dict], builder: GraphBuilder):
        self.project, self.records, self.b = project, records, builder
        self.classes: dict[str, PhpClass] = {}
        self.lower_classes: dict[str, str] = {}
        self.functions: dict[str, PhpFunc] = {}
        self.scripts: dict[str, PhpFunc] = {}
        self.all_funcs: list[PhpFunc] = []   # application code only
        # test code (tests/): in the symbol tables so tests resolve their calls into the app, but analysed in a
        # separate pass whose edges never feed the application graph (see codegraph/tests_index.py)
        self.test_funcs: list[PhpFunc] = []
        self.test_classes: set[str] = set()
        # framework hooks
        self.static_call_rules: list[Callable] = []
        self.method_call_rules: list[Callable] = []
        self.prop_rules: list[Callable] = []
        self.func_rules: list[Callable] = []
        self.fact_handlers: list[Callable] = []
        self.test_fact_handlers: list[Callable] = []   # handlers run on test code only (HTTP test calls, ...)
        self.stats = defaultdict(int)
        self._load()

    # ---------------- symbol tables ----------------
    def _load(self):
        for rec in self.records:
            f = rec["file"]
            in_test = is_test_path(f)
            funcs = self.test_funcs if in_test else self.all_funcs
            for c in rec.get("classes", []):
                pc = PhpClass(fqcn=c["fqcn"], kind=c["kind"], file=f, line=c["line"], end_line=c["end_line"], doc=c.get("doc"),
                              extends=c["extends"], implements=c["implements"], traits=c["traits"], abstract=c.get("abstract", False),
                              props={p["name"]: p for p in c["props"]}, consts=c["consts"], methods={})
                for m in c["methods"]:
                    fn = PhpFunc(id=f"method:{pc.fqcn}::{m['name']}", name=m["name"], cls=pc.fqcn, file=f, line=m["line"],
                                 end_line=m["end_line"], doc=m.get("doc"), params=m["params"], returns=m["returns"], facts=m["facts"],
                                 static=m["static"], abstract=m["abstract"], visibility=m["visibility"],
                                 skel=m.get("skel") or [], returns_nullable=m.get("returns_nullable", False))
                    fn.attributes = m.get("attributes") or []
                    pc.methods[m["name"].lower()] = fn
                    funcs.append(fn)
                if in_test:
                    self.test_classes.add(pc.fqcn)
                self.classes[pc.fqcn] = pc
                self.lower_classes[pc.fqcn.lower()] = pc.fqcn
            for fn in rec.get("functions", []):
                pf = PhpFunc(id=f"function:{fn['name']}", name=fn["name"], cls=None, file=f, line=fn["line"], end_line=fn["end_line"],
                             doc=fn.get("doc"), params=fn["params"], returns=fn["returns"], facts=fn["facts"],
                             skel=fn.get("skel") or [], returns_nullable=fn.get("returns_nullable", False))
                if not in_test or fn["name"].lower() not in self.functions:
                    self.functions[fn["name"].lower()] = pf
                funcs.append(pf)
            if rec.get("top"):
                sf = PhpFunc(id=f"script:{f}", name=f, cls=None, file=f, line=1, end_line=1, doc=None, params=[], returns=[], facts=rec["top"])
                self.scripts[f] = sf
                funcs.append(sf)
        # method-name index for heuristic fallback (application classes only: a test double must not make an
        # application method name ambiguous)
        self.by_name = defaultdict(list)
        for c in self.classes.values():
            if c.fqcn in self.test_classes:
                continue
            for ln, m in c.methods.items():
                self.by_name[ln].append(m)
        self.children = defaultdict(set)
        for c in self.classes.values():
            for p in c.extends + c.implements + c.traits:
                self.children[p].add(c.fqcn)

    def cls(self, name: str | None) -> PhpClass | None:
        if not name:
            return None
        c = self.classes.get(name)
        if c is None:
            fq = self.lower_classes.get(name.lower())
            c = self.classes.get(fq) if fq else None
        return c

    def ancestors(self, fqcn: str, include_self=True) -> list[str]:
        """Linearised ancestors: self, traits, parent chain, interfaces (incl. external names)."""
        out, seen, stack = [], set(), [fqcn]
        while stack:
            x = stack.pop(0)
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
            c = self.cls(x)
            if c:
                stack.extend(c.traits + c.extends + c.implements)
        return out if include_self else out[1:]

    def is_test_class(self, fqcn: str) -> bool:
        return fqcn in self.test_classes

    def is_test_fn(self, fn: "PhpFunc") -> bool:
        return is_test_path(fn.file)

    def is_a(self, fqcn: str, base: str) -> bool:
        b = base.lower()
        return any(a.lower() == b for a in self.ancestors(fqcn))

    def find_method(self, fqcn: str, name: str | None) -> PhpFunc | None:
        if not name:
            return None
        ln = name.lower()
        for a in self.ancestors(fqcn):
            c = self.cls(a)
            if c and ln in c.methods:
                return c.methods[ln]
        return None

    def find_prop(self, fqcn: str, name: str | None) -> dict | None:
        for a in self.ancestors(fqcn):
            c = self.cls(a)
            if c and name in c.props:
                return c.props[name]
        return None

    def descendants(self, fqcn: str) -> set[str]:
        out, stack = set(), [fqcn]
        while stack:
            x = stack.pop()
            for ch in self.children.get(x, ()):
                if ch not in out:
                    out.add(ch)
                    stack.append(ch)
        return out

    # ---------------- types ----------------
    def norm(self, types, ctx_cls: str | None) -> set:
        out = set()
        for t in types or []:
            if not t:
                continue
            tl = t.lower()
            if tl in ("self", "static", "$this"):
                if ctx_cls:
                    out.add(ctx_cls)
            elif tl in SCALARS:
                continue
            else:
                out.add(t.lstrip("\\"))
        return out

    def returns_of(self, m: PhpFunc, recv_cls: str | None) -> set:
        # late static binding: `static`/`self` returns map to the receiver class
        return self.norm(m.returns, recv_cls or m.cls) | {t for t in m.inferred if not t.startswith("__")}

    def type_of(self, d, fn: PhpFunc, env: dict, depth=0) -> set:
        if not d or depth > 8:
            return set()
        k = d.get("k")
        if k == "this":
            return {fn.cls} if fn.cls else set()
        if k == "var":
            return set(env.get(d["n"], ()))
        if k == "new":
            return {d["class"]} if d.get("class") else set()
        if k == "alt":
            out = set()
            for o in d["opts"]:
                out |= self.type_of(o, fn, env, depth + 1)
            return out
        if k == "prop":
            out = set()
            for t in self.type_of(d.get("of"), fn, env, depth + 1):
                for rule in self.prop_rules:
                    r = rule(self, t, d.get("n"))
                    if r:
                        out |= r
                if self.cls(t):
                    p = self.find_prop(t, d.get("n"))
                    if p:
                        out |= self.norm(p.get("types"), t)
            return out
        if k == "sprop":
            c = d.get("class")
            p = self.find_prop(c, d.get("n")) if c and self.cls(c) else None
            return self.norm(p.get("types"), c) if p else set()
        if k == "mcall":
            out = set()
            for t in self.type_of(d.get("of"), fn, env, depth + 1):
                for rule in self.method_call_rules:
                    r = rule(self, t, d.get("m"), d.get("args", []))
                    if r:
                        out |= r
                if self.cls(t):
                    m = self.find_method(t, d.get("m"))
                    if m:
                        out |= self.returns_of(m, t)
            return out
        if k == "scall":
            c, out = d.get("class"), set()
            for rule in self.static_call_rules:
                r = rule(self, c, d.get("m"), d.get("args", []))
                if r:
                    out |= r
            if c and self.cls(c):
                m = self.find_method(c, d.get("m"))
                if m:
                    out |= self.returns_of(m, c)
            return out
        if k == "func":
            out = set()
            for rule in self.func_rules:
                r = rule(self, d.get("n"), d.get("args", []))
                if r:
                    out |= r
            f = self.functions.get((d.get("n") or "").lower())
            if f:
                out |= self.returns_of(f, None)
            return out
        if k == "elem":
            out = set()
            for t in self.type_of(d.get("of"), fn, env, depth + 1):
                if t.startswith("collection:"):
                    out.add(t.split(":", 1)[1])
            return out
        return set()

    def infer_env(self, fn: PhpFunc) -> dict:
        env = defaultdict(set)
        for p in fn.params:
            env[p["name"]] |= self.norm(p.get("types"), fn.cls)
        assigns = [f for f in fn.facts if f["t"] in ("assign", "vartype")]
        for _ in range(3):
            changed = False
            for f in assigns:
                v = f["var"]
                new = self.norm(f["types"], fn.cls) if f["t"] == "vartype" else self.type_of(f["expr"], fn, env)
                if new - env[v]:
                    env[v] |= new
                    changed = True
            if not changed:
                break
        return env

    def run_inference(self, iterations=3):
        self._infer(self.all_funcs, iterations)
        self._infer(self.test_funcs, iterations)   # after the application, so tests see its inferred return types

    def _infer(self, funcs, iterations):
        for _ in range(iterations):
            changed = 0
            for fn in funcs:
                fn.env = self.infer_env(fn)
                inf = set()
                for f in fn.facts:
                    if f["t"] == "return" and not f.get("ctx"):  # ignore returns inside closures
                        inf |= self.type_of(f["expr"], fn, fn.env)
                if inf - fn.inferred:
                    fn.inferred |= inf
                    changed += 1
            if not changed:
                break

    # ---------------- graph emission ----------------
    def emit_declarations(self):
        b = self.b
        for c in self.classes.values():
            ta = {"test": True} if c.fqcn in self.test_classes else {}
            cid = b.add_node(c.kind, c.fqcn, name=c.fqcn.split("\\")[-1], fqn=c.fqcn, file=c.file, line=c.line,
                             end_line=c.end_line, module=module_of(c.file), doc=c.doc, lang="php",
                             attrs={**({"abstract": c.abstract} if c.abstract else {}), **ta})
            for rel, kind in ((c.extends, "EXTENDS"), (c.implements, "IMPLEMENTS"), (c.traits, "USES_TRAIT")):
                for p in rel:
                    pc = self.cls(p)
                    tid = f"{pc.kind}:{pc.fqcn}" if pc else b.add_node("external_class", p, name=p.split("\\")[-1], fqn=p, lang="php")
                    b.add_edge(cid, tid, kind, c.file, c.line, EXACT)
            for p in c.props.values():
                pid = b.add_node("property", f"{c.fqcn}::${p['name']}", name=f"${p['name']}", fqn=f"{c.fqcn}::${p['name']}",
                                 file=c.file, line=p.get("line"), module=module_of(c.file), doc=p.get("doc"), lang="php",
                                 attrs={**({"types": p.get("types"), "default": p.get("default")} if (p.get("types") or p.get("default") is not None) else {}), **ta})
                b.add_edge(cid, pid, "CONTAINS", c.file, p.get("line"), EXACT)
            for m in c.methods.values():
                b.add_node("method", f"{c.fqcn}::{m.name}", name=m.name, fqn=f"{c.fqcn}::{m.name}", file=c.file, line=m.line,
                           end_line=m.end_line, module=module_of(c.file), doc=m.doc, lang="php",
                           attrs={**{k: v for k, v in (("visibility", m.visibility), ("static", m.static), ("abstract", m.abstract)) if v and v != "public"}, **ta})
                b.add_edge(cid, m.id, "CONTAINS", c.file, m.line, EXACT)
                if m.name.lower() == "__construct":
                    for p in m.params:
                        for t in self.norm(p.get("types"), c.fqcn):
                            tc = self.cls(t)
                            if tc:
                                b.add_edge(cid, f"{tc.kind}:{tc.fqcn}", "INJECTS", c.file, p.get("line"), EXACT, param=p["name"])
            # dispatch edges to overriding / implementing methods
            for a in self.ancestors(c.fqcn, include_self=False):
                ac = self.cls(a)
                if not ac:
                    continue
                for ln, m in c.methods.items():
                    if ln in ac.methods and ln != "__construct":
                        kind = "IMPLEMENTED_BY" if (ac.kind == "interface" or ac.methods[ln].abstract) else "OVERRIDDEN_BY"
                        b.add_edge(ac.methods[ln].id, m.id, kind, c.file, m.line, RESOLVED)
        for f in self.functions.values():
            b.add_node("function", f.name, name=f.name, fqn=f.name, file=f.file, line=f.line, end_line=f.end_line,
                       module=module_of(f.file), doc=f.doc, lang="php", attrs={"test": True} if is_test_path(f.file) else {})
        for s in self.scripts.values():
            b.add_node("script", s.file, name=s.file, file=s.file, line=1, module=module_of(s.file), lang="php",
                       attrs={"test": True} if is_test_path(s.file) else {})

    def fn_node_exists(self, fn: PhpFunc) -> bool:
        return self.b.has(fn.id)

    def emit_references(self):
        self._emit_refs(self.all_funcs, self.fact_handlers)
        # test code: calls / instantiations / class refs plus the test-only handlers; framework handlers (columns,
        # bindings, schedules, listeners...) never run on tests, so a test cannot add facts to the application graph
        self._emit_refs(self.test_funcs, self.test_fact_handlers)

    def _emit_refs(self, funcs, handlers):
        b = self.b
        for fn in funcs:
            ctx = ResolveCtx(self, fn)
            for i, f in enumerate(fn.facts):
                b.current_gate = fn.dead.get(i)
                t = f["t"]
                line = f.get("line")
                if t == "call":
                    self._emit_call(fn, f, ctx)
                elif t == "new" and f.get("class"):
                    c = self.cls(f["class"])
                    if c:
                        b.add_edge(fn.id, f"{c.kind}:{c.fqcn}", "INSTANTIATES", fn.file, line, EXACT)
                        ctor = self.find_method(c.fqcn, "__construct")
                        if ctor:
                            b.add_edge(fn.id, ctor.id, "CALLS", fn.file, line, EXACT)
                elif t == "classref" and f.get("class"):
                    c = self.cls(f["class"])
                    if c:
                        b.add_edge(fn.id, f"{c.kind}:{c.fqcn}", "REFERENCES", fn.file, line, EXACT)
                for h in handlers:
                    h(self, fn, f, ctx)
            b.current_gate = None

    def call_targets(self, fn: PhpFunc, f: dict, ctx: ResolveCtx) -> tuple[list, str]:
        """Resolve a call fact to (targets [(PhpFunc, confidence)], status)."""
        kind = f["kind"]
        if kind == "static":
            c = f.get("class")
            if c and self.cls(c):
                m = self.find_method(c, f.get("m"))
                if m:
                    return [(m, EXACT)], "resolved"
            return [], "unresolved_static"
        if kind == "func":
            pf = self.functions.get((f.get("n") or "").lower())
            return ([(pf, EXACT)], "resolved") if pf else ([], "func")
        recv = f.get("recv") or {}
        types = ctx.type_of(recv)
        out = []
        for t in types:
            if self.cls(t):
                m = self.find_method(t, f.get("m"))
                if m and all(m is not o for o, _ in out):
                    out.append((m, EXACT if recv.get("k") == "this" else RESOLVED))
        if out:
            return out, "resolved"
        if types:
            return [], "typed_external"
        name = (f.get("m") or "").lower()
        cands = self.by_name.get(name, [])
        if len(cands) == 1 and name not in HEURISTIC_STOP and len(name) >= 5 and not name.startswith("__"):
            return [(cands[0], HEURISTIC)], "heuristic"
        return [], "unresolved_method"

    def _emit_call(self, fn: PhpFunc, f: dict, ctx: ResolveCtx):
        b, line, kind = self.b, f.get("line"), f["kind"]
        self.stats[f"calls_{kind}"] += 1
        targets, status = self.call_targets(fn, f, ctx)
        for m, conf in targets:
            if conf == HEURISTIC:
                b.add_edge(fn.id, m.id, "CALLS", fn.file, line, HEURISTIC, via="unique-method-name")
            else:
                b.add_edge(fn.id, m.id, "CALLS", fn.file, line, conf)
        if status == "resolved":
            self.stats["calls_resolved"] += 1
        elif status == "heuristic":
            self.stats["calls_heuristic"] += 1
        elif status in ("typed_external", "unresolved_static", "unresolved_method"):
            self.stats[f"calls_{status}"] += 1


class PhpPlugin(LanguagePlugin):
    name = "php"

    def detect(self, project: Project) -> bool:
        return project.exists("composer.json") or any(project.root.glob("*.php"))

    def prerequisite_problem(self, project: Project) -> str | None:
        if not shutil.which("php"):
            return "php not installed (PHP 8.2+ is needed for the PHP extractor)"
        if not (EXTRACTOR.parent / "vendor" / "autoload.php").exists():
            return "PHP extractor dependencies missing: run `(cd codegraph/plugins/php/extractor && composer install)`"
        return None

    def list_files(self, project: Project) -> list[str]:
        # tests/ is indexed as test code (codegraph/tests_index.py keeps it out of the application graph)
        # Composer vendor/, Laravel storage/ and bootstrap/cache/ at the root (codegraph/presets/php.yaml) + .cg.yaml
        rules = path_rules(project, "php")
        out = []
        for dp, dns, fns in os.walk(project.root):
            rd = rel_dir(project.root, dp)
            dns[:] = rules.prune(rd, dns)
            pre = rd + "/" if rd else ""
            for fn in fns:
                if fn.endswith(".php") and not fn.startswith("._") and keep_file(os.path.join(dp, fn)) \
                        and not rules.excluded(pre + fn):
                    out.append(pre + fn)
        return sorted(out)

    def extract(self, project: Project) -> list[dict]:
        files = self.list_files(project)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fl:
            fl.write("\n".join(files))
        res = subprocess.run(["php", str(EXTRACTOR), str(project.root), fl.name], capture_output=True, text=True, check=True)
        os.unlink(fl.name)
        return [json.loads(line) for line in res.stdout.splitlines() if line.strip()]

    def index(self, project: Project, builder: GraphBuilder, frameworks: list[FrameworkPlugin]) -> dict:
        t0 = time.time()
        records = self.extract(project)
        t_extract = time.time() - t0
        self.file_report = {"seen": [r["file"] for r in records if r.get("file")],
                            "parse_failed": [r["file"] for r in records if r.get("error") and r.get("file")]}
        prog = PhpProgram(project, records, builder)
        self.program = prog
        for fw in frameworks:
            fw.register_hooks(prog)
        t1 = time.time()
        prog.run_inference()
        gate_stats = {}
        self.gate_predicates = []
        for sc in (project.options.get("gates") or [])[:1]:  # beta: one scenario per index
            from .gating import GateEvaluator
            tg = time.time()
            ev = GateEvaluator(prog, sc)
            gate_stats[sc["name"]] = ev.run()
            gate_stats[sc["name"]]["seconds"] = round(time.time() - tg, 2)
            self.gate_predicates += [(sc["name"], mid, json.dumps(v), how) for mid, (v, how) in ev.predicates.items()]
        prog.emit_declarations()
        prog.emit_references()
        stats = {"files": len(records), "parse_errors": sum(1 for r in records if r.get("error")),
                 "classes": len(prog.classes) - len(prog.test_classes), "functions": len(prog.all_funcs),
                 "test_files": sum(1 for r in records if is_test_path(r["file"])), "test_functions": len(prog.test_funcs),
                 "extract_seconds": round(t_extract, 2), "resolve_seconds": round(time.time() - t1, 2)}
        stats.update(dict(prog.stats))
        if gate_stats:
            stats["gates"] = gate_stats
        return stats

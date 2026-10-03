"""Dart language plugin.

Facts come from extractor/bin/extract.dart (package:analyzer `parseString`, unresolved AST, no
pub get of the analysed project needed). Names/types are resolved by program.DartProgram.

Nodes: module:<file>, class:<file>#C, method:<file>#C.m (ctor = C.new / C.named,
bloc closure handlers = C.on<Event>@line), function:<file>#f, http:<METHOD> <path>, env:<KEY>.
Edges: CONTAINS, IMPORTS, EXTENDS, IMPLEMENTS, OVERRIDDEN_BY, IMPLEMENTED_BY, CALLS,
INSTANTIATES, HTTP_CALLS, PARSES_JSON, READS_ENV.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from ... import presets
from ...core.model import EXACT, HEURISTIC, RESOLVED, CONFIDENCE_RANK
from ...core.paths import rules as path_rules
from ...core import cache, extractors, fsutil
from ...core.plugin import FrameworkPlugin, GraphBuilder, LanguagePlugin, Project
from .http import TOKEN, HttpExtractor, Tpl, UrlEval, bind_args, join, load_env_files, min_conf
from .models import ModelIndex
from .program import Ctx, DartProgram, DClass, DFunc, root_name, split_type, walk_repr

EXTRACTOR_DIR = Path(__file__).parent / "extractor"
EXTRACTOR = EXTRACTOR_DIR / "bin" / "extract.dart"
BIN = EXTRACTOR_DIR / ".bin" / "extract"
# pub / build output, CocoaPods, FVM, IDE state (codegraph/presets/dart.yaml), for the facts-cache walk; the extractor
# gets the same rules (PathRules.extractor_cfg) through its config
SKIP_DIRS = presets.skip_dirs("dart")


def find_dart() -> str | None:
    for c in (os.environ.get("DART"), shutil.which("dart"), str(Path.home() / "opt/dart-sdk/bin/dart"),
              str(Path.home() / "flutter/bin/dart"), "/usr/lib/dart/bin/dart"):
        if c and Path(c).exists():
            return c
    return None


def module_of(path: str | None) -> str | None:
    if not path:
        return None
    parts = path.split("/")
    if "lib" in parts:
        i = parts.index("lib")
        return "/".join(parts[i + 1:-1]) or "lib"
    return "/".join(parts[:-1]) or None


def extractor_stamp() -> str:
    """Hash of the extractor source and its lockfile; the compiled binary is reused only while it matches (an mtime
    comparison would keep an outdated binary after a checkout that leaves the source older than the binary)."""
    h = hashlib.sha256()
    for f in (EXTRACTOR, EXTRACTOR_DIR / "pubspec.lock"):
        h.update(fsutil.content_key(f).encode() + b"\n")
    return h.hexdigest()


def _stamp_path(bin_: Path | None = None) -> Path:
    b = bin_ or BIN
    return b.with_name(b.name + ".sha256")


def extractor_binary_current(bin_: Path | None = None) -> bool:
    b = bin_ or BIN
    try:
        return b.exists() and _stamp_path(b).read_text().strip() == extractor_stamp()
    except OSError:
        return False


def write_extractor_stamp(bin_: Path | None = None) -> None:
    _stamp_path(bin_).write_text(extractor_stamp() + "\n")


def facts_fingerprint(root: Path, cfg: dict, rules=None) -> str:
    """Cache key for extractor facts: cache version + extractor code + deps lock + config + (path, size, content hash)
    of every .dart / .yaml / .env* file. Content, not mtime: a same-size edit with a restored mtime is a miss."""
    h = hashlib.sha256(f"cg-cache-v{fsutil.CACHE_VERSION}\n".encode())
    for f in (EXTRACTOR, EXTRACTOR_DIR / "pubspec.lock"):
        h.update(f.read_bytes() if f.exists() else b"")
    h.update(json.dumps(cfg, sort_keys=True).encode())
    for dp, dns, fns in os.walk(root):
        rd = os.path.relpath(dp, root).replace(os.sep, "/")
        rd = "" if rd == "." else rd
        dns[:] = rules.prune(rd, dns, dot=True) if rules is not None else sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(fns):
            if fn.endswith((".dart", ".yaml")) or fn.startswith(".env"):
                p = os.path.join(dp, fn)
                h.update(f"{os.path.relpath(p, root)}|{fsutil.content_key(p)}\n".encode())
    return h.hexdigest()[:20]


def normalize_url(url: str) -> tuple[str, dict]:
    """URL template -> (path without trailing slash, info{origin, query, trailing_slash, relative})."""
    info: dict = {}
    u = url.strip()
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*", u)
    if m:
        info["origin"] = m.group(0)
        u = u[m.end():]
    else:
        m = re.match(r"^\{[^{}/]*\}(?=/|$)", u)
        if m:
            info["origin"] = m.group(0)
            u = u[m.end():]
    q = re.search(r"\?(?![^{]*\})", u)
    if q:
        info["query"] = u[q.end():]
        u = u[:q.start()]
    u = re.sub(r"/{2,}", "/", u)
    if not u.startswith("/"):
        info["relative"] = True
        u = "/" + u
    info["trailing_slash"] = len(u) > 1 and u.endswith("/")
    if len(u) > 1:
        u = u.rstrip("/")
    return u, info


def _error_spans(f: dict) -> list:
    """[[line, line]] per analyzer error of a file (`error_lines`, else the line of `first_error`), #73."""
    lines = f.get("error_lines") or []
    if not lines and isinstance(f.get("first_error"), str):
        m = re.search(r"@(\d+)$", f["first_error"])
        lines = [int(m.group(1))] if m else []
    return [[ln, ln] for ln in lines]

class DartPlugin(LanguagePlugin):
    name = "dart"

    def __init__(self):
        self.program: DartProgram | None = None

    def detect(self, project: Project) -> bool:
        if project.exists("pubspec.yaml"):
            return True
        for p in project.root.glob("*/pubspec.yaml"):
            return True
        for p in project.root.glob("*/*/pubspec.yaml"):
            return True
        return False

    def ensure_extractor(self, dart: str) -> str:
        """The compiled extractor: in the package's extractor directory when its dependencies are there (a checkout),
        else in the per-user cache directory (codegraph/core/extractors.py)."""
        work = extractors.workdir("dart")
        bin_ = BIN if work == extractors.SPECS["dart"].pkg else work / ".bin" / "extract"
        if extractor_binary_current(bin_):
            return str(bin_)
        work = extractors.ensure("dart", dart)
        bin_.parent.mkdir(exist_ok=True)
        r = subprocess.run([dart, "compile", "exe", str(work / "bin" / EXTRACTOR.name), "-o", str(bin_)], cwd=work,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("dart extractor compile failed: " + r.stderr[-500:])
        write_extractor_stamp(bin_)
        return str(bin_)

    def run_extractor(self, project: Project, cfg: dict, rules=None) -> tuple[dict, str]:
        cache_file, status = None, "disabled"
        if not os.environ.get("CODEGRAPH_NO_CACHE"):
            fp = facts_fingerprint(project.root, cfg, rules)
            cdir = cache.subdir("dart")
            rkey = cache.root_key(project.root)
            cache_file = cdir / f"{rkey}-v{fsutil.CACHE_VERSION}-{fp}.json"
            if cache_file.exists():
                return json.loads(cache_file.read_text()), "hit"
            status = "miss"
        dart = find_dart()
        if not dart:
            raise FileNotFoundError("dart SDK not found (set $DART or put dart on PATH)")
        exe = self.ensure_extractor(dart)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "facts.json"
            c = dict(cfg, root=str(project.root), out=str(out))
            (Path(td) / "cfg.json").write_text(json.dumps(c))
            r = subprocess.run([exe, "--config", str(Path(td) / "cfg.json")], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError("dart extractor failed: " + r.stderr[-800:])
            facts = json.loads(out.read_text())
        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            for old in cache_file.parent.glob(cache_file.name.split("-")[0] + "-*.json"):
                old.unlink(missing_ok=True)
            cache_file.write_text(json.dumps(facts))
            cache.note_project(project.root)
        return facts, status

    @staticmethod
    def _conditional(b: GraphBuilder, imp: dict, how: str) -> None:
        """Conditional import / export: IMPORTS edges to each alternative library (attrs.condition) and the group for
        the platform tags (codegraph/platforms.py: the default library and each `if (dart.library.x)` one)."""
        if not imp.get("configs"):
            return
        for c in imp["configs"]:
            if c["lib"] is not None:
                b.add_edge(f"module:{imp['file']}", c["lib"].id, "IMPORTS", imp["file"], imp.get("l"), EXACT, conditional=True,
                           condition=c["name"] + ("" if c.get("value") in (None, "true") else f" == {c['value']!r}"),
                           **({"via": "export"} if how == "export" else {}))
        b.platform_imports.append({"file": imp["file"], "line": imp.get("l"), "how": how,
                                   "default": imp["lib"].file if imp["lib"] is not None else None,
                                   "configs": [(c["name"], c.get("value"), c["lib"].file if c["lib"] is not None else None)
                                               for c in imp["configs"]]})

    @staticmethod
    def _variant_reexports(prog, b: GraphBuilder) -> None:
        """A library that is one alternative of a conditional import / export defines the names it re-exports
        (`export 'src/x.dart' show f`) and its public top-level tear-offs (`const f = Impl.f`) too: recorded as
        module attrs.reexports (name -> node id) for the platform divergence checks (codegraph/platforms.py)."""
        files = set()
        for pi in b.platform_imports:
            if pi.get("default"):
                files.add(pi["default"])
            files |= {c[2] for c in pi.get("configs") or () if c[2]}

        def tearoff(lib, e):
            if not isinstance(e, dict):
                return None
            if e.get("k") == "id":
                v = prog.lookup(lib, e.get("v") or "")
                return getattr(v, "id", None) if isinstance(v, DFunc) else None
            t = e.get("t") or {}
            if e.get("k") == "prop" and t.get("k") == "id":
                c = prog.lookup(lib, t.get("v") or "")
                if isinstance(c, DClass):
                    m = c.methods.get(e.get("n"))
                    return m.id if m is not None else None
                for imp in prog.prefix_imports(lib, t.get("v") or ""):
                    v = prog.namespace(imp["lib"]).get(e.get("n")) if imp["lib"] is not None else None
                    if isinstance(v, DFunc):
                        return v.id
            return None
        own_vars = defaultdict(list)
        for v in prog.vars:
            if v.cls is None and not v.name.startswith("_"):
                own_vars[v.file].append(v)
        for lib in prog.libs.values():
            if lib.file not in files or lib.id not in b.nodes:
                continue
            rex = {}
            for k, v in prog.namespace(lib).items():
                vid = getattr(v, "id", None)
                if getattr(v, "file", lib.file) != lib.file and vid in b.nodes:
                    rex[k] = vid
            for v in own_vars.get(lib.file, ()):
                tid = tearoff(lib, v.init)
                if tid in b.nodes:
                    rex[v.name] = tid
            if rex:
                n = b.nodes[lib.id]
                n.attrs = {**(n.attrs or {}), "reexports": rex}

    # ------------------------------------------------------------------ index
    def index(self, project: Project, builder: GraphBuilder, frameworks: list[FrameworkPlugin]) -> dict:
        t0 = time.time()
        # the walk's directory rules (common + dart preset skip_dirs, .cg.yaml skip_dirs.add / keep, include); the
        # extractor keeps no skip list of its own
        rules = path_rules(project, "dart")
        cfg = {**rules.extractor_cfg(project.options.get("dart_skip_dirs") or []),
               "skip_suffixes": list(presets.values("dart", "generated_suffixes", default=[]))
               if not project.options.get("dart_keep_generated") else []}
        try:
            facts, cache = self.run_extractor(project, cfg, rules)
        except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as ex:
            return {"status": "skipped", "reason": str(ex)[:300]}
        t_ext = time.time() - t0
        if rules.exclude:     # .cg.yaml exclude globs: dropped from the extractor's output before the graph is built
            # (generated files stay: *.g.dart holds the exact JSON keys; their nodes leave the graph in the final pass)
            facts = dict(facts, files=[f for f in facts.get("files") or [] if not rules.excluded(f.get("file") or "", generated=False)])
        prog = DartProgram(project.root, facts)
        prog.load()
        failed = [x.get("file") if isinstance(x, dict) else str(x) for x in facts.get("failures") or []]
        self.file_report = {"seen": list(prog.files) + [f for f in failed if f], "parse_failed": [f for f in failed if f],
                            "syntax_errors": {rel: _error_spans(f) for rel, f in prog.files.items() if f.get("errors")}}
        self.program = prog
        prog.models = ModelIndex(prog)
        prog.env_values = load_env_files(project.root, [p.dir for p in prog.pkg_dirs])
        prog.builder = builder
        for fw in frameworks:
            fw.register_hooks(prog)
        b = builder
        st = {"files": len(prog.files), "parse_error_files": sum(1 for f in prog.files.values() if f.get("errors")),
              "extract_failures": len(facts.get("failures") or []), "skipped_generated": facts.get("skipped"),
              "packages": sorted(prog.pkgs), "facts_cache": cache, "extract_seconds": round(t_ext, 2)}
        # ---- symbols
        for rel, f in prog.files.items():
            b.add_node("module", rel, name=rel, fqn=rel, file=rel, line=1, end_line=f.get("lines"), module=module_of(rel), lang="dart",
                       attrs={"part_of": prog.part_owner[rel]} if rel in prog.part_owner else None)
        for c in prog.classes.values():
            b.add_node("class", f"{c.file}#{c.name}", name=c.name, fqn=f"{c.file}#{c.name}", file=c.file, line=c.line,
                       end_line=c.raw.get("end"), module=module_of(c.file), lang="dart",
                       attrs={k: v for k, v in {"dart_kind": c.kind if c.kind != "class" else None,
                                                "annotations": c.ann or None, "abstract": c.raw.get("abstract"),
                                                "sealed": c.raw.get("sealed")}.items() if v})
            b.add_edge(f"module:{c.file}", c.id, "CONTAINS", c.file, c.line, EXACT)
        for fn in prog.funcs.values():
            kind = "function" if fn.cls is None else "method"
            attrs = {k: v for k, v in {"dart_kind": fn.kind if fn.kind not in ("function", "method") else None,
                                       "static": fn.raw.get("static"), "abstract": fn.raw.get("abstract"), "async": fn.raw.get("async"),
                                       "returns": fn.raw.get("ret"), "event": fn.raw.get("event"),
                                       "annotations": [a["name"] for a in fn.raw.get("ann") or []] or None}.items() if v}
            b.add_node(kind, fn.qual, name=fn.name, fqn=fn.qual, file=fn.file, line=fn.line, end_line=fn.raw.get("end"),
                       module=module_of(fn.file), lang="dart", attrs=attrs or None)
            b.add_edge(fn.cls.id if fn.cls else f"module:{fn.file}", fn.id, "CONTAINS", fn.file, fn.line, EXACT)
        # ---- imports
        n_imp = 0
        for lib in prog.libs.values():
            for imp in lib.imports:
                cond = {"conditional": True} if imp.get("configs") else {}
                if imp["lib"] is not None:
                    b.add_edge(f"module:{imp['file']}", imp["lib"].id, "IMPORTS", imp["file"], imp.get("l"), EXACT, **cond); n_imp += 1
                self._conditional(b, imp, "import")
            for ex in lib.exports:
                if ex.get("configs"):
                    if ex["lib"] is not None:
                        b.add_edge(f"module:{ex['file']}", ex["lib"].id, "IMPORTS", ex["file"], ex.get("l"), EXACT,
                                   conditional=True, via="export")
                    self._conditional(b, ex, "export")
                elif ex["lib"] is not None:     # re-exported unconditionally (lib/x.dart: export 'src/x.dart'): built everywhere
                    b.platform_imports.append({"plain": ex["lib"].file})
                    b.add_edge(f"module:{ex['file']}", ex["lib"].id, "IMPORTS", ex["file"], ex.get("l"), EXACT, via="export")
            for p in lib.parts:
                b.add_edge(lib.id, f"module:{p}", "CONTAINS", lib.file, 1, EXACT, via="part")
        self._variant_reexports(prog, b)
        # ---- inheritance / dispatch
        n_ext = 0
        for c in prog.classes.values():
            for rel, t in c.supers:
                sc = prog.resolve_class(c.lib, split_type(t)[0])
                if sc:
                    b.add_edge(c.id, sc.id, "IMPLEMENTS" if rel == "implements" else "EXTENDS", c.file, c.line, EXACT,
                               **({"via": rel} if rel in ("with", "on") else {}))
                    n_ext += 1
                else:
                    b.nodes[c.id].attrs.setdefault("ext_bases", []).append(t)
            for name, m in c.methods.items():
                if m.raw.get("static"):
                    continue
                for s in prog.mro(c):
                    if name in s.methods and not s.methods[name].raw.get("static"):
                        sm = s.methods[name]
                        kind = "IMPLEMENTED_BY" if (sm.raw.get("abstract") or s.kind == "mixin" and False) or any(
                            r == "implements" and split_type(t)[0].split(".")[-1] == s.name for r, t in c.supers) else "OVERRIDDEN_BY"
                        b.add_edge(sm.id, m.id, kind, m.file, m.line, RESOLVED)
                        break
        # ---- calls
        conf_ct = {EXACT: 0, RESOLVED: 0, HEURISTIC: 0}
        n_env = 0

        def emit_calls(src_id: str, file: str, facts_: list, ctx: Ctx, fn: DFunc | None):
            nonlocal n_env
            for f in facts_:
                if f["ft"] in ("call", "new"):
                    for tgt, conf, via in prog.resolve_call(f, ctx):
                        if isinstance(tgt, DClass):
                            b.add_edge(src_id, tgt.id, "INSTANTIATES", file, f.get("l"), conf)
                            ct = tgt.ctors.get(via or "new") if via != "new" else tgt.ctors.get("new")
                            if ct:
                                b.add_edge(src_id, ct.id, "CALLS", file, f.get("l"), conf, via="constructor")
                                prog.callers.setdefault(ct.id, []).append((fn, f, ctx))
                            if via and via in tgt.methods:  # static call through type
                                b.add_edge(src_id, tgt.methods[via].id, "CALLS", file, f.get("l"), conf)
                                prog.callers.setdefault(tgt.methods[via].id, []).append((fn, f, ctx))
                        else:
                            rc = getattr(prog, "last_recv", None)
                            b.add_edge(src_id, tgt.id, "CALLS", file, f.get("l"), conf, **({"via": via} if via else {}),
                                       **({"recv": [rc.id]} if rc is not None and rc.kind != "extension" else {}))
                            prog.callers.setdefault(tgt.id, []).append((fn, f, ctx))
                        conf_ct[conf] += 1
                    k = env_key(f)
                    if k:
                        b.add_edge(src_id, b.add_node("env", k[0], lang="env"), "READS_ENV", file, f.get("l"), EXACT, via=k[1]); n_env += 1
                elif f["ft"] == "index":
                    k = env_key_idx(f)
                    if k:
                        b.add_edge(src_id, b.add_node("env", k[0], lang="env"), "READS_ENV", file, f.get("l"), EXACT, via=k[1]); n_env += 1

        for fn in prog.funcs.values():
            emit_calls(fn.id, fn.file, fn.facts, prog.ctx_of(fn), fn)
            if fn.kind == "ctor" and fn.raw.get("redirect"):
                tname = fn.raw["redirect"].split(".")[0]
                tc = prog.resolve_class(fn.lib, tname)
                if tc:
                    b.add_edge(fn.id, tc.id, "INSTANTIATES", fn.file, fn.line, EXACT, via="redirecting factory")
        for c in prog.classes.values():  # field initialisers run when the object is created
            fs = []
            for v in c.fields.values():
                for x in walk_repr(v.init):
                    if x.get("k") in ("call", "new"):
                        y = dict(x)
                        y["ft"] = x["k"]
                        y["l"] = x.get("l") or v.line
                        fs.append(y)
            if fs:
                emit_calls(c.id, c.file, fs, Ctx(prog, c.lib, None, c), None)
        for v in prog.vars:
            ctx = Ctx(prog, v.lib, None, None)
            emit_calls(f"module:{v.file}", v.file, v.facts, ctx, None)
        # ---- JSON models
        mi: ModelIndex = prog.models
        n_models = 0
        for c in prog.classes.values():
            m = mi.model(c)
            attrs = {}
            if m.get("from"):
                attrs["json_from"] = m["from"]
                attrs["json_from_source"] = m.get("from_source")
            if m.get("to"):
                attrs["json_to"] = m["to"]
                attrs["json_to_source"] = m.get("to_source")
            if m.get("enum_values"):
                attrs["enum_values"] = m["enum_values"]
            if m.get("field_rename"):
                attrs["field_rename"] = m["field_rename"]
            if attrs.get("json_from") or attrs.get("json_to"):
                n_models += 1
            if attrs:
                b.nodes[c.id].attrs.update(attrs)
        for fn in prog.funcs.values():
            ctx = prog.ctx_of(fn)
            for f in fn.facts:
                if f["ft"] == "call" and f.get("n") in ("fromJson", "fromMap") and (f.get("t") or {}).get("k") == "id":
                    tc = prog.resolve_class(fn.lib, f["t"]["v"])
                    if tc:
                        b.add_edge(fn.id, tc.id, "PARSES_JSON", fn.file, f.get("l"), EXACT, role="fromJson")
                elif f["ft"] == "call" and f.get("n") in ("toJson", "toMap") and f.get("t"):
                    tt = prog.infer(f["t"], ctx)
                    if tt and tt[0] == "inst" and tt[1] is not None and mi.keys(tt[1], "to"):
                        b.add_edge(fn.id, tt[1].id, "PARSES_JSON", fn.file, f.get("l"), RESOLVED, role="toJson")
        # ---- HTTP
        n_http, http_stats = self.emit_http(project, prog, b)
        # ---- Flutter platform channels (send side; codegraph/bridges.py adds the native receivers)
        from .bridges import emit as emit_bridges
        bst = emit_bridges(prog, UrlEval(prog, prog.env_values), b)
        if bst["flutter"] or bst["flutter-event"] or bst["unresolved_channel"] or bst.get("dart_handlers"):
            st["platform_channel_sends"] = bst
        # ---- Pigeon APIs (definitions -> endpoint:pigeon:<Api>#<method>; bridges.py adds the native side)
        from .bridges import emit_pigeon, pigeon_apis
        apis = pigeon_apis(prog)
        if apis:
            b.pigeon_apis = apis
            st["pigeon"] = {"apis": len(apis), "host_apis": sum(1 for v in apis.values() if v["kind"] == "host"),
                            "flutter_apis": sum(1 for v in apis.values() if v["kind"] == "flutter"),
                            **emit_pigeon(prog, b, apis)}
        st.update({"libraries": len(prog.libs), "classes": len(prog.classes),
                   "functions": sum(1 for f in prog.funcs.values() if f.cls is None),
                   "methods": sum(1 for f in prog.funcs.values() if f.cls is not None), "imports": n_imp, "extends": n_ext,
                   "calls": conf_ct, "env_reads": n_env, "json_models": n_models, "http_calls": n_http, **http_stats,
                   "parse_error_sample": [f["file"] for f in prog.files.values() if f.get("errors")][:10],
                   "seconds": round(time.time() - t0, 2)})
        return st

    # ------------------------------------------------------------------ http
    def emit_http(self, project: Project, prog: DartProgram, b: GraphBuilder):
        ev = UrlEval(prog, prog.env_values)
        hx = HttpExtractor(prog, ev, prog.models.keys)
        prog.http = hx
        calls = hx.scan()
        api_hosts = set()
        records = []
        for c in calls:
            for rec in self.expand(prog, hx, ev, c):
                records.append(rec)
                t = rec["tpl"]
                m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*://[^/]*)", t.text)
                if m and (t.named or t.env):
                    api_hosts.add(m.group(1))
        n = 0
        unresolved = 0
        for rec in records:
            t: Tpl = rec["tpl"]
            path, info = normalize_url(t.text)
            origin = info.get("origin")
            if rec.get("dio_base_unknown"):
                okind = "unknown"
                origin = origin or "{dio.baseUrl}"
            elif origin and origin.startswith("{env:"):
                okind = "env"  # configured server URL (env key) whose value is not in a .env file
            elif origin and origin in api_hosts:
                okind = "api"
            elif origin and "://" in origin:
                okind = "other"
            elif origin and path == "/" and not info.get("query") and re.fullmatch(r"\{[^{}]*\}", origin):
                okind = "dynamic"  # the whole URL is one runtime value (`http.get(Uri.parse(photo.url))`)
            elif origin:
                okind = "unknown"
            else:
                okind = "unknown" if info.get("relative") or rec["client"] in ("http", "websocket", "dart:io") else "api"
            if rec["method"] is None:
                method = "ANY"
            else:
                method = rec["method"]
            if "{?}" in path:
                unresolved += 1
            key = f"{method} {path}" if okind in ("api", "unknown") else (
                f"{method} {origin}" if okind == "dynamic" else f"{method} {origin or ''}{path}")
            nid = b.add_node("http", key, key, fqn=key, lang="dart",
                             attrs={"method": method, "path": path, "client": rec["client"], "origin": origin, "origin_kind": okind})
            body = rec.get("body_keys") or []
            resp = rec.get("response") or {}
            b.add_edge(rec["src"], nid, "HTTP_CALLS", file=rec["file"], line=rec["line"], confidence=min_conf(rec["conf"], t.conf),
                       client=rec["client"], url=t.text, origin=origin, query=info.get("query"), trailing_slash=info.get("trailing_slash"),
                       env=t.env or None, via_helper=rec.get("via_helper"), helper_chain=rec.get("chain") or None,
                       body_keys=body or None, body_opaque=rec.get("body_opaque") or None, multipart=rec.get("multipart") or None,
                       response_keys=resp.get("keys") or None, response_models=resp.get("models") or None,
                       status_checks=resp.get("status") or None, declared=rec.get("declared") or None)
            n += 1
        return n, {"http_unresolved_paths": unresolved, "dio_base_urls": sorted({t.text for t, *_ in hx.dio_bases})[:5]}

    def expand(self, prog: DartProgram, hx: HttpExtractor, ev: UrlEval, c: dict):
        """Evaluate a call's URL; helpers (URL from a parameter) are expanded at their call sites (<= 3 levels)."""
        fn: DFunc = c["fn"]

        def make(ctx: Ctx, site_fn: DFunc, site_file, site_line, chain, inner_fns, conf_extra=EXACT):
            t = c["tpl"] if c.get("tpl") is not None else ev.eval(c["url"], ctx)
            method = c["method"]
            if method is None and c.get("method_repr") is not None:
                mt = ev.eval(c["method_repr"], ctx)
                method = mt.text.upper() if mt.text and "{" not in mt.text else None
            recs = [(t, EXACT)]
            if c["client"] in ("dio", "retrofit", "chopper") and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", t.text) \
                    and not t.text.startswith("{") and (c["client"] == "dio" or c.get("base_from_dio")):
                bases = hx.dio_base_for(fn)
                recs = [(join(bt, t), bconf) for bt, bconf in bases] if bases else [(t, "unknown-base")]
            res = []
            for tt, bconf in recs:
                body_keys = list(c.get("body_keys") or [])
                opaque = False
                if c.get("body") is not None and not body_absent(c["body"], ctx):
                    ks = hx.keys_of(c["body"], ctx)
                    body_keys += ks
                    opaque = not ks
                resp = {"keys": [], "models": [], "status": []}
                for f_ in [site_fn] + [x for x in inner_fns if x is not site_fn]:
                    r_ = hx.response_facts(f_, prog.ctx_of(f_))
                    for k_ in resp:
                        resp[k_] += r_.get(k_) or []
                res.append({"src": site_fn.id, "file": site_file, "line": site_line, "tpl": tt, "method": method, "client": c["client"],
                            "conf": min_conf(c["conf"], conf_extra, bconf if bconf in CONFIDENCE_RANK else EXACT),
                            "dio_base_unknown": bconf == "unknown-base", "body_keys": body_keys, "body_opaque": opaque,
                            "response": resp, "via_helper": fn.id if site_fn is not fn else None, "chain": chain,
                            "multipart": c.get("multipart"), "declared": c.get("declared")})
            return res

        def needs(recs):
            return any(TOKEN.search(r["tpl"].text) for r in recs) or (c.get("method_repr") is not None and recs and recs[0]["method"] is None)

        first = make(prog.ctx_of(fn), fn, c["file"], c["line"], [], [])
        if not needs(first) or c.get("declared"):
            return first
        results, frontier = [], [[]]
        for _level in range(3):
            nxt = []
            for chain in frontier:
                callee = chain[-1][1] if chain else fn
                for caller, fact, _cctx in prog.callers.get(callee.id, [])[:MAXSITES]:
                    if caller is None or caller is fn or any(caller is x[1] for x in chain):
                        continue
                    ch = chain + [(callee, caller, fact)]
                    ctx = prog.ctx_of(ch[-1][1])
                    for callee_, caller_, fact_ in reversed(ch):
                        ctx = Ctx(prog, callee_.lib, callee_, callee_.cls, bind_args(callee_, fact_, ctx))
                    recs = make(ctx, caller, caller.file, fact.get("l"), [f"{x[1].file}:{x[2].get('l')}" for x in ch],
                                [fn] + [x[1] for x in ch[:-1]], RESOLVED)
                    if needs(recs):
                        nxt.append(ch)
                    else:
                        results += recs
            frontier = nxt[:100]
            if not frontier:
                break
        if not results:
            for r in first:
                r["tpl"] = Tpl(TOKEN.sub(lambda m: "{" + m.group(1) + "}", r["tpl"].text), HEURISTIC, r["tpl"].named, r["tpl"].env)
                r["helper_unexpanded"] = True
            return first
        return results


MAXSITES = 60


def body_absent(r, ctx: Ctx) -> bool:
    """`body: jsonEncode(body)` in a helper whose optional `body` argument was not passed at this call site."""
    while isinstance(r, dict) and r.get("k") in ("call", "nn", "as") and (r.get("k") != "call" or r.get("n") in ("jsonEncode", "encode", None)):
        if r.get("k") == "call":
            a = r.get("a") or []
            if not a:
                return False
            r = a[0]
        else:
            r = r["e"]
    if isinstance(r, dict) and r.get("k") == "id" and r["v"] in ctx.bindings:
        return ctx.bindings[r["v"]][0] is None or (ctx.bindings[r["v"]][0] or {}).get("k") == "null"
    return isinstance(r, dict) and r.get("k") == "null"


def env_key(f: dict):
    n, t = f.get("n"), f.get("t") or {}
    a = f.get("a") or []
    if n == "fromEnvironment" and root_name(t) in ("String", "bool", "int") and a and a[0].get("k") == "str":
        return a[0]["v"], f"{root_name(t)}.fromEnvironment"
    if n in ("get", "maybeGet", "getOrElse", "getBool", "getInt") and (root_name(t) or "").lower() in ("dotenv", "env") and a and a[0].get("k") == "str":
        return a[0]["v"], "dotenv.get"
    return None


def env_key_idx(f: dict):
    tg = f.get("target") or {}
    if tg.get("k") == "prop" and tg.get("n") in ("env", "environment") and (f.get("key") or {}).get("k") == "str":
        return f["key"]["v"], "dotenv.env[]" if tg.get("n") == "env" else "Platform.environment[]"
    return None

"""Language coverage of an index: which source files cg analysed, how (exact / heuristic), and which it could not.

Recorded at index time in the DB meta (stats.coverage) and shown by `cg coverage`, the MCP `coverage` tool and the
notes on empty MCP replies, so an agent knows when an empty answer is not proof of absence and it must fall back to
its normal search and file reading."""
from __future__ import annotations

import os
from collections import Counter

from . import presets
from .core.fsutil import is_real_file
from pathlib import Path

# source extensions per language plugin (key = plugin name in stats["plugins"])
SUPPORTED = {
    "php": (".php",),
    "typescript": (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".vue"),
    "python": (".py",),
    "dart": (".dart",),
    "rust": (".rs",),
    "c_cpp": (".c", ".h", ".cc", ".cpp", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++", ".ipp", ".inl"),
    "kotlin": (".kt", ".kts"),
    "swift": (".swift",),
}
# source types without a native plugin (go / java can be imported from a SCIP index). A generic "looks like source" rule:
# text source extensions of programming / scripting languages, plus a shebang for extensionless scripts (SHEBANGS).
# Data, markup, config and asset extensions are not listed, so they never count as unsupported source.
UNSUPPORTED = {
    ".go": "go", ".java": "java", ".rb": "ruby", ".cs": "csharp",
    ".scala": "scala", ".ex": "elixir", ".exs": "elixir", ".m": "objective-c", ".mm": "objective-c", ".lua": "lua",
    ".pl": "perl", ".pm": "perl", ".clj": "clojure", ".erl": "erlang", ".hrl": "erlang", ".hs": "haskell", ".fs": "fsharp",
    ".fsx": "fsharp", ".groovy": "groovy", ".r": "r", ".jl": "julia", ".zig": "zig", ".sol": "solidity",
    ".qml": "qml", ".sh": "sh", ".bash": "sh", ".zsh": "sh", ".ksh": "sh", ".fish": "fish", ".ps1": "powershell",
    ".psm1": "powershell", ".bat": "batch", ".cmd": "batch", ".svelte": "svelte", ".astro": "astro",
    ".coffee": "coffeescript", ".elm": "elm", ".ml": "ocaml", ".mli": "ocaml", ".nim": "nim", ".cr": "crystal",
    ".rkt": "racket", ".tcl": "tcl", ".vb": "visual-basic", ".cu": "cuda", ".cuh": "cuda", ".gd": "gdscript",
    ".hx": "haxe", ".pas": "pascal", ".f90": "fortran", ".f95": "fortran", ".adb": "ada", ".ads": "ada", ".vala": "vala",
    ".purs": "purescript", ".pyx": "cython", ".v": "verilog", ".sv": "systemverilog", ".vhd": "vhdl",
}
# interpreter of a `#!` line -> language, for extensionless scripts (bin/deploy, scripts/release) in languages cg has
# no plugin for. Extensionless launchers of indexed languages (`artisan`, `bin/console`, `manage`) are not counted.
SHEBANGS = {"sh": "sh", "bash": "sh", "zsh": "sh", "dash": "sh", "ksh": "sh", "ash": "sh", "fish": "fish",
            "perl": "perl", "ruby": "ruby", "lua": "lua", "pwsh": "powershell", "rscript": "r", "elixir": "elixir",
            "julia": "julia", "tclsh": "tcl"}
SHEBANG_EXT = "(shebang)"
# per-file buckets of a language: discovered = indexed + parse_failed + skipped_oversize + excluded + unmapped
BUCKETS = ("parse_failed", "skipped_oversize", "unmapped", "excluded")
BUCKET_TEXT = {"parse_failed": "parse failed", "skipped_oversize": "over size limit",
               "unmapped": "unmapped (no module path: outside the source roots or not a valid package path)",
               "excluded": "excluded (skip list: migrations, generated or cache directories)"}
BUCKET_SHORT = {"parse_failed": "parse failed", "skipped_oversize": "over size limit", "unmapped": "unmapped",
                "excluded": "excluded"}
LANG_LABEL = {"php": "PHP", "typescript": "TypeScript / JavaScript", "python": "Python", "dart": "Dart", "rust": "Rust",
              "c_cpp": "C / C++", "kotlin": "Kotlin", "swift": "Swift"}
MAX_PATHS = 500      # file paths stored per bucket in the index (counts are always exact)
SHOW_PATHS = 5       # shown per bucket by default (`cg coverage --all-files` / coverage(all_files=true) for all)
HINTS = {
    "php": "install PHP 8.2+ and run `(cd codegraph/plugins/php/extractor && composer install)`",
    "typescript": "install Node.js 20+ and run `(cd codegraph/plugins/ts/extractor && npm ci)`",
    "dart": "install the Dart SDK 3.x (`dart` on PATH or $DART)",
    "rust": "exact mode needs rust-analyzer (`rustup component add rust-analyzer`); the tree-sitter layer needs "
            "`pip install tree-sitter tree-sitter-rust`",
    "c_cpp": "exact mode needs scip-clang and a compile_commands.json (docs/native.md); the tree-sitter layer needs "
             "`pip install tree-sitter tree-sitter-c tree-sitter-cpp`",
    "python": "the .py files are only in directories the Python plugin skips (virtualenvs, build output, static/, media/); "
              "index the directory that holds your code",
    "kotlin": "heuristic mode (tree-sitter syntax layer, name-based call resolution); the layer needs "
              "`pip install tree-sitter tree-sitter-kotlin`. For compiler-resolved references index the Gradle / Maven "
              "build with scip-java and pass `--scip index.scip` (docs/kotlin.md)",
    "swift": "heuristic mode (tree-sitter syntax layer, name-based call resolution; runs on Linux without Xcode); "
             "the layer needs `pip install tree-sitter tree-sitter-swift` (docs/swift.md)",
    "go": "no native plugin: index with scip-go and pass `--scip index.scip`",
    "java": "no native plugin: index with scip-java and pass `--scip index.scip`",
}
# dependency / build / cache directories the scan never descends into (codegraph/presets/common.yaml)
SKIP_DIRS = presets.skip_dirs("common", "scan_skip_dirs")
SHOW_ROOTS = 6      # Python source roots shown by default
FALLBACK = "use your normal search and file reading for those parts; an empty cg answer there is not proof of absence"


class Scan:
    """One walk of the indexed root: file counts by extension, repo-relative paths of supported source files, and
    extensionless scripts by shebang language."""

    def __init__(self):
        self.counts: Counter = Counter()
        self.paths: dict[str, list[str]] = {}
        self.scripts: Counter = Counter()
        self.bridge_paths: list[str] = []      # Java / ObjC files: no language plugin, scanned for bridge receivers

    def files(self, exts) -> list[str]:
        return [f for e in exts for f in self.paths.get(e, [])]


_SUPPORTED_EXTS = {e for v in SUPPORTED.values() for e in v}


def _shebang(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            head = fh.read(128)
    except OSError:
        return None
    if not head.startswith(b"#!"):
        return None
    words = head[2:].split(b"\n", 1)[0].decode("utf-8", "replace").split()
    if not words:
        return None
    prog = os.path.basename(words[0])
    if prog == "env":
        rest = [w for w in words[1:] if not w.startswith("-") and "=" not in w]
        prog = rest[0] if rest else ""
    prog = prog.lower()
    return SHEBANGS.get(prog) or SHEBANGS.get(prog.rstrip("0123456789.")) or None


def scan_tree(root: str | Path, rules=None, classifier=None) -> Scan:
    """Source files under root (generated / dependency directories skipped; `rules`: the project's PathRules,
    default the built-in scan list). `classifier` (codegraph/core/generated.py) classifies every source file seen; the
    files it marks are left out of the counts unless the index includes generated files."""
    from .core.paths import PathRules
    rules = rules or PathRules(SKIP_DIRS)
    sc = Scan()
    root = str(root)
    for dp, dns, fns in os.walk(root):
        rd = os.path.relpath(dp, root)
        rd = "" if rd == "." else rd.replace(os.sep, "/")
        if classifier is not None:
            classifier.visit_dir(rd, dp, dns, fns)
        rel_dir = rd + "/" if rd else ""
        keep = []
        for d in dns:
            if rules.skip(rd, d) or d.startswith("._"):
                if classifier is not None:
                    classifier.skipped_dir(rel_dir + d, d)
            else:
                keep.append(d)
        dns[:] = keep
        for fn in fns:
            if fn.startswith("._") or (rules.exclude and rules.excluded(rel_dir + fn)):
                continue
            ext = os.path.splitext(fn)[1].lower()
            p = os.path.join(dp, fn)
            if ext:
                if (ext in _SUPPORTED_EXTS or ext in UNSUPPORTED) and not is_real_file(p):
                    continue
                if classifier is not None and (ext in _SUPPORTED_EXTS or ext in UNSUPPORTED) \
                        and classifier.scan(rel_dir + fn, p) is not None and not classifier.include:
                    continue
                sc.counts[ext] += 1
                if ext in _SUPPORTED_EXTS:
                    sc.paths.setdefault(ext, []).append(rel_dir + fn)
                elif ext in (".java", ".m", ".mm"):
                    sc.bridge_paths.append(rel_dir + fn)
            elif not fn.startswith(".") and is_real_file(p):
                lang = _shebang(p)
                if lang:
                    sc.scripts[lang] += 1
    return sc


def scan(root: str | Path) -> Counter:
    """Source files by extension under root (generated / dependency directories skipped)."""
    return scan_tree(root).counts


def file_completeness(discovered: list[str], report: dict) -> dict:
    """Bucket every discovered file of one language from the plugin's per-file report
    ({seen, parse_failed, skipped_oversize, unmapped, excluded}). A discovered file the plugin never looked at
    (its own skip list) is `excluded`."""
    sets = {b: set(report.get(b) or ()) for b in BUCKETS}
    seen = set(report.get("seen") or ())
    out = {b: [] for b in BUCKETS}
    indexed = 0
    for f in sorted(discovered):
        for b in ("parse_failed", "skipped_oversize", "unmapped", "excluded"):
            if f in sets[b]:
                out[b].append(f)
                break
        else:
            if f in seen:
                indexed += 1
            else:
                out["excluded"].append(f)
    res = {"discovered": len(discovered), "indexed": indexed}
    for b in BUCKETS:
        res[b] = len(out[b])
    res["paths"] = {b: v[:MAX_PATHS] for b, v in out.items() if v}
    return res


def missing_files(e: dict) -> int:
    """Discovered files of a language entry that are not in the graph for a reason other than a deliberate exclusion."""
    return sum(e.get(b, 0) for b in ("parse_failed", "skipped_oversize", "unmapped"))


def _status(lang: str, st: dict | None) -> tuple[str, str | None]:
    if st is None:
        return "not_indexed", None
    s = st.get("status")
    if s in ("skipped", "error", "stub"):
        return "skipped", st.get("reason")
    mode = st.get("mode")
    if lang in ("rust", "c_cpp") and mode and mode != "scip":
        sc = st.get("scip") if isinstance(st.get("scip"), dict) else {}
        if sc.get("error"):     # the exact indexer is installed but its run failed: say why the heuristic layer was used
            last = next((x.strip() for x in reversed(sc.get("stderr_tail") or []) if x.strip()), "")
            return "heuristic", (f"exact indexer run failed ({sc['error']}" + (f": {last[:160]}" if last else "")
                                 + "); heuristic layer used")
        return "heuristic", None
    if lang == "swift" and mode == "heuristic":
        return "heuristic", ("tree-sitter syntax layer with name-based call resolution (no compiler index; works "
                             "without Xcode or a Swift toolchain)")
    if lang == "kotlin" and mode == "heuristic":
        return "heuristic", "tree-sitter syntax layer with name-based call resolution (no compiler index)"
    if lang == "typescript" and st.get("program_files") == 0 and not st.get("nodes"):
        return "not_indexed", "the TypeScript plugin ran but found no source files (tsconfig include / source dirs)"
    return "exact", None


def compute(root: str | Path, plugins: dict, scip_imported: bool = False, reports: dict | None = None,
            scanned: Scan | None = None, blind_spots: list | None = None) -> dict:
    """Coverage entry list from the file scan, the per-plugin index stats and per-file reports, plus the blind spots
    found at index time (codegraph/blindspots.py)."""
    sc = scanned or scan_tree(root)
    files = sc.counts
    reports = reports or {}
    langs = []
    for lang, exts in SUPPORTED.items():
        n = sum(files[e] for e in exts)
        st = plugins.get(lang)
        if not n and st is None:
            continue
        status, reason = _status(lang, st)
        e = {"language": lang, "files": n, "status": status,
             "by_ext": {x: files[x] for x in exts if files[x]}}
        if reason:
            e["reason"] = reason
        if status in ("skipped", "heuristic", "not_indexed"):
            e["hint"] = HINTS.get(lang)
        if status == "not_indexed" and lang == "typescript" and "php" in plugins:
            e["hint"] = "no tsconfig.json / package.json with typescript at the indexed root: index the frontend directory separately"
        rep = reports.get(lang)
        if rep is not None and status not in ("skipped", "not_indexed"):
            fc = file_completeness(sc.files(exts), rep)
            e.update({k: v for k, v in fc.items() if k != "paths"})
            if fc["paths"]:
                e["paths"] = fc["paths"]
            e["files_complete"] = missing_files(e) == 0
            if not e["files_complete"] and lang == "python" and e.get("unmapped"):
                e["hint"] = _python_unmapped_hint(st)
        if lang == "python" and st:
            for k in ("roots_mode", "source_roots", "roots_warnings", "roots_ambiguous", "module_name_collisions"):
                if st.get(k):
                    e[k] = st[k]
            t = st.get("tests") or {}
            if t.get("cases"):
                e["tests"] = {"cases": t["cases"], "test_files": sum((t.get("test_files") or {}).values()),
                              "fixtures": t.get("fixtures", 0),
                              **({"http": {k: v for k, v in t["http"].items() if k in ("requests", "matched", "unmatched", "url_unknown")}}
                                 if t.get("http") else {})}
        langs.append(e)
    other: dict = {}
    for ext, lang in UNSUPPORTED.items():
        if files[ext]:
            o = other.setdefault(lang, {"language": lang, "files": 0, "status": "unsupported", "by_ext": {}})
            o["files"] += files[ext]
            o["by_ext"][ext] = files[ext]
    for lang, n in sorted(sc.scripts.items()):
        o = other.setdefault(lang, {"language": lang, "files": 0, "status": "unsupported", "by_ext": {}})
        o["files"] += n
        o["by_ext"][SHEBANG_EXT] = n
    for lang, st in plugins.items():
        if lang in ("go", "java") and lang in other:
            if st.get("status") == "stub":
                other[lang]["reason"] = st.get("reason")
            elif "status" not in st:
                other[lang]["status"] = "exact"  # a SCIP indexer ran
    for o in other.values():
        if o["status"] == "unsupported":
            if scip_imported and o["language"] in ("go", "java"):
                o["status"] = "scip"
            o["hint"] = HINTS.get(o["language"], "no plugin for this language")
        langs.append(o)
    out = {"languages": langs, "gaps": sum(1 for e in langs if _is_gap(e))}
    if blind_spots:
        out["blind_spots"] = blind_spots
    return out


def _python_unmapped_hint(st: dict) -> str:
    if st.get("roots_mode") in ("configured", "flag"):
        where = "python.source_roots in .cg.yaml" if st["roots_mode"] == "configured" else "--python-root"
        roots = ", ".join(r["path"] for r in st.get("source_roots") or []) or "none"
        return (f"unmapped .py files are outside the configured source roots ({roots}): add their directories to {where}, "
                f"or drop the setting to use detection")
    return ("unmapped .py files are in directories that are not importable module paths (a name with '-' or '.'), "
            "outside the detected source roots, or claim a module name another file has; list their roots under "
            "python.source_roots in .cg.yaml")


def python_roots_lines(e: dict, all_files: bool = False, indent: str = "  ") -> list[str]:
    """'python source roots: lib/ (detected: parent of top-level package core, 3 modules); ...' plus warnings and the
    modules reachable from two roots. Empty for the plain layout (only the indexed root, nothing to warn about)."""
    roots = e.get("source_roots") or []
    mode = e.get("roots_mode")
    plain = mode == "detected" and all(r["path"] == "./" for r in roots)
    out = []
    if roots and (all_files or not plain):
        ranked = sorted(roots, key=lambda r: (-r.get("modules", 0), r["path"]))
        show = ranked if all_files else ranked[:SHOW_ROOTS]
        txt = "; ".join(f"{r['path']}{' as ' + r['package'] if r.get('package') else ''} ({r['origin']}: {r['why']}, "
                        f"{r.get('modules', 0)} module{'s' if r.get('modules', 0) != 1 else ''})" for r in show)
        more = len(roots) - len(show)
        out.append(f"{indent}python source roots: {txt}" + (f" … +{more} more (--all-files)" if more > 0 else ""))
    for w in e.get("roots_warnings") or []:
        out.append(f"{indent}python warning: {w}")
    amb = e.get("roots_ambiguous")
    if amb:
        s = amb["samples"][0]
        out.append(f"{indent}python: {amb['count']} module{'s' if amb['count'] != 1 else ''} importable from two roots, named "
                   f"after the project's imports (e.g. {s['file']} -> {s['chosen']}, not {s['also'][0]})")
    col = e.get("module_name_collisions")
    if col:
        s = col["samples"][0]
        alt = f"named {s['named']}" if s.get("named") else "not indexed"
        moved = col.get("path_named", 0)
        one = col["count"] == 1
        out.append(f"{indent}python: {col['count']} file{'' if one else 's'} claim{'s' if one else ''} a module name another "
                   f"file has (e.g. {s['file']}: {s['name']} is {s['with']}; {alt})"
                   + (f"; {moved} file{' in that package tree is' if moved == 1 else 's in those package trees are'} named "
                      f"by {'its' if moved == 1 else 'their'} path from the indexed root" if moved else ""))
    return out


def python_tests_line(e: dict, indent: str = "  ") -> list[str]:
    """'python tests: 120 test cases (pytest 110, unittest 10) in 30 files, 45 fixtures; 12 HTTP requests, 10 linked to routes'."""
    t = e.get("tests") or {}
    cases = t.get("cases") or {}
    if not cases:
        return []
    def n(k, word):
        return f"{k} {word}{'' if k == 1 else 's'}"
    line = (f"{indent}python tests: {n(sum(cases.values()), 'test case')} ("
            + ", ".join(f"{k} {v}" for k, v in sorted(cases.items(), key=lambda x: (-x[1], x[0])))
            + f") in {n(t.get('test_files', 0), 'file')}" + (f", {n(t['fixtures'], 'fixture')}" if t.get("fixtures") else ""))
    h = t.get("http") or {}
    if h.get("requests"):
        line += f"; {n(h['requests'], 'HTTP test request')}, {h.get('matched', 0)} linked to routes"
    return [line]


def _is_gap(e: dict) -> bool:
    return bool(e.get("files")) and (e["status"] not in ("exact", "scip") or e.get("files_complete") is False)


def gaps(cov: dict | None) -> list[dict]:
    return [e for e in (cov or {}).get("languages", []) if _is_gap(e)]


def blind_spots(cov: dict | None) -> list[dict]:
    return list((cov or {}).get("blind_spots") or [])


def _entry_text(e: dict) -> str:
    """'php 18 exact' / 'python 4 discovered, 2 indexed (exact parser): 1 parse failed, 1 unmapped'."""
    if e.get("files_complete") is False:
        parts = [f"{e[b]} {BUCKET_SHORT[b]}" for b in ("parse_failed", "skipped_oversize", "unmapped") if e.get(b)]
        return (f"{e['language']} {e['files']} discovered, {e['indexed']} indexed ({e['status'].replace('_', ' ')} parser)"
                + (": " + ", ".join(parts) if parts else ""))
    return f"{e['language']} {e['files']} {e['status'].replace('_', ' ')}"


def summary_line(cov: dict | None, repo: str | None = None) -> str:
    """One line: 'coverage: php 18 exact; typescript 9 exact | not fully covered: go 3 unsupported (...)'."""
    if not cov:
        return "coverage: not recorded for this index (re-index with this version of cg)"
    pre = f"coverage{' ' + repo if repo else ''}: "
    ok = [_entry_text(e) for e in cov["languages"] if e["status"] in ("exact", "scip") and not _is_gap(e)]
    bad = [_entry_text(e) for e in gaps(cov)]
    if ok or not bad:
        s = pre + ("; ".join(ok) or "no supported source files")
        if bad:
            s += " | not fully covered: " + "; ".join(bad)
    else:
        s = pre + "not fully covered: " + "; ".join(bad)
    bs = blind_spots(cov)
    if bs:
        s += f" | blind spots: {_bs_count(bs)}"
    g = cov.get("generated") or {}
    if g.get("files"):
        s += f" | generated: {g['files']} file{'s' if g['files'] != 1 else ''} {g.get('mode', 'excluded')}"
    return s


def _bs_count(bs: list[dict]) -> str:
    r = sum(b["count"] for b in bs if b["category"] == "route")
    h = sum(b["count"] for b in bs if b["category"] != "route")
    parts = ([f"{r} unmodelled route registration{'s' if r != 1 else ''}"] if r else []) + \
            ([f"{h} handler{'s' if h != 1 else ''} registered dynamically"] if h else [])
    return ", ".join(parts)


def _paths_lines(e: dict, all_files: bool, indent: str = "    ") -> list[str]:
    out = []
    for b in BUCKETS:
        ps = (e.get("paths") or {}).get(b) or []
        if not ps or (b == "excluded" and not all_files):
            continue
        show = ps if all_files else ps[:SHOW_PATHS]
        more = e.get(b, len(ps)) - len(show)
        out.append(f"{indent}{BUCKET_SHORT[b]}: " + ", ".join(show) + (f" … +{more} more (--all-files)" if more > 0 else ""))
    return out


def blind_spot_lines(bs: list[dict], indent: str = "  ", limit: int = 3) -> list[str]:
    out = []
    for b in bs:
        more = b["count"] - min(limit, len(b["samples"]))
        out.append(f"{indent}{b['count']}× {b['what']} ({b['language']}): " + ", ".join(b["samples"][:limit])
                   + (f" … +{more}" if more > 0 else ""))
    return out


def setup_line(setup: dict) -> str:
    """Frameworks found, presets applied and the project config file of one index."""
    fw = ", ".join(setup.get("frameworks") or []) or "none"
    return (f"  frameworks: {fw} | presets: {', '.join(setup.get('presets') or [])}"
            f" | config: {setup.get('config') or 'no .cg.yaml'}")


def platform_lines(pc: dict | None) -> list[str]:
    """Per-target coverage of platform-specific code (codegraph/platforms.py): files and symbols each target builds."""
    if not pc or not pc.get("targets"):
        return []
    pt = pc.get("per_target") or {}
    parts = [f"{p} {pt[p]['files']} files / {pt[p]['symbols']} symbols ({pt[p]['platform_specific_symbols']} specific)"
             for p in pc["targets"] if p in pt]
    out = [f"  platforms: {'; '.join(parts)}"]
    if pc.get("unevaluated_conditions"):
        out.append(f"    {pc['unevaluated_conditions']} of {pc.get('conditions', '?')} platform conditions could not be "
                   f"evaluated and count for every target (e.g. {(pc.get('unevaluated_samples') or ['?'])[0]})")
    return out


def render(covs: dict[str, dict | None], all_files: bool = False) -> str:
    """Multi-line report for one or more repos (name -> coverage)."""
    out = []
    any_gap = any_bs = False
    for repo, cov in covs.items():
        out.append(summary_line(cov, repo if len(covs) > 1 or repo else None))
        if (cov or {}).get("setup"):
            out.append(setup_line(cov["setup"]))
        from .core.generated import detail_lines as generated_lines
        out += generated_lines((cov or {}).get("generated"), all_files)
        for e in (cov or {}).get("languages", []):
            if e["language"] == "python":
                out += python_roots_lines(e, all_files)
                out += python_tests_line(e)
        for e in gaps(cov):
            any_gap = True
            exts = ", ".join(f"{k} {v}" for k, v in sorted(e["by_ext"].items()))
            if e.get("files_complete") is False:
                parts = [f"{e[b]} {BUCKET_SHORT[b]}" for b in BUCKETS if e.get(b)]
                line = f"  {e['language']}: {e['files']} files ({exts}) {e['indexed']} indexed, " + ", ".join(parts)
            else:
                line = f"  {e['language']}: {e['files']} files ({exts}) {e['status'].replace('_', ' ')}"
            if e.get("reason"):
                line += f": {e['reason']}"
            out.append(line)
            out += _paths_lines(e, all_files)
            if e.get("hint"):
                out.append(f"    fix: {e['hint']}")
        if all_files:
            for e in (cov or {}).get("languages", []):
                if not _is_gap(e) and e.get("excluded"):
                    out.append(f"  {e['language']}: {e['excluded']} excluded")
                    out += _paths_lines(e, True)
        out += platform_lines((cov or {}).get("platforms"))
        bs = blind_spots(cov)
        if bs:
            any_bs = True
            out.append("  blind spots (patterns cg does not model; answers that touch them may be partial):")
            out += blind_spot_lines(bs, "    ", limit=10 if all_files else 3)
    if any_gap:
        out.append("not covered or heuristic only: " + FALLBACK + ".")
    elif any_bs:
        out.append("every source file cg found is indexed; at the blind spots above, use your normal search and file reading "
                   "(an empty cg answer there is not proof of absence).")
    else:
        out.append("every source file cg found is indexed; edges still carry their own exact / resolved / heuristic label.")
    return "\n".join(out)


def for_graph(store) -> dict[str, dict | None]:
    """Coverage per repo of a single or combined graph DB."""
    from .core.store import GraphStore
    try:
        m = store.meta()
    except Exception:  # noqa: BLE001  (not a cg graph yet: coverage unknown, never a crash)
        return {"": None}
    if m.get("repos"):
        if isinstance(m.get("coverage"), dict) and m["coverage"]:
            return dict(m["coverage"])          # copied in by cg link
        out = {}
        for r in m["repos"]:                    # older combined graphs: read the source DBs if they are still there
            here = Path(getattr(store, "path", "") or "").parent / f"{r}.db"
            for src in ((m.get("sources") or {}).get(r), str(here)):
                try:
                    if src and Path(src).exists():
                        out[r] = (GraphStore(src).meta().get("stats") or {}).get("coverage")
                        break
                except Exception:  # noqa: BLE001
                    pass
            else:
                out[r] = None
        return out
    return {m.get("project") or "": (m.get("stats") or {}).get("coverage")}


def note(covs: dict[str, dict | None]) -> str:
    """Short note for empty / unknown-symbol replies."""
    bad = []
    unknown = [r for r, c in covs.items() if c is None]
    for r, c in covs.items():
        for e in gaps(c):
            what = (f"{e['indexed']} of {e['files']} files indexed" if e.get("files_complete") is False
                    else f"{e['files']} files, {e['status'].replace('_', ' ')}")
            bad.append(f"{e['language']} ({what}{', ' + r if len(covs) > 1 else ''})")
    bs = [b for c in covs.values() for b in blind_spots(c)]
    bs_txt = f"; blind spots: {_bs_count(bs)} (see coverage)" if bs else ""
    if bad:
        return "coverage: not fully covered here: " + "; ".join(bad) + bs_txt + ". If the code you mean is there, " + FALLBACK + "."
    if unknown:
        return "coverage: not recorded for this index; if in doubt, " + FALLBACK + "."
    langs = sorted({e["language"] for c in covs.values() for e in (c or {}).get("languages", []) if e["files"]})
    if bs:
        return (f"coverage: every source file cg found is indexed ({', '.join(langs)}){bs_txt}; if the code you mean is "
                f"registered that way, {FALLBACK}.")
    return f"coverage: every source file cg found is indexed ({', '.join(langs)}); code outside these languages or generated at runtime is not in the graph."


# ------------------------------------------------------------------------------------------- scoped completeness

NODE_LANG = {"ts": "typescript", "js": "typescript", "php": "php", "python": "python", "dart": "dart", "rust": "rust",
             "c": "c_cpp", "cpp": "c_cpp"}


def _lang_of_file(f: str | None) -> str | None:
    ext = os.path.splitext(f or "")[1].lower()
    return next((k for k, v in SUPPORTED.items() if ext in v), None)


def _top(f: str | None) -> str:
    parts = (f or "").split("/")
    return parts[0] if len(parts) > 1 else ""


def scope_of(store, node_ids) -> dict:
    """Languages, repos and top-level directories of the nodes an answer is about (for a scoped completeness note)."""
    langs, dirs, repos = set(), set(), set()
    try:
        repo_names = set((store.meta() or {}).get("repos") or [])
    except Exception:  # noqa: BLE001
        repo_names = set()
    ids = [i for i in dict.fromkeys(node_ids or []) if i][:400]
    for k in range(0, len(ids), 200):
        chunk = ids[k:k + 200]
        rows = store.q(f"SELECT file, lang FROM nodes WHERE id IN ({','.join('?' * len(chunk))})", tuple(chunk))
        for r in rows:
            f = r["file"] or ""
            if repo_names and f.split("/", 1)[0] in repo_names:
                repo, f = f.split("/", 1) if "/" in f else (f, "")
                repos.add(repo)
            lang = NODE_LANG.get(r["lang"] or "") or _lang_of_file(f)
            if lang:
                langs.add(lang)
            if f:
                dirs.add(_top(f))
    return {"languages": langs, "dirs": dirs, "repos": repos}


def completeness(covs: dict[str, dict | None], languages=None, dirs=None, repos=None,
                 categories=("route", "handler"), unsupported: bool | None = None, ids=None) -> dict:
    """Machine-readable completeness of an answer, scoped to the languages / top-level directories / repos it
    involves (None = the whole index). {"complete": bool, "languages": {...}, "unsupported": {...},
    "blind_spots": [...]}. Route blind spots apply to every answer in their language (a route registered anywhere can
    reach the code); handler blind spots only when the answer involves one of the registered functions (`ids` = the
    answer's node ids: a target or caller the graph shows without its registration), or, for findings recorded
    without node ids, within the same top-level directory."""
    multi = len(covs) > 1
    whole = languages is None
    unsupported = whole if unsupported is None else unsupported
    langs_out, uns_out, bs_out = {}, {}, []
    known = True
    for repo, cov in covs.items():
        if repos and repo not in repos and multi:
            continue
        if cov is None:
            known = False
            continue
        for e in cov.get("languages", []):
            key = f"{repo}/{e['language']}" if multi and repo else e["language"]
            if e["status"] == "unsupported":
                if unsupported and e["files"]:
                    uns_out[key] = e["files"]
                continue
            if not whole and e["language"] not in languages:
                continue
            if not e.get("files"):
                continue
            d = {"mode": e["status"], "discovered": e["files"]}
            if "indexed" in e:
                d["indexed"] = e["indexed"]
                for b in BUCKETS:
                    if e.get(b):
                        d[b] = e[b]
            if e.get("reason"):
                d["reason"] = e["reason"]
            d["complete"] = not _is_gap(e)
            langs_out[key] = d
        for b in blind_spots(cov):
            if b["category"] not in categories or (not whole and b["language"] not in languages):
                continue
            if b["category"] != "route" and ids is not None and b.get("nodes"):
                samples = [s for n, s in zip(b["nodes"], b.get("node_samples") or b["samples"]) if n in ids]
                if not samples:
                    continue
            elif b["category"] != "route" and dirs is not None and "" not in dirs:
                samples = [s for s in b["samples"] if _top(s.rsplit(":", 1)[0]) in dirs or not _top(s.rsplit(":", 1)[0])]
                if not samples:
                    continue
            else:
                samples = None
            bs_out.append({"kind": b["kind"], "category": b["category"], "language": b["language"], "what": b["what"],
                           "count": b["count"] if samples is None else len(samples),
                           "sample": (samples or b["samples"])[0],
                           **({"repo": repo} if multi and repo else {})})
    complete = known and all(v["complete"] for v in langs_out.values()) and not uns_out and not bs_out
    out = {"complete": complete, "languages": langs_out}
    if uns_out:
        out["unsupported"] = uns_out
    if bs_out:
        out["blind_spots"] = bs_out
    if not known:
        out["recorded"] = False
    return out


def completeness_for(store, node_ids=None, categories=("route", "handler"), whole: bool = False, **kw) -> dict:
    try:
        covs = for_graph(store)
    except Exception:  # noqa: BLE001
        return {"complete": False, "recorded": False, "languages": {}}
    if whole or not node_ids:
        return completeness(covs, categories=categories, **kw)
    sc = scope_of(store, node_ids)
    if not sc["languages"]:
        return completeness(covs, categories=categories, unsupported=False, **kw)
    return completeness(covs, languages=sc["languages"], dirs=sc["dirs"], repos=sc["repos"] or None,
                        categories=categories, ids=set(node_ids), **kw)


def possibly_more(comp: dict) -> str:
    """'1 unmodelled route registration, 2 Python files not indexed' for an incomplete answer, '' when complete."""
    if comp.get("complete"):
        return ""
    parts = []
    r = sum(b["count"] for b in comp.get("blind_spots", []) if b["category"] == "route")
    h = sum(b["count"] for b in comp.get("blind_spots", []) if b["category"] != "route")
    if r:
        parts.append(f"{r} unmodelled route registration{'s' if r != 1 else ''}")
    if h:
        parts.append(f"{h} dynamically registered handler{'s' if h != 1 else ''}")
    for k, v in comp.get("languages", {}).items():
        if v["complete"]:
            continue
        lang = k.rsplit("/", 1)[-1]
        label = LANG_LABEL.get(lang, lang)
        miss = sum(v.get(b, 0) for b in ("parse_failed", "skipped_oversize", "unmapped"))
        if v["mode"] in ("exact", "scip") and miss:
            parts.append(f"{miss} {label} file{'s' if miss != 1 else ''} not indexed")
        elif v["mode"] == "heuristic":
            parts.append(f"{label} heuristic only")
        else:
            parts.append(f"{label} {v['mode'].replace('_', ' ')}")
    if comp.get("unsupported"):
        parts.append("unsupported: " + ", ".join(f"{k} {v}" for k, v in sorted(comp["unsupported"].items())))
    if comp.get("recorded") is False:
        parts.append("coverage not recorded")
    return ", ".join(parts)


def answer_note(comp: dict) -> str:
    """'coverage note: 1 route registration cg does not model (Django urlpatterns built by ...: shop/urls.py:10); 2 Python
    files not indexed' for an incomplete answer; '' when the answer is complete (no noise)."""
    if comp.get("complete"):
        return ""
    parts = []
    for b in comp.get("blind_spots", []):
        noun = "route registration" if b["category"] == "route" else "handler registration"
        parts.append(f"{b['count']} {noun}{'s' if b['count'] != 1 else ''} cg does not model ({b['what']}: {b['sample']})")
    rest = possibly_more({**comp, "blind_spots": []})
    if rest:
        parts.append(rest)
    return "coverage note: " + "; ".join(parts) + ". There, use your normal search and file reading (an empty cg answer is not proof of absence)."

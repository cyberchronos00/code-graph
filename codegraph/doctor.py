"""`cg doctor`: what this installation can index, and how.

Versions of cg and the tools it uses, whether the Node / PHP / Dart extractor dependencies are installed (and where:
codegraph/core/extractors.py), and per language whether `cg index` runs in exact or heuristic mode, why, and the
command that installs what is missing. Every module of the package is imported as well: `cg index` loads every
language plugin, so a module that does not import on this Python (syntax or an API newer than the running
interpreter) breaks indexing for every language; doctor names it and exits non-zero. With a project root it also checks project-level conditions (a
compile_commands.json for C / C++, a Gradle / Maven build for Kotlin, `.cg.yaml` rust.targets) and lists only the
languages the project has."""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .core import cache, extractors

INSTALL_DOC = "docs/install.md"
REPO = "https://github.com/cyberchronos00/code-graph"
GRAMMARS = {"rust": "tree_sitter_rust", "c_cpp": "tree_sitter_c", "kotlin": "tree_sitter_kotlin", "swift": "tree_sitter_swift"}
PIP_NAMES = {"tree_sitter": "tree-sitter", "tree_sitter_rust": "tree-sitter-rust", "tree_sitter_c": "tree-sitter-c",
             "tree_sitter_cpp": "tree-sitter-cpp", "tree_sitter_kotlin": "tree-sitter-kotlin", "tree_sitter_swift": "tree-sitter-swift"}


# language -> the plugin packages its `cg index` run loads (besides the indexer, which loads all of them)
PLUGIN_PACKAGES = {"python": ("python", "pyweb", "django"), "typescript": ("ts", "tsweb", "nuxt", "nest", "nextjs", "express"),
                   "php": ("php", "laravel"), "dart": ("dart", "flutter"), "rust": ("rust", "native", "scip"),
                   "c_cpp": ("cfamily", "native", "scip"), "kotlin": ("kotlin", "scip"), "swift": ("swift",)}


def _run(tool: str | None, args=("--version",)) -> tuple[str | None, bool]:
    """First output line of `tool args` and whether it exited 0 (a rustup proxy without its component prints an
    error and exits non-zero)."""
    if not tool:
        return None, False
    try:
        r = subprocess.run([tool, *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    out = (r.stdout or r.stderr).strip().splitlines()
    return (out[0].strip()[:120] if out else "?"), r.returncode == 0


def _version(tool: str | None, args=("--version",)) -> str | None:
    return _run(tool, args)[0]


def _describe(e: BaseException) -> str:
    """`SyntaxError: <msg> (codegraph/plugins/x/plugin.py:12)`: the error and where in the package it is."""
    pkg = Path(__file__).resolve().parent
    where = None
    if isinstance(e, SyntaxError) and e.filename:
        where = (e.filename, e.lineno)
    else:
        tb = e.__traceback__
        while tb is not None:
            if str(Path(tb.tb_frame.f_code.co_filename).resolve()).startswith(str(pkg)):
                where = (tb.tb_frame.f_code.co_filename, tb.tb_lineno)
            tb = tb.tb_next
    msg = e.msg if isinstance(e, SyntaxError) else str(e)
    if where:
        try:
            rel = Path(where[0]).resolve().relative_to(pkg.parent).as_posix()
        except ValueError:
            rel = where[0]
        return f"{type(e).__name__}: {msg} ({rel}:{where[1]})"
    return f"{type(e).__name__}: {msg}"


def module_imports() -> dict:
    """Import every module of the package. `cg index` imports every language plugin, so one module that fails to
    import on this interpreter makes indexing fail for every language. Returns the module count and the failures
    (module -> error)."""
    import importlib
    import pkgutil

    import codegraph
    failed: dict[str, str] = {}

    def onerror(name):
        e = sys.exc_info()[1]
        if e is not None and name not in failed:
            failed[name] = _describe(e)

    n = 0
    for m in pkgutil.walk_packages(codegraph.__path__, "codegraph.", onerror=onerror):
        n += 1
        try:
            importlib.import_module(m.name)
        except Exception as e:  # noqa: BLE001  (SyntaxError included: a module this Python cannot compile)
            failed.setdefault(m.name, _describe(e))
    return {"modules": n, "failed": failed}


def _tools() -> dict:
    from .plugins.dart.plugin import find_dart
    from .plugins.kotlin.exact import find_java
    from .plugins.native.runner import find_tool
    from .plugins.swift.exact import find_swift
    home = Path.home()
    found = {
        "node": shutil.which(extractors._override() or "node"),
        "npm": shutil.which("npm"), "bun": shutil.which("bun"), "php": shutil.which("php"),
        "composer": shutil.which("composer"), "dart": find_dart(),
        "rust-analyzer": find_tool("CODEGRAPH_RUST_ANALYZER", ["rust-analyzer"], [home / ".cargo" / "bin"]),
        "cargo": os.environ.get("CODEGRAPH_CARGO") or shutil.which("cargo") or
                 (str(home / ".cargo/bin/cargo") if (home / ".cargo/bin/cargo").exists() else None),
        "scip-clang": find_tool("CODEGRAPH_SCIP_CLANG", ["scip-clang"], [home / ".local" / "bin"]),
        "scip-java": find_tool("CODEGRAPH_SCIP_JAVA", ["scip-java"], [home / "tools", home / ".local" / "bin",
                                                                       home / ".local" / "share" / "coursier" / "bin"]),
        "java": find_java(), "swift": find_swift(), "git": shutil.which("git"),
    }
    vargs = {"java": ("-version",), "dart": ("--version",)}
    out = {}
    for k, v in found.items():
        ver, ok = _run(v, vargs.get(k, ("--version",)))
        out[k] = {"path": v, "version": ver, **({"runs": False} if v and not ok else {})}
    return out


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _pip(*mods: str) -> str:
    return f"{Path(sys.executable).name} -m pip install " + " ".join(PIP_NAMES[m] for m in mods) + \
        "  (or reinstall cg: the package depends on it)"


def _languages(tools: dict, root: Path | None, cfg: dict) -> list[dict]:
    has = lambda t: bool(tools[t]["path"])
    out = []

    def add(lang, mode, why, fix=None, **extra):
        out.append({"language": lang, "mode": mode, "why": why, **({"fix": fix} if fix else {}), **extra})

    add("python", "exact", "built-in parser (Python ast)")
    st = extractors.status("typescript")
    rt = extractors.js_runtime()
    if rt is None:
        add("typescript", "unavailable", extractors.js_runtime_problem(),
            "install Node.js 20+ or Bun (or set CODEGRAPH_NODE), then `cg setup typescript`")
    elif not st["installed"] and not has("npm") and not has("bun"):
        add("typescript", "unavailable", "extractor dependencies missing and npm not installed",
            "install npm, then `cg setup typescript`")
    else:
        add("typescript", "exact", f"TypeScript compiler API (runtime: {rt['kind']} {rt['path']})" + (
            "" if st["installed"] else "; its npm dependencies install on the first index (or now: `cg setup typescript`)"),
            runtime={"kind": rt["kind"], "path": rt["path"], "source": rt["source"]})
    st = extractors.status("php")
    if not has("php"):
        add("php", "unavailable", "php not installed (PHP 8.2+ runs the PHP extractor)", "install PHP 8.2+ and Composer, then `cg setup php`")
    elif not st["installed"] and not has("composer"):
        add("php", "unavailable", "extractor dependencies missing and Composer not installed", "install Composer (https://getcomposer.org), then `cg setup php`")
    else:
        add("php", "exact", "nikic/php-parser" + ("" if st["installed"] else "; Composer dependencies install on the first index (or `cg setup php`)"))
    st = extractors.status("dart")
    if not has("dart"):
        add("dart", "unavailable", "Dart SDK not found (`dart` on PATH or $DART)", "install the Dart SDK 3.x (https://dart.dev/get-dart), then `cg setup dart`")
    else:
        add("dart", "exact", "Dart analyzer" + ("" if st["installed"] else "; `dart pub get` + compile run on the first index (or `cg setup dart`)"))
    ts_ok = _module("tree_sitter")
    # Rust
    if not ts_ok or not _module("tree_sitter_rust"):
        add("rust", "unavailable", "tree-sitter grammar missing", _pip("tree_sitter", "tree_sitter_rust"))
    elif os.environ.get("CODEGRAPH_RUST_SCIP", "1") == "0":
        add("rust", "heuristic", "CODEGRAPH_RUST_SCIP=0")
    elif not has("rust-analyzer") or not has("cargo"):
        miss = " and ".join(t for t in ("rust-analyzer", "cargo") if not has(t))
        add("rust", "heuristic", f"{miss} not found: tree-sitter layer with name-based resolution",
            "rustup component add rust-analyzer  (or install.sh --with rust)")
    elif tools["rust-analyzer"].get("runs") is False:
        add("rust", "heuristic", f"rust-analyzer at {tools['rust-analyzer']['path']} does not run "
            f"({tools['rust-analyzer']['version'] or 'no output'}): tree-sitter layer with name-based resolution",
            "rustup component add rust-analyzer  (an empty rustup proxy is on PATH)")
    else:
        from .plugins.rust.plugin import rust_targets_setting
        class _P:      # rust_targets_setting reads project.options["config"]
            options = {"config": cfg}
        val, src = rust_targets_setting(_P)
        off = val in ("0", "", "off", "none")
        note = ("per-target runs off" if off else
                f"plus one run per other target the cfg conditions name ({val}, from {src}); a cold index takes about "
                "twice as long, later runs use rust-analyzer's cache. Turn off with `rust: {targets: off}` in .cg.yaml "
                "or CODEGRAPH_RUST_TARGETS=0")
        add("rust", "exact", f"rust-analyzer ({tools['rust-analyzer']['version']}); {note}", targets=val, targets_source=src)
    # C / C++
    if not ts_ok or not _module("tree_sitter_c") or not _module("tree_sitter_cpp"):
        add("c_cpp", "unavailable", "tree-sitter grammars missing", _pip("tree_sitter", "tree_sitter_c", "tree_sitter_cpp"))
    elif not has("scip-clang"):
        add("c_cpp", "heuristic", "scip-clang not found: tree-sitter layer with name-based resolution",
            "install.sh --with c  (scip-clang release binary into ~/.local/bin; docs/native.md)")
    else:
        compdb = None
        if root is not None:
            from .plugins.cfamily.plugin import find_compdb
            compdb = find_compdb(root)
        if root is not None and compdb is None:
            add("c_cpp", "heuristic", "scip-clang found, but no compile_commands.json in the project",
                "generate one (`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`, `bear -- make`) or set CODEGRAPH_COMPDB")
        else:
            add("c_cpp", "exact", "scip-clang" + (f" with {compdb}" if compdb else " (needs a compile_commands.json in the project)"))
    # Kotlin
    if not ts_ok or not _module("tree_sitter_kotlin"):
        add("kotlin", "unavailable", "tree-sitter grammar missing", _pip("tree_sitter", "tree_sitter_kotlin"))
    elif os.environ.get("CODEGRAPH_KOTLIN_SCIP_FILE"):
        add("kotlin", "exact", "CODEGRAPH_KOTLIN_SCIP_FILE")
    elif not has("scip-java") or not has("java"):
        miss = " and ".join(t for t in ("scip-java", "java") if not has(t))
        add("kotlin", "heuristic", f"{miss} not found: tree-sitter layer with name-based resolution",
            "install.sh --with kotlin  (JDK 17+ and scip-java via coursier), then CODEGRAPH_KOTLIN_SCIP=1")
    elif os.environ.get("CODEGRAPH_KOTLIN_SCIP") != "1":
        add("kotlin", "heuristic", "scip-java found but not run: it runs the Gradle / Maven build (the project's build scripts)",
            "set CODEGRAPH_KOTLIN_SCIP=1 (or pass --scip index.scip)")
    else:
        add("kotlin", "exact", "scip-java on the Gradle / Maven build")
    # Swift
    if not ts_ok or not _module("tree_sitter_swift"):
        add("swift", "unavailable", "tree-sitter grammar missing", _pip("tree_sitter", "tree_sitter_swift"))
    elif os.environ.get("CODEGRAPH_SWIFT_INDEX_STORE"):
        add("swift", "exact", "CODEGRAPH_SWIFT_INDEX_STORE")
    elif not has("swift"):
        add("swift", "heuristic", "no Swift toolchain: tree-sitter layer with name-based resolution",
            "install.sh --with swift  (prints the swift.org toolchain install), then CODEGRAPH_SWIFT_INDEX=1")
    elif os.environ.get("CODEGRAPH_SWIFT_INDEX") != "1":
        add("swift", "heuristic", "Swift toolchain found but not run: the index store needs a `swift build`",
            "set CODEGRAPH_SWIFT_INDEX=1 (SwiftPM) or CODEGRAPH_SWIFT_INDEX_STORE to an existing index store")
    else:
        add("swift", "exact", "swift build --enable-index-store")
    return out


def scip_health(path: str | Path) -> dict:
    """What cg can read from a SCIP index: documents, occurrences with a usable position, definitions, and the
    warning `cg coverage` would show."""
    from .plugins.native import scipread
    p = Path(path)
    out = {"path": str(p)}
    try:
        idx = scipread.load(p)
    except Exception as e:  # noqa: BLE001
        out["warning"] = f"SCIP index {p.name}: cannot be read ({type(e).__name__}: {str(e)[:200]})"
        return out
    ndefs = sum(len(v) for v in idx.defs.values())
    out.update(tool=f"{idx.tool} {idx.version}".strip(), documents=len(idx.docs), occurrences=idx.occurrences,
               positioned=idx.positioned, definitions=ndefs)
    w = scipread.health_warning(p.name, idx.occurrences, idx.positioned, ndefs)
    if w:
        out["warning"] = w
    return out


def _mark_broken(langs: list[dict], imports: dict) -> None:
    """A language whose plugin (or the indexer itself) does not import cannot be indexed, whatever its tools."""
    failed = imports["failed"]
    if not failed:
        return
    index_err = failed.get("codegraph.indexer")
    for x in langs:
        own = [m for m in failed for p in PLUGIN_PACKAGES.get(x["language"], ())
               if m == f"codegraph.plugins.{p}" or m.startswith(f"codegraph.plugins.{p}.")]
        if not own and not index_err:
            continue
        x["mode"] = "broken"
        x["why"] = (f"{own[0]} does not import: {failed[own[0]]}" if own else
                    f"cg index cannot load (codegraph.indexer: {index_err})")
        x["fix"] = "see `cg modules` above"


UPGRADE_HINT = ("upgrade cg (`uv tool upgrade cg-code-graph` / `install.sh --update`); if it persists, report it with the "
                "`cg doctor` output, and meanwhile reinstall cg under a newer Python (`uv tool install --python 3.12 ...`)")


SCIP_JAVA_RANGES = {12: "0.12: Kotlin <= 2.1", 13: "0.13: Kotlin 2.2.0 - 2.2.10"}


def project_checks(root: Path, present: set) -> list[dict]:
    """`cg doctor <root>`: what the project gives the exact layers and the TypeScript program (#65)."""
    out = []

    def add(lang, ok, what, fix=None):
        out.append({"language": lang, "ok": ok, "what": what, **({"fix": fix} if fix else {})})
    if "typescript" in present:
        from .plugins.ts.plugin import sub_tsconfigs
        cfgs = [c for c in ("tsconfig.json", "jsconfig.json") if (root / c).is_file()]
        subs = sub_tsconfigs(root) if not cfgs else []
        if cfgs:
            add("typescript", True, f"{cfgs[0]} at the root: one program from it")
        elif subs:
            add("typescript", True, f"no root tsconfig; {len(subs)} package tsconfig(s) indexed as one program: "
                + ", ".join(subs[:5]) + (" …" if len(subs) > 5 else ""))
        else:
            add("typescript", False, "no tsconfig.json / jsconfig.json: plain-JS defaults over src/ and app/",
                "add a tsconfig.json (or a .cg.yaml `include:`) when the code lives elsewhere")
    if "kotlin" in present:
        from .plugins.kotlin.exact import BUILD_FILES, android_modules, kotlin_version
        builds = [f for f in BUILD_FILES if (root / f).is_file()]
        if not builds:
            add("kotlin", False, "no Gradle / Maven build file at the root: scip-java cannot run (heuristic mode)",
                "run cg on the directory holding settings.gradle(.kts) / build.gradle(.kts) / pom.xml")
        else:
            kv = kotlin_version(root)
            andr = android_modules(root)
            add("kotlin", True, f"{builds[0]} at the root" + (f"; Kotlin {'.'.join(map(str, kv))}" if kv else
                "; Kotlin version not declared in the root build files")
                + (f"; Android modules (no scip-java variant, heuristic there): {', '.join(andr[:4])}" if andr else ""))
            from .plugins.kotlin.exact import _generation, _supports, scip_java_candidates
            tools = scip_java_candidates()
            if tools:                    # #67: each scip-java release's Kotlin plugin loads into a narrow range
                gens = [(t, _generation(t)) for t in tools]
                desc = "; ".join(f"{t} ({SCIP_JAVA_RANGES.get(g, '?')})" for t, g in gens)
                fits = [t for t, g in gens if _supports(g, kv)]
                kvs = ".".join(map(str, kv)) if kv else "undeclared"
                if fits:
                    add("kotlin", True, f"scip-java for Kotlin {kvs}: {fits[0]}  (installed: {desc})")
                else:
                    add("kotlin", False, f"no installed scip-java loads into Kotlin {kvs} (installed: {desc})",
                        "Kotlin 2.2.20+ has no released scip-java yet (docs/kotlin.md#exact-mode); "
                        "pass --scip index.scip from another indexer" if kv and kv >= (2, 2, 20) else
                        "install the scip-java release for this Kotlin version next to the other one "
                        "(scip-java-<version>/scip-java in ~/tools), docs/kotlin.md#exact-mode")
    if "swift" in present:
        from .xcode import _manifests, _projects
        pk, xp = _manifests(root), _projects(root)
        if (root / "Package.swift").is_file():
            add("swift", True, "Package.swift at the root: `swift build` can produce the index store (CODEGRAPH_SWIFT_INDEX=1)"
                + (f"; also {len(xp)} Xcode project(s)" if xp else ""))
        elif xp or pk:
            where = ", ".join([p.parent.parent.relative_to(root).as_posix() or "." for p in xp][:3]
                              + [p.parent.relative_to(root).as_posix() for p in pk][:3])
            add("swift", False, f"no Package.swift at the root ({len(pk)} manifest(s), {len(xp)} Xcode project(s): {where}): "
                "the exact layer builds a root package only and runs no Xcode build (heuristic mode)",
                "point CODEGRAPH_SWIFT_INDEX_STORE at the index store of an Xcode build (DerivedData/<app>/Index.noindex/DataStore)")
        else:
            add("swift", False, "no Package.swift or .xcodeproj found (heuristic mode; platforms from file conditions only)")
    return out


def report(root: str | Path | None = None, scip: list | None = None) -> dict:
    rootp = Path(root).resolve() if root else None
    cfg, cfg_error, present = {}, None, None
    if rootp is not None:
        from .config import ConfigError, load
        from .coverage import SUPPORTED, scan
        try:
            cfg = load(rootp)
        except ConfigError as e:
            cfg_error = str(e)
        counts = scan(rootp)
        present = {lang for lang, exts in SUPPORTED.items() if any(counts.get(e) for e in exts)}
    tools = _tools()
    langs = _languages(tools, rootp, cfg)
    imports = module_imports()
    _mark_broken(langs, imports)
    if present is not None:
        langs = [x for x in langs if x["language"] in present]
    pkg = Path(__file__).resolve().parent
    return {
        "cg": __version__, "python": platform.python_version(), "python_executable": sys.executable,
        "package": str(pkg), "checkout": (pkg.parent / ".git").exists(),
        "os": f"{platform.system()} {platform.machine()}",
        "extractors": {k: extractors.status(k) for k in extractors.SPECS}, "cache": str(extractors.cache_root()),
        "cache_usage": cache.usage(),
        "tools": tools, "python_modules": {m: _module(m) for m in (*PIP_NAMES, "yaml", "mcp")},
        "modules": imports, "root": str(rootp) if rootp else None, "config_error": cfg_error, "languages": langs,
        **({"project": project_checks(rootp, present)} if rootp is not None else {}),
        "update": "uv tool upgrade cg-code-graph  |  pipx upgrade cg-code-graph  |  install.sh --update",
        **({"scip": [scip_health(x) for x in scip]} if scip else {}),
    }


def render(r: dict) -> str:
    out = [f"cg {r['cg']}  (Python {r['python']}, {r['os']}; {r['package']}{', checkout' if r['checkout'] else ''})"]
    out.append("tools:")
    for k, v in r["tools"].items():
        out.append(f"  {k:<14} {v['version'] or 'not found'}" + (f"  ({v['path']})" if v["path"] else "")
                   + ("  [does not run]" if v.get("runs") is False else ""))
    out.append(f"extractor dependencies (cache {r['cache']}):")
    for k, v in r["extractors"].items():
        out.append(f"  {k:<14} {'installed' if v['installed'] else 'not installed'}  ({v['where']}: {v['dir']})")
    cu = r.get("cache_usage")
    if cu:
        kinds = ", ".join(f"{k} {cache.human(v['bytes'])}" for k, v in cu["kinds"].items())
        out.append(f"cache: {cache.human(cu['bytes'])} in {cu['root']}" + (f" ({kinds})" if kinds else "")
                   + (f"; {cache.human(cu['stale_bytes'])} stale (`cg clean --stale`)" if cu["stale_bytes"] else ""))
    mods = r["python_modules"]
    missing = sorted(m for m, ok in mods.items() if not ok)
    out.append("python modules: " + ("all present" if not missing else "missing " + ", ".join(missing)))
    imp = r.get("modules") or {"modules": 0, "failed": {}}
    if not imp["failed"]:
        out.append(f"cg modules: all {imp['modules']} import on Python {r['python']}")
    else:
        out.append(f"cg modules: {len(imp['failed'])} of {imp['modules']} do not import on Python {r['python']}; "
                   "`cg index` fails for every language that loads them:")
        for m, err in sorted(imp["failed"].items()):
            out.append(f"  {m}: {err}")
        out.append(f"  fix: {UPGRADE_HINT}")
    if r.get("config_error"):
        out.append(f"config: {r['config_error']}")
    out.append("languages" + (f" in {r['root']}:" if r["root"] else ":"))
    if not r["languages"]:
        out.append("  (no source files of a supported language found)")
    for x in r["languages"]:
        out.append(f"  {x['language']:<11} {x['mode']:<11} {x['why']}")
        if x.get("fix"):
            out.append(f"  {'':<11} {'':<11} fix: {x['fix']}")
    if r.get("project"):
        out.append("project:")
        for x in r["project"]:
            out.append(f"  {x['language']:<11} {'ok' if x['ok'] else 'check':<11} {x['what']}")
            if x.get("fix"):
                out.append(f"  {'':<11} {'':<11} fix: {x['fix']}")
    for x in r.get("scip") or ():
        if "documents" in x:
            out.append(f"scip {x['path']}: {x['tool']}, {x['documents']} documents, {x['occurrences']} occurrences "
                       f"({x['positioned']} with a usable position), {x['definitions']} definitions")
        if x.get("warning"):
            out.append(f"  warning: {x['warning']}")
    out.append(f"update: {r['update']}  ({INSTALL_DOC})")
    return "\n".join(out)


def setup(langs: list[str] | None = None, quiet: bool = False) -> int:
    """`cg setup [typescript php dart]`: install the extractor dependencies for the languages whose toolchain is
    present (all three by default); returns 1 if an explicitly named one failed."""
    from .plugins.dart.plugin import DartPlugin, find_dart
    want = langs or list(extractors.SPECS)
    rc = 0
    for lang in want:
        tool = {"typescript": "npm", "php": "composer", "dart": None}[lang]
        need = {"typescript": "node", "php": "php", "dart": None}[lang]
        dart = find_dart() if lang == "dart" else None
        if lang == "typescript":
            rt = extractors.js_runtime()
            can_install = extractors.status(lang)["installed"] or bool(shutil.which("npm") or shutil.which("bun"))
            skip = rt is None or not can_install
            detail = extractors.js_runtime_problem() if rt is None else "npm or bun not installed"
        else:
            skip = (need and not shutil.which(need)) or (tool and not shutil.which(tool) and not extractors.status(lang)["installed"]) \
                or (lang == "dart" and not dart)
            detail = f"{'dart' if lang == 'dart' else need if need and not shutil.which(need) else tool} not installed"
        if skip:
            msg = f"{lang}: skipped ({detail})"
            print(msg, file=sys.stderr)
            rc = rc or (1 if langs else 0)
            continue
        try:
            if lang == "dart":
                d = Path(DartPlugin().ensure_extractor(dart)).parent.parent
            else:
                d = extractors.ensure(lang)
            if not quiet:
                print(f"{lang}: ready ({d})")
        except (RuntimeError, OSError) as e:
            print(f"{lang}: failed: {e}", file=sys.stderr)
            rc = 1
    return rc

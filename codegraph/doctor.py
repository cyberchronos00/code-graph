"""`cg doctor`: what this installation can index, and how.

Versions of cg and the tools it uses, whether the Node / PHP / Dart extractor dependencies are installed (and where:
codegraph/core/extractors.py), and per language whether `cg index` runs in exact or heuristic mode, why, and the
command that installs what is missing. With a project root it also checks project-level conditions (a
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
from .core import extractors

INSTALL_DOC = "docs/install.md"
REPO = "https://github.com/cyberchronos00/code-graph"
GRAMMARS = {"rust": "tree_sitter_rust", "c_cpp": "tree_sitter_c", "kotlin": "tree_sitter_kotlin", "swift": "tree_sitter_swift"}
PIP_NAMES = {"tree_sitter": "tree-sitter", "tree_sitter_rust": "tree-sitter-rust", "tree_sitter_c": "tree-sitter-c",
             "tree_sitter_cpp": "tree-sitter-cpp", "tree_sitter_kotlin": "tree-sitter-kotlin", "tree_sitter_swift": "tree-sitter-swift"}


def _version(tool: str | None, args=("--version",)) -> str | None:
    if not tool:
        return None
    try:
        r = subprocess.run([tool, *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or r.stderr).strip().splitlines()
    return out[0].strip()[:120] if out else "?"


def _tools() -> dict:
    from .plugins.dart.plugin import find_dart
    from .plugins.kotlin.exact import find_java
    from .plugins.native.runner import find_tool
    from .plugins.swift.exact import find_swift
    home = Path.home()
    found = {
        "node": shutil.which("node"), "npm": shutil.which("npm"), "php": shutil.which("php"),
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
    return {k: {"path": v, "version": _version(v, vargs.get(k, ("--version",)))} for k, v in found.items()}


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
    if not has("node"):
        add("typescript", "unavailable", "node not installed (Node.js 20+ runs the TypeScript extractor)",
            "install Node.js 20+ (https://nodejs.org or your package manager), then `cg setup typescript`")
    elif not st["installed"] and not has("npm"):
        add("typescript", "unavailable", "extractor dependencies missing and npm not installed", "install npm, then `cg setup typescript`")
    else:
        add("typescript", "exact", "TypeScript compiler API" + ("" if st["installed"] else
            "; its npm dependencies install on the first index (or now: `cg setup typescript`)"))
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


def report(root: str | Path | None = None) -> dict:
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
    if present is not None:
        langs = [x for x in langs if x["language"] in present]
    pkg = Path(__file__).resolve().parent
    return {
        "cg": __version__, "python": platform.python_version(), "python_executable": sys.executable,
        "package": str(pkg), "checkout": (pkg.parent / ".git").exists(),
        "os": f"{platform.system()} {platform.machine()}",
        "extractors": {k: extractors.status(k) for k in extractors.SPECS}, "cache": str(extractors.cache_root()),
        "tools": tools, "python_modules": {m: _module(m) for m in (*PIP_NAMES, "yaml", "mcp")},
        "root": str(rootp) if rootp else None, "config_error": cfg_error, "languages": langs,
        "update": "pipx upgrade codegraph  |  uv tool upgrade codegraph  |  install.sh --update",
    }


def render(r: dict) -> str:
    out = [f"cg {r['cg']}  (Python {r['python']}, {r['os']}; {r['package']}{', checkout' if r['checkout'] else ''})"]
    out.append("tools:")
    for k, v in r["tools"].items():
        out.append(f"  {k:<14} {v['version'] or 'not found'}" + (f"  ({v['path']})" if v["path"] else ""))
    out.append(f"extractor dependencies (cache {r['cache']}):")
    for k, v in r["extractors"].items():
        out.append(f"  {k:<14} {'installed' if v['installed'] else 'not installed'}  ({v['where']}: {v['dir']})")
    mods = r["python_modules"]
    missing = sorted(m for m, ok in mods.items() if not ok)
    out.append("python modules: " + ("all present" if not missing else "missing " + ", ".join(missing)))
    if r.get("config_error"):
        out.append(f"config: {r['config_error']}")
    out.append("languages" + (f" in {r['root']}:" if r["root"] else ":"))
    if not r["languages"]:
        out.append("  (no source files of a supported language found)")
    for x in r["languages"]:
        out.append(f"  {x['language']:<11} {x['mode']:<11} {x['why']}")
        if x.get("fix"):
            out.append(f"  {'':<11} {'':<11} fix: {x['fix']}")
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
        if (need and not shutil.which(need)) or (tool and not shutil.which(tool) and not extractors.status(lang)["installed"]) \
                or (lang == "dart" and not dart):
            msg = f"{lang}: skipped ({'dart' if lang == 'dart' else need if need and not shutil.which(need) else tool} not installed)"
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

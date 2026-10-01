"""Language coverage of an index: which source files cg analysed, how (exact / heuristic), and which it could not.

Recorded at index time in the DB meta (stats.coverage) and shown by `cg coverage`, the MCP `coverage` tool and the
notes on empty MCP replies, so an agent knows when an empty answer is not proof of absence and it must fall back to
its normal search and file reading."""
from __future__ import annotations

import os
from collections import Counter

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
}
# languages without a native plugin (go / java can be imported from a SCIP index)
UNSUPPORTED = {
    ".go": "go", ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".swift": "swift", ".rb": "ruby", ".cs": "csharp",
    ".scala": "scala", ".ex": "elixir", ".exs": "elixir", ".m": "objective-c", ".mm": "objective-c", ".lua": "lua",
    ".pl": "perl", ".pm": "perl", ".clj": "clojure", ".erl": "erlang", ".hs": "haskell", ".fs": "fsharp", ".groovy": "groovy",
    ".r": "r", ".jl": "julia", ".zig": "zig", ".sol": "solidity",
}
HINTS = {
    "php": "install PHP 8.2+ and run `(cd codegraph/plugins/php/extractor && composer install)`",
    "typescript": "install Node.js 20+ and run `(cd codegraph/plugins/ts/extractor && npm ci)`",
    "dart": "install the Dart SDK 3.x (`dart` on PATH or $DART)",
    "rust": "exact mode needs rust-analyzer (`rustup component add rust-analyzer`); the tree-sitter layer needs "
            "`pip install tree-sitter tree-sitter-rust`",
    "c_cpp": "exact mode needs scip-clang and a compile_commands.json (docs/native.md); the tree-sitter layer needs "
             "`pip install tree-sitter tree-sitter-c tree-sitter-cpp`",
    "python": "add a project marker (pyproject.toml, requirements.txt, setup.py or manage.py) at the indexed root",
    "go": "no native plugin: index with scip-go and pass `--scip index.scip`",
    "java": "no native plugin: index with scip-java and pass `--scip index.scip`",
}
SKIP_DIRS = {".git", "node_modules", "vendor", "target", "build", "dist", ".dart_tool", "__pycache__", ".venv", "venv",
             ".tox", ".mypy_cache", ".pytest_cache", ".next", ".output", "out", ".gradle", ".idea", "Pods"}
FALLBACK = "use your normal search and file reading for those parts; an empty cg answer there is not proof of absence"


def scan(root: str | Path) -> Counter:
    """Source files by extension under root (generated / dependency directories skipped)."""
    c: Counter = Counter()
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith("._")]
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            if ext and not fn.startswith("._") and is_real_file(os.path.join(dp, fn)):
                c[ext] += 1
    return c


def _status(lang: str, st: dict | None) -> tuple[str, str | None]:
    if st is None:
        return "not_indexed", None
    s = st.get("status")
    if s in ("skipped", "error", "stub"):
        return "skipped", st.get("reason")
    mode = st.get("mode")
    if lang in ("rust", "c_cpp") and mode and mode != "scip":
        return "heuristic", None
    if lang == "typescript" and st.get("program_files") == 0 and not st.get("nodes"):
        return "not_indexed", "the TypeScript plugin ran but found no source files (tsconfig include / source dirs)"
    return "exact", None


def compute(root: str | Path, plugins: dict, scip_imported: bool = False) -> dict:
    """Coverage entry list from the file scan and the per-plugin index stats."""
    files = scan(root)
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
        langs.append(e)
    other: dict = {}
    for ext, lang in UNSUPPORTED.items():
        if files[ext]:
            o = other.setdefault(lang, {"language": lang, "files": 0, "status": "unsupported", "by_ext": {}})
            o["files"] += files[ext]
            o["by_ext"][ext] = files[ext]
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
    return {"languages": langs, "gaps": sum(1 for e in langs if e["status"] not in ("exact", "scip"))}


def gaps(cov: dict | None) -> list[dict]:
    return [e for e in (cov or {}).get("languages", []) if e["status"] not in ("exact", "scip") and e["files"]]


def summary_line(cov: dict | None, repo: str | None = None) -> str:
    """One line: 'coverage: php 18 exact; typescript 9 exact | gaps: go 3 unsupported (...)'."""
    if not cov:
        return "coverage: not recorded for this index (re-index with this version of cg)"
    pre = f"coverage{' ' + repo if repo else ''}: "
    ok = [f"{e['language']} {e['files']} {e['status']}" for e in cov["languages"] if e["status"] in ("exact", "scip")]
    bad = [f"{e['language']} {e['files']} {e['status'].replace('_', ' ')}" for e in gaps(cov)]
    s = pre + ("; ".join(ok) or "no supported source files")
    if bad:
        s += " | not fully covered: " + "; ".join(bad)
    return s


def render(covs: dict[str, dict | None]) -> str:
    """Multi-line report for one or more repos (name -> coverage)."""
    out = []
    any_gap = False
    for repo, cov in covs.items():
        out.append(summary_line(cov, repo if len(covs) > 1 or repo else None))
        for e in gaps(cov):
            any_gap = True
            exts = ", ".join(f"{k} {v}" for k, v in sorted(e["by_ext"].items()))
            line = f"  {e['language']}: {e['files']} files ({exts}) {e['status'].replace('_', ' ')}"
            if e.get("reason"):
                line += f": {e['reason']}"
            out.append(line)
            if e.get("hint"):
                out.append(f"    fix: {e['hint']}")
    if any_gap:
        out.append("not covered or heuristic only: " + FALLBACK + ".")
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
            bad.append(f"{e['language']} ({e['files']} files, {e['status'].replace('_', ' ')}{', ' + r if len(covs) > 1 else ''})")
    if bad:
        return "coverage: not fully covered here: " + "; ".join(bad) + ". If the code you mean is there, " + FALLBACK + "."
    if unknown:
        return "coverage: not recorded for this index; if in doubt, " + FALLBACK + "."
    langs = sorted({e["language"] for c in covs.values() for e in (c or {}).get("languages", []) if e["files"]})
    return f"coverage: every source file cg found is indexed ({', '.join(langs)}); code outside these languages or generated at runtime is not in the graph."

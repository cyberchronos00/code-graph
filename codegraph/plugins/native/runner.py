"""Running external SCIP indexers (rust-analyzer, scip-clang) with an on-disk cache.

The cache key covers the cache version, the indexer version, its arguments and (path, size, content hash) of every
relevant source file, so re-indexing an unchanged project reuses the previous SCIP file and any content change (even
one that keeps the size and mtime) re-runs the indexer. CODEGRAPH_NO_CACHE=1 disables the cache.
Output never goes into the indexed project: files live under ~/.cache/codegraph/scip/ (or $CODEGRAPH_CACHE/scip/).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from ...core import fsutil


def cache_dir() -> Path:
    d = Path(os.environ.get("CODEGRAPH_CACHE") or os.environ.get("CODEGRAPH_CACHE_DIR") or Path.home() / ".cache" / "codegraph") / "scip"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_tool(env: str, names: list[str], extra_dirs: list[Path] = ()) -> str | None:
    v = os.environ.get(env)
    if v:
        return v if (Path(v).exists() or shutil.which(v)) else None
    for n in names:
        p = shutil.which(n)
        if p:
            return p
        for d in extra_dirs:
            if (Path(d) / n).exists():
                return str(Path(d) / n)
    return None


def tool_version(tool: str, args=("--version",)) -> str:
    try:
        r = subprocess.run([tool, *args], capture_output=True, text=True, timeout=60)
        return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else "?"
    except Exception:
        return "?"


def fingerprint(root: Path, files: list[str], extra: str) -> str:
    """Cache key: cache version + `extra` (indexer version, config) + root + (path, size, content hash) per file."""
    h = hashlib.sha256(f"cg-cache-v{fsutil.CACHE_VERSION}\0{extra}".encode())
    h.update(str(root).encode())
    for f in sorted(files):
        h.update(f"{f}\0{fsutil.content_key(root / f)}\n".encode())
    return h.hexdigest()[:20]


def _prune_legacy(name: str) -> None:
    """Drop SCIP files written under an older cache version (`<name>-<20 hex>.scip`): their keys can never match again."""
    for old in cache_dir().glob(f"{name}-*.scip"):
        if re.fullmatch(rf"{re.escape(name)}-[0-9a-f]{{20}}\.scip", old.name):
            old.unlink(missing_ok=True)


def run_cached(name: str, key: str, cmd: list[str], cwd: Path, out_path_arg: str | None, timeout: int,
               out_flag_style: str = "space") -> tuple[Path | None, dict]:
    """Run `cmd` (which writes a SCIP file) unless a cached result exists. The output path is appended to `cmd`
    as `<out_path_arg> <path>` (style 'space') or `<out_path_arg>=<path>` (style 'eq')."""
    _prune_legacy(name)
    out = cache_dir() / f"{name}-v{fsutil.CACHE_VERSION}-{key}.scip"
    info = {"cache": "hit" if out.exists() and os.environ.get("CODEGRAPH_NO_CACHE") != "1" else "miss", "command": " ".join(cmd)}
    if info["cache"] == "hit":
        return out, info
    tmp = out.with_suffix(".tmp.scip")
    full = list(cmd)
    if out_path_arg:
        full += [f"{out_path_arg}={tmp}"] if out_flag_style == "eq" else [out_path_arg, str(tmp)]
    t0 = time.time()
    try:
        r = subprocess.run(full, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        info["error"] = f"timeout after {timeout}s"
        return None, info
    except OSError as e:
        info["error"] = str(e)
        return None, info
    info["seconds"] = round(time.time() - t0, 2)
    tail = (r.stderr or "").strip().splitlines()[-8:]
    info["stderr_tail"] = tail
    if r.returncode != 0 or not tmp.exists():
        info["error"] = f"exit {r.returncode}"
        return None, info
    os.replace(tmp, out)
    return out, info

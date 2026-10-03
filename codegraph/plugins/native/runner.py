"""Running external SCIP indexers (rust-analyzer, scip-clang) with an on-disk cache.

The cache key covers the cache version, the indexer version, its arguments and (path, size, content hash) of every
relevant source file, so re-indexing an unchanged project reuses the previous SCIP file and any content change (even
one that keeps the size and mtime) re-runs the indexer. CODEGRAPH_NO_CACHE=1 disables the cache.
Output never goes into the indexed project: files live under ~/.cache/codegraph/scip/ (or $CODEGRAPH_CACHE/scip/).
Concurrent processes indexing the same project share one indexer run (a per-key lock) and never see each other's
partial output (private temporary files, atomic os.replace).
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

try:
    import fcntl
except ImportError:      # Windows: no flock; runs stay safe through private temporary files, without the wait
    fcntl = None

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


@contextlib.contextmanager
def key_lock(name: str, key: str, timeout: float):
    """An exclusive per-key lock (`<name>-<key>.lock` in the cache dir), so concurrent processes indexing the same
    project run the indexer once: the others wait, then find the cached result. Yields the seconds spent waiting, or
    None when the lock could not be taken in `timeout` (or file locks are unavailable): the caller runs unlocked then."""
    if fcntl is None:
        yield None
        return
    path = cache_dir() / f"{name}-v{fsutil.CACHE_VERSION}-{key}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    t0, got = time.time(), False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if time.time() - t0 >= timeout:
                    break
                time.sleep(0.2)
        yield round(time.time() - t0, 2) if got else None
    finally:
        if got:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def write_atomic(path: Path, text: str) -> None:
    """Write a file other processes may read at the same time: a private temporary file, then os.replace."""
    if path.exists() and path.read_text() == text:
        return
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _prune_tmp(name: str, max_age: float = 86400) -> None:
    """Drop temporary outputs a killed run left behind (older than a day)."""
    now = time.time()
    for t in cache_dir().glob(f"{name}-*.tmp.scip"):
        try:
            if now - t.stat().st_mtime > max_age:
                t.unlink(missing_ok=True)
        except OSError:
            pass


def run_cached(name: str, key: str, cmd: list[str], cwd: Path, out_path_arg: str | None, timeout: int,
               out_flag_style: str = "space", env: dict | None = None,
               notes: str | None = None) -> tuple[Path | None, dict]:
    """Run `cmd` (which writes a SCIP file) unless a cached result exists. The output path is appended to `cmd`
    as `<out_path_arg> <path>` (style 'space') or `<out_path_arg>=<path>` (style 'eq'). `env` replaces the child's
    environment; output lines matching the `notes` regex are kept in info["notes"] (a build error far above the tail).
    Safe for concurrent processes: one run per key at a time (key_lock; the others wait and get a cache hit), each
    run writes its own temporary file and moves it into the cache atomically."""
    _prune_legacy(name)
    out = cache_dir() / f"{name}-v{fsutil.CACHE_VERSION}-{key}.scip"
    use_cache = os.environ.get("CODEGRAPH_NO_CACHE") != "1"
    info = {"cache": "hit" if out.exists() and use_cache else "miss", "command": " ".join(cmd)}
    if info["cache"] == "hit":
        return out, info
    with key_lock(name, key, timeout) as waited:
        if waited:
            info["lock_wait_seconds"] = waited
        if use_cache and out.exists():         # another process indexed the same project while this one waited
            info["cache"] = "hit"
            return out, info
        _prune_tmp(name)
        return _run(cmd, cwd, out, out_path_arg, out_flag_style, timeout, info, env, notes)


def _run(cmd, cwd, out: Path, out_path_arg, out_flag_style, timeout, info, env=None,
         notes=None) -> tuple[Path | None, dict]:
    tmp = out.with_name(f"{out.stem}.{os.getpid()}.{secrets.token_hex(4)}.tmp.scip")
    full = list(cmd)
    if out_path_arg:
        full += [f"{out_path_arg}={tmp}"] if out_flag_style == "eq" else [out_path_arg, str(tmp)]
    t0 = time.time()
    try:
        try:
            r = subprocess.run(full, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            info["error"] = f"timeout after {timeout}s"
            return None, info
        except OSError as e:
            info["error"] = str(e)
            return None, info
        info["seconds"] = round(time.time() - t0, 2)
        tail = (r.stderr or "").strip().splitlines()[-8:]
        info["stderr_tail"] = tail
        if notes:
            rx = re.compile(notes)
            hits = [x.strip()[:300] for x in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines() if rx.search(x)]
            if hits:
                info["notes"] = list(dict.fromkeys(hits))[:6]
        if r.returncode != 0 or not tmp.exists():
            info["error"] = f"exit {r.returncode}" + ("" if tmp.exists() else ", no SCIP output written")
            if r.returncode == 0 and out.exists():    # the cache file appeared meanwhile (unlocked fallback)
                info.pop("error")
                info["cache"] = "hit"
                return out, info
            return None, info
        os.replace(tmp, out)
        return out, info
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

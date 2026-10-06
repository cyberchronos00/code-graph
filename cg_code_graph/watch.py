"""`cg watch`: re-index a checkout when files change (issue #145, part 2).

Uses `watchfiles` when it is installed (`pip install "cg-code-graph[watch]"`), otherwise polls
`hooks._fingerprint` (git only). Both backends refresh only when that fingerprint changes, so
`.gitignore` applies, and both call `hooks.refresh` (the same lock as the git hooks).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import hooks


def _emit(out, msg: str) -> None:
    out(msg)


def _index_lock(root: Path) -> Path | None:
    proc = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", "index.lock"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _wait_index_lock(root: Path, stop) -> bool:
    """Wait while git holds `index.lock` (a checkout). False when `stop` is set."""
    deadline = time.monotonic() + 60.0
    while True:
        if stop is not None and stop.is_set():
            return False
        path = _index_lock(root)
        if path is None or not path.exists():
            return True
        if time.monotonic() >= deadline:
            return True
        if stop is not None:
            if stop.wait(0.2):
                return False
        else:
            time.sleep(0.2)


def _stopped(stop) -> bool:
    return stop is not None and stop.is_set()


def _sleep(seconds: float, stop) -> bool:
    """Sleep up to `seconds`. False when `stop` is set."""
    if seconds <= 0:
        return not _stopped(stop)
    if stop is not None:
        return not stop.wait(seconds)
    time.sleep(seconds)
    return True


def _refresh(root, db, name, quiet, out, stop) -> bool:
    if not _wait_index_lock(Path(root), stop):
        return False
    if _stopped(stop):
        return False
    hooks.refresh(root, db, name, quiet, out)
    return True


def _refresh_for(root, db, name, quiet, out, stop, stable, debounce: float):
    """Refresh for fingerprint `stable` and return (ok, last). When the tree moved while the index ran
    (an edit saved mid-run, which that run's snapshot missed, or files the run itself wrote), refresh
    once more and then accept whatever the tree looks like, so a run that writes into ROOT cannot loop."""
    rootp = Path(root)
    if not _refresh(root, db, name, quiet, out, stop):
        return False, stable
    if stable is None:
        return True, None
    post = hooks._fingerprint(rootp, db)
    if post is None or post == stable:
        return True, stable
    settled = _await_stable(rootp, db, post, debounce, stop)
    if settled is None or not _refresh(root, db, name, quiet, out, stop):
        return False, stable
    return True, hooks._fingerprint(rootp, db) or settled


def _await_stable(root: Path, db: str, first: str, debounce: float, stop):
    """Fingerprint once writes settle: two reads `debounce` apart that match, or 30s. None if stopped."""
    deadline = time.monotonic() + 30.0
    prev = first
    while True:
        if _stopped(stop):
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return prev
        if not _sleep(min(debounce, remaining), stop):
            return None
        fp = hooks._fingerprint(root, db)
        if fp is None:
            continue
        if fp == prev:
            return fp
        prev = fp


def _poll(root, db, name, quiet, out, stop, interval: float, debounce: float, last) -> None:
    rootp = Path(root)
    while not _stopped(stop):
        if not _sleep(interval, stop):
            return
        fp = hooks._fingerprint(rootp, db)
        if fp is None or fp == last:
            continue
        stable = _await_stable(rootp, db, fp, debounce, stop)
        if stable is None:
            return
        if stable == last:
            continue
        ok, last = _refresh_for(root, db, name, quiet, out, stop, stable, debounce)
        if not ok:
            return


def _watchfiles(root, db, name, quiet, out, stop, debounce: float, last) -> None:
    import inspect

    import watchfiles
    from watchfiles import DefaultFilter

    rootp = Path(root)
    db_abs = str(Path(db).resolve())
    ignore_dirs = tuple(DefaultFilter.ignore_dirs) + (
        "vendor", "build", "dist", "target", "out", ".next", ".nuxt", ".dart_tool")
    filt = DefaultFilter(ignore_dirs=ignore_dirs, ignore_paths=(db_abs,))
    kw = {"watch_filter": filt, "debounce": int(debounce * 1000), "stop_event": stop}
    if "raise_interrupt" in inspect.signature(watchfiles.watch).parameters:
        kw["raise_interrupt"] = True
    for _batch in watchfiles.watch(str(rootp), **kw):
        if _stopped(stop):
            return
        fp = hooks._fingerprint(rootp, db)
        if fp is None:
            stable = fp
        else:
            if fp == last:
                continue
            stable = _await_stable(rootp, db, fp, debounce, stop)
            if stable is None:
                return
            if stable == last:
                continue
        ok, last = _refresh_for(root, db, name, quiet, out, stop, stable, debounce)
        if not ok:
            return


def _backend(backend: str) -> str:
    if backend == "poll":
        return "poll"
    if backend == "watchfiles":
        return "watchfiles"
    try:
        import watchfiles  # noqa: F401
    except ImportError:
        return "poll"
    return "watchfiles"


def watch(root, db, name=None, debounce=1.0, interval=2.0, backend="auto", quiet=False, out=print, stop=None) -> int:
    """Watch `root` and `hooks.refresh` it into `db`. `stop` (a threading.Event) ends the loop for tests."""
    rootp = Path(root)
    if not rootp.is_dir():
        _emit(out, f"cg watch: {root} does not exist")
        return 2
    chosen = _backend(backend)
    if chosen == "watchfiles":
        try:
            import watchfiles  # noqa: F401
        except ImportError:
            _emit(out, 'cg watch: watchfiles is not installed; pip install "cg-code-graph[watch]" or pass --poll')
            return 2
    elif hooks._fingerprint(rootp, db) is None:
        _emit(out, f'cg watch: {root} is not a git repository; install watchfiles '
              f'(pip install "cg-code-graph[watch]") or use git')
        return 2
    mode = "watchfiles" if chosen == "watchfiles" else f"polling every {interval:g}s"
    try:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, signal.default_int_handler)
        _emit(out, f"watching {root} ({mode}); Ctrl-C stops")
        start = hooks._fingerprint(rootp, db)
        ok, last = _refresh_for(root, db, name, quiet, out, stop, start, debounce)
        if not ok:
            return 0
        if chosen == "watchfiles":
            _watchfiles(root, db, name, quiet, out, stop, debounce, last)
        else:
            _poll(root, db, name, quiet, out, stop, interval, debounce, last)
        return 0
    except KeyboardInterrupt:
        _emit(out, "watch stopped")
        # A SIGINT delivered during a native call or interpreter shutdown can still
        # terminate the process after this handler runs. Exit immediately on the
        # main thread so Ctrl-C is status 0.
        if threading.current_thread() is threading.main_thread():
            for stream in (sys.stdout, sys.stderr):
                try:
                    stream.flush()     # os._exit skips the interpreter's own flush (stdout is a pipe or file)
                except Exception:  # noqa: BLE001
                    pass
            os._exit(0)
        return 0

"""File discovery helpers that never abort on odd entries (dangling symlinks, links out of the repo, races).

A dangling symlink (e.g. a committed link to a file on the author's machine) is skipped with a warning by
discovery, and hashed by its link metadata in cache fingerprints, so one bad entry never stops a language."""
from __future__ import annotations

import os
import sys

_warned: set[str] = set()


def is_real_file(path: str | os.PathLike) -> bool:
    """True for a readable regular file (following symlinks); False for dangling links, dirs, sockets..."""
    try:
        return os.path.isfile(path)
    except OSError:
        return False


def warn_skip(path: str | os.PathLike, why: str = "dangling symlink") -> None:
    p = str(path)
    if p not in _warned:
        _warned.add(p)
        print(f"codegraph: skipping {p}: {why}", file=sys.stderr)


def keep_file(path: str | os.PathLike) -> bool:
    """Discovery filter: real files pass; a broken entry is reported once and skipped."""
    if is_real_file(path):
        return True
    if os.path.islink(path):
        warn_skip(path)
    return False


def stat_key(path: str | os.PathLike) -> str:
    """'size|mtime_ns' for fingerprints: the target's stat, else the link's own (dangling), else 'missing'."""
    for f in (os.stat, os.lstat):
        try:
            st = f(path)
            return f"{st.st_size}|{st.st_mtime_ns}"
        except OSError:
            continue
    return "missing"

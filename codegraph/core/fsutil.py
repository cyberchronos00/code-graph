"""File discovery helpers that never abort on odd entries (dangling symlinks, links out of the repo, races).

A dangling symlink (e.g. a committed link to a file on the author's machine) is skipped with a warning by
discovery, and hashed by its link target in cache fingerprints, so one bad entry never stops a language."""
from __future__ import annotations

import hashlib
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


# Bumped whenever the cache key scheme changes: every extractor / SCIP cache key includes it, so entries written by
# an older cg are never reused (TS and Dart drop them on the next write, the SCIP cache prunes them).
# 2: keys hash file content (was size + mtime, which returned stale facts after a same-size edit with a restored mtime).
CACHE_VERSION = 3


def content_key(path: str | os.PathLike) -> str:
    """'size|blake2b of the bytes' for cache fingerprints. Content, not mtime: an edit that keeps the size and the old
    mtime must still invalidate. A dangling symlink hashes as 'link|<target>', anything else unreadable by a marker."""
    if is_real_file(path):          # regular files only: never open a FIFO or device found in the tree
        try:
            h = hashlib.blake2b(digest_size=16)
            n = 0
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
                    n += len(chunk)
            return f"{n}|{h.hexdigest()}"
        except OSError:
            return "unreadable"
    try:
        return f"link|{os.readlink(path)}"
    except OSError:
        return "missing" if not os.path.lexists(path) else "special"

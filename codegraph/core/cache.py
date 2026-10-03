"""The per-user cache: where it lives, what is in it, and `cg clean`.

Root (one lookup for every user: extractors, SCIP outputs, parse caches, the Swift build directory):
$CODEGRAPH_CACHE, else $CODEGRAPH_CACHE_DIR (older name, still read), else %LOCALAPPDATA%\\codegraph on Windows, else
$XDG_CACHE_HOME/codegraph, else ~/.cache/codegraph.

Layout below the root (`v<N>` is fsutil.CACHE_VERSION, `<rkey>` the first 12 hex digits of sha256 of the resolved
project root):

    extractors/<lang>-<lock hash>/          extractor dependency installs (npm, Composer, dart pub)
    scip/<name>-v<N>-<rkey>-<key>.scip      SCIP outputs of the exact layers (rust, cfamily, kotlin), and .lock files
    scip/ra-config-<rkey>-<hash>.json       rust-analyzer configs
    scip/swift-v<N>-<build key>.lock        lock of one Swift build directory
    swift-build/<build key>/                `swift build --enable-index-store` output per project
    ts/<rkey>-v<N>-<fingerprint>.json       TypeScript extractor facts
    dart/<rkey>-v<N>-<fingerprint>.json     Dart extractor facts
    projects/<rkey>                         the project root path behind <rkey> (so `cg clean <dir>` finds the
                                            entries of every project indexed at or below <dir>)

An entry whose name does not follow this layout for the current cache version can never be read again: `--stale`
removes those (older cache versions, names from before this layout) and orphaned temporary and lock files.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import fsutil

try:
    import fcntl
except ImportError:          # Windows
    fcntl = None

KINDS = ("extractors", "scip", "rust-analyzer", "swift-build", "ts", "dart", "projects", "other")
PROJECT_KINDS = ("scip", "rust-analyzer", "swift-build", "ts", "dart", "projects")


class CacheError(Exception):
    pass


def root() -> Path:
    """The cache root (not created)."""
    env = os.environ.get("CODEGRAPH_CACHE") or os.environ.get("CODEGRAPH_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "codegraph"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "codegraph"


def subdir(name: str, create: bool = False) -> Path:
    d = root() / name
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def root_key(project_root) -> str:
    return hashlib.sha256(str(Path(project_root).resolve()).encode()).hexdigest()[:12]


def note_project(project_root) -> None:
    """Remember which project root a <rkey> stands for (best effort; a read-only cache only loses `cg clean <dir>`
    matching of sub-projects)."""
    try:
        p = str(Path(project_root).resolve())
        f = subdir("projects", create=True) / root_key(p)
        if not f.exists() or f.read_text(errors="replace") != p:
            tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
            tmp.write_text(p)
            os.replace(tmp, f)
    except OSError:
        pass


def swift_build_key(project_root) -> str:
    """Name of a project's Swift build directory (the same key plugins/swift/exact.py builds into)."""
    from ..plugins.native import runner
    return runner.fingerprint(Path(project_root), [], "swift-build-dir")[:16]


def check_root(r: Path | None = None) -> Path:
    """The cache root, refused when deleting below it could hit unrelated files: `/`, a drive root, $HOME or one of
    its ancestors."""
    r = Path(r or root())
    try:
        rr = r.resolve()
    except OSError as e:
        raise CacheError(f"cannot resolve the cache root {r}: {e}")
    home = Path.home().resolve()
    if rr == Path(rr.anchor) or rr == home or rr in home.parents:
        raise CacheError(f"refusing to clean: the cache root resolves to {rr} (set CODEGRAPH_CACHE to a directory of "
                         "its own, e.g. ~/.cache/codegraph)")
    return rr


# ------------------------------------------------------------------ classification
V = r"v(\d+)"
SCIP_RE = re.compile(rf"^(?P<name>[a-z]+)-{V}-(?P<rkey>[0-9a-f]{{12}})-(?P<key>[0-9a-z]+)\.(?:scip|lock)$")
SWIFT_LOCK_RE = re.compile(rf"^swift-{V}-(?P<key>[0-9a-f]{{16}})\.lock$")
RA_RE = re.compile(r"^ra-config-(?P<rkey>[0-9a-f]{12})-[0-9a-f]{12}\.json$")
FACTS_RE = re.compile(rf"^(?P<rkey>[0-9a-f]{{12}})-{V}-[0-9a-f]+\.json$")
TMP_RE = re.compile(r"\.tmp(\.scip)?$")


@dataclass
class Entry:
    path: Path
    kind: str
    size: int
    rkey: str | None = None          # project the entry belongs to, when its name says so
    swift_key: str | None = None
    stale: str | None = None         # why it can never be read again
    lock: bool = False
    tmp: bool = False


def _size(p: Path) -> int:
    try:
        if p.is_symlink() or not p.is_dir():
            return p.lstat().st_size
        n = 0
        for dp, _dns, fns in os.walk(p):
            for f in fns:
                try:
                    n += os.lstat(os.path.join(dp, f)).st_size
                except OSError:
                    pass
        return n
    except OSError:
        return 0


def _tmp_age_limit() -> float:
    return float(os.environ.get("CODEGRAPH_INDEXER_TIMEOUT", "3600")) + 600


def entries(r: Path | None = None) -> list[Entry]:
    """Every top-level entry of the cache (files in scip/ ts/ dart/ projects/, directories in extractors/ and
    swift-build/, anything else as `other`)."""
    r = Path(r or root())
    out: list[Entry] = []
    if not r.is_dir():
        return out
    cur = fsutil.CACHE_VERSION
    now = time.time()
    for top in sorted(r.iterdir()):
        name = top.name
        if not top.is_dir() or top.is_symlink():
            out.append(Entry(top, "other", _size(top)))
            continue
        for p in sorted(top.iterdir()):
            e = Entry(p, name if name in KINDS else "other", 0)
            n = p.name
            if TMP_RE.search(n):
                e.tmp = True
                try:
                    if now - p.lstat().st_mtime > _tmp_age_limit():
                        e.stale = "orphaned temporary file"
                except OSError:
                    pass
            elif name == "scip":
                m = SCIP_RE.match(n)
                ms = SWIFT_LOCK_RE.match(n)
                mr = RA_RE.match(n)
                if mr:
                    e.kind, e.rkey = "rust-analyzer", mr.group("rkey")
                elif n.startswith("ra-config-"):
                    e.kind, e.stale = "rust-analyzer", "name from before cache layout v2 (no project key)"
                elif ms:
                    e.swift_key, e.lock = ms.group("key"), True
                    if int(ms.group(1)) != cur:
                        e.stale = f"cache version {ms.group(1)} (current {cur})"
                elif m:
                    e.rkey, e.lock = m.group("rkey"), n.endswith(".lock")
                    if int(m.group(2)) != cur:
                        e.stale = f"cache version {m.group(2)} (current {cur})"
                else:
                    e.lock = n.endswith(".lock")
                    e.stale = "name from an older cache layout"
            elif name in ("ts", "dart"):
                m = FACTS_RE.match(n)
                if m:
                    e.rkey = m.group("rkey")
                    if int(m.group(2)) != cur:
                        e.stale = f"cache version {m.group(2)} (current {cur})"
                elif re.match(r"^[0-9a-f]{12}-", n):
                    e.rkey, e.stale = n[:12], "name from an older cache layout (no cache version)"
            elif name == "swift-build":
                e.swift_key = n
                try:
                    import json
                    v = json.loads((p / "codegraph-stamp.json").read_text()).get("cache_version")
                except (OSError, ValueError, AttributeError):
                    v = None
                if v is not None and v != cur:
                    e.stale = f"cache version {v} (current {cur})"
            elif name == "projects":
                e.rkey = n
            e.size = _size(p)
            out.append(e)
    return out


def _held(p: Path) -> bool:
    """A lock file some process holds right now."""
    if fcntl is None:
        return False
    try:
        fd = os.open(p, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def _projects(r: Path) -> dict[str, str]:
    out = {}
    d = r / "projects"
    if d.is_dir():
        for f in d.iterdir():
            if re.fullmatch(r"[0-9a-f]{12}", f.name):
                try:
                    out[f.name] = f.read_text(errors="replace").strip()
                except OSError:
                    pass
    return out


def _under(path: str, top: Path) -> bool:
    try:
        Path(path).relative_to(top)
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------ cleaning
@dataclass
class Plan:
    root: Path
    remove: list[Entry] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    db_files: list[Path] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)      # project roots matched by `cg clean <dir>`

    @property
    def bytes(self) -> int:
        return sum(e.size for e in self.remove) + sum(_size(p) for p in self.db_files)


def plan(project: str | os.PathLike | None = None, all_: bool = False, extractors: bool = False,
         stale: bool = False, db: str | os.PathLike | None = None) -> Plan:
    if not (project or all_ or stale or db):
        raise CacheError("nothing to clean: pass a project root, --all, --stale or --db")
    if extractors and not all_:
        raise CacheError("--extractors goes with --all")
    if not (project or all_ or stale):          # only --db: the cache is not touched
        pl = Plan(root())
        pl.db_files = _db_files(Path(db))
        return pl
    r = check_root()
    pl = Plan(r)
    es = entries(r)
    pick: dict[Path, Entry] = {}
    if all_:
        for e in es:
            if e.kind == "extractors" and not extractors:
                continue
            pick[e.path] = e
    if project:
        top = Path(project).expanduser().resolve()
        known = _projects(r)
        keys = {root_key(top)} | {k for k, p in known.items() if _under(p, top)}
        pl.projects = sorted(known[k] for k in keys if k in known)
        skeys = {swift_build_key(p) for p in {*pl.projects, str(top)}}
        for e in es:
            if (e.rkey and e.rkey in keys) or (e.swift_key and e.swift_key in skeys):
                pick[e.path] = e
    if stale:
        for e in es:
            if e.stale:
                pick[e.path] = e
            elif e.lock and e.kind == "scip" and not _lock_has_output(e, es):
                e.stale = "lock without a cache entry"
                pick[e.path] = e
    for p, e in sorted(pick.items()):
        if e.lock and _held(p):
            pl.skipped.append((p, "lock held by a running cg"))
            continue
        pl.remove.append(e)
    if db:
        pl.db_files = _db_files(Path(db))
    return pl


def _lock_has_output(e: Entry, es: list[Entry]) -> bool:
    if e.swift_key:
        return any(x.kind == "swift-build" and x.swift_key == e.swift_key for x in es)
    return e.path.with_suffix(".scip").exists()


def _db_files(db: Path) -> list[Path]:
    db = db.expanduser()
    if not db.exists():
        raise CacheError(f"--db {db}: no such file")
    if db.is_dir() or db.is_symlink():
        raise CacheError(f"--db {db}: not a regular file")
    with open(db, "rb") as f:
        head = f.read(16)
    if head != b"SQLite format 3\0" and db.stat().st_size:
        raise CacheError(f"--db {db}: not an SQLite database; nothing deleted")
    return [p for p in (db, Path(f"{db}-wal"), Path(f"{db}-shm"), Path(f"{db}-journal")) if p.exists()]


def execute(pl: Plan) -> list[tuple[Path, str]]:
    """Delete what the plan lists; returns the paths that could not be removed. Every cache path is re-checked to sit
    inside the cache root; symlinks are unlinked, never followed."""
    errors = []
    for e in pl.remove:
        p = e.path
        try:
            parent = p.parent.resolve()
            if parent != pl.root and pl.root not in parent.parents:
                errors.append((p, "outside the cache root"))
                continue
            if p.is_symlink() or not p.is_dir():
                p.unlink(missing_ok=True)
            else:
                shutil.rmtree(p)
        except OSError as ex:
            errors.append((p, str(ex)))
    for p in pl.db_files:
        try:
            p.unlink(missing_ok=True)
        except OSError as ex:
            errors.append((p, str(ex)))
    return errors


def usage(r: Path | None = None) -> dict:
    """Total cache size and the size per kind (for `cg doctor`)."""
    r = Path(r or root())
    by: dict[str, dict] = {}
    stale = 0
    for e in entries(r):
        k = by.setdefault(e.kind, {"bytes": 0, "entries": 0})
        k["bytes"] += e.size
        k["entries"] += 1
        if e.stale:
            stale += e.size
    return {"root": str(r), "bytes": sum(v["bytes"] for v in by.values()), "stale_bytes": stale,
            "kinds": {k: by[k] for k in KINDS if k in by}}


def human(n: int) -> str:
    x = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if x < 1024 or u == "GB":
            return f"{x:.0f} {u}" if u == "B" else f"{x:.1f} {u}"
        x /= 1024
    return f"{n} B"

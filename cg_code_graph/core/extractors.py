"""Where the Node / PHP / Dart extractors run from, and their dependency installs.

The extractor sources ship inside the package (cg_code_graph/plugins/{ts,php,dart}/extractor). Their dependencies
(npm `node_modules`, Composer `vendor/`, `dart pub get` + the compiled Dart extractor) are not part of the package:

- a checkout whose extractor directory already has them (`npm ci` run there) keeps using that directory;
- otherwise the sources are copied into a per-user cache directory and the dependencies installed there, on first
  use or by `cg setup`. The directory is keyed by the lock file and the extractor sources (names and contents), so a
  release that adds or edits a module installs into a fresh directory. A cache directory that is missing a listed
  source is incomplete and is rebuilt.

Cache root (core/cache.py): $CG_CACHE, else %LOCALAPPDATA%\\cg on Windows,
else $XDG_CACHE_HOME/cg or ~/.cache/cg; the extractors live in <root>/extractors.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import cache
from .env import get as cg_env

try:
    import fcntl
except ImportError:          # Windows: msvcrt byte-range locks instead
    fcntl = None

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"


@dataclass(frozen=True)
class Spec:
    lang: str
    pkg: Path                   # extractor directory inside the package
    sources: tuple[str, ...]    # files copied into the cache directory
    lock: str                   # lock file: keys the cache directory
    markers: tuple[str, ...]    # any of these present: dependencies installed
    tool: str                   # program the install needs
    label: str


SPECS = {
    "typescript": Spec("typescript", PLUGINS / "ts" / "extractor", ("extract.mjs", "fw.mjs", "rr.mjs", "package.json",
                                                                  "package-lock.json"),
                       "package-lock.json", ("node_modules/typescript/package.json",), "npm", "TypeScript / JavaScript"),
    "php": Spec("php", PLUGINS / "php" / "extractor", ("extract.php", "composer.json", "composer.lock"), "composer.lock",
                ("vendor/autoload.php",), "composer", "PHP"),
    "dart": Spec("dart", PLUGINS / "dart" / "extractor", ("bin/extract.dart", "pubspec.yaml", "pubspec.lock"), "pubspec.lock",
                 (".dart_tool/package_config.json", ".bin/extract"), "dart", "Dart"),
}


def cache_root() -> Path:
    return cache.root() / "extractors"


def _installed(d: Path, spec: Spec) -> bool:
    return any((d / m).exists() for m in spec.markers)


def _missing_sources(spec: Spec, d: Path) -> list[str]:
    """Listed sources that exist in the package but not in the cache directory."""
    return [rel for rel in spec.sources if (spec.pkg / rel).exists() and not (d / rel).exists()]


def _cache_key(spec: Spec) -> str:
    """Lock file plus every source path and its bytes, so a new or edited module does not reuse an old install."""
    h = hashlib.sha256()
    lock = spec.pkg / spec.lock
    h.update(lock.read_bytes() if lock.exists() else b"")
    for rel in spec.sources:
        h.update(b"\0")
        h.update(rel.encode())
        src = spec.pkg / rel
        h.update(b"\0")
        h.update(src.read_bytes() if src.exists() else b"")
    return h.hexdigest()[:12]


def cache_dir(lang: str) -> Path:
    spec = SPECS[lang]
    return cache_root() / f"{lang}-{_cache_key(spec)}"


def workdir(lang: str) -> Path:
    """The directory the extractor runs from: the package's own one when its dependencies are installed there (a
    development checkout), else the cache directory (sources synced into it; dependencies maybe not yet installed).
    A cache directory missing a listed source is rebuilt from the package sources."""
    spec = SPECS[lang]
    if _installed(spec.pkg, spec):
        return spec.pkg
    d = cache_dir(lang)
    if d.exists() and _missing_sources(spec, d):
        # Incomplete copy (for example a 0.19.0 typescript cache that predates a module): drop it and start over
        # when the key still matches, otherwise cache_dir already points at a fresh directory.
        shutil.rmtree(d, ignore_errors=True)
    _sync(spec, d)
    return d


def _sync(spec: Spec, d: Path) -> None:
    for rel in spec.sources:
        src, dst = spec.pkg / rel, d / rel
        if not src.exists():
            continue
        data = src.read_bytes()
        if dst.exists() and dst.read_bytes() == data:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".sync-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dst)          # atomic: a concurrent index sees the old or the new file


def status(lang: str) -> dict:
    spec = SPECS[lang]
    if _installed(spec.pkg, spec):
        return {"installed": True, "dir": str(spec.pkg), "where": "package"}
    d = cache_dir(lang)
    ready = _installed(d, spec) and not _missing_sources(spec, d)
    return {"installed": ready, "dir": str(d), "where": "cache"}


JS_RUNTIME_MISSING = (
    "node not installed (the TypeScript extractor needs Node.js 20+ or Bun on PATH, "
    "or CG_NODE=/path/to/node)"
)


def _runtime_kind(path: str) -> str:
    """`bun` when the binary (or what a `node` symlink points at, as in Bun images) is Bun, else `node`."""
    names = (Path(path).name, Path(os.path.realpath(path)).name)
    return "bun" if any(n.lower().startswith("bun") for n in names) else "node"


def _override() -> str | None:
    v = (cg_env("NODE") or "").strip()
    return os.path.expanduser(v) if v else None


def js_runtime() -> dict | None:
    """Node.js or Bun for the TypeScript extractor.

    ``CG_NODE`` (a name on PATH or a path) wins, then ``node`` on PATH, then ``bun``.
    """
    override = _override()
    if override:
        path = shutil.which(override)
        if not path:
            return None
        return {"path": path, "kind": _runtime_kind(path), "source": "CG_NODE"}
    node = shutil.which("node")
    if node:
        return {"path": node, "kind": _runtime_kind(node), "source": "PATH"}
    bun = shutil.which("bun")
    if bun:
        return {"path": bun, "kind": "bun", "source": "PATH"}
    return None


def js_runtime_problem() -> str | None:
    """Why the TypeScript extractor cannot start, or None when a runtime resolves."""
    override = _override()
    if override and not shutil.which(override):
        return (f"CG_NODE={cg_env('NODE')} is not an executable on PATH or a path to one "
                "(set it to a node or bun binary, or unset it to use node / bun from PATH)")
    if js_runtime() is None:
        return JS_RUNTIME_MISSING
    return None


def install_command(lang: str, dart: str | None = None) -> list[str]:
    if lang == "typescript":
        npm = shutil.which("npm")
        if npm:
            return [npm, "ci", "--no-audit", "--no-fund", "--loglevel=error"]
        rt = js_runtime()
        bun = rt["path"] if rt and rt["kind"] == "bun" else shutil.which("bun")
        if bun:
            return [bun, "install", "--frozen-lockfile", "--no-progress"]
        return ["npm", "ci", "--no-audit", "--no-fund", "--loglevel=error"]
    if lang == "php":
        return [shutil.which("composer") or "composer", "install", "--no-dev", "--no-interaction", "--no-progress", "--quiet"]
    return [dart or shutil.which("dart") or "dart", "pub", "get"]


@contextlib.contextmanager
def install_lock(d: Path):
    """Hold `<d>/.install.lock` while installing into d: two first-time installs into the same directory at once
    (two `cg index` runs, or `cg setup` next to one) wait for each other instead of running npm / composer / dart pub
    in one directory twice (#65)."""
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(d / ".install.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            try:
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            except (ImportError, OSError):
                pass
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def ensure(lang: str, dart: str | None = None) -> Path:
    """Install the extractor's dependencies if missing; returns the directory to run it from."""
    spec = SPECS[lang]
    d = workdir(lang)
    if lang == "dart":
        done = lambda: (d / ".dart_tool" / "package_config.json").exists()
    else:
        done = lambda: _installed(d, spec)
    if done():
        return d
    with install_lock(d):
        if not done():                 # another process may have finished the install while this one waited
            _run(install_command(lang, dart), d, spec)
    return d


CACHE_DIR_RE = re.compile(r"^(?P<lang>[a-z]+)-[0-9a-f]{12}$")


def prune(dry_run: bool = False) -> list[tuple[Path, int]]:
    """`cg setup --prune`: remove extractor installs (`<language>-<lock hash>` directories) this version of cg does
    not use: left behind by updates that changed a lock file. Another installed cg version that still uses one
    reinstalls it on its next index. A directory whose install lock is held is kept."""
    root = cache_root()
    keep = {cache_dir(lang).name for lang in SPECS}
    out = []
    if not root.is_dir():
        return out
    for p in sorted(root.iterdir()):
        m = CACHE_DIR_RE.match(p.name)
        if not p.is_dir() or p.is_symlink() or not m or m.group("lang") not in SPECS or p.name in keep:
            continue
        if cache._held(p / ".install.lock"):
            continue
        size = cache._size(p)
        if not dry_run:
            shutil.rmtree(p, ignore_errors=True)
        out.append((p, size))
    return out


def _run(cmd: list[str], d: Path, spec: Spec) -> None:
    if not shutil.which(cmd[0]) and not Path(cmd[0]).exists():
        tool = "`npm` (or `bun`)" if spec.tool == "npm" else f"`{spec.tool}`"
        raise RuntimeError(f"{spec.label} extractor dependencies missing and {tool} is not installed")
    r = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{spec.label} extractor dependency install failed ({' '.join(cmd[:2])} in {d}): "
                           f"{(r.stderr or r.stdout)[-1500:]}")

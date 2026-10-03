"""Where the Node / PHP / Dart extractors run from, and their dependency installs.

The extractor sources ship inside the package (codegraph/plugins/{ts,php,dart}/extractor). Their dependencies
(npm `node_modules`, Composer `vendor/`, `dart pub get` + the compiled Dart extractor) are not part of the package:

- a checkout whose extractor directory already has them (`npm ci` run there) keeps using that directory;
- otherwise the sources are copied into a per-user cache directory and the dependencies installed there, on first
  use or by `cg setup`. The directory is keyed by the lock file, so an update that only changes the extractor source
  reuses the installed dependencies, and one that changes the lock file installs into a fresh directory.

Cache root: $CODEGRAPH_CACHE (default ~/.cache/codegraph; %LOCALAPPDATA%\\codegraph on Windows) / extractors.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
    "typescript": Spec("typescript", PLUGINS / "ts" / "extractor", ("extract.mjs", "fw.mjs", "package.json", "package-lock.json"),
                       "package-lock.json", ("node_modules/typescript/package.json",), "npm", "TypeScript / JavaScript"),
    "php": Spec("php", PLUGINS / "php" / "extractor", ("extract.php", "composer.json", "composer.lock"), "composer.lock",
                ("vendor/autoload.php",), "composer", "PHP"),
    "dart": Spec("dart", PLUGINS / "dart" / "extractor", ("bin/extract.dart", "pubspec.yaml", "pubspec.lock"), "pubspec.lock",
                 (".dart_tool/package_config.json", ".bin/extract"), "dart", "Dart"),
}


def cache_root() -> Path:
    env = os.environ.get("CODEGRAPH_CACHE")
    if env:
        base = Path(env)
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"]) / "codegraph"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "codegraph"
    return base / "extractors"


def _installed(d: Path, spec: Spec) -> bool:
    return any((d / m).exists() for m in spec.markers)


def cache_dir(lang: str) -> Path:
    spec = SPECS[lang]
    lock = spec.pkg / spec.lock
    key = hashlib.sha256(lock.read_bytes() if lock.exists() else b"").hexdigest()[:12]
    return cache_root() / f"{lang}-{key}"


def workdir(lang: str) -> Path:
    """The directory the extractor runs from: the package's own one when its dependencies are installed there (a
    development checkout), else the cache directory (sources synced into it; dependencies maybe not yet installed)."""
    spec = SPECS[lang]
    if _installed(spec.pkg, spec):
        return spec.pkg
    d = cache_dir(lang)
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
    return {"installed": _installed(d, spec), "dir": str(d), "where": "cache"}


def install_command(lang: str, dart: str | None = None) -> list[str]:
    if lang == "typescript":
        npm = shutil.which("npm") or "npm"
        return [npm, "ci", "--no-audit", "--no-fund", "--loglevel=error"]
    if lang == "php":
        return [shutil.which("composer") or "composer", "install", "--no-dev", "--no-interaction", "--no-progress", "--quiet"]
    return [dart or shutil.which("dart") or "dart", "pub", "get"]


def ensure(lang: str, dart: str | None = None) -> Path:
    """Install the extractor's dependencies if missing; returns the directory to run it from."""
    spec = SPECS[lang]
    d = workdir(lang)
    if lang == "dart":
        if not (d / ".dart_tool" / "package_config.json").exists():
            _run(install_command(lang, dart), d, spec)
        return d
    if not _installed(d, spec):
        _run(install_command(lang, dart), d, spec)
    return d


def _run(cmd: list[str], d: Path, spec: Spec) -> None:
    if not shutil.which(cmd[0]) and not Path(cmd[0]).exists():
        raise RuntimeError(f"{spec.label} extractor dependencies missing and `{spec.tool}` is not installed")
    r = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{spec.label} extractor dependency install failed ({' '.join(cmd[:2])} in {d}): "
                           f"{(r.stderr or r.stdout)[-1500:]}")

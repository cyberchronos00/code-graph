"""Project config file: `.cg.yaml` (or `.cg.yml`) at the indexed root, discovered automatically.

Records project-specific knowledge once, for the CLI, the MCP server and the visual view. Keys read today:

    version: 1
    python:
      source_roots: [lib, tools/scripts]   # replace source-root detection (paths relative to the indexed root)

Command-line flags take precedence over the file (`cg index --python-root DIR`). Top-level keys this version does not
read are kept and reported in the index stats, so a file written for a newer cg still indexes."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

CONFIG_NAMES = (".cg.yaml", ".cg.yml")
KNOWN_KEYS = {"version", "python"}
PYTHON_KEYS = {"source_roots"}


class ConfigError(ValueError):
    """Invalid project config file; the message names the file and the key."""


def find(root: str | Path) -> Path | None:
    for n in CONFIG_NAMES:
        p = Path(root) / n
        if p.is_file():
            return p
    return None


def norm_root(value: Any, where: str) -> str:
    """A repo-relative directory: 'lib', 'tools/scripts/', './src', '.'. Absolute paths and '..' are rejected."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: expected a directory path relative to the indexed root, got {value!r}")
    v = value.strip().replace("\\", "/")
    p = PurePosixPath(v)
    if p.is_absolute() or ".." in p.parts:
        raise ConfigError(f"{where}: {value!r} must stay inside the indexed root (relative path, no '..')")
    s = str(p)
    return "" if s == "." else s


def load(root: str | Path) -> dict:
    """{"file": ".cg.yaml", "python": {"source_roots": [...]}, "ignored_keys": [...]} or {} without a config file.
    Raises ConfigError for a file cg cannot use."""
    p = find(root)
    if p is None:
        return {}
    import yaml
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as ex:
        raise ConfigError(f"{p.name}: not valid YAML ({str(ex).splitlines()[0]})") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{p.name}: expected a mapping of keys at the top level")
    ver = data.get("version", 1)
    if ver != 1:
        raise ConfigError(f"{p.name}: version {ver!r} is not supported (this cg reads version 1)")
    out: dict = {"file": p.name}
    py = data.get("python")
    if py is not None:
        if not isinstance(py, dict):
            raise ConfigError(f"{p.name}: python: expected a mapping (e.g. python: {{source_roots: [src]}})")
        unknown = sorted(set(py) - PYTHON_KEYS)
        if unknown:
            raise ConfigError(f"{p.name}: python: unknown key {unknown[0]!r} (known: {', '.join(sorted(PYTHON_KEYS))})")
        roots = py.get("source_roots")
        if roots is not None:
            if isinstance(roots, str):
                roots = [roots]
            if not isinstance(roots, list) or not roots:
                raise ConfigError(f"{p.name}: python.source_roots: expected a non-empty list of directories")
            out["python"] = {"source_roots": list(dict.fromkeys(
                norm_root(r, f"{p.name}: python.source_roots[{i}]") for i, r in enumerate(roots)))}
    ignored = sorted(str(k) for k in set(data) - KNOWN_KEYS)
    if ignored:
        out["ignored_keys"] = ignored
    return out

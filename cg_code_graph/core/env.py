"""Process environment: ``CG_*`` names, with ``CODEGRAPH_*`` aliases through 0.18.x.

``get`` reads ``os.environ`` on every call. A legacy name that is set produces one stderr
warning per name per process. ``CG_*`` wins when both are set. ``MCP_TOOLS`` has no legacy
name (``CODEGRAPH_MCP_TOOLS`` belongs to another project).
"""
from __future__ import annotations

import os
import sys

# Suffixes this project reads. ``CACHE`` also accepts the older ``CODEGRAPH_CACHE_DIR``.
SUFFIXES = (
    "CACHE", "CACHE_DIR", "CARGO", "CFAMILY", "COMPDB", "C_MASK_ANNOTATIONS", "C_MAX_MACRO_REFS",
    "C_SCIP", "C_SCIP_FILE", "EXCLUDE_DIRS", "INCLUDE_DIRS", "INDEXER_TIMEOUT", "JOBS",
    "JAVA_SCIP", "JAVA_SCIP_FILE", "KOTLIN_SCIP", "KOTLIN_SCIP_FILE", "LIBINDEXSTORE",
    "MAX_FILE_BYTES", "NODE", "NO_CACHE",
    "NO_CARGO", "NO_HOOKS", "NO_STALE_CHECK", "RUST_ANALYZER", "RUST_BUILD_SCRIPTS", "RUST_SCIP",
    "RUST_SCIP_FILE", "RUST_TARGETS", "SCIP_CLANG", "SCIP_JAVA", "SWIFT", "SWIFT_INDEX",
    "SWIFT_INDEX_STORE",
)
LEGACY = {"CACHE": ("CODEGRAPH_CACHE", "CODEGRAPH_CACHE_DIR")}
_NO_LEGACY = frozenset({"MCP_TOOLS"})
_warned: set[str] = set()


def _legacy_names(name: str) -> tuple[str, ...]:
    if name in _NO_LEGACY:
        return ()
    return LEGACY.get(name, ("CODEGRAPH_" + name,))


def _warn(old: str, new: str, *, both: bool) -> None:
    if old in _warned:
        return
    _warned.add(old)
    if both:
        extra = f"{new} is set too and wins; the CODEGRAPH_* names are removed in 0.19.0"
    else:
        extra = "the CODEGRAPH_* names are removed in 0.19.0"
    print(f"cg: {old} is deprecated, use {new} ({extra})", file=sys.stderr)


def get(name: str, default=None) -> str | None:
    """``CG_<name>``, else the legacy ``CODEGRAPH_*`` value, else ``default``."""
    new = "CG_" + name
    legacy = _legacy_names(name)
    if new in os.environ:
        for old in legacy:
            if old in os.environ:
                _warn(old, new, both=True)
        return os.environ[new]
    found = None
    for old in legacy:
        if old in os.environ:
            _warn(old, new, both=False)
            if found is None:
                found = os.environ[old]
    return default if found is None else found


def is_set(name: str) -> bool:
    """True when ``CG_<name>`` or a legacy alias is present in the environment."""
    if ("CG_" + name) in os.environ:
        return True
    return any(old in os.environ for old in _legacy_names(name))


def flag(name: str) -> bool:
    """True when the value is set and not a false-like token (``0``, ``false``, ``no``, ``off``)."""
    val = get(name)
    return val is not None and val.strip().lower() not in ("", "0", "false", "no", "off")


def legacy_in_env() -> list[str]:
    """``CODEGRAPH_*`` names from this project that are present (for ``cg doctor``)."""
    found: list[str] = []
    seen: set[str] = set()
    for name in SUFFIXES:
        for old in _legacy_names(name):
            if old in os.environ and old not in seen:
                seen.add(old)
                found.append(old)
    return found

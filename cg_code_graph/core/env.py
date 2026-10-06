"""Process environment: ``CG_*`` names only.

``get`` reads ``os.environ`` on every call. A leftover ``CODEGRAPH_*`` name from this project
does not affect the value. The first read prints one stderr notice naming each leftover and
its ``CG_*`` replacement. ``CODEGRAPH_MCP_TOOLS`` belongs to another project and is ignored
silently.
"""
from __future__ import annotations

import os
import sys

# Suffixes this project reads. ``CODEGRAPH_CACHE_DIR`` renames to ``CG_CACHE``.
SUFFIXES = (
    "CACHE", "CACHE_DIR", "CARGO", "CFAMILY", "COMPDB", "C_MASK_ANNOTATIONS", "C_MAX_MACRO_REFS",
    "C_SCIP", "C_SCIP_FILE", "EXCLUDE_DIRS", "INCLUDE_DIRS", "INDEXER_TIMEOUT", "JOBS",
    "JAVA_SCIP", "JAVA_SCIP_FILE", "KOTLIN_SCIP", "KOTLIN_SCIP_FILE", "LIBINDEXSTORE",
    "MAX_FILE_BYTES", "NODE", "NO_CACHE",
    "NO_CARGO", "NO_HOOKS", "NO_STALE_CHECK", "RUST_ANALYZER", "RUST_BUILD_SCRIPTS", "RUST_SCIP",
    "RUST_SCIP_FILE", "RUST_TARGETS", "SCIP_CLANG", "SCIP_JAVA", "SWIFT", "SWIFT_INDEX",
    "SWIFT_INDEX_STORE",
)
_NO_LEGACY = frozenset({"MCP_TOOLS", "CACHE_DIR"})
_noted = False


def replacements() -> dict[str, str]:
    """Old name -> ``CG_*`` name, in suffix order. ``CACHE_DIR`` is ``CG_CACHE``."""
    out: dict[str, str] = {}
    for name in SUFFIXES:
        if name in _NO_LEGACY:
            continue
        if name == "CACHE":
            out["CODEGRAPH_CACHE"] = "CG_CACHE"
            out["CODEGRAPH_CACHE_DIR"] = "CG_CACHE"
        else:
            out["CODEGRAPH_" + name] = "CG_" + name
    return out


def note_removed() -> list[str]:
    """Print one stderr line when any removed name is set. Returns those old names."""
    global _noted
    mapping = replacements()
    found = [old for old in mapping if old in os.environ]
    if _noted:
        return found
    _noted = True
    if found:
        pairs = ", ".join(f"{old} -> {mapping[old]}" for old in found)
        print(f"cg: ignored removed environment variables: {pairs}", file=sys.stderr)
    return found


def get(name: str, default=None) -> str | None:
    """``CG_<name>`` when set, else ``default``. Removed ``CODEGRAPH_*`` names are ignored."""
    note_removed()
    new = "CG_" + name
    if new in os.environ:
        return os.environ[new]
    return default


def is_set(name: str) -> bool:
    """True when ``CG_<name>`` is present in the environment."""
    note_removed()
    return ("CG_" + name) in os.environ


def flag(name: str) -> bool:
    """True when the value is set and not a false-like token (``0``, ``false``, ``no``, ``off``)."""
    val = get(name)
    return val is not None and val.strip().lower() not in ("", "0", "false", "no", "off")


def legacy_in_env() -> list[str]:
    """Removed ``CODEGRAPH_*`` names from this project that are present (for ``cg doctor``)."""
    return note_removed()

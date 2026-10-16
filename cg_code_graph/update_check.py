"""Newer-release notice for `cg doctor` and `cg --version` (#98).

Only these two commands look, and only `cg doctor` goes to the network: one GET of PyPI's JSON for cg-code-graph with
a short timeout, cached for a day in the user cache (`<cache root>/update-check.json`). `cg --version` reads that
cache and never connects. Indexing, queries and the MCP server never check. Any failure (offline, slow, a proxy, an
unreadable cache) is silent: the notice is left out.

Off when `CG_NO_UPDATE_CHECK` is set to anything but `0` / empty, under CI (`CI`), and in tests (`PYTEST_CURRENT_TEST`).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

PYPI_URL = "https://pypi.org/pypi/cg-code-graph/json"
MAX_AGE = 24 * 3600
TIMEOUT = 2.0
UPGRADE = "uv tool upgrade cg-code-graph  |  pipx upgrade cg-code-graph"


def disabled() -> str | None:
    """Why the check is off, or None when it may run."""
    v = os.environ.get("CG_NO_UPDATE_CHECK")
    if v not in (None, "", "0"):
        return "CG_NO_UPDATE_CHECK"
    if os.environ.get("CI") not in (None, "", "0", "false"):
        return "CI"
    if _under_tests():
        return "tests"
    return None


def _under_tests() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def cache_file() -> Path | None:
    try:
        from .core import cache
        return Path(cache.root()) / "update-check.json"
    except Exception:
        return None


def parse_version(v: str) -> tuple | None:
    """`0.23.2` -> (0, 23, 2); a pre-release / dev / local version -> None (never offered as newer)."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest: str | None, current: str) -> bool:
    lv, cv = parse_version(latest or ""), parse_version(current)
    if lv is None or cv is None:
        return False
    n = max(len(lv), len(cv))
    return lv + (0,) * (n - len(lv)) > cv + (0,) * (n - len(cv))


def _read_cache(path: Path | None) -> dict | None:
    try:
        d = json.loads(path.read_text()) if path else None
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("latest"), str) and isinstance(d.get("checked_at"), (int, float)) else None


def _write_cache(path: Path | None, latest: str, now: float) -> None:
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"latest": latest, "checked_at": now}))
        os.replace(tmp, path)
    except OSError:
        pass


def fetch_latest(timeout: float = TIMEOUT) -> str | None:
    """The latest release on PyPI, or None on any failure."""
    import urllib.request
    try:
        req = urllib.request.Request(PYPI_URL, headers={"Accept": "application/json", "User-Agent": "cg-update-check"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read(2_000_000).decode("utf-8", "replace"))
        v = (data.get("info") or {}).get("version")
        return v if isinstance(v, str) and v.strip() else None
    except Exception:
        return None


def check(current: str | None = None, network: bool = True, now: float | None = None, timeout: float = TIMEOUT) -> dict:
    """{current, latest, newer, source: cache | pypi | none, disabled}. network=False only reads the cache."""
    if current is None:
        from . import __version__ as current
    out = {"current": current, "latest": None, "newer": False, "source": "none", "disabled": disabled()}
    if out["disabled"]:
        return out
    now = time.time() if now is None else now
    path = cache_file()
    cached = _read_cache(path)
    if cached and (not network or 0 <= now - cached["checked_at"] < MAX_AGE):
        out.update(latest=cached["latest"], source="cache")
    elif network:
        latest = fetch_latest(timeout)
        if latest:
            _write_cache(path, latest, now)
            out.update(latest=latest, source="pypi")
        elif cached:
            out.update(latest=cached["latest"], source="cache")
    out["newer"] = is_newer(out["latest"], current)
    return out


def notice(res: dict) -> str | None:
    """One line when a newer release exists, else None."""
    if not res.get("newer"):
        return None
    return f"cg {res['latest']} is available (this is {res['current']}): {UPGRADE}"

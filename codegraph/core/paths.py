"""Shared skip rules for every directory walk: the preset skip lists plus the project's `.cg.yaml`.

    exclude:            ["legacy/**", "*.generated.ts", "/tools"]   paths never indexed (gitignore-style globs)
    skip_dirs: {add: [fixtures_big], keep: [static]}               directory names skipped / not skipped anywhere

`rules(project, preset, *keys)` gives the PathRules of one walk; every language plugin, the extractors' configs and
the coverage scan use it, so one config entry applies to the whole index."""
from __future__ import annotations

import re
from typing import Iterable

from .. import presets


def glob_regex(pattern: str) -> str:
    """gitignore-style glob -> regex over repo-relative POSIX paths (Python and JavaScript compatible).
    `*` and `?` stay inside one path segment, `**` spans segments, a leading `/` anchors at the root, a pattern
    without `/` matches at any depth, and a match on a directory covers everything below it."""
    p = pattern.strip().replace("\\", "/")
    anchored = p.startswith("/") or "/" in p.rstrip("/")
    p = p.strip("/")
    out, i = [], 0
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c) if c not in "/-_" else c)
            i += 1
    return ("^" if anchored else "^(?:.*/)?") + "".join(out) + "(?:/.*)?$"


def names_regex(names: Iterable[str]) -> str | None:
    """Directory names anywhere in a relative path, as one regex (for the TS extractor's skip config)."""
    names = sorted(set(names))
    return r"(^|/)(" + "|".join(re.escape(n) for n in names) + r")(/|$)" if names else None


class PathRules:
    """skip(rel_dir, name) for directories, excluded(rel_path) for files; rel paths are POSIX, relative to the root."""

    def __init__(self, names: Iterable[str] = (), exclude: Iterable[str] = (), paths: Iterable[str] = ()):
        self.names = frozenset(names)
        self.exclude = tuple(exclude)
        self.paths = frozenset(p.strip("/") for p in paths)     # root-relative directories (PHP vendor/, storage/ ...)
        self._rx = re.compile("|".join(f"(?:{glob_regex(g)})" for g in self.exclude)) if self.exclude else None

    def excluded(self, rel: str) -> bool:
        return bool(self._rx and self._rx.match(rel))

    def skip(self, rel_dir: str, name: str) -> bool:
        if name in self.names:
            return True
        rel = f"{rel_dir}/{name}" if rel_dir else name
        return rel in self.paths or self.excluded(rel + "/")

    def prune(self, rel_dir: str, dns: list[str], dot: bool = False) -> list[str]:
        """Directory names of one os.walk step to descend into (sorted); dot=True also drops hidden directories."""
        return sorted(d for d in dns if not (dot and d.startswith(".")) and not self.skip(rel_dir, d))

    def exclude_regex(self) -> str | None:
        """The exclude globs as one JavaScript-compatible regex (None without excludes)."""
        return "|".join(f"(?:{glob_regex(g)})" for g in self.exclude) or None


def project_settings(project) -> dict:
    cfg = (getattr(project, "options", None) or {}).get("config") or {}
    sd = cfg.get("skip_dirs") or {}
    return {"exclude": list(cfg.get("exclude") or []), "add": list(sd.get("add") or []), "keep": list(sd.get("keep") or [])}


def rules(project, preset: str = "common", *keys: str, base: Iterable[str] | None = None) -> PathRules:
    """PathRules of one walk: preset skip dirs (common.skip_dirs + `keys` of `preset`, or `base`) adjusted by the
    project's .cg.yaml (skip_dirs.add / keep, exclude)."""
    s = project_settings(project) if project is not None else {"exclude": [], "add": [], "keep": []}
    names = set(base if base is not None else presets.skip_dirs(preset, *keys))
    names = (names | set(s["add"])) - set(s["keep"])
    paths = [p for p in (presets.values(preset, "skip_paths", default=[]) or []) if p not in s["keep"]] if base is None else []
    return PathRules(names, s["exclude"], paths)


def rel_dir(root: str, dp: str) -> str:
    import os
    r = os.path.relpath(dp, root)
    return "" if r == "." else r.replace(os.sep, "/")

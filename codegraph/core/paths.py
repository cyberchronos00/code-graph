"""Shared skip rules for every directory walk: the preset skip lists plus the project's `.cg.yaml`.

    exclude:            ["legacy/**", "*.generated.ts", "/tools"]   paths never indexed (gitignore-style globs)
    skip_dirs: {add: [fixtures_big], keep: [static]}               directory names skipped / not skipped anywhere
    include:   [src/generated, node_modules/@acme/sdk]             directories indexed although a built-in skip
                                                                   (a directory name, generated files) leaves them out

`rules(project, preset, *keys)` gives the PathRules of one walk; every language plugin, the extractors' configs and
the coverage scan use it, so one config entry applies to the whole index."""
from __future__ import annotations

import re
from typing import Iterable

from .. import presets


def glob_regex(pattern: str, below: bool = True) -> str:
    """gitignore-style glob -> regex over repo-relative POSIX paths (Python and JavaScript compatible).
    `*` and `?` stay inside one path segment, `**` spans segments, a leading `/` anchors at the root, a pattern
    without `/` matches at any depth, and a match on a directory covers everything below it (below=False: only the
    path itself matches, as in .gitattributes)."""
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
    return ("^" if anchored else "^(?:.*/)?") + "".join(out) + ("(?:/.*)?$" if below else "$")


def names_regex(names: Iterable[str]) -> str | None:
    """Directory names anywhere in a relative path, as one regex (for the TS extractor's skip config)."""
    names = sorted(set(names))
    return r"(^|/)(" + "|".join(re.escape(n) for n in names) + r")(/|$)" if names else None


class PathRules:
    """skip(rel_dir, name) for directories, excluded(rel_path) for files; rel paths are POSIX, relative to the root.
    `generated`: the project's generated / copied / vendored file classifier (codegraph/core/generated.py); its files
    are excluded too unless the index includes them (`--include-generated`)."""

    def __init__(self, names: Iterable[str] = (), exclude: Iterable[str] = (), paths: Iterable[str] = (), generated=None,
                 include: Iterable[str] = (), keep: Iterable[str] = ()):
        self.names = frozenset(names)
        self.include = tuple(sorted({p.strip("/") for p in include if p.strip("/")}))
        self.keep = frozenset(keep)        # names kept by .cg.yaml skip_dirs.keep (also hidden directories)
        self._forced: dict[str, bool] = {}
        self.exclude = tuple(exclude)
        self.paths = frozenset(p.strip("/") for p in paths)     # root-relative directories (PHP vendor/, storage/ ...)
        self._rx = re.compile("|".join(f"(?:{glob_regex(g)})" for g in self.exclude)) if self.exclude else None
        self.generated = generated if generated is not None and not generated.include else None

    def included(self, rel: str) -> bool:
        """`rel` is an `include` directory or inside one."""
        rel = rel.rstrip("/")
        return any(rel == i or rel.startswith(i + "/") for i in self.include)

    def on_include_path(self, rel: str) -> bool:
        """`rel` is an `include` directory, inside one, or a directory on the way to one."""
        rel = rel.rstrip("/")
        return self.included(rel) or any(i.startswith(rel + "/") for i in self.include)

    def excluded(self, rel: str, generated: bool = True) -> bool:
        """`rel` is never indexed: a .cg.yaml exclude glob, or (generated=True) a generated / copied / vendored file
        outside the `include` directories."""
        if self._rx and self._rx.match(rel):
            return True
        if self.include and self.included(rel):
            return False
        return bool(generated and self.generated is not None and not rel.endswith("/") and self.generated.excludes(rel))

    def _skip_own(self, rel_dir: str, name: str, dot: bool = False) -> bool:
        if name in self.names or (dot and name.startswith(".") and name not in self.keep):
            return True
        rel = f"{rel_dir}/{name}" if rel_dir else name
        return rel in self.paths or self.excluded(rel + "/") or (self.generated is not None and self.generated.dir_excluded(rel))

    def _forced_dir(self, rel_dir: str, dot: bool) -> bool:
        """`rel_dir` (or a directory above it) is skipped by itself and only walked to reach an `include` directory."""
        if not rel_dir or self.included(rel_dir):
            return False
        k = f"{int(dot)}{rel_dir}"
        if k not in self._forced:
            parent, _, name = rel_dir.rpartition("/")
            self._forced[k] = self._skip_own(parent, name, dot) or self._forced_dir(parent, dot)
        return self._forced[k]

    def skip(self, rel_dir: str, name: str, dot: bool = False) -> bool:
        if not self.include:
            return self._skip_own(rel_dir, name, dot)
        rel = f"{rel_dir}/{name}" if rel_dir else name
        if self._rx and self._rx.match(rel + "/"):
            return True
        if self.on_include_path(rel):
            return False
        return self._forced_dir(rel_dir, dot) or self._skip_own(rel_dir, name, dot)

    def prune(self, rel_dir: str, dns: list[str], dot: bool = False) -> list[str]:
        """Directory names of one os.walk step to descend into (sorted); dot=True also drops hidden directories
        (except names in skip_dirs.keep)."""
        return sorted(d for d in dns if not self.skip(rel_dir, d, dot))

    def extractor_cfg(self, extra: Iterable[str] = ()) -> dict:
        """The skip rules for an extractor that walks on its own (TS, Dart): directory names (`extra`: more names of
        that walk, minus skip_dirs.keep), the names kept (hidden directories included) and the include paths."""
        return {"skip_names": sorted(self.names | (set(extra) - self.keep)), "keep_names": sorted(self.keep),
                "include": list(self.include)}

    def user_exclude_regex(self) -> str | None:
        """The .cg.yaml exclude globs as one JavaScript-compatible regex (they apply inside `include` too)."""
        return "|".join(f"(?:{glob_regex(g)})" for g in self.exclude) or None

    def generated_regex(self) -> str | None:
        """The directories the generated-file classifier excludes as a whole, as one regex."""
        return "|".join(self.generated.dir_regexes()) if self.generated is not None else None

    def exclude_regex(self) -> str | None:
        """The exclude globs (and the directories the generated-file classifier excludes as a whole) as one
        JavaScript-compatible regex (None without either)."""
        parts = [f"(?:{glob_regex(g)})" for g in self.exclude]
        if self.generated is not None:
            parts += self.generated.dir_regexes()
        return "|".join(parts) or None

    def excluded_files(self, exts: Iterable[str]) -> list[str]:
        """Classified generated / copied / vendored files with one of `exts` that the scan found (for an extractor
        that walks on its own)."""
        if self.generated is None:
            return []
        exts = tuple(exts)
        build = self.generated.build_dirs
        # declaration files and framework build directories (Nuxt's .nuxt/) stay readable: they are resolution input
        return sorted(f for f in self.generated.files if f.lower().endswith(exts) and not f.endswith(".d.ts")
                      and not any(p in build for p in f.split("/")[:-1]) and not (self.include and self.included(f)))


def project_settings(project) -> dict:
    cfg = (getattr(project, "options", None) or {}).get("config") or {}
    sd = cfg.get("skip_dirs") or {}
    return {"exclude": list(cfg.get("exclude") or []), "add": list(sd.get("add") or []), "keep": list(sd.get("keep") or []),
            "include": list(cfg.get("include") or [])}


def rules(project, preset: str = "common", *keys: str, base: Iterable[str] | None = None, generated: bool = True) -> PathRules:
    """PathRules of one walk: preset skip dirs (common.skip_dirs + `keys` of `preset`, or `base`) adjusted by the
    project's .cg.yaml (skip_dirs.add / keep, exclude), plus the project's generated-file classifier (generated=False:
    without it, for the scan that runs the classifier)."""
    s = project_settings(project) if project is not None else {"exclude": [], "add": [], "keep": [], "include": []}
    names = set(base if base is not None else presets.skip_dirs(preset, *keys))
    names = (names | set(s["add"])) - set(s["keep"])
    paths = [p for p in (presets.values(preset, "skip_paths", default=[]) or []) if p not in s["keep"]] if base is None else []
    gen = (getattr(project, "options", None) or {}).get("generated") if generated and project is not None else None
    return PathRules(names, s["exclude"], paths, generated=gen, include=s["include"], keep=s["keep"])


def rel_dir(root: str, dp: str) -> str:
    import os
    r = os.path.relpath(dp, root)
    return "" if r == "." else r.replace(os.sep, "/")

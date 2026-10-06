"""Swift base URLs: what `{baseURL}` in a request URL template stands for.

Sources, per variable name (the last identifier of `\\(Config.baseURL)` / `baseURL.appendingPathComponent(...)`):
  - a constant with an absolute URL literal and a base-like name (base / api / server / host / endpoint ...): `static let baseURL = URL(string: "https://api.example.com/v1")!`,
    `let apiBase = "https://..."`;
  - an Info.plist key read in Swift (`Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL")`,
    `Bundle.main.infoDictionary?["API_BASE_URL"]`), the key's value from the project's Info.plist files, and a
    `$(VAR)` / `${VAR}` in it from the `.xcconfig` files (one value per configuration file: Debug / Release /
    Staging...). xcconfig writes `//` as `/$()/` (`//` starts a comment there).
One value: the request's origin and path prefix come from it (an API call, `origin_kind` api). Several (per
environment): the origin stays the placeholder with `origin_kind` env and the candidates in `attrs.base_candidates`.
"""
from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path

from cg_code_graph import presets

CONST = re.compile(r'\b(?:let|var)\s+(\w+)\s*(?::\s*[\w.?!]+\s*)?=\s*(?:URL\s*\(\s*string\s*:\s*)?"(https?://[^"\\\s]+)"')
PLIST_READ = re.compile(r'\b(?:let|var)\s+(\w+)\b[^\n]{0,120}?(?:=|\{)(?:(?!\b(?:let|var|func)\b)[\s\S]){0,400}?'
                        r'(?:forInfoDictionaryKey\s*:\s*|infoDictionary\s*\??\s*\[\s*)"([\w.-]+)"')
BASE_NAME = re.compile(r"(?i)base|api|server|host|endpoint|backend|root|domain")
VAR = re.compile(r"\$[({](\w+)(?::[^)}]*)?[)}]")
# the Swift preset's dependency / build directories plus the coverage scan's (build/, vendor/, ...)
SKIP_DIRS = presets.skip_dirs("swift") | presets.skip_dirs("common", "scan_skip_dirs")


def _walk(root: Path, pred) -> list[Path]:
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))
        out += [Path(dp) / f for f in sorted(fn) if pred(f)]
    return out


def _xcconfig(path: Path, depth: int = 0) -> dict[str, str]:
    """Build settings of an .xcconfig file, its `#include`s first (later assignments win)."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        inc = re.match(r'^\s*#include\??\s+"([^"]+)"', ln)
        if inc:
            if depth < 4:
                out.update(_xcconfig(path.parent / inc.group(1), depth + 1))
            continue
        ln = ln.replace("/$()/", "\x00")                 # the escaped `//` of URLs, before stripping comments
        ln = re.sub(r"//.*$", "", ln).replace("\x00", "//").replace("$()", "").strip()
        m = re.match(r"^([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*=\s*(.*?);?$", ln)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"')
    return out


def _plists(root: Path) -> dict[str, list[tuple[str, str]]]:
    """key -> [(string value, plist path)] of the project's Info.plist files."""
    out: dict[str, list] = {}
    for p in _walk(root, lambda f: f == "Info.plist" or f.endswith("-Info.plist")):
        try:
            with open(p, "rb") as fh:
                d = plistlib.load(fh)
        except Exception:  # noqa: BLE001  (not a plist cg can read: skipped)
            continue
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, str):
                    out.setdefault(k, []).append((v, p.relative_to(root).as_posix()))
    return out


def collect(root: Path, texts: dict[str, str]) -> dict[str, list[dict]]:
    """name -> [{value, source}] (distinct values)."""
    found: dict[str, list[dict]] = {}

    def add(name, value, source):
        lst = found.setdefault(name, [])
        if value and not any(x["value"] == value for x in lst):
            lst.append({"value": value, "source": source})
    for rel, txt in texts.items():
        for m in CONST.finditer(txt):
            if BASE_NAME.search(m.group(1)):      # `let url = URL(string: "https://httpbin.org/get")` is a request
                add(m.group(1), m.group(2).rstrip("/"), f"{rel}: {m.group(1)}")
    reads = [(m.group(1), m.group(2), rel) for rel, txt in texts.items()
             if "forInfoDictionaryKey" in txt or "infoDictionary" in txt for m in PLIST_READ.finditer(txt)]
    if reads:
        plists = _plists(root)
        configs = [(p.relative_to(root).as_posix(), _xcconfig(p)) for p in _walk(root, lambda f: f.endswith(".xcconfig"))]
        for name, key, rel in reads:
            for raw, ppath in plists.get(key, []):
                vs = VAR.findall(raw)
                if not vs:
                    if re.match(r"https?://", raw):
                        add(name, raw.rstrip("/"), f"{ppath} {key}")
                    continue
                for cpath, cfg in configs:
                    if all(v in cfg for v in vs):
                        val = VAR.sub(lambda m: cfg.get(m.group(1), ""), raw)
                        for _ in range(3):                  # values referring to other settings
                            if not VAR.search(val):
                                break
                            val = VAR.sub(lambda m: cfg.get(m.group(1), ""), val)
                        if re.match(r"https?://", val):
                            add(name, val.rstrip("/"), f"{ppath} {key} = {raw} ({cpath})")
    return found

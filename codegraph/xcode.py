"""Apple build files: the platforms an Xcode project (`*.xcodeproj/project.pbxproj`) and SwiftPM manifests
(`Package.swift`) build for, and which Xcode target each source file belongs to (#74).

`apple_build(root)` returns
  targets       {platform: where it came from}: ios / macos / tvos / watchos / visionos
  mac           None | "native" (an AppKit macOS target) | "catalyst" (the iOS app built for Mac) | "both"
  membership    {file relative to root: {platforms, targets}}: source files of Xcode targets (build phases and Xcode 16
                synchronized folders with their membership exceptions), with the platforms of the targets that
                compile them
  projects      the .pbxproj files read
"""
from __future__ import annotations

import re
from pathlib import Path

from . import presets

SKIP = presets.skip_dirs("common") | {"Pods", "Carthage", ".build", "checkouts", "DerivedData", "SourcePackages"}
SDK_PLATFORM = {"iphoneos": "ios", "macosx": "macos", "appletvos": "tvos", "watchos": "watchos", "xros": "visionos"}
SPM_PLATFORM = {"iOS": "ios", "macOS": "macos", "tvOS": "tvos", "watchOS": "watchos", "visionOS": "visionos",
                "macCatalyst": "macos", "driverKit": None}
APPLE = ("macos", "ios", "tvos", "watchos", "visionos")


# ------------------------------------------------------------------ OpenStep plist (project.pbxproj)
_TOKEN = re.compile(r'\s+|//[^\n]*|/\*.*?\*/|"((?:[^"\\]|\\.)*)"|([^\s{}()=;,"]+)|([{}()=;,])', re.S)


def parse_pbxproj(text: str):
    """The project.pbxproj dictionary (nested dicts, lists and strings); ValueError on a malformed file."""
    toks = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"pbxproj: unexpected {text[pos:pos + 20]!r}")
        pos = m.end()
        if m.group(1) is not None:
            toks.append(("s", m.group(1).encode().decode("unicode_escape", "replace") if "\\" in m.group(1) else m.group(1)))
        elif m.group(2) is not None:
            toks.append(("s", m.group(2)))
        elif m.group(3) is not None:
            toks.append(("p", m.group(3)))
    i = 0

    def value():
        nonlocal i
        kind, v = toks[i]
        i += 1
        if kind == "s":
            return v
        if v == "{":
            d = {}
            while toks[i] != ("p", "}"):
                k = value()
                if toks[i] != ("p", "="):
                    raise ValueError("pbxproj: '=' expected")
                i += 1
                d[k] = value()
                if toks[i] == ("p", ";"):
                    i += 1
            i += 1
            return d
        if v == "(":
            out = []
            while toks[i] != ("p", ")"):
                out.append(value())
                if toks[i] == ("p", ","):
                    i += 1
            i += 1
            return out
        raise ValueError(f"pbxproj: unexpected {v!r}")
    try:
        return value()
    except IndexError as e:
        raise ValueError("pbxproj: truncated") from e


def _settings_platforms(bs: dict) -> tuple[set[str], bool]:
    """Platforms of one build configuration: SUPPORTED_PLATFORMS (else SDKROOT), and SUPPORTS_MACCATALYST."""
    out = set()
    sup = bs.get("SUPPORTED_PLATFORMS")
    for tok in (sup.split() if isinstance(sup, str) else []):
        if tok in SDK_PLATFORM:
            out.add(SDK_PLATFORM[tok])
    if not out:
        sdk = bs.get("SDKROOT")
        if isinstance(sdk, str) and sdk in SDK_PLATFORM:
            out.add(SDK_PLATFORM[sdk])
    cat = str(bs.get("SUPPORTS_MACCATALYST", "")).upper() == "YES"
    return out, cat


def _read_project(pbx: Path, root: Path) -> dict | None:
    try:
        d = parse_pbxproj(pbx.read_text(encoding="utf-8", errors="replace"))
        objs = d["objects"]
        proj = objs[d["rootObject"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    base = pbx.parent.parent                      # the directory holding Foo.xcodeproj
    if proj.get("projectDirPath"):
        base = base / proj["projectDirPath"]

    def configs(lst_id) -> list[dict]:
        lst = objs.get(lst_id) or {}
        return [(objs.get(c) or {}).get("buildSettings") or {} for c in lst.get("buildConfigurations") or ()]
    proj_bs = configs(proj.get("buildConfigurationList"))
    # file paths: walk the group tree from the main group
    paths: dict[str, Path] = {}

    def walk(gid, parent: Path):
        g = objs.get(gid) or {}
        st = g.get("sourceTree", "<group>")
        p = g.get("path")
        here = parent if not p else (base / p if st == "SOURCE_ROOT" else Path(p) if st == "<absolute>" else parent / p)
        if st not in ("<group>", "SOURCE_ROOT", "<absolute>"):
            return
        paths[gid] = here
        for c in g.get("children") or ():
            walk(c, here)
    walk(proj.get("mainGroup"), base)
    targets = []
    for tid in proj.get("targets") or ():
        t = objs.get(tid) or {}
        if t.get("isa") != "PBXNativeTarget":
            continue
        plats, cat = set(), False
        for bs in configs(t.get("buildConfigurationList")) or [{}]:
            merged = {**(proj_bs[0] if proj_bs else {}), **bs}
            p, c = _settings_platforms(merged)
            plats |= p
            cat |= c
        files = set()
        for ph in t.get("buildPhases") or ():
            phase = objs.get(ph) or {}
            if phase.get("isa") != "PBXSourcesBuildPhase":
                continue
            for bf in phase.get("files") or ():
                ref = (objs.get(bf) or {}).get("fileRef")
                if ref in paths:
                    files.add(paths[ref])
        targets.append({"id": tid, "name": t.get("name") or tid, "platforms": plats, "catalyst": cat, "files": files,
                        "groups": set(t.get("fileSystemSynchronizedGroups") or ())})
    # Xcode 16 synchronized folders: every file under the folder belongs to the targets that list the folder; a
    # membership exception for such a target takes the file out, one for another target adds it there
    by_id = {t["id"]: t for t in targets}
    for gid, g in objs.items():
        if not isinstance(g, dict) or g.get("isa") != "PBXFileSystemSynchronizedRootGroup" or gid not in paths:
            continue
        folder = paths[gid]
        owners = [t for t in targets if gid in t["groups"]]
        excl: dict[str, set] = {}
        for ex in g.get("exceptions") or ():
            e = objs.get(ex) or {}
            if e.get("isa") == "PBXFileSystemSynchronizedBuildFileExceptionSet" and e.get("target") in by_id:
                excl.setdefault(e["target"], set()).update(e.get("membershipExceptions") or ())
        try:
            rel_files = [f.relative_to(folder).as_posix() for f in folder.rglob("*")
                         if f.is_file() and f.suffix in (".swift", ".m", ".mm", ".c", ".cpp")]
        except OSError:
            rel_files = []
        for t in targets:
            ex = excl.get(t["id"], set())
            if t in owners:
                t["files"] |= {folder / r for r in rel_files if r not in ex}
            elif ex:
                t["files"] |= {folder / r for r in rel_files if r in ex}
    try:
        where = pbx.parent.relative_to(root).as_posix()
    except ValueError:
        where = pbx.parent.as_posix()
    return {"where": where, "targets": targets}


def _projects(root: Path) -> list[Path]:
    out = []
    for pat in ("*.xcodeproj/project.pbxproj", "*/*.xcodeproj/project.pbxproj", "*/*/*.xcodeproj/project.pbxproj"):
        for p in root.glob(pat):
            if SKIP.isdisjoint(p.relative_to(root).parts[:-2]):
                out.append(p)
    return sorted(out)


def _manifests(root: Path, depth: int = 4) -> list[Path]:
    out = []
    for lvl in range(depth):
        for p in root.glob("/".join(["*"] * lvl + ["Package.swift"])):
            if SKIP.isdisjoint(p.relative_to(root).parts[:-1]):
                out.append(p)
    return out


def package_platforms(pk: Path) -> list[str]:
    """`platforms: [.iOS(.v15), .macOS("12.0"), .visionOS(.v1)]` of one Package.swift, as SwiftPM names."""
    try:
        txt = pk.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    m = re.search(r"\bplatforms\s*:\s*\[([^\]]*)\]", txt)
    if not m:
        return []
    names = re.findall(r"\.(\w+)\s*\(", m.group(1)) + re.findall(r"\.(\w+)\s*(?=[,\]]|$)", m.group(1))
    return [n for n in dict.fromkeys(names) if n in SPM_PLATFORM]


def apple_build(root: Path | str) -> dict:
    root = Path(root)
    out = {"targets": {}, "mac": None, "membership": {}, "projects": []}
    native = catalyst = False
    for pbx in _projects(root):
        pr = _read_project(pbx, root)
        if pr is None:
            continue
        out["projects"].append(pr["where"])
        for t in pr["targets"]:
            for p in sorted(t["platforms"], key=APPLE.index):
                out["targets"].setdefault(p, f"{pr['where']} target {t['name']}: SDKROOT / SUPPORTED_PLATFORMS")
            if t["catalyst"]:
                out["targets"].setdefault("macos", f"{pr['where']} target {t['name']}: SUPPORTS_MACCATALYST")
            native |= "macos" in t["platforms"]
            catalyst |= t["catalyst"]
            plats = set(t["platforms"]) | ({"macos"} if t["catalyst"] else set())
            for f in t["files"]:
                try:
                    rel = f.resolve().relative_to(root.resolve()).as_posix()
                except (ValueError, OSError):
                    continue
                m = out["membership"].setdefault(rel, {"platforms": set(), "targets": []})
                m["platforms"] |= plats
                if t["name"] not in m["targets"]:
                    m["targets"].append(t["name"])
    # SwiftPM: the root manifest (one level down for a repo holding the package in a folder); local packages only
    # when no Xcode project or root manifest names a platform
    pks = [p for p in [root / "Package.swift", *root.glob("*/Package.swift")] if p.is_file()]
    for pass_ in (pks, [] if out["targets"] or any(package_platforms(p) for p in pks) else _manifests(root)):
        for pk in pass_:
            names = package_platforms(pk)
            where = pk.relative_to(root).as_posix()
            for n in names:
                p = SPM_PLATFORM[n]
                if p:
                    out["targets"].setdefault(p, f"{where} platforms: .{n}")
            native |= "macOS" in names
            catalyst |= "macCatalyst" in names and "macOS" not in names
    if "macos" in out["targets"]:
        out["mac"] = "both" if native and catalyst else "catalyst" if catalyst else "native"
    for m in out["membership"].values():
        m["platforms"] = sorted(m["platforms"], key=APPLE.index)
    return out

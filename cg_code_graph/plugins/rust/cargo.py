"""Cargo workspace discovery: packages, targets, features (no build, no code execution).

Uses `cargo metadata --no-deps --offline` when cargo is installed (handles workspace inheritance, globs,
auto-discovered targets); falls back to reading Cargo.toml files with tomllib and Cargo's auto-discovery rules.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LIB_KINDS = {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"}
from ... import presets
from ...core.env import get as cg_env

# Cargo build output, vendored sources, Cargo home (cg_code_graph/presets/rust.yaml; .cg.yaml skip_dirs adjusts them)
SKIP_DIRS = presets.skip_dirs("rust")


@dataclass
class Target:
    kind: str            # lib | bin | test | bench | example | build
    name: str
    src: str             # repo-relative path of the crate root file
    crate: str           # crate path used in node ids, e.g. kv_core, kv, kv_core[test:roundtrip]
    crate_types: list = field(default_factory=list)
    required_features: list = field(default_factory=list)


@dataclass
class Package:
    name: str
    dir: str             # repo-relative directory ('' = root)
    manifest: str
    version: str = ""
    features: dict = field(default_factory=dict)
    targets: list = field(default_factory=list)
    deps: list = field(default_factory=list)   # dependency package names (workspace-internal ones are linked)

    @property
    def crate_name(self) -> str:
        return self.name.replace("-", "_")


def _norm(s: str) -> str:
    return s.replace("-", "_")


def _rel(root: Path, p: str | Path) -> str:
    try:
        return Path(p).resolve().relative_to(root).as_posix()
    except ValueError:
        return Path(p).as_posix()


def _crate_for(pkg: Package, kind: str, name: str, lib_names: set) -> str:
    pc = pkg.crate_name
    if kind == "lib":
        return _norm(name)
    if kind == "bin":
        n = _norm(name)
        return f"{n}[bin]" if n in lib_names else n
    if kind == "build":
        return f"{pc}[build]"
    return f"{pc}[{kind}:{name}]"


def _kind_of(kinds: list[str]) -> str:
    k = kinds[0] if kinds else "lib"
    if k in LIB_KINDS or any(x in LIB_KINDS for x in kinds):
        return "lib"
    return {"custom-build": "build"}.get(k, k)


def from_metadata(root: Path) -> list[Package] | None:
    cargo = cg_env("CARGO") or shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo")
    if not Path(cargo).exists() and not shutil.which(cargo):
        return None
    try:
        r = subprocess.run([cargo, "metadata", "--no-deps", "--format-version", "1", "--offline"], cwd=root,
                           capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    md = json.loads(r.stdout)
    members = set(md.get("workspace_members") or [])
    pkgs = []
    for p in md["packages"]:
        if members and p["id"] not in members:
            continue
        pdir = Path(p["manifest_path"]).parent
        pkg = Package(p["name"], _rel(root, pdir) if pdir != root else "", _rel(root, p["manifest_path"]), p.get("version", ""),
                      {k: list(v) for k, v in (p.get("features") or {}).items()},
                      deps=[d["name"] for d in p.get("dependencies") or []])
        lib_names = {_norm(t["name"]) for t in p["targets"] if _kind_of(t["kind"]) == "lib"}
        for t in p["targets"]:
            k = _kind_of(t["kind"])
            pkg.targets.append(Target(k, t["name"], _rel(root, t["src_path"]), _crate_for(pkg, k, t["name"], lib_names),
                                      list(t.get("crate_types") or []), list(t.get("required-features") or [])))
        pkgs.append(pkg)
    return pkgs


def _members(root: Path, ws: dict) -> list[Path]:
    out = []
    excl = {(root / e).resolve() for e in ws.get("exclude", [])}
    for m in ws.get("members", []):
        for d in sorted(glob.glob(str(root / m))):
            dp = Path(d).resolve()
            if (dp / "Cargo.toml").exists() and dp not in excl:
                out.append(dp)
    return out


def _inherit(v, ws_pkg: dict, key: str):
    if isinstance(v, dict) and v.get("workspace"):
        return ws_pkg.get(key)
    return v


def from_toml(root: Path) -> list[Package]:
    """Fallback without cargo: Cargo.toml + auto-discovery (src/lib.rs, src/main.rs, src/bin/*, tests/, benches/,
    examples/, build.rs)."""
    try:
        top = tomllib.loads((root / "Cargo.toml").read_text())
    except Exception:
        return []
    ws = top.get("workspace") or {}
    ws_pkg = ws.get("package") or {}
    dirs = []
    if "package" in top:
        dirs.append(root)
    dirs += [d for d in _members(root, ws) if d != root]
    return _packages(root, dirs, ws_pkg)


def tauri_crates(root: Path, depth: int = 4) -> list[Path]:
    """Without a root Cargo.toml: the Rust core of Tauri apps, <app>/src-tauri/Cargo.toml (a JS project root)."""
    out = []
    for dp, dn, fn in os.walk(root):
        d = Path(dp)
        rel = d.relative_to(root)
        dn[:] = sorted(x for x in dn if not x.startswith(".") and x not in ("node_modules", "target", "dist", "build"))
        if d.name == "src-tauri" and "Cargo.toml" in fn:
            out.append(d)
            dn[:] = []
        if len(rel.parts) >= depth:
            dn[:] = []
    return out


def _packages(root: Path, dirs: list[Path], ws_pkg: dict) -> list[Package]:
    pkgs = []
    for d in dirs:
        try:
            t = tomllib.loads((d / "Cargo.toml").read_text())
        except Exception:
            continue
        p = t.get("package") or {}
        name = p.get("name") or d.name
        pkg = Package(name, _rel(root, d) if d != root else "", _rel(root, d / "Cargo.toml"),
                      str(_inherit(p.get("version", ""), ws_pkg, "version") or ""),
                      {k: list(v) for k, v in (t.get("features") or {}).items()},
                      deps=list((t.get("dependencies") or {}).keys()))
        tl = []
        lib = t.get("lib") or {}
        lib_path = lib.get("path") or ("src/lib.rs" if (d / "src/lib.rs").exists() else None)
        if lib_path:
            tl.append(("lib", lib.get("name") or _norm(name), lib_path, lib.get("crate-type") or ["lib"], []))
        seen_bins = set()
        for b in t.get("bin") or []:
            path = b.get("path") or (f"src/bin/{b['name']}.rs" if (d / f"src/bin/{b['name']}.rs").exists() else
                                     f"src/bin/{b['name']}/main.rs" if (d / f"src/bin/{b['name']}/main.rs").exists() else "src/main.rs")
            tl.append(("bin", b["name"], path, ["bin"], b.get("required-features") or []))
            seen_bins.add(path)
        if p.get("autobins", True):
            if (d / "src/main.rs").exists() and "src/main.rs" not in seen_bins:
                tl.append(("bin", name, "src/main.rs", ["bin"], []))
            for f in sorted((d / "src/bin").glob("*.rs")) if (d / "src/bin").is_dir() else []:
                if f"src/bin/{f.name}" not in seen_bins:
                    tl.append(("bin", f.stem, f"src/bin/{f.name}", ["bin"], []))
            for f in sorted((d / "src/bin").glob("*/main.rs")) if (d / "src/bin").is_dir() else []:
                rp = f.relative_to(d).as_posix()
                if rp not in seen_bins:
                    tl.append(("bin", f.parent.name, rp, ["bin"], []))
        for kind, sect, auto in (("test", "test", "tests"), ("bench", "bench", "benches"), ("example", "example", "examples")):
            listed = set()
            for x in t.get(sect) or []:
                path = x.get("path") or f"{auto}/{x['name']}.rs"
                tl.append((kind, x["name"], path, ["bin"], x.get("required-features") or []))
                listed.add(path)
            if p.get(f"auto{auto}", True) and (d / auto).is_dir():
                for f in sorted((d / auto).glob("*.rs")):
                    if f"{auto}/{f.name}" not in listed:
                        tl.append((kind, f.stem, f"{auto}/{f.name}", ["bin"], []))
                for f in sorted((d / auto).glob("*/main.rs")):
                    rp = f.relative_to(d).as_posix()
                    if rp not in listed:
                        tl.append((kind, f.parent.name, rp, ["bin"], []))
        build = p.get("build")
        if build is None and (d / "build.rs").exists():
            build = "build.rs"
        if isinstance(build, str):
            tl.append(("build", "build-script-build", build, ["bin"], []))
        lib_names = {_norm(n) for k, n, *_ in tl if k == "lib"}
        for kind, tname, path, ctypes, req in tl:
            pkg.targets.append(Target(kind, tname, _rel(root, d / path), _crate_for(pkg, kind, tname, lib_names), ctypes, req))
        pkgs.append(pkg)
    return pkgs


def discover(root: Path) -> tuple[list[Package], str]:
    if not (root / "Cargo.toml").exists():
        tc = tauri_crates(root)
        if tc:
            return _packages(root, tc, {}), "src-tauri/Cargo.toml"
    if cg_env("NO_CARGO") != "1":
        md = from_metadata(root)
        if md is not None:
            return md, "cargo metadata"
    return from_toml(root), "Cargo.toml (fallback)"

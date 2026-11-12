"""Language/framework detection from project files (no code execution)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..presets import skip_dirs

MARKERS = {
    # language: files/globs that indicate it
    "php": ["composer.json", "artisan"],
    "typescript": ["tsconfig.json"],
    "javascript": ["package.json"],
    "go": ["go.mod"],
    "rust": ["Cargo.toml"],
    "c_cpp": ["CMakeLists.txt", "compile_commands.json", "meson.build", "Makefile"],
    "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "manage.py", "Pipfile"],
    "dart": ["pubspec.yaml"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "kotlin": ["settings.gradle.kts", "build.gradle.kts"],
    "swift": ["Package.swift"],
}


def _pkg_deps(root: Path) -> dict:
    p = root / "package.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text())
    except Exception:
        return {}
    return {**(d.get("dependencies") or {}), **(d.get("devDependencies") or {})}


def detect(root: Path) -> dict:
    root = Path(root)
    langs = {lang: [m for m in marks if (root / m).exists()] for lang, marks in MARKERS.items()}
    langs = {k: v for k, v in langs.items() if v}
    fw = {}
    cj = {}
    if (root / "composer.json").exists():
        try:
            cj = json.loads((root / "composer.json").read_text())
        except Exception:
            cj = {}
    req = {**(cj.get("require") or {}), **(cj.get("require-dev") or {})}
    if "laravel/framework" in req or (root / "artisan").exists():
        fw["laravel"] = {"version": req.get("laravel/framework")}
    if "filament/filament" in req:
        fw["filament"] = {"version": req.get("filament/filament")}
    deps = _pkg_deps(root)
    if "nuxt" in deps or any((root / n).exists() for n in ("nuxt.config.ts", "nuxt.config.js")):
        fw["nuxt"] = {"version": deps.get("nuxt")}
    if "astro" in deps or any((root / n).exists() for n in (
            "astro.config.mjs", "astro.config.js", "astro.config.ts", "astro.config.mts", "astro.config.cjs")):
        fw["astro"] = {"version": deps.get("astro")}
    if "vue" in deps:
        fw["vue"] = {"version": deps.get("vue")}
    from ..plugins.ts.react_router import is_react_router
    if is_react_router(root):
        ver = deps.get("react-router") or deps.get("react-router-dom") or deps.get("@remix-run/react")
        fw["react-router"] = {"version": ver} if ver else {}
    from ..plugins.tsweb.common import SERVER_DEPS   # TS / JS servers (NestJS, Next.js, Express / Koa / Fastify / Hono ...)
    for name, pkgs in SERVER_DEPS.items():
        hit = [d for d in pkgs if d in deps]
        if hit:
            key = {"next": "nextjs"}.get(name, name)           # framework plugin names
            fw[key] = {"version": deps.get(hit[0])} if name != "express" else {"via": hit, "version": deps.get(hit[0])}
    py_req = ""
    for f in ("requirements.txt", "pyproject.toml", "setup.cfg", "Pipfile", "requirements/base.txt"):
        if (root / f).exists():
            py_req += (root / f).read_text(errors="replace").lower()
    if "django" in py_req or (root / "manage.py").exists():
        fw["django"] = {}
        for extra in ("django-ninja", "djangorestframework", "channels", "celery"):
            if extra in py_req:
                fw[extra] = {}
    if (root / "pubspec.yaml").exists():
        ps = (root / "pubspec.yaml").read_text(errors="replace")
        if "flutter:" in ps:
            fw["flutter"] = {}
        for extra in ("flutter_bloc", "dio", "go_router", "riverpod", "provider", "json_serializable", "freezed", "retrofit", "chopper"):
            if re.search(rf"^\s+{extra}:", ps, re.M):
                fw[extra] = {}
    if "typescript" in deps:
        langs.setdefault("typescript", ["package.json:typescript"])
    if _spring_build(root):
        fw["spring"] = {}
    _file_languages(root, langs, fw)
    out = {"languages": langs, "frameworks": fw}
    if "swift" in langs:
        out["modes"] = {"swift": _swift_mode(root)}
    return out


def _spring_build(root: Path) -> bool:
    """Spring Boot Gradle plugin or a ``spring-boot-starter-*`` dependency in Gradle / Maven files.

    The root build file and one module level (``app/build.gradle.kts``). Deeper trees stay out so a
    monorepo's example apps do not mark the repository root.
    """
    names = {"build.gradle", "build.gradle.kts", "pom.xml"}
    rx = re.compile(r"org\.springframework\.boot|spring-boot-starter(?:-[\w.-]+)?")
    skip = skip_dirs("common") | skip_dirs("common", "scan_skip_dirs")
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        depth = 0 if rel == "." else len(Path(rel).parts)
        if depth > 1:
            dn[:] = []
            continue
        dn[:] = [d for d in dn if d not in skip and not d.startswith(".")]
        for name in fn:
            if name not in names:
                continue
            try:
                text = (Path(dp) / name).read_text(errors="replace")[:200_000]
            except OSError:
                continue
            if rx.search(text):
                return True
    return False


_IMPORT_FW = {"SwiftUI": "swiftui", "UIKit": "uikit", "Vapor": "vapor"}
_IMPORT_RE = re.compile(r"(?m)^[ \t]*import[ \t]+(SwiftUI|UIKit|Vapor)\b")
_VAPOR_RE = re.compile(r"vapor/vapor|name:\s*\"Vapor\"")
_PBX_FW = (("SwiftUI.framework", "swiftui"), ("UIKit.framework", "uikit"))


def _file_languages(root: Path, langs: dict, fw: dict) -> None:
    """Languages the file-walking plugins index, plus Swift manifests those plugins do not name.

    Marker files at the root stay as they are. Swift, Kotlin and Java plugins decide presence by walking
    sources with their own skip lists, so `cg detect` lists a language `cg index` will index. An Xcode
    project or a nested Package.swift is evidence for Swift even when the root has neither marker.
    """
    from ..config import ConfigError, load as load_config
    from .fsutil import keep_file
    from .paths import rel_dir, rules
    from .plugin import Project
    from ..plugins.java.plugin import JavaPlugin
    from ..plugins.kotlin.plugin import KotlinPlugin
    from ..plugins.swift.plugin import SwiftPlugin

    project = Project(root=root, name=root.name)
    try:
        project.options["config"] = load_config(root)
    except ConfigError:
        project.options["config"] = {}
    swift_rules = rules(project, "swift")
    packages: list[str] = []
    projects: list[str] = []
    swift_sources = False
    found_fw: set[str] = set()
    for dp, dn, fn in os.walk(root):
        rd = rel_dir(str(root), dp)
        for d in dn:
            if d.endswith((".xcodeproj", ".xcworkspace")):
                rel = f"{rd}/{d}" if rd else d
                if not swift_rules.excluded(rel + "/"):
                    projects.append(rel)
        dn[:] = swift_rules.prune(rd, dn, dot=True)
        for name in fn:
            rel = f"{rd}/{name}" if rd else name
            full = os.path.join(dp, name)
            if name == "Package.swift" or name.endswith(".swift"):
                if not keep_file(full) or swift_rules.excluded(rel):
                    continue
            if name == "Package.swift":
                packages.append(rel)
                found_fw |= _read_frameworks(full, package=True)
            elif name.endswith(".swift"):
                swift_sources = True
                found_fw |= _read_frameworks(full, package=False)
            elif name == "project.pbxproj":
                found_fw |= _pbx_frameworks(full)
    if packages or swift_sources:
        ev = list(langs.get("swift") or [])
        for rel in sorted(set(packages)):
            if rel not in ev:
                ev.append(rel)
        for rel in sorted(set(projects)):
            if rel not in ev:
                ev.append(rel)
        if swift_sources and "*.swift" not in ev:
            ev.append("*.swift")
        langs["swift"] = ev
        for name in sorted(found_fw):
            fw.setdefault(name, {})
    else:
        # a Swift plugin can still see sources the walk above skipped (a .cg.yaml include). Trust the plugin.
        plug = SwiftPlugin()
        if plug.detect(project):
            langs.setdefault("swift", []).append("*.swift")
    for lang, plug, token in (("kotlin", KotlinPlugin(), "*.kt"), ("java", JavaPlugin(), "*.java")):
        if lang in langs:
            continue
        if plug.detect(project):
            langs[lang] = [token]


def _read_frameworks(path: str, package: bool) -> set[str]:
    try:
        text = Path(path).read_text(errors="replace")[:200_000]
    except OSError:
        return set()
    found = set()
    if package and _VAPOR_RE.search(text):
        found.add("vapor")
    for match in _IMPORT_RE.finditer(text):
        name = _IMPORT_FW.get(match.group(1))
        if name:
            found.add(name)
    return found


def _pbx_frameworks(path: str) -> set[str]:
    try:
        text = Path(path).read_text(errors="replace")[:2_000_000]
    except OSError:
        return set()
    return {name for needle, name in _PBX_FW if needle in text}


def _swift_mode(root: Path) -> dict:
    """Whether a later index can resolve Swift calls, or only the heuristic layer is available.

    This does not build the package. Exact mode stays unavailable until a toolchain and an index
    store are both configured (see docs/swift.md).
    """
    from .env import get as cg_env
    from ..plugins.swift.exact import find_swift

    if cg_env("SWIFT_INDEX_STORE"):
        return {"indexer": "heuristic", "exact": "available",
                "reason": "CG_SWIFT_INDEX_STORE is set; the index reads it when libIndexStore is present"}
    swift = find_swift()
    if not swift:
        return {"indexer": "heuristic", "exact": "unavailable",
                "reason": "no Swift toolchain (swift not on PATH; CG_SWIFT)"}
    if not (root / "Package.swift").is_file():
        return {"indexer": "heuristic", "exact": "unavailable",
                "reason": "no Package.swift at the project root (Xcode projects: set CG_SWIFT_INDEX_STORE)"}
    if cg_env("SWIFT_INDEX") != "1":
        return {"indexer": "heuristic", "exact": "unavailable",
                "reason": "Swift toolchain found; set CG_SWIFT_INDEX=1 to build an index store"}
    return {"indexer": "exact", "exact": "available",
            "reason": "CG_SWIFT_INDEX=1 builds an index store on the next index"}

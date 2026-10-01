"""Language/framework detection from project files (no code execution)."""
from __future__ import annotations

import json
from pathlib import Path

MARKERS = {
    # language: files/globs that indicate it
    "php": ["composer.json", "artisan"],
    "typescript": ["tsconfig.json"],
    "javascript": ["package.json"],
    "go": ["go.mod"],
    "rust": ["Cargo.toml"],
    "c_cpp": ["CMakeLists.txt", "compile_commands.json", "meson.build", "Makefile"],
    "python": ["pyproject.toml", "setup.py", "requirements.txt"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
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
    if "vue" in deps:
        fw["vue"] = {"version": deps.get("vue")}
    if "typescript" in deps:
        langs.setdefault("typescript", ["package.json:typescript"])
    return {"languages": langs, "frameworks": fw}

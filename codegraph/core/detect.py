"""Language/framework detection from project files (no code execution)."""
from __future__ import annotations

import json
import re
from pathlib import Path

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
    return {"languages": langs, "frameworks": fw}

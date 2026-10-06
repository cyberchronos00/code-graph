"""Built-in presets: versioned data files with the per-language and per-framework knowledge cg applies.

    common.yaml          every project: name-based auth / secret token patterns, directories no walk descends into
    <language>.yaml      php, typescript, python, dart, rust, c_cpp: skip directories / paths of that ecosystem
    <framework>.yaml     laravel, django, djangorestframework, django-ninja, nest, next, express, nuxt: guard names
                         (auth / not_auth / secret), plan defaults, framework-specific skip directories

Detection picks them: `select()` returns the applied names in merge order (common -> languages -> frameworks); the
project's `.cg.yaml` and command-line flags come after them. The index records the applied names in its stats
(`stats.presets`), and `cg coverage`, `cg config show` and MCP `coverage` show them."""
from __future__ import annotations

import functools
import re
from pathlib import Path

PRESET_DIR = Path(__file__).parent
LANGUAGE_ORDER = ("php", "typescript", "python", "dart", "rust", "c_cpp", "kotlin", "java", "swift")
# framework spellings accepted in .cg.yaml `frameworks.add/remove` -> framework plugin / preset name
FRAMEWORK_ALIASES = {"nestjs": "nest", "next": "nextjs", "next.js": "nextjs", "koa": "express", "fastify": "express",
                     "hono": "express", "starlette": "fastapi", "drf": "djangorestframework", "ninja": "django-ninja"}


class PresetError(ValueError):
    pass


@functools.lru_cache(maxsize=None)
def load(name: str) -> dict:
    p = PRESET_DIR / f"{name}.yaml"
    if not p.is_file():
        raise PresetError(f"no built-in preset '{name}' (available: {', '.join(available())})")
    import yaml
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if d.get("name") != name:
        raise PresetError(f"preset file {p.name}: name must be '{name}'")
    return d


def available() -> list[str]:
    return sorted(p.stem for p in PRESET_DIR.glob("*.yaml"))


def has(name: str) -> bool:
    return (PRESET_DIR / f"{name}.yaml").is_file()


def skip_dirs(preset: str = "common", *keys: str) -> frozenset[str]:
    """Directory names a walk skips: common.skip_dirs plus `keys` of `preset` (default its own skip_dirs)."""
    out = set(load("common").get("skip_dirs") or [])
    for k in keys or ("skip_dirs",):
        out |= set(load(preset).get(k) or [])
    return frozenset(out)


def values(preset: str, *path: str, default=None):
    """A nested value of one preset file, e.g. values("laravel", "plans", "short_prefixes")."""
    d = load(preset)
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def select(languages, frameworks) -> list[str]:
    """Applied preset names in merge order for the active language plugins and frameworks."""
    out = ["common"]
    out += [lang for lang in LANGUAGE_ORDER if lang in set(languages) and has(lang)]
    for f in frameworks:
        f = FRAMEWORK_ALIASES.get(f, f)
        if f not in out and has(f) and load(f).get("kind") == "framework":
            out.append(f)
    return out


def frameworks() -> list[str]:
    return [n for n in available() if load(n).get("kind") == "framework"]


def name_tokens(name: str) -> str:
    """'ApiKeyGuard' -> 'api_key_guard', 'auth:api' -> 'auth_api', 'password.confirm' -> 'password_confirm'."""
    return "_".join(t.lower() for t in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name or ""))


class NameSet:
    """Guard names from presets. lookup() matches the guard's own name, so `auth` matches `auth:api`, `can` matches
    `can:update,post`, `AuthGuard` matches `AuthGuard('jwt')` and `IsAuthenticated` matches
    `rest_framework.permissions.IsAuthenticated`, while a project's own `MaintenanceAuthGuard` is left to the name
    pattern. contains() matches the preset name anywhere in the guard's word tokens (for names that never count as
    auth, such as `ThrottlerGuard` in `CustomThrottlerGuard`). Both name the preset the entry came from."""

    def __init__(self, entries: list[tuple[str, str, str]]):   # (name, preset, source)
        self.by_tok: dict[str, tuple[str, str, str]] = {}
        for name, preset, source in entries:
            self.by_tok.setdefault(name_tokens(name), (name, preset, source))
        toks = sorted((t for t in self.by_tok if t), key=len, reverse=True)
        self.rx = re.compile(r"(?:^|_)(" + "|".join(map(re.escape, toks)) + r")(?=_|$)") if toks else None

    def lookup(self, name: str) -> tuple[str, str, str] | None:
        for c in own_names(name):
            hit = self.by_tok.get(name_tokens(c))
            if hit:
                return hit
        return None

    def contains(self, name: str) -> tuple[str, str, str] | None:
        if not self.rx:
            return None
        m = self.rx.search(name_tokens(name))
        return self.by_tok[m.group(1)] if m else None

    def __len__(self):
        return len(self.by_tok)


def own_names(name: str) -> list[str]:
    """The names a guard is known by, without its namespace, parameters or call arguments:
    'auth:sanctum' -> auth, 'Illuminate\\Auth\\Middleware\\Authenticate' -> Authenticate, "AuthGuard('jwt')" -> AuthGuard,
    'App\\Http\\Middleware\\Authorize::handle' -> Authorize, 'rest_framework.permissions.IsAuthenticated' -> both the
    dotted name and IsAuthenticated, 'passport.authenticate' stays whole."""
    n = (name or "").strip().lstrip("@")
    n = re.sub(r"::\w+$", "", n).split("(", 1)[0]
    if "::" not in n:
        n = n.split(":", 1)[0]
    n = re.split(r"[\\/]", n)[-1].strip()
    out = [n] if n else []
    if "." in n:
        last = n.rsplit(".", 1)[1]
        if last[:1].isupper():
            out.append(last)
    return out


def guard_names(applied: list[str], section: str, key: str = "guards") -> NameSet:
    """auth.guards / auth.not_auth / secret.guards of the applied presets, in merge order."""
    entries = []
    for p in applied:
        if not has(p):
            continue
        for g in values(p, section, key, default=[]) or []:
            if isinstance(g, dict) and g.get("name"):
                entries.append((str(g["name"]), p, str(g.get("source") or "")))
            elif isinstance(g, str):
                entries.append((g, p, ""))
    return NameSet(entries)

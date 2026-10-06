"""Values of configured base URLs, so client paths built on them can be matched to backend routes.

The extractor turns runtime config / env reads into placeholders: `useRuntimeConfig().public.apiBase` ->
`{runtimeConfig.apiBase}`, `process.env.API_URL` / `import.meta.env.VITE_API_URL` -> `{env.API_URL}`. Here
their values are looked up, in this order:
  1. env files that configure a real run (.env, .env.local, .env.development, .env.development.local); for
     runtime config also Nuxt's override variable NUXT_PUBLIC_<KEY> / NUXT_<KEY>;
  2. the default written in code: nuxt.config runtimeConfig (`apiBase: process.env.API_URL || 'http://...'`) or
     an in-code `process.env.X || '...'` (recorded by the extractor);
  3. example env files (.env.example, .env.sample, .env.dist).
Only the path part matters for matching (`http://localhost:8000/api/v1` -> `/api/v1`)."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

REAL_ENV = (".env", ".env.local", ".env.development", ".env.development.local", ".env.dev")
EXAMPLE_ENV = (".env.example", ".env.sample", ".env.dist", ".env.template", ".env.local.example", ".env.development.example")
CONFIG_FILES = ("nuxt.config.ts", "nuxt.config.js", "nuxt.config.mjs", "app.config.ts")
STR = r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|`([^`$]*)`)"""
API_NAME = re.compile(r"api|backend|server|base_?url|baseurl|endpoint", re.I)


def parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not m:
            continue
        v = m.group(2)
        if v[:1] in "'\"" and v[-1:] == v[:1] and len(v) > 1:
            v = v[1:-1]
        else:
            v = re.sub(r"\s+#.*$", "", v)
        out[m.group(1)] = v
    return out


def _block(text: str, start: int) -> str | None:
    """Text of the balanced {...} block whose `{` is at or after start."""
    i = text.find("{", start)
    if i < 0:
        return None
    depth, j, q = 0, i, None
    while j < len(text):
        c = text[j]
        if q:
            if c == "\\":
                j += 2
                continue
            if c == q:
                q = None
        elif c in "'\"`":
            q = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j]
        j += 1
    return None


def _entries(block: str) -> list[tuple[str, str]]:
    """Top-level `key: expr` entries of an object literal body."""
    out, depth, q, cur = [], 0, None, ""
    for c in block + ",":
        if q:
            cur += c
            if c == q:
                q = None
            continue
        if c in "'\"`":
            q = c
        elif c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        if c == "," and depth == 0:
            m = re.match(r"\s*(?://[^\n]*\n\s*)*['\"]?([A-Za-z_$][\w$-]*)['\"]?\s*:\s*(.*)$", cur, re.S)
            if m:
                out.append((m.group(1), m.group(2).strip()))
            cur = ""
            continue
        cur += c
    return out


def runtime_config_defaults(root: Path) -> dict:
    """{key: (env names read, literal default, file:line)} for nuxt.config runtimeConfig (public and private)."""
    out = {}
    for name in CONFIG_FILES:
        f = root / name
        if not f.exists():
            continue
        text = f.read_text(errors="replace")
        m = re.search(r"\bruntimeConfig\s*:", text)
        if not m:
            continue
        body = _block(text, m.end())
        if body is None:
            continue
        body_at = text.find("{", m.end()) + 1
        for key, expr in _entries(body):
            if key == "public":
                pm = re.search(r"\bpublic\s*:", body)
                sub = _block(body, pm.end()) if pm else None
                items = _entries(sub) if sub else []
            else:
                items = [(key, expr)]
            for k, e in items:
                envs = re.findall(r"(?:process\.env|import\.meta\.env)\.([A-Za-z_][A-Za-z0-9_]*)", e)
                envs += re.findall(r"(?:process\.env|import\.meta\.env)\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]", e)
                found = [m.group(1) or m.group(2) or m.group(3) or "" for m in re.finditer(STR, e)]
                lits = [x for x in found if x and not x.startswith(("NUXT_", "VITE_"))]
                if not lits and "" in found:
                    lits = [""]      # `process.env.X || ''`: unset unless the env sets it
                pos = body.find(f"{k}") + body_at
                out.setdefault(k, (envs, lits[0] if lits else None, f"{name}:{text.count(chr(10), 0, pos) + 1}"))
    return out


def snake_upper(k: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", k).upper()


class ConfigValues:
    def __init__(self, root: Path, code_defaults: dict | None = None):
        root = Path(root)
        self.real, self.example = {}, {}
        for names, dst in ((REAL_ENV, self.real), (EXAMPLE_ENV, self.example)):
            for n in names:
                f = root / n
                if f.is_file():
                    for k, v in parse_env(f.read_text(errors="replace")).items():
                        dst.setdefault(k, (v, n))
        self.rc = runtime_config_defaults(root)
        self.code_defaults = code_defaults or {}

    def lookup(self, ph: str) -> tuple[str, str] | None:
        """'runtimeConfig.apiBase' | 'env.API_URL' -> (value, where from) or None."""
        kind, _, key = ph.partition(".")
        if kind == "env":
            for src in (self.real,):
                if key in src:
                    return src[key][0], src[key][1]
            if ph in self.code_defaults:
                return self.code_defaults[ph], "default in code"
            if key in self.example:
                return self.example[key][0], self.example[key][1]
            return None
        if kind != "runtimeConfig":
            return None
        envs, lit, at = self.rc.get(key, ([], None, None))
        overrides = [f"NUXT_PUBLIC_{snake_upper(key)}", f"NUXT_{snake_upper(key)}"] + envs
        for e in overrides:
            if e in self.real:
                return self.real[e][0], self.real[e][1]
        if lit is not None:
            return lit, at
        if ph in self.code_defaults:
            return self.code_defaults[ph], "default in code"
        for e in overrides:
            if e in self.example:
                return self.example[e][0], self.example[e][1]
        return None


def base_path(value: str) -> str | None:
    """'http://localhost:8000/api/v1/' -> '/api/v1'; '/api' -> '/api'; 'http://host' -> ''; anything else -> None."""
    v = (value or "").strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", v):
        p = urlsplit(v).path
    elif v.startswith("/"):
        p = v.split("?")[0]
    else:
        return None
    p = re.sub(r"/{2,}", "/", p).rstrip("/")
    return p


def is_config_ph(origin: str | None) -> bool:
    return bool(origin) and bool(re.fullmatch(r"\{(runtimeConfig|env)\.[^{}]+\}", origin))

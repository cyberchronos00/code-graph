"""Nuxt framework plugin (on the TypeScript plugin).

register_hooks: finds the source dir (nuxt.config `srcDir`, else `app/` (Nuxt 4), `src/`, or the repo root
  (Nuxt 3 default)) and points the TS extractor at Nuxt's generated types (.nuxt/tsconfig.app.json or
  .nuxt/tsconfig.json, whose nuxt.d.ts pulls in .nuxt/types/imports.d.ts = auto-imported composables /
  utils / stores) and the generated global-components map (.nuxt/types/components.d.ts), plus Nuxt
  directory conventions (pages/, layouts/, components/, composables/, stores/, utils/, middleware/, plugins/).
  A clean checkout without .nuxt (no `nuxi prepare` run) gets an equivalent generated on the fly in a temp
  dir: path aliases (~, @, ~~, @@), auto-imports of composables/ utils/ stores/ exports, global components
  with Nuxt's path-prefixed names. Vue / Nuxt built-ins resolve when node_modules is installed. The stats
  say which one was used (`prepared`: nuxi | generated).
contribute: file-based routing (route path per page), entry points (pages = ui_page; app.vue,
  layouts, global middleware, plugins = ui_global), USES_LAYOUT, i18n keys (USES_I18N; defining
  locale files), Nitro server routes (server/api/** -> `route:<METHOD> /api/...`, server/routes/** without the
  prefix; ROUTES_TO the default-export handler, USES_MIDDLEWARE every server/middleware handler, MATCHES_ROUTE
  from same-origin $fetch / useFetch / useLazyFetch calls of the app) (#216).
"""
from __future__ import annotations

import atexit
from collections import defaultdict
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from ... import presets
from ...core.plugin import FrameworkPlugin, GraphBuilder, Project

# dependency / VCS directories every walk skips (cg_code_graph/presets/common.yaml skip_dirs)
COMMON_SKIP = presets.skip_dirs("common")

DIR_KINDS = [("pages/", "page"), ("layouts/", "layout"), ("components/", "component"), ("composables/", "composable"),
             ("stores/", "store"), ("utils/", "util"), ("middleware/", "middleware"), ("plugins/", "plugin"),
             ("app.vue", "app"), ("error.vue", "app")]


def kind_rules(src_dir: str = "app") -> list[tuple[str, str]]:
    pre = "" if src_dir in ("", ".") else src_dir.strip("/") + "/"
    return [(pre + d, k) for d, k in DIR_KINDS]


KIND_RULES = kind_rules("app")


def nuxt_route(rel: str, src_dir: str = "app") -> str:
    """app/pages/shelves/[id]/books/[bookId].vue -> /shelves/:id/books/:bookId (pages/... when srcDir is the root)"""
    pre = "" if src_dir in ("", ".") else src_dir.strip("/") + "/"
    p = rel[len(f"{pre}pages/"):]
    p = re.sub(r"\.vue$", "", p)
    segs = []
    parts = p.split("/")
    for i, s in enumerate(parts):
        if re.fullmatch(r"\(.*\)", s):
            continue  # route group
        if s == "index" and i == len(parts) - 1:
            continue
        s = re.sub(r"\[\.\.\.(\w+)\]", r":\1(.*)*", s)
        s = re.sub(r"\[\[(\w+)\]\]", r":\1?", s)
        s = re.sub(r"\[(\w+)\]", r":\1", s)
        segs.append(s)
    return "/" + "/".join(segs)


def find_src_dir(root: Path) -> str:
    """nuxt.config srcDir, else app/ (Nuxt 4 layout), src/ (when it holds pages/ or app.vue), else the root."""
    for n in ("nuxt.config.ts", "nuxt.config.js", "nuxt.config.mjs"):
        f = root / n
        if f.exists():
            m = re.search(r"\bsrcDir\s*:\s*['\"`]([^'\"`]+)['\"`]", f.read_text(errors="replace"))
            if m:
                d = m.group(1).strip().lstrip("./").rstrip("/") or "."
                if (root / d).is_dir():
                    return d
    for d in ("app", "src"):
        if any((root / d / x).exists() for x in ("pages", "app.vue", "components", "layouts", "composables")):
            return d
    if (root / "app").is_dir() and not (root / "pages").exists():
        return "app"
    return "."


AUTO_IMPORT_DIRS = ("composables", "utils", "stores")
# Nuxt also scans these (imports/module.ts); stores stays because the stand-in already did.
_LAYER_SCAN_DIRS = ("composables", "utils", "types", "stores")
_SHARED_SCAN = (("shared", "utils"), ("shared", "types"))
_SCAN_EXTS = ("mts", "cts", "ts", "tsx", "mjs", "cjs", "js", "jsx")
_CFG_NAMES = ("nuxt.config.ts", "nuxt.config.mts", "nuxt.config.js", "nuxt.config.mjs", "nuxt.config.cjs")
# kept out of the generated tsconfig: build output, native shells, static assets (cg_code_graph/presets/nuxt.yaml)
EXCLUDE = sorted(presets.skip_dirs("nuxt"))
# Nuxt source dirs outside the srcDir: server/ (Nitro: api, routes, middleware, plugins, utils) and the Nuxt 4 shared/
# dir sit at the root next to app/ (#214)
SIDE_DIRS = ("server", "shared")
_SIDE_EXT = re.compile(r"\.(ts|tsx|mts|cts)$")
_SIDE_SKIP = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$|\.d\.ts$")


def side_dirs(root: Path, src_dir: str) -> list[str]:
    """Root-level server/ and shared/ of a Nuxt project whose srcDir is not the root (with a root srcDir they are
    already inside it; Nuxt 3's src/server sits inside a src/ srcDir), and the server/ dirs of local layers."""
    if src_dir in ("", "."):
        return []
    return [d for d in SIDE_DIRS if (root / d).is_dir()] + layer_server_dirs(root, src_dir)


def layer_server_dirs(root: Path, src_dir: str | None = None) -> list[str]:
    """server/ dirs of local layers (#217): `extends: ['./layers/admin']` and the auto-registered layers/*/."""
    out: list[str] = []
    try:
        plan_layers = nuxt_import_plan(root, src_dir)["layers"][1:]
    except Exception:   # noqa: BLE001  (a config the scanner cannot read: no extends)
        plan_layers = []
    cands = [ly["dir"] for ly in plan_layers]
    if (root / "layers").is_dir():
        cands += sorted(d for d in (root / "layers").iterdir() if d.is_dir())
    for d in cands:
        try:
            rel = d.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if rel and rel != "." and (root / rel / "server").is_dir() and f"{rel}/server" not in out:
            out.append(f"{rel}/server")
    return out


def side_files(root: Path, dirs: list[str]) -> list[str]:
    """TypeScript files under the side dirs (tests and declarations excluded): roots of a prepared run, whose
    .nuxt/tsconfig.app.json leaves server/ to Nitro's own tsconfig."""
    out = []
    for d in dirs:
        for f in sorted((root / d).rglob("*")):
            rel = f.relative_to(root)
            if any(p in COMMON_SKIP or p in EXCLUDE or p.startswith(".") for p in rel.parts[:-1]):
                continue
            if f.is_file() and _SIDE_EXT.search(f.name) and not _SIDE_SKIP.search(f.name):
                out.append(rel.as_posix())
    return out


def _exports(f: Path, _seen: set | None = None) -> list[tuple[str, str]]:
    """(local name, export name) pairs of a module, by regex (enough for auto-import declarations).

    `export … from` is followed one step so a barrel listed in `imports.dirs` contributes the names it
    re-exports. The declaration still points at this file; TypeScript resolves the re-export."""
    seen = _seen if _seen is not None else set()
    try:
        key = str(f.resolve())
    except OSError:
        key = str(f)
    if key in seen or not f.is_file():
        return []
    seen.add(key)
    txt = f.read_text(errors="replace")
    out = []
    for m in re.finditer(r"^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)", txt, re.M):
        out.append((m.group(1), m.group(1)))
    for m in re.finditer(r"^export\s*\{([^}]*)\}(?!\s*from)", txt, re.M):
        for part in m.group(1).split(","):
            bits = part.strip().split(" as ")
            if bits[0].strip() and not bits[0].strip().startswith("type "):
                out.append((bits[-1].strip(), bits[-1].strip()))
    for m in re.finditer(r"^export\s+\*\s+from\s+['\"]([^'\"]+)['\"]", txt, re.M):
        sub = _resolve_rel_module(f.parent, m.group(1))
        if sub:
            out.extend(_exports(sub, seen))
    for m in re.finditer(r"^export\s*\{([^}]*)\}\s+from\s+['\"]([^'\"]+)['\"]", txt, re.M):
        sub = _resolve_rel_module(f.parent, m.group(2))
        names = {pub for pub, _ in _exports(sub, seen)} if sub else set()
        for part in m.group(1).split(","):
            bits = [b.strip() for b in part.strip().split(" as ")]
            if not bits[0] or bits[0].startswith("type "):
                continue
            pub = bits[-1]
            if not names or pub in names or bits[0] in names:
                out.append((pub, pub))
    if re.search(r"^export\s+default\b", txt, re.M):
        stem = f.stem if f.stem != "index" else f.parent.name
        name = re.sub(r"[-_.](\w)", lambda m: m.group(1).upper(), stem)
        out.append((name[:1].lower() + name[1:], "default"))
    return out


def _resolve_rel_module(base: Path, spec: str) -> Path | None:
    if not spec.startswith("."):
        return None
    target = (base / spec)
    cands = [target]
    for ext in _SCAN_EXTS:
        cands.append(Path(str(target) + f".{ext}"))
        cands.append(target / f"index.{ext}")
    for c in cands:
        if c.is_file() and not c.name.endswith(".d.ts"):
            return c
    return None


def _brace_expand(s: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", s)
    if not m:
        return [s]
    out = []
    for part in m.group(1).split(","):
        out.extend(_brace_expand(s[:m.start()] + part + s[m.end():]))
    return out


def _file_pattern(g: str) -> bool:
    base = g.rstrip("/").rsplit("/", 1)[-1]
    return bool(re.search(r"\.[A-Za-z0-9*]+$", base))


def _scan_patterns(entry: str) -> list[str]:
    """unimport normalizeScanDirs: a path with an extension is one file; anything else is one directory level
    (`dir/*.{ts,js,…}`), and a trailing slash means the tree (`dir/**`)."""
    entry = entry.strip()
    if entry.endswith("/"):
        entry = entry + "**"
    out = []
    for g in _brace_expand(entry):
        if _file_pattern(g):
            out.append(g)
        else:
            stem = g.rstrip("/")
            out.extend(_brace_expand(stem + "/*.{%s}" % ",".join(_SCAN_EXTS)))
            # `dir/**` has no extension, so the file pattern is appended under it (unimport joinGlobFilePattern)
            if stem.endswith("**"):
                out.extend(_brace_expand(stem + "/*.{%s}" % ",".join(_SCAN_EXTS)))
    return out


def _glob_files(pattern: str) -> list[Path]:
    magic = re.search(r"[*?]", pattern)
    if not magic:
        p = Path(pattern)
        return [p] if p.is_file() else []
    base = pattern[:magic.start()]
    if base and not base.endswith("/"):
        slash = base.rfind("/")
        base = base[:slash + 1] if slash >= 0 else ""
    rel = pattern[len(base):]
    root = Path(base) if base else Path(".")
    if not root.is_dir():
        return []
    try:
        return [p for p in root.glob(rel) if p.is_file()]
    except (OSError, ValueError):
        return []


def _skip_scan_file(p: Path) -> bool:
    return bool(re.search(r"\.(test|spec|d)\.", p.name)) or p.suffix not in {f".{e}" for e in _SCAN_EXTS}


def _default_scan_files(base: Path) -> list[Path]:
    """Top-level modules plus `*/index.*` — the stand-in the default composables/ utils/ stores/ dirs already used."""
    if not base.is_dir():
        return []
    files = [f for f in sorted(base.iterdir()) if f.is_file() and not _skip_scan_file(f)]
    files += [i for c in sorted(base.iterdir()) if c.is_dir()
              for ext in _SCAN_EXTS for i in (c / f"index.{ext}",) if i.is_file() and not _skip_scan_file(i)]
    return files


class _JS:
    """A scanner over a nuxt.config source. Strings and comments stay intact; values are literals or a sentinel."""

    def __init__(self, s: str):
        self.s, self.i, self.n = s, 0, len(s)

    def _ws(self):
        s, i, n = self.s, self.i, self.n
        while i < n:
            if s.startswith("//", i):
                i = s.find("\n", i)
                i = n if i < 0 else i
            elif s.startswith("/*", i):
                j = s.find("*/", i + 2)
                i = n if j < 0 else j + 2
            elif s[i] in " \t\r\n":
                i += 1
            else:
                break
        self.i = i

    def _line(self) -> int:
        return self.s.count("\n", 0, self.i) + 1

    def parse_value(self):
        self._ws()
        if self.i >= self.n:
            return ("bad", self._line())
        c = self.s[self.i]
        if c in "\"'":
            return self._string(c)
        if c == "`":
            return self._template()
        if c == "{":
            return self._object()
        if c == "[":
            return self._array()
        if self.s.startswith("true", self.i) and not self._ident_cont(4):
            self.i += 4
            return True
        if self.s.startswith("false", self.i) and not self._ident_cont(5):
            self.i += 5
            return False
        if self.s.startswith("null", self.i) and not self._ident_cont(4):
            self.i += 4
            return None
        if c.isdigit() or (c == "-" and self.i + 1 < self.n and self.s[self.i + 1].isdigit()):
            j = self.i + 1
            while j < self.n and self.s[j] in "0123456789._":
                j += 1
            raw = self.s[self.i:j]
            self.i = j
            try:
                return int(raw) if "." not in raw else float(raw)
            except ValueError:
                return ("num",)
        line = self._line()
        self._skip_expr()
        return ("expr", line)

    def _ident_cont(self, k: int) -> bool:
        j = self.i + k
        return j < self.n and (self.s[j].isalnum() or self.s[j] in "_$")

    def _string(self, q: str):
        s, i, n = self.s, self.i + 1, self.n
        buf = []
        while i < n:
            c = s[i]
            if c == "\\":
                if i + 1 < n:
                    buf.append(s[i + 1])
                    i += 2
                    continue
            if c == q:
                self.i = i + 1
                return "".join(buf)
            buf.append(c)
            i += 1
        self.i = n
        return ("bad", self._line())

    def _template(self):
        s, i, n = self.s, self.i + 1, self.n
        buf, expr = [], False
        while i < n:
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == "`":
                self.i = i + 1
                return ("expr", self._line()) if expr else "".join(buf)
            if s.startswith("${", i):
                expr = True
                i += 2
                depth = 1
                while i < n and depth:
                    if s[i] in "\"'`":
                        q = s[i]
                        i += 1
                        while i < n and s[i] != q:
                            i += 2 if s[i] == "\\" else 1
                        i += 1
                        continue
                    if s[i] == "{":
                        depth += 1
                    elif s[i] == "}":
                        depth -= 1
                    i += 1
                continue
            buf.append(s[i])
            i += 1
        self.i = n
        return ("expr", self._line())

    def _object(self) -> dict:
        self.i += 1
        out = {}
        while self.i < self.n:
            self._ws()
            if self.i >= self.n:
                break
            if self.s[self.i] == "}":
                self.i += 1
                break
            if self.s[self.i] == ",":
                self.i += 1
                continue
            key_line = self._line()
            if self.s[self.i] in "\"'`":
                key = self.parse_value()
            elif self.s[self.i] == "[":
                self._skip_expr()
                key = ("expr", key_line)
            else:
                j = self.i
                while j < self.n and (self.s[j].isalnum() or self.s[j] in "_$"):
                    j += 1
                key = self.s[self.i:j]
                self.i = j
            self._ws()
            if self.i < self.n and self.s[self.i] == ":":
                self.i += 1
            val = self.parse_value()
            if isinstance(key, str):
                out[key] = val
        return out

    def _array(self) -> list:
        self.i += 1
        out = []
        while self.i < self.n:
            self._ws()
            if self.i >= self.n:
                break
            if self.s[self.i] == "]":
                self.i += 1
                break
            if self.s[self.i] == ",":
                self.i += 1
                continue
            out.append(self.parse_value())
        return out

    def _skip_expr(self):
        """Skip one non-literal expression, including nested brackets and arrow bodies."""
        s, n = self.s, self.n
        i = self.i
        depth = 0
        while i < n:
            c = s[i]
            if c in "\"'":
                self.i = i
                self._string(c)
                i = self.i
                continue
            if c == "`":
                self.i = i
                self._template()
                i = self.i
                continue
            if c == "/" and i + 1 < n and s[i + 1] == "/":
                i = s.find("\n", i)
                i = n if i < 0 else i
                continue
            if c == "/" and i + 1 < n and s[i + 1] == "*":
                j = s.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif c in ",;" and depth == 0:
                break
            i += 1
        self.i = i


def _find_config_object(src: str) -> dict:
    """The object passed to defineNuxtConfig / export default, or the first object that holds Nuxt keys."""
    js = _JS(src)
    # prefer the argument of defineNuxtConfig(
    m = re.search(r"\bdefineNuxtConfig\s*\(", src)
    if m:
        js.i = m.end()
        val = js.parse_value()
        if isinstance(val, dict):
            return val
    js.i = 0
    best, best_at = {}, 10 ** 9
    while js.i < js.n:
        js._ws()
        if js.i >= js.n:
            break
        if js.s[js.i] == "{":
            at = js.i
            val = js.parse_value()
            if isinstance(val, dict) and any(k in val for k in ("imports", "srcDir", "extends", "components")) and at < best_at:
                return val
        else:
            js.i += 1
    return best


def _as_bool(v, default=None):
    if isinstance(v, bool):
        return v
    return default


def _named_from_imports(val) -> list[dict]:
    """`imports.imports`: `{ name, as, from, priority }` entries and inline presets `{ from, imports: [...] }`."""
    out = []
    if not isinstance(val, list):
        return out
    for item in val:
        if not isinstance(item, dict):
            continue
        if item.get("disabled") is True:
            continue
        if isinstance(item.get("name"), str) and isinstance(item.get("from"), str):
            if item.get("type") is True:
                continue
            pri = item.get("priority")
            pri = pri if isinstance(pri, int) else 1
            out.append({"name": item["name"], "as": item["as"] if isinstance(item.get("as"), str) else item["name"],
                        "from": item["from"], "priority": pri})
            continue
        frm = item.get("from")
        inner = item.get("imports")
        if isinstance(frm, str) and isinstance(inner, list):
            pri = item.get("priority")
            pri = pri if isinstance(pri, int) else 1
            for n in inner:
                if isinstance(n, str):
                    out.append({"name": n, "as": n, "from": frm, "priority": pri})
                elif isinstance(n, dict) and isinstance(n.get("name"), str):
                    out.append({"name": n["name"], "as": n["as"] if isinstance(n.get("as"), str) else n["name"],
                                "from": n["from"] if isinstance(n.get("from"), str) else frm, "priority": pri})
    return out


def _dir_entries(val) -> tuple[list[str], list[tuple[int, str]]]:
    """(literal paths/globs, unevaluable (line, text) entries) from `imports.dirs`."""
    lits, blind = [], []
    if isinstance(val, tuple) and val and val[0] == "expr":
        return [], [(val[1], "imports.dirs")]
    if not isinstance(val, list):
        return [], []
    for item in val:
        if isinstance(item, str) and item.strip():
            lits.append(item.strip())
        elif isinstance(item, dict) and isinstance(item.get("glob"), str):
            lits.append(item["glob"].strip())
        elif isinstance(item, tuple) and item and item[0] == "expr":
            blind.append((item[1], "imports.dirs"))
        elif isinstance(item, dict):
            g = item.get("glob")
            if isinstance(g, tuple) and g and g[0] == "expr":
                blind.append((g[1], "imports.dirs"))
            elif "glob" in item or "dir" in item:
                blind.append((1, "imports.dirs"))
    return lits, blind


def read_nuxt_config(path: Path) -> dict:
    """Static slice of one nuxt.config: srcDir, extends, and the imports block. Missing file -> {}."""
    try:
        src = path.read_text(errors="replace")
    except OSError:
        return {}
    obj = _find_config_object(src)
    imp = obj.get("imports") if isinstance(obj.get("imports"), dict) else {}
    dirs, blind = _dir_entries(imp.get("dirs")) if imp else ([], [])
    extends = obj.get("extends")
    ext_lits = []
    if isinstance(extends, str):
        ext_lits = [extends]
    elif isinstance(extends, list):
        for e in extends:
            if isinstance(e, str):
                ext_lits.append(e)
    return {
        "srcDir": obj.get("srcDir") if isinstance(obj.get("srcDir"), str) else None,
        "extends": ext_lits,
        "scan": _as_bool(imp.get("scan")) if imp else None,
        "global": _as_bool(imp.get("global")) if imp else None,
        "auto_import": _as_bool(imp.get("autoImport")) if imp else None,
        "dirs": dirs,
        "dirs_blind": blind,
        "named": _named_from_imports(imp.get("imports")) if imp else [],
        "has_imports_list": isinstance(imp.get("imports"), list),
    }


def _config_path(directory: Path) -> Path | None:
    for n in _CFG_NAMES:
        f = directory / n
        if f.is_file():
            return f
    return None


def _layer_dirs(directory: Path, spec: str) -> list[Path]:
    """Local layer directories for one `extends` entry. Packages resolve through node_modules; remotes do not."""
    spec = spec.strip()
    if not spec or spec.startswith(("github:", "gitlab:", "bitbucket:", "https:", "http:", "git:")):
        return []
    if spec.startswith("."):
        p = (directory / spec).resolve()
        return [p] if p.is_dir() else []
    nm = directory / "node_modules" / spec
    if (nm / "nuxt.config.ts").is_file() or any((nm / n).is_file() for n in _CFG_NAMES):
        return [nm]
    return []


def nuxt_import_plan(root: Path, src_dir: str | None = None) -> dict:
    """What the stand-in auto-import table should contain when `.nuxt/` is absent.

    `scan: false` on the project config drops directory scanning (Nuxt ignores `dirs` then). A layer with
    `scan: false` drops that layer only. `autoImport: false` drops the table (Nuxt emits no global declarations).
    `global` does not: both values still leave bare names in source, which the stand-in declares.
    """
    root = root.resolve()
    cfg_path = _config_path(root)
    src_dir = find_src_dir(root) if src_dir is None else src_dir
    layers = []
    seen = set()

    def add(directory: Path, src: str, cfg: dict, rel_cfg: str):
        key = str(directory.resolve())
        if key in seen:
            return
        seen.add(key)
        layers.append({"dir": directory, "src": src, "cfg": cfg, "file": rel_cfg})
        base = directory
        for spec in cfg.get("extends") or []:
            for child in _layer_dirs(base, spec):
                child_cfg_path = _config_path(child)
                child_cfg = read_nuxt_config(child_cfg_path) if child_cfg_path else {}
                child_src = child_cfg.get("srcDir") or "."
                if child_src in ("", "."):
                    child_src_path = child
                else:
                    child_src_path = child / child_src.strip().lstrip("./").rstrip("/")
                if not child_src_path.is_dir():
                    child_src = "."
                rel = str(child_cfg_path.relative_to(root)) if child_cfg_path and child_cfg_path.is_relative_to(root) else str(child_cfg_path or child)
                add(child, child_src if child_src_path.is_dir() else ".", child_cfg, rel)

    root_cfg = read_nuxt_config(cfg_path) if cfg_path else {}
    add(root, src_dir if src_dir not in ("",) else ".", root_cfg, str(cfg_path.relative_to(root)) if cfg_path else "nuxt.config.ts")
    project = layers[0]["cfg"] if layers else {}
    scan = True if project.get("scan") is None else project["scan"]
    auto = True if project.get("auto_import") is None else project["auto_import"]
    globl = False if project.get("global") is None else project["global"]
    named = list(project.get("named") or [])
    if not project.get("has_imports_list"):
        for layer in layers[1:]:
            named.extend(layer["cfg"].get("named") or [])
    blind = []
    for layer in layers:
        for line, _what in layer["cfg"].get("dirs_blind") or []:
            blind.append((layer["file"], line))
    return {"scan": scan, "auto_import": auto, "global": globl, "layers": layers, "named": named, "blind": blind}


def _layer_src(layer: dict) -> Path:
    d, src = layer["dir"], layer["src"]
    return d.resolve() if src in ("", ".") else (d / src).resolve()


def _resolve_from(layer_dir: Path, src: Path, spec: str) -> tuple[str, str]:
    """(dts import specifier, access suffix piece). Package specifiers stay as specifiers."""
    raw = spec.strip()
    path = None
    if raw.startswith(("~~/", "@@/")):
        path = layer_dir / raw[3:]
    elif raw.startswith(("~/", "@/")):
        path = src / raw[2:]
    elif raw.startswith(("~~", "@@")) and (len(raw) == 2 or raw[2] in "/"):
        path = layer_dir
    elif raw in ("~", "@") or raw.startswith("./") or raw.startswith("../"):
        path = (src if raw in ("~", "@") else layer_dir) / (raw[2:] if raw in ("~/", "@/") else raw if raw.startswith(".") else "")
        if raw in ("~", "@"):
            path = src
        elif raw.startswith("."):
            path = (layer_dir / raw)
    if path is None:
        return raw, ""
    if path.is_dir():
        for ext in _SCAN_EXTS:
            if (path / f"index.{ext}").is_file():
                path = path / f"index.{ext}"
                break
    elif not path.is_file():
        for ext in _SCAN_EXTS:
            if Path(str(path) + f".{ext}").is_file():
                path = Path(str(path) + f".{ext}")
                break
    if path.is_file():
        return str(path.with_suffix("")).replace("\\", "/"), ""
    return str(path).replace("\\", "/"), ""


def import_dir_files(src: Path, root: Path, entry: str, exclude: list[str] | None = None) -> list[Path]:
    """Files one `imports.dirs` entry contributes, relative aliases resolved against `src` (`~`, `@`) or `root`."""
    if entry.startswith("!"):
        return []
    raw = entry
    if raw.startswith(("~~/", "@@/")):
        raw = str(root / raw[3:])
    elif raw.startswith(("~/", "@/")):
        raw = str(src / raw[2:])
    elif raw.startswith("./"):
        raw = str(src / raw[2:])
    elif raw.startswith("../"):
        raw = str((src / raw).resolve())
    elif not raw.startswith("/"):
        raw = str(src / raw)
    found = []
    for pat in _scan_patterns(raw):
        found.extend(_glob_files(pat))
    excl = exclude or []
    out = []
    for f in found:
        if _skip_scan_file(f):
            continue
        rel = f.resolve().as_posix()
        if any(_glob_match(rel, g) or _glob_match(str(f.relative_to(src)) if str(f).startswith(str(src)) else rel, g) for g in excl):
            continue
        out.append(f)
    return out


def _glob_match(path: str, pattern: str) -> bool:
    p = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", ".")
    return re.fullmatch(p, path) is not None


def unevaluable_import_dirs(root: Path) -> list[tuple[str, int]]:
    """(file, line) of `imports.dirs` / `extends` entries that are not literal paths. Empty when `.nuxt` types exist
    (those stay authoritative) or the project has no nuxt config."""
    root = Path(root)
    if not any((root / n).is_file() for n in _CFG_NAMES):
        # a nuxt config may live in a subfolder; the plan walks from the root config only
        return []
    if (root / ".nuxt" / "types" / "imports.d.ts").is_file() or (root / ".nuxt" / "imports.d.ts").is_file():
        return []
    plan = nuxt_import_plan(root)
    return [(f, line) for f, line in plan["blind"]]


def _split_case(s: str) -> list[str]:
    """scule's splitByCase: `BaseButton` / `base-button` / `base_button` -> [Base, Button] / [base, button]."""
    out = []
    for chunk in re.split(r"[-_./]", s):
        out += [w for w in re.findall(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+", chunk) if w]
    return out


def _component_name(rel_parts: list[str]) -> str:
    """Nuxt's component name for a file under components/ (resolveComponentNameSegments): the directory words, minus
    the trailing ones the file name already starts with. base/form/Input.vue -> BaseFormInput, base/BaseButton.vue ->
    BaseButton, list/Lists.vue -> ListLists, form/input/index.vue -> FormInput, Foo.client.vue -> Foo."""
    *dirs, file = rel_parts
    stem = re.sub(r"(\.(client|server))?\.(vue|tsx|jsx|ts|js)$", "", file)
    file_parts = [] if stem.lower() == "index" and dirs else _split_case(stem)
    content = "/".join(file_parts).lower()
    name_parts = [w for d in dirs for w in _split_case(d)]
    cut, suffix = None, []
    for i in range(len(dirs) - 1, -1, -1):
        suffix = [w.lower() for w in _split_case(dirs[i])] + suffix
        sc = "/".join(suffix)
        if content and (content == sc or content.startswith(sc + "/")):
            cut = sum(len(_split_case(d)) for d in dirs[:i])
    if cut is not None:
        name_parts = name_parts[:cut]
    return "".join(w[:1].upper() + w[1:] for w in name_parts + file_parts)


def generate_types(root: Path, src_dir: str) -> tuple[Path, dict]:
    """A stand-in for `nuxi prepare`: tsconfig + auto-import / component declarations in a temp dir."""
    d = Path(tempfile.mkdtemp(prefix="cg-nuxt-"))
    atexit.register(shutil.rmtree, d, True)
    src = (root / src_dir).resolve() if src_dir not in ("", ".") else root.resolve()
    plan = nuxt_import_plan(root, src_dir)
    lines, n_imp = ["export {}", "declare global {"], 0
    # name -> (priority, declaration). A higher priority replaces (explicit imports default to 1, scanned dirs to 0).
    chosen: dict[str, tuple[int, str]] = {}

    def consider(name: str, priority: int, decl: str) -> None:
        prev = chosen.get(name)
        if prev is None or priority > prev[0]:
            chosen[name] = (priority, decl)

    if plan["auto_import"] and plan["scan"]:
        for layer in plan["layers"]:
            if layer["cfg"].get("scan") is False:
                continue
            layer_src = _layer_src(layer)
            layer_root = layer["dir"].resolve()
            bases = [layer_src / sub for sub in _LAYER_SCAN_DIRS]
            bases += [layer_root / a / b for a, b in _SHARED_SCAN]
            for base in bases:
                for f in _default_scan_files(base):
                    spec = str(f.with_suffix("")).replace("\\", "/")
                    for local, exp in _exports(f):
                        acc = "['default']" if exp == "default" else f".{exp}"
                        consider(local, 0, f"  const {local}: typeof import('{spec}'){acc}")
            excl = [e[1:] for e in layer["cfg"].get("dirs") or [] if isinstance(e, str) and e.startswith("!")]
            for entry in layer["cfg"].get("dirs") or []:
                if not isinstance(entry, str) or entry.startswith("!"):
                    continue
                for f in import_dir_files(layer_src, layer_root, entry, excl):
                    spec = str(f.with_suffix("")).replace("\\", "/")
                    for local, exp in _exports(f):
                        acc = "['default']" if exp == "default" else f".{exp}"
                        consider(local, 0, f"  const {local}: typeof import('{spec}'){acc}")
    if plan["auto_import"]:
        for item in plan["named"]:
            spec, _ = _resolve_from(root.resolve(), src, item["from"])
            acc = f"['{item['name']}']" if not re.match(r"^[A-Za-z_$][\w$]*$", item["name"]) else f".{item['name']}"
            alias = item["as"]
            if re.match(r"^[A-Za-z_$][\w$]*$", alias):
                consider(alias, int(item.get("priority") or 1), f"  const {alias}: typeof import('{spec}'){acc}")
    for decl in chosen.values():
        lines.append(decl[1])
        n_imp += 1
    seen = set(chosen)
    nm = root / "node_modules"
    builtins = 0
    if (nm / "vue").is_dir():
        for n in ("ref", "computed", "reactive", "watch", "watchEffect", "onMounted", "onBeforeUnmount", "onUnmounted",
                  "nextTick", "toRef", "toRefs", "unref", "shallowRef", "readonly"):
            if n not in seen:
                lines.append(f"  const {n}: typeof import('vue')['{n}']")
                builtins += 1
    if (nm / "nuxt").is_dir():
        for n in ("useRuntimeConfig", "useFetch", "useLazyFetch", "useAsyncData", "useLazyAsyncData", "useRoute", "useRouter",
                  "navigateTo", "useState", "useCookie", "useNuxtApp", "defineNuxtPlugin", "defineNuxtRouteMiddleware",
                  "definePageMeta", "useHead", "useRequestHeaders"):
            if n not in seen:
                lines.append(f"  const {n}: typeof import('nuxt/app')['{n}']")
                builtins += 1
        if "$fetch" not in seen:
            lines.append("  const $fetch: typeof import('ofetch')['$fetch']")
    if (nm / "pinia").is_dir() and "defineStore" not in seen:
        lines.append("  const defineStore: typeof import('pinia')['defineStore']")
    lines.append("}")
    (d / "types").mkdir()
    (d / "types" / "imports.d.ts").write_text("\n".join(lines) + "\n")
    (d / "nuxt.d.ts").write_text('/// <reference path="types/imports.d.ts" />\nexport {}\n')
    comps = ["interface _GlobalComponents {"]
    n_comp = 0
    cbase = src / "components"
    if cbase.is_dir():
        for f in sorted(cbase.rglob("*")):
            if f.is_file() and f.suffix in (".vue", ".tsx", ".jsx") and COMMON_SKIP.isdisjoint(f.parts):
                name = _component_name(list(f.relative_to(cbase).parts))
                comps.append(f'  {name}: typeof import("{f}")[\'default\']')
                comps.append(f'  Lazy{name}: LazyComponent<typeof import("{f}")[\'default\']>')
                n_comp += 1
    comps.append("}")
    (d / "types" / "components.d.ts").write_text("\n".join(comps) + "\n")
    # the user's tsconfig (minus its reference to .nuxt) still contributes compilerOptions such as extra paths
    user_opts = {}
    try:
        raw = re.sub(r"(?m)^\s*//.*$", "", (root / "tsconfig.json").read_text())
        user_opts = (json.loads(re.sub(r",(\s*[}\]])", r"\1", raw)) or {}).get("compilerOptions") or {}
    except Exception:
        pass
    paths = {k: [str((root / x).resolve()) for x in v] for k, v in (user_opts.get("paths") or {}).items()}
    for alias, target in (("~", src), ("@", src), ("~~", root.resolve()), ("@@", root.resolve())):
        paths.setdefault(f"{alias}/*", [f"{target}/*"])
        paths.setdefault(alias, [str(target)])
    cfg = {"compilerOptions": {"target": "ESNext", "module": "ESNext", "moduleResolution": "Bundler", "jsx": "preserve",
                               "allowJs": True, "checkJs": False, "strict": False, "skipLibCheck": True, "noEmit": True,
                               "resolveJsonModule": True, "esModuleInterop": True, "baseUrl": str(root.resolve()),
                               "paths": paths},
           "include": [str(d / "nuxt.d.ts"), f"{src}/**/*"],
           "exclude": [str(root.resolve() / x) for x in EXCLUDE] + [f"{src}/**/*.d.ts"]}
    (d / "tsconfig.app.json").write_text(json.dumps(cfg, indent=1))
    return d, {"auto_imports": n_imp, "builtins": builtins, "components": n_comp,
               "import_scan": plan["scan"], "import_global": plan["global"], "auto_import": plan["auto_import"]}


# Nitro file-based routing (https://nitro.build/guide/routing): <name>[.<method>][.<env>].<ext>
NITRO_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "connect", "trace")
_NITRO_EXT = re.compile(r"\.(ts|mts|cts|js|mjs|cjs)$")
_NITRO_SKIP = re.compile(r"\.(test|spec)\.[cm]?[jt]s$|\.d\.ts$")
_PROXY_CALL = re.compile(r"\b(proxyRequest|sendProxy)\s*\(")


def nitro_route(rel: str, prefix: str = "") -> tuple[str, str] | None:
    """Path of a file inside server/api or server/routes -> (METHOD, uri), or None for a non-route file.

    `plants/[id].get.ts` with prefix `/api` -> (GET, /api/plants/{id}); no method suffix -> ANY; `index` is the
    directory; `[...slug]` -> {slug*}, `[...]` -> {_*}; `(group)` directories are dropped; `.dev` / `.prod` /
    `.prerender` env suffixes are ignored."""
    if not _NITRO_EXT.search(rel) or _NITRO_SKIP.search(rel):
        return None
    parts = [p for p in _NITRO_EXT.sub("", rel).split("/") if p]
    if not parts:
        return None
    last = parts[-1]
    last = re.sub(r"\.(dev|prod|prerender)$", "", last)
    method = "ANY"
    m = re.match(r"^(.+)\.(%s)$" % "|".join(NITRO_METHODS), last)
    if m:
        last, method = m.group(1), m.group(2).upper()
    parts[-1] = last
    segs = []
    for i, s in enumerate(parts):
        if re.fullmatch(r"\(.*\)", s):
            continue
        if s == "index" and i == len(parts) - 1:
            continue
        if s == "[...]":
            segs.append("{_*}")
            continue
        mm = re.fullmatch(r"\[\.\.\.(\w+)\]", s)
        if mm:
            segs.append("{%s*}" % mm.group(1))
            continue
        segs.append(re.sub(r"\[(\w+)\]", r"{\1}", s))
    return method, "/" + "/".join(s.strip("/") for s in [prefix.strip("/")] + segs if s.strip("/"))


_OBJ_HOOK = re.compile(r"\b(onRequest|onBeforeResponse)\s*:\s*(\[[^\]]*\]|[A-Za-z_$][\w$]*|\(|async\b|function\b)")
_OBJ_HANDLER_REF = re.compile(r"\bhandler\s*:\s*([A-Za-z_$][\w$]*)\s*[,}\n]")
_PATH_GUARD = re.compile(r"if\s*\(\s*!\s*(?:event\.path|event\.node\.req\.url|getRequestURL\(\s*event\s*\)\.pathname)"
                         r"(?:\?)?\.startsWith\(\s*['\"`]([^'\"`]+)['\"`]\s*\)\s*\)\s*\{?\s*return\b")


def object_hooks(src: str) -> tuple[list[tuple[str, str]], str | None]:
    """`defineEventHandler({ onRequest: [auth], onBeforeResponse: fn, handler })` (#217): [(hook, name)] where name is
    an identifier or `<inline>`, and the identifier given as `handler` (None when it is an inline function)."""
    i = src.find("defineEventHandler(")
    if i < 0:
        return [], None
    j = i + len("defineEventHandler(")
    while j < len(src) and src[j] in " \t\r\n":
        j += 1
    if j >= len(src) or src[j] != "{":
        return [], None
    body = src[j:]
    hooks = []
    for m in _OBJ_HOOK.finditer(body):
        hook, val = m.group(1), m.group(2)
        if val.startswith("["):
            for name in re.findall(r"[A-Za-z_$][\w$]*", val[1:-1]):
                hooks.append((hook, name))
        elif val in ("(", "async", "function"):
            hooks.append((hook, "<inline>"))
        else:
            hooks.append((hook, val))
    ref = _OBJ_HANDLER_REF.search(body)
    return hooks, ref.group(1) if ref and ref.group(1) not in ("async", "function") else None


def path_guard(src: str) -> str | None:
    """Prefix of `if (!event.path.startsWith('/api/admin')) return` at the top of a Nitro middleware (#217)."""
    m = _PATH_GUARD.search(src)
    return m.group(1) if m else None


def rule_covers(pattern: str, uri: str) -> bool:
    """Nitro routeRules pattern (`/api/legacy/**`, `/blog/*`, exact) against a route uri with {param} segments."""
    pat = pattern.rstrip("/") or "/"
    if pat.endswith("/**"):
        base = pat[:-3]
        return uri == base or uri.startswith(base + "/")
    ps, us = pat.strip("/").split("/"), uri.strip("/").split("/")
    if len(ps) != len(us):
        return False
    return all(p == "*" or p == u or (u.startswith("{") and p.startswith(":")) for p, u in zip(ps, us))


def config_handlers(cfg: dict) -> list[dict]:
    """`serverHandlers` and `nitro.handlers` entries of a parsed nuxt.config: [{route, method, handler, middleware}]."""
    out = []
    nitro = cfg.get("nitro") if isinstance(cfg.get("nitro"), dict) else {}
    for where, items in (("serverHandlers", cfg.get("serverHandlers")), ("nitro.handlers", nitro.get("handlers"))):
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict) or not isinstance(it.get("handler"), str):
                continue
            out.append({"route": it.get("route") if isinstance(it.get("route"), str) else None,
                        "method": it.get("method") if isinstance(it.get("method"), str) else None,
                        "handler": it["handler"], "middleware": it.get("middleware") is True, "from": where})
    return out


def proxy_rules(cfg: dict) -> dict[str, str]:
    """routeRules / nitro.routeRules patterns with a `proxy` -> proxy target (or '?' when not a literal)."""
    out = {}
    nitro = cfg.get("nitro") if isinstance(cfg.get("nitro"), dict) else {}
    for rules in (cfg.get("routeRules"), nitro.get("routeRules")):
        for pat, rule in (rules.items() if isinstance(rules, dict) else ()):
            if isinstance(rule, dict) and "proxy" in rule:
                p = rule["proxy"]
                target = p if isinstance(p, str) else (p.get("to") if isinstance(p, dict) and isinstance(p.get("to"), str) else "?")
                out[pat] = target
    return out


def server_dirs(root: Path, src_dir: str) -> list[str]:
    """Nitro server dirs of a Nuxt project: the root server/ (Nuxt 4 serverDir) and <srcDir>/server (Nuxt 3)."""
    cands = ["server"] + ([] if src_dir in ("", ".") else [f"{src_dir.strip('/')}/server"])
    return [d for d in dict.fromkeys(cands) if (root / d).is_dir()]


# ------------------------------------------------------------ i18n locale files (#182)
# a top-level key shaped like a locale code: en, pt-BR, zh_Hant, es-419
LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:[-_](?:[A-Z]{2}|[A-Z][a-z]{3}|\d{3}))*$")
_KNOWN_LOCALES = {"en", "ar", "ku", "fr", "de"}
LOCALE_EXTS = (".json", ".yaml", ".yml")


def json_key_lines(text: str) -> dict[str, int]:
    """Dotted key path -> line of the key, for the objects of a JSON document (keys inside arrays are skipped,
    as flatten() treats arrays as values)."""
    out: dict[str, int] = {}
    stack: list[list] = []   # [type, current key, expecting a key]
    pending = None
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            raw = text[i:j + 1]
            if stack and stack[-1][0] == "obj" and stack[-1][2]:
                try:
                    pending = (json.loads(raw), line)
                except ValueError:
                    pending = (raw.strip('"'), line)
            line += raw.count("\n")
            i = j + 1
            continue
        elif c == ":" and pending is not None and stack and stack[-1][0] == "obj":
            stack[-1][1], stack[-1][2] = pending[0], False
            if all(f[0] == "obj" for f in stack):
                out.setdefault(".".join(str(f[1]) for f in stack), pending[1])
            pending = None
        elif c == "{":
            stack.append(["obj", None, True])
        elif c == "[":
            stack.append(["arr", None, False])
        elif c in "}]":
            if stack:
                stack.pop()
        elif c == "," and stack and stack[-1][0] == "obj":
            stack[-1][2] = True
        i += 1
    return out


def yaml_key_lines(text: str) -> dict[str, int]:
    import yaml
    out: dict[str, int] = {}

    def walk(node, prefix):
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                key = f"{prefix}.{k.value}" if prefix else str(k.value)
                out.setdefault(key, k.start_mark.line + 1)
                walk(v, key)
    try:
        walk(yaml.compose(text), "")
    except Exception:
        pass
    return out


def load_locale_file(f: Path) -> tuple[object, dict[str, int]]:
    """(data, key path -> line) of a JSON / YAML locale file; (None, {}) when it does not parse."""
    try:
        text = f.read_text(errors="replace")
        if f.suffix == ".json":
            return json.loads(text), json_key_lines(text)
        import yaml
        return yaml.safe_load(text), yaml_key_lines(text)
    except Exception:
        return None, {}


def nuxt_i18n_config(root: Path) -> dict:
    """Literal slice of nuxt.config `i18n`: defaultLocale, langDir, locale codes and their files."""
    cfg = _config_path(root)
    if not cfg:
        return {}
    try:
        obj = _find_config_object(cfg.read_text(errors="replace"))
    except Exception:
        return {}
    i18n = obj.get("i18n") if isinstance(obj.get("i18n"), dict) else {}
    codes, files = [], {}
    for loc in i18n.get("locales") or [] if isinstance(i18n.get("locales"), list) else []:
        if isinstance(loc, str):
            codes.append(loc)
        elif isinstance(loc, dict) and isinstance(loc.get("code"), str):
            codes.append(loc["code"])
            fs = [loc["file"]] if isinstance(loc.get("file"), str) else []
            fs += [x for x in loc.get("files") or [] if isinstance(x, str)] if isinstance(loc.get("files"), list) else []
            fs += [x["path"] for x in loc.get("files") or [] if isinstance(x, dict) and isinstance(x.get("path"), str)] \
                if isinstance(loc.get("files"), list) else []
            for x in fs:
                files[x.lstrip("./")] = loc["code"]
    return {"default_locale": i18n.get("defaultLocale") if isinstance(i18n.get("defaultLocale"), str) else None,
            "lang_dir": i18n.get("langDir") if isinstance(i18n.get("langDir"), str) else None,
            "locales": codes, "files": files}


def locale_dirs(root: Path, src_dir: str, lang_dir: str | None) -> list[str]:
    dirs = ["i18n/locales", f"{src_dir}/locales" if src_dir not in ("", ".") else "locales", "app/locales", "locales"]
    if lang_dir:
        ld = lang_dir.strip().lstrip("./").rstrip("/")
        dirs = [f"i18n/{ld}", (f"{src_dir}/{ld}" if src_dir not in ("", ".") else ld), ld] + dirs
    return [d for d in dict.fromkeys(dirs) if d and (root / d).is_dir()]


def _sfc_key_lines(text: str, keys: list[str]) -> dict[str, int]:
    """Line of each key's last segment inside the SFC's <i18n> block (the block's line when it is not found)."""
    m = re.search(r"<i18n\b[^>]*>", text)
    if not m:
        return {}
    base = text.count("\n", 0, m.start()) + 1
    body = text[m.end():]
    end = body.find("</i18n>")
    body = body if end < 0 else body[:end]
    out = {}
    for k in keys:
        last = k.split(".")[-1]
        km = re.search(r"[\"']?%s[\"']?\s*:" % re.escape(last), body)
        out[k] = base + (body.count("\n", 0, km.start()) if km else 0)
    return out


def i18n_definitions(root: Path, src_dir: str, sfc_i18n: dict) -> tuple[dict, list[str], str | None]:
    """key -> [{locale, file, line}], the project's locales, and the default locale (nuxt.config defaultLocale, else
    the first locale found). A file keyed by locale codes ({"en": {...}, "pt-BR": {...}}) defines every locale."""
    cfg = nuxt_i18n_config(root)
    configured = set(cfg.get("locales") or [])
    defs: dict[str, list[dict]] = {}
    seen_locales: list[str] = []

    def add(key, locale, rel, line):
        defs.setdefault(key, []).append({"locale": locale, "file": rel, "line": line or 1})
        if locale and locale not in seen_locales:
            seen_locales.append(locale)

    for d in locale_dirs(root, src_dir, cfg.get("lang_dir")):
        for f in sorted(p for p in (root / d).glob("*") if p.suffix in LOCALE_EXTS and p.is_file()):
            data, lines = load_locale_file(f)
            if not isinstance(data, dict) or not data:
                continue
            rel = f.relative_to(root).as_posix()
            top = list(data)
            stem_locale = f.stem if (LOCALE_RE.match(f.stem) or f.stem in configured) else None
            keyed = all(isinstance(data[k], dict) and isinstance(k, str) for k in top) and (
                set(top) <= (configured or _KNOWN_LOCALES) or set(top) <= _KNOWN_LOCALES
                or (stem_locale is None and all(LOCALE_RE.match(k) for k in top)))
            if keyed:
                for loc in top:
                    for k in flatten(data[loc]):
                        add(k, loc, rel, lines.get(f"{loc}.{k}"))
            else:
                loc = cfg.get("files", {}).get(f.name) or cfg.get("files", {}).get(rel) or stem_locale
                for k in flatten(data):
                    add(k, loc, rel, lines.get(k))
    for f, keys in sfc_i18n.items():
        try:
            text = (root / f).read_text(errors="replace")
        except OSError:
            text = ""
        kl = _sfc_key_lines(text, keys)
        for k in keys:
            add(k, None, f, kl.get(k, 1))
    default = cfg.get("default_locale") or (seen_locales[0] if seen_locales else None)
    locales = list(dict.fromkeys([*configured, *seen_locales]))
    return defs, locales, default


def flatten(d, prefix=""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from flatten(v, f"{prefix}.{k}" if prefix else k)
    else:
        yield prefix


class NuxtPlugin(FrameworkPlugin):
    name, language = "nuxt", "typescript"

    def detect(self, project: Project) -> bool:
        return any(project.exists(n) for n in ("nuxt.config.ts", "nuxt.config.js", "nuxt.config.mjs"))

    def register_hooks(self, ctx) -> None:
        root = ctx.project.root
        cfg = ctx.extractor_cfg
        self.src_dir = find_src_dir(root)
        self.generated = {}
        tsc = next((t for t in (".nuxt/tsconfig.app.json", ".nuxt/tsconfig.json") if (root / t).exists()), None)
        self.prepared = "nuxi" if tsc else "generated"
        if tsc:
            cfg["tsconfig"] = tsc
            cfg["components_dts"] = str(root / ".nuxt" / "types" / "components.d.ts")
            if not Path(cfg["components_dts"]).exists() and (root / ".nuxt" / "components.d.ts").exists():
                cfg["components_dts"] = str(root / ".nuxt" / "components.d.ts")
        else:
            d, self.generated = generate_types(root, self.src_dir)
            cfg["tsconfig"] = str(d / "tsconfig.app.json")
            cfg["components_dts"] = str(d / "types" / "components.d.ts")
            cfg["walk_src"] = True
            cfg["generated_types"] = True
            print(f"nuxt: no .nuxt/ in {root.name} (run `npx nuxi prepare` for Nuxt's own generated types); using "
                  f"generated stand-ins: {self.generated['auto_imports']} auto-imports, {self.generated['components']} "
                  f"components{'' if (root / 'node_modules').is_dir() else ', no node_modules so Vue/Nuxt built-ins stay unresolved'}",
                  file=sys.stderr)
        cfg["kinds"] = kind_rules(self.src_dir)
        # server/ and shared/ are source dirs too (#214); a prepared run lists their files as roots
        self.side_dirs = side_dirs(root, self.src_dir)
        cfg["src_dirs"] = [self.src_dir] + self.side_dirs
        if tsc and self.side_dirs:
            cfg["extra_files"] = list(dict.fromkeys([*(cfg.get("extra_files") or []), *side_files(root, self.side_dirs)]))

    def contribute(self, project: Project, builder: GraphBuilder, ctx) -> dict:
        st = {"prepared": self.prepared, "src_dir": self.src_dir, "side_dirs": getattr(self, "side_dirs", []), "pages": 0, "layouts": 0, "ui_global": 0,
              "i18n_keys_used": 0, "i18n_keys_defined": 0}
        if self.generated:
            st["generated_types"] = self.generated
        if not ctx or not ctx.facts:
            return {**st, "status": "no facts"}
        layouts = {}
        for n in builder.nodes.values():
            if n.lang != "ts" or not n.file:
                continue
            if n.kind == "page":
                route = nuxt_route(n.file, self.src_dir)
                n.attrs["route"] = route
                n.name = route
                n.fqn = route
                n.entry_kind = "ui_page"
                st["pages"] += 1
            elif n.kind == "layout":
                layouts[Path(n.file).stem] = n.id
                n.entry_kind = "ui_global"
                st["layouts"] += 1
            elif n.kind == "app" or (n.kind == "module" and (n.attrs.get("file_kind") == "plugin" or
                                                             (n.attrs.get("file_kind") == "middleware" and ".global." in n.file))):
                n.entry_kind = "ui_global"
                st["ui_global"] += 1
        meta = ctx.facts.get("page_meta") or {}
        for n in list(builder.nodes.values()):
            if n.kind != "page":
                continue
            lay = (meta.get(n.file) or {}).get("layout") or "default"
            if lay in layouts:
                builder.add_edge(n.id, layouts[lay], "USES_LAYOUT", file=n.file, line=1,
                                 confidence="exact" if n.file in meta and meta[n.file].get("layout") else "resolved")
        # i18n: keys used in code/templates, and where they are defined (#182)
        defs, locales, default = i18n_definitions(project.root, self.src_dir, ctx.facts.get("sfc_i18n") or {})
        used: dict[str, int] = {}
        for u in ctx.facts.get("i18n") or []:
            used[u["key"]] = used.get(u["key"], 0) + 1
        for u in ctx.facts.get("i18n") or []:
            key = u["key"]
            ds = defs.get(key) or []
            home = next((d for d in ds if default and d["locale"] == default), ds[0] if ds else None)
            attrs = {"defined_in": list(dict.fromkeys(d["file"] for d in ds)), "definitions": ds, "uses": used[key]}
            have = {d["locale"] for d in ds if d["locale"]}
            if default and default in have:
                missing = sorted(set(locales) - have)
                if missing:
                    attrs["missing_locales"] = missing
            nid = builder.add_node("i18n", key, key, lang="ts", file=home["file"] if home else None,
                                   line=home["line"] if home else None, attrs=attrs)
            builder.add_edge(u["src"], nid, "USES_I18N", file=u["file"], line=u["line"],
                             confidence="exact" if key in defs else "heuristic")
        defined = defs
        st["i18n_locales"] = sorted(locales)
        st["i18n_default_locale"] = default
        st["i18n_keys_used"] = len({u["key"] for u in ctx.facts.get("i18n") or []})
        st["nitro"] = self._nitro(project, builder)
        st["i18n_keys_defined"] = len(defined)
        st["i18n_used_but_undefined"] = len({u["key"] for u in ctx.facts.get("i18n") or []} - set(defined))
        return st

    # ------------------------------------------------------------ Nitro server routes (#216)
    def _nitro(self, project: Project, b: GraphBuilder) -> dict:
        from ..tsweb.common import add_route
        st = {"routes": 0, "middleware": 0, "plugins": 0, "proxy_routes": 0, "linked_calls": 0}
        self._mod_refs = None
        root = project.root
        dirs = server_dirs(root, self.src_dir)
        layer_dirs = [d for d in layer_server_dirs(root, self.src_dir) if d not in dirs]
        cfg_path = _config_path(root)
        cfg = {}
        if cfg_path:
            try:
                cfg = _find_config_object(cfg_path.read_text(errors="replace"))
            except Exception:   # noqa: BLE001
                cfg = {}
        cfg_handlers = config_handlers(cfg)
        if not dirs and not layer_dirs and not cfg_handlers:
            return st
        dirs = dirs + layer_dirs
        files = sorted({n.file for n in b.nodes.values() if n.lang == "ts" and n.kind == "module" and n.file})

        def read(f: str) -> str:
            try:
                return (root / f).read_text(errors="replace")
            except OSError:
                return ""

        def handler(f: str) -> tuple[str | None, str]:
            for nid in (f"function:{f}#default", f"function:{f}#handler"):
                if b.has(nid):
                    return nid, "exact"
            return (f"module:{f}", "resolved") if b.has(f"module:{f}") else (None, "heuristic")

        def label(f: str, kind: str):
            for nid in (f"module:{f}", handler(f)[0]):
                if nid and b.has(nid):
                    b.nodes[nid].attrs["nitro"] = kind

        middleware, routes = [], []
        for d in dirs:
            for f in files:
                if not f.startswith(d + "/"):
                    continue
                rest = f[len(d) + 1:]
                top, _, inner = rest.partition("/")
                if top == "middleware" and inner and nitro_route(inner) is not None:
                    h, _c = handler(f)
                    if h:
                        label(f, "middleware")
                        middleware.append((h, f"nitro:{_NITRO_EXT.sub('', inner)}", "resolved", path_guard(read(f))))
                        st["middleware"] += 1
                elif top == "plugins" and inner and nitro_route(inner) is not None:
                    h, _c = handler(f)
                    if h:
                        label(f, "plugin")
                        b.nodes[h].entry_kind = b.nodes[h].entry_kind or "main"
                        st["plugins"] += 1
                elif top in ("api", "routes") and inner:
                    r = nitro_route(inner, "/api" if top == "api" else "")
                    if r:
                        routes.append((f, top, r))
        # nuxt.config serverHandlers / nitro.handlers: { route, method, handler: '~~/server/handlers/x.ts' }
        for ch in cfg_handlers:
            hf = self._config_handler_file(root, ch["handler"], set(files))
            if not hf:
                st["config_handlers_unresolved"] = st.get("config_handlers_unresolved", 0) + 1
                continue
            if ch["middleware"] or not ch["route"]:
                h, _c = handler(hf)
                if h:
                    label(hf, "middleware")
                    middleware.append((h, f"nitro:{ch['handler']}", "resolved", path_guard(read(hf))))
                    st["middleware"] += 1
                continue
            method = (ch["method"] or "ANY").upper()
            uri = re.sub(r":(\w+)", r"{\1}", "/" + ch["route"].strip("/"))
            routes.append((hf, ch["from"], (method, uri)))
        rules = proxy_rules(cfg)

        def scoped(uri: str) -> list[tuple]:
            out = []
            for m in middleware:
                prefix = m[3] if len(m) > 3 else None
                if prefix is None:
                    out.append(m[:3])
                elif uri == prefix.rstrip("/") or uri.startswith(prefix.rstrip("/") + "/"):
                    out.append((m[0], m[1], "heuristic"))     # early return outside the prefix (#217)
            return out

        for f, top, (method, uri) in routes:
            h, conf = handler(f)
            src = read(f)
            hooks, ref = object_hooks(src)
            if ref and b.has(f"function:{f}#{ref}"):
                h, conf = f"function:{f}#{ref}", "exact"
            line = (b.nodes[h].line or 1) if h else 1
            attrs = {"file_route": top in ("api", "routes"), "nitro_dir": top,
                     "method_from": ("file suffix" if top in ("api", "routes") else "config") if method != "ANY" else "any"}
            if top not in ("api", "routes"):
                attrs["registered_in"] = top
            if _PROXY_CALL.search(src):
                attrs["proxy"] = True
            for pat, target in rules.items():
                if rule_covers(pat, uri):
                    attrs["proxy"] = True
                    attrs["proxy_rule"] = pat
                    if target != "?":
                        attrs["proxy_target"] = target
                    break
            if attrs.get("proxy"):
                st["proxy_routes"] += 1
            if not h or h.startswith("module:"):
                attrs["handler_unresolved"] = True
            mws = scoped(uri)
            for hook, name in hooks:
                nid = self._hook_node(b, f, hook, name)
                if nid:
                    mws.append((nid, f"{hook}:{name if name != '<inline>' else 'inline'}", "exact"))
            add_route(b, method, uri, [h] if h else [], f, line, "nuxt", conf, attrs, mws)
            label(f, "route")
            st["routes"] += 1
        st["linked_calls"] = self._link_nitro(b)
        return st

    def _config_handler_file(self, root: Path, spec: str, files: set[str]) -> str | None:
        """A nuxt.config handler path (`~~/server/x.ts`, `~/server/x`, `./server/x.ts`) -> indexed file."""
        s = spec.strip()
        src = self.src_dir.strip("/") if self.src_dir not in ("", ".") else ""
        bases = []
        for alias, base in (("~~/", ""), ("@@/", ""), ("~/", src), ("@/", src), ("./", ""), ("/", "")):
            if s.startswith(alias):
                bases = [base, ""] if base else [""]
                s = s[len(alias):]
                break
        else:
            bases = [""]
        for base in bases:
            stem = f"{base}/{s}" if base else s
            for cand in [stem] + [stem + ext for ext in (".ts", ".js", ".mjs", ".mts")] + [f"{stem}/index.ts"]:
                if cand in files:
                    return cand
        return None

    def _hook_node(self, b: GraphBuilder, f: str, hook: str, name: str) -> str | None:
        """onRequest / onBeforeResponse function of an object-form handler: inline, local, or an imported function."""
        local = f"function:{f}#{hook if name == '<inline>' else name}"
        if b.has(local):
            return local
        if name == "<inline>":
            return None
        if getattr(self, "_mod_refs", None) is None:
            self._mod_refs = defaultdict(list)
            for e in b.edges.values():
                if e.kind in ("CALLS", "REFERENCES") and e.src.startswith("module:"):
                    self._mod_refs[e.src].append(e.dst)
        for dst in self._mod_refs.get(f"module:{f}", ()):
            if dst.endswith(f"#{name}") and b.has(dst):
                return dst
        return None

    def _link_nitro(self, b: GraphBuilder) -> int:
        """Same-origin client calls of this app (relative URLs: `$fetch('/api/x')`, `useFetch`) -> its Nitro routes.
        Proxy routes (proxyRequest / sendProxy) forward elsewhere and are not link targets."""
        from ...link import match_endpoint
        routes = []
        for n in b.nodes.values():
            a = n.attrs
            if n.kind == "route" and a.get("framework") == "nuxt" and a.get("uri") and not a.get("proxy"):
                routes.append({"id": n.id, "uri": a["uri"], "method": a["method"], "uris": [("as-declared", a["uri"])],
                               "file": n.file, "line": n.line})
        if not routes:
            return 0
        rmap = {r["id"]: r for r in routes}
        linked = 0
        for n in list(b.nodes.values()):
            a = n.attrs
            if n.kind != "http" or n.lang != "ts" or a.get("origin_kind") != "same-origin":
                continue
            res = match_endpoint(a.get("method", "GET"), a.get("path", ""), routes, "api", None)
            for m in res["matched"]:
                r = rmap[m["route"]]
                b.add_edge(n.id, m["route"], "MATCHES_ROUTE", file=r["file"], line=r["line"], confidence=m["confidence"],
                           client_path=a.get("path"), uri_variant=m["uri_variant"], segments=m["segments"], in_repo=True)
            if res["matched"]:
                linked += 1
        return linked

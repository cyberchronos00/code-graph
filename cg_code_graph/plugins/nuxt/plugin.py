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
  locale files).
"""
from __future__ import annotations

import atexit
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
        cfg["src_dirs"] = [self.src_dir]

    def contribute(self, project: Project, builder: GraphBuilder, ctx) -> dict:
        st = {"prepared": self.prepared, "src_dir": self.src_dir, "pages": 0, "layouts": 0, "ui_global": 0,
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
        # i18n: keys used in code/templates, and where they are defined
        defined = {}
        for d in dict.fromkeys(("i18n/locales", f"{self.src_dir}/locales", "app/locales", "locales")):
            for f in sorted((project.root / d).glob("*.json")) if (project.root / d).exists() else []:
                try:
                    data = json.loads(f.read_text())
                except Exception:
                    continue
                locs = {"en", "ar", "ku", "fr", "de"}
                if isinstance(data, dict) and set(data) and set(data) <= locs:
                    data = next(iter(data.values()))
                for k in flatten(data):
                    defined.setdefault(k, []).append(str(f.relative_to(project.root)))
        for f, keys in (ctx.facts.get("sfc_i18n") or {}).items():
            for k in keys:
                defined.setdefault(k, []).append(f)
        for u in ctx.facts.get("i18n") or []:
            nid = builder.add_node("i18n", u["key"], u["key"], lang="ts", attrs={"defined_in": defined.get(u["key"], [])})
            builder.add_edge(u["src"], nid, "USES_I18N", file=u["file"], line=u["line"],
                             confidence="exact" if u["key"] in defined else "heuristic")
        st["i18n_keys_used"] = len({u["key"] for u in ctx.facts.get("i18n") or []})
        st["i18n_keys_defined"] = len(defined)
        st["i18n_used_but_undefined"] = len({u["key"] for u in ctx.facts.get("i18n") or []} - set(defined))
        return st

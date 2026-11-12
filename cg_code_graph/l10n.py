"""Localization keys: catalogs become `i18n` nodes, literal uses become `USES_I18N` edges.

Catalogs: Apple `.xcstrings` and `.strings`, Android `res/values*/strings.xml`, and JSON locale
files under a `locales` / `locale` / `i18n` / `lang` / `langs` / `translations` / `messages`
directory whose file or parent name is a locale code (`en`, `pt-BR`).

Uses, only when the key is a literal (a template or a variable is not a key):

* Swift `String(localized:)`, `NSLocalizedString`, `LocalizedStringResource`
* SwiftUI `Text("key")` when that key is defined in an Apple catalog
* Android `R.string.name` and `@string/name`
* JavaScript / TypeScript `t("key")`, `$t("key")`, and the same names `te`, `tm`, `rt`

Nuxt already emits `i18n` nodes for its locale files. Definitions already on a node are kept;
this pass adds catalogs that pass does not know and does not add a second edge at the same
file and line.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

from .core.model import EXACT
from .core.paths import rel_dir
from .plugins.nuxt.plugin import flatten, json_key_lines
from .presets import load as load_preset
from .presets import skip_dirs

_MAX_BYTES = 1_500_000
_LOCALE_DIRS = frozenset({"locales", "locale", "i18n", "lang", "langs", "translations", "messages"})
_LOCALE_CODE = re.compile(r"^[a-z]{2,3}(?:[-_](?:[A-Za-z]{2,8}|\d{3}))*$")
_ANDROID_XML = re.compile(r"(?:^|/)res/(values(?:-[^/]+)?)/strings\.xml$")
_ANDROID_LANG = re.compile(r"values-([a-z]{2,3})(?:-r([A-Z]{2}))?$")
_LPROJ = re.compile(r"(?:^|/)([A-Za-z0-9_+-]+)\.lproj(?:/|$)")
_STRING_TAG = re.compile(r"<string\b[^>]*?\bname\s*=\s*([\"'])([^\"']+)\1")
_R_STRING = re.compile(r"(?<![\w])R\.string\.([A-Za-z_][A-Za-z0-9_]*)")
_AT_STRING = re.compile(r"@string/([A-Za-z_][A-Za-z0-9_]*)")
_SW_LOCALIZED = re.compile(r"\bString\s*\(\s*localized:\s*\"((?:\\.|[^\"\\])*)\"")
_SW_NS = re.compile(r"\bNSLocalizedString\s*\(\s*\"((?:\\.|[^\"\\])*)\"")
_SW_RESOURCE = re.compile(r"\bLocalizedStringResource\s*\(\s*\"((?:\\.|[^\"\\])*)\"")
_SW_TEXT = re.compile(r"\bText\s*\(\s*\"((?:\\.|[^\"\\])*)\"")
_JS_CALL = re.compile(
    r"(?<![\w$])(\$te|\$t|tm|rt|te|t)\s*\(\s*(['\"])((?:\\.|(?!\2).){1,300}?)\2")
_OWNER_INNER = frozenset({"function", "method", "constructor", "component", "page"})
_OWNER_OUTER = frozenset({"class", "module", "file", "script"})
_APPLE = frozenset({"xcstrings", "strings"})
_SKIP_SCAN = frozenset({
    ".build", ".dart_tool", ".gradle", ".swiftpm", "Carthage", "DerivedData", "Pods",
    "node_modules", "vendor", "xcuserdata",
})


def apply(project, builder) -> dict | None:
    """Add catalog nodes and literal-use edges. None when the tree has neither."""
    root = Path(project.root)
    skip = set(skip_dirs("common", "scan_skip_dirs"))
    skip |= set(load_preset("swift").get("skip_dirs") or [])
    skip |= set(load_preset("kotlin").get("skip_dirs") or [])
    skip |= _SKIP_SCAN
    defs: dict[str, list[dict]] = defaultdict(list)
    files = 0
    for dp, dn, fn in os.walk(root):
        rd = rel_dir(str(root), dp)
        dn[:] = sorted(d for d in dn if d not in skip and not d.startswith("."))
        for name in sorted(fn):
            rel = f"{rd}/{name}" if rd else name
            kind = _catalog_kind(rel)
            if kind is None:
                continue
            text = _read(os.path.join(dp, name))
            if text is None:
                continue
            rows = _catalog(kind, rel, text)
            if not rows:
                continue
            files += 1
            for key, row in rows:
                defs[key].append(row)
    locales = _locales_of(defs)
    apple = {key for key, rows in defs.items() if any(r.get("format") in _APPLE for r in rows)}
    uses = _uses(root, skip, apple)
    if not defs and not uses:
        return None
    _add_js_functions(root, skip, builder, {u["file"] for u in uses})
    have_edge = {(e.file, e.line, e.dst) for e in builder.edges.values() if e.kind == "USES_I18N"}
    owners = _owners(builder)
    n_new = 0
    for key in sorted(defs):
        if f"i18n:{key}" not in builder.nodes:
            n_new += 1
        _define(builder, key, defs[key], locales)
    n_uses = 0
    linked: set[str] = set()
    for use in uses:
        key = use["key"]
        if not key or "\n" in key or len(key) > 300:
            continue
        if f"i18n:{key}" not in builder.nodes:
            n_new += 1
            builder.add_node("i18n", key, key, lang=use["lang"], file=use["file"], line=use["line"],
                             attrs={"definitions": [], "defined_in": [], "uses": 0})
        nid = f"i18n:{key}"
        slot = (use["file"], use["line"], nid)
        if slot in have_edge:
            continue
        src = _owner(owners, use["file"], use["line"]) or builder.add_node(
            "module", f"l10n:{use['file']}", name=use["file"], file=use["file"], line=1, lang=use["lang"])
        builder.add_edge(src, nid, "USES_I18N", file=use["file"], line=use["line"], confidence=EXACT,
                         via=use["via"])
        have_edge.add(slot)
        linked.add(nid)
        n_uses += 1
    for nid in linked:
        node = builder.nodes[nid]
        if _nuxt_node(node):
            continue
        node.attrs["uses"] = sum(1 for e in builder.edges.values() if e.kind == "USES_I18N" and e.dst == nid)
    return {"files": files, "keys": len(defs), "uses": n_uses, "nodes_added": n_new}


def _nuxt_node(node) -> bool:
    """A node Nuxt already filled: its definitions have no catalog format, and its use count stays."""
    return any("format" not in d for d in (node.attrs.get("definitions") or []))


def _catalog_kind(rel: str) -> str | None:
    if rel.endswith(".xcstrings"):
        return "xcstrings"
    if rel.endswith(".strings"):
        return "strings"
    if _ANDROID_XML.search(rel):
        return "android"
    if rel.endswith(".json") and _json_locale(rel) is not None:
        return "json"
    return None


def _json_locale(rel: str) -> str | None:
    parts = rel.split("/")
    if len(parts) < 2 or not any(p in _LOCALE_DIRS for p in parts[:-1]):
        return None
    stem = parts[-1][:-5]
    parent = parts[-2]
    if _LOCALE_CODE.match(parent):
        return parent
    if _LOCALE_CODE.match(stem):
        return stem
    return None


def _catalog(kind: str, rel: str, text: str) -> list[tuple[str, dict]]:
    if kind == "xcstrings":
        return _xcstrings(rel, text)
    if kind == "strings":
        return _strings(rel, text)
    if kind == "android":
        return _android(rel, text)
    return _json_catalog(rel, text)


def _xcstrings(rel: str, text: str) -> list[tuple[str, dict]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    strings = data.get("strings") if isinstance(data, dict) else None
    if not isinstance(strings, dict):
        return []
    lines = json_key_lines(text)
    source = data.get("sourceLanguage") if isinstance(data.get("sourceLanguage"), str) else None
    out = []
    for key, body in strings.items():
        if not isinstance(key, str) or not key:
            continue
        locs = []
        if isinstance(body, dict) and isinstance(body.get("localizations"), dict):
            locs = [loc for loc in body["localizations"] if isinstance(loc, str)]
        if not locs:
            locs = [source]
        line = lines.get(f"strings.{key}") or 1
        for loc in locs:
            out.append((key, _row(rel, line, loc, "xcstrings")))
    return out


def _strings(rel: str, text: str) -> list[tuple[str, dict]]:
    locale = _lproj_locale(rel)
    out = []
    for key, line in _strings_entries(text):
        if key:
            out.append((key, _row(rel, line, locale, "strings")))
    return out


def _android(rel: str, text: str) -> list[tuple[str, dict]]:
    m = _ANDROID_XML.search(rel)
    dirname = m.group(1) if m else "values"
    lang = _ANDROID_LANG.fullmatch(dirname)
    locale = None
    if lang:
        locale = lang.group(1) + (("-" + lang.group(2)) if lang.group(2) else "")
    out = []
    for match in _STRING_TAG.finditer(text):
        key = match.group(2)
        if key:
            out.append((key, _row(rel, text.count("\n", 0, match.start()) + 1, locale, "android")))
    return out


def _json_catalog(rel: str, text: str) -> list[tuple[str, dict]]:
    locale = _json_locale(rel)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    lines = json_key_lines(text)
    return [(key, _row(rel, lines.get(key) or 1, locale, "json"))
            for key in flatten(data) if isinstance(key, str) and key]


def _row(rel: str, line: int, locale: str | None, fmt: str) -> dict:
    row = {"locale": locale, "file": rel, "line": line, "format": fmt}
    return row


def _lproj_locale(rel: str) -> str | None:
    m = _LPROJ.search(rel)
    if not m:
        return None
    name = m.group(1)
    if name.lower() == "base":
        return "base"
    return name


def _strings_entries(text: str) -> list[tuple[str, int]]:
    """Quoted keys of an Apple `.strings` file, comments skipped."""
    out = []
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] == "\n":
                    line += 1
                i += 1
            i = min(n, i + 2)
            continue
        if c == '"':
            key, i, line, key_line = _quoted(text, i, line)
            while i < n and text[i] in " \t\r\n":
                if text[i] == "\n":
                    line += 1
                i += 1
            if i < n and text[i] == "=":
                out.append((key, key_line))
            continue
        i += 1
    return out


def _quoted(text: str, i: int, line: int) -> tuple[str, int, int, int]:
    key_line = line
    i += 1
    buf = []
    while i < len(text) and text[i] != '"':
        if text[i] == "\\" and i + 1 < len(text):
            buf.append(text[i:i + 2])
            if text[i + 1] == "\n":
                line += 1
            i += 2
            continue
        if text[i] == "\n":
            line += 1
        buf.append(text[i])
        i += 1
    if i < len(text) and text[i] == '"':
        i += 1
    raw = "".join(buf)
    try:
        key = json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        key = raw
    return key, i, line, key_line


def _locales_of(defs: dict[str, list[dict]]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    for rows in defs.values():
        for row in rows:
            if row.get("locale"):
                found[row.get("format") or ""].add(row["locale"])
    return found


def _define(builder, key: str, rows: list[dict], locales: dict[str, set[str]]) -> None:
    nid = f"i18n:{key}"
    node = builder.nodes.get(nid)
    rows = sorted(rows, key=lambda d: (d["file"], d["line"], d.get("locale") or "", d.get("format") or ""))
    if node is None:
        home = _home(rows)
        attrs = {"definitions": rows, "defined_in": _defined_in(rows), "uses": 0}
        missing = _missing(rows, locales)
        if missing:
            attrs["missing_locales"] = missing
        lang = {"xcstrings": "swift", "strings": "swift", "android": "kotlin", "json": "javascript"}.get(
            home.get("format") or "", "resource")
        builder.add_node("i18n", key, key, lang=lang, file=home["file"], line=home["line"], attrs=attrs)
        return
    prev = node.attrs.setdefault("definitions", [])
    seen = {(d.get("locale"), d.get("file"), d.get("line")) for d in prev}
    added = False
    for row in rows:
        token = (row.get("locale"), row.get("file"), row.get("line"))
        if token in seen:
            continue
        prev.append(row)
        seen.add(token)
        added = True
    if added:
        node.attrs["defined_in"] = _defined_in(prev)
        if "missing_locales" not in node.attrs and all("format" in d for d in prev):
            missing = _missing(prev, locales)
            if missing:
                node.attrs["missing_locales"] = missing
    if not node.file:
        home = _home(prev or rows)
        node.file = home["file"]
        node.line = home["line"]


def _home(rows: list[dict]) -> dict:
    ens = [d for d in rows if (d.get("locale") or "").lower().startswith("en")]
    defaults = [d for d in rows if not d.get("locale")]
    pool = ens or defaults or rows
    return sorted(pool, key=lambda d: (d["file"], d["line"], d.get("locale") or ""))[0]


def _defined_in(rows: list[dict]) -> list[str]:
    return list(dict.fromkeys(d["file"] for d in rows if d.get("file")))


def _missing(rows: list[dict], locales: dict[str, set[str]]) -> list[str]:
    """Named locales of a catalog format that this key does not define.

    A `values/strings.xml` entry has no locale. It still counts as present for that format, so a
    translation that exists on another key of the same format is missing here.
    """
    missing: set[str] = set()
    present = {row.get("format") or "" for row in rows}
    have: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("locale"):
            have[row.get("format") or ""].add(row["locale"])
    for fmt in present:
        missing |= {loc for loc in locales.get(fmt, ()) if loc not in have.get(fmt, set())}
    return sorted(missing)


def _uses(root: Path, skip: set[str], apple: set[str]) -> list[dict]:
    out = []
    for dp, dn, fn in os.walk(root):
        rd = rel_dir(str(root), dp)
        dn[:] = sorted(d for d in dn if d not in skip and not d.startswith("."))
        for name in sorted(fn):
            rel = f"{rd}/{name}" if rd else name
            kind = _use_kind(name, rel)
            if kind is None:
                continue
            text = _read(os.path.join(dp, name))
            if text is None:
                continue
            if kind == "swift":
                out.extend(_swift_uses(rel, text, apple))
            elif kind == "jvm":
                out.extend(_pattern_uses(rel, text, _R_STRING, "R.string", "kotlin"))
            elif kind == "xml":
                out.extend(_pattern_uses(rel, text, _AT_STRING, "@string", "kotlin"))
            else:
                out.extend(_js_uses(rel, text))
    return out


def _use_kind(name: str, rel: str) -> str | None:
    if name.endswith(".swift"):
        return "swift"
    if name.endswith((".kt", ".kts", ".java")):
        return "jvm"
    if name.endswith(".xml") and "/res/" in f"/{rel}" and not _ANDROID_XML.search(rel):
        return "xml"
    if name.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".astro")):
        return "js"
    return None


def _swift_uses(rel: str, text: str, apple: set[str]) -> list[dict]:
    out = []
    for rx, via in ((_SW_LOCALIZED, "String(localized:)"), (_SW_NS, "NSLocalizedString"),
                    (_SW_RESOURCE, "LocalizedStringResource")):
        out.extend(_quoted_uses(rel, text, rx, via, "swift", None))
    out.extend(_quoted_uses(rel, text, _SW_TEXT, "Text", "swift", apple))
    return out


def _quoted_uses(rel: str, text: str, rx: re.Pattern, via: str, lang: str, allow: set[str] | None) -> list[dict]:
    masked = _mask_comments(text)
    out = []
    for match in rx.finditer(masked):
        key = _unescape(match.group(1))
        if allow is not None and key not in allow:
            continue
        out.append({"key": key, "file": rel, "line": text.count("\n", 0, match.start()) + 1,
                    "via": via, "lang": lang})
    return out


def _pattern_uses(rel: str, text: str, rx: re.Pattern, via: str, lang: str) -> list[dict]:
    masked = _mask_comments(text)
    out = []
    for match in rx.finditer(masked):
        out.append({"key": match.group(1), "file": rel, "line": text.count("\n", 0, match.start()) + 1,
                    "via": via, "lang": lang})
    return out


def _js_uses(rel: str, text: str) -> list[dict]:
    masked = _mask_comments(text)
    out = []
    for match in _JS_CALL.finditer(masked):
        name = match.group(1)
        key = _unescape(match.group(3))
        if not key:
            continue
        out.append({"key": key, "file": rel, "line": text.count("\n", 0, match.start()) + 1,
                    "via": f"{name}()", "lang": "javascript"})
    return out


def _unescape(raw: str) -> str:
    try:
        return json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return raw


def _mask_comments(text: str) -> str:
    """Copy of `text` with comments replaced by spaces, so indexes and line numbers stay."""
    chars = list(text)
    i, n = 0, len(chars)
    state = "code"
    quote = ""
    while i < n:
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                while i < n and chars[i] != "\n":
                    chars[i] = " "
                    i += 1
                continue
            if c == "/" and nxt == "*":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "block"
                continue
            if c in ("'", '"'):
                state = "str"
                quote = c
        elif state == "block":
            if c == "*" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "code"
                continue
            if c != "\n":
                chars[i] = " "
        elif state == "str":
            if c == "\\":
                i += 2
                continue
            if c == quote:
                state = "code"
        i += 1
    return "".join(chars)


_JS_FN = re.compile(r"(?m)^(?:export\s+)?(?:async\s+)?function\s+(\w+)")


def _add_js_functions(root: Path, skip: set[str], builder, files: set[str]) -> None:
    """Function spans for literal `t()` calls when the language plugin did not index the file.

    Impact lists `function` and `module` nodes. A `function name()` whose braces contain the call
    becomes the edge source. Other shapes stay on a module node for that file.
    """
    for rel in sorted(files):
        if not rel.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".astro")):
            continue
        text = _read(root / rel)
        if not text:
            continue
        for name, start, end in _js_function_spans(text):
            builder.add_node("function", f"{rel}#{name}", name=name, file=rel, line=start, end_line=end,
                             lang="javascript")


def _js_function_spans(text: str) -> list[tuple[str, int, int]]:
    spans = []
    for match in _JS_FN.finditer(text):
        brace = text.find("{", match.end())
        if brace < 0:
            continue
        end = _close_brace(text, brace)
        spans.append((match.group(1), text.count("\n", 0, match.start()) + 1,
                      text.count("\n", 0, end) + 1))
    return spans


def _close_brace(text: str, open_at: int) -> int:
    depth = 0
    i, n = open_at, len(text)
    state = "code"
    quote = ""
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                i = text.find("\n", i)
                if i < 0:
                    return n - 1
            elif c == "/" and nxt == "*":
                close = text.find("*/", i + 2)
                i = n if close < 0 else close + 2
                continue
            elif c in ("'", '"', "`"):
                state = "str"
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i
        elif state == "str":
            if c == "\\":
                i += 2
                continue
            if c == quote:
                state = "code"
        i += 1
    return n - 1


def _owners(builder) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for node in builder.nodes.values():
        if node.file and node.line and node.kind in _OWNER_INNER | _OWNER_OUTER:
            out[node.file].append(node)
    return out


def _owner(owners: dict[str, list], file: str, line: int) -> str | None:
    best = None
    best_key = None
    for node in owners.get(file) or ():
        end = node.end_line or node.line
        if not (node.line <= line <= end):
            continue
        rank = 0 if node.kind in _OWNER_INNER else 1
        key = (rank, end - node.line, -node.line, node.id)
        if best_key is None or key < best_key:
            best, best_key = node.id, key
    return best


def _read(path: str) -> str | None:
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return None
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", "replace")
    return raw.decode("utf-8", "replace")

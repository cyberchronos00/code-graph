"""Markdown pages and content collections for the Astro layer.

Pages under ``pages/`` ending in ``.md`` / ``.mdx`` / ``.html`` (and the other Markdown
suffixes) become page nodes. A frontmatter ``layout`` is RENDERS, and MDX imports,
elements and calls of imported functions are edges. ``content.config.ts`` (or the legacy
``content/config.ts``) defines ``table`` nodes that ``getCollection`` / ``getEntry`` /
``render`` read.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from ...core.plugin import GraphBuilder, Project
from .plugin import _pages_prefix, astro_route

_MD_KIND = {
    ".md": "md", ".markdown": "md", ".mdown": "md", ".mkdn": "md", ".mkd": "md", ".mdwn": "md",
    ".mdx": "mdx", ".html": "html",
}
_ENTRY_EXT = (".md", ".mdx", ".mdoc", ".markdown")       # legacy `type: 'content'` entries
_DATA_EXT = (".json", ".yaml", ".yml", ".toml")           # legacy `type: 'data'` entries
_READ_FNS = ("getCollection", "getEntry", "getEntries", "getLiveCollection", "getLiveEntry")
_CFG_NAMES = ("content.config.ts", "content.config.mts", "content.config.js", "content.config.mjs")
_LEGACY_NAMES = ("config.ts", "config.js", "config.mts")
_KV = re.compile(r"""^(?P<q>['\"]?)(?P<key>[A-Za-z_][\w-]*)(?P=q)\s*:\s*(?P<val>.*?)\s*$""")
_IMP_BOTH = re.compile(
    r"""^import\s+([A-Za-z_$][\w$]*)\s*,\s*\{([^}]+)\}\s*from\s+['\"]([^'\"]+)['\"]""")
_IMP_DEF = re.compile(r"""^import\s+([A-Za-z_$][\w$]*)\s+from\s+['\"]([^'\"]+)['\"]""")
_IMP_NAMED = re.compile(r"""^import\s*\{([^}]+)\}\s*from\s+['\"]([^'\"]+)['\"]""")
_TOML_KV = re.compile(r"""^(?P<q>['\"]?)(?P<key>[A-Za-z_][\w-]*)(?P=q)\s*=\s*(?P<val>.*?)\s*$""")
_DEF = re.compile(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*defineCollection\s*\(")
_COLLECTIONS = re.compile(r"export\s+const\s+collections\s*(?::[^=]+)?=\s*\{")
_MD_LINK = re.compile(r"""\]\(\s*<?(/[^)\s>]*)|\bhref\s*=\s*['\"](/[^'\"]*)['\"]""")
_INLINE_CODE = re.compile(r"`[^`]*`")
_FENCE = re.compile(r"^\s*(```|~~~)")
_FILE_KINDS = ("page", "layout", "component", "module")
_RENDER_KINDS = ("component", "layout", "page")


def frontmatter(text: str) -> dict[str, str]:
    """Top-level ``key: value`` lines of a leading ``---`` (YAML) or ``key = value`` lines of a ``+++`` (TOML)
    block, quotes and trailing comments stripped. No block, or an unclosed one, is ``{}``."""
    return {k: v for k, (v, _n) in frontmatter_lines(text).items()}


def frontmatter_lines(text: str) -> dict[str, tuple[str, int]]:
    lines, _end, toml = _fm_block(text)
    out: dict[str, tuple[str, int]] = {}
    for n, raw in lines:
        if toml and raw.lstrip().startswith("["):
            break                                   # a [table]: the rest is not top level
        if not raw or raw[0] in " \t":
            continue                                # nested YAML, or a continuation line
        m = (_TOML_KV if toml else _KV).match(raw.rstrip())
        if m:
            out.setdefault(m.group("key"), (_scalar(m.group("val")), n))
    return out


def _fm_block(text: str) -> tuple[list[tuple[int, str]], int, bool]:
    """(body lines with numbers, closing line, is TOML). Blank lines may precede the opening fence, as in Astro."""
    raw = text.splitlines()
    i = 0
    while i < len(raw) and not raw[i].strip().lstrip("\ufeff"):
        i += 1
    if i >= len(raw):
        return [], 0, False
    m = re.match(r"\ufeff?(---|\+\+\+)\s*$", raw[i])
    if not m:
        return [], 0, False
    fence = m.group(1)
    body = []
    for j in range(i + 1, len(raw)):
        if raw[j].rstrip() == fence:
            return body, j + 1, fence == "+++"
        body.append((j + 1, raw[j]))
    return [], 0, False                             # unclosed: Astro rejects it, so read nothing


def _fm_lines(text: str) -> tuple[list[tuple[int, str]], int]:
    body, end, _toml = _fm_block(text)
    return body, end


def _scalar(v: str) -> str:
    v = v.strip()
    if v[:1] in ("'", '"'):
        q = v[0]
        close = v.find(q, 1)
        return v[1:close] if close > 0 else v[1:]
    return re.sub(r"\s+#.*$", "", v).strip()


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def _norm_rel(v: str) -> str:
    s = v.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.strip("/")


def _resolve(src_file: str, spec: str) -> str | None:
    spec = _unquote(spec.strip())
    if spec.startswith("/"):
        return _norm_rel(spec)
    if not spec.startswith("."):
        return None
    rel = os.path.normpath(str(Path(src_file).parent / spec)).replace("\\", "/")
    return rel[2:] if rel.startswith("./") else rel


def _file_candidates(rel: str) -> list[str]:
    exts = (".astro", ".ts", ".tsx", ".mts", ".js", ".jsx", ".mjs", ".md", ".mdx", ".html")
    out = [rel]
    for ext in exts:
        out.append(rel + ext)
        out.append(rel + "/index" + ext)
    return out


def _existing(root: Path, rel: str) -> str | None:
    for c in _file_candidates(rel):
        if (root / c).is_file():
            return c
    return None


def _by_file(b: GraphBuilder) -> dict[str, list]:
    out: dict[str, list] = {}
    for n in b.nodes.values():
        if n.file:
            out.setdefault(n.file.replace("\\", "/"), []).append(n)
    return out


def _node_for(b: GraphBuilder, path: str, kinds=_FILE_KINDS) -> str | None:
    for kind in kinds:
        nid = f"{kind}:{path}"
        if b.has(nid):
            return nid
    for n in b.nodes.values():
        if (n.file or "").replace("\\", "/") == path and n.kind in kinds:
            return n.id
    return None


def _expand_braces(pat: str) -> list[str]:
    m = re.search(r"\{([^{}]+)\}", pat)
    if not m:
        return [pat]
    return [x for o in m.group(1).split(",") for x in _expand_braces(pat[:m.start()] + o.strip() + pat[m.end():])]


def _glob_re(pat: str) -> str:
    i, out = 0, []
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return "".join(out)


def count_glob(root: Path, base: str, pattern: str) -> int:
    folder = root / base if base else root
    if not folder.is_dir():
        return 0
    regs = [re.compile(_glob_re(p)) for p in _expand_braces(pattern)]
    n = 0
    for p in folder.rglob("*"):
        if p.is_file() and any(r.fullmatch(p.relative_to(folder).as_posix()) for r in regs):
            n += 1
    return n


def _matching_paren(src: str, open_at: int) -> int:
    """Index of the bracket closing the one at ``open_at`` (``(`` or ``{``), skipping strings."""
    opener = src[open_at]
    closer = ")" if opener == "(" else "}"
    depth, i, n = 0, open_at, len(src)
    while i < n:
        c = src[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == q:
                    break
                i += 1
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def _line_at(src: str, idx: int) -> int:
    return src.count("\n", 0, idx) + 1


def read_collections(root, src_dir: str = "src") -> list[dict]:
    """Collections declared in ``content.config.*`` or legacy ``content/config.*``."""
    root = Path(root)
    pre = f"{src_dir}/" if src_dir else ""
    path = next((root / (pre + n) for n in _CFG_NAMES if (root / (pre + n)).is_file()), None)
    if path is None:
        legacy = pre + "content/"
        path = next((root / (legacy + n) for n in _LEGACY_NAMES if (root / (legacy + n)).is_file()), None)
    if path is None:
        return []
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rel_file = path.relative_to(root).as_posix()
    legacy_file = rel_file.endswith(tuple("content/" + n for n in _LEGACY_NAMES))
    src = _blank_comments(src)
    by_var: dict[str, dict] = {}
    for m in _DEF.finditer(src):
        by_var[m.group(1)] = _collection_info(src, m.group(1), m.end() - 1, m.start(), rel_file)
    em = _COLLECTIONS.search(src)
    if not em:
        return []
    obj_close = _matching_paren(src, em.end() - 1)
    out = []
    for part, off in _top_level_parts(src, em.end(), obj_close):
        km = re.match(r"""\s*(?:(['\"])([^'\"]+)\1|([A-Za-z_$][\w$]*))\s*(?::\s*(.*))?$""", part, re.S)
        if not km:
            continue
        name = km.group(2) or km.group(3)
        value = (km.group(4) or "").strip()
        if not value:
            info = by_var.get(km.group(3) or "")
        elif re.fullmatch(r"[A-Za-z_$][\w$]*", value):
            info = by_var.get(value)
        else:
            dm = re.search(r"\bdefineCollection\s*\(", part)
            info = _collection_info(src, name, off + dm.end() - 1, off + dm.start(), rel_file) if dm else None
        if not info:
            continue
        item = dict(info)
        item["name"] = name
        if item["loader"] is None and (item["type"] or legacy_file):
            item["type"] = item["type"] or "content"
            item["base"] = f"{pre}content/{name}"
        if item["loader"] == "glob" and item["pattern"] and item["base"] is not None:
            item["entries"] = count_glob(root, item["base"], item["pattern"])
        elif item["loader"] is None and item["base"]:
            folder = root / item["base"]
            exts = _DATA_EXT if item["type"] == "data" else _ENTRY_EXT
            item["entries"] = sum(1 for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in exts) if folder.is_dir() else 0
        out.append(item)
    return out


def _top_level_parts(src: str, start: int, end: int):
    """Comma-separated members of an object literal body, with their offsets."""
    depth, i, part_at = 0, start, start
    while i < end:
        c = src[i]
        if c in "'\"`":
            q = c
            i += 1
            while i < end and src[i] != q:
                i += 2 if src[i] == "\\" else 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            yield src[part_at:i], part_at
            part_at = i + 1
        i += 1
    if src[part_at:end].strip():
        yield src[part_at:end], part_at


def _collection_info(src: str, var: str, open_at: int, start: int, rel_file: str) -> dict:
    """Loader, glob pattern / base, file path, legacy type and reference() targets of one defineCollection(...)."""
    close = _matching_paren(src, open_at)
    span = src[open_at:close]
    info = {
        "var": var, "file": rel_file, "line": _line_at(src, start),
        "loader": None, "pattern": None, "base": None, "path": None,
        "entries": None, "type": None, "relations": [],
    }
    gm = re.search(r"\bglob\s*\(\s*\{", span)
    fm = re.search(r"\bfile\s*\(\s*['\"]([^'\"]+)['\"]", span)
    if gm:
        body = span[gm.end():_matching_paren(span, gm.end() - 1)]
        pm = re.search(r"""\bpattern\s*:\s*['\"]([^'\"]+)['\"]""", body)
        bm = re.search(r"""\bbase\s*:\s*['\"]([^'\"]+)['\"]""", body)
        info["loader"] = "glob"
        if pm:
            info["pattern"] = pm.group(1)
        if bm:
            info["base"] = _norm_rel(bm.group(1))
    elif fm:
        info["loader"] = "file"
        info["path"] = _norm_rel(fm.group(1))
    elif re.search(r"\bloader\s*:", span):
        lm = re.search(r"\bloader\s*:\s*(?!async\b|function\b)([A-Za-z_$][\w$]*)", span)
        info["loader"] = lm.group(1) if lm else "custom"
    tm = re.search(r"""\btype\s*:\s*['\"](content|data)['\"]""", span)
    if tm:
        info["type"] = tm.group(1)
    for rm in re.finditer(r"""\breference\s*\(\s*['\"]([^'\"]+)['\"]""", span):
        info["relations"].append((rm.group(1), _line_at(src, open_at + rm.start())))
    return info


def _blank_comments(src: str) -> str:
    """``//`` and ``/* */`` comments as spaces (newlines kept, so offsets and lines do not move); strings are kept."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", src[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _body_lines(text: str):
    """(line number, line) outside the frontmatter and fenced code blocks, inline code spans blanked."""
    _fm, fm_end = _fm_lines(text)
    fence = None
    for i, line in enumerate(text.splitlines(), start=1):
        if i <= fm_end:
            continue
        m = _FENCE.match(line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif m.group(1) == fence:
                fence = None
            continue
        if fence is None:
            yield i, _INLINE_CODE.sub(lambda c: " " * len(c.group(0)), line)


def _mdx_imports(lines: list[tuple[int, str]]):
    """(line, statement) for top-level ESM imports, joining an import that spans several lines."""
    i = 0
    while i < len(lines):
        n, line = lines[i]
        if re.match(r"import\b", line) and not re.match(r"import\s*\(", line):
            stmt, j = line.strip(), i
            while not re.search(r"""\bfrom\s*['\"][^'\"]+['\"]|^import\s*['\"]""", stmt) and j + 1 < len(lines) and j - i < 20:
                j += 1
                stmt += " " + lines[j][1].strip()
            yield n, stmt, range(i, j + 1)
            i = j + 1
        else:
            i += 1


def _mdx_edges(project: Project, b: GraphBuilder, rel: str, text: str, page_id: str) -> None:
    lines = list(_body_lines(text))
    defaults: dict[str, str] = {}
    named: dict[str, tuple[str, str]] = {}  # local -> (export name, resolved file)
    skip: set[int] = set()
    for i, stmt, idx in _mdx_imports(lines):
        skip.update(idx)
        if re.match(r"import\s+type\b", stmt):
            continue
        spec = None
        default = None
        names = ""
        both = _IMP_BOTH.match(stmt)
        if both:
            default, names, spec = both.group(1), both.group(2), both.group(3)
        else:
            d = _IMP_DEF.match(stmt)
            nm = _IMP_NAMED.match(stmt)
            if d:
                default, spec = d.group(1), d.group(2)
            elif nm:
                names, spec = nm.group(1), nm.group(2)
        if spec is None:
            continue
        resolved_spec = _resolve(rel, spec)
        target = _existing(project.root, resolved_spec) if resolved_spec else None
        nid = _node_for(b, target) if target else None
        if nid:
            b.add_edge(page_id, nid, "IMPORTS", rel, i, "exact")
        if default and target:
            defaults[default] = target
        if target and names:
            for piece in names.split(","):
                am = re.match(r"\s*(?:type\s+)?([A-Za-z_$][\w$]*)(?:\s+as\s+([A-Za-z_$][\w$]*))?", piece)
                if am and piece.strip():
                    named[am.group(2) or am.group(1)] = (am.group(1), target)
    for k, (i, line) in enumerate(lines):
        if k in skip:
            continue
        for name, target in defaults.items():
            if re.search(r"<" + re.escape(name) + r"[\s/>.]", line + " "):
                nid = _node_for(b, target, _RENDER_KINDS)
                if nid:
                    b.add_edge(page_id, nid, "RENDERS", rel, i, "exact", via=["mdx"])
        for local, (exp, target) in named.items():
            if re.search(r"(?<![\w$.])" + re.escape(local) + r"\s*\(", line):
                fid = f"function:{target}#{exp}"
                if b.has(fid):
                    b.add_edge(page_id, fid, "CALLS", rel, i, "resolved", via=["mdx"])
            elif re.search(r"<" + re.escape(local) + r"[\s/>.]", line + " "):
                # a named component export (`import { Card } from`): the function when it is one, else the file
                fid = f"function:{target}#{exp}"
                nid = fid if b.has(fid) else _node_for(b, target, _RENDER_KINDS)
                if nid:
                    b.add_edge(page_id, nid, "RENDERS", rel, i, "resolved" if nid == fid else "exact", via=["mdx"])


def _link_sites(text: str, page_id: str, rel: str) -> list[dict]:
    """Root-relative Markdown links and `href`s outside code, as navigation sites."""
    sites = []
    for i, line in _body_lines(text):
        for m in _MD_LINK.finditer(line):
            value = (m.group(1) or m.group(2) or "").split("#")[0].split("?")[0]
            if value and not value.startswith("//"):
                sites.append({"src": page_id, "file": rel, "line": i, "via": "markdown",
                              "locs": [{"kind": "path", "value": value, "conf": "exact"}]})
    return sites


def contribute_content(project: Project, b: GraphBuilder, ac: dict, calls: list, sites: list, st: dict) -> None:
    """Markdown pages (with their links appended to ``sites``) and content collections. ``ac`` is read_astro_config()."""
    from .plugin import _locale_of
    src_dir, base = ac["src_dir"], ac["base"]
    locales = ac.get("locales") or []
    st.setdefault("markdown_pages", 0)
    st.setdefault("mdx_pages", 0)
    st.setdefault("collections", 0)
    st.setdefault("collection_reads", 0)
    prefix = _pages_prefix(src_dir)
    pages = project.root / prefix.rstrip("/")
    if pages.is_dir():
        for path in sorted(pages.rglob("*")):
            ext = path.suffix.lower()
            kind = _MD_KIND.get(ext)
            if not kind or not path.is_file():
                continue
            rel = path.relative_to(project.root).as_posix()
            route = astro_route(rel, src_dir, base)
            if route is None or b.has(f"page:{rel}"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            fm = frontmatter_lines(text) if kind != "html" else {}
            attrs = {"route": route, "uri": route, "framework": "astro", "markdown": kind}
            if ac.get("trailing_slash"):
                attrs["trailing_slash"] = ac["trailing_slash"]
            loc = _locale_of(route, base, locales, ac.get("default_locale"), ac.get("prefix_default_locale")) if locales else None
            if loc:
                attrs["locale"] = loc
            if fm.get("title", ("",))[0]:
                attrs["title"] = fm["title"][0]
            layout_rel = _resolve(rel, fm["layout"][0]) if fm.get("layout", ("",))[0] else None
            if layout_rel:
                attrs["layout"] = layout_rel
            nid = b.add_node("page", rel, name=route, fqn=route, file=rel, line=1, lang="ts",
                             entry_kind="ui_page", attrs=attrs)
            if layout_rel:
                lid = _node_for(b, layout_rel, ("layout", "component", "page"))
                if lid:
                    b.add_edge(nid, lid, "RENDERS", rel, fm["layout"][1], "exact", via=["layout"])
            if kind == "mdx":
                _mdx_edges(project, b, rel, text, nid)
                st["mdx_pages"] += 1
            else:
                st["markdown_pages"] += 1
            if kind != "html":
                sites.extend(_link_sites(text, nid, rel))
    cols = read_collections(project.root, src_dir)
    by_name = {}
    for c in cols:
        attrs = {"collection": True, "framework": "astro"}
        if c["loader"]:
            attrs["loader"] = c["loader"]
        if c["pattern"]:
            attrs["pattern"] = c["pattern"]
        if c["base"] is not None and c["loader"] != "file":
            attrs["base"] = c["base"]
        if c["entries"] is not None:
            attrs["entries"] = c["entries"]
        if c["path"]:
            attrs["path"] = c["path"]
        if c["type"] and not c["loader"]:
            attrs["type"] = c["type"]
        tid = b.add_node("table", f"content:{c['name']}", name=c["name"], fqn=c["name"],
                         file=c["file"], line=c["line"], lang="ts", attrs=attrs)
        by_name[c["name"]] = tid
        st["collections"] += 1
    for c in cols:
        for other, line in c["relations"]:
            if other in by_name:
                b.add_edge(by_name[c["name"]], by_name[other], "HAS_RELATION", c["file"], line, "exact", via="reference")
    reads: dict[str, set] = {}
    for call in calls or []:
        fn = call.get("fn")
        args = call.get("args") or []
        name = call.get("collection") or (args[0] if args else None)
        if fn not in _READ_FNS or name not in by_name:
            continue
        src = call.get("src")
        if not src or not b.has(src):
            continue
        b.add_edge(src, by_name[name], "READS_TABLE", call.get("file"), call.get("line"), "exact", via=fn)
        reads.setdefault(call.get("file") or "", set()).add(name)
        st["collection_reads"] += 1
    for call in calls or []:
        if call.get("fn") != "render":
            continue
        names = reads.get(call.get("file") or "") or set()
        if len(names) != 1:
            continue
        fid = _node_for(b, call.get("file") or "", ("page", "layout", "component", "module"))
        if not fid:
            continue
        name = next(iter(names))
        b.add_edge(fid, by_name[name], "READS_TABLE", call.get("file"), call.get("line"), "resolved", via="render")
        st["collection_reads"] += 1

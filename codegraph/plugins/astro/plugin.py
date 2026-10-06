"""Astro framework layer (on the TypeScript plugin).

  pages        src/pages/**.astro -> page node (route `/`, `/books/{slug}`, `/docs/{rest*}`; entry ui_page).
               A segment starting with `_` is skipped. Frontmatter and <script> blocks are TypeScript at
               their real lines (the extractor); template expressions are not code.
  endpoints    src/pages/**.{ts,js,mts,mjs} exporting GET/POST/... (`ALL` -> ANY) -> one route each
  components   <Card /> in the template -> RENDERS; imports from .ts and other .astro files resolve
  getStaticPaths  recorded as attrs.get_static_paths when the frontmatter exports it (not evaluated)
  is:inline    <script is:inline> counted on the file node as attrs.inline_scripts
"""
from __future__ import annotations

import re

from ...core.plugin import FrameworkPlugin, GraphBuilder, Project
from ..tsweb.common import HTTP_VERBS, add_route, finish, fw_facts, merge_extractor_cfg, register, pkg_deps

CONFIG_FILES = ("astro.config.mjs", "astro.config.js", "astro.config.ts", "astro.config.mts", "astro.config.cjs")
_PAGE_EXT = (".astro", ".mts", ".mjs", ".ts", ".js")
_GSP = re.compile(r"export\s+(?:async\s+)?function\s+getStaticPaths\b|export\s+const\s+getStaticPaths\b")
_FM_OPEN = re.compile(r"\ufeff?\s*---[ \t]*\r?\n")
_INLINE = re.compile(r"<script\b([^>]*)>", re.I)
_CATCHALL = re.compile(r"\[\.\.\.([^\]]+)\]")
_PARAM = re.compile(r"\[([^\]]+)\]")
_ENDPOINT = re.compile(r"^src/pages/.+\.(?:ts|js|mts|mjs)$")


def astro_route(rel: str) -> str | None:
    """src/pages path -> `/`, `/books/{slug}`, `/docs/{rest*}`. None when a segment starts with `_`."""
    rel = rel.replace("\\", "/")
    if not rel.startswith("src/pages/") or rel.endswith(".d.ts"):
        return None
    rest = rel[len("src/pages/"):]
    for ext in _PAGE_EXT:
        if rest.endswith(ext):
            rest = rest[: -len(ext)]
            break
    segs = [s for s in rest.split("/") if s]
    if any(s.startswith("_") for s in segs):
        return None
    if segs and segs[-1] == "index":
        segs.pop()
    segs = [_PARAM.sub(r"{\1}", _CATCHALL.sub(r"{\1*}", s)) for s in segs]
    return "/" + "/".join(segs)


def _split(text: str) -> tuple[str, str]:
    """(frontmatter, template), as the extractor reads them: an empty frontmatter counts, an unclosed one runs to
    the end of the file."""
    m = _FM_OPEN.match(text)
    if not m:
        return "", text
    start = m.end()
    end = text.find("\n---", start - 1)
    if end < 0:
        return text[start:], ""
    return text[start:max(start, end)], text[end + 4:]


def _inline_scripts(template: str) -> int:
    return sum(1 for m in _INLINE.finditer(template) if re.search(r"\bis:inline\b", m.group(1)))


class AstroPlugin(FrameworkPlugin):
    name, language = "astro", "typescript"

    def detect(self, project: Project) -> bool:
        return "astro" in pkg_deps(project.root) or any(project.exists(f) for f in CONFIG_FILES)

    def register_hooks(self, ctx) -> None:
        root = ctx.project.root
        cfg = ctx.extractor_cfg
        kinds = cfg.setdefault("kinds", [])
        kinds.extend([("src/pages/", "page"), ("src/layouts/", "layout"), ("src/components/", "component")])
        merge_extractor_cfg(cfg, src_dirs=["src"], walk_src=not (root / "tsconfig.json").exists())
        register(ctx, self.name)

    def contribute(self, project: Project, b: GraphBuilder, ctx) -> dict:
        st = {"pages": 0, "endpoints": 0, "layouts": 0, "components": 0, "inline_scripts": 0, "get_static_paths": 0}
        for n in b.nodes.values():
            if not n.file or not n.file.endswith(".astro") or n.kind not in ("page", "layout", "component"):
                continue
            try:
                text = (project.root / n.file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if n.kind == "page":
                route = astro_route(n.file)
                if route is not None:
                    n.name = n.fqn = route
                    n.attrs.update(route=route, uri=route, framework="astro")
                    n.entry_kind = "ui_page"
                    st["pages"] += 1
                    if _GSP.search(_split(text)[0]):
                        n.attrs["get_static_paths"] = True
                        st["get_static_paths"] += 1
            elif n.kind == "layout":
                st["layouts"] += 1
            else:
                st["components"] += 1
            inline = _inline_scripts(_split(text)[1])
            if inline:
                n.attrs["inline_scripts"] = inline
                st["inline_scripts"] += inline
        mods = (fw_facts(ctx).get("modules") or {})
        for f, mod in mods.items():
            if f.endswith(".d.ts") or not _ENDPOINT.match(f):
                continue
            uri = astro_route(f)
            if uri is None:
                continue
            for ex in mod.get("exports") or []:
                name = ex.get("name") or ""
                if name == "ALL":
                    verb = "ANY"
                elif name in HTTP_VERBS:
                    verb = name
                else:
                    continue
                node = ex.get("node")
                handlers = [node] if node and b.has(node) else []
                add_route(b, verb, uri, handlers, f, ex.get("line") or 1, "astro",
                          "exact" if handlers else "resolved", {"file_route": True})
                st["endpoints"] += 1
        st.update(finish(project, b, ctx, self.name))
        return st

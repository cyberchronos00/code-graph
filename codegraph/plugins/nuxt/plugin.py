"""Nuxt framework plugin (on the TypeScript plugin).

register_hooks: points the TS extractor at Nuxt's generated types (.nuxt/tsconfig.app.json, whose
  nuxt.d.ts pulls in .nuxt/types/imports.d.ts = auto-imported composables/utils/stores) and the
  generated global-components map (.nuxt/types/components.d.ts), plus Nuxt directory conventions
  (pages/, layouts/, components/, composables/, stores/, utils/, middleware/, plugins/).
  Without .nuxt (run `nuxi prepare` locally) it falls back to tsconfig.json and auto-imports stay
  unresolved.
contribute: file-based routing (route path per page), entry points (pages = ui_page; app.vue,
  layouts, global middleware, plugins = ui_global), USES_LAYOUT, i18n keys (USES_I18N; defining
  locale files).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ...core.plugin import FrameworkPlugin, GraphBuilder, Project

KIND_RULES = [("app/pages/", "page"), ("app/layouts/", "layout"), ("app/components/", "component"),
              ("app/composables/", "composable"), ("app/stores/", "store"), ("app/utils/", "util"),
              ("app/middleware/", "middleware"), ("app/plugins/", "plugin"), ("app/app.vue", "app"),
              ("app/error.vue", "app")]


def nuxt_route(rel: str, src_dir: str = "app") -> str:
    """app/pages/shelves/[id]/books/[bookId].vue -> /shelves/:id/books/:bookId"""
    p = rel[len(f"{src_dir}/pages/"):]
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
        self.prepared = (root / ".nuxt" / "tsconfig.app.json").exists()
        if self.prepared:
            cfg["tsconfig"] = ".nuxt/tsconfig.app.json"
            cfg["components_dts"] = str(root / ".nuxt" / "types" / "components.d.ts")
        cfg["kinds"] = KIND_RULES
        cfg["src_dirs"] = ["app"] if (root / "app").exists() else ["."]

    def contribute(self, project: Project, builder: GraphBuilder, ctx) -> dict:
        st = {"prepared": self.prepared, "pages": 0, "layouts": 0, "ui_global": 0, "i18n_keys_used": 0, "i18n_keys_defined": 0}
        if not ctx or not ctx.facts:
            return {**st, "status": "no facts"}
        layouts = {}
        for n in builder.nodes.values():
            if n.lang != "ts" or not n.file:
                continue
            if n.kind == "page":
                route = nuxt_route(n.file)
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
        for d in ("i18n/locales", "app/locales", "locales"):
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

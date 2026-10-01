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

from ...core.plugin import FrameworkPlugin, GraphBuilder, Project

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
EXCLUDE = ["node_modules", ".output", ".nuxt", "dist", ".git", "android", "ios", "public", "coverage", "playwright-report"]


def _exports(f: Path) -> list[tuple[str, str]]:
    """(local name, export name) pairs of a module, by regex (enough for auto-import declarations)."""
    txt = f.read_text(errors="replace")
    out = []
    for m in re.finditer(r"^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)", txt, re.M):
        out.append((m.group(1), m.group(1)))
    for m in re.finditer(r"^export\s*\{([^}]*)\}(?!\s*from)", txt, re.M):
        for part in m.group(1).split(","):
            bits = part.strip().split(" as ")
            if bits[0].strip() and not bits[0].strip().startswith("type "):
                out.append((bits[-1].strip(), bits[-1].strip()))
    if re.search(r"^export\s+default\b", txt, re.M):
        stem = f.stem if f.stem != "index" else f.parent.name
        name = re.sub(r"[-_.](\w)", lambda m: m.group(1).upper(), stem)
        out.append((name[:1].lower() + name[1:], "default"))
    return out


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
    d = Path(tempfile.mkdtemp(prefix="codegraph-nuxt-"))
    atexit.register(shutil.rmtree, d, True)
    src = (root / src_dir).resolve() if src_dir not in ("", ".") else root.resolve()
    lines, n_imp = ["export {}", "declare global {"], 0
    seen = set()
    for sub in AUTO_IMPORT_DIRS:
        base = src / sub
        if not base.is_dir():
            continue
        files = [f for f in sorted(base.iterdir()) if f.is_file() and re.search(r"\.(ts|js|mjs|mts)$", f.name)
                 and not re.search(r"\.(test|spec|d)\.", f.name)]
        files += [i for c in sorted(base.iterdir()) if c.is_dir() for i in (c / "index.ts", c / "index.js") if i.exists()]
        for f in files:
            spec = str(f.with_suffix("")).replace("\\", "/")
            for local, exp in _exports(f):
                if local in seen:
                    continue
                seen.add(local)
                acc = "['default']" if exp == "default" else f".{exp}"
                lines.append(f"  const {local}: typeof import('{spec}'){acc}")
                n_imp += 1
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
            if f.is_file() and f.suffix in (".vue", ".tsx", ".jsx") and "node_modules" not in f.parts:
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
    return d, {"auto_imports": n_imp, "builtins": builtins, "components": n_comp}


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

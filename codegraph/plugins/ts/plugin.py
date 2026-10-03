"""TypeScript / Vue language plugin.

Runs extractor/extract.mjs (TypeScript compiler API + @vue/compiler-sfc) over the project and
maps its facts into the graph:

  nodes  module / function / method / class / type / composable / store / <file-kind> for .vue
         (component, page, layout, ... as named by the framework plugin), http (client endpoint)
  edges  IMPORTS, CALLS, USES_COMPOSABLE, USES_STORE, RENDERS, INSTANTIATES, REFERENCES_TYPE,
         HTTP_CALLS (function -> http:<METHOD> <path template>)
         SUBSCRIBES_CHANNEL (function -> channel_sub:<name>: laravel-echo Echo.private/channel/join, pusher-js
         subscribe, useEcho hooks; attrs.events = .listen('X') names)
  tests  spec / test files (Vitest, Jest, Playwright, Cypress) are indexed with attrs.test and one `test` node per
         it() / test(); their HTTP calls become TEST_HTTP edges, page.goto / cy.visit become TEST_VISITS to the page
         (resolved after the framework plugin set page routes, see codegraph/tests_index.py)

Framework plugins (Nuxt) configure the extractor through `TsContext.extractor_cfg` in
register_hooks(): tsconfig (e.g. .nuxt/tsconfig.app.json for auto-import globals), the
generated global-components map, and file-kind rules.
"""
from __future__ import annotations

import json
from collections import defaultdict
import re
import shutil
import hashlib
import os
import sys
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ... import presets
from ...core.model import CONFIDENCE_RANK
from ...core.paths import names_regex, rules as path_rules
from ...coverage import SUPPORTED
from ...core import cache, extractors, fsutil
from ...core.plugin import FrameworkPlugin, GraphBuilder, LanguagePlugin, Project

EXTRACTOR_DIR = Path(__file__).parent / "extractor"
EXTRACTOR = EXTRACTOR_DIR / "extract.mjs"
PH = re.compile(r"\{[^{}]*\}")


@dataclass
class TsContext:
    project: Project
    extractor_cfg: dict[str, Any] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    http_nodes: dict[str, dict] = field(default_factory=dict)


def min_conf(*cs: str) -> str:
    return min(cs, key=lambda c: CONFIDENCE_RANK[c])


def normalize_client_url(url: str) -> tuple[str, dict]:
    """Client URL template -> path template used as the http node key.
    Strips scheme://host, a leading origin placeholder (the configured server URL, unknown at
    analysis time) and the query string. Placeholders keep their names: {storeSlug}."""
    info = {}
    u = url.strip()
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*", u)
    if m:
        info["origin"] = m.group(0)
        u = u[m.end():]
    m = re.match(r"^\{[^{}/]*\}(?=/|$)", u)
    if m:
        info["origin"] = m.group(0)
        u = u[m.end():]
    q = re.search(r"\?(?![^{]*\})", u)
    if q:
        info["query"] = u[q.end():]
        u = u[:q.start()]
    u = re.sub(r"/{2,}", "/", u)
    if not u.startswith("/"):
        info["relative"] = True
        u = "/" + u if not u.startswith("{") else u
    if len(u) > 1:
        u = u.rstrip("/")
    return u, info


def join_url(base: str | None, url: str) -> str:
    if not base or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url) or url.startswith("{"):
        return url
    return base.rstrip("/") + "/" + url.lstrip("/")


LARAVEL_ASSET_DIRS = ("resources/js", "resources/ts", "resources/assets/js", "resources/scripts", "app/javascript")
PKG_SKIP = {"node_modules", "dist", "build", "out", "coverage", "vendor", "Pods", "DerivedData", "example", "examples",
            "e2e", "test", "tests", "__tests__", "fixtures", "templates", "android-template", "ios-pods-template",
            "ios-spm-template"}


# a PHP / Python backend at the root: its frontend directory is indexed on its own and combined with `cg link`
BACKEND_MARKERS = ("composer.json", "artisan", "pyproject.toml", "setup.py", "setup.cfg", "manage.py", "requirements.txt")


def sub_tsconfigs(root: Path) -> list[str]:
    """The sub-project tsconfigs indexed as one program when the root has none: per-package configs of a JS monorepo
    (root package.json), or, in a repo whose root is not a JS project at all (a Swift / Kotlin / Rust / Dart app with
    a web/ directory), every tsconfig.json one or two levels down, package.json or not (#75). A PHP / Python
    backend root keeps its frontend separate (index it on its own; `cg link` combines the graphs)."""
    root = Path(root)
    if (root / "package.json").is_file():
        return package_tsconfigs(root)
    if any((root / m).exists() for m in BACKEND_MARKERS):
        return []
    return package_tsconfigs(root, need_package_json=False)


def package_tsconfigs(root: Path, need_package_json: bool = True) -> list[str]:
    """A monorepo without a root tsconfig.json / jsconfig.json (lerna / nx / npm workspaces with per-package configs,
    e.g. Capacitor and its plugins): the tsconfig.json of each package (a directory holding package.json and
    tsconfig.json) one or two levels down. The extractor indexes them as one program, like a solution config."""
    root = Path(root)
    if (root / "tsconfig.json").is_file() or (root / "jsconfig.json").is_file():
        return []
    out = []

    def pkg(d: Path) -> bool:
        return (d / "tsconfig.json").is_file() and (not need_package_json or (d / "package.json").is_file())

    def subdirs(d: Path):
        try:
            return sorted(x for x in d.iterdir() if x.is_dir() and not x.name.startswith(".") and x.name not in PKG_SKIP
                          and not x.is_symlink())
        except OSError:
            return []

    for d in subdirs(root):
        if pkg(d):
            out.append(d)
        elif not (d / "package.json").is_file():          # packages/<name>, libs/<name>
            out += [x for x in subdirs(d) if pkg(x)]
    return [str((d / "tsconfig.json").relative_to(root)) for d in out][:200]


class TypeScriptPlugin(LanguagePlugin):
    name = "typescript"

    def __init__(self):
        self.program: TsContext | None = None

    def detect(self, project: Project) -> bool:
        if project.exists("tsconfig.json") or "typescript" in (project.detected.get("languages") or {}):
            return True
        if sub_tsconfigs(project.root):
            return True     # a monorepo with per-package tsconfigs only, or a non-JS repo with web/tsconfig.json
        if project.exists("package.json") and any((project.root / d).is_dir() for d in LARAVEL_ASSET_DIRS):
            return True     # Laravel / Rails-style app with a plain-JS frontend under resources/js (allowJs)
        from ..tsweb.common import has_server_framework   # plain-JS server projects (Express, Koa, ...): allowJs
        return project.exists("package.json") and has_server_framework(project)

    def prerequisite_problem(self, project: Project) -> str | None:
        if not shutil.which("node"):
            return "node not installed (Node.js 20+ is needed for the TypeScript extractor)"
        if not extractors.status("typescript")["installed"] and not shutil.which("npm"):
            return "TypeScript extractor dependencies missing and npm not installed: install npm, then run `cg setup typescript`"
        return None

    def ensure_extractor(self) -> Path:
        """Directory the extractor runs from, with its npm dependencies (codegraph/core/extractors.py)."""
        return extractors.ensure("typescript")

    def index(self, project: Project, builder: GraphBuilder, frameworks: list[FrameworkPlugin]) -> dict:
        if not shutil.which("node"):
            return {"status": "skipped", "reason": "node not installed"}
        exdir = self.ensure_extractor()
        ctx = TsContext(project=project)
        # src/ and app/ (SPA / Next / Nuxt 4), and the Laravel + Vite asset dirs; the extractor falls back to the
        # tsconfig's own files when none of these hold any
        ctx.extractor_cfg = {"root": str(project.root), "tsconfig": "tsconfig.json", "kinds": [],
                             "src_dirs": ["src", "app"] + [d for d in LARAVEL_ASSET_DIRS if (project.root / d).is_dir()]}
        pkg_cfgs = sub_tsconfigs(project.root)
        if pkg_cfgs:
            ctx.extractor_cfg["package_tsconfigs"] = pkg_cfgs
        for fw in frameworks:
            fw.register_hooks(ctx)
        # the walks' directory rules (codegraph/presets: common + typescript skip_dirs, the test walk's
        # test_walk_skip_dirs, the tsconfig files that are resolution input only), adjusted by .cg.yaml skip_dirs.add /
        # keep and include; exclude globs and skip_dirs.add names also drop files the tsconfig itself lists
        rules = path_rules(project, "typescript")
        ctx.extractor_cfg.update(rules.extractor_cfg())
        keep = set(rules.keep)
        ctx.extractor_cfg["test_skip_names"] = sorted(set(presets.values("typescript", "test_walk_skip_dirs", default=[])) - keep)
        ctx.extractor_cfg["source_skip_names"] = sorted(set(presets.values("typescript", "source_skip_dirs", default=[])) - keep)
        added = set((project.options.get("config") or {}).get("skip_dirs", {}).get("add") or [])
        ex = [x for x in (rules.user_exclude_regex(), names_regex(added)) if x]
        if ex:
            ctx.extractor_cfg["exclude_re"] = "|".join(f"(?:{x})" for x in ex)
        if rules.generated_regex():
            ctx.extractor_cfg["generated_re"] = rules.generated_regex()
        from ...platforms import uses_react_native
        pcfg = (project.options.get("config") or {}).get("platforms") or {}
        if pcfg.get("file_suffixes", True) and uses_react_native(project.root):
            ctx.extractor_cfg["platform_suffixes"] = [".ios", ".android", ".native", ".web", ""]
        gen_files = rules.excluded_files(SUPPORTED["typescript"])   # generated / copied / vendored files the scan found
        if gen_files:
            ctx.extractor_cfg["exclude_files"] = gen_files
        self.program = ctx
        t0 = time.time()
        cache_file, cache_status = None, "disabled"
        if not os.environ.get("CODEGRAPH_NO_CACHE"):
            fp = facts_fingerprint(project.root, ctx.extractor_cfg)
            cdir = cache.subdir("ts")
            rkey = cache.root_key(project.root)
            cache_file = cdir / f"{rkey}-v{fsutil.CACHE_VERSION}-{fp}.json"
            cache_status = "miss"
        if cache_file and cache_file.exists():
            facts = json.loads(cache_file.read_text())
            cache_status = "hit"
        else:
            with tempfile.TemporaryDirectory() as td:
                cfgp = Path(td) / "cfg.json"
                out = Path(td) / "facts.json"
                cfg = {**ctx.extractor_cfg, "out": str(out)}
                cfgp.write_text(json.dumps(cfg))
                proc = subprocess.run(["node", "--max-old-space-size=6144", str(exdir / EXTRACTOR.name), "--config", str(cfgp)],
                                      capture_output=True, text=True)
                if proc.returncode != 0:
                    raise RuntimeError(f"ts extractor failed: {proc.stderr[-2000:]}")
                facts = json.loads(out.read_text())
            if cache_file:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                for old in cache_file.parent.glob(f"{rkey}-*.json"):
                    old.unlink(missing_ok=True)  # keep one entry per project root
                cache_file.write_text(json.dumps(facts))
                cache.note_project(project.root)
        ctx.facts = facts
        facts.setdefault("stats", {})["facts_cache"] = cache_status
        t_extract = time.time() - t0
        for n in facts["nodes"]:
            kind, key = n["id"].split(":", 1)
            attrs = dict(n.get("attrs") or {})
            if n.get("parent"):
                attrs["parent"] = n["parent"]
            builder.add_node(kind, key, n["name"], fqn=n["name"] if kind not in ("module",) else None, file=n["file"],
                             line=n["line"], end_line=n.get("end_line"), module=module_of(n["file"]), doc=n.get("doc"),
                             lang="ts", attrs=attrs)
            if n.get("parent"):
                builder.add_edge(n["parent"], n["id"], "CONTAINS", file=n["file"], line=n["line"])
        for e in facts["edges"]:
            a = {k: v for k, v in (e.get("attrs") or {}).items() if v is not None}
            builder.add_edge(e["src"], e["dst"], e["kind"], file=e["file"], line=e["line"], confidence=e["confidence"], **a)
        # HTTP calls -> client endpoint nodes. "API origins" = origins of the configured API clients
        # (axios instances with a resolved baseURL); other origins stay in the endpoint key.
        api_origins = set()
        for a in facts["api_calls"]:
            if a["client"] == "axios-instance":
                for b in a.get("base") or []:
                    o = normalize_client_url(b)[1].get("origin")
                    if o:
                        api_origins.add(o)
        self.api_origins = sorted(api_origins)
        expanded_helpers = {(a["via_helper"]["at"]) for a in facts["api_calls"] if a.get("via_helper")}
        # configured base URLs ({runtimeConfig.X} / {env.X}): their path part prefixes the client path
        from .baseurl import ConfigValues, base_path, is_config_ph, API_NAME
        cv = ConfigValues(project.root, facts.get("config_defaults") or {})
        base_hits, base_unresolved = {}, set()

        def prefer_config(vals):
            """A value built from a configured URL or an opaque override (remote config, a parameter) -> the
            configured one: `remote || config.public.apiBase` is read as the configured API."""
            if not vals or len(vals) < 2:
                return vals
            orig = [normalize_client_url(v or "")[1].get("origin") if v else None for v in vals]
            # a config key whose configured value is empty is unset at run time: the other branch is what runs
            unset = [is_config_ph(o) and (cv.lookup(o[1:-1]) or ("x",))[0] == "" for o in orig]
            if any(unset) and not all(unset):
                vals = [v for v, u in zip(vals, unset) if not u]
                orig = [o for o, u in zip(orig, unset) if not u]
            if len(vals) < 2:
                return vals
            if any(is_config_ph(o) for o in orig):
                keep = [v for v, o in zip(vals, orig) if is_config_ph(o) or not (o and o.startswith("{"))]
                return keep or vals
            return vals
        n_http = n_url_unknown = 0
        for a in facts["api_calls"]:
            at = f"{a['file']}:{a['line']}"
            if not a.get("via_helper") and at in expanded_helpers:
                a["expanded_at_callsites"] = True
                continue  # helper whose URL is a parameter: represented by its call-site expansions
            bases = prefer_config(a.get("base")) or [None]
            for base in bases:
                for url in prefer_config(a["urls"]):
                    path, info = normalize_client_url(join_url(base, url))
                    origin = info.get("origin")
                    if path == "/" and not base and origin and origin.startswith("{") and not is_config_ph(origin):
                        # the whole URL is an opaque value (a wrapper's parameter without callers): no endpoint to name
                        n_url_unknown += 1
                        continue
                    resolved_base = None
                    if is_config_ph(origin):
                        hit = cv.lookup(origin[1:-1])
                        bp = base_path(hit[0]) if hit else None
                        if bp is not None:
                            resolved_base = {"placeholder": origin, "value": hit[0], "from": hit[1]}
                            path = (bp + path) if path != "/" else (bp or "/")
                            base_hits[origin] = resolved_base
                        else:
                            base_unresolved.add(origin)
                    if resolved_base:
                        okind = "env"           # a configured server URL whose value is in the repo
                    elif origin and origin in api_origins:
                        okind = "api"
                    elif is_config_ph(origin) and origin.startswith("{env."):
                        okind = "unknown"       # env value not in the repo: the base is some configured server
                    elif is_config_ph(origin):
                        okind = "env" if API_NAME.search(origin) else "other"
                    elif origin and "://" in origin:
                        okind = "other"
                    elif origin:
                        okind = "unknown"
                    else:
                        okind = "same-origin" if not base else "api"
                    key = f"{a['method']} {path}" if okind in ("api", "unknown", "env") else f"{a['method']} {origin or ''}{path}"
                    nid = builder.add_node("http", key, key, fqn=key, lang="ts", attrs={"method": a["method"], "path": path, "client": a["client"],
                                                                                "origin": origin, "origin_kind": okind})
                    if resolved_base:
                        builder.nodes[nid].attrs["base"] = resolved_base
                    ctx.http_nodes.setdefault(nid, {"method": a["method"], "path": path, "calls": []})["calls"].append(a)
                    conf = min_conf(a["url_conf"], a.get("base_conf") or "exact", "resolved" if resolved_base else "exact")
                    if a.get("test"):
                        builder.nodes[nid].attrs.setdefault("test_only", True)
                    else:
                        builder.nodes[nid].attrs["test_only"] = False
                    builder.add_edge(a["src"], nid, "TEST_HTTP" if a.get("test") else "HTTP_CALLS", file=a["file"], line=a["line"], confidence=conf,
                                     client=a["client"], url=url, base=base, expr=a.get("expr"), origin=info.get("origin"),
                                     query=info.get("query"), via_helper=a.get("via_helper"),
                                     query_keys=a.get("query"), body_keys=a.get("body"),
                                     helper_template=a.get("expanded_at_callsites"))
                    n_http += 1
        # realtime subscriptions -> channel_sub:<name> client channel nodes
        n_sub = 0
        for sub in facts.get("subscriptions") or []:
            for name in sub["names"]:
                if not name or not PH.sub("", name).strip(". -_:"):
                    continue   # fully dynamic channel name
                nid = builder.add_node("channel_sub", name, name, fqn=name, lang="ts", attrs={"name": name})
                n = builder.nodes[nid]
                n.attrs["visibility"] = sub["visibility"] if n.attrs.get("visibility") in (None, sub["visibility"]) else "mixed"
                n.attrs["events"] = sorted(set(n.attrs.get("events") or []) | set(sub.get("events") or []))
                n.attrs["clients"] = sorted(set(n.attrs.get("clients") or []) | {sub["client"]})
                if not sub.get("test"):
                    n.attrs["test_only"] = False
                else:
                    n.attrs.setdefault("test_only", True)
                builder.add_edge(sub["src"], nid, "TEST_CALLS" if sub.get("test") else "SUBSCRIBES_CHANNEL", file=sub["file"],
                                 line=sub["line"], confidence=sub["conf"], client=sub["client"], visibility=sub["visibility"],
                                 events=sub.get("events") or None)
                n_sub += 1
        # web / native bridge sends (Capacitor, React Native, Expo) -> endpoint:<protocol>:<module>#<method>
        # Electron IPC (endpoint:electron-ipc:<channel>), context bridge (endpoint:electron-preload:<key>#<member>),
        # Tauri commands (endpoint:tauri:<command>)
        from ...bridges import endpoint_key, protocol_receive, protocol_send
        n_br = n_brr = 0
        for br in facts.get("bridges") or []:
            if not builder.has(br["src"]):
                continue
            protocol_send(builder, br["protocol"], br["module"], br["method"], br["src"], br["file"], br["line"], br["conf"],
                          test=bool(br.get("test")), via=br.get("via"), module_at=br.get("at"), external=br.get("external"),
                          api=br.get("api"), process=br.get("process"))
            if br.get("api"):
                builder.nodes["endpoint:" + endpoint_key(br["protocol"], br["module"], br["method"])].attrs["api"] = br["api"]
            n_br += 1
        rvs = facts.get("bridge_receivers") or []
        # a channel variable typed as a union (`ipcMain.on(name, relay)` for every IpcEvents member) is a fallback:
        # dropped where the process also receives the channel by name, kept only for channels another process sends
        named = {(rv["protocol"], rv["module"], rv.get("process")) for rv in rvs if not rv.get("union")}
        # (and from another process: main's webContents.send never reaches an ipcMain listener)
        sent = defaultdict(set)
        for br in facts.get("bridges") or []:
            sent[(br["protocol"], br["module"])].add(br.get("process"))
        for rv in rvs:
            key = (rv["protocol"], rv["module"])
            if not builder.has(rv["handler"]) or (rv.get("union") and (key + (rv.get("process"),) in named
                                                               or not (sent.get(key, set()) - {rv.get("process")}))):
                continue
            protocol_receive(builder, rv["protocol"], rv["module"], rv["method"], rv["handler"], rv["file"], rv["line"],
                             rv["conf"], via=rv.get("via"), process=rv.get("process"))
            n_brr += 1
        # browser tests opening pages: resolved to page nodes once the framework plugin has set page routes
        pv = getattr(builder, "pending_visits", None)
        if pv is None:
            pv = builder.pending_visits = []
        pv += [{"src": v["src"], "url": v["url"], "file": v["file"], "line": v["line"], "via": v.get("via")} for v in facts.get("visits") or []]
        # literal fallbacks (`x ?? y ?? 'UTC'`) -> resolution nodes (concept queries)
        n_fb = 0
        for fb in facts.get("fallbacks") or []:
            if not builder.has(fb["owner"]) or fb["literal"] in ("", 0):
                continue  # `?? ''` / `|| 0` are null-guards, not configuration fallbacks
            target = fb["chain"][0]
            chain = [{"kind": "expr", "text": t} for t in fb["chain"][:-1]] + [{"kind": "literal", "value": fb["literal"], "op": fb["op"]}]
            rid = builder.add_node("resolution", f"{fb['owner']}#{target}@{fb['line']}", target, fqn=f"{fb['owner'].split(':', 1)[1]}#{target}",
                                   file=fb["file"], line=fb["line"], module=module_of(fb["file"]), lang="ts",
                                   attrs={"fn": fb["owner"], "target": target, "form": "expr", "op": fb["op"], "chain": chain,
                                          "signature": [c.get("text") if c["kind"] == "expr" else repr(c["value"]) for c in chain],
                                          "final_literal": fb["literal"], "template": fb.get("template")})
            builder.add_edge(fb["owner"], rid, "HAS_RESOLUTION", file=fb["file"], line=fb["line"], confidence="exact")
            n_fb += 1
        st = dict(facts["stats"])
        # files the parser had to recover (#73): their error lines for coverage (no per-file buckets for TypeScript)
        self.file_report = {"syntax_errors": st.pop("syntax_errors", None) or {}}
        if facts.get("skipped_links"):
            st["skipped_dangling_symlinks"] = facts["skipped_links"]
            print(f"typescript: skipped {len(facts['skipped_links'])} dangling symlink(s): "
                  + ", ".join(facts["skipped_links"][:5]), file=sys.stderr)
        st.update({"literal_fallbacks": n_fb, "channel_subscriptions": n_sub, "bridge_sends": n_br, "bridge_receivers": n_brr,
                   "config_base_urls": {k: f"{v['value']} ({v['from']})" for k, v in sorted(base_hits.items())},
                   "config_base_urls_unresolved": sorted(base_unresolved), "http_url_unknown": n_url_unknown})
        st.update({"extract_seconds": round(t_extract, 2), "http_edges": n_http, "http_endpoints": len(ctx.http_nodes),
                   "api_origins": self.api_origins})
        return st


# not hashed into the facts cache key: build output, tool caches, test reports (codegraph/presets/typescript.yaml)
SKIP_DIRS = presets.skip_dirs("typescript")


def facts_fingerprint(root, cfg: dict) -> str:
    """Cache key for extractor facts: cache version + extractor code + deps lock + config + (path, size, content hash)
    of every project file outside node_modules (.nuxt and the project lockfile included, so `nuxi prepare` or a
    dependency bump invalidates it). Content, not mtime: a same-size edit with a restored mtime is a miss."""
    h = hashlib.sha256(f"cg-cache-v{fsutil.CACHE_VERSION}\n".encode())
    for f in (EXTRACTOR, EXTRACTOR_DIR / "package-lock.json"):
        h.update(f.read_bytes() if f.exists() else b"")
    # generated stand-in types live in a fresh temp dir per run; their content follows from the project files
    c = {k: v for k, v in cfg.items() if not (cfg.get("generated_types") and k in ("tsconfig", "components_dts"))}
    h.update(json.dumps(c, sort_keys=True).encode())
    if cfg.get("generated_types"):   # ... and from the code that writes them: hash their content
        gen = Path(cfg["tsconfig"]).parent
        for f in sorted(gen.rglob("*.d.ts")):
            h.update(f.read_bytes().replace(str(gen).encode(), b""))
    root = Path(root)
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            h.update(f"{os.path.relpath(p, root)}|{fsutil.content_key(p)}\n".encode())   # dangling symlinks hash too
    return h.hexdigest()[:20]


def module_of(path: str | None) -> str | None:
    if not path:
        return None
    parts = path.split("/")
    if parts[0] in ("app", "src") and len(parts) > 2:
        return "/".join(parts[1:-1]) or parts[0]
    return "/".join(parts[:-1]) or None

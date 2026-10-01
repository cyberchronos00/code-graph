"""TypeScript / Vue language plugin.

Runs extractor/extract.mjs (TypeScript compiler API + @vue/compiler-sfc) over the project and
maps its facts into the graph:

  nodes  module / function / method / class / type / composable / store / <file-kind> for .vue
         (component, page, layout, ... as named by the framework plugin), http (client endpoint)
  edges  IMPORTS, CALLS, USES_COMPOSABLE, USES_STORE, RENDERS, INSTANTIATES, REFERENCES_TYPE,
         HTTP_CALLS (function -> http:<METHOD> <path template>)

Framework plugins (Nuxt) configure the extractor through `TsContext.extractor_cfg` in
register_hooks(): tsconfig (e.g. .nuxt/tsconfig.app.json for auto-import globals), the
generated global-components map, and file-kind rules.
"""
from __future__ import annotations

import json
import re
import shutil
import hashlib
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.model import CONFIDENCE_RANK
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


class TypeScriptPlugin(LanguagePlugin):
    name = "typescript"

    def __init__(self):
        self.program: TsContext | None = None

    def detect(self, project: Project) -> bool:
        if project.exists("tsconfig.json") or "typescript" in (project.detected.get("languages") or {}):
            return True
        from ..tsweb.common import has_server_framework   # plain-JS server projects (Express, Koa, ...): allowJs
        return project.exists("package.json") and has_server_framework(project)

    def prerequisite_problem(self, project: Project) -> str | None:
        if not shutil.which("node"):
            return "node not installed (Node.js 20+ is needed for the TypeScript extractor)"
        if not (EXTRACTOR_DIR / "node_modules" / "typescript").exists() and not shutil.which("npm"):
            return "TypeScript extractor dependencies missing and npm not installed: run `(cd codegraph/plugins/ts/extractor && npm ci)`"
        return None

    def ensure_extractor(self) -> None:
        if not (EXTRACTOR_DIR / "node_modules" / "typescript").exists():
            subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=EXTRACTOR_DIR, check=True)

    def index(self, project: Project, builder: GraphBuilder, frameworks: list[FrameworkPlugin]) -> dict:
        if not shutil.which("node"):
            return {"status": "skipped", "reason": "node not installed"}
        self.ensure_extractor()
        ctx = TsContext(project=project)
        ctx.extractor_cfg = {"root": str(project.root), "tsconfig": "tsconfig.json", "kinds": [], "src_dirs": ["src", "app"]}
        for fw in frameworks:
            fw.register_hooks(ctx)
        self.program = ctx
        t0 = time.time()
        cache_file, cache_status = None, "disabled"
        if not os.environ.get("CODEGRAPH_NO_CACHE"):
            fp = facts_fingerprint(project.root, ctx.extractor_cfg)
            cdir = Path(os.environ.get("CODEGRAPH_CACHE", Path.home() / ".cache" / "codegraph")) / "ts"
            rkey = hashlib.sha256(str(Path(project.root).resolve()).encode()).hexdigest()[:12]
            cache_file = cdir / f"{rkey}-{fp}.json"
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
                proc = subprocess.run(["node", "--max-old-space-size=6144", str(EXTRACTOR), "--config", str(cfgp)],
                                      capture_output=True, text=True)
                if proc.returncode != 0:
                    raise RuntimeError(f"ts extractor failed: {proc.stderr[-2000:]}")
                facts = json.loads(out.read_text())
            if cache_file:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                for old in cache_file.parent.glob(f"{rkey}-*.json"):
                    old.unlink()  # keep one entry per project root
                cache_file.write_text(json.dumps(facts))
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
        n_http = 0
        for a in facts["api_calls"]:
            at = f"{a['file']}:{a['line']}"
            if not a.get("via_helper") and at in expanded_helpers:
                a["expanded_at_callsites"] = True
                continue  # helper whose URL is a parameter: represented by its call-site expansions
            bases = a.get("base") or [None]
            for base in bases:
                for url in a["urls"]:
                    path, info = normalize_client_url(join_url(base, url))
                    origin = info.get("origin")
                    if origin and origin in api_origins:
                        okind = "api"
                    elif origin and (origin.startswith("{runtimeConfig.") or "://" in origin):
                        okind = "other"
                    elif origin:
                        okind = "unknown"
                    else:
                        okind = "same-origin" if not base else "api"
                    key = f"{a['method']} {path}" if okind in ("api", "unknown") else f"{a['method']} {origin or ''}{path}"
                    nid = builder.add_node("http", key, key, fqn=key, lang="ts", attrs={"method": a["method"], "path": path, "client": a["client"],
                                                                                "origin": origin, "origin_kind": okind})
                    ctx.http_nodes.setdefault(nid, {"method": a["method"], "path": path, "calls": []})["calls"].append(a)
                    conf = min_conf(a["url_conf"], a.get("base_conf") or "exact")
                    builder.add_edge(a["src"], nid, "HTTP_CALLS", file=a["file"], line=a["line"], confidence=conf,
                                     client=a["client"], url=url, base=base, expr=a.get("expr"), origin=info.get("origin"),
                                     query=info.get("query"), via_helper=a.get("via_helper"),
                                     query_keys=a.get("query"), body_keys=a.get("body"),
                                     helper_template=a.get("expanded_at_callsites"))
                    n_http += 1
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
        st.update({"literal_fallbacks": n_fb})
        st.update({"extract_seconds": round(t_extract, 2), "http_edges": n_http, "http_endpoints": len(ctx.http_nodes),
                   "api_origins": self.api_origins})
        return st


SKIP_DIRS = {"node_modules", ".git", ".output", "dist", ".cache", "coverage", "playwright-report", "test-results"}


def facts_fingerprint(root, cfg: dict) -> str:
    """Cache key for extractor facts: extractor code + deps lock + config + (path, size, mtime) of every
    project file outside node_modules (.nuxt and the project lockfile included, so `nuxi prepare` or a
    dependency bump invalidates it)."""
    h = hashlib.sha256()
    for f in (EXTRACTOR, EXTRACTOR_DIR / "package-lock.json"):
        h.update(f.read_bytes() if f.exists() else b"")
    h.update(json.dumps(cfg, sort_keys=True).encode())
    root = Path(root)
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        for fn in sorted(fns):
            st = os.stat(os.path.join(dp, fn))
            h.update(f"{os.path.relpath(os.path.join(dp, fn), root)}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:20]


def module_of(path: str | None) -> str | None:
    if not path:
        return None
    parts = path.split("/")
    if parts[0] in ("app", "src") and len(parts) > 2:
        return "/".join(parts[1:-1]) or parts[0]
    return "/".join(parts[:-1]) or None

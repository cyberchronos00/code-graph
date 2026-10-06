"""React Router v6 / v7 and Remix pages, loaders, navigation and forms.

Code routers become ``page:react-router:<path>`` (the same shape as ``page:vue:<path>``).
Framework mode (``app/routes.ts``, ``flatRoutes()``) becomes ``page:<file>`` like Next.js, with
``route:GET`` / ``route:POST`` for ``loader`` / ``action``. Layouts are ``layout:`` nodes.
Navigation sites stay in ``nav_sites`` so ``nav.apply_nav`` emits NAVIGATES_TO. Forms and
fetchers are ``http:`` endpoints (client ``react-router``) matched in-repo with MATCHES_ROUTE.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..tsweb.common import add_route, express_path, module_of, pkg_deps, route_specs

_CONFIGS = ("react-router.config.ts", "react-router.config.js", "react-router.config.mjs",
            "react-router.config.cjs")
_APP_DIR = re.compile(r"""appDirectory\s*:\s*['"]([^'"]+)['"]""")


def _deps(root: Path) -> dict:
    return pkg_deps(root)


def is_react_router(root: Path) -> bool:
    """package.json dependency, a React Router config, or ``app/routes.ts``."""
    root = Path(root)
    deps = _deps(root)
    if any(k in deps for k in ("react-router", "react-router-dom")):
        return True
    if any(k.startswith("@react-router/") or k.startswith("@remix-run/") for k in deps):
        return True
    if any((root / n).is_file() for n in _CONFIGS):
        return True
    return any((root / n).is_file() for n in ("app/routes.ts", "app/routes.tsx", "app/routes.js"))


def framework_mode(root: Path) -> bool:
    deps = _deps(Path(root))
    return "@react-router/dev" in deps or "@remix-run/dev" in deps


def app_directory(root: Path) -> str:
    """Literal ``appDirectory`` in ``react-router.config.*`` (default ``app``)."""
    root = Path(root)
    for name in _CONFIGS:
        p = root / name
        if not p.is_file():
            continue
        m = _APP_DIR.search(p.read_text(encoding="utf-8", errors="replace")[:20000])
        if m:
            return m.group(1).strip("/") or "app"
    return "app"


def extractor_cfg(root: Path) -> dict | None:
    root = Path(root)
    if not is_react_router(root):
        return None
    return {"app_dir": app_directory(root), "framework": framework_mode(root)}


def _layout_key(route: dict) -> str | None:
    if route.get("mode") == "code":
        return "react-router:" + (route.get("path") or "/")
    return route.get("module") or route.get("file")


def apply_react_router(builder, project, facts: dict | None) -> dict:
    """Page, layout and loader/action route nodes from extractor ``rr_routes``."""
    facts = facts or {}
    routes = list(facts.get("rr_routes") or [])
    st = {"pages": 0, "layouts": 0, "http_routes": 0, "form_links": 0}
    if not routes:
        return st
    fw = framework_mode(project.root) or any(r.get("mode") in ("config", "flat") for r in routes)
    layout_ids: dict[str, str] = {}

    def layout_node(key: str, file: str | None, line: int, component: str | None) -> str | None:
        if not key:
            return None
        if key in layout_ids:
            return layout_ids[key]
        lid = builder.add_node("layout", key, name=file or key, file=file, line=line or 1,
                               module=module_of(file), lang="ts",
                               attrs={"framework": "react-router", "router": "framework" if fw and not key.startswith("react-router:") else "code"})
        builder.nodes[lid].entry_kind = "ui_global"
        if component and builder.has(component):
            builder.add_edge(lid, component, "RENDERS", file=file, line=builder.nodes[component].line or 1, confidence="exact")
        elif file and builder.has(f"module:{file}"):
            builder.add_edge(lid, f"module:{file}", "RENDERS", file=file, line=1, confidence="exact")
        layout_ids[key] = lid
        st["layouts"] += 1
        return lid

    for r in routes:
        if r.get("role") == "page":
            continue
        key = _layout_key(r)
        layout_node(key, r.get("module") or r.get("file"), r.get("line") or 1, r.get("component"))

    for r in routes:
        if r.get("role") == "layout":
            _calls(builder, None, r)
            if fw and r.get("mode") != "code":
                _http_routes(builder, r, st)
            continue
        path = r.get("path") or "/"
        uri = express_path(path)
        file = r.get("module") or r.get("file")
        line = r.get("line") or 1
        if r.get("mode") == "code":
            key = "react-router:" + path
            router = "code"
        else:
            key = file or path
            router = "framework"
        pid = builder.add_node("page", key, name=uri, fqn=path, file=file, line=1, module=module_of(file), lang="ts",
                               entry_kind="ui_page",
                               attrs={"route": path, "uri": uri, "framework": "react-router", "router": router})
        builder.nodes[pid].entry_kind = "ui_page"
        comp = r.get("component")
        if comp and builder.has(comp):
            builder.add_edge(pid, comp, "RENDERS", file=file, line=builder.nodes[comp].line or 1, confidence="exact")
        elif file and builder.has(f"module:{file}"):
            builder.add_edge(pid, f"module:{file}", "RENDERS", file=file, line=1, confidence="exact")
        _calls(builder, pid, r)
        for lk in r.get("layouts") or []:
            lid = layout_ids.get(lk)
            if lid and lid != pid:
                builder.add_edge(pid, lid, "USES_LAYOUT", file=file, line=1, confidence="exact")
        if fw and r.get("mode") != "code":
            _http_routes(builder, r, st)
        st["pages"] += 1
    st["form_links"] = _link_forms(builder)
    return st


def _calls(builder, pid: str | None, route: dict) -> None:
    file = route.get("module") or route.get("file")
    for via, key in (("loader", "loader"), ("action", "action"), ("clientLoader", "client_loader"), ("clientAction", "client_action")):
        fn = route.get(key)
        if not fn or not builder.has(fn):
            continue
        if pid:
            builder.add_edge(pid, fn, "CALLS", file=file, line=builder.nodes[fn].line or 1, confidence="exact", via=via)


_ACTION_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _form_methods(builder, uri: str) -> list[str]:
    """Methods a framework action handles: POST, plus PUT / PATCH / DELETE when a form or fetcher uses them."""
    from ...link import match_path
    found = ["POST"]
    for node in builder.nodes.values():
        attrs = node.attrs or {}
        if node.kind != "http" or attrs.get("client") != "react-router":
            continue
        method = (attrs.get("method") or "GET").upper()
        if method not in ("PUT", "PATCH", "DELETE"):
            continue
        ok, info = match_path(attrs.get("path") or "", uri)
        if ok and info["lit"] > 0 and method not in found:
            found.append(method)
    return found


def _http_routes(builder, route: dict, st: dict) -> None:
    """Framework-mode loader / action run on the server. clientLoader / clientAction stay calls.

    One action function handles every non-GET method. ``route:POST`` is always recorded. A
    ``<Form method="delete">`` (or put / patch, including fetcher.submit / useSubmit) adds
    ``route:DELETE`` (or PUT / PATCH) that ROUTES_TO the same action.
    """
    path = route.get("path") or "/"
    uri = express_path(path)
    file = route.get("module") or route.get("file")
    if route.get("loader") and builder.has(route["loader"]):
        fn = route["loader"]
        add_route(builder, "GET", uri, [fn], file, builder.nodes[fn].line or 1, "react-router", "exact",
                  {"router": "framework"})
        st["http_routes"] += 1
    if route.get("action") and builder.has(route["action"]):
        fn = route["action"]
        for method in _form_methods(builder, uri):
            if method not in _ACTION_METHODS:
                continue
            add_route(builder, method, uri, [fn], file, builder.nodes[fn].line or 1, "react-router", "exact",
                      {"router": "framework"})
            st["http_routes"] += 1


def routes_found(stats: dict | None) -> bool:
    ts = ((stats or {}).get("plugins") or {}).get("typescript") or {}
    rr = ts.get("react_router") or {}
    if not isinstance(rr, dict):
        return False
    return any(rr.get(k) for k in ("pages", "layouts", "http_routes"))


def incidental_next_dependency(root: Path) -> bool:
    """Next.js app that lists ``react-router`` but has no React Router config or ``routes`` module.

    A transitive copy under ``node_modules`` is not a dependency here (``package.json`` only). Routes
    discovered in source still count; ``reconcile_detection`` keeps the framework when that happens.
    """
    root = Path(root)
    deps = _deps(root)
    next_app = "next" in deps or any((root / n).is_file() for n in (
        "next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs"))
    if not next_app or not is_react_router(root):
        return False
    if any((root / n).is_file() for n in _CONFIGS):
        return False
    return not any((root / n).is_file() for n in (
        "app/routes.ts", "app/routes.tsx", "app/routes.js", "app/routes.jsx"))


def reconcile_detection(project, plan: dict, stats: dict | None) -> None:
    """Drop React Router on a Next.js app whose dependency never produced a route. Keep it when routes exist."""
    names = plan.get("frameworks")
    if not isinstance(names, dict):
        return
    found = routes_found(stats)
    changed = False
    if incidental_next_dependency(project.root) and not found and "react-router" in names:
        names.pop("react-router", None)
        changed = True
    elif found and "react-router" not in names:
        names["react-router"] = "detected"
        changed = True
    if not changed:
        return
    from ...presets import select
    applied = select(plan.get("languages") or [], list(names))
    plan["presets"] = applied
    project.options["presets"] = applied
    block = stats.get("presets") if isinstance(stats, dict) else None
    if isinstance(block, dict):
        block["frameworks"] = names
        block["presets"] = applied


def _link_forms(builder) -> int:
    """Form / fetcher calls target this repo's React Router actions. Same-origin page URLs are not API calls,
    so the shared in-repo pass leaves them alone; match them here."""
    from ...link import match_endpoint
    routes = [r for r in route_specs(builder)
              if (builder.nodes[r["id"]].attrs or {}).get("framework") == "react-router"]
    if not routes:
        return 0
    rmap = {r["id"]: r for r in routes}
    n = 0
    for node in list(builder.nodes.values()):
        if node.kind != "http" or (node.attrs or {}).get("client") != "react-router":
            continue
        res = match_endpoint(node.attrs.get("method") or "GET", node.attrs.get("path") or "", routes, "api", None)
        for m in res["matched"]:
            r = rmap[m["route"]]
            builder.add_edge(node.id, m["route"], "MATCHES_ROUTE", file=r["file"], line=r["line"],
                             confidence=m["confidence"], client_path=node.attrs.get("path"),
                             uri_variant=m["uri_variant"], segments=m["segments"], in_repo=True)
            n += 1
    return n

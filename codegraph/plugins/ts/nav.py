"""Vue / Nuxt navigation: NAVIGATES_TO from NuxtLink, RouterLink, router.push / replace, navigateTo and
internal <a href> to page nodes.

The extractor records each site (a literal path, a route name, or the literal a helper returns one call deep,
including useRouter().resolve({ name })). This pass matches them to Nuxt file routes and to vue-router
createRouter routes. A target that matches no page, or several pages equally, stays unresolved
(builder.nav_unresolved, listed by cg coverage).
"""
from __future__ import annotations

import re

from ...tests_index import page_pattern
from ...link import match_path

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def default_route_name(route: str) -> str:
    """Nuxt's file-based name for a route path: /settings/profile -> settings-profile, / -> index."""
    segs = []
    for s in (route or "").strip("/").split("/"):
        if not s:
            continue
        m = re.match(r":(\w+)", s)
        segs.append(m.group(1) if m else s)
    return "-".join(segs) if segs else "index"


def clean_path(value: str) -> str | None:
    if not value or not isinstance(value, str):
        return None
    v = value.strip().split("?")[0].split("#")[0].strip()
    if not v or v.startswith("#") or v.startswith("//") or _SCHEME.match(v):
        return None
    if "{?}" in v:
        return None
    if not v.startswith("/"):
        return None  # relative to the current route, or a computed base (`${base}/x`): not a page we can name
    if len(v) > 1 and v.endswith("/"):
        v = v[:-1]
    return v


_PH_SEG = re.compile(r"\{[^{}/]*\}")
_FILE_SEG = re.compile(r"\.[A-Za-z0-9]{1,5}$")


_OPT = re.compile(r"^\{(\w+)\?\}$")
_CATCH = re.compile(r"\{\w+\*\??\}$")


def _omit_optional(pat: str) -> list[str]:
    """`/{server?}/search` also matches as `/search` (Nuxt `[[server]]`)."""
    segs = pat.split("/")
    idxs = [i for i, s in enumerate(segs) if _OPT.match(s)]
    out = []
    for mask in range(1 << len(idxs)):
        drop = {idxs[b] for b in range(len(idxs)) if mask & (1 << b)}
        kept = [s for i, s in enumerate(segs) if i not in drop]
        p = re.sub(r"/+", "/", "/".join(kept)) or "/"
        if not p.startswith("/"):
            p = "/" + p
        out.append(p)
    return out


def _score(client: str, pat: str) -> tuple | None:
    best = None
    for cand in _omit_optional(pat):
        ok, info = match_path(client, cand)
        if not ok:
            continue
        exact = 1 if client == cand else 0
        # a computed client segment prefers a route param over a literal page (`/users/${uid}`: users/[id], not
        # users/new)
        sc = (exact, info["lit"], -info.get("ph_into_lit", 0), info.get("lit_into_param", 0), -info["param"])
        if best is None or sc > best:
            best = sc
    return best


def _catch_all(route: str) -> bool:
    return "(.*)" in (route or "") or bool(_CATCH.search(page_pattern(route or "")))


def _pages(builder) -> list:
    out = []
    for n in builder.nodes.values():
        if n.kind != "page":
            continue
        route = (n.attrs or {}).get("route")
        if not isinstance(route, str) or not route:
            continue
        out.append(n)
    return out


def _name_pages(pages) -> dict[str, list]:
    by = {}
    for n in pages:
        name = (n.attrs or {}).get("route_name")
        if name:
            by.setdefault(name, []).append(n)
    return by


def _assign_route_names(builder, page_meta: dict) -> None:
    for n in _pages(builder):
        if (n.attrs or {}).get("route_name"):
            continue
        meta = (page_meta or {}).get(n.file) or {}
        n.attrs["route_name"] = meta.get("name") or default_route_name(n.attrs.get("route") or "")


def _add_vue_pages(builder, routes: list) -> int:
    n_new = 0
    seen = {(n.attrs or {}).get("route") for n in _pages(builder)}
    # a child with path '' renders at its parent's URL: the child's view is the page there (parents come first)
    by_path: dict[str, dict] = {}
    for r in routes or []:
        path = r.get("path") or "/"
        prev = by_path.get(path)
        if prev is None or r.get("component"):
            by_path[path] = {**r, "name": r.get("name") or (prev or {}).get("name")}
    for path, r in by_path.items():
        if path in seen:
            continue
        comp = r.get("component")
        key = "vue:" + path
        builder.add_node("page", key, path, fqn=path, file=comp or r.get("file"), line=r.get("line") or 1,
                         lang="ts", entry_kind="ui_page",
                         attrs={"route": path, "route_name": r.get("name") or default_route_name(path),
                                "via": "vue-router", **({"redirect": r["redirect"]} if r.get("redirect") else {})})
        seen.add(path)
        n_new += 1
    return n_new


def _owner(builder, site: dict) -> str | None:
    src = site.get("src")
    if src and builder.has(src):
        return src
    f = site.get("file")
    if not f:
        return None
    for kind in ("page", "component", "layout", "app", "module"):
        nid = f"{kind}:{f}"
        if builder.has(nid):
            return nid
    return None


def _match_path(client: str, pages) -> list:
    scored = []
    by_id = {}
    last = client.rstrip("/").rsplit("/", 1)[-1]
    file_like = bool(_FILE_SEG.search(last)) and not _PH_SEG.search(last)
    segs = [s for s in client.split("/") if s]
    computed = bool(segs) and all(_PH_SEG.fullmatch(s) for s in segs)
    for n in pages:
        if _catch_all(n.attrs["route"]):
            continue  # a catch-all matches every path; it is not a guess
        pat = page_pattern(n.attrs["route"])
        if file_like and pat.rstrip("/").rsplit("/", 1)[-1] != last:
            continue  # `/favicon.ico`, `/docs/guide.pdf`: a static file, not the `[slug]` page
        by_id[n.id] = n
        sc = _score(client, pat)
        if sc is not None and computed and sc[2] < 0:
            continue  # `/${slug}`: every segment computed, so only a page with params there (`[slug]`) fits
        if sc is not None:
            scored.append((sc, n.id))
    if not scored:
        return []
    best = max(s[0] for s in scored)
    ids = [nid for sc, nid in scored if sc == best]
    if len(ids) == 1:
        return ids
    ns = [by_id[i] for i in ids]
    if len({n.attrs.get("route") for n in ns}) != 1:
        return ids  # different routes, same score: unresolved
    # parent `x.vue` and child `x/index.vue` share one URL; the child is the page
    idx = [n for n in ns if (n.file or "").endswith("/index.vue")]
    if len(idx) == 1:
        return [idx[0].id]
    leaf = max(ns, key=lambda n: len(n.file or ""))
    same = [n for n in ns if len(n.file or "") == len(leaf.file or "")]
    return [leaf.id] if len(same) == 1 else ids


def _follow_redirect(builder, dst: str, pages) -> str:
    """A vue-router `{ path: '/', redirect: '/dashboard' }` route lands on the redirect's page (one hop)."""
    n = builder.nodes.get(dst)
    to = clean_path(((n.attrs or {}).get("redirect") or "")) if n is not None else None
    if not to:
        return dst
    mids = _match_path(to, pages)
    return mids[0] if len(mids) == 1 else dst


def apply_nav(builder, facts: dict | None) -> dict:
    """Create vue-router page nodes and NAVIGATES_TO edges. Safe to run twice (edges dedupe, unresolved is replaced)."""
    facts = facts or {}
    _add_vue_pages(builder, facts.get("vue_routes") or [])
    _assign_route_names(builder, facts.get("page_meta") or {})
    pages = _pages(builder)
    by_name = _name_pages(pages)
    edges = 0
    unresolved = []
    sites = facts.get("nav_sites") or []
    for site in sites:
        owner = _owner(builder, site)
        if not owner:
            unresolved.append((site.get("file") or "?", site.get("line") or 1))
            continue
        hits = []  # (dst, conf, via, target)
        for loc in site.get("locs") or []:
            kind, value, conf = loc.get("kind"), loc.get("value"), loc.get("conf") or "exact"
            helper = bool(loc.get("helper"))
            via = "helper" if helper else (site.get("via") or "link")
            if helper:
                conf = "resolved"
            if kind == "name" and value:
                named = by_name.get(value) or []
                if len(named) == 1:
                    hits.append((named[0].id, conf, via, value))
                continue
            if kind == "path":
                client = clean_path(value)
                if not client:
                    continue
                mids = _match_path(client, pages)
                if len(mids) == 1:
                    hits.append((mids[0], conf, via, client))
        if not hits:
            unresolved.append((site.get("file") or "?", site.get("line") or 1))
            continue
        hits = [(_follow_redirect(builder, dst, pages), conf, via, target) for dst, conf, via, target in hits]
        seen = set()
        for dst, conf, via, target in hits:
            key = (dst, via, target)
            if key in seen:
                continue
            seen.add(key)
            attrs = {"via": via, "target": target}
            if via == "helper" and site.get("via"):
                attrs["site"] = site["via"]
            builder.add_edge(owner, dst, "NAVIGATES_TO", site.get("file"), site.get("line"), conf, **attrs)
            edges += 1
    builder.nav_unresolved = unresolved
    return {"sites": len(sites), "edges": edges, "unresolved": len(unresolved),
            "vue_routes": len(facts.get("vue_routes") or [])}

"""Keep test code out of the application graph.

Language / framework plugins index test code (PHPUnit / Pest under tests/, Vitest / Jest / Playwright spec files)
with `attrs.test = True` on every node it declares, plus `test` nodes (one per test case). This pass retypes every
edge that touches test code into a non-propagating TEST_* kind, keeping the original kind in attrs.orig:

  * call-like edges (CALLS, DISPATCHES, ROUTES_TO, ...)         -> TEST_CALLS
  * everything else that propagates or references (tables, config, INSTANTIATES, REFERENCES, IMPORTS, ...)
                                                                -> TEST_USES
  * EXTENDS / IMPLEMENTS / USES_TRAIT from test code to the app (fakes, test doubles) -> TEST_USES
  * CONTAINS and structure inside test code stay as they are; TEST_HTTP / TEST_VISITS are already test kinds.

So callers, impact, reaches, entry tagging and `unguarded` never see tests, and an application method called only
from tests still counts as uncalled. `cg tests <symbol|route>` (query.tests_covering) walks the TEST_* edges.
"""
from __future__ import annotations

import re

from .core.model import EDGE_KINDS, TEST_EDGE_KINDS

CALL_KINDS = {"CALLS", "IMPLEMENTED_BY", "OVERRIDDEN_BY", "BOUND_TO", "ROUTES_TO", "USES_MIDDLEWARE", "HANDLED_BY",
              "SCHEDULES", "DISPATCHES", "LISTENED_BY", "RENDERS", "USES_COMPOSABLE", "USES_STORE", "HTTP_CALLS",
              "REFERENCES_FN", "SUBSCRIBES_CHANNEL", "NAVIGATES_TO", "INSTANTIATES"}
KEEP = {"CONTAINS", *TEST_EDGE_KINDS}
STRUCTURAL = {"EXTENDS", "IMPLEMENTS", "USES_TRAIT"}


def is_test_node(n) -> bool:
    return n.kind == "test" or bool((n.attrs or {}).get("test"))


def page_pattern(route: str) -> str:
    """Frontend page route (`/shelves/:id`, `/docs/:slug(.*)*`, `/[id]`) -> path template with {params}."""
    r = re.sub(r":(\w+)\(\.\*\)\*", r"{\1*?}", route)
    r = re.sub(r":(\w+)\?", r"{\1?}", r)
    r = re.sub(r":(\w+)", r"{\1}", r)
    r = re.sub(r"\[\.\.\.(\w+)\]", r"{\1*}", r)
    return re.sub(r"\[(\w+)\]", r"{\1}", r)


def resolve_visits(builder) -> int:
    """page.goto('/x') / cy.visit('/x') facts (left by the TS plugin) -> TEST_VISITS edges to the matching page."""
    from .link import match_path
    visits = getattr(builder, "pending_visits", None) or []
    pages = [(nid, page_pattern(n.attrs["route"])) for nid, n in builder.nodes.items()
             if n.kind == "page" and isinstance((n.attrs or {}).get("route"), str)]
    n = 0
    for v in visits:
        url = re.sub(r"^[a-z]+://[^/]*|^\{[^{}/]*\}(?=/)", "", v["url"]).split("?")[0].split("#")[0] or "/"
        if not url.startswith("/"):
            url = "/" + url
        hits = []
        for nid, pat in pages:
            ok, info = match_path(url, pat)
            if ok and (info["lit"] or pat == url):
                hits.append((info["lit"], -info["ph_into_lit"], nid))
        if url == "/":
            hits = [(1, 0, nid) for nid, pat in pages if pat == "/"]
        if not hits:
            continue
        best = max(h[:2] for h in hits)
        top = [h[2] for h in hits if h[:2] == best]
        for nid in top:
            builder.add_edge(v["src"], nid, "TEST_VISITS", v["file"], v["line"], "exact" if len(top) == 1 else "heuristic",
                             url=v["url"], via=v.get("via"))
            n += 1
    builder.pending_visits = []
    return n


def isolate_tests(builder) -> dict:
    visits = resolve_visits(builder)
    tests = {nid for nid, n in builder.nodes.items() if is_test_node(n)}
    st = {"test_nodes": sum(1 for n in builder.nodes.values() if n.kind == "test"), "test_code_nodes": len(tests),
          "edges_retyped": 0, "page_visits": visits}
    if not tests:
        return st
    for key, e in list(builder.edges.items()):
        if e.kind in KEEP or e.kind not in EDGE_KINDS:
            continue
        s_test, d_test = e.src in tests, e.dst in tests
        if not (s_test or d_test):
            continue
        if e.kind in STRUCTURAL and (s_test == d_test):
            continue   # test class extends test base class: structure inside the test suite
        new = "TEST_CALLS" if e.kind in CALL_KINDS else "TEST_USES"
        builder.retype_edge(key, new, orig=e.kind)
        st["edges_retyped"] += 1
    return st


def link_local_channels(builder) -> dict:
    """Backend channels and Echo / pusher-js subscriptions in the same checkout (a Laravel app with resources/js):
    match them here, as cg link does across two graphs."""
    from collections import defaultdict
    from .link import channel_links
    subs = [(nid, n.attrs or {}) for nid, n in builder.nodes.items() if n.kind == "channel_sub"]
    chans = [(nid, n.attrs or {}, n.file, n.line) for nid, n in builder.nodes.items() if n.kind == "channel"]
    if not subs or not chans:
        return {}
    events = {nid: n.attrs for nid, n in builder.nodes.items() if n.kind == "event" and (n.attrs or {}).get("broadcast")}
    on = defaultdict(set)
    for e in builder.edges.values():
        if e.kind == "BROADCASTS_ON":
            on[e.dst].add(e.src)
    rows, st = channel_links(chans, subs, events, on)
    for s, d, k, c, a, f, l in rows:
        builder.add_edge(s, d, k, f, l, c, **a)
    return st

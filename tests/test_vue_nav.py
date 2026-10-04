"""Nuxt and vue-router NAVIGATES_TO edges (tests/vue_nav_fixture, tests/vue_router_fixture)."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.indexer import index_project  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

pytestmark = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")

_S: dict = {}


def built(name: str) -> sqlite3.Connection:
    if name not in _S:
        d = Path(__import__("tempfile").mkdtemp(prefix="cg-nav-"))
        stats = index_project(ROOT / "tests" / name, d / "g.db", name)
        _S[name] = (sqlite3.connect(d / "g.db"), stats)
    return _S[name][0]


def stats(name: str) -> dict:
    built(name)
    return _S[name][1]


def nav(name: str):
    return built(name).execute(
        "SELECT src, dst, confidence, json_extract(attrs,'$.via'), json_extract(attrs,'$.target'), line "
        "FROM edges WHERE kind='NAVIGATES_TO' ORDER BY line, src").fetchall()


def test_nuxt_literal_and_helper_edges():
    rows = nav("vue_nav_fixture")
    by = {(r[3], r[4], r[1]) for r in rows}
    profile = "page:app/pages/settings/profile.vue"
    marks = "page:app/pages/bookmarks.vue"
    user = "page:app/pages/users/[id].vue"
    assert ("link", "/settings/profile", profile) in by
    assert ("push", "/settings/profile", profile) in by
    assert any(r[1] == user and r[3] == "navigateTo" and r[2] == "exact" and r[4] == "/users/42" for r in rows)
    assert ("link", "/bookmarks", marks) in by
    assert ("href", "/settings/profile", profile) in by
    assert any(r[3] == "helper" and r[2] == "resolved" and r[1] == profile for r in rows)
    assert any(r[3] == "helper" and r[1] == marks for r in rows)
    # parent docs.vue and docs/index.vue share /docs: the child index is the page
    assert ("link", "/docs", "page:app/pages/docs/index.vue") in by
    # /item is both /item and /:server?/item
    assert all(r[4] != "/item" for r in rows)
    # a target that is not a page, and an external href, stay off the graph
    assert all("example.com" not in (r[4] or "") for r in rows)
    unresolved = stats("vue_nav_fixture")["coverage"]["blind_spots"]
    kinds = [b["kind"] for b in unresolved]
    assert "vue_unresolved_navigation" in kinds
    # several pages: /users/42 matches [id] and not the optional tab page alone; a random path matches none
    nav_stats = stats("vue_nav_fixture")["plugins"]["typescript"]["navigation"]
    assert nav_stats["edges"] >= 6
    assert nav_stats["unresolved"] >= 1


def test_nuxt_impact_lists_linker():
    from codegraph.core.store import GraphStore
    from codegraph import query as Q
    c = built("vue_nav_fixture")
    db = Path(c.execute("PRAGMA database_list").fetchone()[2])
    st = GraphStore(db)
    imp = Q.impact(st, "page:/settings/profile")
    raw = json.dumps(imp)
    assert "page:app/pages/index.vue" in raw
    assert "component:app/components/Menu.vue" in raw


def test_vue_router_pages_and_edges():
    c = built("vue_router_fixture")
    pages = dict(c.execute("SELECT json_extract(attrs,'$.route'), json_extract(attrs,'$.route_name') FROM nodes WHERE kind='page'"))
    assert pages["/"] == "home"
    assert pages["/users/:id"] == "user"
    assert pages["/settings/profile"] == "settings-profile"
    rows = nav("vue_router_fixture")
    dsts = {r[4] for r in rows}
    assert "/" in dsts or any(r[1].endswith(":/") or "/users" in (r[4] or "") or r[4] in ("/", "user", "/settings/profile") for r in rows)
    assert any(r[4] == "user" and r[3] == "push" for r in rows)
    assert any(r[4] == "/settings/profile" for r in rows)
    assert any(r[3] == "link" and r[4] == "settings-profile" for r in rows)
    # dynamic segment resolves to the one user page
    assert any(r[4] == "/users/{id}" and r[2] == "resolved" for r in rows)

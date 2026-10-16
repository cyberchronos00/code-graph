"""Nuxt Nitro server routes (#216): tests/nuxt_nitro_fixture is a Nuxt 4 layout (app/, server/) with no node_modules
and no .nuxt/. Files under server/api and server/routes become route nodes linked to their handlers and to the app's
same-origin $fetch / useFetch / useLazyFetch calls."""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from cg_code_graph.plugins.nuxt.plugin import nitro_route, server_dirs  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

FX = ROOT / "tests" / "nuxt_nitro_fixture"
needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")


@pytest.mark.parametrize("rel,prefix,want", [
    ("plants.get.ts", "/api", ("GET", "/api/plants")),
    ("plants/[id].put.ts", "/api", ("PUT", "/api/plants/{id}")),
    ("orders/index.ts", "/api", ("ANY", "/api/orders")),
    ("index.get.ts", "/api", ("GET", "/api")),
    ("[...slug].ts", "/api", ("ANY", "/api/{slug*}")),
    ("[...].ts", "", ("ANY", "/{_*}")),
    ("sitemap.xml.ts", "", ("ANY", "/sitemap.xml")),
    ("(admin)/users.post.ts", "/api", ("POST", "/api/users")),
    ("stats.get.prod.ts", "/api", ("GET", "/api/stats")),
    ("feed/[slug].json.get.ts", "", ("GET", "/feed/{slug}.json")),
    ("types.d.ts", "/api", None),
    ("plants.spec.ts", "/api", None),
    ("README.md", "/api", None),
])
def test_nitro_route_paths(rel, prefix, want):
    assert nitro_route(rel, prefix) == want


def test_server_dirs(tmp_path):
    assert server_dirs(FX, "app") == ["server"]
    (tmp_path / "src" / "server").mkdir(parents=True)
    assert server_dirs(tmp_path, "src") == ["src/server"]
    (tmp_path / "server").mkdir()
    assert server_dirs(tmp_path, ".") == ["server"]


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    from cg_code_graph.indexer import index_project
    db = tmp_path_factory.mktemp("nitro") / "g.db"
    return index_project(FX, db, "plant-shop"), db


@needs_ts
def test_nitro_routes_link_frontend_to_handlers(indexed):
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph import query
    stats, db = indexed
    nitro = stats["plugins"]["typescript/nuxt"]["nitro"]
    assert nitro == {"routes": 6, "middleware": 1, "plugins": 1, "proxy_routes": 0, "linked_calls": 4}
    con = sqlite3.connect(db)
    routes = {r[0] for r in con.execute("SELECT id FROM nodes WHERE kind='route'")}
    assert routes == {"route:GET /api/plants", "route:POST /api/plants", "route:GET /api/plants/{id}",
                      "route:ANY /api/orders", "route:ANY /api/{slug*}", "route:ANY /sitemap.xml"}
    edges = set(con.execute("SELECT src, dst, kind FROM edges WHERE kind IN ('ROUTES_TO','MATCHES_ROUTE','USES_MIDDLEWARE')"))
    assert ("route:GET /api/plants", "function:server/api/plants.get.ts#default", "ROUTES_TO") in edges
    assert ("route:GET /api/plants/{id}", "function:server/api/plants/[id].get.ts#default", "ROUTES_TO") in edges
    assert ("route:ANY /sitemap.xml", "function:server/routes/sitemap.xml.ts#default", "ROUTES_TO") in edges
    assert ("route:GET /api/plants", "function:server/middleware/log.ts#default", "USES_MIDDLEWARE") in edges
    assert ("http:GET /api/plants", "route:GET /api/plants", "MATCHES_ROUTE") in edges
    assert ("http:POST /api/plants", "route:POST /api/plants", "MATCHES_ROUTE") in edges
    assert ("http:GET /api/plants/{id}", "route:GET /api/plants/{id}", "MATCHES_ROUTE") in edges
    assert ("http:GET /api/orders", "route:ANY /api/orders", "MATCHES_ROUTE") in edges
    # the specific route wins over the catch-all
    assert not any(d == "route:ANY /api/{slug*}" for s, d, k in edges if k == "MATCHES_ROUTE")
    # middleware and plugins are labelled, not routes
    kinds = dict(con.execute("SELECT id, json_extract(attrs, '$.nitro') FROM nodes WHERE file LIKE 'server/%' AND kind='module'"))
    assert kinds["module:server/middleware/log.ts"] == "middleware" and kinds["module:server/plugins/boot.ts"] == "plugin"
    # impact of a server util crosses to the pages that fetch it
    st = GraphStore(db)
    res = query.impact(st, "listPlants")
    entries = {e["name"] for e in res["entry_points"]}
    assert "/" in entries and "/plants/:id" in entries and "GET /api/plants" in entries


@needs_ts
def test_nested_playwright_tree_is_indexed_as_tests(indexed):
    """#156: help/playwright/ is a test root like a top-level playwright/: its specs are test nodes, its helpers are
    test code that `search` finds, and neither is reported as unmapped / excluded."""
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph import query
    stats, db = indexed
    con = sqlite3.connect(db)
    nodes = dict(con.execute("SELECT id, json_extract(attrs, '$.test') FROM nodes WHERE file LIKE 'help/playwright/%'"))
    assert "test:help/playwright/shop.spec.ts#lists plants" in nodes
    assert nodes["function:help/playwright/support.ts#openShop"] == 1
    edges = set(con.execute("SELECT src, dst, kind FROM edges WHERE file LIKE 'help/%'"))
    assert ("test:help/playwright/shop.spec.ts#lists plants", "http:GET /api/plants", "TEST_HTTP") in edges
    assert ("function:help/playwright/support.ts#openShop", "page:app/pages/index.vue", "TEST_VISITS") in edges
    found = query.search(GraphStore(db), "openShop")
    assert any(r["id"] == "function:help/playwright/support.ts#openShop" for r in found["nodes"])
    ts = next(e for e in stats["coverage"]["languages"] if e["language"] == "typescript")
    assert not [p for p in (ts["paths"].get("unmapped") or []) + (ts["paths"].get("excluded") or []) if p.startswith("help/")]

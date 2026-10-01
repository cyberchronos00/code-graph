"""TypeScript/Vue/Nuxt plugin + cross-repo link on the sample apps (examples/bookstore-web -> examples/bookstore-api).

The web sample ships a hand-written `.nuxt/` (same shape `nuxi prepare` generates) so no network or
`nuxi` run is needed; the extractor's own TypeScript (codegraph/plugins/ts/extractor/node_modules) is used.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.link import match_endpoint, match_path  # noqa: E402
from codegraph import query as Q  # noqa: E402
from sample import build, EXTRACTOR_DEPS  # noqa: E402

pytestmark = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")


def fe():
    return sqlite3.connect(build()["web"])


def edge(src, kind, dst):
    r = fe().execute("SELECT confidence, attrs FROM edges WHERE src=? AND kind=? AND dst=?", (src, kind, dst)).fetchone()
    return (r[0], json.loads(r[1] or "{}")) if r else None


PAGE = "page:app/pages/reports/[id].vue"
REPORTS = "app/composables/useReports.ts#useReports"


def test_page_nodes_and_routes():
    rows = dict(fe().execute("SELECT id, name FROM nodes WHERE kind='page'").fetchall())
    assert rows == {PAGE: "/reports/:id", "page:app/pages/index.vue": "/"}
    assert fe().execute("SELECT entry_kind FROM nodes WHERE id=?", (PAGE,)).fetchone()[0] == "ui_page"


def test_auto_imports_destructure_store_and_template():
    assert edge(PAGE, "USES_COMPOSABLE", f"composable:{REPORTS}")[1]["via"] == ["nuxt-auto-import"]
    assert edge(PAGE, "CALLS", f"function:{REPORTS}.fetchTop")[1]["arg_keys"] == [["category_id", "date_from"]]
    assert edge(PAGE, "CALLS", f"function:{REPORTS}.exportTop")[1]["template"] is True
    assert edge(PAGE, "USES_STORE", "store:app/stores/useSessionStore.ts#useSessionStore")
    assert edge(PAGE, "CALLS", "function:app/stores/useSessionStore.ts#useSessionStore.load")
    assert edge(PAGE, "RENDERS", "component:app/components/TopTable.vue")[1]["via"] == ["nuxt-global-component"]
    assert edge("component:app/components/TopTable.vue", "CALLS", "function:app/utils/money.ts#formatMoney")
    assert edge(PAGE, "USES_I18N", "i18n:title") and edge(PAGE, "USES_LAYOUT", "layout:app/layouts/default.vue")


def test_jsdoc_attached():
    doc = fe().execute("SELECT doc FROM nodes WHERE id=?", (f"function:{REPORTS}.fetchTop",)).fetchone()[0]
    assert doc == "Top sellers report."


def test_http_calls_base_url_union_and_request_keys():
    c, a = edge(f"function:{REPORTS}.fetchTop", "HTTP_CALLS", "http:GET /api/v1/main/admin/reports/top")
    assert a["client"] == "axios-instance" and a["base"] == "{runtimeConfig.SERVER_API_URL}/v1/main"
    assert a["query_keys"] == {"keys": ["mode"], "conditional": ["category_id", "timezone"]}
    for fmt in ("csv", "xlsx"):  # string-literal union expanded
        assert edge(f"function:{REPORTS}.exportTop", "HTTP_CALLS", f"http:GET /api/v1/main/admin/reports/top/export.{fmt}")
    assert edge("page:app/pages/index.vue", "HTTP_CALLS", "http:DELETE /api/v1/main/admin/reports/{id}")
    c, a = edge("composable:app/composables/useReports.ts#useAppVersion", "HTTP_CALLS", "http:GET /version.json")
    assert a["client"] == "$fetch" and a["query_keys"]["keys"] == ["t"]
    na = json.loads(fe().execute("SELECT attrs FROM nodes WHERE id='http:GET /version.json'").fetchone()[0])
    assert na["origin_kind"] == "same-origin"


def test_match_path_rules():
    assert match_path("/v1/{storeSlug}/admin/x", "/v1/{store}/admin/x")[0]
    assert match_path("/v1/main/admin/x", "/v1/{store}/admin/x")[0]
    assert match_path("/admin/x/export.csv", "/admin/x/export.{format}")[0]
    assert match_path("/admin/x/export.{fmt}", "/admin/x/export.{format}")[1]["param"] == 1
    assert match_path("/admin/x/export.{fmt}", "/admin/x/export.csv")[1]["ph_into_lit"] == 1
    assert not match_path("/admin/x", "/admin/y")[0]
    assert match_path("/{a}/{b}", "/{c}/{d}")[1]["lit"] == 0  # rejected by match_endpoint: no agreeing literal
    routes = [{"id": "route:GET /api/v1/{s}/a", "uri": "/api/v1/{s}/a", "method": "GET", "uris": [("as-declared", "/api/v1/{s}/a")]}]
    assert match_endpoint("GET", "/api/v1/{storeSlug}/a", routes)["matched"][0]["confidence"] == "exact"
    assert match_endpoint("POST", "/api/v1/{storeSlug}/a", routes)["reason"].startswith("method")


def test_link_confidence_and_match_rate():
    res = build()["link"]
    by = {r["endpoint"]: r for r in res["results"]}
    assert by["http:GET /api/v1/main/admin/reports/top"]["matched"][0]["confidence"] == "resolved"  # main -> {store}
    assert by["http:DELETE /api/v1/main/admin/reports/{id}"]["matched"][0]["route"] == "route:DELETE /v1/{store}/admin/reports/{report}"
    assert by["http:GET /api/v1/main/admin/reports/top/export.csv"]["matched"][0]["confidence"] == "resolved"
    assert (res["stats"]["endpoints_matched"], res["stats"]["endpoints"]) == (4, 6)
    assert by["http:GET /version.json"]["reason"].startswith("not a backend URL: same-origin")
    assert by["http:GET /api/v1/main/admin/session"]["reason"].startswith("no backend route")  # the sample API has no such route


def test_cross_repo_impact_and_downstream():
    st = GraphStore(build()["combined"])
    imp = Q.impact(st, "ReportController::destroy")
    assert any(e["kind"] == "page" and e["name"] == "/" and e["entry_kind"] == "ui_page" for e in imp["entry_points"])
    ds = Q.downstream(st, "page:/")
    assert [t["table"] for t in ds["tables_touched"]] == ["orders"]
    p = Q.path_between(st, "page:/", "SalesReportService::remove")
    assert [s["kind"] for s in p] == ["HTTP_CALLS", "MATCHES_ROUTE", "ROUTES_TO", "CALLS"]


def test_mcp_tools_on_combined_and_reindex_repo():
    from codegraph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE["db"] = str(build()["combined"])
        assert "HTTP_CALLS" in M.path("page:/", "SalesReportService::remove")
        assert "/" in M.impact("ReportController::destroy")
        assert "orders" in M.downstream("page:/")
        assert "UNMATCHED" in M.api_calls("unmatched")
        out = M.index(repo="bookstore-web")  # re-index the frontend's own DB, then relink
        assert out.startswith("re-indexed bookstore-web") and "3/5 call sites matched" in out
    finally:
        M.STATE.clear(); M.STATE.update(old)


def test_path_to_table_follows_its_columns():
    """A table reached only through column reads still has a path (same rule as the visual view)."""
    st = GraphStore(build()["combined"])
    src = Q.resolve_targets(st, "page:/reports/:id")
    assert Q._bfs_path(st, src, {"table:orders"}, "heuristic", 30) == []  # no direct table edge on this path
    p = Q.path_between(st, "page:/reports/:id", "table:orders")
    assert p and p[0]["from"].startswith("page:") and p[-1]["kind"] == "READS_COLUMN"
    assert p[-1]["to"] == "column:orders.placed_at"
    assert "ROUTES_TO" in [s["kind"] for s in p]
    # no column fallback for a different table whose name shares a prefix
    assert all(not s["to"].startswith("column:orders_") for s in p)

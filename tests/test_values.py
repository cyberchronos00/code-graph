"""Value facts + concept query on the sample apps (examples/bookstore-api + examples/bookstore-web).

The sample has a deliberate divergence: SalesReportService::build (`$filters['timezone'] ?? getSetting('reports.timezone',
'UTC') ?: 'UTC'`, request data flowing in through ReportController::top -> report()) vs ReportController::resolveTimezone
(explicit -> latest order customer_timezone -> setting -> store default -> 'UTC'), and a page that calls fetchTop
without `timezone` and falls back to 'UTC' itself.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.concepts import resolutions, render_resolutions  # noqa: E402
from sample import build, EXTRACTOR_DEPS  # noqa: E402

BUILD = "method:App\\Services\\SalesReportService::build"
RESOLVE = "method:App\\Http\\Controllers\\ReportController::resolveTimezone"


def edges(kind, src=None, dst=None):
    c = sqlite3.connect(build()["api"])
    q, args = "SELECT src, dst, attrs FROM edges WHERE kind=?", [kind]
    if src:
        q += " AND src=?"; args.append(src)
    if dst:
        q += " AND dst=?"; args.append(dst)
    return [(s, d, json.loads(a or "{}")) for s, d, a in c.execute(q, args)]


def test_settings_are_nodes_with_read_edges_and_literal_default():
    (s, d, a), = edges("READS_SETTING", src=BUILD)
    assert d == "setting:reports.timezone" and a["default"] == "UTC" and a["owner"] == ["Store"]
    assert edges("READS_SETTING", src=RESOLVE, dst="setting:locale.timezone")[0][2]["default"] == ""


def test_request_keys_formrequest_rules_and_flow_through_two_helper_levels():
    v = {d: a for _, d, a in edges("VALIDATES", src="method:App\\Http\\Requests\\SalesReportRequest::rules")}
    assert set(v) == {"request_key:category_id", "request_key:timezone"} and v["request_key:timezone"]["rule"][-1] == "max:64"
    assert edges("VALIDATED_BY", src="method:App\\Http\\Controllers\\ReportController::top")[0][1].endswith("SalesReportRequest::rules")
    (_, _, a), = edges("READS_INPUT", src=BUILD, dst="request_key:timezone")
    # $filters['timezone'] in build() <- report($store, $filters) <- top(): $request->validated()
    assert a["via"] == "$filters[...]" and "SalesReportService::report" in a["flow"][0] and "validated()" in a["flow"][-1]
    assert edges("READS_INPUT", src="method:App\\Http\\Controllers\\ReportController::summary", dst="request_key:category_id")[0][2]["default"] == 0


def test_resolution_nodes_and_fallback_edges():
    rb = sqlite3.connect(build()["api"]).execute("SELECT id, attrs FROM nodes WHERE kind='resolution'").fetchall()
    sig = {json.loads(a)["fn"]: json.loads(a)["signature"] for _, a in rb}
    assert sig[BUILD] == ["input:timezone", "setting:reports.timezone", "'UTC'"]
    assert sig[RESOLVE] == ["input:timezone", "column:orders.customer_timezone", "setting:locale.timezone",
                            "column:stores.default_timezone", "'UTC'"]
    order = [d for _, d, a in sorted(edges("FALLS_BACK_TO"), key=lambda e: e[2]["order"]) if "resolveTimezone" in _]
    assert order[:2] == ["request_key:timezone", "column:orders.customer_timezone"]


def test_concept_query_surfaces_divergence_backend_only():
    r = resolutions(GraphStore(build()["api"]), "timezone", client=False)
    fn_of = {s["id"]: s["fn"] for s in r["backend_sites"]}
    by_fn = {fn_of[sid]: c["label"] for c in r["chains"] for sid in c["sites"]}
    assert by_fn[BUILD] != by_fn[RESOLVE]
    div = [d for d in r["divergence"] if {d["a"], d["b"]} == {by_fn[BUILD], by_fn[RESOLVE]}][0]
    assert div["common_prefix"] == ["input:timezone"] and div["final_a"] == div["final_b"] == "'UTC'"
    assert {div["a_next"], div["b_next"]} == {"setting:reports.timezone", "column:orders.customer_timezone"}


@pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")
def test_concept_query_client_side_never_sends_and_falls_back():
    r = resolutions(GraphStore(build()["combined"]), "timezone")
    top = [e for e in r["client"] if e["endpoint"].endswith("admin/reports/top")][0]
    assert top["routes"] == ["route:GET /v1/{store}/admin/reports/top"]
    assert top["backend_sites"][0]["site"].startswith("resolution:App\\Services\\SalesReportService::build")
    req = top["requests"][0]
    assert req["keys"]["timezone"] == "conditional"
    assert [c["sends"]["timezone"] for c in req["callers"]] == [False]
    assert req["verdict"]["timezone"].startswith("never sent")
    fb = [f for f in req["client_fallbacks"] if f["literal"] == "UTC"][0]
    assert fb["at"].endswith("app/pages/reports/[id].vue:12")
    text = render_resolutions(r)
    assert "DIVERGENCE" in text and "never sent" in text


def test_concept_query_works_for_any_concept():
    r = resolutions(GraphStore(build()["api"]), "currency", client=False)  # the sample resolves no currency
    assert r["backend_sites"] == []

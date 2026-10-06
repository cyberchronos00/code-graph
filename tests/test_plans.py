"""Planned-change layer (cg_code_graph/plans.py): schema, deterministic checks, verify mode, CLI, MCP tools, overlay view.
Sample: examples/bookstore-api + examples/plans/preorders.yaml; the implemented state is the sample API overlaid
with tests/bookstore_impl."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import plans as P  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from sample import build, PLANS, needs_php  # noqa: E402

BROKEN = ROOT / "tests" / "broken_plans"


@pytest.fixture(scope="module")
def dbs():
    s = build()
    return GraphStore(s["api_plain"]), GraphStore(s["impl"])


def plan():
    return P.load_plan(str(PLANS / "preorders.yaml"))


def by(res, check=None, severity=None, covered=False):
    return {i["node"] for i in res["items"] if (check is None or i["check"] == check)
            and (severity is None or i["severity"] == severity) and i["covered"] == covered}


def dbpath(st):
    return st.db.execute("PRAGMA database_list").fetchone()[2]


# ---------------------------------------------------------------- schema
def test_schema_valid_and_broken():
    assert plan()["_schema_errors"] == []
    errs = P.load_plan(str(BROKEN / "broken.yaml"))["_schema_errors"]
    joined = "\n".join(errs)
    for frag in ("plan_version: must be 1", "name: required", "title: required", "status: one of", "bogus: unknown key",
                 "add_nodes[0].id", "modify[0].intent: required", "add_edges[0].kind", "forbid[0].edge_kind", "forbid[1].guard",
                 "forbid: ids must be unique"):
        assert frag in joined, (frag, joined)
    assert {p["name"] for p in P.list_plans(PLANS)} == {"preorders"}   # snapshot files are not plans
    assert {p["name"] for p in P.list_plans(BROKEN)} == {"Bad Name"}


def test_malformed_items_are_errors_not_crashes(dbs, tmp_path):
    """A string where a mapping belongs (covers: [page:x]) or invalid YAML is reported, never a crash."""
    before, _ = dbs
    (tmp_path / "typo.yaml").write_text(
        "plan_version: 1\nname: typo\ntitle: Guard a route\nmodify:\n  - target: StockService::reserve\n"
        "    intent: guard it\ncovers:\n  - page:app/pages/index.vue\nrequire: route:POST /v1/orders\n", encoding="utf-8")
    (tmp_path / "bad.yaml").write_text("plan_version: 1\nname: bad\ntitle: [unclosed\n", encoding="utf-8")
    pl = P.load_plan("typo", tmp_path)
    joined = "\n".join(pl["_schema_errors"])
    assert "covers[0]: must be a mapping like {spec: ...}" in joined and "require: must be a list" in joined
    assert "SCHEMA covers[0]" in P.render_check_summary(P.check(before, pl))
    assert "covers[0]" in P.render_load(pl) and "INVALID" in P.render_validate(P.validate(before, pl))
    with pytest.raises(P.PlanError, match="invalid YAML"):
        P.load_plan("bad", tmp_path)
    assert any(r.get("error", "").startswith("bad.yaml: invalid YAML") for r in P.list_plans(tmp_path))
    from cg_code_graph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE.update(db=dbpath(before), plans=str(tmp_path))
        assert M.plan_check("bad").startswith("plan error: bad.yaml: invalid YAML")
        assert M.plan_load("missing").startswith("plan error: plan not found: missing")
        assert "SCHEMA covers[0]" in M.plan_check("typo")
    finally:
        M.STATE.clear()
        M.STATE.update(old)


@needs_php
def test_validate_resolution(dbs):
    before, _ = dbs
    v = P.validate(before, plan())
    assert v["ok"], v["unresolved"]
    planned = {p["id"]: p for p in v["planned_nodes"]}
    assert planned["column:books.preorder_until"]["exists"] is False
    bad = dict(plan(), modify=[{"target": "NoSuch::thing", "intent": "x"}, {"target": "BookController::store", "intent": "x"}])
    st = {r["spec"]: r["status"] for r in P.validate(before, bad)["unresolved"]}
    assert st == {"NoSuch::thing": "unresolved"}  # Admin\BookController::store is the only ::store on a BookController
    amb = dict(plan(), modify=[{"target": "BookResource", "intent": "x"}])
    assert P.validate(before, amb)["unresolved"][0]["status"] == "ambiguous"  # Filament vs Http BookResource


# ---------------------------------------------------------------- plan-mode checks
@needs_php
def test_completeness_finds_omitted_siblings(dbs):
    before, _ = dbs
    r = P.check(before, plan())
    miss = by(r, severity="missing")
    assert "method:App\\Filament\\Resources\\BookResource::form" in miss            # admin form (no WRITES edge)
    assert "method:App\\Http\\Resources\\BookResource::toArray" in miss             # API serializer
    assert "property:App\\Models\\Book::$fillable" in miss                           # mass assignment
    assert "method:App\\Http\\Requests\\Admin\\UpdateBookRequest::rules" in by(r, "validation")
    assert "method:App\\Http\\Controllers\\Admin\\BookController::update" in by(r, "table_writer", "missing")
    assert {"method:App\\Http\\Controllers\\StockController::reserve", "method:App\\Http\\Controllers\\OrderController::store"} <= by(r, "caller")
    assert "route:POST /v1/orders" in by(r, "entry_point")
    assert by(r, "external_client") == {"client:example/bookstore-mobile/pages/cart.vue"}  # /me hits no affected route
    assert "table:warehouse_stock" in by(r, "parallel_table", covered=True)          # old mirror (covered by the forbid)
    assert "method:App\\Services\\StockService::reserveFromWarehouse" in by(r, "parallel_method")
    sync = next(i for i in r["items"] if i["node"].endswith("SyncWarehouseCommand::handle"))
    assert sync.get("bypasses") == ["StockService::reserve"]                         # writes books without the guard
    assert "method:App\\Http\\Controllers\\Admin\\BookController::store" not in miss  # covered items are not missing
    assert r["summary"]["missing_from_plan"] == len([i for i in r["items"] if i["severity"] == "missing" and not i["covered"]])
    assert not r["ok"]


@needs_php
def test_conflicts_requirements_findings(dbs):
    before, _ = dbs
    r = P.check(before, plan())
    f = r["conflicts"][0]
    assert f["present"] and f["guard"]["present"] is False
    assert [h["kind"] for h in f["hops"]][:1] == ["CALLS"] and f["hops"][0]["to"].endswith("::reserveFromWarehouse")
    assert r["require"][0]["missing"] == ["auth:api"]
    assert r["middleware_diff"]["route:POST /v1/stock/reserve"]["lacks"] == ["auth:api"]
    fs = {x["id"]: x for x in r["findings"]}
    assert fs["#7"]["linked"] and fs["#7"]["touches_plan"]
    assert not fs["#8"]["linked"] and fs["#8"]["touches_plan"] == ["method:App\\Services\\StockService::reserveLocal"]
    assert r["summary"]["unlinked_open_findings"] == 1
    assert r["precedents"][0]["hits"] and "increment(" in r["precedents"][0]["hits"][0]["text"]
    txt = P.render_check(r)
    for frag in ("MISSING FROM PLAN", "CONFLICTS", "STILL PRESENT", "NOT LINKED", "ENTRY CHAINS", "-ROUTES_TO@api.php:"):
        assert frag in txt, frag


def test_deterministic(dbs):
    before, _ = dbs
    a, b = P.check(before, plan()), P.check(before, plan())
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


# ---------------------------------------------------------------- verify mode
@needs_php
def test_verify_mode(dbs):
    before, after = dbs
    pl = plan()
    base = P.make_baseline(before, pl)
    assert all(t["sha1"] for t in base["targets"].values())
    pre = P.check(before, pl, verify=True, baseline=base)
    assert pre["summary"]["verify_ok"] is False
    assert all(not e["ok"] for e in pre["verify"]["edges"])
    assert {m["state"] for m in pre["verify"]["modified"]} == {"UNCHANGED"}
    post = P.check(after, pl, verify=True, baseline=base)
    v = post["verify"]
    assert post["summary"]["verify_ok"] is True, P.render_check(post)
    assert all(n["ok"] for n in v["nodes"]) and all(e["ok"] for e in v["edges"])
    assert {m["state"] for m in v["modified"]} == {"changed"}
    assert v["forbid"][0]["ok"] and v["forbid"][0]["guard"]["present"]  # path still exists but is now guarded
    assert v["require"][0]["ok"]
    # gaps the implementation still did not close remain visible
    assert "property:App\\Models\\Book::$fillable" in by(post, "model_fillable")
    assert "method:App\\Filament\\Resources\\BookResource::form" in by(post, "admin_surface")


# ---------------------------------------------------------------- CLI + MCP + view
@needs_php
def test_cli(dbs, capsys):
    from cg_code_graph.cli import main
    before, _ = dbs
    main(["plan", "list", "--plans-dir", str(PLANS)])
    main(["plan", "check", "preorders", "--plans-dir", str(PLANS), "--db", dbpath(before)])
    out = capsys.readouterr().out
    assert "preorders [agreed]" in out and "PLAN CHECK preorders" in out
    assert main(["plan", "validate", "broken", "--plans-dir", str(BROKEN), "--db", dbpath(before)]) == 1
    assert main(["plan", "validate", "preorders", "--plans-dir", str(PLANS), "--db", dbpath(before)]) == 0


@needs_php
def test_mcp_plan_tools(dbs):
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    before, _ = dbs

    async def go():
        params = StdioServerParameters(command=sys.executable, cwd=str(ROOT),
                                       args=["-m", "cg_code_graph.mcp_server", "--db", dbpath(before), "--plans", str(PLANS)])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                out = {}
                for name, args in (("plan_list", {}), ("plan_load", {"name": "preorders"}), ("plan_validate", {"name": "preorders"}),
                                   ("plan_check", {"name": "preorders", "max_items": 20}),
                                   ("plan_check", {"name": "preorders", "verify": True, "review": False})):
                    res = await s.call_tool(name, args)
                    key = name + ("_verify" if args.get("verify") else "")
                    out[key] = ("\n".join(getattr(c, "text", "") for c in res.content), res.is_error)
                return names, out
    names, out = asyncio.run(go())
    assert {"plan_list", "plan_load", "plan_validate", "plan_check", "plan_baseline"} <= names
    for k, (txt, err) in out.items():
        assert not err, (k, txt[:300])
    assert "preorders [agreed]" in out["plan_list"][0]
    assert "+ node column:books.preorder_until" in out["plan_load"][0]
    assert "resolve" in out["plan_validate"][0]
    assert "MISSING FROM PLAN" in out["plan_check"][0] and "BookResource::form" in out["plan_check"][0]
    assert "4 VERIFY" in out["plan_check_verify"][0] and "REVIEW (" in out["plan_check_verify"][0]


@needs_php
def test_overlay_view(dbs, tmp_path):
    from cg_code_graph.viz import graph as G
    from cg_code_graph.viz.server import export_plan_html
    before, _ = dbs
    g = G.build_plan(before, str(PLANS / "preorders.yaml"))
    roles = {n["id"]: n.get("plan_role") for n in g["nodes"]}
    assert roles["column:books.preorder_until"] == "added"
    assert roles["method:App\\Services\\StockService::reserve"] == "modified"
    assert roles["method:App\\Filament\\Resources\\BookResource::form"] == "missing"
    assert roles["table:warehouse_stock"] == "forbidden"
    kinds = {e.get("plan") for e in g["edges"]}
    assert {"added", "forbidden", "gap", None} <= kinds
    assert all(e["at"] for e in g["edges"] if not e.get("plan"))  # every real edge carries file:line evidence
    html = Path(export_plan_html(dbpath(before), str(tmp_path / "plan.html"), str(PLANS / "preorders.yaml"))).read_text()
    assert "window.__STATIC__" in html and '"mode": "plan"' in html and "MISSING FROM PLAN" in html

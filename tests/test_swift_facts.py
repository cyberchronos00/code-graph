"""Swift Moya `TargetType` endpoints (enum -> http nodes, `provider.request(.case)` call sites, linked to Vapor
routes), Fluent tables (`static let schema`, migrations, `Todo.query(on:)` / `.find` / `todo.save(on:)`), and the
extra platform conditions (`@available(macOS, unavailable)`, calls to per-`#if os` function variants)."""
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from codegraph.indexer import index_project  # noqa: E402
from codegraph.link import link  # noqa: E402

FIX = ROOT / "tests" / "swift_facts_fixture"
_S: dict = {}


def db():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-swift-facts-"))
        _S["stats"] = index_project(FIX, d / "f.db", "swift-facts")
        _S["db"] = d / "f.db"
        _S["link"] = link(str(d / "f.db"), str(d / "f.db"), str(d / "l.db"),
                          backend_name="swift-facts", frontend_name="swift-facts")
        _S["l"] = d / "l.db"
    return sqlite3.connect(_S["db"])


def edges(kind):
    return {(s, d) for s, d in db().execute("select src, dst from edges where kind = ?", (kind,))}


def test_moya_target_endpoints():
    http = {r[0] for r in db().execute("select id from nodes where kind = 'http'")}
    assert {"http:GET https://api.github.com/zen", "http:GET https://api.github.com/users/{owner}/repos",
            "http:POST https://api.github.com/repos/{repo}/issues",          # per-case method, default .get
            "http:GET https://status.example.com/api/health",                # single-value path / method
            "http:GET /todos", "http:POST /todos", "http:DELETE /todos/{id}"} <= http   # implicit returns
    hc = edges("HTTP_CALLS")
    assert ("class:GitHub", "http:GET https://api.github.com/zen") in hc      # every case is an endpoint of its enum
    assert ("class:TodoAPI", "http:POST /todos") in hc
    st = _S["stats"]["plugins"]["swift"]
    assert st["moya_targets"] == 3


def test_moya_call_sites():
    hc = edges("HTTP_CALLS")
    assert ("method:GitHubClient.loadZen", "http:GET https://api.github.com/zen") in hc       # let provider = ...
    assert ("method:GitHubClient.open", "http:POST https://api.github.com/repos/{repo}/issues") in hc
    assert ("method:GitHubClient.check", "http:GET https://status.example.com/api/health") in hc  # typed property
    assert ("method:TodoClient.all", "http:GET /todos") in hc                  # requestPublisher, stored property
    assert ("method:TodoClient.remove", "http:DELETE /todos/{id}") in hc
    attrs = db().execute("select attrs from edges where kind='HTTP_CALLS' and src='method:GitHubClient.loadZen'").fetchone()[0]
    assert '"target": "GitHub.zen"' in attrs and '"client": "moya"' in attrs


def test_moya_links_to_vapor_routes():
    db()
    s = _S["link"]["stats"]
    assert s["endpoints_matched"] == 3
    l = sqlite3.connect(_S["l"])
    m = {(a, b) for a, b in l.execute("select src, dst from edges where kind = 'MATCHES_ROUTE'")}
    assert ("http:DELETE /todos/{id}", "route:DELETE /todos/{todoID}") in m
    assert ("http:GET /todos", "route:GET /todos") in m
    reasons = _S["link"]["stats"]["unmatched_reasons"]
    assert set(reasons) == {"not a backend URL"}                              # third-party APIs stay unlinked


def test_fluent_models_and_migrations():
    assert {("class:Todo", "table:todos"), ("class:Tag", "table:tags")} <= edges("MAPS_TO_TABLE")  # let / computed
    w = edges("WRITES_TABLE")
    assert ("method:CreateTodo.prepare", "table:todos") in w and ("method:CreateTodo.revert", "table:todos") in w
    via = dict(db().execute("select src, json_extract(attrs, '$.via') from edges where kind='WRITES_TABLE' "
                            "and src like 'method:CreateTodo.%'").fetchall())
    assert via == {"method:CreateTodo.prepare": "migration create", "method:CreateTodo.revert": "migration delete"}


def test_fluent_queries():
    r, w = edges("READS_TABLE"), edges("WRITES_TABLE")
    assert ("method:TodoController.index", "table:todos") in r                # Todo.query(on:).all()
    assert ("method:TodoController.delete", "table:todos") in r               # Todo.find(...)
    assert ("method:TodoController.create", "table:todos") in w               # let todo = Todo(...); todo.save(on:)
    assert ("method:TodoController.delete", "table:todos") in w               # guard let todo = ...find; .delete(on:)
    assert ("method:TodoController.delete", "table:tags") in w                # Tag.query(on:)...delete()
    assert ("method:TodoController.index", "table:tags") not in r | w
    conf = {c for (c,) in db().execute("select confidence from edges where kind in ('READS_TABLE','WRITES_TABLE') "
                                       "and src like 'method:TodoController.%'")}
    assert conf == {"resolved"}
    st = _S["stats"]["plugins"]["swift"]
    assert st["fluent_models"] == 2 and st["fluent_queries"] == 3 and st["fluent_writes"] == 2


def node_attrs(nid):
    import json
    r = db().execute("select attrs from nodes where id = ?", (nid,)).fetchone()
    return json.loads(r[0] or "{}") if r else None


def test_available_unavailable_is_a_platform_condition():
    a = node_attrs("class:Haptics")
    assert "macos" not in a["platforms"] and "ios" in a["platforms"]
    assert a["platform_expr"] == "@available(macOS, unavailable)"
    assert "macos" not in node_attrs("method:Haptics.play")["platforms"]           # members inherit it
    assert "platforms" not in node_attrs("function:newAPI")                         # version-only: every target


def test_call_reaches_every_platform_variant():
    rows = db().execute("select dst, json_extract(attrs, '$.platform_variant_of') from edges "
                        "where src = 'function:describe' and kind = 'CALLS'").fetchall()
    assert ("function:deviceKind", None) in rows
    variant = [d for d, v in rows if v == "function:deviceKind"]
    assert len(variant) == 1 and variant[0].startswith("function:deviceKind@")
    assert node_attrs(variant[0])["platforms"] != node_attrs("function:deviceKind")["platforms"]

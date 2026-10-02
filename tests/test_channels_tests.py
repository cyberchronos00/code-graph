"""Broadcast channels (Laravel Broadcast::channel / ShouldBroadcast / Echo + pusher-js), test indexing (PHPUnit, Pest,
Vitest, Playwright) and secret-checked routes, on tests/broadcast_fixture/{api,web} (a small task-board app)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph import query as Q, realtime as RT, routes as R  # noqa: E402
from codegraph.plugins.php.strings import channel_match, same_shape  # noqa: E402
from codegraph.tests_index import page_pattern  # noqa: E402
from sample import EXTRACTOR_DEPS, needs_php  # noqa: E402

FIX = ROOT / "tests" / "broadcast_fixture"
_S: dict = {}
needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")


def built() -> dict:
    if not _S:
        from codegraph.indexer import index_project
        from codegraph.link import link
        d = Path(tempfile.mkdtemp(prefix="codegraph-bc-"))
        _S["api_stats"] = index_project(FIX / "api", d / "api.db", "taskboard-api")
        _S["api"] = d / "api.db"
        if EXTRACTOR_DEPS.exists():
            index_project(FIX / "web", d / "web.db", "taskboard-web")
            _S["link"] = link(str(d / "api.db"), str(d / "web.db"), str(d / "combined.db"),
                              backend_name="taskboard-api", frontend_name="taskboard-web")
            _S.update(web=d / "web.db", combined=d / "combined.db")
    return _S


def db(name="api"):
    return sqlite3.connect(built()[name])


def edges(src=None, kind=None, dst=None, name="api"):
    q, args = "SELECT src, dst, kind, confidence, attrs FROM edges WHERE 1", []
    for col, v in (("src", src), ("kind", kind), ("dst", dst)):
        if v is not None:
            q += f" AND {col}=?"
            args.append(v)
    return [(r[0], r[1], r[2], r[3], json.loads(r[4] or "{}")) for r in db(name).execute(q, args)]


# ---------------------------------------------------------------- string evaluation / pattern matching

def test_channel_patterns():
    assert channel_match("board.42", "board.{board}")
    assert channel_match("board.{board_id}", "board.{board}")
    assert channel_match("board.{boardId}.activity", "board.{boardId}.{topic}")
    assert not channel_match("board.{boardId}.archive.events", "board.{boardId}.{topic}")
    assert not channel_match("team.1", "team.{teamId}.lobby")
    assert same_shape("orders.{id}", "orders.{orderId}") and not same_shape("orders.1", "orders.{id}")
    assert page_pattern("/shelves/:id/books/:bookId?") == "/shelves/{id}/books/{bookId?}"


# ---------------------------------------------------------------- channels (backend)

@needs_php
def test_channel_nodes_and_auth():
    rows = dict(db().execute("SELECT id, entry_kind FROM nodes WHERE kind='channel'").fetchall())
    assert rows == {"channel:App.Models.User.{id}": "channel_auth", "channel:team.{teamId}": "channel_auth",
                    "channel:team.{teamId}.lobby": "channel_auth", "channel:board.{board}": "channel_auth",
                    "channel:board.{boardId}.{topic}": "channel_auth", "channel:status": None}
    auth = edges(kind="AUTHORIZES_CHANNEL")
    assert {e[0] for e in auth} == {"route:POST /api/broadcasting/auth"} and len(auth) == 5
    # closure body facts belong to the channel node; a channel class is HANDLED_BY its join()
    assert edges("channel:board.{boardId}.{topic}", "CALLS", "method:App\\Models\\User::isBoardMember")
    assert edges("channel:board.{board}", "HANDLED_BY", "method:App\\Broadcasting\\BoardChannel::join")
    assert not edges("script:routes/channels.php", "CALLS", "method:App\\Models\\User::isBoardMember")


@needs_php
def test_broadcast_events_resolve_channel_names():
    on = {(e[0], e[1], e[4]["name"]) for e in edges(kind="BROADCASTS_ON")}
    assert ("event:App\\Events\\TaskMoved", "channel:board.{board}", "board.{board_id}") in on
    assert ("event:App\\Events\\StatusPage", "channel:status", "status") in on
    # helper method + constructor-param channel: one edge per dispatch site, evaluated at the site
    act = {x[2] for x in on if x[0] == "event:App\\Events\\BoardActivity"}
    assert act == {"board.{board_id}.activity", "board.{board_id}.comments"}
    # TaskMoved::broadcastOn also targets team.{teamId} through a static helper
    assert any(x[0] == "event:App\\Events\\TaskMoved" and x[1] == "channel:team.{teamId}" for x in on)


@needs_php
def test_channels_query_backend_only():
    st = GraphStore(built()["api"])
    res = RT.channels(st, "board.42")
    assert [c["id"] for c in res["channels"]] == ["channel:board.{board}"]
    c = res["channels"][0]
    assert c["auth"]["routes"][0]["middleware"] == ["api", "auth:sanctum"]
    assert any(x["target"] == "method:App\\Support\\BoardAccess::visibleBoardIds" for x in c["auth"]["checks"])
    assert c["publishers"][0]["dispatched_by"][0]["fn"] == "method:App\\Http\\Controllers\\TaskController::move"
    out = RT.render_channels(res)
    assert "WHO CAN JOIN" in out and "PUBLISHED BY" in out and "BoardAccess::visibleBoardIds" in out


@needs_php
def test_impact_of_broadcast_event_and_channel_entry():
    st = GraphStore(built()["api"])
    ent = {e["id"] for e in Q.impact(st, "App\\Events\\TaskMoved")["entry_points"]}
    assert ent == {"route:PATCH /tasks/{task}/move"}
    res = Q.impact(st, "App\\Support\\BoardAccess::visibleBoardIds")
    ids = {e["id"] for e in res["entry_points"]}
    assert "channel:board.{board}" in ids
    assert not any(i.startswith("test:") for i in ids) and not any("tests/" in (c["file"] or "") for c in res["callers"])


# ---------------------------------------------------------------- small fixes

@needs_php
def test_writers_accepts_table_prefix():
    st = GraphStore(built()["api"])
    assert Q.writers(st, "table:tasks") == Q.writers(st, "tasks") != []


@needs_php
def test_secret_checked_routes():
    st = GraphStore(built()["api"])
    items = {x["route"]: x for x in R.routes_report(st)["items"]}
    assert items["route:POST /webhooks/payments"]["secret_checked"] and not items["route:POST /webhooks/payments"]["has_auth"]
    assert items["route:GET /exports/{file}"]["secret_checked"]
    assert not items["route:PATCH /tasks/{task}/move"]["secret_checked"]
    assert R.routes_report(st, unguarded=True)["items"] == []
    out = R.render_routes(R.routes_report(st))
    assert "SECRET-CHECKED" in out and "NO AUTH" not in out


# ---------------------------------------------------------------- tests (PHP)

@needs_php
def test_php_test_nodes():
    tests = dict(db().execute("SELECT id, json_extract(attrs,'$.framework') FROM nodes WHERE kind='test'").fetchall())
    assert tests == {
        "test:Tests\\Feature\\TaskMoveTest::test_moving_a_task_updates_its_state": "phpunit",
        "test:Tests\\Feature\\TaskMoveTest::board_tasks_are_listed": "phpunit",
        "test:Tests\\Feature\\TaskMoveTest::webhook_rejects_a_bad_signature": "phpunit",
        "test:Tests\\Feature\\TaskCommentTest::test_a_member_can_comment_on_a_task": "phpunit",
        "test:tests/Unit/BoardAccessTest.php::board access > it lists the boards of the user teams": "pest",
        "test:tests/Unit/BoardAccessTest.php::board access > admins see every board": "pest",
        "test:tests/Unit/BoardAccessTest.php::team membership check": "pest",
    }
    # test code never feeds the app graph: no plain CALLS out of tests/, only TEST_* edges
    rows = db().execute("SELECT e.kind FROM edges e JOIN nodes n ON n.id=e.src WHERE n.file LIKE 'tests/%' "
                        "AND e.kind IN ('CALLS','INSTANTIATES','DISPATCHES','WRITES_TABLE','READS_TABLE')").fetchall()
    assert rows == []


@needs_php
def test_php_tests_covering():
    st = GraphStore(built()["api"])
    res = Q.tests_covering(st, "PATCH /api/tasks/{task}/move")
    assert [t["test"] for t in res["direct"]] == ["test:Tests\\Feature\\TaskMoveTest::test_moving_a_task_updates_its_state"]
    res = Q.tests_covering(st, "route:GET /boards/{board}/tasks")     # route('tasks.index') resolves by name
    assert [t["name"] for t in res["direct"]] == ["TaskMoveTest::board_tasks_are_listed"]
    res = Q.tests_covering(st, "App\\Support\\BoardAccess::visibleBoardIds")
    assert {t["name"] for t in res["direct"]} == {"board access > it lists the boards of the user teams",
                                                  "board access > admins see every board"}
    # through the route: the move test reaches TaskMoved's constructor via the controller
    res = Q.tests_covering(st, "App\\Events\\TaskMoved")
    assert [t["name"] for t in res["transitive"]] == ["TaskMoveTest::test_moving_a_task_updates_its_state"]
    # through project request helpers: postAs($uri) -> sendAs('post', $uri) -> $this->json($method, $uri)
    res = Q.tests_covering(st, "POST /api/tasks/{task}/comments")
    assert [t["test"] for t in res["direct"]] == ["test:Tests\\Feature\\TaskCommentTest::test_a_member_can_comment_on_a_task"]
    # closest tests first
    for res in (Q.tests_covering(st, "App\\Models\\Task"), Q.tests_covering(st, "App\\Support\\BoardAccess")):
        for k in ("direct", "transitive"):
            assert [t["depth"] for t in res[k]] == sorted(t["depth"] for t in res[k])


# ---------------------------------------------------------------- frontend + link

@needs_ts
def test_ts_subscriptions_and_tests():
    subs = dict(db("web").execute("SELECT id, json_extract(attrs,'$.visibility') FROM nodes WHERE kind='channel_sub'").fetchall())
    assert subs == {"channel_sub:status": "public", "channel_sub:board.{boardId}": "private",
                    "channel_sub:board.{boardId}.activity": "private", "channel_sub:team.{teamId}.lobby": "presence",
                    "channel_sub:board.{boardId}.archive.events": "private"}
    tests = dict(db("web").execute("SELECT name, json_extract(attrs,'$.framework') FROM nodes WHERE kind='test'").fetchall())
    assert tests == {"taskLabel > adds the state": "vitest", "board page > shows the tasks of a board": "playwright",
                     "board page > moving a task through the API": "playwright"}
    assert edges("test:e2e/board.spec.ts#board page > shows the tasks of a board", "TEST_VISITS", "page:app/pages/boards/[id].vue", name="web")
    assert edges("test:app/utils/format.test.ts#taskLabel > adds the state", "TEST_CALLS", "function:app/utils/format.ts#taskLabel", name="web")
    assert edges(kind="HTTP_CALLS", dst="http:PATCH /api/tasks/1/move", name="web") == []


@needs_ts
@needs_php
def test_link_channels_and_events():
    st = built()["link"]["stats"] if "stats" in built()["link"] else built()["link"]
    assert st["channel_subscriptions"] == 5 and st["channel_subscriptions_matched"] == 4
    assert st["listened_events"] == 5 and st["listened_events_matched"] == 4
    assert st["endpoints"] == 1 and st["test_endpoints_matched"] == 1     # the Playwright request is not a client call
    m = {(e[0], e[1]): e[3] for e in edges(kind="MATCHES_CHANNEL", name="combined")}
    assert m == {("channel_sub:status", "channel:status"): "exact",
                 ("channel_sub:board.{boardId}", "channel:board.{board}"): "exact",
                 ("channel_sub:board.{boardId}.activity", "channel:board.{boardId}.{topic}"): "resolved",
                 ("channel_sub:team.{teamId}.lobby", "channel:team.{teamId}.lobby"): "exact"}
    lf = {(e[0], e[1]) for e in edges(kind="LISTENS_FOR", name="combined")}
    assert lf == {("channel_sub:status", "event:App\\Events\\StatusPage"),
                  ("channel_sub:board.{boardId}", "event:App\\Events\\TaskMoved"),
                  ("channel_sub:board.{boardId}.activity", "event:App\\Events\\BoardActivity"),
                  ("channel_sub:team.{teamId}.lobby", "event:App\\Events\\TeamOnline")}


@needs_ts
@needs_php
def test_channels_and_tests_on_combined_graph():
    st = GraphStore(built()["combined"])
    res = RT.channels(st, "board.*")
    by = {c["id"]: c for c in res["channels"]}
    assert by["channel:board.{board}"]["subscribers"][0]["name"] == "board.{boardId}"
    assert [o["name"] for o in res["client_subscriptions_unmatched"]] == ["board.{boardId}.archive.events"]
    out = RT.render_channels(res)
    assert "LISTENED TO BY" in out and "page:app/pages/boards/[id].vue" in out
    cov = Q.tests_covering(st, "PATCH /api/tasks/{task}/move")
    assert {t["framework"] for t in cov["direct"]} == {"phpunit", "playwright"}
    cov = Q.tests_covering(st, "taskLabel")
    assert [t["framework"] for t in cov["direct"]] == ["vitest"]
    assert [t["framework"] for t in cov["transitive"]] == ["playwright"]     # page.goto -> page -> taskLabel


@needs_ts
@needs_php
def test_mcp_tools(monkeypatch):
    from codegraph import mcp_server as M
    monkeypatch.setattr(M, "_st", lambda: GraphStore(built()["combined"]))
    out = M.channels("board.{board}")
    assert "WHO CAN JOIN" in out and "useBoardRealtime" in out
    out = M.tests_covering("App\\Support\\BoardAccess::visibleBoardIds")
    assert "admins see every board" in out


@needs_php
def test_public_publish_on_declared_channel_is_flagged():
    st = GraphStore(built()["api"])
    c = RT.channels(st, "team.{teamId}")["channels"][0]
    assert c["id"] == "channel:team.{teamId}"
    assert any(f.startswith("PUBLIC PUBLISH") and "TeamOnline" in f for f in c["flags"])
    lobby = RT.channels(st, "team.{teamId}.lobby")["channels"][0]
    assert not any(f.startswith("PUBLIC PUBLISH") for f in lobby["flags"])


@needs_ts
@needs_php
def test_listeners_on_an_assigned_channel_property():
    ev = json.loads(db("web").execute("SELECT attrs FROM nodes WHERE id='channel_sub:team.{teamId}.lobby'").fetchone()[0])["events"]
    assert ev == ["TeamOnline"]
    assert ("channel_sub:team.{teamId}.lobby", "event:App\\Events\\TeamOnline") in {(e[0], e[1]) for e in edges(kind="LISTENS_FOR", name="combined")}


def test_channel_matcher_flags_visibility_mismatch():
    from codegraph.link import channel_links
    chans = [("channel:orders.{order}", {"pattern": "orders.{order}", "visibility": "private"}, "routes/channels.php", 3)]
    subs = [("channel_sub:orders.{id}", {"name": "orders.{id}", "visibility": "public", "events": [".shipped"]}),
            ("channel_sub:orders.{orderId}", {"name": "orders.{orderId}", "visibility": "private", "events": ["OrderShipped"]})]
    events = {"event:App\\Events\\OrderShipped": {"broadcast_as": "shipped"}}
    rows, st = channel_links(chans, subs, events, {"channel:orders.{order}": {"event:App\\Events\\OrderShipped"}})
    m = {r[0]: r[4] for r in rows if r[2] == "MATCHES_CHANNEL"}
    assert m["channel_sub:orders.{id}"]["visibility_mismatch"] == "client subscribes as public, backend channel is private"
    assert "visibility_mismatch" not in m["channel_sub:orders.{orderId}"]
    assert {(r[0], r[1]) for r in rows if r[2] == "LISTENS_FOR"} == {
        ("channel_sub:orders.{id}", "event:App\\Events\\OrderShipped"), ("channel_sub:orders.{orderId}", "event:App\\Events\\OrderShipped")}
    assert st["channel_subscriptions_matched"] == 2

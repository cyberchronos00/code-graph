"""Kotlin heuristic binding by name (#83 item 6), on tests/kotlin_binding_fixture: a call whose receiver type is
unknown binds a method name declared once as `binding: name`, and one declared on several classes as a `candidate`
edge to each (also from test code, so `cg tests` finds the test, flagged); a typed receiver binds as before, and
a library receiver type (`Headers.build { }`, `client: HttpClient`) binds to no project method."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_kotlin")
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "kotlin_binding_fixture"
_S: dict = {}


def calls(dst):
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-kotlin-binding-"))
        _S["stats"] = index_project(FIX, d / "g.db", "kotlin-binding")
        _S["db"] = d / "g.db"
    q = "select src, line, attrs from edges where kind in ('CALLS', 'TEST_CALLS') and dst = ? order by src, line"
    return [(s, ln, json.loads(a or "{}").get("binding")) for s, ln, a in sqlite3.connect(_S["db"]).execute(q, (dst,))]


def test_ambiguous_name_on_unknown_receiver_gives_candidates():
    assert calls("method:app.InboxStore.reload") == [("method:app.Refresher.refreshAll", 18, "candidate"),
                                                     ("method:app.StoreTest.reloadsStores", 9, "candidate")]
    assert calls("method:app.FeedStore.reload") == [("method:app.Refresher.refreshAll", 18, "candidate"),
                                                    ("method:app.Refresher.refreshFeed", 23, None),
                                                    ("method:app.StoreTest.reloadsStores", 9, "candidate")]
    assert calls("method:app.Archive.restore") == [("function:app.archiveFrom", 33, None),
                                                   ("method:app.Refresher.refreshAll", 19, "name")]
    assert _S["stats"]["plugins"]["kotlin"]["call_candidate_edges"] == 4


def test_library_receiver_does_not_bind_by_name():
    # `LibraryCache.restore(id)`, `client: HttpClient` then `client.reload(true)`: a known non-project receiver type
    calls("method:app.Archive.restore")
    assert _S["stats"]["plugins"]["kotlin"]["calls_library_receiver"] == 2


def test_cg_tests_lists_candidate_tests():
    from cg_code_graph import query as Q
    from cg_code_graph.core.store import GraphStore
    calls("method:app.FeedStore.reload")
    res = Q.tests_covering(GraphStore(str(_S["db"])), "FeedStore.reload")
    assert [(t["name"], t.get("candidate")) for t in res["direct"]] == [("reloadsStores", True)]

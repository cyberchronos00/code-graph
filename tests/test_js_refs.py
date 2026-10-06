"""#138: in JavaScript, a callback passed to a factory (`var server = http.createServer(cb)`) is not the variable's
value, and property reads, element reads, `return f` and assignments of a function variable are not calls. Wrappers
(`var d = debounce(fn)`) still hold the function. tests/jsref_fixture is a small CommonJS package with a test."""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "jsref_fixture"
M = "function:src/index.js#"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("jsref") / "g.db"
    index_project(FX, d, "jsref")
    return d


def _q(db, sql, *a):
    return list(sqlite3.connect(db).execute(sql, a))


def test_callback_of_a_factory_is_not_the_variable(db):
    ids = {r[0] for r in _q(db, "select id from nodes where kind='function'")}
    assert "function:test/server.spec.js#server" not in ids                  # var server = http.createServer(cb)
    assert {M + "startServer", M + "handle"} <= ids
    # the callbacks' calls of handle() belong to their enclosing scope
    assert _q(db, "select 1 from edges where kind='CALLS' and src=? and dst=?", M + "startServer", M + "handle")
    assert _q(db, "select 1 from edges where kind='TEST_CALLS' and src=? and dst=? and line=6", "module:test/server.spec.js",
              M + "handle")


def test_wrapper_keeps_the_function(db):
    ids = {r[0] for r in _q(db, "select id from nodes where kind='function'")}
    assert M + "debounced" in ids                                            # var debounced = debounce(fn)
    assert _q(db, "select 1 from edges where kind='CALLS' and src=? and dst=?", M + "debounced", M + "handle")


def test_reads_returns_and_assignments_are_not_calls(db):
    def into(dst):
        return {r[0] for r in _q(db, "select line from edges where dst=? and kind in ('CALLS','TEST_CALLS')", dst)}
    # `request.get = ..` / `request.defaults = ..` and `return request`: not calls; verbFunc's `request(url, cb)` is
    assert into(M + "request") == {9}
    assert not into(M + "createValidator.l")                                 # return l
    # the real calls from the test stay; `s.url` / `server.listen(0)` are not calls
    t = {(r[0], r[1]) for r in _q(db, "select dst, line from edges where kind='TEST_CALLS' and file like 'test/%'")}
    assert {(M + "startServer", 12), (M + "createValidator", 16), (M + "debounced", 18)} <= t
    assert not any(ln in (8, 13, 14) for _d, ln in t)

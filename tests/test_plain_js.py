"""Plain JavaScript projects without tsconfig / jsconfig (#136): tests/plainjs_fixture holds a CommonJS app
(package.json + src/ + a mocha test + a webpack config), an ESM library (`"type": "module"`, `exports`) and a Python
project whose package.json only carries JS tooling (eslint config, a static widget)."""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.detect import detect  # noqa: E402
from cg_code_graph.core.plugin import Project  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.plugins.ts.plugin import TypeScriptPlugin, plain_js_dirs, plain_js_program  # noqa: E402

FX = ROOT / "tests" / "plainjs_fixture"


def _project(name):
    r = FX / name
    return Project(root=r, name=name, detected=detect(r))


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("plainjs")
    out = {}
    for r in ("cjs-app", "esm-lib", "py-tooling"):
        out[r + "-stats"] = index_project(FX / r, d / f"{r}.db", r)
        out[r] = d / f"{r}.db"
    return out


def _nodes(db):
    con = sqlite3.connect(db)
    return {r[0]: r[1] for r in con.execute("select id, kind from nodes")}


def _edges(db, kind):
    con = sqlite3.connect(db)
    return {(r[0], r[1]) for r in con.execute("select src, dst from edges where kind=?", (kind,))}


def test_detection():
    ts = TypeScriptPlugin()
    assert plain_js_dirs(FX / "cjs-app") == ["src"]                     # webpack.config.js and test/ do not count
    assert ts.detect(_project("cjs-app")) and not ts.detect_configured(_project("cjs-app"))
    assert plain_js_program(_project("esm-lib")) == ["lib"]
    assert not ts.detect(_project("py-tooling"))                         # Python root, no Node package entry


def test_cjs_app_indexed(dbs):
    n = _nodes(dbs["cjs-app"])
    assert n.get("function:src/index.js#publishOrder") == "function"
    assert n.get("function:src/format.js#formatOrder") == "function"
    assert not any("webpack.config" in k for k in n)
    calls = _edges(dbs["cjs-app"], "CALLS")
    assert ("function:src/index.js#publishOrder", "function:src/format.js#formatOrder") in calls   # through require()
    tests = [k for k, v in n.items() if v == "test"]
    assert tests, "mocha test cases in test/ are indexed"
    st = dbs["cjs-app-stats"]["plugins"]["typescript"]["program"]
    assert st["synthesized"] and st["src_dirs"] == ["src"]
    q = "endpoint:amqp:queue:orders"                                   # #35 broker endpoints now reach plain JS
    assert q in n and ("function:src/index.js#publishOrder", q) in _edges(dbs["cjs-app"], "SENDS_TO")


def test_esm_lib_indexed(dbs):
    calls = _edges(dbs["esm-lib"], "CALLS")
    assert ("function:lib/index.mjs#title", "function:lib/slug.mjs#slug") in calls


def test_python_with_js_tooling_unchanged(dbs):
    st = dbs["py-tooling-stats"]
    assert "typescript" not in st["plugins"]
    assert not any(k.startswith(("function:app/static", "module:app/static")) for k in _nodes(dbs["py-tooling"]))

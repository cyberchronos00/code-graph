"""Indexing several projects in one process (the MCP `index` tool called twice, a test session) gives the same graphs
as indexing each project in a fresh process: no plugin state (caches, gate predicates, file lists) carries over from
one project to the next. Projects cover PHP with gates, TS / Express / Koa, Rust, Kotlin, Swift and Python / Django."""
import os
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from sample import EXTRACTOR_DEPS, PHP_EXTRACTOR_DEPS  # noqa: E402

pytestmark = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")

# (root, gates file); the gated PHP project goes first so its gate predicates would show up in every later graph
PROJECTS = [("tests/gating_fixture", "examples/bookstore.gates.json"), ("examples/bookstore-express", None),
            ("tests/ts_fixtures/koa-api", None), ("examples/rust-kvstore", None), ("tests/rust_routes_fixture", None),
            ("examples/bookstore-android", None), ("tests/kotlin_fixture", None), ("tests/swift_fixture", None),
            ("examples/bookstore-ios", None), ("tests/django_access_fixture", None)]
if not PHP_EXTRACTOR_DEPS.exists():   # the other languages are still checked without the PHP extractor
    PROJECTS = PROJECTS[1:]
SCRIPT = """
import sys
from cg_code_graph.indexer import index_project
out, args = sys.argv[1], sys.argv[2:]
for root, gates in zip(args[::2], args[1::2]):
    index_project(root, f"{out}/{root.replace('/', '_')}.db", root.replace('/', '_'), gates=gates or None)
"""


def run(out: Path, projects) -> None:
    args = [x for root, gates in projects for x in (root, gates or "")]   # relative to ROOT (the cwd)
    env = {**os.environ, "CG_NO_CACHE": "1", "PYTHONPATH": str(ROOT)}
    subprocess.run([sys.executable, "-c", SCRIPT, str(out), *args], check=True, env=env, cwd=ROOT,
                   capture_output=True, timeout=600)


def tables(db: Path) -> dict:
    c = sqlite3.connect(db)
    out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'meta'"):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})") if r[1] != "id"]
        out[t] = sorted(map(repr, c.execute(f"SELECT {', '.join(cols)} FROM {t}")))
    return out


def test_projects_indexed_in_one_process_match_separate_runs():
    d = Path(tempfile.mkdtemp(prefix="codegraph-isolation-"))
    (d / "one").mkdir()
    run(d / "one", PROJECTS)
    with ThreadPoolExecutor(4) as ex:
        for i, p in enumerate(PROJECTS):
            (d / f"sep{i}").mkdir()
        list(ex.map(lambda ip: run(d / f"sep{ip[0]}", [ip[1]]), enumerate(PROJECTS)))
    for i, (root, _) in enumerate(PROJECTS):
        name = root.replace("/", "_") + ".db"
        one, sep = tables(d / "one" / name), tables(d / f"sep{i}" / name)
        assert one["nodes"], root
        for t in sep:
            assert one[t] == sep[t], f"{root}: table {t} differs after other projects were indexed in the same process"

"""`cg lint async-state` (#88 phase 3), rule stale-async-result, on tests/async_fixture/{swift,kotlin,react,python}:
each has a write of the awaited result into state with no guard (flagged) and guarded variants (a generation /
request-id comparison, `Task.isCancelled`, a `cancelled` flag) that are not flagged."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import lint_async as LA  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "async_fixture"
_S: dict = {}


def db(lang):
    if lang not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-lint-async-"))
        index_project(FIX / lang, d / "g.db", f"as-{lang}")
        _S[lang] = d / "g.db"
    return _S[lang]


def found(lang):
    """stale-async-result findings in the Search files (Detail files carry the two-writers cases)."""
    return [(f["write_at"], f["async"], f["confidence"]) for f in LA.lint(GraphStore(db(lang)))["findings"]
            if f["rule"] == "stale-async-result" and "Detail" not in f["write_at"] and "detail" not in f["write_at"]]


def two(lang):
    return [(f["state"], [w["at"] for w in f["lifecycle_writes"]], [w["at"] for w in f["async_writes"]], f["confidence"])
            for f in LA.lint(GraphStore(db(lang)))["findings"] if f["rule"] == "two-writers"]


def test_swift():
    pytest.importorskip("tree_sitter_swift")
    assert found("swift") == [("Sources/App/Search.swift:11", "Task", "heuristic")]     # not :21 (generation), :29 (isCancelled)


def test_kotlin():
    assert found("kotlin") == [("src/main/kotlin/app/Search.kt:10", "launch", "heuristic")]  # not :19 (requestId)


def test_react():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    assert found("react") == [("src/Search.tsx:10", "async function", "heuristic")]  # not :22 (cancelled), :36 (bundleAsync)


def test_python_and_cli_json():
    assert found("python") == [("app/search.py:16", "async def", "heuristic")]   # not :23 (token), :27 (fixed request)
    out = subprocess.run([sys.executable, "-m", "codegraph.cli", "lint", "async-state", "--db", str(db("python")), "--json"],
                         capture_output=True, text=True, check=True, cwd=ROOT).stdout
    j = json.loads(out)
    assert j["confidence"] == "heuristic" and j["findings"][0]["rule"] == "stale-async-result"
    assert "incomplete-cache-key" in j["not_implemented"] and "two-writers" in j["rules"]


@pytest.mark.parametrize("lang,state,life,late", [
    ("swift", "field:DetailView.title", "Sources/App/Detail.swift:9", "Sources/App/Detail.swift:13"),        # .onAppear / Task
    ("kotlin", "field:app.DetailViewModel.title", "src/main/kotlin/app/Detail.kt:7", "src/main/kotlin/app/Detail.kt:13"),
    ("react", "field:src/Detail.tsx#Detail.title", "src/Detail.tsx:7", "src/Detail.tsx:10"),                 # useEffect / async
    ("python", "field:app.detail.Detail.title", "app/detail.py:6", "app/detail.py:10"),                      # __init__ / async def
])
def test_two_writers(lang, state, life, late):
    """The same state written with a real value by lifecycle code and by async code after an await. A default in the
    initializer (`results = []`) or a flag reset is not a competing writer, so the Search fixtures have none."""
    if lang == "swift":
        pytest.importorskip("tree_sitter_swift")
    if lang == "react" and not shutil.which("node"):
        pytest.skip("node not installed")
    assert two(lang) == [(state, [life], [late], "heuristic")]


def test_mcp_tool(monkeypatch):
    from codegraph import mcp_server as M
    monkeypatch.setattr(M, "_st", lambda: GraphStore(db("kotlin")))
    out = getattr(M.lint_async_state, "fn", M.lint_async_state)()
    assert "heuristic" in out and "Search.kt:10" in out

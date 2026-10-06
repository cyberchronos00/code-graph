"""explore: source, entry points, call paths and blast radius in one call (#122 part 1)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from codegraph import explore as EX
from codegraph.core.store import GraphStore
from codegraph.indexer import index_project

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("explore") / "g.db"
    index_project(ROOT / "examples" / "bookstore-django", path, "bookstore-django")
    return path


@pytest.fixture(scope="module")
def st(db):
    return GraphStore(db)


def test_placing_an_order(st):
    res = EX.explore(st, "how does placing an order work")
    assert res["symbols"][0]["id"] == "function:catalog.api.place_order"
    text = EX.render_explore(res)
    src = next(s for s in res["sources"] if s["id"] == "function:catalog.api.place_order")
    assert src["start"] == 38 and src["end"] == 46
    nums = {no for no, _ in src["lines"]}
    assert nums >= set(range(38, 47))
    assert "def place_order" in text
    assert "POST /api/orders/" in text and "conf=exact" in text
    blast = res["blast"]["function:catalog.api.place_order"]
    written = {t["name"]: t["confidence"] for t in blast["tables_written"]}
    assert written["catalog_order"] == "resolved"
    assert written["catalog_orderline"] == "resolved"
    assert any(j["name"] == "send_order_confirmation" for j in blast["jobs"])
    assert "catalog_order" in text and "catalog_orderline" in text and "send_order_confirmation" in text


def test_spec_create_book(st):
    res = EX.explore(st, "catalog.api.create_book")
    assert [s["id"] for s in res["symbols"]] == ["function:catalog.api.create_book"]
    text = EX.render_explore(res)
    assert "POST /api/books/" in text
    written = [t["name"] for t in res["blast"]["function:catalog.api.create_book"]["tables_written"]]
    assert "store_books" in written


def test_call_path_writes_table(st):
    res = EX.explore(st, "place_order catalog_order")
    text = EX.render_explore(res)
    assert "WRITES_TABLE" in text and "catalog/api.py:39" in text
    hops = [h for p in res["paths"] for h in p["hops"]]
    assert any(h["kind"] == "WRITES_TABLE" and h["at"] == "catalog/api.py:39" for h in hops)


def test_budget(st):
    res = EX.explore(st, "how does placing an order work", budget_tokens=500)
    text = EX.render_explore(res)
    assert len(text) <= 2200
    assert "## entry points" in text and "## blast radius" in text
    assert "snippet(" in text
    assert text.strip().endswith("tokens")


def test_nothing_matched(st, db):
    res = EX.explore(st, "zzqx frobnicate")
    text = EX.render_explore(res)
    assert "nothing matched 'zzqx frobnicate'" in text
    assert "zzqx" in text and "frobnicate" in text
    proc = subprocess.run(
        [sys.executable, "-m", "codegraph.cli", "explore", "zzqx frobnicate", "--db", str(db)],
        capture_output=True, text=True)
    assert proc.returncode == 1
    assert "nothing matched" in proc.stdout


def test_cli_create_book(db):
    cmd = [sys.executable, "-m", "codegraph.cli", "explore", "how does creating a book work", "--db", str(db)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "create_book" in proc.stdout
    proc = subprocess.run(cmd + ["--json"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    for key in ("query", "symbols", "entry_points", "paths", "blast", "sources",
                "skipped_sources", "next", "budget"):
        assert key in data
    assert any("create_book" in s["id"] for s in data["symbols"])
    assert data["budget"]["limit"] == 3000 and "used" in data["budget"]


def test_mcp_explore(db):
    from codegraph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE["db"] = str(db)
        assert "POST /api/orders/" in M.explore("how does placing an order work")
        assert "completeness" in M.explore.structured("how does placing an order work")
    finally:
        M.STATE.clear()
        M.STATE.update(old)


@pytest.mark.parametrize("budget,max_symbols", [(500, 4), (500, 20), (800, 20), (3000, 20)])
def test_budget_is_a_hard_cap(st, budget, max_symbols):
    res = EX.explore(st, "how are books created and orders placed with discount",
                     budget_tokens=budget, max_symbols=max_symbols)
    text = EX.render_explore(res)
    assert len(text) <= budget * 4
    assert res["budget"]["used"] <= budget
    for sid in res["over_budget"]:
        assert sid not in res["blast"]
    if res["over_budget"]:
        assert "over budget, not expanded: explore(" in text


def test_small_budget_keeps_top_source(st):
    res = EX.explore(st, "how does placing an order work", budget_tokens=500)
    assert res["sources"] and res["sources"][0]["id"] == "function:catalog.api.place_order"
    assert "def place_order" in EX.render_explore(res)


def test_bare_word_skips_test_fixture(st):
    ids = [s["id"] for s in EX.explore(st, "book")["symbols"]]
    assert ids and not any(".tests." in i for i in ids)
    assert [s["id"] for s in EX.explore(st, "catalog.tests.conftest.book")["symbols"]] == \
        ["function:catalog.tests.conftest.book"]


def test_file_uses_its_symbols(st):
    ids = [s["id"] for s in EX.explore(st, "catalog/api.py")["symbols"]]
    assert ids and not any(i.startswith("module:") for i in ids)
    assert all(st.node(i)["file"] == "catalog/api.py" for i in ids)


def test_mcp_explore_stale_note(db, monkeypatch):
    from codegraph import hooks
    from codegraph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE["db"] = str(db)
        M.STATE["root"] = str(ROOT / "examples" / "bookstore-django")
        monkeypatch.delenv("CODEGRAPH_NO_STALE_CHECK", raising=False)
        monkeypatch.setattr(hooks, "staleness", lambda *a, **k: True)
        M._STALE["key"] = None
        assert "files changed since this graph was built" in M.explore("how does placing an order work")
        assert M.explore.structured("how does placing an order work")["stale"] is True
        M._STALE["key"] = None
        assert "coverage" in M.explore("zzqx frobnicate")
    finally:
        M._STALE["key"] = None
        M.STATE.clear()
        M.STATE.update(old)

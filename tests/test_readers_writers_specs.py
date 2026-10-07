"""#178: `cg readers` / `cg writers` take the same specs (`table`, `table:X`, `table.column`, `column:table.column`, `Type.prop`,
`Class::$prop`), list table-level writes apart, read READS_TABLE / READS_COLUMN (heuristic MENTIONS_COLUMN last), print
language-neutral errors with close column matches, and `--json` rows carry `target_kind`. The bundled bookstore sample covers
the PHP forms; a small synthetic graph covers a spec that is both a property and a column, mentions, and the Swift / Kotlin hint."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from sample import build, needs_php  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.model import Edge, Node  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402

CTL = "App\\Http\\Controllers\\Admin\\BookController::"


def cg(*args, db) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", *args, "--db", str(db)], cwd=ROOT, capture_output=True, text=True)


@pytest.fixture(scope="module")
def api():
    return build()["api"]


@needs_php
def test_column_spec_forms_agree(api):
    st = GraphStore(api)
    base = Q.writers(st, "books.title")
    assert {r["fqn"] for r in base if r["group"] == "column"} == {CTL + "store", CTL + "update"}
    assert all(r["kind"] == "WRITES_COLUMN" and r["target_kind"] == "column" for r in base if r["group"] == "column")
    assert Q.writers(st, "column:books.title") == base


@needs_php
def test_table_spec_forms_agree(api):
    st = GraphStore(api)
    assert Q.writers(st, "table:books") == Q.writers(st, "books") != []
    assert {r["target_kind"] for r in Q.writers(st, "books")} == {"table"}


@needs_php
def test_table_level_writes_are_a_separate_group_without_column_writers(api):
    st = GraphStore(api)
    groups = Q.access(st, "books.title", "writers")
    assert [g["group"] for g in groups] == ["column", "table_level"]
    tl = groups[1]
    assert tl["label"] == "table-level writes of books (columns not recorded)"
    assert {r["fqn"] for r in tl["rows"]} == {"App\\Console\\Commands\\SyncWarehouseCommand::handle"}
    assert {r["kind"] for r in tl["rows"]} == {"WRITES_TABLE"}
    column_writers = {r["src"] for r in groups[0]["rows"]}
    assert not column_writers & {r["src"] for r in tl["rows"]}          # a function with a column write is not repeated
    # the controller writes `books` too, but records columns for it, so it is not a table-level writer of `stock` either
    assert CTL + "update" not in {r["fqn"] for r in tl["rows"]}


@needs_php
def test_writers_cli_text(api):
    out = cg("writers", "books.title", db=api).stdout
    lines = out.splitlines()
    assert lines[0].startswith("[Http/Controllers/Admin] " + CTL + "store  WRITES_COLUMN column:books.title  @app/Http/Controllers/Admin/BookController.php:15 (resolved)")
    assert lines[0].endswith("entries: http_route")
    i = lines.index("table-level writes of books (columns not recorded):")
    assert lines[i + 1].startswith("  [Console/Commands] App\\Console\\Commands\\SyncWarehouseCommand::handle  WRITES_TABLE table:books")
    assert lines[-1] == "3 write edges, 3 writers"


@needs_php
def test_readers_of_a_column(api):
    out = cg("readers", "books.is_active", db=api).stdout.splitlines()
    assert len(out) == 3 and out[-1] == "2 read edges, 2 readers"
    assert out[0].startswith("[Http/Controllers/Admin] App\\Http\\Controllers\\Admin\\InventoryController::index  READS_COLUMN column:books.is_active")
    assert out[1].startswith("[Services] App\\Services\\StockService::reserveLocal  READS_COLUMN column:books.is_active")
    assert Q.readers(GraphStore(api), "column:books.is_active") == Q.readers(GraphStore(api), "books.is_active")


@needs_php
def test_readers_of_a_table_group_by_column(api):
    st = GraphStore(api)
    groups = Q.access(st, "books", "readers")
    assert all(g["target_kind"] == "table" for g in groups)
    labels = [g["label"] for g in groups]
    assert "reads of column books.is_active" in labels and "reads of column books.isbn" in labels
    assert labels == sorted(labels, key=lambda x: (not x.startswith("reads of table"), x.startswith("string"), x))
    assert {r["kind"] for g in groups for r in g["rows"]} <= {"READS_TABLE", "READS_COLUMN", "MENTIONS_COLUMN"}
    out = cg("readers", "table:books", db=api).stdout
    assert "reads of column books.is_active:" in out and out.strip().endswith("read edges, 6 readers")


@needs_php
def test_json_rows_carry_target_kind(api):
    rows = json.loads(cg("writers", "books.title", "--json", db=api).stdout)
    assert {r["target_kind"] for r in rows} == {"column"} and {r["group"] for r in rows} == {"column", "table_level"}
    rows = json.loads(cg("writers", "books", "--json", db=api).stdout)
    assert {r["target_kind"] for r in rows} == {"table"}
    rows = json.loads(cg("readers", "books.is_active", "--json", db=api).stdout)
    assert {r["target_kind"] for r in rows} == {"column"}


@needs_php
def test_errors_are_language_neutral_and_list_similar_columns(api):
    for what in ("readers", "writers"):
        out = cg(what, "books.titel", db=api).stdout.strip()
        assert out.startswith("table books has no column 'titel'; similar: title. columns: id, store_id, isbn, title, price, is_active")
        assert "field:" not in out and "Swift" not in out
    out = cg("readers", "books.zzz", db=api).stdout
    assert "table books has no column 'zzz'. columns:" in out and "similar" not in out
    out = cg("writers", "zzz", db=api).stdout
    assert out.startswith("no stored property, table or column 'zzz' in the graph.") and "tables: books, orders" in out
    assert "field:" not in out
    out = cg("readers", "book", db=api).stdout
    assert out.startswith("no stored property, table or column 'book'") and "similar: books" in out
    out = cg("writers", "table:zzz", db=api).stdout
    assert out.startswith("no table 'zzz' in the graph.")
    out = cg("readers", "column:books.zzz", db=api).stdout
    assert "table books has no column 'zzz'" in out


@needs_php
def test_known_spec_without_rows_says_what_is_recorded(api):
    out = cg("readers", "books.age_rating", db=api).stdout
    assert out.startswith("no readers recorded for column 'books.age_rating'")
    assert cg("writers", "stores.name", db=api).stdout.startswith("no writers recorded for column 'stores.name'")
    assert cg("readers", "users", db=api).stdout.startswith("no readers recorded for table 'users'")
    # a column nobody writes still lists the functions that write its table without recording columns
    assert cg("writers", "books.created_at", db=api).stdout.startswith("table-level writes of books (columns not recorded):")


@needs_php
def test_mcp_matches_the_cli(api, monkeypatch):
    from cg_code_graph import mcp_server as M
    monkeypatch.setattr(M, "_st", lambda: GraphStore(api))
    out = M.writers(table="books.title")
    assert out.startswith("column books.title: 3 write edges from 3 writers")
    assert "BookController::update  WRITES_COLUMN column:books.title" in out
    assert "table-level writes of books (columns not recorded):" in out and "SyncWarehouseCommand::handle" in out
    assert M.writers(table="column:books.title") == out
    out = M.readers(prop="books.is_active")
    assert "InventoryController::index" in out and "READS_COLUMN column:books.is_active" in out
    out = M.readers(prop="books")
    assert "reads of column books.isbn:" in out
    assert M.readers(prop="books.titel").startswith("table books has no column 'titel'; similar: title. columns: id,")
    assert M.writers(table="zzz").startswith("no stored property, table or column 'zzz' in the graph.")
    assert M.writers(table="books").startswith("table books: ")                       # the table form keeps its grouped reply


# ----------------------------------------------------------------------------------------------------- synthetic graphs
def graph(tmp_path, nodes, edges):
    st = GraphStore.create(tmp_path / "g.db")
    st.write(nodes, edges)
    return st


def fn(name, lang="python"):
    return Node(id=f"function:{name}", kind="function", name=name, fqn=name, file=f"{name}.py", line=1, module="app", lang=lang)


def test_a_spec_that_is_a_property_and_a_column_lists_both_groups(tmp_path):
    nodes = [Node(id="field:orders.total", kind="field", name="total", fqn="orders.total", lang="python", attrs={"property": "stored"}),
             Node(id="table:orders", kind="table", name="orders", lang="sql"),
             Node(id="column:orders.total", kind="column", name="total", fqn="orders.total", lang="sql"),
             fn("set_prop"), fn("set_col")]
    edges = [Edge("function:set_prop", "field:orders.total", "WRITES_PROP", "a.py", 3, "resolved"),
             Edge("function:set_col", "column:orders.total", "WRITES_COLUMN", "b.py", 4, "resolved")]
    st = graph(tmp_path, nodes, edges)
    groups = Q.access(st, "orders.total", "writers")
    assert [(g["target_kind"], g["label"]) for g in groups] == [("property", "stored property orders.total"),
                                                                 ("column", "column orders.total")]
    assert {r["target_kind"] for r in Q.writers(st, "orders.total")} == {"property", "column"}
    text = "\n".join(Q.render_access(groups))
    assert "stored property orders.total:" in text and "column orders.total:" in text
    from cg_code_graph import mcp_server as M
    M.STATE["db"] = str(tmp_path / "g.db")
    out = M.writers(table="orders.total")
    assert out.startswith("property/column orders.total: 2 write edges from 2 writers")
    assert "stored property orders.total:" in out and "column orders.total:" in out


def test_mentions_come_last_and_are_labelled(tmp_path):
    nodes = [Node(id="table:books", kind="table", name="books", lang="sql"),
             Node(id="column:books.title", kind="column", name="title", fqn="books.title", lang="sql"),
             fn("reader"), fn("mentioner")]
    edges = [Edge("function:mentioner", "column:books.title", "MENTIONS_COLUMN", "m.py", 9, "heuristic"),
             Edge("function:reader", "column:books.title", "READS_COLUMN", "r.py", 2, "resolved"),
             Edge("function:reader", "table:books", "READS_TABLE", "r.py", 1, "resolved")]
    st = graph(tmp_path, nodes, edges)
    for spec in ("books.title", "books"):
        groups = Q.access(st, spec, "readers")
        assert groups[-1]["group"] == "mentions" and groups[-1]["rows"][0]["kind"] == "MENTIONS_COLUMN"
        assert "heuristic" in groups[-1]["label"] and "string mentions" in groups[-1]["label"]
        assert all(g["group"] != "mentions" for g in groups[:-1])
    assert [g["group"] for g in Q.access(st, "books", "readers")] == ["table", "column", "mentions"]
    text = "\n".join(Q.render_access(Q.access(st, "books.title", "readers")))
    assert text.index("READS_COLUMN") < text.index("string mentions")


def test_field_hint_only_when_the_graph_has_swift_or_kotlin(tmp_path):
    base = [Node(id="table:books", kind="table", name="books", lang="sql")]
    py = graph(tmp_path, base + [fn("f")], [])
    assert "field:" not in Q.explain_no_writers(py, "Cart.total", "readers")
    for lang in ("swift", "kotlin"):
        d = tmp_path / lang
        d.mkdir()
        st = graph(d, base + [fn("f", lang)], [])
        msg = Q.explain_no_writers(st, "Cart.total", "readers")
        assert "`field:` nodes" in msg and "Swift / Kotlin" in msg and msg.startswith("no stored property, table or column 'Cart.total'")

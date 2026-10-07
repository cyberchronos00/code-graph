"""#179: the database connection of a table / column edge (`attrs.connection`), shown in `reaches` / `routes --reaches` /
`writers` / `readers` (`conn=` when it is not the default), `impact` (`connections:` line), filtered by `--connection`, and kept
per table (`attrs.connections`, one `CONNECTS_TO` per connection).

tests/db_connection_fixture covers each source in precedence order (the query's own `on()` / `DB::connection()` /
`setConnection()`, the model's `$connection`, the default, a dynamic pattern, an unresolvable name, a mass-assignment write); the
bundled bookstore sample covers the issue's example; a synthetic graph covers a plugin that only sets `attrs.connection`."""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from sample import build, needs_php  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.model import Edge, Node  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.external import table_connections  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.link import link  # noqa: E402

pytestmark = [needs_php, pytest.mark.skipif(not shutil.which("php"), reason="php not installed")]
FIX = ROOT / "tests" / "db_connection_fixture"
CAT = "method:App\\Services\\Catalog::"
_S: dict = {}


def db() -> Path:
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-db-conn-"))
        index_project(FIX, d / "g.db", "db-conn")
        _S["db"] = d / "g.db"
    return _S["db"]


def cg(*args, path=None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", *args, "--db", str(path or db())], cwd=ROOT,
                          capture_output=True, text=True)


def edge(src: str, kind: str, dst: str) -> dict:
    q = "select attrs, file, line from edges where src=? and kind=? and dst=?"
    rows = sqlite3.connect(db()).execute(q, (src, kind, dst)).fetchall()
    assert len(rows) == 1, (src, kind, dst, rows)
    return json.loads(rows[0][0])


def conn(fn: str, kind: str, dst: str) -> str:
    return edge(CAT + fn, kind, dst)["connection"]


# ------------------------------------------------------------------------------------- Scope 1: attrs.connection, in order
def test_query_on_in_a_chain():
    a = edge(CAT + "onChain", "READS_COLUMN", "column:books.isbn")
    assert a["connection"] == "warehouse" and a["connection_via"] == "Book::on"
    assert a["connection_default"] == "mysql" and a["connection_fallback"] == "mysql" and a["connection_model"] == "Book"


def test_db_connection_table():
    a = edge(CAT + "viaFacade", "READS_COLUMN", "column:books.title")
    assert a["connection"] == "warehouse" and a["connection_via"] == "DB::connection"
    assert edge(CAT + "viaFacade", "READS_TABLE", "table:books")["connection"] == "warehouse"


def test_set_connection_on_a_variable():
    a = edge(CAT + "viaBuilderVariable", "WRITES_COLUMN", "column:books.price")
    assert a["connection"] == "reporting" and a["connection_via"] == "setConnection"
    assert edge(CAT + "viaBuilderVariable", "WRITES_TABLE", "table:books")["connection"] == "reporting"


def test_model_connection_property():
    a = edge(CAT + "viaModelProperty", "READS_COLUMN", "column:shipments.isbn")
    assert a["connection"] == "warehouse" and a["connection_via"] == "Shipment::$connection"
    assert "connection_fallback" not in a


def test_the_query_beats_the_model_property():
    a = edge(CAT + "queryOverridesModel", "READS_COLUMN", "column:shipments.isbn")
    assert a["connection"] == "reporting" and a["connection_via"] == "Shipment::on" and a["connection_fallback"] == "warehouse"


def test_default_connection_is_recorded_too():
    a = edge(CAT + "inDefault", "READS_COLUMN", "column:books.title")
    assert a["connection"] == "mysql" and a["connection_default"] == "mysql" and "connection_via" not in a
    d = sqlite3.connect(db()).execute("select attrs from nodes where id='connection:mysql'").fetchone()[0]
    assert json.loads(d)["default"] is True


def test_dynamic_name_keeps_its_pattern():
    assert conn("dynamicName", "READS_COLUMN", "column:books.store_id") == "legacy_{storeId}"
    a = edge(CAT + "viaProvider", "READS_COLUMN", "column:books.price")
    assert a["connection"] == "legacy_{store.id}" and a["connection_from"] == "Catalog::legacyConnection"


def test_unresolvable_name_is_a_question_mark():
    a = edge(CAT + "unknownName", "READS_COLUMN", "column:books.price")
    assert a["connection"] == "?" and a["connection_via"] == "Book::on"


def test_mass_assignment_write_uses_the_model_connection():
    a = edge("method:App\\Http\\Controllers\\AuditController::store", "WRITES_COLUMN", "column:audits.note")
    assert a["mass_assignment"] is True and a["connection"] == "reporting" and a["connection_via"] == "Audit::$connection"


def test_every_table_and_column_edge_has_a_connection():
    rows = sqlite3.connect(db()).execute(
        "select kind, src, dst, attrs from edges where kind in ('READS_COLUMN','WRITES_COLUMN','READS_TABLE','WRITES_TABLE') "
        "and src like 'method:App%' and file not like 'database/%'").fetchall()
    assert rows and all(json.loads(a).get("connection") for _, _, _, a in rows), [r[:3] for r in rows if "connection" not in r[3]]


# ------------------------------------------------------------------------------------- Scope 2: output
def test_reaches_shows_conn_only_when_not_the_default():
    out = cg("reaches", "table:books").stdout
    assert "READS_COLUMN[resolved @ app/Services/Catalog.php:19 conn=warehouse]-> column:books.isbn" in out
    assert "READS_COLUMN[resolved @ app/Services/Catalog.php:14]-> column:books.title" in out        # default: no label
    assert "conn=mysql" not in out
    assert "conn=legacy_{store.id}" in out and "conn=?" in out


def test_reaches_json_hops_carry_the_connection():
    res = json.loads(cg("reaches", "column:books.isbn", "--json").stdout)
    hops = [h for i in res["items"] for h in i["path"] if h.get("connection")]
    assert {h["connection"] for h in hops} == {"warehouse"} and all(h["connection_default"] == "mysql" for h in hops)


def test_routes_reaches_step_label():
    out = cg("routes", "--reaches", "audits.note").stdout
    assert "WRITES_COLUMN@AuditController.php:14~r conn=reporting → column:audits.note" in out
    out = cg("routes", "--reaches", "books.isbn").stdout
    assert "READS_COLUMN@Catalog.php:19~r conn=warehouse → column:books.isbn" in out


def test_writers_and_readers_label_the_connection():
    out = cg("writers", "books").stdout
    assert "Catalog::viaBuilderVariable  WRITES_COLUMN column:books.price" in out and "conn=reporting" in out
    out = cg("readers", "books.title").stdout
    assert out.count("conn=warehouse") == 1
    line = next(x for x in out.splitlines() if "Catalog::inDefault" in x)
    assert "conn=" not in line
    rows = json.loads(cg("readers", "books.title", "--json").stdout)
    assert {r["fqn"].split("::")[1]: r["connection"] for r in rows} == {"inDefault": "mysql", "viaFacade": "warehouse"}


def test_impact_connections_line():
    out = cg("impact", "Catalog::viaProvider", "--no-paths").stdout
    line = next(x for x in out.splitlines() if x.startswith("connections:"))
    assert line == ("connections: legacy_{store.id} (Book::on via Catalog::legacyConnection, app/Services/Catalog.php:51; "
                    "Book default: mysql)")
    assert "connections:" in cg("impact", "Catalog::viaModelProperty", "--no-paths").stdout
    assert "Shipment::$connection, app/Services/Catalog.php:36" in cg("impact", "Catalog::viaModelProperty").stdout
    assert "connections:" not in cg("impact", "Catalog::inDefault", "--no-paths").stdout


def test_connection_filter_on_writers_readers_and_reaches():
    out = cg("readers", "books", "--connection", "warehouse").stdout
    assert "Catalog::onChain" in out and "Catalog::viaFacade" in out and "Catalog::inDefault" not in out
    assert "Catalog::dynamicName" not in out
    out = cg("writers", "books", "--connection", "mysql").stdout
    assert "Catalog::viaBuilderVariable" not in out
    out = cg("readers", "books.store_id", "--connection", "legacy_{storeId}").stdout
    assert "Catalog::dynamicName" in out
    out = cg("reaches", "table:books", "--connection", "warehouse").stdout
    assert "Catalog::onChain" in out and "Catalog::inDefault" not in out and "Catalog::viaProvider" not in out
    assert "Catalog::viaFacade" in out


def test_connection_filter_without_a_match_lists_the_connections_seen():
    out = cg("writers", "books", "--connection", "nope").stdout.strip()
    assert out == ("no writers recorded for table 'books' on connection 'nope' (connections seen: warehouse 3 edges, reporting 2 edges, "
                   "? 1 edge, legacy_{store.id} 1 edge, legacy_{storeId} 1 edge, mysql 1 edge).")
    out = cg("reaches", "table:books", "--connection", "nope").stdout
    assert "no dependents recorded for table:books on connection 'nope' (connections seen: warehouse 3 edges, " in out
    assert json.loads(cg("writers", "books", "--connection", "nope", "--json").stdout) == []


def test_mcp_matches_the_cli(monkeypatch):
    from cg_code_graph import mcp_server as M
    monkeypatch.setattr(M, "_st", lambda: GraphStore(db()))
    out = M.writers(table="books", connection="nope")
    assert out.startswith(cg("writers", "books", "--connection", "nope").stdout.strip())
    assert "Catalog::onChain" in M.readers(prop="books", connection="warehouse")
    assert "Catalog::inDefault" not in M.readers(prop="books", connection="warehouse")
    assert "conn=reporting" in M.writers(table="books")
    r = M.reaches(targets=["table:books"], connection="warehouse")
    assert "Catalog::onChain" in r and "Catalog::inDefault" not in r and "READS_COLUMN@Catalog.php:19~r conn=warehouse" in r
    assert "no dependents recorded for table:books on connection 'nope' (connections seen: warehouse 3 edges," in \
        M.reaches(targets=["table:books"], connection="nope")
    assert "READS_COLUMN@Catalog.php:19~r conn=warehouse" in M.reaches(targets=["table:books"])
    imp = M.impact(method="Catalog::viaProvider")
    assert "connections: legacy_{store.id} (Book::on via Catalog::legacyConnection" in imp


# ------------------------------------------------------------------------------------- Scope 3: tables on several connections
def test_table_node_lists_every_connection_with_counts():
    a = json.loads(sqlite3.connect(db()).execute("select attrs from nodes where id='table:books'").fetchone()[0])
    c = a["connections"]
    assert list(c)[0] == "warehouse" and set(c) == {"mysql", "warehouse", "reporting", "legacy_{store.id}", "legacy_{storeId}", "?"}
    n = sqlite3.connect(db()).execute(
        "select count(*) from edges where kind in ('READS_TABLE','WRITES_TABLE','READS_COLUMN','WRITES_COLUMN','MENTIONS_COLUMN') "
        "and (dst='table:books' or dst like 'column:books.%') and json_extract(attrs,'$.connection')='warehouse'").fetchone()[0]
    assert c["warehouse"] == n >= 2
    assert sum(c.values()) == sum(
        1 for (x,) in sqlite3.connect(db()).execute(
            "select attrs from edges where kind in ('READS_TABLE','WRITES_TABLE','READS_COLUMN','WRITES_COLUMN','MENTIONS_COLUMN') "
            "and (dst='table:books' or dst like 'column:books.%')") if "connection" in x)
    s = json.loads(sqlite3.connect(db()).execute("select attrs from nodes where id='table:shipments'").fetchone()[0])
    assert s["connections"] == {"reporting": 1, "warehouse": 1}


def test_one_connects_to_per_connection():
    rows = sqlite3.connect(db()).execute("select dst, attrs from edges where kind='CONNECTS_TO' and src='table:books'").fetchall()
    assert {d for d, _ in rows} == {"external:mysql:db.internal.example:3306", "external:mysql:warehouse.internal.example:3306",
                                    "external:mysql:reports.internal.example:3306"}
    vias = {json.loads(a)["via"] for _, a in rows}
    assert {"connection warehouse", "connection reporting"} <= vias
    st = sqlite3.connect(db())
    shp = {d for (d,) in st.execute("select dst from edges where kind='CONNECTS_TO' and src='table:shipments'")}
    assert shp == {"external:mysql:warehouse.internal.example:3306", "external:mysql:reports.internal.example:3306"}


def test_attributes_survive_cg_link(tmp_path):
    other = GraphStore.create(tmp_path / "web.db")
    other.write([Node(id="function:page", kind="function", name="page", fqn="page", file="page.ts", line=1, module="web", lang="typescript")], [])
    link(str(db()), str(tmp_path / "web.db"), str(tmp_path / "all.db"), backend_name="shop", frontend_name="web")
    c = sqlite3.connect(tmp_path / "all.db")
    a = json.loads(c.execute("select attrs from edges where kind='READS_COLUMN' and dst='column:books.isbn' and src like ?",
                             (CAT + "onChain",)).fetchone()[0])
    assert a["connection"] == "warehouse" and a["connection_via"] == "Book::on"
    assert json.loads(c.execute("select attrs from nodes where id='table:books'").fetchone()[0])["connections"]["warehouse"] >= 2
    out = cg("reaches", "table:books", "--connection", "warehouse", path=tmp_path / "all.db").stdout
    assert "Catalog::onChain" in out and "conn=warehouse" in out


# ------------------------------------------------------------------------------------- the issue's example
def test_bookstore_example():
    api = build()["api"]
    out = cg("impact", "ArchiveService::legacyBooks", "--no-paths", path=api).stdout
    assert ("connections: legacy_{store.id} (Book::on via ArchiveService::legacyConnection, "
            "app/Services/ArchiveService.php:13; Book default: mysql)") in out
    assert "ArchiveController::index" in out
    out = cg("reaches", "table:books", path=api).stdout
    assert "-READS_COLUMN[resolved @ app/Services/ArchiveService.php:13 conn=legacy_{store.id}]-> column:books.store_id" in out
    out = cg("writers", "books", "--connection", "warehouse", path=api).stdout.strip()
    assert out.startswith("no writers recorded for table 'books' on connection 'warehouse' (connections seen: mysql ")
    assert out.endswith(", legacy_{store.id} 1 edge).")
    out = cg("routes", "--reaches", "books.store_id", path=api).stdout
    assert "READS_COLUMN@ArchiveService.php:13~r conn=legacy_{store.id} → column:books.store_id" in out


# ------------------------------------------------------------------------------------- not tied to one ORM
def test_a_plugin_only_has_to_set_attrs_connection(tmp_path):
    nodes = [Node(id="table:items", kind="table", name="items", lang="sql"),
             Node(id="function:sync", kind="function", name="sync", fqn="sync", file="sync.py", line=1, module="app", lang="python"),
             Node(id="function:show", kind="function", name="show", fqn="show", file="show.py", line=1, module="app", lang="python")]
    edges = [Edge("function:sync", "table:items", "WRITES_TABLE", "sync.py", 5, "resolved", {"connection": "replica"}),
             Edge("function:show", "table:items", "READS_TABLE", "show.py", 2, "resolved", {"connection": "primary"})]
    st = GraphStore.create(tmp_path / "g.db")
    st.write(nodes, edges)
    assert [r["connection"] for r in Q.writers(st, "items")] == ["replica"]
    assert [r["fqn"] for r in Q.readers(st, "items", connection="primary")] == ["show"]
    assert Q.conn_label({"connection": "replica"}) == " conn=replica"        # no default recorded: always shown
    assert Q.conn_label({"connection": "primary", "connection_default": "primary"}) == ""
    assert cg("writers", "items", "--connection", "primary", path=tmp_path / "g.db").stdout.strip() == (
        "no writers recorded for table 'items' on connection 'primary' (connections seen: primary 1 edge, replica 1 edge).")

    class B:
        pass

    b = B()
    b.edges = {i: e for i, e in enumerate(edges)}
    b.nodes = {n.id: n for n in nodes}
    assert table_connections(b) == 1
    assert b.nodes["table:items"].attrs["connections"] == {"primary": 1, "replica": 1}

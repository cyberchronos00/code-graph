"""Nuxt i18n keys (#182): file:line of the defining locale entry, every locale's definition, missing locales, and the
pages / components using a key as its dependents in reaches / impact. tests/nuxt_i18n_fixture has no node_modules
and no .nuxt/."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from cg_code_graph.plugins.nuxt.plugin import i18n_definitions, json_key_lines, nuxt_i18n_config, yaml_key_lines  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

FX = ROOT / "tests" / "nuxt_i18n_fixture"
needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")


def test_key_lines():
    text = '{\n  "a": {\n    "b": "x",\n    "list": [{"c": 1}],\n    "d": "y"\n  },\n  "e\\"q": 2\n}\n'
    assert json_key_lines(text) == {"a": 2, "a.b": 3, "a.list": 4, "a.d": 5, 'e"q': 7}
    assert yaml_key_lines("orders:\n  title: T\n  cancel: C\nnav:\n  home: H\n") == {
        "orders": 1, "orders.title": 2, "orders.cancel": 3, "nav": 4, "nav.home": 5}


def test_config_and_locale_keyed_files(tmp_path):
    cfg = nuxt_i18n_config(FX)
    assert cfg["default_locale"] == "en" and cfg["lang_dir"] == "locales"
    assert cfg["locales"] == ["es", "en", "pt-BR"] and cfg["files"]["pt-BR.yaml"] == "pt-BR"
    # a combined file keyed by locale codes outside the old fixed list (es, pt-BR) defines each locale
    (tmp_path / "locales").mkdir()
    (tmp_path / "locales" / "messages.json").write_text(json.dumps(
        {"es": {"cart": {"empty": "Vacío"}}, "pt-BR": {"cart": {"empty": "Vazio"}}}, indent=1))
    defs, locales, default = i18n_definitions(tmp_path, ".", {})
    assert set(defs) == {"cart.empty"} and locales == ["es", "pt-BR"] and default == "es"
    assert [(d["locale"], d["line"]) for d in defs["cart.empty"]] == [("es", 4), ("pt-BR", 9)]
    # an ordinary file whose top-level keys are not locale codes is not split
    (tmp_path / "locales" / "en.json").write_text('{"cart": {"empty": "Empty"}}')
    defs, _, _ = i18n_definitions(tmp_path, ".", {})
    assert {"locale": "en", "file": "locales/en.json", "line": 1} in defs["cart.empty"]


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    from cg_code_graph.indexer import index_project
    db = tmp_path_factory.mktemp("i18n") / "g.db"
    return index_project(FX, db, "tea-orders"), db


@needs_ts
def test_i18n_nodes_have_locations(indexed):
    stats, db = indexed
    st = stats["plugins"]["typescript/nuxt"]
    assert st["i18n_default_locale"] == "en" and st["i18n_locales"] == ["en", "es", "pt-BR"]
    assert st["i18n_used_but_undefined"] == 0
    con = sqlite3.connect(db)
    rows = {r[0]: (r[1], r[2], json.loads(r[3])) for r in con.execute("SELECT id, file, line, attrs FROM nodes WHERE kind='i18n'")}
    f, line, a = rows["i18n:orders.cancel"]
    assert (f, line) == ("i18n/locales/en.json", 4)
    assert a["definitions"] == [{"locale": "en", "file": "i18n/locales/en.json", "line": 4},
                                {"locale": "es", "file": "i18n/locales/es.json", "line": 3},
                                {"locale": "pt-BR", "file": "i18n/locales/pt-BR.yaml", "line": 3}]
    assert "missing_locales" not in a
    assert rows["i18n:orders.title"][:2] == ("i18n/locales/en.json", 3) and rows["i18n:orders.title"][2]["missing_locales"] == ["es"]
    assert rows["i18n:welcome"][:2] == ("app/pages/index.vue", 8)   # SFC <i18n> block
    assert all(v[0] and v[1] for v in rows.values())


@needs_ts
def test_i18n_users_are_dependents(indexed):
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph import query
    _, db = indexed
    st = GraphStore(db)
    imp = query.impact(st, "i18n:orders.cancel")
    assert [e["name"] for e in imp["entry_points"]] == ["/orders"]
    assert "component:app/components/OrderRow.vue" in {c["id"] for c in imp["callers"]}
    rs = {i["id"]: i for i in query.reaches(st, ["i18n:orders.cancel"])["items"] if not i.get("is_target")}
    assert set(rs) == {"component:app/components/OrderRow.vue", "page:app/pages/orders.vue"}
    assert rs["page:app/pages/orders.vue"]["entry_kind"] == "ui_page"
    out = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "search", "orders.title", "--db", str(db)],
                         capture_output=True, text=True, cwd=ROOT).stdout
    assert "i18n:orders.title  i18n/locales/en.json:3  (2 locales, 1 use)  missing: es" in out
    node = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "node", "i18n:orders.cancel", "--db", str(db)],
                          capture_output=True, text=True, cwd=ROOT).stdout
    assert "definitions: en i18n/locales/en.json:4, es i18n/locales/es.json:3, pt-BR i18n/locales/pt-BR.yaml:3" in node

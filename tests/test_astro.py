"""Astro: .astro frontmatter and <script> as TypeScript, src/pages routes (tests/astro_fixture)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.core.plugin import Project  # noqa: E402
from codegraph.core.detect import detect  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.coverage import SUPPORTED, UNSUPPORTED  # noqa: E402
from codegraph import query as Q  # noqa: E402
from codegraph.plugins.astro.plugin import AstroPlugin, astro_route  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

FX = ROOT / "tests" / "astro_fixture"
_S: dict = {}
needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")

INDEX = "page:src/pages/index.astro"
SLUG = "page:src/pages/books/[slug].astro"
REST = "page:src/pages/docs/[...rest].astro"
BASE = "layout:src/layouts/Base.astro"
CARD = "component:src/components/BookCard.astro"
FORMAT = "module:src/lib/format.ts"
BOOKS = "module:src/lib/books.ts"
LIST = "function:src/lib/books.ts#listBooks"
FIND = "function:src/lib/books.ts#findBook"
PRICE = "function:src/lib/format.ts#formatPrice"
SLUGIFY = "function:src/lib/format.ts#slugify"
GET = "function:src/pages/api/books.ts#GET"
POST = "function:src/pages/api/books.ts#POST"


def built() -> dict:
    if not _S:
        from codegraph.indexer import index_project
        d = Path(tempfile.mkdtemp(prefix="codegraph-astro-"))
        db = d / "astro.db"
        _S["stats"] = index_project(FX, db, "astro-site")
        _S.update(dir=d, db=db)
    return _S


def db():
    return sqlite3.connect(built()["db"])


def node(nid):
    row = db().execute("SELECT name, fqn, entry_kind, attrs FROM nodes WHERE id=?", (nid,)).fetchone()
    assert row, nid
    return {"name": row[0], "fqn": row[1], "entry_kind": row[2], "attrs": json.loads(row[3] or "{}")}


def edge(src, kind, dst):
    return db().execute(
        "SELECT line, confidence FROM edges WHERE src=? AND kind=? AND dst=?", (src, kind, dst)
    ).fetchall()


def test_astro_route():
    assert astro_route("src/pages/index.astro") == "/"
    assert astro_route("src/pages/about.astro") == "/about"
    assert astro_route("src/pages/books/[slug].astro") == "/books/{slug}"
    assert astro_route("src/pages/docs/[...rest].astro") == "/docs/{rest*}"
    assert astro_route("src/pages/api/books.ts") == "/api/books"
    assert astro_route("src/pages/data.json.ts") == "/data.json"
    assert astro_route("src/pages/[lang]-[v].astro") == "/{lang}-{v}"
    assert astro_route("src/pages/_draft.astro") is None
    assert astro_route("src/pages/_lib/x.ts") is None


def test_detect_and_coverage_exts(tmp_path):
    assert AstroPlugin().detect(Project(FX, "astro-site"))
    only = tmp_path / "only-config"
    only.mkdir()
    (only / "astro.config.ts").write_text("export default {}\n")
    assert AstroPlugin().detect(Project(only, "x"))
    assert "astro" in detect(FX)["frameworks"]
    assert "astro" in detect(only)["frameworks"]
    assert ".astro" in SUPPORTED["typescript"]
    assert ".astro" not in UNSUPPORTED


@needs_ts
def test_page_nodes():
    built()
    for nid in (INDEX, SLUG, REST, BASE, CARD, GET, POST, PRICE, SLUGIFY, LIST, FIND):
        node(nid)
    home, slug, rest = node(INDEX), node(SLUG), node(REST)
    assert (home["name"], home["fqn"], home["entry_kind"]) == ("/", "/", "ui_page")
    assert slug["name"] == "/books/{slug}" and slug["entry_kind"] == "ui_page"
    assert slug["attrs"].get("get_static_paths") is True
    assert rest["name"] == "/docs/{rest*}" and rest["entry_kind"] == "ui_page"
    assert rest["attrs"].get("inline_scripts") == 1
    assert "get_static_paths" not in home["attrs"]


@needs_ts
def test_exact_edges():
    built()
    assert (5, "exact") in edge(INDEX, "CALLS", LIST)
    assert (12, "exact") in edge(INDEX, "CALLS", PRICE)
    assert (7, "exact") in edge(INDEX, "RENDERS", BASE)
    assert (8, "exact") in edge(INDEX, "RENDERS", CARD)
    assert (2, "exact") in edge(INDEX, "IMPORTS", BASE)
    assert (3, "exact") in edge(INDEX, "IMPORTS", CARD)
    assert (4, "exact") in edge(INDEX, "IMPORTS", BOOKS)
    assert (11, "exact") in edge(INDEX, "IMPORTS", FORMAT)
    assert (9, "exact") in edge(SLUG, "CALLS", FIND)
    assert (6, "exact") in edge(SLUG, "CALLS", LIST)
    assert (6, "exact") in edge(SLUG, "CALLS", SLUGIFY)
    assert (11, "exact") in edge(SLUG, "RENDERS", BASE)
    assert (2, "exact") in edge(SLUG, "IMPORTS", BASE)
    assert (2, "exact") in edge(CARD, "IMPORTS", FORMAT)
    assert (3, "exact") in edge(CARD, "IMPORTS", BOOKS)
    assert (6, "exact") in edge(CARD, "CALLS", PRICE)


@needs_ts
def test_routes_and_impact():
    built()
    rows = dict(db().execute(
        "SELECT id, line FROM nodes WHERE id IN ('route:GET /api/books', 'route:POST /api/books')"
    ).fetchall())
    assert rows["route:GET /api/books"] == 3
    assert rows["route:POST /api/books"] == 7
    assert (3, "exact") in edge("route:GET /api/books", "ROUTES_TO", GET)
    assert (7, "exact") in edge("route:POST /api/books", "ROUTES_TO", POST)
    imp = Q.impact(GraphStore(built()["db"]), "formatPrice")
    assert CARD in {c["id"] for c in imp["callers"]}
    assert INDEX in {e["id"] for e in imp["entry_points"]}
    books = Q.impact(GraphStore(built()["db"]), "listBooks")
    entries = {e["id"] for e in books["entry_points"]}
    assert {"route:GET /api/books", INDEX, SLUG} <= entries


@needs_ts
def test_coverage_counts():
    st = built()["stats"]
    langs = {e["language"]: e for e in st["coverage"]["languages"]}
    assert "astro" not in langs
    ts = langs["typescript"]
    assert ts["discovered"] == 9
    assert ts["indexed"] == 8
    assert ts["excluded"] == 1
    assert ts["files_complete"]
    assert "astro.config.mjs" in ts.get("paths", {}).get("excluded", [])
    assert "astro" in st["presets"]["frameworks"]


def test_split_frontmatter():
    from codegraph.plugins.astro.plugin import _split
    assert _split("---\nconst a = 1\n---\n<p/>\n") == ("const a = 1", "\n<p/>\n")
    assert _split("---\n---\n<pre>\n---\n</pre>\n") == ("", "\n<pre>\n---\n</pre>\n")   # empty frontmatter
    assert _split("---\r\nconst a = 1\r\n---\r\n<p/>") == ("const a = 1\r", "\r\n<p/>")
    assert _split("---\nconst a = 1\n") == ("const a = 1\n", "")                         # unclosed: to the end
    assert _split("<p>no frontmatter</p>\n") == ("", "<p>no frontmatter</p>\n")


ODD = {
    "package.json": '{"name": "odd", "dependencies": {"astro": "^5.0.0"}}',
    "src/lib/util.ts": "export function helper(): number { return 1 }\n"
                       "export function other(): number { return 2 }\n"
                       "export function third(): number { return 3 }\n",
    "src/components/Card.astro": "<div><slot /></div>\n",
    # empty frontmatter; a later `---` line in the template is not a fence
    "src/pages/empty.astro": "---\n---\n<Card />\n<pre>\n---\n</pre>\n<script>\nimport Card from '../components/Card.astro'\n</script>\n",
    "src/pages/hr.astro": "---\nimport Card from '../components/Card.astro'\n---\n<Card />\n<pre>\n---\n</pre>\n",
    # a commented-out script is not code; a '<script>' string in the frontmatter does not hide the real script
    "src/pages/comment.astro": "---\nimport { other } from '../lib/util'\nother()\n---\n"
                               "<!-- <script>import { helper } from '../lib/util'; helper()</script> -->\n",
    "src/pages/string.astro": "---\nconst tag = '<script>'\n---\n"
                              "<script>\nimport { third } from '../lib/util'\nthird()\n</script>\n",
    # unclosed fence: the frontmatter runs to the end, the stray template line is a syntax error
    "src/pages/unclosed.astro": "---\nimport { helper } from '../lib/util'\nconst xs: Array<String> = []\nhelper()\n<p>oops</p>\n",
    # unclosed <script>: runs to the end of the file
    "src/components/OpenScript.astro": "<div>\n<script>\nimport { helper } from '../lib/util'\nhelper()\n",
    # no frontmatter, several scripts, is:inline, style and JSON-LD
    "src/components/Scripts.astro": "<div>\n<style>\n.Card { color: red }\n</style>\n"
                                    "<script>\nimport { helper } from '../lib/util'\nhelper()\n</script>\n"
                                    "<script>\nimport { other } from '../lib/util'\nother()\n</script>\n"
                                    "<script is:inline>\nwindow.x = 1\n</script>\n"
                                    "<script type=\"application/ld+json\">{\"a\": 1}</script>\n</div>\n",
    # top-level return in the frontmatter (Astro.redirect) is not a syntax error
    "src/pages/redir.astro": "---\nimport { helper } from '../lib/util'\nif (!helper()) return Astro.redirect('/')\n---\n<p/>\n",
    "src/pages/api/[id].json.ts": "import { helper } from '../../lib/util'\n"
                                  "export const GET = () => new Response(String(helper()))\n"
                                  "export const ALL = () => new Response(null)\n",
    "src/pages/api/_hidden.ts": "export function GET() { return new Response('') }\n",
}


@needs_ts
def test_odd_astro_files(tmp_path):
    from codegraph.indexer import index_project
    root = tmp_path / "odd"
    for rel, text in ODD.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    # a page that is not UTF-8 must not stop the plugin before the pages and routes after it
    (root / "src/pages/a-latin1.astro").write_bytes(b"---\nconst s = 'caf\xe9'\n---\n<p>{s}</p>\n")
    dbp = tmp_path / "odd.db"
    st = index_project(root, dbp, "odd")
    c = sqlite3.connect(dbp)

    def e(src, kind, dst):
        return {r[0] for r in c.execute("SELECT line FROM edges WHERE src=? AND kind=? AND dst=?", (src, kind, dst))}

    U = "function:src/lib/util.ts#"
    CARD_ = "component:src/components/Card.astro"
    assert e("page:src/pages/hr.astro", "RENDERS", CARD_) == {4}
    assert e("page:src/pages/empty.astro", "IMPORTS", CARD_) == {8}
    assert e("page:src/pages/empty.astro", "RENDERS", CARD_) == {3}
    assert e("page:src/pages/comment.astro", "CALLS", U + "other") == {3}
    assert e("page:src/pages/comment.astro", "CALLS", U + "helper") == set()
    assert e("page:src/pages/string.astro", "CALLS", U + "third") == {6}
    assert e("page:src/pages/unclosed.astro", "CALLS", U + "helper") == {4}
    assert e("component:src/components/OpenScript.astro", "CALLS", U + "helper") == {4}
    assert e("component:src/components/Scripts.astro", "CALLS", U + "helper") == {7}
    assert e("component:src/components/Scripts.astro", "CALLS", U + "other") == {11}
    assert e("page:src/pages/redir.astro", "CALLS", U + "helper") == {3}
    names = dict(c.execute("SELECT id, name FROM nodes WHERE kind='page'").fetchall())
    assert names["page:src/pages/a-latin1.astro"] == "/a-latin1"
    assert names["page:src/pages/unclosed.astro"] == "/unclosed"
    attrs = json.loads(c.execute("SELECT attrs FROM nodes WHERE id='component:src/components/Scripts.astro'").fetchone()[0])
    assert attrs["inline_scripts"] == 1
    routes = {r[0] for r in c.execute("SELECT id FROM nodes WHERE kind='route'")}
    assert routes == {"route:GET /api/{id}.json", "route:ANY /api/{id}.json"}
    ts = {x["language"]: x for x in st["coverage"]["languages"]}["typescript"]
    assert {s["file"]: s["spans"] for s in ts.get("syntax_errors", [])} == {"src/pages/unclosed.astro": [[5, 5]]}

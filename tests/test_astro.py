"""Astro: .astro frontmatter and <script> as TypeScript, src/pages routes (tests/astro_fixture)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.plugin import Project  # noqa: E402
from cg_code_graph.core.detect import detect  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.coverage import SUPPORTED, UNSUPPORTED  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.plugins.astro.plugin import AstroPlugin, astro_route  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

FX = ROOT / "tests" / "astro_fixture"
_S: dict = {}
needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")

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
SHOP = "page:src/pages/shop.astro"
LABEL = "function:src/pages/shop.astro#label"
GSP = "function:src/pages/books/[slug].astro#getStaticPaths"
COUNTER = "function:src/components/Counter.tsx#Counter"
TOGGLE = "component:src/components/Toggle.vue"
ACCENT = "constant:src/pages/shop.astro#accent"


def built() -> dict:
    if not _S:
        from cg_code_graph.indexer import index_project
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


def edge_attrs(src, kind, dst, line):
    row = db().execute(
        "SELECT attrs FROM edges WHERE src=? AND kind=? AND dst=? AND line=?", (src, kind, dst, line)
    ).fetchone()
    assert row, (src, kind, dst, line)
    return json.loads(row[0] or "{}")


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
    for nid in (INDEX, SLUG, REST, BASE, CARD, GET, POST, PRICE, SLUGIFY, LIST, FIND, LABEL, GSP):
        node(nid)
    home, slug, rest = node(INDEX), node(SLUG), node(REST)
    lab = db().execute("SELECT line, attrs FROM nodes WHERE id=?", (LABEL,)).fetchone()
    assert lab[0] == 7 and json.loads(lab[1])["parent"] == SHOP
    gsp = db().execute("SELECT line, attrs FROM nodes WHERE id=?", (GSP,)).fetchone()
    assert gsp[0] == 5 and json.loads(gsp[1])["parent"] == SLUG
    assert node(BASE)["attrs"]["props"] == ["title"]
    assert node(CARD)["attrs"]["props"] == ["book"]
    assert node(SLUG)["attrs"]["params"] == ["slug"]
    assert node(REST)["attrs"]["params"] == ["rest"]
    assert node(SHOP)["attrs"]["define_vars"] == ["accent", "price"]
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
    assert (6, "exact") in edge(GSP, "CALLS", LIST)
    assert (6, "exact") in edge(GSP, "CALLS", SLUGIFY)
    assert (5, "exact") in edge(SLUG, "CALLS", GSP)
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
    assert {c["id"] for c in imp["callers"]} >= {CARD, LABEL}
    entries = {e["id"]: e for e in imp["entry_points"]}
    assert INDEX in entries and SHOP in entries
    slug_imp = Q.impact(GraphStore(built()["db"]), "slugify")
    assert SLUG in {e["id"] for e in slug_imp["entry_points"]}
    books = Q.impact(GraphStore(built()["db"]), "listBooks")
    entries = {e["id"] for e in books["entry_points"]}
    assert {"route:GET /api/books", INDEX, SLUG} <= entries


@needs_ts
def test_coverage_counts():
    st = built()["stats"]
    langs = {e["language"]: e for e in st["coverage"]["languages"]}
    assert "astro" not in langs
    ts = langs["typescript"]
    assert ts["discovered"] == 12
    assert ts["indexed"] == 11
    assert ts["excluded"] == 1
    assert ts["files_complete"]
    assert "astro.config.mjs" in ts.get("paths", {}).get("excluded", [])
    assert "astro" in st["presets"]["frameworks"]


def test_split_frontmatter():
    from cg_code_graph.plugins.astro.plugin import _split
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
    from cg_code_graph.indexer import index_project
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


@needs_ts
def test_template_expressions():
    built()
    assert (2, "exact") in edge(SHOP, "IMPORTS", BASE)
    assert (3, "exact") in edge(SHOP, "IMPORTS", "module:src/components/Counter.tsx")
    assert (4, "exact") in edge(SHOP, "IMPORTS", TOGGLE)
    assert (5, "exact") in edge(SHOP, "IMPORTS", FORMAT)
    assert (6, "exact") in edge(SHOP, "IMPORTS", BOOKS)
    assert (11, "exact") in edge(SHOP, "CALLS", LIST)
    assert (13, "exact") in edge(SHOP, "CALLS", SLUGIFY)
    assert edge_attrs(SHOP, "CALLS", SLUGIFY, 13).get("template") is True
    assert (15, "exact") in edge(SHOP, "CALLS", LIST)
    assert (16, "exact") in edge(SHOP, "CALLS", LABEL)
    assert (23, "exact") in edge(SHOP, "CALLS", SLUGIFY)
    assert (27, "exact") in edge(SHOP, "CALLS", PRICE)
    assert (8, "exact") in edge(LABEL, "CALLS", PRICE)
    assert (13, "exact") in edge(SHOP, "RENDERS", BASE)
    assert (19, "exact") in edge(SHOP, "RENDERS", COUNTER)
    assert edge_attrs(SHOP, "RENDERS", COUNTER, 19).get("client") == "load"
    assert (20, "exact") in edge(SHOP, "RENDERS", TOGGLE)
    assert edge_attrs(SHOP, "RENDERS", TOGGLE, 20).get("client") == "visible"
    island = edge_attrs(SHOP, "RENDERS", COUNTER, 21)
    assert island.get("client") == "only" and island.get("client_value") == "react"
    assert island.get("branch_line") == 21
    assert "jsx" in (island.get("via") or [])
    assert (22, "exact") in edge(SHOP, "NAVIGATES_TO", SLUG)
    assert (23, "exact") in edge(SHOP, "NAVIGATES_TO", SLUG)
    nav = edge_attrs(SHOP, "NAVIGATES_TO", SLUG, 22)
    assert nav.get("via") in ("href", ["href"]) or "href" in (nav.get("via") or [])
    assert (25, "exact") in edge(SHOP, "USES_VALUE", ACCENT)
    # the external link and the quoted title are not code
    rows = db().execute("SELECT line, attrs FROM edges WHERE src=? AND kind='NAVIGATES_TO'", (SHOP,)).fetchall()
    assert all(r[0] != 24 for r in rows)
    assert not db().execute(
        "SELECT 1 FROM edges WHERE src=? AND file='src/pages/shop.astro' AND line=16 AND attrs LIKE '%not code%'", (SHOP,)
    ).fetchone()


@needs_ts
def test_odd_template_bits(tmp_path):
    from cg_code_graph.indexer import index_project
    root = tmp_path / "bits"
    files = {
        "package.json": '{"name": "bits", "dependencies": {"astro": "^5.0.0"}}',
        "src/components/Card.astro": "<div><slot /></div>\n",
        "src/pages/open.astro": "---\n---\n<p>{unclosed\n",
        "src/pages/cmt.astro": "---\n---\n<p>{/* comment */}</p>\n",
        "src/pages/defer.astro": "---\nimport Card from '../components/Card.astro'\n---\n<Card server:defer />\n",
        "src/pages/dvars.astro": "---\nconst x = 1\n---\n<script define:vars={{ x }}></script>\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    dbp = tmp_path / "bits.db"
    st = index_project(root, dbp, "bits")
    ts = {x["language"]: x for x in st["coverage"]["languages"]}["typescript"]
    assert "src/pages/open.astro" not in {s["file"] for s in ts.get("syntax_errors", [])}
    plug = st["plugins"]["typescript"]
    assert plug.get("astro_expr_dropped") or "src/pages/open.astro" not in {s["file"] for s in ts.get("syntax_errors", [])}
    c = sqlite3.connect(dbp)
    attrs = json.loads(c.execute(
        "SELECT attrs FROM edges WHERE src='page:src/pages/defer.astro' AND kind='RENDERS'"
    ).fetchone()[0])
    assert attrs.get("server") == "defer"
    page = json.loads(c.execute("SELECT attrs FROM nodes WHERE id='page:src/pages/dvars.astro'").fetchone()[0])
    assert page.get("define_vars") == ["x"]


TPL = {
    "package.json": '{"name": "tpl", "dependencies": {"astro": "^5.0.0"}}',
    "src/lib/u.ts": "".join(f"export function f{k}(): number {{ return {k} }}\n" for k in range(1, 7))
                    + "export function __tplHelper(): number { return 7 }\n",
    "src/components/Card.astro": "<div><slot /></div>\n",
    "src/components/Script.astro": "<div><slot /></div>\n",
    "src/pages/target.astro": "<p>target</p>\n",
    "src/pages/books/[slug].astro": "<p>book</p>\n",
    # nested JSX with an HTML comment in it; `<Script>` is a component; a self-closing <script> has no body;
    # is:raw children are text
    "src/pages/nested.astro": "---\n"
                              "import Card from '../components/Card.astro'\n"
                              "import Script from '../components/Script.astro'\n"
                              "import { f1, f2, f3, f4, f5, f6 } from '../lib/u'\n"
                              "const items = [1, 2]\n"
                              "---\n"
                              "{items.map((i) => (\n"
                              "  <Card>\n"
                              "    {i > 1 ? <b>{f1()}</b> : <i>{f2()}</i>}\n"
                              "  </Card>\n"
                              "))}\n"
                              "{items.length && <div><!-- note --><span>{f3()}</span></div>}\n"
                              "<p>&lt;{f4()}&gt; &amp; &#123;</p>\n"
                              "<pre is:raw>{f5()} not code</pre>\n"
                              "<Script />\n"
                              "<script src=\"/x.js\" />\n"
                              "<p>{f6()}</p>\n",
    # a user function whose name starts with __tpl is a node, in a .astro file and in a .ts file
    "src/pages/helper.astro": "---\nimport { __tplHelper } from '../lib/u'\n"
                              "function __tplLocal() { return __tplHelper() }\nconst z = __tplLocal()\n---\n<p>{z}</p>\n",
    # links inside expressions; a broken href expression is dropped without a syntax error
    "src/pages/links.astro": "---\nconst slugs = ['a']\n---\n"
                             "{slugs.map((s) => <a href={`/books/${s}`}>{s}</a>)}\n"
                             "{slugs.length > 0 && <a href=\"/target\">t</a>}\n"
                             "{slugs.length > 0 && <a href=\"https://example.com\">x</a>}\n"
                             "<a href={((}>broken</a>\n",
}


@needs_ts
def test_template_scanner_edge_cases(tmp_path):
    from cg_code_graph.indexer import index_project
    root = tmp_path / "tpl"
    for rel, text in TPL.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    dbp = tmp_path / "tpl.db"
    st = index_project(root, dbp, "tpl")
    c = sqlite3.connect(dbp)

    def e(src, kind, dst):
        return {r[0] for r in c.execute("SELECT line FROM edges WHERE src=? AND kind=? AND dst=?", (src, kind, dst))}

    def via(src, kind, dst, line):
        return json.loads(c.execute("SELECT attrs FROM edges WHERE src=? AND kind=? AND dst=? AND line=?",
                                    (src, kind, dst, line)).fetchone()[0] or "{}").get("via")

    P, U = "page:src/pages/nested.astro", "function:src/lib/u.ts#"
    assert e(P, "RENDERS", "component:src/components/Card.astro") == {8}
    assert "jsx" in via(P, "RENDERS", "component:src/components/Card.astro", 8)     # the whole .map() is one expression
    assert e(P, "USES_VALUE", "constant:src/pages/nested.astro#items") == {7, 12}  # the comment does not split line 12
    assert e(P, "CALLS", U + "f1") == {9} and e(P, "CALLS", U + "f2") == {9}
    assert e(P, "CALLS", U + "f3") == {12} and e(P, "CALLS", U + "f4") == {13}
    assert e(P, "CALLS", U + "f5") == set()                                        # is:raw
    assert e(P, "RENDERS", "component:src/components/Script.astro") == {15}
    assert e(P, "CALLS", U + "f6") == {17}                                         # after <script ... />
    H = "page:src/pages/helper.astro"
    assert e(H, "CALLS", "function:src/pages/helper.astro#__tplLocal") == {4}
    assert e("function:src/pages/helper.astro#__tplLocal", "CALLS", U + "__tplHelper") == {3}
    L = "page:src/pages/links.astro"
    assert e(L, "NAVIGATES_TO", "page:src/pages/books/[slug].astro") == {4}
    assert e(L, "NAVIGATES_TO", "page:src/pages/target.astro") == {5}
    assert {r[0] for r in c.execute("SELECT line FROM edges WHERE src=? AND kind='NAVIGATES_TO'", (L,))} == {4, 5}
    ts = {x["language"]: x for x in st["coverage"]["languages"]}["typescript"]
    assert not ts.get("syntax_errors"), ts.get("syntax_errors")

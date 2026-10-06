"""Astro server: astro.config, middleware, actions, redirect navigation (tests/astro_server_fixture)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.plugin import Project  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.plugins.astro.plugin import astro_route, read_astro_config  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

FX = ROOT / "tests" / "astro_server_fixture"
_S: dict = {}
needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")

INDEX = "page:app/pages/index.astro"
LOGIN = "page:app/pages/login.astro"
SLUG = "page:app/pages/books/[slug].astro"
MISS = "page:app/pages/404.astro"
ABOUT = "page:app/pages/ja/about.astro"
OLD = "page:astro:redirect:/shop/old-books"
LEGACY = "page:astro:redirect:/shop/legacy/{slug}"
API = "route:POST /shop/api/books"
ADD = "route:POST /shop/_actions/addBook"
REM = "route:POST /shop/_actions/removeBook"
CLEAR = "route:POST /shop/_actions/shelf.clear"
POST = "function:app/pages/api/books.ts#POST"
AUTH = "function:app/middleware.ts#auth"
LOG = "function:app/middleware.ts#log"
ADD_H = "function:app/actions/index.ts#server.addBook.handler"
REM_H = "function:app/actions/index.ts#server.removeBook.handler"
CLEAR_H = "function:app/actions/index.ts#server.shelf.clear.handler"
SAVE = "function:app/lib/books.ts#saveBook"
USER = "function:app/lib/auth.ts#currentUser"
PAGES = (INDEX, LOGIN, SLUG, MISS, ABOUT)
ROUTES = (API, ADD, REM, CLEAR)


def built() -> dict:
    if not _S:
        from cg_code_graph.indexer import index_project
        d = Path(tempfile.mkdtemp(prefix="codegraph-astro-server-"))
        db = d / "astro.db"
        _S["stats"] = index_project(FX, db, "astro-server")
        _S.update(dir=d, db=db)
    return _S


def db():
    return sqlite3.connect(built()["db"])


def node(nid):
    row = db().execute(
        "SELECT name, fqn, entry_kind, file, line, attrs FROM nodes WHERE id=?", (nid,)
    ).fetchone()
    assert row, nid
    return {"name": row[0], "fqn": row[1], "entry_kind": row[2], "file": row[3], "line": row[4],
            "attrs": json.loads(row[5] or "{}")}


def edges(src, kind, dst=None):
    if dst is None:
        rows = db().execute(
            "SELECT dst, line, confidence, attrs FROM edges WHERE src=? AND kind=?", (src, kind)
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT line, confidence, attrs FROM edges WHERE src=? AND kind=? AND dst=?", (src, kind, dst)
        ).fetchall()
    return [(r[0], r[1], json.loads(r[-1] or "{}")) if dst is None else (r[0], r[1], json.loads(r[2] or "{}"))
            for r in rows]


def test_read_astro_config(tmp_path):
    cfg = read_astro_config(FX)
    assert cfg["file"] == "astro.config.mjs"
    assert cfg["src_dir"] == "app"
    assert cfg["base"] == "/shop"
    assert cfg["trailing_slash"] == "never"
    assert cfg["redirects"] == {"/old-books": "/books/dune", "/legacy/[slug]": "/books/[slug]"}
    assert cfg["redirect_lines"] == {"/old-books": 8, "/legacy/[slug]": 9}
    assert cfg["locales"] == ["en", "ja"]
    assert cfg["default_locale"] == "en"
    assert cfg["prefix_default_locale"] is False

    (tmp_path / "astro.config.mjs").write_text(
        "export default defineConfig({\n"
        "  redirects: { '/old': { status: 302, destination: '/x' } },\n"
        "  i18n: { locales: [{ path: 'en', codes: ['en-US'] }, 'ja'], defaultLocale: 'ja',\n"
        "          routing: { prefixDefaultLocale: true } },\n"
        "})\n"
    )
    obj = read_astro_config(tmp_path)
    assert obj["redirects"] == {"/old": "/x"}
    assert obj["redirect_status"] == {"/old": 302}
    assert obj["locales"] == ["en", "ja"]
    assert obj["default_locale"] == "ja"
    assert obj["prefix_default_locale"] is True

    (tmp_path / "astro.config.mjs").write_text(
        "export default { srcDir: './site/', base: 'docs/', trailingSlash: 'always' }\n"
    )
    plain = read_astro_config(tmp_path)
    assert plain["src_dir"] == "site"
    assert plain["base"] == "/docs"
    assert plain["trailing_slash"] == "always"
    assert plain["locales"] is None

    (tmp_path / "astro.config.mjs").unlink()
    missing = read_astro_config(tmp_path)
    assert missing["file"] is None
    assert missing["src_dir"] == "src"
    assert missing["base"] == ""
    assert missing["trailing_slash"] is None
    assert missing["redirects"] == {}
    assert missing["locales"] is None
    assert missing["default_locale"] is None
    assert missing["prefix_default_locale"] is False


def test_astro_route_base_and_legacy():
    assert astro_route("app/pages/books/[slug].astro", "app", "/shop") == "/shop/books/{slug}"
    assert astro_route("app/pages/index.astro", "app", "/shop") == "/shop"
    assert astro_route("src/pages/index.astro") == "/"
    assert astro_route("src/pages/about.astro") == "/about"
    assert astro_route("src/pages/books/[slug].astro") == "/books/{slug}"
    assert astro_route("src/pages/docs/[...rest].astro") == "/docs/{rest*}"
    assert astro_route("src/pages/api/books.ts") == "/api/books"
    assert astro_route("src/pages/_draft.astro") is None


@needs_ts
def test_pages_routes_and_redirects():
    pages = {
        INDEX: ("/shop", "en"), LOGIN: ("/shop/login", "en"), SLUG: ("/shop/books/{slug}", "en"),
        MISS: ("/shop/404", "en"), ABOUT: ("/shop/ja/about", "ja"),
    }
    for nid, (route, loc) in pages.items():
        n = node(nid)
        assert n["name"] == route and n["fqn"] == route
        assert n["entry_kind"] == "ui_page"
        assert n["attrs"]["trailing_slash"] == "never"
        assert n["attrs"]["locale"] == loc
        assert n["attrs"]["framework"] == "astro"
    api = node(API)
    assert api["line"] == 4 and api["entry_kind"] == "http_route"
    assert edges(API, "ROUTES_TO", POST)
    for nid, line, accept, handler in (
        (ADD, 5, "form", ADD_H), (REM, 9, "json", REM_H), (CLEAR, 11, "json", CLEAR_H),
    ):
        n = node(nid)
        assert n["line"] == line
        assert n["attrs"]["accept"] == accept
        assert n["attrs"]["action"]
        assert edges(nid, "ROUTES_TO", handler)
    old, legacy = node(OLD), node(LEGACY)
    assert old["line"] == 8 and old["entry_kind"] is None
    assert old["attrs"]["redirect"] == "/shop/books/dune" and old["attrs"]["route"] == "/shop/old-books"
    assert legacy["line"] == 9 and legacy["attrs"]["redirect"] == "/shop/books/{slug}"
    assert legacy["attrs"]["route"] == "/shop/legacy/{slug}"
    assert edges(OLD, "NAVIGATES_TO", SLUG)
    assert edges(LEGACY, "NAVIGATES_TO", SLUG)


@needs_ts
def test_navigation_actions_and_middleware():
    def via(src, dst):
        rows = edges(src, "NAVIGATES_TO", dst)
        assert rows, (src, dst)
        return rows[0][0], rows[0][2].get("via")

    assert via(AUTH, LOGIN) == (5, "redirect")
    assert via(POST, SLUG) == (6, "redirect")
    assert via(SLUG, INDEX) == (3, "redirect")
    assert via(SLUG, MISS) == (4, "rewrite")
    assert via(INDEX, ABOUT) == (5, "i18n")

    def calls(src, dst):
        return [(line, a.get("how")) for line, _c, a in edges(src, "CALLS", dst)]

    assert (7, "form") in calls(INDEX, ADD_H)
    assert (15, "call") in calls(INDEX, CLEAR_H)
    assert all(line != 4 for line, _c, _a in edges(INDEX, "CALLS"))
    assert any(line == 7 for line, _c, _a in edges(ADD_H, "CALLS", SAVE))

    mw = db().execute("SELECT src, dst, attrs FROM edges WHERE kind='USES_MIDDLEWARE'").fetchall()
    assert len(mw) == 18
    by_src = {}
    for src, dst, raw in mw:
        by_src.setdefault(src, []).append((dst, json.loads(raw or {})))
    assert set(by_src) == set(PAGES + ROUTES)
    for src, rows in by_src.items():
        rows.sort(key=lambda r: r[1].get("order"))
        assert [(d, a.get("name"), a.get("order")) for d, a in rows] == [(AUTH, "auth", 0), (LOG, "log", 1)]
    assert not any(src.startswith("page:astro:redirect:") for src, _d, _a in mw)
    entries = {e["id"] for e in Q.impact(GraphStore(built()["db"]), "currentUser")["entry_points"]}
    assert INDEX in entries


@needs_ts
def test_coverage_counts():
    langs = {e["language"]: e for e in built()["stats"]["coverage"]["languages"]}
    ts = langs["typescript"]
    assert ts["discovered"] == 11
    assert ts["indexed"] == 10
    assert ts["excluded"] == 1
    assert ts["files_complete"]
    assert "astro.config.mjs" in ts.get("paths", {}).get("excluded", [])
    # `<a href={jaHome}>` is a variable: unresolved navigation, under a label that names Astro
    nav = [b for b in built()["stats"]["coverage"]["blind_spots"] if b["kind"] == "vue_unresolved_navigation"]
    assert nav and "Astro" in json.dumps(nav[0])


ROOT_PROJECT = {
    "package.json": '{"name": "root-astro", "private": true, "type": "module", "dependencies": {"astro": "^5.0.0"}}',
    "tsconfig.json": '{"compilerOptions": {"target": "es2022", "module": "esnext", "moduleResolution": "bundler", "strict": true}}',
    "astro.config.mjs": """import { defineConfig } from 'astro/config'
import react from '@astrojs/react'
// base: '/commented-out'
export default defineConfig({
  srcDir: '.',
  base: '/b/',
  integrations: [react(), sitemap({ filter: (p) => !p.includes('x') })],
  vite: { plugins: [tailwind()] },
  redirects: {
    '/gone': 'https://example.com/elsewhere',
    '/cdn': '//cdn.example.com/x',
    '/moved': '/about',
  },
  i18n: { locales: ['en', 'ja'], defaultLocale: 'en' },
})
""",
    "lib/mw.ts": """import { defineMiddleware } from 'astro:middleware'
export const guard = defineMiddleware(async (_c, next) => next())
""",
    "middleware.ts": "import { guard } from './lib/mw'\nexport const onRequest = guard\n",
    "lib/save.ts": """export async function saveNote(input: { text: string }) { return input.text }
export function getRelativeLocaleUrl(locale: string, path: string) { return `/${locale}${path}` }
""",
    "actions/index.ts": """import { defineAction } from 'astro:actions'
import { saveNote } from '../lib/save'
async function wipe() { return 0 }
export const server = {
  note: defineAction({ accept: 'form', handler: saveNote }),
  shelf: { clear: defineAction({ handler: wipe }) },
}
""",
    "components/NoteForm.astro": "<form method=\"POST\" action={actions.note}><input name=\"text\" /></form>\n",
    "pages/index.astro": """---
import NoteForm from '../components/NoteForm.astro'
import { getRelativeLocaleUrl } from '../lib/save'
const local = getRelativeLocaleUrl('ja', '/about')
---
<NoteForm />
<p>{local}</p>
""",
    "pages/about.astro": """---
import { getRelativeLocaleUrl } from 'astro:i18n'
const home = getRelativeLocaleUrl('en', '/')
---
<a href={home}>home</a>
""",
    "pages/ja/about.astro": "<h1>About</h1>\n",
    "pages/api/ping.ts": "export const GET = () => new Response('ok')\n",
}


def test_config_edge_cases(tmp_path):
    for name, text in ROOT_PROJECT.items():
        if name == "astro.config.mjs":
            (tmp_path / name).write_text(text)
    cfg = read_astro_config(tmp_path)
    assert cfg["src_dir"] == ""            # srcDir '.' is the project root
    assert cfg["base"] == "/b"             # trailing slash dropped; the commented-out base is ignored
    assert cfg["redirects"]["/gone"] == "https://example.com/elsewhere"
    assert astro_route("pages/about.astro", "", "/b") == "/b/about"
    assert astro_route("pages/index.astro", "", "/b") == "/b"
    assert astro_route("src/pages/index.astro", "", "/b") is None
    for src_dir, want in (("./", ""), ("./app/", "app"), ("app", "app")):
        (tmp_path / "astro.config.mjs").write_text(f"export default {{ srcDir: '{src_dir}' }}\n")
        assert read_astro_config(tmp_path)["src_dir"] == want, src_dir
    # computed or unparseable values keep the defaults, and nothing raises
    for text in (
        "const base = process.env.BASE\nexport default defineConfig({ base, srcDir: path.join('a', 'b') })\n",
        "export default defineConfig(({ mode }) => ({ base: '/fn' }))\n",
        "export default defineConfig({ base: `/${x}`, redirects: { [k]: '/z', '/d': dest } })\n",
        "export default defineConfig({ redirects: { '/a': \n",
        "\x00 }}}{{{ ((( '\n",
        "",
    ):
        (tmp_path / "astro.config.mjs").write_text(text)
        got = read_astro_config(tmp_path)
        assert got["src_dir"] == "src" and got["base"] == "" and got["redirects"] == {}, text
    (tmp_path / "astro.config.mjs").write_bytes(b"export default { base: '/caf\xe9' }\n")
    assert read_astro_config(tmp_path)["src_dir"] == "src"


@needs_ts
def test_root_src_dir_project(tmp_path):
    from cg_code_graph.indexer import index_project
    proj = tmp_path / "proj"
    for name, text in ROOT_PROJECT.items():
        (proj / name).parent.mkdir(parents=True, exist_ok=True)
        (proj / name).write_text(text)
    dbp = tmp_path / "g.db"
    index_project(proj, dbp, "root-astro")
    con = sqlite3.connect(dbp)
    ids = {r[0] for r in con.execute("SELECT id FROM nodes")}
    rows = con.execute("SELECT src, kind, dst, file, line FROM edges").fetchall()

    def out(src, kind):
        return {(r[2], r[3], r[4]) for r in rows if r[0] == src and r[1] == kind}

    assert {"page:pages/index.astro", "page:pages/about.astro", "route:GET /b/api/ping",
            "route:POST /b/_actions/note", "route:POST /b/_actions/shelf.clear"} <= ids
    # an external redirect is a page, but it navigates nowhere in this project
    gone = json.loads(con.execute("SELECT attrs FROM nodes WHERE id='page:astro:redirect:/b/gone'").fetchone()[0])
    assert gone["redirect"] == "https://example.com/elsewhere"
    cdn = json.loads(con.execute("SELECT attrs FROM nodes WHERE id='page:astro:redirect:/b/cdn'").fetchone()[0])
    assert cdn["redirect"] == "//cdn.example.com/x"
    assert not out("page:astro:redirect:/b/gone", "NAVIGATES_TO")
    assert not out("page:astro:redirect:/b/cdn", "NAVIGATES_TO")
    assert out("page:astro:redirect:/b/moved", "NAVIGATES_TO") == {("page:pages/about.astro", "astro.config.mjs", 12)}
    # named handlers, nested or not
    assert out("route:POST /b/_actions/note", "ROUTES_TO") == {("function:lib/save.ts#saveNote", "actions/index.ts", 5)}
    assert out("route:POST /b/_actions/shelf.clear", "ROUTES_TO") == {("function:actions/index.ts#wipe", "actions/index.ts", 6)}
    # a form in a component reaches the action handler
    assert ("function:lib/save.ts#saveNote", "components/NoteForm.astro", 1) in out("component:components/NoteForm.astro", "CALLS")
    # a single defineMiddleware imported into middleware.ts: the edge points at the function's own file
    mw = ("function:lib/mw.ts#guard", "lib/mw.ts", 2)
    for src in ("page:pages/index.astro", "route:GET /b/api/ping", "route:POST /b/_actions/note"):
        assert out(src, "USES_MIDDLEWARE") == {mw}, src
    # getRelativeLocaleUrl from astro:i18n navigates; a local function of the same name does not
    assert ("page:pages/index.astro", "pages/about.astro", 3) in out("page:pages/about.astro", "NAVIGATES_TO")
    assert not {d for d, _f, _l in out("page:pages/index.astro", "NAVIGATES_TO")} & {"page:pages/ja/about.astro"}

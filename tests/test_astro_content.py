"""Astro Markdown pages and content collections (tests/astro_content_fixture)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.plugins.astro.content import frontmatter, read_collections  # noqa: E402
from codegraph.plugins.astro.plugin import astro_route, read_astro_config  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

FX = ROOT / "tests" / "astro_content_fixture"
_S: dict = {}
needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")


def built() -> dict:
    if not _S:
        from codegraph.indexer import index_project
        d = Path(tempfile.mkdtemp(prefix="codegraph-astro-content-"))
        db = d / "astro.db"
        _S["stats"] = index_project(FX, db, "astro-content")
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
        "SELECT line, confidence, attrs FROM edges WHERE src=? AND kind=? AND dst=?", (src, kind, dst)
    ).fetchall()


def test_astro_route_markdown():
    assert astro_route("src/pages/about.md") == "/about"
    assert astro_route("src/pages/docs/guide.mdx") == "/docs/guide"
    assert astro_route("src/pages/about.markdown") == "/about"
    assert astro_route("src/pages/index.html") == "/"
    assert astro_route("src/pages/_draft.md") is None


def test_frontmatter_quotes_crlf_missing():
    assert frontmatter('---\r\ntitle: "About"\r\nlayout: \'../layouts/Base.astro\'\r\n---\r\n') == {
        "title": "About", "layout": "../layouts/Base.astro",
    }
    assert frontmatter("hello\n") == {}
    assert frontmatter("---\ntitle: Plain\n---\n")["title"] == "Plain"


def test_read_collections_fixture_legacy_custom(tmp_path):
    cols = {c["name"]: c for c in read_collections(FX)}
    assert cols["blog"]["loader"] == "glob"
    assert cols["blog"]["pattern"] == "**/*.{md,mdx}"
    assert cols["blog"]["base"] == "src/data/blog"
    assert cols["blog"]["entries"] == 2
    assert cols["blog"]["relations"][0][0] == "authors"
    assert cols["authors"]["loader"] == "file"
    assert cols["authors"]["path"] == "src/data/authors.json"
    legacy = tmp_path / "src" / "content"
    (legacy / "notes").mkdir(parents=True)
    (legacy / "config.ts").write_text(
        "const notes = defineCollection({ type: 'content' })\nexport const collections = { notes }\n",
        encoding="utf-8",
    )
    (legacy / "notes" / "a.md").write_text("# a\n", encoding="utf-8")
    (legacy / "notes" / "b.md").write_text("# b\n", encoding="utf-8")
    got = read_collections(tmp_path)
    assert len(got) == 1 and got[0]["base"] == "src/content/notes" and got[0]["entries"] == 2
    custom = tmp_path / "other"
    (custom / "src").mkdir(parents=True)
    (custom / "src" / "content.config.ts").write_text(
        "const docs = defineCollection({ loader: loadRemote() })\nexport const collections = { docs }\n",
        encoding="utf-8",
    )
    one = read_collections(custom)[0]
    assert one["name"] == "docs" and one["loader"] == "loadRemote"


@needs_ts
def test_markdown_pages_and_collections():
    about = node("page:src/pages/about.md")
    assert about["name"] == "/about" and about["entry_kind"] == "ui_page"
    assert about["attrs"]["markdown"] == "md" and about["attrs"]["title"] == "About"
    assert about["attrs"]["layout"] == "src/layouts/Base.astro"
    er = edge("page:src/pages/about.md", "RENDERS", "layout:src/layouts/Base.astro")
    assert er and er[0][0] == 2 and er[0][1] == "exact" and json.loads(er[0][2] or "{}").get("via") == ["layout"]

    guide = node("page:src/pages/guide.mdx")
    assert guide["name"] == "/guide" and guide["attrs"]["markdown"] == "mdx"
    assert edge("page:src/pages/guide.mdx", "RENDERS", "layout:src/layouts/Base.astro")[0][0] == 2
    assert edge("page:src/pages/guide.mdx", "IMPORTS", "component:src/components/Note.astro")[0][0] == 5
    assert edge("page:src/pages/guide.mdx", "IMPORTS", "module:src/lib/dates.ts")[0][0] == 6
    note = edge("page:src/pages/guide.mdx", "RENDERS", "component:src/components/Note.astro")
    assert note and note[0][0] == 10 and note[0][1] == "exact"
    call = edge("page:src/pages/guide.mdx", "CALLS", "function:src/lib/dates.ts#formatDate")
    assert call and call[0][0] == 10 and call[0][1] == "resolved"
    late = db().execute(
        "SELECT COUNT(*) FROM edges WHERE src=? AND line=13", ("page:src/pages/guide.mdx",)
    ).fetchone()[0]
    assert late == 0

    blog = node("table:content:blog")
    assert blog["attrs"]["loader"] == "glob" and blog["attrs"]["entries"] == 2
    assert blog["attrs"]["pattern"] == "**/*.{md,mdx}" and blog["attrs"]["base"] == "src/data/blog"
    assert blog["attrs"]["collection"] is True
    row = db().execute("SELECT line FROM nodes WHERE id=?", ("table:content:blog",)).fetchone()
    assert row[0] == 4
    authors = node("table:content:authors")
    assert authors["attrs"]["loader"] == "file" and authors["attrs"]["path"] == "src/data/authors.json"
    arow = db().execute("SELECT line FROM nodes WHERE id=?", ("table:content:authors",)).fetchone()
    assert arow[0] == 9
    rel = edge("table:content:blog", "HAS_RELATION", "table:content:authors")
    assert rel and rel[0][0] == 6 and rel[0][1] == "exact"

    def reads(src, dst):
        return edge(src, "READS_TABLE", dst)

    assert reads("function:src/lib/posts.ts#recentPosts", "table:content:blog")[0][0] == 4
    assert reads("function:src/pages/blog/[...slug].astro#getStaticPaths", "table:content:blog")[0][0] == 5
    rend = reads("page:src/pages/blog/[...slug].astro", "table:content:blog")
    assert rend and rend[0][0] == 9 and rend[0][1] == "resolved"
    assert json.loads(rend[0][2] or "{}").get("via") == "render"
    assert reads("page:src/pages/index.astro", "table:content:authors")[0][0] == 4
    names = [r[0] for r in db().execute("SELECT name FROM nodes WHERE kind='page' ORDER BY name").fetchall()]
    assert names == ["/", "/about", "/blog/{slug*}", "/guide"]
    cov = {e["language"]: e for e in built()["stats"]["coverage"]["languages"]}["typescript"]
    assert cov["discovered"] == 8 and cov["indexed"] == 7 and cov["excluded"] == 1


@needs_ts
def test_local_content_calls_and_src_dir_root(tmp_path):
    local = tmp_path / "local"
    (local / "src" / "lib").mkdir(parents=True)
    (local / "src" / "pages").mkdir()
    (local / "package.json").write_text('{"name":"l","private":true,"dependencies":{"astro":"^5.0.0"}}\n')
    (local / "tsconfig.json").write_text('{"compilerOptions":{"strict":true},"include":["src"]}\n')
    (local / "astro.config.mjs").write_text("export default { integrations: [] }\n")
    (local / "src" / "content.config.ts").write_text(
        "const blog = defineCollection({ loader: file('src/data/authors.json') })\n"
        "export const collections = { blog }\n"
    )
    (local / "src" / "lib" / "local.ts").write_text(
        "export function getCollection(n: string) { return [] }\nexport function render(x: unknown) { return x }\n"
    )
    (local / "src" / "pages" / "index.astro").write_text(
        "---\nimport { getCollection, render } from '../lib/local'\n"
        "const posts = await getCollection('blog')\nconst view = await render(posts)\n---\n<p>{view}</p>\n"
    )
    from codegraph.indexer import index_project
    dbp = local / "g.db"
    index_project(local, dbp, "local")
    con = sqlite3.connect(dbp)
    assert con.execute("SELECT COUNT(*) FROM edges WHERE kind='READS_TABLE'").fetchone()[0] == 0

    rootish = tmp_path / "rootish"
    (rootish / "pages").mkdir(parents=True)
    (rootish / "package.json").write_text('{"name":"r","private":true,"dependencies":{"astro":"^5.0.0"}}\n')
    (rootish / "tsconfig.json").write_text('{"compilerOptions":{"strict":true},"include":["."]}\n')
    (rootish / "astro.config.mjs").write_text(
        "import { defineConfig } from 'astro/config'\nexport default defineConfig({ srcDir: '.' })\n"
    )
    (rootish / "pages" / "about.md").write_text("---\ntitle: About\n---\n# About\n")
    assert read_astro_config(rootish)["src_dir"] == ""
    dbp = rootish / "g.db"
    index_project(rootish, dbp, "rootish")
    con = sqlite3.connect(dbp)
    row = con.execute("SELECT name FROM nodes WHERE id=?", ("page:pages/about.md",)).fetchone()
    assert row and row[0] == "/about"


def test_frontmatter_odd_blocks():
    # TOML (Astro 5.2+): top-level keys only, a [table] ends them
    toml = '+++\ntitle = "Toml page"\nlayout = \'/src/layouts/Base.astro\'\n[seo]\ntitle = "nested"\n+++\n# T\n'
    assert frontmatter(toml) == {"title": "Toml page", "layout": "/src/layouts/Base.astro"}
    # nested YAML is not top level; trailing comments and quoted colons; blank lines before the fence
    yaml = '\n\n---\nseo:\n  title: nested\nlayout: ../x.astro # the doc layout\ntitle: "About: us"\n---\n'
    assert frontmatter(yaml) == {"seo": "", "layout": "../x.astro", "title": "About: us"}
    # an unclosed block is not frontmatter (Astro rejects it); neither is a fence after content
    assert frontmatter("---\nlayout: ../x.astro\ntitle: Broken\n") == {}
    assert frontmatter("# Title\n---\nlayout: ../x.astro\n---\n") == {}
    assert frontmatter("+++\ntitle = 'x'\n---\n") == {}


def test_read_collections_inline_comments_legacy_default(tmp_path):
    content = tmp_path / "app" / "content"
    for sub, files in (("notes", ("a.md", "b.mdx", "c.md.txt")), ("people", ("ada.json", "README.md"))):
        (content / sub).mkdir(parents=True)
        for f in files:
            (content / sub / f).write_text("x\n", encoding="utf-8")
    (content / "config.ts").write_text(
        "import { defineCollection, reference, z } from 'astro:content'\n"
        "// const ghost = defineCollection({ type: 'data' })\n"
        "/* export const collections = { ghost } */\n"
        "const notes = defineCollection({\n"
        "  schema: z.object({ title: z.string(), by: reference('people') }),\n"
        "})\n"
        "export const collections = {\n"
        "  notes,\n"
        "  people: defineCollection({ type: 'data', schema: z.object({ name: z.string() }) }),\n"
        "  'live-feed': defineCollection({ loader: async () => [{ id: 'a' }] }),\n"
        "}\n",
        encoding="utf-8",
    )
    cols = {c["name"]: c for c in read_collections(tmp_path, "app")}
    assert set(cols) == {"notes", "people", "live-feed"}
    # a legacy collection with no type is `content`; entries count only entry files
    assert (cols["notes"]["type"], cols["notes"]["base"], cols["notes"]["entries"]) == ("content", "app/content/notes", 2)
    assert cols["notes"]["line"] == 4 and cols["notes"]["relations"] == [("people", 5)]
    assert (cols["people"]["type"], cols["people"]["entries"], cols["people"]["line"]) == ("data", 1, 9)
    assert cols["live-feed"]["loader"] == "custom"
    # inline collections in a Starlight-style config (nested braces in the object)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "content.config.ts").write_text(
        "import { docsLoader } from '@astrojs/starlight/loaders'\n"
        "import { docsSchema } from '@astrojs/starlight/schema'\n"
        "export const collections = {\n"
        "  docs: defineCollection({ loader: docsLoader(), schema: docsSchema({ extend: z.object({ a: z.string() }) }) }),\n"
        "}\n",
        encoding="utf-8",
    )
    star = read_collections(tmp_path)
    assert [(c["name"], c["loader"], c["line"]) for c in star] == [("docs", "docsLoader", 4)]


EDGE = {
    "package.json": '{"name": "c-edge", "private": true, "type": "module", "dependencies": {"astro": "^5.0.0"}}',
    "tsconfig.json": '{"compilerOptions": {"target": "es2022", "module": "esnext", "moduleResolution": "bundler", "strict": true}, "include": ["app"]}',
    "astro.config.mjs": "export default defineConfig({ srcDir: './app', base: '/docs/', i18n: { locales: ['en', 'ja'], defaultLocale: 'en' } })\n",
    "app/content.config.ts": """import { defineCollection, reference, z } from 'astro:content'
import { glob } from 'astro/loaders'
export const collections = {
  notes: defineCollection({ loader: glob({ pattern: '*.md', base: './app/notes' }), schema: z.object({ by: reference('people') }) }),
  people: defineCollection({ loader: glob({ pattern: '*.json', base: './app/people' }) }),
}
""",
    "app/notes/a.md": "---\ntitle: A\n---\nA\n",
    "app/notes/b.md": "---\ntitle: B\n---\nB\n",
    "app/people/ada.json": '{"name": "Ada"}',
    "app/layouts/Doc.astro": "---\nconst { title } = Astro.props\n---\n<main><slot /></main>\n",
    "app/components/Note.astro": "---\nconst { text } = Astro.props\n---\n<aside>{text}</aside>\n",
    "app/lib/util.ts": "export function Card(p: { t: string }) { return p.t }\nexport function slug(s: string) { return s }\n",
    "app/lib/data.ts": """import { getEntry, getEntries } from 'astro:content'
export async function pair() {
  return getEntries([{ collection: 'notes', id: 'a' }, { collection: 'notes', id: 'b' }])
}
export async function person() {
  return getEntry({ collection: 'people', id: 'ada' })
}
""",
    "app/middleware.ts": "import { defineMiddleware } from 'astro:middleware'\nexport const onRequest = defineMiddleware((_c, next) => next())\n",
    "app/pages/index.astro": """---
import { getCollection, render } from 'astro:content'
const notes = await getCollection('notes')
const { Content } = await render(notes[0])
---
<Content />
""",
    "app/pages/toml.md": '+++\ntitle = "Toml page"\nlayout = "/app/layouts/Doc.astro"\n+++\n# T\n\nSee [about](/about#top), [ext](https://x.com) and `[code](/nope)`.\n',
    "app/pages/about.md": '---\nseo:\n  title: nested\nlayout: ../layouts/Doc.astro # the layout\ntitle: "About: us"\n---\n# About\n<a href="/toml">t</a>\n',
    "app/pages/broken.md": "---\nlayout: ../layouts/Doc.astro\ntitle: Broken\n",
    "app/pages/plain.markdown": "# Plain\n",
    "app/pages/_draft.md": "# Draft\n",
    "app/pages/notes/_wip/x.md": "# wip\n",
    "app/pages/ja/index.md": "# ja\n",
    "app/pages/static.html": "<h1>static</h1>\n",
    "docs/readme.md": "# not a page\n",
    "app/pages/guide.mdx": """---
layout: ../layouts/Doc.astro
---
import Note from '../components/Note.astro'
import {
  Card,
  slug as mk,
} from '../lib/util'
export const meta = { s: mk('x') }

# Guide

<Note text={mk('y')} />
<Card t="z" />

Inline `mk(1)` is code.

~~~ts
mk(2)
~~~
""",
}


@needs_ts
def test_markdown_and_collection_edge_cases(tmp_path):
    from codegraph.indexer import index_project
    proj = tmp_path / "proj"
    for name, text in EDGE.items():
        (proj / name).parent.mkdir(parents=True, exist_ok=True)
        (proj / name).write_text(text, encoding="utf-8")
    (proj / "app/pages/latin.md").write_bytes(b"---\ntitle: caf\xe9\nlayout: ../layouts/Doc.astro\n---\n# caf\xe9\n")
    dbp = tmp_path / "g.db"
    index_project(proj, dbp, "c-edge")
    con = sqlite3.connect(dbp)
    nodes = {r[0]: (r[1], json.loads(r[2] or "{}")) for r in con.execute("SELECT id, name, attrs FROM nodes")}
    rows = con.execute("SELECT src, kind, dst, line, confidence FROM edges").fetchall()

    def out(src, kind):
        return {(r[2], r[3], r[4]) for r in rows if r[0] == src and r[1] == kind}

    md = {k: v for k, v in nodes.items() if k.startswith("page:") and v[1].get("markdown")}
    # srcDir and base apply; `_` segments and files outside pages/ are not pages; .html is a page
    assert {k: v[0] for k, v in md.items()} == {
        "page:app/pages/about.md": "/docs/about", "page:app/pages/broken.md": "/docs/broken",
        "page:app/pages/guide.mdx": "/docs/guide", "page:app/pages/ja/index.md": "/docs/ja",
        "page:app/pages/latin.md": "/docs/latin", "page:app/pages/plain.markdown": "/docs/plain",
        "page:app/pages/static.html": "/docs/static", "page:app/pages/toml.md": "/docs/toml",
    }
    assert md["page:app/pages/ja/index.md"][1]["locale"] == "ja"
    assert md["page:app/pages/about.md"][1]["title"] == "About: us"
    assert md["page:app/pages/toml.md"][1]["title"] == "Toml page"
    assert "layout" not in md["page:app/pages/broken.md"][1]
    doc = "layout:app/layouts/Doc.astro"
    assert out("page:app/pages/about.md", "RENDERS") == {(doc, 4, "exact")}
    assert out("page:app/pages/toml.md", "RENDERS") == {(doc, 3, "exact")}      # root-relative layout
    assert out("page:app/pages/latin.md", "RENDERS") == {(doc, 3, "exact")}     # non-UTF-8 file
    assert not out("page:app/pages/broken.md", "RENDERS")
    # Markdown links (code spans and external links are not)
    assert out("page:app/pages/toml.md", "NAVIGATES_TO") == {("page:app/pages/about.md", 7, "exact")}
    assert out("page:app/pages/about.md", "NAVIGATES_TO") == {("page:app/pages/toml.md", 8, "exact")}
    # MDX: multi-line import, `export` code, a named component export; no edge from inline code or a ~~~ fence
    g = "page:app/pages/guide.mdx"
    assert out(g, "IMPORTS") == {("component:app/components/Note.astro", 4, "exact"), ("module:app/lib/util.ts", 5, "exact")}
    assert out(g, "CALLS") == {("function:app/lib/util.ts#slug", 9, "resolved"), ("function:app/lib/util.ts#slug", 13, "resolved")}
    assert out(g, "RENDERS") == {(doc, 2, "exact"), ("component:app/components/Note.astro", 13, "exact"),
                                 ("function:app/lib/util.ts#Card", 14, "resolved")}
    # collections: object-form getEntry / getEntries, render after a read, reference()
    notes, people = "table:content:notes", "table:content:people"
    assert nodes[notes][1]["entries"] == 2 and nodes[notes][1]["base"] == "app/notes"
    assert out(notes, "HAS_RELATION") == {(people, 4, "exact")}
    assert out("function:app/lib/data.ts#pair", "READS_TABLE") == {(notes, 3, "exact")}
    assert out("function:app/lib/data.ts#person", "READS_TABLE") == {(people, 6, "exact")}
    assert out("page:app/pages/index.astro", "READS_TABLE") == {(notes, 3, "exact"), (notes, 4, "resolved")}
    # middleware covers Markdown pages too
    for p in md:
        assert out(p, "USES_MIDDLEWARE"), p

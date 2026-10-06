"""Nuxt app with its source at the repo root, no generated .nuxt/ (clean checkout), base URLs from runtime config
and env, dangling symlinks (tests/nuxt_rootsrc_fixture, linked to the task-board API in tests/broadcast_fixture/api);
plus api-calls globs and coverage on linked graphs."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph import query as Q, coverage as C  # noqa: E402
from cg_code_graph.link import match_endpoint  # noqa: E402
from cg_code_graph.plugins.nuxt.plugin import find_src_dir, nuxt_route, _component_name  # noqa: E402
from cg_code_graph.plugins.ts.baseurl import ConfigValues, base_path, parse_env, runtime_config_defaults  # noqa: E402
from sample import EXTRACTOR_DEPS, needs_php  # noqa: E402

FX = ROOT / "tests" / "nuxt_rootsrc_fixture"
API = ROOT / "tests" / "broadcast_fixture" / "api"
_S: dict = {}
needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")


def built() -> dict:
    if not _S:
        from cg_code_graph.indexer import index_project
        from cg_code_graph.link import link
        d = Path(tempfile.mkdtemp(prefix="codegraph-rootsrc-"))
        _S["web_stats"] = index_project(FX, d / "web.db", "board-web")
        index_project(API, d / "api.db", "board-api")
        _S["link"] = link(str(d / "api.db"), str(d / "web.db"), str(d / "combined.db"), backend_name="board-api",
                          frontend_name="board-web")
        _S.update(dir=d, web=d / "web.db", combined=d / "combined.db")
    return _S


def q(sql, db="web", *args):
    return sqlite3.connect(built()[db]).execute(sql, args).fetchall()


# ---------------------------------------------------------------- pure helpers

def test_src_dir_and_routes(tmp_path):
    assert find_src_dir(FX) == "."
    assert find_src_dir(ROOT / "examples" / "bookstore-web") == "app"
    (tmp_path / "src" / "pages").mkdir(parents=True)
    assert find_src_dir(tmp_path) == "src"
    (tmp_path / "nuxt.config.ts").write_text("export default defineNuxtConfig({ srcDir: 'client/' })")
    (tmp_path / "client").mkdir()
    assert find_src_dir(tmp_path) == "client"
    assert nuxt_route("pages/boards/[id].vue", ".") == "/boards/:id"
    assert nuxt_route("src/pages/index.vue", "src") == "/"
    assert _component_name(["task", "Card.vue"]) == "TaskCard"
    assert _component_name(["base", "BaseButton.vue"]) == "BaseButton"
    assert _component_name(["form", "input", "index.vue"]) == "FormInput"
    assert _component_name(["list", "Lists.vue"]) == "ListLists"                      # word prefixes, not string prefixes
    assert _component_name(["common", "dropdown", "CommonDropdownItem.vue"]) == "CommonDropdownItem"
    assert _component_name(["pwa", "PwaBadge.client.vue"]) == "PwaBadge"


def test_config_values(tmp_path):
    assert parse_env("A=1\nexport B='x y'\n# c\nC=http://h/api # note\n") == {"A": "1", "B": "x y", "C": "http://h/api"}
    rc = runtime_config_defaults(FX)
    assert rc["apiBase"][0] == ["NUXT_PUBLIC_API_BASE"] and rc["apiBase"][1] == "http://localhost:8000/api"
    cv = ConfigValues(FX)
    assert cv.lookup("runtimeConfig.apiBase")[0] == "http://localhost:8000/api"
    assert cv.lookup("env.VITE_STATUS_URL") == ("https://status.example.com/v2", ".env.example")
    assert cv.lookup("runtimeConfig.mapsKey") == ("", "nuxt.config.ts:7") or cv.lookup("runtimeConfig.mapsKey") is None
    # a real .env wins over the default in code (Nuxt's NUXT_PUBLIC_* override)
    shutil.copy(FX / "nuxt.config.ts", tmp_path / "nuxt.config.ts")
    (tmp_path / ".env").write_text("NUXT_PUBLIC_API_BASE=https://api.example.com/api/v3\n")
    assert ConfigValues(tmp_path).lookup("runtimeConfig.apiBase") == ("https://api.example.com/api/v3", ".env")
    assert base_path("http://localhost:8000/api/v1/") == "/api/v1" and base_path("/api") == "/api"
    assert base_path("http://host") == "" and base_path("{x}") is None


def test_match_falls_back_without_configured_base():
    routes = [{"id": "route:GET /v1/items", "uri": "/v1/items", "method": "GET", "uris": [("as-declared", "/v1/items")]}]
    res = match_endpoint("GET", "/api/v1/items", routes, "env", "{runtimeConfig.apiBase}", base_prefix="/api")
    assert res["matched"][0]["confidence"] == "heuristic" and "without configured base /api" in res["matched"][0]["uri_variant"]


# ---------------------------------------------------------------- the fixture

@needs_ts
def test_root_src_dir_without_nuxt_folder():
    st = built()["web_stats"]["plugins"]["typescript/nuxt"]
    assert st["prepared"] == "generated" and st["src_dir"] == "." and st["pages"] == 2 and st["layouts"] == 1
    kinds = dict(q("SELECT id, kind FROM nodes WHERE kind IN ('page','layout','component','composable','app')"))
    assert kinds == {"page:pages/boards/[id].vue": "page", "page:pages/index.vue": "page", "layout:layouts/default.vue": "layout",
                     "component:components/task/Card.vue": "component", "app:app.vue": "app",
                     "component:components/PwaBadge.client.vue": "component",
                     "composable:composables/useTasks.ts#useTasks": "composable",
                     "composable:composables/useSession.ts#useSession": "composable",
                     "composable:composables/useSession.ts#useSessionFetch": "composable",
                     "composable:composables/useApiBase.ts#useApiBase": "composable"}
    assert q("SELECT name FROM nodes WHERE id='page:pages/boards/[id].vue'")[0][0] == "/boards/:id"
    # generated auto-imports and global components resolve like `nuxi prepare` output
    e = {(s, d, k) for s, d, k in q("SELECT src, dst, kind FROM edges WHERE kind IN ('USES_COMPOSABLE','RENDERS','CALLS')")}
    assert ("page:pages/boards/[id].vue", "composable:composables/useTasks.ts#useTasks", "USES_COMPOSABLE") in e
    assert ("page:pages/boards/[id].vue", "component:components/task/Card.vue", "RENDERS") in e
    assert ("component:components/task/Card.vue", "function:utils/text.ts#taskTitle", "CALLS") in e
    # Foo.client.vue registers as <Foo>
    assert ("layout:layouts/default.vue", "component:components/PwaBadge.client.vue", "RENDERS") in e


@needs_ts
def test_dangling_symlinks_are_skipped_not_fatal():
    st = built()["web_stats"]["plugins"]["typescript"]
    assert st["skipped_dangling_symlinks"] == ["notes/meeting-notes.md", "utils/legacy-format.ts"]
    assert built()["web_stats"]["nodes"] > 20


@needs_ts
@needs_php
def test_base_urls_from_runtime_config_and_env():
    eps = {r[0]: json.loads(r[1]) for r in q("SELECT id, attrs FROM nodes WHERE kind='http'")}
    assert set(eps) == {"http:GET /api/boards/{id}/tasks", "http:PATCH /api/tasks/{id}/move",
                        "http:POST /api/tasks/{taskId}/comments", "http:GET /v2/summary.json",
                        "http:POST /api/tasks/{taskId}/archive"}
    # an auto-imported `$fetch.create` instance (utils/session.ts, called without an import statement)
    assert eps["http:POST /api/tasks/{taskId}/archive"]["origin_kind"] == "api"
    # useFetch(url) inside a wrapper without callers names no endpoint (no `GET /`)
    assert built()["web_stats"]["plugins"]["typescript"]["http_url_unknown"] == 1
    # `${config.public.apiBase}` via a const, axios.create({ baseURL: computed(...).value }), a $fetch.create factory
    for k in ("http:GET /api/boards/{id}/tasks", "http:PATCH /api/tasks/{id}/move", "http:POST /api/tasks/{taskId}/comments"):
        assert eps[k]["base"] == {"placeholder": "{runtimeConfig.apiBase}", "value": "http://localhost:8000/api", "from": "nuxt.config.ts:6"}
        assert eps[k]["origin_kind"] == "env"
    assert eps["http:GET /v2/summary.json"]["base"]["from"] == ".env.example"
    stats = built()["link"]["stats"]
    assert (stats["endpoints_matched"], stats["endpoints"]) == (3, 5)      # /tasks/{id}/archive has no route
    m = dict(q("SELECT src, dst FROM edges WHERE kind='MATCHES_ROUTE'", "combined"))
    assert m["http:PATCH /api/tasks/{id}/move"] == "route:PATCH /tasks/{task}/move"
    assert m["http:POST /api/tasks/{taskId}/comments"] == "route:POST /tasks/{task}/comments"


@needs_ts
@needs_php
def test_api_calls_globs():
    st = GraphStore(built()["combined"])
    ids = lambda f: [r["endpoint"] for r in Q.api_calls(st, f)]
    assert ids("GET /api/*/tasks") == ["http:GET /api/boards/{id}/tasks"]
    assert ids("*/tasks/*") == ["http:PATCH /api/tasks/{id}/move", "http:POST /api/tasks/{taskId}/archive",
                                "http:POST /api/tasks/{taskId}/comments"]
    assert ids("PATCH *") == ["http:PATCH /api/tasks/{id}/move"]
    assert ids("*TaskController::index") == ["http:GET /api/boards/{id}/tasks"]
    assert len(ids("*composables/useTasks.ts")) == 4                          # call sites or the helper they go through
    assert ids("*/api/*") == [i for i in ids("all") if "/api/" in i]
    assert ids("tasks/{id}/move") == ["http:PATCH /api/tasks/{id}/move"]       # plain substring still works


@needs_ts
@needs_php
def test_coverage_on_linked_db_after_sources_are_gone():
    d = Path(tempfile.mkdtemp(prefix="codegraph-cov-"))
    shutil.copy(built()["combined"], d / "combined.db")
    covs = C.for_graph(GraphStore(d / "combined.db"))
    assert set(covs) == {"board-api", "board-web"} and all(covs.values())
    out = C.render(covs)
    assert "coverage board-api: php" in out and "coverage board-web: typescript" in out


def test_discovery_skips_dangling_symlinks_in_every_language(tmp_path):
    from cg_code_graph.indexer import index_project
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "ok.py").write_text("def ok():\n    return 1\n")
    os.symlink("/nonexistent/elsewhere/gone.py", tmp_path / "app" / "gone.py")
    os.symlink("../../outside/gone.php", tmp_path / "app" / "gone.php")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    st = index_project(tmp_path, tmp_path / "g.db", "x")
    ids = {r[0] for r in sqlite3.connect(tmp_path / "g.db").execute("SELECT id FROM nodes")}
    assert any(i.endswith("ok") for i in ids) and not any("gone" in i for i in ids)
    assert st["nodes"] > 0

"""Presets and the project config file: built-in per-language / per-framework presets picked by detection and recorded
in the index, every `.cg.yaml` key, `cg config show` (the source of every value) / `cg config validate`, starter
queries derived from the graph, and the shared skip lists every plugin and the coverage scan use. Every fixture is
written from scratch in a temp dir."""
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import config as CFG  # noqa: E402
from codegraph import coverage as C  # noqa: E402
from codegraph import plans as P  # noqa: E402
from codegraph import presets as PR  # noqa: E402
from codegraph import query as Q  # noqa: E402
from codegraph import routes as R  # noqa: E402
from codegraph import starters as S  # noqa: E402
from codegraph.core.paths import PathRules, glob_regex  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import FRAMEWORK_PLUGINS, LANGUAGE_PLUGINS, index_project  # noqa: E402
from codegraph.viz.server import menu  # noqa: E402

EXAMPLES = ROOT / "examples"


def write(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    return root


def build(tmp: Path, name: str, files: dict, **kw) -> GraphStore:
    root = write(tmp / name, files)
    db = tmp / f"{name}.db"
    index_project(root, db, name, **kw)
    return GraphStore(db)


def cg(*args):
    return subprocess.run([sys.executable, "-m", "codegraph.cli", *map(str, args)], cwd=ROOT, capture_output=True, text=True)


def guards(st) -> dict:
    return {i["name"]: i for i in R.routes_report(st)["items"]}


DJANGO = {
    "requirements.txt": "django\n",
    "manage.py": "import os\n",
    "site/__init__.py": "",
    "site/urls.py": """
        from django.urls import path
        from . import views

        urlpatterns = [path('a/', views.a), path('b/', views.b), path('c/', views.c), path('e/', views.e)]
        """,
    "site/views.py": """
        from django.contrib.auth.decorators import login_required
        from .guards import needs_tenant, verify_stripe
        from .models import Order


        @needs_tenant
        def a(request):
            Order.objects.create(total=1)


        @verify_stripe
        def b(request):
            Order.objects.create(total=2)


        @login_required
        def c(request):
            return None


        def e(request):
            Order.objects.filter(total=1).update(total=3)
        """,
    "site/guards.py": "def needs_tenant(f):\n    return f\n\n\ndef verify_stripe(f):\n    return f\n",
    "site/models.py": """
        from django.db import models


        class Order(models.Model):
            total = models.IntegerField()
        """,
    "legacy/old.py": "X = 1\n",
    "static/keep.py": "Y = 1\n",
    "scratch/tmp.py": "Z = 1\n",
    "gates.json": '{"scenarios": [{"name": "s1", "setting_accessors": [], "true_settings": []}]}',
    "docs/plans/p1.yaml": "plan_version: 1\nname: p1\ntitle: a plan\n",
}
FULL_CFG = """
    version: 1
    python:
      source_roots: [.]
    exclude: ["legacy/**"]
    skip_dirs:
      add: [scratch]
      keep: [static]
    frameworks:
      add: [djangorestframework]
    auth:
      extra_patterns: ["needs_tenant"]
    secret:
      extra_patterns: ["verify_stripe"]
    gates: gates.json
    plans:
      dir: docs/plans
      text_mention_dirs: [site]
    viz:
      presets:
        - {id: order_writes, label: what writes orders, mode: reaches, specs: ["table:site_order"]}
    """


# ------------------------------------------------------------------------------------------------ preset files

def test_preset_files_are_well_formed_and_cite_their_source():
    names = PR.available()
    assert {"common", "php", "typescript", "python", "dart", "rust", "c_cpp", "laravel", "django", "djangorestframework",
            "django-ninja", "nest", "nextjs", "express", "nuxt"} <= set(names)
    for n in names:
        d = PR.load(n)
        assert d["name"] == n and d["kind"] in ("common", "language", "framework") and d.get("docs"), n
        for section in ("auth", "secret"):
            for key in ("guards", "not_auth"):
                for g in PR.values(n, section, key, default=[]) or []:
                    assert g.get("name") and g.get("source"), (n, section, g)
    # the config schema knows every framework plugin; frameworks.add accepts them and the framework presets
    assert set(CFG.FRAMEWORK_PLUGIN_NAMES) == {f.name for f in FRAMEWORK_PLUGINS}
    assert set(PR.frameworks()) <= set(CFG.known_frameworks())
    assert PR.select(["python", "typescript"], ["nestjs", "django", "unknown"]) == ["common", "typescript", "python", "nest", "django"]


def test_skip_lists_come_from_the_shared_presets():
    from codegraph.plugins.cfamily import plugin as cf
    from codegraph.plugins.dart import plugin as dart, program as dprog
    from codegraph.plugins.nuxt import plugin as nuxt
    from codegraph.plugins.python import plugin as py
    from codegraph.plugins.rust import cargo
    from codegraph.plugins.ts import plugin as ts
    from codegraph.plugins.tsweb import common as tsw
    common = set(PR.load("common")["skip_dirs"])
    assert {".git", "node_modules", "__pycache__"} == common
    for got, preset, keys in ((C.SKIP_DIRS, "common", ("scan_skip_dirs",)), (py.SKIP_DIRS, "python", ()),
                              (py.DETECT_SKIP, "python", ("skip_dirs", "detect_skip_dirs")), (cf.SKIP_DIRS, "c_cpp", ()),
                              (cargo.SKIP_DIRS, "rust", ()), (ts.SKIP_DIRS, "typescript", ()), (dart.SKIP_DIRS, "dart", ()),
                              (dprog.PACKAGE_SKIP_DIRS, "dart", ("package_skip_dirs",))):
        assert set(got) == set(PR.skip_dirs(preset, *keys)) and common <= set(got), preset
    assert set(nuxt.EXCLUDE) == set(PR.skip_dirs("nuxt"))
    for d in PR.load("typescript")["build_dirs"]:
        assert d.replace(".", "\\.").replace("-", "\\-") in tsw.TEST_SKIP_RE
    assert P.SHORT_PREFIXES[0] == "App\\Models\\" and P.TEXT_MENTION_DIRS == ("app", "resources/views")
    assert R.AUTH_PATTERN == PR.values("common", "auth", "token_pattern")


def test_glob_rules():
    r = PathRules(["node_modules"], ["legacy/**", "*.generated.ts", "/tools", "docs/"], ["bootstrap/cache"])
    assert r.skip("", "legacy") and not r.skip("src", "legacy") and r.skip("", "tools") and not r.skip("src", "tools")
    assert r.skip("a/b", "docs") and r.skip("x", "node_modules") and r.skip("bootstrap", "cache") and not r.skip("", "cache")
    assert r.excluded("src/x.generated.ts") and r.excluded("legacy/a/b.py") and not r.excluded("src/app.ts")
    import re
    assert re.match(glob_regex("**/gen/*.ts"), "a/b/gen/x.ts") and re.match(glob_regex("**/gen/*.ts"), "gen/x.ts")
    assert not re.match(glob_regex("src/*.ts"), "src/a/x.ts")


# ------------------------------------------------------------------------------------------------ .cg.yaml keys

def test_every_config_key_applies(tmp_path):
    st = build(tmp_path, "shop", {**DJANGO, ".cg.yaml": FULL_CFG})
    m = st.meta()["stats"]
    # presets / frameworks recorded; frameworks.add applies the DRF preset
    assert m["presets"]["applied"] == ["common", "python", "django", "djangorestframework"]
    assert m["presets"]["frameworks"] == {"django": "detected", "djangorestframework": ".cg.yaml"}
    assert m["coverage"]["setup"] == {"frameworks": ["django", "djangorestframework"],
                                      "presets": ["common", "python", "django", "djangorestframework"], "config": ".cg.yaml"}
    # exclude / skip_dirs.add / skip_dirs.keep: the plugin and the coverage scan agree
    files = {r["file"] for r in st.q("SELECT DISTINCT file FROM nodes WHERE file IS NOT NULL")}
    assert "static/keep.py" in files and "legacy/old.py" not in files and "scratch/tmp.py" not in files
    py = next(e for e in m["coverage"]["languages"] if e["language"] == "python")
    assert py["files_complete"] is True and py["indexed"] == py["files"]
    # auth / secret extra_patterns: the Django plugin records the project's decorators, routes classifies them
    g = guards(st)
    assert g["ANY /a/"]["has_auth"] and g["ANY /a/"]["guards"][0]["auth_by"] == "project pattern"
    assert not g["ANY /b/"]["has_auth"] and g["ANY /b/"]["secret_checked"]
    assert g["ANY /c/"]["guards"][0]["auth_by"] == "preset django"
    assert not g["ANY /e/"]["has_auth"]
    unguarded = R.routes_report(st, writes="*", unguarded=True)
    assert [i["name"] for i in unguarded["items"]] == ["ANY /e/"]
    txt = R.render_routes(R.routes_report(st), st)
    assert "auth guards by source: preset django 1, project pattern 1" in txt
    # gates, plans.dir, plans.text_mention_dirs, viz.presets
    assert m["gates_from"] == ".cg.yaml" and m["config"]["gates"] == "gates.json"
    assert P.resolve_plans_dir(None, st.path) == str(tmp_path / "shop" / "docs" / "plans")
    out = cg("plan", "list", "--db", st.path).stdout
    assert out.startswith("p1 [draft] a plan")
    assert P.mention_dirs(st, None) == ("site",)
    mn = menu(st)
    assert mn[0]["id"] == "order_writes" and mn[0]["source"] == ".cg.yaml"
    assert not any(p["source"] == "examples" for p in mn)        # sample-app presets do not resolve here
    assert any(p["id"] == "starter_unguarded_write" and p["specs"] == ["route:ANY /e/"] for p in mn)


def test_frameworks_remove_and_flags_take_precedence(tmp_path):
    st = build(tmp_path, "nodj", {**DJANGO, ".cg.yaml": "frameworks:\n  remove: [django]\ngates: missing.json\n"},
               gates=str(tmp_path / "g.json") if (tmp_path / "g.json").write_text('{"scenarios": []}') else None)
    m = st.meta()["stats"]
    assert m["presets"]["applied"] == ["common", "python"] and m["presets"]["frameworks_removed"] == ["django"]
    assert not st.q("SELECT 1 FROM nodes WHERE kind='route'") and m["gates_from"] == "flag"
    # without the flag, a gates file that does not exist is a config error
    r = cg("index", tmp_path / "nodj", "--db", tmp_path / "x.db")
    assert r.returncode == 2 and "cg index: .cg.yaml: gates: 'missing.json' does not exist" in r.stderr


def test_ts_exclude_and_project_auth_pattern(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    st = build(tmp_path, "shop", {
        "package.json": '{"name": "shop", "dependencies": {"express": "^4.19.0"}}',
        "src/app.js": """
            const express = require('express')
            const { requireTenantMember } = require('./guards')
            const app = express()
            app.get('/orders', requireTenantMember, (req, res) => res.json([]))
            app.post('/orders', (req, res) => res.json({}))
            module.exports = app
            """,
        "src/guards.js": "exports.requireTenantMember = (req, res, next) => next()\n",
        "src/legacy/old.js": "const app = require('express')()\napp.get('/legacy', (req, res) => res.json([]))\n",
        "src/legacy/old.test.js": "test('x', () => {})\n",
        ".cg.yaml": 'exclude: ["src/legacy/**"]\nauth:\n  extra_patterns: ["requireTenantMember"]\n'})
    g = guards(st)
    assert set(g) == {"GET /orders", "POST /orders"} and g["GET /orders"]["has_auth"] and not g["POST /orders"]["has_auth"]
    assert not st.q("SELECT 1 FROM nodes WHERE file LIKE 'src/legacy/%'")
    ts = next(e for e in st.meta()["stats"]["coverage"]["languages"] if e["language"] == "typescript")
    assert ts["files"] == 2 and ts["status"] == "exact"
    assert st.meta()["stats"]["presets"]["applied"] == ["common", "typescript", "express"]


def test_invalid_config_files_fail_with_a_clear_message(tmp_path):
    cases = {
        "exclude: ../outside\n": "exclude[0]: '../outside' must stay inside the indexed root",
        "skip_dirs:\n  add: [a/b]\n": "skip_dirs.add: 'a/b' is not a directory name (use exclude for paths and globs)",
        "skip_dirs:\n  drop: [x]\n": "skip_dirs: unknown key 'drop' (known: add, keep)",
        "frameworks:\n  add: [nset]\n": "frameworks.add[0]: unknown framework 'nset'",
        "frameworks:\n  add: [nest]\n  remove: [nestjs]\n": "frameworks: 'nest' is in both add and remove",
        "auth:\n  extra_patterns: ['(']\n": "auth.extra_patterns[0]: '(' is not a valid regular expression",
        "secret: [x]\n": "secret: expected a mapping (keys: extra_patterns)",
        "gates: /etc/gates.json\n": "gates: '/etc/gates.json' must stay inside the indexed root",
        "plans:\n  dir: ../plans\n": "plans.dir: '../plans' must stay inside the indexed root",
        "viz:\n  presets: [{id: a, label: b, mode: sideways, specs: [x]}]\n": "viz.presets[0].mode: 'sideways' is not one of",
        "viz:\n  presets: [{id: a, label: b, mode: reaches}]\n": "viz.presets[0].specs: expected a non-empty list of strings",
        "version: 3\n": "version 3 is not supported",
        "- a\n": "expected a mapping of keys at the top level",
    }
    for i, (body, msg) in enumerate(cases.items()):
        d = write(tmp_path / f"c{i}", {"a.py": "X = 1\n", ".cg.yaml": body})
        with pytest.raises(CFG.ConfigError) as ex:
            CFG.load(d)
        assert msg in str(ex.value) and str(ex.value).startswith(".cg.yaml: "), (body, str(ex.value))
        r = cg("config", "validate", d)
        assert r.returncode == 2 and msg in r.stderr, (body, r.stderr)
    r = cg("index", tmp_path / "c0", "--db", tmp_path / "c0.db")
    assert r.returncode == 2 and "exclude[0]" in r.stderr
    # a typo in a top-level key is a warning with a suggestion (the file still indexes)
    d = write(tmp_path / "typo", {"a.py": "X = 1\n", ".cg.yaml": "exlude: [x]\n"})
    r = cg("config", "validate", d)
    assert r.returncode == 0 and "unknown top-level key 'exlude' is ignored (did you mean 'exclude'?)" in r.stdout
    assert cg("config", "validate", tmp_path / "nothing-here").returncode == 0


def test_config_show_lists_the_source_of_every_value(tmp_path):
    root = write(tmp_path / "shop", {**DJANGO, ".cg.yaml": FULL_CFG})
    r = cg("config", "show", root, "--json", "--auth-pattern", "x_guard", "--plans-dir", "elsewhere")
    eff = json.loads(r.stdout)
    src = {}
    for row in eff["rows"]:
        src.setdefault(row["key"], []).append(row["source"])
        assert row["source"], row
    assert src["frameworks"] == ["detected", ".cg.yaml frameworks.add"]
    assert src["presets"] == ["built-in (codegraph/presets), picked by detection"]
    assert src["python.source_roots"] == [".cg.yaml python.source_roots"]
    assert src["exclude"] == [".cg.yaml exclude"]
    assert "preset common" in src["skip_dirs"] and "preset python" in src["skip_dirs"] and ".cg.yaml skip_dirs.add" in src["skip_dirs"]
    assert src["skip_dirs kept"] == [".cg.yaml skip_dirs.keep"]
    assert src["auth.guards"] == ["preset django", "preset djangorestframework"]
    assert src["auth.extra_patterns"] == [".cg.yaml auth.extra_patterns", "flag --auth-pattern"]
    assert src["secret.extra_patterns"] == [".cg.yaml secret.extra_patterns"]
    assert src["gates"] == [".cg.yaml gates"] and src["plans.dir"] == ["flag --plans-dir"]
    assert src["plans.text_mention_dirs"] == [".cg.yaml plans.text_mention_dirs"]
    assert src["viz.presets"] == [".cg.yaml viz.presets"]
    txt = cg("config", "show", root, "--gates", "other.json").stdout
    assert "[flag --gates]" in txt and "[preset django]" in txt and "[.cg.yaml exclude]" in txt
    bad = write(tmp_path / "bad", {"a.py": "", ".cg.yaml": "frameworks: {add: [nope]}\n"})
    r = cg("config", "show", bad)
    assert r.returncode == 2 and "unknown framework 'nope'" in r.stderr


# ------------------------------------------------------------------------------------------------ guards, starters

def test_framework_preset_guards():
    m = R.AuthMatcher(applied=["common", "php", "laravel", "django", "djangorestframework", "nest", "express"])
    assert m.why("password.confirm") == "preset laravel" and m.why("abilities:check-status") == "preset laravel"
    assert m.why("rest_framework.permissions.IsAuthenticated") == "preset djangorestframework"
    assert m.why("user_passes_test") == "preset django" and m.why("expressjwt") == "preset express"
    assert m.why("AllowAny") is None and m.why("ThrottlerGuard") is None and m.why("csrf_protect") is None
    assert m.why("ApiKeyGuard") == "name pattern" and m.why("AuditLogInterceptor") is None
    assert m.secret("signed") and m.secret("VerifyWebhookSignature") and not m.secret("auth:api")
    # without a framework preset only the common token pattern applies (csrf_protect matches `protect` there)
    assert R.AuthMatcher(applied=["common"]).why("csrf_protect") == "name pattern"
    # a preset entry matches the guard's own name (namespace, parameters and call arguments aside); a project guard
    # that merely contains a preset name is classified by the name pattern
    assert m.why("AuthGuard('jwt')") == "preset nest" and m.why("MaintenanceAuthGuard") == "name pattern"
    assert m.why("Illuminate\\Auth\\Middleware\\EnsureEmailIsVerified") == "preset laravel"
    assert m.why("App\\Http\\Middleware\\Authorize::handle") == "preset laravel" and m.why("auth:sanctum") == "preset laravel"
    assert m.why("passport.authenticate('jwt')") == "preset express" and m.why("CustomThrottlerGuard") is None


@pytest.mark.parametrize("example", ["bookstore-api", "bookstore-django", "bookstore-nest", "bookstore-web", "rust-kvstore"])
def test_starters_resolve_on_the_examples(tmp_path, example):
    if example in ("bookstore-nest", "bookstore-web") and not shutil.which("node"):
        pytest.skip("node not installed")
    if example == "bookstore-api" and not shutil.which("php"):
        pytest.skip("php not installed")
    db = tmp_path / "x.db"
    stats = index_project(EXAMPLES / example, db, example)
    st = GraphStore(db)
    rows = stats["starters"]
    assert rows and rows == S.for_graph(st)
    for s in rows:
        assert s["mode"] in ("reaches", "impact", "downstream") and s["cli"] and s["mcp"], s
        assert all(Q.resolve_targets(st, x) for x in s["specs"]), s
    ids = {s["id"] for s in rows}
    want = {"bookstore-api": {"starter_unguarded_write", "starter_most_written_table", "starter_top_connection"},
            "bookstore-django": {"starter_unguarded_write", "starter_most_written_table"},
            "bookstore-nest": {"starter_unguarded_write", "starter_most_called_1"},
            "bookstore-web": {"starter_deepest_page"}, "rust-kvstore": {"starter_most_called_1"}}[example]
    assert want <= ids, ids
    assert "starter queries" in S.render(rows)


def test_mcp_starters_tool(tmp_path):
    from codegraph import mcp_server as M
    db = tmp_path / "dj.db"
    index_project(write(tmp_path / "dj", DJANGO), db, "dj")
    old = dict(M.STATE)
    try:
        M.STATE.update(db=str(db))
        out = M.starters()
        assert out.startswith("starter queries") and "routes(writes='*', unguarded=true)" in out
        assert "frameworks: django | presets: common, python, django | config: no .cg.yaml" in M.coverage()
    finally:
        M.STATE.clear(); M.STATE.update(old)

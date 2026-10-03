"""Completeness reporting: per-language file completeness (discovered / indexed / parse failed / unmapped / over size /
excluded), unsupported source types by extension or shebang, blind-spot detectors (each with a positive and a negative
fixture, written from scratch in a temp dir), scoped notes on partial answers, and the `completeness` object of MCP
replies. A repository with no gaps and no blind spots keeps the short output."""
import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from sample import EXTRACTOR_DEPS  # noqa: E402
from codegraph import blindspots as B, coverage as C, query as Q, routes as R  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")


def write(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    return root


def cg(*args):
    return subprocess.run([sys.executable, "-m", "codegraph.cli", *map(str, args)], cwd=ROOT, capture_output=True, text=True,
                          env=dict(os.environ, CODEGRAPH_NO_CACHE="1"))


def build(tmp: Path, name: str, files: dict) -> Path:
    root = write(tmp / name, files)
    db = tmp / f"{name}.db"
    index_project(root, db, name)
    return db


def cov_of(db: Path) -> dict:
    return GraphStore(db).meta()["stats"]["coverage"]


def lang(cov, name):
    return next(e for e in cov["languages"] if e["language"] == name)


def kinds(found):
    return {f["kind"]: f for f in found}


PY_REPRO = {
    "requirements.txt": "",
    "app/__init__.py": "",
    "app/util.py": "def helper():\n    return 1\n",
    "app/bad.py": "def broken(:\n    pass\n",
    "my-scripts/run.py": "print('hi')\n",
    "app/Main.qml": "import QtQuick\nItem {}\n",
    "scripts/deploy.sh": "#!/bin/sh\necho deploy\n",
    "bin/release": "#!/usr/bin/env bash\necho release\n",
    "assets/logo.svg": "<svg/>\n",
    "data/rows.csv": "a,b\n",
}

DJANGO = {
    "requirements.txt": "django\n",
    "manage.py": "import os\n",
    "shop/__init__.py": "",
    "shop/urls.py": '''
        from django.urls import path
        from . import views

        def api_routes(prefix):
            return [path(f"{prefix}/orders/", views.list_orders)]

        urlpatterns = [
            path("health/", views.health),
            *api_routes("api"),
        ]
        ''',
    "shop/views.py": '''
        from django.http import JsonResponse

        def health(request):
            return JsonResponse({"ok": True})

        def list_orders(request):
            return JsonResponse({"orders": []})
        ''',
}


# --------------------------------------------------------------------------------------------- file completeness

def test_python_repro_reports_file_buckets_and_unsupported_types(tmp_path):
    db = build(tmp_path, "proj", PY_REPRO)
    cov = cov_of(db)
    py = lang(cov, "python")
    assert (py["files"], py["indexed"], py["parse_failed"], py["unmapped"]) == (4, 2, 1, 1)
    assert py["status"] == "exact" and py["files_complete"] is False
    assert py["paths"] == {"parse_failed": ["app/bad.py"], "unmapped": ["my-scripts/run.py"]}
    uns = {e["language"]: e["files"] for e in cov["languages"] if e["status"] == "unsupported"}
    assert uns == {"qml": 1, "sh": 2}          # .sh by extension + an extensionless bash script by shebang; svg / csv stay out
    out = cg("coverage", "--db", db).stdout
    assert "python 4 discovered, 2 indexed (exact parser): 1 parse failed, 1 unmapped" in out
    assert "parse failed: app/bad.py" not in out and "cg coverage --details" in out       # the summary (#75)
    out = cg("coverage", "--db", db, "--details").stdout
    assert "python 4 discovered, 2 indexed (exact parser): 1 parse failed, 1 unmapped" in out
    assert "parse failed: app/bad.py" in out and "unmapped: my-scripts/run.py" in out
    assert "qml 1 unsupported" in out and "sh 2 unsupported" in out
    assert "every source file cg found is indexed" not in out
    comp = C.completeness(C.for_graph(GraphStore(db)))
    assert comp["complete"] is False and comp["unsupported"] == {"qml": 1, "sh": 2}
    assert comp["languages"]["python"] == {"mode": "exact", "discovered": 4, "indexed": 2, "parse_failed": 1, "unmapped": 1,
                                           "complete": False}
    js = json.loads(cg("coverage", "--db", db, "--json").stdout)
    assert lang(js["proj"], "python")["indexed"] == 2


def test_excluded_files_do_not_make_an_answer_partial(tmp_path):
    db = build(tmp_path, "mig", {"requirements.txt": "", "app/__init__.py": "", "app/models.py": "X = 1\n",
                                 "app/migrations/__init__.py": "", "app/migrations/0001_initial.py": "OPS = []\n"})
    py = lang(cov_of(db), "python")
    assert py["excluded"] == 2 and py["indexed"] == 2 and py["files_complete"] is True
    out = cg("coverage", "--db", db).stdout
    assert out.startswith("coverage mig: python 4 exact\n") and "every source file cg found is indexed" in out
    assert "app/migrations/0001_initial.py" in cg("coverage", "--db", db, "--all-files").stdout


def test_complete_repo_keeps_short_output(tmp_path):
    db = build(tmp_path, "clean", {"requirements.txt": "django\n", "manage.py": "import os\n", "site/__init__.py": "",
                                   "site/urls.py": "from django.urls import path\nfrom . import views\n\n"
                                                   "urlpatterns = [path('a/', views.a)]\n",
                                   "site/views.py": "def a(request):\n    return None\n\ndef unused():\n    return 2\n"})
    st = GraphStore(db)
    cov = cov_of(db)
    assert "blind_spots" not in cov and cov["gaps"] == 0
    assert C.render({"": cov}).endswith("every source file cg found is indexed; edges still carry their own exact / "
                                        "resolved / heuristic label.")
    txt = R.render_routes(R.routes_report(st), st)
    assert "all routes: 1 of 1 routes" in txt and "coverage note" not in txt and "possibly more" not in txt
    r = Q.impact(st, "site.views.unused")
    msg = Q.explain_no_callers(st, "site.views.unused", r["targets"])
    assert "has no recorded callers" in msg and "coverage note" not in msg


# --------------------------------------------------------------------------------------------- partial answers

def test_django_dynamic_urlpatterns_partial_routes_and_impact(tmp_path):
    db = build(tmp_path, "shop", DJANGO)
    st = GraphStore(db)
    bs = kinds(cov_of(db)["blind_spots"])
    assert bs["django_dynamic_urlpatterns"]["sample"] == "shop/urls.py:9"
    txt = R.render_routes(R.routes_report(st), st)
    assert "all routes: 1 indexed (possibly more: 1 unmodelled route registration)" in txt
    assert "coverage note: 1 route registration cg does not model (Django urlpatterns built by a function call" in txt
    out = cg("impact", "shop.views.list_orders", "--db", db).stdout
    # the view is passed to path() inside api_routes: a function reference keeps it in impact, and the unmodelled
    # registration (urlpatterns built by a call) is still named
    assert "d=1 [shop] shop.urls.api_routes  (ref: callback)" in out and "entry points: 0" in out
    assert "1 route registration cg does not model" in out and "shop/urls.py:9" in out
    filt = R.render_routes(R.routes_report(st, writes="*"), st)
    assert "0 of 1 indexed routes (possibly more: 1 unmodelled route registration)" in filt


def test_mcp_replies_carry_completeness(tmp_path):
    db = build(tmp_path, "shop", DJANGO)
    plans = write(tmp_path / "plans", {"p.yaml": '''
        plan_version: 1
        name: p
        title: "touch the order list"
        status: draft
        modify:
          - target: shop.views.list_orders
            intent: "add paging"
        '''})
    from codegraph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE.update(db=str(db), plans=str(plans))
        calls = {"routes": {}, "impact": {"method": "shop.views.list_orders"}, "callers": {"symbol": "shop.views.list_orders"},
                 "reaches": {"targets": ["shop.views.health"]}, "tests_covering": {"target": "shop.views.health"},
                 "plan_check": {"name": "p"}, "coverage": {}, "stats": {}}
        for name, args in calls.items():
            res = asyncio.run(M.server.call_tool(name, args))
            sc = res.structured_content
            assert sc["result"] == res.content[0].text, name
            comp = sc["completeness"]
            assert comp["complete"] is False, name
            assert comp["blind_spots"][0]["kind"] == "django_dynamic_urlpatterns", name
            assert comp["languages"]["python"]["indexed"] == 4, name
            if name in ("routes", "impact", "callers", "reaches", "tests_covering", "plan_check"):
                assert "coverage note: 1 route registration cg does not model" in sc["result"], name
            assert sc["result"].count("coverage note:") <= 1, name
        tools = {t.name: t for t in asyncio.run(M.server.list_tools())}
        assert "completeness" in tools["routes"].output_schema["properties"]
        assert json.loads(M.coverage(json_output=True))["blind_spots"][0]["sample"] == "shop/urls.py:9"
    finally:
        M.STATE.clear(); M.STATE.update(old)


def test_mcp_complete_answer_has_no_note(tmp_path):
    db = build(tmp_path, "clean", {"requirements.txt": "", "pkg/__init__.py": "", "pkg/a.py": "def f():\n    return g()\n\ndef g():\n    return 1\n"})
    from codegraph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE["db"] = str(db)
        res = asyncio.run(M.server.call_tool("impact", {"method": "pkg.a.g"}))
        assert res.structured_content["completeness"]["complete"] is True
        assert "coverage note" not in res.content[0].text
        assert "pkg.a.f" in M.callers("pkg.a.g")
    finally:
        M.STATE.clear(); M.STATE.update(old)


@needs_ts
def test_nest_wrapped_route_decorator_end_to_end(tmp_path):
    db = build(tmp_path, "nestapp", NEST_FILES)
    st = GraphStore(db)
    txt = R.render_routes(R.routes_report(st), st)
    assert "all routes: 1 indexed (possibly more: 1 unmodelled route registration)" in txt
    r = Q.impact(st, "OrdersController.listOrders")
    msg = Q.explain_no_callers(st, "OrdersController.listOrders", r["targets"])
    assert "no callers found in indexed code" in msg and "src/orders.controller.ts:9" in msg


# --------------------------------------------------------------------------------------------- detectors (text)

NEST_FILES = {
    "package.json": '{"name":"n","dependencies":{"@nestjs/common":"^10.0.0"},"devDependencies":{"typescript":"^5.4.0"}}\n',
    "tsconfig.json": '{"compilerOptions":{"experimentalDecorators":true,"target":"ES2021","module":"commonjs"},"include":["src"]}\n',
    "src/api-get.decorator.ts": '''
        import { applyDecorators, Get, UseGuards } from '@nestjs/common';
        import { AuthGuard } from './auth.guard';

        export function ApiGet(path: string) {
          return applyDecorators(Get(path), UseGuards(AuthGuard));
        }

        export const Secured = () => applyDecorators(UseGuards(AuthGuard));
        ''',
    "src/auth.guard.ts": '''
        import { CanActivate, Injectable } from '@nestjs/common';

        @Injectable()
        export class AuthGuard implements CanActivate {
          canActivate(): boolean { return true; }
        }
        ''',
    "src/orders.controller.ts": '''
        import { Controller, Get } from '@nestjs/common';
        import { ApiGet, Secured } from './api-get.decorator';

        @Controller('orders')
        export class OrdersController {
          @Get('health')
          health() { return { ok: true }; }

          @ApiGet('x')
          @Secured()
          listOrders() { return []; }
        }
        ''',
}


def test_nest_detector_positive_and_negative(tmp_path):
    root = write(tmp_path / "n", NEST_FILES)
    files = ["src/api-get.decorator.ts", "src/auth.guard.ts", "src/orders.controller.ts"]
    f = B.nest_wrapped_route_decorators(root, files)
    assert f["count"] == 1 and f["samples"] == ["src/orders.controller.ts:9"]
    write(root, {"src/factory.ts": "import { Post } from '@nestjs/common';\nexport const Create = (p: string) => Post(p);\n",
                 "src/b.controller.ts": "import { Create } from './factory';\nclass B {\n  @Create('y')\n  make() {}\n}\n"})
    f = B.nest_wrapped_route_decorators(root, files + ["src/factory.ts", "src/b.controller.ts"])
    assert f["count"] == 2 and "src/b.controller.ts:3" in f["samples"]
    neg = write(tmp_path / "neg", {"src/d.ts": "import { applyDecorators, UseGuards } from '@nestjs/common';\n"
                                               "export function Auth() { return applyDecorators(UseGuards(X)); }\n",
                                   "src/c.ts": "class C {\n  @Auth()\n  @Get('a')\n  a() {}\n}\n"})
    assert B.nest_wrapped_route_decorators(neg, ["src/d.ts", "src/c.ts"]) is None


def test_express_detector_positive_and_negative(tmp_path):
    pos = write(tmp_path / "pos", {
        "package.json": '{"dependencies":{"express":"^4"}}',
        "src/routes.js": '''
            const express = require('express');
            const router = express.Router();
            const table = [{ method: 'get', path: '/a', fn: a }, { method: 'post', path: '/b', fn: b }];
            for (const r of table) {
              router[r.method](r.path, r.fn);
            }
            ['put', 'patch'].forEach((m) => {
              router[m]('/c', c);
            });
            module.exports = router;
            ''',
        "src/mount.js": '''
            module.exports = function mount(app, routers) {
              Object.entries(routers).forEach(([prefix, r]) => app.use(prefix, r));
            };
            '''})
    f = B.express_loop_routes(pos, ["src/routes.js", "src/mount.js"])
    assert f["count"] == 3 and set(f["samples"]) == {"src/routes.js:4", "src/routes.js:7", "src/mount.js:2"}
    neg = write(tmp_path / "neg", {
        "package.json": '{"dependencies":{"express":"^4"}}',
        "src/routes.js": '''
            const express = require('express');
            const router = express.Router();
            router.get('/a', a);
            // for (const r of table) router[r.method](r.path, r.fn);
            for (const key of keys) {
              cache.get(key);
            }
            items.forEach((x) => total.push(x));
            module.exports = router;
            ''',
        "web/client.js": "for (const id of ids) { api.get(`/x/${id}`); }\n"})
    assert B.express_loop_routes(neg, ["src/routes.js", "web/client.js"]) is None


def test_laravel_detector_positive_and_negative(tmp_path):
    pos = write(tmp_path / "pos", {"routes/api.php": '''
        <?php
        use Illuminate\\Support\\Facades\\Route;

        foreach (['books', 'authors'] as $res) {
            Route::get("/{$res}", [CatalogController::class, 'index']);
        }
        collect(config('modules'))->each(function ($m) {
            Route::apiResource($m['uri'], $m['controller']);
        });
        '''})
    f = B.laravel_loop_routes(pos, ["routes/api.php"])
    assert f["count"] == 2 and f["samples"] == ["routes/api.php:4", "routes/api.php:7"]
    neg = write(tmp_path / "neg", {"routes/api.php": '''
        <?php
        use Illuminate\\Support\\Facades\\Route;

        Route::middleware('auth')->group(function () {
            Route::get('/books', [BookController::class, 'index']);
        });
        foreach ($listeners as $l) {
            Event::listen($l);
        }
        // foreach ($x as $y) { Route::get($y, Z::class); }
        '''})
    assert B.laravel_loop_routes(neg, ["routes/api.php"]) is None


# --------------------------------------------------------------------------------------------- detectors (Python, indexed)

def test_django_detector_positive_and_negative(tmp_path):
    pos = build(tmp_path, "pos", {
        "requirements.txt": "django\n", "manage.py": "", "site/__init__.py": "",
        "site/views.py": "def a(request):\n    return None\n",
        "site/urls.py": '''
            from django.urls import path, include
            from . import views

            def gen():
                return [path("g/", views.a)]

            urlpatterns = [path("a/", views.a)]
            urlpatterns += [path(f"{n}/", views.a) for n in ("x", "y")]
            for name in ("p", "q"):
                urlpatterns.append(path(f"{name}/", views.a))
            urlpatterns += gen()
            urlpatterns += [path("inc/", include(gen()))]
            '''})
    bs = kinds(cov_of(pos)["blind_spots"])
    assert set(bs["django_dynamic_urlpatterns"]["samples"]) == {"site/urls.py:8", "site/urls.py:10", "site/urls.py:11"}
    assert bs["django_unresolved_include"]["samples"] == ["site/urls.py:12"]
    neg = build(tmp_path, "neg", {
        "requirements.txt": "django\n", "manage.py": "", "site/__init__.py": "",
        "site/views.py": "def a(request):\n    return None\n",
        "site/sub.py": "from django.urls import path\nfrom . import views\nurlpatterns = [path('s/', views.a)]\n",
        "site/urls.py": '''
            from django.urls import path, re_path, include
            from django.conf import settings
            from django.conf.urls.static import static
            from rest_framework.urlpatterns import format_suffix_patterns
            from . import views

            extra = [path("e/", views.a)]
            urlpatterns = [
                path("a/", views.a),
                re_path(r"^b/$", views.a),
                path("sub/", include("site.sub")),
                path("accounts/", include("allauth.urls")),
                *extra,
            ]
            urlpatterns = format_suffix_patterns(urlpatterns)
            urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
            '''})
    assert "blind_spots" not in cov_of(neg)


def test_python_registration_detectors_positive_and_negative(tmp_path):
    db = build(tmp_path, "reg", {
        "requirements.txt": "sanic\n", "svc/__init__.py": "",
        "svc/app.py": '''
            from sanic import Sanic
            import functools
            from unittest import mock
            from .registry import registry

            app = Sanic(__name__)

            @app.route("/orders")
            def list_orders():
                return []

            @app.get("/called")
            def called():
                return 1

            def uses():
                return called()

            @registry.register("export")
            def export_job():
                return 2

            @functools.lru_cache
            def cached():
                return 3

            @mock.patch("svc.app.cached")
            def patched(m):
                return m

            def handle_a(msg):
                return msg

            def handle_b(msg):
                return msg

            HANDLERS = {}
            HANDLERS["a"] = handle_a
            HANDLERS["b"] = handle_b

            def main():
                return handle_b("x")
            ''',
        "svc/registry.py": "class Registry:\n    def register(self, name):\n        return lambda f: f\n\nregistry = Registry()\n",
        "svc/tools.py": "def tool(fn):\n    return fn\n\n\n@tool\ndef ping():\n    return 1\n"})
    bs = kinds(cov_of(db)["blind_spots"])
    assert bs["python_decorator_routes"]["samples"] == ["svc/app.py:9"]                 # def line; called() has a caller
    assert bs["python_decorator_registration"]["samples"] == ["svc/app.py:20", "svc/tools.py:6"]  # lru_cache / mock.patch: no
    assert bs["python_registry_assignment"]["samples"] == ["svc/app.py:38"]             # handle_b is also called directly
    st = GraphStore(db)
    msg = Q.explain_no_callers(st, "svc.app.export_job", Q.impact(st, "svc.app.export_job")["targets"])
    assert "no callers found in indexed code" in msg and "dynamically registered handler" in msg
    # handler blind spots are scoped to answers that involve a registered function (or its same-file decorator) ...
    msg = Q.explain_no_callers(st, "svc.tools.tool", Q.impact(st, "svc.tools.tool")["targets"])
    assert "dynamically registered handler" in msg
    comp = C.completeness_for(st, ["function:svc.app.handle_a"])
    assert [b["sample"] for b in comp["blind_spots"] if b["category"] == "handler"] == ["svc/app.py:38"]
    # ... and stay out of unrelated answers in the same directory
    comp = C.completeness_for(st, Q.impact(st, "svc.app.uses")["targets"])
    assert not [b for b in comp.get("blind_spots", []) if b["category"] == "handler"]


def test_detectors_never_fail_the_index(tmp_path, monkeypatch):
    def boom(*a):
        raise RuntimeError("detector bug")
    monkeypatch.setattr(B, "laravel_loop_routes", boom)
    root = write(tmp_path / "x", {"routes/web.php": "<?php Route::get('/a', 'A@b');\n"})
    assert B.detect(root, {".php": ["routes/web.php"]}) == []

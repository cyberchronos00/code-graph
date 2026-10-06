"""Python web routes per host (#59): a Flask `subdomain=` / `host=` or Starlette `Host` route keeps its own route node
(`GET / @api.*`) next to a same-path route, and a test request that names a host links to the route on that host."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_python_tests import build, edges  # noqa: E402

FILES = {
    "requirements.txt": "flask\nfastapi\n",
    "site/app.py": '''
        from flask import Flask

        app = Flask(__name__)
        app.config["SERVER_NAME"] = "localhost"

        @app.route("/", subdomain="api")
        def api_index():
            return "api"

        @app.route("/")
        def index():
            return "site"
        ''',
    "svc/main.py": '''
        from fastapi import FastAPI

        app = FastAPI()
        api = FastAPI()

        @api.get("/v")
        def api_version():
            return {}

        @app.get("/v")
        def site_version():
            return {}

        app.host("api.example.org", api)
        ''',
    "tests/test_hosts.py": '''
        from site.app import app

        def test_api():
            client = app.test_client()
            client.get("/", subdomain="api")

        def test_site():
            client = app.test_client()
            client.get("/")

        def test_base_url():
            client = app.test_client()
            client.get("/", base_url="http://api.localhost")
        ''',
}


def test_host_keys_and_linking(tmp_path):
    st = build(tmp_path, "h", FILES)
    ids = {r["id"] for r in st.q("SELECT id FROM nodes WHERE kind='route'")}
    assert {"route:GET /", "route:GET / @api.*", "route:GET /v", "route:GET /v @api.example.org"} <= ids, sorted(ids)
    names = {r["name"] for r in st.q("SELECT name FROM nodes WHERE id='route:GET / @api.*'")}
    assert names == {"GET /"}
    link = {}
    for s, d, _a in edges(st, "TEST_HTTP"):
        link.setdefault(s.rsplit(".", 1)[-1], set()).add(d)
    assert link["test_api"] == {"route:GET / @api.*"}
    assert link["test_site"] == {"route:GET /"}
    assert link["test_base_url"] == {"route:GET / @api.*"}


PATHS = {
    "requirements.txt": "flask\n",
    "files/app.py": '''
        from flask import Flask

        app = Flask(__name__)

        @app.route("/files/<path:name>")
        def get_file(name):
            return name
        ''',
    "tests/test_files.py": '''
        from files.app import app

        def test_nested_file():
            app.test_client().get("/files/css/site.css")
        ''',
}


def test_path_converter_matches_several_segments(tmp_path):
    st = build(tmp_path, "p", PATHS)
    assert [d for _s, d, _a in edges(st, "TEST_HTTP")] == ["route:GET /files/{name*}"]


DEPS = {
    "requirements.txt": "fastapi\n",
    "api/main.py": '''
        from fastapi import Depends, FastAPI, HTTPException, Request
        from fastapi.responses import JSONResponse
        from starlette.middleware.base import BaseHTTPMiddleware

        def check_token(token):
            if token != "ok":
                raise HTTPException(status_code=401)
            return token

        def load_profile(request: Request):
            return check_token(request.headers.get("x-token"))

        class RoleChecker:
            def __init__(self, role):
                self.role = role

            def __call__(self, request: Request):
                if request.headers.get("x-role") != self.role:
                    raise HTTPException(status_code=403)

        class GateMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                if not request.headers.get("x-gate"):
                    return JSONResponse({"detail": "no"}, status_code=401)
                return await call_next(request)

        app = FastAPI()
        app.add_middleware(GateMiddleware)
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=["*"])

        @app.get("/me")
        def me(user=Depends(load_profile)):
            return user

        @app.get("/admin")
        def admin(_=Depends(RoleChecker("admin"))):
            return {}
        ''',
}


def test_dependency_helpers_class_instances_and_middleware(tmp_path):
    from cg_code_graph import routes as R
    from test_python_web_routes import routes
    st = build(tmp_path, "d", DEPS)
    r = routes(st)
    acc = {a["name"]: a for a in r["GET /me"]["access"]}
    assert acc["load_profile"]["checks"]["rejects"] == [401] and acc["load_profile"]["checks"]["checked_in"] == ["check_token"]
    assert acc["GateMiddleware"]["via"] == "middleware" and acc["GateMiddleware"]["checks"]["rejects"] == [401]
    assert "CORSMiddleware" not in acc          # library middleware that is not access control
    adm = {a["name"]: a for a in r["GET /admin"]["access"]}
    assert adm["RoleChecker"]["checks"]["rejects"] == [403]          # Depends(RoleChecker("admin")): __call__
    rep = {x["route"]: x for x in R.routes_report(st)["items"]}
    g = {x["name"]: x for x in rep["route:GET /me"]["guards"]}
    assert g["load_profile"]["auth_by"] == "dependency check (raises 401 in check_token)"
    assert g["GateMiddleware"]["auth"] is True


OVERRIDES = {
    "requirements.txt": "fastapi\npytest\n",
    "api/main.py": '''
        from fastapi import Depends, FastAPI

        def get_current_user():
            return None

        app = FastAPI()

        @app.get("/me")
        def me(user=Depends(get_current_user)):
            return user
        ''',
    "tests/conftest.py": '''
        import pytest
        from fastapi.testclient import TestClient
        from api.main import app, get_current_user

        @pytest.fixture
        def client():
            app.dependency_overrides[get_current_user] = lambda: {"id": 1}
            yield TestClient(app)
            app.dependency_overrides.clear()
        ''',
    "tests/test_me.py": '''
        from fastapi.testclient import TestClient
        from api.main import app

        def test_me(client):
            client.get("/me")

        def test_me_plain():
            TestClient(app).get("/me")
        ''',
}


def test_dependency_overrides_on_test_requests(tmp_path):
    st = build(tmp_path, "o", OVERRIDES)
    by = {s.rsplit(".", 1)[-1]: a for s, _d, a in edges(st, "TEST_HTTP")}
    assert by["test_me"]["dependency_overrides"] == ["get_current_user"]
    assert "dependency_overrides" not in by["test_me_plain"]


CLASSFUL = {
    "requirements.txt": "flask\nflask-classful\n",
    "web/app.py": '''
        from flask import Flask
        from flask_classful import FlaskView, route

        app = Flask(__name__)

        class QuotesView(FlaskView):
            def index(self):
                return "all"

            def get(self, id):
                return id

            def post(self):
                return "new"

            def random(self):
                return "one"

            @route("/word/<word>", methods=["GET", "POST"])
            def by_word(self, word):
                return word

            def _helper(self):
                return 1

        class AdminView(FlaskView):
            route_base = "/staff"

            def index(self):
                return "staff"

        QuotesView.register(app)
        AdminView.register(app, route_prefix="/v1")
        ''',
}


def test_flask_classful_views(tmp_path):
    from test_python_web_routes import handler_of, routes
    st = build(tmp_path, "c", CLASSFUL)
    r = routes(st)
    assert handler_of(st, r["GET /quotes/"]["id"]) == ["method:web.app.QuotesView.index"]
    assert handler_of(st, r["GET /quotes/{id}/"]["id"]) == ["method:web.app.QuotesView.get"]
    assert handler_of(st, r["POST /quotes/"]["id"]) == ["method:web.app.QuotesView.post"]
    assert handler_of(st, r["GET /quotes/random/"]["id"]) == ["method:web.app.QuotesView.random"]
    assert {"GET /quotes/word/{word}", "POST /quotes/word/{word}"} <= set(r)
    assert handler_of(st, r["GET /v1/staff/"]["id"]) == ["method:web.app.AdminView.index"]
    assert not [k for k in r if "_helper" in k]


OWN = {
    "requirements.txt": "flask\npytest\n",
    "tests/test_conv.py": '''
        import flask

        def test_custom_converter():
            app = flask.Flask(__name__)

            @app.route("/<list:args>")
            def index(args):
                return "|".join(args)

            client = app.test_client()
            client.get("/1,2,3")

        def test_unrelated():
            app = flask.Flask(__name__)
            app.test_client().get("/4,5")
        ''',
}


def test_all_parameter_rule_of_the_test_itself(tmp_path):
    st = build(tmp_path, "w", OWN)
    by = {s.rsplit(".", 1)[-1]: d for s, d, _a in edges(st, "TEST_HTTP")}
    assert by == {"test_custom_converter": "route:GET /{args}"}


LOOP = {
    "requirements.txt": "flask\n",
    "web/views.py": '''
        from flask import Flask
        from flask.views import View

        app = Flask(__name__)

        class AView(View):
            def dispatch_request(self):
                return "a"

        class BView(View):
            def dispatch_request(self):
                return "b"

        for name, view in [("a", AView), ("b", BView)]:
            app.add_url_rule(f"/{name}", view_func=view.as_view(name))
        ''',
}


def test_views_registered_in_a_loop(tmp_path):
    from test_python_web_routes import handler_of, routes
    st = build(tmp_path, "l", LOOP)
    r = routes(st)
    assert handler_of(st, r["GET /a"]["id"]) == ["method:web.views.AView.dispatch_request"]
    assert handler_of(st, r["GET /b"]["id"]) == ["method:web.views.BView.dispatch_request"]
    assert set(r) == {"GET /a", "GET /b", "GET /static/{filename*}"}           # no unresolved f"/{name}" rule

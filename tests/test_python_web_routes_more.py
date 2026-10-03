"""Python web routes left over from the first pass: routes on an app / router received as a parameter (called with a
known app, or a pytest fixture), Flask MethodView / View `methods`, `add_url_rule` with only an endpoint
(`view_functions`, `@app.endpoint`, werkzeug `Rule` / `Submount`), `subdomain=` / `defaults=`, the built-in static
route, flask-restful / flask-restx resources, FastAPI routers built in other functions, Starlette `Host`,
fastapi-utils `@cbv` / `InferringRouter`, classy-fastapi `Routable`, and what a `Depends()` dependency checks."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import routes as R  # noqa: E402
from test_python_tests import build, edges  # noqa: E402
from test_python_web_routes import handler_of, routes  # noqa: E402

FLASK = {
    "requirements.txt": "flask\n",
    "app/__init__.py": '''
        from flask import Flask
        from app.views import init_app, register_routes

        def create_app():
            app = Flask(__name__, static_url_path="/assets")
            register_routes(app)
            init_app(app)
            return app
        ''',
    "app/views.py": '''
        from flask.views import MethodView, View
        from werkzeug.routing import Rule, Submount

        def register_routes(app):
            @app.route("/ping")
            def ping():
                return "pong"

        class Items(MethodView):
            methods = ["GET"]               # post is defined but not routed

            def get(self):
                return "list"

            def post(self):
                return "new"

        class Report(View):
            methods = ["GET", "POST"]

            def dispatch_request(self):
                return "report"

        def show_index():
            return "index"

        def show_home():
            return "home"

        def init_app(app):
            app.add_url_rule("/items", view_func=Items.as_view("items"))
            app.add_url_rule("/report", view_func=Report.as_view("report"))
            app.add_url_rule("/", endpoint="index")              # its view is set below
            app.view_functions["index"] = show_index
            app.add_url_rule("/start", "home", show_home)
            app.add_url_rule("/home", endpoint="home")           # an alias of the rule above
            app.add_url_rule("/admin", view_func=show_index, subdomain="admin", defaults={"page": 1})
            app.url_map.add(Submount("/docs", [Rule("/intro", endpoint="intro")]))

            @app.endpoint("intro")
            def intro():
                return "intro"
        ''',
    "app/api.py": '''
        from flask import Blueprint
        from flask_restful import Api, Resource

        class Todo(Resource):
            def get(self, todo_id):
                return {}

            def delete(self, todo_id):
                return {}

        bp = Blueprint("api", __name__, url_prefix="/api")
        api = Api(bp)
        api.add_resource(Todo, "/todos/<int:todo_id>", endpoint="todo")
        ''',
    "app/restx.py": '''
        from flask import Flask
        from flask_restx import Api, Resource

        app = Flask(__name__, static_folder=None)
        api = Api(app, prefix="/v2")
        ns = api.namespace("cats", path="/felines")

        @ns.route("/<id>")
        class Cat(Resource):
            def get(self, id):
                return {}

            def put(self, id):
                return {}
        ''',
    "tests/conftest.py": '''
        import pytest
        from app import create_app

        @pytest.fixture
        def app():
            app = create_app()
            yield app

        @pytest.fixture
        def client(app):
            return app.test_client()
        ''',
    "tests/test_extra.py": '''
        import flask

        def test_more(app, client):
            @app.route("/more", methods=["GET", "POST"])
            def more():
                return flask.request.method

            assert client.post("/more").status_code == 200
            assert client.get("/ping").data == b"pong"
            assert client.get("/assets/app.css").status_code == 200
            assert client.get("/home").status_code == 200
            assert client.get("/docs/intro").status_code == 200

        def test_url_defaults(app, client):
            bp = flask.Blueprint("bp", __name__)

            @bp.route("/foo")
            def foo():
                return "foo"

            app.register_blueprint(bp, url_prefix="/1")
            app.register_blueprint(bp, name="bp2", url_prefix="/2")
            assert client.get("/1/foo").status_code == 200
            assert client.get("/2/foo").status_code == 200
        ''',
}


def test_flask_routes_on_parameters_and_fixtures(tmp_path):
    st = build(tmp_path, "fl", FLASK)
    r = routes(st)
    # `def register_routes(app)` called from the factory, and a test adding a route to the `app` fixture
    assert handler_of(st, r["GET /ping"]["id"]) == ["function:app.views.register_routes"]
    assert r["GET /ping"]["entry_kind"] == "http_route" and r["GET /ping"]["mounted"] is True
    assert {"GET /more", "POST /more"} <= set(r)
    # a handler in test code: the route -> handler edge becomes TEST_CALLS (test code stays out of the app graph)
    assert [d for _s, d, _a in edges(st, "TEST_CALLS", src=r["POST /more"]["id"])] == ["function:tests.test_extra.test_more"]
    # a blueprint registered twice, under two prefixes and names
    assert {"GET /1/foo", "GET /2/foo"} <= set(r)
    fl = st.stats["plugins"]["python/flask"]
    assert fl["param_objects"] >= 4 and fl["param_objects_bound"] >= 2 and fl["param_objects_from_fixture"] >= 2
    h = st.stats["plugins"]["python"]["tests"]["http"]
    assert h["requests"] == 7 and h["matched"] == 7, h


def test_flask_method_views_endpoints_and_rule_options(tmp_path):
    st = build(tmp_path, "fl", FLASK)
    r = routes(st)
    assert "GET /items" in r and "POST /items" not in r                       # MethodView `methods` override
    assert handler_of(st, r["GET /items"]["id"]) == ["method:app.views.Items.get"]
    assert handler_of(st, r["POST /report"]["id"]) == ["method:app.views.Report.dispatch_request"]
    assert handler_of(st, r["GET /"]["id"]) == ["function:app.views.show_index"]           # view_functions[...]
    assert r["GET /"]["endpoint_alias"] is True
    assert handler_of(st, r["GET /home"]["id"]) == ["function:app.views.show_home"]        # alias of /start
    assert handler_of(st, r["GET /docs/intro"]["id"]) == ["function:app.views.init_app"]   # @app.endpoint
    adm = r["GET /admin"]
    assert adm["subdomain"] == "admin" and adm["defaults"] == ["page"]
    st_route = r["GET /assets/{filename}"]                                     # built-in static route
    assert st_route["static"] is True and st_route["name"] == "static"
    assert "GET /static/{filename}" not in r


def test_flask_restful_and_restx_resources(tmp_path):
    st = build(tmp_path, "fl", FLASK)
    r = routes(st)
    assert handler_of(st, r["DELETE /api/todos/{todo_id}"]["id"]) == ["method:app.api.Todo.delete"]
    assert r["GET /api/todos/{todo_id}"]["name"] == "api.todo"
    assert handler_of(st, r["PUT /v2/felines/{id}"]["id"]) == ["method:app.restx.Cat.put"]
    assert "GET /v2/felines/{id}" in r and "POST /v2/felines/{id}" not in r


FASTAPI = {
    "requirements.txt": "fastapi\n",
    "svc/__init__.py": "",
    "svc/deps.py": '''
        from fastapi import Depends, Header, HTTPException, status
        from fastapi.security import APIKeyHeader, OAuth2PasswordBearer

        oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
        optional_key = APIKeyHeader(name="X-Key", auto_error=False)

        def check_header(x_signature_value: str = Header()):
            if x_signature_value != "ok":
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
            return x_signature_value

        def get_account(sig=Depends(check_header)):
            return {"sig": sig}

        def read_locale(accept_language: str = Header("en")):
            return accept_language

        def find_team(team: int = Header(0)):
            exc = HTTPException(status_code=404)
            if not team:
                raise exc
            return team

        def current_owner(token: str = Depends(oauth2_scheme)):
            return token
        ''',
    "svc/routes.py": '''
        from fastapi import Depends, FastAPI
        from svc.deps import find_team, get_account, optional_key, read_locale

        def register_routes(app: FastAPI):
            @app.get("/status")
            def status_():
                return {}

        def include_extra(router):
            @router.get("/extra", dependencies=[Depends(get_account)])
            def extra():
                return {}

            @router.get("/locale")
            def locale(lang=Depends(read_locale), team=Depends(find_team)):
                return {}

            @router.get("/opt")
            def opt(key=Depends(optional_key)):
                return {}
        ''',
    "svc/sub.py": '''
        from fastapi import Depends, FastAPI
        from svc.deps import current_owner

        def make_admin():
            admin = FastAPI()

            @admin.get("/dashboard")
            def dashboard(owner=Depends(current_owner)):
                return {}
            return admin
        ''',
    "svc/cbv.py": '''
        from fastapi_utils.cbv import cbv
        from fastapi_utils.inferring_router import InferringRouter

        router = InferringRouter()

        @cbv(router)
        class ItemsView:
            @router.get("/items")
            def list_items(self):
                return []
        ''',
    "svc/classy.py": '''
        from classy_fastapi import Routable, get, post

        class Users(Routable):
            @get("/users")
            def all(self):
                return []

            @post("/users")
            def add(self):
                return {}
        ''',
    "svc/star.py": '''
        from starlette.applications import Starlette
        from starlette.routing import Host, Mount, Route

        async def api_home(request):
            return None

        async def www_home(request):
            return None

        site = Starlette(routes=[
            Host("api.example.org", routes=[Route("/home", api_home)]),
            Mount("/www", routes=[Route("/", www_home)]),
        ])
        ''',
    "svc/main.py": '''
        from fastapi import APIRouter, FastAPI
        from svc import cbv
        from svc.classy import Users
        from svc.routes import include_extra, register_routes
        from svc.sub import make_admin

        app = FastAPI()
        register_routes(app)
        extra = APIRouter(prefix="/x")
        include_extra(extra)
        app.include_router(extra)
        app.mount("/admin", make_admin())
        app.include_router(cbv.router)
        app.include_router(Users().router, prefix="/c")

        api = FastAPI()

        @api.get("/v")
        def version():
            return {}

        app.host("api.example.com", api)
        ''',
}


def test_fastapi_parameters_sub_apps_class_routers_and_hosts(tmp_path):
    st = build(tmp_path, "fa", FASTAPI)
    r = routes(st)
    assert handler_of(st, r["GET /status"]["id"]) == ["function:svc.routes.register_routes"]
    assert {"GET /x/extra", "GET /x/locale", "GET /x/opt"} <= set(r), sorted(r)
    assert handler_of(st, r["GET /admin/dashboard"]["id"]) == ["function:svc.sub.make_admin"]  # mount(make_admin())
    assert handler_of(st, r["GET /items"]["id"]) == ["method:svc.cbv.ItemsView.list_items"]    # @cbv + InferringRouter
    assert handler_of(st, r["POST /c/users"]["id"]) == ["method:svc.classy.Users.add"]         # Routable
    assert r["GET /v"]["host"] == "api.example.com" and r["GET /v"]["entry_kind"] == "http_route"
    assert r["GET /home"]["host"] == "api.example.org" and "GET /api.example.org/home" not in r  # Starlette Host
    assert "GET /www/" in r and "host" not in r["GET /www/"]


def test_depends_checks_are_evaluated(tmp_path):
    st = build(tmp_path, "fa", FASTAPI)
    r = routes(st)
    acc = {a["name"]: a for a in r["GET /x/extra"]["access"]}
    chk = acc["get_account"]["checks"]
    assert chk["effect"] == "rejects" and chk["rejects_via"] == "check_header" and chk["nested"] == ["check_header"]
    loc = {a["name"]: a["checks"] for a in r["GET /x/locale"]["access"]}       # nested def params count
    assert loc["read_locale"] == {"reads": ["header accept_language"], "effect": "reads"}
    assert loc["find_team"]["effect"] == "raises" and loc["find_team"]["raises_status"] == [404]   # exc variable
    opt = r["GET /x/opt"]["access"][0]["checks"]
    assert opt == {"scheme": "APIKeyHeader", "effect": "reads"}               # auto_error=False
    dash = r["GET /admin/dashboard"]["access"][0]["checks"]
    assert dash["rejects_via"] == "oauth2_scheme" and dash["effect"] == "rejects"
    rep = R.routes_report(st)
    by = {x["route"]: x for x in rep["items"]}
    g = {x["name"]: x for x in by["route:GET /x/extra"]["guards"]}
    assert g["get_account"]["auth"] is True and g["get_account"]["auth_by"] == "dependency check (via check_header)"
    assert not any(x["auth"] for x in by["route:GET /x/locale"]["guards"])
    assert not any(x["auth"] for x in by["route:GET /x/opt"]["guards"])

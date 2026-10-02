"""FastAPI / Starlette / Flask routes: app / router / blueprint objects, include_router / register_blueprint prefixes,
path parameters, Depends() / Security() and Flask decorators as route access, Starlette Route lists, route nodes with
ROUTES_TO, TEST_HTTP from TestClient / app.test_client() requests (f-string prefixes from settings, url_for /
url_path_for names) and `cg tests <handler>`. Fixtures are written from scratch in a temp dir."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import query as Q  # noqa: E402
from codegraph.plugins.python.plugin import parse_source  # noqa: E402
from test_python_tests import build, edges, names  # noqa: E402

FASTAPI = {
    "requirements.txt": "fastapi\n",
    "app/__init__.py": "", "app/api/__init__.py": "", "app/api/routes/__init__.py": "",
    "app/config.py": '''
        class Settings:
            API_V1_STR: str = "/api/v1"

        settings = Settings()
        ''',
    "app/deps.py": '''
        from typing import Annotated
        from fastapi import Depends, Security

        def get_current_user():
            return 1

        def require_scope():
            return True

        CurrentUser = Annotated[int, Depends(get_current_user)]
        ''',
    "app/api/routes/items.py": '''
        from fastapi import APIRouter, Depends
        from app.deps import CurrentUser, require_scope

        router = APIRouter(prefix="/items", tags=["items"])

        @router.get("/{item_id}")
        def read_item(item_id: int, user: CurrentUser):
            return {"id": item_id}

        @router.post("/", dependencies=[Depends(require_scope)])
        def create_item():
            return {}

        @router.api_route("/{item_id}/sync", methods=["PUT", "PATCH"])
        async def sync_item(item_id: int):
            return {}

        def legacy():
            return {}

        router.add_api_route("/legacy", legacy, methods=["GET"])
        ''',
    "app/api/main.py": '''
        from fastapi import APIRouter
        from app.api.routes import items

        api_router = APIRouter()
        api_router.include_router(items.router)
        ''',
    "app/main.py": '''
        from fastapi import FastAPI
        from app.api.main import api_router
        from app.config import settings

        app = FastAPI()
        app.include_router(api_router, prefix=settings.API_V1_STR)

        @app.get("/health", name="health")
        async def health():
            return "ok"

        @app.websocket("/ws")
        async def ws(socket):
            pass
        ''',
    "app/orphan.py": '''
        from fastapi import APIRouter
        router = APIRouter()

        @router.get("/never-mounted")
        def orphan():
            return 1
        ''',
    "tests/__init__.py": "",
    "tests/test_items.py": '''
        from fastapi.testclient import TestClient
        from app.main import app
        from app.config import settings

        client = TestClient(app)

        def test_read_item():
            r = client.get(f"{settings.API_V1_STR}/items/1")
            assert r.status_code == 200

        def test_create():
            TestClient(app).post("/api/v1/items/", json={})

        def test_health():
            assert client.get(app.url_path_for("health")).status_code == 200
        ''',
}

FLASK = {
    "pyproject.toml": "[project]\nname = 'flaskr'\ndependencies = ['flask']\n",
    "flaskr/__init__.py": '''
        from flask import Flask

        def create_app():
            app = Flask(__name__)
            from . import auth, blog, admin
            app.register_blueprint(auth.bp)
            app.register_blueprint(blog.bp)
            app.register_blueprint(admin.bp, url_prefix="/staff")

            @app.route("/hello")
            def hello():
                return "Hello"
            return app
        ''',
    "flaskr/auth.py": '''
        import functools
        from flask import Blueprint, url_for, redirect

        bp = Blueprint("auth", __name__, url_prefix="/auth")

        def login_required(view):
            @functools.wraps(view)
            def wrapped(**kw):
                return view(**kw)
            return wrapped

        @bp.route("/register", methods=("GET", "POST"))
        def register():
            return redirect(url_for("auth.login"))

        @bp.route("/login", methods=("GET", "POST"))
        def login():
            return ""
        ''',
    "flaskr/blog.py": '''
        from flask import Blueprint
        from flaskr.auth import login_required

        bp = Blueprint("blog", __name__)

        @bp.route("/")
        def index():
            return ""

        @bp.route("/<int:id>/update", methods=("GET", "POST"))
        @login_required
        def update(id):
            return ""
        ''',
    "flaskr/admin.py": '''
        from flask import Blueprint
        from flask.views import MethodView

        bp = Blueprint("admin", __name__, url_prefix="/admin")

        class Users(MethodView):
            def get(self):
                return ""

            def post(self):
                return ""

        bp.add_url_rule("/users", view_func=Users.as_view("users"))
        ''',
    "tests/conftest.py": '''
        import pytest
        from flaskr import create_app

        @pytest.fixture
        def app():
            return create_app()

        @pytest.fixture
        def client(app):
            return app.test_client()
        ''',
    "tests/test_auth.py": '''
        from flask import url_for

        def test_register(client, app):
            assert client.get("/auth/register").status_code == 200
            with app.test_request_context():
                client.post(url_for("auth.login"), data={})

        def test_update(client):
            client.post("/1/update")

        def test_index(client):
            client.get("/")

        def test_hello(client):
            client.get("/hello")

        def test_users(client):
            client.post("/staff/users")
        ''',
}


def routes(st):
    out = {}
    for r in st.q("SELECT id, name, entry_kind, attrs FROM nodes WHERE kind='route'"):
        a = json.loads(r["attrs"] or "{}")
        out[r["name"]] = dict(a, id=r["id"], entry_kind=r["entry_kind"])
    return out


def handler_of(st, rid):
    return [d for _s, d, _a in edges(st, "ROUTES_TO", src=rid)]


def test_fastapi_routers_prefixes_params_and_test_requests(tmp_path):
    st = build(tmp_path, "fa", FASTAPI)
    r = routes(st)
    assert {"GET /api/v1/items/{item_id}", "POST /api/v1/items/", "PUT /api/v1/items/{item_id}/sync",
            "PATCH /api/v1/items/{item_id}/sync", "GET /api/v1/items/legacy", "GET /health", "WS /ws",
            "GET /never-mounted"} <= set(r)
    ri = r["GET /api/v1/items/{item_id}"]
    assert handler_of(st, ri["id"]) == ["function:app.api.routes.items.read_item"]
    assert ri["path_params"] == ["item_id"] and ri["entry_kind"] == "http_route" and ri["framework"] == "fastapi"
    assert [a["name"] for a in ri["access"]] == ["get_current_user"]
    assert [a["name"] for a in r["POST /api/v1/items/"]["access"]] == ["require_scope"]
    assert handler_of(st, r["GET /api/v1/items/legacy"]["id"]) == ["function:app.api.routes.items.legacy"]
    assert r["WS /ws"]["entry_kind"] == "websocket"
    assert r["GET /never-mounted"]["entry_kind"] is None and r["GET /never-mounted"]["mounted"] is False
    assert r["GET /health"]["name"] == "health"
    h = st.stats["plugins"]["python"]["tests"]["http"]
    assert h["requests"] == 3 and h["matched"] == 3, h
    th = {(s, d) for s, d, _a in edges(st, "TEST_HTTP")}
    assert ("function:tests.test_items.test_read_item", ri["id"]) in th
    assert ("function:tests.test_items.test_health", r["GET /health"]["id"]) in th
    res = Q.tests_covering(st, "app.api.routes.items.read_item")
    assert names(res, "transitive") == ["test_read_item"]
    assert names(Q.tests_covering(st, "app.api.routes.items.create_item"), "transitive") == ["test_create"]
    assert st.stats["plugins"]["python/fastapi"]["routes"] >= 8
    # the decorator references no longer count as an unmodelled route framework
    cov = st.meta()["stats"]["coverage"]
    assert "languages" in cov and not [b for b in cov.get("blind_spots") or [] if b["kind"] == "python_decorator_routes"]


def test_flask_blueprints_url_prefix_url_for_and_method_views(tmp_path):
    st = build(tmp_path, "fl", FLASK)
    r = routes(st)
    assert {"GET /auth/register", "POST /auth/register", "GET /auth/login", "POST /auth/login", "GET /",
            "GET /{id}/update", "POST /{id}/update", "GET /hello", "GET /staff/users", "POST /staff/users"} <= set(r)
    assert not any(k.endswith("/admin/users") for k in r)          # register_blueprint(url_prefix=) replaces it
    login = r["POST /auth/login"]
    assert login["name"] == "auth.login" and handler_of(st, login["id"]) == ["function:flaskr.auth.login"]
    assert [a["name"] for a in r["POST /{id}/update"]["access"]] == ["login_required"]
    assert handler_of(st, r["POST /staff/users"]["id"]) == ["method:flaskr.admin.Users.post"]
    assert handler_of(st, r["GET /hello"]["id"]) == ["function:flaskr.create_app"]   # nested view: its factory
    h = st.stats["plugins"]["python"]["tests"]["http"]
    assert h["requests"] == 6 and h["matched"] == 6, h
    assert names(Q.tests_covering(st, "flaskr.auth.login"), "transitive") == ["test_register"]
    assert names(Q.tests_covering(st, "flaskr.blog.index"), "transitive") == ["test_index"]
    assert names(Q.tests_covering(st, "flaskr.admin.Users.post"), "transitive") == ["test_users"]


def test_starlette_route_lists_and_mounts(tmp_path):
    st = build(tmp_path, "sl", {
        "requirements.txt": "starlette\n",
        "web/__init__.py": "",
        "web/app.py": '''
            from starlette.applications import Starlette
            from starlette.routing import Route, Mount, WebSocketRoute

            async def homepage(request):
                return None

            async def user(request):
                return None

            async def feed(ws):
                pass

            users = [Route("/{username:str}", user, methods=["GET", "DELETE"])]
            app = Starlette(routes=[
                Route("/", homepage),
                Mount("/users", routes=users),
                WebSocketRoute("/feed", feed),
            ])
            ''',
    })
    r = routes(st)
    assert {"GET /", "GET /users/{username}", "DELETE /users/{username}", "WS /feed"} <= set(r), sorted(r)
    assert handler_of(st, r["DELETE /users/{username}"]["id"]) == ["function:web.app.user"]
    assert r["GET /"]["framework"] == "starlette"


def test_unrelated_project_gets_no_web_plugin(tmp_path):
    st = build(tmp_path, "plain", {
        "pyproject.toml": "[project]\nname = 'plain'\n",
        "plain/__init__.py": "",
        "plain/util.py": '''
            class Router:
                def get(self, path):
                    return lambda f: f

            router = Router()

            @router.get("/x")
            def x():
                return 1
            ''',
        # a test that writes a FastAPI / Flask fixture does not make the project one
        "tests/test_gen.py": 'SRC = """\nfrom fastapi import FastAPI\nfrom flask import Flask\n"""\n',
    })
    assert not routes(st)
    assert "python/fastapi" not in st.stats["plugins"] and "python/flask" not in st.stats["plugins"]


def test_pep758_unparenthesized_except_parses():
    src = "try:\n    pass\nexcept ValueError, TypeError:\n    pass\n"
    tree = parse_source(src)
    assert tree.body[0].handlers[0].lineno == 3

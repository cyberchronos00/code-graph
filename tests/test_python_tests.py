"""pytest / unittest in the tests feature: discovery (defaults, pytest config, testpaths, conftest, pytest_plugins),
test cases (functions, classes, inherited methods, parametrize, marks, unittest / Django / DRF TestCase with setUp),
fixtures (nested conftest, overrides, autouse, chains, usefixtures, getfixturevalue), HTTP test requests linked to
routes (Django client + reverse(), DRF APIClient, FastAPI TestClient, Flask test_client), isolation from the application
graph and the tests query. Every fixture is written from scratch in a temp dir, except the bundled Django sample."""
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.model import PROPAGATING  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.coverage import render as render_coverage, for_graph  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402


def write(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    return root


def build(tmp: Path, name: str, files: dict):
    root = write(tmp / name, files)
    db = tmp / f"{name}.db"
    stats = index_project(root, db, name)
    st = GraphStore(db)
    st.stats = stats
    return st


def case_nodes(st):
    return {r["id"]: dict(r, attrs=json.loads(r["attrs"] or "{}")) for r in st.q("SELECT * FROM nodes WHERE kind='test'")}


def edges(st, kind, src=None, dst=None):
    q, a = "SELECT src, dst, attrs FROM edges WHERE kind=?", [kind]
    if src:
        q += " AND src=?"; a.append(src)
    if dst:
        q += " AND dst=?"; a.append(dst)
    return [(r["src"], r["dst"], json.loads(r["attrs"] or "{}")) for r in st.q(q, a)]


def names(res, key):
    return sorted(t["name"] for t in res[key])


REPRO = {
    "checks.py": '''
        def check_size(item):
            return item["size"] < 10

        def validate(item):
            return check_size(item)
        ''',
    "tests/test_checks.py": '''
        import pytest
        from checks import check_size, validate

        @pytest.fixture
        def item():
            return {"size": 1}

        def test_validate_accepts_small_items(item):
            assert validate(item)

        @pytest.mark.parametrize("size", [10, 20])
        def test_check_size_rejects_large(size):
            assert not check_size({"size": size})
        ''',
}


def test_issue_repro_tests_and_impact(tmp_path):
    st = build(tmp_path, "checks", REPRO)
    v = Q.tests_covering(st, "checks.validate")
    assert names(v, "direct") == ["test_validate_accepts_small_items"] and v["transitive"] == []
    c = Q.tests_covering(st, "checks.check_size")
    assert names(c, "direct") == ["test_check_size_rejects_large"]
    assert names(c, "transitive") == ["test_validate_accepts_small_items"]
    assert c["stats"]["tests_by_framework"] == {"pytest": 2}
    assert "of 2 test cases in the graph: pytest 2" in Q.render_tests_covering(c)
    imp = Q.impact(st, "checks.validate")
    assert imp["callers"] == [] and imp["entry_points"] == []
    assert "Only test code uses it" in Q.explain_no_callers(st, "checks.validate", imp["targets"])
    t = case_nodes(st)["test:tests.test_checks.test_check_size_rejects_large"]
    assert t["entry_kind"] == "test" and t["attrs"]["framework"] == "pytest"
    assert t["attrs"]["params"] == [{"names": ["size"], "cases": 2, "values": ["10", "20"]}] and t["attrs"]["cases"] == 2
    # the fixture the test requests
    assert edges(st, "TEST_USES", src="test:tests.test_checks.test_validate_accepts_small_items") == [
        ("test:tests.test_checks.test_validate_accepts_small_items", "function:tests.test_checks.item",
         {"via": "fixture", "fixture": "item"})]
    # nothing from test code propagates
    test_code = {r["id"] for r in st.q("SELECT id FROM nodes WHERE json_extract(attrs, '$.test')")}
    assert "module:tests.test_checks" in test_code
    assert not [r for r in st.q("SELECT src, kind FROM edges") if r["src"] in test_code and r["kind"] in PROPAGATING]


def test_pytest_classes_inheritance_parametrize_and_marks(tmp_path):
    st = build(tmp_path, "classes", {
        "shop/__init__.py": "",
        "shop/cart.py": '''
            class Cart:
                def __init__(self):
                    self.items = []

                def add(self, sku, qty=1):
                    self.items.append((sku, qty))

                def total(self):
                    return sum(q for _, q in self.items)
            ''',
        "tests/test_cart.py": '''
            import pytest
            from shop.cart import Cart

            class CartContract:
                def test_starts_empty(self):
                    assert Cart().total() == 0

            @pytest.mark.slow
            class TestCart(CartContract):
                @pytest.mark.parametrize("sku,qty", [("a", 1), ("b", 2)], ids=["one", "two"])
                def test_add(self, sku, qty):
                    c = Cart()
                    c.add(sku, qty)
                    assert c.total() == qty

                @pytest.mark.skip(reason="later")
                def test_skipped(self):
                    pass

                def helper(self):
                    return Cart()

                class TestNested:
                    def test_inner(self):
                        assert Cart().items == []

            class TestWithInit:
                def __init__(self):
                    self.x = 1

                def test_never_collected(self):
                    pass

            class TestDisabled:
                __test__ = False

                def test_off(self):
                    pass
            ''',
    })
    t = case_nodes(st)
    assert set(t) == {"test:tests.test_cart.TestCart.test_starts_empty", "test:tests.test_cart.TestCart.test_add",
                      "test:tests.test_cart.TestCart.test_skipped", "test:tests.test_cart.TestCart.TestNested.test_inner"}
    add = t["test:tests.test_cart.TestCart.test_add"]
    assert add["name"] == "TestCart.test_add"
    assert add["attrs"]["params"] == [{"names": ["sku", "qty"], "cases": 2, "ids": ["one", "two"]}]
    assert add["attrs"]["marks"] == ["slow"]
    assert t["test:tests.test_cart.TestCart.test_skipped"]["attrs"]["marks"] == ["skip", "slow"]
    inh = t["test:tests.test_cart.TestCart.test_starts_empty"]
    assert inh["attrs"]["inherited_from"] == "tests.test_cart.CartContract"
    assert edges(st, "TEST_CALLS", src=inh["id"])[0][1] == "method:tests.test_cart.CartContract.test_starts_empty"
    res = Q.tests_covering(st, "shop.cart.Cart.total")
    assert names(res, "direct") == ["TestCart.test_add", "TestCart.test_starts_empty"]
    assert Q.impact(st, "shop.cart.Cart.add")["callers"] == []


CONFTEST_FILES = {
    "app/__init__.py": "",
    "app/db.py": '''
        def connect():
            return object()

        def reset(conn):
            return conn

        def seed(conn):
            return conn
        ''',
    "app/api.py": '''
        def make_client(conn):
            return conn

        def login(client):
            return client
        ''',
    "conftest.py": '''
        import pytest
        from app.db import connect, reset

        @pytest.fixture(scope="session")
        def conn():
            return connect()

        @pytest.fixture(autouse=True)
        def clean_db(conn):
            yield
            reset(conn)

        @pytest.fixture
        def client(conn):
            from app.api import make_client
            return make_client(conn)
        ''',
    "tests/__init__.py": "",
    "tests/api/__init__.py": "",
    "tests/api/conftest.py": '''
        import pytest
        from app.api import login

        @pytest.fixture
        def client(client):
            return login(client)

        @pytest.fixture(params=["admin", "staff"])
        def role(request):
            return request.param
        ''',
    "tests/api/test_users.py": '''
        import pytest
        from app.db import seed

        @pytest.fixture
        def seeded(conn):
            return seed(conn)

        def test_list(client, role):
            assert client

        @pytest.mark.usefixtures("seeded")
        def test_seeded():
            pass

        def test_dynamic(request):
            request.getfixturevalue("seeded")

        class TestWithAutouse:
            @pytest.fixture(autouse=True)
            def _setup(self, seeded):
                self.data = seeded

            def test_uses_class_autouse(self):
                assert self.data
        ''',
    "tests/test_plain.py": '''
        def test_root_client(client):
            assert client
        ''',
}


def test_fixtures_nested_conftest_override_autouse_chains(tmp_path):
    st = build(tmp_path, "fx", CONFTEST_FILES)

    def uses(tid):
        return sorted((d, a["via"]) for _, d, a in edges(st, "TEST_USES", src=tid))
    assert uses("test:tests.api.test_users.test_list") == [
        ("function:conftest.clean_db", "autouse fixture"), ("function:tests.api.conftest.client", "fixture"),
        ("function:tests.api.conftest.role", "fixture")]
    assert uses("test:tests.test_plain.test_root_client") == [
        ("function:conftest.clean_db", "autouse fixture"), ("function:conftest.client", "fixture")]
    assert ("function:tests.api.test_users.seeded", "fixture") in uses("test:tests.api.test_users.test_seeded")
    assert ("function:tests.api.test_users.seeded", "fixture") in uses("test:tests.api.test_users.test_dynamic")
    assert ("method:tests.api.test_users.TestWithAutouse._setup", "autouse fixture") in \
        uses("test:tests.api.test_users.TestWithAutouse.test_uses_class_autouse")
    # the overriding fixture reaches the outer one of the same name; chains go to conftest at the root
    assert ("function:tests.api.conftest.client", "function:conftest.client") in \
        {(s, d) for s, d, _ in edges(st, "TEST_USES")}
    assert ("function:conftest.client", "function:conftest.conn") in {(s, d) for s, d, _ in edges(st, "TEST_USES")}
    role = st.node("function:tests.api.conftest.role")
    assert role is not None
    # fixture bodies count for the test: connect() runs for every test through the conn fixture chain
    res = Q.tests_covering(st, "app.db.connect")
    assert "test_list" in names(res, "direct") and "test_root_client" in names(res, "direct")
    login = Q.tests_covering(st, "app.api.login")
    assert names(login, "direct") == ["test_list"]
    seed = Q.tests_covering(st, "app.db.seed")
    assert names(seed, "direct") == ["TestWithAutouse.test_uses_class_autouse", "test_dynamic", "test_seeded"]
    # application code called only by fixtures has no callers
    assert Q.impact(st, "app.db.reset")["callers"] == []
    ts = st.stats["plugins"]["python"]["tests"]
    assert ts["fixtures"] == 7 and ts["cases"] == {"pytest": 5} and ts["test_files"]["conftest"] == 2


def test_unittest_and_setup_methods(tmp_path):
    st = build(tmp_path, "ut", {
        "calc/__init__.py": "",
        "calc/ops.py": '''
            def add(a, b):
                return a + b

            def prepare():
                return {}

            def connect():
                return 1
            ''',
        "tests/__init__.py": "",
        "tests/base.py": '''
            import unittest
            from calc.ops import connect

            class DbTestCase(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    cls.conn = connect()
            ''',
        "tests/test_ops.py": '''
            import unittest
            from calc.ops import add, prepare
            from tests.base import DbTestCase

            def setUpModule():
                prepare()

            class OpsTest(DbTestCase):
                def setUp(self):
                    self.state = prepare()

                def test_add(self):
                    self.assertEqual(add(1, 2), 3)

                def testShortName(self):
                    self.assertTrue(True)

                def helper(self):
                    return add(0, 0)

            if __name__ == "__main__":
                unittest.main()
            ''',
    })
    t = case_nodes(st)
    assert set(t) == {"test:tests.test_ops.OpsTest.test_add", "test:tests.test_ops.OpsTest.testShortName"}
    assert t["test:tests.test_ops.OpsTest.test_add"]["attrs"]["framework"] == "unittest"
    hops = {(d, a.get("via")) for _, d, a in edges(st, "TEST_CALLS", src="test:tests.test_ops.OpsTest.test_add")}
    assert {("method:tests.test_ops.OpsTest.test_add", "test method"), ("method:tests.test_ops.OpsTest.setUp", "setUp"),
            ("method:tests.base.DbTestCase.setUpClass", "setUpClass"),
            ("function:tests.test_ops.setUpModule", "setUpModule")} <= hops
    assert names(Q.tests_covering(st, "calc.ops.connect"), "direct") == ["OpsTest.testShortName", "OpsTest.test_add"]
    assert Q.impact(st, "calc.ops.prepare")["callers"] == []
    # the `python tests/test_ops.py` block is a test entry, not a runtime `main`
    scripts = st.q("SELECT entry_kind FROM nodes WHERE kind='script'")
    assert [r["entry_kind"] for r in scripts] == ["test"]


DJANGO_FILES = {
    "manage.py": "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'site_cfg.settings')\n",
    "requirements.txt": "Django>=4.2\ndjangorestframework>=3.14\n",
    "site_cfg/__init__.py": "",
    "site_cfg/settings.py": "ROOT_URLCONF = 'site_cfg.urls'\nINSTALLED_APPS = ['notes']\n",
    "site_cfg/urls.py": '''
        from django.urls import include, path
        from notes import views
        from notes.api import router

        urlpatterns = [
            path("notes/", views.note_list, name="notes-page"),
            path("notes/<int:pk>/", views.note_detail, name="notes-item"),
            path("board/", include(("notes.board_urls", "board"), namespace="board")),
            path("api/", include(router.urls)),
        ]
        ''',
    "notes/__init__.py": "",
    "notes/models.py": '''
        from django.db import models

        class Note(models.Model):
            text = models.TextField()
        ''',
    "notes/views.py": '''
        from django.http import JsonResponse

        def load_notes():
            return []

        def note_list(request):
            return JsonResponse({"notes": load_notes()})

        def note_detail(request, pk):
            return JsonResponse({"pk": pk})

        def board(request):
            return JsonResponse({})
        ''',
    "notes/board_urls.py": '''
        from django.urls import path
        from . import views

        urlpatterns = [path("", views.board, name="index")]
        ''',
    "notes/api.py": '''
        from rest_framework import routers, viewsets
        from rest_framework.decorators import action
        from rest_framework.response import Response
        from .models import Note

        class NoteViewSet(viewsets.ModelViewSet):
            queryset = Note.objects.all()

            @action(detail=True, methods=["post"])
            def pin_note(self, request, pk=None):
                return Response({})

        router = routers.DefaultRouter()
        router.register(r"notes", NoteViewSet)
        ''',
    "notes/tests.py": '''
        from django.test import TestCase
        from django.urls import reverse

        NOTES = "/notes/"

        class NoteViewsTest(TestCase):
            def setUp(self):
                self.detail = reverse("notes-item", kwargs={"pk": 1})

            def test_list(self):
                self.assertEqual(self.client.get(reverse("notes-page")).status_code, 200)

            def test_list_by_constant(self):
                self.client.get(NOTES)

            def test_detail(self):
                self.client.get(self.detail)

            def test_detail_fstring(self):
                pk = 3
                self.client.get(f"/notes/{pk}/")

            def test_board(self):
                url = reverse("board:index")
                self.client.get(url)

            def test_wrong_verb(self):
                self.client.generic("PUT", "/nowhere/")
        ''',
    "notes/test_api.py": '''
        import pytest
        from django.urls import reverse
        from rest_framework.test import APIClient

        @pytest.fixture
        def api():
            return APIClient()

        def test_pin(api):
            api.post(reverse("note-pin-note", args=[1]))

        def test_list_api():
            client = APIClient()
            client.get("/api/notes/")
        ''',
}


def test_django_client_reverse_and_drf_apiclient(tmp_path):
    st = build(tmp_path, "dj", DJANGO_FILES)
    http = {(s.split(":", 1)[1], d) for s, d, _ in edges(st, "TEST_HTTP")}
    assert http == {
        ("notes.tests.NoteViewsTest.test_list", "route:ANY /notes/"),
        ("notes.tests.NoteViewsTest.test_list_by_constant", "route:ANY /notes/"),
        ("notes.tests.NoteViewsTest.test_detail", "route:ANY /notes/{pk}/"),
        ("notes.tests.NoteViewsTest.test_detail_fstring", "route:ANY /notes/{pk}/"),
        ("notes.tests.NoteViewsTest.test_board", "route:ANY /board/"),
        ("notes.test_api.test_pin", "route:POST /api/notes/{pk}/pin_note/"),
        ("notes.test_api.test_list_api", "route:GET /api/notes/"),
    }
    h = st.stats["plugins"]["python"]["tests"]["http"]
    assert h["requests"] == 8 and h["matched"] == 7 and h["unmatched"] == 1
    res = Q.tests_covering(st, "notes.views.load_notes")
    assert names(res, "transitive") == ["NoteViewsTest.test_list", "NoteViewsTest.test_list_by_constant"]
    assert names(Q.tests_covering(st, "route:ANY /notes/"), "direct") == ["NoteViewsTest.test_list",
                                                                         "NoteViewsTest.test_list_by_constant"]
    # Django tests.py is test code even though its name is not a pytest default
    assert case_nodes(st)["test:notes.tests.NoteViewsTest.test_list"]["attrs"]["testcase"] == "TestCase"
    # DRF route names: default basename from the queryset model, extra actions as <basename>-<url_name>
    rn = {json.loads(r["attrs"])["name"] for r in st.q("SELECT attrs FROM nodes WHERE kind='route'")}
    assert {"note-list", "note-detail", "note-pin-note"} <= rn


def test_fastapi_and_flask_clients_are_recorded(tmp_path):
    st = build(tmp_path, "asgi", {
        "pyproject.toml": "[project]\nname = 'svc'\n",
        "svc/__init__.py": "",
        "svc/main.py": '''
            from fastapi import FastAPI
            app = FastAPI()

            @app.get("/items/{item_id}")
            def read_item(item_id: int):
                return {"id": item_id}
            ''',
        "svc/web.py": '''
            from flask import Flask
            web = Flask(__name__)
            ''',
        "tests/conftest.py": '''
            import pytest
            from fastapi.testclient import TestClient
            from svc.main import app

            @pytest.fixture
            def client():
                with TestClient(app) as c:
                    yield c
            ''',
        "tests/test_items.py": '''
            from fastapi.testclient import TestClient
            from svc.main import app, read_item
            from svc.web import web

            def test_read(client):
                assert client.get("/items/1").status_code == 200

            def test_inline():
                TestClient(app).get("/items/2")

            def test_flask():
                with web.test_client() as c:
                    c.get("/health")

            def test_direct():
                assert read_item(3) == {"id": 3}

            def test_not_http():
                cache = {}
                cache.get("/items/1")
            ''',
    })
    h = st.stats["plugins"]["python"]["tests"]["http"]
    assert h["requests"] == 3 and h["matched"] == 2 and h["unmatched_samples"] == ["tests/test_items.py:13 GET /health"]
    res = Q.tests_covering(st, "svc.main.read_item")
    assert names(res, "direct") == ["test_direct"] and names(res, "transitive") == ["test_inline", "test_read"]
    assert not [c for c in Q.impact(st, "svc.main.read_item")["callers"] if c["file"].startswith("tests/")]


def test_pytest_config_testpaths_and_library_test_utils(tmp_path):
    st = build(tmp_path, "cfg", {
        "pyproject.toml": '''
            [project]
            name = "lib"

            [tool.pytest.ini_options]
            python_files = ["check_*.py"]
            python_functions = ["check_", "test_"]
            python_classes = "Checks"
            testpaths = ["testing"]
            ''',
        "src/lib/__init__.py": "",
        "src/lib/core.py": '''
            from lib.test.support import make_fixture

            def run():
                return make_fixture()
            ''',
        "src/lib/test/__init__.py": "",
        "src/lib/test/support.py": '''
            def make_fixture():
                return 1
            ''',
        "testing/helpers.py": '''
            from lib.core import run

            def build():
                return run()
            ''',
        "testing/check_core.py": '''
            from helpers import build

            def check_run():
                assert build() == 1

            def test_also_collected():
                pass

            def helper_not_a_test():
                pass

            class ChecksCore:
                def check_method(self):
                    pass
            ''',
    })
    t = case_nodes(st)
    assert {x["name"] for x in t.values()} == {"check_run", "test_also_collected", "ChecksCore.check_method"}
    ts = st.stats["plugins"]["python"]["tests"]
    assert ts["pytest_config"] == [{"dir": ".", "source": "pyproject.toml [tool.pytest]", "python_files": ["check_*.py"],
                                    "python_classes": ["Checks"], "python_functions": ["check_", "test_"],
                                    "testpaths": ["testing"]}]
    test_code = {r["file"] for r in st.q("SELECT file FROM nodes WHERE kind='module' AND json_extract(attrs, '$.test')")}
    assert test_code == {"testing/helpers.py", "testing/check_core.py"}     # src/lib/test/ is imported by lib.core
    assert names(Q.tests_covering(st, "lib.core.run"), "direct") == ["check_run"]   # through a test helper
    assert [c["fqn"] for c in Q.impact(st, "lib.test.support.make_fixture")["callers"]] == ["lib.core.run"]


def test_testpaths_naming_the_application_package_keeps_app_code(tmp_path):
    st = build(tmp_path, "pkgtp", {
        "pyproject.toml": '''
            [project]
            name = "app"

            [tool.pytest.ini_options]
            testpaths = ["app"]
            ''',
        "app/__init__.py": "",
        "app/orders/__init__.py": "",
        "app/orders/models.py": '''
            def total(items):
                return sum(items)
            ''',
        "app/orders/service.py": '''
            from app.orders.models import total

            def checkout(items):
                return total(items)
            ''',
        "app/conftest.py": '''
            import pytest

            @pytest.fixture
            def items():
                return [1, 2]
            ''',
        "app/orders/tests/__init__.py": "",
        "app/orders/tests/utils.py": "def make():\n    return [3]\n",
        "app/orders/tests/test_orders.py": '''
            from app.orders.service import checkout

            def test_checkout(items):
                assert checkout(items) == 3
            ''',
    })
    test_code = {r["file"] for r in st.q("SELECT file FROM nodes WHERE kind='module' AND json_extract(attrs, '$.test')")}
    assert test_code == {"app/conftest.py", "app/orders/tests/__init__.py", "app/orders/tests/utils.py",
                         "app/orders/tests/test_orders.py"}
    assert [c["fqn"] for c in Q.impact(st, "app.orders.models.total")["callers"]] == ["app.orders.service.checkout"]
    assert edges(st, "CALLS", src="function:app.orders.service.checkout")
    assert names(Q.tests_covering(st, "app.orders.service.checkout"), "direct") == ["test_checkout"]
    assert names(Q.tests_covering(st, "app.orders.models.total"), "transitive") == ["test_checkout"]


def test_pytest_plugins_modules_provide_fixtures(tmp_path):
    st = build(tmp_path, "plug", {
        "app/__init__.py": "",
        "app/svc.py": "def make():\n    return 1\n",
        "app/testing/__init__.py": "",
        "app/testing/fixtures.py": '''
            import pytest
            from app.svc import make

            @pytest.fixture
            def thing():
                return make()
            ''',
        "tests/conftest.py": 'pytest_plugins = ["app.testing.fixtures"]\n',
        "tests/test_thing.py": "def test_thing(thing):\n    assert thing\n",
    })
    assert [d for _, d, _ in edges(st, "TEST_USES", src="test:tests.test_thing.test_thing")] == ["function:app.testing.fixtures.thing"]
    assert Q.impact(st, "app.svc.make")["callers"] == []
    assert names(Q.tests_covering(st, "app.svc.make"), "direct") == ["test_thing"]


def test_functions_referenced_only_from_tests_stay_uncalled_end_to_end(tmp_path):
    """#14 follow-up: tables, callbacks and parametrize arguments in test code are not callers once tests are marked."""
    st = build(tmp_path, "refs", {
        "app/__init__.py": "",
        "app/core.py": "def helper(x):\n    return x\n\ndef other(x):\n    return x\n\ndef used(x):\n    return x\n\n"
                       "def api(x):\n    return used(x)\n",
        "tests/__init__.py": "",
        "tests/test_core.py": '''
            import pytest
            from app.core import helper, other, api

            CASES = [helper]

            @pytest.mark.parametrize("fn", [other])
            def test_param(fn):
                assert fn(1) == 1

            def test_table():
                for case in CASES:
                    case(1)
                assert list(map(helper, [1])) == [1]

            def test_api():
                assert api(1) == 1
            ''',
    })
    for fn in ("app.core.helper", "app.core.other"):
        imp = Q.impact(st, fn)
        assert imp["callers"] == [] and imp["entry_points"] == [], fn
    into = st.q("SELECT kind FROM edges WHERE dst IN ('function:app.core.helper', 'function:app.core.other')")
    assert not [r for r in into if r["kind"] in PROPAGATING]
    assert names(Q.tests_covering(st, "app.core.helper"), "direct") == ["test_table"]
    assert [c["fqn"] for c in Q.impact(st, "app.core.used")["callers"]] == ["app.core.api"]


def test_bundled_django_sample_tests_reach_their_routes():
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="codegraph-djtests-"))
    stats = index_project(ROOT / "examples" / "bookstore-django", d / "dj.db", "bookstore-django")
    st = GraphStore(d / "dj.db")
    http = {d_ for _, d_, _ in edges(st, "TEST_HTTP")}
    assert {"route:ANY /shop/featured/", "route:GET /shop/books/{pk}/", "route:GET /api/books/",
            "route:GET /api/books/{book_id}/", "route:GET /api/books/{book_id}/availability/", "route:POST /api/orders/",
            "route:POST /drf/reviews/{pk}/upvote/", "route:GET /drf/health/", "route:GET /drf/reviews/",
            "route:DELETE /drf/reviews/{pk}/"} == http
    ts = stats["plugins"]["python"]["tests"]
    assert ts["cases"] == {"unittest": 5, "pytest": 5} and ts["http"]["matched"] == ts["http"]["requests"]
    # application code keeps its runtime callers only
    callers = Q.impact(st, "catalog.pricing.line_total")["callers"]
    assert callers and all("/tests/" not in c["file"] for c in callers)
    assert names(Q.tests_covering(st, "catalog.api.place_order"), "transitive") == ["test_order_needs_token"]
    cov = render_coverage(for_graph(st))
    assert "python tests: 10 test cases (pytest 5, unittest 5) in 5 files, 5 fixtures; 9 HTTP test requests, 9 linked to routes" in cov

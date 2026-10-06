"""An application module named tests.py (#49): imported by application code and defining no test case, it stays
application code; a Django app's tests.py stays test code. Fixtures are written from scratch in a temp dir."""
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402


def build(tmp: Path, files: dict, name="app"):
    root = tmp / name
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    db = tmp / f"{name}.db"
    index_project(root, db, name)
    return GraphStore(db)


def callers(st, spec):
    return {c["id"] for c in Q.impact(st, spec)["callers"]}


PKG = {
    "pkg/__init__.py": "",
    "pkg/util.py": "def helper():\n    return 1\n",
    "pkg/tests.py": """
        from pkg.util import helper

        def run():
            return helper()
        """,
    "pkg/main.py": """
        from pkg.tests import run

        def main():
            return run()

        if __name__ == "__main__":
            main()
        """,
}


def test_imported_tests_module_is_application_code(tmp_path):
    st = build(tmp_path, PKG)
    got = callers(st, "pkg.util.helper")
    assert "function:pkg.tests.run" in got
    assert "function:pkg.main.main" in got                    # and on up to the entry point
    edges = {(r[0], r[1]) for r in st.q("SELECT src, kind FROM edges WHERE dst = 'function:pkg.util.helper'")}
    assert ("function:pkg.tests.run", "CALLS") in edges
    assert not json.loads(st.node("function:pkg.tests.run")["attrs"] or "{}").get("test")


DJANGO = {
    "manage.py": "import os\n",
    "shop/__init__.py": "",
    "shop/models.py": "def price(x):\n    return x * 2\n",
    "shop/views.py": "from shop.models import price\n\ndef show():\n    return price(1)\n",
    # Django's runner discovers it; nothing imports it
    "shop/tests.py": """
        from django.test import TestCase
        from shop.models import price

        class PriceTests(TestCase):
            def test_double(self):
                self.assertEqual(price(2), 4)
        """,
    # imported by application code, but it holds test cases: still test code
    "billing/__init__.py": "from billing import tests\n",
    "billing/tests.py": """
        import unittest

        def check():
            return 1

        class BillingTest(unittest.TestCase):
            def test_check(self):
                self.assertEqual(check(), 1)
        """,
}


def test_django_tests_module_stays_test_code(tmp_path):
    st = build(tmp_path, DJANGO, "site")
    got = callers(st, "shop.models.price")
    assert "function:shop.views.show" in got
    assert not any(c.startswith(("method:shop.tests", "class:shop.tests")) for c in got)
    ids = {r[0] for r in st.q("SELECT id FROM nodes WHERE kind = 'test'")}
    assert any("shop.tests.PriceTests.test_double" in i for i in ids)
    assert any("billing.tests.BillingTest.test_check" in i for i in ids)
    for nid in ("method:shop.tests.PriceTests.test_double", "function:billing.tests.check"):
        assert json.loads(st.node(nid)["attrs"] or "{}").get("test"), nid

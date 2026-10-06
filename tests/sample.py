"""Shared builders: index the bundled sample apps (examples/bookstore-api + examples/bookstore-web) once per session."""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "examples" / "bookstore-api"
WEB = ROOT / "examples" / "bookstore-web"
GATES = ROOT / "examples" / "bookstore.gates.json"
PLANS = ROOT / "examples" / "plans"
IMPL = ROOT / "tests" / "bookstore_impl"
EXTRACTOR_DEPS = ROOT / "cg_code_graph" / "plugins" / "ts" / "extractor" / "node_modules" / "typescript"
PHP_EXTRACTOR_DEPS = ROOT / "cg_code_graph" / "plugins" / "php" / "extractor" / "vendor" / "autoload.php"


# a test that indexes PHP code (the bundled Laravel sample) needs the PHP extractor's Composer dependencies
needs_php = pytest.mark.skipif(not PHP_EXTRACTOR_DEPS.exists(), reason="run `composer install` in cg_code_graph/plugins/php/extractor")


os.environ["CG_NO_CACHE"] = "1"  # always exercise the real extractors
_S: dict = {}


def build() -> dict:
    """Paths: api (gated), api_plain (no gates), web, combined (api+web), impl (api with tests/bookstore_impl applied)."""
    if _S:
        return _S
    from cg_code_graph.indexer import index_project
    from cg_code_graph.link import link
    d = Path(tempfile.mkdtemp(prefix="codegraph-test-"))
    index_project(API, d / "api.db", "bookstore-api", gates=str(GATES))
    index_project(API, d / "api_plain.db", "bookstore-api")
    impl = d / "impl_src"
    shutil.copytree(API, impl)
    shutil.copytree(IMPL, impl, dirs_exist_ok=True)
    index_project(impl, d / "impl.db", "bookstore-api")
    _S.update(dir=d, api=d / "api.db", api_plain=d / "api_plain.db", impl=d / "impl.db")
    if EXTRACTOR_DEPS.exists():
        index_project(WEB, d / "web.db", "bookstore-web")
        _S["link"] = link(str(d / "api.db"), str(d / "web.db"), str(d / "combined.db"),
                          backend_name="bookstore-api", frontend_name="bookstore-web")
        _S.update(web=d / "web.db", combined=d / "combined.db")
    return _S

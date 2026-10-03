"""CLI output (#75): stats / node text and --json, coverage summary and install hints, search paths, detected
languages, the confidence-filter message, routes on a SwiftUI app, TypeScript in a sub-directory, and `Class::method`
/ `Class.method` in every language."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from codegraph import coverage as C  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from sample import API, EXTRACTOR_DEPS, needs_php  # noqa: E402

XC = ROOT / "tests" / "platform_fixtures" / "xcode_app"
_S: dict = {}


def cg(*args):
    return subprocess.run([sys.executable, "-m", "codegraph.cli", *map(str, args)], cwd=ROOT, capture_output=True,
                          text=True, env={**__import__("os").environ, "CODEGRAPH_NO_CACHE": "1"})


def xc_db() -> Path:
    if "xc" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-cli-"))
        index_project(XC, d / "xc.db", "xcode-app")
        _S["xc"] = d / "xc.db"
    return _S["xc"]


def test_stats_json_is_one_document_and_text_has_none():
    j = json.loads(cg("stats", "--db", xc_db(), "--json").stdout)
    assert j["project"] == "xcode-app" and j["nodes"] == sum(j["nodes_by_kind"].values()) > 0
    assert j["edges_by_kind"]["CONTAINS"]["exact"] > 0 and "plugins" in j["stats"]
    txt = cg("stats", "--db", xc_db()).stdout
    assert "{" not in txt and "nodes by kind:" in txt and "edges by kind / confidence:" in txt
    assert txt.splitlines()[1].startswith("languages: swift |")


def test_node_json_has_the_edges_and_text_has_no_json():
    j = json.loads(cg("node", "ToolbarItems.close", "--db", xc_db(), "--json").stdout)
    assert [d["node"]["id"] for d in j] == ["method:ToolbarItems.close", "method:ToolbarItems.close@7"]   # per-#if variants
    assert isinstance(j[0]["node"]["attrs"], dict)
    assert {e["src"] for e in j[0]["in"]} >= {"class:ToolbarItems"} and all("dst" in e for e in j[0]["out"])
    txt = cg("node", "ToolbarItems::close", "--db", xc_db()).stdout       # either separator
    assert "{" not in txt and txt.startswith("method:ToolbarItems.close  (method, swift)")
    assert "  at: App/Toolbar.swift:5" in txt and "  platforms: macos  (#if targetEnvironment(macCatalyst))" in txt
    assert "  incoming (" in txt and "<- CONTAINS class:ToolbarItems" in txt


def test_detected_languages_come_from_the_plugins_that_ran():
    det = GraphStore(xc_db()).meta()["stats"]["detected"]["languages"]
    assert det == {"swift": ["source files (.swift 5)"]}             # no root Package.swift


def test_search_prints_root_relative_paths():
    out = cg("search", "close", "--db", xc_db()).stdout
    assert "App/Toolbar.swift:5" in out


def test_min_confidence_message_names_the_filter():
    out = cg("impact", "ToolbarItems.close", "--db", xc_db(), "--min-confidence", "resolved").stdout
    assert "no callers at --min-confidence resolved" in out and "(heuristic) are below the threshold" in out
    assert "docs/swift.md#exact-mode" in out and "no callers found" not in out


def test_routes_on_a_swiftui_app_points_to_its_screens():
    out = cg("routes", "--db", xc_db()).stdout
    assert "`routes` lists server-side HTTP routes" in out and "1 app screen (1 SwiftUI" in out
    assert "--kind page" in out


def test_coverage_text_is_a_short_summary():
    out = cg("coverage", "--db", xc_db()).stdout
    lines = out.splitlines()
    assert lines[0].startswith("coverage xcode-app: not fully covered: swift 5 heuristic") and len(lines) <= 8
    assert "{" not in out and "`cg coverage --details`" in lines[-1]
    full = cg("coverage", "--db", xc_db(), "--details").stdout
    assert "swift: 5 files (.swift 5) heuristic" in full and "fix: heuristic mode" in full


def test_install_hint_only_for_missing_modules(monkeypatch):
    assert "pip install" not in C.hint("swift") and "pip install" not in C.hint("kotlin")   # installed here
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n == "tree_sitter_swift" else real(n, *a))
    assert "the layer needs `pip install tree-sitter-swift`" in C.hint("swift")
    assert "tree-sitter-kotlin" not in C.hint("kotlin")


def test_tests_help_names_both_separators():
    out = cg("tests", "-h").stdout
    assert "Class.method or Class::method" in " ".join(out.split())


def test_python_class_method_with_double_colon(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'p'\n")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "svc.py").write_text("class Svc:\n    def run(self):\n        return 1\n\n\ndef main():\n"
                                             "    Svc().run()\n")
    index_project(tmp_path, tmp_path / "g.db", "py")
    a = cg("impact", "Svc.run", "--db", tmp_path / "g.db").stdout
    b = cg("impact", "Svc::run", "--db", tmp_path / "g.db").stdout
    assert "main" in a and a == b


@needs_php
def test_php_class_method_with_a_dot(tmp_path):
    index_project(API, tmp_path / "api.db", "bookstore-api")
    a = cg("impact", "StockService::reserve", "--db", tmp_path / "api.db").stdout
    b = cg("impact", "StockService.reserve", "--db", tmp_path / "api.db").stdout
    assert "targets: ['method:App\\\\Services\\\\StockService::reserve']" in a and a == b


@pytest.mark.skipif(not EXTRACTOR_DEPS.exists() or not shutil.which("node"), reason="TypeScript extractor not installed")
def test_typescript_in_a_subdirectory_of_a_swift_repo(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(XC, root)
    (root / "web" / "src").mkdir(parents=True)
    (root / "web" / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}, "include": ["src"]}\n')
    (root / "web" / "src" / "api.ts").write_text("export function listOrders() { return fetch('/v1/orders') }\n"
                                                  "export function page() { return listOrders() }\n")
    index_project(root, tmp_path / "g.db", "mixed")
    st = GraphStore(tmp_path / "g.db")
    assert st.q("SELECT count(*) c FROM nodes WHERE lang='ts' AND file='web/src/api.ts'")[0]["c"] >= 2
    langs = {e["language"]: e["status"] for e in st.meta()["stats"]["coverage"]["languages"]}
    assert langs["typescript"] == "exact" and langs["swift"] == "heuristic"
    # a PHP / Python backend root keeps its frontend separate (index it on its own, `cg link`)
    from codegraph.plugins.ts.plugin import sub_tsconfigs
    assert sub_tsconfigs(root) == ["web/tsconfig.json"]
    (root / "requirements.txt").write_text("")
    assert sub_tsconfigs(root) == []

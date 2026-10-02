"""Python source roots: detection without markers or flags (src/ layout from pyproject.toml, several package roots,
PEP 420 namespace packages, a lib/ launcher layout, nested projects), a configured root that replaces detection
(`.cg.yaml` and `cg index --python-root`), module names chosen by the project's imports, and the roots reported by
`cg coverage` / MCP `coverage`. Every fixture is written from scratch in a temp dir."""
import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import coverage as C  # noqa: E402
from codegraph.config import ConfigError, load as load_config  # noqa: E402
from codegraph.core.plugin import Project  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.plugins.python.plugin import PythonPlugin  # noqa: E402
from codegraph.plugins.python.roots import packaging_roots  # noqa: E402


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


def modules(st):
    return {r["name"]: r["file"] for r in st.q("SELECT name, file FROM nodes WHERE kind='module'")}


def edges(st, kind):
    return {(r["src"], r["dst"], r["confidence"]) for r in st.q("SELECT src, dst, confidence FROM edges WHERE kind=?", (kind,))}


def py_cov(st):
    return next(e for e in st.meta()["stats"]["coverage"]["languages"] if e["language"] == "python")


def roots_of(st):
    return {r["path"]: r for r in py_cov(st).get("source_roots") or []}


REPRO = {
    "lib/core/__init__.py": "",
    "lib/core/policy.py": "def is_allowed(x):\n    return x > 0\n",
    "lib/service.py": "from core.policy import is_allowed\n\n\ndef handle(x):\n    return is_allowed(x)\n",
}


def test_issue_repro_without_marker_or_flags(tmp_path):
    st = build(tmp_path, "proj", REPRO)
    assert modules(st) == {"core": "lib/core/__init__.py", "core.policy": "lib/core/policy.py", "service": "lib/service.py"}
    assert ("function:service.handle", "function:core.policy.is_allowed", "exact") in edges(st, "CALLS")
    assert ("module:service", "module:core.policy", "exact") in edges(st, "IMPORTS")
    py = py_cov(st)
    assert (py["files"], py["indexed"], py["files_complete"]) == (3, 3, True)
    r = roots_of(st)["lib/"]
    assert r["origin"] == "detected" and "parent of top-level package core" in r["why"] and r["modules"] == 3
    out = cg("coverage", "--db", st.path).stdout
    assert out.startswith("coverage proj: python 3 exact\n")
    assert "python source roots: lib/ (detected: lib/ directory; parent of top-level package core, 3 modules)" in out


def test_python_detected_anywhere_but_not_in_dependency_dirs(tmp_path):
    deep = write(tmp_path / "deep", {"tools/gen/run.py": "def go():\n    return 1\n", "README.md": "x\n"})
    deps = write(tmp_path / "deps", {"node_modules/pkg/build.py": "X = 1\n", "vendor/lib/x.py": "Y = 1\n",
                                     ".venv/lib/site.py": "Z = 1\n", "index.js": "module.exports = 1\n"})
    assert PythonPlugin().detect(Project(root=deep, name="deep"))
    assert not PythonPlugin().detect(Project(root=deps, name="deps"))


def test_src_layout_from_pyproject(tmp_path):
    st = build(tmp_path, "srcpkg", {
        "pyproject.toml": '[project]\nname = "acme"\n\n[tool.setuptools]\npackage-dir = {"" = "code"}\n',
        "code/acme/__init__.py": "from .store import save\n",
        "code/acme/store.py": "def save(x):\n    return x\n",
        "code/acme/api.py": "from acme.store import save\n\n\ndef create(x):\n    return save(x)\n",
        "tests/test_api.py": "from acme.api import create\n\n\ndef test_create():\n    assert create(1) == 1\n",
    })
    m = modules(st)
    assert m["acme.api"] == "code/acme/api.py" and m["acme.store"] == "code/acme/store.py" and m["acme"] == "code/acme/__init__.py"
    assert "code.acme.api" not in m
    assert ("function:acme.api.create", "function:acme.store.save", "exact") in edges(st, "CALLS")
    calls = {(s, d) for s, d, _ in edges(st, "CALLS")} | {(s, d) for s, d, _ in edges(st, "TEST_CALLS")}
    assert ("function:tests.test_api.test_create", "function:acme.api.create") in calls
    assert "pyproject.toml [tool.setuptools] package-dir" in roots_of(st)["code/"]["why"]


def test_two_package_roots_in_one_repo(tmp_path):
    st = build(tmp_path, "mono", {
        "services/billing/billing/__init__.py": "",
        "services/billing/billing/invoice.py": "from auth.tokens import verify\n\n\ndef charge(t):\n    return verify(t)\n",
        "services/auth/auth/__init__.py": "",
        "services/auth/auth/tokens.py": "def verify(t):\n    return bool(t)\n",
    })
    m = modules(st)
    assert m["billing.invoice"] == "services/billing/billing/invoice.py" and m["auth.tokens"] == "services/auth/auth/tokens.py"
    assert ("function:billing.invoice.charge", "function:auth.tokens.verify", "exact") in edges(st, "CALLS")
    r = roots_of(st)
    assert set(r) == {"services/auth/", "services/billing/"}
    assert r["services/auth/"]["why"] == "parent of top-level package auth"


def test_namespace_package(tmp_path):
    st = build(tmp_path, "ns", {
        "plugins/acme/core/base.py": "class Base:\n    def run(self):\n        return 1\n",
        "plugins/acme/extra/thing.py": "from acme.core.base import Base\n\n\nclass Thing(Base):\n    pass\n",
        "app/main.py": "import acme.extra.thing\n\n\ndef start():\n    return acme.extra.thing.Thing().run()\n",
    })
    m = modules(st)
    assert m["acme.core.base"] == "plugins/acme/core/base.py" and m["acme.extra.thing"] == "plugins/acme/extra/thing.py"
    assert ("class:acme.extra.thing.Thing", "class:acme.core.base.Base", "exact") in edges(st, "EXTENDS")
    assert ("module:app.main", "module:acme.extra.thing", "exact") in edges(st, "IMPORTS")
    assert any(s == "function:app.main.start" and d == "method:acme.core.base.Base.run" for s, d, _ in edges(st, "CALLS"))
    assert roots_of(st)["plugins/"]["why"].startswith("namespace package acme (imported as acme.")


def test_lib_launcher_layout(tmp_path):
    st = build(tmp_path, "launch", {
        "bin/run.py": """
            import os, sys
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
            from tool.cli import main


            def entry():
                return main()
        """,
        "lib/tool/__init__.py": "",
        "lib/tool/cli.py": "from helpers import fmt\n\n\ndef main():\n    return fmt('ok')\n",
        "lib/helpers.py": "def fmt(s):\n    return s.upper()\n",
    })
    m = modules(st)
    assert m["helpers"] == "lib/helpers.py" and m["tool.cli"] == "lib/tool/cli.py" and m["bin.run"] == "bin/run.py"
    c = edges(st, "CALLS")
    assert ("function:bin.run.entry", "function:tool.cli.main", "exact") in c
    assert ("function:tool.cli.main", "function:helpers.fmt", "exact") in c


LAYOUT = {
    "lib/tool/__init__.py": "",
    "lib/tool/cli.py": "from shared.text import fmt\n\n\ndef main():\n    return fmt('x')\n",
    "vendored/shared/__init__.py": "",
    "vendored/shared/text.py": "def fmt(s):\n    return s\n",
    "scripts/release.py": "def go():\n    return 1\n",
}


def test_configured_root_overrides_detection(tmp_path):
    st = build(tmp_path, "conf", {**LAYOUT, ".cg.yaml": "version: 1\npython:\n  source_roots: [lib, vendored/]\n"})
    m = modules(st)
    assert set(m) == {"tool", "tool.cli", "shared", "shared.text"}           # scripts/ is outside every configured root
    assert ("function:tool.cli.main", "function:shared.text.fmt", "exact") in edges(st, "CALLS")
    py = py_cov(st)
    assert py["roots_mode"] == "configured" and py["unmapped"] == 1 and py["paths"]["unmapped"] == ["scripts/release.py"]
    assert [(r["path"], r["origin"], r["why"]) for r in py["source_roots"]] == [
        ("lib/", "configured", "configured in .cg.yaml"), ("vendored/", "configured", "configured in .cg.yaml")]
    assert "outside the configured source roots (lib/, vendored/)" in py["hint"] and "python.source_roots" in py["hint"]
    out = cg("coverage", "--db", st.path).stdout
    assert "python source roots: lib/ (configured: configured in .cg.yaml, 2 modules); vendored/ (configured" in out
    assert "unmapped: scripts/release.py" in out
    # the same layout without the config file: detection finds both package roots and keeps scripts/ as scripts.release
    det = build(tmp_path, "det", LAYOUT)
    assert set(modules(det)) == {"tool", "tool.cli", "shared", "shared.text", "scripts.release"}
    assert py_cov(det)["roots_mode"] == "detected"


def test_python_root_flag_beats_config_file_and_is_kept_by_mcp_reindex(tmp_path):
    files = {**LAYOUT, ".cg.yaml": "python:\n  source_roots: [lib]\n"}
    st = build(tmp_path, "flag", files, python_roots=["lib", "vendored", "scripts"])
    assert set(modules(st)) == {"tool", "tool.cli", "shared", "shared.text", "release"}
    py = py_cov(st)
    assert py["roots_mode"] == "flag" and {r["origin"] for r in py["source_roots"]} == {"flag"}
    r = cg("index", tmp_path / "flag", "--db", tmp_path / "cli.db", "--python-root", "lib", "--python-root", "vendored")
    assert r.returncode == 0, r.stderr[-500:]
    assert set(modules(GraphStore(tmp_path / "cli.db"))) == {"tool", "tool.cli", "shared", "shared.text"}
    from codegraph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE.update(db=str(tmp_path / "cli.db"), root=None, gates=None)
        assert "indexed" in M.index()
        st2 = GraphStore(tmp_path / "cli.db")
        assert set(modules(st2)) == {"tool", "tool.cli", "shared", "shared.text"}
        assert py_cov(st2)["roots_mode"] == "flag"
    finally:
        M.STATE.clear(); M.STATE.update(old)


def test_config_file_errors_and_warnings(tmp_path):
    bad = write(tmp_path / "bad", {"a.py": "X = 1\n", ".cg.yaml": "python:\n  source_roots: [../outside]\n"})
    with pytest.raises(ConfigError, match=r"\.cg\.yaml: python\.source_roots\[0\]: '\.\./outside' must stay inside"):
        load_config(bad)
    r = cg("index", bad, "--db", tmp_path / "bad.db")
    assert r.returncode == 2 and "cg index: .cg.yaml: python.source_roots[0]" in r.stderr
    write(bad, {".cg.yaml": "python:\n  roots: [src]\n"})
    with pytest.raises(ConfigError, match="unknown key 'roots'"):
        load_config(bad)
    write(bad, {".cg.yaml": "version: 2\n"})
    with pytest.raises(ConfigError, match="version 2 is not supported"):
        load_config(bad)
    write(bad, {".cg.yaml": "python: [\n"})
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)
    # keys for later versions of the file are kept and reported; a missing configured root is a warning
    st = build(tmp_path, "warn", {"app/__init__.py": "", "app/x.py": "X = 1\n",
                                  ".cg.yml": "apps: [{name: api, root: .}]\npython:\n  source_roots: [., gone]\n"})
    assert st.meta()["stats"]["config"] == {"file": ".cg.yml", "python": {"source_roots": ["", "gone"]}, "ignored_keys": ["apps"]}
    py = py_cov(st)
    assert py["roots_warnings"] == ["configured source root gone/ does not exist"] and py["indexed"] == 2
    out = C.render({"": st.meta()["stats"]["coverage"]})
    assert "python warning: configured source root gone/ does not exist" in out
    nothing = build(tmp_path, "none", {"app/x.py": "X = 1\n", ".cg.yaml": "python:\n  source_roots: [missing]\n"})
    py = py_cov(nothing)
    assert py["unmapped"] == 1 and py["files_complete"] is False
    assert "no source root: none of the configured source roots exists" in py["roots_warnings"]
    assert "python 1 discovered, 0 indexed" in cg("coverage", "--db", nothing.path).stdout


def test_module_name_follows_the_projects_imports(tmp_path):
    # backend/ is a nested project (its own pyproject.toml), but the repo's code imports backend.app.*: the canonical
    # name follows the imports, and the other name still resolves as an alias
    st = build(tmp_path, "imp", {
        "backend/pyproject.toml": "[project]\nname = 'backend'\n",
        "backend/app/__init__.py": "",
        "backend/app/models.py": "def load():\n    return 1\n",
        "backend/app/views.py": "from app.models import load\n\n\ndef show():\n    return load()\n",
        "jobs/nightly.py": "from backend.app.models import load\nfrom backend.app import views\n\n\ndef run():\n    return load()\n",
        "jobs/weekly.py": "from backend.app.models import load\n\n\ndef run():\n    return load()\n",
    })
    m = modules(st)
    assert m["backend.app.models"] == "backend/app/models.py" and "app.models" not in m
    c = edges(st, "CALLS")
    assert ("function:backend.app.views.show", "function:backend.app.models.load", "exact") in c   # via the alias app.models
    assert ("function:jobs.nightly.run", "function:backend.app.models.load", "exact") in c
    amb = st.meta()["stats"]["plugins"]["python"]["roots_ambiguous"]
    assert amb["count"] == 3 and amb["samples"][0]["chosen"].startswith("backend.app")
    assert "importable from two roots, named after the project's imports" in cg("coverage", "--db", st.path).stdout


def test_same_module_name_in_two_nested_projects(tmp_path):
    st = build(tmp_path, "twins", {
        "tests/conftest.py": "def fixture():\n    return 1\n",
        "svc-a/pyproject.toml": "[project]\nname = 'a'\n",
        "svc-a/a_pkg/__init__.py": "def a():\n    return 1\n",
        "svc-a/tests/__init__.py": "",
        "svc-a/tests/conftest.py": "from a_pkg import a\n\n\ndef fixture():\n    return a()\n",
        "svc-b/pyproject.toml": "[project]\nname = 'b'\n",
        "svc-b/b_pkg/__init__.py": "def b():\n    return 2\n",
        "svc-b/tests/__init__.py": "",
        "svc-b/tests/helpers.py": "def mk():\n    return 2\n",
        "svc-b/tests/conftest.py": "from b_pkg import b\nfrom .helpers import mk\n\n\ndef fixture():\n    return b() + mk()\n",
    })
    m = modules(st)
    assert m["tests.conftest"] == "tests/conftest.py"            # no other importable name: keeps it
    assert m["svc-a.tests.conftest"] == "svc-a/tests/conftest.py" and m["svc-b.tests.helpers"] == "svc-b/tests/helpers.py"
    assert m["a_pkg"] == "svc-a/a_pkg/__init__.py" and m["b_pkg"] == "svc-b/b_pkg/__init__.py"
    assert py_cov(st)["indexed"] == 8
    calls = {(s, d) for s, d, _ in edges(st, "CALLS")} | {(s, d) for s, d, _ in edges(st, "TEST_CALLS")}
    assert ("function:svc-b.tests.conftest.fixture", "function:svc-b.tests.helpers.mk") in calls   # relative import
    assert ("function:svc-a.tests.conftest.fixture", "function:a_pkg.a") in calls
    col = st.meta()["stats"]["plugins"]["python"]["module_name_collisions"]
    assert col["count"] == 2 and col["path_named"] == 5


@pytest.mark.parametrize("files, expect", [
    ({"pyproject.toml": '[tool.setuptools.packages.find]\nwhere = ["src"]\n'},
     [("src", "", "pyproject.toml [tool.setuptools.packages.find] where")]),
    ({"pyproject.toml": '[tool.setuptools]\npackage-dir = {"core" = "lib/core", "extra" = "plug"}\n'},
     [("lib", "", "pyproject.toml [tool.setuptools] package-dir"), ("plug", "extra", "pyproject.toml [tool.setuptools] package-dir")]),
    ({"pyproject.toml": '[tool.hatch.build.targets.wheel]\npackages = ["src/foo"]\n'},
     [("src", "", "pyproject.toml [tool.hatch.build.targets.wheel] packages")]),
    ({"pyproject.toml": '[tool.hatch.build]\nsources = ["py"]\n'}, [("py", "", "pyproject.toml [tool.hatch.build] sources")]),
    ({"pyproject.toml": '[tool.poetry]\npackages = [{ include = "foo", from = "source" }]\n'},
     [("source", "", "pyproject.toml [tool.poetry] packages from")]),
    ({"pyproject.toml": '[tool.pdm.build]\npackage-dir = "src"\n'}, [("src", "", "pyproject.toml [tool.pdm.build] package-dir")]),
    ({"pyproject.toml": '[tool.maturin]\npython-source = "python"\n'}, [("python", "", "pyproject.toml [tool.maturin] python-source")]),
    ({"setup.cfg": "[options]\npackage_dir =\n    =src\n"}, [("src", "", "setup.cfg [options] package_dir")]),
    ({"setup.cfg": "[options.packages.find]\nwhere = lib\n"}, [("lib", "", "setup.cfg [options.packages.find] where")]),
    ({"setup.py": "from setuptools import setup, find_packages\nsetup(package_dir={'': 'src'}, packages=find_packages('src'))\n"},
     [("src", "", "setup.py package_dir"), ("src", "", "setup.py find_packages('src')")]),
    ({"setup.py": "import setuptools\nsetuptools.setup(package_dir=DIRS)\n"}, []),         # not a literal: no evidence
    ({"pyproject.toml": "[tool.setuptools\n"}, []),                                        # invalid TOML: ignored
])
def test_packaging_config_roots(tmp_path, files, expect):
    write(tmp_path, files)
    assert packaging_roots(tmp_path, "") == expect
    write(tmp_path / "nested", files)
    assert packaging_roots(tmp_path, "nested") == [(f"nested/{d}", p, f"nested/{w}") for d, p, w in expect]


def test_mcp_coverage_lists_roots(tmp_path):
    st = build(tmp_path, "mcp", {**REPRO, "my-tools/x.py": "X = 1\n"})
    from codegraph import mcp_server as M
    old = dict(M.STATE)
    try:
        M.STATE["db"] = st.path
        txt = asyncio.run(M.server.call_tool("coverage", {})).content[0].text
        assert "python source roots: lib/ (detected: lib/ directory; parent of top-level package core, 3 modules)" in txt
        assert "unmapped: my-tools/x.py" in txt
        js = json.loads(M.coverage(json_output=True))
        assert js["python_source_roots"]["roots_mode"] == "detected"
        assert js["python_source_roots"]["source_roots"][0]["path"] == "lib/"
    finally:
        M.STATE.clear(); M.STATE.update(old)


def test_plain_layout_keeps_short_coverage(tmp_path):
    st = build(tmp_path, "plain", {"requirements.txt": "", "app/__init__.py": "", "app/x.py": "X = 1\n"})
    assert [r["path"] for r in py_cov(st)["source_roots"]] == ["./"]
    out = C.render({"": st.meta()["stats"]["coverage"]})
    assert "source roots" not in out
    assert "python source roots: ./ (detected: indexed root; parent of top-level package app, 2 modules)" in \
        C.render({"": st.meta()["stats"]["coverage"]}, all_files=True)

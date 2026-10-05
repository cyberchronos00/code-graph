"""`.cg.yaml` extras: `include` directories and `skip_dirs.keep` reaching every walk (Python, the TS and Dart
extractors, which keep no skip list of their own), workspace `apps` (one `cg index` indexes each app into one
combined graph) and `cg config show` for the new keys. Fixtures are written from scratch in a temp dir."""
import json
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.config import ConfigError, parse, effective  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.plugins.dart.plugin import find_dart  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="TS extractor deps not installed")
needs_dart = pytest.mark.skipif(find_dart() is None, reason="Dart SDK not found (set $DART or put dart on PATH)")


def write(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    return root


def files_of(db: Path, lang: str | None = None) -> set:
    c = sqlite3.connect(db)
    q = "SELECT DISTINCT file FROM nodes WHERE file IS NOT NULL" + (" AND lang=?" if lang else "")
    return {r[0] for r in c.execute(q, (lang,) if lang else ())}


def test_no_skip_directory_names_outside_presets():
    """Directory skip lists live in codegraph/presets/*.yaml only (the walks and both extractors read them)."""
    ts = (ROOT / "codegraph/plugins/ts/extractor/extract.mjs").read_text()
    dart = (ROOT / "codegraph/plugins/dart/extractor/bin/extract.dart").read_text()
    assert "TEST_WALK_SKIP" not in ts and not re.search(r"name === 'node_modules'|includes\('/node_modules/'\)", ts)
    assert not re.search(r"'\.dart_tool'|'Pods'|'node_modules'", dart)
    py = [p for p in (ROOT / "codegraph").rglob("*.py") if "presets" not in p.parts]
    sets = [p for p in py if re.search(r"\{[^{}\n]*\"node_modules\"[^{}\n]*\}", p.read_text())]
    assert not sets, sets


PY_FILES = {
    "pyproject.toml": "[project]\nname = 'svc'\n",
    "svc/__init__.py": "",
    "svc/main.py": "from svc.gen.models import Order\n\ndef run():\n    return Order()\n",
    "build/svc_gen/models.py": "class Order:\n    pass\n",
    "build/other/junk.py": "def junk():\n    return 1\n",
    "static/tools.py": "def tool():\n    return 1\n",
    "env/settings.py": "def setting():\n    return 1\n",
}


def test_python_include_and_keep(tmp_path):
    base = write(tmp_path / "plain", PY_FILES)
    index_project(base, tmp_path / "plain.db", "plain")
    plain = files_of(tmp_path / "plain.db", "python")
    assert not {f for f in plain if f.startswith(("build/", "static/", "env/"))}
    cfg = write(tmp_path / "cfg", dict(PY_FILES, **{
        ".cg.yaml": "version: 1\ninclude: [build/svc_gen]\nskip_dirs:\n  keep: [static, env]\n"}))
    index_project(cfg, tmp_path / "cfg.db", "cfg")
    got = files_of(tmp_path / "cfg.db", "python")
    assert {"build/svc_gen/models.py", "static/tools.py", "env/settings.py"} <= got
    assert "build/other/junk.py" not in got          # build/ is walked only on the way to the include directory


TS_FILES = {
    "package.json": '{"name": "web"}',
    "tsconfig.json": '{"compilerOptions": {"target": "es2020", "module": "esnext", "strict": true}, "include": ["src", "generated"]}',
    "src/app.ts": "import { client } from '../generated/client'\nexport function load() { return client() }\n",
    "src/dist/keep.ts": "export function kept() { return 1 }\n",
    "src/.storybook/story.ts": "export function story() { return 1 }\n",
    "generated/client.ts": "export function client() { return fetch('/api/items') }\n",
    "tests/fixtures/data.test.ts": "export const x = 1\n",
    "tests/app.test.ts": "import { load } from '../src/app'\ntest('x', () => load())\n",
}


@needs_ts
def test_ts_extractor_include_keep_and_preset_skips(tmp_path):
    base = write(tmp_path / "plain", TS_FILES)
    index_project(base, tmp_path / "plain.db", "plain")
    plain = files_of(tmp_path / "plain.db")
    assert "src/app.ts" in plain and "tests/app.test.ts" in plain
    assert not {"src/dist/keep.ts", "src/.storybook/story.ts", "tests/fixtures/data.test.ts"} & plain
    cfg = write(tmp_path / "cfg", dict(TS_FILES, **{
        ".cg.yaml": "version: 1\ninclude: [generated]\nskip_dirs:\n  keep: [dist, .storybook, fixtures]\n"}))
    index_project(cfg, tmp_path / "cfg.db", "cfg")
    got = files_of(tmp_path / "cfg.db")
    assert {"src/app.ts", "src/dist/keep.ts", "src/.storybook/story.ts", "generated/client.ts",
            "tests/fixtures/data.test.ts"} <= got
    c = sqlite3.connect(tmp_path / "cfg.db")
    assert c.execute("SELECT count(*) FROM edges WHERE kind='CALLS' AND dst LIKE '%generated/client.ts%client'").fetchone()[0] == 1


@needs_ts
def test_ts_include_inside_a_skipped_directory(tmp_path):
    root = write(tmp_path / "nm", {
        "package.json": '{"name": "web"}',
        "tsconfig.json": '{"compilerOptions": {"target": "es2020", "module": "esnext"}, "include": ["src"]}',
        "src/app.ts": "export function a() { return 1 }\n",
        "src/node_modules/@acme/sdk/index.ts": "export function sdk() { return 1 }\n",
        "src/node_modules/other/index.ts": "export function other() { return 1 }\n",
        ".cg.yaml": "version: 1\ninclude: [src/node_modules/@acme/sdk]\n",
    })
    index_project(root, tmp_path / "nm.db", "nm")
    got = files_of(tmp_path / "nm.db")
    assert "src/node_modules/@acme/sdk/index.ts" in got and "src/node_modules/other/index.ts" not in got


@needs_dart
def test_dart_extractor_include_and_keep(tmp_path):
    files = {
        "pubspec.yaml": "name: app\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n",
        "lib/main.dart": "void main() { run(); }\nvoid run() {}\n",
        "build/gen/api.dart": "class Api {}\n",
        "build/other/junk.dart": "class Junk {}\n",
        ".tool/helper.dart": "class Helper {}\n",
    }
    base = write(tmp_path / "plain", files)
    index_project(base, tmp_path / "plain.db", "plain")
    assert files_of(tmp_path / "plain.db", "dart") == {"lib/main.dart"}
    cfg = write(tmp_path / "cfg", dict(files, **{".cg.yaml": "version: 1\ninclude: [build/gen]\nskip_dirs:\n  keep: [.tool]\n"}))
    index_project(cfg, tmp_path / "cfg.db", "cfg")
    assert files_of(tmp_path / "cfg.db", "dart") == {"lib/main.dart", "build/gen/api.dart", ".tool/helper.dart"}


MONO = {
    ".cg.yaml": """
        version: 1
        apps:
          - {name: api, root: apps/api, role: backend}
          - {name: web, root: apps/web, role: frontend, links: [api]}
          - {name: admin, root: apps/admin, role: frontend}
        """,
    "apps/api/requirements.txt": "fastapi\n",
    "apps/api/app/__init__.py": "",
    "apps/api/app/main.py": '''
        from fastapi import FastAPI
        app = FastAPI()

        @app.get("/api/items/{item_id}")
        def read_item(item_id: int):
            return {}

        @app.post("/api/items")
        def create_item():
            return {}
        ''',
    "apps/web/package.json": '{"name": "web"}',
    "apps/web/tsconfig.json": '{"compilerOptions": {"target": "es2020", "module": "esnext"}, "include": ["src"]}',
    "apps/web/src/api.ts": "export const getItem = (id: number) => fetch(`/api/items/${id}`)\n"
                           "export const createItem = () => fetch('/api/items', { method: 'POST' })\n",
    "apps/admin/package.json": '{"name": "admin"}',
    "apps/admin/tsconfig.json": '{"compilerOptions": {"target": "es2020", "module": "esnext"}, "include": ["src"]}',
    "apps/admin/src/admin.ts": "export const items = () => fetch('/api/items', { method: 'POST' })\n",
}


def graph(db: Path) -> tuple[set, set]:
    c = sqlite3.connect(db)
    return (set(c.execute("SELECT id, kind, file, line FROM nodes")),
            set(c.execute("SELECT src, dst, kind, file, line, confidence FROM edges")))


@needs_ts
def test_monorepo_apps_one_command_matches_one_by_one(tmp_path):
    root = write(tmp_path / "mono", MONO)
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-m", "codegraph.cli", "index", str(root), "--db", str(out / "mono.db")],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout)
    assert [a["name"] for a in summary["apps"]] == ["api", "web", "admin"]
    assert [(x["frontend"], x["backend"], x["endpoints_matched"]) for x in summary["links"]] == [("web", "api", 2), ("admin", "api", 1)]
    assert "web -> api: 2/2 endpoints" in r.stderr
    # the same graphs one by one
    sep = tmp_path / "sep"
    sep.mkdir()
    for a in ("api", "web", "admin"):
        index_project(root / "apps" / a, sep / f"{a}.db", a)
        assert graph(sep / f"{a}.db") == graph(out / f"mono.{a}.db")
    assert not (out / "mono.web+api.db").exists() and not (out / "mono.admin+api.db").exists()
    con = sqlite3.connect(out / "mono.db")
    assert json.loads(con.execute("SELECT value FROM meta WHERE key='repos'").fetchone()[0]) == ["api", "web", "admin"]
    assert con.execute("SELECT count(*) FROM edges WHERE kind='MATCHES_ROUTE'").fetchone()[0] >= 3
    # --no-apps: the root as one project
    r = subprocess.run([sys.executable, "-m", "codegraph.cli", "index", str(root), "--db", str(tmp_path / "one.db"), "--no-apps"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "apps" not in json.loads(r.stdout)


def test_apps_validation():
    ok = parse({"apps": {"api": {"root": "apps/api"}, "web": {"root": "apps/web", "role": "frontend"}}})
    assert ok["apps"] == [{"name": "api", "root": "apps/api", "role": "backend"},
                          {"name": "web", "root": "apps/web", "role": "frontend"}]
    outside = parse({"apps": [{"name": "api", "root": "../api"},
                              {"name": "web", "root": "/srv/web", "role": "frontend", "links": ["api"]}]})
    assert [a["root"] for a in outside["apps"]] == ["../api", "/srv/web"]
    for bad, msg in (
            ({"apps": [{"name": "a", "root": "x"}, {"name": "a", "root": "y"}]}, "used by two apps"),
            ({"apps": [{"name": "web", "root": "w", "role": "frontend", "links": ["api"]}]}, "is not a backend app"),
            ({"apps": [{"name": "api", "role": "server"}]}, "backend or frontend"),
            ({"apps": [{"name": "api", "links": ["x"]}]}, "only a frontend"),
            ({"apps": [{"name": "api", "path": "x"}]}, "unknown key 'path'"),
            ({"include": ["../x"]}, "inside the indexed root"),
            ({"include": ["."]}, "indexed root itself")):
        with pytest.raises(ConfigError, match=re.escape(msg)):
            parse(bad)


def test_config_show_lists_include_and_apps(tmp_path):
    root = write(tmp_path / "mono", dict(MONO, **{".cg.yaml": textwrap.dedent(MONO[".cg.yaml"]) + "include: [apps/api/build]\n"}))
    rows = {(r["key"], str(r["value"])): r["source"] for r in effective(root)["rows"]}
    assert rows[("include", "['apps/api/build']")] == ".cg.yaml include"
    assert rows[("apps", "web: apps/web (frontend)")] == ".cg.yaml apps"
    assert rows[("apps (link pairs)", "web -> api")] == ".cg.yaml apps[web].links"
    assert rows[("apps (link pairs)", "admin -> api")].startswith("built-in")

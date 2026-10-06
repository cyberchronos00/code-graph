"""A first-level src/app/lib/web tsconfig.json without package.json joins the program (#153)."""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.extractors import status as extractor_status  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.plugins.ts.plugin import sub_tsconfigs  # noqa: E402

TS_DEPS = ROOT / "cg_code_graph" / "plugins" / "ts" / "extractor" / "node_modules"
needs_ts = pytest.mark.skipif(
    shutil.which("node") is None or not (TS_DEPS.exists() or extractor_status("typescript")["installed"]),
    reason="node and the TypeScript extractor are required",
)


def _ts(d: Path, body: str = '{"compilerOptions": {"module": "commonjs"}}') -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "tsconfig.json").write_text(body)


def test_src_and_package_tsconfigs(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "ts153", "private": true}')
    _ts(tmp_path / "src")
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "package.json").write_text('{"name": "tasks", "private": true}')
    _ts(tmp_path / "tasks", '{"compilerOptions": {"module": "commonjs"}, "include": ["*.ts"]}')
    assert sorted(sub_tsconfigs(tmp_path)) == ["src/tsconfig.json", "tasks/tsconfig.json"]


def test_conventional_dirs_only(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "app", "private": true}')
    _ts(tmp_path / "app")
    _ts(tmp_path / "lib")
    _ts(tmp_path / "scripts")
    _ts(tmp_path / "src")
    _ts(tmp_path / "src" / "api")
    (tmp_path / "packages" / "a").mkdir(parents=True)
    (tmp_path / "packages" / "b").mkdir(parents=True)
    for name in ("a", "b"):
        d = tmp_path / "packages" / name
        (d / "package.json").write_text(f'{{"name": "{name}"}}')
        _ts(d)
    _ts(tmp_path / "tests" / "x")
    got = sorted(sub_tsconfigs(tmp_path))
    assert "app/tsconfig.json" in got
    assert "lib/tsconfig.json" in got
    assert "src/tsconfig.json" in got
    assert "scripts/tsconfig.json" not in got
    assert "src/api/tsconfig.json" not in got
    assert "packages/a/tsconfig.json" in got
    assert "packages/b/tsconfig.json" in got
    assert "tests/x/tsconfig.json" not in got


def test_root_tsconfig_still_empty(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "tsconfig.json").write_text("{}")
    _ts(tmp_path / "src")
    assert sub_tsconfigs(tmp_path) == []


@needs_ts
def test_index_src_tsconfig_calls(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"name": "ts153", "private": true, "devDependencies": {"typescript": "^5"}}')
    _ts(tmp_path / "src", '{"compilerOptions": {"target": "es2020", "module": "commonjs", "strict": true}}')
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "lib" / "util.ts").write_text(
        "export function helper(x: number): number {\n  return x + 1\n}\n")
    (tmp_path / "src" / "index.ts").write_text(
        'import { helper } from "./lib/util"\n\nexport function main(): number {\n  return helper(1)\n}\n')
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "package.json").write_text('{"name": "tasks", "private": true}')
    _ts(tmp_path / "tasks", '{"compilerOptions": {"module": "commonjs"}, "include": ["*.ts"]}')
    (tmp_path / "tasks" / "build.ts").write_text('export function build(): string {\n  return "ok"\n}\n')
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "ts153")
    st = GraphStore(str(db))
    ids = {r["id"] for r in st.q("SELECT id FROM nodes WHERE kind='function'")}
    assert "function:src/lib/util.ts#helper" in ids
    assert "function:src/index.ts#main" in ids
    assert "function:tasks/build.ts#build" in ids
    rows = st.q("SELECT src, dst FROM edges WHERE kind='CALLS'")
    assert any(r["src"].endswith("src/index.ts#main") and r["dst"].endswith("src/lib/util.ts#helper") for r in rows)

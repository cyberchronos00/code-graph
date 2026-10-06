"""`cg coverage` names the real reason the TypeScript plugin did not run on a tree with .ts files (no root tsconfig
and nothing else that makes it a TypeScript project), with the directories holding a tsconfig to index instead,
not an install hint."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import coverage as C  # noqa: E402


def _ts(root: Path, plugins: dict) -> dict:
    return next(e for e in C.compute(root, plugins)["languages"] if e["language"] == "typescript")


def test_ts_not_run_names_reason_and_dirs(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}')
    (tmp_path / "web" / "src").mkdir(parents=True)
    (tmp_path / "web" / "tsconfig.json").write_text("{}")
    (tmp_path / "web" / "src" / "a.ts").write_text("export const a = 1\n")
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "tsconfig.json").write_text("{}")
    e = _ts(tmp_path, {})
    assert e["status"] == "not_indexed"
    assert e["reason"].startswith("the TypeScript plugin did not run: no tsconfig.json / jsconfig.json at the indexed root")
    assert "Node" not in e["hint"] and "(web)" in e["hint"]


def test_ts_not_run_without_package_json(tmp_path):
    (tmp_path / "a.ts").write_text("export const a = 1\n")
    e = _ts(tmp_path, {})
    assert e["reason"].endswith("and no package.json") and "add a tsconfig.json" in e["hint"]
    # a PHP app with a plain frontend keeps its own hint
    assert "frontend directory" in _ts(tmp_path, {"php": {"nodes": 1}})["hint"]


def test_ts_install_hint_only_when_skipped(tmp_path):
    (tmp_path / "a.ts").write_text("export const a = 1\n")
    e = _ts(tmp_path, {"typescript": {"status": "skipped", "reason": "node not installed"}})
    assert e["status"] == "skipped" and "install Node.js" in e["hint"]
    e = _ts(tmp_path, {"typescript": {"program_files": 0, "nodes": 0}})
    assert e["status"] == "not_indexed" and "Node" not in e["hint"] and "include" in e["hint"]


def test_ts_files_outside_the_source_dirs_are_not_indexed(tmp_path):
    """Files the TypeScript program never read (outside the source dirs, bin / script files and test trees) are
    `unmapped`, so coverage is not reported as complete; tool configs and test-named files stay excluded (#106)."""
    import json
    from cg_code_graph.indexer import index_project
    root = tmp_path / "app"
    files = {
        "package.json": '{"name": "app", "version": "1.0.0", "devDependencies": {"jest": "29"}}',
        "tsconfig.json": '{"compilerOptions": {"allowJs": true}, "include": ["src", "test"]}',
        "src/a.ts": "export function a() { return 1 }\n",
        "test/a.test.ts": "import { a } from '../src/a'\ntest('a', () => { a() })\n",
        "tools/gen.ts": "export function gen() { return 2 }\n",
        "examples/demo.js": "console.log(1)\n",
        "vite.config.ts": "export default {}\n",
    }
    for rel, txt in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt)
    stats = index_project(root, tmp_path / "app.db", "app")
    e = next(x for x in stats["coverage"]["languages"] if x["language"] == "typescript")
    assert e["discovered"] == 5 and e["indexed"] == 2, json.dumps(e)
    assert sorted(e["paths"]["unmapped"]) == ["examples/demo.js", "tools/gen.ts"], e["paths"]
    assert e["paths"]["excluded"] == ["vite.config.ts"], e["paths"]
    assert e["files_complete"] is False and ".cg.yaml `include`" in e["hint"]

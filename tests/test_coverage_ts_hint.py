"""`cg coverage` names the real reason the TypeScript plugin did not run on a tree with .ts files (no root tsconfig
and nothing else that makes it a TypeScript project), with the directories holding a tsconfig to index instead,
not an install hint."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import coverage as C  # noqa: E402


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

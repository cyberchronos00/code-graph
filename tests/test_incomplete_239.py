"""`cg index` on a project whose main language could not be indexed (#239).

A missing PHP toolchain used to give exit 0 and a two-node graph. Now the stats carry `incomplete`, every skipped
language prints a WARNING, and a mostly-unindexed project exits 3 unless `--allow-incomplete` is passed.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.coverage import incomplete, incomplete_lines  # noqa: E402


def _cov(*langs):
    return {"languages": [dict(language=n, files=f, status=s, reason="php not installed", hint="run `cg setup php`")
                          for n, f, s in langs]}


def test_incomplete_none_when_everything_indexed():
    assert incomplete(_cov(("php", 10, "exact"), ("typescript", 4, "exact"))) is None
    assert incomplete(None) is None


def test_unsupported_files_do_not_count():
    inc = incomplete(_cov(("php", 3, "skipped"), ("typescript", 40, "exact"), ("sh", 500, "unsupported")))
    assert inc == {"fatal": False, "skipped_files": 3, "supported_files": 43,
                   "languages": [{"language": "php", "files": 3, "reason": "php not installed",
                                  "hint": "run `cg setup php`"}]}
    lines = incomplete_lines(inc)
    assert lines == ["cg index: WARNING: php 3 files not indexed: php not installed; fix: run `cg setup php`"]


def test_fatal_at_half_of_supported_files():
    inc = incomplete(_cov(("php", 20, "skipped"), ("typescript", 20, "exact")))
    assert inc["fatal"]
    assert incomplete_lines(inc, where="app api")[-1].startswith(
        "cg index: ERROR (app api): incomplete graph: 20 of 40 supported source files were not indexed; exit status 3")


def _write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _cg(*args, path):
    env = {"PATH": path, "HOME": str(Path(path).parent / "home"), "CG_CACHE": str(Path(path).parent / "cache")}
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", *map(str, args)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)


def test_cli_warns_but_exits_0_when_few_files_are_skipped(tmp_path):
    app = tmp_path / "kiosk"
    for i in range(6):
        _write(app, f"src/screen{i}.ts", f"export function screen{i}(): number {{ return {i} }}\n")
    _write(app, "tools/ping.php", "<?php\nfunction ping() { return 1; }\n")
    _write(app, "composer.json", '{"name": "kiosk/tools"}\n')
    _write(app, "package.json", '{"name": "kiosk", "private": true}\n')
    _write(app, "tsconfig.json", '{"include": ["src"]}\n')
    bindir = tmp_path / "bin"          # node only: TypeScript is indexed, PHP is not
    bindir.mkdir()
    node = shutil.which("node")
    if node:
        os.symlink(node, bindir / "node")
    r = _cg("index", app, "--db", tmp_path / "g.db", path=str(bindir))
    st = json.loads(r.stdout)
    if (st["plugins"].get("ts") or st["plugins"].get("typescript") or {}).get("status") == "skipped":
        return      # no JS runtime on this PATH: covered by the unit tests above
    assert r.returncode == 0, r.stderr[-600:]
    assert st["incomplete"]["fatal"] is False and st["incomplete"]["skipped_files"] == 1
    assert "cg index: WARNING: php 1 file not indexed" in r.stderr and "ERROR" not in r.stderr

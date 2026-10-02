"""Extractor / indexer caches must never serve facts for old source.

Each cache (TypeScript facts, Dart facts, the SCIP cache used by Rust and C/C++) is keyed by the content of the
source files, not only their size and mtime: an edit that keeps the byte length and restores the old mtime (an
editor or a checkout tool doing so, a file copied with `cp -p`, a coarse filesystem clock) still invalidates it."""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.core import fsutil  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402
from codegraph.plugins.native import runner  # noqa: E402
from native_util import env  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

RUN_FOO = "export function foo() { return 1; }\nexport function bar() { return 2; }\nexport function run() { return foo(); }\n"


def same_size_edit(path: Path, old: str, new: str) -> None:
    """Replace `old` by `new` (same byte length) and put the previous mtime back."""
    assert len(old.encode()) == len(new.encode())
    st = path.stat()
    path.write_text(path.read_text().replace(old, new, 1))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert path.stat().st_size == st.st_size and path.stat().st_mtime_ns == st.st_mtime_ns


def calls(db: Path) -> set[tuple[str, str, str]]:
    return {tuple(r) for r in sqlite3.connect(db).execute("SELECT src, dst, confidence FROM edges WHERE kind='CALLS'")}


# ---------------------------------------------------------------- shared key
def test_content_key_changes_on_same_size_same_mtime_edit(tmp_path):
    f = tmp_path / "a.ts"
    f.write_text("foo();\n")
    k1 = fsutil.content_key(f)
    same_size_edit(f, "foo", "bar")
    assert fsutil.content_key(f) != k1
    assert fsutil.content_key(tmp_path / "missing.ts") == "missing"


def test_content_key_dangling_symlink_hashes_its_target_name(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.symlink_to(tmp_path / "nowhere-1")
    b.symlink_to(tmp_path / "nowhere-2")
    assert fsutil.content_key(a).startswith("link|") and fsutil.content_key(a) != fsutil.content_key(b)


# ---------------------------------------------------------------- TypeScript facts cache
def _ts_project(d: Path) -> Path:
    (d / "src").mkdir(parents=True)
    (d / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"strict": True, "target": "es2020", "module": "commonjs"},
                                                 "include": ["src"]}))
    (d / "package.json").write_text(json.dumps({"name": "cache-probe", "devDependencies": {"typescript": "^5"}}))
    (d / "src" / "app.ts").write_text(RUN_FOO)
    return d


@pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")
def test_ts_facts_cache_same_size_same_mtime_edit_is_a_miss(tmp_path):
    proj = _ts_project(tmp_path / "proj")
    with env(CODEGRAPH_NO_CACHE=None, CODEGRAPH_CACHE=tmp_path / "cache"):
        st1 = index_project(proj, tmp_path / "1.db", "probe")
        st2 = index_project(proj, tmp_path / "2.db", "probe")
        same_size_edit(proj / "src" / "app.ts", "return foo()", "return bar()")
        st3 = index_project(proj, tmp_path / "3.db", "probe")
    assert st1["plugins"]["typescript"]["facts_cache"] == "miss"
    assert st2["plugins"]["typescript"]["facts_cache"] == "hit"          # unchanged project: reused
    assert ("function:src/app.ts#run", "function:src/app.ts#foo", "exact") in calls(tmp_path / "2.db")
    assert st3["plugins"]["typescript"]["facts_cache"] == "miss"         # content changed: re-extracted
    c3 = calls(tmp_path / "3.db")
    assert ("function:src/app.ts#run", "function:src/app.ts#bar", "exact") in c3
    assert not any(dst == "function:src/app.ts#foo" for _, dst, _ in c3)


@pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")
def test_ts_facts_cache_from_an_older_cache_version_is_discarded(tmp_path):
    from codegraph.plugins.ts.plugin import facts_fingerprint
    import hashlib
    proj = _ts_project(tmp_path / "proj")
    cdir = tmp_path / "cache" / "ts"
    cdir.mkdir(parents=True)
    rkey = hashlib.sha256(str(proj.resolve()).encode()).hexdigest()[:12]
    stale = cdir / f"{rkey}-0123456789abcdef0123.json"     # an entry written by a previous cache version
    stale.write_text(json.dumps({"nodes": [], "edges": []}))
    with env(CODEGRAPH_NO_CACHE=None, CODEGRAPH_CACHE=tmp_path / "cache"):
        st = index_project(proj, tmp_path / "1.db", "probe")
        fp = facts_fingerprint(proj, {})
    assert st["plugins"]["typescript"]["facts_cache"] == "miss"
    assert not stale.exists()
    assert ("function:src/app.ts#run", "function:src/app.ts#foo", "exact") in calls(tmp_path / "1.db")
    assert fp != facts_fingerprint(proj, {"other": 1})


def test_ts_fingerprint_includes_the_cache_version(tmp_path, monkeypatch):
    from codegraph.plugins.ts import plugin as ts
    proj = _ts_project(tmp_path / "proj")
    a = ts.facts_fingerprint(proj, {})
    monkeypatch.setattr(fsutil, "CACHE_VERSION", fsutil.CACHE_VERSION + 1)
    assert ts.facts_fingerprint(proj, {}) != a


# ---------------------------------------------------------------- Dart facts cache
def _dart_project(d: Path) -> Path:
    (d / "lib").mkdir(parents=True)
    (d / "pubspec.yaml").write_text("name: cache_probe\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n")
    (d / "lib" / "app.dart").write_text("int foo() => 1;\nint bar() => 2;\nint run() { return foo(); }\n")
    return d


def test_dart_fingerprint_same_size_same_mtime_edit(tmp_path, monkeypatch):
    from codegraph.plugins.dart import plugin as dart
    proj = _dart_project(tmp_path / "proj")
    a = dart.facts_fingerprint(proj, {})
    same_size_edit(proj / "lib" / "app.dart", "return foo()", "return bar()")
    b = dart.facts_fingerprint(proj, {})
    assert a != b
    monkeypatch.setattr(fsutil, "CACHE_VERSION", fsutil.CACHE_VERSION + 1)
    assert dart.facts_fingerprint(proj, {}) != b


def test_dart_facts_cache_same_size_same_mtime_edit_is_a_miss(tmp_path):
    from codegraph.plugins.dart.plugin import find_dart
    if not find_dart():
        pytest.skip("Dart SDK not installed")
    proj = _dart_project(tmp_path / "proj")
    with env(CODEGRAPH_NO_CACHE=None, CODEGRAPH_CACHE=tmp_path / "cache"):
        st1 = index_project(proj, tmp_path / "1.db", "probe")
        st2 = index_project(proj, tmp_path / "2.db", "probe")
        same_size_edit(proj / "lib" / "app.dart", "return foo()", "return bar()")
        st3 = index_project(proj, tmp_path / "3.db", "probe")
    assert (st1["plugins"]["dart"]["facts_cache"], st2["plugins"]["dart"]["facts_cache"]) == ("miss", "hit")
    assert st3["plugins"]["dart"]["facts_cache"] == "miss"
    dsts = {dst for src, dst, _ in calls(tmp_path / "3.db") if src.endswith("#run")}
    assert any(d.endswith("#bar") for d in dsts) and not any(d.endswith("#foo") for d in dsts)


def test_dart_extractor_binary_is_rebuilt_when_its_source_changes(tmp_path, monkeypatch):
    from codegraph.plugins.dart import plugin as dart
    src, lock, bin_ = tmp_path / "extract.dart", tmp_path / "pubspec.lock", tmp_path / ".bin" / "extract"
    src.write_text("void main() { print(1); }\n")
    lock.write_text("packages: {}\n")
    bin_.parent.mkdir()
    bin_.write_text("binary")
    monkeypatch.setattr(dart, "EXTRACTOR", src)
    monkeypatch.setattr(dart, "EXTRACTOR_DIR", tmp_path)
    monkeypatch.setattr(dart, "BIN", bin_)
    assert not dart.extractor_binary_current()          # no stamp yet (built by an older cg): rebuild
    dart.write_extractor_stamp()
    assert dart.extractor_binary_current()
    same_size_edit(src, "print(1)", "print(2)")         # newer source with an older mtime than the binary
    os.utime(bin_, ns=(src.stat().st_mtime_ns + 10**9,) * 2)
    assert not dart.extractor_binary_current()


# ---------------------------------------------------------------- SCIP cache (Rust, C / C++)
def test_scip_fingerprint_same_size_same_mtime_edit(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "lib.rs"
    f.write_text("pub fn run() { foo() }\n")
    files = ["src/lib.rs", "Cargo.toml"]          # a listed file that does not exist hashes as missing
    a = runner.fingerprint(tmp_path, files, "rust-analyzer 1.0")
    assert a == runner.fingerprint(tmp_path, files, "rust-analyzer 1.0")
    same_size_edit(f, "foo()", "bar()")
    b = runner.fingerprint(tmp_path, files, "rust-analyzer 1.0")
    assert a != b
    assert b != runner.fingerprint(tmp_path, files, "rust-analyzer 1.1")
    monkeypatch.setattr(fsutil, "CACHE_VERSION", fsutil.CACHE_VERSION + 1)
    assert runner.fingerprint(tmp_path, files, "rust-analyzer 1.0") != b


FAKE_INDEXER = """import sys, pathlib
out = sys.argv[sys.argv.index('--out') + 1]
pathlib.Path(out).write_bytes(pathlib.Path(sys.argv[1]).read_bytes())
"""


def test_scip_cache_rerun_after_same_size_same_mtime_edit_and_legacy_entries_pruned(tmp_path):
    """run_cached with a stand-in indexer that copies the source into its 'SCIP' output."""
    tool = tmp_path / "fake_indexer.py"
    tool.write_text(FAKE_INDEXER)
    proj = tmp_path / "proj"
    proj.mkdir()
    src = proj / "main.c"
    src.write_text("int run(void) { return foo(); }\n")
    cache = tmp_path / "cache"
    (cache / "scip").mkdir(parents=True)
    legacy = cache / "scip" / "cfamily-0123456789abcdef0123.scip"      # key format of an older cache version
    legacy.write_bytes(b"old")

    def index():
        key = runner.fingerprint(proj, ["main.c"], "fake 1")
        return runner.run_cached("cfamily", key, [sys.executable, str(tool), str(src)], proj, "--out", 60)

    with env(CODEGRAPH_NO_CACHE=None, CODEGRAPH_CACHE=cache):
        p1, i1 = index()
        p2, i2 = index()
        same_size_edit(src, "foo()", "bar()")
        p3, i3 = index()
    assert (i1["cache"], i2["cache"], i3["cache"]) == ("miss", "hit", "miss")
    assert b"bar()" in p3.read_bytes() and p3 != p1
    assert not legacy.exists()


def test_cfamily_scip_key_follows_compile_commands_content(tmp_path):
    from codegraph.plugins.cfamily.plugin import scip_cache_key
    (tmp_path / "a.c").write_text("int a;\n")
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(json.dumps([{"directory": ".", "file": "a.c", "command": "cc -DX=1 -c a.c"}]))
    k1 = scip_cache_key(tmp_path, ["a.c"], "scip-clang 0.3", cdb)
    same_size_edit(cdb, "-DX=1", "-DX=2")
    assert scip_cache_key(tmp_path, ["a.c"], "scip-clang 0.3", cdb) != k1

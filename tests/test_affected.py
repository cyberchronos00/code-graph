"""`cg affected` (#121 part 1) and file / module specs in `cg tests`."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cg_code_graph import query as Q
from cg_code_graph.affected import affected, file_changes, git_changes, render_affected, render_quiet
from cg_code_graph.core.store import GraphStore
from cg_code_graph.indexer import index_project

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git not installed (the fixture repo is a git repository)")

FILES = {
    "pyproject.toml": '[project]\nname = "affx"\nversion = "0"\n\n[project.scripts]\naffx = "app.cli:main"\n',
    "app/__init__.py": "",
    "app/util.py": "def a():\n    return 1\n\n\ndef b():\n    return 2\n",
    "app/svc.py": "from app.util import a, b\n\ndef use_a():\n    return a()\n\ndef use_b():\n    return b()\n",
    "app/cli.py": "from app.svc import use_a\n\ndef main():\n    print(use_a())\n",
    "tests/test_svc.py": "from app.svc import use_a, use_b\n\ndef test_use_a():\n    assert use_a() == 1\n\ndef test_use_b():\n    assert use_b() == 2\n",
    "tests/test_util.py": "from app.util import b\n\ndef test_b_direct():\n    assert b() == 2\n",
}


def _git(repo, *args):
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def _names(res):
    return {t["name"] for key in ("direct", "transitive", "ui") for t in res[key]}


@pytest.fixture(scope="module")
def affx(tmp_path_factory):
    repo = tmp_path_factory.mktemp("affx")
    db = tmp_path_factory.mktemp("affxdb") / "g.db"
    for rel, body in FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True, text=True)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    index_project(repo, db, "affx")
    return repo, db


def _reset(repo):
    _git(repo, "reset", "--hard", "HEAD")
    _git(repo, "clean", "-fd", "-q")


def _cli(repo, db, *args):
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", *args, "--db", str(db)],
                          cwd=repo, capture_output=True, text=True)


def test_file_and_module_specs(affx):
    repo, db = affx
    st = GraphStore(str(db))
    for spec in ("app/util.py", "app.util"):
        res = Q.tests_covering(st, spec)
        assert _names(res) == {"test_use_a", "test_use_b", "test_b_direct"}
        assert res["expanded"]["symbols"] >= 2
        assert "app/util.py" in res["expanded"]["files"]
    one = Q.tests_covering(st, "app.util.b")
    assert "expanded" not in one
    assert [t["name"] for t in one["direct"]] == ["test_b_direct"]
    assert [t["name"] for t in one["transitive"]] == ["test_use_b"]
    out = _cli(repo, db, "tests", "app/util.py", "--no-paths").stdout
    assert "file spec:" in out


def test_whole_file_and_quiet(affx):
    repo, db = affx
    st = GraphStore(str(db))
    res = affected(st, file_changes(["app/util.py"], repo))
    assert _names({"direct": res["tests"]["direct"], "transitive": res["tests"]["transitive"], "ui": res["tests"]["ui"]}) == {
        "test_use_a", "test_use_b", "test_b_direct"}
    assert "script:console_scripts:affx" in [e["id"] for e in res["entry_points"]]
    proc = _cli(repo, db, "affected", "app/util.py", "--quiet")
    assert proc.returncode == 0
    assert proc.stdout == "tests/test_svc.py\ntests/test_util.py\n"


def test_hunk_only_b(affx):
    repo, db = affx
    path = repo / "app" / "util.py"
    original = path.read_text()
    path.write_text(original.replace("return 2", "return 3"))
    try:
        st = GraphStore(str(db))
        changes = git_changes(repo, "HEAD")
        hit = next(c for c in changes if c["path"] == "app/util.py")
        assert hit["ranges"] == [(6, 6)]
        res = affected(st, changes, base="HEAD")
        got = _names({"direct": res["tests"]["direct"], "transitive": res["tests"]["transitive"], "ui": res["tests"]["ui"]})
        assert got == {"test_b_direct", "test_use_b"}
        assert res["entry_points"] == []
        text = render_affected(res)
        assert "(lines 6-6)" in text
        assert "test_use_a" not in text
    finally:
        path.write_text(original)


def test_changed_test_file(affx):
    repo, db = affx
    path = repo / "tests" / "test_util.py"
    original = path.read_text()
    path.write_text(original.replace("== 2", "== 3"))
    try:
        res = affected(GraphStore(str(db)), git_changes(repo, "HEAD"), base="HEAD")
        assert any(t["name"] == "test_b_direct" for t in res["changed_tests"])
        assert "tests/test_util.py" in res["test_files"]
    finally:
        path.write_text(original)


def test_deleted_and_renamed(affx):
    repo, db = affx
    st = GraphStore(str(db))
    _git(repo, "rm", "-q", "app/util.py")
    try:
        res = affected(st, git_changes(repo, "HEAD"), base="HEAD")
        deleted = next(f for f in res["files"] if f["path"] == "app/util.py")
        assert deleted["status"] == "D"
        assert _names({"direct": res["tests"]["direct"], "transitive": res["tests"]["transitive"], "ui": res["tests"]["ui"]}) == {
            "test_use_a", "test_use_b", "test_b_direct"}
        assert " (from the index)" in render_affected(res)
    finally:
        _reset(repo)
    _git(repo, "mv", "app/util.py", "app/helpers.py")
    try:
        changes = git_changes(repo, "HEAD")
        renamed = next(c for c in changes if c["status"] == "R")
        assert renamed["path"] == "app/helpers.py" and renamed["old_path"] == "app/util.py"
        res = affected(st, changes, base="HEAD")
        assert "app/helpers.py" not in res["not_indexed"]
        assert _names({"direct": res["tests"]["direct"], "transitive": res["tests"]["transitive"], "ui": res["tests"]["ui"]}) == {
            "test_use_a", "test_use_b", "test_b_direct"}
    finally:
        _reset(repo)


def test_not_indexed_and_bad_base(affx):
    repo, db = affx
    (repo / "README.md").write_text("notes\n")
    try:
        proc = _cli(repo, db, "affected", "README.md")
        assert proc.returncode == 0
        assert "not in the index" in proc.stdout
        res = affected(GraphStore(str(db)), file_changes(["README.md"], repo))
        assert res["not_indexed"] == ["README.md"]
    finally:
        (repo / "README.md").unlink()
    proc = _cli(repo, db, "affected", "--base", "nope")
    assert proc.returncode == 2
    assert proc.stderr


def test_json_and_cap(affx):
    repo, db = affx
    proc = _cli(repo, db, "affected", "app/util.py", "--json", "--max-targets", "1")
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    for key in ("base", "files", "not_indexed", "targets", "changed_tests", "tests", "test_files",
                "entry_points", "truncated", "stats", "completeness"):
        assert key in res
    assert res["truncated"]["walked"] == 1
    assert res["truncated"]["symbols"] > 1
    assert set(res["tests"]) == {"direct", "transitive", "ui"}
    quiet = render_quiet(affected(GraphStore(str(db)), file_changes(["app/util.py"], repo)))
    assert quiet == "tests/test_svc.py\ntests/test_util.py"


def test_mcp(affx, monkeypatch):
    repo, db = affx
    import cg_code_graph.mcp_server as M
    monkeypatch.setitem(M.STATE, "db", str(db))
    monkeypatch.setitem(M.STATE, "root", str(repo))
    out = M.affected(files=["app/util.py"])
    assert "test_use_a" in out


def test_hunk_header_paths_and_deletions():
    from cg_code_graph.affected import _hunks
    diff = "\n".join([
        "diff --git a/a b.py b/a b.py",
        "--- a/a b.py\t",
        "+++ b/a b.py\t",
        "@@ -3,2 +3,0 @@ def f():",
        "diff --git \"a/q\\\"x.py\" \"b/q\\\"x.py\"",
        "--- \"a/q\\\"x.py\"",
        "+++ \"b/q\\\"x.py\"",
        "@@ -1 +1 @@",
        "@@ -9,0 +10,4 @@",
        "diff --git a/top.py b/top.py",
        "--- a/top.py",
        "+++ b/top.py",
        "@@ -1,2 +0,0 @@",
        "diff --git a/gone.py b/gone.py",
        "--- a/gone.py",
        "+++ /dev/null",
        "@@ -1,3 +0,0 @@",
    ])
    got = _hunks(diff)
    assert got["a b.py"] == [(3, 4)]
    assert got['q"x.py'] == [(1, 1), (10, 13)]
    assert got["top.py"] == [(1, 1)]
    assert "gone.py" not in got


def test_diff_config_does_not_change_paths(affx, monkeypatch):
    repo, db = affx
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")      # `git diff HEAD` would print c/ and w/ prefixes
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "diff.mnemonicPrefix")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    path = repo / "app" / "util.py"
    original = path.read_text()
    path.write_text(original.replace("return 2", "return 3"))
    try:
        hit = next(c for c in git_changes(repo, "HEAD") if c["path"] == "app/util.py")
        assert hit["ranges"] == [(6, 6)]
    finally:
        path.write_text(original)


def test_innermost_keeps_a_class_level_line(tmp_path):
    proj = tmp_path / "k"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "__init__.py").write_text("")
    (proj / "pkg" / "k.py").write_text("class K:\n    x = 1\n\n    def m(self):\n        return 2\n")
    db = tmp_path / "k.db"
    index_project(proj, db, "k")
    st = GraphStore(str(db))
    rows = {r["id"]: (r["kind"], r["line"], r["end_line"]) for r in st.q("SELECT id, kind, line, end_line FROM nodes WHERE file='pkg/k.py'")}
    cls = next(i for i, v in rows.items() if v[0] == "class")
    meth = next(i for i, v in rows.items() if v[0] in ("method", "function") and v[1] == 4)
    assert Q.file_symbols(st, "pkg/k.py", [(5, 5)]) == [meth]
    both = Q.file_symbols(st, "pkg/k.py", [(5, 5), (2, 2)])
    assert cls in both and meth in both


def test_cap_zero_and_quiet_with_no_tests(affx):
    repo, db = affx
    res = affected(GraphStore(str(db)), file_changes(["app/util.py"], repo), max_targets=0)
    assert res["truncated"] is None and len(res["targets"]) > 1
    cut = affected(GraphStore(str(db)), file_changes(["app/util.py"], repo), max_targets=1)
    assert "walked the first 1 of" in render_affected(cut)
    (repo / "README.md").write_text("notes\n")
    try:
        proc = _cli(repo, db, "affected", "README.md", "--quiet")
        assert proc.returncode == 0 and proc.stdout == ""
    finally:
        (repo / "README.md").unlink()

"""Swift sources the tree-sitter grammar did not parse (#73): `#_sourceLocation` defaults, `#sourceLocation(...)`
directives, `#if os(...)` around a lone attribute, and other valid forms (empty tuples, a cast before `??`,
`try? await` in a condition, continuation-line operators, `@convention(c)`) are rewritten in place before parsing;
members after a macro the grammar does not know are recovered into their type; `cg coverage` lists every file that
still has syntax errors with its line spans and the declarations lost, in every language, and an answer touching such
a file is marked partial."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from cg_code_graph import coverage as C  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.core.syntax_errors import merge, span_text  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.plugins.swift.plugin import _preprocess  # noqa: E402

FIX = ROOT / "tests" / "swift_parse_fixture"
_S: dict = {}


def fix_db() -> Path:
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-swift73-"))
        _S["stats"] = index_project(FIX, d / "g.db", "swift-parse-fixture")
        _S["db"] = d / "g.db"
    return _S["db"]


def rows(sql, *a):
    return sqlite3.connect(fix_db()).execute(sql, a).fetchall()


def node(nid):
    r = rows("SELECT entry_kind, attrs FROM nodes WHERE id=?", nid)
    return (r[0][0], json.loads(r[0][1] or "{}")) if r else None


def cov_of(db) -> dict:
    return next(iter(C.for_graph(GraphStore(db)).values()))


def cg(*args):
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", *map(str, args)], cwd=ROOT, capture_output=True,
                          text=True, env=dict(os.environ, CG_NO_CACHE="1"))


def test_source_location_default_and_directive_tests_are_indexed():
    for t in ("ExpectTests.loads", "ExpectTests.loadsAgain", "SourceLocationTests.testGenerated",
              "SourceLocationTests.testAfter"):
        assert node(f"method:{t}")[0] == "test", t
    assert not rows("SELECT id FROM nodes WHERE id IN ('function:testGenerated', 'function:testAfter')")
    calls = {r[0] for r in rows("SELECT dst FROM edges WHERE src='method:ExpectTests.loads'")}
    assert "method:ExpectTests.expectLoaded" in calls


def test_attribute_inside_if_os_block_keeps_the_test_and_its_platform():
    ek, a = node("method:PlatformTests.loadsOnMac")
    assert ek == "test" and a["platforms"] == ["macos"] and a["framework"] == "swift-testing"
    assert node("method:PlatformTests.loads")[1].get("platforms") is None


def test_members_after_an_unknown_macro_are_recovered_into_the_type():
    for t in ("testBeforeMacro", "testAfterMacro"):
        assert node(f"method:OrphanTests.{t}")[0] == "test", t
    assert not rows("SELECT id FROM nodes WHERE id LIKE 'function:test%'")
    assert fix_db() and _S["stats"]["plugins"]["swift"]["declarations_recovered_into_type"] >= 1


def test_valid_forms_the_grammar_rejects_are_rewritten_and_fully_indexed():
    have = {r[0] for r in rows("SELECT id FROM nodes WHERE file='Sources/App/Rewrites.swift'")}
    assert {"method:Runner." + m for m in ("first", "second", "third", "fetch", "total", "callback", "check")} <= have
    assert {"class:Outcome", "class:Loader", "class:Saver", "class:Runner"} <= have
    assert ("method:Runner.fetch",) in rows("SELECT dst FROM edges WHERE src='method:Runner.third' AND kind='CALLS'")
    errs = {e["file"] for e in next(e for e in cov_of(fix_db())["languages"] if e["language"] == "swift").get("syntax_errors", [])
            if e["file"].endswith("Rewrites.swift")}
    assert not errs
    sw = _S["stats"]["plugins"]["swift"]
    for k in ("source_location_directives", "underscore_macros", "empty_tuples", "cast_coalesce", "try_await",
              "continuation_operators", "convention_attributes"):
        assert sw.get(f"files_with_{k}", 0) >= 1, k


@pytest.mark.parametrize("src", [
    b'let a = x as? String ?? ""\nlet b = (y as? Int) ?? 0\n',
    b"if let n = try? await f() {\n}\nlet t = base\n    * word.count\n",
    b"let v = g(())\nswitch o { case .done(): break }\ntypealias T = Tagged<((), ()), String>\n",
    b"#sourceLocation(file: \"G.swift\", line: 1)\nfunc f(l: SourceLocation = #_sourceLocation) {}\n#sourceLocation()\n",
    b"func f() {\n  let t = a // note\n    + b\n}\n",
])
def test_rewrites_keep_byte_offsets_and_lines(src):
    out, _ = _preprocess(src)
    assert len(out) == len(src) and out.count(b"\n") == src.count(b"\n")


def test_rewrites_leave_types_and_comments_alone():
    src = b"typealias T = Tagged<((), email: ()), String>\nlet b = (y as? Int) ?? 0\nlet t = a // x\n    + b\n"
    out, what = _preprocess(src)
    assert out == src and what == []


def test_coverage_lists_files_with_syntax_errors_spans_and_lost_declarations():
    cov = cov_of(fix_db())
    sw = next(e for e in cov["languages"] if e["language"] == "swift")
    by = {e["file"]: e for e in sw["syntax_errors"]}
    assert set(by) == {"Sources/App/Invalid.swift", "Tests/AppTests/OrphanTests.swift"}
    inv = by["Sources/App/Invalid.swift"]
    assert inv["spans"] == [[6, 6]] and inv["decls_lost"] == 1 and inv["lost"] == ["broken:6"]
    assert by["Tests/AppTests/OrphanTests.swift"]["decls_lost"] == 0      # recovered: errors, nothing lost
    assert sw["syntax_error_files"] == 2 and sw["parsed_with_errors"] == 2 and sw["decls_lost"] == 1
    r = cg("coverage", "--db", fix_db())
    assert r.returncode == 0, r.stderr
    assert "syntax errors: swift 2 files, 1 declaration lost" in r.stdout        # the summary (#75)
    r = cg("coverage", "--db", fix_db(), "--details")
    assert "swift 8 heuristic, 2 parsed with syntax errors" in r.stdout
    assert "swift: syntax errors in 2 files, 1 declaration lost" in r.stdout
    assert "Sources/App/Invalid.swift:6 (1 declaration lost: broken:6)" in r.stdout
    j = json.loads(cg("coverage", "--json", "--db", fix_db()).stdout)
    assert {e["file"] for e in next(x for x in next(iter(j.values()))["languages"] if x["language"] == "swift")["syntax_errors"]} == set(by)
    assert "2 files with syntax errors" in C.note({"": cov})


def test_answer_touching_a_file_with_errors_is_partial():
    st = GraphStore(fix_db())
    comp = C.completeness_for(st, ["function:swallowed"])
    assert comp["complete"] is False
    assert comp["syntax_errors"] == [{"language": "swift", "file": "Sources/App/Invalid.swift", "spans": [[6, 6]],
                                      "decls_lost": 1}]
    assert "Sources/App/Invalid.swift:6" in C.possibly_more(comp)
    assert "syntax_errors" not in C.completeness_for(st, ["method:Store.load"])


def test_span_helpers():
    assert merge([[5, 6], [1, 1], [2, 3], [9, 9]]) == [[1, 3], [5, 6], [9, 9]]
    e = {"file": "A.swift", "spans": [[1, 2], [5, 5], [7, 7], [9, 9]], "errors": 4, "decls_lost": 2, "lost": ["a:1"]}
    assert span_text(e) == "A.swift:1-2, 5, 7 +1 (2 declarations lost: a:1 …)"
    assert span_text({"file": "b.py", "spans": [[3, 3]], "errors": 1, "decls_lost": 2, "parse_failed": True}) == \
        "b.py:3 (parse failed, 2 declarations lost)"


def _index(tmp: Path, files: dict) -> dict:
    root = tmp / "p"
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    index_project(root, tmp / "g.db", "p")
    return {e["language"]: e for e in cov_of(tmp / "g.db")["languages"]}


def test_python_parse_failure_is_listed_with_its_line(tmp_path):
    cov = _index(tmp_path, {"app/ok.py": "def fine():\n    return 1\n",
                            "app/bad.py": "def lost_a():\n    return 1\n\ndef broken(:\n    pass\n\nclass LostB:\n    pass\n"})
    se = cov["python"]["syntax_errors"]
    assert [e["file"] for e in se] == ["app/bad.py"]
    assert se[0]["parse_failed"] and se[0]["spans"] == [[4, 4]] and se[0]["decls_lost"] == 3
    assert cov["python"]["parsed_with_errors"] == 0 and cov["python"]["syntax_error_files"] == 1


def test_kotlin_syntax_errors_are_listed(tmp_path):
    pytest.importorskip("tree_sitter_kotlin")
    cov = _index(tmp_path, {"src/main/kotlin/A.kt": """
        class A {
            fun kept() = 1
        }

        fun broken(a: Int = { {

        fun after() = 2
        """})
    se = cov["kotlin"]["syntax_errors"]
    assert [e["file"] for e in se] == ["src/main/kotlin/A.kt"] and se[0]["spans"]


@pytest.mark.skipif(not shutil.which("php"), reason="php not installed")
def test_php_parse_failure_is_listed_with_its_line(tmp_path):
    cov = _index(tmp_path, {"composer.json": "{}", "src/Ok.php": "<?php\nclass Ok { function a() { return 1; } }\n",
                            "src/Bad.php": "<?php\nclass Bad {\n    function a() { return 1 }\n}\n"})
    se = cov["php"]["syntax_errors"]
    assert [e["file"] for e in se] == ["src/Bad.php"] and se[0]["parse_failed"] and se[0]["spans"][0][0] in (3, 4)


def test_typescript_syntax_errors_are_listed(tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    from sample import EXTRACTOR_DEPS
    if not EXTRACTOR_DEPS.exists():
        pytest.skip("run `npm ci` in cg_code_graph/plugins/ts/extractor")
    cov = _index(tmp_path, {"tsconfig.json": '{"include": ["src"]}', "package.json": '{"name": "x"}',
                            "src/a.ts": "export function ok() { return 1 }\n\nexport function broken(a: number {\n"
                                        "  return a\n}\n\nexport class After {}\n"})
    se = cov["typescript"]["syntax_errors"]
    assert [e["file"] for e in se] == ["src/a.ts"] and se[0]["spans"][0][0] == 3

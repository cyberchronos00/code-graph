"""`cg snippet`: print one symbol's source by symbol or node id, with context, max-lines
truncation, ambiguity listing, and the no-end-line fallback. Fixtures are written from scratch."""
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

SRC = {
    "pkg/__init__.py": "",
    "pkg/shapes.py": '''
        class Circle:
            def __init__(self, r):
                self.r = r

            def area(self):
                return 3.14 * self.r * self.r


        def helper(x):
            return x + 1
    ''',
    "pkg/other.py": '''
        def helper(y):
            return y - 1
    ''',
}


def build(tmp):
    root = tmp / "proj"
    for rel, body in SRC.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    db = tmp / "proj.db"
    index_project(root, db, "proj")
    return GraphStore(db), db


def cg(*args):
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "snippet", *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)


def test_snippet_prints_symbol_source(tmp_path):
    st, _ = build(tmp_path)
    n = st.node("class:pkg.shapes.Circle")
    assert n and n["end_line"] and n["end_line"] > n["line"]
    res = Q.snippet(st, "Circle")
    assert res["status"] == "ok"
    assert res["start"] == n["line"] and res["end"] == n["end_line"]
    assert res["lines"][0] == [n["line"], "class Circle:"]
    text = Q.render_snippet(res)
    assert text.splitlines()[0] == f'{n["file"]}:{n["line"]}-{n["end_line"]}'
    assert f'{n["line"]}| class Circle:' in text


def test_snippet_context_and_truncation(tmp_path):
    st, _ = build(tmp_path)
    n = st.node("method:pkg.shapes.Circle.area")
    res = Q.snippet(st, "Circle.area", context=2)
    assert res["shown_start"] == max(1, n["line"] - 2)
    assert res["lines"][0][0] == res["shown_start"]
    full = Q.snippet(st, "Circle")
    res2 = Q.snippet(st, "Circle", max_lines=2)
    assert len(res2["lines"]) == 2
    assert res2["truncated"] == len(full["lines"]) - 2
    assert f'truncated, {res2["truncated"]} more lines' in Q.render_snippet(res2)


def test_snippet_ambiguous_lists_candidates(tmp_path):
    st, db = build(tmp_path)
    res = Q.snippet(st, "helper")
    assert res["status"] == "ambiguous" and res["count"] >= 2
    assert len({c["id"] for c in res["candidates"]}) >= 2
    assert "name one" in Q.render_snippet(res)
    r = cg("helper", "--db", db)
    assert r.returncode == 1 and "nodes match 'helper'" in r.stdout


def test_snippet_none_and_cli_ok(tmp_path):
    st, db = build(tmp_path)
    assert Q.snippet(st, "Nope")["status"] == "none"
    r = cg("Nope", "--db", db)
    assert r.returncode == 1 and "no node matches" in r.stdout
    r2 = cg("Circle", "--db", db)
    assert r2.returncode == 0
    import re
    assert re.match(r".+:\d+-\d+$", r2.stdout.splitlines()[0])  # file:start-end header
    assert "class Circle:" in r2.stdout


def test_snippet_no_end_line_fallback(tmp_path):
    st, _ = build(tmp_path)
    st.db.execute("UPDATE nodes SET end_line=NULL WHERE id=?", ("function:pkg.other.helper",))
    st.db.commit()
    res = Q.snippet(st, "function:pkg.other.helper")
    assert res["status"] == "ok" and res["end"] == res["start"]
    assert len(res["lines"]) == 1 and res["note"] and "no end line" in res["note"]
    assert "note: no end line" in Q.render_snippet(res)

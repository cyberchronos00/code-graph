"""Same-name impact entries in one directory gain a file suffix (#207)."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.model import Edge, Node  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

@pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm install` in cg_code_graph/plugins/ts/extractor")
def test_three_same_name_functions_one_dir(tmp_path):
    root = tmp_path / "shelf"
    for name in ("a", "b", "c"):
        p = root / "src" / "shelf" / f"{name}.ts"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("export function formatLabel() { return target() }\nfunction target() { return 1 }\n")
    (root / "package.json").write_text('{"name":"shelf"}\n')
    (root / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}\n')
    db = tmp_path / "s.db"
    index_project(root, db, "shelf")
    for flag in ([], ["--no-paths"]):
        p = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "impact", "target", "--db", str(db), *flag],
                           cwd=ROOT, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-400:]
        for base in ("a.ts", "b.ts", "c.ts"):
            assert f"formatLabel ({base})" in p.stdout, p.stdout
    import cg_code_graph.mcp_server as M
    old = M.STATE["db"]
    M.STATE["db"] = str(db)
    try:
        for paths in (True, False):
            text = M.impact("target", paths=paths)
            for base in ("a.ts", "b.ts", "c.ts"):
                assert f"formatLabel ({base})" in text, text
    finally:
        M.STATE["db"] = old


def test_unique_suffix_when_basenames_collide(tmp_path):
    st = GraphStore.create(tmp_path / "g.db")
    nodes = [
        Node("function:target", "function", "target", fqn="target", file="t.ts", line=1, lang="ts"),
        Node("function:src/a/x.ts#formatLabel", "function", "formatLabel", fqn="formatLabel", file="src/a/x.ts", line=2, module="src", lang="ts"),
        Node("function:src/b/x.ts#formatLabel", "function", "formatLabel", fqn="formatLabel", file="src/b/x.ts", line=3, module="src", lang="ts"),
    ]
    edges = [
        Edge(nodes[1].id, nodes[0].id, "CALLS", nodes[1].file, 2, "exact"),
        Edge(nodes[2].id, nodes[0].id, "CALLS", nodes[2].file, 3, "exact"),
    ]
    st.write(nodes, edges)
    p = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "impact", "target", "--db", str(tmp_path / "g.db"), "--no-paths"],
                       cwd=ROOT, capture_output=True, text=True)
    assert "formatLabel (a/x.ts)" in p.stdout and "formatLabel (b/x.ts)" in p.stdout, p.stdout

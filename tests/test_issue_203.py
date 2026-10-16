"""`Name@N` and `Name@N@line` resolve like the kind-prefixed id (#203)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.model import Node  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402


def _db(tmp_path):
    st = GraphStore.create(tmp_path / "v.db")
    nodes = [
        Node("method:X.m", "method", "m", fqn="X.m", file="Sources/App/X.swift", line=4, lang="swift"),
        Node("method:X.m@24", "method", "m", fqn="X.m", file="Sources/App/X.swift", line=24, lang="swift"),
        Node("function:formatLabel@8", "function", "formatLabel", fqn="formatLabel", file="a.swift", line=8, lang="swift"),
        Node("class:Printer@3", "class", "Printer", fqn="Printer", file="a.swift", line=3, lang="swift"),
        Node("constant:shared@2@11", "constant", "shared", fqn="shared", file="a.swift", line=11, lang="swift"),
    ]
    st.write(nodes, [])
    return st


def test_short_variant_matches_kind_prefix(tmp_path):
    st = _db(tmp_path)
    assert Q.resolve_targets(st, "X.m@24") == Q.resolve_targets(st, "method:X.m@24") == ["method:X.m@24"]
    assert Q.resolve_targets(st, "formatLabel@8") == ["function:formatLabel@8"]
    assert Q.resolve_targets(st, "Printer@3") == ["class:Printer@3"]
    assert Q.resolve_targets(st, "shared@2@11") == ["constant:shared@2@11"]
    assert set(Q.resolve_targets(st, "X.m")) == {"method:X.m", "method:X.m@24"}


def test_cli_node_impact_and_mcp_callers(tmp_path):
    st = _db(tmp_path)
    db = tmp_path / "v.db"
    import cg_code_graph.mcp_server as M
    old = M.STATE["db"]
    M.STATE["db"] = str(db)
    try:
        for spec in ("X.m@24", "formatLabel@8", "Printer@3", "shared@2@11"):
            nid = Q.resolve_targets(st, spec)[0]
            for cmd in ("node", "impact"):
                p = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", cmd, spec, "--db", str(db)],
                                   cwd=ROOT, capture_output=True, text=True)
                assert p.returncode == 0, (cmd, spec, p.stdout, p.stderr[-300:])
                assert nid in p.stdout
            assert "not found" not in M.node(spec)
            assert "not found" not in M.callers(spec)
    finally:
        M.STATE["db"] = old

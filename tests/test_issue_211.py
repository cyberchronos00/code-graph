"""impact --platform divergence hint only when divergence has a row for the target (#211)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core.model import Edge, Node  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402

HINT = "platforms divergence --target"


def _graph(tmp_path, divergence):
    st = GraphStore.create(tmp_path / "g.db")
    method = Node("method:Exporter.share", "method", "share", fqn="Exporter.share", file="Exporter.swift", line=4,
                  lang="swift", attrs={"platforms": ["ios", "macos"]})
    caller = Node("function:shareFromPhone", "function", "shareFromPhone", fqn="shareFromPhone", file="Phone.swift",
                  line=2, lang="swift", attrs={"platforms": ["ios"]})
    st.write([method, caller], [Edge(caller.id, method.id, "CALLS", caller.file, 3, "exact",
                                      attrs={"platforms": ["ios"]})])
    st.set_meta(project="app", stats={"platforms": {
        "targets": ["ios", "macos", "visionos"],
        "divergence": divergence,
    }})
    return tmp_path / "g.db"


def test_hint_hidden_when_divergence_has_no_row(tmp_path):
    db = _graph(tmp_path, {"variants": [], "api_surface": [], "missing_callee": [], "counts": {}})
    p = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "impact", "Exporter.share", "--platform", "visionos",
                        "--db", str(db)], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-300:]
    assert "not built for visionos" in p.stdout
    assert HINT not in p.stdout, p.stdout


def test_hint_shown_when_divergence_lists_the_method(tmp_path):
    db = _graph(tmp_path, {"variants": [], "api_surface": [], "counts": {}, "missing_callee": [{
        "from": "shareFromHeadset", "kind": "CALLS", "to": "method:Exporter.share", "at": "Head.swift:2",
        "missing_on": ["visionos"], "callee_platforms": ["ios", "macos"],
    }]})
    p = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "impact", "Exporter.share", "--platform", "visionos",
                        "--db", str(db)], cwd=ROOT, capture_output=True, text=True)
    assert HINT in p.stdout and "visionos" in p.stdout, p.stdout

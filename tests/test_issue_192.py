"""Coverage buckets add up, javascript label, excluded reason, single-repo note (#192)."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import coverage as C  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402


def test_buckets_sum_label_and_excluded_reason(tmp_path):
    root = tmp_path / "shop"
    root.mkdir()
    (root / "a.js").write_text("export const n = 1\n")
    (root / "b.js").write_text("export const n = 2\n")
    (root / "vite.config.js").write_text("export default {}\n")
    (root / "widget.test.js").write_text("export {}\n")
    (root / "kept.js").write_text("export {}\n")
    (root / "tool.sh").write_text("#!/bin/sh\necho hi\n")
    plugins = {"typescript": {"status": "ok", "mode": "exact", "nodes": 1, "program_files": 1}}
    reports = {"typescript": {"seen": ["a.js", "b.js"], "roots": ["./"], "parse_failed": [], "unmapped": [], "excluded": []}}
    cov = C.compute(root, plugins, reports=reports)
    for e in cov["languages"]:
        buckets = sum(e.get(b, 0) for b in C.BUCKETS)
        if "indexed" in e:
            assert e["indexed"] + buckets == e["files"] == e.get("discovered", e["files"]), e
    js = next(e for e in cov["languages"] if e["language"] == "typescript")
    assert js["label"] == "javascript"
    assert "excluded" in C._entry_text(js)
    reasons = js["exclude_reasons"]
    assert reasons["vite.config.js"] == "tool config"
    assert reasons["widget.test.js"] == "test file"
    db = tmp_path / "shop.db"
    st = GraphStore.create(db)
    st.set_meta(project="shop", stats={"coverage": cov})
    text = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "coverage", "--db", str(db)],
                          cwd=ROOT, capture_output=True, text=True).stdout
    details = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "coverage", "--db", str(db), "--details"],
                             cwd=ROOT, capture_output=True, text=True).stdout
    raw = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "coverage", "--db", str(db), "--json"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    assert "javascript" in text and "excluded" in text
    assert "vite.config.js (tool config)" in details
    doc = json.loads(raw)
    for e in doc["shop"]["languages"]:
        if "indexed" in e:
            assert e["indexed"] + sum(e.get(b, 0) for b in C.BUCKETS) == e["files"]
    assert doc["shop"]["languages"][0]["language"] == "typescript" or any(
        e.get("label") == "javascript" and e["language"] == "typescript" for e in doc["shop"]["languages"])
    note = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "reaches", "missing", "--db", str(db)],
                          cwd=ROOT, capture_output=True, text=True).stdout
    assert "unsupported: sh " in note and "shop/sh" not in note


def test_combined_graph_keeps_repo_prefix():
    uns = {"api/sh": 3, "web/sh": 1}
    text = C._unsupported_text(uns)
    assert "api/sh 3" in text and "web/sh 1" in text

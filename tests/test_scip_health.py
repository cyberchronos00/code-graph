"""An SCIP index cg cannot use (occurrences without a readable position, no definitions, none matched) is reported
in `cg coverage` (`warnings`) and by `cg doctor --scip` instead of adding nothing silently."""
import sqlite3  # noqa: F401
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cg_code_graph" / "plugins" / "scip"))
import scip_pb2  # noqa: E402

from cg_code_graph import coverage, doctor  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "kotlin_mixed_fixture"
GOOD = FIX / "index.scip"


def _write(tmp_path, name, mutate) -> Path:
    i = scip_pb2.Index()
    i.ParseFromString(GOOD.read_bytes())
    for d in i.documents:
        for o in d.occurrences:
            mutate(o)
    p = tmp_path / name
    p.write_bytes(i.SerializeToString())
    return p


def _nopos(o):
    del o.range[:]
    o.ClearField("enclosing_range")


def _nodefs(o):
    o.symbol_roles &= ~scip_pb2.SymbolRole.Definition


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("CG_KOTLIN_SCIP", raising=False)
    monkeypatch.delenv("CG_KOTLIN_SCIP_FILE", raising=False)
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))


def test_doctor_scip_health(tmp_path, monkeypatch):
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))
    ok = doctor.scip_health(GOOD)
    assert ok["occurrences"] == ok["positioned"] > 0 and ok["definitions"] > 0 and "warning" not in ok
    bad = doctor.scip_health(_write(tmp_path, "nopos.scip", _nopos))
    assert bad["positioned"] == 0 and "none with a usable position" in bad["warning"]
    nd = doctor.scip_health(_write(tmp_path, "nodefs.scip", _nodefs))
    assert nd["definitions"] == 0 and "0 definitions" in nd["warning"]
    junk = tmp_path / "junk.scip"
    junk.write_bytes(b"\xff\xfe not protobuf")
    assert "cannot be read" in doctor.scip_health(junk)["warning"]
    text = doctor.render(doctor.report(scip=[str(tmp_path / "nopos.scip")]))
    assert "0 with a usable position" in text and "warning: SCIP index nopos.scip" in text


def test_generic_importer_warns(tmp_path, isolated):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "README.md").write_text("no sources\n")
    bad = _write(tmp_path, "nopos.scip", _nopos)
    st = index_project(proj, tmp_path / "g.db", "p", scip=[str(bad)])
    imp = st["plugins"][f"scip:{bad}"]
    assert imp["occurrences"] and imp["positioned"] == 0 and "usable position" in imp["warning"]
    assert any("nopos.scip" in w for w in st["coverage"]["warnings"])
    assert "  warning: SCIP index nopos.scip" in coverage.render({"": st["coverage"]})
    good = index_project(proj, tmp_path / "h.db", "p", scip=[str(GOOD)])
    assert "warning" not in good["plugins"][f"scip:{GOOD}"] and "warnings" not in good["coverage"]


def test_kotlin_exact_layer_warns(tmp_path, isolated):
    pytest.importorskip("tree_sitter_kotlin")
    bad = _write(tmp_path, "nopos.scip", _nopos)
    st = index_project(FIX, tmp_path / "g.db", "k", scip=[str(bad)])
    w = st["plugins"]["kotlin"]["scip"]["warning"]
    assert "usable position" in w
    assert any(x.startswith("kotlin: SCIP index nopos.scip") for x in st["coverage"]["warnings"])
    good = index_project(FIX, tmp_path / "h.db", "k", scip=[str(GOOD)])
    assert "warning" not in good["plugins"]["kotlin"]["scip"] and "warnings" not in good["coverage"]


def test_kotlin_no_definition_matched_warns(tmp_path, isolated):
    """Positions readable but shifted (an index of other sources): 0 definitions matched."""
    pytest.importorskip("tree_sitter_kotlin")

    def shift(o):
        if len(o.range):
            o.range[0] += 500
    st = index_project(FIX, tmp_path / "g.db", "k", scip=[str(_write(tmp_path, "shift.scip", shift))])
    w = st["plugins"]["kotlin"]["scip"]["warning"]
    assert "0 of" in w and "matched the Kotlin declarations" in w

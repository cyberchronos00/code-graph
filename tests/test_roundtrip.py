"""`cg roundtrip Type.prop` (#88 phase 2) on tests/roundtrip_fixture/{swift,kotlin,react,python}: the motivating
pattern in each language. A store property is written through a lossy call (clamp / coerceIn / Math.min-max /
round, a `fit*` name, a `.cg.yaml` `lossy:` name, an `@cg-lossy` function), directly, through a local or through a
caller's argument, and read back to seed UI state (init, remember, useState(initial), __init__) next to a wider
range (`0...100`). Findings are heuristic and add no edges."""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import roundtrip as RT  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "roundtrip_fixture"
_S: dict = {}


def db(lang):
    if lang not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-roundtrip-"))
        index_project(FIX / lang, d / "g.db", f"rt-{lang}")
        _S[lang] = d / "g.db"
    return _S[lang]


def rt(lang, spec="LevelStore.level"):
    return RT.roundtrip(GraphStore(db(lang)), spec)


def test_swift_picker_round_trip():
    pytest.importorskip("tree_sitter_swift")
    r = rt("swift")
    assert r["confidence"] == "heuristic"
    (w,) = r["writes"]
    assert w["lossy"]["calls"] == ["fitToGamut"] and w["lossy"]["bounds"] == (0.0, 10.0)   # from the function body
    (f,) = r["findings"]
    assert f["seeds"].startswith("initializer") and f["read"].endswith("Picker.swift:30")
    assert f["wider_ranges"][0]["range"] == [0.0, 100.0] and f["wider_ranges"][0]["at"].endswith(":34")
    t = rt("swift", "LevelStore.tint")["writes"][0]["lossy"]
    assert t["how"] == "local `c`" and t["calls"] == ["clamped"] and t["bounds"] == (0.0, 5.0)
    p = rt("swift", "LevelStore.plain")["writes"][0]["lossy"]
    assert p["how"].startswith("parameter `v`, from caller function:reset") and p["calls"] == ["round"]


def test_kotlin_compose_round_trip():
    r = rt("kotlin")
    assert r["writes"][0]["lossy"]["calls"] == ["coerceIn"] and r["writes"][0]["lossy"]["bounds"] == (0.0, 10.0)
    (f,) = r["findings"]
    assert f["seeds"] == "remember" and f["wider_ranges"][0]["range"] == [0.0, 100.0]


def test_react_round_trip():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    r = rt("react")
    assert set(r["writes"][0]["lossy"]["calls"]) == {"min", "max"}
    (f,) = r["findings"]
    assert f["seeds"] == "useState(initial)" and f["wider_ranges"][0]["range"] == [0.0, 100.0]


def test_python_round_trip_config_and_tag():
    r = rt("python")
    assert r["lossy_sources"] == ["built-in", ".cg.yaml lossy", "@cg-lossy"]
    lz = [w for w in r["writes"] if w["lossy"]]
    assert [w["lossy"]["calls"] for w in lz] == [["round"]]
    assert [x["seeds"] for x in r["reads"]] == ["initializer (__init__)"]
    assert rt("python", "LevelStore.speed")["writes"][1]["lossy"]["calls"] == ["squash"]      # .cg.yaml lossy
    assert rt("python", "LevelStore.gain")["writes"][1]["lossy"]["calls"] == ["tame"]         # @cg-lossy
    assert len(rt("python", "LevelStore.gain")["findings"]) == 1


def test_no_edges_added_and_cli_json(tmp_path):
    before = sqlite3.connect(db("python")).execute("select count(*) from edges").fetchone()[0]
    out = subprocess.run([sys.executable, "-m", "codegraph.cli", "roundtrip", "LevelStore.level", "--db", str(db("python")),
                          "--json"], capture_output=True, text=True, check=True, cwd=ROOT).stdout
    j = json.loads(out)
    assert j["confidence"] == "heuristic" and j["findings"][0]["confidence"] == "heuristic"
    assert sqlite3.connect(db("python")).execute("select count(*) from edges").fetchone()[0] == before
    txt = subprocess.run([sys.executable, "-m", "codegraph.cli", "roundtrip", "LevelStore.level", "--db", str(db("python"))],
                         capture_output=True, text=True, check=True, cwd=ROOT).stdout
    assert "heuristic" in txt and "SEEDS initializer" in txt
    assert "no stored property" in RT.render(RT.roundtrip(GraphStore(db("python")), "Nope.x"))


def test_mcp_tool(monkeypatch):
    from codegraph import mcp_server as M
    monkeypatch.setattr(M, "_st", lambda: GraphStore(db("kotlin")))
    fn = getattr(M.roundtrip, "fn", M.roundtrip)
    out = fn("LevelStore.level")
    assert "findings (1, heuristic)" in out and "un-narrowed range 0…100" in out

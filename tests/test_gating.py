"""Unit tests for the deterministic guard evaluator on a synthetic PHP fixture (tests/gating_fixture)."""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402
from sample import needs_php  # noqa: E402

FIX = ROOT / "tests" / "gating_fixture"
GATES = ROOT / "examples" / "bookstore.gates.json"
SCENARIO = "new_inventory"
_DB = None


def db():
    global _DB
    if _DB is None:
        p = Path(tempfile.mkdtemp()) / "fx.db"
        index_project(FIX, p, "gating-fixture", gates=str(GATES))
        _DB = sqlite3.connect(p)
    return _DB


def calls(method, target):
    return db().execute("SELECT line, gate FROM edges WHERE kind='CALLS' AND src=? AND dst=? ORDER BY line",
                        (f"method:App\\Support\\Sample::{method}", f"method:App\\Support\\{target}::hit")).fetchall()


def gated(method, target="OldFlow"):
    rows = calls(method, target)
    assert rows, f"no CALLS edge {method} -> {target}"
    return [g == SCENARIO for _, g in rows]


@needs_php
def test_predicates_derived():
    preds = dict(db().execute("SELECT method, value FROM gate_predicates").fetchall())
    assert preds.get("method:App\\Support\\Flags::on") == "true"
    assert preds.get("method:App\\Support\\FeatureGate::usesNewInventory") == "true"
    assert preds.get("method:App\\Support\\FeatureGate::oldMode") == "false"


@needs_php
def test_branch_shapes_gate_old_side():
    for m in ("ifElse", "earlyReturn", "negated", "ternary", "elseifChain", "inClosure", "matchArm"):
        assert all(gated(m)), m
    assert gated("viaVariable") == [True, True, True]


@needs_php
def test_new_side_and_unrelated_code_stay_live():
    for m in ("ifElse", "earlyReturn", "negated", "ternary", "matchArm"):
        assert not any(gated(m, "NewFlow")), m
    assert not any(gated("unknownStaysLive"))
    assert not any(gated("unguarded"))

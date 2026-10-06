"""Swift heuristic calls on SDK receivers (#70): the full selector (argument labels, arity) has to fit, static members
only through the type and instance members only through a value, SDK values (modifier chains, `Font`,
`NotificationCenter.default`, `UIApplication.shared`, closure parameters) reach only project extensions, and SDK
selectors (`contains(_:)`, `resume(returning:)`, `.accessibilityIdentifier(_:)` ...) on unknown receivers stay
unbound; a labelled selector that exactly one project method declares is kept through optional / force-unwrapped /
untyped receivers (`region!.contains(normalized:y:)`)."""
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "swift_sdk_fixture"
_S: dict = {}


def con():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-swift-sdk-"))
        _S["stats"] = index_project(FIX, d / "g.db", "swift-sdk")
        _S["db"] = d / "g.db"
    return sqlite3.connect(_S["db"])


def calls(src=None, dst=None):
    q = "select src, dst, line, confidence from edges where kind='CALLS'"
    return [(s, d, ln, c) for s, d, ln, c in con().execute(q) if (src is None or s == src) and (dst is None or d == dst)]


def test_issue_repro_one_edge():
    body = calls("method:SwatchView.body")
    assert [(d, ln) for _s, d, ln, _c in body] == [("method:Palette.accessibilityIdentifier", 22)]
    for d in ("method:Coordinator.accessibilityLabel", "method:Coordinator.draw", "method:Palette.weight"):
        assert not calls(dst=d)


def test_labelled_selector_kept_through_optional_and_untyped_receivers():
    hit = sorted(ln for _s, _d, ln, _c in calls("method:Selection.hit", "method:Region.contains"))
    assert hit == [15, 16, 17]                         # region?. / current!. / other.region!.
    probe = calls("method:Selection.probe", "method:Region.contains")
    assert [(ln, c) for _s, _d, ln, c in probe] == [(24, "heuristic")]   # lookup["x"]!.contains(normalized:y:)
    assert len(calls(dst="method:Region.contains")) == 4               # not tags.contains / items.contains(where:)


def test_sdk_receivers_and_selectors():
    assert [ln for _s, _d, ln, _c in calls(dst="method:VideoModel.resume")] == [14]    # not continuation.resume(returning:)
    assert sorted(ln for _s, _d, ln, _c in calls(dst="method:Client.post")) == [20, 32]  # not NotificationCenter.post
    assert not calls(dst="method:SafariManager.open")                               # UIApplication.shared.open
    assert _S["stats"]["plugins"]["swift"]["calls_sdk_selector"] >= 1


def test_static_and_instance():
    make = calls(dst="method:Client.make")
    assert [(s, ln) for s, _d, ln, _c in make] == [("method:Notifier.announce", 33)]    # Client.make(), not c.make()
    assert ("method:Notifier.announce", "method:Client.send", 34, "heuristic") in calls()


def test_overloads_by_labels_and_project_extensions():
    fmt = sorted(ln for s, _d, ln, _c in calls(dst="method:Formatter.format"))
    assert fmt == [12]                                                # amount: / date: / date:style: (one edge per line)
    assert not calls("method:Report.bad")                             # format(currency:) fits no overload
    card = {d for _s, d, _l, _c in calls("method:Card.body")}
    assert card == {"method:View.cardStyle", "method:Font.emphasized"}


def test_collection_methods_unchanged():
    # #58: standard-library members on SDK / unknown receivers stay unbound
    assert not calls("method:Region.contains")                        # points.contains(point): [Double]

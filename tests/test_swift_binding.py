"""Swift heuristic binding regressions from 0.8.0 (#83), on tests/swift_binding_fixture: a prefix `!` / `-` is not
part of the receiver, a property / local built with a generic initializer (`SlotGate<Image>()`) types its calls,
locals typed by literals or SDK initializers (`var inside = false`, `Path()`) do not bind to a project method of the
same name, a call bound by its selector alone is marked `binding: name` and kept out of platform divergence, static
and instance members of one name are separate nodes, and a continuation line after a binary operator keeps its
call (and its own line); `Module.function()` reaches a free function of that target folder; an unknown receiver
whose selector fits several project methods gets a `candidate` edge to each (flagged by `cg tests` / impact, left
out of divergence). Caller counts are checked against a plain text search of the fixture."""
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from codegraph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "swift_binding_fixture"
_S: dict = {}


def con():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-swift-binding-"))
        _S["stats"] = index_project(FIX, d / "g.db", "swift-binding")
        _S["db"] = d / "g.db"
    return sqlite3.connect(_S["db"])


def calls(dst=None, src=None):
    q = "select src, dst, line, attrs from edges where kind in ('CALLS', 'TEST_CALLS')"
    return [(s, d, ln, json.loads(a or "{}")) for s, d, ln, a in con().execute(q)
            if (dst is None or d == dst) and (src is None or s == src)]


def grep(pattern):
    """(file, line) of every match in the fixture's Swift sources, like `rg -n`."""
    out = []
    for f in sorted(FIX.rglob("*.swift")):
        for i, text in enumerate(f.read_text().splitlines(), 1):
            if re.search(pattern, text):
                out.append((str(f.relative_to(FIX)), i))
    return out


def sites(dst):
    q = "select file, line from edges where kind in ('CALLS', 'TEST_CALLS') and dst = ?"
    return sorted(con().execute(q, (dst,)).fetchall())


def test_prefix_not_is_not_a_receiver():
    # `#expect(!Preview.matches(a, b))`, `if !Chrome.shouldAutoPresent()` (0.8.0 dropped both)
    assert sites("method:Preview.matches") == grep(r"Preview\.matches\(") and len(grep(r"!Preview\.matches\(")) == 2
    assert sites("method:Chrome.shouldAutoPresent") == grep(r"Chrome\.shouldAutoPresent\(")
    tests = {s for s, _d, _l, _a in calls("method:Preview.matches") if s.startswith("function:")}
    assert tests == {"function:matchesPositive", "function:matchesNegative"}
    # a module-qualified free function behind `!` (`try #require(!Styleguide.registerFonts() == false)`)
    assert sites("function:registerFonts") == grep(r"Styleguide\.registerFonts\(") != []


def test_generic_initializer_types_the_receiver():
    # `@State private var gate = SlotGate<Image>()` / `let g = SlotGate<String>()`: `cancel()` is also an SDK
    # selector and declared twice in the project, so only the receiver type can bind it
    assert sites("method:SlotGate.cancel") == grep(r"\b(gate|g)\.cancel\(")
    assert sites("method:SlotGate.requestFull") == grep(r"gate\.requestFull\(")
    assert not calls("method:Upload.cancel")


def test_known_non_app_receivers_do_not_bind_by_name():
    # `var inside = false; inside.toggle()`, `Path().close()`, `UIGraphicsPDFRenderer(...).pdfData { }`
    assert [(s, ln) for s, _d, ln, _a in calls("method:HUDController.toggle")] == [("method:Panel.unknown", 36)]
    assert sites("method:SomeClient.close") == grep(r"client\.close\(")
    assert not calls("method:CGImage.pdfData")
    # an untyped receiver still binds a unique selector, marked as bound by name
    (_s, _d, _l, a), = calls("method:HUDController.toggle")
    assert a.get("binding") == "name"
    assert all("binding" not in a for _s, d, _l, a in calls()
               if d != "method:HUDController.toggle" and not d.endswith("Store.reload"))
    # `UIBezierPath().close()`, `Path { p in p.close() }`, `UIGraphicsPDFRenderer(bounds: .zero).pdfData { }`
    assert not calls(src="method:Drawing.shapes")
    assert _S["stats"]["plugins"]["swift"]["calls_by_name_only"] == 1


def test_name_only_binding_is_not_a_divergence_finding():
    # HUDController exists only under `#if os(macOS)`; `mystery.toggle()` (untyped) is not reported as a call to
    # code missing on the other targets, and `inside.toggle()` (a Bool) is not bound at all
    from codegraph.core.store import GraphStore
    from codegraph import platforms as PF
    con()
    d = PF.divergence(GraphStore(str(_S["db"])))
    assert set(d["targets"]) - {"macos"}                     # targets the class is missing on
    assert not [f for f in d["missing_callee"] if f["to"] == "method:HUDController.toggle"]


def test_static_and_instance_overloads_are_separate_nodes():
    ids = {r[0] for r in con().execute("select id from nodes where fqn = 'Weights.weight'")}
    assert ids == {"method:Weights.weight", "method:Weights.weight~static"}
    static = sites("method:Weights.weight~static")
    assert static == grep(r"(?<!func )weight\(from:")              # Self. / Weights. / bare in a static func
    inst = calls("method:Weights.weight")
    assert sorted((s, ln) for s, _d, ln, _a in inst) == [("function:weights", 15), ("method:Weights.total", 36)]
    a = {r[0]: json.loads(r[1] or "{}") for r in con().execute("select id, attrs from nodes where fqn = 'Weights.weight'")}
    assert a["method:Weights.weight~static"].get("static") and not a["method:Weights.weight"].get("static")


def test_continuation_line_after_binary_operator():
    # `let ok = a` / `    < Scorer.score(x)` (a parse error in the grammar) and `+ Scorer.score(x)` (parsed as
    # `(a + Scorer).score(x)`): one edge each, on the operator's line
    assert sites("method:Scorer.score") == grep(r"Scorer\.score\(")
    assert [ln for _s, _d, ln, _a in calls("method:Scorer.score")] == [41, 43]


def test_unknown_receiver_with_ambiguous_selector_gives_candidates():
    # `let store = registry.store(for: id); store.reload(force: true)`: FeedStore and InboxStore both fit; 0.8.0
    # dropped the call (and the test's), so `cg tests` found no test of either
    from codegraph import query as Q
    from codegraph.core.store import GraphStore
    for dst in ("method:FeedStore.reload", "method:InboxStore.reload"):
        got = sorted((s, ln, a.get("binding"), a.get("candidates")) for s, _d, ln, a in calls(dst))
        assert got == [("function:reloadsStores", 6, "candidate", 2), ("method:Refresher.refreshAll", 17, "candidate", 2)]
        assert sites(dst) == grep(r"store\.reload\(")
    st = GraphStore(str(_S["db"]))
    res = Q.tests_covering(st, "FeedStore.reload")
    assert [(t["name"], t.get("candidate")) for t in res["direct"]] == [("reloadsStores", True)]
    out = Q.render_tests_covering(res)
    assert "(candidate)" in out and Q.CANDIDATE_NOTE in out
    imp = Q.impact(st, "FeedStore.reload")
    assert [(c["fqn"], c.get("candidate")) for c in imp["callers"]] == [("Refresher.refreshAll", True)]
    assert "(candidate)" in Q.caller_label(imp["callers"][0])
    # a typed receiver is never a candidate
    assert not Q.impact(st, "SlotGate.cancel")["callers"][0].get("candidate")
    assert _S["stats"]["plugins"]["swift"]["call_candidate_edges"] == 4


def test_candidate_edges_are_not_divergence_findings():
    # InboxStore exists only under `#if os(iOS)`: the candidate call to it is no `missing_callee` elsewhere
    from codegraph.core.store import GraphStore
    from codegraph import platforms as PF
    con()
    d = PF.divergence(GraphStore(str(_S["db"])))
    assert not [f for f in d["missing_callee"] if f["to"] == "method:InboxStore.reload"]

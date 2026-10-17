"""Swift selector candidates narrowed by a local's known type or a single visible declaring type (#218)."""
import sqlite3

import pytest

pytest.importorskip("tree_sitter_swift")
from cg_code_graph.indexer import index_project  # noqa: E402

SHELF = """\
final class ShelfBridge {
    func refreshStatus() {}
    static func shared() -> ShelfBridge { ShelfBridge() }
}

final class LabelReader {
    func refreshStatus() {}
}

func makeBridge() -> ShelfBridge { ShelfBridge() }
func makeAnything() -> AnyObject { LabelReader() }

func reload() {
    let r = makeBridge()
    r.refreshStatus()
}

func reloadShared() {
    let s = try? ShelfBridge.shared()
    s?.refreshStatus()
}

func reloadUnknown() {
    let u = makeAnything()
    u.refreshStatus()
}

func syncAll() {
    let x = makeAnything()
    x.syncBadges()
}
"""

BADGES = """\
final class BadgeCounter {
    func syncBadges() {}
}

private final class BadgeCache {
    func syncBadges() {}
}

final class BadgeStore {
    fileprivate func syncBadges() {}
}

func localSync() {
    let y = makeAnything()
    y.syncBadges()
}
"""


@pytest.fixture(scope="module")
def edges(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("i218")
    root = tmp / "app"
    (root / "Sources").mkdir(parents=True)
    (root / "Sources" / "Shelf.swift").write_text(SHELF, encoding="utf-8")
    (root / "Sources" / "Badges.swift").write_text(BADGES, encoding="utf-8")
    db = tmp / "g.db"
    index_project(root, db, "shelf")
    con = sqlite3.connect(db)
    out = {}
    for src, dst, attrs in con.execute("select src, dst, attrs from edges where kind='CALLS'"):
        out.setdefault(src, []).append((dst, attrs or ""))
    return out


def _targets(edges, caller, name):
    return sorted((d, '"candidate"' in a) for d, a in edges.get(caller, []) if d.endswith("." + name))


def test_local_from_a_function_with_a_project_return_type(edges):
    assert _targets(edges, "function:reload", "refreshStatus") == [("method:ShelfBridge.refreshStatus", False)]


def test_local_from_a_static_factory(edges):
    assert _targets(edges, "function:reloadShared", "refreshStatus") == [("method:ShelfBridge.refreshStatus", False)]


def test_sdk_return_type_stays_a_candidate(edges):
    got = _targets(edges, "function:reloadUnknown", "refreshStatus")
    assert got == [("method:LabelReader.refreshStatus", True), ("method:ShelfBridge.refreshStatus", True)]


def test_one_visible_declaring_type_binds_by_name(edges):
    # BadgeCache is private and BadgeStore.syncBadges fileprivate, both in another file: only BadgeCounter is visible
    got = [(d, a) for d, a in edges.get("function:syncAll", []) if d.endswith(".syncBadges")]
    assert [d for d, _ in got] == ["method:BadgeCounter.syncBadges"]
    assert '"binding": "name"' in got[0][1]


def test_same_file_still_sees_private_members(edges):
    got = _targets(edges, "function:localSync", "syncBadges")
    assert [d for d, _ in got] == ["method:BadgeCache.syncBadges", "method:BadgeCounter.syncBadges",
                                   "method:BadgeStore.syncBadges"]
    assert all(c for _, c in got)

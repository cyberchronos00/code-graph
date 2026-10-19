"""Dead branches (#222): code under nested conditions that no target meets together (`#elseif os(macOS)` inside
`#if os(iOS)`, `#ifdef __APPLE__` inside `#ifdef __linux__`) is built nowhere. Its references are left out of the
graph and each region is a `dead_branch` divergence finding."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import platforms as PF  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

PANEL = """\
struct Panel {
    #if os(macOS)
    var windowTitle = ""
    #endif
    var count = 0

    #if os(iOS)
    func layout() {
        #if targetEnvironment(macCatalyst)
        badge()
        #elseif os(macOS)
        windowTitle = "Panel"
        print(windowTitle)
        helper()
        #endif
        print(count)
    }
    #endif

    #if os(macOS)
    func title() -> String { windowTitle }
    #if os(iOS)
    func ghost() { helper() }
    #endif
    #endif

    func badge() {}
}

func helper() {}
"""

CLOCK = """\
#ifdef __APPLE__
static int mach_ticks(void) { return 1; }
#endif

static int posix_ticks(void) { return 2; }

#ifdef __linux__
int read_ticks(void) {
#ifdef __APPLE__
  return mach_ticks();
#else
  return posix_ticks();
#endif
}
#endif

#if defined(__unix__)
int bsd_ticks(void) {
#ifdef __NetBSD__
  return posix_ticks();
#endif
  return 0;
}
#endif
"""


TERM = """\
static int cyg_width(void) { return 80; }

#ifndef _WIN32
int term_width(void) {
#ifdef __CYGWIN__
  return cyg_width();
#endif
  return 0;
}
#endif

int term_height(void) {
#ifdef _WIN32
  return cyg_width();
#endif
  return 0;
}
"""


def _build(tmp_path, files, name):
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    st = index_project(tmp_path, tmp_path / f"{name}.db", name)
    return st, sqlite3.connect(tmp_path / f"{name}.db")


def _edges(db, kind=None):
    q = "SELECT src, dst, kind FROM edges WHERE kind NOT IN ('CONTAINS', 'DEFINES', 'GATED_BY')"
    return {r for r in db.execute(q)}


def test_swift_elseif_nested_in_another_platform_is_left_out(tmp_path):
    pytest.importorskip("tree_sitter_swift")
    st, db = _build(tmp_path, {
        "Package.swift": 'let package = Package(name: "App", platforms: [.iOS(.v17), .macOS(.v14)], targets: [])\n',
        "Sources/App/Panel.swift": PANEL}, "panel")
    e = _edges(db)
    assert ("method:Panel.layout", "field:Panel.windowTitle", "WRITES_PROP") not in e
    assert ("method:Panel.layout", "field:Panel.windowTitle", "READS_PROP") not in e
    assert ("method:Panel.layout", "function:helper", "CALLS") not in e
    # live code next to it keeps its references; the macOS method still reads the macOS property
    assert ("method:Panel.layout", "field:Panel.count", "READS_PROP") in e
    assert ("method:Panel.title", "field:Panel.windowTitle", "READS_PROP") in e
    # a Catalyst branch in a project without a Catalyst build is built nowhere by itself: not a contradiction
    assert ("method:Panel.layout", "method:Panel.badge", "CALLS") in e
    regions = st["platforms"]["divergence"]["dead_branch"]
    assert len(regions) == 2
    r = next(r for r in regions if r["references_left_out"])
    assert r["lines"] == [12, 14] and r["references_left_out"] == 3
    assert [c["condition"] for c in r["conditions"]] == ["#if os(iOS)", "#elseif os(macOS)"]
    # a type declared in a dead branch keeps its node, tagged
    s = next(r for r in regions if r["symbols"])
    assert s["symbols"] == ["method:Panel.ghost"]
    assert ("method:Panel.ghost", "function:helper", "CALLS") not in e
    a = json.loads(db.execute("SELECT attrs FROM nodes WHERE id = ?", (s["symbols"][0],)).fetchone()[0])
    assert a["dead_branch"] is True and a["platforms"] == []
    g = GraphStore(tmp_path / "panel.db")
    out = PF.render_divergence(PF.divergence(g, kind="dead_branch"))
    assert "DEAD BRANCHES" in out and "left out: method:Panel.layout -WRITES_PROP-> field:Panel.windowTitle" in out


def test_c_ifdef_apple_inside_linux_is_left_out_but_a_bsd_block_is_not(tmp_path):
    st, db = _build(tmp_path, {"src/clock.c": CLOCK, "src/unix/term.c": TERM}, "clock")
    e = _edges(db)
    assert ("function:read_ticks", "function:src/clock.c#mach_ticks", "CALLS") not in e
    assert ("function:read_ticks", "function:src/clock.c#posix_ticks", "CALLS") in e
    # `__NetBSD__` holds on no known target by itself: code for another build, kept as before
    assert ("function:bsd_ticks", "function:src/clock.c#posix_ticks", "CALLS") in e
    # a POSIX layer on Windows, and a `unix/` directory (a guess from the path) against a Windows check: kept
    assert ("function:term_width", "function:src/unix/term.c#cyg_width", "CALLS") in e
    assert ("function:term_height", "function:src/unix/term.c#cyg_width", "CALLS") in e
    regions = st["platforms"]["divergence"]["dead_branch"]
    assert [(r["file"], r["lines"]) for r in regions] == [("src/clock.c", [9, 10])]
    assert st["platforms"]["dead_branch_references"] == 1


def test_rust_cfg_windows_inside_a_unix_module(tmp_path):
    st, db = _build(tmp_path, {
        "Cargo.toml": '[package]\nname = "deadr"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/lib.rs": "pub fn shared() -> u32 { 1 }\n\n#[cfg(unix)]\nmod posix {\n    #[cfg(windows)]\n"
                      "    pub fn handle() -> u32 { crate::shared() }\n\n    pub fn fd() -> u32 { crate::shared() }\n}\n"}, "deadr")
    e = _edges(db)
    assert ("function:deadr::posix::fd", "function:deadr::shared", "CALLS") in e
    assert ("function:deadr::posix::handle", "function:deadr::shared", "CALLS") not in e
    (r,) = st["platforms"]["divergence"]["dead_branch"]
    assert r["symbols"] == ["function:deadr::posix::handle"]
    assert [c["condition"] for c in r["conditions"]] == ["cfg(unix)", "cfg(windows)"]

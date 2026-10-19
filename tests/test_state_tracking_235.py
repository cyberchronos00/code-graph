"""#235 leftovers: Kotlin `$name` string templates and extension-receiver field reads, a second construction
branch on one line, and Swift fields that share an `@AppStorage` / `@SceneStorage` key.

Fixtures are invented. `SHARES_STORAGE` links the same wrapper and key across types; AppStorage and SceneStorage
of one string stay apart.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402

KT = """\
package app

class Cart(val items: List<Int>) {
    fun label() = "cart $items"
}

fun Cart.total() = items.size

annotation class Composable

@Composable
fun Home(loading: Boolean) {
    if (loading) Spinner() else Spinner()
}

@Composable
fun Spinner() {}
"""

SWIFT = """\
import SwiftUI

struct ThemeA {
    @AppStorage("theme") var theme = "light"
}

struct ThemeB {
    @AppStorage("theme") var theme = "dark"
}

struct ThemeScene {
    @SceneStorage("theme") var theme = ""
}

struct Screen: View {
    var body: some View {
        if true { Picker() } else { Picker() }
    }
}

struct Picker: View { var body: some View { EmptyView() } }
"""


def _index(src: str, name: str, lang_ext: str):
    d = Path(tempfile.mkdtemp(prefix="cg-235-"))
    root = d / "app"
    f = root / f"src/{name}.{lang_ext}"
    f.parent.mkdir(parents=True)
    f.write_text(src)
    dbp = d / "g.db"
    index_project(root, dbp, name)
    return sqlite3.connect(dbp)


@pytest.fixture(scope="module")
def kt():
    pytest.importorskip("tree_sitter_kotlin")
    return _index(KT, "Cart", "kt")


@pytest.fixture(scope="module")
def swift():
    pytest.importorskip("tree_sitter_swift")
    return _index(SWIFT, "Theme", "swift")


def test_kotlin_string_template_and_extension_receiver(kt):
    e = {(s, d, k, ln, json.loads(a or "{}").get("receiver")) for s, d, k, ln, a in kt.execute(
        "select src, dst, kind, line, attrs from edges where kind='READS_PROP'")}
    assert ("method:app.Cart.label", "field:app.Cart.items", "READS_PROP", 4, "this") in e
    assert ("function:app.total", "field:app.Cart.items", "READS_PROP", 7, "Cart") in e


def test_kotlin_second_branch_same_line(kt):
    rows = [json.loads(a or "{}").get("branch") for (a,) in kt.execute(
        "select attrs from edges where kind='CALLS' and src='function:app.Home' and dst='function:app.Spinner'")]
    assert set(rows) == {"if (loading)", "else of if (loading)"}, rows


def test_swift_shared_storage_key(swift):
    rows = {(s, d, json.loads(a or "{}").get("wrapper"), json.loads(a or "{}").get("key"))
            for s, d, a in swift.execute("select src, dst, attrs from edges where kind='SHARES_STORAGE'")}
    assert ("field:ThemeA.theme", "field:ThemeB.theme", "AppStorage", "theme") in rows
    assert ("field:ThemeB.theme", "field:ThemeA.theme", "AppStorage", "theme") in rows
    assert not any(x[0].endswith("ThemeScene.theme") or x[1].endswith("ThemeScene.theme") for x in rows)


def test_swift_second_branch_same_line(swift):
    rows = [json.loads(a or "{}").get("branch") for (a,) in swift.execute(
        "select attrs from edges where kind='INSTANTIATES' and dst='class:Picker'")]
    assert set(rows) == {"if true", "else of if true"}, rows

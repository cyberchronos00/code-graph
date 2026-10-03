"""#68: `navigationDestination(for: T.self) { switch ... }` matched to `NavigationLink(value: T.x(...))` and
`router.navigate(to: .x)`; URLComponents endpoints."""
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_swift")
from codegraph.indexer import index_project  # noqa: E402

FIX = Path(__file__).resolve().parent / "swift_nav_values"


def test_value_navigation(tmp_path):
    index_project(FIX, tmp_path / "g.db", "nav")
    c = sqlite3.connect(tmp_path / "g.db")
    nav = {(s, d, ln, how) for s, d, ln, how in c.execute(
        "SELECT src, dst, line, json_extract(attrs, '$.how') FROM edges WHERE kind='NAVIGATES_TO'")}
    body = "method:ListsView.body"
    assert {(d, ln) for s, d, ln, _ in nav if s == body} == {
        ("page:swift:TimelineView", 52),        # NavigationLink(value: RouterDestination.list(...)), shared case
        ("page:swift:AccountDetailView", 53),   # navigate(to: .accountDetail(...)): one destination type has it
        ("page:swift:AboutView", 54),           # .about: OtherRoute's `default:` branch
        ("page:swift:SettingsView", 56),        # OtherRoute.settings; the untyped `.settings` (55) is ambiguous
    }
    router = {d for s, d, _, how in nav if s == "method:View.withAppRouter"}
    assert router == {"page:swift:AccountDetailView", "page:swift:TimelineView", "page:swift:SettingsView"}


def test_urlcomponents_endpoints(tmp_path):
    """`URLComponents(string:)` + `.path`, and `URLComponents()` + `.scheme` / `.host` / `.path` (#68)."""
    index_project(FIX.parent / "swift_components", tmp_path / "g.db", "comp")
    c = sqlite3.connect(tmp_path / "g.db")
    calls = set(c.execute("SELECT src, dst FROM edges WHERE kind='HTTP_CALLS'"))
    assert calls == {("method:UsersAPI.user", "http:GET https://api.example.com/v1/users/{id}"),
                     ("method:UsersAPI.search", "http:POST https://search.example.com/search")}

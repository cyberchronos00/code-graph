"""#177: column writes through mass assignment (`update($request->validated())`, `create($data)`, `fill(...)`, ...), on
tests/mass_assign_fixture and the bundled bookstore sample.

Keys come from the FormRequest `rules()` (top-level) or the inline `validate([...])`, narrowed by `only` / `except`, then by the
model's `$fillable` / `$guarded` (not for `forceFill` / `forceCreate` or query-builder writes). Edges are `WRITES_COLUMN` at
`resolved` (`heuristic` for `all()` / `input()`) with `attrs.via` and `attrs.keys_from`."""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from sample import build, needs_php  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

pytestmark = [needs_php, pytest.mark.skipif(not shutil.which("php"), reason="php not installed")]
FIX = ROOT / "tests" / "mass_assign_fixture"
_S: dict = {}
CTL = "App\\Http\\Controllers\\BookController::"


def db() -> Path:
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-mass-assign-"))
        index_project(FIX, d / "g.db", "mass-assign")
        _S["db"] = d / "g.db"
    return _S["db"]


def writes(fn: str, db_path=None, owner=CTL) -> dict:
    """{column: (confidence, attrs)} of the mass-assignment WRITES_COLUMN edges of one function."""
    q = "select dst, confidence, attrs from edges where kind='WRITES_COLUMN' and src=? and attrs like '%mass_assignment%'"
    return {d[len("column:"):]: (c, json.loads(a)) for d, c, a in sqlite3.connect(db_path or db()).execute(q, (f"method:{owner}{fn}",))}


def cols(fn: str, **kw) -> set:
    return set(writes(fn, **kw))


def test_validated_intersects_fillable_and_keeps_provenance():
    w = writes("updateValidated")
    assert set(w) == {"books.title", "books.price", "books.age_rating"}
    conf, at = w["books.age_rating"]
    assert conf == "resolved"
    assert at["via"] == "update(validated())"
    assert at["keys_from"] == ["UpdateBookRequest::rules", "Book::$fillable"]


def test_validated_but_not_fillable_gives_no_edge():
    # `reviewer_note` is a rule key and a real column, but not in Book::$fillable
    assert "books.reviewer_note" not in cols("updateValidated")
    assert "books.reviewer_note" not in cols("createStatic")
    # a nested rule key (`tags.*.name`) is the top-level `tags`, which is not a column: no edge
    assert not any(c.endswith(".tags") for c in cols("updateValidated"))


def test_local_variable_and_argument_flow_through_a_service():
    assert cols("updateViaLocal") == cols("updateValidated")
    saved = writes("save", owner="App\\Services\\BookSaver::")
    assert set(saved) == {"books.title", "books.price", "books.age_rating"}
    assert saved["books.title"][1]["via"] == "update(validated())" and saved["books.title"][1]["param"] == "$data"


def test_only_and_except_and_request_only():
    assert cols("updateOnly") == {"books.price"}                       # safe()->only(['price', 'reviewer_note']) ∩ rules ∩ fillable
    assert cols("updateExcept") == {"books.title", "books.age_rating"}  # safe()->except(['price'])
    assert cols("updateRequestOnly") == {"books.title"}                # literal keys of `$request->only(...)` ∩ fillable
    assert writes("updateRequestOnly")["books.title"][1]["via"] == "update(only())"


def test_array_merge_and_spread_take_both_key_sets():
    for fn in ("updateMerged",):
        w = writes(fn)
        assert {"books.title", "books.price", "books.age_rating", "books.is_active"} <= set(w)
        assert "books.reviewer_note" not in w
    assert writes("updateMerged")["books.title"][1]["via"] == "update(array_merge(validated()))"
    spread = sqlite3.connect(db()).execute("select dst from edges where kind='WRITES_COLUMN' and src=?",
                                           (f"method:{CTL}updateSpread",)).fetchall()
    assert {d[len("column:"):] for (d,) in spread} == {"books.title", "books.price", "books.age_rating", "books.is_active"}


def test_static_create_and_update_or_create():
    assert cols("createStatic") == {"books.title", "books.price", "books.age_rating"}
    assert cols("createOrUpdate") == {"books.title", "books.price", "books.age_rating"}   # the values array (second argument)


def test_force_fill_and_force_create_ignore_fillable():
    for fn in ("fillForced", "createForced"):
        assert cols(fn) == {"books.title", "books.price", "books.age_rating", "books.reviewer_note"}
    assert writes("createForced")["books.reviewer_note"][1]["keys_from"] == ["UpdateBookRequest::rules"]


def test_all_without_rules_is_heuristic_fillable_keys():
    w = writes("updateAll")
    assert set(w) == {"books.title", "books.price", "books.age_rating", "books.is_active"}
    assert {c for c, _ in w.values()} == {"heuristic"}
    assert w["books.title"][1]["keys_from"] == ["Book::$fillable"] and w["books.title"][1]["via"] == "update(all())"


def test_inline_validate_keys():
    # `$request->validate([...])` keys ∩ fillable: `internal_code` is validated but not fillable
    assert cols("updateInline") == {"books.title", "books.price"}
    assert writes("updateInline")["books.title"][1]["keys_from"] == ["BookController::updateInline validate()", "Book::$fillable"]


def test_relation_create_uses_the_related_model():
    w = writes("addReview")
    assert set(w) == {"reviews.body", "reviews.stars"}                  # moderator_note is validated but not fillable
    assert w["reviews.body"][1]["keys_from"] == ["ReviewRequest::rules", "Review::$fillable"]


def test_guarded_keys_are_dropped_and_empty_guarded_keeps_everything():
    assert cols("createAuthor") == {"authors.name", "authors.bio"}      # `$guarded = ['role']`
    assert "Author::$guarded" in writes("createAuthor")["authors.name"][1]["keys_from"]
    assert cols("createShelf") == {"shelves.label", "shelves.location"}  # `$guarded = []`


def test_new_model_with_save():
    assert cols("newThenSave") == {"orders.status", "orders.qty"}       # `secret` is not a column / not fillable
    assert writes("newThenSave")["orders.qty"][1]["via"] == "new Order(validate())"
    assert cols("newWithoutSave") == set()                              # never saved: no column write


def test_query_builder_updates_do_not_consult_fillable():
    for fn in ("builderUpdate", "tableUpdate"):
        assert cols(fn) == {"books.title", "books.price", "books.age_rating", "books.reviewer_note"}


def test_keys_built_at_run_time_stay_out():
    assert cols("computedKeys") == set()


def test_the_table_write_is_still_recorded():
    q = "select count(*) from edges where kind='WRITES_TABLE' and src=? and dst='table:books'"
    assert sqlite3.connect(db()).execute(q, (f"method:{CTL}updateValidated",)).fetchone()[0] == 1


def test_bookstore_sample_update_writes_age_rating_but_not_reviewer_note():
    from cg_code_graph import query as Q
    from cg_code_graph.core.store import GraphStore
    api = build()["api"]
    w = writes("update", api, "App\\Http\\Controllers\\Admin\\BookController::")
    assert "books.age_rating" in w and w["books.age_rating"][1]["keys_from"] == ["UpdateBookRequest::rules", "Book::$fillable"]
    assert "books.reviewer_note" not in w
    st = GraphStore(api)
    assert any(r["fqn"].endswith("BookController::update") for r in Q.writers(st, "books.age_rating"))


def test_node_and_routes_show_the_mass_assignment_write():
    import subprocess
    api = str(build()["api"])
    out = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "node", "App\\Http\\Controllers\\Admin\\BookController::update",
                          "--db", api], capture_output=True, text=True, check=True).stdout
    assert "WRITES_COLUMN column:books.age_rating" in out
    assert "via update(validated()) keys from UpdateBookRequest::rules ∩ Book::$fillable" in out
    out = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "routes", "--reaches", "books.age_rating", "--db", api],
                         capture_output=True, text=True, check=True).stdout
    assert "PUT /v1/admin/books/{id}" in out and "WRITES_COLUMN@BookController.php:30" in out


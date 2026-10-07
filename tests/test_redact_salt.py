"""Salted secret fingerprints (#47 part 2d): `redacted:hmac:<8 hex>` is an HMAC-SHA-256 keyed with a per-graph salt kept
in the `meta` table (`redact_salt`), so a common password is not recognisable from its marker and markers only compare
within one graph (and across re-indexes of it)."""
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core import redact as R  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

PHP_FIX = ROOT / "tests" / "surface_fixture" / "php"
needs_php = pytest.mark.skipif(shutil.which("php") is None, reason="php is not installed (PHP extractor)")


def test_marker_is_keyed_not_a_plain_digest():
    plain = hashlib.sha256(b"password").hexdigest()[:8]
    assert plain == "5e884898"
    m = R.marker("password", "salt-a-0000")
    assert m.startswith(R.MARK) and m != R.MARK + plain and m != R.LEGACY_MARK + plain
    assert plain not in m


def test_two_graphs_give_different_markers_and_one_graph_a_stable_one():
    assert R.marker("hunter2", "salt-a") != R.marker("hunter2", "salt-b")
    assert R.marker("hunter2", "salt-a") == R.marker("hunter2", "salt-a")
    assert R.marker("hunter2", "salt-a") != R.marker("hunter3", "salt-a")
    with pytest.raises(ValueError):
        R.marker("hunter2", "")


def test_redact_and_urls_use_the_salt_and_both_prefixes_count_as_redacted():
    assert R.redact("fake-bookstore-pw-0000", "db_password", "s1") == R.marker("fake-bookstore-pw-0000", "s1")
    url = R.redact("mysql://reader:fake-bookstore-pw-0000@replica.example.test/shop", None, "s1")
    assert url == f"mysql://reader:{R.marker('fake-bookstore-pw-0000', 's1')}@replica.example.test/shop"
    assert R.is_redacted(R.marker("x", "s1")) and R.is_redacted("redacted:sha256:5e884898")
    assert R.redact(R.marker("x", "s1"), "password", "s2") == R.marker("x", "s1")     # a marker is never re-hashed


def _config(db):
    con = sqlite3.connect(db)
    try:
        salt = json.loads(con.execute("SELECT value FROM meta WHERE key='redact_salt'").fetchone()[0])
        attrs = {r[0]: json.loads(r[1] or "{}") for r in con.execute("SELECT id, attrs FROM nodes WHERE kind='config'")}
    finally:
        con.close()
    return salt, attrs["config:services.github.secret"]["value"]


@needs_php
def test_salt_is_created_per_graph_and_kept_on_reindex(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    index_project(PHP_FIX, a, "php")
    index_project(PHP_FIX, b, "php")
    salt_a, marker_a = _config(a)
    salt_b, marker_b = _config(b)
    assert len(salt_a) == 32 and salt_a != salt_b
    assert marker_a.startswith("redacted:hmac:") and marker_a != marker_b                    # two graphs, same value
    index_project(PHP_FIX, a, "php")                                                           # re-index the same DB
    assert _config(a) == (salt_a, marker_a)


@needs_php
def test_refresh_keeps_the_salt(tmp_path):
    from cg_code_graph import hooks
    root = tmp_path / "php"
    shutil.copytree(PHP_FIX, root, ignore=shutil.ignore_patterns("vendor"))
    db = tmp_path / "graph.db"
    index_project(root, db, "php")
    before = _config(db)
    (root / "app" / "Services" / "Extra.php").write_text("<?php\nnamespace App\\Services;\nclass Extra {}\n")
    assert hooks.refresh(str(root), str(db), "php", quiet=True) == 0
    assert _config(db) == before


@needs_php
def test_cg_link_keeps_markers_and_has_no_salt_of_its_own(tmp_path):
    from cg_code_graph.link import link_many
    a, b, out = tmp_path / "a.db", tmp_path / "b.db", tmp_path / "combined.db"
    index_project(PHP_FIX, a, "php-a")
    index_project(PHP_FIX, b, "php-b")
    link_many([("one", str(a), "both"), ("two", str(b), "both")], str(out))
    con = sqlite3.connect(out)
    try:
        assert con.execute("SELECT 1 FROM meta WHERE key='redact_salt'").fetchone() is None
        vals = {r[0]: json.loads(r[1] or "{}").get("value") for r in con.execute(
            "SELECT id, attrs FROM nodes WHERE id LIKE '%config:services.github.secret'")}
    finally:
        con.close()
    assert len(vals) == 2 and all(str(v).startswith("redacted:hmac:") for v in vals.values())
    assert len(set(vals.values())) == 2          # fingerprints of different source graphs do not compare

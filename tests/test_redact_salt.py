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


LARAVEL_FIX = ROOT / "tests" / "redact_fixture" / "laravel-bookstore"


@pytest.mark.parametrize("key", ["AUTH_GUARD", "AUTH_PASSWORD_BROKER", "AUTH_PASSWORD_RESET_TOKEN_TABLE",
                                 "try_it_credentials_policy", "auth.defaults.guard", "auth.passwords.readers.table",
                                 "DB_PASSWORD_TABLE", "S3_PUBLIC_KEY", "SIGNING_KEY_ID", "SESSION_DRIVER", "tokenTtl",
                                 "REPORTS_KEY_PATH", "primary_key", "foreign_key", "sort_key", "cache.key", "route_key",
                                 "partition_key", "idempotency_key", "remember_token"])
def test_last_word_rule_leaves_framework_settings_alone(key):
    assert not R.looks_secret(key, "created_at") and not R.secret_key(key, "created_at")


@pytest.mark.parametrize("key", ["AUTH_TOKEN", "DB_PASSWORD", "STRIPE_SECRET", "password", "client_secret", "api_key",
                                 "apiKey", "REPORTS_DSN", "private_key", "signing_key", "ENCRYPTION_KEY",
                                 "webhook_secret", "access_token", "refresh_token", "bearer_token", "DB_PASS",
                                 "app.key", "APP_KEY", "STRIPE_SECRET_KEY"])
def test_last_word_rule_keeps_credentials(key):
    assert R.looks_secret(key) and R.secret_key(key)


_HEX32 = "a" + ("fake" + "0a1b" * 7)[:31]                 # 32 chars, lowercase hex-looking, starts with a letter
_REDACTED_ROWS = [
    ("LEDGER_TOKEN", "fake-bookstore-token-0000"),
    ("LEDGER_TOKEN", "SURF-PHP-LEDGER-DEFAULT-d7e2"),
    ("services.ledger.token", _HEX32),
    ("MAILGUN_KEY", "key-" + "fake0a1b" * 3 + "fake0a"),
    ("PUSHER_APP_KEY", "fakebookstore" + "abcdefghijk"[:9] + "0042"),
    ("services.x.key", "FakeBook" + "Store12345" + "AbC"),
]


@pytest.mark.parametrize("key,value", _REDACTED_ROWS)
def test_bare_key_and_token_with_credential_looking_values_are_redacted(key, value):
    assert R.secret_key(key, value) and R.looks_secret(key, value)
    assert R.redact(value, key, "s1").startswith(R.MARK)


_HOSTILE_ROWS = [
    "key-abcdef", "key-fakebookstorekeynodigits", "key-FAKE",            # key- prefixed, with and without digits
    "abc.def.ghi", "fake.bookstore.token",                                # dotted, JWT-segment-like
    "eyJmYWtlIjoiYm9va3N0b3JlIn0", "FakeBookstoreTokenNoDigits",          # base64url segment, mixed case no digits
    "fake_bookstore_token", "xoxb-fake-bookstore-token", "fakebookstore",  # lowercase words only
]


@pytest.mark.parametrize("key", ["services.ledger.token", "services.ledger.key", "LEDGER_TOKEN", "MAILGUN_KEY"])
@pytest.mark.parametrize("value", _HOSTILE_ROWS)
def test_bare_key_and_token_redact_any_value_shape(key, value):
    assert R.secret_key(key, value)
    assert R.redact(value, key, "s1").startswith(R.MARK)


@pytest.mark.parametrize("key", ["services.ledger.token", "services.ledger.key", "LEDGER_TOKEN", "STRIPE_KEY"])
def test_bare_key_and_token_are_redacted_whatever_the_value(key):
    assert R.secret_key(key) and R.secret_key(key, None) and R.secret_key(key, 12345)      # no value: redact
    assert R.secret_key(key, "sk_test_FAKE_bookstore_000000")
    assert R.secret_key(key, "sk_fakebookstore") and R.secret_key(key, "pk_live_fakebookstore")
    assert R.secret_key(key, "rk-fakebookstore")
    assert R.secret_key(key, "base64:FAKEBOOKSTOREAPPKEY0000000000000000000000=")
    assert R.secret_key(key, "Remember_Token") and R.secret_key(key, "token1")
    assert R.secret_key(key, "a" * 41) and R.secret_key(key, R.marker("x", "s1"))
    assert R.secret_key(key, "a" * 40)
    assert R.secret_key(key, "created_at") and R.secret_key(key, "x-idempotency-key")


@pytest.mark.parametrize("key,value", [("primary_key", "id"), ("foreign_key", "author_id"), ("sort_key", "created_at"),
                                       ("remember_token", "remember_token"), ("cache.key", "bookstore_cache"),
                                       ("idempotency_key", "x-idempotency-key"), ("AUTH_GUARD", "web"),
                                       ("AUTH_PASSWORD_BROKER", "readers"),
                                       ("AUTH_PASSWORD_RESET_TOKEN_TABLE", "reader_password_resets"),
                                       ("try_it_credentials_policy", "include")])
def test_kept_cases(key, value):
    assert not R.secret_key(key, value)
    assert R.redact(value, key, "s1") == value


@pytest.mark.parametrize("word", ["primary", "foreign", "sort", "partition", "route", "cache", "idempotency",
                                  "remember", "csrf", "xsrf", "public", "i18n", "translation", "lookup", "unique",
                                  "index", "storage", "prefix"])
@pytest.mark.parametrize("last", ["key", "token"])
def test_known_non_secret_compounds_keep_any_value(word, last):
    assert not R.secret_key(f"{word}_{last}", "Fake0a1b" * 4)
    assert not R.secret_key(f"{word.upper()}_{last.upper()}", "Fake0a1b" * 4)
    assert not R.secret_key(f"app.{word}.{last}", "Fake0a1b" * 4)


@pytest.mark.parametrize("key", ["S3_KEY_ID", "S3_PUBLIC_KEY", "SIGNING_KEY_PATH", "SIGNING_KEY_FILE"])
def test_key_id_public_key_and_path_are_never_credentials(key):
    assert not R.secret_key(key, "Fake0a1b" * 4)


def test_redact_keeps_a_guard_name_and_hides_a_password():
    assert R.redact("web", "guard", "s1") == "web"
    assert R.redact("readers", "AUTH_PASSWORD_BROKER", "s1") == "readers"
    assert R.redact("fake-bookstore-pw-0000", "DB_PASSWORD", "s1").startswith(R.MARK)


@needs_php
def test_laravel_bookstore_config_keeps_defaults_and_redacts_secrets(tmp_path):
    db = tmp_path / "laravel.db"
    index_project(LARAVEL_FIX, db, "laravel-bookstore")
    con = sqlite3.connect(db)
    try:
        attrs = {r[0]: json.loads(r[1] or "{}") for r in con.execute("SELECT id, attrs FROM nodes WHERE kind='config'")}
    finally:
        con.close()
    assert attrs["config:auth.defaults.guard"]["env_default"] == ["web"]
    assert attrs["config:auth.defaults.passwords"]["env_default"] == ["readers"]
    assert attrs["config:auth.passwords.readers.table"]["env_default"] == ["reader_password_resets"]
    assert attrs["config:shelfdocs.try_it_credentials_policy"]["value"] == "include"
    assert attrs["config:models.primary_key"]["value"] == "id"
    assert attrs["config:models.foreign_key"]["value"] == "author_id"
    assert attrs["config:models.sort_key"]["value"] == "created_at"
    assert attrs["config:models.remember_token"]["value"] == "remember_token"
    assert attrs["config:models.cache.key"]["value"] == "bookstore_cache"
    assert attrs["config:services.ledger.idempotency_key"]["value"] == "x-idempotency-key"
    redacted = ["config:database.connections.pgsql.password", "config:services.ledger.token", "config:app.key",
                "config:services.stripe.secret"]
    for nid in redacted:
        assert attrs[nid]["env_default"][0].startswith(R.MARK), nid
    assert attrs["config:services.ledger.key"]["value"].startswith(R.MARK)
    assert attrs["config:shelfdocs.client_secret"]["value"].startswith(R.MARK)
    assert "fake-bookstore" not in json.dumps(attrs) and "FAKE" not in json.dumps(attrs)


@needs_php
def test_surface_php_fixture_still_redacts_exactly_three_values(tmp_path):
    db = tmp_path / "php.db"
    index_project(PHP_FIX, db, "php")
    con = sqlite3.connect(db)
    try:
        attrs = [json.loads(r[0] or "{}") for r in con.execute("SELECT attrs FROM nodes WHERE kind='config'")]
    finally:
        con.close()
    n = sum(1 for a in attrs for v in [a.get("value"), *(a.get("env_default") or [])] if R.is_redacted(v))
    assert n == 3

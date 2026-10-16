"""Newer-release notice (#98): hermetic, PyPI is never contacted (fetch_latest is replaced)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import update_check as U  # noqa: E402


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    for k in ("CG_NO_UPDATE_CHECK", "CI"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(U, "_under_tests", lambda: False)   # pytest re-sets PYTEST_CURRENT_TEST for each phase
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))
    calls = []

    def fake(timeout=U.TIMEOUT):
        calls.append(timeout)
        return fake.latest
    fake.latest = "0.30.0"
    monkeypatch.setattr(U, "fetch_latest", fake)
    return fake, calls, tmp_path / "cache" / "update-check.json"


def test_versions():
    assert U.is_newer("0.24.0", "0.23.2") and U.is_newer("1.0", "0.99.9") and U.is_newer("0.23.10", "0.23.9")
    assert not U.is_newer("0.23.2", "0.23.2") and not U.is_newer("0.23.1", "0.23.2")
    assert not U.is_newer("0.24.0rc1", "0.23.2") and not U.is_newer(None, "0.23.2") and not U.is_newer("0.24.0", "0.24.0.dev1")


def test_check_fetches_once_then_caches(enabled):
    fake, calls, path = enabled
    r = U.check("0.23.2", now=1000.0)
    assert r["source"] == "pypi" and r["latest"] == "0.30.0" and r["newer"] and len(calls) == 1
    assert json.loads(path.read_text()) == {"latest": "0.30.0", "checked_at": 1000.0}
    assert U.check("0.23.2", now=1000.0 + U.MAX_AGE - 1)["source"] == "cache" and len(calls) == 1
    fake.latest = "0.31.0"
    assert U.check("0.23.2", now=1000.0 + U.MAX_AGE + 1)["latest"] == "0.31.0" and len(calls) == 2
    assert "cg 0.31.0 is available (this is 0.23.2)" in U.notice(U.check("0.23.2", network=False))


def test_offline_is_silent(enabled):
    fake, calls, path = enabled
    fake.latest = None    # offline / timeout / bad JSON
    r = U.check("0.23.2", now=5.0)
    assert r["latest"] is None and not r["newer"] and U.notice(r) is None and not path.exists()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert U.check("0.23.2", now=5.0)["newer"] is False


def test_cache_only_never_fetches(enabled):
    fake, calls, path = enabled
    assert U.check("0.23.2", network=False)["source"] == "none" and calls == []


@pytest.mark.parametrize("var,val", [("CG_NO_UPDATE_CHECK", "1"), ("CI", "true"), ("PYTEST_CURRENT_TEST", "x")])
def test_disabled(enabled, monkeypatch, var, val):
    fake, calls, path = enabled
    monkeypatch.setenv(var, val)
    if var == "PYTEST_CURRENT_TEST":
        monkeypatch.setattr(U, "_under_tests", lambda: True)
    r = U.check("0.23.2")
    assert r["disabled"] and r["latest"] is None and calls == [] and not path.exists()


def test_fetch_latest_failure_returns_none(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert U.fetch_latest(0.01) is None


def test_version_and_doctor_cli_show_cached_notice(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "update-check.json").write_text(json.dumps({"latest": "99.0.0", "checked_at": 4e9}))
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("CG_NO_UPDATE_CHECK", "CI", "PYTEST_CURRENT_TEST")}
    env["CG_CACHE"] = str(cache)
    out = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "--version"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert out.returncode == 0 and out.stdout.splitlines()[1].startswith("cg 99.0.0 is available")
    # the cache is fresh (checked_at in the future counts as stale, so use now): doctor reads it without a fetch
    import time
    (cache / "update-check.json").write_text(json.dumps({"latest": "99.0.0", "checked_at": time.time()}))
    doc = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "doctor"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert "newer release: cg 99.0.0 is available" in doc.stdout
    env["CG_NO_UPDATE_CHECK"] = "1"
    out = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "--version"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert out.stdout.strip().splitlines() == [out.stdout.strip().splitlines()[0]]

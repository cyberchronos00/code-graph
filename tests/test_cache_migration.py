"""Moving ~/.cache/codegraph to ~/.cache/cg (only when no cache override is set)."""
import os
from pathlib import Path

import pytest

from cg_code_graph.core import cache


def _reset(monkeypatch):
    cache._migrated = False
    cache._legacy_failure = None
    for key in ("CG_CACHE", "CODEGRAPH_CACHE", "CODEGRAPH_CACHE_DIR", "XDG_CACHE_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(key, raising=False)


def _seed(old: Path):
    (old / "ts").mkdir(parents=True)
    (old / "ts" / "x.json").write_text("{}", encoding="utf-8")
    (old / "extractors" / "ts-abc").mkdir(parents=True)
    (old / "extractors" / "ts-abc" / "m").write_text("m", encoding="utf-8")
    (old / "swift-build" / "k").mkdir(parents=True)
    (old / "swift-build" / "k" / "f").write_text("f", encoding="utf-8")


def test_old_cache_is_moved_and_swift_build_dropped(tmp_path, monkeypatch, capsys):
    _reset(monkeypatch)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    old = tmp_path / "codegraph"
    _seed(old)
    assert cache.root() == tmp_path / "cg"
    new = tmp_path / "cg"
    assert (new / "ts" / "x.json").is_file()
    assert (new / "extractors" / "ts-abc" / "m").is_file()
    assert not (new / "swift-build").exists()
    assert not old.exists()
    err = capsys.readouterr().err
    assert err.count("moved the cache") == 1
    assert f"{old} -> {new}" in err
    assert cache.root() == new
    assert capsys.readouterr().err == ""


def test_both_caches_keep_the_new_one(tmp_path, monkeypatch, capsys):
    _reset(monkeypatch)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    old, new = tmp_path / "codegraph", tmp_path / "cg"
    old.mkdir()
    (old / "old.txt").write_text("o", encoding="utf-8")
    new.mkdir()
    (new / "new.txt").write_text("n", encoding="utf-8")
    assert cache.root() == new
    assert (old / "old.txt").is_file() and (new / "new.txt").is_file()
    assert "moved the cache" not in capsys.readouterr().err
    assert cache.legacy_root() == old
    from cg_code_graph import doctor
    text = doctor.render(doctor.report())
    assert f"note: the old cache {old} still exists" in text
    assert "delete it when you no longer need it" in text


def test_rename_failure_leaves_a_fresh_dir_and_a_doctor_note(tmp_path, monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    old = tmp_path / "codegraph"
    _seed(old)

    def boom(*_a, **_k):
        raise OSError("busy")

    monkeypatch.setattr(os, "rename", boom)
    assert cache.root() == tmp_path / "cg"
    assert (tmp_path / "cg").is_dir()
    assert old.is_dir()
    assert cache.legacy_note() and "busy" in cache.legacy_note()
    from cg_code_graph import doctor
    text = doctor.render(doctor.report())
    assert "note:" in text and "busy" in text
    assert f"the old cache {old} still exists" in text


def test_env_override_skips_migration(tmp_path, monkeypatch, capsys):
    _reset(monkeypatch)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    old = tmp_path / "codegraph"
    _seed(old)
    custom = tmp_path / "custom"
    monkeypatch.setenv("CG_CACHE", str(custom))
    assert cache.root() == custom
    assert old.is_dir() and (old / "swift-build" / "k" / "f").is_file()
    assert not custom.exists()
    assert "moved the cache" not in capsys.readouterr().err


def test_unreadable_legacy_is_safe_and_noted(tmp_path, monkeypatch, capsys):
    """An unreadable old directory does not raise, does not clobber, and leaves a doctor note."""
    _reset(monkeypatch)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    old = tmp_path / "codegraph"
    _seed(old)
    real_is_dir = cache._Path.is_dir
    real_exists = cache._Path.exists

    def is_dir(self):
        if self.name == "codegraph":
            raise OSError("unreadable")
        return real_is_dir(self)

    def exists(self):
        if self.name == "codegraph":
            raise OSError("unreadable")
        return real_exists(self)

    monkeypatch.setattr(cache._Path, "is_dir", is_dir)
    monkeypatch.setattr(cache._Path, "exists", exists)
    assert cache.root() == tmp_path / "cg"
    assert (tmp_path / "cg").is_dir()
    assert (old / "ts" / "x.json").is_file()
    note = cache.legacy_note()
    assert note and "unreadable" in note
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "moved the cache" not in captured.err
    from cg_code_graph import doctor
    text = doctor.render(doctor.report())
    assert "note:" in text and "unreadable" in text


def test_rename_race_does_not_clobber(tmp_path, monkeypatch, capsys):
    """A concurrent first run that creates `cg` wins; the loser keeps that directory."""
    _reset(monkeypatch)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    old = tmp_path / "codegraph"
    _seed(old)
    new = tmp_path / "cg"

    def race(src, dst):
        new.mkdir()
        (new / "kept.txt").write_text("kept", encoding="utf-8")
        raise OSError("race")

    monkeypatch.setattr(os, "rename", race)
    assert cache.root() == new
    assert (new / "kept.txt").read_text(encoding="utf-8") == "kept"
    assert not (new / "ts").exists()
    assert old.is_dir()
    assert cache.legacy_note() is None
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "moved the cache" not in captured.err


def test_windows_localappdata_form(tmp_path, monkeypatch, capsys):
    _reset(monkeypatch)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    old = tmp_path / "local" / "codegraph"
    _seed(old)
    assert cache.root() == tmp_path / "local" / "cg"
    assert (tmp_path / "local" / "cg" / "ts" / "x.json").is_file()
    assert not (tmp_path / "local" / "cg" / "swift-build").exists()
    assert not old.exists()
    assert "moved the cache" in capsys.readouterr().err


def test_dot_codegraph_is_not_source(tmp_path, monkeypatch):
    """A `.codegraph/` directory is cache data, not a TypeScript project."""
    _reset(monkeypatch)
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "proj"
    hidden = root / ".codegraph"
    hidden.mkdir(parents=True)
    (hidden / "index.db").write_bytes(b"")
    (hidden / "cache.json").write_text("{}", encoding="utf-8")
    (hidden / "x.ts").write_text("export const n = 1\n", encoding="utf-8")
    (root / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")
    from cg_code_graph.coverage import scan, scan_tree
    counts = scan(root)
    assert counts.get(".ts", 0) == 0
    assert counts.get(".py") == 1
    assert scan_tree(root).paths.get(".py") == ["app.py"]
    assert ".ts" not in scan_tree(root).paths
    from cg_code_graph.indexer import index_project
    st = index_project(root, tmp_path / "graph.db", "proj")
    langs = {e["language"]: e for e in st["coverage"]["languages"]}
    assert set(langs) == {"python"}
    assert langs["python"]["files"] == 1
    from cg_code_graph.coverage import render_summary
    text = render_summary({"": st["coverage"]})
    assert "python" in text and "typescript" not in text and "x.ts" not in text and "app.py" not in text.split("python", 1)[0]
    # the indexed Python file is app.py; the hidden .ts file is not a source file of this index
    paths = []
    for e in st["coverage"]["languages"]:
        for group in (e.get("paths") or {}).values():
            paths.extend(group)
    assert paths == [] or paths == ["app.py"]

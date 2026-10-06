"""`cg clean` and the shared cache root (#80): one root lookup for every cache user, per-project removal by the
project key in the entry names (and every project indexed below a directory), --stale for entries no cg can read
again, --all keeping the extractors, --dry-run, --db with its -wal / -shm, and the refusals that keep a misconfigured
root from deleting a home directory."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.core import cache, extractors, fsutil  # noqa: E402
from cg_code_graph.plugins.native import runner  # noqa: E402

V = fsutil.CACHE_VERSION


@pytest.fixture
def croot(tmp_path, monkeypatch):
    for k in ("CG_CACHE", "CODEGRAPH_CACHE", "CODEGRAPH_CACHE_DIR", "XDG_CACHE_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(k, raising=False)
    r = tmp_path / "cache"
    monkeypatch.setenv("CG_CACHE", str(r))
    return r


def touch(p: Path, data=b"x" * 10, age=0.0):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if age:
        t = time.time() - age
        os.utime(p, (t, t))
    return p


def populate(r: Path, proj: Path) -> dict:
    """One project's entries in every kind, plus another project's and stale ones."""
    k = cache.root_key(proj)
    cache.note_project(proj)
    sk = cache.swift_build_key(proj)
    mine = {
        "scip": touch(r / "scip" / f"rust-v{V}-{k}-{'a' * 20}.scip"),
        "lock": touch(r / "scip" / f"rust-v{V}-{k}-{'a' * 20}.lock", b""),
        "ra": touch(r / "scip" / f"ra-config-{k}-{'b' * 12}.json"),
        "swift": touch(r / "swift-build" / sk / "cg-stamp.json",
                       json.dumps({"key": "k", "cache_version": V}).encode()).parent,
        "swift_lock": touch(r / "scip" / f"swift-v{V}-{sk}.lock", b""),
        "ts": touch(r / "ts" / f"{k}-v{V}-{'c' * 20}.json"),
        "dart": touch(r / "dart" / f"{k}-v{V}-{'d' * 20}.json"),
        "project": r / "projects" / k,
    }
    other = "e" * 12
    keep = {
        "other_scip": touch(r / "scip" / f"rust-v{V}-{other}-{'a' * 20}.scip"),
        "other_ts": touch(r / "ts" / f"{other}-v{V}-{'c' * 20}.json"),
        "extractor": touch(r / "extractors" / "typescript-0123456789ab" / "extract.mjs"),
    }
    stale = {
        "old_version": touch(r / "scip" / f"cfamily-v{V - 1}-{other}-{'f' * 20}.scip"),
        "old_layout": touch(r / "scip" / f"cfamily-v{V}-{'f' * 20}.scip"),
        "legacy": touch(r / "scip" / f"cfamily-{'f' * 20}.scip"),
        "old_ra": touch(r / "scip" / f"ra-config-{'b' * 12}.json"),
        "old_ts": touch(r / "ts" / f"{other}-{'c' * 20}.json"),
        "old_tmp": touch(r / "scip" / f"rust-v{V}-{other}-x.123.abcd.tmp.scip", age=3 * 86400),
        "orphan_lock": touch(r / "scip" / f"kotlin-v{V}-{other}-{'9' * 20}.lock", b""),
        "old_swift": touch(r / "swift-build" / ("0" * 16) / "cg-stamp.json",
                           json.dumps({"key": "k", "cache_version": V - 1}).encode()).parent,
    }
    fresh_tmp = touch(r / "scip" / f"rust-v{V}-{other}-y.1.abcd.tmp.scip")
    keep["fresh_tmp"] = fresh_tmp
    return {"mine": mine, "keep": keep, "stale": stale}


def run(*args, env=None):
    return subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "clean", *map(str, args)], cwd=ROOT, env=env,
                          capture_output=True, text=True)


def test_one_cache_root_for_every_user(tmp_path, monkeypatch):
    for k in ("CG_CACHE", "CODEGRAPH_CACHE", "CODEGRAPH_CACHE_DIR", "XDG_CACHE_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(k, raising=False)
    cache._migrated = False
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    want = tmp_path / "xdg" / "cg"
    assert cache.root() == want
    assert runner.cache_dir() == want / "scip"
    assert extractors.cache_root() == want / "extractors"
    monkeypatch.setenv("CODEGRAPH_CACHE_DIR", str(tmp_path / "old-name"))
    assert cache.root() == tmp_path / "old-name" and runner.cache_dir().parent == tmp_path / "old-name"
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "c"))
    assert cache.root() == tmp_path / "c" == extractors.cache_root().parent
    # the TS / Dart / Swift / rust cache users take their directory from the same helper
    for f in ("plugins/ts/plugin.py", "plugins/dart/plugin.py", "plugins/swift/exact.py", "plugins/rust/plugin.py"):
        src = (ROOT / "cg_code_graph" / f).read_text()
        assert 'environ.get("CG_CACHE' not in src and '".cache"' not in src, f


def test_scip_entries_carry_the_project_key(croot, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cmd = [sys.executable, "-c", "import sys; open(sys.argv[-1], 'wb').write(b'scip')"]
    p, info = runner.run_cached("fake", "1" * 20, cmd, proj, "--out", 60)
    assert info["cache"] == "miss" and p.name == f"fake-v{V}-{cache.root_key(proj)}-{'1' * 20}.scip"
    assert (croot / "projects" / cache.root_key(proj)).read_text() == str(proj.resolve())
    pl = cache.plan(proj)
    assert p in [e.path for e in pl.remove]


def test_clean_project_removes_only_its_entries(croot, tmp_path):
    proj = tmp_path / "work" / "app"
    proj.mkdir(parents=True)
    ent = populate(croot, proj)
    pl = cache.plan(proj)
    got = {e.path for e in pl.remove}
    assert got == set(ent["mine"].values())
    assert pl.projects == [str(proj.resolve())]
    assert cache.execute(pl) == []
    assert not any(p.exists() for p in ent["mine"].values())
    assert all(p.exists() for p in [*ent["keep"].values(), *ent["stale"].values()])


def test_clean_directory_covers_projects_indexed_below_it(croot, tmp_path):
    a, b, c = tmp_path / "mono" / "api", tmp_path / "mono" / "web", tmp_path / "elsewhere"
    for p in (a, b, c):
        p.mkdir(parents=True)
    ea, eb, ec = populate(croot, a), populate(croot, b), populate(croot, c)
    pl = cache.plan(tmp_path / "mono")
    assert sorted(pl.projects) == sorted([str(a.resolve()), str(b.resolve())])
    cache.execute(pl)
    assert not any(p.exists() for p in [*ea["mine"].values(), *eb["mine"].values()])
    assert all(p.exists() for p in ec["mine"].values())


def test_stale_removes_unreadable_entries_only(croot, tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    ent = populate(croot, proj)
    pl = cache.plan(stale=True)
    got = {e.path for e in pl.remove}
    assert got == set(ent["stale"].values()), sorted(map(str, got ^ set(ent["stale"].values())))
    assert all(e.stale for e in pl.remove)
    cache.execute(pl)
    assert all(p.exists() for p in [*ent["mine"].values(), *ent["keep"].values()])


@pytest.mark.skipif(not runner.fcntl, reason="no file locks")
def test_held_lock_is_skipped(croot, tmp_path):
    lock = touch(croot / "scip" / f"kotlin-v{V - 1}-{'e' * 12}-{'9' * 20}.lock", b"")
    with runner.key_lock("kotlin", f"{'e' * 12}-{'9' * 20}", 5):
        pass
    fd = os.open(lock, os.O_RDWR)
    runner.fcntl.flock(fd, runner.fcntl.LOCK_EX)
    try:
        pl = cache.plan(stale=True)
        assert lock not in [e.path for e in pl.remove] and lock in [p for p, _ in pl.skipped]
    finally:
        os.close(fd)
    assert lock in [e.path for e in cache.plan(stale=True).remove]


def test_all_keeps_extractors_unless_asked(croot, tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    ent = populate(croot, proj)
    cache.execute(cache.plan(all_=True))
    assert ent["keep"]["extractor"].exists()
    assert [p.name for p in croot.iterdir()] == ["extractors"] or sorted(
        x.name for x in croot.iterdir() if any(x.iterdir())) == ["extractors"]
    cache.execute(cache.plan(all_=True, extractors=True))
    assert not ent["keep"]["extractor"].exists()
    with pytest.raises(cache.CacheError):
        cache.plan(extractors=True)


def test_dry_run_deletes_nothing(croot, tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    ent = populate(croot, proj)
    db = touch(tmp_path / "g.db", b"SQLite format 3\0" + b"\0" * 84)
    env = dict(os.environ, CG_CACHE=str(croot))
    r = run("--all", "--extractors", "--stale", proj, "--db", db, "--dry-run", "--json", env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["dry_run"] and out["would_remove"] and out["bytes"] > 0
    assert all(p.exists() for grp in ent.values() for p in grp.values()) and db.exists()
    r = run(proj, "--dry-run", env=env)
    assert r.returncode == 0 and "would remove 8 entries" in r.stdout and str(proj.resolve()) in r.stdout


def test_db_and_its_wal_shm(croot, tmp_path):
    db = touch(tmp_path / "g.db", b"SQLite format 3\0" + b"\0" * 84)
    wal, shm = touch(tmp_path / "g.db-wal"), touch(tmp_path / "g.db-shm")
    other = touch(tmp_path / "g.db.bak")
    pl = cache.plan(db=db)
    assert sorted(pl.db_files) == sorted([db, wal, shm]) and not pl.remove
    cache.execute(pl)
    assert not db.exists() and not wal.exists() and not shm.exists() and other.exists()
    notdb = touch(tmp_path / "notes.txt", b"hello")
    with pytest.raises(cache.CacheError, match="not an SQLite database"):
        cache.plan(db=notdb)
    assert notdb.exists()


@pytest.mark.parametrize("where", ["/", "home", "home-parent"])
def test_refuses_root_or_home(tmp_path, monkeypatch, where):
    home = tmp_path / "home" / "me"
    home.mkdir(parents=True)
    keep = touch(home / "important.txt")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    target = {"/": Path("/"), "home": home, "home-parent": home.parent}[where]
    monkeypatch.setenv("CG_CACHE", str(target))
    for kw in ({"all_": True}, {"stale": True}, {"project": tmp_path}):
        with pytest.raises(cache.CacheError, match="refusing to clean"):
            cache.plan(**kw)
    env = dict(os.environ, CG_CACHE=str(target), HOME=str(home))
    r = run("--all", env=env)
    assert r.returncode == 2 and "refusing to clean" in r.stderr
    assert keep.exists()


def test_never_deletes_outside_the_root(croot, tmp_path):
    outside = tmp_path / "outside"
    keep = touch(outside / "data.txt")
    (croot / "ts").mkdir(parents=True)
    os.symlink(outside, croot / "ts" / f"{'a' * 12}-v{V}-link.json")      # a symlink inside the cache
    os.symlink(outside, croot / "swift-build")                              # a symlinked kind directory
    cache.execute(cache.plan(all_=True, extractors=True))
    assert keep.exists()


def test_doctor_reports_cache_size_by_kind(croot, tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    populate(croot, proj)
    from cg_code_graph import doctor
    u = cache.usage()
    assert u["root"] == str(croot) and u["bytes"] > 0 and u["stale_bytes"] > 0
    assert {"scip", "rust-analyzer", "swift-build", "ts", "dart", "extractors", "projects"} <= set(u["kinds"])
    r = doctor.report()
    assert r["cache_usage"]["bytes"] == u["bytes"]
    line = next(x for x in doctor.render(r).splitlines() if x.startswith("cache:"))
    assert str(croot) in line and "scip" in line and "cg clean --stale" in line

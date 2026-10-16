"""One cg-mcp server, many graphs (issue #200)."""
import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cg_code_graph.core.store import GraphStore


@pytest.fixture
def mcp():
    from cg_code_graph import mcp_server as M
    yield M
    importlib.reload(M)


def _db(path: Path, project: str, root: Path, commit: str | None = None, languages=None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    st = GraphStore.create(path)
    meta = {"project": project, "root": str(root), "languages": languages or ["python"], "stats": {}}
    if commit:
        meta["commit"] = commit
    st.set_meta(**meta)
    st.db.close()


def _git(repo: Path, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, timeout=20)


def test_single_db_has_no_graph_field(mcp, tmp_path, monkeypatch):
    root = tmp_path / "only"
    db = tmp_path / "only.db"
    _db(db, "only", root)
    monkeypatch.chdir(root)
    mcp.configure(single_db=str(db))
    import asyncio
    tools = {t.name: t for t in asyncio.run(mcp.server.list_tools())}
    assert "graph" not in (tools["stats"].input_schema.get("properties") or {})
    text = mcp.stats()
    assert "only" in text and not text.startswith("graph ")
    assert "graph" not in mcp.stats.structured()


def test_ten_graphs_one_tool_set_and_resolution(mcp, tmp_path, monkeypatch):
    import asyncio
    entries = []
    for i in range(10):
        root = tmp_path / f"repo{i}"
        db = tmp_path / f"g{i}.db"
        _db(db, f"proj{i}", root, commit=f"{i:040x}", languages=["python"] if i % 2 == 0 else ["php"])
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
        entries.append((f"g{i}", db, root))
    reg = tmp_path / "graphs.toml"
    lines = ["default = \"g3\"", ""]
    for name, db, root in entries:
        lines += ["[[graphs]]", f'name = "{name}"', f'db = "{db}"', f'roots = ["{root}"]', ""]
    reg.write_text("\n".join(lines), encoding="utf-8")
    before = [t.name for t in asyncio.run(mcp.server.list_tools())]
    mcp.configure(graphs=str(reg))
    names = [t.name for t in asyncio.run(mcp.server.list_tools())]
    assert len(names) == len(set(names)) == len(before)
    assert "graphs" in names
    props = {t.name: t for t in asyncio.run(mcp.server.list_tools())}["stats"].input_schema["properties"]
    assert "graph" in props
    listed = mcp.graphs()
    for name, _dbp, _root in entries:
        assert name in listed
    assert "languages: python" in listed and "languages: php" in listed
    assert "freshness:" in listed and "commit:" in listed

    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(entries[1][2])
    assert mcp.stats().startswith("graph g1\n")
    assert mcp.stats.structured()["graph"] == "g1"

    monkeypatch.chdir(outside)
    via_path = mcp.coverage(path=str(entries[4][2] / "src" / "a.py"))
    assert via_path.startswith("graph g4\n")

    token = mcp._MCP_ROOTS.set([str(entries[6][2])])
    try:
        assert mcp.stats().startswith("graph g6\n")
    finally:
        mcp._MCP_ROOTS.reset(token)

    assert mcp.stats().startswith("graph g3\n")  # configured default, cwd matches nothing

    for name, _dbp, root in entries:
        assert mcp.stats(graph=name).startswith(f"graph {name}\n")
        assert f"proj{name[1:]}" in mcp.stats(graph=name)
    assert mcp.stats(graph="g0").startswith("graph g0\n")
    assert "unknown graph 'nope'" in mcp.stats(graph="nope")

    shared = tmp_path / "shared"
    left, right = tmp_path / "left.db", tmp_path / "right.db"
    _db(left, "left", shared)
    _db(right, "right", shared)
    overlap = tmp_path / "overlap.toml"
    overlap.write_text(
        f'[[graphs]]\nname = "left"\ndb = "{left}"\nroots = ["{shared}"]\n\n'
        f'[[graphs]]\nname = "right"\ndb = "{right}"\nroots = ["{shared}"]\n',
        encoding="utf-8")
    mcp.configure(graphs=str(overlap))
    monkeypatch.chdir(shared)
    err = mcp.stats()
    assert err.startswith("ambiguous graph:")
    assert "left" in err and "right" in err

    wide = tmp_path / "wide.db"
    _db(wide, "wide", shared)
    # a second root so the combined graph's roots are a superset of `left`
    extra = tmp_path / "extra"
    extra.mkdir()
    combo = tmp_path / "combo.toml"
    combo.write_text(
        f'[[graphs]]\nname = "left"\ndb = "{left}"\nroots = ["{shared}"]\n\n'
        f'[[graphs]]\nname = "wide"\ndb = "{wide}"\nroots = ["{shared}", "{extra}"]\n',
        encoding="utf-8")
    mcp.configure(graphs=str(combo))
    monkeypatch.chdir(shared)
    assert mcp.stats().startswith("graph wide\n")

    kept = mcp.apply_tool_allowlist("core")
    assert "graphs" in kept and "attack_surface" not in kept


def test_freshness_follows_head(mcp, tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    assert _git(repo, "add", "a.py").returncode == 0
    assert _git(repo, "commit", "-m", "init").returncode == 0
    head = _git(repo, "rev-parse", "HEAD")
    assert head.returncode == 0
    db = tmp_path / "g.db"
    _db(db, "repo", repo, commit=head.stdout.strip())
    reg = tmp_path / "graphs.toml"
    reg.write_text(f'default = "repo"\n\n[[graphs]]\nname = "repo"\ndb = "{db}"\nroots = ["{repo}"]\n', encoding="utf-8")
    mcp.configure(graphs=str(reg))
    assert "freshness: fresh" in mcp.graphs()
    (repo / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    assert _git(repo, "add", "a.py").returncode == 0
    assert _git(repo, "commit", "-m", "edit").returncode == 0
    assert "freshness: stale" in mcp.graphs()


def test_migrate_dry_run_and_apply(tmp_path, monkeypatch, capsys):
    from cg_code_graph.cli import main
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    db_a, db_b = tmp_path / "alpha.db", tmp_path / "beta.db"
    _db(db_a, "alpha", alpha)
    _db(db_b, "beta", beta)
    cursor = home / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True)
    cursor.write_text(json.dumps({"mcpServers": {
        "cg-alpha": {"type": "stdio", "command": "cg-mcp", "args": ["--db", str(db_a), "--root", str(alpha)]},
        "cg-beta": {"type": "stdio", "command": "cg-mcp", "args": ["--db", str(db_b), "--root", str(beta)]},
        "other": {"command": "keep", "args": []},
    }}), encoding="utf-8")
    graphs = tmp_path / "graphs.toml"
    rc = main(["install", "--host", "cursor", "--migrate", "--graphs", str(graphs)])
    out = capsys.readouterr().out
    assert rc == 0 and "dry run" in out and "alpha" in out and "beta" in out
    assert not graphs.exists()
    assert "cg-alpha" in cursor.read_text()
    rc = main(["install", "--host", "cursor", "--migrate", "--apply", "--graphs", str(graphs)])
    out = capsys.readouterr().out
    assert rc == 0 and "wrote" in out
    assert graphs.is_file() and list(cursor.parent.glob("mcp.json.*.bak"))
    data = json.loads(cursor.read_text())
    assert "cg-alpha" not in data["mcpServers"] and "cg-beta" not in data["mcpServers"]
    assert data["mcpServers"]["other"]["command"] == "keep"
    args = data["mcpServers"]["cg"]["args"]
    assert "--db" not in args and "--root" not in args
    body = graphs.read_text()
    assert 'name = "alpha"' in body and 'name = "beta"' in body and str(alpha) in body


def test_migrate_keeps_tools_relative_db_and_plain_cg(tmp_path, monkeypatch, capsys):
    from cg_code_graph.cli import main
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    repo = tmp_path / "repo"
    db = tmp_path / "repo.db"
    _db(db, "repo", repo)
    cursor = home / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True)
    cursor.write_text(json.dumps({"mcpServers": {
        "cg": {"command": "cg-mcp", "args": ["--db", "repo.db", "--root", str(repo), "--tools", "core"],
               "env": {"CG_MCP_TOOLS": "core"}},
        "cg-other": {"command": "cg-mcp", "args": ["--db", str(db), "--tools", "impact"],
                     "env": {"CG_MCP_TOOLS": "search"}},
    }}), encoding="utf-8")
    # relative --db is resolved from the config directory, so place the db beside mcp.json too
    placed = cursor.parent / "repo.db"
    placed.write_bytes(db.read_bytes())
    graphs = tmp_path / "graphs.toml"
    graphs.write_text('default = "kept"\n\n[[graphs]]\nname = "kept"\ndb = "/tmp/kept.db"\nroots = []\n', encoding="utf-8")
    rc = main(["install", "--host", "cursor", "--migrate", "--apply", "--graphs", str(graphs)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "warning:" in out
    assert list(graphs.parent.glob("graphs.toml.*.bak"))
    body = graphs.read_text()
    assert 'name = "kept"' in body
    assert 'name = "repo"' in body and str(placed.resolve()) in body
    assert 'tools = "core"' in body
    assert 'tools = "impact"' in body
    data = json.loads(cursor.read_text())
    assert "--db" not in data["mcpServers"]["cg"]["args"]


def test_registry_install_omits_db(tmp_path, monkeypatch, capsys):
    from cg_code_graph.cli import main
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    rc = main(["install", "--host", "cursor", "--registry", "--yes"])
    assert rc == 0
    data = json.loads((home / ".cursor" / "mcp.json").read_text())
    assert "--db" not in data["mcpServers"]["cg"]["args"]
    assert "--root" not in data["mcpServers"]["cg"]["args"]


def test_db_dir_and_repeatable_db(mcp, tmp_path, monkeypatch):
    folder = tmp_path / "dbs"
    folder.mkdir()
    roots = []
    for name in ("one", "two"):
        root = tmp_path / name
        _db(folder / f"{name}.db", name, root)
        roots.append(root)
    monkeypatch.chdir(roots[0])
    mcp.configure(db_dir=str(folder))
    assert mcp.stats().startswith("graph one\n")
    mcp2_db = tmp_path / "extra.db"
    _db(mcp2_db, "extra", tmp_path / "extra")
    # a second configure replaces the registry
    importlib.reload(mcp)
    from cg_code_graph import mcp_server as again
    again.configure(dbs=[f"one={folder / 'one.db'}", f"two={folder / 'two.db'}"])
    monkeypatch.chdir(roots[1])
    assert again.stats().startswith("graph two\n")


def test_concurrent_calls_keep_their_own_graph(mcp, tmp_path, monkeypatch):
    """Sync tools run in worker threads: a call must neither see another call's graph nor reuse its connection."""
    from concurrent.futures import ThreadPoolExecutor
    dbs = []
    for i in range(4):
        root = tmp_path / f"r{i}"
        db = tmp_path / f"g{i}.db"
        _db(db, f"project{i}x", root)
        dbs.append(f"g{i}={db}")
    monkeypatch.chdir(tmp_path)
    mcp.configure(dbs=dbs)
    jobs = [i % 4 for i in range(64)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        outs = list(pool.map(lambda i: (i, mcp.stats(graph=f"g{i}")), jobs))
    for i, text in outs:
        assert text.startswith(f"graph g{i}\n"), text[:200]
        assert f"project{i}x" in text and not any(f"project{j}x" in text for j in range(4) if j != i), text[:200]


def test_per_graph_tools_do_not_shrink_the_other_graph(mcp, tmp_path, monkeypatch):
    import asyncio
    backend = tmp_path / "backend"
    web = tmp_path / "web"
    bdb, wdb = tmp_path / "backend.db", tmp_path / "web.db"
    _db(bdb, "backend", backend)
    _db(wdb, "web", web)
    reg = tmp_path / "graphs.toml"
    reg.write_text(
        f'[[graphs]]\nname = "backend"\ndb = "{bdb}"\nroots = ["{backend}"]\ntools = "stats"\n\n'
        f'[[graphs]]\nname = "web"\ndb = "{wdb}"\nroots = ["{web}"]\ntools = "coverage"\n',
        encoding="utf-8")
    before = {t.name for t in asyncio.run(mcp.server.list_tools())}
    mcp.configure(graphs=str(reg))
    listed = {t.name for t in asyncio.run(mcp.server.list_tools())}
    assert listed == {"stats", "coverage"}
    assert "impact" in before and "impact" not in listed
    monkeypatch.chdir(backend)
    assert mcp.stats().startswith("graph backend\n")
    denied = mcp.coverage()
    assert denied == "tool coverage is not allowed for graph backend"
    monkeypatch.chdir(web)
    assert "tool stats is not allowed for graph web" == mcp.stats()
    assert mcp.coverage().startswith("graph web\n")


def test_shared_backend_picks_its_own_graph(mcp, tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    app_a = tmp_path / "appA"
    app_b = tmp_path / "appB"
    bdb = tmp_path / "backend.db"
    adb = tmp_path / "appa.db"
    bdb2 = tmp_path / "appb.db"
    _db(bdb, "backend", backend)
    _db(adb, "appa", app_a)
    _db(bdb2, "appb", app_b)
    reg = tmp_path / "graphs.toml"
    reg.write_text(
        f'[[graphs]]\nname = "backend"\ndb = "{bdb}"\nroots = ["{backend}"]\n\n'
        f'[[graphs]]\nname = "appa"\ndb = "{adb}"\nroots = ["{app_a}", "{backend}"]\n\n'
        f'[[graphs]]\nname = "appb"\ndb = "{bdb2}"\nroots = ["{app_b}", "{backend}"]\n',
        encoding="utf-8")
    mcp.configure(graphs=str(reg))
    monkeypatch.chdir(backend)
    assert mcp.stats().startswith("graph backend\n")

    left, right = tmp_path / "left.db", tmp_path / "right.db"
    shared = tmp_path / "shared"
    _db(left, "left", shared)
    _db(right, "right", shared)
    overlap = tmp_path / "overlap.toml"
    overlap.write_text(
        f'default = "right"\n'
        f'[[graphs]]\nname = "left"\ndb = "{left}"\nroots = ["{shared}"]\n\n'
        f'[[graphs]]\nname = "right"\ndb = "{right}"\nroots = ["{shared}"]\n',
        encoding="utf-8")
    mcp.configure(graphs=str(overlap))
    monkeypatch.chdir(shared)
    assert mcp.stats().startswith("graph right\n")


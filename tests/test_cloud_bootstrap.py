"""Clean-machine install: PATH hint, python -m, non-interactive MCP install, .cg-head freshness."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Ledger", "-c", "user.email=ledger@example.com", *args],
        capture_output=True, text=True, timeout=20,
    )


def _init_repo(repo: Path) -> str:
    repo.mkdir()
    (repo / "ledger.py").write_text("def total():\n    return 1\n", encoding="utf-8")
    assert _git(repo, "init", "-b", "main").returncode == 0
    assert _git(repo, "add", "ledger.py").returncode == 0
    assert _git(repo, "commit", "-m", "init").returncode == 0
    head = _git(repo, "rev-parse", "HEAD")
    assert head.returncode == 0
    return head.stdout.strip()


def test_python_module_entrypoint_runs_the_cli():
    proc = subprocess.run(
        [sys.executable, "-m", "cg_code_graph", "--version"],
        capture_output=True, text=True, cwd=ROOT, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("cg ")


def test_doctor_prints_path_fix_without_editing_shell(monkeypatch, tmp_path):
    from cg_code_graph import doctor

    scripts = tmp_path / "user-bin"
    scripts.mkdir()
    (scripts / "cg").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr("cg_code_graph.doctor._script_dirs", lambda: [str(scripts)])
    text = doctor.render(doctor.report())
    assert "command: cg not on PATH" in text
    assert f'export PATH="{scripts}:$PATH"' in text
    assert f"{sys.executable} -m cg_code_graph" in text
    assert "Not a Rust crate" in text
    assert "cargo install does not apply" in text


def test_doctor_skips_path_fix_when_cg_resolves(monkeypatch, tmp_path):
    from cg_code_graph import doctor

    monkeypatch.setenv("CG_CACHE", str(tmp_path / "cache"))
    real = shutil.which

    def which(cmd, *args, **kwargs):
        if cmd == "cg":
            return "/opt/cg-bin/cg"
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr("cg_code_graph.doctor.shutil.which", which)
    text = doctor.render(doctor.report())
    assert "command: cg on PATH (/opt/cg-bin/cg)" in text
    assert "export PATH=" not in text


def test_install_yes_project_writes_cursor_without_a_prompt(tmp_path, monkeypatch, capsys):
    from cg_code_graph.cli import main

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    proj = tmp_path / "ledger"
    proj.mkdir()
    refused = main(["install", "--project", "--dir", str(proj), "--no-rules"])
    out = capsys.readouterr().out
    assert refused == 2 and "no MCP host detected" in out and "--yes" in out
    assert not (proj / ".cursor").exists()

    rc = main(["install", "--yes", "--project", "--dir", str(proj), "--no-rules"])
    written = capsys.readouterr().out
    assert rc == 0, written
    assert "Cursor project config" in written
    data = json.loads((proj / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "cg" in data["mcpServers"]
    assert not (proj / "AGENTS.md").exists()


def test_cg_head_stamp_and_graphs_freshness(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph.mcp_graphs import GraphEntry, adopt_cg_head, describe, write_cg_head

    repo = tmp_path / "ledger"
    head = _init_repo(repo)
    db = tmp_path / "out" / "graph.db"
    db.parent.mkdir()
    st = GraphStore.create(db)
    st.set_meta(project="ledger", root=str(repo), stats={})
    st.db.close()

    entry = GraphEntry("ledger", db, roots=[repo])
    assert describe(entry)["freshness"] == "missing"
    assert describe(entry)["commit"] == ""

    write_cg_head(db, head)
    assert not (db.parent / ".cg-head.tmp").exists()
    assert not Path(str(db) + ".cg-head.tmp").exists()
    assert (db.parent / ".cg-head").read_text(encoding="utf-8").strip() == f"graph.db {head}"
    assert Path(str(db) + ".cg-head").read_text(encoding="utf-8").strip() == head
    info = describe(entry)
    assert info["freshness"] == "fresh"
    assert info["commit"] == head[:12]

    (repo / "ledger.py").write_text("def total():\n    return 2\n", encoding="utf-8")
    assert _git(repo, "add", "ledger.py").returncode == 0
    assert _git(repo, "commit", "-m", "edit").returncode == 0
    assert describe(entry)["freshness"] == "stale"

    swapped = tmp_path / "out" / "graph.db.refresh.tmp"
    write_cg_head(swapped, head)
    adopt_cg_head(swapped, db)
    text = (db.parent / ".cg-head").read_text(encoding="utf-8")
    assert f"graph.db {head}" in text
    assert "refresh.tmp" not in text
    assert not Path(str(swapped) + ".cg-head").exists()

    Path(str(db) + ".cg-head").unlink()
    (db.parent / ".cg-head").unlink()
    assert describe(entry)["freshness"] == "missing"

    (db.parent / ".cg-head").mkdir()
    failed = describe(entry)
    assert failed["freshness"] == "unknown"
    assert failed["commit"] == ""


def test_index_records_cg_head(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    from cg_code_graph.indexer import index_project

    repo = tmp_path / "ledger"
    head = _init_repo(repo)
    db = tmp_path / "out" / "graph.db"
    index_project(repo, db, "ledger")
    assert (db.parent / ".cg-head").read_text(encoding="utf-8").strip() == f"graph.db {head}"
    assert Path(str(db) + ".cg-head").read_text(encoding="utf-8").strip() == head
    assert not (db.parent / ".cg-head.tmp").exists()


def test_cloud_bootstrap_docs_are_linked():
    page = (ROOT / "docs" / "cloud-agents.md").read_text(encoding="utf-8")
    assert page.startswith("# Cloud agent bootstrap")
    for phrase in (
        "python -m pip install --user",
        "python -m cg_code_graph",
        "export PATH",
        ".cg-head",
        "os.replace",
        "heuristic",
        "--budget",
        "--kind",
        "cg install --yes --project",
        "cargo install",
        "Not a Rust crate",
    ):
        assert phrase in page
    assert "touches" not in page.lower()
    quick = (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    install = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    nav = (ROOT / "docs-site" / "app" / "utils" / "docs-nav.ts").read_text(encoding="utf-8")
    assert "cloud-agents.md" in quick and "cloud-agents.md" in install
    assert "cloud-agents" in nav
    assert "pip install cg-code-graph" in readme
    assert "not a Rust crate" in readme
    assert "cargo install" in readme
    assert "Python package" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "touches" not in page

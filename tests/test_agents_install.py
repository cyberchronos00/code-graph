"""Tests for `cg agents` (issue #144): install / update / remove / dry-run of the managed block
and the cg MCP entry, with pre-existing content preserved byte-for-byte."""
import json

import pytest

from codegraph import agents, agent_rules


def _write(p, text):
    p.write_text(text, encoding="utf-8")
    return p


def test_insert_preserves_surrounding(tmp_path):
    f = _write(tmp_path / "AGENTS.md", "# My project\n\nSome notes.\n")
    rc = agents.run("install", root=str(tmp_path), targets=["agents"], assume_yes=True, out=lambda *a: None)
    assert rc == 0
    out = f.read_text()
    assert out.startswith("# My project\n\nSome notes.\n")
    assert agent_rules.BEGIN_MARK in out and agent_rules.END_MARK in out
    assert out.count(agent_rules.BEGIN_MARK) == 1


def test_update_replaces_stale_block_in_place(tmp_path):
    # a block with the SAME markers but stale inner text, wrapped by content that must survive
    stale = agent_rules.BEGIN_MARK + "\nOLD STALE RULES\n" + agent_rules.END_MARK
    f = _write(tmp_path / "AGENTS.md", "X-TOP\n\n" + stale + "\n\nY-BOTTOM\n")
    rc = agents.run("update", root=str(tmp_path), targets=["agents"], assume_yes=True, out=lambda *a: None)
    assert rc == 0
    out = f.read_text()
    assert "OLD STALE RULES" not in out
    assert out.count(agent_rules.BEGIN_MARK) == 1
    assert out.startswith("X-TOP\n\n") and out.endswith("\n\nY-BOTTOM\n")
    assert agent_rules.TEXT.strip().splitlines()[0] in out


def test_remove_round_trips_byte_for_byte(tmp_path):
    orig = "# Keep me\n\nline two\n"
    f = _write(tmp_path / "AGENTS.md", orig)
    agents.run("install", root=str(tmp_path), targets=["agents"], assume_yes=True, out=lambda *a: None)
    assert f.read_text() != orig
    agents.run("remove", root=str(tmp_path), targets=["agents"], assume_yes=True, out=lambda *a: None)
    assert f.read_text() == orig


def test_dry_run_writes_nothing(tmp_path):
    orig = "# proj\n"
    f = _write(tmp_path / "AGENTS.md", orig)
    rc = agents.run("install", root=str(tmp_path), targets=["agents"], dry_run=True, out=lambda *a: None)
    assert rc == 0
    assert f.read_text() == orig


def test_show_writes_nothing(tmp_path):
    f = _write(tmp_path / "AGENTS.md", "# proj\n")
    rc = agents.run("show", root=str(tmp_path), targets=["agents"], out=lambda *a: None)
    assert rc == 0
    assert agent_rules.BEGIN_MARK not in f.read_text()


def test_prompt_abort_writes_nothing(tmp_path):
    orig = "# proj\n"
    f = _write(tmp_path / "AGENTS.md", orig)
    rc = agents.run("install", root=str(tmp_path), targets=["agents"],
                    input_fn=lambda *a: "n", out=lambda *a: None)
    assert rc == 1
    assert f.read_text() == orig


def test_prompt_yes_writes(tmp_path):
    f = _write(tmp_path / "AGENTS.md", "# proj\n")
    rc = agents.run("install", root=str(tmp_path), targets=["agents"],
                    input_fn=lambda *a: "y", out=lambda *a: None)
    assert rc == 0
    assert agent_rules.BEGIN_MARK in f.read_text()


def test_mcp_adds_cg_and_preserves_others(tmp_path):
    mcpf = tmp_path / ".cursor" / "mcp.json"
    mcpf.parent.mkdir(parents=True)
    _write(mcpf, json.dumps({"mcpServers": {"other": {"command": "x", "args": ["--a"]}},
                             "someOtherKey": 7}, indent=2) + "\n")
    rc = agents.run("install", root=str(tmp_path), targets=[], mcp=True, assume_yes=True, out=lambda *a: None)
    assert rc == 0
    data = json.loads(mcpf.read_text())
    assert data["someOtherKey"] == 7
    assert data["mcpServers"]["other"] == {"command": "x", "args": ["--a"]}
    assert data["mcpServers"]["cg"]["command"] == "cg-mcp"
    # remove deletes only cg
    agents.run("remove", root=str(tmp_path), targets=[], mcp=True, assume_yes=True, out=lambda *a: None)
    data = json.loads(mcpf.read_text())
    assert "cg" not in data["mcpServers"]
    assert "other" in data["mcpServers"]


def test_mcp_creates_file_when_absent(tmp_path):
    mcpf = tmp_path / "mcp.json"
    rc = agents.run("install", root=str(tmp_path), targets=[], mcp=True, mcp_file=str(mcpf),
                    assume_yes=True, out=lambda *a: None)
    assert rc == 0
    data = json.loads(mcpf.read_text())
    assert data["mcpServers"]["cg"]["command"] == "cg-mcp"


def test_mcp_leaves_invalid_json_untouched(tmp_path):
    mcpf = _write(tmp_path / "mcp.json", "{not json")
    rc = agents.run("install", root=str(tmp_path), targets=[], mcp=True, mcp_file=str(mcpf),
                    assume_yes=True, out=lambda *a: None)
    assert rc == 2
    assert mcpf.read_text() == "{not json"


def test_default_targets_detect_existing(tmp_path):
    _write(tmp_path / "CLAUDE.md", "# claude\n")
    # no --target: should pick CLAUDE.md (exists) and not create AGENTS.md
    rc = agents.run("install", root=str(tmp_path), assume_yes=True, out=lambda *a: None)
    assert rc == 0
    assert agent_rules.BEGIN_MARK in (tmp_path / "CLAUDE.md").read_text()
    assert not (tmp_path / "AGENTS.md").exists()


def test_nothing_selected_returns_2(tmp_path):
    rc = agents.run("install", root=str(tmp_path), out=lambda *a: None)
    assert rc == 2


def _marked_bodies(root):
    bodies = []
    for name in agents.TARGETS.values():
        p = root / name
        if not p.exists():
            continue
        text = p.read_text()
        start = text.index(agent_rules.BEGIN_MARK) + len(agent_rules.BEGIN_MARK)
        end = text.index(agent_rules.END_MARK)
        bodies.append(text[start:end])
    return bodies


def test_all_writes_one_full_block_and_two_pointers(tmp_path):
    lines = []
    rc = agents.run("install", root=str(tmp_path), all_targets=True, dry_run=True, out=lines.append)
    assert rc == 0
    out = "\n".join(lines)
    assert out.count("# Reading this codebase with cg") == 1
    assert out.count("[insert]") == 3
    assert "@AGENTS.md" in out
    agents.run("install", root=str(tmp_path), all_targets=True, assume_yes=True, out=lambda *a: None)
    inside = "".join(_marked_bodies(tmp_path))
    assert len(inside) < 1600
    assert (tmp_path / "AGENTS.md").read_text().count("# Reading this codebase with cg") == 1
    assert "# Reading this codebase with cg" not in (tmp_path / "CLAUDE.md").read_text()
    assert "# Reading this codebase with cg" not in (tmp_path / ".cursor/rules/cg.mdc").read_text()


def test_full_block_pins_pypi_not_editable():
    from codegraph import __version__

    major, minor, *_rest = __version__.split(".")
    expected = f"pip install 'cg-code-graph>={major}.{minor}'"
    body = agent_rules.project_text()
    assert expected in body
    assert "cg-mcp" in body
    assert "pip install -e" not in body
    assert len(body) <= 1000


def test_claude_and_cursor_pointers_name_agents(tmp_path):
    agents.run("install", root=str(tmp_path), all_targets=True, assume_yes=True, out=lambda *a: None)
    claude = (tmp_path / "CLAUDE.md").read_text()
    cursor = (tmp_path / ".cursor/rules/cg.mdc").read_text()
    assert "@AGENTS.md" in claude
    assert "AGENTS.md" in cursor
    assert "@AGENTS.md" not in cursor


def test_all_migrates_stale_full_block_to_pointer(tmp_path):
    stale = agent_rules.BEGIN_MARK + "\nOLD STALE RULES\n" + agent_rules.END_MARK
    f = _write(tmp_path / "CLAUDE.md", "X-TOP\n\n" + stale + "\n\nY-BOTTOM\n")
    lines = []
    rc = agents.run("install", root=str(tmp_path), all_targets=True, assume_yes=True, out=lines.append)
    assert rc == 0
    joined = "\n".join(lines)
    assert "CLAUDE.md" in joined and "[update]" in joined
    out = f.read_text()
    assert "OLD STALE RULES" not in out
    assert "@AGENTS.md" in out
    assert out.count(agent_rules.BEGIN_MARK) == 1
    assert out.startswith("X-TOP\n\n") and out.endswith("\n\nY-BOTTOM\n")
    for name in agents.TARGETS.values():
        assert (tmp_path / name).read_text().count(agent_rules.BEGIN_MARK) == 1


def test_single_target_is_full_block_pair_names_primary(tmp_path):
    agents.run("install", root=str(tmp_path), targets=["claude"], assume_yes=True, out=lambda *a: None)
    assert "# Reading this codebase with cg" in (tmp_path / "CLAUDE.md").read_text()
    agents.run("install", root=str(tmp_path), targets=["claude", "cursor"], assume_yes=True,
               out=lambda *a: None)
    assert "# Reading this codebase with cg" in (tmp_path / "CLAUDE.md").read_text()
    cursor = (tmp_path / ".cursor/rules/cg.mdc").read_text()
    assert "CLAUDE.md" in cursor
    assert "# Reading this codebase with cg" not in cursor


def test_remove_after_all_restores_bytes(tmp_path):
    orig_a = "# keep agents\n"
    orig_c = "# keep claude\n"
    _write(tmp_path / "AGENTS.md", orig_a)
    _write(tmp_path / "CLAUDE.md", orig_c)
    agents.run("install", root=str(tmp_path), all_targets=True, assume_yes=True, out=lambda *a: None)
    created = tmp_path / ".cursor/rules/cg.mdc"
    assert agent_rules.BEGIN_MARK in created.read_text()
    agents.run("remove", root=str(tmp_path), all_targets=True, assume_yes=True, out=lambda *a: None)
    assert (tmp_path / "AGENTS.md").read_text() == orig_a
    assert (tmp_path / "CLAUDE.md").read_text() == orig_c
    assert agent_rules.BEGIN_MARK not in created.read_text()


def test_rerun_is_a_noop(tmp_path):
    agents.run("install", root=str(tmp_path), all_targets=True, assume_yes=True, out=lambda *a: None)
    before = {n: (tmp_path / n).read_text() for n in agents.TARGETS.values()}
    lines = []
    rc = agents.run("install", root=str(tmp_path), all_targets=True, dry_run=True, out=lines.append)
    assert rc == 0
    assert sum("no change (block up to date)" in ln for ln in lines) == 3
    assert not any("[update]" in ln for ln in lines)
    lines = []
    assert agents.run("install", root=str(tmp_path), all_targets=True, out=lines.append,
                      input_fn=lambda *_: pytest.fail("prompted on a no-op re-run")) == 0
    assert "nothing to do" in lines
    assert {n: (tmp_path / n).read_text() for n in agents.TARGETS.values()} == before


@pytest.mark.parametrize("version,expected", [("0.14.0", "cg-code-graph>=0.14"), ("1.2.3rc1", "cg-code-graph>=1.2"),
                                              ("0.15.0.dev2", "cg-code-graph>=0.15"), ("10.20", "cg-code-graph>=10.20")])
def test_pin_is_major_minor(monkeypatch, version, expected):
    import codegraph
    monkeypatch.setattr(codegraph, "__version__", version)
    assert agent_rules.pin() == expected

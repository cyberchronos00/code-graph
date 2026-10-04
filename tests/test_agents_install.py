"""Tests for `cg agents` (issue #144): install / update / remove / dry-run of the managed block
and the cg MCP entry, with pre-existing content preserved byte-for-byte."""
import json

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

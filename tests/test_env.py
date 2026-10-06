"""CG_* environment names. CODEGRAPH_* names were removed in 0.19.0."""
import cg_code_graph.core.env as env


def _clear():
    env._noted = False


def test_old_name_is_ignored_with_one_notice(monkeypatch, capsys):
    _clear()
    monkeypatch.delenv("CG_JOBS", raising=False)
    monkeypatch.setenv("CODEGRAPH_JOBS", "2")
    assert env.get("JOBS") is None
    assert env.get("JOBS") is None
    err = capsys.readouterr().err
    assert err.count("cg: ignored removed environment variables:") == 1
    assert "CODEGRAPH_JOBS -> CG_JOBS" in err


def test_new_name_only_is_silent(monkeypatch, capsys):
    _clear()
    monkeypatch.delenv("CODEGRAPH_JOBS", raising=False)
    monkeypatch.setenv("CG_JOBS", "3")
    assert env.get("JOBS") == "3"
    assert capsys.readouterr().err == ""


def test_both_names_new_wins_and_one_notice(monkeypatch, capsys):
    _clear()
    monkeypatch.setenv("CG_JOBS", "3")
    monkeypatch.setenv("CODEGRAPH_JOBS", "2")
    assert env.get("JOBS") == "3"
    err = capsys.readouterr().err
    assert err.count("ignored removed environment variables") == 1
    assert "CODEGRAPH_JOBS -> CG_JOBS" in err


def test_cache_dir_alias_is_ignored(monkeypatch, capsys):
    _clear()
    monkeypatch.delenv("CG_CACHE", raising=False)
    monkeypatch.delenv("CODEGRAPH_CACHE", raising=False)
    monkeypatch.setenv("CODEGRAPH_CACHE_DIR", "/tmp/cg-cache-alias")
    assert env.get("CACHE") is None
    err = capsys.readouterr().err
    assert "CODEGRAPH_CACHE_DIR -> CG_CACHE" in err
    assert err.count("\n") == 1


def test_mcp_tools_has_no_legacy_name(monkeypatch, capsys):
    _clear()
    monkeypatch.delenv("CG_MCP_TOOLS", raising=False)
    monkeypatch.setenv("CODEGRAPH_MCP_TOOLS", "core")
    assert env.get("MCP_TOOLS") is None
    assert capsys.readouterr().err == ""
    assert "CODEGRAPH_MCP_TOOLS" not in env.legacy_in_env()


def test_legacy_in_env_lists_set_names(monkeypatch, capsys):
    _clear()
    monkeypatch.setenv("CODEGRAPH_JOBS", "1")
    monkeypatch.setenv("CODEGRAPH_NODE", "bun")
    monkeypatch.delenv("CODEGRAPH_CARGO", raising=False)
    names = env.legacy_in_env()
    assert "CODEGRAPH_JOBS" in names and "CODEGRAPH_NODE" in names
    assert "CODEGRAPH_CARGO" not in names
    err = capsys.readouterr().err
    assert err.count("ignored removed environment variables") == 1
    assert "CODEGRAPH_JOBS -> CG_JOBS" in err and "CODEGRAPH_NODE -> CG_NODE" in err

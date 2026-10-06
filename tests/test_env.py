"""CG_* environment names and the CODEGRAPH_* aliases (removed in 0.18.0)."""
import cg_code_graph.core.env as env


def _clear():
    env._warned.clear()


def test_old_name_only_warns_once(monkeypatch, capsys):
    _clear()
    monkeypatch.delenv("CG_JOBS", raising=False)
    monkeypatch.setenv("CODEGRAPH_JOBS", "2")
    assert env.get("JOBS") == "2"
    assert env.get("JOBS") == "2"
    err = capsys.readouterr().err
    assert err.count("CODEGRAPH_JOBS is deprecated, use CG_JOBS") == 1
    assert "removed in 0.18.0" in err
    assert "is set too and wins" not in err


def test_new_name_only_is_silent(monkeypatch, capsys):
    _clear()
    monkeypatch.delenv("CODEGRAPH_JOBS", raising=False)
    monkeypatch.setenv("CG_JOBS", "3")
    assert env.get("JOBS") == "3"
    assert capsys.readouterr().err == ""


def test_both_names_new_wins_and_warning_names_both(monkeypatch, capsys):
    _clear()
    monkeypatch.setenv("CG_JOBS", "3")
    monkeypatch.setenv("CODEGRAPH_JOBS", "2")
    assert env.get("JOBS") == "3"
    assert env.get("JOBS") == "3"
    err = capsys.readouterr().err
    assert err.count("is deprecated") == 1
    assert "CODEGRAPH_JOBS" in err and "CG_JOBS is set too and wins" in err


def test_cache_dir_alias_is_cg_cache(monkeypatch, capsys):
    _clear()
    monkeypatch.delenv("CG_CACHE", raising=False)
    monkeypatch.delenv("CODEGRAPH_CACHE", raising=False)
    monkeypatch.setenv("CODEGRAPH_CACHE_DIR", "/tmp/cg-cache-alias")
    assert env.get("CACHE") == "/tmp/cg-cache-alias"
    err = capsys.readouterr().err
    assert "CODEGRAPH_CACHE_DIR is deprecated, use CG_CACHE" in err


def test_mcp_tools_has_no_legacy_name(monkeypatch, capsys):
    _clear()
    monkeypatch.delenv("CG_MCP_TOOLS", raising=False)
    monkeypatch.setenv("CODEGRAPH_MCP_TOOLS", "core")
    assert env.get("MCP_TOOLS") is None
    assert capsys.readouterr().err == ""
    assert "CODEGRAPH_MCP_TOOLS" not in env.legacy_in_env()


def test_legacy_in_env_lists_set_names(monkeypatch):
    _clear()
    monkeypatch.setenv("CODEGRAPH_JOBS", "1")
    monkeypatch.setenv("CODEGRAPH_NODE", "bun")
    monkeypatch.delenv("CODEGRAPH_CARGO", raising=False)
    names = env.legacy_in_env()
    assert "CODEGRAPH_JOBS" in names and "CODEGRAPH_NODE" in names
    assert "CODEGRAPH_CARGO" not in names

"""cg install / uninstall, doctor registrations, and CG_MCP_TOOLS."""
import asyncio
import importlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from cg_code_graph.mcp_install import (
    InstallError, _FALLBACK_NOTE, build_entry, config_path, plan_text, remove_cg, remove_member,
    server_command, set_cg, set_member,
)

ENTRY = {"type": "stdio",
         "command": "C:\\Users\\a b\\pipx\\venvs\\cg-code-graph\\Scripts\\cg-mcp.exe",
         "args": ["--db", "/abs/out/graph.db", "--root", "/abs"]}
TOML_ENTRY = {"command": "C:\\Users\\a b\\Scripts\\cg-mcp.exe",
              "args": ["--db", "C:\\p\\out\\graph.db"]}

JSON_CASES = {
    "empty": "{}\n",
    "indent2": '{\n  "mcpServers": {\n    "other": {\n      "command": "x"\n    }\n  }\n}\n',
    "crlf": '{\r\n    "mcpServers": {\r\n        "a": {"command": "a"},\r\n        "b": {"command": "b"}\r\n    }\r\n}\r\n',
    "tabs": '{\n\t"mcpServers": {\n\t\t"other": {"command": "x"}\n\t}}',
    "theme": '{\n  "theme": "dark",\n  "editor": {"font": "mono"}\n}\n',
    "jsonc": '{\n  // my servers\n  "mcpServers": {\n    "other": {"command": "x"} // keep\n  }\n}\n',
    "compact": '{"mcpServers":{"a":{"command":"x"}}}\n',
}
TOML_CASES = {
    "empty": "",
    "model": 'model = "o3"\n',
    "docs": 'model = "o3"\n\n[mcp_servers.docs]\ncommand = "d"\n',
    "other": '[mcp_servers.other]\ncommand = "o"\n',
}


@pytest.fixture
def home(monkeypatch, tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))
    monkeypatch.setenv("APPDATA", str(root / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "xdg"))
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    return root


def test_json_round_trips_and_idempotent():
    for name, src in JSON_CASES.items():
        once = set_member(src, ["mcpServers"], "cg", ENTRY)
        twice = set_member(once, ["mcpServers"], "cg", ENTRY)
        assert twice == once, name
        assert remove_member(once, ["mcpServers"], "cg") == src, name
        assert json.loads(once.replace("// my servers", "").replace("// keep", ""))["mcpServers"]["cg"]["command"] == ENTRY["command"]
    indented = JSON_CASES["indent2"]
    got = set_member(indented, ["mcpServers"], "cg", ENTRY)
    assert '"other"' in got and '"cg"' in got
    assert json.loads(got)["mcpServers"]["cg"]["command"] == ENTRY["command"]


def test_update_changes_only_the_cg_value():
    old = '{\n  "mcpServers": {\n    "cg": {"command": "old", "args": []},\n    "other": {"command": "keep"}\n  }\n}\n'
    new = set_member(old, ["mcpServers"], "cg", {"command": "new", "args": ["--db", "z"]})
    assert old.split('"cg":', 1)[0] == new.split('"cg":', 1)[0]
    assert old.split('"other":', 1)[1] == new.split('"other":', 1)[1]
    assert '"command": "old"' not in new and '"command": "new"' in new
    action, text, err = plan_text(old, kind="json", path_keys=["mcpServers"],
                                  entry={"command": "new", "args": ["--db", "z"]}, remove=False, delete_empty=True)
    assert action == "update" and err is None and text == new


def test_invalid_json_and_toml_stay_untouched():
    action, new, err = plan_text("{nope", kind="json", path_keys=["mcpServers"], entry=ENTRY,
                                 remove=False, delete_empty=True)
    assert action == "error" and new == "{nope" and err
    bad = "[\n= not toml"
    action, new, err = plan_text(bad, kind="toml", path_keys=[], entry=TOML_ENTRY, remove=False, delete_empty=True)
    assert action == "error" and new == bad and err


def test_toml_round_trips():
    import tomllib
    for name, src in TOML_CASES.items():
        once = set_cg(src, TOML_ENTRY)
        assert set_cg(once, TOML_ENTRY) == once, name
        assert remove_cg(once) == src, name
        data = tomllib.loads(once)
        assert data["mcp_servers"]["cg"] == TOML_ENTRY
        if name == "other":
            assert data["mcp_servers"]["other"] == {"command": "o"}
    nonew = 'model = "o3"'
    back = remove_cg(set_cg(nonew, TOML_ENTRY))
    assert back == nonew + "\n"


def test_host_paths(home, monkeypatch, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "linux")
    assert config_path("cursor", "project", root) == root / ".cursor" / "mcp.json"
    assert config_path("cursor", "global", root) == home / ".cursor" / "mcp.json"
    assert config_path("claude", "project", root) == root / ".mcp.json"
    assert config_path("vscode", "project", root) == root / ".vscode" / "mcp.json"
    assert config_path("vscode", "global", root) == home / ".copilot" / "mcp-config.json"
    assert config_path("windsurf", "global", root) == home / "xdg" / "devin" / "mcp_config.json"
    assert config_path("codex", "project", root) == root / ".codex" / "config.toml"
    assert config_path("codex", "global", root) == home / ".codex" / "config.toml"
    assert config_path("gemini", "project", root) == root / ".gemini" / "settings.json"
    assert config_path("gemini", "global", root) == home / ".gemini" / "settings.json"
    assert config_path("zed", "project", root) == root / ".zed" / "settings.json"
    assert config_path("zed", "global", root) == home / ".config" / "zed" / "settings.json"
    with pytest.raises(InstallError, match="global only"):
        config_path("claude-desktop", "project", root)
    with pytest.raises(InstallError, match="no Linux build"):
        config_path("claude-desktop", "global", root)
    with pytest.raises(InstallError, match="global only"):
        config_path("windsurf", "project", root)
    monkeypatch.setenv("COPILOT_HOME", str(home / "copilot-custom"))
    assert config_path("vscode", "global", root) == home / "copilot-custom" / "mcp-config.json"
    monkeypatch.setattr(sys, "platform", "darwin")
    desk = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    assert config_path("claude-desktop", "global", root) == desk
    # a Windows host on POSIX: `os.name` stays patched only inside this block, so pytest's own reporting (which builds
    # `Path`s) never runs with `os.name == "nt"`
    with monkeypatch.context() as win:
        win.setattr(os, "name", "nt")
        win.setattr(sys, "platform", "win32")
        assert config_path("claude-desktop", "global", root) == home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
        assert config_path("windsurf", "global", root) == home / "AppData" / "Roaming" / "devin" / "mcp_config.json"
        assert config_path("cursor", "global", root) == home / ".cursor" / "mcp.json"
        with pytest.raises(InstallError, match="not verified"):
            config_path("zed", "global", root)


def test_entry_shape_per_host(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    cur = build_entry("cursor", "project", root, root / "out" / "graph.db", None, False)
    assert cur["type"] == "stdio" and cur["args"][-2:] == ["--root", str(root.resolve())]
    gem = build_entry("gemini", "global", root, root / "db", None, False)
    assert "type" not in gem and "--root" not in gem["args"]
    zed = build_entry("zed", "project", root, root / "db", "core", False)
    assert zed["env"] == {} and zed["args"][-2:] == ["--tools", "core"]
    port = build_entry("cursor", "project", root, None, None, True)
    assert port["command"] == "cg-mcp" and port["args"] == ["--db", "out/graph.db"] and port["type"] == "stdio"
    action, text, err = plan_text("", kind="json", path_keys=["servers"], entry=cur, remove=False, delete_empty=True)
    assert action == "insert" and err is None and "servers" in json.loads(text)


def _isolate_command_search(monkeypatch, tmp_path):
    """Hide the machine's real cg-mcp so each lookup step can be tested alone."""
    import sysconfig
    scripts = tmp_path / "scripts"
    user_scripts = tmp_path / "user-scripts"
    scripts.mkdir()
    user_scripts.mkdir()
    which = {"hit": None}

    def get_path(name, scheme=None, **kwargs):
        if name != "scripts":
            return None
        return str(user_scripts if scheme and str(scheme).endswith("_user") else scripts)

    monkeypatch.setattr(sysconfig, "get_path", get_path)
    monkeypatch.setattr(shutil, "which", lambda name: which["hit"])
    monkeypatch.setattr(sys, "argv", ["pytest"])
    return scripts, user_scripts, which


def test_server_command(monkeypatch, tmp_path):
    scripts, user_scripts, which = _isolate_command_search(monkeypatch, tmp_path)
    exe = tmp_path / "bin" / "python"
    exe.parent.mkdir()
    exe.write_text("")
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(os, "name", "posix")
    fallback = [str(Path(exe).resolve()), "-m", "cg_code_graph.mcp_server"]
    assert server_command() == fallback

    tool = tmp_path / "bin" / "cg-mcp"
    tool.write_text("")
    later = scripts / "cg-mcp"
    later.write_text("")
    assert server_command() == [str(tool.resolve())]

    tool.unlink()
    assert server_command() == [str(later.resolve())]
    later.unlink()

    user = user_scripts / "cg-mcp"
    user.write_text("")
    assert server_command() == [str(user.resolve())]
    user.unlink()

    on_path = tmp_path / "path" / "cg-mcp"
    on_path.parent.mkdir()
    on_path.write_text("")
    which["hit"] = str(on_path)
    assert server_command() == [str(on_path.resolve())]
    which["hit"] = None

    cg = tmp_path / "running" / "cg"
    cg.parent.mkdir()
    sibling = cg.parent / "cg-mcp"
    sibling.write_text("")
    monkeypatch.setattr(sys, "argv", [str(cg)])
    assert server_command() == [str(sibling.resolve())]
    monkeypatch.setattr(sys, "argv", ["pytest"])

    win_dir = tmp_path / "Scripts"
    win_dir.mkdir()
    win = win_dir / "cg-mcp.exe"
    win.write_text("")
    py = win_dir / "python.exe"
    py.write_text("")
    monkeypatch.setattr(os, "name", "nt")
    which["hit"] = None
    assert server_command() == fallback
    monkeypatch.setattr(sys, "executable", str(py.resolve()))
    assert server_command() == [str(win.resolve())]


def _main(argv):
    from cg_code_graph.cli import main
    return main(argv)


def test_module_fallback_prints_a_note(home, tmp_path, monkeypatch, capsys):
    exe = str(Path(sys.executable).resolve())
    monkeypatch.setattr("cg_code_graph.mcp_install.server_command",
                        lambda: [exe, "-m", "cg_code_graph.mcp_server"])
    proj = tmp_path / "proj"
    proj.mkdir()
    rc = _main(["install", "--host", "cursor", "--dry-run", "--dir", str(proj)])
    out = capsys.readouterr().out
    assert rc == 0 and _FALLBACK_NOTE in out and "cg_code_graph.mcp_server" in out
    assert not (proj / ".cursor").exists()


def test_cli_dry_run_detection_portable_and_round_trip(home, tmp_path, capsys):
    proj = tmp_path / "proj"
    proj.mkdir()
    rc = _main(["install", "--host", "cursor", "--dry-run", "--dir", str(proj)])
    out = capsys.readouterr().out
    assert rc == 0 and "(dry run: nothing written)" in out
    assert ".cursor/mcp.json" in out and "--root" in out and str(proj.resolve()) in out
    cmd = server_command()
    assert cmd[0] in out
    assert not (proj / ".cursor" / "mcp.json").exists()
    (proj / ".cursor").mkdir()
    rc = _main(["install", "--dry-run", "--dir", str(proj)])
    out = capsys.readouterr().out
    assert rc == 0 and "detected: cursor (.cursor/)" in out
    assert not (proj / ".cursor" / "mcp.json").exists()
    rc = _main(["install", "--global", "--host", "cursor", "--dir", str(proj)])
    out = capsys.readouterr().out
    assert rc == 2 and "a global entry serves one graph; pass --db (or use --project)" in out
    rc = _main(["install", "--host", "cursor", "--portable", "--yes", "--dir", str(proj)])
    assert rc == 0
    data = json.loads((proj / ".cursor" / "mcp.json").read_text())
    assert data["mcpServers"]["cg"] == {"type": "stdio", "command": "cg-mcp", "args": ["--db", "out/graph.db"]}
    (proj / ".cursor" / "mcp.json").unlink()
    rc = _main(["install", "--host", "cursor", "--yes", "--dir", str(proj)])
    assert rc == 0 and (proj / ".cursor" / "mcp.json").is_file()
    rc = _main(["install", "--host", "cursor", "--yes", "--dir", str(proj)])
    out = capsys.readouterr().out
    assert rc == 0 and "no change (cg already registered)" in out
    rc = _main(["uninstall", "--host", "cursor", "--yes", "--dir", str(proj)])
    assert rc == 0
    assert not (proj / ".cursor" / "mcp.json").exists()
    assert not (proj / ".cursor").exists()


def test_codex_uninstall_removes_the_entry(home, tmp_path, capsys):
    proj = tmp_path / "proj"
    proj.mkdir()
    assert _main(["install", "--host", "codex", "--yes", "--dir", str(proj)]) == 0
    path = proj / ".codex" / "config.toml"
    text = path.read_text()
    assert "[mcp_servers.cg]" in text and "other" not in text
    assert _main(["uninstall", "--host", "codex", "--yes", "--dir", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "removed" in out
    assert not path.exists()
    assert not path.parent.exists()


def test_cli_legacy_note_and_removal(home, tmp_path, capsys):
    proj = tmp_path / "proj"
    path = proj / ".cursor" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {
        "code-graph": {"command": "/usr/local/bin/cg-mcp", "args": ["--db", "out/graph.db"]},
        "other": {"command": "keep"},
    }}, indent=2) + "\n")
    rc = _main(["install", "--host", "cursor", "--yes", "--dir", str(proj)])
    out = capsys.readouterr().out
    assert rc == 0 and 'older "code-graph" entry' in out
    data = json.loads(path.read_text())
    assert data["mcpServers"]["other"] == {"command": "keep"}
    assert "code-graph" in data["mcpServers"] and "cg" in data["mcpServers"]
    rc = _main(["uninstall", "--host", "cursor", "--yes", "--dir", str(proj)])
    assert rc == 0
    data = json.loads(path.read_text())
    assert "cg" not in data["mcpServers"] and "code-graph" not in data["mcpServers"]
    assert data["mcpServers"]["other"] == {"command": "keep"}


def test_cli_refused_host_and_invalid_file(home, tmp_path, capsys):
    proj = tmp_path / "proj"
    proj.mkdir()
    rc = _main(["install", "--host", "windsurf", "--dir", str(proj)])
    out = capsys.readouterr().out
    assert rc == 2 and "global only" in out
    path = proj / ".cursor" / "mcp.json"
    path.parent.mkdir()
    path.write_text("{nope")
    rc = _main(["install", "--host", "cursor", "--yes", "--dir", str(proj)])
    assert rc == 2 and path.read_text() == "{nope"


def test_claude_global_invokes_cli(home, tmp_path, monkeypatch, capsys):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "claude.log"
    script = bindir / "claude"
    script.write_text("#!/bin/sh\necho \"$@\" >> \"$LOG\"\nexit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("LOG", str(log))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    proj = tmp_path / "proj"
    proj.mkdir()
    db = proj / "graph.db"
    rc = _main(["install", "--host", "claude", "--global", "--db", str(db), "--dry-run", "--dir", str(proj)])
    out = capsys.readouterr().out
    assert rc == 0 and "mcp remove cg --scope user" in out and "mcp add-json cg" in out and "--scope user" in out
    assert not log.exists()
    rc = _main(["install", "--host", "claude", "--global", "--db", str(db), "--yes", "--dir", str(proj)])
    assert rc == 0, capsys.readouterr().out
    lines = log.read_text().splitlines()
    assert lines[0].startswith("mcp remove cg") and "--scope user" in lines[0]
    assert "mcp add-json cg" in lines[1] and "--scope user" in lines[1]


def test_doctor_lists_registrations(home, tmp_path):
    from cg_code_graph.doctor import render, report
    proj = tmp_path / "proj"
    db = proj / "out" / "graph.db"
    cursor = proj / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True)
    cursor.write_text(json.dumps({"mcpServers": {"cg": {
        "command": sys.executable, "args": ["--db", str(db), "--root", str(proj)],
    }}}, indent=2))
    codex = proj / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text(f'[mcp_servers.cg]\ncommand = "{sys.executable}"\nargs = ["--db", "{db}"]\n')
    items = report(proj)["mcp"]
    by = {(i["host"], i["scope"]): i for i in items}
    assert by[("cursor", "project")]["problem"] == "db not found"
    assert by[("cursor", "project")]["ok"] is False
    assert by[("cursor", "project")]["key"] == "cg"
    assert by[("codex", "project")]["problem"] == "db not found"
    text = render(report(proj))
    assert "mcp:" in text and "cursor (project)" in text and "codex (project)" in text
    assert "db not found" in text and text.index("mcp:") < text.index("update:")


def test_tool_allowlist(capsys):
    import fnmatch
    from cg_code_graph import mcp_server as M
    before = [t.name for t in asyncio.run(M.server.list_tools())]
    assert "explore" in before and "affected" in before
    core = {"explore", "search", "node", "snippet", "impact", "reaches", "callers", "routes",
            "downstream", "path", "coverage", "index"}
    try:
        assert M.apply_tool_allowlist("core") == [n for n in before if n in core]
        assert {t.name for t in asyncio.run(M.server.list_tools())} == core
        importlib.reload(M)
        plan = [n for n in before if fnmatch.fnmatch(n, "plan_*")]
        assert len(plan) == 5
        kept = M.apply_tool_allowlist("all,-plan_*")
        assert kept == [n for n in before if n not in plan]
        assert len(kept) == len(before) - 5
        importlib.reload(M)
        assert M.apply_tool_allowlist("plan_*") == plan
        importlib.reload(M)
        warned = M.apply_tool_allowlist("search,nosuch")
        assert "search" in warned and "nosuch" not in warned
        assert "nosuch" in capsys.readouterr().err
        importlib.reload(M)
        with pytest.raises(SystemExit) as exc:
            M.apply_tool_allowlist("nosuch")
        assert exc.value.code == 2
        assert "selects no tools" in capsys.readouterr().err
        assert {t.name for t in asyncio.run(M.server.list_tools())} == set(before)
    finally:
        importlib.reload(M)

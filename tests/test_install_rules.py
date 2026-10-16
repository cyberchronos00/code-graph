"""`cg install` writes "use cg first" agent rules: a marked section in AGENTS.md and an alwaysApply
.cursor/rules/cg.mdc. Re-runs replace only the marked section; --no-rules and --global skip them."""
import pytest

from cg_code_graph import install_rules as IR


@pytest.fixture
def home(monkeypatch, tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(root))
    monkeypatch.setenv("APPDATA", str(root / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "xdg"))
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    return root


def _main(argv):
    from cg_code_graph.cli import main
    return main(argv)


def test_install_writes_rules_idempotently_and_keeps_user_content(home, tmp_path, capsys):
    proj = tmp_path / "recipe-site"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("# Team notes\n\nRun the linter before pushing.\n")
    assert _main(["install", "--host", "cursor", "--yes", "--dir", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "AGENTS.md (cg agent rules, insert)" in out and "cg.mdc (cg agent rules, insert)" in out
    agents = (proj / "AGENTS.md").read_text()
    mdc = (proj / ".cursor" / "rules" / "cg.mdc").read_text()
    assert agents.startswith("# Team notes\n\nRun the linter before pushing.\n\n" + IR.START)
    assert mdc.startswith("---\n") and "alwaysApply: true" in mdc
    for text in (agents, mdc):
        assert text.count(IR.START) == 1 and text.count(IR.END) == 1
        assert "`search`" in text and "`impact`" in text and "`snippet`" in text and "`graphs`" in text
    # user edits after the section survive; a re-run changes nothing
    (proj / "AGENTS.md").write_text(agents + "\n## Release\n\nTag from main.\n")
    agents2 = (proj / "AGENTS.md").read_text()
    assert _main(["install", "--host", "cursor", "--yes", "--dir", str(proj)]) == 0
    assert (proj / "AGENTS.md").read_text() == agents2
    assert (proj / ".cursor" / "rules" / "cg.mdc").read_text() == mdc
    # an outdated section is replaced in place
    stale = agents2.replace("- `snippet`", "- `old line`")
    (proj / "AGENTS.md").write_text(stale)
    assert _main(["install", "--host", "cursor", "--yes", "--dir", str(proj)]) == 0
    assert (proj / "AGENTS.md").read_text() == agents2
    # uninstall removes only the section, and the rule file it created
    assert _main(["uninstall", "--host", "cursor", "--yes", "--dir", str(proj)]) == 0
    left = (proj / "AGENTS.md").read_text()
    assert IR.START not in left and "Run the linter before pushing." in left and "Tag from main." in left
    assert not (proj / ".cursor").exists()


def test_existing_cursor_rule_gains_header_once(tmp_path):
    proj = tmp_path / "p"
    (proj / ".cursor" / "rules").mkdir(parents=True)
    (proj / ".cursor" / "rules" / "cg.mdc").write_text("Prefer small diffs.\n")
    plans = {p["path"].rsplit("/", 1)[-1]: p for p in IR.plans(proj)}
    new = plans["cg.mdc"]["new"]
    assert new.startswith(IR.MDC_HEADER) and "Prefer small diffs.\n" in new and new.count(IR.START) == 1
    (proj / ".cursor" / "rules" / "cg.mdc").write_text(new)
    assert {p["action"] for p in IR.plans(proj)} == {"insert", "noop"}   # AGENTS.md still new, cg.mdc current


def test_no_rules_and_global_skip(home, tmp_path, capsys):
    proj = tmp_path / "p"
    proj.mkdir()
    assert _main(["install", "--host", "cursor", "--yes", "--no-rules", "--dir", str(proj)]) == 0
    assert (proj / ".cursor" / "mcp.json").is_file()
    assert not (proj / "AGENTS.md").exists() and not (proj / ".cursor" / "rules").exists()
    db = tmp_path / "g.db"
    db.write_bytes(b"")
    assert _main(["install", "--global", "--host", "cursor", "--yes", "--db", str(db), "--dir", str(proj)]) == 0
    assert not (proj / "AGENTS.md").exists() and not (proj / ".cursor" / "rules").exists()
    assert not list(home.rglob("AGENTS.md")) and not list(home.rglob("cg.mdc"))
    # dry run previews the rules and writes nothing
    assert _main(["install", "--host", "cursor", "--dry-run", "--dir", str(proj)]) == 0
    out = capsys.readouterr().out
    assert IR.START in out and not (proj / "AGENTS.md").exists()

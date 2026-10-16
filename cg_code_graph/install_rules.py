"""Agent rules written by `cg install` (project scope, on by default, `--no-rules` skips): a short "use cg first"
section in AGENTS.md (Claude Code, Codex and other agents read it) and an alwaysApply Cursor rule at
.cursor/rules/cg.mdc. Only the `<!-- cg:start --> ... <!-- cg:end -->` section is ever written: a re-run replaces
it in place, content around it is kept, and `cg uninstall` removes it."""
from __future__ import annotations

import re
from pathlib import Path

START = "<!-- cg:start -->"
END = "<!-- cg:end -->"
AGENTS_FILE = "AGENTS.md"
CURSOR_FILE = ".cursor/rules/cg.mdc"
MDC_HEADER = "---\ndescription: Use the cg code graph before grep or opening whole files\nalwaysApply: true\n---\n"
_SECTION = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)


def body(db: str = "out/graph.db") -> str:
    return (
        "## Code graph (cg)\n\n"
        "This repo has a cg code graph (MCP server `cg`). Use it before grep or opening whole files:\n"
        "- `search` to find a symbol (file:line);\n"
        "- `impact` before editing a function (or `reaches` for a table, column or env key) to see what the change affects;\n"
        "- `snippet` to read one symbol instead of a whole file;\n"
        "- `graphs` if unsure which graph to query.\n\n"
        "Use normal search only for what cg reports as not covered (`coverage`). Without MCP: "
        f"`cg search|impact|snippet <symbol> --db {db}`.\n")


def section(db: str = "out/graph.db") -> str:
    return f"{START}\n{body(db)}{END}"


def _plan(path: Path, new: str | None, old: str, delete: bool = False) -> dict:
    if delete:
        return {"path": str(path), "kind": "text", "action": "remove", "old": old, "new": "", "delete_file": True}
    if new == old:
        return {"path": str(path), "kind": "text", "action": "noop", "old": old, "new": old, "current": True}
    action = "insert" if not _SECTION.search(old) else ("remove" if not _SECTION.search(new) else "update")
    return {"path": str(path), "kind": "text", "action": action, "old": old, "new": new}


def _with_section(old: str, sec: str) -> str:
    if _SECTION.search(old):
        return _SECTION.sub(lambda _m: sec, old, count=1)
    if not old:
        return sec + "\n"
    return old + ("" if old.endswith("\n\n") else "\n" if old.endswith("\n") else "\n\n") + sec + "\n"


def _without_section(old: str) -> str:
    m = _SECTION.search(old)
    if not m:
        return old
    s, e = m.start(), m.end()
    while e < len(old) and old[e] == "\n":
        e += 1
    while s > 0 and old[s - 1] == "\n":
        s -= 1
    head = old[:s]
    return (head + "\n" + old[e:]) if head and old[e:] else (head + "\n" if head else old[e:])


def plans(root: Path, remove: bool = False, db: str = "out/graph.db") -> list[dict]:
    """One text plan per rules file (the shape `cg agents` previews and `cg install` applies)."""
    out = []
    sec = section(db)
    agents = root / AGENTS_FILE
    old = agents.read_text(encoding="utf-8") if agents.exists() else ""
    if remove:
        rest = _without_section(old)
        out.append(_plan(agents, None, old, delete=True) if _SECTION.search(old) and not rest.strip() else _plan(agents, rest, old))
    else:
        out.append(_plan(agents, _with_section(old, sec), old))
    mdc = root / CURSOR_FILE
    old = mdc.read_text(encoding="utf-8") if mdc.exists() else ""
    if remove:
        rest = _without_section(old)
        if mdc.exists() and _SECTION.search(old) and rest.strip() in ("", MDC_HEADER.strip()):
            out.append(_plan(mdc, None, old, delete=True))
        else:
            out.append(_plan(mdc, rest, old))
    else:
        new = _with_section(old, sec)
        if not new.startswith("---\n"):
            new = MDC_HEADER + new        # an alwaysApply header, added once in front of whatever is there
        out.append(_plan(mdc, new, old))
    return [p for p in out if not (remove and p["action"] == "noop" and not p["old"])]

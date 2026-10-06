"""`cg agents`: opt-in install of the cg usage guidance (and the cg MCP server entry) into a
project's AGENTS.md / CLAUDE.md / Cursor rules. It only ever writes a clearly marked block and
never writes silently: every run previews the exact changes and asks before writing (--dry-run
previews without writing, --yes skips the prompt). Re-running replaces the block in place; remove
deletes it and restores the surrounding bytes. MCP changes touch only the `cg` server entry."""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from . import agent_rules as AR

# project guidance files `cg agents` can manage, by --target key
TARGETS = {"agents": "AGENTS.md", "claude": "CLAUDE.md", "cursor": ".cursor/rules/cg.mdc"}
DEFAULT_MCP_FILE = ".cursor/mcp.json"
MCP_ENTRY = {"command": "cg-mcp", "args": ["--db", "out/graph.db"]}

_CORE = re.compile(re.escape(AR.BEGIN_MARK) + r".*?" + re.escape(AR.END_MARK), re.S)


def _core_text(body: str | None = None) -> str:
    """The managed block without a trailing newline (BEGIN … END)."""
    return AR.block(body).rstrip("\n")


def plan_text(path: Path, remove: bool, body: str | None = None) -> dict:
    """Decide the new content of one guidance file. action: insert | update | remove | noop.
    Everything outside the marked block is preserved; a re-run replaces the block in place and
    remove restores the bytes the matching insert added (for newline-terminated files)."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    m = _CORE.search(old)
    core = _core_text(body)
    if remove:
        if not m:
            return {"path": str(path), "kind": "text", "action": "noop", "old": old, "new": old}
        s, e = m.start(), m.end()
        while e < len(old) and old[e] == "\n":          # drop the block's trailing newlines
            e += 1
        j = s
        while j > 0 and old[j - 1] == "\n":             # drop separator newlines before it
            j -= 1
        s = j + 1 if j > 0 else 0                        # but keep one newline after real content
        new = old[:s] + old[e:]
        return {"path": str(path), "kind": "text", "action": "remove", "old": old, "new": new}
    if m:
        new = old[:m.start()] + core + old[m.end():]
        if new == old:                                   # a re-run with nothing to change
            return {"path": str(path), "kind": "text", "action": "noop", "old": old, "new": old, "current": True}
        return {"path": str(path), "kind": "text", "action": "update", "old": old, "new": new}
    if not old:
        new = core + "\n"
    elif old.endswith("\n\n"):
        new = old + core + "\n"
    elif old.endswith("\n"):
        new = old + "\n" + core + "\n"
    else:
        new = old + "\n\n" + core + "\n"
    return {"path": str(path), "kind": "text", "action": "insert", "old": old, "new": new}


def plan_mcp(path: Path, remove: bool) -> dict:
    """Add / update / remove only the `cg` entry under mcpServers; every other entry and key is
    kept. Returns action noop when there is nothing to change, or error when the file is not JSON."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old.strip():
        try:
            data = json.loads(old)
        except ValueError as ex:
            return {"path": str(path), "kind": "mcp", "action": "error", "old": old, "new": old,
                    "error": f"not valid JSON ({ex}); left unchanged"}
    else:
        data = {}
    if not isinstance(data, dict):
        return {"path": str(path), "kind": "mcp", "action": "error", "old": old, "new": old,
                "error": "top level is not a JSON object; left unchanged"}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    if remove:
        if "cg" not in servers:
            return {"path": str(path), "kind": "mcp", "action": "noop", "old": old, "new": old}
        servers = {k: v for k, v in servers.items() if k != "cg"}
        action = "remove"
    else:
        action = "update" if "cg" in servers else "insert"
        servers = {**servers, "cg": MCP_ENTRY}
    data = {**data, "mcpServers": servers}
    new = json.dumps(data, indent=2) + "\n"
    return {"path": str(path), "kind": "mcp", "action": action, "old": old, "new": new}


def preview(plan: dict) -> str:
    """A unified diff of one plan (the exact proposed change), or a one-line note for noop/error."""
    if plan["action"] == "noop" and plan.get("current"):
        return f"{plan['path']}: no change (block up to date)"
    if plan["action"] == "noop":
        return f"{plan['path']}: no change (block not present)" if plan["kind"] == "text" \
            else f"{plan['path']}: no change (no cg entry)"
    if plan["action"] == "error":
        return f"{plan['path']}: {plan['error']}"
    diff = difflib.unified_diff(plan["old"].splitlines(True), plan["new"].splitlines(True),
                                fromfile=plan["path"], tofile=plan["path"], n=1)
    body = "".join(diff).rstrip("\n")
    return f"{plan['path']}  [{plan['action']}]\n{body}"


def build_plans(root: Path, targets: list[str], mcp: bool, mcp_file: str | None, remove: bool) -> list[dict]:
    # Primary is the first selected target in TARGETS order; it gets the full block.
    # Every other selected file gets a pointer inside the same markers.
    ordered = [t for t in TARGETS if t in targets]
    primary = ordered[0] if ordered else None
    plans = []
    for t in targets:
        body = None if t == primary else AR.pointer_text(TARGETS[primary], t)
        plans.append(plan_text(root / TARGETS[t], remove, body))
    if mcp:
        plans.append(plan_mcp(Path(mcp_file) if mcp_file else root / DEFAULT_MCP_FILE, remove))
    return plans


def run(action: str, root: str = ".", targets: list[str] | None = None, all_targets: bool = False,
        mcp: bool = False, mcp_file: str | None = None, dry_run: bool = False, assume_yes: bool = False,
        input_fn=input, out=print) -> int:
    """Entry point for the CLI. action: install | update | remove | show. Returns a process exit code."""
    rootp = Path(root)
    remove = action == "remove"
    if all_targets:
        sel = list(TARGETS)
    elif targets:
        sel = targets
    else:                                   # default: the guidance files that already exist
        sel = [t for t, f in TARGETS.items() if (rootp / f).exists()]
    if not sel and not mcp:
        out("nothing selected: pass --target agents|claude|cursor (repeatable), --all, or --mcp "
            "(none of AGENTS.md / CLAUDE.md / .cursor/rules/cg.mdc exist here)")
        return 2
    plans = build_plans(rootp, sel, mcp, mcp_file, remove)
    for p in plans:
        out(preview(p))
    if any(p["action"] == "error" for p in plans):
        return 2
    changing = [p for p in plans if p["action"] not in ("noop",)]
    if action == "show" or dry_run:
        out("(dry run: nothing written)" if dry_run else "(show: nothing written)")
        return 0
    if not changing:
        out("nothing to do")
        return 0
    if not assume_yes:
        resp = input_fn(f"apply {len(changing)} change(s)? [y/N] ")
        if (resp or "").strip().lower() not in ("y", "yes"):
            out("aborted; nothing written")
            return 1
    for p in changing:
        fp = Path(p["path"])
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(p["new"], encoding="utf-8")
        out(f"wrote {p['path']} ({p['action']})")
    return 0

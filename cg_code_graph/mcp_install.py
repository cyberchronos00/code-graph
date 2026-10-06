"""`cg install` / `cg uninstall`: register the cg MCP server (key `cg`) in a host config.

Edits affect only the `cg` member (and, on uninstall, a cg-owned legacy `code-graph` member).
Install then uninstall is byte-identical, with two known edges: a file that was already `{}`
or `{"mcpServers": {}}` is deleted or collapsed by uninstall, and a TOML file without a final
newline gains one. A trailing `// comment` on the previous member can end up after the new
member; the round trip is still identical.

Doctor reads the same paths and never writes. Claude Code's user scope is read from
`~/.claude.json` and changed only by the `claude` CLI.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tomllib
from pathlib import Path

HOSTS = ("cursor", "claude", "claude-desktop", "vscode", "windsurf", "codex", "gemini", "zed")
_EMPTY_PARENTS = {".cursor", ".vscode", ".codex", ".gemini", ".zed"}
_CONTAINERS = {"mcpServers", "servers", "context_servers"}

# Cursor: https://cursor.com/docs/context/mcp
#   project <root>/.cursor/mcp.json, global ~/.cursor/mcp.json, mcpServers, type stdio
# Claude Code: https://code.claude.com/docs/en/mcp
#   project <root>/.mcp.json, global `claude mcp add-json`, mcpServers, type stdio
# Claude Desktop: https://modelcontextprotocol.io/docs/develop/build-client (quickstart)
#   global only; macOS ~/Library/Application Support/Claude/claude_desktop_config.json;
#   Windows %APPDATA%\\Claude\\claude_desktop_config.json; no Linux build. mcpServers, command+args
# VS Code: https://code.visualstudio.com/docs/copilot/reference/mcp-configuration
#   project <root>/.vscode/mcp.json container `servers`; global $COPILOT_HOME/mcp-config.json
#   else ~/.copilot/mcp-config.json container mcpServers; type stdio
# Windsurf: https://docs.windsurf.com/windsurf/cascade/mcp
#   global only (legacy Cascade); $XDG_CONFIG_HOME/devin/mcp_config.json else ~/.config/devin/…;
#   Windows %APPDATA%\\devin\\mcp_config.json; mcpServers, command+args
# Codex: https://developers.openai.com/codex/mcp
#   project <root>/.codex/config.toml (trusted projects only), global ~/.codex/config.toml, [mcp_servers.cg]
# Gemini CLI: https://geminicli.com/docs/tools/mcp-server
#   project <root>/.gemini/settings.json, global ~/.gemini/settings.json, mcpServers, command+args
# Zed: https://zed.dev/docs/ai/mcp
#   project <root>/.zed/settings.json, global ~/.config/zed/settings.json (macOS and Linux;
#   Windows not verified), context_servers, command+args+env


class InstallError(Exception):
    """A host or scope cg will not edit. The CLI exits 2."""


def _child(base, *parts):
    """Join path parts. A monkeypatched ``os.name == 'nt'`` on POSIX cannot build a real WindowsPath."""
    text = os.path.join(str(base), *(str(p) for p in parts)) if parts else str(base)
    try:
        path = Path(text)
        if os.name == "nt":
            path.parent
        return path
    except NotImplementedError:
        from pathlib import PurePosixPath
        return PurePosixPath(text)


def _home() -> Path:
    if os.name == "nt":
        val = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    else:
        val = os.environ.get("HOME")
    return _child(val) if val else Path.home()


def _appdata() -> Path:
    val = os.environ.get("APPDATA")
    return _child(val) if val else _child(_home(), "AppData", "Roaming")


def _xdg_config() -> Path:
    val = os.environ.get("XDG_CONFIG_HOME")
    return _child(val) if val else _child(_home(), ".config")


_FALLBACK_NOTE = (
    "note: cg-mcp was not found beside Python, in the scripts directory, or on PATH; "
    "using python -m cg_code_graph.mcp_server (that module path changes when the package is renamed)"
)


def _script_name() -> str:
    return "cg-mcp.exe" if os.name == "nt" else "cg-mcp"


def _scripts_dirs() -> list[str]:
    """sysconfig script directories: the active scheme, then the user scheme (``~/.local/bin``)."""
    dirs: list[str] = []
    schemes: list[str | None] = [None, "nt_user" if os.name == "nt" else "posix_user"]
    for scheme in schemes:
        try:
            path = sysconfig.get_path("scripts") if scheme is None else sysconfig.get_path("scripts", scheme)
        except (KeyError, OSError, ValueError):
            path = None
        if path and path not in dirs:
            dirs.append(path)
    return dirs


def server_command() -> list[str]:
    """Absolute ``cg-mcp``, else ``python -m cg_code_graph.mcp_server``.

    The first file that exists wins:
    1. beside ``sys.executable`` (venv, pipx, or uv tool);
    2. the sysconfig scripts directory, then the user-scheme scripts directory;
    3. ``shutil.which("cg-mcp")``;
    4. beside the running ``cg`` (``sys.argv[0]``).

    The module fallback is last. The import package is renamed later, so an absolute
    ``cg-mcp`` keeps working. ``run`` prints a note when the fallback is used.
    """
    name = _script_name()
    candidates: list[str] = [os.path.join(os.path.dirname(os.path.abspath(sys.executable)), name)]
    candidates.extend(os.path.join(d, name) for d in _scripts_dirs())
    found = shutil.which("cg-mcp")
    if found:
        candidates.append(found)
    argv0 = sys.argv[0] if sys.argv else ""
    if os.path.basename(argv0).lower() in ("cg", "cg.exe"):
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(argv0)), name))
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        try:
            exists = os.path.isfile(cand)
        except OSError:
            exists = False
        if exists:
            return [os.path.abspath(cand)]
    return [os.path.abspath(sys.executable), "-m", "cg_code_graph.mcp_server"]


def uses_module_fallback(cmd: list[str] | None = None) -> bool:
    cmd = server_command() if cmd is None else cmd
    return len(cmd) >= 3 and cmd[1] == "-m" and cmd[2] == "cg_code_graph.mcp_server"


def container_key(host: str, scope: str) -> str:
    if host == "zed":
        return "context_servers"
    if host == "vscode" and scope == "project":
        return "servers"
    return "mcpServers"


def has_type(host: str) -> bool:
    return host in ("cursor", "claude", "vscode")


def config_path(host: str, scope: str, root: Path) -> Path:
    """Filesystem path for a host and scope. Claude Code global has no file cg edits."""
    root = Path(root)
    if host == "claude" and scope == "global":
        raise InstallError("Claude Code global scope is changed with the claude CLI, not a file")
    if host == "claude-desktop" and scope == "project":
        raise InstallError("Claude Desktop is global only")
    if host == "windsurf" and scope == "project":
        raise InstallError("Windsurf is global only")
    if host == "claude-desktop" and scope == "global":
        if os.name == "nt":
            return _child(_appdata(), "Claude", "claude_desktop_config.json")
        if sys.platform == "darwin":
            return _child(_home(), "Library", "Application Support", "Claude", "claude_desktop_config.json")
        raise InstallError("Claude Desktop has no Linux build")
    if host == "zed" and scope == "global" and os.name == "nt":
        raise InstallError("Zed's global MCP config on Windows is not verified")
    if scope == "project":
        rel = {
            "cursor": (".cursor", "mcp.json"),
            "claude": (".mcp.json",),
            "vscode": (".vscode", "mcp.json"),
            "codex": (".codex", "config.toml"),
            "gemini": (".gemini", "settings.json"),
            "zed": (".zed", "settings.json"),
        }[host]
        return _child(root, *rel)
    if host == "cursor":
        return _child(_home(), ".cursor", "mcp.json")
    if host == "vscode":
        base = os.environ.get("COPILOT_HOME")
        return _child(base, "mcp-config.json") if base else _child(_home(), ".copilot", "mcp-config.json")
    if host == "windsurf":
        if os.name == "nt":
            return _child(_appdata(), "devin", "mcp_config.json")
        return _child(_xdg_config(), "devin", "mcp_config.json")
    if host == "codex":
        return _child(_home(), ".codex", "config.toml")
    if host == "gemini":
        return _child(_home(), ".gemini", "settings.json")
    if host == "zed":
        return _child(_home(), ".config", "zed", "settings.json")
    raise InstallError(f"unknown host {host}")


def location_label(host: str, scope: str, root: Path) -> str:
    if host == "claude" and scope == "global":
        return "PATH"
    path = config_path(host, scope, root)
    parent = path.parent
    if scope == "project":
        try:
            rel = parent.resolve().relative_to(Path(root).resolve())
        except ValueError:
            rel = parent
        text = "." if str(rel) == "." else str(rel)
        return path.name if text == "." else text + "/"
    try:
        rel = parent.resolve().relative_to(_home().resolve())
    except ValueError:
        return str(parent)
    return "~/" + rel.as_posix() + "/"


def build_entry(host: str, scope: str, root: Path, db: Path | None, tools: str | None, portable: bool) -> dict:
    if portable:
        command, args = "cg-mcp", ["--db", "out/graph.db"]
    else:
        cmd = server_command()
        command, args = cmd[0], list(cmd[1:])
        args += ["--db", str(Path(db).resolve())]
        if scope == "project":
            args += ["--root", str(Path(root).resolve())]
    if tools:
        args += ["--tools", tools]
    if host == "codex":
        return {"command": command, "args": args}
    entry: dict = {}
    if has_type(host):
        entry["type"] = "stdio"
    entry["command"] = command
    entry["args"] = args
    if host == "zed":
        entry["env"] = {}
    return entry


_OLD_MCP = "codegraph" + ".mcp_server"
_NEW_MCP = "cg_code_graph.mcp_server"


def _removed_module(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    blob = str(entry.get("command") or "")
    blob += " " + " ".join(str(a) for a in (entry.get("args") or ()))
    return _OLD_MCP in blob


def is_ours(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    cmd = str(entry.get("command") or "")
    base = cmd.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base in ("cg-mcp", "cg-mcp.exe"):
        return True
    args = entry.get("args") or ()
    return any(_NEW_MCP in str(a) or _OLD_MCP in str(a) for a in args)


# --- JSON / JSONC text editor (byte-preserving) ---------------------------------------------

def _skip_ws(s, i):
    n = len(s)
    while i < n:
        if s[i] in " \t\r\n":
            i += 1
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            break
    return i


def _string_end(s, i):
    i += 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            return i + 1
        i += 1
    raise ValueError("unterminated string")


def _value_end(s, i):
    i = _skip_ws(s, i)
    c = s[i]
    if c == '"':
        return _string_end(s, i)
    if c in "{[":
        close = "}" if c == "{" else "]"
        i += 1
        while True:
            i = _skip_ws(s, i)
            if s[i] == close:
                return i + 1
            if c == "{":
                i = _string_end(s, _skip_ws(s, i))
                i = _skip_ws(s, i)
                assert s[i] == ":"
                i += 1
            i = _value_end(s, i)
            i = _skip_ws(s, i)
            if s[i] == ",":
                i += 1
    m = re.compile(r"-?[0-9.eE+-]+|true|false|null").match(s, i)
    if not m:
        raise ValueError(f"bad value at {i}")
    return m.end()


def members(s, obj_start):
    """[(key, key_start, value_start, value_end)] of the object at obj_start, plus the index of '}'."""
    out, i = [], obj_start + 1
    while True:
        i = _skip_ws(s, i)
        if i >= len(s):
            raise ValueError("unterminated object")
        if s[i] == "}":
            return out, i
        ks = i
        ke = _string_end(s, i)
        key = json.loads(s[ks:ke])
        i = _skip_ws(s, ke)
        if i >= len(s) or s[i] != ":":
            raise ValueError("expected ':'")
        vs = _skip_ws(s, i + 1)
        ve = _value_end(s, vs)
        out.append((key, ks, vs, ve))
        i = _skip_ws(s, ve)
        if i < len(s) and s[i] == ",":
            i += 1


def _indent_of(s, pos):
    ls = s.rfind("\n", 0, pos) + 1
    return re.match(r"[ \t]*", s[ls:]).group(0)


def _render(value, indent, unit, nl):
    txt = json.dumps(value, indent=unit if unit else None, ensure_ascii=False)
    return txt.replace("\n", nl + indent) if unit else txt


def set_member(s, path, key, value):
    """Insert or replace s[path...][key] = value with minimal text change."""
    nl = "\r\n" if "\r\n" in s else "\n"
    if not s.strip():
        return json.dumps(_nest(path, {key: value}), indent=2, ensure_ascii=False) + "\n"
    obj = _skip_ws(s, 0)
    if obj >= len(s) or s[obj] != "{":
        raise ValueError("top level is not a JSON object")
    for depth, k in enumerate(path):
        ms, close = members(s, obj)
        hit = [m for m in ms if m[0] == k]
        if not hit:
            return _insert(s, obj, ms, close, k, _nest(path[depth + 1:], {key: value}), nl)
        obj = hit[0][2]
        if s[obj] != "{":
            raise ValueError(f"{k} is not an object")
    ms, close = members(s, obj)
    hit = [m for m in ms if m[0] == key]
    if hit:
        _, ks, vs, ve = hit[0]
        unit = _unit(s, ms, obj)
        new = _render(value, _indent_of(s, ks), unit, nl)
        return s[:vs] + new + s[ve:]
    return _insert(s, obj, ms, close, key, value, nl)


def _nest(path, leaf):
    for k in reversed(path):
        leaf = {k: leaf}
    return leaf


def _unit(s, ms, obj):
    if ms and "\n" in s[obj:ms[0][1]]:
        inner = _indent_of(s, ms[0][1])
        outer = _indent_of(s, obj)
        return inner[len(outer):] or "  "
    return "  " if not ms else None


def _insert(s, obj, ms, close, key, value, nl):
    unit = _unit(s, ms, obj)
    if ms:
        ind = _indent_of(s, ms[-1][1]) if unit else ""
        sep = "," + (nl + ind if unit else " ")
        at = ms[-1][3]
    else:
        ind = _indent_of(s, obj) + (unit or "")
        sep = nl + ind if unit else ""
        at = obj + 1
    text = sep + json.dumps(key, ensure_ascii=False) + ": " + _render(value, ind, unit, nl)
    if not ms and unit:
        text += nl + _indent_of(s, obj)
    return s[:at] + text + s[at:]


def remove_member(s, path, key):
    """Inverse of set_member. A container left empty is removed too."""
    if not s.strip():
        return s
    obj = _skip_ws(s, 0)
    if obj >= len(s) or s[obj] != "{":
        raise ValueError("top level is not a JSON object")
    for k in path:
        hit = [m for m in members(s, obj)[0] if m[0] == k]
        if not hit:
            return s
        obj = hit[0][2]
        if s[obj] != "{":
            raise ValueError(f"{k} is not an object")
    ms, close = members(s, obj)
    idx = [i for i, m in enumerate(ms) if m[0] == key]
    if not idx:
        return s
    i = idx[0]
    if len(ms) == 1:
        if path:
            return remove_member(s, path[:-1], path[-1])
        return s[:obj + 1] + s[close:]
    if i > 0:
        return s[:ms[i - 1][3]] + s[ms[i][3]:]
    return s[:ms[0][1]] + s[ms[1][1]:]


def strip_jsonc(s: str) -> str:
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i] == '"':
            j = _string_end(s, i)
            out.append(s[i:j])
            i = j
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


# --- TOML text editor -----------------------------------------------------------------------

_HDR = re.compile(r"^[ \t]*\[\[?[ \t]*([^\]]+?)[ \t]*\]\]?[ \t]*(#.*)?$", re.M)


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _toml_block(entry: dict) -> str:
    lines = ["[mcp_servers.cg]", f"command = {_q(entry['command'])}",
             "args = [" + ", ".join(_q(a) for a in entry["args"]) + "]"]
    return "\n".join(lines) + "\n"


def _span(s: str, table: str = "mcp_servers.cg"):
    hs = list(_HDR.finditer(s))
    for i, m in enumerate(hs):
        if m.group(1).replace(" ", "") == table:
            end = len(s)
            for n in hs[i + 1:]:
                if not n.group(1).replace(" ", "").startswith(table + "."):
                    end = n.start()
                    break
            return m.start(), end
    return None


def set_cg(s: str, entry: dict) -> str:
    tomllib.loads(s or "")
    sp = _span(s)
    block = _toml_block(entry)
    if sp:
        a, e = sp
        tail = s[a:e]
        keep = tail[len(tail.rstrip("\n")):]
        return s[:a] + block.rstrip("\n") + (keep if e < len(s) else "\n") + s[e:]
    data = tomllib.loads(s or "")
    if "cg" in (data.get("mcp_servers") or {}):
        raise ValueError("cg is defined inline; edit by hand")
    sep = "" if not s else ("\n" if s.endswith("\n") else "\n\n")
    return s + sep + block


def remove_table(s: str, table: str) -> str:
    sp = _span(s, table)
    if not sp:
        return s
    a, e = sp
    if e == len(s):
        head = s[:a]
        return head[:-1] if head.endswith("\n\n") else head
    return s[:a] + s[e:]


def remove_cg(s: str) -> str:
    return remove_table(s, "mcp_servers.cg")


# --- plans ----------------------------------------------------------------------------------

def _parse(text: str, kind: str):
    if kind == "toml":
        return tomllib.loads(text or "")
    if not text.strip():
        return {}
    return json.loads(strip_jsonc(text))


def _get(data, path, key):
    cur = data
    for k in path:
        if not isinstance(cur, dict) or not isinstance(cur.get(k), dict):
            return None
        cur = cur[k]
    if not isinstance(cur, dict):
        return None
    return cur.get(key)


def _others(data, path, skip: set[str]):
    cur = data
    for k in path:
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(k)
    if not isinstance(cur, dict):
        return {}
    return {k: v for k, v in cur.items() if k not in skip}


def _should_delete(text: str, kind: str) -> bool:
    if kind == "toml":
        try:
            data = tomllib.loads(text or "")
        except tomllib.TOMLDecodeError:
            return False
        return data == {} or data == {"mcp_servers": {}}
    if not text.strip():
        return True
    try:
        data = json.loads(strip_jsonc(text))
    except json.JSONDecodeError:
        return False
    if data == {}:
        return True
    if isinstance(data, dict) and data and set(data) <= _CONTAINERS and all(v == {} for v in data.values()):
        return True
    return False


def _legacy_keys(data, path, kind: str) -> list[str]:
    if kind == "toml":
        entry = (data.get("mcp_servers") or {}).get("code-graph") if isinstance(data, dict) else None
        return ["code-graph"] if is_ours(entry) else []
    entry = _get(data, path, "code-graph")
    return ["code-graph"] if is_ours(entry) else []


def plan_text(old: str, *, kind: str, path_keys: list[str], entry: dict | None, remove: bool,
              delete_empty: bool) -> tuple[str, str, str | None]:
    """Return (action, new_text, error). Does not look at the filesystem."""
    try:
        data = _parse(old, kind) if old.strip() or kind == "toml" else {}
        if kind == "json" and old.strip() and not isinstance(data, dict):
            raise ValueError("top level is not a JSON object")
    except (ValueError, AssertionError, IndexError, tomllib.TOMLDecodeError) as ex:
        return "error", old, f"not valid {'TOML' if kind == 'toml' else 'JSON'} ({ex}); left unchanged"
    if remove:
        legacy = _legacy_keys(data, path_keys, kind)
        present = _get(data, path_keys, "cg") is not None or bool(legacy)
        if not present:
            if delete_empty and _should_delete(old, kind) and old.strip():
                return "remove", "", None
            return "noop", old, None
        try:
            new = old
            if kind == "toml":
                new = remove_cg(new)
                if legacy:
                    new = remove_table(new, "mcp_servers.code-graph")
            else:
                new = remove_member(new, path_keys, "cg")
                for name in legacy:
                    new = remove_member(new, path_keys, name)
            _parse(new, kind) if new.strip() else {}
        except (ValueError, AssertionError, IndexError, tomllib.TOMLDecodeError) as ex:
            return "error", old, f"edit failed ({ex}); left unchanged"
        if delete_empty and _should_delete(new, kind):
            return "remove", "", None
        return ("noop", old, None) if new == old else ("remove", new, None)
    current = _get(data, path_keys, "cg")
    if current == entry:
        return "noop", old, None
    try:
        new = set_cg(old, entry) if kind == "toml" else set_member(old, path_keys, "cg", entry)
        after = _parse(new, kind)
    except (ValueError, AssertionError, IndexError, tomllib.TOMLDecodeError) as ex:
        return "error", old, f"not valid {'TOML' if kind == 'toml' else 'JSON'} ({ex}); left unchanged"
    skip = {"cg"}
    if _others(data, path_keys, skip) != _others(after, path_keys, skip):
        return "error", old, "edit would change another server; left unchanged"
    if not old.strip():
        action = "insert"
    else:
        action = "update" if current is not None else "insert"
    if new == old:
        return "noop", old, None
    return action, new, None


def plan_file(path: Path, *, kind: str, path_keys: list[str], entry: dict | None, remove: bool,
              delete_empty: bool, host: str | None = None, scope: str | None = None) -> dict:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    action, new, err = plan_text(old, kind=kind, path_keys=path_keys, entry=entry, remove=remove,
                                 delete_empty=delete_empty)
    plan = {"path": str(path), "kind": "mcp", "action": action, "old": old, "new": new,
            "host": host, "scope": scope}
    if action == "noop" and not remove and entry is not None and old.strip():
        plan["registered"] = True
    if action == "noop" and not old.strip() and remove:
        plan["registered"] = False
    if err:
        plan["error"] = err
    if action == "remove" and new == "" and (old.strip() or path.exists()):
        plan["delete_file"] = True
    if not remove and old.strip():
        try:
            data = _parse(old, kind)
        except (ValueError, tomllib.TOMLDecodeError):
            data = None
        if data is not None and _legacy_keys(data, path_keys, kind):
            plan["legacy"] = True
    return plan


def _kind(host: str) -> str:
    return "toml" if host == "codex" else "json"


def iter_plans(hosts: list[str], *, scope: str, root: Path, db: Path | None, tools: str | None,
               portable: bool, remove: bool) -> list[dict]:
    plans = []
    for host in hosts:
        if host == "claude" and scope == "global":
            plans.append(_claude_plan(root, db, tools, portable, remove))
            continue
        try:
            path = config_path(host, scope, root)
        except InstallError as ex:
            plans.append({"path": host, "kind": "mcp", "action": "error", "old": "", "new": "",
                          "host": host, "scope": scope, "error": str(ex)})
            continue
        entry = None
        if not remove:
            entry = build_entry(host, scope, root, db, tools, portable)
        # Codex stores the entry under [mcp_servers.cg], not the JSON key mcpServers.
        keys = ["mcp_servers"] if host == "codex" else [container_key(host, scope)]
        plan = plan_file(path, kind=_kind(host), path_keys=keys, entry=entry,
                         remove=remove, delete_empty=True, host=host, scope=scope)
        if host == "codex" and scope == "project":
            plan["codex_trust"] = True
        plans.append(plan)
    return plans


def _claude_user_file() -> Path:
    return _home() / ".claude.json"


def _claude_entry(remove: bool, root: Path, db, tools, portable):
    if remove:
        return None
    return build_entry("claude", "global", root, db, tools, portable)


def _read_claude_cg():
    path = _claude_user_file()
    if not path.is_file():
        return None, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, True
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if isinstance(servers, dict) and "cg" in servers:
        return servers["cg"], True
    return None, True


def _claude_plan(root, db, tools, portable, remove) -> dict:
    entry = _claude_entry(remove, root, db, tools, portable)
    current, present = _read_claude_cg()
    path = str(_claude_user_file())
    base = {"path": path, "kind": "mcp", "action": "noop", "old": "", "new": "",
            "host": "claude", "scope": "global", "claude_cli": True}
    if remove:
        if current is None:
            base["registered"] = False
            return base
        base["action"] = "remove"
        base["commands"] = ["claude", "mcp", "remove", "cg", "--scope", "user"]
        return base
    if current == entry:
        base["registered"] = True
        return base
    payload = json.dumps(entry, ensure_ascii=False)
    base["action"] = "update" if current is not None else "insert"
    base["commands"] = [
        ["claude", "mcp", "remove", "cg", "--scope", "user"],
        ["claude", "mcp", "add-json", "cg", payload, "--scope", "user"],
    ]
    base["new"] = "claude mcp remove cg --scope user\nclaude mcp add-json cg " + shlex.quote(payload) + " --scope user\n"
    return base


def detected_hosts(scope: str, root: Path) -> list[str]:
    found = []
    for host in HOSTS:
        if host == "claude" and scope == "global":
            if shutil.which("claude"):
                found.append(host)
            continue
        try:
            path = config_path(host, scope, root)
        except InstallError:
            continue
        if path.exists():
            found.append(host)
            continue
        parent = path.parent
        if scope == "project" and parent.resolve() == Path(root).resolve():
            continue
        if scope == "global" and parent.resolve() == _home().resolve():
            continue
        if parent.exists():
            found.append(host)
    return found


def _preview(plan: dict) -> str:
    from . import agents as AG
    if plan.get("claude_cli") and plan["action"] in ("insert", "update", "remove"):
        if plan["action"] == "remove":
            return "claude mcp remove cg --scope user"
        return plan.get("new") or ""
    return AG.preview(plan)


def _apply_file(plan: dict) -> None:
    path = Path(plan["path"])
    if plan.get("delete_file"):
        if path.exists():
            path.unlink()
        parent = path.parent
        if parent.name in _EMPTY_PARENTS and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan["new"], encoding="utf-8")


def _run_claude(plan: dict, dry_run: bool, out) -> str | None:
    """Run or print the claude CLI. Returns an error string, or None."""
    if plan["action"] in ("noop", "error"):
        return plan.get("error")
    if dry_run:
        return None
    exe = shutil.which("claude")
    if not exe:
        shown = plan.get("new") or "claude mcp remove cg --scope user"
        return f"claude is not on PATH; {shown.strip()}"
    commands = plan.get("commands") or []
    if commands and isinstance(commands[0], str):
        commands = [commands]
    for i, cmd in enumerate(commands):
        proc = subprocess.run([exe, *cmd[1:]], check=False)
        if proc.returncode != 0 and not (i == 0 and cmd[1:3] == ["mcp", "remove"]):
            return f"claude exited {proc.returncode}"
    return None


def run(hosts: list[str] | None, *, scope: str, root: str = ".", db: str | None = None,
        tools: str | None = None, portable: bool = False, remove: bool = False,
        dry_run: bool = False, assume_yes: bool = False, input_fn=input, out=print) -> int:
    rootp = Path(root).resolve()
    if scope == "global" and not db and not remove:
        out("a global entry serves one graph; pass --db (or use --project)")
        return 2
    if not hosts:
        hosts = detected_hosts(scope, rootp)
        if not hosts:
            out("no MCP host detected; pass --host " + " | ".join((*HOSTS, "all")))
            return 2
        labels = ", ".join(f"{h} ({location_label(h, scope, rootp)})" for h in hosts)
        out(f"detected: {labels}")
    expanded: list[str] = []
    for h in hosts:
        if h == "all":
            for name in HOSTS:
                if name not in expanded:
                    expanded.append(name)
        elif h not in expanded:
            expanded.append(h)
    dbp = Path(db).expanduser().resolve() if db else (rootp / "out" / "graph.db")
    if scope == "global" and remove and not db:
        dbp = None
    plans = iter_plans(expanded, scope=scope, root=rootp, db=dbp, tools=tools, portable=portable, remove=remove)
    if not portable and not remove and uses_module_fallback():
        out(_FALLBACK_NOTE)
    for plan in plans:
        if plan.get("legacy"):
            out(f'note: an older "code-graph" entry also starts cg; remove it with cg uninstall --host {plan["host"]}')
        if plan.get("codex_trust") and plan["action"] in ("insert", "update"):
            out("note: Codex reads .codex/config.toml only in trusted projects")
        text = _preview(plan)
        if text:
            out(text.rstrip("\n"))
    if any(p["action"] == "error" for p in plans):
        return 2
    changing = [p for p in plans if p["action"] not in ("noop",)]
    if any(p.get("claude_cli") for p in changing) and not shutil.which("claude"):
        out("claude is not on PATH")
        return 2
    if dry_run:
        out("(dry run: nothing written)")
        return 0
    if not changing:
        out("nothing to do")
        return 0
    if not assume_yes:
        resp = input_fn(f"apply {len(changing)} change(s)? [y/N] ")
        if (resp or "").strip().lower() not in ("y", "yes"):
            out("aborted; nothing written")
            return 1
    for plan in changing:
        if plan.get("claude_cli"):
            err = _run_claude(plan, dry_run=False, out=out)
            if err:
                out(err)
                return 2
            out(f"updated claude ({plan['action']})")
            continue
        _apply_file(plan)
        if plan.get("delete_file"):
            out(f"removed {plan['path']}")
        else:
            out(f"wrote {plan['path']} ({plan['action']})")
    return 0


# --- doctor (read-only) ---------------------------------------------------------------------

def _db_from_args(args) -> str | None:
    if not isinstance(args, (list, tuple)):
        return None
    for i, a in enumerate(args):
        if a == "--db" and i + 1 < len(args):
            return str(args[i + 1])
    return None


def _command_ok(command: str) -> bool:
    if not command:
        return False
    path = Path(command)
    if path.is_absolute() or "/" in command or "\\" in command:
        return path.is_file()
    return shutil.which(command) is not None


def _item(host, scope, path, key, entry, root: Path) -> dict:
    command = str((entry or {}).get("command") or "")
    args = list((entry or {}).get("args") or [])
    db = _db_from_args(args)
    problem = None
    if _removed_module(entry):
        problem = "uses the removed module name; re-run cg install"
    elif not _command_ok(command):
        problem = "command not found"
    else:
        db_path = None if db is None else Path(db)
        if db_path is not None and not db_path.is_absolute():
            db_path = (root / db_path).resolve()
        if db_path is None or not db_path.exists():
            problem = "db not found"
    try:
        shown = Path(path)
        if shown.is_absolute():
            try:
                shown = shown.resolve().relative_to(root.resolve())
            except ValueError:
                try:
                    shown = "~" / shown.resolve().relative_to(_home().resolve())
                except ValueError:
                    pass
        path_s = shown.as_posix() if isinstance(shown, Path) else str(path)
    except OSError:
        path_s = str(path)
    return {"host": host, "scope": scope, "path": path_s, "key": key, "command": command,
            "args": args, "db": db, "ok": problem is None, "problem": problem}


def _json_items(host, scope, path: Path, root: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as ex:
        return [{"host": host, "scope": scope, "path": str(path), "key": None, "command": "",
                 "db": None, "ok": False, "problem": "invalid JSON", "error": str(ex)}]
    try:
        data = json.loads(strip_jsonc(text))
    except (json.JSONDecodeError, ValueError):
        return [{"host": host, "scope": scope, "path": _rel(path, root), "key": None, "command": "",
                 "db": None, "ok": False, "problem": "invalid JSON"}]
    if not isinstance(data, dict):
        return []
    key = container_key(host, scope)
    block = data.get(key)
    if not isinstance(block, dict):
        return []
    out = []
    if isinstance(block.get("cg"), dict):
        out.append(_item(host, scope, path, "cg", block["cg"], root))
    if is_ours(block.get("code-graph")):
        out.append(_item(host, scope, path, "code-graph", block["code-graph"], root))
    return out


def _toml_items(host, scope, path: Path, root: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError):
        return [{"host": host, "scope": scope, "path": _rel(path, root), "key": None, "command": "",
                 "db": None, "ok": False, "problem": "invalid JSON"}]
    servers = data.get("mcp_servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return []
    out = []
    if isinstance(servers.get("cg"), dict):
        out.append(_item(host, scope, path, "cg", servers["cg"], root))
    if is_ours(servers.get("code-graph")):
        out.append(_item(host, scope, path, "code-graph", servers["code-graph"], root))
    return out


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        pass
    try:
        return ("~" / path.resolve().relative_to(_home().resolve())).as_posix()
    except ValueError:
        return str(path)


def _claude_json_items(root: Path) -> list[dict]:
    path = _claude_user_file()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [{"host": "claude", "scope": "user", "path": _rel(path, root), "key": None,
                 "command": "", "db": None, "ok": False, "problem": "invalid JSON"}]
    if not isinstance(data, dict):
        return []
    out = []
    top = data.get("mcpServers")
    if isinstance(top, dict):
        if isinstance(top.get("cg"), dict):
            out.append(_item("claude", "user", path, "cg", top["cg"], root))
        if is_ours(top.get("code-graph")):
            out.append(_item("claude", "user", path, "code-graph", top["code-graph"], root))
    projects = data.get("projects")
    if isinstance(projects, dict):
        for key in (str(root), str(root) + os.sep):
            proj = projects.get(key)
            if not isinstance(proj, dict):
                continue
            block = proj.get("mcpServers")
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("cg"), dict):
                out.append(_item("claude", "project", path, "cg", block["cg"], root))
            if is_ours(block.get("code-graph")):
                out.append(_item("claude", "project", path, "code-graph", block["code-graph"], root))
            break
    return out


def registrations(root: Path) -> list[dict]:
    """Host entries that start cg. Reads files and ~/.claude.json; never writes and never runs claude."""
    root = Path(root).resolve()
    out: list[dict] = []
    for scope in ("project", "global"):
        for host in HOSTS:
            if host == "claude" and scope == "global":
                continue
            try:
                path = config_path(host, scope, root)
            except InstallError:
                continue
            if not path.is_file():
                continue
            if host == "codex":
                out.extend(_toml_items(host, scope, path, root))
            else:
                out.extend(_json_items(host, scope, path, root))
    out.extend(_claude_json_items(root))
    return out


def render_mcp(items: list[dict]) -> list[str]:
    if not items:
        return ["mcp: cg is not registered in any MCP host (cg install)"]
    lines = ["mcp:"]
    for it in items:
        args = " ".join(str(a) for a in it.get("args") or [])
        cmd = it.get("command") or ""
        body = f"{cmd} {args}".rstrip()
        prob = f"  ({it['problem']})" if it.get("problem") else ""
        lines.append(f"  {it['host']} ({it['scope']}) {it['path']}: {it['key']} -> {body}{prob}")
    return lines

"""Per-platform C / C++ source lists from CMake (#111): `if(WIN32) list(APPEND srcs src/win/fs.c)`,
`if(CMAKE_SYSTEM_NAME MATCHES "Android|Linux") ...`, `target_sources(...)` under a condition. A source file that every
CMakeLists.txt mentions only under platform conditions is built only where one of them holds; that is the build's own
answer, so it replaces the directory / file-name guess (src/unix/, linux.c) for that file.

Read without running CMake: `if` / `elseif` / `else` / `endif` over WIN32, APPLE, UNIX, ANDROID, IOS, EMSCRIPTEN,
MSVC, MINGW, CYGWIN and `CMAKE_SYSTEM_NAME STREQUAL / MATCHES`, with NOT / AND / OR and parentheses. Anything else (an
option, a version check) is unknown and keeps the file live. Files are named in `list(APPEND|PREPEND ...)`, `set(...)`,
`target_sources(...)`, `add_library(...)` and `add_executable(...)`, relative to the CMakeLists.txt (a
`${CMAKE_CURRENT_SOURCE_DIR}/` prefix is understood); names built from other variables are not followed.

compile_db_membership: `.cg.yaml` platforms.compile_commands, one compile_commands.json per target build, is the exact
answer and wins over the CMake lists."""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import presets

SYSTEM_NAME = {"windows": "Windows", "linux": "Linux", "macos": "Darwin", "ios": "iOS", "android": "Android",
               "web": "Emscripten", "tvos": "tvOS", "watchos": "watchOS", "visionos": "visionOS"}
# operating systems outside the known targets, to tell whether a source list also builds the file there
OTHER = tuple("other:" + n for n in ("FreeBSD", "OpenBSD", "NetBSD", "DragonFly", "AIX", "SunOS", "OS390", "QNX",
                                     "Haiku", "CYGWIN", "MSYS", "GNU", "OS400"))
BSDS = {"other:FreeBSD", "other:OpenBSD", "other:NetBSD", "other:DragonFly"}
SRC = re.compile(r"\.(c|cc|cpp|cxx|m|mm|h|hh|hpp|hxx)$", re.I)
LISTS = {"list", "set", "target_sources", "add_library", "add_executable"}


def _strip_comments(text: str) -> str:
    text = re.sub(r"#\[(=*)\[.*?\]\1\]", " ", text, flags=re.S)       # bracket comments
    out = []
    for line in text.split("\n"):
        q = False
        for i, ch in enumerate(line):
            if ch == '"':
                q = not q
            elif ch == "#" and not q:
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def _commands(text: str):
    """(name, args text, line) for every command call, args with nested parentheses."""
    i, n = 0, len(text)
    rx = re.compile(r"([A-Za-z_]\w*)\s*\(")
    while True:
        m = rx.search(text, i)
        if not m:
            return
        depth, j, q = 1, m.end(), False
        while j < n and depth:
            ch = text[j]
            if ch == '"':
                q = not q
            elif not q and ch == "(":
                depth += 1
            elif not q and ch == ")":
                depth -= 1
            j += 1
        yield m.group(1).lower(), text[m.end():j - 1], text.count("\n", 0, m.start()) + 1
        i = j


def _tokens(args: str) -> list[str]:
    return [a[1:-1] if a.startswith('"') else a for a in re.findall(r'"[^"]*"|\(|\)|[^\s()"]+', args)]


# ------------------------------------------------------------------ three-valued condition evaluation per target
def _var(name: str, p: str):
    name = re.sub(r"^\$\{(.*)\}$", r"\1", name)
    u = name.upper()
    if p.startswith("other:"):
        if u in ("WIN32", "APPLE", "ANDROID", "IOS", "EMSCRIPTEN", "MSVC", "MINGW", "CMAKE_CL_64", "LINUX"):
            return False
        if u == "UNIX":
            return True
        if u in ("CYGWIN", "MSYS"):
            return p == "other:" + u
        if u == "BSD":
            return p in BSDS
    if u == "WIN32":
        return p == "windows"
    if u == "APPLE":
        return p in ("macos", "ios", "tvos", "watchos", "visionos")
    if u == "UNIX":
        return p != "windows"
    if u == "ANDROID":
        return p == "android"
    if u == "IOS":
        return p == "ios"
    if u == "EMSCRIPTEN":
        return p == "web"
    if u in ("MSVC", "MINGW", "CMAKE_CL_64"):
        return None if p == "windows" else False
    if u in ("CYGWIN", "MSYS", "BSD"):
        return False
    if u == "LINUX":
        return p == "linux"
    if u in ("ON", "TRUE", "YES", "Y") or (u.isdigit() and u != "0"):
        return True
    if u in ("OFF", "FALSE", "NO", "N", "0", "IGNORE", "NOTFOUND", ""):
        return False
    return None


def _system(tok: str) -> bool:
    return re.sub(r"^\$\{(.*)\}$", r"\1", tok) == "CMAKE_SYSTEM_NAME"


class _Cond:
    def __init__(self, toks: list[str]):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self):
        self.i += 1
        return self.t[self.i - 1]

    def parse(self, p: str):
        self.i = 0
        try:
            v = self.p_or(p)
        except (IndexError, re.error):
            return None
        return v if self.i >= len(self.t) else None

    def p_or(self, p):
        v = self.p_and(p)
        while self.peek() == "OR":
            self.take()
            w = self.p_and(p)
            v = True if (v is True or w is True) else (False if (v is False and w is False) else None)
        return v

    def p_and(self, p):
        v = self.p_not(p)
        while self.peek() == "AND":
            self.take()
            w = self.p_not(p)
            v = False if (v is False or w is False) else (True if (v is True and w is True) else None)
        return v

    def p_not(self, p):
        if self.peek() == "NOT":
            self.take()
            v = self.p_not(p)
            return None if v is None else not v
        return self.p_bin(p)

    def p_bin(self, p):
        tok = self.take()
        if tok == "(":
            v = self.p_or(p)
            if self.take() != ")":
                raise IndexError
            return v
        if tok in ("DEFINED", "EXISTS", "COMMAND", "TARGET", "POLICY", "IS_DIRECTORY", "IS_ABSOLUTE"):
            self.take()
            return None
        op = self.peek()
        if op in ("STREQUAL", "MATCHES", "EQUAL", "STRLESS", "STRGREATER", "LESS", "GREATER", "IN_LIST",
                  "VERSION_LESS", "VERSION_GREATER", "VERSION_EQUAL", "VERSION_GREATER_EQUAL", "VERSION_LESS_EQUAL",
                  "LESS_EQUAL", "GREATER_EQUAL", "STRLESS_EQUAL", "STRGREATER_EQUAL"):
            self.take()
            rhs = self.take()
            sysname = SYSTEM_NAME.get(p) or p.split(":", 1)[-1]
            if _system(tok) and op == "STREQUAL":
                return sysname == rhs
            if _system(tok) and op == "MATCHES":
                return re.search(rhs, sysname) is not None
            return None
        return _var(tok, p)


def _values(toks: list[str]) -> dict:
    from .platforms import KNOWN
    c = _Cond(toks)
    return {p: c.parse(p) for p in KNOWN + OTHER}


def _and(a: dict, b: dict) -> dict:
    return {p: False if (a[p] is False or b[p] is False) else (True if (a[p] is True and b[p] is True) else None) for p in a}


def _not(a: dict) -> dict:
    return {p: None if v is None else not v for p, v in a.items()}


def _or(a: dict, b: dict) -> dict:
    return {p: True if (a[p] is True or b[p] is True) else (False if (a[p] is False and b[p] is False) else None) for p in a}


# ------------------------------------------------------------------ source membership
def membership(root: Path, files: set[str]) -> dict[str, dict]:
    """rel file -> {"values": {target: True / False / None}, "text": first condition, "at": "CMakeLists.txt:12",
    "mentions": n, "elsewhere": also built on another OS} for the indexed files the CMake lists mention only under
    platform conditions."""
    from .platforms import KNOWN
    root = Path(root)
    every = {p: True for p in KNOWN + OTHER}
    skip = presets.skip_dirs("common")
    seen: dict[str, dict] = {}
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in skip and not x.startswith(".")]
        if "CMakeLists.txt" not in names and not any(n.endswith(".cmake") for n in names):
            continue
        for name in sorted(n for n in names if n == "CMakeLists.txt" or n.endswith(".cmake")):
            path = Path(d) / name
            try:
                text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            base = Path(d).relative_to(root).as_posix()
            rel_cm = path.relative_to(root).as_posix()
            stack: list[list] = []          # [current branch values, values of previous branches, text]
            body = 0                        # inside function() / macro(): the lists are built where it is called
            for cmd, args, line in _commands(text):
                if cmd in ("function", "macro"):
                    body += 1
                    continue
                if cmd in ("endfunction", "endmacro"):
                    body = max(0, body - 1)
                    continue
                if body:
                    continue
                if cmd == "if":
                    v = _values(_tokens(args))
                    stack.append([v, [v], f"if({args.strip()})"[:120]])
                elif cmd == "elseif" and stack:
                    v = _values(_tokens(args))
                    prev = stack[-1][1]
                    cur = v
                    for pv in prev:
                        cur = _and(cur, _not(pv))
                    stack[-1] = [cur, prev + [v], f"elseif({args.strip()})"[:120]]
                elif cmd == "else" and stack:
                    cur = dict(every)
                    for pv in stack[-1][1]:
                        cur = _and(cur, _not(pv))
                    stack[-1] = [cur, stack[-1][1], "else() of " + stack[-1][2]]
                elif cmd == "endif" and stack:
                    stack.pop()
                elif cmd in LISTS:
                    cond = dict(every)
                    for v, _, _ in stack:
                        cond = _and(cond, v)
                    for tok in _tokens(args):
                        tok = re.sub(r"^\$\{(?:CMAKE_CURRENT_SOURCE_DIR|CMAKE_CURRENT_LIST_DIR)\}/", "", tok)
                        if "$" in tok or not SRC.search(tok) or tok.startswith("/"):
                            continue
                        rel = os.path.normpath(os.path.join(base, tok)).replace(os.sep, "/") if base != "." else os.path.normpath(tok).replace(os.sep, "/")
                        if rel not in files:
                            continue
                        s = seen.get(rel)
                        text_c = " && ".join(t for _, _, t in stack) or "unconditional"
                        if s is None:
                            seen[rel] = {"values": cond, "text": text_c, "at": f"{rel_cm}:{line}", "mentions": 1}
                        else:
                            s["values"] = _or(s["values"], cond)
                            s["mentions"] += 1
    out = {}
    for f, s in seen.items():
        vals = {p: s["values"][p] for p in KNOWN}
        if any(v is False for v in vals.values()):
            # also built on an OS outside the known targets (a BSD, AIX, Cygwin): its #else branches are live there
            out[f] = {**s, "values": vals, "elsewhere": any(s["values"][p] is not False for p in OTHER)}
    return out


def tree(values: dict):
    """A platforms.Cond tree with these per-target values: True where it holds, unknown where it may."""
    parts = [("atom", "platform", p) for p, v in values.items() if v is True]
    parts += [("atom", "unknown_on", p) for p, v in values.items() if v is None]
    if not parts:
        return ("atom", "never", None)
    return parts[0] if len(parts) == 1 else ("any", parts)


# ------------------------------------------------------------------ compile databases per target
def compile_db_membership(root: Path, dbs: dict[str, str], files: set[str]) -> dict[str, dict]:
    """`.cg.yaml` platforms.compile_commands (target -> compile_commands.json of that target's build): a source file
    is built on the targets whose database compiles it, not on the other listed targets, and unknown on targets
    without a database. Headers are not compiled on their own and are left out."""
    from .platforms import KNOWN
    root = Path(root).resolve()
    compiled: dict[str, set[str]] = {}
    for target, path in dbs.items():
        p = (root / path) if not os.path.isabs(path) else Path(path)
        try:
            entries = __import__("json").loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        got = set()
        for e in entries if isinstance(entries, list) else ():
            f = e.get("file") if isinstance(e, dict) else None
            if not f:
                continue
            fp = Path(f) if os.path.isabs(f) else Path(e.get("directory") or root) / f
            try:
                rel = Path(os.path.normpath(fp)).resolve().relative_to(root).as_posix()
            except (ValueError, OSError):
                continue
            got.add(rel)
        compiled[target] = got
    if not compiled:
        return {}
    out = {}
    for f in files:
        if not re.search(r"\.(c|cc|cpp|cxx|c\+\+|m|mm)$", f, re.I):
            continue
        vals = {p: (f in compiled[p]) if p in compiled else None for p in KNOWN}
        if any(v is False for v in vals.values()):
            on = [p for p in KNOWN if vals[p]]
            out[f] = {"values": vals, "text": f"compile_commands ({', '.join(on) or 'no listed target'})"}
    return out

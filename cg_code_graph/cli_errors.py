"""Short errors for rejected flags and unknown subcommands.

Grep- and git-style flags are not accepted: the reply names the long option
that already exists on this command, or the commands that do have it.
Unknown subcommands name the closest real commands instead of the full list.
"""
from __future__ import annotations

import argparse
import difflib
import re

# Rejected spellings -> long options that already exist somewhere on the CLI.
FLAG_HINTS: dict[str, tuple[str, ...]] = {
    "-k": ("--kind",),
    "-A": ("--context", "--max-lines"),
    "-C": ("--context",),
    "--depth": ("--max-depth",),
    "--name": ("--db",),
}

# Guesses that spelling distance does not connect to the real command.
COMMAND_HINTS: dict[str, tuple[str, ...]] = {
    "callers": ("impact", "node", "readers"),
    "caller": ("impact", "node", "readers"),
    "called-by": ("impact", "node", "readers"),
    "whocalls": ("impact", "node", "readers"),
    "who-calls": ("impact", "node", "readers"),
    "callees": ("downstream", "node"),
    "callee": ("downstream", "node"),
    "find": ("search",),
    "grep": ("search",),
    "cat": ("snippet", "node"),
    "source": ("snippet", "node"),
}


def explain_parse_error(parser: argparse.ArgumentParser, message: str, argv: list[str] | None) -> str | None:
    """A one-screen stderr body, or None to keep argparse's own error."""
    root = getattr(parser, "_cg_root", parser)
    args = list(argv or getattr(parser, "_cg_argv", []) or [])
    if message.startswith("unrecognized arguments:"):
        return _unknown_args(root, message, args)
    if message.startswith("argument cmd: invalid choice:"):
        return _unknown_command(root, message)
    return None


def _subparsers(parser: argparse.ArgumentParser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    out: set[str] = set()
    for action in parser._actions:
        out.update(action.option_strings)
    return out


def _owners(root: argparse.ArgumentParser, option: str) -> list[str]:
    action = _subparsers(root)
    if action is None:
        return []
    return sorted(name for name, sub in action.choices.items() if option in _option_strings(sub))


def _command(argv: list[str], names: set[str]) -> str | None:
    for arg in argv:
        if arg == "--":
            return None
        if arg in names:
            return arg
        if arg.startswith("-"):
            continue
        return None
    return None


def _normalize_flag(token: str) -> str:
    if token.startswith("--") and "=" in token:
        return token.split("=", 1)[0]
    if re.fullmatch(r"-[A-Za-z]\d+", token):
        return token[:2]
    return token


def _flag_hint(root: argparse.ArgumentParser, command: str | None, flag: str) -> str | None:
    options = FLAG_HINTS.get(flag)
    if not options:
        return None
    parts: list[str] = []
    for opt in options:
        owners = _owners(root, opt)
        if not owners:
            continue
        if command and command in owners:
            parts.append(opt)
        else:
            parts.append(f"{opt} (on {', '.join(owners)})")
    if not parts:
        return None
    text = f"unknown {flag}; did you mean {' or '.join(parts)}?"
    if flag == "--name":
        named = _owners(root, "--name")
        if named:
            text += f" (--name sets the project name on {', '.join(named)})"
    return text


def _unknown_args(root: argparse.ArgumentParser, message: str, argv: list[str]) -> str:
    action = _subparsers(root)
    names = set(action.choices) if action is not None else set()
    command = _command(argv, names)
    tokens = message.split(":", 1)[1].strip().split()
    hints: list[str] = []
    leftover: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("-") and token != "-":
            flag = _normalize_flag(token)
            hint = _flag_hint(root, command, flag)
            i += 1
            values: list[str] = []
            while i < len(tokens) and not (tokens[i].startswith("-") and tokens[i] != "-"):
                values.append(tokens[i])
                i += 1
            if hint:
                hints.append(hint)
            else:
                leftover.append(token)
                leftover.extend(values)
            continue
        leftover.append(token)
        i += 1
    body: list[str] = list(hints)
    if leftover:
        body.append("unrecognized arguments: " + " ".join(leftover))
    if not body:
        body.append(message)
    if leftover:
        body.append(f"see `cg {command} --help`" if command else "see `cg --help`")
    text = "cg: error: " + body[0]
    if len(body) > 1:
        text += "\n" + "\n".join(body[1:])
    return text


def _fmt_try(names: list[str]) -> str:
    shown = [f"cg {name}" for name in names]
    if len(shown) == 1:
        return shown[0]
    if len(shown) == 2:
        return f"{shown[0]} or {shown[1]}"
    return ", ".join(shown[:-1]) + ", or " + shown[-1]


def _unknown_command(root: argparse.ArgumentParser, message: str) -> str:
    action = _subparsers(root)
    choices = list(action.choices) if action is not None else []
    match = re.search(r"invalid choice: '([^']*)'", message)
    name = match.group(1) if match else ""
    picks = [cmd for cmd in COMMAND_HINTS.get(name, ()) if cmd in choices]
    if not picks and name:
        picks = difflib.get_close_matches(name, choices, n=3, cutoff=0.6)
    if picks:
        return f"cg: error: unknown command {name!r}; try: {_fmt_try(picks)}"
    return f"cg: error: unknown command {name!r}; run `cg --help` for commands"

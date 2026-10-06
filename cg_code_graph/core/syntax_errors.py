"""Files that parsed with syntax errors, in every language (#73).

A plugin records the error line spans per file in its file report (`syntax_errors`: {path: [[first, last], ...]}):
tree-sitter ERROR / MISSING nodes (Swift, Kotlin, Rust, C / C++), the TypeScript parser's diagnostics, the Dart
analyzer's errors, the line of a Python / PHP parse failure. After indexing, `summarize` adds the declarations lost:
declaration heads (per-language patterns) inside the spans (the whole file for a file that failed to parse) whose
name the graph has no node for in that file. `cg coverage` lists the files with their spans and losses, and an answer
that touches such a file carries a partial-answer note."""
from __future__ import annotations

import re
from pathlib import Path

MAX_FILES = 500        # files stored per language (counts are always exact)
MAX_SPANS = 20         # spans stored per file
MAX_NAMES = 5          # lost declaration names stored per file

_SWIFT_MODS = (r"(?:(?:public|private|fileprivate|internal|open|package|static|final|override|mutating|nonmutating|"
               r"nonisolated|isolated|required|convenience|dynamic|indirect|distributed|class(?=\s+func))\s+)*")
DECL = {
    "swift": re.compile(r"^[ \t]*(?:@[\w.]+(?:\([^)\n]*\))?\s+)*" + _SWIFT_MODS +
                        r"(func|init|class|struct|enum|protocol|extension|actor)\b[ \t]*([A-Za-z_]\w*)?", re.M),
    "kotlin": re.compile(r"^[ \t]*(?:@[\w.]+(?:\([^)\n]*\))?\s+)*(?:(?:public|private|internal|protected|open|abstract|"
                         r"override|suspend|inline|data|sealed|enum|annotation|inner|value|companion|actual|expect|"
                         r"operator|infix|tailrec|external)\s+)*(fun|class|interface|object)\b[ \t]*"
                         r"(?:<[^>\n]*>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)?", re.M),
    "rust": re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(?:(?:const|async|unsafe|extern\s+\"\w+\")\s+)*"
                       r"(fn|struct|enum|trait|mod|union)\s+([A-Za-z_]\w*)", re.M),
    "c_cpp": re.compile(r"^(?:[\w:*&<>,~]+[ \t]+)*?(?:(class|struct)\s+([A-Za-z_]\w*)\s*(?::[^;{]*)?\{|"
                        r"()(?:[\w:<>]+::)?(~?[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{)", re.M),
    "typescript": re.compile(r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
                             r"(function\*?|class|interface|enum)\s+([A-Za-z_$][\w$]*)", re.M),
    "python": re.compile(r"^[ \t]*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)", re.M),
    "php": re.compile(r"^[ \t]*(?:(?:abstract|final|public|private|protected|static|readonly)\s+)*"
                      r"(function|class|interface|trait|enum)\s+&?([A-Za-z_]\w*)", re.M),
    "dart": re.compile(r"^[ \t]*(?:(?:abstract|base|final|sealed|interface|mixin)\s+)*(class|mixin|enum|extension)\s+"
                       r"([A-Za-z_]\w*)", re.M),
}
NO_NODE = {"swift": {"deinit"}, "rust": set(), "c_cpp": {"if", "for", "while", "switch", "catch", "return", "sizeof"}}


def tree_spans(root) -> list[list[int]]:
    """[first line, last line] (1-based) of the outermost ERROR nodes and of each MISSING node, merged."""
    out = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "ERROR":
            if n.start_byte == n.end_byte and n.next_sibling is not None and n.next_sibling.type == "directive":
                continue        # tree-sitter-swift's empty ERROR before an `#if` between members: nothing lost
            out.append([n.start_point[0] + 1, max(n.end_point[0] + 1 - (n.end_point[1] == 0 and
                                                                      n.end_point[0] > n.start_point[0]), n.start_point[0] + 1)])
            continue
        if n.is_missing:
            out.append([n.start_point[0] + 1, n.start_point[0] + 1])
            continue
        if n.has_error:
            stack.extend(n.children)
    return merge(out)


def merge(spans) -> list[list[int]]:
    out: list[list[int]] = []
    for a, b in sorted([int(x), int(y)] for x, y in spans):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _decls(lang: str, text: str, spans) -> list[tuple[int, str]]:
    """(line, name) of the declaration heads inside the spans."""
    rx = DECL.get(lang)
    if rx is None or not spans:
        return []
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    import bisect
    out = []
    for m in rx.finditer(text):
        kw = next((g for g in m.groups()[0::2] if g is not None), "")
        name = next((g for g in m.groups()[1::2] if g), None) or (kw if kw == "init" else None)
        if not name or name in NO_NODE.get(lang, ()) or (lang == "swift" and kw == "extension"):
            continue        # a Swift extension has no node of its own (the type's may be in another file); its members count
        if lang == "c_cpp" and not kw and name.upper() == name:
            continue        # `TEST_IMPL(poll_duplex) {`: a macro that expands to a definition, not a function name
        ln = bisect.bisect_right(starts, m.start(m.lastindex) if m.lastindex else m.start())
        if any(a <= ln <= b for a, b in spans):
            out.append((ln, name))
    return out


def summarize(lang: str, root: Path, raw: dict, names_by_file: dict, failed=()) -> list[dict]:
    """[{file, spans, errors, decls_lost, lost}] for the files of one language, most declarations lost first."""
    failed = set(failed or ())
    out = []
    for rel, spans in raw.items():
        spans = merge(spans or [])
        if not spans and rel not in failed:
            continue
        try:
            text = (Path(root) / rel).read_bytes().decode("utf-8", "replace")
        except OSError:
            text = ""
        whole = [[1, text.count("\n") + 1]] if rel in failed else spans
        have = names_by_file.get(rel, set())
        lost = [(ln, nm) for ln, nm in _decls(lang, text, whole) if nm not in have]
        e = {"file": rel, "spans": spans[:MAX_SPANS], "errors": len(spans), "decls_lost": len(lost)}
        if lost:
            e["lost"] = [f"{nm}:{ln}" for ln, nm in lost[:MAX_NAMES]]
        if rel in failed:
            e["parse_failed"] = True
        out.append(e)
    out.sort(key=lambda e: (-e["decls_lost"], e["file"]))
    return out


def span_text(e: dict, limit: int = 3) -> str:
    """'Sources/A.swift:83-90, 120 (2 declarations lost: loads:85, testAfter:88)'."""
    sp = e.get("spans") or []
    pos = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in sp[:limit]) + (f" +{e['errors'] - limit}" if e.get("errors", 0) > limit else "")
    s = f"{e['file']}:{pos}" if pos else e["file"]
    n = e.get("decls_lost", 0)
    if e.get("parse_failed"):
        s += " (parse failed"
        s += f", {n} declaration{'s' if n != 1 else ''} lost" if n else ""
        return s + ")"
    if n:
        s += f" ({n} declaration{'s' if n != 1 else ''} lost: {', '.join(e.get('lost') or [])}" + (" …" if n > len(e.get("lost") or []) else "") + ")"
    return s

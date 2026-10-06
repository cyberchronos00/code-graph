"""tree-sitter helpers (py-tree-sitter >= 0.22 with the per-language wheels)."""
from __future__ import annotations

from functools import lru_cache


class TreeSitterMissing(RuntimeError):
    pass


@lru_cache(maxsize=None)
def parser(lang: str):
    try:
        from tree_sitter import Language, Parser
        if lang == "rust":
            import tree_sitter_rust as m
        elif lang == "c":
            import tree_sitter_c as m
        elif lang == "cpp":
            import tree_sitter_cpp as m
        else:
            raise TreeSitterMissing(lang)
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise TreeSitterMissing(f"tree-sitter grammar for {lang} not installed ({e}); "
                                f"pip install tree-sitter tree-sitter-rust tree-sitter-c tree-sitter-cpp") from e
    return Parser(Language(m.language()))


def text(src: bytes, n) -> str:
    return src[n.start_byte:n.end_byte].decode("utf-8", "replace")


def walk(n):
    """Pre-order iterator over all named and anonymous descendants (iterative, no recursion limit)."""
    stack = [n]
    while stack:
        x = stack.pop()
        yield x
        stack.extend(reversed(x.children))


def string_value(src: bytes, n) -> str | None:
    """Value of a string literal node (rust string_literal / raw_string_literal, C string_literal)."""
    if n is None:
        return None
    if n.type in ("string_literal", "raw_string_literal", "string"):
        parts = [text(src, c) for c in n.children if c.type in ("string_content", "string_fragment")]
        if parts:
            return "".join(parts)
        t = text(src, n)
        if t.startswith(("r#", "r\"")):
            t = t.lstrip("r").strip("#")
        return t.strip('"')
    return None

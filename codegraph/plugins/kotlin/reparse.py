"""Member-level re-parse of a Kotlin file that tree-sitter-kotlin cannot parse (#104).

tree-sitter's error recovery often turns one construct the grammar lacks (a parenthesized function type with a
receiver, a `$$"..."` string, a context parameter) into an ERROR that swallows the rest of the class or the file.
Here the file is split into members by a small lexer (braces, parentheses and brackets outside strings, characters and
comments): top-level declarations, and recursively the members of class / object / interface bodies. Each member is
parsed on its own inside the file's skeleton (package, imports, class headers and braces; every other member blanked)
and a member that still errors is blanked in the final copy. Blanking replaces bytes with spaces and keeps newlines,
so the other members keep their lines, offsets and text, and the dropped members are reported as error spans."""
from __future__ import annotations

import re

_DECL_START = re.compile(rb"(?:@|/\*|//|(?:(?:public|private|internal|protected|open|abstract|override|suspend|inline|"
                         rb"data|sealed|enum|annotation|inner|value|companion|actual|expect|operator|infix|tailrec|"
                         rb"external|const|lateinit|final|noinline|crossinline|fun)\b)|(?:fun|val|var|class|object|"
                         rb"interface|init|constructor|typealias)\b)")
_ATTACH = re.compile(rb"(?:@[\w.]+(?:\(.*\))?\s*|/\*.*|//.*|\*.*)$")
_HEADER = re.compile(rb"(?:package|import)\s")
_HEADER_CONT = re.compile(rb"(?:(?:public|private|internal|protected)\s+)?(?:@\w+\s+)*constructor\b|:|where\b")
_CLASSY = re.compile(rb"\b(?:class|object|interface)\b")
MAX_MEMBERS = 400


def _depths(src: bytes) -> tuple[list[int], list[bool]]:
    """Per byte: nesting depth of ( [ { outside strings / chars / comments, and whether the byte is code."""
    n = len(src)
    depth = [0] * (n + 1)
    code = [True] * n
    d, i = 0, 0
    tmpl: list[int] = []          # brace depth at which a `${` template inside a string started
    while i < n:
        c = src[i]
        depth[i] = d
        if c == 0x2F and i + 1 < n and src[i + 1] == 0x2F:            # // comment
            j = src.find(b"\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                depth[k], code[k] = d, False
            i = j
            continue
        if c == 0x2F and i + 1 < n and src[i + 1] == 0x2A:            # /* nested */ comment
            lvl, j = 1, i + 2
            while j < n and lvl:
                if src[j:j + 2] == b"/*":
                    lvl, j = lvl + 1, j + 2
                elif src[j:j + 2] == b"*/":
                    lvl, j = lvl - 1, j + 2
                else:
                    j += 1
            for k in range(i, min(j, n)):
                depth[k], code[k] = d, False
            i = j
            continue
        if c == 0x27:                                                    # 'c' / '\n' / '\u0041'
            m = re.match(rb"'(?:\\u[0-9a-fA-F]{4}|\\.|[^'\\\n])'", src[i:i + 8])
            if m:
                for k in range(i, i + m.end()):
                    depth[k], code[k] = d, False
                i += m.end()
                continue
        if c == 0x22:                                                    # "..." / """..."""
            raw = src[i:i + 3] == b'"""'
            j = i + (3 if raw else 1)
            while j < n:
                if not raw and src[j] == 0x5C:
                    j += 2
                    continue
                if raw and src[j:j + 3] == b'"""':
                    j += 3
                    while j < n and src[j] == 0x22:
                        j += 1
                    break
                if not raw and src[j] == 0x22:
                    j += 1
                    break
                if not raw and src[j] == 0x0A:
                    break
                if src[j] == 0x24 and j + 1 < n and src[j + 1] == 0x7B:   # ${ ... } template: skip balanced
                    lvl, j = 1, j + 2
                    while j < n and lvl:
                        if src[j] == 0x7B:
                            lvl += 1
                        elif src[j] == 0x7D:
                            lvl -= 1
                        elif src[j] == 0x22:                             # a string inside the template
                            q = src.find(b'"', j + 1)
                            j = n if q < 0 else q
                        j += 1
                    continue
                j += 1
            for k in range(i, min(j, n)):
                depth[k], code[k] = d, False
            i = j
            continue
        if c in (0x28, 0x5B, 0x7B):
            d += 1
        elif c in (0x29, 0x5D, 0x7D):
            d = max(d - 1, 0)
        i += 1
    depth[n] = d
    return depth, code


def _members(src: bytes, depth, code, lo: int, hi: int, d: int, out: list, level: int = 0):
    """Split [lo, hi) (inside a body at depth d) into member chunks; recurse into class-like bodies."""
    starts = []
    pos = lo
    while pos < hi:
        e = src.find(b"\n", pos, hi)
        e = hi if e < 0 else e
        s = pos
        while s < e and src[s] in (0x20, 0x09):
            s += 1
        if s < e and depth[s] == d and (code[s] or src[s:s + 2] in (b"/*", b"//")) and src[s] != 0x7D:
            starts.append((pos, s, e))
        pos = e + 1
    cont = set()                          # `@Ann` lines in front of a `constructor(` that continues a class header
    for k, (pos, s, e) in enumerate(starts):
        if _HEADER_CONT.match(src[s:e]):
            j = k - 1
            while j >= 0 and _ATTACH.match(src[starts[j][1]:starts[j][2]].rstrip()):
                cont.add(j)
                j -= 1
    chunks: list[list] = []               # [start, end, has_body, keep]
    for k, (pos, s, e) in enumerate(starts):
        line = src[s:e].rstrip()
        if d == 0 and code[s] and _HEADER.match(line):
            chunks.append([pos, e, True, True])   # package / import lines stay in the skeleton
            continue
        is_start = bool(_DECL_START.match(line))
        attach = bool(_ATTACH.match(line)) or not code[s]
        if (chunks and not chunks[-1][3] and (k in cont or _HEADER_CONT.match(line))
                and _CLASSY.search(src[chunks[-1][0]:pos])):
            is_start = False                      # `class A` + `private constructor(...)` / `: B()` on the next line
        if chunks and not chunks[-1][3] and (not is_start or not chunks[-1][2]):
            chunks[-1][1] = e
            chunks[-1][2] = chunks[-1][2] or not attach
            continue
        chunks.append([pos, e, not attach, False])
    # a chunk runs to the next one's start (its body's lines are deeper)
    for k, ch in enumerate(chunks):
        ch[1] = chunks[k + 1][0] if k + 1 < len(chunks) else hi
    for a, b, _, keep in chunks:
        if keep:
            continue
        # strip trailing whitespace-only region so the closing brace of the enclosing body stays outside
        body = None
        head_end = a
        if level < 3:
            j = a
            while j < b:
                if src[j] == 0x7B and depth[j] == d and code[j]:
                    body = j
                    break
                if src[j] == 0x3D and depth[j] == d and code[j]:       # `=`: an initializer, not a body
                    break
                j += 1
            if body is not None and not _CLASSY.search(_code_only(src, code, a, body)):
                body = None
        if body is not None:
            close = body + 1
            while close < b and not (src[close] == 0x7D and depth[close] == d + 1 and code[close]):
                close += 1
            if close < b:
                head_end = body + 1
                inner: list = []
                _members(src, depth, code, head_end, close, d + 1, inner, level + 1)
                if inner:
                    out.extend(inner)
                    continue
        out.append((a, b))


def _code_only(src: bytes, code, a: int, b: int) -> bytes:
    return bytes(ch for k, ch in enumerate(src[a:b]) if code[a + k])


def _blank(buf: bytearray, a: int, b: int):
    for k in range(a, b):
        if buf[k] not in (0x0A, 0x0D):
            buf[k] = 0x20


def _err_bytes(root) -> int:
    tot, stack = 0, [root]
    while stack:
        n = stack.pop()
        if n.type == "ERROR" or n.is_missing:
            tot += max(n.end_byte - n.start_byte, 1)
        elif n.has_error:
            stack.extend(n.children)
    return tot


def reparse_members(parser, psrc: bytes, tree):
    """(tree, dropped [(first line, last line)], members) for a file whose tree has errors; tree is None when the
    member split does not help (the skeleton itself does not parse, or nothing would be recovered)."""
    depth, code = _depths(psrc)
    chunks: list = []
    _members(psrc, depth, code, 0, len(psrc), 0, chunks)
    if not chunks or len(chunks) > MAX_MEMBERS:
        return None, [], len(chunks)
    skel = bytearray(psrc)
    for a, b in chunks:
        _blank(skel, a, b)
    if parser.parse(bytes(skel)).root_node.has_error:
        return None, [], len(chunks)
    bad = []
    for a, b in chunks:
        t = bytearray(skel)
        t[a:b] = psrc[a:b]
        if parser.parse(bytes(t)).root_node.has_error:
            bad.append((a, b))
    if not bad:
        return None, [], len(chunks)
    out = bytearray(psrc)
    for a, b in bad:
        _blank(out, a, b)
    nt = parser.parse(bytes(out))
    lost = sum(b - a for a, b in bad) + _err_bytes(nt.root_node)
    if lost >= _err_bytes(tree.root_node):
        return None, [], len(chunks)
    dropped = []
    for a, b in bad:
        s = a
        while s < b and psrc[s] in b" \t\r\n":
            s += 1
        e = b
        while e > s and psrc[e - 1] in b" \t\r\n":
            e -= 1
        if e > s:
            dropped.append((psrc.count(b"\n", 0, s) + 1, psrc.count(b"\n", 0, e - 1) + 1))
    return nt, dropped, len(chunks)

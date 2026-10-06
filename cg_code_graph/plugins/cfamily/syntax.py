"""Syntactic layer for C and C++ (tree-sitter-c / tree-sitter-cpp, no preprocessing).

Per file: function definitions (with namespace/class qualification), prototypes and in-class method declarations
(virtual / override / pure, access), classes/structs/unions/enums (+ bases, fields, enumerators), typedefs, globals
(with initializer ranges: dispatch tables), macros, #includes, getenv() keys, call sites and identifier uses (for the
heuristic mode), and preprocessor conditional regions (line-based scan, so it works even where tree-sitter cannot
structure the #if).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..native.ts import parser, string_value, text

TEST_MACROS = {"TEST", "TEST_F", "TEST_P", "TYPED_TEST", "TYPED_TEST_P", "TEST_CASE", "TEST_CASE_METHOD", "SCENARIO",
               "BOOST_AUTO_TEST_CASE", "BOOST_FIXTURE_TEST_CASE", "BOOST_DATA_TEST_CASE", "CATCH_TEST_CASE",
               "DOCTEST_TEST_CASE", "TEST_CASE_TEMPLATE", "UTEST", "UTEST_F", "CTEST", "CTEST2", "START_TEST", "BENCHMARK"}
GETENV = {"getenv", "secure_getenv", "_wgetenv", "std::getenv", "g_getenv", "qgetenv", "qEnvironmentVariable",
          "__secure_getenv", "_dupenv_s", "getenv_s"}
EXPORT_RE = re.compile(r"\b(__declspec\s*\(\s*dllexport\s*\)|__attribute__\s*\(\(\s*visibility\s*\(\s*\"default\"\s*\)\s*\)\)|"
                       r"[A-Z][A-Z0-9_]*(?:_API|_EXPORT|_EXTERN|_PUBLIC|_DLL|_VISIBLE|API|EXPORT|EXTERN|PUBLIC)(?:_[A-Z0-9_]+)?)\b")
NOT_EXPORT = {"EXTERN", "API"}  # too generic on their own


@dataclass
class CItem:
    kind: str                 # function method class struct union enum enumerator typedef global field macro
    name: str
    key: str
    file: str
    line: int
    col: int
    start: int
    end: int
    module: str
    qual: str = ""            # qualified name without file prefix / signature
    static: bool = False
    attrs: dict = field(default_factory=dict)
    parent: str | None = None
    scope: list = field(default_factory=list)   # raw qualifier of out-of-line definitions (A::B::f -> [A, B])
    ns: list = field(default_factory=list)
    sig: str = ""
    doc: str | None = None


@dataclass
class CDecl:
    name: str
    qual: str
    file: str
    line: int
    col: int
    kind: str                 # function | method
    export: bool = False
    access: str | None = None
    virtual: bool = False
    pure: bool = False
    override: bool = False
    static: bool = False
    extern_c: bool = False


@dataclass
class CFile:
    path: str
    lang: str                 # c | cpp
    items: list = field(default_factory=list)
    decls: list = field(default_factory=list)
    includes: list = field(default_factory=list)      # (path, line, system)
    regions: list = field(default_factory=list)       # (start, end, cond, directive line, branch)
    guards: set = field(default_factory=set)          # include-guard macro names
    env: list = field(default_factory=list)           # (key, line, owner CItem)
    calls: list = field(default_factory=list)         # (owner CItem, form, text, name, line, col)
    idents: list = field(default_factory=list)        # (owner CItem, name, line, col, followed_by_paren)
    type_refs: list = field(default_factory=list)     # (owner CItem, name, line, col)
    bases: list = field(default_factory=list)         # (class key, base name text, line, access)
    decl_pos: set = field(default_factory=set)        # (line, col) of declaration names (not references)
    lines: list = field(default_factory=list)
    generators: dict = field(default_factory=dict)    # function-like macro -> (params, value) when it defines functions
    error_spans: list = field(default_factory=list)   # [first, last] lines tree-sitter could not parse (#73)
    top_calls: list = field(default_factory=list)     # (name, args, line, col, end line): `NAME(args)` at column 0
    recovered: int = 0                                # definitions recovered after a region tree-sitter could not parse


def _first(n, *types):
    for c in n.children:
        if c.type in types:
            return c
    return None


def _declarator_name(src, d):
    """Unwrap pointer/reference/function/array declarators -> (name node, function_declarator or None)."""
    fd = None
    while d is not None:
        t = d.type
        if t == "function_declarator":
            fd = fd or d
            d = d.child_by_field_name("declarator")
        elif t in ("pointer_declarator", "reference_declarator", "array_declarator", "init_declarator",
                   "parenthesized_declarator", "attributed_declarator", "abstract_function_declarator"):
            nxt = d.child_by_field_name("declarator")
            if nxt is None:
                nxt = next((c for c in d.named_children if c.type.endswith("declarator") or c.type in
                            ("identifier", "field_identifier", "qualified_identifier", "destructor_name", "operator_name")), None)
            d = nxt
        else:
            return d, fd
    return None, fd


def _qual_parts(src, n) -> tuple[list[str], str]:
    """qualified_identifier A::B::f -> ([A, B], 'f'); other name nodes -> ([], text)."""
    scope = []
    while n is not None and n.type == "qualified_identifier":
        s = n.child_by_field_name("scope")
        if s is not None:
            st = text(src, s)
            scope.append(re.sub(r"<.*>", "", st).strip())
        n = n.child_by_field_name("name")
    if n is None:
        return scope, "?"
    if n.type == "template_function":
        n = n.child_by_field_name("name") or n
    return scope, re.sub(r"\s+", "", text(src, n))


def _params_sig(src, fd) -> str:
    if fd is None:
        return ""
    pl = fd.child_by_field_name("parameters")
    if pl is None:
        return ""
    parts = []
    for p in pl.named_children:
        if p.type in ("parameter_declaration", "optional_parameter_declaration"):
            ty = p.child_by_field_name("type")
            t = text(src, ty) if ty is not None else "?"
            d = p.child_by_field_name("declarator")
            if d is not None and d.type in ("pointer_declarator", "reference_declarator"):
                t += "*" if d.type == "pointer_declarator" else "&"
            parts.append(re.sub(r"\s+", " ", t))
        elif p.type == "variadic_parameter":
            parts.append("...")
    return ",".join(parts)


class Extractor:
    def __init__(self, path: str, src: bytes, lang: str, module: str, blank: re.Pattern | None = None):
        self.src, self.path, self.module = src, path, module
        self.blank = blank
        self.f = CFile(path, lang)

    def run(self) -> CFile:
        src = mask_lock_annotations(mask_annotations(self.src, self.blank) if self.blank is not None else self.src)
        tree = parser(self.f.lang).parse(src)
        if tree.root_node.has_error:
            # `ABSL_FLAG(uint16_t, port, 50051, "..");` at file level parses as a broken function definition that
            # runs to the next `}` and swallows the class after it (#131): blank such macro statements and keep
            # the parse that loses fewer lines
            masked = mask_macro_statements(src)
            if masked != src:
                t2 = parser(self.f.lang).parse(masked)
                if _error_lines(t2.root_node) < _error_lines(tree.root_node):
                    tree = t2
        self.f.lines = self.src.decode("utf-8", "replace").split("\n")
        if tree.root_node.has_error:
            from ...core.syntax_errors import tree_spans
            self.f.error_spans = tree_spans(tree.root_node)
        self.scan_directives()
        self.container(tree.root_node, {"ns": [], "cls": None, "access": None, "anon": False, "extern_c": False})
        lines = self.f.lines
        for i, ln in enumerate(lines):
            if (ln[:1].isalpha() or ln[:1] == "_") and "(" in ln:
                m = TOP_CALL.match(ln)
                if m and m.group(1) not in C_KEYWORDS:
                    self.f.top_calls.append((m.group(1), m.group(2), i + 1, 0, i + 1))
                    continue
                # `NAME(a,` continued on the next lines (up to 12)
                if re.match(r"[A-Za-z_]\w*\s*\(", ln) and ln.count("(") > ln.count(")") and not ln.rstrip().endswith(("{", ";")):
                    j, buf = i, ln
                    while buf.count("(") > buf.count(")") and j + 1 < len(lines) and j - i < 12:
                        j += 1
                        buf += " " + lines[j].strip()
                    m = TOP_CALL.match(buf)
                    if m and m.group(1) not in C_KEYWORDS:
                        self.f.top_calls.append((m.group(1), m.group(2), i + 1, 0, j + 1))
        if tree.root_node.has_error:
            self.recover()
        return self.f

    # ---------------------------------------------------------------- error recovery
    def recover(self):
        """Top-level definitions in or after a region tree-sitter could not parse (unbalanced macro arguments,
        unknown syntax): every brace block at file level that no extracted item starts in is parsed again on its own
        (same line / column positions); a block that still fails gives a function from its head (calls by name)."""
        txt = self.src.decode("utf-8", "replace")
        spans = [(it.start, it.end) for it in self.f.items if it.kind != "macro"]
        names = {it.name for it in self.f.items if it.kind in ("function", "method")}
        line_at = _line_index(txt)
        rec_spans = []
        for a, z in _toplevel_blocks(txt):
            sl, el = line_at(a), line_at(z)
            # covered: an item starts in the block (or just before it: a macro / attribute line on top)
            if any(sl - 3 <= x <= el and y >= sl for x, y in spans):
                continue
            scol = a - txt.rfind("\n", 0, a) - 1
            chunk = "\n" * (sl - 1) + " " * scol + _unwrap(txt[a:z + 1])
            sub = Extractor(self.path, chunk.encode("utf-8"), self.f.lang, self.module, self.blank)
            sub.f = self.f
            n0 = len(self.f.items)
            t = parser(self.f.lang).parse(mask_lock_annotations(
                mask_annotations(sub.src, self.blank) if self.blank is not None else sub.src))
            if not t.root_node.has_error:
                sub.container(t.root_node, {"ns": [], "cls": None, "access": None, "anon": False, "extern_c": False})
            if len(self.f.items) == n0:
                self._head_fn(txt, a, z, sl, el, line_at)
            new = self.f.items[n0:]
            # a name the file already defines (the main parse placed it elsewhere): keep the first
            keep = [it for it in new if not (it.kind in ("function", "method") and it.name in names)]
            if len(keep) != len(new):
                drop = {id(it) for it in new} - {id(it) for it in keep}
                self.f.items[n0:] = keep
                self.f.calls = [c for c in self.f.calls if id(c[0]) not in drop]
                self.f.idents = [c for c in self.f.idents if id(c[0]) not in drop]
                self.f.type_refs = [c for c in self.f.type_refs if id(c[0]) not in drop]
            for it in keep:
                it.attrs.setdefault("recovered", True)
                names.add(it.name)
                rec_spans.append((it.start, it.end, id(it)))
            self.f.recovered += len(keep)
        if rec_spans:
            # a definition the main parse ran on into the recovered ones: clipped, and the references inside the
            # recovered definitions belong to them only
            rec_ids = {r[2] for r in rec_spans}
            first = min(r[0] for r in rec_spans)
            for it in self.f.items:
                if id(it) not in rec_ids and it.start < first <= it.end and it.kind in ("function", "method"):
                    it.end = max(it.start, max((y for x, y in _block_spans_before(txt, line_at, first)), default=it.start))

            def inside(line, owner):
                return id(owner) not in rec_ids and any(x <= line <= y for x, y, _ in rec_spans)
            self.f.calls = [c for c in self.f.calls if not inside(c[4], c[0])]
            self.f.idents = [c for c in self.f.idents if not inside(c[2], c[0])]
            self.f.type_refs = [c for c in self.f.type_refs if not inside(c[2], c[0])]

    def _head_fn(self, txt, a, z, sl, el, line_at):
        b = txt.find("{", a, z + 1)
        head = re.sub(r"\s+", " ", _unwrap(txt[a:b])).strip()
        m = HEAD_FN.search(head)
        if not m or m.group(1) in C_KEYWORDS or re.match(r"(typedef|struct|union|enum|class|namespace|extern)\b", head) or "=" in head:
            return
        name = m.group(1)
        npos = txt.find(name, a, b)
        ln = line_at(npos)
        col = npos - txt.rfind("\n", 0, npos) - 1
        static = head.startswith("static ") or " static " in head[:m.start()]
        it = CItem("function", name, f"{self.path}#{name}" if static else name, self.path, ln, col, sl, el, self.module,
                   name, static=static, attrs={"recovered": "head"})
        self.f.items.append(it)
        for c in CALL_RE.finditer(txt, b, z):
            nm = c.group(1)
            if nm not in C_KEYWORDS:
                p = c.start(1)
                self.f.calls.append((it, "name", nm, nm, line_at(p), p - txt.rfind("\n", 0, p) - 1))

    # ---------------------------------------------------------------- preprocessor regions (line based)
    def scan_directives(self):
        lines = self.f.lines
        stack = []
        first_directive = True
        i = 0
        n = len(lines)
        while i < n:
            raw = lines[i]
            ln = i + 1
            s = raw.strip()
            while s.endswith("\\") and i + 1 < n:
                i += 1
                s = s[:-1] + " " + lines[i].strip()
            i += 1
            if not s.startswith("#"):
                if s and not s.startswith(("//", "/*", "*")):
                    first_directive = False
                continue
            m = re.match(r"#\s*(\w+)\s*(.*)", s)
            if not m:
                continue
            d, rest = m.group(1), re.sub(r"/\*.*?\*/|//.*$", "", m.group(2)).strip()
            if d in ("if", "ifdef", "ifndef"):
                cond = rest if d == "if" else (f"defined({rest.split()[0]})" if d == "ifdef" else f"!defined({rest.split()[0] if rest else '?'})")
                guard = None
                if d == "ifndef" and first_directive and rest:
                    nxt = next((x.strip() for x in lines[i:i + 3] if x.strip()), "")
                    if re.match(rf"#\s*define\s+{re.escape(rest.split()[0])}\b", nxt):
                        guard = rest.split()[0]
                        self.f.guards.add(guard)
                stack.append({"start": ln, "conds": [cond], "cur": cond, "line": ln, "branch": "if", "guard": guard})
            elif d in ("elif", "else", "elifdef", "elifndef") and stack:
                fr = stack[-1]
                self._close(fr, ln - 1)
                prev = " && ".join(f"!({c})" for c in fr["conds"])
                if d == "else":
                    cond = prev
                else:
                    e = rest if d == "elif" else (f"defined({rest})" if d == "elifdef" else f"!defined({rest})")
                    cond = f"{prev} && ({e})"
                    fr["conds"].append(e)
                fr.update({"start": ln, "cur": cond, "line": ln, "branch": d})
            elif d == "endif" and stack:
                fr = stack.pop()
                self._close(fr, ln - 1)
            first_directive = first_directive and d in ("ifndef", "define", "pragma", "if", "ifdef")

    def _close(self, fr, end):
        if fr.get("guard"):
            return
        if end >= fr["start"]:
            self.f.regions.append((fr["start"], end, fr["cur"], fr["line"], fr["branch"]))

    # ---------------------------------------------------------------- declarations
    def _qual(self, ctx, parts: list[str]) -> str:
        return "::".join([p for p in ctx["ns"] if p] + parts)

    def _key(self, ctx, qual: str, static: bool) -> str:
        if static or ctx["anon"]:
            return f"{self.path}#{qual}"
        return qual

    def _add(self, kind, name, key, qual, n, name_node, **kw) -> CItem:
        line = (name_node or n).start_point[0] + 1
        col = (name_node or n).start_point[1]
        it = CItem(kind, name, key, self.path, line, col, n.start_point[0] + 1, n.end_point[0] + 1, self.module, qual, **kw)
        it.doc = self._doc(n)
        self.f.items.append(it)
        return it

    def _doc(self, n) -> str | None:
        p = n.prev_sibling
        docs = []
        while p is not None and p.type == "comment" and p.end_point[0] >= n.start_point[0] - 1 - len(docs) * 0:
            docs.insert(0, text(self.src, p))
            if len(docs) > 6:
                break
            p = p.prev_sibling
        if not docs:
            return None
        s = "\n".join(re.sub(r"^\s*(/\*+|\*+/|\*|//+!?|///?)", "", ln).rstrip(" */") for d in docs for ln in d.splitlines())
        s = s.strip()
        return s[:1500] or None

    def container(self, node, ctx):
        for c in node.children:
            self.decl(c, ctx)

    def decl(self, n, ctx):
        t = n.type
        src = self.src
        if t == "ERROR":
            # a `#define` tree-sitter gave up on (a comment inside a continued macro body): the macro still exists
            kids = n.children
            for i, c in enumerate(kids):
                if c.type == "#define" and i + 1 < len(kids) and kids[i + 1].type == "identifier":
                    name = text(src, kids[i + 1])
                    if name not in self.f.guards:
                        fl = i + 2 < len(kids) and kids[i + 2].type == "preproc_params"
                        it = self._add("macro", name, name, name, c, kids[i + 1], attrs={"function_like": fl})
                        ln = c.start_point[0]
                        body = self.f.lines[ln].split(name, 1)[-1] if ln < len(self.f.lines) else ""
                        if fl:
                            body = re.sub(r"^\s*\([^()]*\)", "", body)
                        it.attrs["value"] = body.strip().rstrip("\\").strip()[:120] or None
        if t in ("preproc_if", "preproc_ifdef", "preproc_elif", "preproc_else", "preproc_elifdef", "ERROR", "declaration_list"):
            self.container(n, ctx)
            return
        if t == "preproc_include":
            p = n.child_by_field_name("path")
            if p is not None:
                s = text(src, p)
                self.f.includes.append((s.strip('<>"'), n.start_point[0] + 1, s.startswith("<")))
            return
        if t in ("preproc_def", "preproc_function_def"):
            nm = n.child_by_field_name("name")
            if nm is not None:
                name = text(src, nm)
                if name not in self.f.guards:
                    it = self._add("macro", name, name, name, n, nm, attrs={"function_like": t == "preproc_function_def"})
                    val = n.child_by_field_name("value")
                    it.attrs["value"] = text(src, val)[:120] if val is not None else None
                    ps = n.child_by_field_name("parameters")
                    if val is not None and ps is not None and "{" in text(src, val):
                        params = [x.strip() for x in text(src, ps).strip("()").split(",") if x.strip()]
                        if generated_names(params, text(src, val), params):
                            self.f.generators[name] = (params, text(src, val))
            return
        if t == "namespace_definition":
            nm = n.child_by_field_name("name")
            body = n.child_by_field_name("body")
            name = text(src, nm) if nm is not None else ""
            c2 = dict(ctx, ns=ctx["ns"] + (name.split("::") if name else []), anon=ctx["anon"] or not name)
            if body is not None:
                self.container(body, c2)
            return
        if t == "linkage_specification":
            body = n.child_by_field_name("body")
            c2 = dict(ctx, extern_c=True)
            if body is not None:
                if body.type == "declaration_list":
                    self.container(body, c2)
                else:
                    self.decl(body, c2)
            return
        if t == "template_declaration":
            for c in n.named_children:
                if c.type not in ("template_parameter_list",):
                    self.decl(c, ctx)
            return
        if t in ("class_specifier", "struct_specifier", "union_specifier", "enum_specifier"):
            self.type_spec(n, ctx)
            return
        if t == "function_definition":
            self.func_def(n, ctx)
            return
        if t in ("declaration", "field_declaration"):
            self.declaration(n, ctx)
            return
        if t == "type_definition":
            ty = n.child_by_field_name("type")
            if ty is not None and ty.type in ("struct_specifier", "union_specifier", "enum_specifier", "class_specifier"):
                tname = None
                for d in n.children_by_field_name("declarator"):
                    nm, _ = _declarator_name(src, d)
                    if nm is not None and nm.type in ("type_identifier", "identifier"):
                        tname = text(src, nm)
                        break
                self.type_spec(ty, ctx, alias=tname)
            for d in n.children_by_field_name("declarator"):
                nm, fd = _declarator_name(src, d)
                if nm is not None and nm.type in ("type_identifier", "identifier", "primitive_type"):
                    name = text(src, nm)
                    q = f"{ctx['cls']['qual']}::{name}" if ctx["cls"] else self._qual(ctx, [name])
                    if not any(x.kind in ("struct", "union", "enum", "class") and x.key == q and x.line == nm.start_point[0] + 1 for x in self.f.items):
                        self._add("typedef", name, q, q, n, nm, attrs={"of": re.sub(r"\s+", " ", text(src, ty))[:80] if ty is not None else None})
            return
        if t == "alias_declaration":
            nm = n.child_by_field_name("name")
            if nm is not None:
                name = text(src, nm)
                q = f"{ctx['cls']['qual']}::{name}" if ctx["cls"] else self._qual(ctx, [name])
                self._add("typedef", name, q, q, n, nm, parent=ctx["cls"]["key"] if ctx["cls"] else None)
            return
        if t in ("access_specifier",):
            ctx["access"] = text(src, n).strip(": ")
            return

    def type_spec(self, n, ctx, alias: str | None = None):
        src = self.src
        body = n.child_by_field_name("body")
        nm = n.child_by_field_name("name")
        if body is None:
            return
        kind = {"class_specifier": "class", "struct_specifier": "struct", "union_specifier": "union", "enum_specifier": "enum"}[n.type]
        spec = ""
        if nm is not None and nm.type == "template_type":
            # explicit / partial specialization `struct S<T*, int>`: name S, key S<T*,int>
            args = nm.child_by_field_name("arguments")
            spec = re.sub(r"\s+", "", text(src, args)) if args is not None else ""
            nm = nm.child_by_field_name("name") or nm
        elif nm is not None and nm.type == "qualified_identifier":
            nn = nm.child_by_field_name("name")
            nm = nn if nn is not None and nn.type == "type_identifier" else nm
        name = text(src, nm) if nm is not None else alias
        if not name:
            name = None
        if name:
            parent_q = ctx["cls"]["qual"] if ctx["cls"] else None
            q = (f"{parent_q}::{name}" if parent_q else self._qual(ctx, [name])) + spec
            it = self._add(kind, name, self._key(ctx, q, False), q, n, nm if nm is not None else n, parent=ctx["cls"]["key"] if ctx["cls"] else None)
            key = it.key
        else:
            q = key = None
        if kind == "enum":
            for e in body.named_children:
                if e.type == "enumerator":
                    en = e.child_by_field_name("name")
                    if en is not None:
                        ename = text(src, en)
                        eq = f"{q}::{ename}" if q and n.children and any(c.type in ("class", "struct") for c in n.children) else self._qual(ctx, [ename])
                        self._add("enumerator", ename, self._key(ctx, eq, False), eq, e, en, parent=key)
            return
        bc = _first(n, "base_class_clause")
        if bc is not None and key:
            access = None
            for c in bc.children:
                if c.type == "access_specifier":
                    access = text(src, c)
                elif c.type in ("type_identifier", "qualified_identifier", "template_type"):
                    self.f.bases.append((key, re.sub(r"<.*>", "", text(src, c)), c.start_point[0] + 1, access))
                    access = None
        if key is None:
            # anonymous struct/union: members belong to the enclosing scope; still walk for nested types
            c2 = ctx
        else:
            c2 = dict(ctx, cls={"qual": q, "key": key, "kind": kind}, access="private" if kind == "class" else "public")
        for c in body.children:
            if c.type == "access_specifier":
                c2["access"] = text(src, c).strip(": ")
                continue
            if c.type == "field_declaration" and key:
                self.field_decl(c, c2)
            elif c.type in ("function_definition", "template_declaration", "declaration", "class_specifier", "struct_specifier",
                            "union_specifier", "enum_specifier", "type_definition", "alias_declaration", "preproc_if", "preproc_ifdef",
                            "friend_declaration"):
                if c.type == "friend_declaration":
                    continue
                self.decl(c, c2)

    def field_decl(self, n, ctx):
        src = self.src
        ty = n.child_by_field_name("type")
        if ty is not None and ty.type in ("struct_specifier", "union_specifier", "enum_specifier", "class_specifier") and ty.child_by_field_name("body") is not None:
            self.type_spec(ty, ctx)
        txt_types = [c.type for c in n.children]
        for d in n.children_by_field_name("declarator"):
            nm, fd = _declarator_name(src, d)
            if nm is None:
                continue
            if fd is not None:   # method declaration
                scope, name = _qual_parts(src, nm)
                virt = "virtual" in txt_types or any(c.type == "virtual" for c in n.children)
                vs = _first(fd, "virtual_specifier")
                override = vs is not None
                pure = n.child_by_field_name("default_value") is not None and text(src, n.child_by_field_name("default_value")) == "0"
                q = f"{ctx['cls']['qual']}::{name}"
                self.f.decls.append(CDecl(name, q, self.path, nm.start_point[0] + 1, nm.start_point[1], "method",
                                          export=False, access=ctx.get("access"), virtual=virt or override, pure=pure,
                                          override=override, static="static" in [text(src, c) for c in n.children if c.type == "storage_class_specifier"]))
                self.f.decl_pos.add((nm.start_point[0] + 1, nm.start_point[1]))
            elif nm.type in ("field_identifier", "identifier"):
                name = text(src, nm)
                q = f"{ctx['cls']['qual']}::{name}"
                fit = self._add("field", name, f"{ctx['cls']['key']}::{name}", q, n, nm, parent=ctx["cls"]["key"],
                                attrs={"access": ctx.get("access")} if self.f.lang == "cpp" else {})
                if ty is not None:
                    self._type_refs(ty, fit)

    def declaration(self, n, ctx):
        src = self.src
        if n.type == "field_declaration" and ctx.get("cls"):
            self.field_decl(n, ctx)
            return
        storage = [text(src, c) for c in n.children if c.type == "storage_class_specifier"]
        static = "static" in storage
        ty = n.child_by_field_name("type")
        if ty is not None and ty.type in ("struct_specifier", "union_specifier", "enum_specifier", "class_specifier") and ty.child_by_field_name("body") is not None:
            self.type_spec(ty, ctx)
        for d in n.children_by_field_name("declarator"):
            nm, fd = _declarator_name(src, d)
            if nm is None:
                continue
            scope, name = _qual_parts(src, nm)
            if fd is not None:
                prefix = src[n.start_byte:nm.start_byte].decode("utf-8", "replace")
                exp = bool(EXPORT_RE.search(prefix)) and not all(m.group(0) in NOT_EXPORT for m in EXPORT_RE.finditer(prefix))
                q = self._qual(ctx, scope + [name]) if not ctx.get("cls") else f"{ctx['cls']['qual']}::{name}"
                self.f.decls.append(CDecl(name, q, self.path, nm.start_point[0] + 1, nm.start_point[1],
                                          "method" if ctx.get("cls") else "function", export=exp, access=ctx.get("access"),
                                          static=static, extern_c=ctx["extern_c"]))
                self.f.decl_pos.add((nm.start_point[0] + 1, nm.start_point[1]))
                continue
            if "extern" in storage or "typedef" in storage:
                self.f.decl_pos.add((nm.start_point[0] + 1, nm.start_point[1]))
                continue
            if nm.type not in ("identifier", "qualified_identifier", "field_identifier"):
                continue
            q = self._qual(ctx, scope + [name])
            it = self._add("global", name, self._key(ctx, q, static), q, n, nm, static=static,
                           attrs={"const": any(text(src, c) in ("const", "constexpr") for c in n.children if c.type == "type_qualifier")})
            val = d.child_by_field_name("value") if d.type == "init_declarator" else None
            if val is not None:
                self.body(val, it)
            if ty is not None:
                self._type_refs(ty, it)

    def func_def(self, n, ctx):
        src = self.src
        ty = n.child_by_field_name("type")
        if ty is not None and text(src, ty) in ("namespace", "inline namespace"):
            # error recovery sometimes reads `namespace x {` as a function `namespace x() {}`
            d = n.child_by_field_name("declarator")
            body = n.child_by_field_name("body")
            name = text(src, d) if d is not None and d.type in ("identifier", "field_identifier", "type_identifier") else ""
            if body is not None:
                self.container(body, dict(ctx, ns=ctx["ns"] + ([name] if name else []), anon=ctx["anon"] or not name))
            return
        d = n.child_by_field_name("declarator")
        nm, fd = _declarator_name(src, d)
        if nm is None:
            return
        storage = [text(src, c) for c in n.children if c.type == "storage_class_specifier"]
        static = "static" in storage
        scope, name = _qual_parts(src, nm)
        attrs = {}
        if name in TEST_MACROS and fd is not None:
            args = [re.sub(r"\s+", "", text(src, p)) for p in fd.child_by_field_name("parameters").named_children] if fd.child_by_field_name("parameters") is not None else []
            label = ".".join(a.strip('"') for a in args[:2]) or name
            q = f"{name}({label})"
            it = self._add("function", label, f"{self.path}#{q}", q, n, nm, attrs={"test_macro": name})
            body = n.child_by_field_name("body")
            if body is not None:
                self.body(body, it)
            return
        prefix = src[n.start_byte:nm.start_byte].decode("utf-8", "replace")
        if EXPORT_RE.search(prefix) and not all(m.group(0) in NOT_EXPORT for m in EXPORT_RE.finditer(prefix)):
            attrs["export"] = True
        if any(c.type == "virtual" for c in n.children):
            attrs["virtual"] = True
        vs = _first(fd, "virtual_specifier") if fd is not None else None
        if vs is not None:
            attrs["override"] = True
            attrs["virtual"] = True
        if ctx["extern_c"]:
            attrs["extern_c"] = True
        sig = _params_sig(src, fd)
        if ctx.get("cls"):
            q = f"{ctx['cls']['qual']}::{name}"
            kind = "method"
            if ctx.get("access") and self.f.lang == "cpp":
                attrs["access"] = ctx["access"]
            it = self._add(kind, name, f"{ctx['cls']['key']}::{name}", q, n, nm, parent=ctx["cls"]["key"], attrs=attrs, sig=sig)
        else:
            q = self._qual(ctx, scope + [name])
            kind = "method" if scope else "function"   # refined later (scope may be a namespace)
            it = self._add(kind, name, self._key(ctx, q, static), q, n, nm, static=static, attrs=attrs, scope=scope,
                           ns=list(ctx["ns"]), sig=sig)
        body = n.child_by_field_name("body")
        if body is not None:
            self.body(body, it)
        # constructor initializer lists
        fil = _first(n, "field_initializer_list")
        if fil is not None:
            self.body(fil, it)

    def _type_refs(self, node, owner):
        stack = [node]
        while stack:
            x = stack.pop()
            if x.type == "type_identifier":
                self.f.type_refs.append((owner, text(self.src, x), x.start_point[0] + 1, x.start_point[1]))
            stack.extend(x.children)

    # ---------------------------------------------------------------- bodies
    def body(self, node, owner):
        src = self.src
        stack = [node]
        while stack:
            x = stack.pop()
            t = x.type
            if t == "call_expression":
                fn = x.child_by_field_name("function")
                if fn is not None:
                    if fn.type == "identifier":
                        nm = text(src, fn)
                        self.f.calls.append((owner, "name", nm, nm, fn.start_point[0] + 1, fn.start_point[1]))
                    elif fn.type == "qualified_identifier":
                        scope, nm = _qual_parts(src, fn)
                        nn = fn
                        while nn.type == "qualified_identifier" and nn.child_by_field_name("name") is not None:
                            nn = nn.child_by_field_name("name")
                        self.f.calls.append((owner, "qualified", "::".join(scope + [nm]), nm, nn.start_point[0] + 1, nn.start_point[1]))
                    elif fn.type == "field_expression":
                        fld = fn.child_by_field_name("field")
                        if fld is not None:
                            nm = text(src, fld)
                            self.f.calls.append((owner, "member", nm, nm, fld.start_point[0] + 1, fld.start_point[1]))
                    elif fn.type == "template_function":
                        nmn = fn.child_by_field_name("name")
                        if nmn is not None:
                            nm = text(src, nmn)
                            self.f.calls.append((owner, "name", nm, nm.split("::")[-1], nmn.start_point[0] + 1, nmn.start_point[1]))
                    ftxt = re.sub(r"\s+", "", text(src, fn))
                    if ftxt in GETENV:
                        args = x.child_by_field_name("arguments")
                        if args is not None and args.named_children:
                            a0 = args.named_children[0]
                            k = string_value(src, a0) if a0.type == "string_literal" else None
                            if k is None and a0.type == "concatenated_string":
                                k = "".join(string_value(src, s) or "" for s in a0.named_children if s.type == "string_literal") or None
                            if ftxt in ("_dupenv_s", "getenv_s") and len(args.named_children) >= 3:
                                k = string_value(src, args.named_children[-1])
                            if k:
                                self.f.env.append((k, x.start_point[0] + 1, owner))
            elif t == "identifier":
                p = x.parent
                if p is not None and p.type != "qualified_identifier" and not (p.type == "call_expression" and p.child_by_field_name("function") == x):
                    if p.type not in ("init_declarator", "parameter_declaration", "pointer_declarator", "array_declarator",
                                      "function_declarator", "declaration") or (p.type == "init_declarator" and p.child_by_field_name("value") == x):
                        self.f.idents.append((owner, text(src, x), x.start_point[0] + 1, x.start_point[1]))
            elif t == "type_identifier":
                self.f.type_refs.append((owner, text(src, x), x.start_point[0] + 1, x.start_point[1]))
            elif t in ("lambda_expression",):
                pass
            stack.extend(x.children)


C_KEYWORDS = {"if", "while", "for", "switch", "return", "sizeof", "do", "else", "case", "defined", "_Alignof",
              "alignof", "decltype", "typeof", "__typeof__", "__attribute__", "__declspec", "static_assert",
              "_Static_assert", "catch", "noexcept", "throw", "new", "delete", "operator"}
TOP_CALL = re.compile(r"([A-Za-z_]\w*)\s*\(((?:[^()]|\([^()]*\))*)\)\s*;?\s*(?://.*|/\*.*\*/)?$")
HEAD_FN = re.compile(r"([A-Za-z_]\w*)\s*\((?:[^()]|\([^()]*\))*\)\s*(?:const\s*)?$")
CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
# a function definition inside a macro value: `int get_##name(void) {`, `static void fn(int x) {`
GEN_DEF = re.compile(r"(?:^|[\s*;}])((?:[A-Za-z_]\w*)(?:\s*##\s*[A-Za-z_]\w*)*)\s*\(([^;{}()]*(?:\([^()]*\)[^;{}()]*)*)\)\s*\{")
LEX = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|^[ \t]*#(?:\\\n|[^\n])*|[{};]", re.S | re.M)


def generated_names(params: list[str], value: str, args: list[str]) -> list[tuple[str, bool, str]]:
    """Functions a function-like macro defines when expanded with `args`: [(name, static, parameter types)]. `##`
    pastes tokens, parameters are replaced by their argument."""
    sub = dict(zip(params, (a.strip() for a in args)))
    out = []
    for m in GEN_DEF.finditer(value):
        expr = m.group(1)
        parts = [x.strip() for x in expr.split("##")]
        if len(parts) == 1 and parts[0] in C_KEYWORDS:
            continue
        if not any(p in sub for p in parts) or (len(parts) == 1 and m.start(1) == len(value) - len(value.lstrip())):
            continue                 # a name that does not depend on the arguments, or no return type before it
        name = "".join(sub.get(p, p) for p in parts)
        if re.fullmatch(r"[A-Za-z_]\w*", name) and name not in C_KEYWORDS:
            ptxt = re.sub(r"\s*##\s*", "", re.sub(r"[A-Za-z_]\w*", lambda q: sub.get(q.group(0), q.group(0)), m.group(2)))
            sig = []
            for prm in (x.strip() for x in ptxt.split(",") if x.strip()):
                if prm == "void":
                    continue
                prm = re.sub(r"\s*([*&])\s*", r"\1 ", prm).strip()
                ws = prm.split()
                ty = " ".join(ws[:-1]) if len(ws) > 1 and re.fullmatch(r"\w+", ws[-1]) else prm
                sig.append(re.sub(r"\b(const|struct|volatile)\b\s*", lambda q: q.group(0) if q.group(1) == "struct" else "", ty).replace(" *", "*").replace("* ", "*").strip())
            out.append((name, "static" in value[:m.start(1)].split(";")[-1].split("}")[-1], ",".join(sig)))
    return out


def _unwrap(chunk: str) -> str:
    """`HEAP_EXPORT(void heap_insert(struct heap* h)) {` -> the wrapper macro and its parentheses blanked (same
    length), so the declaration inside parses as the definition's head."""
    m = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*\(", chunk)
    b = chunk.find("{")
    if not m or b < 0:
        return chunk
    depth, close = 0, None
    for i in range(m.end() - 1, b):
        if chunk[i] == "(":
            depth += 1
        elif chunk[i] == ")":
            depth -= 1
            if depth == 0:
                close = i
                break
    inner = chunk[m.end():close] if close else ""
    if close is None or "(" not in inner or chunk[close + 1:b].strip():
        return chunk
    return " " * m.end() + inner + " " + chunk[close + 1:]


def _block_spans_before(txt: str, line_at, line: int):
    """Line spans of the file-level blocks that end before `line`."""
    for a, z in _toplevel_blocks(txt):
        if line_at(z) < line:
            yield line_at(a), line_at(z)


def _line_index(txt: str):
    import bisect
    nl = [i for i, ch in enumerate(txt) if ch == "\n"]
    return lambda pos: bisect.bisect_right(nl, pos - 1) + 1


def _toplevel_blocks(txt: str):
    """(start, end) offsets of file-level constructs that contain a brace block (a definition: head ... `}`),
    skipping comments, strings and preprocessor lines; stray closing braces are ignored."""
    depth, start, last_end = 0, None, 0
    pp = []                    # per #if level: True while inside an #elif / #else branch (its braces are not counted)
    for m in LEX.finditer(txt):
        tok = m.group(0)
        if tok.lstrip().startswith("#"):
            d = re.match(r"\s*#\s*(\w*)", tok).group(1)
            if d in ("if", "ifdef", "ifndef"):
                pp.append(False)
            elif d in ("elif", "else", "elifdef", "elifndef") and pp:
                pp[-1] = True
            elif d == "endif" and pp:
                pp.pop()
        if any(pp):
            continue
        if len(tok) != 1 or tok not in "{};":            # comment, string, preprocessor line
            if depth == 0 and start is None:
                if tok[:1] in "\"'":
                    start = m.start()
                else:
                    last_end = m.end()
            continue
        if depth == 0 and start is None:
            seg = txt[last_end:m.start()]
            start = last_end + len(seg) - len(seg.lstrip())
        if tok == "{":
            depth += 1
        elif tok == "}":
            if depth == 0:
                start, last_end = None, m.end()
                continue
            depth -= 1
            if depth == 0:
                yield start, m.end() - 1
                start, last_end = None, m.end()
        elif depth == 0:
            start, last_end = None, m.end()


def extract(path: str, src: bytes, lang: str, module: str, blank: re.Pattern | None = None) -> CFile:
    return Extractor(path, src, lang, module, blank).run()


# ---------------------------------------------------------------- annotation macros
# Declaration annotations hidden behind macros (FOO_API, FOO_CONSTEXPR, FOO_INLINE, FOO_NODISCARD ...) are the main
# reason tree-sitter mis-parses real C/C++ headers. They are blanked (same byte length, so every position is kept)
# before parsing: names that match these suffixes, plus every object-like macro the project defines whose value is
# empty or only attributes/keywords.
ANNOT_NAME = re.compile(rb"^[A-Z][A-Z0-9_]*_(?:API|EXPORT|EXPORTS|IMPORT|INLINE|FORCEINLINE|FORCE_INLINE|ALWAYS_INLINE|NOINLINE|"
                        rb"CONSTEXPR\d*|CONSTEXPR_\w+|CONSTEVAL|NODISCARD|NOEXCEPT|NORETURN|DEPRECATED|MAYBE_UNUSED|UNUSED|"
                        rb"NO_UNIQUE_ADDRESS|VISIBILITY\w*|VISIBLE|HIDDEN|EXTERN|EXTERN_C|LOCAL|PUBLIC_API|STATIC_INLINE|"
                        rb"INLINE_VAR|ATTRIBUTE|ATTR|WARN_UNUSED_RESULT|MUST_USE_RESULT|PURE|HOT|COLD)_?$")
ANNOT_VALUE = re.compile(rb"^(?:\s|__attribute__\s*\(\(.*?\)\)|__declspec\s*\([^)]*\)|\[\[[^\]]*\]\]|inline|__inline|"
                         rb"__inline__|__forceinline|static|extern|\"C\"|constexpr|consteval|noexcept|explicit|virtual|"
                         rb"__cdecl|__stdcall|__fastcall|WINAPI|[A-Z][A-Z0-9_]*_(?:API|EXPORT|INLINE|CONSTEXPR\d*))*$")
NS_WRAP = re.compile(rb"^\s*(?:(?:inline\s+)?namespace\s+[\w:]+\s*\{\s*|\}\s*)+$")
DEFINE_RE = re.compile(rb"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(?![\w(])[ \t]*(.*?)\\?$", re.M)
WORD_RE = re.compile(rb"\b[A-Z][A-Z0-9_]{2,}\b")


def annotation_macros(sources) -> set:
    """Collect project macros that are pure declaration annotations (see above)."""
    names, values_bad, numeric = set(), set(), set()
    for src in sources:
        for m in DEFINE_RE.finditer(src):
            nm, val = m.group(1), m.group(2).strip()
            if m.group(0).rstrip().endswith(b"\\"):
                # multi-line value: only `namespace a { inline namespace b {` / `}}` wrappers count (masked in pairs)
                end = src.find(b"\n", m.end())
                full = m.group(2)
                pos = m.end()
                while full.rstrip().endswith(b"\\") or src[pos - 1:pos] == b"\\":
                    nxt = src.find(b"\n", pos + 1)
                    if nxt < 0:
                        break
                    full += b" " + src[pos + 1:nxt].rstrip(b"\\")
                    pos = nxt
                    if not src[nxt - 1:nxt] == b"\\":
                        break
                if NS_WRAP.match(full.replace(b"\\", b" ")):
                    names.add(nm)
                else:
                    values_bad.add(nm)
                continue
            if ANNOT_VALUE.match(val):
                names.add(nm)
            else:
                values_bad.add(nm)
                if re.match(rb"^\(?-?\d", val):
                    numeric.add(nm)
        for w in set(WORD_RE.findall(src)):
            if ANNOT_NAME.match(w):
                names.add(w)
    # a macro that is an annotation in one #if branch but a value in another stays only if its name says so
    return {n for n in names if n not in numeric and (n not in values_bad or ANNOT_NAME.match(n))}


def annotation_regex(names: set) -> re.Pattern | None:
    if not names:
        return None
    alt = b"|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rb"\b(?:" + alt + rb")\b")


def mask_annotations(src: bytes, rx: re.Pattern) -> bytes:
    out, cont = [], False
    for line in src.split(b"\n"):
        directive = cont or line.lstrip().startswith(b"#")
        cont = directive and line.rstrip().endswith(b"\\")
        out.append(line if directive else rx.sub(lambda m: b" " * (m.end() - m.start()), line))
    return b"\n".join(out)


MACRO_STMT = re.compile(rb"^[A-Z][A-Z0-9_]*[ \t]*\(", re.M)


def mask_macro_statements(src: bytes) -> bytes:
    """File-level `NAME(...);` statements of an upper-case macro starting in column 0 blanked (same length, newlines
    kept). Strings and comments in the arguments are skipped while matching the parentheses."""
    out = bytearray(src)
    for m in MACRO_STMT.finditer(src):
        i, depth, n = m.end() - 1, 0, len(src)
        while i < n:
            ch = src[i:i + 1]
            if ch in (b'"', b"'"):
                j = i + 1
                while j < n and src[j:j + 1] not in (ch, b"\n"):
                    j += 2 if src[j:j + 1] == b"\\" else 1
                i = j + 1
                continue
            if src[i:i + 2] == b"/*":
                j = src.find(b"*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if src[i:i + 2] == b"//":
                j = src.find(b"\n", i)
                i = n if j < 0 else j
                continue
            if ch == b"(":
                depth += 1
            elif ch == b")":
                depth -= 1
                if depth == 0:
                    break
            elif ch in (b"{", b"}", b";"):
                i = n
                break
            i += 1
        if i >= n:
            continue
        rest = re.match(rb"[ \t]*;", src[i + 1:i + 40])
        if not rest:
            continue
        end = i + 1 + rest.end()
        for k in range(m.start(), end):
            if out[k] != 10:
                out[k] = 32
    return bytes(out)


def _error_lines(root) -> int:
    from ...core.syntax_errors import tree_spans
    return sum(b - a + 1 for a, b in tree_spans(root))


# Clang thread-safety annotations after a declarator: `void Write() ABSL_EXCLUSIVE_LOCKS_REQUIRED(&mu_) {`,
# `bool done_ GUARDED_BY(mu_) = false;`, `GTEST_LOCK_EXCLUDED_(mutex_)`. tree-sitter reads them as the declarator
# (a method `mu_`), so they are blanked (same length) like the name-only annotation macros.
LOCK_ANNOT = re.compile(
    rb"\b(?:(?:[A-Z][A-Z0-9]*_)*(?:EXCLUSIVE_LOCKS_REQUIRED|SHARED_LOCKS_REQUIRED|LOCKS_EXCLUDED|LOCK_EXCLUDED|"
    rb"LOCK_RETURNED|PT_GUARDED_BY|GUARDED_BY|ACQUIRED_BEFORE|ACQUIRED_AFTER|EXCLUSIVE_LOCK_FUNCTION|SHARED_LOCK_FUNCTION|"
    rb"UNLOCK_FUNCTION|EXCLUSIVE_TRYLOCK_FUNCTION|SHARED_TRYLOCK_FUNCTION|ASSERT_EXCLUSIVE_LOCK|ASSERT_SHARED_LOCK|"
    rb"CAPABILITY)|"
    rb"[A-Z][A-Z0-9]*_(?:REQUIRES_SHARED|REQUIRES|EXCLUDES|ACQUIRE_SHARED|ACQUIRE|RELEASE_SHARED|RELEASE|TRY_ACQUIRE|"
    rb"RETURN_CAPABILITY|ASSERT_CAPABILITY))_?[ \t]*\((?:[^()\n]|\([^()\n]*\))*\)")


LOCK_NAME = re.compile(rb"\b(?:[A-Z][A-Z0-9]*_)*(?:SCOPED_LOCKABLE|LOCKABLE|SCOPED_CAPABILITY|NO_THREAD_SAFETY_ANALYSIS)_?\b(?![ \t]*\()")


def mask_lock_annotations(src: bytes) -> bytes:
    if b"LOCK" not in src and b"GUARDED_BY" not in src and b"_REQUIRES" not in src and b"_EXCLUDES" not in src \
            and b"_ACQUIRE" not in src and b"_RELEASE" not in src and b"CAPABILITY" not in src:
        return src
    out = bytearray(src)
    for m in LOCK_ANNOT.finditer(src):
        ls = src.rfind(b"\n", 0, m.start()) + 1
        before = src[ls:m.start()].rstrip()
        if not before.strip() and ls > 0:
            # on a line of its own after the declarator: `void Mock::Allow(uintptr_t obj)\n    GTEST_LOCK_EXCLUDED_(mu) {`
            pl = src[src.rfind(b"\n", 0, ls - 1) + 1:ls - 1].rstrip()
            before = pl if re.search(rb"(?:\)|\bconst|\boverride|\bnoexcept)$", pl) and not pl.lstrip().startswith(b"#") else b""
        if before.lstrip().startswith(b"#") or not before or not re.search(rb"[\w)&*\]]$", before):
            continue        # a directive, or not after a declarator (a statement / call of its own)
        if re.search(rb"\b(?:return|if|while|case)\b[^;{}]*$", before):
            continue
        out[m.start():m.end()] = b" " * (m.end() - m.start())
    for m in LOCK_NAME.finditer(src):       # `class SCOPED_LOCKABLE MutexLock {`, `void f() NO_THREAD_SAFETY_ANALYSIS {`
        ls = src.rfind(b"\n", 0, m.start()) + 1
        if not src[ls:m.start()].lstrip().startswith(b"#"):
            out[m.start():m.end()] = b" " * (m.end() - m.start())
    return bytes(out)

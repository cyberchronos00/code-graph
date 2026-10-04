"""Syntactic layer for Rust (tree-sitter-rust): items with stable keys and ranges, attributes, cfg gates,
visibility, impls, unsafe blocks, FFI, env keys, async entry points, axum/actix-style routes, and (for the
heuristic mode without rust-analyzer) call sites, type references and `use` maps.

Keys follow Rust paths so the SCIP layer can be matched onto them by (file, line, name):
  function  kv_core::util::checksum            method   kv_core::store::MemoryStore::len
  trait-impl method  kv_core::store::<MemoryStore as Store>::get
  trait method       kv_core::store::Store::get   type     kv_core::store::MemoryStore (struct/enum/union/trait/type_alias)
  field / variant    kv_core::store::MemoryStore::map
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..native.ts import parser, string_value, text

ITEM_TYPES = {"function_item", "function_signature_item", "struct_item", "enum_item", "union_item", "trait_item",
              "type_item", "const_item", "static_item", "mod_item", "impl_item", "foreign_mod_item", "macro_definition"}
HTTP_VERBS = {"get", "post", "put", "delete", "patch", "head", "options", "trace", "any"}
ENTRY_ATTRS = {  # attribute path -> (entry kind, runtime)
    "tokio::main": ("main", "tokio"), "async_std::main": ("main", "async-std"), "actix_web::main": ("main", "actix"),
    "actix_rt::main": ("main", "actix"), "smol_potat::main": ("main", "smol"), "rocket::main": ("main", "rocket"),
    "rocket::launch": ("main", "rocket"), "launch": ("main", "rocket"), "main": ("main", None),
    "test": ("test", None), "tokio::test": ("test", "tokio"), "async_std::test": ("test", "async-std"),
    "actix_rt::test": ("test", "actix"), "actix_web::test": ("test", "actix"), "rstest": ("test", None),
    "test_case": ("test", None), "quickcheck": ("test", None), "proptest": ("test", None), "bench": ("bench", None),
    "wasm_bindgen_test": ("test", None), "sqlx::test": ("test", None),
}
_BLANK = re.compile(rb"[^\n]")
ATTR_FN_REF_ATTRS = {"serde", "arg", "clap", "validate", "builder"}
ATTR_FN_REF = re.compile(r'\b(default|with|serialize_with|deserialize_with|skip_serializing_if|value_parser|getter|custom)\s*=\s*(?:"([A-Za-z_][\w:]*)"|([A-Za-z_][\w:]*)\b(?!\s*[(!]))')
ROUTE_ATTRS = {"get", "post", "put", "delete", "patch", "head", "options", "route"}  # actix-web / rocket
ENV_CALLS = re.compile(r"(^|::)(env::var|env::var_os|dotenvy?::var|env::var_os)$")


@dataclass
class RItem:
    kind: str
    name: str
    key: str
    file: str
    line: int                 # 1-based line of the name
    col: int                  # 0-based column of the name
    start: int
    end: int
    module: str
    vis: str | None = None
    attrs: list = field(default_factory=list)
    cfgs: list = field(default_factory=list)      # [(predicate text, line)] incl. inherited
    in_test: bool = False
    unsafe: bool = False
    is_async: bool = False
    abi: str | None = None
    parent: str | None = None                      # owning type / trait / impl prefix key
    impl_self: str | None = None
    impl_trait: str | None = None                  # trait text, e.g. From<u8>
    impl_trait_pos: tuple | None = None            # (line, col) of the trait name (for SCIP lookup)
    impl_self_pos: tuple | None = None
    doc: str | None = None
    pub_chain: bool = False                        # item and every enclosing module are `pub`


@dataclass
class ModDecl:
    name: str
    line: int
    vis: str | None
    path_attr: str | None
    cfgs: list
    in_test: bool
    module: str              # module key of the declared child
    pub_chain: bool


@dataclass
class RFile:
    path: str
    crate: str
    module: str
    items: list = field(default_factory=list)
    mods: list = field(default_factory=list)
    unsafe_blocks: list = field(default_factory=list)   # (line, owner key)
    macro_bodies: int = 0                               # item-level macro bodies indexed as items
    attr_refs: list = field(default_factory=list)       # (owner key, fn/module path, line, attribute key)
    env: list = field(default_factory=list)             # (key, line, owner, how)
    routes: list = field(default_factory=list)          # (method, path, handler text, line, col, owner, framework)
    cfg_regions: list = field(default_factory=list)     # (start, end, predicate, attr line, owner)
    use_ranges: list = field(default_factory=list)      # (start, end)
    use_map: dict = field(default_factory=dict)         # (module key, alias) -> path
    pub_uses: list = field(default_factory=list)        # (module key, path, line)
    calls: list = field(default_factory=list)           # (owner, form, path text, name, line, col)
    type_refs: list = field(default_factory=list)       # (owner, name, line, col)
    inner_cfgs: list = field(default_factory=list)      # #![cfg(..)] at file top
    error_spans: list = field(default_factory=list)     # [first, last] lines tree-sitter could not parse (#73)
    file_doc: str | None = None
    lines: list = field(default_factory=list)
    test_macros: dict = field(default_factory=dict)     # macro_rules! name -> {"pos", "calls"} for test-generating macros
    macro_tests: int = 0                                # tests generated by invocations of such macros (#106)
    macro_calls: list = field(default_factory=list)     # calls of a test macro's own body, per generated test


def _vis(src, n) -> str | None:
    for c in n.children:
        if c.type == "visibility_modifier":
            return text(src, c)
    return None


def _attr_path(src, a) -> tuple[str, str]:
    """attribute_item -> (path text, args text)."""
    at = next((c for c in a.children if c.type == "attribute"), None)
    if at is None:
        return "", ""
    path = at.children[0] if at.children else None
    args = at.child_by_field_name("arguments")
    val = at.child_by_field_name("value")
    return (text(src, path) if path is not None else ""), (text(src, args) if args is not None else (text(src, val) if val is not None else ""))


def _type_name(src, t) -> str:
    """Self type of an impl -> short name (generic args and path prefix dropped)."""
    if t is None:
        return "?"
    while t.type in ("reference_type", "pointer_type") and t.child_by_field_name("type") is not None:
        t = t.child_by_field_name("type")
    if t.type == "generic_type":
        t = t.child_by_field_name("type") or t
    s = text(src, t)
    s = re.sub(r"\s+", " ", s)
    return s.split("::")[-1] if "<" not in s else s


def _trait_text(src, t) -> str:
    s = re.sub(r"\s+", "", text(src, t))
    if s.startswith("::"):
        s = s[2:]
    # drop the path prefix outside generic arguments: fmt::Display -> Display, a::From<b::C> -> From<b::C>
    head, lt, rest = s.partition("<")
    return head.split("::")[-1] + lt + rest


def _name_pos(n):
    nm = n.child_by_field_name("name")
    if nm is None:
        return None, n.start_point[0] + 1, n.start_point[1]
    return nm, nm.start_point[0] + 1, nm.start_point[1]


def parse_use(src, n, prefix: str = "") -> list[tuple[str, str]]:
    """use tree -> [(alias, full path)]; glob imports give ('*', path)."""
    out = []
    t = n.type
    if t in ("identifier", "self", "crate", "super", "metavariable"):
        p = f"{prefix}::{text(src, n)}" if prefix else text(src, n)
        alias = text(src, n)
        if alias == "self" and prefix:
            alias, p = prefix.split("::")[-1], prefix
        out.append((alias, p))
    elif t == "scoped_identifier":
        p = re.sub(r"\s+", "", text(src, n))
        out.append((p.split("::")[-1], f"{prefix}::{p}" if prefix else p))
    elif t == "use_as_clause":
        path = n.child_by_field_name("path")
        alias = n.child_by_field_name("alias")
        p = re.sub(r"\s+", "", text(src, path))
        out.append((text(src, alias), f"{prefix}::{p}" if prefix else p))
    elif t == "use_wildcard":
        p = re.sub(r"\s+", "", text(src, n))[:-3].rstrip(":")
        out.append(("*", f"{prefix}::{p}" if prefix and p else (p or prefix)))
    elif t == "scoped_use_list":
        path = n.child_by_field_name("path")
        lst = n.child_by_field_name("list")
        p = re.sub(r"\s+", "", text(src, path)) if path is not None else ""
        np = f"{prefix}::{p}" if prefix and p else (p or prefix)
        if lst is not None:
            for c in lst.named_children:
                out += parse_use(src, c, np)
    elif t == "use_list":
        for c in n.named_children:
            out += parse_use(src, c, prefix)
    return out


class Extractor:
    def __init__(self, path: str, src: bytes, crate: str, module: str, cfgs: list, in_test: bool, pub_chain: bool,
                 test_macros: dict | None = None):
        self.src = src
        self.test_macros = test_macros or {}
        self.f = RFile(path, crate, module)
        self.base_cfgs = list(cfgs)
        self.base_test = in_test
        self.base_pub = pub_chain
        self.consts: dict[str, str] = {}

    # ---------------------------------------------------------------- entry
    def run(self) -> RFile:
        tree = parser("rust").parse(self.src)
        root = tree.root_node
        if root.has_error:
            from ...core.syntax_errors import tree_spans
            self.f.error_spans = tree_spans(root)
        self.f.lines = self.src.decode("utf-8", "replace").split("\n")
        # pass 1: string consts (for env::var(CONST))
        for n in root.children:
            if n.type in ("const_item", "static_item"):
                v = n.child_by_field_name("value")
                nm = n.child_by_field_name("name")
                sv = string_value(self.src, v) if v is not None else None
                if nm is not None and sv is not None:
                    self.consts[text(self.src, nm)] = sv
        cfgs, test = list(self.base_cfgs), self.base_test
        docs = []
        for c in root.children:
            if c.type == "inner_attribute_item":
                p, a = _attr_path(self.src, c)
                if p == "cfg":
                    pred = a[1:-1] if a.startswith("(") else a
                    if pred.strip() == "test":
                        test = True
                    else:
                        cfgs.append((pred, c.start_point[0] + 1))
                        self.f.inner_cfgs.append((pred, c.start_point[0] + 1))
            elif c.type == "line_comment" and text(self.src, c).startswith("//!"):
                docs.append(text(self.src, c)[3:].strip())
        self.f.file_doc = "\n".join(docs) or None
        self.container(root, self.f.module, cfgs, test, self.base_pub, None)
        return self.f

    # ---------------------------------------------------------------- items
    def container(self, node, module: str, cfgs: list, in_test: bool, pub_chain: bool, ctx: dict | None):
        """Walk a source_file / declaration_list: attach preceding attributes + doc comments to each item."""
        attrs, docs = [], []
        for c in node.children:
            t = c.type
            if t == "attribute_item":
                attrs.append(c)
                continue
            if t == "line_comment" or t == "block_comment":
                s = text(self.src, c)
                if s.startswith("///") or s.startswith("/**"):
                    docs.append(s.lstrip("/*! ").rstrip("*/ "))
                continue
            if t in ITEM_TYPES or t == "use_declaration" or t in ("field_declaration", "enum_variant"):
                self.item(c, attrs, docs, module, cfgs, in_test, pub_chain, ctx)
            elif ctx is None and self.test_macros and self._macro_test(c, module, cfgs):
                pass
            elif t == "macro_invocation" and (ctx is None or ctx.get("kind") in ("impl", "trait_impl", "trait")):
                self.macro_item(c, module, cfgs, in_test, pub_chain, ctx)
            attrs, docs = [], []

    def _macro_test(self, n, module, cfgs) -> bool:
        """`rgtest!(name, |dir, cmd| { ... });` with a project `macro_rules!` that expands to `#[test] fn $name()`
        (#106): a test function `module::name` at the invocation. Its calls are the macro body's own calls
        (`crate::util::setup(..)`) and the calls in the invocation's arguments (the closure)."""
        if n.type == "expression_statement" and n.children and n.children[0].type == "macro_invocation":
            n = n.children[0]
        if n.type != "macro_invocation" or not n.children:
            return False
        info = self.test_macros.get(text(self.src, n.children[0]).split("::")[-1])
        tt = next((c for c in n.children if c.type == "token_tree"), None)
        if info is None or tt is None:
            return False
        args, cur = [], []
        for c in tt.children[1:-1]:
            if c.type == ",":
                args.append(cur)
                cur = []
            else:
                cur.append(c)
        args.append(cur)
        a = args[info["pos"]] if info["pos"] < len(args) else []
        if len(a) != 1 or a[0].type != "identifier":
            return False
        name = text(self.src, a[0])
        it = self._add("function", name, f"{module}::{name}", n, module, None, [("test", "")], cfgs, True, [],
                       pos=(a[0].start_point[0] + 1, a[0].start_point[1]))
        it.pub_chain = False
        it.attrs.append("macro_test")
        line, col = n.start_point[0] + 1, n.start_point[1]
        for path in info["calls"]:
            self.f.macro_calls.append((it.key, "path", path, path.split("::")[-1], line, col))
        self._tt_calls(tt, it)
        self.f.macro_tests += 1
        return True

    def _test_macro_def(self, n, name):
        """A `macro_rules!` with a rule expanding to `#[test] fn $x(..)`: which matcher binding names the test, and the
        calls the expansion makes (paths, not metavariables or macros)."""
        for rule in (c for c in n.children if c.type == "macro_rule"):
            left, right = rule.child_by_field_name("left"), rule.child_by_field_name("right")
            if left is None or right is None:
                continue
            body = text(self.src, right)
            m = re.search(r"#\s*\[\s*test\s*\]\s*(?:#\s*\[[^\]]*\]\s*)*(?:async\s+)?fn\s+\$(\w+)", body)
            if not m:
                continue
            binds = [text(self.src, c) for c in left.children if c.type == "token_binding_pattern"]
            pos = next((i for i, bd in enumerate(binds) if bd.replace(" ", "") == f"${m.group(1)}:ident"), None)
            if pos is None:
                continue
            calls = []
            for cm in re.finditer(r"(?<![\w$:!.])((?:[A-Za-z_]\w*::)*[A-Za-z_]\w*)\s*\(", body[m.end():]):
                p = cm.group(1)
                if p.split("::")[-1] not in RUST_KEYWORDS and p not in calls:
                    calls.append(p)
            self.f.test_macros[name] = {"pos": pos, "calls": calls}
            return

    def _attr_info(self, attrs):
        out_attrs, cfg_add, test = [], [], False
        self._pending_refs = []
        for a in attrs:
            p, args = _attr_path(self.src, a)
            out_attrs.append((p, args))
            if p in ATTR_FN_REF_ATTRS and args:
                # #[serde(default = "path", with = "module", ...)] / #[arg(value_parser = path)]: fn named in an attribute
                for m in ATTR_FN_REF.finditer(args):
                    self._pending_refs.append((m.group(2) or m.group(3), a.start_point[0] + 1, m.group(1)))
            if p == "cfg":
                pred = args[1:-1] if args.startswith("(") else args
                if pred.strip() == "test":
                    test = True
                else:
                    cfg_add.append((pred, a.start_point[0] + 1))
        return out_attrs, cfg_add, test

    def _add(self, kind, name, key, n, module, vis, attrs, cfgs, in_test, docs, **kw) -> RItem:
        nm, line, col = _name_pos(n)
        if kw.get("pos"):
            line, col = kw.pop("pos")
        it = RItem(kind, name, key, self.f.path, line, col, n.start_point[0] + 1, n.end_point[0] + 1, module, vis,
                   [p for p, _ in attrs] if attrs and isinstance(attrs[0], tuple) else list(attrs or []), list(cfgs),
                   in_test, doc="\n".join(docs) or None, **kw)
        self.f.items.append(it)
        if getattr(self, "_pending_refs", None):
            for path, line, how in self._pending_refs:
                self.f.attr_refs.append((it.key, path, line, how))
            self._pending_refs = []
        return it

    def item(self, n, attr_nodes, docs, module, cfgs, in_test, pub_chain, ctx):
        src = self.src
        attrs, cfg_add, test = self._attr_info(attr_nodes)
        cfgs = cfgs + cfg_add
        in_test = in_test or test
        t = n.type
        vis = _vis(src, n)
        is_pub = bool(vis) and vis.strip() == "pub"
        # inside a trait every method is as public as the trait; impl items carry their own visibility
        chain = pub_chain and (is_pub or (ctx is not None and ctx.get("kind") in ("trait", "trait_impl")))
        if t == "use_declaration":
            self.f.use_ranges.append((n.start_point[0] + 1, n.end_point[0] + 1))
            arg = n.child_by_field_name("argument")
            if arg is not None:
                for alias, path in parse_use(src, arg):
                    self.f.use_map.setdefault((module, alias), path) if alias != "*" else self.f.use_map.setdefault((module, "*" + path), path)
                    if is_pub:
                        self.f.pub_uses.append((module, path if alias != "*" else path + "::*", n.start_point[0] + 1))
            return
        if t in ("function_item", "function_signature_item"):
            nm = n.child_by_field_name("name")
            if nm is None:
                return
            name = text(src, nm)
            mods = next((c for c in n.children if c.type == "function_modifiers"), None)
            mtxt = text(src, mods) if mods is not None else ""
            abi = None
            if mods is not None:
                for c in mods.children:
                    if c.type == "extern_modifier":
                        sv = next((string_value(src, x) for x in c.children if x.type == "string_literal"), None)
                        abi = sv or "C"
            if ctx and ctx.get("kind") == "foreign":
                kind, key = "ffi", f"{module}::{name}"
                abi = ctx.get("abi")
            elif ctx and ctx.get("kind") in ("impl", "trait_impl", "trait"):
                kind, key = "method", f"{ctx['prefix']}::{name}"
            elif ctx and ctx.get("kind") == "fn":
                kind, key = "function", f"{ctx['prefix']}::{name}"
            else:
                kind, key = "function", f"{module}::{name}"
            it = self._add(kind, name, key, n, module, vis, attrs, cfgs, in_test, docs, unsafe="unsafe" in mtxt.split(),
                           is_async="async" in mtxt.split(), abi=abi, parent=(ctx or {}).get("prefix"),
                           impl_self=(ctx or {}).get("self"), impl_trait=(ctx or {}).get("trait"),
                           impl_trait_pos=(ctx or {}).get("trait_pos"), impl_self_pos=(ctx or {}).get("self_pos"))
            it.pub_chain = chain if not (ctx and ctx.get("kind") == "fn") else False
            body = n.child_by_field_name("body")
            if body is not None:
                self.body(body, it, module, cfgs, in_test)
            return
        if t in ("struct_item", "enum_item", "union_item", "trait_item", "type_item"):
            nm = n.child_by_field_name("name")
            if nm is None:
                return
            name = text(src, nm)
            kind = {"struct_item": "struct", "enum_item": "enum", "union_item": "union", "trait_item": "trait", "type_item": "type_alias"}[t]
            prefix = ctx["prefix"] if ctx and ctx.get("kind") == "fn" else module
            if ctx and ctx.get("kind") in ("impl", "trait_impl", "trait"):  # associated type
                prefix = ctx["prefix"]
            key = f"{prefix}::{name}"
            it = self._add(kind, name, key, n, module, vis, attrs, cfgs, in_test, docs, parent=(ctx or {}).get("prefix"))
            it.pub_chain = chain
            if "unsafe" in [c.type for c in n.children]:
                it.unsafe = True
            body = n.child_by_field_name("body")
            if body is not None:
                if kind == "trait":
                    self.container(body, module, cfgs, in_test, chain, {"kind": "trait", "prefix": key, "trait": name})
                elif kind in ("struct", "union"):
                    self.fields(body, key, module, cfgs, in_test, chain)
                elif kind == "enum":
                    self.variants(body, key, module, cfgs, in_test, chain)
            return
        if t in ("field_declaration", "enum_variant"):
            return  # handled by fields()/variants()
        if t in ("const_item", "static_item"):
            nm = n.child_by_field_name("name")
            if nm is None:
                return
            name = text(src, nm)
            if ctx and ctx.get("kind") == "foreign":
                kind, key = "ffi", f"{module}::{name}"
            else:
                kind = "const" if t == "const_item" else "static"
                prefix = ctx["prefix"] if ctx else module
                key = f"{prefix}::{name}"
            it = self._add(kind, name, key, n, module, vis, attrs, cfgs, in_test, docs, parent=(ctx or {}).get("prefix"))
            it.pub_chain = chain
            if "mutable_specifier" in [c.type for c in n.children]:
                it.attrs.append("mut")
            v = n.child_by_field_name("value")
            if v is not None:
                self.body(v, it, module, cfgs, in_test)
            return
        if t == "macro_definition":
            nm = n.child_by_field_name("name")
            if nm is None:
                return
            name = text(src, nm)
            it = self._add("macro", name, f"{self.f.crate}::{name}" if any(p == "macro_export" for p, _ in attrs) else f"{module}::{name}",
                           n, module, vis, attrs, cfgs, in_test, docs)
            it.pub_chain = any(p == "macro_export" for p, _ in attrs)
            self._test_macro_def(n, name)
            return
        if t == "mod_item":
            nm = n.child_by_field_name("name")
            if nm is None:
                return
            name = text(src, nm)
            mkey = f"{module}::{name}"
            body = n.child_by_field_name("body")
            path_attr = next((a.strip().lstrip("=").strip().strip('"') for p, a in attrs if p == "path"), None)
            it = self._add("mod", name, mkey, n, module, vis, attrs, cfgs, in_test, docs)
            it.pub_chain = pub_chain and is_pub
            if body is None:
                self.f.mods.append(ModDecl(name, n.start_point[0] + 1, vis, path_attr, cfgs, in_test, mkey, pub_chain and is_pub))
            else:
                self.f.mods.append(ModDecl(name, n.start_point[0] + 1, vis, path_attr, cfgs, in_test, mkey, pub_chain and is_pub))
                self.f.mods[-1].inline = True
                self.container(body, mkey, cfgs, in_test, pub_chain and is_pub, None)
            return
        if t == "impl_item":
            ty = n.child_by_field_name("type")
            tr = n.child_by_field_name("trait")
            self_name = _type_name(src, ty)
            base = ctx["prefix"] if ctx and ctx.get("kind") == "fn" else module
            unsafe_impl = any(c.type == "unsafe" for c in n.children)
            if tr is not None:
                trait = _trait_text(src, tr)
                tnode = tr
                if tnode.type == "generic_type":
                    tnode = tnode.child_by_field_name("type") or tnode
                if tnode.type == "scoped_type_identifier":
                    tnode = tnode.child_by_field_name("name") or tnode
                prefix = f"{base}::<{self_name} as {trait}>"
                c2 = {"kind": "trait_impl", "prefix": prefix, "self": self_name, "trait": trait,
                      "trait_pos": (tnode.start_point[0] + 1, tnode.start_point[1]),
                      "self_pos": self._type_pos(ty), "unsafe_impl": unsafe_impl}
            else:
                prefix = f"{base}::{self_name}"
                c2 = {"kind": "impl", "prefix": prefix, "self": self_name, "self_pos": self._type_pos(ty)}
            body = n.child_by_field_name("body")
            # impl blocks are not nodes; record them as pseudo-items for trait -> impl edges
            self.f.items.append(RItem("impl", self_name, prefix, self.f.path, n.start_point[0] + 1, n.start_point[1],
                                      n.start_point[0] + 1, n.end_point[0] + 1, module, None, [p for p, _ in attrs], list(cfgs),
                                      in_test, unsafe=unsafe_impl, impl_self=self_name, impl_trait=c2.get("trait"),
                                      impl_trait_pos=c2.get("trait_pos"), impl_self_pos=c2.get("self_pos"), pub_chain=pub_chain))
            if body is not None:
                self.container(body, module, cfgs, in_test, pub_chain, c2)
            return
        if t == "foreign_mod_item":
            em = next((c for c in n.children if c.type == "extern_modifier"), None)
            abi = next((string_value(src, x) for x in em.children if x.type == "string_literal"), None) if em is not None else None
            body = n.child_by_field_name("body")
            if body is not None:
                self.container(body, module, cfgs, in_test, False, {"kind": "foreign", "abi": abi or "C", "prefix": module})
            return

    def _type_pos(self, ty):
        if ty is None:
            return None
        t = ty
        while t.type in ("reference_type", "pointer_type", "generic_type") and (t.child_by_field_name("type") is not None):
            t = t.child_by_field_name("type")
        if t.type == "scoped_type_identifier":
            t = t.child_by_field_name("name") or t
        return (t.start_point[0] + 1, t.start_point[1])

    def fields(self, body, owner_key, module, cfgs, in_test, chain):
        attrs = []
        for c in body.children:
            if c.type == "attribute_item":
                attrs.append(c)
                continue
            if c.type == "field_declaration":
                nm = c.child_by_field_name("name")
                if nm is not None:
                    a, cadd, _ = self._attr_info(attrs)
                    it = self._add("field", text(self.src, nm), f"{owner_key}::{text(self.src, nm)}", c, module, _vis(self.src, c), a, cfgs + cadd, in_test, [],
                                   parent=owner_key)
                    it.pub_chain = chain and bool(it.vis)
                    ty = c.child_by_field_name("type")
                    if ty is not None:
                        self._type_refs(ty, it)
            if c.type not in ("line_comment", "block_comment"):
                attrs = []

    def variants(self, body, owner_key, module, cfgs, in_test, chain):
        attrs = []
        for c in body.children:
            if c.type == "attribute_item":
                attrs.append(c)
                continue
            if c.type == "enum_variant":
                nm = c.child_by_field_name("name")
                if nm is not None:
                    a, cadd, _ = self._attr_info(attrs)
                    it = self._add("variant", text(self.src, nm), f"{owner_key}::{text(self.src, nm)}", c, module, None, a, cfgs + cadd, in_test, [],
                                   parent=owner_key)
                    it.pub_chain = chain
                    b = c.child_by_field_name("body")
                    if b is not None and b.type == "field_declaration_list":
                        # struct-like variant `V { a: T }`: fields keyed under the variant
                        self.fields(b, it.key, module, cfgs + cadd, in_test, chain)
                    elif b is not None:
                        self._type_refs(b, it)
            if c.type not in ("line_comment", "block_comment"):
                attrs = []

    def macro_item(self, n, module, cfgs, in_test, pub_chain=True, ctx=None):
        """Item-level macro invocations are not expanded, but a very common pattern wraps ordinary items in a
        macro (`cfg_rt! { pub mod runtime; pub fn spawn() {} }`, `cfg_if!`-like wrappers, `impl X { cfg_io! { fn f() } }`).
        When the macro body parses cleanly as Rust items, index those items in place (same byte offsets, so lines and
        SCIP positions match). Bodies that are not plain items (`lazy_static!`, `bitflags!`, DSLs) are skipped."""
        tt = next((c for c in n.children if c.type == "token_tree"), None)
        if tt is None or tt.end_byte - tt.start_byte < 4 or getattr(self, "_macro_depth", 0) > 3:
            return
        a, b = tt.start_byte + 1, tt.end_byte - 1
        src = self.src
        if len(src) > 4_000_000:
            return
        masked = _BLANK.sub(b" ", src[:a]) + src[a:b] + _BLANK.sub(b" ", src[b:])
        root = parser("rust").parse(masked).root_node
        if root.has_error or not any(c.type in ITEM_TYPES for c in root.children):
            return
        self.f.macro_bodies += 1
        self._macro_depth = getattr(self, "_macro_depth", 0) + 1
        try:
            self.container(root, module, cfgs, in_test, pub_chain, ctx)
        finally:
            self._macro_depth -= 1

    def _type_refs(self, node, owner: RItem):
        for x in _iter(node):
            if x.type == "type_identifier":
                self.f.type_refs.append((owner.key, text(self.src, x), x.start_point[0] + 1, x.start_point[1]))

    # ---------------------------------------------------------------- bodies
    def body(self, body, owner: RItem, module, cfgs, in_test):
        src = self.src
        stack = [body]
        fn_ctx = {"kind": "fn", "prefix": owner.key}
        while stack:
            x = stack.pop()
            t = x.type
            if t in ITEM_TYPES and x is not body:
                attrs = []
                p = x.prev_sibling
                while p is not None and p.type in ("attribute_item", "line_comment", "block_comment"):
                    if p.type == "attribute_item":
                        attrs.insert(0, p)
                    p = p.prev_sibling
                self.item(x, attrs, [], module, cfgs, in_test, False, fn_ctx)
                continue
            if t == "attribute_item":
                p, a = _attr_path(src, x)
                if p == "cfg":
                    nxt = x.next_sibling
                    while nxt is not None and nxt.type in ("attribute_item", "line_comment", "block_comment"):
                        nxt = nxt.next_sibling
                    if nxt is not None:
                        pred = a[1:-1] if a.startswith("(") else a
                        # `#[cfg(unix)] Pattern => { ... }`: the attribute is the arm's first child and gates the whole arm
                        end = x.parent if x.parent is not None and x.parent.type == "match_arm" else nxt
                        self.f.cfg_regions.append((nxt.start_point[0] + 1, end.end_point[0] + 1, pred, x.start_point[0] + 1, owner.key))
                continue
            if t == "unsafe_block":
                self.f.unsafe_blocks.append((x.start_point[0] + 1, owner.key))
            elif t == "macro_invocation":
                mname = text(src, x.children[0]) if x.children else ""
                short = mname.split("::")[-1]
                if short in ("env", "option_env"):
                    tt = next((c for c in x.children if c.type == "token_tree"), None)
                    sl = next((c for c in tt.children if c.type == "string_literal"), None) if tt is not None else None
                    k = string_value(src, sl)
                    if k:
                        self.f.env.append((k, x.start_point[0] + 1, owner.key, f"{short}!"))
                self.f.calls.append((owner.key, "macro", mname, short, x.start_point[0] + 1, x.start_point[1]))
                if short not in ("env", "option_env", "include_str", "include_bytes", "concat", "stringify", "cfg"):
                    tt = next((c for c in x.children if c.type == "token_tree"), None)
                    if tt is not None:
                        self._tt_calls(tt, owner)
            elif t == "call_expression":
                self.call(x, owner)
            elif t == "type_identifier":
                self.f.type_refs.append((owner.key, text(src, x), x.start_point[0] + 1, x.start_point[1]))
            elif t == "struct_expression":
                nm = x.child_by_field_name("name")
                if nm is not None:
                    s = text(src, nm)
                    self.f.type_refs.append((owner.key, s.split("::")[-1].split("<")[0], nm.end_point[0] + 1, max(0, nm.end_point[1] - len(s.split("::")[-1]))))
            stack.extend(reversed(x.children))

    def _tt_calls(self, tt, owner: RItem):
        """Macro arguments are unparsed token trees (`assert_eq!(f(x), 1)`, `println!("{}", a.b())`): recover calls
        as `ident (` / `a::b (` / `.m (` token sequences. Heuristic by nature; SCIP mode resolves these exactly."""
        src = self.src
        stack = [tt]
        while stack:
            node = stack.pop()
            ch = node.children
            for i, c in enumerate(ch):
                if c.type == "token_tree":
                    stack.append(c)
                    continue
                if c.type != "identifier" or i + 1 >= len(ch) or ch[i + 1].type != "token_tree" or not text(src, ch[i + 1]).startswith("("):
                    continue
                name = text(src, c)
                prev = ch[i - 1] if i > 0 else None
                if prev is not None and text(src, prev) == ".":
                    self.f.calls.append((owner.key, "method", name, name, c.start_point[0] + 1, c.start_point[1]))
                    continue
                parts, j = [name], i - 1
                while j >= 1 and text(src, ch[j]) == "::" and ch[j - 1].type in ("identifier", "self", "super", "crate"):
                    parts.insert(0, text(src, ch[j - 1]))
                    j -= 2
                path = "::".join(parts)
                self.f.calls.append((owner.key, "path", path, name, c.start_point[0] + 1, c.start_point[1]))
                if ENV_CALLS.search(path):
                    sl = next((y for y in ch[i + 1].children if y.type == "string_literal"), None)
                    k = string_value(src, sl) if sl is not None else None
                    if k:
                        self.f.env.append((k, c.start_point[0] + 1, owner.key, path))

    def call(self, x, owner: RItem):
        src = self.src
        fn = x.child_by_field_name("function")
        args = x.child_by_field_name("arguments")
        if fn is None:
            return
        if fn.type == "generic_function":
            fn = fn.child_by_field_name("function") or fn
        ft = text(src, fn)
        if fn.type in ("identifier", "scoped_identifier"):
            name = ft.split("::")[-1]
            nm = fn.child_by_field_name("name") if fn.type == "scoped_identifier" else fn
            self.f.calls.append((owner.key, "path", re.sub(r"\s+", "", ft), name, nm.start_point[0] + 1, nm.start_point[1]))
            if ENV_CALLS.search(re.sub(r"\s+", "", ft)) and args is not None and args.named_children:
                a0 = args.named_children[0]
                k = string_value(src, a0)
                if k is None and a0.type == "identifier":
                    k = self.consts.get(text(src, a0))
                if k is None and a0.type == "scoped_identifier":
                    k = self.consts.get(text(src, a0).split("::")[-1])
                if k:
                    self.f.env.append((k, x.start_point[0] + 1, owner.key, re.sub(r"\s+", "", ft)))
        elif fn.type == "field_expression":
            fld = fn.child_by_field_name("field")
            if fld is not None:
                name = text(src, fld)
                self.f.calls.append((owner.key, "method", name, name, fld.start_point[0] + 1, fld.start_point[1]))
                if name == "route" and args is not None and len(args.named_children) >= 2:
                    self.route(args, owner)

    def route(self, args, owner: RItem):
        """axum `.route("/p", get(h).post(h2))`, actix `.route("/p", web::get().to(h))`."""
        src = self.src
        p = string_value(src, args.named_children[0])
        if p is None:
            return
        found = []
        for x in _iter(args.named_children[1]):
            if x.type != "call_expression":
                continue
            fn = x.child_by_field_name("function")
            a = x.child_by_field_name("arguments")
            if fn is None:
                continue
            verb = text(src, fn.child_by_field_name("field")) if fn.type == "field_expression" and fn.child_by_field_name("field") is not None else text(src, fn).split("::")[-1]
            if verb not in HTTP_VERBS and verb != "to":
                # `use axum::routing::get as aget;` -> aget(h)
                alias = self.f.use_map.get((owner.module, verb))
                if alias and alias.split("::")[-1] in HTTP_VERBS:
                    verb = alias.split("::")[-1]
            if verb in HTTP_VERBS and a is not None and a.named_children:
                h = a.named_children[0]
                if h.type in ("identifier", "scoped_identifier"):
                    hn = h.child_by_field_name("name") if h.type == "scoped_identifier" else h
                    found.append((verb.upper(), text(src, h), hn.start_point[0] + 1, hn.start_point[1], "axum"))
            elif verb == "to" and a is not None and a.named_children and fn.type == "field_expression":
                h = a.named_children[0]
                inner = fn.child_by_field_name("value")
                v = None
                for y in _iter(inner) if inner is not None else []:
                    if y.type in ("identifier", "field_identifier") and text(src, y) in HTTP_VERBS:
                        v = text(src, y)
                if h.type in ("identifier", "scoped_identifier"):
                    hn = h.child_by_field_name("name") if h.type == "scoped_identifier" else h
                    found.append(((v or "any").upper(), text(src, h), hn.start_point[0] + 1, hn.start_point[1], "actix"))
        for verb, h, line, col, fw in found:
            self.f.routes.append((verb, p, h, line, col, owner.key, fw))


def _iter(n):
    stack = [n]
    while stack:
        x = stack.pop()
        yield x
        stack.extend(reversed(x.children))


RUST_KEYWORDS = {"fn", "if", "while", "match", "for", "loop", "return", "let", "in", "as", "move", "async", "await",
                 "Some", "Ok", "Err", "Box", "Vec", "String"}


def extract(path: str, src: bytes, crate: str, module: str, cfgs: list, in_test: bool, pub_chain: bool,
            test_macros: dict | None = None) -> RFile:
    return Extractor(path, src, crate, module, cfgs, in_test, pub_chain, test_macros).run()

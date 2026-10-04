"""GraphQL root fields as protocol endpoints (#34): `endpoint:graphql:<Query|Mutation|Subscription>.<field>`.

  schema      SDL (`.graphql` / `.graphqls` / `.gql` files, gql / graphql / `/* GraphQL */` templates, Python gql("..."))
              `type Query { ... }`, `extend type ...`, `schema { query: X }`: the declared root fields (attrs.served,
              declared_in, type): a declared field counts as received even when cg finds no resolver function
  resolvers   RECEIVED_BY the function resolving a root field:
                JS / TS resolver maps   {Query: {launches: fn, ...}, Mutation: {...}} (spreads, shorthand, identifiers)
                graphene                ObjectType root classes of graphene.Schema(query=..) / build_federated_schema(..)
                                        and their bases (Query(AccountQueries, ...)): resolve_<field>, Mutation.Field()
                                        -> perform_mutation / mutate (own or inherited); snake_case -> camelCase
                strawberry              @strawberry.type roots: @strawberry.field / mutation / subscription methods,
                                        strawberry.field(resolver=fn); camelCase
                ariadne                 QueryType() / MutationType() / SubscriptionType() / ObjectType("Query"):
                                        @x.field("f"), @x.source("f"), x.set_field("f", fn)
                Nest                    route:GRAPHQL Query.x (plugins/nest): the endpoint twin, merged into the route
                                        by protocols/view.py
  operations  SENDS_TO from the code requesting a root field (attrs operation, operation_kind, fields: the field's
              first-level selection with fragments expanded): useQuery / useLazyQuery / useMutation / useSubscription
              / useSuspenseQuery (DOC), client.query({query: DOC}) / mutate / subscribe, request(url, DOC), codegen
              hooks use<Op>Query / use<Op>Mutation / ... (the generated hook itself is a wrapper, not a sender), any
              call taking a known document constant (Python: QUERY = \"\"\"...\"\"\" constants passed to a call)
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

from .core.model import HEURISTIC, RESOLVED

ROOTS = ("Query", "Mutation", "Subscription")
KIND_ROOT = {"query": "Query", "mutation": "Mutation", "subscription": "Subscription"}
SDL_EXT = (".graphql", ".graphqls", ".gql")
JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")
SKIP_PARTS = {"node_modules", "vendor", "third_party", ".git", "build", "dist", "target", "__pycache__", ".venv", "venv",
              ".next", ".nuxt", "coverage"}
TEST_FILE = re.compile(r"(?:^|/)(?:tests?|__tests__|spec|e2e|cypress|playwright)/|[._-](?:test|spec|stories)\.[cm]?[jt]sx?$|"
                       r"(?:^|/)test_[^/]*\.py$|_tests?\.py$|(?:^|/)conftest\.py$")
GENERATED = re.compile(r"\.generated\.[jt]sx?$|(?:^|/)__generated__/|(?:^|/)generated/graphql\.[jt]sx?$|(?:^|/)gql/graphql\.[jt]s$")
NAME = re.compile(r"[A-Za-z_]\w*$")
GENERIC = r"(?:<[^()<>]{0,300}(?:<[^()<>]{0,300}(?:<[^()<>]{0,300}>[^()<>]{0,300})*>[^()<>]{0,300})*>)?"   # <T, U<V>>

# ------------------------------------------------------------------ GraphQL documents (SDL and executable)
TOKEN = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|#[^\n]*|\.\.\.|[A-Za-z_]\w*|[{}():@$!=\[\],&|]|-?\d[\w.+-]*|\S')
SDL_KW = ("type", "interface", "input", "enum", "union", "scalar", "directive", "schema", "extend")


def _tokens(text: str) -> list[tuple[str, int]]:
    return [(m.group(0), m.start()) for m in TOKEN.finditer(text) if not m.group(0).startswith("#")]


class Doc:
    """A parsed GraphQL text: operations, fragments, object type fields and the schema's root type names."""

    def __init__(self, text: str):
        self.ops: list[dict] = []          # {name, kind, sel, pos}
        self.frags: dict[str, dict] = {}   # name -> {on, sel, pos}
        self.types: dict[str, dict] = {}   # type name -> {field: {pos, type}}
        self.schema: dict[str, str] = {}   # query / mutation / subscription -> type name
        self.tk = _tokens(text)
        self.n = len(self.tk)
        try:
            self._parse()
        except (IndexError, RecursionError):
            pass

    def t(self, i):
        return self.tk[i][0] if 0 <= i < self.n else ""

    def _skip(self, i, o, c):
        depth = 0
        while i < self.n:
            x = self.t(i)
            if x == o:
                depth += 1
            elif x == c:
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return i

    def _dirs(self, i):
        out = []
        while self.t(i) == "@":
            out.append(self.t(i + 1))
            i += 2
            if self.t(i) == "(":
                i = self._skip(i, "(", ")")
        return out, i

    def _sel(self, i, depth=0):
        items = []
        i += 1
        while i < self.n and self.t(i) != "}":
            x = self.t(i)
            if x == "...":
                i += 1
                if self.t(i) == "on":
                    on = self.t(i + 1)
                    dirs, i = self._dirs(i + 2)
                    if self.t(i) == "{":
                        ch, i = self._sel(i, depth + 1)
                        items.append(("inline", on, ch, dirs))
                elif self.t(i) in ("{", "@"):
                    dirs, i = self._dirs(i)
                    if self.t(i) == "{":
                        ch, i = self._sel(i, depth + 1)
                        items.append(("inline", None, ch, dirs))
                else:
                    name = self.t(i)
                    dirs, i = self._dirs(i + 1)
                    items.append(("spread", name, None, dirs))
                continue
            if NAME.match(x):
                name, alias, pos = x, None, self.tk[i][1]
                i += 1
                if self.t(i) == ":":
                    alias, name = name, self.t(i + 1)
                    i += 2
                if self.t(i) == "(":
                    i = self._skip(i, "(", ")")
                dirs, i = self._dirs(i)
                ch = None
                if self.t(i) == "{":
                    ch, i = self._sel(i, depth + 1)
                items.append(("field", name, ch, dirs, alias, pos))
                continue
            i += 1
        return items, i + 1

    def _parse(self):
        i = 0
        while i < self.n:
            x, pos = self.tk[i]
            if x in ("query", "mutation", "subscription") and self.t(i + 1) not in (":", "(") or \
                    x in ("query", "mutation", "subscription") and self.t(i + 1) == "(" and self._is_op_vars(i + 1):
                kind, i, name = x, i + 1, None
                if NAME.match(self.t(i)) and self.t(i) != "on":
                    name, i = self.t(i), i + 1
                if self.t(i) == "(":
                    i = self._skip(i, "(", ")")
                _d, i = self._dirs(i)
                if self.t(i) == "{":
                    sel, i = self._sel(i)
                    self.ops.append({"name": name, "kind": kind, "sel": sel, "pos": pos})
                continue
            if x == "fragment" and self.t(i + 2) == "on":
                name, on = self.t(i + 1), self.t(i + 3)
                _d, i = self._dirs(i + 4)
                if self.t(i) == "{":
                    sel, i = self._sel(i)
                    self.frags[name] = {"on": on, "sel": sel, "pos": pos}
                continue
            if x == "{":
                sel, i = self._sel(i)
                self.ops.append({"name": None, "kind": "query", "sel": sel, "pos": pos})
                continue
            if x in SDL_KW:
                i = self._sdl(i)
                continue
            i += 1

    def _is_op_vars(self, i):
        return self.t(i + 1) == "$"

    def _sdl(self, i):
        x = self.t(i)
        if x == "extend":
            i += 1
            x = self.t(i)
        if x == "schema":
            _d, i = self._dirs(i + 1)
            if self.t(i) == "{":
                j = i + 1
                while j < self.n and self.t(j) != "}":
                    if self.t(j) in KIND_ROOT and self.t(j + 1) == ":":
                        self.schema[self.t(j)] = self.t(j + 2)
                        j += 3
                    else:
                        j += 1
                return j + 1
            return i
        if x in ("type", "interface", "input", "enum"):
            name = self.t(i + 1)
            i += 2
            while i < self.n and self.t(i) not in ("{",) and self.t(i) not in SDL_KW and not self.t(i).startswith('"'):
                if self.t(i) == "@":
                    _d, i = self._dirs(i)
                    continue
                i += 1
            if self.t(i) != "{":
                return i
            if x != "type":
                return self._skip(i, "{", "}")
            fields = self.types.setdefault(name, {})
            i += 1
            while i < self.n and self.t(i) != "}":
                f = self.t(i)
                if f.startswith('"') or not NAME.match(f):
                    i += 1
                    continue
                pos = self.tk[i][1]
                i += 1
                if self.t(i) == "(":
                    i = self._skip(i, "(", ")")
                if self.t(i) != ":":
                    continue
                i += 1
                ty = ""
                while self.t(i) in ("[", "]", "!") or (NAME.match(self.t(i)) and not ty.strip("[]!")):
                    ty += self.t(i)
                    i += 1
                while self.t(i) in ("]", "!"):
                    ty += self.t(i)
                    i += 1
                _d, i = self._dirs(i)
                fields.setdefault(f, {"pos": pos, "type": ty})
            return i + 1
        return i + 1


def root_fields(sel) -> list[tuple[str, list, list, int]]:
    """[(field, selection, directives, pos)] of an operation's root selection (inline fragments flattened)."""
    out = []
    for it in sel:
        if it[0] == "field":
            out.append((it[1], it[2] or [], it[3], it[5]))
        elif it[0] == "inline":
            out += root_fields(it[2])
    return out


def first_level(sel, frags: dict, depth: int = 0) -> list[str]:
    """Field names of a selection set, fragment spreads and inline fragments expanded."""
    out = []
    if depth > 8:
        return out
    for it in sel or ():
        if it[0] == "field":
            if it[1] != "__typename" and it[1].strip("_"):
                out.append(it[1])
        elif it[0] == "inline":
            out += first_level(it[2], frags, depth + 1)
        elif it[0] == "spread" and it[1] in frags:
            out += first_level(frags[it[1]]["sel"], frags, depth + 1)
    return list(dict.fromkeys(out))


def camel(name: str, style: str = "graphene") -> str:
    """graphene / strawberry auto camelCase of a Python field name."""
    lead = len(name) - len(name.lstrip("_"))
    parts = name[lead:].split("_")
    if style == "graphene":
        rest = "".join(p.capitalize() if p else "_" for p in parts[1:])
    else:
        rest = "".join(p[:1].upper() + p[1:] for p in parts[1:])
    return "_" * lead + parts[0] + rest


# ------------------------------------------------------------------ source text helpers
TAG = re.compile(r"(?:\b(?:gql|graphql)\s*(?:<[^`<>()]*(?:<[^`<>()]*>[^`<>()]*)*>)?\s*(\(\s*)?|/\*\s*GraphQL\s*\*/\s*)`")
PY_STR = re.compile(r'(?<![\w"\'])([rRbBuUfF]{0,2})("""|\'\'\')([\s\S]*?)\2')
PY_GQL = re.compile(r'\bgql\s*\(\s*([rRfF]{0,2})("""|\'\'\'|"|\')')


def _template_end(src: str, i: int) -> int:
    """Offset of the closing backtick of the template literal whose body starts at `i`."""
    n = len(src)
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i
        if c == "$" and i + 1 < n and src[i + 1] == "{":
            depth, i = 1, i + 2
            while i < n and depth:
                if src[i] == "{":
                    depth += 1
                elif src[i] == "}":
                    depth -= 1
                elif src[i] == "`":
                    i = _template_end(src, i + 1)
                i += 1
            continue
        i += 1
    return n


def _blank_interp(body: str) -> str:
    """`${...}` interpolations -> spaces (offsets kept)."""
    out, i, n = [], 0, len(body)
    while i < n:
        if body[i] == "$" and i + 1 < n and body[i + 1] == "{":
            depth, j = 1, i + 2
            while j < n and depth:
                depth += {"{": 1, "}": -1}.get(body[j], 0)
                j += 1
            out.append(re.sub(r"[^\n]", "_", body[i:j]))     # a dynamic name: `___` (root_fields skips it)
            i = j
            continue
        out.append(body[i])
        i += 1
    return "".join(out)


def _blank_fstring(body: str) -> str:
    return re.sub(r"\{\{|\}\}|\{[^{}\n]*\}", lambda m: m.group(0)[0] + " " if m.group(0) in ("{{", "}}") else "_" * len(m.group(0)), body)


ASSIGN_JS = re.compile(r"(?:\b(?:const|let|var)\s+|\bexport\s+(?:const|let|var)\s+|\bthis\.|^\s*|[,{]\s*)([A-Za-z_$][\w$]*)\s*"
                       r"(?::\s*[^=;\n]{1,120})?\s*=\s*$")
ASSIGN_PY = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=\n]+)?=\s*(?:\(\s*)?(?:[\w.]+\s*\+\s*)*(?:gql\s*\(\s*)?$")


# ------------------------------------------------------------------ the scan
class Scan:
    def __init__(self, project, b, sock=None):
        from .sockets import Scan as SockScan
        self.root, self.b = project.root, b
        self.s = sock or SockScan(project, b)
        self.st = defaultdict(int)
        self.samples = defaultdict(list)
        self.docs: list[dict] = []                      # {file, pos, var, doc, lang}
        self.frags: dict[str, dict] = {}
        self.fields: dict[str, dict] = {}               # "Query.x" -> {file, line, type}
        self.rootname = {}                              # schema type name -> Query / Mutation / Subscription
        self.received = set()
        self.done = set()
        self._modules = None

    def miss(self, key, text):
        self.st[key] += 1
        if len(self.samples[key]) < 6:
            self.samples[key].append(text[:100])

    def module_of(self, file):
        if self._modules is None:
            self._modules = {}
            for nid, n in self.b.nodes.items():
                if n.kind == "module" and n.file:
                    self._modules.setdefault(n.file, nid)
        return self._modules.get(file)

    # -------------------------------------------------------------- files
    def _walk(self, exts):
        out = []
        for dp, dns, fns in os.walk(self.root):
            dns[:] = sorted(d for d in dns if d not in SKIP_PARTS and not d.startswith("."))
            for f in fns:
                if f.endswith(exts):
                    out.append(os.path.relpath(os.path.join(dp, f), self.root))
            if len(out) > 5000:
                break
        return sorted(out)

    def collect(self):
        """Every GraphQL text of the project: SDL / operation files and embedded documents."""
        for rel in self._walk(SDL_EXT):
            try:
                if (self.root / rel).stat().st_size > 3_000_000:
                    continue
            except OSError:
                continue
            t = self.s.text(rel)
            self._add(rel, 0, None, Doc(t), "graphql")
            self.st["graphql_files"] += 1
        for rel in sorted(self.s.files):
            if rel.endswith(JS_EXT):
                src = self.s.text(rel)
                if "gql" not in src and "graphql" not in src and "GraphQL" not in src:
                    continue
                for m in TAG.finditer(src):
                    if self.s.masked(rel, m.start()):
                        continue
                    lo = m.end()
                    hi = _template_end(src, lo)
                    body = _blank_interp(src[lo:hi])
                    pre = src[max(0, m.start() - 200):m.start()]
                    v = ASSIGN_JS.search(pre)
                    self._add(rel, lo, v.group(1) if v else None, Doc(body), "js", tag_start=m.start())
            elif rel.endswith(".py"):
                src = self.s.text(rel)
                if not re.search(r"\b(?:query|mutation|subscription|type\s+Query|fragment)\b", src):
                    continue
                for m in PY_STR.finditer(src):
                    body = m.group(3)
                    if not re.match(r"\s*(?:#[^\n]*\n\s*)*(?:query\b|mutation\b|subscription\b|fragment\b|\{|type\s|extend\s|schema\s)", body):
                        continue
                    if "f" in m.group(1).lower():
                        body = _blank_fstring(body)
                    ls = src.rfind("\n", 0, m.start()) + 1
                    v = ASSIGN_PY.match(src[ls:m.start()])
                    self._add(rel, m.start(3), v.group(1) if v else None, Doc(body), "py", tag_start=m.start())

    def _add(self, file, pos, var, doc, lang, tag_start=None):
        if not (doc.ops or doc.frags or doc.types or doc.schema):
            return
        d = {"file": file, "pos": pos, "var": var, "doc": doc, "lang": lang, "start": pos if tag_start is None else tag_start}
        self.docs.append(d)
        self.frags.update({k: v for k, v in doc.frags.items() if k not in self.frags})
        for k, v in doc.schema.items():
            self.rootname[v] = KIND_ROOT[k]
        self.st["documents"] += 1

    # -------------------------------------------------------------- schema
    def declare(self):
        from .protocols import _endpoint
        for r in ROOTS:
            self.rootname.setdefault(r, r)
        for d in self.docs:
            for tname, fields in d["doc"].types.items():
                root = self.rootname.get(tname)
                if root not in ROOTS or (tname in ROOTS and tname != root):
                    continue
                for f, info in fields.items():
                    key = f"{root}.{f}"
                    line = self.s.line_of(d["file"], d["pos"] + info["pos"])
                    if key in self.fields:
                        self.fields[key].setdefault("also", []).append(f"{d['file']}:{line}")
                        continue
                    self.fields[key] = {"file": d["file"], "line": line, "type": info["type"]}
                    _endpoint(self.b, "graphql", key, {"root": root, "field": f, "type": info["type"] or None,
                                                       "declared_in": f"{d['file']}:{line}", "served": "schema"})
                    self.st["schema_fields"] += 1

    def receive(self, root, field, handler, file, line, conf, framework, how, served=None, **attrs):
        from .protocols import protocol_receive
        key = f"{root}.{field}"
        if ("r", key, handler) in self.done:
            return
        self.done.add(("r", key, handler))
        protocol_receive(self.b, "graphql", key, handler, file, line, conf, library=framework, how=how,
                         node_attrs={"root": root, "field": field}, **attrs)
        if served:
            self.b.nodes[f"endpoint:graphql:{key}"].attrs.setdefault("served", served)
        self.received.add(key)
        self.st[f"resolvers_{framework}"] += 1

    def served_only(self, root, field, framework, file, line):
        """A root field declared in code (graphene / strawberry) without a resolver function: the default resolver."""
        from .protocols import _endpoint
        key = f"{root}.{field}"
        nid = _endpoint(self.b, "graphql", key, {"root": root, "field": field})
        a = self.b.nodes[nid].attrs
        a.setdefault("served", framework)
        a.setdefault("declared_in", f"{file}:{line}")
        self.st[f"fields_without_resolver_{framework}"] += 1

    # -------------------------------------------------------------- JS / TS resolver maps
    ROOT_KEY = re.compile(r"""(?:^|[{,\s])['"]?(\w+)['"]?\s*:\s*\{""")
    ITEM = re.compile(r"""\s*(?:(\.\.\.)\s*([\w$.]+)|(?:async\s+)?(?:\*\s*)?(?:(\w+)|'([^']+)'|"([^"]+)")\s*(\(|:|,|\}|$))""")

    def js_resolvers(self):
        roots = {k for k, v in self.rootname.items() if v in ROOTS}
        hint = re.compile(r"resolvers?|apollo|graphql|@graphql-tools|mercurius|yoga|typeDefs", re.I)
        for rel in sorted(self.s.files):
            if not rel.endswith(JS_EXT) or GENERATED.search(rel):
                continue
            src = self.s.text(rel)
            if not any(r in src for r in roots) or not (hint.search(src) or hint.search(rel)):
                continue
            for m in self.ROOT_KEY.finditer(src):
                if m.group(1) not in roots or self.s.masked(rel, m.start(1)):
                    continue
                if re.search(r"typePolicies|InMemoryCache|possibleTypes", src[max(0, m.start() - 300):m.start()]):
                    continue                        # Apollo cache type policies, not resolvers
                self._js_map(rel, src, m.end() - 1, self.rootname[m.group(1)], 0)

    def _body_items(self, src, lo):
        """(key, kind, value_text, key_pos, item_end) of the object literal opening at src[lo] == '{'."""
        from .rpc import _block
        s, e = _block(src, lo)
        i, out = s + 1, []
        depth, q = 0, None
        start = i
        j = i
        while j < e - 1:
            c = src[j]
            if q:
                if c == "\\":
                    j += 2
                    continue
                if c == q:
                    q = None
                j += 1
                continue
            if c in "'\"`":
                q = c
            elif c == "/" and src[j + 1:j + 2] == "/":
                nl = src.find("\n", j)
                j = nl if nl > 0 else e
                continue
            elif c == "/" and src[j + 1:j + 2] == "*":
                k = src.find("*/", j + 2)
                j = k + 2 if k > 0 else e
                continue
            elif c in "{([":
                depth += 1
            elif c in "})]":
                depth -= 1
            elif c == "," and depth == 0:
                out.append((start, j))
                start = j + 1
            j += 1
        out.append((start, e - 1))
        items = []
        for a, z in out:
            m = self.ITEM.match(src, a, z + 1)
            if not m:
                continue
            if m.group(1):
                items.append((m.group(2), "spread", m.group(2), m.start(2), z))
                continue
            key = m.group(3) or m.group(4) or m.group(5)
            sep = m.group(6)
            kind = "method" if sep == "(" else "value" if sep == ":" else "shorthand"
            val = src[m.end():z].strip() if kind == "value" else ""
            items.append((key, kind, val, m.start(3) if m.group(3) else m.start(), z))
        return items

    def _fn_at(self, file, line, name):
        for ln, _end, nid in self.s.spans.get(file, ()):
            if ln == line and (self.s.b.nodes[nid].name or "").split(".")[-1] == name:
                return nid
        for ln, _end, nid in self.s.spans.get(file, ()):
            if ln == line:
                return nid
        return None

    def _ident(self, file, name):
        """A function node named `name`: in `file`, else the only one in the project."""
        from .sockets import _short
        short = name.split(".")[-1]
        here = [nid for _l, _e, nid in self.s.spans.get(file, ()) if _short(self.b.nodes[nid]) == short]
        if len(here) == 1:
            return here[0], RESOLVED
        cand = self.s.by_name.get(short) or []
        if name != short:
            cand = [c for c in cand if (self.b.nodes[c].name or "").endswith(name)] or cand
        if len(cand) == 1:
            return cand[0], HEURISTIC
        return None, None

    def _js_map(self, file, src, lo, root, depth):
        if depth > 3:
            return
        for key, kind, val, kpos, end in self._body_items(src, lo):
            line = self.s.line_of(file, kpos)
            if kind == "spread":
                self._js_spread(file, src, key, root, depth)
                continue
            if key in ("__resolveType", "__isTypeOf", "__resolveReference"):
                continue
            h, conf, how = None, RESOLVED, "resolver map"
            if kind == "method" or kind == "value" and re.match(r"(?:async\s+)?(?:function\b|\(|[\w$]+\s*=>)", val):
                h = self._fn_at(file, line, key)
            elif kind == "value" and val.startswith("{"):          # subscription {subscribe, resolve}
                vpos = src.find("{", kpos + len(key))
                for k2, kd2, _v2, kp2, _e2 in self._body_items(src, vpos):
                    if k2 in ("subscribe", "resolve"):
                        h2 = self._fn_at(file, self.s.line_of(file, kp2), k2)
                        if h2 and (h is None or k2 == "subscribe"):
                            h = h2
                how = "resolver map (subscribe)"
            elif kind == "value" and re.fullmatch(r"[\w$.]+", val) or kind == "shorthand":
                h, conf = self._ident(file, val if kind == "value" else key)
                how = "resolver map (reference)"
            if h:
                self.receive(root, key, h, file, line, conf or HEURISTIC, "js", how)
            else:
                self.miss("js_resolver_not_found", f"{file}:{line} {root}.{key}")

    def _js_spread(self, file, src, name, root, depth):
        rx = re.compile(rf"\b(?:const|let|var)\s+{re.escape(name.split('.')[-1])}\s*(?::[^=;\n]+)?=\s*\{{")
        m = rx.search(src)
        if m:
            self._js_map(file, src, m.end() - 1, root, depth + 1)
            return
        hits = []
        for rel in sorted(self.s.files):
            if rel.endswith(JS_EXT) and rel != file:
                t = self.s.text(rel)
                if name.split(".")[-1] in t and (mm := rx.search(t)):
                    hits.append((rel, t, mm))
        if len(hits) == 1:
            rel, t, mm = hits[0]
            self._js_map(rel, t, mm.end() - 1, root, depth + 1)
        else:
            self.miss("js_resolver_spread_not_found", f"{file} ...{name}")

    # -------------------------------------------------------------- Python (graphene / strawberry / ariadne)
    def _py_classes(self):
        self.cls = {}                     # nid -> {name, file, line, end, bases: [nid], header}
        by_name = defaultdict(list)
        for nid, n in self.b.nodes.items():
            if n.kind == "class" and n.file and n.file.endswith(".py") and n.line:
                self.cls[nid] = {"name": n.name, "file": n.file, "line": n.line, "end": n.end_line or n.line, "bases": []}
                by_name[n.name].append(nid)
        for e in self.b.edges.values():
            if e.kind == "EXTENDS" and e.src in self.cls and e.dst in self.cls:
                self.cls[e.src]["bases"].append(e.dst)
        self.cls_by_name = by_name
        self.methods = defaultdict(dict)  # class nid -> {method name: nid}
        for nid, n in self.b.nodes.items():
            if n.kind == "method" and n.file and n.file.endswith(".py"):
                c = nid.rsplit(".", 1)[0].replace("method:", "class:", 1)
                if c in self.cls:
                    self.methods[c][n.name] = nid
        for nid, c in self.cls.items():
            src = self.s.text(c["file"])
            lo = self.s.off(c["file"], c["line"])
            m = re.compile(r"class\s+\w+\s*(\([^:]*\))?\s*:").search(src, lo, lo + 3000)
            c["header"] = m.group(1) or "" if m else ""
            c["deco"] = src[max(0, lo - 300):lo].rsplit("\n\n", 1)[-1] if lo else ""
            ls = src.rfind("\n", 0, lo) + 1
            dec = []
            k = ls
            while k > 0:
                pl = src.rfind("\n", 0, k - 1) + 1
                line = src[pl:k - 1].strip()
                if line.startswith("@"):
                    dec.append(line)
                    k = pl
                else:
                    break
            c["decorators"] = dec

    def _mro(self, nid):
        out, todo = [], [nid]
        while todo:
            x = todo.pop(0)
            if x in out:
                continue
            out.append(x)
            todo += self.cls[x]["bases"]
        return out

    def _is_graphene(self, nid):
        return any("ObjectType" in self.cls[x]["header"] and "graphene" in self.s.text(self.cls[x]["file"])
                   for x in self._mro(nid))

    def _is_strawberry(self, nid):
        return any(any(re.match(r"@strawberry(?:\.federation)?\.(?:type|interface)\b", d) for d in self.cls[x]["decorators"])
                   for x in self._mro(nid))

    SCHEMA_CALL = re.compile(r"\b((?:graphene|strawberry)\.(?:federation\.)?Schema|build_\w*schema|Schema)\s*\(")

    def _class_ref(self, file, name):
        c = self.cls_by_name.get(name) or []
        here = [x for x in c if self.cls[x]["file"] == file]
        if here:
            return here[0]
        src = self.s.text(file)
        m = re.search(rf"^\s*from\s+([.\w]+)\s+import\s+[^\n]*\b{re.escape(name)}\b", src, re.M) or \
            re.search(rf"^\s*from\s+([.\w]+)\s+import\s+\([^)]*\b{re.escape(name)}\b", src, re.M)
        if m:
            mod = m.group(1)
            tail = mod.lstrip(".").replace(".", "/")
            for x in c:
                f = self.cls[x]["file"]
                if f.endswith(f"{tail}.py") or f.endswith(f"{tail}/__init__.py") or (not tail and os.path.dirname(f) == os.path.dirname(file)):
                    return x
        return c[0] if len(c) == 1 else None

    def py_resolvers(self):
        pyfiles = [f for f in sorted(self.s.files) if f.endswith(".py")]
        if not any(re.search(r"\b(?:graphene|strawberry|ariadne)\b", self.s.text(f)[:20000]) for f in pyfiles):
            return
        self._py_classes()
        roots = []                        # (class nid, root, style)
        for f in pyfiles:
            src = self.s.text(f)
            if "chema" not in src:
                continue
            for m in self.SCHEMA_CALL.finditer(src):
                if self.s.masked(f, m.start()):
                    continue
                from .sockets import _args_text, split_args
                args = split_args(_args_text(src, m.end() - 1, 4000))
                camel_on = not re.search(r"auto_camel_?case\s*=\s*False", ", ".join(args))
                for k, a in enumerate(args):
                    kw = re.match(r"(query|mutation|subscription)\s*=\s*([A-Za-z_]\w*)\s*$", a.strip())
                    name, kind = (kw.group(2), kw.group(1)) if kw else (a.strip(), "query") if k == 0 and NAME.match(a.strip()) else (None, None)
                    if not name:
                        continue
                    c = self._class_ref(f, name)
                    if c is None:
                        continue
                    style = "graphene" if self._is_graphene(c) else "strawberry" if self._is_strawberry(c) else None
                    if style:
                        roots.append((c, KIND_ROOT[kind], style, camel_on))
        if not roots:
            for r in ROOTS:
                for c in self.cls_by_name.get(r, ()):
                    style = "graphene" if self._is_graphene(c) else "strawberry" if self._is_strawberry(c) else None
                    if style:
                        roots.append((c, r, style, True))
        seen = set()
        for c, root, style, camel_on in roots:
            if (c, root) in seen:
                continue
            seen.add((c, root))
            self.st[f"roots_{style}"] += 1
            if style == "graphene":
                self._graphene(c, root, camel_on)
            else:
                self._strawberry(c, root, camel_on)
        self._ariadne(pyfiles)

    FIELD_ASSIGN = re.compile(r"^([ \t]+)([a-z_]\w*)\s*(?::[^=\n]+)?=\s*(?:\(\s*)?([\w.]+)\s*\(", re.M)

    def _class_fields(self, c):
        """[(python name, callee, line, offset)] of the class-level field assignments of class `c`."""
        info = self.cls[c]
        src = self.s.text(info["file"])
        lo, hi = self.s.off(info["file"], info["line"]), self.s.off(info["file"], info["end"] + 1)
        out = []
        indent = None
        for m in self.FIELD_ASSIGN.finditer(src, lo, hi):
            if indent is None:
                indent = len(m.group(1))
            if len(m.group(1)) != indent or m.group(2).startswith("_") or self.s.masked(info["file"], m.start(2)):
                continue
            out.append((m.group(2), m.group(3), self.s.line_of(info["file"], m.start(2)), m.end()))
        return out

    def _handler(self, cls_chain, names):
        for x in cls_chain:
            for nm in names:
                if nm in self.methods.get(x, {}):
                    return self.methods[x][nm], x
        return None, None

    def _graphene(self, rc, root, camel_on):
        chain = [x for x in self._mro(rc) if self._is_graphene(x)]
        for x in chain:
            info = self.cls[x]
            for py, callee, line, end in self._class_fields(x):
                if py == "Meta" or callee in ("staticmethod", "classmethod", "property"):
                    continue
                gql = camel(py) if camel_on else py
                file = info["file"]
                mname = callee.rsplit(".", 1)[0].split(".")[-1] if callee.endswith(".Field") else ""
                if mname[:1].isupper():
                    mc = self._class_ref(file, mname)
                    if mc is not None:
                        h, owner = self._handler(self._mro(mc), ("perform_mutation", "mutate", "subscribe"))
                        if h:
                            inh = owner != mc
                            self.receive(root, gql, h, file, line, HEURISTIC if inh else RESOLVED, "graphene",
                                         "Mutation.Field()" + (" (inherited)" if inh else ""), served="graphene",
                                         mutation=self.cls[mc]["name"], inherited_from=self.cls[owner]["name"] if inh else None)
                            continue
                    self.served_only(root, gql, "graphene", file, line)
                    continue
                from .sockets import _args_text
                args = _args_text(self.s.text(file), end - 1, 4000)
                rv = re.search(r"\bresolver\s*=\s*([\w.]+)", args)
                h, owner = self._handler(chain, (f"subscribe_{py}", f"resolve_{py}") if root == "Subscription" else (f"resolve_{py}",))
                if h:
                    self.receive(root, gql, h, file, line, RESOLVED if owner == x else HEURISTIC, "graphene", "resolve_" + py,
                                 served="graphene")
                elif rv and (r := self._ident(file, rv.group(1))[0]):
                    self.receive(root, gql, r, file, line, HEURISTIC, "graphene", "resolver=", served="graphene")
                else:
                    self.served_only(root, gql, "graphene", file, line)

    STRAW_DECO = re.compile(r"@strawberry\.(?:federation\.)?(field|mutation|subscription)\b(\([^)]*\))?\s*\n\s*(?:async\s+)?def\s+(\w+)")

    def _strawberry(self, rc, root, camel_on):
        for x in self._mro(rc):
            if not self._is_strawberry(x):
                continue
            info = self.cls[x]
            file, src = info["file"], self.s.text(info["file"])
            lo, hi = self.s.off(file, info["line"]), self.s.off(file, info["end"] + 1)
            for m in self.STRAW_DECO.finditer(src, lo, hi):
                py = m.group(3)
                nm = re.search(r"\bname\s*=\s*['\"](\w+)['\"]", m.group(2) or "")
                gql = nm.group(1) if nm else camel(py, "strawberry") if camel_on else py
                h = self.methods.get(x, {}).get(py)
                if h:
                    self.receive(root, gql, h, file, self.s.line_of(file, m.start()), RESOLVED, "strawberry",
                                 f"@strawberry.{m.group(1)}", served="strawberry")
            for py, callee, line, end in self._class_fields(x):
                if not re.match(r"strawberry(?:_django)?\.(?:federation\.|relay\.)?(?:field|mutation|subscription|connection|node)$", callee):
                    continue
                from .sockets import _args_text
                args = _args_text(src, end - 1, 4000)
                nm = re.search(r"\bname\s*=\s*['\"](\w+)['\"]", args)
                gql = nm.group(1) if nm else camel(py, "strawberry") if camel_on else py
                rv = re.search(r"\bresolver\s*=\s*([\w.]+)", args)
                r = self._ident(file, rv.group(1))[0] if rv else None
                if r:
                    self.receive(root, gql, r, file, line, RESOLVED, "strawberry", "strawberry.field(resolver=)", served="strawberry")
                else:
                    self.served_only(root, gql, "strawberry", file, line)
            for m in self.ANNOTATED.finditer(src, lo, hi):    # `x: T` without a value: a field the default resolver serves
                py = m.group(2)
                if py.startswith("_") or self.s.masked(file, m.start(2)) or len(m.group(1)) != self._body_indent(src, lo, hi):
                    continue
                self.served_only(root, camel(py, "strawberry") if camel_on else py, "strawberry", file, self.s.line_of(file, m.start(2)))

    ANNOTATED = re.compile(r"^([ \t]+)([A-Za-z_]\w*)\s*:\s*[^=\n(]+$", re.M)

    @staticmethod
    def _body_indent(src, lo, hi):
        m = re.compile(r"\n([ \t]+)\S").search(src, lo, hi)
        return len(m.group(1)) if m else -1

    ARIADNE_VAR = re.compile(r"^(\w+)\s*=\s*(?:ariadne\.)?(QueryType|MutationType|SubscriptionType|ObjectType)\s*\(\s*(?:['\"](\w+)['\"])?", re.M)

    def _ariadne(self, pyfiles):
        for f in pyfiles:
            src = self.s.text(f)
            if "ariadne" not in src:
                continue
            for m in self.ARIADNE_VAR.finditer(src):
                root = {"QueryType": "Query", "MutationType": "Mutation", "SubscriptionType": "Subscription"}.get(m.group(2)) \
                    or self.rootname.get(m.group(3) or "")
                if root not in ROOTS:
                    continue
                v = re.escape(m.group(1))
                for d in re.finditer(rf"@{v}\.(field|source)\(\s*['\"](\w+)['\"]\s*\)\s*\n\s*(?:async\s+)?def\s+(\w+)", src):
                    line = self.s.line_of(f, src.find("def", d.start()))
                    h = self._fn_at(f, line, d.group(3))
                    if h:
                        self.receive(root, d.group(2), h, f, line, RESOLVED, "ariadne", f"@{m.group(1)}.{d.group(1)}")
                for d in re.finditer(rf"\b{v}\.set_(field|source)\(\s*['\"](\w+)['\"]\s*,\s*([\w.]+)", src):
                    h, conf = self._ident(f, d.group(3))
                    if h:
                        self.receive(root, d.group(2), h, f, self.s.line_of(f, d.start()), conf, "ariadne", f"{m.group(1)}.set_{d.group(1)}")

    # -------------------------------------------------------------- Nest
    def nest(self):
        """`route:GRAPHQL Query.x` (plugins/nest): the endpoint twin (cg link, schema fields); protocols/view.py shows
        the route only."""
        for nid, n in list(self.b.nodes.items()):
            if n.kind != "route" or not nid.startswith("route:GRAPHQL ") or n.attrs.get("graphql") not in ROOTS:
                continue
            for e in self.b.edges.values():
                if e.src == nid and e.kind == "ROUTES_TO":
                    self.receive(n.attrs["graphql"], n.attrs.get("field"), e.dst, e.file, e.line, RESOLVED, "nest", "@Resolver",
                                 served="nest", route=nid)
                    ep = self.b.nodes[f"endpoint:graphql:{n.attrs['graphql']}.{n.attrs.get('field')}"]
                    if ep.entry_kind == "message_handler":
                        ep.entry_kind = None       # the route is the entry point
                    break

    # -------------------------------------------------------------- operations
    HOOK = re.compile(r"(?<![\w$.])(?:[\w$]+\.)?(useQuery|useLazyQuery|useMutation|useSubscription|useSuspenseQuery|"
                      r"useBackgroundQuery|useFragment)\s*" + GENERIC + r"\s*\(\s*")
    CLIENT = re.compile(r"(?<![\w$])([\w$]+)\s*" + GENERIC + r"\s*\(\s*(?:[\w$.]+\s*,\s*)?\{")   # x.query({query: DOC}) ...
    CACHE = {"readQuery", "writeQuery", "readFragment", "writeFragment", "updateQuery", "watchFragment", "useFragment",
             "useQuery", "useLazyQuery", "useMutation", "useSubscription", "useSuspenseQuery", "useBackgroundQuery", "if",
             "for", "while", "switch", "function", "return"}
    URQL = re.compile(r"\.(query|mutation|subscription|executeQuery|executeMutation|request|rawRequest)\s*" + GENERIC + r"\s*\(\s*")
    CODEGEN = re.compile(r"(?<![\w$])use([A-Z]\w*?)(Query|LazyQuery|SuspenseQuery|Mutation|Subscription)\s*" + GENERIC + r"\s*\(")

    def operations(self):
        by_var = defaultdict(list)
        ops_by_name = defaultdict(list)
        for d in self.docs:
            if d["var"] and d["doc"].ops:
                by_var[d["var"]].append(d)
            for op in d["doc"].ops:
                if op["name"]:
                    ops_by_name[(op["name"], op["kind"])].append((d, op))
            self.st["operations"] += len(d["doc"].ops)
        self.by_var = by_var
        for rel in sorted(self.s.files):
            if rel.endswith(JS_EXT):
                self._js_sends(rel, ops_by_name)
            elif rel.endswith(".py") and by_var:
                self._py_sends(rel)
        # .graphql operation files without a codegen hook / Document use stay unsent (stats only)

    def _doc_for(self, file, name, pos=None):
        """The document bound to `name` at `pos`: assigned earlier in the calling function, at module level in the
        same file, or imported (the only one with that name, or the one in the imported file)."""
        c = self.by_var.get(name) or []
        here = [d for d in c if d["file"] == file]
        if here:
            fn, lo, _hi = self.s.fn_bounds(file, pos) if pos is not None else (None, 0, 0)
            local = [d for d in here if fn is not None and lo <= d["start"] < pos]
            top = [d for d in here if self.s.fn_bounds(file, d["start"])[0] is None]
            if local or top:
                return (local or top)[-1]
            return None                                   # assigned in another function: not this one's
        src = self.s.text(file)
        n = re.escape(name)
        m = re.search(rf"import\s*(?:type\s+)?\{{[^}}]*\b{n}\b[^}}]*\}}\s*from\s*['\"]([^'\"]+)['\"]|"
                      rf"^\s*from\s+([.\w]+)\s+import\s+(?:\([^)]*\b{n}\b|[^\n]*\b{n}\b)", src, re.M)
        if not m:
            return None
        if len(c) == 1:
            return c[0]
        mod = m.group(1) or (m.group(2) or "").lstrip(".").replace(".", "/")
        stem = os.path.basename(mod)
        hit = [d for d in c if os.path.splitext(os.path.basename(d["file"]))[0] in (stem, "index", "__init__") and
               (stem not in ("index", "__init__") or os.path.basename(os.path.dirname(d["file"])) == os.path.basename(os.path.dirname(mod)))]
        return hit[0] if len(hit) == 1 else None

    def _inline_doc(self, file, pos):
        for d in self.docs:
            if d["file"] == file and d["start"] == pos:
                return d
        return None

    def _js_sends(self, file, ops_by_name):
        src = self.s.text(file)
        gen = bool(GENERATED.search(file))
        if self.by_var or any(d["file"] == file for d in self.docs):
            for rx, api in ((self.HOOK, "apollo"), (self.URQL, "client")):
                for m in rx.finditer(src):
                    if self.s.masked(file, m.start()):
                        continue
                    if api == "apollo" and m.group(1) == "useFragment":
                        continue
                    arg = src[m.end():m.end() + 300]
                    d = None
                    if (a := re.match(r"\{\s*(?:query|document|mutation)\s*:\s*([\w$]+)", arg)) and api == "apollo":
                        d = self._doc_for(file, a.group(1), m.start())
                    elif a := re.match(r"(?:[\w$.]+\s*,\s*|['\"`][^'\"`]*['\"`]\s*,\s*)?([A-Za-z_$][\w$]*)\s*[,)]", arg) \
                            if api == "client" and m.group(1) in ("request", "rawRequest") else re.match(r"([A-Za-z_$][\w$]*)\s*[,)]", arg):
                        d = self._doc_for(file, a.group(1), m.start())
                    elif re.match(r"(?:gql|graphql)\s*`|/\*\s*GraphQL", arg):
                        t = TAG.search(src, m.end())
                        d = self._inline_doc(file, t.start()) if t else None
                    if d:
                        self._send_doc(file, m.start(), d, api if api != "client" else m.group(1), gen)
            for m in self.CLIENT.finditer(src):
                if m.group(1) in self.CACHE or self.s.masked(file, m.start()):
                    continue
                a = re.match(r"[^{}]*?\b(?:query|mutation|document)\s*:\s*([\w$]+)", src[m.end():m.end() + 400])
                if a and (d := self._doc_for(file, a.group(1), m.start())):
                    self._send_doc(file, m.start(), d, m.group(1), gen)
        if ops_by_name and not gen:
            for m in self.CODEGEN.finditer(src):
                if self.s.masked(file, m.start()) or re.search(r"function\s+$", src[max(0, m.start() - 20):m.start()]):
                    continue
                kind = {"Mutation": "mutation", "Subscription": "subscription"}.get(m.group(2), "query")
                hit = ops_by_name.get((m.group(1), kind)) or []
                if hit:
                    # the operation's source document and its generated copies (hooks.generated.ts): one operation
                    # when every copy requests the same root fields
                    own = [h for h in hit if not GENERATED.search(h[0]["file"])] or hit
                    same = len({tuple(f[0] for f in root_fields(op["sel"])) for _d, op in hit}) == 1
                    d, op = own[0]
                    self._send_op(file, m.start(), d, op, f"codegen use{m.group(2)}",
                                  RESOLVED if same and len(own) == 1 or len(hit) == 1 else HEURISTIC)
                else:
                    self.miss("codegen_hook_without_operation", f"{file}:{self.s.line_of(file, m.start())} use{m.group(1)}{m.group(2)}")

    def _py_sends(self, file):
        src = self.s.text(file)
        names = [v for v in self.by_var if v in src]
        if not names:
            return
        rx = re.compile(r"\b([\w.]+)\s*\(\s*(?:(?:query|document|request_string|source)\s*=\s*)?(?:gql\(\s*)?(" +
                        "|".join(re.escape(v) for v in sorted(names, key=len, reverse=True)) + r")\b(?!\s*[=(.\[])")
        for m in rx.finditer(src):
            if m.group(1) in ("gql", "print", "len", "str", "format") or self.s.masked(file, m.start()):
                continue
            d = self._doc_for(file, m.group(2), m.start())
            if d and d["lang"] == "py":
                self._send_doc(file, m.start(), d, "python", False, conf=HEURISTIC)

    def _send_doc(self, file, pos, d, api, gen, conf=RESOLVED):
        fn, _lo, _hi = self.s.fn_bounds(file, pos)
        if gen and fn is not None:
            n = self.b.nodes.get(fn)
            nm = (n.name or "").split(".")[-1] if n else ""
            if any(nm == f"use{op['name']}{sfx}" for op in d["doc"].ops if op["name"]
                   for sfx in ("Query", "LazyQuery", "SuspenseQuery", "Mutation", "Subscription")):
                self.st["codegen_hooks"] += 1      # the generated hook: its callers send
                return
        for op in d["doc"].ops:
            self._send_op(file, pos, d, op, api, conf)

    def _send_op(self, file, pos, d, op, api, conf):
        from .protocols import protocol_send
        from .tests_index import is_test_node
        fn, _lo, _hi = self.s.fn_bounds(file, pos)
        top = fn is None
        if top:
            fn = self.module_of(file)
        if fn is None:
            self.miss("operation_outside_function", f"{file}:{self.s.line_of(file, pos)}")
            return
        line = self.s.line_of(file, pos)
        n = self.b.nodes.get(fn)
        test = bool(n and is_test_node(n)) or bool(TEST_FILE.search(file))
        frags = dict(self.frags)
        frags.update(d["doc"].frags)
        root = KIND_ROOT[op["kind"]]
        for field, sel, dirs, _p in root_fields(op["sel"]):
            if field.startswith("__") or "client" in dirs:
                continue
            if not field.strip("_"):
                self.st["dynamic_root_fields"] += 1       # `{name}(...)` / `${name}`: the field is computed
                continue
            key = f"{root}.{field}"
            if ("s", key, fn, line, op["name"]) in self.done:
                continue
            self.done.add(("s", key, fn, line, op["name"]))
            protocol_send(self.b, "graphql", key, fn, file, line, conf, test=test, role="request", library=api,
                          operation=op["name"], operation_kind=op["kind"], fields=first_level(sel, frags)[:60] or None,
                          node_attrs={"root": root, "field": field})
            self.st["operation_fields_sent"] += 1
            if key not in self.received and not self.b.nodes[f"endpoint:graphql:{key}"].attrs.get("served"):
                self.st["sent_fields_undeclared"] += 1

    def run(self) -> dict:
        self.collect()
        has_py = any(f.endswith(".py") for f in self.s.files)
        has_nest = any(nid.startswith("route:GRAPHQL ") for nid in self.b.nodes)
        if not self.docs and not has_nest and not has_py:
            return {}
        self.declare()
        self.js_resolvers()
        if has_py:
            self.py_resolvers()
        if has_nest:
            self.nest()
        self.operations()
        out = {k: v for k, v in self.st.items() if v}
        if not any(k.startswith(("resolvers_", "schema_fields", "operation_fields", "fields_without")) for k in out):
            return {}
        if self.samples:
            out["samples"] = dict(self.samples)
        return out


def apply(project, builder, sock=None) -> dict:
    """GraphQL schema root fields, their resolvers and the operations requesting them (#34); empty without any."""
    return Scan(project, builder, sock).run()

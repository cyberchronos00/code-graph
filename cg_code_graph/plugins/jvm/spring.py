"""Spring facts for the Java and Kotlin syntax layers.

Both plugins hand this module the same shape: classes (annotations with arguments, supertypes,
fields), methods (annotations, parameter types), and calls that carry string-literal arguments.
Routes, guards, beans, tables, listeners, HTTP clients and test requests are emitted here so a
Java ``SecurityFilterChain`` guards a Kotlin controller and the other way round.

Kotlin's previous extractor lived in ``plugins/kotlin/plugin.py``. The route, repository and
filter-chain rules are the same ones; this module is what both plugins call.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from ...core.model import EXACT, HEURISTIC, RESOLVED
from ...presets import skip_dirs

SPRING_MAP = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
              "PatchMapping": "PATCH", "RequestMapping": None,
              "GetExchange": "GET", "PostExchange": "POST", "PutExchange": "PUT", "DeleteExchange": "DELETE",
              "PatchExchange": "PATCH", "HttpExchange": None}
REPO_SUPERS = (r"(?:Jpa|Crud|ListCrud|PagingAndSorting|ListPagingAndSorting|CoroutineCrud|CoroutineSorting|"
               r"ReactiveCrud|ReactiveSorting|R2dbc|Mongo|ReactiveMongo|Kotlin)?Repository")
DATA_WRITE = re.compile(r"^(save|delete|remove|insert|update|upsert|batchInsert|batchUpsert|replace|persist|merge|"
                        r"flush|truncate)")
DATA_READ = re.compile(r"^(find|get|read|query|search|stream|count|exists|select|selectAll|all|slice|fetch|load)")
SEC_AUTH = r"(hasRole|hasAnyRole|hasAuthority|hasAnyAuthority|authenticated|fullyAuthenticated|permitAll|denyAll|access)"
SPRING_GUARDS = {"PreAuthorize", "Secured", "RolesAllowed", "PostAuthorize"}
LISTENERS = {"KafkaListener", "RabbitListener", "JmsListener", "SqsListener", "EventListener", "StreamListener"}
STEREOTYPES = {"Service", "Component", "Repository", "Controller", "RestController", "Configuration"}
RUNNERS = {"CommandLineRunner", "ApplicationRunner"}
TEST_ANNOTATIONS = {"Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate"}
TEST_FRAMEWORKS = (("org.junit.jupiter.", "junit5"), ("org.junit.", "junit4"), ("org.testng.", "testng"),
                   ("kotlin.test.", "kotlin-test"))
REST_VERBS = {"getForObject": "GET", "getForEntity": "GET", "postForObject": "POST", "postForEntity": "POST",
              "postForLocation": "POST", "put": "PUT", "patchForObject": "PATCH", "delete": "DELETE"}
HTTP_VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}
SQL_TABLE = re.compile(r"\b(?:from|join|update|into)\s+([A-Za-z_][\w.]*)", re.I)
SQL_WRITE = re.compile(r"^\s*(insert|update|delete|merge)\b", re.I)
KT_REPO = re.compile(r"\binterface\s+(\w+)\s*(?:<[^>{]*>)?\s*:[^{]*?\b" + REPO_SUPERS + r"\s*<\s*([\w.]+)")
JAVA_REPO = re.compile(r"\binterface\s+(\w+)\b[^{]*?\bextends\b[^{]*?\b" + REPO_SUPERS + r"\s*<\s*([\w.]+)")


def _ann(a):
    name = a[0]
    arg = a[1] if len(a) > 1 else None
    raw = a[2] if len(a) > 2 else ""
    return name, arg, raw


def _guard(a) -> str:
    name, arg, _ = _ann(a)
    return f"{name}({arg})" if arg is not None else name


def mapping_paths(a) -> list[str]:
    """Same path list the Kotlin extractor used: value / path / arrayOf / a bare string."""
    if a is None:
        return [""]
    raw = _ann(a)[2]
    inner = raw[raw.find("(") + 1:raw.rfind(")")] if "(" in raw else ""
    m = re.search(r"(?:value|path)\s*=\s*(\[[^\]]*\]|arrayOf\([^)]*\)|\{[^}]*\}|\"[^\"]*\")", inner)
    part = m.group(1) if m else (inner if inner and not re.match(r"\s*\w+\s*=", inner) else "")
    ps = re.findall(r'"((?:[^"\\]|\\.)*)"', part)
    return ps or [""]


def _verbs(name: str, raw: str) -> list[str]:
    if SPRING_MAP.get(name):
        return [SPRING_MAP[name]]
    found = re.findall(r"RequestMethod\.(\w+)", raw or "")
    return [v.upper() for v in found] or ["ANY"]


def _join(base: str, p: str) -> str:
    if not p:
        return base or "/"
    if p.startswith("/") and base:
        return base.rstrip("/") + p
    return (base.rstrip("/") + "/" + p) if base else ("/" + p.lstrip("/"))


def context_path(root: Path) -> str:
    """``server.servlet.context-path`` from ``application.properties`` or ``application.yml`` (not test config)."""
    skip = skip_dirs("common") | skip_dirs("common", "scan_skip_dirs")
    found = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in skip and not d.startswith(".")]
        rel = os.path.relpath(dp, root).replace(os.sep, "/")
        rel = "" if rel == "." else rel
        if rel == "src/test" or rel.startswith("src/test/") or "/src/test/" in f"/{rel}/":
            continue
        for name in ("application.properties", "application.yml", "application.yaml"):
            if name in fn and "application-" not in name:
                found.append(Path(dp) / name)

    def rank(p: Path):
        s = str(p).replace(os.sep, "/")
        return (0 if "/src/main/resources/" in f"/{s}/" or s.endswith("src/main/resources/" + p.name) else 1, len(s), s)

    for p in sorted(found, key=rank):
        v = _context_in(p)
        if not v:
            continue
        v = str(v).strip().strip('"').strip("'")
        if not v or v == "/":
            continue
        return v if v.startswith("/") else "/" + v
    return ""


def _context_in(p: Path) -> str | None:
    try:
        text = p.read_text(errors="replace")
    except OSError:
        return None
    if p.suffix == ".properties":
        m = re.search(r"(?m)^\s*server\.servlet\.context-path\s*[=:]\s*(\S+)", text)
        return m.group(1) if m else None
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:
        data = None
    if isinstance(data, dict):
        cur = data.get("server")
        if isinstance(cur, dict):
            cur = cur.get("servlet")
        if isinstance(cur, dict):
            v = cur.get("context-path") or cur.get("contextPath")
            if isinstance(v, str):
                return v
    m = re.search(r"(?m)^\s*context-path\s*:\s*[\"']?([^\"'\s#]+)", text)
    return m.group(1) if m else None


def emit_route(host, method: str, uri: str, handler: str | None, file: str, line: int, lang: str,
               guards: list, conf: str = EXACT) -> str:
    """One Spring route. ``lang`` is ``kotlin`` or ``java``. A context path, when the project has one, is prefixed."""
    uri = re.sub(r"\{(\w+)(?::[^{}]*)?\}", r"{\1}", uri)
    if not uri.startswith("/"):
        uri = "/" + uri
    ctx = getattr(host, "spring_ctx", "") or ""
    if ctx:
        uri = _join(ctx, uri)
    key = f"{method} {uri}"
    attrs = {"uri": uri, "method": method, "framework": "spring"}
    if guards:
        attrs["middleware"] = list(dict.fromkeys(g for g in guards if g))
    rid = host.b.add_node("route", key, name=key, file=file, line=line, lang=lang, entry_kind="http_route", attrs=attrs)
    if host.b.nodes[rid].entry_kind is None:
        host.b.nodes[rid].entry_kind = "http_route"
    if handler:
        host.b.add_edge(rid, handler, "ROUTES_TO", file, line, conf)
        node = host.b.nodes.get(handler)
        host.b.nodes[rid].attrs.setdefault("handler", node.name if node is not None else handler)
    host.st["routes"] += 1
    host.st["routes_spring"] += 1
    return rid


def method_route(host, d, lang: str) -> None:
    """Class + method mappings. Same controller gate as the Kotlin extractor."""
    anns = {a[0]: a for a in d.annotations}
    if not any(nm in ("GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "RequestMapping")
               for nm in anns):
        return
    cls = host.classes.get(d.cls) if d.cls else None
    if cls is None:
        return
    canns = {a[0]: a for a in cls.annotations}
    if "FeignClient" in canns or "HttpExchange" in canns:
        return
    if not ({"RestController", "Controller"} & set(canns)) and "RequestMapping" not in canns:
        return
    prefix = mapping_paths(canns.get("RequestMapping"))[0] if canns.get("RequestMapping") is not None else ""
    guards = [_guard(a) for nm2, a in canns.items() if nm2 in SPRING_GUARDS]
    guards += [_guard(a) for nm2, a in anns.items() if nm2 in SPRING_GUARDS]
    for nm, a in anns.items():
        if nm not in ("GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "RequestMapping"):
            continue
        _, _, raw = _ann(a)
        for v in _verbs(nm, raw):
            for p in mapping_paths(a):
                uri = _join(prefix or "", p) if (prefix or p) else "/"
                emit_route(host, v, uri, d.id, d.file, d.line, lang, guards, EXACT)


def mark_entry(host, d) -> None:
    """``@Scheduled``, message listeners, and ``CommandLineRunner`` / ``ApplicationRunner.run``."""
    if d.kind == "class" or d.id not in host.b.nodes:
        return
    names = {a[0] for a in d.annotations}
    node = host.b.nodes[d.id]
    if "Scheduled" in names:
        if node.entry_kind != "scheduled":
            node.entry_kind = "scheduled"
            host.st["scheduled"] += 1
        return
    if names & LISTENERS:
        if node.entry_kind != "listener":
            node.entry_kind = "listener"
            host.st["listeners"] += 1
        return
    cls = host.classes.get(d.cls) if d.cls else None
    if cls is not None and d.name == "run" and _short_supers(cls) & RUNNERS:
        if node.entry_kind != "main":
            node.entry_kind = "main"
            host.st["command_line_runners"] += 1


def _short_supers(cls) -> set[str]:
    return {s.split(".")[-1].split("<")[0] for s in (cls.supers or [])}


def _is_interface(host, cls) -> bool:
    n = host.b.nodes.get(cls.id)
    if n is None:
        return False
    return (n.attrs or {}).get("java_kind") == "interface" or (n.attrs or {}).get("kotlin_kind") == "interface"


def _bean_name(cls) -> str:
    for a in cls.annotations:
        name, arg, raw = _ann(a)
        if name in STEREOTYPES and arg:
            return arg
        m = re.search(r'(?:value|name)\s*=\s*"([^"]+)"', raw or "")
        if name in STEREOTYPES and m:
            return m.group(1)
    n = cls.name[:1].lower() + cls.name[1:] if cls.name else cls.name
    return n


def _quals(anns) -> set[str]:
    out = set()
    for a in anns or []:
        name, arg, raw = _ann(a)
        if name in ("Qualifier", "Resource") and arg:
            out.add(arg)
        elif name in ("Qualifier", "Resource"):
            m = re.search(r'(?:value|name)\s*=\s*"([^"]+)"', raw or "")
            if m:
                out.add(m.group(1))
    return out


def _primary(anns) -> bool:
    return any(a[0] == "Primary" for a in anns or [])


# ------------------------------------------------------------------ tables
def table_node(host, name: str, via: str) -> str:
    return host.b.add_node("table", name, lang="sql", attrs={"inferred": True, "via": via})


def entity_table(host, entity: str, fileobj) -> str:
    """``@Table(name = "owners")`` on the entity, else Spring Boot's CamelCase to snake_case."""
    cls = None
    try:
        cls = host._class_of(entity, fileobj)
    except TypeError:
        cls = host._class_of(entity, fileobj, None)
    if cls is not None:
        ann = next((a for a in cls.annotations if a[0] in ("Table", "Document")), None)
        if ann is not None and ann[1]:
            return ann[1]
    return re.sub(r"(?<!^)(?=[A-Z])", "_", entity).lower()


def index_kotlin_repos(host, kfiles) -> None:
    host.repos = getattr(host, "repos", {}) or {}
    for kf in kfiles:
        txt = kf.src.decode("utf-8", "replace")
        if "Repository" not in txt:
            continue
        for m in KT_REPO.finditer(txt):
            host.repos[m.group(1)] = entity_table(host, m.group(2).split(".")[-1], kf)


def index_java_repos(host, jfiles) -> None:
    host.repos = getattr(host, "repos", {}) or {}
    for jf in jfiles:
        txt = jf.src.decode("utf-8", "replace")
        if "Repository" not in txt:
            continue
        for m in JAVA_REPO.finditer(txt):
            host.repos[m.group(1)] = entity_table(host, m.group(2).split(".")[-1], jf)


def repository_access(host, owner: str, name: str, recv: str | None, line: int, rel: str, decl) -> None:
    """Spring Data call on a repository receiver. Exposed stays in the Kotlin plugin."""
    repos = getattr(host, "repos", None) or {}
    if not repos:
        return
    kind = "WRITES_TABLE" if DATA_WRITE.match(name) else "READS_TABLE" if DATA_READ.match(name) else None
    if kind is None:
        return
    rname = re.sub(r"[?!]", "", recv).split(".")[-1].strip() if recv else None
    repo = None
    if recv is None or recv == "this":
        if decl is not None and decl.cls and decl.cls.split(".")[-1] in repos:
            repo = decl.cls.split(".")[-1]
    else:
        cls = host.classes.get(decl.cls) if decl is not None and decl.cls else None
        ty = decl.types.get(rname) if decl is not None else None
        if ty is None and cls is not None and rname:
            ty = host._field_type(cls, rname)
        ty = (ty or "").split(".")[-1].split("<")[0].rstrip("?")
        if ty in repos:
            repo = ty
        elif rname in repos:
            repo = rname
    if repo is None:
        return
    host.b.add_edge(owner, table_node(host, repos[repo], "spring-data"), kind, rel, line, RESOLVED,
                    via=f"{repo}.{name}")
    host.st["table_access_spring_data"] += 1


def query_edges(host) -> None:
    """``@Query`` SQL / JPQL table names on a repository method."""
    for d in list(host.decls.values()):
        ann = next((a for a in d.annotations if a[0] == "Query"), None)
        if ann is None:
            continue
        _, arg, raw = _ann(ann)
        sql = arg or ""
        m = re.search(r'(?:value|nativeQuery)\s*=\s*"((?:[^"\\]|\\.)*)"', raw or "")
        # value= is the SQL; nativeQuery is a boolean. Prefer the longest string in the annotation.
        strings = re.findall(r'"((?:[^"\\]|\\.)*)"', raw or "")
        sql = max(strings, key=len) if strings else sql
        if not sql:
            continue
        fileobj = _file(host, d.file)
        write = bool(SQL_WRITE.match(sql) or any(a[0] == "Modifying" for a in d.annotations))
        kind = "WRITES_TABLE" if write else "READS_TABLE"
        seen = set()
        for tm in SQL_TABLE.finditer(sql):
            raw_name = tm.group(1).split(".")[-1]
            if raw_name.upper() in {"SELECT", "WHERE", "SET", "ON"}:
                continue
            if raw_name[:1].isupper() and fileobj is not None:
                name = entity_table(host, raw_name, fileobj)
            else:
                name = raw_name
            if name in seen:
                continue
            seen.add(name)
            host.b.add_edge(d.id, table_node(host, name, "spring-query"), kind, d.file, d.line, RESOLVED,
                            via="@Query")
            host.st["table_access_query"] += 1


def _file(host, rel: str):
    return getattr(host, "_kf_by_rel", {}).get(rel) or getattr(host, "_jf_by_rel", {}).get(rel)


# ------------------------------------------------------------------ security filter chains
def _ant(p: str):
    """Spring path pattern -> regex: ``**`` any depth, ``*`` one segment part, ``{x}`` one segment."""
    out, i = "", 0
    while i < len(p):
        if p.startswith("/**", i):
            out += r"(?:/.*)?"
            i += 3
        elif p.startswith("**", i):
            out += r".*"
            i += 2
        elif p[i] == "*":
            out += r"[^/]*"
            i += 1
        elif p[i] == "{":
            j = p.find("}", i)
            out += r"[^/]+"
            i = j + 1 if j > 0 else len(p)
        else:
            out += re.escape(p[i])
            i += 1
    return re.compile(out)


def _chain_rules(body: str):
    """URL rules inside one filter-chain bean. Same regexes the Kotlin extractor used."""
    rx_chain = re.compile(r"\b(?:requestMatchers|antMatchers|mvcMatchers|pathMatchers)\s*\(([^()]*)\)\s*\.\s*"
                          + SEC_AUTH + r"\s*\(([^()]*)\)")
    rx_dsl = re.compile(r"\bauthorize\s*\(\s*(?:HttpMethod\.(\w+)\s*,\s*)?(\"[^\"]*\"|anyRequest)\s*,\s*"
                        + SEC_AUTH + r"\b(?:\s*\(([^()]*)\))?")
    rx_any = re.compile(r"\banyRequest\s*\(\s*\)\s*\.\s*" + SEC_AUTH + r"\s*\(([^()]*)\)")
    rx_matcher = re.compile(r"\bsecurityMatchers?\s*(?:\(([^()]*(?:\([^()]*\)[^()]*)*)\)|\{([^{}]*)\})")
    matchers = None
    mm = rx_matcher.search(body)
    if mm:
        lits = re.findall(r'"([^"]*)"', mm.group(1) or mm.group(2) or "")
        matchers = [_ant(x) for x in lits] if lits else []
    found = []
    for m in rx_chain.finditer(body):
        meth = re.search(r"HttpMethod\.(\w+)", m.group(1))
        pats = re.findall(r'"([^"]*)"', m.group(1))
        found.append((m.start(), meth.group(1).upper() if meth else None, pats, m.group(2), m.group(3)))
    for m in rx_dsl.finditer(body):
        pats = ["/**"] if m.group(2) == "anyRequest" else [m.group(2).strip('"')]
        found.append((m.start(), (m.group(1) or "").upper() or None, pats, m.group(3), m.group(4) or ""))
    for m in rx_any.finditer(body):
        found.append((m.start(), None, ["/**"], m.group(1), m.group(2)))
    rules = []
    for _, meth, pats, auth, arg in sorted(found, key=lambda x: x[0]):
        roles = ",".join(re.findall(r'"([^"]*)"', arg))
        g = None if auth == "permitAll" else (f"{auth}({roles})" if roles else auth)
        rules.append((meth, [_ant(p) for p in pats if p], g))
    return matchers, rules


def _order_of(head: str) -> int:
    rx_order = re.compile(r"@Order\s*\(\s*(?:value\s*=\s*)?(-?\d+|Ordered\.(HIGHEST|LOWEST)_PRECEDENCE)"
                          r"(?:\s*([+-])\s*(\d+))?\s*\)")
    lowest = 2 ** 31 - 1
    om = None
    for om in rx_order.finditer(head):
        pass
    if not om:
        return lowest
    base = (-2 ** 31 if om.group(2) == "HIGHEST" else lowest) if om.group(2) else int(om.group(1))
    if om.group(3):
        base = base + int(om.group(4)) if om.group(3) == "+" else base - int(om.group(4))
    return base


def _kotlin_spans(txt: str):
    """``fun x(): SecurityFilterChain`` beans. Unchanged from the Kotlin extractor."""
    rx_fun = re.compile(r"\bfun\s+(\w+)\s*\([^()]*\)\s*:\s*Security(?:Web)?FilterChain\b\s*([{=])")
    lowest = 2 ** 31 - 1
    spans = []
    for m in rx_fun.finditer(txt):
        if m.group(2) == "{":
            depth, i = 0, m.end() - 1
            while i < len(txt):
                if txt[i] == "{":
                    depth += 1
                elif txt[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            end = i
        else:
            nxt = re.compile(r"\n\s*(?:@\w+[^\n]*\n\s*)*(?:(?:private|public|internal|open|override)\s+)*fun\b").search(txt, m.end())
            end = nxt.start() if nxt else len(txt)
        head = txt[max(0, m.start() - 300):m.start()]
        head = head[head.rfind("}") + 1:]
        spans.append((m.start(), end, _order_of(head)))
    if not spans:
        spans = [(0, len(txt), lowest)]
    return spans


def _java_spans(txt: str):
    """A ``@Bean`` method whose return type is ``SecurityFilterChain`` (Java has no ``fun x():`` form)."""
    rx_fun = re.compile(
        r"@Bean\b.{0,800}?Security(?:Web)?FilterChain\s+(\w+)\s*\([^;{]*\)\s*(?:throws\s+[\w., \t]+)?\{",
        re.S)
    lowest = 2 ** 31 - 1
    spans = []
    for m in rx_fun.finditer(txt):
        depth, i = 0, m.end() - 1
        while i < len(txt):
            if txt[i] == "{":
                depth += 1
            elif txt[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        head = txt[max(0, m.start() - 300):m.start()]
        head = head[head.rfind("}") + 1:]
        spans.append((m.start(), i, _order_of(head)))
    if not spans:
        spans = [(0, len(txt), lowest)]
    return spans


def _chains_in(txt: str, rel: str, lang: str):
    if "SecurityFilterChain" not in txt and "SecurityWebFilterChain" not in txt:
        return []
    spans = _kotlin_spans(txt) if lang == "kotlin" else _java_spans(txt)
    chains = []
    for start, end, order in spans:
        body = txt[start:end]
        matchers, rules = _chain_rules(body)
        if rules or matchers is not None:
            chains.append((order, rel, start, matchers, rules))
    return chains


def _ann_guard(g: str) -> bool:
    return g.split("(", 1)[0] in SPRING_GUARDS


def security_pass(host, files, lang: str) -> None:
    """Record this language's filter chains and apply every chain seen so far (Java and Kotlin) to every Spring route.

    Chains are ordered by ``@Order`` then source order. The first chain whose ``securityMatcher`` matches a
    request wins; a chain with no matcher matches every request; a ``RequestMatcher`` bean (no literal) matches
    none. Annotation guards (``@PreAuthorize`` and the rest) stay. Re-applying replaces chain guards so a chain
    discovered by the other language can take a route the first pass had already guarded.
    """
    found = []
    for f in files:
        txt = f.src.decode("utf-8", "replace") if isinstance(f.src, (bytes, bytearray)) else f.src
        found.extend(_chains_in(txt, f.rel, lang))
    box = host.b.__dict__.setdefault("_spring_chains", [])
    box.extend(found)
    if found:
        host.st["security_rules"] = sum(len(c[4]) for c in found)
        if len(found) > 1:
            host.st["security_chains"] = len(found)
        unk = sum(1 for c in found if c[3] == [])
        if unk:
            host.st["security_chains_unknown_matcher"] = unk
    if not box:
        return
    chains = sorted(box, key=lambda c: (c[0], c[1], c[2]))
    for n in host.b.nodes.values():
        if n.kind != "route" or n.lang not in ("kotlin", "java") or (n.attrs or {}).get("framework") != "spring":
            continue
        mw = [g for g in (n.attrs.get("middleware") or []) if _ann_guard(g)]
        if mw:
            n.attrs["middleware"] = mw
        else:
            n.attrs.pop("middleware", None)
        n.attrs.pop("security", None)
        uri, meth = n.attrs.get("uri", ""), n.attrs.get("method")
        probe = re.sub(r"\{\w+\}", "x", uri)
        chain = next((c for c in chains if c[3] is None or any(r.fullmatch(probe) for r in c[3])), None)
        if chain is None:
            continue
        for rm, rxs, g in chain[4]:
            if rm and rm != meth:
                continue
            if any(r.fullmatch(probe) for r in rxs):
                if g:
                    got = n.attrs.setdefault("middleware", [])
                    if g not in got:
                        got.append(g)
                    n.attrs.setdefault("security", f"SecurityFilterChain ({chain[1]})")
                    host.st["routes_guarded_by_security_chain"] += 1
                break


# ------------------------------------------------------------------ DI
def _harvest_key(host) -> str:
    return "_spring_beans"


def harvest(host, lang: str) -> None:
    """Beans (stereotypes and ``@Bean`` methods) and injection sites (constructor and field)."""
    beans = host.b.__dict__.setdefault("_spring_beans", [])
    sites = host.b.__dict__.setdefault("_spring_sites", [])
    seen_b = {b["id"] for b in beans}
    seen_s = {(s["host"], s.get("param") or s["type"], s["line"]) for s in sites}
    own = _files_of(host)
    for cls in list(host.classes.values()):
        if cls.file not in own:
            continue
        names = {a[0] for a in cls.annotations}
        if names & STEREOTYPES and cls.id not in seen_b:
            beans.append({"id": cls.id, "type": cls.name, "fqn": cls.fqn, "name": _bean_name(cls),
                          "primary": _primary(cls.annotations), "quals": _quals(cls.annotations),
                          "supers": _short_supers(cls), "iface": _is_interface(host, cls),
                          "file": cls.file, "line": cls.line, "lang": lang})
            seen_b.add(cls.id)
        if not (names & STEREOTYPES):
            continue
        for d in host.decls.values():
            if d.kind == "constructor" and d.cls == cls.fqn:
                _sites_from(sites, seen_s, cls, d.types, getattr(d, "param_anns", {}) or {}, d.file, d.line)
        if lang == "kotlin":
            _sites_from(sites, seen_s, cls, cls.types, getattr(cls, "param_anns", {}) or {}, cls.file, cls.line)
        for fname, anns in (getattr(cls, "field_anns", {}) or {}).items():
            if not ({a[0] for a in anns} & {"Autowired", "Inject", "Resource"}):
                continue
            ty = (cls.types or {}).get(fname) or ""
            short = ty.split(".")[-1].split("<")[0]
            if not short:
                continue
            key = (cls.id, fname, cls.line)
            if key in seen_s:
                continue
            seen_s.add(key)
            sites.append({"host": cls.id, "type": short, "quals": _quals(anns), "file": cls.file,
                          "line": cls.line, "param": fname})
    for d in list(host.decls.values()):
        if d.kind != "method" or d.file not in own or d.id in seen_b:
            continue
        if not any(a[0] == "Bean" for a in d.annotations):
            continue
        ret = (getattr(d, "ret", None) or "").split(".")[-1].split("<")[0]
        if not ret or ret in ("void", "Void", "Unit", "SecurityFilterChain", "SecurityWebFilterChain"):
            continue
        beans.append({"id": d.id, "type": ret, "fqn": d.fqn, "name": d.name, "primary": _primary(d.annotations),
                      "quals": _quals(d.annotations), "supers": set(), "iface": False, "bean_method": True,
                      "file": d.file, "line": d.line, "lang": lang})
        seen_b.add(d.id)


def _files_of(host) -> set[str]:
    return set(getattr(host, "_kf_by_rel", {}) or {}) | set(getattr(host, "_jf_by_rel", {}) or {})


def _sites_from(sites, seen, cls, types: dict, anns: dict, file: str, line: int) -> None:
    for pname, ty in (types or {}).items():
        short = (ty or "").split(".")[-1].split("<")[0].rstrip("?")
        if not short or short in ("String", "Int", "Long", "Integer", "Boolean", "boolean", "int", "long",
                                  "Double", "double", "List", "Map", "Set", "Optional"):
            continue
        key = (cls.id, short, line, pname)
        if key in seen:
            continue
        seen.add(key)
        sites.append({"host": cls.id, "type": short, "quals": _quals(anns.get(pname) or []), "file": file,
                      "line": line, "param": pname})


def _iface_id(host, tname: str) -> str | None:
    hits = [c for c in host.classes.values() if c.name == tname]
    if len(hits) == 1 and _is_interface(host, hits[0]):
        return hits[0].id
    nodes = [n for n in host.b.nodes.values() if n.kind == "class" and n.name == tname
             and ((n.attrs or {}).get("java_kind") == "interface" or (n.attrs or {}).get("kotlin_kind") == "interface")]
    if len(nodes) == 1:
        return nodes[0].id
    if len(hits) == 1 and _is_interface(host, hits[0]):
        return hits[0].id
    return None


def bind_injections(host) -> None:
    """An injected interface is BOUND_TO the single implementing bean, or the ``@Primary`` / ``@Qualifier`` one."""
    beans = host.b.__dict__.get("_spring_beans") or []
    sites = host.b.__dict__.get("_spring_sites") or []
    if not beans or not sites:
        return
    for s in sites:
        tname = s["type"]
        iface_id = _iface_id(host, tname)
        if iface_id is None:
            continue
        impls = [b for b in beans if not b.get("iface") and (
            tname in b["supers"] or (b.get("bean_method") and b["type"] == tname))]
        chosen = _choose(impls, s["quals"])
        if chosen is None or chosen["id"] == iface_id:
            continue
        key = (iface_id, chosen["id"], "BOUND_TO", s["file"], s["line"], None)
        if key in host.b.edges:
            continue
        host.b.add_edge(iface_id, chosen["id"], "BOUND_TO", s["file"], s["line"], RESOLVED, via="spring-bean")
        host.st["beans_bound"] += 1


def _choose(impls: list, quals: set):
    if not impls:
        return None
    if len(impls) == 1:
        return impls[0]
    primary = [b for b in impls if b["primary"]]
    if len(primary) == 1 and not quals:
        return primary[0]
    if quals:
        hit = [b for b in impls if quals & (b["quals"] | {b["name"]})]
        if len(hit) == 1:
            return hit[0]
    if len(primary) == 1:
        return primary[0]
    return None


# ------------------------------------------------------------------ HTTP clients and test requests
def observe_call(host, owner: str, name: str, recv: str | None, strings: list[str], text: str, line: int,
                 rel: str, decl, test: bool) -> None:
    """A call the syntax layer already walked. Only Spring client and test-client shapes are kept."""
    path = _client_url(strings, text)
    rec = {"owner": owner, "name": name, "recv": recv or "", "path": path, "text": text or "", "line": line,
           "file": rel, "test": test, "decl": decl}
    bag = host.__dict__.setdefault("_spring_calls", [])
    if name in REST_VERBS or name == "exchange" or name == "uri" or (test and name in HTTP_VERBS and path):
        bag.append(rec)


def _client_url(strings: list, text: str) -> str | None:
    """The URL a Spring client call names.

    A relative template (``/api/books/{id}``) wins over a bare origin in the same call. One
    ``baseUrl("https://host")`` or ``WebClient.create("https://host")`` / ``RestClient.create``
    on that call is prefixed onto the relative template.
    """
    strs = [s for s in strings if isinstance(s, str) and s]
    rels = [s for s in strs if s.startswith("/") or (s.startswith("{") and "/" in s)]
    absolute = []
    for s in strs:
        if s.startswith("http://") or s.startswith("https://"):
            rest = re.sub(r"^[a-zA-Z][\w+.-]*://[^/]+", "", s)
            if rest.startswith("/"):
                absolute.append(s)
    if absolute:
        return absolute[0]
    path = rels[0] if rels else next(
        (s for s in strs if s.startswith("{") or s.startswith("http://") or s.startswith("https://")), None)
    if path and path.startswith("/"):
        bases = re.findall(r'(?:baseUrl|create)\s*\(\s*"(https?://[^"]*)"', text or "")
        if len(bases) == 1:
            return bases[0].rstrip("/") + path
    return path


def _verb_of(rec) -> str | None:
    if rec["name"] in REST_VERBS:
        return REST_VERBS[rec["name"]]
    if rec["name"] == "exchange":
        m = re.search(r"HttpMethod\.(\w+)", rec["text"])
        return m.group(1).upper() if m else None
    if rec["name"] == "uri":
        m = re.search(r"\.(get|post|put|patch|delete|head|options)\s*\(\s*\)\s*$", rec["recv"])
        if not m:
            m = re.search(r"\.(get|post|put|patch|delete|head|options)\s*\(\s*\)", rec["recv"])
        return m.group(1).upper() if m else "GET"
    if rec["name"] in HTTP_VERBS:
        return rec["name"].upper()
    return None


def _recv_type(host, rec) -> str:
    decl = rec.get("decl")
    recv = rec["recv"] or ""
    rname = recv.split(".")[0].split("(")[0].strip()
    ty = ""
    if decl is not None and rname:
        ty = (getattr(decl, "types", {}) or {}).get(rname) or ""
        cls = host.classes.get(decl.cls) if getattr(decl, "cls", None) else None
        if not ty and cls is not None:
            try:
                ty = host._field_type(cls, rname) or ""
            except Exception:
                ty = ""
    return ty


def _client_of(rec, host) -> str | None:
    """Which Spring client a recorded call is. Test clients become TEST_HTTP; the others are HTTP_CALLS."""
    ty = _recv_type(host, rec)
    recv = rec["recv"] or ""
    src = _src(host, rec["file"])
    if "TestRestTemplate" in ty or "TestRestTemplate" in recv:
        return "test-rest-template"
    if "WebTestClient" in ty or (rec["test"] and rec["name"] == "uri" and "WebTestClient" in src):
        return "web-test-client"
    if "RestClient" in ty:
        return "rest-client"
    if "WebClient" in ty:
        return "web-client"
    if "RestTemplate" in ty and "TestRestTemplate" not in ty:
        return "rest-template"
    if rec["test"] and rec["name"] in HTTP_VERBS and (
            "MockMvc" in src or "MockMvcRequestBuilders" in src):
        return "mock-mvc"
    if rec["name"] in REST_VERBS or rec["name"] == "exchange":
        if rec["test"] and "TestRestTemplate" in src and "RestTemplate" not in src.replace("TestRestTemplate", ""):
            return "test-rest-template"
        if "RestTemplate" in src or "TestRestTemplate" in src:
            return "test-rest-template" if rec["test"] and "TestRestTemplate" in src else "rest-template"
    if rec["name"] == "uri" and re.search(r"\.(get|post|put|patch|delete|head|options)\s*\(", recv):
        if rec["test"] and "WebTestClient" in src:
            return "web-test-client"
        named = _client_in_call(rec)
        if named:
            return named
        if "RestClient" in src and "WebClient" not in src:
            return "rest-client"
        if "WebClient" in src or "WebTestClient" in src:
            return "web-client"
    return None


def _client_in_call(rec) -> str | None:
    """Client named on this call. A file that uses both WebClient and RestClient must not label every call as one of them."""
    blob = f"{rec.get('text') or ''} {rec.get('recv') or ''}"
    rest = "RestClient" in blob
    web = "WebClient" in blob
    if rest and not web:
        return "rest-client"
    if web and not rest:
        return "web-client"
    return None


def _src(host, rel: str) -> str:
    f = _file(host, rel)
    if f is None:
        return ""
    raw = f.src
    return raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw


def emit_clients(host, lang: str) -> None:
    """``RestTemplate`` / ``RestClient`` / ``WebClient`` calls, plus Feign and ``@HttpExchange`` interfaces."""
    _feign_and_exchange(host, lang)
    for rec in host.__dict__.get("_spring_calls") or []:
        if rec["file"] not in _files_of(host):
            continue
        client = _client_of(rec, host)
        verb = _verb_of(rec)
        path = rec["path"]
        if not client or not verb or not path:
            continue
        if client in ("mock-mvc", "web-test-client", "test-rest-template"):
            host.__dict__.setdefault("_spring_test_http", []).append(
                {"src": rec["owner"], "method": verb, "path": path.split("?")[0], "file": rec["file"],
                 "line": rec["line"], "via": client})
            continue
        _http_node(host, rec["owner"], verb, path, client, rec["file"], rec["line"], lang)


def _http_node(host, src: str, method: str, url: str, client: str, file: str, line: int, lang: str) -> None:
    origin, path = _split_url(url)
    okind = "api" if origin is None else ("unknown" if origin.startswith("{") else "other")
    key = f"{method} {path}" if okind in ("api", "unknown") else f"{method} {origin}{path}"
    nid = host.b.add_node("http", key, key, fqn=key, lang=lang,
                          attrs={"method": method, "path": path, "client": client, "origin": origin,
                                 "origin_kind": okind})
    host.b.add_edge(src, nid, "HTTP_CALLS", file, line, HEURISTIC if okind != "api" else EXACT,
                    client=client, url=url, origin=origin)
    host.st[f"http_{client.replace('-', '_')}"] += 1


def _split_url(t: str):
    t = t.split("?")[0].split("#")[0]
    m = re.match(r"^([a-zA-Z][\w+.-]*://[^/]*)(.*)$", t)
    origin = None
    if m:
        origin, t = m.group(1), m.group(2)
    else:
        m = re.match(r"^(\{[^{}/]*\})(/.*|)$", t)
        if m and not t.startswith("{/"):
            origin, t = m.group(1), m.group(2)
    if not t.startswith("/"):
        t = "/" + t
    return origin, t


def _feign_and_exchange(host, lang: str) -> None:
    for cls in list(host.classes.values()):
        if cls.file not in _files_of(host):
            continue
        canns = {a[0]: a for a in cls.annotations}
        feign = canns.get("FeignClient")
        exch = canns.get("HttpExchange")
        if feign is None and exch is None:
            continue
        base = ""
        if feign is not None:
            _, _, raw = _ann(feign)
            m = re.search(r'url\s*=\s*"([^"]*)"', raw)
            base = m.group(1) if m else ""
        if exch is not None:
            base = mapping_paths(exch)[0] or _ann(exch)[1] or base
        client = "feign" if feign is not None else "http-exchange"
        for d in host.decls.values():
            if d.cls != cls.fqn or d.kind != "method":
                continue
            anns = {a[0]: a for a in d.annotations}
            for nm, a in anns.items():
                if nm not in SPRING_MAP:
                    continue
                _, _, raw = _ann(a)
                paths = mapping_paths(a)
                for v in _verbs(nm, raw):
                    for p in paths:
                        url = _join(base, p) if base else (p or "/")
                        _http_node(host, d.id, v, url, client, d.file, d.line, lang)


def link_test_http(host) -> None:
    """``MockMvc`` / ``WebTestClient`` / ``TestRestTemplate`` requests become TEST_HTTP edges to the route."""
    pending = host.__dict__.get("_spring_test_http") or []
    if not pending:
        return
    from ...link import match_endpoint
    routes = []
    for nid, n in host.b.nodes.items():
        a = n.attrs or {}
        if n.kind != "route" or not a.get("uri") or not a.get("method") or a.get("method") == "WS":
            continue
        routes.append({"id": nid, "uri": a["uri"], "method": a["method"], "uris": [("as-declared", a["uri"])],
                       "file": n.file, "line": n.line or 0})
    for rec in pending:
        path = rec["path"]
        if not path.startswith("/"):
            continue
        res = match_endpoint(rec["method"], path, routes, "api") if routes else {"matched": []}
        for mm in res.get("matched") or []:
            host.b.add_edge(rec["src"], mm["route"], "TEST_HTTP", rec["file"], rec["line"], mm["confidence"],
                            via=rec["via"], path=path)
            host.st["test_http"] += 1


def mark_java_tests(host) -> None:
    """JUnit 4 / 5 and TestNG methods in this Java plugin (Kotlin already marks its own)."""
    for d in list(host.decls.values()):
        if d.kind != "method" or not d.test:
            continue
        if not any(a[0] in TEST_ANNOTATIONS for a in d.annotations):
            continue
        n = host.b.nodes.get(d.id)
        if n is None:
            continue
        n.entry_kind = "test"
        fw = _java_fw(host, d.file)
        if fw:
            n.attrs["framework"] = fw
        if any(a[0] == "ParameterizedTest" for a in d.annotations):
            n.attrs["parameterized"] = True
        host.st["test_cases"] += 1


def _java_fw(host, rel: str) -> str | None:
    jf = getattr(host, "_jf_by_rel", {}).get(rel)
    if jf is None:
        return None
    cands = list(jf.imports.values())
    cands += [f + ".*" for f in jf.star]
    for c in cands:
        for prefix, fw in TEST_FRAMEWORKS:
            if c.startswith(prefix):
                return fw
    return None


def prepare(host, root: Path) -> None:
    host.spring_ctx = context_path(root)
    host.repos = {}


def finish(host, files, lang: str) -> None:
    """Entries, queries, clients, DI and filter chains for one language, seeing facts the other language already added."""
    for d in list(host.decls.values()):
        if d.file in _files_of(host):
            mark_entry(host, d)
    if lang == "java":
        for d in list(host.decls.values()):
            if d.kind == "method" and d.file in _files_of(host):
                method_route(host, d, "java")
        mark_java_tests(host)
    query_edges(host)
    emit_clients(host, lang)
    link_test_http(host)
    harvest(host, lang)
    bind_injections(host)
    security_pass(host, files, lang)

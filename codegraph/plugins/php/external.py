"""PHP client constructors as external facts (#41 step 3): `new PDO($dsn)`, Doctrine DBAL
`DriverManager::getConnection([...])` / `EntityManager::create([...])`. DSN / url from a string
literal or an env/config key; passwords only as the env key or the literal's location."""
from __future__ import annotations

import re
from pathlib import Path

from ... import presets
from ...core.plugin import GraphBuilder, Project
from ...external import parse_dsn

SKIP = presets.skip_dirs("common")
PDO_NEW = re.compile(r"""\bnew\s+\\?PDO\s*\(""", re.I)
DOCTRINE = re.compile(
    r"""\b(?:DriverManager::getConnection|EntityManager(?:Interface)?::create|EntityManagerFactory::createEntityManager)\s*\("""
)
ENV_GET = re.compile(
    r"""(?:getenv|env)\s*\(\s*['\"](\w+)['\"]"""
    r"""|(?:\$_(?:ENV|SERVER))\s*\[\s*['\"](\w+)['\"]"""
    r"""|config\s*\(\s*['\"][^'\"]*['\"]\s*\)"""
)


def _php_files(root: Path):
    out = []
    for p in root.rglob("*.php"):
        if SKIP.isdisjoint(p.parts):
            out.append(p)
            if len(out) >= 400:
                break
    return out


def _str(s: str):
    s = (s or "").strip()
    m = re.fullmatch(r"""['\"]([^'\"]*)['\"]""", s)
    return m.group(1) if m else None


def _env_key(expr: str):
    m = re.search(r"""(?:getenv|env)\s*\(\s*['\"](\w+)['\"]""", expr or "")
    if m:
        return m.group(1)
    m = re.search(r"""\$_(?:ENV|SERVER)\s*\[\s*['\"](\w+)['\"]""", expr or "")
    return m.group(1) if m else None


def _first_arg(src: str, start: int):
    """Argument text of a call starting at `start` (index of '('), stopping at top-level comma or ')'. """
    i = src.find("(", start)
    if i < 0:
        return None, start
    i += 1
    depth, in_str, q, beg = 0, False, "", i
    while i < len(src):
        c = src[i]
        if in_str:
            if c == "\\" and i + 1 < len(src):
                i += 2
                continue
            if c == q:
                in_str = False
            i += 1
            continue
        if c in "'\"":
            in_str, q = True, c
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                return src[beg:i].strip(), i
            depth -= 1
        elif c == "," and depth == 0:
            return src[beg:i].strip(), i
        i += 1
    return None, start


def _url_fact(expr: str, proto_hint: str | None, f: str, line: int, src_id: str, client: str):
    lit = _str(expr)
    if lit is not None:
        if "://" not in lit and not lit.lower().startswith(("mysql:", "pgsql:", "sqlite:", "sqlsrv:")):
            return None
        # PDO DSN `mysql:host=x;dbname=y` -> synthesise a URL-ish form for parse_dsn
        if "://" not in lit and ":" in lit:
            driver, _, rest = lit.partition(":")
            parts = dict(re.findall(r"(\w+)=([^;]+)", rest))
            host = parts.get("host") or parts.get("unix_socket") or "localhost"
            port = parts.get("port")
            db = parts.get("dbname") or parts.get("database")
            scheme = {"mysql": "mysql", "pgsql": "postgres", "postgres": "postgres", "sqlite": "sqlite",
                      "sqlsrv": "mssql", "oci": "oracle"}.get(driver.lower())
            if scheme == "sqlite":
                return None  # local file
            if not scheme:
                return None
            url = f"{scheme}://{host}" + (f":{port}" if port else "") + (f"/{db}" if db else "")
            d = parse_dsn(url)
            if not d or (d["host"] or "").lower() in ("localhost", "127.0.0.1", "::1"):
                # still record as config-local when host is loopback with explicit non-default?
                pass
            return {"url": ("lit", url), "protocol": d["protocol"] if d else scheme, "src": src_id,
                    "file": f, "line": line, "var": client, "client": client, "module": f,
                    "host_only": False}
        d = parse_dsn(lit)
        if not d:
            return None
        if d["protocol"] == "sqlite":
            return None
        return {"url": ("lit", lit), "protocol": d["protocol"], "src": src_id, "file": f, "line": line,
                "var": client, "client": client, "module": f}
    ek = _env_key(expr)
    if ek:
        return {"url": ("env", ek, None), "protocol": proto_hint, "src": src_id,
                "file": f, "line": line, "var": client, "client": client, "module": f}
    return None


def contribute(project: Project, b: GraphBuilder, ctx=None) -> dict:
    root = Path(project.root)
    facts = []
    # fn_at approximation: nearest function/method node covering the line
    by_file: dict = {}
    for n in b.nodes.values():
        if n.file and n.kind in ("function", "method") and n.line:
            by_file.setdefault(n.file, []).append(n)

    def fn_at(rel, line):
        best = None
        for n in by_file.get(rel) or []:
            if n.line <= line and (n.end_line or n.line) >= line:
                if best is None or n.line >= best.line:
                    best = n
        return best.id if best else f"module:{rel}"

    for path in _php_files(root):
        try:
            txt = path.read_text(errors="ignore")
        except Exception:
            continue
        if "PDO" not in txt and "DriverManager" not in txt and "EntityManager" not in txt:
            continue
        rel = str(path.relative_to(root))
        for rx, client, proto in ((PDO_NEW, "pdo", None), (DOCTRINE, "doctrine", None)):
            for m in rx.finditer(txt):
                arg, _ = _first_arg(txt, m.end() - 1)
                if not arg:
                    continue
                line = txt[:m.start()].count("\n") + 1
                # Doctrine often takes an array: ['url' => ..., 'dbname' => ..., 'host' => ...]
                if client == "doctrine" and ("=>" in arg or "[" in arg):
                    um = re.search(r"""['\"]url['\"]\s*=>\s*([^,\\]]+)""", arg)
                    if um:
                        fact = _url_fact(um.group(1).strip(), "sql", rel, line, fn_at(rel, line), client)
                    else:
                        hm = re.search(r"""['\"]host['\"]\s*=>\s*([^,\\]]+)""", arg)
                        dm = re.search(r"""['\"](?:dbname|database)['\"]\s*=>\s*([^,\\]]+)""", arg)
                        drv = re.search(r"""['\"]driver['\"]\s*=>\s*['\"](?:pdo_)?(\w+)['\"]""", arg)
                        if not hm:
                            continue
                        proto = None
                        if drv:
                            proto = {"mysql": "mysql", "pgsql": "postgres", "postgres": "postgres",
                                     "sqlite": None, "sqlsrv": "mssql", "oci8": "oracle",
                                     "postgresql": "postgres"}.get(drv.group(1).lower())
                        if proto is None:
                            continue
                        lit = _str(hm.group(1).strip())
                        ek = _env_key(hm.group(1).strip())
                        fact = {"protocol": proto, "src": fn_at(rel, line), "file": rel, "line": line,
                                "var": client, "client": client, "module": rel}
                        if lit:
                            fact["host"] = ("lit", lit)
                        elif ek:
                            fact["host"] = ("env", ek, None)
                        else:
                            continue
                        if dm and _str(dm.group(1).strip()):
                            fact["resource"] = _str(dm.group(1).strip())
                    if fact:
                        facts.append(fact)
                    continue
                fact = _url_fact(arg, proto, rel, line, fn_at(rel, line), client)
                if fact:
                    facts.append(fact)
    if facts:
        b.external_facts = getattr(b, "external_facts", []) + facts
    return {"php_clients": len(facts)} if facts else {}

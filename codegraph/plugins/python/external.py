"""Python settings -> external-system facts for codegraph/external.py (#40).

Module-level settings dicts (Django `DATABASES` / `CACHES`, NetBox-style `DATABASE` / `REDIS`, and nested aliases
`{"default": {...}}`) with a HOST / LOCATION, and URL settings (`DATABASE_URL`, `CELERY_BROKER_URL`, `BROKER_URL`,
`REDIS_URL`, `CACHE_URL`, `EMAIL_HOST`): literal values, `os.environ.get("K", default)` / `os.getenv` / `env("K")`
reads and names of other module dicts. Client constructors with an address in their arguments (#77):
`psycopg.connect(host=...)`, `redis.Redis(host=)` / `redis.from_url(url)`, `smtplib.SMTP(host, port)`,
`pymongo.MongoClient(url)`, `ldap3.Server(host)`, `boto3.client("s3", endpoint_url=)`, and a client built bare and
connected afterwards (`c = paramiko.SSHClient(); c.connect(host)`, `ftp = ftplib.FTP(); ftp.connect(host)`, #103) ...
Only facts are recorded
(builder.external_facts); passwords are kept as the env key or the location of a literal, never the value."""
from __future__ import annotations

import ast

DICT_VARS = {"DATABASES": None, "DATABASE": None, "CACHES": None, "REDIS": "redis", "CACHE": None}
URL_VARS = {"DATABASE_URL": None, "CELERY_BROKER_URL": None, "BROKER_URL": None, "REDIS_URL": "redis", "CACHE_URL": None,
            "CELERY_RESULT_BACKEND": None, "EMAIL_HOST": "smtp", "MONGO_URL": "mongodb", "MONGODB_URI": "mongodb"}
ENGINES = (("postgis", "postgres"), ("postgresql", "postgres"), ("psycopg", "postgres"), ("mysql", "mysql"),
           ("oracle", "oracle"), ("mssql", "mssql"), ("sql_server", "mssql"), ("redis", "redis"), ("memcache", "memcached"),
           ("sqlite", None), ("locmem", None), ("dummy", None), ("filebased", None), ("db.DatabaseCache", None))


def _value(prog, m, e, depth=0):
    """('lit', str) | ('env', key, default) | ('dict', ast.Dict, module) | None"""
    if e is None or depth > 3:
        return None
    if isinstance(e, ast.Constant) and isinstance(e.value, (str, int)) and not isinstance(e.value, bool):
        return ("lit", str(e.value))
    if isinstance(e, ast.Dict):
        return ("dict", e, m)
    if isinstance(e, ast.Call):
        fn = e.func
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
        recv = fn.value if isinstance(fn, ast.Attribute) else None
        rname = recv.attr if isinstance(recv, ast.Attribute) else recv.id if isinstance(recv, ast.Name) else None
        envish = (isinstance(fn, ast.Name) and name in ("getenv", "env")) or (name == "get" and rname == "environ") \
            or (name == "getenv" and rname == "os") or (rname in ("env", "environ") and name in ("str", "int", "url", "db", "cache_url", "db_url"))
        if envish and e.args \
                and isinstance(e.args[0], ast.Constant) and isinstance(e.args[0].value, str) and e.args[0].value.isupper():
            d = e.args[1] if len(e.args) > 1 else next((k.value for k in e.keywords if k.arg == "default"), None)
            dv = d.value if isinstance(d, ast.Constant) and isinstance(d.value, (str, int)) else None
            return ("env", e.args[0].value, None if dv is None else str(dv))
        if name in ("int", "str") and e.args:
            return _value(prog, m, e.args[0], depth + 1)
    if isinstance(e, ast.Subscript) and isinstance(e.slice, ast.Constant) and isinstance(e.slice.value, str) \
            and (getattr(e.value, "attr", None) == "environ" or getattr(e.value, "id", None) == "environ"):
        return ("env", e.slice.value, None)
    if isinstance(e, ast.Name):
        r = prog.resolve_name(m, e.id)
        if r and r[0] == "var":
            for v, _l, _a in r[1].vars.get(r[2], [])[-1:]:
                return _value(prog, r[1], v, depth + 1)
    return None


def _proto(engine: str | None, default):
    if engine:
        for k, p in ENGINES:
            if k in engine.lower():
                return p, True
    return default, False


def index(prog, b, walk_body=None) -> int:
    facts = []
    for m in prog.modules.values():
        if not any(s in m.name.rsplit(".", 1)[-1].lower() for s in ("settings", "config", "configuration")):
            continue
        for name, vals in m.vars.items():
            if name not in DICT_VARS and name not in URL_VARS:
                continue
            v, line, _a = vals[-1]
            if name in URL_VARS:
                x = _value(prog, m, v)
                if x and x[0] in ("lit", "env"):
                    facts.append({"var": name, "module": m.name, "protocol": URL_VARS[name], "url": x, "src": f"module:{m.name}", "file": m.file, "line": line,
                                  "host_only": name == "EMAIL_HOST"})
                continue
            x = _value(prog, m, v)
            if not x or x[0] != "dict":
                continue
            d, dm = x[1], x[2]
            entries = []
            keys = {k.value: val for k, val in zip(d.keys, d.values) if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if any(k in keys for k in ("HOST", "LOCATION", "ENGINE", "BACKEND", "URL")):
                entries.append((None, keys, dm))
            else:
                for alias, val in keys.items():
                    y = _value(prog, dm, val)
                    if y and y[0] == "dict":
                        sub = {k.value: vv for k, vv in zip(y[1].keys, y[1].values) if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                        entries.append((alias, sub, y[2]))
            for alias, kv, em in entries:
                eng = _value(prog, em, kv.get("ENGINE") or kv.get("BACKEND"))
                proto, known = _proto(eng[1] if eng and eng[0] == "lit" else None, DICT_VARS[name])
                if (eng and eng[0] == "lit") and known and proto is None:
                    continue                                   # sqlite / local-memory cache: no network system
                loc = _value(prog, em, kv.get("LOCATION") or kv.get("URL"))
                nm = _value(prog, em, kv.get("NAME"))
                f = {"var": name + (f"[{alias}]" if alias else ""), "module": m.name, "protocol": proto, "src": f"module:{m.name}", "file": m.file, "line": line,
                     "resource": nm[1] if nm and nm[0] == "lit" and nm[1] else None}
                if loc is not None and (loc[0] == "env" or (loc[0] == "lit" and "://" in loc[1])):
                    f["url"] = loc
                else:
                    f["host"] = _value(prog, em, kv.get("HOST"))
                    f["port"] = _value(prog, em, kv.get("PORT"))
                    if f["host"] is None:
                        continue
                pw = kv.get("PASSWORD")
                if pw is not None:
                    pv = _value(prog, em, pw)
                    f["password"] = ("env", pv[1]) if pv and pv[0] == "env" else ("literal", f"{em.file}:{pw.lineno}") \
                        if pv and pv[0] == "lit" and pv[1] else None
                facts.append(f)
    if walk_body is not None:
        facts += client_facts(prog, b, walk_body)
    if facts:
        b.external_facts = getattr(b, "external_facts", []) + facts
    return len(facts)


# top-level package -> (protocol, constructor / function names, default port override)
CLIENTS = {
    "psycopg": ("postgres", {"connect", "Connection", "AsyncConnection", "ConnectionPool", "AsyncConnectionPool"}),
    "psycopg2": ("postgres", {"connect", "SimpleConnectionPool", "ThreadedConnectionPool"}),
    "psycopg_pool": ("postgres", {"ConnectionPool", "AsyncConnectionPool"}),
    "asyncpg": ("postgres", {"connect", "create_pool"}),
    "pg8000": ("postgres", {"connect", "Connection"}),
    "pymysql": ("mysql", {"connect", "Connection"}),
    "MySQLdb": ("mysql", {"connect", "Connection"}),
    "mysql": ("mysql", {"connect", "MySQLConnection"}),
    "aiomysql": ("mysql", {"connect", "create_pool"}),
    "redis": ("redis", {"Redis", "StrictRedis", "from_url", "ConnectionPool"}),
    "aioredis": ("redis", {"Redis", "from_url", "create_redis_pool"}),
    "pymongo": ("mongodb", {"MongoClient"}),
    "motor": ("mongodb", {"AsyncIOMotorClient", "MotorClient"}),
    "smtplib": ("smtp", {"SMTP", "SMTP_SSL"}),
    "aiosmtplib": ("smtp", {"SMTP", "send"}),
    "ftplib": ("ftp", {"FTP", "FTP_TLS"}),
    "ldap3": ("ldap", {"Server"}),
    "ldap": ("ldap", {"initialize"}),
    "pika": ("amqp", {"URLParameters", "ConnectionParameters"}),
    "aio_pika": ("amqp", {"connect", "connect_robust"}),
    "kombu": ("amqp", {"Connection"}),
    "elasticsearch": ("elasticsearch", {"Elasticsearch", "AsyncElasticsearch"}),
    "pymemcache": ("memcached", {"Client", "PooledClient"}),
    "boto3": ("s3", {"client", "resource"}),
    "paramiko": ("ssh", {"SSHClient", "Transport"}),
}
# a client built without an address and connected afterwards (`c = paramiko.SSHClient(); c.connect(host)`, #103)
CONNECT_METHODS = {"connect"}
TLS_CTORS = {"SMTP_SSL": "465", "FTP_TLS": None}
URL_KW = ("dsn", "url", "conninfo", "host_url", "endpoint_url", "hosts")
HOST_KW = ("host", "hostname", "server")


def _dotted(m, e):
    """`redis.asyncio.Redis` / `Redis` (from redis import Redis) -> 'redis.asyncio.Redis', through the imports."""
    parts = []
    while isinstance(e, ast.Attribute):
        parts.append(e.attr)
        e = e.value
    if not isinstance(e, ast.Name):
        return None
    imp = m.imports.get(e.id)
    if not isinstance(imp, tuple) or len(imp) < 2 or not isinstance(imp[1], str):
        return None
    base = imp[1] + (f".{imp[2]}" if len(imp) > 2 and imp[0] == "sym" and isinstance(imp[2], str) else "")
    return ".".join([base, *reversed(parts)])


def _kwarg(call, names):
    for k in call.keywords:
        if k.arg in names:
            return k.value
    return None


def client_facts(prog, b, walk_body) -> list:
    tops = set(CLIENTS)
    facts = []
    for m in prog.modules.values():
        if not any(isinstance(v, tuple) and len(v) > 1 and isinstance(v[1], str) and v[1].split(".")[0] in tops
                   for v in m.imports.values()):
            continue
        def bare_of(nodes, self_only=False):
            """var / self.attr -> dotted constructor of a client built without arguments"""
            out = {}
            for a in nodes:
                if isinstance(a, ast.Assign) and len(a.targets) == 1 and isinstance(a.value, ast.Call) \
                        and not a.value.args and not a.value.keywords:
                    t, dv = a.targets[0], _dotted(m, a.value.func)
                    k = t.id if isinstance(t, ast.Name) and not self_only else f"self.{t.attr}" if isinstance(t, ast.Attribute) \
                        and isinstance(t.value, ast.Name) and t.value.id == "self" else None
                    if k and dv and dv.split(".")[0] in CLIENTS:
                        out[k] = dv
            return out

        scopes = [(f.id, walk_body(f.node), {}) for f in m.funcs.values()]
        for c in m.classes.values():
            own = {}                         # `self.smtp = smtplib.SMTP()` in __init__, `self.smtp.connect(..)` elsewhere
            for f in c.methods.values():
                own.update(bare_of(list(walk_body(f.node)), self_only=True))
            scopes += [(f.id, walk_body(f.node), own) for f in c.methods.values()]
        scopes.append((f"module:{m.name}", (n for st in m.tree.body if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                                            for n in ast.walk(st)), {}))
        for src, nodes, cls_bare in scopes:
            if src not in b.nodes:
                continue
            nodes = list(nodes)
            bare = {**cls_bare, **bare_of(nodes)}
            for c in nodes:
                if not isinstance(c, ast.Call):
                    continue
                dn = _dotted(m, c.func)
                fv = c.func.value if isinstance(c.func, ast.Attribute) and c.func.attr in CONNECT_METHODS else None
                bk = fv.id if isinstance(fv, ast.Name) else f"self.{fv.attr}" if isinstance(fv, ast.Attribute) \
                    and isinstance(fv.value, ast.Name) and fv.value.id == "self" else None
                if bk in bare:
                    dn = bare[bk]          # c.connect(host, port): the address of the client built above
                if not dn:
                    continue
                top, last = dn.split(".")[0], dn.rsplit(".", 1)[-1]
                if top not in CLIENTS or last not in CLIENTS[top][1]:
                    continue
                proto = CLIENTS[top][0]
                if top == "boto3":
                    if not (c.args and isinstance(c.args[0], ast.Constant) and c.args[0].value == "s3"):
                        continue
                    if _kwarg(c, ("endpoint_url",)) is None:
                        continue
                url = _kwarg(c, URL_KW)
                host = _kwarg(c, HOST_KW)
                pos = c.args[0] if c.args and top != "boto3" else None
                if url is None and host is None and pos is not None:
                    pv = _value(prog, m, pos)
                    if pv and pv[0] == "lit" and "://" in pv[1] or last in ("from_url", "URLParameters") or top in ("pymongo", "motor", "asyncpg", "aio_pika") and pv and pv[0] == "env":
                        url = pos
                    elif proto in ("smtp", "ftp", "ldap", "elasticsearch", "memcached", "amqp", "ssh") or (top == "redis" and last != "from_url"):
                        host = pos
                port = _kwarg(c, ("port",)) or (c.args[1] if len(c.args) > 1 and proto in ("smtp", "ftp", "redis", "ssh") else None)
                f = {"var": f"{dn}()", "module": m.name, "protocol": proto, "src": src, "file": m.file, "line": c.lineno,
                     "client": top, "resource": None}
                if url is not None:
                    u = _value(prog, m, url)
                    if not u or u[0] not in ("lit", "env") or (u[0] == "lit" and "://" not in u[1] and proto != "ldap"):
                        continue
                    if u[0] == "lit" and "://" not in u[1]:            # ldap3.Server("ldap.example.org")
                        f["host"], f["port"] = u, None
                    else:
                        f["url"] = u
                elif host is not None:
                    h = _value(prog, m, host)
                    if not h or h[0] not in ("lit", "env"):
                        continue
                    if h[0] == "lit" and "://" in h[1]:
                        f["url"] = h
                    else:
                        f["host"] = h
                        pv = _value(prog, m, port) if port is not None else None
                        f["port"] = pv if pv and pv[0] in ("lit", "env") else (("lit", TLS_CTORS[last]) if TLS_CTORS.get(last) else None)
                else:
                    continue
                db = _kwarg(c, ("dbname", "database", "db"))
                dv = _value(prog, m, db) if db is not None else None
                f["resource"] = dv[1] if dv and dv[0] == "lit" else None
                pw = _kwarg(c, ("password", "passwd"))
                if pw is not None:
                    pv = _value(prog, m, pw)
                    f["password"] = ("env", pv[1]) if pv and pv[0] == "env" else ("literal", f"{m.file}:{pw.lineno}") \
                        if pv and pv[0] == "lit" and pv[1] else None
                facts.append(f)
    return facts

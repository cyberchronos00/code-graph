"""Python settings -> external-system facts for codegraph/external.py (#40).

Module-level settings dicts (Django `DATABASES` / `CACHES`, NetBox-style `DATABASE` / `REDIS`, and nested aliases
`{"default": {...}}`) with a HOST / LOCATION, and URL settings (`DATABASE_URL`, `CELERY_BROKER_URL`, `BROKER_URL`,
`REDIS_URL`, `CACHE_URL`, `EMAIL_HOST`): literal values, `os.environ.get("K", default)` / `os.getenv` / `env("K")`
reads and names of other module dicts. Only facts are recorded (builder.external_facts); passwords are kept as the
env key or the location of a literal, never the value."""
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


def index(prog, b) -> int:
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
    if facts:
        b.external_facts = getattr(b, "external_facts", []) + facts
    return len(facts)

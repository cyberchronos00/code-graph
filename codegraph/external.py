"""External systems (#40, epic #30): databases, caches, brokers, mail relays, directories, file-transfer hosts and
object stores the code connects to, as `external:<protocol>:<target>` nodes.

  target   `host:port` when the address is known (a literal DSN, a `.env.example` value naming a docker-compose
           service or a non-loopback host), otherwise `env:<KEY>` (the address comes from the environment; two repos
           reading the same key meet on one node in `cg link`)
  attrs    protocol, scheme, host, port, resource, tls (true / false / null), address_source (literal | env-example |
           compose | env | config) + address_at, address_default (a framework / example default that is not used as
           the target, such as 127.0.0.1), credential_source (env | config | literal | none | unknown) + credential_at,
           user (from a DSN), deployment_name / image (docker-compose), library
  edges    CONNECTS_TO (code or a logical connection -> external, propagating; attrs.op, via), CONFIGURED_BY
           (external -> env / config node of the address), CREDENTIAL_FROM (external -> env / config node of the
           password / token)

Sources, language-agnostic over the built graph:
  * env keys read by code (env nodes, READS_ENV), grouped by prefix (DB_*, DATABASE_URL, REDIS_*, MAIL_* / SMTP_*,
    MONGO*_*, RABBITMQ_* / AMQP_*, ELASTICSEARCH_*, MEMCACHED_*, LDAP_*, SFTP_* / SSH_*, FTP_*, S3_*); every reader of
    an address key connects;
  * Laravel `connection` nodes (config/database.php): the default connection, used ones and custom ones attach to
    the external of their driver and host keys (the framework's unused template connections do not);
  * `.env.example` / `.env.sample` / `.env.dist` values and `docker-compose*.yml` / `compose*.yaml` services resolve
    the target (`DB_HOST=db` + a `db` service running postgres -> external:postgres:db:5432).
Secret values (passwords, tokens, DSN passwords) are never read into the graph: only the key and its location.
Third-party HTTP origins (`http` nodes with origin_kind = other) are shown by `cg external` as `https` systems
without extra nodes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

SCHEMES = {
    "postgres": "postgres", "postgresql": "postgres", "pgsql": "postgres", "postgis": "postgres",
    "mysql": "mysql", "mariadb": "mysql", "mysql2": "mysql", "sqlsrv": "mssql", "mssql": "mssql", "sqlserver": "mssql",
    "oracle": "oracle", "mongodb": "mongodb", "mongodb+srv": "mongodb", "redis": "redis", "rediss": "redis",
    "valkey": "redis", "amqp": "amqp", "amqps": "amqp", "smtp": "smtp", "smtps": "smtp", "submission": "smtp",
    "ldap": "ldap", "ldaps": "ldap", "sftp": "ssh", "ssh": "ssh", "scp": "ssh", "ftp": "ftp", "ftps": "ftp",
    "s3": "s3", "memcached": "memcached", "memcache": "memcached", "elasticsearch": "elasticsearch",
    "nats": "nats", "kafka": "kafka", "mqtt": "mqtt", "mqtts": "mqtt", "imap": "imap", "imaps": "imap",
    "pop3": "pop3", "pop3s": "pop3", "clickhouse": "clickhouse", "cassandra": "cassandra",
}
TLS_SCHEMES = {"rediss", "amqps", "smtps", "ldaps", "ftps", "mqtts", "imaps", "pop3s", "https", "mongodb+srv", "sftp", "ssh",
               "scp", "s3"}
PLAIN_SCHEMES = {"redis", "amqp", "smtp", "ldap", "ftp", "mqtt", "imap", "pop3", "http"}
PORTS = {"postgres": 5432, "mysql": 3306, "mssql": 1433, "oracle": 1521, "mongodb": 27017, "redis": 6379, "amqp": 5672,
         "smtp": 587, "ldap": 389, "ssh": 22, "ftp": 21, "memcached": 11211, "elasticsearch": 9200, "nats": 4222,
         "kafka": 9092, "mqtt": 1883, "imap": 143, "pop3": 110, "clickhouse": 9000, "cassandra": 9042, "https": 443}
SCHEME_PORTS = {"amqps": 5671, "smtps": 465, "ldaps": 636, "ftps": 990, "mqtts": 8883, "imaps": 993, "pop3s": 995}
TLS_PORTS = {"smtp": {465}, "ldap": {636}, "imap": {993}, "pop3": {995}, "redis": set(), "amqp": {5671}, "mqtt": {8883},
             "ftp": {990}}
DRIVERS = {"pgsql": "postgres", "mysql": "mysql", "mariadb": "mysql", "sqlsrv": "mssql", "oracle": "oracle",
           "mongodb": "mongodb", "redis": "redis", "sqlite": None}
# env key prefix -> protocol (None: from DB_CONNECTION / the URL scheme)
PREFIXES = [("DATABASE", None), ("DB", None), ("POSTGRES", "postgres"), ("POSTGRESQL", "postgres"), ("PG", "postgres"),
            ("MYSQL", "mysql"), ("MARIADB", "mysql"), ("MSSQL", "mssql"), ("REDIS", "redis"), ("VALKEY", "redis"),
            ("MAIL", "smtp"), ("SMTP", "smtp"), ("EMAIL", "smtp"), ("MONGO", "mongodb"), ("MONGODB", "mongodb"),
            ("RABBITMQ", "amqp"), ("RABBIT", "amqp"), ("AMQP", "amqp"), ("BROKER", None), ("ELASTICSEARCH", "elasticsearch"),
            ("OPENSEARCH", "elasticsearch"), ("ELASTIC", "elasticsearch"), ("MEMCACHED", "memcached"), ("LDAP", "ldap"),
            ("SFTP", "ssh"), ("SSH", "ssh"), ("FTP", "ftp"), ("S3", "s3"), ("MINIO", "s3"), ("NATS", "nats"),
            ("KAFKA", "kafka"), ("MQTT", "mqtt"), ("IMAP", "imap"), ("CLICKHOUSE", "clickhouse")]
ADDRESS = ("URL", "URI", "DSN", "HOST", "HOSTNAME", "SERVER", "ENDPOINT", "ADDR", "ADDRESS", "BROKERS")
SECRET = ("PASSWORD", "PASS", "PASSWD", "SECRET", "TOKEN", "SECRET_KEY", "SECRET_ACCESS_KEY", "KEY", "AUTH")
IMAGES = [("postgis", "postgres"), ("postgres", "postgres"), ("pgvector", "postgres"), ("timescale", "postgres"),
          ("mariadb", "mysql"), ("mysql", "mysql"), ("mssql", "mssql"), ("valkey", "redis"), ("redis", "redis"),
          ("mongo", "mongodb"), ("rabbitmq", "amqp"), ("elasticsearch", "elasticsearch"), ("opensearch", "elasticsearch"),
          ("memcached", "memcached"), ("minio", "s3"), ("localstack", "s3"), ("mailhog", "smtp"), ("mailpit", "smtp"),
          ("maildev", "smtp"), ("openldap", "ldap"), ("nats", "nats"), ("kafka", "kafka"), ("mosquitto", "mqtt"),
          ("clickhouse", "clickhouse"), ("cassandra", "cassandra")]
LOOPBACK = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal", ""}
ENV_FILES = (".env.example", ".env.sample", ".env.dist", ".env.template", ".env.defaults", "example.env", "env.example")


# ------------------------------------------------------------------ DSN / URL parsing
def parse_dsn(url: str) -> dict | None:
    """`postgres://user:pw@db:5432/app?sslmode=require`, `jdbc:mysql://h/db`, `rediss://:pw@cache:6380/0` ... ->
    {protocol, scheme, host, port, resource, user, has_password, tls}; None when it is not a known scheme or has no
    host. The password is never returned."""
    if not url or "://" not in url:
        return None
    u = url.strip()
    if u.lower().startswith("jdbc:"):
        u = u[5:]
    scheme = u.split("://", 1)[0].lower()
    base = scheme.split("+")[0] if scheme not in SCHEMES else scheme
    proto = SCHEMES.get(scheme) or SCHEMES.get(base) or ("https" if base in ("http", "https") else None)
    if proto is None:
        return None
    try:
        p = urlsplit(u)
        host = (p.hostname or "").lower()
        port = p.port
    except ValueError:
        return None
    if not host or "{" in host or "$" in host:
        return None
    tls = True if scheme in TLS_SCHEMES or re.search(r"ssl(mode)?=(require|verify|true|1)|tls=true", p.query or "", re.I) \
        else False if scheme in PLAIN_SCHEMES else None
    res = unquote(p.path.lstrip("/")) or None
    return {"protocol": proto, "scheme": scheme, "host": host, "port": port or SCHEME_PORTS.get(scheme) or PORTS.get(proto), "resource": res,
            "user": unquote(p.username) if p.username else None, "has_password": bool(p.password), "tls": tls}


def target_of(host: str, port) -> str:
    return f"{host}:{port}" if port else host


# ------------------------------------------------------------------ project files
def read_env_example(root: Path) -> dict:
    """Non-secret values of the example env files at the root: {KEY: (value, file:line)}; secret keys keep only
    their location (value None); a DSN password becomes `***`."""
    out = {}
    for name in ENV_FILES:
        f = root / name
        if not f.is_file():
            continue
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines, 1):
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", ln)
            if not m or m.group(1) in out:
                continue
            k, v = m.group(1), m.group(2).strip()
            if v[:1] in "\"'" and v[-1:] == v[:1]:
                v = v[1:-1]
            elif " #" in v:
                v = v.split(" #", 1)[0].strip()
            if "://" in v:                                   # DSN password -> placeholder (presence kept, value dropped)
                v = re.sub(r"(://[^:/@\s]*):[^@/\s]+@", r"\1:***@", v)
            out[k] = (None if _secret(k) else v, f"{name}:{i}")
    return out


def read_compose(root: Path) -> dict:
    """docker-compose services with a known image: {service: {protocol, image, port, file}}."""
    out = {}
    files = sorted({*root.glob("docker-compose*.y*ml"), *root.glob("compose*.y*ml"), *root.glob("docker/docker-compose*.y*ml")})
    if not files:
        return out
    try:
        import yaml
    except ImportError:
        return out
    for f in files:
        try:
            d = yaml.safe_load(f.read_text(errors="replace")) or {}
        except Exception:  # noqa: BLE001
            continue
        for svc, spec in ((d.get("services") or {}) if isinstance(d, dict) else {}).items():
            if not isinstance(spec, dict):
                continue
            img = str(spec.get("image") or "")
            name = img.rsplit("/", 1)[-1].split(":", 1)[0].lower() or str(svc).lower()
            proto = next((p for k, p in IMAGES if k in name), None)
            if proto is None:
                continue
            out[str(svc)] = {"protocol": proto, "image": img or None, "port": PORTS.get(proto),
                             "file": str(f.relative_to(root))}
            for alias in ((spec.get("container_name"),) if spec.get("container_name") else ()):
                out.setdefault(str(alias), out[str(svc)])
    return out


def _secret(key: str) -> bool:
    k = key.upper()
    return any(k.endswith("_" + s) or k == s for s in SECRET) and not k.endswith(("_KEY_ID", "_PUBLIC_KEY"))


def _split(key: str):
    """DB_HOST -> ('DB', 'HOST'); DATABASE_URL -> ('DATABASE', 'URL'); REDIS_CACHE_HOST -> ('REDIS_CACHE', 'HOST')."""
    k = key.upper()
    for pre, proto in sorted(PREFIXES, key=lambda x: -len(x[0])):
        if k == pre or k.startswith(pre + "_"):
            rest = k[len(pre) + 1:]
            parts = rest.split("_") if rest else []
            if not parts:
                return None
            role = "_".join(parts[-2:]) if "_".join(parts[-2:]) in SECRET else parts[-1]
            group = "_".join([pre, *parts[:-len(role.split("_"))]])
            return pre, proto, group, role
    return None


SQL_DEPS = {"pg": "postgres", "postgres": "postgres", "pg-promise": "postgres", "@neondatabase/serverless": "postgres",
            "mysql": "mysql", "mysql2": "mysql", "mariadb": "mysql", "mssql": "mssql", "tedious": "mssql",
            "oracledb": "oracle", "psycopg": "postgres", "psycopg2": "postgres", "psycopg2-binary": "postgres",
            "asyncpg": "postgres", "mysqlclient": "mysql", "pymysql": "mysql", "aiomysql": "mysql", "pyodbc": "mssql",
            "cx_oracle": "oracle"}


def dep_sql_protocol(root: Path, compose: dict | None = None) -> str | None:
    """The one SQL protocol a project's dependencies (package.json, requirements*.txt, pyproject.toml) or compose
    services point to, for `DB_*` / `DATABASE_URL` keys without a driver or URL scheme; None when none or several."""
    found = set()
    pj = root / "package.json"
    if pj.is_file():
        try:
            d = json.loads(pj.read_text(errors="replace"))
            for sec in ("dependencies", "devDependencies", "optionalDependencies"):
                found |= {SQL_DEPS[k.lower()] for k in (d.get(sec) or {}) if k.lower() in SQL_DEPS}
        except (OSError, ValueError, AttributeError):
            pass
    for f in [*root.glob("requirements*.txt"), root / "pyproject.toml"]:
        if f.is_file():
            try:
                txt = f.read_text(errors="replace").lower()
            except OSError:
                continue
            found |= {v for k, v in SQL_DEPS.items() if re.search(rf"(^|[\s\"'\[])({re.escape(k)})\s*([<>=~!\[;\"',]|$)", txt, re.M)}
    if not found and compose:
        found = {c["protocol"] for c in compose.values() if c.get("protocol") in ("postgres", "mysql", "mssql", "oracle")}
    return found.pop() if len(found) == 1 else None


# ------------------------------------------------------------------ index-time pass
def attach(builder, root: Path) -> dict:
    root = Path(root)
    envs = {n.id[len("env:"):]: n for n in builder.nodes.values() if n.kind == "env"}
    conns = [n for n in builder.nodes.values() if n.kind == "connection"]
    facts = getattr(builder, "external_facts", None) or []
    if not envs and not conns and not facts:
        return {}
    ex = read_env_example(root)
    compose = read_compose(root)
    readers: dict = {}
    for e in builder.edges.values():
        if e.kind == "READS_ENV":
            readers.setdefault(e.dst, []).append(e)
    st = {"systems": 0, "connects": 0, "from_connections": 0, "compose_services": len(compose)}

    def val(key):
        v = ex.get(key)
        return v[0] if v else None

    def resolve(proto, host_key=None, port_key=None, url_key=None, host_val=None, port_val=None, src_hint=None):
        """(target, attrs, confidence) for an address."""
        a = {}
        if url_key:
            d = parse_dsn(val(url_key) or "")
            if d:
                proto = proto or d["protocol"]
                a.update({k: d[k] for k in ("scheme", "resource", "user", "tls") if d.get(k) is not None})
                a["credential_in_url"] = d["has_password"] or None
                host, port = d["host"], d["port"]
                src = ("env-example", ex[url_key][1])
            else:
                host, port, src = None, None, None
        else:
            host = (host_val if host_val is not None else val(host_key) if host_key else None)
            pv = port_val if port_val is not None else val(port_key) if port_key else None
            port = int(pv) if pv and str(pv).isdigit() else None
            src = ("env-example", ex[host_key][1]) if host_key and val(host_key) else (src_hint if host_val else None)
        if proto is None:
            return None
        if host and host.lower() in compose:
            c = compose[host.lower()]
            a.update({"deployment_name": host.lower(), "image": c["image"], "compose_file": c["file"]})
            port = port or c["port"]
            return target_of(host.lower(), port or PORTS.get(proto)), {**a, "address_source": "compose",
                                                                       "address_at": src[1] if src else c["file"]}, "resolved", proto
        if host and host.lower() not in LOOPBACK and re.match(r"^[A-Za-z0-9.\-]+$", host):
            return target_of(host.lower(), port or PORTS.get(proto)), {**a, "address_source": src[0] if src else "literal",
                                                                       "address_at": src[1] if src else None}, \
                "resolved" if src and src[0] == "env-example" else "exact", proto
        key = url_key or host_key
        if key is None:
            return None
        if host:
            a["address_default"] = target_of(host.lower(), port or PORTS.get(proto))
        return f"env:{key}", {**a, "address_source": "env"}, "heuristic", proto

    def node(proto, target, attrs, conf):
        nid = builder.add_node("external", f"{proto}:{target}", f"{proto}:{target}", fqn=f"{proto}:{target}",
                               attrs={"protocol": proto, "target": target, "confidence": conf})
        n = builder.nodes[nid]
        for k, v in attrs.items():
            if v not in (None, "", []):
                n.attrs.setdefault(k, v)
        if ":" in target and not target.startswith(("env:", "config:")):
            h, _, p = target.rpartition(":")
            n.attrs.setdefault("host", h)
            if p.isdigit():
                n.attrs.setdefault("port", int(p))
                if n.attrs.get("tls") is None and int(p) in TLS_PORTS.get(proto, set()):
                    n.attrs["tls"] = True
        if nid not in seen:
            seen.add(nid)
            st["systems"] += 1
        return nid

    seen: set = set()
    used_keys: set = set()

    def cred(nid, keys):
        for k in keys:
            eid = f"env:{k}"
            if eid in builder.nodes:
                builder.add_edge(nid, eid, "CREDENTIAL_FROM", None, None, "resolved", secret_kind=_cred_kind(k))
                builder.nodes[nid].attrs.setdefault("credential_source", "env")
                builder.nodes[nid].attrs.setdefault("credential_at", f"env:{k}")

    # ---- Laravel connections (config/database.php) -> external of their driver and host
    cfg = {n.id[len("config:"):]: n for n in builder.nodes.values() if n.kind == "config"}
    default = None
    dn = cfg.get("database.default")
    if dn is not None:
        da = dn.attrs or {}
        envk = (da.get("env") or [None])[0]
        default = (val(envk) if envk else None) or (da.get("env_default") or [None])[0] or da.get("value")
    used = {e.dst for e in builder.edges.values() if e.kind == "USES_CONNECTION"}
    templates = {"sqlite", "mysql", "mariadb", "pgsql", "sqlsrv"}
    for c in conns:
        name = c.id.split(":", 1)[1]
        if name in templates and name != default and c.id not in used:
            continue
        base = f"database.connections.{name}"

        def leaf(k):
            n = cfg.get(f"{base}.{k}")
            a = (n.attrs or {}) if n else {}
            ek = (a.get("env") or [None])[0]
            return ek, (val(ek) if ek and val(ek) else None) or (a.get("value") if "value" in a else None) or \
                ((a.get("env_default") or [None])[0]), n
        drv = (leaf("driver")[1]) or name
        proto = DRIVERS.get(str(drv).lower(), SCHEMES.get(str(drv).lower()))
        if proto is None:
            continue
        hk, hv, hn = leaf("host")
        pk, pv, _ = leaf("port")
        uk, uv, _ = leaf("url")
        r = None
        if uk and val(uk):
            r = resolve(proto, url_key=uk)
        if r is None:
            if hk:
                r = resolve(proto, host_key=hk, port_key=pk) or None
                if r and r[0].startswith("env:") and hv:
                    pass
            elif hv:
                r = resolve(proto, host_val=str(hv), port_val=pv, src_hint=("config", f"config:{base}.host"))
        if r is None:
            continue
        target, attrs, conf, proto = r
        nid = node(proto, target, {**attrs, "library": "laravel", "connection": name}, conf)
        builder.add_edge(c.id, nid, "CONNECTS_TO", c.file, c.line, conf, op="query", via=f"connection {name}")
        if hn is not None:
            builder.add_edge(nid, hn.id, "CONFIGURED_BY", None, None, conf)
        if hk:
            used_keys.add(hk)
            if f"env:{hk}" in builder.nodes:
                builder.add_edge(nid, f"env:{hk}", "CONFIGURED_BY", None, None, conf)
        for k in ("password", "username"):
            ck, _cv, cn = leaf(k)
            if k == "password" and ck:
                cred(nid, [ck])
                used_keys.add(ck)
            elif k == "password" and cn is not None and (cn.attrs or {}).get("value"):
                builder.nodes[nid].attrs["credential_source"] = "literal"
                builder.nodes[nid].attrs["credential_at"] = cn.id
        st["from_connections"] += 1

    # ---- facts from language plugins (Python settings dicts / URLs)
    defaults_at: set = set()

    def code_default(key, default, f):
        """`os.environ.get("K", "db")`: the in-code default stands in for a missing .env.example value."""
        if default and key not in ex and not _secret(key):
            at = f"{f['file']}:{f['line']}"
            ex[key] = (default, at)
            defaults_at.add(at)

    for f in facts:
        u = f.get("url")
        r = None
        if u is not None:
            if u[0] == "lit":
                d = parse_dsn(u[1])
                if d:
                    r = (target_of(d["host"], d["port"]), {"address_source": "literal", "address_at": f"{f['file']}:{f['line']}",
                         **{k: d[k] for k in ("scheme", "resource", "user", "tls") if d.get(k) is not None}}, "exact", d["protocol"])
                    if d["has_password"]:
                        r[1].update(credential_source="literal", credential_at=f"{f['file']}:{f['line']}")
                elif f.get("host_only") and f["protocol"]:
                    r = resolve(f["protocol"], host_val=u[1], src_hint=("literal", f"{f['file']}:{f['line']}"))
            else:
                code_default(u[1], u[2], f)
                r = resolve(f["protocol"], url_key=u[1]) if not f.get("host_only") else resolve(f["protocol"], host_key=u[1])
                if r is None and f["protocol"]:
                    r = (f"env:{u[1]}", {"address_source": "env"}, "heuristic", f["protocol"])
        else:
            h, p = f.get("host"), f.get("port")
            pv = p[1] if p and p[0] == "lit" else (p[2] if p and p[0] == "env" else None)
            proto = f["protocol"] or (dep_sql_protocol(root, compose) if f["var"].startswith("DATABASE") else None) or "sql"
            if h[0] == "lit":
                r = resolve(proto, host_val=h[1], port_val=pv, src_hint=("literal", f"{f['file']}:{f['line']}"))
                if r is None or r[0].startswith("env:"):
                    r = (f"config:{f['module']}.{f['var']}", {"address_source": "config", "address_at": f"{f['file']}:{f['line']}",
                                                "address_default": target_of(h[1] or "localhost", pv or PORTS.get(proto))}, "heuristic", proto)
            elif h[0] == "env":
                code_default(h[1], h[2], f)
                r = resolve(proto, host_key=h[1], port_val=pv)
        if r is None:
            continue
        target, attrs, conf, proto = r
        if attrs.get("address_source") == "env-example" and attrs.get("address_at") in defaults_at:
            attrs = {**attrs, "address_source": "code-default"}
        nid = node(proto, target, {**attrs, "setting": f["var"], "resource": f.get("resource")}, conf)
        builder.add_edge(f["src"], nid, "CONNECTS_TO", f["file"], f["line"], conf, op="configure", via=f"setting {f['var']}")
        st["connects"] += 1
        for kk in ([u[1]] if u is not None and u[0] == "env" else []) + ([f["host"][1]] if f.get("host") and f["host"][0] == "env" else []):
            if f"env:{kk}" in builder.nodes:
                builder.add_edge(nid, f"env:{kk}", "CONFIGURED_BY", None, None, conf)
            used_keys.add(kk)
        pw = f.get("password")
        if pw and pw[0] == "env":
            cred(nid, [pw[1]])
            builder.nodes[nid].attrs.setdefault("credential_source", "env")
            builder.nodes[nid].attrs.setdefault("credential_at", f"env:{pw[1]}")
            used_keys.add(pw[1])
        elif pw:
            builder.nodes[nid].attrs.update(credential_source="literal", credential_at=pw[1])

    # ---- env keys read by code, grouped by prefix
    groups: dict = {}
    for key in set(envs) | {k for k in ex if _split(k)}:
        sp = _split(key)
        if sp:
            pre, proto, group, role = sp
            groups.setdefault(group, {"proto": proto, "pre": pre, "keys": {}})["keys"][role] = key
    for group, g in sorted(groups.items()):
        ks = g["keys"]
        addr = [ks[r] for r in ("URL", "URI", "DSN") if r in ks] or [ks[r] for r in ADDRESS[3:] if r in ks]
        addr = [k for k in addr if k not in used_keys]
        if not addr:
            continue
        akey = addr[0]
        if f"env:{akey}" not in builder.nodes:        # nothing reads it: an example file entry only
            continue
        proto = g["proto"]
        if g["pre"] in ("DB", "DATABASE") and proto is None:
            conn = val("DB_CONNECTION") or val("DATABASE_CONNECTION")
            proto = DRIVERS.get((conn or "").lower(), SCHEMES.get((conn or "").lower()))
            if proto is None and not conn:
                proto = dep_sql_protocol(root, compose)
                g["dep"] = proto is not None
        if g["pre"] == "MAIL" and (val("MAIL_MAILER") or val("MAIL_DRIVER") or "smtp").lower() != "smtp":
            continue
        is_url = akey.endswith(("_URL", "_URI", "_DSN"))
        r = resolve(proto, url_key=akey) if is_url else resolve(proto, host_key=akey, port_key=ks.get("PORT"))
        if r is None and is_url and proto:
            r = (f"env:{akey}", {"address_source": "env"}, "heuristic", proto)
        if r is None:
            continue
        target, attrs, conf, proto = r
        if g.get("dep"):
            attrs = {**attrs, "protocol_source": "dependencies"}
        nid = node(proto, target, attrs, conf)
        builder.add_edge(nid, f"env:{akey}", "CONFIGURED_BY", None, None, conf)
        if attrs.get("credential_in_url"):
            builder.add_edge(nid, f"env:{akey}", "CREDENTIAL_FROM", None, None, "resolved", secret_kind="url password")
            builder.nodes[nid].attrs.setdefault("credential_source", "env")
            builder.nodes[nid].attrs.setdefault("credential_at", f"env:{akey}")
        cred(nid, [ks[r] for r in ks if r in SECRET or r.endswith(tuple(SECRET))])
        for e in readers.get(f"env:{akey}", []):
            src = builder.nodes.get(e.src)
            if src is None or src.kind in ("config", "env"):
                continue
            builder.add_edge(e.src, nid, "CONNECTS_TO", e.file, e.line, "heuristic" if conf == "heuristic" else "resolved",
                             op="connect", via=f"env {akey}")
            st["connects"] += 1
    return {k: v for k, v in st.items() if v} if st["systems"] else {}


def _cred_kind(key: str) -> str:
    k = key.upper()
    return "token" if "TOKEN" in k else "key" if k.endswith("KEY") else "password"


# ------------------------------------------------------------------ query: cg external / MCP external_systems
def external(st, pattern: str | None = None, protocol: str | None = None, source: str | None = None,
             tls_off: bool = False, max_items: int = 200) -> dict:
    import fnmatch
    rows = list(st.q("SELECT id, name, attrs FROM nodes WHERE kind='external'"))
    systems = []
    for r in rows:
        a = json.loads(r["attrs"] or "{}")
        systems.append({"id": r["id"], "protocol": a.get("protocol"), "target": a.get("target"), "attrs": a})
    # third-party HTTP origins: an adapter over http nodes (no extra nodes)
    https: dict = {}
    for r in st.q("SELECT id, attrs FROM nodes WHERE kind='http' AND attrs LIKE '%\"origin_kind\": \"other\"%'"):
        a = json.loads(r["attrs"] or "{}")
        o = a.get("origin") or ""
        d = parse_dsn(o) if "://" in o else None
        if not d or a.get("test_only"):
            continue
        kinds = {x["kind"] for x in st.q("SELECT kind FROM edges WHERE dst=?", (r["id"],))}
        if kinds and all(k.startswith("TEST_") for k in kinds):          # only tests call it (fixtures, mocks)
            continue
        key = target_of(d["host"], d["port"])
        h = https.setdefault(key, {"id": f"external:https:{key}", "protocol": "https", "target": key,
                                   "attrs": {"protocol": "https", "host": d["host"], "port": d["port"], "tls": d["tls"],
                                             "address_source": "literal", "adapter": "http"}, "http": []})
        h["http"].append(r["id"])
    systems += list(https.values())
    out = []
    for s in sorted(systems, key=lambda x: x["id"]):
        a = s["attrs"]
        if protocol and s["protocol"] != protocol:
            continue
        if source and a.get("address_source") != source and a.get("credential_source") != source:
            continue
        if tls_off and a.get("tls") is not False:
            continue
        if pattern and not (fnmatch.fnmatchcase(s["id"], pattern) or pattern.lower() in s["id"].lower()):
            continue
        if s.get("http"):
            users = [{"id": h, "via": "http", "op": "request"} for h in s["http"]]
            ids = []
            for h in s["http"]:
                ids += [x["src"] for x in st.q("SELECT src FROM edges WHERE dst=? AND kind IN ('CALLS_API','SENDS_TO','CALLS')", (h,))]
        else:
            users = [{"id": x["src"], "op": json.loads(x["attrs"] or "{}").get("op"), "via": json.loads(x["attrs"] or "{}").get("via"),
                      "at": f"{x['file']}:{x['line']}" if x["file"] else None, "confidence": x["confidence"]}
                     for x in st.q("SELECT src, attrs, file, line, confidence FROM edges WHERE dst=? AND kind IN ('CONNECTS_TO','TEST_USES')", (s["id"],))]
            ids = [u["id"] for u in users]
        callers = set(ids)
        for x in list(ids):
            if x.startswith("connection:"):
                callers |= {y["src"] for y in st.q("SELECT src FROM edges WHERE dst=? AND kind='USES_CONNECTION'", (x,))}
        entries = {}
        for c in callers:
            for e in st.q("SELECT entry_kind, entry_count FROM node_entry WHERE node_id=?", (c,)):
                entries[e["entry_kind"]] = entries.get(e["entry_kind"], 0) + e["entry_count"]
        conf = [(x["dst"], x["kind"]) for x in st.q("SELECT dst, kind FROM edges WHERE src=? AND kind IN ('CONFIGURED_BY','CREDENTIAL_FROM')", (s["id"],))]
        out.append({**s, "users": users, "callers": len(callers), "entry_kinds": entries,
                    "configured_by": sorted({k for k, v in conf if v == "CONFIGURED_BY"}),
                    "credential_from": sorted({k for k, v in conf if v == "CREDENTIAL_FROM"})})
    summ: dict = {}
    for s in out:
        summ[s["protocol"]] = summ.get(s["protocol"], 0) + 1
    return {"summary": dict(sorted(summ.items())), "systems": out[:max_items], "selected": len(out),
            "filters": {"pattern": pattern, "protocol": protocol, "source": source, "tls_off": tls_off}}


def render_external(res: dict, max_items: int = 60) -> str:
    if not res["systems"]:
        return "no external systems found (see docs/external.md for what is recognised)"
    L = [f"external systems: {res['selected']} (" + ", ".join(f"{k} {v}" for k, v in res["summary"].items()) + ")", ""]
    for s in res["systems"][:max_items]:
        a = s["attrs"]
        bits = [f"address {a.get('address_source')}" + (f" @ {a['address_at']}" if a.get("address_at") else "")]
        if a.get("address_default"):
            bits.append(f"default {a['address_default']}")
        if a.get("deployment_name"):
            bits.append(f"compose service {a['deployment_name']}" + (f" ({a['image']})" if a.get("image") else ""))
        if a.get("credential_source"):
            bits.append(f"credentials {a['credential_source']}" + (f" @ {a['credential_at']}" if a.get("credential_at") else ""))
        if a.get("tls") is not None:
            bits.append("tls" if a["tls"] else "plaintext")
        if a.get("resource"):
            bits.append(f"resource {a['resource']}")
        L.append(f"{s['id']}  [{a.get('confidence') or 'exact'}]")
        L.append("    " + "; ".join(bits))
        if s["entry_kinds"]:
            L.append("    reached from " + ", ".join(f"{k}({v})" for k, v in sorted(s["entry_kinds"].items())))
        for u in s["users"][:8]:
            L.append(f"    <- {u['id']}" + (f" @ {u['at']}" if u.get("at") else "") + (f"  ({u['via']})" if u.get("via") else ""))
        if len(s["users"]) > 8:
            L.append(f"    ... {len(s['users']) - 8} more users")
        if s["configured_by"]:
            L.append("    configured by " + ", ".join(s["configured_by"][:6]))
    if res["selected"] > max_items:
        L.append(f"... {res['selected'] - max_items} more")
    return "\n".join(L)

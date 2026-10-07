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
Third-party HTTP origins (`http` nodes with origin_kind = other) become real `external:http(s):<host>:<port>`
nodes (`http` → port 80, `https` → 443, or the explicit port) with CONNECTS_TO from the calling function
(`via` http). Template hosts and loopback addresses stay unattached; `cg external` reads those nodes
(no query-time adapter).
"""
from __future__ import annotations

from . import presets

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
    "http": "http", "https": "https",
}
TLS_SCHEMES = {"rediss", "amqps", "smtps", "ldaps", "ftps", "mqtts", "imaps", "pop3s", "https", "mongodb+srv", "sftp", "ssh",
               "scp", "s3"}
PLAIN_SCHEMES = {"redis", "amqp", "smtp", "ldap", "ftp", "mqtt", "imap", "pop3", "http"}
PORTS = {"postgres": 5432, "mysql": 3306, "mssql": 1433, "oracle": 1521, "mongodb": 27017, "redis": 6379, "amqp": 5672,
         "smtp": 587, "ldap": 389, "ssh": 22, "ftp": 21, "memcached": 11211, "elasticsearch": 9200, "nats": 4222,
         "kafka": 9092, "mqtt": 1883, "imap": 143, "pop3": 110, "clickhouse": 9000, "cassandra": 9042, "https": 443, "http": 80}
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
          ("maildev", "smtp"), ("openldap", "ldap"), ("nats", "nats"), ("mosquitto", "mqtt"),
          ("clickhouse", "clickhouse"), ("cassandra", "cassandra")]
# Exact image basenames for Kafka brokers only — never substring-match UIs/sidecars
# (kafka-ui, kafdrop, kafka-exporter, schema-registry, kafka-connect, …).
KAFKA_IMAGES = {"kafka", "cp-kafka", "cp-server", "redpanda"}
LLM_KEYS = {"openai": ("OPENAI_API_KEY",), "azure-openai": ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_KEY"),
            "anthropic": ("ANTHROPIC_API_KEY",), "ollama": (), "openai-agents": ("OPENAI_API_KEY",)}
LOOPBACK = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal", ""}
# compose services listed by `cg external` even when no client URL resolves to them (#158)
COMPOSE_ONLY = {"kafka", "amqp", "nats", "redis", "mqtt"}
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
    proto = SCHEMES.get(scheme) or SCHEMES.get(base)
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
            # Kafka: exact basename only (bitnami/kafka, apache/kafka, wurstmeister/kafka,
            # confluentinc/cp-kafka|cp-server, redpandadata|vectorized/redpanda).
            if name in KAFKA_IMAGES:
                proto = "kafka"
            else:
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
SPRING_SKIP = {"h2", "hsqldb", "derby", "sqlite"}
_SKIP = presets.skip_dirs("common")


def read_spring_datasources(root: Path) -> list:
    """`spring.datasource.url` in application*.properties / *.yml (#41 step 3). Embedded H2/HSQLDB/Derby/SQLite skipped."""
    out, files = [], []
    for pat in ("**/application*.properties", "**/application*.yml", "**/application*.yaml"):
        files += [p for p in root.glob(pat) if _SKIP.isdisjoint(p.parts)]
    for f in list(dict.fromkeys(files))[:20]:
        try:
            txt = f.read_text()
        except Exception:
            continue
        rel = str(f.relative_to(root))
        props = {}
        if f.suffix == ".properties":
            for m in re.finditer(r"^(spring\.datasource\.\S+?)\s*=\s*(.+)$", txt, re.M):
                props[m.group(1).strip()] = m.group(2).strip()
        else:
            for m in re.finditer(r"^spring\.datasource\.(\S+?)\s*:\s*(.+)$", txt, re.M):
                props[f"spring.datasource.{m.group(1)}"] = m.group(2).strip().strip("\"'")
            block = re.search(r"(?m)^spring:\s*$\n((?:[ \t]+.*\n)*)", txt)
            if block:
                ds = re.search(r"(?m)^[ \t]+datasource:\s*$\n((?:[ \t]+.*\n)*)", block.group(1))
                if ds:
                    for m in re.finditer(r"(?m)^[ \t]+(url|username|password|driver-class-name)\s*:\s*(.+)$", ds.group(1)):
                        props[f"spring.datasource.{m.group(1)}"] = m.group(2).strip().strip("\"'")
        url = props.get("spring.datasource.url")
        if not url:
            continue
        em = re.fullmatch(r"\$\{(\w+)(?::([^}]*))?\}", url)
        line = next((i for i, L in enumerate(txt.splitlines(), 1)
                     if "datasource.url" in L or re.match(r"\s+url\s*:", L)), 1)
        fact = {"datasource": "spring", "protocol": None, "file": rel, "line": line,
                "var": "spring.datasource", "module": rel, "library": "spring", "tables": []}
        if em:
            fact["url"] = ("env", em.group(1), em.group(2))
            d = parse_dsn(em.group(2) or "") if em.group(2) else None
            fact["protocol"] = (d["protocol"] if d else None) or "sql"
        else:
            d = parse_dsn(url)
            if not d:
                continue
            if d["protocol"] in SPRING_SKIP:
                continue
            fact["url"] = ("lit", url)
            fact["protocol"] = d["protocol"]
        if fact["protocol"] in SPRING_SKIP or fact["protocol"] is None:
            continue
        pw = props.get("spring.datasource.password")
        if pw and not pw.startswith("${"):
            fact["password"] = ("lit", f"{rel}:{line}")
        elif pw and (pm := re.fullmatch(r"\$\{(\w+)(?::([^}]*))?\}", pw)):
            fact["password"] = ("env", pm.group(1), None)
        out.append(fact)
    return out

def attach(builder, root: Path) -> dict:
    root = Path(root)
    envs = {n.id[len("env:"):]: n for n in builder.nodes.values() if n.kind == "env"}
    conns = [n for n in builder.nodes.values() if n.kind == "connection"]
    # Spring datasources before the empty-check so a repo with only application*.properties still indexes (#41)
    if (spring := read_spring_datasources(root)):
        jpa_orm = sorted({n.name for n in builder.nodes.values()
                          if n.kind == "table" and (
                              (n.attrs or {}).get("orm") in ("Entity", "Table")
                              or (n.attrs or {}).get("via") in ("spring-data", "exposed"))})
        for f in spring:
            f["tables"] = jpa_orm
        builder.external_facts = getattr(builder, "external_facts", []) + spring
    facts = getattr(builder, "external_facts", None) or []
    # model calls made from test code are fixtures, not systems the application talks to (as for third-party HTTP)
    llm = [n for n in builder.nodes.values() if (n.attrs or {}).get("llm_calls") and not n.attrs.get("test")]
    # Cloud SDK calls (boto3, S3Client, Storage::disk, …) can be the only signal — do not return
    # before attach_sdk. http_other / compose-only brokers still run through the loops below.
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
                proto = d["protocol"] if (not proto or proto == "sql") else proto
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
            # host:port only. `aws` targets such as `sqs:env:QUEUE` and `secretsmanager:prod/db` are not addresses.
            if p.isdigit():
                n.attrs.setdefault("host", h)
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
                if d and f.get("client") and (d["host"] or "").lower() in LOOPBACK:
                    continue                                   # redis.from_url("redis://localhost"): no system
                if d:
                    # an HTTP endpoint of a client that speaks another protocol over it (boto3 S3 endpoint_url)
                    pr = f["protocol"] if f.get("client") and d["protocol"] in ("http", "https") and f["protocol"] else d["protocol"]
                    host, port = d["host"], d["port"]
                    a = {"address_source": "literal", "address_at": f"{f['file']}:{f['line']}",
                         **{k: d[k] for k in ("scheme", "resource", "user", "tls") if d.get(k) is not None}}
                    if host and host.lower() in compose:
                        c = compose[host.lower()]
                        a.update({"deployment_name": host.lower(), "image": c["image"], "compose_file": c["file"],
                                  "address_source": "compose"})
                        port = port or c["port"]
                    r = (target_of(host, port), a, "exact", pr)
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
                if f.get("client") and (r is None or r[0].startswith("env:")):
                    continue                                   # a client pointed at the local machine: no system
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
        if f.get("datasource"):    # an ORM datasource (Prisma): the tables of its models live in the system (#41)
            nid = node(proto, target, {**attrs, "library": f["library"], "datasource": f["datasource"],
                                       "declared_at": f"{f['file']}:{f['line']}"}, conf)
            for t in f["tables"]:
                tid = f"table:{t}"
                if tid in builder.nodes:
                    builder.nodes[tid].attrs.setdefault("system", nid)
                    builder.add_edge(tid, nid, "CONNECTS_TO", f["file"], f["line"], conf, op="table",
                                     via=f"{f['library']} datasource {f['datasource']}")
                    st["tables"] = st.get("tables", 0) + 1
            if u is not None and u[0] == "env" and attrs.get("credential_in_url"):
                builder.nodes[nid].attrs.setdefault("credential_source", "env")
                builder.nodes[nid].attrs.setdefault("credential_at", f"env:{u[1]}")
                if f"env:{u[1]}" in builder.nodes:
                    builder.add_edge(nid, f"env:{u[1]}", "CREDENTIAL_FROM", None, None, "resolved", secret_kind="url password")
        elif f.get("client"):        # a client constructor in code (#77)
            src = f["src"]
            src_n = builder.nodes.get(src)
            # same rule as #42 HTTP: test-only callers do not attach systems (attach runs before tests_index)
            if str(src).startswith("test:") or (src_n is not None and (
                    src_n.kind == "test" or (src_n.attrs or {}).get("test"))):
                continue
            nid = node(proto, target, {**attrs, "client": f["var"], "library": f["client"], "resource": f.get("resource")}, conf)
            builder.add_edge(src, nid, "CONNECTS_TO", f["file"], f["line"], conf, op="connect", via=f"client {f['var']}")
        else:
            nid = node(proto, target, {**attrs, "setting": f["var"], "resource": f.get("resource")}, conf)
            builder.add_edge(f["src"], nid, "CONNECTS_TO", f["file"], f["line"], conf, op="configure", via=f"setting {f['var']}")
        st["connects"] += 1
        for kk in ([u[1]] if u is not None and u[0] == "env" else []) + ([f["host"][1]] if f.get("host") and f["host"][0] == "env" else []):
            if f"env:{kk}" in builder.nodes:
                builder.add_edge(nid, f"env:{kk}", "CONFIGURED_BY", None, None, conf)
            if not f.get("datasource"):      # the code reading a datasource's key still connects through it
                used_keys.add(kk)
        pw = f.get("password")
        if pw and pw[0] == "env":
            cred(nid, [pw[1]])
            builder.nodes[nid].attrs.setdefault("credential_source", "env")
            builder.nodes[nid].attrs.setdefault("credential_at", f"env:{pw[1]}")
            used_keys.add(pw[1])
        elif pw:
            builder.nodes[nid].attrs.update(credential_source="literal", credential_at=pw[1])

    # ---- model calls (#66 attrs.llm_calls) -> external:llm:<provider>, the API-key env var as the credential (#77)
    for n in llm:
        for c in n.attrs["llm_calls"]:
            prov = c.get("provider")
            if not prov:
                continue
            base = parse_dsn(c["base_url"]) if c.get("base_url") else None
            host = base["host"] if base else None
            if host and host.lower() not in LOOPBACK:
                target, a = target_of(host.lower(), base["port"]), {"address_source": "literal", "tls": base.get("tls")}
            else:
                target, a = prov, {"address_source": "provider", **({"address_default": target_of(host, base["port"])} if host else {})}
            nid = node("llm", target, {**a, "provider": prov, "address_at": f"{n.file}:{c.get('line')}"}, "exact" if host else "resolved")
            models = builder.nodes[nid].attrs.setdefault("models", [])
            if c.get("model") and c["model"] not in models:
                models.append(c["model"])
            builder.add_edge(n.id, nid, "CONNECTS_TO", n.file, c.get("line"), "resolved", op=c.get("op") or "model call",
                             via=f"model {c['model']}" if c.get("model") else f"{prov} client")
            st["connects"] += 1
            cred(nid, [k for k in LLM_KEYS.get(prov, ()) if k in envs])
            used_keys.update(k for k in LLM_KEYS.get(prov, ()) if k in envs)

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

    # ---- Laravel Eloquent / migration tables -> the connection's external (#41 step 3)
    conn_ext = {e.src: e.dst for e in builder.edges.values()
                if e.kind == "CONNECTS_TO" and e.src.startswith("connection:") and e.dst.startswith("external:")}
    default_ext = conn_ext.get(f"connection:{default}") if default else None
    maps = [(e.src, e.dst) for e in builder.edges.values() if e.kind == "MAPS_TO_TABLE"]
    uses = {e.src: e.dst for e in builder.edges.values()
            if e.kind == "USES_CONNECTION" and e.src.startswith("class:") and e.dst.startswith("connection:")}
    model_ext = {tid: conn_ext[cid] for cls, tid in maps
                 if (cid := uses.get(cls)) and cid in conn_ext}
    php_tables = {tid for cls, tid in maps
                  if (src := builder.nodes.get(cls)) and (src.lang == "php" or (src.file or "").endswith(".php"))}
    for n in list(builder.nodes.values()):
        if n.kind != "table" or (n.attrs or {}).get("system"):
            continue
        if n.id not in model_ext and n.id not in php_tables and not (n.module or "").startswith("database/migrations"):
            continue
        nid = model_ext.get(n.id) or default_ext
        if not nid:
            continue
        n.attrs.setdefault("system", nid)
        n.attrs.setdefault("orm", (n.attrs or {}).get("orm") or "eloquent")
        builder.add_edge(n.id, nid, "CONNECTS_TO", n.file, n.line,
                         (builder.nodes[nid].attrs or {}).get("confidence") or "resolved",
                         op="table", via="laravel connection")
        st["tables"] = st.get("tables", 0) + 1

    # ---- ORM tables without a datasource block: attach to the sole SQL system of the project (#41 step 2)
    # Immich (Kysely + DB_URL), Nest TypeORM (@Entity + DATABASE_URL) when no DataSource fact named them.
    SQL = {"postgres", "mysql", "mssql", "oracle", "mongodb", "sqlite"}
    ORM = {"Entity", "ViewEntity", "Table", "kysely", "drizzle", "knex", "prisma", "typeorm"}
    by_proto: dict = {}
    for n in builder.nodes.values():
        if n.kind != "external":
            continue
        pr = (n.attrs or {}).get("protocol")
        if pr in SQL:
            by_proto.setdefault(pr, []).append(n.id)
    # Only tables an ORM declared (not bare Laravel migration tables / raw SQL names)
    pending = [n for n in builder.nodes.values()
               if n.kind == "table" and not (n.attrs or {}).get("system")
               and (n.attrs or {}).get("orm") in ORM]
    if pending and sum(1 for ids in by_proto.values() if len(ids) == 1) >= 1:
        # one SQL system total across protocols, or one per protocol with all tables going to the only SQL proto
        sole = [ids[0] for ids in by_proto.values() if len(ids) == 1]
        if len(sole) == 1 or (len(by_proto) == 1 and len(sole) == 1):
            nid = sole[0]
            # If multiple sole protocols (postgres + redis), only attach to the SQL one (redis not in SQL... redis not in SQL set)
            # sole may be [postgres] only since redis is not in SQL. Good.
            if len([i for i in sole if builder.nodes[i].attrs.get("protocol") in SQL]) == 1:
                nid = next(i for i in sole if builder.nodes[i].attrs.get("protocol") in SQL)
                for n in pending:
                    n.attrs.setdefault("system", nid)
                    builder.add_edge(n.id, nid, "CONNECTS_TO", n.file, n.line, "heuristic",
                                     op="table", via=f"sole {(builder.nodes[nid].attrs or {}).get('protocol')} system")
                    st["tables"] = st.get("tables", 0) + 1
                    st["sole_attach"] = st.get("sole_attach", 0) + 1

    attach_redis_es_resources(builder, root, st)
    _attach_http_hosts(builder, node, st)
    from .sdk_systems import attach_sdk
    attach_sdk(builder, root, node, cred, st)

    # Brokers declared only in compose: a node with no CONNECTS_TO (#158). Skip a service a client fact
    # already turned into the same protocol + host / deployment name.
    seen_compose: set = set()
    for svc, c in compose.items():
        if id(c) in seen_compose:
            continue
        seen_compose.add(id(c))
        proto = c.get("protocol")
        if proto not in COMPOSE_ONLY:
            continue
        sl = str(svc).lower()
        if any((a := (n.attrs or {})).get("protocol") == proto and (
                str(a.get("host") or "").lower() == sl or str(a.get("deployment_name") or "").lower() == sl
                or str(a.get("target") or "") == sl or str(a.get("target") or "").startswith(sl + ":"))
               for n in builder.nodes.values() if n.kind == "external"):
            continue
        port = c.get("port") or PORTS.get(proto)
        node(proto, target_of(sl, port), {"source": "compose", "address_source": "compose",
                         "address_at": c.get("file"), "deployment_name": sl, "image": c.get("image"),
                         "compose_file": c.get("file"), "host": sl, "port": port}, "resolved")

    return {k: v for k, v in st.items() if v} if st["systems"] else {}


def _worst_conf(confs) -> str:
    """The weakest label among the client calls a host node is built from (a const-map host stays heuristic)."""
    from .core.model import CONFIDENCE_RANK
    cs = [c for c in confs if c in CONFIDENCE_RANK]
    return min(cs, key=lambda c: CONFIDENCE_RANK[c]) if cs else "exact"


def _attach_http_hosts(builder, node, st) -> None:
    """Group `http` nodes with `origin_kind` other into `external:http(s):<host>:<port>`.

    Scheme selects the protocol and default port (`http` → 80, `https` → 443; an explicit port wins).
    CONNECTS_TO runs from the calling function to the host (`via` http, `op` request). Several call sites
    from one function are one edge with `count`. Hosts `parse_dsn` rejects (`{...}`, `${...}`) stay
    unattached. Loopback (`localhost`, `127.0.0.1`, `::1`, ...) is skipped. Calls only from tests are left out.
    These nodes are not routes: `match_endpoint` already ignores `origin_kind` other, and nothing here
    writes MATCHES_ROUTE.
    """
    incoming: dict = {}
    for e in builder.edges.values():
        incoming.setdefault(e.dst, []).append(e)
    groups: dict = {}
    for n in builder.nodes.values():
        if n.kind != "http":
            continue
        a = n.attrs or {}
        if a.get("origin_kind") != "other" or a.get("test_only"):
            continue
        origin = a.get("origin") or ""
        if "://" not in origin:
            continue
        d = parse_dsn(origin)
        if not d or d["protocol"] not in ("http", "https") or (d.get("host") or "").lower() in LOOPBACK:
            continue
        # Skip TEST_* edges and edges from test nodes (tests_index remaps later; attach runs first).
        calls = []
        for e in incoming.get(n.id, ()):
            if str(e.kind).startswith("TEST_"):
                continue
            src_n = builder.nodes.get(e.src)
            if e.src.startswith("test:") or (src_n is not None and (
                    src_n.kind == "test" or (src_n.attrs or {}).get("test"))):
                continue
            calls.append(e)
        if not calls:
            continue
        proto = d["protocol"]
        key = target_of(d["host"], d["port"])
        g = groups.setdefault((proto, key), {"tls": d["tls"], "scheme": d["scheme"], "paths": set(), "by_src": {}, "confs": []})
        g["confs"].extend(e.confidence for e in calls)
        if a.get("path") and a["path"] not in ("", "/"):
            g["paths"].add(a["path"])
        for e in calls:
            g["by_src"].setdefault(e.src, []).append(e)
    for (proto, key), g in groups.items():
        nid = node(proto, key, {
            "scheme": g["scheme"], "tls": g["tls"], "address_source": "literal",
            "paths": sorted(g["paths"]) or None,
        }, _worst_conf(g["confs"]))
        for src, es in g["by_src"].items():
            es.sort(key=lambda e: ((e.file or ""), e.line or 0))
            e0 = es[0]
            extra = {"via": "http", "op": "request"}
            if len(es) > 1:
                extra["count"] = len(es)
            builder.add_edge(src, nid, "CONNECTS_TO", e0.file, e0.line, _worst_conf([e.confidence for e in es]), **extra)
            st["connects"] += 1


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
        users = []
        ids = []
        for x in st.q("SELECT src, attrs, file, line, confidence FROM edges WHERE dst=? AND kind IN ('CONNECTS_TO','TEST_USES')", (s["id"],)):
            ua = json.loads(x["attrs"] or "{}")
            users.append({"id": x["src"], "op": ua.get("op"), "via": ua.get("via"), "count": ua.get("count"),
                          "at": f"{x['file']}:{x['line']}" if x["file"] else None, "confidence": x["confidence"]})
            ids.append(x["src"])
        tables = sorted(u["id"] for u in users if u["id"].startswith("table:"))
        users = [u for u in users if not u["id"].startswith("table:")]
        callers = set(ids) - set(tables)
        for x in list(ids):
            if x.startswith("connection:"):
                callers |= {y["src"] for y in st.q("SELECT src FROM edges WHERE dst=? AND kind='USES_CONNECTION'", (x,))}
            elif x.startswith("table:"):       # the tables of an ORM datasource (#41): their readers / writers use the system
                callers |= {y["src"] for y in st.q("SELECT src FROM edges WHERE dst=? AND kind IN "
                                                   "('READS_TABLE','WRITES_TABLE','MAPS_TO_TABLE')", (x,))}
        entries = {}
        for c in callers:
            for e in st.q("SELECT entry_kind, entry_count FROM node_entry WHERE node_id=?", (c,)):
                entries[e["entry_kind"]] = entries.get(e["entry_kind"], 0) + e["entry_count"]
        conf = [(x["dst"], x["kind"]) for x in st.q("SELECT dst, kind FROM edges WHERE src=? AND kind IN ('CONFIGURED_BY','CREDENTIAL_FROM')", (s["id"],))]
        out.append({**s, "users": users, "tables": tables, "callers": len(callers), "entry_kinds": entries,
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
        elif a.get("auth"):
            bits.append(f"auth {a['auth']}")
        if a.get("tls") is not None:
            bits.append("tls" if a["tls"] else "plaintext")
        if a.get("resource"):
            bits.append(f"resource {a['resource']}")
        if a.get("key_prefixes"):
            bits.append("key prefixes " + ", ".join(a["key_prefixes"][:6])
                        + (" ..." if len(a["key_prefixes"]) > 6 else ""))
        if a.get("indices"):
            bits.append("indices " + ", ".join(a["indices"][:6])
                        + (" ..." if len(a["indices"]) > 6 else ""))
        if a.get("paths"):
            bits.append("paths " + ", ".join(a["paths"][:6]) + (" ..." if len(a["paths"]) > 6 else ""))
        L.append(f"{s['id']}  [{a.get('confidence') or 'exact'}]")
        L.append("    " + "; ".join(bits))
        if s["entry_kinds"]:
            L.append("    reached from " + ", ".join(f"{k}({v})" for k, v in sorted(s["entry_kinds"].items())))
        for u in s["users"][:8]:
            L.append(f"    <- {u['id']}" + (f" @ {u['at']}" if u.get("at") else "") + (f"  ({u['via']})" if u.get("via") else "")
                     + (f" x{u['count']}" if u.get("count") else ""))
        if len(s["users"]) > 8:
            L.append(f"    ... {len(s['users']) - 8} more users")
        if s.get("tables"):
            L.append(f"    {len(s['tables'])} tables ({a.get('library') or 'orm'} datasource {a.get('datasource') or ''}".rstrip()
                     + "): " + ", ".join(t[len("table:"):] for t in s["tables"][:8]) + (" ..." if len(s["tables"]) > 8 else "")
                     + (f"; used by {s['callers']} functions" if s["callers"] else ""))
        if s["configured_by"]:
            L.append("    configured by " + ", ".join(s["configured_by"][:6]))
    if res["selected"] > max_items:
        L.append(f"... {res['selected'] - max_items} more")
    return "\n".join(L)


# ------------------------------------------------------------------ Redis key prefixes / Elasticsearch indices (#41 step 4)
_REDIS_CALL = re.compile(
    r"""(?:redis|cache|client|r|conn|store)\s*\.\s*(?:async_)?(?:get|set|setex|setnx|add|delete|del|exists|expire|hget|hset|hdel|hgetall|incr|decr|lpush|rpush|sadd|zadd|keys|mget|mset|getdel|unlink|get_or_set|set_many|delete_many|has_key|touch|fetch|getex)\s*\(\s*""",
    re.I,
)
_ES_INDEX_KW = re.compile(
    r"""\.(?:index|search|delete_by_query|update_by_query|indices\.create|indices\.delete|indices\.exists)\s*\(\s*""",
    re.I,
)
_FSTR_PREFIX = re.compile(r"""^[fF]?["']([^"'{}$]*:)(?:\{|\$\{)""")  # f"cache:user:{id}" / f'a:{x}'
_TPL_PREFIX = re.compile(r"""^`([^`${]*:)(?:\$\{)""")                 # `website:${id}`
_LIT_KEY = re.compile(r"""^["']([\w.:\-]+/?)["']""")                  # "cache:user:1" or 'posts'
# Word-boundary so API_KEY_PREFIX / DISPLAY_KEY_PREFIX do not match KEY_PREFIX.
_KEY_PREFIX_ASSIGN = re.compile(
    r"""["']?\b(?:KEY_PREFIX|key_prefix|keyPrefix|INDEX_PREFIX|index_prefix|indexPrefix)\b["']?\s*[=:]\s*["']([^"']+)["']""",
)
_ENV_DEFAULT_PREFIX = re.compile(
    r"""(?:getenv|env|os\.environ\.get)\s*\(\s*["'](\w*(?:INDEX_PREFIX|KEY_PREFIX|PREFIX)\w*)["']\s*,\s*["']([^"']+)["']""",
    re.I,
)
# Helper-returned / assigned key templates (saleor: `return f"login:fail:ip:{ip}"`).
_KEY_EXPR_BIND = re.compile(
    r"""(?:return\s+|=\s*)([fF]["'][^"']*["']|`[^`]*`)""",
)


def _prefix_of(expr: str) -> str | None:
    """Static prefix of a redis/cache key expression (`cache:user:{id}` -> `cache:user:`)."""
    e = (expr or "").strip()
    for rx in (_FSTR_PREFIX, _TPL_PREFIX):
        m = rx.match(e)
        if m and len(m.group(1)) >= 2:
            return m.group(1)
    m = _LIT_KEY.match(e)
    if m and ":" in m.group(1):
        # literal key: keep through the last colon (namespace), drop the final segment if it looks like an id
        parts = m.group(1).rsplit(":", 1)
        if len(parts) == 2 and parts[0]:
            return parts[0] + ":"
    return None


def _arg0(src: str, call_end: int) -> str | None:
    """First argument text of a call whose '(' ends at call_end-1... actually call_end is past '('."""
    i = call_end
    depth, in_str, q, beg = 0, False, "", i
    while i < len(src) and i < call_end + 400:
        c = src[i]
        if in_str:
            if c == "\\" and i + 1 < len(src):
                i += 2
                continue
            if c == q:
                in_str = False
            i += 1
            continue
        if c in "'\"`":
            in_str, q = True, c
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0 and c == ")":
                return src[beg:i].strip()
            depth -= 1
        elif c == "," and depth == 0:
            return src[beg:i].strip()
        i += 1
    return None


def _ok_redis_prefix(p: str) -> bool:
    if not p or len(p) < 2 or len(p) > 80 or " " in p or "\n" in p:
        return False
    body = p.rstrip(":")
    if not body or body.isdigit() or body in ("True", "False", "None", "null", "undefined"):
        return False
    if re.match(r"(?i)(https?|mailto|xmlns|mcp|ftp|file|data|sha256|x-api-key):", p):
        return False
    if not re.search(r"[A-Za-z]", body):
        return False
    # Prefer namespace-shaped tokens (colon or trailing underscore, or Django KEY_PREFIX bare word)
    if ":" in p:
        if not re.match(r"^[A-Za-z_][\w.\-/]*:$", p) and not re.match(r"^[A-Za-z_][\w.\-/]*(?:[\w.\-/]*:)+$", p):
            # multi-segment prefixes like login:fail:ip:
            if not re.match(r"^[A-Za-z_][\w.\-/]*(?:\:[\w.\-/]+)*:$", p):
                return False
    elif not re.match(r"^[A-Za-z_][\w.\-]*_?$", p):
        return False
    return True


def _ok_es_index(name: str) -> bool:
    if not name or len(name) < 2 or len(name) > 120 or " " in name:
        return False
    if name.isdigit() or name.upper() in ("HNSW", "COSINE", "EUCLIDEAN", "DOT", "IP", "L2", "TRUE", "FALSE"):
        return False
    if not re.fullmatch(r"[A-Za-z][\w.\-]*", name):
        return False
    # Bare words like "data"/"docs" are usually unrelated kwargs; prefer namespaced indices.
    if "_" not in name and "-" not in name and "." not in name and len(name) < 8:
        return False
    return True


def scan_redis_es_resources(root: Path) -> dict:
    """Literal redis key prefixes and Elasticsearch index names in the project (#41 step 4)."""
    prefixes, indices = set(), set()
    needles = ("redis", "Redis", "ioredis", "cache.", "cache[", "cache(", "KEY_PREFIX", "keyPrefix",
               "elasticsearch", "Elasticsearch", "OpenSearch", "opensearch", "INDEX_PREFIX", "index_prefix")
    files = []
    for pat in ("**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.php"):
        for pth in root.glob(pat):
            if not _SKIP.isdisjoint(pth.parts):
                continue
            # skip noisy generated / test-only OpenAPI fixtures for prefix assigns
            name = pth.name
            if name.endswith((".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.tsx", ".spec.js")):
                continue
            if "/generated/" in str(pth).replace("\\", "/") or pth.parts[-2:] == ("generated", name):
                continue
            try:
                txt = pth.read_text(errors="ignore")
            except Exception:
                continue
            if any(k in txt for k in needles):
                files.append((pth, txt))
            if len(files) >= 400:
                break
        if len(files) >= 400:
            break

    redisish = ("redis", "Redis", "cache.", "cache[", "ioredis", "BullModule", "bullmq", "django.core.cache")
    for f, txt in files:
        is_redis_file = any(k in txt for k in redisish)
        for m in _KEY_PREFIX_ASSIGN.finditer(txt):
            v = m.group(1)
            if "index" in m.group(0).lower():
                if _ok_es_index(v):
                    indices.add(v)
            elif _ok_redis_prefix(v):
                prefixes.add(v)
        for m in _ENV_DEFAULT_PREFIX.finditer(txt):
            key, default = m.group(1), m.group(2)
            if "INDEX" in key.upper():
                if _ok_es_index(default):
                    indices.add(default)
            elif _ok_redis_prefix(default):
                prefixes.add(default)
        if is_redis_file:
            for m in _REDIS_CALL.finditer(txt):
                a0 = _arg0(txt, m.end())
                if not a0:
                    continue
                if re.match(r"^\w+\s*=", a0) and not a0.lstrip().startswith(("key", "name", "path")):
                    km = re.search(r"""(?:key|name|path)\s*=\s*(.+)""", a0)
                    a0 = km.group(1).strip() if km else a0
                pfx = _prefix_of(a0)
                if pfx and _ok_redis_prefix(pfx):
                    prefixes.add(pfx)
            # Helper-returned / assigned key templates near redis/cache usage
            for m in _KEY_EXPR_BIND.finditer(txt):
                pfx = _prefix_of(m.group(1))
                if pfx and _ok_redis_prefix(pfx):
                    prefixes.add(pfx)
        for m in re.finditer(r"""\bindex\s*=\s*([fF]?["'`][^"'`]*["'`]|f["'][^"']*\{)""", txt):
            expr = m.group(1)
            pfx = _prefix_of(expr) if ("{" in expr or "$" in expr) else None
            lit = re.match(r"""[fF]?["']([^"'{}$]+)["']""", expr.rstrip("{"))
            if pfx and _ok_es_index(pfx.rstrip(":")):
                indices.add(pfx.rstrip(":"))
            elif lit and _ok_es_index(lit.group(1)):
                indices.add(lit.group(1))
        for m in _ES_INDEX_KW.finditer(txt):
            a0 = _arg0(txt, m.end())
            if not a0 or a0.lstrip().startswith("index"):
                continue
            lit = re.match(r"""[fF]?["']([^"'{}$]+)["']""", a0)
            if lit and _ok_es_index(lit.group(1)):
                indices.add(lit.group(1))
    return {"key_prefixes": sorted(prefixes), "indices": sorted(indices)}



def attach_redis_es_resources(builder, root: Path, st: dict) -> None:
    """Attach scanned redis key prefixes / ES index names onto the matching external nodes."""
    found = scan_redis_es_resources(root)
    if not found["key_prefixes"] and not found["indices"]:
        return
    redis_nodes = [n for n in builder.nodes.values()
                   if n.kind == "external" and (n.attrs or {}).get("protocol") == "redis"]
    es_nodes = [n for n in builder.nodes.values()
                if n.kind == "external" and (n.attrs or {}).get("protocol") == "elasticsearch"]
    if found["key_prefixes"] and redis_nodes:
        # prefer a single redis node; otherwise attach to all (same prefixes used cluster-wide)
        for n in redis_nodes:
            cur = list(n.attrs.get("key_prefixes") or [])
            for p in found["key_prefixes"]:
                if p not in cur:
                    cur.append(p)
            n.attrs["key_prefixes"] = cur
            # resource: the first literal prefix when nothing else is set (DSN db number stays)
            if not n.attrs.get("resource") and len(cur) == 1:
                n.attrs["resource"] = cur[0]
        st["redis_prefixes"] = len(found["key_prefixes"])
    if found["indices"] and es_nodes:
        for n in es_nodes:
            cur = list(n.attrs.get("indices") or [])
            for i in found["indices"]:
                if i not in cur:
                    cur.append(i)
            n.attrs["indices"] = cur
            if not n.attrs.get("resource") and len(cur) == 1:
                n.attrs["resource"] = cur[0]
        st["es_indices"] = len(found["indices"])

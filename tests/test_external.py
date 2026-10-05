"""External systems (#40): DSN parsing, .env.example / docker-compose, Laravel connections, env-key groups, Python
settings dicts, one node per system across linked repos, secrets never stored; `cg external` / MCP."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.external import _split, parse_dsn, read_compose, read_env_example  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "external_fixture"
SECRETS = (b"s3cr3t-fixture-pw", b"r3dis-fixture-pw", b"sm7p-fixture-pw", b"pg-fixture-literal")


def cli(*a):
    return subprocess.run([sys.executable, "-m", "codegraph.cli", *a], cwd=ROOT, capture_output=True, text=True)


def ext(st):
    return {r["id"]: json.loads(r["attrs"]) for r in st.q("SELECT id, attrs FROM nodes WHERE kind='external'")}


def edges(st, kind):
    return {(r["src"], r["dst"]) for r in st.q("SELECT src, dst FROM edges WHERE kind=?", (kind,))}


@pytest.mark.parametrize("url,proto,host,port,res,tls", [
    ("postgres://u:pw@db.example:6543/app", "postgres", "db.example", 6543, "app", None),
    ("postgresql://db/app?sslmode=require", "postgres", "db", 5432, "app", True),
    ("mysql://root@mysql/shop", "mysql", "mysql", 3306, "shop", None),
    ("mongodb+srv://u:p@cluster0.example.net/orders", "mongodb", "cluster0.example.net", None, "orders", True),
    ("rediss://:pw@cache.example:6380/2", "redis", "cache.example", 6380, "2", True),
    ("redis://redis", "redis", "redis", 6379, None, False),
    ("amqps://mq.example/vhost", "amqp", "mq.example", 5671, "vhost", True),
    ("smtps://mail.example", "smtp", "mail.example", 465, None, True),
    ("ldaps://dir.example", "ldap", "dir.example", 636, None, True),
    ("sftp://files.example/in", "ssh", "files.example", 22, "in", True),
    ("s3://my-bucket/prefix", "s3", "my-bucket", None, "prefix", True),
    ("jdbc:postgresql://pg.example:5433/core", "postgres", "pg.example", 5433, "core", None),
])
def test_parse_dsn(url, proto, host, port, res, tls):
    d = parse_dsn(url)
    assert d["protocol"] == proto and d["host"] == host
    if port is not None:
        assert d["port"] == port
    assert (d.get("resource") or None) == res
    if tls is not None:
        assert d["tls"] is tls
    assert "pw" not in json.dumps(d) and "password" not in d


def test_parse_dsn_rejects_templates_and_garbage():
    for v in ("", "not a url", "postgres://${DB_HOST}/x", "https://", "redis://:%s@/0", "file:///tmp/x", "unknown://h/x"):
        assert parse_dsn(v) is None, v
    assert parse_dsn("postgres://u:pw@h/db")["has_password"] is True
    assert parse_dsn("postgres://u@h/db")["has_password"] is False


def test_split_env_and_compose():
    assert _split("DB_HOST")[3] == "HOST" and _split("REDIS_PASSWORD")[1:] == ("redis", "REDIS", "PASSWORD")
    assert _split("REDIS_CACHE_HOST")[2:] == ("REDIS_CACHE", "HOST")
    assert _split("SMTP_SECRET_KEY")[3] == "SECRET_KEY"
    assert _split("APP_NAME") is None
    ex = read_env_example(FX / "shop-worker")
    assert ex["SMTP_HOST"][0] == "mail.example.net"
    assert ex["SMTP_PASSWORD"][0] is None                      # secrets: location only
    assert ex["DATABASE_URL"][0] is not None and ex["DATABASE_URL"][0] == "postgres://shop:***@db:5432/shop"
    comp = read_compose(FX / "shop-api")
    assert comp["db"]["image"].startswith("postgres") and comp["redis"]["port"] == 6379


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("ext")
    out = {}
    for n in ("shop-api", "shop-worker", "shop-py"):
        out[n] = d / f"{n}.db"
        out[n + ":stats"] = index_project(FX / n, out[n], n)
    out["link"] = d / "link.db"
    r = cli("link", "--backend", str(out["shop-api"]), "--frontend", str(out["shop-worker"]), "--db", str(out["link"]),
            "--backend-name", "shop-api", "--frontend-name", "shop-worker")
    assert r.returncode == 0, r.stderr
    return out


def test_laravel_connections(dbs):
    st = GraphStore(dbs["shop-api"])
    e = ext(st)
    assert "external:mysql:reports.internal.example:3306" in e                      # literal host in a custom connection
    assert e["external:mysql:reports.internal.example:3306"]["confidence"] == "exact"
    pg = e["external:postgres:db:5432"]                                               # default connection, compose service
    assert pg["address_source"] == "compose" and pg["image"].startswith("postgres")
    assert "external:redis:redis:6379" in e
    assert not any(k.startswith("external:mysql:") and "reports" not in k for k in e)    # unused template mysql skipped
    ct = edges(st, "CONNECTS_TO")
    assert ("connection:pgsql", "external:postgres:db:5432") in ct
    assert ("connection:reporting", "external:mysql:reports.internal.example:3306") in ct
    assert ("external:postgres:db:5432", "env:DB_PASSWORD") in edges(st, "CREDENTIAL_FROM")
    assert dbs["shop-api:stats"]["external"]["systems"] >= 3


def test_env_groups_and_tls(dbs):
    st = GraphStore(dbs["shop-worker"])
    e = ext(st)
    assert e["external:redis:redis:6379"]["tls"] is False
    assert e["external:smtp:mail.example.net:465"]["tls"] is True
    ct = edges(st, "CONNECTS_TO")
    assert any(d == "external:postgres:db:5432" and "pool" in s for s, d in ct)
    assert any(d == "external:smtp:mail.example.net:465" for s, d in ct)
    assert ("external:smtp:mail.example.net:465", "env:SMTP_PASSWORD") in edges(st, "CREDENTIAL_FROM")


def test_python_settings(dbs):
    st = GraphStore(dbs["shop-py"])
    e = ext(st)
    pg = e["external:postgres:db:5432"]
    assert pg["address_source"] == "compose" and pg["resource"] == "shop" and pg["credential_at"] == "env:POSTGRES_PASSWORD"
    assert e["external:redis:cache:6379"]["setting"] == "CACHES[default]"
    assert e["external:amqp:queue.internal.example:5672"]["confidence"] == "exact"
    assert "external:smtp:smtp.example.org:587" in e
    assert "external:redis:redis-cache.internal.example:6380" in e
    db = e["external:postgres:config:netboxish.configuration.DATABASE"]   # loopback: per-config node; protocol from compose
    assert db["address_default"] == "localhost:5432" and db["credential_source"] == "literal"
    assert not any("sqlite" in k or "local" in k.split(":")[-1] for k in e)          # sqlite alias: no network system
    assert ("module:shop.settings", "external:postgres:db:5432") in edges(st, "CONNECTS_TO")


def test_link_one_node_per_system_and_reach(dbs):
    st = GraphStore(dbs["link"])
    pg = [r for r in st.q("SELECT id FROM nodes WHERE id='external:postgres:db:5432'")]
    assert len(pg) == 1
    srcs = {r["src"] for r in st.q("SELECT src FROM edges WHERE kind='CONNECTS_TO' AND dst='external:postgres:db:5432'")}
    assert any(s.startswith("connection:") for s in srcs) and any("pool" in s for s in srcs)
    r = cli("reaches", "external:postgres:db:5432", "--db", str(dbs["link"]))
    assert r.returncode == 0 and "OrderController" in r.stdout and "/sync" in r.stdout, r.stdout + r.stderr


def test_impact_on_external_and_table(dbs):
    """#77: impact walks CONNECTS_TO / USES_CONNECTION / table edges into the code that uses the system."""
    from codegraph.query import impact
    st = GraphStore(dbs["link"])
    res = impact(st, "external:postgres:db:5432")
    callers = {c["name"]: c["depth"] for c in res["callers"]}
    assert callers.get("pool") == 1 and "App\\Http\\Controllers\\OrderController::index" in {c["fqn"] for c in res["callers"]}
    assert {e["name"] for e in res["entry_points"]} >= {"GET /orders", "POST /sync"}
    t = impact(st, "table:orders")
    assert [c["fqn"] for c in t["callers"]] == ["App\\Http\\Controllers\\OrderController::index"]
    r = cli("impact", "external:postgres:db:5432", "--db", str(dbs["link"]))
    assert r.returncode == 0 and "no recorded callers" not in r.stdout and "POST /sync" in r.stdout


def test_no_secret_bytes(dbs):
    for k in ("shop-api", "shop-worker", "shop-py", "link"):
        raw = Path(dbs[k]).read_bytes()
        for s in SECRETS:
            assert s not in raw, (k, s)


def test_cli_and_mcp(dbs, monkeypatch):
    r = cli("external", "--db", str(dbs["shop-worker"]), "--json")
    assert r.returncode == 0, r.stderr
    js = json.loads(r.stdout)
    assert {s["id"] for s in js["systems"]} >= {"external:redis:redis:6379", "external:smtp:mail.example.net:465"}
    r = cli("external", "--db", str(dbs["shop-worker"]), "--tls-off")
    assert "external:redis:redis:6379" in r.stdout and "mail.example.net" not in r.stdout
    r = cli("external", "--db", str(dbs["shop-api"]), "--protocol", "mysql")
    assert "reports.internal.example" in r.stdout and "postgres" not in r.stdout.split("\n", 1)[1]
    from codegraph import mcp_server
    monkeypatch.setattr(mcp_server, "_st", lambda: GraphStore(dbs["shop-py"]))
    out = str(mcp_server.external_systems(protocol="amqp"))
    assert "queue.internal.example" in out and "smtp.example.org" not in out


def test_plain_project_has_no_externals(tmp_path):
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    db = tmp_path / "p.db"
    stats = index_project(tmp_path, db, "plain")
    assert not ext(GraphStore(db)) and not stats.get("external")


CLIENTS_PY = """import os
import smtplib
import psycopg2
import redis
from redis import Redis
from pymongo import MongoClient
import ldap3
import boto3


def reports():
    return psycopg2.connect(host="reports.internal.example", port=5433, dbname="reports", password=os.environ["REPORTS_PW"])


def cache():
    return Redis(host=os.getenv("CACHE_HOST", "localhost"), port=6380)


def queue_cache():
    return redis.from_url("rediss://queue-cache.internal.example:6390/0")


def local():
    return redis.Redis()             # localhost default: nothing


def loop():
    return redis.Redis(host="127.0.0.1")


def loop_url():
    return psycopg2.connect("postgresql://app@localhost:54320/app")


def mail(msg):
    with smtplib.SMTP_SSL("smtp.mail.example.com") as s:
        s.send_message(msg)


def events():
    return MongoClient("mongodb://events.internal.example:27018/events")


def directory():
    return ldap3.Server("ldap.corp.example", port=636)


def bucket():
    return boto3.client("s3", endpoint_url="https://minio.internal.example:9000")


def plain_s3():
    return boto3.client("s3")
"""


def test_python_client_constructors(tmp_path):
    """#77: client constructors with an address in their arguments are CONNECTS_TO from the calling function."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "clients.py").write_text(CLIENTS_PY)
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "clients")
    st = GraphStore(db)
    e = ext(st)
    pg = e["external:postgres:reports.internal.example:5433"]
    assert pg["confidence"] == "exact" and pg["resource"] == "reports" and pg["credential_at"] == "env:REPORTS_PW"
    assert e["external:redis:queue-cache.internal.example:6390"]["tls"] is True
    assert e["external:smtp:smtp.mail.example.com:465"]["tls"] is True
    assert "external:mongodb:events.internal.example:27018" in e and "external:ldap:ldap.corp.example:636" in e
    assert "external:s3:minio.internal.example:9000" in e
    assert e["external:redis:env:CACHE_HOST"]["address_source"] == "env"
    assert not any("127.0.0.1" in k or "localhost" in k for k in e)              # local defaults: no system
    ct = edges(st, "CONNECTS_TO")
    assert ("function:app.clients.reports", "external:postgres:reports.internal.example:5433") in ct
    assert ("function:app.clients.mail", "external:smtp:smtp.mail.example.com:465") in ct
    assert not any(s in ("function:app.clients.local", "function:app.clients.loop", "function:app.clients.plain_s3") for s, _ in ct)
    assert not any(s == "function:app.clients.loop_url" for s, _ in ct)
    assert ("external:postgres:reports.internal.example:5433", "env:REPORTS_PW") in edges(st, "CREDENTIAL_FROM")


def test_llm_providers_as_externals(tmp_path):
    """#77: model calls (#66 attrs.llm_calls) are CONNECTS_TO external:llm:<provider>; a base_url names the host."""
    (tmp_path / "bot.py").write_text(
        "import os\nfrom openai import OpenAI\nfrom anthropic import Anthropic\n\n"
        "KEY = os.environ['OPENAI_API_KEY']\n\n\n"
        "def ask(q):\n    c = OpenAI()\n    return c.chat.completions.create(model='gpt-4.1', messages=[q])\n\n\n"
        "def ask_claude(q):\n    return Anthropic().messages.create(model='claude-x', max_tokens=10, messages=[q])\n\n\n"
        "def ask_gateway(q):\n    return OpenAI(base_url='https://llm-gateway.internal.example/v1', model='m')\n")
    (tmp_path / "test_bot.py").write_text(
        "from openai import OpenAI\n\n\ndef test_fake():\n    OpenAI(base_url='https://fake.example.test/v1', model='t')\n")
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "bot")
    st = GraphStore(db)
    e = ext(st)
    oa = e["external:llm:openai"]
    assert oa["provider"] == "openai" and oa["models"] == ["gpt-4.1"] and oa["credential_at"] == "env:OPENAI_API_KEY"
    assert "claude-x" in e["external:llm:anthropic"]["models"]
    assert e["external:llm:llm-gateway.internal.example:443"]["address_source"] == "literal"
    ct = edges(st, "CONNECTS_TO")
    assert ("function:bot.ask", "external:llm:openai") in ct and ("function:bot.ask_claude", "external:llm:anthropic") in ct
    assert ("function:bot.ask_gateway", "external:llm:llm-gateway.internal.example:443") in ct
    assert not any("fake.example.test" in k for k in e)                              # model calls from tests: none


CLIENTS_TS = '''
import { Pool } from 'pg'
import Redis from 'ioredis'
import * as nodemailer from 'nodemailer'
import mongoose from 'mongoose'
import knex from 'knex'
import SftpClient from 'ssh2-sftp-client'
import express from 'express'
import env from './env'

const DB_URL = process.env.DATABASE_URL || 'postgres://localhost/app'
export const app = express()

export function pool() {
  return new Pool({ connectionString: DB_URL })
}
export class Cache extends Redis {
  constructor() { super(env.REDIS_URL) }
}
export const side = new Redis(6379, 'cache.internal.example')
export function mailer() {
  return nodemailer.createTransport({ host: process.env.SMTP_HOST, port: 587, auth: { user: 'u', pass: process.env.SMTP_PASSWORD } })
}
export const tls = nodemailer.createTransport({ host: 'mail.example.org', secure: true })
export async function mongo() { await mongoose.connect(process.env.MONGO_URL as string) }
export const kx = knex({ client: 'pg', connection: { host: 'db.example.org', port: 5433, password: 'pg-fixture-literal' } })
export const lite = knex({ client: 'sqlite3', connection: { filename: 'x.db' } })
export const local = new Redis('redis://localhost:6379')
export async function upload() {
  const sftp = new SftpClient()
  await sftp.connect({ host: process.env.SFTP_HOST, port: 22 })
}
export function setup() { process.env.FEATURE_FLAG = 'on' }
'''


@pytest.mark.skipif(not (ROOT / "codegraph" / "plugins" / "ts" / "extractor" / "node_modules").exists(),
                    reason="run `npm ci` in codegraph/plugins/ts/extractor")
def test_node_client_constructors_and_env_wrappers(tmp_path):
    """#103: Node client constructors (pg, ioredis incl. a subclass, nodemailer, mongoose, knex, ssh2-sftp-client)
    are CONNECTS_TO from the constructing code; `env.X` wrappers and values parsed from process.env read the key;
    `process.env.X = ..` is not a read."""
    (tmp_path / "package.json").write_text(json.dumps({"name": "fx", "dependencies": {
        "express": "4", "pg": "8", "ioredis": "5", "nodemailer": "6", "mongoose": "8", "knex": "3", "ssh2-sftp-client": "9"}}))
    (tmp_path / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"esModuleInterop": True}}))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "db.ts").write_text(CLIENTS_TS)
    (tmp_path / "src" / "env.ts").write_text("class Env { REDIS_URL = process.env.REDIS_URL }\nexport default new Env()\n")
    (tmp_path / "src" / "config.ts").write_text(
        "const parsed = Schema.safeParse(process.env)\nconst dto = parsed.data\nexport const queue = dto.QUEUE_URL\n")
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "fx")
    st = GraphStore(db)
    e, ct = ext(st), edges(st, "CONNECTS_TO")
    assert ("function:src/db.ts#pool", "external:postgres:env:DATABASE_URL") in ct
    assert e["external:postgres:env:DATABASE_URL"]["library"] == "pg"
    assert ("class:src/db.ts#Cache", "external:redis:env:REDIS_URL") in ct              # super() of a client subclass
    assert ("module:src/db.ts", "external:redis:cache.internal.example:6379") in ct
    assert ("function:src/db.ts#mailer", "external:smtp:env:SMTP_HOST") in ct
    assert e["external:smtp:mail.example.org:465"]["tls"] is True
    assert ("function:src/db.ts#mongo", "external:mongodb:env:MONGO_URL") in ct
    assert e["external:postgres:db.example.org:5433"]["credential_source"] == "literal"
    assert ("function:src/db.ts#upload", "external:ssh:env:SFTP_HOST") in ct
    assert not any("localhost" in k or "sqlite" in k for k in e)
    assert ("external:smtp:env:SMTP_HOST", "env:SMTP_PASSWORD") in edges(st, "CREDENTIAL_FROM")
    env = edges(st, "READS_ENV")
    assert ("module:src/config.ts", "env:QUEUE_URL") in env
    assert not any(d == "env:FEATURE_FLAG" for _, d in env)
    assert b"pg-fixture-literal" not in db.read_bytes()


def test_impact_on_table_includes_column_users(tmp_path):
    """#103: `impact table:x` also follows READS_COLUMN / WRITES_COLUMN into code that touches only some columns."""
    from codegraph.core.model import Edge, Node
    from codegraph.query import impact
    st = GraphStore.create(tmp_path / "g.db")
    st.write([Node("table:orders", "table", "orders"), Node("column:orders.total", "column", "total"),
              Node("table:users", "table", "users"), Node("column:users.email", "column", "email"),
              *(Node(f"function:{n}", "function", n, file="a.py", line=i + 1, lang="python")
                for i, n in enumerate(("repo", "report", "mail", "handler")))],
             [Edge("table:orders", "column:orders.total", "CONTAINS"),
              Edge("table:users", "column:users.email", "CONTAINS"),
              Edge("function:repo", "table:orders", "READS_TABLE", "a.py", 1),
              Edge("function:report", "column:orders.total", "READS_COLUMN", "a.py", 2),
              Edge("function:mail", "column:users.email", "READS_COLUMN", "a.py", 3),
              Edge("function:handler", "function:report", "CALLS", "a.py", 4)])
    callers = {c["name"]: c["depth"] for c in impact(st, "table:orders")["callers"]}
    assert callers == {"repo": 1, "report": 1, "handler": 2}           # not mail: another table's column
    assert {c["name"] for c in impact(st, "column:orders.total")["callers"]} == {"report", "handler"}


def test_python_clients_connected_after_construction(tmp_path):
    """#103: `c = paramiko.SSHClient(); c.connect(host)` / `ftp = ftplib.FTP(); ftp.connect(host, port)` /
    `self.smtp = smtplib.SMTP(); self.smtp.connect(host)`: the connect call carries the address."""
    (tmp_path / "jobs.py").write_text(
        "import os, ftplib, smtplib\nimport paramiko\n\n\n"
        "def backup():\n    c = paramiko.SSHClient()\n    c.connect(hostname='backup.internal.example', port=2222,"
        " password=os.environ['BACKUP_PW'])\n\n\n"
        "def mirror():\n    ftp = ftplib.FTP()\n    ftp.connect(os.environ.get('FTP_HOST'), 21)\n\n\n"
        "class Mailer:\n    def __init__(self):\n        self.smtp = smtplib.SMTP()\n\n"
        "    def open(self):\n        self.smtp.connect('relay.internal.example', 25)\n\n\n"
        "def local():\n    c = paramiko.SSHClient()\n    c.connect('localhost')\n")
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "jobs")
    st = GraphStore(db)
    e, ct = ext(st), edges(st, "CONNECTS_TO")
    assert ("function:jobs.backup", "external:ssh:backup.internal.example:2222") in ct
    assert e["external:ssh:backup.internal.example:2222"]["credential_at"] == "env:BACKUP_PW"
    assert ("function:jobs.mirror", "external:ftp:env:FTP_HOST") in ct
    assert ("method:jobs.Mailer.open", "external:smtp:relay.internal.example:25") in ct
    assert not any(s == "function:jobs.local" for s, _ in ct)


# ---- #41: ORM datasources (Prisma)
@pytest.fixture(scope="module")
def prisma_db(tmp_path_factory):
    d = tmp_path_factory.mktemp("prisma") / "p.db"
    index_project(FX / "shop-prisma", d, "shop-prisma")
    return d


def test_prisma_datasource_system(prisma_db):
    st = GraphStore(prisma_db)
    a = ext(st)["external:postgres:pg.internal:5432"]
    assert (a["library"], a["datasource"], a["address_source"], a["tls"]) == ("prisma", "db", "env-example", True)
    assert (a["credential_source"], a["credential_at"]) == ("env", "env:DATABASE_URL")
    sysof = {r["id"]: json.loads(r["attrs"]).get("system") for r in st.q("SELECT id, attrs FROM nodes WHERE kind='table'")}
    assert sysof == {"table:orders": "external:postgres:pg.internal:5432", "table:Customer": "external:postgres:pg.internal:5432"}
    assert ("table:orders", "external:postgres:pg.internal:5432") in edges(st, "CONNECTS_TO")
    assert b"pr1sma-fixture-pw" not in prisma_db.read_bytes()


def test_prisma_impact_and_cli(prisma_db):
    r = cli("impact", "external:postgres:pg.internal:5432", "--db", str(prisma_db))
    assert "listOrders" in r.stdout and "GET /orders" in r.stdout
    r = cli("external", "--db", str(prisma_db))
    assert "2 tables (prisma datasource db): Customer, orders" in r.stdout
    r = cli("external", "--db", str(prisma_db), "--json")
    s = json.loads(r.stdout)["systems"][0]
    assert s["tables"] == ["table:Customer", "table:orders"] and s["users"] == [] and s["callers"] == 1


def test_prisma_sqlite_and_literal(tmp_path):
    for name, provider, url in (("lite", "sqlite", '"file:./dev.db"'), ("mongo", "mongodb", '"mongodb://mongo.internal:27017/shop"'),
                                ("cfg", "mysql", None)):
        p = tmp_path / name
        (p / "prisma").mkdir(parents=True)
        (p / "package.json").write_text('{"dependencies": {"express": "^4.19.0", "@prisma/client": "^5.0.0"}}')
        if url is None:      # Prisma 7: the url lives in prisma.config.ts
            (p / "prisma.config.ts").write_text("import { defineConfig, env } from 'prisma/config'\n"
                                                "export default defineConfig({ datasource: { url: env('SHOP_DB_URL') } })\n")
        (p / "prisma" / "schema.prisma").write_text(f'datasource db {{\n  provider = "{provider}"\n' + (f'  url = {url}\n' if url else '') + '}\n\n'
                                                    'model User {\n  id Int @id\n  email String\n}\n')
        (p / "tsconfig.json").write_text("{}")
        (p / "index.ts").write_text("import { PrismaClient } from '@prisma/client'\nconst prisma = new PrismaClient()\n"
                                    "export function users() { return prisma.user.findMany() }\n")
        index_project(p, tmp_path / f"{name}.db", name)
    assert ext(GraphStore(tmp_path / "lite.db")) == {}                    # a local file, not a system
    m = ext(GraphStore(tmp_path / "mongo.db"))["external:mongodb:mongo.internal:27017"]
    assert (m["address_source"], m["library"]) == ("literal", "prisma")
    c = ext(GraphStore(tmp_path / "cfg.db"))["external:mysql:env:SHOP_DB_URL"]
    assert (c["address_source"], c["confidence"]) == ("env", "heuristic")

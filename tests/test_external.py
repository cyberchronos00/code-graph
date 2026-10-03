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

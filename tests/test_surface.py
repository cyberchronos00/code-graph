"""`cg surface` / MCP `attack_surface` (#47 part 1): findings, inbound / outbound surface, JSON / SARIF, --fail-on,
`.cg.yaml` surface.ignore, combined graphs, and no secret values in any output.

Fixtures (tests/surface_fixture): py (Django), ts (Express), php (Laravel), rs (Rust listener). Their fake secrets all
start with `SURF-`; test_no_secret_values greps every output format for each of them. The SARIF 2.1.0 schema
(tests/sarif-schema-2.1.0.json, OASIS, https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/) is vendored
for the schema check, which is skipped when `jsonschema` is not importable."""
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from cg_code_graph import surface as S  # noqa: E402
from cg_code_graph.cli import main  # noqa: E402
from cg_code_graph.config import ConfigError, parse  # noqa: E402
from cg_code_graph.core.model import Edge, Node  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.link import link_many  # noqa: E402

FIX = ROOT / "tests" / "surface_fixture"
NEEDS = {"ts": ("node", "node is not installed (TS extractor)"), "php": ("php", "php is not installed (PHP extractor)")}
SECRET_RE = re.compile(r"SURF-[A-Z]+-[A-Za-z0-9-]+")
FINDING_NAMES = ["hardcoded", "plaintext", "unverified", "unguarded", "exposed-listener"]       # what these fixtures produce
PART2A_NAMES = ["tls-off", "ipc-exposed"]                                                         # tests/test_surface_transport.py

# (finding, node) pairs each fixture must produce; protocol-level facts only, no line numbers
EXPECTED = {
    "py": {
        ("hardcoded", "external:postgres:db.bookstore.example:5432"), ("hardcoded", "external:saas:postmark"),
        ("plaintext", "external:amqp:queue.bookstore.example:5672"), ("plaintext", "external:redis:cache.bookstore.example:6379"),
        ("unverified", "route:ANY /hooks/github-open/"),
        ("unguarded", "route:ANY /admin/purge/"), ("unguarded", "route:ANY /health/"), ("unguarded", "route:ANY /hooks/github-open/"),
        ("exposed-listener", "endpoint:tcp:7100"),
    },
    "ts": {
        ("hardcoded", "external:postgres:pg.bookstore.example:5432"), ("hardcoded", "external:saas:postmark"),
        ("plaintext", "external:http:partner.bookstore.example:80"),
        ("unverified", "route:POST /hooks/github-open"),
        ("unguarded", "route:DELETE /admin/orders"), ("unguarded", "route:GET /stock"), ("unguarded", "route:POST /hooks/github-open"),
        ("exposed-listener", "endpoint:tcp:7200"),
    },
    "php": {
        ("hardcoded", "external:postgres:pg.bookstore.example:5432"), ("hardcoded", "external:saas:mailgun"),
        ("hardcoded", "config:services.github.secret"), ("hardcoded", "config:services.ledger.token"),
        ("plaintext", "external:http:ledger.bookstore.example:80"),
        ("unverified", "route:POST /hooks/github-open"),
        ("unguarded", "route:DELETE /admin/orders"), ("unguarded", "route:GET /health"), ("unguarded", "route:POST /hooks/github-open"),
        ("exposed-listener", "endpoint:tcp:7300"),
    },
}


def _need(name):
    if name in NEEDS and shutil.which(NEEDS[name][0]) is None:
        pytest.skip(NEEDS[name][1])
    if name == "ts" and not (ROOT / "cg_code_graph" / "plugins" / "ts" / "extractor" / "node_modules").exists():
        pytest.skip("run `npm ci` in cg_code_graph/plugins/ts/extractor")


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("surface")
    cache: dict = {}

    def get(name):
        _need(name)
        if name not in cache:
            cache[name] = d / f"{name}.db"
            index_project(FIX / name, cache[name], name)
        return cache[name]
    return get


def cg(capsys, *args):
    rc = main([str(a) for a in args]) or 0
    out = capsys.readouterr()
    return rc, out.out, out.err


def run_json(capsys, db, *args):
    rc, out, _ = cg(capsys, "surface", "--db", db, "--format", "json", *args)
    return rc, json.loads(out)


def pairs(res):
    return {(f["finding"], f["node"]) for f in res["findings"]}


# ------------------------------------------------------------------ findings
@pytest.mark.parametrize("lang", ["py", "ts", "php"])
def test_findings_per_language(dbs, capsys, lang):
    rc, res = run_json(capsys, dbs(lang))
    assert rc == 0
    assert pairs(res) == EXPECTED[lang]
    assert res["summary"]["findings"] == len(EXPECTED[lang])


def test_every_finding_type_across_languages(dbs, capsys):
    seen = set()
    for lang in ("py", "ts", "php"):
        seen |= {f for f, _ in pairs(run_json(capsys, dbs(lang))[1])}
    assert seen == set(FINDING_NAMES) == set(S.FINDINGS) - set(PART2A_NAMES)


@pytest.mark.parametrize("lang", ["py", "ts", "php"])
def test_finding_shape(dbs, capsys, lang):
    _, res = run_json(capsys, dbs(lang))
    for f in res["findings"]:
        assert {"finding", "severity", "confidence", "protocol", "node", "file", "line", "entry_points", "detail",
                "fingerprint"} <= set(f)
        assert f["severity"] in ("high", "medium", "low") and f["confidence"] in ("exact", "resolved", "heuristic")
        assert f["file"] and isinstance(f["line"], int) and f["line"] >= 1 and f["protocol"]
        assert f["fingerprint"] == S.fingerprint(f["finding"], f["node"], f["file"])
        assert (ROOT / "tests" / "surface_fixture" / lang / f["file"]).is_file()
        assert "repo" not in f                       # single-repo graph
        for e in f["entry_points"]:
            assert set(e) == {"kind", "count", "sample"}
        assert f["entry_point_total"] >= sum(e["count"] for e in f["entry_points"])


def test_entry_points_reach_findings(dbs, capsys):
    _, res = run_json(capsys, dbs("py"))
    by = {(f["finding"], f["node"]): f for f in res["findings"]}
    pm = by[("hardcoded", "external:saas:postmark")]
    assert [e["kind"] for e in pm["entry_points"]] == ["http_route"]          # create_order -> send_receipt
    assert pm["entry_points"][0]["sample"] == "route:ANY /orders/"
    assert by[("exposed-listener", "endpoint:tcp:7100")]["entry_points"][0]["kind"] == "message_handler"
    assert by[("unguarded", "route:ANY /health/")]["entry_points"][0]["kind"] == "http_route"


def test_entry_points_are_capped_and_counted(tmp_path):
    kinds = ["http_route", "websocket", "scheduled", "queue_job", "listener", "main", "message_handler"]
    st = GraphStore.create(tmp_path / "g.db")
    st.write([Node(id="external:ftp:files.bookstore.example:21", kind="external", name="x", attrs={
        "protocol": "ftp", "target": "files.bookstore.example:21", "host": "files.bookstore.example", "port": 21,
        "scheme": "ftp", "tls": False, "confidence": "exact", "address_at": "app/ftp.py:3"}),
              Node(id="function:app.sync", kind="function", name="sync", file="app/ftp.py", line=2)],
             [Edge("function:app.sync", "external:ftp:files.bookstore.example:21", "CONNECTS_TO", "app/ftp.py", 3)])
    st.db.executemany("INSERT INTO node_entry VALUES (?,?,?,?)", [("function:app.sync", k, i + 1, f"s{i}") for i, k in enumerate(kinds)])
    st.db.commit()
    res = S.surface(st)
    (f,) = res["findings"]
    assert f["finding"] == "plaintext" and len(f["entry_points"]) == S.ENTRY_CAP
    assert f["entry_point_total"] == sum(range(1, 8)) and f["entry_points"][0]["count"] == 7
    assert "... " in S.render_surface(res)


def test_fingerprint_is_stable_across_runs_and_line_shifts(tmp_path, dbs, capsys):
    _, base = run_json(capsys, dbs("py"))
    copy = tmp_path / "py"
    shutil.copytree(FIX / "py", copy)
    for rel in ("bookshop/settings.py", "bookshop/notify.py", "bookshop/ingest.py"):
        p = copy / rel
        p.write_text("# shifted\n\n" + p.read_text())
    db = tmp_path / "shifted.db"
    index_project(copy, db, "py")
    _, shifted = run_json(capsys, db)
    a = {f["fingerprint"]: f for f in base["findings"]}
    b = {f["fingerprint"]: f for f in shifted["findings"]}
    assert set(a) == set(b) and len(a) == len(base["findings"])
    moved = [k for k in a if a[k]["file"] in ("bookshop/settings.py", "bookshop/notify.py", "bookshop/ingest.py")]
    assert moved and all(b[k]["line"] == a[k]["line"] + 2 for k in moved)


def test_hermetic_fixtures():
    bad = [p for p in FIX.rglob("Cargo.lock")]
    bad += [p for p in FIX.rglob("*") if p.is_dir() and p.name in ("node_modules", "vendor", "__pycache__", ".venv", "target")]
    assert not bad, f"fixtures must not carry dependency directories: {bad}"
    assert (ROOT / "tests" / "sarif-schema-2.1.0.json").is_file()


# ------------------------------------------------------------------ inbound / outbound surface
def _inbound(capsys, db, *args):
    return {i["node"]: i for i in run_json(capsys, db, "--inbound", *args)[1]["inbound"]}


def test_inbound_surface_python(dbs, capsys):
    items = _inbound(capsys, dbs("py"))
    assert items["route:ANY /orders/"]["guard_state"] == "guarded" and "login_required" in items["route:ANY /orders/"]["guards"][0]
    assert items["route:ANY /admin/purge/"]["guard_state"] == "unguarded" and items["route:ANY /admin/purge/"]["reaches_write"]
    assert not items["route:ANY /health/"]["reaches_write"]
    wh = items["route:ANY /hooks/github-open/"]
    assert wh["webhook"]["verified"] is False and wh["webhook"]["provider"] == "github"
    ok = items["route:ANY /hooks/github/"]
    assert ok["guard_state"] == "secret-checked" and ok["webhook"]["verified"] is True
    t = items["endpoint:tcp:7100"]
    assert (t["protocol"], t["transport"], t["exposure"], t["port"]) == ("tcp", "tcp", "all", 7100)
    assert items["endpoint:tcp:7101"]["exposure"] == "loopback"
    assert all(i["file"] and i["entry_points"] for i in items.values())


def test_inbound_surface_php_and_ts(dbs, capsys):
    php = _inbound(capsys, dbs("php"))
    assert php["route:POST /orders"]["guard_state"] == "guarded" and php["route:POST /orders"]["guards"] == ["auth:sanctum"]
    assert php["route:POST /orders"]["reaches_write"] and php["route:DELETE /admin/orders"]["reaches_write"]
    assert php["route:POST /hooks/github"]["guard_state"] == "secret-checked"
    assert php["endpoint:tcp:7300"]["exposure"] == "all" and php["endpoint:tcp:7301"]["exposure"] == "loopback"
    ts = _inbound(capsys, dbs("ts"))
    assert ts["route:POST /orders"]["guards"][0] == "requireAuth" and ts["route:POST /orders"]["guard_state"] == "guarded"
    assert ts["route:POST /hooks/github-open"]["webhook"]["verified"] is False
    assert ts["endpoint:tcp:7200"]["exposure"] == "all" and ts["endpoint:tcp:7201"]["exposure"] == "loopback"


def test_inbound_covers_other_protocols(capsys, tmp_path):
    """Socket.IO server handlers are listed with their guards, their client-side handlers are not."""
    db = tmp_path / "rt.db"
    index_project(ROOT / "tests" / "realtime_fixture", db, "rt")
    items = _inbound(capsys, db)
    assert {i["protocol"] for i in items.values()} == {"socketio"}
    assert items["endpoint:socketio:/admin#kick"]["guard_state"] == "guarded"
    assert items["endpoint:socketio:/#room:join"]["guard_state"] == "unguarded"
    assert not any("chat-web" in (i["file"] or "") for i in items.values())
    db2 = tmp_path / "rpc.db"
    index_project(ROOT / "tests" / "rpc_fixture", db2, "rpc")
    assert {"grpc", "jsonrpc"} <= {i["protocol"] for i in _inbound(capsys, db2).values()}


def test_outbound_surface(dbs, capsys):
    _, res = run_json(capsys, dbs("py"), "--outbound")
    by = {o["node"]: o for o in res["outbound"]}
    assert set(by) == {"external:amqp:queue.bookstore.example:5672", "external:postgres:db.bookstore.example:5432",
                       "external:redis:cache.bookstore.example:6379", "external:saas:postmark"}
    pg = by["external:postgres:db.bookstore.example:5432"]
    assert pg["credential_source"] == "literal" and pg["credential_at"] == "bookshop/settings.py:6"
    assert pg["address_source"] == "literal" and pg["address_at"] == "bookshop/settings.py:1"
    assert by["external:amqp:queue.bookstore.example:5672"]["tls"] is False
    assert by["external:saas:postmark"]["credential_source"] == "literal"
    assert by["external:saas:postmark"]["entry_points"][0]["kind"] == "http_route"
    assert "inbound" not in res and res["surface_counts"]["outbound"] == 4
    assert {f["finding"] for f in res["findings"]} == {"hardcoded", "plaintext"}       # direction filter on findings


def test_php_credential_in_dsn_and_config_default(tmp_path, capsys):
    """A DSN with an inline password (credential_in_url) and an env() default name the kind, never the value."""
    _need("php")
    db = tmp_path / "pdo.db"
    index_project(ROOT / "tests" / "external_fixture" / "shop-php-pdo", db, "pdo")
    _, res = run_json(capsys, db)
    (f,) = [f for f in res["findings"] if f["finding"] == "hardcoded"]
    assert f["node"] == "external:postgres:pg.internal:5432" and "inline in the postgres connection URL" in f["detail"]
    assert f["file"] == ".env.example" and f["line"] == 1


def test_other_systems_show_up_without_code_changes(tmp_path):
    """Attrs read generically: a push / Kubernetes style system with credential_literal, a Docker TCP host without
    TLS, a literal_credential edge, a loopback host (not reported) and an env-sourced one (not reported)."""
    st = GraphStore.create(tmp_path / "g.db")
    nodes = [
        Node(id="external:push:fcm", kind="external", name="fcm", attrs={
            "protocol": "push", "target": "fcm", "auth": "explicit", "credential_literal": True, "credential_source": "literal",
            "credential_at": "app/push.py:4", "confidence": "exact"}),
        Node(id="external:docker:10.2.3.4:2375", kind="external", name="d", attrs={
            "protocol": "docker", "target": "10.2.3.4:2375", "host": "10.2.3.4", "port": 2375, "scheme": "tcp", "tls": False,
            "confidence": "resolved", "address_at": "ops/deploy.py:9"}),
        Node(id="external:kube:cluster", kind="external", name="k", attrs={
            "protocol": "kube", "target": "cluster", "auth": "explicit", "confidence": "exact"}),
        Node(id="external:docker:localhost:2375", kind="external", name="l", attrs={
            "protocol": "docker", "target": "localhost:2375", "host": "localhost", "tls": False, "confidence": "exact"}),
        Node(id="function:app.push", kind="function", name="push", file="app/push.py", line=3),
        Node(id="function:ops.kube", kind="function", name="kube", file="ops/kube.py", line=5),
    ]
    edges = [Edge("function:app.push", "external:push:fcm", "CONNECTS_TO", "app/push.py", 4),
             Edge("function:ops.kube", "external:kube:cluster", "CONNECTS_TO", "ops/kube.py", 7,
                  attrs={"literal_credential": True, "via": "kubernetes"})]
    st.write(nodes, edges)
    res = S.surface(st)
    got = {(f["finding"], f["node"], f["file"], f["line"]) for f in res["findings"]}
    assert got == {("hardcoded", "external:push:fcm", "app/push.py", 4),
                   ("hardcoded", "external:kube:cluster", "ops/kube.py", 7),
                   ("plaintext", "external:docker:10.2.3.4:2375", "ops/deploy.py", 9)}


# ------------------------------------------------------------------ renderers and filters
def test_text_output(dbs, capsys):
    rc, out, _ = cg(capsys, "surface", "--db", dbs("php"), "--inbound", "--outbound")
    assert rc == 0
    for frag in ("attack surface: 10 finding(s)", "== hardcoded (4)", "== plaintext (1)", "== unverified (1)", "== unguarded (3)",
                 "== exposed-listener (1)", "== inbound surface", "== outbound surface", "config/database.php:12",
                 "app/Services/Mailers.php:10", "reached from http_route(1)", "fingerprint ", "webhook github NOT verified",
                 "reaches a write", "bind all", "value not shown"):
        assert frag in out, frag
    assert out.index("== hardcoded") < out.index("== plaintext") < out.index("== unverified") < out.index("== exposed-listener")
    rc, short, _ = cg(capsys, "surface", "--db", dbs("php"))
    assert "== inbound surface" not in short and "inbound surface: 7 item(s)" in short


def test_filters(dbs, capsys):
    db = dbs("php")
    _, res = run_json(capsys, db, "--finding", "plaintext")
    assert pairs(res) == {("plaintext", "external:http:ledger.bookstore.example:80")}
    _, res = run_json(capsys, db, "--protocol", "http")
    assert {p for p, _ in pairs(res)} == {"plaintext", "unverified", "unguarded"}
    _, res = run_json(capsys, db, "--protocol", "saas")
    assert pairs(res) == {("hardcoded", "external:saas:mailgun")}
    _, res = run_json(capsys, db, "--inbound")
    assert {f["finding"] for f in res["findings"]} == {"unverified", "unguarded", "exposed-listener"} and "inbound" in res
    _, res = run_json(capsys, db, "--min-confidence", "resolved")
    assert ("hardcoded", "config:services.ledger.token") not in pairs(res)       # heuristic: an env() default
    assert ("hardcoded", "config:services.github.secret") in pairs(res)
    _, all_ = run_json(capsys, db)
    assert ("hardcoded", "config:services.ledger.token") in pairs(all_)
    _, res = run_json(capsys, db, "--max-items", "2")
    assert len(res["findings"]) == 2 and res["findings_total"] == 10


def test_unknown_finding_is_a_usage_error(dbs, capsys):
    rc, _, err = cg(capsys, "surface", "--db", dbs("py"), "--finding", "ssrf")
    assert rc == 2 and "unknown finding 'ssrf'" in err
    rc, _, err = cg(capsys, "surface", "--db", dbs("py"), "--fail-on", "hardcoded,bogus")
    assert rc == 2 and "bogus" in err


def test_strict_counts_route_guards_only(tmp_path, capsys):
    _need("php")
    db = tmp_path / "ig.db"
    index_project(ROOT / "tests" / "inline_guards_fixture", db, "ig")
    loose = run_json(capsys, db, "--finding", "unguarded")[1]["summary"]["findings"]
    strict = run_json(capsys, db, "--finding", "unguarded", "--strict")[1]["summary"]["findings"]
    assert loose == 17 and strict == 46            # the numbers of `cg routes --unguarded` / `--unguarded --strict`


# ------------------------------------------------------------------ SARIF
@pytest.mark.parametrize("lang", ["py", "php"])
def test_sarif_structure(dbs, capsys, lang):
    rc, out, _ = cg(capsys, "surface", "--db", dbs(lang), "--format", "sarif")
    sarif = json.loads(out)
    assert rc == 0 and sarif["version"] == "2.1.0" and sarif["$schema"].endswith("sarif-schema-2.1.0.json")
    (run,) = sarif["runs"]
    drv = run["tool"]["driver"]
    assert drv["name"] == "cg"
    rules = {r["id"]: r for r in drv["rules"]}
    assert set(rules) == {f"cg.surface.{n}" for n in FINDING_NAMES + PART2A_NAMES}   # one rule per finding type
    assert len(run["results"]) == len(EXPECTED[lang])
    for r in run["results"]:
        assert r["ruleId"] in rules and drv["rules"][r["ruleIndex"]]["id"] == r["ruleId"]
        assert r["level"] in ("error", "warning", "note") and r["message"]["text"]
        (loc,) = r["locations"]
        pl = loc["physicalLocation"]
        assert not pl["artifactLocation"]["uri"].startswith(("/", "py/", "php/")) and pl["region"]["startLine"] >= 1
        assert (FIX / lang / pl["artifactLocation"]["uri"]).is_file()
        assert re.fullmatch(r"[0-9a-f]{16}", r["partialFingerprints"]["cg/surface/v1"])
    levels = {r["ruleId"].split(".")[-1]: r["level"] for r in run["results"]}
    assert levels["hardcoded"] == "error" and levels["unguarded"] == "warning"


def test_sarif_validates_against_the_2_1_0_schema(dbs, capsys):
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema is not installed: SARIF schema validation skipped")
    schema = json.loads((ROOT / "tests" / "sarif-schema-2.1.0.json").read_text())
    validator = jsonschema.Draft4Validator(schema)
    for lang in ("py", "php"):
        sarif = json.loads(cg(capsys, "surface", "--db", dbs(lang), "--format", "sarif", "--show-ignored")[1])
        errors = [e.message for e in validator.iter_errors(sarif)]
        assert not errors, errors[:3]


# ------------------------------------------------------------------ --fail-on
def test_fail_on_exit_codes(dbs, capsys, tmp_path):
    db = dbs("py")
    assert cg(capsys, "surface", "--db", db, "--fail-on", "hardcoded")[0] == 1
    assert cg(capsys, "surface", "--db", db, "--fail-on", "plaintext,unverified")[0] == 1
    rc, out, _ = cg(capsys, "surface", "--db", db, "--fail-on", "exposed-listener")
    assert rc == 1 and "gate FAILED: exposed-listener" in out
    assert cg(capsys, "surface", "--db", db)[0] == 0
    # a gate judges every finding type, not only the one shown
    assert cg(capsys, "surface", "--db", db, "--finding", "plaintext", "--fail-on", "hardcoded")[0] == 1
    res = run_json(capsys, db, "--fail-on", "hardcoded,unverified")[1]
    assert res["failed"] and res["failing"] == ["hardcoded", "unverified"]
    # nothing of that type left: exit 0
    empty = tmp_path / "e.db"
    st = GraphStore.create(empty)
    st.write([Node(id="function:a", kind="function", name="a", file="a.py", line=1)], [])
    rc, out, _ = cg(capsys, "surface", "--db", empty, "--fail-on", "hardcoded,plaintext,unverified,unguarded,exposed-listener")
    assert rc == 0 and "gate passed" in out and "no findings" in out
    # --protocol narrows the gate
    assert cg(capsys, "surface", "--db", db, "--protocol", "saas", "--fail-on", "plaintext")[0] == 0


# ------------------------------------------------------------------ ignores
def _ignored_copy(tmp_path, ignores_yaml, lang="py"):
    root = tmp_path / lang
    shutil.copytree(FIX / lang, root)
    (root / ".cg.yaml").write_text("version: 1\nsurface:\n  ignore:\n" + ignores_yaml)
    db = tmp_path / f"{lang}-ignored.db"
    index_project(root, db, lang)
    return db


def test_ignore_by_path_id_and_fingerprint(dbs, capsys, tmp_path):
    _, base = run_json(capsys, dbs("py"))
    fp = next(f["fingerprint"] for f in base["findings"] if f["node"] == "external:redis:cache.bookstore.example:6379")
    db = _ignored_copy(tmp_path, f"""\
    - {{finding: unguarded, path: "bookshop/urls.py", reason: public pages by design}}
    - {{finding: hardcoded, id: "external:saas:*", reason: sandbox key}}
    - {{finding: plaintext, fingerprint: {fp}, reason: compose-internal cache}}
""")
    rc, res = run_json(capsys, db, "--show-ignored")
    gone = {("unguarded", "route:ANY /admin/purge/"), ("unguarded", "route:ANY /health/"),
            ("unguarded", "route:ANY /hooks/github-open/"), ("hardcoded", "external:saas:postmark"),
            ("plaintext", "external:redis:cache.bookstore.example:6379")}
    assert pairs(res) == EXPECTED["py"] - gone
    assert res["ignored_count"] == 5 and {(f["finding"], f["node"]) for f in res["ignored"]} == gone
    reasons = {f["ignored_by"]["reason"] for f in res["ignored"]}
    assert reasons == {"public pages by design", "sandbox key", "compose-internal cache"}
    # the unverified finding on the same route is a different finding type: still reported
    assert ("unverified", "route:ANY /hooks/github-open/") in pairs(res)
    rc, out, _ = cg(capsys, "surface", "--db", db)
    assert "5 ignored (--show-ignored lists them)" in out and "route:ANY /admin/purge/" not in out
    rc, out, _ = cg(capsys, "surface", "--db", db, "--show-ignored")
    assert "== ignored (5)" in out and "ignored: sandbox key" in out
    sarif = json.loads(cg(capsys, "surface", "--db", db, "--show-ignored", "--format", "sarif")[1])
    sup = [r for r in sarif["runs"][0]["results"] if r.get("suppressions")]
    assert len(sup) == 5 and {r["suppressions"][0]["justification"] for r in sup} == reasons
    plain = json.loads(cg(capsys, "surface", "--db", db, "--format", "sarif")[1])
    assert len(plain["runs"][0]["results"]) == len(EXPECTED["py"]) - 5


def test_ignored_findings_do_not_fail_the_gate(capsys, tmp_path):
    db = _ignored_copy(tmp_path, """\
    - {finding: hardcoded, path: "bookshop", reason: demo credentials rotated nightly}
""")
    assert cg(capsys, "surface", "--db", db, "--fail-on", "hardcoded")[0] == 0
    assert cg(capsys, "surface", "--db", db, "--fail-on", "hardcoded,plaintext")[0] == 1


def test_config_ignores_apply_without_reindexing(capsys, tmp_path):
    db = _ignored_copy(tmp_path, '    - {finding: plaintext, id: "external:amqp:*", reason: first}\n')
    assert run_json(capsys, db)[1]["ignored_count"] == 1
    (tmp_path / "py" / ".cg.yaml").write_text("version: 1\nsurface:\n  ignore:\n"
                                              "    - {finding: plaintext, id: \"external:*\", reason: broader}\n")
    assert run_json(capsys, db)[1]["ignored_count"] == 2


def test_ignore_validation(tmp_path, capsys):
    ok = parse({"surface": {"ignore": [{"finding": "plaintext", "id": "external:redis:*", "reason": " accepted "}]}})
    assert ok["surface"]["ignore"] == [{"finding": "plaintext", "id": "external:redis:*", "reason": "accepted"}]
    bad = [
        ([{"finding": "plaintext", "path": "a.py"}], "reason is required"),
        ([{"finding": "plaintext", "path": "a.py", "reason": "  "}], "reason is required"),
        ([{"finding": "nope", "path": "a.py", "reason": "x"}], "is not one of hardcoded"),
        ([{"finding": "plaintext", "reason": "x"}], "at least one of path, id or fingerprint"),
        ([{"finding": "plaintext", "path": "a", "why": "x", "reason": "x"}], "unknown key 'why'"),
        (["plaintext"], "expected a mapping"),
    ]
    for entries, msg in bad:
        with pytest.raises(ConfigError, match=msg):
            parse({"surface": {"ignore": entries}})
    with pytest.raises(ConfigError, match="expected a list"):
        parse({"surface": {"ignore": {"finding": "plaintext"}}})
    (tmp_path / ".cg.yaml").write_text("version: 1\nsurface:\n  ignore:\n    - {finding: unguarded, path: app/x.php}\n")
    assert main(["config", "validate", str(tmp_path)]) == 2
    assert "surface.ignore[0]: reason is required" in capsys.readouterr().err
    (tmp_path / ".cg.yaml").write_text("version: 1\nsurface:\n  ignore:\n    - {finding: unguarded, path: app/x.php, reason: probe}\n")
    assert main(["config", "validate", str(tmp_path)]) == 0
    assert "ok:" in capsys.readouterr().out


# ------------------------------------------------------------------ combined graph
@pytest.fixture(scope="module")
def combined(dbs, tmp_path_factory):
    d = tmp_path_factory.mktemp("surface-combined")
    repos = [(n, str(dbs(n)), "both") for n in ("py", "ts", "php")]
    out = d / "combined.db"
    link_many(repos, str(out))
    return out


def test_combined_graph_names_the_repo(combined, capsys):
    rc, res = run_json(capsys, combined)
    assert res["combined"] and res["repos"] == ["py", "ts", "php"]
    assert all(f["repo"] in ("py", "ts", "php") and f["file"].startswith(f["repo"] + "/") for f in res["findings"])
    by_repo = {r: {(f["finding"], f["node"].split(":", 1)[1] if f["node"].startswith(r + ":") else f["node"])
                   for f in res["findings"] if f["repo"] == r} for r in ("py", "ts", "php")}
    assert ("exposed-listener", "endpoint:tcp:7100") in by_repo["py"]
    assert ("exposed-listener", "endpoint:tcp:7200") in by_repo["ts"]
    assert ("exposed-listener", "endpoint:tcp:7300") in by_repo["php"]
    # one external node shared by two repos: each repo's literal key keeps its own repo and file
    pm = {f["repo"]: f["file"] for f in res["findings"] if f["node"] == "external:saas:postmark"}
    assert pm == {"py": "py/bookshop/notify.py", "ts": "ts/src/notify.ts"}
    rc, out, _ = cg(capsys, "surface", "--db", combined)
    assert "[php] php/config/services.php:5" in out and "[ts] ts/src/db.ts:3" in out


def test_combined_sarif_uses_repo_relative_uris(combined, capsys):
    sarif = json.loads(cg(capsys, "surface", "--db", combined, "--format", "sarif")[1])
    for r in sarif["runs"][0]["results"]:
        repo = r["properties"]["repo"]
        uri = r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert not uri.startswith(repo + "/") and (FIX / repo / uri).is_file(), (repo, uri)


def test_combined_ignore_applies_to_its_own_repo(dbs, capsys, tmp_path):
    root = tmp_path / "ts"
    shutil.copytree(FIX / "ts", root)
    (root / ".cg.yaml").write_text("version: 1\nsurface:\n  ignore:\n"
                                   "    - {finding: hardcoded, id: \"external:saas:postmark\", reason: ts sandbox key}\n")
    ts_db = tmp_path / "ts.db"
    index_project(root, ts_db, "ts")
    out = tmp_path / "c.db"
    link_many([("py", str(dbs("py")), "both"), ("ts", str(ts_db), "both")], str(out))
    res = run_json(capsys, out, "--show-ignored")[1]
    keep = {(f["repo"], f["file"]) for f in res["findings"] if f["node"] == "external:saas:postmark"}
    gone = {(f["repo"], f["file"]) for f in res["ignored"] if f["node"] == "external:saas:postmark"}
    assert keep == {("py", "py/bookshop/notify.py")} and gone == {("ts", "ts/src/notify.ts")}


# ------------------------------------------------------------------ no secret values
def test_no_secret_values(dbs, capsys, combined):
    secrets = set()
    for p in FIX.rglob("*"):
        if p.is_file() and p.suffix in (".py", ".ts", ".php", ".rs", ".yaml", ".json", ".toml"):
            secrets |= set(SECRET_RE.findall(p.read_text()))
    assert len(secrets) >= 7, secrets
    outputs = []
    for db in [dbs("py"), dbs("ts"), dbs("php"), combined]:
        for fmt in ("text", "json", "sarif"):
            outputs.append(cg(capsys, "surface", "--db", db, "--format", fmt, "--inbound", "--outbound", "--show-ignored")[1])
    for out in outputs:
        for secret in secrets:
            assert secret not in out, secret
    assert sum(len(o) for o in outputs) > 20000


EXT = ROOT / "tests" / "external_fixture"


def test_external_systems_from_3b_are_picked_up_without_leaking(tmp_path, capsys):
    secrets = {"literal-auth-token-0123456789", "MAXXXXXXXXXXXXXXXXXX"}
    for name in ("sms-extra", "docker-py"):
        shutil.copytree(EXT / name, tmp_path / name, ignore=shutil.ignore_patterns("node_modules", "vendor"))
    found = {}
    for name in ("sms-extra", "docker-py"):
        db = tmp_path / f"{name}.db"
        index_project(tmp_path / name, db, name)
        for fmt in ("text", "json", "sarif"):
            out = cg(capsys, "surface", "--db", db, "--format", fmt, "--inbound", "--outbound", "--show-ignored")[1]
            for secret in secrets:
                assert secret not in out, (name, fmt, secret)
        found[name] = {(f["finding"], f["node"]) for f in run_json(capsys, db)[1]["findings"]}
    assert ("hardcoded", "external:saas:plivo") in found["sms-extra"]
    assert ("plaintext", "external:docker:10.20.0.5:2375") in found["docker-py"]


def _db_text(path) -> list[tuple[str, str]]:
    """(table.column, text) for every text value of every table of a graph database."""
    out = []
    con = sqlite3.connect(path)
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
        for row in con.execute(f'SELECT * FROM "{table}"'):
            for col, v in zip(cols, row):
                if isinstance(v, bytes):
                    v = v.decode("utf-8", "replace")
                if isinstance(v, str):
                    out.append((f"{table}.{col}", v))
    con.close()
    return out


@pytest.mark.parametrize("lang", ["py", "ts", "php", "rs"])
def test_no_secret_value_is_stored_in_the_database(tmp_path, lang):
    """The fixtures' fake secrets must be nowhere in the SQLite file, in any table (not only in command output)."""
    if lang != "rs":
        _need(lang)
    root = tmp_path / lang
    shutil.copytree(FIX / lang, root, ignore=shutil.ignore_patterns("node_modules", "vendor", "target"))
    db = tmp_path / f"{lang}.db"
    index_project(root, db, lang)
    rows = _db_text(db)
    assert len(rows) > 100
    leaks = [(c, m) for c, v in rows for m in re.findall(r"SURF-[A-Za-z0-9-]*", v)]
    assert not leaks, leaks
    raw = db.read_bytes()
    assert b"SURF-" not in raw


def test_laravel_config_literals_are_redacted_with_a_fingerprint(dbs):
    _need("php")
    con = sqlite3.connect(dbs("php"))
    attrs = {r[0]: json.loads(r[1] or "{}") for r in con.execute("SELECT id, attrs FROM nodes WHERE kind='config'")}
    gh = attrs["config:services.github.secret"]["value"]
    assert re.fullmatch(r"redacted:sha256:[0-9a-f]{8}", gh)
    assert gh == "redacted:sha256:" + __import__("hashlib").sha256(b"SURF-PHP-GH-SECRET-1d4e").hexdigest()[:8]
    dflt = attrs["config:services.ledger.token"]["env_default"]
    assert dflt == ["redacted:sha256:" + __import__("hashlib").sha256(b"SURF-PHP-LEDGER-DEFAULT-d7e2").hexdigest()[:8]]


def test_redaction_keeps_non_secret_values_and_url_hosts(tmp_path, capsys):
    _need("php")
    root = tmp_path / "php"
    shutil.copytree(FIX / "php", root, ignore=shutil.ignore_patterns("vendor"))
    (root / "config" / "shop.php").write_text("""<?php

return [
    'currency' => 'EUR',
    'catalog_host' => env('CATALOG_HOST', 'catalog.bookstore.example'),
    'api_key' => 'SURF-PHP-SHOP-KEY-0001',
    'replica' => env('SHOP_REPLICA_URL', 'mysql://reader:SURF-PHP-URL-PW-0002@replica.bookstore.example/shop'),
    'signing' => ['passphrase' => 'SURF-PHP-SIGN-PASS-0003', 'algorithm' => 'sha256'],
];
""")
    db = tmp_path / "shop.db"
    index_project(root, db, "php")
    con = sqlite3.connect(db)
    attrs = {r[0]: json.loads(r[1] or "{}") for r in con.execute("SELECT id, attrs FROM nodes WHERE kind='config'")}
    assert attrs["config:shop.currency"]["value"] == "EUR"
    assert attrs["config:shop.catalog_host"]["env_default"] == ["catalog.bookstore.example"]
    assert attrs["config:shop.signing.algorithm"]["value"] == "sha256"
    assert attrs["config:shop.api_key"]["value"].startswith("redacted:sha256:")
    assert attrs["config:shop.signing.passphrase"]["value"].startswith("redacted:sha256:")
    url = attrs["config:shop.replica"]["env_default"][0]
    assert url.startswith("mysql://reader:redacted:sha256:") and url.endswith("@replica.bookstore.example/shop")
    assert not [1 for _, v in _db_text(db) if "SURF-" in v]
    found = {(f["finding"], f["node"]) for f in run_json(capsys, db)[1]["findings"]}
    assert ("hardcoded", "config:shop.api_key") in found
    assert not any(n == "config:shop.currency" for _, n in found)


def test_no_secret_values_in_ignored_findings(capsys, tmp_path):
    db = _ignored_copy(tmp_path, '    - {finding: hardcoded, path: "bookshop", reason: sandbox}\n')
    for fmt in ("text", "json", "sarif"):
        out = cg(capsys, "surface", "--db", db, "--format", fmt, "--show-ignored")[1]
        assert "SURF-" not in out and "sandbox" in out


# ------------------------------------------------------------------ Rust
def test_rust_listener(tmp_path, capsys):
    from native_util import TS_SKIP, have_tree_sitter
    if not have_tree_sitter():
        pytest.skip(TS_SKIP)
    root = tmp_path / "rs"
    shutil.copytree(FIX / "rs", root)                  # the Rust analysis writes Cargo.lock / target next to the sources
    db = tmp_path / "rs.db"
    index_project(root, db, "rs")
    _, res = run_json(capsys, db)
    assert pairs(res) == {("exposed-listener", "endpoint:tcp:7400")}
    (f,) = res["findings"]
    assert f["file"] == "src/main.rs" and f["line"] == 5 and f["protocol"] == "tcp"
    assert cg(capsys, "surface", "--db", db, "--fail-on", "exposed-listener")[0] == 1


# ------------------------------------------------------------------ MCP
def test_mcp_attack_surface(dbs, monkeypatch):
    from cg_code_graph import mcp_server
    monkeypatch.setattr(mcp_server, "_st", lambda: GraphStore(dbs("py")))
    txt = str(mcp_server.attack_surface(finding="hardcoded"))
    assert "== hardcoded (2)" in txt and "== plaintext" not in txt and "bookshop/settings.py:6" in txt
    assert "SURF-" not in txt
    s = mcp_server.attack_surface.structured(inbound=True, protocol="tcp")
    assert {i["node"] for i in s["surface"]["inbound"]} == {"endpoint:tcp:7100", "endpoint:tcp:7101"}
    assert "complete" in s["completeness"] and "endpoint:tcp:7100" in s["result"]
    s = mcp_server.attack_surface.structured(outbound=True, min_confidence="exact", finding="plaintext")
    assert {f["node"] for f in s["surface"]["findings"]} == {"external:amqp:queue.bookstore.example:5672",
                                                              "external:redis:cache.bookstore.example:6379"}
    assert "external:saas:postmark" in s["result"] and "SURF-" not in json.dumps(s)


def test_sweep_redacts_credential_attrs_but_not_hosts_flags_or_plain_values():
    from types import SimpleNamespace as NS
    from cg_code_graph.core import redact as R
    n1 = NS(attrs={"token": "ReportsService", "secret": "payments-signing", "password": "SURF-pw-1", "credential_literal": True, "auth": "unknown", "host": "db.example.test",
                   "entries": [{"key": "api_token", "value": "SURF-tok-2"}, {"key": "locale", "value": "en"}],
                   "url": "postgres://app:SURF-pw-3@db.example.test:5432/shop"})
    e1 = NS(kind="HTTP_CALLS", attrs={"literal_credential": True, "setting": "SECRET_KEY", "default": "SURF-key-4", "env_default_x": "x"})
    b = NS(nodes={"a": n1}, edges={"b": e1})
    R.sweep(b)
    text = repr(n1.attrs) + repr(e1.attrs)
    assert "SURF-" not in text
    assert n1.attrs["password"].startswith(R.MARK)
    assert n1.attrs["credential_literal"] is True and n1.attrs["auth"] == "unknown"
    assert n1.attrs["host"] == "db.example.test" and "db.example.test:5432" in n1.attrs["url"]
    assert n1.attrs["entries"][1]["value"] == "en"
    assert n1.attrs["token"] == "ReportsService" and n1.attrs["secret"] == "payments-signing"
    assert e1.attrs["literal_credential"] is True and e1.attrs["default"].startswith(R.MARK)


def test_nest_injects_tokens_are_not_redacted(tmp_path):
    import shutil
    src = Path(__file__).parent.parent / "examples" / "bookstore-nest"
    dst = tmp_path / "nest"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("node_modules"))
    db = tmp_path / "nest.db"
    index_project(dst, db, name="nest")
    con = sqlite3.connect(db)
    rows = [json.loads(a or "{}") for (a,) in con.execute("select attrs from edges where kind='INJECTS'")]
    assert len(rows) >= 10
    assert all(r.get("token") and not str(r["token"]).startswith("redacted:") for r in rows)
    assert any(r["token"].endswith("#ReportsService") for r in rows) and "REPORT_REPOSITORY" in {r["token"] for r in rows}
    assert not any("redacted:" in (a or "") for (a,) in con.execute("select attrs from edges where kind='INJECTS'"))

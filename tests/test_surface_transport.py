"""#47 part 2a: disabled TLS / SSH verification (`tls-off`), insecure gRPC channels (`plaintext`, protocol grpc) and IPC
exposure (`ipc-exposed`) in `cg surface`.

Fixtures (tests/insecure_fixture): py, ts, php, kt, rs, go (one positive per detector and a negative twin each, plus a
test-file hit that must be skipped) and ipc (extension manifests, Electron, postMessage, Unix sockets). Every test
works on a copy in a temp dir, so a stray `node_modules` / `vendor` / `target` never reaches the fixtures. The detectors
are regex-level: Kotlin, Rust and Go need no toolchain here (their function nodes are, when the plugin runs, a bonus;
assertions use file and line)."""
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
from cg_code_graph import insecure_transport as IT  # noqa: E402
from cg_code_graph import surface as S  # noqa: E402
from cg_code_graph.cli import main  # noqa: E402
from cg_code_graph.core.model import Edge, Node  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "insecure_fixture"
LANGS = ["py", "ts", "php", "kt", "rs", "go", "ipc"]
HOMES = {"ts": "node", "ipc": "node", "php": "php"}


def _need(name):
    import shutil as sh
    if name in HOMES and sh.which(HOMES[name]) is None:
        pytest.skip(f"{HOMES[name]} is not installed")
    if name in ("ts", "ipc") and not (ROOT / "cg_code_graph" / "plugins" / "ts" / "extractor" / "node_modules").exists():
        pytest.skip("run `npm ci` in cg_code_graph/plugins/ts/extractor")


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("transport")
    cache: dict = {}

    def get(name):
        _need(name)
        if name not in cache:
            root = d / name
            shutil.copytree(FIX / name, root, ignore=shutil.ignore_patterns("node_modules", "vendor", "target", "Cargo.lock"))
            if name == "ts":
                # a real .env is never read: this value must not become a finding
                (root / ".env").write_text("NODE_TLS_REJECT_UNAUTHORIZED=0\nSTRICT_SSH=StrictHostKeyChecking=no\n")
            cache[name] = d / f"{name}.db"
            index_project(root, cache[name], name)
        return cache[name]
    get.dir = d
    return get


def cg(capsys, *args):
    rc = main([str(a) for a in args]) or 0
    out = capsys.readouterr()
    return rc, out.out, out.err


def run_json(capsys, db, *args):
    rc, out, _ = cg(capsys, "surface", "--db", db, "--format", "json", *args)
    return rc, json.loads(out)


def line_of(lang: str, rel: str, needle: str, nth: int = 1) -> int:
    hits = [i + 1 for i, ln in enumerate((FIX / lang / rel).read_text().splitlines()) if needle in ln]
    assert len(hits) >= nth, (rel, needle)
    return hits[nth - 1]


def lines(res, finding, file, protocol=None) -> set[int]:
    out = set()
    for f in res["findings"]:
        if f["finding"] == finding and f["file"] == file and (protocol is None or f["protocol"] == protocol):
            out |= {f["line"], *f.get("also_at_lines", [])}
    return out


def files(res, finding) -> set[str]:
    return {f["file"] for f in res["findings"] if f["finding"] == finding}


# (finding, file, needle[, protocol]) each fixture must report, one expected line per row; the negatives are every other
# line of those files: lines() must equal exactly the expected set per (finding, file)
EXPECTED = {
    "py": [
        ("tls-off", "bookshop/catalog.py", 'catalog.bookstore.example/books", verify=False', "tls"),
        ("tls-off", "bookshop/catalog.py", "httpx.Client(verify=False)", "tls"),
        ("tls-off", "bookshop/catalog.py", "ctx.check_hostname = False", "tls"),
        ("tls-off", "bookshop/catalog.py", "ctx.verify_mode = ssl.CERT_NONE", "tls"),
        ("tls-off", "bookshop/catalog.py", "return ssl._create_unverified_context()", "tls"),
        ("tls-off", "bookshop/catalog.py", "set_missing_host_key_policy(paramiko.AutoAddPolicy())", "ssh"),
        ("tls-off", "deploy/ssh_config", "StrictHostKeyChecking no", "ssh"),
        ("tls-off", "deploy/ssh_config", "UserKnownHostsFile /dev/null", "ssh"),
        ("tls-off", "ansible/ansible.cfg", "host_key_checking = False", "ssh"),
        ("tls-off", "scripts/sync.sh", 'rsync -e "ssh -o StrictHostKeyChecking=no"', "ssh"),
        ("plaintext", "bookshop/catalog.py", 'grpc.insecure_channel("inventory.bookstore.example:50051")', "grpc"),
        ("plaintext", "bookshop/catalog.py", 'server.add_insecure_port("[::]:50052")', "grpc"),
    ],
    "ts": [
        ("tls-off", "src/partner.ts", "rejectUnauthorized: false", "https"),
        ("tls-off", "src/partner.ts", "process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'", "tls"),
        ("tls-off", "src/ssh.ts", "hostVerifier: () => true", "ssh"),
        ("tls-off", ".env.example", "NODE_TLS_REJECT_UNAUTHORIZED=0", "tls"),
        ("tls-off", "docker-compose.yml", "- NODE_TLS_REJECT_UNAUTHORIZED=0", "tls"),
        ("tls-off", "Dockerfile", "ENV NODE_TLS_REJECT_UNAUTHORIZED=0", "tls"),
        ("tls-off", "package.json", "start:dev", "tls"),
        ("plaintext", "src/rpc.ts", "return grpc.credentials.createInsecure()", "grpc"),
        ("plaintext", "src/rpc.ts", "new Client('inventory.bookstore.example:50051'", "grpc"),
        ("plaintext", "src/rpc.ts", "server.bindAsync('0.0.0.0:50052'", "grpc"),
    ],
    "php": [
        ("tls-off", "app/Services/Fetcher.php", "CURLOPT_SSL_VERIFYPEER, false", "tls"),
        ("tls-off", "app/Services/Fetcher.php", "CURLOPT_SSL_VERIFYHOST, 0", "tls"),
        ("tls-off", "app/Services/Fetcher.php", "['verify' => false]", None),
        ("tls-off", "app/Services/Fetcher.php", "Http::withoutVerifying()", None),
        ("tls-off", "app/Services/Fetcher.php", "'verify_peer' => false", "tls"),
    ],
    "kt": [
        ("tls-off", "src/main/kotlin/shop/Net.kt", "override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}", "tls"),
        ("tls-off", "src/main/kotlin/shop/Net.kt", "override fun verify(hostname: String, session: SSLSession): Boolean = true", "tls"),
        ("tls-off", "src/main/kotlin/shop/Net.kt", "hostnameVerifier { _, _ -> true }", "tls"),
        ("tls-off", "src/main/kotlin/shop/Net.kt", 'setConfig("StrictHostKeyChecking", "no")', "ssh"),
        ("plaintext", "src/main/kotlin/shop/Net.kt", "        .usePlaintext()", "grpc"),
    ],
    "rs": [
        ("tls-off", "src/main.rs", "danger_accept_invalid_certs(true)", "tls"),
        ("tls-off", "src/main.rs", "danger_accept_invalid_hostnames(true)", "tls"),
        ("plaintext", "src/main.rs", 'Channel::from_static("http://inventory.bookstore.example', "grpc"),
    ],
    "go": [
        ("tls-off", "main.go", "InsecureSkipVerify: true", "tls"),
        ("tls-off", "main.go", "ssh.InsecureIgnoreHostKey()", "ssh"),
        ("plaintext", "main.go", 'grpc.Dial("inventory.bookstore.example:50051"', "grpc"),
    ],
    "ipc": [
        ("ipc-exposed", "src/main.ts", "nodeIntegration: true", "electron"),
        ("ipc-exposed", "src/main.ts", "contextIsolation: false", "electron"),
        ("ipc-exposed", "src/main.ts", "webSecurity: false", "electron"),
        ("ipc-exposed", "extension-open/manifest.json", '"matches": ["*://*/*"]', "extension"),
        ("ipc-exposed", "extension-open/manifest.json", '"ids": ["*"]', "extension"),
        ("ipc-exposed", "src/background_open.ts", "chrome.runtime.onConnectExternal.addListener", "extension"),
        ("ipc-exposed", "src/embed.ts", "contentWindow.postMessage({ type: 'cart', items: 3 }, '*')", "postmessage"),
        ("ipc-exposed", "agent/sock.py", "s.bind(OPEN_SOCK)", "unix"),
    ],
}


def _expected_lines(lang):
    out: dict = {}
    for finding, rel, needle, proto in EXPECTED[lang]:
        out.setdefault((finding, rel), set()).add(line_of(lang, rel, needle))
    return out


@pytest.mark.parametrize("lang", LANGS)
def test_every_detector_hits_exactly_its_positives(dbs, capsys, lang):
    _, res = run_json(capsys, dbs(lang))
    want = _expected_lines(lang)
    got = {(f["finding"], f["file"]) for f in res["findings"]
           if f["finding"] in ("tls-off", "ipc-exposed") or (f["finding"] == "plaintext" and f["protocol"] == "grpc")}
    assert got == set(want), (got ^ set(want))
    for (finding, rel), exp in want.items():
        assert lines(res, finding, rel) == exp, (finding, rel)


@pytest.mark.parametrize("lang", LANGS)
def test_finding_shape_protocol_confidence_and_entry_points(dbs, capsys, lang):
    _, res = run_json(capsys, dbs(lang))
    protos = {(f, r, p) for f, r, _n, p in EXPECTED[lang] if p}
    mine = [f for f in res["findings"] if f["finding"] in ("tls-off", "ipc-exposed")
            or (f["finding"] == "plaintext" and f["protocol"] == "grpc")]
    assert mine
    for f in mine:
        assert {"finding", "severity", "confidence", "protocol", "node", "file", "line", "entry_points", "entry_point_total",
                "detail", "fingerprint"} <= set(f)
        assert f["confidence"] in ("exact", "resolved", "heuristic") and f["protocol"]
        assert f["file"] and isinstance(f["line"], int) and (FIX / lang / f["file"]).is_file()
        assert f["severity"] == S.FINDINGS[f["finding"]]["severity"]
        assert isinstance(f["entry_points"], list) and f["entry_point_total"] >= sum(e["count"] for e in f["entry_points"])
        assert (f["finding"], f["file"], f["protocol"]) in protos or any(
            x[0] == f["finding"] and x[1] == f["file"] and x[3] is None for x in EXPECTED[lang]), f
    sev = {"tls-off": "high", "plaintext": "medium", "ipc-exposed": "medium"}
    assert all(f["severity"] == sev[f["finding"]] for f in mine)


def test_tls_findings_name_the_ssh_host_key(dbs, capsys):
    _, res = run_json(capsys, dbs("py"))
    ssh = [f for f in res["findings"] if f["finding"] == "tls-off" and f["protocol"] == "ssh"]
    assert len(ssh) == 4 and all("SSH host key" in f["detail"] for f in ssh)
    assert {f["file"] for f in ssh} == {"bookshop/catalog.py", "deploy/ssh_config", "ansible/ansible.cfg", "scripts/sync.sh"}
    ansible = next(f for f in ssh if f["file"] == "ansible/ansible.cfg")
    assert ansible["confidence"] == "heuristic"


def test_negative_twins_are_not_findings(dbs, capsys):
    _, py = run_json(capsys, dbs("py"))
    all_py = {f["line"] for f in py["findings"]} | {x for f in py["findings"] for x in f.get("also_at_lines", [])}
    for rel, needle in [("bookshop/catalog.py", "verify=True"), ("bookshop/catalog.py", "ctx.check_hostname = True"),
                        ("bookshop/catalog.py", "RejectPolicy"), ("bookshop/catalog.py", "grpc.secure_channel"),
                        ("bookshop/catalog.py", 'insecure_channel("localhost:50051")'),
                        ("bookshop/catalog.py", "unix:/run/bookshop/inventory.sock"),
                        ("bookshop/catalog.py", "unix:/run/bookshop/serve.sock")]:
        assert line_of("py", rel, needle) not in lines(py, "tls-off", rel) | lines(py, "plaintext", rel), needle
    assert not files(py, "tls-off") & {"ansible-pinned/ansible.cfg", "tests/checks.py"}
    assert line_of("py", "scripts/sync.sh", "in a comment") not in lines(py, "tls-off", "scripts/sync.sh")
    assert line_of("py", "scripts/sync.sh", "StrictHostKeyChecking=yes") not in lines(py, "tls-off", "scripts/sync.sh")
    assert line_of("py", "deploy/ssh_config", "StrictHostKeyChecking yes") not in lines(py, "tls-off", "deploy/ssh_config")
    assert all_py


def test_skipped_test_files_and_unix_loopback_are_counted(dbs):
    stats = {}
    for lang in ("py", "ts", "php", "kt", "ipc"):
        stats[lang] = json.loads(sqlite3.connect(dbs(lang)).execute("SELECT value FROM meta WHERE key='stats'").fetchone()[0])["insecure_transport"]
    assert stats["py"]["tests_skipped"] == 1 and stats["py"]["grpc_unix"] == 2 and stats["py"]["grpc_loopback"] == 1
    assert stats["ts"]["tests_skipped"] == 1 and stats["ts"]["grpc_unix"] == 1 and stats["ts"]["grpc_loopback"] == 1
    assert stats["php"]["tests_skipped"] == 1 and stats["kt"]["tests_skipped"] == 1
    assert stats["kt"]["grpc_loopback"] == 1
    assert stats["py"]["tls_verify_off"] == 5 and stats["py"]["ssh_hostkey_off"] == 5


def test_env_file_is_never_read(dbs, capsys):
    _, res = run_json(capsys, dbs("ts"))
    assert ".env" not in files(res, "tls-off")
    con = sqlite3.connect(dbs("ts"))
    assert not con.execute("SELECT 1 FROM nodes WHERE file='.env' OR id LIKE '%:.env'").fetchall()


def test_facts_are_stored_on_the_enclosing_function(dbs):
    con = sqlite3.connect(dbs("ts"))
    attrs = json.loads(con.execute("SELECT attrs FROM nodes WHERE id='function:src/partner.ts#pullPartnerFeed'").fetchone()[0])
    (fact,) = attrs["insecure_transport"]
    assert set(fact) >= {"kind", "line", "lib", "detail", "confidence"} and fact["kind"] == "tls-verify-off"
    assert fact["lib"] == "https" or fact["lib"] == "axios"
    assert fact["system"] == "external:https:feed.bookstore.example:443"
    ok = json.loads(con.execute("SELECT attrs FROM nodes WHERE id='function:src/partner.ts#pullPartnerFeedChecked'").fetchone()[0])
    assert "insecure_transport" not in ok
    mod = json.loads(sqlite3.connect(dbs("py")).execute("SELECT attrs FROM nodes WHERE id='function:bookshop.catalog.sync_catalog'").fetchone()[0])
    assert mod["insecure_transport"][0]["lib"] == "requests"


def test_external_and_api_calls_show_tls_verify_off(dbs, capsys):
    db = dbs("ts")
    con = sqlite3.connect(db)
    sysattrs = json.loads(con.execute("SELECT attrs FROM nodes WHERE id='external:https:feed.bookstore.example:443'").fetchone()[0])
    assert sysattrs["tls_verify"] is False
    rows = {r[0]: json.loads(r[1] or "{}") for r in con.execute(
        "SELECT src, attrs FROM edges WHERE kind='CONNECTS_TO' AND dst='external:https:feed.bookstore.example:443'")}
    assert rows["function:src/partner.ts#pullPartnerFeed"]["tls_verify"] is False
    assert "tls_verify" not in rows["function:src/partner.ts#pullPartnerFeedChecked"]
    out = cg(capsys, "external", "--db", db)[1]
    assert "tls verify off" in out
    users = [ln for ln in out.splitlines() if "pullPartnerFeed" in ln]
    assert len(users) == 2 and sum("[tls verify off]" in ln for ln in users) == 1
    assert "[tls verify off]" in next(ln for ln in users if ln.split("@")[0].strip().endswith("pullPartnerFeed"))
    api = cg(capsys, "api-calls", "all", "--db", db)[1]
    off = [ln for ln in api.splitlines() if "[tls verify off]" in ln]
    assert len(off) == 1 and "pullPartnerFeed " in off[0] + " "
    j = json.loads(cg(capsys, "api-calls", "all", "--db", db, "--json")[1])
    assert sorted(bool(c["tls_verify"] is False) for r in j for c in r["calls"]) == [False, True]
    assert "tls verify off" in cg(capsys, "external", "--db", dbs("ts"), "https")[1]


# ------------------------------------------------------------------ one finding per item
def test_each_item_appears_under_exactly_one_finding(dbs, capsys):
    _, res = run_json(capsys, dbs("ipc"), "--inbound")
    by_loc: dict = {}
    for f in res["findings"]:
        for ln in {f["line"], *f.get("also_at_lines", [])}:
            by_loc.setdefault((f["file"], ln), set()).add(f["finding"])
    assert by_loc and all(len(v) == 1 for v in by_loc.values()), {k: v for k, v in by_loc.items() if len(v) > 1}
    assert len({f["fingerprint"] for f in res["findings"]}) == len(res["findings"])
    # onMessageExternal and the message listener without an origin check are part 1 `unguarded` findings
    bg = line_of("ipc", "src/background_open.ts", "chrome.runtime.onMessageExternal.addListener")
    assert by_loc[("src/background_open.ts", bg)] == {"unguarded"}
    assert by_loc[("src/background_open.ts", line_of("ipc", "src/background_open.ts", "onConnectExternal"))] == {"ipc-exposed"}
    theme = line_of("ipc", "src/embed.ts", "window.addEventListener('message', (event: MessageEvent) => {", 2)
    assert by_loc[("src/embed.ts", theme)] == {"unguarded"}
    assert by_loc[("src/embed.ts", line_of("ipc", "src/embed.ts", "'*')"))] == {"ipc-exposed"}


def test_every_fixture_item_has_one_finding_across_all_fixtures(dbs, capsys):
    for lang in LANGS:
        _, res = run_json(capsys, dbs(lang))
        seen: dict = {}
        for f in res["findings"]:
            for ln in {f["line"], *f.get("also_at_lines", [])}:
                seen.setdefault((f["file"], ln, f["node"]), []).append(f["finding"])
        dup = {k: v for k, v in seen.items() if len(v) > 1}
        assert not dup, (lang, dup)


def test_negative_ipc_twins(dbs, capsys):
    _, res = run_json(capsys, dbs("ipc"))
    got = {f["file"] for f in res["findings"]}
    assert not got & {"extension-pinned/manifest.json", "src/background_pinned.ts"}
    assert "agent/sock.py" in got
    sock = [f for f in res["findings"] if f["file"] == "agent/sock.py"]
    assert len(sock) == 1 and "agent-open.sock" in sock[0]["node"] and sock[0]["protocol"] == "unix"
    assert "0o666" in sock[0]["detail"]
    main_ts = {f["line"] for f in res["findings"] if f["file"] == "src/main.ts"}
    assert main_ts == {line_of("ipc", "src/main.ts", k) for k in ("nodeIntegration: true", "contextIsolation: false", "webSecurity: false")}
    embed = lines(res, "ipc-exposed", "src/embed.ts")
    assert embed == {line_of("ipc", "src/embed.ts", "}, '*')")}
    # a sender with a concrete origin and the test-file sender are not findings
    assert line_of("ipc", "src/embed.ts", "'https://shop.bookstore.example')") not in embed
    assert "src/tests/embed.spec.ts" not in got


def test_unix_socket_mode_is_on_the_node(dbs):
    con = sqlite3.connect(dbs("ipc"))
    modes = {r[0]: json.loads(r[1])["mode"] for r in con.execute(
        "SELECT id, attrs FROM nodes WHERE id LIKE 'endpoint:unix:%' AND attrs LIKE '%mode%'")}
    assert modes == {"endpoint:unix:/run/bookshop/agent-open.sock": "0o666", "endpoint:unix:/run/bookshop/agent-private.sock": "0o600"}


@pytest.mark.parametrize("mode,expect", [("0o666", True), ("0o777", True), ("0666", True), ("777", True), ("o+w", True),
                                         ("a+rw", True), ("0o600", False), ("0o660", False), ("0o755", False),
                                         ("g+w", False), ("u+rwx", False), (None, False)])
def test_world_writable(mode, expect):
    assert S.world_writable(mode) is expect


# ------------------------------------------------------------------ registry
def test_second_producer_under_an_existing_finding_name_runs(tmp_path):
    assert len(S._PRODUCERS["plaintext"]) == 2 and all(isinstance(v, list) for v in S._PRODUCERS.values())
    st = GraphStore.create(tmp_path / "g.db")
    st.write([Node(id="external:ftp:files.bookstore.example:21", kind="external", name="x", attrs={
        "protocol": "ftp", "target": "files.bookstore.example:21", "host": "files.bookstore.example", "port": 21,
        "scheme": "ftp", "tls": False, "confidence": "exact", "address_at": "app/ftp.py:3"}),
              Node(id="function:app.sync", kind="function", name="sync", file="app/ftp.py", line=2),
              Node(id="function:app.rpc", kind="function", name="rpc", file="app/rpc.py", line=1, attrs={
                  "insecure_transport": [{"kind": "grpc-plaintext", "line": 2, "lib": "grpc", "confidence": "resolved",
                                          "detail": "client channel without TLS: inventory.bookstore.example:50051"}]})],
             [Edge("function:app.sync", "external:ftp:files.bookstore.example:21", "CONNECTS_TO", "app/ftp.py", 3)])
    res = S.surface(st)
    assert {(f["finding"], f["node"], f["protocol"]) for f in res["findings"]} == {
        ("plaintext", "external:ftp:files.bookstore.example:21", "ftp"), ("plaintext", "function:app.rpc", "grpc")}
    only = S.surface(st, protocol="grpc", finding="plaintext")
    assert [f["node"] for f in only["findings"]] == ["function:app.rpc"]


def test_fingerprints_keep_part_1_values_and_tell_variants_apart():
    assert S.fingerprint("hardcoded", "n", "f.py") == S.fingerprint("hardcoded", "n", "f.py", None)
    assert S.fingerprint("tls-off", "n", "f.py", "tls-verify-off") != S.fingerprint("tls-off", "n", "f.py", "ssh-hostkey-off")


# ------------------------------------------------------------------ gate, ignore, SARIF, MCP, help
def test_finding_filter_text_and_fail_on(dbs, capsys):
    db = dbs("py")
    rc, out, _ = cg(capsys, "surface", "--db", db, "--finding", "tls-off")
    assert rc == 0 and "== tls-off (8) - TLS or SSH host verification disabled ==" in out and "SSH host key" in out
    assert "== plaintext" not in out
    rc, out, _ = cg(capsys, "surface", "--db", db, "--finding", "plaintext")
    assert "grpc" in out and "client channel without TLS: inventory.bookstore.example:50051" in out
    assert cg(capsys, "surface", "--db", db, "--fail-on", "tls-off")[0] == 1
    assert cg(capsys, "surface", "--db", db, "--fail-on", "ipc-exposed")[0] == 0
    assert cg(capsys, "surface", "--db", dbs("ipc"), "--fail-on", "ipc-exposed")[0] == 1
    rc, out, _ = cg(capsys, "surface", "--db", dbs("ipc"), "--finding", "ipc-exposed")
    assert "== ipc-exposed (" in out and "postMessage" in out and "externally_connectable" in out
    _, res = run_json(capsys, db, "--outbound")
    assert {"tls-off", "plaintext"} <= {f["finding"] for f in res["findings"]}
    _, res = run_json(capsys, dbs("ipc"), "--outbound")
    assert "ipc-exposed" not in {f["finding"] for f in res["findings"]}
    _, res = run_json(capsys, dbs("ipc"), "--inbound")
    assert "ipc-exposed" in {f["finding"] for f in res["findings"]}
    _, res = run_json(capsys, db, "--protocol", "ssh")
    assert {f["protocol"] for f in res["findings"]} == {"ssh"}
    _, res = run_json(capsys, db, "--min-confidence", "exact")
    assert not [f for f in res["findings"] if f["confidence"] != "exact"]


def test_surface_ignore_applies_to_the_new_findings(dbs, capsys, tmp_path):
    root = tmp_path / "py"
    shutil.copytree(FIX / "py", root)
    (root / ".cg.yaml").write_text("""surface:
  ignore:
    - {finding: tls-off, path: "deploy", reason: scratch host in the lab}
    - {finding: plaintext, path: "bookshop/catalog.py", reason: mesh-internal channel}
""")
    db = tmp_path / "py.db"
    index_project(root, db, "py")
    _, res = run_json(capsys, db, "--show-ignored")
    assert "deploy/ssh_config" not in files(res, "tls-off") and "bookshop/catalog.py" in files(res, "tls-off")
    assert {f["file"] for f in res["ignored"]} == {"deploy/ssh_config", "bookshop/catalog.py"}
    assert not [f for f in res["findings"] if f["finding"] == "plaintext"]
    assert {f["ignored_by"]["reason"] for f in res["ignored"]} == {"scratch host in the lab", "mesh-internal channel"}
    assert cg(capsys, "surface", "--db", db, "--fail-on", "tls-off")[0] == 1       # the rest still fails the gate


def test_sarif_rules_and_schema(dbs, capsys):
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema is not installed: SARIF schema validation skipped")
    schema = json.loads((ROOT / "tests" / "sarif-schema-2.1.0.json").read_text())
    validator = jsonschema.Draft4Validator(schema)
    for lang in ("py", "ts", "ipc"):
        sarif = json.loads(cg(capsys, "surface", "--db", dbs(lang), "--format", "sarif", "--show-ignored")[1])
        assert [e.message for e in validator.iter_errors(sarif)] == []
        rules = {r["id"]: r for r in sarif["runs"][0]["tool"]["driver"]["rules"]}
        assert {"cg.surface.tls-off", "cg.surface.ipc-exposed"} <= set(rules)
        assert rules["cg.surface.tls-off"]["defaultConfiguration"]["level"] == "error"
        assert rules["cg.surface.ipc-exposed"]["defaultConfiguration"]["level"] == "warning"
        assert rules["cg.surface.tls-off"]["properties"]["tags"] == ["security", "outbound"]
        assert rules["cg.surface.ipc-exposed"]["properties"]["tags"] == ["security", "inbound"]
        results = sarif["runs"][0]["results"]
        names = [r["ruleId"] for r in results]
        assert all(rules[r["ruleId"]] is rules[sarif["runs"][0]["tool"]["driver"]["rules"][r["ruleIndex"]]["id"]] for r in results)
        want = {"py": "cg.surface.tls-off", "ts": "cg.surface.tls-off", "ipc": "cg.surface.ipc-exposed"}[lang]
        assert want in names
        for r in results:
            loc = r["locations"][0]["physicalLocation"]
            assert not loc["artifactLocation"]["uri"].startswith("/") and loc["region"]["startLine"] >= 1
            assert r["partialFingerprints"]["cg/surface/v1"]


def test_finding_names_in_help_and_mcp_text_come_from_the_table(capsys, monkeypatch):
    with pytest.raises(SystemExit):
        main(["surface", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    from cg_code_graph import mcp_server
    doc = " ".join((mcp_server.attack_surface.__doc__ or str(getattr(mcp_server.attack_surface, "description", ""))).split())
    for name in S.FINDINGS:
        assert name in help_text, name
        assert name in doc, name


def test_mcp_attack_surface_filters(dbs, monkeypatch):
    from cg_code_graph import mcp_server
    monkeypatch.setattr(mcp_server, "_st", lambda: GraphStore(dbs("py")))
    s = mcp_server.attack_surface.structured(finding="tls-off")
    assert {f["finding"] for f in s["surface"]["findings"]} == {"tls-off"} and "SSH host key" in s["result"]
    s = mcp_server.attack_surface.structured(finding="plaintext", protocol="grpc")
    assert {f["protocol"] for f in s["surface"]["findings"]} == {"grpc"}
    monkeypatch.setattr(mcp_server, "_st", lambda: GraphStore(dbs("ipc")))
    s = mcp_server.attack_surface.structured(inbound=True, finding="ipc-exposed")
    assert len(s["surface"]["findings"]) == 8 and "ipc-exposed" in s["result"]


# ------------------------------------------------------------------ no secrets, hermetic fixtures
def test_new_fixtures_hold_no_real_format_secrets_and_outputs_stay_clean(dbs, capsys):
    bad = re.compile(r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bSK[0-9a-f]{32}\b|sk_live_|SURF-")
    for p in FIX.rglob("*"):
        if p.is_file():
            assert not bad.search(p.read_text()), p
    for lang in LANGS:
        for fmt in ("text", "json", "sarif"):
            out = cg(capsys, "surface", "--db", dbs(lang), "--format", fmt, "--inbound", "--outbound", "--show-ignored")[1]
            assert "SURF-" not in out and "ZZSECRET" not in out
    assert not [t for lang in LANGS for t, v in _db_text(dbs(lang)) if "SURF-" in v]


def _db_text(path):
    con = sqlite3.connect(path)
    out = []
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        for row in con.execute(f'SELECT * FROM "{table}"'):
            out += [(table, v) for v in row if isinstance(v, str)]
    return out


def test_fixtures_are_hermetic():
    bad = [p for p in FIX.rglob("*") if p.is_dir() and p.name in ("node_modules", "vendor", "__pycache__", ".venv", "target")]
    bad += list(FIX.rglob("Cargo.lock")) + list(FIX.rglob("package-lock.json")) + list(FIX.rglob("composer.lock"))
    assert not bad, f"fixtures must not carry dependency directories or lock files: {bad}"
    assert (FIX / "ts" / "types" / "axios.d.ts").is_file()          # the TS extractor's stubs are committed, nothing is fetched
    assert not list(FIX.rglob(".env"))


# ------------------------------------------------------------------ detector units (languages without a fixture)
@pytest.mark.parametrize("group,text,kind", [
    ("java", "class T implements X509TrustManager {\n  public void checkServerTrusted(X509Certificate[] c, String a) throws CertificateException {\n  }\n}", "tls-verify-off"),
    ("java", "class V implements HostnameVerifier {\n  public boolean verify(String h, SSLSession s) { return true; }\n}", "tls-verify-off"),
    ("java", 'session.setConfig("StrictHostKeyChecking", "no");', "ssh-hostkey-off"),
    ("java", 'ManagedChannelBuilder.forAddress("inventory.bookstore.example", 50051).usePlaintext().build();', "grpc-plaintext"),
    ("dart", "client.badCertificateCallback = (X509Certificate cert, String host, int port) => true;", "tls-verify-off"),
    ("dart", "ClientChannel('inventory.bookstore.example', port: 50051, options: const ChannelOptions(credentials: ChannelCredentials.insecure()));", "grpc-plaintext"),
])
def test_detectors_without_a_fixture_positive(group, text, kind):
    found = IT.scan_text(text, "x", group)
    assert [f["kind"] for f in found] == [kind], found


@pytest.mark.parametrize("group,text", [
    ("java", "class T implements X509TrustManager {\n  public void checkServerTrusted(X509Certificate[] c, String a) {\n    verifyChain(c);\n  }\n}"),
    ("java", "class V { public boolean verify(String h, SSLSession s) { return h.endsWith(\".bookstore.example\"); } }"),
    ("java", 'session.setConfig("StrictHostKeyChecking", "yes");'),
    ("java", 'ManagedChannelBuilder.forAddress("localhost", 50051).usePlaintext().build();'),
    ("dart", "client.badCertificateCallback = (X509Certificate cert, String host, int port) => host == 'api.bookstore.example';"),
    ("dart", "ClientChannel('localhost', port: 50051, options: const ChannelOptions(credentials: ChannelCredentials.insecure()));"),
    ("py", "# requests.get(url, verify=False)\nrequests.get(url, verify=True)"),
    ("js", "// rejectUnauthorized: false\nconst agent = { rejectUnauthorized: true }"),
    ("py", "from ssl import CERT_NONE, CERT_REQUIRED\nimport ssl.CERT_NONE"),
    ("py", 'def make(verify=True):\n    """Pass verify=True; setting client.verify = False later has no effect."""\n    return verify'),
    ("py", "DOCUMENTATION = r'''\n  opts:\n    - Adds C(-o StrictHostKeyChecking=no) to the ssh options.\n'''\nrequests.get(url, verify=True)"),
])
def test_detectors_without_a_fixture_negative(group, text):
    assert [f for f in IT.scan_text(text, "x", group) if f["kind"] != "grpc-local"] == []


@pytest.mark.parametrize("target,server,kind", [
    ('"unix:/run/x.sock"', False, "unix"), ('"unix:///run/x.sock"', True, "unix"), ('"localhost:50051"', False, "loopback"),
    ('"127.0.0.1:50051"', False, "loopback"), ('"[::1]:50051"', False, "loopback"), ('"0.0.0.0:50051"', False, "loopback"),
    ('"0.0.0.0:50051"', True, "remote"), ('"[::]:50051"', True, "remote"), ('"inventory.bookstore.example:50051"', False, "remote"),
    ('"dns:///inventory.bookstore.example:443"', False, "remote"), ("target", False, "unknown"), (None, False, "unknown"),
])
def test_grpc_target_classification(target, server, kind):
    assert IT.target_kind(target, server=server)[0] == kind


def test_manifest_wildcards():
    def hits(m):
        return IT.manifest_facts(json.dumps({"manifest_version": 3, "externally_connectable": m}, indent=1))
    assert len(hits({"matches": ["*://*/*"]})) == 1 and len(hits({"matches": ["<all_urls>"]})) == 1
    assert len(hits({"ids": ["*"]})) == 1 and len(hits({"matches": ["*://*/*"], "ids": ["*"]})) == 2
    assert hits({"matches": ["https://shop.bookstore.example/*"], "ids": ["abcdefghijklmnopabcdefghijklmnop"]}) == []
    assert IT.manifest_facts('{"manifest_version": 3}') == []


def test_config_group_never_reads_env_files():
    for name in (".env", ".env.local", ".env.production", "app.env", "config/.env"):
        assert IT._config_group(name) is None, name
    assert IT._config_group(".env.example") == "envex" and IT._config_group("Dockerfile.prod") == "docker"
    assert IT._config_group("docker-compose.yml") == "yaml" and IT._config_group("deploy/ssh_config") == "sshcfg"

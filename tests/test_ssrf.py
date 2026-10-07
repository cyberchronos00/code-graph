"""#47 part 2b: outbound URLs built from request input (SSRF candidates) and DNS lookups of input in `cg surface`
(finding `ssrf`), `url_from_input` facts on edges and functions, and the `url from input` marker of `cg api-calls` /
`cg external` (the #42 "URLs from input" criterion).

Fixtures: tests/ssrf_fixture/{ts-express, ts-nuxt-or-next, php-laravel, py-django, py-flask-or-fastapi}. Each holds a
host-controlled URL, a path-only input on a fixed host, an allow-listed host, a one-level helper, a DNS lookup of input
and a test-file call that must be skipped. Every test works on a copy in a temp dir without `node_modules` / `vendor`; the
TS extractor needs only the committed type stubs."""
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import mcp_server  # noqa: E402
from cg_code_graph import ssrf_input as SI  # noqa: E402
from cg_code_graph import surface as S  # noqa: E402
from cg_code_graph.cli import main  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "ssrf_fixture"
NAMES = ["ts-express", "ts-nuxt-or-next", "php-laravel", "py-django", "py-flask-or-fastapi"]
NEEDS = {"ts-express": "node", "ts-nuxt-or-next": "node", "php-laravel": "php"}

# (source, key, part, via, checked, kind) of every recorded fact; findings are the host / url, unchecked ones
EXPECTED = {
    "ts-express": [
        ("query", "url", "url", "direct", False, "ssrf"), ("param", "isbn", "path", "direct", False, "ssrf"),
        ("body", "callback", "url", "direct", True, "ssrf"), ("body", "feed", "url", "helper", False, "ssrf"),
        ("query", "host", "host", "direct", False, "dns-input"), ("query", "code", "query", "direct", False, "ssrf"),
        ("body", "link", "url", "direct", False, "ssrf")],
    "ts-nuxt-or-next": [
        ("query", "url", "url", "direct", False, "ssrf"), ("param", "isbn", "path", "direct", False, "ssrf"),
        ("body", "callback", "url", "direct", True, "ssrf"), ("body", "feed", "url", "helper", False, "ssrf"),
        ("query", "host", "host", "direct", False, "dns-input"), ("body", "link", "url", "direct", False, "ssrf")],
    "php-laravel": [
        ("query", "url", "url", "direct", False, "ssrf"), ("param", "isbn", "path", "direct", False, "ssrf"),
        ("body", "callback", "url", "direct", True, "ssrf"), ("body", "feed", "url", "helper", False, "ssrf"),
        ("query", "host", "host", "direct", False, "dns-input"), ("query", "code", "query", "direct", False, "ssrf"),
        ("body", "link", "url", "direct", False, "ssrf")],
    "py-django": [
        ("query", "url", "url", "direct", False, "ssrf"), ("param", "isbn", "path", "direct", False, "ssrf"),
        ("body", "callback", "url", "direct", True, "ssrf"), ("body", "feed", "url", "helper", False, "ssrf"),
        ("query", "host", "host", "direct", False, "dns-input"), ("query", "code", "query", "direct", False, "ssrf"),
        ("param", "link", "url", "direct", False, "ssrf")],
    "py-flask-or-fastapi": [
        ("query", "url", "url", "direct", False, "ssrf"), ("param", "isbn", "path", "direct", False, "ssrf"),
        ("body", "callback", "url", "direct", True, "ssrf"), ("body", "feed", "url", "helper", False, "ssrf"),
        ("query", "host", "host", "direct", False, "dns-input"), ("query", "code", "query", "direct", False, "ssrf"),
        ("body", "ref", "url", "direct", False, "ssrf"),
        ("query", "url", "url", "direct", False, "ssrf"), ("param", "isbn", "path", "direct", False, "ssrf")],
}
FINDINGS_OF = {n: sorted(t for t in EXPECTED[n] if not t[4] and (t[5] == "dns-input" or t[2] in ("host", "url"))) for n in NAMES}


def _need(name):
    if name in NEEDS and shutil.which(NEEDS[name]) is None:
        pytest.skip(f"{NEEDS[name]} is not installed")
    ext = ROOT / "cg_code_graph" / "plugins"
    if NEEDS.get(name) == "node" and not (ext / "ts" / "extractor" / "node_modules").exists():
        pytest.skip("run `npm ci` in cg_code_graph/plugins/ts/extractor")
    if NEEDS.get(name) == "php" and not (ext / "php" / "extractor" / "vendor").exists():
        pytest.skip("run `composer install` in cg_code_graph/plugins/php/extractor")


@pytest.fixture(scope="module")
def dbs(tmp_path_factory):
    d = tmp_path_factory.mktemp("ssrf")
    cache: dict = {}

    def get(name):
        _need(name)
        if name not in cache:
            root = d / name
            shutil.copytree(FIX / name, root, ignore=shutil.ignore_patterns("node_modules", "vendor"))
            cache[name] = (d / f"{name}.db", index_project(root, d / f"{name}.db", name), root)
        return cache[name][0]
    get.stats = lambda name: (get(name), cache[name][1])[1]
    get.root = lambda name: (get(name), cache[name][2])[1]
    return get


def cg(capsys, *args):
    rc = main([str(a) for a in args]) or 0
    out = capsys.readouterr()
    return rc, out.out, out.err


def surface_json(capsys, db, *args):
    rc, out, _ = cg(capsys, "surface", "--db", db, "--format", "json", *args)
    return rc, json.loads(out)


def recorded(db) -> list[dict]:
    """Every url_from_input fact in the graph: function-level facts and edge attrs, one per call site."""
    con = sqlite3.connect(db)
    out, seen = [], set()
    for nid, file, attrs in con.execute("SELECT id, file, attrs FROM nodes WHERE attrs LIKE '%url_from_input%'"):
        for f in json.loads(attrs).get("insecure_transport") or []:
            if f.get("url_from_input"):
                out.append({"node": nid, "file": file, "line": f["line"], "kind": f["kind"], "u": f["url_from_input"], "where": "function"})
    for src, kind, file, line, attrs in con.execute("SELECT src, kind, file, line, attrs FROM edges WHERE attrs LIKE '%url_from_input%'"):
        u = json.loads(attrs).get("url_from_input")
        if u and (src, file, line) not in seen:
            seen.add((src, file, line))
            out.append({"node": src, "file": file, "line": line, "kind": "ssrf", "u": u, "where": "edge"})
    return out


def tup(r):
    u = r["u"]
    return (u["source"], u["key"], u["part"], u["via"], u["checked"], r["kind"])


# ------------------------------------------------------------------ facts
@pytest.mark.parametrize("name", NAMES)
def test_facts_cover_every_case_and_nothing_else(dbs, name):
    facts = recorded(dbs(name))
    assert sorted(map(tup, facts)) == sorted(EXPECTED[name])
    assert not [f for f in facts if "test" in f["file"].lower().split("/")[-1] or f["file"].startswith("tests/")]
    assert dbs.stats(name)["ssrf_input"]["tests_skipped"] >= 1


@pytest.mark.parametrize("name", NAMES)
def test_fact_shape_keys_and_values(dbs, name):
    for f in recorded(dbs(name)):
        assert set(f["u"]) - {"caller"} == {"source", "key", "part", "via", "checked"}, f
        assert f["u"]["source"] in ("query", "body", "param", "header", "path", "cookie")
        assert f["u"]["part"] in ("host", "url", "path", "query")
        assert f["u"]["via"] in ("direct", "helper") and isinstance(f["u"]["checked"], bool)
        assert f["kind"] in ("ssrf", "dns-input")


def test_edge_facts_sit_on_http_calls_and_connects_to(dbs):
    facts = recorded(dbs("php-laravel"))
    edge = [f for f in facts if f["where"] == "edge"]
    assert {(f["u"]["source"], f["u"]["key"]) for f in edge} >= {("query", "url"), ("param", "isbn"), ("query", "code"), ("body", "callback")}
    con = sqlite3.connect(dbs("php-laravel"))
    kinds = {k for (k,) in con.execute("SELECT kind FROM edges WHERE attrs LIKE '%url_from_input%'")}
    assert kinds == {"HTTP_CALLS", "CONNECTS_TO"}
    # a call with no edge keeps the fact on its function, as an insecure_transport fact
    fn = [f for f in facts if f["where"] == "function"]
    assert {f["kind"] for f in fn} == {"dns-input", "ssrf"} and any("raw" in f["node"] for f in fn)


def test_helper_flow_is_recorded_with_the_caller(dbs):
    for name in NAMES:
        helper = [f for f in recorded(dbs(name)) if f["u"]["via"] == "helper"]
        assert len(helper) == 1 and (helper[0]["u"].get("caller") or helper[0]["where"] == "edge")


# ------------------------------------------------------------------ cg api-calls / cg external
def test_api_calls_and_external_show_the_marker(dbs, capsys):
    db = dbs("php-laravel")
    out = cg(capsys, "api-calls", "all", "--db", db)[1]
    assert "[url from input (query.url, url)]" in out
    assert "[url from input (param.isbn, path)]" in out
    assert "[url from input (body.callback, url; host checked)]" in out
    assert "[url from input (query.code, query)]" in out
    assert "[url from input (body.feed, url; via helper)]" in out
    ext = cg(capsys, "external", "--db", db)[1]
    assert "[url from input (param.isbn, path)]" in ext and "[url from input (query.code, query)]" in ext
    calls = json.loads(cg(capsys, "api-calls", "all", "--db", db, "--json")[1])
    flat = json.dumps(calls)
    assert '"url_from_input"' in flat
    sysj = json.loads(cg(capsys, "external", "--db", db, "--json")[1])
    assert '"url_from_input"' in json.dumps(sysj)


def test_marker_on_ts_express(dbs, capsys):
    out = cg(capsys, "external", "--db", dbs("ts-express"))[1]
    assert "url from input (param.isbn, path)" in out and "url from input (query.code, query)" in out
    assert "url from input (query.url" not in out      # no resolved external system for the unresolved URL


# ------------------------------------------------------------------ findings
@pytest.mark.parametrize("name", NAMES)
def test_ssrf_findings_with_entry_points(dbs, capsys, name):
    rc, res = surface_json(capsys, dbs(name), "--finding", "ssrf")
    fs = res["findings"]
    assert {f["finding"] for f in fs} == {"ssrf"}
    assert len(fs) == len(FINDINGS_OF[name])
    for f in fs:
        if not f["file"].startswith("server/"):         # Nuxt server/ handlers are not modelled as routes yet
            assert f["entry_points"] and f["entry_points"][0]["kind"] == "http_route", f
        dns = f["protocol"] == "dns"
        assert f["severity"] == ("medium" if dns else "high")
        assert f["confidence"] in ("resolved", "heuristic")
        assert ("DNS lookup of request input" in f["detail"]) == dns
    helper = [f for f in fs if "via helper" in f["detail"]]
    assert len(helper) == 1 and helper[0]["confidence"] == "heuristic"
    assert all(f["confidence"] == "resolved" for f in fs if f not in helper)
    # path-only and allow-listed calls are recorded but never reported
    assert not [f for f in fs if "isbn" in f["detail"] or "code" in f["detail"] or "callback" in f["detail"]]
    assert sum(1 for f in fs if f["protocol"] == "dns") == 1


@pytest.mark.parametrize("name", NAMES)
def test_findings_name_the_input_and_never_a_value(dbs, capsys, name):
    out = cg(capsys, "surface", "--db", dbs(name), "--finding", "ssrf")[1]
    assert re.search(r"\((?:query|body|param)\.\w+\)", out)
    assert "bookstore.test" not in out


def test_fail_on_ssrf_and_finding_filter(dbs, capsys):
    db = dbs("py-django")
    assert cg(capsys, "surface", "--db", db, "--fail-on", "ssrf")[0] == 1
    assert cg(capsys, "surface", "--db", db, "--fail-on", "tls-off")[0] == 0
    _, res = surface_json(capsys, db, "--finding", "tls-off")
    assert res["findings"] == []
    _, all_res = surface_json(capsys, db)
    assert any(f["finding"] == "ssrf" for f in all_res["findings"])


def test_surface_ignore_applies_to_ssrf(capsys, tmp_path):
    root = tmp_path / "py"
    shutil.copytree(FIX / "py-django", root)
    (root / ".cg.yaml").write_text("""surface:
  ignore:
    - {finding: ssrf, path: "shop/feeds.py", reason: feed host is pinned by the gateway}
""")
    db = tmp_path / "py.db"
    index_project(root, db, "py")
    _, res = surface_json(capsys, db, "--show-ignored", "--finding", "ssrf")
    assert [f["file"] for f in res["ignored"]] == ["shop/feeds.py"]
    assert "shop/feeds.py" not in {f["file"] for f in res["findings"]} and len(res["findings"]) == 3


def test_sarif_rule_and_results(dbs, capsys):
    _, out, _ = cg(capsys, "surface", "--db", dbs("py-django"), "--format", "sarif", "--finding", "ssrf")
    sarif = json.loads(out)
    run = sarif["runs"][0]
    rules = [r["id"] for r in run["tool"]["driver"]["rules"]]
    assert "cg.surface.ssrf" in rules
    res = run["results"]
    assert len(res) == 4 and {r["ruleId"] for r in res} == {"cg.surface.ssrf"}
    assert sorted(r["level"] for r in res) == ["error", "error", "error", "warning"]
    assert all(r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"].startswith("shop/") for r in res)


def test_finding_table_help_and_mcp(dbs, capsys, monkeypatch):
    assert "ssrf" in S.FINDINGS and S.FINDINGS["ssrf"]["direction"] == "outbound" and S.FINDINGS["ssrf"]["severity"] == "high"
    assert "ssrf" in S.finding_names() and "ssrf" in (mcp_server.attack_surface.__doc__ or "")
    monkeypatch.setattr(mcp_server, "_st", lambda: GraphStore(dbs("py-django")))
    s = mcp_server.attack_surface.structured(outbound=True, finding="ssrf")
    assert len(s["surface"]["findings"]) == 4 and "ssrf" in s["result"]


# ------------------------------------------------------------------ secrets and hermetic fixtures
def _db_text(path):
    con = sqlite3.connect(path)
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        for row in con.execute(f'SELECT * FROM "{table}"'):
            yield from (v for v in row if isinstance(v, str))


def test_no_fake_secret_or_sample_value_reaches_the_index(dbs):
    secret = "fake-bookstore-django-key-0000"
    assert secret in (FIX / "py-django" / "shop" / "settings.py").read_text()
    for name in NAMES:
        blob = "\n".join(_db_text(dbs(name)))
        assert secret not in blob, name


def test_fixtures_are_hermetic_and_fake():
    bad = [p for p in FIX.rglob("*") if p.is_dir() and p.name in ("node_modules", "vendor", ".venv", "target")]
    bad += list(FIX.rglob("package-lock.json")) + list(FIX.rglob("composer.lock")) + list(FIX.rglob(".env"))
    assert not bad, bad
    assert (FIX / "ts-express" / "types" / "axios.d.ts").is_file() and (FIX / "ts-nuxt-or-next" / "types" / "axios.d.ts").is_file()
    real = re.compile(r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bSK[0-9a-f]{32}\b|sk_live_|sk_test_|ghp_[A-Za-z0-9]{20}")
    for p in FIX.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            try:
                text = p.read_text()
            except UnicodeDecodeError:
                continue
            assert not real.search(text), p


# ------------------------------------------------------------------ the analysis itself (no graph)
def _spans(lang, expr, **env):
    e = SI.Env(lang)
    for k, v in env.items():
        e.vars[k] = v
    return e, e.spans(expr)


@pytest.mark.parametrize("lang,expr,tainted,part", [
    ("js", "`https://${host}/x`", {"host": ("query", "h")}, "host"),
    ("js", "`https://api.bookstore.test/${id}`", {"id": ("param", "id")}, "path"),
    ("js", "'https://api.bookstore.test/s?q=' + q", {"q": ("query", "q")}, "query"),
    ("js", "target", {"target": ("query", "t")}, "url"),
    ("js", "new URL(target)", {"target": ("query", "t")}, "url"),
    ("js", "`${BASE}/books/${id}`", {"id": ("param", "id")}, "path"),
    ("py", 'f"https://{host}/x"', {"host": ("query", "h")}, "host"),
    ("py", 'f"https://api.bookstore.test/books/{isbn}"', {"isbn": ("param", "isbn")}, "path"),
    ("py", 'urljoin("https://api.bookstore.test/", ref)', {"ref": ("body", "ref")}, "url"),
    ("py", '"https://%s/x" % host', {"host": ("query", "h")}, "host"),
    ("py", '"https://api.bookstore.test/{}".format(isbn)', {"isbn": ("param", "isbn")}, "path"),
    ("php", '"https://$host/x"', {"host": ("query", "h")}, "host"),
    ("php", "'https://api.bookstore.test/q?x=' . $code", {"code": ("query", "code")}, "query"),
    ("php", "sprintf('https://api.bookstore.test/%s', $id)", {"id": ("param", "id")}, "path"),
    ("php", "$u", {"u": ("body", "u")}, "url"),
])
def test_url_part_unit(lang, expr, tainted, part):
    env = SI.Env(lang)
    env.vars.update(tainted)
    fmt = SI._format_parts(expr, env)
    if fmt:
        text, start = fmt
        sp = env.spans(text[start:])
        assert sp, expr
        assert SI.url_part(text, sp[0][0] + start, env) == part
    else:
        sp = env.spans(expr)
        assert sp, expr
        assert SI.url_part(expr, sp[0][0], env) == part


@pytest.mark.parametrize("lang,body,part", [
    ("js", "const endpoint = `${BASE()}/api/jobs/${id}`\nawait fetch(endpoint)\n", "path"),
    ("js", "const endpoint = `https://api.bookstore.test/jobs/${id}`\nawait fetch(endpoint)\n", "path"),
    ("js", "const endpoint = `https://api.bookstore.test/jobs?id=${id}`\nawait fetch(endpoint)\n", "query"),
    ("js", "const endpoint = `${id}/status`\nawait fetch(endpoint)\n", "url"),
    ("js", "const endpoint = id\nawait fetch(endpoint)\n", "url"),
    ("js", "await fetch(`${id}`)\n", "url"),
    ("js", "const { url } = id\nawait fetch(url)\n", "url"),
])
def test_part_survives_a_derived_variable(lang, body, part):
    env = SI.Env(lang)
    env.vars["id"] = ("arg", "0")
    SI.run_assignments(body, env, lang)
    sinks = SI.find_sinks(body, 1, lang, env, None, lambda ln: True)
    assert [s.part for s in sinks] == [part]
    assert all(str(sp[3]).isdigit() for s in sinks for sp in s.spans if sp[2] == "arg")


@pytest.mark.parametrize("rhs", ["\\realpath($base . '/' . $f)", "basename($f)", "(int) $f", "intval($f)", "parseInt(f, 10)", "Number(f)", "int(f)",
                                 "os.path.basename(f)"])
def test_path_and_number_normalisers_end_the_taint(rhs):
    assert SI._normalized(rhs)


@pytest.mark.parametrize("rhs", ["realpath($f) . '/x'", "trim($f)", "$f", "urldecode(basename($f)) . $g", "intval($f) + 'x' . $u"])
def test_other_expressions_stay_tainted(rhs):
    assert not SI._normalized(rhs)


@pytest.mark.parametrize("lang,text,expect", [
    ("js", "req.query.url", [("query", "url")]),
    ("js", "req.body['callback']", [("body", "callback")]),
    ("js", "req.params.id", [("param", "id")]),
    ("js", "req.headers['x-forward']", [("header", "x-forward")]),
    ("js", "getRouterParam(event, 'isbn')", [("param", "isbn")]),
    ("js", "c.req.query('url')", [("query", "url")]),
    ("js", "c.req.param('id')", [("param", "id")]),
    ("js", "request.nextUrl.searchParams.get('host')", [("query", "host")]),
    ("php", "$request->input('callback')", [("body", "callback")]),
    ("php", "$request->route('slug')", [("param", "slug")]),
    ("php", "$_GET['u']", [("query", "u")]),
    ("php", "request('u')", [("body", "u")]),
    ("py", "request.GET.get('url')", [("query", "url")]),
    ("py", "request.args['url']", [("query", "url")]),
    ("py", "request.query_params.get('url')", [("query", "url")]),
    ("py", "request.headers.get('X-Target')", [("header", "X-Target")]),
])
def test_source_patterns(lang, text, expect):
    sp = SI.Env(lang).spans(text)
    assert [(s[2], s[3]) for s in sp] == expect


def test_sinks_are_language_scoped():
    py = SI.SINKS["py"]
    assert any(rx.search("requests.get(u)") for _, _, rx, _ in py)
    assert not any(rx.search("request.session.get(u)") for _, _, rx, _ in py)
    assert any(rx.search("dns.lookup(h, cb)") for _, _, rx, _ in SI.SINKS["js"])
    assert any(rx.search("gethostbyname($h)") for _, _, rx, _ in SI.SINKS["php"])
    assert any(rx.search("curl_setopt($ch, CURLOPT_URL, $u)") for _, _, rx, _ in SI.SINKS["php"])


def test_allow_list_guard_detection():
    env = SI.Env("py")
    env.vars["url"] = ("query", "url")
    lines = ["host = urlparse(url).hostname", "if host not in ALLOWED_HOSTS:", "    abort(400)", "requests.get(url)"]
    SI.run_assignments("\n".join(lines), env, "py")
    assert SI._guard_before(lines, 3, env, "py") is True
    assert SI._guard_before(["host = urlparse(url).hostname", "requests.get(url)"], 1, env, "py") is False


@pytest.mark.parametrize("lang,var,lines", [
    ("js", "target", ["if (!target.startsWith('https://covers.bookstore.test/')) {", "  return res.sendStatus(400)", "}", "await axios.get(target)"]),
    ("py", "url", ["if not url.startswith('https://covers.bookstore.test/'):", "    abort(400)", "requests.get(url)"]),
    ("php", "url", ["if (! str_starts_with($url, 'https://covers.bookstore.test/')) {", "    abort(400);", "}", "Http::get($url);"]),
])
def test_prefix_checks_count_as_allow_lists(lang, var, lines):
    env = SI.Env(lang)
    env.vars[var] = ("query", "url")
    assert SI._guard_before(lines, len(lines) - 1, env, lang) is True


@pytest.mark.parametrize("lang,var,line", [
    ("js", "target", "const label = target.startsWith('https://covers.bookstore.test/') ? 'cover' : 'other'"),
    ("js", "target", "if (target.startsWith('/')) { log(target) }"),
    ("py", "url", "x = url.startswith('ftp')"),
])
def test_prefix_text_without_a_host_check_is_not_a_guard(lang, var, line):
    env = SI.Env(lang)
    env.vars[var] = ("query", "url")
    assert SI._guard_before([line, "call(x)"], 1, env, lang) is False


@pytest.mark.parametrize("prefix", ["'abc\\", '"https://x.test/\\', "`a\\"])
def test_literal_prefix_ends_on_a_trailing_backslash(prefix):
    assert isinstance(SI._literal_prefix(prefix), str)


def test_packaged_with_the_wheel():
    assert (ROOT / "cg_code_graph" / "ssrf_input.py").is_file()

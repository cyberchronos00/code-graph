"""TS outbound HTTP through node:http / node:https, undici and got (#188): client endpoints, external hosts and the
ts_unrecognised_http_client blind spot."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import coverage as C  # noqa: E402
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.external import external  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "ts_fixtures" / "node-http-clients"


def _index(tmp_path, src, name):
    db = tmp_path / f"{name}.db"
    index_project(src, db, name)
    return GraphStore(db)


def _endpoints(st):
    return {r["name"]: json.loads(r["attrs"] or "{}") for r in st.q("SELECT name, attrs FROM nodes WHERE kind='http'")}


def test_http_https_options_url_and_env(tmp_path):
    ep = _endpoints(_index(tmp_path, FX, "n"))
    assert "GET https://ledger.bookbank.example/v2/entries" in ep                 # module scheme, method defaults to GET
    assert "POST https://ledger.bookbank.example:8443/v2/entries" in ep          # protocol + host + port + method
    assert "GET http://status.bookbank.example/health" in ep                     # http.get, scheme from the module
    assert "GET https://rates.bookbank.example/v1/rates" in ep                   # URL string
    assert "DELETE https://accounts.bookbank.example/v1/accounts/{id}" in ep     # new URL(path, base) + method option
    env = ep["GET /v1/balance"]                                                  # hostname from process.env.X
    assert env["origin"] == "{env.BANK_HOST}" and env["origin_kind"] == "env"
    assert ep["GET http://status.bookbank.example/health"]["origin_kind"] == "other"


def test_undici_and_pool(tmp_path):
    ep = _endpoints(_index(tmp_path, FX, "n"))
    assert "POST https://settle.bookbank.example/v1/settlements" in ep           # undici request(url, { method })
    assert "GET https://settle.bookbank.example/v1/settlements" in ep            # undici.request(url) namespace call
    assert "GET https://pool.bookbank.example/v1/batches" in ep                  # new Client(origin).request({ path, method })
    pool = ep["POST /v1/batches/close"]                                          # new Pool(process.env.X)
    assert pool["origin_kind"] == "env" and pool["origin"] == "{env.SETTLEMENT_URL}"


def test_got_instances_and_keys(tmp_path):
    st = _index(tmp_path, FX, "n")
    ep = _endpoints(st)
    assert "GET https://rates.bookbank.example/v1/quotes" in ep                  # got(url)
    assert "POST https://rates.bookbank.example/v1/quotes" in ep                 # got.post(url, { json })
    assert "GET https://cards.bookbank.example/v3/cards" in ep                   # got.extend({ prefixUrl }).get
    assert "POST https://cards.bookbank.example/v3/cards" in ep
    assert "DELETE https://cards.bookbank.example/v3/cards/{id}" in ep           # instance called directly with a method
    edges = [json.loads(r["attrs"] or "{}") for r in st.q(
        "SELECT attrs FROM edges WHERE kind='HTTP_CALLS' AND dst='http:POST https://rates.bookbank.example/v1/quotes'")]
    keys = edges[0]["body_keys"]["keys"] if isinstance(edges[0]["body_keys"], dict) else edges[0]["body_keys"]
    assert {"pair", "amount"} <= {k if isinstance(k, str) else k.get("key") for k in keys}


def test_const_map_hosts_are_heuristic_and_external(tmp_path):
    st = _index(tmp_path, FX, "n")
    for host in ("api.bookbank.example", "api.stage.bookbank.example"):
        n = f"POST https://{host}/v1/charges"
        assert n in _endpoints(st)
        confs = [r["confidence"] for r in st.q("SELECT confidence FROM edges WHERE kind='HTTP_CALLS' AND dst=?", (f"http:{n}",))]
        assert confs == ["heuristic"]
    res = external(st, protocol="https")
    sys_ = {s["id"]: s for s in res["systems"]}
    for host in ("api.bookbank.example", "api.stage.bookbank.example"):
        s = sys_[f"external:https:{host}:443"]
        assert s["attrs"]["confidence"] == "heuristic"
    assert "external:https:cards.bookbank.example:443" in sys_
    assert [s["id"] for s in external(st, protocol="http")["systems"]] == ["external:http:status.bookbank.example:80"]


def test_blind_spot_positive_and_negative(tmp_path):
    st = _index(tmp_path, FX, "n")
    bs = C.blind_spots(C.for_graph(st)[next(iter(C.for_graph(st)))])
    hit = [b for b in bs if b["kind"] == "ts_unrecognised_http_client"]
    assert len(hit) == 1
    assert hit[0]["category"] == "endpoint" and hit[0]["language"] == "typescript"
    assert hit[0]["samples"] == ["src/relay.ts:6"]                     # superagent / opaque got(url) / https.request(opts)
    note = C.endpoint_note(st)
    assert "src/relay.ts:6" in note
    # files whose calls made endpoints (ledger, bank, pool, gotclient) and a server-only file are not reported
    assert hit[0]["count"] == 1


def test_issue_example_bank_charge(tmp_path):
    """The `charge(env, body)` example of #188: one endpoint per HOSTS value, both heuristic, both external hosts."""
    st = _index(tmp_path, FX, "n")
    rows = [r for r in Q.api_calls(st, "all") if r["calls"][0]["caller"] == "function:src/bank.ts#charge"]
    assert sorted(r["endpoint"] for r in rows) == [
        "http:POST https://api.bookbank.example/v1/charges", "http:POST https://api.stage.bookbank.example/v1/charges"]
    out = Q.render_api_calls(rows)
    assert out.startswith("2 client endpoints (0 matched)")
    assert out.count("[heuristic]") == 2

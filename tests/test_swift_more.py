"""Swift heuristic precision and base URLs (#58): SDK initializers on types the project only extends are not
INSTANTIATES edges, initializer calls go to the overloads whose argument labels fit, unknown-receiver calls of
standard-library collection methods are not resolved to same-named project methods; `{baseURL}` in request URLs is
resolved from base-like constants, Info.plist keys and .xcconfig settings (one value: api origin; one per
configuration: env with the candidates)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.link import match_endpoint  # noqa: E402

FIX = ROOT / "tests" / "swift_more_fixture"
_S: dict = {}


def con():
    if "db" not in _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-swift-more-"))
        _S["stats"] = index_project(FIX, d / "g.db", "swift-more")
        _S["db"] = d / "g.db"
    return sqlite3.connect(_S["db"])


def edges(kind):
    return {(s, d, ln) for s, d, ln in con().execute("select src, dst, line from edges where kind = ?", (kind,))}


def test_sdk_initializers_on_extended_types():
    inst, calls = edges("INSTANTIATES"), edges("CALLS")
    # String(decoding:as:) and Data(base64Encoded:) are the SDK's: no edge to the extension-only String / Data nodes
    assert not any(ln == 29 for _s, _d, ln in inst | calls)
    assert not any(d == "class:Data" for _s, d, _l in inst)
    # String(order:) is the project's extension initializer
    assert ("function:build", "class:String", 30) in inst and ("function:build", "method:String.init", 30) in calls
    assert _S["stats"]["plugins"]["swift"]["calls_sdk_initializer"] >= 2


def test_initializer_labels():
    inst, calls = edges("INSTANTIATES"), edges("CALLS")
    assert ("function:build", "method:Box.init", 32) in calls        # init(width:height:) with the default left out
    assert ("function:build", "method:Box.init", 33) in calls        # init(_:)
    assert ("function:build", "class:Box", 34) in inst               # Box(depth:): no initializer fits
    assert ("function:build", "method:Box.init", 34) not in calls
    assert ("function:build", "method:Retrier.init", 35) in calls    # trailing closure into a closure typealias


def test_stdlib_method_not_resolved_by_unique_name():
    assert not any(d == "method:Queue.first" for _s, d, _l in edges("CALLS"))


def _http():
    return {i: json.loads(a) for i, a in con().execute("select id, attrs from nodes where kind = 'http'")}


def test_base_url_from_constant_and_plist():
    h = _http()
    a = h["http:GET /v2/orders"]          # Config.apiBaseURL.appendingPathComponent("orders")
    assert a["origin"] == "https://api.example.com" and a["origin_kind"] == "api"
    assert a["base"]["source"] == "Sources/App/Config.swift: apiBaseURL"
    t = h["http:POST /oauth/token"]       # Info.plist AUTH_URL = https://$(AUTH_HOST)/oauth, Shared.xcconfig
    assert t["origin"] == "https://auth.example.com" and t["origin_kind"] == "api"
    assert "AUTH_URL" in t["base"]["source"]
    s = h["http:GET /v2/status"]          # an absolute URL on a configured base's origin
    assert s["origin_kind"] == "api"


def test_base_url_per_configuration():
    p = _http()["http:GET /users/me"]     # Info.plist SERVER_URL = $(SERVER_URL), Debug / Release .xcconfig
    assert p["origin_kind"] == "env" and p["origin"] == "{serverURL}"
    assert [c["value"] for c in p["base_candidates"]] == ["http://localhost:8080", "https://shop.example.com"]
    routes = [{"id": "route:GET /users/me", "method": "GET", "uri": "/users/me", "uris": [("", "/users/me")]}]
    m = match_endpoint("GET", p["path"], routes, p["origin_kind"], p["origin"])
    assert m["matched"] and m["matched"][0]["confidence"] == "resolved"


def test_xcconfig_parsing(tmp_path):
    from cg_code_graph.plugins.swift.baseurl import _xcconfig
    (tmp_path / "base.xcconfig").write_text("HOST = a.example.com\nAPI = https:/$()/$(HOST)/v1 // comment\n")
    (tmp_path / "prod.xcconfig").write_text('#include "base.xcconfig"\nHOST = b.example.com\n')
    c = _xcconfig(tmp_path / "prod.xcconfig")
    assert c["HOST"] == "b.example.com" and c["API"] == "https://$(HOST)/v1"

"""Echo subscription wrappers (#193): a channel passed through a wrapper function is resolved at each call site, on
tests/echo_wrapper_fixture/{api,web} (a Laravel API linked to a Nuxt frontend)."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.coverage import render  # noqa: E402
from cg_code_graph.link import link  # noqa: E402
from cg_code_graph import realtime as RT  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from sample import EXTRACTOR_DEPS, needs_php  # noqa: E402

FIX = ROOT / "tests" / "echo_wrapper_fixture"
pytestmark = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
_S: dict = {}


def built() -> dict:
    if not _S:
        d = Path(tempfile.mkdtemp(prefix="codegraph-echo-wrapper-"))
        _S["web_stats"] = index_project(FIX / "web", d / "web.db", "bookstore-web")
        _S["web"] = d / "web.db"
        _S["db"] = sqlite3.connect(d / "web.db")
        _S["api"] = d / "api.db"
    return _S


def sub_edges():
    rows = built()["db"].execute(
        "SELECT src, dst, file, line, confidence, attrs FROM edges WHERE kind='SUBSCRIBES_CHANNEL' ORDER BY file, line")
    return [(s, d, f, ln, c, json.loads(a or "{}")) for s, d, f, ln, c, a in rows]


def test_wrapper_call_sites_become_subscriptions():
    edges = [e for e in sub_edges() if e[1] == "channel_sub:store.{storeId}.orders"]
    assert [(e[2], e[3]) for e in edges] == [("app/pages/orders.vue", 6), ("app/pages/orders.vue", 7)]
    for src, _dst, _f, _ln, conf, attrs in edges:
        assert src == "page:app/pages/orders.vue" and conf == "exact"
        assert attrs["visibility"] == "private" and attrs["events"] == [".OrderShipped"]


def test_via_helper_names_the_wrapper_and_the_echo_call():
    by_line = {e[3]: e[5] for e in sub_edges() if e[2] == "app/pages/orders.vue"}
    direct, nested = by_line[6]["via_helper"], by_line[7]["via_helper"]
    assert direct["fn"] == "function:app/composables/useRealtime.ts#privateChannel"
    assert nested["fn"] == "function:app/composables/useRealtime.ts#ordersChannel"
    assert direct["at"] == nested["at"] == "app/composables/useRealtime.ts:6"


def test_direct_call_unchanged_and_no_placeholder_nodes():
    db = built()["db"]
    direct = [e for e in sub_edges() if e[1] == "channel_sub:store.1.orders"]
    assert len(direct) == 1 and direct[0][2:4] == ("app/composables/useRealtime.ts", 18)
    assert "via_helper" not in direct[0][5] and direct[0][5]["events"] == [".OrderHeld"]
    names = {r[0] for r in db.execute("SELECT id FROM nodes WHERE kind='channel_sub'")}
    assert names == {"channel_sub:store.1.orders", "channel_sub:store.{storeId}.orders", "channel_sub:store.{storeId}.desk"}
    assert not any("dynamicName" in n or "{name}" in n for n in names)


def test_wrapper_called_with_the_callers_parameter_keeps_the_placeholder():
    desk = [e for e in sub_edges() if e[1] == "channel_sub:store.{storeId}.desk"]
    assert len(desk) == 1
    src, _dst, f, ln, conf, attrs = desk[0]
    assert (f, ln) == ("app/pages/desk.ts", 4) and src == "function:app/pages/desk.ts#openDesk"
    assert attrs["events"] == [".DeskUpdated"] and attrs["visibility"] == "private"
    assert attrs["via_helper"]["fn"] == "function:app/composables/useRealtime.ts#privateChannel"


def test_unresolved_wrappers_are_counted():
    stats = built()["web_stats"]["plugins"]["typescript"]
    # presenceChannel(runtimeName()) and privateChannel(dynamicName): call sites with no resolvable name
    assert stats["realtime_subscriptions_unresolved_wrapper"] == 2
    assert sorted(stats["realtime_subscriptions_unresolved_wrapper_at"]) == ["app/pages/desk.ts:6", "app/pages/orders.vue:10"]
    # privateChannel <- ordersChannel <- levelThree is deeper than two levels
    assert stats["realtime_subscriptions_wrapper_too_deep"] == 1
    assert "2 subscription wrapper calls whose channel does not resolve" in render({"web": built()["web_stats"]["coverage"]}, all_files=True)


@needs_php
def test_linked_channels_show_each_call_site_and_match_the_event():
    s = built()
    index_project(FIX / "api", s["api"], "bookstore-api")
    out = s["api"].parent / "combined.db"
    link(str(s["api"]), str(s["web"]), str(out), backend_name="bookstore-api", frontend_name="bookstore-web")
    res = RT.channels(GraphStore(out), "store.{storeId}.orders")
    chan = res["channels"][0]
    subs = {x["name"]: x for x in chan["subscribers"]}
    wrapped = subs["store.{storeId}.orders"]
    assert [a["at"] for a in wrapped["subscribed_in"]] == ["bookstore-web/app/pages/orders.vue:6", "bookstore-web/app/pages/orders.vue:7"]
    assert wrapped["listens_for"] == ["event:App\\Events\\OrderShipped"]
    assert "page:app/pages/orders.vue" in wrapped["pages"]
    assert subs["store.1.orders"]["events"] == [".OrderHeld"]

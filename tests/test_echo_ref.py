"""Echo / pusher-js clients held in a Vue ref, or on a store / composable field, are subscriptions."""
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
from cg_code_graph.core.store import GraphStore  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

FIX = ROOT / "tests" / "echo_ref_fixture"
pytestmark = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")


def _built():
    d = Path(tempfile.mkdtemp(prefix="codegraph-echo-ref-"))
    stats = index_project(FIX, d / "web.db", "echo-ref")
    return stats, sqlite3.connect(d / "web.db"), GraphStore(d / "web.db")


def test_ref_store_and_composable_subscribe():
    stats, db, _st = _built()
    subs = {}
    for nid, vis, ev in db.execute(
        "SELECT id, json_extract(attrs,'$.visibility'), json_extract(attrs,'$.events') FROM nodes WHERE kind='channel_sub'"
    ):
        subs[nid] = (vis, json.loads(ev))
    assert subs["channel_sub:App.Models.User.{userId}"] == ("private", [".OrderShipped"])
    assert subs["channel_sub:orders.held"] == ("private", ["Held"])
    assert subs["channel_sub:orders.desk"][0] == "private"
    assert set(subs["channel_sub:orders.desk"][1]) == {"DeskUpdated", "OrderShipped"}
    assert subs["channel_sub:orders.plain"] == ("private", ["Plain"])
    assert subs["channel_sub:shop.user.{userId}"] == ("private", [".CartUpdated"])
    assert subs["channel_sub:App.Models.User.1"][0] == "private"
    # .leave / .disconnect are not subscriptions; a non-literal argument is not an unresolved receiver
    assert "channel_sub:not-a.{userId}" not in subs
    ts = stats["plugins"]["typescript"]
    assert ts["realtime_subscriptions_unresolved_receiver"] == 2
    at = ts["realtime_subscriptions_unresolved_receiver_at"]
    assert any(a.startswith("app/pages/orders/index.vue:") for a in at)
    text = render({"echo-ref": stats["coverage"]})
    assert "2 .private(...) calls on an unknown receiver" in text and "orders/index.vue" in text

"""Android IPC (#38 part 3) over tests/android_ipc_fixture: explicit intents to in-repo components (also through
ComponentName(pkg, "class") and PendingIntent), implicit intent actions to manifest and IntentFilter receivers, and AIDL
interfaces between an app module's Stub implementation and a client library."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_kotlin")
from cg_code_graph.indexer import index_project  # noqa: E402

FX = ROOT / "tests" / "android_ipc_fixture"
APP = "com.example.app"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("aipc") / "a.db"
    st = index_project(FX, d, "android-ipc")
    return d, st


def _edges(db, kind):
    con = sqlite3.connect(db[0])
    return {(r[0], r[1]): (r[2], json.loads(r[3] or "{}")) for r in
            con.execute("select src, dst, confidence, attrs from edges where kind=?", (kind,))}


def test_explicit_intents(db):
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    sync = f"endpoint:intent:{APP}.SyncService"
    assert s[(f"method:{APP}.MainActivity.startSync", sync)][1]["via"] == "startService"
    assert (f"method:{APP}.RefreshReceiver.onReceive", sync) in s
    assert r[(sync, f"method:{APP}.SyncService.onStartCommand")][1]["component"] == "service"
    main = f"endpoint:intent:{APP}.MainActivity"
    assert s[(f"method:{APP}.SyncService.onStartCommand", main)][1]["via"] == "PendingIntent.getActivity"
    assert (main, f"method:{APP}.MainActivity.onCreate") in r
    assert s[(f"method:{APP}.MainActivity.reopen", main)][1]["via"] == "startActivity"   # not the comment / launch { }
    notify = f"endpoint:intent:{APP}.NotifyService"                  # consumer call opened on an earlier line
    assert s[(f"method:{APP}.SyncService.onStartCommand", notify)][1]["via"] == "PendingIntentCompat.getService"
    assert (notify, f"method:{APP}.NotifyService.onCreate") in r       # onBind { return null }: started only
    bridge = f"endpoint:intent:{APP}.BridgeService"                  # ComponentName(pkg, CONST) across modules
    assert s[("method:com.example.client.BridgeClient.connect", bridge)][0] == "resolved"
    assert s[("method:com.example.client.BridgeClient.connect", bridge)][1]["via"] == "bindService"
    assert (bridge, f"method:{APP}.BridgeService.onBind") in r


def test_actions(db):
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    refresh = f"endpoint:intent-action:{APP}.ACTION_REFRESH"
    assert s[(f"method:{APP}.MainActivity.refresh", refresh)][1]["via"] == "sendBroadcast"
    assert r[(refresh, f"method:{APP}.RefreshReceiver.onReceive")][1]["how"].startswith("AndroidManifest <receiver>")
    con = sqlite3.connect(db[0])
    assert con.execute("select file, line from edges where kind='RECEIVED_BY' and src=?", (refresh,)).fetchall() == [
        ("app/src/main/AndroidManifest.xml", 15)]                          # the <action> line in the manifest
    live = f"endpoint:intent-action:{APP}.ACTION_LIVE"
    assert (f"method:{APP}.LiveUpdates.publish", live) in s                 # Intent().apply { action = X }
    assert (live, f"method:{APP}.LiveUpdates.onReceive") in r              # registerReceiver(r, IntentFilter(X))
    assert not any("android.intent" in k[0] or "android.intent" in k[1] or "ACTION_SEND" in k[1] for k in list(s) + list(r))


def test_assertions_are_not_sends(db):
    t = _edges(db, "TEST_CALLS")                                         # ComponentName(..) compared in assertEquals
    assert not any(k[0].startswith("method:com.example.client.BridgeClientTest.componentIsExplicit") for k in t)
    assert not any(k[0].endswith("BridgeClientTest.registerFake") for k in t)   # a ComponentName never sent


def test_exposure(db):
    r = _edges(db, "RECEIVED_BY")
    refresh = r[(f"endpoint:intent-action:{APP}.ACTION_REFRESH", f"method:{APP}.RefreshReceiver.onReceive")][1]
    assert refresh["exported"] is False and "permission" not in refresh
    ext = r[(f"endpoint:intent-action:{APP}.ACTION_EXTERNAL", f"method:{APP}.ExternalReceiver.onReceive")][1]
    assert ext["exported"] is True and "permission" not in ext          # intent filter, no android:exported: exported
    bridge = r[(f"endpoint:intent:{APP}.BridgeService", f"method:{APP}.BridgeService.onBind")][1]
    assert bridge["exported"] is True and bridge["permission"] == "com.example.app.permission.BIND_BRIDGE"
    sync = r[(f"endpoint:intent:{APP}.SyncService", f"method:{APP}.SyncService.onStartCommand")][1]
    assert sync["exported"] is False


def test_exposure_guards(db):
    from cg_code_graph.core.store import GraphStore
    from cg_code_graph.protocols.view import collect
    ep = collect(GraphStore(db[0]))
    ext = ep[f"endpoint:intent-action:{APP}.ACTION_EXTERNAL"]       # exported, no permission: any app can send it
    assert ext["guards"] == [] and "unguarded" in ext["checks"]
    refresh = ep[f"endpoint:intent-action:{APP}.ACTION_REFRESH"]
    assert refresh["guards"] == ["not exported"] and "unguarded" not in refresh["checks"]
    bridge = ep[f"endpoint:intent:{APP}.BridgeService"]
    assert bridge["guards"] == ["permission com.example.app.permission.BIND_BRIDGE"] and "unguarded" not in bridge["checks"]
    main = ep[f"endpoint:intent:{APP}.MainActivity"]                  # MAIN / LAUNCHER: public by design
    assert main["guards"] is None and "unguarded" not in main["checks"]


def test_aidl(db):
    s, r = _edges(db, "SENDS_TO"), _edges(db, "RECEIVED_BY")
    b = "endpoint:aidl:com.example.bridge.IBridge"
    for m in ("ping", "register", "sync"):
        assert (f"{b}.{m}", f"method:{APP}.BridgeService.{m}") in r
    assert s[("method:com.example.client.BridgeClient.onServiceConnected", f"{b}.ping")][0] == "resolved"
    assert ("method:com.example.client.BridgeClient.onServiceConnected", f"{b}.register") in s
    assert ("method:com.example.client.BridgeClient.requestSync", f"{b}.sync") in s                  # bridge?.sync(..)
    cb = "endpoint:aidl:com.example.bridge.IBridgeCallback.onSynced"
    assert (cb, "method:com.example.client.BridgeClient.onSynced") in r
    assert (f"method:{APP}.BridgeService.notifySynced", cb) in s           # RemoteCallbackList item call
    assert not any(k[0] == f"method:{APP}.BridgeService.sync" for k in s)   # no sends from inside its own Stub


def test_aidl_mocks(db):
    t = [(k, v) for k, v in _edges(db, "TEST_CALLS").items() if k[1] == "endpoint:aidl:com.example.bridge.IBridge.ping"]
    con = sqlite3.connect(db[0])
    lines = [r[0] for r in con.execute("select line from edges where kind='TEST_CALLS' and dst=?",
                                       ("endpoint:aidl:com.example.bridge.IBridge.ping",))]
    assert len(t) == 1 and lines == [18]                                    # the real call, not every { } / verify { }


def test_stats(db):
    st = db[1]["android_ipc"]
    assert st["aidl_interfaces"] == 2 and st["aidl_methods"] == 4 and st["aidl_calls"] == 5
    assert st["intent_senders"] == 6 and st["action_senders"] == 2 and st["action_receivers"] == 3

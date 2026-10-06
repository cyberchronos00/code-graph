"""`cg parity` (#85): symbols of one graph with no counterpart in another (a Swift app and its Kotlin port)."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
pytest.importorskip("tree_sitter_kotlin")
from cg_code_graph import parity as P  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

IOS = {
 "App/Cart/CartView.swift": "import SwiftUI\n\nstruct CartView: View {\n    var body: some View { Text(\"cart\") }\n}\n",
 "App/Orders/OrderService.swift": "final class OrderService {\n    func place() {}\n    func cancel() {}\n}\n\n"
                                  "enum OrderStatus {\n    case active\n    case pausedByUser\n    case archived\n}\n",
 "App/Account/AccountVC.swift": "import UIKit\n\n/// Android: ProfileFragment\nfinal class AccountVC: UIViewController {\n"
                                "    override func viewDidLoad() { super.viewDidLoad() }\n}\n",
 "App/Orders/OrderAction.swift": "enum OrderAction {\n    case deletePressed\n}\n\nstruct SettingsState {\n    var isOn = false\n}\n",
 "App/Only/OnlyOnIOS.swift": "struct WidgetTimeline {\n    func entries() -> Int { 1 }\n}\n",
 "App/Broken/Broken.swift": "struct HalfParsed {\n    func ok() {}\n    func bad( { \n}\n",
}
ANDROID = {
 "app/src/main/java/com/shop/cart/CartScreen.kt": "package com.shop.cart\n\nimport androidx.compose.runtime.Composable\n\n"
                                                    "@Composable\nfun CartScreen() {}\n",
 "app/src/main/java/com/shop/orders/OrderService.kt": "package com.shop.orders\n\nclass OrderService {\n    fun place() {}\n"
                                                      "    companion object { const val LIMIT = 3 }\n}\n\n"
                                                      "enum class OrderStatus { ACTIVE, PAUSED_BY_USER }\n",
 "app/src/main/java/com/shop/orders/OrderAction.kt": "package com.shop.orders\n\nsealed class OrderAction {\n"
                                                     "    data object DeleteClick : OrderAction()\n}\n\nclass VaultSettingsState\n",
 "app/src/main/java/com/shop/account/ProfileFragment.kt": "package com.shop.account\n\nclass ProfileFragment\n",
}


def build(files, name):
    tmp = Path(tempfile.mkdtemp(prefix="codegraph-parity-"))
    for rel, body in files.items():
        p = tmp / name / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    index_project(tmp / name, tmp / f"{name}.db", name)
    return str(tmp / f"{name}.db")


@pytest.fixture(scope="module")
def dbs():
    return build(IOS, "ios"), build(ANDROID, "android")


def by_symbol(res):
    return {r["symbol"]: r for r in res["matched"]}


def test_ios_to_android(dbs):
    res = P.parity(*dbs)
    m = by_symbol(res)
    assert m["CartView"]["confidence"] == "normalized" and m["CartView"]["target"].endswith("CartScreen")
    assert m["OrderService"]["confidence"] == "exact" and m["OrderService.place"]["confidence"] == "exact"
    assert m["OrderStatus.pausedByUser"]["confidence"] == "normalized"
    assert m["AccountVC"]["confidence"] == "explicit" and m["AccountVC"]["target"].endswith("ProfileFragment")
    missing = {r["symbol"] for r in res["missing"]}
    assert {"OrderService.cancel", "OrderStatus.archived", "WidgetTimeline"} <= missing
    assert "AccountVC.viewDidLoad" not in missing and res["skipped"] >= 1      # lifecycle override
    unknown = {r["symbol"] for r in res["unknown"]}
    assert "HalfParsed" in unknown and "HalfParsed" not in missing


def test_android_to_ios_and_cli(dbs, tmp_path):
    res = P.parity(dbs[1], dbs[0])
    assert not [r for r in res["missing"] if "Companion" in r["symbol"]]
    assert "com.shop.orders.OrderService.LIMIT" in {r["symbol"] for r in res["missing"]}
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"WidgetTimeline": "OrderService"}))
    out = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "parity", "--db", dbs[0], "--against", dbs[1],
                          "--map", str(mp), "--json"], capture_output=True, text=True, cwd=ROOT).stdout
    r = json.loads(out)
    assert by_symbol(r)["WidgetTimeline"]["confidence"] == "explicit"
    txt = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "parity", "--db", dbs[0], "--against", dbs[1]],
                         capture_output=True, text=True, cwd=ROOT).stdout
    assert "== MISSING in target" in txt and "unknown (syntax errors)" in txt


def test_member_verbs_and_prefixes(dbs):
    m = by_symbol(P.parity(*dbs))
    assert m["OrderAction.deletePressed"]["target"].endswith("OrderAction.DeleteClick")   # sealed-class action
    assert "SettingsState" not in m
    m = by_symbol(P.parity(*dbs, strip_prefixes=["Vault"]))
    assert m["SettingsState"]["target"].endswith("VaultSettingsState")
    assert P.norm_member("getDefaultUriMatchType") == P.norm_member("defaultUriMatchType")
    assert P._near(["item", "list", "state"], ["item", "listing", "state"])
    assert not P._near(["login", "totp", "state"], ["login", "state"])


def _retag(db, tag_of, targets):
    """Set `attrs.platforms` per node (tag_of(name) -> list | None) and the graph's platform targets."""
    import shutil
    import sqlite3
    out = db.replace(".db", f"-{'-'.join(targets)}.db")
    shutil.copy(db, out)
    c = sqlite3.connect(out)
    for nid, name, attrs in c.execute("SELECT id, name, attrs FROM nodes").fetchall():
        a = json.loads(attrs or "{}")
        tags = tag_of(name or "")
        if tags is not None:
            a["platforms"] = tags
            c.execute("UPDATE nodes SET attrs=? WHERE id=?", (json.dumps(a), nid))
    st = json.loads(dict(c.execute("SELECT key, value FROM meta"))["stats"])
    st["platforms"] = {"targets": targets}
    c.execute("UPDATE meta SET value=? WHERE key='stats'", (json.dumps(st),))
    c.commit()
    c.close()
    return out


def test_app_platform_is_not_platform_only(dbs):
    """#107: an iOS app whose symbols are all tagged `ios` (Xcode target membership) compared with an Android
    graph: only code for a side platform (`watchos`) is platform_only, the app itself is compared."""
    src = _retag(dbs[0], lambda n: ["watchos"] if n.startswith("WidgetTimeline") or n == "entries" else ["ios"],
                 ["ios", "watchos"])
    tgt = _retag(dbs[1], lambda n: None, ["android"])
    res = P.parity(src, tgt)
    assert {r["symbol"] for r in res["platform_only"]} == {"WidgetTimeline"}, res["platform_only"]
    missing = {r["symbol"] for r in res["missing"]}
    assert {"OrderService.cancel", "OrderStatus.archived"} <= missing, missing
    assert by_symbol(res)["OrderService"]["confidence"] == "exact"

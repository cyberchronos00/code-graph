"""`cg parity --structure` (#93): pairs whose names differ, matched by what they use, and learned rename rules."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
pytest.importorskip("tree_sitter_swift")
pytest.importorskip("tree_sitter_kotlin")
from test_parity import ROOT, build  # noqa: E402
from codegraph import parity as P  # noqa: E402

IOS = {
 "App/Auth/PreloginRequest.swift": 'struct PreloginRequest {\n    var path: String { "/accounts/prelogin" }\n'
                                   '    var headers: [String: String] { ["x-device-identifier": id] }\n    let id = ""\n}\n',
 "App/Vault/VaultSummaryView.swift": "import SwiftUI\n\nstruct VaultSummaryView: View {\n    var body: some View {\n"
                                     "        VStack {\n            Text(Localizations.vaultLocked)\n"
                                     "            Text(Localizations.unlockVaultToContinue)\n        }\n    }\n}\n",
 "App/Login/LoginCoordinator.swift": "/// Android: LoginNavigator\nfinal class LoginCoordinator {\n    func start() {}\n}\n",
 "App/Settings/SettingsCoordinator.swift": "/// Android: SettingsNavigator\nfinal class SettingsCoordinator {\n    func start() {}\n}\n",
 "App/Profile/ProfileCoordinator.swift": "final class ProfileCoordinator {\n    func start() {}\n}\n",
 "App/Only/WidgetTimeline.swift": 'struct WidgetTimeline {\n    func entries() -> String { "widget-kind-timeline" }\n}\n',
}
ANDROID = {
 "app/src/main/java/com/acme/network/IdentityApi.kt": "package com.acme.network\n\ninterface IdentityApi {\n"
     '    @POST("accounts/prelogin")\n    suspend fun prelogin(@Header("x-device-identifier") id: String): String\n}\n',
 "app/src/main/java/com/acme/vault/LockedVaultBanner.kt": "package com.acme.vault\n\n"
     "import androidx.compose.runtime.Composable\n\n@Composable\nfun LockedVaultBanner() {\n"
     "    Text(stringResource(BitwardenString.vault_locked))\n    Text(stringResource(BitwardenString.unlock_vault_to_continue))\n}\n",
 "app/src/main/java/com/acme/login/LoginNavigator.kt": "package com.acme.login\n\nclass LoginNavigator {\n    fun start() {}\n}\n",
 "app/src/main/java/com/acme/settings/SettingsNavigator.kt": "package com.acme.settings\n\nclass SettingsNavigator {\n    fun start() {}\n}\n",
 "app/src/main/java/com/acme/profile/ProfileNavigator.kt": "package com.acme.profile\n\nclass ProfileNavigator {\n    fun start() {}\n}\n",
 "app/src/main/java/com/acme/other/Unrelated.kt": 'package com.acme.other\n\nclass Unrelated {\n    fun x() = "something-else"\n}\n',
}


@pytest.fixture(scope="module")
def dbs():
    return build(IOS, "sios"), build(ANDROID, "sandroid")


def test_off_by_default_is_name_matching_only(dbs):
    res = P.parity(*dbs)
    assert not [r for r in res["matched"] if r["confidence"] in ("structure", "learned")]
    assert "inferred" not in res["summary"] and "learned_rules" not in res
    missing = {r["symbol"] for r in res["missing"]}
    assert {"PreloginRequest", "VaultSummaryView", "ProfileCoordinator", "WidgetTimeline"} <= missing, missing


def test_structure_endpoint_l10n_learned_and_true_missing(dbs):
    res = P.parity(*dbs, structure=True)
    m = {r["symbol"]: r for r in res["matched"]}
    # shared endpoint path + header name: a request type and the Retrofit interface that sends it
    assert m["PreloginRequest"]["target"] == "com.acme.network.IdentityApi", m.get("PreloginRequest")
    assert m["PreloginRequest"]["confidence"] == "structure"
    assert "str:accounts/prelogin" in m["PreloginRequest"]["evidence"]
    # shared localization keys (`Localizations.vaultLocked` / `BitwardenString.vault_locked`): view -> @Composable
    assert m["VaultSummaryView"]["target"] == "com.acme.vault.LockedVaultBanner", m.get("VaultSummaryView")
    assert "l10n:vaultlocked" in m["VaultSummaryView"]["evidence"]
    # two explicit pairs teach Coordinator -> Navigator; the rule pairs a third
    assert {"at": "tail", "from": "coordinator", "to": "navigator", "support": 2} in res["learned_rules"]
    assert m["ProfileCoordinator"]["target"] == "com.acme.profile.ProfileNavigator"
    assert m["ProfileCoordinator"]["confidence"] == "learned"
    assert "ProfileCoordinator.start" in m           # members of the inferred pair are compared too
    # really missing: no forced match
    assert "WidgetTimeline" in {r["symbol"] for r in res["missing"]}
    assert res["summary"]["inferred"] == 3


def test_cli_structure_and_write_map(dbs, tmp_path):
    out = tmp_path / "map.json"
    r = subprocess.run([sys.executable, "-m", "codegraph.cli", "parity", "--db", dbs[0], "--against", dbs[1],
                        "--structure", "--write-map", str(out)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "== INFERRED matches (not by name): 3" in r.stdout and "== LEARNED rename rules" in r.stdout
    mp = json.loads(out.read_text())
    assert mp["ProfileCoordinator"] == "com.acme.profile.ProfileNavigator"
    # the written map, fed back, turns them into explicit matches
    res = P.parity(*dbs, mapping=mp)
    assert {r["symbol"]: r["confidence"] for r in res["matched"]}["PreloginRequest"] == "explicit"

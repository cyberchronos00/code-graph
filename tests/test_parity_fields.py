"""`cg parity --structure` part 2 (#93): types paired by their declared fields and wire names, views paired by the
image resources they use, generic shared keys not enough, and `--both-sides` (matched pairs plus what only the
target has). Invented app: a hiking app in Swift and its Kotlin port."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
pytest.importorskip("tree_sitter_swift")
pytest.importorskip("tree_sitter_kotlin")
from test_parity import ROOT, build  # noqa: E402
from cg_code_graph import parity as P  # noqa: E402
from cg_code_graph import parity_structure as PS  # noqa: E402

IOS = {
 "Trails/Models/TrailSummaryResponse.swift":
     "struct TrailSummaryResponse: Codable {\n    let trailId: String\n    let displayName: String\n"
     "    let elevationGain: Int\n    let loopKind: String\n\n    enum CodingKeys: String, CodingKey {\n"
     '        case trailId = "trail_id"\n        case displayName = "display_name"\n'
     '        case elevationGain = "elevation_gain"\n        case loopKind = "loop_kind"\n    }\n}\n',
 "Trails/Detail/HikeDetailState.swift":
     "struct HikeDetailState {\n    var title: String = \"\"\n    var isFavorite = false\n    var distanceText = \"\"\n"
     "    var difficulty = 0\n    var photoCount = 0\n}\n",
 "Trails/Badges/BadgeShelfView.swift":
     "import SwiftUI\n\nstruct BadgeShelfView: View {\n    var body: some View {\n        HStack {\n"
     '            Image("badge_summit")\n            Image("badge_sunrise")\n        }\n    }\n}\n',
 "Trails/Trip/HikeShareSheet.swift":
     "import SwiftUI\n\nstruct HikeShareSheet: View {\n    var body: some View {\n        HStack {\n"
     "            Text(Localizations.cancel)\n            Text(Localizations.yes)\n        }\n    }\n}\n",
 "Trails/Settings/SettingsStore.swift":
     "final class SettingsStore {\n    func reset() {}\n}\n",
 "Trails/Widgets/LiveActivityWidget.swift":
     'struct LiveActivityWidget {\n    func kind() -> String { "live-activity-hike" }\n}\n',
}
ANDROID = {
 "app/src/main/java/com/trailmate/data/TrailOverviewJson.kt":
     "package com.trailmate.data\n\n@Serializable\ndata class TrailOverviewJson(\n"
     '    @SerialName("trail_id") val id: String,\n    @SerialName("display_name") val name: String,\n'
     '    @SerialName("elevation_gain") val gain: Int,\n    @SerialName("loop_kind") val loop: String,\n)\n',
 "app/src/main/java/com/trailmate/detail/RouteDetailViewState.kt":
     "package com.trailmate.detail\n\ndata class RouteDetailViewState(\n    val title: String,\n"
     "    val isFavorite: Boolean,\n    val distanceText: String,\n    val difficulty: Int,\n    val elevationText: String,\n)\n",
 "app/src/main/java/com/trailmate/badges/AchievementRow.kt":
     "package com.trailmate.badges\n\nimport androidx.compose.runtime.Composable\n\n@Composable\nfun AchievementRow() {\n"
     "    Image(painterResource(R.drawable.badge_summit), null)\n    Image(painterResource(R.drawable.badge_sunrise), null)\n}\n",
 "app/src/main/java/com/trailmate/trip/HikeDeleteDialog.kt":
     "package com.trailmate.trip\n\nimport androidx.compose.runtime.Composable\n\n@Composable\nfun HikeDeleteDialog() {\n"
     "    Text(stringResource(R.string.cancel))\n    Text(stringResource(R.string.yes))\n}\n",
 # cancel / yes are on every prompt: generic keys
 **{f"app/src/main/java/com/trailmate/common/{n}Prompt.kt":
    f"package com.trailmate.common\n\nclass {n}Prompt {{\n    fun confirm{n}() {{\n        show(R.string.cancel)\n"
    f"        show(R.string.yes)\n    }}\n}}\n" for n in ("Exit", "Logout", "Unfollow", "Archive")},
 "app/src/main/java/com/trailmate/settings/SettingsStore.kt":
     "package com.trailmate.settings\n\nclass SettingsStore {\n    fun reset() {}\n    fun exportBackup() {}\n}\n",
 "app/src/main/java/com/trailmate/wear/WearTileService.kt":
     "package com.trailmate.wear\n\nclass WearTileService {\n    fun onTileRequest() {}\n}\n",
}


@pytest.fixture(scope="module")
def dbs():
    return build(IOS, "fios"), build(ANDROID, "fandroid")


def test_default_output_unchanged(dbs):
    res = P.parity(*dbs)
    assert "target_only" not in res and "inferred" not in res["summary"]
    assert not [r for r in res["matched"] if r["confidence"] in P.INFERRED]


def test_fields_resources_and_generic_keys(dbs):
    res = P.parity(*dbs, structure=True)
    m = {r["symbol"]: r for r in res["matched"]}
    # wire names: CodingKeys raw values meet @SerialName, although every property is renamed
    t = m["TrailSummaryResponse"]
    assert t["target"] == "com.trailmate.data.TrailOverviewJson", t
    assert {"str:trail_id", "field:trailid"} & set(t["evidence"]), t
    # screen state by its stored properties
    h = m["HikeDetailState"]
    assert h["target"] == "com.trailmate.detail.RouteDetailViewState" and h["confidence"] == "fields", h
    # image resources: Image("badge_summit") / R.drawable.badge_summit
    b = m["BadgeShelfView"]
    assert b["target"] == "com.trailmate.badges.AchievementRow" and b["confidence"] == "structure", b
    assert "res:badgesummit" in b["evidence"]
    # only generic keys (cancel / yes on every prompt) in common: no match
    missing = {r["symbol"] for r in res["missing"]}
    assert "HikeShareSheet" in missing and "LiveActivityWidget" in missing, missing


def test_generic_guard_is_what_keeps_them_apart(dbs, monkeypatch):
    monkeypatch.setattr(PS, "SPECIFIC_FILES", 10 ** 6)
    m = {r["symbol"]: r["target"] for r in P.parity(*dbs, structure=True)["matched"]}
    # one name word and two generic keys in common: IDF alone would pair a share sheet with a delete dialog
    assert m.get("HikeShareSheet") == "com.trailmate.trip.HikeDeleteDialog"


def test_both_sides(dbs):
    res = P.parity(*dbs, structure=True, both_sides=True)
    only = {r["symbol"] for r in res["target_only"]}
    assert "com.trailmate.wear.WearTileService" in only
    assert "com.trailmate.settings.SettingsStore.exportBackup" in only      # member of a matched type
    assert "com.trailmate.wear.WearTileService.onTileRequest" not in only   # counted under its type
    assert "com.trailmate.data.TrailOverviewJson" not in only
    assert res["summary"]["target_only"] == len(res["target_only"])


def test_cli_both_sides(dbs):
    r = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "parity", "--db", dbs[0], "--against", dbs[1],
                        "--structure", "--both-sides"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "== MATCHED pairs:" in r.stdout and "== ONLY IN TARGET (no source counterpart):" in r.stdout
    assert "SettingsStore -> com.trailmate.settings.SettingsStore  [exact]" in r.stdout
    assert "[fields" in r.stdout
    j = json.loads(subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "parity", "--db", dbs[0], "--against",
                                   dbs[1], "--both-sides", "--json"], capture_output=True, text=True, cwd=ROOT).stdout)
    assert "target_only" in j and "inferred" not in j["summary"]


def test_wire_names(dbs):
    want = {"field:trailid", "field:displayname", "field:elevationgain", "field:loopkind"}
    assert PS.Side(P.load(dbs[0])).fields("TrailSummaryResponse") == want            # CodingKeys raw values
    assert PS.Side(P.load(dbs[1])).fields("com.trailmate.data.TrailOverviewJson") == want   # @SerialName

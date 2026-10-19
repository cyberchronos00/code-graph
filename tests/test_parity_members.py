"""`cg parity --structure` (#227): members of a matched type paired by what they call inside the counterpart type
(`duplicatePlants` / `copySelected`), and Kotlin view-model handlers paired with the `case .x:` arms of the Swift
processor's `receive(_:)`. Invented app: a plant-care app in Swift and its Kotlin port."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
pytest.importorskip("tree_sitter_swift")
pytest.importorskip("tree_sitter_kotlin")
from test_parity import build  # noqa: E402
from cg_code_graph import parity as P  # noqa: E402

IOS = {
 "Garden/Store/PlantStore.swift":
     "final class PlantStore {\n    func insertPlant(_ p: Int) {}\n    func nextSortIndex() -> Int { 0 }\n"
     "    func archiveAll() {}\n\n    func duplicatePlants() {\n        let i = nextSortIndex()\n        insertPlant(i)\n    }\n}\n",
 "Garden/Services/WateringService.swift":
     "final class WateringService {\n    func logWatering() {}\n    func snooze() {}\n}\n",
 "Garden/Plant/PlantAction.swift":
     "enum PlantAction {\n    case waterNowPressed\n    case renamePlantTapped(String)\n    case dismissTapped\n}\n",
 "Garden/Plant/PlantProcessor.swift":
     "final class PlantProcessor {\n    let watering = WateringService()\n    var name = \"\"\n\n"
     "    func receive(_ action: PlantAction) {\n        switch action {\n        case .waterNowPressed:\n"
     "            watering.logWatering()\n        case let .renamePlantTapped(newName):\n            name = newName\n"
     "        case .dismissTapped:\n            watering.snooze()\n        }\n    }\n}\n",
}
ANDROID = {
 "app/src/main/java/com/leafy/store/PlantStore.kt":
     "package com.leafy.store\n\nclass PlantStore {\n    fun insertPlant(p: Int) {}\n    fun nextSortIndex(): Int = 0\n"
     "    fun archiveAll() {}\n\n    fun copySelected() {\n        val i = nextSortIndex()\n        insertPlant(i)\n    }\n"
     "    fun exportCsv() {}\n}\n",
 "app/src/main/java/com/leafy/services/WateringService.kt":
     "package com.leafy.services\n\nclass WateringService {\n    fun logWatering() {}\n    fun snooze() {}\n}\n",
 "app/src/main/java/com/leafy/plant/PlantViewModel.kt":
     "package com.leafy.plant\n\nimport com.leafy.services.WateringService\n\nclass PlantViewModel(private val watering: WateringService) {\n"
     "    var name = \"\"\n    fun handleWaterNowClick() {\n        watering.logWatering()\n    }\n"
     "    fun handleRenamePlantTapped(newName: String) {\n        name = newName\n    }\n"
     "    fun handleSettingsClick() {}\n}\n",
}


@pytest.fixture(scope="module")
def dbs():
    return build(IOS, "gios"), build(ANDROID, "gandroid")


def test_member_pairs_by_paired_calls(dbs):
    res = P.parity(*dbs, structure=True)
    m = {r["symbol"]: r for r in res["matched"]}
    r = m["PlantStore.duplicatePlants"]
    assert r["target"] == "com.leafy.store.PlantStore.copySelected" and r["confidence"] == "structure", r
    assert "sym:com.leafy.store.PlantStore.insertPlant" in r["evidence"]
    # without --structure nothing is inferred
    assert "PlantStore.duplicatePlants" in {x["symbol"] for x in P.parity(*dbs)["missing"]}


def test_handlers_pair_with_receive_arms(dbs):
    res = P.parity(dbs[1], dbs[0], structure=True)
    m = {r["symbol"]: r for r in res["matched"]}
    w = m["com.leafy.plant.PlantViewModel.handleWaterNowClick"]
    assert w["target"] == "PlantProcessor.receive" and w["case"] == "waterNowPressed", w
    assert "case .waterNowPressed" in w["evidence"]
    assert m["com.leafy.plant.PlantViewModel.handleRenamePlantTapped"]["case"] == "renamePlantTapped"
    # nothing in common with any arm: still missing
    assert "com.leafy.plant.PlantViewModel.handleSettingsClick" in {x["symbol"] for x in res["missing"]}

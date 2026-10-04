"""Stand-in auto-imports from nuxt.config `imports.dirs` / `imports.imports` when `.nuxt/` is absent."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph.plugins.nuxt.plugin import (  # noqa: E402
    generate_types, nuxt_import_plan, unevaluable_import_dirs)
from codegraph.blindspots import nuxt_unevaluable_import_dirs  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _mark_nuxt(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", '{ "name": "nuxt-imports-fixture", "private": true, "dependencies": { "nuxt": "^3.17.0" } }\n')
    _write(tmp_path / "tsconfig.json", '{ "extends": "./.nuxt/tsconfig.json" }\n')


def _project(tmp_path: Path) -> None:
    _mark_nuxt(tmp_path)
    _write(tmp_path / "composables/nested/deep/useDeep.ts", "export function useDeep() { return 1 }\n")
    _write(tmp_path / "composables/globbed/sub/useGlob.ts", "export function useGlob() { return 1 }\n")
    _write(tmp_path / "composables/globbed/skip.ts", "export function useNotGlob() { return 1 }\n")
    _write(tmp_path / "composables/single/only.ts", "export function useOnly() { return 1 }\n")
    _write(tmp_path / "composables/single/other.ts", "export function useOther() { return 1 }\n")
    _write(tmp_path / "utils/aliased.ts", "export function useAliased() { return 1 }\n")
    _write(tmp_path / "layers/extra/composables/fromlayer/useLayer.ts", "export function useLayer() { return 1 }\n")
    _write(tmp_path / "layers/extra/nuxt.config.ts",
           "export default defineNuxtConfig({ imports: { dirs: ['./composables/fromlayer'] } })\n")
    _write(tmp_path / "pages/index.vue", """<script setup lang="ts">
const a = useDeep()
const b = useGlob()
const c = useOnly()
const d = useRenamed()
const e = useLayer()
const f = useOther()
const g = useNotGlob()
</script>
<template><div /></template>
""")
    _write(tmp_path / "nuxt.config.ts", """
export default defineNuxtConfig({
  extends: ['./layers/extra'],
  imports: {
    global: false,
    dirs: [
      './composables/nested/**',
      './composables/globbed/sub/*.ts',
      './composables/single/only.ts',
      someDir,
    ],
    imports: [{ name: 'useAliased', as: 'useRenamed', from: '~/utils/aliased', priority: 5 }],
  },
})
""")


def test_plan_reads_dirs_named_layer_and_blind_spots(tmp_path):
    _project(tmp_path)
    plan = nuxt_import_plan(tmp_path)
    assert plan["scan"] is True and plan["global"] is False and plan["auto_import"] is True
    assert plan["named"] == [{"name": "useAliased", "as": "useRenamed", "from": "~/utils/aliased", "priority": 5}]
    assert len(plan["layers"]) == 2
    dirs = plan["layers"][0]["cfg"]["dirs"]
    assert "./composables/nested/**" in dirs and "./composables/single/only.ts" in dirs
    assert plan["layers"][1]["cfg"]["dirs"] == ["./composables/fromlayer"]
    blind = unevaluable_import_dirs(tmp_path)
    assert blind == [("nuxt.config.ts", 10)]
    finding = nuxt_unevaluable_import_dirs(tmp_path)
    assert finding["kind"] == "nuxt_unevaluable_import_dirs" and finding["sample"] == "nuxt.config.ts:10"
    d, st = generate_types(tmp_path, ".")
    text = (d / "types" / "imports.d.ts").read_text()
    assert "const useDeep:" in text and "const useGlob:" in text and "const useOnly:" in text
    assert "const useRenamed:" in text and "utils/aliased" in text
    assert "const useLayer:" in text
    assert "useOther" not in text and "useNotGlob" not in text
    assert st["import_global"] is False and st["import_scan"] is True


def test_scan_false_keeps_named_imports_only(tmp_path):
    _mark_nuxt(tmp_path)
    _write(tmp_path / "composables/useGone.ts", "export function useGone() { return 1 }\n")
    _write(tmp_path / "utils/aliased.ts", "export function useAliased() { return 1 }\n")
    _write(tmp_path / "nuxt.config.ts", """
export default defineNuxtConfig({
  imports: {
    scan: false,
    global: true,
    dirs: ['./composables'],
    imports: [{ name: 'useAliased', from: '~/utils/aliased' }],
  },
})
""")
    assert unevaluable_import_dirs(tmp_path) == []
    _d, st = generate_types(tmp_path, ".")
    text = (_d / "types" / "imports.d.ts").read_text()
    assert "const useAliased:" in text
    assert "useGone" not in text
    assert st["import_scan"] is False and st["import_global"] is True and st["auto_imports"] == 1


def test_prepared_nuxt_dir_is_not_a_blind_spot(tmp_path):
    _mark_nuxt(tmp_path)
    _write(tmp_path / "nuxt.config.ts", "export default defineNuxtConfig({ imports: { dirs: [computedDir] } })\n")
    _write(tmp_path / ".nuxt/imports.d.ts", "export {}\n")
    assert unevaluable_import_dirs(tmp_path) == []
    assert nuxt_unevaluable_import_dirs(tmp_path) is None


@needs_ts
def test_indexed_edges_for_dirs_glob_file_alias_and_layer(tmp_path):
    _project(tmp_path)
    from codegraph.indexer import index_project
    db = tmp_path / "g.db"
    index_project(tmp_path, db, "fixture")
    import sqlite3
    rows = sqlite3.connect(db).execute(
        "SELECT dst, kind FROM edges WHERE src LIKE 'page:%' AND kind IN ('CALLS','USES_COMPOSABLE')").fetchall()
    dsts = {r[0] for r in rows}
    assert any(d.endswith("#useDeep") for d in dsts)
    assert any(d.endswith("#useGlob") for d in dsts)
    assert any(d.endswith("#useOnly") for d in dsts)
    assert any(d.endswith("#useAliased") for d in dsts)
    assert any("useLayer" in d for d in dsts)
    assert not any(d.endswith("#useOther") or d.endswith("#useNotGlob") for d in dsts)
    from codegraph.core.store import GraphStore
    kinds = [b["kind"] for b in (GraphStore(db).meta().get("stats") or {}).get("coverage", {}).get("blind_spots") or []]
    assert "nuxt_unevaluable_import_dirs" in kinds

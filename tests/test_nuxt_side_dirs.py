"""Nuxt server/ and shared/ are source dirs next to the srcDir (#214): tests/nuxt4_side_fixture is a Nuxt 4 layout
(app/, server/, shared/) with no node_modules and no .nuxt/."""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from cg_code_graph.plugins.nuxt.plugin import find_src_dir, side_dirs, side_files  # noqa: E402
from sample import EXTRACTOR_DEPS  # noqa: E402

FX = ROOT / "tests" / "nuxt4_side_fixture"
needs_ts = pytest.mark.skipif(not EXTRACTOR_DEPS.exists(), reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
SIDE = {"server/api/recipes.get.ts", "server/utils/db.ts", "server/middleware/log.ts", "shared/types/recipe.ts",
        "shared/utils/slug.ts"}


def test_side_dirs_by_layout(tmp_path):
    assert find_src_dir(FX) == "app" and side_dirs(FX, "app") == ["server", "shared"]
    assert set(side_files(FX, ["server", "shared"])) == SIDE
    # Nuxt 3 with the root srcDir: server/ is already inside it
    (tmp_path / "server" / "api").mkdir(parents=True)
    (tmp_path / "pages").mkdir()
    assert find_src_dir(tmp_path) == "." and side_dirs(tmp_path, ".") == []
    # Nuxt 3 with srcDir src/: src/server is inside it, a root server/ is not
    (tmp_path / "src" / "server").mkdir(parents=True)
    (tmp_path / "src" / "app.vue").write_text("<template><div/></template>\n")
    assert find_src_dir(tmp_path) == "src" and side_dirs(tmp_path, "src") == ["server"]


def _index(root: Path, tmp_path: Path):
    from cg_code_graph.indexer import index_project
    stats = index_project(root, tmp_path / "g.db", "recipe-site")
    con = sqlite3.connect(tmp_path / "g.db")
    return stats, con


def _check(stats, con):
    files = {f for (f,) in con.execute("SELECT DISTINCT file FROM nodes WHERE file IS NOT NULL")}
    assert SIDE <= files
    assert any(f.startswith("app/") for f in files)
    calls = set(con.execute("SELECT src, dst FROM edges WHERE kind='CALLS'"))
    assert ("function:server/api/recipes.get.ts#handler", "function:server/utils/db.ts#listRecipes") in calls
    assert ("function:server/utils/db.ts#listRecipes", "function:shared/utils/slug.ts#slugify") in calls
    ts = next(e for e in stats["coverage"]["languages"] if e["language"] == "typescript")
    assert ts["paths"]["unmapped"] == ["layers/extra/helper.ts"]
    assert not SIDE & set(ts["paths"]["unmapped"]) and ts["indexed"] == 7
    assert "Nuxt project" in ts["hint"] and "app/, server/, shared/" in ts["hint"]
    assert stats["plugins"]["typescript/nuxt"]["side_dirs"] == ["server", "shared"]


@needs_ts
def test_nuxt4_server_and_shared_are_indexed(tmp_path):
    stats, con = _index(FX, tmp_path)
    assert stats["plugins"]["typescript/nuxt"]["prepared"] == "generated"
    _check(stats, con)


@needs_ts
def test_prepared_app_tsconfig_without_server(tmp_path):
    """`nuxi prepare` output: .nuxt/tsconfig.app.json covers app/ only (Nitro has its own tsconfig)."""
    root = tmp_path / "recipe-site"
    shutil.copytree(FX, root)
    (root / ".nuxt").mkdir()
    (root / ".nuxt" / "tsconfig.app.json").write_text(json.dumps(
        {"compilerOptions": {"strict": True, "module": "ESNext", "moduleResolution": "Bundler", "target": "ESNext",
                             "jsx": "preserve", "skipLibCheck": True, "noEmit": True},
         "include": ["../app/**/*"]}))
    stats, con = _index(root, tmp_path)
    assert stats["plugins"]["typescript/nuxt"]["prepared"] == "nuxi"
    _check(stats, con)

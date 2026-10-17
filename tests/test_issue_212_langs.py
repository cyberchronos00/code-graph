"""Parameter defaults outside Swift (#212): the declaration edge carries `default_arg`, and a call that leaves the
argument out gets its own edge to what the default calls (`omits_default`, `default_of`) where the language runs the
default per call (TypeScript / JavaScript, Kotlin, PHP). Python evaluates a default once, at definition."""
import json
import sqlite3
from pathlib import Path

import pytest

from cg_code_graph.indexer import index_project

ROOT = Path(__file__).resolve().parents[1]
HAS_TS = (ROOT / "cg_code_graph" / "plugins" / "ts" / "extractor" / "node_modules").exists()
HAS_PHP = (ROOT / "cg_code_graph" / "plugins" / "php" / "extractor" / "vendor").exists()


def _calls(db: Path) -> list[tuple[str, str, int, dict]]:
    con = sqlite3.connect(db)
    return [(s, d, ln, json.loads(a or "{}")) for s, d, ln, a in
            con.execute("select src, dst, line, attrs from edges where kind in ('CALLS','INSTANTIATES')")]


def _index(tmp_path, files: dict, name: str) -> list:
    root = tmp_path / name
    for rel, txt in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(txt, encoding="utf-8")
    index_project(root, tmp_path / f"{name}.db", name)
    return _calls(tmp_path / f"{name}.db")


TS = """\
export function currentGamut(): number { return 1 }
export function fallbackTint(): number { return 0 }

export function render(c: number, gamut: number = currentGamut(), tint = fallbackTint()) { return c + gamut + tint }

export function preview() { return render(1) }
export function exportImage() { return render(1, 2) }
export function exportTinted() { return render(1, 2, 3) }
export function exportUndefined() { return render(1, undefined, 3) }
export function exportSpread(xs: [number, number]) { return render(1, ...xs) }
"""


@pytest.mark.skipif(not HAS_TS, reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
def test_typescript_defaults(tmp_path):
    calls = _index(tmp_path, {"package.json": '{"name": "render"}', "tsconfig.json": "{}", "src/render.ts": TS}, "ts")
    gamut = "function:src/render.ts#currentGamut"
    tint = "function:src/render.ts#fallbackTint"
    render = "function:src/render.ts#render"
    decl = {(d, a.get("default_arg")) for s, d, _l, a in calls if s == render}
    assert decl == {(gamut, "gamut"), (tint, "tint")}
    omits = {(s.split("#")[1], d.split("#")[1], a["omits_default"]) for s, d, _l, a in calls if a.get("omits_default")}
    assert omits == {("preview", "currentGamut", "gamut"), ("preview", "fallbackTint", "tint"),
                     ("exportImage", "fallbackTint", "tint"), ("exportUndefined", "currentGamut", "gamut")}
    assert all(a["default_of"] == render for *_x, a in calls if a.get("omits_default"))


KT = """\
package shop

object Prefs {
    fun tint(): Int = 0
}

fun currentGamut(): Int = 1

fun render(c: Int, gamut: Int = currentGamut(), tint: Int = Prefs.tint(), done: () -> Unit = {}) {}

fun preview() { render(1) }
fun exportImage() { render(1, 2) }
fun exportNamed() { render(1, tint = 3) }
fun exportTrailing() { render(1, 2, 3) { } }
"""


def test_kotlin_defaults(tmp_path):
    pytest.importorskip("tree_sitter_kotlin")
    calls = _index(tmp_path, {"build.gradle.kts": "", "src/main/kotlin/shop/Render.kt": KT}, "kt")
    gamut, tint, render = "function:shop.currentGamut", "method:shop.Prefs.tint", "function:shop.render"
    decl = {(d, a.get("default_arg")) for s, d, _l, a in calls if s == render}
    assert decl == {(gamut, "gamut"), (tint, "tint")}
    omits = {(s.split(".")[-1], d.split(".")[-1], a["omits_default"]) for s, d, _l, a in calls if a.get("omits_default")}
    assert omits == {("preview", "currentGamut", "gamut"), ("preview", "tint", "tint"),
                     ("exportImage", "tint", "tint"), ("exportNamed", "currentGamut", "gamut")}
    assert all(a["default_of"] == render for *_x, a in calls if a.get("omits_default"))


PY = """\
def current_gamut():
    return 1


def render(c, gamut=current_gamut(), *, tint=None):
    return c + gamut


def preview():
    return render(1)
"""


def test_python_marks_the_declaration_only(tmp_path):
    calls = _index(tmp_path, {"app/__init__.py": "", "app/render.py": PY}, "py")
    decl = [(s, d, a) for s, d, _l, a in calls if s == "function:app.render.render"]
    assert decl == [("function:app.render.render", "function:app.render.current_gamut", {"default_arg": "gamut"})]
    assert not any(a.get("omits_default") for *_x, a in calls)


PHP = """<?php
namespace App\\Services;

class SystemClock { public function __construct() {} }

class Render
{
    public function draw(int $c, SystemClock $clock = new SystemClock(), int $tint = 0) {}

    public function preview() { $this->draw(1); }
    public function exportImage() { $this->draw(1, new SystemClock()); }
    public function exportNamed() { $this->draw(1, tint: 3); }
    public function exportSpread(array $xs) { $this->draw(1, ...$xs); }
}
"""


@pytest.mark.skipif(not HAS_PHP, reason="run `composer install` in cg_code_graph/plugins/php/extractor")
def test_php_new_in_initializer(tmp_path):
    from cg_code_graph import query as Q
    from cg_code_graph.core.store import GraphStore
    calls = _index(tmp_path, {"composer.json": '{"autoload": {"psr-4": {"App\\\\": "app/"}}}',
                              "app/Services/Render.php": PHP}, "php")
    draw, ctor = "method:App\\Services\\Render::draw", "method:App\\Services\\SystemClock::__construct"
    assert {(d, a.get("default_arg")) for s, d, _l, a in calls if s == draw} == {
        (ctor, "clock"), ("class:App\\Services\\SystemClock", "clock")}
    omits = {s.split("::")[1] for s, d, _l, a in calls if a.get("omits_default") and d == ctor}
    assert omits == {"preview", "exportNamed"}
    r = Q.impact(GraphStore(str(tmp_path / "php.db")), "SystemClock::__construct")
    by = {c["id"]: c for c in r["callers"]}
    assert by["method:App\\Services\\Render::preview"].get("omits_default") == "clock"
    assert "method:App\\Services\\Render::exportImage" in by and not by["method:App\\Services\\Render::exportImage"].get("omits_default")
    assert "method:App\\Services\\Render::exportSpread" not in by


TS_STATIC = """\
export class BaseService {
  static create(Service: any, ctx: BaseService) { return new Service(ctx) }
  ping() { return 1 }
}
export class TagService extends BaseService {
  async create(name: string) { return name }
  ping() { return 2 }
}
"""


@pytest.mark.skipif(not HAS_TS, reason="run `npm ci` in cg_code_graph/plugins/ts/extractor")
def test_typescript_instance_method_does_not_override_a_static_one(tmp_path):
    # found by the #199 hand-check: a base class's static factory and a subclass's instance `create`
    _index(tmp_path, {"package.json": '{"name": "svc"}', "tsconfig.json": "{}", "src/svc.ts": TS_STATIC}, "tss")
    con = sqlite3.connect(tmp_path / "tss.db")
    ov = set(con.execute("select src, dst from edges where kind='OVERRIDDEN_BY'"))
    assert ("method:src/svc.ts#BaseService.ping", "method:src/svc.ts#TagService.ping") in ov
    assert not any(s.endswith("BaseService.create") for s, _d in ov)

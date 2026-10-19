"""Dispatch part 3 (#109): receivers typed through generic substitution (`Garage<E>.get() -> T`), call chains
(`shop.engine().run()`), locals initialised from a call chain, and Swift `class func` reached through a subclass.
Swift and Kotlin. Invented fixtures."""
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.indexer import index_project  # noqa: E402

SWIFT = """\
class Engine {
    func run() {}
}
final class TurboEngine: Engine {
    override func run() {}
}
struct Garage<T> {
    let item: T
    func get() -> T { item }
    func first() -> T? { item }
}
class Base {
    class func make() -> Self { fatalError() }
    static func shared() -> Base { Base() }
    func ping() {}
}
final class Sub: Base {
    override func ping() {}
}
struct Shop {
    func engine() -> TurboEngine { TurboEngine() }
    var garage: Garage<TurboEngine> { Garage(item: TurboEngine()) }
}
func makeShop() -> Shop { Shop() }

struct Driver {
    let shop: Shop
    func a() {
        let g = Garage<TurboEngine>(item: TurboEngine())
        g.get().run()
    }
    func b() {
        let g: Garage<TurboEngine> = Garage(item: TurboEngine())
        g.first()?.run()
    }
    func c() {
        shop.engine().run()
    }
    func d() {
        makeShop().engine().run()
    }
    func e() {
        Sub.make().ping()
    }
    func g() {
        shop.garage.get().run()
    }
    func h() {
        let e = shop.engine()
        e.run()
    }
}
"""

KOTLIN = """\
package demo

open class Engine { open fun run() {} }
class TurboEngine : Engine() { override fun run() {} }
class Garage<T>(val item: T) { fun get(): T = item }
class Shop {
    fun engine(): TurboEngine = TurboEngine()
    val garage: Garage<TurboEngine> = Garage(TurboEngine())
}
fun makeShop(): Shop = Shop()

class Driver(val shop: Shop) {
    fun a() { val s = makeShop(); s.engine().run() }
    fun b() { shop.engine().run() }
    fun c() { val g = Garage<TurboEngine>(TurboEngine()); g.get().run() }
    fun d() { val e = shop.engine(); e.run() }
    fun e() { shop.garage.get().run() }
}
"""


def _calls(files, lang):
    pytest.importorskip("tree_sitter_swift" if lang == "swift" else "tree_sitter_kotlin")
    tmp = Path(tempfile.mkdtemp(prefix="cg-dispatch3-"))
    for rel, body in files.items():
        (tmp / "p" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / "p" / rel).write_text(body)
    index_project(tmp / "p", tmp / "g.db", "p")
    c = sqlite3.connect(tmp / "g.db")
    out = {}
    for src, dst, attrs in c.execute("SELECT src, dst, attrs FROM edges WHERE kind = 'CALLS'"):
        out.setdefault(src.rsplit(".", 1)[-1], {})[dst] = attrs
    return out


def test_swift_generic_chain_and_class_func():
    calls = _calls({"App/Main.swift": SWIFT}, "swift")
    for fn in ("a", "b", "c", "d", "g", "h"):
        assert "method:TurboEngine.run" in calls[fn], (fn, calls[fn])
        assert "method:Engine.run" not in calls[fn]
    assert "method:Shop.engine" in calls["d"] and calls["d"]["method:Shop.engine"] is None   # typed, not by name
    # `class func make() -> Self` through Sub: the call lands on Base.make with recv Sub, and its result is a Sub
    assert '"recv": ["class:Sub"]' in calls["e"]["method:Base.make"]
    assert calls["e"].get("method:Sub.ping") is None and "method:Base.ping" not in calls["e"]


def test_kotlin_generic_chain_and_factory_locals():
    calls = _calls({"src/Main.kt": KOTLIN}, "kotlin")
    for fn in ("a", "b", "c", "d", "e"):
        assert "method:demo.TurboEngine.run" in calls[fn], (fn, calls[fn])
    assert calls["a"]["method:demo.Shop.engine"] is None        # was a name-only binding
    assert "method:demo.Garage.get" in calls["c"]

"""Java heuristic plugin: declarations, imports, receiver-type calls, the bookstore-spring sample."""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_java")
from cg_code_graph.indexer import index_project  # noqa: E402

FIX = ROOT / "tests" / "java_fixture"
SHOP = ROOT / "examples" / "bookstore-spring"


def _index(tmp_path, root, name="java"):
    db = tmp_path / "g.db"
    st = index_project(root, db, name)
    return st, sqlite3.connect(db)


def _ids(con, kind=None):
    if kind:
        return {r[0] for r in con.execute("select id from nodes where kind=?", (kind,))}
    return {r[0] for r in con.execute("select id from nodes")}


def _edges(con, kind):
    return {(s, d, c) for s, d, c in con.execute(
        "select src, dst, confidence from edges where kind=?", (kind,))}


def test_node_kinds_and_ids(tmp_path):
    st, con = _index(tmp_path, FIX, "java-fixture")
    assert st["plugins"]["java"]["mode"] == "heuristic"
    assert st["plugins"]["java"]["non_utf8_files"] >= 1
    ids = _ids(con)
    assert "package:com.example.shop" in ids
    assert "class:com.example.shop.DefaultPricing" in ids
    assert "class:com.example.shop.Pricing" in ids
    assert "class:com.example.shop.Color" in ids
    assert "class:com.example.shop.Rec" in ids
    assert "class:com.example.shop.Outer.Inner" in ids
    assert any(i.startswith("class:com.example.shop.Outer.anon.") for i in ids)
    assert "method:com.example.shop.OrderService.place" in ids
    assert "constructor:com.example.shop.OrderService.<init>" in ids
    assert "field:com.example.shop.OrderService.pricing" in ids
    assert "field:com.example.shop.Rec.name" in ids
    assert "enum_case:com.example.shop.Color.RED" in ids
    assert "class:com.example.shop.Latin" in ids
    # overloads share one id
    assert sum(1 for i in ids if i == "method:com.example.shop.DefaultPricing.line") == 1
    kinds = {r[0]: r[1] for r in con.execute(
        "select id, json_extract(attrs, '$.java_kind') from nodes where kind='class'")}
    assert kinds["class:com.example.shop.Pricing"] == "interface"
    assert kinds["class:com.example.shop.Color"] == "enum"
    assert kinds["class:com.example.shop.Rec"] == "record"
    assert kinds["class:com.example.shop.DefaultPricing"] == "class"


def test_calls_imports_and_hierarchy(tmp_path):
    _, con = _index(tmp_path, FIX, "java-fixture")
    calls = _edges(con, "CALLS")
    inst = _edges(con, "INSTANTIATES")
    impl = _edges(con, "IMPLEMENTS")
    ext = _edges(con, "EXTENDS")
    by = _edges(con, "IMPLEMENTED_BY") | _edges(con, "OVERRIDDEN_BY")

    def called(src, dst):
        return any(s == src and d == dst and c == "heuristic" for s, d, c in calls)

    assert ("class:com.example.shop.DefaultPricing", "class:com.example.shop.Pricing", "heuristic") in impl
    assert ("class:com.example.shop.Rec", "class:com.example.shop.Marker", "heuristic") in impl
    assert ("class:com.example.shop.Child", "class:com.example.shop.Base", "heuristic") in ext
    assert ("method:com.example.shop.Pricing.line", "method:com.example.shop.DefaultPricing.line", "heuristic") in by
    assert ("method:com.example.shop.Base.open", "method:com.example.shop.Child.open", "heuristic") in by
    # receiver field type, this, local variable
    assert called("method:com.example.shop.OrderService.place", "method:com.example.shop.Pricing.line")
    assert called("method:com.example.shop.OrderService.place", "method:com.example.shop.OrderService.helper")
    assert called("method:com.example.shop.OrderService.again", "method:com.example.shop.OrderService.place")
    assert called("method:com.example.shop.OrderService.localReceiver", "method:com.example.shop.Pricing.line")
    assert called("method:com.example.shop.Child.open", "method:com.example.shop.Base.open")
    # explicit import, wildcard, same package, static import, unique name
    assert ("method:com.example.other.Explicit.go", "class:com.example.shop.OrderService", "heuristic") in inst
    assert called("method:com.example.other.Explicit.go", "method:com.example.shop.OrderService.place")
    assert ("method:com.example.other.Wild.make", "class:com.example.shop.DefaultPricing", "heuristic") in inst
    assert ("method:com.example.shop.SamePackage.make", "class:com.example.shop.DefaultPricing", "heuristic") in inst
    assert called("method:com.example.other.Statics.sum", "method:com.example.shop.Maths.add")
    assert called("method:com.example.other.OnlyName.go", "method:com.example.shop.Unique.uniqShopMarker")
    # nested type and a call inside an anonymous class
    assert called("method:com.example.shop.Outer.useInner", "method:com.example.shop.Outer.Inner.value")
    anon = next(i for i in _ids(con) if i.startswith("method:com.example.shop.Outer.anon.") and i.endswith(".run"))
    assert called(anon, "method:com.example.shop.Outer.ping")
    refs = _edges(con, "REFERENCES_FN")
    assert ("method:com.example.shop.Outer.ref", "method:com.example.shop.Outer.ping", "heuristic") in refs
    # lambda body stays on the enclosing method
    assert called("method:com.example.shop.Outer.lambda", "method:com.example.shop.Outer.ping")


def test_syntax_error_is_recorded_and_index_continues(tmp_path):
    st, con = _index(tmp_path, FIX, "java-fixture")
    cov = next(e for e in st["coverage"]["languages"] if e["language"] == "java")
    assert cov["status"] == "heuristic"
    assert any(e["file"].endswith("Broken.java") for e in cov["syntax_errors"])
    assert "method:com.example.shop.Broken.ok" in _ids(con) or "class:com.example.shop.Broken" in _ids(con)


def test_bookstore_spring_plain_graph(tmp_path):
    st, con = _index(tmp_path, SHOP, "bookstore-spring")
    langs = {e["language"]: e for e in st["coverage"]["languages"]}
    assert langs["java"]["status"] == "heuristic" and langs["java"]["files"] == 14
    # OrderEvents.kt plus build.gradle.kts and settings.gradle.kts (.kts is Kotlin)
    assert langs["kotlin"]["status"] == "heuristic" and langs["kotlin"]["files"] == 3
    calls = _edges(con, "CALLS") | _edges(con, "TEST_CALLS")
    impl = _edges(con, "IMPLEMENTS")
    assert ("class:com.example.bookstore.pricing.DefaultPricingService",
            "class:com.example.bookstore.pricing.PricingService", "heuristic") in impl
    assert ("method:com.example.bookstore.order.OrderService.place",
            "method:com.example.bookstore.pricing.PricingService.lineTotal", "heuristic") in calls
    assert ("method:com.example.bookstore.web.OrderController.create",
            "method:com.example.bookstore.order.OrderService.place", "heuristic") in calls
    assert ("method:com.example.bookstore.web.BookController.list",
            "method:com.example.bookstore.catalog.BookRepository.findAll", "heuristic") in calls
    assert ("method:com.example.bookstore.pricing.PricingServiceTest.appliesDiscount",
            "method:com.example.bookstore.pricing.DefaultPricingService.lineTotal", "heuristic") in calls
    assert "method:com.example.bookstore.order.OrderEvents.onPlaced" in _ids(con)


def test_call_shapes_a_java_developer_expects(tmp_path):
    """Chains, field and static calls, interface receivers, super, lambdas, method references."""
    root = tmp_path / "proj"
    shop = root / "com" / "example" / "shop"
    other = root / "com" / "example" / "other"
    shop.mkdir(parents=True)
    other.mkdir()
    (shop / "Shapes.java").write_text("""\
package com.example.shop;
interface Pricing {
    int line(int qty);
    Pricing child();
}
class DefaultPricing implements Pricing {
    public int line(int qty) { return qty; }
    public Pricing child() { return this; }
}
class OtherPricing implements Pricing {
    public int line(int qty) { return qty * 2; }
    public Pricing child() { return this; }
}
class Maths { public static int add(int a, int b) { return a + b; } }
class Base { public void open() {} }
class Child extends Base { public void open() { super.open(); } }
class Box {
    Inner b() { return new Inner(); }
    static class Inner { int c() { return 1; } }
}
class Use {
    Pricing pricing;
    void field() { this.pricing.line(1); }
    void param(Pricing p) { p.line(1); }
    void chain(Box a) { a.b().c(); }
    void ret(Pricing p) { p.child().line(1); }
    void constructed() { new DefaultPricing().line(1); }
    void staticCall() { Maths.add(1, 2); }
    void lambda() {
        java.util.function.Consumer<Pricing> c = (Pricing x) -> x.line(1);
    }
    void refs(Pricing p) {
        Runnable r = Use::staticPing;
        Runnable r2 = this::instancePing;
        Runnable r3 = p::line;
        java.util.function.Supplier<DefaultPricing> s = DefaultPricing::new;
    }
    static void staticPing() {}
    void instancePing() {}
}
""")
    (other / "Via.java").write_text("""\
package com.example.other;
import static com.example.shop.Maths.add;
public class Via { int sum() { return add(1, 2); } }
""")
    _, con = _index(tmp_path, root, "shapes")
    calls = _edges(con, "CALLS")
    refs = _edges(con, "REFERENCES_FN")

    def dsts(src):
        return {d for s, d, c in calls if s == src and c == "heuristic"}

    # chained call a.b().c() with a known return type, and this.field.method()
    assert dsts("method:com.example.shop.Use.chain") == {
        "method:com.example.shop.Box.b", "method:com.example.shop.Box.Inner.c"}
    assert dsts("method:com.example.shop.Use.field") == {"method:com.example.shop.Pricing.line"}
    # static method via the class name, and a static import
    assert dsts("method:com.example.shop.Use.staticCall") == {"method:com.example.shop.Maths.add"}
    assert dsts("method:com.example.other.Via.sum") == {"method:com.example.shop.Maths.add"}
    # parameter typed by an interface: the interface method, not either implementation
    assert dsts("method:com.example.shop.Use.param") == {"method:com.example.shop.Pricing.line"}
    assert dsts("method:com.example.shop.Use.ret") == {
        "method:com.example.shop.Pricing.child", "method:com.example.shop.Pricing.line"}
    assert dsts("method:com.example.shop.Use.constructed") == {
        "constructor:com.example.shop.DefaultPricing.<init>",
        "method:com.example.shop.DefaultPricing.line"}
    # super call, and a call inside an explicitly typed lambda
    assert dsts("method:com.example.shop.Child.open") == {"method:com.example.shop.Base.open"}
    assert dsts("method:com.example.shop.Use.lambda") == {"method:com.example.shop.Pricing.line"}
    # Foo::bar, this::bar, expr::bar, Foo::new
    ref_dst = {d for s, d, c in refs if s == "method:com.example.shop.Use.refs" and c == "heuristic"}
    assert ref_dst == {
        "method:com.example.shop.Use.staticPing",
        "method:com.example.shop.Use.instancePing",
        "method:com.example.shop.Pricing.line",
        "constructor:com.example.shop.DefaultPricing.<init>",
    }


def test_setup_java_prints_jdk_hint():
    r = subprocess.run([sys.executable, "-m", "cg_code_graph.cli", "setup", "java"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = r.stdout + r.stderr
    assert "JDK 17+" in text
    assert "scip-java" in text

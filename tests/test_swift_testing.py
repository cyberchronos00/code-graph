"""Test cases outside `test` nodes (#71): Swift Testing `@Test` functions (a parameterized `@Test(arguments:)` is one
case, `@Suite` types and nested suites carry attrs.suite, display names / tags / traits from the attribute), XCTest
`test*` methods of XCTestCase subclasses only, and Kotlin `@Test` / `@ParameterizedTest` functions with the framework
from the file's imports all count in `cg tests` and its test-case total; the empty-result hint tells "no test code"
apart from "test files but no recognised test cases"."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("tree_sitter_swift")
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402

FIX = ROOT / "tests"
_S: dict = {}


def db(name):
    if name not in _S:
        d = Path(tempfile.mkdtemp(prefix=f"codegraph-{name}-"))
        index_project(FIX / name, d / "g.db", name)
        _S[name] = d / "g.db"
    return _S[name]


def store(name):
    return GraphStore(db(name))


def attrs(name, nid):
    r = sqlite3.connect(db(name)).execute("select entry_kind, attrs from nodes where id=?", (nid,)).fetchone()
    assert r is not None, nid
    return r[0], json.loads(r[1] or "{}")


def names(res, key):
    return sorted(t["name"] for t in res[key])


def test_issue_repro_three_direct_tests():
    res = Q.tests_covering(store("swift_testing_fixture"), "Pricing.total")
    assert names(res, "direct") == ["freeFunctionTest", "sums", "totalOfEmptyIsZero"]
    assert {t["framework"] for t in res["direct"]} == {"swift-testing"}
    st = res["stats"]
    assert st["tests_in_graph"] == 11
    assert st["tests_by_framework"] == {"swift-testing": 9, "xctest": 2}
    out = Q.render_tests_covering(res)
    assert "of 11 test cases in the graph: swift-testing 9, xctest 2" in out
    assert 'sums "sums items"' in out


def test_mixed_xctest_and_swift_testing():
    res = Q.tests_covering(store("swift_testing_fixture"), "Cart.checkout")
    assert names(res, "direct") == ["`checkout sums the items`", "cartStartsEmpty", "testCheckoutSumsItems"]
    fw = {t["name"]: t["framework"] for t in res["direct"]}
    assert fw == {"cartStartsEmpty": "swift-testing", "testCheckoutSumsItems": "xctest",
                  "`checkout sums the items`": "swift-testing"}


def test_bare_free_function_name_resolves():
    st = store("swift_testing_fixture")
    assert Q.resolve_targets(st, "formatPrice") == ["function:formatPrice"]
    res = Q.tests_covering(st, "formatPrice")
    assert names(res, "direct") == ["backtickedAttribute", "testFormat"]
    assert Q.resolve_targets(st, "Pricing") and "class:Pricing" in Q.resolve_targets(st, "Pricing")


def test_raw_identifier_names_and_backticked_attributes():
    ek, a = attrs("swift_testing_fixture", "method:`Cart checkout tests`.`checkout sums the items`")
    assert ek == "test" and a["suite"] == "`Cart checkout tests`"
    ek, a = attrs("swift_testing_fixture", "method:`Cart checkout tests`.backtickedAttribute")
    assert ek == "test" and a["framework"] == "swift-testing" and a["tags"] == ["money"]
    r = sqlite3.connect(db("swift_testing_fixture")).execute(
        "select line, end_line from nodes where id='method:`Cart checkout tests`.`checkout sums the items`'").fetchone()
    assert r == (6, 10)


def test_helpers_are_not_test_cases():
    for nid in ("method:DiscountTests.makePricing", "method:BaseCase.makeCart", "method:CartXCTests.testHelper",
                "method:CartXCTests.testFactory", "method:TestDataBuilder.testCart"):
        ek, a = attrs("swift_testing_fixture", nid)
        assert ek is None, nid
        assert a.get("test") is True and "framework" not in a


def test_xctest_through_project_base_class():
    ek, a = attrs("swift_testing_fixture", "method:CartXCTests.testFormat")
    assert ek == "test" and a["framework"] == "xctest"


def test_parameterized_tags_traits_and_nested_suites():
    ek, a = attrs("swift_testing_fixture", "method:DiscountTests.percentOff")
    assert ek == "test" and a["parameterized"] is True and a["display_name"] == "percent off"
    assert a["tags"] == ["money"] and a["suite"] == "DiscountTests"
    ek, a = attrs("swift_testing_fixture", "method:DiscountTests.Edge.hundredPercent")
    assert ek == "test" and a["suite"] == "DiscountTests.Edge" and a["traits"] == ["bug", "disabled"]
    _ek, a = attrs("swift_testing_fixture", "class:DiscountTests")
    assert a["suite"] is True and a["display_name"] == "Discounts" and a["tags"] == ["money"]
    assert a["swift_kind"] == "struct"      # `@Suite("...", .tags(.x))` with nested parentheses before `struct`
    _ek, a = attrs("swift_testing_fixture", "class:DiscountTests.Edge")
    assert a["suite"] is True and a["display_name"] == "Edge cases"
    ek, a = attrs("swift_testing_fixture", "method:DiscountTests.zeroAmount")      # in an extension of the suite
    assert ek == "test" and a["suite"] == "DiscountTests"
    res = Q.tests_covering(store("swift_testing_fixture"), "Pricing.discount")
    assert names(res, "direct") == ["hundredPercent", "percentOff", "zeroAmount"]
    assert '"percent off" (parameterized)' in Q.render_tests_covering(res)


def test_testing_import_marks_a_test_target_folder():
    ek, a = attrs("swift_testing_fixture", "method:AppChecks.cartStartsEmpty")
    assert ek == "test" and a["test"] is True and a["framework"] == "swift-testing"
    _ek, a = attrs("swift_testing_fixture", "file:swift:AppTargetChecks/AppChecks.swift")
    assert a.get("test") is True


def test_kotlin_tests_counted_by_framework():
    pytest.importorskip("tree_sitter_kotlin")
    res = Q.tests_covering(store("kotlin_test_fixture"), "shop.Calc.total")
    assert names(res, "direct") == ["sumsItems", "totalOfEmptyIsZero"]
    assert res["stats"]["tests_by_framework"] == {"junit5": 2, "junit4": 1, "kotlin-test": 1}
    res = Q.tests_covering(store("kotlin_test_fixture"), "shop.Calc.discount")
    fw = {t["name"]: t["framework"] for t in res["direct"]}
    assert fw == {"discountNeverGrows": "junit5", "discountOfZero": "kotlin-test"}
    ek, a = attrs("kotlin_test_fixture", "method:shop.CalcTest.discountNeverGrows")
    assert ek == "test" and a["parameterized"] is True
    ek, _a = attrs("kotlin_test_fixture", "method:shop.CalcTest.makeItems")
    assert ek is None


def test_empty_hint_distinguishes_no_test_code(tmp_path):
    lib = tmp_path / "lib"
    (lib / "Sources" / "Lib").mkdir(parents=True)
    (lib / "Sources" / "Lib" / "A.swift").write_text("struct A {\n    func f() {}\n}\n")
    index_project(lib, tmp_path / "a.db", "a")
    out = Q.render_tests_covering(Q.tests_covering(GraphStore(tmp_path / "a.db"), "A.f"))
    assert "no indexed test reaches the target (the graph has no test code" in out
    (lib / "Tests" / "LibTests").mkdir(parents=True)
    (lib / "Tests" / "LibTests" / "Helpers.swift").write_text("struct Fixture {\n    func make() {}\n}\n")
    index_project(lib, tmp_path / "b.db", "b")
    out = Q.render_tests_covering(Q.tests_covering(GraphStore(tmp_path / "b.db"), "A.f"))
    assert "the graph has 1 test files but no recognised test cases" in out
    out = Q.render_tests_covering(Q.tests_covering(store("swift_testing_fixture"), "Pricing.init"))
    assert "no indexed test" not in out
    (lib / "Tests" / "LibTests" / "T.swift").write_text("import Testing\n\n@Test func unrelated() {}\n")
    index_project(lib, tmp_path / "c.db", "c")
    out = Q.render_tests_covering(Q.tests_covering(GraphStore(tmp_path / "c.db"), "A.f"))
    assert "(1 test cases are indexed; none calls the target" in out

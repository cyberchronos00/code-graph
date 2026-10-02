"""impact keeps the override relation apart from the callers (#26): a base method is not a caller of its override,
and the callers of an abstract base method include the calls that land on its overrides. Fixtures are written from
scratch in a temp dir."""
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

FILES = {
    "shapes/__init__.py": "",
    "shapes/base.py": '''
        from abc import ABC, abstractmethod

        class Shape(ABC):
            @abstractmethod
            def area(self) -> float:
                ...

        class Square(Shape):
            def __init__(self, s):
                self.s = s

            def area(self):
                return self.s * self.s

        class Circle(Shape):
            def __init__(self, r):
                self.r = r

            def area(self):
                return 3.14 * self.r * self.r
        ''',
    "shapes/report.py": '''
        from shapes.base import Square, Circle, Shape

        SHAPES = [Square(2), Circle(1)]

        def total():
            return sum(s.area() for s in SHAPES)

        def describe(shape: Shape):
            return f"{shape.area():.2f}"

        def main():
            print(total(), describe(Square(3)))

        if __name__ == "__main__":
            main()
        ''',
}
B = "method:shapes.base."


def build(tmp: Path):
    root = tmp / "shapes_app"
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    db = tmp / "shapes.db"
    index_project(root, db, "shapes_app")
    return GraphStore(db), db


def by_id(res):
    return {c["id"]: c for c in res["callers"]}


def test_override_is_not_a_caller(tmp_path):
    st, _ = build(tmp_path)
    res = Q.impact(st, "Square.area")
    callers = by_id(res)
    assert B + "Shape.area" not in callers                       # the base does not call the override
    assert "function:shapes.report.total" in callers              # the loop over the instances does
    assert [o["fqn"] for o in res["overrides"]] == ["shapes.base.Shape.area"]
    assert res["overridden_by"] == []
    # a call through the base type reaches the override through the base declaration
    assert callers["function:shapes.report.describe"]["via_base"] == "shapes.base.Shape.area"


def test_abstract_base_lists_the_override_callers(tmp_path):
    st, _ = build(tmp_path)
    res = Q.impact(st, "Shape.area")
    callers = by_id(res)
    assert "function:shapes.report.total" in callers
    assert callers["function:shapes.report.total"]["via_override"] == ["shapes.base.Circle.area", "shapes.base.Square.area"]
    assert "function:shapes.report.main" in callers               # and on up to the entry point
    assert B + "Square.area" not in callers and B + "Circle.area" not in callers
    assert {o["fqn"] for o in res["overridden_by"]} == {"shapes.base.Square.area", "shapes.base.Circle.area"}
    assert any(e["entry_kind"] for e in res["entry_points"])


def test_cli_and_mcp_state_the_relation(tmp_path):
    _, db = build(tmp_path)
    p = subprocess.run([sys.executable, "-m", "codegraph.cli", "impact", "Square.area", "--db", str(db), "--no-paths"],
                       cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "overrides: shapes.base.Shape.area" in p.stdout
    assert "] shapes.base.Shape.area" not in p.stdout              # not in the callers list
    p = subprocess.run([sys.executable, "-m", "codegraph.cli", "impact", "Shape.area", "--db", str(db), "--no-paths"],
                       cwd=ROOT, capture_output=True, text=True)
    assert "overridden by: shapes.base.Circle.area, shapes.base.Square.area" in p.stdout
    assert "shapes.report.total  (call through a collection)  (via override shapes.base.Circle.area +1)" in p.stdout

    import codegraph.mcp_server as M
    old = M.STATE["db"]
    M.STATE["db"] = str(db)
    try:
        txt = M.impact("Square.area")
        assert "overrides: shapes.base.Shape.area" in txt
        sc = M.impact.structured("Shape.area")
        ov = sc["overrides"]
        assert ov["overrides"] == [] and {x["fqn"] for x in ov["overridden_by"]} == {"shapes.base.Square.area", "shapes.base.Circle.area"}
        assert "via override (1): shapes.report.total -> shapes.base.Circle.area, shapes.base.Square.area" in sc["result"]
        assert "overrides" not in M.impact.structured("shapes.report.total")
    finally:
        M.STATE["db"] = old

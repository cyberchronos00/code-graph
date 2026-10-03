"""`tests` and `reaches` follow overrides like `impact` (#53): a base / interface method lists the tests and the
dependents of its overrides, marked via_override. A method a subclass inherits without redefining it resolves through
the class's ancestors (`B.run` -> `Base.run`, noted), for Python and TypeScript classes; the overrides followed are
limited to that subclass's own subtree. Fixtures are written from scratch in a temp dir."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

TS_DEPS = ROOT / "codegraph" / "plugins" / "ts" / "extractor" / "node_modules"
needs_ts = pytest.mark.skipif(not TS_DEPS.exists(), reason="run `npm ci` in codegraph/plugins/ts/extractor")

PY = {
    "pkg/__init__.py": "",
    "pkg/core.py": '''
        class Base:
            def run(self):
                return 0

            def name(self):
                return "base"


        class A(Base):
            def run(self):
                return 1


        class B(Base):
            pass


        class C(B):
            def run(self):
                return 3


        PLUGINS = [A()]


        def main():
            for p in PLUGINS:
                p.run()


        def use_b():
            return B().name()


        def use_c():
            return C().run()
        ''',
    "tests/test_main.py": '''
        from pkg.core import main, use_b, use_c


        def test_main():
            main()


        def test_use_b():
            use_b()


        def test_use_c():
            use_c()
        ''',
}
TS = {
    "tsconfig.json": '{"compilerOptions": {"target": "es2020", "module": "esnext", "moduleResolution": "node", "strict": true}, "include": ["src"]}',
    "src/shapes.ts": '''
        export class Shape {
          area(): number { return 0 }
          label(): string { return 'shape' }
        }

        export class Square extends Shape {
          area(): number { return 4 }
        }

        export class Circle extends Shape {}

        export function total(shapes: Shape[]): number {
          return shapes.reduce((s, x) => s + x.area(), 0)
        }

        export function describe(c: Circle): string {
          return c.label()
        }

        export function squareArea(): number {
          return new Square().area()
        }

        export abstract class Repo {
          abstract save(): void
        }

        export class MemRepo implements Repo {
          save(): void {}
        }

        export function persist(r: Repo) {
          r.save()
        }
        ''',
    "src/shapes.test.ts": '''
        import { describe as d, squareArea } from './shapes'
        test('square', () => { squareArea() })
        test('circle', () => { d(new (class {} as any)()) })
        ''',
}


def build(tmp: Path, files: dict, name: str):
    root = tmp / name
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    db = tmp / f"{name}.db"
    index_project(root, db, name)
    return GraphStore(db), db


def cli(*args):
    p = subprocess.run([sys.executable, "-m", "codegraph.cli", *args], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_tests_follow_overrides(tmp_path):
    st, db = build(tmp_path, PY, "py_app")
    res = Q.tests_covering(st, "pkg.core.Base.run")
    by = {t["name"]: t for t in res["transitive"]}
    # main loops over [A()]: test_main reaches Base.run's API through A.run; use_c calls C.run
    assert set(by) == {"test_main", "test_use_c"}
    assert by["test_main"]["via_override"] == ["pkg.core.A.run"]
    assert by["test_use_c"]["via_override"] == ["pkg.core.C.run"]
    # an override's own tests are unchanged and unmarked
    own = Q.tests_covering(st, "pkg.core.A.run")["transitive"]
    assert [t["name"] for t in own] == ["test_main"] and "via_override" not in own[0]
    out = cli("tests", "pkg.core.Base.run", "--db", str(db), "--no-paths")
    assert "test_main  [pytest] tests/test_main.py:4  depth=3 conf=resolved  (via override pkg.core.A.run)" in out
    js = json.loads(cli("tests", "pkg.core.Base.run", "--db", str(db), "--json"))
    assert {t["name"]: t.get("via_override") for t in js["transitive"]}["test_main"] == ["pkg.core.A.run"]

    import codegraph.mcp_server as M
    old = M.STATE["db"]
    M.STATE["db"] = str(db)
    try:
        assert "(via override pkg.core.A.run)" in M.tests_covering("pkg.core.Base.run")
    finally:
        M.STATE["db"] = old


def test_reaches_follows_overrides(tmp_path):
    st, db = build(tmp_path, PY, "py_app")
    res = Q.reaches(st, ["pkg.core.Base.run"])
    items = {i["id"]: i for i in res["items"]}
    assert items["function:pkg.core.main"]["via_override"] == "pkg.core.A.run"
    assert items["method:pkg.core.A.run"]["is_target"] and items["method:pkg.core.A.run"]["override_seed"]
    assert res["overrides_followed"] == ["pkg.core.A.run", "pkg.core.C.run"]
    # a spec with no overrides: unchanged
    plain = Q.reaches(st, ["pkg.core.A.run"])
    assert "overrides_followed" not in plain and not any(i.get("via_override") for i in plain["items"])
    out = cli("reaches", "pkg.core.Base.run", "--db", str(db))
    assert "overrides followed" in out and "pkg.core.main  depth=1" in out and "(via override pkg.core.A.run)" in out


def test_inherited_method_spec_python(tmp_path):
    st, db = build(tmp_path, PY, "py_app")
    inh = Q.inherited_targets(st, "B.name")
    assert inh and inh[0]["method"] == "method:pkg.core.Base.name" and inh[0]["class"] == "class:pkg.core.B"
    assert Q.resolve_targets(st, "pkg.core.B.name") == ["method:pkg.core.Base.name"]
    imp = Q.impact(st, "B.name")
    assert [c["id"] for c in imp["callers"]] == ["function:pkg.core.use_b"] and imp["inherited"]
    assert [t["name"] for t in Q.tests_covering(st, "B.name")["transitive"]] == ["test_use_b"]
    # B.run: inherited from Base.run; only the overrides below B (C.run) are followed, not the sibling A.run
    imp = Q.impact(st, "B.run")
    assert [x["fqn"] for x in imp["overridden_by"]] == ["pkg.core.C.run"]
    assert [c["id"] for c in imp["callers"]] == ["function:pkg.core.use_c"]
    names = {t["name"] for t in Q.tests_covering(st, "B.run")["transitive"]}
    assert names == {"test_use_c"}
    out = cli("impact", "B.run", "--db", str(db), "--no-paths")
    assert "B.run -> inherited from pkg.core.Base.run" in out and "overridden by: pkg.core.C.run" in out
    out = cli("tests", "B.name", "--db", str(db), "--no-paths")
    assert "B.name -> inherited from pkg.core.Base.name" in out
    # defined methods and unknown names are unchanged
    assert Q.resolve_targets(st, "pkg.core.C.run") == ["method:pkg.core.C.run"] and not Q.inherited_targets(st, "C.run")
    assert Q.resolve_targets(st, "B.nothing") == []


@needs_ts
def test_ts_class_hierarchy_and_inherited_spec(tmp_path):
    st, db = build(tmp_path, TS, "ts_app")
    kinds = {(r["src"], r["dst"], r["kind"]) for r in st.q(
        "SELECT src, dst, kind FROM edges WHERE kind IN ('EXTENDS','IMPLEMENTS','OVERRIDDEN_BY','IMPLEMENTED_BY')")}
    assert kinds == {("class:src/shapes.ts#Square", "class:src/shapes.ts#Shape", "EXTENDS"),
                     ("class:src/shapes.ts#Circle", "class:src/shapes.ts#Shape", "EXTENDS"),
                     ("method:src/shapes.ts#Shape.area", "method:src/shapes.ts#Square.area", "OVERRIDDEN_BY"),
                     ("class:src/shapes.ts#MemRepo", "class:src/shapes.ts#Repo", "IMPLEMENTS"),
                     ("method:src/shapes.ts#Repo.save", "method:src/shapes.ts#MemRepo.save", "IMPLEMENTED_BY")}
    # `implements` an abstract class: a call on the abstract type reaches the implementation's callers
    callers = {c["id"]: c for c in Q.impact(st, "MemRepo.save")["callers"]}
    assert callers["function:src/shapes.ts#persist"]["via_base"] == "Repo.save"
    # a call on a base-typed value resolves to Shape.area: Square.area's callers include it via the base
    callers = {c["id"]: c for c in Q.impact(st, "Square.area")["callers"]}
    assert callers["function:src/shapes.ts#total"]["via_base"] == "Shape.area"
    # Circle.label is Shape.label
    imp = Q.impact(st, "Circle.label")
    assert imp["targets"] == ["method:src/shapes.ts#Shape.label"] and imp["inherited"][0]["class_fqn"] == "Circle"
    assert [c["id"] for c in imp["callers"]] == ["function:src/shapes.ts#describe"]
    # tests on the base method: the test calling Square.area directly, via override
    tests = Q.tests_covering(st, "Shape.area")
    sq = [t for t in tests["transitive"] + tests["direct"] if t["name"] == "square"]
    assert sq and sq[0]["via_override"] == ["Square.area"]

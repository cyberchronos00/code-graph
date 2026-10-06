"""Calls on the items of a list of instances that reaches the loop through several hops (#50): a copy, filtering
comprehensions, a dict returned by a setup helper and a filter inside another loop. Fixtures are written from
scratch in a temp dir."""
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import query as Q  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402

FILES = {
    "plug/__init__.py": "",
    "plug/base.py": '''
        class FrameworkPlugin:
            name = "base"

            def detect(self, project):
                return True

            def contribute(self, project):
                return None


        class DjangoPlugin(FrameworkPlugin):
            def contribute(self, project):
                return "django"


        class FlaskPlugin(FrameworkPlugin):
            def contribute(self, project):
                return "flask"


        class NuxtPlugin(FrameworkPlugin):
            pass


        class Unrelated:
            def contribute(self, project):
                return "other"
        ''',
    "plug/run.py": '''
        import copy

        from plug.base import DjangoPlugin, FlaskPlugin, NuxtPlugin, Unrelated

        PLUGINS = [DjangoPlugin(), FlaskPlugin(), NuxtPlugin()]


        def setup(project, add=(), remove=()):
            all_fws = copy.deepcopy(PLUGINS)
            detected = [f for f in all_fws if f.detect(project)]
            fws = [f for f in all_fws if (f in detected or f.name in add) and f.name not in remove]
            return {"plugins": fws, "names": [f.name for f in fws]}


        def index(project):
            plan = setup(project)
            frameworks = plan["plugins"]
            for lang in ("python", "ts"):
                fws = [f for f in frameworks if f.detect(lang)]
                for fw in fws:
                    fw.contribute(project)


        def unknown(project, items):
            for it in items:                      # a parameter: nothing known about its elements
                it.contribute(project)
            for n in [1, "a", None]:              # elements without the method
                n.contribute(project)


        def growing(project):
            acc = []
            for p in PLUGINS:
                acc = acc + [p]                   # refers back to itself
            for p in acc:
                p.contribute(project)


        def pick(kind, depth=0):                  # many returns that lead back to the helper itself
            if kind == "a":
                return pick("b", depth + 1)
            if kind == "b":
                return pick("c", depth + 1) or pick("a", depth + 1)
            if kind == "c":
                return [DjangoPlugin()] + pick("d", depth + 1)
            return pick("a", depth + 1) + pick("b", depth + 1)


        def recursive(project):
            for p in pick("a"):
                p.contribute(project)


        if __name__ == "__main__":
            index(".")
        ''',
}
B = "method:plug.base."


def build(tmp: Path):
    root = tmp / "plug_app"
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    db = tmp / "plug.db"
    index_project(root, db, "plug_app")
    return GraphStore(db)


def calls(st, src):
    return {(r["dst"], r["confidence"]) for r in st.q("SELECT dst, confidence FROM edges WHERE src = ? AND kind = 'CALLS'", (src,))}


def test_loop_over_a_filtered_copy_of_a_returned_list(tmp_path):
    st = build(tmp_path)
    got = calls(st, "function:plug.run.index")
    for m in ("FrameworkPlugin.contribute", "DjangoPlugin.contribute", "FlaskPlugin.contribute", "FrameworkPlugin.detect"):
        assert (B + m, "resolved") in got, m
    assert not any(d == B + "Unrelated.contribute" for d, _ in got)


def test_impact_on_an_override_and_the_base(tmp_path):
    st = build(tmp_path)
    res = Q.impact(st, "FlaskPlugin.contribute")
    assert "function:plug.run.index" in {c["id"] for c in res["callers"]}
    assert any(e["entry_kind"] for e in res["entry_points"])
    res = Q.impact(st, "FrameworkPlugin.contribute")
    assert "function:plug.run.index" in {c["id"] for c in res["callers"]}


def test_no_edges_without_known_elements_and_cycles_end(tmp_path):
    st = build(tmp_path)
    assert not any(d.endswith(".contribute") for d, _ in calls(st, "function:plug.run.unknown"))
    got = calls(st, "function:plug.run.growing")
    assert (B + "DjangoPlugin.contribute", "resolved") in got     # `acc = acc + [p]`: followed once, no loop
    assert not any(d == B + "Unrelated.contribute" for d, _ in got)


def test_recursive_helpers_end_quickly(tmp_path):
    import time
    t = time.time()
    st = build(tmp_path)
    assert time.time() - t < 20
    got = calls(st, "function:plug.run.recursive")
    assert (B + "DjangoPlugin.contribute", "resolved") in got
    assert not any(d == B + "Unrelated.contribute" for d, _ in got)

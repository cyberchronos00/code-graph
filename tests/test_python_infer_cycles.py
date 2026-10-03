"""Python type inference on self-referencing attributes (#78): an attribute re-assigned from comprehensions over itself,
several per method with one shared loop name (the shape of langgraph's PregelLoop.checkpoint_pending_writes), made the
indexer recurse through every combination of bindings and never finish. Attribute types are now memoised per
(class, attribute) and a cycle back to an attribute already being inferred is unknown. Fixtures are written from
scratch in a temp dir."""
import json
import signal
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import query as Q  # noqa: E402
from codegraph.core.store import GraphStore  # noqa: E402
from codegraph.indexer import index_project  # noqa: E402

FILES = {
    "pkg/__init__.py": "",
    "pkg/store.py": """
        class Store:
            def save(self, rows):
                return len(rows)
        """,
    "pkg/loop.py": """
        from pkg.store import Store


        class Loop:
            def __init__(self, saved):
                self.pending = [w for w in saved]
                self.store = Store()

            def put(self, task_id, writes):
                if all(w[0] for w in writes):
                    writes = [w for w in writes]
                if task_id:
                    self.pending = [w for w in self.pending if w[0] != task_id or w[1]]
                    keep = [w[1:] for w in self.pending if w[0] == task_id]
                else:
                    self.pending = [w for w in self.pending if w[0] != task_id]
                    keep = writes
                return keep

            def drop(self, kind):
                if kind:
                    self.pending = [w for w in self.pending if w[1] != kind]
                else:
                    self.pending = [w for w in self.pending if w[1] != "resume"]
                return [w for w in self.pending if w[2]]

            def flush(self):
                return self.store.save([w for w in self.pending if w])

            def first(self):
                return self.pending[0].upper()
        """,
}


def _timeout(*_):
    raise TimeoutError("python indexing did not finish (#78 regression: exponential attribute inference)")


def test_self_referencing_attributes_index_quickly(tmp_path):
    root = tmp_path / "app"
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    db = tmp_path / "app.db"
    old = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(60)
    try:
        t0 = time.time()
        index_project(root, db, "app")
        took = time.time() - t0
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert took < 20, took
    st = GraphStore(db)
    # a typed attribute next to the cyclic one still resolves
    callers = {c["id"] for c in Q.impact(st, "pkg.store.Store.save")["callers"]}
    assert any(c.endswith("pkg.loop.Loop.flush") for c in callers), callers
    stats = st.meta()["stats"]
    lim = json.dumps(stats)
    assert '"inference_limits": {"attr_cycles": ' in lim and '"budget_exhausted": 0' in lim, lim

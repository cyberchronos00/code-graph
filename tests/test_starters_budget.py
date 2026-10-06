"""Starter queries stay within their time budget, and the one-pass route report (query.GroupClosures) gives the same
answer as one reverse closure + shortest paths per write target. Synthetic graphs only."""
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph import query as Q, routes as R, starters as S  # noqa: E402
from cg_code_graph.core.model import PROPAGATING, Edge, Node  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402


def synthetic(tmp_path, n_routes=12, n_fns=60, n_tables=9, seed=7) -> GraphStore:
    """Routes -> a layered call graph with shortcuts, mixed confidence, a gated edge and parallel edges -> writes."""
    import random
    rnd = random.Random(seed)
    st = GraphStore.create(tmp_path / "g.db")
    nodes, edges = [], []
    fns = [f"function:f{i}" for i in range(n_fns)]
    for i, f in enumerate(fns):
        nodes.append(Node(f, "function", f"f{i}", file=f"m{i % 5}.py", line=i + 1, lang="python"))
    for t in range(n_tables):
        nodes.append(Node(f"table:t{t}", "table", f"t{t}"))
    for r in range(n_routes):
        rid = f"route:POST /r{r}"
        nodes.append(Node(rid, "route", f"POST /r{r}", file="urls.py", line=r + 1, attrs={"framework": "django"}))
        for _ in range(2):
            edges.append(Edge(rid, rnd.choice(fns[:20]), "ROUTES_TO", "urls.py", r + 1))
    for i, f in enumerate(fns):
        for _ in range(rnd.randint(1, 3)):
            j = min(n_fns - 1, i + rnd.randint(1, 12))
            if j != i:
                edges.append(Edge(f, fns[j], "CALLS", f"m{i % 5}.py", 100 + i, rnd.choice(["exact", "resolved", "heuristic"]),
                                  gate="flag_off" if rnd.random() < 0.08 else None))
        if rnd.random() < 0.3:
            edges.append(Edge(f, f"table:t{rnd.randrange(n_tables)}", "WRITES_TABLE", f"m{i % 5}.py", 200 + i))
        if rnd.random() < 0.1:
            edges.append(Edge(f, f"column:t{rnd.randrange(n_tables)}.c", "WRITES_COLUMN", f"m{i % 5}.py", 300 + i))
    st.write(nodes, edges)
    st.db.execute("INSERT INTO node_entry_live VALUES ('flag_off', 'function:f0', 'http_route', 1, 'route:POST /r0')")
    st.db.commit()
    return st


def per_target_reference(st, groups, routes, min_conf="heuristic", gate=None):
    """The earlier route-report traversal: one reverse closure and one shortest-path pass per group."""
    out = defaultdict(list)
    for label, targets in groups.items():
        depth = Q.reverse_closure(st, targets, kinds=PROPAGATING, min_conf=min_conf)
        hit = [r for r in depth if r in routes]
        if not hit:
            continue
        paths = Q.shortest_paths(st, depth, min_conf=min_conf)
        live = Q.reverse_closure(st, targets, kinds=PROPAGATING, min_conf=min_conf, exclude_gate=gate) if gate else depth
        for rid in hit:
            out[rid].append((label, depth[rid], [(p["from"], p["kind"], p["to"], p["at"]) for p in paths.get(rid) or []],
                             rid not in live))
    return {k: sorted(v) for k, v in out.items()}


def test_route_report_matches_per_target_closures(tmp_path):
    for seed in (1, 7, 23):
        st = synthetic(tmp_path / str(seed), seed=seed)
        routes = {r["id"] for r in st.q("SELECT id FROM nodes WHERE kind='route'")}
        for min_conf in ("heuristic", "resolved", "exact"):
            res = R.routes_report(st, writes="*", min_conf=min_conf)
            groups = {f"writes {t}": list(ws) for t, ws in R._groups_for_writes(st, "*").items()}
            ref = per_target_reference(st, groups, routes, min_conf, gate="flag_off")
            got = {i["route"]: sorted((r["what"], r["depth"], [(p["from"], p["kind"], p["to"], p["at"]) for p in r["path"][:-1]],
                                       r["gated_only"]) for r in i["reaches"]) for i in res["items"]}
            assert res["gate"] == "flag_off" and got == ref and got, (seed, min_conf)
        # reaches groups take the same walk
        spec = "function:f40"
        res = R.routes_report(st, reaches=[spec])
        ref = per_target_reference(st, {f"reaches {spec}": [spec]}, routes, gate="flag_off")
        assert {i["route"] for i in res["items"]} == set(ref)


def test_starters_over_budget_are_skipped_and_reported(tmp_path, monkeypatch):
    st = synthetic(tmp_path)
    rep = {}
    full = S.generate(st, report=rep)
    assert rep["skipped"] == [] and {s["id"] for s in full} >= {"starter_unguarded_write", "starter_most_written_table",
                                                                   "starter_most_called_1"}
    rep = {}
    assert S.generate(st, budget_s=0.0, report=rep) == []
    assert rep["skipped"][0] == "starter_unguarded_write" and "starter_most_called" in rep["skipped"]

    def slow(st, n=2):    # a starter query that cannot finish: the budget interrupts the running SQL
        st.q("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 1000000000) SELECT max(x) FROM c")
        return []
    monkeypatch.setattr(S, "_most_called", slow)
    rep, t = {}, time.time()
    got = S.generate(st, budget_s=0.5, report=rep)
    assert time.time() - t < 3
    assert rep["skipped"] == ["starter_most_called"]
    assert [s["id"] for s in got] == [s["id"] for s in full if not s["id"].startswith("starter_most_called")]


def test_index_stats_list_skipped_starters(tmp_path, monkeypatch):
    from cg_code_graph.indexer import index_project
    orig = S.generate
    monkeypatch.setattr(S, "generate", lambda st, budget_s=20.0, report=None: orig(st, budget_s=0.0, report=report))
    stats = index_project(ROOT / "tests" / "django_access_fixture", tmp_path / "a.db", "access")
    assert stats["starters"] == [] and "starter_most_written_table" in stats["starters_skipped"]
    meta = GraphStore(tmp_path / "a.db").meta()
    assert meta["stats"]["starters_skipped"] == stats["starters_skipped"]

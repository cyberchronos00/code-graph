"""Orchestrates detection -> language plugins (+ framework sub-plugins) -> entry tagging -> SQLite."""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from pathlib import Path

from .core.detect import detect
from .core.model import ENTRY_KINDS, PROPAGATING
from .core.plugin import GraphBuilder, Project
from .core.store import GraphStore
from .plugins.laravel.plugin import LaravelPlugin
from .plugins.php.plugin import PhpPlugin
from .plugins.stubs.plugins import SCIP_PLUGINS
from .plugins.ts.plugin import TypeScriptPlugin
from .plugins.nuxt.plugin import NuxtPlugin

LANGUAGE_PLUGINS = [PhpPlugin(), TypeScriptPlugin(), *SCIP_PLUGINS]
FRAMEWORK_PLUGINS = [LaravelPlugin(), NuxtPlugin()]


def tag_entries(builder: GraphBuilder, skip_gate: str | None = None) -> list[tuple]:
    """Forward closure from every entry node over propagating edges (optionally ignoring edges
    that are dead under gate scenario `skip_gate`). Returns rows (node_id, entry_kind, entry_count, sample_entry)."""
    prop = set(PROPAGATING)
    fwd = defaultdict(list)
    for e in builder.edges.values():
        if e.kind in prop and not (skip_gate and e.gate == skip_gate):
            fwd[e.src].append(e.dst)
    counts = defaultdict(lambda: defaultdict(int))
    sample = {}
    for n in builder.nodes.values():
        if not n.entry_kind:
            continue
        seen, dq = {n.id}, deque([n.id])
        while dq:
            x = dq.popleft()
            for y in fwd.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    dq.append(y)
        for x in seen:
            counts[x][n.entry_kind] += 1
            sample.setdefault((x, n.entry_kind), n.id)
    return [(nid, k, c, sample[(nid, k)]) for nid, ks in counts.items() for k, c in ks.items()]


def index_project(root: str | Path, db_path: str | Path, name: str | None = None, scip: list[str] | None = None,
                  gates: str | None = None) -> dict:
    t0 = time.time()
    project = Project(root=Path(root).resolve(), name=name or Path(root).name)
    if gates:
        project.options["gates"] = json.loads(Path(gates).read_text())["scenarios"]
    project.detected = detect(project.root)
    builder = GraphBuilder()
    stats = {"detected": project.detected, "plugins": {}}
    frameworks = [f for f in FRAMEWORK_PLUGINS if f.detect(project)]
    for lp in LANGUAGE_PLUGINS:
        if not lp.detect(project):
            continue
        fws = [f for f in frameworks if f.language == lp.name]
        st = lp.index(project, builder, fws)
        stats["plugins"][lp.name] = st
        ctx = getattr(lp, "program", None)
        for fw in fws:
            stats["plugins"][f"{lp.name}/{fw.name}"] = fw.contribute(project, builder, ctx)
    for fw in frameworks:
        if f"{fw.language}/{fw.name}" not in stats["plugins"]:
            stats["plugins"][f"{fw.language}/{fw.name}"] = {"status": "detected; language plugin not active"}
    if scip:
        from .plugins.scip.importer import import_scip
        for s in scip:
            stats["plugins"][f"scip:{s}"] = import_scip(s, builder)
    # dangling edge targets -> placeholder nodes so every edge resolves
    for e in list(builder.edges.values()):
        for nid in (e.src, e.dst):
            if nid not in builder.nodes:
                kind, key = nid.split(":", 1)
                builder.add_node(kind, key, attrs={"placeholder": True})
    t_tag = time.time()
    rows = tag_entries(builder)
    stats["entry_tagging_seconds"] = round(time.time() - t_tag, 2)
    store = GraphStore.create(db_path)
    store.write(builder.nodes.values(), builder.edges.values())
    store.db.executemany("INSERT INTO node_entry VALUES (?,?,?,?)", rows)
    scen = [g["name"] for g in (project.options.get("gates") or [])[:1]]
    for sc in scen:
        live = tag_entries(builder, skip_gate=sc)
        store.db.executemany("INSERT INTO node_entry_live VALUES (?,?,?,?,?)", [(sc, *r) for r in live])
        stats["gated_edges"] = {sc: sum(1 for e in builder.edges.values() if e.gate == sc)}
    for lp in LANGUAGE_PLUGINS:
        store.db.executemany("INSERT OR REPLACE INTO gate_predicates VALUES (?,?,?,?)", getattr(lp, "gate_predicates", []) or [])
    store.db.commit()
    stats["nodes"] = len(builder.nodes)
    stats["edges"] = len(builder.edges)
    stats["index_seconds"] = round(time.time() - t0, 2)
    store.set_meta(project=project.name, root=str(project.root), stats=stats, indexed_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    store.db.close()
    return stats

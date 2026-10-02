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
from .plugins.rust.plugin import RustPlugin
from .plugins.cfamily.plugin import CFamilyPlugin
from .plugins.python.plugin import PythonPlugin
from .plugins.django.plugin import DjangoPlugin
from .plugins.dart.plugin import DartPlugin
from .plugins.flutter.plugin import FlutterPlugin
from .plugins.nest.plugin import NestPlugin
from .plugins.nextjs.plugin import NextPlugin
from .plugins.express.plugin import ExpressPlugin

LANGUAGE_PLUGINS = [PhpPlugin(), TypeScriptPlugin(), PythonPlugin(), DartPlugin(), RustPlugin(), CFamilyPlugin(), *SCIP_PLUGINS]
FRAMEWORK_PLUGINS = [LaravelPlugin(), NuxtPlugin(), DjangoPlugin(), FlutterPlugin(), NestPlugin(), NextPlugin(), ExpressPlugin()]


def _crash_site(ex: BaseException) -> str | None:
    """Innermost codegraph frame of an exception (file:line function), for plugin crash reports."""
    import traceback
    frames = [f for f in traceback.extract_tb(ex.__traceback__) if "codegraph" in f.filename]
    if not frames:
        return None
    f = frames[-1]
    return f"{Path(f.filename).name}:{f.lineno} {f.name}"


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
                  gates: str | None = None, python_roots: list[str] | None = None) -> dict:
    """Index `root` into a new graph DB. The project config file (.cg.yaml at the root) is read automatically;
    python_roots (`cg index --python-root`) overrides its python.source_roots. Raises config.ConfigError for an
    invalid config file or root."""
    from .config import load as load_config, norm_root
    t0 = time.time()
    project = Project(root=Path(root).resolve(), name=name or Path(root).name)
    project.options["config"] = load_config(project.root)
    if python_roots:
        project.options["python_roots"] = list(dict.fromkeys(norm_root(r, "--python-root") for r in python_roots))
    if gates:
        project.options["gates"] = json.loads(Path(gates).read_text())["scenarios"]
    project.detected = detect(project.root)
    builder = GraphBuilder()
    stats = {"detected": project.detected, "plugins": {}}
    if project.options["config"]:
        stats["config"] = {k: v for k, v in project.options["config"].items()}
    if python_roots:
        stats["python_roots_flag"] = project.options["python_roots"]
    file_reports: dict = {}   # language -> per-file outcome (coverage file completeness)
    frameworks = [f for f in FRAMEWORK_PLUGINS if f.detect(project)]
    for lp in LANGUAGE_PLUGINS:
        if not lp.detect(project):
            continue
        fws = [f for f in frameworks if f.language == lp.name]
        lp.program = None
        # one missing indexer / toolchain never fails the whole index: that language is skipped with a note
        missing = lp.prerequisite_problem(project) if hasattr(lp, "prerequisite_problem") else None
        if missing:
            stats["plugins"][lp.name] = {"status": "skipped", "reason": missing}
            continue
        n0, e0 = len(builder.nodes), len(builder.edges)
        lp.file_report = None
        try:
            st = lp.index(project, builder, fws)
        except Exception as ex:  # noqa: BLE001
            stats["plugins"][lp.name] = {"status": "skipped", "reason": f"{type(ex).__name__}: {str(ex)[:300]}",
                                         "partial_nodes": len(builder.nodes) - n0, "partial_edges": len(builder.edges) - e0}
            continue
        stats["plugins"][lp.name] = st
        if isinstance(st, dict) and st.get("status") in ("skipped", "error", "stub"):
            continue
        if getattr(lp, "file_report", None) is not None:
            file_reports[lp.name] = lp.file_report
            lp.file_report = None
        ctx = getattr(lp, "program", None)
        for fw in fws:
            try:
                stats["plugins"][f"{lp.name}/{fw.name}"] = fw.contribute(project, builder, ctx)
            except Exception as ex:  # noqa: BLE001
                stats["plugins"][f"{lp.name}/{fw.name}"] = {"status": "skipped", "reason": f"{type(ex).__name__}: {str(ex)[:300]}",
                                                            "at": _crash_site(ex)}
    for fw in frameworks:
        if f"{fw.language}/{fw.name}" not in stats["plugins"]:
            stats["plugins"][f"{fw.language}/{fw.name}"] = {"status": "detected; language plugin not active"}
    if scip:
        from .plugins.scip.importer import import_scip
        for s in scip:
            stats["plugins"][f"scip:{s}"] = import_scip(s, builder)
    # test code (tests/, *.spec.ts ...) never feeds the application graph: its edges become TEST_* kinds
    from .tests_index import isolate_tests
    stats["tests"] = isolate_tests(builder)
    from .tests_index import link_local_channels
    if (ch := link_local_channels(builder)):
        stats["channels_linked"] = ch
    # dangling edge targets -> placeholder nodes so every edge resolves
    for e in list(builder.edges.values()):
        for nid in (e.src, e.dst):
            if nid not in builder.nodes:
                kind, key = nid.split(":", 1)
                builder.add_node(kind, key, attrs={"placeholder": True})
    # completeness: one file scan for coverage + the blind-spot detectors (patterns no plugin models)
    from .coverage import compute, scan_tree
    from .blindspots import detect as detect_blind_spots
    t_cov = time.time()
    scanned = scan_tree(project.root)
    progs = {lp.name: lp.program for lp in LANGUAGE_PLUGINS if getattr(lp, "program", None) is not None
             and lp.name in stats["plugins"] and "status" not in (stats["plugins"][lp.name] or {})}
    bspots = detect_blind_spots(project.root, scanned.paths, builder, progs)
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
    stats["coverage"] = compute(project.root, {k: v for k, v in stats["plugins"].items() if "/" not in k and not k.startswith("scip:")},
                                scip_imported=bool(scip), reports=file_reports, scanned=scanned, blind_spots=bspots)
    stats["completeness_seconds"] = round(t_tag - t_cov, 2)
    stats["nodes"] = len(builder.nodes)
    stats["edges"] = len(builder.edges)
    stats["index_seconds"] = round(time.time() - t0, 2)
    store.set_meta(project=project.name, root=str(project.root), stats=stats, indexed_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    store.db.close()
    return stats

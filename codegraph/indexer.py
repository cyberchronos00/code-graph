"""Orchestrates detection -> language plugins (+ framework sub-plugins) -> entry tagging -> SQLite."""
from __future__ import annotations

import copy
import json
import time
from collections import defaultdict, deque
from pathlib import Path

from .core.detect import detect
from .core.model import ENTRY_KINDS, PROPAGATING
from .core.plugin import GraphBuilder, Project, gc_paused
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
from .plugins.pyweb.plugin import FastAPIPlugin, FlaskPlugin
from .plugins.dart.plugin import DartPlugin
from .plugins.flutter.plugin import FlutterPlugin
from .plugins.nest.plugin import NestPlugin
from .plugins.nextjs.plugin import NextPlugin
from .plugins.express.plugin import ExpressPlugin
from .plugins.kotlin.plugin import KotlinPlugin
from .plugins.swift.plugin import SwiftPlugin

LANGUAGE_PLUGINS = [PhpPlugin(), TypeScriptPlugin(), PythonPlugin(), DartPlugin(), RustPlugin(), CFamilyPlugin(), KotlinPlugin(), SwiftPlugin(),
                    *SCIP_PLUGINS]
FRAMEWORK_PLUGINS = [LaravelPlugin(), NuxtPlugin(), DjangoPlugin(), FastAPIPlugin(), FlaskPlugin(), FlutterPlugin(), NestPlugin(), NextPlugin(), ExpressPlugin()]


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
    with gc_paused():
        return _tag_entries(builder, skip_gate)


def _tag_entries(builder: GraphBuilder, skip_gate: str | None) -> list[tuple]:
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


def setup(project: Project) -> dict:
    """Which language plugins, framework plugins and presets apply to `project` (detection, then .cg.yaml
    frameworks.add / remove). Fills project.options["presets"] (the applied preset names)."""
    from . import presets
    from .config import FRAMEWORK_PLUGIN_NAMES
    cfg = project.options.get("config") or {}
    fwc = cfg.get("frameworks") or {}
    add, remove = set(fwc.get("add") or []), set(fwc.get("remove") or [])
    if not project.detected:
        project.detected = detect(project.root)
    # fresh plugin instances per run: state a plugin keeps on itself (caches, gate predicates, file lists) never
    # carries over to the next project indexed in the same process (MCP `index`, test suites)
    langs = [lp for lp in copy.deepcopy(LANGUAGE_PLUGINS) if lp.detect(project)]
    all_fws = copy.deepcopy(FRAMEWORK_PLUGINS)
    detected = [f for f in all_fws if f.detect(project)]
    fws = [f for f in all_fws if (f in detected or f.name in add) and f.name not in remove]
    found = {f.name: "detected" for f in detected}
    for k in (project.detected.get("frameworks") or {}):        # sub-frameworks with a preset (DRF, django-ninja)
        k = presets.FRAMEWORK_ALIASES.get(k, k)
        if k in presets.frameworks() and k not in FRAMEWORK_PLUGIN_NAMES:
            found.setdefault(k, "detected")
    names = {k: v for k, v in found.items() if k not in remove}
    for k in sorted(add):
        names.setdefault(k, ".cg.yaml")
    applied = presets.select([lp.name for lp in langs], list(names))
    project.options["presets"] = applied
    return {"language_plugins": langs, "framework_plugins": fws, "languages": [lp.name for lp in langs],
            "frameworks": names, "removed": sorted(remove & set(found)), "presets": applied}


def index_project(root: str | Path, db_path: str | Path, name: str | None = None, scip: list[str] | None = None,
                  gates: str | None = None, python_roots: list[str] | None = None, include_generated: bool = False) -> dict:
    """Index `root` into a new graph DB. The project config file (.cg.yaml at the root) is read automatically;
    python_roots (`cg index --python-root`) overrides its python.source_roots and `gates` its gates. Generated, copied
    and vendored files (codegraph/core/generated.py) stay out of the graph unless include_generated
    (`--include-generated`, .cg.yaml generated.include) indexes them with attrs.generated. Raises
    config.ConfigError for an invalid config file or root."""
    from .config import ConfigError, load as load_config, norm_root, resolve_path
    t0 = time.time()
    project = Project(root=Path(root).resolve(), name=name or Path(root).name)
    project.options["config"] = cfg = load_config(project.root)
    if python_roots:
        project.options["python_roots"] = list(dict.fromkeys(norm_root(r, "--python-root") for r in python_roots))
    gates_from = "flag" if gates else None
    if not gates and cfg.get("gates"):
        gates, gates_from = str(resolve_path(project.root, cfg["gates"])), cfg["file"]
        if not Path(gates).is_file():
            raise ConfigError(f"{cfg['file']}: gates: {cfg['gates']!r} does not exist")
    if gates:
        project.options["gates"] = json.loads(Path(gates).read_text())["scenarios"]
    project.detected = detect(project.root)
    # one file scan up front: coverage counts + the generated / copied / vendored classification every walk consults
    from .core.generated import Classifier, apply as apply_generated
    from .core.paths import rules as path_rules
    from .coverage import scan_tree
    t_scan = time.time()
    clf = Classifier(project.root, cfg, include=include_generated)
    scanned = scan_tree(project.root, path_rules(project, "common", "scan_skip_dirs", generated=False), classifier=clf)
    project.options["generated"] = clf
    if clf.include:
        project.options["dart_keep_generated"] = True
    scan_seconds = round(time.time() - t_scan, 2)
    builder = GraphBuilder()
    stats = {"detected": project.detected, "plugins": {}}
    if cfg:
        stats["config"] = {k: v for k, v in cfg.items()}
    if python_roots:
        stats["python_roots_flag"] = project.options["python_roots"]
    if include_generated:
        stats["include_generated_flag"] = True
    if gates_from:
        stats["gates_from"] = gates_from
    plan = setup(project)
    stats["presets"] = {"applied": plan["presets"], "languages": plan["languages"], "frameworks": plan["frameworks"]}
    if plan["removed"]:
        stats["presets"]["frameworks_removed"] = plan["removed"]
    file_reports: dict = {}   # language -> per-file outcome (coverage file completeness)
    frameworks = plan["framework_plugins"]
    for lp in plan["language_plugins"]:
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
        if fws and hasattr(lp, "after_frameworks"):
            lp.after_frameworks(builder, st)
        if hasattr(lp, "link_test_requests"):
            lp.link_test_requests(builder, st)
    for fw in frameworks:
        if f"{fw.language}/{fw.name}" not in stats["plugins"]:
            stats["plugins"][f"{fw.language}/{fw.name}"] = {"status": "detected; language plugin not active"}
    if scip:
        from .plugins.scip.importer import import_scip
        for s in scip:
            stats["plugins"][f"scip:{s}"] = import_scip(s, builder)
    # generated / copied / vendored files: out of the graph (default) or labelled (attrs.generated)
    stats["generated"] = apply_generated(builder, clf)
    # web / native bridges: native receivers of Capacitor / React Native / Flutter calls (platform marks on their files)
    from . import bridges as bridges_mod
    t_br = time.time()
    br = bridges_mod.apply(project, builder, scanned)
    br_seconds = time.time() - t_br
    # platform-specific code: platform tags on nodes / edges, variant implementations linked, divergence
    from .platforms import apply as apply_platforms
    t_pl = time.time()
    pl = apply_platforms(project, builder)
    if pl:
        pl["seconds"] = round(time.time() - t_pl, 2)
        stats["platforms"] = pl
    # test code (tests/, *.spec.ts ...) never feeds the application graph: its edges become TEST_* kinds
    from .tests_index import isolate_tests
    stats["tests"] = isolate_tests(builder)
    t_br = time.time()
    br = bridges_mod.finalize(project, builder, br, (pl or {}).get("targets") or bridges_mod.project_targets(project, builder))
    if br:
        br["seconds"] = round(br_seconds + time.time() - t_br, 2)
        stats["bridges"] = br
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
    from .coverage import compute
    from .blindspots import detect as detect_blind_spots
    t_cov = time.time()
    progs = {lp.name: lp.program for lp in plan["language_plugins"] if getattr(lp, "program", None) is not None
             and lp.name in stats["plugins"] and "status" not in (stats["plugins"][lp.name] or {})}
    bspots = detect_blind_spots(project.root, scanned.paths, builder, progs)
    t_tag = time.time()
    rows = tag_entries(builder)
    stats["entry_tagging_seconds"] = round(time.time() - t_tag, 2)
    store = GraphStore.create(db_path)
    with gc_paused():
        store.write(builder.nodes.values(), builder.edges.values())
    store.db.executemany("INSERT INTO node_entry VALUES (?,?,?,?)", rows)
    scen = [g["name"] for g in (project.options.get("gates") or [])[:1]]
    for sc in scen:
        live = tag_entries(builder, skip_gate=sc)
        store.db.executemany("INSERT INTO node_entry_live VALUES (?,?,?,?,?)", [(sc, *r) for r in live])
        stats["gated_edges"] = {sc: sum(1 for e in builder.edges.values() if e.gate == sc)}
    for lp in plan["language_plugins"]:
        store.db.executemany("INSERT OR REPLACE INTO gate_predicates VALUES (?,?,?,?)", getattr(lp, "gate_predicates", []) or [])
    store.db.commit()
    stats["coverage"] = compute(project.root, {k: v for k, v in stats["plugins"].items() if "/" not in k and not k.startswith("scip:")},
                                scip_imported=bool(scip), reports=file_reports, scanned=scanned, blind_spots=bspots)
    gsum = clf.summary()
    if gsum["files"] or gsum.get("build_dirs"):
        stats["coverage"]["generated"] = gsum
    # starter queries for the visual view / MCP, derived from this graph (each resolves to existing nodes)
    from .starters import generate as gen_starters
    t_st = time.time()
    store.set_meta(project=project.name, root=str(project.root), stats=stats)    # presets / config for the route guards
    gst = GraphStore(db_path)
    try:
        rep: dict = {}
        stats["starters"] = gen_starters(gst, report=rep)
        if rep.get("skipped"):
            stats["starters_skipped"] = rep["skipped"]     # over the time budget
    except Exception as ex:  # noqa: BLE001  (never fails an index)
        stats["starters"], stats["starters_error"] = [], f"{type(ex).__name__}: {str(ex)[:200]}"
    finally:
        gst.db.close()
    stats["starters_seconds"] = round(time.time() - t_st, 2)
    stats["coverage"]["setup"] = {"frameworks": sorted(plan["frameworks"]), "presets": plan["presets"],
                                  "config": cfg.get("file")}
    if pl:       # per-target coverage: what each declared target builds, conditions left unevaluated
        stats["coverage"]["platforms"] = {k: pl[k] for k in ("targets", "per_target", "unevaluated_conditions",
                                                             "unevaluated_samples", "conditions") if k in pl}
    stats["completeness_seconds"] = round(t_tag - t_cov, 2)
    stats["scan_seconds"] = scan_seconds
    stats["nodes"] = len(builder.nodes)
    stats["edges"] = len(builder.edges)
    stats["index_seconds"] = round(time.time() - t0, 2)
    store.set_meta(project=project.name, root=str(project.root), stats=stats, indexed_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    store.db.close()
    return stats

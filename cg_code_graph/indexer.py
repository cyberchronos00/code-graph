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
from .plugins.astro.plugin import AstroPlugin
from .plugins.kotlin.plugin import KotlinPlugin
from .plugins.java.plugin import JavaPlugin
from .plugins.swift.plugin import SwiftPlugin

LANGUAGE_PLUGINS = [PhpPlugin(), TypeScriptPlugin(), PythonPlugin(), DartPlugin(), RustPlugin(), CFamilyPlugin(),
                    KotlinPlugin(), JavaPlugin(), SwiftPlugin(),
                    *SCIP_PLUGINS]
FRAMEWORK_PLUGINS = [LaravelPlugin(), NuxtPlugin(), DjangoPlugin(), FastAPIPlugin(), FlaskPlugin(), FlutterPlugin(), NestPlugin(), NextPlugin(), ExpressPlugin(), AstroPlugin()]


def _crash_site(ex: BaseException) -> str | None:
    """Innermost cg_code_graph frame of an exception (file:line function), for plugin crash reports."""
    import traceback
    frames = [f for f in traceback.extract_tb(ex.__traceback__) if "cg_code_graph" in f.filename]
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
    # Elysia shares the Express plugin and preset. The coverage label is elysia, not express (via elysia).
    det_fw = project.detected.get("frameworks") or {}
    if "elysia" in det_fw and "elysia" not in remove:
        if "express" in names and "express" not in det_fw:
            names.pop("express")
        names.setdefault("elysia", "detected")
    applied = presets.select([lp.name for lp in langs], list(names))
    project.options["presets"] = applied
    return {"language_plugins": langs, "framework_plugins": fws, "languages": [lp.name for lp in langs],
            "frameworks": names, "removed": sorted(remove & set(found)), "presets": applied}


def scip_warnings(plugins: dict) -> list[str]:
    """Unusable SCIP indexes (no readable positions, no definitions, none matched): the generic importer's
    (`scip:<path>`) and the exact layers' (`<lang>.scip.warning`)."""
    out = []
    for k, v in plugins.items():
        if not isinstance(v, dict):
            continue
        w = v.get("warning") if k.startswith("scip:") else (v.get("scip") or {}).get("warning") \
            if isinstance(v.get("scip"), dict) else None
        if w:
            out.append(w if k.startswith("scip:") else f"{k}: {w}")
    return out


def index_project(root: str | Path, db_path: str | Path, name: str | None = None, scip: list[str] | None = None,
                  gates: str | None = None, python_roots: list[str] | None = None, include_generated: bool = False) -> dict:
    """Index `root` into a new graph DB. The project config file (.cg.yaml at the root) is read automatically;
    python_roots (`cg index --python-root`) overrides its python.source_roots and `gates` its gates. Generated, copied
    and vendored files (cg_code_graph/core/generated.py) stay out of the graph unless include_generated
    (`--include-generated`, .cg.yaml generated.include) indexes them with attrs.generated. Raises
    config.ConfigError for an invalid config file or root."""
    from .config import ConfigError, load as load_config, norm_root, resolve_path
    t0 = time.time()
    project = Project(root=Path(root).resolve(), name=name or Path(root).name)
    project.options["config"] = cfg = load_config(project.root)
    if scip:
        project.options["scip"] = list(scip)     # a language plugin may consume its own index (Kotlin: scip-java)
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
    kp = project.options.get("kotlin_plugin")
    if kp is not None and not getattr(kp, "_exact_done", False):
        kp._finish_exact(project, project.options.get("kotlin_files") or [], kp._exported_stats)
    if scip:
        from .plugins.scip.importer import import_scip
        for s in scip:
            if s in (project.options.get("scip_consumed") or []):
                who = (project.options.get("scip_consumed_by") or {}).get(s) or ["kotlin"]
                names = [w.capitalize() for w in who]
                word = "plugin" if len(names) == 1 else "plugins"
                stats["plugins"][f"scip:{s}"] = {
                    "status": f"imported by the {' and '.join(names)} {word} (exact layer)"}
                continue
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
    # external systems (#40): env address keys, Laravel connections, .env.example / docker-compose -> external:<protocol>:<target>
    from .external import attach as attach_external
    if (ex := attach_external(builder, project.root)):
        stats["external"] = ex
    # tests that run the project's programs in a subprocess, outside Python (Rust, Node, PHP artisan, Dart; #60)
    from .process_runs import apply as apply_process_runs
    if (pr := apply_process_runs(project, builder)):
        stats["process_runs"] = pr
    # raw TCP / UDP sockets paired by port (#39): endpoint:tcp:<port> / endpoint:udp:<port>
    from .sockets import apply as apply_sockets
    t_so = time.time()
    if (so := apply_sockets(project, builder)):
        so["seconds"] = round(time.time() - t_so, 2)
        stats["sockets"] = so
    # RPC contracts (#33): gRPC services of the .proto files, their implementations and stub calls
    from .rpc import apply as apply_rpc
    t_rpc = time.time()
    if (rp := apply_rpc(project, builder)):
        rp["seconds"] = round(time.time() - t_rpc, 2)
        stats["rpc"] = rp
    # GraphQL (#34): schema root fields endpoint:graphql:Query.x, their resolvers and the operations requesting them
    from .graphql import apply as apply_graphql
    t_gq = time.time()
    if (gq := apply_graphql(project, builder)):
        gq["seconds"] = round(time.time() - t_gq, 2)
        stats["graphql"] = gq
    # job queues (#36): endpoint:job:<framework>:<name> / endpoint:queue:<framework>/<queue>, workers from process files
    from .jobs import apply as apply_jobs
    t_jb = time.time()
    if (jb := apply_jobs(project, builder)):
        jb["seconds"] = round(time.time() - t_jb, 2)
        stats["jobs"] = jb
    # message brokers and pub/sub (#35): endpoint:kafka / amqp / redis-pubsub / redis-stream / mqtt / nats
    from .brokers import apply as apply_brokers
    t_bk = time.time()
    if (bk := apply_brokers(project, builder)):
        bk["seconds"] = round(time.time() - t_bk, 2)
        stats["brokers"] = bk
    # realtime events (#32): Socket.IO in JS / TS (servers, socket.io-client, Nest gateways) -> endpoint:socketio
    from .realtime_events import apply as apply_realtime
    t_rt = time.time()
    if (rt := apply_realtime(project, builder)):
        rt["seconds"] = round(time.time() - t_rt, 2)
        stats["realtime"] = rt
    # raw WebSocket servers (`ws`, python websockets) and SSE routes (#32 part 2)
    from .realtime_ws import apply as apply_ws
    t_ws = time.time()
    if (wsst := apply_ws(project, builder)):
        wsst["seconds"] = round(time.time() - t_ws, 2)
        stats["websockets"] = wsst
    # Socket.IO in Dart, Kotlin / Java, Swift and Rust (#32 part 3)
    from .realtime_native import apply as apply_native_sio
    t_ns = time.time()
    if (nsst := apply_native_sio(project, builder)):
        nsst["seconds"] = round(time.time() - t_ns, 2)
        stats["realtime_native"] = nsst
    # webhook receivers: signature checks on routes and the provider events they handle (#37)
    from .webhooks import apply as apply_webhooks
    t_wh = time.time()
    if (whst := apply_webhooks(project, builder)):
        whst["seconds"] = round(time.time() - t_wh, 2)
        stats["webhooks"] = whst
    # local IPC in JS / TS: workers, service workers, BroadcastChannel, window.postMessage, extensions (#38)
    from .local_ipc import apply as apply_local_ipc
    t_ipc = time.time()
    if (ipcst := apply_local_ipc(project, builder)):
        ipcst["seconds"] = round(time.time() - t_ipc, 2)
        stats["local_ipc"] = ipcst
    # Unix domain sockets, named pipes / FIFOs and D-Bus (#38 part 2)
    from .local_sockets import apply as apply_local_sockets
    t_ls = time.time()
    if (lsst := apply_local_sockets(project, builder)):
        lsst["seconds"] = round(time.time() - t_ls, 2)
        stats["local_sockets"] = lsst
    # Android intents and AIDL (#38 part 3)
    from .android_ipc import apply as apply_android_ipc
    t_ai = time.time()
    if (aist := apply_android_ipc(project, builder)):
        aist["seconds"] = round(time.time() - t_ai, 2)
        stats["android_ipc"] = aist
    # disabled TLS / SSH verification, plaintext gRPC channels, IPC exposure facts for `cg surface` (#47 part 2a)
    from .insecure_transport import apply as apply_insecure_transport
    if (itst := apply_insecure_transport(project, builder)):
        stats["insecure_transport"] = itst
    # outbound URLs built from request input (SSRF candidates) and DNS lookups of input (#47 part 2b)
    from .ssrf_input import apply as apply_ssrf_input
    if (ssst := apply_ssrf_input(project, builder)):
        stats["ssrf_input"] = ssst
    # XPC services and Darwin notifications (#38 part 3)
    from .apple_ipc import apply as apply_apple_ipc
    if (apst := apply_apple_ipc(project, builder)):
        stats["apple_ipc"] = apst
    # Dart isolates (#38 part 3)
    from .dart_isolates import apply as apply_isolates
    if (isost := apply_isolates(project, builder)):
        stats["dart_isolates"] = isost
    # child processes in application code as endpoint:process:<program> (#38 part 3)
    from .process_runs import process_endpoints
    if (pe := process_endpoints(builder)):
        stats["process_endpoints"] = pe
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
    # protocol endpoints (#31): MATCHES_ENDPOINT between senders and receivers of one graph (wildcards, templates)
    from .protocols import apply as apply_protocols
    if (pr := apply_protocols(builder, ((cfg or {}).get("protocols") or {}).get("external") or [])):
        stats["protocols"] = pr
    # dangling edge targets -> placeholder nodes so every edge resolves
    for e in list(builder.edges.values()):
        for nid in (e.src, e.dst):
            if nid not in builder.nodes:
                kind, key = nid.split(":", 1)
                builder.add_node(kind, key, attrs={"placeholder": True})
    # files parsed with syntax errors (#73): their error spans and the declarations the graph lost there
    from .core.syntax_errors import summarize as summarize_syntax_errors
    raw_err = {lang: rep["syntax_errors"] for lang, rep in file_reports.items() if rep.get("syntax_errors")}
    if raw_err:
        want = {f for raw in raw_err.values() for f in raw}
        names_by_file: dict = {}
        for n in builder.nodes.values():
            if n.file in want and n.name:
                names_by_file.setdefault(n.file, set()).add(n.name.split(".")[-1].split("(")[0])
        for lang, raw in raw_err.items():
            file_reports[lang]["syntax_errors"] = summarize_syntax_errors(
                lang, project.root, raw, names_by_file, file_reports[lang].get("parse_failed"))
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
    from .core.redact import sweep as redact_sweep
    redact_sweep(builder)
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
                                scip_imported=bool(scip), reports=file_reports, scanned=scanned, blind_spots=bspots,
                                warnings=scip_warnings(stats["plugins"]))
    # detected.languages: the root marker files (detect), plus every language a plugin indexed source files of: an
    # Xcode app has no root Package.swift, a monorepo no root tsconfig.json (#75)
    dl = stats["detected"].setdefault("languages", {})
    for e in stats["coverage"].get("languages") or []:
        pst = stats["plugins"].get(e["language"])
        if e["files"] and e["language"] not in dl and isinstance(pst, dict) and pst.get("status") not in ("skipped", "error", "stub"):
            dl[e["language"]] = ["source files (" + ", ".join(f"{x} {n}" for x, n in sorted(e["by_ext"].items())) + ")"]
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
    from .plugins.ts.react_router import reconcile_detection
    reconcile_detection(project, plan, stats)
    stats["coverage"]["setup"] = {"frameworks": sorted(plan["frameworks"]), "presets": plan["presets"],
                                  "config": cfg.get("file")}
    if pl:       # per-target coverage: what each declared target builds, conditions left unevaluated
        stats["coverage"]["platforms"] = {k: pl[k] for k in ("targets", "per_target", "unevaluated_conditions",
                                                             "unevaluated_samples", "conditions") if k in pl}
    from .coverage import value_counts
    vc = value_counts(builder)
    if vc:       # enum cases / constants per language and the references resolved to them (#84)
        stats["coverage"]["values"] = vc
    stats["completeness_seconds"] = round(t_tag - t_cov, 2)
    stats["scan_seconds"] = scan_seconds
    stats["nodes"] = len(builder.nodes)
    stats["edges"] = len(builder.edges)
    stats["index_seconds"] = round(time.time() - t0, 2)
    store.set_meta(project=project.name, root=str(project.root), stats=stats, indexed_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    store.db.close()
    return stats

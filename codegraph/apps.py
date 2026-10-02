"""Monorepo apps (`.cg.yaml` `apps`): one `cg index <root>` indexes every app and links every frontend / backend pair.

    apps:
      - {name: api, root: apps/api, role: backend}
      - {name: web, root: apps/web, role: frontend, links: [api]}    # links: the backends it calls (default: all)

`cg index <root> --db out/mono.db` writes `out/mono.<app>.db` per app and `out/mono.<frontend>+<backend>.db` per link
pair, the same graphs `cg index <app root>` and `cg link` give one by one. `out/mono.db` itself is the combined graph
of the first pair (in config order); without a frontend it is the first app's graph. Each app is indexed with its own
`.cg.yaml` (at the app root), like a separate checkout."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import ConfigError, app_pairs


def app_dbs(db: str | Path, apps: list[dict]) -> tuple[dict, dict]:
    """(app name -> DB path, (frontend, backend) -> combined DB path) next to `db`."""
    db = Path(db)
    stem = db.name[:-3] if db.name.endswith(".db") else db.name
    per_app = {a["name"]: db.parent / f"{stem}.{a['name']}.db" for a in apps}
    pairs = {(f["name"], b["name"]): db.parent / f"{stem}.{f['name']}+{b['name']}.db" for f, b in app_pairs(apps)}
    return per_app, pairs


def index_apps(root: str | Path, db: str | Path, cfg: dict, scip=None, python_roots=None,
               include_generated: bool = False) -> dict:
    """Index each app of `cfg["apps"]` and link each pair; returns a summary (per-app and per-pair stats)."""
    from .indexer import index_project
    from .link import link
    t0 = time.time()
    root = Path(root).resolve()
    apps = cfg["apps"]
    fname = cfg.get("file", ".cg.yaml")
    for a in apps:
        d = root / a["root"] if a["root"] else root
        if not d.is_dir():
            raise ConfigError(f"{fname}: apps[{a['name']}].root: {a['root']!r} is not a directory under {root.name}")
    per_app, pairs = app_dbs(db, apps)
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    out = {"project": root.name, "config": fname, "apps": [], "links": []}
    for a in apps:
        st = index_project(root / a["root"] if a["root"] else root, per_app[a["name"]], a["name"], scip,
                           python_roots=python_roots, include_generated=include_generated)
        out["apps"].append({"name": a["name"], "root": a["root"] or ".", "role": a["role"], "db": str(per_app[a["name"]]),
                            "nodes": st.get("nodes"), "edges": st.get("edges"), "index_seconds": st.get("index_seconds"),
                            "frameworks": sorted(((st.get("presets") or {}).get("frameworks") or {}))})
    for (fn, bn), path in pairs.items():
        res = link(str(per_app[bn]), str(per_app[fn]), str(path), backend_name=bn, frontend_name=fn)
        s = res["stats"]
        out["links"].append({"frontend": fn, "backend": bn, "db": str(path), "endpoints": s.get("endpoints"),
                             "endpoints_matched": s.get("endpoints_matched"), "call_sites": s.get("call_sites"),
                             "call_sites_matched": s.get("call_sites_matched"), "link_seconds": s.get("link_seconds")})
    first = next(iter(pairs.values()), None) or per_app[apps[0]["name"]]
    shutil.copyfile(first, db)
    out["db"] = str(db)
    out["db_is"] = (f"combined graph {next(iter(pairs))[0]} + {next(iter(pairs))[1]}" if pairs
                    else f"graph of app {apps[0]['name']}")
    out["index_seconds"] = round(time.time() - t0, 2)
    return out


def render(summary: dict) -> str:
    lines = [f"apps of {summary['project']} ({summary['config']}): {len(summary['apps'])} indexed, "
             f"{len(summary['links'])} linked in {summary['index_seconds']} s"]
    for a in summary["apps"]:
        lines.append(f"  {a['name']:<16} {a['role']:<8} {a['root']:<28} {a['nodes']} nodes, {a['edges']} edges, "
                     f"{a['index_seconds']} s -> {Path(a['db']).name}")
    for ln in summary["links"]:
        lines.append(f"  {ln['frontend']} -> {ln['backend']}: {ln['endpoints_matched']}/{ln['endpoints']} endpoints, "
                     f"{ln['call_sites_matched']}/{ln['call_sites']} call sites matched -> {Path(ln['db']).name}")
    lines.append(f"  {Path(summary['db']).name}: {summary['db_is']}")
    return "\n".join(lines)

"""Workspace apps (`.cg.yaml` `apps`): one `cg index <root>` indexes every app into one combined graph.

    apps:
      - {name: api, root: apps/api, role: backend}
      - {name: web, root: apps/web, role: frontend, links: [api]}    # links: the backends it calls (default: all)
      - {name: other, root: ../other-repo, role: backend}            # root may sit outside this checkout

`cg index <root> --db out/mono.db` writes `out/mono.<app>.db` per app and one combined `out/mono.db`.
Client HTTP calls in every app are matched against every other backend; a frontend `links:` list limits that app.
Each app is indexed with its own `.cg.yaml` (at the app root), like a separate checkout.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import ConfigError


def app_dbs(db: str | Path, apps: list[dict]) -> dict:
    """App name -> per-app DB path next to `db`."""
    db = Path(db)
    stem = db.name[:-3] if db.name.endswith(".db") else db.name
    return {a["name"]: db.parent / f"{stem}.{a['name']}.db" for a in apps}


def app_path(root: Path, rel: str) -> Path:
    """Directory for an app root: empty means the workspace root; absolute is used as-is; `../x` resolves."""
    if not rel:
        return root
    p = Path(rel)
    return p if p.is_absolute() else (root / rel).resolve()


def index_apps(root: str | Path, db: str | Path, cfg: dict, scip=None, python_roots=None,
               include_generated: bool = False) -> dict:
    """Index each app of `cfg["apps"]` and link them into one graph. Returns per-app and per-pair stats."""
    from .indexer import index_project
    from .link import link_many
    t0 = time.time()
    root = Path(root).resolve()
    apps = cfg["apps"]
    fname = cfg.get("file", ".cg.yaml")
    located = []
    for a in apps:
        d = app_path(root, a["root"]) if a["root"] else root
        if not d.is_dir():
            raise ConfigError(f"{fname}: apps[{a['name']}].root: {a['root']!r} is not a directory under {root.name}")
        located.append(d)
    per_app = app_dbs(db, apps)
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    out = {"project": root.name, "config": fname, "apps": [], "links": []}
    for a, d in zip(apps, located):
        st = index_project(d, per_app[a["name"]], a["name"], scip, python_roots=python_roots,
                           include_generated=include_generated)
        out["apps"].append({"name": a["name"], "root": a["root"] or ".", "role": a["role"], "db": str(per_app[a["name"]]),
                            "nodes": st.get("nodes"), "edges": st.get("edges"), "index_seconds": st.get("index_seconds"),
                            "frameworks": sorted(((st.get("presets") or {}).get("frameworks") or {}))})
    if len(apps) == 1:
        shutil.copyfile(per_app[apps[0]["name"]], db)
        out["db_is"] = f"graph of app {apps[0]['name']}"
    else:
        allow = {a["name"]: a["links"] for a in apps if "links" in a}
        res = link_many([(a["name"], str(per_app[a["name"]]), a["role"]) for a in apps], str(db), allow=allow)
        s = res["stats"]
        for p in s.get("pairs") or []:
            out["links"].append({"frontend": p["client"], "backend": p["server"], "db": str(db),
                                 "endpoints": p.get("endpoints"), "endpoints_matched": p.get("endpoints_matched"),
                                 "call_sites": p.get("call_sites"), "call_sites_matched": p.get("call_sites_matched"),
                                 "link_seconds": s.get("link_seconds")})
        out["db_is"] = "combined graph of " + ", ".join(a["name"] for a in apps)
    out["db"] = str(db)
    out["index_seconds"] = round(time.time() - t0, 2)
    return out


def render(summary: dict) -> str:
    lines = [f"apps of {summary['project']} ({summary['config']}): {len(summary['apps'])} indexed, "
             f"{len(summary['links'])} link pairs in {summary['index_seconds']} s"]
    for a in summary["apps"]:
        lines.append(f"  {a['name']:<16} {a['role']:<8} {a['root']:<28} {a['nodes']} nodes, {a['edges']} edges, "
                     f"{a['index_seconds']} s -> {Path(a['db']).name}")
    for ln in summary["links"]:
        lines.append(f"  {ln['frontend']} -> {ln['backend']}: {ln['endpoints_matched']}/{ln['endpoints']} endpoints, "
                     f"{ln['call_sites_matched']}/{ln['call_sites']} call sites matched")
    lines.append(f"  {Path(summary['db']).name}: {summary['db_is']}")
    return "\n".join(lines)

"""Project config file: `.cg.yaml` (or `.cg.yml`) at the indexed root, discovered automatically.

Records project-specific knowledge once, for the CLI, the MCP server and the visual view:

    version: 1
    python:
      source_roots: [lib, tools/scripts]     # replace source-root detection (paths relative to the indexed root)
    exclude: ["legacy/**", "*.generated.ts"] # paths never indexed, by any language plugin or the coverage scan
    include: [src/generated, build/api]      # directories indexed although a built-in skip (directory name, generated
                                             # files) would leave them out; exclude globs still apply inside them
    skip_dirs:
      add: [fixtures_big]                    # more directory names to skip everywhere
      keep: [static]                         # directory names skipped by default that hold project code here
    generated:
      paths: ["src/api/generated/**"]        # generated files detection misses (kept out of the graph, listed by coverage)
      vendored: ["third_party/**"]           # vendored copies of other projects
      keep: ["src/schema.gen.ts"]            # hand-maintained files detection would classify
      include: false                         # true: index them, labelled attrs.generated (cg index --include-generated)
    frameworks:
      add: [nest]                            # force a framework detection missed (plugin or preset name)
      remove: [flutter]                      # drop one that was detected
    auth:
      extra_patterns: ["requireTenantMember", "withOrgScope"]   # regexes on guard names that count as auth
    secret:
      extra_patterns: ["verifyStripeSignature"]                 # ... as a shared-secret / signature check
    protocols:
      external: ["kafka:audit.*", "http:GET /status"]   # <protocol>:<name glob> handled outside the analysed repos
                                             # (not reported as no_receiver / no_sender by cg protocols)
    surface:
      ignore:                                # accepted risks of `cg surface` (reason is required)
        - {finding: unguarded, path: "app/Http/Controllers/HealthController.php", reason: public health probe}
        - {finding: plaintext, id: "external:redis:cache:6379", reason: compose-internal network}
        - {finding: hardcoded, fingerprint: 3f9a1c0b7d2e4a55, reason: rotated test key}
    gates: config/gates.json                 # gate scenarios file (cg index --gates)
    plans:
      dir: docs/plans                        # plans directory (--plans-dir)
      text_mention_dirs: [src, templates]    # where plan completeness scans for text mentions
    viz:
      presets:                               # canned queries in the visual view's starter cards
        - {id: orders_writes, label: what writes the orders table, mode: reaches, specs: ["table:orders"]}
    apps:                                    # workspace: `cg index <root>` indexes each app into one combined graph
      - {name: api, root: apps/api, role: backend}
      - {name: web, root: apps/web, role: frontend, links: [api]}   # links: backends it calls (default: all)
      # root may also be absolute or ../other-repo (a workspace of separate checkouts)

Command-line flags take precedence over the file. Top-level keys this version does not read are kept and reported
in the index stats (and as a warning by `cg config validate`), so a file written for a newer cg still indexes."""
from __future__ import annotations

import difflib
import re
from pathlib import Path, PurePosixPath
from typing import Any

CONFIG_NAMES = (".cg.yaml", ".cg.yml")
SCHEMA: dict[str, set | None] = {     # top-level key -> allowed sub-keys (None: a scalar or list value)
    "version": None, "python": {"source_roots"}, "exclude": None, "skip_dirs": {"add", "keep"},
    "frameworks": {"add", "remove"}, "auth": {"extra_patterns"}, "secret": {"extra_patterns"}, "gates": None,
    "plans": {"dir", "text_mention_dirs"}, "viz": {"presets"}, "generated": {"paths", "vendored", "keep", "include"},
    "platforms": {"targets", "paths", "file_suffixes", "path_conventions"}, "include": None, "apps": None,
    "rust": {"targets"}, "protocols": {"external"}, "lossy": None, "surface": {"ignore"},
}
APP_KEYS = ("name", "root", "role", "links")
APP_ROLES = ("backend", "frontend")
KNOWN_KEYS = set(SCHEMA)
PYTHON_KEYS = SCHEMA["python"]
VIZ_MODES = ("reaches", "impact", "downstream", "path", "plan")
# framework plugins of cg_code_graph/indexer.py FRAMEWORK_PLUGINS (frameworks.add / remove also take preset names)
FRAMEWORK_PLUGIN_NAMES = ("laravel", "nuxt", "django", "fastapi", "flask", "flutter", "nest", "nextjs", "express", "astro")


class ConfigError(ValueError):
    """Invalid project config file; the message names the file and the key."""


def find(root: str | Path) -> Path | None:
    for n in CONFIG_NAMES:
        p = Path(root) / n
        if p.is_file():
            return p
    return None


def norm_app_root(value: Any, where: str) -> str:
    """An app directory: relative to the workspace (`apps/api`, `./src`, `.`), `../other-repo`, or absolute."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: expected a directory path, got {value!r}")
    v = value.strip().replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", v) or v.startswith("/"):
        return v.rstrip("/") or "/"
    s = str(PurePosixPath(v))
    return "" if s == "." else s


def norm_root(value: Any, where: str) -> str:
    """A repo-relative directory: 'lib', 'tools/scripts/', './src', '.'. Absolute paths and '..' are rejected."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: expected a directory path relative to the indexed root, got {value!r}")
    v = value.strip().replace("\\", "/")
    p = PurePosixPath(v)
    if p.is_absolute() or ".." in p.parts:
        raise ConfigError(f"{where}: {value!r} must stay inside the indexed root (relative path, no '..')")
    s = str(p)
    return "" if s == "." else s


def known_frameworks() -> list[str]:
    from . import presets
    return sorted(set(FRAMEWORK_PLUGIN_NAMES) | set(presets.frameworks()))


def framework_name(value: Any, where: str) -> str:
    from . import presets
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: expected a framework name, got {value!r}")
    v = value.strip().lower()
    v = presets.FRAMEWORK_ALIASES.get(v, v)
    if v not in known_frameworks():
        near = difflib.get_close_matches(v, known_frameworks(), n=1)
        raise ConfigError(f"{where}: unknown framework {value!r}" + (f" (did you mean {near[0]!r}?)" if near else "")
                          + f"; known: {', '.join(known_frameworks())}")
    return v


def _strings(v: Any, where: str, allow_one: bool = True) -> list[str]:
    if isinstance(v, str) and allow_one:
        v = [v]
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v):
        raise ConfigError(f"{where}: expected a non-empty list of strings")
    return list(dict.fromkeys(x.strip() for x in v))


def _section(data: dict, key: str, fname: str) -> dict | None:
    sec = data.get(key)
    if sec is None:
        return None
    if not isinstance(sec, dict):
        raise ConfigError(f"{fname}: {key}: expected a mapping (keys: {', '.join(sorted(SCHEMA[key]))})")
    unknown = sorted(set(sec) - SCHEMA[key])
    if unknown:
        raise ConfigError(f"{fname}: {key}: unknown key {unknown[0]!r} (known: {', '.join(sorted(SCHEMA[key]))})")
    return sec


def _regexes(v: Any, where: str) -> list[str]:
    out = _strings(v, where)
    for i, rx in enumerate(out):
        try:
            re.compile(rx)
        except re.error as ex:
            raise ConfigError(f"{where}[{i}]: {rx!r} is not a valid regular expression ({ex})") from None
    return out


def _viz_presets(v: Any, where: str) -> list[dict]:
    if not isinstance(v, list) or not v:
        raise ConfigError(f"{where}: expected a non-empty list of {{id, label, mode, specs}} entries")
    out, ids = [], set()
    for i, p in enumerate(v):
        w = f"{where}[{i}]"
        if not isinstance(p, dict):
            raise ConfigError(f"{w}: expected a mapping with id, label, mode, specs")
        unknown = sorted(set(p) - {"id", "label", "mode", "specs", "sinks"})
        if unknown:
            raise ConfigError(f"{w}: unknown key {unknown[0]!r} (known: id, label, mode, specs, sinks)")
        for k in ("id", "label", "mode"):
            if not isinstance(p.get(k), str) or not p[k].strip():
                raise ConfigError(f"{w}.{k}: required (a string)")
        if p["mode"] not in VIZ_MODES:
            raise ConfigError(f"{w}.mode: {p['mode']!r} is not one of {', '.join(VIZ_MODES)}")
        if p["id"] in ids:
            raise ConfigError(f"{w}.id: duplicate id {p['id']!r}")
        ids.add(p["id"])
        e = {"id": p["id"], "label": p["label"], "mode": p["mode"], "specs": _strings(p.get("specs"), f"{w}.specs")}
        if p.get("sinks") is not None:
            e["sinks"] = _strings(p["sinks"], f"{w}.sinks")
        out.append(e)
    return out


def _platform_names(v: Any, where: str, extra: tuple = ()) -> list[str]:
    from .platforms import KNOWN, norm
    out = []
    for i, n in enumerate(_strings(v, where)):
        p = n.lower() if n.lower() in extra else norm(n)
        if p is None:
            raise ConfigError(f"{where}[{i}]: unknown platform {n!r} (known: {', '.join(KNOWN + extra)})")
        out.append(p)
    return list(dict.fromkeys(out))


def _platforms(pf: dict, fname: str) -> dict:
    """platforms: targets (the project's build targets), paths (glob -> targets: files built only there),
    file_suffixes / path_conventions / xcode_membership (React Native .ios.ts files, C/C++ win/ unix/ directories,
    Xcode target membership; default on)."""
    out: dict = {}
    if pf.get("targets") is not None:
        out["targets"] = _platform_names(pf["targets"], f"{fname}: platforms.targets")
        if not out["targets"]:
            raise ConfigError(f"{fname}: platforms.targets: expected at least one target")
    if pf.get("paths") is not None:
        if not isinstance(pf["paths"], dict):
            raise ConfigError(f"{fname}: platforms.paths: expected a mapping of glob -> targets "
                              f"(e.g. 'src/win/**': [windows])")
        out["paths"] = {}
        for g, ts in pf["paths"].items():
            g = str(g)
            if ".." in PurePosixPath(g.strip("/")).parts:
                raise ConfigError(f"{fname}: platforms.paths: {g!r} must stay inside the indexed root (no '..')")
            names = _platform_names(ts, f"{fname}: platforms.paths[{g!r}]", extra=("unix", "native"))
            if not names:
                raise ConfigError(f"{fname}: platforms.paths[{g!r}]: expected at least one target")
            out["paths"][g] = names
    for k in ("file_suffixes", "path_conventions", "xcode_membership"):
        if pf.get(k) is not None:
            if not isinstance(pf[k], bool):
                raise ConfigError(f"{fname}: platforms.{k}: expected true or false, got {pf[k]!r}")
            out[k] = pf[k]
    return out


def parse(data: Any, fname: str = ".cg.yaml") -> dict:
    """Validated, normalized config from parsed YAML (see load())."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{fname}: expected a mapping of keys at the top level")
    ver = data.get("version", 1)
    if ver != 1:
        raise ConfigError(f"{fname}: version {ver!r} is not supported (this cg reads version 1)")
    out: dict = {"file": fname}
    py = _section(data, "python", fname)
    if py is not None:
        roots = py.get("source_roots")
        if roots is not None:
            if isinstance(roots, str):
                roots = [roots]
            if not isinstance(roots, list) or not roots:
                raise ConfigError(f"{fname}: python.source_roots: expected a non-empty list of directories")
            out["python"] = {"source_roots": list(dict.fromkeys(
                norm_root(r, f"{fname}: python.source_roots[{i}]") for i, r in enumerate(roots)))}
    if data.get("exclude") is not None:
        globs = _strings(data["exclude"], f"{fname}: exclude")
        for i, g in enumerate(globs):
            if ".." in PurePosixPath(g.strip("/")).parts:
                raise ConfigError(f"{fname}: exclude[{i}]: {g!r} must stay inside the indexed root (no '..')")
        out["exclude"] = globs
    if data.get("include") is not None:
        out["include"] = list(dict.fromkeys(norm_root(d, f"{fname}: include[{i}]")
                                            for i, d in enumerate(_strings(data["include"], f"{fname}: include"))))
        if "" in out["include"]:
            raise ConfigError(f"{fname}: include: '.' is the indexed root itself; name the directories to include")
    if data.get("apps") is not None:
        out["apps"] = _apps(data["apps"], fname)
    sd = _section(data, "skip_dirs", fname)
    if sd is not None:
        out["skip_dirs"] = {}
        for k in ("add", "keep"):
            if sd.get(k) is not None:
                names = _strings(sd[k], f"{fname}: skip_dirs.{k}")
                bad = [n for n in names if "/" in n or "*" in n]
                if bad:
                    raise ConfigError(f"{fname}: skip_dirs.{k}: {bad[0]!r} is not a directory name (use exclude for paths and globs)")
                out["skip_dirs"][k] = names
    gen = _section(data, "generated", fname)
    if gen is not None:
        out["generated"] = {}
        for k in ("paths", "vendored", "keep"):
            if gen.get(k) is not None:
                globs = _strings(gen[k], f"{fname}: generated.{k}")
                for i, g in enumerate(globs):
                    if ".." in PurePosixPath(g.strip("/")).parts:
                        raise ConfigError(f"{fname}: generated.{k}[{i}]: {g!r} must stay inside the indexed root (no '..')")
                out["generated"][k] = globs
        if gen.get("include") is not None:
            if not isinstance(gen["include"], bool):
                raise ConfigError(f"{fname}: generated.include: expected true or false, got {gen['include']!r}")
            out["generated"]["include"] = gen["include"]
    pf = _section(data, "platforms", fname)
    if pf is not None:
        out["platforms"] = _platforms(pf, fname)
    rs = _section(data, "rust", fname)
    if rs is not None and rs.get("targets") is not None:
        out["rust"] = {"targets": rust_targets(rs["targets"], f"{fname}: rust.targets")}
    fw = _section(data, "frameworks", fname)
    if fw is not None:
        out["frameworks"] = {}
        for k in ("add", "remove"):
            if fw.get(k) is not None:
                out["frameworks"][k] = list(dict.fromkeys(
                    framework_name(n, f"{fname}: frameworks.{k}[{i}]") for i, n in enumerate(_strings(fw[k], f"{fname}: frameworks.{k}"))))
        both = set(out["frameworks"].get("add", [])) & set(out["frameworks"].get("remove", []))
        if both:
            raise ConfigError(f"{fname}: frameworks: {sorted(both)[0]!r} is in both add and remove")
    for key in ("auth", "secret"):
        sec = _section(data, key, fname)
        if sec is not None and sec.get("extra_patterns") is not None:
            out[key] = {"extra_patterns": _regexes(sec["extra_patterns"], f"{fname}: {key}.extra_patterns")}
    pr = _section(data, "protocols", fname)
    if pr is not None and pr.get("external") is not None:
        ext = _strings(pr["external"], f"{fname}: protocols.external")
        bad = [x for x in ext if ":" not in x]
        if bad:
            raise ConfigError(f"{fname}: protocols.external: {bad[0]!r} is not <protocol>:<name glob> (e.g. kafka:audit.*)")
        out["protocols"] = {"external": ext}
    sf = _section(data, "surface", fname)
    if sf is not None and sf.get("ignore") is not None:
        out["surface"] = {"ignore": _surface_ignores(sf["ignore"], f"{fname}: surface.ignore")}
    if data.get("gates") is not None:
        out["gates"] = norm_root(data["gates"], f"{fname}: gates")
    pl = _section(data, "plans", fname)
    if pl is not None:
        out["plans"] = {}
        if pl.get("dir") is not None:
            out["plans"]["dir"] = norm_root(pl["dir"], f"{fname}: plans.dir")
        if pl.get("text_mention_dirs") is not None:
            out["plans"]["text_mention_dirs"] = [norm_root(d, f"{fname}: plans.text_mention_dirs[{i}]")
                                                 for i, d in enumerate(_strings(pl["text_mention_dirs"], f"{fname}: plans.text_mention_dirs"))]
    vz = _section(data, "viz", fname)
    if vz is not None and vz.get("presets") is not None:
        out["viz"] = {"presets": _viz_presets(vz["presets"], f"{fname}: viz.presets")}
    if data.get("lossy") is not None:      # #88: extra lossy-transform call names / globs for `cg roundtrip`
        out["lossy"] = _strings(data["lossy"], f"{fname}: lossy")
    ignored = sorted(str(k) for k in set(data) - KNOWN_KEYS)
    if ignored:
        out["ignored_keys"] = ignored
    return out


SURFACE_IGNORE_KEYS = ("finding", "path", "id", "fingerprint", "reason")


def _surface_ignores(v: Any, where: str) -> list[dict]:
    """`surface.ignore`: [{finding, path | id | fingerprint, reason}]; the reason is required."""
    from .surface import FINDINGS
    if not isinstance(v, list):
        raise ConfigError(f"{where}: expected a list of {{finding, path | id | fingerprint, reason}} entries")
    out = []
    for i, e in enumerate(v):
        w = f"{where}[{i}]"
        if not isinstance(e, dict):
            raise ConfigError(f"{w}: expected a mapping with finding, path | id | fingerprint and reason")
        bad = sorted(str(k) for k in set(e) - set(SURFACE_IGNORE_KEYS))
        if bad:
            raise ConfigError(f"{w}: unknown key {bad[0]!r} (allowed: {', '.join(SURFACE_IGNORE_KEYS)})")
        f = e.get("finding")
        if f not in FINDINGS:
            raise ConfigError(f"{w}: finding {f!r} is not one of {', '.join(FINDINGS)}")
        reason = e.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(f"{w}: reason is required (say why the risk is accepted)")
        sel = {k: e[k] for k in ("path", "id", "fingerprint") if e.get(k) is not None}
        if not sel:
            raise ConfigError(f"{w}: give at least one of path, id or fingerprint")
        for k, x in sel.items():
            if not isinstance(x, str) or not x.strip():
                raise ConfigError(f"{w}: {k} must be a non-empty string")
        out.append({"finding": f, **{k: x.strip() for k, x in sel.items()}, "reason": reason.strip()})
    return out


def _apps(v: Any, fname: str) -> list[dict]:
    """apps as a list of {name, root, role, links} (or a mapping name -> {root, role, links}), validated: unique names,
    role backend / frontend, links naming backend apps. `root` may be relative, `../other-repo` or absolute.
    A frontend without `links` links to every backend."""
    if isinstance(v, dict):
        v = [dict(x or {}, name=k) if isinstance(x, dict) or x is None else x for k, x in v.items()]
    if not isinstance(v, list) or not v:
        raise ConfigError(f"{fname}: apps: expected a non-empty list of apps ({{name, root, role, links}})")
    apps, names = [], set()
    for i, a in enumerate(v):
        where = f"{fname}: apps[{i}]"
        if not isinstance(a, dict):
            raise ConfigError(f"{where}: expected a mapping with keys {', '.join(APP_KEYS)}")
        bad = sorted(set(a) - set(APP_KEYS))
        if bad:
            raise ConfigError(f"{where}: unknown key {bad[0]!r} (keys: {', '.join(APP_KEYS)})")
        name = a.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][\w.-]*", name or ""):
            raise ConfigError(f"{where}.name: expected a name of letters, digits, '.', '-' or '_', got {name!r}")
        if name in names:
            raise ConfigError(f"{where}.name: {name!r} is used by two apps")
        names.add(name)
        root = norm_app_root(a.get("root") if a.get("root") is not None else name, f"{where}.root")
        role = a.get("role", "backend")
        if role not in APP_ROLES:
            raise ConfigError(f"{where}.role: expected backend or frontend, got {role!r}")
        links = _strings(a["links"], f"{where}.links") if a.get("links") is not None else None
        if links is not None and role != "frontend":
            raise ConfigError(f"{where}.links: only a frontend app links to backends")
        apps.append({"name": name, "root": root, "role": role, **({"links": links} if links is not None else {})})
    backends = {a["name"] for a in apps if a["role"] == "backend"}
    for i, a in enumerate(apps):
        for n in a.get("links") or []:
            if n not in backends:
                raise ConfigError(f"{fname}: apps[{i}].links: {n!r} is not a backend app"
                                  + (f" (backends: {', '.join(sorted(backends))})" if backends else ""))
    return apps


def app_pairs(apps: list[dict]) -> list[tuple[dict, dict]]:
    """(frontend, backend) link pairs in config order."""
    by = {a["name"]: a for a in apps}
    backends = [a for a in apps if a["role"] == "backend"]
    out = []
    for a in apps:
        if a["role"] == "frontend":
            out += [(a, by[n]) for n in a["links"]] if "links" in a else [(a, b) for b in backends]
    return out


def rust_targets(v, where: str) -> str:
    """`rust.targets`: extra rust-analyzer runs per target (#56). `auto` (default), `off` / false, or a list of
    platforms / target triples; returned in the CG_RUST_TARGETS form."""
    if v is False or (isinstance(v, str) and v.strip().lower() in ("off", "none", "0", "false")):
        return "off"
    if isinstance(v, str) and v.strip().lower() == "auto":
        return "auto"
    items = [v] if isinstance(v, str) else v
    if not isinstance(items, list) or not items or not all(isinstance(x, str) and x.strip() for x in items):
        raise ConfigError(f"{where}: expected auto, off or a list of platforms / target triples, got {v!r}")
    return ",".join(x.strip() for x in items)


def load(root: str | Path) -> dict:
    """{"file": ".cg.yaml", "python": {...}, "exclude": [...], ..., "ignored_keys": [...]} or {} without a config
    file. Raises ConfigError for a file cg cannot use."""
    p = find(root)
    return load_file(p) if p is not None else {}


def load_file(p: str | Path) -> dict:
    import yaml
    p = Path(p)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as ex:
        raise ConfigError(f"{p.name}: not valid YAML ({str(ex).splitlines()[0]})") from None
    except OSError as ex:
        raise ConfigError(f"{p}: cannot read ({ex.strerror})") from None
    return parse(data, p.name)


def unknown_key_warnings(cfg: dict) -> list[str]:
    out = []
    for k in cfg.get("ignored_keys") or []:
        near = difflib.get_close_matches(k, sorted(KNOWN_KEYS), n=1)
        out.append(f"{cfg.get('file', '.cg.yaml')}: unknown top-level key {k!r} is ignored"
                   + (f" (did you mean {near[0]!r}?)" if near else f" (keys this cg reads: {', '.join(sorted(KNOWN_KEYS))})"))
    return out


def resolve_path(root: str | Path, rel: str) -> Path:
    return Path(root) / rel if rel else Path(root)


def graph_configs(st) -> list[tuple[str | None, dict, str | None]]:
    """(repo, config, root) recorded in a graph DB at index time: one entry for a single-repo graph, one per repo for a
    combined graph (read from the source DBs that still exist)."""
    try:
        m = st.meta()
    except Exception:  # noqa: BLE001
        return []
    if not m.get("repos"):
        return [(None, (m.get("stats") or {}).get("config") or {}, m.get("root"))]
    out = []
    for r in m["repos"]:
        src = (m.get("sources") or {}).get(r)
        from .routes import _meta_of
        mm = _meta_of(src)
        out.append((r, (mm.get("stats") or {}).get("config") or {}, mm.get("root")))
    return out


def configured_plans_dir(st) -> Path | None:
    """plans.dir of the .cg.yaml recorded in the graph (first repo that sets it), resolved against its root."""
    for _, cfg, root in graph_configs(st):
        d = (cfg.get("plans") or {}).get("dir")
        if d is not None and root:
            return resolve_path(root, d)
    return None


# ------------------------------------------------------------------------------------------- cg config show
def effective(root: str | Path, python_roots: list[str] | None = None, gates: str | None = None,
              auth_pattern: str | None = None, plans_dir: str | None = None, presets_file: str | None = None,
              include_generated: bool = False) -> dict:
    """Effective configuration of `root` without indexing it: every value with its source (`detected`, `built-in`,
    `preset <name>`, the config file name, `flag --x`). Raises ConfigError for an invalid config file."""
    from . import presets as PR
    from .core.plugin import Project
    from .indexer import setup
    root = Path(root).resolve()
    project = Project(root=root, name=root.name)
    project.options["config"] = cfg = load(root)
    fname = cfg.get("file", ".cg.yaml")
    plan = setup(project)
    rows: list[dict] = []

    def row(key, value, source):
        rows.append({"key": key, "value": value, "source": source})

    row("config file", fname if cfg else None, "found at the indexed root" if cfg else "none (optional)")
    row("languages", plan["languages"], "detected")
    for f, how in plan["frameworks"].items():
        row("frameworks", f, f"{fname} frameworks.add" if how == ".cg.yaml" else "detected")
    for f in plan["removed"]:
        row("frameworks", f"{f} (removed)", f"{fname} frameworks.remove")
    row("presets", plan["presets"], "built-in (cg_code_graph/presets), picked by detection")
    if python_roots:
        row("python.source_roots", [norm_root(r, "--python-root") for r in python_roots], "flag --python-root")
    elif (cfg.get("python") or {}).get("source_roots"):
        row("python.source_roots", cfg["python"]["source_roots"], f"{fname} python.source_roots")
    elif "python" in plan["languages"]:
        row("python.source_roots", "detected at index time (cg coverage lists them)", "detected")
    row("exclude", cfg.get("exclude") or [], f"{fname} exclude" if cfg.get("exclude") else "none")
    sd = cfg.get("skip_dirs") or {}
    for p in ["common", *[x for x in plan["presets"] if x != "common"]]:
        names = PR.values(p, "skip_dirs", default=None)
        if names:
            row("skip_dirs", sorted(n for n in names if n not in sd.get("keep", [])), f"preset {p}")
        sp = PR.values(p, "skip_paths", default=None)
        if sp:
            row("skip_paths", sorted(n for n in sp if n not in sd.get("keep", [])), f"preset {p} (root-relative)")
    if cfg.get("include"):
        row("include", cfg["include"], f"{fname} include")
    else:
        row("include", [], "none")
    row("skip_dirs (coverage scan)", sorted(PR.values("common", "scan_skip_dirs", default=[])), "preset common")
    if sd.get("add"):
        row("skip_dirs", sd["add"], f"{fname} skip_dirs.add")
    if sd.get("keep"):
        row("skip_dirs kept", sd["keep"], f"{fname} skip_dirs.keep")
    gc = cfg.get("generated") or {}
    row("generated (build dirs)", sorted(PR.values("common", "generated", "build_dirs", default={}) or {}), "preset common")
    row("generated (file names)", sorted(PR.values("common", "generated", "files", default={}) or {}), "preset common")
    row("generated (detected)", ".gitattributes linguist-generated / linguist-vendored, generator header banners, "
        "Capacitor / Cordova copy targets, .openapi-generator/FILES", "built-in")
    for k in ("paths", "vendored", "keep"):
        if gc.get(k):
            row(f"generated.{k}", gc[k], f"{fname} generated.{k}")
    if include_generated:
        row("generated.include", True, "flag --include-generated")
    else:
        row("generated.include", bool(gc.get("include")), f"{fname} generated.include" if "include" in gc else "built-in (excluded, listed by cg coverage)")
    pc = cfg.get("platforms") or {}
    if pc.get("targets"):
        row("platforms.targets", pc["targets"], f"{fname} platforms.targets")
    else:
        row("platforms.targets", "detected at index time: Flutter platform folders, Expo app.json, React Native, "
            "Electron / Tauri, else the desktop targets plus those the conditions name (cg platforms)", "built-in")
    if pc.get("paths"):
        row("platforms.paths", [f"{g}: {', '.join(ts)}" for g, ts in pc["paths"].items()], f"{fname} platforms.paths")
    for k, what in (("file_suffixes", "React Native .ios / .android / .native / .web files"),
                    ("path_conventions", "C / C++ win/ unix/ posix/ darwin/ directories and _win / _unix file names")):
        row(f"platforms.{k}", pc.get(k, True), f"{fname} platforms.{k}" if k in pc else f"built-in ({what})")
    row("auth.token_pattern", PR.values("common", "auth", "token_pattern"), "preset common")
    for section in ("auth", "secret"):
        for key in ("guards", "not_auth", "session_only") if section == "auth" else ("guards",):
            for p in plan["presets"]:
                g = PR.values(p, section, key, default=None)
                if g:
                    row(f"{section}.{key}", [x["name"] if isinstance(x, dict) else x for x in g], f"preset {p}")
        if (cfg.get(section) or {}).get("extra_patterns"):
            row(f"{section}.extra_patterns", cfg[section]["extra_patterns"], f"{fname} {section}.extra_patterns")
    if auth_pattern:
        row("auth.extra_patterns", [auth_pattern], "flag --auth-pattern")
    row("secret.token_pattern", PR.values("common", "secret", "token_pattern"), "preset common")
    if gates:
        row("gates", gates, "flag --gates")
    else:
        row("gates", cfg.get("gates"), f"{fname} gates" if cfg.get("gates") else "none")
    pl = cfg.get("plans") or {}
    if plans_dir:
        row("plans.dir", plans_dir, "flag --plans-dir")
    else:
        row("plans.dir", pl.get("dir", "plans/ in the cg checkout"), f"{fname} plans.dir" if "dir" in pl else "built-in")
    row("plans.text_mention_dirs", pl.get("text_mention_dirs") or PR.values("laravel", "plans", "text_mention_dirs"),
        f"{fname} plans.text_mention_dirs" if pl.get("text_mention_dirs") else "preset laravel")
    row("plans.short_prefixes", PR.values("laravel", "plans", "short_prefixes"), "preset laravel")
    if presets_file:
        row("viz.presets", presets_file, "flag --presets")
    elif (cfg.get("viz") or {}).get("presets"):
        row("viz.presets", [p["id"] for p in cfg["viz"]["presets"]], f"{fname} viz.presets")
    row("viz.presets (starters)", "derived from the graph at index time (cg starters)", "built-in")
    if cfg.get("apps"):
        for a in cfg["apps"]:
            row("apps", f"{a['name']}: {a['root'] or '.'} ({a['role']})", f"{fname} apps")
        for f, b in app_pairs(cfg["apps"]):
            row("apps (link pairs)", f"{f['name']} -> {b['name']}",
                f"{fname} apps[{f['name']}].links" if "links" in f else "built-in (a frontend links to every backend)")
    else:
        row("apps", "none: the root is indexed as one project", "none")
    if cfg.get("ignored_keys"):
        row("ignored keys", cfg["ignored_keys"], f"{fname} (not read by this cg version)")
    return {"root": str(root), "config": cfg, "rows": rows, "warnings": unknown_key_warnings(cfg)}


def render_effective(eff: dict) -> str:
    out = [f"effective configuration of {Path(eff['root']).name}"]
    w = max(len(r["key"]) for r in eff["rows"])
    for r in eff["rows"]:
        v = r["value"]
        if isinstance(v, list):
            v = ", ".join(map(str, v)) if v else "(none)"
        v = "(none)" if v is None else str(v)
        if len(v) > 100:
            v = v[:97] + "..."
        out.append(f"  {r['key']:<{w}}  {v}   [{r['source']}]")
    out += [f"warning: {x}" for x in eff["warnings"]]
    return "\n".join(out)
